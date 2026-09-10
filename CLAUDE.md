# LiveBridge

Ableton Live 12 control plugin for Claude: a Python MIDI Remote Script (`remote_script/LiveBridge`,
stdlib only, Python 3.11 inside Live) + an MCP server (`mcp_server/livebridge_mcp`, FastMCP, stdio).

- Spec: `docs/ARCHITECTURE.md` (components, layout, threading, LOM paths, serialization, tool naming)
  and `docs/PROTOCOL.md` (wire protocol). Follow them exactly.
- Tests: `.venv/bin/python -m pytest -q tests` (uses the fake `Live` package in `tests/live_stub`).
- Handlers (`remote_script/LiveBridge/handlers/*.py`) and MCP tools (`mcp_server/livebridge_mcp/tools/*.py`)
  are auto-discovered: add a file, never edit a registry list.
- Cross-platform (Windows + macOS) always. No third-party imports inside `remote_script/`.
