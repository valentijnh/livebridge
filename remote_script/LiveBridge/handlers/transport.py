"""Transport, tempo, loop, record flags, quantization, scale, undo — and the
whole-set snapshot (``song.summary`` / ``song.snapshot``).

Times are always **beats** (quarter notes) on the wire.  Wherever a time is
accepted, a string in Live's ``bars.beats.sixteenths`` notation works too
(``"9.1.1"`` = bar 9; 1-based for positions).  Lengths given as strings are
0-based durations (``"4.0.0"`` = four bars, ``"2"`` = two bars).  Every result
that carries a time also carries its ``bbs`` form, computed with the song's
global time signature.

Only commands that change the Live *document* are ``mutating`` (one undo step
each).  Pure transport actions (play, stop, locate, undo/redo themselves) are
not, so they never add entries to Live's undo history.

Verified on Live 12.4.5 (docs/live_test/T1-global.md): Live applies some song
properties on its *next* main-thread tick — ``is_playing`` (start/stop/continue),
``current_song_time`` (also ``jump_by`` and cue jumps), ``loop``, ``punch_in``,
``punch_out``, ``session_automation_record``, ``record_mode`` and
``back_to_arranger`` still read back their old value inside the command that
set them.  Results therefore report the *requested* state for those (see
:data:`DEFERRED_PROPS`).  ``start_time``, tempo, signature, metronome,
overdub, quantization, groove and scale settings apply immediately.  Positions
(``current_song_time``, ``start_time``, the loop brace) must stay inside
``song.song_length`` (the arrangement length plus Live's padding), otherwise
Live raises "Cannot set ... behind the Songlength".  LiveBridge gets past that
wall by stretching the loop brace in 32-beat steps (each write moves
``song_length`` 32 beats further at once — verified on 12.4.5), writing the
position and putting the brace back (:func:`extend_song` / :func:`restore_loop`):
a cue point, the playhead or the loop brace behind the end then keeps the song
that long by itself (the start marker may stay behind it), so a song structure
can be laid out before any clip exists.
"""

from .. import compat
from .. import resolve
from .. import serialize
from ..registry import BridgeError, command
from . import view as view_handlers

_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_FLAT_NAMES = {"DB": 1, "EB": 3, "FB": 4, "GB": 6, "AB": 8, "BB": 10, "CB": 11,
               "E#": 5, "B#": 0}
_CAPTURE_DESTINATIONS = {"auto": 0, "session": 1, "arrangement": 2}
_EPS = 1e-9

#: Song properties Live 12.4.5 applies on its next tick (reading them back in the
#: same command returns the previous value) — verified on the real Live.
DEFERRED_PROPS = ("is_playing", "current_song_time", "loop", "punch_in", "punch_out",
                  "session_automation_record", "record_mode", "session_record", "overdub",
                  "back_to_arranger")

#: ``song.groove_amount`` accepts 0..1.3125 in Live 12.4.5 (131.25 %; larger values clamp).
GROOVE_MAX = 1.3125


# --------------------------------------------------------------------------
# small helpers (shared inside this module)
# --------------------------------------------------------------------------

#: Call into Live with protocol errors — shared implementation (``resolve.live_call``).
_live = resolve.live_call


def _assign(obj, prop, value, what=None):
    """``obj.prop = value`` with protocol errors."""
    label = what or prop
    try:
        setattr(obj, prop, value)
    except AttributeError:
        raise BridgeError("unsupported", "%s cannot be set in this Live version" % label)
    except Exception as error:
        raise BridgeError("invalid_state", "%s: Live refused %r (%s)" % (label, value, error))


def _bool(value, name):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in ("true", "on", "yes", "1",
                                                            "false", "off", "no", "0"):
        return value.strip().lower() in ("true", "on", "yes", "1")
    raise BridgeError("bad_args", "%s must be true or false, got %r" % (name, value))


def _number(value, name, low=None, high=None):
    if isinstance(value, bool) or value is None:
        raise BridgeError("bad_args", "%s must be a number, got %r" % (name, value))
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise BridgeError("bad_args", "%s must be a number, got %r" % (name, value))
    if number != number or number in (float("inf"), float("-inf")):
        raise BridgeError("bad_args", "%s must be a finite number" % name)
    if (low is not None and number < low) or (high is not None and number > high):
        if high is None:
            raise BridgeError("bad_args", "%s must be >= %s, got %s"
                              % (name, _fmt(low), _fmt(number)))
        if low is None:
            raise BridgeError("bad_args", "%s must be <= %s, got %s"
                              % (name, _fmt(high), _fmt(number)))
        raise BridgeError("bad_args", "%s must be within %s..%s, got %s"
                          % (name, _fmt(low), _fmt(high), _fmt(number)))
    return number


def _int(value, name, low=None, high=None):
    number = _number(value, name, low, high)
    if abs(number - round(number)) > _EPS:
        raise BridgeError("bad_args", "%s must be a whole number, got %r" % (name, value))
    return int(round(number))


def _fmt(number):
    if number is None:
        return "?"
    if float(number) == int(number):
        return str(int(number))
    return str(round(float(number), 6))


def _round(value, digits=4):
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


_detail = resolve.check_detail


# --------------------------------------------------------------------------
# time: beats <-> bars.beats.sixteenths
# --------------------------------------------------------------------------

def _signature(song):
    numerator = compat.safe_getattr(song, "signature_numerator", 4) or 4
    denominator = compat.safe_getattr(song, "signature_denominator", 4) or 4
    try:
        return int(numerator), int(denominator)
    except (TypeError, ValueError):
        return 4, 4


def _grid(song):
    """``(bar_length, beat_length)`` in quarter-note beats."""
    numerator, denominator = _signature(song)
    beat = 4.0 / denominator
    return numerator * beat, beat


def beats_to_bbs(song, beats, is_length=False):
    """``9.0`` -> ``"3.1.1"`` in 4/4 (1-based); lengths are 0-based (``"2.0.0"``).

    Uses the shared implementation in ``LiveBridge/resolve.py`` with the
    song's time signature.
    """
    numerator, denominator = _signature(song)
    return resolve.beats_to_bbs(beats, numerator, denominator, is_length)


def parse_time(song, value, name, is_length=False):
    """A time argument -> beats (float).

    Numbers are beats.  Strings are ``bars[.beats[.sixteenths]]`` — 1-based
    positions (``"9.1.1"``) or, with ``is_length``, 0-based durations
    (``"4.0.0"`` = four bars).  Shared implementation: ``resolve.parse_time``.
    """
    return resolve.parse_time(song, value, name, is_length)


def _time(song, beats, is_length=False):
    return {"beats": _round(beats), "bbs": beats_to_bbs(song, beats, is_length)}


def song_length(song):
    """``song.song_length`` as a float (``None`` when unavailable)."""
    value = compat.safe_getattr(song, "song_length")
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


#: Live pads ``song_length`` by this many beats behind the material / loop brace (12.4.5).
SONG_LENGTH_PADDING = 32.0
#: Most loop-brace steps :func:`extend_song` takes (each adds :data:`SONG_LENGTH_PADDING`
#: beats; one write costs ~10 ms on Live 12.4.5, so this caps the main-thread time at ~1.3 s
#: and the reach at 4096 beats — 1024 bars of 4/4 — behind the current end).
MAX_EXTEND_STEPS = 128


