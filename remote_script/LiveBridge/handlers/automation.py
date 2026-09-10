"""Clip automation envelopes, parameter automation state and re-enable.

What Live's Python API offers (``docs/LIVE_API_DUMP_12.4.5.md``):

* ``clip.automation_envelope(param)`` -> ``Live.Envelope.Envelope`` or None.
  Live 12.4.5's own docstring: *None if the envelope doesn't exist, None for
  Arrangement clips, None for parameters from a different track*.
* ``clip.create_automation_envelope(param)`` (raises when impossible),
  ``clip.clear_envelope(param)``, ``clip.clear_all_envelopes()``,
  ``clip.automation_envelopes`` / ``clip.has_envelopes`` (Live 12).
* ``Envelope.insert_step(time, length, value)``, ``value_at_time(time)`` and
  the newer ``events_in_range`` / ``create_event`` / ``delete_events_in_range``
  (feature-detected).
* ``DeviceParameter.automation_state`` (0 none, 1 playing, 2 overridden) and
  ``re_enable_automation()``; ``song.re_enable_automation()`` and
  ``song.re_enable_automation_enabled``.

Envelope times are clip-local beats (the same timeline as the clip's notes),
values are the parameter's internal values (``min``..``max``).  Time arguments
accept beats or ``bars.beats.sixteenths`` strings (clip signature, ``"1.1.1"``
= clip start) — helpers live in ``handlers/cues.py``.

Arrangement, verified on Live 12.4.5 (2026-09-10):

* Arrangement clips return None from ``automation_envelope(p)``.  An
  arrangement copy made by ``duplicate_clip_to_arrangement`` may carry the
  source's envelopes (12.4.5: on a MIDI track without devices, session ->
  arrangement and arrangement -> arrangement) or none at all (12.4.5 check,
  Session view focused, with an instrument on the track — mixer envelopes
  included) — then there is nothing to edit (``unsupported``).  Envelopes that
  did come along are listed in ``clip.automation_envelopes`` and can be read
  and edited there (``value_at_time``, ``insert_step``, ``create_event``,
  ``delete_events_in_range``, ``clear_envelope``); only *creating* a new one
  raises "Not a session clip or parameter belongs to another track."  This
  module finds envelopes through both, so every command works on those.
* Arrangement track automation lanes are not reachable.  ``automation.record``
  records them the way a person does: Arrangement Record + Automation Arm,
  moving the parameter (``begin_gesture`` / ``value`` / ``end_gesture``) on
  every display tick while the song plays through the range.
* ``EnvelopeEvent.control_coefficients`` (curved segments) is accepted by
  ``create_event`` but ``value_at_time`` ignores it — not exposed.
* A clip envelope always follows the clip loop; Live's "unlinked" envelope
  loop length is not in the API.
"""

import bisect
import math
import random
import re
import time as _time

from .. import compat
from .. import resolve as shared_resolve
from .. import serialize
from ..registry import BridgeError, command
from . import clips as clips_handlers
from . import cues as timing

_AUTOMATION_STATES = {0: "none", 1: "playing", 2: "overridden"}
_MAX_STEPS = 2048
#: Shortest LFO / square cycle (beats).  Anything shorter cannot be drawn with
#: at most ``_MAX_STEPS`` steps over a useful range and made the square loop
#: spin on Live's main thread.
_MIN_CYCLE = 1.0 / 64.0
_MAX_POINTS = 2048
_MAX_WALK = 6000
_MAX_DEPTH = 4
_SCAN = 64
_BISECT = 48
_ARRANGEMENT_HINT = (
    "Live cannot create a new envelope in an arrangement clip: an arrangement clip only has "
    "the envelopes Live copied along from its source (those can be read and edited here) - "
    "and an arrangement copy may carry no envelopes at all (Live 12.4.5 dropped them on a "
    "track with an instrument). Record track automation with automation.record "
    "(Arrangement Record + Automation Arm), or write the envelope into a session clip and "
    "copy it (live_arrangement_duplicate_clip reports envelopes.copied).")

_MIXER_ALIASES = {
    "volume": "volume", "vol": "volume", "track volume": "volume", "mixer volume": "volume",
    "fader": "volume", "pan": "panning", "panning": "panning", "track panning": "panning",
    "mixer pan": "panning", "track on": "track_activator", "activator": "track_activator",
    "track activator": "track_activator", "speaker": "track_activator",
    # master track only
    "tempo": "song_tempo", "song tempo": "song_tempo", "bpm": "song_tempo",
    "crossfader": "crossfader", "cue": "cue_volume", "cue volume": "cue_volume",
    "preview volume": "cue_volume",
}
#: Mixer parameters listed by ``track_parameters`` (the last three exist on the master only).
_MIXER_PARAMS = ("volume", "panning", "track_activator", "song_tempo", "crossfader",
                 "cue_volume")
_SEND_RE = re.compile(r"^(?:send|sends)\s*[:\-_ ]?\s*(.+)$", re.I)
_SENDS_INDEX_RE = re.compile(r"^sends\[(\d+)\]$", re.I)
_UNITS = {"db": ("db", 1.0), "dbfs": ("db", 1.0), "hz": ("hz", 1.0), "khz": ("hz", 1000.0),
          "ms": ("s", 0.001), "s": ("s", 1.0), "sec": ("s", 1.0), "%": ("%", 1.0),
          "st": ("st", 1.0), "ct": ("ct", 1.0), "": ("", 1.0)}
_NUMBER_RE = re.compile(r"^([+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?)\s*(.*)$", re.I)
_PAN_RE = re.compile(r"^(?:(\d+(?:\.\d+)?)\s*([lr])|([lr])\s*(\d+(?:\.\d+)?))$")
_SHAPES = ("ramp_up", "ramp_down", "sine", "triangle", "saw_up", "saw_down", "square",
           "random")
_SHAPE_ALIASES = {"ramp": "ramp_up", "fade_in": "ramp_up", "fade_out": "ramp_down",
                  "saw": "saw_up", "lfo": "sine", "sin": "sine", "tri": "triangle",
                  "noise": "random", "sample_and_hold": "random", "s&h": "random",
                  "pulse": "square"}

rnd = timing.rnd


# --------------------------------------------------------------------------
# clip and track resolution
# --------------------------------------------------------------------------

def _kind(obj):
    return serialize.kind_of(obj)


def _name(obj, default=""):
    value = compat.safe_getattr(obj, "name", default)
    return default if value is None else str(value)


def owner_track(obj):
    """The Track that owns a clip/parameter/device (walks ``canonical_parent``)."""
    node = obj
    for _ in range(12):
        if node is None:
            return None
        if _kind(node) == "track":
            return node
        node = compat.safe_getattr(node, "canonical_parent")
    return None


def is_arrangement(clip):
    return bool(compat.safe_getattr(clip, "is_arrangement_clip", False))


def resolve_clip(ctx, track=None, slot=None, clip=None):
    """A Clip from ``clip`` (path / name / "selected") or ``track`` + ``slot``.

    The shared clip addressing of ``handlers/clips.py`` (same forms, same
    errors as every clip command).
    """
    return clips_handlers.resolve_clip(ctx, track, slot, clip)


def _clip_track(ctx, clip, track=None):
    track_obj = owner_track(clip)
    if track_obj is None and track is not None:
        track_obj = ctx.track(track)
    if track_obj is None:
        raise BridgeError("not_found", "cannot tell which track owns this clip")
    return track_obj


def clip_range(clip):
    """Default time window of a clip: its loop, or — unlooped — the clip
    start..end, which Live 12.4.5 keeps in ``loop_start``/``loop_end`` too
    (``handlers/clips.py:clip_region``)."""
    get = compat.safe_getattr
    start = get(clip, "loop_start" if get(clip, "looping", True) else "start_marker", 0.0)
    end = get(clip, "loop_end", None)
    start = float(start or 0.0)
    if end is None or float(end) <= start:
        end = start + float(get(clip, "length", 4.0) or 4.0)
    return start, float(end)


def _window(clip, start, end):
    default_start, default_end = clip_range(clip)
    begin = default_start if start is None else timing.parse_time(clip, start, "start")
    finish = default_end if end is None else timing.parse_time(clip, end, "end")
    if finish <= begin:
        raise BridgeError("bad_args", "end (%s) must be after start (%s)"
                          % (rnd(finish), rnd(begin)))
    return begin, finish


def clip_info(ctx, clip):
    info = {"path": ctx.path_of(clip), "name": _name(clip)}
    if is_arrangement(clip):
        info["arrangement"] = True
    return info


# --------------------------------------------------------------------------
# parameters of a track
# --------------------------------------------------------------------------

def track_parameters(ctx, track_obj, include_mixer=True):
    """Every automatable parameter of a track (devices, nested racks, mixer).

    Returns a list of ``(parameter, device_label, path)``; racks are labelled
    ``"Rack > Chain > Device"``.
    """
    get = compat.safe_getattr
    track_path = ctx.path_of(track_obj) or "track"
    entries = []

    def walk(devices, prefix, label, depth):
        for d_index, device in enumerate(devices or ()):
            if len(entries) >= _MAX_WALK:
                return
            d_path = "%s[%d]" % (prefix, d_index)
            d_label = _name(device, "device") if not label else label + " > " + _name(device)
            for p_index, param in enumerate(get(device, "parameters", ()) or ()):
                entries.append((param, d_label, "%s.parameters[%d]" % (d_path, p_index)))
            if depth >= _MAX_DEPTH or not get(device, "can_have_chains", False):
                continue
            for attr in ("chains", "return_chains"):
                for c_index, chain in enumerate(get(device, attr, ()) or ()):
                    walk(get(chain, "devices", ()),
                         "%s.%s[%d].devices" % (d_path, attr, c_index),
                         d_label + " > " + _name(chain, "chain"), depth + 1)

    walk(get(track_obj, "devices", ()), track_path + ".devices", "", 0)
    mixer = get(track_obj, "mixer_device")
    if include_mixer and mixer is not None:
        m_path = track_path + ".mixer_device"
        for attr in _MIXER_PARAMS:
            param = get(mixer, attr)
            if param is not None:
                entries.append((param, "Mixer", "%s.%s" % (m_path, attr)))
        for s_index, send in enumerate(get(mixer, "sends", ()) or ()):
            entries.append((send, "Mixer", "%s.sends[%d]" % (m_path, s_index)))
    return entries


def _same(a, b):
    if a is b:
        return True
    try:
        return bool(a == b)
    except Exception:
        return False


def _entry_for(ctx, track_obj, param, entries=None):
    for entry in entries if entries is not None else track_parameters(ctx, track_obj):
        if _same(entry[0], param):
            return entry
    parent = compat.safe_getattr(param, "canonical_parent")
    label = "Mixer" if _kind(parent) == "mixer_device" else _name(parent, "device")
    return param, label, ctx.path_of(param)


def _mixer_alias(ctx, track_obj, text):
    mixer = compat.safe_getattr(track_obj, "mixer_device")
    if mixer is None:
        return None
    base = (ctx.path_of(track_obj) or "track") + ".mixer_device"
    attr = _MIXER_ALIASES.get(text.lower())
    if attr is not None:
        param = compat.safe_getattr(mixer, attr)
        return (param, "Mixer", "%s.%s" % (base, attr)) if param is not None else None
    sends = list(compat.safe_getattr(mixer, "sends", ()) or ())
    index = None
    match = _SENDS_INDEX_RE.match(text)
    if match:
        index = int(match.group(1))
    else:
        match = _SEND_RE.match(text)
        if not match:
            return None
        rest = match.group(1).strip()
        if len(rest) == 1 and rest.isalpha():
            index = ord(rest.upper()) - ord("A")
        else:
            names = [_name(r).lower() for r in
                     compat.safe_getattr(ctx.song, "return_tracks", ()) or ()]
            wanted = rest.lower()
            for test in (lambda n: n == wanted, lambda n: n.startswith(wanted),
                         lambda n: wanted in n):
                hits = [i for i, n in enumerate(names) if test(n)]
                if hits:
                    index = hits[0]
                    break
            if index is None:
                return None
    if not 0 <= index < len(sends):
        raise BridgeError("not_found", "%r: this track has %d sends" % (text, len(sends)))
    return sends[index], "Mixer", "%s.sends[%d]" % (base, index)


