# Live API notes — what the Live Object Model can and cannot do

A practical companion to [ARCHITECTURE.md §10](ARCHITECTURE.md). For exact signatures see
[LIVE_API_VERIFIED.md](LIVE_API_VERIFIED.md) (sourced reference) and
[LIVE_API_DUMP_12.4.5.md](LIVE_API_DUMP_12.4.5.md) (introspection of a real Live 12.4.5 Suite —
the final authority when the two disagree). Everything below is reachable through the curated
tools; anything else in the LOM through `live_lom_get/set/call/describe/children` or
`live_eval_python`.

## 1. Units, addressing, identity

- **Time is in beats** (quarter notes) everywhere in the LOM: song position, loop, clip
  start/end/loop, note start/duration, arrangement positions. A 4/4 bar is 4 beats; in 6/8 a bar
  is 3 beats (6 eighths). Tools that take `unit="bars"` or bars.beats.sixteenths
  (`live_time_convert`) convert using the song's signature.
- **Note times are clip-relative**; arrangement clip positions are song time.
- Unwarped audio clips report positions in **seconds**; sample positions (Simpler, warp markers)
  are in **frames**.
- **Paths**: `song.tracks[2].devices[0].parameters[3]`, `song.tracks[0].clip_slots[1].clip`,
  `song.return_tracks[0]`, `song.master_track`, `song.scenes[1]`, `browser.instruments`.
  Indices are 0-based and **shift** when tracks, scenes, devices or clips are inserted/deleted —
  re-read (`live_set_snapshot`) after structural changes.
