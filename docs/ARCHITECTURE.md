# LiveBridge — Architecture Specification

LiveBridge lets Claude (Claude Code, Claude Desktop, any MCP client) control **everything** in
Ableton Live 12: transport, tracks, clips, MIDI notes, audio clips, devices, third‑party plugin
parameters, the browser (instruments/effects/plugins/samples/packs/user library/Splice folder),
automation, arrangement, scenes, mixer, routing, recording and view/navigation.

Targets: **Ableton Live 12.4.5 Suite** (must also work on Standard/Intro), **Windows and macOS**,
Live on the same machine as Claude *or* on another machine on the LAN (the user alternates
between a Windows PC and a Mac).

This document is the contract every builder agent follows. Read `docs/PROTOCOL.md` too.

---

## 1. Components

```
┌──────────────────────────────┐        TCP JSON-lines (PROTOCOL.md)        ┌──────────────────────────────┐
│  Ableton Live 12             │  <────────────────────────────────────────> │  livebridge-mcp (Python)      │
│  ┌────────────────────────┐  │        default 127.0.0.1:9880               │  FastMCP server, stdio        │
│  │ Remote Script          │  │        LAN mode: 0.0.0.0:9880 + token       │  tools/*.py                   │
│  │ remote_script/LiveBridge│ │  <──── UDP discovery beacon 9881 ────────   │  client.py (reconnecting)     │
│  │ (stdlib Python 3.11)   │  │                                             │                              │
│  └────────────────────────┘  │                                             └──────────────┬───────────────┘
└──────────────────────────────┘                                                            │ stdio (MCP)
                                                                                            ▼
                                                                    Claude Code / Claude Desktop / Cursor …
                                                                    (+ official Splice remote MCP https://mcp.splice.com/mcp)
```

1. **Remote Script** (`remote_script/LiveBridge/`) — a MIDI Remote Script (ControlSurface
   subclass) that Live loads at startup. It runs a TCP server on a background thread and executes
   every command on Live's main thread against the Live Object Model (LOM). **Python stdlib only**
   (Live embeds its own Python 3.11; no pip). Must be Python 3.11‑compatible: no 3.12+ syntax
   (no PEP 695 generics, no nested same‑quote f‑strings, no `type` statements).
2. **MCP server** (`mcp_server/`) — a normal Python package (`livebridge_mcp`, Python ≥3.10,
   `mcp` package / FastMCP) exposing curated MCP tools over stdio. It talks to the Remote Script
   over TCP and reconnects automatically.
3. **Installers** (`installers/`) — cross‑platform `install.py` (+ `install.sh` / `install.ps1`
   wrappers) that copies the Remote Script into Live's User Library, writes config, installs the
   MCP server, and registers it (plus Splice) in Claude Desktop and Claude Code.
4. **Tests** (`tests/`) — a fake `Live` package (`tests/live_stub/`) so Remote Script code is unit
   tested outside Live, plus MCP tool tests against a fake bridge, plus `tests/integration_check.py`
   for a real Live instance.

---

## 2. Directory layout (authoritative — do not invent other top‑level dirs)

