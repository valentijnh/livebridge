"""Recording tools: arm tracks, record into session clip slots, record the arrangement, record
settings (metronome, overdub, punch in/out, MIDI record quantization, automation arm), stop,
status, Capture MIDI, and resampling / bouncing a section onto an audio track (Live's API has
no export, freeze or bounce).

Prerequisites outside Live's API (tell the user when a result's `notes` mention them):
    * audio recording needs an audio interface input configured in Live → Preferences → Audio,
      and the track input set to "Ext. In" + the right channel (live_routing_set);
    * MIDI recording needs a MIDI input ("All Ins", a controller or "Computer Keyboard") with
      Track enabled in Preferences → Link, Tempo & MIDI;
    * count-in length, exclusive arm and "start transport with record" are Live preferences
      (read-only here).
"""

from __future__ import annotations

import time
from typing import Any

from . import bridge_call, drop_none, tool_error
from ..client import BridgeClient
from .routing import check_monitoring
from .tracks import check_track

LAUNCH_QUANTIZATION = ("global", "none", "8 bars", "4 bars", "2 bars", "1 bar", "1/2", "1/2T",
                       "1/4", "1/4T", "1/8", "1/8T", "1/16", "1/16T", "1/32")
MIDI_QUANTIZATION = ("none", "1/4", "1/8", "1/8T", "1/8+1/8T", "1/16", "1/16T", "1/16+1/16T",
                     "1/32")
#: Live starts a clip-slot recording on its next tick (~100 ms): wait this long before reading
#: the recording state back.
SETTLE_SECONDS = 0.3
_STATUS_KEYS = ("is_playing", "session_record", "session_record_status", "is_counting_in",
                "recording_slots")
#: live_record_resample(wait=true) waits at most this long for a pass (the bridge's longest
#: request timeout is 120 s; longer sections are started and then polled with action="status").
RESAMPLE_WAIT_LIMIT = 110.0
#: Seconds between two status polls while waiting for a resample pass.
RESAMPLE_POLL = 0.5


def _positive(value: float | None, label: str, cmd: str) -> dict[str, Any] | None:
    if value is not None and value <= 0:
        return tool_error(f"{label} must be > 0", cmd=cmd)
    return None


