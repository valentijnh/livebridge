# `tests/live_stub` — the fake Live environment

This directory is a stand-in for everything Ableton Live injects into a remote
script, so the whole `remote_script/LiveBridge` package can be unit tested
outside Live. **It mirrors the real Live 12.4 Python API as recorded in
`docs/LIVE_API_VERIFIED.md`** — names, signatures, enum values, value ranges,
read-only-ness and "only available for X" errors. If the stub and that document
disagree, the document wins: fix the stub.

```
tests/live_stub/
├── Live/                  fake `Live` package (Application, Song, Track, Clip, …)
│   └── _model.py          every class lives here; the Live.* modules re-export
├── _Framework/            fake `_Framework.ControlSurface` (real scheduling semantics)
├── ableton/v2/control_surface/   the modern alias of ControlSurface
├── factory.py             builders — USE THESE, don't edit the model
└── README.md              this file
```

`tests/conftest.py` puts this directory on `sys.path` (so `import Live`,
`import _Framework` and `import ableton.v2.control_surface` resolve to the
fakes) and puts `tests/` on `sys.path` (so `from live_stub import factory`
works).

## Ownership rule

`Live/_model.py`, `_Framework/`, `factory.py` and `tests/conftest.py` are owned
by the Fundament agent. **Do not edit them.** Two supported ways to get what
your module needs:

1. **Build it with `factory`** — the builders below cover devices, racks,
   drum racks, plug-ins, clips, notes, browser items, groups, extra
   tracks/scenes/cue points.
2. **Patch it from your own module** — add `tests/live_stub_ext/<module>.py`
   with a function `install(stub)` and call it from your own test file
   (`docs/ARCHITECTURE.md` §2). Only patch in things that exist in real Live
   (check `docs/LIVE_API_VERIFIED.md`); never invent API.

If you genuinely need a change inside the shared stub, write it in your agent
report instead of editing it — the integrator applies it once.

## Fidelity rules (what makes the stub strict)

| Rule | Example |
|---|---|
| Enums are Boost.Python-style int subclasses: `.name` on members, `values`/`names` dicts on the class, **no `__members__`** | `Live.Clip.WarpMode.names["repitch"] == 3` |
| Read-only LOM properties are real read-only `property` objects (assigning raises `AttributeError`) | `clip.length`, `cue.time`, `device.is_active`, `param.min` |
| Int properties reject floats (`TypeError`), bool properties accept ints | `clip.pitch_coarse = 1.5` fails |
| Out-of-range values raise | `param.value`, `song.tempo` (20..999), `clip.gain` (0..1) |
| "Only available for X" properties raise `RuntimeError` | `midi_clip.warping`, `param.value_items` (not quantized), `param.default_value` (quantized), `return_track.arm`, `track.fold_state` (not a group), `mixer.cue_volume` (not master), `master.input_routing_type` |
| Unnamed real arguments are positional-only, named ones use Live's exact names | `track.insert_device(DeviceName, DeviceIndex=-1)`, `song.create_midi_track(Index=None)`, `song.stop_all_clips(Quantized=True)` |
| Every device has `parameters[0] == "Device On"` | factory `params` start at `parameters[1]` |
| Note API returns `MidiNoteVector`; `apply_note_modifications` only accepts that vector | modify notes in place, pass the same vector back |
| `MidiNoteSpecification(pitch, start_time, duration, …)` is write-only | it has no readable attributes |
| Scheduling mirrors `_Framework`: `schedule_message` callbacks run from the **base** `ControlSurface.update_display()` | override `update_display` → call the base |
| Collections are read-only `Live.Base.Vector`s (not list/tuple — like Live 12.4.5): `len`, index, slice, iterate, `in`; no `index()`, no `+`, identity equality | `list(song.tracks)`, `compat.is_sequence(x)` |
| Real-Live behaviour measured on 2026-09-10 (`docs/LIVE_API_VERIFIED.md` §19.1) | song-length limits, creation-order cues at the snapped insert marker, scene creation copying the neighbour's tempo, API arming ignoring Exclusive Arm, unlooped clip braces, same-pitch note merging, `duplicate_clip_slot` overwriting, breakpoint envelopes, return names "A-…", fader displays, real `insert_device` names/errors, drum-chain ranges, hot-swap filtering |

