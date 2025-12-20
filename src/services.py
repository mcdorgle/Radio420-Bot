import threading
import time
import socket
import re
import requests
import random
from datetime import datetime, timedelta
import pytz

from werkzeug.serving import make_server
from tkinter import messagebox, ttk

from twitch_bot import TwitchBot
from db import ensure_tables_exist
from web_overlay import app, socketio, shared_state
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
song_tracker_thread: threading.Thread = None
song_tracker_running: bool = False
last_announced_song: tuple = None
mod_tracker_thread: threading.Thread = None
mod_tracker_running: bool = False
shouted_mods: set = set()

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
    # Ensure minimal DB tables exist before the bot starts
    try:
        ensure_tables_exist()
    except Exception as e:
        log(f"Warning: could not ensure DB tables exist: {e}")

    bot_instance = TwitchBot()
    twitch_thread = threading.Thread(target=run_twitch_loop, daemon=True)
    twitch_running = True
    twitch_thread.start()
    start_points_manager()
    start_song_tracker()
    # Mod tracker (TMI polling) is disabled by default because some channels
    # return 404 from the TMI endpoint. In-chat tag detection handles shoutouts.
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
    stop_song_tracker()
    # stop_mod_tracker() intentionally not called (mod tracker not started)
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
        "!playing": bot_instance.playing,
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

                    # In-chat mod detection: announce mods when we see a PRIVMSG with mod tag (fallback to TMI)
                    try:
                        if tags and tags.get('mod') == '1' and user and user not in shouted_mods:
                            bot_instance.send(f"Shoutout to moderator @{user} — thanks for keeping chat tidy!")
                            shouted_mods.add(user)
                    except Exception:
                        # Non-fatal, continue processing
                        pass

                    # Active point earning
                    now = time.time()
                    if now - bot_instance.last_active_times.get(user, 0) > POINTS_ACTIVE_COOLDOWN:
                        bot_instance.update_user_points(user, POINTS_ACTIVE_AMOUNT, is_active=True)
                        bot_instance.last_active_times[user] = now

                    # Command parsing
                    command_part = msg.split(" ")[0].lower()
                    handler = commands.get(command_part)
                    if handler:
                        # Pass tags to any command that might need it for permission checks
                        if command_part in ["!addpoints"]:
                            handler(user, msg, tags)
                        else:
                            handler(user, msg)
                        continue

                    m = re.match(r"!pick (\d+)", msg, re.IGNORECASE)
                    if m:
                        bot_instance.pick(user, int(m.group(1)))
                        continue

                    m_playnext = re.match(r"!playnext (\d+)", msg, re.IGNORECASE)
                    if m_playnext:
                        bot_instance.playnext(user, int(m_playnext.group(1)))
                        continue

                    # (bleep game removed)

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
            log(f"An unexpected error occurred in Twitch loop: {type(e).__name__}: {e}")
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
        # Use socketio.run() for proper WebSocket support, not make_server.
        # It needs to run in a separate thread so it doesn't block the main GUI.
        def run_server():
            log(f"Starting overlay server on http://{HTTP_HOST}:{HTTP_PORT}")
            try:
                # log_output=False prevents SocketIO from capturing and silencing our logs.
                # allow_unsafe_werkzeug is needed for the dev server in a thread.
                socketio.run(app, host=HTTP_HOST, port=HTTP_PORT, allow_unsafe_werkzeug=True, log_output=False)
            except Exception as e:
                log(f"!!! Overlay server crashed: {e}")

        overlay_thread = threading.Thread(target=run_server, daemon=True)
        overlay_thread.start()
        overlay_running = True
        log(f"Overlay: http://{HTTP_HOST}:{HTTP_PORT}/")
    except Exception as e:
        log(f"Overlay start error: {e}")

def stop_overlay() -> None:
    global overlay_server, overlay_thread, overlay_running
    if not overlay_running: return
    try:
        # SocketIO server doesn't have a clean public shutdown method when run this way.
        # Since it's a daemon thread, it will exit when the main app closes.
        log("Overlay: Service stopping (server will shut down with app).")
    except Exception as e:
        log(f"Overlay stop error: {e}")
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


def start_song_tracker():
    global song_tracker_thread, song_tracker_running, last_announced_song
    if song_tracker_running:
        return
    song_tracker_running = True
    song_tracker_thread = threading.Thread(target=run_song_tracker_loop, daemon=True)
    song_tracker_thread.start()
    log("NowPlaying Tracker: started")


