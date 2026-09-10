"""LOM objects -> compact JSON summaries (``docs/ARCHITECTURE.md`` §6).

``summarize(obj, detail)`` is the only function handlers should need::

    ctx.summarize(track)                 # "summary"
    ctx.summarize(track, "minimal")      # index/name/path only
    ctx.summarize(device, "full")        # + every parameter

Detail levels:

``minimal``  identity only — for long lists (all devices of all tracks).
``summary``  the default; a whole set must fit in a few KB.
``full``     everything cheap to read, including nested collections.

Rules: never serialise raw LOM objects, never include listeners, catch
per-attribute exceptions and omit what fails.  Every summary carries the
canonical ``path`` so Claude can navigate straight on with ``lom.*``.
"""

from . import compat
from . import lom

DETAILS = ("minimal", "summary", "full")

_MISSING = object()

# Enum tables — values verified against Live 12.4 (docs/LIVE_API_VERIFIED.md).

#: ``device.type`` values (``Live.Device.DeviceType``) — midi_effect is 4.
DEVICE_TYPES = {0: "undefined", 1: "instrument", 2: "audio_effect", 4: "midi_effect"}

#: ``track.current_monitoring_state`` (``Live.Track.Track.monitoring_states``).
MONITORING_STATES = {0: "in", 1: "auto", 2: "off"}

#: ``parameter.automation_state`` (``Live.DeviceParameter.AutomationState``).
AUTOMATION_STATES = {0: "none", 1: "playing", 2: "overridden"}

#: ``parameter.state`` (``Live.DeviceParameter.ParameterState``).
PARAMETER_STATES = {0: "enabled", 1: "irrelevant", 2: "disabled"}

#: ``clip.warp_mode`` / ``sample.warp_mode`` (``Live.Clip.WarpMode``).
WARP_MODES = {0: "beats", 1: "tones", 2: "texture", 3: "repitch", 4: "complex",
              5: "rex", 6: "complex_pro"}

#: ``clip.launch_quantization`` (``Live.Clip.ClipLaunchQuantization``).
CLIP_LAUNCH_QUANTIZATION = {0: "global", 1: "none", 2: "8 bars", 3: "4 bars",
                            4: "2 bars", 5: "1 bar", 6: "1/2", 7: "1/2T", 8: "1/4",
                            9: "1/4T", 10: "1/8", 11: "1/8T", 12: "1/16", 13: "1/16T",
                            14: "1/32"}

#: ``clip.launch_mode`` (``Live.Clip.LaunchMode``).
LAUNCH_MODES = {0: "trigger", 1: "gate", 2: "toggle", 3: "repeat"}

#: ``song.clip_trigger_quantization`` (``Live.Song.Quantization``).
SONG_QUANTIZATION = {0: "none", 1: "8 bars", 2: "4 bars", 3: "2 bars", 4: "1 bar",
                     5: "1/2", 6: "1/2T", 7: "1/4", 8: "1/4T", 9: "1/8", 10: "1/8T",
                     11: "1/16", 12: "1/16T", 13: "1/32"}

#: ``song.midi_recording_quantization`` and the ``clip.quantize`` grid
#: (``Live.Song.RecordingQuantization``).
RECORD_QUANTIZATION = {0: "none", 1: "1/4", 2: "1/8", 3: "1/8T", 4: "1/8+1/8T",
                       5: "1/16", 6: "1/16T", 7: "1/16+1/16T", 8: "1/32"}

#: ``mixer_device.crossfade_assign``.
CROSSFADE_ASSIGN = {0: "A", 1: "none", 2: "B"}

#: ``mixer_device.panning_mode``.
PANNING_MODES = {0: "stereo", 1: "split_stereo"}

#: ``song.session_record_status`` (``Live.Song.SessionRecordStatus``).
SESSION_RECORD_STATUS = {0: "off", 1: "on", 2: "transition"}

#: ``song.count_in_duration``.
COUNT_IN = {0: "none", 1: "1 bar", 2: "2 bars", 3: "4 bars"}

