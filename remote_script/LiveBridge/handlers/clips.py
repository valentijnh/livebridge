"""Clips — session and arrangement: list, inspect, create, delete, duplicate,
fire/stop, edit properties, quantize, crop, duplicate the loop, reverse, grooves
(the groove pool and a clip's groove), warp markers and Live's audio
conversions (audio -> MIDI / Drum Rack / Simpler).

Addressing a clip (every command here, and ``notes.*`` / ``arrangement.*``,
use :func:`resolve_clip`):

* ``track`` + ``slot`` — a session clip.  ``track`` is an index into
  ``song.tracks``, a track name (exact, then case-insensitive prefix) or a LOM
  path; ``slot`` is a scene index or a scene name.
* ``clip`` — a LOM path (``"song.tracks[0].clip_slots[2].clip"``,
  ``"song.tracks[1].arrangement_clips[0]"``, or a clip-slot path), a clip
  name (searched in the session and the arrangement, optionally limited to
  ``track``), or ``"selected"`` (the clip shown in Live's Detail view, else the
  highlighted clip slot's clip).

Times are in beats (quarter notes) unless a command offers ``unit="bars"``;
bars follow the song's time signature.

The helpers at the top (``live_call``, ``resolve_clip``, ``owner_track``,
``to_beats``, ``page`` ...) are shared with ``notes.py`` and
``arrangement.py``.

Not in the Live API (so not here): consolidating clips, exporting/rendering
audio, freezing.
"""

import math
import re

from .. import compat
from .. import resolve
from .. import serialize
from ..registry import BridgeError, command


# ==========================================================================
# shared helpers (also imported by notes.py and arrangement.py)
# ==========================================================================

DETAILS = ("minimal", "summary", "full")

_PATH_TRACK_RE = re.compile(r"song\.tracks\[(-?\d+)\]")
_PATH_SLOT_RE = re.compile(r"clip_slots\[(-?\d+)\]")
_PATH_ARR_RE = re.compile(r"arrangement_clips\[(-?\d+)\]")


#: Call into Live with protocol errors — shared implementation (``resolve.live_call``).
live_call = resolve.live_call


#: Validate a ``detail`` argument — shared implementation.
check_detail = resolve.check_detail


def as_float(value, name, minimum=None, maximum=None, allow_none=False):
    """Coerce a JSON number to float with a friendly ``bad_args``."""
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        if isinstance(value, str):
            try:
                value = float(value.strip())
            except ValueError:
                raise BridgeError("bad_args", "%s must be a number, got %r" % (name, value))
        else:
            raise BridgeError("bad_args", "%s must be a number, got %r" % (name, value))
    value = float(value)
    if value != value or value in (float("inf"), float("-inf")):
        raise BridgeError("bad_args", "%s must be a finite number" % name)
    if minimum is not None and value < minimum:
        raise BridgeError("bad_args", "%s must be >= %s (got %s)" % (name, minimum, value))
    if maximum is not None and value > maximum:
        raise BridgeError("bad_args", "%s must be <= %s (got %s)" % (name, maximum, value))
    return value


def as_int(value, name, minimum=None, maximum=None, allow_none=False):
    """Coerce a JSON integer (or an integral float/str) with ``bad_args``."""
    if value is None and allow_none:
        return None
    if isinstance(value, bool):
        raise BridgeError("bad_args", "%s must be an integer, got %r" % (name, value))
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        value = int(value.strip())
    if not isinstance(value, int):
        raise BridgeError("bad_args", "%s must be an integer, got %r" % (name, value))
    if minimum is not None and value < minimum:
        raise BridgeError("bad_args", "%s must be >= %d (got %d)" % (name, minimum, value))
    if maximum is not None and value > maximum:
        raise BridgeError("bad_args", "%s must be <= %d (got %d)" % (name, maximum, value))
    return value


def as_bool(value, name, allow_none=False):
    """Coerce a JSON boolean (also 0/1 and "true"/"false")."""
    if value is None and allow_none:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in ("true", "false", "on", "off",
                                                            "yes", "no", "1", "0"):
        return value.strip().lower() in ("true", "on", "yes", "1")
    raise BridgeError("bad_args", "%s must be true or false, got %r" % (name, value))


def rnd(value, digits=4):
    """Round a float for compact JSON (ints stay ints)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    number = round(number, digits)
    if number.is_integer() and abs(number) < 1e15:
        return int(number)
    return number


def beats_per_bar(song):
    """Beats (quarter notes) in one bar of the song's time signature."""
    numerator = compat.safe_getattr(song, "signature_numerator", 4) or 4
    denominator = compat.safe_getattr(song, "signature_denominator", 4) or 4
    try:
        return float(numerator) * 4.0 / float(denominator)
    except (TypeError, ValueError, ZeroDivisionError):
        return 4.0


def check_unit(unit):
    """Validate a ``unit`` argument (``"beats"`` or ``"bars"``)."""
    if unit not in ("beats", "bars"):
        raise BridgeError("bad_args", "unit must be 'beats' or 'bars' (got %r)" % (unit,))
    return unit


def to_beats(ctx, value, unit, name, position=False, allow_none=True, minimum=None):
    """Convert a time/length argument to beats.

    With ``unit="bars"`` a *position* is a 1-based bar number (bar 1 = beat 0)
    and a *length* is a number of bars.  A full ``"bars.beats.sixteenths"``
    string (``"17.1.1"`` position, ``"4.0.0"`` length) is accepted in any unit.
    """
    if value is None:
        if allow_none:
            return None
        raise BridgeError("bad_args", "%s is required" % name)
    if resolve.is_bbs(value):
        # "17.1.1" works everywhere (1-based position / 0-based length),
        # whatever ``unit`` says — same notation as transport, cues, automation.
        number = resolve.parse_time(ctx.song, value, name, is_length=not position)
        if minimum is not None and number < minimum:
            raise BridgeError("bad_args", "%s must be >= %s beats (got %s)"
                              % (name, minimum, number))
        return number
    number = as_float(value, name)
    if unit == "bars":
        bar = beats_per_bar(ctx.song)
        if position:
            if number < 1:
                raise BridgeError("bad_args", "%s is a 1-based bar number with unit='bars' "
                                  "(bar 1 = the start), got %s" % (name, number))
            number = (number - 1.0) * bar
        else:
            number = number * bar
    if minimum is not None and number < minimum:
        raise BridgeError("bad_args", "%s must be >= %s beats (got %s)" % (name, minimum, number))
    return number


def bar_beat(ctx, time):
    """``"17.1.1"`` (bar.beat.sixteenth, 1-based) for a time in beats."""
    numerator, denominator = resolve.signature_of(ctx.song)
    return resolve.beats_to_bbs(time, numerator, denominator)


def page(items, offset, limit, maximum=2000):
    """Slice ``items`` for paging; returns ``(slice, offset, limit)``."""
    offset, limit = resolve.check_paging(offset, limit, maximum=maximum)
    return items[offset:offset + limit], offset, limit


def paged_result(key, items, offset, limit, total, extra=None):
    """The standard paged result (``resolve.paged``): ``{"total", "offset",
    "count" (returned), <key>: [...], "next_offset"?}``."""
    return resolve.paged(key, items, offset, total, extra)


def _normal(text):
    return re.sub(r"[\s_\-]+", "", str(text).strip().lower())


def enum_value(value, table, name, live_enum=None):
    """An int for an enum argument given as a number or a (loose) name.

    ``table`` is one of the ``serialize`` ``{int: name}`` tables; names are
    compared without case/spaces/underscores ("1 bar" == "1bar"); Live's own
    member names (``q_sixteenth``) are accepted when ``live_enum`` names the
    ``Live`` enum class (``"Clip.ClipLaunchQuantization"``).
    """
    if isinstance(value, bool):
        raise BridgeError("bad_args", "%s must be a number or a name" % name)
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int):
        if value in table:
            return value
        raise BridgeError("bad_args", "%s %d is not valid (choose from %s)"
                          % (name, value, _choices(table)))
    if isinstance(value, str):
        text = value.strip()
        if text.lstrip("-").isdigit():
            return enum_value(int(text), table, name, live_enum)
        wanted = _normal(text)
        for number, label in table.items():
            if _normal(label) == wanted:
                return number
        if live_enum:
            names = compat.safe_getattr(compat.live_enum(live_enum), "names")
            if isinstance(names, dict):
                for member_name, member in names.items():
                    if _normal(member_name) == wanted:
                        try:
                            return int(member)
                        except (TypeError, ValueError):
                            break
    raise BridgeError("bad_args", "%s %r is not valid (choose from %s)"
                      % (name, value, _choices(table)))


def _choices(table):
    return ", ".join("%r" % label for _number, label in sorted(table.items()))


def owner_track(clip):
    """The Track a clip (session, arrangement or take-lane clip) belongs to."""
    node = compat.safe_getattr(clip, "canonical_parent")
    for _ in range(4):
        if node is None:
            return None
        if serialize.kind_of(node) == "track":
            return node
        node = compat.safe_getattr(node, "canonical_parent")
    return None


