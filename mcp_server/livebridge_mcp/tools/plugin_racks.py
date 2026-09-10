"""Full control of big plug-ins — Serum 2 first, any VST3 — through generated rack presets.

Live only exposes a plug-in's parameters from the device's Configure list, and big plug-ins
(Serum 2: 0 of 2623) start with an empty one; Live's API cannot add to it. LiveBridge works
around that without the user: it writes an Instrument Rack preset (effects: Audio Effect Rack)
that lists the wanted parameters by their VST3 ParameterId, lets Live index it in
"<User Library>/LiveBridge/Racks" and loads it. The exposed parameters are then ordinary Live
parameters: live_device_set_parameter(s) (display strings such as "800 Hz", "20 ms", "-1 oct"),
live_device_parameters, clip automation and rack macros all work.

Flow for Serum 2 (its complete name -> ParameterId map ships with LiveBridge):
1. live_plugin_expose(plugin="Serum 2", parameters=["sound_design"], new_track=true) — 120
   curated sound-design controls (oscillators, sub/noise, filters, envelopes, LFO rates,
   macros, FX slots); add names or groups as needed (max 128 per rack).
2. live_device_set_parameters(track=..., device=<returned device path>, values={...}).
3. Automate them like any parameter (live_automation_write).
For any other big VST3 plug-in: live_plugin_param_map(plugin=...) once (probes ~30 s), then
expose. Starting sounds: live_plugin_preset_files → preset_file=.
"""

from __future__ import annotations

import asyncio
import time as _time
from typing import Any

from . import bridge_call, drop_none, tool_error
from ..client import BridgeClient

_STEP_SECONDS = 4.0
_CALL_TIMEOUT = 45.0
_POLL_SECONDS = 1.0
_MAX_WAIT = 600.0
_FORMATS = ("VST3", "AU", "VST2", "vst3", "au", "vst2")


def _is_error(value: Any) -> bool:
    return isinstance(value, dict) and "error" in value and "type" in value


def _check_format(plugin_format: str | None, cmd: str) -> dict[str, Any] | None:
    if plugin_format is not None and plugin_format not in _FORMATS:
        return tool_error("plugin_format must be 'VST3', 'AU' or 'VST2'", cmd=cmd)
    return None


