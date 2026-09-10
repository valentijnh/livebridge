"""Mixer commands: volume / pan / sends / track activator / crossfader, master and cue
volume, per-track summaries, batch changes, reset to defaults and level meters.

Value formats (parsed here so Claude can speak like a producer):

* volume / sends / cue: a number is Live's normalised parameter value 0..1
  (track volume 0.85 = 0 dB, 1.0 = +6 dB; send 1.0 = 0 dB);
  ``"-6 dB"`` / ``"-6db"`` / ``"0 dB"`` / ``"-inf"`` is an absolute level;
  ``"+3"`` / ``"-2.5"`` (explicit sign, no unit) is a *relative* change in dB;
  ``"50%"`` is 0.5 normalised.
* pan: -1..1, ``"C"``/``"center"``, ``"L50"``/``"50L"``/``"R20"`` (Live shows
  50L..50R), ``"left"``/``"right"``.
* crossfade assign: ``"A"``, ``"B"``, ``"none"`` (or 0/1/2).
* crossfader position: -1..1, ``"A"``, ``"B"``, ``"center"``, ``"25A"``/``"B40"``.

The dB <-> value curves were measured on Live 12.4.5 with
``DeviceParameter.str_for_value`` (see :func:`volume_to_db`).
"""

import math
import re

from .. import compat
from .. import resolve
from ..registry import BridgeError, command
from .tracks import parse_toggle, resolve_track, resolve_tracks, track_kind

#: Low-end tables (value, dB) measured on Live 12.4.5 — below them: -inf.
_VOLUME_TAIL = ((0.001, -69.565), (0.002, -69.151), (0.005, -68.015), (0.0075, -67.17),
                (0.01, -66.4), (0.02, -63.373), (0.03, -60.92))
_SEND_TAIL = ((0.005, -69.695), (0.01, -69.4), (0.015, -68.871), (0.02, -68.373),
              (0.025, -67.616), (0.03, -66.92))

CROSSFADE = {0: "A", 1: "none", 2: "B"}
_CROSSFADE_NAMES = {"a": 0, "none": 1, "off": 1, "": 1, "b": 2}
PANNING_MODES = {0: "stereo", 1: "split_stereo"}

#: What ``mixer.reset`` resets by default.
RESET_DEFAULT = ("volume", "pan", "sends", "activator", "crossfade", "panning_mode")
RESET_ALL = RESET_DEFAULT + ("mute", "solo")

#: Keys ``mixer.set`` / ``mixer.set_many`` understand.
SET_KEYS = ("volume", "pan", "sends", "activator", "crossfade", "mute", "solo",
            "panning_mode", "cue_volume", "crossfader")


# --------------------------------------------------------------------------
# value conversions
# --------------------------------------------------------------------------

def _interpolate(table, value):
    if value < table[0][0]:
        return float("-inf")
    for (x0, y0), (x1, y1) in zip(table, table[1:]):
        if value <= x1:
            return y0 + (y1 - y0) * (value - x0) / (x1 - x0)
    return table[-1][1]


def volume_to_db(value, send=False):
    """Normalised track volume (or send) value -> dB (``-inf`` at the bottom).

    Live 12.4.5: volume = 40*v - 34 dB for v >= 0.4, -66.8 + 202*v - 200*v^2
    for 0.03 <= v < 0.4, a measured table below; sends are 6 dB lower than
    volume for v >= 0.03 (send 1.0 = 0 dB).
    """
    value = float(value)
    if value >= 0.03:
        if value >= 0.4:
            db = 40.0 * value - 34.0
        else:
            db = -66.8 + 202.0 * value - 200.0 * value * value
        return db - 6.0 if send else db
    return _interpolate(_SEND_TAIL if send else _VOLUME_TAIL, value)


def db_to_volume(db, send=False):
    """Inverse of :func:`volume_to_db` (bisection; clamps into 0..1)."""
    if db is None or db == float("-inf"):
        return 0.0
    db = float(db)
    if db >= volume_to_db(1.0, send):
        return 1.0
    floor = (_SEND_TAIL if send else _VOLUME_TAIL)[0][1]
    if db < floor:
        return 0.0
    low, high = 0.0, 1.0
    for _ in range(60):
        middle = (low + high) / 2.0
        if volume_to_db(middle, send) < db:
            low = middle
        else:
            high = middle
    return round(high, 6)


def db_value(db):
    """dB for JSON: a rounded float, or the string ``"-inf"``."""
    if db is None:
        return None
    if db == float("-inf") or (isinstance(db, float) and math.isinf(db)):
        return "-inf"
    return round(db, 2) + 0.0  # no "-0.0"


