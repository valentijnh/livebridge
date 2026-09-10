"""Automation tools — clip envelopes (read, draw, shapes, clear) and parameter automation state.

How automation works through Live's API:

* Envelopes belong to **session clips**: a clip can automate any parameter of its own track —
  device parameters (also inside racks) and the mixer's volume, pan, sends and track activator.
  Times are clip-local beats (loop with the clip); values are the parameter's internal numbers.
* **Arrangement clips** return None from automation_envelope and only have the envelopes Live
  copied along with them: live_arrangement_duplicate_clip copies them on a track without
  devices, but Live 12.4.5 dropped them all on a track with an instrument (Session view
  focused) — its result reports `envelopes: {source, copied}`. Copied envelopes can be read and
  edited like session envelopes; Live cannot create a new envelope in an arrangement clip.
* **Arrangement track automation** (automation lanes) cannot be drawn through the API.
  live_automation_record records it the way a person does: Arrangement Record + Automation Arm
  while the song plays through the range and the parameter is moved on every tick (works for
  any track incl. returns and the master — "tempo" = the master's Song Tempo). A parameter's
  automation state ("playing"/"overridden") is the only way to see existing lanes.

Addressing: the clip is `track` + `slot` (scene index or name) or `clip` (a clip / clip slot /
arrangement clip path such as "song.tracks[0].clip_slots[2].clip", a clip name, or
"selected"). The parameter is a name ("Filter Freq", "Operator > Filter Freq" to disambiguate),
a mixer alias ("volume", "pan", "send A", "send Reverb", "track on"), an index together with
`device`, or a LOM path ("song.tracks[0].devices[0].parameters[3]",
"song.tracks[0].mixer_device.sends[1]"). Values: internal numbers, `normalized` 0..1, display
strings ("-6 dB", "1.2 kHz", "25L", "50 %") or value items ("Saw").
"""

from __future__ import annotations

from typing import Any

from . import bridge_call, drop_none, tool_error
from ..client import BridgeClient
from .cues import time_error

Ref = int | str
TimeArg = float | str

_MODES = ("step", "linear", "events")
_SHAPES = ("ramp_up", "ramp_down", "sine", "triangle", "saw_up", "saw_down", "square", "random",
           "ramp", "fade_in", "fade_out", "saw", "lfo", "sin", "tri", "noise",
           "sample_and_hold", "s&h", "pulse")


def _clip_error(track: Any, slot: Any, clip: Any, cmd: str) -> dict[str, Any] | None:
    if clip is not None:
        if not isinstance(clip, str) or not clip.strip():
            return tool_error("clip must be a LOM path, a clip name or 'selected'", cmd=cmd)
        return None
    if track is None:
        return tool_error("Say which clip: track + slot (e.g. track='Bass', slot=0) or "
                          "clip='<path|name|selected>'.", cmd=cmd)
    if slot is None:
        return tool_error("slot is required with track (scene index or name) — or pass clip=",
                          cmd=cmd)
    return None


def _address(track: Any, slot: Any, clip: Any) -> dict[str, Any]:
    if clip is not None:
        return {"clip": clip.strip()}
    return drop_none(track=track, slot=slot)


def _param_error(parameter: Any, cmd: str) -> dict[str, Any] | None:
    if parameter is None or (isinstance(parameter, str) and not parameter.strip()):
        return tool_error("parameter is required (name, mixer alias like 'volume', index with "
                          "device, or a path)", cmd=cmd)
    return None


