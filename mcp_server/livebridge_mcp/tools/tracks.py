"""Track tools: list/inspect/find tracks, create/delete/duplicate, rename/colour/mute/solo/arm/
fold, group members, stop clips, select.

Track arguments everywhere (also in the mixer/routing/record tools) accept: an index into the
regular tracks (0-based), a name (exact, case-insensitive prefix or unique part of it), a return
track letter ("A", "return B"), "master" (Live 12 calls it "Main"), "selected", or a LOM path such
as "song.return_tracks[0]". Not possible through Live's API (so no tool offers it): moving or
reordering tracks, grouping/ungrouping, freezing/flattening.
"""

from __future__ import annotations

from typing import Any

from . import bridge_call, drop_none, tool_error
from ..client import BridgeClient

DETAILS = ("minimal", "summary", "full")
TRACK_TYPES = ("midi", "audio", "return", "master", "group")


def check_detail(detail: str, cmd: str) -> dict[str, Any] | None:
    """Return a tool error when ``detail`` is not minimal/summary/full."""
    if detail not in DETAILS:
        return tool_error(f"detail must be one of {', '.join(DETAILS)}", cmd=cmd)
    return None


def check_track(track: Any, cmd: str, allow_list: bool = False) -> dict[str, Any] | None:
    """Return a tool error for an empty/invalid track reference."""
    if isinstance(track, bool):
        return tool_error("track must be an index, a name or a path", cmd=cmd)
    if isinstance(track, list):
        if not allow_list:
            return tool_error("pass a single track here", cmd=cmd)
        if not track:
            return tool_error("the track list is empty", cmd=cmd)
        for item in track:
            error = check_track(item, cmd)
            if error:
                return error
        return None
    if isinstance(track, str) and not track.strip():
        return tool_error("track must not be empty", cmd=cmd)
    return None


