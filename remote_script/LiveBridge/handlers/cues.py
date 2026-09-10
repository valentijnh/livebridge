"""Cue points (arrangement locators) and song-time conversion.

Live's API for locators is small (``docs/LIVE_API_VERIFIED.md`` §4,
``docs/LIVE_API_DUMP_12.4.5.md`` ``Live.Song.CuePoint``):

* ``song.cue_points`` — in *creation* order on Live 12.4.5 (not by time; the
  handlers sort them, so every ``index`` here is the time order);
  ``CuePoint.name`` is writable,
  ``CuePoint.time`` is **read-only**, ``CuePoint.jump()`` moves the playhead
  (quantized while playing, otherwise it just moves the start position).
* ``song.set_or_delete_cue()`` takes no argument and returns None: when the
  playhead sits on a cue that cue is deleted, otherwise a new cue (named "1",
  "2", ...) is created at ``current_song_time``.  Live 12.4.5 applies a
  ``current_song_time`` write on its *next* tick, so adding/deleting/moving a
  cue at an arbitrary time takes two requests: park the playhead there
  (``{"pending": true, "retry": ...}``), then toggle on the retry and put the
  playhead back.  Every toggle is verified so it can never silently do the
  opposite.
* ``song.jump_to_next_cue()`` / ``jump_to_prev_cue()`` / ``can_jump_to_*``.
* Live refuses a playhead behind ``song.song_length`` (end of material, cues
  or loop brace + 32 beats).  A cue behind the end is still possible: the
  loop brace is stretched until the time is reachable (``transport.extend_song``)
  and put back on the last step — the new cue then keeps the song that long.
  So locators can be laid out before any clip exists (``cues.layout``).

Time notation shared by this module and ``handlers/automation.py``: numbers
are **beats** (quarter notes); strings are Live's ``bars.beats.sixteenths``
(``"9.1.1"`` = bar 9, 1-based for positions; lengths are 0-based durations,
``"4.0.0"`` = four bars).  Conversions use the song's (or a clip's) time
signature: in 6/8 a bar is 3 quarter notes and a "beat" is an eighth note.
"""

import re

from .. import compat
from .. import resolve
from ..registry import BridgeError, command
from . import transport

_EPS = 1e-6
#: Two cue times closer than this are the same locator.
_CUE_EPS = 1e-3
#: Suggested wait before a client sends the ``retry`` of a pending cue operation.
RETRY_MS = 150
#: Arrangement zoom-in steps per retry when Live snapped a new cue off target, and the max.
ZOOM_STEP = 6
MAX_ZOOM = 12

_NOTE_LENGTH_RE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*(t|d|\.)?\s*$", re.I)
_BARS_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(bars?|b|beats?)\s*$", re.I)


# --------------------------------------------------------------------------
# time helpers — also imported by handlers/automation.py
# --------------------------------------------------------------------------

def rnd(value, digits=4):
    """Round a float for output (``None`` stays ``None``)."""
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return value
    value = round(value, digits)
    return 0.0 if value == 0 else value


# The shared implementations (LiveBridge/resolve.py) — re-exported so
# ``handlers/automation.py`` (``from . import cues as timing``) keeps working.
signature_of = resolve.signature_of
parse_signature = resolve.parse_signature
grid = resolve.grid
beats_to_bbs = resolve.beats_to_bbs
bbs_to_beats = resolve.bbs_to_beats
parse_time = resolve.parse_time


def parse_length(owner, value, name):
    """A duration: beats, ``"1/4"`` (note value, ``"1/8T"`` triplet, ``"1/8D"``
    dotted), ``"2 bars"`` / ``"3 beats"`` or a 0-based bbs length ``"1.0.0"``.

    Raises:
        BridgeError: ``bad_args`` for anything else or a length <= 0.
    """
    if isinstance(value, str):
        text = value.strip()
        note = _NOTE_LENGTH_RE.match(text)
        if note:
            num, den = int(note.group(1)), int(note.group(2))
            if den == 0:
                raise BridgeError("bad_args", "%s: %r has a zero denominator" % (name, value))
            beats = 4.0 * num / den
            mark = (note.group(3) or "").lower()
            if mark == "t":
                beats *= 2.0 / 3.0
            elif mark in ("d", "."):
                beats *= 1.5
        else:
            bars = _BARS_RE.match(text)
            if bars:
                amount = float(bars.group(1))
                if bars.group(2).lower().startswith("beat"):
                    beats = amount
                else:
                    beats = amount * grid(*signature_of(owner))[0]
            else:
                beats = parse_time(owner, text, name, is_length=True)
    else:
        beats = parse_time(owner, value, name, is_length=True)
    if beats <= 0:
        raise BridgeError("bad_args", "%s must be longer than 0 beats" % name)
    return float(beats)


def time_row(owner, beats, is_length=False):
    """``{"beats": 8.0, "bbs": "3.1.1"}`` using ``owner``'s signature."""
    return resolve.time_row(owner, beats, is_length)


def signature_text(owner):
    numerator, denominator = signature_of(owner)
    return "%d/%d" % (numerator, denominator)


# --------------------------------------------------------------------------
# cue helpers
# --------------------------------------------------------------------------

def _cues(song):
    """The cue points in **time order**.

    Live 12.4.5 keeps ``song.cue_points`` in creation order (verified), so
    every ``index`` in this module is the position in time order; the LOM
    path uses the raw order (see :func:`cue_path`).
    """
    return sorted(compat.safe_getattr(song, "cue_points", ()) or (), key=_time_of)


def cue_path(song, cue):
    """``song.cue_points[<raw index>]`` for ``cue`` (``None`` when gone)."""
    for index, candidate in enumerate(compat.safe_getattr(song, "cue_points", ()) or ()):
        if _same(candidate, cue):
            return "song.cue_points[%d]" % index
    return None


def _same(a, b):
    if a is b:
        return True
    try:
        return bool(a == b)
    except Exception:
        return False


def _cue_index(song, cue):
    for index, candidate in enumerate(_cues(song)):
        if _same(candidate, cue):
            return index
    return None


