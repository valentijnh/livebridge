"""Recording commands: arming, session (clip slot) recording, arrangement recording,
record settings (metronome, overdub, punch in/out, MIDI record quantization,
automation arm), stopping, status, Capture MIDI — and resampling / bouncing a
section of the song onto an audio track (``record.resample``, the stand-in for
Live's missing export / freeze / "bounce to track" API).

What recording needs outside the LOM (report it, do not fake it):

* Audio recording needs an audio interface / input device configured in Live's
  Preferences → Audio; an audio track's input must be "Ext. In" (or another
  track / "Resampling") with the right channel.
* MIDI recording needs a MIDI input ("All Ins", a controller, or "Computer
  Keyboard") enabled for Track in Preferences → Link, Tempo & MIDI.
* Count-in length is a preference (``song.count_in_duration`` is read-only).
* Live's "Exclusive Arm" preference only applies to clicks in Live's UI: arming
  through the API leaves other tracks armed (verified on 12.4.5) — use
  ``exclusive=true`` for that behaviour.
* With Live's default "Start Playback with Record" preference, switching
  ``record_mode`` on starts the transport at once and records every armed track.

Live 12.4.5 applies ``record_mode``, ``punch_in``/``punch_out``,
``session_automation_record``, ``is_playing`` and a clip-slot recording on its
next tick, so results report the requested state for what a command changed
(see ``transport.DEFERRED_PROPS``).
"""

import time as _time

from .. import compat
from .. import resolve
from .. import serialize
from ..registry import BridgeError, command
from . import transport
from .routing import match_option, monitoring_of, set_monitoring
from .tracks import index_in, parse_toggle, resolve_track, resolve_tracks, track_kind

_CAPTURE = {"auto": 0, "session": 1, "arrangement": 2}

#: Song.Quantization by the names users say ("1 bar", "1/16", "none", "q_bar" ...).
_LAUNCH_Q = dict((name.replace(" ", "").lower(), value)
                 for value, name in serialize.SONG_QUANTIZATION.items())
_LAUNCH_Q.update({"bar": 4, "1bars": 4, "8bar": 1, "4bar": 2, "2bar": 3, "off": 0,
                  "1/1": 4})

#: Song.RecordingQuantization by name.
_RECORD_Q = dict((name.replace(" ", "").lower(), value)
                 for value, name in serialize.RECORD_QUANTIZATION.items())
_RECORD_Q.update({"off": 0, "1/8+t": 4, "1/16+t": 7, "1/8+1/8t": 4, "1/16+1/16t": 7})


def _beats_per_bar(song):
    numerator = compat.safe_getattr(song, "signature_numerator", 4) or 4
    denominator = compat.safe_getattr(song, "signature_denominator", 4) or 4
    return float(numerator) * 4.0 / float(denominator)


def _record_length(song, length_bars, length_beats):
    if length_bars is not None and length_beats is not None:
        raise BridgeError("bad_args", "pass length_bars or length_beats, not both")
    for value, unit in ((length_bars, "length_bars"), (length_beats, "length_beats")):
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise BridgeError("bad_args", "%s must be a positive number" % unit)
        return float(value) * (_beats_per_bar(song) if unit == "length_bars" else 1.0)
    return None


def _parse_launch_quantization(value):
    """``"1 bar"`` / ``"1/16"`` / ``"none"`` / 0..13 -> Song.Quantization int.

    ``None`` / ``"global"`` -> ``None`` (use the song's clip trigger quantization).
    """
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        if 0 <= value <= 13:
            return value
        raise BridgeError("bad_args", "launch_quantization must be 0..13 (Song.Quantization)")
    text = str(value).strip().lower().replace(" ", "")
    if text in ("global", "default", "song"):
        return None
    if text in _LAUNCH_Q:
        return _LAUNCH_Q[text]
    if text.startswith("q_"):
        return _enum_value("Song.Quantization", text)
    raise BridgeError("bad_args", "unknown launch_quantization %r (use one of: %s)"
                      % (value, ", ".join(serialize.SONG_QUANTIZATION.values())))


def _enum_value(path, name):
    enum = compat.live_enum(path)
    names = compat.safe_getattr(enum, "names")
    if isinstance(names, dict) and name in names:
        return int(names[name])
    raise BridgeError("bad_args", "unknown %s member %r" % (path, name))


def _parse_record_quantization(value):
    if isinstance(value, int) and not isinstance(value, bool):
        if 0 <= value <= 8:
            return value
        raise BridgeError("bad_args", "midi_quantization must be 0..8")
    text = str(value).strip().lower().replace(" ", "")
    if text in _RECORD_Q:
        return _RECORD_Q[text]
    if text.startswith("rec_q_"):
        return _enum_value("Song.RecordingQuantization", text)
    raise BridgeError("bad_args", "unknown midi_quantization %r (use one of: %s)"
                      % (value, ", ".join(serialize.RECORD_QUANTIZATION.values())))


def _input_note(ctx, track):
    """A warning when a track cannot record anything with its current input."""
    kind = compat.safe_getattr(track, "input_routing_type")
    name = str(compat.safe_getattr(kind, "display_name", "") or "")
    label = compat.safe_getattr(track, "name", "")
    if not name or name.lower() == "no input":
        return ("%r has input 'No Input' — set one with routing.set (e.g. 'Ext. In' for audio, "
                "'All Ins' for MIDI) or nothing will be recorded" % label)
    if track_kind(ctx, track) == "audio" and name.lower().startswith("ext"):
        return ("%r records from '%s' — this needs an audio interface input configured in "
                "Live's Preferences > Audio" % (label, name))
    return None


def _armed(ctx):
    return [t for t in ctx.song.tracks
            if compat.safe_getattr(t, "can_be_armed", False) and
            compat.safe_getattr(t, "arm", False)]


def _track_line(ctx, track):
    data = ctx.summarize(track, "minimal")
    data.pop("kind", None)
    data["monitoring"] = monitoring_of(track)
    kind = compat.safe_getattr(track, "input_routing_type")
    if kind is not None:
        data["input"] = compat.safe_getattr(kind, "display_name")
    return data


def _set_arm(track, value):
    try:
        track.arm = value
    except (RuntimeError, AttributeError, TypeError, ValueError) as error:
        raise BridgeError("invalid_state", "cannot arm %r: %s"
                          % (compat.safe_getattr(track, "name", ""), error))


