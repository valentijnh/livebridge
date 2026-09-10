"""Routing tools: input/output routing of tracks, monitoring, a whole-set routing overview and
track-to-track routing (resampling, busses, compressor side-chain).

Routing choices are whatever Live offers for that track right now (they depend on the track
type, the audio interface and the other tracks), so read them with live_routing_get and pass the
names back. Live 12 calls the master output "Main" ("Master" is accepted as an alias).
"""

from __future__ import annotations

from typing import Any

from . import bridge_call, drop_none, tool_error
from ..client import BridgeClient
from .tracks import check_track

MONITORING = ("in", "auto", "off")


def check_monitoring(monitoring: str | None, cmd: str) -> dict[str, Any] | None:
    """Tool error unless ``monitoring`` is None or in/auto/off."""
    if monitoring is not None and monitoring.strip().lower() not in MONITORING:
        return tool_error("monitoring must be 'in', 'auto' or 'off'", cmd=cmd)
    return None


def register(mcp: Any, bridge: BridgeClient) -> None:
    """Register the routing tools on the MCP app."""

    @mcp.tool()
    def live_routing_get(track: int | str, include_available: bool = True) -> Any:
        """Show a track's input/output routing, monitoring and every routing choice Live offers.

        Args:
            track: Index, name, return letter ("A"), "master", "selected" or path.
            include_available: Also list the available types/channels (default true).

        Returns:
            {track, input: {type, channel, category} | null, output: {...} | null,
            monitoring: "in"|"auto"|"off"|null, arm?, available: {input_types: [{index, name,
            category, track?}], input_channels: [{index, name, layout}], output_types,
            output_channels}}. Categories: external, resampling, main, track, none, ...

        Gotchas:
            Channel lists belong to the CURRENT type; they change after the type changes.
            Monitoring and arm exist on MIDI/audio tracks only; `input`/`output` are null where
            Live offers no routing for the track. Hardware inputs/outputs ("Ext. In"/"Ext. Out"
            channels) depend on Live's audio preferences.
        """
        error = check_track(track, "routing.get")
        if error:
            return error
        return bridge_call(bridge, "routing.get",
                           {"track": track, "include_available": include_available})

    @mcp.tool()
    def live_routing_set(
        track: int | str,
        input_type: str | int | None = None,
        input_channel: str | int | None = None,
        output_type: str | int | None = None,
        output_channel: str | int | None = None,
        monitoring: str | None = None,
    ) -> Any:
        """Change a track's input/output routing and/or monitoring by name.

        Args:
            track: Index, name, return letter, "master", "selected" or path.
            input_type: e.g. "Ext. In", "All Ins", "Computer Keyboard", "Resampling",
                "No Input", or another track's name.
            input_channel: e.g. "1/2", "1", "All Channels", "Ch. 1", "Post FX", "Pre FX".
            output_type: e.g. "Main" (or "Master"), "Sends Only", "Ext. Out", another track.
            output_channel: e.g. "Track In", "1/2".
            monitoring: "in" (always hear the input), "auto" (hear it while armed — default)
                or "off".
            Names match case-insensitively (exact, then unique prefix, then unique part); an
            integer picks the option by its index from live_routing_get.

        Returns:
            The routing after the change incl. the new channel choices, plus `changed`.

        Gotchas:
            Types are applied before channels. An unknown name returns the list of valid
            choices. For "route track A into B" use live_routing_route.
        """
        cmd = "routing.set"
        error = check_track(track, cmd) or check_monitoring(monitoring, cmd)
        if error:
            return error
        values = drop_none(input_type=input_type, input_channel=input_channel,
                           output_type=output_type, output_channel=output_channel,
                           monitoring=monitoring.strip().lower() if monitoring else None)
        if not values:
            return tool_error("nothing to change: pass input_type, input_channel, output_type, "
                              "output_channel or monitoring", cmd=cmd)
        return bridge_call(bridge, cmd, {"track": track, **values})

    @mcp.tool()
    def live_routing_summary(include_returns: bool = True, include_master: bool = True) -> Any:
        """One compact routing line per track: input, output, monitoring and arm.

        Args:
            include_returns / include_master: Include return tracks / the master (default yes).

        Returns:
            {"tracks": [{name, path, type, in: "Ext. In | 1/2", out: "Main", monitoring,
            arm?}]} — `in`/`out` are null where the track has none.
        """
        return bridge_call(bridge, "routing.summary",
                           {"include_returns": include_returns, "include_master": include_master})

    @mcp.tool()
    def live_routing_route(
        source: int | str,
        destination: int | str,
        method: str = "input",
        channel: str | None = None,
        monitoring: str | None = None,
        device: int | str | None = None,
    ) -> Any:
        """Route one track into another: resampling/recording, bus/submix, or side-chain.

        Args:
            source: The track whose signal is used (index, name, path, or "master").
            destination: The track that receives it.
            method:
                "input" (default) — the destination's INPUT listens to the source (record or
                resample the source onto the destination; the source still plays to Main);
                "output" — the source's OUTPUT goes into the destination (bus/submix; the source
                no longer feeds Main directly);
                "sidechain" — the side-chain of a Compressor on the destination listens to the
                source (ducking/pumping).
            channel: Optional tap/channel: "Post FX", "Pre FX", "Post Mixer" (input/sidechain)
                or "Track In" (output). Default = Live's default.
            monitoring: Destination monitoring for method "input": "in" to hear the routed
                signal, "off" to only record it, "auto" = hear it while armed.
            device: Side-chain only — which device on the destination (index or name);
                default = the first device with side-chain routing.

        Returns:
            {method, source, destination, routing: {...}, device?, sidechain_enabled?,
            notes?: [...]}.

        Gotchas:
            Live only offers compatible tracks (MIDI into MIDI tracks, audio into audio
            tracks) — otherwise the error lists the valid choices. To record the result, arm
            the destination (live_record_arm). Only Compressor exposes side-chain routing to
            Live's API; add one first (device tools).
        """
        cmd = "routing.route"
        method = method.strip().lower()
        if method not in ("input", "output", "sidechain"):
            return tool_error("method must be 'input', 'output' or 'sidechain'", cmd=cmd)
        error = check_track(source, cmd) or check_track(destination, cmd) or \
            check_monitoring(monitoring, cmd)
        if error:
            return error
        if monitoring is not None and method != "input":
            return tool_error("monitoring only applies to method='input'", cmd=cmd)
        if device is not None and method != "sidechain":
            return tool_error("device only applies to method='sidechain'", cmd=cmd)
        return bridge_call(bridge, cmd, drop_none(
            source=source, destination=destination, method=method, channel=channel,
            monitoring=monitoring.strip().lower() if monitoring else None, device=device))