#: ``simpler.playback_mode`` (``Live.SimplerDevice.PlaybackMode``).
SIMPLER_PLAYBACK_MODES = {0: "classic", 1: "one_shot", 2: "slicing"}


# --------------------------------------------------------------------------
# scalars
# --------------------------------------------------------------------------

def is_scalar(value):
    """True when ``value`` can go into JSON untouched."""
    return value is None or isinstance(value, (bool, int, float, str))


def scalar(value, _depth=0):
    """Make any value JSON-safe and compact.

    Numbers/strings/bools pass through, enums become their name, LOM objects
    become ``"<Track 'Bass'>"``-ish markers (use :func:`summarize` when you
    want the real thing), sequences become lists (bounded), everything else
    becomes its ``repr``.  Never raises.
    """
    try:
        if value is None or isinstance(value, (bool, str)):
            return value
        if isinstance(value, int):
            name = getattr(value, "name", None)
            if isinstance(name, str) and not isinstance(value, bool):
                return name
            return int(value)
        if isinstance(value, float):
            if value != value or value in (float("inf"), float("-inf")):
                return None
            return round(value, 6)
        if isinstance(value, bytes):
            return value.decode("utf-8", "replace")
        if compat.is_sequence(value):
            if _depth > 2:
                return "[%d items]" % len(value)
            return [scalar(item, _depth + 1) for item in list(value)[:64]]
        if isinstance(value, dict):
            if _depth > 2:
                return "{%d keys}" % len(value)
            return dict((str(k), scalar(v, _depth + 1)) for k, v in value.items())
        name = compat.safe_getattr(value, "name")
        display = compat.safe_getattr(value, "display_name")
        label = name if isinstance(name, str) else display
        if isinstance(label, str):
            return "<%s %s>" % (type(value).__name__, label)
        return "<%s>" % type(value).__name__
    except Exception:
        return "<unserialisable>"


def _num(value, digits=6):
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _prune(data):
    """Drop ``None`` values so summaries stay small."""
    return dict((k, v) for k, v in data.items() if v is not None)


def _enum_name(value, table, default=None):
    try:
        return table.get(int(value), default if default is not None else int(value))
    except (TypeError, ValueError):
        name = compat.safe_getattr(value, "name")
        return name if isinstance(name, str) else default


def _detail(detail):
    detail = str(detail or "summary").lower()
    return detail if detail in DETAILS else "summary"


# --------------------------------------------------------------------------
# type detection (duck typing — class names differ per Live version/edition)
# --------------------------------------------------------------------------

def kind_of(obj):
    """The LOM family of ``obj``: ``"track"``, ``"clip"``, ``"device"`` ...

    Returns ``None`` for anything that is not a recognised LOM object.
    """
    if obj is None or is_scalar(obj):
        return None
    has = compat.has
    if has(obj, "tracks") and has(obj, "scenes") and has(obj, "tempo"):
        return "song"
    if has(obj, "clip_slots") and has(obj, "mixer_device"):
        return "track"
    if has(obj, "has_clip") and has(obj, "fire"):
        return "clip_slot"
    if has(obj, "is_midi_clip"):
        return "clip"
    # NOT value_items: Live raises reading it on non-quantized parameters.
    if has(obj, "is_quantized") and has(obj, "original_name") and has(obj, "min"):
        return "parameter"
    if has(obj, "note") and has(obj, "chains") and not has(obj, "parameters"):
        return "drum_pad"
    if has(obj, "class_name") and has(obj, "parameters"):
        return "device"
    if has(obj, "devices") and has(obj, "mixer_device"):
        return "chain"
    if has(obj, "sends") and has(obj, "volume"):
        return "mixer_device"
    if has(obj, "clip_slots") and has(obj, "is_triggered"):
        return "scene"
    if has(obj, "time") and has(obj, "jump"):
        return "cue_point"
    if has(obj, "arrangement_clips") and has(obj, "create_midi_clip"):
        return "take_lane"
    if has(obj, "uri") and has(obj, "is_loadable"):
        return "browser_item"
    if has(obj, "instruments") and has(obj, "load_item"):
        return "browser"
    if has(obj, "get_major_version"):
        return "application"
    return None


