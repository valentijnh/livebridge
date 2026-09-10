# Live test T2 — tracks, mixer, routing, clips, notes, arrangement, automation

Run against **Ableton Live 12.4.5 Suite on macOS (arm64)**, 2026-09-10, with two other testers
working in the same set at the same time. Every command was called through the real bridge
(`tests/live_query.py` / `LiveBridge` TCP) on scratch objects named `LB_T2-tracks-clips …`
(tracks MIDI / Audio / Bus / MCP, their clips, a return track, a take lane) that were deleted at
the end. All 48 MCP tools of these areas were also called in-process (`create_app(BridgeClient)`)
against the real bridge. Song-level state (transport, tempo, loop, signature) belongs to T1 and
was only read; T1 changed the signature to 3/4 and 7/8 during the run, and the bar-based
commands followed it correctly.

Legend: **PASS** works on real Live as documented · **FIXED** real Live differed, the
handler/tool/stub was corrected (what was wrong in the list) · **UNSUPPORTED** cannot be done
through Live's API · **FAIL** broken and not fixed (none).

Unit tests: `tests/test_{tracks,mixer,routing,clips,notes,arrangement,automation}.py` plus the new
`tests/test_clips_live.py` and `tests/test_mixer_live.py`. The stub now mirrors the measured
behaviour through `tests/live_stub_ext/clips_live.py` (clips, clip slots, notes) and
`tests/live_stub_ext/automation_live.py` (envelopes); both install per test and uninstall
afterwards.

## Summary

| area | commands | PASS | FIXED | UNSUPPORTED | FAIL |
|---|---|---|---|---|---|
| tracks | 10 | 8 | 1 | 1 | 0 |
| mixer | 5 | 4 | 1 | 0 | 0 |
| routing | 4 | 4 | 0 | 0 | 0 |
| clips | 12 | 5 | 7 | 0 | 0 |
| notes | 9 | 5 | 4 | 0 | 0 |
| arrangement | 10 | 8 | 2 | 0 | 0 |
| automation | 8 | 4 | 4 | 0 | 0 |
| **total** | **58** | **38** | **19** | **1** | **0** |

## tracks

