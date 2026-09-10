"""`BridgeClient` — the TCP JSON-lines client that talks to the LiveBridge Remote Script.

Wire protocol: ``docs/PROTOCOL.md``. One JSON object per line, UTF-8, ``\\n`` terminated, max
16 MiB per line. The client is synchronous and thread-safe (a single re-entrant lock serialises
requests), connects lazily on the first request and reconnects once automatically when the socket
turns out to be dead (Live restarted, control surface reloaded, laptop slept).

Typical use::

    bridge = BridgeClient(host="127.0.0.1", port=9880, token=None, timeout=10.0)
    tracks = bridge.request("tracks.list", {"detail": "summary"})

Every failure raises :class:`livebridge_mcp.errors.BridgeError`; MCP tools turn that into one
friendly line with :func:`livebridge_mcp.errors.friendly_error`.
"""

from __future__ import annotations

import errno
import ipaddress
import json
import logging
import socket
import sys
import threading
import uuid
from typing import Any

from .errors import DROPPED_AFTER_ACCEPT, MACOS_LOCAL_NETWORK_HINT, BridgeError

__all__ = [
    "BridgeClient",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "DEFAULT_TIMEOUT",
    "MAX_LINE",
    "PROTOCOL_VERSION",
]

log = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9880
DEFAULT_TIMEOUT = 10.0
#: Hard protocol limit for one JSON line, in both directions (PROTOCOL.md "Framing rules").
MAX_LINE = 16 * 1024 * 1024
PROTOCOL_VERSION = 1

#: The socket waits a little longer than the command timeout so the Remote Script's own
#: `timeout` error envelope wins over a local socket timeout when both are close.
_SOCKET_GRACE = 0.5
#: Timeout used for the connect() call itself (a refused/filtered port must fail fast).
_CONNECT_TIMEOUT = 5.0

_DROPPED = (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, EOFError)


def _is_loopback(host: str) -> bool:
    if host.strip().lower() in ("localhost", "localhost."):
        return True
    try:
        return ipaddress.ip_address(host.strip().strip("[]")).is_loopback
    except ValueError:
        return False


def _needs_local_network_hint(exc: OSError, host: str, platform: str | None = None) -> bool:
    """True for "No route to host" to a LAN host on macOS — the Local Network permission symptom.

    macOS 15+ answers connects of apps without Local Network access with EHOSTUNREACH, which
    otherwise reads like a network fault. `platform` defaults to ``sys.platform``.
    """
    name = sys.platform if platform is None else platform
    return (name == "darwin" and getattr(exc, "errno", None) == errno.EHOSTUNREACH
            and not _is_loopback(host))