def track_type(track, ctx=None):
    """``"midi"``, ``"audio"``, ``"return"``, ``"master"`` or ``"group"``."""
    song = getattr(ctx, "song", None) if ctx is not None else None
    if song is None:
        song = compat.safe_getattr(track, "canonical_parent")
    if song is not None:
        master = compat.safe_getattr(song, "master_track")
        if master is not None and (master is track or master == track):
            return "master"
        for candidate in compat.safe_getattr(song, "return_tracks", ()) or ():
            if candidate is track or candidate == track:
                return "return"
    if compat.safe_getattr(track, "is_foldable", False):
        return "group"
    if compat.safe_getattr(track, "has_midi_input", False):
        return "midi"
    return "audio"


# --------------------------------------------------------------------------
# the entry point
# --------------------------------------------------------------------------

def summarize(obj, detail="summary", ctx=None):
    """Compact JSON summary of any LOM object.

    Args:
        obj: a LOM object (or a list/tuple of them, or a plain value).
        detail: ``"minimal"``, ``"summary"`` (default) or ``"full"``.
        ctx: the command Context — used for canonical paths and to tell
            return/master tracks apart.  Optional but recommended.

    Returns:
        A dict for LOM objects, a list for sequences, the value itself for
        scalars.  Never raises.
    """
    detail = _detail(detail)
    if is_scalar(obj):
        return scalar(obj)
    if compat.is_sequence(obj):
        return [summarize(item, detail, ctx) for item in obj]
    if isinstance(obj, dict):
        return dict((str(k), summarize(v, detail, ctx)) for k, v in obj.items())
    kind = kind_of(obj)
    handler = _SUMMARIZERS.get(kind)
    if handler is None:
        return scalar(obj)
    try:
        data = handler(obj, detail, ctx)
    except Exception:
        return {"kind": kind or "object", "type": type(obj).__name__,
                "error": "could not summarise"}
    data.setdefault("kind", kind)
    return _prune(data)


def path_for(obj, ctx):
    """Canonical path of ``obj`` or ``None`` (never raises)."""
    if ctx is None:
        return None
    try:
        return lom.path_of(obj, ctx)
    except Exception:
        return None


def _index_in(collection, obj):
    try:
        for index, item in enumerate(collection or ()):
            if item is obj or item == obj:
                return index
    except Exception:
        pass
    return None


def _path_index(path):
    """The trailing ``[n]`` of a path, as an int."""
    if not path or not path.endswith("]"):
        return None
    start = path.rfind("[")
    if start < 0:
        return None
    try:
        return int(path[start + 1:-1])
    except ValueError:
        return None


# --------------------------------------------------------------------------
# per type
# --------------------------------------------------------------------------

