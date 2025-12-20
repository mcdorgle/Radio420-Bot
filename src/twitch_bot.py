import socket
import time
import re
import random

from config import ( # noqa
    TWITCH_SERVER, TWITCH_PORT, TWITCH_OAUTH, TWITCH_NICK, TWITCH_CHANNEL, MAX_RESULTS, config,
    POINTS_CURRENCY, POINTS_REQUEST_COST, POINTS_PLAYNEXT_COST, POINTS_GIVE_TAX
)
from db import get_db_connection
from utils import log
import pymysql

class TwitchBot:
    def __init__(self):
        self.sock: socket.socket = None
        self.running: bool = True
        self.reconnect_delay: int = 5  # Exponential backoff starting point
        self.first_connect: bool = True  # Flag to track first connection
        self.stream_start_time: float = time.time()
        self.last_results: dict = {}
        self.command_cooldowns: dict = {} # For user-specific command cooldowns
        self.last_active_times: dict = {} # For active point earning cooldown

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
                station_name = config.get("twitch", "station_name", fallback="Radio420")
                self.send(f"🎧 {station_name} is now online & taking requests - use !search <song/artist>")
                self.first_connect = False
            self.stream_start_time = time.time()
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

    def parse(self, line: str) -> tuple[str, str, dict]:
        try:
            tags_raw, _, message_raw = line.partition(' ')
            if not line.startswith("@"):
                return None, None, {}
            
            tags = {kv.split('=', 1)[0]: kv.split('=', 1)[1] for kv in tags_raw[1:].split(';') if '=' in kv}
            user = tags.get('display-name', '').lower()
            
            msg_parts = message_raw.split(" :", 1)
            msg = msg_parts[1].strip() if len(msg_parts) > 1 else ""
            return user, msg, tags
        except Exception:
            return None, None, {}

    def _is_on_cooldown(self, user: str, command: str, cooldown_seconds: int) -> bool:
        """Checks if a user is on cooldown for a specific command."""
        now = time.time()
        user_cooldowns = self.command_cooldowns.setdefault(user, {})
        last_used = user_cooldowns.get(command, 0)
        if now - last_used < cooldown_seconds:
            return True
        user_cooldowns[command] = now
        return False

    def is_mod(self, user: str, tags: dict) -> bool:
        """Checks if a user is a mod or the broadcaster based on tags."""
        is_mod = tags.get('mod') == '1'
        is_broadcaster = user.lower() == TWITCH_CHANNEL.lower()
        return is_mod or is_broadcaster

    # ===== Points System Methods =====

    def get_user_points(self, user: str) -> int:
        """Gets points for a user from the database."""
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT points FROM community_points WHERE username = %s", (user,))
                result = cursor.fetchone()
                return result['points'] if result else 0
        finally:
            conn.close()

    def update_user_points(self, user: str, amount: int, is_active: bool = False) -> int:
        """
        Updates a user's points. Can be a positive or negative amount.
        Returns the new point total.
        """
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                # Use INSERT ... ON DUPLICATE KEY UPDATE to handle new and existing users
                # Also updates last_seen and last_active timestamps
                active_update_sql = ", last_active = NOW()" if is_active else ""
                sql = f"""
                    INSERT INTO community_points (username, points, last_seen{", last_active" if is_active else ""})
                    VALUES (%s, %s, NOW(){", NOW()" if is_active else ""})
                    ON DUPLICATE KEY UPDATE points = points + %s, last_seen = NOW(){active_update_sql};
                """
                # The initial amount for an insert should not be negative.
                insert_amount = max(0, amount)
                cursor.execute(sql, (user, insert_amount, amount))
                conn.commit()
                
                # Get the new total
                cursor.execute("SELECT points FROM community_points WHERE username = %s", (user,))
                result = cursor.fetchone()
                return result['points'] if result else 0
        except Exception as e:
            log(f"Error updating points for {user}: {e}")
            conn.rollback()
        finally:
            conn.close()

    # ===== Command Handlers =====

    def points(self, user: str, msg: str) -> None:
        """!points - Checks the user's point balance."""
        if self._is_on_cooldown(user, 'points', 15): return
        current_points = self.get_user_points(user)
        self.send(f"@{user}, you have {current_points} {POINTS_CURRENCY}.")

    def uptime(self, user: str, msg: str) -> None:
        """!uptime - Shows how long the stream has been live."""
        if self._is_on_cooldown(user, 'uptime', 30): return
        duration_seconds = int(time.time() - self.stream_start_time)
        hours, remainder = divmod(duration_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        self.send(f"The stream has been live for {hours}h {minutes}m {seconds}s.")

    def lastplayed(self, user: str, msg: str) -> None:
        """!lastplayed - Shows the last 3 played songs."""
        if self._is_on_cooldown(user, 'lastplayed', 20): return
        conn = get_db_connection()
        try:
            with conn.cursor() as c:
                c.execute("SELECT artist, title FROM history ORDER BY date_played DESC LIMIT 3")
                rows = c.fetchall()
                if not rows:
                    self.send("No songs have been played recently.")
                    return
                history_str = " | ".join([f"{row['artist']} - {row['title']}" for row in rows])
                self.send(f"Last Played: {history_str}")
        finally:
            conn.close()

    def playing(self, user: str, msg: str) -> None:
        """!playing - Shows the currently playing song (most recent history entry)."""
        if self._is_on_cooldown(user, 'playing', 8):
            return
        conn = get_db_connection()
        try:
            with conn.cursor() as c:
                c.execute("SELECT artist, title FROM history ORDER BY date_played DESC LIMIT 1")
                row = c.fetchone()
                if not row:
                    self.send("Nothing is playing right now.")
                    return
                self.send(f"Now Playing: {row['artist']} - {row['title']}")
        except Exception as e:
            log(f"Error fetching now playing for !playing: {e}")
        finally:
            conn.close()

    def queue(self, user: str, msg: str) -> None:
        """!queue - Shows the next 3 pending requests."""
        if self._is_on_cooldown(user, 'queue', 20): return
        conn = get_db_connection()
        try:
            with conn.cursor() as c:
                c.execute("SELECT s.artist, s.title FROM requests r JOIN songs s ON r.songID = s.ID WHERE r.played = 0 ORDER BY r.ID ASC LIMIT 3")
                rows = c.fetchall()
                if not rows:
                    self.send("The request queue is empty.")
                    return
                queue_str = " | ".join([f"{row['artist']} - {row['title']}" for row in rows])
                self.send(f"Up Next: {queue_str}")
        finally:
            conn.close()

    def search(self, user: str, q: str) -> None:
        if self._is_on_cooldown(user, 'search', 30): return

        # Extract the actual search query from the chat message (strip the command)
        parts = q.split(' ', 1)
        if len(parts) < 2 or not parts[1].strip():
            self.send(f"@{user}, usage: !search <song or artist>")
            return
        query = parts[1].strip()

        conn = get_db_connection()
        try:
            with conn.cursor() as c:
                c.execute(
                    "SELECT ID, artist, title FROM songs WHERE (artist LIKE %s OR title LIKE %s) AND enabled=1 ORDER BY artist, title LIMIT %s",
                    (f"%{query}%", f"%{query}%", MAX_RESULTS)
                )
                rows = c.fetchall()
        finally:
            conn.close()

        if not rows:
            return self.send(f"@{user} No results")

        self.last_results[user.lower()] = rows
        out = [f"{i}. {r['artist']} - {r['title']}" for i, r in enumerate(rows, 1)]
        self.send(f"@{user} " + " | ".join(out))
        self.send(f"Pick using !pick <number> (e.g., !pick 1)")
    
    def pick(self, user: str, i: int) -> None:
        try:
            u = user.lower()  # Standardize username
            log(f"DEBUG: !pick called by {u} for index {i}")

            if u not in self.last_results:
                self.send(f"@{user}, please use !search for a song before trying to !pick one.")
                return

            current_points = self.get_user_points(u)
            if current_points < POINTS_REQUEST_COST:
                self.send(f"@{user}, you don't have enough points to make a request! It costs {POINTS_REQUEST_COST} {POINTS_CURRENCY}, but you only have {current_points}.")
                return

            rows = self.last_results[u]
            if not 1 <= i <= len(rows):
                self.send(f"@{user}, that's not a valid number. Please pick a number from your search results.")
                return

            track = rows[i - 1]
            conn = get_db_connection()
            try:
                with conn.cursor() as cursor:
                    # Perform both actions in a single transaction
                    cursor.execute("UPDATE community_points SET points = points - %s WHERE username = %s", (POINTS_REQUEST_COST, u))

                    cursor.execute(
                        "INSERT INTO requests (songID,username,userIP,message,requested) "
                        "VALUES (%s,%s,%s,%s,NOW())",
                        (track["ID"], user, f"twitch/{user}", ""),
                    )
                conn.commit()  # Commit both changes
                self.send(f"@{user} spent {POINTS_REQUEST_COST} {POINTS_CURRENCY} to request → {track['artist']} - {track['title']}")
                del self.last_results[u]  # Clear search results after successful pick
            except pymysql.err.IntegrityError:
                conn.rollback()
                self.send(f"@{user}, that song has already been requested recently! Your points were not deducted.")
            except Exception as e:
                conn.rollback()
                log(f"Error during !pick transaction: {e}")
                self.send(f"@{user}, an error occurred. Your points were not deducted.")
            finally:
                conn.close()
        except Exception as e:
            log(f"Unhandled error in pick handler for user {user}: {e}")
            try:
                self.send(f"@{user}, an unexpected error occurred while processing your pick.")
            except Exception:
                pass

    def gamble(self, user: str, msg: str) -> None:
        """!gamble <amount> - Gamble your points!"""
        if self._is_on_cooldown(user, 'gamble', 10): return
        
        try:
            amount_to_gamble = int(msg.split()[1])
        except (IndexError, ValueError):
            self.send(f"@{user}, please specify how many points to gamble. Usage: !gamble <amount>")
            return

        if amount_to_gamble <= 0:
            self.send(f"@{user}, you must gamble at least 1 point.")
            return

        current_points = self.get_user_points(user)
        if amount_to_gamble > current_points:
            self.send(f"@{user}, you don't have that many points to gamble! You have {current_points} {POINTS_CURRENCY}.")
            return

        roll = random.randint(1, 100)
        if roll > 50: # Win
            new_total = self.update_user_points(user, amount_to_gamble)
            self.send(f"@{user} rolled a {roll} and won {amount_to_gamble} {POINTS_CURRENCY}! You now have {new_total} {POINTS_CURRENCY}.")
        else: # Lose
            new_total = self.update_user_points(user, -amount_to_gamble)
            self.send(f"@{user} rolled a {roll} and lost {amount_to_gamble} {POINTS_CURRENCY}. You now have {new_total} {POINTS_CURRENCY}.")

    def addpoints(self, user: str, msg: str, tags: dict) -> None:
        """!addpoints <user> <amount> - Mod command to give points."""
        # Permission check: only mods or the broadcaster can use this
        if not self.is_mod(user, tags):
            self.send(f"@{user}, you don't have permission to use that command.")
            return

        parts = msg.split()
        if len(parts) != 3:
            self.send(f"@{user}, usage: !addpoints <username> <amount>")
            return

        try:
            target_user = parts[1].lower().lstrip('@')
            amount = int(parts[2])
        except ValueError:
            self.send(f"@{user}, invalid amount. Please use a number.")
            return

        if not target_user:
            self.send(f"@{user}, you must specify a user to give points to.")
            return

        # update_user_points returns the new total
        new_total = self.update_user_points(target_user, amount)
        self.send(f"Gave {amount} {POINTS_CURRENCY} to {target_user}. They now have {new_total} {POINTS_CURRENCY}.")

    def leaderboard(self, user: str, msg: str) -> None:
        """!leaderboard - Shows the top 5 users with the most points."""
        if self._is_on_cooldown(user, 'leaderboard', 60): return
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT username, points FROM community_points ORDER BY points DESC LIMIT 5")
                rows = cursor.fetchall()
                if not rows:
                    self.send("The leaderboard is empty!")
                    return
                
                leaderboard_entries = []
                for i, row in enumerate(rows, 1):
                    leaderboard_entries.append(f"{i}. {row['username']} ({row['points']})")
                
                self.send(f"🏆 Top 5 Point Leaders: {' | '.join(leaderboard_entries)}")
        finally:
            conn.close()

    def give_points(self, user: str, msg: str) -> None:
        """!give <user> <amount> - Give your points to another user."""
        if self._is_on_cooldown(user, 'give', 30): return

        sender = user.lower()
        parts = msg.split()
        if len(parts) != 3:
            self.send(f"@{user}, usage: !give <username> <amount>")
            return

        try:
            receiver = parts[1].lower().lstrip('@')
            amount = int(parts[2])
        except ValueError:
            self.send(f"@{user}, invalid amount. Please use a number.")
            return

        if sender == receiver:
            self.send(f"@{user}, you can't give points to yourself.")
            return
        if amount <= 0:
            self.send(f"@{user}, you must give at least 1 {POINTS_CURRENCY}.")
            return

        sender_points = self.get_user_points(sender)
        if sender_points < amount:
            self.send(f"@{user}, you don't have enough points to give away! You only have {sender_points} {POINTS_CURRENCY}.")
            return

        # Calculate tax
        tax = int(amount * (POINTS_GIVE_TAX / 100))
        amount_after_tax = amount - tax

        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                # Subtract from sender
                cursor.execute("UPDATE community_points SET points = points - %s WHERE username = %s", (amount, sender))
                # Add to receiver
                cursor.execute(
                    "INSERT INTO community_points (username, points, last_seen) VALUES (%s, %s, NOW()) ON DUPLICATE KEY UPDATE points = points + %s",
                    (receiver, amount_after_tax, amount_after_tax)
                )
            conn.commit()
            self.send(f"@{user} gave {amount_after_tax} {POINTS_CURRENCY} to {receiver}! ({tax} {POINTS_CURRENCY} tax paid)")
        except Exception as e:
            conn.rollback()
            log(f"Error during !give transaction: {e}")
            self.send(f"@{user}, an error occurred during the transfer.")
        finally:
            conn.close()

    def playnext(self, user: str, i: int) -> None:
        """!playnext <number> - Spends a lot of points to inject a song at the top of the playlist."""
        u = user.lower()
        if u not in self.last_results:
            self.send(f"@{user}, please use !search for a song first.")
            return

        current_points = self.get_user_points(u)
        if current_points < POINTS_PLAYNEXT_COST:
            self.send(f"@{user}, you need {POINTS_PLAYNEXT_COST} {POINTS_CURRENCY} to use !playnext. You have {current_points}.")
            return

        rows = self.last_results[u]
        if not 1 <= i <= len(rows):
            self.send(f"@{user}, that's not a valid number from your search results.")
            return

        track = rows[i - 1]
        conn = get_db_connection()
        try:
            with conn.cursor() as cursor:
                # Deduct points
                cursor.execute("UPDATE community_points SET points = points - %s WHERE username = %s", (POINTS_PLAYNEXT_COST, u))
                # Inject into queuelist. 'S' is for Song type.
                cursor.execute("INSERT INTO queuelist (trackID, track_type) VALUES (%s, 'S')", (track['ID'],))
            conn.commit()
            self.send(f"🔥 @{user} spent {POINTS_PLAYNEXT_COST} {POINTS_CURRENCY} to play next: {track['artist']} - {track['title']} 🔥")
            del self.last_results[u]
        except Exception as e:
            conn.rollback()
            log(f"Error during !playnext transaction: {e}")
            self.send(f"@{user}, an error occurred. Your points were not deducted.")
        finally:
            conn.close()


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