def owner_slot(clip):
    """The ClipSlot of a session clip, else ``None``."""
    parent = compat.safe_getattr(clip, "canonical_parent")
    if parent is not None and serialize.kind_of(parent) == "clip_slot":
        return parent
    return None


def location(ctx, obj):
    """``{"track": i, "slot": j}`` / ``{"track": i, "arrangement_index": k}``
    parsed from the canonical path of a clip or clip slot."""
    path = ctx.path_of(obj) or ""
    data = {}
    match = _PATH_TRACK_RE.search(path)
    if match:
        data["track"] = int(match.group(1))
    match = _PATH_SLOT_RE.search(path)
    if match:
        data["slot"] = int(match.group(1))
    match = _PATH_ARR_RE.search(path)
    if match:
        data["arrangement_index"] = int(match.group(1))
    return data


def clip_row(ctx, clip, detail="minimal"):
    """A clip summary plus its track name and slot/arrangement position."""
    row = ctx.summarize(clip, detail)
    row.pop("kind", None)
    if detail == "minimal":
        row.pop("is_audio", None)
    track = owner_track(clip)
    if track is not None:
        row["track"] = compat.safe_getattr(track, "name")
    where = location(ctx, clip)
    if "slot" in where:
        row["slot"] = where["slot"]
    if compat.safe_getattr(clip, "is_arrangement_clip", False):
        row["start_time"] = rnd(compat.safe_getattr(clip, "start_time"))
        row["end_time"] = rnd(compat.safe_getattr(clip, "end_time"))
    return row


def _is_path(text):
    return text.startswith("song.") or text == "song"


def _selected_clip(ctx):
    view = ctx.view
    clip = compat.safe_getattr(view, "detail_clip")
    if clip is not None:
        return clip
    slot = compat.safe_getattr(view, "highlighted_clip_slot")
    clip = compat.safe_getattr(slot, "clip")
    if clip is not None:
        return clip
    raise BridgeError("not_found", "no clip is selected (the Detail view shows no clip and "
                      "the highlighted clip slot is empty)")


def _all_clips(ctx, track=None):
    """Every clip of the set (or of one track): session first, then arrangement."""
    tracks = [ctx.track(track)] if track is not None else \
        list(compat.safe_getattr(ctx.song, "tracks", ()) or ())
    for track_obj in tracks:
        for slot in compat.safe_getattr(track_obj, "clip_slots", ()) or ():
            clip = compat.safe_getattr(slot, "clip")
            if clip is not None:
                yield clip
    for track_obj in tracks:
        for clip in compat.safe_getattr(track_obj, "arrangement_clips", ()) or ():
            yield clip


def _clip_by_name(ctx, name, track=None):
    clips = list(_all_clips(ctx, track))
    for clip in clips:
        if compat.safe_getattr(clip, "name") == name:
            return clip
    lowered = name.lower()
    for clip in clips:
        if str(compat.safe_getattr(clip, "name", "")).lower() == lowered:
            return clip
    prefix = [c for c in clips
              if str(compat.safe_getattr(c, "name", "")).lower().startswith(lowered)]
    if len(prefix) == 1:
        return prefix[0]
    if len(prefix) > 1:
        paths = ", ".join("%r at %s" % (compat.safe_getattr(c, "name", ""), ctx.path_of(c))
                          for c in prefix[:5])
        raise BridgeError("bad_args", "clip %r is ambiguous: %s" % (name, paths))
    raise BridgeError("not_found", "no clip named %r%s — pass track+slot or a clip path"
                      % (name, " on that track" if track is not None else ""))


def resolve_clip(ctx, track=None, slot=None, clip=None, need=None):
    """Turn the loose clip arguments into a Clip.

    Args:
        track, slot: session address (track index/name/path + scene index/name).
        clip: a LOM path (clip, clip slot or arrangement clip), a clip name, or
            ``"selected"``; wins over ``track``/``slot``.
        need: ``"midi"`` or ``"audio"`` to require a clip type.

    Raises:
        BridgeError: ``bad_args`` when the address is incomplete,
            ``not_found`` for an empty slot / unknown name, ``invalid_state``
            when the clip has the wrong type.
    """
    if clip is not None:
        if isinstance(clip, str):
            text = clip.strip()
            if not text:
                raise BridgeError("bad_args", "clip must not be empty")
            if text.lower() in ("selected", "detail", "current"):
                obj = _selected_clip(ctx)
            elif _is_path(text):
                obj = ctx.resolve(text)
                if obj is None:
                    raise BridgeError("not_found", "%s: there is no clip there (empty slot?)"
                                      % text)
                kind = serialize.kind_of(obj)
                if kind == "clip_slot":
                    inner = compat.safe_getattr(obj, "clip")
                    if inner is None:
                        raise BridgeError("not_found", "%s: the clip slot is empty" % text)
                    obj = inner
                elif kind != "clip":
                    raise BridgeError("bad_args", "%s is a %s, not a clip" % (text, kind or
                                                                         type(obj).__name__))
            else:
                obj = _clip_by_name(ctx, text, track)
        elif serialize.kind_of(clip) == "clip":
            obj = clip
        else:
            raise BridgeError("bad_args", "clip must be a LOM path, a clip name or 'selected'")
    elif track is not None and slot is not None:
        obj = ctx.clip(track, slot)
    elif track is not None:
        raise BridgeError("bad_args", "slot is required with track (a scene index or name), "
                          "or pass clip=<path|name>")
    else:
        raise BridgeError("bad_args", "address the clip with track+slot, or clip=<path|name|"
                          "'selected'>")
    if need == "midi" and not compat.safe_getattr(obj, "is_midi_clip", False):
        raise BridgeError("invalid_state", "%s is an audio clip — this needs a MIDI clip"
                          % (ctx.path_of(obj) or "the clip"))
    if need == "audio" and compat.safe_getattr(obj, "is_midi_clip", False):
        raise BridgeError("invalid_state", "%s is a MIDI clip — this needs an audio clip"
                          % (ctx.path_of(obj) or "the clip"))
    return obj


def first_empty_slot(ctx, track_obj):
    """Index of the first empty clip slot of a track, or ``None``."""
    for index, slot in enumerate(compat.safe_getattr(track_obj, "clip_slots", ()) or ()):
        if not compat.safe_getattr(slot, "has_clip", False):
            return index
    return None


def resolve_slot(ctx, track=None, slot=None, clip=None):
    """A ClipSlot from track+slot or a clip-slot path (``clip``)."""
    if isinstance(clip, str) and _is_path(clip.strip()):
        obj = ctx.resolve(clip.strip())
        kind = serialize.kind_of(obj)
        if kind == "clip_slot":
            return obj
        if kind == "clip":
            parent = owner_slot(obj)
            if parent is not None:
                return parent
        raise BridgeError("bad_args", "%s is not a session clip slot" % clip)
    if track is None or slot is None:
        raise BridgeError("bad_args", "track and slot are required")
    return ctx.clip_slot(track, slot)


def midi_clip_for_writing(ctx, track=None, slot=None, clip=None, create=True,
                          length=None, name=None):
    """The MIDI clip to write notes into — created in the slot when missing.

    Returns ``(clip, created)``.  With ``create`` and no clip, the clip goes
    into ``slot`` (or the track's first empty slot when ``slot`` is omitted);
    ``length`` (beats) is rounded up to whole bars, minimum one bar.
    """
    clip_slot = None
    if clip is not None:
        if isinstance(clip, str) and _is_path(clip.strip()):
            obj = ctx.resolve(clip.strip())
            kind = serialize.kind_of(obj)
            if kind == "clip_slot":
                if compat.safe_getattr(obj, "clip") is not None:
                    return resolve_clip(ctx, clip=obj.clip, need="midi"), False
                clip_slot = obj
            elif obj is None and clip.strip().endswith(".clip"):
                clip_slot = ctx.resolve(clip.strip()[:-len(".clip")])
            else:
                return resolve_clip(ctx, track, slot, clip, need="midi"), False
        else:
            return resolve_clip(ctx, track, slot, clip, need="midi"), False
    elif track is not None and slot is not None:
        clip_slot = ctx.clip_slot(track, slot)
        existing = compat.safe_getattr(clip_slot, "clip")
        if existing is not None:
            return resolve_clip(ctx, clip=existing, need="midi"), False
    elif track is not None:
        if not create:
            raise BridgeError("bad_args", "slot is required (or pass create=true to use the "
                              "first empty slot)")
        track_obj = ctx.track(track)
        index = first_empty_slot(ctx, track_obj)
        if index is None:
            raise BridgeError("invalid_state", "track %r has no empty clip slot — pass a slot "
                              "or add a scene" % compat.safe_getattr(track_obj, "name", ""))
        clip_slot = track_obj.clip_slots[index]
    else:
        raise BridgeError("bad_args", "address the clip with track+slot or clip=<path|name>")
    if clip_slot is None or serialize.kind_of(clip_slot) != "clip_slot":
        raise BridgeError("not_found", "no clip slot there")
    if not create:
        raise BridgeError("not_found", "%s: the clip slot is empty (pass create=true to make a "
                          "clip)" % (ctx.path_of(clip_slot) or "clip slot"))
    bar = beats_per_bar(ctx.song)
    wanted = float(length) if length else bar
    bars = max(1, int(math.ceil(wanted / bar - 1e-9)))
    new_clip = live_call("create_clip", clip_slot.create_clip, float(bars * bar))
    if name:
        live_call("clip.name", setattr, new_clip, "name", str(name))
    return new_clip, True