def summarize_song(song, detail, ctx):
    """Song summary — the one-call overview of the set."""
    get = compat.safe_getattr
    view = get(song, "view")
    data = {
        "path": "song",
        "tempo": _num(get(song, "tempo"), 3),
        "signature": "%s/%s" % (get(song, "signature_numerator", 4),
                                get(song, "signature_denominator", 4)),
        "is_playing": bool(get(song, "is_playing", False)),
        "current_song_time": _num(get(song, "current_song_time"), 4),
        "track_count": len(get(song, "tracks", ()) or ()),
        "return_count": len(get(song, "return_tracks", ()) or ()),
        "scene_count": len(get(song, "scenes", ()) or ()),
        "live_version": compat.live_version_string(),
    }
    if detail == "minimal":
        return data
    data.update({
        "loop": bool(get(song, "loop", False)),
        "loop_start": _num(get(song, "loop_start"), 4),
        "loop_length": _num(get(song, "loop_length"), 4),
        "metronome": bool(get(song, "metronome", False)),
        "record_mode": bool(get(song, "record_mode", False)),
        "session_record": bool(get(song, "session_record", False)),
        "overdub": bool(get(song, "overdub", False)),
        "arrangement_overdub": bool(get(song, "arrangement_overdub", False)),
        "punch_in": bool(get(song, "punch_in", False)),
        "punch_out": bool(get(song, "punch_out", False)),
        "session_record_status": _enum_name(get(song, "session_record_status"),
                                            SESSION_RECORD_STATUS),
        "clip_trigger_quantization": _enum_name(get(song, "clip_trigger_quantization"),
                                                SONG_QUANTIZATION),
        "midi_recording_quantization": _enum_name(
            get(song, "midi_recording_quantization"), RECORD_QUANTIZATION),
        "count_in": _enum_name(get(song, "count_in_duration"), COUNT_IN),
        "can_undo": bool(get(song, "can_undo", False)),
        "can_redo": bool(get(song, "can_redo", False)),
        "scale_name": get(song, "scale_name"),
        "root_note": get(song, "root_note"),
        "scale_mode": get(song, "scale_mode"),
        "cue_point_count": len(get(song, "cue_points", ()) or ()),
    })
    if view is not None:
        selected_track = get(view, "selected_track")
        selected_scene = get(view, "selected_scene")
        data["selected_track"] = summarize(selected_track, "minimal", ctx) \
            if selected_track is not None else None
        data["selected_scene"] = summarize(selected_scene, "minimal", ctx) \
            if selected_scene is not None else None
    if detail == "full":
        data["tracks"] = [summarize(t, "summary", ctx) for t in get(song, "tracks", ()) or ()]
        data["return_tracks"] = [summarize(t, "summary", ctx)
                                 for t in get(song, "return_tracks", ()) or ()]
        master = get(song, "master_track")
        data["master_track"] = summarize(master, "summary", ctx) if master else None
        data["scenes"] = [summarize(s, "summary", ctx) for s in get(song, "scenes", ()) or ()]
    return data


def summarize_track(track, detail, ctx):
    """Track summary — see ``docs/ARCHITECTURE.md`` §6."""
    get = compat.safe_getattr
    path = path_for(track, ctx)
    data = {
        "path": path,
        "index": _path_index(path),
        "name": get(track, "name"),
        "type": track_type(track, ctx),
    }
    if detail == "minimal":
        return data
    mixer = get(track, "mixer_device")
    data.update({
        "color_index": get(track, "color_index"),
        "mute": bool(get(track, "mute", False)),
        "solo": bool(get(track, "solo", False)),
        "arm": bool(get(track, "arm", False)) if compat.has(track, "arm") else None,
        "can_be_armed": bool(get(track, "can_be_armed", False)),
        "is_grouped": bool(get(track, "is_grouped", False)),
        "is_foldable": bool(get(track, "is_foldable", False)),
        "fold_state": get(track, "fold_state") if get(track, "is_foldable", False) else None,
        "group_track": path_for(get(track, "group_track"), ctx),
        "is_frozen": bool(get(track, "is_frozen", False)),
        "has_midi_input": bool(get(track, "has_midi_input", False)),
        "has_audio_input": bool(get(track, "has_audio_input", False)),
        "monitoring": _enum_name(get(track, "current_monitoring_state"), MONITORING_STATES),
        "playing_slot_index": get(track, "playing_slot_index"),
        "fired_slot_index": get(track, "fired_slot_index"),
        "device_count": len(get(track, "devices", ()) or ()),
        "devices": [summarize(d, "minimal", ctx) for d in get(track, "devices", ()) or ()],
    })
    if mixer is not None:
        volume = get(mixer, "volume")
        panning = get(mixer, "panning")
        data["volume"] = _num(get(volume, "value")) if volume is not None else None
        data["panning"] = _num(get(panning, "value")) if panning is not None else None
        sends = get(mixer, "sends", ()) or ()
        if sends:
            data["sends"] = [_num(get(s, "value")) for s in sends]
    input_type = get(track, "input_routing_type")
    output_type = get(track, "output_routing_type")
    data["input_routing"] = get(input_type, "display_name") if input_type is not None else None
    data["output_routing"] = get(output_type, "display_name") if output_type is not None else None
    slots = get(track, "clip_slots", ()) or ()
    data["clip_slot_count"] = len(slots)
    if detail == "full":
        clips = []
        for index, slot in enumerate(slots):
            clip = get(slot, "clip")
            if clip is None:
                continue
            clips.append(_prune({
                "index": index,
                "name": get(clip, "name"),
                "length": _num(get(clip, "length"), 4),
                "is_playing": bool(get(clip, "is_playing", False)),
                "is_midi": bool(get(clip, "is_midi_clip", False)),
            }))
        data["clips"] = clips
        data["arrangement_clip_count"] = len(get(track, "arrangement_clips", ()) or ())
        data["devices"] = [summarize(d, "summary", ctx)
                           for d in get(track, "devices", ()) or ()]
    return data


