"""Shared argument resolvers and parsers — ONE implementation for every handler.

Several handler modules used to carry their own copy of the same helper
(track lookup, colour parsing, time signatures, ``bars.beats.sixteenths``
conversion, ``detail`` checks, paging).  They live here now so every command
accepts the same loose argument forms and answers with the same shapes:

* :func:`track` / :func:`tracks` — the ``track`` argument everywhere
  (``Context.track`` delegates here); :func:`scene` — the ``scene`` / ``slot``
  argument (``Context.scene`` delegates here); :func:`device` / :func:`parameter`
  (``Context.device`` / ``Context.parameter`` delegate here);
* :func:`parse_color` / :func:`apply_color` / :func:`color_info` — the
  ``color`` argument of tracks, clips and scenes;
* :func:`parse_signature`, :func:`signature_of` — ``"3/4"`` / ``[3, 4]``;
* :func:`beats_to_bbs`, :func:`bbs_to_beats`, :func:`parse_time`,
  :func:`time_row` — beats <-> ``"bars.beats.sixteenths"`` (1-based
  positions, 0-based lengths, signature aware);
* :func:`check_detail`, :func:`check_paging`, :func:`paged` — the
  ``detail`` / ``offset`` / ``limit`` arguments and the paged result shape
  ``{"total", "offset", "count", <key>: [...], "next_offset"?}``;
* :func:`index_in` — index of a LOM object in a collection (``==``);
* :func:`live_call` — call into Live, mapping its exceptions to protocol errors.

Standard library only (this runs inside Live's Python 3.11).
"""

import re

from . import compat
from . import serialize
from .registry import BridgeError

#: Accepted ``detail`` values (same tuple as :data:`serialize.DETAILS`).
DETAILS = serialize.DETAILS

#: Live's time-signature denominators.
DENOMINATORS = (1, 2, 4, 8, 16)

_SIXTEENTH = 0.25
_MAX_BEATS = 1e7
_BBS_RE = re.compile(r"^\s*(\d+)(?:[.:](\d+))?(?:[.:](\d+))?\s*$")
_SIGNATURE_RE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*$")
_LETTER_RE = re.compile(r"^(?:return\s*)?([a-lA-L])$")
_SELECTED = ("selected", "current", "selected_track", "selected track")
_MASTER = ("master", "master track", "master_track", "main", "main track")

#: Named colours -> Live palette index (Live 10-12 palette, 70 colours; the
#: mapping is approximate — UNVERIFIED against every Live skin).
COLOR_NAMES = {
    "pink": 0, "orange": 1, "gold": 2, "light yellow": 3, "lime": 4,
    "light green": 5, "mint": 6, "aqua": 7, "light blue": 8, "sky": 8,
    "periwinkle": 10, "lavender": 11, "rose": 12, "white": 13,
    "red": 14, "dark orange": 15, "brown": 16, "yellow": 17, "green": 19,
    "teal": 20, "cyan": 21, "blue": 22, "dark blue": 23, "purple": 24,
    "violet": 25, "magenta": 26, "grey": 27, "gray": 27, "silver": 27,
    "black": 55,
}


# --------------------------------------------------------------------------
# generic helpers
# --------------------------------------------------------------------------

def index_in(collection, obj):
    """Index of ``obj`` in a LOM collection (``==`` comparison), or ``None``."""
    for index, item in enumerate(collection or ()):
        if item is obj or item == obj:
            return index
    return None


def live_call(what, func, *args, **kwargs):
    """Call into Live and turn its exceptions into protocol errors — the one implementation.

    ``Live.Base.LimitationError`` (edition limits) becomes ``unsupported``;
    Boost.Python argument errors (``ArgumentError`` / ``TypeError``) become
    ``bad_args``; ``RuntimeError`` / ``ValueError`` / ``AssertionError`` /
    ``AttributeError`` (Live refusing the operation) become ``invalid_state`` —
    always with Live's own message.  Anything else propagates (-> ``internal``
    with a traceback), because it is a bug rather than a Live refusal.
    """
    try:
        return func(*args, **kwargs)
    except BridgeError:
        raise
    except Exception as error:  # Live raises plain RuntimeErrors
        name = type(error).__name__
        message = str(error).strip() or name
        limitation = compat.live_enum("Base.LimitationError")
        if name == "LimitationError" or (isinstance(limitation, type)
                                         and isinstance(error, limitation)):
            raise BridgeError("unsupported", "%s: this Live edition's limits prevent it (%s)"
                              % (what, message))
        if isinstance(error, TypeError) or name == "ArgumentError":
            raise BridgeError("bad_args", "%s: Live rejected the arguments (%s)"
                              % (what, message))
        if isinstance(error, (RuntimeError, ValueError, AssertionError, AttributeError)):
            raise BridgeError("invalid_state", "%s: %s" % (what, message))
        raise


