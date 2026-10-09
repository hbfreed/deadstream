"""
A local control socket for the Time Machine, so other programs (eg an MCP server for Claude) can drive it.

Requests and replies are one line of JSON each, over a Unix socket that only the Time Machine's user can open:

    {"command": "status"}                       -> {"ok": true, "result": {...}}
    {"command": "play_date", "date": "1977-05-08"}
    {"command": "nope"}                         -> {"ok": false, "error": "unknown command nope"}

livemusic registers the commands (see livemusic.control_commands); this module only serves them.
"""
import json
import logging
import os
import socketserver
import threading

logger = logging.getLogger(__name__)

# Override both to let a separate (eg sandboxed) user in: TIMEMACHINE_CONTROL_SOCKET=/run/timemachine/control.sock
# and TIMEMACHINE_CONTROL_GROUP=tmcontrol make the socket rw-rw---- for that group.
SOCKET_PATH = os.getenv("TIMEMACHINE_CONTROL_SOCKET") or os.path.join(os.getenv("HOME", "/tmp"), ".timemachine-control.sock")
SOCKET_GROUP = os.getenv("TIMEMACHINE_CONTROL_GROUP")
MAX_REQUEST = 64 * 1024


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        line = self.rfile.readline(MAX_REQUEST)
        if not line:
            return
        try:
            request = json.loads(line)
            command = request.pop("command")
            func = self.server.commands.get(command)
            if func is None:
                reply = {"ok": False, "error": f"unknown command {command}"}
            else:
                reply = {"ok": True, "result": func(**request)}
        except Exception as e:
            logger.exception(f"control request {line[:200]!r} failed")
            reply = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        self.wfile.write((json.dumps(reply, default=str) + "\n").encode())


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def serve(commands, path=SOCKET_PATH, group=SOCKET_GROUP):
    """Serve {name: function(**args) -> JSON-able result} on a Unix socket, in a background thread"""
    if os.path.exists(path):
        os.unlink(path)
    old_umask = os.umask(0o177)  # the socket is created rw------- : only this user can control the player
    try:
        server = _Server(path, _Handler)
    finally:
        os.umask(old_umask)
    if group:  # ... and the members of this group
        import grp

        os.chown(path, -1, grp.getgrnam(group).gr_gid)
        os.chmod(path, 0o660)
    server.commands = commands
    threading.Thread(target=server.serve_forever, name="control", daemon=True).start()
    logger.info(f"control socket at {path}: {', '.join(sorted(commands))}")
    return server


def request(command, path=SOCKET_PATH, timeout=60, **args):
    """Send one command to a running Time Machine and return its result (raises on an error reply)"""
    import socket

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(path)
        s.sendall((json.dumps({"command": command, **args}) + "\n").encode())
        data = b""
        while not data.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
    reply = json.loads(data)
    if not reply.get("ok"):
        raise RuntimeError(reply.get("error", "control request failed"))
    return reply["result"]
