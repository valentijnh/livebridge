"""LiveBridge MCP tool modules (auto-discovered) and the helpers every module uses.

**Contract for every file in this package** (`transport.py`, `tracks.py`, `clips.py`, ...)::

    from typing import Any

    from . import bridge_call, drop_none

    def register(mcp, bridge) -> None:
        \"\"\"Register this module's tools on the MCP app.\"\"\"

        @mcp.tool()
        def live_transport_play(start_time: float | None = None) -> Any:
            \"\"\"Start playback...  (full docstring: what, args, returns, gotchas)\"\"\"
            return bridge_call(bridge, "transport.play", drop_none(start_time=start_time))

Rules:
- `register(mcp, bridge)` is the only required module-level name; `livebridge_mcp.server`
  imports every module here with `pkgutil` and calls it. Never edit a registry list.
- Tool names are `live_<area>_<verb>` and come from the function name, so name the function
  exactly what the tool should be called.
- Annotate the return type as `Any`: tools return either the result data or the friendly
  `{"error": ..., "type": ...}` dict from `bridge_call`, never both shapes in one schema.
- Never let an exception escape a tool. `bridge_call` already converts every `BridgeError` into
  the friendly dict; wrap your own validation in `tool_error(...)` for the same shape.
- Keep results compact — Claude pays for every token. Offer `detail`/`limit`/`offset` arguments
  instead of dumping everything.
"""

from __future__ import annotations

import logging
from typing import Any

from ..client import BridgeClient
from ..errors import BridgeError, error_payload

__all__ = ["bridge_call", "drop_none", "tool_error"]

log = logging.getLogger(__name__)


def bridge_call(
    bridge: BridgeClient,
    cmd: str,
    args: dict[str, Any] | None = None,
    timeout: float | None = None,
) -> Any:
    """Run one bridge command for a tool and never raise.

    Args:
        bridge: The shared client handed to `register(mcp, bridge)`.
        cmd: Bridge command, ``namespace.name`` (e.g. ``"tracks.list"``).
        args: Keyword arguments for the handler; use :func:`drop_none` to leave optional
            arguments out entirely.
        timeout: Seconds to wait; defaults to the client's timeout. Raise it for slow work
            (loading a big device, scanning the browser).

    Returns:
        The handler's result on success, or ``{"error": "<one friendly line>", "type": "<kind>",
        "cmd": "<cmd>"}`` when anything went wrong — including Live not running.
    """
    try:
        return bridge.request(cmd, args or {}, timeout=timeout)
    except BridgeError as exc:
        log.debug("bridge error on %s: %s", cmd, exc.to_dict())
        return error_payload(exc, bridge.endpoint)
    except Exception as exc:  # pragma: no cover - defensive: a tool must never raise
        log.exception("unexpected error calling %s", cmd)
        return {
            "error": f"Unexpected LiveBridge client error while running {cmd}: {exc}",
            "type": "internal",
            "cmd": cmd,
        }


def drop_none(**kwargs: Any) -> dict[str, Any]:
    """Return the keyword arguments that are not ``None``.

    Optional tool arguments must not be forwarded as explicit ``None`` — the Remote Script would
    pass ``None`` to the handler instead of using its own default.
    """
    return {key: value for key, value in kwargs.items() if value is not None}


def tool_error(message: str, type: str = "bad_args", cmd: str | None = None) -> dict[str, Any]:
    """Build the same friendly error shape for a problem a tool detects itself (bad argument,
    unsupported combination) without going to Live."""
    payload: dict[str, Any] = {"error": message, "type": type}
    if cmd:
        payload["cmd"] = cmd
    return payload
