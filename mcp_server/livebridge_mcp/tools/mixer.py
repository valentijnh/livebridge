"""Mixer tools: volume, pan, sends, track activator, crossfader, master/cue volume, batch
changes and reset.

Level formats (volume, sends, cue volume):
    * a number 0..1 = Live's raw parameter value (track volume 0.85 = 0 dB, 1.0 = +6 dB;
      send 1.0 = 0 dB);
    * "-6 dB", "0dB", "-inf" = absolute decibels;
    * "+3" / "-2.5" (explicit sign, no unit) = relative change in dB from the current level;
    * "50%" = 0.5 raw.
Pan: -1..1, "C", "L20" / "20L", "R50", "left", "right" (Live displays 50L..50R).
Crossfade assign: "A", "B", "none". Crossfader position: -1..1, "A", "B", "center", "25A".
"""

from __future__ import annotations

import time
from typing import Any

from . import bridge_call, drop_none, tool_error
from ..client import BridgeClient
from .tracks import check_detail, check_track

SET_KEYS = ("volume", "pan", "sends", "activator", "crossfade", "mute", "solo",
            "panning_mode", "cue_volume", "crossfader")
RESET_KEYS = ("volume", "pan", "sends", "activator", "crossfade", "panning_mode", "mute",
              "solo")
#: Meter positions at or above this are "at the top of the meter" (possible clipping).
HOT_METER = 0.99
#: Below this a track counts as silent.
SILENT_METER = 0.001
MAX_METER_SAMPLES = 100


