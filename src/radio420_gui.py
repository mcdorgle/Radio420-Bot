import os
import sys
import socket
import time
import pymysql
import re
import threading
import configparser
import random
import queue
import logging
from datetime import datetime, timedelta

from flask import Flask, render_template_string
from werkzeug.serving import make_server

import pytz
import pyttsx3
import winsound

import tkinter as tk
from tkinter import ttk

# ======================================================
# BASE DIR (works for .py and PyInstaller .exe)
# ======================================================
if getattr(sys, "frozen", False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ======================================================
# CONFIG LOAD
# ======================================================
config = configparser.ConfigParser()
config.read(os.path.join(BASE_DIR, "config.ini"))

TWITCH_SERVER = "irc.chat.twitch.tv"
TWITCH_PORT = 6667

TWITCH_NICK = config.get("twitch", "nick", fallback="")
TWITCH_CHANNEL = config.get("twitch", "channel", fallback="")
TWITCH_OAUTH = config.get("twitch", "oauth", fallback="")

MYSQL_HOST = config.get("database", "host", fallback="localhost")
MYSQL_USER = config.get("database", "user", fallback="root")
MYSQL_PASS = config.get("database", "password", fallback="")
MYSQL_DB = config.get("database", "db", fallback="radiodj")

HTTP_HOST = config.get("server", "host", fallback="0.0.0.0")
HTTP_PORT = config.getint("server", "port", fallback=8080)

MAX_RESULTS = config.getint("overlay", "max_results", fallback=5)
REFRESH = config.getint("style", "refresh_rate", fallback=5)

BG = config.get("style", "background", fallback="#000000")
COLOR = config.get("style", "text_color", fallback="#FFEB3B")
TITLECOL = config.get("style", "title_color", fallback="#FFC107")
FSIZE = config.getint("style", "font_size", fallback=20)

SOUND_FILE = os.path.join(BASE_DIR, "blaze.wav")

# ======================================================
# GLOBALS
# ======================================================
last_results = {}

bot_instance = None
twitch_thread = None
twitch_running = False

overlay_server = None
overlay_thread = None
overlay_running = False

tracker_thread = None
announcer_thread = None
tracker_running = False
announcer_running = False

next_420_utc = None
next_420_city = None
last_fired_target = None

last_420_message = ""
popup_message = ""
popup_expire_utc = None

log_queue = queue.Queue()

# ======================================================
# LOGGING UTIL + redirect Flask logs into GUI
# ======================================================
def log(msg: str):
    print(msg)
    try:
        log_queue.put(msg)
    except Exception:
        pass


class TkLogHandler(logging.Handler):
    def emit(self, record):
        log(self.format(record))


# Attach handler to werkzeug (Flask HTTP logs)
werk_handler = TkLogHandler()
werk_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
werk_log = logging.getLogger("werkzeug")
werk_log.setLevel(logging.INFO)
werk_log.addHandler(werk_handler)

# Optionally attach to root as well for other logs
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.addHandler(werk_handler)

# ======================================================
# DB
# ======================================================
def db():
    return pymysql.connect(
        host=MYSQL_HOST,
        user=MYSQL_USER,
        password=MYSQL_PASS,
        database=MYSQL_DB,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )

# ======================================================
# TWITCH BOT
# ======================================================
class TwitchBot:
    def __init__(self):
        self.sock = None
        self.running = True

    def connect(self):
        if not self.running:
            return
        try:
            self.sock = socket.socket()
            self.sock.connect((TWITCH_SERVER, TWITCH_PORT))
            self.sock.sendall(
                b"CAP REQ :twitch.tv/tags twitch.tv/commands twitch.tv/membership\r\n"
            )
            self.sock.sendall(f"PASS {TWITCH_OAUTH}\r\n".encode())
            self.sock.sendall(f"NICK {TWITCH_NICK}\r\n".encode())
            self.sock.sendall(f"JOIN #{TWITCH_CHANNEL}\r\n".encode())
            self.send("🎧 Radio420 Bot Ready — !search <song>")
            log("Twitch: Connected")
        except Exception as e:
            log(f"Twitch Connect Error: {e}")
            time.sleep(5)
            if self.running:
                self.connect()

    def send(self, msg):
        try:
            if self.sock:
                self.sock.sendall(f"PRIVMSG #{TWITCH_CHANNEL} :{msg}\r\n".encode())
        except Exception as e:
            log(f"Twitch Send Error: {e}")

    def parse(self, line):
        try:
            if line.startswith("@"):
                line = line.split(" ", 1)[1]
            user = line.split("!", 1)[0][1:]
            msg = line.split(" :", 1)[1]
            return user, msg.strip()
        except Exception:
            return None, None

    def search(self, user, q):
        c = db().cursor()
        c.execute(
            "SELECT ID,artist,title FROM songs WHERE artist LIKE %s OR title LIKE %s LIMIT %s",
            (f"%{q}%", f"%{q}%", MAX_RESULTS),
        )
        rows = c.fetchall()
        if not rows:
            return self.send(f"@{user} No results")
        last_results[user.lower()] = rows
        out = [f"[!{i}] {r['artist']} - {r['title']}" for i, r in enumerate(rows, 1)]
        self.send(f"@{user} " + " | ".join(out))
        self.send(f"Pick using !1–!{len(rows)}")

    def pick(self, user, i):
        u = user.lower()
        if u not in last_results:
            return self.send("Use !search first")
        rows = last_results[u]
        if not 1 <= i <= len(rows):
            return self.send("Invalid number")
        track = rows[i - 1]

        c = db().cursor()
        c.execute(
            "INSERT INTO requests (songID,username,userIP,message,requested) VALUES (%s,%s,%s,%s,NOW())",
            (track["ID"], user, f"twitch/{user}", ""),
        )
        self.send(f"@{user} requested → {track['artist']} - {track['title']}")

    def run(self):
        self.connect()
        while self.running:
            try:
                data = self.sock.recv(2048).decode("utf8", "ignore")
                if not data:
                    if not self.running:
                        break
                    time.sleep(1)
                    continue
                for line in data.split("\r\n"):
                    if not line:
                        continue
                    if "PING" in line:
                        try:
                            self.sock.sendall(b"PONG\r\n")
                        except Exception:
                            pass
                    if "PRIVMSG" in line:
                        user, msg = self.parse(line)
                        if not msg:
                            continue
                        if msg.startswith("!search"):
                            self.search(user, msg[8:].strip())
                            continue
                        m = re.match(r"!(\d+)$", msg)
                        if m:
                            self.pick(user, int(m.group(1)))
                            continue
            except Exception as e:
                if not self.running:
                    break
                log(f"Twitch Loop Error: {e}")
                time.sleep(3)
                self.connect()

        log("Twitch: Bot loop exiting")
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass

    def stop(self):
        self.running = False
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass

# ======================================================
# OVERLAY (Flask)
# ======================================================
app = Flask(__name__)

HTML = r"""
<!DOCTYPE html><html><head>
<meta http-equiv="refresh" content="{{ refresh }}">
<style>
body{
  background:{{bg}};
  color:{{color}};
  font-size:{{fsize}}px;
  font-family:Segoe UI, sans-serif;
  margin:15px;
}
.title{
  color:{{titlecol}};
  font-size:{{fsize*1.3}}px;
  font-weight:bold;
  margin-top:8px;
}
.now{
  font-size:{{fsize*1.6}}px;
  font-weight:bold;
}
.popup420{
  width:600px;
  max-width:90vw;
  text-align:center;
  margin:20px auto;
  background:rgba(10,0,20,.85);
  color:#dfffdf;
  padding:15px;
  border-radius:10px;
  border:2px solid #66ff99;
  box-shadow:0 0 20px #66ff99;
  animation:fade 12s forwards;
}
.popup420-title{
  font-weight:bold;
  color:#b566ff;
  font-size:{{fsize*1.2}}px;
}
@keyframes fade{0%{opacity:0;}10%{opacity:1;}80%{opacity:1;}100%{opacity:0;}}
</style></head><body>

<div class="title">Now Playing</div>
<div class="now">{{ now.artist }} - {{ now.title }}</div>

<div class="title">Requests</div>
{% if requests %}
  {% for r in requests %}
    <div>🎧 {{ r.username }} → {{ r.artist }} - {{ r.title }}</div>
  {% endfor %}
{% else %}
  <div>No pending requests.</div>
{% endif %}

<div class="title">Next 4:20</div>
<div>{{ next_city }} — {{ next_eta }}</div>

{% if popup_text %}
<div class="popup420">
  <div class="popup420-title">🌿🔥 4:20 BLAZE IT 🔥🌿</div>
  <div>{{ popup_text }}</div>
</div>
{% endif %}
</body></html>
"""

def get_overlay_data():
    c = db().cursor()
    # Now playing (history last)
    c.execute(
        "SELECT artist,title FROM history ORDER BY date_played DESC LIMIT 1"
    )
    r = c.fetchone()
    now = r if r else {"artist": "", "title": ""}

    # Requests
    c.execute(
        "SELECT username,artist,title FROM requests r "
        "JOIN songs s ON s.ID=r.songID "
        "WHERE played=0 OR played IS NULL "
        "ORDER BY requested DESC LIMIT 10"
    )
    req = c.fetchall()
    return now, req

def format_eta(delta: timedelta):
    total = int(delta.total_seconds())
    if total <= 0:
        return "any moment"
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    parts = []
    if h:
        parts.append(f"{h}h")
    if h or m:
        parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)

