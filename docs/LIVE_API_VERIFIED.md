# Live 12 Python API — verified reference

The primary API reference for every LiveBridge module builder. Everything here was
checked against real Live sources on 2026-09-10; the fake `Live` package in
`tests/live_stub` mirrors exactly this document (names, signatures, enum values,
ranges, read-only-ness, "only available for X" errors). **If you need something that
is not in this file, check the sources below before using it — and mark it
UNVERIFIED in your code comments.**

Target: Live 12.4.5 (Suite, also Standard/Intro), Python **3.11** inside Live
(inferred from the 12.4 runtime capture: `int.to_bytes` has 3.11 defaults and
`Exception.add_note` exists, `int.is_integer` (3.12) does not).

## Sources (cited as `[KEY]`)

| Key | Source | What it gives |
|---|---|---|
| `[RT]` | Structure Void runtime capture of Live 12.4's `Live` module — https://midiremotescripts.structure-void.com/reference/live12/Live.Song.runtime/ (same URL pattern for `Live.Track`, `Live.Clip`, …) | Every class, property, method with Boost.Python signatures and docstrings. **Ground truth for names and signatures.** |
| `[RC]` | https://midiremotescripts.structure-void.com/guides/runtime-changes-11-to-12/ | What was added/removed between the Live 11 and Live 12 captures. |
| `[C74]` | Cycling '74 LOM reference — https://docs.cycling74.com/apiref/lom/<class>/ | Access (get/set/observe), value ranges, enum meanings, "Available since Live x.y". |
| `[FW]` | Decompiled Live 12.4 MIDI Remote Scripts — https://github.com/gluon/AbletonLive12_MIDIRemoteScripts (`_Framework/ControlSurface.py`, `ableton/v2/control_surface/control_surface.py`, `Push2/*`, `pushbase/*`, …) | How Ableton itself calls the API (argument order, enum usage, writable properties). |
| `[NSU]` | NSUSpray Live API doc (Live 11.0 capture incl. nested `View` classes) — https://github.com/NSUSpray/Live_API_Doc/blob/master/11.0.0.xml | Signatures of `Song.View`, `Track.View`, `Clip.View`, `Application.View` (not in `[RT]`). |
| `[AOSC]` | https://github.com/ideoforms/AbletonOSC (`manager.py`, `abletonosc/*.py`, `README.md`) | Working Live 11/12 script; main-thread design without threads. |
| `[AMCP]` | https://github.com/ahujasid/ableton-mcp — `AbletonMCP_Remote_Script/__init__.py` | Working Live 12 socket script (threads + `schedule_message`). |
| `[BSM]` | https://github.com/bschoepke/ableton-live-mcp — `Ableton_Live_MCP/bridge.py`, `AGENTS.md` | Working Live 12 socket script; `insert_device`, extended note API, main-thread pitfalls. |
| `[ZIF]` `[JPX]` | https://github.com/Ziforge/ableton-liveapi-tools , https://github.com/jpoindexter/ableton-mcp | More working socket scripts (browser loading, take lanes). |
| `[SV]` | https://midiremotescripts.structure-void.com/guides/controlsurface-lifecycle/ | ControlSurface lifecycle, `update_display` ~100 ms. |

Notation: `rw` = get/set, `ro` = read-only (assigning raises `AttributeError`),
`obs` = has `add_<name>_listener`. Methods show the exact Python signature;
`arg`-style names in `[RT]` (unnamed C++ args) are **positional-only** — keyword
names are only usable where shown. "raises" means Live raises (`RuntimeError`
unless noted). Units: song/clip time in **beats**, sample times in **frames**.

---

## 1. ControlSurface, threading, lifecycle

