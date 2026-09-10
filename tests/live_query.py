"""Tiny CLI to talk to a running Live through LiveBridge (dev tool, not a unit test).

Examples::

    python tests/live_query.py hello
    python tests/live_query.py cmd lom.describe '{"path": "song.tracks[0]"}'
    python tests/live_query.py eval "Live.Clip.Clip.add_new_notes.__doc__"
    python tests/live_query.py eval "[d.class_name for d in song.tracks[0].devices]"
    python tests/live_query.py eval --code "x = song.tempo" "x * 2"

Reads the token from the installed Remote Script config.json (see
``tests/dump_live_api.py:find_token``) or ``LIVEBRIDGE_TOKEN``. Prints the JSON
response as UTF-8 (also into a pipe on Windows, where Python would otherwise use the ANSI code
page and crash on names like "C\u266f" or emoji). Exit code 0 on ``ok``, 1 on an error envelope,
2 when Live is unreachable.
Read-only use is always safe; anything that mutates the Live set should clean up after itself.
"""

import argparse
import json
import os
import socket
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dump_live_api import find_token  # noqa: E402


def call(cmd, args, host="127.0.0.1", port=9880, token=None, timeout=30.0):
    token = find_token() if token is None else token
    req = {"id": uuid.uuid4().hex[:8], "cmd": cmd, "args": args or {}, "timeout": timeout}
    if token:
        req["token"] = token
    with socket.create_connection((host, port), timeout=timeout + 5) as sock:
        sock.sendall((json.dumps(req) + "\n").encode("utf-8"))
        data = b""
        while not data.endswith(b"\n"):
            chunk = sock.recv(1 << 20)
            if not chunk:
                break
            data += chunk
    return json.loads(data.decode("utf-8"))


def utf8_stdout(stream=None):
    """Make ``stream`` (default ``sys.stdout``) write UTF-8, replacing what cannot be encoded.

    On Windows a piped/redirected stdout (Claude Code's shell tool, CI, ``> file``) uses the
    ANSI code page with ``errors="strict"`` before Python 3.15, so any non-cp1252 character in
    a Live name or docstring would raise ``UnicodeEncodeError`` after the command already ran.
    """
    stream = sys.stdout if stream is None else stream
    if hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError, AttributeError):
            pass
    return stream


def main(argv=None):
    utf8_stdout()
    parser = argparse.ArgumentParser(description="Query a running Live via LiveBridge")
    parser.add_argument("--host", default=os.environ.get("LIVEBRIDGE_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("LIVEBRIDGE_PORT", "9880")))
    parser.add_argument("--token", default=None)
    parser.add_argument("--timeout", type=float, default=30.0)
    sub = parser.add_subparsers(dest="mode", required=True)
    sub.add_parser("hello")
    p_cmd = sub.add_parser("cmd")
    p_cmd.add_argument("name")
    p_cmd.add_argument("args", nargs="?", default="{}")
    p_eval = sub.add_parser("eval")
    p_eval.add_argument("--code", default="")
    p_eval.add_argument("expr", nargs="?", default=None)
    ns = parser.parse_args(argv)
    if ns.mode == "hello":
        cmd, args = "system.hello", {}
    elif ns.mode == "cmd":
        cmd, args = ns.name, json.loads(ns.args)
    else:
        cmd, args = "eval.python", {"code": ns.code, "expr": ns.expr}
    try:
        resp = call(cmd, args, ns.host, ns.port, ns.token, ns.timeout)
    except OSError as error:
        print(json.dumps({"ok": False, "error": {"type": "unreachable", "message": str(error)}}))
        return 2
    print(json.dumps(resp, indent=1, ensure_ascii=False))
    return 0 if resp.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
