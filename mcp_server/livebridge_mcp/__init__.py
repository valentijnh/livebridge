"""LiveBridge MCP server — Claude's control surface for Ableton Live 12.

Public API::

    from livebridge_mcp import BridgeClient, BridgeError, create_app, load_config

    cfg = load_config()                     # CLI > env > ~/.livebridge/config.json > defaults
    bridge = BridgeClient.from_config(cfg)  # TCP JSON-lines client, reconnects by itself
    app = create_app(bridge)                # FastMCP app with every tools/*.py module registered
    app.run("stdio")

See ``docs/ARCHITECTURE.md`` and ``docs/PROTOCOL.md`` for the contract this package implements.
"""

from __future__ import annotations

from .client import DEFAULT_HOST, DEFAULT_PORT, DEFAULT_TIMEOUT, BridgeClient
from .config import load_config, save_config
from .discovery import discover
from .errors import BridgeError, error_payload, friendly_error

__version__ = "0.1.0"

__all__ = [
    "BridgeClient",
    "BridgeError",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "DEFAULT_TIMEOUT",
    "__version__",
    "create_app",
    "discover",
    "error_payload",
    "friendly_error",
    "load_config",
    "save_config",
]


def create_app(*args, **kwargs):
    """Lazy re-export of :func:`livebridge_mcp.server.create_app` (keeps `mcp` off the import path
    for code that only needs the client)."""
    from .server import create_app as _create_app

    return _create_app(*args, **kwargs)
