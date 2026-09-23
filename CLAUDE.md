# LiveBridge

Ableton Live 12 control plugin for Claude: a Python MIDI Remote Script (`remote_script/LiveBridge`,
stdlib only, Python 3.11 inside Live) + an MCP server (`mcp_server/livebridge_mcp`, FastMCP, stdio).

- Spec: `docs/ARCHITECTURE.md` (components, layout, threading, LOM paths, serialization, tool naming)
  and `docs/PROTOCOL.md` (wire protocol). Follow them exactly. Live API: `docs/LIVE_API_VERIFIED.md`;
  the real 12.4.5 dump `docs/LIVE_API_DUMP_12.4.5.md` wins when they disagree (§19 lists the corrections).
- Tests: `.venv/bin/python -m pytest -q tests` (uses the fake `Live` package in `tests/live_stub`).
- Real Live (when running): `.venv/bin/python tests/integration_check.py --scenario beat
  --scenario arrangement --no-play` (temporary `LB_` tracks, deleted again); `installers/bundle.py`
  zips the repo for another machine. Dev loop: `tests/live_dev.py sync [--modules X]` copies the
  handler plus the top-level helpers it imports (`plugin_racks_lib.py`) and data files
  (`plugin_maps/*.json`) and hot-reloads them; `server.py`/`dispatcher.py`/`config.py` changes
  need `tests/live_dev.py restart`.
- Handlers (`remote_script/LiveBridge/handlers/*.py`) and MCP tools (`mcp_server/livebridge_mcp/tools/*.py`)
  are auto-discovered: add a file, never edit a registry list.
- Shared argument parsing lives in `remote_script/LiveBridge/resolve.py` (track/device/parameter
  resolvers behind `ctx.track`/`ctx.device`/`ctx.parameter`, colours, signatures,
  bars.beats.sixteenths, `detail`, paging `{total, offset, count, <items>, next_offset?}`). Use it;
  never re-implement a resolver in a handler.
- Tool names: `live_<area>_<verb>`, singular area (exceptions: `live_tracks_*`, system entry points).
  Every bridge command needs a tool or a `COVERED_BY` entry in `installers/gen_tools_doc.py`.
- After changing tools or commands: `.venv/bin/python installers/gen_tools_doc.py` regenerates
  `docs/TOOLS.md` (168 tools / 200 commands today; `tests/test_consistency.py` fails when it is
  stale). A new tool with required arguments needs a `SAMPLE_ARGS` entry in
  `tests/test_consistency.py` (the end-to-end smoke call must reach the bridge).
- The installed `mcp` is 2.x (`MCPServer`); tool modules get the app from `register(mcp, bridge)`,
  return `Any`, and never raise (`bridge_call` / `tool_error`).
- Analysis lives MCP-side only: `mcp_server/livebridge_mcp/theory.py` (pure Python) and
  `audio_analysis.py` (optional `audio` extra: numpy/scipy/soundfile/pyloudnorm, lazy imports, no
  librosa/numba) behind `tools/analysis.py`. Musical know-how for Claude is
  `.claude/skills/livebridge/PRODUCTION.md` (resource `livebridge://production`); a test checks
  that every `live_*` name in it exists.
- Cross-platform (Windows + macOS) always. No third-party imports inside `remote_script/`.
- Public face (GitHub `valentijnh/livebridge-ableton-mcp`, public): `examples/` (walkthroughs run
  against real Live; `tests/test_examples.py` checks every `live_*` call and argument in their
  ```python blocks and every tool name in README/site), `site/` (GitHub Pages, deployed by
  `.github/workflows/pages.yml`; screenshots and the social image live in `docs/images`, their
  HTML source in `docs/images/src/card.html`). CI (`.github/workflows/tests.yml`) runs the suite
  on macOS, Windows and Linux. Renaming a tool means updating those pages too.