_DB_RE = re.compile(r"^([+-]?\d+(?:\.\d+)?)\s*db$")
_REL_RE = re.compile(r"^[+-]\d+(?:\.\d+)?$")
_PCT_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*%$")


def _display_db(parameter, value):
    """dB shown by Live for ``value`` (``str_for_value``), or ``None``."""
    ok, text = compat.safe_call(parameter, "str_for_value", float(value))
    if not ok or text is None:
        return None
    text = str(text).strip().lower()
    if text.startswith("-inf"):
        return float("-inf")
    match = re.match(r"^\s*([+-]?\d+(?:\.\d+)?)\s*db\s*$", text)
    return float(match.group(1)) if match else None


def refine_db(parameter, guess, db):
    """Nudge ``guess`` so Live's own display of the parameter reads ``db``.

    The curves in :func:`volume_to_db` are within ~0.2 dB of Live 12.4.5;
    this bisects on the parameter's ``str_for_value`` around the guess so
    "-3 dB" shows as "-3.0 dB" rather than "-2.998 dB". Falls back to
    ``guess`` when the display cannot be read or does not bracket ``db``.
    """
    if parameter is None or db is None or db == float("-inf"):
        return guess
    shown = _display_db(parameter, guess)
    if shown is not None and abs(shown - db) < 1e-9:
        return guess            # already exact (e.g. "0 dB" on a send = the top, 1.0)
    low_limit = float(compat.safe_getattr(parameter, "min", 0.0))
    high_limit = float(compat.safe_getattr(parameter, "max", 1.0))
    low, high = max(low_limit, guess - 0.03), min(high_limit, guess + 0.03)
    low_db, high_db = _display_db(parameter, low), _display_db(parameter, high)
    if low_db is None or high_db is None or not low_db <= db <= high_db:
        return guess
    for _ in range(40):
        middle = (low + high) / 2.0
        shown = _display_db(parameter, middle)
        if shown is None:
            return guess
        if shown < db:
            low = middle
        else:
            high = middle
    return high


def parse_level(value, current, what="volume", send=False, parameter=None):
    """A volume/send/cue argument -> normalised value (see module docstring).

    With ``parameter`` given, absolute and relative dB values are refined
    against Live's own display of that parameter (:func:`refine_db`).
    """
    if isinstance(value, bool) or value is None:
        raise BridgeError("bad_args", "%s must be a number 0..1 or a dB string like '-6 dB'"
                          % what)
    if isinstance(value, (int, float)):
        number = float(value)
        if 0.0 <= number <= 1.0:
            return number
        raise BridgeError("bad_args", "%s %r is outside 0..1 — numbers are Live's normalised "
                          "value (0.85 = 0 dB for track volume); pass a string like '%s dB' "
                          "for decibels" % (what, value, value))
    if not isinstance(value, str):
        raise BridgeError("bad_args", "%s must be a number or a string" % what)
    text = value.strip().lower().replace(" ", "")
    if text in ("-inf", "-infdb", "inf", "silence", "silent"):
        return 0.0
    if text in ("unity", "0db"):
        return refine_db(parameter, db_to_volume(0.0, send), 0.0)
    match = _DB_RE.match(text)
    if match:
        db = float(match.group(1))
        return refine_db(parameter, db_to_volume(db, send), db)
    if _REL_RE.match(text):
        base = volume_to_db(float(current), send)
        if base == float("-inf"):
            base = (_SEND_TAIL if send else _VOLUME_TAIL)[0][1]
        db = base + float(text)
        return refine_db(parameter, db_to_volume(db, send), db)
    match = _PCT_RE.match(text)
    if match:
        percent = float(match.group(1))
        if percent > 100.0:
            raise BridgeError("bad_args", "%s percentage must be 0..100" % what)
        return percent / 100.0
    try:
        return parse_level(float(text), current, what, send, parameter)
    except ValueError:
        raise BridgeError("bad_args", "cannot read %s %r (use 0..1, '-6 dB', '+3' for a "
                          "relative change, or '-inf')" % (what, value))


_PAN_RE = (re.compile(r"^([lr])(\d+(?:\.\d+)?)$"), re.compile(r"^(\d+(?:\.\d+)?)([lr])$"))


