# livebridge-mcp

The MCP server half of **LiveBridge**: it exposes Ableton Live 12 to Claude (Claude Code, Claude
Desktop, any MCP client) over stdio, and talks to the `LiveBridge` Remote Script running inside
Live over TCP (JSON lines, `docs/PROTOCOL.md`).

```
Claude  ──stdio(MCP)──>  livebridge-mcp  ──TCP 9880 JSON-lines──>  Ableton Live 12
                                          <──UDP 9881 beacons────
```

## Install

```bash
pip install -e mcp_server          # from the repository root
livebridge-mcp --version
```

Requires Python ≥ 3.10 and the `mcp` package (`mcp>=1.2`; both the 1.x `FastMCP` and the 2.x
`MCPServer` API are supported). Live itself needs the Remote Script from `remote_script/LiveBridge`
— see `installers/install.py`.

## Run

```bash
livebridge-mcp                                    # 127.0.0.1:9880
livebridge-mcp --host 192.168.1.20 --token abc123  # Live on another machine (LAN mode)
livebridge-mcp --log-level DEBUG                   # troubleshooting; logs go to stderr
```

Register it with Claude Code:

```bash
claude mcp add livebridge -- livebridge-mcp
```

or in `claude_desktop_config.json`:

```json
{ "mcpServers": { "livebridge": { "command": "livebridge-mcp", "args": [] } } }
```

**stdout is the MCP channel.** Never print to it; all logging goes to stderr.

## Configuration

Precedence: CLI arguments → environment → user config file → defaults.

| Setting | CLI | Environment | Config file key | Default |
|---|---|---|---|---|
| Host | `--host` | `LIVEBRIDGE_HOST` | `host` | `127.0.0.1` |
| Port | `--port` | `LIVEBRIDGE_PORT` | `port` | `9880` |
| Token | `--token` | `LIVEBRIDGE_TOKEN` | `token` | none |
| Timeout (s) | `--timeout` | `LIVEBRIDGE_TIMEOUT` | `timeout` | `10` |

Config file: `~/.livebridge/config.json` (Windows: `%USERPROFILE%\.livebridge\config.json`), or the
path in `LIVEBRIDGE_CONFIG`. `live_connect(..., persist=True)` writes it for you.

## Tools in this package

168 tools in 22 modules — the full generated reference is [docs/TOOLS.md](../docs/TOOLS.md).
The core (`tools/system.py`, `tools/lom.py`):

| Tool | What it does |
|---|---|
| `live_status` | Is Live reachable? Version, edition, token/eval state, where settings came from. **Call this first.** |
| `live_connect` | Point at another Live instance (host/port/token), optionally persisting it. |
| `live_discover` | Listen for UDP beacons and list Live machines on the LAN. |
| `live_commands` | The command catalogue this Live installation actually supports. |
| `live_command_call` | Run any bridge command by name (for the few without a curated tool). |
| `live_log` | Write a line into Live's `Log.txt` and/or read back the bridge's recent log lines. |
| `live_lom_get` / `live_lom_set` | Read/write any property in the Live Object Model. |
| `live_lom_call` | Call any LOM method. |
| `live_lom_describe` | Properties, methods and child collections of any object. |
| `live_lom_children` | List a collection (`song.tracks`, `...devices`, `...parameters`). |
| `live_eval_python` | Run Python inside Live (needs `allow_eval`). Last resort. |

LOM paths: `song`, `song.tracks[2]`, `song.tracks[2].devices[0].parameters[3]`,
`song.tracks[0].clip_slots[3].clip`, `song.return_tracks[0]`, `song.master_track`, `song.scenes[1]`,
`song.view.selected_track`, `app.view`, `browser.instruments`. Indices are 0-based; negatives count
from the end. Every summary carries its own `path`.

## Adding a tool module

Drop a file in `livebridge_mcp/tools/`; it is auto-discovered (no registry to edit).

```python
from typing import Any
from . import bridge_call, drop_none

def register(mcp, bridge) -> None:
    @mcp.tool()
    def live_transport_play(start_time: float | None = None) -> Any:
        """Start playback. Args: start_time — bar position to start from. Returns: {...}."""
        return bridge_call(bridge, "transport.play", drop_none(start_time=start_time))
```

Rules: the tool name is the function name (`live_<area>_<verb>`, singular area); annotate the
return as `Any`; never raise — `bridge_call` turns every failure into
`{"error": "<one friendly line>", "type": ...}`; keep results compact. `mcp` 2.x renamed FastMCP to
`MCPServer`: never import it in a tool module (use the `mcp` argument, or `from ..server import
FastMCP`). Then run `.venv/bin/python installers/gen_tools_doc.py` to refresh `docs/TOOLS.md`.

## Errors

Tools never raise and never show tracebacks. They return
`{"error": "<one line>", "type": "<kind>", "cmd": "<bridge command>"}` where `type` is one of
`auth`, `bad_request`, `unknown_command`, `bad_args`, `not_found`, `invalid_state`, `unsupported`,
`timeout`, `forbidden`, `internal`, plus `connection` and `protocol` from the client itself.

## Tests

```bash
.venv/bin/python -m pytest -q tests/test_mcp_core.py
```

`tests/fake_bridge.py` is an in-process TCP server that speaks the real protocol (token checks,
error envelopes, scripted delays and dropped connections), so client, config, discovery and every
tool are tested without Ableton Live.
