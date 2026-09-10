# LiveBridge build report

**Date:** 2026-09-10. **Verified on:** macOS arm64 (MacBook Air), Ableton Live 12.4.5 Suite
(Python 3.11.6 inside Live), development venv Python 3.13.1 with `mcp` 2.2.0.
**Versions:** Remote Script `1.0.0` (protocol 1), MCP server `livebridge-mcp 0.1.0`,
installer `0.1.0`.

LiveBridge lets Claude control Ableton Live 12. It has two halves:

- **Remote Script** (`remote_script/LiveBridge`): a MIDI Remote Script that runs inside Live. It
  uses the standard library only and serves the bridge commands over a JSON-lines TCP socket
  (port 9880), with a token.
- **MCP server** (`mcp_server/livebridge_mcp`): a FastMCP server over stdio. It turns the bridge
  commands into `live_*` tools for Claude Desktop and Claude Code.

The spec is in [ARCHITECTURE.md](ARCHITECTURE.md) and [PROTOCOL.md](PROTOCOL.md).

## 1. What was built

| Part | Count | Where |
|---|---|---|
| Bridge commands (Remote Script) | **200** in 23 namespaces, 103 of them mutating (one undo step each) | `remote_script/LiveBridge/handlers/*.py` (21 handler modules, auto-discovered) |
| MCP tools | **166** in 21 tool modules, all strict (unknown arguments are rejected) | `mcp_server/livebridge_mcp/tools/*.py` (auto-discovered) |
| Unit and integration tests | **1505** tests in 32 test files, run against a fake `Live` package | `tests/`, `tests/live_stub`, `tests/live_stub_ext` |
| Installers | `install.py` / `uninstall.py` (macOS + Windows), `install.sh`, `install.ps1`, `bundle.py`, `gen_tools_doc.py` | `installers/` |
| Claude skill | a production-workflow skill for Claude Code and a ZIP for Claude Desktop; also served as the MCP resource `livebridge://skill` and the prompt `livebridge_workflow` | `.claude/skills/livebridge/SKILL.md` |
| Shipped plug-in maps | Serum 2 and Serum 2 FX (VST3): 541 parameters each | `remote_script/LiveBridge/plugin_maps/` |

Code size, roughly: Remote Script 31k lines, MCP server 12k, installers 2.6k, tests 29k.

Bridge commands per namespace: arrangement 13, automation 11, browser 7, clips 15, cues 10,
devices 18, eval 1, lom 5, mixer 6, notes 12, plugin_racks 3, plugins 6, racks 9, record 9,
routing 4, samples 6, scenes 11, simpler 3, song 2, system 9, tracks 10, transport 16, view 14.

What the tools cover:

- **Session and connection:** `live_status`, `live_set_snapshot`, `live_discover` (UDP beacon)
  and `live_connect` (switch machines at runtime).
- **Transport:** tempo, time signature, metronome, loop, quantization, scale, undo and redo.
- **Tracks, mixer and routing:** create, rename, colour and arm tracks. Volume, pan and sends
  in dB. Buses, side-chains, resampling, level meters.
- **Clips and notes:** step patterns, chords and arpeggios from symbols or Roman numerals.
  Quantize, humanize, transpose, warp markers, audio to MIDI.
- **Arrangement and locators:** scenes to arrangement, sections, clip duplicate, resize and move,
  and bars/beats conversion.
- **Devices, racks, Simpler and Drum Rack:** any parameter by name or by display text ("-12 dB",
  "1/8"), macros, drum pads, hot-swap.
- **Plug-ins:** listing, programs, exposed parameters, and full VST3 control through generated
  rack presets (section 5).
- **Browser, samples and Splice:** load anything by name, import files (also across the LAN),
  and import Splice downloads.
- **Automation:** clip envelopes and LFO shapes, and real-time arrangement automation recording.
- **View and dialogs:** show what Claude is working on, follow the user's selection, read and
  press Live's dialogs.
- **Escape hatches:** `live_lom_*`, `live_eval_python`, `live_commands`, `live_command_call` and
  `live_command_batch` (many commands as one undo step).

