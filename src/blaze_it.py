import os
import random
from datetime import datetime, timedelta
import pytz
import winsound
import pyttsx3

from utils import log
from config import SOUND_FILE

zones = {
    "America/New_York": [
        "New York", "Miami", "Philadelphia", "Boston", "Atlanta", "Orlando", "Washington D.C.",
    ],
    "America/Chicago": [
        "Chicago", "Dallas", "Kansas City", "New Orleans", "Memphis", "St. Louis", "Milwaukee",
    ],
    "America/Denver": ["Denver", "Salt Lake City", "Boise", "Colorado Springs", "Cheyenne"],
    "America/Los_Angeles": [
        "Los Angeles", "San Francisco", "Seattle", "Las Vegas", "Portland", "San Diego", "Sacramento",
    ],
    "America/Phoenix": ["Phoenix", "Tucson", "Flagstaff"],
    "America/Anchorage": ["Anchorage", "Fairbanks", "Juneau"],
    "Pacific/Honolulu": ["Honolulu", "Maui", "Hilo"],
    "Europe/London": ["London", "Manchester", "Birmingham", "Liverpool", "Bristol"],
    "Europe/Paris": ["Paris", "Lyon", "Marseille", "Nice", "Bordeaux"],
    "Europe/Berlin": ["Berlin", "Hamburg", "Munich", "Frankfurt", "Cologne"],
    "Europe/Moscow": ["Moscow", "St. Petersburg", "Kazan", "Sochi"],
    "Asia/Tokyo": ["Tokyo", "Osaka", "Kyoto", "Nagoya"],
    "Asia/Seoul": ["Seoul", "Busan", "Incheon"],
    "Asia/Singapore": ["Singapore"],
    "Asia/Bangkok": ["Bangkok", "Chiang Mai", "Pattaya"],
    "Australia/Sydney": ["Sydney", "Melbourne", "Brisbane", "Canberra", "Gold Coast"],
    "Asia/Dubai": ["Dubai", "Abu Dhabi"],
    "Africa/Johannesburg": ["Johannesburg", "Cape Town"],
    "America/Sao_Paulo": ["Sao Paulo", "Rio de Janeiro"],
}

jokes = [
    "spark it if you got it!",
    "puff puff pass — don’t hold it hostage!",
    "snacks are now mandatory!",
    "inhale the good shit, exhale the bullshit!",
    "stoner mode activated!",
    "time to legally forget responsibilities!",
    "let's get baked like cookies!",
    "weed be good together right now!",
]


def next_420_pair() -> tuple[list, list]:
    """Get next AM and PM locations globally."""
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


def compute_next_420(now_utc: datetime = None) -> tuple[datetime, str]:
    """For countdown / overlay: next blaze event and one representative city."""
    if now_utc is None:
        now_utc = datetime.now(pytz.utc)
    best = None
    city = None
    for zone, cities in zones.items():
        tz = pytz.timezone(zone)
        local = now_utc.astimezone(tz)
        for hr in (4, 16):
            t = tz.localize(datetime(local.year, local.month, local.day, hr, 20))
            if t <= local:
                t += timedelta(days=1)
            u = t.astimezone(pytz.utc)
            if not best or u < best:
                best = u
                city = random.choice(cities)
    return best, city


def fire_420(bot_instance, test: bool = False) -> str:
    """Triggers the 420 announcement and returns the message."""
    now_utc = datetime.now(pytz.utc)
    am = []
    pm = []

    # check actual 4:20 now
    for zone, cities in zones.items():
        tz = pytz.timezone(zone)
        local = now_utc.astimezone(tz)
        if local.hour == 4 and local.minute == 20:
            am += cities
        if local.hour == 16 and local.minute == 20:
            pm += cities

    # If test or no exact hits, pick the upcoming AM/PM pair
    if test or (not am and not pm):
        am, pm = next_420_pair()

    city_am = random.choice(am) if am else "Somewhere"
    city_pm = random.choice(pm) if pm else "Somewhere"
    msg = f"It's 4:20 in {city_am} and {city_pm} — {random.choice(jokes)}"

    # Audio (with fallback)
    try:
        if os.path.exists(SOUND_FILE):
            winsound.PlaySound(SOUND_FILE, winsound.SND_FILENAME | winsound.SND_ASYNC)
        else:
            log(f"Sound file not found at {SOUND_FILE}")
    except Exception as e:
        log(f"Sound failed: {e}. Consider using pydub for cross-platform support.")

    # TTS (with fallback)
    try:
        v = pyttsx3.init()
        v.setProperty("rate", 160)
        v.say(msg)
        v.runAndWait()
    except Exception as e:
        log(f"TTS failed: {e}. Consider gTTS as alternative.")

    log("[420] " + msg)

    if bot_instance:
        bot_instance.send(msg)

    return msg