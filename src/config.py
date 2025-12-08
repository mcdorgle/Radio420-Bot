import os
import sys
import textwrap
import configparser

# ======================================================
# PATHS / CONFIG
# ======================================================

# Where bundled resources live (PyInstaller) vs where EXE/script lives
if getattr(sys, "frozen", False):
    BUNDLE_DIR = sys._MEIPASS          # internal bundle
    APP_DIR = os.path.dirname(sys.argv[0])  # actual EXE location
    CONFIG_PATH = os.path.join(APP_DIR, "config.ini")
else:
    # Look for config relative to the current working directory, not the script file.
    # This is more robust for development environments.
    APP_DIR = os.getcwd()
    BUNDLE_DIR = APP_DIR
    CONFIG_PATH = os.path.join(APP_DIR, "config.ini")

SOUND_FILE = os.path.join(BUNDLE_DIR, "blaze.wav")  # blaze.wav shipped with app / add-data
LOGO_FILE = os.path.join(BUNDLE_DIR, "logo.png")  # Assume logo.png is bundled similarly

config = configparser.ConfigParser()

def create_default_config():
    """Creates a default config.ini file if one does not exist."""
    log_msg = f"Config file not found. Creating a default one at: {CONFIG_PATH}"
    print(log_msg) # Log function might not be ready yet, so print.
    
    default_config_content = textwrap.dedent("""\
    [twitch]
    nick = your_twitch_username
    channel = your_twitch_channel
    oauth = oauth:xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

    [database]
    host = 127.0.0.1
    user = root
    password = 
    db = radiodj

    [server]
    host = 127.0.0.1
    port = 8080

    [overlay]
    max_results = 5

    [style]
    background = #000000
    text_color = #FFEB3B
    title_color = #FFC107
    font_size = 20
    refresh_rate = 5

    [encoder1]
    name = Primary Stream
    host = 127.0.0.1
    port = 8000
    password = hackme
    mount = /stream
    enabled = false

    [encoder2]
    name = Backup Stream
    enabled = false

    [encoder3]
    name = Test Stream
    enabled = false
    """)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(default_config_content)

if not os.path.exists(CONFIG_PATH):
    create_default_config()

config.read(CONFIG_PATH, encoding="utf-8")

# ======================================================
# CONFIG VALUES WITH VALIDATION
# ======================================================

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

# Shoutcast Encoder Configs (up to 3)
ENCODERS = []
for i in range(1, 4):
    prefix = f"encoder{i}"
    if config.has_section(prefix):
        ENCODERS.append({
            "name": config.get(prefix, "name", fallback=f"Encoder {i}"),
            "host": config.get(prefix, "host", fallback="localhost"),
            "port": config.getint(prefix, "port", fallback=8000),
            "password": config.get(prefix, "password", fallback=""),
            "mount": config.get(prefix, "mount", fallback="/stream"),
            "enabled": config.getboolean(prefix, "enabled", fallback=False)
        })

def save_config_from_gui(entries: dict) -> None:
    """Saves the configuration from the GUI's entry widgets and variables."""
    # update config object
    for section, keys in entries.items():
        if section != "audio": # Do not save the audio section directly, it's a combobox selection
            for key, value_source in keys.items():
                # Handle different Tkinter variable types
                if hasattr(value_source, 'get'): # Covers Entry, BooleanVar, etc.
                    val = value_source.get()
                    # Explicitly convert booleans to 'true'/'false' for config file clarity
                    if isinstance(val, bool):
                        config.set(section, key, 'true' if val else 'false')
                    else:
                        config.set(section, key, str(val))
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        config.write(f)