def parse_pan(value):
    """A pan argument -> -1..1 (see module docstring)."""
    if isinstance(value, bool) or value is None:
        raise BridgeError("bad_args", "pan must be -1..1 or a string like 'L20'/'C'/'R50'")
    if isinstance(value, (int, float)):
        if -1.0 <= float(value) <= 1.0:
            return float(value)
        raise BridgeError("bad_args", "pan %r is outside -1..1 (use 'L50'..'R50' for Live's "
                          "display units)" % (value,))
    text = str(value).strip().lower().replace(" ", "")
    if text in ("c", "center", "centre", "mid", "middle"):
        return 0.0
    if text in ("left", "hardleft", "l"):
        return -1.0
    if text in ("right", "hardright", "r"):
        return 1.0
    for pattern in _PAN_RE:
        match = pattern.match(text)
        if match:
            side, amount = (match.group(1), match.group(2)) if pattern is _PAN_RE[0] \
                else (match.group(2), match.group(1))
            amount = float(amount)
            if amount > 50.0:
                raise BridgeError("bad_args", "pan %r: Live pans from 50L to 50R" % value)
            return (-amount if side == "l" else amount) / 50.0
    try:
        return parse_pan(float(text))
    except ValueError:
        raise BridgeError("bad_args", "cannot read pan %r (use -1..1, 'L20', 'C', 'R50')"
                          % value)


def parse_crossfade_assign(value):
    """``"A"``/``"none"``/``"B"`` or 0/1/2 -> 0/1/2."""
    if isinstance(value, int) and not isinstance(value, bool) and value in (0, 1, 2):
        return value
    if isinstance(value, str) and value.strip().lower() in _CROSSFADE_NAMES:
        return _CROSSFADE_NAMES[value.strip().lower()]
    raise BridgeError("bad_args", "crossfade must be 'A', 'B' or 'none' (or 0/1/2)")


def parse_crossfader(value):
    """Crossfader position -> -1..1 (``"A"`` = -1, ``"B"`` = 1, ``"25A"`` = -0.5)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if -1.0 <= float(value) <= 1.0:
            return float(value)
        raise BridgeError("bad_args", "crossfader must be -1..1 (A..B)")
    if isinstance(value, str):
        text = value.strip().lower().replace(" ", "")
        if text in ("a", "fulla"):
            return -1.0
        if text in ("b", "fullb"):
            return 1.0
        if text in ("c", "center", "centre", "middle", "0"):
            return 0.0
        match = re.match(r"^(\d+(?:\.\d+)?)([ab])$", text) or \
            re.match(r"^([ab])(\d+(?:\.\d+)?)$", text)
        if match:
            groups = match.groups()
            side, amount = (groups[1], groups[0]) if groups[1] in ("a", "b") else groups
            amount = float(amount)
            if amount <= 50.0:
                return (-amount if side == "a" else amount) / 50.0
        try:
            return parse_crossfader(float(text))
        except ValueError:
            pass
    raise BridgeError("bad_args", "cannot read crossfader %r (use -1..1, 'A', 'B', "
                      "'center', '25A')" % (value,))


# --------------------------------------------------------------------------
# parameters and summaries
# --------------------------------------------------------------------------

def _display(parameter, value=None):
    ok, text = compat.safe_call(parameter, "str_for_value",
                                compat.safe_getattr(parameter, "value", 0.0)
                                if value is None else value)
    return str(text) if ok and text is not None else None


def set_parameter(parameter, value, label):
    """Clamp ``value`` into the parameter's range and write it."""
    if parameter is None:
        raise BridgeError("invalid_state", "%s is not available on this track" % label)
    low = float(compat.safe_getattr(parameter, "min", 0.0))
    high = float(compat.safe_getattr(parameter, "max", 1.0))
    number = max(low, min(high, float(value)))
    if compat.safe_getattr(parameter, "is_quantized", False):
        number = float(int(round(number)))
    try:
        parameter.value = number
    except (RuntimeError, ValueError, TypeError, AttributeError) as error:
        raise BridgeError("invalid_state", "Live refused %s = %r: %s" % (label, number, error))
    return compat.safe_getattr(parameter, "value", number)


def _level(parameter, send=False, detail="summary"):
    value = float(compat.safe_getattr(parameter, "value", 0.0))
    if detail == "minimal":
        return db_value(volume_to_db(value, send))
    return {"value": round(value, 4), "db": db_value(volume_to_db(value, send)),
            "display": _display(parameter)}


def _return_names(ctx):
    return [str(compat.safe_getattr(t, "name", ""))
            for t in compat.safe_getattr(ctx.song, "return_tracks", ()) or ()]


