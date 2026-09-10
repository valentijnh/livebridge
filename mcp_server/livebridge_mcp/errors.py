"""Errors of the LiveBridge wire protocol and their human-readable rendering.

`BridgeError` is the single exception type raised by :class:`livebridge_mcp.client.BridgeClient`.
It mirrors the error envelope of ``docs/PROTOCOL.md``::

    {"id": "a1b2", "ok": false,
     "error": {"type": "not_found", "message": "...", "traceback": "...", "cmd": "tracks.get"}}

MCP tools never let a `BridgeError` escape: they render it with :func:`friendly_error` into a
single line of plain English that tells Claude (and the user) what to do next. Tracebacks are
kept on the exception object for logging, but never shown in tool output.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "BridgeError",
    "DROPPED_AFTER_ACCEPT",
    "ERROR_TYPES",
    "MACOS_LOCAL_NETWORK_HINT",
    "friendly_error",
    "error_payload",
]

#: Error `type` values of the protocol plus the three types the client itself produces
#: (`connection`, `timeout`, `protocol`).
ERROR_TYPES: tuple[str, ...] = (
    "auth",
    "bad_request",
    "unknown_command",
    "bad_args",
    "not_found",
    "invalid_state",
    "unsupported",
    "timeout",
    "forbidden",
    "internal",
    "connection",
    "protocol",
)


class BridgeError(Exception):
    """An error returned by (or while talking to) the LiveBridge Remote Script.

    Args:
        type: One of :data:`ERROR_TYPES`. Unknown values are kept as-is.
        message: Technical message from the Remote Script (or from the socket layer).
        cmd: The bridge command that failed, e.g. ``"lom.get"``. Optional.
        traceback: Remote traceback for `internal` errors. Never shown to the model.
        endpoint: ``"host:port"`` of the bridge the request went to. Optional.
        timeout: The timeout (seconds) that was in effect, for `timeout` errors.
        connection_level: True when the server sent the error with ``"id": null`` — not the
            answer to a request but a verdict on the connection (e.g. "too many clients"); the
            client closes the socket after it.
    """

    def __init__(
        self,
        type: str,
        message: str,
        cmd: str | None = None,
        traceback: str | None = None,
        endpoint: str | None = None,
        timeout: float | None = None,
        connection_level: bool = False,
    ) -> None:
        super().__init__(message)
        self.type = type or "internal"
        self.message = message
        self.cmd = cmd
        self.traceback = traceback
        self.endpoint = endpoint
        self.timeout = timeout
        self.connection_level = bool(connection_level)

    def __str__(self) -> str:  # pragma: no cover - trivial
        where = f" [{self.cmd}]" if self.cmd else ""
        return f"{self.type}: {self.message}{where}"

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"BridgeError(type={self.type!r}, message={self.message!r}, cmd={self.cmd!r})"

    @classmethod
    def from_envelope(
        cls,
        envelope: dict[str, Any],
        cmd: str | None = None,
        endpoint: str | None = None,
        connection_level: bool = False,
    ) -> "BridgeError":
        """Build a `BridgeError` from the ``error`` object of a protocol response.

        `connection_level` marks an envelope that arrived with ``"id": null``.
        """
        env = envelope if isinstance(envelope, dict) else {}
        return cls(
            type=str(env.get("type") or "internal"),
            message=str(env.get("message") or "unknown error"),
            cmd=env.get("cmd") or cmd,
            traceback=env.get("traceback"),
            endpoint=endpoint,
            connection_level=connection_level,
        )

    def to_dict(self) -> dict[str, Any]:
        """Compact dict for logging/debugging (includes the remote traceback)."""
        out: dict[str, Any] = {"type": self.type, "message": self.message}
        if self.cmd:
            out["cmd"] = self.cmd
        if self.endpoint:
            out["endpoint"] = self.endpoint
        if self.traceback:
            out["traceback"] = self.traceback
        return out


#: Command prefixes whose ``not_found`` errors are about browser items, folders, files or
#: plug-ins (a fresh set snapshot does not help there).
_BROWSER_PREFIXES: tuple[str, ...] = ("browser.", "samples.", "plugins.")


#: Phrase the client puts in a `connection` error when Live accepted a fresh TCP connection and
#: closed it before answering (client limit reached, or the Remote Script is reloading) — as
#: opposed to nothing listening at all.
DROPPED_AFTER_ACCEPT = "accepted the connection but closed it before answering"

#: Hint the client adds to a `connection` error on macOS when a LAN host answers "No route to
#: host" (EHOSTUNREACH): macOS 15+ blocks apps without Local Network permission exactly like that.
MACOS_LOCAL_NETWORK_HINT = ("macOS 15+: allow Claude Desktop / your terminal under System Settings "
                            "> Privacy & Security > Local Network, then restart it")

#: Substring of the Remote Script's rejection when its `max_clients` limit is reached
#: (``remote_script/LiveBridge/server.py``: ``"too many clients (max N)"``).
_TOO_MANY_CLIENTS = "too many clients"


def _endpoint(err: BridgeError, endpoint: str | None) -> str:
    return endpoint or err.endpoint or "127.0.0.1:9880"


def _namespace(cmd: str | None) -> str:
    """The namespace of a bridge command (``"clips.create"`` -> ``"clips"``); ``"system"`` fallback."""
    if cmd and "." in cmd:
        head = cmd.split(".", 1)[0].strip()
        if head:
            return head
    return "system"


def friendly_error(err: BridgeError, endpoint: str | None = None) -> str:
    """Render a `BridgeError` as one friendly line, with a hint about what to do next.

    Args:
        err: The error raised by the bridge client.
        endpoint: ``"host:port"`` to name in the message; falls back to ``err.endpoint``.

    Returns:
        A single line of text. Never contains a traceback, never ends with a newline.
    """
    where = _endpoint(err, endpoint)
    cmd = err.cmd or "the command"
    msg = (err.message or "").strip()

    if err.type == "connection":
        if DROPPED_AFTER_ACCEPT in msg:
            return (
                f"LiveBridge at {where} accepted the connection but closed it before answering. "
                "Live is running, but it either has the maximum number of LiveBridge clients "
                "connected (close other Claude sessions, or raise \"max_clients\" in the "
                "config.json next to the Remote Script and restart Live) or the control surface "
                "is reloading — wait a few seconds and retry."
            )
        if MACOS_LOCAL_NETWORK_HINT in msg:
            return (
                f"This Mac has no route to Live at {where} (No route to host). "
                f"{MACOS_LOCAL_NETWORK_HINT}. Also check the IP and that both machines are on the "
                "same network."
            )
        return (
            f"Live is not reachable at {where} — is Live running with the LiveBridge control "
            "surface enabled? Run live_discover to find instances on the network."
        )
    if err.type == "timeout":
        secs = f"{err.timeout:g}s" if err.timeout else "the timeout"
        return (
            f"Live did not answer {cmd} within {secs}. Live's main thread is busy (a modal dialog, "
            "a big set loading or a long operation) and the command may still complete inside "
            "Live — check the result before repeating it. Wait a moment and retry; for a "
            f"genuinely long operation run it as live_command_call(cmd=\"{err.cmd or '<command>'}\", "
            "args={...}, timeout=<seconds, max 120>)."
        )
    if err.type == "auth":
        return (
            f"LiveBridge at {where} rejected the token ({msg}). Use live_connect(host, port, "
            "token=...) with the token from config.json next to the LiveBridge Remote Script."
        )
    if err.type == "protocol":
        return (
            f"Got an unreadable answer from LiveBridge at {where} ({msg}). The connection was "
            "reset; retry the command, and check Live's Log.txt if it keeps happening."
        )
    if err.type == "unknown_command":
        return (
            f"LiveBridge does not know the command '{err.cmd or msg}'. Run "
            f"live_commands(namespace=\"{_namespace(err.cmd)}\") to see what this Live installation "
            "supports there (live_commands() lists every namespace) — the Remote Script may be an "
            "older version."
        )
    if err.type == "bad_args":
        lowered = msg.lower()
        if "argument" in lowered or "accepts:" in lowered:
            # a parameter-name problem: point at the authoritative parameter list
            return (f"Wrong arguments for {cmd}: {msg}. Check "
                    f"live_commands(namespace=\"{err.cmd or _namespace(err.cmd)}\") for the exact "
                    "parameters.")
        return f"Wrong arguments for {cmd}: {msg}."
    if err.type == "not_found":
        if (err.cmd or "").startswith(_BROWSER_PREFIXES):
            # missing browser items, folders, files or plug-ins — not a stale set
            return (
                f"Not found: {msg}. Search with live_browser_search (or browse with "
                "live_browser_browse) and check file paths with live_sample_inspect."
            )
        if "[" in msg or "song." in msg or "index" in msg.lower():
            return (
                f"Not found: {msg}. Verify the path with live_lom_children on the parent (paths "
                "look like song.tracks[0].devices[1]) or take a fresh live_set_snapshot — indices "
                "shift when tracks or clips are added or removed."
            )
        return f"Not found: {msg}. Take a fresh live_set_snapshot if the set changed."
    if err.type == "invalid_state":
        if _TOO_MANY_CLIENTS in msg.lower():
            return (
                f"Live at {where} already has the maximum number of LiveBridge clients connected "
                f"({msg}). Close other Claude sessions (each Claude Code session and Claude "
                "Desktop runs its own LiveBridge MCP server), or raise \"max_clients\" in the "
                "config.json next to the LiveBridge Remote Script and restart Live."
            )
        return f"Live refused this right now: {msg}. Check the current state (live_set_snapshot) and adjust."
    if err.type == "unsupported":
        return (
            f"Not supported by this Live version/edition/setup: {msg}. See the message for "
            "alternatives; otherwise the Live API offers no way to do this."
        )
    if err.type == "forbidden":
        lowered = msg.lower()
        if "token" in lowered and "allow_eval" not in lowered:
            # e.g. "eval.python needs a token — this bridge has none ...": the message itself
            # says what to do; the allow_eval advice would send the user the wrong way.
            return f"Refused by LiveBridge: {msg.rstrip('.')}."
        if "allow_eval" in lowered or "disabled" in lowered or not msg:
            return (
                f"Refused by LiveBridge: {msg.rstrip('.') or 'eval.python is disabled'}. Set "
                "\"allow_eval\": true in config.json next to the Remote Script and restart Live "
                "to allow it."
            )
        return f"Refused by LiveBridge: {msg.rstrip('.')}."
    if err.type == "bad_request":
        return f"LiveBridge could not read the request for {cmd}: {msg}."
    if err.type == "internal":
        return (
            f"LiveBridge hit an unexpected error in {cmd}: {msg}. Live is still running; see Log.txt "
            "for the traceback."
        )
    return f"LiveBridge error ({err.type}) in {cmd}: {msg}"


def error_payload(err: BridgeError, endpoint: str | None = None) -> dict[str, Any]:
    """The dict an MCP tool returns instead of raising: ``{"error": <one line>, "type": ...}``.

    Also carries ``cmd`` when known. Tools return this so Claude sees a readable sentence rather
    than an exception, and can branch on ``type`` (e.g. ``"connection"`` → tell the user to start
    Live) without parsing prose.
    """
    payload: dict[str, Any] = {
        "error": friendly_error(err, endpoint),
        "type": err.type,
    }
    if err.cmd:
        payload["cmd"] = err.cmd
    return payload