def arm_tracks(ctx, targets, arm=True, exclusive=False, monitoring=None):
    """Arm/disarm ``targets``; returns (changed names, notes)."""
    notes = []
    for track in targets:
        if not compat.safe_getattr(track, "can_be_armed", False):
            raise BridgeError("invalid_state", "%r cannot be armed (%s track)"
                              % (compat.safe_getattr(track, "name", ""),
                                 track_kind(ctx, track)))
    wanted = {}
    for track in targets:
        wanted[id(track)] = parse_toggle(arm, compat.safe_getattr(track, "arm", False), "arm")
    if exclusive and any(wanted.values()):
        for other in ctx.song.tracks:
            if index_in(targets, other) is None and \
                    compat.safe_getattr(other, "can_be_armed", False) and \
                    compat.safe_getattr(other, "arm", False):
                _set_arm(other, False)
    changed = []
    for track in targets:
        _set_arm(track, wanted[id(track)])
        changed.append(compat.safe_getattr(track, "name", ""))
        if monitoring is not None:
            set_monitoring(track, monitoring)
    lost = [compat.safe_getattr(t, "name", "") for t in targets
            if wanted[id(t)] and not compat.safe_getattr(t, "arm", False)]
    if lost:
        notes.append("Live disarmed %s again — its 'Exclusive Arm' preference is on "
                     "(Preferences > Record): only one track can stay armed"
                     % ", ".join(repr(n) for n in lost))
    for track in targets:
        if wanted[id(track)]:
            note = _input_note(ctx, track)
            if note:
                notes.append(note)
    return changed, notes


def record_status(ctx, **requested):
    """The dict returned by ``record.status``.

    ``requested`` overrides keys a command just changed: Live 12.4.5 still
    reports the old value of deferred properties inside the same command.
    """
    song = ctx.song
    get = compat.safe_getattr
    loop_start = float(get(song, "loop_start", 0.0) or 0.0)
    loop_length = float(get(song, "loop_length", 0.0) or 0.0)
    recording = []
    for track in song.tracks:
        for index, slot in enumerate(get(track, "clip_slots", ()) or ()):
            if get(slot, "is_recording", False) or \
                    (get(slot, "is_triggered", False) and get(slot, "will_record_on_start",
                                                            False)):
                clip = get(slot, "clip")
                recording.append({"track": get(track, "name"), "slot": index,
                                  "path": ctx.path_of(slot),
                                  "clip": get(clip, "name") if clip is not None else None,
                                  "recording": bool(get(slot, "is_recording", False))})
    count_in = get(song, "count_in_duration")
    data = {
        "is_playing": bool(get(song, "is_playing", False)),
        "record_mode": bool(get(song, "record_mode", False)),
        "session_record": bool(get(song, "session_record", False)),
        "session_record_status": serialize.SESSION_RECORD_STATUS.get(
            get(song, "session_record_status", 0), get(song, "session_record_status")),
        "arrangement_overdub": bool(get(song, "arrangement_overdub", False)),
        "automation_arm": bool(get(song, "session_automation_record", False)),
        "punch_in": bool(get(song, "punch_in", False)),
        "punch_out": bool(get(song, "punch_out", False)),
        "punch_region": {"start": round(loop_start, 4), "end": round(loop_start + loop_length, 4),
                         "loop_on": bool(get(song, "loop", False))},
        "metronome": bool(get(song, "metronome", False)),
        "count_in": serialize.COUNT_IN.get(count_in, count_in),
        "is_counting_in": bool(get(song, "is_counting_in", False)),
        "midi_quantization": serialize.RECORD_QUANTIZATION.get(
            get(song, "midi_recording_quantization", 0),
            get(song, "midi_recording_quantization")),
        "exclusive_arm": get(song, "exclusive_arm"),
        "can_capture_midi": get(song, "can_capture_midi"),
        "current_song_time": round(float(get(song, "current_song_time", 0.0) or 0.0), 4),
        "armed_tracks": [_track_line(ctx, t) for t in _armed(ctx)],
        "recording_slots": recording,
    }
    for key, value in requested.items():
        if key in data and value is not None:
            data[key] = value
    return data


@command("record.status", doc="Everything about recording right now")
def record_status_command(ctx):
    """Recording overview.

    Returns:
        {is_playing, record_mode (arrangement record button), session_record,
        session_record_status "off"|"on"|"transition", arrangement_overdub,
        automation_arm, punch_in, punch_out, punch_region {start, end, loop_on}
        (= the loop brace, in beats), metronome, count_in ("none"/"1 bar"/...,
        a preference), is_counting_in, midi_quantization, exclusive_arm
        (Live's preference — it only governs clicks in Live, not API arming),
        can_capture_midi, current_song_time, armed_tracks [{path, index, name,
        type, monitoring, input, input_level: {level, left?, right?}}],
        recording_slots [{track, slot, path, clip, recording}]}.

    Gotchas:
        ``input_level`` is one momentary meter reading of what arrives at the
        armed track's input (0..1 meter positions, ``left``/``right`` on audio
        tracks) — it tells whether the recording input receives signal (0.0 =
        silence or nothing plugged in; poll a few times, meters fall fast).
    """
    from .mixer import meter_row  # lazy: keeps the import graph of record.py small
    data = record_status(ctx)
    armed = _armed(ctx)
    for line, track in zip(data.get("armed_tracks") or [], armed):
        try:
            line["input_level"] = meter_row(ctx, track, include_input=True).get("input")
        except Exception:  # a meter must never break the status
            line["input_level"] = None
    return data


@command("record.arm", mutating=True, doc="Arm/disarm tracks for recording (+ monitoring)")
def record_arm(ctx, track, arm=True, exclusive=False, monitoring=None):
    """Arm (or disarm) one or several tracks.

    Args:
        track: index, name, "selected", path, or a LIST of them (multi-arm),
            or "all" (every armable track).
        arm: true (default), false or "toggle".
        exclusive: disarm every other track first (Live's exclusive arm).
        monitoring: optional "in" / "auto" / "off" for these tracks — "auto"
            (Live's default) monitors while armed; "in" always; "off" never
            (record without hearing the input).

    Returns:
        {"changed": [names], "armed_tracks": [{path, name, monitoring,
        input}], "notes": [...warnings: no input, audio interface needed,
        exclusive-arm preference...]}.

    Gotchas:
        Return, master and group tracks cannot be armed (``invalid_state``).
        Live's "Exclusive Arm" preference does not apply to API arming — other
        armed tracks stay armed unless ``exclusive`` is true.
    """
    if isinstance(track, str) and track.strip().lower() == "all":
        targets = [t for t in ctx.song.tracks if compat.safe_getattr(t, "can_be_armed", False)]
    else:
        targets = resolve_tracks(ctx, track, allow_returns=False, allow_master=False)
    changed, notes = arm_tracks(ctx, targets, arm, bool(exclusive), monitoring)
    data = {"changed": changed, "armed_tracks": [_track_line(ctx, t) for t in _armed(ctx)]}
    if notes:
        data["notes"] = notes
    return data


