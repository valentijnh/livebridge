"""Arrangement tools — the timeline: what is where, create / copy / move / delete clips.

The loop brace and the playhead are transport state: `live_transport_set_loop`,
`live_transport_set_position` and `live_transport_get` (the bridge commands `arrangement.loop` /
`arrangement.position` do the same with `unit="bars"` and stay reachable through
`live_command_call`).

Units: times are beats (quarter notes) by default. With `unit="bars"` positions are 1-based bar
numbers in the song's time signature (bar 1 = the very start, bar 17 = beat 64 in 4/4) and
lengths are numbers of bars. A full "bars.beats.sixteenths" string ("17.1.1"; lengths "4.0.0")
works in any unit.

Arrangement clips are addressed by `track` + `index` (0-based, in time order), by `track` + `at`
(the clip covering that time) or by `clip` (a path such as `"song.tracks[0].arrangement_clips[2]"`
or a clip name).

Building a song: `live_arrangement_from_scenes` lays scenes out as sections in one call,
`live_arrangement_duplicate_clip(length=...)` fills a range with one clip,
`live_arrangement_copy_range` repeats a section, `live_arrangement_resize_clip` changes a clip's
length on the timeline. Track automation lanes: `live_automation_record`.

Not possible through Live's API (say so instead of improvising): consolidating clips, exporting
or rendering audio/MIDI, freezing/flattening, drawing arrangement automation lanes directly
(record them instead), time selections and insert/delete-time commands.
"""

from __future__ import annotations

import re
from typing import Any

from . import bridge_call, drop_none, tool_error
from ..client import BridgeClient
from .clips import address_args, clip_address, check_range

Ref = int | str
#: A time: beats (number) or a full "bars.beats.sixteenths" string ("17.1.1"; lengths "4.0.0").
Time = float | str

_BBS_RE = re.compile(r"^\s*\d+[.:]\d+[.:]\d+\s*$")

_UNITS = ("beats", "bars")
_DETAILS = ("minimal", "summary", "full")


def _unit_error(unit: str, cmd: str) -> dict[str, Any] | None:
    if unit not in _UNITS:
        return tool_error("unit must be 'beats' or 'bars'", cmd=cmd)
    return None


def _position_error(value: float | str | None, name: str, unit: str,
                    cmd: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, str):
        if not _BBS_RE.match(value):
            return tool_error(f"{name} must be beats (a number) or 'bars.beats.sixteenths' "
                              f"such as '17.1.1', got {value!r}", cmd=cmd)
        return None
    if unit == "bars" and value < 1:
        return tool_error(f"{name} is a 1-based bar number with unit='bars' (bar 1 = start)",
                          cmd=cmd)
    if value < 0:
        return tool_error(f"{name} must be >= 0 beats", cmd=cmd)
    return None


def _length_error(value: float | str | None, cmd: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, str):
        if not _BBS_RE.match(value):
            return tool_error("length must be beats/bars (a number) or a 0-based "
                              "'bars.beats.sixteenths' length such as '8.0.0'", cmd=cmd)
        return None
    if value <= 0:
        return tool_error("length must be > 0", cmd=cmd)
    return None


def _selector(track: Any, index: int | None, at: float | str | None, clip: str | None,
              cmd: str) -> dict[str, Any] | None:
    if clip is not None:
        if not clip.strip():
            return tool_error("clip must be a path or a clip name", cmd=cmd)
        return None
    if track is None or (index is None and at is None):
        return tool_error("Say which arrangement clip: track + index, track + at (a time), or "
                          "clip='song.tracks[0].arrangement_clips[1]' / a clip name.", cmd=cmd)
    if index is not None and at is not None:
        return tool_error("pass index or at, not both", cmd=cmd)
    return None


