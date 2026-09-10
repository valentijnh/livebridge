"""Arrangement view: clips on the timeline, the loop region, the playhead.

* ``arrangement.list`` / ``arrangement.overview`` — what is where.
* ``arrangement.create_midi_clip`` / ``arrangement.create_audio_clip`` —
  ``Track.create_midi_clip(start, length)`` (Live 12.0+) and
  ``Track.create_audio_clip(path, position)``.
* ``arrangement.duplicate_clip`` — ``Track.duplicate_clip_to_arrangement``
  (session clip -> timeline), optionally filling a length or repeating.
* ``arrangement.from_scenes`` — lay scenes out as song sections in one call;
  ``arrangement.copy_range`` — copy a time range of every track elsewhere.
* ``arrangement.resize_clip`` — change a clip's length on the timeline.
* ``arrangement.delete_clip`` / ``arrangement.move_clip`` — a clip's
  ``start_time`` is read-only in the Live API, so *move* = duplicate at the
  new time + delete the original (one undo step).

Verified on Live 12.4.5 (2026-09-10, scratch clips): the copy made by
``duplicate_clip_to_arrangement`` keeps notes and loop settings — also when the
source is an arrangement clip (what ``move_clip`` relies on).  Clip envelopes
are NOT reliably copied: on a MIDI track without devices they travel with the
copy, but with an instrument (Operator) on the track the copy had no envelopes
at all, mixer ones included (Session view focused, checked right away and 2 s
later).  ``duplicate_clip`` / ``move_clip`` therefore report
``envelopes: {source, copied}`` and a note when envelopes were lost.  A looped
arrangement clip's timeline length does not follow its
loop; switching looping off, moving ``loop_end``/``end_marker`` and switching
looping back on sets the timeline length while the loop repeats inside it
(``resize_clip``).  Lengthening stops at the start of the next clip on the
track (Live caps ``end_time`` there instead of cutting that clip).
* ``arrangement.loop``, ``arrangement.position``,
  ``arrangement.back_to_arranger``.

Arrangement clips are addressed by ``track`` + ``index`` (0-based into the
track's ``arrangement_clips``, which Live keeps in time order), by ``track`` +
``at`` (the clip that covers that time) or by ``clip`` (a path such as
``"song.tracks[0].arrangement_clips[2]"`` or a clip name).

Times are beats unless ``unit="bars"`` (then positions are 1-based bar numbers:
bar 1 = beat 0, lengths are bars in the song's time signature).

Not in the Live API — so not available here: consolidating clips
(Cmd/Ctrl+J), exporting/rendering audio or MIDI, freezing/flattening,
arrangement track automation, time selections and the "insert/delete time"
commands.
"""

from .. import compat
from .. import serialize
from ..registry import BridgeError, command
from .clips import (as_bool, as_int, bar_beat, beats_per_bar, check_audio_file, check_detail,
                    check_unit, clip_region, clip_row, live_call, owner_track, page,
                    paged_result, resolve_clip, rnd, to_beats, write_region)

#: ``Track.create_midi_clip`` / ``create_audio_clip`` reject times outside this range.
MAX_TIME = 1576800.0


def _arrangement_tracks(ctx, track=None):
    if track is not None:
        return [ctx.track(track)]
    return list(compat.safe_getattr(ctx.song, "tracks", ()) or ())


def _clips_of(track_obj):
    return list(compat.safe_getattr(track_obj, "arrangement_clips", ()) or ())


def _clip_span(clip):
    start = compat.safe_getattr(clip, "start_time", 0.0) or 0.0
    end = compat.safe_getattr(clip, "end_time", start) or start
    return float(start), float(end)


def _arrangement_clip(ctx, track=None, index=None, at=None, clip=None, unit="beats"):
    """Resolve an arrangement clip from track+index, track+at or clip."""
    if clip is not None:
        obj = resolve_clip(ctx, track=track, clip=clip)
        if not compat.safe_getattr(obj, "is_arrangement_clip", False):
            raise BridgeError("bad_args", "%s is a session clip — pass an arrangement clip"
                              % ctx.path_of(obj))
        return obj
    if track is None:
        raise BridgeError("bad_args", "address the arrangement clip with track+index, "
                          "track+at, or clip=<path|name>")
    track_obj = ctx.track(track)
    clips = _clips_of(track_obj)
    if index is not None:
        index = as_int(index, "index")
        if -len(clips) <= index < len(clips):
            return clips[index]
        raise BridgeError("not_found", "%s.arrangement_clips[%d]: index out of range (%d clips)"
                          % (ctx.path_of(track_obj) or "track", index, len(clips)))
    if at is not None:
        time = to_beats(ctx, at, unit, "at", position=True)
        for candidate in clips:
            start, end = _clip_span(candidate)
            if start - 1e-9 <= time < end - 1e-9:
                return candidate
        raise BridgeError("not_found", "no arrangement clip on %r at beat %s"
                          % (compat.safe_getattr(track_obj, "name", ""), rnd(time)))
    raise BridgeError("bad_args", "pass index or at (or clip=<path|name>)")


def _row(ctx, clip, detail, index=None):
    row = clip_row(ctx, clip, detail)
    start, end = _clip_span(clip)
    row["start_time"] = rnd(start)
    row["end_time"] = rnd(end)
    row["start_bar"] = bar_beat(ctx, start)
    if index is not None:
        row["index"] = index
    if compat.safe_getattr(clip, "muted", False):
        row["muted"] = True
    return row


def _require(track_obj, method, what):
    if not compat.has(track_obj, method):
        raise BridgeError("unsupported", "Track.%s is not available in this Live version "
                          "(%s needs Live 12)" % (method, what))


