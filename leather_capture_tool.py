import os
import json
import sys
import time
import datetime
import math
import socket
import threading
import queue
import subprocess
import shutil

# Graceful import of image processing packages
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

CANVAS_WIDTH = 840
CANVAS_HEIGHT = 620
PEN_THICKNESS = 4
MIN_BRUSH, MAX_BRUSH = 1, 50
MAX_ZOOM = 16.0
ZOOM_STEP = 1.25
OVERLAY_OPACITY = 0.75
MASK_FOREGROUND = 255
MASK_BACKGROUND = 0

DEFAULT_CONFIG = {
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

# ======================================================================
# NETWORK UTILITIES (DYNAMIC IP RESOLUTION)
# ======================================================================
def get_local_ip():
    """Detect current local LAN IP dynamically without any hardcoding."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        # Standard UDP route discovery (no packets actually sent)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    try:
        host_name = socket.gethostname()
        ip = socket.gethostbyname(host_name)
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    return "127.0.0.1"

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
        print(f"[WARN] Error loading config: {exc}")
        return DEFAULT_CONFIG.copy()

def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as exc:
        print(f"[WARN] Error saving config: {exc}")

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
        return False, "ADB not found (USB port-forwarding unavailable)."
    try:
        proc = subprocess.run([adb, "devices"], capture_output=True, text=True, timeout=5)
        lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        devices = [l for l in lines[1:] if "\tdevice" in l]
        if not devices:
            return False, "No Android device detected over USB."
        
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
flask_app = Flask("LeatherCaptureServer")
incoming_queue = queue.Queue()
active_server_ref = {"app_instance": None}

@flask_app.route("/api/status", methods=["GET"])
def server_status():
    inst = active_server_ref.get("app_instance")
    out_dir = inst.output_dir if inst else DEFAULT_DATASET_DIR
    return jsonify({
        "status": "online",
        "service": "Leather-Capture-Server-v2",
        "host_ip": get_local_ip(),
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
    size_val = metadata_dict.get("size") or metadata_dict.get("size_category", "Medium")
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

    print(f"\n[RECEIVED PAIR] {piece_id} -> Saved to: {piece_dir}")

    # Automatically synchronize Kaggle metadata.csv & index
    try:
        import dataset_manager
        dataset_manager.sync_dataset_to_kaggle_format(target_dir)
    except Exception as exc:
        print(f"[WARN] Kaggle dataset sync notice: {exc}")

    return jsonify({
        "success": True,
        "piece_id": piece_id,
        "message": f"Successfully received pair for {piece_id}."
    }), 200

def run_flask_server(port):
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


# ======================================================================
# MODERN DARK THEMED DESKTOP APPLICATION
# ======================================================================
class LeatherCaptureApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Leather Dataset Capture & Grading Tool (v2.0 Pro)")
        self.root.geometry("1340x880")
        self.root.minsize(1080, 700)

        # Dynamic Network Info
        self.local_ip = get_local_ip()
        self.config_data = load_config()
        self.server_port = self.config_data.get("server_port", 5000)
        self.output_dir = self.config_data.get("output_directory", DEFAULT_DATASET_DIR)
        os.makedirs(self.output_dir, exist_ok=True)
        active_server_ref["app_instance"] = self

        # Dark Theme Palette
        self.BG_MAIN = "#121214"
        self.BG_CARD = "#1A1A1F"
        self.BG_INPUT = "#24242C"
        self.BORDER_COLOR = "#2F2F3D"
        self.ACCENT_CYAN = "#00E5FF"
        self.ACCENT_BLUE = "#3B82F6"
        self.TEXT_PRIMARY = "#FFFFFF"
        self.TEXT_MUTED = "#9CA3AF"
        self.TEXT_GREEN = "#10B981"
        self.TEXT_ORANGE = "#F59E0B"

        self.root.configure(bg=self.BG_MAIN)

        # State Variables
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
        self.status_var = tk.StringVar(value="Ready. Waiting for incoming photos from phone via USB or Wi-Fi...")
        self.usb_status_var = tk.StringVar(value="USB: Probing...")
        self.wifi_url_var = tk.StringVar(value=f"http://{self.local_ip}:{self.server_port}")

        self._init_styles()
        self._build_ui()
        self._start_server_thread()
        self._check_adb_bridge()
        self._poll_incoming_queue()

    def _init_styles(self):
        style = ttk.Style()
        style.theme_use("clam")

        style.configure(".", background=self.BG_MAIN, foreground=self.TEXT_PRIMARY, font=("Segoe UI", 9))
        style.configure("TFrame", background=self.BG_MAIN)
        style.configure("Card.TFrame", background=self.BG_CARD, relief="flat")
        style.configure("TLabel", background=self.BG_MAIN, foreground=self.TEXT_PRIMARY)
        style.configure("Card.TLabel", background=self.BG_CARD, foreground=self.TEXT_PRIMARY)
        style.configure("Muted.TLabel", background=self.BG_CARD, foreground=self.TEXT_MUTED, font=("Segoe UI", 8))
        style.configure("Title.TLabel", background=self.BG_CARD, foreground=self.TEXT_PRIMARY, font=("Segoe UI", 11, "bold"))
        
        style.configure("Accent.TButton", background=self.ACCENT_BLUE, foreground="#FFFFFF",
                        font=("Segoe UI", 9, "bold"), borderwidth=0, padding=6)
        style.map("Accent.TButton", background=[("active", "#2563EB")])

        style.configure("Secondary.TButton", background=self.BG_INPUT, foreground=self.TEXT_PRIMARY,
                        borderwidth=1, relief="flat", padding=5)
        style.map("Secondary.TButton", background=[("active", "#32323E")])

        style.configure("TRadiobutton", background=self.BG_CARD, foreground=self.TEXT_PRIMARY)
        style.map("TRadiobutton", background=[("active", self.BG_CARD)])
        style.configure("TCheckbutton", background=self.BG_CARD, foreground=self.TEXT_PRIMARY)
        style.map("TCheckbutton", background=[("active", self.BG_CARD)])

    def _start_server_thread(self):
        t = threading.Thread(target=run_flask_server, args=(self.server_port,), daemon=True)
        t.start()
        print(f"\n========================================================")
        print(f" LEATHER CAPTURE INGESTION SERVER ONLINE")
        print(f" -> Wi-Fi Address : http://{self.local_ip}:{self.server_port}")
        print(f" -> USB Loopback  : http://127.0.0.1:{self.server_port}")
        print(f"========================================================\n")

    def _check_adb_bridge(self):
        custom_adb = self.config_data.get("adb_path", "").strip() or None
        success, msg = setup_adb_reverse(port=self.server_port, custom_adb=custom_adb)
        if success:
            self.usb_status_var.set("USB: Linked (5000)")
            self.status_var.set("USB cable linked via ADB reverse! Ready for captures.")
        else:
            self.usb_status_var.set("USB: Waiting")
            self.status_var.set(f"Server active. Use Wi-Fi (http://{self.local_ip}:{self.server_port}) or plug in USB.")

    def _copy_wifi_url(self):
        url = self.wifi_url_var.get()
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        messagebox.showinfo("URL Copied", f"Server URL copied to clipboard:\n\n{url}\n\nEnter this in the Android App Settings.")

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
            return "Total Saved Pieces: 0"
        dirs = [d for d in os.listdir(self.output_dir) if os.path.isdir(os.path.join(self.output_dir, d))]
        return f"Total Saved Pieces: {len(dirs)}"

    def _build_ui(self):
        # ---------------- TOP NAVIGATION BAR ----------------
        top_bar = tk.Frame(self.root, bg=self.BG_CARD, padx=12, pady=8, highlightthickness=1, highlightbackground=self.BORDER_COLOR)
        top_bar.pack(side="top", fill="x")

        # Left: Branding
        lbl_brand = tk.Label(top_bar, text="✦ Leather Capture Pro", bg=self.BG_CARD, fg=self.ACCENT_CYAN,
                             font=("Segoe UI", 12, "bold"))
        lbl_brand.pack(side="left", padx=(0, 16))

        # Save Folder Selector
        tk.Label(top_bar, text="Save To:", bg=self.BG_CARD, fg=self.TEXT_MUTED, font=("Segoe UI", 9)).pack(side="left")
        self.dir_label_var = tk.StringVar(value=self.output_dir)
        dir_lbl = tk.Label(top_bar, textvariable=self.dir_label_var, bg=self.BG_INPUT, fg=self.TEXT_PRIMARY,
                           padx=8, pady=3, font=("Segoe UI", 8), width=32, anchor="w")
        dir_lbl.pack(side="left", padx=6)
        ttk.Button(top_bar, text="Browse...", style="Secondary.TButton", command=self._select_output_dir).pack(side="left", padx=2)
        ttk.Button(top_bar, text="📊 Sync Kaggle CSV", style="Secondary.TButton", command=self._sync_kaggle_csv).pack(side="left", padx=4)

        # Right: Connection Badges
        btn_copy = ttk.Button(top_bar, text="Copy Wi-Fi URL", style="Secondary.TButton", command=self._copy_wifi_url)
        btn_copy.pack(side="right", padx=(4, 0))

        # Dynamic Wi-Fi Badge
        wifi_frame = tk.Frame(top_bar, bg=self.BG_INPUT, padx=8, pady=3, highlightthickness=1, highlightbackground=self.BORDER_COLOR)
        wifi_frame.pack(side="right", padx=6)
        tk.Label(wifi_frame, text="Wi-Fi:", bg=self.BG_INPUT, fg=self.TEXT_MUTED, font=("Segoe UI", 8)).pack(side="left")
        lbl_wifi = tk.Label(wifi_frame, textvariable=self.wifi_url_var, bg=self.BG_INPUT, fg=self.ACCENT_CYAN, font=("Segoe UI", 8, "bold"))
        lbl_wifi.pack(side="left", padx=4)

        # USB ADB Badge
        usb_frame = tk.Frame(top_bar, bg=self.BG_INPUT, padx=8, pady=3, highlightthickness=1, highlightbackground=self.BORDER_COLOR)
        usb_frame.pack(side="right", padx=6)
        lbl_usb = tk.Label(usb_frame, textvariable=self.usb_status_var, bg=self.BG_INPUT, fg=self.TEXT_GREEN, font=("Segoe UI", 8, "bold"))
        lbl_usb.pack(side="left")
        ttk.Button(top_bar, text="↻ Refresh USB", style="Secondary.TButton", command=self._check_adb_bridge).pack(side="right", padx=4)

        # ---------------- MAIN CONTENT SPLIT ----------------
        content = tk.Frame(self.root, bg=self.BG_MAIN, padx=10, pady=10)
        content.pack(fill="both", expand=True)

        # Left Column: Canvas & Controls
        left = tk.Frame(content, bg=self.BG_MAIN)
        left.pack(side="left", fill="both", expand=True)

        # Side Selector Segmented Bar
        side_bar = tk.Frame(left, bg=self.BG_CARD, padx=10, pady=6, highlightthickness=1, highlightbackground=self.BORDER_COLOR)
        side_bar.pack(fill="x", pady=(0, 6))

        self.piece_title_var = tk.StringVar(value="Waiting for first capture...")
        tk.Label(side_bar, textvariable=self.piece_title_var, bg=self.BG_CARD, fg=self.TEXT_PRIMARY, font=("Segoe UI", 11, "bold")).pack(side="left")

        self.side_var = tk.StringVar(value="front")
        self.btn_side_front = tk.Radiobutton(side_bar, text="✦ Side 1: Front (Grain)", value="front",
                                             variable=self.side_var, command=self._on_switch_side,
                                             bg=self.BG_CARD, fg=self.ACCENT_CYAN, selectcolor=self.BG_INPUT,
                                             activebackground=self.BG_CARD, activeforeground=self.ACCENT_CYAN,
                                             indicatoron=False, padx=12, pady=4, font=("Segoe UI", 9, "bold"))
        self.btn_side_front.pack(side="right", padx=4)

        self.btn_side_back = tk.Radiobutton(side_bar, text="✦ Side 2: Back (Suede)", value="back",
                                            variable=self.side_var, command=self._on_switch_side,
                                            bg=self.BG_CARD, fg=self.TEXT_ORANGE, selectcolor=self.BG_INPUT,
                                            activebackground=self.BG_CARD, activeforeground=self.TEXT_ORANGE,
                                            indicatoron=False, padx=12, pady=4, font=("Segoe UI", 9, "bold"))
        self.btn_side_back.pack(side="right", padx=4)

        # Canvas Viewport
        self.canvas = tk.Canvas(left, width=CANVAS_WIDTH, height=CANVAS_HEIGHT, bg="#16161A",
                                highlightthickness=1, highlightbackground=self.BORDER_COLOR)
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

        # Annotation Tools Bar
        tool_bar = tk.Frame(left, bg=self.BG_CARD, padx=8, pady=6, highlightthickness=1, highlightbackground=self.BORDER_COLOR)
        tool_bar.pack(fill="x", pady=(6, 0))

        ttk.Radiobutton(tool_bar, text="Defect Pen [P]", variable=self.tool_var, value="pen",
                        command=self._update_cursor).pack(side="left", padx=4)
        ttk.Radiobutton(tool_bar, text="Eraser [E]", variable=self.tool_var, value="eraser",
                        command=self._update_cursor).pack(side="left", padx=4)

        tk.Label(tool_bar, text="Brush:", bg=self.BG_CARD, fg=self.TEXT_MUTED).pack(side="left", padx=(12, 2))
        tk.Scale(tool_bar, from_=MIN_BRUSH, to=MAX_BRUSH, orient="horizontal",
                 variable=self.brush_size, length=110, bg=self.BG_CARD, fg=self.TEXT_PRIMARY,
                 troughcolor=self.BG_INPUT, highlightthickness=0).pack(side="left")

        ttk.Button(tool_bar, text="Undo [Ctrl+Z]", style="Secondary.TButton", command=self.undo_stroke).pack(side="left", padx=4)
        ttk.Button(tool_bar, text="Clear", style="Secondary.TButton", command=self.clear_marks).pack(side="left", padx=4)
        ttk.Button(tool_bar, text="Fit View", style="Secondary.TButton", command=self._reset_view).pack(side="left", padx=4)
        tk.Label(tool_bar, textvariable=self.zoom_text, bg=self.BG_CARD, fg=self.ACCENT_CYAN, font=("Segoe UI", 8, "bold")).pack(side="left", padx=6)

        # Live Status Line
        status_line = tk.Frame(left, bg=self.BG_MAIN, pady=4)
        status_line.pack(fill="x")
        tk.Label(status_line, textvariable=self.status_var, bg=self.BG_MAIN, fg=self.TEXT_MUTED, font=("Segoe UI", 8)).pack(anchor="w")

        # ---------------- RIGHT COLUMN: METADATA & INSPECTION ----------------
        right = tk.Frame(content, bg=self.BG_CARD, width=320, padx=14, pady=12, highlightthickness=1, highlightbackground=self.BORDER_COLOR)
        right.pack(side="right", fill="y", padx=(10, 0))

        tk.Label(right, text="Quality Grading & Inspector", bg=self.BG_CARD, fg=self.TEXT_PRIMARY, font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 10))

        # Grade
        tk.Label(right, text="LEATHER GRADE", bg=self.BG_CARD, fg=self.TEXT_MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self.grade_var = tk.StringVar(value="A")
        grade_frame = tk.Frame(right, bg=self.BG_CARD)
        grade_frame.pack(fill="x", pady=(2, 10))
        for g in self.config_data["grades"]:
            tk.Radiobutton(grade_frame, text=g, value=g, variable=self.grade_var,
                           bg=self.BG_CARD, fg=self.TEXT_PRIMARY, selectcolor=self.BG_INPUT,
                           indicatoron=False, padx=8, pady=3, font=("Segoe UI", 8, "bold")).pack(side="left", padx=2)

        # Size
        tk.Label(right, text="PIECE SIZE", bg=self.BG_CARD, fg=self.TEXT_MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self.size_var = tk.StringVar(value="Medium")
        size_combo = ttk.Combobox(right, textvariable=self.size_var, values=self.config_data["size_categories"], state="readonly")
        size_combo.pack(fill="x", pady=(2, 10))

        # Defects Checkboxes
        tk.Label(right, text="DEFECT TYPES OBSERVED", bg=self.BG_CARD, fg=self.TEXT_MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self.defect_box = tk.Frame(right, bg=self.BG_INPUT, padx=8, pady=8, highlightthickness=1, highlightbackground=self.BORDER_COLOR)
        self.defect_box.pack(fill="x", pady=(2, 10))

        self.defect_vars = {}
        for defect in self.config_data["defect_types"]:
            var = tk.BooleanVar(value=False)
            tk.Checkbutton(self.defect_box, text=defect, variable=var,
                           bg=self.BG_INPUT, fg=self.TEXT_PRIMARY, selectcolor=self.BG_CARD,
                           activebackground=self.BG_INPUT, font=("Segoe UI", 8)).pack(anchor="w", pady=1)
            self.defect_vars[defect] = var

        # Notes
        tk.Label(right, text="OPERATOR NOTES", bg=self.BG_CARD, fg=self.TEXT_MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self.notes_text = tk.Text(right, height=4, width=28, bg=self.BG_INPUT, fg=self.TEXT_PRIMARY,
                                  highlightthickness=1, highlightbackground=self.BORDER_COLOR, insertbackground=self.TEXT_PRIMARY)
        self.notes_text.pack(fill="x", pady=(2, 12))

        # Save Action Button
        self.save_btn = tk.Button(right, text="✓ Save Annotations & Masks", bg=self.ACCENT_BLUE, fg="#FFFFFF",
                                  activebackground="#2563EB", activeforeground="#FFFFFF",
                                  font=("Segoe UI", 10, "bold"), relief="flat", padx=10, pady=8,
                                  command=self.save_piece_annotations, state="disabled")
        self.save_btn.pack(fill="x", pady=(4, 10))

        self.samples_saved_var = tk.StringVar(value=self._count_saved_pieces())
        tk.Label(right, textvariable=self.samples_saved_var, bg=self.BG_CARD, fg=self.TEXT_MUTED, font=("Segoe UI", 8)).pack(anchor="w")

        # Shortcuts
        self.root.bind("<Control-z>", lambda e: self.undo_stroke())
        self.root.bind("p", lambda e: self.tool_var.set("pen"))
        self.root.bind("e", lambda e: self.tool_var.set("eraser"))

    # ------------------------------------------------------------------
    # INCOMING QUEUE POLLER
    # ------------------------------------------------------------------
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
                tk.Checkbutton(self.defect_box, text=d, variable=var,
                               bg=self.BG_INPUT, fg=self.TEXT_PRIMARY, selectcolor=self.BG_CARD,
                               activebackground=self.BG_INPUT, font=("Segoe UI", 8)).pack(anchor="w", pady=1)
                self.defect_vars[d] = var

        self.piece_title_var.set(f"Active: {self.current_piece_id}")
        self.status_var.set(f"Received pair for {self.current_piece_id}! Saved to folder. Ready for review.")
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

    # ------------------------------------------------------------------
    # CANVAS RENDERING
    # ------------------------------------------------------------------
    def _render(self):
        if not CV2_AVAILABLE:
            self.canvas.delete("all")
            self.canvas.create_text(
                CANVAS_WIDTH // 2, CANVAS_HEIGHT // 2,
                text=f"✓ PHOTOS RECEIVED & SAVED TO PC DISK!\n({self.current_piece_id or 'Awaiting Captures'})\n\n[Optional] To render live visual defect mask canvas:\nRun: pip install opencv-python pillow",
                fill="#10B981", font=("Segoe UI", 12, "bold"), justify="center"
            )
            return

        img, mask, _ = self._get_active_data()
        if img is None:
            self.canvas.delete("all")
            self.canvas.create_text(CANVAS_WIDTH // 2, CANVAS_HEIGHT // 2,
                                    text="No leather hide loaded.\nCapture Front and Back photos from the Android app\n(Connect via USB or Wi-Fi).",
                                    fill="#71717A", font=("Segoe UI", 12), justify="center")
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
        color = "#FF3B30" if self.tool_var.get() == "pen" else self.ACCENT_CYAN
        self.canvas.create_oval(x - r, y - r, x + r, y + r, outline=color, width=2, tags="cursor")

    def _sync_kaggle_csv(self):
        try:
            import dataset_manager
            s, p = dataset_manager.sync_dataset_to_kaggle_format(self.output_dir)
            messagebox.showinfo("Kaggle Dataset Synced",
                                f"Successfully synced Kaggle metadata:\n\n"
                                f"• metadata.csv ({s} samples)\n"
                                f"• pieces_summary.csv ({p} hides)\n\n"
                                f"Location:\n{self.output_dir}")
        except Exception as exc:
            messagebox.showerror("Sync Error", f"Could not sync Kaggle CSV:\n{exc}")

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
        data["custom_fields"] = {field: var.get() for field, var in self.text_field_vars.items()} if hasattr(self, 'text_field_vars') else {}
        data["annotated_at"] = datetime.datetime.now().isoformat()

        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        # Automatically synchronize Kaggle metadata.csv with new masks and labels
        try:
            import dataset_manager
            dataset_manager.sync_dataset_to_kaggle_format(self.output_dir)
            self.samples_saved_var.set(self._count_saved_pieces())
        except Exception as exc:
            print(f"[WARN] Kaggle dataset sync notice: {exc}")

        messagebox.showinfo("Saved", f"Annotations & masks successfully saved for:\n{self.current_piece_id}")
        self.status_var.set(f"Saved {self.current_piece_id}. Waiting for next phone capture.")


def main():
    root = tk.Tk()
    app = LeatherCaptureApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