def summarize_clip_slot(slot, detail, ctx):
    """Clip slot summary (the clip itself is nested at ``summary``/``full``)."""
    get = compat.safe_getattr
    path = path_for(slot, ctx)
    clip = get(slot, "clip")
    data = {
        "path": path,
        "index": _path_index(path),
        "has_clip": bool(get(slot, "has_clip", clip is not None)),
    }
    if detail == "minimal":
        if clip is not None:
            data["clip_name"] = get(clip, "name")
        return data
    data.update({
        "is_playing": bool(get(slot, "is_playing", False)),
        "is_recording": bool(get(slot, "is_recording", False)),
        "is_triggered": bool(get(slot, "is_triggered", False)),
        "has_stop_button": bool(get(slot, "has_stop_button", False)),
        "is_group_slot": bool(get(slot, "is_group_slot", False)),
        "clip": summarize(clip, "summary" if detail == "full" else "minimal", ctx)
                if clip is not None else None,
    })
    return data


def summarize_clip(clip, detail, ctx):
    """Clip summary.  Notes are never included — use ``notes.get``."""
    get = compat.safe_getattr
    path = path_for(clip, ctx)
    is_midi = bool(get(clip, "is_midi_clip", False))
    data = {
        "path": path,
        "name": get(clip, "name"),
        "is_midi": is_midi,
        "is_audio": bool(get(clip, "is_audio_clip", not is_midi)),
        "length": _num(get(clip, "length"), 4),
    }
    if detail == "minimal":
        return data
    data.update({
        "color_index": get(clip, "color_index"),
        "looping": bool(get(clip, "looping", False)),
        "loop_start": _num(get(clip, "loop_start"), 4),
        "loop_end": _num(get(clip, "loop_end"), 4),
        "start_marker": _num(get(clip, "start_marker"), 4),
        "end_marker": _num(get(clip, "end_marker"), 4),
        "muted": bool(get(clip, "muted", False)),
        "is_playing": bool(get(clip, "is_playing", False)),
        "is_recording": bool(get(clip, "is_recording", False)),
        "is_triggered": bool(get(clip, "is_triggered", False)),
        "signature": "%s/%s" % (get(clip, "signature_numerator", 4),
                                get(clip, "signature_denominator", 4)),
        "is_arrangement_clip": bool(get(clip, "is_arrangement_clip", False)),
    })
    if get(clip, "is_arrangement_clip", False):
        data["start_time"] = _num(get(clip, "start_time"), 4)
        data["end_time"] = _num(get(clip, "end_time"), 4)
    if is_midi:
        if compat.has(clip, "get_all_notes_extended"):
            notes = compat.safe_call(clip, "get_all_notes_extended")
        else:
            notes = compat.safe_call(clip, "get_notes_extended", 0, 128, 0.0,
                                     get(clip, "length", 0.0) or 0.0)
        if notes[0] and notes[1] is not None:
            data["note_count"] = len(notes[1])
    else:
        data.update({
            "warping": bool(get(clip, "warping", False)),
            "warp_mode": _enum_name(get(clip, "warp_mode"), WARP_MODES),
            "gain": _num(get(clip, "gain")),
            "gain_display": get(clip, "gain_display_string"),
            "pitch_coarse": get(clip, "pitch_coarse"),
            "pitch_fine": _num(get(clip, "pitch_fine"), 3),
            "file_path": get(clip, "file_path"),
            "sample_length": get(clip, "sample_length"),
        })
    if detail == "full":
        data["has_envelopes"] = bool(get(clip, "has_envelopes", False))
        data["launch_mode"] = _enum_name(get(clip, "launch_mode"), LAUNCH_MODES)
        data["launch_quantization"] = _enum_name(get(clip, "launch_quantization"),
                                                 CLIP_LAUNCH_QUANTIZATION)
        data["legato"] = get(clip, "legato")
        data["velocity_amount"] = _num(get(clip, "velocity_amount"))
    return data