def _pick_slot(ctx, track, slot):
    song = ctx.song
    slots = list(compat.safe_getattr(track, "clip_slots", ()) or ())
    if slot is not None:
        return ctx.clip_slot(track, slot)
    scenes = list(song.scenes)
    selected = compat.safe_getattr(ctx.view, "selected_scene")
    start = index_in(scenes, selected) or 0
    for index in list(range(start, len(slots))) + list(range(0, start)):
        if not compat.safe_getattr(slots[index], "has_clip", False):
            return slots[index]
    previous = selected
    try:
        scene = song.create_scene(-1)
    except Exception as error:
        raise BridgeError("invalid_state", "no empty clip slot on %r and a new scene could not "
                          "be created: %s" % (compat.safe_getattr(track, "name", ""), error))
    # Live 12.4.5 copies the tempo/signature of the scene above into the new scene
    # (and selects it): a recording scene must not change the song tempo when fired.
    scene = scene if scene is not None else list(song.scenes)[-1]
    for prop in ("tempo_enabled", "time_signature_enabled"):
        if compat.safe_getattr(scene, prop, False):
            try:
                setattr(scene, prop, False)
            except Exception as error:
                ctx.log("record.session: could not clear %s of the new scene: %s"
                        % (prop, error))
    if previous is not None and compat.safe_getattr(ctx.view, "selected_scene") != previous:
        try:
            ctx.view.selected_scene = previous
        except Exception as error:
            ctx.log("record.session: could not restore the scene selection: %s" % error)
    return list(track.clip_slots)[-1]


@command("record.session", mutating=True, doc="Record into a session clip slot (arm, select, fire)")
def record_session(ctx, track=None, slot=None, length_bars=None, length_beats=None,
                   launch_quantization=None, exclusive=False, monitoring=None,
                   overwrite=False, method="slot"):
    """Start a session recording.

    Args:
        track: MIDI/audio track to record on (default: the selected track).
        slot: slot index or scene name; default = the first empty slot from
            the selected scene down (a new scene is added when all are full).
        length_bars / length_beats: fixed recording length — Live stops
            recording after it and keeps playing the new clip (bars use the
            song's time signature). Omit for open-ended recording.
        launch_quantization: when recording starts — "none", "1 bar",
            "1/4", ... (Song.Quantization) — default = the song's global
            quantization.
        exclusive: disarm the other tracks first.
        monitoring: "in"/"auto"/"off" for the recording track.
        overwrite: delete the clip in the target slot first (otherwise a full
            slot is an ``invalid_state`` error, because firing it would play).
        method: "slot" (default: fire that exact slot) or "trigger" (Live's
            Session Record button = ``song.trigger_session_record`` — records
            into the selected scene on EVERY armed track).

    Returns:
        {track, slot: path, slot_index, method, record_length_beats, count_in,
        fired: true, was_playing, notes?}.

    Gotchas:
        Live starts the recording (and the transport, after the count-in set
        in Live's Preferences) on its next tick, so poll ``record.status`` to
        follow it (``recording_slots``, ``session_record_status``); the new
        clip is named "<track name> <n>".  With a fixed length Live stops
        recording by itself and the clip keeps playing.  Stop with
        ``record.stop``.  Needs a working input — see the notes.
    """
    method = str(method or "slot").strip().lower()
    if method not in ("slot", "trigger"):
        raise BridgeError("bad_args", "method must be 'slot' or 'trigger'")
    song = ctx.song
    obj = resolve_track(ctx, track if track is not None else "selected",
                        allow_returns=False, allow_master=False)
    if not compat.safe_getattr(obj, "can_be_armed", False):
        raise BridgeError("invalid_state", "%r cannot record (group track?)"
                          % compat.safe_getattr(obj, "name", ""))
    record_length = _record_length(song, length_bars, length_beats)
    quantization = _parse_launch_quantization(launch_quantization)
    target = _pick_slot(ctx, obj, slot)
    if compat.safe_getattr(target, "has_clip", False):
        if not overwrite:
            raise BridgeError("invalid_state", "%s already holds a clip — pick an empty slot or "
                              "pass overwrite=true" % ctx.path_of(target))
        target.delete_clip()
    _changed, notes = arm_tracks(ctx, [obj], True, bool(exclusive), monitoring)
    slot_index = index_in(obj.clip_slots, target)
    was_playing = bool(compat.safe_getattr(song, "is_playing", False))
    view = ctx.view
    try:
        view.selected_track = obj
        view.selected_scene = song.scenes[slot_index]
        view.highlighted_clip_slot = target
    except Exception as error:
        notes.append("could not select the slot in the UI: %s" % error)
    if method == "slot":
        kwargs = {}
        if record_length is not None:
            kwargs["record_length"] = record_length
        if quantization is not None:
            kwargs["launch_quantization"] = quantization
        try:
            target.fire(**kwargs)
        except TypeError:
            target.fire()
            if kwargs:
                notes.append("this Live version ignores record length / launch quantization "
                             "on ClipSlot.fire")
        except RuntimeError as error:
            raise BridgeError("invalid_state", "Live refused to record into %s: %s"
                              % (ctx.path_of(target), error))
    else:
        try:
            if record_length is not None:
                song.trigger_session_record(record_length)
            else:
                song.trigger_session_record()
        except Exception as error:
            raise BridgeError("invalid_state", "trigger_session_record failed: %s" % error)
    get = compat.safe_getattr
    count_in = get(song, "count_in_duration")
    data = {
        "track": get(obj, "name"),
        "slot": ctx.path_of(target),
        "slot_index": slot_index,
        "method": method,
        "record_length_beats": record_length,
        "count_in": serialize.COUNT_IN.get(count_in, count_in),
        "fired": True,
        "was_playing": was_playing,
    }
    if notes:
        data["notes"] = notes
    return data


@command("record.arrangement", mutating=True, doc="Start/stop arrangement recording")
def record_arrangement(ctx, start=True, time=None, overdub=None, punch_in=None,
                       punch_out=None):
    """Arrangement recording (the Arrangement Record button + transport).

    Args:
        start: true (default) = press Record and start playback if stopped;
            false = release Record (transport keeps playing).
        time: optional start position in beats or "bars.beats.sixteenths"
            (moves the insert marker;
            while playing, jumps the playhead there).
        overdub: optional MIDI Arrangement Overdub true/false.
        punch_in / punch_out: optional true/false — recording only happens
            inside the loop brace (set it with ``record.settings``).

    Returns:
        ``record.status`` after the change.

    Gotchas:
        Records EVERY armed track (arm first with ``record.arm``; check
        ``armed_tracks``). Count-in follows Live's preference. Recording
        overwrites arrangement material on armed tracks unless overdub (MIDI)
        is on.  Live applies record_mode / punch / playback on its next tick —
        the result reports the requested state.  A ``time`` behind the end of
        the song extends the song first (up to 4096 beats).  Scenes launched while recording write their tempo /
        time signature into the arrangement (Live behaviour; the LOM cannot
        remove those markers again).
    """
    song = ctx.song
    get = compat.safe_getattr
    if not isinstance(start, bool) and start not in (0, 1):
        raise BridgeError("bad_args", "start must be true or false")
    # validate everything first, so a bad argument changes nothing
    plan = []
    for prop, key, value in (("arrangement_overdub", "arrangement_overdub", overdub),
                             ("punch_in", "punch_in", punch_in),
                             ("punch_out", "punch_out", punch_out)):
        if value is not None:
            plan.append((prop, key, bool(parse_toggle(value, get(song, prop, False),
                                                      "overdub" if prop.endswith("overdub")
                                                      else prop))))
    beats = None
    if time is not None:
        beats = resolve.parse_time(song, time, "time")
        transport.check_reachable(song, beats, "time")
    was_playing = bool(get(song, "is_playing", False))
    requested = {}
    for prop, key, value in plan:
        try:
            setattr(song, prop, value)
        except Exception as error:
            raise BridgeError("invalid_state", "Live refused %s = %s: %s" % (prop, value, error))
        requested[key] = value
    if beats is not None:
        saved = transport.extend_song(song, beats, "time")
        try:
            if was_playing:
                song.current_song_time = float(beats)
            else:
                song.start_time = float(beats)
        except (RuntimeError, ValueError) as error:
            raise BridgeError("invalid_state", "cannot move to beat %r: %s" % (beats, error))
        finally:
            transport.restore_loop(song, saved)
    if start:
        song.record_mode = True
        if not was_playing:
            song.start_playing()
        requested.update(record_mode=True, is_playing=True)
    else:
        song.record_mode = False
        requested["record_mode"] = False
    data = record_status(ctx, **requested)
    if start and not data["armed_tracks"]:
        data["notes"] = ["no track is armed — nothing is being recorded (only automation "
                         "when automation arm is on)"]
    return data


@command("record.stop", mutating=True, doc="Stop recording (arrangement + session)")
def record_stop(ctx, stop_transport=False, stop_clips=False, disarm=False):
    """End every recording.

    Args:
        stop_transport: also stop playback.
        stop_clips: also stop the clips on tracks that were recording (by
            default the freshly recorded session clips keep playing, like
            pressing Session Record again in Live).
        disarm: also disarm all tracks.

    Returns:
        ``record.status`` after stopping, plus ``stopped`` (what was done).

    Gotchas:
        Turns off the Arrangement Record button and Session Record.  Live
        applies it on its next tick; the result reports the requested state.
    """
    song = ctx.song
    stopped = []
    recording_tracks = [t for t in song.tracks
                        if any(compat.safe_getattr(s, "is_recording", False)
                               for s in compat.safe_getattr(t, "clip_slots", ()) or ())]
    if compat.safe_getattr(song, "record_mode", False):
        song.record_mode = False
        stopped.append("arrangement_record")
    if compat.safe_getattr(song, "session_record", False):
        song.session_record = False
        stopped.append("session_record")
    if stop_clips:
        for track in recording_tracks:
            try:
                track.stop_all_clips(False)
            except TypeError:
                track.stop_all_clips()
        if recording_tracks:
            stopped.append("clips")
    if stop_transport and compat.safe_getattr(song, "is_playing", False):
        song.stop_playing()
        stopped.append("transport")
    if disarm:
        for track in _armed(ctx):
            _set_arm(track, False)
        stopped.append("arm")
    requested = {"record_mode": False, "session_record": False}
    if "transport" in stopped:
        requested["is_playing"] = False
    data = record_status(ctx, **requested)
    data["stopped"] = stopped
    return data


@command("record.settings", mutating=True,
         doc="Metronome, overdub, punch in/out + region, MIDI record quantization, automation arm")
