"""Device tools: list/inspect devices, read and write parameters (numbers, normalized values,
value names or display strings like "-12 dB"), device state, insert/duplicate/move/delete,
set-wide search and Simpler helpers.

Addressing used by every tool here (and by the plug-in and rack tools):

* ``track`` — index into the regular tracks (0-based), a track name (exact, then
  case-insensitive prefix), ``"master"``, or a LOM path like ``"song.return_tracks[0]"``.
  Omitted = the selected track.
* ``device`` — index on that track, a device name (exact, then inside racks, then class name,
  then a unique prefix), or a full LOM path such as
  ``"song.tracks[2].devices[0].chains[1].devices[0]"`` (nested rack chains; ``track`` is then
  ignored). Omitted = the track's selected device (or its only device).
"""

from __future__ import annotations

from typing import Any

from . import bridge_call, drop_none, tool_error
from ..client import BridgeClient

DETAILS = ("minimal", "summary", "full")
DEVICE_KINDS = ("plugin", "max", "rack", "drum_rack", "simpler", "sampler", "native")
DEVICE_TYPES = ("instrument", "audio_effect", "midi_effect")
SIMPLER_ACTIONS = ("crop", "reverse", "warp_as", "warp_double", "warp_half",
                   "guess_playback_length", "insert_slices", "remove_slices", "move_slice",
                   "clear_slices", "reset_slices", "replace_sample", "to_drum_rack")
MODULATION_OPS = ("get", "set", "add")


def check_detail(detail: str, cmd: str) -> dict[str, Any] | None:
    """Friendly error for a bad ``detail`` value (None when fine)."""
    if detail not in DETAILS:
        return tool_error(f"detail must be one of {', '.join(DETAILS)}, got {detail!r}", cmd=cmd)
    return None


def check_address(track: Any, device: Any, cmd: str) -> dict[str, Any] | None:
    """Reject empty strings / booleans for track and device."""
    for label, value in (("track", track), ("device", device)):
        if isinstance(value, bool):
            return tool_error(f"{label} must be an index, a name or a LOM path", cmd=cmd)
        if isinstance(value, str) and not value.strip():
            return tool_error(f"{label} must not be empty (omit it to use the selection)", cmd=cmd)
    return None


def check_paging(offset: int, limit: int, cmd: str, max_limit: int = 1000) -> dict[str, Any] | None:
    if offset < 0:
        return tool_error("offset must be >= 0", cmd=cmd)
    if not 1 <= limit <= max_limit:
        return tool_error(f"limit must be between 1 and {max_limit}", cmd=cmd)
    return None


def _address(track: Any, device: Any) -> dict[str, Any]:
    return drop_none(track=track, device=device)