class BridgeClient:
    """Reconnecting TCP client for one LiveBridge Remote Script instance.

    Args:
        host: Address Live listens on (``127.0.0.1`` locally, the machine's LAN IP remotely).
        port: TCP port of the Remote Script (default 9880).
        token: Shared secret; required whenever the Remote Script has one configured
            (always in LAN mode). Empty string and ``None`` both mean "no token".
        timeout: Default seconds to wait for a command result (protocol max is 120).

    Thread-safety: all public methods may be called from any thread; requests are serialised.
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        token: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.host = host or DEFAULT_HOST
        self.port = int(port or DEFAULT_PORT)
        self.token = token or None
        self.timeout = float(timeout or DEFAULT_TIMEOUT)
        #: Per-instance copy of the framing cap so tests can shrink it.
        self.max_line = MAX_LINE
        self._sock: socket.socket | None = None
        self._buf = bytearray()
        self._lock = threading.RLock()
        self._hello: dict[str, Any] | None = None
        self._connects = 0

    # ------------------------------------------------------------------ config

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "BridgeClient":
        """Build a client from a config dict as produced by :func:`livebridge_mcp.config.load_config`."""
        return cls(
            host=config.get("host") or DEFAULT_HOST,
            port=int(config.get("port") or DEFAULT_PORT),
            token=config.get("token") or None,
            timeout=float(config.get("timeout") or DEFAULT_TIMEOUT),
        )

    @property
    def endpoint(self) -> str:
        """``"host:port"`` — used in every user-facing error message."""
        return f"{self.host}:{self.port}"

    @property
    def connects(self) -> int:
        """How many TCP connections this client has opened (test/diagnostics helper)."""
        return self._connects

    @property
    def connected(self) -> bool:
        """True when a socket is currently open (says nothing about Live being responsive)."""
        return self._sock is not None

    def switch(self, host: str, port: int | None = None, token: str | None = None) -> None:
        """Point the client at another Live instance, dropping the current connection.

        Args:
            host: New host.
            port: New port; keeps the current one when omitted.
            token: New token; pass ``""`` to clear it, omit to keep the current one.

        The cached ``system.hello`` is discarded; the next request connects lazily.
        """
        with self._lock:
            self.close()
            self.host = host or self.host
            if port is not None:
                self.port = int(port)
            if token is not None:
                self.token = token or None
            self._hello = None

    def describe(self) -> dict[str, Any]:
        """Compact snapshot of the client's configuration (no network traffic)."""
        return {
            "host": self.host,
            "port": self.port,
            "endpoint": self.endpoint,
            "token_set": bool(self.token),
            "timeout": self.timeout,
            "connected": self.connected,
        }

    # ------------------------------------------------------------- connection

    def connect(self) -> None:
        """Open the TCP connection if it is not open yet (raises `BridgeError('connection')`)."""
        with self._lock:
            self._ensure_socket()

    def close(self) -> None:
        """Close the socket and drop any half-read buffer. Safe to call repeatedly."""
        with self._lock:
            sock, self._sock = self._sock, None
            self._buf = bytearray()
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    sock.close()
                except OSError:
                    pass

    def _ensure_socket(self) -> socket.socket:
        if self._sock is not None:
            return self._sock
        try:
            sock = socket.create_connection((self.host, self.port), timeout=_CONNECT_TIMEOUT)
        except OSError as exc:
            message = f"cannot connect to {self.endpoint}: {exc}"
            if _needs_local_network_hint(exc, self.host):
                message += f" — {MACOS_LOCAL_NETWORK_HINT}"
            raise BridgeError("connection", message, endpoint=self.endpoint) from exc
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:  # pragma: no cover - platform dependent
            pass
        self._sock = sock
        self._buf = bytearray()
        self._connects += 1
        log.debug("connected to LiveBridge at %s", self.endpoint)
        return sock

    # ----------------------------------------------------------------- request

    def request(
        self,
        cmd: str,
        args: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Send one command to Live and return its result.

        Args:
            cmd: ``namespace.name``, e.g. ``"transport.play"`` or ``"lom.get"``.
            args: Keyword arguments for the handler. ``None`` means ``{}``. Keys with value
                ``None`` are sent as-is (the Remote Script treats them as explicit ``None``),
                so drop optional args you do not want to pass.
            timeout: Seconds to wait for this command; defaults to the client timeout.
                The protocol caps it at 120 s.

        Returns:
            The handler's ``result`` (dict/list/str/int/float/bool/None).

        Raises:
            BridgeError: on socket failure (`connection`), local timeout (`timeout`),
                unreadable data (`protocol`) or any error envelope from the Remote Script
                (`auth`, `bad_args`, `not_found`, `unsupported`, `internal`, ...).

        Gotchas:
            - An error envelope with ``"id": null`` (the server's "too many clients (max N)"
              rejection, or ``bad_request`` for an unparseable line) is raised for the request in
              flight — as `invalid_state` / `bad_request`, not as a connection error — and the
              socket is closed.
            - Reconnects once automatically when an established socket turns out to be dead;
              a command that was already delivered is retried, so it can run twice. Only
              genuinely dropped sockets trigger this, never a timeout.
            - On timeout the socket is closed (the late answer would desynchronise the stream)
              and the command may still complete inside Live.
        """
        if not cmd or not isinstance(cmd, str):
            raise BridgeError("bad_request", "cmd must be a non-empty string", cmd=str(cmd))
        wait = float(timeout if timeout is not None else self.timeout)
        wait = max(0.05, min(wait, 120.0))
        req_id = uuid.uuid4().hex[:8]
        payload: dict[str, Any] = {"id": req_id, "cmd": cmd, "v": PROTOCOL_VERSION, "timeout": wait}
        if args:
            payload["args"] = args
        else:
            payload["args"] = {}
        if self.token:
            payload["token"] = self.token

        try:
            line = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8") + b"\n"
        except (TypeError, ValueError) as exc:
            raise BridgeError(
                "bad_request", f"arguments are not JSON-serialisable: {exc}", cmd=cmd,
                endpoint=self.endpoint,
            ) from exc
        if len(line) > self.max_line:
            raise BridgeError(
                "bad_request",
                f"request is {len(line)} bytes, over the {self.max_line} byte line limit; "
                "split the work into smaller calls",
                cmd=cmd,
                endpoint=self.endpoint,
            )

        with self._lock:
            last: BridgeError | None = None
            for attempt in (0, 1):
                reused = self._sock is not None
                opened = False
                try:
                    sock = self._ensure_socket()
                    opened = True
                    sock.settimeout(wait + _SOCKET_GRACE)
                    try:
                        sock.sendall(line)
                    except socket.timeout as exc:
                        raise BridgeError(
                            "timeout", f"could not send {cmd} to {self.endpoint} within {wait:g}s",
                            cmd=cmd, endpoint=self.endpoint, timeout=wait,
                        ) from exc
                    except OSError as exc:
                        raise BridgeError(
                            "connection", f"sending {cmd} to {self.endpoint} failed: {exc}",
                            cmd=cmd, endpoint=self.endpoint,
                        ) from exc
                    response = self._read_response(sock, req_id, cmd, wait)
                except BridgeError as exc:
                    if exc.type == "connection" and attempt == 0 and reused:
                        # An established socket died between requests: reconnect and retry once.
                        log.debug("LiveBridge socket dropped, reconnecting (%s)", exc.message)
                        self.close()
                        last = exc
                        continue
                    if exc.type in ("connection", "protocol", "timeout") or exc.connection_level:
                        # the stream is out of sync, or the server is closing this socket
                        self.close()
                    if exc.type == "connection" and opened and not reused:
                        # Live accepted a brand-new connection and dropped it before answering:
                        # something is listening, so "is Live running?" would be misleading.
                        raise BridgeError(
                            "connection",
                            f"{self.endpoint} {DROPPED_AFTER_ACCEPT} {cmd} ({exc.message})",
                            cmd=cmd, endpoint=self.endpoint,
                        ) from exc
                    raise
                except OSError as exc:  # pragma: no cover - defensive
                    self.close()
                    raise BridgeError(
                        "connection", f"socket error on {self.endpoint}: {exc}", cmd=cmd,
                        endpoint=self.endpoint,
                    ) from exc
                return response
            # Unreachable in practice: the loop either returns or raises.
            raise last or BridgeError("connection", "request failed", cmd=cmd, endpoint=self.endpoint)

    def _read_response(self, sock: socket.socket, req_id: str, cmd: str, wait: float) -> Any:
        """Read lines until the response with our id shows up; skip events/foreign ids."""
        while True:
            line = self._read_line(sock, cmd, wait)
            try:
                msg = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise BridgeError(
                    "protocol", f"unreadable line from {self.endpoint}: {exc}", cmd=cmd,
                    endpoint=self.endpoint,
                ) from exc
            if not isinstance(msg, dict):
                raise BridgeError(
                    "protocol", f"expected a JSON object, got {type(msg).__name__}", cmd=cmd,
                    endpoint=self.endpoint,
                )
            msg_id = msg.get("id")
            if msg_id is None and msg.get("ok") is False and isinstance(msg.get("error"), dict):
                # An error the server could not tie to a request id: the connection-level
                # rejection "too many clients (max N)" (sent right before the server closes the
                # socket) or a `bad_request` for a line it could not parse. Requests are
                # serialised, so it concerns the request in flight — report it instead of
                # skipping it like an event and then failing with a misleading connection error.
                raise BridgeError.from_envelope(msg["error"], cmd=cmd, endpoint=self.endpoint,
                                                connection_level=True)
            if msg_id != req_id:
                # Unsolicited event or a stale answer: ignore it (PROTOCOL.md "Events").
                log.debug("ignoring line without matching id (%r)", msg_id)
                continue
            if msg.get("ok"):
                return msg.get("result")
            error = msg.get("error")
            raise BridgeError.from_envelope(
                error if isinstance(error, dict) else {"message": str(error)},
                cmd=cmd,
                endpoint=self.endpoint,
            )

    def _too_long(self, size: int, cmd: str) -> BridgeError:
        return BridgeError(
            "protocol",
            f"answer of {size} bytes exceeds the {self.max_line} byte line limit; ask the handler "
            "to page the result (offset/limit)",
            cmd=cmd,
            endpoint=self.endpoint,
        )

    def _read_line(self, sock: socket.socket, cmd: str, wait: float) -> bytes:
        """Read one ``\\n`` terminated line from the socket, honouring the 16 MiB cap."""
        while True:
            nl = self._buf.find(b"\n")
            if nl >= 0:
                line = bytes(self._buf[:nl])
                del self._buf[: nl + 1]
                if len(line) > self.max_line:
                    self._buf = bytearray()
                    raise self._too_long(len(line), cmd)
                return line
            if len(self._buf) > self.max_line:
                size = len(self._buf)
                self._buf = bytearray()
                raise self._too_long(size, cmd)
            try:
                chunk = sock.recv(65536)
            except socket.timeout as exc:
                raise BridgeError(
                    "timeout",
                    f"no answer from {self.endpoint} within {wait:g}s",
                    cmd=cmd,
                    endpoint=self.endpoint,
                    timeout=wait,
                ) from exc
            except _DROPPED as exc:
                raise BridgeError(
                    "connection", f"connection to {self.endpoint} was dropped: {exc}", cmd=cmd,
                    endpoint=self.endpoint,
                ) from exc
            except OSError as exc:
                raise BridgeError(
                    "connection", f"socket error on {self.endpoint}: {exc}", cmd=cmd,
                    endpoint=self.endpoint,
                ) from exc
            if not chunk:
                raise BridgeError(
                    "connection",
                    f"connection to {self.endpoint} was closed by Live",
                    cmd=cmd,
                    endpoint=self.endpoint,
                )
            self._buf.extend(chunk)

    # ------------------------------------------------------------- convenience

    def hello(self, refresh: bool = False) -> dict[str, Any]:
        """Return (and cache) ``system.hello``: versions, platform, machine name, allow_eval.

        Args:
            refresh: Ignore the cache and ask Live again.

        The cache is cleared by :meth:`switch`; it is not cleared by a reconnect, because a
        reconnect goes to the same Live instance.
        """
        with self._lock:
            if self._hello is None or refresh:
                result = self.request("system.hello", timeout=min(self.timeout, 10.0))
                self._hello = result if isinstance(result, dict) else {"raw": result}
            return dict(self._hello)

    def ping(self, timeout: float = 5.0) -> Any:
        """Send ``system.ping`` and return its result (``{"pong": true, "t": <song time>}``)."""
        return self.request("system.ping", timeout=timeout)

    def is_alive(self, timeout: float = 3.0) -> bool:
        """True when Live answers a ping right now. Never raises."""
        try:
            self.ping(timeout=timeout)
            return True
        except BridgeError:
            return False
        except Exception:  # pragma: no cover - defensive: is_alive must never raise
            log.exception("unexpected error while pinging %s", self.endpoint)
            return False

    # ------------------------------------------------------------ context mgr

    def __enter__(self) -> "BridgeClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"BridgeClient({self.endpoint}, token={'set' if self.token else 'none'})"