def check_detail(detail):
    """Validate a ``detail`` argument (``"minimal"``, ``"summary"``, ``"full"``)."""
    if detail not in DETAILS:
        raise BridgeError("bad_args", "detail must be one of %s (got %r)"
                          % (", ".join(DETAILS), detail))
    return detail


def _as_int(value, name):
    if isinstance(value, bool):
        raise BridgeError("bad_args", "%s must be an integer" % name)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    raise BridgeError("bad_args", "%s must be an integer, got %r" % (name, value))


def check_paging(offset, limit, maximum=2000, allow_none=False):
    """Validate ``offset`` (>= 0) and ``limit`` (1..``maximum``).

    ``limit=None`` means "everything" when ``allow_none`` is true.

    Returns:
        ``(offset, limit)`` as ints (``limit`` may be ``None``).
    """
    offset = _as_int(0 if offset is None else offset, "offset")
    if offset < 0:
        raise BridgeError("bad_args", "offset must be >= 0 (got %d)" % offset)
    if limit is None:
        if allow_none:
            return offset, None
        raise BridgeError("bad_args", "limit is required")
    limit = _as_int(limit, "limit")
    if not 1 <= limit <= maximum:
        raise BridgeError("bad_args", "limit must be within 1..%d (got %d)" % (maximum, limit))
    return offset, limit


def paged(key, window, offset, total, extra=None):
    """The standard paged result.

    ``{"total": <matching items>, "offset": o, "count": <items returned>,
    <key>: window, "next_offset": o + count}`` — ``next_offset`` only when
    more items follow.  ``extra`` keys are merged in (they never override the
    paging keys).
    """
    window = list(window)
    result = {}
    if extra:
        result.update(extra)
    result.update({"total": total, "offset": offset, "count": len(window), key: window})
    if offset + len(window) < total:
        result["next_offset"] = offset + len(window)
    return result


# --------------------------------------------------------------------------
# tracks
# --------------------------------------------------------------------------

def _is_track(obj):
    return compat.has(obj, "clip_slots") and compat.has(obj, "mixer_device")


def _name(obj):
    return str(compat.safe_getattr(obj, "name", "") or "")


def normalise(text):
    """Lower-case ``text`` and drop everything but letters and digits."""
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def all_tracks(song, include_returns=True, include_master=True):
    """Regular tracks, then return tracks, then the master track."""
    result = list(compat.safe_getattr(song, "tracks", ()) or ())
    if include_returns:
        result += list(compat.safe_getattr(song, "return_tracks", ()) or ())
    if include_master:
        master = compat.safe_getattr(song, "master_track")
        if master is not None:
            result.append(master)
    return result


def _names(candidates, limit=12):
    return ", ".join(repr(_name(t)) for t in candidates[:limit])


def _by_name(song, text, include_returns, include_master):
    """Name lookup: exact, case-insensitive, unique prefix, then fuzzy."""
    candidates = all_tracks(song, include_returns, include_master)
    for obj in candidates:
        if _name(obj) == text:
            return obj
    lowered = text.lower()
    same = [t for t in candidates if _name(t).lower() == lowered]
    if len(same) >= 1:
        return same[0]
    prefix = [t for t in candidates if _name(t).lower().startswith(lowered)]
    if len(prefix) == 1:
        return prefix[0]
    if len(prefix) > 1:
        raise BridgeError("bad_args", "track %r is ambiguous: %s" % (text, _names(prefix, 6)))
    contains = [t for t in candidates if lowered in _name(t).lower()]
    if len(contains) == 1:
        return contains[0]
    wanted = normalise(text)
    if wanted:
        for test in (lambda n: n == wanted, lambda n: wanted in n):
            loose = [t for t in candidates if test(normalise(_name(t)))]
            if len(loose) == 1:
                return loose[0]
    if len(contains) > 1:
        raise BridgeError("bad_args", "track %r is ambiguous: %s" % (text, _names(contains, 6)))
    raise BridgeError("not_found", "no track named %r (have: %s)" % (text, _names(candidates)))