@app.route("/")
def index():
    global next_420_utc, next_420_city, popup_message, popup_expire_utc
    now, req = get_overlay_data()
    now_utc = datetime.now(pytz.utc)

    if next_420_utc and next_420_city:
        eta = format_eta(next_420_utc - now_utc)
        ncity = next_420_city
    else:
        eta = "calculating..."
        ncity = "..."

    popup_text = ""
    if popup_expire_utc and now_utc < popup_expire_utc:
        popup_text = popup_message

    return render_template_string(
        HTML,
        now=now,
        requests=req,
        next_city=ncity,
        next_eta=eta,
        popup_text=popup_text,
        refresh=REFRESH,
        bg=BG,
        color=COLOR,
        titlecol=TITLECOL,
        fsize=FSIZE,
    )

def start_overlay_server():
    global overlay_server, overlay_thread, overlay_running
    if overlay_running:
        return
    try:
        overlay_server = make_server(HTTP_HOST, HTTP_PORT, app)
        overlay_thread = threading.Thread(
            target=overlay_server.serve_forever, daemon=True
        )
        overlay_thread.start()
        overlay_running = True
        log(f"Overlay: http://{HTTP_HOST}:{HTTP_PORT}/")
    except Exception as e:
        log(f"Overlay start error: {e}")

