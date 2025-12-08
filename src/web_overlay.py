from flask import Flask, render_template_string
from datetime import datetime, timedelta
import pytz

from db import get_db_connection
from utils import log
from config import REFRESH, BG, COLOR, TITLECOL, FSIZE

app = Flask(__name__)

# Shared state with the main GUI thread
shared_state = {
    "next_420_utc": None,
    "next_420_city": None,
    "popup_message": "",
    "popup_expire_utc": None
}

HTML = r"""
<!DOCTYPE html>
<html>
<head>
<meta http-equiv="refresh" content="{{ refresh }}">
<style>
body{
    background:{{bg}};
    color:{{color}};
    font-size:{{fsize}}px;
    font-family:Segoe UI, sans-serif;
    margin:15px;
}
@keyframes glow{
    0%  {color:#fff; text-shadow:0 0 10px #0f0;}
    100%{color:{{color}}; text-shadow:none;}
}
.highlight{
    animation:glow 5s ease-out 1;
    font-weight:bold;
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
.item{
    margin-left:10px;
    white-space:nowrap;
    overflow:hidden;
    text-overflow: ellipsis; /* Added for better overflow handling */
}

/* 420 popup, fixed 600px width, below main overlay */
.popup420{
    margin:25px auto 0 auto;
    width:600px;
    max-width:90vw;
    text-align:center;
    background:rgba(10,0,20,0.85);
    border:2px solid #66ff99;
    box-shadow:0 0 18px rgba(102,255,153,0.6);
    padding:18px;
    border-radius:10px;
    color:#e0ffe0;
    font-size:{{fsize*1.0}}px;
    animation:smokeFade 10s ease-out forwards;
}
.popup420-title{
    font-weight:bold;
    color:#a855ff;
    text-shadow:0 0 15px #a855ff;
    font-size:{{fsize*1.2}}px;
}
.popup420-text{
    margin-top:4px;
    font-size:{{fsize*0.95}}px;
}
@keyframes smokeFade{
    0%{opacity:0;}
    10%{opacity:1;}
    70%{opacity:1;}
    100%{opacity:0;}
}
</style>
</head>
<body>

<div class="title">Now Playing</div>
<div class="now">{{ now.artist|default('Nothing playing') }} - {{ now.title|default('') }}</div>

<div class="title">Up Next</div>
<div class="item">{{ nxt.artist|default('Nothing queued') }} - {{ nxt.title|default('') }}</div>

<div class="title">History</div>
{% for h in history %}
  <div class="item">{{h.artist}} - {{h.title}}</div>
{% endfor %}

<div class="title">Requests</div>
{% if requests %}
  {% for r in requests %}
    <div class="item {% if loop.first %}highlight{% endif %}">
      🎧 {{r.username}} → {{r.artist}} - {{r.title}}
    </div>
  {% endfor %}
{% else %}
  <div class="item">No pending requests.</div>
{% endif %}

<div class="title">🌿 Next Blaze Time 🌿</div>
<div class="item">{{ next_city }}{% if next_eta %} — {{ next_eta }}{% endif %}</div>

{% if popup_text %}
<div class="popup420">
  <div class="popup420-title">🌿🔥 4:20 BLAZE IT 🔥🌿</div>
  <div class="popup420-text">{{ popup_text }}</div>
</div>
{% endif %}

</body>
</html>
"""

def get_data() -> tuple:
    try:
        conn = get_db_connection()
        try:
            with conn.cursor() as c:
                # NOW + HISTORY
                c.execute("SELECT artist,title FROM history ORDER BY date_played DESC LIMIT 5")
                r = c.fetchall()
                now_t = r[0] if r else {"artist": "", "title": ""}
                history = [{"artist": x["artist"], "title": x["title"]} for x in r[1:]]

                # NEXT
                c.execute(
                    "SELECT s.artist,s.title FROM queuelist q "
                    "JOIN songs s ON s.ID=q.songID ORDER BY q.ID ASC LIMIT 1"
                )
                nxt = c.fetchone() or {"artist": "", "title": ""}

                # REQUESTS
                c.execute(
                    "SELECT username,artist,title "
                    "FROM requests r JOIN songs s ON s.ID=r.songID "
                    "WHERE played=0 OR played IS NULL "
                    "ORDER BY requested DESC LIMIT 10"
                )
                req = c.fetchall()

            return now_t, nxt, history, req
        finally:
            conn.close()
    except Exception as e:
        log(f"DB Query Error in Overlay: {e}")
        return {}, {}, [], []

def format_eta(delta: timedelta) -> str:
    total = int(delta.total_seconds())
    if total <= 0:
        return "any moment"
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    out = []
    if h > 0:
        out.append(f"{h}h")
    if m > 0 or h > 0:
        out.append(f"{m}m")
    out.append(f"{s}s")
    return " ".join(out)

@app.route("/")
def index():
    now_utc = datetime.now(pytz.utc)

    popup_text = ""
    ncity = ""
    neta = ""

    if shared_state["next_420_utc"] and shared_state["next_420_city"]:
        delta = shared_state["next_420_utc"] - now_utc
        ncity = shared_state["next_420_city"]
        neta = format_eta(delta)

    if shared_state["popup_expire_utc"] and now_utc < shared_state["popup_expire_utc"]:
        popup_text = shared_state["popup_message"]

    now_t, nxt, history, req = get_data()

    return render_template_string(
        HTML,
        now=now_t,
        nxt=nxt,
        history=history,
        requests=req,
        next_city=ncity,
        next_eta=neta,
        popup_text=popup_text,
        refresh=REFRESH,
        bg=BG,
        color=COLOR,
        titlecol=TITLECOL,
        fsize=FSIZE,
    )