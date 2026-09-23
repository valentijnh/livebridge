"""Talk to LiveBridge with nothing but the standard library: the wire protocol in 30 lines.

LiveBridge is a TCP server inside Live that reads one JSON object per line and answers with one
JSON object per line (docs/PROTOCOL.md). Anything that can open a socket can drive Live this
way — Node, Max, TouchDesigner, a shell script. This reads host, port and token from the
installer's config file.

    python3 examples/python/raw_protocol.py
"""

from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

config = json.loads((Path.home() / ".livebridge" / "config.json").read_text(encoding="utf-8"))
host, port, token = config.get("host", "127.0.0.1"), config.get("port", 9880), config.get("token")


def request(sock_file, cmd: str, args: dict | None = None, request_id: str = "1") -> dict:
    message = {"id": request_id, "cmd": cmd, "args": args or {}}
    if token:
        message["token"] = token
    sock_file.write((json.dumps(message) + "\n").encode("utf-8"))
    sock_file.flush()
    while True:
        reply = json.loads(sock_file.readline())
        if "ok" in reply:  # lines without "ok" are events, not answers
            return reply


with socket.create_connection((host, port), timeout=10) as sock, sock.makefile("rwb") as f:
    ping = request(f, "system.ping", request_id="ping")
    tempo = request(f, "lom.get", {"path": "song", "prop": "tempo"}, request_id="tempo")

if not ping["ok"]:
    sys.exit(f"LiveBridge said: {ping['error']['type']}: {ping['error']['message']}")
print(json.dumps(ping))
print(json.dumps(tempo))