@command("arrangement.list", doc="List arrangement clips (start/end/name) per track")
def arrangement_list(ctx, track=None, start=None, end=None, unit="beats", detail="minimal",
                     include_take_lanes=False, offset=0, limit=200):
    """List clips on the arrangement timeline.

    Args:
        track: one track (index/name/path); omit for every track.
        start, end: only clips overlapping [start, end) (``unit``).
        unit: "beats" (default) or "bars" (1-based bar numbers).
        detail: "minimal" (default), "summary" or "full" clip summaries.
        include_take_lanes: also list clips on Live 12 take lanes (``take_lane``
            name on each row).
        offset, limit: paging (limit max 2000).

    Returns:
        {"total", "offset", "count", "clips": [{path, name, is_midi, length, track, index,
         start_time, end_time, start_bar "17.1.1", muted?, take_lane?}], "next_offset"?}
        Take-lane clips have no ``index`` and a path like
        ``song.tracks[0].take_lanes[1].arrangement_clips[0]`` — pass it as
        ``clip`` to clips.* / notes.* commands.
    """
    check_detail(detail)
    check_unit(unit)
    window_start = to_beats(ctx, start, unit, "start", position=True)
    window_end = to_beats(ctx, end, unit, "end", position=True)
    include_take_lanes = as_bool(include_take_lanes, "include_take_lanes")
    found = []
    for track_obj in _arrangement_tracks(ctx, track):
        for index, clip in enumerate(_clips_of(track_obj)):
            found.append((clip, index, None))
        if include_take_lanes:
            track_path = ctx.path_of(track_obj) or "track"
            for l_index, lane in enumerate(compat.safe_getattr(track_obj, "take_lanes", ())
                                           or ()):
                for c_index, clip in enumerate(compat.safe_getattr(lane, "arrangement_clips",
                                                                   ()) or ()):
                    # The shared path_of cannot see into take lanes on real Live, so the
                    # row carries the path built here (lom.resolve walks it fine).
                    found.append((clip, None, (compat.safe_getattr(lane, "name"),
                                               "%s.take_lanes[%d].arrangement_clips[%d]"
                                               % (track_path, l_index, c_index))))
    if window_start is not None or window_end is not None:
        low = window_start if window_start is not None else float("-inf")
        high = window_end if window_end is not None else float("inf")
        found = [entry for entry in found
                 if _clip_span(entry[0])[1] > low + 1e-9 and _clip_span(entry[0])[0] < high]
    items, offset, limit = page(found, offset, limit)
    rows = []
    for clip, index, lane in items:
        row = _row(ctx, clip, detail, index)
        if lane is not None:
            row["take_lane"] = lane[0]
            if not row.get("path"):
                row["path"] = lane[1]
        rows.append(row)
    return paged_result("clips", rows, offset, limit, len(found))


@command("arrangement.overview", doc="The whole arrangement at a glance: tracks x time, "
                                     "loop, cues, bar grid")
