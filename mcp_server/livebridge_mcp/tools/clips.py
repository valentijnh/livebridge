"""Clip tools — session and arrangement clips: list, inspect, create, delete, duplicate, launch,
stop, edit properties, quantize / crop / duplicate-loop / reverse, warp markers, Live's audio
conversions (audio -> MIDI / Drum Rack / Simpler) and the Groove Pool.

How to address a clip (every `live_clip_*` tool, including the note tools):

- `track` + `slot`: a session clip. `track` = index into the regular tracks (0-based), a track
  name ("Bass", case-insensitive prefix works) or a LOM path; `slot` = scene index (0-based) or
  scene name ("Chorus").
- `clip`: a LOM path (`"song.tracks[0].clip_slots[2].clip"`, an arrangement clip
  `"song.tracks[1].arrangement_clips[0]"`), a clip name, or `"selected"` (the clip open in
  Live's Detail view). `clip` wins over `track`/`slot`.

Times are beats (1 beat = a quarter note) unless a tool offers `unit="bars"`; a full
"bars.beats.sixteenths" string ("2.1.1" position, "1.0.0" length) works in any unit.
"""

from __future__ import annotations

import re
from typing import Any

from . import bridge_call, drop_none, tool_error
from ..client import BridgeClient

Ref = int | str
#: A time: beats (number) or a full "bars.beats.sixteenths" string ("17.1.1"; lengths "4.0.0").
Time = float | str

_BBS_RE = re.compile(r"^\s*\d+[.:]\d+[.:]\d+\s*$")

_DETAILS = ("minimal", "summary", "full")
_UNITS = ("beats", "bars")
_EDIT_ACTIONS = {"quantize": "clips.quantize", "crop": "clips.crop",
                 "duplicate_loop": "clips.duplicate_loop", "reverse": "clips.reverse"}
_RECORD_GRIDS = ("1/4", "1/8", "1/8T", "1/8+1/8T", "1/16", "1/16T", "1/16+1/16T", "1/32")


def clip_address(track: Any, slot: Any, clip: Any, cmd: str) -> dict[str, Any] | None:
    """Return a tool error when a clip address is incomplete, else ``None``.

    Shared by the clip, note and arrangement tool modules.
    """
    if clip is not None:
        if not isinstance(clip, str) or not clip.strip():
            return tool_error("clip must be a LOM path, a clip name or 'selected'", cmd=cmd)
        return None
    if track is None:
        return tool_error(
            "Say which clip: track + slot (e.g. track='Bass', slot=0) or clip='<path|name|"
            "selected>'.", cmd=cmd)
    if slot is None:
        return tool_error("slot is required with track (scene index or name) — or pass clip=",
                          cmd=cmd)
    return None


def address_args(track: Any, slot: Any, clip: Any) -> dict[str, Any]:
    """The address part of a request (``clip`` alone when given)."""
    if clip is not None:
        return {"clip": clip.strip()} if isinstance(clip, str) else {"clip": clip}
    return drop_none(track=track, slot=slot)


def time_error(value: Any, name: str, cmd: str, positive: bool = False) -> dict[str, Any] | None:
    """A tool error for a malformed time (number or "bars.beats.sixteenths"), else ``None``."""
    if value is None:
        return None
    if isinstance(value, str):
        if _BBS_RE.match(value):
            return None
        return tool_error(f"{name} must be a number or 'bars.beats.sixteenths' such as "
                          f"'2.1.1' (lengths: '1.0.0'), got {value!r}", cmd=cmd)
    if positive and value <= 0:
        return tool_error(f"{name} must be > 0", cmd=cmd)
    return None


def check_range(value: float | int | None, name: str, low: float, high: float,
                cmd: str) -> dict[str, Any] | None:
    """A tool error when ``value`` is outside ``[low, high]``."""
    if value is not None and not (low <= value <= high):
        return tool_error(f"{name} must be between {low} and {high}, got {value}", cmd=cmd)
    return None