def extend_song(song, beats, name="time"):
    """Make ``beats`` reachable: grow ``song.song_length`` past it.

    Live 12.4.5 refuses a playhead, start marker, cue position or loop brace
    behind ``song_length`` (the end of the arrangement material, cue points,
    playhead or loop brace plus ~32 beats).  A ``loop_length`` write may
    reach up to ``song_length`` and moves ``song_length`` 32 beats further at
    once (verified), so the brace is stretched in steps until ``beats`` fits.
    ``loop_start`` and the loop on/off switch are not touched.  Once
    something real sits behind the old end (a cue, the playhead, the final
    loop brace) Live keeps the song that long, so the caller puts the brace
    back with :func:`restore_loop` when it is done (verified on 12.4.5; a
    start marker alone does not hold the length, but Live keeps it there).

    Returns:
        ``None`` when ``beats`` was already inside the song, else the original
        ``(loop_start, loop_length)`` for :func:`restore_loop`.

    Raises:
        BridgeError: ``bad_args`` when ``beats`` lies more than
        :data:`MAX_EXTEND_STEPS` x 32 beats behind the end; ``invalid_state``
        when Live refuses a step (the brace is put back first).
    """
    limit = check_reachable(song, beats, name)
    if limit is None or beats <= limit + 1e-6:
        return None
    get = compat.safe_getattr
    start = float(get(song, "loop_start", 0.0) or 0.0)
    saved = (start, float(get(song, "loop_length", 0.0) or 0.0))
    for _ in range(MAX_EXTEND_STEPS):
        try:
            song.loop_length = limit - start
        except Exception as error:
            restore_loop(song, saved)
            raise BridgeError("invalid_state", "Live refused to stretch the loop brace to "
                              "extend the song for %s %s: %s" % (name, _fmt(beats), error))
        grown = song_length(song)
        if grown is None or grown <= limit + 1e-6:
            restore_loop(song, saved)
            raise BridgeError("invalid_state", "Live did not extend the song length past %s "
                              "beats for %s %s" % (_fmt(limit), name, _fmt(beats)))
        limit = grown
        if beats <= limit + 1e-6:
            return saved
    restore_loop(song, saved)
    raise BridgeError("bad_args", "%s %s is too far after the end of the song" % (name,
                                                                                  _fmt(beats)))


def check_reachable(song, beats, name):
    """Raise ``bad_args`` when :func:`extend_song` could not reach ``beats``
    (more than :data:`MAX_EXTEND_STEPS` x 32 beats behind the end); writes
    nothing.  Returns the current ``song_length`` (``None`` when unknown)."""
    limit = song_length(song)
    if limit is not None and beats > limit + MAX_EXTEND_STEPS * SONG_LENGTH_PADDING + 1e-6:
        raise BridgeError("bad_args", "%s %s (%s) is %s beats after the end of the song "
                          "(song_length = %s beats, %s): LiveBridge extends the song by at most "
                          "%d beats per command"
                          % (name, _fmt(beats), beats_to_bbs(song, beats), _fmt(beats - limit),
                             _fmt(limit), beats_to_bbs(song, limit),
                             int(MAX_EXTEND_STEPS * SONG_LENGTH_PADDING)))
    return limit


def restore_loop(song, saved):
    """Put the loop brace back to ``saved`` = ``(loop_start, loop_length)``.

    ``None`` does nothing.  Walks the brace back with :func:`move_brace`
    (shrinking first always fits).  Returns ``True`` when the brace is back.
    """
    if not saved:
        return True
    try:
        move_brace(song, float(saved[0]), float(saved[1]))
    except Exception:
        return False
    return True


def check_inside_song(song, beats, name):
    """Raise ``bad_args`` for a time after ``song.song_length``.

    Live 12.4.5 refuses ``current_song_time`` / ``start_time`` / loop brace
    values behind the song length ("Cannot set the Songtime behind the
    Songlength"); the song length is the end of the arrangement material plus
    Live's padding and grows when clips or the loop brace reach further.
    """
    limit = song_length(song)
    if limit is not None and beats > limit + 1e-6:
        raise BridgeError("bad_args", "%s %s (%s) is after the end of the song: Live only "
                          "accepts times up to song_length = %s beats (%s); it grows when "
                          "arrangement clips reach further"
                          % (name, _fmt(beats), beats_to_bbs(song, beats), _fmt(limit),
                             beats_to_bbs(song, limit)))
    return beats


def apply_loop(song, start=None, length=None, enabled=None, what="loop"):
    """Move / resize / switch the arrangement loop brace the way Live accepts it.

    Live checks ``loop_start + loop_length <= song_length`` on *each* write,
    keeps the length when the start moves, and ``song_length`` itself follows
    the brace (end + 32 beats) when nothing else holds the song that long —
    so the brace is walked to its target (:func:`move_brace`): shrink first,
    move earlier in one write, move later / grow in steps of at most the room
    Live has (32 beats when the brace alone defines the song length).  A
    brace behind the end of the song therefore extends the song (up to
    :data:`MAX_EXTEND_STEPS` x 32 beats behind it).  Live raises lengths below
    one beat to 1.0.  ``loop`` is applied on Live's next tick, so the returned
    ``on`` is the requested value.

    Returns:
        :func:`loop_state` after the change (+ ``song_extended: true`` when
        the brace now ends behind the old song length).
    """
    get = compat.safe_getattr
    old_start = float(get(song, "loop_start", 0.0) or 0.0)
    old_length = float(get(song, "loop_length", 0.0) or 0.0)
    new_start = old_start if start is None else float(start)
    new_length = old_length if length is None else float(length)
    if new_start < 0:
        raise BridgeError("bad_args", "the %s start must not be negative" % what)
    if new_length <= 0:
        raise BridgeError("bad_args", "the %s length must be > 0 (got %s beats)"
                          % (what, _fmt(new_length)))
    limit = song_length(song)
    extended = limit is not None and new_start + new_length > limit + 1e-6
    if extended and new_start + new_length > limit + MAX_EXTEND_STEPS * SONG_LENGTH_PADDING:
        raise BridgeError("bad_args", "the %s %s..%s (%s..%s) ends %s beats after the end of "
                          "the song (song_length = %s beats, %s): LiveBridge extends the song "
                          "by at most %d beats per command"
                          % (what, _fmt(new_start), _fmt(new_start + new_length),
                             beats_to_bbs(song, new_start),
                             beats_to_bbs(song, new_start + new_length),
                             _fmt(new_start + new_length - limit), _fmt(limit),
                             beats_to_bbs(song, limit),
                             int(MAX_EXTEND_STEPS * SONG_LENGTH_PADDING)))
    try:
        move_brace(song, new_start, new_length)
    except Exception as error:
        try:
            move_brace(song, old_start, old_length)
            undone = True
        except Exception:
            undone = False
        if isinstance(error, BridgeError):
            raise
        raise BridgeError("invalid_state", "%s: Live refused the brace %s..%s (%s)%s"
                          % (what, _fmt(new_start), _fmt(new_start + new_length), error,
                             "" if undone else " — the brace may be partly changed"))
    if enabled is not None:
        _assign(song, "loop", bool(enabled), "loop")
    state = loop_state(song, on=enabled)
    if extended:
        state["song_extended"] = True
    return state