| command | result | notes |
|---|---|---|
| `tracks.create` | PASS | MIDI/audio at end, at index 0, out-of-range index (`not_found`), bad index type, `type=group` refused; colour by name/hex/list. Return track: Live prefixes the name with its letter (`name="X"` → `"C-X"`), now documented. New audio tracks report monitoring `off`, MIDI `auto` (Live 12.4.5 default here). |
| `tracks.delete` | PASS | regular + return track; master refused. |
| `tracks.duplicate` | FIXED | copy lands right after the source and gets the name. A return track answered "no track named 'A-Reverb'" (the resolver hid returns); now `bad_args` "'A-Reverb' is the return track — Live's API can only duplicate regular tracks". |
| `tracks.find` | PASS | rank exact/prefix/contains; empty result is not an error. |
| `tracks.get` | PASS | name, index, `"master"`, ambiguous prefix (`bad_args` lists candidates), out of range. |
| `tracks.group` | UNSUPPORTED | Live's API cannot create groups and the shared set had none, so only the "not a group and not inside one" error could be exercised; fold/unfold is stub-tested. |
| `tracks.list` | PASS | type filters (single/list), name, armed, frozen, paging with `next_offset`, bad type. |
| `tracks.select` | PASS | |
| `tracks.set` | PASS | name/colour/mute/solo/arm/toggle on one and several tracks, master mute and return arm refused, colour index 70 → nearest palette colour. `exclusive` was not used on the shared set (it would un-solo/disarm other testers' tracks) — stub-tested. |
| `tracks.stop_clips` | PASS | one track, list, `quantized=false`. The set-wide form is song-level (T1) — stub-tested. |

## mixer

| command | result | notes |
|---|---|---|
| `mixer.get` | PASS | one track, list, whole mixer, minimal/summary/full (split-stereo values appear in full). |
| `mixer.master` | PASS | read; write only with the current values (the master is not T2's). |
| `mixer.reset` | PASS | per track, `what="all"`, bad key. The no-argument form (every track) was not run on the shared set. |
| `mixer.set` | FIXED | dB strings were converted with a fitted curve only, so Live showed "-2.998 dB" for "-3 dB" and "-64.869 dB" for "-65 dB". Absolute and relative dB now refine against the parameter's own `str_for_value` → exact display ("-3.0 dB", "-65.0 dB", "-69.5 dB", sends too); `db` no longer reports `-0.0`. Pan L/R/C, percentages, sends by letter/name/list, activator (== track mute in Live), crossfade, panning mode, range errors all as documented. |
| `mixer.set_many` | PASS | partial failures listed, unknown keys, `stop_on_error`. |

## routing

| command | result | notes |
|---|---|---|
| `routing.get` | PASS | MIDI/audio/return/master; real Live 12.4.5 gives the master input "Ext. In" and output "Ext. Out"; return tracks have input and output routing (matches `live_stub_ext/routing.py`). |
| `routing.set` | PASS | track input by name/prefix/index, channel ("Post FX", "1"), Resampling, Computer Keyboard + "Ch. 2", monitoring; bad monitoring, return monitoring refused, nothing-to-change. |
| `routing.summary` | PASS | |
| `routing.route` | PASS | `input` (with channel + monitoring), `output` (source → bus, channel "Track In"), `sidechain` into a Compressor ("S/C On" switched on), master as source ("Main"), incompatible MIDI source lists the choices, same track refused. |

## clips

| command | result | notes |
|---|---|---|
| `clips.create` | PASS | MIDI by beats/bars, unlooped, first empty slot, occupied slot, audio clip from a Core Library WAV, MIDI↔audio mismatch, return/master (no slots), relative/missing file. |
| `clips.crop` | FIXED | Live keeps `min(start_marker, loop_start)..loop_end` of a looped clip (the lead-in before the loop survives) and an unlooped clip's start..end; docstrings said "outside the loop". Unlooped clips now get `end_marker` aligned to the real end afterwards. |
| `clips.delete` | PASS | session, arrangement path, empty slot. |
| `clips.duplicate` | FIXED | **data loss**: Live 12.4.5's `Track.duplicate_clip_slot(i)` overwrites slot `i+1` (its docstring says "next free slot") — the test clip below was destroyed. The command now copies into the next EMPTY slot below (`duplicate_clip_to`), adding a scene only when none is free (`created_scene`). Target slot/track/overwrite, MIDI→audio refused. |
| `clips.duplicate_loop` | FIXED | Live pushes notes that were after the loop back by the loop length (documented now); for unlooped clips it doubles `loop_start..loop_end` and leaves `end_marker` behind — now aligned. |
| `clips.fire` | FIXED | `force_legato=true` on an empty slot raised "Can only pass force_legato to non-empty slots." — now ignored there. Empty-slot fire (stop button) and `launch_quantization` verified; launching a real clip was not done because the transport was stopped the whole run and a launch would start it (T1's state) — stub-tested. |
| `clips.get` | FIXED | warp markers were rounded to 0.01 s / 1e-4 beat (`[0.01, 0.0312]` for Live's `(0.011719, 0.03125)`); now 6 digits, `sample_time` documented as seconds. |
| `clips.list` | PASS | track, whole set, arrangement, playing_only, paging. |
| `clips.quantize` | PASS | Live applies the song swing (a note at beat 1.05 went to 1.25 with 1/4 grid); audio clips accepted; grid `none`/unknown refused. |
| `clips.reverse` | FIXED | MIDI mirrored correctly; unlooped clips now use the real clip start/end (`loop_start..loop_end`) instead of `end_marker`. Audio → `unsupported` as documented. |
| `clips.set` | FIXED | On an **unlooped** clip Live 12.4.5 ignores writes to `start_marker` (it follows `loop_start`) and plays `loop_start..loop_end` — a written `end_marker` alone does not move the end (an arrangement clip's `end_time` stays). The command reported `changed` for writes Live silently dropped. Now start → `loop_start`, end → `loop_end` + `end_marker`, `length` likewise, clashing aliases refused, and `adjusted` lists positions Live did not keep. Also verified: loop moves in both directions, bars and "3.1.1", `position`, signature, launch mode/quantization (incl. Live names `q_eighth`), colour, audio warp mode (rex refused), gain/gain_db (+24 dB max), pitch; `pitch_fine` beyond ±50 carries into `pitch_coarse` (documented); turning warping off switches looping off. |
| `clips.stop` | PASS | clip, arrangement clip, track (`quantized=false`). Set-wide form not run (song-level). |

## notes

| command | result | notes |
|---|---|---|
| `notes.add` | FIXED | Live never keeps overlapping notes of one pitch: a new note replaces one with the same pitch+start, swallows same-pitch notes that start inside it and shortens one it starts inside of; two identical notes in one call become one and `add_new_notes` returns one id. The docstring said the opposite and `added` counted requested notes. Now `added` = notes Live kept, `merged` = notes that disappeared. `extend` on an unlooped clip now moves the real end; on a looped arrangement clip the result says the timeline length stays (`timeline_end`, recipe to lengthen). |
| `notes.clear` | PASS | by ids (unknown ids ignored), pitch name, window, all. |
| `notes.get` | PASS | filters, pitch names, expression fields, paging. |
| `notes.modify` | FIXED | lengthening into the next same-pitch note is cut by Live; moving onto another note's start deletes one — now reported as `merged` and documented. |
| `notes.replace` | FIXED | same `added`/`merged` accounting as `notes.add`. |
| `notes.theory` | PASS | |
| `notes.transform` | FIXED | quantize/humanize can merge same-pitch notes (reported as `merged`); `legato` now ends at the real clip end of unlooped clips. Humanize seed, transpose range check, velocity scale/min all verified. |
| `notes.write_chords` | PASS | progression string, objects, N.C., voice leading, bass, strum, drop2, bad symbol/octave. Now also reports `merged`. |
| `notes.write_pattern` | PASS | drum names, accents, holds, repeat, swing, create/extend, bad characters. Now also reports `merged`. |

## arrangement

| command | result | notes |
|---|---|---|
| `arrangement.back_to_arranger` | FIXED | Live applies `track.back_to_arranger = False` on its next tick; the command read the old value back and reported `true`. Now it reports the requested state plus `pending: true`. The set-wide form is song-level (T1) and was not run. |
| `arrangement.create_audio_clip` | PASS | Core Library WAV at a beat position; MIDI track and missing file refused. |
| `arrangement.create_midi_clip` | PASS | beats, bars (3/4 and 7/8 songs), "9.1.1", audio track/negative start/zero length refused. Documented that a looped arrangement clip keeps its timeline length when the loop changes (lengthen via `looping=false` + `end_marker`, then `looping=true` — verified). |
| `arrangement.delete_clip` | PASS | index, at, name/path; out of range, empty time, session clip refused. |
| `arrangement.duplicate_clip` | PASS | session MIDI and audio clips onto the timeline (notes travel), incompatible target, empty slot. |
| `arrangement.list` | FIXED | take-lane rows had no `path` on real Live (the shared `path_of` cannot see into take lanes — see shared changes); rows now carry `song.tracks[i].take_lanes[j].arrangement_clips[k]`, which the clip/note commands accept. |
| `arrangement.loop` | PASS | read; argument validation. Writing the loop is song-level (T1). |
| `arrangement.move_clip` | PASS | by index and by time; incompatible target track refused. |
| `arrangement.overview` | PASS | grid lanes, window in bars, cue points. |
| `arrangement.position` | PASS | read; argument validation (writes are T1's). |

## automation

| command | result | notes |
|---|---|---|
| `automation.clear` | PASS | one envelope, a range (Live then interpolates across the gap), all, nothing to clear, bad arguments. |
| `automation.get` | FIXED | (1) `value_at_time` exactly on a step border returns the value *before* it (every `insert_step` border is two breakpoints), so samples on the grid showed the previous step — samples are now read 1e-4 beat after their time. (2) `EnvelopeEvent.value` is in Live's internal units (Filter Freq 0.2953 → 199.99 Hz, volume → linear gain), so `include_events` returned unusable numbers — breakpoints are now read back in the parameter's range. |
| `automation.list` | FIXED | same sampling fix. |
| `automation.overview` | PASS | |
| `automation.re_enable` | PASS | parameter and track scope; song scope is song-level (not run). |
| `automation.shape` | FIXED | the returned `check` read the old value at the first step; now correct. Sine/ramp/random(seed)/square, display-string ranges ("200 Hz".."5 kHz"), sends by letter. |
| `automation.state` | PASS | track, parameter, whole set. |
| `automation.write` | FIXED | same `check` fix. Step/linear/events modes, dB/Hz/pan strings, arrangement clip → `unsupported`, other track's parameter and bad values refused. |

## Other real-Live facts recorded for the stub/docs

- Every LOM collection is a `Base.Vector` (not list/tuple) — breaks the shared `lom._is_sequence`.
- `create_event` at a time that already has a breakpoint adds a second one after it.
- `Track.duplicate_clip_slot` overwrites, `ClipSlot.fire(force_legato=True)` refuses empty slots.
- Unlooped clips: two remembered braces; toggling looping restores the other brace; an
  arrangement clip's `end_time` follows the unlooped brace and stays when looping is switched on.
- New audio tracks came up with monitoring "off" (2), MIDI tracks "auto" (1).