def mixer_summary(ctx, track, detail="summary"):
    """Compact mixer state of one track."""
    get = compat.safe_getattr
    mixer = get(track, "mixer_device")
    kind = track_kind(ctx, track)
    data = {"name": get(track, "name"), "path": ctx.path_of(track), "type": kind}
    if mixer is None:
        return data
    data["volume"] = _level(get(mixer, "volume"), False, detail)
    panning = get(mixer, "panning")
    if panning is not None:
        pan_value = round(float(get(panning, "value", 0.0)), 4)
        data["pan"] = pan_value if detail == "minimal" else \
            {"value": pan_value, "display": _display(panning)}
    if kind != "master":
        data["mute"] = bool(get(track, "mute", False))
        data["solo"] = bool(get(track, "solo", False))
    activator = get(mixer, "track_activator")
    if activator is not None:
        data["active"] = bool(get(activator, "value", 1.0))
    names = _return_names(ctx)
    sends = []
    for index, send in enumerate(get(mixer, "sends", ()) or ()):
        entry = {"index": index, "letter": chr(ord("A") + index) if index < 26 else None,
                 "return": names[index] if index < len(names) else get(send, "name")}
        entry.update(_level(send, True, "summary"))
        if detail == "minimal":
            entry = {"letter": entry["letter"], "db": entry["db"]}
        sends.append(entry)
    if sends:
        data["sends"] = sends
    if kind == "master":
        cue = get(mixer, "cue_volume")
        if cue is not None:
            data["cue_volume"] = _level(cue, False, detail)
        crossfader = get(mixer, "crossfader")
        if crossfader is not None:
            position = round(float(get(crossfader, "value", 0.0)), 4)
            data["crossfader"] = position if detail == "minimal" else \
                {"value": position, "display": _display(crossfader)}
    else:
        assign = get(mixer, "crossfade_assign")
        if assign is not None:
            data["crossfade"] = CROSSFADE.get(int(assign), int(assign))
    if detail == "full":
        mode = get(mixer, "panning_mode")
        if mode is not None:
            data["panning_mode"] = PANNING_MODES.get(int(mode), int(mode))
            if int(mode) == 1:
                for name in ("left_split_stereo", "right_split_stereo"):
                    parameter = get(mixer, name)
                    if parameter is not None:
                        data[name] = {"value": round(float(get(parameter, "value", 0.0)), 4),
                                      "display": _display(parameter)}
        data["parameter_paths"] = {
            "volume": ctx.path_of(get(mixer, "volume")),
            "pan": ctx.path_of(panning) if panning is not None else None,
        }
    return data


def resolve_send(ctx, track, spec):
    """(index, send parameter) from an index, a letter or a return-track name."""
    sends = list(compat.safe_getattr(compat.safe_getattr(track, "mixer_device"), "sends", ())
                 or ())
    names = _return_names(ctx)
    if not sends:
        raise BridgeError("invalid_state", "%r has no sends (the set has no return tracks)"
                          % compat.safe_getattr(track, "name", ""))
    index = None
    if isinstance(spec, int) and not isinstance(spec, bool):
        index = spec
    elif isinstance(spec, str):
        text = spec.strip()
        if text.lstrip("-").isdigit():
            index = int(text)
        elif re.match(r"^(send\s*)?[a-zA-Z]$", text) and len(text) <= 6:
            index = ord(text[-1].upper()) - ord("A")
        else:
            lowered = text.lower()
            exact = [i for i, n in enumerate(names) if n.lower() == lowered]
            contains = [i for i, n in enumerate(names) if lowered in n.lower()]
            param_names = [str(compat.safe_getattr(s, "name", "")).lower() for s in sends]
            by_param = [i for i, n in enumerate(param_names) if lowered in n]
            for group in (exact, contains, by_param):
                if len(group) == 1:
                    index = group[0]
                    break
                if len(group) > 1:
                    raise BridgeError("bad_args", "send %r is ambiguous: %s"
                                      % (spec, ", ".join(repr(names[i]) for i in group)))
    else:
        raise BridgeError("bad_args", "send must be an index, a letter or a return-track name")
    if index is None or not (-len(sends) <= index < len(sends)):
        listing = ", ".join("%s=%r" % (chr(ord("A") + i), n) for i, n in enumerate(names))
        raise BridgeError("not_found", "no send %r (returns: %s)" % (spec, listing or "none"))
    index = index % len(sends)
    return index, sends[index]


def _send_items(sends):
    """``{"A": v}`` / ``[{"send": "A", "value": v}]`` / ``[v0, v1]`` -> [(spec, value)]."""
    if isinstance(sends, dict):
        return list(sends.items())
    if isinstance(sends, (list, tuple)):
        items = []
        for index, item in enumerate(sends):
            if isinstance(item, dict):
                if "send" not in item or "value" not in item:
                    raise BridgeError("bad_args", "each send entry needs 'send' and 'value'")
                items.append((item["send"], item["value"]))
            elif item is not None:
                items.append((index, item))
        return items
    raise BridgeError("bad_args", "sends must be an object like {\"A\": \"-12 dB\"} or a list")