def resolve_parameter(ctx, track_obj, parameter, device=None, entries=None):
    """``(param, device_label, path)`` for a parameter of ``track_obj``.

    ``parameter`` may be a LOM path, a mixer alias ("volume", "pan", "send A",
    "send Reverb", "track on"), a parameter name (exact, case-insensitive,
    prefix; "Device > Param" disambiguates) or — with ``device`` — an index.
    """
    if _kind(parameter) == "parameter":
        return _entry_for(ctx, track_obj, parameter, entries)
    if isinstance(parameter, bool) or parameter is None:
        raise BridgeError("bad_args", "parameter must be a name, an index (with device) or a path")
    if isinstance(parameter, str) and parameter.strip().startswith("song."):
        obj = ctx.resolve(parameter.strip())
        if _kind(obj) != "parameter":
            raise BridgeError("bad_args", "%s is not a device parameter" % parameter.strip())
        return _entry_for(ctx, track_obj, obj, entries)
    if device is not None:
        if isinstance(device, str) and device.strip().startswith("song."):
            dev = ctx.resolve(device.strip())
        else:
            dev = ctx.device(track_obj, device)
        if _kind(dev) != "device":
            raise BridgeError("bad_args", "device %r is not a device" % (device,))
        return _entry_for(ctx, track_obj, ctx.parameter(dev, parameter), entries)
    if isinstance(parameter, int) or (isinstance(parameter, str)
                                      and parameter.strip().lstrip("-").isdigit()):
        raise BridgeError("bad_args", "a parameter index needs device=<index|name|path>")
    if not isinstance(parameter, str) or not parameter.strip():
        raise BridgeError("bad_args", "parameter must be a name, an index (with device) or a path")
    text = parameter.strip()
    alias = _mixer_alias(ctx, track_obj, text)
    if alias is not None:
        return alias
    entries = entries if entries is not None else track_parameters(ctx, track_obj)
    wanted_device = None
    if " > " in text:
        wanted_device, text = [part.strip() for part in text.rsplit(" > ", 1)]
    pool = entries
    if wanted_device:
        low = wanted_device.lower()
        pool = [e for e in entries if e[1].lower() == low or e[1].lower().endswith(" > " + low)
                or e[1].lower().startswith(low)]
    for test in (lambda n: n == text, lambda n: n.lower() == text.lower(),
                 lambda n: n.lower().startswith(text.lower())):
        hits = [e for e in pool if test(_name(e[0]))]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise BridgeError("bad_args", "parameter %r is ambiguous on %r: %s — pass device= "
                              "or the path" % (parameter, _name(track_obj), ", ".join(
                                  "%s > %s (%s)" % (e[1], _name(e[0]), e[2]) for e in hits[:6])))
    names = ", ".join(sorted(set("%s > %s" % (e[1], _name(e[0])) for e in entries[:40])))
    raise BridgeError("not_found", "no parameter %r on %r (have e.g.: %s)"
                      % (parameter, _name(track_obj), names))


def param_info(param, label, path, detail=False):
    info = {"name": _name(param), "device": label, "path": path}
    if detail:
        info["min"] = rnd(compat.safe_getattr(param, "min", 0.0), 6)
        info["max"] = rnd(compat.safe_getattr(param, "max", 1.0), 6)
        if compat.safe_getattr(param, "is_quantized", False):
            info["quantized"] = True
    return info


def _state(param):
    state = compat.safe_getattr(param, "automation_state", 0)
    try:
        return _AUTOMATION_STATES.get(int(state), str(state))
    except (TypeError, ValueError):
        return str(state)


def _display(param, value):
    ok, text = compat.safe_call(param, "str_for_value", float(value))
    return str(text) if ok and text is not None else None


# --------------------------------------------------------------------------
# values: native numbers, normalized 0..1, display strings, value items
# --------------------------------------------------------------------------

#: Compound displays such as Serum 2's "50% [-9.0 dB]" (a second reading in brackets).
_COMPOUND_RE = re.compile(r"^(.*?\S)\s*[\[(]\s*([^\])]*?)\s*[\])]$")


def _readings(text):
    """Every ``(number, unit)`` reading of a display: ``"50% [-9.0 dB]"`` -> two."""
    match = _COMPOUND_RE.match(str(text).strip())
    if match:
        found = [r for r in (parse_display(match.group(1)), parse_display(match.group(2)))
                 if r is not None]
        if found:
            return found
    single = parse_display(text)
    return [single] if single is not None else []


def parse_display(text):
    """``"1.00 kHz"`` -> ``(1000.0, "hz")``, ``"25L"`` -> ``(-25.0, "pan")``,
    ``"-inf dB"`` -> ``(-inf, "db")``, ``"50% [-9.0 dB]"`` -> ``(50.0, "%")`` (the
    primary reading of a compound display); ``None`` when there is no number."""
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
        amount = float(match.group(1) or match.group(4))
        side = match.group(2) or match.group(3)
        return (-amount if side == "l" else amount), "pan"
    inf = re.match(r"^([+-]?)inf(?:inity)?\s*([a-z%]*)$", raw)
    if inf:
        unit = _UNITS.get(inf.group(2), (inf.group(2), 1.0))[0]
        return float("-inf") if inf.group(1) == "-" else float("inf"), unit
    match = _NUMBER_RE.match(raw)
    if not match:
        return None
    unit, scale = _UNITS.get(match.group(2).strip(), (match.group(2).strip(), 1.0))
    return float(match.group(1)) * scale, unit


def value_for_display(param, text):
    """Internal value whose ``str_for_value`` reads like ``text``.

    Returns ``(value, clamped)``.  Scans the range, then bisects inside the
    bracket (display curves are monotonic: dB, Hz, ms, %, pan).
    """
    target = parse_display(text)
    name = _name(param, "parameter")
    if target is None:
        raise BridgeError("bad_args", "%r: cannot read %r as a value — send a number, a "
                          "display string like '-6 dB' / '1.2 kHz' / '25L', or "
                          "normalized=true" % (name, text))
    number, unit = target
    low = float(compat.safe_getattr(param, "min", 0.0))
    high = float(compat.safe_getattr(param, "max", 1.0))

    def measure(x):
        for parsed in _readings(_display(param, x) or ""):
            if not (unit and parsed[1] and parsed[1] != unit):
                return parsed[0]
        return None

    samples = []
    for k in range(_SCAN + 1):
        x = low + (high - low) * k / float(_SCAN)
        y = measure(x)
        if y is not None:
            samples.append((x, y))
    if not samples:
        raise BridgeError("bad_args", "%r: %r does not match this parameter's display (e.g. %r)"
                          % (name, text, _display(param, low)))
    for index, ((x0, y0), (x1, y1)) in enumerate(zip(samples, samples[1:])):
        if not min(y0, y1) <= number <= max(y0, y1):
            continue
        if y0 == y1:
            return x0, False
        increasing = y1 > y0

        def reached(x):
            y = measure(x)
            return y is not None and (y >= number if increasing else y <= number)

        first = x0 if reached(x0) else _bisect(x0, x1, reached)
        shown = measure(first)
        if shown != number:
            return first, False
        # The display rounds: aim for the middle of the band that reads `text`,
        # or the range end when the band touches it ("+6 dB", "-inf dB").
        if first <= samples[0][0]:
            return samples[0][0], False
        previous = first
        for x, _y in samples[index + 1:]:
            if measure(x) != shown:
                last = _bisect(previous, x, lambda v: measure(v) != shown)
                return (first + last) / 2.0, False
            previous = x
        return samples[-1][0], False
    lowest = min(samples, key=lambda s: s[1])
    highest = max(samples, key=lambda s: s[1])
    return (lowest[0] if number < lowest[1] else highest[0]), True


def _bisect(lo, hi, test):
    """First x in ``(lo, hi]`` where ``test`` holds (``test(hi)`` is true)."""
    for _ in range(_BISECT):
        mid = (lo + hi) / 2.0
        if test(mid):
            hi = mid
        else:
            lo = mid
    return hi


def native_value(param, value, normalized=False):
    """JSON value -> ``(internal value, clamped)`` for ``param``.

    Numbers are internal values (``normalized=True``: 0..1 of min..max);
    booleans map to max/min; strings are display text ("-6 dB", "1.2 kHz",
    "25L", "50 %") or a value item ("Saw") for quantized parameters.
    """
    low = float(compat.safe_getattr(param, "min", 0.0))
    high = float(compat.safe_getattr(param, "max", 1.0))
    quantized = bool(compat.safe_getattr(param, "is_quantized", False))
    clamped = False
    if isinstance(value, bool):
        target = high if value else low
    elif isinstance(value, (int, float)):
        target = float(value)
        if target != target or target in (float("inf"), float("-inf")):
            raise BridgeError("bad_args", "values must be finite numbers")
        if normalized:
            clamped = not 0.0 <= target <= 1.0
            target = low + max(0.0, min(1.0, target)) * (high - low)
    elif isinstance(value, str):
        text = value.strip()
        target = None
        if normalized:
            try:
                return native_value(param, float(text), True)
            except ValueError:
                pass
        if quantized:
            items = [str(i) for i in (compat.safe_getattr(param, "value_items", ()) or ())]
            for test in (lambda i: i.lower() == text.lower(),
                         lambda i: i.lower().startswith(text.lower())):
                hits = [index for index, item in enumerate(items) if test(item)]
                if hits:
                    offset = low if abs((high - low + 1) - len(items)) < 0.5 else 0.0
                    target = offset + hits[0]
                    break
        if target is None:
            try:
                target = float(text)
            except ValueError:
                target, clamped = value_for_display(param, text)
    else:
        raise BridgeError("bad_args", "values must be numbers, booleans or display strings, "
                          "got %s" % type(value).__name__)
    if target < low or target > high:
        clamped = True
        target = max(low, min(high, target))
    if quantized:
        target = max(low, min(high, float(int(round(target)))))
    return target, clamped


class _Converter(object):
    """Caches display-string lookups while writing many points."""

    def __init__(self, param, normalized):
        self.param = param
        self.normalized = normalized
        self.cache = {}
        self.clamped = 0
        self.quantized = bool(compat.safe_getattr(param, "is_quantized", False))
        self.low = float(compat.safe_getattr(param, "min", 0.0))
        self.high = float(compat.safe_getattr(param, "max", 1.0))

    def __call__(self, value):
        key = (type(value).__name__, value)
        if key not in self.cache:
            self.cache[key] = native_value(self.param, value, self.normalized)
        target, clamped = self.cache[key]
        if clamped:
            self.clamped += 1
        return target

    def fit(self, value):
        """Clamp/round an already-native value."""
        value = max(self.low, min(self.high, float(value)))
        return float(int(round(value))) if self.quantized else value


# --------------------------------------------------------------------------
# envelopes
# --------------------------------------------------------------------------

def get_envelope(clip, param):
    """The clip's envelope for ``param`` or None.

    ``automation_envelope(param)`` answers None for arrangement clips on Live
    12.4.5 even when the clip has that envelope (when
    ``duplicate_clip_to_arrangement`` copied it along — it may copy none), so
    ``automation_envelopes`` is searched as well.
    """
    if not compat.has(clip, "automation_envelope"):
        raise BridgeError("unsupported", "this Live version has no clip.automation_envelope")
    try:
        envelope = clip.automation_envelope(param)
    except Exception:
        envelope = None
    if envelope is not None:
        return envelope
    for candidate in compat.safe_getattr(clip, "automation_envelopes", ()) or ():
        if _same(compat.safe_getattr(candidate, "parameter"), param):
            return candidate
    return None


