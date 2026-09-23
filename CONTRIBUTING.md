# Contributing to LiveBridge

Thanks for helping! Bug reports from real sets, new tools, better examples and fixes for Windows
or other Live versions are all welcome.

## Reporting a bug

Open an [issue](https://github.com/valentijnh/livebridge-ableton-mcp/issues/new/choose) with:

- your OS, Live version and edition, and how Claude runs (Claude Code / Claude Desktop / other);
- the output of `live_status(include_server=true)` (the token is redacted);
- what you asked, what happened and what you expected; the tool call and its error if you have it;
- the LiveBridge lines from Live's `Log.txt` if Live misbehaved
  ([where to find it](docs/TROUBLESHOOTING.md)).

Security problems: please don't open a public issue, see [SECURITY.md](SECURITY.md).

## Development setup

```bash
git clone https://github.com/valentijnh/livebridge-ableton-mcp.git && cd livebridge-ableton-mcp
python3 -m venv .venv && .venv/bin/pip install -e "mcp_server[dev,audio]"
.venv/bin/python -m pytest -q tests
```

The tests need no Ableton Live: they run the Remote Script against a fake `Live` package in
`tests/live_stub`. CI runs them on macOS, Windows and Linux.

With Live running and LiveBridge enabled, `tests/integration_check.py` checks the real thing
end to end on temporary tracks and removes them afterwards:

```bash
.venv/bin/python tests/integration_check.py --scenario beat --scenario arrangement --no-play
```

While you work on a handler, `tests/live_dev.py sync` copies it into Live's Remote Scripts folder
and hot-reloads it; changes to `server.py`, `dispatcher.py` or `config.py` need
`tests/live_dev.py restart`.

## Ground rules

- **Read the spec first:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (components, threading,
  LOM paths, serialization, tool naming) and [docs/PROTOCOL.md](docs/PROTOCOL.md). What Live's
  API really does is in [docs/LIVE_API_VERIFIED.md](docs/LIVE_API_VERIFIED.md); the dump of Live
  12.4.5 wins when they disagree.
- **Cross-platform, always:** everything must work on Windows and macOS.
- **The Remote Script is standard-library Python 3.11** (the Python inside Live 12): no
  third-party imports in `remote_script/`.
- **Add files, don't edit registries:** handlers (`remote_script/LiveBridge/handlers/*.py`) and
  MCP tools (`mcp_server/livebridge_mcp/tools/*.py`) are discovered automatically.
- **Reuse the resolvers** in `remote_script/LiveBridge/resolve.py` for tracks, devices,
  parameters, colours, times and paging. Don't write a new one in a handler.
- **Tool names** are `live_<area>_<verb>` with a singular area (exceptions: `live_tracks_*` and the
  system entry points). Every bridge command needs a tool or a `COVERED_BY` entry in
  `installers/gen_tools_doc.py`.
- **Tools never raise:** they return results or a friendly error (`bridge_call` / `tool_error`).
- **Every mutating command is one undo step** in Live.

## After changing tools or commands

```bash
.venv/bin/python installers/gen_tools_doc.py      # regenerates docs/TOOLS.md
.venv/bin/python -m pytest -q tests
```

`tests/test_consistency.py` fails when `docs/TOOLS.md` is stale, and a new tool with required
arguments needs a `SAMPLE_ARGS` entry there so the end-to-end smoke call reaches the bridge.
`tests/test_examples.py` checks that the tool calls in `examples/` and the tool names in the
README and on the website still exist, so update those pages when you rename something.

## Pull requests

- One topic per PR, with a short description of what changes for the user.
- Tests for new behaviour; the full suite green on your machine.
- If you verified against a real Live, say which version, edition and OS.
- New tools deserve a line in an [example](examples/) or the
  [prompt library](examples/prompts.md) when they enable something new.

By contributing you agree that your work is released under the [MIT License](LICENSE).
