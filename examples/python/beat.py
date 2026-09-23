"""Build a 4-bar house beat in Ableton Live from a Python script, without Claude.

Every request is a LiveBridge bridge command (the list: `live_commands`, or the "Bridge
commands" table in docs/TOOLS.md) and one undo step in Live.

    ~/.livebridge/venv/bin/python examples/python/beat.py            # build it
    ~/.livebridge/venv/bin/python examples/python/beat.py --play     # build it and play it
"""

from __future__ import annotations

import argparse
import sys

from livebridge_mcp.client import BridgeClient
from livebridge_mcp.config import load_config
from livebridge_mcp.errors import BridgeError

PATTERN = {
    "kick": "x...x...x...x...",
    "clap": "....X.......X...",
    "ohh":  "..x...x...x...x.",
    "hat":  ".o.o.o.o.o.o.o.o",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tempo", type=float, default=124.0)
    parser.add_argument("--kit", default="909 Core Kit", help="drum kit in Live's browser")
    parser.add_argument("--track", default="Script Drums", help="name of the new track")
    parser.add_argument("--play", action="store_true", help="fire the clip afterwards")
    args = parser.parse_args()

    live = BridgeClient.from_config(load_config())
    try:
        live.request("transport.set", {"tempo": args.tempo})
        loaded = live.request("browser.load", {
            "query": args.kit, "category": "drum_kit",
            "new_track": "midi", "track_name": args.track}, timeout=30)
        track = loaded["track"]["path"]
        written = live.request("notes.write_pattern", {
            "track": track, "slot": 0, "pattern": PATTERN, "repeat": 4})
        print(f"{loaded['loaded']['name']} on {args.track}: {written['added']} notes, "
              f"{written['length'] / 4:g} bars")
        for row, pad in written.get("kit", {}).get("rows", {}).items():
            print(f"  {row:<5} -> pad {pad['note']} ({pad.get('pad', '?')})")
        if args.play:
            live.request("clips.fire", {"track": track, "slot": 0})
            print("playing (stop it in Live)")
    except BridgeError as exc:
        print(f"LiveBridge error: {exc}", file=sys.stderr)
        return 1
    finally:
        live.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
