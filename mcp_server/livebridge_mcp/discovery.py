"""UDP discovery: find Ableton Live machines running LiveBridge on the local network.

The Remote Script broadcasts every 2 s to ``255.255.255.255:9881`` (see ``docs/PROTOCOL.md``)::

    {"livebridge": 1, "name": "Valentijn-PC", "host": "192.168.1.20", "port": 9880,
     "live": "12.4.5", "needs_token": true}

:func:`discover` listens on ``0.0.0.0:9881`` for a few seconds and returns the unique instances it
heard. It never raises: firewalls, a port already in use or malformed datagrams simply yield fewer
results (with the reason in ``notes``), because a discovery failure must never break a tool call.
"""

from __future__ import annotations

import json
import logging
import socket
import sys
import time
from typing import Any

__all__ = ["BEACON_PORT", "discover", "parse_beacon", "silence_hint"]

log = logging.getLogger(__name__)

BEACON_PORT = 9881
_MAX_DATAGRAM = 8192


def parse_beacon(data: bytes | str, addr: tuple[str, int] | None = None) -> dict[str, Any] | None:
    """Parse one beacon datagram into an instance dict, or ``None`` when it is not a beacon.

    Args:
        data: Raw datagram payload (JSON, UTF-8).
        addr: ``(ip, port)`` the datagram came from; used as the host when the beacon omits one
            or advertises a non-routable address.

    Returns:
        ``{"name", "host", "port", "live", "needs_token", "source_ip"}`` or ``None``.

    Gotcha: a beacon whose ``host`` is ``0.0.0.0``/``127.0.0.1`` is rewritten to the sender IP —
    Live cannot always tell which of its interfaces you will reach it on.
    """
    try:
        if isinstance(data, bytes):
            data = data.decode("utf-8", "replace")
        payload = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict) or not payload.get("livebridge"):
        return None

    source_ip = addr[0] if addr else None
    host = str(payload.get("host") or "") or None
    if host in (None, "", "0.0.0.0", "127.0.0.1", "::") and source_ip:
        host = source_ip
    if not host:
        return None
    try:
        port = int(payload.get("port") or 9880)
    except (TypeError, ValueError):
        port = 9880

    instance: dict[str, Any] = {
        "name": str(payload.get("name") or host),
        "host": host,
        "port": port,
        "live": payload.get("live"),
        "needs_token": bool(payload.get("needs_token")),
    }
    if source_ip and source_ip != host:
        instance["source_ip"] = source_ip
    return instance


def discover(
    seconds: float = 3.0,
    port: int = BEACON_PORT,
    stop_after: int | None = None,
) -> dict[str, Any]:
    """Listen for LiveBridge beacons and return the instances heard.

    Args:
        seconds: How long to listen (clamped to 0.2 – 30 s). Beacons arrive every 2 s, so 3 s
            is the sensible default.
        port: UDP port to listen on (default 9881).
        stop_after: Return early once this many unique instances were found.

    Returns:
        ``{"instances": [{"name", "host", "port", "live", "needs_token"}, ...],
        "listened": <seconds>, "port": <port>, "notes": [str, ...]}`` — sorted by name.
        ``notes`` explains an empty result (e.g. the port is already in use by another
        LiveBridge MCP server, or the firewall blocks UDP).

    Gotchas:
        - Broadcasts do not cross subnets or most VPNs; use ``live_connect(host, port, token)``
          with the IP directly when Live sits elsewhere.
        - Only one process can comfortably own port 9881 on some systems; SO_REUSEADDR (and
          SO_REUSEPORT where available) is set, but a second listener may still hear nothing.
        - When nothing was heard (and the port could be opened) ``notes`` carries the platform
          hint from :func:`silence_hint`: on Windows the firewall of THIS (Claude) machine may
          drop the beacons (the note has the ``New-NetFirewallRule`` line to allow them); on
          macOS 15+ the app running Claude needs Local Network access, and the Live machine must
          have been installed with ``--network`` (beacon on).
    """
    seconds = max(0.2, min(float(seconds or 3.0), 30.0))
    notes: list[str] = []
    socket_error = False
    found: dict[tuple[str, int], dict[str, Any]] = {}

    sock: socket.socket | None = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        except OSError:  # pragma: no cover - platform dependent
            pass
        reuse_port = getattr(socket, "SO_REUSEPORT", None)
        if reuse_port is not None:
            try:
                sock.setsockopt(socket.SOL_SOCKET, reuse_port, 1)
            except OSError:  # pragma: no cover - Windows has no SO_REUSEPORT
                pass
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except OSError:  # pragma: no cover - platform dependent
            pass
        sock.bind(("0.0.0.0", int(port)))
        sock.settimeout(0.25)

        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(_MAX_DATAGRAM)
            except socket.timeout:
                continue
            except OSError as exc:
                notes.append(f"stopped listening: {exc}")
                socket_error = True
                break
            instance = parse_beacon(data, addr)
            if not instance:
                continue
            found[(instance["host"], instance["port"])] = instance
            if stop_after and len(found) >= stop_after:
                break
    except OSError as exc:
        notes.append(
            f"could not listen on UDP {port}: {exc}. Another LiveBridge MCP server may already be "
            "listening, or the firewall blocks it."
        )
        socket_error = True
    except Exception as exc:  # pragma: no cover - discovery must never raise
        log.exception("unexpected discovery error")
        notes.append(f"unexpected discovery error: {exc}")
        socket_error = True
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:  # pragma: no cover
                pass

    instances = sorted(found.values(), key=lambda i: (str(i.get("name") or ""), i["host"]))
    if not instances and not socket_error:
        hint = silence_hint(int(port))
        if hint:
            notes.append(hint)
    return {"instances": instances, "listened": seconds, "port": int(port), "notes": notes}


def silence_hint(port: int = BEACON_PORT, platform: str | None = None) -> str | None:
    """Why a listen that worked may still hear no beacon, for this (Claude) machine's platform.

    Args:
        port: The UDP beacon port that was listened on.
        platform: ``sys.platform`` value to use (default: the running one).

    Returns:
        The Windows firewall note (with the PowerShell line that allows the beacons), the macOS
        Local Network note, or ``None`` on other platforms.
    """
    name = sys.platform if platform is None else platform
    if name.startswith("win"):
        return (f"nothing heard on UDP {port}: on Windows the firewall of THIS (Claude) machine "
                "may block the beacons - allow them (admin PowerShell): New-NetFirewallRule "
                f"-DisplayName \"LiveBridge discovery\" -Direction Inbound -Protocol UDP "
                f"-LocalPort {port} -Action Allow -Profile Private - or use live_connect with the "
                "Live machine's IP")
    if name == "darwin":
        return (f"nothing heard on UDP {port}: on macOS 15+ the app running Claude (Claude "
                "Desktop or your terminal) needs Local Network access (System Settings > Privacy "
                "& Security > Local Network); also check that the Live machine was installed "
                "with --network (beacon on) - or use live_connect with the Live machine's IP")
    return None