def move_brace(song, start, length):
    """Walk the loop brace to ``start``/``length`` with writes Live accepts.

    Each iteration makes one write: shrink the length first (always fits),
    move the start earlier (the end moves earlier too), move it later by as
    much as ``song_length`` allows, then grow the length by as much as it
    allows — Live moves ``song_length`` along after every write.  Raises the
    exception of a refused write, or ``BridgeError`` when it takes more than
    ``2 * MAX_EXTEND_STEPS`` writes.
    """
    get = compat.safe_getattr
    minimum = 1.0            # Live raises shorter loop lengths to one beat
    length = max(float(length), minimum)
    start = float(start)
    for _ in range(2 * MAX_EXTEND_STEPS + 4):
        cur_start = float(get(song, "loop_start", 0.0) or 0.0)
        cur_length = float(get(song, "loop_length", 0.0) or 0.0)
        if abs(cur_start - start) <= _EPS and abs(cur_length - length) <= _EPS:
            return
        limit = song_length(song)
        if limit is None:
            limit = float("inf")
        if cur_length > length + _EPS:
            song.loop_length = length
        elif cur_start > start + _EPS:
            song.loop_start = start
        elif cur_start < start - _EPS:
            target = min(start, limit - cur_length)
            if target <= cur_start + _EPS:
                raise BridgeError("invalid_state", "the loop brace cannot move past %s beats "
                                  "(song_length = %s)" % (_fmt(cur_start), _fmt(limit)))
            song.loop_start = target
        else:
            target = min(length, limit - cur_start)
            if target <= cur_length + _EPS:
                raise BridgeError("invalid_state", "the loop brace cannot grow past %s beats "
                                  "(song_length = %s)" % (_fmt(cur_start + cur_length),
                                                          _fmt(limit)))
            song.loop_length = target
    raise BridgeError("bad_args", "the loop brace %s..%s is too far from the current one"
                      % (_fmt(start), _fmt(start + length)))


# --------------------------------------------------------------------------
# enum-ish arguments
# --------------------------------------------------------------------------

def _norm(text):
    return str(text).strip().lower().replace(" ", "").replace("_", "")


def parse_choice(value, table, live_enum_path, name, aliases=None):
    """An int, a friendly label from ``table`` or a Live enum member name -> int."""
    if isinstance(value, bool) or value is None:
        raise BridgeError("bad_args", "%s: expected one of %s" % (name, _labels(table)))
    if isinstance(value, (int, float)):
        number = _int(value, name)
        if number in table:
            return number
        raise BridgeError("bad_args", "%s: %r is out of range (%d..%d: %s)"
                          % (name, value, min(table), max(table), _labels(table)))
    if isinstance(value, str):
        wanted = _norm(value)
        for number, label in table.items():
            if _norm(label) == wanted:
                return number
        for alias, number in (aliases or {}).items():
            if _norm(alias) == wanted:
                return number
        if wanted.lstrip("-").isdigit():
            return parse_choice(int(wanted), table, live_enum_path, name, aliases)
        enum_class = compat.live_enum(live_enum_path)
        names = compat.safe_getattr(enum_class, "names")
        if isinstance(names, dict):
            for member_name, member in names.items():
                if _norm(member_name) == wanted:
                    return int(member)
    raise BridgeError("bad_args", "%s: %r is not valid — use one of %s"
                      % (name, value, _labels(table)))


def _labels(table):
    return ", ".join(repr(label) for _number, label in sorted(table.items()))


_QUANT_ALIASES = {"off": 0, "no": 0, "bar": 4, "1bars": 4, "2bar": 3, "4bar": 2, "8bar": 1,
                  "half": 5, "quarter": 7, "eighth": 9, "sixteenth": 11,
                  "thirtysecond": 13, "1/2triplet": 6, "1/4triplet": 8,
                  "1/8triplet": 10, "1/16triplet": 12}
_REC_QUANT_ALIASES = {"off": 0, "no": 0, "quarter": 1, "eighth": 2, "sixteenth": 5,
                      "thirtysecond": 8, "1/8triplet": 3, "1/16triplet": 6}


def parse_root_note(value):
    """``0..11`` or a note name (``"C"``, ``"F#"``, ``"Bb"``) -> int."""
    if isinstance(value, str):
        text = value.strip().upper().replace("♯", "#").replace("♭", "B")
        if text.lstrip("-").isdigit():
            return _int(int(text), "root_note", 0, 11)
        if text in _NOTE_NAMES:
            return _NOTE_NAMES.index(text)
        if text in _FLAT_NAMES:
            return _FLAT_NAMES[text]
        raise BridgeError("bad_args", "root_note %r is not a note name (C, C#, Db, ... B) "
                          "or 0..11" % value)
    return _int(value, "root_note", 0, 11)


def available_scales():
    """Live's scale names (``Live.Song.get_all_scales_ordered``) or ``None``."""
    getter = compat.live_enum("Song.get_all_scales_ordered")
    if not callable(getter):
        return None
    try:
        return [str(entry[0]) for entry in getter()]
    except Exception:
        return None


def parse_scale_name(value):
    if not isinstance(value, str) or not value.strip():
        raise BridgeError("bad_args", "scale_name must be a non-empty string")
    names = available_scales()
    if not names:
        return value.strip()
    wanted = _norm(value)
    for name in names:
        if _norm(name) == wanted:
            return name
    raise BridgeError("bad_args", "unknown scale %r — Live's scales: %s"
                      % (value, ", ".join(names)))


# --------------------------------------------------------------------------
# state readers
# --------------------------------------------------------------------------

def loop_state(song, on=None):
    """The loop brace; ``on`` overrides ``song.loop`` (Live applies it next tick)."""
    get = compat.safe_getattr
    start = get(song, "loop_start", 0.0) or 0.0
    length = get(song, "loop_length", 0.0) or 0.0
    return {
        "on": bool(get(song, "loop", False) if on is None else on),
        "start": _round(start), "length": _round(length), "end": _round(start + length),
        "start_bbs": beats_to_bbs(song, start),
        "end_bbs": beats_to_bbs(song, start + length),
        "length_bbs": beats_to_bbs(song, length, is_length=True),
    }


def position_state(song, is_playing=None, position=None):
    """``{is_playing, position, start_time, seconds_at_current_tempo}``.

    ``is_playing`` / ``position`` override what Live reports: after
    ``start_playing()`` / ``stop_playing()`` / a ``current_song_time`` write
    Live still returns the old values until its next tick, so commands that
    changed them pass the state they requested.
    """
    get = compat.safe_getattr
    now = get(song, "current_song_time", 0.0) or 0.0
    if position is not None:
        now = position
    tempo = get(song, "tempo", 120.0) or 120.0
    playing = get(song, "is_playing", False) if is_playing is None else is_playing
    data = {"is_playing": bool(playing),
            "position": _time(song, now),
            "start_time": _time(song, get(song, "start_time", 0.0) or 0.0)}
    try:
        data["seconds_at_current_tempo"] = round(float(now) * 60.0 / float(tempo), 3)
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    return data