def _check_same_track(ctx, clip_track, param):
    param_track = owner_track(param)
    if param_track is not None and not _same(param_track, clip_track):
        raise BridgeError("bad_args", "the parameter is on track %r but the clip is on %r — a "
                          "clip can only automate parameters of its own track"
                          % (_name(param_track), _name(clip_track)))


def ensure_envelope(ctx, clip, param, clear=False):
    """The clip's envelope for ``param``, created when missing.

    Returns ``(envelope, created)``.
    """
    envelope = get_envelope(clip, param)
    if clear and envelope is not None and is_arrangement(clip):
        # Live cannot create a new envelope on an arrangement clip, so "clear"
        # empties the existing one instead of deleting it.
        _delete_all_events(envelope)
        return envelope, False
    if clear and envelope is not None and compat.has(clip, "clear_envelope"):
        try:
            clip.clear_envelope(param)
        except Exception as error:
            raise BridgeError("invalid_state", "Live refused clear_envelope: %s" % error)
        envelope = get_envelope(clip, param)
    if envelope is not None:
        return envelope, False
    if not compat.has(clip, "create_automation_envelope"):
        raise BridgeError("unsupported", "this Live version cannot create clip envelopes")
    try:
        envelope = clip.create_automation_envelope(param)
    except Exception as error:
        if is_arrangement(clip):
            raise BridgeError("unsupported", _ARRANGEMENT_HINT)
        envelope = get_envelope(clip, param)
        if envelope is None:
            raise BridgeError("invalid_state", "Live could not create an envelope for %r: %s"
                              % (_name(param), error))
        return envelope, False
    if envelope is None:
        raise BridgeError("unsupported" if is_arrangement(clip) else "invalid_state",
                          _ARRANGEMENT_HINT if is_arrangement(clip)
                          else "Live returned no envelope for %r" % _name(param))
    return envelope, True


#: ``events_in_range`` / ``delete_events_in_range`` raise "Range out of bounds." beyond
#: +-1576800 beats on Live 12.4.5 (the arrangement's time limit).
_ENVELOPE_LIMIT = 1576800.0


def _delete_all_events(envelope):
    if not compat.has(envelope, "delete_events_in_range"):
        raise BridgeError("unsupported", "this Live version cannot empty an envelope")
    try:
        envelope.delete_events_in_range(-_ENVELOPE_LIMIT, _ENVELOPE_LIMIT)
    except Exception as error:
        raise BridgeError("invalid_state", "Live refused delete_events_in_range: %s" % error)


def _insert_steps(envelope, steps):
    for start, length, value in steps:
        try:
            envelope.insert_step(float(start), float(length), float(value))
        except Exception as error:
            raise BridgeError("invalid_state", "Live refused insert_step(%s, %s, %s): %s"
                              % (rnd(start), rnd(length), rnd(value), error))


def _create_events(envelope, points, start, end):
    event_class = compat.live_enum("Envelope.EnvelopeEvent")
    if event_class is None or not compat.has(envelope, "create_event"):
        raise BridgeError("unsupported", "mode='events' needs Live 12's Envelope.create_event; "
                          "use mode='linear'")
    if compat.has(envelope, "delete_events_in_range"):
        try:
            envelope.delete_events_in_range(float(start), float(end))
        except Exception as error:
            raise BridgeError("invalid_state", "Live refused delete_events_in_range: %s" % error)
    for time, value in points:
        try:
            envelope.create_event(event_class(float(time), float(value)))
        except Exception as error:
            raise BridgeError("invalid_state", "Live refused create_event(%s, %s): %s"
                              % (rnd(time), rnd(value), error))


#: Envelopes are read this far after each sample time.  On Live 12.4.5
#: ``value_at_time`` at a breakpoint that has two events (every step border
#: written by ``insert_step``) returns the value *before* the border, so a
#: sample taken exactly on a step start would show the previous step.
_SAMPLE_EPS = 1e-4


def _sample(envelope, times):
    values = []
    for t in times:
        try:
            values.append(rnd(envelope.value_at_time(float(t) + _SAMPLE_EPS), 5))
        except Exception:
            values.append(None)
    return values


def _event_rows(envelope, begin, finish):
    """``[[time, value], ...]`` breakpoints of ``envelope`` in [begin, finish].

    Live 12.4.5 reports ``EnvelopeEvent.value`` in internal units (linear
    gain for volume/sends, Hz for frequencies, ...), not in the parameter's
    value range, so each value is read back with ``value_at_time`` instead: a
    step border has two events at the same time — the first is the value
    before the border, the second the value after it.
    """
    ok, events = compat.safe_call(envelope, "events_in_range", float(begin), float(finish))
    if not ok:
        return None
    times = [compat.safe_getattr(e, "time") for e in list(events or ())[:_MAX_POINTS]]
    rows = []
    for index, time in enumerate(times):
        if time is None:
            continue
        time = float(time)
        before = index > 0 and times[index - 1] is not None and \
            abs(float(times[index - 1]) - time) < 1e-9
        after = index + 1 < len(times) and times[index + 1] is not None and \
            abs(float(times[index + 1]) - time) < 1e-9
        probe = time + _SAMPLE_EPS if before and not after else time
        if after and not before:
            probe = time - _SAMPLE_EPS if time - _SAMPLE_EPS >= 0 else time
        try:
            value = envelope.value_at_time(probe)
        except Exception:
            value = None
        rows.append([rnd(time), rnd(value, 6) if value is not None else None])
    return rows


def _prepare(ctx, track, slot, clip, parameter, device):
    clip_obj = resolve_clip(ctx, track, slot, clip)
    track_obj = _clip_track(ctx, clip_obj, track)
    param, label, path = resolve_parameter(ctx, track_obj, parameter, device)
    _check_same_track(ctx, track_obj, param)
    return clip_obj, track_obj, param, label, path


def _loop_warning(clip, start, stop):
    """``{"clip_range", "written", "note"}`` when ``start..stop`` leaves what the clip plays."""
    loop_start, loop_end = clip_range(clip)
    if stop <= loop_end + 1e-6 and start >= loop_start - 1e-6:
        return None
    return {"clip_range": [rnd(loop_start), rnd(loop_end)], "written": [rnd(start), rnd(stop)],
            "note": "part of this automation lies outside the clip's loop/region and is never "
                    "heard — pass extend_clip=true to grow the clip to the written range, or "
                    "write inside %s..%s" % (rnd(loop_start), rnd(loop_end))}


def _extend_for(ctx, clip, stop):
    """Grow the clip (whole bars) so it plays up to ``stop``; the new length or None."""
    return clips_handlers.extend_clip_to(clip, stop, ctx.song)


def _write_result(ctx, clip, envelope, param, label, path, created, mode, steps, start, end,
                  converter, extended=None):
    values = [s[2] for s in steps] if steps else []
    span = end - start
    checks = [start + span * k / 8.0 for k in range(8)]
    result = {"clip": clip_info(ctx, clip), "parameter": param_info(param, label, path, True),
              "mode": mode, "created": created, "steps": len(steps),
              "range": [rnd(start), rnd(end)],
              "check": [[rnd(t), v] for t, v in zip(checks, _sample(envelope, checks))]}
    if values:
        result["values"] = {"min": rnd(min(values), 6), "max": rnd(max(values), 6)}
    if converter.clamped:
        result["clamped"] = converter.clamped
    if extended is not None:
        result["extended_to"] = extended
    warning = _loop_warning(clip, start, end)
    if warning is not None:
        result["outside_loop"] = warning
    return result


def _parse_points(clip, points, converter):
    if not isinstance(points, (list, tuple)) or not points:
        raise BridgeError("bad_args", "points must be a non-empty list of {time, value} or "
                          "[time, value]")
    if len(points) > _MAX_POINTS:
        raise BridgeError("bad_args", "at most %d points per call" % _MAX_POINTS)
    parsed = {}
    for index, point in enumerate(points):
        if isinstance(point, dict):
            if "time" not in point or "value" not in point:
                raise BridgeError("bad_args", "points[%d] needs time and value" % index)
            time, value = point["time"], point["value"]
        elif isinstance(point, (list, tuple)) and len(point) == 2:
            time, value = point
        else:
            raise BridgeError("bad_args", "points[%d] must be {time, value} or [time, value]"
                              % index)
        beats = timing.parse_time(clip, time, "points[%d].time" % index)
        parsed[round(beats, 9)] = converter(value)
    return sorted(parsed.items())


def build_steps(points, end, mode, resolution, converter):
    """Steps ``(start, length, value)`` from sorted ``(time, value)`` points."""
    steps = []
    for index, (time, value) in enumerate(points):
        if index + 1 < len(points):
            next_time, next_value = points[index + 1]
            length = next_time - time
            if mode == "linear" and next_value != value and resolution > 1:
                for k in range(resolution):
                    level = converter.fit(value + (next_value - value) * k / float(resolution))
                    steps.append((time + length * k / float(resolution),
                                  length / float(resolution), level))
                continue
            steps.append((time, length, value))
        else:
            length = end - time if end > time + 1e-9 else timing.grid(4, 4)[2]
            steps.append((time, length, value))
    merged = []
    for step in steps:
        if merged and merged[-1][2] == step[2] and \
                abs(merged[-1][0] + merged[-1][1] - step[0]) < 1e-9:
            merged[-1] = (merged[-1][0], merged[-1][1] + step[1], step[2])
        else:
            merged.append(step)
    if len(merged) > _MAX_STEPS:
        raise BridgeError("bad_args", "%d steps is too many (max %d) — lower resolution or "
                          "use fewer points" % (len(merged), _MAX_STEPS))
    return merged


def _lfo_unit(shape, pos):
    """0..1 level of an LFO shape at cycle position ``pos`` (0..1, 1 = end of a cycle)."""
    if shape == "sine":
        return 0.5 + 0.5 * math.sin(2.0 * math.pi * pos)
    if shape == "triangle":
        return 1.0 - abs(2.0 * pos - 1.0)
    if shape == "saw_up":
        return pos
    return 1.0 - pos


