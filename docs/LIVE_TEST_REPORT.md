# LiveBridge — real-Live test report

**Live:** Ableton Live 12.4.5 Suite, macOS arm64, Python 3.11.6 inside Live.
**Date:** 2026-09-10.
**Method:** three testers worked in parallel on one scratch set: T1-global, T2-tracks-clips
and T3-devices-browser. Each called every bridge command of its areas through the running
LiveBridge (`tests/live_query.py`) with realistic arguments and edge cases. Each also called
every MCP tool of its areas in-process (`create_app(BridgeClient)`) against the real bridge.
Every tester touched only objects named `LB_<tester> …` and deleted them afterwards. After
the testers, a merge pass applied all requested shared changes, ran
`tests/integration_check.py` against the real Live and wrote this report.

Per-command detail with full notes: `docs/live_test/T1-global.md`,
`docs/live_test/T2-tracks-clips.md`, `docs/live_test/T3-devices-browser.md` and
`docs/live_test/T4-plugins.md` (plug-ins, run after the user enabled plug-ins); the generated
plug-in racks (Serum agent) are documented in `docs/PLUGIN_RACKS.md`.
The Live API facts learned from these runs are in `docs/LIVE_API_VERIFIED.md` §19.1.

Legend:
- **PASS**: works on real Live as documented.
- **FIXED**: real Live behaved differently. The handler, tool, docs and/or stub were corrected.
- **UNSUPPORTED**: could not be run here, either by rule or because this machine cannot.
- **FAIL**: still broken.

## Totals

| tester | areas | commands | PASS | FIXED | UNSUPPORTED | FAIL |
|---|---|---|---|---|---|---|
| T1-global | transport, song, scenes, view, cues, record | 58 | 28 | 28 | 2 | 0 |
| T2-tracks-clips | tracks, mixer, routing, clips, notes, arrangement, automation | 58 | 38 | 19 | 1 | 0 |
| T3-devices-browser | devices, simpler, plugins, racks, browser, samples (+ Splice tools) | 41 | 17 | 20 | 4 | 0 |
| T4-plugins | plugins (Serum 2 VST3/AU, Serum 2 FX, Apple AUs), plug-in browser, Splice folder | 6 | 1 | 5 | 0 | 0 |
| Serum racks | plugin_racks (generated rack presets) | 3 | 3 | 0 | 0 | 0 |
| **all** | 20 areas | **166** | **87** | **72** | **7** | **0** |

T4 re-ran the 4 plug-in commands T3 could not run (plug-in use was off then) plus the new
`plugins.exposure` and `plugins.parameters scope="all"`, so the T3 row's 4 UNSUPPORTED are
resolved by the T4 row; the "all" row counts each command once in its last state. The Serum
racks row is the Serum agent's end-to-end proof of `plugin_racks.expose` / `map` / `presets`
(new commands, PASS on first real run after two crash-finding probes, see below).

The other 12 of the 169 commands that existed during the tests are `system.*`, `lom.*`,
`eval.python` and the dev-only `system.reload`. (`plugins.configure`, the 170th command, was
added by another agent during the merge pass and is not covered by these runs.) The testers used them all the time, and the integration check and the merge
checks below also cover them. The MCP tools were covered too: 38 tools by T1, 48 by T2, and
every composite and primitive tool of T3's areas.

Merge pass (after the testers):

| check | result |
|---|---|
| `tests/integration_check.py` (real Live) | **32 PASS, 0 FAIL, 6 SKIP** — the skips are read commands that need a clip or device, and the scratch set has none |
| shared changes requested by the testers | **21 of 21 applied**, except T2 (i), the audio monitoring default (see "Open points") |
| unit tests `.venv/bin/python -m pytest -q tests` | green (see the merge notes below) |
| leftover `LB_` test objects in the set | none. At the end the set had tracks 1-MIDI, 2-MIDI, 3-Audio, 4-Audio, returns A-Reverb and B-Delay, 8 unnamed scenes, no cues, and was stopped |

## Per area

### T1-global