def apply_mixer(ctx, track, volume=None, pan=None, sends=None, activator=None,
                crossfade=None, mute=None, solo=None, panning_mode=None, cue_volume=None,
                crossfader=None):
    """Apply mixer changes to one track; returns the list of changed keys."""
    get = compat.safe_getattr
    mixer = get(track, "mixer_device")
    label = str(get(track, "name", "track"))
    kind = track_kind(ctx, track)
    if mixer is None:
        raise BridgeError("invalid_state", "%r has no mixer" % label)
    changed = []
    if volume is not None:
        parameter = get(mixer, "volume")
        set_parameter(parameter, parse_level(volume, get(parameter, "value", 0.85),
                                             parameter=parameter),
                      "%s volume" % label)
        changed.append("volume")
    if pan is not None:
        set_parameter(get(mixer, "panning"), parse_pan(pan), "%s pan" % label)
        changed.append("pan")
    if sends is not None:
        for spec, value in _send_items(sends):
            index, parameter = resolve_send(ctx, track, spec)
            set_parameter(parameter, parse_level(value, get(parameter, "value", 0.0),
                                                 "send", send=True, parameter=parameter),
                          "%s send %s" % (label, chr(ord("A") + index)))
        changed.append("sends")
    if activator is not None:
        parameter = get(mixer, "track_activator")
        current = bool(get(parameter, "value", 1.0))
        set_parameter(parameter, 1.0 if parse_toggle(activator, current, "activator") else 0.0,
                      "%s activator" % label)
        changed.append("activator")
    if crossfade is not None:
        if kind == "master":
            raise BridgeError("invalid_state", "the master track has no crossfade assignment")
        try:
            mixer.crossfade_assign = parse_crossfade_assign(crossfade)
        except (RuntimeError, ValueError, TypeError) as error:
            raise BridgeError("invalid_state", "%s: crossfade assign refused: %s"
                              % (label, error))
        changed.append("crossfade")
    if panning_mode is not None:
        mode = {"stereo": 0, "split": 1, "split_stereo": 1, "stereo_split": 1}.get(
            str(panning_mode).strip().lower(), panning_mode)
        if mode not in (0, 1):
            raise BridgeError("bad_args", "panning_mode must be 'stereo' or 'split_stereo'")
        try:
            mixer.panning_mode = mode
        except (RuntimeError, ValueError, TypeError, AttributeError) as error:
            raise BridgeError("invalid_state", "%s: panning mode refused: %s" % (label, error))
        changed.append("panning_mode")
    for key, value in (("mute", mute), ("solo", solo)):
        if value is None:
            continue
        if kind == "master":
            raise BridgeError("invalid_state", "the master track has no %s" % key)
        try:
            setattr(track, key, parse_toggle(value, get(track, key, False), key))
        except (RuntimeError, ValueError, TypeError) as error:
            raise BridgeError("invalid_state", "%s: cannot set %s (%s)" % (label, key, error))
        changed.append(key)
    for key, value in (("cue_volume", cue_volume), ("crossfader", crossfader)):
        if value is None:
            continue
        if kind != "master":
            raise BridgeError("invalid_state", "%s exists on the master track only" % key)
        parameter = get(mixer, key)
        if parameter is None:
            raise BridgeError("unsupported", "%s is not available in this Live version" % key)
        number = parse_level(value, get(parameter, "value", 0.85), key,
                             parameter=parameter) \
            if key == "cue_volume" else parse_crossfader(value)
        set_parameter(parameter, number, key)
        changed.append(key)
    return changed


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

_check_detail = resolve.check_detail


@command("mixer.get", doc="Mixer state (volume dB, pan, sends, activator, crossfade)")
def mixer_get(ctx, track=None, detail="summary"):
    """Read mixer settings.

    Args:
        track: one track (index, name, return letter, "master", path), a list,
            or omitted = the whole mixer (tracks, returns, master).
        detail: "minimal" (numbers only: volume dB, pan, send dBs),
            "summary" (default: value + dB + Live's display text) or "full"
            (+ panning mode, split stereo, parameter paths).

    Returns:
        One track: {name, path, type, volume:{value, db, display},
        pan:{value, display}, mute, solo, active, sends:[{index, letter, return,
        value, db, display}], crossfade "A"/"none"/"B"} — the master adds
        cue_volume and crossfader. Without ``track``: {"tracks": [...],
        "returns": [...], "master": {...}}; a list gives {"tracks": [...]}.

    Gotchas:
        ``db`` is "-inf" at the bottom. Track volume 0.85 = 0 dB, 1.0 = +6 dB;
        sends top out at 0 dB.
    """
    _check_detail(detail)
    if track is None:
        song = ctx.song
        master = compat.safe_getattr(song, "master_track")
        return {
            "tracks": [mixer_summary(ctx, t, detail) for t in song.tracks],
            "returns": [mixer_summary(ctx, t, detail) for t in song.return_tracks],
            "master": mixer_summary(ctx, master, detail) if master is not None else None,
        }
    if isinstance(track, (list, tuple)) or (isinstance(track, str) and track == "all"):
        return {"tracks": [mixer_summary(ctx, t, detail) for t in resolve_tracks(ctx, track)]}
    return mixer_summary(ctx, resolve_track(ctx, track), detail)


