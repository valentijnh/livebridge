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
│   └── handlers/                      # ONE FILE PER MODULE, auto-discovered
│       ├── __init__.py                # empty (registry uses pkgutil to import every module here)
│       ├── system.py   lom.py   eval.py                     # core (Fundament phase)
│       ├── transport.py scenes.py view.py                   # module A
│       ├── tracks.py  mixer.py  routing.py  record.py       # module B
│       ├── clips.py   notes.py  arrangement.py              # module C
│       ├── devices.py plugins.py racks.py                   # module D
│       ├── browser.py samples.py                            # module E
│       └── automation.py cues.py                            # module F
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
│           ├── browser.py samples.py splice.py              # module E
│           └── automation.py cues.py                        # module F
├── installers/  install.py  install.sh  install.ps1  uninstall.py
├── docs/        ARCHITECTURE.md PROTOCOL.md TOOLS.md INSTALL.md TROUBLESHOOTING.md LIVE_API_NOTES.md
├── tests/
│   ├── live_stub/                     # fake `Live` package: Live.Application, Live.Song, Live.Track, Live.Clip, Live.Device, Live.Browser, Live.Base ...
│   ├── conftest.py                    # puts tests/live_stub and remote_script on sys.path; fixtures: song, bridge, mcp_app
│   ├── fake_bridge.py                 # in-process TCP server speaking PROTOCOL.md for MCP tests
│   ├── test_core.py test_mcp_core.py
│   ├── test_<module>.py               # one per handler/tool module
│   └── integration_check.py           # run against real Live; prints PASS/FAIL per capability
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
- The main thread drains the queue from two places: `LiveBridge.update_display()` (Live calls it
  ~every 100 ms) **and** `self.schedule_message(0, self._drain)` requested by the socket thread
  right after enqueueing (lower latency). Draining is idempotent and bounded (max N commands or
  ~40 ms per drain to keep the UI responsive; a long command still runs to completion).
- The socket thread waits on the Event with the request's timeout (default 10 s, max 120 s).
  If it times out it returns `error.type = "timeout"` but the command may still execute later.
- Every handler runs inside `try/except`; exceptions become `error` envelopes with `type`,
  `message`, `traceback`. The script must never crash Live and never leave the queue stuck.
- `song.begin_undo_step()/end_undo_step()` wrap every mutating command so each command is one
  undo step (handler declares `mutating=True` in the decorator).
- `disconnect()` stops the server, closes sockets, stops the beacon. Reload‑safe.

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
  (the ControlSurface instance).
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
index, a track name (exact then case‑insensitive prefix), or a full path.

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
  "beacon": true, "beacon_port": 9881, "name": "Valentijn-PC", "max_clients": 4 }