def record_settings(ctx, metronome=None, overdub=None, punch_in=None, punch_out=None,
                    punch_start=None, punch_end=None, punch_from_loop=False,
                    midi_quantization=None, automation_arm=None):
    """Recording-related switches in one call (all optional).

    Args:
        metronome: true / false / "toggle".
        overdub: MIDI Arrangement Overdub — true / false / "toggle".
        punch_in / punch_out: true / false / "toggle".
        punch_start / punch_end: punch region in beats or "bars.beats.sixteenths"
            ("9.1.1"). Live's punch region IS the loop brace, so this moves
            ``song.loop_start`` / ``loop_length`` (the loop on/off switch is
            not touched); it must end inside the song length.
        punch_from_loop: true = enable punch in AND out using the current
            loop brace as the region (shortcut).
        midi_quantization: record quantization — "none", "1/4", "1/8",
            "1/8T", "1/8+1/8T", "1/16", "1/16T", "1/16+1/16T", "1/32" (or 0..8).
        automation_arm: Automation Arm button true / false / "toggle".

    Returns:
        ``record.status`` after the change, plus ``changed`` (keys).

    Gotchas:
        Count-in, exclusive arm and "start transport with record" are Live
        preferences and cannot be set from the LOM.  Everything is validated
        before anything changes.  Live applies punch / automation arm on its
        next tick — the result reports the requested state.
    """
    song = ctx.song
    get = compat.safe_getattr
    punch_from_loop = parse_toggle(punch_from_loop, False, "punch_from_loop") \
        if not isinstance(punch_from_loop, bool) else punch_from_loop
    # validate everything first, so a bad argument changes nothing
    plan = []
    toggles = (("metronome", "metronome", "metronome", metronome),
               ("overdub", "arrangement_overdub", "arrangement_overdub", overdub),
               ("punch_in", "punch_in", "punch_in", punch_in),
               ("punch_out", "punch_out", "punch_out", punch_out),
               ("automation_arm", "session_automation_record", "automation_arm",
                automation_arm))
    for key, prop, status_key, value in toggles:
        if value is None:
            continue
        if not compat.has(song, prop):
            raise BridgeError("unsupported", "song.%s is not available in this Live version"
                              % prop)
        plan.append((key, prop, status_key, bool(parse_toggle(value, get(song, prop, False),
                                                               key))))
    if punch_from_loop:
        plan = [entry for entry in plan if entry[1] not in ("punch_in", "punch_out")]
        plan.append(("punch_from_loop", "punch_in", "punch_in", True))
        plan.append(("punch_from_loop", "punch_out", "punch_out", True))
    region = None
    if punch_start is not None or punch_end is not None:
        loop_start = float(get(song, "loop_start", 0.0) or 0.0)
        loop_end = loop_start + float(get(song, "loop_length", 4.0) or 4.0)
        start = loop_start if punch_start is None else \
            resolve.parse_time(song, punch_start, "punch_start")
        end = loop_end if punch_end is None else resolve.parse_time(song, punch_end, "punch_end")
        if end <= start:
            raise BridgeError("bad_args", "punch_end must be after punch_start")
        region = (start, end)
    quantization = None
    if midi_quantization is not None:
        quantization = _parse_record_quantization(midi_quantization)
    if not plan and region is None and quantization is None:
        raise BridgeError("bad_args", "nothing to change: pass metronome, overdub, punch_in, "
                          "punch_out, punch_start/punch_end, punch_from_loop, "
                          "midi_quantization or automation_arm")
    changed, requested = [], {}
    if region is not None:
        # Live's punch region IS the loop brace (validated against the song length)
        transport.apply_loop(song, region[0], region[1] - region[0], what="punch region")
        changed.append("punch_region")
    for key, prop, status_key, value in plan:
        try:
            setattr(song, prop, value)
        except Exception as error:
            raise BridgeError("invalid_state", "Live refused %s = %s: %s" % (prop, value, error))
        requested[status_key] = value
        if key not in changed:
            changed.append(key)
    if quantization is not None:
        try:
            song.midi_recording_quantization = quantization
        except Exception as error:
            raise BridgeError("invalid_state", "Live refused the record quantization: %s"
                              % error)
        changed.append("midi_quantization")
    data = record_status(ctx, **requested)
    data["changed"] = changed
    return data


@command("record.capture_midi", mutating=True, doc="Capture MIDI (what you just played)")
def record_capture_midi(ctx, destination="auto"):
    """Live's Capture MIDI: turn recently played MIDI into a clip.

    Args:
        destination: "auto" (Session or Arrangement, whichever is visible),
            "session" or "arrangement".

    Returns:
        {"captured": true, "destination": ...}.

    Gotchas:
        Only works when Live buffered MIDI on an armed or monitored MIDI track
        (``can_capture_midi``) — otherwise ``invalid_state``.
    """
    key = str(destination).strip().lower() if not isinstance(destination, int) else None
    if isinstance(destination, int) and not isinstance(destination, bool) and \
            destination in (0, 1, 2):
        value = destination
    elif key in _CAPTURE:
        value = _CAPTURE[key]
    else:
        raise BridgeError("bad_args", "destination must be 'auto', 'session' or 'arrangement'")
    song = ctx.song
    if not compat.has(song, "capture_midi"):
        raise BridgeError("unsupported", "Capture MIDI needs Live 10 or newer")
    if compat.has(song, "can_capture_midi") and not song.can_capture_midi:
        raise BridgeError("invalid_state", "nothing to capture — play some MIDI into an armed "
                          "or monitoring MIDI track first")
    try:
        song.capture_midi(value)
    except Exception as error:
        raise BridgeError("invalid_state", "Capture MIDI failed: %s" % error)
    names = {0: "auto", 1: "session", 2: "arrangement"}
    return {"captured": True, "destination": names[value]}


# ==========================================================================
# record.resample — bounce a section onto an audio track
# ==========================================================================

#: The running / last ``record.resample`` pass (one at a time; module state).
_RESAMPLER = {"current": None, "last": None, "counter": 0}
#: Display ticks (~100 ms) to wait for playback to reach the start (count-in, preroll).
_RESAMPLE_WAIT_TICKS = 600
#: Display ticks to wait for Live to hand over the recorded clip after the stop.
_COLLECT_TICKS = 10
#: Longest section one pass records (beats).
RESAMPLE_MAX_BEATS = 2048.0
_FINISHED = ("done", "aborted", "failed")
#: ``RoutingTypeCategory`` of Live's "Resampling" input (12.4.5: 2).
_RESAMPLING_CATEGORY = 2


def _routing_name(option):
    return str(compat.safe_getattr(option, "display_name", "") or "")


def _input_for(ctx, dest, source):
    """The input routing option of ``dest`` that carries ``source`` (``None`` = master)."""
    options = list(compat.safe_getattr(dest, "available_input_routing_types", ()) or ())
    if source is None:
        for option in options:
            category = compat.safe_getattr(option, "category")
            try:
                if int(category) == _RESAMPLING_CATEGORY:
                    return option
            except (TypeError, ValueError):
                pass
        for wanted in ("resampling", "main", "master"):
            for option in options:
                if _routing_name(option).lower() == wanted:
                    return option
        raise BridgeError("not_found", "%r offers no 'Resampling' input (choices: %s)"
                          % (compat.safe_getattr(dest, "name", ""),
                             ", ".join(repr(_routing_name(o)) for o in options)))
    for option in options:
        attached = compat.safe_getattr(option, "attached_object")
        if attached is not None and attached == source:
            return option
    wanted = str(compat.safe_getattr(source, "name", "")).lower()
    for option in options:
        if _routing_name(option).lower() == wanted:
            return option
    raise BridgeError("not_found", "%r cannot take %r as its input (choices: %s)"
                      % (compat.safe_getattr(dest, "name", ""),
                         compat.safe_getattr(source, "name", ""),
                         ", ".join(repr(_routing_name(o)) for o in options)))