def _length_error(value: Any, name: str, cmd: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return tool_error(f"{name} must be > 0 beats", cmd=cmd) if value <= 0 else None
    if isinstance(value, str) and value.strip():
        return None
    return tool_error(f"{name} must be beats, a note value like '1/8' or '2 bars'", cmd=cmd)


def register(mcp: Any, bridge: BridgeClient) -> None:
    """Register the automation tools."""

    @mcp.tool()
    def live_automation_overview(track: Ref, include_empty: bool = False) -> Any:
        """Everything automation-related on one track in one call: which clips have envelopes
        for which parameters, which parameters are automated/overridden, and whether Live's
        Re-Enable Automation button is lit.

        Args:
            track: Track index, name or path.
            include_empty: Also list clips that have no envelopes.

        Returns:
            {"track": {"name", "path"}, "clip_count",
            "clips": [{"slot" | "arrangement_index", "name", "path",
                       "envelopes": [{"name", "device", "path"}]}],
            "automated": [{"name", "device", "path", "state": "playing"|"overridden"}],
            "song": {"re_enable_automation_enabled", "session_automation_record"}, "note"?}

        Gotchas: `automated` is the only visible trace of arrangement automation (Live's API
        cannot read or write arrangement automation lanes). "overridden" = a manual change
        disabled the automation; fix with live_automation_state(re_enable=True).
        """
        cmd = "automation.overview"
        if isinstance(track, str) and not track.strip():
            return tool_error("track must be an index, a name or a path", cmd=cmd)
        args: dict[str, Any] = {"track": track}
        if include_empty:
            args["include_empty"] = True
        return bridge_call(bridge, cmd, args)

    @mcp.tool()
    def live_automation_list(track: Ref | None = None, slot: Ref | None = None,
                             clip: str | None = None, sample: int = 0,
                             include_available: bool = False, filter: str | None = None,
                             limit: int = 200) -> Any:
        """List the automation envelopes of one clip, and optionally every parameter that could
        be automated from it.

        Args:
            track, slot: Session clip (track index/name/path + scene index/name).
            clip: Instead: a clip / clip slot / arrangement clip path, a clip name or
                "selected".
            sample: Also return N values per envelope sampled evenly over the clip loop (0..256).
            include_available: Also list automatable parameters (devices incl. nested racks,
                mixer volume/pan/sends/track activator) with their paths.
            filter: Substring filter for `available` ("filter", "reverb").
            limit: Max rows in `available` (1..5000).

        Returns:
            {"clip": {"path", "name", "arrangement"?}, "range": [start, end],
            "envelopes": [{"name", "device", "path", "values"?}], "count",
            "available"?: [{"name", "device", "path"}], "available_total"?, "note"?}
        """
        cmd = "automation.list"
        error = _clip_error(track, slot, clip, cmd)
        if error:
            return error
        if not 0 <= sample <= 256:
            return tool_error("sample must be 0..256", cmd=cmd)
        if not 1 <= limit <= 5000:
            return tool_error("limit must be 1..5000", cmd=cmd)
        args = _address(track, slot, clip)
        if sample:
            args["sample"] = sample
        if include_available:
            args.update(drop_none(include_available=True, filter=filter, limit=limit))
        return bridge_call(bridge, cmd, args)

    @mcp.tool()
    def live_automation_get(parameter: Ref, track: Ref | None = None, slot: Ref | None = None,
                            clip: str | None = None, device: Ref | None = None,
                            start: TimeArg | None = None, end: TimeArg | None = None,
                            points: int = 16, step: TimeArg | None = None,
                            display: bool = False, include_events: bool = False) -> Any:
        """Read one clip envelope, sampled at N evenly spaced points or on a beat grid.

        Args:
            parameter: Name, "Device > Param", mixer alias ("volume", "pan", "send A"), index
                (with device) or path.
            track, slot / clip: The clip (see live_automation_list).
            device: Device index/name/path when the parameter name is ambiguous.
            start, end: Window in clip beats or "bars.beats.sixteenths" ("2.1.1" = bar 2 of the
                clip). Default: the clip loop (or its start..end when unlooped).
            points: Number of samples (1..2048) — sample k is at start + k*step.
            step: Sample every `step` instead: beats, "1/16", "1/8T", "1 bar".
            display: Also return display strings ("-6.0 dB").
            include_events: Also return the breakpoints [[time, value]] (Live 12 API), values
                in the parameter's range like `values`; a step border shows as two
                breakpoints at the same time (value before, value after).

        Returns:
            {"clip", "parameter": {"name", "device", "path", "min", "max"}, "state",
            "has_envelope": true, "start", "end", "step", "values": [...], "display"?,
            "events"?} — or {"has_envelope": false, "value", "display_value"} when the clip has
            no envelope for it.
        """
        cmd = "automation.get"
        error = _param_error(parameter, cmd) or _clip_error(track, slot, clip, cmd) or \
            time_error(start, "start", cmd) or time_error(end, "end", cmd) or \
            _length_error(step, "step", cmd)
        if error:
            return error
        if not 1 <= points <= 2048:
            return tool_error("points must be 1..2048", cmd=cmd)
        args = _address(track, slot, clip)
        args.update(drop_none(parameter=parameter, device=device, start=start, end=end,
                              step=step))
        if step is None and points != 16:
            args["points"] = points
        if display:
            args["display"] = True
        if include_events:
            args["include_events"] = True
        return bridge_call(bridge, cmd, args)

    @mcp.tool()
    def live_automation_write(parameter: Ref, points: list[Any], track: Ref | None = None,
                              slot: Ref | None = None, clip: str | None = None,
                              device: Ref | None = None, mode: str = "step",
                              resolution: int = 16, end: TimeArg | None = None,
                              normalized: bool = False, clear: bool = False,
                              extend_clip: bool = False) -> Any:
        """Draw automation into a clip envelope from breakpoints.

        Args:
            parameter: What to automate (name, "Device > Param", "volume", "pan", "send A",
                index with device, or path).
            points: [{"time": 0, "value": "-12 dB"}, {"time": "2.1.1", "value": 0.85}] or
                [[time, value], ...] (max 2048). time = clip beats or "bars.beats.sixteenths";
                value = internal number, display string ("-6 dB", "1.2 kHz", "25L", "50 %"),
                value item ("Saw") or bool.
            track, slot / clip: The clip (session clips only).
            device: Device index/name/path to disambiguate `parameter`.
            mode: "step" (hold each value until the next point), "linear" (ramp between points,
                drawn as `resolution` steps per segment) or "events" (Live 12 breakpoints —
                true ramps, newer API).
            resolution: Steps per segment for mode="linear" (1..256).
            end: Where the last point's value stops (default: clip end).
            normalized: Numbers are 0..1 of the parameter range instead of internal values.
            clear: Delete this parameter's existing envelope first.
            extend_clip: Grow the clip (whole bars) when the points reach past its loop end —
                otherwise that part is stored but never heard.

        Returns:
            {"clip", "parameter": {"name", "device", "path", "min", "max"}, "mode", "created",
            "steps", "range": [start, end], "values": {"min", "max"},
            "check": [[time, value] x8 read back], "clamped"?, "extended_to"?,
            "outside_loop"?: {"clip_range", "written", "note"}}

        Gotchas: envelopes follow the clip loop — `outside_loop` warns when points lie beyond
        it (use extend_clip=true, or write inside the loop). Arrangement clips only accept
        envelopes they already have (an arrangement copy may carry none), else
        "unsupported" — use live_automation_record for arrangement automation. Steps
        overwrite only their own range; other breakpoints stay unless clear=true. Out-of-range
        values are clamped and counted. For LFOs/ramps prefer live_automation_shape.
        """
        cmd = "automation.write"
        error = _param_error(parameter, cmd) or _clip_error(track, slot, clip, cmd) or \
            time_error(end, "end", cmd)
        if error:
            return error
        if not points:
            return tool_error("points must not be empty", cmd=cmd)
        if len(points) > 2048:
            return tool_error("at most 2048 points per call", cmd=cmd)
        for index, point in enumerate(points):
            if isinstance(point, dict):
                if "time" not in point or "value" not in point:
                    return tool_error(f"points[{index}] needs 'time' and 'value'", cmd=cmd)
            elif not (isinstance(point, (list, tuple)) and len(point) == 2):
                return tool_error(f"points[{index}] must be {{time, value}} or [time, value]",
                                  cmd=cmd)
        if mode not in _MODES:
            return tool_error("mode must be step, linear or events", cmd=cmd)
        if not 1 <= resolution <= 256:
            return tool_error("resolution must be 1..256", cmd=cmd)
        args = _address(track, slot, clip)
        args.update(drop_none(parameter=parameter, points=points, device=device, end=end))
        args["mode"] = mode
        if mode == "linear":
            args["resolution"] = resolution
        if normalized:
            args["normalized"] = True
        if clear:
            args["clear"] = True
        if extend_clip:
            args["extend_clip"] = True
        return bridge_call(bridge, cmd, args)

    @mcp.tool()
    def live_automation_shape(parameter: Ref, shape: str = "sine", track: Ref | None = None,
                              slot: Ref | None = None, clip: str | None = None,
                              device: Ref | None = None, start: TimeArg | None = None,
                              end: TimeArg | None = None, low: float | str = 0.0,
                              high: float | str = 1.0, normalized: bool = True,
                              period: TimeArg | None = None, cycles: float | None = None,
                              phase: float = 0.0, step: TimeArg | None = None,
                              duty: float = 0.5, curve: float = 1.0,
                              seed: int | str | None = None, clear: bool = False,
                              mode: str = "step", slots: list[Ref] | None = None,
                              extend_clip: bool = False) -> Any:
        """Write an automation shape into a clip: ramps/fades, LFOs (sine, triangle, saw),
        square/pulse and seeded random (sample & hold) — or one continuous shape across several
        clips of a track (a build-up sweep over scenes 2-5).

        Args:
            parameter, device, track, slot / clip: See live_automation_write.
            shape: ramp_up | ramp_down | sine | triangle | saw_up | saw_down | square | random
                (aliases: fade_in, fade_out, ramp, saw, lfo, pulse, noise, s&h).
            start, end: Range in clip beats or "bars.beats.sixteenths" (default: the clip loop).
            low, high: Value range. By default normalized 0..1 of the parameter's range (0..1 =
                full sweep); with normalized=false internal values; display strings work in both
                ("-24 dB", "0 dB", "200 Hz").
            period: Cycle length for LFO/square shapes: beats, "1/4", "1/8T", "1 bar", "2 bars"
                (default 1 bar).
            cycles: Number of cycles over the range (instead of period).
            phase: Cycle offset 0..1 (0.25 starts a sine at its top).
            step: Resolution (default: period/16 for LFOs, range/128 for ramps, 1/16 note for
                random) — beats or "1/32": the staircase step, or the sampling of curves in
                mode="events".
            duty: Fraction of each square cycle spent at `high` (0..1).
            curve: Exponent on the 0..1 shape (1 linear, 2 slow start, 0.5 fast start).
            seed: Random seed; the seed used is returned so a pattern can be repeated.
            clear: Delete this parameter's existing envelope first.
            mode: "step" (default, a staircase) or "events" — Live 12 breakpoints with linear
                interpolation: a linear ramp becomes 2 breakpoints and sweeps perfectly smooth,
                LFOs are sampled and interpolated (no audible stepping, much smaller). Use
                "events" for filter/volume sweeps.
            slots: Scenes (indices/names) of `track` — ONE continuous shape across those clips
                in order: the range is their loops laid end to end and each clip gets its part
                (e.g. track="Bass", slots=[1,2,3,4], shape="ramp_up"). Not with clip/slot/start/
                end.
            extend_clip: Grow the clip when the range reaches past its loop (single clip).

        Returns:
            Same as live_automation_write plus {"shape", "period"?, "seed"?}; with slots:
            {"shape", "mode", "total_length", "clips": [{"clip", "range", "steps", "values"}]}.

        Gotchas: max 2048 steps/breakpoints per clip — more fails with a hint instead of being
        cut short; LFO cycles must be >= 1/64 beat. Volume's normalized range is the fader
        travel (0.85 = 0 dB), not dB-linear — use display strings for exact dB targets.
        """
        cmd = "automation.shape"
        if slots is not None:
            if not slots:
                return tool_error("slots must list at least one scene", cmd=cmd)
            if track is None:
                return tool_error("slots needs track", cmd=cmd)
            if clip is not None or slot is not None or start is not None or end is not None:
                return tool_error("slots writes across whole clips — do not combine it with "
                                  "clip, slot, start or end", cmd=cmd)
            if extend_clip:
                return tool_error("extend_clip works with a single clip, not slots", cmd=cmd)
            address_error = None
        else:
            address_error = _clip_error(track, slot, clip, cmd)
        error = _param_error(parameter, cmd) or address_error or \
            time_error(start, "start", cmd) or time_error(end, "end", cmd) or \
            _length_error(period, "period", cmd) or _length_error(step, "step", cmd)
        if error:
            return error
        if mode not in ("step", "events"):
            return tool_error("mode must be step or events", cmd=cmd)
        if shape.strip().lower() not in _SHAPES:
            return tool_error("shape must be ramp_up, ramp_down, sine, triangle, saw_up, "
                              "saw_down, square or random", cmd=cmd)
        if period is not None and cycles is not None:
            return tool_error("pass period or cycles, not both", cmd=cmd)
        if cycles is not None and cycles <= 0:
            return tool_error("cycles must be > 0", cmd=cmd)
        if not 0.0 <= duty <= 1.0:
            return tool_error("duty must be 0..1", cmd=cmd)
        if not 0.05 <= curve <= 20.0:
            return tool_error("curve must be 0.05..20", cmd=cmd)
        if normalized:
            for name, value in (("low", low), ("high", high)):
                if isinstance(value, (int, float)) and not 0.0 <= value <= 1.0:
                    return tool_error(f"{name} must be 0..1 with normalized=true (or pass "
                                      "normalized=false / a display string)", cmd=cmd)
        args = {"track": track, "slots": slots} if slots is not None else \
            _address(track, slot, clip)
        args.update(drop_none(parameter=parameter, device=device, start=start, end=end,
                              period=period, cycles=cycles, step=step, seed=seed))
        args.update({"shape": shape.strip().lower(), "low": low, "high": high,
                     "normalized": normalized})
        if mode != "step":
            args["mode"] = mode
        if extend_clip:
            args["extend_clip"] = True
        if phase:
            args["phase"] = phase
        if duty != 0.5:
            args["duty"] = duty
        if curve != 1.0:
            args["curve"] = curve
        if clear:
            args["clear"] = True
        return bridge_call(bridge, cmd, args)

    @mcp.tool()
    def live_automation_copy(parameter: Ref, targets: list[Ref], track: Ref | None = None,
                             slot: Ref | None = None, clip: str | None = None,
                             device: Ref | None = None, shift: float = 0.0, scale: float = 1.0,
                             target_parameter: Ref | None = None, clear: bool = True) -> Any:
        """Copy one parameter's envelope from a clip into other clips — breakpoint for
        breakpoint, optionally shifted or time-stretched (e.g. reuse a filter sweep on scenes
        2-4, or copy the Bass clip's volume ducking onto the Pad clip).

        Args:
            parameter, device: The envelope to copy (name, "Device > Param", "volume", "send A",
                index with device, or path).
            targets: Destination clips: scene indices/names (clips on the source track) and/or
                clip paths / clip names (any track).
            track, slot / clip: The source clip.
            shift: Move the copy by this many beats (may be negative).
            scale: Stretch time by this factor (2 = half speed, 0.5 = double speed).
            target_parameter: The parameter on the target clips' track (default: the same
                parameter, or the one with the same name on another track).
            clear: Empty the target envelope first (default true).

        Returns:
            {"source": {"clip", "parameter", "events"}, "copied": [{"clip", "parameter",
            "events", "range", "created", "outside_loop"?}], "count"}

        Gotchas: needs Live 12's breakpoint API. Arrangement clips only accept envelopes they
        already have.
        """
        cmd = "automation.copy"
        error = _param_error(parameter, cmd) or _clip_error(track, slot, clip, cmd)
        if error:
            return error
        if not targets:
            return tool_error("targets must list at least one slot or clip", cmd=cmd)
        if len(targets) > 64:
            return tool_error("at most 64 targets per call", cmd=cmd)
        if not 0.01 <= scale <= 100:
            return tool_error("scale must be 0.01..100", cmd=cmd)
        args = _address(track, slot, clip)
        if clip is not None and track is not None:
            args["track"] = track
        args.update(drop_none(parameter=parameter, targets=targets, device=device,
                              target_parameter=target_parameter))
        if shift:
            args["shift"] = shift
        if scale != 1.0:
            args["scale"] = scale
        if not clear:
            args["clear"] = False
        return bridge_call(bridge, cmd, args)

    @mcp.tool()
    def live_automation_record(parameter: Ref | None = None, track: Ref | None = None,
                               device: Ref | None = None, points: list[Any] | None = None,
                               shape: str | None = None, start: TimeArg | None = None,
                               end: TimeArg | None = None, low: float | str = 0.0,
                               high: float | str = 1.0, normalized: bool | None = None,
                               period: TimeArg | None = None, cycles: float | None = None,
                               phase: float = 0.0, duty: float = 0.5, curve: float = 1.0,
                               seed: int | str | None = None, mode: str = "linear",
                               preroll: float = 1.0, action: str = "start") -> Any:
        """Record real ARRANGEMENT automation (track automation lanes) — tempo ramps, master
        filter sweeps, return-track sends, long builds. Live's API cannot draw lanes, so this
        does it like a person: Arrangement Record + Automation Arm on, playback from `preroll`
        beats before `start`, the parameter moved along your curve on every tick, stop at
        `end`, then loop / punch / Automation Arm / armed tracks / playhead are restored.

        Args:
            parameter, device, track: What to automate — any track incl. returns and the master
                ("tempo" = the master's Song Tempo, "volume", "pan", "send A", "crossfader",
                device parameters by name/index/path).
            points: [[song_time, value], ...] or [{"time", "value"}] — SONG beats or
                "bars.beats.sixteenths" ("17.1.1"); values as in live_automation_write
                ("-6 dB", "1.2 kHz", 128 for tempo). Or instead:
            shape: ramp_up, ramp_down, sine, triangle, saw_up, saw_down, square, random over
                start..end, with low/high/normalized/period/cycles/phase/duty/curve/seed as in
                live_automation_shape (LFO cycles >= 1/4 beat).
            start, end: The range (song beats or "bars.beats.sixteenths"); default for points:
                first..last point.
            normalized: Numbers are 0..1 of the parameter range (default: true for shape
                low/high, false for points).
            mode: How points connect: "linear" (default) or "step".
            preroll: Beats of playback before `start` (0..16).
            action: "start" (default), "status" (progress of the running/last pass) or "stop"
                (abort the running pass).

        Returns:
            start: {"id", "phase", "range", "parameter", "track", "points", "expected_seconds",
            "note"} — the pass runs in REAL TIME: call again with action="status" until
            "finished" is true ({"phase": "done", "writes", "recorded": [from, to],
            "state_after": "playing", "restored": {...}}).

        Gotchas: needs a stopped transport, one pass at a time; armed tracks are disarmed
        during the pass (so no clips get recorded) and re-armed after. Resolution is Live's
        ~100 ms tick (≈0.2 beat at 120 BPM): ramps are smooth, steps may land a tick late.
        Existing automation of the parameter inside the range is replaced; the lane cannot be
        read back through the API. For clip-level automation use live_automation_write/shape.
        """
        if action == "status":
            return bridge_call(bridge, "automation.record_status", {})
        cmd = "automation.record"
        if action == "stop":
            return bridge_call(bridge, cmd, {"action": "stop"})
        if action != "start":
            return tool_error("action must be start, status or stop", cmd=cmd)
        error = _param_error(parameter, cmd) or time_error(start, "start", cmd) or \
            time_error(end, "end", cmd) or _length_error(period, "period", cmd)
        if error:
            return error
        if track is None and not (isinstance(parameter, str)
                                  and parameter.strip().startswith("song.")):
            return tool_error("track is required (or pass the parameter as a LOM path)",
                              cmd=cmd)
        if (points is None) == (shape is None):
            return tool_error("pass points or a shape (not both)", cmd=cmd)
        if shape is not None and shape.strip().lower() not in _SHAPES:
            return tool_error("shape must be ramp_up, ramp_down, sine, triangle, saw_up, "
                              "saw_down, square or random", cmd=cmd)
        if shape is not None and (start is None or end is None):
            return tool_error("a shape needs start and end", cmd=cmd)
        if points is not None and not points:
            return tool_error("points must not be empty", cmd=cmd)
        if mode not in ("linear", "step"):
            return tool_error("mode must be linear or step", cmd=cmd)
        if not 0.0 <= preroll <= 16.0:
            return tool_error("preroll must be 0..16 beats", cmd=cmd)
        if period is not None and cycles is not None:
            return tool_error("pass period or cycles, not both", cmd=cmd)
        args = drop_none(parameter=parameter, track=track, device=device, points=points,
                         start=start, end=end, normalized=normalized)
        if shape is not None:
            args.update(drop_none(shape=shape.strip().lower(), low=low, high=high,
                                  period=period, cycles=cycles, seed=seed))
            if phase:
                args["phase"] = phase
            if duty != 0.5:
                args["duty"] = duty
            if curve != 1.0:
                args["curve"] = curve
        if mode != "linear":
            args["mode"] = mode
        if preroll != 1.0:
            args["preroll"] = preroll
        return bridge_call(bridge, cmd, args)

    @mcp.tool()
    def live_automation_clear(track: Ref | None = None, slot: Ref | None = None,
                              clip: str | None = None, parameter: Ref | None = None,
                              device: Ref | None = None, all: bool = False,
                              start: TimeArg | None = None, end: TimeArg | None = None) -> Any:
        """Remove automation from a clip: one parameter's envelope, a time range of it, or every
        envelope of the clip.

        Args:
            track, slot / clip: The clip.
            parameter, device: The envelope to clear (see live_automation_write).
            all: Clear every envelope of the clip (omit parameter).
            start, end: With parameter: only delete the breakpoints in this range (Live 12 API).

        Returns:
            {"clip", "cleared": [{"name", "device", "path", "range"?}], "count", "note"?}.
        """
        cmd = "automation.clear"
        error = _clip_error(track, slot, clip, cmd) or time_error(start, "start", cmd) or \
            time_error(end, "end", cmd)
        if error:
            return error
        if all and parameter is not None:
            return tool_error("pass parameter or all=true, not both", cmd=cmd)
        if not all and parameter is None:
            return tool_error("say which envelope (parameter=...) or pass all=true", cmd=cmd)
        if all and (start is not None or end is not None):
            return tool_error("start/end only work with a single parameter", cmd=cmd)
        args = _address(track, slot, clip)
        if all:
            args["all"] = True
        else:
            args.update(drop_none(parameter=parameter, device=device, start=start, end=end))
        return bridge_call(bridge, cmd, args)

    @mcp.tool()
    def live_automation_state(track: Ref | None = None, parameter: Ref | None = None,
                              device: Ref | None = None, include_all: bool = False,
                              re_enable: bool = False) -> Any:
        """Read automation states (none / playing / overridden) — or press Re-Enable Automation
        for one parameter, one track or the whole song.

        Args:
            track: Limit to one track (index, name or path).
            parameter, device: One parameter (needs track unless parameter is a path).
            include_all: With track: list every parameter, not only automated ones.
            re_enable: Re-enable overridden automation: the parameter, every overridden
                parameter of the track, or (neither given) the whole song.

        Returns:
            read: {"song": {"re_enable_automation_enabled", "session_automation_record"},
            "parameter"?: {"name", "device", "path", "state", "value", "display"},
            "track"?: {"name", "automated": [...]}, "tracks"?: [{"name", "automated"}]}
            re_enable: {"scope": "parameter"|"track"|"song", "re_enabled"?: [...], "song"}.

        Gotchas: "playing" = follows automation (arrangement lane or a playing clip envelope);
        "overridden" = moved by hand, automation is paused until re-enabled.
        """
        if isinstance(parameter, str) and not parameter.strip():
            return tool_error("parameter must not be empty", cmd="automation.state")
        if re_enable:
            if include_all:
                return tool_error("include_all only applies to reading", cmd="automation.re_enable")
            return bridge_call(bridge, "automation.re_enable",
                               drop_none(track=track, parameter=parameter, device=device))
        args = drop_none(track=track, parameter=parameter, device=device)
        if include_all:
            args["include_all"] = True
        return bridge_call(bridge, "automation.state", args)