def summarize_device(device, detail, ctx):
    """Device summary (racks, plugins and Simpler add their own fields)."""
    get = compat.safe_getattr
    path = path_for(device, ctx)
    parameters = get(device, "parameters", ()) or ()
    data = {
        "path": path,
        "index": _path_index(path),
        "name": get(device, "name"),
        "class_name": get(device, "class_name"),
        "type": _enum_name(get(device, "type"), DEVICE_TYPES),
        "is_active": bool(get(device, "is_active", True)),
    }
    if detail == "minimal":
        return data
    data.update({
        "class_display_name": get(device, "class_display_name"),
        "parameter_count": len(parameters),
        "can_have_chains": bool(get(device, "can_have_chains", False)),
        "can_have_drum_pads": bool(get(device, "can_have_drum_pads", False)),
        "is_plugin": compat.is_plugin_device(device),
        "is_max_device": compat.is_max_device(device) or None,
    })
    if data["is_plugin"]:
        # Live exposes only the Configure list ("Device On" + chosen parameters) while
        # get_parameter_names() lists everything — Serum 2 right after loading: 0 of 2623.
        data["exposed_parameter_count"] = max(0, len(parameters) - 1)
        getter = get(device, "get_parameter_names")
        if callable(getter):
            try:
                data["plugin_parameter_count"] = len(getter())
            except Exception:
                pass
    chains = get(device, "chains", ()) or ()
    if chains:
        data["chain_count"] = len(chains)
    pads = get(device, "drum_pads", ()) or ()
    if pads:
        data["drum_pad_count"] = len([p for p in pads if get(p, "chains", ())])
    if compat.has(device, "presets"):
        presets = get(device, "presets", ()) or ()
        data["preset_count"] = len(presets)
        data["selected_preset_index"] = get(device, "selected_preset_index")
    sample = get(device, "sample")
    if sample is not None:
        data["sample"] = _prune({
            "file_path": get(sample, "file_path"),
            "length": get(sample, "length"),
            "slice_count": len(get(sample, "slices", ()) or ()),
        })
    if compat.has(device, "playback_mode"):
        data["playback_mode"] = _enum_name(get(device, "playback_mode"),
                                           SIMPLER_PLAYBACK_MODES)
    if detail == "full":
        data["parameters"] = [summarize_parameter(p, "summary", ctx) for p in parameters]
        if chains:
            data["chains"] = [summarize(c, "minimal", ctx) for c in chains]
        if pads:
            data["drum_pads"] = [summarize(p, "minimal", ctx)
                                 for p in pads if get(p, "chains", ())]
    return data


def summarize_parameter(parameter, detail, ctx):
    """DeviceParameter summary — always includes min/max so Claude can clamp."""
    get = compat.safe_getattr
    path = path_for(parameter, ctx)
    value = get(parameter, "value")
    data = {
        "path": path,
        "index": _path_index(path),
        "name": get(parameter, "name"),
        "value": _num(value),
    }
    if detail == "minimal":
        return data
    display = compat.safe_call(parameter, "str_for_value", value)
    data.update({
        "original_name": get(parameter, "original_name"),
        "min": _num(get(parameter, "min")),
        "max": _num(get(parameter, "max")),
        "default_value": _num(get(parameter, "default_value")),
        "is_quantized": bool(get(parameter, "is_quantized", False)),
        "display_value": str(display[1]) if display[0] and display[1] is not None else None,
        "is_enabled": bool(get(parameter, "is_enabled", True)),
        "automation_state": _enum_name(get(parameter, "automation_state"),
                                       AUTOMATION_STATES),
    })
    # value_items raises on non-quantized parameters in Live — only ask when
    # it can succeed.
    items = get(parameter, "value_items", ()) or () \
        if get(parameter, "is_quantized", False) else ()
    if items:
        data["value_items"] = [str(item) for item in items]
    return data