def register(mcp: Any, bridge: BridgeClient) -> None:
    """Register the recording tools on the MCP app."""

    @mcp.tool()
    def live_record_status() -> Any:
        """Everything about recording right now, in one call.

        Returns:
            {is_playing, record_mode (Arrangement Record button), session_record,
            session_record_status "off"|"on"|"transition", arrangement_overdub, automation_arm,
            punch_in, punch_out, punch_region {start, end, loop_on} (= loop brace, beats),
            metronome, count_in (preference), is_counting_in, midi_quantization,
            exclusive_arm (preference), can_capture_midi, current_song_time,
            armed_tracks [{path, name, type, monitoring, input, input_level: {level, left?,
            right?}}], recording_slots [{track, slot, path, clip, recording}]}.

        Gotcha: `input_level` is one momentary meter reading (0..1) of what reaches each armed
        track's input — 0.0 means no signal (nothing plugged in, wrong input, or silence);
        read it again while the user plays before recording.
        """
        return bridge_call(bridge, "record.status")

    @mcp.tool()
    def live_record_arm(
        track: int | str | list[int | str],
        arm: bool | str = True,
        exclusive: bool = False,
        monitoring: str | None = None,
    ) -> Any:
        """Arm (or disarm) one or several tracks for recording, optionally setting monitoring.

        Args:
            track: A track (index, name, "selected", path), a LIST of tracks (multi-track
                recording), or "all" (every armable track).
            arm: true (default), false or "toggle".
            exclusive: Disarm every other track first.
            monitoring: "auto" (Live default: hear the input while armed), "in" (always) or
                "off" (record without hearing the input, e.g. when resampling).

        Returns:
            {"changed": [names], "armed_tracks": [{path, name, monitoring, input}],
            "notes": [warnings — "No Input", audio interface needed, Live's Exclusive Arm
            preference disarmed a track, ...]}.

        Gotchas:
            Return, master and group tracks cannot be armed. If Live's "Exclusive Arm"
            preference is on, Live may keep only one track armed — check `notes`.
        """
        cmd = "record.arm"
        error = check_track(track, cmd, allow_list=True) or check_monitoring(monitoring, cmd)
        if error:
            return error
        return bridge_call(bridge, cmd, drop_none(
            track=track, arm=arm, exclusive=exclusive or None,
            monitoring=monitoring.strip().lower() if monitoring else None))

    @mcp.tool()
    def live_record_session(
        track: int | str | None = None,
        slot: int | str | None = None,
        length_bars: float | None = None,
        length_beats: float | None = None,
        launch_quantization: str | int | None = None,
        exclusive: bool = False,
        monitoring: str | None = None,
        overwrite: bool = False,
        method: str = "slot",
    ) -> Any:
        """Record a new clip in a Session View slot: arms the track, selects the slot, fires it.

        Args:
            track: MIDI or audio track to record on (default: the selected track).
            slot: Slot index (0-based) or scene name. Default: the first empty slot from the
                selected scene down (a scene is added when every slot is full).
            length_bars / length_beats: Fixed length — recording stops automatically after it
                and the new clip keeps looping (bars follow the song's time signature). Omit to
                record until live_record_stop.
            launch_quantization: When recording starts: "none", "1 bar", "2 bars", "1/4", ...
                Default = the song's global quantization.
            exclusive: Disarm the other tracks first.
            monitoring: "in" / "auto" / "off" for the recording track.
            overwrite: Delete the clip already in the slot first (otherwise that is an error,
                because firing a full slot would play it instead of recording).
            method: "slot" (default) = fire exactly that slot; "trigger" = Live's Session
                Record button: records into the selected scene on EVERY armed track.

        Returns:
            {track, slot (path), slot_index, method, record_length_beats, count_in, fired,
            was_playing, status: {is_playing, session_record, session_record_status,
            is_counting_in, recording_slots} (read ~0.3 s after firing), notes?}.

        Gotchas:
            Starts the transport if it is stopped (after Live's count-in preference). Needs a
            working input (see notes). Stop with live_record_stop. The new clip ends up at the
            returned slot path and is named "<track name> <n>". method="trigger" records on
            EVERY armed track.
        """
        cmd = "record.session"
        method = method.strip().lower()
        if method not in ("slot", "trigger"):
            return tool_error("method must be 'slot' or 'trigger'", cmd=cmd)
        if length_bars is not None and length_beats is not None:
            return tool_error("pass length_bars or length_beats, not both", cmd=cmd)
        error = (check_track(track, cmd) if track is not None else None) or \
            _positive(length_bars, "length_bars", cmd) or \
            _positive(length_beats, "length_beats", cmd) or check_monitoring(monitoring, cmd)
        if error:
            return error
        if isinstance(launch_quantization, str) and \
                launch_quantization.strip() not in LAUNCH_QUANTIZATION and \
                launch_quantization.strip().lower() not in [q.lower() for q in
                                                            LAUNCH_QUANTIZATION]:
            return tool_error(f"launch_quantization must be one of "
                              f"{', '.join(LAUNCH_QUANTIZATION)}", cmd=cmd)
        result = bridge_call(bridge, cmd, drop_none(
            track=track, slot=slot, length_bars=length_bars, length_beats=length_beats,
            launch_quantization=None if launch_quantization in (None, "global")
            else launch_quantization,
            exclusive=exclusive or None,
            monitoring=monitoring.strip().lower() if monitoring else None,
            overwrite=overwrite or None, method=method))
        if isinstance(result, dict) and "error" not in result:
            # Live starts the recording on its next tick: read the real state back.
            time.sleep(SETTLE_SECONDS)
            status = bridge_call(bridge, "record.status")
            if isinstance(status, dict) and "error" not in status:
                result["status"] = {key: status.get(key) for key in _STATUS_KEYS}
        return result

    @mcp.tool()
    def live_record_arrangement(
        start: bool = True,
        time: float | str | None = None,
        overdub: bool | None = None,
        punch_in: bool | None = None,
        punch_out: bool | None = None,
    ) -> Any:
        """Start (or stop) Arrangement recording on all armed tracks.

        Args:
            start: true (default) = press Arrangement Record and start playback if stopped;
                false = release Record (playback continues).
            time: Optional start position in beats or "bars.beats.sixteenths" ("17.1.1")
                (moves the start marker; while playing it jumps the playhead).
            overdub: Optional MIDI Arrangement Overdub on/off (keep existing MIDI notes).
            punch_in / punch_out: Optional — only record inside the loop brace (set the region
                with live_record_settings punch_start/punch_end).

        Returns:
            live_record_status after the change (+ notes when nothing is armed).

        Gotchas:
            Arm tracks first (live_record_arm). Recording replaces material on armed tracks
            unless MIDI overdub is on. Count-in follows Live's preference.
        """
        if isinstance(time, (int, float)) and time < 0:
            return tool_error("time must be >= 0 beats", cmd="record.arrangement")
        return bridge_call(bridge, "record.arrangement", drop_none(
            start=start, time=time, overdub=overdub, punch_in=punch_in, punch_out=punch_out))

    @mcp.tool()
    def live_record_stop(
        stop_transport: bool = False,
        stop_clips: bool = False,
        disarm: bool = False,
    ) -> Any:
        """Stop all recording (Arrangement Record and Session Record off).

        Args:
            stop_transport: Also stop playback.
            stop_clips: Also stop the clips of tracks that were recording (by default freshly
                recorded session clips keep playing, like in Live).
            disarm: Also disarm every track.

        Returns:
            live_record_status after stopping, plus `stopped` (what was done).
        """
        return bridge_call(bridge, "record.stop", {"stop_transport": stop_transport,
                                                   "stop_clips": stop_clips, "disarm": disarm})

    @mcp.tool()
    def live_record_settings(
        metronome: bool | str | None = None,
        overdub: bool | str | None = None,
        punch_in: bool | str | None = None,
        punch_out: bool | str | None = None,
        punch_start: float | str | None = None,
        punch_end: float | str | None = None,
        punch_from_loop: bool = False,
        midi_quantization: str | int | None = None,
        automation_arm: bool | str | None = None,
    ) -> Any:
        """Recording switches: metronome, MIDI overdub, punch in/out (+ region), record
        quantization, automation arm. All optional; combine freely.

        Args:
            metronome: true / false / "toggle".
            overdub: MIDI Arrangement Overdub — true / false / "toggle".
            punch_in / punch_out: true / false / "toggle".
            punch_start / punch_end: Punch region in beats or "bars.beats.sixteenths" ("9.1.1").
                Live's punch region IS the loop
                brace, so this moves the loop start/length (loop on/off is not changed).
            punch_from_loop: true = turn punch in AND out on, using the current loop brace.
            midi_quantization: Record quantization — "none", "1/4", "1/8", "1/8T",
                "1/8+1/8T", "1/16", "1/16T", "1/16+1/16T", "1/32".
            automation_arm: Automation Arm button — true / false / "toggle".

        Returns:
            live_record_status after the change, plus `changed`.

        Gotchas:
            Count-in, exclusive arm and "start transport with record" are Live preferences and
            cannot be changed here.
        """
        cmd = "record.settings"
        if isinstance(midi_quantization, str) and midi_quantization.strip().lower() not in \
                [q.lower() for q in MIDI_QUANTIZATION]:
            return tool_error(f"midi_quantization must be one of {', '.join(MIDI_QUANTIZATION)}",
                              cmd=cmd)
        for label, value in (("punch_start", punch_start), ("punch_end", punch_end)):
            if isinstance(value, (int, float)) and value < 0:
                return tool_error(f"{label} must be >= 0 beats", cmd=cmd)
        if isinstance(punch_start, (int, float)) and isinstance(punch_end, (int, float)) \
                and punch_end <= punch_start:
            return tool_error("punch_end must be after punch_start", cmd=cmd)
        args = drop_none(metronome=metronome, overdub=overdub, punch_in=punch_in,
                         punch_out=punch_out, punch_start=punch_start, punch_end=punch_end,
                         punch_from_loop=punch_from_loop or None,
                         midi_quantization=midi_quantization, automation_arm=automation_arm)
        if not args:
            return tool_error("nothing to change: pass metronome, overdub, punch_in, punch_out, "
                              "punch_start/punch_end, punch_from_loop, midi_quantization or "
                              "automation_arm", cmd=cmd)
        return bridge_call(bridge, cmd, args)

    @mcp.tool()
    def live_record_resample(
        start: float | str | None = None,
        end: float | str | None = None,
        bars: float | None = None,
        length: float | str | None = None,
        source: int | str = "master",
        track: int | str | None = None,
        name: str | None = None,
        channel: str | None = None,
        preroll: float = 1.0,
        wait: bool = True,
        action: str = "start",
    ) -> Any:
        """Resample / bounce a section of the song to audio — the stand-in for export, freeze
        and "bounce to track", which Live's API does not have. Records `start`..`end` of the
        master mix (or one track) onto an audio track in real time and puts everything back.

        Args:
            start: Section start — beats or "bars.beats.sixteenths" ("17.1.1").
            end / bars / length: Section end, OR its length in bars, OR a duration ("8.0.0").
                Exactly one.
            source: "master" (default: the whole mix via the "Resampling" input) or a track
                (index, name, path) to bounce on its own.
            track: Existing audio track to record onto (its material in the section is
                replaced). Default: a new audio track at the end, named `name` or
                "Resample <start>-<end>".
            name: Name for the new track.
            channel: Input tap for a track source: "Post FX", "Pre FX", "Post Mixer".
            preroll: Beats played before `start` (0..16) so reverb/delay tails lead in.
            wait: Block until the pass is finished and return the result (sections up to ~110
                s; longer ones return at once — then poll with action="status").
            action: "start" (default), "status" (progress of the running/last pass) or "stop"
                (abort it).

        Returns:
            {"id", "phase" (done|aborted|failed when finished), "finished", "source",
            "track": {name, path, created, deleted?}, "range": {start, end, start_bbs, end_bbs},
            "progress", "clips": [{name, path, start, end, file_path}], "restored", "error"?,
            "expected_seconds"?}.

        Gotchas: plays the section in REAL TIME through the speakers (16 bars at 120 BPM ≈ 32 s)
        and needs Live's audio engine (an output device). Needs a stopped transport; one pass
        at a time. Punch-In/Out on the loop brace make Live record exactly the section; loop,
        punch, automation arm, other tracks' arm, routing and playhead are restored. A new
        track that got no clip is deleted again. The file lands in the set's Samples/Recorded
        folder (unsaved set: Live's temp folder) — save the set to keep it.
        """
        cmd = "record.resample"
        action = action.strip().lower()
        if action == "status":
            return bridge_call(bridge, "record.resample_status", {})
        if action == "stop":
            return bridge_call(bridge, cmd, {"action": "stop"})
        if action != "start":
            return tool_error("action must be start, status or stop", cmd=cmd)
        if start is None:
            return tool_error("start is required (beats or 'bars.beats.sixteenths')", cmd=cmd)
        if sum(1 for value in (end, bars, length) if value is not None) != 1:
            return tool_error("pass exactly one of end, bars or length", cmd=cmd)
        for label, value in (("start", start), ("end", end)):
            if isinstance(value, (int, float)) and value < 0:
                return tool_error(f"{label} must be >= 0 beats", cmd=cmd)
        if bars is not None and bars <= 0:
            return tool_error("bars must be > 0", cmd=cmd)
        if not 0.0 <= preroll <= 16.0:
            return tool_error("preroll must be 0..16 beats", cmd=cmd)
        args = drop_none(start=start, end=end, bars=bars, length=length, track=track,
                         name=name, channel=channel)
        if source != "master":
            args["source"] = source
        if preroll != 1.0:
            args["preroll"] = preroll
        started = bridge_call(bridge, cmd, args)
        if not wait or not isinstance(started, dict) or "error" in started:
            return started
        expected = float(started.get("expected_seconds") or 0.0)
        if expected > RESAMPLE_WAIT_LIMIT:
            started["note"] = (f"the pass takes ~{expected:.0f} s — poll with "
                               "action='status' until finished")
            return started
        deadline = time.monotonic() + expected + 15.0
        status: Any = started
        while time.monotonic() < deadline:
            time.sleep(RESAMPLE_POLL)
            status = bridge_call(bridge, "record.resample_status", {})
            if not isinstance(status, dict) or "error" in status or status.get("finished"):
                return status
        if isinstance(status, dict):
            status["note"] = "still running — poll with action='status'"
        return status

    @mcp.tool()
    def live_record_capture_midi(destination: str = "auto") -> Any:
        """Capture MIDI: turn what was just played on an armed/monitored MIDI track into a clip.

        Args:
            destination: "auto" (Session or Arrangement, whichever view is visible),
                "session" or "arrangement".

        Returns:
            {"captured": true, "destination": ...}.

        Gotchas:
            Fails with invalid_state when Live has nothing buffered (play some MIDI first).
        """
        destination = destination.strip().lower()
        if destination not in ("auto", "session", "arrangement"):
            return tool_error("destination must be 'auto', 'session' or 'arrangement'",
                              cmd="record.capture_midi")
        return bridge_call(bridge, "record.capture_midi", {"destination": destination})