Live's *next-tick* deferral (transport, playhead, loop/punch flags, recordings) is not in the
base stub: `tests/live_stub_ext/transport_live.py` switches it on per test (`tick()` applies
what Live would apply on its next tick).

Stub-only extras (never use them from `remote_script/` code): `Track._kind`,
`Song._undo_steps`/`_undo_depth`, `Browser.loaded_items`/`previewed_items`,
`Application.shown_messages`, `Timer.fire()`,
`ControlSurface.pending_scheduled_messages`, anything starting with `_`.

## What the model covers

| Area | Classes / API |
|---|---|
| Application | `Application` (`get_major/minor/bugfix_version`, `get_version_string`, `get_build_id`, `get_variant`, `get_document`, `has_option`, `show_message`, dialogs, CPU), `Application.View` (`show_view`, `hide_view`, `focus_view`, `is_view_visible`, `zoom_view`, `scroll_view`, `toggle_browse`, `available_main_views`, read-only `focused_document_view`/`browse_mode`), `Live.Application.get_application()` |
| Song | tempo (20–999) / signature / transport / loop / metronome / record flags, quantization enums, scale (`scale_name`, `root_note`, `scale_mode`, `scale_intervals`), `create_midi_track(Index=None)`, `create_audio_track`, `create_return_track`, `delete_*`, `duplicate_track`/`duplicate_scene` (return None), `create_scene(index)`, `cue_points`, `set_or_delete_cue`, `jump_*`, `tap_tempo`, `capture_midi(Destination)`, `capture_and_insert_scene`, `trigger_session_record`, `move_device`, `find_device_position`, `get_data`/`set_data`, undo, `Song.View` (`selected_track`, `selected_scene`, `highlighted_clip_slot`, `detail_clip`, `selected_chain`, read-only `selected_parameter`, `select_device(device, ShouldAppointDevice=True)`, `follow_song`, `draw_mode`), `CuePoint` (read-only `time`) |
| Track | name/color/mute/solo/arm/implicit_arm/monitoring (`Track.monitoring_states`), `devices`, `clip_slots`, `arrangement_clips`, `take_lanes`, `mixer_device`, routing types + channels (+ `available_*`), `playing_slot_index`, `fired_slot_index`, `create_midi_clip(start, length)`, `create_audio_clip(path, position)`, `duplicate_clip_to_arrangement(clip, destination_time)`, `delete_clip`, `delete_device`, `duplicate_device`, `insert_device` (12.3+), `duplicate_clip_slot`, `create_take_lane`, `stop_all_clips(Quantized)`, `Track.View` (`select_instrument()`, read-only `selected_device`, `device_insert_mode`, `is_collapsed`) |
| ClipSlot | `has_clip`, `clip`, `create_clip(length)`, `create_audio_clip(path)`, `delete_clip()`, `fire(record_length, launch_quantization, force_legato)`, `stop()`, `duplicate_clip_to(slot)`, `playing_status`, `is_playing`, `is_recording`, `is_triggered`, `has_stop_button` |
| Clip | markers/loop (ordered writes), launch props (`launch_mode`, `launch_quantization` = `ClipLaunchQuantization`, `legato`, `velocity_amount`), `fire`/`stop`, `quantize(RecordingQuantization grid, amount)`, `crop`, `duplicate_loop`, `duplicate_region`; MIDI: `add_new_notes` (returns ids), `get_notes_extended`, `get_all_notes_extended`, `get_notes_by_id`, `get_selected_notes_extended`, `apply_note_modifications`, `remove_notes_extended`, `remove_notes_by_id`, `select_all_notes`/`deselect_all_notes`/`select_notes_by_id`, `duplicate_notes_by_id`, legacy `get_notes`/`set_notes`/`remove_notes`; audio: `warping`, `warp_mode`, `gain`, `gain_display_string`, `pitch_coarse/fine`, `file_path`, `sample_length`, `sample_rate`, `warp_markers` + `add/move/remove_warp_marker`, `ram_mode`; automation: `automation_envelope`, `create_automation_envelope`, `automation_envelopes`, `clear_envelope`, `clear_all_envelopes` (`Live.Envelope.Envelope` with `insert_step`, `value_at_time`, `events_in_range`, `create_event`, `delete_events_in_range`) |
| Devices | `Device` (read-only `is_active`, `parameters[0]` Device On), `RackDevice` (chains, `insert_chain`, 16 macros + Chain Selector, `add_macro`/`remove_macro`, variations, `randomize_macros`, `copy_pad`, 128 `drum_pads` / 16 `visible_drum_pads`), `DrumChain` (`in_note`, `out_note`, `choke_group`), `DrumPad` (chains = rack chains with matching `in_note`), `Chain` (`insert_device`, `duplicate_device`), `PluginDevice` (presets, `get_parameter_names`), `SimplerDevice` (`sample` or None, `replace_sample`, `crop`, `reverse`, `warp_as`, …) + `Sample` (frames, slices), `DeviceParameter` |
| Mixer | `MixerDevice` (`volume`, `panning`, `sends`, `track_activator`, int `crossfade_assign`, `panning_mode`, split stereo, master-only `cue_volume`/`crossfader`/`song_tempo`), `ChainMixerDevice` |
| Scene | `Scene` (name, color, `is_triggered`, `is_empty`, tempo/time signature returning -1 when disabled, `clip_slots`, `fire(force_legato, can_select_scene_on_launch)`, `fire_as_selected`) |
| Browser | `Browser` with the 12 folder roots plus list roots `colors`/`user_folders`/`legacy_libraries`, `load_item`, `preview_item`, `stop_preview`, writable `hotswap_target`/`filter_type`, `relation_to_hotswap_target` (→ `Relation`); `BrowserItem` (no `canonical_parent`, lazy `children`, `iter_children` **property**) |
| Base | `Live.Base.Timer(callback, interval, repeat=False, start=False)`, `Live.Base.LimitationError` |
| Enums | every enum listed in `docs/LIVE_API_VERIFIED.md` §2 |

