"""
polarization_quad_viewer.py
 
GUI viewer for Thorlabs polarization cameras (e.g. CS505MUP).
Mimics ThorImageCAM layout with:
  - Sidebar: Polar Image Type selector (Quad View, 0deg, -45deg, 45deg, 90deg)
  - Three buttons: Live, Snapshot, Capture
  - Main display area showing selected view
 
SETUP:
    1. Close ThorImageCAM
    2. Place this file + DLLs in your directory:
    3. py polarization_quad_viewer.py
"""
 
import os
import datetime
import threading
import numpy as np
import cv2
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk
 
# -- DLL path ------------------------------------------------------------------
MANUAL_DLL_PATH = r"C:\YOUR_DIRECTORY"
os.environ['PATH'] = MANUAL_DLL_PATH + os.pathsep + os.environ['PATH']
try:
    os.add_dll_directory(MANUAL_DLL_PATH)
except AttributeError:
    pass
 
from thorlabs_tsi_sdk.tl_camera import TLCameraSDK
from thorlabs_tsi_sdk.tl_camera_enums import SENSOR_TYPE
from thorlabs_tsi_sdk.tl_polarization_processor import PolarizationProcessorSDK
 
# -- Save location -------------------------------------------------------------
SAVE_DIR = r"C:\YOUR_DIRECTORY"
 
# -- View modes ----------------------------------------------------------------
VIEW_QUAD   = "Quad View"
VIEW_0      = "Frame 0 deg"
VIEW_N45    = "Frame -45 deg"
VIEW_45     = "Frame 45 deg"
VIEW_90     = "Frame 90 deg"
VIEW_MODES  = [VIEW_QUAD, VIEW_0, VIEW_N45, VIEW_45, VIEW_90]
 