@command("mixer.set", mutating=True, doc="Set volume/pan/sends/activator/crossfade of a track")
def mixer_set(ctx, track, volume=None, pan=None, sends=None, activator=None, crossfade=None,
              mute=None, solo=None, panning_mode=None, cue_volume=None, crossfader=None):
    """Change a track's mixer in one step.

    Args:
        track: index, name, return letter ("A"), "master", "selected" or path.
        volume: 0..1 (0.85 = 0 dB), "-6 dB", "+3"/"-2" (relative dB), "-inf".
        pan: -1..1, "C", "L20", "R50", "left", "right".
        sends: {"A": "-12 dB", "Reverb": 0.5, "1": "+3"} — keys are send
            indices, letters or return-track names; or a list of values by
            index; or [{"send": .., "value": ..}].
        activator: true/false/"toggle" (the track's on/off "Speaker" button).
        crossfade: crossfader assignment "A", "B" or "none".
        mute / solo: true, false or "toggle".
        panning_mode: "stereo" or "split_stereo".
        cue_volume / crossfader: master track only (cue level like volume;
            crossfader -1..1, "A", "B", "center").

    Returns:
        The track's mixer summary after the change, plus ``changed`` (keys).

    Gotchas:
        Values are clamped to Live's range. Relative dB ("+3") is computed
        from the current level. dB strings are matched against Live's own
        fader display, so "-3 dB" shows exactly "-3.0 dB"; the returned
        ``db`` comes from a fitted curve (within 0.02 dB down to -60 dB,
        ~0.15 dB below that — ``display`` is Live's exact text). The master
        has no mute/solo/crossfade assign.
    """
    values = dict(volume=volume, pan=pan, sends=sends, activator=activator,
                  crossfade=crossfade, mute=mute, solo=solo, panning_mode=panning_mode,
                  cue_volume=cue_volume, crossfader=crossfader)
    if all(v is None for v in values.values()):
        raise BridgeError("bad_args", "nothing to change: pass one of %s" % ", ".join(SET_KEYS))
    obj = resolve_track(ctx, track)
    changed = apply_mixer(ctx, obj, **values)
    data = mixer_summary(ctx, obj)
    data["changed"] = changed
    return data


@command("mixer.set_many", mutating=True, doc="Apply several mixer settings in one undo step")
def mixer_set_many(ctx, settings, stop_on_error=False):
    """Batch mixer changes (one undo step for all of them).

    Args:
        settings: list of objects, each with ``track`` plus any ``mixer.set``
            keys: volume, pan, sends, activator, crossfade, mute, solo,
            panning_mode, cue_volume, crossfader. Example:
            [{"track": "Bass", "volume": "-3 dB", "pan": "L10"},
             {"track": "Vocals", "sends": {"A": "-12 dB"}},
             {"track": "master", "volume": "-1 dB"}]
        stop_on_error: stop at the first failing entry (earlier entries stay
            applied) instead of continuing.

    Returns:
        {"applied": n_ok, "failed": n_failed, "results": [{track, ok,
        changed | error}]} — each ok entry also has volume_db and pan.
    """
    if not isinstance(settings, (list, tuple)) or not settings:
        raise BridgeError("bad_args", "settings must be a non-empty list of objects")
    results = []
    applied = failed = 0
    for position, entry in enumerate(settings):
        if not isinstance(entry, dict) or "track" not in entry:
            outcome = {"index": position, "ok": False, "error": "entry needs a 'track' key"}
        else:
            unknown = sorted(set(entry) - set(SET_KEYS) - {"track"})
            try:
                if unknown:
                    raise BridgeError("bad_args", "unknown keys %s (allowed: %s)"
                                      % (", ".join(unknown), ", ".join(SET_KEYS)))
                obj = resolve_track(ctx, entry["track"])
                changed = apply_mixer(ctx, obj, **dict((k, entry.get(k)) for k in SET_KEYS))
                summary = mixer_summary(ctx, obj, "minimal")
                outcome = {"index": position, "track": summary.get("name"), "ok": True,
                           "changed": changed, "volume_db": summary.get("volume"),
                           "pan": summary.get("pan")}
            except BridgeError as error:
                outcome = {"index": position, "track": entry.get("track"), "ok": False,
                           "error": error.message}
        results.append(outcome)
        if outcome["ok"]:
            applied += 1
        else:
            failed += 1
            if stop_on_error:
                break
    return {"applied": applied, "failed": failed, "results": results}