def _clip_rows(ctx, clips):
    rows = []
    for clip in clips:
        rows.append({"name": compat.safe_getattr(clip, "name"), "path": ctx.path_of(clip),
                     "start": _rnd(compat.safe_getattr(clip, "start_time")),
                     "end": _rnd(compat.safe_getattr(clip, "end_time")),
                     "file_path": compat.safe_getattr(clip, "file_path")})
    return rows


def _rnd(value, digits=4):
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


class _Resample(object):
    """One ``record.resample`` pass, advanced by Live's display tick.

    Phases: arming (Arrangement Record on next tick) -> waiting (for the
    playhead to reach ``start``) -> recording (until ``end``) -> stopping ->
    restoring -> collecting (the new clip, a few ticks at most) -> done |
    aborted | failed.  Punch in/out on the loop brace
    make Live record exactly ``start..end``.
    """

    def __init__(self, ctx, dest, source, created, start, end, lead_in):
        _RESAMPLER["counter"] += 1
        self.id = "resample%d" % _RESAMPLER["counter"]
        self.ctx = ctx
        self.song = ctx.song
        self.dest = dest
        self.source = source
        self.source_name = "master" if source is None else compat.safe_getattr(source, "name")
        self.created = created
        self.start = start
        self.end = end
        self.lead_in = lead_in
        self.phase = "arming"
        self.outcome = None
        self.error = None
        self.notes = []
        self.ticks = 0
        self.started_at = _time.time()
        self.saved = {}
        self.disarmed = []
        self.before = []
        self.clips = []
        self.recorded_from = None
        self.track_name = compat.safe_getattr(dest, "name")
        self.track_path = ctx.path_of(dest)
        self.deleted_track = False
        self.collect_ticks = 0

    # -- setup / teardown -------------------------------------------------
    def setup(self, option, channel):
        song, dest = self.song, self.dest
        get = compat.safe_getattr
        for prop in ("loop", "loop_start", "loop_length", "punch_in", "punch_out",
                     "session_automation_record", "start_time", "current_song_time"):
            self.saved[prop] = get(song, prop)
        self.saved["arm"] = bool(get(dest, "arm", False))
        self.saved["monitoring"] = get(dest, "current_monitoring_state")
        self.saved["input_type"] = get(dest, "input_routing_type")
        self.saved["input_channel"] = get(dest, "input_routing_channel")
        self.before = list(get(dest, "arrangement_clips", ()) or ())
        for candidate in list(get(song, "tracks", ()) or ()):
            if candidate != dest and get(candidate, "can_be_armed", False) and \
                    get(candidate, "arm", False):
                self.disarmed.append(candidate)
        try:
            for candidate in self.disarmed:
                candidate.arm = False
            dest.input_routing_type = option
            if channel is not None:
                dest.input_routing_channel = match_option(
                    list(get(dest, "available_input_routing_channels", ()) or ()), channel,
                    "input routing channel")
            set_monitoring(dest, "off")
            dest.arm = True
            transport.apply_loop(song, self.start, self.end - self.start,
                                 what="resample range")
            for prop, value in (("loop", False), ("punch_in", True), ("punch_out", True),
                                ("session_automation_record", False)):
                if compat.has(song, prop):
                    setattr(song, prop, value)
            song.start_time = float(self.lead_in)
            song.current_song_time = float(self.lead_in)
        except Exception as error:
            self.restore()
            if isinstance(error, BridgeError):
                raise
            raise BridgeError("invalid_state", "Live refused to prepare the resampling: %s"
                              % error)

    def restore(self):
        song, dest = self.song, self.dest
        problems = []

        def attempt(label, func):
            try:
                func()
            except Exception as error:
                problems.append("%s: %s" % (label, error))

        alive = dest != None  # noqa: E711  (deleted LOM objects == None)
        if alive:
            attempt("arm", lambda: setattr(dest, "arm", self.saved.get("arm", False)))
            if self.created:
                none = [o for o in compat.safe_getattr(dest, "available_input_routing_types",
                                                       ()) or ()
                        if _routing_name(o).lower() == "no input"]
                if none:
                    attempt("input", lambda: setattr(dest, "input_routing_type", none[0]))
                attempt("monitoring", lambda: set_monitoring(dest, "auto"))
            else:
                if self.saved.get("input_type") is not None:
                    attempt("input", lambda: setattr(dest, "input_routing_type",
                                                     self.saved["input_type"]))
                if self.saved.get("input_channel") is not None:
                    attempt("input channel", lambda: setattr(dest, "input_routing_channel",
                                                             self.saved["input_channel"]))
                if self.saved.get("monitoring") is not None:
                    attempt("monitoring", lambda: setattr(dest, "current_monitoring_state",
                                                          self.saved["monitoring"]))
        for prop in ("punch_in", "punch_out", "session_automation_record", "loop"):
            value = self.saved.get(prop)
            if value is not None and compat.has(song, prop):
                attempt(prop, lambda prop=prop, value=value: setattr(song, prop, value))
        if self.saved.get("loop_start") is not None:
            attempt("loop brace", lambda: transport.restore_loop(
                song, (self.saved["loop_start"], self.saved["loop_length"])) or
                _raise("Live refused the old loop brace"))
        for candidate in self.disarmed:
            if candidate != None:  # noqa: E711
                attempt("arm %s" % compat.safe_getattr(candidate, "name", ""),
                        lambda candidate=candidate: setattr(candidate, "arm", True))
        for prop in ("start_time", "current_song_time"):
            value = self.saved.get(prop)
            if value is not None:
                attempt(prop, lambda prop=prop, value=value: setattr(song, prop, float(value)))
        if problems:
            self.error = (self.error + "; " if self.error else "") + \
                "could not restore " + ", ".join(problems)

    def collect(self):
        """The arrangement clips the pass left on the destination track."""
        found = []
        for clip in compat.safe_getattr(self.dest, "arrangement_clips", ()) or ():
            if any(clip == old for old in self.before):
                continue
            begin = float(compat.safe_getattr(clip, "start_time", 0.0) or 0.0)
            finish = float(compat.safe_getattr(clip, "end_time", begin) or begin)
            if finish > self.start - 1e-6 and begin < self.end + 1e-6:
                found.append(clip)
        self.clips = _clip_rows(self.ctx, found)
        return found

    # -- tick loop ---------------------------------------------------------
    def schedule(self):
        schedule = compat.safe_getattr(self.ctx.script, "schedule_message")
        if schedule is None:
            raise BridgeError("unsupported", "this control surface cannot schedule ticks")
        schedule(1, self.tick)

    def tick(self):
        if self.phase in _FINISHED:
            return
        self.ticks += 1
        try:
            self._advance()
        except Exception as error:
            self.error = "%s: %s" % (type(error).__name__, error)
            self._finish("failed", wait=False)
        if self.phase not in _FINISHED:
            try:
                self.schedule()
            except Exception as error:
                self.error = "cannot schedule the next tick: %s" % error
                self._finish("failed", wait=False)

    def _now(self):
        return float(compat.safe_getattr(self.song, "current_song_time", 0.0) or 0.0)

    def _advance(self):
        song = self.song
        if self.phase == "arming":
            song.record_mode = True
            self.phase = "waiting"
            return
        if self.phase == "waiting":
            playing = bool(compat.safe_getattr(song, "is_playing", False))
            if playing and self._now() >= self.start - 1e-6:
                self.phase = "recording"
                self.recorded_from = self._now()
                self._advance()
            elif self.ticks > _RESAMPLE_WAIT_TICKS:
                self.error = "playback did not reach beat %s" % _rnd(self.start)
                self._finish("failed")
            return
        if self.phase == "recording":
            if not compat.safe_getattr(song, "is_playing", False) or \
                    not compat.safe_getattr(song, "record_mode", True):
                self.error = "the transport or Arrangement Record was stopped before the end"
                self._finish("aborted")
                return
            if self._now() >= self.end - 1e-6:
                self._finish("done")
            return
        if self.phase == "stopping":
            try:
                song.stop_playing()
            finally:
                self.phase = "restoring"
            return
        if self.phase == "restoring":
            self.restore()
            self.phase = "collecting"
            return
        if self.phase == "collecting":
            found = self.collect()
            self.collect_ticks += 1
            if found or self.outcome != "done" or self.collect_ticks >= _COLLECT_TICKS:
                self._conclude(found)

    def _finish(self, outcome, wait=True):
        """Leave Arrangement Record now; stop and restore on the next ticks."""
        self.outcome = outcome
        try:
            self.song.record_mode = False
        except Exception as error:
            self.error = (self.error + "; " if self.error else "") + "record_mode: %s" % error
        if wait:
            self.phase = "stopping"
            return
        try:
            self.song.stop_playing()
        except Exception:
            pass
        self.restore()
        self._conclude(self.collect())

    def _conclude(self, found):
        outcome = self.outcome or "done"
        if outcome == "done" and not found:
            outcome = "failed"
            self.error = (self.error + "; " if self.error else "") + \
                "Live recorded no clip on %r — is Live's audio engine running (an output " \
                "device in Preferences > Audio)?" % self.track_name
        if self.created and not found and self.dest != None:  # noqa: E711
            index = resolve.index_in(compat.safe_getattr(self.song, "tracks", ()) or (),
                                     self.dest)
            if index is not None:
                try:
                    self.song.delete_track(index)
                    self.deleted_track = True
                except Exception as error:
                    self.error = (self.error + "; " if self.error else "") + \
                        "could not delete the empty track: %s" % error
        self.phase = outcome
        if _RESAMPLER.get("current") is self:
            _RESAMPLER["current"] = None

    def abort(self, reason):
        if self.phase in _FINISHED or self.phase in ("stopping", "restoring", "collecting"):
            return
        self.error = reason
        self._finish("aborted")

    def status(self):
        finished = self.phase in _FINISHED
        now = self._now()
        span = max(self.end - self.start, 1e-9)
        if self.phase == "recording":
            progress = max(0.0, min(1.0, (now - self.start) / span))
        elif self.outcome == "done" or self.phase == "done":
            progress = 1.0
        else:
            progress = 0.0
        song = self.song
        data = {"id": self.id, "phase": self.phase, "finished": finished,
                "source": self.source_name,
                "track": {"name": self.track_name, "path": self.track_path,
                          "created": self.created},
                "range": {"start": _rnd(self.start), "end": _rnd(self.end),
                          "start_bbs": transport.beats_to_bbs(song, self.start),
                          "end_bbs": transport.beats_to_bbs(song, self.end)},
                "progress": _rnd(progress, 3), "ticks": self.ticks,
                "elapsed_s": _rnd(_time.time() - self.started_at, 2)}
        if finished:
            data["clips"] = self.clips
            data["restored"] = {"loop": self.saved.get("loop"),
                                "position": _rnd(self.saved.get("current_song_time") or 0.0),
                                "rearmed": [compat.safe_getattr(t, "name") for t in
                                            self.disarmed]}
            if self.deleted_track:
                data["track"]["deleted"] = True
        if self.error:
            data["error"] = self.error
        if self.notes:
            data["notes"] = self.notes
        return data