def register(mcp: Any, bridge: BridgeClient) -> None:
    """Register the generated-rack plug-in tools on the MCP app."""

    @mcp.tool()
    async def live_plugin_expose(
        parameters: list[str] | None = None,
        track: int | str | None = None,
        plugin: str | None = None,
        macros: dict[str, str] | None = None,
        preset_file: str | None = None,
        replace: bool = True,
        name: str | None = None,
        new_track: bool = False,
        track_name: str | None = None,
        plugin_format: str | None = None,
        editor_open: bool | None = None,
        wait_seconds: float = 30.0,
    ) -> Any:
        """Make a big plug-in (Serum 2, any mapped VST3) fully controllable: load it in a
        generated rack that exposes up to 128 chosen parameters to Live.

        Use this INSTEAD of asking the user to click Configure. Typical Serum 2 start:
        live_plugin_expose(plugin="Serum 2", parameters=["sound_design"], new_track=true,
        track_name="Lead") → then live_device_set_parameters with display strings
        ({"Filter 1 Freq": "800 Hz", "Env 1 Attack": "20 ms", "A Octave": "-1 oct",
        "A WT Pos": 0.5}) on the returned `device` path, and automate with the automation
        tools. Plug-in values are normalized 0..1 (plain numbers), display strings use the
        plug-in's own units.

        Args:
            parameters: Parameter names (fuzzy: "filter 1 cutoff", "env 1 attack") and/or
                group keywords. Serum 2 groups: "sound_design" (120 curated: osc A/B/C enable,
                level, pan, octave, semi, fine, WT pos, warp 1/2, unison voices/detune/blend;
                sub + noise level/pitch; osc→filter balance; filter 1/2 on, type, freq, res,
                drive, var, wet; env 1-4 A/H/D/S/R; LFO 1-4 rate; macros 1-8; main vol, tuning,
                transpose, porta, mono/legato, bus volumes; FX Main Param 1-16), "osc",
                "osc a", "osc b", "osc c", "sub", "noise", "filter", "filter 1", "filter 2",
                "env", "lfo", "macros", "fx", "unison", "routing", "mod", "arp", "global",
                "clip". Default: "sound_design". Max 128 parameters in total — more crashes Live.
            track: Target track (index, name, LOM path); omitted = selected track.
            plugin: Plug-in to load ("Serum 2", "Xfer Records/Serum 2"); omitted = the plug-in
                already on the track.
            macros: {"1".."16": parameter} — wire rack macro n to that parameter over its full
                range (macro 0..127 = parameter 0..1); the macro is named after it.
            preset_file: Sound to start from: a .vstpreset or a Live preset (.adv/.adg) holding
                the plug-in (see live_plugin_preset_files; .SerumPreset cannot be embedded).
            replace: Replace the plug-in (or the LiveBridge rack around it) on the track, at the
                same position. false = add a new rack.
            name: Rack name (default "<plug-in> Rack").
            new_track: Create a new MIDI track (effects: audio track) for the rack.
            track_name: Name for the new track.
            plugin_format: "VST3" (default). Audio Units load but Live exposes nothing — VST3 only.
            editor_open: true/false opens/closes the plug-in window after loading (Live opens it
                when its "Auto-Open Plug-In Windows" setting is on); null leaves it as Live did.
            wait_seconds: How long to wait while Live indexes a new rack file (first use only,
                ~3 s). 0..600.

        Returns:
            {status: "done", track, rack (path), rack_name, device (plug-in path — use it as
             `device` in live_device_*), plugin, format, exposed_count, parameters: [{index,
             name, value, display}], missing?, problems?, state ("preset_file"|"template"|
             "plugin_default"), state_note, replaced?, editor_open, macros?: {n: {parameter,
             index, macro, path, value}}, file}

        Gotchas:
            Live's API cannot read a plug-in's current sound: replacing restarts it from
            preset_file or the template ("- Init -" for Serum 2) — expose first, then design.
            Re-exposing a different parameter set also resets the sound, so pick a generous set
            up front ("sound_design" + extras). Macro moves are applied by Live on the next
            tick. One undo step per call.
        """
        cmd = "plugin_racks.expose"
        error = _check_format(plugin_format, cmd)
        if error:
            return error
        if parameters is not None and (not isinstance(parameters, list) or not all(
                isinstance(p, str) and p.strip() for p in parameters)):
            return tool_error("parameters must be a list of non-empty names / group keywords",
                              cmd=cmd)
        if not 0 <= wait_seconds <= _MAX_WAIT:
            return tool_error(f"wait_seconds must be 0..{_MAX_WAIT:g}", cmd=cmd)
        if new_track and track is not None:
            return tool_error("pass either track or new_track, not both", cmd=cmd)
        args = {**drop_none(parameters=parameters, track=track, plugin=plugin, macros=macros,
                            preset_file=preset_file, name=name, track_name=track_name,
                            plugin_format=plugin_format, editor_open=editor_open),
                "replace": replace, "new_track": new_track}
        deadline = _time.monotonic() + wait_seconds
        while True:
            result = await asyncio.to_thread(bridge_call, bridge, cmd, args, _CALL_TIMEOUT)
            if _is_error(result) or not isinstance(result, dict):
                return result
            if result.get("status") != "indexing":
                break
            if _time.monotonic() >= deadline:
                result["timed_out"] = True
                return result
            await asyncio.sleep(_POLL_SECONDS)
        if editor_open is not None and result.get("device"):
            # Live auto-opens plug-in windows on the tick after a load: set it again now
            window = await asyncio.to_thread(bridge_call, bridge, "plugins.set",
                                             {"device": result["device"],
                                              "editor_open": editor_open})
            if isinstance(window, dict) and "editor_open" in window:
                result["editor_open"] = window["editor_open"]
        return result

    @mcp.tool()
    async def live_plugin_param_map(
        plugin: str | None = None,
        track: int | str | None = None,
        device: int | str | None = None,
        plugin_format: str | None = None,
        refresh: bool = False,
        filter: str | None = None,
        offset: int = 0,
        limit: int = 50,
        wait_seconds: float = 240.0,
        cancel: bool = False,
    ) -> Any:
        """Get (or build once) a plug-in's name → VST3 ParameterId map — what
        live_plugin_expose needs — and search its parameter names.

        Serum 2's complete map (541 parameters) ships with LiveBridge, so this returns at once;
        use it to look up exact names (filter="env 2", "lfo 3", "fx bus"). For another big VST3
        plug-in call it once: LiveBridge loads probe racks (128 candidate ids each) on a
        temporary "LB_MAP" track, reads the names Live reports, cross-checks them against the
        plug-in's full name list and caches the map in the User Library (Serum 2 took 27 loads,
        ~15 s + ~5 s for Live to index the probe file). "LB_MAP" is deleted afterwards.

        Args:
            plugin: Plug-in name ("Serum 2"); or identify a loaded one with track/device.
            track: Track of a loaded plug-in (index, name, path).
            device: The plug-in device (index, name, path).
            plugin_format: "VST3" (default) / "AU" / "VST2" (probing is VST3-only).
            refresh: Probe again although a map exists (after a plug-in update).
            filter: Name filter for the returned page ("cutoff", "osc b", "macro").
            offset: Page start.
            limit: Page size 1..1000.
            wait_seconds: How long to keep probing (0..600); on timeout the probe keeps its
                progress — call again to continue.
            cancel: Abort a running probe and delete "LB_MAP".

        Returns:
            {status: "done", plugin, format, source ("shipped"|"cache"|"probed"), path, count,
             plugin_parameter_count, missing: [names not found], groups: {keyword: size},
             matched, offset, returned, parameters: [{name, id}], next_offset?}
            or {status: "running"|"indexing", found, probed, loads, timed_out: true} after
            wait_seconds.

        Gotchas:
            Probing briefly keeps one extra plug-in instance alive. Plug-ins with hashed
            ParameterIds (some JUCE builds) cannot be probed — count stays 0; tell the user.
        """
        cmd = "plugin_racks.map"
        error = _check_format(plugin_format, cmd)
        if error:
            return error
        if plugin is None and track is None and device is None:
            return tool_error("pass plugin (a name) or the track/device of a loaded plug-in",
                              cmd=cmd)
        if not 0 <= wait_seconds <= _MAX_WAIT:
            return tool_error(f"wait_seconds must be 0..{_MAX_WAIT:g}", cmd=cmd)
        if offset < 0 or not 1 <= limit <= 1000:
            return tool_error("offset must be >= 0 and limit 1..1000", cmd=cmd)
        args = {**drop_none(plugin=plugin, track=track, device=device,
                            plugin_format=plugin_format, filter=filter),
                "refresh": refresh, "offset": offset, "limit": limit,
                "max_seconds": _STEP_SECONDS, "cancel": cancel}
        deadline = _time.monotonic() + wait_seconds
        while True:
            result = await asyncio.to_thread(bridge_call, bridge, cmd, args, _CALL_TIMEOUT)
            if _is_error(result) or not isinstance(result, dict) or \
                    result.get("status") not in ("running", "indexing"):
                return result
            args["refresh"] = False     # continue the running probe, never restart it
            if _time.monotonic() >= deadline:
                result["timed_out"] = True
                return result
            if result.get("status") == "indexing":
                await asyncio.sleep(_POLL_SECONDS)

    @mcp.tool()
    def live_plugin_preset_files(
        plugin: str | None = None,
        filter: str | None = None,
        offset: int = 0,
        limit: int = 50,
        plugin_format: str | None = None,
        embeddable_only: bool = False,
    ) -> Any:
        """List a plug-in's preset files on disk, so a starting sound can be picked by name
        (pass `path` to live_plugin_expose preset_file=).

        Looks in the plug-in's own preset folders (Serum 2 macOS: ~/Documents/Xfer/Serum 2
        Presets, /Library/Audio/Presets/Xfer Records/Serum 2 Presets/Presets; Windows:
        %USERPROFILE%\\Documents\\Xfer\\Serum 2 Presets, %PUBLIC%\\Documents\\Xfer\\Serum 2
        Presets), the standard VST3 preset folders (…/VST3 Presets/<vendor>/<plug-in>),
        ~/Splice/presets and Live's User Library (Live presets that contain the plug-in).

        Args:
            plugin: Plug-in name ("Serum 2") — required.
            filter: Words that must all appear in the preset name or its folder ("bass",
                "pad warm", "factory lead").
            offset: Page start.
            limit: Page size 1..500.
            plugin_format: "VST3" (default) / "AU".
            embeddable_only: Only files live_plugin_expose can embed (.vstpreset, Live presets).

        Returns:
            {plugin, format, folders: [{path, exists}], total, matched, offset, count,
             presets: [{name, path, kind ("vstpreset"|"live_preset"|"SerumPreset"|"aupreset"),
             embeddable, category?}], next_offset?, truncated?, note?}

        Gotchas:
            Serum 2's own .SerumPreset files (all 626 factory presets) are listed with
            embeddable=false: Serum rejects them as a VST3 state. To use one, open Serum's
            window (live_plugin_set editor_open=true) and let the user load it there, or save
            the sound in Live as a preset (.adv / .vstpreset) and pass that file.
        """
        cmd = "plugin_racks.presets"
        error = _check_format(plugin_format, cmd)
        if error:
            return error
        if not isinstance(plugin, str) or not plugin.strip():
            return tool_error("plugin must be a plug-in name", cmd=cmd)
        if offset < 0 or not 1 <= limit <= 500:
            return tool_error("offset must be >= 0 and limit 1..500", cmd=cmd)
        return bridge_call(bridge, cmd, {**drop_none(filter=filter, plugin_format=plugin_format),
                                         "plugin": plugin, "offset": offset, "limit": limit,
                                         "embeddable_only": embeddable_only}, timeout=30.0)