def _by_name_or_refuse(song, text, include_returns, include_master):
    """:func:`_by_name`, but a name that only matches a return track or the master
    while those are excluded answers ``bad_args`` ("'A-Reverb' is a return track —
    not allowed here") instead of a misleading "no track named ..."."""
    try:
        return _by_name(song, text, include_returns, include_master)
    except BridgeError as error:
        if error.type != "not_found" or (include_returns and include_master):
            raise
        try:
            obj = _by_name(song, text, True, True)
        except BridgeError:
            raise error
        master = compat.safe_getattr(song, "master_track")
        if master is not None and obj == master:
            raise BridgeError("bad_args", "%r is the master track — not allowed here"
                              % _name(obj))
        raise BridgeError("bad_args", "%r is a return track — not allowed here" % _name(obj))


def track(ctx, spec, include_returns=True, include_master=True):
    """Resolve a loose ``track`` argument to a Track — the one implementation.

    Accepted forms:

    * an int index into ``song.tracks`` (negative counts from the end), an
      integral float (``2.0``) or a numeric string;
    * a name: exact, then case-insensitive, then a unique case-insensitive
      prefix, then a unique *contains* / punctuation-insensitive match
      (``"areverb"`` -> ``"A-Reverb"``);
    * a return letter ``"A"`` / ``"return B"`` (unless a track is literally
      called that);
    * ``"master"`` / ``"main"``, ``"selected"`` (the selected track);
    * a LOM path (``"song.return_tracks[0]"``, ``"song.view.selected_track"``);
    * a Track object (returned unchanged).

    Args:
        include_returns: allow return tracks (by name, letter, path or object).
        include_master: allow the master track.

    Raises:
        BridgeError: ``not_found`` when nothing matches, ``bad_args`` for an
            unusable/ambiguous argument or a track kind that is not allowed
            (also when a name or letter names a return/the master that is
            excluded: "'A-Reverb' is a return track — not allowed here").
    """
    song = ctx.song
    if spec is None:
        raise BridgeError("bad_args", "track is required")
    if isinstance(spec, bool):
        raise BridgeError("bad_args", "track must be an index, a name or a path")
    if _is_track(spec):
        obj = spec
    elif isinstance(spec, float):
        if not spec.is_integer():
            raise BridgeError("bad_args", "track index must be a whole number, got %r" % spec)
        return track(ctx, int(spec), include_returns, include_master)
    elif isinstance(spec, int):
        regular = list(compat.safe_getattr(song, "tracks", ()) or ())
        if not -len(regular) <= spec < len(regular):
            raise BridgeError("not_found", "song.tracks[%d]: index out of range (%d tracks)"
                              % (spec, len(regular)))
        obj = regular[spec]
    elif isinstance(spec, str):
        text = spec.strip()
        lowered = text.lower()
        if not text:
            raise BridgeError("bad_args", "track must not be empty")
        if text.startswith("song.") or text in ("song", "app", "browser"):
            obj = ctx.resolve(text)
            if obj is None:
                raise BridgeError("not_found", "%s: no track there" % text)
            if not _is_track(obj):
                raise BridgeError("bad_args", "%s is a %s, not a track"
                                  % (text, serialize.kind_of(obj) or type(obj).__name__))
        elif lowered in _SELECTED:
            obj = compat.safe_getattr(ctx.view, "selected_track")
            if obj is None:
                raise BridgeError("not_found", "no track is selected")
        elif lowered in _MASTER:
            if not include_master:
                raise BridgeError("bad_args", "the master track is not allowed here")
            obj = compat.safe_getattr(song, "master_track")
            if obj is None:
                raise BridgeError("not_found", "this set has no master track")
        elif text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
            return track(ctx, int(text), include_returns, include_master)
        else:
            obj = None
            letter = _LETTER_RE.match(text)
            if letter:
                named = [t for t in all_tracks(song) if _name(t).lower() == lowered]
                returns = list(compat.safe_getattr(song, "return_tracks", ()) or ())
                index = ord(letter.group(1).upper()) - ord("A")
                if not named and index < len(returns):
                    if not include_returns:
                        raise BridgeError(
                            "bad_args", "%r is return track %s (%r) — return tracks are not "
                            "allowed here" % (text, letter.group(1).upper(),
                                              _name(returns[index])))
                    obj = returns[index]
            if obj is None:
                obj = _by_name_or_refuse(song, text, include_returns, include_master)
    else:
        raise BridgeError("bad_args", "track must be an index, a name or a path, got %s"
                          % type(spec).__name__)
    kind = serialize.track_type(obj, ctx)
    if kind == "master" and not include_master:
        raise BridgeError("bad_args", "the master track is not allowed here")
    if kind == "return" and not include_returns:
        raise BridgeError("bad_args", "return tracks are not allowed here (%r)" % _name(obj))
    return obj