def stop_song_tracker():
    global song_tracker_running, song_tracker_thread
    if not song_tracker_running:
        return
    song_tracker_running = False
    if song_tracker_thread:
        song_tracker_thread.join(timeout=5)
    log("NowPlaying Tracker: stopped")


def run_song_tracker_loop():
    """Poll the RadioDJ `history` table and announce when the top entry changes."""
    global bot_instance, last_announced_song, song_tracker_running
    from db import get_db_connection
    poll_interval = 5
    while song_tracker_running:
        try:
            conn = get_db_connection()
            try:
                with conn.cursor() as c:
                    c.execute("SELECT artist, title FROM history ORDER BY date_played DESC LIMIT 1")
                    row = c.fetchone()
            finally:
                conn.close()

            if row:
                cur = ( (row.get('artist') or '').strip(), (row.get('title') or '').strip() )
                if last_announced_song is None:
                    last_announced_song = cur
                elif cur != last_announced_song:
                    last_announced_song = cur
                    if bot_instance and bot_instance.running:
                        try:
                            templates = [
                                "Turn it up for {artist} - {title} — this one's a banger! 🔥",
                                "Brace yourselves: {artist} - {title} is about to melt faces.",
                                "Turn it UP — {artist} - {title}! Respect the volume.",
                                "Lock in: {artist} - {title} — no refunds for blown minds.",
                                "Warning: {artist} - {title} incoming. Headphones advised. 😈"
                            ]
                            msg = random.choice(templates).format(artist=cur[0], title=cur[1])
                            bot_instance.send(msg)
                        except Exception as e:
                            log(f"Error announcing now playing: {e}")

            time.sleep(poll_interval)
        except Exception as e:
            log(f"NowPlaying Tracker Error: {e}")
            time.sleep(poll_interval)


def start_mod_tracker():
    global mod_tracker_thread, mod_tracker_running
    if mod_tracker_running:
        return
    mod_tracker_running = True
    mod_tracker_thread = threading.Thread(target=run_mod_tracker_loop, daemon=True)
    mod_tracker_thread.start()
    log("Mod Tracker: started")


def stop_mod_tracker():
    global mod_tracker_running, mod_tracker_thread
    if not mod_tracker_running:
        return
    mod_tracker_running = False
    if mod_tracker_thread:
        mod_tracker_thread.join(timeout=5)
    log("Mod Tracker: stopped")


def run_mod_tracker_loop():
    """Polls the Twitch TMI endpoint for the current moderators list and announces new mods once."""
    global mod_tracker_running, shouted_mods, bot_instance
    poll_interval = 15
    # sanitize channel for the TMI endpoint (no leading '#', lowercase)
    channel_clean = TWITCH_CHANNEL.strip().lstrip('#').lower()
    url = f"https://tmi.twitch.tv/group/user/{channel_clean}/chatters"
    while mod_tracker_running:
        try:
            if not bot_instance or not bot_instance.running:
                time.sleep(poll_interval)
                continue
            try:
                response = requests.get(url, timeout=5)
                response.raise_for_status()
                data = response.json()
            except requests.HTTPError as he:
                log(f"Mod Tracker HTTP Error ({url}): {he}")
                time.sleep(poll_interval)
                continue
            except Exception as e:
                log(f"Mod Tracker Error fetching TMI: {e}")
                time.sleep(poll_interval)
                continue

            mods_raw = data.get('chatters', {}).get('moderators', []) or []
            mods = set(m.strip().lstrip('@').lower() for m in mods_raw)

            # Exclude the bot and broadcaster
            if TWITCH_NICK:
                mods.discard(TWITCH_NICK.strip().lstrip('@').lower())
            mods.discard(channel_clean)

            new_mods = [m for m in mods if m not in shouted_mods]
            for m in new_mods:
                try:
                    if bot_instance and bot_instance.running:
                        bot_instance.send(f"Shoutout to moderator @{m} — thanks for keeping chat tidy!")
                        shouted_mods.add(m)
                except Exception as e:
                    log(f"Error sending mod shoutout for {m}: {e}")

        except Exception as e:
            log(f"Mod Tracker Error: {e}")

        time.sleep(poll_interval)

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

# Bleep game removed
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