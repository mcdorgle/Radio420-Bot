#!/usr/bin/env python3
import os
import sys
import time
import threading
import logging
from datetime import datetime, timedelta

import pytz

import tkinter as tk
from tkinter import ttk, messagebox
from PIL import ImageTk, Image  # Added for image support in Tkinter

from config import ( 
    CONFIG_PATH, ENCODERS, HTTP_HOST, HTTP_PORT, save_config_from_gui, config
)
from utils import log, log_queue, TkLogHandler # noqa
from web_overlay import format_eta, shared_state as overlay_shared_state
from blaze_it import fire_420
from shoutcast_encoder import get_ffmpeg_dshow_devices
import services
from functools import partial
# ======================================================
# GLOBALS / LOGGING
# ======================================================

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        # For development, the base path is the directory of the script
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)

LOGO_FILE = resource_path("logo.png")
ICON_FILE = resource_path("logo.ico")

werk_handler = TkLogHandler()
werk_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))

werk_log = logging.getLogger("werkzeug")
werk_log.setLevel(logging.INFO)
werk_log.addHandler(werk_handler)

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.addHandler(werk_handler)

# ======================================================
# SERVICE CONTROL (with thread joining for clean shutdown)
# ======================================================


def start_twitch() -> None:
    services.start_twitch()

def stop_twitch() -> None:
    services.stop_twitch()

def restart_twitch() -> None:
    stop_twitch()
    time.sleep(1)
    start_twitch()

def start_overlay() -> None:
    services.start_overlay()

def stop_overlay() -> None:
    services.stop_overlay()

def restart_overlay() -> None:
    stop_overlay()
    time.sleep(1)
    start_overlay()

def start_encoder(index: int, audio_combo: ttk.Combobox) -> None:
    services.start_encoder(index, audio_combo)

def stop_encoder(index: int) -> None:
    services.stop_encoder(index)

def restart_encoder(index: int, audio_combo: ttk.Combobox) -> None:
    stop_encoder(index)
    time.sleep(1)
    start_encoder(index, audio_combo)

def start_420() -> None:
    services.start_420()

def stop_420() -> None:
    services.stop_420()

def restart_420() -> None:
    stop_420()
    time.sleep(1)
    start_420()

def test_420() -> None:
    msg = fire_420(services.bot_instance, test=True)
    overlay_shared_state["last_420_message"] = msg
    overlay_shared_state["popup_message"] = msg
    overlay_shared_state["popup_expire_utc"] = datetime.now(pytz.utc) + timedelta(seconds=12)

def handle_save_config(entries: dict) -> None:
    save_config_from_gui(entries)
    messagebox.showinfo("Config Saved", "Config updated.\nRestart services for full effect.")
    log("Config saved to " + CONFIG_PATH)

# ======================================================
# GUI (enhanced with logo, better styling, gradients, and layout)
# ======================================================