- MIDI pitch: 60 = **C3** in Live's naming (C-2 = 0). The first Drum Rack pad is 36 = C1.
- LOM objects must be compared with `==`, never `is`; a deleted object compares equal to `None`.
- Colours: `color_index` 0–69 (Live's palette) or `color` as 0xRRGGBB. Every LiveBridge
  `color` argument (tracks, clips, scenes) takes a palette index, `"#RRGGBB"`, `[r, g, b]` or a
  colour name (`"red"`) — one parser, `remote_script/LiveBridge/resolve.py`.
- Time arguments: a number is beats; song positions, loop points and clip lengths also take a
  string `"17.1.1"` = bars.beats.sixteenths (1-based positions, 0-based lengths `"4.0.0"`) in the
  song's signature. Note times inside a clip stay clip-relative beats.

## 2. Threading, undo, timing

- The LOM may only be touched on Live's main thread. LiveBridge queues every command and runs it
  from `update_display` (~every 100 ms), so a command takes one Live tick (~10–100 ms).
- Every mutating command is wrapped in `begin_undo_step()/end_undo_step()` → **one undo step**
  (`live_transport_undo` shows the names). The user's own edits share the stack.
- A **modal dialog** (save prompt, plug-in authorisation, preferences) can block the main
  thread: commands time out until it is closed. A timed-out command may still execute later.
  `Application.open_dialog_count` / `current_dialog_message` / `current_dialog_button_count` /
  `press_current_dialog_button(i)` read and answer a dialog that does not block
  (`live_dialog_get` / `live_dialog_press`); **button labels are not exposed** (only the message
  and the count), and a dialog that blocks the main thread blocks those commands too.
- Several commands can run in one main-thread pass as one undo step with `system.batch`
  (`live_command_batch`); next-tick state (playhead, `is_playing`, loop/punch) reads stale inside
  the batch.
- Live forbids changes from inside listener callbacks ("Changes cannot be triggered by
  notifications"); LiveBridge never does that.
- Edition limits (Intro/Lite track or scene counts) raise `Live.Base.LimitationError` →
  `unsupported`.

## 3. Song and transport

Readable/writable: `tempo` (20–999), `signature_numerator/denominator`, `is_playing`,
`current_song_time`, `start_time`, `loop`/`loop_start`/`loop_length`, `metronome`, `record_mode`
(Arrangement Record), `session_record`, `overdub`, `arrangement_overdub`, `punch_in/out`,
`back_to_arranger`, `clip_trigger_quantization` (0 none … 13 1/32), `midi_recording_quantization`,
`groove_amount`, `swing_amount`, `scale_name`/`root_note`/`scale_mode` (Live 12 scale awareness).
Actions: `start_playing/stop_playing/continue_playing`, `stop_all_clips`, `tap_tempo`,
`undo/redo`, `capture_midi`, `capture_and_insert_scene`, `trigger_session_record`,
`jump_by`, `jump_to_next_cue/prev_cue`, `set_or_delete_cue`.

Read-only or absent: `count_in_duration` (read-only), exclusive arm/solo preferences, **no
`save()`**.

**Song length wall.** Live refuses `current_song_time`, `start_time`, the loop brace — and
therefore new cue points — behind `song.song_length` (end of arrangement material / cue points /
playhead / loop brace + ~32 beats). LiveBridge gets past it: each `loop_length` write may reach
`song_length` and moves it 32 beats further at once (~10 ms per write, verified 12.4.5), so the
brace is stretched in steps, the position written and the brace put back; a cue point or the
playhead behind the old end then holds the new length (a start marker alone does not, but stays
where it is). So `live_cue_layout` / `live_cue_add`, `live_transport_set_loop`,
`live_transport_set_position` / play(position), `live_record_arrangement(time)` and punch
regions work in an empty arrangement, up to 4096 beats behind the end per command
(`song_extended: true` in the result). Continue (Shift+Space, `transport.continue`) resumes where
playback last stopped and ignores playhead moves made while stopped.

### Cue points (locators)

`song.cue_points` is in creation order (LiveBridge indexes by time); `CuePoint.name` rw, `time`
read-only — a move is delete + re-create. `set_or_delete_cue()` only acts at the playhead and
Live moves the playhead on its next tick, so every locator created/deleted away from the playhead
costs one tick (~0.15 s); the MCP tools follow the bridge's pending/retry protocol automatically
and `live_cue_layout` places a whole list of sections (at most 64) in one call (8 sections ≈ 2 s).
Away from the playhead the transport must be stopped — `stop_playback=true` stops, applies and
continues from where playback stopped. Stopped, Live snaps new locators to the zoom-dependent
Arrangement grid; LiveBridge zooms in, retries and reports `snapped` when even the finest grid
misses. The Arrangement zoom level cannot be read, so after such a pass it is not always
restored exactly.

## 4. Tracks

- Create MIDI/audio/return tracks at an index (`create_midi_track(-1)` = end); delete, duplicate
  (the copy lands right after; the call returns nothing, LiveBridge finds it).
- **Cannot**: move a track to another index, create or ungroup **group tracks**, freeze/flatten
  (`is_frozen` is read-only; a frozen track refuses clip/device edits).
- `arm` exists only where `can_be_armed` (reading it on main/return tracks raises); `fold_state`
  only on group tracks.
- Monitoring: `current_monitoring_state` 0 In, 1 Auto, 2 Off.
- Routing: assign one element of `available_input_routing_types/channels` /
  `available_output_routing_types/channels` (by display name in `live_routing_set`). Side-chain
  sources are device-level routings (`live_routing_route(method="sidechain")`).
- Take lanes (Live 12) are listed per track; comping itself is not scriptable.

## 5. Mixer

- `mixer_device.volume` 0.0–1.0 where **0.85 = 0 dB** (parameter name "Track Volume"), `panning`
  −1…1 ("Track Panning"), `sends[i]` named after the return ("A-Reverb"), `track_activator`,
  `crossfade_assign` (int 0 A, 1 none, 2 B), `panning_mode` (stereo/split).
- Master only: `cue_volume` (parameter "Preview Volume"), `crossfader`, `song_tempo`.
- `live_mixer_*` converts to and from dB text using the parameter's own display strings, so "-6
  dB" works regardless of Live's curve.
- **Meters**: `output_meter_level` / `output_meter_left/right` and `input_meter_*` (0..1 meter
  positions, left/right only for audio) plus `Application.average/peak_process_usage` (CPU
  percent) — `live_mixer_meters(seconds=...)`, and `live_record_status` reports each armed
  track's input level. The only audio feedback the API has; no spectrum or loudness.

## 6. Clips and notes

- **Session clips**: `ClipSlot.create_clip(length)` (MIDI tracks, empty slot only),
  `ClipSlot.create_audio_clip(absolute_path)` (audio tracks), `delete_clip`, `duplicate_clip_to`
  (overwrites), `fire()` — two overloads in 12.4.5: `fire()` and
  `fire(record_length, launch_quantization, force_legato)` (the dump's one-line summary shows
  only the first; the real docstring lists both, checked read-only on 12.4.5) — `stop()`.
- **Arrangement clips**: `Track.create_midi_clip(start, length)`, `Track.create_audio_clip(path,
  position)`, `duplicate_clip_to_arrangement(clip, time)`, `delete_clip(clip)`. Moving an
  arrangement clip = duplicate + delete (LiveBridge does this in `live_arrangement_move_clip`).
- Clip properties: name, colour, loop (`loop_start/loop_end`, `looping`), markers, `muted`,
  launch mode/quantization (`ClipLaunchQuantization`: 0 = global …), legato, velocity amount,
  signature; audio: `warping`, `warp_mode` (0 beats, 1 tones, 2 texture, 3 re-pitch, 4 complex,
  6 complex pro), `gain` (0–1), `pitch_coarse/fine`, `ram_mode`, warp markers.
- Editing: `quantize(grid, amount)` where grid is the **recording** quantization enum (1 = 1/4,
  2 = 1/8, 5 = 1/16, 8 = 1/32 …), `crop`, `duplicate_loop`, `duplicate_region`.
- **Notes** (Live 11+ extended API): `add_new_notes(MidiNoteSpecification…)` returns note ids;
  `get_notes_extended`, `get_all_notes_extended`, `apply_note_modifications` keep ids and per-note
  **probability, velocity deviation and release velocity**; `remove_notes_extended`,
  `remove_notes_by_id`. MPE/per-note expression curves are not in the API.
- Recording: arm + `fire()` an empty slot (session) or `record_mode` (arrangement); Capture MIDI
  retrieves what was played on armed/monitored MIDI tracks.
- **Groove Pool**: `song.groove_pool.grooves` (amounts in percent, `timing_amount` 100.0) —
  `live_groove_pool`, and `live_clip_set(groove=...)` assigns one. The API **cannot clear** a
  clip's groove (`None` is rejected); new clips report the pool's groove.
- **Warp markers**: `WarpMarker.sample_time` is in seconds, while `beat_to_sample_time` returns
  frames — `live_clip_warp`.
- **Conversions** (`Live.Conversions`, curated): `audio_to_midi_clip` (drums/harmony/melody —
  runs in the background; the new track appears later, right after the source track),
  `create_midi_track_with_simpler` — `live_clip_convert`; `sliced_simpler_to_drum_rack` (replaces
  the Simpler in place, 64 pads for a guitar loop), `create_midi_track_from_drum_pad` (names the
  new track after the pad chain), `move_devices_on_track_to_new_drum_rack_pad` (returns the new
  rack's C1 `DrumPad` and rebuilds the track after the selected track — its index can change and
  the old Track object dies) — `live_drumrack_convert`, `live_simpler_action("to_drum_rack")`.
  Tracks created this way are auto-numbered ("8-Drum Rack") and renamed when other tracks move.

## 7. Devices, racks, plug-ins

- **Built-in devices without the browser**: `Track.insert_device(name, index)` /
  `Chain.insert_device` (**Live 12.3+**) with the UI name ("EQ Eight", "Operator", "Drum Rack").
  Native devices only — presets, plug-ins, Max devices and samples go through the browser.
  Live enforces MIDI effects → instrument → audio effects order.
- `parameters[0]` is always **"Device On"**; `device.is_active` is read-only (toggle the
  parameter instead).
- Parameters: `value` (clamped to `min..max`), `is_quantized` + `value_items` (menus/switches),
  `default_value` (continuous only), `str_for_value`/`display_value` (what the knob shows),
  `automation_state`, `re_enable_automation()`. Internal values are often 0–1 even for dB/Hz
  knobs — LiveBridge accepts display text ("250 ms", "1.5 kHz") and finds the value.
- Racks: chains (mute/solo/volume/pan/sends), macros (up to 16, `visible_macro_count`,
  `add_macro/remove_macro`), macro variations (store/recall/delete/randomize), `insert_chain`
  (12.3+). Drum racks: 128 `drum_pads` (topmost rack only), chains with `in_note` (12.3+),
  `out_note`, `choke_group`, `RackDevice.copy_pad(src, dst)`, `DrumPad.delete_all_chains()`.
  What racks **cannot** do through the API (macro mapping, deleting chains, zones, grouping,
  presets) is listed in §12 with the workaround for each.
- `Song.move_device(device, target, position)` moves a device to another track **or into a rack
  chain** (verified on 12.4.5: a Reverb moved from the track into the first chain of an Audio
  Effect Rack) — `live_device_move(target_chain=...)`.
- Utility's gain knob is the parameter **"Output"** in 12.4.5 (−inf…+35 dB, internal −1…1);
  "Gain" is not a parameter name. Side-chain parameters ("S/C On", "S/C Gain", "S/C Mix", …)
  exist on Compressor, Glue Compressor, Gate, Auto Filter and Multiband Dynamics, but only the
  **Compressor** exposes the side-chain *source* (`available_input_routing_types`).
- **Plug-ins** (`PluginDevice` for VST2/VST3, `AuPluginDevice` for AU): `presets` (Serum 2 and
  Apple AUs: only `["Default"]`), `selected_preset_index`, `is_editor_open` (rw),
  `get_parameter_names()` (every parameter the plug-in has — Serum 2: 2623 = 541 synth
  parameters + 2082 MIDI proxies) and **only the parameters in the Configure list** as
  `parameters` — empty for big plug-ins (Serum 2 VST3/AU: 0 exposed). The API cannot add to a
  loaded device's list, but a **generated rack preset** can expose any VST3 parameter by its
  ParameterId (max 128 per rack): `live_plugin_expose` ([PLUGIN_RACKS.md](PLUGIN_RACKS.md);
  Serum 2 / Serum 2 FX maps ship, other VST3s need `live_plugin_param_map` once). The plug-in
  GUI itself and the plug-in's current sound (its state) are unreachable.
- Simpler: `sample` (file, markers, slices, warp), playback/slicing modes, `replace_sample(path)`,
  `crop`, `reverse`, `warp_as`. Other device classes with extra API in 12.4: Drift, Meld,
  Compressor (side-chain), Eq8, Hybrid Reverb, Looper (`export_to_clip_slot`), Wavetable,
  Max devices (banks) — curated: `live_device_properties` / `live_device_action` /
  `live_device_modulation` (Wavetable matrix). Verified 12.4.5: enum-typed properties (Wavetable
  `unison_mode` / `poly_voices` / `filter_routing` / effect modes, Simpler playback modes, Sample
  `warp_mode`) answer plain ints and accept ints; Eq8 `edit_mode` is a bool (False = A).

## 8. Browser

- Roots: `instruments`, `audio_effects`, `midi_effects`, `drums`, `sounds`, `plugins`, `samples`,
  `clips`, `packs`, `user_library`, `current_project`, `max_for_live` (+ lists `user_folders`
  (Places), `colors`). Items have `name`, `uri`, `is_folder`, `is_loadable`, lazy `children`.
- Walking the browser is slow on big libraries; LiveBridge searches with bounded depth, a time
  budget and a cache.
- `browser.load_item(item)` loads onto the **selected track** (device position follows
  `device_insert_mode`) or replaces `browser.hotswap_target`. LiveBridge selects the target track
  first and restores nothing else.
- **Splice**: Live 12.3+ shows Splice in the browser sidebar, but that section is **not in the
  Python browser API**. LiveBridge uses Splice's official MCP for search/download and imports the
  files from disk (`live_splice_import_downloaded`, `live_sample_import`). A Splice folder added to
  Places becomes searchable via `user_folders`. LiveBridge reads Live's `Library.cfg` for the
  User Library and for where Live's own Splice downloads go (`SpliceDownloadFolderModeMember` /
  `CustomSpliceDownloadPathMember`), and searches those folders too.

## 9. Automation

- **Session clip envelopes only**: `clip.automation_envelope(param)`,
  `create_automation_envelope`, `clear_envelope`, `clear_all_envelopes`; envelope
  `insert_step(time, length, value)`, `value_at_time`, and in 12.x `events_in_range`,
  `create_event`, `delete_events_in_range`. Only parameters of the clip's own track.
- Arrangement clips return `None` from `automation_envelope(p)`; `create_automation_envelope`
  fails on them. Envelopes an arrangement clip *has* are listed in `automation_envelopes` and are
  editable (`live_automation_*` finds them there).
- `duplicate_clip_to_arrangement` (session → arrangement and arrangement → arrangement) **may or
  may not carry the envelopes** — measured on Live 12.4.5 (2026-09-10, Session view focused): on
  a MIDI track **without devices** the copy kept the Track Volume envelope (also arr → arr); with
  an **instrument (Operator) on the track** the copy had no envelopes at all, mixer ones
  included, right away and 2 s later. `live_arrangement_duplicate_clip` / `_move_clip` therefore
  report `envelopes: {source, copied}` and a note when they were lost.
- **Track automation lanes** (arrangement) cannot be drawn: `live_automation_record` records
  them like a person — Arrangement Record + Automation Arm, `begin_gesture` / value /
  `end_gesture` on every ~100 ms tick while the song plays through the range, state restored
  afterwards (any track incl. returns and the master; "tempo" = Song Tempo).
- A clip envelope always follows the clip loop; the separate "unlinked" envelope length is not in
  the LOM. `Envelope.events_in_range` / `delete_events_in_range` raise "Range out of bounds."
  beyond ±1576800 beats. `EnvelopeEvent.control_coefficients` has no visible effect on
  `value_at_time`.
- Per-parameter `automation_state` (none/playing/overridden) and `re_enable_automation()` work
  everywhere.

## 10. View

`app.view.show_view/hide_view/focus_view/is_view_visible` with `"Session"`, `"Arranger"`,
`"Detail"`, `"Detail/Clip"`, `"Detail/DeviceChain"`, `"Browser"`; `scroll_view`/`zoom_view`
(direction 0 up, 1 down, 2 left, 3 right); `toggle_browse` (hot-swap). `song.view`:
`selected_track`, `selected_scene`, `highlighted_clip_slot`, `detail_clip`, `select_device`,
`follow_song`, `draw_mode`. There is no `song.view.selected_device` — use
`selected_track.view.selected_device` (read-only; select with `select_device`).

Clip editor (`Clip.View`): `select_envelope_parameter(p)` + `show_envelope()`, `hide_envelope()`,
`grid_quantization` (`GridQuantization` 0 none, 1 8 bars … 4 bar … 8 1/16, 9 1/32; reads back as
the enum, e.g. `g_sixteenth`), `grid_is_triplet`, `show_loop()` — `live_view_clip_editor` (e.g.
open the lane of automation just written). The Arrangement zoom level is not readable (only
`zoom_view` steps).

## 11. Versions and editions

| Feature | Available |
|---|---|
| Core LOM, extended note API, take lanes, `get_variant()` (edition) | Live 12.0+ |
| `Track/Chain.insert_device`, `RackDevice.insert_chain`, `DrumChain.in_note` | Live 12.3+ |
| `SimplerDevice.replace_sample`, `ClipSlot.create_audio_clip` | present in 12.4 (feature-detected) |
| Max for Live browser root, M4L devices | Suite (or Standard + Max for Live) |
| Track/scene count limits | Intro/Lite (raise `LimitationError`) |

Edition: `Live.Application.get_application().get_variant()` → "Suite", "Standard", "Intro",
"Lite", "Trial", "Beta". LiveBridge feature-detects with `hasattr` and answers `unsupported`
instead of crashing.

## 12. Not possible through the LOM — and what to do instead

| Wanted | Why not | Alternative |
|---|---|---|
| Save / Save As / Collect All and Save | no `save()` in the API | Ask the user to press Cmd/Ctrl+S; or Computer Use (UI automation) |
| Export / render audio or MIDI files | not in the API | `live_record_resample(start, bars\|end, source="master")`: records the section onto an audio track in real time (Resampling input, monitoring off, Punch-In/Out on the loop brace, stops at the end, restores loop/punch/arm/routing/playhead; verified 12.4.5: a 2-bar pass landed exactly on 116.1.1–118.1.1). Or the Ableton Extensions SDK, or Computer Use on *File → Export* |
| Freeze / flatten, bounce to track | not in the API | `live_record_resample(source=<track>, channel="Post FX")` bounces one track to audio; or the user (right-click → Freeze) |
| Group / ungroup tracks, move tracks | not in the API | The user (Cmd/Ctrl+G, drag); `live_tracks_group` only reads/folds groups |
| Draw arrangement track automation | not exposed | `live_automation_record` records it in real time (§9); session clip envelopes + `live_arrangement_duplicate_clip` only when `envelopes.copied` is true (not on tracks with an instrument, 12.4.5) |
| Create an envelope in an arrangement clip | `create_automation_envelope` fails on arrangement clips | Record it (`live_automation_record`), or write it in a session clip and copy (check `envelopes.copied`) |
| An "unlinked" clip envelope length | not in the LOM — envelopes follow the clip loop | Lengthen the loop (`extend_clip=true`) or ask the user to unlink it |
| Clear a clip's groove | `clip.groove = None` is rejected | Assign another groove, or the user (Groove menu → None) |
| Plug-in parameters outside Configure | a loaded device's Configure list cannot be changed | `live_plugin_expose` (VST3, generated rack preset, no click; max 128 per rack — more crashes Live); manual Configure for AU / VST2 |
| A plug-in's current sound / GUI / preset browser | the plug-in state is not readable; `presets` is only "Default" for Serum 2 and AUs | Start from preset files: `live_plugin_preset_files` → `live_plugin_expose(preset_file=...)` (`.vstpreset`, `.adv/.adg`; `.SerumPreset` cannot be embedded — Serum 2 rejects it as a VST3 state); re-exposing restarts the sound; the user browses presets in the plug-in window (`editor_open=true`) |
| Expose Audio Unit parameters without Configure | a generated rack exposes nothing for AUs | Use the VST3 version (`plugin_format="VST3"`), or Configure by hand |
| Menu commands, preferences, key commands | not exposed | Computer Use / the user |
| Dialog button labels; answering a blocking dialog | only `current_dialog_message` + the button count are exposed; a dialog that blocks the main thread blocks every command | `live_dialog_get` / `live_dialog_press(button, expect=...)` after confirming with the user; a blocking dialog: the user answers it in Live |
| Listening to audio (analysis, "does this sound good") | no audio in the API | `live_mixer_meters` (levels, CPU); a Max for Live device that analyses audio (future), or the user's ears |
| Splice browser section | not in the Python browser | Splice MCP + `live_splice_import_downloaded(files=[...])` |
| Warping audio precisely, audio-to-MIDI | curated now | `live_clip_warp` (warp markers), `live_clip_convert` (audio → MIDI / Simpler), `live_drumrack_convert` / `live_simpler_action("to_drum_rack")` (§6) |
| Read the Arrangement zoom level | not in the API (only `zoom_view` steps) | LiveBridge cannot restore the exact zoom after a snapped-cue pass; the user re-zooms |
| MPE / per-note expression curves | not in the note API | The user draws them |

### Racks and devices: what the API cannot do (Live 12.4.5 dump + real checks)

Claude gets asked for these; give the answer and the workaround instead of trying tools.

| Wanted ("…") | Why not | What to do instead |
|---|---|---|
| **Map a parameter to a macro** ("map cutoff to Macro 1") | No mapping method anywhere: `RackDevice.macros_mapped` / `has_macro_mappings` are read-only, `DeviceParameter` has only `str_for_value`, `re_enable_automation`, `begin/end_gesture` | The user right-clicks the parameter → *Map to Macro 1* (or Map mode, Cmd/Ctrl+M). Or load a rack preset that already has the mapping (`live_browser_load`), for plug-in parameters a generated rack with `live_plugin_expose(macros={"1": "Filter 1 Freq"})`, or set the target parameter directly (`live_device_set_parameter`) / automate it. Once mapped, `live_rack_macros` sets the macro. |
| **Delete, duplicate or reorder a rack chain** ("delete chain 3") | `RackDevice` has `insert_chain` only — no `delete_chain`, `duplicate_chain` or chain move | Silence it instead: chain `mute` / activator off (`live_rack_set_chain`), or empty it (`Chain.delete_device`). Drum Rack pads: `DrumPad.delete_all_chains()` clears a pad, `copy_pad(src, dst)` duplicates one. Real deletion/reordering: the user. |
| **Key / velocity / chain-select zones** | `Chain` has no zone properties (only `DrumChain.in_note`/`out_note`/`choke_group`) | The chain selector *value* is settable (`live_rack_macros(chain_selector=...)`); the zones themselves: the user (Zone editor), or a rack preset that has them. |
| **Group devices into a rack** (Cmd/Ctrl+G) | No "group" call | Build it: `live_device_insert("Audio Effect Rack" / "Instrument Rack")`, `live_rack_insert_chain`, then move the devices in with `live_device_move(target_chain=...)` (`Song.move_device`, verified). `Live.Conversions.move_devices_on_track_to_new_drum_rack_pad` moves a whole track chain onto C1 of a new Drum Rack. |
| **Save a device / rack / plug-in preset** (.adv, .adg, .fxp/.aupreset) | No save or export call | The user: the device title bar's save button (or drag the rack to the browser). For quick comparisons `live_device_set_state(save_to_compare_slot=true)` (`Device.save_preset_to_compare_ab_slot()`) stores the *current* state in the A/B compare slot of devices with `can_compare_ab`, and `compare_b` switches between the slots. |
| **Edit Sampler zones or its samples** (Sampler / "MultiSampler") | Sampler is a plain `Device`: parameters only, no sample, zone or key-map API | Use **Simpler** (`SimplerDevice.sample`, `replace_sample(path)` — `live_simpler_*`, `live_sample_import(mode="simpler")`), or load a Sampler preset from the browser. |
| **Replace the sample in a Drum Sampler** (`DrumCellDevice`) | Its API exposes `gain` only — no sample or file path | Hot-swap the pad with a sample from the browser (`live_browser_hotswap(track, device, drum_pad=..., query=...)` — verified on 12.4.5: the pad's chain content is replaced by a Simpler holding the sample) or `live_sample_import(mode="simpler")` on a pad chain. |
| **Arrangement automation of a rack macro / device** | Arrangement lanes cannot be drawn (§9) | `live_automation_record` (records the macro/parameter moves in real time), as §9. |
| **More than 128 plug-in parameters in one generated rack** | Live crashes on >128 `PluginParameterSettings` (and on a macro index with an empty `MidiControllerRange` slot) | LiveBridge refuses above 128; split the wanted parameters or pick the important ones (the Serum 2 "sound_design" group is 120). |

**Ableton Extensions SDK** (official, 2026, JavaScript/Node, Suite-only, beta): a second layer that
can do things control-surface scripts cannot (e.g. rendering). Extensions do not auto-start with
Live; LiveBridge does not depend on it. **Computer Use** (Claude operating Live's UI from
screenshots) covers menus and dialogs as a last resort. **Max for Live** devices see the same LOM
plus audio/MIDI streams (needed for analysis), but require Suite or the add-on.

## 13. Reference differences noted during the build

- `ClipSlot.fire` has two overloads in 12.4.5: `fire()` and `fire(record_length,
  launch_quantization, force_legato)`. `docs/LIVE_API_DUMP_12.4.5.md` only captured the first
  line of the docstring, which made it look argument-less; LiveBridge calls plain `fire()` unless
  a record length / launch quantization / legato is asked for, and falls back to `fire()` on
  `TypeError`.
- Mixer parameter names are real: "Track Volume", "Track Panning", sends "A-<return name>",
  master cue "Preview Volume" (≈0.70 by default in a new set; default value 0.85).
- The real browser has 15 roots including `legacy_libraries` (always empty) and list roots
  `user_folders`, `colors`; there is no `splice` root.
- The full list of corrections (return/master routing, "Main", `Scene.time_signature_enabled`
  writable, `Song.overdub`, enum values, ...) is in
  [LIVE_API_VERIFIED.md §19](LIVE_API_VERIFIED.md).
