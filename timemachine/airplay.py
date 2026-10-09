#!/usr/bin/python3
"""
Grateful Dead Time Machine -- copyright 2021 Steve Eichblatt

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""
# AirPlay through shairport-sync: notice sessions and track info, and end a session when the Time Machine takes over.
# shairport-sync plays AirPlay into the same PulseAudio server as the Time Machine. Its metadata pipe reports when
# a phone starts and stops playing and what it plays; its D-Bus interface can drop the session.
import base64
import logging
import os
import re
import subprocess
import time
from threading import Lock, Thread

logger = logging.getLogger(__name__)

CONFIG_PATH = "/etc/shairport-sync.conf"
PIPE_PATH = "/tmp/shairport-sync-metadata"
ITEM = re.compile(
    r"<item><type>([0-9a-f]{8})</type><code>([0-9a-f]{8})</code><length>(\d+)</length>"
    r"(?:\s*<data encoding=\"base64\">\s*([^<]*)</data>)?\s*</item>",
    re.DOTALL,
)


def available():
    return os.path.exists(CONFIG_PATH)


def parse_items(text):
    """(type, code, data) for each complete item in text, and the text left over after the last complete item"""
    items, end = [], 0
    for m in ITEM.finditer(text):
        type_, code = bytes.fromhex(m.group(1)).decode("ascii", "replace"), bytes.fromhex(m.group(2)).decode("ascii", "replace")
        data = base64.b64decode(m.group(4)) if m.group(4) else b""
        items.append((type_, code, data))
        end = m.end()
    return items, text[end:]


class AirPlay(Thread):
    """Follows shairport-sync's metadata pipe.

    on_start() is called when a phone starts playing, on_stop() when the session ends, and on_track(title, artist,
    album) when the track info changes. The callbacks run in this thread.
    """

    def __init__(self, on_start=None, on_stop=None, on_track=None, pipe_path=PIPE_PATH):
        super().__init__(daemon=True)
        self.on_start = on_start or (lambda: None)
        self.on_stop = on_stop or (lambda: None)
        self.on_track = on_track or (lambda title, artist, album: None)
        self.pipe_path = pipe_path
        self.active = False
        self.title = self.artist = self.album = ""
        self._pending = {}
        self._lock = Lock()

    def handle(self, type_, code, data):
        if type_ == "ssnc" and code in ("pbeg", "prsm"):
            self._set_active(True)
        elif type_ == "ssnc" and code == "pend":
            self._set_active(False)
        elif type_ == "core" and code in ("minm", "asar", "asal"):
            self._pending[code] = data.decode("utf-8", "replace").strip()
        elif type_ == "ssnc" and code == "mden":  # end of a block of track info
            title = self._pending.get("minm", self.title)
            artist = self._pending.get("asar", self.artist)
            album = self._pending.get("asal", self.album)
            self._pending = {}
            if (title, artist, album) != (self.title, self.artist, self.album):
                self.title, self.artist, self.album = title, artist, album
                logger.info(f"AirPlay track: {title} / {artist} / {album}")
                self.on_track(title, artist, album)

    def _set_active(self, active):
        with self._lock:
            if active == self.active:
                return
            self.active = active
        logger.info(f"AirPlay session {'started' if active else 'ended'}")
        if active:
            self.on_start()
        else:
            self.title = self.artist = self.album = ""
            self.on_stop()

    def drop_session(self):
        """End the AirPlay session (the phone sees the speaker disconnect). Returns at once"""
        if not self.active:
            return
        self._set_active(False)
        Thread(target=drop_session, daemon=True).start()

    def run(self):
        while True:
            try:
                if not os.path.exists(self.pipe_path):
                    time.sleep(10)
                    continue
                with open(self.pipe_path, "r", errors="replace") as pipe:  # blocks until shairport-sync opens it
                    buffer = ""
                    while True:
                        chunk = pipe.readline()
                        if chunk == "":  # writer closed the pipe
                            break
                        buffer += chunk
                        items, buffer = parse_items(buffer)
                        for item in items:
                            try:
                                self.handle(*item)
                            except Exception:
                                logger.exception("AirPlay callback failed")
                        if len(buffer) > 1_000_000:
                            buffer = ""
            except Exception:
                logger.exception("Reading the AirPlay metadata pipe failed")
                time.sleep(5)


def drop_session():
    cmd = [
        "dbus-send", "--system", "--print-reply", "--dest=org.gnome.ShairportSync", "/org/gnome/ShairportSync",
        "org.gnome.ShairportSync.DropSession"
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=5)
    except Exception as e:
        logger.warning(f"dbus DropSession failed ({e}), restarting shairport-sync")
        subprocess.run(["sudo", "systemctl", "restart", "shairport-sync"], capture_output=True, timeout=30)