def stop_overlay_server():
    global overlay_server, overlay_thread, overlay_running
    if not overlay_running:
        return
    try:
        if overlay_server:
            overlay_server.shutdown()
    except Exception as e:
        log(f"Overlay stop error: {e}")
    overlay_server = None
    overlay_thread = None
    overlay_running = False
    log("Overlay: stopped")

# ======================================================
# 4:20 LOGIC
# ======================================================
zones = {
    "America/New_York": ["New York", "Miami", "Orlando", "Boston", "Atlanta"],
    "America/Chicago": ["Chicago", "Mississippi", "Dallas", "Memphis", "St. Louis"],
    "America/Los_Angeles": ["Los Angeles", "Las Vegas", "Seattle", "Portland", "San Diego"],
    "Europe/London": ["London", "Manchester", "Liverpool"],
    "Europe/Paris": ["Paris", "Lyon", "Nice", "Marseille"],
    "Europe/Moscow": ["Moscow", "St. Petersburg", "Kazan"],
    "Asia/Tokyo": ["Tokyo", "Osaka", "Kyoto"],
    "Asia/Bangkok": ["Bangkok", "Chiang Mai"],
    "Australia/Sydney": ["Sydney", "Melbourne", "Brisbane", "Gold Coast"],
}

jokes = [
    "spark it if you got it!",
    "puff puff pass — don’t hold it hostage!",
    "snacks are now mandatory!",
    "inhale the good shit, exhale the bullshit!",
    "stoner mode activated!",
]

