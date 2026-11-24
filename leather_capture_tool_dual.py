import os
import json
import sys
import time
import datetime
import math
import threading
import queue
import subprocess
import shutil

# Try importing cv2 and PIL gracefully so server NEVER fails to start
try:
    import cv2
    import numpy as np
    from PIL import Image, ImageTk
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename

# ======================================================================
# CONFIGURATION & CONSTANTS
# ======================================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")
DEFAULT_DATASET_DIR = os.path.join(SCRIPT_DIR, "dataset")

CANVAS_WIDTH = 800
CANVAS_HEIGHT = 600
PEN_THICKNESS = 4
MIN_BRUSH, MAX_BRUSH = 1, 50
MAX_ZOOM = 16.0
ZOOM_STEP = 1.25
OVERLAY_OPACITY = 0.75
MASK_FOREGROUND = 255
MASK_BACKGROUND = 0

DEFAULT_CONFIG = {
    "camera_source": "0",
    "server_port": 5000,
    "output_directory": DEFAULT_DATASET_DIR,
    "grades": ["A", "B", "C", "D", "Waste"],
    "size_categories": ["Small", "Medium", "Large", "Full Hide", "5ft", "6ft", "7ft", "8ft", "9ft", "10ft", "11ft", "12ft"],
    "defect_types": [
        "Scratch",
        "Hole",
        "Discoloration",
        "Scar",
        "Wrinkle",
        "Vein Marks",
        "Lamp Skin",
        "Spots",
        "Color Variation"
    ],
    "text_fields": ["thickness_mm", "operator_name", "batch_id"],
    "adb_path": ""
}

def load_config():
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
        return DEFAULT_CONFIG.copy()
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        for key, val in DEFAULT_CONFIG.items():
            cfg.setdefault(key, val)
        return cfg
    except Exception as exc:
        print(f"Error loading config: {exc}")
        return DEFAULT_CONFIG.copy()

def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as exc:
        print(f"Error saving config: {exc}")

def find_adb():
    adb_bin = shutil.which("adb")
    if adb_bin:
        return adb_bin
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        standard_path = os.path.join(local_app_data, "Android", "Sdk", "platform-tools", "adb.exe")
        if os.path.isfile(standard_path):
            return standard_path
    return None

def setup_adb_reverse(port=5000, custom_adb=None):
    adb = custom_adb or find_adb()
    if not adb:
        return False, "ADB executable not found. Add Android platform-tools to PATH."
    try:
        proc = subprocess.run([adb, "devices"], capture_output=True, text=True, timeout=5)
        lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        devices = [l for l in lines[1:] if "\tdevice" in l]
        if not devices:
            return False, "No Android device detected. Connect USB cable and enable USB Debugging."
        
        rev_cmd = subprocess.run([adb, "reverse", f"tcp:{port}", f"tcp:{port}"],
                                 capture_output=True, text=True, timeout=5)
        if rev_cmd.returncode == 0:
            return True, f"ADB reverse active: tcp:{port} -> device"
        else:
            return False, f"ADB reverse failed: {rev_cmd.stderr.strip()}"
    except Exception as exc:
        return False, f"ADB error: {str(exc)}"


# ======================================================================
# FLASK INGESTION SERVER
# ======================================================================
flask_app = Flask("LeatherReceiverServer")
incoming_queue = queue.Queue()
active_server_ref = {"app_instance": None}

@flask_app.route("/api/status", methods=["GET"])
def server_status():
    inst = active_server_ref.get("app_instance")
    out_dir = inst.output_dir if inst else DEFAULT_DATASET_DIR
    return jsonify({
        "status": "online",
        "service": "Leather-Capture-PC-Bridge",
        "output_directory": out_dir,
        "timestamp": datetime.datetime.now().isoformat()
    })