| area | commands | PASS | FIXED | UNSUPPORTED | FAIL |
|---|---|---|---|---|---|
| transport + song | 18 | 6 | 10 | 2 | 0 |
| scenes | 11 | 8 | 3 | 0 | 0 |
| view | 13 | 12 | 1 | 0 | 0 |
| cues | 9 | 1 | 8 | 0 | 0 |
| record | 7 | 1 | 6 | 0 | 0 |

| command | result | what was wrong / verified |
|---|---|---|
| `transport.get` | PASS | all fields match Live; 35 scales |
| `transport.play` | FIXED | when stopped, `position` started from the old playhead because the write is deferred; the result was stale; positions past the song length are now `bad_args` |
| `transport.continue` / `stop` / `toggle` | FIXED | the result showed the stale `is_playing` (deferred); the double-stop reset is documented |
| `transport.set_position` | FIXED | reported the previous position; the song-length check now runs up front |
| `transport.set_tempo` / `tap_tempo` / `set_time_signature` | PASS | limits 20..999, 1..99, 1/2/4/8/16; the signature is per position when the arrangement has signature markers |
| `transport.set_loop` | FIXED | `on` was stale; a brace past the song length used to fail after half-writing; now it is validated first and written in an order Live accepts |
| `transport.set` | FIXED | flags were stale; groove range is 0..1.3125; documented that `record_mode` starts playback |
| `transport.back_to_arranger` | FIXED | returned the stale `true` |
| `transport.undo` / `redo` | UNSUPPORTED | not run on the shared set, by rule; stub-tested |
| `transport.capture_midi` / `stop_all_clips` | PASS | |
| `song.summary` / `song.snapshot` | FIXED | `kind` tags dropped; `limit=0` message; cue points listed in time order |
| `scenes.list` / `get` / `duplicate` / `set` / `rename` / `stop_all` / `select` / `capture` | PASS | |
| `scenes.create` | FIXED | a new scene inherited the tempo/signature of the scene above and took the selection |
| `scenes.delete` | FIXED | a track path gave a misleading `not_found`; now `bad_args` |
| `scenes.fire` | FIXED | the docs said firing always starts the transport; `clip_count` added |
| `view.*` (12 commands) | PASS | the zoom direction docs were corrected |
| `view.select` | FIXED | it selected the track before failing on the slot; now everything is resolved first |
| `cues.list` / `position` / `jump` / `loop` | FIXED | Live keeps cues in creation order; rows are now in time order with the LOM path; stale `position` / `on` |
| `cues.add` / `toggle` / `delete` / `set` | FIXED | created the cue at the old playhead. They now use a pending/retry protocol: park the playhead, toggle on the retry, zoom the Arrangement in against grid snapping, never delete a cue under a snapped marker, restore the playhead and start marker. Verified exact at 1.1.1, 5.1.1, 9.2.3 and 12.7 |
| `cues.convert_time` | PASS | |
| `record.status` / `arm` / `session` / `arrangement` / `stop` / `settings` | FIXED | stale statuses; arguments were written before all were validated; recording scenes inherited a tempo; API arming ignores Exclusive Arm (documented) |
| `record.capture_midi` | PASS | |

### T2-tracks-clips

| area | commands | PASS | FIXED | UNSUPPORTED | FAIL |
|---|---|---|---|---|---|
| tracks | 10 | 8 | 1 | 1 | 0 |
| mixer | 5 | 4 | 1 | 0 | 0 |
| routing | 4 | 4 | 0 | 0 | 0 |
| clips | 12 | 5 | 7 | 0 | 0 |
| notes | 9 | 5 | 4 | 0 | 0 |
| arrangement | 10 | 8 | 2 | 0 | 0 |
| automation | 8 | 4 | 4 | 0 | 0 |

