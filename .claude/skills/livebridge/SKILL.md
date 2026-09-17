---
name: livebridge
description: Control Ableton Live 12 through the LiveBridge MCP tools (live_*). Use whenever the user wants to make, edit, arrange, mix or play music in Ableton Live - beats, drum patterns, basslines, chords, melodies, loading instruments/effects/VST/AU plug-ins (Serum 2 included), setting device parameters, clips, scenes, arrangement, automation, recording, bouncing, tempo, routing - or wants Splice samples in their Live set, or asks about their Live set, even if they just say "Ableton", "Live", "my track", "the DAW" or "music production".
---

# Working in Ableton Live with LiveBridge

LiveBridge gives you ~168 `live_*` tools that drive a running Ableton Live 12 through its Live
Object Model. Every mutating call is **one undo step** in Live. You cannot hear the result
directly - you work from notes, parameters, names, level meters (`live_mixer_meters`), the two
analysis tools (`live_theory_analyze` for MIDI, `live_audio_analyze` for audio - section 4b) and
the user's feedback, so describe what you did in musical terms and invite them to listen.

**Making music, not just clips**: before arranging, building transitions/build-ups, choosing
effects or mixing, read **`PRODUCTION.md`** next to this file (MCP resource
`livebridge://production`): energy curves, section blueprints per genre, the build-up and
transition toolbox, effect chains, harmony/melody rules, density, mix targets and a finishing
checklist. A track must work as a whole: plan the sections first, make every part serve one
idea, check theory and sound with the analysis tools.

## 1. Start every session the same way