def summarize_chain(chain, detail, ctx):
    """A chain inside a rack."""
    get = compat.safe_getattr
    path = path_for(chain, ctx)
    data = {
        "path": path,
        "index": _path_index(path),
        "name": get(chain, "name"),
    }
    if detail == "minimal":
        return data
    data.update({
        "mute": bool(get(chain, "mute", False)),
        "solo": bool(get(chain, "solo", False)),
        "color_index": get(chain, "color_index"),
        "device_count": len(get(chain, "devices", ()) or ()),
        "devices": [summarize(d, "minimal", ctx) for d in get(chain, "devices", ()) or ()],
    })
    if detail == "full":
        data["devices"] = [summarize(d, "summary", ctx)
                           for d in get(chain, "devices", ()) or ()]
    return data


def summarize_drum_pad(pad, detail, ctx):
    """A drum rack pad (``note`` is the MIDI note it answers to)."""
    get = compat.safe_getattr
    path = path_for(pad, ctx)
    chains = get(pad, "chains", ()) or ()
    data = {
        "path": path,
        "note": get(pad, "note"),
        "name": get(pad, "name"),
        "has_chains": bool(chains),
    }
    if detail == "minimal":
        return data
    data.update({
        "mute": bool(get(pad, "mute", False)),
        "solo": bool(get(pad, "solo", False)),
        "chain_count": len(chains),
    })
    if detail == "full":
        data["chains"] = [summarize(c, "summary", ctx) for c in chains]
    return data


def summarize_mixer(mixer, detail, ctx):
    """A track's mixer device (volume/pan/sends as values, not objects).

    ``crossfade_assign`` / ``panning_mode`` are plain ints in Live (named
    here); ``cue_volume``/``crossfader`` exist on the master track only and the
    split-stereo pair only matters when ``panning_mode`` is split stereo.
    """
    get = compat.safe_getattr
    data = {"path": path_for(mixer, ctx)}
    panning_mode = get(mixer, "panning_mode")
    names = ["volume", "panning", "track_activator", "cue_volume", "crossfader"]
    if panning_mode == 1:
        names += ["left_split_stereo", "right_split_stereo"]
    for name in names:
        parameter = get(mixer, name)
        if parameter is None:
            continue
        if detail == "full":
            data[name] = summarize_parameter(parameter, "summary", ctx)
        else:
            data[name] = _num(get(parameter, "value"))
    data["crossfade_assign"] = _enum_name(get(mixer, "crossfade_assign"), CROSSFADE_ASSIGN)
    data["panning_mode"] = _enum_name(panning_mode, PANNING_MODES)
    sends = get(mixer, "sends", ()) or ()
    if detail == "full":
        data["sends"] = [summarize_parameter(s, "summary", ctx) for s in sends]
    else:
        data["sends"] = [_num(get(s, "value")) for s in sends]
    return data


def summarize_scene(scene, detail, ctx):
    """Scene summary."""
    get = compat.safe_getattr
    path = path_for(scene, ctx)
    data = {
        "path": path,
        "index": _path_index(path),
        "name": get(scene, "name"),
    }
    if detail == "minimal":
        return data
    data.update({
        "color_index": get(scene, "color_index"),
        "is_triggered": bool(get(scene, "is_triggered", False)),
        "is_empty": bool(get(scene, "is_empty", False)),
    })
    if get(scene, "tempo_enabled", False):
        data["tempo"] = _num(get(scene, "tempo"), 3)
    if get(scene, "time_signature_enabled", False):
        data["signature"] = "%s/%s" % (get(scene, "time_signature_numerator", 4),
                                       get(scene, "time_signature_denominator", 4))
    if detail == "full":
        data["clip_slots"] = [summarize(s, "minimal", ctx)
                              for s in get(scene, "clip_slots", ()) or ()]
    return data