```
AbletonPluginClaude/
├── remote_script/LiveBridge/          # copied verbatim into Live's Remote Scripts folder
│   ├── __init__.py                    # create_instance(c_instance) -> LiveBridge
│   ├── LiveBridge.py                  # ControlSurface subclass; lifecycle; main-thread pump
│   ├── config.py                      # loads config.json next to the script (host/port/token/allow_eval/beacon)
│   ├── server.py                      # threaded TCP JSON-lines server + UDP discovery beacon
│   ├── dispatcher.py                  # queue -> main thread; timeouts; error envelopes
│   ├── registry.py                    # @command("ns.name") decorator, auto-discovery of handlers/*.py
│   ├── lom.py                         # LOM path resolver (see §5), safe get/set/call, describe
│   ├── serialize.py                   # LOM object -> compact JSON summaries (see §6)
│   ├── log.py                         # logging into Live's Log.txt via c_instance.log_message
│   ├── compat.py                      # feature detection helpers (hasattr-based), Live version
│   ├── resolve.py                     # SHARED argument resolvers/parsers (track, device, colour,
│   │                                  #   signature, bars.beats.sixteenths, detail, paging) — §4
│   ├── plugin_racks_lib.py            # generated rack presets (.adg XML) exposing VST3 parameters
│   ├── plugin_maps/*.json             # shipped name -> VST3 ParameterId maps (Serum 2, Serum 2 FX)
│   └── handlers/                      # ONE FILE PER MODULE, auto-discovered
│       ├── __init__.py                # empty (registry uses pkgutil to import every module here)
│       ├── system.py   lom.py   eval.py                     # core (Fundament phase)
│       ├── transport.py scenes.py view.py                   # module A
│       ├── tracks.py  mixer.py  routing.py  record.py       # module B
│       ├── clips.py   notes.py  arrangement.py              # module C
│       ├── devices.py plugins.py racks.py                   # module D
│       ├── plugin_racks.py                                  # plugin_racks.expose/map/presets
│       ├── browser.py samples.py                            # module E
│       ├── automation.py cues.py                            # module F
│       └── devtools.py                                      # system.reload (dev hot reload)
├── mcp_server/
│   ├── pyproject.toml                 # name "livebridge-mcp", script entry `livebridge-mcp = livebridge_mcp.__main__:main`
│   └── livebridge_mcp/
│       ├── __init__.py  __main__.py   # main(): parse env/args, build FastMCP, run stdio
│       ├── server.py                  # create_app() -> FastMCP; auto-registers tools/*.py via register(mcp, bridge)
│       ├── client.py                  # BridgeClient: connect/reconnect, request(cmd, args, timeout), token, discovery
│       ├── config.py                  # env LIVEBRIDGE_HOST/PORT/TOKEN/TIMEOUT + ~/.livebridge/config.json
│       ├── discovery.py               # listen for UDP beacons, list Live instances on LAN
│       ├── errors.py                  # BridgeError -> friendly tool error text
│       └── tools/                     # ONE FILE PER MODULE, auto-discovered; each exposes register(mcp, bridge)
│           ├── __init__.py
│           ├── system.py  lom.py                            # core
│           ├── transport.py scenes.py view.py               # module A
│           ├── tracks.py mixer.py routing.py record.py      # module B
│           ├── clips.py notes.py arrangement.py             # module C
│           ├── devices.py plugins.py racks.py               # module D
│           ├── plugin_racks.py                              # live_plugin_expose / _param_map / _preset_files
│           ├── browser.py samples.py splice.py              # module E
│           └── automation.py cues.py                        # module F
├── installers/  install.py  install.sh  install.ps1  uninstall.py  bundle.py  gen_tools_doc.py (→ docs/TOOLS.md)
├── docs/        ARCHITECTURE.md PROTOCOL.md TOOLS.md (generated) INSTALL.md NETWORK.md TROUBLESHOOTING.md
│                LIVE_API_NOTES.md LIVE_API_VERIFIED.md LIVE_API_DUMP_12.4.5.{md,json} PLUGIN_RACKS.md
│                LIVE_TEST_REPORT.md live_test/*.md
├── tests/
│   ├── live_stub/                     # fake `Live` package: Live.Application, Live.Song, Live.Track, Live.Clip, Live.Device, Live.Browser, Live.Base ...
│   ├── conftest.py                    # puts tests/live_stub and remote_script on sys.path; fixtures: song, bridge, mcp_app
│   ├── fake_bridge.py                 # in-process TCP server speaking PROTOCOL.md for MCP tests
│   ├── live_stub_ext/<module>.py      # per-module stub extensions (install(stub) -> uninstall)
│   ├── test_core.py test_mcp_core.py
│   ├── test_<module>.py               # one per handler/tool module
│   ├── test_consistency.py            # every tool end-to-end, shared resolvers, TOOLS.md freshness
│   ├── test_workflow_beat.py          # the 8-bar-beat recipe through the MCP tools on the stub
│   ├── integration_check.py           # run against real Live; prints PASS/FAIL per capability
│   └── (dev tools) tests/live_dev.py, tests/live_query.py, tests/dump_live_api.py — dev loop against Live
├── .claude/skills/livebridge/SKILL.md # how Claude should use the tools (workflow tips)
├── CLAUDE.md  PLAN.md  README.md  LICENSE (MIT)
```

**Ownership rule for parallel builders:** a builder only creates/edits the files assigned to it.
Shared files (`registry.py`, `dispatcher.py`, `lom.py`, `serialize.py`, `server.py`, `client.py`,
`tests/live_stub/*`, `conftest.py`) are owned by the Fundament agents. If a module needs extra
stub behaviour it adds `tests/live_stub_ext/<module>.py` (a function `install(stub)` that patches
the stub) and loads it from its own test file — it never edits the shared stub.

---

## 3. Remote Script: threading model (critical)

- Live's LOM may **only** be touched from Live's main thread. The socket thread never touches LOM.
- `server.py` accepts connections on a daemon thread; one reader thread per client; parses JSON
  lines; each request is put on `dispatcher.queue` together with a `threading.Event` + result slot.
- The main thread drains the queue from `LiveBridge.update_display()` (Live calls it ~every
  100 ms), which first calls the base `ControlSurface.update_display()`. The socket thread does
  **not** call `schedule_message` (verified: it is not thread-safe and its callbacks run from
  `update_display` anyway — see §10 and `docs/LIVE_API_VERIFIED.md` §1). `dispatch()` on the main
  thread itself executes inline. Draining is idempotent and bounded (max N commands or ~40 ms per
  drain to keep the UI responsive; a long command still runs to completion).
- Inside Live the socket threads only get the GIL while the main thread runs Python, so while
  clients are connected `update_display` sleeps 1 ms before the drain (readers enqueue what
  arrived) and again after running jobs (readers send the responses): ≈0.1 s per round trip
  instead of ≈0.5–1 s (measured on Live 12.4.5).
- The socket thread waits on the Event with the request's timeout (default 10 s, max 120 s).
  If it times out it returns `error.type = "timeout"` but the command may still execute later.
- Every handler runs inside `try/except`; exceptions become `error` envelopes with `type`,
  `message`, `traceback`. The script must never crash Live and never leave the queue stuck.
- `song.begin_undo_step()/end_undo_step()` wrap every mutating command so each command is one
  undo step (handler declares `mutating=True` in the decorator).
- `disconnect()` stops the server, closes sockets, stops the beacon. Reload‑safe.
- A failed bind (port still held after opening a set or re-selecting the control surface) is
  retried from `update_display`: every 1 s for 3 min, then every 10 s (`system.status` →
  `bind {bound, attempts, error, retry_in}`). On Windows `stop()` resets client sockets
  (`SO_LINGER` 0) so `SO_EXCLUSIVEADDRUSE` can rebind at once. `SystemExit` /
  `KeyboardInterrupt` raised by a handler (e.g. `sys.exit()` in `eval.python`) are contained
  and answered as `bad_args`; `update_display` never lets an exception reach Live.
- A connection that is refused ("too many clients") gets the rejection line, then the server
  half-closes, drains the client's request for ≤0.5 s and closes (a plain close would make the
  kernel answer the unread request with an RST that can discard the line).