def scale_state(song):
    get = compat.safe_getattr
    root = get(song, "root_note")
    data = {"name": get(song, "scale_name"), "root_note": root,
            "mode": get(song, "scale_mode")}
    if isinstance(root, int) and 0 <= root < 12:
        data["root"] = _NOTE_NAMES[root]
    intervals = get(song, "scale_intervals")
    if intervals is not None:
        try:
            data["intervals"] = [int(i) for i in intervals]
        except (TypeError, ValueError):
            pass
    return serialize._prune(data)


def transport_state(song):
    """Everything on Live's transport bar and its neighbours, compactly."""
    get = compat.safe_getattr
    numerator, denominator = _signature(song)
    data = position_state(song)
    data.update({
        "tempo": _round(get(song, "tempo"), 3),
        "signature": "%d/%d" % (numerator, denominator),
        "loop": loop_state(song),
        "metronome": get(song, "metronome"),
        "record_mode": get(song, "record_mode"),
        "session_record": get(song, "session_record"),
        "session_record_status": serialize._enum_name(
            get(song, "session_record_status"), serialize.SESSION_RECORD_STATUS),
        "arrangement_overdub": get(song, "arrangement_overdub"),
        "punch_in": get(song, "punch_in"),
        "punch_out": get(song, "punch_out"),
        "session_automation_record": get(song, "session_automation_record"),
        "back_to_arranger": get(song, "back_to_arranger"),
        "re_enable_automation_enabled": get(song, "re_enable_automation_enabled"),
        "clip_trigger_quantization": serialize._enum_name(
            get(song, "clip_trigger_quantization"), serialize.SONG_QUANTIZATION),
        "midi_recording_quantization": serialize._enum_name(
            get(song, "midi_recording_quantization"), serialize.RECORD_QUANTIZATION),
        "count_in": serialize._enum_name(get(song, "count_in_duration"), serialize.COUNT_IN),
        "is_counting_in": get(song, "is_counting_in"),
        "groove_amount": _round(get(song, "groove_amount")),
        "swing_amount": _round(get(song, "swing_amount")),
        "scale": scale_state(song),
        "can_undo": get(song, "can_undo"),
        "can_redo": get(song, "can_redo"),
        "can_capture_midi": get(song, "can_capture_midi"),
        "song_length": _round(get(song, "song_length")),
        "link": get(song, "is_ableton_link_enabled"),
    })
    for key, value in list(data.items()):
        if isinstance(value, int) and not isinstance(value, bool) and key in (
                "metronome", "record_mode", "session_record", "arrangement_overdub",
                "punch_in", "punch_out", "session_automation_record", "back_to_arranger",
                "is_counting_in", "can_undo", "can_redo", "can_capture_midi", "link",
                "re_enable_automation_enabled"):
            data[key] = bool(value)
    return serialize._prune(data)


# --------------------------------------------------------------------------
# song.summary / song.snapshot
# --------------------------------------------------------------------------

@command("song.summary", doc="Song-level overview: tempo, signature, transport, counts")
def song_summary(ctx, detail="summary"):
    """Song-level overview (no per-track data unless ``detail="full"``).

    Args:
        detail: "minimal" (tempo, signature, counts), "summary" (+ loop, record
            flags, quantization, scale, selection) or "full" (+ every track,
            return, master and scene summary — prefer ``song.snapshot``).

    Returns:
        The song summary dict (``path: "song"``) plus ``position_bbs``.
    """
    detail = _detail(detail)
    data = ctx.summarize(ctx.song, detail)
    if isinstance(data, dict):
        data["position_bbs"] = beats_to_bbs(ctx.song, data.get("current_song_time") or 0.0)
        _drop_kind(data)
    return data


def _drop_kind(data):
    """Remove the serializer's ``kind`` tags (redundant here) — in place."""
    if isinstance(data, dict):
        data.pop("kind", None)
        for value in data.values():
            if isinstance(value, (dict, list)):
                _drop_kind(value)
    elif isinstance(data, list):
        for value in data:
            _drop_kind(value)


_TRACK_FLAGS = ("mute", "solo", "arm", "can_be_armed", "is_grouped", "is_foldable",
                "is_frozen", "has_midi_input", "has_audio_input")


def _compact_device(device_data):
    entry = {"index": device_data.get("index"), "name": device_data.get("name"),
             "class_name": device_data.get("class_name"), "type": device_data.get("type")}
    if device_data.get("is_active") is False:
        entry["active"] = False
    for key in ("is_plugin", "can_have_chains", "can_have_drum_pads", "parameter_count",
                "chain_count", "drum_pad_count", "preset_count", "selected_preset_index",
                "sample", "playback_mode"):
        value = device_data.get(key)
        if value not in (None, False, [], {}):
            entry[key] = value
    return serialize._prune(entry)


def _compact_track(data, include_devices):
    """Drop default/false fields from a track summary (documented in the tool)."""
    data = dict(data)
    data.pop("kind", None)
    data.pop("clip_slot_count", None)
    for key in _TRACK_FLAGS:
        if data.get(key) is False:
            data.pop(key)
    # implied by ``type`` (arm/can_be_armed only matter on MIDI/audio tracks)
    for key in ("has_midi_input", "has_audio_input", "can_be_armed"):
        data.pop(key, None)
    for key in ("playing_slot_index", "fired_slot_index"):
        if data.get(key) == -1:
            data.pop(key)
    if data.get("monitoring") == "auto":
        data.pop("monitoring")
    devices = data.pop("devices", None)
    if include_devices:
        data.pop("device_count", None)
        compact = [_compact_device(d) for d in devices or () if isinstance(d, dict)]
        if compact:
            data["devices"] = compact
    return serialize._prune(data)


def _track_clips(ctx, track, detail):
    get = compat.safe_getattr
    clips = []
    for index, slot in enumerate(get(track, "clip_slots", ()) or ()):
        clip = get(slot, "clip")
        if clip is None:
            continue
        entry = {"slot": index, "name": get(clip, "name"),
                 "length": _round(get(clip, "length"))}
        for key, label in (("is_playing", "playing"), ("is_triggered", "triggered"),
                           ("is_recording", "recording")):
            if get(clip, key, False):
                entry[label] = True
        if detail == "full":
            entry["color_index"] = get(clip, "color_index")
            entry["looping"] = bool(get(clip, "looping", False))
            entry["loop_start"] = _round(get(clip, "loop_start"))
            entry["loop_end"] = _round(get(clip, "loop_end"))
            if get(clip, "muted", False):
                entry["muted"] = True
            if get(clip, "is_audio_clip", False):
                entry["file_path"] = get(clip, "file_path")
        clips.append(serialize._prune(entry))
    return clips


def _arrangement_clips(track, detail):
    get = compat.safe_getattr
    clips = list(get(track, "arrangement_clips", ()) or ())
    if detail != "full":
        return len(clips)
    return [serialize._prune({"index": i, "name": get(c, "name"),
                              "start": _round(get(c, "start_time")),
                              "end": _round(get(c, "end_time"))})
            for i, c in enumerate(clips)]


def _track_entry(ctx, track, detail, include_clips, include_devices):
    summary_detail = "minimal" if detail == "minimal" else "summary"
    data = ctx.summarize(track, summary_detail)
    if not isinstance(data, dict):
        return data
    if detail == "minimal":
        data.pop("kind", None)
        names = [compat.safe_getattr(d, "name")
                 for d in compat.safe_getattr(track, "devices", ()) or ()]
        if include_devices and names:
            data["devices"] = names
    else:
        if detail == "full" and include_devices:
            data["devices"] = [ctx.summarize(d, "summary")
                               for d in compat.safe_getattr(track, "devices", ()) or ()]
        data = _compact_track(data, include_devices)
    if include_clips and compat.safe_getattr(track, "clip_slots", ()):
        clips = _track_clips(ctx, track, detail)
        if clips:
            data["clips"] = clips
        arrangement = _arrangement_clips(track, detail)
        if arrangement:
            data["arrangement_clips"] = arrangement
    return data