def clip_region(clip):
    """``(start, end)`` beats of what a clip plays: the loop, or — unlooped —
    the clip start/end, which Live 12 keeps in ``start_marker`` (== its
    ``loop_start``) and ``loop_end`` (see :func:`write_region`)."""
    get = compat.safe_getattr
    looping = get(clip, "looping", True)
    start = float(get(clip, "loop_start" if looping else "start_marker", 0.0) or 0.0)
    end = float(get(clip, "loop_end", 0.0) or 0.0)
    if end <= start:
        end = start + float(compat.safe_getattr(clip, "length", 0.0) or 0.0)
    return start, end


def extend_clip_to(clip, end_time, song):
    """Grow a clip's loop (or unlooped clip end) so it reaches ``end_time``
    (whole bars from its start).

    Returns the new length in beats, or ``None`` when nothing changed.
    """
    looping = compat.safe_getattr(clip, "looping", True)
    base, current_end = clip_region(clip)
    if end_time <= current_end + 1e-9:
        return None
    bar = beats_per_bar(song)
    bars = int(math.ceil((end_time - base) / bar - 1e-9))
    new_end = base + bars * bar
    if looping:
        live_call("clip.loop_end", setattr, clip, "loop_end", new_end)
        if (compat.safe_getattr(clip, "end_marker", 0.0) or 0.0) < new_end:
            live_call("clip.end_marker", setattr, clip, "end_marker", new_end)
    else:
        write_region(clip, None, new_end)
    return rnd(new_end - base)


def write_region(clip, start, end, label="markers"):
    """Set the start/end of an **unlooped** clip.

    Live 12.4.5 (verified on the running Live; its API docs say the same:
    ``loop_start``/``loop_end`` are "for unlooped clips: clip start/end"):
    while looping is off the clip plays ``loop_start..loop_end`` —
    ``clip.length``, crop, duplicate-loop and an arrangement clip's
    ``end_time`` all follow that pair.  ``start_marker`` is tied to
    ``loop_start`` (writing ``start_marker`` is silently ignored) and
    ``end_marker`` is a separate value that does not move the end.  So the
    start is written to ``loop_start`` and the end to ``loop_end`` and
    ``end_marker`` (kept equal so every reading agrees), in an order that
    keeps start < end after every single write.
    """
    get = compat.safe_getattr
    if start is not None and end is not None and start >= end:
        raise BridgeError("bad_args", "%s: start (%s) must be before end (%s)"
                          % (label, rnd(start), rnd(end)))

    def write_end():
        live_call("clip.loop_end", setattr, clip, "loop_end", end)
        live_call("clip.end_marker", setattr, clip, "end_marker", end)

    if start is not None and end is not None:
        current_ends = min(float(get(clip, "loop_end", 0.0) or 0.0),
                           float(get(clip, "end_marker", 0.0) or 0.0))
        if start >= current_ends:
            write_end()
            live_call("clip.loop_start", setattr, clip, "loop_start", start)
        else:
            live_call("clip.loop_start", setattr, clip, "loop_start", start)
            write_end()
    elif start is not None:
        live_call("clip.loop_start", setattr, clip, "loop_start", start)
    elif end is not None:
        write_end()
    if start is not None and abs(float(get(clip, "start_marker", start) or 0.0) - start) > 1e-9:
        # Live moved start_marker with loop_start already; hosts that keep
        # them separate need the explicit write.
        live_call("clip.start_marker", setattr, clip, "start_marker", start)


def align_unlooped_end(clip):
    """After a Live edit of an unlooped clip, set ``end_marker`` to the real
    end (``loop_end``) so every reading agrees. Returns True when written."""
    get = compat.safe_getattr
    if get(clip, "looping", True):
        return False
    start = float(get(clip, "start_marker", 0.0) or 0.0)
    end = float(get(clip, "loop_end", 0.0) or 0.0)
    if end <= start or abs(float(get(clip, "end_marker", end) or 0.0) - end) < 1e-9:
        return False
    live_call("clip.end_marker", setattr, clip, "end_marker", end)
    return True


# ==========================================================================
# clip property helpers
# ==========================================================================

def _parse_db(text):
    value = str(text or "").strip().lower().replace("db", "").strip()
    if not value:
        raise ValueError("empty gain display")
    if "inf" in value:
        return float("-inf")
    return float(value)


def gain_db(clip):
    """The clip gain in dB parsed from ``gain_display_string`` (``None`` if unreadable)."""
    try:
        value = _parse_db(compat.safe_getattr(clip, "gain_display_string"))
    except (TypeError, ValueError):
        return None
    if value == float("-inf"):
        return "-inf"
    return rnd(value, 2)


def set_gain_db(clip, db):
    """Set an audio clip's gain in dB.

    Live exposes gain as 0..1 with an undocumented curve, so this bisects the
    raw value against ``gain_display_string`` (which is monotonic).  Values
    outside Live's range end at the nearest limit.
    """
    try:
        _parse_db(compat.safe_getattr(clip, "gain_display_string"))
    except (TypeError, ValueError):
        raise BridgeError("unsupported", "cannot map dB on this Live version "
                          "(unreadable gain_display_string) — pass gain (0..1) instead")
    low, high = 0.0, 1.0
    for _ in range(28):
        middle = (low + high) / 2.0
        live_call("clip.gain", setattr, clip, "gain", middle)
        try:
            shown = _parse_db(clip.gain_display_string)
        except (TypeError, ValueError):
            shown = float("-inf")
        if shown < db:
            low = middle
        else:
            high = middle
    live_call("clip.gain", setattr, clip, "gain", (low + high) / 2.0)


def _set_ordered(clip, first, second, new_first, new_second, label):
    """Write a (start, end) pair so ``start < end`` holds after every write."""
    if new_first is not None and new_second is not None:
        if new_first >= new_second:
            raise BridgeError("bad_args", "%s: start (%s) must be before end (%s)"
                              % (label, new_first, new_second))
        current_second = compat.safe_getattr(clip, second, 0.0) or 0.0
        if new_first >= current_second:
            live_call("clip." + second, setattr, clip, second, new_second)
            live_call("clip." + first, setattr, clip, first, new_first)
        else:
            live_call("clip." + first, setattr, clip, first, new_first)
            live_call("clip." + second, setattr, clip, second, new_second)
    elif new_first is not None:
        live_call("clip." + first, setattr, clip, first, new_first)
    elif new_second is not None:
        live_call("clip." + second, setattr, clip, second, new_second)


def _parse_signature(value):
    return resolve.parse_signature(value, "signature")


def clip_details(ctx, clip, detail="full", include_warp_markers=False):
    """The ``clips.get`` payload for one clip."""
    data = clip_row(ctx, clip, detail)
    get = compat.safe_getattr
    data["is_session_clip"] = bool(get(clip, "is_session_clip",
                                       not get(clip, "is_arrangement_clip", False)))
    data["playing_position"] = rnd(get(clip, "playing_position", 0.0))
    if detail == "full":
        data["launch_mode"] = serialize.LAUNCH_MODES.get(get(clip, "launch_mode"),
                                                         get(clip, "launch_mode"))
        data["launch_quantization"] = serialize.CLIP_LAUNCH_QUANTIZATION.get(
            get(clip, "launch_quantization"), get(clip, "launch_quantization"))
        data["color"] = "#%06X" % get(clip, "color") if isinstance(get(clip, "color"), int) \
            else None
        data["has_groove"] = bool(get(clip, "has_groove", False))
        if data["has_groove"]:
            groove = get(clip, "groove")
            if groove is not None:
                data["groove"] = get(groove, "name")
    if not get(clip, "is_midi_clip", False):
        data["gain_db"] = gain_db(clip)
        data["ram_mode"] = get(clip, "ram_mode")
        data["sample_rate"] = get(clip, "sample_rate")
        modes = get(clip, "available_warp_modes")
        if modes is not None:
            try:
                data["available_warp_modes"] = [serialize.WARP_MODES.get(int(m), int(m))
                                                for m in modes]
            except (TypeError, ValueError):
                pass
        if include_warp_markers:
            markers = []
            for marker in get(clip, "warp_markers", ()) or ():
                # sample_time is in seconds in Live 12.4.5 (e.g. 0.011719 s
                # for the 1/32-beat helper marker Live adds after the first).
                markers.append([rnd(get(marker, "sample_time"), 6),
                                rnd(get(marker, "beat_time"), 6)])
            data["warp_markers"] = markers
    return dict((k, v) for k, v in data.items() if v is not None)


# ==========================================================================
# commands
# ==========================================================================

@command("clips.list", doc="List non-empty clips (session, optionally arrangement) of a track "
                           "or the whole set")