def arrangement_overview(ctx, start=None, end=None, unit="beats", grid=True, max_columns=64,
                         include_empty=False):
    """Map of the arrangement.

    Args:
        start, end: time window (default: 0 .. the end of the last clip).
        unit: "beats" (default) or "bars".
        grid: add a text lane per track, one character per column —
            ``#`` a clip covers the column, ``.`` empty.
        max_columns: grid width; each column is a whole number of bars.
        include_empty: also list tracks without arrangement clips.

    Returns:
        {"signature", "beats_per_bar", "song_length", "last_event_time",
         "position", "loop": {enabled, start, end}, "cue_points": [[name, time]],
         "window": [start, end], "bars_per_column"?,
         "tracks": [{"index", "name", "type", "clips": [[start, end, name], ...],
                     "lane"?: "##..#"}]}

    Gotchas:
        Session clips are not shown (see clips.list). Consolidate, export and
        freeze are not part of the Live API.
    """
    check_unit(unit)
    grid = as_bool(grid, "grid")
    include_empty = as_bool(include_empty, "include_empty")
    max_columns = as_int(max_columns, "max_columns", 8, 512)
    song = ctx.song
    bar = beats_per_bar(song)
    tracks = _arrangement_tracks(ctx)
    last_end = 0.0
    for track_obj in tracks:
        for clip in _clips_of(track_obj):
            last_end = max(last_end, _clip_span(clip)[1])
    window_start = to_beats(ctx, start, unit, "start", position=True) or 0.0
    window_end = to_beats(ctx, end, unit, "end", position=True)
    if window_end is None:
        window_end = max(last_end, window_start + bar)
    if window_end <= window_start:
        raise BridgeError("bad_args", "end must be after start")
    total_bars = max(1, int(-(-(window_end - window_start) // bar)))
    bars_per_column = max(1, int(-(-total_bars // max_columns)))
    columns = int(-(-total_bars // bars_per_column))
    column_length = bars_per_column * bar
    rows = []
    for index, track_obj in enumerate(tracks):
        spans = []
        for clip in _clips_of(track_obj):
            clip_start, clip_end = _clip_span(clip)
            if clip_end <= window_start + 1e-9 or clip_start >= window_end - 1e-9:
                continue
            spans.append((clip_start, clip_end, compat.safe_getattr(clip, "name", "")))
        if not spans and not include_empty:
            continue
        row = {"index": index, "name": compat.safe_getattr(track_obj, "name"),
               "type": serialize.track_type(track_obj, ctx),
               "clips": [[rnd(s), rnd(e), n] for s, e, n in spans]}
        if grid:
            cells = []
            for column in range(columns):
                cell_start = window_start + column * column_length
                cell_end = cell_start + column_length
                covered = any(s < cell_end - 1e-9 and e > cell_start + 1e-9
                              for s, e, _n in spans)
                cells.append("#" if covered else ".")
            row["lane"] = "".join(cells)
        rows.append(row)
    loop_start = compat.safe_getattr(song, "loop_start", 0.0) or 0.0
    loop_length = compat.safe_getattr(song, "loop_length", 0.0) or 0.0
    result = {
        "signature": "%s/%s" % (compat.safe_getattr(song, "signature_numerator", 4),
                                compat.safe_getattr(song, "signature_denominator", 4)),
        "beats_per_bar": rnd(bar),
        "song_length": rnd(compat.safe_getattr(song, "song_length", last_end)),
        "last_event_time": rnd(compat.safe_getattr(song, "last_event_time", last_end)),
        "position": rnd(compat.safe_getattr(song, "current_song_time", 0.0)),
        "loop": {"enabled": bool(compat.safe_getattr(song, "loop", False)),
                 "start": rnd(loop_start), "end": rnd(loop_start + loop_length)},
        "cue_points": [[compat.safe_getattr(c, "name"), rnd(compat.safe_getattr(c, "time"))]
                       for c in compat.safe_getattr(song, "cue_points", ()) or ()],
        "window": [rnd(window_start), rnd(window_end)],
        "tracks": rows,
    }
    if grid:
        result["bars_per_column"] = bars_per_column
        result["grid_starts_at_bar"] = int(window_start // bar) + 1
    return result


def _check_time(value, name):
    if value < 0 or value > MAX_TIME:
        raise BridgeError("bad_args", "%s must be within 0..%d beats (got %s)"
                          % (name, int(MAX_TIME), rnd(value)))
    return value


@command("arrangement.create_midi_clip", mutating=True,
         doc="Create an empty MIDI clip on the arrangement timeline")
def arrangement_create_midi_clip(ctx, track, start, length=None, unit="beats", name=None,
                                 color_index=None, looping=None):
    """Create a MIDI clip in the arrangement (``Track.create_midi_clip``).

    Args:
        track: a MIDI track (index/name/path).
        start: position — beats, or a 1-based bar number with unit="bars".
        length: beats (or bars with unit="bars"); default one bar.
        name, color_index (0..69), looping: optional clip settings.

    Returns:
        The new clip: {path, name, length, start_time, end_time, start_bar, index, ...}

    Gotchas:
        Needs Live 12.0+. Fails (invalid_state) on audio/frozen tracks or while
        the track records. Overlapping clips are cut by Live like in the UI.
        Write notes with notes.add using the returned ``path`` as ``clip``.
        A looped clip keeps this length on the timeline even when its loop
        is changed later (the loop then repeats inside it); to change the
        timeline length use clips.set looping=false end_marker=<beats>, then
        looping=true (verified on Live 12.4.5).
    """
    check_unit(unit)
    track_obj = ctx.track(track)
    _require(track_obj, "create_midi_clip", "creating arrangement MIDI clips")
    if not compat.safe_getattr(track_obj, "has_midi_input", False):
        raise BridgeError("invalid_state", "%r is not a MIDI track"
                          % compat.safe_getattr(track_obj, "name", ""))
    position = _check_time(to_beats(ctx, start, unit, "start", position=True,
                                    allow_none=False), "start")
    beats = to_beats(ctx, length, unit, "length") if length is not None \
        else beats_per_bar(ctx.song)
    if beats <= 0:
        raise BridgeError("bad_args", "length must be > 0")
    clip = live_call("create_midi_clip", track_obj.create_midi_clip, float(position),
                     float(beats))
    clip = _find_new_clip(track_obj, clip, position)
    _apply_settings(clip, name, color_index, looping)
    return _row(ctx, clip, "summary", _index_in(track_obj, clip))


@command("arrangement.create_audio_clip", mutating=True,
         doc="Place an audio file on the arrangement timeline")
def arrangement_create_audio_clip(ctx, track, file_path, start=0.0, unit="beats", name=None,
                                  color_index=None):
    """Create an audio clip from a file (``Track.create_audio_clip``).

    Args:
        track: an audio track (index/name/path).
        file_path: absolute path of the audio file **on the machine running
            Live** (wav, aif, mp3, flac, ogg, ...). Checked before calling Live.
        start: position — beats, or a 1-based bar number with unit="bars".
        name, color_index: optional clip settings.

    Returns:
        The new clip summary (path, name, start_time, end_time, file_path, warping ...).

    Gotchas:
        Fails with not_found when the file does not exist on the Live machine
        and invalid_state on MIDI/frozen tracks or for files Live cannot read.
    """
    check_unit(unit)
    track_obj = ctx.track(track)
    _require(track_obj, "create_audio_clip", "creating arrangement audio clips")
    if compat.safe_getattr(track_obj, "has_midi_input", False) or \
            not compat.safe_getattr(track_obj, "has_audio_input", False):
        raise BridgeError("invalid_state", "%r is not an audio track"
                          % compat.safe_getattr(track_obj, "name", ""))
    path = check_audio_file(file_path)
    position = _check_time(to_beats(ctx, start, unit, "start", position=True,
                                    allow_none=False), "start")
    clip = live_call("create_audio_clip", track_obj.create_audio_clip, path, float(position))
    clip = _find_new_clip(track_obj, clip, position)
    _apply_settings(clip, name, color_index, None)
    return _row(ctx, clip, "summary", _index_in(track_obj, clip))


def _find_new_clip(track_obj, returned, position):
    """The clip Live returned — or, if a version returns None, the clip at ``position``."""
    if returned is not None:
        return returned
    for clip in _clips_of(track_obj):
        if abs(_clip_span(clip)[0] - position) < 1e-6:
            return clip
    raise BridgeError("internal", "Live created the clip but it could not be found")


def _index_in(track_obj, clip):
    for index, candidate in enumerate(_clips_of(track_obj)):
        if candidate == clip:
            return index
    return None


def _apply_settings(clip, name, color_index, looping):
    if name is not None:
        live_call("clip.name", setattr, clip, "name", str(name))
    if color_index is not None:
        live_call("clip.color_index", setattr, clip, "color_index",
                  as_int(color_index, "color_index", 0, 69))
    if looping is not None:
        live_call("clip.looping", setattr, clip, "looping", as_bool(looping, "looping"))


def _timeline_length(clip):
    start, end = _clip_span(clip)
    return end - start


def _can_resize(clip):
    """Unwarped audio clips measure their markers in seconds — not resizable in beats."""
    if compat.safe_getattr(clip, "is_midi_clip", False):
        return True
    return bool(compat.safe_getattr(clip, "warping", True))


def resize_clip_to(clip, length):
    """Set an arrangement clip's length on the timeline (beats).

    Looped clips keep their loop, which then repeats (or is cut) inside the
    new length: looping off -> move the unlooped end -> looping on (the
    recipe verified on Live 12.4.5).  Unlooped clips move their end.
    Returns the length Live kept (it stops at the next clip on the track).
    """
    if length <= 0:
        raise BridgeError("bad_args", "length must be > 0")
    if not _can_resize(clip):
        raise BridgeError("unsupported", "an unwarped audio clip's markers are in seconds — "
                          "switch warping on (clips.set warping=true) to resize it in beats")
    if compat.safe_getattr(clip, "looping", False):
        live_call("clip.looping", setattr, clip, "looping", False)
        try:
            base = clip_region(clip)[0]
            write_region(clip, None, base + float(length))
        finally:
            live_call("clip.looping", setattr, clip, "looping", True)
    else:
        base = clip_region(clip)[0]
        write_region(clip, None, base + float(length))
    return _timeline_length(clip)


def _clear_starting_in(track_obj, begin, end, keep=()):
    """Delete the arrangement clips of a track that START inside [begin, end)."""
    removed = 0
    for candidate in list(_clips_of(track_obj)):
        start = _clip_span(candidate)[0]
        if begin - 1e-9 <= start < end - 1e-9 and not any(candidate == k for k in keep):
            live_call("delete_clip", track_obj.delete_clip, candidate)
            removed += 1
    return removed


def _place(ctx, destination, source, position, length=None, count=1, replace=True):
    """Copy ``source`` to ``destination`` at ``position``.

    ``length``: fill that many beats — a looped copy is stretched (its loop
    repeats), anything else is repeated back to back with the last copy cut.
    ``count``: that many back-to-back copies (ignored with ``length``).
    Returns ``(new clips, notes)``.
    """
    unit_length = float(compat.safe_getattr(source, "length", 0.0) or 0.0)
    if unit_length <= 0:
        raise BridgeError("invalid_state", "the source clip has no length")
    total = float(length) if length is not None else unit_length * count
    _check_time(position + total, "the end of the copy")
    notes = []
    if replace:
        removed = _clear_starting_in(destination, position, position + total)
        if removed:
            notes.append("replaced %d clip(s) that started in the filled range" % removed)
    placed = []
    looped = bool(compat.safe_getattr(source, "looping", False)) and _can_resize(source)
    if length is not None and looped:
        new_clip = live_call("duplicate_clip_to_arrangement",
                             destination.duplicate_clip_to_arrangement, source,
                             float(position))
        new_clip = _find_new_clip(destination, new_clip, position)
        kept = resize_clip_to(new_clip, total)
        if kept < total - 1e-6:
            notes.append("Live stopped the clip at beat %s (the next clip on the track)"
                         % rnd(position + kept))
        return [new_clip], notes
    cursor, remaining = float(position), total
    while remaining > 1e-6:
        new_clip = live_call("duplicate_clip_to_arrangement",
                             destination.duplicate_clip_to_arrangement, source, float(cursor))
        new_clip = _find_new_clip(destination, new_clip, cursor)
        placed.append(new_clip)
        span = _timeline_length(new_clip) or unit_length
        if remaining < span - 1e-6:
            if _can_resize(new_clip):
                resize_clip_to(new_clip, remaining)
            else:
                notes.append("the last copy is longer than the range (unwarped audio cannot "
                             "be cut in beats)")
            break
        cursor += span
        remaining -= span
        if len(placed) >= 512:
            raise BridgeError("bad_args", "more than 512 copies — use a longer clip or a "
                              "shorter range")
    return placed, notes


#: The note when Live dropped the clip envelopes of an arrangement copy.
ENVELOPES_LOST = ("Live did not copy the clip envelopes into the arrangement - redraw them "
                  "or record the automation (automation.record)")


def envelope_report(source, copy):
    """``({"source": bool, "copied": bool}, note-or-None)`` for a clip copy.

    ``(None, None)`` when this Live has no ``Clip.has_envelopes``.  Live 12.4.5
    copies clip envelopes on a track without devices but dropped them all with
    an instrument on the track, so every copy is checked.
    """
    get = compat.safe_getattr
    if copy is None or not compat.has(source, "has_envelopes"):
        return None, None
    had = bool(get(source, "has_envelopes", False))
    copied = bool(get(copy, "has_envelopes", False))
    return {"source": had, "copied": copied}, (ENVELOPES_LOST if had and not copied else None)


def _source_and_destination(ctx, track, slot, clip, target_track):
    source = resolve_clip(ctx, track, slot, clip)
    destination = ctx.track(target_track) if target_track is not None else owner_track(source)
    if destination is None:
        raise BridgeError("invalid_state", "cannot find the source clip's track — pass "
                          "target_track")
    _require(destination, "duplicate_clip_to_arrangement", "duplicating into the arrangement")
    return source, destination


@command("arrangement.duplicate_clip", mutating=True,
         doc="Copy a session clip (or any clip) onto the arrangement timeline — once, "
             "repeated, or stretched to fill a length")
def arrangement_duplicate_clip(ctx, time, track=None, slot=None, clip=None, target_track=None,
                               unit="beats", length=None, count=None, replace=True):
    """Duplicate a clip into the arrangement (``Track.duplicate_clip_to_arrangement``).

    Args:
        time: destination — beats, or a 1-based bar number with unit="bars".
        track, slot / clip: the source clip (session address, path or name).
        target_track: destination track (default: the source clip's track).
        length: fill this many beats (bars with unit="bars", or "8.0.0"):
            a looping clip becomes ONE arrangement clip whose loop repeats over
            the whole length; a non-looping clip is repeated back to back and
            the last copy is cut at the end.
        count: place this many back-to-back copies instead (1..512).
        replace: with length/count — first delete clips on the destination
            track that start inside the filled range (default true; Live itself
            cuts clips the copy overlaps).

    Returns:
        One copy (no length/count): the new arrangement clip summary.
        With length/count: {"clips": [summaries], "count", "range": [start,
        end], "start_bar", "note"?}.

    Gotchas:
        MIDI clips need a MIDI target track and audio clips an audio track.
        Notes and loop settings travel with the copy.  Clip envelopes are not
        reliably copied (12.4.5: copied on a track without devices, lost with
        an instrument on the track) — check ``envelopes`` ``{source, copied}``
        in the result; when they were lost, redraw them on the copy's track
        (``automation.record``) instead.  A clip cannot be stretched over a
        clip that starts later on the same track — ``note`` says where Live
        stopped it.
    """
    check_unit(unit)
    source, destination = _source_and_destination(ctx, track, slot, clip, target_track)
    position = _check_time(to_beats(ctx, time, unit, "time", position=True, allow_none=False),
                           "time")
    if length is None and count is None:
        new_clip = live_call("duplicate_clip_to_arrangement",
                             destination.duplicate_clip_to_arrangement, source,
                             float(position))
        new_clip = _find_new_clip(destination, new_clip, position)
        row = _row(ctx, new_clip, "summary", _index_in(destination, new_clip))
        envelopes, lost = envelope_report(source, new_clip)
        if envelopes is not None:
            row["envelopes"] = envelopes
        if lost:
            row["note"] = lost
        return row
    if length is not None and count is not None:
        raise BridgeError("bad_args", "pass length or count, not both")
    beats = None
    if length is not None:
        beats = to_beats(ctx, length, unit, "length")
        if beats <= 0:
            raise BridgeError("bad_args", "length must be > 0")
    copies = as_int(count, "count", 1, 512) if count is not None else 1
    placed, notes = _place(ctx, destination, source, position, beats, copies,
                           as_bool(replace, "replace"))
    end = max(_clip_span(c)[1] for c in placed)
    result = {"clips": [_row(ctx, c, "minimal", _index_in(destination, c)) for c in placed],
              "count": len(placed), "range": [rnd(position), rnd(end)],
              "start_bar": bar_beat(ctx, position)}
    envelopes, lost = envelope_report(source, placed[0] if placed else None)
    if envelopes is not None:
        result["envelopes"] = envelopes
    if lost:
        notes.append(lost)
    if notes:
        result["note"] = "; ".join(notes)
    return result


@command("arrangement.resize_clip", mutating=True,
         doc="Change an arrangement clip's length on the timeline (a looped clip repeats "
             "its loop)")
def arrangement_resize_clip(ctx, track=None, index=None, at=None, clip=None, length=None,
                            end=None, unit="beats"):
    """Lengthen or shorten an arrangement clip where it sits.

    Args:
        track + index / track + at / clip: the clip (see arrangement.delete_clip).
        length: new timeline length — beats, bars with unit="bars", or "4.0.0".
        end: or the new end position on the timeline (beats / 1-based bar).

    Returns:
        {"clip": {summary}, "length": beats kept, "requested": beats, "capped"?:
         true, "note"?}

    Gotchas:
        A looped clip keeps its loop, which repeats inside the new length (a
        2-bar loop resized to 8 bars plays 4 times) or is cut. A non-looped
        clip moves its end marker (MIDI: empty space or cut notes; audio: up to
        the end of the file). Live stops a lengthened clip at the start of the
        next clip on the track (``capped``) — delete or move that one first.
        Unwarped audio clips cannot be resized in beats.
    """
    check_unit(unit)
    obj = _arrangement_clip(ctx, track, index, at, clip, unit)
    if (length is None) == (end is None):
        raise BridgeError("bad_args", "pass length or end")
    start = _clip_span(obj)[0]
    if length is not None:
        wanted = to_beats(ctx, length, unit, "length")
    else:
        wanted = to_beats(ctx, end, unit, "end", position=True) - start
    if wanted <= 0:
        raise BridgeError("bad_args", "the new end must be after the clip start (beat %s)"
                          % rnd(start))
    _check_time(start + wanted, "the new end")
    kept = resize_clip_to(obj, wanted)
    result = {"clip": _row(ctx, obj, "summary", _index_in(owner_track(obj), obj)),
              "length": rnd(kept), "requested": rnd(wanted)}
    if kept < wanted - 1e-6:
        result["capped"] = True
        result["note"] = ("Live stopped the clip at beat %s — the next clip on the track "
                          "starts there" % rnd(start + kept))
    return result


def _section_items(ctx, sections, unit):
    if not isinstance(sections, (list, tuple)) or not sections:
        raise BridgeError("bad_args", "sections must be a non-empty list of scenes or "
                          "{scene, length?}")
    if len(sections) > 256:
        raise BridgeError("bad_args", "at most 256 sections")
    items = []
    for index, entry in enumerate(sections):
        if isinstance(entry, dict):
            unknown = set(entry) - {"scene", "length", "bars", "repeat"}
            if unknown:
                raise BridgeError("bad_args", "sections[%d]: unknown key(s) %s"
                                  % (index, ", ".join(sorted(unknown))))
            if "scene" not in entry:
                raise BridgeError("bad_args", "sections[%d] needs a scene" % index)
            scene_spec = entry["scene"]
            if "length" in entry and "bars" in entry:
                raise BridgeError("bad_args", "sections[%d]: pass length or bars" % index)
            if "bars" in entry:
                beats = to_beats(ctx, entry["bars"], "bars", "sections[%d].bars" % index)
            elif "length" in entry:
                beats = to_beats(ctx, entry["length"], unit, "sections[%d].length" % index)
            else:
                beats = None
            if beats is not None and beats <= 0:
                raise BridgeError("bad_args", "sections[%d]: the length must be > 0" % index)
            repeat = as_int(entry.get("repeat", 1), "sections[%d].repeat" % index, 1, 64)
        else:
            scene_spec, beats, repeat = entry, None, 1
        scene = ctx.scene(scene_spec)
        scene_index = None
        for position, candidate in enumerate(compat.safe_getattr(ctx.song, "scenes", ())
                                             or ()):
            if candidate == scene:
                scene_index = position
                break
        if scene_index is None:
            raise BridgeError("not_found", "sections[%d]: scene not found" % index)
        for _ in range(repeat):
            items.append((scene_index, scene, beats))
    return items


@command("arrangement.from_scenes", mutating=True,
         doc="Build a song: lay scenes out one after another on the timeline (one call for "
             "every track and section)")
def arrangement_from_scenes(ctx, sections, start=None, unit="beats", tracks=None,
                            replace=True):
    """Turn session scenes into arrangement sections.

    For each section every track's clip in that scene is copied to the
    timeline at the section start and filled to the section length (a
    looping clip repeats its loop, see arrangement.duplicate_clip length=).

    Args:
        sections: list of scenes (index or name) or objects {"scene": ...,
            "bars": 8 | "length": <unit>, "repeat": n}; default length = the
            longest clip of the scene (on the tracks used).
            Example: [{"scene": "Intro", "bars": 8}, {"scene": "Verse", "bars": 16},
            {"scene": "Drop", "bars": 16, "repeat": 2}].
        start: where the first section begins (beats or 1-based bar with
            unit="bars", or "17.1.1"; default the very start).
        tracks: limit to these tracks (list of indices/names); default every
            regular track.
        replace: delete clips of those tracks that start inside each section
            before placing (default true — "paste over").

    Returns:
        {"sections": [{"scene", "name", "start", "end", "start_bar", "length",
         "clips": n, "tracks": [names]}], "start", "end", "end_bar",
         "placed": n, "note"?}

    Gotchas:
        Tracks without a clip in a scene stay empty in that section. Scene
        tempo/signature changes are not written to the arrangement. Empty
        scenes need an explicit length (else bad_args).
    """
    check_unit(unit)
    items = _section_items(ctx, sections, unit)
    cursor = 0.0 if start is None else _check_time(
        to_beats(ctx, start, unit, "start", position=True, allow_none=False), "start")
    if tracks is None:
        chosen = [t for t in _arrangement_tracks(ctx)
                  if compat.has(t, "duplicate_clip_to_arrangement")]
    else:
        specs = tracks if isinstance(tracks, (list, tuple)) else [tracks]
        chosen = [ctx.track(spec, include_returns=False, include_master=False)
                  for spec in specs]
    replace = as_bool(replace, "replace")
    first = cursor
    rows, notes, placed_total = [], [], 0
    for scene_index, scene, beats in items:
        sources = []
        for track_obj in chosen:
            slots = compat.safe_getattr(track_obj, "clip_slots", ()) or ()
            if scene_index >= len(slots):
                continue
            source = compat.safe_getattr(slots[scene_index], "clip")
            if source is not None:
                sources.append((track_obj, source))
        if beats is None:
            lengths = [float(compat.safe_getattr(c, "length", 0.0) or 0.0) for _t, c in sources]
            if not lengths or max(lengths) <= 0:
                raise BridgeError("bad_args", "scene %r has no clips on these tracks — give "
                                  "that section a length" % compat.safe_getattr(scene, "name",
                                                                               scene_index))
            beats = max(lengths)
        _check_time(cursor + beats, "the end of the arrangement")
        names = []
        for track_obj, source in sources:
            placed, extra = _place(ctx, track_obj, source, cursor, beats, 1, replace)
            placed_total += len(placed)
            names.append(compat.safe_getattr(track_obj, "name"))
            notes.extend("%s: %s" % (compat.safe_getattr(track_obj, "name"), n) for n in extra
                         if not n.startswith("replaced"))
        rows.append({"scene": scene_index, "name": compat.safe_getattr(scene, "name"),
                     "start": rnd(cursor), "end": rnd(cursor + beats),
                     "start_bar": bar_beat(ctx, cursor), "length": rnd(beats),
                     "clips": len(sources), "tracks": names})
        cursor += beats
    result = {"sections": rows, "start": rnd(first), "end": rnd(cursor),
              "end_bar": bar_beat(ctx, cursor), "placed": placed_total}
    if notes:
        result["note"] = "; ".join(notes[:10])
    return result


@command("arrangement.copy_range", mutating=True,
         doc="Copy a time range of the arrangement (all or some tracks) to another position")
def arrangement_copy_range(ctx, start, end, destination, unit="beats", tracks=None,
                           replace=True):
    """Copy a section of the arrangement ("copy bars 9-16 to bar 33").

    Every clip that starts inside [start, end) is copied to ``destination``
    plus its offset in the range; a clip that runs past ``end`` is cut at
    the range end on the copy.

    Args:
        start, end: the range (beats, 1-based bars with unit="bars", or "9.1.1").
        destination: where the range goes (same units).
        tracks: limit to these tracks (list); default every regular track.
        replace: first delete clips of those tracks that start inside the
            destination range (default true — "paste over").

    Returns:
        {"copied": n, "range": [start, end], "destination": [start, end],
         "tracks": [{"name", "clips": n}], "skipped"?: [{"track", "name",
         "start_time", "reason"}]}

    Gotchas:
        Clips that start BEFORE the range are not copied (listed in
        ``skipped``) — split sections at clip borders. The destination must not
        overlap the source range.
    """
    check_unit(unit)
    begin = to_beats(ctx, start, unit, "start", position=True, allow_none=False)
    finish = to_beats(ctx, end, unit, "end", position=True, allow_none=False)
    target = _check_time(to_beats(ctx, destination, unit, "destination", position=True,
                                  allow_none=False), "destination")
    if finish <= begin:
        raise BridgeError("bad_args", "end must be after start")
    span = finish - begin
    _check_time(target + span, "the end of the copy")
    if target < finish - 1e-9 and target + span > begin + 1e-9:
        raise BridgeError("bad_args", "the destination overlaps the copied range")
    if tracks is None:
        chosen = _arrangement_tracks(ctx)
    else:
        specs = tracks if isinstance(tracks, (list, tuple)) else [tracks]
        chosen = [ctx.track(spec, include_returns=False, include_master=False)
                  for spec in specs]
    plan, skipped = [], []
    for track_obj in chosen:
        for candidate in _clips_of(track_obj):
            clip_start, clip_end = _clip_span(candidate)
            if clip_end <= begin + 1e-9 or clip_start >= finish - 1e-9:
                continue
            if clip_start < begin - 1e-9:
                skipped.append({"track": compat.safe_getattr(track_obj, "name"),
                                "name": compat.safe_getattr(candidate, "name"),
                                "start_time": rnd(clip_start),
                                "reason": "starts before the range"})
                continue
            plan.append((track_obj, candidate, clip_start, clip_end))
    copied, per_track = 0, {}
    if as_bool(replace, "replace"):
        for track_obj in chosen:
            _clear_starting_in(track_obj, target, target + span)
    for track_obj, candidate, clip_start, clip_end in plan:
        position = target + (clip_start - begin)
        new_clip = live_call("duplicate_clip_to_arrangement",
                             track_obj.duplicate_clip_to_arrangement, candidate,
                             float(position))
        new_clip = _find_new_clip(track_obj, new_clip, position)
        if clip_end > finish + 1e-9:
            if _can_resize(new_clip):
                resize_clip_to(new_clip, finish - clip_start)
            else:
                skipped.append({"track": compat.safe_getattr(track_obj, "name"),
                                "name": compat.safe_getattr(candidate, "name"),
                                "start_time": rnd(clip_start),
                                "reason": "copied whole — unwarped audio cannot be cut"})
        copied += 1
        name = compat.safe_getattr(track_obj, "name")
        per_track[name] = per_track.get(name, 0) + 1
    result = {"copied": copied, "range": [rnd(begin), rnd(finish)],
              "destination": [rnd(target), rnd(target + span)],
              "tracks": [{"name": n, "clips": c} for n, c in per_track.items()]}
    if skipped:
        result["skipped"] = skipped
    return result


@command("arrangement.delete_clip", mutating=True, doc="Delete an arrangement clip")
def arrangement_delete_clip(ctx, track=None, index=None, at=None, clip=None, unit="beats"):
    """Delete a clip from the arrangement timeline.

    Args:
        track + index: 0-based position in the track's arrangement clips, or
        track + at: the clip covering that time, or
        clip: path ("song.tracks[0].arrangement_clips[1]") or clip name.
        unit: for ``at``.

    Returns:
        {"deleted": path, "name", "start_time", "end_time"}
    """
    check_unit(unit)
    obj = _arrangement_clip(ctx, track, index, at, clip, unit)
    track_obj = owner_track(obj)
    if track_obj is None:
        raise BridgeError("invalid_state", "cannot find the track that owns this clip")
    path = ctx.path_of(obj)
    name = compat.safe_getattr(obj, "name")
    start, end = _clip_span(obj)
    live_call("delete_clip", track_obj.delete_clip, obj)
    return {"deleted": path, "name": name, "start_time": rnd(start), "end_time": rnd(end)}


@command("arrangement.move_clip", mutating=True,
         doc="Move an arrangement clip to a new time (and optionally another track)")
def arrangement_move_clip(ctx, start, track=None, index=None, at=None, clip=None,
                          target_track=None, unit="beats"):
    """Move an arrangement clip.

    Live's ``clip.start_time`` is read-only, so this duplicates the clip to the
    new position (``duplicate_clip_to_arrangement`` with the arrangement clip
    as source) and deletes the original — one undo step. Notes, loop
    settings, timeline length and name travel with it (verified on Live
    12.4.5); clip envelopes only when Live copies them (see
    arrangement.duplicate_clip) — ``envelopes`` / ``note`` say so.

    Args:
        start: new start — beats, or a 1-based bar number with unit="bars".
        track + index / track + at / clip: the clip to move (see arrangement.delete_clip).
        target_track: move to another track of the same type.

    Returns:
        {"moved_from": beats, "clip": {new clip summary}, "envelopes"?:
         {source, copied}, "note"?: "<envelopes were lost>"}

    Gotchas:
        The clip gets a new path/index. Moving onto other clips cuts them as in
        Live's UI.  When ``envelopes.copied`` is false but ``source`` was true,
        the original's envelopes are gone with it (undo brings them back).
    """
    check_unit(unit)
    obj = _arrangement_clip(ctx, track, index, at, clip, unit)
    source_track = owner_track(obj)
    if source_track is None:
        raise BridgeError("invalid_state", "cannot find the track that owns this clip")
    destination = ctx.track(target_track) if target_track is not None else source_track
    _require(destination, "duplicate_clip_to_arrangement", "moving arrangement clips")
    position = _check_time(to_beats(ctx, start, unit, "start", position=True,
                                    allow_none=False), "start")
    old_start = _clip_span(obj)[0]
    new_clip = live_call("duplicate_clip_to_arrangement",
                         destination.duplicate_clip_to_arrangement, obj, float(position))
    new_clip = _find_new_clip(destination, new_clip, position)
    envelopes, lost = envelope_report(obj, new_clip)
    # Placing the copy over the original may already have trimmed or removed
    # it; delete whatever is left of it (deleted LOM objects compare == None).
    if obj != None and _index_in(source_track, obj) is not None and obj != new_clip:  # noqa: E711
        live_call("delete_clip", source_track.delete_clip, obj)
    result = {"moved_from": rnd(old_start),
              "clip": _row(ctx, new_clip, "summary", _index_in(destination, new_clip))}
    if envelopes is not None:
        result["envelopes"] = envelopes
    if lost:
        result["note"] = lost
    return result


@command("arrangement.loop", mutating=True, doc="Read or set the arrangement loop region")
def arrangement_loop(ctx, enabled=None, start=None, end=None, length=None, unit="beats"):
    """Get/set the arrangement loop (the loop brace).

    Args:
        enabled: switch the loop on/off.
        start: loop start (beats, or 1-based bar with unit="bars").
        end: loop end (same unit) — or give ``length`` instead.
        length: loop length (beats, or bars with unit="bars").

    Returns:
        {"enabled", "start", "end", "length", "start_bar", "end_bar"}

    Gotchas:
        With no arguments it only reads. end must be after start.
    """
    check_unit(unit)
    song = ctx.song
    if end is not None and length is not None:
        raise BridgeError("bad_args", "pass end or length, not both")
    new_start = to_beats(ctx, start, unit, "start", position=True)
    new_end = to_beats(ctx, end, unit, "end", position=True)
    new_length = to_beats(ctx, length, unit, "length")
    if new_start is not None:
        _check_time(new_start, "start")
        live_call("song.loop_start", setattr, song, "loop_start", float(new_start))
    current_start = float(compat.safe_getattr(song, "loop_start", 0.0) or 0.0)
    if new_end is not None:
        new_length = new_end - current_start
        if new_length <= 0:
            raise BridgeError("bad_args", "loop end (%s) must be after the loop start (%s)"
                              % (rnd(new_end), rnd(current_start)))
    if new_length is not None:
        if new_length <= 0:
            raise BridgeError("bad_args", "length must be > 0")
        live_call("song.loop_length", setattr, song, "loop_length", float(new_length))
    if enabled is not None:
        live_call("song.loop", setattr, song, "loop", as_bool(enabled, "enabled"))
    loop_start = float(compat.safe_getattr(song, "loop_start", 0.0) or 0.0)
    loop_length = float(compat.safe_getattr(song, "loop_length", 0.0) or 0.0)
    return {"enabled": bool(compat.safe_getattr(song, "loop", False)),
            "start": rnd(loop_start), "end": rnd(loop_start + loop_length),
            "length": rnd(loop_length), "start_bar": bar_beat(ctx, loop_start),
            "end_bar": bar_beat(ctx, loop_start + loop_length)}


def _position_payload(ctx):
    song = ctx.song
    time = compat.safe_getattr(song, "current_song_time", 0.0) or 0.0
    return {"time": rnd(time), "bar_beat": bar_beat(ctx, time),
            "is_playing": bool(compat.safe_getattr(song, "is_playing", False)),
            "start_marker": rnd(compat.safe_getattr(song, "start_time", 0.0)),
            "song_length": rnd(compat.safe_getattr(song, "song_length", 0.0)),
            "back_to_arranger": bool(compat.safe_getattr(song, "back_to_arranger", False))}


@command("arrangement.position", doc="Read, set or nudge the arrangement playhead")
def arrangement_position(ctx, time=None, jump_by=None, unit="beats"):
    """Get/set the playhead (``song.current_song_time``).

    Args:
        time: absolute position — beats, or a 1-based bar number with unit="bars".
        jump_by: relative jump (beats, or bars with unit="bars"; negative = back).
        unit: "beats" (default) or "bars".

    Returns:
        {"time", "bar_beat": "17.1.1", "is_playing", "start_marker",
         "song_length", "back_to_arranger"}

    Gotchas:
        While stopped, moving the playhead also moves where playback starts.
        ``back_to_arranger`` true means session clips override the arrangement
        (the Back to Arrangement button is lit) — see arrangement.back_to_arranger.
    """
    check_unit(unit)
    song = ctx.song
    if time is not None and jump_by is not None:
        raise BridgeError("bad_args", "pass time or jump_by, not both")
    if time is not None:
        position = _check_time(to_beats(ctx, time, unit, "time", position=True), "time")
        live_call("song.current_song_time", setattr, song, "current_song_time", float(position))
    elif jump_by is not None:
        delta = to_beats(ctx, jump_by, unit, "jump_by")
        if compat.has(song, "jump_by"):
            live_call("song.jump_by", song.jump_by, float(delta))
        else:
            now = compat.safe_getattr(song, "current_song_time", 0.0) or 0.0
            live_call("song.current_song_time", setattr, song, "current_song_time",
                      max(0.0, float(now) + float(delta)))
    return _position_payload(ctx)


@command("arrangement.back_to_arranger",
         doc="Press 'Back to Arrangement': session clips stop overriding the timeline")
def arrangement_back_to_arranger(ctx, track=None):
    """Return playback to the arrangement.

    Args:
        track: only this track (Live 12 ``Track.back_to_arranger``); omit for the
            whole set (``song.back_to_arranger = False``).

    Returns:
        {"was_overriding": bool, "back_to_arranger": false, "track"?,
         "pending"?: true}

    Gotchas:
        Live 12.4.5 applies the switch on its next display tick: reading the
        flag in the same command still shows the old value, so the result
        reports the requested state and ``pending: true`` when Live has not
        caught up yet (read again with arrangement.position / tracks.get).
        Stopping a track's session clips (clip stop buttons) also makes a
        track leave the arrangement — that is what this undoes.
    """
    if track is not None:
        track_obj = ctx.track(track, include_returns=False, include_master=False)
        if not compat.has(track_obj, "back_to_arranger"):
            raise BridgeError("unsupported", "Track.back_to_arranger needs Live 12")
        before = bool(compat.safe_getattr(track_obj, "back_to_arranger", False))
        live_call("track.back_to_arranger", setattr, track_obj, "back_to_arranger", False)
        result = {"track": compat.safe_getattr(track_obj, "name"), "was_overriding": before,
                  "back_to_arranger": False}
        if compat.safe_getattr(track_obj, "back_to_arranger", False):
            result["pending"] = True
        return result
    song = ctx.song
    before = bool(compat.safe_getattr(song, "back_to_arranger", False))
    live_call("song.back_to_arranger", setattr, song, "back_to_arranger", False)
    result = {"was_overriding": before, "back_to_arranger": False}
    if compat.safe_getattr(song, "back_to_arranger", False):
        result["pending"] = True
    return result