The full reference is [TOOLS.md](TOOLS.md). It is generated from the code, and
`installers/gen_tools_doc.py --check` passes, so it is current.

## 2. Test results

### Unit and integration tests (fake Live)

| Check | Result |
|---|---|
| `.venv/bin/pip install -e mcp_server` | OK |
| `.venv/bin/python -m pytest -q tests` | **1503 passed, 2 skipped, 0 failed** (about 4 min). Skips: `test_consistency.py:245` needs a file to arrive and is covered in `test_splice`; `test_installer.py:1238` needs PowerShell, which is not installed on this Mac. |
| `python -m compileall -q remote_script` | OK, no errors |
| Non-stdlib imports in `remote_script/` | **None.** The only non-stdlib names are Live's own `Live`, `_Framework` and `ableton` modules. `aifc` (removed in Python 3.13) and `winreg` (Windows only) are imported inside `try/except`. |
| `installers/gen_tools_doc.py --check` | OK: TOOLS.md matches the code |

### Installer dry-runs (this Mac, nothing written)

| Run | Result |
|---|---|
| `HOME=$(mktemp -d) install.py --dry-run --network --splice` | exit 0, **0 files written** in the temp HOME. The plan: Remote Script into `~/Music/Ableton/User Library/Remote Scripts` in LAN mode (0.0.0.0:9880, token generated, beacon on), venv + editable `pip install` in `~/.livebridge/venv`, Claude Desktop skipped because it is not installed, the `claude mcp add` commands for livebridge and Splice printed, skill + ZIP, and a firewall and macOS Local Network checklist. The skill path follows `$CLAUDE_CONFIG_DIR`, which is set in this shell, and nothing was written there either. |
| Windows branch (`Host(platform="win32")` injected with a fake runner) `--dry-run --network --splice` | exit 0, 0 files written. Windows paths: `Documents\Ableton\User Library\Remote Scripts`, `venv\Scripts\python.exe`, `livebridge-mcp.exe`, `py` in the verify step. The Windows firewall hint and the `New-NetFirewallRule` pointer are printed. |
| Windows `--dry-run --pair 192.168.1.20 --token … --no-remote-script` | exit 0: the MCP server points at 192.168.1.20:9880 and the steps for inbound UDP 9881 (`live_discover`) are printed |
| macOS `--dry-run --pair 192.168.1.30 --token …` | exit 0: the paired plan plus the macOS 15 Local Network permission hint |
| `--pair` without `--token` / with a 6-character token | refused with a clear message (exit 1). Tokens must be 8–256 characters. |

### Real Live on this Mac (Live 12.4.5 Suite)

| Check | Result |
|---|---|
| `tests/live_query.py hello` | OK: LiveBridge 1.0.0, protocol 1, Live 12.4.5 suite, Python 3.11.6, 200 commands, token required |
| `tests/live_dev.py sync` | OK: all 21 handler modules and the helpers reloaded, 0 failures, 200 commands, `restart_live_needed_for: []` (so no restart was needed) |
| `tests/integration_check.py` | **32 PASS, 0 FAIL, 6 SKIP**. The skips are read commands that need a clip or a device, and the scratch set has none. The write path passed: temp MIDI track, clip, notes, Operator from the browser, a parameter by display text, fire/stop, cleanup. |
| `tests/integration_check.py --scenario beat --scenario arrangement --no-play` | **52 PASS, 0 FAIL, 6 SKIP** |
| **Serum 2 smoke test** (MCP tools in-process against the real bridge) | **PASS**, see below |

Serum 2 smoke test, run through `create_app()` tools:

1. `live_plugin_expose(plugin="Serum 2", parameters=["sound_design"], new_track=true,
   track_name="LB_VERIFY_SERUM", editor_open=false)`: `status: done` in 1.05 s. Serum 2 VST3
   loaded in the generated "Serum 2 Rack", shipped map, **120 parameters exposed**. Device
   `song.tracks[4].devices[0].chains[0].devices[0]`.