def clips_list(ctx, track=None, detail="minimal", include_arrangement=False,
               playing_only=False, offset=0, limit=200):
    """List the clips of one track or of every track.

    Args:
        track: index/name/path of one track; omit for every track in ``song.tracks``.
        detail: "minimal" (path, name, is_midi, length — default), "summary" or "full".
        include_arrangement: also list arrangement clips (they carry start_time/end_time).
        playing_only: only clips that are playing or triggered.
        offset, limit: paging over the result (limit max 2000).

    Returns:
        {"total", "offset", "count", "clips": [{path, name, is_midi, length, track, slot?,
         start_time?, end_time?, ...}], "next_offset"?}

    Gotchas:
        Empty slots are skipped. ``slot`` is the scene index; arrangement clips
        have no ``slot`` but a ``path`` ending in ``arrangement_clips[i]``.
    """
    check_detail(detail)
    include_arrangement = as_bool(include_arrangement, "include_arrangement")
    playing_only = as_bool(playing_only, "playing_only")
    tracks = [ctx.track(track)] if track is not None else \
        list(compat.safe_getattr(ctx.song, "tracks", ()) or ())
    found = []
    for track_obj in tracks:
        for slot in compat.safe_getattr(track_obj, "clip_slots", ()) or ():
            clip = compat.safe_getattr(slot, "clip")
            if clip is None:
                continue
            if playing_only and not (compat.safe_getattr(clip, "is_playing", False)
                                     or compat.safe_getattr(clip, "is_triggered", False)):
                continue
            found.append(clip)
        if include_arrangement:
            for clip in compat.safe_getattr(track_obj, "arrangement_clips", ()) or ():
                if playing_only and not compat.safe_getattr(clip, "is_playing", False):
                    continue
                found.append(clip)
    items, offset, limit = page(found, offset, limit)
    rows = [clip_row(ctx, clip, detail) for clip in items]
    if detail != "minimal":
        return paged_result("clips", rows, offset, limit, len(found))
    for row, clip in zip(rows, items):
        if compat.safe_getattr(clip, "is_playing", False):
            row["is_playing"] = True
    return paged_result("clips", rows, offset, limit, len(found))


@command("clips.get", doc="Everything about one clip (loop, markers, launch, audio props)")
def clips_get(ctx, track=None, slot=None, clip=None, detail="full",
              include_warp_markers=False):
    """Inspect one clip.

    Args:
        track, slot: session address; or ``clip`` = path / name / "selected".
        detail: "minimal", "summary" or "full" (default: launch settings, colour, ...).
        include_warp_markers: audio clips — add ``warp_markers`` as
            [[sample_time (seconds in the file), beat_time], ...]; a freshly
            warped clip reports its start marker plus one 1/32 beat later.

    Returns:
        The clip summary (path, name, is_midi, length, looping, loop_start/end,
        start/end_marker, muted, is_playing, signature, note_count or the audio
        fields warping/warp_mode/gain/gain_db/pitch_coarse/pitch_fine/file_path)
        plus track, slot, playing_position, is_session_clip.

    Gotchas:
        Positions are beats — for unwarped audio clips Live reports seconds.
        Notes are not included: use notes.get.
    """
    check_detail(detail)
    obj = resolve_clip(ctx, track, slot, clip)
    return clip_details(ctx, obj, detail, as_bool(include_warp_markers,
                                                  "include_warp_markers"))


@command("clips.create", mutating=True,
         doc="Create a MIDI clip (or an audio clip from a file) in a session slot")
def clips_create(ctx, track, slot=None, length=4.0, unit="beats", name=None,
                 color_index=None, looping=None, file_path=None):
    """Create a clip in a session clip slot.

    Args:
        track: index/name/path of a MIDI track (audio track with ``file_path``).
        slot: scene index or name; omit for the first empty slot.
        length: clip length (MIDI only), in ``unit`` — default 4 beats.
        unit: "beats" (default) or "bars" (uses the song's time signature).
        name: clip name.
        color_index: 0..69 (Live's palette).
        looping: set the loop switch (default: Live's default, on).
        file_path: absolute path of an audio file on the Live machine — creates
            an audio clip instead (``ClipSlot.create_audio_clip``).

    Returns:
        The new clip's summary plus ``track``/``slot``.

    Gotchas:
        Fails with invalid_state when the slot already holds a clip, when a
        MIDI clip is requested on an audio track (or vice versa) or when the
        track is frozen. Delete first or pick another slot.
    """
    check_unit(unit)
    track_obj = ctx.track(track)
    if not compat.safe_getattr(track_obj, "clip_slots", ()):
        raise BridgeError("invalid_state", "%r has no clip slots (return or main track)"
                          % compat.safe_getattr(track_obj, "name", ""))
    if slot is None:
        index = first_empty_slot(ctx, track_obj)
        if index is None:
            raise BridgeError("invalid_state", "track %r has no empty clip slot — pass a slot "
                              "or add a scene" % compat.safe_getattr(track_obj, "name", ""))
        clip_slot = track_obj.clip_slots[index]
    else:
        clip_slot = ctx.clip_slot(track_obj, slot)
    if compat.safe_getattr(clip_slot, "has_clip", False):
        raise BridgeError("invalid_state", "%s already holds clip %r — delete it first or "
                          "choose another slot"
                          % (ctx.path_of(clip_slot), compat.safe_getattr(clip_slot.clip, "name")))
    if file_path is not None:
        path = check_audio_file(file_path)
        if not compat.has(clip_slot, "create_audio_clip"):
            raise BridgeError("unsupported", "ClipSlot.create_audio_clip is not available in "
                              "this Live version (needs Live 12)")
        if compat.safe_getattr(track_obj, "has_midi_input", False):
            raise BridgeError("invalid_state", "%r is a MIDI track — audio clips need an audio "
                              "track" % compat.safe_getattr(track_obj, "name", ""))
        new_clip = live_call("create_audio_clip", clip_slot.create_audio_clip, path)
    else:
        if not compat.safe_getattr(track_obj, "has_midi_input", False):
            raise BridgeError("invalid_state", "%r is an audio track — MIDI clips need a MIDI "
                              "track (or pass file_path for an audio clip)"
                              % compat.safe_getattr(track_obj, "name", ""))
        beats = to_beats(ctx, length, unit, "length", allow_none=False)
        if beats <= 0:
            raise BridgeError("bad_args", "length must be > 0")
        new_clip = live_call("create_clip", clip_slot.create_clip, float(beats))
    if new_clip is None:
        new_clip = compat.safe_getattr(clip_slot, "clip")
    if name is not None:
        live_call("clip.name", setattr, new_clip, "name", str(name))
    if color_index is not None:
        live_call("clip.color_index", setattr, new_clip, "color_index",
                  as_int(color_index, "color_index", 0, 69))
    if looping is not None:
        live_call("clip.looping", setattr, new_clip, "looping", as_bool(looping, "looping"))
    return clip_row(ctx, new_clip, "summary")


def check_audio_file(file_path):
    """Validate an audio file path on the Live machine; returns the normalised path.

    The same checks as ``samples.import`` (``samples.require_file``): quotes from
    Windows Explorer's "Copy as path", ``file://`` URLs, ``~``, ``%VAR%`` /
    ``$VAR`` are handled, a Windows path while Live runs on macOS (or the other
    way round) is explained, and the file must exist and be an audio file.
    """
    if not isinstance(file_path, str) or not file_path.strip():
        raise BridgeError("bad_args", "file_path must be a non-empty string")
    from . import samples as samples_handlers  # lazy: samples imports browser
    return samples_handlers.require_file(file_path)


@command("clips.delete", mutating=True, doc="Delete a session or arrangement clip")
def clips_delete(ctx, track=None, slot=None, clip=None):
    """Delete a clip.

    Args:
        track, slot: session address; or ``clip`` = path / name / "selected"
            (arrangement clips by path, e.g. "song.tracks[0].arrangement_clips[1]").

    Returns:
        {"deleted": path, "name": clip name}

    Gotchas:
        Indices of later arrangement clips shift after a delete.
    """
    obj = resolve_clip(ctx, track, slot, clip)
    path = ctx.path_of(obj)
    name = compat.safe_getattr(obj, "name")
    clip_slot = owner_slot(obj)
    if clip_slot is not None:
        live_call("delete_clip", clip_slot.delete_clip)
    else:
        track_obj = owner_track(obj)
        if track_obj is None:
            raise BridgeError("invalid_state", "cannot find the track that owns this clip")
        live_call("delete_clip", track_obj.delete_clip, obj)
    return {"deleted": path, "name": name}


@command("clips.duplicate", mutating=True,
         doc="Duplicate a session clip to the next free slot or to a given slot")