## 4. Handler registration API (`registry.py`)

```python
from ..registry import command

@command("tracks.list", mutating=False, doc="List all tracks with summaries")
def tracks_list(ctx, include_returns=True, detail="summary"):
    return [ctx.summarize(t, detail) for t in ctx.song.tracks]
```

- `ctx` (a `Context` object) exposes: `song` (Live.Song.Song), `app` (Live.Application),
  `browser` (`app.browser`), `view` (`song.view`), `resolve(path)` (see §5), `path_of(obj)` (best
  effort), `summarize(obj, detail)`, `log(msg)`, `version` (tuple), `has(obj, attr)`, `script`
  (the ControlSurface instance), and the loose-argument coercers `track(spec)`, `scene(spec)`,
  `clip_slot(track, slot)`, `clip(track, slot)`, `device(track, device)`,
  `parameter(device, parameter)`.
- **Shared helpers live in `resolve.py`, never in a handler copy.** `ctx.track` / `ctx.scene` /
  `ctx.device` / `ctx.parameter` delegate to `resolve.track` / `resolve.scene` /
  `resolve.device` / `resolve.parameter`, so every command accepts the same forms and raises
  the same errors (ambiguous names → `bad_args` listing the candidates; integral floats are
  accepted as indices; a return/master name where those are excluded → `bad_args` "… is a
  return track — not allowed here"). Also there: `parse_color` /
  `apply_color` (palette index, `"#RRGGBB"`, `[r,g,b]`, colour name), `parse_signature`
  (`"3/4"`, `[3,4]`), `beats_to_bbs` / `bbs_to_beats` / `parse_time` (numbers are beats,
  `"17.1.1"` strings are bars.beats.sixteenths — 1-based positions, 0-based lengths),
  `check_detail`, `check_paging` and `paged()` (the paged result shape
  `{total, offset, count, <items>, next_offset?}`). A helper needed by 2+ modules goes there.
- Args are passed as keyword arguments from the request `args` object. Unknown args ⇒
  `error.type="bad_args"`. Handlers validate types and raise `BridgeError(type, message)`.
- Handlers return JSON‑serialisable data only (dict/list/str/int/float/bool/None). Use
  `ctx.summarize()` for LOM objects.
- `registry.discover()` imports every module in `handlers/` via `pkgutil.iter_modules` so nobody
  has to edit `handlers/__init__.py`. Duplicate command names raise at load time (log it).
- `system.commands` returns every registered command with its doc + parameter names (introspected
  from the function signature) — the MCP server can use it, and it is the truth for `docs/TOOLS.md`.

## 5. LOM path specification (`lom.py`)

A path is a string that addresses any object in the Live Object Model. Grammar:

```
path      := root ("." segment)*
root      := "song" | "app" | "browser"
segment   := name | name "[" index "]"
index     := integer (0-based; negative allowed)
```

Examples: `song`, `song.tracks[2]`, `song.tracks[2].devices[0].parameters[3]`,
`song.tracks[0].clip_slots[3].clip`, `song.tracks[0].arrangement_clips[1]`,
`song.return_tracks[0]`, `song.master_track`, `song.scenes[1]`, `song.view.selected_track`,
`app.view`, `browser.instruments`, `song.tracks[1].devices[0].chains[0].devices[0]`,
`song.tracks[0].devices[0].drum_pads[36].chains[0]`.

`lom.resolve(path)` walks attributes and index lookups (vectors are indexed with `[i]` and also
support `len()`), raising `BridgeError("not_found", ...)` with the failing segment.
`lom.get(path, prop)`, `lom.set(path, prop, value)` (with type coercion: enums/ints/floats/bools/str;
for `DeviceParameter` clamp to `min/max` and support `value`, `str_for_value`), `lom.call(path,
method, args)`, `lom.describe(path)` (lists properties with current values + types, methods, child
collections with counts, using `dir()` and skipping private/`add_*_listener`/`remove_*_listener`),
`lom.children(path)`. Every object summary includes its canonical `path` so Claude can navigate.

Track indices refer to `song.tracks` (regular tracks). Return tracks are addressed via
`song.return_tracks[i]`, master via `song.master_track`. Handlers accept `track` args as an int
index, a track name (exact, case-insensitive, unique prefix, then a unique contains /
punctuation-free match), a return letter (`"A"`, `"return B"`), `"master"`/`"main"`,
`"selected"`, or a full path — one implementation, `resolve.track`. `slot` is a scene index or
scene name; `clip` is a clip / clip-slot / arrangement-clip path, a clip name or `"selected"`;
`device` is an index, a name (also inside racks) or a path; `parameter` an index, name or path.

## 6. Serialization (`serialize.py`)

`summarize(obj, detail="summary"|"full"|"minimal")` returns compact dicts per type. Token economy
matters: `summary` must fit a whole set in a few KB. Per type:

- **Track**: path, index, name, type (midi/audio/return/master/group), color_index, mute, solo, arm,
  is_grouped/group_track, fold_state, devices (minimal: index, name, class_name, is_active), volume/pan
  (values), playing_slot_index, fired_slot_index, has_midi_input/has_audio_input, monitoring state,
  input/output routing names, clip_slots (only non‑empty: index, clip name, is_playing, length) in
  `full`.
- **Clip**: path, name, is_midi/is_audio, length, loop_start/loop_end, start_marker/end_marker, looping,
  is_playing/is_recording/is_triggered, color_index, muted, signature, warping/warp_mode (audio),
  gain, pitch_coarse/fine, file_path (audio), note count (MIDI). Notes are not included unless asked.
- **Device**: path, index, name, class_name, class_display_name, type (instrument/audio_effect/midi_effect,
  from `device.type`), is_active, can_have_chains, can_have_drum_pads, is_plugin (class_name in
  `PluginDevice`/`AuPluginDevice`/`VstPluginDevice`/`Vst3PluginDevice`), parameter count; `full` adds
  parameters (index, name, value, min, max, is_quantized, value_items when quantized, display value),
  chains, presets info where available.
- **DeviceParameter**: path, index, name, original_name, value, min, max, default_value, is_quantized,
  value_items, display_value (`str_for_value(value)`), is_enabled, automation_state.
- **Scene**: path, index, name, color_index, is_triggered, tempo/time signature if enabled.
- **Song** (`song.summary`): tempo, signature, is_playing, current_song_time, loop info, metronome,
  record_mode, session_record, track count, scene count, selected track/scene, Live version.

Never serialize raw LOM objects; never include listeners; catch per‑attribute exceptions and omit.

## 7. Config & security

`remote_script/LiveBridge/config.json` (written by the installer, defaults in `config.py`):

```json
{ "host": "127.0.0.1", "port": 9880, "token": "<random hex>", "allow_eval": true,
  "beacon": true, "beacon_port": 9881, "name": "Valentijn-PC", "max_clients": 8 }
```

- `host` = `127.0.0.1` (same machine) or `0.0.0.0` (LAN mode). The token is **required** in LAN
  mode and checked on every request; on localhost a missing token is accepted only if config token
  is empty. `load_config` falls back to `127.0.0.1` when `host` is non-loopback and there is no
  token (Log.txt says so) — a LAN bridge never runs open.
- The UDP beacon broadcasts `{"livebridge":1,"name":...,"port":9880,"live":"12.4.5","host":<ip>}`
  every 2 s to `255.255.255.255:9881` when enabled. Its default follows the mode: on in LAN mode,
  off on localhost unless `"beacon"` is set in config.json. The MCP server's `discovery.py` listens for 3 s
  to list instances (tool `live_discover`); tool `live_connect(host, port, token, persist)`
  switches at runtime (`persist=true` also writes `~/.livebridge/config.json`). The beacon is
  outgoing from the Live machine; the *Claude* machine is the one that must accept incoming UDP
  9881 (firewall), and the Live machine only incoming TCP 9880 (docs/NETWORK.md).
- Re-running the installer keeps `host` (LAN mode), `port`, `allow_eval`, `beacon` and the MCP
  host/port unless the matching flag is passed (`--network/--no-network`, `--local`, `--port`,
  `--allow-eval/--no-allow-eval`, `--beacon/--no-beacon`, `--pair`).
- `allow_eval` gates `eval.python` (arbitrary Python inside Live), and `eval.python` is refused
  (`forbidden`) whenever no token is configured — eval is always token-protected; `hello.allow_eval`
  reports the effective value (allow_eval **and** a token).

MCP server config precedence: CLI args > env (`LIVEBRIDGE_HOST`, `LIVEBRIDGE_PORT`,
`LIVEBRIDGE_TOKEN`, `LIVEBRIDGE_TIMEOUT`, `LIVEBRIDGE_TOOLSETS`; `--toolsets`) >
`~/.livebridge/config.json` (`"toolsets"`: `"core"`, `"core,automation"` or a list) > defaults.
Tokens in `~/.livebridge/config.json` are bound to their endpoint (top-level token = the file's
host:port, plus a `"tokens"` `{"host:port": token}` map); a token is never sent to a different
endpoint, and `live_connect` without a token uses only the token stored for that exact endpoint.

## 8. Cross‑platform paths

| What | macOS | Windows |
|---|---|---|
| Remote Scripts (User Library) | `~/Music/Ableton/User Library/Remote Scripts/` | `%USERPROFILE%\Documents\Ableton\User Library\Remote Scripts\` |
| Live Log.txt | `~/Library/Preferences/Ableton/Live 12.x.x/Log.txt` | `%APPDATA%\Ableton\Live 12.x.x\Preferences\Log.txt` |
| Claude Desktop config | `~/Library/Application Support/Claude/claude_desktop_config.json` | `%APPDATA%\Claude\claude_desktop_config.json` |
| LiveBridge user config | `~/.livebridge/config.json` | `%USERPROFILE%\.livebridge\config.json` |

The User Library location can be changed in Live's preferences; the installer accepts
`--remote-scripts-dir` and also scans `Library.cfg`/Preferences for a custom path when feasible.
After copying, the user enables **Preferences → Link, Tempo & MIDI → Control Surface → LiveBridge**
(Input/Output: None). The installer prints exactly this.

## 9. MCP server design

- FastMCP over stdio. The repo pins `mcp` 2.x, where FastMCP was renamed `MCPServer`
  (`from mcp.server.mcpserver import MCPServer`); `server.py` imports whichever exists
  (`mcp.server.fastmcp.FastMCP` on 1.x) and tool modules get the instance from
  `register(mcp, bridge)` — never import FastMCP in a tool module. `create_app(bridge)` in
  `server.py` auto‑imports `tools/*.py` and calls `register(mcp, bridge)` on each. Tools annotate
  their return type as `Any` and never raise (`bridge_call` / `tool_error` return the friendly
  `{"error", "type", "cmd"}` shape).
- Tool naming: `live_<area>_<verb>` snake_case with a **singular** area, e.g.
  `live_transport_play`, `live_clip_add_notes`, `live_device_set_parameter`, `live_scene_fire`,
  `live_cue_add`, `live_browser_load`, `live_lom_get`. Exceptions: `live_tracks_*` (named in the
  original spec) and the system entry points `live_status`, `live_connect`, `live_discover`,
  `live_commands`, `live_log`. `installers/gen_tools_doc.py --check` enforces the rule. The
  build has 166 tools (200 bridge commands) — rich tools with optional args instead of dozens of tiny ones. Every tool
  has a precise docstring (what, args, returns, gotchas) — Claude reads these. `docs/TOOLS.md`
  is generated from the code; every bridge command maps to a tool or to a documented composite.
- Tools return compact text/JSON: `create_app` hands each tool module a registrar whose `tool()`
  wraps the function so dict/list results leave as ONE compact JSON text (FastMCP would indent
  them and split lists into one block per item); large results (notes, parameter lists) support
  paging or filtering args. Tools reject undeclared argument names
  (`server.enforce_declared_arguments` sets `extra=forbid` and `additionalProperties: false`);
  tool modules can be limited with toolsets (`server.TOOLSETS`: minimal, core, production, all).
  The skill text (`.claude/skills/livebridge/SKILL.md`) is also served as the resource
  `livebridge://skill` and the prompt `livebridge_workflow` for clients without the skill. Errors from the bridge are returned as clear messages (`Live is not reachable at
  host:port — is Live running with the LiveBridge control surface enabled?`), never stack traces.
- Higher‑level composite tools live next to primitives where it saves Claude round‑trips, e.g.
  `live_browser_load(query, category="instrument", new_track=true)`,
  `live_clip_write_pattern(...)`, `live_command_batch([...])` (many commands, one undo step),
  `live_plugin_expose(plugin="Serum 2", parameters=["sound_design"])`,
  `live_sample_import(file_path, track, mode="session"|"arrangement")`,
  `live_set_snapshot()` (whole set summary in one call).
- `live_lom_get/set/call/describe/children` + `live_eval_python` are the escape hatches so
  *anything* in the LOM is reachable even without a curated tool; `live_command_call(cmd, args)`
  runs any bridge command that has no curated tool (e.g. `notes.theory`, `system.reload`).
- Splice: not reimplemented. `tools/splice.py` documents the official remote MCP
  (`https://mcp.splice.com/mcp`) and provides `live_splice_import_downloaded(files=[...] |
  folder, track, mode="session"|"arrangement"|"simpler"|"drum_rack", ...)` — `files` are the
  exact paths the Splice MCP reported (uploaded to the Live machine in LAN mode), without them
  the newest sample(s) of the download folders —, `live_splice_watch_folder` (waits for the next
  download and imports it; only for downloads made by hand in the Splice app/browser) and
  `live_splice_setup_info`. Splice is not
  exposed through Live's Python browser; a Splice folder added to Places is found by
  `live_browser_search`/`live_browser_browse(path="splice/...")`.

## 10. Live API notes (verified — `docs/LIVE_API_VERIFIED.md` is the full reference)

Checked on 2026-09-10 against a runtime capture of Live 12.4's `Live` module, the Cycling '74
LOM docs, Ableton's decompiled 12.4 factory scripts and working Live 12 socket scripts. The
test stub (`tests/live_stub`) mirrors that reference exactly. Always feature-detect 12.x
additions with `compat.has(obj, name)`. A real introspection dump of Live 12.4.5 Suite
(`docs/LIVE_API_DUMP_12.4.5.md`) is the final authority; its corrections are collected in
`docs/LIVE_API_VERIFIED.md` §19.

- **Runtime**: Live 12 embeds Python 3.11. Base class `_Framework.ControlSurface.ControlSurface`
  (also `ableton.v2.control_surface.ControlSurface`); `create_instance(c_instance)`;
  `self.song()`, `self.application()`, `self.log_message(*msg)`, `self.show_message(msg)`,
  `disconnect()` (call the base). `update_display()` is called by Live on the main thread every
  ~100 ms — an override **must call the base**. `schedule_message(delay_in_ticks, callback,
  parameter=None)` only queues a task that the base `update_display` runs on a later tick; it is
  **not thread-safe** (never call it from a socket thread). `c_instance` has no
  `schedule_message`. `Live.Application.get_application()`: `get_major/minor/bugfix_version()`,
  `get_version_string()`, `get_variant()` ("Suite"/"Standard"/"Intro"/"Lite"/"Trial"/"Beta" —
  real edition detection); there is no `get_major_minor_version`. `Live.Base.Timer(callback,
  interval_ms, repeat=False, start=False)` exists; `Live.Base.LimitationError` signals edition
  limits.
- **Objects**: compare LOM objects with `==`/`!=` (deleted objects `== None`), never `is`.
  Read-only properties raise on assignment; "only for X" properties raise instead of returning
  None; int properties reject floats. **Collections are `Live.Base.Vector`** (not list/tuple:
  `len`, index, slice, iterate, `in`; no `index()`) — test them with `compat.is_sequence`.
- **Next-tick properties** (verified 12.4.5): `is_playing` (start/stop/continue),
  `current_song_time` (also `jump_by`, `CuePoint.jump`), `loop`, `punch_in/out`,
  `session_automation_record`, `record_mode`, `back_to_arranger` (also per track) and
  clip-slot recordings are applied on Live's next tick — a read-back in the same command is
  stale, so handlers report the requested state. `continue_playing()` in the same tick as a
  `current_song_time` write starts from the old playhead. `start_time`, tempo, signature,
  metronome, `arrangement_overdub`, quantization, groove and scale are immediate.
- **Enums** are Boost.Python enums (`.name`, class `values`/`names` dicts, no `__members__`):
  `Device.DeviceType` 0 undefined 1 instrument 2 audio_effect **4 midi_effect**;
  `Clip.ClipLaunchQuantization` (clip.launch_quantization, 0 = global … 14 = 1/32);
  `Song.Quantization` (clip_trigger_quantization 0 none … 13 1/32);
  `Song.RecordingQuantization` (midi_recording_quantization **and `clip.quantize` grid**: 0 none,
  1 1/4, 2 1/8, 3 1/8T, 4 1/8+T, 5 1/16, 6 1/16T, 7 1/16+T, 8 1/32);
  `Clip.GridQuantization` (clip.view only); `Clip.WarpMode` 0 beats 1 tones 2 texture 3 repitch
  4 complex 5 rex 6 complex_pro; `Clip.LaunchMode`; monitoring is
  `Live.Track.Track.monitoring_states` (0 IN, 1 AUTO, 2 OFF — no `Live.Track.MonitoringState`);
  `DeviceParameter.AutomationState` 0 none 1 playing 2 overridden.
- **Song**: `tempo` (20–999), `signature_numerator/denominator`, `is_playing` (rw),
  `start_playing()`, `stop_playing()`, `continue_playing()`, `current_song_time`, `start_time`,
  `loop`, `loop_start`, `loop_length`, `metronome`, `record_mode`, `session_record`,
  `session_record_status`, `arrangement_overdub`, `overdub`, `punch_in/punch_out`,
  `back_to_arranger`, `re_enable_automation()`, `stop_all_clips(Quantized=True)`, `tap_tempo()`,
  `undo()/redo()` (return str), `can_undo/can_redo`, `begin_undo_step()/end_undo_step()`,
  `capture_midi(Destination=0)`, `capture_and_insert_scene()`, `trigger_session_record(record_length)`,
  `create_midi_track(Index=None)` / `create_audio_track(Index=None)` (-1 = end, None = after
  selection) → Track, `create_return_track()`, `delete_track(i)`, `delete_return_track(i)`,
  `duplicate_track(i)` (returns **None**), `create_scene(index)` (index required, → Scene),
  `delete_scene(i)`, `duplicate_scene(i)` (returns None), `move_device(device, target, position)`,
  `tracks`, `visible_tracks`, `return_tracks`, `master_track` ("Main"), `scenes`, `cue_points`,
  `set_or_delete_cue()` (returns None), `jump_to_next_cue()/jump_to_prev_cue()`, `jump_by(beats)`,
  `scale_name`/`root_note`/`scale_mode` (12)/`scale_intervals`, `clip_trigger_quantization`,
  `midi_recording_quantization`, `groove_amount`, `swing_amount`, `nudge_up/down`,
  `count_in_duration` (read-only), `groove_pool`, `get_data/set_data`. **No `save()`.**
  `song_length` = max(arrangement end, loop end) + 32 beats; playhead, start marker and loop
  brace writes beyond it raise (checked on each write); `song_length` follows the brace
  immediately (a `loop_length` write reaching it moves it 32 beats on; shrinking the brace
  shrinks it) — LiveBridge extends a song this way (`transport.extend_song`, `move_brace`);
  cue points and the playhead behind the end hold the length; `continue_playing()` resumes at
  the last stop point and ignores `current_song_time` writes made while stopped;
  `loop_length` < 1 is raised to 1;
  `groove_amount` 0..1.3125. `cue_points` is in **creation order**; stopped,
  `set_or_delete_cue()` acts at the insert marker, which `current_song_time` writes snap to the
  zoom-dependent Arrangement grid (`CuePoint.jump()` is exact). `create_scene(i)` copies the
  tempo/signature of `scenes[i-1]` and selects the new scene. API arming ignores "Exclusive
  Arm"; `record_mode = True` starts playback.
  `Song.View`: `selected_track`, `selected_scene`, `highlighted_clip_slot`, `detail_clip`,
  `selected_chain`, `selected_parameter` (ro), `select_device(device, ShouldAppointDevice=True)`,
  `follow_song`, `draw_mode` — **no `selected_device`** (use
  `selected_track.view.selected_device`). `CuePoint`: `name` rw, `time` **read-only**, `jump()`.
- **Track**: `name`, `color_index`, `color`, `mute`, `solo`, `arm` (raises unless
  `can_be_armed`), `implicit_arm`, `has_midi_input`, `has_audio_input/output`, `is_foldable`,
  `fold_state` (raises unless foldable), `is_grouped`, `group_track`, `devices`, `clip_slots`,
  `arrangement_clips`, `take_lanes` (12; `TakeLane`: `name`, `arrangement_clips`,
  `create_midi_clip`, `create_audio_clip`), `mixer_device` (`volume`, `panning`, `sends`,
  `track_activator`, **`crossfade_assign` int 0 A/1 none/2 B**, `panning_mode`,
  `left/right_split_stereo`, master-only `cue_volume`/`crossfader`/`song_tempo`),
  `current_monitoring_state`, `input_routing_type/channel`, `available_input_routing_types/channels`,
  `output_routing_type/channel` (assign an element of `available_*`; in 12.4.5 return tracks and
  the master have input and output routing too, the master output is "Main"), `playing_slot_index`,
  `fired_slot_index`, `delete_device(index)`, `duplicate_device(index)` (12),
  `insert_device(DeviceName, DeviceIndex=-1)` (**12.3+, native devices by exact UI name only**;
  unknown names raise `ValueError("Device X not found.")`),
  `create_midi_clip(start_time, length)` (arrangement, **12.0+**), `create_audio_clip(file_path,
  position)` (arrangement, absolute path), `duplicate_clip_to_arrangement(clip, destination_time)`
  → Clip, `delete_clip(clip)`, `duplicate_clip_slot(i)` (**overwrites** slot i+1),
  `stop_all_clips(Quantized=True)`,
  `view.select_instrument()` → bool, `view.selected_device` (ro), `view.device_insert_mode`,
  `is_frozen`. No API to group, freeze or flatten.
- **ClipSlot**: `has_clip`, `clip`, `create_clip(length)` (MIDI), `create_audio_clip(path)`
  (audio, absolute path), `delete_clip()`, `fire()` / `fire(record_length, launch_quantization,
  force_legato)` (two overloads in 12.4.5), `stop()`, `duplicate_clip_to(slot)` (returns None), `is_playing`,
  `is_recording`, `is_triggered`, `playing_status`, `has_stop_button`.
- **Clip**: `name`, `color_index`, `is_midi_clip`, `is_audio_clip`, `is_session_clip` (12),
  `length` (ro), `loop_start`, `loop_end`, `start_marker`, `end_marker`, `position`, `looping`,
  `muted`, `is_playing` (rw), `is_recording`, `fire()`, `stop()`, `launch_mode`,
  `launch_quantization`, `legato`, `velocity_amount`, `signature_numerator/denominator`,
  `playing_position`, `start_time`, `end_time`, `quantize(grid, amount)` (RecordingQuantization
  grid), `crop()`, `duplicate_loop()`, `duplicate_region(region_start, region_length,
  destination_time, pitch=-1, transposition_amount=0)`. MIDI: `add_new_notes(iterable of
  Live.Clip.MidiNoteSpecification(pitch, start_time, duration, velocity=100.0, mute=False,
  probability=1.0, velocity_deviation=0.0, release_velocity=64.0))` → note ids;
  `get_notes_extended(from_pitch, pitch_span, from_time, time_span)`, `get_all_notes_extended()`,
  `get_notes_by_id(ids)`, `get_selected_notes_extended()` → **`MidiNoteVector`** of notes with
  `note_id, pitch, start_time, duration, velocity, mute, probability, velocity_deviation,
  release_velocity`; `apply_note_modifications(vector)` (pass the returned vector, modified in
  place); `remove_notes_extended(...)`, `remove_notes_by_id(ids)`, `select_all_notes()`,
  `deselect_all_notes()`, `duplicate_notes_by_id(ids, destination_time=None, transposition_amount=0)`.
  Audio (raise on MIDI clips): `warping`, `warp_mode`, `warp_markers`, `gain` (0–1),
  `gain_display_string`, `pitch_coarse` (−48…48), `pitch_fine` (−50…49), `file_path`,
  `sample_length`, `ram_mode`. Automation (session clips only; None for arrangement clips and
  other tracks' parameters): `automation_envelope(p)`, `create_automation_envelope(p)`,
  `automation_envelopes`, `clear_envelope(p)`, `clear_all_envelopes()`; `Live.Envelope.Envelope`:
  `value_at_time(t)`, `insert_step(time, length, value)`, 12.x `events_in_range`,
  `create_event`, `delete_events_in_range` (inclusive). `value_at_time` on a step border
  returns the value before it; `EnvelopeEvent.value` is in internal units (Hz, linear gain).
  Unlooped clips play `loop_start..loop_end` (`start_marker` follows `loop_start`);
  same-pitch notes never overlap (`add_new_notes` merges and returns surviving ids only).
  Arrangement track automation lanes are not reachable (recorded in real time by
  `automation.record`); an arrangement copy may or may not carry the source clip's envelopes
  (12.4.5: kept on a track without devices, dropped with an instrument on it).
  `Clip.View`: `show_envelope()`, `hide_envelope()`, `select_envelope_parameter(p)`,
  `grid_quantization` (`GridQuantization`), `grid_is_triplet`, `show_loop()`.
- **Device**: `name` (rw), `class_name`, `class_display_name`, `type`, `is_active` (**ro** —
  toggle via `parameters[0]` "Device On"), `parameters`, `can_have_chains`, `can_have_drum_pads`,
  `view.is_collapsed`, `store_chosen_bank(bank, preset)`; RackDevice: `chains`, `return_chains`,
  `drum_pads` (128, topmost drum rack only), `visible_drum_pads` (16), `insert_chain(Index=-1)`
  (12.3+), `copy_pad(src, dst)`, `macros_mapped`, `visible_macro_count`, `add_macro()`,
  `remove_macro()`, `variation_count`, `selected_variation_index`, `store_variation()`,
  `recall_selected_variation()`, `delete_selected_variation()`, `randomize_macros()`; Chain:
  `devices`, `mixer_device`, `mute` (= `mixer_device.chain_activator`), `solo`,
  `insert_device(DeviceName, DeviceIndex=-1)` (12.3+); DrumChain: `in_note` (12.3+, **0..127
  only** — no "All Notes"; new Drum Rack chains land on 36), `out_note` 0..127, `choke_group`
  0..16; DrumPad: `note`, `name` (ro: chain name / "Multi" / note name), `chains`,
  `mute`, `solo`, `delete_all_chains()`; PluginDevice (class_name `PluginDevice` for VST2/VST3,
  `AuPluginDevice` for AU): `presets` (only `["Default"]` for Serum 2 and Apple AUs),
  `selected_preset_index`, `is_editor_open` (rw), `get_parameter_names(begin=0, end=-1)`
  (**every** plug-in parameter, never "Device On"; the list changes with the plug-in's mode)
  while `parameters` holds only "Device On" + the "Configure" list — empty for big plug-ins
  (Serum 2 VST3/AU: 0 of ~2600) until the user configures it or a generated rack preset exposes
  them (`docs/PLUGIN_RACKS.md`: any VST3 parameter by ParameterId, max 128 per rack — more
  `PluginParameterSettings`, or a macro index with an empty `MidiControllerRange` slot, crash
  Live; rack names come from the file name, `UserName` is ignored; rewriting an indexed preset
  file reloads at once; hot-swapping keeps the plug-in instance); `save_preset_to_compare_ab_slot()`
  (only with `can_compare_ab`); SimplerDevice: `sample` (None when empty; `file_path`,
  `length`, `slices` in frames, `insert_slice(t)`, `warp_markers`, `gain_display_string()`),
  `playback_mode`, `slicing_playback_mode`, `crop()`, `reverse()`, `warp_as(beats)`,
  `guess_playback_length()`, `replace_sample(path)` (12.x); DeviceParameter: `name` (ro),
  `original_name`, `value`, `display_value` (12), `min`, `max`, `default_value` (non-quantized
  only), `is_quantized`, `value_items` (quantized only), `str_for_value(v)`, `is_enabled`,
  `automation_state`, `re_enable_automation()`, `state`.
- **Browser**: `app.browser` roots `audio_effects`, `midi_effects`, `instruments`, `sounds`,
  `drums`, `plugins`, `samples`, `packs`, `user_library`, `current_project`, `max_for_live`,
  `clips` (BrowserItems) plus list roots `user_folders`, `colors` (and empty
  `legacy_libraries`); no `splice` root. Items: `name`, `uri`, `children` (lazy, slow), `is_folder`,
  `is_device`, `is_loadable`, `is_selected`, `source`, `iter_children` (a **property**); no
  `canonical_parent`. `browser.load_item(item)` loads onto the selected track at
  `track.view.device_insert_mode` (or replaces `browser.hotswap_target`, which is writable);
  sample loading via the browser is UNVERIFIED — prefer `create_audio_clip` /
  `replace_sample`. Also `preview_item(item)`, `stop_preview()`, `relation_to_hotswap_target(item)`
  (Relation: 3 = none), `filter_type`. A set `hotswap_target` filters the browser roots (setting the same target
  again raises "Couldn't set hotswap target"); `.alc` loads create a new track; sample/clip
  loads into clip slots do nothing while the Arrangement view is focused.
  Search = bounded recursive walk with a cache keyed by uri. Plug-in items are `is_loadable` but
  `is_device` False; `browser.plugins` folders are `AUv2/<vendor>`, `VST` (flat, uri
  `#VST:Local:`) and `VST3/<vendor>`.
- **Application view**: `app.view.show_view(name)`, `hide_view`, `focus_view`,
  `is_view_visible(name, main_window_only=True)`, `zoom_view(direction, view_name,
  modifier_pressed)`, `scroll_view(direction, view_name, modifier_pressed)` (all 3 args required,
  direction 0 up/1 down/2 left/3 right; `zoom_view(3, "Arranger", …)` zooms time in, 2 out), `toggle_browse()`, `available_main_views()`,
  `focused_document_view` (ro), `browse_mode` (ro); names: `"Session"`, `"Arranger"`, `"Detail"`,
  `"Detail/Clip"`, `"Detail/DeviceChain"`, `"Browser"` (`""` = visible main view).
- **Scene**: `name`, `color_index`, `is_empty`, `is_triggered`, `tempo`/`tempo_enabled`,
  `time_signature_numerator/denominator` (−1 while disabled), `time_signature_enabled` (rw),
  `fire(force_legato=False,
  can_select_scene_on_launch=True)`, `fire_as_selected(force_legato=False)`.
- Not available through the LOM (document, don't fake): saving the set, audio export/render
  (real-time resampling instead: `record.resample`), freeze/flatten, grouping tracks, drawing
  arrangement track automation (recorded instead: `automation.record`), plugin GUI knobs not in
  Configure (generated rack presets instead: `plugin_racks.expose`), menu commands,
  preferences, dialog button labels. `docs/LIVE_API_NOTES.md` §12 lists every limit with its
  workaround. These are candidates for the Ableton Extensions SDK
  layer (Suite, phase 2).

## 11. Testing

- `tests/live_stub/Live/…` fakes the modules above with plain Python classes that behave like the
  real ones (collections as read-only `Vector`s, properties as attributes,
  `MidiNoteSpecification`, `DeviceParameter` with min/max/quantized) and the real-Live behaviour
  measured on 2026-09-10 (`docs/LIVE_API_VERIFIED.md` §19.1); Live's next-tick deferral is
  switched on per test by `tests/live_stub_ext/transport_live.py`. `conftest.py` installs it on `sys.path` before importing the Remote
  Script, and provides a `song` fixture with 3 tracks, devices with parameters, clips with notes,
  scenes, returns and a browser tree.
- Handlers are tested by calling the dispatcher end‑to‑end (`dispatch({"cmd": ..., "args": ...})`)
  so the registry/serializer are covered too.
- MCP tools are tested by starting `tests/fake_bridge.py` (real TCP, real protocol) that answers
  with canned/echoed results, then calling tools via `app.call_tool(name, args)`.
- `python -m pytest -q tests` must pass with the repo `.venv` (`.venv/bin/python`).

## 12. Code style

Python, 4‑space indent, type hints in the MCP server (Python ≥3.10), *no* third‑party imports in
`remote_script/`, docstrings on every command and tool, no prints (use `ctx.log`/`logging`),
explicit errors, no silent `except: pass`. Keep files focused; a module file over ~600 lines should
be split into the sibling files listed in §2. (Known debt: several handler files — `devices.py`,
`browser.py`, `automation.py`, `clips.py`, `notes.py`, `transport.py`, `samples.py` — are
1000–2000 lines, mostly docstrings; private `_<name>.py` siblings are skipped by the registry
and are the sanctioned place for a future split.)