def tracks(ctx, spec, include_returns=True, include_master=True):
    """Like :func:`track` but ``spec`` may also be a list or ``"all"``.

    ``"all"`` means every regular track (plus returns when allowed, never the
    master).  Duplicates are removed, order is kept.
    """
    if isinstance(spec, str) and spec.strip().lower() in ("all", "*", "every"):
        return all_tracks(ctx.song, include_returns, include_master=False)
    if isinstance(spec, (list, tuple)):
        if not spec:
            raise BridgeError("bad_args", "track list is empty")
        result = []
        for item in spec:
            obj = track(ctx, item, include_returns, include_master)
            if index_in(result, obj) is None:
                result.append(obj)
        return result
    return [track(ctx, spec, include_returns, include_master)]


# --------------------------------------------------------------------------
# scenes
# --------------------------------------------------------------------------

def _is_scene(obj):
    return serialize.kind_of(obj) == "scene"


def scene(ctx, spec):
    """Resolve a loose ``scene`` argument to a Scene — the one implementation.

    Accepted forms:

    * an int index into ``song.scenes`` (negative counts from the end), an
      integral float (``8.0``) or a numeric string;
    * a name: exact, then case-insensitive, then a unique case-insensitive
      prefix, then a unique *contains* / punctuation-insensitive match;
    * a LOM path to a scene (``"song.scenes[2]"``, ``"song.view.selected_scene"``);
    * a Scene object (returned unchanged).

    Raises:
        BridgeError: ``not_found`` when nothing matches (the message lists the
            scene names), ``bad_args`` for an unusable argument, a path that is
            not a scene, or an ambiguous name (the candidates are listed —
            the first match is never picked silently).
    """
    song = ctx.song
    if spec is not None and not isinstance(spec, (bool, int, float, str)) and _is_scene(spec):
        return spec
    if isinstance(spec, bool) or spec is None:
        raise BridgeError("bad_args", "scene must be an index, a name or a path")
    scenes = list(compat.safe_getattr(song, "scenes", ()) or ())
    if isinstance(spec, float):
        if not spec.is_integer():
            raise BridgeError("bad_args", "scene index must be a whole number, got %r" % spec)
        spec = int(spec)
    if isinstance(spec, int):
        if -len(scenes) <= spec < len(scenes):
            return scenes[spec]
        raise BridgeError("not_found", "song.scenes[%d]: index out of range (%d scenes)"
                          % (spec, len(scenes)))
    if not isinstance(spec, str):
        raise BridgeError("bad_args", "scene must be an index, a name or a path, got %s"
                          % type(spec).__name__)
    text = spec.strip()
    if not text:
        raise BridgeError("bad_args", "scene must not be empty")
    if text.startswith("song.") or text in ("song", "app", "browser"):
        obj = ctx.resolve(text)
        if obj is None:
            raise BridgeError("not_found", "%s: no scene there" % text)
        if not _is_scene(obj):
            raise BridgeError("bad_args", "%s is a %s, not a scene"
                              % (text, serialize.kind_of(obj) or type(obj).__name__))
        return obj
    if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
        return scene(ctx, int(text))
    for obj in scenes:
        if _name(obj) == text:
            return obj
    lowered = text.lower()
    same = [s for s in scenes if _name(s).lower() == lowered]
    if same:
        return same[0]
    prefix = [s for s in scenes if _name(s).lower().startswith(lowered)]
    if len(prefix) == 1:
        return prefix[0]
    if len(prefix) > 1:
        raise BridgeError("bad_args", "scene %r is ambiguous: %s — use the full name or an "
                          "index" % (text, _scene_names(scenes, prefix)))
    contains = [s for s in scenes if lowered in _name(s).lower()]
    if len(contains) == 1:
        return contains[0]
    wanted = normalise(text)
    if wanted and not contains:
        loose = [s for s in scenes if wanted in normalise(_name(s))]
        if len(loose) == 1:
            return loose[0]
        contains = loose
    if len(contains) > 1:
        raise BridgeError("bad_args", "scene %r is ambiguous: %s — use the full name or an "
                          "index" % (text, _scene_names(scenes, contains)))
    named = [s for s in scenes if _name(s)]
    raise BridgeError("not_found", "no scene named %r (%d scenes%s)"
                      % (text, len(scenes),
                         "; named: " + _scene_names(scenes, named) if named else ""))


