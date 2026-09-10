"""The LOM path resolver — generic access to every object in Live.

Path grammar (``docs/ARCHITECTURE.md`` §5)::

    path      := root ("." segment)*
    root      := "song" | "app" | "browser"
    segment   := name | name "[" index "]"
    index     := integer (0-based; negative allowed)

Examples::

    song
    song.tracks[2]
    song.tracks[2].devices[0].parameters[3]
    song.tracks[0].clip_slots[3].clip
    song.tracks[0].arrangement_clips[1]
    song.return_tracks[0]        song.master_track      song.scenes[1]
    song.view.selected_track     app.view               browser.instruments
    song.tracks[1].devices[0].chains[0].devices[0]
    song.tracks[0].devices[0].drum_pads[36].chains[0]

Public API: :func:`resolve`, :func:`get`, :func:`set_property`, :func:`call`,
:func:`describe`, :func:`children`, :func:`path_of`.  Everything raises
:class:`~.registry.BridgeError` with a protocol error type and a message that
tells Claude what went wrong (``"song.tracks[7]: index out of range
(5 tracks)"``).
"""

import difflib
import inspect
import re

from . import compat
from . import serialize
from .registry import BridgeError

#: The three roots a path may start with (``application`` is an alias of ``app``).
ROOTS = ("song", "app", "browser")
_ROOT_ALIASES = {"application": "app", "live_set": "song", "set": "song"}

_SEGMENT_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)((?:\[-?\d+\])*)$")
_INDEX_RE = re.compile(r"\[(-?\d+)\]")

_MISSING = object()

#: Collections we look through when reconstructing a path (``path_of``).
_COLLECTION_ATTRS = (
    "tracks", "return_tracks", "scenes", "cue_points", "clip_slots", "devices",
    "parameters", "chains", "return_chains", "drum_pads", "sends",
    "arrangement_clips", "take_lanes", "automation_envelopes", "children",
    "user_folders", "colors",
)

#: Single-object attributes we look through when reconstructing a path.
_SINGULAR_ATTRS = (
    "master_track", "clip", "mixer_device", "view", "sample", "browser",
    "volume", "panning", "track_activator", "chain_activator", "cue_volume",
    "crossfader", "song_tempo", "left_split_stereo", "right_split_stereo",
    "groove_pool",
    "audio_effects", "midi_effects", "instruments", "sounds", "drums",
    "plugins", "samples", "packs", "user_library", "current_project",
    "max_for_live", "clips",
)


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------

def parse_path(path):
    """Split a path into ``[(name, [indices]), ...]``.

    Raises:
        BridgeError: ``bad_args`` when the path is empty or malformed,
            ``not_found`` when the root is unknown.
    """
    if not isinstance(path, str):
        raise BridgeError("bad_args", "path must be a string, got %s" % type(path).__name__)
    text = path.strip()
    if not text:
        raise BridgeError("bad_args", "path must not be empty (try 'song')")
    segments = []
    for raw in text.split("."):
        raw = raw.strip()
        if not raw:
            raise BridgeError("bad_args", "%s: empty path segment" % path)
        match = _SEGMENT_RE.match(raw)
        if match is None:
            raise BridgeError(
                "bad_args",
                "%s: bad path segment %r — expected name or name[index]" % (path, raw))
        name = match.group(1)
        indices = [int(i) for i in _INDEX_RE.findall(match.group(2))]
        segments.append((name, indices))
    root, root_indices = segments[0]
    root = _ROOT_ALIASES.get(root, root)
    if root not in ROOTS:
        raise BridgeError(
            "not_found",
            "%s: unknown root %r — paths start with %s"
            % (path, segments[0][0], ", ".join(ROOTS)))
    if root_indices:
        raise BridgeError("bad_args", "%s: the root %r cannot be indexed" % (path, root))
    segments[0] = (root, root_indices)
    return segments


def _root_object(name, ctx):
    if name == "song":
        obj = ctx.song
    elif name == "app":
        obj = ctx.app
    else:
        obj = ctx.browser
    if obj is None:
        raise BridgeError("unsupported", "%r is not available in this Live session" % name)
    return obj


def _is_sequence(value):
    """True for LOM collections (lists, tuples and Live's ``Base.Vector``) —
    see :func:`compat.is_sequence`."""
    return compat.is_sequence(value)