def register(mcp: Any, bridge: BridgeClient) -> None:
    """Register the clip tools on the MCP app."""

    @mcp.tool()
    def live_clip_list(
        track: Ref | None = None,
        detail: str = "minimal",
        include_arrangement: bool = False,
        playing_only: bool = False,
        offset: int = 0,
        limit: int = 200,
    ) -> Any:
        """List the clips of one track or of the whole set (empty slots are skipped).

        Args:
            track: One track (index, name or path). Omit for every regular track.
            detail: "minimal" (path, name, is_midi, length — default), "summary" (loop, markers,
                playing state, audio props, note_count) or "full" (+ launch settings).
            include_arrangement: Also list arrangement (timeline) clips; they carry
                `start_time`/`end_time` instead of `slot`.
            playing_only: Only clips that are playing or triggered right now.
            offset, limit: Paging (limit 1..2000).

        Returns:
            {"total", "offset", "count", "clips": [{"path", "name", "is_midi", "length", "track",
            "slot"?, "start_time"?, "end_time"?, "is_playing"?}], "next_offset"?}

        Gotchas: `slot` is the scene index. Use each row's `path` as `clip=` in other tools.
        """
        if detail not in _DETAILS:
            return tool_error(f"detail must be one of {', '.join(_DETAILS)}", cmd="clips.list")
        if offset < 0 or not 1 <= limit <= 2000:
            return tool_error("offset must be >= 0 and limit 1..2000", cmd="clips.list")
        args = drop_none(track=track, detail=detail, offset=offset, limit=limit)
        if include_arrangement:
            args["include_arrangement"] = True
        if playing_only:
            args["playing_only"] = True
        return bridge_call(bridge, "clips.list", args)

    @mcp.tool()
    def live_clip_get(
        track: Ref | None = None,
        slot: Ref | None = None,
        clip: str | None = None,
        detail: str = "full",
        warp_markers: bool = False,
    ) -> Any:
        """Everything about one clip: loop, markers, launch settings, audio properties.

        Args:
            track, slot: Session address (track index/name + scene index/name).
            clip: Instead of track/slot — a LOM path, a clip name or "selected".
            detail: "minimal", "summary" or "full" (default).
            warp_markers: Audio clips — include `warp_markers` as [[sample_time (seconds),
                beat_time], ...].

        Returns:
            {"path", "name", "is_midi", "length", "looping", "loop_start", "loop_end",
            "start_marker", "end_marker", "muted", "is_playing", "signature", "track", "slot",
            "playing_position", "launch_mode", "launch_quantization", "legato", ...;
            MIDI: "note_count"; audio: "file_path", "warping", "warp_mode", "gain", "gain_db",
            "pitch_coarse", "pitch_fine", "available_warp_modes"}

        Gotchas: notes are not included — use `live_clip_get_notes`. Unwarped audio clips report
        positions in seconds, not beats.
        """
        error = clip_address(track, slot, clip, "clips.get")
        if error:
            return error
        if detail not in _DETAILS:
            return tool_error(f"detail must be one of {', '.join(_DETAILS)}", cmd="clips.get")
        args = address_args(track, slot, clip)
        args["detail"] = detail
        if warp_markers:
            args["include_warp_markers"] = True
        return bridge_call(bridge, "clips.get", args)

    @mcp.tool()
    def live_clip_create(
        track: Ref,
        slot: Ref | None = None,
        length: Time = 4.0,
        unit: str = "beats",
        name: str | None = None,
        color_index: int | None = None,
        looping: bool | None = None,
        file_path: str | None = None,
    ) -> Any:
        """Create an empty MIDI clip in a session slot — or an audio clip from a file.

        Args:
            track: A MIDI track (an audio track when `file_path` is given).
            slot: Scene index or name; omit for the track's first empty slot.
            length: Clip length in `unit` (MIDI clips only). Default 4 beats = 1 bar in 4/4.
            unit: "beats" (default) or "bars" (uses the song's time signature).
            name: Clip name.  color_index: 0..69 (Live's colour palette).
            looping: Loop switch (Live's default is on).
            file_path: Absolute path of an audio file ON THE MACHINE RUNNING LIVE → creates an
                audio clip in the slot (Live 12).

        Returns:
            The new clip summary with `path`, `track` and `slot`.

        Gotchas: fails with invalid_state if the slot already holds a clip (delete it or choose
        another slot) or if the track type does not match. To fill it, follow up with
        `live_clip_add_notes`, `live_clip_write_pattern` or `live_clip_write_chords` — those can
        also create the clip themselves.
        """
        if unit not in _UNITS:
            return tool_error("unit must be 'beats' or 'bars'", cmd="clips.create")
        if file_path is None:
            error = time_error(length, "length", "clips.create", positive=True)
            if error:
                return error
        error = check_range(color_index, "color_index", 0, 69, "clips.create")
        if error:
            return error
        if file_path is not None and not file_path.strip():
            return tool_error("file_path must be a non-empty absolute path", cmd="clips.create")
        args = drop_none(track=track, slot=slot, unit=unit, name=name, color_index=color_index,
                         looping=looping, file_path=file_path)
        if file_path is None:
            args["length"] = length if isinstance(length, str) else float(length)
        return bridge_call(bridge, "clips.create", args)

    @mcp.tool()
    def live_clip_delete(
        track: Ref | None = None,
        slot: Ref | None = None,
        clip: str | None = None,
    ) -> Any:
        """Delete a session clip (track + slot) or any clip by `clip` (path / name / "selected").

        Args:
            track, slot: Session address.
            clip: A LOM path — also arrangement clips, e.g. "song.tracks[0].arrangement_clips[1]".

        Returns:
            {"deleted": "<path it had>", "name": "<clip name>"}

        Gotchas: one undo step in Live. Arrangement clip indices after it shift down by one.
        """
        error = clip_address(track, slot, clip, "clips.delete")
        if error:
            return error
        return bridge_call(bridge, "clips.delete", address_args(track, slot, clip))

    @mcp.tool()
    def live_clip_duplicate(
        track: Ref,
        slot: Ref,
        target_track: Ref | None = None,
        target_slot: Ref | None = None,
        overwrite: bool = False,
    ) -> Any:
        """Duplicate a session clip.

        Args:
            track, slot: The source clip.
            target_track: Destination track (default: same track). Must be the same type
                (MIDI→MIDI, audio→audio).
            target_slot: Destination scene index/name. Omit both targets for the next EMPTY
                slot below the source (a scene is added at the end when none is free — the
                result then has "created_scene"). Clips below are never overwritten.
                With only `target_track`, its first empty slot is used.
            overwrite: Allow replacing a clip already in the target slot.

        Returns:
            {"source": path, "target": slot path, "clip": {summary of the copy},
             "created_scene"?: scene index}

        Gotchas: to copy a clip onto the timeline use `live_arrangement_duplicate_clip`.
        """
        args = drop_none(track=track, slot=slot, target_track=target_track,
                         target_slot=target_slot)
        if overwrite:
            args["overwrite"] = True
        return bridge_call(bridge, "clips.duplicate", args)

    @mcp.tool()
    def live_clip_fire(
        track: Ref | None = None,
        slot: Ref | None = None,
        clip: str | None = None,
        force_legato: bool = False,
        launch_quantization: str | None = None,
    ) -> Any:
        """Launch a session clip slot (plays its clip; an empty slot fires its stop button).

        Args:
            track, slot: Session address (or `clip` = path / name / "selected").
            force_legato: Start at once, playhead kept in sync with the running clip
                (ignored for an empty slot — Live refuses it there).
            launch_quantization: Override the global launch quantization for this launch:
                "none", "8 bars", "4 bars", "2 bars", "1 bar", "1/2", "1/2T", "1/4", "1/4T",
                "1/8", "1/8T", "1/16", "1/16T", "1/32".

        Returns:
            {"path", "has_clip", "is_triggered", "is_playing"}

        Gotchas: launches are quantized, so `is_playing` is usually false right after the call
        and `is_triggered` true; the transport starts if it was stopped. An empty slot on an armed
        track starts recording. Scenes: use the scene tools.
        """
        error = clip_address(track, slot, clip, "clips.fire")
        if error:
            return error
        args = address_args(track, slot, clip)
        if force_legato:
            args["force_legato"] = True
        if launch_quantization is not None:
            args["launch_quantization"] = launch_quantization
        return bridge_call(bridge, "clips.fire", args)

    @mcp.tool()
    def live_clip_stop(
        track: Ref | None = None,
        slot: Ref | None = None,
        clip: str | None = None,
        quantized: bool = True,
    ) -> Any:
        """Stop clips: one clip (track + slot or `clip`), every clip on a track (track only) or
        every clip in the set (no arguments).

        Args:
            track, slot, clip: What to stop (see above).
            quantized: Stop on the launch-quantization grid (default) or immediately
                (track/set stops only).

        Returns:
            {"stopped": "clip"|"track"|"all", "path"?}

        Gotchas: stopping all clips keeps the transport running (use the transport tools to
        stop playback).
        """
        args = address_args(track, slot, clip) if clip is not None else drop_none(
            track=track, slot=slot)
        if slot is not None and track is None and clip is None:
            return tool_error("slot needs a track", cmd="clips.stop")
        args["quantized"] = quantized
        return bridge_call(bridge, "clips.stop", args)

    @mcp.tool()
    def live_clip_set(
        track: Ref | None = None,
        slot: Ref | None = None,
        clip: str | None = None,
        name: str | None = None,
        color_index: int | None = None,
        color: int | str | list[int] | None = None,
        muted: bool | None = None,
        looping: bool | None = None,
        loop_start: Time | None = None,
        loop_end: Time | None = None,
        start_marker: Time | None = None,
        end_marker: Time | None = None,
        length: Time | None = None,
        position: Time | None = None,
        unit: str = "beats",
        signature: str | None = None,
        launch_mode: str | None = None,
        launch_quantization: str | None = None,
        legato: bool | None = None,
        velocity_amount: float | None = None,
        warping: bool | None = None,
        warp_mode: str | None = None,
        gain: float | None = None,
        gain_db: float | None = None,
        pitch_coarse: int | None = None,
        pitch_fine: float | None = None,
        ram_mode: bool | None = None,
        groove: int | str | None = None,
    ) -> Any:
        """Change any clip properties in one call (one undo step): rename, colour, mute, loop,
        markers, length, launch behaviour, groove and audio settings. Pass only what should
        change.

        Args:
            track, slot / clip: Which clip (session address, or path / name / "selected").
            name: New clip name.
            color_index: 0..69 (Live palette).
            color: "#RRGGBB", [r, g, b] (Live picks the nearest palette colour), a palette
                index 0..69 or a colour name ("red", "blue") — same forms as live_tracks_set.
            muted: true = clip deactivated.
            looping: Loop switch on/off.
            loop_start, loop_end, start_marker, end_marker, position: Positions in `unit`
                (or "bars.beats.sixteenths", e.g. "2.1.1" = beat 4 in 4/4). Pairs are written
                in a safe order, so moving a loop later in one call works. On an UNLOOPED clip
                Live keeps the clip start/end in loop_start/loop_end (the start marker follows
                loop_start), so there start_marker/loop_start both set the start and
                end_marker/loop_end both set the end (pass looping=true in the same call to
                edit a real loop).
            length: New loop length (looping clips) or start→end span (unlooped), in
                `unit`. E.g. length=2, unit="bars" makes a 2-bar loop.
            unit: "beats" (default) or "bars" — with bars, positions are 1-based bar numbers
                (bar 1 = clip start) and lengths are bar counts.
            signature: Clip time signature, "3/4".
            launch_mode: "trigger", "gate", "toggle" or "repeat".
            launch_quantization: "global", "none", "8 bars", "4 bars", "2 bars", "1 bar", "1/2",
                "1/2T", "1/4", "1/4T", "1/8", "1/8T", "1/16", "1/16T", "1/32".
            legato: Legato launch.  velocity_amount: 0..1 (velocity → volume).
            groove: A Groove Pool groove (index or name, see live_groove_pool) — the clip plays
                with that groove's swing/feel ("MPC 16 Swing-62"). Cannot be removed via the
                API; set the groove's amounts to 0 instead.
            Audio clips only:
            warping: Warp on/off.  warp_mode: "beats", "tones", "texture", "repitch", "complex",
                "complex_pro" ("rex" for REX files).
            gain: Raw 0..1 value, or gain_db: decibels (e.g. -6; Live's range is about -inf..+24).
            pitch_coarse: -48..48 semitones.  pitch_fine: cents — Live keeps -50..+49 and
                carries whole semitones into pitch_coarse (+60 reads back as +1 st / -40 ct).
            ram_mode: Load the sample into RAM.

        Returns:
            {"changed": [property names], "clip": {full summary after the change},
             "adjusted"?: {property: value Live actually kept}}

        Gotchas: audio-only properties on a MIDI clip fail with invalid_state. Unwarped audio
        cannot loop and uses seconds for positions. If one property fails, the ones before it
        stay applied — Live's undo reverts the whole call.
        """
        cmd = "clips.set"
        error = clip_address(track, slot, clip, cmd)
        if error:
            return error
        if unit not in _UNITS:
            return tool_error("unit must be 'beats' or 'bars'", cmd=cmd)
        for value, label, low, high in ((color_index, "color_index", 0, 69),
                                        (velocity_amount, "velocity_amount", 0.0, 1.0),
                                        (gain, "gain", 0.0, 1.0),
                                        (pitch_coarse, "pitch_coarse", -48, 48),
                                        (pitch_fine, "pitch_fine", -500.0, 500.0)):
            error = check_range(value, label, low, high, cmd)
            if error:
                return error
        for value, label in ((loop_start, "loop_start"), (loop_end, "loop_end"),
                             (start_marker, "start_marker"), (end_marker, "end_marker"),
                             (position, "position")):
            error = time_error(value, label, cmd)
            if error:
                return error
        error = time_error(length, "length", cmd, positive=True)
        if error:
            return error
        if gain is not None and gain_db is not None:
            return tool_error("pass gain or gain_db, not both", cmd=cmd)
        changes = drop_none(
            name=name, color_index=color_index, color=color, muted=muted, looping=looping,
            loop_start=loop_start, loop_end=loop_end, start_marker=start_marker,
            end_marker=end_marker, length=length, position=position, signature=signature,
            launch_mode=launch_mode, launch_quantization=launch_quantization, legato=legato,
            velocity_amount=velocity_amount, warping=warping, warp_mode=warp_mode, gain=gain,
            gain_db=gain_db, pitch_coarse=pitch_coarse, pitch_fine=pitch_fine,
            ram_mode=ram_mode, groove=groove)
        if not changes:
            return tool_error("nothing to change — pass at least one property", cmd=cmd)
        args = address_args(track, slot, clip)
        args.update(changes)
        args["unit"] = unit
        return bridge_call(bridge, cmd, args)

    @mcp.tool()
    def live_clip_edit(
        action: str,
        track: Ref | None = None,
        slot: Ref | None = None,
        clip: str | None = None,
        grid: str = "1/16",
        amount: float = 1.0,
    ) -> Any:
        """Clip editing commands: quantize, crop, duplicate the loop, reverse.

        Args:
            action: "quantize" (Live's Quantize on every note — or the warp markers of an audio
                clip), "crop" (keep the loop — plus a lead-in from an earlier start marker —
                or start..end marker when unlooped; notes outside are deleted and the kept part
                moves to beat 0), "duplicate_loop" (double the loop and copy its notes/envelopes
                into the new half; notes after the loop move later; an unlooped clip doubles
                its start..end region) or "reverse" (MIDI: mirror the notes inside the loop in time;
                audio: only where Live's API allows it — Live 12.4 answers "unsupported").
            track, slot / clip: Which clip.
            grid: quantize only — "1/4", "1/8", "1/8T", "1/8+1/8T", "1/16" (default), "1/16T",
                "1/16+1/16T", "1/32".
            amount: quantize only — strength 0..1 (1 = fully on the grid).

        Returns:
            quantize: {"clip", "grid", "amount"}; crop / duplicate_loop: {"clip": {summary}};
            reverse: {"clip", "reversed": number of notes | "audio"}

        Gotchas: Live's quantize applies the song's swing and always works on the whole clip.
        For a note selection, any grid, swing or quantizing note ends use
        `live_clip_transform_notes`.
        """
        cmd = _EDIT_ACTIONS.get(action)
        if cmd is None:
            return tool_error(f"action must be one of {', '.join(_EDIT_ACTIONS)}",
                              cmd="clips.edit")
        error = clip_address(track, slot, clip, cmd)
        if error:
            return error
        args = address_args(track, slot, clip)
        if action == "quantize":
            if grid not in _RECORD_GRIDS:
                return tool_error(f"grid must be one of {', '.join(_RECORD_GRIDS)}", cmd=cmd)
            if not 0.0 <= amount <= 1.0:
                return tool_error("amount must be between 0 and 1", cmd=cmd)
            args.update(grid=grid, amount=amount)
        return bridge_call(bridge, cmd, args)

    @mcp.tool()
    def live_clip_warp(
        action: str = "list",
        track: Ref | None = None,
        slot: Ref | None = None,
        clip: str | None = None,
        beat_time: float | None = None,
        sample_time: float | None = None,
        distance: float | None = None,
        to: float | None = None,
    ) -> Any:
        """Warp markers of an audio clip: list, add, move, remove — fix the timing of a loop or
        a sample (e.g. pull a late hit onto the beat).

        Args:
            action: "list" (default), "add", "move" or "remove".
            track, slot / clip: The audio clip (must be warped for add/move).
            beat_time: The marker's beat (clip beats). add: where to put it; move/remove: which
                marker (the one at that beat).
            sample_time: add only — seconds into the audio file that should sit at beat_time.
                Default: where the audio plays at that beat now (a "pinned" marker).
            distance: move only — beats to move the marker by (stretches the audio around it);
                or `to`: its new beat.

        Returns:
            {"clip", "action", "warp_markers": [[seconds, beat], ...], "count"}

        Gotchas: typical flow — add a pinned marker at the transient, then move it onto the grid.
        Moves that would cross a neighbour are refused (invalid_state). Live keeps a helper
        marker 1/32 beat after the last one. Snap all markers to a grid with
        live_clip_edit(action="quantize").
        """
        cmd = "clips.warp"
        error = clip_address(track, slot, clip, cmd)
        if error:
            return error
        if action not in ("list", "add", "move", "remove"):
            return tool_error("action must be list, add, move or remove", cmd=cmd)
        if action != "list" and beat_time is None:
            return tool_error(f"beat_time is required for action={action}", cmd=cmd)
        if action == "move" and (distance is None) == (to is None):
            return tool_error("move needs distance (beats) or to (the new beat)", cmd=cmd)
        if sample_time is not None and sample_time < 0:
            return tool_error("sample_time must be >= 0 seconds", cmd=cmd)
        args = address_args(track, slot, clip)
        args["action"] = action
        args.update(drop_none(beat_time=beat_time, sample_time=sample_time, distance=distance,
                              to=to))
        return bridge_call(bridge, cmd, args)

    @mcp.tool()
    def live_clip_convert(
        to: str,
        track: Ref | None = None,
        slot: Ref | None = None,
        clip: str | None = None,
        type: str = "drums",
    ) -> Any:
        """Live's audio conversions on an audio clip (Splice loops!): extract MIDI from drums /
        a melody / chords, or turn the clip into a playable Drum Rack or Simpler track.

        Args:
            to: "midi" (Convert Drums/Melody/Harmony to New MIDI Track), "drum_rack" (a new
                track with a Drum Rack, the clip on the first pad) or "simpler" (a new MIDI
                track with a Simpler playing the clip).
            track, slot / clip: The audio clip (session or arrangement).
            type: For to="midi": "drums" (default), "melody" or "harmony".

        Returns:
            {"clip", "to", "type"?, "new_tracks": [{"index", "name"}], "pending"?, "note"?}

        Gotchas: to="midi" analyses in the background — the new track ("Drums to MIDI" ...)
        appears a moment later (`pending`); list tracks again. Live names, places and selects
        the new tracks. Needs Suite/Standard.
        """
        cmd = "clips.convert"
        error = clip_address(track, slot, clip, cmd)
        if error:
            return error
        if to not in ("midi", "drum_rack", "simpler"):
            return tool_error("to must be midi, drum_rack or simpler", cmd=cmd)
        if to == "midi" and type not in ("drums", "melody", "harmony"):
            return tool_error("type must be drums, melody or harmony", cmd=cmd)
        args = address_args(track, slot, clip)
        args["to"] = to
        if to == "midi":
            args["type"] = type
        return bridge_call(bridge, cmd, args)

    @mcp.tool()
    def live_groove_pool(
        groove: int | str | None = None,
        timing: float | None = None,
        random: float | None = None,
        velocity: float | None = None,
        quantize: float | None = None,
        base: str | None = None,
        name: str | None = None,
        include_clips: bool = False,
    ) -> Any:
        """List the set's Groove Pool, or change a groove's amounts — Live's own swing/feel
        (MPC, SP-1200, Logic grooves ...). Apply a groove to a clip with
        live_clip_set(groove=...).

        Args:
            groove: The groove to change (index or name); omit to just list.
            timing, random, quantize: 0..100 (%); velocity: -100..100 (%).
            base: Groove base grid: "1/4", "1/8", "1/8T", "1/16", "1/16T", "1/32".
            name: Rename the groove.
            include_clips: Also list which session clips use each groove.

        Returns:
            {"count", "grooves": [{"index", "name", "base", "timing", "random", "velocity",
            "quantization", "clips"?}], "global_amount", "changed"?, "note"?}

        Gotchas: grooves get into the pool from Live's browser (Grooves category, .agr files —
        live_browser_search / live_browser_load); the API cannot add or delete pool entries.
        The global groove amount is live_transport_set(groove_amount=...).
        """
        cmd = "clips.grooves"
        for value, label, low, high in ((timing, "timing", 0, 100), (random, "random", 0, 100),
                                        (quantize, "quantize", 0, 100),
                                        (velocity, "velocity", -100, 100)):
            error = check_range(value, label, low, high, cmd)
            if error:
                return error
        changes = drop_none(timing=timing, random=random, velocity=velocity,
                            quantize=quantize, base=base, name=name)
        if changes and groove is None:
            return tool_error("say which groove to change (groove=<index|name>)", cmd=cmd)
        if groove is not None and not changes:
            return tool_error("nothing to change — pass timing, random, velocity, quantize, "
                              "base or name (omit groove to list)", cmd=cmd)
        if base is not None and base not in ("1/4", "1/8", "1/8T", "1/16", "1/16T", "1/32"):
            return tool_error("base must be 1/4, 1/8, 1/8T, 1/16, 1/16T or 1/32", cmd=cmd)
        args = drop_none(groove=groove, **changes)
        if include_clips:
            args["include_clips"] = True
        return bridge_call(bridge, cmd, args)
