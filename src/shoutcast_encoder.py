import os
import re
import socket
import subprocess
import threading
import sys
import time
import requests

from utils import log
from config import AUDIO_INPUT_DEVICE # Import the global audio input device

class ShoutcastEncoder(threading.Thread):
    """
    Handles audio capture with FFmpeg and streaming to a SHOUTcast v2 server via HTTP PUT.
    """
    def __init__(self, index: int, config: dict, audio_device_name: str):
        super().__init__(daemon=True)
        self.index = index
        self.config = config
        self.audio_device_name = audio_device_name
        self.running = False
        self.status = "Stopped"
        self.color = "gray"
        self.ffmpeg_process = None
        self.session = None
        self.v1_socket = None

    def _update_status(self, status: str, color: str):
        self.status = status
        self.color = color
        log(f"Encoder {self.index + 1} ({self.config['name']}): {status}")

    def _is_shoutcast_v1(self) -> bool:
        """
        Determines if the server is likely Shoutcast v1.
        A simple heuristic: if the mount point is '/' or empty, assume v1.
        Shoutcast v2 and Icecast typically use more descriptive mount points.
        This can be overridden if needed.
        """
        mount = self.config.get('mount', '/').strip()
        # You could add a specific config option like 'protocol = v1' for more explicit control
        return mount in ('', '/')

    def _log_ffmpeg_errors(self):
        """Reads from ffmpeg's stderr and logs it."""
        while self.running and self.ffmpeg_process and self.ffmpeg_process.poll() is None:
            line = self.ffmpeg_process.stderr.readline().decode('utf-8', 'ignore').strip()
            if line:
                log(f"FFmpeg (Encoder {self.index + 1}): {line}")


    def run(self):
        self.running = True
        self._update_status("Starting...", "#f59e0b")

        # 1. Construct the FFmpeg command
        # On Windows, FFmpeg uses dshow and identifies devices by name like "Microphone (Realtek High Definition Audio)"
        ffmpeg_command = [
            'ffmpeg',
            '-f', 'dshow',  # Use DirectShow for audio capture on Windows
            '-i', f'audio={self.audio_device_name}',
            '-acodec', 'libmp3lame',  # Encode to MP3
            '-ar', '44100', # Sample rate
            '-ac', '2', # Stereo
            '-b:a', self.config.get('bitrate', '128k'), # Bitrate
            '-content_type', 'audio/mpeg', # Set content type for the stream
            '-f', 'mp3',             # Output format
            'pipe:1'                 # Output to stdout
        ]

        # 2. Start the FFmpeg subprocess
        try:
            self._update_status("Launching FFmpeg...", "#f59e0b")
            self.ffmpeg_process = subprocess.Popen(
                ffmpeg_command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, # Capture and redirect errors
                creationflags=subprocess.CREATE_NO_WINDOW # Hide FFmpeg window on Windows
            )
            # Start a thread to monitor FFmpeg's stderr
            threading.Thread(target=self._log_ffmpeg_errors, daemon=True).start()

        except Exception as e:
            self._update_status(f"FFmpeg Error: {e}", "#f97373")
            log("FFmpeg failed to start. Ensure ffmpeg.exe is in your system's PATH.")
            self._cleanup()
            return

        # Give FFmpeg a moment to start or fail
        time.sleep(2)
        if self.ffmpeg_process.poll() is not None:
            self._update_status("FFmpeg failed to start", "#f97373")
            log(f"FFmpeg for Encoder {self.index + 1} exited with code {self.ffmpeg_process.poll()}. Check logs for details.")
            self._cleanup()
            return

        # Check which protocol to use
        if self._is_shoutcast_v1():
            self._run_shoutcast_v1()
        else:
            self._run_shoutcast_v2()

        self._cleanup()
        self._update_status("Stopped", "gray")

    def _run_shoutcast_v2(self):
        """Handles streaming to Shoutcast v2 / Icecast servers."""
        # 4. Stream to SHOUTcast server
        stream_url = f"http://{self.config['host']}:{self.config['port']}{self.config['mount']}"
        headers = {
            'Content-Type': 'audio/mpeg',
            'Icy-Name': self.config.get('name', 'Radio420 Stream'),
            'Icy-Genre': 'Variety',
            'Icy-Pub': '1',
        }

        # Use a session for connection persistence
        self.session = requests.Session()
        self.session.headers.update(headers)
        self.session.auth = ('source', self.config['password'])

        try:
            while self.running:
                self._update_status("Connecting...", "#f59e0b")
                # The `put` call will block here and stream data from ffmpeg.
                # When `self.session.close()` is called from `stop()`, this will raise an exception.
                self.session.put(
                    stream_url,
                    data=self.ffmpeg_process.stdout,
                    stream=True
                )
                # If the stream ends (e.g., server disconnects), the loop continues.
                if not self.running: break
                self._update_status("Stream ended. Reconnecting...", "#f59e0b")
                time.sleep(5)

        except requests.exceptions.RequestException as e:
            # This will catch HTTP errors (like 401) and connection errors during the initial connect.
            # It will also catch the error from self.session.close() when stopping.
            if self.running: # Only log as an error if we weren't intentionally stopping.
                status_msg = f"Stream Error: {e}"
                if hasattr(e, 'response') and e.response is not None:
                    status_msg = f"Connect Error: {e.response.status_code} {e.response.reason}"
                    log(f"Server response for {self.config['name']}: {e.response.text.strip()}")
                self._update_status(status_msg, "#f97373")
        finally:
            if self.session:
                self.session.close()

    def _run_shoutcast_v1(self):
        """Handles streaming to legacy Shoutcast v1 servers."""
        self._update_status("Using Shoutcast v1 protocol", "#f59e0b")
        
        while self.running:
            self.v1_socket = None
            try:
                self._update_status("Connecting (v1)...", "#f59e0b")
                self.v1_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.v1_socket.settimeout(10)
                self.v1_socket.connect((self.config['host'], self.config['port']))

                # 1. Send password
                self.v1_socket.sendall(f"{self.config['password']}\r\n".encode())

                # 2. Send ICY headers
                bitrate_kbps = self.config.get('bitrate', '128k').replace('k', '')
                headers = [
                    f"icy-name:{self.config.get('name', 'Radio420 Stream')}",
                    f"icy-genre:Variety",
                    f"icy-pub:1",
                    f"icy-br:{bitrate_kbps}", # Bitrate
                    "\r\n" # End of headers
                ]
                self.v1_socket.sendall("\r\n".join(headers).encode())

                self._update_status("Streaming (v1)", "#22c55e")
                while self.running:
                    chunk = self.ffmpeg_process.stdout.read(4096)
                    if not chunk:
                        break # FFmpeg process ended
                    self.v1_socket.sendall(chunk)

            except (socket.error, socket.timeout, BrokenPipeError) as e:
                # Only log as an error if we weren't intentionally stopping
                if self.running:
                    self._update_status(f"Stream Error (v1): {e}", "#f97373")
            finally:
                if self.v1_socket:
                    self.v1_socket.close()

            if not self.running:
                break
            log(f"Encoder {self.index + 1}: Connection lost. Reconnecting in 5 seconds...")
            time.sleep(5)

    def stop(self):
        self.running = False
        # Closing the session will interrupt the blocking `put` call in _run_shoutcast_v2
        if self.session:
            self.session.close()
        # Closing the socket will interrupt the blocking `read` or `sendall` in _run_shoutcast_v1
        if self.v1_socket:
            self.v1_socket.close()

    def _cleanup(self):
        if self.ffmpeg_process:
            log(f"Terminating FFmpeg process for Encoder {self.index + 1}...")
            self.ffmpeg_process.terminate()
            try:
                self.ffmpeg_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.ffmpeg_process.kill()
            self.ffmpeg_process = None


def get_ffmpeg_dshow_devices() -> list:
    """
    Returns a list of available dshow audio input devices by asking FFmpeg directly.
    This is the most reliable way to get names FFmpeg will understand.
    """
    devices = []
    ffmpeg_path = 'ffmpeg'  # Always rely on the system PATH

    try:
        # Command to list dshow devices
        command = [ffmpeg_path, '-list_devices', 'true', '-f', 'dshow', '-i', 'dummy']
        # Run the command, capturing output
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='ignore',
            creationflags=subprocess.CREATE_NO_WINDOW
        )

        output = result.stderr
        for line in output.splitlines():
            # Check if the line describes an audio device
            if "(audio)" in line:
                # Extract the device name, which is enclosed in quotes.
                match = re.search(r'"([^"]+)"', line)
                if match:
                    # The index here is just for our internal list
                    device_name = match.group(1)
                    devices.append({"index": len(devices), "name": device_name})
    except FileNotFoundError:
        log("CRITICAL: ffmpeg.exe not found. Please ensure it is in your system's PATH.")
        return [] # Return empty list to prevent crash
    except Exception as e:
        log(f"CRITICAL: Error getting audio devices from FFmpeg: {e}")
    return devices