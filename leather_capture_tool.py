import os
import json
import sys
import time
import datetime
import math

import cv2
import numpy as np
from PIL import Image, ImageTk
import tkinter as tk
from tkinter import ttk, messagebox

# ======================================================================
# CONFIGURATION -- edit this constant to change the camera source
# ======================================================================
# Set in config.json, or override with LEATHER_CAMERA_SOURCE. For an Android
# phone in USB webcam mode, Windows exposes it as a normal camera device.

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
DATASET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset")

CANVAS_WIDTH = 800
CANVAS_HEIGHT = 600
PEN_THICKNESS = 3          # now just the STARTING brush size (adjustable in the app)
MIN_BRUSH, MAX_BRUSH = 1, 40
MAX_ZOOM = 16.0
ZOOM_STEP = 1.25
OVERLAY_OPACITY = 0.75     # how strongly red marks show on screen
MASK_FOREGROUND = 255       # white = defect
MASK_BACKGROUND = 0         # black = usable


# ======================================================================
# Default config used only if config.json is missing (also written to
# disk so the user has something to edit)
# ======================================================================
DEFAULT_CONFIG = {
    "camera_source": "1",  # "auto", a camera index such as 0, or a stream URL
    "grades": ["A", "B", "C", "D", "Waste"],
    "size_categories": ["5ft", "6ft", "7ft", "8ft", "9ft", "10ft", "11ft", "12ft"],
    "defect_types": ["Vein Marks", "Lamp Skin", "Spots", "Scars", "Wrinkles", "Holes"],
    "text_fields": ["thickness_mm", "operator_name", "batch_id"],
}

def open_camera(source):
    """Open a camera index or stream URL. ``auto`` probes common indices."""
    source = str(source).strip()
    if source.lower() == "auto":
        candidates = range(10)
    elif source.isdigit():
        candidates = (int(source),)
    else:
        cap = cv2.VideoCapture(source)
        if cap.isOpened():
            return cap
        cap.release()
        return None

    # DirectShow is usually best for USB webcam devices on Windows; the other
    # backends cover newer Windows camera drivers and non-Windows systems.
    backends = (cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY) if sys.platform == "win32" else (cv2.CAP_ANY,)
    for index in candidates:
        for backend in backends:
            cap = cv2.VideoCapture(index, backend)
            if cap.isOpened():
                ok, _ = cap.read()
                if ok:
                    print(f"Camera {index} opened")
                    return cap
            cap.release()
    return None

def load_config():
    """Load grading/metadata options from config.json, creating a default
    file if none exists."""
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
        return DEFAULT_CONFIG
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    # Fill in any missing keys with defaults so the app never crashes
    # on a partially-edited config file.
    for key, val in DEFAULT_CONFIG.items():
        cfg.setdefault(key, val)
    # Keep the camera setting out of the editable metadata fields.
    return cfg


def ensure_dataset_dirs():
    for sub in ("images", "masks", "metadata"):
        os.makedirs(os.path.join(DATASET_DIR, sub), exist_ok=True)


class LeatherCaptureApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Leather Grading Dataset Capture Tool")

        self.config_data = load_config()
        ensure_dataset_dirs()

        # ---------------- camera ----------------
        
        camera_source = os.environ.get("LEATHER_CAMERA_SOURCE", self.config_data["camera_source"])
        self.camera_source = camera_source
        self.cap = open_camera(camera_source)

        # ---------------- state ----------------
        self.live_mode = True
        self.current_frame = None          # last live frame (BGR, np.ndarray)
        self.captured_frame = None         # frozen frame being annotated (BGR)     
        self.mask = None                   # single-channel binary mask, same size as captured_frame
        self.strokes = []                  # list of strokes; each stroke = list of (x, y) in IMAGE coords
        self._current_stroke = None
        self._last_point = None            # last mouse point in image coords, for line drawing
        self._tk_image = None              # keep reference so Tk doesn't garbage-collect it
        self._scale = 1.0                  # canvas-display-size / image-size scale factor
        self.tool_var = tk.StringVar(value="pen")      # "pen" or "eraser"
        self.brush_size = tk.IntVar(value=PEN_THICKNESS)
        self.zoom_text = tk.StringVar(value="Zoom: 1.0x")
        self.zoom = 1.0
        self.view_x = 0.0                              # top-left of the view, in image pixels
        self.view_y = 0.0
        self._pan_start = None
        self._mouse = None

        self._build_ui()
        self._update_live_feed()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        main = ttk.Frame(self.root)
        main.pack(fill="both", expand=True, padx=8, pady=8)

        # ---- left: canvas + capture controls ----
        left = ttk.Frame(main)
        left.pack(side="left", fill="both", expand=True)

        self.canvas = tk.Canvas(left, width=CANVAS_WIDTH, height=CANVAS_HEIGHT, bg="black")
        self.canvas.pack()
        self.canvas.bind("<ButtonPress-1>", self._on_mouse_down)
        self.canvas.bind("<B1-Motion>", self._on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_mouse_up)
        self.canvas.bind("<Motion>", self._on_mouse_move)
        self.canvas.bind("<Leave>", lambda e: self.canvas.delete("cursor"))
        self.canvas.bind("<MouseWheel>", self._on_wheel)   # Windows / macOS
        self.canvas.bind("<Button-4>", self._on_wheel)     # Linux
        self.canvas.bind("<Button-5>", self._on_wheel)
        for b in (2, 3):                                   # middle or right button = pan
            self.canvas.bind(f"<ButtonPress-{b}>", self._on_pan_start)
            self.canvas.bind(f"<B{b}-Motion>", self._on_pan_move)
            self.canvas.bind(f"<ButtonRelease-{b}>", self._on_pan_end)


        btn_row = ttk.Frame(left)
        btn_row.pack(fill="x", pady=6)

        self.capture_btn = ttk.Button(btn_row, text="Capture Frame  [SPACE]", command=self.capture_frame)
        self.capture_btn.pack(side="left", padx=4)

        self.resume_btn = ttk.Button(btn_row, text="Resume Live Feed", command=self.resume_live, state="disabled")
        self.resume_btn.pack(side="left", padx=4)

        self.undo_btn = ttk.Button(btn_row, text="Undo Last Stroke  [Ctrl+Z]", command=self.undo_stroke, state="disabled")
        self.undo_btn.pack(side="left", padx=4)

        self.clear_btn = ttk.Button(btn_row, text="Clear Marks", command=self.clear_marks, state="disabled")
        self.clear_btn.pack(side="left", padx=4)

        tools = ttk.Frame(left)
        tools.pack(fill="x", pady=(0, 4))
        ttk.Radiobutton(tools, text="Pen [P]", variable=self.tool_var, value="pen",
                        command=self._update_cursor).pack(side="left", padx=4)
        ttk.Radiobutton(tools, text="Eraser [E]", variable=self.tool_var, value="eraser",
                        command=self._update_cursor).pack(side="left", padx=4)
        ttk.Label(tools, text="Brush:").pack(side="left", padx=(12, 2))
        tk.Scale(tools, from_=MIN_BRUSH, to=MAX_BRUSH, orient="horizontal",
                 variable=self.brush_size, length=150).pack(side="left")
        ttk.Button(tools, text="Zoom -", command=lambda: self._zoom_at(
            CANVAS_WIDTH / 2, CANVAS_HEIGHT / 2, 1 / ZOOM_STEP)).pack(side="left", padx=(12, 2))
        ttk.Button(tools, text="Zoom +", command=lambda: self._zoom_at(
            CANVAS_WIDTH / 2, CANVAS_HEIGHT / 2, ZOOM_STEP)).pack(side="left", padx=2)
        ttk.Button(tools, text="Fit", command=self._reset_view).pack(side="left", padx=2)
        ttk.Label(tools, textvariable=self.zoom_text).pack(side="left", padx=6)

        initial_status = ("Live feed running. Press Capture when ready." if self.cap
                          else "Camera not found. Connect it, then click Reconnect camera.")
        self.status_var = tk.StringVar(value=initial_status)
        ttk.Label(left, textvariable=self.status_var, foreground="#444").pack(anchor="w", pady=(4, 0))
        ttk.Button(left, text="Reconnect camera", command=self.reconnect_camera).pack(anchor="w", pady=(4, 0))

        self.root.bind("<space>", self._shortcut(self.capture_frame))
        self.root.bind("<Control-z>", self._shortcut(self.undo_stroke))
        self.root.bind("p", self._shortcut(lambda: self._set_tool("pen")))
        self.root.bind("e", self._shortcut(lambda: self._set_tool("eraser")))
        self.root.bind("<bracketleft>", self._shortcut(lambda: self._change_brush(-1)))
        self.root.bind("<bracketright>", self._shortcut(lambda: self._change_brush(+1)))

        # ---- right: dynamic metadata sidebar ----
        right = ttk.Frame(main, width=260)
        right.pack(side="right", fill="y", padx=(12, 0))

        ttk.Label(right, text="Sample Metadata", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 8))

        # Grade dropdown
        ttk.Label(right, text="Grade").pack(anchor="w")
        self.grade_var = tk.StringVar(value=self.config_data["grades"][0] if self.config_data["grades"] else "")
        ttk.OptionMenu(right, self.grade_var, self.grade_var.get(), *self.config_data["grades"]).pack(fill="x", pady=(0, 8))

        # Size dropdown
        ttk.Label(right, text="Size Category").pack(anchor="w")
        self.size_var = tk.StringVar(value=self.config_data["size_categories"][0] if self.config_data["size_categories"] else "")
        ttk.OptionMenu(right, self.size_var, self.size_var.get(), *self.config_data["size_categories"]).pack(fill="x", pady=(0, 8))

        # Defect type checkboxes (multi-select)
        ttk.Label(right, text="Defect Types Present").pack(anchor="w", pady=(4, 0))
        self.defect_vars = {}
        for defect in self.config_data["defect_types"]:
            var = tk.BooleanVar(value=False)
            ttk.Checkbutton(right, text=defect, variable=var).pack(anchor="w")
            self.defect_vars[defect] = var

        # Free-text fields, generated from config
        ttk.Label(right, text="Additional Fields").pack(anchor="w", pady=(10, 0))
        self.text_field_vars = {}
        for field_name in self.config_data["text_fields"]:
            ttk.Label(right, text=field_name.replace("_", " ").title()).pack(anchor="w")
            var = tk.StringVar(value="")
            ttk.Entry(right, textvariable=var).pack(fill="x", pady=(0, 6))
            self.text_field_vars[field_name] = var

        ttk.Label(right, text="Notes").pack(anchor="w", pady=(6, 0))
        self.notes_text = tk.Text(right, height=4, width=28)
        self.notes_text.pack(fill="x", pady=(0, 10))

        self.save_btn = ttk.Button(right, text="Save Sample", command=self.save_sample, state="disabled")
        self.save_btn.pack(fill="x", pady=(10, 0))

        self.sample_count_var = tk.StringVar(value=self._sample_count_text())
        ttk.Label(right, textvariable=self.sample_count_var, foreground="#666").pack(anchor="w", pady=(8, 0))

        self.brush_size.trace_add("write", lambda *a: self._update_cursor())

    def _sample_count_text(self):
        img_dir = os.path.join(DATASET_DIR, "images")
        n = len([f for f in os.listdir(img_dir) if f.lower().endswith(".jpg")]) if os.path.isdir(img_dir) else 0
        return f"Samples saved so far: {n}"

    # ------------------------------------------------------------------
    # Live feed
    # ------------------------------------------------------------------
    def _update_live_feed(self):
        if self.live_mode and self.cap is not None and self.cap.isOpened():
            ok, frame = self.cap.read()
            if ok:
                self.current_frame = frame
                self._render_to_canvas(frame)
        self.root.after(15, self._update_live_feed)

    def reconnect_camera(self):
        if self.cap is not None:
            self.cap.release()
        self.cap = open_camera(self.camera_source)
        if self.cap is None:
            self.status_var.set(f"Camera unavailable ({self.camera_source}). Check the USB connection and source setting.")
        else:
            self.status_var.set("Camera connected. Live feed running.")

    # Rendering: zoom + pan + red overlay generated from the mask
    # ------------------------------------------------------------------
    def _clamp_view(self, w, h, scale):
        vw, vh = CANVAS_WIDTH / scale, CANVAS_HEIGHT / scale
        # if the image is smaller than the canvas, center it; otherwise keep it in bounds
        self.view_x = (w - vw) / 2 if vw >= w else min(max(self.view_x, 0), w - vw)
        self.view_y = (h - vh) / 2 if vh >= h else min(max(self.view_y, 0), h - vh)

    def _render_to_canvas(self, bgr_frame, mask=None):
        h, w = bgr_frame.shape[:2]
        fit = min(CANVAS_WIDTH / w, CANVAS_HEIGHT / h)
        scale = fit * self.zoom
        self._scale = scale
        self._clamp_view(w, h, scale)
        vx, vy = self.view_x, self.view_y

        # visible region of the image, in whole pixels
        x0 = max(int(math.floor(vx)), 0)
        y0 = max(int(math.floor(vy)), 0)
        x1 = min(int(math.ceil(vx + CANVAS_WIDTH / scale)), w)
        y1 = min(int(math.ceil(vy + CANVAS_HEIGHT / scale)), h)

        canvas_img = np.zeros((CANVAS_HEIGHT, CANVAS_WIDTH, 3), dtype=np.uint8)
        if x1 > x0 and y1 > y0:
            out_w = max(int(round((x1 - x0) * scale)), 1)
            out_h = max(int(round((y1 - y0) * scale)), 1)
            shrink = scale < 1
            interp = cv2.INTER_AREA if shrink else (cv2.INTER_NEAREST if scale >= 6 else cv2.INTER_LINEAR)
            crop = cv2.resize(bgr_frame[y0:y1, x0:x1], (out_w, out_h), interpolation=interp)

            if mask is not None:
                m = cv2.resize(mask[y0:y1, x0:x1], (out_w, out_h),
                               interpolation=cv2.INTER_AREA if shrink else cv2.INTER_LINEAR)
                alpha = (m.astype(np.float32) / 255.0 * OVERLAY_OPACITY)[..., None]
                red = np.array((0, 0, 255), dtype=np.float32)       # BGR red
                crop = (crop * (1 - alpha) + red * alpha).astype(np.uint8)

            px = int(round((x0 - vx) * scale))
            py = int(round((y0 - vy) * scale))
            dx0, dy0 = max(px, 0), max(py, 0)
            dx1, dy1 = min(px + out_w, CANVAS_WIDTH), min(py + out_h, CANVAS_HEIGHT)
            if dx1 > dx0 and dy1 > dy0:
                canvas_img[dy0:dy1, dx0:dx1] = crop[dy0 - py:dy1 - py, dx0 - px:dx1 - px]

        rgb = cv2.cvtColor(canvas_img, cv2.COLOR_BGR2RGB)
        self._tk_image = ImageTk.PhotoImage(Image.fromarray(rgb))
        self.canvas.delete("frame")
        self.canvas.create_image(0, 0, anchor="nw", image=self._tk_image, tags="frame")
        self.canvas.tag_lower("frame")

    def _refresh(self):
        if self.captured_frame is not None:
            self._render_to_canvas(self.captured_frame, self.mask)

    # ------------------------------------------------------------------
    # Zoom / pan
    # ------------------------------------------------------------------
    def _set_zoom_text(self):
        self.zoom_text.set(f"Zoom: {self.zoom:.1f}x")

    def _reset_view(self):
        self.zoom = 1.0
        self.view_x = self.view_y = 0.0
        self._set_zoom_text()
        self._refresh()

    def _zoom_at(self, cx, cy, factor):
        """Zoom keeping the image point under canvas position (cx, cy) fixed."""
        if self.captured_frame is None:
            return
        h, w = self.captured_frame.shape[:2]
        fit = min(CANVAS_WIDTH / w, CANVAS_HEIGHT / h)
        old_scale = fit * self.zoom
        img_x = self.view_x + cx / old_scale
        img_y = self.view_y + cy / old_scale
        self.zoom = min(max(self.zoom * factor, 1.0), MAX_ZOOM)
        new_scale = fit * self.zoom
        self.view_x = img_x - cx / new_scale
        self.view_y = img_y - cy / new_scale
        self._set_zoom_text()
        self._refresh()
        self._update_cursor()

    def _on_wheel(self, event):
        zoom_in = event.num == 4 or event.delta > 0
        self._zoom_at(event.x, event.y, ZOOM_STEP if zoom_in else 1 / ZOOM_STEP)

    def _on_pan_start(self, event):
        if self.captured_frame is not None:
            self._pan_start = (event.x, event.y, self.view_x, self.view_y)

    def _on_pan_move(self, event):
        if self._pan_start is None or self.captured_frame is None:
            return
        sx, sy, vx0, vy0 = self._pan_start
        self.view_x = vx0 - (event.x - sx) / self._scale
        self.view_y = vy0 - (event.y - sy) / self._scale
        self._refresh()

    def _on_pan_end(self, event):
        self._pan_start = None

    # ------------------------------------------------------------------
    # Capture / resume
    # ------------------------------------------------------------------
    def capture_frame(self):
        if not self.live_mode:
            return          # already holding a frame; don't wipe the marks
        if self.current_frame is None:
            messagebox.showwarning("No Frame", "No camera frame available yet.")
            return
        self.live_mode = False
        self.captured_frame = self.current_frame.copy()
        self.mask = np.zeros(self.captured_frame.shape[:2], dtype=np.uint8)
        self.strokes = []
        self._reset_view()
        self.canvas.config(cursor="crosshair")
        self.status_var.set("Frame captured. Draw with the pen; wheel = zoom, right-drag = pan.")
        self.capture_btn.config(state="disabled")
        self.resume_btn.config(state="normal")
        self.undo_btn.config(state="normal")
        self.clear_btn.config(state="normal")
        self.save_btn.config(state="normal")

    def resume_live(self):
        self.live_mode = True
        self.captured_frame = None
        self.mask = None
        self.strokes = []
        self._current_stroke = None
        self.zoom = 1.0
        self.view_x = self.view_y = 0.0
        self._set_zoom_text()
        self.canvas.delete("cursor")
        self.canvas.config(cursor="")
        self.status_var.set("Live feed running. Press Capture when ready.")
        self.capture_btn.config(state="normal")
        self.resume_btn.config(state="disabled")
        self.undo_btn.config(state="disabled")
        self.clear_btn.config(state="disabled")
        self.save_btn.config(state="disabled")

    # ------------------------------------------------------------------
    # Pen / eraser
    # ------------------------------------------------------------------
    def _canvas_to_image_coords(self, cx, cy):
        return (int(math.floor(self.view_x + cx / self._scale)),
                int(math.floor(self.view_y + cy / self._scale)))

    def _in_bounds(self, ix, iy):
        h, w = self.captured_frame.shape[:2]
        return 0 <= ix < w and 0 <= iy < h

    @staticmethod
    def _draw_segment(mask, p1, p2, size, value):
        cv2.line(mask, p1, p2, value, size)
        if size > 2:                                   # round joins between segments
            cv2.circle(mask, p2, size // 2, value, -1)

    def _paint_stroke(self, mask, stroke):
        value = MASK_FOREGROUND if stroke["tool"] == "pen" else MASK_BACKGROUND
        size, pts = stroke["size"], stroke["points"]
        if len(pts) == 1:                              # single click = dot
            cv2.circle(mask, pts[0], size // 2, value, -1)
            return
        for p1, p2 in zip(pts, pts[1:]):
            self._draw_segment(mask, p1, p2, size, value)

    def _on_mouse_down(self, event):
        if self.captured_frame is None:
            return
        ix, iy = self._canvas_to_image_coords(event.x, event.y)
        if not self._in_bounds(ix, iy):
            return
        self._current_stroke = {"tool": self.tool_var.get(),
                                "size": self.brush_size.get(),
                                "points": [(ix, iy)]}
        self._last_point = (ix, iy)
        self._paint_stroke(self.mask, self._current_stroke)
        self._refresh()

    def _on_mouse_drag(self, event):
        self._mouse = (event.x, event.y)
        if self.captured_frame is None or self._current_stroke is None:
            return
        h, w = self.captured_frame.shape[:2]
        ix, iy = self._canvas_to_image_coords(event.x, event.y)
        ix, iy = min(max(ix, 0), w - 1), min(max(iy, 0), h - 1)   # clamp at image edge
        s = self._current_stroke
        value = MASK_FOREGROUND if s["tool"] == "pen" else MASK_BACKGROUND
        self._draw_segment(self.mask, self._last_point, (ix, iy), s["size"], value)
        s["points"].append((ix, iy))
        self._last_point = (ix, iy)
        self._refresh()
        self._update_cursor()

    def _on_mouse_up(self, event):
        if self._current_stroke:
            self.strokes.append(self._current_stroke)   # pen AND eraser strokes are undoable
        self._current_stroke = None
        self._last_point = None

    def _rebuild_mask(self):
        """Replay all strokes in order (pen adds, eraser removes)."""
        self.mask = np.zeros(self.captured_frame.shape[:2], dtype=np.uint8)
        for s in self.strokes:
            self._paint_stroke(self.mask, s)
        self._refresh()

    def undo_stroke(self):
        if self.captured_frame is None or not self.strokes:
            return
        self.strokes.pop()
        self._rebuild_mask()
        self.status_var.set(f"Undid last stroke. {len(self.strokes)} stroke(s) remaining.")

    def clear_marks(self):
        if self.captured_frame is None:
            return
        self.strokes = []
        self._rebuild_mask()
        self.status_var.set("All defect marks cleared.")

    # ------------------------------------------------------------------
    # Brush cursor ring + shortcuts
    # ------------------------------------------------------------------
    def _on_mouse_move(self, event):
        self._mouse = (event.x, event.y)
        self._update_cursor()

    def _update_cursor(self):
        self.canvas.delete("cursor")
        if self.captured_frame is None or self._mouse is None:
            return
        x, y = self._mouse
        r = max(self.brush_size.get() * self._scale / 2, 1.5)
        color = "#ff4040" if self.tool_var.get() == "pen" else "#40c0ff"
        self.canvas.create_oval(x - r, y - r, x + r, y + r, outline=color, tags="cursor")

    def _set_tool(self, tool):
        self.tool_var.set(tool)
        self._update_cursor()

    def _change_brush(self, delta):
        self.brush_size.set(min(max(self.brush_size.get() + delta, MIN_BRUSH), MAX_BRUSH))

    def _shortcut(self, fn):
        """Ignore keyboard shortcuts while the user is typing in a text box."""
        def handler(event):
            if isinstance(event.widget, (tk.Entry, ttk.Entry, tk.Text)):
                return
            fn()
        return handler

    # Save
    # ------------------------------------------------------------------
    def save_sample(self):
        if self.captured_frame is None or self.mask is None:
            messagebox.showwarning("Nothing to Save", "Capture a frame first.")
            return

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        base_name = f"sample_{timestamp}"

        image_path = os.path.join(DATASET_DIR, "images", base_name + ".jpg")
        mask_path = os.path.join(DATASET_DIR, "masks", base_name + "_mask.png")
        json_path = os.path.join(DATASET_DIR, "metadata", base_name + ".json")

        # Save the untouched camera frame. The red annotation is preview-only;
        # the mask is written separately and no watermark is added.
        if not cv2.imwrite(image_path, self.captured_frame):
            messagebox.showerror("Save failed", f"Could not write image:\n{image_path}")
            return
        if not cv2.imwrite(mask_path, self.mask):
            try:
                os.remove(image_path)
            except OSError:
                pass
            messagebox.showerror("Save failed", f"Could not write mask:\n{mask_path}")
            return

        metadata = {
            "timestamp": datetime.datetime.now().isoformat(),
            "image_file": os.path.relpath(image_path, DATASET_DIR),
            "mask_file": os.path.relpath(mask_path, DATASET_DIR),
            "grade": self.grade_var.get(),
            "size_category": self.size_var.get(),
            "defect_types_present": [name for name, var in self.defect_vars.items() if var.get()],
            "notes": self.notes_text.get("1.0", "end").strip(),
        }
        for field_name, var in self.text_field_vars.items():
            metadata[field_name] = var.get()

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)

        self.status_var.set(f"Saved: {base_name}")
        self.sample_count_var.set(self._sample_count_text())
        messagebox.showinfo("Saved", f"Sample saved as:\n{base_name}")

        # Reset notes/defect checkboxes for the next sample, then go back to live view
        self.notes_text.delete("1.0", "end")
        for var in self.defect_vars.values():
            var.set(False)
        self.resume_live()

    # ------------------------------------------------------------------
    def on_close(self):
        if self.cap is not None and self.cap.isOpened():
            self.cap.release()
        self.root.destroy()


def main():
    root = tk.Tk()
    try:
        app = LeatherCaptureApp(root)
    except (OSError, json.JSONDecodeError) as exc:
        root.destroy()
        messagebox.showerror("Startup error", str(exc))
        return
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