2. `live_device_set_parameter(device=…, parameter="Filter 1 Freq", value="800 Hz")`: matched by
   `display`, previous "1011 Hz" (the Init patch), new value 0.58.
3. `live_device_get_parameter(...)` read back **"800 Hz"** (value 0.58).
4. `live_tracks_delete("LB_VERIFY_SERUM")`: deleted. The track list afterwards was identical to
   the one before (1-MIDI, 2-MIDI, 3-Audio, 4-Audio).

The earlier per-area real-Live runs (166 commands: 87 PASS, 72 FIXED, 7 UNSUPPORTED, 0 FAIL)
are in [LIVE_TEST_REPORT.md](LIVE_TEST_REPORT.md). The plug-in rack experiments, crash limits
and the full Serum 2 end-to-end proof are in [PLUGIN_RACKS.md](PLUGIN_RACKS.md).

### Documentation spot-check

- Every `live_*` name in TOOLS.md, README.md, INSTALL.md, NETWORK.md, TROUBLESHOOTING.md and
  `mcp_server/README.md` is a real tool. The only other matches are paths and script names
  (`live_stub`, `live_query`, `live_test`) and the `live_simpler_*` wildcard. All 166 tools are
  in TOOLS.md.
- These 14 tools have TOOLS.md argument tables that match their live schema exactly:
  `live_plugin_expose`, `live_plugin_param_map`, `live_plugin_configure`, `live_status`,
  `live_connect`, `live_discover`, `live_tracks_create`, `live_clip_add_notes`,
  `live_device_set_parameter`, `live_splice_import_downloaded`, `live_splice_setup_info`,
  `live_sample_import`, `live_browser_load`, `live_mixer_set`.
- Every installer flag the docs use exists: `--network`, `--no-network`, `--local`, `--pair`,
  `--token`, `--new-token`, `--port`, `--dry-run` and `--no-skill` in `install.py`;
  `--keep-config`, `--keep-skill` and `--remove-splice` in `uninstall.py`. `--uninstall` is
  handled by `install.sh` / `install.ps1`.

## 3. Installing

Requirements: Ableton Live 12, Python 3.10+ for the MCP server (the installer runs on 3.9+;
`--uv` can fetch Python), and Claude Desktop and/or Claude Code. Node.js (`npx`) is only needed
for Splice in Claude Desktop's JSON config. Full details are in [INSTALL.md](INSTALL.md).

### macOS

```bash
git clone <repository> LiveBridge && cd LiveBridge     # or unzip a bundle from installers/bundle.py
./installers/install.sh --dry-run                     # optional: see the plan
./installers/install.sh                               # or: python3 installers/install.py
```

### Windows (PowerShell)

```powershell
git clone <repository> LiveBridge; cd LiveBridge
powershell -ExecutionPolicy Bypass -File installers\install.ps1 --dry-run
powershell -ExecutionPolicy Bypass -File installers\install.ps1      # or: py installers\install.py
```

### Then, on both

1. Restart Live (it scans Remote Scripts at startup only).
2. Preferences → Link, Tempo & MIDI → Control Surface → **LiveBridge**, Input/Output None. The
   status bar shows `LiveBridge … ready on port 9880`.
3. Quit and reopen Claude Desktop, or open a new Claude Code session. Ask for `live_status`.
4. Claude Desktop only: Settings → Capabilities → Skills → Upload
   `~/.livebridge/livebridge-skill.zip`.

The installer is idempotent. Run it again after an update: it keeps the token, the port, LAN
mode, `allow_eval` and the paired host. To remove LiveBridge, run `installers/uninstall.py`
(`--keep-config`, `--keep-skill`, `--remove-splice`). To use fewer tools in context, pass
`livebridge-mcp --toolsets minimal|core|production`, or set `LIVEBRIDGE_TOOLSETS` or
`"toolsets"` in `~/.livebridge/config.json`.

## 4. LAN pairing: Mac and Windows PC

The Live machine listens on the LAN. The Claude machine points its MCP server at it. Example:
Live on the Windows PC at 192.168.1.20, Claude on the Mac.