def clips_duplicate(ctx, track, slot, target_track=None, target_slot=None, overwrite=False):
    """Duplicate a session clip.

    Args:
        track, slot: the source clip.
        target_track: destination track (default: the same track).
        target_slot: destination scene index/name. Omit (with no target_track)
            for the next EMPTY slot below the source on the same track; when
            every slot below is taken a scene is added at the end of the set.
        overwrite: allow replacing a clip already in the target slot.

    Returns:
        {"source": path, "target": path, "clip": {summary},
         "created_scene"?: index of the scene that had to be added}

    Gotchas:
        Source and target track must both be MIDI or both audio. Live cannot
        duplicate into group-track slots. Live's own
        ``Track.duplicate_clip_slot`` is not used: on Live 12.4.5 it
        overwrites the clip directly below instead of looking for a free
        slot (verified on the running Live).
    """
    source_slot = ctx.clip_slot(track, slot)
    if not compat.safe_getattr(source_slot, "has_clip", False):
        raise BridgeError("not_found", "%s: the clip slot is empty" % ctx.path_of(source_slot))
    overwrite = as_bool(overwrite, "overwrite")
    source_track = ctx.track(track)
    created_scene = None
    if target_slot is None and target_track is None:
        slots = list(compat.safe_getattr(source_track, "clip_slots", ()) or ())
        index = resolve.index_in(slots, source_slot)
        if index is None:
            raise BridgeError("invalid_state", "cannot find the source slot index")
        free = [i for i in range(index + 1, len(slots))
                if not compat.safe_getattr(slots[i], "has_clip", False)]
        if free:
            target = slots[free[0]]
        else:
            live_call("create_scene", ctx.song.create_scene, -1)
            slots = list(compat.safe_getattr(source_track, "clip_slots", ()) or ())
            created_scene = len(slots) - 1
            target = slots[created_scene]
        live_call("duplicate_clip_to", source_slot.duplicate_clip_to, target)
    else:
        destination_track = ctx.track(target_track) if target_track is not None else source_track
        if target_slot is None:
            index = first_empty_slot(ctx, destination_track)
            if index is None:
                raise BridgeError("invalid_state", "no empty slot on the target track")
            target = destination_track.clip_slots[index]
        else:
            target = ctx.clip_slot(destination_track, target_slot)
        if target == source_slot:
            raise BridgeError("bad_args", "target slot is the source slot")
        if compat.safe_getattr(target, "has_clip", False) and not overwrite:
            raise BridgeError("invalid_state", "%s already holds a clip — pass overwrite=true "
                              "to replace it" % ctx.path_of(target))
        live_call("duplicate_clip_to", source_slot.duplicate_clip_to, target)
    new_clip = compat.safe_getattr(target, "clip")
    result = {"source": ctx.path_of(source_slot.clip), "target": ctx.path_of(target),
              "clip": clip_row(ctx, new_clip, "minimal") if new_clip is not None else None}
    if created_scene is not None:
        result["created_scene"] = created_scene
    return result


@command("clips.fire", doc="Launch a session clip slot (or a clip)")
def clips_fire(ctx, track=None, slot=None, clip=None, force_legato=False,
               launch_quantization=None):
    """Fire a clip slot: plays its clip, or triggers the stop button when empty.

    Args:
        track, slot: session address; or ``clip`` = path / name / "selected".
        force_legato: start immediately, keeping the playhead in sync.
        launch_quantization: override the global launch quantization for this
            launch: "none", "8 bars", "4 bars", "2 bars", "1 bar", "1/2", "1/2T",
            "1/4", "1/4T", "1/8", "1/8T", "1/16", "1/16T", "1/32" (or 0..13).

    Returns:
        {"path", "has_clip", "is_triggered", "is_playing"}

    Gotchas:
        Firing is quantized to the launch quantization, so ``is_playing`` is
        often still false right after the call (``is_triggered`` is true).
        Launching a clip starts the transport when it is stopped. An empty
        slot on an armed track starts recording; on an unarmed track it
        only stops the track's clips (``force_legato`` is ignored there).
    """
    force_legato = as_bool(force_legato, "force_legato")
    if clip is not None:
        target = resolve_clip(ctx, track, slot, clip)
        clip_slot = owner_slot(target)
    else:
        clip_slot = resolve_slot(ctx, track, slot)
        target = None
    if clip_slot is None:
        live_call("clip.fire", target.fire)
        subject = target
    else:
        kwargs = {}
        if force_legato and compat.safe_getattr(clip_slot, "has_clip", False):
            # Live 12.4.5 raises "Can only pass force_legato to non-empty slots".
            kwargs["force_legato"] = True
        if launch_quantization is not None:
            kwargs["launch_quantization"] = enum_value(
                launch_quantization, serialize.SONG_QUANTIZATION, "launch_quantization",
                "Song.Quantization")
        live_call("fire", clip_slot.fire, **kwargs)
        subject = clip_slot
    inner = compat.safe_getattr(subject, "clip") if subject is clip_slot else subject
    return {
        "path": ctx.path_of(subject),
        "has_clip": inner is not None,
        "is_triggered": bool(compat.safe_getattr(subject, "is_triggered", False)),
        "is_playing": bool(compat.safe_getattr(subject, "is_playing", False)),
    }


@command("clips.stop", doc="Stop a clip, every clip of a track, or every clip in the set")
def clips_stop(ctx, track=None, slot=None, clip=None, quantized=True):
    """Stop clips.

    Args:
        track + slot (or ``clip``): stop that clip.
        track alone: stop every clip on the track.
        nothing: stop every clip in the set (``song.stop_all_clips``).
        quantized: follow the launch quantization (default) or stop at once
            (track/set only).

    Returns:
        {"stopped": "clip"|"track"|"all", "path"?}
    """
    quantized = as_bool(quantized, "quantized")
    if clip is not None or (track is not None and slot is not None):
        if clip is not None:
            obj = resolve_clip(ctx, track, slot, clip)
            clip_slot = owner_slot(obj)
            if clip_slot is None:
                live_call("clip.stop", obj.stop)
                return {"stopped": "clip", "path": ctx.path_of(obj)}
        else:
            clip_slot = ctx.clip_slot(track, slot)
        live_call("stop", clip_slot.stop)
        return {"stopped": "clip", "path": ctx.path_of(clip_slot)}
    if track is not None:
        track_obj = ctx.track(track)
        live_call("stop_all_clips", track_obj.stop_all_clips, Quantized=quantized)
        return {"stopped": "track", "path": ctx.path_of(track_obj)}
    live_call("stop_all_clips", ctx.song.stop_all_clips, Quantized=quantized)
    return {"stopped": "all"}


@command("clips.set", mutating=True,
         doc="Change clip properties: name, colour, loop, markers, length, launch, audio")