```

- `host` = `127.0.0.1` (same machine) or `0.0.0.0` (LAN mode). The token is **required** in LAN
  mode and checked on every request; on localhost a missing token is accepted only if config token
  is empty.
- The UDP beacon broadcasts `{"livebridge":1,"name":...,"port":9880,"live":"12.4.5","host":<ip>}`
  every 2 s to `255.255.255.255:9881` when enabled. The MCP server's `discovery.py` listens for 3 s
  to list instances; tool `livebridge_connect(host, port, token)` switches at runtime.
- `allow_eval` gates `eval.python` (arbitrary Python inside Live). Keep enabled for the owner but
  always require the token for it.

MCP server config precedence: CLI args > env (`LIVEBRIDGE_HOST`, `LIVEBRIDGE_PORT`,
`LIVEBRIDGE_TOKEN`, `LIVEBRIDGE_TIMEOUT`) > `~/.livebridge/config.json` > defaults.

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

- FastMCP (`from mcp.server.fastmcp import FastMCP`), stdio transport. `create_app(bridge)` in
  `server.py` auto‑imports `tools/*.py` and calls `register(mcp, bridge)` on each.
- Tool naming: `live_<area>_<verb>` snake_case, e.g. `live_transport_play`, `live_tracks_list`,
  `live_clip_add_notes`, `live_device_set_parameter`, `live_browser_load`, `live_lom_get`. Keep the
  count reasonable (target ~90–130 tools total) by preferring rich tools with optional args over
  dozens of tiny ones. Every tool has a precise docstring (what, args, returns, gotchas) — Claude
  reads these.
- Tools return compact text/JSON; large results (notes, parameter lists) support paging or
  filtering args. Errors from the bridge are returned as clear messages (`Live is not reachable at
  host:port — is Live running with the LiveBridge control surface enabled?`), never stack traces.
- Higher‑level composite tools live next to primitives where it saves Claude round‑trips, e.g.
  `live_track_create_with_instrument(name, instrument_query)`, `live_clip_write_pattern(...)`,
  `live_sample_import(file_path, track, mode="session"|"arrangement")`,
  `live_set_snapshot()` (whole set summary in one call).
- `live_lom_get/set/call/describe/children` + `live_eval_python` are the escape hatches so
  *anything* in the LOM is reachable even without a curated tool.
- Splice: not reimplemented. `tools/splice.py` documents the official remote MCP
  (`https://mcp.splice.com/mcp`) and provides `live_splice_import_downloaded(path_or_folder, track,
  mode)` which watches/imports the newest sample(s) from a download folder into Live, and
  `live_browser_search("splice", ...)` covers Live 12.3+'s built‑in Splice browser folder if present.

## 10. Live API notes (verify with feature detection — `compat.has(obj, name)`)

- Live 12 embeds Python 3.11. Remote Script root: `from _Framework.ControlSurface import ControlSurface`
  (or `ableton.v2.control_surface.ControlSurface`); `self.song()`, `self.application()`,
  `self.schedule_message(delay_ticks, callback)`, `self.log_message()`, `update_display()`,
  `disconnect()`. `Live.Application.get_application()` gives version via `get_major_version()`,
  `get_minor_version()`, `get_bugfix_version()`.
- Song: `tempo`, `signature_numerator/denominator`, `is_playing`, `start_playing()`, `stop_playing()`,
  `continue_playing()`, `current_song_time`, `loop`, `loop_start`, `loop_length`, `metronome`,
  `record_mode`, `session_record`, `arrangement_overdub`, `overdub`, `punch_in/punch_out`,
  `back_to_arranger`, `re_enable_automation()`, `stop_all_clips()`, `tap_tempo()`, `undo()/redo()`,
  `can_undo/can_redo`, `begin_undo_step()/end_undo_step()`, `capture_midi()`, `create_midi_track(index)`,
  `create_audio_track(index)`, `create_return_track()`, `delete_track(index)`, `duplicate_track(index)`,
  `create_scene(index)`, `delete_scene(index)`, `duplicate_scene(index)`, `tracks`, `visible_tracks`,
  `return_tracks`, `master_track`, `scenes`, `cue_points`, `set_or_delete_cue()`, `jump_to_next_cue()`,
  `scale_name/root_note` (Live 12 scale awareness), `groove_pool`, `view` (Song.View: `selected_track`,
  `selected_scene`, `highlighted_clip_slot`, `detail_clip`, `select_device(device)`, `follow_song`,
  `draw_mode`).
- Track: `name`, `color_index`, `color`, `mute`, `solo`, `arm`, `can_be_armed`, `has_midi_input`,
  `has_audio_input/output`, `is_foldable`, `fold_state`, `is_grouped`, `group_track`, `devices`,
  `clip_slots`, `arrangement_clips`, `mixer_device` (`volume`, `panning`, `sends`, `track_activator`,
  `crossfader_assign`), `current_monitoring_state`, `input_routing_type/channel`,
  `available_input_routing_types/channels`, `output_routing_type/channel`, `playing_slot_index`,
  `fired_slot_index`, `delete_device(index)`, `create_midi_clip(start_time, length)` (arrangement),
  `create_audio_clip(file_path, position)` (arrangement, Live 11+), `duplicate_clip_to_arrangement(clip,
   time)`, `delete_clip(clip)`, `stop_all_clips()`, `view.select_instrument()`, `is_frozen`.
- ClipSlot: `has_clip`, `clip`, `create_clip(length)` (MIDI), `delete_clip()`, `fire()`, `stop()`,
  `duplicate_clip_to(slot)`, `is_playing`, `is_recording`, `is_triggered`, `has_stop_button`.
- Clip: `name`, `color_index`, `is_midi_clip`, `is_audio_clip`, `length`, `loop_start`, `loop_end`,
  `start_marker`, `end_marker`, `looping`, `muted`, `is_playing`, `is_recording`, `fire()`, `stop()`,
  `signature_numerator`, `playing_position`, `start_time` (arrangement), `end_time`, `quantize(grid,
  amount)`, `crop()`, `duplicate_loop()`, `duplicate_region(...)`, MIDI: `add_new_notes(tuple of
  Live.Clip.MidiNoteSpecification(pitch=, start_time=, duration=, velocity=, mute=, probability=,
  velocity_deviation=, release_velocity=))`, `get_notes_extended(from_pitch, pitch_span, from_time,
  time_span)` → objects with `note_id, pitch, start_time, duration, velocity, mute, probability,
  velocity_deviation, release_velocity`, `apply_note_modifications(vector)`, `remove_notes_extended(
  from_pitch, pitch_span, from_time, time_span)`, `select_all_notes()`, `deselect_all_notes()`,
  `get_selected_notes_extended()`; Audio: `warping`, `warp_mode`, `warp_markers`, `gain`,
  `gain_display_string`, `pitch_coarse`, `pitch_fine`, `file_path`, `sample_length`, `ram_mode`;
  automation: `automation_envelope(parameter)`, `create_automation_envelope(parameter)`,
  `clear_envelope(parameter)`, `clear_all_envelopes()`; envelope: `value_at_time(t)`, `insert_step(
  time, length, value)`.
- Device: `name`, `class_name`, `class_display_name`, `type` (`Live.Device.DeviceType`), `is_active`,
  `parameters`, `can_have_chains`, `chains`, `return_chains`, `can_have_drum_pads`, `drum_pads`,
  `view.is_collapsed`, `store_chosen_bank()`; RackDevice: `chains`, `macros_mapped`, `variation_count`,
  `selected_variation_index`, `recall_selected_variation()`, `store_variation()`, `randomize_macros()`;
  PluginDevice: `presets`, `selected_preset_index` (parameters exposed are those in Live's
  “Configure” panel for VST/VST3/AU — document this limitation; Live 12 exposes more for VST3);
  SimplerDevice: `sample` (`file_path`, `length`, `slices`, `insert_slice()`, `warp_markers`),
  `playback_mode`, `slicing_playback_mode`, `crop()`, `reverse()`, `warp_as()`, `guess_playback_length()`;
  DrumPad: `note`, `name`, `chains`, `mute`, `solo`; DeviceParameter: `name`, `original_name`, `value`,
  `min`, `max`, `default_value`, `is_quantized`, `value_items`, `str_for_value(v)`, `is_enabled`,
  `automation_state`, `re_enable_automation()`, `state`.
- Browser: `app.browser` with roots `audio_effects`, `midi_effects`, `instruments`, `sounds`, `drums`,
  `plugins`, `samples`, `packs`, `user_library`, `current_project`, `max_for_live`, `clips`,
  `colors`; items have `name`, `uri`, `children` (lazy), `is_folder`, `is_device`, `is_loadable`,
  `source`; `browser.load_item(item)` loads onto the selected track / highlighted slot
  (`browser.hotswap_target`, `relation_to_hotswap_target`, `preview_item(item)`, `stop_preview()`).
  Searching = recursive walk with depth/result limits and a cache keyed by uri; expose
  `browser.search(query, root=None, limit=…)` and `browser.load(uri | path)`.
- Application view: `app.view.show_view(name)`, `hide_view`, `focus_view`, `is_view_visible`,
  `zoom_view(direction, view_name, modifier)`, `scroll_view(...)`, `toggle_browse()`; names:
  `"Session"`, `"Arranger"`, `"Detail"`, `"Detail/Clip"`, `"Detail/DeviceChain"`, `"Browser"`.
- Not available through the LOM (document, don't fake): audio export/render, freeze/flatten,
  plugin GUI knobs not in Configure, menu commands, file→save (Live 12 has `song.save()`? — verify;
  otherwise document). These are candidates for the Ableton Extensions SDK layer (Suite, phase 2).

## 11. Testing

- `tests/live_stub/Live/…` fakes the modules above with plain Python classes that behave like the
  real ones (vectors as lists, properties as attributes, `MidiNoteSpecification`, `DeviceParameter`
  with min/max/quantized). `conftest.py` installs it on `sys.path` before importing the Remote
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
be split into the sibling files listed in §2.