1. **On the PC (Live):**
   `powershell -ExecutionPolicy Bypass -File installers\install.ps1 --network`.
   It prints the token, the PC's IP and the exact pairing command. Allow Live on **Private**
   networks when Windows asks (incoming TCP 9880), then restart Live.
2. **On the Mac (Claude):** `./installers/install.sh --pair 192.168.1.20 --token <token>`.
   On macOS 15+, allow Claude Desktop (or the terminal that runs Claude Code) under Privacy &
   Security → Local Network. Without it the connection fails with "No route to host".
3. Ask Claude for `live_status`. It should say `connected: true` and show the PC's Live version.

For the reverse direction, swap the roles: `--network` on the Mac and `--pair <mac-ip>` on the
PC. On the Mac, Live itself also needs the Local Network permission for its discovery beacon.

At runtime, `live_discover` listens for Live's UDP beacon on port 9881 (the Claude machine needs
inbound UDP 9881 for this, or use `live_connect` with the IP instead).
`live_connect(host, port, token, persist=true)` switches machines. Samples and Splice files from
the Claude machine are sent across into `<User Library>/Samples/LiveBridge` on the Live machine.
`install.py --local` makes a machine standalone again. Both machines need the same LiveBridge
version: use `installers/bundle.py` when there is no shared git remote. Details are in
[NETWORK.md](NETWORK.md).

## 5. Splice

LiveBridge does not reimplement Splice. The installer registers **Splice's official remote MCP**
(`https://mcp.splice.com/mcp`) next to LiveBridge:

- Claude Code: `claude mcp add --transport http --scope user splice https://mcp.splice.com/mcp`,
  then sign in through `/mcp`.
- Claude Desktop: via `npx mcp-remote`, or Settings → Connectors → Add custom connector.
- `--no-splice` skips it.

The flow:

1. Claude searches Splice with the Splice tools. Search is free.
2. Claude downloads a sound. Downloads need a paid plan and are limited to 100 per 24 h.
3. `live_splice_import_downloaded(files=[<paths Splice reported>])` puts the files into a
   session slot, the arrangement, a Simpler or a Drum Rack pad. In LAN mode the files are sent
   to the Live machine first.
4. For sounds downloaded by hand in the Splice app, use `live_splice_watch_folder`.
   `live_splice_setup_info` shows the detected folders.

## 6. Serum 2 and other plug-ins

Live exposes only the parameters in a plug-in's **Configure** list, and for big plug-ins that
list starts empty (Serum 2: 0 of 2623). Live's API cannot add to it. LiveBridge works around
this by **writing a rack preset** (`.adg`) that lists the wanted VST3 ParameterIds, then loading
it through Live's browser. Details are in [PLUGIN_RACKS.md](PLUGIN_RACKS.md).

- `live_plugin_expose(plugin="Serum 2", parameters=["sound_design"], new_track=true)` exposes
  the curated set of 120 controls, or any names or groups (`"filter 1"`, `"osc a"`, `"lfo"`, …),
  up to **128 per rack**. Optional extras: `macros={"1": "Filter 1 Freq"}`, `preset_file=`
  (a `.vstpreset` or a Live preset), `replace`, `editor_open`.
- Then use `live_device_set_parameters` / `live_device_set_parameter` with display strings
  ("800 Hz", "20 ms", "-6 dB", "1/8"), and `live_automation_write` / `live_automation_record`.
- `live_plugin_preset_files(plugin="Serum 2")` lists starting sounds on disk (626 factory
  presets on this Mac).
- For other VST3 plug-ins, run `live_plugin_param_map(plugin=...)` once. It probes and caches the
  name → id map (about 10–30 s). Serum 2 and Serum 2 FX ship with a map.
- **Configure fallback** for AU / VST2 or ids that cannot be probed:
  `live_plugin_configure(parameters=[...], wait_seconds=…, open_editor=true)` reports which wanted
  parameters Live already exposes. It can wait while the user adds the rest with the device's
  Configure button. `live_plugin_parameters` / `live_plugin_set` then work on whatever is exposed.

Limits:

