"""Cue point (arrangement locator) tools and song-time conversion.

Times: numbers are beats (quarter notes); strings are Live's "bars.beats.sixteenths"
("9.1.1" = bar 9, 1-based positions; "4.0.0" = a length of four bars). Cues are addressed by
index (0-based, time order), name (exact, then case-insensitive prefix), "@<time>" ("@32",
"@9.1.1") or LOM path ("song.cue_points[1]" — Live keeps that list in creation order; the `path`
of each cue is in live_cue_list).

The pure helpers :func:`beats_to_bbs` / :func:`bbs_to_beats` mirror
``remote_script/LiveBridge/handlers/cues.py`` so `live_time_convert` can answer offline when the
time signature is given.
"""

from __future__ import annotations

import re
import time as _time
from typing import Any

from . import bridge_call, drop_none, tool_error
from ..client import BridgeClient

Ref = int | str
TimeArg = float | str

_BBS_RE = re.compile(r"^\s*(\d+)(?:[.:](\d+))?(?:[.:](\d+))?\s*$")
_SIGNATURE_RE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*$")
_DIRECTIONS = ("next", "prev", "previous", "first", "last")
#: Max. retries of a pending cue operation (Live needs one tick per playhead move).
_MAX_ROUNDS = 40
#: Commands a pending result may ask to retry (built, not literal, so docs/TOOLS.md maps each
#: tool only to the command it starts).
_PENDING_CMDS = frozenset("cues." + verb for verb in ("add", "toggle", "delete", "set",
                                                      "layout"))
#: Most locators per live_cue_layout call (the handler's limit).
MAX_LAYOUT = 64
#: Seconds to wait for Live's next tick after a transport stop / before resuming playback.
_TICK_WAIT = 0.15


def follow_pending(bridge: BridgeClient, cmd: str, args: dict[str, Any],
                   max_rounds: int = _MAX_ROUNDS) -> Any:
    """Run a cue command and follow its ``pending``/``retry`` protocol to the end.

    Live 12.4.5 moves the playhead on its next tick and only sets/deletes a locator at the
    playhead, so ``cues.add/toggle/delete/set/layout`` away from the playhead answer
    ``{"pending": true, "retry": {"cmd", "args"}, "retry_after_ms"}``; this sends the retry
    (after the suggested wait) until a final result arrives (at most ``max_rounds``).
    ``deleted`` rows of intermediate ``cues.delete`` rounds are accumulated.
    """
    deleted: list[Any] = []
    moved_from: Any = None
    result = bridge_call(bridge, cmd, args)
    for _ in range(max_rounds):
        if not (isinstance(result, dict) and result.get("pending")):
            break
        deleted.extend(result.get("deleted") or [])
        moved_from = result.get("moved_from", moved_from)
        retry = result.get("retry") or {}
        next_cmd, next_args = retry.get("cmd"), retry.get("args")
        if next_cmd not in _PENDING_CMDS or not isinstance(next_args, dict):
            return tool_error(f"unexpected retry from the bridge: {retry!r}", cmd=cmd,
                              type="internal")
        wait = result.get("retry_after_ms", 150)
        _time.sleep(max(0.05, min(float(wait) / 1000.0, 2.0)))
        result = bridge_call(bridge, next_cmd, next_args)
    else:
        if isinstance(result, dict) and result.get("pending"):
            return tool_error("Live did not move the playhead in time (is the transport "
                              "running or another client moving it?)", cmd=cmd,
                              type="invalid_state")
    if isinstance(result, dict) and "error" not in result:
        if deleted and isinstance(result.get("deleted"), list):
            result["deleted"] = deleted + result["deleted"]
        if moved_from is not None and "created" in result:
            result = {"cue": result.get("cue"), "moved": True, "moved_from": moved_from,
                      "renamed": (result.get("cue") or {}).get("name") != moved_from.get("name"),
                      "count": result.get("count")}
    return result