def clips_set(ctx, track=None, slot=None, clip=None, name=None, color_index=None, color=None,
              muted=None, looping=None, loop_start=None, loop_end=None, start_marker=None,
              end_marker=None, length=None, position=None, unit="beats", signature=None,
              launch_mode=None, launch_quantization=None, legato=None, velocity_amount=None,
              warping=None, warp_mode=None, gain=None, gain_db=None, pitch_coarse=None,
              pitch_fine=None, ram_mode=None, groove=None):
    """Set any number of clip properties in one undo step.

    Args:
        track, slot / clip: which clip (see module docs).
        name: new name.  color_index: 0..69.  color: "#RRGGBB", 0xRRGGBB, [r, g, b],
            a palette index 0..69 or a colour name ("red").
        muted: clip activator off (true) / on (false).
        looping: loop switch.
        loop_start, loop_end, start_marker, end_marker, position: in ``unit``
            (beats default; with unit="bars" they are 1-based bar numbers).
            Pairs are written in a safe order, so moving a loop later works.
            ``position`` moves the loop (loop_start, keeping its length).
            On an UNLOOPED clip Live keeps the clip start/end in
            loop_start/loop_end (start_marker is tied to loop_start, a
            written end_marker alone would not move the end), so there
            start_marker/loop_start both set the start and end_marker/loop_end
            both set the end (the command writes loop_end and end_marker
            together); pass looping=true in the same call to edit a real loop.
        length: loop length (looping clips) or start..end span (non-looping),
            in ``unit`` — sets the end to start + length.
        signature: clip time signature "3/4".
        launch_mode: "trigger", "gate", "toggle", "repeat".
        launch_quantization: "global", "none", "8 bars", "4 bars", "2 bars",
            "1 bar", "1/2", "1/2T", "1/4", "1/4T", "1/8", "1/8T", "1/16",
            "1/16T", "1/32" (or 0..14).
        legato: legato launch.  velocity_amount: 0..1 velocity→volume.
        groove: a groove of the set's Groove Pool (index or name, see
            clips.grooves) — the clip then plays with that groove's timing /
            velocity feel (like dragging a groove onto the clip).
        Audio clips only: warping (bool), warp_mode ("beats", "tones",
        "texture", "repitch", "complex", "rex", "complex_pro"), gain (raw
        0..1) or gain_db (dB, e.g. -6), pitch_coarse (-48..48 semitones),
        pitch_fine (cents; Live keeps -50..+49 and carries whole semitones
        into pitch_coarse, so +60 reads back as coarse +1 / fine -40), ram_mode.

    Returns:
        {"changed": [names], "clip": {full summary}, "adjusted"?: {prop: value
        Live actually kept}} — ``adjusted`` appears when Live moved or ignored
        a requested position (e.g. clamped to the clip's content).

    Gotchas:
        Turning warping off on a looping audio clip also switches looping
        off (Live cannot loop unwarped audio). A groove cannot be removed
        through the API (Live 12.4.5 rejects None) — assign another one or set
        its amounts to 0 with clips.grooves.
        Unwarped audio clips cannot loop, and their positions are seconds, not
        beats. Setting a property that does not apply (warp_mode on a MIDI
        clip) fails with invalid_state and nothing after it is applied (the
        earlier ones are — undo reverts the whole command).
    """
    check_unit(unit)
    obj = resolve_clip(ctx, track, slot, clip)
    is_midi = bool(compat.safe_getattr(obj, "is_midi_clip", False))
    changed = []
    audio_args = dict((k, v) for k, v in (("warping", warping), ("warp_mode", warp_mode),
                                          ("gain", gain), ("gain_db", gain_db),
                                          ("pitch_coarse", pitch_coarse),
                                          ("pitch_fine", pitch_fine), ("ram_mode", ram_mode))
                      if v is not None)
    if is_midi and audio_args:
        raise BridgeError("invalid_state", "%s only apply to audio clips; %s is a MIDI clip"
                          % (", ".join(sorted(audio_args)), ctx.path_of(obj)))
    if gain is not None and gain_db is not None:
        raise BridgeError("bad_args", "pass gain or gain_db, not both")

    def put(prop, value):
        live_call("clip." + prop, setattr, obj, prop, value)
        changed.append(prop)

    if name is not None:
        put("name", str(name))
    if color_index is not None:
        put("color_index", as_int(color_index, "color_index", 0, 69))
    if color is not None:
        mode, number = resolve.parse_color(color)
        put("color_index" if mode == "index" else "color", number)
    if muted is not None:
        put("muted", as_bool(muted, "muted"))
    if warping is not None:
        put("warping", as_bool(warping, "warping"))
    if warp_mode is not None:
        mode = enum_value(warp_mode, serialize.WARP_MODES, "warp_mode", "Clip.WarpMode")
        available = compat.safe_getattr(obj, "available_warp_modes")
        if available is not None:
            try:
                allowed = [int(m) for m in available]
            except (TypeError, ValueError):
                allowed = None
            if allowed is not None and mode not in allowed:
                raise BridgeError("invalid_state", "warp mode %r is not available for this "
                                  "clip (available: %s)"
                                  % (serialize.WARP_MODES.get(mode, mode),
                                     ", ".join(serialize.WARP_MODES.get(m, str(m))
                                               for m in allowed)))
        put("warp_mode", mode)
    if looping is not None:
        put("looping", as_bool(looping, "looping"))
    requested = {}
    if position is not None:
        value = to_beats(ctx, position, unit, "position", position=True)
        put("position", value)
        requested["position"] = value
    new_loop_start = to_beats(ctx, loop_start, unit, "loop_start", position=True)
    new_loop_end = to_beats(ctx, loop_end, unit, "loop_end", position=True)
    new_start = to_beats(ctx, start_marker, unit, "start_marker", position=True)
    new_end = to_beats(ctx, end_marker, unit, "end_marker", position=True)
    if compat.safe_getattr(obj, "looping", False):
        if new_loop_start is not None or new_loop_end is not None:
            _set_ordered(obj, "loop_start", "loop_end", new_loop_start, new_loop_end, "loop")
            for prop, value in (("loop_start", new_loop_start), ("loop_end", new_loop_end)):
                if value is not None:
                    changed.append(prop)
                    requested[prop] = value
        if new_start is not None or new_end is not None:
            _set_ordered(obj, "start_marker", "end_marker", new_start, new_end, "markers")
            for prop, value in (("start_marker", new_start), ("end_marker", new_end)):
                if value is not None:
                    changed.append(prop)
                    requested[prop] = value
    else:
        # Unlooped: the clip start/end live in loop_start/loop_end; start_marker
        # follows loop_start and end_marker is kept equal by write_region.
        for first, second, label in ((new_start, new_loop_start, "start"),
                                     (new_end, new_loop_end, "end")):
            if first is not None and second is not None and abs(first - second) > 1e-9:
                raise BridgeError("bad_args", "the clip does not loop, so loop_%s and %s_marker "
                                  "are the same point — pass one of them (or looping=true "
                                  "to edit the loop)" % (label, label))
        region_start = new_start if new_start is not None else new_loop_start
        region_end = new_end if new_end is not None else new_loop_end
        if region_start is not None or region_end is not None:
            write_region(obj, region_start, region_end)
            for prop, value in (("loop_start", new_loop_start), ("loop_end", new_loop_end),
                                ("start_marker", new_start), ("end_marker", new_end)):
                if value is not None:
                    changed.append(prop)
            if region_start is not None:
                requested["start_marker"] = region_start
            if region_end is not None:
                requested["end_marker"] = region_end
    if length is not None:
        beats = to_beats(ctx, length, unit, "length")
        if beats <= 0:
            raise BridgeError("bad_args", "length must be > 0")
        if compat.safe_getattr(obj, "looping", False):
            start = compat.safe_getattr(obj, "loop_start", 0.0) or 0.0
            put("loop_end", start + beats)
            requested["loop_end"] = start + beats
        else:
            start = clip_region(obj)[0]
            write_region(obj, None, start + beats)
            changed.append("end_marker")
            requested["end_marker"] = start + beats
    if signature is not None:
        numerator, denominator = _parse_signature(signature)
        put("signature_numerator", numerator)
        put("signature_denominator", denominator)
    if launch_mode is not None:
        put("launch_mode", enum_value(launch_mode, serialize.LAUNCH_MODES, "launch_mode",
                                      "Clip.LaunchMode"))
    if launch_quantization is not None:
        put("launch_quantization", enum_value(launch_quantization,
                                              serialize.CLIP_LAUNCH_QUANTIZATION,
                                              "launch_quantization",
                                              "Clip.ClipLaunchQuantization"))
    if legato is not None:
        put("legato", as_bool(legato, "legato"))
    if velocity_amount is not None:
        put("velocity_amount", as_float(velocity_amount, "velocity_amount", 0.0, 1.0))
    if groove is not None:
        put("groove", resolve_groove(ctx, groove))
    if gain is not None:
        put("gain", as_float(gain, "gain", 0.0, 1.0))
    if gain_db is not None:
        set_gain_db(obj, as_float(gain_db, "gain_db"))
        changed.append("gain")
    if pitch_coarse is not None:
        put("pitch_coarse", as_int(pitch_coarse, "pitch_coarse", -48, 48))
    if pitch_fine is not None:
        put("pitch_fine", as_float(pitch_fine, "pitch_fine", -500.0, 500.0))
    if ram_mode is not None:
        put("ram_mode", as_bool(ram_mode, "ram_mode"))
    if not changed:
        raise BridgeError("bad_args", "nothing to change — pass at least one property")
    result = {"changed": changed, "clip": clip_details(ctx, obj, "full")}
    adjusted = {}
    for prop, value in requested.items():
        actual = compat.safe_getattr(obj, "loop_start" if prop == "position" else prop)
        if actual is not None and abs(float(actual) - float(value)) > 1e-6:
            adjusted[prop] = rnd(actual)
    if adjusted:
        result["adjusted"] = adjusted
    return result


@command("clips.quantize", mutating=True,
         doc="Live's own quantize: MIDI notes, or warp markers of audio clips")
def clips_quantize(ctx, track=None, slot=None, clip=None, grid="1/16", amount=1.0):
    """Quantize a clip with Live's Quantize command (``Clip.quantize``).

    Args:
        track, slot / clip: which clip.
        grid: "1/4", "1/8", "1/8T", "1/8+1/8T", "1/16", "1/16T", "1/16+1/16T",
            "1/32" (or the Live RecordingQuantization number 1..8).
        amount: strength 0..1 (1 = fully on the grid).

    Returns:
        {"clip": path, "grid": name, "amount"}

    Gotchas:
        Works on every note of the clip (and on warp markers of audio clips).
        Live applies the song's swing amount. For selections, custom grids or
        quantizing note ends, use notes.transform (quantize=...).
    """
    obj = resolve_clip(ctx, track, slot, clip)
    grid_value = enum_value(grid, serialize.RECORD_QUANTIZATION, "grid",
                            "Song.RecordingQuantization")
    if grid_value == 0:
        raise BridgeError("bad_args", "grid 'none' does not quantize anything")
    amount = as_float(amount, "amount", 0.0, 1.0)
    live_call("quantize", obj.quantize, grid_value, amount)
    return {"clip": ctx.path_of(obj), "grid": serialize.RECORD_QUANTIZATION[grid_value],
            "amount": amount}


@command("clips.crop", mutating=True, doc="Crop a clip to its loop (or markers)")
def clips_crop(ctx, track=None, slot=None, clip=None):
    """Remove the parts of a clip that cannot play (Live's Crop).

    Looped clips keep the loop plus any lead-in between an earlier start
    marker and the loop start (Live 12.4.5 keeps
    ``min(start_marker, loop_start) .. loop_end``); unlooped clips keep their
    start..end. The kept part is moved to start at beat 0.

    Returns:
        {"clip": {summary after}}

    Gotchas:
        Notes outside the kept part are deleted (undo restores them).
    """
    obj = resolve_clip(ctx, track, slot, clip)
    live_call("crop", obj.crop)
    align_unlooped_end(obj)
    return {"clip": clip_row(ctx, obj, "summary")}