1. `live_status` - is Live reachable, which version/edition, is `allow_eval` on?
   - `type: "connection"`: tell the user to start Live and enable **Preferences -> Link, Tempo &
     MIDI -> Control Surface -> LiveBridge** (Input/Output: None). For another computer:
     `live_discover`, then `live_connect(host, port, token, persist=true)` (ask the user for the
     token; it is in the Live machine's `Remote Scripts/LiveBridge/config.json`).
   - `type: "auth"`: token mismatch - ask the user for the right token, then `live_connect`.
2. `live_set_snapshot` - tempo, signature, tracks (with `path`), devices, clips, scenes, selection.
   Big set? `detail="minimal"` first, then `live_tracks_get` / `live_clip_list` for details.
3. `live_view_selection` when the user says "this track", "this clip", "the selected device".

Re-snapshot after structural changes: indices shift when tracks, scenes, devices or clips are
added or removed. Prefer the `path` fields from the latest summary, or names.

## 2. Conventions

- **Time is in beats** (quarter notes). In 4/4 a bar = 4 beats; a 16th = 0.25. Note starts are
  clip-relative. Song positions, loop points and clip lengths also take Live's bars.beats.16ths
  as a string ("17.1.1" = bar 17; lengths are 0-based, "4.0.0" = 4 bars); clip/arrangement tools
  also accept `unit="bars"`; `live_time_convert` converts either way.
- **Pitch**: 60 = C3 (Live naming). Bass lives around C1-C2 (36-48), chords C3-C4, leads C4-C5.
  Note names ("F#2", "Bb3") work everywhere.
- **Drum Rack pads**: kick 36 (C1), rim 37, snare 38, clap 39, closed hat 42 (F#1), open hat 46,
  crash 49, ride 51 - `live_clip_write_pattern` takes these names and maps them onto the loaded
  kit's **pad names** (check `result.kit` and `warnings`; `live_drumrack_overview` shows the pads).
- Tracks, scenes, devices, clips accept an **index, a name (exact, then prefix) or a LOM path**
  in every tool. Tracks also take `"selected"`, `"master"` and return letters (`"A"` = first
  return); clips take `"selected"` (the clip in the Detail view); `slot` also takes a scene name.
- Colours (`color=`): a palette index 0-69, "#RRGGBB", `[r, g, b]` or a name ("red", "blue").
  Some create tools call it `color_index` (0-69) - check the schema; unknown argument names are
  rejected with the accepted list.
- **Name and colour what you create** (`name=`, `color=` on create tools): the user must recognise
  your tracks and clips.
- Volume/pan/sends in **dB text** ("-6 dB", "C", "L20"); device parameters by name and by the
  text the knob shows ("250 ms", "1.5 kHz", "Saw", "1/8"; compound displays such as Serum's
  "50% [-9.0 dB]" take "70%" or "-6 dB"). Read before you write
  (`live_device_parameters(filter=...)`) so you use the parameter's own units.

## 3. Prefer composite tools (fewer round trips, one undo step)

| Goal | Tool |
|---|---|
| New track with an instrument | `live_browser_load(query, category="instrument", new_track=true, track_name=...)` - or `live_tracks_create` + `live_device_insert(name)` for plain built-ins (Live 12.3+, fastest) |
| Drum kit | `live_browser_load("909 Core Kit", category="drum_kit", new_track=true, track_name="Drums")` |
| Serum 2 / a big VST3 synth | `live_plugin_expose(plugin="Serum 2", parameters=["sound_design"], new_track=true, track_name=...)` (loads it already controllable) |
| Drum/arp/bass step pattern | `live_clip_write_pattern` (creates the clip) |
| Chord progression | `live_clip_write_chords("Am7 Fmaj7 C G", voice_leading=true)` - Roman numerals in the song key work too ("i VI III VII") |
| Arpeggio | `live_clip_write_arp(chords="Am F C G", style="up", rate="1/16")` |
| Free notes | `live_clip_add_notes(notes=[["C3",0,1,100], ...], create=true)` |
| Groove/feel | `live_clip_transform_notes(quantize=..., swing=..., humanize_timing=..., velocity_scale=..., fit_scale=true, transpose_steps=...)`; Groove Pool: `live_groove_pool` + `live_clip_set(groove=...)` |
| Many mixer changes | `live_mixer_set_many` |
| Many parameters | `live_device_set_parameters` |
| A long build (many commands) | `live_command_batch([{cmd, args}, ...])` - bridge commands (see `live_commands`), one round trip, **one undo step**, `"$0.path"` references earlier results |
| Everything about the set | `live_set_snapshot` |

## 4. The full production recipe (8 bars, ~25 calls)

Genre starting points (tempo, feel, key):

| Genre | Tempo | Drums | Key / harmony |
|---|---|---|---|
| House | 122-126 | four-on-the-floor kick, clap on 2+4, off-beat open hat | minor, i-VI-III-VII |
| Techno | 128-135 | kick every beat, closed 16th hats, sparse clap | minor/phrygian, one chord + stabs |
| Hip-hop / boom bap | 85-95 | kick 1 + "and of 2", snare 2+4, swung hats (`swing=0.55`) | minor 7ths, ii-V |
| Trap | 140 (half-time feel) | 808 kick, snare on 3, 1/32 hat rolls | minor, dark pads |
| Drum & bass | 172-176 | two-step kick/snare, fast hats | minor, long pads |
| Lo-fi | 70-85 | swung, humanized, low velocity | major 7ths/9ths |

1. **Set** - `live_transport_set(tempo=124, signature="4/4")`; sections first if the user wants
   a song: `live_cue_layout(cues=[{"name":"Intro","bar":1},{"name":"Drop","bar":9}])` (works
   before any clip exists).
2. **Drums** - `live_browser_load("909 Core Kit", category="drum_kit", new_track=true,
   track_name="Drums")`; `live_drumrack_overview(track="Drums")` to see which pads hold what;
   `live_clip_write_pattern(track="Drums", slot=0, pattern={"kick":"x...x...x...x...",
   "clap":"....X.......X...", "hat":"..x...x...x...x.", "ohh":"......x.......x."}, repeat=8)`
   (8 bars of 16ths; check `result.kit` / `warnings` for rows that hit an empty pad).
   Samples onto pads: `live_drumrack_set_pad(note="C1", track="Drums", file_path=...)`.
3. **Bass** - `live_browser_load("Sub Bass", category="instrument", new_track=true,
   track_name="Bass")` (or `live_device_insert("Drift")` on a new MIDI track), then
   `live_clip_add_notes(track="Bass", slot=0, create=true, notes=[["A1",0,0.75],["A1",1.5,0.5],
   ["A1",2.5,1], ["F1",4,0.75], ...])` - roots (plus fifths/octaves), rhythmic gaps,
   monophonic, below C3. **Browser presets usually load as racks with macros**: shape them with
   `live_rack_macros(track="Bass", values={"Filter": 40})` (read it first); use
   `live_device_set_parameters` on the inner device path only when no macro covers it.
4. **Chords / pad** - `live_browser_load("Warm Pad", category="instrument", new_track=true,
   track_name="Pad")`; `live_clip_write_chords(track="Pad", slot=0, chords="Am7 Fmaj7 C G",
   duration=8, octave=3, voice_leading=true, velocity=80)` (4 chords x 8 beats = 8 bars);
   optional `live_clip_write_arp` on a lead.
5. **Serum 2 lead (optional)** - `live_plugin_expose(plugin="Serum 2",
   parameters=["sound_design"], new_track=true, track_name="Lead")` -> set the sound with
   `live_device_set_parameters(device=<returned device>, values={"Filter 1 Freq": "1.2 kHz",
   "Env 1 Attack": "5 ms", "A Octave": "+1 oct"})`.
6. **Splice sample (optional, credits!)** - see section 6: search -> ask -> download ->
   `live_splice_import_downloaded(files=[...], track=..., mode="session", warp=true)`; match
   key with `live_clip_set(pitch_coarse=...)`, check the warp with `live_clip_warp`.
7. **Effects** - `live_device_insert("EQ Eight", track=...)` with a **low cut** on everything
   that is not kick/bass (~100-150 Hz on pads/leads/hats); `Compressor` on the drum bus;
   `Utility` for gain (its knob is "Output"); returns: `live_device_insert("Reverb",
   track="A")` / `("Delay", track="B")` and `live_mixer_set(track="Pad", sends={"A": "-12 dB"})`.
   **Side-chain** the bass/pad to the kick: `live_device_insert("Compressor", track="Bass")`,
   then `live_routing_route(source="Drums", destination="Bass", method="sidechain")`.
   Group devices into a rack: `live_device_insert("Audio Effect Rack")`, `live_rack_insert_chain`,
   `live_device_move(target_chain=...)`.
8. **Gain staging / mix** - `live_mixer_set_many`: kick/drums around -6 dB, bass -8, pads -12,
   leads -10, returns -15 dB; `live_mixer_master(volume="0 dB")` with a Limiter last on the
   master. Play and read `live_mixer_meters(seconds=3)`: the master peak must stay below ~0.9
   (about -1 dBFS headroom; aim for -6 dB on the master before the limiter), no instrument track
   silent (peak 0 means nothing sounds - check notes, device on, routing).
9. **Arrangement** - put the variations in scenes, then `live_arrangement_from_scenes(sections=
   [{"scene":"Intro","bars":8}, {"scene":"Drop","bars":16}])`; extend loops with
   `live_arrangement_duplicate_clip(length=...)` / `live_arrangement_resize_clip`; check with
   `live_arrangement_overview`.
10. **Automation** - sweeps into session clips: `live_automation_shape(parameter="Filter Freq",
    shape="ramp_up", mode="events", track=..., slots=[0, 1, 2])` (several scene clips at once);
    show it with `live_view_clip_editor(envelope="Filter Freq")`. Clip envelopes survive the copy
    to the arrangement only on tracks without an instrument (check `envelopes.copied` in the
    duplicate result); **track automation in the arrangement**: `live_automation_record(parameter=
    "Filter Freq", track=..., points=[[0, "200 Hz"], [32, "4 kHz"]], start=..., end=...)` - it
    plays through the range in real time (poll `action="status"`), then restores the transport.
11. **Bounce / export** - `live_record_resample(start="1.1.1", bars=8, source="master")` records
    the section onto an audio track in real time (plays through the speakers; tell the user).
    Freeze a track: `live_record_resample(source="Bass", channel="Post FX")`.
12. **Save** - Live's API cannot save: ask the user to press **Cmd/Ctrl+S**.

A multi-step build can go through `live_command_batch` (one undo step, no rollback - steps before
a failure stay; next-tick state such as the playhead reads stale inside the batch).

## 4b. Check your work: theory and ears

- **`live_theory_analyze`** (MIDI, instant): `clips=[{"track":"Bass","slot":0},
  {"track":"Pad","slot":0}, {"track":"Lead","slot":0}]` -> key (+ runner-up), chord names with
  Roman numerals, `progression` (paste-able into `live_clip_write_chords`), `out_of_key` notes,
  `clashes` between parts (`semitone` rubs, `low_mud`) and suggestions. Use it **before** adding
  to the user's material (which key? which chords?) and **after** writing parts that play
  together. Fix clashes with `live_clip_modify_notes` / `live_clip_transform_notes(fit_scale=
  true)`. Drum Rack clips are recognised and only get statistics.
- **`live_audio_analyze`** (audio, ~1 s per minute of audio): `file_path=` or an audio clip
  (`track`+`slot`) -> LUFS, true peak, crest factor, 8-band spectral balance with plain-language
  `flags` (mud, harsh, no sub, wide low end, clipping), stereo correlation/width, tempo, key and
  the `energy` curve over time; `reference=` compares against a reference track. Uses:
  - *Hear the set*: `live_record_resample(...)` -> `clips[0].file_path` -> analyse (`kind="mix"`).
  - *Samples / Splice loops*: tempo + key before importing -> set warp/`pitch_coarse` to match.
  - *The user's reference track*: structure (`energy`), loudness and balance to aim for.
  It needs the optional audio libraries (`type: "unsupported"` carries the pip command - relay
  it). It measures; the user's ears decide.

## 5. Instruments, effects, plug-ins, samples, parameters

- Built-in device, no preset needed: `live_device_insert(name="EQ Eight", track=...)` (12.3+).
- Presets/sounds from Live's library: `live_browser_load(query, category="instrument" |
  "audio_effect" | "midi_effect" | "drum_kit")`, or `live_browser_search` +
  `live_browser_load(uri=...)`; replace a device in place with `live_browser_hotswap`.
- **Serum 2 / big VST3 plug-ins**: a plain load exposes **0** parameters (Live only shows the
  device's Configure list). Use `live_plugin_expose(plugin="Serum 2",
  parameters=["sound_design"], new_track=true)` - 120 curated controls; add names/groups
  ("osc a", "filter", "env", "lfo", "fx", "macros"; max 128 per rack), wire macros with
  `macros={"1": "Filter 1 Freq"}`, start from a sound with
  `live_plugin_preset_files(plugin="Serum 2")` -> `preset_file=`. Then
  `live_device_set_parameters` / automation on the returned `device` path. Expose **first**, then
  design: re-exposing restarts the sound from the preset/template. `.SerumPreset` files cannot be
  embedded (load them in Serum's window: `live_plugin_set(editor_open=true)`). Other VST3s:
  `live_plugin_param_map(plugin=...)` once. Audio Units expose nothing this way - prefer VST3;
  for AU/VST2 ask the user to use the device's **Configure** button (`live_plugin_configure`
  lists the names and waits).
- Plain plug-in load (no control needed): `live_browser_load("Serum 2", category="plugin",
  plugin_format="vst3", editor_open=false)`.
- Racks: `live_rack_macros` (read + set macros), `live_drumrack_overview`, `live_drumrack_set_pad`,
  `live_drumrack_convert` (pad <-> track), `live_simpler_action("to_drum_rack")` (slices to pads).
- Samples on disk: `live_sample_import(file_path, track, mode="session"|"arrangement"|"simpler")`,
  onto a pad with `note="C1"` (or `live_drumrack_set_pad(note, file_path=...)`); `.mid` files
  import as MIDI clips the same way. Paths are on the machine running Live; in LAN mode a file
  that only exists on Claude's machine is sent over automatically (`live_sample_upload` does it
  explicitly, also for URLs).
- Simpler: `live_simpler_get/set/action` (playback mode, warp, slices, crop, reverse,
  replace_sample).
- Non-parameter settings (Wavetable wavetables, Hybrid Reverb IR, EQ Eight modes, Looper):
  `live_device_properties` / `live_device_action`; the Wavetable modulation matrix:
  `live_device_modulation`.
- Audio -> MIDI (drums/harmony/melody) or audio -> Simpler: `live_clip_convert`.
- Save an A/B snapshot of a device: `live_device_set_state(save_to_compare_slot=true)`, compare
  with `compare_b`.

## 6. Splice

LiveBridge does not search Splice itself; the official Splice MCP (a separate server, usually named
`splice`) does. If its tools are missing, call `live_splice_setup_info` and relay the steps.
1. Search with the Splice tools (key, BPM, genre, instrument, loop vs one-shot); present a few
   options before downloading - **downloads cost the user's Splice credits** (max 100 / 24 h).
2. After downloading with the Splice MCP: `live_splice_import_downloaded(files=[<paths Splice
   reported>], track=..., mode="session"|"arrangement"|"simpler"|"drum_rack")` (files are sent to
   the Live machine automatically in LAN mode). Without paths use `newest=N,
   max_age_minutes=10`. `live_splice_watch_folder` is only for downloads the user makes by hand
   in the Splice app/browser.
3. Tidy up: `live_clip_set` (warp, gain, name), match tempo (`warping=true`), transpose with
   `pitch_coarse` if the key differs, fix hits with `live_clip_warp`. One-shots: `mode="simpler"`
   or onto a pad (`mode="drum_rack"`, `note=`).

## 7. Safety and etiquette

- **Ask before destructive actions** on the user's material: deleting tracks/scenes/clips that
  contain content, `replace=true`/`clear` on existing notes, overwriting a clip, randomising a
  device, deleting devices. Your own freshly created objects may be changed freely.
- Don't start or stop playback, arm tracks, record or resample unless asked (or clearly part of
  the task); say when you do. Keep volumes sane (nothing above 0 dB on the master).
- After a `type: "timeout"`, do **not** blindly repeat a mutating call - it may still have run;
  re-read the state first. `live_dialog_get` shows whether a Live dialog is open; answer it with
  `live_dialog_press(button, expect=...)` **only after the user said which button** (labels are
  not available - show the message and the button count).
- Undo is available: `live_transport_undo(steps=n)` (check the returned names; the user's own
  edits are on the same stack).
- **Not possible through Live's API**: saving the set, exporting files (bounce with
  `live_record_resample` instead), freeze/flatten, grouping or moving tracks, drawing arrangement
  automation lanes (record them with `live_automation_record`), menu commands, preferences. Say
  so plainly and ask the user to do it (e.g. "press Cmd/Ctrl+S to save"); remind them to save
  after substantial work.
- **Racks/devices the API cannot touch** (answer, don't try tools): mapping a parameter to a macro
  (user: right-click the knob -> *Map to Macro*; then you can drive the macro; plug-in macros:
  `live_plugin_expose(macros=...)`); deleting, duplicating or reordering rack chains (mute the
  chain instead; Drum Rack pads can be cleared or copied with `live_drumrack_set_pad`);
  key/velocity/chain-select zones; saving device/rack/plug-in presets (user: the device's save
  button; A/B slot: `save_to_compare_slot`); Sampler zones/samples (use Simpler:
  `live_sample_import(mode="simpler")`); the Drum Sampler's sample (hot-swap the pad with a
  sample: `live_browser_hotswap(drum_pad=...)` puts a Simpler there). Details:
  `docs/LIVE_API_NOTES.md` §12.
- `live_lom_*` and `live_eval_python` are escape hatches for things no curated tool covers
  (`live_commands` lists what the installed Remote Script supports; `live_command_call` runs such
  a bridge command directly). Use `live_lom_describe` to explore first; `live_eval_python` only
  when necessary, never with destructive code.

## 8. Token economy

Use `detail="minimal"`, `filter=`, `limit`/`offset` on list tools (paged answers carry `total`,
`count` and `next_offset` while more follow); fetch notes only for the clip
you edit (`live_clip_get_notes`); don't re-snapshot the whole set after every small edit - only
after structural changes or when something is unexpected.

## 9. Errors

| `type` | Do this |
|---|---|
| `connection` | Live not reachable - see step 1. |
| `not_found` | Index/name moved - re-snapshot, use names/paths. A plug-in parameter "Live does not expose" -> `live_plugin_expose`. |
| `invalid_state` | Read the message (slot full, audio vs MIDI track, frozen track) and adapt. |
| `bad_args` | Fix the argument; the message lists valid values (and accepted argument names). |
| `unsupported` | This Live version/edition can't; use the alternative the message suggests (e.g. browser instead of `live_device_insert` before 12.3). |
| `timeout` | A dialog may be open in Live - `live_dialog_get`, ask the user; verify before retrying. |
| `forbidden` | `live_eval_python` is disabled (no token or `allow_eval: false`); use curated/LOM tools. |