def _sequence_len(value):
    try:
        return len(value)
    except Exception:
        return None


def _index_into(obj, index, walked, name):
    length = _sequence_len(obj)
    if length is None or isinstance(obj, (str, bytes)) or not hasattr(obj, "__getitem__"):
        raise BridgeError(
            "not_found",
            "%s[%d]: %r is not indexable (%s)" % (walked, index, name, type(obj).__name__))
    real = index + length if index < 0 else index
    if real < 0 or real >= length:
        raise BridgeError(
            "not_found",
            "%s[%d]: index out of range (%d %s)" % (walked, index, length, name))
    try:
        return obj[real]
    except Exception:
        raise BridgeError("not_found", "%s[%d]: could not read the element" % (walked, index))


# --------------------------------------------------------------------------
# resolve / get / set / call
# --------------------------------------------------------------------------

def resolve(path, ctx):
    """Return the LOM object a path points at.

    A path may legitimately end on ``None`` (``song.tracks[0].clip_slots[3].clip``
    of an empty slot) — that returns ``None``.  A ``None`` *in the middle* of a
    path raises ``not_found``.
    """
    segments = parse_path(path)
    name, _indices = segments[0]
    obj = _root_object(name, ctx)
    walked = name
    for name, indices in segments[1:]:
        if obj is None:
            raise BridgeError("not_found", "%s: is empty (nothing to look '%s' up on)"
                              % (walked, name))
        value = compat.safe_getattr(obj, name, _MISSING)
        if value is _MISSING:
            raise BridgeError(
                "not_found",
                "%s.%s: no attribute %r on %s" % (walked, name, name, type(obj).__name__))
        walked = "%s.%s" % (walked, name)
        obj = value
        for index in indices:
            obj = _index_into(obj, index, walked, name)
            walked = "%s[%d]" % (walked, index)
    return obj


def resolve_existing(path, ctx):
    """Like :func:`resolve` but ``None`` is an error (used by describe/children)."""
    obj = resolve(path, ctx)
    if obj is None:
        raise BridgeError("not_found", "%s: resolved to nothing (empty clip slot?)" % path)
    return obj


def _read_attribute(obj, prop, path):
    """``obj.prop`` with protocol errors.

    * the attribute does not exist -> ``not_found`` (with suggestions);
    * it exists but Live refuses to read it here ("only available for audio
      clips", ``arm`` on a return track, ``value_items`` of a continuous
      parameter ...) -> ``invalid_state`` carrying Live's message.
    """
    try:
        return getattr(obj, prop)
    except AttributeError:
        if not isinstance(getattr(type(obj), prop, None), property):
            raise BridgeError(
                "not_found",
                "%s.%s: no attribute %r on %s%s"
                % (path, prop, prop, type(obj).__name__, _suggest(obj, prop)))
        raise BridgeError("invalid_state", "%s.%s is not available on this %s"
                          % (path, prop, type(obj).__name__))
    except Exception as error:
        raise BridgeError("invalid_state", "%s.%s is not available on this %s: %s"
                          % (path, prop, type(obj).__name__, error))


def get(path, prop=None, ctx=None):
    """Read ``path`` or, when ``prop`` is given, ``path.prop``.

    Returns the raw value; callers summarise LOM objects with
    :func:`~.serialize.summarize`.
    """
    obj = resolve(path, ctx)
    if prop is None:
        return obj
    if obj is None:
        raise BridgeError("not_found", "%s: resolved to nothing, cannot read %r" % (path, prop))
    if not isinstance(prop, str) or not prop:
        raise BridgeError("bad_args", "prop must be a non-empty string")
    return _read_attribute(obj, prop, path)


def _suggest(obj, prop):
    """" — did you mean 'x', 'y'?" for a typo'd property name."""
    try:
        names = [n for n in dir(obj) if not n.startswith("_") and not _is_listener_name(n)]
    except Exception:
        return ""
    lowered = str(prop).lower()
    close = [n for n in names if lowered in n.lower() or n.lower() in lowered]
    for name in difflib.get_close_matches(str(prop), names, n=4, cutoff=0.6):
        if name not in close:
            close.append(name)
    if not close:
        return ""
    return " — did you mean %s?" % ", ".join(repr(n) for n in close[:4])


