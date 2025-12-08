import threading
import time
import socket
from datetime import datetime, timedelta
import pytz

from werkzeug.serving import make_server
from tkinter import messagebox, ttk

from twitch_bot import TwitchBot
from web_overlay import app, shared_state
from blaze_it import compute_next_420, fire_420
from shoutcast_encoder import ShoutcastEncoder
from utils import log
from config import HTTP_HOST, HTTP_PORT, ENCODERS

# ======================================================
# GLOBALS / STATE
# ======================================================

bot_instance: 'TwitchBot' = None
twitch_thread: threading.Thread = None
twitch_running: bool = False

overlay_server: 'make_server' = None
overlay_thread: threading.Thread = None
overlay_running: bool = False

tracker_thread: threading.Thread = None
announcer_thread: threading.Thread = None
tracker_running: bool = False
announcer_running: bool = False

last_fired_target: datetime = None

encoder_instances: list['ShoutcastEncoder'] = [None] * 3
encoder_running: list[bool] = [False] * 3

# ======================================================
# TWITCH SERVICE
# ======================================================

def start_twitch() -> None:
    global bot_instance, twitch_thread, twitch_running
    if twitch_running:
        return
    bot_instance = TwitchBot()
    twitch_thread = threading.Thread(target=run_twitch_loop, daemon=True)
    twitch_running = True
    twitch_thread.start()
    log("Twitch: started")

def stop_twitch() -> None:
    global bot_instance, twitch_thread, twitch_running
    if not twitch_running:
        return
    if bot_instance:
        bot_instance.stop()
    twitch_running = False
    if twitch_thread:
        twitch_thread.join(timeout=5)
    log("Twitch: stopped")

def run_twitch_loop():
    global bot_instance
    bot_instance.connect()
    while bot_instance.running:
        try:
            data = bot_instance.sock.recv(2048).decode("utf8", "ignore")
            if not data:
                if not bot_instance.running: break
                time.sleep(1)
                continue
            for line in data.split("\r\n"):
                if not line: continue
                if "PING" in line:
                    try:
                        bot_instance.sock.sendall(b"PONG\r\n")
                    except Exception: pass
                if "PRIVMSG" in line:
                    user, msg = bot_instance.parse(line)
                    if not user and "Login authentication failed" in msg:
                        log("Twitch Error: Login authentication failed. Please check your oauth token in config.ini.")
                        messagebox.showerror("Twitch Auth Error", "Login failed. Please check your 'oauth' token in the config and restart the bot.")
                        # Stop the service to prevent a reconnect loop
                        stop_twitch()
                        return
                    if not msg: continue
                    if msg.startswith("!search"):
                        bot_instance.search(user, msg[8:].strip())
                        continue
                    m = bot_instance.re.match(r"!(\d+)$", msg)
                    if m:
                        bot_instance.pick(user, int(m.group(1)))
                        continue
        except socket.timeout:
            log("Twitch: Socket timeout, reconnecting...")
            bot_instance.connect()
        except Exception as e:
            if not bot_instance.running: break
            log(f"Twitch Loop Error: {e}")
            time.sleep(3)
            bot_instance.connect()
    log("Twitch: Bot loop exiting")
    if bot_instance.sock:
        bot_instance.sock.close()

# ======================================================
# OVERLAY SERVICE
# ======================================================

def start_overlay() -> None:
    global overlay_server, overlay_thread, overlay_running
    if overlay_running: return
    try:
        overlay_server = make_server(HTTP_HOST, HTTP_PORT, app, threaded=True)
        overlay_thread = threading.Thread(target=overlay_server.serve_forever, daemon=True)
        overlay_thread.start()
        overlay_running = True
        log(f"Overlay: http://{HTTP_HOST}:{HTTP_PORT}/")
    except Exception as e:
        log(f"Overlay start error: {e}")

def stop_overlay() -> None:
    global overlay_server, overlay_thread, overlay_running
    if not overlay_running: return
    try:
        if overlay_server:
            overlay_server.shutdown()
            overlay_thread.join(timeout=5)
    except Exception as e:
        log(f"Overlay stop error: {e}")
    overlay_server = None
    overlay_thread = None
    overlay_running = False
    log("Overlay: stopped")

# ======================================================
# 420 SERVICE
# ======================================================

def start_420() -> None:
    global tracker_thread, announcer_thread, tracker_running, announcer_running
    if tracker_running or announcer_running: return
    tracker_running = True
    announcer_running = True
    tracker_thread = threading.Thread(target=next_420_tracker_loop, daemon=True)
    announcer_thread = threading.Thread(target=run_420_announcer_loop, daemon=True)
    tracker_thread.start()
    announcer_thread.start()
    log("420 Timer: started")

def stop_420() -> None:
    global tracker_running, announcer_running, tracker_thread, announcer_thread
    tracker_running = False
    announcer_running = False
    if tracker_thread: tracker_thread.join(timeout=5)
    if announcer_thread: announcer_thread.join(timeout=5)
    log("420 Timer: stopped")

def next_420_tracker_loop() -> None:
    while tracker_running:
        shared_state["next_420_utc"], shared_state["next_420_city"] = compute_next_420()
        time.sleep(60)
    log("420 Tracker: stopped")

def run_420_announcer_loop() -> None:
    global last_fired_target
    while announcer_running:
        if shared_state["next_420_utc"]:
            delta = (shared_state["next_420_utc"] - datetime.now(pytz.utc)).total_seconds()
            if 0 <= delta <= 30 and last_fired_target != shared_state["next_420_utc"]:
                msg = fire_420(bot_instance, test=False)
                shared_state["last_420_message"] = msg
                shared_state["popup_message"] = msg
                shared_state["popup_expire_utc"] = datetime.now(pytz.utc) + timedelta(seconds=12)
                last_fired_target = shared_state["next_420_utc"]
        time.sleep(5)
    log("420 Announcer: stopped")

# ======================================================
# ENCODER SERVICES
# ======================================================

def start_encoder(index: int, audio_combo: ttk.Combobox) -> None:
    global encoder_instances, encoder_running
    if index >= len(ENCODERS) or not ENCODERS[index].get("enabled"):
        log(f"Encoder {index+1} is disabled or not configured.")
        return
    if encoder_running[index]: return

    selection = audio_combo.get()
    if not selection:
        log("Error: No audio device selected in the Config tab.")
        messagebox.showerror("Audio Device Error", "Please select an audio input device from the dropdown in the 'Config' tab before starting an encoder.")
        return
    
    # The selection format is "index: device name". We need the name.
    try:
        audio_device_name = selection.split(":", 1)[1].strip()
    except IndexError:
        log(f"Error: Invalid audio device format selected: '{selection}'")
        messagebox.showerror("Audio Device Error", f"The selected audio device is invalid.\n'{selection}'")
        return

    encoder_instances[index] = ShoutcastEncoder(index, ENCODERS[index], audio_device_name)
    encoder_running[index] = True
    encoder_instances[index].start()
    log(f"Encoder {index+1}: started")

def stop_encoder(index: int) -> None:
    global encoder_instances, encoder_running
    if not encoder_running[index]: return
    if encoder_instances[index]:
        encoder_instances[index].stop()
    encoder_running[index] = False
    log(f"Encoder {index+1}: stopped")