def _scene_entry(ctx, index, scene):
    get = compat.safe_getattr
    entry = {"index": index, "name": get(scene, "name")}
    color = get(scene, "color_index")
    if color is not None:
        entry["color_index"] = color
    if get(scene, "tempo_enabled", False):
        entry["tempo"] = _round(get(scene, "tempo"), 3)
    if get(scene, "time_signature_enabled", False):
        entry["signature"] = "%s/%s" % (get(scene, "time_signature_numerator"),
                                        get(scene, "time_signature_denominator"))
    if get(scene, "is_triggered", False):
        entry["triggered"] = True
    return entry


@command("song.snapshot", doc="The whole set in one compact payload (call this first)")
def song_snapshot(ctx, detail="summary", include_clips=True, include_devices=True,
                  include_returns=True, include_scenes=True, offset=0, limit=None):
    """The whole Live set in one call: song, tracks, returns, master, scenes,
    cue points and the current selection.

    Args:
        detail: "minimal" (tracks: index/name/type/path), "summary" (default:
            mixer values, flags, routing, devices, clips) or "full" (+ device
            summaries, clip loop info, arrangement clip list).
        include_clips: list non-empty session clip slots per track (and the
            arrangement clip count, or list at "full").
        include_devices: list each track's devices.
        include_returns: include return tracks and the master track.
        include_scenes: include the scene list.
        offset / limit: page through ``song.tracks`` on very big sets
            (returns/master/scenes are always complete).

    Returns:
        {song:{tempo, signature, is_playing, position, loop, record flags,
        quantization, scale, counts...}, tracks:[...], returns:[...],
        master:{...}, scenes:[...], cue_points:[...], selection:{...},
        paging:{total, offset, count, next_offset?}}

    Gotchas:
        Compact form: boolean flags are only present when true, defaults
        (monitoring "auto", slot index -1) are omitted, and only tracks carry
        a ``path``: device j of a track is ``<track.path>.devices[j]``, the
        clip in slot s is ``<track.path>.clip_slots[s].clip`` and scene i is
        ``song.scenes[i]``.  Volume/pan/send values are Live's raw 0..1 /
        -1..1 parameter values (0.85 volume = 0 dB).
    """
    detail = _detail(detail)
    include_clips = _bool(include_clips, "include_clips")
    include_devices = _bool(include_devices, "include_devices")
    include_returns = _bool(include_returns, "include_returns")
    include_scenes = _bool(include_scenes, "include_scenes")
    offset = _int(offset, "offset", 0)
    if limit is not None:
        limit = _int(limit, "limit", 1)
    song = ctx.song
    get = compat.safe_getattr
    song_data = ctx.summarize(song, "minimal" if detail == "minimal" else "summary")
    if isinstance(song_data, dict):
        for key in ("kind", "path", "selected_track", "selected_scene", "current_song_time",
                    "loop", "loop_start", "loop_length"):
            song_data.pop(key, None)
        song_data["position"] = _time(song, get(song, "current_song_time", 0.0) or 0.0)
        if detail != "minimal":
            song_data["loop"] = loop_state(song)
            song_data["scale"] = scale_state(song)
            for key in ("scale_name", "root_note", "scale_mode"):
                song_data.pop(key, None)
            for key in list(song_data):
                if song_data[key] is False:
                    song_data.pop(key)
    tracks = list(get(song, "tracks", ()) or ())
    window = tracks[offset:offset + limit] if limit is not None else tracks[offset:]
    result = {
        "song": song_data,
        "tracks": [_track_entry(ctx, t, detail, include_clips, include_devices)
                   for t in window],
    }
    if offset or (limit is not None and offset + limit < len(tracks)):
        result["paging"] = resolve.paged("tracks", window, offset, len(tracks))
        del result["paging"]["tracks"]
    if include_returns:
        result["returns"] = [_track_entry(ctx, t, detail, False, include_devices)
                             for t in get(song, "return_tracks", ()) or ()]
        master = get(song, "master_track")
        if master is not None:
            result["master"] = _track_entry(ctx, master, detail, False, include_devices)
    if include_scenes:
        result["scenes"] = [_scene_entry(ctx, i, s)
                            for i, s in enumerate(get(song, "scenes", ()) or ())]
    # Live keeps cue_points in creation order; list them by time (index = time order,
    # like the cues.* commands).
    cues = sorted(get(song, "cue_points", ()) or (), key=lambda c: get(c, "time", 0.0) or 0.0)
    if cues:
        result["cue_points"] = [{"index": i, "name": get(c, "name"),
                                 "time": _round(get(c, "time"))} for i, c in enumerate(cues)]
    result["selection"] = view_handlers.selection_state(ctx)
    return result


# --------------------------------------------------------------------------
# transport
# --------------------------------------------------------------------------

@command("transport.get", doc="Full transport state: play, position, tempo, loop, flags")
def transport_get(ctx, include_scales=False):
    """Read the transport bar and its neighbours in one call.

    Args:
        include_scales: also list every scale name Live accepts for
            ``scale_name``.

    Returns:
        {is_playing, position:{beats,bbs}, start_time:{beats,bbs},
        seconds_at_current_tempo, tempo, signature, loop:{on,start,length,end,
        start_bbs,end_bbs,length_bbs}, metronome, record_mode, session_record,
        session_record_status, arrangement_overdub, punch_in, punch_out,
        session_automation_record, back_to_arranger, clip_trigger_quantization,
        midi_recording_quantization, count_in, groove_amount, swing_amount,
        scale:{name,root_note,root,mode,intervals}, can_undo, can_redo,
        can_capture_midi, song_length, link, scales?}

    Gotchas:
        ``bbs`` uses the song's global signature (signature changes inside the
        arrangement are not taken into account); ``seconds_at_current_tempo``
        ignores tempo automation.
    """
    data = transport_state(ctx.song)
    if _bool(include_scales, "include_scales"):
        data["scales"] = available_scales() or []
    return data