| command | result | what was wrong / verified |
|---|---|---|
| `tracks.create` / `delete` / `find` / `get` / `list` / `select` / `set` / `stop_clips` | PASS | return names get their letter prefix (`"C-X"`); new audio tracks come up with monitoring Off |
| `tracks.duplicate` | FIXED | a return track answered "no track named …"; now a clear `bad_args` |
| `tracks.group` | UNSUPPORTED | Live's API cannot create groups and the set had none; fold is stub-tested |
| `mixer.get` / `master` / `reset` / `set_many` | PASS | |
| `mixer.set` | FIXED | dB writes landed about 0.1–0.2 dB off; they are now refined against Live's own display (`"-3 dB"` shows `-3.0 dB`) |
| `routing.*` (4) | PASS | inputs, outputs, sidechain, Resampling, monitoring, master and returns |
| `clips.create` / `delete` / `list` / `quantize` / `stop` | PASS | |
| `clips.duplicate` | FIXED | **data loss**: Live's `duplicate_clip_slot` overwrites the slot below; the command now copies into the next empty slot |
| `clips.set` | FIXED | on unlooped clips Live ignores `start_marker` and ends at `loop_end`; the command reported writes Live dropped, and now returns `adjusted` |
| `clips.crop` / `duplicate_loop` / `reverse` | FIXED | real crop keeps the lead-in; duplicate_loop pushes later notes back; unlooped clips use the real start and end |
| `clips.fire` | FIXED | `force_legato` on an empty slot raised; it is now ignored there |
| `clips.get` | FIXED | warp markers were rounded too much; `sample_time` is in seconds |
| `notes.clear` / `get` / `theory` / `write_chords` / `write_pattern` | PASS | |
| `notes.add` / `modify` / `replace` / `transform` | FIXED | Live merges overlapping same-pitch notes; `added` now counts the notes Live kept, and `merged` is reported |
| `arrangement.create_audio_clip` / `create_midi_clip` / `delete_clip` / `duplicate_clip` / `loop` / `move_clip` / `overview` / `position` | PASS | looped arrangement clips keep their timeline length (recipe documented) |
| `arrangement.back_to_arranger` | FIXED | deferred; now reports `pending` |
| `arrangement.list` | FIXED | take-lane rows had no path |
| `automation.clear` / `overview` / `re_enable` / `state` | PASS | |
| `automation.get` / `list` / `shape` / `write` | FIXED | `value_at_time` on a step border returns the previous step, so samples and checks were one step off; breakpoints were in internal units |

### T3-devices-browser

| area | commands | PASS | FIXED | UNSUPPORTED | FAIL |
|---|---|---|---|---|---|
| devices + simpler | 17 | 12 | 5 | 0 | 0 |
| plugins | 5 | 0 | 1 | 4 | 0 |
| racks | 8 | 2 | 6 | 0 | 0 |
| browser | 7 | 2 | 5 | 0 | 0 |
| samples | 4 | 1 | 3 | 0 | 0 |

| command | result | what was wrong / verified |
|---|---|---|
| `devices.list` / `get` / `parameters` / `set_parameters` / `reset_parameters` / `randomize` / `set_state` / `duplicate` / `delete` / `find`, `simpler.get` / `set` | PASS | |
| `devices.get_parameter` / `set_parameter` | FIXED | punctuation-free name matching (`"sc eq freq"`); Compressor ratio strings (`"4:1"`, `"inf:1"`) |
| `devices.insert` | FIXED | Live needs exact, case-sensitive UI names; names are now translated, `not_found` lists close matches, and Max for Live devices are loaded through the browser |
| `devices.move` | FIXED | Live's bare "Couldn't move device." now gets an explanation |
| `simpler.action` | FIXED | `remove_slices` snaps to the nearest slice (Live ignores frames that are not exact) |
| `plugins.list` | FIXED | reports what Live's Plug-Ins browser offers (`installed` hint) |
| `plugins.get` / `presets` / `parameters` / `set` | UNSUPPORTED | plug-in use is off in this Live's Settings, so no plug-in can load; error paths verified |
| `racks.macros` / `drum_pads` | PASS | |
| `racks.chains` / `set_chain` / `insert_chain` / `set_macros` / `variations` / `set_pad` | FIXED | chain mute is the activator; no "all notes" `in_note`; new drum chains land on C1; macro names with trailing spaces; variations only touch mapped macros; "Multi" pads can be found by chain name |
| `browser.roots` / `preview` | PASS | |
| `browser.browse` / `search` / `load` / `hotswap` / `cache` | FIXED | a hot-swap target filters the browser; setting the same target twice raised; Live reuses device objects; `.alc` clips make a new track; no sample loads in the Arrangement view |
| `samples.list` | PASS | |
| `samples.import` / `inspect` / `locations` | FIXED | Arrangement-view refusal up front; AIFF data named `.wav` is rejected; Core Library and Factory Packs locations |
| Splice tools (`live_splice_*`) | PASS | import_downloaded in session, arrangement and simpler modes; watch_folder |