@command("clips.duplicate_loop", mutating=True,
         doc="Double the loop, duplicating its notes/envelopes")
def clips_duplicate_loop(ctx, track=None, slot=None, clip=None):
    """Make the loop twice as long and copy its content into the new half
    (Live's "Duplicate Loop"). An unlooped clip doubles its start..end.

    Returns:
        {"clip": {summary after}}

    Gotchas:
        Like in Live's UI, notes that were after the loop end move later by
        the loop length (they stay after the doubled loop).
    """
    obj = resolve_clip(ctx, track, slot, clip)
    live_call("duplicate_loop", obj.duplicate_loop)
    align_unlooped_end(obj)
    return {"clip": clip_row(ctx, obj, "summary")}


@command("clips.reverse", mutating=True,
         doc="Reverse a clip: MIDI notes are mirrored in the loop; audio where Live allows")
def clips_reverse(ctx, track=None, slot=None, clip=None):
    """Reverse a clip.

    MIDI clips: every note inside the loop (or inside the clip start..end
    when the clip does not loop) is mirrored in time, so the last note plays
    first; ids, velocities and expression are kept.  Audio clips: uses
    ``clip.reverse()`` when this Live version offers it.

    Returns:
        {"clip": path, "reversed": n notes (MIDI) | "audio"}

    Gotchas:
        Live 12.4's API has no audio-clip reverse (only ``SimplerDevice.reverse``)
        — the command then answers ``unsupported``; reverse the sample in
        Live's Clip view (Rev button) or in a Simpler instead.
    """
    obj = resolve_clip(ctx, track, slot, clip)
    if not compat.safe_getattr(obj, "is_midi_clip", False):
        if compat.has(obj, "reverse") and callable(compat.safe_getattr(obj, "reverse")):
            live_call("reverse", obj.reverse)
            return {"clip": ctx.path_of(obj), "reversed": "audio"}
        raise BridgeError("unsupported", "this Live version's API cannot reverse audio clips "
                          "(only Simpler.reverse exists) — use the Rev button in Live's Clip "
                          "view or reverse the sample in a Simpler")
    start, end = clip_region(obj)
    if not compat.has(obj, "get_all_notes_extended"):
        raise BridgeError("unsupported", "this Live version has no extended note API")
    vector = live_call("get_all_notes_extended", obj.get_all_notes_extended)
    count = 0
    for note in vector:
        if start - 1e-9 <= note.start_time < end - 1e-9:
            length = min(note.duration, end - note.start_time)
            note.start_time = max(start, end - (note.start_time - start) - length)
            note.duration = length
            count += 1
    if count:
        live_call("apply_note_modifications", obj.apply_note_modifications, vector)
    return {"clip": ctx.path_of(obj), "reversed": count}


# ==========================================================================
# grooves
# ==========================================================================

#: ``Live.Groove.Base`` (verified 12.4.5: gb_four=0 ... gb_thirtytwo=5).
GROOVE_BASES = {0: "1/4", 1: "1/8", 2: "1/8T", 3: "1/16", 4: "1/16T", 5: "1/32"}
_GROOVE_AMOUNTS = (("timing_amount", 0.0, 100.0), ("random_amount", 0.0, 100.0),
                   ("velocity_amount", -100.0, 100.0), ("quantization_amount", 0.0, 100.0))


def _grooves(ctx):
    pool = compat.safe_getattr(ctx.song, "groove_pool")
    if pool is None:
        raise BridgeError("unsupported", "this Live version has no song.groove_pool")
    return list(compat.safe_getattr(pool, "grooves", ()) or ())