def run_cue_op(bridge: BridgeClient, cmd: str, args: dict[str, Any],
               stop_playback: bool = False, max_rounds: int = _MAX_ROUNDS) -> Any:
    """:func:`follow_pending`, optionally around a transport stop.

    With ``stop_playback`` and a running transport: stop it (``transport.stop``; Live applies
    it on its next tick), run the cue operation (its last step puts the playhead back where
    playback stopped) and continue playback from there (``transport.continue``). The result
    then carries ``"playback": "resumed"``. Without a running transport nothing extra happens.
    """
    resume = False
    if stop_playback:
        where = bridge_call(bridge, "cues.position")
        if isinstance(where, dict) and "error" in where:
            return where
        if isinstance(where, dict) and where.get("is_playing"):
            stopped = bridge_call(bridge, "transport.stop")
            if isinstance(stopped, dict) and "error" in stopped:
                return stopped
            resume = True
            _time.sleep(_TICK_WAIT)
    result = follow_pending(bridge, cmd, args, max_rounds)
    if resume:
        _time.sleep(_TICK_WAIT)      # the playhead restore lands on Live's next tick
        resumed = bridge_call(bridge, "transport.continue")
        if isinstance(result, dict):
            if isinstance(resumed, dict) and "error" in resumed:
                result["playback"] = f"not resumed: {resumed['error']}"
            else:
                result["playback"] = "resumed"
    return result


def _grid(numerator: int, denominator: int) -> tuple[float, float, float]:
    beat = 4.0 / denominator
    return numerator * beat, beat, (0.25 if beat >= 0.25 else beat)


def parse_signature(text: str) -> tuple[int, int]:
    """``"3/4"`` -> ``(3, 4)``; raises ``ValueError`` with a readable message."""
    match = _SIGNATURE_RE.match(str(text))
    if not match:
        raise ValueError(f"signature must look like '4/4', got {text!r}")
    numerator, denominator = int(match.group(1)), int(match.group(2))
    if not 1 <= numerator <= 99 or denominator not in (1, 2, 4, 8, 16, 32):
        raise ValueError(f"signature {text!r} out of range (numerator 1..99, denominator "
                         "1/2/4/8/16/32)")
    return numerator, denominator


