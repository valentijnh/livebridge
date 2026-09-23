"""Connect to Ableton Live through LiveBridge and print what is in the set.

Uses the MCP server's own client and settings (~/.livebridge/config.json and the LIVEBRIDGE_*
environment variables), so it reaches the same Live as `live_status` — also on another computer.

    ~/.livebridge/venv/bin/python examples/python/hello_live.py
"""

from __future__ import annotations

import sys

from livebridge_mcp.client import BridgeClient
from livebridge_mcp.config import load_config
from livebridge_mcp.errors import BridgeError


def main() -> int:
    client = BridgeClient.from_config(load_config())
    try:
        hello = client.request("system.hello")
        transport = client.request("transport.get")
        tracks = client.request("tracks.list", {"detail": "minimal"})
    except BridgeError as exc:
        print(f"Cannot talk to Live at {client.endpoint}: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()

    live = hello["live"]
    print(f"Ableton Live {live['version']} {live['edition']} · LiveBridge {hello['version']}"
          f" · {hello['commands']} commands")
    print(f"{transport['tempo']:g} BPM · {transport['signature']} · "
          f"{'playing' if transport['is_playing'] else 'stopped'}")
    for track in tracks["tracks"]:
        print(f"  {track['path']:<24} {track['type']:<7} {track['name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