def resolve_groove(ctx, spec):
    """A Groove of the pool from an index or a name (exact, case-insensitive, prefix,
    contains)."""
    if isinstance(spec, str) and spec.strip().lower() in ("none", "off", "no groove", ""):
        raise BridgeError("unsupported", "Live's API cannot remove a clip's groove (Live "
                          "12.4.5 rejects None) — assign another groove, or set the "
                          "groove's amounts to 0 with clips.grooves")
    grooves = _grooves(ctx)
    if not grooves:
        raise BridgeError("not_found", "the Groove Pool is empty — load a groove first (e.g. "
                          "live_browser_search for a .agr groove, or drag one from Live's "
                          "browser)")
    if isinstance(spec, bool):
        raise BridgeError("bad_args", "groove must be an index or a name")
    if isinstance(spec, (int, float)) or (isinstance(spec, str) and
                                          spec.strip().lstrip("-").isdigit()):
        index = as_int(int(float(spec)), "groove")
        if -len(grooves) <= index < len(grooves):
            return grooves[index]
        raise BridgeError("not_found", "groove %d: the pool has %d grooves" % (index,
                                                                                len(grooves)))
    if not isinstance(spec, str):
        raise BridgeError("bad_args", "groove must be an index or a name")
    wanted = spec.strip().lower()
    names = [str(compat.safe_getattr(g, "name", "")) for g in grooves]
    for test in (lambda n: n == spec.strip(), lambda n: n.lower() == wanted,
                 lambda n: n.lower().startswith(wanted), lambda n: wanted in n.lower()):
        hits = [g for g, n in zip(grooves, names) if test(n)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise BridgeError("bad_args", "groove %r is ambiguous: %s" % (spec, ", ".join(
                n for n in names if test(n))))
    raise BridgeError("not_found", "no groove %r in the pool (have: %s)" % (spec,
                                                                            ", ".join(names)))


def _groove_row(index, groove):
    get = compat.safe_getattr
    base = get(groove, "base")
    try:
        base = GROOVE_BASES.get(int(base), int(base))
    except (TypeError, ValueError):
        base = str(base)
    row = {"index": index, "name": get(groove, "name"), "base": base}
    for attr, _low, _high in _GROOVE_AMOUNTS:
        value = get(groove, attr)
        if value is not None:
            row[attr.replace("_amount", "")] = rnd(value, 2)
    return row


@command("clips.grooves", mutating=True,
         doc="The set's Groove Pool: list grooves, change a groove's amounts/base, see which "
             "clips use them")
def clips_grooves(ctx, groove=None, timing=None, random=None, velocity=None, quantize=None,
                  base=None, name=None, include_clips=False):
    """Read or edit the Groove Pool (``song.groove_pool.grooves``).

    Args:
        groove: the groove to change (index or name); omit to only list.
        timing, random, quantize: 0..100 (%); velocity: -100..100 (%) — the
            groove's Timing / Random / Quantize / Velocity amounts.
        base: the groove's base grid "1/4", "1/8", "1/8T", "1/16", "1/16T", "1/32".
        name: rename the groove.
        include_clips: also list the session clips that use each groove.

    Returns:
        {"count", "grooves": [{"index", "name", "base", "timing", "random",
         "velocity", "quantization", "clips"?}], "global_amount", "changed"?,
         "note"?}

    Gotchas:
        Assign a groove to a clip with clips.set groove=<name|index>. The
        global amount (``song.groove_amount``) is a transport setting
        (live_transport_set). New grooves come from the browser (.agr files);
        the API cannot add or remove pool entries.
    """
    grooves = _grooves(ctx)
    changed = []
    if groove is not None:
        target = resolve_groove(ctx, groove)
        values = {"timing_amount": timing, "random_amount": random,
                  "velocity_amount": velocity, "quantization_amount": quantize}
        for attr, low, high in _GROOVE_AMOUNTS:
            value = values[attr]
            if value is not None:
                live_call("groove." + attr, setattr, target, attr,
                          as_float(value, attr.replace("_amount", ""), low, high))
                changed.append(attr.replace("_amount", ""))
        if base is not None:
            wanted = str(base).strip().lower()
            codes = dict((v.lower(), k) for k, v in GROOVE_BASES.items())
            if wanted not in codes:
                raise BridgeError("bad_args", "base must be one of %s"
                                  % ", ".join(GROOVE_BASES.values()))
            live_call("groove.base", setattr, target, "base", codes[wanted])
            changed.append("base")
        if name is not None:
            live_call("groove.name", setattr, target, "name", str(name))
            changed.append("name")
        if not changed:
            raise BridgeError("bad_args", "nothing to change — pass timing, random, velocity, "
                              "quantize, base or name (omit groove to just list)")
    elif any(v is not None for v in (timing, random, velocity, quantize, base, name)):
        raise BridgeError("bad_args", "say which groove to change (groove=<index|name>)")
    rows = [_groove_row(index, g) for index, g in enumerate(grooves)]
    if as_bool(include_clips, "include_clips"):
        for row in rows:
            row["clips"] = []
        for clip_obj in _all_clips(ctx):
            if not compat.safe_getattr(clip_obj, "has_groove", False):
                continue
            used = compat.safe_getattr(clip_obj, "groove")
            for index, candidate in enumerate(grooves):
                if used is not None and used == candidate:
                    rows[index]["clips"].append(ctx.path_of(clip_obj))
    result = {"count": len(rows), "grooves": rows,
              "global_amount": rnd(compat.safe_getattr(ctx.song, "groove_amount", 1.0), 3)}
    if changed:
        result["changed"] = changed
    if not rows:
        result["note"] = ("the Groove Pool is empty — load a groove (.agr) from Live's browser "
                          "(Grooves / Swing) first")
    return result


# ==========================================================================
# warp markers
# ==========================================================================

def _marker_rows(clip):
    rows = []
    for marker in compat.safe_getattr(clip, "warp_markers", ()) or ():
        rows.append([rnd(compat.safe_getattr(marker, "sample_time"), 6),
                     rnd(compat.safe_getattr(marker, "beat_time"), 6)])
    return rows


def _seconds_at(clip, beat):
    """Where the audio currently plays at ``beat`` (seconds) — pins a new marker in place."""
    rate = compat.safe_getattr(clip, "sample_rate")
    if compat.has(clip, "beat_to_sample_time") and rate:
        ok, frames = compat.safe_call(clip, "beat_to_sample_time", float(beat))
        if ok and frames is not None:
            return float(frames) / float(rate)
    rows = sorted(_marker_rows(clip), key=lambda r: r[1])
    if len(rows) < 2:
        raise BridgeError("bad_args", "pass sample_time (seconds into the audio) — Live "
                          "cannot tell where beat %s is" % rnd(beat))
    for (s0, b0), (s1, b1) in zip(rows, rows[1:]):
        if b0 <= beat <= b1 or (b1 == rows[-1][1] and beat > b1):
            return s0 + (s1 - s0) * (beat - b0) / (b1 - b0) if b1 != b0 else s0
    s0, b0 = rows[0]
    s1, b1 = rows[1]
    return s0 + (s1 - s0) * (beat - b0) / (b1 - b0)


@command("clips.warp", mutating=True,
         doc="Warp markers of an audio clip: list, add (pinned or at a sample position), "
             "move, remove")
def clips_warp(ctx, action="list", track=None, slot=None, clip=None, beat_time=None,
               sample_time=None, distance=None, to=None):
    """Edit the warp markers of an audio clip (the clip must be warped).

    Args:
        action: "list", "add", "move" or "remove".
        track, slot / clip: the audio clip.
        beat_time: the marker's beat (clip beats) — add: where to put it;
            move / remove: which marker (the one at that beat).
        sample_time: add only — the position in the audio file in SECONDS
            that should sit at ``beat_time`` (default: where the audio plays
            at that beat now, i.e. a "pinned" marker that changes nothing
            until it is moved).
        distance: move only — beats to move the marker by (stretches the
            audio between its neighbours); or ``to``: the new beat.

    Returns:
        {"clip", "action", "warp_markers": [[sample_time_seconds, beat_time], ...],
         "count"}

    Gotchas:
        Live refuses markers on unwarped clips and moves that would cross a
        neighbour (invalid_state). Live keeps a helper marker 1/32 beat after
        the last one. To warp to a tempo in one go use clips.set warping /
        warp_mode; clips.quantize snaps warp markers to a grid.
    """
    obj = resolve_clip(ctx, track, slot, clip, need="audio")
    action = str(action).strip().lower()
    if action not in ("list", "add", "move", "remove"):
        raise BridgeError("bad_args", "action must be list, add, move or remove")
    if action != "list":
        if beat_time is None:
            raise BridgeError("bad_args", "beat_time is required for %s" % action)
        beat = as_float(beat_time, "beat_time")
        if action in ("add", "move") and not compat.safe_getattr(obj, "warping", False):
            raise BridgeError("invalid_state", "the clip is not warped — clips.set "
                              "warping=true first")
        if action == "add":
            seconds = _seconds_at(obj, beat) if sample_time is None else \
                as_float(sample_time, "sample_time", minimum=0.0)
            import Live  # only available inside Live (or the test stub)
            marker = Live.Clip.WarpMarker(float(seconds), float(beat))
            live_call("add_warp_marker", obj.add_warp_marker, marker)
        elif action == "move":
            if (distance is None) == (to is None):
                raise BridgeError("bad_args", "pass distance (beats) or to (the new beat)")
            delta = as_float(distance, "distance") if distance is not None else \
                as_float(to, "to") - beat
            existing = [row[1] for row in _marker_rows(obj)]
            if not any(abs(b - beat) < 1e-6 for b in existing):
                raise BridgeError("not_found", "no warp marker at beat %s (markers at %s)"
                                  % (rnd(beat), ", ".join(str(b) for b in existing)))
            live_call("move_warp_marker", obj.move_warp_marker, float(beat), float(delta))
        else:
            existing = [row[1] for row in _marker_rows(obj)]
            if not any(abs(b - beat) < 1e-6 for b in existing):
                raise BridgeError("not_found", "no warp marker at beat %s (markers at %s)"
                                  % (rnd(beat), ", ".join(str(b) for b in existing)))
            live_call("remove_warp_marker", obj.remove_warp_marker, float(beat))
    rows = _marker_rows(obj)
    return {"clip": ctx.path_of(obj), "action": action, "warp_markers": rows,
            "count": len(rows)}


# ==========================================================================
# conversions (Live.Conversions)
# ==========================================================================

_AUDIO_TO_MIDI = {"drums": 2, "drum": 2, "beat": 2, "melody": 1, "harmony": 0, "chords": 0,
                  "polyphonic": 0}


@command("clips.convert", mutating=True,
         doc="Live's audio conversions: audio clip -> MIDI (drums/melody/harmony), new Drum "
             "Rack track, new Simpler track")
def clips_convert(ctx, to, track=None, slot=None, clip=None, type="drums"):
    """Convert an audio clip with Live's own conversions (``Live.Conversions``).

    Args:
        to: "midi" — Convert Drums/Melody/Harmony to New MIDI Track;
            "drum_rack" — Convert to a new track with a Drum Rack (the clip on
            the first pad); "simpler" — a new MIDI track with a Simpler playing
            the clip.
        track, slot / clip: the audio clip (session or arrangement).
        type: for to="midi": "drums" (default), "melody" or "harmony".

    Returns:
        {"clip", "to", "type"?, "new_tracks": [{"index", "name"}], "pending"?,
         "note"?}

    Gotchas:
        to="midi" runs Live's audio analysis in the background: the new MIDI
        track ("Drums to MIDI" ...) appears a moment later (``pending``) —
        list tracks again. Live names and inserts the new tracks itself and
        selects them. Suite/Standard only (Intro/Lite lack audio-to-MIDI).
        Slicing a Simpler into a Drum Rack is a device action (not here).
    """
    obj = resolve_clip(ctx, track, slot, clip, need="audio")
    target = str(to).strip().lower().replace("-", "_").replace(" ", "_")
    try:
        import Live  # only available inside Live (or the test stub)
        conversions = getattr(Live, "Conversions", None)
    except ImportError:
        conversions = None
    if conversions is None:
        raise BridgeError("unsupported", "this Live version has no Live.Conversions (Live 12)")
    song = ctx.song
    before = list(compat.safe_getattr(song, "tracks", ()) or ())
    result = {"clip": ctx.path_of(obj), "to": target}
    if target in ("midi", "audio_to_midi"):
        kind = _AUDIO_TO_MIDI.get(str(type).strip().lower())
        if kind is None:
            raise BridgeError("bad_args", "type must be drums, melody or harmony")
        check = getattr(conversions, "is_convertible_to_midi", None)
        if check is not None:
            try:
                convertible = bool(check(song, obj))
            except Exception as error:
                raise BridgeError("invalid_state", "Live cannot convert this clip: %s" % error)
            if not convertible:
                raise BridgeError("invalid_state", "Live says this clip cannot be converted to "
                                  "MIDI")
        live_call("audio_to_midi_clip", conversions.audio_to_midi_clip, song, obj, kind)
        result["type"] = [k for k, v in _AUDIO_TO_MIDI.items() if v == kind][0]
    elif target in ("drum_rack", "drums_rack", "drumrack"):
        live_call("create_drum_rack_from_audio_clip",
                  conversions.create_drum_rack_from_audio_clip, song, obj)
    elif target in ("simpler", "midi_track_with_simpler"):
        live_call("create_midi_track_with_simpler",
                  conversions.create_midi_track_with_simpler, song, obj)
    else:
        raise BridgeError("bad_args", "to must be midi, drum_rack or simpler")
    after = list(compat.safe_getattr(song, "tracks", ()) or ())
    new = [(index, t) for index, t in enumerate(after) if not any(t == b for b in before)]
    result["new_tracks"] = [{"index": index, "name": compat.safe_getattr(t, "name")}
                            for index, t in new]
    if target in ("midi", "audio_to_midi") and not new:
        result["pending"] = True
        result["note"] = ("Live analyses the audio in the background — a new MIDI track "
                          "(e.g. 'Drums to MIDI') appears in a moment; list the tracks again")
    return result