def summarize_cue_point(cue, detail, ctx):
    """Arrangement cue point (locator)."""
    get = compat.safe_getattr
    path = path_for(cue, ctx)
    return {
        "path": path,
        "index": _path_index(path),
        "name": get(cue, "name"),
        "time": _num(get(cue, "time"), 4),
    }


def summarize_take_lane(lane, detail, ctx):
    """A take lane of an arrangement track (Live 12): name and its clips."""
    get = compat.safe_getattr
    path = path_for(lane, ctx)
    clips = list(get(lane, "arrangement_clips", ()) or ())
    data = {"path": path, "index": _path_index(path), "name": get(lane, "name"),
            "clip_count": len(clips)}
    if detail == "full":
        data["clips"] = [summarize(c, "minimal", ctx) for c in clips]
    return data


def summarize_browser_item(item, detail, ctx):
    """A browser entry.  ``children`` are only counted at ``full`` because
    reading them makes Live load the folder."""
    get = compat.safe_getattr
    data = {
        "name": get(item, "name"),
        "uri": get(item, "uri"),
        "is_folder": bool(get(item, "is_folder", False)),
        "is_device": bool(get(item, "is_device", False)),
        "is_loadable": bool(get(item, "is_loadable", False)),
    }
    if detail == "minimal":
        return data
    data["source"] = get(item, "source") or None
    if detail == "full":
        children = get(item, "children", ()) or ()
        data["child_count"] = len(children)
        data["children"] = [summarize_browser_item(child, "minimal", ctx)
                            for child in children[:200]]
    return data


#: Browser root attributes in Live 12.4 (``colors`` and ``user_folders`` are
#: lists of items; there is no ``splice`` root).
BROWSER_ROOTS = ("instruments", "audio_effects", "midi_effects", "sounds", "drums",
                 "plugins", "samples", "packs", "user_library", "user_folders",
                 "current_project", "max_for_live", "clips", "colors")


def summarize_browser(browser, detail, ctx):
    """The browser itself: its roots and how many children each has."""
    get = compat.safe_getattr
    roots = []
    for name in BROWSER_ROOTS:
        root = get(browser, name)
        if root is None:
            continue
        entry = {"name": name, "path": "browser.%s" % name}
        if detail != "minimal":
            if compat.is_sequence(root):
                # colors / user_folders are lists of items, not a folder item
                entry["child_count"] = len(root)
            else:
                children = get(root, "children", ()) or ()
                entry["child_count"] = len(children)
        roots.append(entry)
    hotswap = get(browser, "hotswap_target")
    return {"path": "browser", "roots": roots,
            "hotswap_target": path_for(hotswap, ctx) if hotswap is not None else None}


def summarize_application(app, detail, ctx):
    """The Live application: version, edition and the focused view."""
    get = compat.safe_getattr
    view = get(app, "view")
    return {
        "path": "app",
        "version": compat.live_version_string(),
        "edition": compat.edition(),
        "variant": compat.variant(),
        "focused_document_view": get(view, "focused_document_view") if view else None,
        "open_dialog_count": get(app, "open_dialog_count"),
    }


_SUMMARIZERS = {
    "song": summarize_song,
    "track": summarize_track,
    "clip_slot": summarize_clip_slot,
    "clip": summarize_clip,
    "device": summarize_device,
    "parameter": summarize_parameter,
    "chain": summarize_chain,
    "drum_pad": summarize_drum_pad,
    "mixer_device": summarize_mixer,
    "scene": summarize_scene,
    "cue_point": summarize_cue_point,
    "take_lane": summarize_take_lane,
    "browser_item": summarize_browser_item,
    "browser": summarize_browser,
    "application": summarize_application,
}
