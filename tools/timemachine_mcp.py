"""
An MCP server for the Time Machine, so Claude can pick and play shows ("play Cornell '77").

It runs on the Pi next to the Time Machine and talks to it over its control socket (timemachine/control.py).
It only exposes music controls. Claude reaches it over HTTPS (eg Tailscale Funnel) at a secret path:

    https://<host>/<secret>/mcp

Config, ~/.timemachine-mcp.json (chmod 600):  {"secret": "<long random string>", "host": "<public host name>"}
Requirements: mcp>=2 (it brings uvicorn). Setup: docs/Fresh Install.md, "Claude (MCP)".
"""
import argparse
import json
import os
import socket

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

CONFIG_PATH = os.getenv("TIMEMACHINE_MCP_CONFIG") or os.path.join(os.getenv("HOME", ""), ".timemachine-mcp.json")
SOCKET_PATH = os.getenv("TIMEMACHINE_CONTROL_SOCKET") or os.path.join(os.getenv("HOME", ""), ".timemachine-control.sock")

server = MCPServer(
    "Time Machine",
    instructions=(
        "Controls a Grateful Dead Time Machine: a box in Henry's home that plays live concert recordings by date, "
        "from archive.org, official releases, nugs.net and his own files. To play something, find the date "
        "(use your knowledge of famous shows, or find_shows), then play_show. A date can have several recordings "
        "(soundboards, audience tapes, official releases); the first is the preferred one. Official releases are "
        "marked 'complete' or 'partial'."
    ),
)


def tm(command, **args):
    """Send a command to the Time Machine over its control socket. Failures are ToolErrors, so Claude sees why."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(90)  # starting a show waits for the stream to begin
        try:
            s.connect(SOCKET_PATH)
        except OSError:
            raise ToolError("The Time Machine isn't running (or is still starting up)")
        try:
            s.sendall((json.dumps({"command": command, **args}) + "\n").encode())
            data = b""
            while not data.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
        except socket.timeout:
            raise ToolError(f"The Time Machine didn't answer {command} in 90 seconds")
    try:
        reply = json.loads(data)
    except ValueError:
        raise ToolError("The Time Machine gave no answer (it may have restarted)")
    if not reply.get("ok"):
        raise ToolError(reply.get("error", "the Time Machine refused"))
    return reply["result"]


@server.tool()
def now_playing() -> dict:
    """What the Time Machine is doing: date, artist, venue, track, play state, volume (and AirPlay, if a phone is playing)."""
    return tm("status")


@server.tool()
def library() -> dict:
    """The artists and sources the Time Machine has, in its order of preference, and its date range."""
    return tm("library")


@server.tool()
def find_shows(year: int, month: int | None = None, artist: str | None = None, venue: str | None = None) -> list:
    """Dates with recordings in a year (optionally a month), optionally only an artist's (eg "GratefulDead",
    "GooseBand", "Orebolo") or at a venue (substring, eg "Cornell", "Red Rocks")."""
    return tm("shows", year=year, month=month, artist=artist, venue=venue)


@server.tool()
def list_recordings(date: str) -> list:
    """The recordings of a date (YYYY-MM-DD), preferred first, with source and official-release status."""
    return tm("recordings", date=date)


@server.tool()
def play_show(date: str, artist: str | None = None, recording: str | None = None) -> dict:
    """Play a show: turns the Time Machine's dial to the date (YYYY-MM-DD) and plays its preferred recording.
    Optionally the artist (when several played that date), or a recording by index or part of its name
    from list_recordings. Takes over from AirPlay if a phone is playing."""
    return tm("play", date=date, artist=artist, recording=recording)


@server.tool()
def pause() -> dict:
    """Pause playback."""
    return tm("pause")


@server.tool()
def resume() -> dict:
    """Resume playback (or start the show on the dial)."""
    return tm("resume")


@server.tool()
def stop() -> dict:
    """Stop playback."""
    return tm("stop")


@server.tool()
def next_track() -> dict:
    """Skip to the next track."""
    return tm("next_track")


@server.tool()
def previous_track() -> dict:
    """Go back to the previous track (or the start of the show)."""
    return tm("previous_track")


@server.tool()
def random_show() -> dict:
    """Play a random show, like holding the Play button."""
    return tm("random_show")


@server.tool()
def set_volume(level: int) -> int:
    """Set the volume, 0-100 (100 is the Time Machine's normal full volume, which the speakers already limit)."""
    return tm("volume", level=level)


def main():
    parser = argparse.ArgumentParser(description="MCP server for the Time Machine")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--config", default=CONFIG_PATH)
    args = parser.parse_args()
    with open(args.config) as f:
        cfg = json.load(f)
    secret, host = cfg["secret"], cfg["host"]
    if len(secret) < 32:
        raise SystemExit("the secret must be at least 32 characters")
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=[host, f"127.0.0.1:{args.port}"], allowed_origins=[f"https://{host}"]
    )
    app = server.streamable_http_app(
        streamable_http_path=f"/{secret}/mcp", stateless_http=True, json_response=True, transport_security=security
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