Behaviour mimics Live where it matters: `create_midi_track(index)` inserts at
that index (`None` = after the selected track), `create_scene()`/`delete_scene()`
keep every track's `clip_slots` in sync, `create_return_track()` adds a send
everywhere, `clip_slot.create_clip(length)` refuses audio tracks / occupied
slots, `clip_slot.fire()` sets `track.playing_slot_index`,
`browser.load_item()` puts devices on the selected track at
`track.view.device_insert_mode`.

Every `LomObject` carries `canonical_parent` and Live's listener protocol
(`add_<x>_listener` / `remove_<x>_listener` / `<x>_has_listener`).

## `factory` — the builder API

```python
from live_stub import factory
```

### Set

| Function | What it does |
|---|---|
| `make_song(midi_tracks=1, audio_tracks=1, return_tracks=2, scenes=4, tempo=120.0, names=None)` | Build a `Song`. Returns the song. |
| `make_application(song=None, version=(12,4,5), install=True, browser=True, variant="Suite")` | Build an `Application`, fill its browser and install it so `Live.Application.get_application()` returns it. `variant` is what `get_variant()` returns. |
| `make_c_instance(song)` / `FakeCInstance` | The opaque object Live hands to a remote script (only the real methods: `song`, `log_message`, `show_message`, `send_midi`, …). Records `.log`, `.messages`, `.midi`. No `schedule_message` — that lives in `ControlSurface`. |

### Tracks, scenes, cues