def beats_to_bbs(beats: float, numerator: int = 4, denominator: int = 4,
                 is_length: bool = False) -> str | None:
    """Beats -> "bars.beats.sixteenths" (1-based positions, 0-based lengths)."""
    value = float(beats)
    if value < 0:
        return None
    bar_len, beat_len, sub = _grid(numerator, denominator)
    value += 1e-7
    bars = int(value // bar_len)
    rest = value - bars * bar_len
    beat = int(rest // beat_len)
    rest -= beat * beat_len
    sixteenth = int(rest // sub)
    offset = 0 if is_length else 1
    return f"{bars + offset}.{beat + offset}.{sixteenth + offset}"


def bbs_to_beats(text: str, numerator: int = 4, denominator: int = 4,
                 is_length: bool = False) -> float:
    """"bars[.beats[.sixteenths]]" -> beats; raises ``ValueError`` when invalid."""
    match = _BBS_RE.match(str(text))
    if match is None:
        raise ValueError(f"{text!r} is not bars.beats.sixteenths (e.g. '9.1.1')")
    bar_len, beat_len, sub = _grid(numerator, denominator)
    per_beat = max(1, int(round(beat_len / sub)))
    bars = int(match.group(1))
    if is_length:
        beat = int(match.group(2) or 0)
        sixteenth = int(match.group(3) or 0)
        if beat >= numerator or sixteenth >= per_beat:
            raise ValueError(f"{text!r}: as a length the beat part must be 0..{numerator - 1} "
                             f"and the sixteenth part 0..{per_beat - 1}")
        return bars * bar_len + beat * beat_len + sixteenth * sub
    beat = int(match.group(2) or 1)
    sixteenth = int(match.group(3) or 1)
    if bars < 1 or not 1 <= beat <= numerator or not 1 <= sixteenth <= per_beat:
        raise ValueError(f"{text!r}: positions are 1-based — bar >= 1, beat 1..{numerator}, "
                         f"sixteenth 1..{per_beat}")
    return (bars - 1) * bar_len + (beat - 1) * beat_len + (sixteenth - 1) * sub


def time_error(value: Any, name: str, cmd: str) -> dict[str, Any] | None:
    """A tool error for a negative number or a malformed bbs string, else ``None``."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return tool_error(f"{name} must be >= 0 beats", cmd=cmd) if value < 0 else None
    if isinstance(value, str) and _BBS_RE.match(value):
        return None
    return tool_error(f"{name} must be beats (a number) or 'bars.beats.sixteenths' like '9.1.1'",
                      cmd=cmd)


def _cue_error(cue: Any, cmd: str) -> dict[str, Any] | None:
    if isinstance(cue, str) and not cue.strip():
        return tool_error("cue must be an index, a name, '@<time>' or a path", cmd=cmd)
    return None


def register(mcp: Any, bridge: BridgeClient) -> None:
    """Register the cue point and time tools."""

    @mcp.tool()
    def live_cue_list(include_position: bool = True, position_only: bool = False) -> Any:
        """List the arrangement cue points (locators) — the song's sections — and where the
        playhead is relative to them.

        Args:
            include_position: Add the playhead block (default True).
            position_only: Skip the cue list; return only the playhead block (cheap "where am
                I / which section" check).

        Returns:
            {"count", "signature": "4/4", "cues": [{"index", "name", "time" (beats),
            "bbs": "9.1.1", "length" (beats to the next cue; null for the last), "path"}],
            "position": {"position": {"beats", "bbs"}, "is_playing", "at", "previous", "next",
            "section" (name of the cue at/before the playhead), "since_section_start"?,
            "until_next"?}}
            With position_only only the "position" block's contents are returned.

        Gotchas: `index` is the time order and shifts when cues are added/removed before it;
        `path` is the LOM path (Live keeps song.cue_points in creation order, so it can differ).
        """
        if position_only:
            return bridge_call(bridge, "cues.position")
        return bridge_call(bridge, "cues.list", {"include_position": include_position})

    @mcp.tool()
    def live_cue_add(time: TimeArg | None = None, name: str | None = None,
                     toggle: bool = False, stop_playback: bool = False) -> Any:
        """Add a locator (cue point) at a time, optionally named — or toggle one like Live's
        Set/Delete button.

        Args:
            time: Beats or "bars.beats.sixteenths" ("17.1.1" = bar 17). Default: the playhead.
            name: Name for the new cue ("Chorus"). Ignored with toggle.
            toggle: Live's Set/Delete semantics: delete the cue at `time` if one is there,
                otherwise add an unnamed one.
            stop_playback: While playing, stop the transport, place the cue and continue
                playback from where it stopped (otherwise a `time` away from the playhead is
                refused while playing).

        Returns:
            add: {"created": bool, "cue": {"index", "name", "time", "bbs"}, "count",
            "snapped"?, "playback"?: "resumed"} — created=false means a cue already sat there
            (it gets renamed, never deleted). toggle: {"action": "added"|"deleted", "cue",
            "count"}.

        Gotchas: Live can only add a cue at the playhead and moves the playhead on its next
        tick, so for a `time` away from the playhead the tool parks the playhead there, waits a
        tick, toggles and puts the playhead back (a few hundred ms). That needs a stopped
        transport (or stop_playback=true). A time behind the end of the song extends the song
        (the loop brace is stretched and put back), so sections can be marked before any clip
        exists. Several cues at once: live_cue_layout. Live names unnamed cues "1", "2", ...
        """
        cmd = "cues.toggle" if toggle else "cues.add"
        error = time_error(time, "time", cmd)
        if error:
            return error
        if toggle:
            if name is not None:
                return tool_error("name cannot be combined with toggle", cmd=cmd)
            return run_cue_op(bridge, cmd, drop_none(time=time), stop_playback)
        return run_cue_op(bridge, cmd, drop_none(time=time, name=name), stop_playback)

    @mcp.tool()
    def live_cue_delete(cue: Ref | None = None, all: bool = False,
                        stop_playback: bool = False) -> Any:
        """Delete one locator, or all of them.

        Args:
            cue: Index, name, "@<time>" ("@32", "@9.1.1") or path.
            all: Delete every cue point (omit `cue`).
            stop_playback: While playing, stop the transport, delete and continue playback
                from where it stopped.

        Returns:
            {"deleted": [{"index", "name", "time", "bbs"}], "count": remaining,
            "playback"?: "resumed"}.

        Gotchas: a cue can only be deleted at the playhead, so the tool parks the playhead on
        each cue (one Live tick per cue) and puts it back — the transport must be stopped (or
        stop_playback=true).
        """
        cmd = "cues.delete"
        if all and cue is not None:
            return tool_error("pass cue or all=true, not both", cmd=cmd)
        if not all and cue is None:
            return tool_error("say which cue (index, name or '@time') or pass all=true", cmd=cmd)
        error = _cue_error(cue, cmd)
        if error:
            return error
        return run_cue_op(bridge, cmd, {"all": True} if all else {"cue": cue}, stop_playback)

    @mcp.tool()
    def live_cue_set(cue: Ref, name: str | None = None, time: TimeArg | None = None,
                     stop_playback: bool = False) -> Any:
        """Rename and/or move a locator.

        Args:
            cue: Index, name, "@<time>" or path of the cue to change.
            name: New name.
            time: New position (beats or "bars.beats.sixteenths").
            stop_playback: While playing, stop the transport for a move and continue playback
                from where it stopped afterwards.

        Returns:
            {"cue": {"index", "name", "time", "bbs"}, "moved": bool, "renamed": bool,
            "moved_from"?: {name, time, bbs}, "merged"?: true, "count"?, "playback"?}.

        Gotchas: cue times are read-only in Live, so a move deletes the cue and re-creates it
        (keeping the name) — its index can change. Moving onto another cue merges into it. A
        move parks the playhead twice (a few hundred ms) and needs a stopped transport (or
        stop_playback=true); a rename works any time. Moving behind the song end extends it.
        """
        cmd = "cues.set"
        if name is None and time is None:
            return tool_error("pass name and/or time", cmd=cmd)
        error = _cue_error(cue, cmd) or time_error(time, "time", cmd)
        if error:
            return error
        return run_cue_op(bridge, cmd, drop_none(cue=cue, name=name, time=time),
                          stop_playback and time is not None)

    @mcp.tool()
    def live_cue_layout(cues: list[dict[str, Any] | list[Any]], replace: bool = False,
                        stop_playback: bool = False) -> Any:
        """Lay out many locators at once — the sections of a song — in ONE call (instead of
        one live_cue_add per section).

        Args:
            cues: List of sections, each {"name": "Verse", "bar": 9} (1-based bar number in the
                song signature), {"name": "Verse", "time": "9.1.1"} (beats or
                "bars.beats.sixteenths") or ["9.1.1", "Verse"]; at most 64, times must differ.
            replace: Also delete every existing cue that is not in the list (the set ends up
                with exactly these locators).
            stop_playback: While playing, stop the transport, lay out and continue playback
                from where it stopped.

        Returns:
            {"cues": [{"index", "name", "time", "bbs"}] (the layout, time order),
            "created": [{"name", "time", "bbs"}], "renamed": [...], "deleted": [...],
            "count", "snapped"?: [{"requested", "time"}], "playback"?: "resumed"}.

        Gotchas: a cue already at a time is renamed, never deleted (a null name keeps it).
        Live places locators only at the playhead, one Live tick per cue created/deleted away
        from it (~0.15-0.3 s each; 8 sections take ~2 s) — the tool follows that automatically
        and puts playhead, start marker and loop brace back. Needs a stopped transport (or
        stop_playback=true). Times behind the end of the song extend it, so a song structure
        can be laid out in an empty arrangement.
        """
        cmd = "cues.layout"
        if not isinstance(cues, list) or not cues:
            return tool_error("cues must be a non-empty list of {name, bar} / {name, time} / "
                              "[time, name]", cmd=cmd)
        if len(cues) > MAX_LAYOUT:
            return tool_error(f"at most {MAX_LAYOUT} cue points per layout", cmd=cmd)
        for index, item in enumerate(cues):
            if isinstance(item, dict):
                error = time_error(item.get("time"), f"cues[{index}].time", cmd)
            elif isinstance(item, list) and len(item) == 2:
                error = time_error(item[0], f"cues[{index}] time", cmd)
            else:
                error = tool_error(f"cues[{index}] must be {{name, bar}}, {{name, time}} or "
                                   "[time, name]", cmd=cmd)
            if error:
                return error
        args: dict[str, Any] = {"cues": cues}
        if replace:
            args["replace"] = True
        return run_cue_op(bridge, cmd, args, stop_playback,
                          max_rounds=_MAX_ROUNDS + 3 * len(cues))

    @mcp.tool()
    def live_cue_jump(cue: Ref | None = None, direction: str | None = None) -> Any:
        """Move the playhead to a locator: by index/name, or next/previous/first/last.

        Args:
            cue: Index, name, "@<time>" or path.
            direction: "next", "prev", "first" or "last" (instead of cue).

        Returns:
            {"jumped": bool, "target": {"index", "name", "time", "bbs"}|null,
            "quantized": bool, "position": {"beats", "bbs"}, "reason"?}.

        Gotchas: while playing, Live quantizes the jump (global launch quantization), so
        `position` still shows the current time — `target` is where it lands. Stopped, the
        start marker moves to the cue too. Not an undo step.
        """
        cmd = "cues.jump"
        if (cue is None) == (direction is None):
            return tool_error("pass either cue or direction (next|prev|first|last)", cmd=cmd)
        if direction is not None and direction.strip().lower() not in _DIRECTIONS:
            return tool_error("direction must be next, prev, first or last", cmd=cmd)
        error = _cue_error(cue, cmd)
        if error:
            return error
        return bridge_call(bridge, cmd, drop_none(cue=cue, direction=direction))

    @mcp.tool()
    def live_cue_loop(start: Ref, end: Ref | None = None, enable: bool = True,
                       jump: bool = False) -> Any:
        """Set the arrangement loop to a section: from one locator to the next (or to `end`).

        Args:
            start: Cue where the loop starts (index, name, "@<time>", path).
            end: Cue where it ends (exclusive). Default: the next cue after `start`.
            enable: Switch the loop on (default True; False only moves the loop brace).
            jump: Also move the playhead to the loop start.

        Returns:
            {"loop": {"on", "start", "end", "length", "start_bbs", "end_bbs", "length_bbs"},
            "from": cue, "to": cue}.

        Gotchas: fails when `start` is the last cue and no `end` is given. This is the
        arrangement loop; for arbitrary times use live_transport_set_loop.
        """
        cmd = "cues.loop"
        error = _cue_error(start, cmd) or _cue_error(end, cmd)
        if error:
            return error
        args: dict[str, Any] = drop_none(start=start, end=end)
        args["enable"] = enable
        if jump:
            args["jump"] = True
        return bridge_call(bridge, cmd, args)

    @mcp.tool()
    def live_time_convert(beats: float | list[float] | None = None,
                          bbs: str | list[str] | None = None, is_length: bool = False,
                          signature: str | None = None, tempo: float | None = None) -> Any:
        """Convert song times: beats <-> "bars.beats.sixteenths" (+ seconds and bars).

        Args:
            beats: A number or list of numbers (quarter-note beats).
            bbs: A "bars.beats.sixteenths" string or a list of them.
            is_length: Durations instead of positions ("4.0.0" = 4 bars; positions are
                1-based: "5.1.1" = bar 5 = beat 16 in 4/4).
            signature: e.g. "3/4" or "6/8". Given: computed locally (works without Live);
                omitted: Live's current song signature and tempo are used.
            tempo: BPM for `seconds` when `signature` is given (default: no seconds).

        Returns:
            {"signature", "tempo"?, "beats_per_bar", "results": [{"beats", "bbs", "seconds"?,
            "bars"}]} — `bars` is a float bar count (0-based for positions).

        Gotchas: in 6/8 a bar is 3 quarter-note beats and a "beat" in bbs is an eighth note.
        Seconds ignore tempo automation.
        """
        cmd = "cues.convert_time"
        if beats is None and bbs is None:
            return tool_error("pass beats and/or bbs", cmd=cmd)
        beat_list = beats if isinstance(beats, list) else ([] if beats is None else [beats])
        bbs_list = bbs if isinstance(bbs, list) else ([] if bbs is None else [bbs])
        if any(b < 0 for b in beat_list):
            return tool_error("beats must not be negative", cmd=cmd)
        if len(beat_list) + len(bbs_list) > 1000:
            return tool_error("at most 1000 values per call", cmd=cmd)
        if tempo is not None and not 20 <= tempo <= 999:
            return tool_error("tempo must be 20..999 BPM", cmd=cmd)
        if signature is None:
            return bridge_call(bridge, cmd, drop_none(beats=beats, bbs=bbs,
                                                      is_length=is_length or None))
        try:
            numerator, denominator = parse_signature(signature)
            values = [float(b) for b in beat_list]
            values += [bbs_to_beats(t, numerator, denominator, is_length) for t in bbs_list]
        except ValueError as exc:
            return tool_error(str(exc), cmd=cmd)
        bar_len = _grid(numerator, denominator)[0]
        results = []
        for value in values:
            row: dict[str, Any] = {"beats": round(value, 4),
                                   "bbs": beats_to_bbs(value, numerator, denominator, is_length),
                                   "bars": round(value / bar_len, 4)}
            if tempo is not None:
                row["seconds"] = round(value * 60.0 / tempo, 3)
            results.append(row)
        payload: dict[str, Any] = {"signature": f"{numerator}/{denominator}",
                                   "beats_per_bar": round(bar_len, 4), "results": results,
                                   "source": "local"}
        if tempo is not None:
            payload["tempo"] = tempo
        return payload