def build_gui() -> tk.Tk:
    # This will be created and passed directly where needed, removing the global variable dependency.
    root = tk.Tk()
    root.title("RadioBot v4.0")
    root.geometry("600x750")  # Set a default size for better appearance

    # Set the window icon, ensuring it's bundled
    if os.path.exists(ICON_FILE):
        root.iconbitmap(ICON_FILE)
    else:
        log(f"Icon file not found at {ICON_FILE}. Skipping icon.")

    bg = "#05040a"  # Dark background
    txt = "#b7ffb7"  # Light green text
    accent = "#22c55e"  # Green accent
    danger = "#f97373"  # Red for offline
    secondary = "#1e1b2e"  # Darker secondary background for frames
    root.configure(bg=bg)

    style = ttk.Style()
    style.theme_use("clam")
    style.configure(".", background=bg, foreground=txt)
    style.configure("TFrame", background=bg)
    style.configure("TLabel", background=bg, foreground=txt)
    style.configure("TLabelframe", background=secondary, foreground=txt, borderwidth=0)
    style.configure("TLabelframe.Label", background=secondary, foreground=accent)
    style.configure("TButton", padding=6, background=secondary, foreground=txt)
    style.map("TButton", background=[("active", accent)], foreground=[("active", bg)])
    style.configure("TNotebook", background=bg, foreground=txt)
    style.configure("TNotebook.Tab", background=secondary, foreground=txt)
    style.map("TNotebook.Tab", background=[("selected", accent)], foreground=[("selected", bg)])
    style.configure("TCheckbutton", background=secondary, foreground=txt)
    style.configure("TEntry", foreground="black", fieldbackground="white")
    style.configure("TCombobox", foreground="black", fieldbackground="white")

    # Header frame for logo and title
    header_frame = ttk.Frame(root, padding=10)
    header_frame.pack(fill="x", pady=10)

    # Load and display logo (resize to fit)
    if os.path.exists(LOGO_FILE):
        logo_img = Image.open(LOGO_FILE)
        logo_img = logo_img.resize((100, 100), Image.LANCZOS)  # Resize logo smaller
        logo_photo = ImageTk.PhotoImage(logo_img)
        logo_label = tk.Label(header_frame, image=logo_photo, bg=bg)
        logo_label.image = logo_photo  # Keep reference
        logo_label.pack(side="left", padx=10)
    else:
        log(f"Logo file not found at {LOGO_FILE}. Skipping logo display.")

    # App title
    title_label = ttk.Label(header_frame, text="RadioBot", font=("Segoe UI", 18, "bold"), foreground=accent)
    title_label.pack(side="left", expand=True)

    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True, padx=10, pady=10)

    dash = ttk.Frame(notebook)
    logs_frame = ttk.Frame(notebook)
    config_tab = ttk.Frame(notebook)

    notebook.add(dash, text="Dashboard")
    notebook.add(logs_frame, text="Logs")
    notebook.add(config_tab, text="Config")

    # ===== DASHBOARD TAB (with scrolling) =====
    dash_scroll_canvas = tk.Canvas(dash, bg=bg, highlightthickness=0)
    dash_scroll_canvas.pack(side="left", fill="both", expand=True)

    dash_scrollbar = ttk.Scrollbar(dash, orient="vertical", command=dash_scroll_canvas.yview)
    dash_scrollbar.pack(side="right", fill="y")

    dash_inner_frame = ttk.Frame(dash_scroll_canvas)
    dash_scroll_canvas.create_window((0, 0), window=dash_inner_frame, anchor="nw")

    dash_inner_frame.bind("<Configure>", lambda e: dash_scroll_canvas.configure(scrollregion=dash_scroll_canvas.bbox("all")))
    dash_scroll_canvas.configure(yscrollcommand=dash_scrollbar.set)

    # ===== BLAZE INFO & OVERLAY URL =====
    info_frame = ttk.LabelFrame(dash_inner_frame, text="Blaze Info", padding=10)
    info_frame.pack(fill="x", padx=10, pady=5)

    lbl_next_420 = ttk.Label(info_frame, text="Next 4:20: calculating...", font=("Segoe UI", 10))
    lbl_next_420.pack(anchor="w", pady=4, padx=8)

    lbl_last_420 = ttk.Label(info_frame, text="Last Event: none", font=("Segoe UI", 10))
    lbl_last_420.pack(anchor="w", pady=4, padx=8)

    lbl_overlay_url = ttk.Label(
        info_frame,
        text=f"Overlay URL: http://{HTTP_HOST}:{HTTP_PORT}/",
        font=("Segoe UI", 10)
    )
    lbl_overlay_url.pack(anchor="w", pady=4, padx=8)

    # ===== SERVICE CONTROLS (Integrated Status & Controls) =====
    services_frame = ttk.LabelFrame(dash_inner_frame, text="Service Controls", padding=10)
    services_frame.pack(fill="x", padx=10, pady=10)

    def create_service_row(parent, name, row, start_cmd, stop_cmd, restart_cmd):
        """Creates a row with a label, status, and control buttons."""
        # Service Name
        ttk.Label(parent, text=name, font=("Segoe UI", 10, "bold")).grid(row=row, column=0, sticky="w", padx=5, pady=8)

        # Status Label
        status_lbl = tk.Label(parent, text="○ OFFLINE", bg=secondary, fg=danger, font=("Segoe UI", 9, "bold"), relief="flat", padx=6, pady=3)
        status_lbl.grid(row=row, column=1, sticky="w", padx=5, pady=8)

        # Control Buttons
        btn_frame = ttk.Frame(parent)
        btn_frame.grid(row=row, column=2, sticky="e", padx=5, pady=8)
        ttk.Button(btn_frame, text="Start", command=start_cmd).pack(side="left", padx=3)
        ttk.Button(btn_frame, text="Stop", command=stop_cmd).pack(side="left", padx=3)
        ttk.Button(btn_frame, text="Restart", command=restart_cmd).pack(side="left", padx=3)

        parent.grid_columnconfigure(0, weight=1) # Allow name to expand
        parent.grid_columnconfigure(1, weight=1) # Allow status to expand
        parent.grid_columnconfigure(2, weight=2) # Give buttons more space
        return status_lbl

    # Create rows for each service
    lbl_twitch_status = create_service_row(services_frame, "Twitch Bot", 0, start_twitch, stop_twitch, restart_twitch)
    lbl_overlay_status = create_service_row(services_frame, "Web Overlay", 1, start_overlay, stop_overlay, restart_overlay)
    lbl_420_status = create_service_row(services_frame, "420 Timer", 2, start_420, stop_420, restart_420)

    # Separator
    ttk.Separator(services_frame, orient='horizontal').grid(row=3, columnspan=3, sticky='ew', pady=10, padx=5)

    # ===== GLOBAL ACTIONS =====
    actions_frame = ttk.LabelFrame(dash_inner_frame, text="Global Actions", padding=10)
    actions_frame.pack(fill="x", padx=10, pady=10)

    ttk.Button(actions_frame, text="TEST 4:20 Blaze", command=test_420).pack(side="left", padx=5, pady=5)

    def quit_all() -> None:
        """Stops all services in a separate thread to avoid freezing the GUI, then quits."""
        def shutdown_thread():
            log("Shutting down all services...")
            # Disable the quit button to prevent multiple clicks
            quit_button.config(state="disabled")
            stop_420()
            stop_overlay()
            stop_twitch()
            for i in range(len(ENCODERS)):
                stop_encoder(i)
            log("All services stopped. Exiting.")
            root.after(100, root.destroy) # Safely destroy the root window from the main thread
        threading.Thread(target=shutdown_thread, daemon=True).start()
    
    quit_button = ttk.Button(actions_frame, text="Quit & Stop All", command=quit_all)
    quit_button.pack(side="right", padx=5, pady=5)

    # ===== LOGS TAB (with scrollbar) =====
    log_container = ttk.Frame(logs_frame)
    log_container.pack(fill="both", expand=True, padx=10, pady=10)

    scrollbar = ttk.Scrollbar(log_container)
    scrollbar.pack(side="right", fill="y")

    txt_log = tk.Text(
        log_container,
        height=20,
        bg="#0b0715",
        fg=txt,
        insertbackground=txt,
        borderwidth=0,
        yscrollcommand=scrollbar.set,
        font=("Segoe UI", 9)
    )
    txt_log.pack(side="left", fill="both", expand=True)
    scrollbar.config(command=txt_log.yview)

    # ===== CONFIG TAB =====
    config_scroll = tk.Canvas(config_tab, bg=bg)
    config_scroll.pack(side="left", fill="both", expand=True)

    config_scrollbar = ttk.Scrollbar(config_tab, orient="vertical", command=config_scroll.yview)
    config_scrollbar.pack(side="right", fill="y")

    config_inner = ttk.Frame(config_scroll)
    config_scroll.create_window((0, 0), window=config_inner, anchor="nw")

    config_inner.bind("<Configure>", lambda e: config_scroll.configure(scrollregion=config_scroll.bbox("all")))
    config_scroll.configure(yscrollcommand=config_scrollbar.set)

    config_entries = {}

    # Define the keys we expect to see for each section to ensure they are all rendered
    # This avoids both including parser defaults and missing fallback-only values.
    expected_keys = {
        "twitch": ["station_name", "nick", "channel", "oauth"],
        "database": ["host", "user", "password", "db"],
        "server": ["host", "port"],
        "points": ["currency_name", "passive_earn_amount", "passive_earn_interval_minutes", "active_earn_amount", "active_earn_cooldown_seconds", "request_cost", "playnext_cost", "give_points_tax_percent"],
        "overlay": ["max_results"],
        "style": ["background", "text_color", "title_color", "font_size", "refresh_rate"]
    }

    # --- Create Config Sections Dynamically ---
    for section, keys in expected_keys.items():
        sec_frame = ttk.LabelFrame(config_inner, text=section, padding=10)
        sec_frame.pack(fill="x", padx=10, pady=5)

        config_entries[section] = {}
        
        for key in keys:
            rowf = ttk.Frame(sec_frame)
            rowf.pack(fill="x", pady=4)

            ttk.Label(rowf, text=key, width=20, font=("Segoe UI", 10)).pack(side="left", padx=5)
            e = ttk.Entry(rowf, font=("Segoe UI", 10))
            e.insert(0, config.get(section, key, fallback=""))
            e.pack(side="left", fill="x", expand=True, padx=5)
            config_entries[section][key] = e

    # --- Audio Config ---
    audio_frame = ttk.LabelFrame(config_inner, text="Audio Input", padding=10)
    audio_frame.pack(fill="x", padx=10, pady=5, anchor="n")
    
    rowf_audio = ttk.Frame(audio_frame)
    rowf_audio.pack(fill="x", pady=4)
    ttk.Label(rowf_audio, text="Input Device", width=20, font=("Segoe UI", 10)).pack(side="left", padx=5)
    
    audio_device_combo = ttk.Combobox(rowf_audio, font=("Segoe UI", 10), state="readonly")
    audio_device_combo.pack(side="left", fill="x", expand=True, padx=5)
    
    def refresh_audio_devices() -> list:
        log("Refreshing audio device list...")
        new_devices = get_ffmpeg_dshow_devices()
        device_names = [f"{d['index']}: {d['name']}" for d in new_devices]
        if not device_names:
            log("Warning: No audio input devices found by FFmpeg.")
        audio_device_combo['values'] = device_names
        return device_names
    
    # Initial population
    device_names = refresh_audio_devices()
    initial_device = config.get("audio", "input_device", fallback="")
    
    if initial_device and any(initial_device in name for name in device_names):
        audio_device_combo.set(next(name for name in device_names if initial_device in name))
    elif device_names:
        audio_device_combo.set(device_names[0])

    config_entries["audio"] = {"input_device": audio_device_combo}
    
    refresh_button = ttk.Button(rowf_audio, text="🔄", command=refresh_audio_devices, width=3)
    refresh_button.pack(side="left", padx=5)
    
    # --- Encoder Configs ---
    for i, enc_cfg in enumerate(ENCODERS):
        section_name = f"Encoder {i+1} ({enc_cfg['name']})"
        sec_frame = ttk.LabelFrame(config_inner, text=section_name, padding=10)
        sec_frame.pack(fill="x", padx=10, pady=5)

        config_entries[f"encoder{i+1}"] = {}

        # Enabled checkbox
        rowf_enabled = ttk.Frame(sec_frame)
        rowf_enabled.pack(fill="x", pady=4)
        ttk.Label(rowf_enabled, text="enabled", width=20, font=("Segoe UI", 10)).pack(side="left", padx=5)
        enabled_var = tk.BooleanVar(value=enc_cfg["enabled"])
        chk_enabled = ttk.Checkbutton(rowf_enabled, variable=enabled_var)
        chk_enabled.pack(side="left", padx=5)
        config_entries[f"encoder{i+1}"]["enabled"] = enabled_var # Store the variable

        # Add other text entries
        for key in ["name", "host", "port", "password", "mount"]:
            rowf = ttk.Frame(sec_frame)
            rowf.pack(fill="x", pady=4)

            ttk.Label(rowf, text=key, width=20, font=("Segoe UI", 10)).pack(side="left", padx=5)
            e = ttk.Entry(rowf, font=("Segoe UI", 10))
            e.insert(0, str(enc_cfg[key]))
            e.pack(side="left", fill="x", expand=True, padx=5)
            config_entries[f"encoder{i+1}"][key] = e

        # Bitrate Combobox
        rowf_bitrate = ttk.Frame(sec_frame)
        rowf_bitrate.pack(fill="x", pady=4)
        ttk.Label(rowf_bitrate, text="bitrate", width=20, font=("Segoe UI", 10)).pack(side="left", padx=5)
        bitrate_options = ["64k", "96k", "128k", "192k", "256k", "320k"]
        e = ttk.Combobox(rowf_bitrate, values=bitrate_options, font=("Segoe UI", 10), state="readonly")
        e.set(enc_cfg.get("bitrate", "128k"))
        e.pack(side="left", fill="x", expand=True, padx=5)
        config_entries[f"encoder{i+1}"]["bitrate"] = e

    ttk.Button(
        config_inner,
        text="💾 Save Config",
        command=lambda: handle_save_config(config_entries),
    ).pack(pady=10)

    # --- Create Encoder Service Rows (after audio_device_combo is created) ---
    lbl_encoder_statuses = []
    for i, enc_cfg in enumerate(ENCODERS):
        start_cmd = partial(start_encoder, i, audio_device_combo)
        stop_cmd = partial(stop_encoder, i)
        restart_cmd = partial(restart_encoder, i, audio_device_combo)
        
        status_lbl = create_service_row(services_frame, enc_cfg["name"], 4 + i, start_cmd, stop_cmd, restart_cmd)
        lbl_encoder_statuses.append(status_lbl)


    # ===== UI UPDATE LOOP =====
    def update_ui() -> None:
        # logs
        while not log_queue.empty():
            line = log_queue.get()
            txt_log.insert("end", line + "\n")
            txt_log.see("end")

        # status lights
        if services.twitch_running:
            lbl_twitch_status.config(text="● ONLINE", fg=accent, bg=secondary)
        else:
            lbl_twitch_status.config(text="○ OFFLINE", fg=danger, bg=secondary)

        if services.overlay_running:
            lbl_overlay_status.config(text="● RUNNING", fg=accent, bg=secondary)
        else:
            lbl_overlay_status.config(text="○ STOPPED", fg=danger)

        if services.tracker_running and services.announcer_running:
            lbl_420_status.config(text="● ACTIVE", fg=accent)
        else:
            lbl_420_status.config(text="○ IDLE", fg=danger)

        # Encoder statuses
        for i in range(len(ENCODERS)):
            if services.encoder_running[i] and services.encoder_instances[i]:
                lbl_encoder_statuses[i].config(text=f"● {services.encoder_instances[i].status}", fg=services.encoder_instances[i].color, bg=secondary)
            else:
                lbl_encoder_statuses[i].config(text="○ STOPPED", fg="gray", bg=secondary)


        # blaze info
        next_utc = overlay_shared_state.get("next_420_utc")
        next_city = overlay_shared_state.get("next_420_city")
        if next_utc and next_city:
            eta = format_eta(next_utc - datetime.now(pytz.utc))
            lbl_next_420.config(text=f"Next 4:20: {next_city} — {eta}")
        else:
            lbl_next_420.config(text="Next 4:20: calculating...")

        last_420_message = overlay_shared_state.get("last_420_message")
        lbl_last_420.config(
            text=f"Last Event: {last_420_message}" if last_420_message else "Last Event: none",
        )

        root.after(300, update_ui)

    update_ui()
    return root

if __name__ == "__main__":
    gui = build_gui()
    gui.mainloop()
    log("Radio420 GUI closed.")