| Item | Verified fact | Source |
|---|---|---|
| Base class | `from _Framework.ControlSurface import ControlSurface` exists in Live 12.4; `ableton.v2.control_surface.ControlSurface` (and `ableton.v3`) too. Both implement the members below identically. | `[FW]` `[AMCP]` `[BSM]` `[AOSC]` |
| Entry point | package `__init__.py`: `def create_instance(c_instance): return MyScript(c_instance)` | `[FW]` `[SV]` |
| Constructor | `ControlSurface.__init__(self, c_instance=None, *a, **k)`; registers listeners on `song()`; logs "Initializing...". | `[FW]` |
| `song()` / `application()` | `self._c_instance.song()` / `Live.Application.get_application()` | `[FW]` |
| `schedule_message(delay_in_ticks, callback, parameter=None)` | Appends a task to the surface's task group; **runs from `ControlSurface.update_display()`** (`_task_group.update(0.1)`). Called outside a tick the delay is decremented by one (so 0 and 1 both mean "next tick"). Calls `callback(parameter)` if `parameter` is truthy, else `callback()`. Live 10.1 asserted `delay_in_ticks > 0` (why `[AMCP]` catches `AssertionError`). **Not thread-safe**: `TaskGroup.do_update` iterates and reassigns its task list without a lock. | `[FW]` (+ Live 10.1/11 decompiles) |
| `update_display()` | Called by Live on the main thread about every 100 ms (`Defaults.TIMER_DELAY = 0.1`). A subclass that overrides it **must call the base**, otherwise scheduled messages and component tasks never run. | `[FW]` `[SV]` |
| `log_message(*message)` | Writes `"(ClassName) msg"` via `c_instance.log_message` (Live's `Log.txt`) and the Python logger. | `[FW]` |
| `show_message(message)` | Status-bar text via `c_instance.show_message`. | `[FW]` |
| `disconnect()` | Called on unload/reload/quit; subclasses must remove listeners, stop threads/sockets and call `super().disconnect()`. | `[FW]` `[SV]` |
| `c_instance` API | Only: `song`, `log_message`, `show_message`, `send_midi`, `instance_identifier`, `request_rebuild_midi_map`, `set_pad_translation`, `set_feedback_channels`, `set_controlled_track`, `release_controlled_track`, `toggle_lock`, `handle`, `playhead`, `preferences`, … **no `schedule_message`**. | `[FW]` |
| `Live.Base.Timer(callback, interval, repeat=False, start=False)` | Main-thread timer, `interval` in **ms**, `start()/stop()/restart()`, `running`; errors in the callback stop it. Used by Push2 for LED fades. Whether LOM *mutations* are allowed from a Timer callback is **UNVERIFIED** (LiveBridge does not use it). | `[RT]` `[FW]` |

### How the working projects talk to Live

* `[AMCP]`, `[BSM]`, `[ZIF]`, `[JPX]`: TCP server on daemon threads; per request they call
  `self.schedule_message(0, task)` from the socket thread and wait on a `queue.Queue`/`Event`
  (10–30 s). `[AMCP]` runs *read-only* commands directly on the socket thread (touches the LOM
  off the main thread — unsafe). `[BSM]` serialises main-thread calls and documents that
  timeouts mostly mean a modal dialog or heavy indexing blocks Live, and that a timed-out
  mutation must **not** be retried blindly (it may still execute).
* `[AOSC]`: no threads (its comment: threads "beachballed" in the Live 11 beta); a
  non-blocking UDP socket polled from a self-rescheduling `schedule_message(1, tick)` every
  100 ms.

### Verdict for LiveBridge (implemented)

The design — socket threads that never touch the LOM, a lock-protected job queue drained on
Live's main thread from `update_display`, `threading.Event` waits with timeouts, abandoned jobs
still executed, bounded drains — is sound and matches the working projects. Corrections made:

1. The socket thread no longer calls `schedule_message` (races with the main thread's task
   group and gains no latency: those callbacks run from `update_display` anyway).
2. `LiveBridge.update_display()` now calls `ControlSurface.update_display(self)` first.
3. `dispatch()` on the main thread executes inline (waiting for itself would deadlock Live).
4. Log lines produced on socket/beacon threads are buffered and passed to
   `c_instance.log_message` on the main thread (`log.flush_pending()` every tick).
5. The UDP beacon thread no longer calls `Live.Application…` — the version is cached at start-up.
6. Windows: `SO_EXCLUSIVEADDRUSE` instead of `SO_REUSEADDR` (which on Windows lets a second
   socket bind a port that is still listening).

Latency: ≤ ~100 ms per command (one tick), same as AbletonOSC.

### Pitfalls that apply to every handler

* **Main thread only.** Never read or write a LOM object on a socket thread — not even a
  property read, not even `get_application()`.
* **Compare LOM objects with `==` / `!=`, never `is`.** Ableton's own `liveobj_valid(obj)` is
  `obj != None`: wrappers of **deleted** objects compare equal to `None` `[FW
  ableton/v2/base/live_api_utils.py]`. Whether Live caches wrappers (so `is` works) is
  UNVERIFIED — do not rely on it.
* Properties that are "only available for …" **raise** instead of returning None
  (`track.fold_state` on non-groups `[AOSC handler.py]`, `parameter.value_items` on
  non-quantized parameters `[RT]`, audio-only clip properties on MIDI clips `[RT]`). Wrap reads
  (`compat.safe_getattr`) or check the guard property first.
* Int properties do not accept floats (Boost's int converter); bool properties accept ints.
  `lom.coerce_value` already converts integral floats.
* Changing the LOM from inside a listener callback raises ("Changes cannot be triggered by
  notifications") — defer with `schedule_message` from the main thread. UNVERIFIED here
  (well-known Max/LOM rule, not re-tested).
* Edition/track limits raise `Live.Base.LimitationError` (`create_*_track`, `create_scene`,
  `duplicate_*`) `[RT]` `[FW Push2/browser_component.py]`.

---

## 2. Enums (value → name)

All Live enums are Boost.Python enums: `int` subclasses, members are class attributes with a
`.name`, the class has `values` (int → member) and `names` (name → member) dicts; **there is no
`__members__`** `[RT]`. Most LOM *properties* return plain ints.

| Enum | Values | Used by | Source |
|---|---|---|---|
| `Live.Device.DeviceType` | 0 undefined, 1 instrument, 2 audio_effect, **4 midi_effect** | `device.type` | `[RT]` `[C74 device]` |
| `Live.DeviceParameter.AutomationState` | 0 none, 1 playing, 2 overridden | `param.automation_state` | `[RT]` `[C74]` |
| `Live.DeviceParameter.ParameterState` | 0 enabled, 1 irrelevant, 2 disabled | `param.state` | `[RT]` `[C74]` |
| `Live.Clip.WarpMode` | 0 beats, 1 tones, 2 texture, 3 repitch, 4 complex, 5 rex, 6 complex_pro, 7 count | `clip.warp_mode`, `sample.warp_mode` | `[RT]` `[C74 clip]` |
| `Live.Clip.ClipLaunchQuantization` | 0 q_global, 1 q_none, 2 q_8_bars, 3 q_4_bars, 4 q_2_bars, 5 q_bar, 6 q_half, 7 q_half_triplet, 8 q_quarter, 9 q_quarter_triplet, 10 q_eighth, 11 q_eighth_triplet, 12 q_sixteenth, 13 q_sixteenth_triplet, 14 q_thirtysecond | `clip.launch_quantization` | `[RT]` `[C74 clip]` `[AOSC README]` |
| `Live.Clip.LaunchMode` | 0 trigger, 1 gate, 2 toggle, 3 repeat | `clip.launch_mode` | `[RT]` `[C74]` |
| `Live.Clip.GridQuantization` | 0 no_grid, 1 g_8_bars, 2 g_4_bars, 3 g_2_bars, 4 g_bar, 5 g_half, 6 g_quarter, 7 g_eighth, 8 g_sixteenth, 9 g_thirtysecond, 10 count (5–10 inferred from order; `[RT]` dict repr truncated) | `clip.view.grid_quantization` **only** | `[RT]` `[FW pushbase/clip_control_component.py]` |
| `Live.ClipSlot.ClipSlotPlayingState` | 0 stopped, 1 started, 2 recording | `slot.playing_status` | `[RT]` `[C74]` |
| `Live.Song.Quantization` | 0 q_no_q, 1 q_8_bars, 2 q_4_bars, 3 q_2_bars, 4 q_bar, 5 q_half, 6 q_half_triplet, 7 q_quarter, 8 q_quarter_triplet, 9 q_eight, 10 q_eight_triplet, 11 q_sixtenth, 12 q_sixtenth_triplet, 13 q_thirtytwoth (Live's spelling) | `song.clip_trigger_quantization`, `slot.fire(launch_quantization=)` | `[RT]` `[C74 song]` |
| `Live.Song.RecordingQuantization` | 0 rec_q_no_q, 1 rec_q_quarter, 2 rec_q_eight, 3 rec_q_eight_triplet, 4 rec_q_eight_eight_triplet, 5 rec_q_sixtenth, 6 rec_q_sixtenth_triplet, 7 rec_q_sixtenth_sixtenth_triplet, 8 rec_q_thirtysecond | `song.midi_recording_quantization` **and the grid of `clip.quantize()`** | `[RT]` `[C74 song]` `[FW Launchpad_Pro/ActionsComponent.py, APC64/settings.py]` |
| `Live.Song.SessionRecordStatus` | 0 off, 1 on, 2 transition | `song.session_record_status` | `[RT]` |
| `Live.Song.CaptureDestination` | 0 auto, 1 session, 2 arrangement | `song.capture_midi()` | `[RT]` `[C74]` |
| `Live.Song.CaptureMode` | 0 all, 1 all_except_selected | `song.capture_and_insert_scene()` | `[RT]` |
| `Live.Song.TimeFormat` | 0 ms_time, 1 smpte_24, 2 smpte_25, 3 smpte_30, 4 smpte_30_drop, 5 smpte_29 | `song.get_current_smpte_song_time()` | `[RT]` `[C74]` |
| `Live.Track.Track.monitoring_states` (class attribute — **no `Live.Track.MonitoringState`**) | 0 IN, 1 AUTO, 2 OFF | `track.current_monitoring_state` | `[FW SL_MkIII/channel_strip.py, Push2/routing.py]` |
| `Live.Track.DeviceInsertMode` | 0 default (end of chain), 1 selected_left, 2 selected_right, 3 count | `track.view.device_insert_mode` | `[RT]` `[C74 track_view]` |
| `Live.Track.RoutingChannelLayout` | 0 midi, 1 mono, 2 stereo | `RoutingChannel.layout` | `[RT]` |
| `Live.Track.RoutingTypeCategory` | 0 external, 1 rewire, 2 resampling, 3 master, 4 track; parent_group_track, none, invalid (values 5–7 UNVERIFIED) | `RoutingType.category` | `[RT]` |
| `Live.MixerDevice.MixerDevice.crossfade_assignments` | 0 A, 1 none, 2 B (member spelling UNVERIFIED, values verified) | `mixer.crossfade_assign` | `[C74 mixerdevice]` `[FW FaderfoxHelper.py]` |
| `Live.MixerDevice.MixerDevice.panning_modes` | 0 stereo, 1 stereo_split | `mixer.panning_mode` | `[C74]` `[FW pushbase/mixer_utils.py]` |
| `Live.Browser.Relation` | 0 ancestor, 1 equal, 2 descendant, 3 none | `browser.relation_to_hotswap_target()` | `[RT]` |
| `Live.Browser.FilterType` | -1 disabled, 0 hotswap_off, 1 instrument_hotswap, 2 audio_effect_hotswap; midi_effect_hotswap, drum_pad_hotswap, midi_track_devices, samples, count (values ≥3 UNVERIFIED) | `browser.filter_type` | `[RT]` |
| `Live.SimplerDevice.PlaybackMode` | 0 classic, 1 one_shot, 2 slicing | `simpler.playback_mode` | `[RT]` `[C74]` |
| `Live.SimplerDevice.SlicingPlaybackMode` | 0 mono, 1 poly, 2 thru | `simpler.slicing_playback_mode` | `[RT]` `[C74]` |
| `Live.Sample.SlicingStyle` | 0 transient, 1 beat, 2 region, 3 manual | `sample.slicing_style` | `[RT]` `[C74]` |
| `Live.Sample.TransientLoopMode` | 0 off, 1 forward, 2 alternate | `sample.beats_transient_loop_mode` | `[RT]` `[C74]` |
| `Live.Sample.SlicingBeatDivision` | 0 sixteenth, 1 sixteenth_triplett, 2 eighth, … four_bars (order of 3–10 UNVERIFIED) | `sample.slicing_beat_division` | `[RT]` |
| `Live.Groove.Base` | 0 gb_four, 1 gb_eight, 2 gb_eight_triplet, 3 gb_sixteen, 4 gb_sixteen_triplet, 5 gb_thirtytwo(?), count | `groove.base` | `[RT]` |
| `Application.View.NavDirection` | 0 up, 1 down, 2 left, 3 right | `scroll_view`/`zoom_view` | `[NSU]` `[C74 application_view]` |
| `Live.Conversions.AudioToMidiType` | 0 harmony_to_midi, 1 melody_to_midi, 2 drums_to_midi | `Live.Conversions.audio_to_midi_clip` | `[RT]` |

Other int codes: `song.count_in_duration` 0 none, 1 = 1 bar, 2 = 2 bars, 3 = 4 bars `[C74]`;
`track.fired_slot_index` -1 none, -2 stop button `[C74]`; `track.playing_slot_index` -1
none/arrangement, -2 stop slot fired `[C74]`; `sample.beats_granulation_resolution` 0 1 bar,
1 1/2, 2 1/4, 3 1/8, 4 1/16, 5 1/32, 6 transients `[C74]`.

---

## 3. Application — `Live.Application`

`Live.Application.get_application() -> Application` `[RT]`.

| Member | Type / signature | Notes | Src |
|---|---|---|---|
| `browser` | ro Browser | | `[RT]` |
| `view` | ro Application.View | | `[RT]` |
| `control_surfaces` | ro list | selected surfaces; `None` for empty slots | `[RT]` `[C74]` |
| `open_dialog_count`, `current_dialog_message`, `current_dialog_button_count` | ro int / str / int | a modal dialog blocks the main thread → timeouts | `[RT]` |
| `average_process_usage`, `peak_process_usage` | ro float, obs | CPU | `[RT]` |
| `unavailable_features` | ro vector of `UnavailableFeature` | edition limits (e.g. note_velocity_ranges_and_probabilities) | `[RT]` |
| `get_major_version()`, `get_minor_version()`, `get_bugfix_version()` | -> int | there is **no** `get_major_minor_version` | `[RT]` |
| `get_version_string()` | -> str | "12.4.5" | `[RT]` |
| `get_build_id()` | -> str | Live 12+ | `[RT]` `[RC]` |
| `get_variant()` | -> str: "Suite", "Standard", "Intro", "Lite", "Trial", "Beta" | **real edition detection**, Live 12+ | `[RT]` `[RC]` |
| `get_document()` | -> Song | | `[RT]` |
| `has_option(name)` | positional -> bool | Options.txt entry | `[RT]` |
| `press_current_dialog_button(index)` | positional | | `[RT]` |
| `show_message(text, buttons=MessageButtons.OK_BUTTON, enable_markup=False, show_success_icon=False)` | -> int | modal box (`text` is a `Live.Base.Text`); blocks the UI — avoid | `[RT]` |

### Application.View

| Member | Signature | Notes | Src |
|---|---|---|---|
| `focused_document_view` | ro str, obs | "Session" or "Arranger" | `[NSU]` `[C74]` |
| `browse_mode` | ro bool, obs | hot-swap mode active | `[NSU]` `[C74]` |
| `available_main_views()` | -> StringVector | `Browser, Arranger, Session, Detail, Detail/Clip, Detail/DeviceChain` | `[NSU]` `[C74]` |
| `show_view(name)`, `hide_view(name)`, `focus_view(name)` | positional | `""` = the visible main view; `show_view` raises during Live's init scope | `[NSU]` `[C74]` |
| `is_view_visible(identifier, main_window_only=True)` | -> bool | | `[NSU]` |
| `scroll_view(direction, view_name, modifier_pressed)` | all 3 positional, required | direction 0 up 1 down 2 left 3 right; scrollable: Arranger, Browser, Session, Detail/DeviceChain | `[NSU]` `[C74]` |
| `zoom_view(direction, view_name, modifier_pressed)` | same | Arrangement and Session only | `[NSU]` `[C74]` |
| `toggle_browse()` | | device chain + browser + hot-swap for the selected device; call again to stop | `[NSU]` `[C74]` |

---

## 4. Song — `Live.Song.Song` (`c_instance.song()`)

### Properties

| Property | Type, access, range | Notes | Src |
|---|---|---|---|
| `tempo` | float rw obs, 20.0–999.0 | may be automated | `[RT]` `[C74]` |
| `signature_numerator` / `signature_denominator` | int rw obs | numerator 1–99, denominator 1/2/4/8/16 (range UNVERIFIED) | `[RT]` |
| `is_playing` | bool rw obs | setting starts/stops transport | `[RT]` `[C74]` |
| `current_song_time` | float rw obs, beats | playhead | `[RT]` `[C74]` |
| `start_time` | float rw obs | insert marker (where play starts) | `[RT]` `[C74]` |
| `loop`, `loop_start`, `loop_length` | bool / float / float rw obs | arrangement loop, beats | `[RT]` `[C74]` |
| `metronome` | bool rw obs | | `[RT]` |
| `record_mode` | bool rw obs | Arrangement Record button | `[RT]` `[C74]` |
| `session_record` | bool rw obs | Session Record button | `[RT]` `[C74]` |
| `session_record_status` | int ro obs | SessionRecordStatus | `[RT]` |
| `session_automation_record` | bool rw obs | Automation Arm | `[RT]` `[C74]` |
| `arrangement_overdub` | bool rw obs | MIDI Arrangement Overdub | `[RT]` `[C74]` |
| `overdub` | bool rw obs | legacy alias of the overdub state | `[RT]` `[C74]` |
| `punch_in` / `punch_out` | bool rw obs | | `[RT]` |
| `back_to_arranger` | bool rw obs | set False to return to arrangement playback | `[RT]` `[C74]` |
| `re_enable_automation_enabled` | bool ro obs | | `[RT]` |
| `nudge_up` / `nudge_down` | bool rw obs | | `[RT]` |
| `clip_trigger_quantization` | int rw obs, 0–13 | Song.Quantization | `[RT]` `[C74]` |
| `midi_recording_quantization` | int rw obs, 0–8 | Song.RecordingQuantization | `[RT]` `[C74]` |
| `count_in_duration` | int ro obs, 0–3 | preference; not settable | `[RT]` `[C74]` |
| `is_counting_in` | bool ro obs | | `[RT]` |
| `groove_amount` | float rw obs, 0.0–1.0 | (UI shows up to 130 %; >1.0 UNVERIFIED) | `[RT]` `[C74]` |
| `swing_amount` | float rw obs, 0.0–1.0 | affects `clip.quantize` | `[RT]` `[C74]` |
| `root_note` | int rw obs, 0–11 (0 = C) | | `[RT]` `[C74]` |
| `scale_name` | str rw obs | must be a Live scale name (`Live.Song.get_all_scales_ordered()`) | `[RT]` `[C74]` |
| `scale_mode` | bool rw obs | Live 12 | `[RT]` `[RC]` |
| `scale_intervals` | ro list of int, obs | e.g. Major (0,2,4,5,7,9,11) | `[RT]` `[C74]` |
| `tuning_system` | ro obs | Live 12 | `[RT]` `[RC]` |
| `exclusive_arm`, `exclusive_solo`, `select_on_launch` | bool ro | preferences | `[RT]` `[C74]` |
| `can_undo`, `can_redo` | bool ro | | `[RT]` |
| `can_capture_midi`, `can_jump_to_next_cue`, `can_jump_to_prev_cue` | bool ro obs | | `[RT]` |
| `last_event_time`, `song_length` | float ro | | `[RT]` `[C74]` |
| `name`, `file_path` | str ro | empty when unsaved | `[RT]` `[C74]` |
| `appointed_device` | Device rw obs | blue-hand device | `[RT]` `[C74]` |
| `is_ableton_link_enabled`, `is_ableton_link_start_stop_sync_enabled`, `tempo_follower_enabled` | bool rw obs | | `[RT]` |
| `tracks`, `visible_tracks`, `return_tracks`, `scenes`, `cue_points` | ro vectors, obs | `tracks` excludes returns and master | `[RT]` |
| `master_track` | ro Track | named "Main" in Live 12 | `[RT]` |
| `view` | ro Song.View | | `[RT]` |
| `groove_pool` | ro GroovePool | | `[RT]` |

### Methods

| Signature | Returns / notes | Src |
|---|---|---|
| `start_playing()`, `stop_playing()`, `continue_playing()` | start from insert marker / stop / continue from playhead | `[RT]` |
| `play_selection()` | plays the arrangement selection | `[RT]` |
| `stop_all_clips(Quantized=True)` | False = immediately | `[RT]` `[C74]` |
| `jump_by(beats)`, `scrub_by(beats)` | positional | `[RT]` |
| `jump_to_next_cue()`, `jump_to_prev_cue()`, `is_cue_point_selected()` | | `[RT]` |
| `set_or_delete_cue()` | **returns None**; toggles a cue at `current_song_time`. To create a named cue at time t: set `current_song_time = t`, call it, then find the new cue in `cue_points` by time and set `name` (`[AMCP]` does this) | `[RT]` `[AMCP]` |
| `tap_tempo()` | | `[RT]` |
| `undo()`, `redo()` | -> str | `[RT]` |
| `begin_undo_step()`, `end_undo_step()` | exist in 12.4; group changes into one undo step (nesting semantics UNVERIFIED — LiveBridge's Context guards nesting) | `[RT]` |
| `capture_midi(Destination=CaptureDestination.auto)` | | `[RT]` `[C74]` |
| `capture_and_insert_scene(CaptureMode=CaptureMode.all)` | raises on limits | `[RT]` |
| `trigger_session_record(record_length=<none>)` | record in selected/next empty slot of armed tracks | `[RT]` `[C74]` |
| `re_enable_automation()` | | `[RT]` |
| `create_midi_track(Index=None)` / `create_audio_track(Index=None)` | -> Track. `-1` = end, `None` = after the selection, invalid index/limits raise | `[RT]` |
| `create_return_track()` | -> Track (appended; max returns → RuntimeError) | `[RT]` |
| `delete_track(index)`, `delete_return_track(index)` | positional; bad index raises | `[RT]` |
| `duplicate_track(index)` | **returns None**; the copy is inserted after and selected | `[RT]` |
| `create_scene(index)` | positional, **required**; -1 = end; -> Scene | `[RT]` `[C74]` |
| `delete_scene(index)`, `duplicate_scene(index)` | duplicate returns None, selects the new scene | `[RT]` |
| `move_device(device, target, target_position)` | -> int (actual position); `target` = Track or Chain | `[RT]` `[C74]` |
| `find_device_position(device, target, target_position)` | -> int, -1 if impossible | `[RT]` |
| `get_current_beats_song_time()` | -> BeatTime(bars, beats, sub_division, ticks) | `[RT]` |
| `get_beats_loop_start()`, `get_beats_loop_length()` | -> BeatTime | `[RT]` |
| `get_current_smpte_song_time(format)` | TimeFormat -> SmptTime | `[RT]` |
| `get_data(key, default_value)`, `set_data(key, value)` | persistent per-set storage | `[RT]` |
| `force_link_beat_time()` | | `[RT]` |
| **no** `save()` / export / render | not in the API | `[RT]` |

`Live.Song.get_all_scales_ordered() -> tuple((name, intervals), …)` `[RT]`.

### Song.View

| Member | Access | Notes | Src |
|---|---|---|---|
| `selected_track` | rw obs | also returns/master | `[NSU]` `[C74]` |
| `selected_scene` | rw obs | | `[NSU]` `[C74]` |
| `highlighted_clip_slot` | rw | None for master/returns | `[NSU]` `[C74]` |
| `detail_clip` | rw obs | clip shown in Detail view | `[NSU]` `[C74]` |
| `selected_chain` | rw obs | | `[NSU]` `[C74]` |
| `selected_parameter` | **ro** obs | | `[C74]` |
| `follow_song`, `draw_mode` | bool rw obs | | `[NSU]` `[C74]` |
| `select_device(device, ShouldAppointDevice=True)` | `device` positional | selects the device in its track (does not reveal the track) | `[NSU]` `[FW]` |

There is **no** `song.view.selected_device` — use
`song.view.selected_track.view.selected_device`.

### CuePoint — `Live.Song.CuePoint`

`name` str rw obs; `time` float **ro** obs (beats); `jump()` (quantized when playing).
There is no way to move a cue: delete it (`set_or_delete_cue` at its time) and create a new
one. `[RT]` `[C74 cuepoint]`

---

## 5. Track — `Live.Track.Track`

| Property | Type, access | Notes | Src |
|---|---|---|---|
| `name` | str rw obs | | `[RT]` |
| `color` | int rw obs (0x00rrggbb) | nearest palette colour is used | `[RT]` `[C74]` |
| `color_index` | int rw obs | may be None ("no colour"); palette size 70 (0–69) UNVERIFIED | `[RT]` |
| `mute`, `solo` | bool rw obs | not on master; `solo` bypasses exclusive solo | `[RT]` `[C74]` |
| `arm` | bool rw obs | **raises** on master/return (check `can_be_armed`) | `[RT]` `[C74]` |
| `can_be_armed` | bool ro | False for master, returns, groups | `[RT]` |
| `implicit_arm` | bool rw obs | Push's "weak" arm, not saved | `[RT]` `[C74]` |
| `current_monitoring_state` | int rw obs | `Track.monitoring_states` 0 IN, 1 AUTO, 2 OFF | `[RT]` `[FW]` |
| `has_midi_input`, `has_audio_input`, `has_midi_output`, `has_audio_output` | bool ro | MIDI track ⇔ `has_midi_input` | `[RT]` `[C74]` |
| `is_foldable` | bool ro | group tracks | `[RT]` |
| `fold_state` | int rw | 0 open, 1 folded; **raises unless `is_foldable`** | `[RT]` `[C74]` `[AOSC]` |
| `is_grouped`, `group_track` | ro | | `[RT]` |
| `is_visible` | bool ro | False inside a folded group | `[RT]` |
| `is_frozen`, `can_be_frozen` | bool ro | no freeze/flatten API | `[RT]` |
| `is_part_of_selection` | bool ro | | `[RT]` |
| `is_showing_chains` rw / `can_show_chains` ro | bool | Instrument Rack chains in Session | `[RT]` |
| `back_to_arranger` | bool rw obs | Live 12 | `[RT]` `[RC]` |
| `playing_slot_index`, `fired_slot_index` | int ro obs | see §2 | `[RT]` `[C74]` |
| `muted_via_solo` | bool ro obs | | `[RT]` |
| `input_meter_level/left/right`, `output_meter_level/left/right` | float ro obs | 0.0–1.0 | `[RT]` `[C74]` |
| `performance_impact` | float ro obs | | `[RT]` |
| `devices` | ro vector obs | the device chain (the mixer is `mixer_device`) | `[RT]` |
| `clip_slots` | ro vector obs | one per scene; empty for master/returns (UNVERIFIED for master) | `[RT]` |
| `arrangement_clips` | ro vector obs | empty for master, returns, groups | `[RT]` |
| `take_lanes` | ro vector obs | Live 12 | `[RT]` `[RC]` |
| `mixer_device` | ro MixerDevice | | `[RT]` |
| `view` | ro Track.View | | `[RT]` |
| `monitoring_states` | class attribute (enum) | | `[FW]` |

### Routing (MIDI and audio tracks; output also on returns; nothing on master input)

| Property | Notes | Src |
|---|---|---|
| `available_input_routing_types`, `available_input_routing_channels`, `available_output_routing_types`, `available_output_routing_channels` | ro vectors of `RoutingType` (`display_name`, `category`, `attached_object`) / `RoutingChannel` (`display_name`, `layout`) | `[RT]` |
| `input_routing_type`, `input_routing_channel`, `output_routing_type`, `output_routing_channel` | rw obs: **assign an element of the matching `available_*` vector** (match by `display_name`); channels change after the type changes | `[RT]` `[C74]` `[AOSC track.py]` |
| `current_input_routing`, `current_output_routing`, `*_sub_routing`, `input_routings`, … | legacy string API, still present — do not use | `[RT]` |

Typical names: MIDI input types "All Ins", "Computer Keyboard", "No Input", …; audio input
"Ext. In", "Resampling", track names, "No Input"; outputs "Master", "Sends Only", track names
(exact lists depend on the set and audio setup).

### Methods

| Signature | Returns / notes | Src |
|---|---|---|
| `create_midi_clip(start_time, length)` | positional; -> Clip in the arrangement; **Live 12.0+**; raises on non-MIDI/frozen tracks, while recording, or time outside [0, 1576800] | `[RT]` `[RC]` `[C74]` |
| `create_audio_clip(file_path, position)` | positional; absolute path to a supported audio file; -> Clip in 12 (returned None in the Live 11 capture); raises on non-audio/frozen tracks | `[RT]` `[C74]` `[BSM]` |
| `duplicate_clip_to_arrangement(clip, destination_time)` | -> Clip; types must match | `[RT]` `[AMCP]` |
| `delete_clip(clip)` | positional; arrangement clips (raises for clips of another track) | `[RT]` |
| `duplicate_clip_slot(index)` | -> int (destination slot; may create a scene) | `[RT]` |
| `delete_device(index)` | positional | `[RT]` |
| `duplicate_device(index)` | positional; Live 12 | `[RT]` `[RC]` |
| `insert_device(DeviceName, DeviceIndex=-1)` | -> Device; **Live 12.3+**; *native* devices only, by their UI name ("EQ Eight", "Utility", "Drum Rack", …); not plug-ins, not Max devices; index rules as with the mouse (MIDI effects before the instrument) — raises otherwise. Feature-detect with `hasattr(track, "insert_device")`, fall back to the browser | `[RT]` `[C74 track]` `[BSM]` |
| `create_take_lane()` | -> TakeLane; Live 12 | `[RT]` |
| `stop_all_clips(Quantized=True)` | | `[RT]` |
| `jump_in_running_session_clip(beats)` | positional | `[RT]` |
| `get_data(key, default_value)`, `set_data(key, value)` | per-track persistent data | `[RT]` |
| **no** API to create/ungroup group tracks, freeze or flatten | | `[RT]` |

### Track.View

| Member | Access | Notes | Src |
|---|---|---|---|
| `selected_device` | ro obs (per `[C74]`; `[RT]` docstring is ambiguous) | select with `song.view.select_device(d)` | `[NSU]` `[C74]` |
| `device_insert_mode` | int rw obs | 0 end, 1 left of selected, 2 right of selected — where `browser.load_item` inserts | `[NSU]` `[C74]` `[FW Push2/browser_component.py]` |
| `is_collapsed` | bool rw obs | arrangement track height | `[NSU]` `[C74]` |
| `select_instrument()` | -> bool | False when the track has no devices | `[NSU]` `[C74]` |

### TakeLane — `Live.TakeLane.TakeLane` (Live 12)

`name` rw; `arrangement_clips` ro; `create_midi_clip(start_time, length)`,
`create_audio_clip(file_path, start_time)` (positional, same rules as on Track). `[RT]`

---

## 6. ClipSlot — `Live.ClipSlot.ClipSlot`

| Member | Access / signature | Notes | Src |
|---|---|---|---|
| `clip` | ro | None when empty | `[RT]` |
| `has_clip` | bool ro obs | | `[RT]` |
| `has_stop_button` | bool rw obs | | `[RT]` `[C74]` |
| `is_playing`, `is_recording`, `is_triggered` | bool ro obs | | `[RT]` `[C74]` |
| `playing_status` | int ro obs | ClipSlotPlayingState (group slots) | `[RT]` `[C74]` |
| `is_group_slot`, `controls_other_clips`, `will_record_on_start` | bool ro | | `[RT]` |
| `color`, `color_index` | ro | None when no clip | `[RT]` |
| `create_clip(length)` | positional -> Clip | empty slot on a MIDI track, length > 0 | `[RT]` `[C74]` |
| `create_audio_clip(path)` | positional -> Clip | absolute path; empty slot on a non-frozen audio track. Present in the 11.x capture and 12.4 (`[AMCP]` says 12.0.5+) — feature-detect | `[RT]` `[C74]` `[AMCP]` |
| `delete_clip()` | | raises when empty | `[RT]` |
| `duplicate_clip_to(target_slot)` | positional, **returns None** | overwrites target; raises on type mismatch or group slots | `[RT]` `[AOSC clip_slot.py]` |
| `fire(record_length=<none>, launch_quantization=<none>, force_legato=False)` | | plays the clip / triggers the stop button / records on an armed empty slot; `record_length` raises if the slot has a clip; `launch_quantization` is a Song.Quantization value | `[RT]` `[C74]` |
| `stop()` | | stops the track's clips (any slot) | `[RT]` `[C74]` |
| `set_fire_button_state(state)` | positional | | `[RT]` |

---

## 7. Clip — `Live.Clip.Clip`

### Common properties

| Property | Type, access | Notes | Src |
|---|---|---|---|
| `name` | str rw obs | | `[RT]` |
| `color` / `color_index` | int rw obs | | `[RT]` |
| `is_midi_clip`, `is_audio_clip`, `is_arrangement_clip`, `is_session_clip` (12), `is_take_lane_clip` (12) | bool ro | | `[RT]` `[RC]` |
| `length` | float **ro** | loop length if looped, else end−start marker | `[RT]` `[C74]` |
| `loop_start`, `loop_end` | float rw obs | beats (seconds for unwarped audio); keep `loop_start < loop_end` — to move a loop later set `loop_end` first | `[RT]` `[C74]` |
| `start_marker`, `end_marker` | float rw obs | end cannot precede start | `[RT]` `[C74]` |
| `position` | float rw obs | = loop_start; setting keeps the loop length | `[RT]` `[C74]` |
| `looping` | bool rw obs | unwarped audio cannot loop | `[RT]` `[C74]` |
| `start_time`, `end_time` | float ro obs | arrangement position / end (session: launch time) | `[RT]` `[C74]` |
| `playing_position` | float ro obs | | `[RT]` |
| `is_playing` | bool rw | setting fires/stops | `[RT]` `[C74]` |
| `is_recording`, `is_triggered`, `is_overdubbing`, `will_record_on_start` | bool ro | | `[RT]` |
| `muted` | bool rw obs | clip activator | `[RT]` `[C74]` |
| `launch_mode` | int rw obs | LaunchMode | `[RT]` `[C74]` |
| `launch_quantization` | int rw obs | **ClipLaunchQuantization, 0 = global** | `[RT]` `[C74]` |
| `legato` | bool rw obs | | `[RT]` `[C74]` |
| `velocity_amount` | float rw obs 0–1 | | `[RT]` `[C74]` |
| `signature_numerator` / `signature_denominator` | int rw obs | | `[RT]` |
| `groove` rw obs, `has_groove` ro | | | `[RT]` `[C74]` |
| `has_envelopes` | bool ro obs | | `[RT]` |
| `automation_envelopes` | ro vector | Live 12 | `[RT]` `[RC]` |
| `view` | ro Clip.View | | `[RT]` |

### Audio-only (raise on MIDI clips)

| Property / method | Notes | Src |
|---|---|---|
| `warping` bool rw obs | set is deferred internally by Live | `[RT]` `[C74]` |
| `warp_mode` int rw obs | WarpMode; `available_warp_modes` ro list | `[RT]` `[C74]` |
| `gain` float rw obs **0.0–1.0**; `gain_display_string` ro str (e.g. "1.3 dB") | | `[RT]` `[C74]` |
| `pitch_coarse` int rw obs **−48…48**; `pitch_fine` float rw obs **−50…49** (cents) | | `[RT]` `[C74]` |
| `file_path` str ro, `sample_length` int ro (frames), `sample_rate` float ro, `ram_mode` bool rw | | `[RT]` `[C74]` |
| `warp_markers` ro vector of `WarpMarker(sample_time, beat_time)` (last one is hidden) | | `[RT]` `[C74]` |
| `add_warp_marker(warp_marker)` | warped clips; pass `Live.Clip.WarpMarker(sample_time, beat_time)` (dict form is the Max API; Python form UNVERIFIED) | `[RT]` `[C74]` |
| `move_warp_marker(marker_beat_time, beat_time_distance)`, `remove_warp_marker(beat_time)` | | `[RT]` |
| `beat_to_sample_time(beat_time)`, `sample_to_beat_time(sample_time)` (warped), `seconds_to_sample_time(seconds)` (unwarped) | | `[RT]` |

### MIDI note API (Live 11+ "extended" API; raise on audio clips)

| Signature | Returns / notes | Src |
|---|---|---|
| `Live.Clip.MidiNoteSpecification(pitch, start_time, duration, velocity=100.0, mute=False, probability=1.0, velocity_deviation=0.0, release_velocity=64.0)` | pitch/start/duration **required**; write-only object (no readable attributes) | `[RT]` `[FW pushbase/note_editor_component.py]` |
| `add_new_notes(specs)` | positional; any iterable of specs; **-> IntU64Vector of new note ids** | `[RT]` `[C74]` `[FW APC64/render_to_clip.py]` |
| `get_notes_extended(from_pitch, pitch_span, from_time, time_span)` | keyword names allowed (Ableton uses them); notes **starting** in the area; -> `MidiNoteVector` of `MidiNote` | `[RT]` `[FW ableton/v3/.../note_editor.py]` |
| `get_all_notes_extended()` | all notes, regardless of markers/loop (11.1+) | `[RT]` `[C74]` `[BSM]` |
| `get_notes_by_id(note_ids)` | -> MidiNoteVector | `[RT]` |
| `get_selected_notes_extended()` | -> MidiNoteVector | `[RT]` |
| `apply_note_modifications(notes)` | positional; **pass the `MidiNoteVector` you got from a get_* call, modified in place** (vectors cannot be built from Python; a subset is fine — get it with `get_notes_by_id`); must not contain foreign notes; preserves per-note expression | `[RT]` `[BSM]` `[FW]` |
| `remove_notes_extended(from_pitch, pitch_span, from_time, time_span)` | | `[RT]` `[AMCP]` |
| `remove_notes_by_id(ids)` | positional | `[RT]` `[AOSC]` |
| `select_all_notes()`, `deselect_all_notes()`, `select_notes_by_id(ids)` | | `[RT]` |
| `duplicate_notes_by_id(note_ids, destination_time=None, transposition_amount=0)` | -> IntU64Vector (11.1.2+) | `[RT]` `[C74]` |
| `duplicate_region(region_start, region_length, destination_time, pitch=-1, transposition_amount=0)` | | `[RT]` `[C74]` |
| `quantize(grid, amount)` | positional; **grid = Song.RecordingQuantization value**, amount 0–1; also aligns warp markers on audio | `[RT]` `[FW Launchpad_Pro, APC64]` |
| `quantize_pitch(pitch, grid, amount)` | positional | `[RT]` |
| `note_number_to_name(midi_pitch)` | -> str (Live 12, tuning-aware) | `[RT]` `[RC]` |
| legacy `get_notes`, `set_notes`, `remove_notes`, `get_selected_notes`, `replace_selected_notes` | still present, lose probability/expression — **do not use** | `[RT]` |

`MidiNote` attributes (all writable on the returned copy): `note_id` (unique within the clip),
`pitch` int 0–127, `start_time` float (beats, absolute clip time), `duration` float,
`velocity` float 0–127, `mute` bool, `probability` 0–1, `velocity_deviation` −127…127,
`release_velocity` float. `[RT]` `[C74]`

### Transport / editing

`fire()` (no arguments), `stop()`, `set_fire_button_state(state)`,
`move_playing_pos(beats)`, `scrub(scrub_position)`, `stop_scrub()`, `crop()`,
`duplicate_loop()` (doubles the loop, duplicating notes and envelopes). `[RT]` `[C74]`

### Automation (session clips only)

| Signature | Notes | Src |
|---|---|---|
| `automation_envelope(parameter)` | -> Envelope or None; **None for arrangement clips and for parameters of another track** | `[RT]` |
| `create_automation_envelope(parameter)` | -> Envelope; only when none exists; raises if impossible | `[RT]` |
| `clear_envelope(parameter)`, `clear_all_envelopes()` | | `[RT]` |

Arrangement (track) automation is **not reachable** through the Python LOM.

`Live.Envelope.Envelope` (Live 12; `Live.Clip.AutomationEnvelope` was removed `[RC]`):
`parameter` ro; `insert_step(time, length, value)` positional; `value_at_time(time)` -> float;
`events_in_range(a, b)` -> vector of `EnvelopeEvent(time, value, control_coefficients)`;
`create_event(event)`; `delete_events_in_range(a, b)` — whether `b` is an end time or a
length is UNVERIFIED (the stub treats it as end time); these three are newer than the
Cycling '74 docs — feature-detect. `[RT]`

### Clip.View

`grid_quantization` int rw (GridQuantization), `grid_is_triplet` bool rw, `show_loop()`,
`show_envelope()`, `hide_envelope()`, `select_envelope_parameter(parameter)`. `[NSU]` `[C74]`

---

## 8. Device & DeviceParameter

### Device — `Live.Device.Device`

| Member | Access | Notes | Src |
|---|---|---|---|
| `name` | str rw obs | title-bar name | `[RT]` `[C74]` |
| `class_name` | str ro | see table below | `[RT]` |
| `class_display_name` | str ro | browser name | `[RT]` |
| `type` | int ro | DeviceType (**midi_effect = 4**) | `[RT]` `[C74]` |
| `is_active` | bool **ro** obs | False when off or inside an off rack — **toggle with `parameters[0].value`** ("Device On") | `[RT]` `[FW Push2/device_navigation.py]` |
| `parameters` | ro vector obs | **`parameters[0]` is always "Device On"** | `[FW]` |
| `can_have_chains`, `can_have_drum_pads` | bool ro | racks / drum racks | `[RT]` |
| `latency_in_samples`, `latency_in_ms` | ro | | `[RT]` |
| `can_compare_ab`, `is_using_compare_preset_b`, `save_preset_to_compare_ab_slot()` | 12.3+ (the latter two raise unless `can_compare_ab`) | `[RT]` `[C74]` |
| `store_chosen_bank(bank_index, preset_index)` | positional | `[RT]` |
| `view` | Device.View: `is_collapsed` bool rw | | `[NSU]` `[C74]` |

`class_name` values (verified in `[FW]` bank definitions / `[C74]`): Simpler `OriginalSimpler`,
Sampler `MultiSampler`, Drum Rack `DrumGroupDevice`, Instrument Rack `InstrumentGroupDevice`,
Audio Effect Rack `AudioEffectGroupDevice`, MIDI Effect Rack `MidiEffectGroupDevice`, VST2 and
VST3 plug-ins `PluginDevice`, Audio Units `AuPluginDevice`, Max for Live `MxDeviceInstrument` /
`MxDeviceAudioEffect` / `MxDeviceMidiEffect`, Wavetable `InstrumentVector`, Analog
`UltraAnalog`, Operator `Operator`, Drift `Drift`, Meld `InstrumentMeld`, Drum Sampler
`DrumCell`, EQ Eight `Eq8`, Compressor `Compressor2`, Glue `GlueCompressor`, Utility
`StereoGain`, Auto Filter `AutoFilter`, Arpeggiator `MidiArpeggiator`, Chord `MidiChord`,
Scale `MidiScale`. Prefer `isinstance(d, Live.PluginDevice.PluginDevice)` etc. over names.

### DeviceParameter — `Live.DeviceParameter.DeviceParameter`

| Member | Access | Notes | Src |
|---|---|---|---|
| `value` | float rw obs | internal value in [`min`, `max`]; **clamp before writing** (out-of-range writes raise; exact exception type UNVERIFIED) | `[RT]` `[C74]` |
| `display_value` | float rw | the GUI value (Live 12) | `[RT]` `[RC]` `[C74]` |
| `min`, `max` | float ro | | `[RT]` |
| `default_value` | float ro | **only for non-quantized** parameters | `[RT]` `[C74]` |
| `is_quantized` | bool ro | True for booleans/enums (MIDI-pitch-like ints are not) | `[RT]` `[C74]` |
| `value_items`, `short_value_items` | ro StringVector | **raise unless `is_quantized`** | `[RT]` |
| `name` | str **ro** obs | as shown in the automation chooser | `[RT]` `[C74]` |
| `original_name` | str ro | macro name before assignment | `[RT]` `[C74]` |
| `is_enabled` | bool ro | False when macro-mapped / Max-controlled | `[RT]` `[C74]` |
| `state` | int ro obs | ParameterState | `[RT]` `[C74]` |
| `automation_state` | int ro obs | AutomationState | `[RT]` `[C74]` |
| `str_for_value(value)` | positional -> str | display text incl. units; `str(param)` = current value text | `[RT]` `[C74]` |
| `re_enable_automation()` | | | `[RT]` |
| `begin_gesture()`, `end_gesture()` | | group changes while recording automation | `[RT]` |

Plug-ins expose only the parameters in Live's "Configure" list (`[C74 plugindevice]`,
`[RT]` `get_parameter_names`).

---

## 9. Racks, chains, drum pads

### RackDevice — `Live.RackDevice.RackDevice`

| Member | Access / signature | Notes | Src |
|---|---|---|---|
| `chains` | ro vector obs | raises if `can_have_chains` is False | `[RT]` |
| `return_chains` | ro vector | | `[RT]` |
| `drum_pads` | ro vector | **128 pads for the topmost Drum Rack, 0 for nested ones**; raises on non-drum racks | `[RT]` `[C74]` |
| `visible_drum_pads` | ro vector | the 16 visible pads (`view.drum_pads_scroll_position` × 4 …) | `[RT]` `[C74]` |
| `has_drum_pads` | bool ro | raises on non-drum racks | `[RT]` |
| `chain_selector` | ro DeviceParameter | | `[RT]` `[C74]` |
| `macros_mapped` | ro tuple of bool (one per macro) | | `[RT]` |
| `has_macro_mappings` | bool ro obs | | `[RT]` |
| `visible_macro_count` | int ro obs | `add_macro()` / `remove_macro()` (raise at the limits; Live 11+ racks have up to 16 macros) | `[RT]` `[C74]` |
| `variation_count` | int ro obs | | `[RT]` |
| `selected_variation_index` | int rw | raises when out of range | `[RT]` `[C74]` |
| `store_variation()`, `recall_selected_variation()`, `recall_last_used_variation()`, `delete_selected_variation()`, `randomize_macros()` | Live 11+ | `[RT]` `[C74]` |
| `insert_chain(Index=-1)` | -> Chain; **12.3+**; a Drum Rack chain starts with `in_note = -1` ("All Notes") — set `in_note` to the pad note | `[RT]` `[C74]` |
| `copy_pad(source_index, destination_index)` | positional, pad notes 0–127; raises on empty source | `[RT]` `[C74]` |
| `is_showing_chains` ro, `can_show_chains` ro | | | `[RT]` |

Rack parameter layout: `parameters[0]` "Device On", then "Macro 1" … "Macro 16" (values
0–127), then "Chain Selector" — macro count verified `[FW]`, exact order after "Device On"
UNVERIFIED (look macros up by name/original_name).

RackDevice.View (`device.view`): `selected_chain` rw obs, `selected_drum_pad` rw obs (drum
racks), `drum_pads_scroll_position` int rw 0–28, `is_showing_chain_devices` bool rw,
`is_collapsed`. `[NSU]` `[C74]`

### Chain — `Live.Chain.Chain`

`name` str rw obs, `color` / `color_index` rw, `is_auto_colored` rw, `mute` / `solo` rw,
`muted_via_solo` ro, `devices` ro, `mixer_device` ro (ChainMixerDevice),
`has_audio_input/output`, `has_midi_input/output` ro;
`delete_device(index)`, `duplicate_device(index)` (12), `insert_device(DeviceName,
DeviceIndex=-1)` (**12.3+**, native devices only). `[RT]` `[C74 chain]`

`Live.DrumChain.DrumChain(Chain)`: `in_note` int rw (−1 = all notes, **12.3+**), `out_note` int
rw, `choke_group` int rw. `[RT]` `[C74 drumchain]`

ChainMixerDevice: `volume`, `panning`, `chain_activator` (DeviceParameter), `sends`. `[RT]`

### DrumPad — `Live.DrumPad.DrumPad`

`note` int ro, `name` str **ro** (derived from its chains), `mute` / `solo` bool rw obs,
`chains` ro vector, `delete_all_chains()`. `[RT]` `[C74 drumpad]`

---

## 10. Plug-ins, Max devices, Simpler, Sample

### PluginDevice — `Live.PluginDevice.PluginDevice`

`presets` ro StringVector, `selected_preset_index` int rw obs,
`get_parameter_names(begin=0, end=-1)` -> StringVector. `parameters` = "Device On" + the
Configure list only. `[RT]` `[C74]` Measured on 12.4.5 (§19.1, T4):
`get_parameter_names()` lists **every** parameter of the plug-in (Serum 2 VST3: 2623), never
"Device On", and changes with the plug-in's state; `is_editor_open` rw; there is no API to add
to the Configure list.

### MaxDevice — `Live.MaxDevice.MaxDevice`

`audio_inputs`, `audio_outputs`, `midi_inputs`, `midi_outputs` (DeviceIO: `routing_type`,
`routing_channel` rw, `available_routing_types/channels`), `get_bank_count()`,
`get_bank_name(i)`, `get_bank_parameters(i)`, `get_value_item_icons(param)`. `[RT]`

### SimplerDevice — `Live.SimplerDevice.SimplerDevice` (class_name `OriginalSimpler`)

| Member | Access | Notes | Src |
|---|---|---|---|
| `sample` | ro Sample obs | **None when empty** (UNVERIFIED that it is exactly None; treat falsy as empty) | `[RT]` `[C74]` |
| `playback_mode` | int rw obs | PlaybackMode | `[RT]` `[C74]` |
| `slicing_playback_mode` | int rw obs | SlicingPlaybackMode | `[RT]` `[C74]` |
| `multi_sample_mode` | bool ro | | `[RT]` `[C74]` |
| `pad_slicing`, `retrigger` | bool rw | | `[RT]` `[C74]` |
| `voices` | int rw | valid values: `Live.SimplerDevice.get_available_voice_numbers()` | `[RT]` |
| `pitch_bend_range`, `note_pitch_bend_range` | int rw | | `[RT]` |
| `playing_position` (0–1), `playing_position_enabled` | ro | | `[RT]` `[C74]` |
| `can_warp_as`, `can_warp_double`, `can_warp_half` | bool ro | | `[RT]` |
| `crop()`, `reverse()`, `warp_as(beat_time)`, `warp_double()`, `warp_half()`, `guess_playback_length()` -> float | raise on an empty Simpler | `[RT]` `[C74]` |
| `replace_sample(file_path)` | loads/replaces the sample from an absolute path — present in the 12.4 runtime, absent from Cycling '74 docs: feature-detect | `[RT]` `[RC]` |

SimplerDevice.View: `selected_slice`, `sample_start`, `sample_end`, `sample_loop_start`,
`sample_loop_end`, `sample_loop_fade`, `sample_env_fade_in/out` (frames, −1 when empty). `[NSU]`

### Sample — `Live.Sample.Sample`

`file_path` ro, `length` int ro (frames), `sample_rate` ro, `start_marker` / `end_marker` int
rw (frames), `gain` float rw, `warping` bool rw, `warp_mode` int rw, `warp_markers` ro,
`slices` ro (frames), `slicing_style`, `slicing_sensitivity` (0–1), `slicing_beat_division`,
`slicing_region_count`, `beats_granulation_resolution`, `beats_transient_envelope` (0–100),
`beats_transient_loop_mode`, `complex_pro_envelope`, `complex_pro_formants`, `texture_flux`,
`texture_grain_size`, `tones_grain_size` (all rw);
methods `insert_slice(slice_time)`, `move_slice(old_time, new_time)` -> int,
`remove_slice(slice_time)`, `clear_slices()`, `reset_slices()`, **`gain_display_string()` (a
method here)**, `beat_to_sample_time(beat_time)`, `sample_to_beat_time(sample_time)` (warped
only). `[RT]` `[C74 sample]`

---

## 11. MixerDevice — `Live.MixerDevice.MixerDevice` (`track.mixer_device`)

| Member | Type | Range / notes | Src |
|---|---|---|---|
| `volume` | DeviceParameter | 0.0–1.0 (0.85 ≈ 0 dB; mapping UNVERIFIED beyond that) | `[RT]` |
| `panning` | DeviceParameter | −1.0…1.0 | `[RT]` |
| `sends` | vector of DeviceParameter | one per return track, 0.0–1.0 | `[RT]` `[C74]` |
| `track_activator` | DeviceParameter | 0/1 (track on) | `[RT]` |
| `crossfade_assign` | **int** rw obs | 0 A, 1 none, 2 B; not on the master track — there is **no** `crossfader_assign` | `[RT]` `[C74]` `[FW]` |
| `panning_mode` | int rw obs | 0 stereo, 1 split stereo | `[RT]` `[C74]` |
| `left_split_stereo`, `right_split_stereo` | DeviceParameter | used when `panning_mode == 1` | `[RT]` `[C74]` |
| `cue_volume`, `crossfader`, `song_tempo` | DeviceParameter | **master track only** (UNVERIFIED whether other tracks raise or return None) | `[RT]` `[C74]` |

Parameter *names* of mixer parameters (e.g. "Track Volume") are UNVERIFIED — address them
through these attributes, never by name.

---

## 12. Scene — `Live.Scene.Scene`

| Member | Access / signature | Notes | Src |
|---|---|---|---|
| `name`, `color`, `color_index` | rw obs | | `[RT]` |
| `is_empty`, `is_triggered` | bool ro | | `[RT]` |
| `clip_slots` | ro vector | one per track | `[RT]` |
| `tempo` | float rw obs | **−1 while `tempo_enabled` is False** | `[RT]` `[C74]` |
| `tempo_enabled` | bool rw obs | | `[RT]` `[C74]` |
| `time_signature_numerator` / `_denominator` | int rw obs | −1 while disabled | `[RT]` `[C74]` |
| `time_signature_enabled` | bool | `[RT]` says "Get" only (read-only); `[AOSC]` exposes a setter — UNVERIFIED. Enable by setting numerator/denominator; the stub treats it as read-only | `[RT]` `[AOSC]` |
| `fire(force_legato=False, can_select_scene_on_launch=True)` | | fires all slots, selects the scene | `[RT]` `[C74]` |
| `fire_as_selected(force_legato=False)` | | fires the *selected* scene and selects the next | `[RT]` `[C74]` |
| `set_fire_button_state(state)` | positional | | `[RT]` |

## 13. Groove pool

`song.groove_pool.grooves` (ro); `Live.Groove.Groove`: `name`, `base` (Groove.Base),
`quantization_amount`, `random_amount`, `timing_amount`, `velocity_amount` (rw). Clips:
`clip.groove` rw. `[RT]` `[C74]`

---

## 14. Browser — `Live.Browser.Browser` (`app.browser`)

| Member | Access | Notes | Src |
|---|---|---|---|
| `audio_effects`, `clips`, `current_project`, `drums`, `instruments`, `max_for_live`, `midi_effects`, `packs`, `plugins`, `samples`, `sounds`, `user_library` | ro BrowserItem | the category roots | `[RT]` |
| `user_folders` | ro **list** of BrowserItem | folders added in Places | `[RT]` `[BSM]` |
| `colors` | ro **list** of BrowserItem | colour tags | `[RT]` |
| `legacy_libraries` | ro list | always empty | `[RT]` |
| (no `splice` root) | | where Live 12.3+'s Splice content appears in the Python browser is UNVERIFIED — search `user_folders`/`samples`/`packs` | `[RT]` |
| `hotswap_target` | rw obs | device / drum pad / None; Push sets it before `load_item` to replace | `[RT]` `[FW pushbase/browser_modes.py]` |
| `filter_type` | int rw obs | FilterType | `[RT]` `[FW]` |
| `load_item(item)` | positional | see below | `[RT]` |
| `preview_item(item)`, `stop_preview()` | | | `[RT]` |
| `relation_to_hotswap_target(item)` | -> Relation | | `[RT]` |

### BrowserItem (a plain instance — no `canonical_parent`)

`name` ro, `uri` ro (unique id, e.g. `query:Synths#Instrument%20Rack:Bass:FileId_5116`
`[AMCP]`), `is_folder`, `is_device`, `is_loadable`, `is_selected`, `source` ro,
`children` ro vector (loads the folder lazily — can be slow), `iter_children` — a
**property** returning a `BrowserItemIterator` (iterable, supports `len()`), not a method.
`[RT]` `[FW Push2/browser_component.py]` `[BSM]`

### `load_item` semantics

* Loads onto `song.view.selected_track`; the device goes where
  `selected_track.view.device_insert_mode` says (0 end, 1/2 left/right of the selected
  device). With `hotswap_target` set, the item replaces/loads into that target. This is how
  every working script loads devices: select the track, then `load_item`. `[FW
  Push2/browser_component.py]` `[AMCP]` `[BSM]` `[ZIF]` `[JPX]`
* Samples / audio files via `load_item`: behaviour (Simpler on a MIDI track, clip in the
  highlighted slot on an audio track, replacing a hot-swapped Simpler's sample) is
  **UNVERIFIED**. For deterministic results use `ClipSlot.create_audio_clip(path)`,
  `Track.create_audio_clip(path, position)` and `SimplerDevice.replace_sample(path)`.
* Browser walks run on the main thread: bound depth and visited-count (`[BSM]` uses 20000).

---

## 15. Live.Base and misc modules

`Live.Base.Timer(callback, interval, repeat=False, start=False)` (§1),
`Live.Base.LimitationError`, `Live.Base.log(str)`, vectors (`Vector`, `IntVector`,
`FloatVector`, `StringVector`, `ObjectVector`: read-only sequences). `Live.Conversions`
(`audio_to_midi_clip(song, clip, type)`, `create_midi_track_with_simpler(song, clip)`,
`create_drum_rack_from_audio_clip(song, clip)`, `sliced_simpler_to_drum_rack(song, simpler)`,
`move_devices_on_track_to_new_drum_rack_pad(song, track_index)`,
`create_midi_track_from_drum_pad(song, pad)`, `is_convertible_to_midi(song, clip)`) exists in
12.4 — UNVERIFIED in practice. `[RT]`

## 16. Not available through the Python LOM

Saving the set, audio export/render, freeze/flatten, grouping/ungrouping tracks, arrangement
track automation, plug-in parameters outside "Configure", menu commands, preferences
(count-in, exclusive arm/solo are read-only). `[RT]` (absence) `[C74]`

---

## 17. What this check changed (stub / core / ARCHITECTURE §10)

| Area | Before (wrong) | Verified truth |
|---|---|---|
| `DeviceType.midi_effect` | 3 | **4** |
| `RecordingQuantization` | quarter missing, values shifted | 0 none, 1 1/4 … 8 1/32 |
| `GridQuantization` | 1 = 1/32 … 6 = bar | 1 = 8 bars … 9 = 1/32; only for `clip.view` |
| `clip.quantize` grid | GridQuantization | **RecordingQuantization** |
| `clip.launch_quantization` | Song.Quantization, default 0 = none | **ClipLaunchQuantization**, 0 = global |
| Monitoring enum | `Live.Track.MonitoringState` | `Live.Track.Track.monitoring_states` |
| Mixer crossfade | `crossfader_assign` DeviceParameter | `crossfade_assign` **int** (+ `panning_mode`) |
| Device parameters | no "Device On", `is_active` writable | `parameters[0]` = "Device On", `is_active` read-only |
| `value_items` / `default_value` | always readable | raise for non-quantized / quantized parameters |
| Note API | tuples, `add_new_notes` -> None, list accepted | `MidiNoteVector`, returns note ids, vector required; `get_all_notes_extended`, `*_by_id` added |
| `MidiNoteSpecification` | all args optional, readable | pitch/start/duration required, write-only |
| Envelopes | `ClipEnvelope`, allowed on arrangement clips | `Live.Envelope.Envelope`, session clips only, same-track parameters only |
| `Song.View.selected_device` | existed | does not exist; `select_device(device, ShouldAppointDevice=True)` |
| `Track.View.selected_device` | writable | read-only |
| `CuePoint.time` | writable | read-only |
| Scene tempo / signature | 4/4 while disabled | −1 while disabled |
| `Scene.fire` | `can_trigger_stop` kwarg | `can_select_scene_on_launch` |
| `ClipSlot.fire` | `**kwargs` | `record_length`, `launch_quantization`, `force_legato` |
| `duplicate_track` / `duplicate_scene` / `duplicate_clip_to` | returned the copy | return None |
| `Track.insert_device`, `Chain.insert_device`, `RackDevice.insert_chain`, `DrumChain.in_note`, `ClipSlot.create_audio_clip`, `SimplerDevice.replace_sample`, `Track.duplicate_device`, take lanes | missing | present (12.x; feature-detect) |
| Browser | `iter_children()` method, `colors` an item, `relation` none = 0, items with `canonical_parent` | property, list roots (`colors`, `user_folders`), none = 3, no parent |
| Application | `get_major_minor_version` | removed; `get_variant()` / `get_build_id()` added → real edition detection |
| `schedule_message` | assumed to run immediately / from `c_instance` | queued in the surface's task group, runs from base `update_display`; not thread-safe |
| Core code | socket thread called `schedule_message`; `update_display` skipped the base; beacon read the LOM; logging from threads; serializer used wrong enum tables and `value_items` for type detection; `lom.set` enum lookup used `__members__` | all fixed (see §1 and `remote_script/LiveBridge/*`) |

## 18. UNVERIFIED (do not rely on without a real-Live check — `tests/integration_check.py`)

* `browser.load_item` behaviour for samples/audio files; where Splice content lives.
* LOM mutations from `Live.Base.Timer` callbacks; the "no changes from notifications" rule.
* Whether LOM wrappers are cached (`is` identity) — always use `==`.
* Exception type for out-of-range `parameter.value` / `song.tempo` writes (raise vs clamp).
* `Scene.time_signature_enabled` writability; what enables a scene time signature.
* Values ≥ 5 of `RoutingTypeCategory`, ≥ 3 of `FilterType` and `SlicingBeatDivision`,
  `Groove.Base.gb_thirtytwo`; `GridQuantization` 5–10 (inferred from order).
* `crossfade_assignments` member spelling; mixer parameter names; volume dB mapping; clip gain
  dB mapping.
* Rack parameter order after "Device On" ("Chain Selector" position).
* `Envelope.events_in_range` / `delete_events_in_range` second argument (end vs length).
* `add_warp_marker` argument form in Python (WarpMarker object vs dict).
* `track.clip_slots` on the master track; `current_monitoring_state` on return tracks;
  `mixer.cue_volume` on non-master tracks (raise vs None).
* Colour palette size (70 colours, index 0–69).
* `begin_undo_step` / `end_undo_step` nesting semantics.
* `Live.Conversions.*` in practice.

## 19. Corrections from the real 12.4.5 dump and read-only checks (integration phase)

`docs/LIVE_API_DUMP_12.4.5.md` (runtime introspection of Live 12.4.5 Suite) and read-only
`eval.python` checks by the module builders are the final authority. Where they disagree
with the sections above, this is the truth (the stub in `tests/live_stub` still follows the
older text in the marked places; handlers feature-detect both):

| Topic | Above / stub | Real Live 12.4.5 |
|---|---|---|
| `ClipSlot.fire` | §-table lists the kwargs; the dump summary shows `fire()` only | two overloads: `fire()` and `fire(record_length, launch_quantization, force_legato)` (the dump only captured the first docstring line) |
| Return / master routing | returns have no input, master no input/output | returns **and** the master expose input and output routing (master input "Ext. In", output "Ext. Out"); the master's available outputs include **"Main"** (not "Master"). Stub: still the old behaviour |
| Routing categories | "All Ins" / "Computer Keyboard" external | category 7 |
| Monitoring on returns/master | — | raises "Main and Return Tracks have no monitoring state!" |
| `crossfade_assign` | — | exists on return tracks, raises on the master |
| `Scene.time_signature_enabled` | read-only (§12) | **rw** (real fset in the dump; verified: True fills in the song signature, False sets −1/−1). `scenes.set` tries the setter, then falls back to numerator/denominator (−1 = off). Stub: rw like Live |
| `Scene.color_index` | int | can be `None` |
| `Song.overdub` | alias of `arrangement_overdub` (stub) | legacy hook: "hooks to session record, but never starts playback" — LiveBridge only uses `arrangement_overdub` and `session_record` |
| `Song.View` | — | also `mod_mapping_device`, `mod_mapping_parameter`; `selected_chain` is writable |
| `PluginDevice.is_editor_open` | missing in §10 | present, rw (feature-detected) |
| `Browser.FilterType` ≥ 3 | UNVERIFIED | midi_effect_hotswap=3, drum_pad_hotswap=4, midi_track_devices=5, samples=6, count=7 |
| `Sample.SlicingBeatDivision` 3–10 | UNVERIFIED | full order in the dump (used by `simpler.set`) |
| `Clip.pitch_fine` | −50…49 | accepts any cents; whole semitones carry into `pitch_coarse` while \|fine\| ≥ 50 (60 → coarse +1 / fine −40, 50 → +1 / −50, −50 → −1 / +50), clamped at coarse ±48 (T2 live test; the docstring says −500…500) |
| `DrumCellDevice` (Drum Sampler) | — | exposes gain, but no sample file path |
| Browser items | folders have `is_folder` | device items ("Analog") and packs have `is_folder == False` but do have children — walks read children on every item |
| Mixer names/values | UNVERIFIED | "Track Volume" / "Track Panning", sends "A-<return name>", master cue "Preview Volume"; volume 0.85 = 0 dB, sends 0.0, crossfader 0.0; measured volume/send dB curves live in `handlers/mixer.py` |
| Splice | no root | not exposed through the Python browser; Live's own Splice downloads go to the folder set in `Library.cfg` (`SpliceDownloadFolderModeMember`); LiveBridge imports files from disk (`live_splice_*`, `live_sample_import`) |

Still UNVERIFIED after the real-Live tests of 2026-09-10 (§19.1–§19.5):
`clip.gain_display_string` format, loading a sample onto a **MIDI** track through
`browser.load_item` while the Arrangement view is focused (the stub still creates a Simpler) and
the monitoring state of new audio tracks (came up "Off" on the test machine — possibly a
preference; the stub keeps "Auto"). Also still UNVERIFIED: ending a session recording started
with `ClipSlot.fire(record_length=...)` by writing `song.session_record = False`
(`record.session` / `record.stop` rely on it; testing it changes song-level record state, which
the scratch-set rules of these checks exclude), and a full `automation.record` pass (stub-tested;
on real Live only its refusal paths ran). Resolved since: `duplicate_clip_to_arrangement` with an
arrangement clip as source (§19.4), whether it copies clip envelopes (§19.5: depends on the
track), third-party plug-in parameters (T4, §19.1) and `EnvelopeEvent.control_coefficients`
(no visible effect on `value_at_time`, §19.4).

### 19.1 Measured by the real-Live tests (Live 12.4.5 Suite, macOS arm64, 2026-09-10)

Per-command results: `docs/LIVE_TEST_REPORT.md` (merged from `docs/live_test/T1-global.md`,
`T2-tracks-clips.md`, `T3-devices-browser.md`, `T4-plugins.md`). The shared stub (`tests/live_stub`) mirrors
every item below except the next-tick deferral, which `tests/live_stub_ext/transport_live.py`
switches on per test.

**Runtime / collections**

* Every LOM collection (`song.tracks`, `scenes`, `clip_slots`, `devices`, `parameters`,
  `chains`, `arrangement_clips`, `take_lanes`, mixer `sends` …) is a `Live.Base.Vector`:
  `type(song.tracks).__mro__ == (Vector, Boost.Python.instance, object)` — not a list or tuple
  (`BrowserItem.children` is a `BrowserItemVector`). `len()`, indexing, slicing, iteration and
  `in` work; there is no `index()`. LiveBridge tests collections with `compat.is_sequence`.
* Socket threads only get the GIL while Live's main thread runs Python: a request waited
  several ~100 ms ticks (≈0.5 s round trip, ≈1 s with three clients). `update_display` now
  sleeps 1 ms around the drain while clients are connected → ≈0.1 s round trip.

**Song / transport (T1)**

* Applied on Live's **next tick** (read-back in the same command is stale): `is_playing`
  (`start_playing` / `stop_playing` / `continue_playing`), `current_song_time` (also `jump_by`,
  `CuePoint.jump`), `loop`, `punch_in` / `punch_out`, `session_automation_record`,
  `record_mode`, `back_to_arranger` (song and `Track.back_to_arranger = False`) and clip-slot
  recordings. Immediate: `start_time`, tempo, signature, metronome, `arrangement_overdub`,
  quantization, groove and scale settings.
* `continue_playing()` in the same tick as a `current_song_time` write starts from the *old*
  playhead (the write is lost); set `start_time` (immediate) and call `start_playing()`.
* `song_length` = max(end of arrangement material, loop end) + 32 beats. `current_song_time`
  beyond it raises "Cannot set the Songtime behind the Songlength", `start_time` "Cannot set the
  start time after the song length"; `loop_start` with `value + loop_length` beyond it "Cannot
  set the Loopstart behind the Songlength", `loop_length` "Cannot set the Loop behind the song
  length" — checked on **each** write. `loop_length` below 1 beat is raised to 1.
* `stop_playing()` while already stopped returns playhead and start marker to 0.
* `groove_amount` 0..1.3125 (larger clamps, negative raises "Groove Amount out of range");
  `swing_amount` accepts odd values silently. Tempo 20..999, numerator 1..99, denominator
  1/2/4/8/16 (32 raises, 3 silently becomes 4).
* With arrangement time-signature markers `song.signature_*` is the signature at the playhead
  (writing changes that section); the LOM cannot list or delete the markers.
* `record_mode = True` starts playback (default "Start Playback with Record" preference);
  `overdub = True` switches Session Record on without playing. API arming ignores the
  "Exclusive Arm" preference (`song.exclusive_arm` only governs clicks).

**Cue points**

* `song.cue_points` is in **creation order**, not time order; new cues are named "1", "2" …
* Stopped, `set_or_delete_cue()` / `is_cue_point_selected()` act at Live's **insert marker**,
  which a `current_song_time` write moves to the nearest line of the zoom-dependent Arrangement
  grid (12.7 → 12.0 / 12.75 / 16.0); `jump_by` moves only the reported time; `CuePoint.jump()`
  is exact and also moves the start marker. `app.view.zoom_view(3, "Arranger", False)` zooms
  time in (finer grid, down to 1/256 beat), direction 2 zooms out.

**Scenes**

* `create_scene(i)` copies tempo / signature settings of `scenes[i-1]` and selects the new
  scene; `capture_and_insert_scene()` inserts after the selected scene even when nothing plays
  and copies its name, tempo and signature; `duplicate_scene` keeps the name; firing a scene
  applies its tempo *and* signature (an empty scene does not start the transport).

**Clips, notes, envelopes (T2)**

* `Track.duplicate_clip_slot(i)` **overwrites** slot `i+1` (its docstring says "next free
  slot"); `ClipSlot.fire(force_legato=True)` on an empty slot raises "Can only pass
  force_legato to non-empty slots.".
* Unlooped clips: Live keeps two loop braces; the clip plays `loop_start..loop_end`,
  `start_marker` follows `loop_start` (writes to it are ignored), `end_marker` does not move the
  end, `length` = `loop_end - loop_start`; toggling looping swaps the remembered braces; an
  arrangement clip's `end_time` follows the unlooped brace and keeps its timeline length when
  looping is switched on (lengthen: looping off → end → looping on).
* `crop()` keeps `min(start_marker, loop_start)..loop_end` (looped) / the brace (unlooped);
  `duplicate_loop()` pushes notes after the loop back by the loop length.
* Same-pitch notes never overlap: `add_new_notes` replaces a note with the same pitch+start
  (also inside one batch), swallows notes starting inside the new one, shortens one it starts
  in, and returns only the surviving ids; `apply_note_modifications` collapses same-start
  duplicates (newest id survives) and cuts overlaps at the next note.
* Envelopes are breakpoint lists: `insert_step` writes two breakpoints per border;
  `value_at_time` exactly on a border returns the value *before* it; `create_event` at an
  occupied time adds a second breakpoint after it; `delete_events_in_range` is inclusive;
  `EnvelopeEvent.value` is in internal units (Hz, linear gain) while `create_event` /
  `value_at_time` use the parameter's range.
* Warp marker `sample_time` is in seconds; a fresh clip reports a helper marker 1/32 beat after
  the first. Return-track names read with their letter (`"A-Reverb"`; `name = "X"` on the third
  return reads `"C-X"`). Volume display: 40·v − 34 dB for v ≥ 0.4 (0.85 = "0.0 dB"), sends 6 dB
  lower.

**Devices, racks, browser (T3)**

* `insert_device(name)`: exact case-sensitive UI names; unknown (also Max for Live based
  browser devices such as LFO, Shaper, DS Kick) → `ValueError("Device X not found.")`;
  refusals "Can not insert device 'X': Device chains cannot have more than one instrument
  each." / "…: Only audio effects can be inserted into an audio track." / "Invalid insert index
  for device 'X': Insert MIDI effects before instruments. A valid index would be 0.". Real class
  names include `AutoFilter2`, `AutoPan2`, `Chorus2`, `Erosion2`, `Redux2`, `PhaserNew`, `Tube`,
  `Hybrid`, `Transmute`, `Spectral`, `SpectrumAnalyzer`, `Vinyl`, `Resonator`, `FilterEQ3`,
  `ChannelEq`, `LoungeLizard`, `StringStudio`, `InstrumentImpulse` (all 66:
  `handlers/devices.NATIVE_DEVICE_CLASSES`, stub `NATIVE_DEVICES`).
* `DrumChain.in_note` / `out_note` 0..127 only (−1 raises "Invalid note number." / "Invalid
  note." — no "All Notes" through the API), `choke_group` 0..16 ("Invalid choke group.");
  `insert_chain()` on a Drum Rack lands on C1 (36) whatever pad is selected; `DrumPad.name` =
  chain name / "Multi" / the note name ("D1") for an empty pad; `Chain.mute` **is**
  `mixer_device.chain_activator`; `muted_via_solo` is real.
* Variations: `store_variation` keeps the selection, `delete_selected_variation` leaves −1,
  recall / randomize only touch *mapped* macros.
* Browser: setting the current `hotswap_target` again raises "Couldn't set hotswap target";
  while a target is set the browser is filtered (instrument target → `audio_effects` /
  `midi_effects` empty), `app.view.browse_mode` is true and `filter_type` stays −1; after a
  hot-swap load the new device stays the target. `load_item` of a preset of the same device /
  a kit over a Drum Rack keeps the device object; `.alc` clips create a new MIDI track;
  samples/clips into clip slots do nothing while the Arrangement view is focused; an
  instrument onto an audio track creates a new MIDI track.
* `DeviceParameter.display_value` raises "Invalid display value" for some quantized
  parameters — `str_for_value` is the reliable formatter. `Sample.remove_slice(t)` ignores a
  frame that is not exactly a slice; `move_slice` keeps the slice between its neighbours.
* The Core Library lives next to the executable (`sys.executable` = `…/Contents/MacOS/Live`,
  library in `…/Contents/App-Resources/Core Library`; Windows `…\Resources\Core Library`).
* Live's Plug-Ins browser lists nothing while plug-in use is off in Live's Settings, even with
  AU/VST2 plug-ins installed.

**Third-party plug-ins (T4: Serum 2 v2.1.5 VST3 + AU, Serum 2 FX, Apple AUs)**

* `PluginDevice.class_name` is "PluginDevice" for VST2 and VST3, "AuPluginDevice" for AU;
  `class_display_name` is the plug-in name; `can_compare_ab` false; `latency_in_samples` 0 here.
* `parameters` is "Device On" + the **Configure** list. Live fills that list by itself only for
  small plug-ins (AUNBandEQ 41/41, AUMIDISynth 4/4, DLSMusicDevice 3/3, AUMatrixReverb 2/17,
  AUDelay 3/4) and leaves it empty for big ones: Serum 2 exposes **0 of 2623** (VST3) / 2622
  (AU) after loading. No method adds to it (`dir()` of the real `PluginDevice`, 12.4.5 dump);
  the user clicks Configure in the device title bar and moves the knobs, then saves the set or
  an .adv preset.
* `get_parameter_names(begin=0, end=-1)` lists **every** plug-in parameter (never "Device On"),
  dynamically: switching an AUNBandEQ band to "Low Shelf" drops that band's "Bandwidth" (41 →
  40 names while 41 parameters stay exposed). Serum 2 VST3: 541 synth parameters, 16 × 130
  MIDI-proxy names ("Pitch Bend Chan n", "Aftertouch Chan n", "CC0 Chan n" …), "Mod Wheel",
  "Pitch Bend" (exact list: `tests/live_stub_ext/plugins_live.py:serum2_names`). AUNBandEQ
  names its eight bands' parameters identically ("Frequency", "Gain", "Bandwidth", "Type",
  "Bypass").
* `presets` is `["Default"]` for Serum 2 and every Apple AU tried (AU factory presets are not
  listed); `selected_preset_index` 0. `is_editor_open` is rw and listenable (Live opens the
  window after every load with "Auto-Open Plug-In Windows"); Configure mode cannot be toggled.
* Exposed AU parameters are 0..1 internally; `display_value` is the plain number (50.0,
  15000.0), `str_for_value` the unit string ("50 %", "15000 Hz", "0.00 dB", "0 st").
* Browser: `browser.plugins` → `AUv2/<vendor>/<name>`, `VST/<name>` (flat, uri
  `…#VST:Local:<name>`), `VST3/<vendor>/<name>`; plug-in items are `is_loadable` but
  `is_device` **false** and carry no device type. VST3 is the first "Serum 2" hit.

### 19.2 Checked by the docs/installer pass (Live 12.4.5 Suite, macOS arm64, 2026-09-10)

Read-only probes plus scratch `LB_*` tracks that were deleted again; `tests/integration_check.py
--scenario beat --no-play` exercises most of it end to end (all 16 beat steps PASS on this Live).

* **Preview/cue volume** (`master_track.mixer_device.cue_volume`, name "Preview Volume", 0..1)
  uses exactly the track-volume fader curve: `str_for_value` gives the same text for both at every
  value tried (0 → "-inf dB", 0.1 → "-48.6 dB", 0.2 → "-34.4 dB", 0.4 → "-18.0 dB", 0.7 →
  "-6.0 dB", 0.85 → "0.0 dB", 1.0 → "6.0 dB"), so `mixer.set(cue_volume="-6 dB")` is right.
* **Compressor side-chain**: `Compressor2` parameters include "S/C On", "S/C Listen",
  "S/C Gain", "S/C Mix", "S/C EQ On/Type/Freq/Q/Gain". `routing.route(method="sidechain")` set
  the Compressor's `input_routing_type` to the source track and "S/C On" to 1.0 (`_SIDECHAIN_SWITCHES`
  matches "s/c on"). Glue Compressor, Gate, Auto Filter and Multiband Dynamics have "S/C On" too
  but **no** `available_input_routing_types` — their side-chain source is not reachable; Limiter has
  no side-chain parameters.
* **Utility**: the gain knob is the parameter "Output" (min −1, max 1; "-inf dB" … "35.0 dB",
  default "0.00 dB"); "Bass Freq" 0..1 = "50.0 Hz" … "500 Hz". Display-string writes land exactly
  ("-6 dB" → "-6.00 dB", "200 Hz" → "200 Hz"); a clip envelope on "Output" written with
  "-12 dB" … "0 dB" stores −0.342857 … 0.0 internally.
* **Racks**: `Song.move_device(device, chain, 0)` moves a track device into a rack chain (the
  "group into a rack" workaround); `RackDevice` has no `delete_chain` / `duplicate_chain` / chain
  move and `Chain` no key/velocity/chain-select zone properties (`dir()` of the real objects).
  Macro mapping has no API (see LIVE_API_NOTES §12).
* **Drum-pad hot-swap with a sample**: `browser.load(uri=<sample>, hotswap=true, device=0,
  drum_pad=36)` on the 909 Core Kit replaced pad C1's chain content (an Instrument Rack "Bass Drum")
  with a Simpler (`OriginalSimpler`) named after the sample; the pad is renamed to it.
* **Envelopes and `duplicate_clip_to_arrangement`**: with the Session view focused, the arrangement
  copy of a session MIDI clip that has an envelope (Operator "Filter Freq" or "Track Volume",
  written with `insert_step` through `automation.write` or directly) reports
  `has_envelopes == False` and an empty `automation_envelopes` (checked right away and 2 s later).
  T2 (§19.1 and `handlers/arrangement.py`) recorded the envelopes travelling along — resolved in
  §19.5: the difference is whether the track holds an instrument.
* **Beat scenario** (`integration_check.py --scenario beat`): 909 Core Kit through
  `browser.load(query, category="drums")` (≈1–2 s), `notes.write_pattern` with accents and a ghost
  note (15 notes, rows kick 36 / snare 38 / hat 42), Operator via `devices.insert`,
  `notes.write_chords("Am F C G")` (A3/C4/E4, F3/A3/C4, C3/E3/G3, G3/B3/D4), `mixer.set_many` in
  dB ("-6 dB" reads back "-6.0 dB"), Compressor side-chain, `automation.write` (linear, 9 steps),
  `arrangement.duplicate_clip`, `record.arm` on / off of the drum track, cleanup of all three tracks.

### 19.3 Generated plug-in racks (Serum agent, Live 12.4.5 Suite, macOS arm64, 2026-09-10)

Details and the probe evidence: [PLUGIN_RACKS.md](PLUGIN_RACKS.md). Corrections / facts:

* **`.adg` `PluginParameterSettings`**: a rack preset may list plug-in parameters by VST3
  ParameterId; Live exposes exactly those on the loaded plug-in (the Configure list), for any
  VST3 parameter. **More than 128** entries crash Live (Log.txt ends right after "VST3: parameter
  count is 2623"). A macro mapping (`MacroControlIndex`) needs the range slot
  `<MidiControllerRange><MidiControllerRange Id="0"><Min/><Max/>` (Live's
  `ASlot<AMidiControllerRange>`); an empty `<MidiControllerRange/>` crashes Live. Min/Max are in
  the plug-in's value units and clamped; −1e9..1e9 = the full range (macro/127 = normalized value,
  measured on 7 parameters). `plugin_racks_lib` refuses >128 and always writes the slot; the stub
  raises `LiveWouldCrash` for both.
* Macro values are **not pushed** to the parameters on load, and a macro move applies on the next
  tick — `plugin_racks.expose` syncs every macro to its parameter.
* Live **auto-opens the plug-in window** on the tick after a load ("Auto-Open Plug-In Windows");
  the MCP tool re-applies `editor_open` with a follow-up `plugins.set`.
* The VST3 `Uid` in a preset is the class id as 4 big-endian int32. In a `.vstpreset` the `Comp` /
  `Cont` chunks are the ProcessorState / ControllerState; an empty ProcessorState gives Serum's
  constructor defaults, the shipped Init template gives "- Init -". `.SerumPreset` data is
  rejected as a VST3 state (cannot be embedded).
* Rack names come from the **file name** (`UserName` is ignored); a new rack file is indexed by
  Live in ~3–5 s, an already indexed file can be rewritten and loads at once (read on load); a
  probe rack loads in ~0.3 s; hot-swapping keeps the old plug-in instance ("preset transfer"), so
  a re-expose deletes and re-inserts.
* **Audio Unit** plug-ins in a generated rack expose nothing — VST3 only.
* Serum 2 FX has the same 541 parameter ids as Serum 2 (id blocks: 0 global, 1 oscillators
  A/B/C + Noise/Sub, 2 filters, 3 envelopes, 4 LFOs, 6 Mod 1–64, 7 macros, 9 routing, 10 clip
  player, 12 arp, 14 key/scale, 15 randomization, 17 FX).
* Serum 2's compound displays ("50% [-9.0 dB]" on Main Vol, A/B/C Level, Sub/Noise Level, bus
  volumes) are parsed by both readings (verified by the cross fixer on 12.4.5: Main Vol "70%" →
  "70% [-3.2 dB]", A Level "-6 dB" → "71% [-6.0 dB]", "-12 dB" → "42% [-12.0 dB]"; clip
  automation points "-18 dB" … "-6 dB" work the same way).

### 19.4 Measured by the g1 / g2 fixers (Live 12.4.5 Suite, macOS arm64, 2026-09-10) — VERIFIED

* **Song length** (g1): each `loop_length` write can reach `song_length` and moves it 32 beats on
  immediately; `song_length` also shrinks immediately with the brace. A cue point or the playhead
  behind the old end holds `song_length` after the brace is put back; the start marker does not.
* `continue_playing()` resumes at the last stop point and ignores `current_song_time` writes made
  while stopped (even ticks later).
* Arrangement Record with Punch-In/Out on and the loop **off** records exactly the brace
  (resample 460–468 beats): `record.resample`.
* `Clip.View.grid_quantization` reads back as the enum (`g_sixteenth`);
  `select_envelope_parameter` / `show_envelope` / `hide_envelope` / `show_loop` work on session
  clips.
* **Arrangement copies** (g2): `duplicate_clip_to_arrangement` with an **arrangement clip as
  source** works — notes, envelopes (when copied, §19.5) and the timeline length are kept (a
  looped clip stretched to 16 beats with a 4-beat loop copies as 16 beats).
* The arrangement timeline-length recipe: looping off → `loop_end` / `end_marker` to the new end →
  looping on; lengthening is capped at the start of the next clip on the track (Live caps
  `end_time` instead of cutting that clip) — `arrangement.resize_clip`.
* `duplicate_notes_by_id(ids, destination_time, transposition_amount)` puts the earliest note at
  `destination_time` and returns the new ids.
* `Clip.groove` assignment works, `None` is rejected (a groove cannot be cleared); Groove amounts
  are percentages (`timing_amount` 100.0 by default).
* `Envelope.events_in_range` / `delete_events_in_range` raise `ValueError("Range out of bounds.")`
  beyond ±1576800 beats; `EnvelopeEvent.control_coefficients` has no visible effect on
  `value_at_time`.
* `WarpMarker.sample_time` is seconds, `beat_to_sample_time` returns frames;
  `Live.Conversions.audio_to_midi_clip` runs in the background (the new track appears later, right
  after the source track).
* (g3) Enum-typed device properties (Wavetable modes, Simpler playback modes, Sample `warp_mode`)
  answer plain ints and accept ints; `Eq8.edit_mode` is a bool (False = A);
  `move_devices_on_track_to_new_drum_rack_pad` returns the new rack's C1 `DrumPad` and rebuilds
  the track after the selected track (its index can change, the old Track object dies).

### 19.5 Clip envelopes in arrangement copies (cross fixer, Live 12.4.5, 2026-09-10)

Session view focused, transport playing, scratch `LB_X_*` MIDI tracks (deleted afterwards), a
4-beat session clip whose envelopes were written with `automation.write`, then
`arrangement.duplicate_clip(time=4000)`; checked 1 s later:

| Track content | Envelopes on the session clip | Arrangement copy |
|---|---|---|
| no devices | Track Volume | **kept** (`has_envelopes` true, "Track Volume" listed; also arr → arr) |
| Operator | Track Volume | **none** (`has_envelopes` false) |
| Operator | Filter Freq | none |
| Operator | Operator Volume, Filter Freq, Track Volume, Track Panning | none (arr → arr copy: none) |

So `duplicate_clip_to_arrangement` drops **all** clip envelopes when the track holds an
instrument, and keeps them on a track without devices. `arrangement.duplicate_clip` /
`move_clip` report `envelopes: {source, copied}` plus a note when they were lost;
`tests/live_stub_ext/arrangement_live.install(Live, copy_envelopes="without_devices")` models the
rule. For arrangement automation use `automation.record`.

Also checked by the cross fixer on the same run: `Device.save_preset_to_compare_ab_slot()` works
on Operator (`devices.set_state(save_to_compare_slot=true)`); `record.status` reports an armed
MIDI track's input meter as `{level}` only (no left/right); a refused connection beyond
`max_clients` reads the `"too many clients (max 4)"` line; `system.status` → `bind {bound: true,
attempts: 1}` after a restart.