def _raise(message):
    raise RuntimeError(message)


def _resample_range(song, start, end, bars, length):
    """``(start, end)`` in beats from the loose arguments."""
    if start is None:
        raise BridgeError("bad_args", "start is required (beats or \"bars.beats.sixteenths\")")
    begin = resolve.parse_time(song, start, "start")
    given = [name for name, value in (("end", end), ("bars", bars), ("length", length))
             if value is not None]
    if len(given) != 1:
        raise BridgeError("bad_args", "pass exactly one of end, bars or length")
    if end is not None:
        finish = resolve.parse_time(song, end, "end")
    elif bars is not None:
        if isinstance(bars, bool) or not isinstance(bars, (int, float)) or bars <= 0:
            raise BridgeError("bad_args", "bars must be a number > 0")
        finish = begin + float(bars) * _beats_per_bar(song)
    else:
        finish = begin + resolve.parse_time(song, length, "length", is_length=True)
    if finish <= begin + 1e-6:
        raise BridgeError("bad_args", "end must be after start")
    if finish - begin > RESAMPLE_MAX_BEATS:
        raise BridgeError("bad_args", "at most %d beats per resample pass"
                          % int(RESAMPLE_MAX_BEATS))
    return begin, finish


@command("record.resample", mutating=True,
         doc="Resample / bounce a section (the master mix or one track) onto an audio track "
             "in real time")