@command("transport.play", doc="Start playback (from the start marker, a position, or continue)")
def transport_play(ctx, position=None, mode="start"):
    """Start the transport.

    Args:
        position: optional start point — beats (number) or "bars.beats.sixteenths"
            (1-based string, e.g. "17.1.1").  Stopped: the start marker moves
            there and playback starts from it.  Playing: the playhead jumps
            there (quantized by Live to the global launch quantization).
        mode: "start" (default: like the space bar, from the start marker),
            "continue" (Shift+Space: from where playback last stopped — see
            ``transport.continue``), "selection" (play the arrangement
            selection).  Ignored when ``position`` is given.

    Returns:
        {is_playing, position:{beats,bbs}, start_time:{...}, seconds_at_current_tempo,
        quantized?}

    Gotchas:
        Live starts the transport on its next tick, so ``is_playing`` /
        ``position`` report the requested state (for "selection" Live's
        current state: it plays nothing when there is no arrangement
        selection).  A position behind the end of the song extends the song
        first (``song_extended: true``; up to 4096 beats behind the end —
        see :func:`extend_song`).
    """
    song = ctx.song
    if mode not in ("start", "continue", "selection"):
        raise BridgeError("bad_args", "mode must be 'start', 'continue' or 'selection'")
    playing = bool(compat.safe_getattr(song, "is_playing", False))
    if position is not None:
        beats = parse_time(song, position, "position")
        saved = extend_song(song, beats, "position")
        try:
            if playing:
                # Live quantizes the jump to the global launch quantization.
                _assign(song, "current_song_time", beats, "current_song_time")
                data = position_state(song, True, beats)
                data["quantized"] = True
            else:
                # current_song_time is applied on Live's next tick and
                # continue_playing() in the same tick would start from the old
                # playhead — start_time is immediate, so move the start marker
                # and start from it.
                _assign(song, "start_time", beats, "start_time")
                _live("start_playing", song.start_playing)
                data = position_state(song, True, beats)
        finally:
            restore_loop(song, saved)
        if saved:
            data["song_extended"] = True
        return data
    if mode == "continue":
        _live("continue_playing", song.continue_playing)
        return position_state(song, True)
    if mode == "selection":
        if not compat.has(song, "play_selection"):
            raise BridgeError("unsupported", "play_selection is not available in this Live")
        _live("play_selection", song.play_selection)
        return position_state(song)
    _live("start_playing", song.start_playing)
    start = compat.safe_getattr(song, "start_time", 0.0) or 0.0
    return position_state(song, True, None if playing else start)


@command("transport.continue", doc="Continue playback from the current playhead")
def transport_continue(ctx):
    """Continue playing from where playback last stopped (Shift+Space in Live).

    Returns:
        {is_playing: true, position, start_time, seconds_at_current_tempo}
        (the requested state — Live starts on its next tick).

    Gotchas:
        Measured on Live 12.4.5: ``continue_playing()`` resumes at the point
        where the transport last stopped and ignores playhead moves made while
        stopped (``transport.set_position``, cue jumps — even several ticks
        earlier), so ``position`` (Live's reported playhead) can differ from
        where playback resumes.  To start somewhere else use
        ``transport.play(position=...)``.
    """
    _live("continue_playing", ctx.song.continue_playing)
    return position_state(ctx.song, True)


@command("transport.stop", doc="Stop playback (optionally also stop all clips)")
def transport_stop(ctx, stop_clips=False, quantized=False):
    """Stop the transport.

    Args:
        stop_clips: also stop every playing session clip (so the next play
            does not resume them).
        quantized: with ``stop_clips``: stop clips at the next launch-quantization
            boundary instead of immediately.

    Returns:
        {is_playing: false, position, start_time, seconds_at_current_tempo}

    Gotchas:
        The playhead stays where playback stopped (Live applies the stop on
        its next tick, so ``position`` may lag by a few milliseconds).
    """
    song = ctx.song
    stop_clips = _bool(stop_clips, "stop_clips")
    quantized = _bool(quantized, "quantized")
    _live("stop_playing", song.stop_playing)
    if stop_clips:
        _live("stop_all_clips", song.stop_all_clips, quantized)
    return position_state(song, False)


@command("transport.toggle", doc="Toggle play/stop like the space bar")
def transport_toggle(ctx):
    """Play when stopped (from the start marker), stop when playing.

    Returns:
        {is_playing, position, start_time, seconds_at_current_tempo} — the
        state after the toggle (requested; Live applies it on its next tick).
    """
    song = ctx.song
    if compat.safe_getattr(song, "is_playing", False):
        _live("stop_playing", song.stop_playing)
        return position_state(song, False)
    _live("start_playing", song.start_playing)
    return position_state(song, True, compat.safe_getattr(song, "start_time", 0.0) or 0.0)


@command("transport.set_position", doc="Move the playhead (absolute or relative)")
def transport_set_position(ctx, position=None, jump_by=None, set_start_marker=None):
    """Locate the playhead.

    Args:
        position: absolute target — beats (number) or "bars.beats.sixteenths"
            (1-based string, "33.1.1" = bar 33).
        jump_by: relative move in beats (negative = backwards, stops at 0);
            exclusive with ``position``.
        set_start_marker: also move the start marker (where "play" starts).
            Default: only while the transport is stopped.

    Returns:
        {is_playing, position:{beats,bbs}, start_time:{beats,bbs},
        seconds_at_current_tempo, quantized?}

    Gotchas:
        Live moves the playhead on its next tick, so ``position`` is the
        target.  While playing Live quantizes the jump to the global launch
        quantization (``quantized: true``).  A target behind the end of the
        song extends the song first (``song_extended: true``: the loop brace
        is stretched and put back, the playhead there then keeps the song
        that long — up to 4096 beats behind the end).  Stopped, Live's
        Continue (``transport.continue``) ignores this move and resumes where
        playback stopped; ``transport.play`` starts from the start marker.
    """
    song = ctx.song
    if (position is None) == (jump_by is None):
        raise BridgeError("bad_args", "pass exactly one of position or jump_by")
    playing = bool(compat.safe_getattr(song, "is_playing", False))
    if set_start_marker is None:
        set_start_marker = not playing
    else:
        set_start_marker = _bool(set_start_marker, "set_start_marker")
    if position is not None:
        beats = parse_time(song, position, "position")
        delta = None
    else:
        delta = _number(jump_by, "jump_by")
        now = compat.safe_getattr(song, "current_song_time", 0.0) or 0.0
        beats = max(0.0, float(now) + delta)
    saved = extend_song(song, beats, "position" if delta is None else "jump_by target")
    try:
        if delta is not None and compat.has(song, "jump_by"):
            _live("jump_by", song.jump_by, delta)
        else:
            _assign(song, "current_song_time", beats, "current_song_time")
        if set_start_marker:
            _assign(song, "start_time", beats, "start_time")
    finally:
        restore_loop(song, saved)
    data = position_state(song, playing, beats)
    if playing:
        data["quantized"] = True
    if saved:
        data["song_extended"] = True
    return data


@command("transport.set_tempo", mutating=True, doc="Set the song tempo in BPM")
def transport_set_tempo(ctx, bpm):
    """Set the global tempo.

    Args:
        bpm: 20..999.

    Returns:
        {"tempo": <after>}

    Gotchas:
        If the tempo is automated in the arrangement, Live's automation wins
        while playing (re-enable automation with the transport's back-to-arranger
        or by clearing the override).
    """
    song = ctx.song
    _assign(song, "tempo", _number(bpm, "bpm", 20.0, 999.0), "tempo")
    return {"tempo": _round(compat.safe_getattr(song, "tempo"), 3)}


@command("transport.tap_tempo", mutating=True, doc="One tap of Live's Tap Tempo button")
def transport_tap_tempo(ctx):
    """Press Tap Tempo once.

    Returns:
        {"tempo": <after>}

    Gotchas:
        Live derives the tempo from the real time between taps, so tempo only
        changes after several calls spaced like the beat (one tap per request;
        the ~100 ms command latency makes this imprecise — prefer
        ``transport.set_tempo`` when you know the BPM).  The reported tempo
        can lag one tap behind (Live recomputes it on its next tick).
    """
    song = ctx.song
    _live("tap_tempo", song.tap_tempo)
    return {"tempo": _round(compat.safe_getattr(song, "tempo"), 3)}


