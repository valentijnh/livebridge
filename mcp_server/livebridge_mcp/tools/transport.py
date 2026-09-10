"""Transport and whole-set tools: `live_set_snapshot` (call it first), play/stop/locate, tempo,
time signature, loop, record/overdub/punch flags, launch & record quantization, scale, undo/redo,
Capture MIDI and Stop All Clips.

Time arguments are **beats** (quarter notes) when you send a number, or Live's
`"bars.beats.sixteenths"` notation when you send a string (`"17.1.1"` = start of bar 17, 1-based).
Lengths given as strings are 0-based durations (`"4.0.0"` or `"4"` = four bars). Results carry both
forms (`{"beats": 64.0, "bbs": "17.1.1"}`), computed with the song's global time signature.
"""

from __future__ import annotations

from typing import Any

from . import bridge_call, drop_none, tool_error
from ..client import BridgeClient

_DETAILS = ("minimal", "summary", "full")
_SNAPSHOT_TIMEOUT = 30.0


def _slow_timeout(bridge: BridgeClient) -> float:
    try:
        return max(float(bridge.timeout), _SNAPSHOT_TIMEOUT)
    except (AttributeError, TypeError, ValueError):
        return _SNAPSHOT_TIMEOUT


def _bad_time(value: Any, name: str, cmd: str) -> dict[str, Any] | None:
    """Local sanity check for a beats-or-bbs argument (the bridge does the real parsing)."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < 0:
            return tool_error(f"{name} must be >= 0 beats", cmd=cmd)
        return None
    if isinstance(value, str) and value.strip():
        return None
    return tool_error(f"{name} must be beats (number) or 'bars.beats.sixteenths' like '9.1.1'",
                      cmd=cmd)


def register(mcp: Any, bridge: BridgeClient) -> None:
    """Register the transport / snapshot tools on the MCP app."""

    @mcp.tool()
    def live_set_snapshot(
        detail: str = "summary",
        include_clips: bool = True,
        include_devices: bool = True,
        include_returns: bool = True,
        include_scenes: bool = True,
        include_tracks: bool = True,
        offset: int = 0,
        limit: int | None = None,
    ) -> Any:
        """The whole Live set in ONE call — call this first, and again after structural changes.

        Args:
            detail: "minimal" (tracks = index/name/type/path only — cheapest overview),
                "summary" (default: mixer values, arm/mute/solo, routing, devices, clips) or
                "full" (+ device summaries, clip loop info, arrangement clip list).
            include_clips: list each track's non-empty session slots
                (`[{slot, name, length, playing?}]`) and its arrangement clip count.
            include_devices: list each track's devices (`[{index, name, class_name, type}]`).
            include_returns: include return tracks and the master track.
            include_scenes: include scenes (`[{index, name, tempo?, signature?}]`).
            include_tracks: false = only the song-level summary (tempo, signature, loop,
                record flags, quantization, scale, counts, selection) — very small.
            offset / limit: page through `song.tracks` on huge sets (returns/master/scenes are
                always complete; `paging: {total, offset, count, next_offset?}` appears when
                the list is paged).

        Returns:
            `{song:{tempo, signature, is_playing, position:{beats,bbs}, loop:{...}, scale:{...},
            track_count, scene_count, clip_trigger_quantization, ...}, tracks:[{path, index, name,
            type, color_index, volume, panning, sends, devices, clips, ...}], returns:[...],
            master:{...}, scenes:[...], cue_points:[...], selection:{track, scene, clip_slot,
            detail_clip, device, ...}, paging?}`

        Gotchas:
            - Compact form: booleans (mute, solo, arm, ...) appear only when true; defaults
              (monitoring "auto", slot index -1) are omitted.
            - Only tracks carry `path`: device j is `<track.path>.devices[j]`, the clip in slot s
              is `<track.path>.clip_slots[s].clip`, scene i is `song.scenes[i]`.
            - volume/panning/sends are raw parameter values (volume 0.85 = 0 dB, pan -1..1).
            - Indices shift when tracks/scenes are added or deleted — re-snapshot afterwards.
        """
        cmd = "song.snapshot" if include_tracks else "song.summary"
        if detail not in _DETAILS:
            return tool_error("detail must be 'minimal', 'summary' or 'full'", cmd=cmd)
        if offset < 0:
            return tool_error("offset must be >= 0", cmd=cmd)
        if limit is not None and limit < 1:
            return tool_error("limit must be >= 1", cmd=cmd)
        if not include_tracks:
            return bridge_call(bridge, "song.summary",
                               {"detail": "minimal" if detail == "minimal" else "summary"})
        args = drop_none(detail=detail, include_clips=include_clips,
                         include_devices=include_devices, include_returns=include_returns,
                         include_scenes=include_scenes, offset=offset or None, limit=limit)
        return bridge_call(bridge, "song.snapshot", args, timeout=_slow_timeout(bridge))

    @mcp.tool()
    def live_transport_get(include_scales: bool = False) -> Any:
        """Read the transport: playing?, playhead, tempo, signature, loop, record/overdub/punch
        flags, metronome, quantization, scale, undo availability.

        Args:
            include_scales: also return every scale name Live accepts (for
                `live_transport_set(scale_name=...)`).

        Returns:
            `{is_playing, position:{beats,bbs}, start_time:{beats,bbs}, seconds_at_current_tempo,
            tempo, signature:"4/4", loop:{on,start,length,end,start_bbs,end_bbs,length_bbs},
            metronome, record_mode, session_record, session_record_status, arrangement_overdub,
            punch_in, punch_out, session_automation_record, back_to_arranger,
            clip_trigger_quantization:"1 bar", midi_recording_quantization:"none", count_in,
            groove_amount, swing_amount, scale:{name, root_note, root, mode, intervals},
            can_undo, can_redo, can_capture_midi, song_length, link, scales?}`

        Gotcha: `bbs` uses the global signature (arrangement signature changes are ignored).
        """
        return bridge_call(bridge, "transport.get", drop_none(include_scales=include_scales or None))

    @mcp.tool()
    def live_transport_play(position: float | str | None = None, mode: str = "start") -> Any:
        """Start playback — or toggle it like the space bar.

        Args:
            position: optional start point: beats (number) or "bars.beats.sixteenths" string
                ("17.1.1" = bar 17). Playback continues from there.
            mode: "start" (default: from the start marker, like the space bar), "continue"
                (Shift+Space: from where playback last stopped — Live ignores playhead moves
                made while stopped), "selection" (play the arrangement selection), "toggle"
                (stop if playing, else start). Ignored when `position` is given.

        Returns:
            `{is_playing, position:{beats,bbs}, start_time:{beats,bbs}, seconds_at_current_tempo,
            quantized?}` — the requested state (Live starts on its next tick, ~100 ms).

        Gotchas: stopped + `position` moves the start marker there and plays from it; while
        playing, `position` is a jump quantized to the global launch quantization. A position
        behind the end of the song extends the song first (`song_extended: true`). Session
        clips keep playing independently; stop them with `live_transport_stop(stop_clips=true)`.
        """
        cmd = "transport.play"
        if mode not in ("start", "continue", "selection", "toggle"):
            return tool_error("mode must be 'start', 'continue', 'selection' or 'toggle'", cmd=cmd)
        problem = _bad_time(position, "position", cmd)
        if problem:
            return problem
        if mode == "toggle" and position is None:
            return bridge_call(bridge, "transport.toggle")
        if mode == "continue" and position is None:
            return bridge_call(bridge, "transport.continue")
        return bridge_call(bridge, cmd, drop_none(position=position,
                                                  mode=None if mode == "toggle" else mode))

    @mcp.tool()
    def live_transport_stop(stop_clips: bool = False, quantized: bool = False) -> Any:
        """Stop the transport.

        Args:
            stop_clips: also stop all playing session clips (otherwise they resume on play).
            quantized: with `stop_clips`, stop clips at the next launch-quantization boundary
                instead of immediately.

        Returns:
            `{is_playing: false, position:{beats,bbs}, start_time:{...}}`
        """
        return bridge_call(bridge, "transport.stop",
                           {"stop_clips": stop_clips, "quantized": quantized})

    @mcp.tool()
    def live_transport_set_position(
        position: float | str | None = None,
        jump_by: float | None = None,
        set_start_marker: bool | None = None,
    ) -> Any:
        """Move the playhead — absolute (`position`) or relative (`jump_by`).

        Args:
            position: beats (number) or "bars.beats.sixteenths" ("33.1.1" = bar 33, 1-based).
            jump_by: relative move in beats, negative = backwards (exclusive with `position`).
            set_start_marker: also move the start marker (where play starts). Default: only
                while stopped, so a following `live_transport_play()` starts there.

        Returns:
            `{is_playing, position:{beats,bbs}, start_time:{beats,bbs}, seconds_at_current_tempo,
            quantized?}` — `position` is the target (Live moves the playhead on its next tick).

        Gotchas: while playing, Live quantizes the jump (`quantized: true`). A target behind the
        end of the song extends the song (`song_extended: true`; up to 4096 beats behind it).
        Stopped, Live's Continue ignores this move (it resumes where playback stopped) —
        `live_transport_play()` starts from the start marker.
        """
        cmd = "transport.set_position"
        if (position is None) == (jump_by is None):
            return tool_error("pass exactly one of position or jump_by", cmd=cmd)
        problem = _bad_time(position, "position", cmd)
        if problem:
            return problem
        return bridge_call(bridge, cmd, drop_none(position=position, jump_by=jump_by,
                                                  set_start_marker=set_start_marker))

    @mcp.tool()
    def live_transport_set(
        tempo: float | None = None,
        signature: str | None = None,
        metronome: bool | None = None,
        record_mode: bool | None = None,
        session_record: bool | None = None,
        arrangement_overdub: bool | None = None,
        overdub: bool | None = None,
        punch_in: bool | None = None,
        punch_out: bool | None = None,
        session_automation_record: bool | None = None,
        back_to_arranger: bool | None = None,
        loop: bool | None = None,
        link: bool | None = None,
        clip_trigger_quantization: str | int | None = None,
        midi_recording_quantization: str | int | None = None,
        groove_amount: float | None = None,
        swing_amount: float | None = None,
        scale_name: str | None = None,
        root_note: str | int | None = None,
        scale_mode: bool | None = None,
    ) -> Any:
        """Change any combination of song/transport settings in one call (one undo step).

        Args (all optional — only what you pass changes):
            tempo: BPM 20..999.
            signature: time signature "3/4", "6/8", "7/8" (numerator 1..99, denominator 1/2/4/8/16).
            metronome, punch_in, punch_out, loop: on/off.
            record_mode: Arrangement Record button — records EVERY armed track; with Live's
                default "Start Playback with Record" preference it starts the transport at once.
            session_record: Session Record button (records into armed tracks' slots and starts
                the transport).
            arrangement_overdub: MIDI Arrangement Overdub.
            overdub: Live's legacy overdub hook (drives session record without starting
                playback) — prefer the two above.
            session_automation_record: Automation Arm.
            back_to_arranger: false = press "Back to Arrangement" (arrangement plays again).
            link: Ableton Link on/off.
            clip_trigger_quantization: global launch quantization: "none", "8 bars", "4 bars",
                "2 bars", "1 bar", "1/2", "1/2T", "1/4", "1/4T", "1/8", "1/8T", "1/16",
                "1/16T", "1/32" (or 0..13).
            midi_recording_quantization: "none", "1/4", "1/8", "1/8T", "1/8+1/8T", "1/16",
                "1/16T", "1/16+1/16T", "1/32" (or 0..8).
            groove_amount: global groove amount 0..1.3125 (Live's 0-131 %).
            swing_amount: 0..1.
            scale_name: a Live scale ("Major", "Minor", "Dorian", "Mixolydian", "Minor
                Pentatonic", ... — `live_transport_get(include_scales=true)` lists all).
            root_note: key root 0..11 or a name ("C", "F#", "Bb").
            scale_mode: Scale Mode highlighting in clips/devices on/off.

        Returns:
            `{"changed": {<setting>: <value after>, ...}}`

        Gotchas: everything is validated before anything changes, so one bad value changes
        nothing. Recording happens on armed tracks while the transport runs. Loop / punch /
        record / automation-arm switches are applied by Live on its next tick — `changed` shows
        the requested value for those.
        """
        cmd = "transport.set"
        if tempo is not None and not 20.0 <= tempo <= 999.0:
            return tool_error("tempo must be within 20..999 BPM", cmd=cmd)
        for name, value, high in (("groove_amount", groove_amount, 1.3125),
                                  ("swing_amount", swing_amount, 1.0)):
            if value is not None and not 0.0 <= value <= high:
                return tool_error(f"{name} must be within 0..{high:g}", cmd=cmd)
        if signature is not None and "/" not in signature:
            return tool_error("signature must look like '3/4'", cmd=cmd)
        args = drop_none(
            tempo=tempo, signature=signature, metronome=metronome, record_mode=record_mode,
            session_record=session_record, arrangement_overdub=arrangement_overdub,
            overdub=overdub, punch_in=punch_in, punch_out=punch_out,
            session_automation_record=session_automation_record,
            back_to_arranger=back_to_arranger, loop=loop, is_ableton_link_enabled=link,
            clip_trigger_quantization=clip_trigger_quantization,
            midi_recording_quantization=midi_recording_quantization,
            groove_amount=groove_amount, swing_amount=swing_amount, scale_name=scale_name,
            root_note=root_note, scale_mode=scale_mode)
        if not args:
            return tool_error("nothing to change — pass at least one setting", cmd=cmd)
        return bridge_call(bridge, cmd, args)

    @mcp.tool()
    def live_transport_set_loop(
        enabled: bool | None = None,
        start: float | str | None = None,
        length: float | str | None = None,
        end: float | str | None = None,
        start_bar: int | None = None,
        bars: float | None = None,
    ) -> Any:
        """Set the arrangement loop brace (on/off, where, how long).

        Args:
            enabled: loop on/off.
            start: loop start — beats or "bars.beats.sixteenths" ("9.1.1").
            length: loop length — beats, or a 0-based bar duration string ("4.0.0" or "4").
            end: loop end instead of length — beats or "bars.beats.sixteenths".
            start_bar: 1-based bar number instead of `start` (start_bar=9 = "9.1.1").
            bars: number of bars instead of `length` (e.g. 8).

        Returns:
            `{on, start, length, end, start_bbs, end_bbs, length_bbs, song_extended?}` (beats +
            bbs; `on` is the requested state — Live switches the loop on its next tick).

        Example: loop bars 9-16 → `start_bar=9, bars=8, enabled=true`.
        Gotchas: a brace behind the end of the song extends the song (`song_extended: true`;
        works in an empty arrangement, up to 4096 beats behind the end); Live's minimum length
        is 1 beat.
        """
        cmd = "transport.set_loop"
        if start is not None and start_bar is not None:
            return tool_error("pass start or start_bar, not both", cmd=cmd)
        if sum(v is not None for v in (length, end, bars)) > 1:
            return tool_error("pass only one of length, end or bars", cmd=cmd)
        if start_bar is not None and start_bar < 1:
            return tool_error("start_bar is 1-based (>= 1)", cmd=cmd)
        if bars is not None and bars <= 0:
            return tool_error("bars must be > 0", cmd=cmd)
        for name, value in (("start", start), ("length", length), ("end", end)):
            problem = _bad_time(value, name, cmd)
            if problem:
                return problem
        args = drop_none(enabled=enabled, start=start, length=length, end=end,
                         start_bar=start_bar, bars=bars)
        if not args:
            return tool_error("nothing to change — pass enabled, start/start_bar and/or "
                              "length/end/bars", cmd=cmd)
        return bridge_call(bridge, cmd, args)

    @mcp.tool()
    def live_transport_tap_tempo() -> Any:
        """Press Live's Tap Tempo button once.

        Returns:
            `{"tempo": <bpm after>}`

        Gotcha: Live computes the tempo from the real time between taps, so it only changes
        after several calls spaced exactly like the beat — imprecise over the network. If you
        know the BPM, use `live_transport_set(tempo=...)`.
        """
        return bridge_call(bridge, "transport.tap_tempo")

    @mcp.tool()
    def live_transport_undo(steps: int = 1, redo: bool = False) -> Any:
        """Undo (or redo) the last actions, like Cmd/Ctrl+Z.

        Args:
            steps: how many steps, 1..100 (stops early when nothing is left).
            redo: true = redo instead of undo.

        Returns:
            `{undone|redone: [<Live's action names>], count, can_undo, can_redo}`

        Gotcha: every mutating LiveBridge tool call is exactly one undo step; the user's own
        edits are on the same stack, so check `count`/names before undoing more.
        """
        cmd = "transport.redo" if redo else "transport.undo"
        if not 1 <= steps <= 100:
            return tool_error("steps must be within 1..100", cmd=cmd)
        return bridge_call(bridge, cmd, {"steps": steps})

    @mcp.tool()
    def live_transport_stop_all_clips(quantized: bool = True) -> Any:
        """Stop all session clips (the Session "Stop All Clips" button); the transport keeps
        running so the arrangement / other material continues.

        Args:
            quantized: true (default) = at the next global launch-quantization boundary,
                false = immediately.

        Returns:
            `{"stopped": true, "quantized": bool, "is_playing": bool}`
        """
        return bridge_call(bridge, "transport.stop_all_clips", {"quantized": quantized})