def record_resample(ctx, start=None, end=None, bars=None, length=None, source="master",
                    track=None, name=None, channel=None, preroll=1.0, action="start"):
    """Record ``start..end`` of the song as audio — Live's export / freeze /
    bounce is not in the API, so this does what a person does: an audio track
    whose input is "Resampling" (the master) or the source track, monitoring
    off, armed alone; the loop brace set to the section with Punch-In/Out on
    (so Live records exactly the section); Arrangement Record from
    ``preroll`` beats before ``start``; stopped at ``end``; then transport,
    loop brace, punch, automation arm, arming and routing are put back.  The
    command returns at once; poll ``record.resample_status`` (it plays in
    real time).

    Args:
        start: section start — beats or "bars.beats.sixteenths" ("17.1.1").
        end / bars / length: section end, or its length in bars (song
            signature) or as a duration (beats, "8.0.0").  Exactly one.
        source: "master" (default: the whole mix via the "Resampling" input)
            or a track (index, name, path) to bounce on its own.
        track: an existing audio track to record onto (its arrangement
            material in the section is replaced); default: a new audio track
            at the end of the set, named ``name`` or "Resample <start>-<end>".
        name: name for the new track.
        channel: input channel of the destination, e.g. "Post FX",
            "Pre FX", "Post Mixer" for a track source (default: Live's).
        preroll: beats of playback before ``start`` (0..16; lets reverbs and
            delays from before the section in).
        action: "start" (default) or "stop" (abort the running pass).

    Returns:
        start: the status (below) with ``phase: "arming"`` plus
        ``expected_seconds``.  stop: the status of the stopped pass.
        Status: {"id", "phase", "finished", "source", "track": {name, path,
        created, deleted?}, "range": {start, end, start_bbs, end_bbs},
        "progress" 0..1, "clips"? [{name, path, start, end, file_path}],
        "restored"?, "error"?}.

    Gotchas:
        Needs a stopped transport and Live's audio engine (an output device);
        one pass at a time.  Other armed tracks are disarmed during the pass
        and re-armed after.  The recording is real time (a 16-bar section at
        120 BPM takes ~32 s) and plays through the speakers; muted/soloed
        tracks sound as they do now.  A new track that ends up without a clip
        is deleted again.  The recorded file lives in the set's
        Samples/Recorded folder (an unsaved set: Live's temporary folder).
    """
    action = str(action).strip().lower()
    current = _RESAMPLER.get("current")
    if action == "stop":
        if current is None:
            last = _RESAMPLER.get("last")
            if last is None:
                raise BridgeError("invalid_state", "no resampling is running")
            return last.status()
        current.abort("stopped with action=stop")
        return current.status()
    if action != "start":
        raise BridgeError("bad_args", "action must be start or stop")
    if current is not None and current.phase not in _FINISHED:
        raise BridgeError("invalid_state", "resampling %s is still running — wait for it "
                          "(record.resample_status) or stop it (action=stop)" % current.id)
    song = ctx.song
    begin, finish = _resample_range(song, start, end, bars, length)
    if isinstance(preroll, bool) or not isinstance(preroll, (int, float)) or \
            not 0.0 <= preroll <= 16.0:
        raise BridgeError("bad_args", "preroll must be 0..16 beats")
    if name is not None and not isinstance(name, str):
        raise BridgeError("bad_args", "name must be a string")
    if compat.safe_getattr(song, "is_playing", False):
        raise BridgeError("invalid_state", "stop the transport first — the resampling starts "
                          "playback itself")
    if compat.safe_getattr(song, "record_mode", False):
        raise BridgeError("invalid_state", "Arrangement Record is already on")
    transport.check_reachable(song, finish, "end")
    src = None
    if not (isinstance(source, str) and source.strip().lower() in ("master", "main", "mix")):
        src = resolve_track(ctx, source)
        if track_kind(ctx, src) == "master":
            src = None
    dest = None
    if track is not None:
        dest = resolve_track(ctx, track, allow_returns=False, allow_master=False)
        if track_kind(ctx, dest) != "audio":
            raise BridgeError("bad_args", "%r is not an audio track — resampling records "
                              "audio" % compat.safe_getattr(dest, "name", ""))
        if src is not None and dest == src:
            raise BridgeError("bad_args", "the destination cannot be the source track")
        option = _input_for(ctx, dest, src)
        if channel is not None:
            match_option(list(compat.safe_getattr(dest, "available_input_routing_channels",
                                                  ()) or ()), channel, "input routing channel")
    created = dest is None
    if created:
        try:
            dest = song.create_audio_track(-1)
        except Exception as error:
            raise BridgeError("invalid_state", "could not create the audio track: %s" % error)
        if dest is None:  # pragma: no cover - every 12.x returns the track
            dest = list(song.tracks)[-1]
        try:
            dest.name = name if name is not None else "Resample %s-%s" % (
                transport.beats_to_bbs(song, begin), transport.beats_to_bbs(song, finish))
            option = _input_for(ctx, dest, src)
            if channel is not None:
                match_option(list(compat.safe_getattr(dest, "available_input_routing_channels",
                                                      ()) or ()), channel,
                             "input routing channel")
        except Exception:
            _delete_track(song, dest)
            raise
    lead_in = max(0.0, begin - float(preroll))
    recording = _Resample(ctx, dest, src, created, begin, finish, lead_in)
    try:
        recording.setup(option, channel)
        recording.schedule()
    except Exception:
        recording.restore()
        if created:
            _delete_track(song, dest)
        raise
    _RESAMPLER["current"] = recording
    _RESAMPLER["last"] = recording
    tempo = float(compat.safe_getattr(song, "tempo", 120.0) or 120.0)
    result = recording.status()
    result["expected_seconds"] = _rnd((finish - lead_in) * 60.0 / tempo, 1)
    result["note"] = "records in real time — poll record.resample_status until finished"
    return result


def _delete_track(song, track):
    index = resolve.index_in(compat.safe_getattr(song, "tracks", ()) or (), track)
    if index is not None:
        try:
            song.delete_track(index)
        except Exception:
            pass


@command("record.resample_status", doc="Progress / result of the running or last "
                                        "record.resample pass")
def record_resample_status(ctx):
    """Status of ``record.resample``.

    Returns:
        {"id", "phase": arming|waiting|recording|stopping|restoring|collecting|done|
         aborted|failed,
         "finished", "source", "track": {name, path, created, deleted?}, "range",
         "progress", "clips"? (the recorded arrangement clips, once finished),
         "restored"?, "error"?} — or {"phase": "idle", "finished": true} when
        nothing was resampled since the script loaded.
    """
    recording = _RESAMPLER.get("current") or _RESAMPLER.get("last")
    if recording is None:
        return {"phase": "idle", "finished": True}
    return recording.status()