def _cycle_count(start, end, origin, period):
    """Cycles (starting at ``origin``) that touch ``start..end``; bad_args when too many."""
    count = int(math.ceil((end - origin) / period - 1e-9))
    if count < 1:
        count = 1
    if count * 2 > _MAX_STEPS:
        raise BridgeError("bad_args", "%d cycles is too many (max %d) — use a longer period "
                          "or fewer cycles" % (count, _MAX_STEPS // 2))
    return count


def shape_steps(shape, start, end, low, high, period, phase, step, duty, curve, rng):
    """Steps ``(start, length, value)`` for a named shape between native ``low``/``high``.

    Raises ``bad_args`` (instead of looping or silently stopping early) when the
    shape would need more than ``_MAX_STEPS`` steps.
    """
    span = end - start
    steps = []
    if shape == "square":
        origin = start - (phase % 1.0) * period
        for cycle in range(_cycle_count(start, end, origin, period)):
            begin = origin + cycle * period
            for seg_start, seg_end, level in ((begin, begin + duty * period, high),
                                              (begin + duty * period, begin + period, low)):
                a, b = max(seg_start, start), min(seg_end, end)
                if b - a > 1e-9:
                    steps.append((a, b - a, level))
        return steps
    count = int(math.ceil(span / step - 1e-9))
    if count > _MAX_STEPS:
        raise BridgeError("bad_args", "%d steps is too many (max %d) — use a larger step"
                          % (count, _MAX_STEPS))
    for k in range(count):
        t = start + k * step
        length = min(step, end - t)
        if shape in ("ramp_up", "ramp_down"):
            frac = k / float(count - 1) if count > 1 else 1.0
            frac = frac ** curve
            level = low + (high - low) * (frac if shape == "ramp_up" else 1.0 - frac)
        elif shape == "random":
            level = low + (high - low) * rng.random()
        else:
            pos = ((t - start) / period + phase) % 1.0
            level = low + (high - low) * (_lfo_unit(shape, pos) ** curve)
        steps.append((t, length, level))
    return steps


def shape_points(shape, start, end, low, high, period, phase, step, duty, curve, rng):
    """Breakpoints ``[(time, value)]`` for ``mode="events"`` (Live interpolates linearly
    between them, so ramps and LFOs come out smooth instead of as a staircase).

    Straight segments get only their corners (a linear ramp is 2 events, a
    triangle 2 per cycle); curved ones (sine, ``curve != 1``) are sampled
    every ``step``.  A jump (square edge, saw reset, random step) is two
    events at the same time — Live's own way to draw a vertical edge.
    """
    points = []

    def add(t, value):
        if points and abs(points[-1][0] - t) < 1e-9 and abs(points[-1][1] - value) < 1e-12:
            return
        points.append((t, value))

    if shape in ("square", "random"):
        for t, length, value in shape_steps(shape, start, end, low, high, period, phase, step,
                                            duty, curve, rng):
            add(t, value)
            add(t + length, value)
    elif shape in ("ramp_up", "ramp_down"):
        count = 1 if abs(curve - 1.0) < 1e-9 else max(2, int(math.ceil((end - start) / step
                                                                       - 1e-9)))
        if count > _MAX_POINTS:
            raise BridgeError("bad_args", "%d breakpoints is too many (max %d) — use a larger "
                              "step" % (count, _MAX_POINTS))
        for k in range(count + 1):
            frac = (k / float(count)) ** curve
            add(start + (end - start) * k / float(count),
                low + (high - low) * (frac if shape == "ramp_up" else 1.0 - frac))
    else:
        origin = start - (phase % 1.0) * period
        straight = abs(curve - 1.0) < 1e-9 and shape != "sine"
        corners = {"triangle": (0.0, 0.5, 1.0), "saw_up": (0.0, 1.0),
                   "saw_down": (0.0, 1.0)}.get(shape)
        per_cycle = max(2, int(math.ceil(period / step - 1e-9)))
        positions = corners if straight else [k / float(per_cycle)
                                              for k in range(per_cycle + 1)]
        cycles = _cycle_count(start, end, origin, period)
        if cycles * len(positions) > _MAX_POINTS:
            raise BridgeError("bad_args", "%d breakpoints is too many (max %d) — use a larger "
                              "step or period" % (cycles * len(positions), _MAX_POINTS))

        def level(pos):
            return low + (high - low) * (_lfo_unit(shape, pos) ** curve)

        for cycle in range(cycles):
            begin = origin + cycle * period
            row = [(begin + pos * period, pos) for pos in positions]
            for index, (t, pos) in enumerate(row):
                if t < start - 1e-9:
                    following = row[index + 1] if index + 1 < len(row) else None
                    if following is not None and following[0] > start + 1e-9:
                        add(start, level(pos + (following[1] - pos) * (start - t)
                                         / (following[0] - t)))
                    continue
                if t > end + 1e-9:
                    previous = row[index - 1] if index > 0 else None
                    if previous is not None and previous[0] < end - 1e-9:
                        add(end, level(previous[1] + (pos - previous[1]) * (end - previous[0])
                                       / (t - previous[0])))
                    break
                add(t, level(pos))
    if len(points) > _MAX_POINTS:
        raise BridgeError("bad_args", "%d breakpoints is too many (max %d)"
                          % (len(points), _MAX_POINTS))
    return points


def _check_resolution(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise BridgeError("bad_args", "%s must be an integer %d..%d" % (name, low, high))
    return value


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def _envelope_rows(ctx, clip, entries, sample=0):
    rows = []
    if compat.safe_getattr(clip, "has_envelopes", True) is False:
        return rows
    found = []
    for param, label, path in entries:
        envelope = get_envelope(clip, param)
        if envelope is not None:
            found.append((param, label, path, envelope))
    for envelope in compat.safe_getattr(clip, "automation_envelopes", ()) or ():
        param = compat.safe_getattr(envelope, "parameter")
        if param is not None and not any(_same(param, f[0]) for f in found):
            found.append((param, _entry_for(ctx, None, param, [])[1], ctx.path_of(param),
                          envelope))
    start, end = clip_range(clip)
    for param, label, path, envelope in found:
        row = param_info(param, label, path)
        if sample:
            times = [start + (end - start) * k / float(sample) for k in range(sample)]
            row["values"] = _sample(envelope, times)
        rows.append(row)
    return rows


@command("automation.list", doc="Which parameters have envelopes in a clip (+ what could "
                                "be automated)")
def automation_list(ctx, track=None, slot=None, clip=None, sample=0, include_available=False,
                    filter=None, limit=200):
    """List the automation envelopes of one clip.

    Args:
        track, slot: session clip address (track index/name/path + scene
            index/name); or ``clip``: a clip / clip slot / arrangement clip
            path, a clip name, or "selected".
        sample: also return N values per envelope, sampled evenly over the
            clip's loop (0 = no values).
        include_available: also list the parameters that *could* be
            automated (devices incl. nested racks + mixer volume/pan/sends).
        filter: case-insensitive substring filter for ``available``.
        limit: max rows in ``available``.

    Returns:
        {"clip": {"path", "name", "arrangement"?}, "range": [start, end],
         "envelopes": [{"name", "device", "path", "values"?}], "count",
         "available"?: [{"name", "device", "path"}], "note"?}

    Gotchas:
        Arrangement clips never report envelopes (Live's API returns None).
        Mixer parameters show ``device: "Mixer"``.
    """
    clip_obj = resolve_clip(ctx, track, slot, clip)
    track_obj = _clip_track(ctx, clip_obj, track)
    sample = _check_resolution(sample, "sample", 0, 256)
    limit = _check_resolution(limit, "limit", 1, 5000)
    entries = track_parameters(ctx, track_obj)
    rows = _envelope_rows(ctx, clip_obj, entries, sample)
    start, end = clip_range(clip_obj)
    result = {"clip": clip_info(ctx, clip_obj), "range": [rnd(start), rnd(end)],
              "envelopes": rows, "count": len(rows)}
    if include_available:
        needle = str(filter).lower() if filter else None
        available = [param_info(p, label, path) for p, label, path in entries
                     if needle is None or needle in (label + " " + _name(p)).lower()]
        result["available"] = available[:limit]
        if len(available) > len(result["available"]):
            result["available_total"] = len(available)
    if is_arrangement(clip_obj):
        result["note"] = _ARRANGEMENT_HINT
    return result


@command("automation.overview", doc="One-call automation overview of a track: envelopes per "
                                    "clip, automated parameters, re-enable state")
def automation_overview(ctx, track, include_empty=False):
    """Everything automation-related on one track.

    Args:
        track: index, name or path.
        include_empty: also list clips without envelopes.

    Returns:
        {"track": {"name", "path"}, "clip_count",
         "clips": [{"slot"|"arrangement_index", "name", "path",
                    "envelopes": [{"name", "device", "path"}]}],
         "automated": [{"name", "device", "path", "state"}],
         "song": {"re_enable_automation_enabled", "session_automation_record"},
         "note"?}

    Gotchas:
        ``automated`` lists parameters whose ``automation_state`` is
        "playing" or "overridden" — the only trace of arrangement track
        automation the API gives.  "overridden" means a manual change
        disabled the automation: re-enable with ``automation.re_enable``.
    """
    track_obj = ctx.track(track)
    entries = track_parameters(ctx, track_obj)
    clips = []
    for index, slot in enumerate(compat.safe_getattr(track_obj, "clip_slots", ()) or ()):
        clip_obj = compat.safe_getattr(slot, "clip")
        if clip_obj is not None:
            clips.append(("slot", index, clip_obj))
    for index, clip_obj in enumerate(compat.safe_getattr(track_obj, "arrangement_clips", ())
                                     or ()):
        clips.append(("arrangement_index", index, clip_obj))
    rows = []
    for key, index, clip_obj in clips:
        envelopes = _envelope_rows(ctx, clip_obj, entries)
        if envelopes or include_empty:
            rows.append({key: index, "name": _name(clip_obj), "path": ctx.path_of(clip_obj),
                         "envelopes": envelopes})
    automated = []
    for param, label, path in entries:
        state = _state(param)
        if state != "none":
            row = param_info(param, label, path)
            row["state"] = state
            automated.append(row)
    song = ctx.song
    result = {"track": {"name": _name(track_obj), "path": ctx.path_of(track_obj)},
              "clip_count": len(clips), "clips": rows, "automated": automated,
              "song": {"re_enable_automation_enabled": bool(compat.safe_getattr(
                  song, "re_enable_automation_enabled", False)),
                  "session_automation_record": bool(compat.safe_getattr(
                      song, "session_automation_record", False))}}
    if any(key == "arrangement_index" for key, _i, _c in clips):
        result["note"] = _ARRANGEMENT_HINT
    return result


@command("automation.get", doc="Read one envelope of a clip, sampled at N points or a beat grid")
def automation_get(ctx, parameter, track=None, slot=None, clip=None, device=None, start=None,
                   end=None, points=16, step=None, display=False, include_events=False):
    """Sample a clip envelope with ``value_at_time``.

    Args:
        parameter: name ("Filter Freq", "Operator > Filter Freq"), mixer
            alias ("volume", "pan", "send A", "send Reverb", "track on"),
            index with ``device``, or a LOM path.
        track, slot / clip: the clip (see ``automation.list``).
        device: device index/name/path to disambiguate ``parameter``.
        start, end: window in clip beats or "bars.beats.sixteenths" (clip
            signature; default: the clip loop, or its start..end when unlooped).
        points: number of samples, evenly spaced from ``start`` (2..2048).
        step: sample every ``step`` beats instead ("1/16", 0.25, "1 bar").
        display: also return the display strings ("-6.0 dB").
        include_events: also return the breakpoints (Live 12
            ``events_in_range``) as [[time, value], ...] — values in the
            parameter's range like ``values``; a step border appears as two
            breakpoints at the same time (value before, value after).

    Returns:
        {"clip", "parameter": {"name", "device", "path", "min", "max"},
         "has_envelope", "start", "step", "values": [...], "display"?,
         "events"?, "state"} — sample k is the value from ``start + k*step``
        on (read a hair after that time, so a step that begins exactly there
        is what you see).
        Without an envelope: {"has_envelope": false, "value": current value}.

    Gotchas:
        Outside the written range the envelope keeps the value it had before
        (for a new envelope: the parameter's value when it was created).
    """
    clip_obj, _track, param, label, path = _prepare(ctx, track, slot, clip, parameter, device)
    begin, finish = _window(clip_obj, start, end)
    if step is not None:
        spacing = timing.parse_length(clip_obj, step, "step")
        count = int(math.ceil((finish - begin) / spacing - 1e-9))
    else:
        count = _check_resolution(points, "points", 1, _MAX_POINTS)
        spacing = (finish - begin) / float(count)
    if count > _MAX_POINTS:
        raise BridgeError("bad_args", "%d samples is too many (max %d)" % (count, _MAX_POINTS))
    result = {"clip": clip_info(ctx, clip_obj), "parameter": param_info(param, label, path, True),
              "state": _state(param)}
    envelope = get_envelope(clip_obj, param)
    if envelope is None:
        value = compat.safe_getattr(param, "value", 0.0)
        result.update({"has_envelope": False, "value": rnd(value, 6),
                       "display_value": _display(param, value)})
        if is_arrangement(clip_obj):
            result["note"] = _ARRANGEMENT_HINT
        return result
    times = [begin + spacing * k for k in range(count)]
    values = _sample(envelope, times)
    result.update({"has_envelope": True, "start": rnd(begin), "end": rnd(finish),
                   "step": rnd(spacing, 6), "values": values})
    if display:
        result["display"] = [_display(param, v) if v is not None else None for v in values]
    if include_events:
        if not compat.has(envelope, "events_in_range"):
            result["events"] = None
        else:
            result["events"] = _event_rows(envelope, begin, finish)
    return result


@command("automation.write", mutating=True,
         doc="Write a clip envelope from points [{time, value}] as steps or linear ramps")
def automation_write(ctx, parameter, points, track=None, slot=None, clip=None, device=None,
                     mode="step", resolution=16, end=None, normalized=False, clear=False,
                     extend_clip=False):
    """Draw automation into a clip envelope.

    Args:
        parameter, device: what to automate (see ``automation.get``).
        points: [{"time": t, "value": v}, ...] or [[t, v], ...]; time in
            clip beats or "bars.beats.sixteenths"; value as the parameter's
            internal number, a display string ("-6 dB", "1.2 kHz", "25L",
            "50 %"), a value item ("Saw") or a bool.
        track, slot / clip: the clip.
        mode: "step" (hold each value until the next point), "linear"
            (ramps between points, drawn as ``resolution`` steps per
            segment) or "events" (Live 12 breakpoints via create_event —
            true linear ramps, newer API).
        resolution: steps per segment for mode="linear" (1..256).
        end: where the last point's value stops (default: clip end).
        normalized: numbers are 0..1 of the parameter range.
        clear: remove the existing envelope of this parameter first.
        extend_clip: grow the clip (whole bars, like notes.add extend) when
            the written range reaches past its loop end — otherwise that part
            is stored but never heard.

    Returns:
        {"clip", "parameter", "mode", "created", "steps", "range",
         "values": {"min", "max"}, "check": [[time, value] x8], "clamped"?,
         "extended_to"?, "outside_loop"?: {"clip_range", "written", "note"}}

    Gotchas:
        Envelope times are clip-local and follow the clip loop (Live's
        separate "unlinked" envelope length is not in the API): a point past
        the loop end is kept but never plays — ``outside_loop`` says so, and
        ``extend_clip=true`` grows the loop instead.  Session clips get new
        envelopes; an arrangement clip can only edit envelopes it already has
        — arrangement clips return None from ``automation_envelope`` and an
        arrangement copy may carry no envelopes at all (12.4.5 check, Session
        view focused, instrument on the track) — otherwise ``unsupported``.
        ``insert_step`` replaces what was in each step's range; existing
        breakpoints outside the written range stay unless ``clear``.
        Out-of-range values are clamped (``clamped`` counts them).
    """
    mode = str(mode).lower()
    if mode not in ("step", "linear", "events"):
        raise BridgeError("bad_args", "mode must be step, linear or events")
    resolution = _check_resolution(resolution, "resolution", 1, 256)
    clip_obj, _track, param, label, path = _prepare(ctx, track, slot, clip, parameter, device)
    converter = _Converter(param, bool(normalized))
    parsed = _parse_points(clip_obj, points, converter)
    _begin, default_end = clip_range(clip_obj)
    finish = default_end if end is None else timing.parse_time(clip_obj, end, "end")
    envelope, created = ensure_envelope(ctx, clip_obj, param, bool(clear))
    start = parsed[0][0]
    if mode == "events":
        _create_events(envelope, parsed, start, max(finish, parsed[-1][0]))
        steps = [(t, 0.0, v) for t, v in parsed]
    else:
        steps = build_steps(parsed, finish, mode, resolution, converter)
        _insert_steps(envelope, steps)
    stop = max(finish, steps[-1][0] + steps[-1][1])
    extended = _extend_for(ctx, clip_obj, stop) if as_flag(extend_clip, "extend_clip") \
        else None
    return _write_result(ctx, clip_obj, envelope, param, label, path, created, mode, steps,
                         start, stop, converter, extended)


def as_flag(value, name):
    """A JSON boolean argument (``bad_args`` otherwise)."""
    if isinstance(value, bool):
        return value
    raise BridgeError("bad_args", "%s must be true or false" % name)


def _write_events_guarded(envelope, points, start, end):
    """Replace the breakpoints in ``start..end`` by ``points`` and keep the values
    just outside the range (a vertical edge at each end instead of a ramp into the
    old automation)."""
    try:
        before = envelope.value_at_time(float(start))
        after = envelope.value_at_time(float(end) + _SAMPLE_EPS)
    except Exception:
        before = after = None
    guarded = list(points)
    if before is not None and guarded and abs(guarded[0][0] - start) < 1e-9 and \
            abs(before - guarded[0][1]) > 1e-12 and start > 1e-9:
        guarded.insert(0, (start, before))
    if after is not None and guarded and abs(guarded[-1][0] - end) < 1e-9 and \
            abs(after - guarded[-1][1]) > 1e-12:
        guarded.append((end, after))
    _create_events(envelope, guarded, start, end)


def _split_steps(steps, begin, end):
    """The part of ``steps`` inside ``begin..end`` (cut at the borders)."""
    out = []
    for t, length, value in steps:
        a, b = max(t, begin), min(t + length, end)
        if b - a > 1e-9:
            out.append((a, b - a, value))
    return out


def _split_points(points, begin, end):
    """The breakpoints inside ``begin..end`` plus interpolated border points."""

    def value_at(t, left):
        for index, (pt, pv) in enumerate(points):
            if abs(pt - t) < 1e-9:
                if left:
                    return pv
                same = [v for tt, v in points if abs(tt - t) < 1e-9]
                return same[-1]
            if pt > t:
                if index == 0:
                    return pv
                qt, qv = points[index - 1]
                return qv + (pv - qv) * (t - qt) / (pt - qt) if pt > qt else pv
        return points[-1][1]

    inside = [(t, v) for t, v in points if begin + 1e-9 < t < end - 1e-9]
    return [(begin, value_at(begin, False))] + inside + [(end, value_at(end, True))]


@command("automation.shape", mutating=True,
         doc="Write a shape (ramp, sine/triangle/saw LFO, square, seeded random) into a clip "
             "envelope, or one continuous shape across several clips")
def automation_shape(ctx, parameter, shape="sine", track=None, slot=None, clip=None,
                     device=None, start=None, end=None, low=0.0, high=1.0, normalized=True,
                     period=None, cycles=None, phase=0.0, step=None, duty=0.5, curve=1.0,
                     seed=None, clear=False, mode="step", slots=None, extend_clip=False):
    """Generate automation over a time range of a clip — or across several clips.

    Args:
        parameter, device, track, slot / clip: see ``automation.write``.
        shape: ramp_up, ramp_down, sine, triangle, saw_up, saw_down, square,
            random (aliases: ramp, fade_in, fade_out, saw, lfo, noise, s&h).
        start, end: range (default: the clip loop, or its start..end when unlooped).
        low, high: value range; by default normalized 0..1 of the
            parameter's range (``normalized=false`` for internal values);
            display strings work too ("-24 dB", "0 dB").
        period: cycle length for LFO shapes — beats, "1/4", "1/8T",
            "1 bar", "2 bars", "1.0.0" (default: 1 bar of the clip); at least
            1/64 beat.
        cycles: number of cycles across the range (instead of period).
        phase: 0..1 cycle offset (0.25 starts a sine at its peak).
        step: resolution (default: period/16 for LFOs, range/128 for ramps,
            1/16 note for random) — the staircase step in mode "step", the
            sampling of curved segments in mode "events".
        duty: high fraction of a square cycle (0..1).
        curve: exponent applied to the 0..1 shape (1 linear, 2 ease-in,
            0.5 ease-out).
        seed: random seed (the used seed is returned so it can be repeated).
        clear: remove the existing envelope of this parameter first.
        mode: "step" (default: a staircase of ``insert_step`` steps) or
            "events" (Live 12 breakpoints with linear interpolation: a linear
            ramp is 2 breakpoints and plays perfectly smooth, LFOs are sampled
            every ``step`` and interpolated — no audible stepping, far
            smaller).  The values just before and after the range are kept.
        slots: a list of scenes (indices/names) of ``track`` — writes ONE
            continuous shape across those clips in order (e.g. a filter sweep
            over scenes 2-5): the range is the clips' loops laid end to end and
            each clip gets its part.  Not combinable with clip/slot/start/end.
        extend_clip: grow the clip when the range reaches past its loop
            (single clip only; see ``automation.write``).

    Returns:
        Same as ``automation.write`` plus {"shape", "period"?, "seed"?} — with
        ``slots``: {"shape", "mode", "total_length", "clips": [{"clip",
        "range", "steps", "values"}], "period"?, "seed"?, "clamped"?}.

    Gotchas:
        At most 2048 steps / breakpoints per clip; a shape that would need
        more fails with bad_args (use a larger step/period) instead of being
        cut short.
    """
    key = _SHAPE_ALIASES.get(str(shape).strip().lower(), str(shape).strip().lower())
    if key not in _SHAPES:
        raise BridgeError("bad_args", "shape must be one of %s" % ", ".join(_SHAPES))
    mode = str(mode).strip().lower()
    if mode not in ("step", "events"):
        raise BridgeError("bad_args", "mode must be step or events")
    for value, name, low_limit, high_limit in ((phase, "phase", -1e6, 1e6),
                                               (duty, "duty", 0.0, 1.0),
                                               (curve, "curve", 0.05, 20.0)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or \
                not low_limit <= value <= high_limit:
            raise BridgeError("bad_args", "%s must be a number %s..%s" % (name, low_limit,
                                                                           high_limit))
    extend_clip = as_flag(extend_clip, "extend_clip")
    if slots is not None:
        targets = _slot_clips(ctx, track, slots, clip, start, end)
        if extend_clip:
            raise BridgeError("bad_args", "extend_clip works with a single clip, not slots")
        first = targets[0]
        track_obj = _clip_track(ctx, first, track)
        param, label, path = resolve_parameter(ctx, track_obj, parameter, device)
        _check_same_track(ctx, track_obj, param)
        windows, total = [], 0.0
        for target in targets:
            clip_start, clip_end = clip_range(target)
            windows.append((target, clip_start, total, clip_end - clip_start))
            total += clip_end - clip_start
        timeline_clip, begin, finish = first, 0.0, total
    else:
        clip_obj, track_obj, param, label, path = _prepare(ctx, track, slot, clip, parameter,
                                                           device)
        begin, finish = _window(clip_obj, start, end)
        timeline_clip = clip_obj
    span = finish - begin
    converter = _Converter(param, bool(normalized))
    low_value, high_value = converter(low), converter(high)
    if cycles is not None:
        if isinstance(cycles, bool) or not isinstance(cycles, (int, float)) or cycles <= 0:
            raise BridgeError("bad_args", "cycles must be a number > 0")
        cycle_len = span / float(cycles)
    elif period is not None:
        cycle_len = timing.parse_length(timeline_clip, period, "period")
    else:
        cycle_len = timing.grid(*timing.signature_of(timeline_clip))[0]
    lfo = key not in ("ramp_up", "ramp_down", "random")
    if lfo and cycle_len < _MIN_CYCLE - 1e-12:
        raise BridgeError("bad_args", "a cycle of %s beats is too short — the shortest LFO "
                          "cycle is 1/64 beat (use a longer period or fewer cycles)"
                          % rnd(cycle_len, 9))
    if step is not None:
        spacing = timing.parse_length(timeline_clip, step, "step")
    elif key in ("ramp_up", "ramp_down"):
        spacing = max(0.0625, span / 128.0)
    elif key == "random":
        spacing = 0.25
    else:
        spacing = max(0.03125, cycle_len / 16.0)
    used_seed = None
    rng = None
    if key == "random":
        if seed is None:
            used_seed = int(_time.time() * 1000) % 1000000
        elif isinstance(seed, bool) or not isinstance(seed, (int, str)):
            raise BridgeError("bad_args", "seed must be an integer or a string")
        else:
            used_seed = seed
        rng = random.Random(used_seed)
    if mode == "events":
        shaped = shape_points(key, begin, finish, low_value, high_value, cycle_len,
                              float(phase), spacing, float(duty), float(curve), rng)
        shaped = [(t, converter.fit(v)) for t, v in shaped]
    else:
        shaped = shape_steps(key, begin, finish, low_value, high_value, cycle_len,
                             float(phase), spacing, float(duty), float(curve), rng)
        shaped = [(t, length, converter.fit(v)) for t, length, v in shaped]
    if not shaped:
        raise BridgeError("bad_args", "the range produced no steps")
    if len(shaped) > _MAX_STEPS:
        raise BridgeError("bad_args", "too many steps (max %d) — use a larger step or period"
                          % _MAX_STEPS)
    extra = {"shape": key}
    if lfo:
        extra["period"] = rnd(cycle_len)
    if used_seed is not None:
        extra["seed"] = used_seed
    if slots is not None:
        rows = []
        for target, clip_start, offset, length in windows:
            if mode == "events":
                part = _split_points(shaped, offset, offset + length)
                local = [(clip_start + t - offset, v) for t, v in part]
            else:
                part = _split_steps(shaped, offset, offset + length)
                local = [(clip_start + t - offset, n, v) for t, n, v in part]
            envelope, _created = ensure_envelope(ctx, target, param, bool(clear))
            if mode == "events":
                _write_events_guarded(envelope, local, clip_start, clip_start + length)
                values = [v for _t, v in local]
            else:
                _insert_steps(envelope, local)
                values = [v for _t, _n, v in local]
            rows.append({"clip": clip_info(ctx, target),
                         "range": [rnd(clip_start), rnd(clip_start + length)],
                         "steps": len(local),
                         "values": {"min": rnd(min(values), 6), "max": rnd(max(values), 6)}})
        result = {"parameter": param_info(param, label, path, True), "mode": mode,
                  "total_length": rnd(total), "clips": rows}
        if converter.clamped:
            result["clamped"] = converter.clamped
        result.update(extra)
        return result
    envelope, created = ensure_envelope(ctx, clip_obj, param, bool(clear))
    if mode == "events":
        _write_events_guarded(envelope, shaped, begin, finish)
        steps = [(t, 0.0, v) for t, v in shaped]
    else:
        _insert_steps(envelope, shaped)
        steps = shaped
    extended = _extend_for(ctx, clip_obj, finish) if extend_clip else None
    result = _write_result(ctx, clip_obj, envelope, param, label, path, created, mode,
                           steps, begin, finish, converter, extended)
    result.update(extra)
    return result


def _slot_clips(ctx, track, slots, clip, start, end):
    """The clips of ``track`` in ``slots`` (in the given order) for a multi-clip shape."""
    if clip is not None or start is not None or end is not None:
        raise BridgeError("bad_args", "slots writes across whole clips — do not combine it "
                          "with clip, start or end")
    if track is None:
        raise BridgeError("bad_args", "slots needs track")
    if not isinstance(slots, (list, tuple)) or len(slots) < 1:
        raise BridgeError("bad_args", "slots must be a non-empty list of scene indices/names")
    if len(slots) > 64:
        raise BridgeError("bad_args", "at most 64 slots per call")
    targets = []
    for spec in slots:
        target = resolve_clip(ctx, track, spec, None)
        if any(_same(target, other) for other in targets):
            raise BridgeError("bad_args", "slot %r is listed twice" % (spec,))
        targets.append(target)
    return targets


@command("automation.copy", mutating=True,
         doc="Copy one parameter's envelope from a clip to other clips (optional shift/scale)")
def automation_copy(ctx, parameter, targets, track=None, slot=None, clip=None, device=None,
                    shift=0.0, scale=1.0, target_parameter=None, clear=True):
    """Copy an envelope between clips, breakpoint for breakpoint.

    Args:
        parameter, device: the envelope to copy (see ``automation.get``).
        track, slot / clip: the source clip.
        targets: destination clips — a list of scene indices/names (clips of
            the source track) and/or clip paths / names (any track: the
            parameter is then looked up by name on that track, or pass
            ``target_parameter``).
        shift: move the copy by this many beats (clip time; may be negative).
        scale: stretch time by this factor around the source range start
            (2 = twice as slow, 0.5 = twice as fast).
        target_parameter: parameter name/alias/path on the target clips'
            track (default: the same parameter, or one with the same name on
            another track).
        clear: empty the target envelope first (default); false keeps what
            lies outside the copied range.

    Returns:
        {"source": {"clip", "parameter", "events"}, "copied": [{"clip",
         "parameter", "events", "range", "created"}], "count"}

    Gotchas:
        Uses Live 12's ``events_in_range`` / ``create_event``; the copy's
        breakpoint values are read with ``value_at_time`` (Live reports raw
        event values in internal units).  Envelopes can be copied into
        arrangement clips only when they already have that envelope.
    """
    source_clip, source_track, param, label, path = _prepare(ctx, track, slot, clip,
                                                             parameter, device)
    source_env = get_envelope(source_clip, param)
    if source_env is None:
        raise BridgeError("not_found", "%r has no envelope in the source clip" % _name(param))
    if not isinstance(targets, (list, tuple)) or not targets:
        raise BridgeError("bad_args", "targets must be a non-empty list of slots or clips")
    if len(targets) > 64:
        raise BridgeError("bad_args", "at most 64 targets per call")
    for value, name in ((shift, "shift"), (scale, "scale")):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise BridgeError("bad_args", "%s must be a number" % name)
    if not 0.01 <= scale <= 100:
        raise BridgeError("bad_args", "scale must be 0.01..100")
    clear = as_flag(clear, "clear")
    begin, finish = clip_range(source_clip)
    rows = _event_rows(source_env, -_ENVELOPE_LIMIT, _ENVELOPE_LIMIT)
    if rows is None:
        raise BridgeError("unsupported", "copying envelopes needs Live 12's events_in_range")
    rows = [(t, v) for t, v in rows if v is not None]
    if not rows:
        raise BridgeError("invalid_state", "the source envelope has no breakpoints")
    origin = rows[0][0]
    mapped_source = [(origin + (t - origin) * float(scale) + float(shift), v) for t, v in rows]
    copied = []
    for index, spec in enumerate(targets):
        if isinstance(spec, str) and (spec.strip().startswith("song.") or
                                      spec.strip().lower() == "selected"):
            target_clip = resolve_clip(ctx, None, None, spec.strip())
        elif isinstance(spec, (int, float)) and not isinstance(spec, bool):
            target_clip = resolve_clip(ctx, source_track, spec, None)
        else:
            try:
                target_clip = resolve_clip(ctx, source_track, spec, None)
            except BridgeError:
                target_clip = resolve_clip(ctx, None, None, spec)
        if _same(target_clip, source_clip):
            raise BridgeError("bad_args", "targets[%d] is the source clip" % index)
        target_track = _clip_track(ctx, target_clip)
        if target_parameter is not None:
            t_param, t_label, t_path = resolve_parameter(ctx, target_track, target_parameter)
        elif _same(target_track, source_track):
            t_param, t_label, t_path = param, label, path
        else:
            t_param, t_label, t_path = resolve_parameter(ctx, target_track, _name(param))
        _check_same_track(ctx, target_track, t_param)
        converter = _Converter(t_param, False)
        values = [(t, converter.fit(v)) for t, v in mapped_source if t >= 0.0]
        if not values:
            raise BridgeError("bad_args", "the shift moves the whole envelope before the "
                              "clip start")
        envelope, created = ensure_envelope(ctx, target_clip, t_param, False)
        if clear:
            _delete_all_events(envelope)
            _create_events(envelope, values, values[0][0], values[-1][0])
        else:
            _write_events_guarded(envelope, values, values[0][0], values[-1][0])
        entry = {"clip": clip_info(ctx, target_clip),
                 "parameter": param_info(t_param, t_label, t_path), "events": len(values),
                 "range": [rnd(values[0][0]), rnd(values[-1][0])], "created": created}
        warning = _loop_warning(target_clip, values[0][0], values[-1][0])
        if warning is not None:
            entry["outside_loop"] = warning
        copied.append(entry)
    return {"source": {"clip": clip_info(ctx, source_clip),
                       "parameter": param_info(param, label, path), "events": len(rows)},
            "copied": copied, "count": len(copied)}


# --------------------------------------------------------------------------
# arrangement track automation: record parameter moves (tick driven)
# --------------------------------------------------------------------------

#: The running / last recording (module state; one at a time).
_RECORDER = {"current": None, "last": None, "counter": 0}
#: Ticks to wait for playback to reach the range (count-in included) before giving up.
_RECORD_WAIT_TICKS = 400
#: Longest range one recording may cover (beats) — keeps a mistake from running for hours.
_RECORD_MAX_BEATS = 4096.0


def _curve_value(points, time, mode):
    """Value of a breakpoint list ``[(t, v)]`` at ``time`` ("linear" or "step").

    At a time with two breakpoints (a vertical edge) the later one wins."""
    if time <= points[0][0]:
        return points[0][1]
    if time >= points[-1][0]:
        return points[-1][1]
    index = bisect.bisect_right([t for t, _v in points], time)
    t0, v0 = points[index - 1]
    t1, v1 = points[index]
    if mode == "step" or t1 <= t0:
        return v0
    return v0 + (v1 - v0) * (time - t0) / (t1 - t0)


class _Recording(object):
    """One ``automation.record`` pass, advanced by Live's display tick."""

    def __init__(self, ctx, track_obj, param, label, path, points, mode, start, end, begin):
        _RECORDER["counter"] += 1
        self.id = "rec%d" % _RECORDER["counter"]
        self.ctx = ctx
        self.song = ctx.song
        self.track = track_obj
        self.param = param
        self.info = param_info(param, label, path, True)
        self.track_name = _name(track_obj)
        self.points = points
        self.mode = mode
        self.start = start
        self.end = end
        self.begin = begin
        self.phase = "arming"
        self.outcome = None
        self.error = None
        self.writes = 0
        self.last_value = None
        self.first_time = None
        self.last_time = None
        self.ticks = 0
        self.gesture = False
        self.started_at = _time.time()
        self.saved = {}
        self.disarmed = []

    # -- setup / teardown -------------------------------------------------
    def setup(self):
        song = self.song
        get = compat.safe_getattr
        for prop in ("loop", "punch_in", "punch_out", "session_automation_record",
                     "current_song_time"):
            self.saved[prop] = get(song, prop)
        for candidate in list(get(song, "tracks", ()) or ()):
            if get(candidate, "can_be_armed", False) and get(candidate, "arm", False):
                self.disarmed.append(candidate)
        try:
            for candidate in self.disarmed:
                candidate.arm = False
            for prop, value in (("loop", False), ("punch_in", False), ("punch_out", False),
                                ("session_automation_record", True)):
                if compat.has(song, prop):
                    setattr(song, prop, value)
            song.current_song_time = float(self.begin)
        except Exception as error:
            self.restore()
            raise BridgeError("invalid_state", "Live refused to prepare the recording: %s"
                              % error)

    def restore(self):
        song = self.song
        problems = []
        for prop in ("loop", "punch_in", "punch_out", "session_automation_record"):
            value = self.saved.get(prop)
            if value is not None and compat.has(song, prop):
                try:
                    setattr(song, prop, value)
                except Exception as error:
                    problems.append("%s: %s" % (prop, error))
        for candidate in self.disarmed:
            try:
                if candidate != None:  # noqa: E711  (deleted LOM objects == None)
                    candidate.arm = True
            except Exception as error:
                problems.append("arm %s: %s" % (_name(candidate), error))
        position = self.saved.get("current_song_time")
        if position is not None:
            try:
                song.current_song_time = float(position)
            except Exception as error:
                problems.append("current_song_time: %s" % error)
        if problems:
            self.error = (self.error + "; " if self.error else "") + \
                "could not restore " + ", ".join(problems)

    # -- tick loop ---------------------------------------------------------
    def schedule(self):
        schedule = compat.safe_getattr(self.ctx.script, "schedule_message")
        if schedule is None:
            raise BridgeError("unsupported", "this control surface cannot schedule ticks")
        schedule(1, self.tick)

    def tick(self):
        if self.phase in ("done", "aborted", "failed"):
            return
        self.ticks += 1
        try:
            self._advance()
        except Exception as error:
            self.error = "%s: %s" % (type(error).__name__, error)
            self._finish("failed")
        if self.phase not in ("done", "aborted", "failed"):
            try:
                self.schedule()
            except Exception as error:
                self.error = "cannot schedule the next tick: %s" % error
                self._finish("failed", wait=False)

    def _advance(self):
        song = self.song
        if self.phase == "arming":
            song.record_mode = True
            self.phase = "waiting"
            return
        if self.phase == "waiting":
            playing = bool(compat.safe_getattr(song, "is_playing", False))
            now = float(compat.safe_getattr(song, "current_song_time", 0.0) or 0.0)
            if playing and now >= self.start - 1e-6:
                self.param.begin_gesture()
                self.gesture = True
                self.phase = "recording"
                self._write(now)
            elif self.ticks > _RECORD_WAIT_TICKS:
                self.error = "playback did not reach beat %s" % rnd(self.start)
                self._finish("failed")
            return
        if self.phase == "recording":
            if not compat.safe_getattr(song, "is_playing", False) or \
                    not compat.safe_getattr(song, "record_mode", True):
                self.error = "the transport was stopped before the end of the range"
                self._finish("aborted")
                return
            now = float(compat.safe_getattr(song, "current_song_time", 0.0) or 0.0)
            if now >= self.end - 1e-6:
                self._write(self.end)
                self._finish("done")
                return
            self._write(now)
            return
        if self.phase == "stopping":
            try:
                song.stop_playing()
            finally:
                self.phase = "restoring"
            return
        if self.phase == "restoring":
            self.restore()
            self.phase = self.outcome or "done"

    def _write(self, now):
        value = _curve_value(self.points, min(max(now, self.start), self.end), self.mode)
        if self.last_value is not None and abs(value - self.last_value) < 1e-9:
            return
        self.param.value = value
        self.last_value = value
        self.writes += 1
        if self.first_time is None:
            self.first_time = now
        self.last_time = now

    def _finish(self, outcome, wait=True):
        """End the gesture, leave record mode; stop + restore on the next ticks."""
        self.outcome = outcome
        if self.gesture:
            try:
                self.param.end_gesture()
            except Exception as error:
                self.error = (self.error + "; " if self.error else "") + \
                    "end_gesture: %s" % error
            self.gesture = False
        try:
            self.song.record_mode = False
        except Exception as error:
            self.error = (self.error + "; " if self.error else "") + "record_mode: %s" % error
        if wait:
            self.phase = "stopping"
        else:
            try:
                self.song.stop_playing()
            except Exception:
                pass
            self.restore()
            self.phase = outcome
        if _RECORDER.get("current") is self and self.phase in ("done", "aborted", "failed"):
            _RECORDER["current"] = None

    def abort(self, reason):
        if self.phase in ("done", "aborted", "failed", "stopping", "restoring"):
            return
        self.error = reason
        self._finish("aborted")

    def status(self):
        finished = self.phase in ("done", "aborted", "failed")
        if finished and _RECORDER.get("current") is self:
            _RECORDER["current"] = None
        span = max(self.end - self.start, 1e-9)
        if self.outcome == "done":
            progress = 1.0
        elif self.last_time is not None:
            progress = max(0.0, min(1.0, (self.last_time - self.start) / span))
        else:
            progress = 0.0
        data = {"id": self.id, "phase": self.phase, "finished": finished,
                "track": self.track_name, "parameter": self.info,
                "range": [rnd(self.start), rnd(self.end)], "writes": self.writes,
                "progress": rnd(progress, 3), "ticks": self.ticks,
                "elapsed_s": rnd(_time.time() - self.started_at, 2)}
        if self.first_time is not None:
            data["recorded"] = [rnd(self.first_time), rnd(self.last_time)]
        if self.error:
            data["error"] = self.error
        if finished:
            data["state_after"] = _state(self.param)
            data["restored"] = {"loop": self.saved.get("loop"),
                                "position": rnd(self.saved.get("current_song_time") or 0.0),
                                "rearmed": [_name(t) for t in self.disarmed]}
        return data


def _record_points(ctx, converter, points, shape, start, end, low, high, period, cycles,
                   phase, duty, curve, seed, mode):
    """``(points, mode, start, end, extra)`` for automation.record in song beats."""
    to_beats = clips_handlers.to_beats
    if points is not None and shape is not None:
        raise BridgeError("bad_args", "pass points or shape, not both")
    if points is not None:
        if not isinstance(points, (list, tuple)) or not points:
            raise BridgeError("bad_args", "points must be a non-empty list of {time, value} "
                              "or [time, value] (song beats or \"bars.beats.sixteenths\")")
        if len(points) > _MAX_POINTS:
            raise BridgeError("bad_args", "at most %d points" % _MAX_POINTS)
        parsed = {}
        for index, point in enumerate(points):
            if isinstance(point, dict) and "time" in point and "value" in point:
                time, value = point["time"], point["value"]
            elif isinstance(point, (list, tuple)) and len(point) == 2:
                time, value = point
            else:
                raise BridgeError("bad_args", "points[%d] must be {time, value} or "
                                  "[time, value]" % index)
            beats = to_beats(ctx, time, "beats", "points[%d].time" % index, position=True,
                             allow_none=False, minimum=0.0)
            parsed[round(beats, 9)] = converter(value)
        curve_points = sorted(parsed.items())
        begin = curve_points[0][0] if start is None else \
            to_beats(ctx, start, "beats", "start", position=True, minimum=0.0)
        finish = curve_points[-1][0] if end is None else \
            to_beats(ctx, end, "beats", "end", position=True, minimum=0.0)
        if finish <= begin:
            raise BridgeError("bad_args", "the points must span some time (or pass end)")
        return curve_points, mode, begin, finish, {}
    if start is None or end is None:
        raise BridgeError("bad_args", "a shape needs start and end (song beats or "
                          "\"bars.beats.sixteenths\")")
    begin = to_beats(ctx, start, "beats", "start", position=True, minimum=0.0)
    finish = to_beats(ctx, end, "beats", "end", position=True, minimum=0.0)
    if finish <= begin:
        raise BridgeError("bad_args", "end must be after start")
    key = _SHAPE_ALIASES.get(str(shape).strip().lower(), str(shape).strip().lower())
    if key not in _SHAPES:
        raise BridgeError("bad_args", "shape must be one of %s" % ", ".join(_SHAPES))
    for value, name, low_limit, high_limit in ((phase, "phase", -1e6, 1e6),
                                               (duty, "duty", 0.0, 1.0),
                                               (curve, "curve", 0.05, 20.0)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or \
                not low_limit <= value <= high_limit:
            raise BridgeError("bad_args", "%s must be a number %s..%s"
                              % (name, low_limit, high_limit))
    bar = clips_handlers.beats_per_bar(ctx.song)
    if cycles is not None:
        if isinstance(cycles, bool) or not isinstance(cycles, (int, float)) or cycles <= 0:
            raise BridgeError("bad_args", "cycles must be a number > 0")
        cycle_len = (finish - begin) / float(cycles)
    elif period is not None:
        cycle_len = _song_length_arg(ctx, period, bar, "period")
    else:
        cycle_len = bar
    lfo = key not in ("ramp_up", "ramp_down", "random")
    if lfo and cycle_len < 0.25 - 1e-12:
        raise BridgeError("bad_args", "recording follows Live's ~100 ms display tick: an LFO "
                          "cycle must be at least 1/4 beat")
    rng = None
    extra = {"shape": key}
    if key == "random":
        used = seed if seed is not None else int(_time.time() * 1000) % 1000000
        if isinstance(used, bool) or not isinstance(used, (int, str)):
            raise BridgeError("bad_args", "seed must be an integer or a string")
        rng = random.Random(used)
        extra["seed"] = used
    spacing = max(0.125, cycle_len / 16.0) if lfo else 0.25
    low_value, high_value = converter(low), converter(high)
    curve_points = shape_points(key, begin, finish, low_value, high_value, cycle_len,
                                float(phase), spacing, float(duty), float(curve), rng)
    return curve_points, "linear", begin, finish, extra


def _song_length_arg(ctx, value, bar, name):
    """A length in song beats: beats, "1/4", "1/8T", "1 bar", "2 bars" or "1.0.0"."""
    if shared_resolve.is_bbs(value):
        number = shared_resolve.parse_time(ctx.song, value, name, is_length=True)
        if number <= 0:
            raise BridgeError("bad_args", "%s must be > 0" % name)
        return number
    if isinstance(value, bool):
        raise BridgeError("bad_args", "%s must be a length" % name)
    text = str(value).strip().lower()
    match = re.match(r"^(\d+(?:\.\d+)?)\s*bars?$", text)
    if match:
        return float(match.group(1)) * bar
    match = re.match(r"^1/(\d+)(t)?$", text)
    if match:
        length = 4.0 / int(match.group(1))
        return length * 2.0 / 3.0 if match.group(2) else length
    try:
        number = float(text)
    except ValueError:
        raise BridgeError("bad_args", "%s %r: use beats, \"1/4\", \"1 bar\" or "
                          "\"2 bars\"" % (name, value))
    if number <= 0:
        raise BridgeError("bad_args", "%s must be > 0" % name)
    return number


@command("automation.record", mutating=True,
         doc="Record ARRANGEMENT track automation: play the song through a range while "
             "moving a parameter (Arrangement Record + Automation Arm)")
def automation_record(ctx, parameter=None, track=None, device=None, points=None, shape=None,
                      start=None, end=None, low=0.0, high=1.0, normalized=None, period=None,
                      cycles=None, phase=0.0, duty=0.5, curve=1.0, seed=None, mode="linear",
                      preroll=1.0, action="start"):
    """Write real arrangement (track) automation — the only way through Live's API.

    Live's API cannot draw into an automation lane, so this does what a person
    does: it switches on Arrangement Record and Automation Arm, starts
    playback ``preroll`` beats before ``start`` and, on every display tick
    (~100 ms), moves the parameter to the value the curve has at the current
    song time (``begin_gesture`` ... ``end_gesture``, so Live records one
    touch).  At ``end`` it stops, and restores the loop, punch in/out,
    Automation Arm, armed tracks and the playhead.  The command returns at
    once; poll ``automation.record_status`` (the song plays in real time).

    Args:
        parameter, device, track: what to automate — any track, including
            return tracks and the master ("tempo" = the master's Song Tempo,
            "crossfader", "cue volume"), see ``automation.get``.
        points: [[song_time, value], ...] or [{"time", "value"}] — song beats
            or "bars.beats.sixteenths"; values like ``automation.write``.
        shape: instead of points — ramp_up, ramp_down, sine, triangle,
            saw_up, saw_down, square, random over ``start``..``end`` with
            low/high/normalized/period/cycles/phase/duty/curve/seed as in
            ``automation.shape`` (LFO cycles >= 1/4 beat).
        start, end: range in song beats / "bars.beats.sixteenths" (default
            for points: first..last point).
        normalized: numbers are 0..1 of the parameter range (default: true
            for a shape's low/high, false for points — like automation.shape
            and automation.write).
        mode: how points connect — "linear" (default) or "step".
        preroll: beats of playback before ``start`` (0..16).
        action: "start" (default) or "stop" (abort the running recording;
            nothing else needed).

    Returns:
        start: {"id", "phase": "arming", "range", "parameter", "track",
        "points", "expected_seconds", "note"} — then poll
        automation.record_status.  stop: the status of the stopped recording.

    Gotchas:
        Needs a stopped transport; one recording at a time. Armed tracks are
        disarmed during the pass (else Live would record clips too) and
        re-armed after. Timing follows Live's ~100 ms tick (≈0.2 beat at 120
        BPM): Live records the moves as breakpoints, so ramps come out smooth
        and steps can land up to a tick late. Existing automation of that
        parameter inside the range is replaced; the recorded lane cannot be
        read back through the API (the parameter's automation state turns
        "playing"). Undo removes the recording in steps (Live's own undo).
    """
    action = str(action).strip().lower()
    current = _RECORDER.get("current")
    if action == "stop":
        if current is None:
            last = _RECORDER.get("last")
            if last is None:
                raise BridgeError("invalid_state", "no automation recording is running")
            return last.status()
        current.abort("stopped with action=stop")
        return current.status()
    if action != "start":
        raise BridgeError("bad_args", "action must be start or stop")
    if current is not None and current.phase not in ("done", "aborted", "failed"):
        raise BridgeError("invalid_state", "recording %s is still running — wait for it "
                          "(automation.record_status) or stop it (action=stop)" % current.id)
    if parameter is None:
        raise BridgeError("bad_args", "parameter is required")
    if points is None and shape is None:
        raise BridgeError("bad_args", "pass points or a shape")
    mode = str(mode).strip().lower()
    if mode not in ("linear", "step"):
        raise BridgeError("bad_args", "mode must be linear or step")
    if isinstance(preroll, bool) or not isinstance(preroll, (int, float)) or \
            not 0.0 <= preroll <= 16.0:
        raise BridgeError("bad_args", "preroll must be 0..16 beats")
    song = ctx.song
    if compat.safe_getattr(song, "is_playing", False):
        raise BridgeError("invalid_state", "stop the transport first — the recording starts "
                          "playback itself")
    if compat.safe_getattr(song, "record_mode", False):
        raise BridgeError("invalid_state", "Arrangement Record is already on")
    track_obj, (param, label, path) = _param_target(ctx, track, parameter, device)
    if compat.safe_getattr(param, "is_enabled", True) is False:
        raise BridgeError("invalid_state", "%r is disabled (macro-mapped or controlled by "
                          "Max) — Live does not record it" % _name(param))
    if normalized is None:
        normalized = shape is not None
    elif not isinstance(normalized, bool):
        raise BridgeError("bad_args", "normalized must be true or false")
    converter = _Converter(param, normalized)
    curve_points, mode, begin, finish, extra = _record_points(
        ctx, converter, points, shape, start, end, low, high, period, cycles, phase, duty,
        curve, seed, mode)
    if finish - begin > _RECORD_MAX_BEATS:
        raise BridgeError("bad_args", "the range is %s beats — at most %d per recording"
                          % (rnd(finish - begin), int(_RECORD_MAX_BEATS)))
    curve_points = [(t, converter.fit(v)) for t, v in curve_points]
    lead_in = max(0.0, begin - float(preroll))
    song_length = compat.safe_getattr(song, "song_length")
    if song_length is not None and lead_in > float(song_length) + 1e-9:
        raise BridgeError("invalid_state", "the song ends at beat %s (last clip or loop end "
                          "+ 32 beats) — Live cannot put the playhead at beat %s; place a "
                          "clip or the loop brace further out first"
                          % (rnd(song_length), rnd(lead_in)))
    recording = _Recording(ctx, track_obj, param, label, path, curve_points, mode, begin,
                           finish, lead_in)
    recording.setup()
    try:
        recording.schedule()
    except BridgeError:
        recording.restore()
        raise
    _RECORDER["current"] = recording
    _RECORDER["last"] = recording
    tempo = float(compat.safe_getattr(song, "tempo", 120.0) or 120.0)
    result = recording.status()
    result.update({"points": len(curve_points), "mode": mode,
                   "expected_seconds": rnd((finish - lead_in) * 60.0 / tempo, 1),
                   "note": "recording runs in real time — poll automation.record_status "
                           "until finished"})
    if converter.clamped:
        result["clamped"] = converter.clamped
    result.update(extra)
    return result


@command("automation.record_status", doc="Progress / result of the running or last "
                                         "automation.record pass")
def automation_record_status(ctx):
    """Status of ``automation.record``.

    Returns:
        {"id", "phase": arming|waiting|recording|stopping|restoring|done|aborted|failed,
         "finished", "track", "parameter", "range", "writes", "progress" 0..1,
         "recorded"?: [first, last song time written], "error"?,
         "state_after"? (the parameter's automation state — "playing" once
         Live has the lane), "restored"?} — or {"phase": "idle"} when nothing
        was recorded since the script loaded.
    """
    recording = _RECORDER.get("current") or _RECORDER.get("last")
    if recording is None:
        return {"phase": "idle", "finished": True}
    return recording.status()


@command("automation.clear", mutating=True,
         doc="Clear one clip envelope (optionally a time range) or all envelopes of a clip")
def automation_clear(ctx, track=None, slot=None, clip=None, parameter=None, device=None,
                     all=False, start=None, end=None):
    """Remove automation from a clip.

    Args:
        track, slot / clip: the clip.
        parameter, device: the envelope to clear (see ``automation.get``).
        all: clear every envelope of the clip (no ``parameter``).
        start, end: with ``parameter``: only delete the breakpoints in this
            range (needs Live 12 ``delete_events_in_range``).

    Returns:
        {"clip", "cleared": [{"name", "device", "path"}] | "all", "count"}.
    """
    clip_obj = resolve_clip(ctx, track, slot, clip)
    track_obj = _clip_track(ctx, clip_obj, track)
    if all:
        if parameter is not None:
            raise BridgeError("bad_args", "pass parameter or all=true, not both")
        entries = track_parameters(ctx, track_obj)
        before = _envelope_rows(ctx, clip_obj, entries)
        if not compat.has(clip_obj, "clear_all_envelopes"):
            raise BridgeError("unsupported", "this Live version has no clip.clear_all_envelopes")
        try:
            clip_obj.clear_all_envelopes()
        except Exception as error:
            raise BridgeError("invalid_state", "Live refused clear_all_envelopes: %s" % error)
        return {"clip": clip_info(ctx, clip_obj), "cleared": before, "count": len(before)}
    if parameter is None:
        raise BridgeError("bad_args", "pass parameter (the envelope to clear) or all=true")
    param, label, path = resolve_parameter(ctx, track_obj, parameter, device)
    info = param_info(param, label, path)
    envelope = get_envelope(clip_obj, param)
    if envelope is None:
        return {"clip": clip_info(ctx, clip_obj), "cleared": [], "count": 0,
                "note": "%r has no envelope in this clip" % _name(param)}
    if start is not None or end is not None:
        begin, finish = _window(clip_obj, start, end)
        if not compat.has(envelope, "delete_events_in_range"):
            raise BridgeError("unsupported", "this Live version cannot delete a range of an "
                              "envelope — clear the whole envelope instead")
        try:
            envelope.delete_events_in_range(float(begin), float(finish))
        except Exception as error:
            raise BridgeError("invalid_state", "Live refused delete_events_in_range: %s" % error)
        info["range"] = [rnd(begin), rnd(finish)]
    else:
        try:
            clip_obj.clear_envelope(param)
        except Exception as error:
            raise BridgeError("invalid_state", "Live refused clear_envelope: %s" % error)
    return {"clip": clip_info(ctx, clip_obj), "cleared": [info], "count": 1}


def _automated_rows(ctx, track_obj, include_all=False):
    rows = []
    for param, label, path in track_parameters(ctx, track_obj):
        state = _state(param)
        if include_all or state != "none":
            row = param_info(param, label, path)
            row["state"] = state
            rows.append(row)
    return rows


def _song_state(song):
    return {"re_enable_automation_enabled": bool(compat.safe_getattr(
        song, "re_enable_automation_enabled", False)),
        "session_automation_record": bool(compat.safe_getattr(
            song, "session_automation_record", False))}


def _all_tracks(song):
    tracks = list(compat.safe_getattr(song, "tracks", ()) or ())
    tracks += list(compat.safe_getattr(song, "return_tracks", ()) or ())
    master = compat.safe_getattr(song, "master_track")
    if master is not None:
        tracks.append(master)
    return tracks


def _param_target(ctx, track, parameter, device):
    if track is not None:
        track_obj = ctx.track(track)
    elif isinstance(parameter, str) and parameter.strip().startswith("song."):
        track_obj = owner_track(ctx.resolve(parameter.strip()))
    elif isinstance(device, str) and device.strip().startswith("song."):
        track_obj = owner_track(ctx.resolve(device.strip()))
    else:
        raise BridgeError("bad_args", "pass track with parameter (or a parameter path)")
    if track_obj is None:
        raise BridgeError("not_found", "cannot tell which track owns that parameter")
    return track_obj, resolve_parameter(ctx, track_obj, parameter, device)


@command("automation.state", doc="Automation state (none/playing/overridden) of a parameter, "
                                 "a track or the whole set")
def automation_state(ctx, track=None, parameter=None, device=None, include_all=False):
    """Read automation states.

    Args:
        track: limit to one track (index, name or path).
        parameter, device: one parameter (needs ``track`` unless a path).
        include_all: with ``track``: list every parameter, not only
            automated ones.

    Returns:
        {"song": {"re_enable_automation_enabled", "session_automation_record"},
         "parameter"?: {"name", "device", "path", "state", "value", "display"},
         "track"?: {"name", "automated": [...]},
         "tracks"?: [{"name", "automated": [...]}]  (no track/parameter:
         every track, return and master that has automated parameters)}

    Gotchas:
        "playing" = the parameter follows automation (arrangement track
        automation or a playing clip envelope); "overridden" = you moved it
        by hand and Live shows the re-enable button.
    """
    song = ctx.song
    result = {"song": _song_state(song)}
    if parameter is not None:
        _track_obj, (param, label, path) = _param_target(ctx, track, parameter, device)
        row = param_info(param, label, path)
        value = compat.safe_getattr(param, "value", 0.0)
        row.update({"state": _state(param), "value": rnd(value, 6),
                    "display": _display(param, value)})
        result["parameter"] = row
        return result
    if track is not None:
        track_obj = ctx.track(track)
        result["track"] = {"name": _name(track_obj),
                           "automated": _automated_rows(ctx, track_obj, bool(include_all))}
        return result
    result["tracks"] = [{"name": _name(t), "automated": rows}
                        for t, rows in ((t, _automated_rows(ctx, t)) for t in _all_tracks(song))
                        if rows]
    return result


@command("automation.re_enable", mutating=True,
         doc="Re-enable overridden automation for a parameter, a track or the whole song")
def automation_re_enable(ctx, track=None, parameter=None, device=None):
    """Press "Re-Enable Automation".

    Args:
        track: only parameters of this track that are "overridden".
        parameter, device: one parameter (``parameter.re_enable_automation()``).
        (none): the whole song (``song.re_enable_automation()``).

    Returns:
        {"scope": "parameter"|"track"|"song", "re_enabled": [{"name", "device",
         "path"}] (parameter/track), "song": {...state after}}.
    """
    song = ctx.song
    if parameter is not None:
        _track_obj, (param, label, path) = _param_target(ctx, track, parameter, device)
        _re_enable(param)
        row = param_info(param, label, path)
        row["state"] = _state(param)
        return {"scope": "parameter", "re_enabled": [row], "song": _song_state(song)}
    if track is not None:
        track_obj = ctx.track(track)
        done = []
        for param, label, path in track_parameters(ctx, track_obj):
            if _state(param) == "overridden":
                _re_enable(param)
                row = param_info(param, label, path)
                row["state"] = _state(param)
                done.append(row)
        return {"scope": "track", "re_enabled": done, "song": _song_state(song)}
    if not compat.has(song, "re_enable_automation"):
        raise BridgeError("unsupported", "this Live version has no song.re_enable_automation")
    try:
        song.re_enable_automation()
    except Exception as error:
        raise BridgeError("invalid_state", "Live refused re_enable_automation: %s" % error)
    return {"scope": "song", "song": _song_state(song)}


def _re_enable(param):
    if not compat.has(param, "re_enable_automation"):
        raise BridgeError("unsupported", "this parameter has no re_enable_automation")
    try:
        param.re_enable_automation()
    except Exception as error:
        raise BridgeError("invalid_state", "Live refused re_enable_automation for %r: %s"
                          % (_name(param), error))