def _parse_signature(signature):
    return resolve.parse_signature(signature, "signature")


def _check_denominator(denominator):
    if denominator not in (1, 2, 4, 8, 16):
        raise BridgeError("bad_args", "signature denominator must be 1, 2, 4, 8 or 16")
    return denominator


@command("transport.set_time_signature", mutating=True, doc="Set the song time signature")
def transport_set_time_signature(ctx, numerator=None, denominator=None, signature=None):
    """Set the global time signature.

    Args:
        numerator: 1..99.
        denominator: 1, 2, 4, 8 or 16.
        signature: alternatively both at once as a string, "6/8".

    Returns:
        {"signature": "6/8"}

    Gotchas:
        When the arrangement contains time-signature changes (Live writes them
        e.g. when scenes with a signature are launched while recording the
        arrangement), ``song.signature_*`` is the signature at the playhead
        and this changes the section under the playhead — verified on 12.4.5.
        There is no LOM API to list or delete those markers.
    """
    song = ctx.song
    if signature is not None:
        numerator, denominator = _parse_signature(signature)
    if numerator is None and denominator is None:
        raise BridgeError("bad_args", "pass numerator and/or denominator (or signature='3/4')")
    if denominator is not None:
        _assign(song, "signature_denominator",
                _check_denominator(_int(denominator, "denominator", 1, 16)),
                "signature_denominator")
    if numerator is not None:
        _assign(song, "signature_numerator", _int(numerator, "numerator", 1, 99),
                "signature_numerator")
    return {"signature": "%d/%d" % _signature(song)}


@command("transport.set_loop", mutating=True, doc="Arrangement loop: on/off, start, length/end, bars")
def transport_set_loop(ctx, enabled=None, start=None, length=None, end=None,
                       start_bar=None, bars=None):
    """Set the arrangement loop brace.

    Args:
        enabled: switch the loop on/off.
        start: loop start — beats or "bars.beats.sixteenths" (1-based).
        length: loop length — beats or a 0-based bar duration string ("4.0.0"
            or "4" = four bars).
        end: loop end (exclusive with ``length``) — beats or "bars.beats.sixteenths".
        start_bar: alternative to ``start``: the 1-based bar number.
        bars: alternative to ``length``: number of bars (may be fractional).

    Returns:
        {on, start, length, end, start_bbs, end_bbs, length_bbs, song_extended?}

    Gotchas:
        Bars use the global time signature.  Omitted values stay as they are.
        A brace that ends behind the song extends the song first (Live
        refuses it otherwise; ``song_extended: true``, up to 4096 beats
        behind the end).  Live raises lengths below 1 beat to 1 beat.  ``on``
        is the requested state (Live switches the loop on its next tick).
    """
    song = ctx.song
    if start is not None and start_bar is not None:
        raise BridgeError("bad_args", "pass start or start_bar, not both")
    if sum(1 for v in (length, end, bars) if v is not None) > 1:
        raise BridgeError("bad_args", "pass only one of length, end or bars")
    bar_len, _beat_len = _grid(song)
    new_start = None
    if start is not None:
        new_start = parse_time(song, start, "start")
    elif start_bar is not None:
        new_start = (_int(start_bar, "start_bar", 1) - 1) * bar_len
    current_start = compat.safe_getattr(song, "loop_start", 0.0) or 0.0
    base = new_start if new_start is not None else float(current_start)
    new_length = None
    if length is not None:
        new_length = parse_time(song, length, "length", is_length=True)
    elif bars is not None:
        new_length = _number(bars, "bars") * bar_len
    elif end is not None:
        new_length = parse_time(song, end, "end") - base
    if new_length is not None and new_length <= 0:
        raise BridgeError("bad_args", "the loop length must be > 0 (got %s beats)"
                          % _fmt(new_length))
    if enabled is None and new_start is None and new_length is None:
        raise BridgeError("bad_args", "nothing to change: pass enabled, start/start_bar "
                          "and/or length/end/bars")
    if enabled is not None:
        enabled = _bool(enabled, "enabled")
    return apply_loop(song, new_start, new_length, enabled)


#: Boolean song properties ``transport.set`` accepts (argument name = property).
_FLAG_ARGS = ("metronome", "record_mode", "session_record", "overdub", "arrangement_overdub",
              "punch_in", "punch_out", "session_automation_record", "back_to_arranger",
              "loop", "is_ableton_link_enabled", "scale_mode")


