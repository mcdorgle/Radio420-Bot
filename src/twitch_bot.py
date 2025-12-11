import socket
import time
import re

from config import ( # noqa
    TWITCH_SERVER, TWITCH_PORT, TWITCH_OAUTH, TWITCH_NICK, TWITCH_CHANNEL, MAX_RESULTS
)
from db import get_db_connection
from utils import log

class TwitchBot:
    def __init__(self):
        self.sock: socket.socket = None
        self.running: bool = True
        self.reconnect_delay: int = 5  # Exponential backoff starting point
        self.first_connect: bool = True  # Flag to track first connection
        self.last_results: dict = {}

    def connect(self) -> None:
        if not self.running:
            return
        try:
            self.sock = socket.socket()
            self.sock.settimeout(10)  # Added timeout to prevent hangs
            self.sock.connect((TWITCH_SERVER, TWITCH_PORT))
            self.sock.sendall(
                b"CAP REQ :twitch.tv/tags twitch.tv/commands twitch.tv/membership\r\n"
            )
            self.sock.sendall(f"PASS {TWITCH_OAUTH}\r\n".encode())
            self.sock.sendall(f"NICK {TWITCH_NICK}\r\n".encode())
            self.sock.sendall(f"JOIN #{TWITCH_CHANNEL}\r\n".encode())
            if self.first_connect:
                from config import STATION_NAME # Import here to get latest value on reconnect
                self.send(f"🎧 {STATION_NAME} is now online & taking requests - use !search <song/artist>")
                self.first_connect = False
            log("Twitch: Connected")
            self.reconnect_delay = 5  # Reset delay on success
        except Exception as e:
            log(f"Twitch Connect Error: {e}")
            time.sleep(self.reconnect_delay)
            self.reconnect_delay = min(self.reconnect_delay * 2, 60)  # Exponential backoff
            if self.running:
                self.connect()

    def send(self, msg: str) -> None:
        try:
            if self.sock:
                self.sock.sendall(f"PRIVMSG #{TWITCH_CHANNEL} :{msg}\r\n".encode())
        except Exception as e:
            log(f"Twitch Send Error: {e}")
            self.connect()  # Attempt reconnect on send failure

    def parse(self, line: str) -> tuple[str, str]:
        try:
            if line.startswith("@"):
                line = line.split(" ", 1)[1]
            user = line.split("!", 1)[0][1:]
            msg = line.split(" :", 1)[1]
            return user, msg.strip()
        except Exception:
            return None, None

    def search(self, user: str, q: str) -> None:
        conn = get_db_connection()
        try:
            with conn.cursor() as c:
                c.execute(
                    "SELECT ID,artist,title FROM songs WHERE artist LIKE %s OR title LIKE %s LIMIT %s",
                    (f"%{q}%", f"%{q}%", MAX_RESULTS),
                )
                rows = c.fetchall()
        finally:
            conn.close()
        if not rows:
            return self.send(f"@{user} No results")
        self.last_results[user.lower()] = rows
        out = [f"[!{i}] {r['artist']} - {r['title']}" for i, r in enumerate(rows, 1)]
        self.send(f"@{user} " + " | ".join(out))
        self.send(f"Pick using !1–!{len(rows)}")

    def pick(self, user: str, i: int) -> None:
        u = user.lower()
        if u not in self.last_results:
            return self.send(f"@{user} use !search first")
        rows = self.last_results[u]
        if not 1 <= i <= len(rows):
            return self.send("Invalid number")
        track = rows[i - 1]
        conn = get_db_connection()
        try:
            with conn.cursor() as c:
                c.execute(
                    "INSERT INTO requests (songID,username,userIP,message,requested) "
                    "VALUES (%s,%s,%s,%s,NOW())",
                    (track["ID"], user, f"twitch/{user}", ""),
                )
        finally:
            conn.close()
        self.send(f"@{user} requested → {track['artist']} - {track['title']}")

    def run(self) -> None:
        # This method is now managed by the services.py logic
        pass

    def stop(self) -> None:
        self.running = False
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass