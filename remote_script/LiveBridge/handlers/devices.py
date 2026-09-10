"""Devices: listing, parameters (by index / name / display string), state,
insert / duplicate / move / delete, search across the set, Simpler helpers.

Addressing (every command that takes ``track`` + ``device``):

* ``device`` as a LOM path — ``"song.tracks[0].devices[1]"``,
  ``"song.tracks[2].devices[0].chains[3].devices[0]"`` (nested chains,
  return chains and drum-rack chains all work); ``track`` is then ignored.
* ``device`` as an int — index into ``track.devices`` (negative from the end).
* ``device`` as a name — exact name first (top level, then inside racks), then
  case-insensitive, then class name ("Operator", "Eq8"), then a unique prefix.
* ``device`` omitted — the track's selected device (``track.view.selected_device``),
  or its only device.  ``track`` omitted too — the selected track.

``track`` is an index into ``song.tracks``, a track name (exact, then
case-insensitive prefix), ``"master"`` or a LOM path such as
``"song.return_tracks[0]"``.

Parameter values (``devices.set_parameter`` and friends):

* a JSON number is the **internal** value in ``[min, max]`` (clamped);
* with ``normalized=True`` a number 0..1 is mapped onto ``[min, max]``;
* a string is a **display value**: a ``value_items`` entry for quantized
  parameters (``"Saw"``, ``"On"``, ``"1/8"``), otherwise text as Live shows it
  (``"-12 dB"``, ``"250 ms"``, ``"1.5 kHz"``, ``"25L"``, ``"C"``, ``"40 %"``).
  Display strings are found with a coarse scan of ``str_for_value`` over the
  whole range followed by a bisection on the parsed number, so any monotonic
  mapping (dB, Hz, ms, %) works without knowing the curve.

The helpers at the top (``resolve_device``, ``find_parameter``,
``resolve_value``, ``parameter_info``, ``iter_devices`` ...) are shared with
``plugins.py`` and ``racks.py``.
"""

import difflib
import os
import random
import re

from .. import compat
from .. import resolve
from .. import serialize
from ..registry import BridgeError, command

DETAILS = ("minimal", "summary", "full")

#: Simpler / Sample enums (verified in docs/LIVE_API_DUMP_12.4.5.md).
PLAYBACK_MODES = {0: "classic", 1: "one_shot", 2: "slicing"}
SLICING_PLAYBACK_MODES = {0: "mono", 1: "poly", 2: "thru"}
SLICING_STYLES = {0: "transient", 1: "beat", 2: "region", 3: "manual"}
SLICING_BEAT_DIVISIONS = {0: "1/16", 1: "1/16T", 2: "1/8", 3: "1/8T", 4: "1/4",
                          5: "1/4T", 6: "1/2", 7: "1/2T", 8: "1 bar", 9: "2 bars",
                          10: "4 bars"}
#: Live's own member names for SlicingBeatDivision (accepted as input too).
SLICING_BEAT_DIVISION_NAMES = {0: "sixteenth", 1: "sixteenth_triplett", 2: "eighth",
                               3: "eighth_triplett", 4: "quarter", 5: "quarter_triplett",
                               6: "half", 7: "half_triplett", 8: "one_bar",
                               9: "two_bars", 10: "four_bars"}
WARP_MODES = {0: "beats", 1: "tones", 2: "texture", 3: "repitch", 4: "complex",
              5: "rex", 6: "complex_pro"}

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

_MAX_DEPTH = 8
_SCAN_POINTS = 128
_BISECT_STEPS = 40


# ==========================================================================
# small helpers
# ==========================================================================

#: Validate a ``detail`` argument — shared implementation.
check_detail = resolve.check_detail


def check_paging(offset, limit, max_limit=1000):
    """Validate ``offset`` / ``limit`` and return them as ints (``resolve.check_paging``)."""
    return resolve.check_paging(offset, limit, maximum=max_limit)