| Function | Notes |
|---|---|
| `add_track(song, name=None, kind="midi", index=-1)` | `kind` = `"midi"`, `"audio"` or `"return"`. |
| `add_return_track(song, name=None)` | Also adds a send on every existing track. |
| `add_group_track(song, name="Group", members=(), index=-1)` | Stub-only (the LOM cannot group tracks): gives `is_foldable`/`fold_state` and `group_track`. |
| `add_scene(song, name=None, index=-1)` | Clip slots stay in sync. |
| `add_cue_point(song, name="Cue", time=0.0)` | Kept sorted by time. |

### Devices

| Function | Notes |
|---|---|
| `add_device(host, name, class_name=None, kind="audio_effect", params=None, index=-1)` | `host` is a Track **or** a Chain. `kind` = `instrument` / `audio_effect` / `midi_effect`. `params` land at `parameters[1:]`. |
| `add_rack(host, name, kind="instrument", chains=2, params=None)` | 16 macros + Chain Selector by default. |
| `add_drum_rack(host, name="Drum Rack", pads=((36,"Kick"),(38,"Snare"),(42,"Hat")))` | 128 pads; each listed pad gets a DrumChain (`in_note`) with a Simpler holding `/Samples/Drums/<name>.wav`. |
| `add_plugin(host, name="Serum", params=None, kind="instrument", presets=(...), class_name="PluginDevice")` | `AuPluginDevice` for Audio Units. |
| `add_simpler(host, name="Simpler", file_path="")` | `sample` is None when `file_path` is empty. |
| `add_parameter(device, name, value=0.0, min=0.0, max=1.0, is_quantized=False, value_items=(), unit="")` | Append a parameter to an existing device. |
| `add_chain(rack, name=None, devices=(), in_note=None)` | `devices` = names or `add_device` kwargs dicts; `in_note` for drum racks. |

`params` accepts any mix of `"Name"`, `("Name", value)`,
`("Name", value, min, max)` and full kwargs dicts.

### Clips and notes

| Function | Notes |
|---|---|
| `add_clip(track, slot=0, length=4.0, name="", notes=(), audio=False, file_path="")` | Replaces whatever is in the slot. |
| `add_arrangement_clip(track, start_time=0.0, length=4.0, name="", notes=(), audio=False, file_path="/Samples/stub.wav")` | Uses the real `create_midi_clip` / `create_audio_clip`. |
| `add_notes(clip, notes)` | Append notes. |

Notes are `(pitch, start_time, duration, velocity[, mute])` tuples, dicts, or
`MidiNote` objects.

### Browser

| Function | Notes |
|---|---|
| `add_browser_item(parent, name, uri=None, is_folder=False, is_device=False, is_loadable=None, load_kind=None, children=(), source="", device_type=..., children_factory=None)` | `load_kind` decides what `browser.load_item()` does: `"device"` → a device on the selected track, `"sample"` → a Simpler on a MIDI track / the highlighted clip slot on an audio track, `"clip"` → the highlighted clip slot. `children_factory(item)` makes children lazy. |
| `add_user_folder(browser, name, children=())` | Appends to the `browser.user_folders` tuple. |
| `populate_browser(browser)` | The default tree (instruments, audio/midi effects, drums, sounds, plugins VST3+AU, samples with a lazy folder, packs, user library, clips, Max for Live, one user folder). |

## Example

```python
import Live
from live_stub import factory

def test_my_module():
    song = factory.make_song(midi_tracks=2, audio_tracks=1, scenes=8)
    factory.make_application(song)
    rack = factory.add_rack(song.tracks[0], "My Rack", chains=3)
    factory.add_device(rack.chains[0], "Reverb", kind="audio_effect",
                       params=[("Dry/Wet", 0.4)])
    clip = factory.add_clip(song.tracks[0], slot=2, length=8.0,
                            notes=[(60, 0.0, 1.0, 100)])
    notes = clip.get_notes_extended(0, 128, 0.0, 8.0)   # a MidiNoteVector
    notes[0].velocity = 90.0
    clip.apply_note_modifications(notes)
```

The shared `song` fixture in `tests/conftest.py` is built exactly this way —
read its docstring for the exact shape (3 tracks, 2 returns, master, 4 scenes,
clips with notes, cue points and the browser tree).