# -- Colors (ThorImageCAM dark theme) -----------------------------------------
BG_DARK     = "#1e1e1e"
BG_PANEL    = "#2b2b2b"
BG_BUTTON   = "#3c3c3c"
FG_TEXT     = "#ffffff"
FG_LABEL    = "#ffdc32"   # amber
FG_ACTIVE   = "#00aaff"   # blue highlight
BTN_LIVE    = "#1a6b1a"   # green
BTN_SNAP    = "#1a4a8a"   # blue
BTN_CAP     = "#8a1a1a"   # red
BTN_STOP    = "#555555"   # grey when stopped
 
 
# ==============================================================================
class PolarizationViewer:
 
    def __init__(self, root):
        self.root = root
        self.root.title("Polarization Viewer")
        self.root.configure(bg=BG_DARK)
        self.root.geometry("1300x900")
        self.root.resizable(True, True)
 
        # state
        self.view_mode   = tk.StringVar(value=VIEW_QUAD)
        self.is_live     = False
        self.is_capturing = False
        self.capture_num = 0
        self.status_text = tk.StringVar(value="Ready")
        self.current_channels = None   # dict of latest frames
        self.live_thread = None
        self.stop_event  = threading.Event()
 
        # camera objects (opened on Live)
        self.sdk     = None
        self.pol_sdk = None
        self.cam     = None
 
        self._build_ui()
 
    # --------------------------------------------------------------------------
    def _build_ui(self):
        # ── left sidebar ──────────────────────────────────────────────────────
        sidebar = tk.Frame(self.root, bg=BG_PANEL, width=220)
        sidebar.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 2))
        sidebar.pack_propagate(False)
 
        # title
        tk.Label(sidebar, text="Polarization Viewer",
                 bg=BG_PANEL, fg=FG_LABEL,
                 font=("Segoe UI", 11, "bold"),
                 pady=12).pack(fill=tk.X)
 
        ttk.Separator(sidebar, orient="horizontal").pack(fill=tk.X, pady=4)
 
        # polar image type label
        tk.Label(sidebar, text="Polar Image Type",
                 bg=BG_PANEL, fg=FG_TEXT,
                 font=("Segoe UI", 9, "bold"),
                 anchor="w", padx=12).pack(fill=tk.X, pady=(8, 4))
 
        # radio buttons for view mode
        for mode in VIEW_MODES:
            rb = tk.Radiobutton(
                sidebar, text=mode,
                variable=self.view_mode, value=mode,
                bg=BG_PANEL, fg=FG_TEXT,
                selectcolor=BG_DARK,
                activebackground=BG_PANEL,
                activeforeground=FG_ACTIVE,
                font=("Segoe UI", 9),
                anchor="w", padx=24,
                command=self._on_view_change
            )
            rb.pack(fill=tk.X, ipady=3)
 
        ttk.Separator(sidebar, orient="horizontal").pack(fill=tk.X, pady=12)
 
        # ── three main buttons ─────────────────────────────────────────────
        btn_cfg = {"font": ("Segoe UI", 10, "bold"),
                   "relief": "flat", "bd": 0,
                   "pady": 10, "cursor": "hand2"}
 
        self.btn_live = tk.Button(
            sidebar, text="⏵  Live",
            bg=BTN_LIVE, fg=FG_TEXT,
            command=self._toggle_live,
            **btn_cfg)
        self.btn_live.pack(fill=tk.X, padx=12, pady=4)
 
        self.btn_snap = tk.Button(
            sidebar, text="📷  Snapshot",
            bg=BTN_SNAP, fg=FG_TEXT,
            command=self._snapshot,
            state=tk.DISABLED,
            **btn_cfg)
        self.btn_snap.pack(fill=tk.X, padx=12, pady=4)
 
        self.btn_cap = tk.Button(
            sidebar, text="⏺  Capture",
            bg=BTN_CAP, fg=FG_TEXT,
            command=self._toggle_capture,
            state=tk.DISABLED,
            **btn_cfg)
        self.btn_cap.pack(fill=tk.X, padx=12, pady=4)
 
        ttk.Separator(sidebar, orient="horizontal").pack(fill=tk.X, pady=12)
 
        # capture counter
        tk.Label(sidebar, text="Frames captured:",
                 bg=BG_PANEL, fg=FG_TEXT,
                 font=("Segoe UI", 8),
                 anchor="w", padx=12).pack(fill=tk.X)
 
        self.lbl_count = tk.Label(sidebar, text="0",
                                  bg=BG_PANEL, fg=FG_LABEL,
                                  font=("Segoe UI", 16, "bold"))
        self.lbl_count.pack(pady=4)
 
        # save dir info
        tk.Label(sidebar, text="Saves to:",
                 bg=BG_PANEL, fg="#888888",
                 font=("Segoe UI", 7),
                 anchor="w", padx=12).pack(fill=tk.X, pady=(8,0))
        tk.Label(sidebar, text="...\\captures\\",
                 bg=BG_PANEL, fg="#aaaaaa",
                 font=("Segoe UI", 7),
                 anchor="w", padx=12,
                 wraplength=190).pack(fill=tk.X)
 
        # ── right: main display area ──────────────────────────────────────
        right = tk.Frame(self.root, bg=BG_DARK)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
 
        # view title bar
        self.lbl_view_title = tk.Label(
            right, textvariable=self.view_mode,
            bg=BG_DARK, fg=FG_LABEL,
            font=("Segoe UI", 11, "bold"),
            anchor="w", padx=10, pady=6)
        self.lbl_view_title.pack(fill=tk.X)
 
        # canvas for image display
        self.canvas = tk.Canvas(right, bg="#111111",
                                highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self.canvas_image_id = None
 
        # status bar
        status_bar = tk.Frame(right, bg="#333333", height=28)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)
        status_bar.pack_propagate(False)
        tk.Label(status_bar, textvariable=self.status_text,
                 bg="#333333", fg="#cccccc",
                 font=("Segoe UI", 8),
                 anchor="w", padx=8).pack(fill=tk.Y, side=tk.LEFT)
 
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
 
    # --------------------------------------------------------------------------
    def _on_view_change(self):
        if self.current_channels:
            self._update_display(self.current_channels)
 
    # --------------------------------------------------------------------------
    def _toggle_live(self):
        if not self.is_live:
            self._start_live()
        else:
            self._stop_live()
 
    def _start_live(self):
        try:
            self.stop_event.clear()
            self.sdk     = TLCameraSDK()
            self.pol_sdk = PolarizationProcessorSDK()
            cameras = self.sdk.discover_available_cameras()
            if not cameras:
                raise RuntimeError("No cameras found. Is ThorImageCAM still open?")
            self.cam = self.sdk.open_camera(cameras[0])
            self.cam.frames_per_trigger_zero_for_unlimited = 0
            self.cam.image_poll_timeout_ms = 2000
            self.cam.arm(2)
            self.cam.issue_software_trigger()
 
            self.img_w      = self.cam.image_width_pixels
            self.img_h      = self.cam.image_height_pixels
            self.bit_depth  = self.cam.bit_depth
            self.polar_phase = self.cam.polar_phase
 
            self.is_live = True
            self.btn_live.config(text="⏹  Stop Live", bg="#555555")
            self.btn_snap.config(state=tk.NORMAL)
            self.btn_cap.config(state=tk.NORMAL)
            self.status_text.set("Live  |  " + str(self.img_w) + " x " + str(self.img_h) +
                                 "  |  " + str(self.bit_depth) + "-bit")
 
            self.live_thread = threading.Thread(target=self._live_loop, daemon=True)
            self.live_thread.start()
 
        except Exception as e:
            self.status_text.set("Error: " + str(e))
            self._cleanup_camera()
 
    def _stop_live(self):
        self.stop_event.set()
        self.is_live = False
        self.is_capturing = False
        self.btn_live.config(text="⏵  Live", bg=BTN_LIVE)
        self.btn_snap.config(state=tk.DISABLED)
        self.btn_cap.config(text="⏺  Capture", bg=BTN_CAP, state=tk.DISABLED)
        self.status_text.set("Stopped")
        self._cleanup_camera()
 
    def _cleanup_camera(self):
        try:
            if self.cam:
                self.cam.disarm()
                self.cam.dispose()
                self.cam = None
        except:
            pass
        try:
            if self.sdk:
                self.sdk.dispose()
                self.sdk = None
        except:
            pass
        try:
            if self.pol_sdk:
                self.pol_sdk.dispose()
                self.pol_sdk = None
        except:
            pass
 
    # --------------------------------------------------------------------------
    def _live_loop(self):
        while not self.stop_event.is_set():
            try:
                frame = self.cam.get_pending_frame_or_null()
                if frame is None:
                    self.cam.issue_software_trigger()
                    continue
 
                raw  = np.array(frame.image_buffer, dtype=np.uint16).reshape(self.img_h, self.img_w)
                raw8 = (raw >> (self.bit_depth - 8)).astype(np.uint8)
 
                channels = {
                    VIEW_0:   raw8[0::2, 0::2].copy(),
                    VIEW_45:  raw8[0::2, 1::2].copy(),
                    VIEW_90:  raw8[1::2, 0::2].copy(),
                    VIEW_N45: raw8[1::2, 1::2].copy(),
                }
 
                self.current_channels = channels
 
                if self.is_capturing:
                    self.capture_num += 1
                    self._save_channels(channels, "frame", frame_num=self.capture_num)
                    self.root.after(0, self.lbl_count.config,
                                    {"text": str(self.capture_num)})
 
                self.root.after(0, self._update_display, channels)
 
            except Exception as e:
                if not self.stop_event.is_set():
                    self.root.after(0, self.status_text.set, "Error: " + str(e))
                break
 
    # --------------------------------------------------------------------------
    def _update_display(self, channels):
        mode = self.view_mode.get()
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw < 10 or ch < 10:
            return
 
        if mode == VIEW_QUAD:
            img = self._make_quad(channels, cw, ch)
        else:
            src = channels.get(mode)
            if src is None:
                return
            img = self._make_single(src, mode, cw, ch)
 
        photo = ImageTk.PhotoImage(image=Image.fromarray(img))
        if self.canvas_image_id is None:
            self.canvas_image_id = self.canvas.create_image(
                cw // 2, ch // 2, anchor=tk.CENTER, image=photo)
        else:
            self.canvas.itemconfig(self.canvas_image_id, image=photo)
        self.canvas.image = photo   # prevent GC
 
    def _make_single(self, gray, label, cw, ch):
        label_h = 36
        img_h = ch - label_h
        img_w = cw
        resized = cv2.resize(gray, (img_w, img_h))
        canvas = np.zeros((ch, cw), dtype=np.uint8)
        # label bar
        cv2.rectangle(canvas, (0,0), (cw, label_h), (30,30,30), -1)
        ts, _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.9, 2)
        cv2.putText(canvas, label,
                    ((cw - ts[0])//2, (label_h + ts[1])//2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255,220,50), 2, cv2.LINE_AA)
        canvas[label_h:, :] = resized
        return canvas
 
    def _make_quad(self, channels, cw, ch):
        label_h = 28
        qw = cw // 2
        qh = (ch - 2 * label_h) // 2
        canvas = np.zeros((ch, cw), dtype=np.uint8)
 
        quads = [
            (VIEW_0,   0,   0),
            (VIEW_45,  0,   1),
            (VIEW_90,  1,   0),
            (VIEW_N45, 1,   1),
        ]
 
        for view_key, row, col in quads:
            src = channels.get(view_key)
            if src is None:
                continue
            x0 = col * qw
            y0 = row * (qh + label_h)
 
            # label bar
            cv2.rectangle(canvas, (x0, y0), (x0+qw, y0+label_h), (30,30,30), -1)
            ts, _ = cv2.getTextSize(view_key, cv2.FONT_HERSHEY_SIMPLEX, 0.65, 2)
            cv2.putText(canvas, view_key,
                        (x0 + (qw-ts[0])//2, y0 + (label_h+ts[1])//2),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255,220,50), 2, cv2.LINE_AA)
 
            # image
            y1 = y0 + label_h
            resized = cv2.resize(src, (qw, qh))
            canvas[y1:y1+qh, x0:x0+qw] = resized
 
        # dividers
        cv2.line(canvas, (cw//2, 0), (cw//2, ch), (70,70,70), 2)
        cv2.line(canvas, (0, ch//2), (cw, ch//2), (70,70,70), 2)
 
        return canvas
 
    # --------------------------------------------------------------------------
    def _snapshot(self):
        if not self.current_channels:
            self.status_text.set("No frame available yet")
            return
        saved = self._save_channels(self.current_channels, "snapshot")
        self.status_text.set("Snapshot saved  (" + str(len(saved)) + " files)  ->  captures\\")
        print("Snapshot saved: " + str(saved))
 
    def _toggle_capture(self):
        if not self.is_capturing:
            self.is_capturing = True
            self.capture_num  = 0
            self.lbl_count.config(text="0")
            self.btn_cap.config(text="⏹  Stop Capture", bg="#aa4400")
            self.status_text.set("Capturing...  Press 'Stop Capture' to stop")
            print("Capture started -> " + SAVE_DIR)
        else:
            self.is_capturing = False
            self.btn_cap.config(text="⏺  Capture", bg=BTN_CAP)
            self.status_text.set("Capture stopped  (" + str(self.capture_num) + " frames saved)")
            print("Capture stopped  (" + str(self.capture_num) + " frames saved)")
 
    # --------------------------------------------------------------------------
    def _save_channels(self, channels, prefix, frame_num=None):
        os.makedirs(SAVE_DIR, exist_ok=True)
 
        if frame_num is not None:
            base = prefix + "_" + str(frame_num).zfill(4)
        else:
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            base = prefix + "_" + ts
 
        key_map = {
            VIEW_0:   "0deg",
            VIEW_45:  "45deg",
            VIEW_90:  "90deg",
            VIEW_N45: "neg45deg",
        }
 
        saved = []
        for view_key, file_key in key_map.items():
            img = channels.get(view_key)
            if img is not None:
                filename = base + "_" + file_key + ".png"
                filepath = os.path.join(SAVE_DIR, filename)
                cv2.imwrite(filepath, img)
                saved.append(filename)
 
        return saved
 
    # --------------------------------------------------------------------------
    def _on_close(self):
        self._stop_live()
        self.root.destroy()
 
 
# ==============================================================================
def main():
    # check PIL is available
    try:
        from PIL import Image, ImageTk
    except ImportError:
        print("Pillow not found. Installing...")
        os.system("py -m pip install Pillow")
        from PIL import Image, ImageTk
 
    root = tk.Tk()
    app  = PolarizationViewer(root)
    root.mainloop()
 
 
if __name__ == "__main__":
    main()