#: Properties that hold a ``Live.*`` enum value (an int) — these also accept
#: the member name as a string in :func:`lom.set`.  Paths are relative to the
#: ``Live`` module and verified against Live 12.4 (docs/LIVE_API_VERIFIED.md);
#: note ``clip.launch_quantization`` uses ``Clip.ClipLaunchQuantization``
#: (0 = global), not ``Song.Quantization``.
_ENUM_PROPERTIES = {
    "warp_mode": "Clip.WarpMode",
    "grid_quantization": "Clip.GridQuantization",
    "launch_quantization": "Clip.ClipLaunchQuantization",
    "launch_mode": "Clip.LaunchMode",
    "clip_trigger_quantization": "Song.Quantization",
    "midi_recording_quantization": "Song.RecordingQuantization",
    "current_monitoring_state": "Track.Track.monitoring_states",
    "device_insert_mode": "Track.DeviceInsertMode",
    "crossfade_assign": "MixerDevice.MixerDevice.crossfade_assignments",
    "panning_mode": "MixerDevice.MixerDevice.panning_modes",
    "playback_mode": "SimplerDevice.PlaybackMode",
    "slicing_playback_mode": "SimplerDevice.SlicingPlaybackMode",
    "slicing_style": "Sample.SlicingStyle",
    "slicing_beat_division": "Sample.SlicingBeatDivision",
    "beats_transient_loop_mode": "Sample.TransientLoopMode",
    "filter_type": "Browser.FilterType",
    "automation_state": "DeviceParameter.AutomationState",
    "type": "Device.DeviceType",
}


def _enum_class_for(prop):
    """The ``Live.<module>.<Enum>`` class behind a property name, or ``None``."""
    path = _ENUM_PROPERTIES.get(prop)
    if path is None:
        return None
    return compat.live_enum(path)


def _enum_value_by_name(enum_class, name):
    """``"repitch"`` -> ``3`` for the given enum class (case-insensitive).

    Live's enums are Boost.Python enums: members are class attributes and the
    class has a ``names`` dict (there is no ``__members__``).
    """
    if enum_class is None or not isinstance(name, str):
        return None
    wanted = name.strip().lower().replace(" ", "_").replace("-", "_")
    names = compat.safe_getattr(enum_class, "names")
    if isinstance(names, dict):
        for member_name, member in names.items():
            if str(member_name).lower() == wanted:
                try:
                    return int(member)
                except (TypeError, ValueError):
                    break
    try:
        attributes = dir(enum_class)
    except Exception:
        return None
    for attribute in attributes:
        if attribute.startswith("_"):
            continue
        if attribute.lower() != wanted:
            continue
        try:
            value = getattr(enum_class, attribute)
        except Exception:
            continue
        if callable(value):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _is_parameter(obj):
    return (compat.has(obj, "is_quantized") and compat.has(obj, "value")
            and compat.has(obj, "min") and compat.has(obj, "max"))