@flask_app.route("/api/upload_pair", methods=["POST"])
def upload_pair():
    inst = active_server_ref.get("app_instance")
    target_dir = inst.output_dir if inst else DEFAULT_DATASET_DIR

    if "front_image" not in request.files or "back_image" not in request.files:
        return jsonify({"success": False, "error": "Both front_image and back_image are required."}), 400

    front_file = request.files["front_image"]
    back_file = request.files["back_image"]
    piece_id = request.form.get("piece_id", "").strip()
    if not piece_id:
        timestamp_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        piece_id = f"Piece_{timestamp_str}"
    else:
        piece_id = secure_filename(piece_id)

    raw_metadata = request.form.get("metadata", "{}")
    try:
        metadata_dict = json.loads(raw_metadata)
    except Exception:
        metadata_dict = {}

    piece_dir = os.path.join(target_dir, piece_id)
    os.makedirs(piece_dir, exist_ok=True)

    front_path = os.path.join(piece_dir, "front.jpg")
    back_path = os.path.join(piece_dir, "back.jpg")
    front_file.save(front_path)
    back_file.save(back_path)

    grade_val = metadata_dict.get("grade", "A")
    size_val = metadata_dict.get("size") or metadata_dict.get("size_category", "")
    defects_val = metadata_dict.get("defects") or metadata_dict.get("defect_types_present", [])

    metadata_path = os.path.join(piece_dir, "metadata.json")
    metadata_record = {
        "piece_id": piece_id,
        "received_at": datetime.datetime.now().isoformat(),
        "front_image": "front.jpg",
        "back_image": "back.jpg",
        "grade": grade_val,
        "size_category": size_val,
        "defect_types_present": defects_val,
        "notes": metadata_dict.get("notes", ""),
        "raw_metadata": metadata_dict
    }
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata_record, f, indent=2)

    incoming_queue.put({
        "piece_id": piece_id,
        "piece_dir": piece_dir,
        "front_path": front_path,
        "back_path": back_path,
        "metadata": metadata_record
    })

    print(f"\n[SERVER SUCCESS] Saved {piece_id} into {piece_dir}")

    return jsonify({
        "success": True,
        "piece_id": piece_id,
        "message": f"Successfully received pair for {piece_id}."
    }), 200


def run_flask_server(port):
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    print(f"[SERVER] Starting background ingestion server on port {port}...")
    flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