@command("mixer.master", mutating=True, doc="Master volume/pan, cue (preview) volume, crossfader")
def mixer_master(ctx, volume=None, pan=None, cue_volume=None, crossfader=None):
    """Read or change the master ("Main") track's mixer.

    Args:
        volume: master volume (0..1, "-3 dB", "+1", "-inf").
        pan: master pan (-1..1, "L10", "C").
        cue_volume: cue/preview volume (same formats as volume).
        crossfader: -1..1, "A", "B", "center", "25A".
        All optional — with none given this just reads the master mixer.

    Returns:
        The master mixer summary (volume, pan, cue_volume, crossfader, sends
        none) plus ``changed``.
    """
    master = compat.safe_getattr(ctx.song, "master_track")
    if master is None:
        raise BridgeError("not_found", "this set has no master track")
    changed = apply_mixer(ctx, master, volume=volume, pan=pan, cue_volume=cue_volume,
                          crossfader=crossfader)
    data = mixer_summary(ctx, master)
    data["changed"] = changed
    return data


def _reset_track(ctx, track, what):
    get = compat.safe_getattr
    mixer = get(track, "mixer_device")
    kind = track_kind(ctx, track)
    done = []
    if mixer is None:
        return done

    def default(parameter, fallback):
        value = get(parameter, "default_value")
        return fallback if value is None else value

    if "volume" in what:
        parameter = get(mixer, "volume")
        set_parameter(parameter, default(parameter, 0.85), "volume")
        done.append("volume")
    if "pan" in what:
        parameter = get(mixer, "panning")
        set_parameter(parameter, default(parameter, 0.0), "pan")
        done.append("pan")
    if "sends" in what:
        for send in get(mixer, "sends", ()) or ():
            set_parameter(send, default(send, 0.0), "send")
        done.append("sends")
    if "activator" in what and get(mixer, "track_activator") is not None:
        set_parameter(get(mixer, "track_activator"), 1.0, "activator")
        done.append("activator")
    assignments = []
    if "crossfade" in what and kind != "master":
        assignments.append((mixer, "crossfade_assign", 1, "crossfade"))
    if "panning_mode" in what and compat.has(mixer, "panning_mode"):
        assignments.append((mixer, "panning_mode", 0, "panning_mode"))
    if kind != "master":
        assignments += [(track, key, False, key) for key in ("mute", "solo") if key in what]
    for obj, prop, value, label in assignments:
        try:
            setattr(obj, prop, value)
            done.append(label)
        except Exception as error:
            ctx.log("mixer.reset: %s.%s refused: %s", get(track, "name", ""), prop, error)
    return done


@command("mixer.reset", mutating=True, doc="Reset mixer settings to Live's defaults")
def mixer_reset(ctx, track=None, what=None):
    """Reset volume (0 dB), pan (center), sends (-inf), activator (on),
    crossfade (none) and panning mode (stereo).

    Args:
        track: one track, a list, or omitted = every track, return and the
            master.
        what: optional list limiting the reset to some of "volume", "pan",
            "sends", "activator", "crossfade", "panning_mode", "mute", "solo"
            (mute/solo are only reset when listed), or "all" for everything.

    Returns:
        {"reset": [{track, done:[...]}], "what": [...]}.

    Gotchas:
        Defaults come from each parameter's ``default_value`` (track volume
        0.85 = 0 dB). One undo step.
    """
    if what is None:
        keys = list(RESET_DEFAULT)
    elif isinstance(what, str) and what.strip().lower() == "all":
        keys = list(RESET_ALL)
    else:
        keys = [what] if isinstance(what, str) else list(what)
        keys = [str(k).strip().lower() for k in keys]
        bad = [k for k in keys if k not in RESET_ALL]
        if bad:
            raise BridgeError("bad_args", "cannot reset %s (choose from %s)"
                              % (", ".join(bad), ", ".join(RESET_ALL)))
    if track is None:
        song = ctx.song
        targets = list(song.tracks) + list(song.return_tracks)
        master = compat.safe_getattr(song, "master_track")
        if master is not None:
            targets.append(master)
    else:
        targets = resolve_tracks(ctx, track)
    report = [{"track": compat.safe_getattr(t, "name", ""), "done": _reset_track(ctx, t, keys)}
              for t in targets]
    return {"reset": report, "what": keys}