def register(mcp: Any, bridge: BridgeClient) -> None:
    """Register the device tools on the MCP app."""

    # ------------------------------------------------------------------ listing

    @mcp.tool()
    def live_device_list(
        track: int | str | None = None,
        tree: bool = False,
        detail: str = "minimal",
        max_depth: int = 4,
    ) -> Any:
        """List the devices on a track, optionally as a tree through rack chains.

        Args:
            track: Track index, name, "master" or LOM path; omitted = selected track.
            tree: Also descend into rack chains (and return chains). Drum Rack chains carry
                `in_note`/`key` = the pad that triggers them (C1 = 36).
            detail: "minimal" (index, name, class_name, type, is_active, path — default),
                "summary" (+ parameter/chain/preset counts, Simpler sample) or "full"
                (+ every parameter; large — prefer live_device_parameters).
            max_depth: Rack nesting depth for `tree` (1..8).

        Returns:
            {track: {path, name}, count, selected: <path of the selected device>|null,
             devices: [{index, name, class_name, type, is_active, path, ...,
             chains?: [{index, name, path, in_note?, key?, devices: [...]}]}]}

        Gotchas:
            Live orders a chain MIDI effects → instrument → audio effects. Use the `path` values
            to address nested devices in other tools.
        """
        cmd = "devices.list"
        error = check_detail(detail, cmd) or check_address(track, None, cmd)
        if error:
            return error
        if not 1 <= max_depth <= 8:
            return tool_error("max_depth must be 1..8", cmd=cmd)
        return bridge_call(bridge, cmd, drop_none(track=track, tree=tree, detail=detail,
                                                  max_depth=max_depth))

    @mcp.tool()
    def live_device_get(
        track: int | str | None = None,
        device: int | str | None = None,
        detail: str = "summary",
        max_parameters: int = 64,
    ) -> Any:
        """Describe one device: type/class, on/off, host, selection, plug-in presets, rack chains.

        Args:
            track: Track index, name, "master" or LOM path; omitted = selected track.
            device: Device index, name or LOM path (nested chains allowed); omitted = the
                track's selected device.
            detail: "minimal", "summary" (default) or "full" (adds the first `max_parameters`
                parameters in compact form).
            max_parameters: Cap for the parameter list at "full" (1..1000).

        Returns:
            {path, index, name, class_name, class_display_name, type, is_active, device_kind
             ("native"|"plugin"|"max"|"rack"|"drum_rack"|"simpler"|"sampler"),
             parameter_count, collapsed, host, position, selected, latency_ms?, can_compare_ab?,
             compare_b?, plugin?: {preset_count, selected_preset_index, selected_preset,
             editor_open}, chains?, visible_macro_count?, variation_count?, parameters?,
             class_properties?: [names of settings that are not parameters — Wavetable
             wavetables, Hybrid Reverb IRs, EQ Eight modes ...], class_actions?: [methods]}

        Gotchas:
            Native device presets live in Live's browser (use the browser tools to hot-swap);
            only plug-ins expose a preset list (live_plugin_presets). `class_properties` are
            read/set with live_device_properties, `class_actions` run with live_device_action.
        """
        cmd = "devices.get"
        error = check_detail(detail, cmd) or check_address(track, device, cmd)
        if error:
            return error
        if not 1 <= max_parameters <= 1000:
            return tool_error("max_parameters must be 1..1000", cmd=cmd)
        return bridge_call(bridge, cmd, {**_address(track, device), "detail": detail,
                                         "max_parameters": max_parameters})

    @mcp.tool()
    def live_device_parameters(
        track: int | str | None = None,
        device: int | str | None = None,
        filter: str | None = None,
        offset: int = 0,
        limit: int = 64,
        only_changed: bool = False,
        detail: str = "summary",
    ) -> Any:
        """List a device's parameters with their current and displayed values (paged).

        Args:
            track: Track index, name, "master" or LOM path; omitted = selected track.
            device: Device index, name or LOM path; omitted = selected device.
            filter: Case-insensitive substring of the parameter name ("freq", "attack").
            offset: Start of the page (0-based, over the filtered list).
            limit: Page size 1..1000 (default 64).
            only_changed: Only continuous parameters whose value differs from the default.
            detail: "minimal" (index, name, value, display), "summary" (default: + min, max,
                quantized + items, enabled=false, automation, original_name) or "full"
                (+ path, default, normalized, state).

        Returns:
            {device: {path, name, class_name}, total, matched, offset, count,
             parameters: [{index, name, value, display, min, max, quantized?, items?, ...}],
             next_offset?}

        Gotchas:
            `value` is Live's internal value (often 0..1 even for dB/Hz/ms knobs); `display` is
            what the GUI shows. parameters[0] is always "Device On". Pass `display` strings back
            to live_device_set_parameter to set values the way a human would (compound displays
            such as Serum 2's "50% [-9.0 dB]" take either reading: "70%" or "-6 dB").
            Utility's gain knob is the parameter "Output" (-inf..+35 dB) in Live 12.4.5.
            Sampler (class MultiSampler) exposes parameters only — no zones or samples (use
            Simpler: live_sample_import mode="simpler" / live_simpler_*); a Drum Sampler
            (DrumCellDevice) exposes gain only, no sample path. Plug-ins expose only their
            Configure list — live_plugin_expose for big VST3s such as Serum 2.
        """
        cmd = "devices.parameters"
        error = check_detail(detail, cmd) or check_address(track, device, cmd) or \
            check_paging(offset, limit, cmd)
        if error:
            return error
        return bridge_call(bridge, cmd, {**_address(track, device), **drop_none(filter=filter),
                                         "offset": offset, "limit": limit,
                                         "only_changed": only_changed, "detail": detail})

    @mcp.tool()
    def live_device_get_parameter(
        parameter: int | str,
        track: int | str | None = None,
        device: int | str | None = None,
    ) -> Any:
        """Read one parameter in full detail (value, display text, range, default, items).

        Args:
            parameter: Index, name (exact, case-insensitive, original name, then a unique
                prefix/substring) or a full LOM path (then track/device are not needed — works
                for mixer parameters like "song.tracks[0].mixer_device.volume" too).
            track: Track index, name, "master" or LOM path; omitted = selected track.
            device: Device index, name or LOM path; omitted = selected device.

        Returns:
            {index, name, value, display, min, max, quantized?, items?, default?, normalized,
             state, automation?, enabled?, original_name?, path, device?}
        """
        cmd = "devices.get_parameter"
        error = check_address(track, device, cmd)
        if error:
            return error
        if isinstance(parameter, str) and not parameter.strip():
            return tool_error("parameter must be an index, a name or a LOM path", cmd=cmd)
        return bridge_call(bridge, cmd, {**_address(track, device), "parameter": parameter})

    # ------------------------------------------------------------------ writing

    @mcp.tool()
    def live_device_set_parameter(
        parameter: int | str,
        value: float | str | bool,
        track: int | str | None = None,
        device: int | str | None = None,
        normalized: bool = False,
    ) -> Any:
        """Set one device parameter — by raw number, normalized 0..1, value name or display text.

        Args:
            parameter: Index, name or LOM path (see live_device_get_parameter).
            value: How to read it:
                - number → Live's internal value, clamped to [min, max];
                - number with normalized=true → 0..1 across the range;
                - true/false → max/min (switches);
                - string → a value item for menus/switches ("Saw", "On", "Low Pass", "1/8")
                  or the text the knob shows: "-12 dB", "250 ms", "1.5 kHz", "40 %", "25L",
                  "C", "2.50 s". Display text is matched by scanning the parameter's own
                  display over its range and bisecting, so it works for any curve.
            track: Track index, name, "master" or LOM path; omitted = selected track.
            device: Device index, name or LOM path; omitted = selected device.
            normalized: Treat a numeric value as 0..1.

        Returns:
            {index, name, path, value, display, previous (display before), matched?
             ("item"|"display"|"normalized"), clamped?}

        Gotchas:
            Use the units the parameter itself displays (read it first). Parameters mapped to
            a rack macro (enabled=false) cannot be set — set the macro (live_rack_macros).
            Each call is one undo step.
        """
        cmd = "devices.set_parameter"
        error = check_address(track, device, cmd)
        if error:
            return error
        if isinstance(parameter, str) and not parameter.strip():
            return tool_error("parameter must be an index, a name or a LOM path", cmd=cmd)
        if isinstance(value, str) and not value.strip():
            return tool_error("value must not be empty", cmd=cmd)
        return bridge_call(bridge, cmd, {**_address(track, device), "parameter": parameter,
                                         "value": value, "normalized": normalized})

    @mcp.tool()
    def live_device_set_parameters(
        values: dict[str, float | str | bool] | list[dict[str, Any]],
        track: int | str | None = None,
        device: int | str | None = None,
        normalized: bool = False,
    ) -> Any:
        """Set many parameters at once, atomically (all validated first; one undo step).

        Args:
            values: Either {"Filter Freq": "2 kHz", "Resonance": 0.3, "5": "Saw"} (keys are
                names or indices) or a list [{"parameter": <index|name|LOM path>, "value": ...,
                "normalized"?: bool}]. List entries with full LOM paths may target different
                devices (then track/device can be omitted). Values follow the same rules as
                live_device_set_parameter.
            track: Track index, name, "master" or LOM path; omitted = selected track.
            device: Device index, name or LOM path; omitted = selected device.
            normalized: Default for numeric values (a list entry's "normalized" wins).

        Returns:
            {device?, count, parameters: [{index, name, value, display, matched?, clamped?}]}

        Gotchas:
            If any value cannot be resolved nothing is written; if Live refuses a write midway
            the earlier ones are rolled back. Max 512 entries.
        """
        cmd = "devices.set_parameters"
        error = check_address(track, device, cmd)
        if error:
            return error
        if not values:
            return tool_error("values must not be empty", cmd=cmd)
        return bridge_call(bridge, cmd, {**_address(track, device), "values": values,
                                         "normalized": normalized})

    @mcp.tool()
    def live_device_reset_parameters(
        track: int | str | None = None,
        device: int | str | None = None,
        parameters: list[int | str] | None = None,
    ) -> Any:
        """Reset device parameters to Live's default values.

        Args:
            track: Track index, name, "master" or LOM path; omitted = selected track.
            device: Device index, name or LOM path; omitted = selected device.
            parameters: Indices/names to reset; omitted = every parameter except "Device On".

        Returns:
            {device, reset: [{index, name, display}], skipped: [{index, name, reason}]}

        Gotchas:
            Live has no default for quantized parameters (switches, menus) and they are
            skipped (reason "quantized") — set them explicitly. Macro-mapped parameters are
            skipped too.
        """
        cmd = "devices.reset_parameters"
        error = check_address(track, device, cmd)
        if error:
            return error
        if parameters is not None and not parameters:
            return tool_error("parameters must be a non-empty list (or omitted for all)", cmd=cmd)
        return bridge_call(bridge, cmd, {**_address(track, device),
                                         **drop_none(parameters=parameters)})

    @mcp.tool()
    def live_device_randomize(
        track: int | str | None = None,
        device: int | str | None = None,
        parameters: list[int | str] | None = None,
        seed: int | None = None,
        amount: float = 1.0,
        include_quantized: bool = True,
    ) -> Any:
        """Randomize a device's parameters (whole range or a gentle nudge), reproducibly.

        Args:
            track: Track index, name, "master" or LOM path; omitted = selected track.
            device: Device index, name or LOM path; omitted = selected device.
            parameters: Indices/names to randomize; omitted = all except "Device On",
                macro-mapped parameters and a rack's "Chain Selector".
            seed: Integer seed for reproducible results (the used seed is returned).
            amount: 1.0 = anywhere in the range; 0 < amount < 1 = move each value by up to
                amount × range from where it is (subtle variations).
            include_quantized: Also randomize switches/menus (when parameters is omitted).

        Returns:
            {device, seed, changed, parameters: [{index, name, display}]}

        Gotchas:
            One undo step, so undo restores everything. For racks, live_rack_variations with
            action="randomize" uses Live's own macro randomization.
        """
        cmd = "devices.randomize"
        error = check_address(track, device, cmd)
        if error:
            return error
        if not 0.0 < amount <= 1.0:
            return tool_error("amount must be in (0, 1]", cmd=cmd)
        if parameters is not None and not parameters:
            return tool_error("parameters must be a non-empty list (or omitted for all)", cmd=cmd)
        return bridge_call(bridge, cmd, {**_address(track, device),
                                         **drop_none(parameters=parameters, seed=seed),
                                         "amount": amount, "include_quantized": include_quantized})

    # ------------------------------------------------------------------ state / editing

    @mcp.tool()
    def live_device_set_state(
        track: int | str | None = None,
        device: int | str | None = None,
        active: bool | str | None = None,
        name: str | None = None,
        collapsed: bool | None = None,
        select: bool | None = None,
        compare_b: bool | None = None,
        show_chains: bool | None = None,
        save_to_compare_slot: bool = False,
    ) -> Any:
        """Turn a device on/off, rename it, fold it, select it, or save/switch A/B compare.

        Args:
            track: Track index, name, "master" or LOM path; omitted = selected track.
            device: Device index, name or LOM path; omitted = selected device.
            active: true/false, or "toggle" — flips the device's "Device On" switch.
            name: New device name (title bar).
            collapsed: Fold (true) / unfold (false) the device in the device chain.
            select: true = select the device (and its track) and show the device chain.
            compare_b: Live 12.3+ A/B compare: true = slot B, false = slot A (native devices
                with can_compare_ab only).
            show_chains: Racks only: show/hide the chain list.
            save_to_compare_slot: true stores the device's CURRENT state in the A/B compare slot
                (before any `compare_b` switch in the same call) — a snapshot to come back to;
                `compare_b` then switches between the two slots. `invalid_state` when the
                device cannot compare (plug-ins, Live before 12.3).

        Returns:
            {path, name, is_active, collapsed, selected, compare_b?, show_chains?,
             saved_to_compare_slot?}

        Gotchas:
            `is_active` is also false when an enclosing rack is off. Live cannot save
            device/rack/plug-in presets (.adv/.adg/.fxp) through the API — the user saves them
            from the device title bar; the A/B slot is the only snapshot. One undo step.
        """
        cmd = "devices.set_state"
        error = check_address(track, device, cmd)
        if error:
            return error
        if isinstance(active, str) and active.strip().lower() not in ("toggle", "on", "off",
                                                                      "true", "false"):
            return tool_error('active must be true, false or "toggle"', cmd=cmd)
        if name is not None and not name.strip():
            return tool_error("name must not be empty", cmd=cmd)
        return bridge_call(bridge, cmd, {**_address(track, device), **drop_none(
            active=active, name=name, collapsed=collapsed, select=select, compare_b=compare_b,
            show_chains=show_chains, save_to_compare_slot=save_to_compare_slot or None)})

    @mcp.tool()
    def live_device_delete(
        device: int | str,
        track: int | str | None = None,
    ) -> Any:
        """Delete a device from its track or rack chain.

        Args:
            device: Device index, name or LOM path (required, to avoid deleting the wrong one).
            track: Track index, name, "master" or LOM path; omitted = selected track.

        Returns:
            {deleted: {name, class_name, path}, host: <track/chain path>, devices: [remaining
             names in order]}

        Gotchas:
            Devices after it move down one index. Undo in Live restores it.
        """
        cmd = "devices.delete"
        error = check_address(track, device, cmd)
        if error:
            return error
        return bridge_call(bridge, cmd, _address(track, device))

    @mcp.tool()
    def live_device_insert(
        name: str,
        track: int | str | None = None,
        chain: str | None = None,
        index: int = -1,
        select: bool = False,
    ) -> Any:
        """Insert a built-in Live device by name, without the browser (Live 12.3+).

        Args:
            name: The device's name as in Live's browser: "EQ Eight", "Compressor", "Utility",
                "Reverb", "Delay", "Auto Filter", "Saturator", "Limiter", "Glue Compressor",
                "Operator", "Wavetable", "Drift", "Meld", "Simpler", "Sampler", "Drum Rack",
                "Instrument Rack", "Audio Effect Rack", "MIDI Effect Rack", "Arpeggiator",
                "Chord", "Scale" ... Case and spacing don't matter and class names work too
                ("eq eight", "Eq8", "StereoGain"); the answer's `requested` shows a translation.
            track: Target track; omitted = selected track.
            chain: LOM path of a rack chain to insert into instead of a track
                (e.g. "song.tracks[0].devices[1].chains[0]").
            index: Position in the chain, -1 = end.
            select: Select the new device afterwards.

        Returns:
            The new device's summary including its `path`; `via` is "insert_device" or
            "browser" (Max for Live based devices).

        Gotchas:
            Live's insert_device only creates native devices. The Max for Live based ones in
            Live's device lists (LFO, Shaper, Envelope Follower, Drum Sampler, DS Kick ...,
            Note Echo, MIDI Monitor ...) are loaded through the browser instead — only at the
            end of a track, not into a chain or at an index. Plug-ins and presets: browser
            tools. Live enforces MIDI effects → instrument → audio effects, one instrument per
            chain and audio effects only on audio tracks (invalid_state with Live's reason).
            Unknown names answer not_found with close matches. Older Live: "unsupported".
            Utility's gain knob is the parameter "Output" (-inf..+35 dB) in 12.4.5. Sampler
            (MultiSampler) exposes parameters only — no zones/samples through the API (use
            Simpler for samples); a Drum Sampler exposes gain only, no sample path. To group
            devices into a rack: insert "Audio Effect Rack"/"Instrument Rack" here, add a chain
            with live_rack_insert_chain, then live_device_move(target_chain=...).
        """
        cmd = "devices.insert"
        if not name.strip():
            return tool_error("name must be a device name such as 'EQ Eight'", cmd=cmd)
        if index < -1:
            return tool_error("index must be -1 (end) or >= 0", cmd=cmd)
        if chain is not None and not chain.strip().startswith("song."):
            return tool_error("chain must be a LOM path like 'song.tracks[0].devices[1].chains[0]'",
                              cmd=cmd)
        error = check_address(track, None, cmd)
        if error:
            return error
        return bridge_call(bridge, cmd, {"name": name.strip(),
                                         **drop_none(track=track, chain=chain),
                                         "index": index, "select": select})

    @mcp.tool()
    def live_device_duplicate(
        track: int | str | None = None,
        device: int | str | None = None,
    ) -> Any:
        """Duplicate a device; the copy is placed right after the original.

        Args:
            track: Track index, name, "master" or LOM path; omitted = selected track.
            device: Device index, name or LOM path; omitted = selected device.

        Returns:
            The copy's summary (with its `path`).
        """
        cmd = "devices.duplicate"
        error = check_address(track, device, cmd)
        if error:
            return error
        return bridge_call(bridge, cmd, _address(track, device))

    @mcp.tool()
    def live_device_move(
        device: int | str,
        track: int | str | None = None,
        target_track: int | str | None = None,
        target_chain: str | None = None,
        position: int = -1,
    ) -> Any:
        """Move a device to another position, another track, or into/out of a rack chain.

        Args:
            device: Device index, name or LOM path.
            track: Track of the device (when `device` is not a path); omitted = selected track.
            target_track: Destination track; omitted (with no target_chain) = same track/chain.
            target_chain: LOM path of a destination rack chain instead.
            position: Index in the destination chain; 0 = first, -1 = end.

        Returns:
            {moved: {name, path}, position, host}

        Gotchas:
            Live picks the nearest legal position when the requested one breaks the device
            order, and refuses devices the destination cannot hold (e.g. an instrument onto an
            audio track) with invalid_state. `target_chain` moves a device INTO a rack chain
            (Song.move_device, verified on Live 12.4.5) — the way to group devices into a rack,
            together with live_device_insert("Audio Effect Rack") and live_rack_insert_chain.
        """
        cmd = "devices.move"
        error = check_address(track, device, cmd) or check_address(target_track, None, cmd)
        if error:
            return error
        if position < -1:
            return tool_error("position must be -1 (end) or >= 0", cmd=cmd)
        if target_chain is not None and not target_chain.strip().startswith("song."):
            return tool_error("target_chain must be a LOM path of a rack chain", cmd=cmd)
        return bridge_call(bridge, cmd, {**_address(track, device), **drop_none(
            target_track=target_track, target_chain=target_chain), "position": position})

    @mcp.tool()
    def live_device_find(
        query: str | None = None,
        class_name: str | None = None,
        kind: str | None = None,
        type: str | None = None,
        include_nested: bool = True,
        include_returns: bool = True,
        limit: int = 100,
    ) -> Any:
        """Find devices anywhere in the set (all tracks, returns, master, inside racks).

        Args:
            query: Case-insensitive substring of the device name, class name or browser name
                ("reverb", "serum", "eq").
            class_name: Exact Live class, e.g. "OriginalSimpler", "Eq8", "Compressor2",
                "PluginDevice", "AuPluginDevice", "InstrumentGroupDevice".
            kind: "plugin", "max", "rack" (includes drum racks), "drum_rack", "simpler",
                "sampler" or "native".
            type: "instrument", "audio_effect" or "midi_effect".
            include_nested: Look inside rack chains.
            include_returns: Also search return tracks and the master track.
            limit: Max results (1..1000).

        Returns:
            {count, truncated?, devices: [{path, track, name, class_name, type, kind, is_active,
             depth}]} — use the paths with the other device tools.
        """
        cmd = "devices.find"
        if kind is not None and kind not in DEVICE_KINDS:
            return tool_error(f"kind must be one of {', '.join(DEVICE_KINDS)}", cmd=cmd)
        if type is not None and type not in DEVICE_TYPES:
            return tool_error(f"type must be one of {', '.join(DEVICE_TYPES)}", cmd=cmd)
        if not 1 <= limit <= 1000:
            return tool_error("limit must be 1..1000", cmd=cmd)
        return bridge_call(bridge, cmd, {**drop_none(query=query, class_name=class_name,
                                                     kind=kind, type=type),
                                         "include_nested": include_nested,
                                         "include_returns": include_returns, "limit": limit})

    # ------------------------------------------------------------------ Simpler

    @mcp.tool()
    def live_simpler_get(
        track: int | str | None = None,
        device: int | str | None = None,
        max_slices: int = 64,
    ) -> Any:
        """Inspect a Simpler: loaded sample (file, length, markers, warp), slices and modes.

        Args:
            track: Track index, name, "master" or LOM path; omitted = selected track.
            device: The Simpler (index, name or LOM path — for a drum pad's Simpler use the
                path from live_drumrack_overview full_paths or live_device_find kind="simpler").
            max_slices: How many slice positions to return (0..4096).

        Returns:
            {device, playback_mode ("classic"|"one_shot"|"slicing"), slicing_playback_mode
             ("mono"|"poly"|"thru"), voices, retrigger, pad_slicing, multi_sample_mode,
             can_warp_as/double/half, empty, sample?: {file_path, file_name, length (frames),
             sample_rate, seconds, start_marker, end_marker, gain, gain_display, warping,
             warp_mode, slicing_style, slicing_sensitivity, slicing_beat_division,
             slicing_region_count, slice_count, slices (frames)}, selected_slice}

        Gotchas:
            Sampler (MultiSampler) has no sample API in Live — use its parameters instead.
        """
        cmd = "simpler.get"
        error = check_address(track, device, cmd)
        if error:
            return error
        if not 0 <= max_slices <= 4096:
            return tool_error("max_slices must be 0..4096", cmd=cmd)
        return bridge_call(bridge, cmd, {**_address(track, device), "max_slices": max_slices})

    @mcp.tool()
    def live_simpler_set(
        track: int | str | None = None,
        device: int | str | None = None,
        playback_mode: str | int | None = None,
        slicing_playback_mode: str | int | None = None,
        voices: int | None = None,
        retrigger: bool | None = None,
        pad_slicing: bool | None = None,
        warping: bool | None = None,
        warp_mode: str | int | None = None,
        slicing_style: str | int | None = None,
        slicing_sensitivity: float | None = None,
        slicing_beat_division: str | int | None = None,
        slicing_region_count: int | None = None,
        start_marker: int | None = None,
        end_marker: int | None = None,
        gain: float | None = None,
    ) -> Any:
        """Change Simpler modes and sample settings (every argument optional).

        Args:
            track: Track index, name, "master" or LOM path; omitted = selected track.
            device: The Simpler (index, name or LOM path); omitted = selected device.
            playback_mode: "classic", "one_shot" or "slicing".
            slicing_playback_mode: "mono", "poly" or "thru".
            voices: Polyphony (Live accepts its own steps, typically 1..32).
            retrigger: Retrigger on/off.
            pad_slicing: Allow adding slices by playing unassigned notes.
            warping: Warp the sample on/off.
            warp_mode: "beats", "tones", "texture", "repitch", "complex" or "complex_pro".
            slicing_style: "transient", "beat", "region" or "manual".
            slicing_sensitivity: 0..1 (transient slicing).
            slicing_beat_division: "1/16", "1/16T", "1/8", "1/8T", "1/4", "1/4T", "1/2", "1/2T",
                "1 bar", "2 bars" or "4 bars" (beat slicing).
            slicing_region_count: Number of regions (region slicing).
            start_marker: Sample start in frames.
            end_marker: Sample end in frames.
            gain: Sample gain (internal value 0..1).

        Returns:
            The live_simpler_get shape after the change.

        Gotchas:
            Sample settings need a loaded sample (load one with live_simpler_action
            action="replace_sample").
        """
        cmd = "simpler.set"
        error = check_address(track, device, cmd)
        if error:
            return error
        if slicing_sensitivity is not None and not 0.0 <= slicing_sensitivity <= 1.0:
            return tool_error("slicing_sensitivity must be 0..1", cmd=cmd)
        if voices is not None and voices < 1:
            return tool_error("voices must be >= 1", cmd=cmd)
        for label, value in (("slicing_region_count", slicing_region_count),
                             ("start_marker", start_marker), ("end_marker", end_marker)):
            if value is not None and value < 0:
                return tool_error(f"{label} must be >= 0", cmd=cmd)
        settings = drop_none(
            playback_mode=playback_mode, slicing_playback_mode=slicing_playback_mode,
            voices=voices, retrigger=retrigger, pad_slicing=pad_slicing, warping=warping,
            warp_mode=warp_mode, slicing_style=slicing_style,
            slicing_sensitivity=slicing_sensitivity, slicing_beat_division=slicing_beat_division,
            slicing_region_count=slicing_region_count, start_marker=start_marker,
            end_marker=end_marker, gain=gain)
        if not settings:
            return tool_error("pass at least one setting to change (use live_simpler_get to read)",
                              cmd=cmd)
        return bridge_call(bridge, cmd, {**_address(track, device), **settings})

    @mcp.tool()
    def live_simpler_action(
        action: str,
        track: int | str | None = None,
        device: int | str | None = None,
        beats: float | None = None,
        slices: list[float] | None = None,
        unit: str = "frames",
        file_path: str | None = None,
        old_time: float | None = None,
        new_time: float | None = None,
    ) -> Any:
        """Run a Simpler operation: crop, reverse, warp, slice editing or loading a new sample.

        Args:
            action: One of
                "replace_sample" (load `file_path` — an absolute path to a .wav/.aif/.mp3/...;
                  Windows "C:\\..." and macOS "/Users/..." both work),
                "crop" (keep only start..end marker), "reverse",
                "warp_as" (warp the start..end region to `beats`; default = Live's guess),
                "warp_double", "warp_half",
                "guess_playback_length" (estimate in beats, changes nothing),
                "insert_slices" / "remove_slices" (`slices` = list of positions),
                "move_slice" (`old_time` → `new_time`), "clear_slices" (manual slices),
                "reset_slices",
                "to_drum_rack" (Live 12: a Simpler in playback_mode "slicing" becomes a Drum
                  Rack with one pad per slice from C1 up — the Simpler is replaced; the answer
                  is {action, via, rack:{path, name, pads}}).
            track: Track index, name, "master" or LOM path; omitted = selected track.
            device: The Simpler (index, name or LOM path); omitted = selected device.
            beats: Length for warp_as.
            slices: Positions for insert_slices/remove_slices.
            unit: Unit of slices/old_time/new_time: "frames" (default), "seconds" or "beats"
                (beats need a warped sample).
            file_path: For replace_sample.
            old_time: For move_slice.
            new_time: For move_slice.

        Returns:
            {action, result? (e.g. the guessed length or the slice frames), simpler: <the
             live_simpler_get shape>}

        Gotchas:
            Everything except replace_sample fails on an empty Simpler. Slices only matter in
            playback_mode "slicing". The file must exist on the machine running Live.
        """
        cmd = "simpler.action"
        if action not in SIMPLER_ACTIONS:
            return tool_error(f"action must be one of {', '.join(SIMPLER_ACTIONS)}", cmd=cmd)
        if unit not in ("frames", "seconds", "beats"):
            return tool_error("unit must be 'frames', 'seconds' or 'beats'", cmd=cmd)
        error = check_address(track, device, cmd)
        if error:
            return error
        if action == "replace_sample" and not (file_path and file_path.strip()):
            return tool_error("replace_sample needs file_path (absolute path to an audio file)",
                              cmd=cmd)
        if action in ("insert_slices", "remove_slices") and not slices:
            return tool_error(f"{action} needs slices (a list of positions)", cmd=cmd)
        if action == "move_slice" and (old_time is None or new_time is None):
            return tool_error("move_slice needs old_time and new_time", cmd=cmd)
        if beats is not None and beats <= 0:
            return tool_error("beats must be > 0", cmd=cmd)
        return bridge_call(bridge, cmd, {"action": action, **_address(track, device),
                                         **drop_none(beats=beats, slices=slices,
                                                     file_path=file_path, old_time=old_time,
                                                     new_time=new_time),
                                         "unit": unit})

    # ------------------------------------------------------------ class-specific extras

    @mcp.tool()
    def live_device_properties(
        track: int | str | None = None,
        device: int | str | None = None,
        values: dict[str, Any] | None = None,
        include_lists: bool = False,
    ) -> Any:
        """Read or set a device's class-specific properties — the settings that are NOT
        automatable parameters: Wavetable's oscillator wavetable category/index, effect
        and unison modes, voices, filter routing; Drift's mod matrix sources/targets and
        voice mode; Meld's engine and voices; Hybrid Reverb's IR category/file and IR
        shaping; EQ Eight's edit mode (A/B), global mode (stereo/L-R/M-S) and
        oversampling; Looper's record length; Roar/Shifter/Spectral Resonator modes;
        CC Control custom targets; a Simpler's pitch-bend ranges and its sample's
        warp-engine settings ("sample.texture_grain_size", "sample.complex_pro_formants" ...).

        Args:
            track: Track index, name, "master" or LOM path; omitted = selected track.
            device: Device index, name or LOM path; omitted = selected device.
            values: omit to read. To set: {name: value}, e.g.
                {"oscillator_1_wavetable_category": "Basics",
                 "oscillator_1_wavetable_index": "Saw"}, {"ir_category_index": "Halls"},
                {"edit_mode": "b", "oversample": true}, {"unison_mode": "classic"}. Index
                properties take the position or a name from `choices`; enum properties
                their name or number. Keys are applied in order — put a category before
                the item that depends on it.
            include_lists: reading — also list the raw `*_list` properties.

        Returns:
            Read: `{device, properties:[{name, value, writable, choices?, value_name?}],
            sample_properties?:[...], actions:[method names for live_device_action]}`.
            Set: `{device, changed:[{name, was, value, value_name?}], properties}`.

        Gotchas: discovered from Live's class at runtime — only devices with such extras
        list anything (most effects have none: use live_device_parameters). Every name and
        value type is validated before the first write. Wavetable modulation amounts:
        live_device_modulation.
        """
        cmd = "devices.set_properties" if values is not None else "devices.properties"
        error = check_address(track, device, cmd)
        if error:
            return error
        if values is not None:
            if not values:
                return tool_error("values must name at least one property", cmd=cmd)
            if include_lists:
                return tool_error("include_lists only applies when reading", cmd=cmd)
            return bridge_call(bridge, cmd, {"values": values, **_address(track, device)})
        args = _address(track, device)
        if include_lists:
            args["include_lists"] = True
        return bridge_call(bridge, cmd, args)

    @mcp.tool()
    def live_device_action(
        action: str,
        track: int | str | None = None,
        device: int | str | None = None,
        args: list[Any] | None = None,
        target_track: int | str | None = None,
        slot: int | str | None = None,
        parameter: int | str | None = None,
    ) -> Any:
        """Run one of a device's own methods (listed as `actions` by live_device_properties /
        `class_actions` by live_device_get): Looper "record", "overdub", "play", "stop",
        "clear", "undo", "double_length", "half_length", "double_speed", "half_speed",
        "export_to_clip_slot"; CC Control "resend"; Max for Live "get_bank_count",
        "get_bank_name", "get_bank_parameters"; Wavetable "is_parameter_modulatable";
        "save_preset_to_compare_ab_slot" on devices with A/B compare.

        Args:
            action: the method name.
            track: Track index, name, "master" or LOM path; omitted = selected track.
            device: Device index, name or LOM path; omitted = selected device.
            args: extra positional arguments (numbers/strings; "song...." paths become
                those objects), e.g. [0] for get_bank_name.
            target_track, slot: Looper "export_to_clip_slot" — the empty session slot
                (track + slot index or scene name) that receives the loop as a clip.
            parameter: for methods taking a parameter of this device (index/name/path).

        Returns:
            `{device, action, result}`

        Gotchas: Looper records the audio arriving at its track and follows its own
        quantization; only the device class's own methods are allowed.
        """
        cmd = "devices.action"
        if not action or not action.strip():
            return tool_error("action must be a method name (see live_device_properties)",
                              cmd=cmd)
        error = check_address(track, device, cmd)
        if error:
            return error
        if (target_track is None) != (slot is None):
            return tool_error("pass target_track and slot together", cmd=cmd)
        return bridge_call(bridge, cmd, {"action": action.strip(), **_address(track, device),
                                         **drop_none(args=args, target_track=target_track,
                                                     slot=slot, parameter=parameter)})

    @mcp.tool()
    def live_device_modulation(
        op: str = "get",
        track: int | str | None = None,
        device: int | str | None = None,
        target: int | str | None = None,
        source: int | str | None = None,
        amount: float | None = None,
        parameter: int | str | None = None,
    ) -> Any:
        """Wavetable's modulation matrix: read it, set one amount, or add a parameter as a
        matrix target.

        Args:
            op: "get" (default), "set" or "add".
            track: Track index, name or LOM path; omitted = selected track.
            device: The Wavetable (index, name or LOM path); omitted = selected device.
            target: "set" — target index or name from `targets` (e.g. "Osc 1 Pos").
            source: "set" — "amp_envelope", "envelope_2", "envelope_3", "lfo_1", "lfo_2",
                "midi_velocity", "midi_note", "midi_pitch_bend", "midi_channel_pressure",
                "midi_mod_wheel", "midi_random" (or 0-10).
            amount: "set" — the amount (Live's internal value, typically -1..1).
            parameter: "add" — a parameter of the Wavetable (name, index or path) to make
                a modulation target (not pitch parameters).

        Returns:
            `{device, sources, targets:[{index, name, amounts:{source: value}}],
            changed?:{target, source, was, amount}, added?:{parameter, target_index}}`

        Gotchas: Wavetable only (other devices: `unsupported`); Drift's matrix routing is
        in live_device_properties (mod_matrix_*). Only non-zero amounts are listed.
        """
        cmd = "devices.modulation"
        if op not in MODULATION_OPS:
            return tool_error(f"op must be one of {', '.join(MODULATION_OPS)}", cmd=cmd)
        error = check_address(track, device, cmd)
        if error:
            return error
        if op == "set" and (target is None or source is None or amount is None):
            return tool_error("op='set' needs target, source and amount", cmd=cmd)
        if op == "add" and parameter is None:
            return tool_error("op='add' needs parameter", cmd=cmd)
        return bridge_call(bridge, cmd, {"op": op, **_address(track, device),
                                         **drop_none(target=target, source=source,
                                                     amount=amount, parameter=parameter)})