# ======================================================================
# TKINTER GUI (DUAL-VIEW & DEFECT ANNOTATION)
# ======================================================================
class LeatherDualCaptureApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Leather Dual-Side Dataset & Grading Tool (PC Receiver)")
        self.root.geometry("1280x840")

        self.config_data = load_config()
        self.output_dir = self.config_data.get("output_directory", DEFAULT_DATASET_DIR)
        os.makedirs(self.output_dir, exist_ok=True)
        active_server_ref["app_instance"] = self

        self.current_piece_id = None
        self.current_piece_dir = None
        self.current_side = "front"

        self.front_img = None
        self.front_mask = None
        self.front_strokes = []

        self.back_img = None
        self.back_mask = None
        self.back_strokes = []

        self._current_stroke = None
        self._last_point = None
        self._scale = 1.0
        self.zoom = 1.0
        self.view_x = 0.0
        self.view_y = 0.0
        self._pan_start = None
        self._mouse = None
        self._tk_image = None

        self.tool_var = tk.StringVar(value="pen")
        self.brush_size = tk.IntVar(value=PEN_THICKNESS)
        self.zoom_text = tk.StringVar(value="Zoom: 1.0x")
        self.status_var = tk.StringVar(value="Waiting for mobile phone capture over USB or Wi-Fi...")
        self.usb_status_var = tk.StringVar(value="USB Server starting...")

        self._build_ui()
        self._start_server_thread()
        self._check_adb_bridge()
        self._poll_incoming_queue()

    def _start_server_thread(self):
        port = self.config_data.get("server_port", 5000)
        t = threading.Thread(target=run_flask_server, args=(port,), daemon=True)
        t.start()
        self.usb_status_var.set(f"Port {port} Online")

    def _check_adb_bridge(self):
        port = self.config_data.get("server_port", 5000)
        custom_adb = self.config_data.get("adb_path", "").strip() or None
        success, msg = setup_adb_reverse(port=port, custom_adb=custom_adb)
        if success:
            self.usb_status_var.set(f"USB Linked (Port {port})")
            self.status_var.set("USB ADB active. Ready to receive captures from phone!")
        else:
            self.usb_status_var.set(f"Port {port} Listening")
            self.status_var.set(f"Server online on port {port}. Wi-Fi ready, USB waiting for device.")

    def _build_ui(self):
        top_bar = ttk.Frame(self.root, padding=6)
        top_bar.pack(side="top", fill="x")

        ttk.Label(top_bar, text="Save Destination: ", font=("Segoe UI", 9, "bold")).pack(side="left")
        self.dir_label_var = tk.StringVar(value=self.output_dir)
        dir_lbl = ttk.Label(top_bar, textvariable=self.dir_label_var, foreground="#0066cc", width=50)
        dir_lbl.pack(side="left", padx=4)

        ttk.Button(top_bar, text="Browse Folder...", command=self._select_output_dir).pack(side="left", padx=4)
        ttk.Button(top_bar, text="Refresh USB (ADB)", command=self._check_adb_bridge).pack(side="left", padx=6)
        
        usb_badge = ttk.Label(top_bar, textvariable=self.usb_status_var, font=("Segoe UI", 9, "bold"), foreground="#007700")
        usb_badge.pack(side="right", padx=10)

        sep = ttk.Separator(self.root, orient="horizontal")
        sep.pack(side="top", fill="x")

        main = ttk.Frame(self.root, padding=8)
        main.pack(fill="both", expand=True)

        left = ttk.Frame(main)
        left.pack(side="left", fill="both", expand=True)

        side_selector_bar = ttk.Frame(left)
        side_selector_bar.pack(fill="x", pady=(0, 6))

        self.piece_title_var = tk.StringVar(value="No piece loaded yet.")
        ttk.Label(side_selector_bar, textvariable=self.piece_title_var, font=("Segoe UI", 11, "bold")).pack(side="left", padx=4)

        self.side_var = tk.StringVar(value="front")
        self.btn_front = ttk.Radiobutton(side_selector_bar, text="Side 1: Front (Grain)", value="front",
                                         variable=self.side_var, command=self._on_switch_side)
        self.btn_front.pack(side="right", padx=8)

        self.btn_back = ttk.Radiobutton(side_selector_bar, text="Side 2: Back (Suede/Flesh)", value="back",
                                        variable=self.side_var, command=self._on_switch_side)
        self.btn_back.pack(side="right", padx=8)

        self.canvas = tk.Canvas(left, width=CANVAS_WIDTH, height=CANVAS_HEIGHT, bg="#1a1a1a")
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<ButtonPress-1>", self._on_mouse_down)
        self.canvas.bind("<B1-Motion>", self._on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_mouse_up)
        self.canvas.bind("<Motion>", self._on_mouse_move)
        self.canvas.bind("<Leave>", lambda e: self.canvas.delete("cursor"))
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        for b in (2, 3):
            self.canvas.bind(f"<ButtonPress-{b}>", self._on_pan_start)
            self.canvas.bind(f"<B{b}-Motion>", self._on_pan_move)
            self.canvas.bind(f"<ButtonRelease-{b}>", self._on_pan_end)

        tools = ttk.Frame(left, padding=(0, 6))
        tools.pack(fill="x")

        ttk.Radiobutton(tools, text="Defect Pen [P]", variable=self.tool_var, value="pen",
                        command=self._update_cursor).pack(side="left", padx=4)
        ttk.Radiobutton(tools, text="Eraser [E]", variable=self.tool_var, value="eraser",
                        command=self._update_cursor).pack(side="left", padx=4)

        ttk.Label(tools, text="Brush:").pack(side="left", padx=(12, 2))
        tk.Scale(tools, from_=MIN_BRUSH, to=MAX_BRUSH, orient="horizontal",
                 variable=self.brush_size, length=120).pack(side="left")

        ttk.Button(tools, text="Undo [Ctrl+Z]", command=self.undo_stroke).pack(side="left", padx=4)
        ttk.Button(tools, text="Clear Marks", command=self.clear_marks).pack(side="left", padx=4)
        ttk.Button(tools, text="Fit View", command=self._reset_view).pack(side="left", padx=4)
        ttk.Label(tools, textvariable=self.zoom_text).pack(side="left", padx=6)

        ttk.Label(left, textvariable=self.status_var, foreground="#333", font=("Segoe UI", 9)).pack(anchor="w", pady=(4, 0))

        # Right sidebar
        right = ttk.Frame(main, width=320, padding=(12, 0))
        right.pack(side="right", fill="y")

        ttk.Label(right, text="Leather Inspection & Metadata", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 8))

        ttk.Label(right, text="Quality Grade:").pack(anchor="w")
        self.grade_var = tk.StringVar(value=self.config_data["grades"][0] if self.config_data["grades"] else "A")
        ttk.OptionMenu(right, self.grade_var, self.grade_var.get(), *self.config_data["grades"]).pack(fill="x", pady=(0, 8))

        ttk.Label(right, text="Size Category:").pack(anchor="w")
        self.size_var = tk.StringVar(value=self.config_data["size_categories"][0] if self.config_data["size_categories"] else "Medium")
        ttk.OptionMenu(right, self.size_var, self.size_var.get(), *self.config_data["size_categories"]).pack(fill="x", pady=(0, 8))

        ttk.Label(right, text="Defects Observed:").pack(anchor="w", pady=(4, 0))
        self.defect_frame = ttk.Frame(right)
        self.defect_frame.pack(fill="x", pady=(0, 4))
        self.defect_vars = {}
        for defect in self.config_data["defect_types"]:
            var = tk.BooleanVar(value=False)
            ttk.Checkbutton(self.defect_frame, text=defect, variable=var).pack(anchor="w")
            self.defect_vars[defect] = var

        ttk.Label(right, text="Additional Fields:").pack(anchor="w", pady=(6, 0))
        self.text_field_vars = {}
        for field_name in self.config_data["text_fields"]:
            ttk.Label(right, text=field_name.replace("_", " ").title()).pack(anchor="w")
            var = tk.StringVar(value="")
            ttk.Entry(right, textvariable=var).pack(fill="x", pady=(0, 4))
            self.text_field_vars[field_name] = var

        ttk.Label(right, text="Operator Notes:").pack(anchor="w", pady=(6, 0))
        self.notes_text = tk.Text(right, height=4, width=30)
        self.notes_text.pack(fill="x", pady=(0, 10))

        self.save_btn = ttk.Button(right, text="Save Annotations & Masks", command=self.save_piece_annotations, state="disabled")
        self.save_btn.pack(fill="x", pady=(8, 0))

        self.samples_saved_var = tk.StringVar(value=self._count_saved_pieces())
        ttk.Label(right, textvariable=self.samples_saved_var, foreground="#666").pack(anchor="w", pady=(10, 0))

        self.root.bind("<Control-z>", lambda e: self.undo_stroke())
        self.root.bind("p", lambda e: self.tool_var.set("pen"))
        self.root.bind("e", lambda e: self.tool_var.set("eraser"))

    def _select_output_dir(self):
        chosen = filedialog.askdirectory(initialdir=self.output_dir, title="Select Leather Dataset Save Directory")
        if chosen:
            self.output_dir = chosen
            self.dir_label_var.set(chosen)
            self.config_data["output_directory"] = chosen
            save_config(self.config_data)
            self.samples_saved_var.set(self._count_saved_pieces())
            messagebox.showinfo("Directory Updated", f"Captures will be saved in:\n{chosen}")

    def _count_saved_pieces(self):
        if not os.path.isdir(self.output_dir):
            return "Total Pieces: 0"
        dirs = [d for d in os.listdir(self.output_dir) if os.path.isdir(os.path.join(self.output_dir, d))]
        return f"Total Pieces in Folder: {len(dirs)}"

    def _poll_incoming_queue(self):
        while not incoming_queue.empty():
            item = incoming_queue.get()
            self._load_piece(item)
        self.root.after(300, self._poll_incoming_queue)

    def _load_piece(self, item):
        self.current_piece_id = item["piece_id"]
        self.current_piece_dir = item["piece_dir"]

        if CV2_AVAILABLE:
            self.front_img = cv2.imread(item["front_path"])
            self.back_img = cv2.imread(item["back_path"])

            if self.front_img is not None:
                self.front_mask = np.zeros(self.front_img.shape[:2], dtype=np.uint8)
            if self.back_img is not None:
                self.back_mask = np.zeros(self.back_img.shape[:2], dtype=np.uint8)

        self.front_strokes = []
        self.back_strokes = []

        meta = item["metadata"]
        if meta.get("grade"):
            self.grade_var.set(meta["grade"])
        if meta.get("size_category"):
            self.size_var.set(meta["size_category"])

        for var in self.defect_vars.values():
            var.set(False)

        defects_list = meta.get("defect_types_present", [])
        for d in defects_list:
            if d in self.defect_vars:
                self.defect_vars[d].set(True)
            else:
                var = tk.BooleanVar(value=True)
                ttk.Checkbutton(self.defect_frame, text=d, variable=var).pack(anchor="w")
                self.defect_vars[d] = var

        self.piece_title_var.set(f"Active: {self.current_piece_id}")
        self.status_var.set(f"Successfully received {self.current_piece_id}! Saved in folder.")
        self.save_btn.config(state="normal")
        self.side_var.set("front")
        self._on_switch_side()
        self.samples_saved_var.set(self._count_saved_pieces())

    def _on_switch_side(self):
        self.current_side = self.side_var.get()
        self._reset_view()

    def _get_active_data(self):
        if self.current_side == "front":
            return self.front_img, self.front_mask, self.front_strokes
        else:
            return self.back_img, self.back_mask, self.back_strokes

    def _render(self):
        if not CV2_AVAILABLE:
            self.canvas.delete("all")
            self.canvas.create_text(
                CANVAS_WIDTH // 2, CANVAS_HEIGHT // 2,
                text=f"SUCCESS: Photos received and saved to disk!\n({self.current_piece_id or 'Waiting for uploads'})\n\nTo view and draw defect masks on PC canvas, run:\npip install opencv-python pillow",
                fill="#4caf50", font=("Segoe UI", 12, "bold"), justify="center"
            )
            return

        img, mask, _ = self._get_active_data()
        if img is None:
            self.canvas.delete("all")
            self.canvas.create_text(CANVAS_WIDTH // 2, CANVAS_HEIGHT // 2,
                                    text="No leather image loaded.\nConnect Android App via USB or Wi-Fi and capture a pair.",
                                    fill="#777", font=("Segoe UI", 12), justify="center")
            return

        h, w = img.shape[:2]
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        if cw <= 1 or ch <= 1:
            cw, ch = CANVAS_WIDTH, CANVAS_HEIGHT

        fit = min(cw / w, ch / h)
        scale = fit * self.zoom
        self._scale = scale

        vw, vh = cw / scale, ch / scale
        self.view_x = (w - vw) / 2 if vw >= w else min(max(self.view_x, 0), w - vw)
        self.view_y = (h - vh) / 2 if vh >= h else min(max(self.view_y, 0), h - vh)

        x0 = max(int(math.floor(self.view_x)), 0)
        y0 = max(int(math.floor(self.view_y)), 0)
        x1 = min(int(math.ceil(self.view_x + cw / scale)), w)
        y1 = min(int(math.ceil(self.view_y + ch / scale)), h)

        canvas_img = np.zeros((ch, cw, 3), dtype=np.uint8)
        if x1 > x0 and y1 > y0:
            out_w = max(int(round((x1 - x0) * scale)), 1)
            out_h = max(int(round((y1 - y0) * scale)), 1)
            crop = cv2.resize(img[y0:y1, x0:x1], (out_w, out_h), interpolation=cv2.INTER_LINEAR)

            if mask is not None:
                m = cv2.resize(mask[y0:y1, x0:x1], (out_w, out_h), interpolation=cv2.INTER_LINEAR)
                alpha = (m.astype(np.float32) / 255.0 * OVERLAY_OPACITY)[..., None]
                red = np.array((0, 0, 255), dtype=np.float32)
                crop = (crop * (1 - alpha) + red * alpha).astype(np.uint8)

            px = int(round((x0 - self.view_x) * scale))
            py = int(round((y0 - self.view_y) * scale))
            dx0, dy0 = max(px, 0), max(py, 0)
            dx1, dy1 = min(px + out_w, cw), min(py + out_h, ch)
            if dx1 > dx0 and dy1 > dy0:
                canvas_img[dy0:dy1, dx0:dx1] = crop[dy0 - py:dy1 - py, dx0 - px:dx1 - px]

        rgb = cv2.cvtColor(canvas_img, cv2.COLOR_BGR2RGB)
        self._tk_image = ImageTk.PhotoImage(Image.fromarray(rgb))
        self.canvas.delete("frame")
        self.canvas.create_image(0, 0, anchor="nw", image=self._tk_image, tags="frame")
        self.canvas.tag_lower("frame")

    def _reset_view(self):
        self.zoom = 1.0
        self.view_x = 0.0
        self.view_y = 0.0
        self.zoom_text.set("Zoom: 1.0x")
        self._render()

    def _zoom_at(self, cx, cy, factor):
        if not CV2_AVAILABLE:
            return
        img, _, _ = self._get_active_data()
        if img is None:
            return
        h, w = img.shape[:2]
        cw = max(self.canvas.winfo_width(), CANVAS_WIDTH)
        ch = max(self.canvas.winfo_height(), CANVAS_HEIGHT)
        fit = min(cw / w, ch / h)
        old_scale = fit * self.zoom
        img_x = self.view_x + cx / old_scale
        img_y = self.view_y + cy / old_scale
        self.zoom = min(max(self.zoom * factor, 1.0), MAX_ZOOM)
        new_scale = fit * self.zoom
        self.view_x = img_x - cx / new_scale
        self.view_y = img_y - cy / new_scale
        self.zoom_text.set(f"Zoom: {self.zoom:.1f}x")
        self._render()
        self._update_cursor()

    def _on_wheel(self, event):
        zoom_in = event.num == 4 or event.delta > 0
        self._zoom_at(event.x, event.y, ZOOM_STEP if zoom_in else 1 / ZOOM_STEP)

    def _on_pan_start(self, event):
        self._pan_start = (event.x, event.y, self.view_x, self.view_y)

    def _on_pan_move(self, event):
        if self._pan_start is None:
            return
        sx, sy, vx0, vy0 = self._pan_start
        self.view_x = vx0 - (event.x - sx) / self._scale
        self.view_y = vy0 - (event.y - sy) / self._scale
        self._render()

    def _on_pan_end(self, event):
        self._pan_start = None

    def _canvas_to_image_coords(self, cx, cy):
        return (int(math.floor(self.view_x + cx / self._scale)),
                int(math.floor(self.view_y + cy / self._scale)))

    def _paint_stroke(self, mask, stroke):
        if not CV2_AVAILABLE:
            return
        value = MASK_FOREGROUND if stroke["tool"] == "pen" else MASK_BACKGROUND
        size, pts = stroke["size"], stroke["points"]
        if len(pts) == 1:
            cv2.circle(mask, pts[0], size // 2, value, -1)
            return
        for p1, p2 in zip(pts, pts[1:]):
            cv2.line(mask, p1, p2, value, size)
            if size > 2:
                cv2.circle(mask, p2, size // 2, value, -1)

    def _on_mouse_down(self, event):
        if not CV2_AVAILABLE:
            return
        img, mask, strokes = self._get_active_data()
        if img is None:
            return
        ix, iy = self._canvas_to_image_coords(event.x, event.y)
        h, w = img.shape[:2]
        if not (0 <= ix < w and 0 <= iy < h):
            return
        self._current_stroke = {"tool": self.tool_var.get(),
                                "size": self.brush_size.get(),
                                "points": [(ix, iy)]}
        self._last_point = (ix, iy)
        self._paint_stroke(mask, self._current_stroke)
        self._render()

    def _on_mouse_drag(self, event):
        if not CV2_AVAILABLE:
            return
        self._mouse = (event.x, event.y)
        img, mask, strokes = self._get_active_data()
        if img is None or self._current_stroke is None:
            return
        h, w = img.shape[:2]
        ix, iy = self._canvas_to_image_coords(event.x, event.y)
        ix, iy = min(max(ix, 0), w - 1), min(max(iy, 0), h - 1)
        s = self._current_stroke
        value = MASK_FOREGROUND if s["tool"] == "pen" else MASK_BACKGROUND
        cv2.line(mask, self._last_point, (ix, iy), value, s["size"])
        s["points"].append((ix, iy))
        self._last_point = (ix, iy)
        self._render()
        self._update_cursor()

    def _on_mouse_up(self, event):
        if self._current_stroke:
            _, _, strokes = self._get_active_data()
            strokes.append(self._current_stroke)
        self._current_stroke = None
        self._last_point = None

    def undo_stroke(self):
        if not CV2_AVAILABLE:
            return
        img, mask, strokes = self._get_active_data()
        if img is None or not strokes:
            return
        strokes.pop()
        mask.fill(0)
        for s in strokes:
            self._paint_stroke(mask, s)
        self._render()

    def clear_marks(self):
        if not CV2_AVAILABLE:
            return
        img, mask, strokes = self._get_active_data()
        if img is None:
            return
        strokes.clear()
        mask.fill(0)
        self._render()

    def _on_mouse_move(self, event):
        self._mouse = (event.x, event.y)
        self._update_cursor()

    def _update_cursor(self):
        self.canvas.delete("cursor")
        if self._mouse is None:
            return
        x, y = self._mouse
        r = max(self.brush_size.get() * self._scale / 2, 1.5)
        color = "#ff3333" if self.tool_var.get() == "pen" else "#3399ff"
        self.canvas.create_oval(x - r, y - r, x + r, y + r, outline=color, tags="cursor")

    def save_piece_annotations(self):
        if not self.current_piece_dir:
            return

        if CV2_AVAILABLE:
            front_mask_path = os.path.join(self.current_piece_dir, "front_mask.png")
            back_mask_path = os.path.join(self.current_piece_dir, "back_mask.png")
            if self.front_mask is not None:
                cv2.imwrite(front_mask_path, self.front_mask)
            if self.back_mask is not None:
                cv2.imwrite(back_mask_path, self.back_mask)

        metadata_path = os.path.join(self.current_piece_dir, "metadata.json")
        data = {}
        if os.path.exists(metadata_path):
            try:
                with open(metadata_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                pass

        data["grade"] = self.grade_var.get()
        data["size_category"] = self.size_var.get()
        data["defect_types_present"] = [name for name, var in self.defect_vars.items() if var.get()]
        data["notes"] = self.notes_text.get("1.0", "end").strip()
        data["custom_fields"] = {field: var.get() for field, var in self.text_field_vars.items()}
        data["annotated_at"] = datetime.datetime.now().isoformat()

        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        messagebox.showinfo("Saved", f"Annotations & masks successfully saved for {self.current_piece_id}!")
        self.status_var.set(f"Completed {self.current_piece_id}. Waiting for next phone capture.")


def main():
    root = tk.Tk()
    app = LeatherDualCaptureApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