## Merge pass: what changed after the testers

Shared code:
- `compat.is_sequence` / `lom._is_sequence` now accept Live's `Base.Vector` collections. On
  real Live `path_of` used to fall back to a brute-force scan on every call and could not
  find take-lane clips. `_scan_track` also walks take lanes.
- `serialize` summarises Vectors as lists and take lanes as `take_lane`. Before,
  `lom.get("song.tracks")` returned a string on real Live.
- `resolve.scene` is the one scene resolver (`ctx.scene` delegates to it):
  - an ambiguous name gives `bad_args` listing the candidates;
  - integral floats (`8.0`) are accepted;
  - a path that is not a scene gives `bad_args`.
- `resolve.track` gives `bad_args` "'A-Reverb' is a return track — not allowed here", also
  for letters and the master. The old answer was a misleading `not_found`. Integral floats
  are accepted. `ctx.clip_slot` rejects fractional slots.
- The `config.py` default for `max_clients` is now 8.
- `mixer.refine_db` keeps a guess that already displays the exact dB. `"0 dB"` on a send now
  stays 1.0.
- MCP `errors.friendly_error`:
  - `browser.` / `samples.` / `plugins.` `not_found` errors now point to browser search and
    `live_sample_inspect` instead of a set snapshot;
  - `unsupported` no longer claims "no workaround".
- **Latency:** inside Live the socket threads only get the GIL while the main thread runs
  Python. A round trip took about 0.5 s (about 1 s with three testers). While clients are
  connected, `update_display` now sleeps 1 ms before and after the drain. The measured round
  trip is now about 0.1 s, and the whole integration check runs about 5× faster.

Stub (`tests/live_stub`):
- The real-Live behaviour from the three `live_stub_ext/*_live.py` mirrors is now the base
  stub's own behaviour, so every module sees it. That covers:
  - song-length limits, groove range, arming that ignores Exclusive Arm;
  - cues in creation order at the grid-snapped insert marker (zoom-driven);
  - scene creation, `time_signature_enabled`, double stop, `record_mode` starting playback;
  - unlooped clip braces, crop and duplicate_loop, same-pitch note merging;
  - `duplicate_clip_slot` overwriting, empty-slot `force_legato`, `pitch_fine` carry;
  - breakpoint envelopes, return-name prefixes, Live-like fader displays;
  - the real `insert_device` table and errors, drum-chain ranges, pad names, chain mute,
    variations, hot-swap filtering, `.alc` loads, Arrangement-view loads.
- Collections are read-only `Vector`s, as in Live.
- `clips_live.py`, `automation_live.py`, `devices_live.py`, `racks_live.py` and
  `browser_live.py` are gone. `transport_live.py` keeps only the switchable next-tick
  deferral.
- The browser's real device list now comes from `factory.add_browser_devices`.

Docs:
- `docs/ARCHITECTURE.md` §3, §4, §7, §10 and §11 are updated.
- `docs/LIVE_API_VERIFIED.md` §19.1 is new.
- `docs/TOOLS.md` is regenerated.

Live was restarted twice through `tests/live_dev.py restart` to load the `config.py` and
`LiveBridge.py` changes. Both times the dialog was answered with Don't Save, so the scratch
set went back to its saved state. That also removed T1's leftover arrangement time-signature
markers and the changed Arrangement zoom.

### T4-plugins (third-party plug-ins, after plug-in use was switched on)