@command("transport.set", mutating=True, doc="Set several transport/song settings at once")
def transport_set(ctx, tempo=None, signature=None, signature_numerator=None,
                  signature_denominator=None, metronome=None, record_mode=None,
                  session_record=None, overdub=None, arrangement_overdub=None,
                  punch_in=None, punch_out=None, session_automation_record=None,
                  back_to_arranger=None, loop=None, is_ableton_link_enabled=None,
                  clip_trigger_quantization=None, midi_recording_quantization=None,
                  groove_amount=None, swing_amount=None, scale_name=None, root_note=None,
                  scale_mode=None):
    """Change any combination of song-level settings in one undo step.

    Args (all optional; omitted = unchanged):
        tempo: BPM 20..999.
        signature: "3/4" (or signature_numerator 1..99 / signature_denominator 1,2,4,8,16).
        metronome, punch_in, punch_out, loop: booleans.
        record_mode: Arrangement Record button.  True records every armed track
            as soon as the transport runs — and with Live's default "Start
            Playback with Record" preference it starts the transport at once.
        session_record: Session Record button (records into the selected
            scene's slots of every armed track, starting the transport).
        arrangement_overdub: MIDI Arrangement Overdub.
        overdub: Live's legacy overdub hook (in Live 12 it drives session record
            without starting playback) — prefer the two above.
        session_automation_record: Automation Arm.
        back_to_arranger: pass false to press "Back to Arrangement".
        is_ableton_link_enabled: Ableton Link on/off.
        clip_trigger_quantization: global launch quantization — "none", "8 bars",
            "4 bars", "2 bars", "1 bar", "1/2", "1/2T", "1/4", "1/4T", "1/8",
            "1/8T", "1/16", "1/16T", "1/32" (or 0..13 / Live's q_* names).
        midi_recording_quantization: "none", "1/4", "1/8", "1/8T", "1/8+1/8T",
            "1/16", "1/16T", "1/16+1/16T", "1/32" (or 0..8).
        groove_amount: global groove amount 0..1.3125 (Live's 0-131 %).
        swing_amount: 0..1 (swing used when quantizing / adding notes).
        scale_name: one of Live's scales ("Major", "Minor", "Dorian", ...;
            case-insensitive — see transport.get include_scales).
        root_note: 0..11 or a note name ("C", "F#", "Bb").
        scale_mode: Scale Mode highlighting on/off.

    Returns:
        {"changed": {<name>: <value after>, ...}}

    Gotchas:
        Everything is validated before anything is written, so a bad argument
        changes nothing.  Recording flags record every armed track once the
        transport runs.  Live applies loop / punch / record / automation-arm
        switches on its next tick — ``changed`` reports the requested value
        for those (see ``DEFERRED_PROPS``); everything else is read back.
    """
    song = ctx.song
    plan = []
    if signature is not None:
        if signature_numerator is not None or signature_denominator is not None:
            raise BridgeError("bad_args", "pass signature or signature_numerator/denominator")
        signature_numerator, signature_denominator = _parse_signature(signature)
    if tempo is not None:
        plan.append(("tempo", _number(tempo, "tempo", 20.0, 999.0)))
    if signature_denominator is not None:
        plan.append(("signature_denominator", _check_denominator(
            _int(signature_denominator, "signature_denominator", 1, 16))))
    if signature_numerator is not None:
        plan.append(("signature_numerator",
                     _int(signature_numerator, "signature_numerator", 1, 99)))
    flags = {"metronome": metronome, "record_mode": record_mode,
             "session_record": session_record, "overdub": overdub,
             "arrangement_overdub": arrangement_overdub, "punch_in": punch_in,
             "punch_out": punch_out, "session_automation_record": session_automation_record,
             "back_to_arranger": back_to_arranger, "loop": loop,
             "is_ableton_link_enabled": is_ableton_link_enabled, "scale_mode": scale_mode}
    for name in _FLAG_ARGS:
        if flags[name] is not None:
            plan.append((name, _bool(flags[name], name)))
    if clip_trigger_quantization is not None:
        plan.append(("clip_trigger_quantization", parse_choice(
            clip_trigger_quantization, serialize.SONG_QUANTIZATION, "Song.Quantization",
            "clip_trigger_quantization", _QUANT_ALIASES)))
    if midi_recording_quantization is not None:
        plan.append(("midi_recording_quantization", parse_choice(
            midi_recording_quantization, serialize.RECORD_QUANTIZATION,
            "Song.RecordingQuantization", "midi_recording_quantization",
            _REC_QUANT_ALIASES)))
    if groove_amount is not None:
        plan.append(("groove_amount", _number(groove_amount, "groove_amount", 0.0,
                                              GROOVE_MAX)))
    if swing_amount is not None:
        plan.append(("swing_amount", _number(swing_amount, "swing_amount", 0.0, 1.0)))
    if scale_name is not None:
        plan.append(("scale_name", parse_scale_name(scale_name)))
    if root_note is not None:
        plan.append(("root_note", parse_root_note(root_note)))
    if not plan:
        raise BridgeError("bad_args", "nothing to change — pass at least one setting")
    for prop, _value in plan:
        if not compat.has(song, prop):
            raise BridgeError("unsupported", "%s is not available in this Live version" % prop)
    changed = {}
    for prop, value in plan:
        _assign(song, prop, value, prop)
        after = value if prop in DEFERRED_PROPS else compat.safe_getattr(song, prop)
        if prop == "clip_trigger_quantization":
            after = serialize._enum_name(after, serialize.SONG_QUANTIZATION)
        elif prop == "midi_recording_quantization":
            after = serialize._enum_name(after, serialize.RECORD_QUANTIZATION)
        elif prop == "root_note" and isinstance(after, int) and 0 <= after < 12:
            changed["root"] = _NOTE_NAMES[after]
        changed[prop] = serialize.scalar(after)
    return {"changed": changed}


@command("transport.back_to_arranger", doc="Press 'Back to Arrangement'")
def transport_back_to_arranger(ctx):
    """Give playback back to the arrangement after session clips took over.

    Returns:
        {"back_to_arranger": false, "was": bool}

    Gotchas:
        Live applies it on its next tick; session clips on tracks with
        arrangement material stop and the arrangement plays again.
    """
    was = bool(compat.safe_getattr(ctx.song, "back_to_arranger", False))
    _assign(ctx.song, "back_to_arranger", False, "back_to_arranger")
    return {"back_to_arranger": False, "was": was}


def _undo_redo(ctx, steps, redo):
    song = ctx.song
    steps = _int(steps, "steps", 1, 100)
    guard, method = ("can_redo", "redo") if redo else ("can_undo", "undo")
    done = []
    for _ in range(steps):
        if not compat.safe_getattr(song, guard, False):
            break
        name = _live(method, getattr(song, method))
        done.append(str(name) if name is not None else "")
    return {("redone" if redo else "undone"): done, "count": len(done),
            "can_undo": bool(compat.safe_getattr(song, "can_undo", False)),
            "can_redo": bool(compat.safe_getattr(song, "can_redo", False))}


@command("transport.undo", doc="Undo the last N actions")
def transport_undo(ctx, steps=1):
    """Undo like Cmd/Ctrl+Z.

    Args:
        steps: how many undo steps (1..100); stops early when nothing is left.

    Returns:
        {undone:[<action names>], count, can_undo, can_redo}

    Gotchas:
        Every mutating LiveBridge command is exactly one undo step, so
        ``steps`` = number of LiveBridge edits to revert (the user's own edits
        are on the same stack).
    """
    return _undo_redo(ctx, steps, redo=False)


@command("transport.redo", doc="Redo the last N undone actions")
def transport_redo(ctx, steps=1):
    """Redo like Cmd/Ctrl+Shift+Z.

    Args:
        steps: how many steps (1..100); stops early when nothing is left.

    Returns:
        {redone:[...], count, can_undo, can_redo}
    """
    return _undo_redo(ctx, steps, redo=True)


@command("transport.capture_midi", mutating=True, doc="Capture recently played MIDI")
def transport_capture_midi(ctx, destination="auto"):
    """Live's Capture MIDI: turn what was just played on armed/monitored MIDI
    tracks into a clip.

    Args:
        destination: "auto" (Live decides), "session" or "arrangement".

    Returns:
        {"captured": true, "destination": ...}

    Gotchas:
        Fails with ``invalid_state`` when Live has nothing to capture
        (``can_capture_midi`` is false).
    """
    song = ctx.song
    if not isinstance(destination, str) or destination.lower() not in _CAPTURE_DESTINATIONS:
        raise BridgeError("bad_args", "destination must be 'auto', 'session' or 'arrangement'")
    if not compat.has(song, "capture_midi"):
        raise BridgeError("unsupported", "capture_midi is not available in this Live")
    if compat.has(song, "can_capture_midi") and not compat.safe_getattr(
            song, "can_capture_midi", False):
        raise BridgeError("invalid_state", "nothing to capture — play some MIDI on an armed "
                          "or monitored MIDI track first")
    value = _CAPTURE_DESTINATIONS[destination.lower()]
    enum_class = compat.live_enum("Song.CaptureDestination")
    names = compat.safe_getattr(enum_class, "names")
    if isinstance(names, dict) and destination.lower() in names:
        value = int(names[destination.lower()])
    _live("capture_midi", song.capture_midi, value)
    return {"captured": True, "destination": destination.lower()}


@command("transport.stop_all_clips", doc="Stop all session clips (transport keeps running)")
def transport_stop_all_clips(ctx, quantized=True):
    """The Session view's Stop All Clips button.

    Args:
        quantized: true (default) stops at the next global launch-quantization
            boundary; false stops immediately.

    Returns:
        {"stopped": true, "quantized": bool, "is_playing": bool}
    """
    song = ctx.song
    quantized = _bool(quantized, "quantized")
    _live("stop_all_clips", song.stop_all_clips, quantized)
    return {"stopped": True, "quantized": quantized,
            "is_playing": bool(compat.safe_getattr(song, "is_playing", False))}