def register(mcp: Any, bridge: BridgeClient) -> None:
    """Register the arrangement tools on the MCP app."""

    @mcp.tool()
    def live_arrangement_overview(
        start: Time | None = None,
        end: Time | None = None,
        unit: str = "beats",
        grid: bool = True,
        max_columns: int = 64,
        include_empty: bool = False,
    ) -> Any:
        """The whole arrangement at a glance: every track's clips as time ranges, a bar grid, the
        loop, cue points and the playhead — the cheapest way to see a song's structure.

        Args:
            start, end: Window to show (default: from the start to the end of the last clip).
            unit: "beats" (default) or "bars".
            grid: Add a text lane per track: one character per column, "#" = a clip plays there,
                "." = empty. Columns are whole bars (`bars_per_column`).
            max_columns: Width of the grid (8..512).
            include_empty: Also list tracks without arrangement clips.

        Returns:
            {"signature": "4/4", "beats_per_bar", "song_length", "position",
            "loop": {"enabled", "start", "end"}, "cue_points": [[name, time]],
            "window": [start, end], "bars_per_column", "grid_starts_at_bar",
            "tracks": [{"index", "name", "type", "clips": [[start, end, name]], "lane": "##..##"}]}

        Gotchas: session clips are not part of the arrangement (see `live_clip_list`). Times in
        `clips` are beats even with unit="bars".
        """
        cmd = "arrangement.overview"
        error = _unit_error(unit, cmd) or _position_error(start, "start", unit, cmd) or \
            _position_error(end, "end", unit, cmd)
        if error:
            return error
        if not 8 <= max_columns <= 512:
            return tool_error("max_columns must be 8..512", cmd=cmd)
        args = drop_none(start=start, end=end, unit=unit, grid=grid, max_columns=max_columns)
        if include_empty:
            args["include_empty"] = True
        return bridge_call(bridge, cmd, args)

    @mcp.tool()
    def live_arrangement_clips(
        track: Ref | None = None,
        start: Time | None = None,
        end: Time | None = None,
        unit: str = "beats",
        detail: str = "minimal",
        include_take_lanes: bool = False,
        offset: int = 0,
        limit: int = 200,
    ) -> Any:
        """List arrangement clips with their positions (one track or all).

        Args:
            track: One track (index, name or path); omit for every track.
            start, end: Only clips overlapping this window (`unit`).
            unit: "beats" (default) or "bars".
            detail: "minimal" (default), "summary" (loop, markers, note_count / audio props) or
                "full".
            include_take_lanes: Also list clips on Live 12 take lanes (rows get `take_lane`, no
                `index`, and a path "song.tracks[i].take_lanes[j].arrangement_clips[k]" that
                the clip/note tools accept as `clip`).
            offset, limit: Paging (limit 1..2000).

        Returns:
            {"total", "offset", "count", "clips": [{"path", "name", "is_midi", "length", "track",
            "index", "start_time", "end_time", "start_bar": "17.1.1", "muted"?}], "next_offset"?}

        Gotchas: `index` is the position in that track's arrangement clip list — use it (or the
        `path`) with the other arrangement tools; it changes after moves and deletes.
        """
        cmd = "arrangement.list"
        error = _unit_error(unit, cmd) or _position_error(start, "start", unit, cmd) or \
            _position_error(end, "end", unit, cmd)
        if error:
            return error
        if detail not in _DETAILS:
            return tool_error(f"detail must be one of {', '.join(_DETAILS)}", cmd=cmd)
        if offset < 0 or not 1 <= limit <= 2000:
            return tool_error("offset must be >= 0 and limit 1..2000", cmd=cmd)
        args = drop_none(track=track, start=start, end=end, unit=unit, detail=detail,
                         offset=offset, limit=limit)
        if include_take_lanes:
            args["include_take_lanes"] = True
        return bridge_call(bridge, cmd, args)

    @mcp.tool()
    def live_arrangement_create_clip(
        track: Ref,
        start: Time,
        length: Time | None = None,
        unit: str = "beats",
        file_path: str | None = None,
        name: str | None = None,
        color_index: int | None = None,
        looping: bool | None = None,
    ) -> Any:
        """Create a clip on the arrangement timeline: an empty MIDI clip, or an audio clip from a
        file.

        Args:
            track: Target track — a MIDI track for MIDI clips, an audio track with `file_path`.
            start: Position (beats, or 1-based bar number with unit="bars").
            length: MIDI clip length (beats or bars); default one bar. Ignored for audio.
            unit: "beats" (default) or "bars".
            file_path: Absolute path of an audio file ON THE MACHINE RUNNING LIVE (wav, aif, mp3,
                flac, ogg ...) → places that file as an audio clip. Checked on the Live side.
            name: Clip name.  color_index: 0..69.  looping: MIDI clips only.

        Returns:
            The new clip: {"path", "name", "length", "start_time", "end_time", "start_bar",
            "index", "track", ...}

        Gotchas: MIDI clips need Live 12. Clips already at that spot are cut, as in Live's UI.
        Fill a new MIDI clip with `live_clip_add_notes` / `live_clip_write_pattern` /
        `live_clip_write_chords` using the returned `path` as `clip`.
        """
        cmd = "arrangement.create_audio_clip" if file_path is not None \
            else "arrangement.create_midi_clip"
        error = _unit_error(unit, cmd) or _position_error(start, "start", unit, cmd) or \
            check_range(color_index, "color_index", 0, 69, cmd)
        if error:
            return error
        if file_path is not None:
            if not file_path.strip():
                return tool_error("file_path must be a non-empty absolute path", cmd=cmd)
            if looping is not None:
                return tool_error("looping only applies to MIDI clips here", cmd=cmd)
            args = drop_none(track=track, file_path=file_path.strip(), start=start, unit=unit,
                             name=name, color_index=color_index)
        else:
            if isinstance(length, str):
                if not _BBS_RE.match(length):
                    return tool_error("length must be beats/bars (a number) or a 0-based "
                                      "'bars.beats.sixteenths' length such as '4.0.0'", cmd=cmd)
            elif length is not None and length <= 0:
                return tool_error("length must be > 0", cmd=cmd)
            args = drop_none(track=track, start=start, length=length, unit=unit, name=name,
                             color_index=color_index, looping=looping)
        return bridge_call(bridge, cmd, args)

    @mcp.tool()
    def live_arrangement_duplicate_clip(
        time: Time,
        track: Ref | None = None,
        slot: Ref | None = None,
        clip: str | None = None,
        target_track: Ref | None = None,
        unit: str = "beats",
        length: Time | None = None,
        count: int | None = None,
        replace: bool = True,
    ) -> Any:
        """Copy a session clip (or any clip) onto the arrangement timeline — once, repeated, or
        stretched to fill a length (one call instead of one per repetition).

        Args:
            time: Destination (beats, or 1-based bar number with unit="bars").
            track, slot / clip: The source clip (session address, or a path / clip name).
            target_track: Destination track (default: the source clip's own track). Must be the
                same kind (MIDI/audio).
            unit: "beats" (default) or "bars" — for `time` and `length`.
            length: Fill this much (8 with unit="bars" = 8 bars, "8.0.0"): a looping clip
                becomes ONE clip whose loop repeats over the length; a non-looping clip is
                repeated back to back, the last copy cut at the end.
            count: Or: place this many back-to-back copies (1..512).
            replace: With length/count, first delete clips on the destination track that start
                inside the filled range (default true — like pasting over them).

        Returns:
            Plain copy: the new arrangement clip summary (path, start_time, end_time, index ...,
            envelopes: {source, copied}, note?). With length/count: {"clips": [...], "count",
            "range": [start, end], "start_bar", "envelopes", "note"?}.

        Gotchas: notes and loop settings travel with the copy; clip envelopes are NOT reliably
        copied (Live 12.4.5 copied them on a track without devices but dropped all of them with
        an instrument on the track) — check `envelopes.copied`; when false, record the
        automation with live_automation_record or redraw it. A clip cannot be
        stretched over a clip that starts later on the same track (`note` says where Live
        stopped it). To lay out whole scenes use `live_arrangement_from_scenes`.
        """
        cmd = "arrangement.duplicate_clip"
        error = clip_address(track, slot, clip, cmd) or _unit_error(unit, cmd) or \
            _position_error(time, "time", unit, cmd) or _length_error(length, cmd)
        if error:
            return error
        if length is not None and count is not None:
            return tool_error("pass length or count, not both", cmd=cmd)
        if count is not None and not 1 <= count <= 512:
            return tool_error("count must be 1..512", cmd=cmd)
        args = address_args(track, slot, clip)
        args.update(drop_none(time=time, target_track=target_track, unit=unit, length=length,
                              count=count))
        if not replace and (length is not None or count is not None):
            args["replace"] = False
        return bridge_call(bridge, cmd, args)

    @mcp.tool()
    def live_arrangement_resize_clip(
        track: Ref | None = None,
        index: int | None = None,
        at: Time | None = None,
        clip: str | None = None,
        length: Time | None = None,
        end: Time | None = None,
        unit: str = "beats",
    ) -> Any:
        """Lengthen or shorten an arrangement clip where it sits on the timeline.

        Args:
            track + index / track + at / clip: Which clip (see `live_arrangement_delete_clip`).
            length: New length on the timeline (beats, bars with unit="bars", or "4.0.0").
            end: Or the new end position (beats / 1-based bar).
            unit: "beats" (default) or "bars".

        Returns:
            {"clip": {summary}, "length": beats kept, "requested", "capped"?, "note"?}

        Gotchas: a looped clip keeps its loop, which repeats inside the new length (a 2-bar
        loop resized to 8 bars plays 4 times) — that is how to extend a loop in the
        arrangement (changing the loop itself does not change the timeline length). Live stops
        a lengthened clip at the next clip on the track (`capped`). Unwarped audio cannot be
        resized in beats.
        """
        cmd = "arrangement.resize_clip"
        error = _selector(track, index, at, clip, cmd) or _unit_error(unit, cmd) or \
            _position_error(at, "at", unit, cmd) or _position_error(end, "end", unit, cmd) or \
            _length_error(length, cmd)
        if error:
            return error
        if (length is None) == (end is None):
            return tool_error("pass length or end", cmd=cmd)
        if clip is not None:
            args = drop_none(clip=clip.strip(), track=track)
        else:
            args = drop_none(track=track, index=index, at=at)
        args.update(drop_none(length=length, end=end, unit=unit))
        return bridge_call(bridge, cmd, args)

    @mcp.tool()
    def live_arrangement_from_scenes(
        sections: list[Any],
        start: Time | None = None,
        unit: str = "beats",
        tracks: list[Ref] | None = None,
        replace: bool = True,
    ) -> Any:
        """Build a song structure in ONE call: lay session scenes out one after another on the
        timeline — every track's clip of each scene is copied and filled to the section length.

        Args:
            sections: Scenes in song order — indices/names, or objects {"scene": "Verse",
                "bars": 16} / {"scene": 2, "length": 32 (in `unit`)} / {"scene": "Drop",
                "bars": 8, "repeat": 2}. Default section length = the scene's longest clip.
                Example: [{"scene": "Intro", "bars": 8}, {"scene": "Verse", "bars": 16},
                {"scene": "Chorus", "bars": 16}, {"scene": "Outro", "bars": 8}].
            start: Where the first section begins (beats, 1-based bar with unit="bars",
                "17.1.1"); default the very start.
            unit: "beats" (default) or "bars" — for `start` and `length`.
            tracks: Only these tracks (default every regular track).
            replace: Delete clips of those tracks that start inside each section first
                (default true).

        Returns:
            {"sections": [{"scene", "name", "start", "end", "start_bar", "length", "clips",
            "tracks"}], "start", "end", "end_bar", "placed", "note"?}

        Gotchas: looping clips repeat inside their section, non-looping ones are repeated and
        cut. Tracks without a clip in a scene stay empty there. Scene tempo/signature changes are
        not written. Check the result with `live_arrangement_overview`.
        """
        cmd = "arrangement.from_scenes"
        error = _unit_error(unit, cmd) or _position_error(start, "start", unit, cmd)
        if error:
            return error
        if not sections:
            return tool_error("sections must list at least one scene", cmd=cmd)
        for position, entry in enumerate(sections):
            if isinstance(entry, dict):
                if "scene" not in entry:
                    return tool_error(f"sections[{position}] needs a 'scene'", cmd=cmd)
                unknown = sorted(set(entry) - {"scene", "bars", "length", "repeat"})
                if unknown:
                    return tool_error(f"sections[{position}] has unknown keys {unknown}; "
                                      "allowed: scene, bars, length, repeat", cmd=cmd)
            elif not isinstance(entry, (int, str)) or isinstance(entry, bool):
                return tool_error(f"sections[{position}] must be a scene index/name or "
                                  "{scene, bars?}", cmd=cmd)
        args: dict[str, Any] = drop_none(sections=sections, start=start, unit=unit)
        if tracks is not None:
            args["tracks"] = tracks
        if not replace:
            args["replace"] = False
        return bridge_call(bridge, cmd, args)

    @mcp.tool()
    def live_arrangement_copy_range(
        start: Time,
        end: Time,
        destination: Time,
        unit: str = "beats",
        tracks: list[Ref] | None = None,
        replace: bool = True,
    ) -> Any:
        """Copy a section of the arrangement to another position — all tracks or some
        ("copy bars 9-16 to bar 33").

        Args:
            start, end: The range (beats, 1-based bars with unit="bars", or "9.1.1").
            destination: Where it goes (same units).
            unit: "beats" (default) or "bars".
            tracks: Only these tracks (default every regular track).
            replace: Delete clips of those tracks that start inside the destination range first
                (default true).

        Returns:
            {"copied": n, "range": [start, end], "destination": [start, end],
            "tracks": [{"name", "clips"}], "skipped"?: [{"track", "name", "start_time",
            "reason"}]}

        Gotchas: clips that start before the range are skipped (listed); clips running past
        the end are cut on the copy. The destination must not overlap the range.
        """
        cmd = "arrangement.copy_range"
        error = _unit_error(unit, cmd) or _position_error(start, "start", unit, cmd) or \
            _position_error(end, "end", unit, cmd) or \
            _position_error(destination, "destination", unit, cmd)
        if error:
            return error
        args: dict[str, Any] = {"start": start, "end": end, "destination": destination,
                                "unit": unit}
        if tracks is not None:
            args["tracks"] = tracks
        if not replace:
            args["replace"] = False
        return bridge_call(bridge, cmd, args)

    @mcp.tool()
    def live_arrangement_delete_clip(
        track: Ref | None = None,
        index: int | None = None,
        at: Time | None = None,
        clip: str | None = None,
        unit: str = "beats",
    ) -> Any:
        """Delete a clip from the arrangement timeline.

        Args:
            track + index: The track's n-th arrangement clip (0-based, time order), or
            track + at: the clip covering that time (beats, or bar number with unit="bars"), or
            clip: its path ("song.tracks[0].arrangement_clips[1]") or name.
            unit: "beats" (default) or "bars" — for `at`.

        Returns:
            {"deleted": path, "name", "start_time", "end_time"}
        """
        cmd = "arrangement.delete_clip"
        error = _selector(track, index, at, clip, cmd) or _unit_error(unit, cmd) or \
            _position_error(at, "at", unit, cmd)
        if error:
            return error
        if clip is not None:
            return bridge_call(bridge, cmd, drop_none(clip=clip.strip(), track=track))
        return bridge_call(bridge, cmd, drop_none(track=track, index=index, at=at, unit=unit))

    @mcp.tool()
    def live_arrangement_move_clip(
        start: Time,
        track: Ref | None = None,
        index: int | None = None,
        at: Time | None = None,
        clip: str | None = None,
        target_track: Ref | None = None,
        unit: str = "beats",
    ) -> Any:
        """Move an arrangement clip to a new position (and optionally to another track).

        Args:
            start: New start (beats, or 1-based bar number with unit="bars").
            track + index / track + at / clip: Which clip (see `live_arrangement_delete_clip`).
            target_track: Another track of the same kind (MIDI/audio).
            unit: "beats" (default) or "bars" — for `start` and `at`.

        Returns:
            {"moved_from": beats, "clip": {new clip summary with its new path/index},
            "envelopes"?: {source, copied}, "note"?}

        Gotchas: Live's API cannot move clips directly, so this duplicates the clip to the new
        place and deletes the original (one undo step) — the clip gets a new path/index.
        Moving onto other clips cuts them, as in Live's UI. Clip envelopes may not survive the
        move (`envelopes.copied` false + `note`; undo restores the original).
        """
        cmd = "arrangement.move_clip"
        error = _selector(track, index, at, clip, cmd) or _unit_error(unit, cmd) or \
            _position_error(start, "start", unit, cmd) or _position_error(at, "at", unit, cmd)
        if error:
            return error
        if clip is not None:
            args = drop_none(clip=clip.strip(), track=track)
        else:
            args = drop_none(track=track, index=index, at=at)
        args.update(drop_none(start=start, target_track=target_track, unit=unit))
        return bridge_call(bridge, cmd, args)