def next_420_pair():
    now_utc = datetime.now(pytz.utc)
    am = []
    pm = []
    for zone, cities in zones.items():
        tz = pytz.timezone(zone)
        local = now_utc.astimezone(tz)

        t_am = tz.localize(datetime(local.year, local.month, local.day, 4, 20))
        t_pm = tz.localize(datetime(local.year, local.month, local.day, 16, 20))

        if t_am <= local:
            t_am += timedelta(days=1)
        if t_pm <= local:
            t_pm += timedelta(days=1)

        am.append((t_am, cities))
        pm.append((t_pm, cities))

    next_am_cities = min(am, key=lambda x: x[0])[1]
    next_pm_cities = min(pm, key=lambda x: x[0])[1]
    return next_am_cities, next_pm_cities

def fire_420(test=False):
    global last_420_message, popup_message, popup_expire_utc

    now_utc = datetime.now(pytz.utc)
    am = []
    pm = []

    for zone, cities in zones.items():
        tz = pytz.timezone(zone)
        local = now_utc.astimezone(tz)
        if local.hour == 4 and local.minute == 20:
            am += cities
        if local.hour == 16 and local.minute == 20:
            pm += cities

    if test or (not am and not pm):
        am, pm = next_420_pair()

    city_am = random.choice(am)
    city_pm = random.choice(pm)

    message = f"It's 4:20 in {city_am} and {city_pm} — {random.choice(jokes)}"

    # Sound
    try:
        winsound.PlaySound(SOUND_FILE, winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception as e:
        log(f"Sound failed: {e}")

    # TTS
    try:
        v = pyttsx3.init()
        v.setProperty("rate", 160)
        v.say(message)
        v.runAndWait()
    except Exception as e:
        log(f"TTS failed: {e}")

    last_420_message = message
    popup_message = message
    popup_expire_utc = datetime.now(pytz.utc) + timedelta(seconds=12)
    log("[420] " + message)

    if bot_instance:
        bot_instance.send(message)

def compute_next_420():
    now_utc = datetime.now(pytz.utc)
    soon = None
    city = None
    for zone, cities in zones.items():
        tz = pytz.timezone(zone)
        local = now_utc.astimezone(tz)
        for hr in (4, 16):
            t = tz.localize(datetime(local.year, local.month, local.day, hr, 20))
            if t <= local:
                t += timedelta(days=1)
            utc = t.astimezone(pytz.utc)
            if soon is None or utc < soon:
                soon = utc
                city = random.choice(cities)
    return soon, city

def next_420_tracker_loop():
    global next_420_utc, next_420_city, tracker_running
    while tracker_running:
        next_420_utc, next_420_city = compute_next_420()
        time.sleep(60)
    log("420 Tracker: stopped")

def run_420_announcer_loop():
    global last_fired_target, announcer_running
    while announcer_running:
        if next_420_utc:
            delta = (next_420_utc - datetime.now(pytz.utc)).total_seconds()
            if 0 <= delta <= 30 and last_fired_target != next_420_utc:
                fire_420(test=False)
                last_fired_target = next_420_utc
        time.sleep(5)
    log("420 Announcer: stopped")

# ======================================================
# SERVICE CONTROL HELPERS
# ======================================================
def start_twitch():
    global bot_instance, twitch_thread, twitch_running
    if twitch_running:
        return
    bot_instance = TwitchBot()
    twitch_thread = threading.Thread(target=bot_instance.run, daemon=True)
    twitch_running = True
    twitch_thread.start()
    log("Twitch: started")

def stop_twitch():
    global bot_instance, twitch_thread, twitch_running
    if not twitch_running:
        return
    if bot_instance:
        bot_instance.stop()
    twitch_running = False
    log("Twitch: stop requested")

def restart_twitch():
    stop_twitch()
    time.sleep(1)
    start_twitch()

def start_overlay():
    start_overlay_server()

def stop_overlay():
    stop_overlay_server()

def restart_overlay():
    stop_overlay_server()
    time.sleep(1)
    start_overlay_server()

def start_420():
    global tracker_thread, announcer_thread, tracker_running, announcer_running
    if tracker_running or announcer_running:
        return
    tracker_running = True
    announcer_running = True
    tracker_thread = threading.Thread(target=next_420_tracker_loop, daemon=True)
    announcer_thread = threading.Thread(target=run_420_announcer_loop, daemon=True)
    tracker_thread.start()
    announcer_thread.start()
    log("420 Timer: started")

def stop_420():
    global tracker_running, announcer_running
    tracker_running = False
    announcer_running = False
    log("420 Timer: stop requested")

def restart_420():
    stop_420()
    time.sleep(1)
    start_420()

def test_420():
    fire_420(test=True)

# ======================================================
# GUI
# ======================================================
def build_gui():
    root = tk.Tk()
    root.title("Radio420 Control Panel")

    bg = "#05040a"
    txt = "#b7ffb7"
    accent = "#22c55e"
    danger = "#f97373"
    root.configure(bg=bg)

    style = ttk.Style()
    style.theme_use("clam")
    style.configure(".", background=bg, foreground=txt)
    style.configure("TFrame", background=bg)
    style.configure("TLabel", background=bg, foreground=txt)
    style.configure("TLabelframe", background=bg, foreground=txt)
    style.configure("TLabelframe.Label", background=bg, foreground=txt)
    style.configure("TButton", padding=6)

    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True, padx=8, pady=8)

    dash = ttk.Frame(notebook)
    logs_frame = ttk.Frame(notebook)

    notebook.add(dash, text="Dashboard")
    notebook.add(logs_frame, text="Logs")

    # --- Dashboard: Status panel ---
    status_frame = ttk.LabelFrame(dash, text="Service Status", padding=10)
    status_frame.pack(fill="x", padx=10, pady=10)

    def make_status_row(parent, name, row):
        ttk.Label(parent, text=name + ":").grid(row=row, column=0, sticky="w", padx=4, pady=2)
        lbl = tk.Label(parent, text="○ OFFLINE", bg=bg, fg=danger, font=("Segoe UI", 10, "bold"))
        lbl.grid(row=row, column=1, sticky="w", padx=4, pady=2)
        return lbl

    lbl_twitch_status = make_status_row(status_frame, "Twitch Bot", 0)
    lbl_overlay_status = make_status_row(status_frame, "Overlay Server", 1)
    lbl_420_status = make_status_row(status_frame, "420 Timer", 2)

    # --- Dashboard: Next / Last info ---
    info_frame = ttk.LabelFrame(dash, text="Blaze Info", padding=10)
    info_frame.pack(fill="x", padx=10, pady=5)

    lbl_next_420 = ttk.Label(info_frame, text="Next 4:20: calculating...")
    lbl_next_420.pack(anchor="w", pady=2)

    lbl_last_420 = ttk.Label(info_frame, text="Last Event: none")
    lbl_last_420.pack(anchor="w", pady=2)

    lbl_overlay_url = ttk.Label(
        info_frame,
        text=f"Overlay URL: http://{HTTP_HOST}:{HTTP_PORT}/",
    )
    lbl_overlay_url.pack(anchor="w", pady=2)

    # --- Dashboard: Controls ---
    controls = ttk.LabelFrame(dash, text="Controls", padding=10)
    controls.pack(fill="x", padx=10, pady=10)

    # Twitch controls
    row = 0
    ttk.Label(controls, text="Twitch Bot").grid(row=row, column=0, sticky="w", padx=4, pady=4)
    ttk.Button(controls, text="Start", command=start_twitch).grid(row=row, column=1, padx=4, pady=4)
    ttk.Button(controls, text="Stop", command=stop_twitch).grid(row=row, column=2, padx=4, pady=4)
    ttk.Button(controls, text="Restart", command=restart_twitch).grid(row=row, column=3, padx=4, pady=4)

    # Overlay controls
    row += 1
    ttk.Label(controls, text="Overlay Server").grid(row=row, column=0, sticky="w", padx=4, pady=4)
    ttk.Button(controls, text="Start", command=start_overlay).grid(row=row, column=1, padx=4, pady=4)
    ttk.Button(controls, text="Stop", command=stop_overlay).grid(row=row, column=2, padx=4, pady=4)
    ttk.Button(controls, text="Restart", command=restart_overlay).grid(row=row, column=3, padx=4, pady=4)

    # 420 controls
    row += 1
    ttk.Label(controls, text="420 Timer").grid(row=row, column=0, sticky="w", padx=4, pady=4)
    ttk.Button(controls, text="Start", command=start_420).grid(row=row, column=1, padx=4, pady=4)
    ttk.Button(controls, text="Stop", command=stop_420).grid(row=row, column=2, padx=4, pady=4)
    ttk.Button(controls, text="Restart", command=restart_420).grid(row=row, column=3, padx=4, pady=4)

    # Test + Quit
    row += 1
    ttk.Button(controls, text="TEST 4:20 Blaze", command=test_420).grid(
        row=row, column=0, padx=4, pady=8, sticky="w"
    )

    def quit_all():
        stop_420()
        stop_overlay()
        stop_twitch()
        root.after(300, root.destroy)

    ttk.Button(controls, text="Quit", command=quit_all).grid(
        row=row, column=3, padx=4, pady=8, sticky="e"
    )

    # --- Logs tab ---
    txt_log = tk.Text(
        logs_frame,
        height=20,
        bg="#0b0715",
        fg=txt,
        insertbackground=txt,
        borderwidth=0,
    )
    txt_log.pack(fill="both", expand=True, padx=10, pady=10)

    # --- Update loop ---
    def update_ui():
        # Pump logs
        while not log_queue.empty():
            line = log_queue.get()
            txt_log.insert("end", line + "\n")
            txt_log.see("end")

        # Status indicators
        if twitch_running:
            lbl_twitch_status.config(text="● ONLINE", fg=accent)
        else:
            lbl_twitch_status.config(text="○ OFFLINE", fg=danger)

        if overlay_running:
            lbl_overlay_status.config(text="● RUNNING", fg=accent)
        else:
            lbl_overlay_status.config(text="○ STOPPED", fg=danger)

        if tracker_running or announcer_running:
            lbl_420_status.config(text="● ACTIVE", fg=accent)
        else:
            lbl_420_status.config(text="○ IDLE", fg=danger)

        # Next & last info
        if next_420_utc and next_420_city:
            eta = format_eta(next_420_utc - datetime.now(pytz.utc))
            lbl_next_420.config(text=f"Next 4:20: {next_420_city} — {eta}")
        else:
            lbl_next_420.config(text="Next 4:20: calculating...")

        lbl_last_420.config(
            text=f"Last Event: {last_420_message}" if last_420_message else "Last Event: none"
        )

        root.after(300, update_ui)

    update_ui()
    return root

# ======================================================
# MAIN
# ======================================================
if __name__ == "__main__":
    gui = build_gui()
    gui.mainloop()