# --------------------------------------------------------------------------
# meters
# --------------------------------------------------------------------------

def _meter(value):
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def meter_row(ctx, track, include_input=False, include_impact=False):
    """Momentary meters of one track: ``{name, type, level, left?, right?, peak, audio}``.

    Live 12.4.5 (``LIVE_API_DUMP``): ``output_meter_level`` is the MIDI or audio
    meter of the track output (after the mixer), ``output_meter_left/right``
    exist for tracks with audio output only; all are 0.0..1.0.  The ``input_*``
    meters show what arrives at the track input (``input_meter_left/right``:
    audio tracks only).
    """
    get = compat.safe_getattr
    row = {"name": get(track, "name"), "type": track_kind(ctx, track)}
    level = _meter(get(track, "output_meter_level"))
    left = _meter(get(track, "output_meter_left"))
    right = _meter(get(track, "output_meter_right"))
    row["level"] = level
    if left is not None or right is not None:
        row["left"], row["right"] = left, right
    values = [v for v in (level, left, right) if v is not None]
    row["peak"] = max(values) if values else None
    row["audio"] = left is not None or right is not None
    if include_input:
        entry = {"level": _meter(get(track, "input_meter_level"))}
        in_left = _meter(get(track, "input_meter_left"))
        in_right = _meter(get(track, "input_meter_right"))
        if in_left is not None or in_right is not None:
            entry["left"], entry["right"] = in_left, in_right
        row["input"] = entry
    if include_impact:
        impact = _meter(get(track, "performance_impact"))
        if impact is not None:
            row["performance_impact"] = impact
    return row


@command("mixer.meters", doc="Momentary level meters (output, optionally input) of tracks, "
                             "returns and the master, plus Live's CPU load")
def mixer_meters(ctx, track=None, include_input=False, include_impact=False,
                 include_cpu=True):
    """One snapshot of Live's level meters — the only audio feedback the API has.

    Args:
        track: one track, a list, or omitted = every track, return and the master.
        include_input: also the input meters (is signal arriving at an armed /
            monitoring track?).
        include_impact: also each track's ``performance_impact`` (CPU share).
        include_cpu: Live's average / peak CPU load in percent, like Live's CPU
            meter (``Application.average_process_usage`` / ``peak_process_usage``;
            an idle 12.4.5 set reads about 1).

    Returns:
        {"playing", "tracks": [{"name", "type", "level", "left"?, "right"?,
         "peak", "audio", "input"?, "performance_impact"?}], "returns": [...],
         "master": {...}, "cpu"?: {"average", "peak"}} — with ``track``:
        {"playing", "tracks": [...], "cpu"?}.

    Gotchas:
        Values are Live's momentary meter positions 0.0..1.0 (the meter's own
        scale — not dB, not linear gain; the top of the meter is 1.0). One
        snapshot is one instant: sample several times while the song plays
        (the MCP tool live_mixer_meters does that with ``seconds``). Everything
        reads 0 while the transport is stopped and nothing is monitored.
        ``left``/``right`` exist only for tracks with audio output (a MIDI
        track without an instrument shows a MIDI meter in ``level``).
    """
    for value, name in ((include_input, "include_input"), (include_impact, "include_impact"),
                        (include_cpu, "include_cpu")):
        if not isinstance(value, bool):
            raise BridgeError("bad_args", "%s must be true or false" % name)
    song = ctx.song
    result = {"playing": bool(compat.safe_getattr(song, "is_playing", False))}

    def rows(tracks):
        return [meter_row(ctx, t, include_input, include_impact) for t in tracks]

    if track is None:
        result["tracks"] = rows(compat.safe_getattr(song, "tracks", ()) or ())
        result["returns"] = rows(compat.safe_getattr(song, "return_tracks", ()) or ())
        master = compat.safe_getattr(song, "master_track")
        result["master"] = meter_row(ctx, master, include_input, include_impact) \
            if master is not None else None
    elif isinstance(track, (list, tuple)) or (isinstance(track, str) and track == "all"):
        result["tracks"] = rows(resolve_tracks(ctx, track))
    else:
        result["tracks"] = rows([resolve_track(ctx, track)])
    if include_cpu:
        app = ctx.app
        result["cpu"] = {"average": _meter(compat.safe_getattr(app, "average_process_usage")),
                         "peak": _meter(compat.safe_getattr(app, "peak_process_usage"))}
    return result
