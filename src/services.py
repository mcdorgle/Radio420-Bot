import threading
import time
import socket
import re
import requests
from datetime import datetime, timedelta
import pytz

from werkzeug.serving import make_server
from tkinter import messagebox, ttk

from twitch_bot import TwitchBot
from web_overlay import app, shared_state
from blaze_it import compute_next_420, fire_420
from shoutcast_encoder import ShoutcastEncoder
from utils import log
from config import HTTP_HOST, HTTP_PORT, ENCODERS, TWITCH_CHANNEL, POINTS_PASSIVE_INTERVAL, POINTS_PASSIVE_AMOUNT, POINTS_ACTIVE_AMOUNT, POINTS_ACTIVE_COOLDOWN

# ======================================================
# GLOBALS / STATE
# ======================================================

bot_instance: 'TwitchBot' = None
twitch_thread: threading.Thread = None
twitch_running: bool = False

overlay_server: 'make_server' = None
overlay_thread: threading.Thread = None
overlay_running: bool = False

points_thread: threading.Thread = None
points_running: bool = False

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
    start_points_manager()
    log("Twitch: started")

def stop_twitch() -> None:
    global bot_instance, twitch_thread, twitch_running
    if not twitch_running:
        return
    if bot_instance:
        bot_instance.stop()
        # The stop() method sets bot_instance.running to False, which will cause the loop to exit.
    twitch_running = False
    if twitch_thread:
        twitch_thread.join(timeout=5)
    stop_points_manager()
    log("Twitch: stopped")

def run_twitch_loop():
    global bot_instance
    bot_instance.connect()

    # Command mapping
    commands = {
        "!search": bot_instance.search,
        "!points": bot_instance.points,
        "!uptime": bot_instance.uptime,
        "!lastplayed": bot_instance.lastplayed,
        "!queue": bot_instance.queue,
        "!gamble": bot_instance.gamble,
        "!addpoints": bot_instance.addpoints,
        "!leaderboard": bot_instance.leaderboard,
        "!give": bot_instance.give_points,
    }

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
                    user, msg, tags = bot_instance.parse(line)
                    if not user and "Login authentication failed" in msg:
                        log("Twitch Error: Login authentication failed. Please check your oauth token in config.ini.")
                        messagebox.showerror("Twitch Auth Error", "Login failed. Please check your 'oauth' token in the config and restart the bot.")
                        # Stop the service to prevent a reconnect loop
                        stop_twitch()
                        return
                    if not msg: continue

                    # Active point earning
                    now = time.time()
                    if now - bot_instance.last_active_times.get(user, 0) > POINTS_ACTIVE_COOLDOWN:
                        bot_instance.update_user_points(user, POINTS_ACTIVE_AMOUNT, is_active=True)
                        bot_instance.last_active_times[user] = now

                    # Command parsing
                    command_part = msg.split(" ")[0].lower()
                    if command_part in commands:
                        # Pass tags to the command handler for permission checks
                        if command_part == "!addpoints":
                            commands[command_part](user, msg, tags)
                        else:
                            commands[command_part](user, msg)
                        continue

                    m = re.match(r"!pick (\d+)", msg, re.IGNORECASE)
                    if m:
                        bot_instance.pick(user, int(m.group(1)))
                        continue

                    m_playnext = re.match(r"!playnext (\d+)", msg, re.IGNORECASE)
                    if m_playnext:
                        bot_instance.playnext(user, int(m_playnext.group(1)))
                        continue
        except socket.timeout:
            log("Twitch: Socket timeout, reconnecting...")
            bot_instance.connect()
        except (socket.error, BrokenPipeError) as e:
            if not bot_instance.running: break
            log(f"Twitch Socket Error: {e}. Reconnecting...")
            time.sleep(3)
            bot_instance.connect()
        except Exception as e:
            if not bot_instance.running: break
            log(f"An unexpected error occurred in Twitch loop: {e}")
            time.sleep(5) # Wait a bit longer on unexpected errors
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
# POINTS MANAGER SERVICE
# ======================================================

def start_points_manager():
    global points_thread, points_running
    if points_running: return
    points_running = True
    points_thread = threading.Thread(target=run_points_manager_loop, daemon=True)
    points_thread.start()
    log("Points Manager: started")

def stop_points_manager():
    global points_running, points_thread
    if not points_running: return
    points_running = False
    if points_thread:
        points_thread.join(timeout=5)
    log("Points Manager: stopped")

def run_points_manager_loop():
    """Periodically awards points to active chatters."""
    global bot_instance
    while points_running:
        time.sleep(POINTS_PASSIVE_INTERVAL * 60)
        if not bot_instance or not bot_instance.running:
            continue
        
        try:
            # This is an undocumented Twitch endpoint, but it's widely used.
            url = f"https://tmi.twitch.tv/group/user/{TWITCH_CHANNEL}/chatters"
            response = requests.get(url, timeout=5)
            response.raise_for_status()
            chatters_data = response.json()
            all_chatters = set(sum(chatters_data['chatters'].values(), []))
            log(f"Points Manager: Found {len(all_chatters)} chatters. Awarding {POINTS_PASSIVE_AMOUNT} points.")
            for user in all_chatters:
                bot_instance.update_user_points(user, POINTS_PASSIVE_AMOUNT)
        except Exception as e:
            log(f"Points Manager Error: Could not fetch chatters. {e}")

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