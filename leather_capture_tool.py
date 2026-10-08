import os
import json
import sys
import time
import datetime

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
PEN_THICKNESS = 2          # thickness of the red defect-marking pen, in image pixels
MASK_FOREGROUND = 255       # white = defect
MASK_BACKGROUND = 0         # black = usable


# ======================================================================
# Default config used only if config.json is missing (also written to
# disk so the user has something to edit)
# ======================================================================
DEFAULT_CONFIG = {
    "camera_source": "http://172.30.222.241:4747/video",  # "auto", a camera index such as 0, or a stream URL
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
        self.display_frame = None          # captured_frame + red strokes, for on-screen preview
        self.mask = None                   # single-channel binary mask, same size as captured_frame
        self.strokes = []                  # list of strokes; each stroke = list of (x, y) in IMAGE coords
        self._current_stroke = None
        self._last_point = None            # last mouse point in image coords, for line drawing
        self._tk_image = None              # keep reference so Tk doesn't garbage-collect it
        self._scale = 1.0                  # canvas-display-size / image-size scale factor
        self._offset = (0, 0)              # top-left offset of the image within the canvas

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

        initial_status = ("Live feed running. Press Capture when ready." if self.cap
                          else "Camera not found. Connect it, then click Reconnect camera.")
        self.status_var = tk.StringVar(value=initial_status)
        ttk.Label(left, textvariable=self.status_var, foreground="#444").pack(anchor="w", pady=(4, 0))
        ttk.Button(left, text="Reconnect camera", command=self.reconnect_camera).pack(anchor="w", pady=(4, 0))

        self.root.bind("<space>", lambda e: self.capture_frame())
        self.root.bind("<Control-z>", lambda e: self.undo_stroke())

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

    def _render_to_canvas(self, bgr_frame):
        """Resize (preserving aspect ratio) and draw a BGR frame onto the canvas.
        Also updates self._scale / self._offset so mouse clicks can be mapped
        back to true image pixel coordinates."""
        h, w = bgr_frame.shape[:2]
        scale = min(CANVAS_WIDTH / w, CANVAS_HEIGHT / h)
        disp_w, disp_h = int(w * scale), int(h * scale)
        self._scale = scale
        self._offset = ((CANVAS_WIDTH - disp_w) // 2, (CANVAS_HEIGHT - disp_h) // 2)

        rgb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
        resized = cv2.resize(rgb, (disp_w, disp_h), interpolation=cv2.INTER_AREA)

        canvas_img = np.zeros((CANVAS_HEIGHT, CANVAS_WIDTH, 3), dtype=np.uint8)
        ox, oy = self._offset
        canvas_img[oy:oy + disp_h, ox:ox + disp_w] = resized

        self._tk_image = ImageTk.PhotoImage(Image.fromarray(canvas_img))
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self._tk_image)

    # ------------------------------------------------------------------
    # Capture / resume
    # ------------------------------------------------------------------
    def capture_frame(self):
        if self.current_frame is None:
            messagebox.showwarning("No Frame", "No camera frame available yet.")
            return
        self.live_mode = False
        self.captured_frame = self.current_frame.copy()
        self.display_frame = self.captured_frame.copy()
        self.mask = np.zeros(self.captured_frame.shape[:2], dtype=np.uint8)
        self.strokes = []

        self._render_to_canvas(self.display_frame)
        self.status_var.set("Frame captured. Draw over defects with the mouse (red pen).")
        self.capture_btn.config(state="disabled")
        self.resume_btn.config(state="normal")
        self.undo_btn.config(state="normal")
        self.clear_btn.config(state="normal")
        self.save_btn.config(state="normal")

    def resume_live(self):
        self.live_mode = True
        self.captured_frame = None
        self.display_frame = None
        self.mask = None
        self.strokes = []
        self.status_var.set("Live feed running. Press Capture when ready.")
        self.capture_btn.config(state="normal")
        self.resume_btn.config(state="disabled")
        self.undo_btn.config(state="disabled")
        self.clear_btn.config(state="disabled")
        self.save_btn.config(state="disabled")

    # ------------------------------------------------------------------
    # Freehand pen tool -- mouse events (only active while a frame is captured)
    # ------------------------------------------------------------------
    def _canvas_to_image_coords(self, cx, cy):
        ox, oy = self._offset
        ix = (cx - ox) / self._scale
        iy = (cy - oy) / self._scale
        return int(ix), int(iy)

    def _in_bounds(self, ix, iy):
        h, w = self.captured_frame.shape[:2]
        return 0 <= ix < w and 0 <= iy < h

    def _on_mouse_down(self, event):
        if self.captured_frame is None:
            return
        ix, iy = self._canvas_to_image_coords(event.x, event.y)
        if not self._in_bounds(ix, iy):
            return
        self._current_stroke = [(ix, iy)]
        self._last_point = (ix, iy)

    def _on_mouse_drag(self, event):
        if self.captured_frame is None or self._current_stroke is None:
            return
        ix, iy = self._canvas_to_image_coords(event.x, event.y)
        if not self._in_bounds(ix, iy):
            return
        cv2.line(self.mask, self._last_point, (ix, iy), MASK_FOREGROUND, PEN_THICKNESS, lineType=cv2.LINE_AA)
        cv2.line(self.display_frame, self._last_point, (ix, iy), (0, 0, 255), PEN_THICKNESS, lineType=cv2.LINE_AA)  # BGR red
        self._current_stroke.append((ix, iy))
        self._last_point = (ix, iy)
        self._render_to_canvas(self.display_frame)

    def _on_mouse_up(self, event):
        if self._current_stroke and len(self._current_stroke) > 1:
            self.strokes.append(self._current_stroke)
        self._current_stroke = None
        self._last_point = None

    def _redraw_from_strokes(self):
        """Rebuild the mask and the red-overlay preview from self.strokes.
        Used after Undo/Clear so both stay in sync."""
        self.mask = np.zeros(self.captured_frame.shape[:2], dtype=np.uint8)
        self.display_frame = self.captured_frame.copy()
        for stroke in self.strokes:
            for p1, p2 in zip(stroke, stroke[1:]):
                cv2.line(self.mask, p1, p2, MASK_FOREGROUND, PEN_THICKNESS, lineType=cv2.LINE_AA)
                cv2.line(self.display_frame, p1, p2, (0, 0, 255), PEN_THICKNESS, lineType=cv2.LINE_AA)
        self._render_to_canvas(self.display_frame)

    def undo_stroke(self):
        if self.captured_frame is None or not self.strokes:
            return
        self.strokes.pop()
        self._redraw_from_strokes()
        self.status_var.set(f"Undid last stroke. {len(self.strokes)} stroke(s) remaining.")

    def clear_marks(self):
        if self.captured_frame is None:
            return
        self.strokes = []
        self._redraw_from_strokes()
        self.status_var.set("All defect marks cleared.")

    # ------------------------------------------------------------------
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