def _scene_names(scenes, candidates, limit=8):
    parts = []
    for obj in candidates[:limit]:
        index = index_in(scenes, obj)
        parts.append("%r (#%s)" % (_name(obj), index))
    if len(candidates) > limit:
        parts.append("... %d more" % (len(candidates) - limit))
    return ", ".join(parts)


# --------------------------------------------------------------------------
# devices and parameters
# --------------------------------------------------------------------------

def device(ctx, track_spec, spec):
    """Resolve ``track`` + ``device`` to a Device — the one implementation.

    Delegates to ``handlers/devices.py`` (imported lazily: the handler module
    owns the device model — racks, chains, drum pads).  Accepted ``device``
    forms: an index (negative allowed), a name (exact, case-insensitive,
    class name, unique prefix, unique substring — searched inside racks too,
    top level first), a LOM path (also into chains) or a Device object;
    ``None`` = the track's selected (or only) device.  ``track`` takes every
    :func:`track` form; ``None`` = the selected track.
    """
    from .handlers import devices as device_handlers
    obj, _path = device_handlers.resolve_device(ctx, track_spec, spec)
    return obj


def parameter(ctx, device_obj, spec):
    """Resolve a parameter of ``device_obj`` (index, name, original name,
    unique prefix/substring, or LOM path) — the one implementation, shared with
    ``handlers/devices.py``."""
    from .handlers import devices as device_handlers
    obj, _index = device_handlers.find_parameter(ctx, device_obj, spec)
    return obj


# --------------------------------------------------------------------------
# colours
# --------------------------------------------------------------------------

def parse_color(value, name="color"):
    """A colour argument -> ``("index", n)`` or ``("rgb", 0xRRGGBB)``.

    ints 0..69 are palette indices, bigger ints are ``0xRRGGBB``;
    ``"#FF8800"`` / ``"FF8800"`` / ``"0xFF8800"`` / ``[255, 136, 0]`` are RGB;
    a digit string is an index; a colour name (``"red"``) maps to the palette.
    """
    if isinstance(value, bool) or value is None:
        raise BridgeError("bad_args", "%s must be a palette index, '#RRGGBB', [r,g,b] "
                          "or a colour name" % name)
    if isinstance(value, int):
        if 0 <= value <= 69:
            return ("index", value)
        if 0 <= value <= 0xFFFFFF:
            return ("rgb", value)
        raise BridgeError("bad_args", "%s %r is out of range" % (name, value))
    if isinstance(value, (list, tuple)):
        if len(value) != 3 or not all(isinstance(c, (int, float)) and not isinstance(c, bool)
                                      and 0 <= c <= 255 for c in value):
            raise BridgeError("bad_args", "an RGB %s is [r, g, b] with 0..255 each" % name)
        red, green, blue = (int(round(c)) for c in value)
        return ("rgb", (red << 16) | (green << 8) | blue)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in COLOR_NAMES:
            return ("index", COLOR_NAMES[text])
        if text.isdigit():
            return parse_color(int(text), name)
        hexa = text[1:] if text.startswith("#") else text[2:] if text.startswith("0x") else text
        if re.match(r"^[0-9a-f]{6}$", hexa):
            return ("rgb", int(hexa, 16))
        raise BridgeError("bad_args", "unknown %s %r (use a palette index 0-69, "
                          "'#RRGGBB' or one of: %s)"
                          % (name, value, ", ".join(sorted(COLOR_NAMES))))
    raise BridgeError("bad_args", "%s must be a number, a string or [r, g, b]" % name)