| command | result | what was wrong / verified |
|---|---|---|
| `plugins.list` | FIXED | vendor (from the browser's vendor folder), `plugin_parameter_count` next to the exposed count; `needs_configure` only lists plug-ins exposing none of their parameters (Serum 2) |
| `plugins.get` | FIXED | `get_parameter_names()` lists **every** plug-in parameter (Serum 2: 2623), not the Configure list; now `parameter_names` = exposed, `plugin_parameter_count`, `not_exposed`, a corrected `configure_hint` |
| `plugins.presets` | FIXED | Serum 2 (VST3 + AU) and every Apple AU report only `["Default"]`; a `note` says where presets live |
| `plugins.set` | PASS | preset "Default"/0; `editor_open` true/false verified on the real window |
| `plugins.parameters` | FIXED | `scope="all"` pages through all 2623 Serum names with `exposed`; word + synonym filter ("cutoff" → Filter 1/2 Freq) |
| `plugins.exposure` | FIXED (new) | which wanted parameters are exposed, `missing`, the steps — and now `alternative` (`plugin_racks.expose`) |

Headline: Live exposes **none** of Serum 2's parameters after loading (VST3 and AU: 0 of 2623 /
2622) — only the device's Configure list, which Live fills by itself for small plug-ins only
(AUNBandEQ 41/41, AUMIDISynth 4/4, AUMatrixReverb 2/17). No API adds to it. Exposed AU
parameters work with every device and automation tool (display strings in the plug-in's units,
duplicate AU names as "Frequency #3"). Presets: only "Default". `is_editor_open` is rw. The
plug-in browser is `AUv2/<vendor>`, `VST` (flat), `VST3/<vendor>`; plug-in items are
`is_loadable` but not `is_device`. Details: `docs/live_test/T4-plugins.md`.

### Serum racks (generated rack presets — `plugin_racks.*`)

| command / tool | result | verified |
|---|---|---|
| `plugin_racks.map` / `live_plugin_param_map` | PASS | Serum 2: 541 mapped, 0 missing, 27 probe loads, 12.1 s — identical to the shipped map; Serum 2 FX: the same 541 ids in 9.3 s |
| `plugin_racks.expose` / `live_plugin_expose` | PASS | `parameters=["sound_design"]`, `new_track=true`: 120 parameters exposed; 16 set with display strings and read back ("800 Hz", "20 ms", "-1 oct", "1/8" …); a Filter 1 Freq clip envelope 300 Hz → 4000 Hz played and moved the filter; a `preset_file` + 4 wired macros (macro/127 = normalized value); a plain browser-loaded Serum 2 between an Arpeggiator and a Reverb replaced in place; Serum 2 FX in an Audio Effect Rack |
| `plugin_racks.presets` / `live_plugin_preset_files` | PASS | the User Library `.vstpreset` and a Live preset as embeddable; 8 factory `.SerumPreset` files for "bass reese", marked not embeddable |

Found on the way (each now blocked in code and in the stub, `LiveWouldCrash`): **Live crashes**
on a rack with more than 128 `PluginParameterSettings` and on a macro index with an empty
`MidiControllerRange` slot. Also: AU racks expose nothing (VST3 only), `.SerumPreset` data is
refused as a VST3 state, rack names come from the file name, Live does not push macro values on
load, hot-swapping keeps the old plug-in instance. Details: `docs/PLUGIN_RACKS.md`,
LIVE_API_VERIFIED §19.3.

## What you need to do

1. **Plug-ins:** done — plug-in use is on and T4 ran every plug-in command (above). For Serum 2
   or any big VST3 use `live_plugin_expose`; the manual Configure route stays for AU / VST2.
2. **More clients:** the installed `config.json` next to the Remote Script still says
   `"max_clients": 4`, and explicit values override the new default of 8. If several Claude
   clients will talk to Live at once, change it to 8 (or delete the key) and restart Live.
3. **Undo / redo:** try `transport.undo` / `redo` once in a scratch set. They were
   deliberately not run on the shared set.
4. **Recording:** Live's "Start Playback with Record" preference is on here, so
   `record_mode=true` (`transport.set`, `record.arrangement`) starts playback. API arming
   ignores "Exclusive Arm", so other armed tracks stay armed and record too. Disarm tracks you
   do not want recorded.