def register(mcp: Any, bridge: BridgeClient) -> None:
    """Register the track tools on the MCP app."""

    @mcp.tool()
    def live_tracks_list(
        type: str | list[str] | None = None,
        name: str | None = None,
        include_returns: bool = True,
        include_master: bool = True,
        frozen: bool | None = None,
        armed: bool | None = None,
        detail: str = "summary",
        offset: int = 0,
        limit: int | None = None,
    ) -> Any:
        """List the tracks of the set (regular tracks, then returns, then the master).

        Args:
            type: Only these types: "midi", "audio", "return", "master", "group" (or a list).
            name: Only tracks whose name contains this text (case-insensitive).
            include_returns / include_master: Include return tracks / the master (default yes).
            frozen: true = only frozen tracks, false = only unfrozen.
            armed: true = only armed tracks, false = only unarmed.
            detail: "minimal" (path, index, name, type — cheapest), "summary" (default: + mute,
                solo, arm, colour, monitoring, routing, volume/pan values, devices) or "full"
                (+ clips per slot and device details).
            offset / limit: Paging over the filtered list.

        Returns:
            {"total", "offset", "count", "tracks": [{path, index, name, type, ...}]}. Use the
            `path` (song.tracks[i], song.return_tracks[i], song.master_track) in later calls.

        Gotchas:
            A return track's `index` is its position in song.return_tracks. Tracks cannot be
            reordered through Live's API. Volume here is the raw 0..1 value — use
            live_mixer_get for dB.
        """
        cmd = "tracks.list"
        error = check_detail(detail, cmd)
        if error:
            return error
        kinds = [type] if isinstance(type, str) else type
        if kinds is not None:
            bad = [k for k in kinds if str(k).lower() not in TRACK_TYPES]
            if bad:
                return tool_error(f"unknown track type(s) {bad}; use {', '.join(TRACK_TYPES)}",
                                  cmd=cmd)
        if offset < 0 or (limit is not None and limit < 0):
            return tool_error("offset and limit must be >= 0", cmd=cmd)
        return bridge_call(bridge, cmd, drop_none(
            type=type, name=name, include_returns=include_returns,
            include_master=include_master, frozen=frozen, armed=armed, detail=detail,
            offset=offset, limit=limit))

    @mcp.tool()
    def live_tracks_get(track: int | str, detail: str = "full") -> Any:
        """Everything about one track: state, devices, clips, routing, freeze and group info.

        Args:
            track: Index, name, return letter ("A"), "master", "selected" or LOM path.
            detail: "minimal" | "summary" | "full" (default — includes clips and devices).

        Returns:
            Track summary + `color` (hex), `is_visible`, `muted_via_solo`,
            `freeze: {is_frozen, can_be_frozen}` and, for group tracks, `members`.

        Gotchas:
            Freeze/flatten cannot be triggered through Live's API — only read.
        """
        cmd = "tracks.get"
        error = check_track(track, cmd) or check_detail(detail, cmd)
        if error:
            return error
        return bridge_call(bridge, cmd, {"track": track, "detail": detail})

    @mcp.tool()
    def live_tracks_find(name: str, limit: int = 20) -> Any:
        """Find tracks (incl. returns and master) by name or part of it.

        Args:
            name: Text to search, case-insensitive; exact matches rank first, then prefix,
                then contains, then punctuation-insensitive ("areverb" finds "A-Reverb").
            limit: Maximum matches (default 20).

        Returns:
            {"query", "count", "matches": [{path, index, name, type}]} — empty when nothing
            matches (not an error).
        """
        if not name or not name.strip():
            return tool_error("name must not be empty", cmd="tracks.find")
        if limit < 1:
            return tool_error("limit must be >= 1", cmd="tracks.find")
        return bridge_call(bridge, "tracks.find", {"name": name, "limit": limit})

    @mcp.tool()
    def live_tracks_create(
        type: str = "midi",
        name: str | None = None,
        index: int = -1,
        after_selected: bool = False,
        color: int | str | list[int] | None = None,
    ) -> Any:
        """Create a MIDI, audio or return track.

        Args:
            type: "midi" (default), "audio" or "return".
            name: Optional track name.
            index: Position among the regular tracks (0 = first); -1 (default) = at the end.
                Ignored for return tracks (always appended).
            after_selected: true = insert right after the selected track (Live's own default)
                instead of using `index`.
            color: Palette index 0-69, "#RRGGBB", [r, g, b] or a name ("red", "blue", "green",
                "yellow", "orange", "purple", "pink", "cyan", "grey", ...).

        Returns:
            The new track's summary — its `path` says where it landed.

        Gotchas:
            Choose the position now: Live's API cannot move tracks later. Group tracks cannot
            be created. To add an instrument, follow up with the device/browser tools.
            Intro/Lite track limits come back as type "unsupported". Live prefixes a return
            track's name with its letter (name="Verb" reads back "C-Verb").
        """
        cmd = "tracks.create"
        kind = type.strip().lower()
        if kind not in ("midi", "audio", "return"):
            return tool_error("type must be 'midi', 'audio' or 'return' (groups cannot be "
                              "created through Live's API)", cmd=cmd)
        if index < -1:
            return tool_error("index must be >= 0, or -1 for the end", cmd=cmd)
        args: dict[str, Any] = drop_none(type=kind, name=name, color=color)
        args["index"] = None if after_selected else index
        return bridge_call(bridge, cmd, args)

    @mcp.tool()
    def live_tracks_delete(track: int | str) -> Any:
        """Delete a regular or return track (one undo step).

        Args:
            track: Index, name, return letter or path. The master cannot be deleted.

        Returns:
            {"deleted": name, "type", "path" (before deletion), "track_count",
            "return_count"}.

        Gotchas:
            Indices of later tracks shift down — re-list before using indices again. A set
            keeps at least one regular track. Deleting a return removes its send everywhere.
        """
        error = check_track(track, "tracks.delete")
        if error:
            return error
        return bridge_call(bridge, "tracks.delete", {"track": track})

    @mcp.tool()
    def live_tracks_duplicate(track: int | str, name: str | None = None) -> Any:
        """Duplicate a regular track with its devices and clips (the copy goes right after it).

        Args:
            track: Index, name or path of a MIDI/audio/group track.
            name: Optional name for the copy.

        Returns:
            The copy's summary (Live selects it).

        Gotchas:
            Return tracks and the master cannot be duplicated; a group is copied with its
            members.
        """
        error = check_track(track, "tracks.duplicate")
        if error:
            return error
        return bridge_call(bridge, "tracks.duplicate", drop_none(track=track, name=name))

    @mcp.tool()
    def live_tracks_set(
        track: int | str | list[int | str],
        name: str | None = None,
        color: int | str | list[int] | None = None,
        mute: bool | str | None = None,
        solo: bool | str | None = None,
        arm: bool | str | None = None,
        fold: bool | str | None = None,
        exclusive: bool = False,
    ) -> Any:
        """Rename, recolour, mute, solo, arm or fold one or several tracks in one undo step.

        Args:
            track: One track (index, name, return letter, "master", "selected", path), a LIST
                of tracks, or "all" (every regular + return track).
            name: New name (single track only).
            color: Palette index 0-69, "#RRGGBB", [r, g, b] or a colour name ("red", "blue",
                ...). Ints above 69 are read as 0xRRGGBB.
            mute / solo / arm / fold: true, false or "toggle". `fold` works on group tracks.
            exclusive: With solo=true un-solo all other tracks; with arm=true disarm all others.

        Returns:
            {"tracks": [{path, name, type, mute, solo, arm?, folded?, color_index, color}],
            "errors": [{track, error}] only when some of several tracks failed}.

        Gotchas:
            Return, master and group tracks cannot be armed; the master has no mute/solo.
            Volume/pan/sends are in live_mixer_set; arming with monitoring/input checks is
            live_record_arm.
        """
        cmd = "tracks.set"
        error = check_track(track, cmd, allow_list=True)
        if error:
            return error
        if all(v is None for v in (name, color, mute, solo, arm, fold)):
            return tool_error("nothing to change: pass name, color, mute, solo, arm or fold",
                              cmd=cmd)
        if name is not None and (isinstance(track, list) and len(track) != 1):
            return tool_error("name can only be set on a single track", cmd=cmd)
        for label, value in (("mute", mute), ("solo", solo), ("arm", arm), ("fold", fold)):
            if isinstance(value, str) and value.strip().lower() not in (
                    "toggle", "on", "off", "true", "false", "yes", "no", "1", "0"):
                return tool_error(f"{label} must be true, false or \"toggle\"", cmd=cmd)
        return bridge_call(bridge, cmd, drop_none(
            track=track, name=name, color=color, mute=mute, solo=solo, arm=arm, fold=fold,
            exclusive=exclusive or None))

    @mcp.tool()
    def live_tracks_group(track: int | str, fold: bool | str | None = None) -> Any:
        """Show a group track's members and fold state; optionally fold/unfold it.

        Args:
            track: A group track, or any track inside one (its group is used).
            fold: Optional true (fold), false (unfold) or "toggle".

        Returns:
            {"group": {path, name, ...}, "folded": bool, "grouped_in": parent group path or
            null, "members": [{path, name, type, depth}]} — depth 1 = direct member.

        Gotchas:
            Live's API cannot create groups, ungroup, or move tracks in/out of groups.
        """
        error = check_track(track, "tracks.group")
        if error:
            return error
        return bridge_call(bridge, "tracks.group", drop_none(track=track, fold=fold))

    @mcp.tool()
    def live_tracks_stop_clips(
        track: int | str | list[int | str] | None = None,
        quantized: bool = True,
    ) -> Any:
        """Stop the session clips of one track, several tracks, or all tracks.

        Args:
            track: A track, a list of tracks, or omitted / "all" = every track.
            quantized: true (default) = stop at the next launch-quantization boundary;
                false = immediately.

        Returns:
            {"stopped": [track names] or "all", "quantized": bool}.

        Gotchas:
            The transport keeps running and arrangement playback is unaffected.
        """
        if track is not None:
            error = check_track(track, "tracks.stop_clips", allow_list=True)
            if error:
                return error
        return bridge_call(bridge, "tracks.stop_clips",
                           drop_none(track=track, quantized=quantized))

    @mcp.tool()
    def live_tracks_select(track: int | str) -> Any:
        """Select a track in Live (what the user sees highlighted; browser loads target it).

        Args:
            track: Index, name, return letter, "master" or path.

        Returns:
            {"selected": {path, index, name, type}}.
        """
        error = check_track(track, "tracks.select")
        if error:
            return error
        return bridge_call(bridge, "tracks.select", {"track": track})