def _rows_of(snapshot: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    for group in ("tracks", "returns"):
        for row in snapshot.get(group) or []:
            rows.append((group, row))
    if snapshot.get("master"):
        rows.append(("master", snapshot["master"]))
    return rows


def aggregate_meters(snapshots: list[dict[str, Any]], seconds: float) -> dict[str, Any]:
    """Peak / average per track over several ``mixer.meters`` snapshots."""
    stats: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []
    cpu_avg: list[float] = []
    cpu_peak = 0.0
    playing = False
    for snap in snapshots:
        playing = playing or bool(snap.get("playing"))
        cpu = snap.get("cpu") or {}
        if cpu.get("average") is not None:
            cpu_avg.append(float(cpu["average"]))
        if cpu.get("peak") is not None:
            cpu_peak = max(cpu_peak, float(cpu["peak"]))
        for group, row in _rows_of(snap):
            key = (group, str(row.get("name")))
            if key not in stats:
                stats[key] = {"name": row.get("name"), "type": row.get("type"), "peak": 0.0,
                              "sum": 0.0, "n": 0, "input_peak": None}
                order.append(key)
            entry = stats[key]
            peak = row.get("peak") or 0.0
            entry["peak"] = max(entry["peak"], float(peak))
            entry["sum"] += float(row.get("level") or 0.0)
            entry["n"] += 1
            level_in = (row.get("input") or {})
            values = [v for v in (level_in.get("level"), level_in.get("left"),
                                  level_in.get("right")) if v is not None]
            if values:
                entry["input_peak"] = max(entry["input_peak"] or 0.0, max(values))
    result: dict[str, Any] = {"seconds": round(seconds, 2), "samples": len(snapshots),
                              "playing": playing, "tracks": [], "returns": []}
    hot: list[str] = []
    silent: list[str] = []
    for key in order:
        entry = stats[key]
        row: dict[str, Any] = {"name": entry["name"], "type": entry["type"],
                               "peak": round(entry["peak"], 4),
                               "avg": round(entry["sum"] / max(entry["n"], 1), 4)}
        if entry["input_peak"] is not None:
            row["input_peak"] = round(entry["input_peak"], 4)
        if entry["peak"] >= HOT_METER:
            row["hot"] = True
            hot.append(str(entry["name"]))
        if entry["peak"] < SILENT_METER:
            row["silent"] = True
            silent.append(str(entry["name"]))
        if key[0] == "master":
            result["master"] = row
        else:
            result[key[0]].append(row)
    if cpu_avg:
        result["cpu"] = {"average": round(sum(cpu_avg) / len(cpu_avg), 3),
                         "peak": round(cpu_peak, 3)}
    warnings = []
    if not playing:
        warnings.append("the transport was stopped — meters only move while audio plays or "
                        "a track monitors its input")
    if hot:
        warnings.append(f"at the top of the meter (possible clipping): {', '.join(hot)}")
    if playing and silent:
        warnings.append(f"no signal: {', '.join(silent[:12])}")
    if warnings:
        result["warnings"] = warnings
    return result


def _check_number(value: Any, low: float, high: float, label: str, cmd: str) -> Any:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and \
            not low <= float(value) <= high:
        hint = " — use a string like '-6 dB' for decibels" if label in (
            "volume", "cue_volume", "send") else ""
        return tool_error(f"{label} {value} is outside {low}..{high}{hint}", cmd=cmd)
    return None


def register(mcp: Any, bridge: BridgeClient) -> None:
    """Register the mixer tools on the MCP app."""

    @mcp.tool()
    def live_mixer_get(
        track: int | str | list[int | str] | None = None,
        detail: str = "summary",
    ) -> Any:
        """Read mixer settings in dB: volume, pan, sends, mute/solo, activator, crossfade.

        Args:
            track: One track (index, name, return letter "A", "master", path), a list, or
                omitted = the whole mixer (tracks, returns and master).
            detail: "minimal" (just numbers: volume dB, pan, send dBs — best for a whole-set
                overview), "summary" (default: value + dB + Live's display text per control)
                or "full" (+ panning mode, split-stereo pan, parameter paths for automation).

        Returns:
            One track: {name, path, type, volume: {value, db, display}, pan: {value, display},
            mute, solo, active, sends: [{index, letter, return, value, db, display}],
            crossfade: "A"|"none"|"B"}; the master adds cue_volume and crossfader.
            No track: {"tracks": [...], "returns": [...], "master": {...}}.

        Gotchas:
            `db` is "-inf" when fully down. Track volume 0.85 = 0 dB (max +6 dB); sends top out
            at 0 dB.
        """
        cmd = "mixer.get"
        error = check_detail(detail, cmd)
        if error:
            return error
        if track is not None:
            error = check_track(track, cmd, allow_list=True)
            if error:
                return error
        return bridge_call(bridge, cmd, drop_none(track=track, detail=detail))

    @mcp.tool()
    def live_mixer_set(
        track: int | str,
        volume: float | str | None = None,
        pan: float | str | None = None,
        sends: dict[str, Any] | list[Any] | None = None,
        activator: bool | str | None = None,
        crossfade: str | int | None = None,
        mute: bool | str | None = None,
        solo: bool | str | None = None,
        panning_mode: str | None = None,
    ) -> Any:
        """Set one track's mixer: volume, pan, sends, on/off, crossfade assign, mute/solo.

        Args:
            track: Index, name, return letter ("A"), "master", "selected" or path.
            volume: 0..1 raw (0.85 = 0 dB), "-6 dB", "+3" / "-2" (relative dB), "-inf".
            pan: -1..1, "C", "L20", "R35", "left", "right".
            sends: {"A": "-12 dB", "Reverb": 0.5, "1": "+3"} — keys are send letters, indices
                or (part of) the return track's name; or a list of levels by send index.
            activator: The track's on/off (Speaker) switch: true, false or "toggle".
            crossfade: Crossfader assignment "A", "B" or "none".
            mute / solo: true, false or "toggle".
            panning_mode: "stereo" or "split_stereo".

        Returns:
            The track's mixer (live_mixer_get shape, with dB) plus `changed`.

        Gotchas:
            Values are clamped to Live's range. For the master's cue volume / crossfader use
            live_mixer_master; several tracks at once: live_mixer_set_many.
        """
        cmd = "mixer.set"
        error = check_track(track, cmd) or _check_number(volume, 0, 1, "volume", cmd) or \
            _check_number(pan, -1, 1, "pan", cmd)
        if error:
            return error
        values = drop_none(volume=volume, pan=pan, sends=sends, activator=activator,
                           crossfade=crossfade, mute=mute, solo=solo, panning_mode=panning_mode)
        if not values:
            return tool_error("nothing to change: pass volume, pan, sends, activator, "
                              "crossfade, mute, solo or panning_mode", cmd=cmd)
        if isinstance(crossfade, str) and crossfade.strip().lower() not in ("a", "b", "none",
                                                                            "off"):
            return tool_error("crossfade must be 'A', 'B' or 'none'", cmd=cmd)
        if panning_mode is not None and panning_mode not in ("stereo", "split_stereo"):
            return tool_error("panning_mode must be 'stereo' or 'split_stereo'", cmd=cmd)
        return bridge_call(bridge, cmd, {"track": track, **values})

    @mcp.tool()
    def live_mixer_set_many(settings: list[dict[str, Any]], stop_on_error: bool = False) -> Any:
        """Apply mixer settings to many tracks at once — one call, one undo step.

        Args:
            settings: List of objects, each with "track" plus any of: volume, pan, sends,
                activator, crossfade, mute, solo, panning_mode, and for the master also
                cue_volume, crossfader (same formats as live_mixer_set). Example:
                [{"track": "Bass", "volume": "-3 dB", "pan": "L10"},
                 {"track": "Vocals", "sends": {"A": "-12 dB"}, "mute": false},
                 {"track": "master", "volume": "-1 dB"}]
            stop_on_error: Stop at the first failing entry (earlier ones stay applied).
                Default: continue and report failures.

        Returns:
            {"applied": n, "failed": n, "results": [{index, track, ok, changed, volume_db,
            pan} | {index, track, ok: false, error}]}.
        """
        cmd = "mixer.set_many"
        if not settings:
            return tool_error("settings must be a non-empty list", cmd=cmd)
        for position, entry in enumerate(settings):
            if "track" not in entry:
                return tool_error(f"settings[{position}] needs a 'track' key", cmd=cmd)
            unknown = sorted(set(entry) - set(SET_KEYS) - {"track"})
            if unknown:
                return tool_error(f"settings[{position}] has unknown keys {unknown}; allowed: "
                                  f"track, {', '.join(SET_KEYS)}", cmd=cmd)
        return bridge_call(bridge, cmd, {"settings": settings, "stop_on_error": stop_on_error})

    @mcp.tool()
    def live_mixer_master(
        volume: float | str | None = None,
        pan: float | str | None = None,
        cue_volume: float | str | None = None,
        crossfader: float | str | None = None,
    ) -> Any:
        """Read or set the master ("Main") track: volume, pan, cue (preview) volume, crossfader.

        Args:
            volume: Master volume — 0..1 raw, "-3 dB", "+1" (relative), "-inf".
            pan: Master pan (-1..1, "L10", "C").
            cue_volume: Cue/preview output level (same formats as volume).
            crossfader: Position -1..1, "A", "B", "center", "25A", "B10".
            All optional — call with nothing to just read the master mixer.

        Returns:
            {name, path, type: "master", volume, pan, cue_volume, crossfader, active,
            changed}.
        """
        cmd = "mixer.master"
        error = _check_number(volume, 0, 1, "volume", cmd) or \
            _check_number(cue_volume, 0, 1, "cue_volume", cmd) or \
            _check_number(pan, -1, 1, "pan", cmd) or \
            _check_number(crossfader, -1, 1, "crossfader", cmd)
        if error:
            return error
        return bridge_call(bridge, cmd, drop_none(volume=volume, pan=pan, cue_volume=cue_volume,
                                                  crossfader=crossfader))

    @mcp.tool()
    def live_mixer_reset(
        track: int | str | list[int | str] | None = None,
        what: list[str] | str | None = None,
    ) -> Any:
        """Reset mixer controls to Live's defaults (one undo step).

        Defaults: volume 0 dB, pan center, sends -inf, activator on, crossfade none, panning
        mode stereo; mute/solo off only when listed in `what`.

        Args:
            track: A track, a list, or omitted = every track, return and the master.
            what: Limit to some of "volume", "pan", "sends", "activator", "crossfade",
                "panning_mode", "mute", "solo"; or "all" for everything incl. mute/solo.

        Returns:
            {"reset": [{track, done: [...]}], "what": [...]}.
        """
        cmd = "mixer.reset"
        if track is not None:
            error = check_track(track, cmd, allow_list=True)
            if error:
                return error
        if what is not None and what != "all":
            keys = [what] if isinstance(what, str) else what
            bad = [k for k in keys if k not in RESET_KEYS]
            if bad:
                return tool_error(f"cannot reset {bad}; choose from {', '.join(RESET_KEYS)} "
                                  "or 'all'", cmd=cmd)
        return bridge_call(bridge, cmd, drop_none(track=track, what=what))

    @mcp.tool()
    def live_mixer_meters(
        track: int | str | list[int | str] | None = None,
        seconds: float = 0.0,
        include_input: bool = False,
        include_impact: bool = False,
    ) -> Any:
        """Read Live's level meters (tracks, returns, master) and CPU load — the only audio
        feedback available: check that an instrument / imported sample actually sounds, that
        nothing is pinned at the top (clipping), that a recording input receives signal.

        Args:
            track: One track, a list, or omitted = every track, return and the master.
            seconds: 0 = one instant snapshot. > 0 = sample repeatedly for that long (max 10 s,
                ~10 samples/s) and report the peak and average per track — use this while the
                song or a clip plays (start playback first, e.g. live_transport_play).
            include_input: Also the input meters (is an armed/monitoring track receiving?).
            include_impact: Also each track's CPU impact (snapshot mode).

        Returns:
            Snapshot: {"playing", "tracks": [{"name", "type", "level", "left"?, "right"?,
            "peak", "audio", "input"?}], "returns", "master", "cpu": {"average", "peak"}}.
            Sampled: {"seconds", "samples", "playing", "tracks": [{"name", "type", "peak",
            "avg", "hot"?, "silent"?, "input_peak"?}], "returns", "master",
            "cpu": {"average", "peak"}, "warnings"?}.

        Gotchas: meter values are Live's meter positions 0..1 (not dB); >= 0.99 is flagged
        "hot" (at the top of the meter — possible clipping). Everything reads 0 while stopped.
        CPU is in percent like Live's CPU meter.
        """
        cmd = "mixer.meters"
        if track is not None:
            error = check_track(track, cmd, allow_list=True)
            if error:
                return error
        if not 0.0 <= seconds <= 10.0:
            return tool_error("seconds must be 0..10", cmd=cmd)
        args = drop_none(track=track)
        if include_input:
            args["include_input"] = True
        if include_impact:
            args["include_impact"] = True
        if seconds <= 0:
            return bridge_call(bridge, cmd, args)
        snapshots: list[dict[str, Any]] = []
        # perf_counter, not monotonic: on Windows before Python 3.13 monotonic() ticks every
        # ~16 ms, so the loop would keep sleeping a few ms and re-sampling until it catches up
        started = time.perf_counter()
        while len(snapshots) < MAX_METER_SAMPLES:
            snap = bridge_call(bridge, cmd, args)
            if isinstance(snap, dict) and "error" in snap and "type" in snap:
                return snap if not snapshots else {**aggregate_meters(
                    snapshots, time.perf_counter() - started), "error": snap["error"]}
            snapshots.append(snap)
            elapsed = time.perf_counter() - started
            if elapsed >= seconds:
                break
            time.sleep(min(0.1, max(0.0, seconds - elapsed)))
        return aggregate_meters(snapshots, time.perf_counter() - started)