5. **Not yet run on real Live** (stub-tested only):
   - launching a real clip;
   - `tracks.set exclusive`;
   - the set-wide forms of `stop_clips` / `mixer.reset` / `back_to_arranger`;
   - `tracks.group` fold on a real group.

   They are safe to try in a scratch set.

## Open points (for the next build round)

- New audio tracks came up with monitoring "Off" on this machine. This may be a preference.
  The stub still defaults to "Auto" (T2 shared change (i) not applied).
- It is unverified whether a sample loaded through the browser onto a **MIDI** track while
  the Arrangement view is focused still creates a Simpler. The stub assumes it does;
  audio-track and clip loads do nothing, which was measured.
- The server accepts any line-framed JSON. A critic's probe (`http.py` in the shared
  scratchpad) sent an HTTP POST whose body was a JSON request to an in-process stub bridge.
  With a token configured this cannot run commands, but closing connections whose first line
  looks like HTTP would harden LAN mode. The probe ran against the stub (port 0), not the
  real Live. It shadows the stdlib `http` module when scripts are run from that folder.
- **Envelopes into the arrangement:** resolved (LIVE_API_VERIFIED §19.5): the copy keeps the
  envelopes on a track without devices and drops all of them when the track holds an instrument.
  `arrangement.duplicate_clip` / `move_clip` now report `envelopes: {source, copied}` and a note;
  `integration_check.py --scenario arrangement` checks the kept case on real Live.

## Follow-up: end-to-end beat scenario (2026-09-10)

`tests/integration_check.py --scenario beat --no-play` ran against this Live after the fixes:
all 16 beat steps PASS (drum kit from the browser + step pattern, Operator + chord progression,
display-string parameters on Utility, clip automation, `mixer.set_many` in dB, a Compressor
side-chained to the drums, duplicate to the arrangement, arming and disarming the drum
track, cleanup). Separate probes on scratch `LB_` tracks confirmed the Preview-volume fader
curve, the Compressor "S/C On" switch, moving a device into a rack chain and hot-swapping a sample
onto a Drum Rack pad (details in LIVE_API_VERIFIED §19.2). Re-run it
after changing handlers: `python tests/integration_check.py --scenario beat` (add `--no-play` to
leave the transport alone).

## Follow-up: cross fixer (2026-09-10)

After all fixer groups merged, the whole Remote Script was synced (`tests/live_dev.py sync`) and
Live restarted (`tests/live_dev.py restart`, scratch set, Don't Save) to load the new
`server.py` / `dispatcher.py` / `config.py`. Smoke checks: `system.status` → `bind {bound: true,
attempts: 1}`, `server.authenticated` / `pending`; a request without a token gets `auth` and the
connection is closed; a raw `POST / HTTP/1.1` gets `403`; `eval.python` with the token works and
`sys.exit()` inside it comes back as `bad_args`; a 5th client beyond `max_clients` 4 reads the
"too many clients" line; `system.reload` now reloads `plugin_racks_lib` too; `system.batch`
and `system.dialog` answer.

| check | result |
|---|---|
| `tests/integration_check.py` | **32 PASS, 0 FAIL, 6 SKIP** (read commands that need a clip or device) |
| `tests/integration_check.py --scenario beat --scenario arrangement --no-play` | **52 PASS, 0 FAIL, 6 SKIP** — all 16 beat steps and the new arrangement steps (envelope kept on a track without devices, 16-beat resize with the 4-beat loop, move keeps the length) |
| clip envelopes into the arrangement | resolved: kept without devices, dropped with Operator on the track (LIVE_API_VERIFIED §19.5); `duplicate_clip` reports `envelopes: {source: true, copied: false}` + note |
| Serum 2 compound displays | `plugin_racks.expose` + `devices.set_parameter` "70%" / "-6 dB" / "-12 dB" land exactly (§19.3) |
| A/B compare slot, `record.status` input level | PASS on Operator / an armed MIDI track |
| leftover `LB_` objects | none (tracks, scenes, cues checked afterwards) |