def color_info(obj):
    """``{"color_index": n, "color": "#rrggbb"}`` of a track, clip or scene."""
    raw = compat.safe_getattr(obj, "color")
    return {
        "color_index": compat.safe_getattr(obj, "color_index"),
        "color": ("#%06x" % int(raw)) if isinstance(raw, int) and not isinstance(raw, bool)
        else None,
    }


def apply_color(obj, value, name="color"):
    """Set ``obj``'s colour from :func:`parse_color` input; returns :func:`color_info`."""
    mode, number = parse_color(value, name)
    try:
        if mode == "index":
            obj.color_index = number
        else:
            obj.color = number
    except (ValueError, RuntimeError, TypeError, AttributeError) as error:
        raise BridgeError("invalid_state", "Live refused %s %r: %s" % (name, value, error))
    return color_info(obj)


# --------------------------------------------------------------------------
# signatures and bars.beats.sixteenths
# --------------------------------------------------------------------------

def parse_signature(value, name="signature"):
    """``"3/4"`` / ``[3, 4]`` -> ``(3, 4)`` (numerator 1..99, denominator 1/2/4/8/16).

    Raises:
        BridgeError: ``bad_args`` for anything else.
    """
    if isinstance(value, (list, tuple)) and len(value) == 2:
        numerator, denominator = value
    elif isinstance(value, str) and _SIGNATURE_RE.match(value):
        match = _SIGNATURE_RE.match(value)
        numerator, denominator = match.group(1), match.group(2)
    else:
        raise BridgeError("bad_args", "%s must look like '3/4' or [3, 4], got %r"
                          % (name, value))
    if isinstance(numerator, bool) or isinstance(denominator, bool):
        raise BridgeError("bad_args", "%s must hold two integers, got %r" % (name, value))
    try:
        numerator, denominator = int(numerator), int(denominator)
    except (TypeError, ValueError):
        raise BridgeError("bad_args", "%s must hold two integers, got %r" % (name, value))
    if not 1 <= numerator <= 99 or denominator not in DENOMINATORS:
        raise BridgeError("bad_args", "%s %r out of range (numerator 1..99, denominator "
                          "1, 2, 4, 8 or 16)" % (name, value))
    return numerator, denominator


def signature_of(owner, default=(4, 4)):
    """``(numerator, denominator)`` of a Song or Clip (``default`` if unreadable)."""
    numerator = compat.safe_getattr(owner, "signature_numerator", default[0])
    denominator = compat.safe_getattr(owner, "signature_denominator", default[1])
    try:
        numerator, denominator = int(numerator), int(denominator)
    except (TypeError, ValueError):
        return default
    if numerator < 1 or denominator < 1:
        return default
    return numerator, denominator


def grid(numerator=4, denominator=4):
    """``(bar_length, beat_length, sixteenth_length)`` in quarter-note beats."""
    beat = 4.0 / float(denominator)
    sub = _SIXTEENTH if beat >= _SIXTEENTH else beat
    return numerator * beat, beat, sub


def beats_per_bar(owner):
    """Quarter-note beats in one bar of ``owner``'s (Song/Clip) signature."""
    return grid(*signature_of(owner))[0]