def num(value, digits=6):
    """Float rounded for JSON (``None`` for NaN / non-numbers, inf kept as None)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return round(number, digits)


def prune(data):
    """Drop ``None`` values."""
    return dict((k, v) for k, v in data.items() if v is not None)


def note_name(note):
    """MIDI note -> Live's name (60 = C3, 36 = C1)."""
    try:
        note = int(note)
    except (TypeError, ValueError):
        return None
    if note < 0 or note > 127:
        return None
    return "%s%d" % (NOTE_NAMES[note % 12], note // 12 - 2)


def enum_value(value, table, what, extra_names=None):
    """Accept an int or a (case/space-insensitive) name from ``table``."""
    if isinstance(value, bool):
        raise BridgeError("bad_args", "%s: expected a name or an integer" % what)
    if isinstance(value, int):
        if value in table:
            return value
        raise BridgeError("bad_args", "%s: %r is not one of %s"
                          % (what, value, _enum_choices(table)))
    if isinstance(value, str):
        wanted = _norm_key(value)
        for number, name in table.items():
            if _norm_key(name) == wanted:
                return number
        for number, name in (extra_names or {}).items():
            if _norm_key(name) == wanted:
                return number
        if value.strip().lstrip("-").isdigit():
            return enum_value(int(value.strip()), table, what)
    raise BridgeError("bad_args", "%s: %r is not one of %s"
                      % (what, value, _enum_choices(table)))


def _enum_choices(table):
    return ", ".join("%d=%s" % (k, v) for k, v in sorted(table.items()))


def _norm_key(text):
    return re.sub(r"[\s_\-]+", "", str(text).strip().lower())


def _squeeze(text):
    """Letters and digits only, lower-case ("S/C EQ Freq" -> "sceqfreq")."""
    return re.sub(r"[^0-9a-z]+", "", str(text).lower())


def _name(obj, default=""):
    value = compat.safe_getattr(obj, "name", default)
    return value if isinstance(value, str) else default


def is_device(obj):
    return serialize.kind_of(obj) == "device"


def is_rack(device):
    return bool(compat.safe_getattr(device, "can_have_chains", False))


def is_drum_rack(device):
    return bool(compat.safe_getattr(device, "can_have_drum_pads", False))


def is_simpler(device):
    return (compat.safe_getattr(device, "class_name", "") == "OriginalSimpler"
            or (compat.has(device, "playback_mode") and compat.has(device, "sample")))


def device_kind(device):
    """``plugin`` / ``max`` / ``drum_rack`` / ``rack`` / ``simpler`` /
    ``sampler`` / ``native``."""
    if compat.is_plugin_device(device):
        return "plugin"
    if compat.is_max_device(device):
        return "max"
    if is_drum_rack(device):
        return "drum_rack"
    if is_rack(device):
        return "rack"
    if is_simpler(device):
        return "simpler"
    if compat.safe_getattr(device, "class_name", "") == "MultiSampler":
        return "sampler"
    return "native"


DEVICE_KINDS = ("plugin", "max", "drum_rack", "rack", "simpler", "sampler", "native")
DEVICE_TYPES = {"instrument": 1, "audio_effect": 2, "midi_effect": 4}


def device_brief(device, path):
    """Tiny identity dict (for echoing which device a command touched)."""
    return prune({"path": path, "name": _name(device),
                  "class_name": compat.safe_getattr(device, "class_name")})


# ==========================================================================
# walking the set
# ==========================================================================

def iter_tracks(ctx, include_returns=True, include_master=True):
    """Yield ``(track, path)`` for regular, return and master tracks."""
    song = ctx.song
    for index, track in enumerate(compat.safe_getattr(song, "tracks", ()) or ()):
        yield track, "song.tracks[%d]" % index
    if include_returns:
        for index, track in enumerate(compat.safe_getattr(song, "return_tracks", ()) or ()):
            yield track, "song.return_tracks[%d]" % index
    if include_master:
        master = compat.safe_getattr(song, "master_track")
        if master is not None:
            yield master, "song.master_track"


def iter_devices(container, prefix, nested=True, depth=0, max_depth=_MAX_DEPTH):
    """Yield ``(device, path, depth)`` for the devices of a track or chain,
    descending into rack chains and return chains when ``nested``."""
    for index, device in enumerate(compat.safe_getattr(container, "devices", ()) or ()):
        path = "%s.devices[%d]" % (prefix, index)
        yield device, path, depth
        if nested and depth < max_depth and is_rack(device):
            for attr in ("chains", "return_chains"):
                for chain_index, chain in enumerate(
                        compat.safe_getattr(device, attr, ()) or ()):
                    for item in iter_devices(chain, "%s.%s[%d]" % (path, attr, chain_index),
                                             nested, depth + 1, max_depth):
                        yield item


def host_of(device):
    """The Track or Chain that holds ``device`` (its ``canonical_parent``)."""
    parent = compat.safe_getattr(device, "canonical_parent")
    if parent is None or not compat.has(parent, "devices"):
        raise BridgeError("invalid_state", "cannot find the track/chain holding %r"
                          % _name(device, "device"))
    return parent


def index_in(collection, obj):
    for index, item in enumerate(collection or ()):
        try:
            if item is obj or item == obj:
                return index
        except Exception:
            continue
    return None


def path_of(ctx, obj, fallback=None):
    try:
        path = ctx.path_of(obj)
    except Exception:
        path = None
    return path or fallback


# ==========================================================================
# device resolution
# ==========================================================================

def _selected_track(ctx):
    track = compat.safe_getattr(compat.safe_getattr(ctx.song, "view"), "selected_track")
    if track is None:
        raise BridgeError("invalid_state", "no track is selected — pass track")
    return track


def resolve_track(ctx, track):
    """``track`` argument -> Track (selected track when ``None``)."""
    if track is None:
        return _selected_track(ctx)
    return ctx.track(track)


def resolve_device(ctx, track=None, device=None):
    """Resolve ``track`` + ``device`` arguments to ``(device, path)``.

    See the module docstring for the accepted forms.

    Raises:
        BridgeError: ``not_found`` / ``bad_args`` with the available device
            names so Claude can correct itself.
    """
    if isinstance(device, bool):
        raise BridgeError("bad_args", "device must be an index, a name or a LOM path")
    if device is not None and not isinstance(device, (int, str)):
        if is_device(device):
            return device, path_of(ctx, device)
        raise BridgeError("bad_args", "device must be an index, a name or a LOM path, got %s"
                          % type(device).__name__)
    if isinstance(device, str) and device.strip().startswith("song."):
        text = device.strip()
        obj = ctx.resolve(text)
        if obj is None:
            raise BridgeError("not_found", "%s: nothing there" % text)
        if not is_device(obj):
            raise BridgeError("bad_args", "%s is a %s, not a device"
                              % (text, serialize.kind_of(obj) or type(obj).__name__))
        return obj, path_of(ctx, obj, text)
    track_obj = resolve_track(ctx, track)
    track_path = path_of(ctx, track_obj, "track")
    devices = list(compat.safe_getattr(track_obj, "devices", ()) or ())
    if device is None:
        selected = compat.safe_getattr(compat.safe_getattr(track_obj, "view"),
                                       "selected_device")
        if selected is not None and is_device(selected):
            return selected, path_of(ctx, selected)
        if len(devices) == 1:
            return devices[0], "%s.devices[0]" % track_path
        if not devices:
            raise BridgeError("not_found", "%r has no devices" % _name(track_obj, "track"))
        raise BridgeError("bad_args", "%r has %d devices and none is selected — pass device "
                          "(have: %s)" % (_name(track_obj, "track"), len(devices),
                                          _names(devices)))
    if isinstance(device, int) or device.strip().lstrip("-").isdigit():
        index = int(device)
        if -len(devices) <= index < len(devices):
            real = index % len(devices)
            return devices[real], "%s.devices[%d]" % (track_path, real)
        raise BridgeError("not_found", "%s.devices[%d]: index out of range (%d devices%s)"
                          % (track_path, index, len(devices),
                             (": " + _names(devices)) if devices else ""))
    return _device_by_name(track_obj, track_path, device.strip())


def _names(objects, limit=16):
    names = [repr(_name(o)) for o in list(objects)[:limit]]
    if len(objects) > limit:
        names.append("...")
    return ", ".join(names)


def _device_by_name(track_obj, track_path, text):
    if not text:
        raise BridgeError("bad_args", "device name must not be empty")
    found = list(iter_devices(track_obj, track_path))
    lowered = text.lower()

    def name_of(entry):
        return _name(entry[0])

    tiers = (
        [e for e in found if name_of(e) == text],
        [e for e in found if name_of(e).lower() == lowered],
        [e for e in found
         if str(compat.safe_getattr(e[0], "class_name", "")).lower() == lowered
         or str(compat.safe_getattr(e[0], "class_display_name", "")).lower() == lowered],
        [e for e in found if name_of(e).lower().startswith(lowered)],
        [e for e in found if lowered in name_of(e).lower()],
    )
    for level, matches in enumerate(tiers):
        if not matches:
            continue
        if level <= 2:
            # exact-ish: the first one in chain order wins (top level first).
            matches.sort(key=lambda e: e[2])
            return matches[0][0], matches[0][1]
        distinct = set(name_of(e) for e in matches)
        if len(distinct) == 1:
            matches.sort(key=lambda e: e[2])
            return matches[0][0], matches[0][1]
        raise BridgeError("bad_args", "device %r is ambiguous on %r: %s — use the exact name, "
                          "an index or a path"
                          % (text, _name(track_obj, "track"),
                             ", ".join(sorted(repr(n) for n in distinct))))
    raise BridgeError("not_found", "no device named %r on %r (have: %s)"
                      % (text, _name(track_obj, "track"),
                         ", ".join(repr(name_of(e)) for e in found[:20]) or "no devices"))


def resolve_container(ctx, track=None, chain=None):
    """A device container: a chain (LOM path) or a track -> ``(obj, path)``."""
    if chain is not None:
        if not isinstance(chain, str) or not chain.strip().startswith("song."):
            raise BridgeError("bad_args", "chain must be a LOM path such as "
                              "'song.tracks[0].devices[1].chains[0]'")
        obj = ctx.resolve(chain.strip())
        if obj is None or serialize.kind_of(obj) != "chain":
            raise BridgeError("bad_args", "%s is not a rack chain" % chain)
        return obj, path_of(ctx, obj, chain.strip())
    track_obj = resolve_track(ctx, track)
    return track_obj, path_of(ctx, track_obj, "track")


# ==========================================================================
# parameters
# ==========================================================================

def parameters_of(device):
    return list(compat.safe_getattr(device, "parameters", ()) or ())


def find_parameter(ctx, device, spec):
    """Resolve a parameter argument (index, name or LOM path) -> ``(param, index)``.

    ``index`` is ``None`` when a path points at a parameter of another object
    (a mixer parameter, another device).
    """
    params = parameters_of(device) if device is not None else []
    if isinstance(spec, bool) or spec is None:
        raise BridgeError("bad_args", "parameter must be an index, a name or a LOM path")
    if isinstance(spec, str) and spec.strip().startswith("song."):
        obj = ctx.resolve(spec.strip())
        if obj is None or serialize.kind_of(obj) != "parameter":
            raise BridgeError("bad_args", "%s is not a device parameter" % spec)
        return obj, index_in(params, obj)
    if device is None:
        raise BridgeError("bad_args", "parameter %r needs a device (or pass a full LOM path)"
                          % (spec,))
    if isinstance(spec, int) or (isinstance(spec, str) and spec.strip().lstrip("-").isdigit()):
        index = int(spec)
        if -len(params) <= index < len(params):
            real = index % len(params)
            return params[real], real
        raise BridgeError("not_found", "parameters[%d]: index out of range (%d parameters on %r)"
                          % (index, len(params), _name(device, "device")))
    if not isinstance(spec, str):
        raise BridgeError("bad_args", "parameter must be an index, a name or a LOM path, got %s"
                          % type(spec).__name__)
    text = spec.strip()
    if not text:
        raise BridgeError("bad_args", "parameter name must not be empty")
    plugin = compat.is_plugin_device(device)
    if plugin:  # "Name #n" + duplicate names (AUNBandEQ's eight "Frequency"); see plugins.py
        from . import plugins as plugin_handlers  # lazy: plugins.py imports this module
        found = plugin_handlers.find_plugin_parameter(device, params, text, "before")
        if found is not None:
            return found
    lowered = text.lower()
    indexed = list(enumerate(params))
    squeezed = _squeeze(text)
    tiers = (
        [(i, p) for i, p in indexed if _name(p) == text],
        [(i, p) for i, p in indexed if _name(p).lower() == lowered],
        [(i, p) for i, p in indexed
         if str(compat.safe_getattr(p, "original_name", "")).lower() == lowered],
        [(i, p) for i, p in indexed if squeezed and _squeeze(_name(p)) == squeezed],
        [(i, p) for i, p in indexed if _name(p).lower().startswith(lowered)],
        [(i, p) for i, p in indexed if lowered in _name(p).lower()],
        [(i, p) for i, p in indexed if squeezed and _squeeze(_name(p)).startswith(squeezed)],
        [(i, p) for i, p in indexed if squeezed and squeezed in _squeeze(_name(p))],
    )
    for level, matches in enumerate(tiers):
        if not matches:
            continue
        if len(matches) == 1 or level <= 3:
            return matches[0][1], matches[0][0]
        raise BridgeError("bad_args", "parameter %r is ambiguous on %r: %s — use the exact "
                          "name or the index"
                          % (text, _name(device, "device"),
                             ", ".join("%d %r" % (i, _name(p)) for i, p in matches[:10])))
    if plugin:  # synonyms ("cutoff" -> "Filter 1 Freq") + "exists but not exposed (Configure)"
        found = plugin_handlers.find_plugin_parameter(device, params, text, "after")
        if found is not None:
            return found
    raise BridgeError("not_found", "no parameter named %r on %r (%d parameters, e.g. %s) — "
                      "list them with devices.parameters and a filter"
                      % (text, _name(device, "device"), len(params),
                         ", ".join(repr(_name(p)) for p in params[:12])))


def display_of(param, value=None):
    """``str_for_value`` as text (``None`` when Live cannot format it)."""
    if value is None:
        value = compat.safe_getattr(param, "value")
    if value is None:
        return None
    try:
        text = param.str_for_value(float(value))
    except Exception:
        return None
    return str(text).strip() if text is not None else None


def value_items(param):
    """``value_items`` as a list of strings (empty for continuous params)."""
    if not compat.safe_getattr(param, "is_quantized", False):
        return []
    items = compat.safe_getattr(param, "value_items", ()) or ()
    try:
        return [str(item) for item in items]
    except Exception:
        return []


def parameter_info(param, index=None, path=None, detail="summary"):
    """Compact dict for one parameter.

    minimal: index, name, value, display.
    summary: + min, max, quantized/items, enabled=false, automation, original_name.
    full:    + path, default, normalized, state.
    """
    get = compat.safe_getattr
    value = get(param, "value")
    data = {"index": index, "name": _name(param), "value": num(value),
            "display": display_of(param, value)}
    if detail == "minimal":
        return prune(data)
    minimum = get(param, "min")
    maximum = get(param, "max")
    data["min"] = num(minimum)
    data["max"] = num(maximum)
    if get(param, "is_quantized", False):
        data["quantized"] = True
        items = value_items(param)
        if items:
            data["items"] = items
    if not get(param, "is_enabled", True):
        data["enabled"] = False
    automation = get(param, "automation_state", 0)
    try:
        if int(automation):
            data["automation"] = serialize.AUTOMATION_STATES.get(int(automation),
                                                                 int(automation))
    except (TypeError, ValueError):
        pass
    original = get(param, "original_name")
    if isinstance(original, str) and original and original != data["name"]:
        data["original_name"] = original
    if detail == "full":
        data["path"] = path
        if not get(param, "is_quantized", False):
            data["default"] = num(get(param, "default_value"))
        try:
            span = float(maximum) - float(minimum)
            data["normalized"] = round((float(value) - float(minimum)) / span, 6) \
                if span else 0.0
        except (TypeError, ValueError):
            pass
        state = get(param, "state")
        if state is not None:
            try:
                data["state"] = serialize.PARAMETER_STATES.get(int(state), int(state))
            except (TypeError, ValueError):
                pass
    return prune(data)


# -- display strings ----------------------------------------------------------

_UNIT_ALIASES = {
    "db": ("db", 1.0), "dbfs": ("db", 1.0),
    "hz": ("hz", 1.0), "khz": ("hz", 1000.0),
    "ms": ("s", 0.001), "s": ("s", 1.0), "sec": ("s", 1.0), "secs": ("s", 1.0),
    "%": ("%", 1.0), "pct": ("%", 1.0),
    "st": ("st", 1.0), "semitones": ("st", 1.0), "ct": ("ct", 1.0), "cents": ("ct", 1.0),
    "": ("", 1.0),
}
_NUMBER_RE = re.compile(r"^([+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?)\s*(.*)$")
_PAN_RE = re.compile(r"^(?:(\d+(?:\.\d+)?)\s*([lr])|([lr])\s*(\d+(?:\.\d+)?))$")
_FRACTION_RE = re.compile(r"^(\d+)\s*/\s*(\d+)$")
#: Compressor-style ratios: "4:1", "4.00 : 1", "inf : 1", "1 : 1.15" (expansion).
_RATIO_RE = re.compile(r"^(\d+(?:\.\d+)?|inf)\s*:\s*(\d+(?:\.\d+)?|inf)$")


#: A compound display: a reading plus a second one in brackets, e.g. Serum 2's
#: "50% [-9.0 dB]" (Main Vol, A/B/C Level, Sub/Noise Level, Direct/Bus Vol).
_COMPOUND_RE = re.compile(r"^(.*?\S)\s*[\[(]\s*([^\])]*?)\s*[\])]$")


def display_readings(text):
    """Every ``(number, unit)`` reading of a display string, primary first.

    ``"50% [-9.0 dB]"`` -> ``[(50.0, "%"), (-9.0, "db")]``; a plain display
    gives one reading (``parse_display``); no number -> ``[]``.
    """
    if text is None:
        return []
    match = _COMPOUND_RE.match(str(text).strip())
    if match:
        readings = [parse_display(match.group(1)), parse_display(match.group(2))]
        readings = [r for r in readings if r is not None]
        if readings:
            return readings
    reading = parse_display(text)
    return [reading] if reading is not None else []


def _reading(text, unit):
    """The reading of display ``text`` in ``unit`` (any unit when ``unit`` is empty)."""
    readings = display_readings(text)
    if not unit:
        return readings[0] if readings else None
    for reading in readings:
        if reading[1] == unit:
            return reading
    return None


def norm_display(text):
    """Normalise display text for exact comparison."""
    text = re.sub(r"\s+", " ", str(text).strip().lower())
    return re.sub(r"\s*/\s*", "/", text)


def parse_display(text):
    """Parse a display string into ``(number, unit)`` in canonical units.

    ``"1.00 kHz"`` -> ``(1000.0, "hz")``, ``"250 ms"`` -> ``(0.25, "s")``,
    ``"-inf dB"`` -> ``(-inf, "db")``, ``"25L"`` -> ``(-25.0, "pan")``,
    ``"C"`` -> ``(0.0, "pan")``, ``"3/16"`` -> ``(0.1875, "fraction")``,
    ``"4:1"`` / ``"4.00 : 1"`` -> ``(4.0, "ratio")``, ``"1 : 2"`` -> ``(0.5, "ratio")``.
    A compound display gives its primary reading: ``"50% [-9.0 dB]"`` ->
    ``(50.0, "%")`` (``display_readings`` returns both).
    Returns ``None`` for text without a number.
    """
    if text is None:
        return None
    raw = str(text).strip().lower()
    if not raw:
        return None
    compound = _COMPOUND_RE.match(raw)
    if compound and _NUMBER_RE.match(compound.group(2)):
        head = parse_display(compound.group(1))
        if head is not None:
            return head
    if raw in ("c", "center", "centre"):
        return 0.0, "pan"
    match = _PAN_RE.match(raw)
    if match:
        if match.group(1) is not None:
            amount, side = float(match.group(1)), match.group(2)
        else:
            amount, side = float(match.group(4)), match.group(3)
        return (-amount if side == "l" else amount), "pan"
    inf = re.match(r"^([+-]?)inf(?:inity)?\s*([a-z%]*)$", raw)
    if inf:
        unit = _UNIT_ALIASES.get(inf.group(2), (inf.group(2), 1.0))[0]
        return (float("-inf") if inf.group(1) == "-" else float("inf")), unit
    fraction = _FRACTION_RE.match(raw)
    if fraction and int(fraction.group(2)):
        return int(fraction.group(1)) / float(int(fraction.group(2))), "fraction"
    ratio = _RATIO_RE.match(raw)
    if ratio:
        left, right = ratio.group(1), ratio.group(2)
        left = float("inf") if left == "inf" else float(left)
        right = float("inf") if right == "inf" else float(right)
        if right == 0 or (left == float("inf") and right == float("inf")):
            return None
        return left / right, "ratio"
    match = _NUMBER_RE.match(raw)
    if not match:
        return None
    try:
        number = float(match.group(1))
    except ValueError:
        return None
    unit_text = match.group(2).strip()
    if unit_text.startswith("/"):
        denominator = re.match(r"^/\s*(\d+)$", unit_text)
        if denominator and int(denominator.group(1)):
            return number / float(int(denominator.group(1))), "fraction"
    unit, scale = _UNIT_ALIASES.get(unit_text, (unit_text, 1.0))
    return number * scale, unit


def _sample_points(minimum, maximum, quantized):
    span = maximum - minimum
    points = set()
    integral = float(minimum).is_integer() and float(maximum).is_integer()
    if quantized or (integral and 2 <= span <= 512):
        if span <= 4096:
            points.update(float(v) for v in range(int(round(minimum)), int(round(maximum)) + 1))
    if not quantized:
        for step in range(_SCAN_POINTS + 1):
            points.add(minimum + span * step / float(_SCAN_POINTS))
    return sorted(points)


def value_for_display(param, text):
    """Find the internal value whose ``str_for_value`` matches ``text``.

    Raises:
        BridgeError: ``bad_args`` when nothing in the range displays like
            ``text`` (the message shows the display range and examples).
    """
    minimum = float(compat.safe_getattr(param, "min", 0.0))
    maximum = float(compat.safe_getattr(param, "max", 1.0))
    quantized = bool(compat.safe_getattr(param, "is_quantized", False))
    if maximum < minimum:
        minimum, maximum = maximum, minimum
    samples = []
    for x in _sample_points(minimum, maximum, quantized):
        display = display_of(param, x)
        if display is not None:
            samples.append((x, display))
    if not samples:
        raise BridgeError("unsupported", "%r cannot format its values (str_for_value failed); "
                          "pass a number instead" % _name(param, "parameter"))
    wanted = norm_display(text)
    exact = [x for x, display in samples if norm_display(display) == wanted]
    target = parse_display(text)
    if target is not None and not quantized:
        value = _numeric_search(param, samples, target)
        if value is not None:
            return value
    if exact:
        return exact[0]
    low, high = samples[0][1], samples[-1][1]
    examples = []
    for _x, display in samples[::max(1, len(samples) // 6)]:
        if display not in examples:
            examples.append(display)
    if target is not None:
        units = set(r[1] for _x, d in samples for r in display_readings(d))
        if units and target[1] and target[1] not in units:
            reason = "unit %r does not match this parameter's display (%s)" % (
                target[1], ", ".join(sorted(repr(u) for u in units)))
        else:
            reason = "it is outside the display range %s … %s" % (low, high)
    else:
        reason = "no value displays like that"
    raise BridgeError("bad_args", "%r: cannot set %r — %s. Examples: %s"
                      % (_name(param, "parameter"), text, reason,
                         ", ".join(repr(e) for e in examples[:8])))


def _numeric_search(param, samples, target):
    number, unit = target

    def measure(x):
        parsed = _reading(display_of(param, x), unit)
        return None if parsed is None else parsed[0]

    points = []
    for x, display in samples:
        parsed = _reading(display, unit)
        if parsed is None:
            continue
        points.append((x, parsed[0]))
    if not points:
        return None
    bracket = None
    for position, ((x0, y0), (x1, y1)) in enumerate(zip(points, points[1:])):
        if min(y0, y1) <= number <= max(y0, y1):
            bracket = position
            break
    if bracket is None:
        for x, y in points:
            if y == number:
                return x
        return None
    x0, y0 = points[bracket]
    x1, y1 = points[bracket + 1]
    if y0 == y1:
        return (x0 + x1) / 2.0
    increasing = y1 > y0

    def reached(x):
        y = measure(x)
        return y is not None and (y >= number if increasing else y <= number)

    def beyond(x):
        y = measure(x)
        return y is not None and (y > number if increasing else y < number)

    def bisect(lo, hi, test):
        """First x in (lo, hi] where ``test`` holds (``test(hi)`` is True)."""
        for _ in range(_BISECT_STEPS):
            mid = (lo + hi) / 2.0
            if test(mid):
                hi = mid
            else:
                lo = mid
        return hi

    # a = first x where the display reaches the target
    a = x0 if reached(x0) else bisect(x0, x1, reached)
    # b = last x before the display goes past the target (the rounding band may
    # continue over several scan points)
    b = None
    previous = a
    for x, _y in points[bracket + 1:]:
        if beyond(x):
            b = bisect(previous, x, beyond)
            break
        previous = x
    if b is None:
        b = previous
    # A band that touches either end of the range means "all the way": "100 %",
    # "+6 dB", "-inf dB", "50L" land exactly on max/min.
    low_end, high_end = points[0][0], points[-1][0]
    span = abs(high_end - low_end) or 1.0
    if b >= high_end - span * 1e-9 and measure(high_end) == measure(a):
        return high_end
    if a <= low_end + span * 1e-9 and measure(low_end) == measure(a):
        return low_end
    middle = (a + b) / 2.0
    if measure(middle) != measure(a):
        return a
    for digits in (3, 6):
        tidy = round(middle, digits)
        if a <= tidy <= b and measure(tidy) == measure(middle):
            return tidy
    return middle


def resolve_value(param, value, normalized=False):
    """Turn a JSON value into the internal value to write.

    Returns ``(value, how, clamped)`` where ``how`` is ``raw``,
    ``normalized``, ``bool``, ``item`` or ``display``.
    """
    name = _name(param, "parameter")
    minimum = float(compat.safe_getattr(param, "min", 0.0))
    maximum = float(compat.safe_getattr(param, "max", 1.0))
    if maximum < minimum:
        minimum, maximum = maximum, minimum
    quantized = bool(compat.safe_getattr(param, "is_quantized", False))
    if isinstance(value, bool):
        target, how = (maximum if value else minimum), "bool"
    elif isinstance(value, (int, float)):
        target = float(value)
        if target != target or target in (float("inf"), float("-inf")):
            raise BridgeError("bad_args", "%r: value must be a finite number" % name)
        how = "raw"
        if normalized:
            outside = not 0.0 <= target <= 1.0
            target = minimum + max(0.0, min(1.0, target)) * (maximum - minimum)
            return _finish(target, "normalized", outside, minimum, maximum, quantized)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise BridgeError("bad_args", "%r: value must not be empty" % name)
        if normalized:
            try:
                return resolve_value(param, float(text), True)
            except ValueError:
                raise BridgeError("bad_args", "%r: normalized values must be numbers 0..1"
                                  % name)
        items = value_items(param)
        match = _match_item(param, items, text)
        if match is not None:
            index = match
            if abs((maximum - minimum + 1) - len(items)) < 0.5:
                target = minimum + index
            else:
                target = float(index)
            how = "item"
        else:
            target, how = value_for_display(param, text), "display"
    else:
        raise BridgeError("bad_args", "%r: value must be a number, a boolean or a display "
                          "string, got %s" % (name, type(value).__name__))
    clamped = target < minimum or target > maximum
    return _finish(target, how, clamped, minimum, maximum, quantized)


def _finish(target, how, clamped, minimum, maximum, quantized):
    target = max(minimum, min(maximum, target))
    if quantized:
        target = float(int(round(target)))
        target = max(minimum, min(maximum, target))
    return target, how, clamped


def _match_item(param, items, text):
    if not items:
        return None
    wanted = norm_display(text)
    for index, item in enumerate(items):
        if norm_display(item) == wanted:
            return index
    short = compat.safe_getattr(param, "short_value_items", ()) or ()
    try:
        for index, item in enumerate(short):
            if norm_display(item) == wanted and index < len(items):
                return index
    except Exception:
        pass
    squeezed = _norm_key(text)
    for index, item in enumerate(items):
        if _norm_key(item) == squeezed:
            return index
    return None


def write_parameter(param, target):
    """``param.value = target`` with protocol errors."""
    if not compat.safe_getattr(param, "is_enabled", True):
        raise BridgeError("invalid_state", "%r is disabled (macro-mapped or controlled by Max) "
                          "— change the controlling macro instead" % _name(param, "parameter"))
    try:
        param.value = float(target)
    except (RuntimeError, ValueError, TypeError, AttributeError) as error:
        raise BridgeError("invalid_state", "Live refused %r for %r: %s"
                          % (target, _name(param, "parameter"), error))


def set_parameter_value(param, value, normalized=False, index=None, path=None):
    """Resolve + write one value; return the compact result dict."""
    before = compat.safe_getattr(param, "value")
    target, how, clamped = resolve_value(param, value, normalized)
    write_parameter(param, target)
    after = compat.safe_getattr(param, "value", target)
    result = {"index": index, "name": _name(param), "path": path,
              "value": num(after), "display": display_of(param, after),
              "previous": display_of(param, before) if before is not None else None}
    if how in ("display", "item", "normalized"):
        result["matched"] = how
    if clamped:
        result["clamped"] = True
    return prune(result)


def param_path(device_path, index):
    if device_path and index is not None:
        return "%s.parameters[%d]" % (device_path, index)
    return None


# ==========================================================================
# commands — listing
# ==========================================================================

def _device_node(ctx, device, path, detail, tree, depth, max_depth):
    # "full" parameters are built here with known paths (the generic
    # serializer would search every parameter's path — slow on big plug-ins).
    node = ctx.summarize(device, "summary" if detail == "full" else detail)
    if not isinstance(node, dict):
        node = device_brief(device, path)
    node["path"] = node.get("path") or path
    if detail == "full":
        node["parameters"] = [parameter_info(p, i, param_path(node["path"], i), "summary")
                              for i, p in enumerate(parameters_of(device))]
    if compat.is_plugin_device(device):
        node["is_plugin"] = True
    if tree and is_rack(device) and depth < max_depth:
        for attr in ("chains", "return_chains"):
            chains = list(compat.safe_getattr(device, attr, ()) or ())
            if not chains and attr == "return_chains":
                continue
            entries = []
            for chain_index, chain in enumerate(chains):
                chain_path = "%s.%s[%d]" % (node["path"], attr, chain_index)
                entry = {"index": chain_index, "name": _name(chain), "path": chain_path}
                in_note = compat.safe_getattr(chain, "in_note")
                if isinstance(in_note, int) and not isinstance(in_note, bool):
                    entry["in_note"] = in_note
                    entry["key"] = note_name(in_note) if in_note >= 0 else "all"
                if compat.safe_getattr(chain, "mute", False):
                    entry["mute"] = True
                if compat.safe_getattr(chain, "solo", False):
                    entry["solo"] = True
                entry["devices"] = [
                    _device_node(ctx, d, "%s.devices[%d]" % (chain_path, i), detail, tree,
                                 depth + 1, max_depth)
                    for i, d in enumerate(compat.safe_getattr(chain, "devices", ()) or ())]
                entries.append(entry)
            node[attr] = entries
    return node


@command("devices.list", doc="List the devices of a track (flat, or as a tree through racks)")
def devices_list(ctx, track=None, tree=False, detail="minimal", max_depth=4):
    """List a track's device chain.

    Args:
        track: index / name / "master" / LOM path; default = the selected track.
        tree: also descend into rack chains (and return chains); drum-rack
            chains carry ``in_note`` + ``key`` (the pad they answer to).
        detail: "minimal" (index, name, class_name, type, is_active, path),
            "summary" (+ parameter/chain/preset counts, Simpler sample) or
            "full" (+ every parameter — large, prefer devices.parameters).
        max_depth: rack nesting depth for ``tree`` (1..8).

    Returns:
        {track: {path, name}, count, selected: <path of the selected device>|null,
         devices: [{index, name, class_name, type, is_active, path, ...,
                    chains?: [{index, name, path, in_note?, key?, devices: [...]}]}]}

    Gotchas:
        Indices are positions in ``track.devices``; Live orders MIDI effects,
        then the instrument, then audio effects.  Paths stay valid until
        devices are inserted/removed before them.
    """
    check_detail(detail)
    if isinstance(max_depth, bool) or not isinstance(max_depth, int) or not 1 <= max_depth <= 8:
        raise BridgeError("bad_args", "max_depth must be an integer 1..8")
    track_obj = resolve_track(ctx, track)
    track_path = path_of(ctx, track_obj, "track")
    devices = list(compat.safe_getattr(track_obj, "devices", ()) or ())
    selected = compat.safe_getattr(compat.safe_getattr(track_obj, "view"), "selected_device")
    nodes = [_device_node(ctx, d, "%s.devices[%d]" % (track_path, i), detail, bool(tree), 0,
                          max_depth)
             for i, d in enumerate(devices)]
    return {
        "track": {"path": track_path, "name": _name(track_obj)},
        "count": len(devices),
        "selected": path_of(ctx, selected) if selected is not None else None,
        "devices": nodes,
    }


@command("devices.get", doc="One device: summary, state, rack/plugin/Simpler extras")
def devices_get(ctx, track=None, device=None, detail="summary", max_parameters=64):
    """Describe one device.

    Args:
        track, device: see the module docstring (default: selected device).
        detail: "minimal" | "summary" | "full" ("full" adds the first
            ``max_parameters`` parameters in compact form).
        max_parameters: cap for the parameter list at "full" (1..1000).

    Returns:
        The device summary plus {kind, collapsed, selected, host (track/chain
        path), position, latency_ms, can_compare_ab, compare_b?, plugin
        {presets, selected_preset}?, macros?, chains? (minimal), parameters?,
        class_properties? (names of the class-specific properties — read/set
        them with devices.properties / devices.set_properties), class_actions?
        (methods for devices.action)}

    Gotchas:
        Native device presets are browser items (use the browser tools to
        hot-swap them); only plug-ins expose a preset list here.
    """
    check_detail(detail)
    if isinstance(max_parameters, bool) or not isinstance(max_parameters, int) \
            or not 1 <= max_parameters <= 1000:
        raise BridgeError("bad_args", "max_parameters must be an integer 1..1000")
    dev, path = resolve_device(ctx, track, device)
    get = compat.safe_getattr
    data = ctx.summarize(dev, "minimal" if detail == "minimal" else "summary")
    data["path"] = data.get("path") or path
    path = data["path"]
    data["device_kind"] = device_kind(dev)
    if detail == "minimal":
        return data
    view = get(dev, "view")
    data["collapsed"] = bool(get(view, "is_collapsed", False)) if view is not None else None
    host = get(dev, "canonical_parent")
    data["host"] = path_of(ctx, host) if host is not None else None
    data["position"] = index_in(get(host, "devices", ()) or (), dev) if host is not None \
        else None
    track_obj = _track_of_device(dev)
    selected = get(get(track_obj, "view"), "selected_device") if track_obj is not None else None
    data["selected"] = bool(selected is not None and selected == dev)
    latency = get(dev, "latency_in_ms")
    if latency:
        data["latency_ms"] = num(latency, 3)
    if get(dev, "can_compare_ab", False):
        data["can_compare_ab"] = True
        data["compare_b"] = bool(get(dev, "is_using_compare_preset_b", False))
    if compat.is_plugin_device(dev):
        presets = list(get(dev, "presets", ()) or ())
        selected_preset = get(dev, "selected_preset_index")
        data["plugin"] = prune({
            "preset_count": len(presets),
            "selected_preset_index": selected_preset,
            "selected_preset": presets[selected_preset]
            if isinstance(selected_preset, int) and 0 <= selected_preset < len(presets)
            else None,
            "editor_open": get(dev, "is_editor_open"),
        })
    try:
        class_rows, class_methods = device_properties(dev)
    except Exception as error:  # never let discovery break devices.get
        ctx.log("devices.get: property discovery failed: %s", error)
        class_rows, class_methods = [], []
    if class_rows:
        data["class_properties"] = [r["name"] for r in class_rows]
    actions = [m for m in class_methods if m not in _CURATED_METHODS]
    if actions:
        data["class_actions"] = actions
    if is_rack(dev):
        chains = list(get(dev, "chains", ()) or ())
        data["chains"] = [{"index": i, "name": _name(c), "path": "%s.chains[%d]" % (path, i)}
                          for i, c in enumerate(chains)]
        visible = get(dev, "visible_macro_count")
        if visible is not None:
            data["visible_macro_count"] = visible
        if get(dev, "variation_count") is not None:
            data["variation_count"] = get(dev, "variation_count")
    if detail == "full":
        params = parameters_of(dev)
        data["parameters"] = [parameter_info(p, i, param_path(path, i), "summary")
                              for i, p in enumerate(params[:max_parameters])]
        if len(params) > max_parameters:
            data["parameters_truncated"] = len(params) - max_parameters
    return prune(data)


def _track_of_device(device):
    obj = device
    for _ in range(16):
        obj = compat.safe_getattr(obj, "canonical_parent")
        if obj is None:
            return None
        if compat.has(obj, "clip_slots") and compat.has(obj, "mixer_device"):
            return obj
    return None


@command("devices.parameters",
         doc="List a device's parameters (paged, filterable, with display values)")
def devices_parameters(ctx, track=None, device=None, filter=None, offset=0, limit=64,
                       only_changed=False, detail="summary"):
    """Page through a device's parameters.

    Args:
        track, device: see the module docstring.
        filter: case-insensitive substring of the name or original name
            ("freq", "attack", "macro").
        offset, limit: paging over the (filtered) list; limit 1..1000.
        only_changed: only continuous parameters whose value differs from
            their default (quantized ones have no default in Live).
        detail: "minimal" (index, name, value, display), "summary" (+ min,
            max, quantized/items, enabled, automation) or "full" (+ path,
            default, normalized, state).

    Returns:
        {device: {path, name, class_name}, total, matched, offset, count,
         parameters: [...], next_offset?}

    Gotchas:
        ``parameters[0]`` is always "Device On".  ``value`` is Live's internal
        value (often 0..1 even for dB/Hz knobs) — read ``display`` for what
        the GUI shows.
    """
    check_detail(detail)
    offset, limit = check_paging(offset, limit)
    if filter is not None and not isinstance(filter, str):
        raise BridgeError("bad_args", "filter must be a string")
    dev, path = resolve_device(ctx, track, device)
    params = list(enumerate(parameters_of(dev)))
    if filter:
        needle = filter.strip().lower()
        params = [(i, p) for i, p in params
                  if needle in _name(p).lower()
                  or needle in str(compat.safe_getattr(p, "original_name", "")).lower()]
    if only_changed:
        changed = []
        for i, p in params:
            if compat.safe_getattr(p, "is_quantized", False):
                continue
            default = compat.safe_getattr(p, "default_value")
            value = compat.safe_getattr(p, "value")
            if default is None or value is None:
                continue
            if abs(float(value) - float(default)) > 1e-6:
                changed.append((i, p))
        params = changed
    page = params[offset:offset + limit]
    result = {
        "device": device_brief(dev, path),
        "total": len(parameters_of(dev)),
        "matched": len(params),
        "offset": offset,
        "count": len(page),
        "parameters": [parameter_info(p, i, param_path(path, i), detail) for i, p in page],
    }
    if offset + limit < len(params):
        result["next_offset"] = offset + limit
    return result


@command("devices.get_parameter", doc="One parameter in full detail")
def devices_get_parameter(ctx, parameter, track=None, device=None):
    """Read one parameter.

    Args:
        parameter: index, name (exact, case-insensitive, original name, then a
            unique prefix/substring) or a LOM path (then track/device are
            optional — works for mixer parameters too).
        track, device: see the module docstring.

    Returns:
        {index, name, value, display, min, max, quantized?, items?, default?,
         normalized, state, automation?, enabled?, path, device: {...}}
    """
    dev = None
    path = None
    if not (isinstance(parameter, str) and parameter.strip().startswith("song.")):
        dev, path = resolve_device(ctx, track, device)
    param, index = find_parameter(ctx, dev, parameter)
    ppath = param_path(path, index) or path_of(ctx, param, parameter
                                                  if isinstance(parameter, str) else None)
    info = parameter_info(param, index, ppath, "full")
    if dev is not None:
        info["device"] = device_brief(dev, path)
    return info


# ==========================================================================
# commands — writing parameters
# ==========================================================================

@command("devices.set_parameter", mutating=True,
         doc="Set one parameter by number, normalized 0..1, value item or display string")
def devices_set_parameter(ctx, parameter, value, track=None, device=None, normalized=False):
    """Set one device parameter.

    Args:
        parameter: index, name or LOM path (see devices.get_parameter).
        value: a number = internal value (clamped to [min, max]); a boolean =
            max/min (on/off switches); a string = display value: a value item
            ("Saw", "On", "1/8") or GUI text ("-12 dB", "250 ms", "1.5 kHz",
            "25L", "C", "40 %").
        normalized: interpret a numeric value as 0..1 across [min, max].
        track, device: see the module docstring.

    Returns:
        {index, name, path, value, display, previous (display before),
         matched? ("item"|"display"|"normalized"), clamped?}

    Gotchas:
        Display strings are matched against what this very parameter shows,
        so use its units (read ``display`` first).  Macro-mapped parameters
        (enabled=false) cannot be set — change the macro instead.
    """
    dev = None
    path = None
    if not (isinstance(parameter, str) and parameter.strip().startswith("song.")):
        dev, path = resolve_device(ctx, track, device)
    param, index = find_parameter(ctx, dev, parameter)
    ppath = param_path(path, index) or path_of(ctx, param)
    return set_parameter_value(param, value, bool(normalized), index, ppath)


def _entries(values):
    """Normalise ``values`` (dict or list) to ``[(spec, value, normalized|None)]``."""
    entries = []
    if isinstance(values, dict):
        for key, value in values.items():
            entries.append((key, value, None))
    elif isinstance(values, list):
        for position, item in enumerate(values):
            if not isinstance(item, dict) or "value" not in item:
                raise BridgeError("bad_args", "values[%d] must be an object with 'parameter' "
                                  "and 'value'" % position)
            spec = item.get("parameter", item.get("name", item.get("index", item.get("path"))))
            if spec is None:
                raise BridgeError("bad_args", "values[%d] needs 'parameter' (index, name or "
                                  "path)" % position)
            unknown = set(item) - set(("parameter", "name", "index", "path", "value",
                                       "normalized"))
            if unknown:
                raise BridgeError("bad_args", "values[%d]: unknown keys %s"
                                  % (position, ", ".join(sorted(unknown))))
            normalized = item.get("normalized")
            if normalized is not None and not isinstance(normalized, bool):
                raise BridgeError("bad_args", "values[%d].normalized must be a boolean"
                                  % position)
            entries.append((spec, item["value"], normalized))
    else:
        raise BridgeError("bad_args", "values must be an object {parameter: value} or a list "
                          "of {parameter, value, normalized?}")
    if not entries:
        raise BridgeError("bad_args", "values is empty")
    if len(entries) > 512:
        raise BridgeError("bad_args", "at most 512 parameters per call")
    return entries


@command("devices.set_parameters", mutating=True,
         doc="Set many parameters atomically (validated first, one undo step)")
def devices_set_parameters(ctx, values, track=None, device=None, normalized=False):
    """Set several parameters in one go.

    Args:
        values: ``{"Filter Freq": "2 kHz", "3": 0.5, "Osc Wave": "Saw"}``
            (keys = names or indices) or a list
            ``[{"parameter": <index|name|path>, "value": ..., "normalized"?: bool}]``
            — list entries with full LOM paths may target other devices.
        normalized: default for numeric values (per-entry ``normalized`` wins).
        track, device: the device for names/indices (see module docstring);
            may be omitted when every entry is a LOM path.

    Returns:
        {device?: {...}, count, parameters: [{index, name, value, display, ...}]}

    Gotchas:
        Every value is resolved and validated before anything is written; if
        Live still refuses one write, the earlier writes are rolled back.
        The whole call is one undo step.
    """
    entries = _entries(values)
    needs_device = any(not (isinstance(spec, str) and spec.strip().startswith("song."))
                       for spec, _v, _n in entries)
    dev, path = (None, None)
    if needs_device:
        dev, path = resolve_device(ctx, track, device)
    plan = []
    for spec, value, entry_normalized in entries:
        param, index = find_parameter(ctx, dev, spec)
        use_normalized = bool(normalized) if entry_normalized is None else entry_normalized
        target, how, clamped = resolve_value(param, value, use_normalized)
        if not compat.safe_getattr(param, "is_enabled", True):
            raise BridgeError("invalid_state", "%r is disabled (macro-mapped or controlled by "
                              "Max) — nothing was changed" % _name(param, "parameter"))
        ppath = param_path(path, index) if index is not None and dev is not None \
            else path_of(ctx, param)
        plan.append((param, index, ppath, target, how, clamped))
    done = []
    try:
        for param, _index, _path, target, _how, _clamped in plan:
            before = compat.safe_getattr(param, "value")
            write_parameter(param, target)
            done.append((param, before))
    except BridgeError:
        for param, before in reversed(done):
            try:
                param.value = before
            except Exception:
                ctx.log("devices.set_parameters: rollback of %s failed", _name(param))
        raise
    results = []
    for param, index, ppath, _target, how, clamped in plan:
        after = compat.safe_getattr(param, "value")
        entry = {"index": index, "name": _name(param), "value": num(after),
                 "display": display_of(param, after)}
        if index is None:
            entry["path"] = ppath
        if how in ("display", "item", "normalized"):
            entry["matched"] = how
        if clamped:
            entry["clamped"] = True
        results.append(prune(entry))
    result = {"count": len(results), "parameters": results}
    if dev is not None:
        result["device"] = device_brief(dev, path)
    return result


def _select_parameters(ctx, dev, parameters, skip_device_on=True):
    """``parameters`` (None = all) -> ``[(index, param)]``."""
    params = parameters_of(dev)
    if parameters is None:
        chosen = list(enumerate(params))
        if skip_device_on and chosen and _is_device_on(chosen[0][1]):
            chosen = chosen[1:]
        return chosen
    if not isinstance(parameters, list) or not parameters:
        raise BridgeError("bad_args", "parameters must be a non-empty list of indices/names")
    chosen = []
    for spec in parameters:
        param, index = find_parameter(ctx, dev, spec)
        if index is None:
            raise BridgeError("bad_args", "%r does not belong to %r" % (spec, _name(dev)))
        chosen.append((index, param))
    return chosen


def _is_device_on(param):
    return compat.safe_getattr(param, "original_name", _name(param)) == "Device On" \
        or _name(param) == "Device On"


@command("devices.reset_parameters", mutating=True,
         doc="Reset parameters to their default values")
def devices_reset_parameters(ctx, track=None, device=None, parameters=None):
    """Reset parameters to Live's default values.

    Args:
        track, device: see the module docstring.
        parameters: list of indices/names; omit to reset every parameter
            except "Device On".

    Returns:
        {device, reset: [{index, name, display}], skipped: [{index, name, reason}]}

    Gotchas:
        Live has no default for quantized parameters (switches, menus) —
        they are skipped and listed; set them explicitly.  Disabled
        (macro-mapped) parameters are skipped too.
    """
    dev, path = resolve_device(ctx, track, device)
    reset = []
    skipped = []
    for index, param in _select_parameters(ctx, dev, parameters):
        if compat.safe_getattr(param, "is_quantized", False):
            skipped.append({"index": index, "name": _name(param), "reason": "quantized"})
            continue
        if not compat.safe_getattr(param, "is_enabled", True):
            skipped.append({"index": index, "name": _name(param), "reason": "disabled"})
            continue
        default = compat.safe_getattr(param, "default_value")
        if default is None:
            skipped.append({"index": index, "name": _name(param), "reason": "no default"})
            continue
        target, _how, _clamped = resolve_value(param, float(default))
        write_parameter(param, target)
        reset.append({"index": index, "name": _name(param), "display": display_of(param)})
    return {"device": device_brief(dev, path), "reset": reset, "skipped": skipped}


@command("devices.randomize", mutating=True, doc="Randomize device parameters (optional seed)")
def devices_randomize(ctx, track=None, device=None, parameters=None, seed=None, amount=1.0,
                      include_quantized=True):
    """Randomize parameters.

    Args:
        track, device: see the module docstring.
        parameters: list of indices/names; omit for all (except "Device On",
            disabled/macro-mapped ones and, for racks, "Chain Selector").
        seed: integer for reproducible results (returned either way).
        amount: 1.0 = anywhere in the range; 0 < amount < 1 = move each value
            randomly by up to ``amount`` × range from where it is.
        include_quantized: also randomize switches/menus.

    Returns:
        {device, seed, changed, parameters: [{index, name, display}]}

    Gotchas:
        One undo step — undo restores everything.  For racks prefer
        racks.variations(action="randomize") which respects Live's
        macro-randomization exclusions.
    """
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) \
            or not 0.0 < float(amount) <= 1.0:
        raise BridgeError("bad_args", "amount must be a number in (0, 1]")
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
        raise BridgeError("bad_args", "seed must be an integer")
    dev, path = resolve_device(ctx, track, device)
    if seed is None:
        seed = random.randrange(1, 1 << 30)
    rng = random.Random(seed)
    explicit = parameters is not None
    changed = []
    for index, param in _select_parameters(ctx, dev, parameters):
        if not compat.safe_getattr(param, "is_enabled", True):
            continue
        quantized = bool(compat.safe_getattr(param, "is_quantized", False))
        if quantized and not include_quantized and not explicit:
            continue
        if not explicit and _name(param) == "Chain Selector" and is_rack(dev):
            continue
        minimum = float(compat.safe_getattr(param, "min", 0.0))
        maximum = float(compat.safe_getattr(param, "max", 1.0))
        span = maximum - minimum
        if span <= 0:
            continue
        if float(amount) >= 1.0:
            if quantized:
                target = float(rng.randint(int(round(minimum)), int(round(maximum))))
            else:
                target = rng.uniform(minimum, maximum)
        else:
            current = float(compat.safe_getattr(param, "value", minimum))
            target = current + rng.uniform(-1.0, 1.0) * float(amount) * span
        target, _how, _clamped = resolve_value(param, target)
        write_parameter(param, target)
        changed.append({"index": index, "name": _name(param), "display": display_of(param)})
    return {"device": device_brief(dev, path), "seed": seed, "changed": len(changed),
            "parameters": changed}


# ==========================================================================
# commands — device state and editing
# ==========================================================================

def device_on_parameter(device):
    params = parameters_of(device)
    for param in params[:1] + params:
        if _is_device_on(param):
            return param
    return None


def select_device(ctx, device, show=True):
    """Select ``device`` in Live (its track becomes the selected track)."""
    view = ctx.view
    track_obj = _track_of_device(device)
    if track_obj is not None:
        try:
            view.selected_track = track_obj
        except Exception as error:
            raise BridgeError("invalid_state", "cannot select the track: %s" % error)
    try:
        view.select_device(device)
    except Exception as error:
        raise BridgeError("invalid_state", "Live refused to select %r: %s"
                          % (_name(device), error))
    if show:
        app_view = compat.safe_getattr(ctx.app, "view")
        compat.safe_call(app_view, "show_view", "Detail/DeviceChain")


@command("devices.set_state", mutating=True,
         doc="Turn a device on/off, rename, collapse, select, A/B compare")
def devices_set_state(ctx, track=None, device=None, active=None, name=None, collapsed=None,
                      select=None, compare_b=None, show_chains=None, save_to_compare_slot=False):
    """Change a device's state; every argument is optional.

    Args:
        track, device: see the module docstring.
        active: true/false or "toggle" — flips "Device On" (``is_active`` is
            read-only in Live).
        name: new device name (shown in the title bar).
        collapsed: fold/unfold the device in the chain view.
        select: true = select the device (and its track) and show the device
            chain in the Detail view.
        compare_b: Live 12.3+ A/B compare — true loads slot B, false slot A
            (only when ``can_compare_ab``).
        show_chains: racks only — show/hide the chain list.
        save_to_compare_slot: true stores the device's CURRENT state in the
            A/B compare slot (``Device.save_preset_to_compare_ab_slot``), before
            any ``compare_b`` switch in the same call; ``compare_b`` then
            switches between the two slots.  ``invalid_state`` when the device
            cannot compare (``can_compare_ab`` false: plug-ins, older Live).

    Returns:
        {path, name, is_active, collapsed, selected, compare_b?, show_chains?,
         saved_to_compare_slot?: true}

    Gotchas:
        Live cannot save .adv/.adg device or rack presets (or plug-in .fxp)
        through the API — the A/B compare slot is the only snapshot it offers;
        the user saves presets from the device title bar.  The whole call is
        one undo step.
    """
    dev, path = resolve_device(ctx, track, device)
    get = compat.safe_getattr
    if active is not None:
        on = device_on_parameter(dev)
        if on is None:
            raise BridgeError("unsupported", "%r has no 'Device On' parameter" % _name(dev))
        word = active.strip().lower() if isinstance(active, str) else None
        if word == "toggle":
            target = 0.0 if float(get(on, "value", 1.0)) else 1.0
        elif isinstance(active, bool) or word in ("on", "off", "true", "false"):
            target = 1.0 if active is True or word in ("on", "true") else 0.0
        else:
            raise BridgeError("bad_args", "active must be true, false or \"toggle\"")
        write_parameter(on, target)
    if name is not None:
        if not isinstance(name, str) or not name.strip():
            raise BridgeError("bad_args", "name must be a non-empty string")
        try:
            dev.name = name
        except Exception as error:
            raise BridgeError("invalid_state", "cannot rename: %s" % error)
    view = get(dev, "view")
    if collapsed is not None:
        if not isinstance(collapsed, bool):
            raise BridgeError("bad_args", "collapsed must be a boolean")
        if view is None or not compat.has(view, "is_collapsed"):
            raise BridgeError("unsupported", "this device has no collapsible view")
        view.is_collapsed = collapsed
    if not isinstance(save_to_compare_slot, bool):
        raise BridgeError("bad_args", "save_to_compare_slot must be a boolean")
    if save_to_compare_slot:
        if not get(dev, "can_compare_ab", False):
            raise BridgeError("invalid_state", "%r has no A/B compare slot (can_compare_ab is "
                              "false: plug-ins and Live before 12.3) — nothing was saved"
                              % _name(dev))
        if not compat.has(dev, "save_preset_to_compare_ab_slot"):
            raise BridgeError("unsupported", "this Live version cannot save to the A/B slot")
        try:
            dev.save_preset_to_compare_ab_slot()
        except Exception as error:
            raise BridgeError("invalid_state", "saving to the A/B slot failed: %s" % error)
    if compare_b is not None:
        if not isinstance(compare_b, bool):
            raise BridgeError("bad_args", "compare_b must be a boolean")
        if not get(dev, "can_compare_ab", False):
            raise BridgeError("unsupported", "%r does not support A/B compare (Live 12.3+ "
                              "native devices only)" % _name(dev))
        try:
            dev.is_using_compare_preset_b = compare_b
        except Exception as error:
            raise BridgeError("invalid_state", "A/B compare failed: %s" % error)
    if show_chains is not None:
        if not isinstance(show_chains, bool):
            raise BridgeError("bad_args", "show_chains must be a boolean")
        if not is_rack(dev) or view is None or not compat.has(view, "is_showing_chain_devices"):
            raise BridgeError("unsupported", "show_chains only applies to racks")
        view.is_showing_chain_devices = show_chains
    if select is not None:
        if not isinstance(select, bool):
            raise BridgeError("bad_args", "select must be a boolean")
        if select:
            select_device(ctx, dev)
    track_obj = _track_of_device(dev)
    selected = get(get(track_obj, "view"), "selected_device") if track_obj is not None else None
    result = {
        "path": path, "name": _name(dev),
        "is_active": bool(get(dev, "is_active", True)),
        "collapsed": bool(get(view, "is_collapsed", False)) if view is not None else None,
        "selected": bool(selected is not None and selected == dev),
    }
    if get(dev, "can_compare_ab", False):
        result["compare_b"] = bool(get(dev, "is_using_compare_preset_b", False))
    if is_rack(dev) and view is not None and compat.has(view, "is_showing_chain_devices"):
        result["show_chains"] = bool(get(view, "is_showing_chain_devices", False))
    if save_to_compare_slot:
        result["saved_to_compare_slot"] = True
    return prune(result)


@command("devices.delete", mutating=True, doc="Delete a device from its track or chain")
def devices_delete(ctx, track=None, device=None):
    """Delete a device.

    Args:
        track, device: see the module docstring (a nested device is deleted
            from its rack chain).  ``device`` is required unless it is the
            selected device you mean.

    Returns:
        {deleted: {name, class_name, path}, host: <track/chain path>,
         devices: [remaining device names in order]}

    Gotchas:
        Paths/indices of the devices after it shift down by one.  Live's
        undo brings it back (the command is one undo step).
    """
    dev, path = resolve_device(ctx, track, device)
    host = host_of(dev)
    index = index_in(compat.safe_getattr(host, "devices", ()), dev)
    if index is None:
        raise BridgeError("invalid_state", "cannot locate %r in its chain" % _name(dev))
    brief = device_brief(dev, path)
    host_path = path_of(ctx, host)
    try:
        host.delete_device(index)
    except Exception as error:
        raise BridgeError("invalid_state", "Live refused to delete %r: %s" % (_name(dev), error))
    return {"deleted": brief, "host": host_path,
            "devices": [_name(d) for d in compat.safe_getattr(host, "devices", ()) or ()]}


#: Live 12.4.5 UI device name -> class_name, verified on the real Live by inserting every
#: device the browser lists.  ``insert_device`` wants the UI name (case-sensitive); the
#: class names are accepted as aliases.  Max for Live based devices (Drum Sampler, the DS
#: drums, LFO, Shaper, Envelope Follower, Align Delay, Expression Control, MIDI Monitor,
#: MPE Control, Note Echo, Envelope MIDI, Shaper MIDI) are NOT insertable — they are loaded
#: through the browser instead.
NATIVE_DEVICE_CLASSES = {
    "Analog": "UltraAnalog", "Collision": "Collision", "Drift": "Drift",
    "Drum Rack": "DrumGroupDevice", "Electric": "LoungeLizard",
    "External Instrument": "ProxyInstrumentDevice", "Impulse": "InstrumentImpulse",
    "Instrument Rack": "InstrumentGroupDevice", "Meld": "InstrumentMeld",
    "Operator": "Operator", "Sampler": "MultiSampler", "Simpler": "OriginalSimpler",
    "Tension": "StringStudio", "Wavetable": "InstrumentVector",
    "Arpeggiator": "MidiArpeggiator", "CC Control": "MidiCcControl", "Chord": "MidiChord",
    "MIDI Effect Rack": "MidiEffectGroupDevice", "Note Length": "MidiNoteLength",
    "Pitch": "MidiPitcher", "Random": "MidiRandom", "Scale": "MidiScale",
    "Velocity": "MidiVelocity",
    "Amp": "Amp", "Audio Effect Rack": "AudioEffectGroupDevice", "Auto Filter": "AutoFilter2",
    "Auto Pan-Tremolo": "AutoPan2", "Auto Shift": "AutoShift", "Beat Repeat": "BeatRepeat",
    "Cabinet": "Cabinet", "Channel EQ": "ChannelEq", "Chorus-Ensemble": "Chorus2",
    "Compressor": "Compressor2", "Corpus": "Corpus", "Delay": "Delay",
    "Drum Buss": "DrumBuss", "Dynamic Tube": "Tube", "Echo": "Echo", "EQ Eight": "Eq8",
    "EQ Three": "FilterEQ3", "Erosion": "Erosion2",
    "External Audio Effect": "ProxyAudioEffectDevice", "Filter Delay": "FilterDelay",
    "Gate": "Gate", "Glue Compressor": "GlueCompressor", "Grain Delay": "GrainDelay",
    "Hybrid Reverb": "Hybrid", "Limiter": "Limiter", "Looper": "Looper",
    "Multiband Dynamics": "MultibandDynamics", "Overdrive": "Overdrive", "Pedal": "Pedal",
    "Phaser-Flanger": "PhaserNew", "Redux": "Redux2", "Resonators": "Resonator",
    "Reverb": "Reverb", "Roar": "Roar", "Saturator": "Saturator", "Shifter": "Shifter",
    "Spectral Resonator": "Transmute", "Spectral Time": "Spectral",
    "Spectrum": "SpectrumAnalyzer", "Tuner": "Tuner", "Utility": "StereoGain",
    "Vinyl Distortion": "Vinyl", "Vocoder": "Vocoder",
}

#: Extra spellings people use for native devices (normalised with ``_norm_key``).
_DEVICE_NAME_ALIASES = {
    "eq8": "EQ Eight", "eq3": "EQ Three", "glue": "Glue Compressor", "lounge lizard":
    "Electric", "string studio": "Tension", "chorus": "Chorus-Ensemble", "phaser":
    "Phaser-Flanger", "flanger": "Phaser-Flanger", "auto pan": "Auto Pan-Tremolo",
    "autopan": "Auto Pan-Tremolo", "tremolo": "Auto Pan-Tremolo", "arp": "Arpeggiator",
    "vinyl": "Vinyl Distortion", "tube": "Dynamic Tube", "multiband": "Multiband Dynamics",
    "ott": "Multiband Dynamics", "gain": "Utility", "hybrid": "Hybrid Reverb",
}

_BROWSER_DEVICE_ROOTS = ("instruments", "audio_effects", "midi_effects")


def _is_unknown_device_error(error):
    text = str(error).lower()
    return "not found" in text or "unknown device" in text


def _browser_devices(ctx):
    """``[(root, name)]`` of the device items at the top of the device roots."""
    found = []
    browser = compat.safe_getattr(ctx, "browser")
    for root in _BROWSER_DEVICE_ROOTS:
        item = compat.safe_getattr(browser, root)
        for child in compat.safe_getattr(item, "children", ()) or ():
            if compat.safe_getattr(child, "is_device", False):
                name = compat.safe_getattr(child, "name", "")
                if isinstance(name, str) and name:
                    found.append((root, name))
    return found


def canonical_device_name(ctx, name):
    """Map user text to the UI name ``insert_device`` wants.

    Returns ``(ui_name or None, browser_path or None)`` — ``browser_path``
    ("audio_effects/LFO") is set when the name only exists as a browser device (Max for
    Live based, not insertable).
    """
    key = _norm_key(name)
    for ui_name, class_name in NATIVE_DEVICE_CLASSES.items():
        if _norm_key(ui_name) == key or _norm_key(class_name) == key:
            return ui_name, None
    alias = _DEVICE_NAME_ALIASES.get(re.sub(r"[\s_\-]+", " ", name.strip().lower()))
    if alias is None:
        alias = dict((_norm_key(k), v) for k, v in _DEVICE_NAME_ALIASES.items()).get(key)
    if alias:
        return alias, None
    for root, browser_name in _browser_devices(ctx):
        if _norm_key(browser_name) == key:
            if browser_name in NATIVE_DEVICE_CLASSES:
                return browser_name, None
            return None, "%s/%s" % (root, browser_name)
    return None, None


def _insert_native(host, name, index):
    """``host.insert_device`` -> device (``None`` when Live returns nothing)."""
    return host.insert_device(name, index)


@command("devices.insert", mutating=True,
         doc="Insert a Live device by name (native via insert_device, Max devices via the browser)")
def devices_insert(ctx, name, track=None, chain=None, index=-1, select=False):
    """Insert a built-in Live device by name.

    Args:
        name: the device's name as in Live's browser — "EQ Eight", "Compressor",
            "Utility", "Reverb", "Auto Filter", "Operator", "Wavetable", "Drift",
            "Simpler", "Drum Rack", "Instrument Rack", "Audio Effect Rack",
            "Arpeggiator" ...  Case/spacing do not matter and class names work too
            ("eq eight", "Eq8", "StereoGain", "Compressor2").
        track: target track (default: selected track) — ignored with ``chain``.
        chain: LOM path of a rack chain to insert into instead.
        index: position in the chain, -1 = end.
        select: also select the new device.

    Returns:
        The new device's summary (with ``path``); ``via`` is "insert_device" or
        "browser" (Max for Live based devices), ``requested`` echoes a name that
        was translated.

    Gotchas:
        Live 12.3+.  ``insert_device`` only knows native devices; the Max for Live
        based ones (LFO, Shaper, Envelope Follower, Drum Sampler, DS Kick ...,
        Note Echo, MIDI Monitor ...) are loaded through the browser instead — that
        works only at the end of a track (not into a chain / at an index).  No
        plug-ins or presets (use the browser tools).  Live enforces MIDI effects →
        instrument → audio effects and one instrument per chain ("Device chains
        cannot have more than one instrument each"); audio tracks take audio
        effects only — refusals come back as ``invalid_state`` with Live's reason.
    """
    if not isinstance(name, str) or not name.strip():
        raise BridgeError("bad_args", "name must be a device name such as 'EQ Eight'")
    if isinstance(index, bool) or not isinstance(index, int) or index < -1:
        raise BridgeError("bad_args", "index must be -1 (end) or a position >= 0")
    host, host_path = resolve_container(ctx, track, chain)
    if not compat.has(host, "insert_device"):
        raise BridgeError("unsupported", "insert_device needs Live 12.3+; load the device "
                          "through the browser tools instead")
    count = len(compat.safe_getattr(host, "devices", ()) or ())
    if index > count:
        raise BridgeError("bad_args", "index %d is past the end (%d devices)" % (index, count))
    requested = name.strip()
    ui_name = requested
    via = "insert_device"
    new_device = None
    try:
        new_device = _insert_native(host, ui_name, index)
    except Exception as error:
        if not _is_unknown_device_error(error):
            raise BridgeError("invalid_state", "Live refused to insert %r at %d on %s: %s"
                              % (ui_name, index, host_path, error))
        canonical, browser_path = canonical_device_name(ctx, requested)
        if canonical is not None and canonical != requested:
            ui_name = canonical
            try:
                new_device = _insert_native(host, ui_name, index)
            except Exception as second:
                raise BridgeError("invalid_state", "Live refused to insert %r at %d on %s: %s"
                                  % (ui_name, index, host_path, second))
        elif browser_path is not None:
            return _insert_via_browser(ctx, host, host_path, browser_path, chain, index,
                                       select)
        else:
            raise BridgeError("not_found", "Live has no device called %r (%s). Native "
                              "devices include: %s" % (requested, error,
                                                       _close_device_names(ctx, requested)))
    devices = list(compat.safe_getattr(host, "devices", ()) or ())
    if new_device is None or not is_device(new_device):
        position = count if index == -1 else index
        new_device = devices[position] if 0 <= position < len(devices) else None
    if new_device is None:
        return {"inserted": ui_name, "host": host_path, "via": via}
    position = index_in(devices, new_device)
    if select:
        select_device(ctx, new_device)
    summary = ctx.summarize(new_device, "summary")
    summary["path"] = summary.get("path") or "%s.devices[%s]" % (host_path, position)
    summary["via"] = via
    if ui_name != requested:
        summary["requested"] = requested
    return summary


def _close_device_names(ctx, text, limit=8):
    names = sorted(set(list(NATIVE_DEVICE_CLASSES) + [n for _r, n in _browser_devices(ctx)]))
    close = difflib.get_close_matches(text, names, n=limit, cutoff=0.4)
    lowered = text.strip().lower()
    close += [n for n in names if lowered and lowered in n.lower() and n not in close]
    return ", ".join(repr(n) for n in (close or names[:limit])[:limit])


def _insert_via_browser(ctx, host, host_path, browser_path, chain, index, select):
    """Max for Live based devices: load the browser item onto the track (end only)."""
    from . import browser as browser_handlers
    name = browser_path.split("/", 1)[-1]
    if chain is not None or index != -1:
        raise BridgeError("unsupported", "%r is a Max for Live device — Live's insert_device "
                          "cannot create it, and the browser can only add it at the end of a "
                          "track (not into a chain / at an index). Use browser.load(path=%r)"
                          % (name, browser_path))
    browser = browser_handlers.require_browser(ctx)
    entry = browser_handlers.find_by_path(browser, browser_path)
    changes = browser_handlers.load_entry(ctx, browser, entry, host)
    inserted = changes.get("inserted") or []
    if not inserted:
        raise BridgeError("invalid_state", "Live did not add %r to %s (%s)"
                          % (name, host_path, "; ".join(changes.get("notes", [])) or
                             "no change"))
    summary = dict(inserted[0])
    summary["via"] = "browser"
    if changes.get("new_tracks"):
        summary["new_tracks"] = changes["new_tracks"]
    if select:
        device = ctx.resolve(summary["path"]) if summary.get("path") else None
        if device is not None:
            select_device(ctx, device)
    return summary


@command("devices.duplicate", mutating=True, doc="Duplicate a device (copy lands right after it)")
def devices_duplicate(ctx, track=None, device=None):
    """Duplicate a device in place (Live 12 ``duplicate_device``).

    Args:
        track, device: see the module docstring.

    Returns:
        The copy's summary (it sits at the original index + 1).
    """
    dev, _path = resolve_device(ctx, track, device)
    host = host_of(dev)
    if not compat.has(host, "duplicate_device"):
        raise BridgeError("unsupported", "duplicate_device needs Live 12")
    index = index_in(compat.safe_getattr(host, "devices", ()), dev)
    if index is None:
        raise BridgeError("invalid_state", "cannot locate %r in its chain" % _name(dev))
    try:
        host.duplicate_device(index)
    except Exception as error:
        raise BridgeError("invalid_state", "Live refused to duplicate %r: %s"
                          % (_name(dev), error))
    devices = list(compat.safe_getattr(host, "devices", ()) or ())
    if index + 1 >= len(devices):
        raise BridgeError("internal", "the duplicate did not appear")
    return ctx.summarize(devices[index + 1], "summary")


@command("devices.move", mutating=True, doc="Move a device to another position/track/chain")
def devices_move(ctx, track=None, device=None, target_track=None, target_chain=None,
                 position=-1):
    """Move a device (``song.move_device``).

    Args:
        track, device: the device to move (see the module docstring).
        target_track: destination track (default: the device's own track /
            chain).
        target_chain: LOM path of a rack chain as destination instead.
        position: index in the destination chain; -1 = end, 0 = first.

    Returns:
        {moved: {name, path}, position, host}

    Gotchas:
        Live picks the nearest legal position when the requested one breaks
        the MIDI effect → instrument → audio effect order (e.g. an audio effect
        moved to position 0 of a track with an instrument lands at 1 — the
        answer reports the real ``position``), and refuses device types the
        destination cannot hold ("Couldn't move device." → ``invalid_state``).
    """
    if isinstance(position, bool) or not isinstance(position, int) or position < -1:
        raise BridgeError("bad_args", "position must be -1 (end) or an index >= 0")
    dev, _path = resolve_device(ctx, track, device)
    if target_track is None and target_chain is None:
        target = host_of(dev)
        target_path = path_of(ctx, target)
    else:
        target, target_path = resolve_container(ctx, target_track, target_chain)
    count = len(compat.safe_getattr(target, "devices", ()) or ())
    real = count if position == -1 else min(position, count)
    song = ctx.song
    if not compat.has(song, "move_device"):
        raise BridgeError("unsupported", "song.move_device is not available in this Live")
    try:
        new_index = song.move_device(dev, target, real)
    except Exception as error:
        hint = ""
        dev_type = int(compat.safe_getattr(dev, "type", 0) or 0)
        if dev_type == DEVICE_TYPES["instrument"]:
            hint = " (an instrument needs a MIDI track / instrument chain without another " \
                   "instrument)"
        elif dev_type == DEVICE_TYPES["midi_effect"]:
            hint = " (MIDI effects need a MIDI track or a MIDI/instrument rack chain)"
        raise BridgeError("invalid_state", "Live refused to move %r to %s: %s%s"
                          % (_name(dev), target_path, error, hint))
    devices = list(compat.safe_getattr(target, "devices", ()) or ())
    found = index_in(devices, dev)
    if found is None and isinstance(new_index, int) and 0 <= new_index < len(devices):
        found = new_index
    moved = devices[found] if found is not None else dev
    return {"moved": {"name": _name(moved), "path": "%s.devices[%d]" % (target_path, found)
                      if found is not None else None},
            "position": found, "host": target_path}


@command("devices.find", doc="Find devices anywhere in the set by name, class or kind")
def devices_find(ctx, query=None, class_name=None, kind=None, type=None, include_nested=True,
                 include_returns=True, limit=100):
    """Search every track (and rack chains) for devices.

    Args:
        query: case-insensitive substring of the device name, class name or
            browser name ("reverb", "serum", "eq").
        class_name: exact Live class ("OriginalSimpler", "Eq8", "PluginDevice").
        kind: "plugin", "max", "rack", "drum_rack", "simpler", "sampler" or
            "native".
        type: "instrument", "audio_effect" or "midi_effect".
        include_nested: look inside rack chains.
        include_returns: also search return tracks and the master track.
        limit: max results (1..1000).

    Returns:
        {count, truncated?, devices: [{path, track, name, class_name, type,
         kind, is_active, depth}]}
    """
    if query is not None and not isinstance(query, str):
        raise BridgeError("bad_args", "query must be a string")
    if kind is not None and kind not in DEVICE_KINDS:
        raise BridgeError("bad_args", "kind must be one of %s" % ", ".join(DEVICE_KINDS))
    if type is not None and type not in DEVICE_TYPES:
        raise BridgeError("bad_args", "type must be one of %s" % ", ".join(DEVICE_TYPES))
    _offset, limit = check_paging(0, limit)
    needle = query.strip().lower() if query else None
    found = []
    truncated = False
    for track_obj, track_path in iter_tracks(ctx, include_returns, include_returns):
        for dev, path, depth in iter_devices(track_obj, track_path, bool(include_nested)):
            get = compat.safe_getattr
            if needle:
                haystack = " ".join(str(get(dev, attr, "") or "") for attr in
                                    ("name", "class_name", "class_display_name")).lower()
                if needle not in haystack:
                    continue
            if class_name and str(get(dev, "class_name", "")).lower() != class_name.lower():
                continue
            dev_kind = device_kind(dev)
            if kind and dev_kind != kind and not (kind == "rack" and dev_kind == "drum_rack"):
                continue
            if type and int(get(dev, "type", 0) or 0) != DEVICE_TYPES[type]:
                continue
            if len(found) >= limit:
                truncated = True
                break
            found.append({
                "path": path, "track": _name(track_obj), "name": _name(dev),
                "class_name": get(dev, "class_name"),
                "type": serialize.DEVICE_TYPES.get(int(get(dev, "type", 0) or 0), "undefined"),
                "kind": dev_kind, "is_active": bool(get(dev, "is_active", True)),
                "depth": depth,
            })
        if truncated:
            break
    result = {"count": len(found), "devices": found}
    if truncated:
        result["truncated"] = True
    return result


# ==========================================================================
# Simpler
# ==========================================================================

def resolve_simpler(ctx, track, device):
    dev, path = resolve_device(ctx, track, device)
    if not is_simpler(dev):
        hint = " (Sampler exposes only its parameters — use devices.parameters)" \
            if compat.safe_getattr(dev, "class_name", "") == "MultiSampler" else ""
        raise BridgeError("bad_args", "%r (%s) is not a Simpler%s"
                          % (_name(dev), compat.safe_getattr(dev, "class_name", "?"), hint))
    return dev, path


def _sample_info(sample, max_slices):
    get = compat.safe_getattr
    rate = get(sample, "sample_rate") or 0
    length = get(sample, "length")
    slices = list(get(sample, "slices", ()) or ())
    file_path = get(sample, "file_path")
    info = {
        "file_path": file_path,
        "file_name": os.path.basename(str(file_path).replace("\\", "/")) if file_path else None,
        "length": length,
        "sample_rate": rate,
        "seconds": round(float(length) / float(rate), 4) if rate and length else None,
        "start_marker": get(sample, "start_marker"),
        "end_marker": get(sample, "end_marker"),
        "gain": num(get(sample, "gain")),
        "gain_display": None,
        "warping": get(sample, "warping"),
        "warp_mode": WARP_MODES.get(get(sample, "warp_mode"), get(sample, "warp_mode")),
        "slicing_style": SLICING_STYLES.get(get(sample, "slicing_style"),
                                            get(sample, "slicing_style")),
        "slicing_sensitivity": num(get(sample, "slicing_sensitivity"), 4),
        "slicing_beat_division": SLICING_BEAT_DIVISIONS.get(
            get(sample, "slicing_beat_division"), get(sample, "slicing_beat_division")),
        "slicing_region_count": get(sample, "slicing_region_count"),
        "slice_count": len(slices),
        "slices": [int(s) for s in slices[:max_slices]],
    }
    ok, gain_text = compat.safe_call(sample, "gain_display_string")
    if ok and gain_text is not None:
        info["gain_display"] = str(gain_text)
    if len(slices) > max_slices:
        info["slices_truncated"] = len(slices) - max_slices
    return prune(info)


def simpler_info(ctx, dev, path, max_slices=64):
    get = compat.safe_getattr
    view = get(dev, "view")
    sample = get(dev, "sample")
    data = {
        "device": device_brief(dev, path),
        "playback_mode": PLAYBACK_MODES.get(get(dev, "playback_mode"), get(dev, "playback_mode")),
        "slicing_playback_mode": SLICING_PLAYBACK_MODES.get(get(dev, "slicing_playback_mode"),
                                                            get(dev, "slicing_playback_mode")),
        "voices": get(dev, "voices"),
        "retrigger": get(dev, "retrigger"),
        "pad_slicing": get(dev, "pad_slicing"),
        "multi_sample_mode": get(dev, "multi_sample_mode"),
        "pitch_bend_range": get(dev, "pitch_bend_range"),
        "can_warp_as": get(dev, "can_warp_as"),
        "can_warp_double": get(dev, "can_warp_double"),
        "can_warp_half": get(dev, "can_warp_half"),
        "empty": sample is None,
        "sample": _sample_info(sample, max_slices) if sample is not None else None,
    }
    if view is not None:
        data["selected_slice"] = get(view, "selected_slice")
    return prune(data)


@command("simpler.get", doc="Simpler: sample file, length, markers, warp, slices, modes")
def simpler_get(ctx, track=None, device=None, max_slices=64):
    """Inspect a Simpler.

    Args:
        track, device: see the module docstring (a Simpler inside a drum pad:
            pass its LOM path, e.g. from racks.drum_pads).
        max_slices: how many slice positions to return (0..4096).

    Returns:
        {device, playback_mode ("classic"|"one_shot"|"slicing"),
         slicing_playback_mode ("mono"|"poly"|"thru"), voices, retrigger,
         pad_slicing, multi_sample_mode, can_warp_as/double/half, empty,
         sample: {file_path, file_name, length (frames), sample_rate, seconds,
         start_marker, end_marker (frames), gain, gain_display, warping,
         warp_mode, slicing_style, slicing_sensitivity, slicing_beat_division,
         slicing_region_count, slice_count, slices (frames)}, selected_slice}
    """
    if isinstance(max_slices, bool) or not isinstance(max_slices, int) \
            or not 0 <= max_slices <= 4096:
        raise BridgeError("bad_args", "max_slices must be an integer 0..4096")
    dev, path = resolve_simpler(ctx, track, device)
    return simpler_info(ctx, dev, path, max_slices)


def _set_attr(obj, attr, value, what):
    try:
        setattr(obj, attr, value)
    except Exception as error:
        raise BridgeError("invalid_state", "cannot set %s to %r: %s" % (what, value, error))


@command("simpler.set", mutating=True,
         doc="Simpler: playback/slicing modes, voices, sample warp/slicing settings")
def simpler_set(ctx, track=None, device=None, playback_mode=None, slicing_playback_mode=None,
                voices=None, retrigger=None, pad_slicing=None, warping=None, warp_mode=None,
                slicing_style=None, slicing_sensitivity=None, slicing_beat_division=None,
                slicing_region_count=None, start_marker=None, end_marker=None, gain=None):
    """Change Simpler settings; every argument is optional.

    Args:
        track, device: see the module docstring.
        playback_mode: "classic", "one_shot" or "slicing" (or 0/1/2).
        slicing_playback_mode: "mono", "poly" or "thru".
        voices: polyphony (Live accepts 1..32 in its own steps).
        retrigger, pad_slicing: booleans.
        warping: sample warp on/off; warp_mode: "beats", "tones", "texture",
            "repitch", "complex", "complex_pro".
        slicing_style: "transient", "beat", "region" or "manual".
        slicing_sensitivity: 0..1 (transient mode).
        slicing_beat_division: "1/16", "1/16T", "1/8", "1/8T", "1/4", "1/4T",
            "1/2", "1/2T", "1 bar", "2 bars", "4 bars" (beat mode).
        slicing_region_count: number of regions (region mode).
        start_marker, end_marker: sample frames.
        gain: sample gain 0..1 (internal value).

    Returns:
        The same shape as simpler.get after the change.

    Gotchas:
        Sample settings need a loaded sample (``invalid_state`` otherwise).
    """
    dev, path = resolve_simpler(ctx, track, device)
    if playback_mode is not None:
        _set_attr(dev, "playback_mode", enum_value(playback_mode, PLAYBACK_MODES,
                                                   "playback_mode"), "playback_mode")
    if slicing_playback_mode is not None:
        _set_attr(dev, "slicing_playback_mode",
                  enum_value(slicing_playback_mode, SLICING_PLAYBACK_MODES,
                             "slicing_playback_mode"), "slicing_playback_mode")
    for attr, value in (("voices", voices), ("retrigger", retrigger),
                        ("pad_slicing", pad_slicing)):
        if value is None:
            continue
        if attr == "voices":
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise BridgeError("bad_args", "voices must be a positive integer")
        elif not isinstance(value, bool):
            raise BridgeError("bad_args", "%s must be a boolean" % attr)
        _set_attr(dev, attr, value, attr)
    sample_args = (warping, warp_mode, slicing_style, slicing_sensitivity,
                   slicing_beat_division, slicing_region_count, start_marker, end_marker, gain)
    if any(v is not None for v in sample_args):
        sample = compat.safe_getattr(dev, "sample")
        if sample is None:
            raise BridgeError("invalid_state", "%r is empty — load a sample first "
                              "(simpler.action replace_sample)" % _name(dev))
        if warping is not None:
            if not isinstance(warping, bool):
                raise BridgeError("bad_args", "warping must be a boolean")
            _set_attr(sample, "warping", warping, "warping")
        if warp_mode is not None:
            _set_attr(sample, "warp_mode", enum_value(warp_mode, WARP_MODES, "warp_mode"),
                      "warp_mode")
        if slicing_style is not None:
            _set_attr(sample, "slicing_style",
                      enum_value(slicing_style, SLICING_STYLES, "slicing_style"),
                      "slicing_style")
        if slicing_sensitivity is not None:
            if isinstance(slicing_sensitivity, bool) \
                    or not isinstance(slicing_sensitivity, (int, float)) \
                    or not 0.0 <= float(slicing_sensitivity) <= 1.0:
                raise BridgeError("bad_args", "slicing_sensitivity must be 0..1")
            _set_attr(sample, "slicing_sensitivity", float(slicing_sensitivity),
                      "slicing_sensitivity")
        if slicing_beat_division is not None:
            _set_attr(sample, "slicing_beat_division",
                      enum_value(slicing_beat_division, SLICING_BEAT_DIVISIONS,
                                 "slicing_beat_division", SLICING_BEAT_DIVISION_NAMES),
                      "slicing_beat_division")
        for attr, value in (("slicing_region_count", slicing_region_count),
                            ("start_marker", start_marker), ("end_marker", end_marker)):
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise BridgeError("bad_args", "%s must be an integer >= 0" % attr)
            _set_attr(sample, attr, value, attr)
        if gain is not None:
            if isinstance(gain, bool) or not isinstance(gain, (int, float)):
                raise BridgeError("bad_args", "gain must be a number")
            _set_attr(sample, "gain", float(gain), "gain")
    return simpler_info(ctx, dev, path)


SIMPLER_ACTIONS = ("crop", "reverse", "warp_as", "warp_double", "warp_half",
                   "guess_playback_length", "insert_slices", "remove_slices", "move_slice",
                   "clear_slices", "reset_slices", "replace_sample", "to_drum_rack")


def _to_frames(sample, value, unit):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BridgeError("bad_args", "slice positions must be numbers")
    if unit == "frames":
        return int(round(float(value)))
    if unit == "seconds":
        rate = compat.safe_getattr(sample, "sample_rate") or 0
        if not rate:
            raise BridgeError("invalid_state", "sample rate unknown")
        return int(round(float(value) * float(rate)))
    try:
        return int(round(float(sample.beat_to_sample_time(float(value)))))
    except Exception as error:
        raise BridgeError("invalid_state", "beats need a warped sample: %s" % error)


def _remove_slices(sample, frames):
    """Remove the slice nearest to each frame (Live's ``remove_slice`` silently ignores
    a frame that is not exactly a slice).  A slice counts as "that one" within 10 ms
    (or 1 % of the sample when the rate is unknown).  Returns ``(removed, missing)``."""
    rate = float(compat.safe_getattr(sample, "sample_rate", 0) or 0)
    length = float(compat.safe_getattr(sample, "length", 0) or 0)
    tolerance = rate * 0.01 if rate else max(1.0, length * 0.01)
    removed = []
    missing = []
    for frame in frames:
        current = [int(x) for x in (compat.safe_getattr(sample, "slices", ()) or ())]
        if not current:
            missing.append(frame)
            continue
        nearest = min(current, key=lambda x: abs(x - frame))
        if abs(nearest - frame) > tolerance:
            missing.append(frame)
            continue
        sample.remove_slice(nearest)
        removed.append(nearest)
    return removed, missing


@command("simpler.action", mutating=True,
         doc="Simpler: crop, reverse, warp_as, slices, replace_sample ...")
def simpler_action(ctx, action, track=None, device=None, beats=None, slices=None,
                   unit="frames", file_path=None, old_time=None, new_time=None):
    """Run one Simpler operation.

    Args:
        action: one of
            "crop" (keep start..end marker), "reverse", "warp_as" (``beats``
            = length of the start..end region; default = Live's guess),
            "warp_double", "warp_half", "guess_playback_length" (read-only
            estimate in beats), "insert_slices" / "remove_slices" (``slices``
            = list of positions), "move_slice" (``old_time`` -> ``new_time``),
            "clear_slices" (manual slices), "reset_slices",
            (slice edits apply to ``slicing_style="manual"``; Live keeps a moved
            slice between its neighbours, so ``move_slice`` returns where it
            really landed; ``remove_slices`` removes the slice nearest to each
            position within 10 ms and lists the others as ``not_found``),
            "replace_sample" (``file_path`` = absolute path to an audio file),
            "to_drum_rack" (``Live.Conversions.sliced_simpler_to_drum_rack``,
            Live 12: replaces a Simpler in slicing mode by a Drum Rack with one
            pad per slice from C1 up — set playback_mode="slicing" first).
        track, device: see the module docstring.
        beats: for warp_as.
        slices: positions for insert/remove.
        unit: "frames" (default), "seconds" or "beats" (beats need warping)
            for slices/old_time/new_time.
        file_path: for replace_sample; Windows and macOS paths both work.

    Returns:
        {action, result?, simpler: <simpler.get shape>} — to_drum_rack:
        {action, via, rack: {path, name, class_name, pads}} (the Simpler is gone).

    Gotchas:
        Everything except replace_sample raises ``invalid_state`` on an empty
        Simpler.  Slice edits only show in slicing mode.
    """
    if action not in SIMPLER_ACTIONS:
        raise BridgeError("bad_args", "action must be one of %s" % ", ".join(SIMPLER_ACTIONS))
    if unit not in ("frames", "seconds", "beats"):
        raise BridgeError("bad_args", "unit must be 'frames', 'seconds' or 'beats'")
    dev, path = resolve_simpler(ctx, track, device)
    sample = compat.safe_getattr(dev, "sample")
    result = None
    if action == "to_drum_rack":
        return _simpler_to_drum_rack(ctx, dev, path, sample)
    if action == "replace_sample":
        if not isinstance(file_path, str) or not file_path.strip():
            raise BridgeError("bad_args", "replace_sample needs file_path")
        if not compat.has(dev, "replace_sample"):
            raise BridgeError("unsupported", "replace_sample is not available in this Live")
        # The same checks as samples.import / clips.create: quoted Explorer paths,
        # file:// URLs, ~, %VAR% / $VAR, a wrong-OS path explained, must exist.
        from . import samples as samples_handlers  # lazy: samples imports browser
        path_text = samples_handlers.require_file(file_path)
        try:
            dev.replace_sample(path_text)
        except Exception as error:
            raise BridgeError("invalid_state", "Live could not load %r: %s" % (path_text, error))
    else:
        if sample is None:
            raise BridgeError("invalid_state", "%r is empty — load a sample first "
                              "(action replace_sample)" % _name(dev))
        try:
            if action in ("crop", "reverse", "warp_double", "warp_half"):
                getattr(dev, action)()
            elif action == "guess_playback_length":
                result = num(dev.guess_playback_length(), 4)
            elif action == "warp_as":
                if beats is None:
                    beats = float(dev.guess_playback_length())
                if isinstance(beats, bool) or not isinstance(beats, (int, float)) \
                        or float(beats) <= 0:
                    raise BridgeError("bad_args", "beats must be a positive number")
                dev.warp_as(float(beats))
                result = num(beats, 4)
            elif action == "insert_slices":
                if not isinstance(slices, list) or not slices:
                    raise BridgeError("bad_args", "%s needs slices (a list of positions)" % action)
                frames = [_to_frames(sample, s, unit) for s in slices]
                for frame in frames:
                    sample.insert_slice(frame)
                result = frames
            elif action == "remove_slices":
                if not isinstance(slices, list) or not slices:
                    raise BridgeError("bad_args", "%s needs slices (a list of positions)" % action)
                frames = [_to_frames(sample, s, unit) for s in slices]
                removed, missing = _remove_slices(sample, frames)
                result = {"removed": removed}
                if missing:
                    result["not_found"] = missing
            elif action == "move_slice":
                if old_time is None or new_time is None:
                    raise BridgeError("bad_args", "move_slice needs old_time and new_time")
                result = sample.move_slice(_to_frames(sample, old_time, unit),
                                           _to_frames(sample, new_time, unit))
            elif action == "clear_slices":
                sample.clear_slices()
            elif action == "reset_slices":
                sample.reset_slices()
        except BridgeError:
            raise
        except Exception as error:
            raise BridgeError("invalid_state", "%s failed: %s" % (action, error))
    data = {"action": action, "simpler": simpler_info(ctx, dev, path)}
    if result is not None:
        data["result"] = result
    return data


def _simpler_to_drum_rack(ctx, dev, path, sample):
    """``simpler.action to_drum_rack``: Live.Conversions.sliced_simpler_to_drum_rack."""
    get = compat.safe_getattr
    if sample is None:
        raise BridgeError("invalid_state", "%r is empty — load a sample first" % _name(dev))
    if get(dev, "playback_mode") != 2:
        raise BridgeError("invalid_state", "%r is not in slicing mode — set "
                          "playback_mode='slicing' first (simpler.set)" % _name(dev))
    func = compat.live_enum("Conversions.sliced_simpler_to_drum_rack")
    if func is None or not callable(func):
        raise BridgeError("unsupported", "Live.Conversions.sliced_simpler_to_drum_rack needs "
                          "Live 12")
    host = get(dev, "canonical_parent")
    position = index_in(get(host, "devices", ()) or (), dev) if host is not None else None
    try:
        func(ctx.song, dev)
    except Exception as error:
        raise BridgeError("invalid_state", "sliced_simpler_to_drum_rack failed: %s" % error)
    data = {"action": "to_drum_rack", "via": "Live.Conversions.sliced_simpler_to_drum_rack"}
    devices = list(get(host, "devices", ()) or ()) if host is not None else []
    candidates = [(i, d) for i, d in enumerate(devices) if is_drum_rack(d)]
    if position is not None:
        candidates.sort(key=lambda item: abs(item[0] - position))
    if candidates:
        i, rack = candidates[0]
        base = path.rsplit(".devices[", 1)[0] if ".devices[" in path else None
        rack_path = "%s.devices[%d]" % (base, i) if base else path_of(ctx, rack)
        data["rack"] = dict(device_brief(rack, rack_path),
                            pads=len([c for c in get(rack, "chains", ()) or ()]))
    return data


# ==========================================================================
# class-specific properties and actions (Wavetable, Drift, Meld, Hybrid Reverb,
# Eq8, Looper, Roar, Shifter, Spectral Resonator, CC Control, Max devices ...)
# ==========================================================================

#: Properties that have their own curated commands (or are LOM collections).
_PROPERTY_SKIP = frozenset((
    "parameters", "chains", "return_chains", "drum_pads", "visible_drum_pads", "sample",
    "view", "canonical_parent", "presets", "audio_inputs", "audio_outputs", "midi_inputs",
    "midi_outputs", "available_input_routing_types", "available_input_routing_channels",
    "input_routing_type", "input_routing_channel"))

#: Choice lists that do not follow the ``<name>_list`` convention.
_CHOICE_LISTS = {
    "oscillator_1_wavetable_category": "oscillator_wavetable_categories",
    "oscillator_2_wavetable_category": "oscillator_wavetable_categories",
    "oscillator_1_wavetable_index": "oscillator_1_wavetables",
    "oscillator_2_wavetable_index": "oscillator_2_wavetables",
}

_MAX_CHOICES = 256
_METHOD_SKIP = frozenset(("store_chosen_bank",))

#: Properties Live 12.4.5 answers with a plain int (or bool) although an enum exists for
#: them: (Python class name, property) -> ``Live.<enum>`` (verified on the running Live).
_ENUM_PROPERTIES = {
    ("WavetableDevice", "filter_routing"): "WavetableDevice.FilterRouting",
    ("WavetableDevice", "mono_poly"): "WavetableDevice.Voicing",
    ("WavetableDevice", "oscillator_1_effect_mode"): "WavetableDevice.EffectMode",
    ("WavetableDevice", "oscillator_2_effect_mode"): "WavetableDevice.EffectMode",
    ("WavetableDevice", "poly_voices"): "WavetableDevice.VoiceCount",
    ("WavetableDevice", "unison_mode"): "WavetableDevice.UnisonMode",
    ("Eq8Device", "edit_mode"): "Eq8Device.EditMode",
    ("Eq8Device", "global_mode"): "Eq8Device.GlobalMode",
    ("SimplerDevice", "playback_mode"): "SimplerDevice.PlaybackMode",
    ("SimplerDevice", "slicing_playback_mode"): "SimplerDevice.SlicingPlaybackMode",
    ("Sample", "warp_mode"): "Clip.WarpMode",
    ("Sample", "slicing_style"): "Sample.SlicingStyle",
    ("Sample", "slicing_beat_division"): "Sample.SlicingBeatDivision",
    ("Sample", "beats_transient_loop_mode"): "Sample.TransientLoopMode",
}


def _enum_choices_for(obj, name):
    """Enum names (index = value) for a property Live answers with an int, or None."""
    for cls in type(obj).__mro__:
        path = _ENUM_PROPERTIES.get((cls.__name__, name))
        if path is None:
            continue
        names = compat.enum_names(compat.live_enum(path))
        if names and sorted(names) == list(range(len(names))):
            choices = [names[i] for i in range(len(names))]
            if len(choices) > 1 and choices[-1] == "count":   # Clip.WarpMode sentinel
                choices.pop()
            return choices
    return None


def _class_members(obj, stop="Device"):
    """``(properties {name: writable}, methods [names])`` defined by the object's own
    class(es) — everything above ``stop`` in the MRO (Live's Boost.Python classes expose
    their attributes as ``property`` objects; read-only ones have no setter)."""
    properties = {}
    methods = []
    for cls in type(obj).__mro__:
        if cls.__name__ in (stop, "LomObject", "object", "instance"):
            break
        for name, attr in vars(cls).items():
            if name.startswith("_") or name in properties or name in methods:
                continue
            if isinstance(attr, property):
                properties[name] = attr.fset is not None
            elif callable(attr) and not isinstance(attr, type) and \
                    not name.endswith(("_listener", "_has_listener")):
                methods.append(name)
    return properties, sorted(methods)


def _is_enum(value):
    return isinstance(value, int) and not isinstance(value, bool) and \
        isinstance(getattr(type(value), "names", None), dict) and \
        isinstance(getattr(type(value), "values", None), dict)


def _plain(value):
    """``(json value, extra dict)`` for a property value, or ``(None, None)`` when it is
    not a scalar / list of scalars (LOM objects are skipped)."""
    if value is None or isinstance(value, (bool, str)):
        return value, {}
    if _is_enum(value):
        return int(value), {"value_name": str(getattr(value, "name", "") or int(value)),
                            "choices": [str(k) for k, _v in sorted(
                                type(value).names.items(), key=lambda kv: int(kv[1]))]}
    if isinstance(value, int):
        return int(value), {}
    if isinstance(value, float):
        return num(value), {}
    if compat.is_sequence(value) or isinstance(value, (list, tuple)):
        items = list(value)[:_MAX_CHOICES + 1]
        if all(isinstance(i, (str, int, float)) and not isinstance(i, bool) for i in items):
            return [i if isinstance(i, (str, int)) else num(i) for i in items], {}
    return None, None


def device_properties(dev, include_lists=False, stop="Device", skip=_PROPERTY_SKIP):
    """``(properties [{name, value, writable, choices?, value_name?}], methods)`` of a
    device (or, with ``stop="LomObject"``, of any LOM object such as a Sample)."""
    props, methods = _class_members(dev, stop=stop)
    get = compat.safe_getattr
    choice_lists = {}
    for name in props:
        partner = _CHOICE_LISTS.get(name)
        if partner is None:
            for candidate in (name + "_list", name[:-len("_index")] + "_list"
                              if name.endswith("_index") else None):
                if candidate and candidate in props:
                    partner = candidate
                    break
        if partner is not None and partner in props:
            choice_lists[name] = partner
    used_lists = set(choice_lists.values())
    rows = []
    for name in sorted(props):
        if name in skip or (name in used_lists and not include_lists):
            continue
        value, extra = _plain(get(dev, name))
        if extra is None:
            continue
        row = {"name": name, "value": value, "writable": props[name]}
        row.update(extra)
        enum_choices = None if "choices" in row else _enum_choices_for(dev, name)
        if enum_choices is not None and isinstance(value, int):
            row["choices"] = enum_choices
            if 0 <= int(value) < len(enum_choices):
                row["value_name"] = enum_choices[int(value)]
        partner = choice_lists.get(name)
        if partner is not None:
            choices, _extra = _plain(get(dev, partner))
            if isinstance(choices, list):
                row["choices"] = [str(c) for c in choices[:_MAX_CHOICES]]
                if len(choices) > _MAX_CHOICES:
                    row["choices_truncated"] = True
                if isinstance(value, int) and not isinstance(value, bool) and \
                        0 <= value < len(choices):
                    row["value_name"] = str(choices[value])
        rows.append(row)
    return rows, [m for m in methods if m not in _METHOD_SKIP]


def _coerce_property_index(name, value, choices):
    """Position of ``value`` (a choice name — exact, then case/space/underscore-insensitive,
    then a unique substring — or an index) in ``choices``."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int) and not isinstance(value, bool):
        if 0 <= value < len(choices):
            return value
        raise BridgeError("bad_args", "%s must be 0..%d" % (name, len(choices) - 1))
    if not isinstance(value, str):
        raise BridgeError("bad_args", "%s must be one of %s" % (name, ", ".join(choices[:24])))
    wanted = _norm_key(value.replace("_", " "))
    exact = [i for i, c in enumerate(choices) if _norm_key(str(c).replace("_", " ")) == wanted]
    loose = exact or [i for i, c in enumerate(choices)
                      if _squeeze(str(c)) == _squeeze(value)] or \
        [i for i, c in enumerate(choices)
         if wanted and wanted in _norm_key(str(c).replace("_", " "))]
    if len(loose) == 1 or exact:
        return loose[0]
    raise BridgeError("bad_args" if loose else "not_found",
                      "%s: %r %s (choices: %s)" % (
                          name, value, "is ambiguous" if loose else "not found",
                          ", ".join(str(c) for c in choices[:24])))


def _resolve_property_owner(dev, name):
    """``(owner, property name, label)`` — ``sample.<name>`` addresses a Simpler's
    Sample."""
    if isinstance(name, str) and name.startswith("sample."):
        sample = compat.safe_getattr(dev, "sample")
        if sample is None:
            raise BridgeError("invalid_state", "%r has no sample" % _name(dev))
        return sample, name[len("sample."):], name
    return dev, name, name


def _coerce_property(owner, name, value, row):
    """The value to assign to ``owner.name`` (enum member, index from a choice name ...)."""
    current = compat.safe_getattr(owner, name)
    choices = row.get("choices") or []
    if _is_enum(current):
        enum = type(current)
        if isinstance(value, str):
            key = _norm_key(value).replace(" ", "_")
            for member_name, member in enum.names.items():
                if _norm_key(member_name).replace(" ", "_") == key:
                    return member
            raise BridgeError("bad_args", "%s must be one of %s" % (name, ", ".join(
                sorted(enum.names, key=lambda k: int(enum.names[k])))))
        if isinstance(value, int) and not isinstance(value, bool) and value in enum.values:
            return enum.values[value]
        raise BridgeError("bad_args", "%s must be one of %s (or its number)" % (
            name, ", ".join(sorted(enum.names, key=lambda k: int(enum.names[k])))))
    if isinstance(current, bool):
        if choices and not isinstance(value, bool):
            # Eq8 edit_mode: Live answers a bool for its two-value enum (a/b)
            index = _coerce_property_index(name, value, choices)
            return bool(index)
        if not isinstance(value, bool):
            raise BridgeError("bad_args", "%s must be true or false" % name)
        return value
    if isinstance(current, int):
        if isinstance(value, str) and choices:
            return _coerce_property_index(name, value, choices)
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if isinstance(value, bool) or not isinstance(value, int):
            raise BridgeError("bad_args", "%s must be an integer%s" % (
                name, " or one of its choices" if choices else ""))
        if choices and not 0 <= value < len(choices):
            raise BridgeError("bad_args", "%s must be 0..%d" % (name, len(choices) - 1))
        return value
    if isinstance(current, float):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise BridgeError("bad_args", "%s must be a number" % name)
        return float(value)
    if isinstance(current, str):
        if not isinstance(value, str):
            raise BridgeError("bad_args", "%s must be a string" % name)
        return value
    return value


@command("devices.properties",
         doc="Class-specific device properties (Wavetable, Drift, Hybrid Reverb, Looper ...)")
def devices_properties(ctx, track=None, device=None, include_lists=False):
    """List the extra properties and actions a device class has beyond parameters —
    e.g. Wavetable's wavetable category/index and unison mode, Drift's mod matrix and
    voice mode, Meld's engine, Hybrid Reverb's IR category/file, Eq8's edit/global
    mode and oversampling, Looper's record length, Roar/Shifter/Spectral Resonator
    modes, CC Control targets, a Simpler's pitch-bend ranges and its Sample's
    warp-engine settings.

    Args:
        track, device: see the module docstring (default: selected device).
        include_lists: also list the raw ``*_list`` properties (their contents
            are already shown as ``choices`` of the property they belong to).

    Returns:
        {device: {path, name, class_name}, properties: [{name, value, writable,
         choices?: [names], value_name?}], sample_properties?: [...] (Simpler:
         the loaded Sample's warp/slicing settings, set them as
         "sample.<name>"), actions: [method names for devices.action]}

    Gotchas:
        Discovered from Live's class at runtime (Live 12.4.5 names); an
        ``_index`` property takes the position in its ``choices``.  Enum
        properties show the enum names as choices.
    """
    dev, path = resolve_device(ctx, track, device)
    rows, methods = device_properties(dev, bool(include_lists))
    data = {"device": device_brief(dev, path), "properties": rows,
            "actions": [m for m in methods if m not in _CURATED_METHODS]}
    sample = compat.safe_getattr(dev, "sample")
    if sample is not None:
        sample_rows = device_properties(sample, False, "LomObject", _SAMPLE_SKIP)[0]
        for row in sample_rows:
            row["name"] = "sample." + row["name"]
        data["sample_properties"] = sample_rows
    return data


_SAMPLE_SKIP = frozenset(("slices", "warp_markers", "file_path", "canonical_parent"))


#: Methods other commands already cover (simpler.action, plugins, racks ...).
_CURATED_METHODS = frozenset((
    "crop", "reverse", "warp_as", "warp_double", "warp_half", "guess_playback_length",
    "replace_sample", "get_parameter_names", "insert_chain", "copy_pad", "add_macro",
    "remove_macro", "store_variation", "recall_selected_variation",
    "recall_last_used_variation", "delete_selected_variation", "randomize_macros",
    "get_value_item_icons"))


@command("devices.set_properties", mutating=True,
         doc="Set class-specific device properties (wavetable, IR, modes, voices ...)")
def devices_set_properties(ctx, values, track=None, device=None):
    """Set one or more class-specific properties listed by devices.properties.

    Args:
        values: {name: value} — e.g. {"oscillator_1_wavetable_category": "Basics",
            "oscillator_1_wavetable_index": "Saw", "unison_mode": "classic"},
            {"ir_category_index": "Halls"}, {"edit_mode": "b"},
            {"sample.texture_grain_size": 20.0}.  An ``_index`` / list property
            takes an index or a choice name (exact, then case/space-insensitive,
            then a unique substring); an enum property its name or number.
        track, device: see the module docstring.

    Returns:
        {device, changed: [{name, was, value, value_name?}], properties: the
         changed properties after the write}

    Gotchas:
        Every value is validated before anything is written.  Choice lists can
        change after a write (Wavetable's wavetable names follow the category;
        Hybrid Reverb's IR files follow the IR category) — set the category
        first (a dict keeps its order).  Read-only properties raise bad_args.
    """
    if not isinstance(values, dict) or not values:
        raise BridgeError("bad_args", "values must be a non-empty object {name: value}")
    dev, path = resolve_device(ctx, track, device)

    def rows_of(owner):
        if owner is dev:
            return dict((r["name"], r) for r in device_properties(dev, True)[0])
        return dict((r["name"], r) for r in device_properties(owner, True, "LomObject",
                                                              _SAMPLE_SKIP)[0])

    device_rows = rows_of(dev)
    planned = []
    for name, value in values.items():
        owner, attr, label = _resolve_property_owner(dev, name)
        rows = device_rows if owner is dev else rows_of(owner)
        row = rows.get(attr)
        if row is None:
            raise BridgeError("not_found", "%s has no property %r (have: %s)" % (
                "%r (%s)" % (_name(dev), compat.safe_getattr(dev, "class_name", "?"))
                if owner is dev else "the sample", attr,
                ", ".join(sorted(n for n, r in rows.items() if r["writable"])) or "none"))
        if not row["writable"]:
            raise BridgeError("bad_args", "%s is read-only" % label)
        try:
            _coerce_property(owner, attr, value, row)
        except BridgeError as error:
            # a choice name may only exist after an earlier write in this call
            # (Wavetable category -> wavetable names): re-checked when written
            if error.type != "not_found" or len(values) < 2:
                raise
        planned.append((owner, attr, label, value))
    changed = []
    for owner, attr, label, value in planned:
        # choices may depend on an earlier write (category -> wavetables): re-read
        row = rows_of(owner)[attr]
        target = _coerce_property(owner, attr, value, row)
        was = row["value"]
        _set_attr(owner, attr, target, label)
        after_row = rows_of(owner).get(attr, {})
        entry = {"name": label, "was": was, "value": after_row.get("value")}
        if after_row.get("value_name") is not None:
            entry["value_name"] = after_row["value_name"]
        changed.append(entry)
    names = set(attr for owner, attr, _l, _v in planned if owner is dev)
    after = [r for r in device_properties(dev, False)[0] if r["name"] in names]
    return {"device": device_brief(dev, path), "changed": changed, "properties": after}


def _action_argument(ctx, value):
    """One devices.action argument: a "song...." string becomes that LOM object."""
    if isinstance(value, str) and value.strip().startswith("song."):
        return ctx.resolve(value.strip())
    return value


@command("devices.action", mutating=True,
         doc="Run a class-specific device method (Looper record/overdub/export, CC resend ...)")
def devices_action(ctx, action, track=None, device=None, args=None, target_track=None,
                   slot=None, parameter=None):
    """Call one of the device's own methods listed as ``actions`` by devices.properties.

    Args:
        action: the method name, e.g. Looper "record", "overdub", "play", "stop",
            "clear", "undo", "double_length", "half_length", "double_speed",
            "half_speed", "export_to_clip_slot"; CC Control "resend"; Max for
            Live "get_bank_count" / "get_bank_name" / "get_bank_parameters";
            Wavetable "is_parameter_modulatable"; any device
            "save_preset_to_compare_ab_slot" (A/B compare).
        track, device: see the module docstring.
        args: positional arguments (numbers/strings; a "song...." string is
            resolved to that LOM object).
        target_track, slot: for Looper "export_to_clip_slot" — the empty session
            clip slot (track + slot index / scene name) to receive the loop.
        parameter: for methods that take a DeviceParameter (Wavetable
            "is_parameter_modulatable" / "add_parameter_to_modulation_matrix"):
            a parameter of this device (index, name or path).

    Returns:
        {device, action, result} (``result`` is the method's return value, LOM
        objects summarised).

    Gotchas:
        Looper transport methods act at Looper's own quantization; recording
        needs audio arriving at the track.  Only methods of the device's own
        class (and save_preset_to_compare_ab_slot) are allowed.
    """
    dev, path = resolve_device(ctx, track, device)
    _props, methods = _class_members(dev)
    allowed = [m for m in methods if m not in _METHOD_SKIP]
    if compat.safe_getattr(dev, "can_compare_ab", False):
        allowed.append("save_preset_to_compare_ab_slot")
    if action not in allowed:
        raise BridgeError("bad_args", "%r (%s) has no action %r (have: %s)" % (
            _name(dev), compat.safe_getattr(dev, "class_name", "?"), action,
            ", ".join(allowed) or "none"))
    call_args = []
    if args is not None:
        if not isinstance(args, list):
            raise BridgeError("bad_args", "args must be a list")
        call_args = [_action_argument(ctx, a) for a in args]
    if parameter is not None:
        call_args.insert(0, find_parameter(ctx, dev, parameter)[0])
    if slot is not None or target_track is not None:
        if slot is None or target_track is None:
            raise BridgeError("bad_args", "pass target_track and slot together")
        call_args.insert(0, ctx.clip_slot(target_track, slot))
    try:
        returned = getattr(dev, action)(*call_args)
    except BridgeError:
        raise
    except Exception as error:
        raise BridgeError("invalid_state", "%s failed: %s" % (action, error))
    value, extra = _plain(returned)
    if extra is None:
        try:
            value = ctx.summarize(returned, "minimal")
        except Exception:
            value = str(returned)
    return {"device": device_brief(dev, path), "action": action, "result": value}


_MOD_SOURCES = ("amp_envelope", "envelope_2", "envelope_3", "lfo_1", "lfo_2",
                "midi_velocity", "midi_note", "midi_pitch_bend", "midi_channel_pressure",
                "midi_mod_wheel", "midi_random")


def _mod_sources():
    enum = compat.live_enum("WavetableDevice.ModulationSource")
    names = compat.enum_names(enum) if enum is not None else {}
    if names:
        return [names[k] for k in sorted(names)]
    return list(_MOD_SOURCES)


def _mod_source(value, sources):
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < len(sources):
        return value
    if isinstance(value, str):
        key = _norm_key(value).replace(" ", "_")
        for index, name in enumerate(sources):
            if _norm_key(name).replace(" ", "_") == key or \
                    _squeeze(name) == _squeeze(value):
                return index
    raise BridgeError("bad_args", "source must be one of %s (or 0..%d)"
                      % (", ".join(sources), len(sources) - 1))


def _mod_target(dev, value):
    names = [str(n) for n in compat.safe_getattr(dev, "visible_modulation_target_names", ())
             or ()]
    if isinstance(value, int) and not isinstance(value, bool):
        if 0 <= value < len(names):
            return value, names
        raise BridgeError("bad_args", "target must be 0..%d" % (len(names) - 1))
    if isinstance(value, str):
        key = _norm_key(value)
        matches = [i for i, n in enumerate(names) if _norm_key(n) == key] or \
            [i for i, n in enumerate(names) if _squeeze(n) == _squeeze(value)] or \
            [i for i, n in enumerate(names) if key and key in _norm_key(n)]
        if len(matches) == 1:
            return matches[0], names
        raise BridgeError("bad_args" if matches else "not_found",
                          "modulation target %r %s (targets: %s)" % (
                              value, "is ambiguous" if matches else "not found",
                              ", ".join(names)))
    raise BridgeError("bad_args", "target must be a target index or name")


@command("devices.modulation", mutating=True,
         doc="Wavetable modulation matrix: read, set an amount, add a parameter as target")
def devices_modulation(ctx, op="get", track=None, device=None, target=None, source=None,
                       amount=None, parameter=None):
    """Wavetable's modulation matrix (the only native device with a matrix API).

    Args:
        op: "get" (every visible target with its non-zero amounts), "set" (one
            amount), "add" (make a parameter a matrix target).
        track, device: the Wavetable (see the module docstring).
        target: "set" — a target index or name from ``targets`` ("Osc 1 Pos").
        source: "set" — "amp_envelope", "envelope_2", "envelope_3", "lfo_1",
            "lfo_2", "midi_velocity", "midi_note", "midi_pitch_bend",
            "midi_channel_pressure", "midi_mod_wheel", "midi_random" (or 0..10).
        amount: "set" — the modulation amount (Live's internal value, e.g. -1..1).
        parameter: "add" — a parameter of this Wavetable (name/index/path);
            pitch parameters cannot be added (Live says so).

    Returns:
        {device, sources: [...], targets: [{index, name, amounts: {source: value}}],
         changed?: {target, source, was, amount}, added?: {parameter, target_index}}

    Gotchas:
        ``targets`` are Live's *visible* modulation targets; adding a parameter
        makes it visible.  Other devices answer ``unsupported`` (Drift's matrix
        is in devices.properties: mod_matrix_*).
    """
    if op not in ("get", "set", "add"):
        raise BridgeError("bad_args", "op must be 'get', 'set' or 'add'")
    dev, path = resolve_device(ctx, track, device)
    if not compat.has(dev, "get_modulation_value"):
        raise BridgeError("unsupported", "%r (%s) has no modulation matrix API (Wavetable "
                          "only)" % (_name(dev), compat.safe_getattr(dev, "class_name", "?")))
    sources = _mod_sources()
    data = {"device": device_brief(dev, path), "sources": sources}
    if op == "add":
        if parameter is None:
            raise BridgeError("bad_args", "op='add' needs parameter")
        param = find_parameter(ctx, dev, parameter)[0]
        ok, modulatable = compat.safe_call(dev, "is_parameter_modulatable", param)
        if ok and modulatable is False:
            raise BridgeError("invalid_state", "%r cannot be modulated by the matrix"
                              % compat.safe_getattr(param, "name", "?"))
        try:
            index = dev.add_parameter_to_modulation_matrix(param)
        except Exception as error:
            raise BridgeError("invalid_state", "add_parameter_to_modulation_matrix failed: %s"
                              % error)
        data["added"] = {"parameter": compat.safe_getattr(param, "name"),
                         "target_index": index}
    elif op == "set":
        if target is None or source is None or amount is None:
            raise BridgeError("bad_args", "op='set' needs target, source and amount")
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            raise BridgeError("bad_args", "amount must be a number")
        index, names = _mod_target(dev, target)
        src = _mod_source(source, sources)
        try:
            was = dev.get_modulation_value(index, src)
            dev.set_modulation_value(index, src, float(amount))
            now = dev.get_modulation_value(index, src)
        except Exception as error:
            raise BridgeError("invalid_state", "set_modulation_value failed: %s" % error)
        data["changed"] = {"target": names[index], "source": sources[src],
                           "was": num(was, 4), "amount": num(now, 4)}
    names = [str(n) for n in compat.safe_getattr(dev, "visible_modulation_target_names", ())
             or ()]
    targets = []
    for index, name in enumerate(names):
        amounts = {}
        for src, source_name in enumerate(sources):
            ok, value = compat.safe_call(dev, "get_modulation_value", index, src)
            if ok and isinstance(value, (int, float)) and value:
                amounts[source_name] = num(value, 4)
        targets.append({"index": index, "name": name, "amounts": amounts})
    data["targets"] = targets
    return data