def _time_of(cue):
    try:
        return float(compat.safe_getattr(cue, "time", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def cue_row(song, cue, index=None, with_length=False):
    """Compact row: ``{index, name, time, bbs}`` (+ ``length`` to the next cue
    and the LOM ``path`` with ``with_length``); ``index`` = time order."""
    if index is None:
        index = _cue_index(song, cue)
    time = _time_of(cue)
    row = {"index": index, "name": compat.safe_getattr(cue, "name", ""),
           "time": rnd(time), "bbs": time_row(song, time)["bbs"]}
    if with_length:
        later = [_time_of(c) for c in _cues(song) if _time_of(c) > time + _CUE_EPS]
        row["length"] = rnd(min(later) - time) if later else None
        row["path"] = cue_path(song, cue)
    return row


def cue_at(song, time):
    """The cue at ``time`` (within a millisecond of a beat) or ``None``."""
    for cue in _cues(song):
        if abs(_time_of(cue) - time) <= _CUE_EPS:
            return cue
    return None


def resolve_cue(ctx, spec):
    """A cue point from a loose argument.

    Accepts an int index in time order (negative from the end), a digit
    string, a LOM path (``"song.cue_points[1]"`` — Live's raw, creation
    order), ``"@<time>"`` (beats or
    bars.beats.sixteenths, e.g. ``"@32"`` / ``"@9.1.1"``), or a name (exact,
    then case-insensitive, then case-insensitive prefix; with duplicate names
    the earliest cue wins).

    Raises:
        BridgeError: ``not_found`` / ``bad_args``.
    """
    song = ctx.song
    cues = _cues(song)
    if compat.has(spec, "jump") and compat.has(spec, "time"):
        return spec
    if isinstance(spec, bool) or spec is None:
        raise BridgeError("bad_args", "cue must be an index, a name, '@<time>' or a path")
    if isinstance(spec, float) and spec.is_integer():
        spec = int(spec)
    if isinstance(spec, int):
        if not cues:
            raise BridgeError("not_found", "the set has no cue points (locators)")
        if -len(cues) <= spec < len(cues):
            return cues[spec]
        raise BridgeError("not_found", "cue index %d is out of range (%d cue points, "
                          "time order)" % (spec, len(cues)))
    if not isinstance(spec, str):
        raise BridgeError("bad_args", "cue must be an index, a name, '@<time>' or a path, got %s"
                          % type(spec).__name__)
    text = spec.strip()
    if not text:
        raise BridgeError("bad_args", "cue must not be empty")
    if text.startswith("song."):
        obj = ctx.resolve(text)
        if not (compat.has(obj, "jump") and compat.has(obj, "time")):
            raise BridgeError("bad_args", "%s is not a cue point" % text)
        return obj
    if text.lstrip("-").isdigit():
        return resolve_cue(ctx, int(text))
    if text.startswith("@"):
        raw = text[1:].strip()
        try:
            value = float(raw)
        except ValueError:
            value = raw
        time = parse_time(song, value, "cue time")
        cue = cue_at(song, time)
        if cue is None:
            raise BridgeError("not_found", "no cue point at %s (have: %s)"
                              % (time_row(song, time)["bbs"], _cue_names(song)))
        return cue
    if not cues:
        raise BridgeError("not_found", "the set has no cue points (locators)")
    for cue in cues:
        if compat.safe_getattr(cue, "name", None) == text:
            return cue
    lowered = text.lower()
    for cue in cues:
        if str(compat.safe_getattr(cue, "name", "")).lower() == lowered:
            return cue
    for cue in cues:
        if str(compat.safe_getattr(cue, "name", "")).lower().startswith(lowered):
            return cue
    raise BridgeError("not_found", "no cue point named %r (have: %s)" % (text, _cue_names(song)))


def _cue_names(song, limit=16):
    rows = []
    for index, cue in enumerate(_cues(song)[:limit]):
        rows.append("%d %r@%s" % (index, compat.safe_getattr(cue, "name", ""),
                                  time_row(song, _time_of(cue))["bbs"]))
    return ", ".join(rows) or "none"


def _set_position(song, beats):
    try:
        song.current_song_time = float(beats)
    except Exception as error:
        raise BridgeError("invalid_state", "Live refused to move the playhead to %s: %s"
                          % (rnd(beats), error))


def _playhead(song):
    return float(compat.safe_getattr(song, "current_song_time", 0.0) or 0.0)


def _playing(song):
    return bool(compat.safe_getattr(song, "is_playing", False))


def _at_playhead(song, time):
    return abs(_playhead(song) - float(time)) <= _CUE_EPS


def _toggle(song):
    if not compat.has(song, "set_or_delete_cue"):
        raise BridgeError("unsupported", "this Live version has no song.set_or_delete_cue()")
    try:
        song.set_or_delete_cue()
    except Exception as error:
        raise BridgeError("invalid_state", "Live refused set_or_delete_cue: %s" % error)


def _marker_on_cue(song):
    """``song.is_cue_point_selected()``: is Live's (insert-marker) playhead on a cue?

    ``None`` when the method is missing.  Stopped, Live's insert marker can
    differ from the reported ``current_song_time`` (``jump_by`` moves only the
    reported time; writes are snapped to the Arrangement grid), and
    ``set_or_delete_cue()`` acts at the marker.
    """
    ok, value = compat.safe_call(song, "is_cue_point_selected")
    return bool(value) if ok else None


def _toggle_create(song, name=None):
    """Create a cue at the marker with a verified toggle; returns the new cue."""
    before = _cues(song)
    _toggle(song)
    after = _cues(song)
    new = [c for c in after if not any(_same(c, b) for b in before)]
    if not new:
        if len(after) < len(before):
            # Live deleted a cue it considered to be at the playhead: put it back.
            _toggle(song)
        raise BridgeError("invalid_state", "Live did not create a cue point at %s"
                          % time_row(song, _playhead(song))["bbs"])
    cue = new[0]
    if name is not None:
        try:
            cue.name = name
        except Exception as error:
            raise BridgeError("invalid_state", "cue created but Live refused the name %r: %s"
                              % (name, error))
    return cue


def _toggle_delete(song, cue):
    """Delete ``cue`` (Live's marker must sit on it) with a verified toggle."""
    count = len(_cues(song))
    _toggle(song)
    after = _cues(song)
    if len(after) >= count or any(_same(c, cue) for c in after):
        if len(after) > count:
            # The toggle created a cue instead — remove it again.
            _toggle(song)
        raise BridgeError("invalid_state", "Live did not delete the cue point at %s"
                          % time_row(song, _time_of(cue))["bbs"])


def _check_target(song, time, what):
    """A cue operation away from the playhead needs a stopped transport (and a
    time LiveBridge can make reachable, see ``transport.extend_song``)."""
    if _playing(song):
        raise BridgeError("invalid_state", "%s at %s needs the transport stopped: Live only "
                          "sets/deletes a locator at the playhead, and while playing the "
                          "playhead cannot be parked there (stop playback — the MCP tools do "
                          "it with stop_playback=true — or omit time to use the moving "
                          "playhead)" % (what, time_row(song, time)["bbs"]))
    transport.check_reachable(song, time, "time")


def _pending(song, target, cmd, args, extra=None, cue=None):
    """Park Live's playhead on ``target`` (via ``cue.jump()`` when ``cue`` is
    given — exact) and tell the client to send ``retry``.

    Live 12.4.5 moves the playhead on its next tick and ``set_or_delete_cue()``
    acts at the playhead Live then reports, so an operation away from the
    playhead takes (at least) two requests.
    """
    if cue is not None:
        try:
            cue.jump()
        except Exception as error:
            raise BridgeError("invalid_state", "Live refused to jump to the cue: %s" % error)
    else:
        _set_position(song, target)
    data = {"pending": True, "retry": {"cmd": cmd, "args": args},
            "retry_after_ms": RETRY_MS,
            "reason": "the playhead moves to %s on Live's next tick — send retry to finish"
                      % time_row(song, target)["bbs"]}
    if extra:
        data.update(extra)
    return data


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BridgeError("bad_args", "%s must be a number of beats" % name)
    if value < 0:
        raise BridgeError("bad_args", "%s must not be negative" % name)
    return float(value)


def _restore_value(song, restore):
    """What to put back at the end: ``[playhead, start marker]`` — the caller's
    (pending protocol) or the current ones.  ``CuePoint.jump()`` also moves
    Live's start marker, so both are restored.  A 4-item list also carries
    the loop brace ``[..., loop_start, loop_length]`` stretched to reach a
    time behind the end of the song (see :func:`_reach`)."""
    if restore is None:
        start = compat.safe_getattr(song, "start_time", None)
        return [rnd(_playhead(song), 6),
                rnd(start, 6) if isinstance(start, (int, float)) else None]
    if isinstance(restore, (list, tuple)):
        if len(restore) not in (2, 4):
            raise BridgeError("bad_args", "restore must be [playhead, start_marker] or "
                              "[playhead, start_marker, loop_start, loop_length]")
        value = [_number(restore[0], "restore"),
                 None if restore[1] is None else _number(restore[1], "restore")]
        if len(restore) == 4:
            value += [_number(restore[2], "restore"), _number(restore[3], "restore")]
        return value
    return [_number(restore, "restore"), None]


def _reach(song, time, restore):
    """The restore list for an operation at ``time``, after making ``time``
    reachable: Live refuses a playhead behind ``song_length``, so the loop
    brace is stretched (``transport.extend_song``) and its original values
    are appended to the list, for :func:`_finish` to put back."""
    keep = _restore_value(song, restore)
    saved = transport.extend_song(song, time, "time")
    if saved and len(keep) == 2:
        keep += [rnd(saved[0], 6), rnd(saved[1], 6)]
    return keep


def _zoom_steps(value):
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_ZOOM:
        raise BridgeError("bad_args", "zoom must be an integer 0..%d" % MAX_ZOOM)
    return value


def _zoom_arranger(ctx, steps):
    """Zoom the Arrangement's time axis in (``steps`` > 0) or back out (< 0).

    ``app.view.zoom_view`` direction 3 (right) zooms in, 2 (left) zooms out
    (verified on Live 12.4.5); the finer grid lets Live place a new locator
    closer to the requested time.  Returns the number of steps done.
    """
    app_view = compat.safe_getattr(ctx.app, "view")
    if not steps or not compat.has(app_view, "zoom_view"):
        return 0
    direction = 3 if steps > 0 else 2
    done = 0
    for _ in range(abs(steps)):
        ok, _result = compat.safe_call(app_view, "zoom_view", direction, "Arranger", False)
        if not ok:
            break
        done += 1
    return done


def _finish(ctx, restore, zoom=0):
    """Put the playhead (and start marker) back where the operation found them —
    the playhead is applied on Live's next tick —, the loop brace when it was
    stretched to reach a time behind the song end, and undo the Arrangement
    zoom-in.

    A playhead that playback carried behind the material is behind
    ``song_length`` again once the transport stopped (Live refuses it — found
    on 12.4.5 with ``stop_playback``), so the song is extended for the restore
    as well and the brace put back after it.
    """
    song = ctx.song
    values = _restore_value(song, restore) if restore is not None else None
    if values is not None and len(values) == 4:
        if not transport.restore_loop(song, (values[2], values[3])):
            raise BridgeError("invalid_state", "Live refused to put the loop brace back to "
                              "%s..%s" % (rnd(values[2]), rnd(values[2] + values[3])))
    if values is not None and not _playing(song):
        playhead, start = values[0], values[1]
        move = abs(_playhead(song) - playhead) > _EPS
        mark = start is not None and abs(float(compat.safe_getattr(song, "start_time", 0.0)
                                               or 0.0) - start) > _EPS
        saved = None
        if move or mark:
            saved = transport.extend_song(song, max(playhead if move else 0.0,
                                                    start if mark else 0.0), "restore")
        try:
            if move:
                _set_position(song, playhead)
            if mark:
                try:
                    song.start_time = start
                except Exception as error:
                    raise BridgeError("invalid_state", "Live refused to put the start marker "
                                      "back to %s: %s" % (rnd(start), error))
        finally:
            transport.restore_loop(song, saved)
    if zoom:
        _zoom_arranger(ctx, -zoom)


def create_cue(song, time, name=None):
    """Create a cue at ``time`` when Live's playhead is already there (or reuse
    the cue at that time) — no playhead move, no retry.

    Returns:
        ``(cue, created)``.
    Raises:
        BridgeError: ``invalid_state`` when the playhead is elsewhere or Live
        did not create a cue.
    """
    existing = cue_at(song, time)
    if existing is not None:
        if name is not None:
            existing.name = name
        return existing, False
    if not _at_playhead(song, time) or _marker_on_cue(song):
        raise BridgeError("invalid_state", "the playhead is not at %s"
                          % time_row(song, time)["bbs"])
    return _toggle_create(song, name), True


def _check_name(name):
    if name is None:
        return None
    if not isinstance(name, str):
        raise BridgeError("bad_args", "name must be a string")
    return name


def position_info(song, position=None):
    """Where the playhead is (or ``position``, when given) relative to the cue points."""
    if position is None:
        position = _playhead(song)
    position = float(position)
    cues = _cues(song)
    at = previous = following = None
    for index, cue in enumerate(cues):
        time = _time_of(cue)
        if abs(time - position) <= _CUE_EPS:
            at = (index, cue)
        elif time < position:
            previous = (index, cue)
        elif following is None and time > position:
            following = (index, cue)
    data = {"position": time_row(song, position), "is_playing": _playing(song)}
    data["at"] = cue_row(song, at[1], at[0]) if at else None
    data["previous"] = cue_row(song, previous[1], previous[0]) if previous else None
    data["next"] = cue_row(song, following[1], following[0]) if following else None
    section = at or previous
    data["section"] = compat.safe_getattr(section[1], "name", "") if section else None
    if previous or at:
        data["since_section_start"] = rnd(position - _time_of((at or previous)[1]))
    if following:
        data["until_next"] = rnd(_time_of(following[1]) - position)
    return data


def _place(ctx, target, name, zoom, again, restore_at):
    """One attempt to create a cue at ``target`` (stopped transport).

    Returns ``(pending, None)`` — the pending result from ``again(zoom)`` when
    the playhead is not there yet or the Arrangement grid made Live miss the
    time (the zoom-in is recorded in the new ``zoom``) — or ``(None, cue)``.
    """
    song = ctx.song
    if not _at_playhead(song, target):
        return again(zoom), None
    if _marker_on_cue(song):
        # Live snapped its insert marker onto another cue: a toggle would delete it.
        if zoom + ZOOM_STEP <= MAX_ZOOM:
            return again(zoom + _zoom_arranger(ctx, ZOOM_STEP)), None
        _finish(ctx, restore_at, zoom)
        raise BridgeError("invalid_state", "Live keeps the playhead on an existing cue near %s "
                          "(its Arrangement grid is too coarse) — zoom the Arrangement in "
                          "(view.zoom direction='right') and retry"
                          % time_row(song, target)["bbs"])
    cue = _toggle_create(song, name)
    if abs(_time_of(cue) - target) > _CUE_EPS and zoom + ZOOM_STEP <= MAX_ZOOM:
        # Live snapped the new locator to the Arrangement grid: remove it, zoom the
        # Arrangement in (finer grid) and place it again on the next round.
        _toggle_delete(song, cue)
        return again(zoom + _zoom_arranger(ctx, ZOOM_STEP)), None
    return None, cue


def _add_flow(ctx, target, name, restore, zoom, cmd, extra=None):
    """Create a cue at ``target`` (stopped transport, time given) — one step of
    the pending protocol.  Shared by ``cues.add`` and ``cues.toggle``."""
    song = ctx.song
    restore_at = _reach(song, target, restore)

    def again(zoom_after):
        args = {"time": rnd(target, 6), "restore": restore_at}
        if name is not None and cmd == "cues.add":
            args["name"] = name
        if zoom_after:
            args["zoom"] = zoom_after
        return _pending(song, target, cmd, args, extra)

    pending, cue = _place(ctx, target, name, zoom, again, restore_at)
    if pending is not None:
        return pending
    actual = _time_of(cue)
    _finish(ctx, restore_at, zoom)
    row = cue_row(song, cue)
    data = {"cue": row, "count": len(_cues(song))}
    if abs(actual - target) > _CUE_EPS:
        data["snapped"] = True
        data["requested"] = time_row(song, target)
    return data


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

@command("cues.list", doc="List cue points (locators) with times, section lengths and "
                          "where the playhead is")
def cues_list(ctx, include_position=True):
    """List every arrangement cue point (locator).

    Args:
        include_position: add ``position`` (see ``cues.position``).

    Returns:
        {"count", "signature": "4/4",
         "cues": [{"index", "name", "time" (beats), "bbs": "9.1.1",
                   "length" (beats to the next cue, None for the last), "path"}],
         "position"?: {...}}

    Gotchas:
        Cue ``index`` values are the time order and shift when cues are
        added/deleted before them.  ``path`` is the LOM path — Live keeps
        ``song.cue_points`` in creation order, so it can differ from index.
    """
    song = ctx.song
    cues = _cues(song)
    result = {"count": len(cues), "signature": signature_text(song),
              "cues": [cue_row(song, cue, index, with_length=True)
                       for index, cue in enumerate(cues)]}
    if include_position:
        result["position"] = position_info(song)
    return result


@command("cues.position", doc="Playhead position relative to the cue points")
def cues_position(ctx):
    """Where is the playhead, in which section, and how far to the next cue.

    Args:
        none.

    Returns:
        {"position": {"beats", "bbs"}, "is_playing", "at": cue|None,
         "previous": cue|None, "next": cue|None, "section": name of the cue at
         or before the playhead (None before the first cue),
         "since_section_start"?: beats, "until_next"?: beats}
        where a cue is {"index", "name", "time", "bbs"}.
    """
    return position_info(ctx.song)


@command("cues.add", mutating=True,
         doc="Add a cue point at a time (default: the playhead), optionally named")
def cues_add(ctx, time=None, name=None, restore=None, zoom=None):
    """Create a locator.

    Args:
        time: beats or "bars.beats.sixteenths" (default: current playhead).
        name: optional name for the new cue (Live names new cues "1", "2", ...).
        restore, zoom: bookkeeping of the pending protocol ([playhead, start
            marker] to put back, Arrangement zoom steps to undo) — omit them.

    Returns:
        {"created": bool, "cue": {"index", "name", "time", "bbs"}, "count",
        "snapped"?: true, "requested"?: {beats, bbs}} — ``created`` is False
        when a cue already existed at that time (it is renamed when ``name``
        is given, never deleted) — or a pending result.

    Gotchas:
        Cue times are read-only in Live; to move a cue use ``cues.set``.
        Stopped, Live snaps a new locator to the Arrangement grid (it depends
        on the zoom): when it lands off the requested time LiveBridge removes
        it, zooms the Arrangement in and places it again (the zoom is undone
        afterwards); if even the finest grid misses (odd times such as 12.7
        beats) the closest position is kept and ``snapped: true`` is reported.

    Pending protocol:
        Live 12.4.5 only sets/deletes a locator at the playhead and moves the
        playhead on its *next* tick.  When the playhead is not already at the
        time, the command parks it there and returns ``{"pending": true,
        "retry": {"cmd", "args"}, "retry_after_ms", "reason"}`` — send
        ``retry`` (after ~150 ms) until the result is final; the last step
        puts the playhead back.  The MCP tools do this automatically.  Times
        away from the playhead need a stopped transport (``invalid_state``
        while playing) and must lie inside the song length (``bad_args``).
    """
    song = ctx.song
    name = _check_name(name)
    zoom = _zoom_steps(zoom)
    target = _playhead(song) if time is None else parse_time(song, time, "time")
    existing = cue_at(song, target)
    if existing is not None:
        if name is not None:
            try:
                existing.name = name
            except Exception as error:
                raise BridgeError("invalid_state", "Live refused the name %r: %s"
                                  % (name, error))
        _finish(ctx, restore, zoom)
        return {"created": False, "cue": cue_row(song, existing), "count": len(_cues(song))}
    if time is None or (_playing(song) and _at_playhead(song, target)):
        if _marker_on_cue(song) and not _playing(song):
            raise BridgeError("invalid_state", "Live's playhead sits on a cue point next to "
                              "%s — pass time" % time_row(song, target)["bbs"])
        cue = _toggle_create(song, name)
        return {"created": True, "cue": cue_row(song, cue), "count": len(_cues(song))}
    _check_target(song, target, "adding a cue point")
    data = _add_flow(ctx, target, name, restore, zoom, "cues.add")
    if not data.get("pending"):
        data["created"] = True
    return data


@command("cues.toggle", mutating=True,
         doc="Live's Set/Delete Locator button at a time (default: the playhead)")
def cues_toggle(ctx, time=None, restore=None, zoom=None):
    """Toggle a locator: delete the cue at ``time`` if there is one, else add one.

    Args:
        time: beats or "bars.beats.sixteenths" (default: current playhead —
            exactly Live's Set/Delete button).
        restore, zoom: pending-protocol bookkeeping (omit).

    Returns:
        {"action": "added"|"deleted", "cue": {...}, "count", "snapped"?} or a
        pending result.

    Pending protocol:
        As ``cues.add``: times away from the playhead take several requests
        (``{"pending": true, "retry": {...}}``), need a stopped transport and
        must lie inside the song length.  Deleting parks the playhead with the
        cue's own ``jump()`` (exact).
    """
    song = ctx.song
    zoom = _zoom_steps(zoom)
    if time is None:
        before = _cues(song)
        rows = dict((id(c), cue_row(song, c, i)) for i, c in enumerate(before))
        _toggle(song)
        after = _cues(song)
        new = [c for c in after if not any(_same(c, b) for b in before)]
        gone = [b for b in before if not any(_same(b, c) for c in after)]
        if new:
            return {"action": "added", "cue": cue_row(song, new[0]), "count": len(after)}
        if gone:
            return {"action": "deleted", "cue": rows[id(gone[0])], "count": len(after)}
        raise BridgeError("invalid_state", "Live's Set/Delete Locator did nothing at %s"
                          % time_row(song, _playhead(song))["bbs"])
    target = parse_time(song, time, "time")
    existing = cue_at(song, target)
    if existing is not None:
        if _at_playhead(song, target) and _marker_on_cue(song) is not False:
            row = cue_row(song, existing)
            _toggle_delete(song, existing)
            _finish(ctx, restore, zoom)
            return {"action": "deleted", "cue": row, "count": len(_cues(song))}
        _check_target(song, target, "deleting a cue point")
        return _pending(song, target, "cues.toggle",
                        {"time": rnd(target, 6), "restore": _restore_value(song, restore)},
                        cue=existing)
    if _playing(song) and _at_playhead(song, target):
        cue = _toggle_create(song)
        return {"action": "added", "cue": cue_row(song, cue), "count": len(_cues(song))}
    _check_target(song, target, "adding a cue point")
    data = _add_flow(ctx, target, None, restore, zoom, "cues.toggle")
    if not data.get("pending"):
        data["action"] = "added"
    return data


@command("cues.delete", mutating=True, doc="Delete one cue point (or all of them)")
def cues_delete(ctx, cue=None, all=False, restore=None):
    """Delete locators.

    Args:
        cue: index, name, "@<time>" or path of the cue to delete.
        all: delete every cue point (``cue`` must be omitted).
        restore: [playhead, start marker] to put back when done (pending
            protocol; omit).

    Returns:
        {"deleted": [{"index", "name", "time", "bbs"}], "count": remaining} —
        or a pending result that also carries the ``deleted`` rows so far
        (``all`` needs one round per cue that is not under the playhead).

    Gotchas:
        Indices of later cues shift down after a delete.

    Pending protocol:
        Live deletes a locator only when its playhead sits on it, and moves
        the playhead on its next tick: the command parks the playhead with
        the cue's ``jump()`` and returns ``{"pending": true, "retry": {...}}``
        — send ``retry`` (~150 ms later) until the result is final; the last
        step puts the playhead back.  Needs a stopped transport for cues not
        under the playhead.  The MCP tools follow the protocol automatically.
    """
    song = ctx.song
    if all and cue is not None:
        raise BridgeError("bad_args", "pass cue or all=true, not both")
    if not all and cue is None:
        raise BridgeError("bad_args", "say which cue to delete (cue=index|name|'@time') "
                          "or pass all=true")
    targets = list(_cues(song)) if all else [resolve_cue(ctx, cue)]
    targets.sort(key=lambda c: 0 if _at_playhead(song, _time_of(c)) else 1)
    deleted = []
    for target in targets:
        time = _time_of(target)
        if not (_at_playhead(song, time) and _marker_on_cue(song) is not False):
            _check_target(song, time, "deleting a cue point")
            args = {"all": True} if all else {"cue": "@%s" % rnd(time, 6)}
            args["restore"] = _restore_value(song, restore)
            return _pending(song, time, "cues.delete", args,
                            {"deleted": deleted, "count": len(_cues(song))}, cue=target)
        row = cue_row(song, target)
        _toggle_delete(song, target)
        deleted.append(row)
    _finish(ctx, restore)
    return {"deleted": deleted, "count": len(_cues(song))}


@command("cues.set", mutating=True, doc="Rename and/or move a cue point")
def cues_set(ctx, cue, name=None, time=None, restore=None):
    """Rename a locator, move it, or both.

    Args:
        cue: index, name, "@<time>" or path.
        name: new name.
        time: new position (beats or "bars.beats.sixteenths").
        restore: [playhead, start marker] to put back when done (pending
            protocol; omit).

    Returns:
        {"cue": {"index", "name", "time", "bbs"}, "moved": bool, "renamed": bool}
        for a rename (a move onto an existing cue adds "merged": true).  A
        move is delete + re-create (``CuePoint.time`` is read-only): it runs
        through the pending protocol and its last step is ``cues.add``
        (result ``{"created", "cue", "count", "snapped"?}``; the MCP tool
        reports it as ``moved``).

    Gotchas:
        Moving a cue changes its index.  Moving onto an existing cue merges
        into that one (it gets the name).  Same pending protocol, stopped
        transport and grid snapping as ``cues.add`` / ``cues.delete``.
    """
    song = ctx.song
    name = _check_name(name)
    if name is None and time is None:
        raise BridgeError("bad_args", "pass name and/or time")
    target = resolve_cue(ctx, cue)
    old_name = compat.safe_getattr(target, "name", "")
    new_name = old_name if name is None else name
    old_time = _time_of(target)
    new_time = None if time is None else parse_time(song, time, "time")
    if new_time is None or abs(new_time - old_time) <= _CUE_EPS:
        if name is not None:
            try:
                target.name = name
            except Exception as error:
                raise BridgeError("invalid_state", "Live refused the name %r: %s"
                                  % (name, error))
        _finish(ctx, restore)
        return {"cue": cue_row(song, target), "moved": False,
                "renamed": new_name != old_name}
    _check_target(song, new_time, "moving a cue point")
    keep = _reach(song, new_time, restore)
    if not (_at_playhead(song, old_time) and _marker_on_cue(song) is not False):
        args = {"cue": "@%s" % rnd(old_time, 6), "time": rnd(new_time, 6), "restore": keep}
        if name is not None:
            args["name"] = name
        return _pending(song, old_time, "cues.set", args, cue=target)
    _toggle_delete(song, target)
    merge = cue_at(song, new_time)
    if merge is not None:
        try:
            merge.name = new_name
        except Exception as error:
            raise BridgeError("invalid_state", "Live refused the name %r: %s"
                              % (new_name, error))
        _finish(ctx, keep)
        return {"cue": cue_row(song, merge), "moved": True, "renamed": new_name != old_name,
                "merged": True}
    return _pending(song, new_time, "cues.add",
                    {"time": rnd(new_time, 6), "name": new_name, "restore": keep},
                    {"moved_from": {"name": old_name, "time": rnd(old_time),
                                    "bbs": time_row(song, old_time)["bbs"]}})


#: Most locators one ``cues.layout`` call accepts.
MAX_LAYOUT = 64


def _layout_items(song, cues):
    """The layout list -> ``[(time, name)]`` sorted by time (validated, no writes)."""
    if not isinstance(cues, (list, tuple)) or not cues:
        raise BridgeError("bad_args", "cues must be a non-empty list of {name, time}, "
                          "{name, bar} or [time, name]")
    if len(cues) > MAX_LAYOUT:
        raise BridgeError("bad_args", "at most %d cue points per layout" % MAX_LAYOUT)
    bar_len = grid(*signature_of(song))[0]
    items = {}
    for index, item in enumerate(cues):
        label = "cues[%d]" % index
        if isinstance(item, dict):
            unknown = sorted(set(item) - {"name", "time", "bar"})
            if unknown:
                raise BridgeError("bad_args", "%s: unknown key(s) %s (use name, time or bar)"
                                  % (label, ", ".join(unknown)))
            if ("time" in item) == ("bar" in item):
                raise BridgeError("bad_args", "%s needs time or bar (one of them)" % label)
            name = item.get("name")
            if "bar" in item:
                bar = item["bar"]
                if isinstance(bar, bool) or not isinstance(bar, (int, float)) or bar < 1:
                    raise BridgeError("bad_args", "%s.bar must be a bar number >= 1 "
                                      "(1-based)" % label)
                time = (float(bar) - 1.0) * bar_len
            else:
                time = parse_time(song, item["time"], "%s.time" % label)
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            time = parse_time(song, item[0], "%s time" % label)
            name = item[1]
        else:
            raise BridgeError("bad_args", "%s must be {name, time}, {name, bar} or "
                              "[time, name]" % label)
        if name is not None and not isinstance(name, str):
            raise BridgeError("bad_args", "%s: name must be a string" % label)
        key = round(time, 6)
        if key in items:
            raise BridgeError("bad_args", "%s: two cue points at %s"
                              % (label, time_row(song, time)["bbs"]))
        items[key] = (float(time), name)
    return [items[key] for key in sorted(items)]


def _layout_state(done):
    """The pending-protocol bookkeeping of ``cues.layout`` (what earlier rounds did)."""
    state = {"created": [], "renamed": [], "deleted": [], "snapped": []}
    if done is None:
        return state
    if not isinstance(done, dict):
        raise BridgeError("bad_args", "done is pending-protocol bookkeeping — omit it")
    for key in state:
        value = done.get(key, [])
        if not isinstance(value, list):
            raise BridgeError("bad_args", "done.%s must be a list" % key)
        state[key] = list(value)
    return state


def _short_row(song, cue):
    time = _time_of(cue)
    return {"name": compat.safe_getattr(cue, "name", ""), "time": rnd(time),
            "bbs": time_row(song, time)["bbs"]}


@command("cues.layout", mutating=True,
         doc="Lay out many cue points (song sections) in one go, optionally replacing the rest")
def cues_layout(ctx, cues, replace=False, restore=None, zoom=None, done=None):
    """Set a whole list of locators — e.g. the sections of a song — at once.

    Args:
        cues: list of ``{"name": "Verse", "time": 32}`` (beats or
            "bars.beats.sixteenths"), ``{"name": "Verse", "bar": 9}``
            (1-based bar number, song signature) or ``[time, name]``; at most
            64, times must differ.  A cue already at a time is renamed (a
            ``null`` name keeps its name), never deleted.
        replace: also delete every existing cue that is not in the list.
        restore, zoom, done: pending-protocol bookkeeping (playhead / start
            marker / loop brace to put back, Arrangement zoom steps to undo,
            what earlier rounds did) — omit them.

    Returns:
        {"cues": [{"index", "name", "time", "bbs"}] (the layout's cues, time
        order), "created": [{"name", "time", "bbs"}], "renamed": [...],
        "deleted": [...], "count": all cues in the set, "snapped"?:
        [{"requested", "time"}]} — or a pending result.

    Pending protocol:
        As ``cues.add``: Live only sets/deletes a locator at the playhead and
        moves the playhead on its next tick, so every cue that has to be
        created or deleted away from the playhead costs one round
        (``{"pending": true, "retry": {"cmd": "cues.layout", "args"}, ...}``
        — send ``retry`` after ~150 ms until the result is final; the MCP tool
        does it).  Needs a stopped transport when anything has to be created
        or deleted (``invalid_state`` while playing; renames always work).
        Times behind the end of the song extend it (the loop brace is
        stretched and put back on the last round), so a structure can be laid
        out in an empty arrangement.  Grid snapping is handled like
        ``cues.add``.
    """
    song = ctx.song
    items = _layout_items(song, cues)
    if not isinstance(replace, bool):
        raise BridgeError("bad_args", "replace must be true or false")
    zoom = _zoom_steps(zoom)
    state = _layout_state(done)
    snapped = dict((round(float(pair[0]), 6), float(pair[1])) for pair in state["snapped"]
                   if isinstance(pair, (list, tuple)) and len(pair) == 2)
    layout_times = [t for t, _n in items] + list(snapped.values())

    def in_layout(time):
        return any(abs(time - t) <= _CUE_EPS for t in layout_times)

    def open_items():
        return [(t, n) for t, n in items
                if cue_at(song, t) is None and round(t, 6) not in snapped]

    stale = [c for c in _cues(song) if not in_layout(_time_of(c))] if replace else []
    todo = open_items()
    for time, _name in todo:
        _check_target(song, time, "laying out cue points")
    for cue in stale:
        _check_target(song, _time_of(cue), "deleting a cue point")
    for time, name in items:
        existing = cue_at(song, time)
        if existing is not None and name is not None and \
                compat.safe_getattr(existing, "name", None) != name:
            try:
                existing.name = name
            except Exception as error:
                raise BridgeError("invalid_state", "Live refused the name %r: %s"
                                  % (name, error))
            state["renamed"].append(_short_row(song, existing))
    keep = _reach(song, max(t for t, _n in todo), restore) if todo else \
        _restore_value(song, restore)
    layout_args = [{"time": rnd(t, 6), "name": n} for t, n in items]

    def again(target, zoom_after, cue=None):
        args = {"cues": layout_args, "restore": keep, "done": state}
        if replace:
            args["replace"] = True
        if zoom_after:
            args["zoom"] = zoom_after
        left = len(open_items()) + len([c for c in _cues(song) if replace and
                                        not in_layout(_time_of(c))])
        return _pending(song, target, "cues.layout", args, {"left": left}, cue=cue)

    for cue in stale:
        time = _time_of(cue)
        if not (_at_playhead(song, time) and _marker_on_cue(song) is not False):
            return again(time, zoom, cue=cue)
        row = _short_row(song, cue)
        _toggle_delete(song, cue)
        state["deleted"].append(row)
    for time, name in todo:
        pending, cue = _place(ctx, time, name, zoom,
                              lambda zoom_after, target=time: again(target, zoom_after), keep)
        if pending is not None:
            return pending
        state["created"].append(_short_row(song, cue))
        if abs(_time_of(cue) - time) > _CUE_EPS:
            state["snapped"].append([rnd(time, 6), rnd(_time_of(cue), 6)])
            snapped[round(time, 6)] = _time_of(cue)
    _finish(ctx, keep, zoom)
    rows = []
    for index, cue in enumerate(_cues(song)):
        time = _time_of(cue)
        if any(abs(time - t) <= _CUE_EPS for t, _n in items) or \
                any(abs(time - t) <= _CUE_EPS for t in snapped.values()):
            rows.append(cue_row(song, cue, index))
    result = {"cues": rows, "created": state["created"], "renamed": state["renamed"],
              "deleted": state["deleted"], "count": len(_cues(song))}
    if state["snapped"]:
        result["snapped"] = [{"requested": time_row(song, a)["bbs"], "time": rnd(b)}
                             for a, b in state["snapped"]]
    return result


@command("cues.jump", doc="Jump to a cue point by index/name, or to the next/previous one")
def cues_jump(ctx, cue=None, direction=None):
    """Move the playhead to a locator.

    Args:
        cue: index, name, "@<time>" or path.
        direction: "next", "prev"/"previous", "first" or "last" (instead of cue).

    Returns:
        {"jumped": bool, "target": cue|None, "quantized": bool,
         "position": {"beats", "bbs"}, "reason"?: str}.

    Gotchas:
        Live moves the playhead on its next tick.  Stopped, ``position`` is
        the cue (the start marker moves there too).  While playing Live
        quantizes the jump to the global launch quantization
        (``quantized: true``) and ``position`` is still the current playhead.
    """
    song = ctx.song
    if (cue is None) == (direction is None):
        raise BridgeError("bad_args", "pass either cue or direction (next|prev|first|last)")
    playing = _playing(song)
    cues = _cues(song)
    if direction is not None:
        key = str(direction).strip().lower()
        if key in ("first", "last"):
            if not cues:
                return {"jumped": False, "target": None, "quantized": playing,
                        "reason": "the set has no cue points",
                        "position": time_row(song, _playhead(song))}
            cue = 0 if key == "first" else -1
        elif key in ("next", "prev", "previous"):
            position = _playhead(song)
            if key == "next":
                later = [c for c in cues if _time_of(c) > position + _CUE_EPS]
                target = later[0] if later else None
                can = compat.safe_getattr(song, "can_jump_to_next_cue", None)
                method = "jump_to_next_cue"
            else:
                earlier = [c for c in cues if _time_of(c) < position - _CUE_EPS]
                target = earlier[-1] if earlier else None
                can = compat.safe_getattr(song, "can_jump_to_prev_cue", None)
                method = "jump_to_prev_cue"
            if target is None or can is False:
                return {"jumped": False, "target": None, "quantized": playing,
                        "reason": "no cue point %s the playhead"
                                  % ("after" if key == "next" else "before"),
                        "position": time_row(song, position)}
            ok, _result = compat.safe_call(song, method)
            if not ok:
                ok, _result = compat.safe_call(target, "jump")
            if not ok:
                raise BridgeError("invalid_state", "Live refused %s" % method)
            return _jumped(song, target, playing)
        else:
            raise BridgeError("bad_args", "direction must be next, prev, first or last")
    target = resolve_cue(ctx, cue)
    try:
        target.jump()
    except Exception as error:
        raise BridgeError("invalid_state", "Live refused to jump to the cue: %s" % error)
    return _jumped(song, target, playing)


def _jumped(song, target, playing):
    time = _time_of(target)
    if not playing:
        # Stopped, CuePoint.jump() moves the playhead (and start marker) on
        # Live's next tick; writing the same time makes sure it lands there.
        _set_position(song, time)
    return {"jumped": True, "target": cue_row(song, target), "quantized": playing,
            "position": time_row(song, _playhead(song) if playing else time)}


@command("cues.loop", mutating=True,
         doc="Set the arrangement loop to span from one cue point to another")
def cues_loop(ctx, start, end=None, enable=True, jump=False):
    """Loop a section between two locators.

    Args:
        start: the cue where the loop starts (index, name, "@<time>", path).
        end: the cue where it ends (exclusive); default: the next cue after
            ``start``.
        enable: switch the arrangement loop on (False leaves it as it is).
        jump: also move the playhead to the loop start.

    Returns:
        {"loop": {"on", "start", "end", "length", "start_bbs", "end_bbs",
         "length_bbs"}, "from": cue, "to": cue}.

    Gotchas:
        Fails with ``invalid_state`` when ``start`` is the last cue and no
        ``end`` is given.  The loop is the arrangement loop brace
        (``song.loop_start``/``loop_length``), it does not affect session
        clips; ``on`` is the requested state (Live switches the loop on its
        next tick).
    """
    song = ctx.song
    first = resolve_cue(ctx, start)
    begin = _time_of(first)
    if end is None:
        later = [c for c in _cues(song) if _time_of(c) > begin + _CUE_EPS]
        if not later:
            raise BridgeError("invalid_state", "cue %r is the last cue point — pass end"
                              % compat.safe_getattr(first, "name", ""))
        last = later[0]
    else:
        last = resolve_cue(ctx, end)
    finish = _time_of(last)
    if finish <= begin + _CUE_EPS:
        raise BridgeError("bad_args", "the end cue (%s) must come after the start cue (%s)"
                          % (time_row(song, finish)["bbs"], time_row(song, begin)["bbs"]))
    loop = transport.apply_loop(song, begin, finish - begin, True if enable else None)
    if jump:
        _set_position(song, begin)
    return {"loop": _loop_row(loop), "from": cue_row(song, first), "to": cue_row(song, last)}


def _loop_row(loop):
    return dict((key, loop.get(key)) for key in ("on", "start", "end", "length",
                                                 "start_bbs", "end_bbs", "length_bbs"))


@command("cues.convert_time",
         doc="Convert beats <-> bars.beats.sixteenths (and seconds) with the song signature")
def cues_convert_time(ctx, beats=None, bbs=None, is_length=False, signature=None):
    """Convert song times.

    Args:
        beats: a number or a list of numbers (quarter-note beats).
        bbs: a "bars.beats.sixteenths" string or a list of them.
        is_length: treat values as durations (0-based "4.0.0" = four bars)
            instead of positions (1-based "5.1.1" = bar 5).
        signature: override the song signature ("3/4").

    Returns:
        {"signature": "4/4", "tempo", "beats_per_bar",
         "results": [{"beats", "bbs", "seconds", "bars"}]} — ``seconds`` at
        the current song tempo (ignores tempo automation); ``bars`` is the
        position/length in bars as a float (0-based for positions).
    """
    if beats is None and bbs is None:
        raise BridgeError("bad_args", "pass beats and/or bbs")
    song = ctx.song
    if signature is not None:
        numerator, denominator = parse_signature(signature)
    else:
        numerator, denominator = signature_of(song)
    bar_len = grid(numerator, denominator)[0]
    tempo = float(compat.safe_getattr(song, "tempo", 120.0) or 120.0)
    values = []
    for item in (beats if isinstance(beats, (list, tuple)) else
                 ([] if beats is None else [beats])):
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise BridgeError("bad_args", "beats must be numbers, got %r" % (item,))
        if item < 0:
            raise BridgeError("bad_args", "beats must not be negative")
        values.append(float(item))
    for item in (bbs if isinstance(bbs, (list, tuple)) else ([] if bbs is None else [bbs])):
        try:
            values.append(bbs_to_beats(str(item), numerator, denominator, bool(is_length)))
        except ValueError as error:
            raise BridgeError("bad_args", "bbs: %s" % error)
    if len(values) > 1000:
        raise BridgeError("bad_args", "at most 1000 values per call")
    results = []
    for value in values:
        results.append({"beats": rnd(value),
                        "bbs": beats_to_bbs(value, numerator, denominator, bool(is_length)),
                        "seconds": rnd(value * 60.0 / tempo, 3),
                        "bars": rnd(value / bar_len, 4)})
    return {"signature": "%d/%d" % (numerator, denominator), "tempo": rnd(tempo, 3),
            "beats_per_bar": rnd(bar_len), "results": results}