def beats_to_bbs(beats, numerator=4, denominator=4, is_length=False):
    """Beats -> ``"bars.beats.sixteenths"``.

    Positions are 1-based (``8.0`` -> ``"3.1.1"`` in 4/4), lengths 0-based
    (``16.0`` -> ``"4.0.0"``).  A remainder below one sixteenth is dropped —
    the ``beats`` number stays the exact value.  Returns ``None`` for
    negative or non-numeric input.
    """
    try:
        value = float(beats)
    except (TypeError, ValueError):
        return None
    if value < 0 or value != value or value == float("inf"):
        return None
    bar_len, beat_len, sub = grid(numerator, denominator)
    value += 1e-7
    bars = int(value // bar_len)
    rest = value - bars * bar_len
    beat = int(rest // beat_len)
    rest -= beat * beat_len
    sixteenth = int(rest // sub)
    offset = 0 if is_length else 1
    return "%d.%d.%d" % (bars + offset, beat + offset, sixteenth + offset)


def bbs_to_beats(text, numerator=4, denominator=4, is_length=False):
    """``"bars[.beats[.sixteenths]]"`` -> beats.

    Raises:
        ValueError: with a readable message when ``text`` is not valid in
            this signature.
    """
    match = _BBS_RE.match(str(text))
    if match is None:
        raise ValueError("%r is not bars.beats.sixteenths (e.g. '9.1.1')" % (text,))
    bar_len, beat_len, sub = grid(numerator, denominator)
    per_beat = max(1, int(round(beat_len / sub)))
    bars = int(match.group(1))
    if is_length:
        beat = int(match.group(2)) if match.group(2) else 0
        sixteenth = int(match.group(3)) if match.group(3) else 0
        if beat >= numerator or sixteenth >= per_beat:
            raise ValueError("%r: as a length the beat part must be 0..%d and the sixteenth "
                             "part 0..%d (signature %d/%d)"
                             % (text, numerator - 1, per_beat - 1, numerator, denominator))
        return bars * bar_len + beat * beat_len + sixteenth * sub
    beat = int(match.group(2)) if match.group(2) else 1
    sixteenth = int(match.group(3)) if match.group(3) else 1
    if bars < 1 or not 1 <= beat <= numerator or not 1 <= sixteenth <= per_beat:
        raise ValueError("%r: positions are 1-based — bar >= 1, beat 1..%d, sixteenth 1..%d "
                         "(signature %d/%d)" % (text, numerator, per_beat, numerator,
                                                 denominator))
    return (bars - 1) * bar_len + (beat - 1) * beat_len + (sixteenth - 1) * sub


def is_bbs(value):
    """True for a full ``"bars.beats.sixteenths"`` string such as ``"17.1.1"``."""
    return isinstance(value, str) and re.match(r"^\s*\d+[.:]\d+[.:]\d+\s*$", value) is not None


def parse_time(owner, value, name, is_length=False, allow_negative=False):
    """A loose time argument -> beats (float).

    Args:
        owner: the Song or Clip whose signature applies to ``bbs`` strings.
        value: a number (beats) or a ``bars.beats.sixteenths`` string.
        name: argument name for error messages.
        is_length: parse strings as 0-based durations.
        allow_negative: accept negative numbers (offsets).

    Raises:
        BridgeError: ``bad_args`` with a message that shows the expected form.
    """
    if isinstance(value, bool) or value is None:
        raise BridgeError("bad_args", "%s must be beats (a number) or 'bars.beats.sixteenths'"
                          % name)
    if isinstance(value, (int, float)):
        beats = float(value)
        if beats != beats or abs(beats) > _MAX_BEATS:
            raise BridgeError("bad_args", "%s must be a finite number of beats" % name)
    elif isinstance(value, str):
        numerator, denominator = signature_of(owner)
        try:
            beats = bbs_to_beats(value.strip(), numerator, denominator, is_length)
        except ValueError as error:
            raise BridgeError("bad_args", "%s: %s — or send a number of beats" % (name, error))
    else:
        raise BridgeError("bad_args", "%s must be beats (a number) or 'bars.beats.sixteenths', "
                          "got %s" % (name, type(value).__name__))
    if beats < 0 and not allow_negative:
        raise BridgeError("bad_args", "%s must not be negative" % name)
    return float(beats)


def time_row(owner, beats, is_length=False, digits=4):
    """``{"beats": 8.0, "bbs": "3.1.1"}`` using ``owner``'s signature."""
    numerator, denominator = signature_of(owner)
    try:
        value = round(float(beats), digits)
        value = 0.0 if value == 0 else value
    except (TypeError, ValueError):
        value = None
    return {"beats": value, "bbs": beats_to_bbs(beats, numerator, denominator, is_length)}
