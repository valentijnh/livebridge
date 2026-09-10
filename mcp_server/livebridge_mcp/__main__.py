"""Entry point: ``livebridge-mcp`` / ``python -m livebridge_mcp``.

Runs the LiveBridge MCP server over stdio. **stdout is the MCP channel** — nothing but protocol
frames may be written there, so all logging goes to stderr.

Examples::

    livebridge-mcp                                   # 127.0.0.1:9880, config/env defaults
    livebridge-mcp --host 192.168.1.20 --token abc   # Live on another machine (LAN mode)
    livebridge-mcp --log-level DEBUG                 # verbose troubleshooting on stderr
    livebridge-mcp --toolsets core                   # fewer tools = less context per chat
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Sequence

from . import __version__
from .client import BridgeClient
from .config import config_path, load_config

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def build_parser() -> argparse.ArgumentParser:
    """The CLI parser (also used by the installers to validate arguments)."""
    parser = argparse.ArgumentParser(
        prog="livebridge-mcp",
        description=(
            "MCP server that lets Claude control Ableton Live 12 through the LiveBridge Remote "
            "Script. Settings precedence: CLI arguments > environment (LIVEBRIDGE_HOST, "
            "LIVEBRIDGE_PORT, LIVEBRIDGE_TOKEN, LIVEBRIDGE_TIMEOUT, LIVEBRIDGE_TOOLSETS) > "
            "~/.livebridge/config.json > defaults (127.0.0.1:9880, 10 s, all tools)."
        ),
    )
    parser.add_argument("--host", default=None, help="Host Ableton Live listens on (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="TCP port of the Remote Script (default 9880)")
    parser.add_argument("--token", default=None, help="Shared token; required in LAN mode")
    parser.add_argument("--timeout", type=float, default=None, help="Default command timeout in seconds (default 10)")
    parser.add_argument(
        "--toolsets",
        default=None,
        help=(
            "Register only some tool modules to save context tokens: comma-separated profiles "
            "(minimal, core, production, all), module names (automation, view, cues, ...) and exclusions "
            "(-view). Examples: 'core', 'core,automation', 'all,-view,-cues'. Default: all. "
            "system and lom are always loaded; hidden commands stay reachable via "
            "live_command_call."
        ),
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=LOG_LEVELS,
        help="Logging verbosity on stderr (default WARNING)",
    )
    parser.add_argument("--version", action="version", version=f"livebridge-mcp {__version__}")
    return parser


def configure_logging(level: str | None) -> None:
    """Send logging to stderr only. Writing to stdout would corrupt the MCP stream."""
    logging.basicConfig(
        stream=sys.stderr,
        level=getattr(logging, (level or "WARNING").upper(), logging.WARNING),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments, build the app and serve MCP over stdio.

    Returns:
        Process exit code: 0 on a clean shutdown (including Ctrl-C / closed stdin).
    """
    args = build_parser().parse_args(argv)
    configure_logging(args.log_level)
    log = logging.getLogger("livebridge_mcp")

    config = load_config(host=args.host, port=args.port, token=args.token, timeout=args.timeout,
                         toolsets=args.toolsets)
    bridge = BridgeClient.from_config(config)
    log.info(
        "LiveBridge MCP %s -> %s (token %s, timeout %.0fs, config %s)",
        __version__,
        bridge.endpoint,
        "set" if bridge.token else "none",
        bridge.timeout,
        config_path(),
    )

    from .server import create_app  # imported late so --version/--help work without `mcp`

    app = create_app(bridge, config=config, toolsets=config.get("toolsets"))
    try:
        app.run("stdio")
    except KeyboardInterrupt:  # pragma: no cover - interactive
        log.info("interrupted, shutting down")
    finally:
        bridge.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