- VST3 only. An AU rack loads but exposes nothing.
- At most 128 parameters per rack (256 crashed Live).
- Re-exposing restarts the sound from `preset_file` or Serum's Init patch. Live cannot read a
  plug-in's state, so expose first, then design.
- `.SerumPreset` files cannot be embedded.
- The plug-in rack route is unit-tested for Windows but has only been run in real Live on macOS.

## 7. Known limitations

Most of these are limits of Live's Python API, not of LiveBridge (see
[LIVE_API_NOTES.md](LIVE_API_NOTES.md) and [LIVE_API_VERIFIED.md](LIVE_API_VERIFIED.md)).

- **Export, freeze and menus:** there is no audio export, no freeze/flatten and no saving the set,
  and no menu commands or preferences. Real-time bouncing through `live_record_resample` is the
  workaround.
- **Racks:** no macro mapping on existing racks, no deleting or reordering chains, no key or
  velocity zones, no saving presets. Sampler zones and the Drum Sampler's sample cannot be
  reached. (Generated plug-in racks do map macros, because they are written as presets.)
- **Automation:** it is drawn into session clips. Arrangement lanes can only be recorded in real
  time. When a clip is copied into the arrangement, its envelopes are dropped on tracks that hold
  an instrument (Live 12.4.5). LiveBridge reports this.
- **No audio listening:** Claude cannot hear the result. It works from the set's data (notes,
  parameters, names) and Live's level meters and CPU load (`live_mixer_meters`).
- **Plug-in UIs:** Claude cannot see or click inside a plug-in's window. It controls only
  exposed parameters. Plug-in preset browsers (e.g. `.SerumPreset`) must be used by hand.
- `live_device_insert` needs Live 12.3+. `live_eval_python` runs arbitrary Python inside Live:
  it is on by default for the owner, always behind the token, and can be switched off with
  `--no-allow-eval`.
- **Windows:** not yet run on a real Windows PC with Live. The Windows code paths are covered
  by unit tests with an injected host, plus the dry-run above. The PowerShell wrapper test was
  skipped because PowerShell is not installed on this Mac.

Minor items seen during this verification:

- **Version numbers differ:** the Remote Script reports `1.0.0`, while the MCP server and the
  installer are `0.1.0`. `live_status` shows both, so users may notice.
- **MCP server token on this Mac:** there is no `~/.livebridge/config.json` on this development
  Mac (the installer has not been run here; the Remote Script was installed by hand). Scripts
  that use the MCP server need `LIVEBRIDGE_TOKEN`; `tests/live_query.py` and
  `integration_check.py` read the token from the Remote Script's config instead.
- **`max_clients`:** the installed Remote Script `config.json` still has `"max_clients": 4`
  (the new default is 8).
- **Scratchpad `http.py`:** an `http.py` probe left in the shared scratchpad folder shadows the
  stdlib `http` module when scripts run from that folder. The smoke test used `python -P` to
  avoid it. This is not a product issue.

## 8. Next steps

1. **Real Windows run.** Install on the Windows PC with `--network`, pair the Mac with
   `--pair <pc-ip> --token …`, and run `tests/integration_check.py` and the Serum 2 smoke test
   there. This also checks the Windows VST3 scanning and preset paths, and the firewall prompts.
2. **Install on this Mac with the real installer** (`./installers/install.sh`) so that Claude
   Desktop / Claude Code get the MCP server and the skill, then check `live_status` from
   Claude.
3. **Commit the build.** `remote_script/`, `mcp_server/` and `tests/` are still untracked in
   git.
4. **Align the versions** of the Remote Script and the MCP server (both `0.1.0`, or both
   `1.0.0`).
5. Run the stub-only items from LIVE_TEST_REPORT "What you need to do" in a scratch set:
   `transport.undo` / `redo`, `tracks.set exclusive`, set-wide `stop_clips` / `mixer.reset`,
   and `tracks.group` fold.
6. Open points from LIVE_TEST_REPORT:
   - the monitoring default for new audio tracks;
   - sample-on-MIDI-track loads while the Arrangement view is focused.
7. More shipped plug-in maps (e.g. other common VST3 synths), so they skip the one-time probe.