def coerce_value(current, value, obj=None, prop=None):
    """Coerce a JSON value to the type the LOM property expects.

    Rules:
      * ``bool`` targets accept ``true/false``, ``1/0``, ``"on"/"off"``;
      * ``int`` targets accept ints, integral floats and numeric strings;
      * enum-typed ints also accept the enum member name (``"beats"``);
      * ``float`` targets accept ints/floats/numeric strings;
      * ``str`` targets accept anything with a sane ``str()``;
      * collections are rejected (``bad_args``).

    ``obj``/``prop`` are only used for error messages.
    """
    where = ("%s.%s" % (type(obj).__name__, prop)) if prop else "value"
    if _is_sequence(current):
        raise BridgeError("bad_args", "%s: is a collection and cannot be assigned" % where)
    if isinstance(current, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            text = value.strip().lower()
            if text in ("1", "true", "yes", "on"):
                return True
            if text in ("0", "false", "no", "off"):
                return False
        raise BridgeError("bad_args", "%s: expected a boolean, got %r" % (where, value))
    if isinstance(current, int) and not isinstance(current, bool):
        enum_value = _enum_from_name(current, value)
        if enum_value is None and prop:
            enum_value = _enum_value_by_name(_enum_class_for(prop), value)
        if enum_value is not None:
            return enum_value
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            if abs(value - round(value)) < 1e-9:
                return int(round(value))
            raise BridgeError("bad_args", "%s: expected a whole number, got %r" % (where, value))
        if isinstance(value, str):
            try:
                return int(value.strip(), 10)
            except ValueError:
                try:
                    return int(round(float(value.strip())))
                except ValueError:
                    pass
        enum_class = _enum_class_for(prop) if prop else None
        if enum_class is not None:
            options = ", ".join(sorted(
                n for n in dir(enum_class)
                if not n.startswith("_") and not callable(getattr(enum_class, n, None))))
            raise BridgeError("bad_args", "%s: expected an integer or one of %s, got %r"
                              % (where, options, value))
        raise BridgeError("bad_args", "%s: expected an integer, got %r" % (where, value))
    if isinstance(current, float):
        if isinstance(value, bool):
            return float(value)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                pass
        raise BridgeError("bad_args", "%s: expected a number, got %r" % (where, value))
    if isinstance(current, str):
        if isinstance(value, (str, int, float, bool)):
            return str(value)
        raise BridgeError("bad_args", "%s: expected a string, got %r" % (where, value))
    # Unknown/None current value: pass through, Live will complain if it must.
    return value


def _enum_from_name(current, value):
    """A Boost.Python enum value accepts a member name as a string.

    Only applies when the property returned an enum member (most LOM
    properties return plain ints — :func:`_enum_class_for` covers those).
    """
    if not isinstance(value, str):
        return None
    enum_type = type(current)
    lookup = getattr(enum_type, "names", None)
    if not isinstance(lookup, dict):
        lookup = getattr(enum_type, "__members__", None)
    if not lookup:
        return None
    member = lookup.get(value) or lookup.get(value.lower())
    if member is None:
        return None
    try:
        return int(member)
    except Exception:
        return None


def coerce_parameter_value(parameter, value):
    """Coerce + clamp a value for a ``DeviceParameter``.

    Quantized parameters also accept one of their ``value_items`` as a string
    (``"On"``, ``"Sine"``).  Continuous values are clamped into
    ``[min, max]`` instead of raising, because Live rejects out-of-range
    assignments outright.
    """
    items = compat.safe_getattr(parameter, "value_items", ()) or ()
    if isinstance(value, str):
        text = value.strip()
        matched = None
        for index, item in enumerate(items):
            if str(item).lower() == text.lower():
                matched = float(index)
                break
        if matched is None:
            try:
                number = float(text)
            except ValueError:
                raise BridgeError(
                    "bad_args",
                    "%s: %r is not a valid value%s"
                    % (compat.safe_getattr(parameter, "name", "parameter"), value,
                       (" (expected one of: %s)" % ", ".join(str(i) for i in items))
                       if items else ""))
        else:
            number = matched
    elif isinstance(value, bool):
        number = float(int(value))
    elif isinstance(value, (int, float)):
        number = float(value)
    else:
        raise BridgeError("bad_args", "parameter values must be numbers or strings, got %r"
                          % (value,))
    minimum = float(compat.safe_getattr(parameter, "min", 0.0))
    maximum = float(compat.safe_getattr(parameter, "max", 1.0))
    if minimum > maximum:
        minimum, maximum = maximum, minimum
    clamped = max(minimum, min(maximum, number))
    if compat.safe_getattr(parameter, "is_quantized", False):
        clamped = float(int(round(clamped)))
        clamped = max(minimum, min(maximum, clamped))
    return clamped


def set_property(path, prop, value, ctx=None):
    """Set ``path.prop`` to ``value`` with type coercion.

    Returns ``{"path", "prop", "value"}`` with the value *after* the write, so
    the caller sees what Live actually accepted (clamped/quantized).

    Raises:
        BridgeError: ``not_found`` (unknown property), ``bad_args`` (wrong
            type), ``invalid_state`` (read-only, or Live refused the value).
    """
    obj = resolve(path, ctx)
    if obj is None:
        raise BridgeError("not_found", "%s: resolved to nothing, cannot set %r" % (path, prop))
    if not isinstance(prop, str) or not prop:
        raise BridgeError("bad_args", "prop must be a non-empty string")
    current = _read_attribute(obj, prop, path)
    if callable(current):
        raise BridgeError("bad_args", "%s.%s is a method — use lom.call" % (path, prop))
    if prop == "value" and _is_parameter(obj):
        new_value = coerce_parameter_value(obj, value)
    elif _holds_object(current) and looks_like_path(value):
        # Object-valued property (song.view.selected_track, detail_clip,
        # browser.hotswap_target, track.input_routing_type, clip.groove ...):
        # the JSON value is the LOM path of the object to assign.
        new_value = resolve(value, ctx)
    else:
        new_value = coerce_value(current, value, obj, prop)
    try:
        setattr(obj, prop, new_value)
    except AttributeError:
        raise BridgeError("invalid_state", "%s.%s is read-only" % (path, prop))
    except (ValueError, TypeError, RuntimeError) as error:
        raise BridgeError("invalid_state", "%s.%s: Live refused %r (%s)"
                          % (path, prop, new_value, error))
    after = compat.safe_getattr(obj, prop, new_value)
    if serialize.kind_of(after) is not None and ctx is not None:
        shown = serialize.summarize(after, "minimal", ctx)
    else:
        shown = serialize.scalar(after)
    result = {"path": path, "prop": prop, "value": shown}
    if _is_parameter(obj) and prop == "value":
        display = compat.safe_call(obj, "str_for_value", after)
        if display[0]:
            result["display_value"] = str(display[1])
    return result


def looks_like_path(value):
    """``True`` when ``value`` is a string that starts like a LOM path (``"song.tracks[2]"``)."""
    if not isinstance(value, str):
        return False
    text = value.strip()
    head = re.split(r"[.\[]", text, maxsplit=1)[0]
    if head not in ROOTS and head not in _ROOT_ALIASES:
        return False
    return text == head or text[len(head):len(head) + 1] in (".", "[")


def _holds_object(current):
    """The property currently holds a LOM object or nothing (not a number/string/list)."""
    if current is None:
        return True
    return not serialize.is_scalar(current) and not _is_sequence(current)


def call(path, method, args=None, kwargs=None, ctx=None):
    """Call ``path.method(*args, **kwargs)`` and return its result.

    ``args`` is a JSON list, ``kwargs`` a JSON object (both optional).  LOM
    objects in the arguments can be passed as path strings — they are *not*
    resolved automatically, use :func:`resolve` in the handler when a method
    needs an object (e.g. ``song.view.select_device``).

    Raises:
        BridgeError: ``not_found`` (no such method), ``bad_args`` (wrong
            arity/type), ``invalid_state`` (Live refused the call).
    """
    obj = resolve(path, ctx)
    if obj is None:
        raise BridgeError("not_found", "%s: resolved to nothing, cannot call %r" % (path, method))
    if not isinstance(method, str) or not method:
        raise BridgeError("bad_args", "method must be a non-empty string")
    target = compat.safe_getattr(obj, method, _MISSING)
    if target is _MISSING:
        raise BridgeError(
            "not_found",
            "%s.%s: no method %r on %s%s"
            % (path, method, method, type(obj).__name__, _suggest(obj, method)))
    if not callable(target):
        raise BridgeError("bad_args", "%s.%s is a property, not a method — use lom.get/lom.set"
                          % (path, method))
    call_args = list(args or [])
    call_kwargs = dict(kwargs or {})
    try:
        return target(*call_args, **call_kwargs)
    except TypeError as error:
        raise BridgeError("bad_args", "%s.%s: %s" % (path, method, error))
    except (ValueError, RuntimeError, IndexError, KeyError) as error:
        raise BridgeError("invalid_state", "%s.%s: %s" % (path, method, error))


# --------------------------------------------------------------------------
# describe / children
# --------------------------------------------------------------------------

def _is_listener_name(name):
    return (name.startswith("add_") and name.endswith("_listener")) or \
           (name.startswith("remove_") and name.endswith("_listener")) or \
           name.endswith("_has_listener") or name == "notify_listeners"


def _signature_params(func):
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return None
    names = []
    for index, (name, parameter) in enumerate(signature.parameters.items()):
        if index == 0 and name == "self":
            continue
        if parameter.kind == parameter.VAR_KEYWORD:
            names.append("**" + name)
        elif parameter.kind == parameter.VAR_POSITIONAL:
            names.append("*" + name)
        elif parameter.default is not parameter.empty:
            names.append("%s=%s" % (name, serialize.scalar(parameter.default)))
        else:
            names.append(name)
    return names


def describe(path, ctx, include_methods=True):
    """Describe any LOM object: properties, methods and child collections.

    Returns::

        {"type": "Track", "path": "song.tracks[0]",
         "properties": [{"name", "value", "type", "writable"?}, ...],
         "methods": ["fire()", "stop()", ...],
         "children": [{"name": "devices", "count": 3, "path": "..."}, ...]}

    Private names and the ``add_*_listener`` / ``remove_*_listener`` /
    ``*_has_listener`` family are skipped, and every attribute read is wrapped
    in its own ``try/except`` so one hostile property cannot break the call.
    """
    obj = resolve_existing(path, ctx)
    canonical = path_of(obj, ctx) or path
    properties = []
    methods = []
    child_collections = []
    try:
        names = sorted(set(dir(obj)))
    except Exception:
        names = []
    for name in names:
        if name.startswith("_") or _is_listener_name(name):
            continue
        value = compat.safe_getattr(obj, name, _MISSING)
        if value is _MISSING:
            continue
        if isinstance(value, type):
            # nested classes: Song.View, Track.monitoring_states (enums) ...
            continue
        if callable(value):
            if include_methods:
                params = _signature_params(value)
                methods.append("%s(%s)" % (name, ", ".join(params)) if params is not None
                               else "%s(...)" % name)
            continue
        if _is_sequence(value):
            length = _sequence_len(value) or 0
            if length and all(serialize.is_scalar(item) for item in value) and length <= 32:
                properties.append({"name": name, "value": [serialize.scalar(v) for v in value],
                                   "type": "list"})
            else:
                child_collections.append({
                    "name": name, "count": length,
                    "path": "%s.%s" % (canonical, name)})
            continue
        entry = {"name": name, "value": serialize.scalar(value),
                 "type": type(value).__name__}
        writable = _writable(obj, name)
        if writable is not None:
            entry["writable"] = writable
        properties.append(entry)
    return {
        "type": type(obj).__name__,
        "path": canonical,
        "properties": properties,
        "methods": methods,
        "children": child_collections,
    }


def _writable(obj, name):
    """True/False when we can tell whether a property is settable, else None."""
    descriptor = getattr(type(obj), name, None)
    if isinstance(descriptor, property):
        return descriptor.fset is not None
    if descriptor is None and name in getattr(obj, "__dict__", {}):
        return True
    return None


def children(path, ctx):
    """Children of a path.

    Returns ``("items", [obj, ...])`` when the path resolves to a collection
    (``song.tracks``), and ``("collections", [{"name", "count", "path"}, ...])``
    when it resolves to a plain object (``song`` → tracks, scenes, ...).
    The handler decides how to serialise.
    """
    obj = resolve_existing(path, ctx)
    if _is_sequence(obj):
        return "items", list(obj)
    canonical = path_of(obj, ctx) or path
    found = []
    try:
        names = sorted(set(dir(obj)))
    except Exception:
        names = []
    for name in names:
        if name.startswith("_") or _is_listener_name(name):
            continue
        value = compat.safe_getattr(obj, name, _MISSING)
        if value is _MISSING or callable(value) or not _is_sequence(value):
            continue
        length = _sequence_len(value) or 0
        if length and all(serialize.is_scalar(item) for item in value):
            continue
        found.append({"name": name, "count": length,
                      "path": "%s.%s" % (canonical, name)})
    return "collections", found


# --------------------------------------------------------------------------
# path_of
# --------------------------------------------------------------------------

def _same(a, b):
    if a is b:
        return True
    try:
        return bool(a == b)
    except Exception:
        return False


def _locate(parent, obj):
    """The path fragment (``".devices[2]"``) that leads from parent to obj."""
    for name in _SINGULAR_ATTRS:
        value = compat.safe_getattr(parent, name, _MISSING)
        if value is not _MISSING and not _is_sequence(value) and _same(value, obj):
            return ".%s" % name
    for name in _COLLECTION_ATTRS:
        value = compat.safe_getattr(parent, name, _MISSING)
        if value is _MISSING or not _is_sequence(value):
            continue
        for index, item in enumerate(value):
            if _same(item, obj):
                return ".%s[%d]" % (name, index)
    return None


def path_of(obj, ctx, _depth=0):
    """Best-effort canonical path of a LOM object, or ``None``.

    Walks ``canonical_parent`` upwards (cheap and exact for tracks, scenes,
    clip slots, clips, devices, chains, drum pads and parameters) and falls
    back to a bounded scan of the song when an object has no usable parent.
    """
    if obj is None or ctx is None:
        return None
    if _depth > 12:
        return None
    if _same(obj, ctx.song):
        return "song"
    if _same(obj, ctx.app):
        return "app"
    if ctx.browser is not None and _same(obj, ctx.browser):
        return "browser"
    parent = compat.safe_getattr(obj, "canonical_parent")
    if parent is not None:
        parent_path = path_of(parent, ctx, _depth + 1)
        if parent_path:
            fragment = _locate(parent, obj)
            if fragment:
                return parent_path + fragment
    return _scan_for(obj, ctx)


def _scan_for(obj, ctx):
    """Bounded brute-force search from the song (objects without a parent)."""
    song = ctx.song
    if song is None:
        return None
    groups = (("tracks", compat.safe_getattr(song, "tracks", ())),
              ("return_tracks", compat.safe_getattr(song, "return_tracks", ())))
    for name, tracks in groups:
        for index, track in enumerate(tracks or ()):
            found = _scan_track(obj, track, "song.%s[%d]" % (name, index))
            if found:
                return found
    master = compat.safe_getattr(song, "master_track")
    if master is not None:
        found = _scan_track(obj, master, "song.master_track")
        if found:
            return found
    for name in ("scenes", "cue_points"):
        for index, item in enumerate(compat.safe_getattr(song, name, ()) or ()):
            if _same(item, obj):
                return "song.%s[%d]" % (name, index)
    return None


def _scan_track(obj, track, track_path):
    if _same(track, obj):
        return track_path
    mixer = compat.safe_getattr(track, "mixer_device")
    if mixer is not None:
        if _same(mixer, obj):
            return track_path + ".mixer_device"
        fragment = _locate(mixer, obj)
        if fragment:
            return track_path + ".mixer_device" + fragment
    for index, slot in enumerate(compat.safe_getattr(track, "clip_slots", ()) or ()):
        slot_path = "%s.clip_slots[%d]" % (track_path, index)
        if _same(slot, obj):
            return slot_path
        clip = compat.safe_getattr(slot, "clip")
        if clip is not None and _same(clip, obj):
            return slot_path + ".clip"
    for index, clip in enumerate(compat.safe_getattr(track, "arrangement_clips", ()) or ()):
        if _same(clip, obj):
            return "%s.arrangement_clips[%d]" % (track_path, index)
    for lane_index, lane in enumerate(compat.safe_getattr(track, "take_lanes", ()) or ()):
        lane_path = "%s.take_lanes[%d]" % (track_path, lane_index)
        if _same(lane, obj):
            return lane_path
        for index, clip in enumerate(compat.safe_getattr(lane, "arrangement_clips", ()) or ()):
            if _same(clip, obj):
                return "%s.arrangement_clips[%d]" % (lane_path, index)
    return _scan_devices(obj, compat.safe_getattr(track, "devices", ()) or (),
                         track_path + ".devices", 0)


def _scan_devices(obj, devices, prefix, depth):
    if depth > 6:
        return None
    for index, device in enumerate(devices):
        device_path = "%s[%d]" % (prefix, index)
        if _same(device, obj):
            return device_path
        for param_index, param in enumerate(
                compat.safe_getattr(device, "parameters", ()) or ()):
            if _same(param, obj):
                return "%s.parameters[%d]" % (device_path, param_index)
        for chain_index, chain in enumerate(
                compat.safe_getattr(device, "chains", ()) or ()):
            if _same(chain, obj):
                return "%s.chains[%d]" % (device_path, chain_index)
            found = _scan_devices(obj, compat.safe_getattr(chain, "devices", ()) or (),
                                  "%s.chains[%d].devices" % (device_path, chain_index),
                                  depth + 1)
            if found:
                return found
        for pad_index, pad in enumerate(
                compat.safe_getattr(device, "drum_pads", ()) or ()):
            if _same(pad, obj):
                return "%s.drum_pads[%d]" % (device_path, pad_index)
    return None
