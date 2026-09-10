"""In-process TCP server that speaks the LiveBridge protocol (``docs/PROTOCOL.md``).

Used by the MCP tests so tools and the bridge client run against a real socket, real JSON-lines
framing and real error envelopes — without Ableton Live.

Usage::

    from fake_bridge import FakeBridge

    with FakeBridge(token="s3cret") as fake:
        client = BridgeClient(host=fake.host, port=fake.port, token="s3cret")
        client.request("system.ping")
        fake.requests[-1]["cmd"]          # -> "system.ping"

Scripting the server:

- ``fake.set_error("tracks.list", "not_found", "no such track")`` — next call to that command
  returns an error envelope (``once=False`` to keep returning it).
- ``fake.set_delay("system.ping", 1.0)`` — answer that command 1 s late (timeout tests).
- ``fake.drop_next_request()`` — close the connection instead of answering (reconnect tests).
- ``fake.close_connections()`` — drop all open sockets, as when Live reloads the script.
- ``fake.set_result("lom.get", {...})`` — canned result for one command.
- ``fake.emit_event_before_response = True`` — send an unsolicited event line first.
- ``FakeBridge(max_clients=1)`` — connections beyond the limit get the Remote Script's
  ``{"id": null, "ok": false, "error": {"type": "invalid_state", "message": "too many clients
  (max 1)"}}`` line and are closed; ``reject_mode="drain"`` (default) half-closes and drains the
  client's request first so the line always arrives, ``"abrupt"`` closes at once like a server
  that lets the kernel reset the connection.

Everything not scripted is answered generically: ``system.*`` is implemented, ``lom.*`` and
``eval.*`` echo their arguments in a canned shape, and any other command comes back as
``unknown_command``.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from typing import Any

MAX_LINE = 16 * 1024 * 1024

HELLO: dict[str, Any] = {
    "name": "LiveBridge",
    "version": "0.1.0",
    "protocol": 1,
    "live": {"major": 12, "minor": 4, "bugfix": 5, "edition": "Suite"},
    "python": "3.11.8",
    "platform": "FakeOS",
    "machine": "FakeLive",
    "allow_eval": True,
}

COMMANDS: list[dict[str, Any]] = [
    {"cmd": "system.hello", "doc": "Handshake", "params": [], "mutating": False},
    {"cmd": "system.ping", "doc": "Ping", "params": [], "mutating": False},
    {"cmd": "system.commands", "doc": "Command catalogue",
     "params": [{"name": "namespace", "default": None}], "mutating": False},
    {"cmd": "system.log", "doc": "Write to Log.txt", "params": [{"name": "message"}], "mutating": False},
    {"cmd": "lom.get", "doc": "Read a LOM property",
     "params": [{"name": "path"}, {"name": "prop", "default": None}], "mutating": False},
    {"cmd": "lom.set", "doc": "Write a LOM property",
     "params": [{"name": "path"}, {"name": "prop"}, {"name": "value"}], "mutating": True},
    {"cmd": "lom.call", "doc": "Call a LOM method",
     "params": [{"name": "path"}, {"name": "method"}, {"name": "args", "default": []}], "mutating": True},
    {"cmd": "lom.describe", "doc": "Describe a LOM object", "params": [{"name": "path"}], "mutating": False},
    {"cmd": "lom.children", "doc": "List children",
     "params": [{"name": "path"}, {"name": "detail", "default": "minimal"},
                {"name": "offset", "default": 0}, {"name": "limit", "default": 200}],
     "mutating": False},
    {"cmd": "eval.python", "doc": "Run Python inside Live",
     "params": [{"name": "code", "default": ""}, {"name": "expr", "default": None},
                {"name": "detail", "default": "summary"}, {"name": "reset", "default": False}],
     "mutating": True},
    {"cmd": "tracks.list", "doc": "List tracks",
     "params": [{"name": "detail", "default": "summary"}], "mutating": False},
]


class FakeBridge:
    """A minimal LiveBridge-compatible TCP server for tests.

    Args:
        token: Token this server requires; ``None`` accepts requests without one.
        host: Interface to bind (default loopback).
        name: Machine name reported in `system.hello`.
        song_time: Value returned as `t` by `system.ping`.
    """

    def __init__(
        self,
        token: str | None = None,
        host: str = "127.0.0.1",
        name: str = "FakeLive",
        song_time: float = 8.0,
        max_clients: int | None = None,
        reject_mode: str = "drain",
    ) -> None:
        self.token = token
        self.name = name
        self.song_time = song_time
        self.max_clients = max_clients
        self.reject_mode = reject_mode
        self.requests: list[dict[str, Any]] = []
        self.connections = 0
        self.rejected = 0
        self.emit_event_before_response = False

        self._errors: dict[str, list[tuple[dict[str, Any], bool]]] = {}
        self._delays: dict[str, float] = {}
        self._results: dict[str, Any] = {}
        self._drop_next = 0
        self._lock = threading.Lock()
        self._clients: list[socket.socket] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, 0))
        self._sock.listen(8)
        self._sock.settimeout(0.2)
        self.host, self.port = self._sock.getsockname()[:2]

    # ------------------------------------------------------------- lifecycle

    def start(self) -> "FakeBridge":
        """Start the accept loop on a daemon thread. Returns self."""
        if self._thread is None:
            self._thread = threading.Thread(target=self._serve, name="fake-bridge", daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        """Stop the server and close every socket. Safe to call twice."""
        self._stop.set()
        self.close_connections()
        try:
            self._sock.close()
        except OSError:
            pass
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=2.0)

    def __enter__(self) -> "FakeBridge":
        return self.start()

    def __exit__(self, *exc_info: Any) -> None:
        self.stop()

    @property
    def endpoint(self) -> str:
        return f"{self.host}:{self.port}"

    # -------------------------------------------------------------- scripting

    def set_error(self, cmd: str, type: str, message: str, once: bool = True,
                  traceback: str | None = None) -> None:
        """Answer `cmd` with an error envelope (once, or until cleared when ``once=False``)."""
        env: dict[str, Any] = {"type": type, "message": message, "cmd": cmd}
        if traceback:
            env["traceback"] = traceback
        with self._lock:
            self._errors.setdefault(cmd, []).append((env, once))

    def set_delay(self, cmd: str, seconds: float) -> None:
        """Wait `seconds` before answering `cmd` (use for timeout tests)."""
        with self._lock:
            self._delays[cmd] = float(seconds)

    def set_result(self, cmd: str, result: Any) -> None:
        """Return a canned result for `cmd` instead of the built-in echo."""
        with self._lock:
            self._results[cmd] = result

    def drop_next_request(self, count: int = 1) -> None:
        """Close the connection instead of answering the next `count` requests."""
        with self._lock:
            self._drop_next += int(count)

    def close_connections(self) -> None:
        """Drop every open client connection (as when Live reloads the Remote Script)."""
        with self._lock:
            clients, self._clients = self._clients, []
        for client in clients:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                client.close()
            except OSError:
                pass

    def reset(self) -> None:
        """Forget recorded requests and all scripted behaviour."""
        with self._lock:
            self.requests.clear()
            self._errors.clear()
            self._delays.clear()
            self._results.clear()
            self._drop_next = 0
            self.emit_event_before_response = False

    # ----------------------------------------------------------------- server

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                client, _addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with self._lock:
                full = self.max_clients is not None and len(self._clients) >= self.max_clients
                if full:
                    self.rejected += 1
                else:
                    self.connections += 1
                    self._clients.append(client)
            if full:
                self._reject(client)
                continue
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _reject(self, client: socket.socket) -> None:
        """Refuse a connection over `max_clients` the way the Remote Script does."""
        line = json.dumps({"id": None, "ok": False,
                           "error": {"type": "invalid_state",
                                     "message": f"too many clients (max {self.max_clients})"}})
        try:
            client.sendall(line.encode("utf-8") + b"\n")
        except OSError:
            pass
        if self.reject_mode == "abrupt":
            try:
                client.close()
            except OSError:
                pass
            return

        def drain() -> None:
            try:
                client.shutdown(socket.SHUT_WR)
                client.settimeout(0.5)
                deadline = time.monotonic() + 0.5
                while time.monotonic() < deadline and client.recv(65536):
                    pass
            except OSError:
                pass
            finally:
                try:
                    client.close()
                except OSError:
                    pass

        threading.Thread(target=drain, name="fake-bridge-reject", daemon=True).start()

    def _handle(self, client: socket.socket) -> None:
        client.settimeout(0.5)
        buf = bytearray()
        try:
            while not self._stop.is_set():
                nl = buf.find(b"\n")
                if nl < 0:
                    if len(buf) > MAX_LINE:
                        return
                    try:
                        chunk = client.recv(65536)
                    except socket.timeout:
                        continue
                    except OSError:
                        return
                    if not chunk:
                        return
                    buf.extend(chunk)
                    continue
                line = bytes(buf[:nl])
                del buf[: nl + 1]
                if not line.strip():
                    continue
                response = self._respond(line, client)
                if response is None:  # dropped on purpose
                    return
                try:
                    client.sendall(json.dumps(response).encode("utf-8") + b"\n")
                except OSError:
                    return
        finally:
            with self._lock:
                if client in self._clients:
                    self._clients.remove(client)
            try:
                client.close()
            except OSError:
                pass

    def _respond(self, line: bytes, client: socket.socket) -> dict[str, Any] | None:
        started = time.monotonic()
        try:
            request = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            return {"id": None, "ok": False,
                    "error": {"type": "bad_request", "message": f"malformed JSON: {exc}"}}
        if not isinstance(request, dict):
            return {"id": None, "ok": False,
                    "error": {"type": "bad_request", "message": "expected a JSON object"}}

        req_id = request.get("id")
        cmd = request.get("cmd")
        args = request.get("args") or {}
        with self._lock:
            self.requests.append(request)
            if self._drop_next > 0:
                self._drop_next -= 1
                drop = True
            else:
                drop = False
            delay = self._delays.get(cmd, 0.0)
            errors = self._errors.get(cmd) or []
            error_env: dict[str, Any] | None = None
            if errors:
                env, once = errors[0]
                error_env = dict(env)
                if once:
                    errors.pop(0)
            canned = self._results.get(cmd, ...) if cmd in self._results else ...

        if drop:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                client.close()
            except OSError:
                pass
            return None

        if delay:
            time.sleep(delay)

        if not isinstance(req_id, str) or not isinstance(cmd, str):
            return {"id": req_id, "ok": False,
                    "error": {"type": "bad_request", "message": "id and cmd are required"}}

        if self.token and request.get("token") != self.token:
            return {"id": req_id, "ok": False,
                    "error": {"type": "auth", "message": "bad or missing token", "cmd": cmd}}

        if error_env is not None:
            return {"id": req_id, "ok": False, "error": error_env}

        if self.emit_event_before_response:
            try:
                client.sendall(
                    json.dumps({"event": "song.is_playing", "data": {"value": True}}).encode("utf-8")
                    + b"\n"
                )
            except OSError:
                return None

        if canned is not ...:
            result: Any = canned
        else:
            try:
                result = self._dispatch(cmd, args)
            except KeyError as exc:
                return {"id": req_id, "ok": False,
                        "error": {"type": "unknown_command", "message": str(exc), "cmd": cmd}}
            except ValueError as exc:
                return {"id": req_id, "ok": False,
                        "error": {"type": "bad_args", "message": str(exc), "cmd": cmd}}

        return {"id": req_id, "ok": True, "result": result,
                "ms": round((time.monotonic() - started) * 1000, 2)}

    def _dispatch(self, cmd: str, args: dict[str, Any]) -> Any:
        """Canned answers. `lom.*` and `eval.*` echo their arguments so tests can assert them."""
        if cmd == "system.hello":
            hello = dict(HELLO)
            hello["machine"] = self.name
            return hello
        if cmd == "system.ping":
            return {"pong": True, "t": self.song_time}
        if cmd == "system.commands":
            # Same shape as remote_script/LiveBridge/handlers/system.py.
            namespace = args.get("namespace")
            commands = [dict(c) for c in COMMANDS
                        if not namespace or c["cmd"].split(".", 1)[0] == namespace]
            if args.get("include_doc") is False:
                for entry in commands:
                    entry.pop("doc", None)
            return {"count": len(commands),
                    "namespaces": sorted({c["cmd"].split(".", 1)[0] for c in COMMANDS}),
                    "commands": commands}
        if cmd == "system.log":
            message = args.get("message")
            if not isinstance(message, str) or not message:
                raise ValueError("message must be a non-empty string")
            return {"ok": True, "logged": message}
        if cmd == "lom.get":
            path = self._need(args, "path")
            prop = args.get("prop")
            if prop:
                return {"path": path, "prop": prop, "value": f"value-of:{prop}"}
            return {"path": path, "type": "Track", "name": f"summary-of:{path}"}
        if cmd == "lom.set":
            path = self._need(args, "path")
            prop = self._need(args, "prop")
            return {"path": path, "prop": prop, "value": args.get("value")}
        if cmd == "lom.call":
            path = self._need(args, "path")
            method = self._need(args, "method")
            return {"path": path, "method": method, "args": args.get("args", []), "returned": None}
        if cmd == "lom.describe":
            path = self._need(args, "path")
            return {
                "type": "Track",
                "path": path,
                "properties": [{"name": "name", "value": "1-MIDI", "type": "str", "writable": True}],
                "methods": ["stop_all_clips"],
                "children": [{"name": "devices", "count": 2}, {"name": "clip_slots", "count": 8}],
            }
        if cmd == "lom.children":
            path = self._need(args, "path")
            detail = args.get("detail", "summary")
            # Same shape as remote_script/LiveBridge/handlers/lom.py (collection path).
            items = [
                {"path": f"{path}[0]", "index": 0, "name": "child-0", "detail": detail},
                {"path": f"{path}[1]", "index": 1, "name": "child-1", "detail": detail},
            ]
            return {"kind": "items", "path": path, "total": len(items), "offset": 0,
                    "count": len(items), "items": items}
        if cmd == "eval.python":
            code = args.get("code", "")
            if not code and not args.get("expr"):
                raise ValueError("pass 'code', 'expr' or both")
            return {"result": args.get("expr") and f"eval:{args['expr']}", "stdout": f"ran:{code}"}
        if cmd == "tracks.list":
            return [{"path": "song.tracks[0]", "index": 0, "name": "1-MIDI"}]
        raise KeyError(f"unknown command '{cmd}'")

    @staticmethod
    def _need(args: dict[str, Any], key: str) -> Any:
        if key not in args or args[key] in (None, ""):
            raise ValueError(f"missing required argument '{key}'")
        return args[key]


def free_tcp_port(host: str = "127.0.0.1") -> int:
    """An ephemeral TCP port that is closed again — connecting to it is refused.

    Used to test "Live is not running" behaviour. There is a small race (the port could be reused
    by something else), which is acceptable in tests.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def free_udp_port(host: str = "127.0.0.1") -> int:
    """An ephemeral UDP port that is free again (for discovery tests)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()
