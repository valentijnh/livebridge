"""Rack tools (Instrument / Audio Effect / MIDI Effect / Drum Racks): chains and their mixers,
macros, macro variations, chain selector, and Drum Rack pads.

Addressing: `track` + `device` as in the device tools. With `device` omitted the selected
device is used when it is a rack, otherwise the first rack on the track (top level first, then
nested) — for the drum tools the first Drum Rack.

Drum pad notes use Live's octave numbering: C1 = 36 (the usual kick pad), D1 = 38, F#1 = 42,
C3 = 60. Pads can also be addressed by name ("Kick").
"""

from __future__ import annotations

from typing import Any

from . import bridge_call, drop_none, tool_error
from ..client import BridgeClient
from .devices import check_address, check_detail

VARIATION_ACTIONS = ("list", "store", "recall", "recall_last", "select", "delete", "randomize")


def register(mcp: Any, bridge: BridgeClient) -> None:
    """Register the rack tools on the MCP app."""

    @mcp.tool()
    def live_rack_chains(
        track: int | str | None = None,
        device: int | str | None = None,
        include_return_chains: bool = True,
        detail: str = "summary",
    ) -> Any:
        """List a rack's chains: name, mute/solo, volume/pan (with dB text), devices.

        Args:
            track: Track index, name, "master" or LOM path; omitted = selected track.
            device: Rack index, name or LOM path; omitted = selected/first rack on the track.
            include_return_chains: Also list the rack's return chains.
            detail: "minimal" (index, name, path, mute, solo, in_note/key), "summary"
                (default: + volume/panning and their display, active, device names, Drum Rack
                out_note/choke_group) or "full" (+ sends, device entries with paths, color).

        Returns:
            {rack: {path, name, class_name}, chain_count, selected_chain?, chain_selector?:
             {value, display}, chains: [{index, name, path, mute, solo, volume, volume_display,
             panning, panning_display, devices, in_note?, key?, ...}], return_chains?}

        Gotcha: Live's API cannot delete, duplicate or reorder chains (RackDevice
            has only insert_chain) and has no key/velocity/chain-select zones — mute the chain
            or set its activator off instead; only the chain selector value is settable
            (live_rack_macros chain_selector).
        """
        cmd = "racks.chains"
        error = check_detail(detail, cmd) or check_address(track, device, cmd)
        if error:
            return error
        return bridge_call(bridge, cmd, {**drop_none(track=track, device=device),
                                         "include_return_chains": include_return_chains,
                                         "detail": detail})

    @mcp.tool()
    def live_rack_set_chain(
        chain: int | str,
        track: int | str | None = None,
        device: int | str | None = None,
        name: str | None = None,
        mute: bool | None = None,
        solo: bool | None = None,
        exclusive_solo: bool = False,
        volume: float | str | None = None,
        panning: float | str | None = None,
        active: bool | None = None,
        sends: dict[str, float | str] | None = None,
        color_index: int | None = None,
        select: bool | None = None,
        in_note: int | str | None = None,
        out_note: int | str | None = None,
        choke_group: int | None = None,
    ) -> Any:
        """Change one rack chain: name, mute/solo, volume/pan, activator, sends, select, drum notes.

        Args:
            chain: Chain index, name, or LOM path ("song.tracks[0].devices[1].chains[2]").
            track: Track of the rack (when `chain` is not a path); omitted = selected track.
            device: The rack (when `chain` is not a path); omitted = selected/first rack.
            name: Rename the chain (in a Drum Rack this renames the pad).
            mute: Mute the chain.
            solo: Solo the chain; with exclusive_solo=true the other chains are un-soloed.
            exclusive_solo: See solo.
            volume: Raw 0..1 (0.85 = 0 dB) or display text like "-6 dB".
            panning: Raw -1..1 or display text "25L", "C", "10R".
            active: Chain activator on/off — the same switch as `mute` in Live
                (active=false is mute=true).
            sends: {send index or name: value} for the rack's return chains (raw or "dB" text).
            color_index: Live colour palette index.
            select: Select the chain (rack view and Live's selection).
            in_note: Drum Racks, Live 12.3+: the pad note that triggers the chain (0..127 or
                "C1"); moving it moves the chain to that pad. Live has no "all notes" value
                through the API (-1 is refused).
            out_note: Drum Racks: note sent to the chain's devices (0..127, e.g. 60 / "C3").
            choke_group: Drum Racks: 0 (none) .. 16.

        Returns:
            The chain in full detail after the change.

        Gotcha: Live's API cannot delete, duplicate or reorder chains (RackDevice
            has only insert_chain) and has no key/velocity/chain-select zones — mute the chain
            or set its activator off instead; only the chain selector value is settable
            (live_rack_macros chain_selector).
        """
        cmd = "racks.set_chain"
        error = check_address(track, device, cmd)
        if error:
            return error
        if isinstance(chain, str) and not chain.strip():
            return tool_error("chain must be an index, a name or a LOM path", cmd=cmd)
        if name is not None and not name.strip():
            return tool_error("name must not be empty", cmd=cmd)
        if choke_group is not None and not 0 <= choke_group <= 16:
            return tool_error("choke_group must be 0..16", cmd=cmd)
        changes = drop_none(name=name, mute=mute, solo=solo, volume=volume, panning=panning,
                            active=active, sends=sends, color_index=color_index, select=select,
                            in_note=in_note, out_note=out_note, choke_group=choke_group)
        if not changes:
            return tool_error("pass at least one setting (use live_rack_chains to read)", cmd=cmd)
        if exclusive_solo:
            changes["exclusive_solo"] = True
        return bridge_call(bridge, cmd, {"chain": chain, **drop_none(track=track, device=device),
                                         **changes})

    @mcp.tool()
    def live_rack_insert_chain(
        track: int | str | None = None,
        device: int | str | None = None,
        index: int = -1,
        name: str | None = None,
        in_note: int | str | None = None,
        device_name: str | None = None,
    ) -> Any:
        """Add a new chain to a rack, optionally with a built-in device in it (Live 12.3+).

        Args:
            track: Track index, name, "master" or LOM path; omitted = selected track.
            device: The rack; omitted = selected/first rack on the track.
            index: Position, -1 = end.
            name: Chain name.
            in_note: Drum Racks: the pad for the new chain (36 / "C1"). Without it Live puts
                every new Drum Rack chain on C1 (36) — if C1 is taken the pad turns into a
                "Multi" pad, so pass the note you want.
            device_name: A native device to insert into the new chain ("Simpler", "Operator",
                "Reverb" ... — same names as live_device_insert; Max for Live devices can't
                go into chains).

        Returns:
            The new chain in full detail (with its `path` and device paths).

        Gotchas:
            To put a sample on a drum pad: insert a chain with in_note and
            device_name="Simpler", then live_simpler_action(action="replace_sample",
            device=<the new Simpler's path>, file_path=...) — or simply
            live_drumrack_set_pad(note, file_path=...).
            To group devices into a rack: live_device_insert("Audio Effect Rack" /
            "Instrument Rack"), this tool, then live_device_move(target_chain=...)
            (Song.move_device, verified on 12.4.5).
            Live's API cannot delete, duplicate or reorder chains (RackDevice
            has only insert_chain) and has no key/velocity/chain-select zones — mute the chain
            or set its activator off instead; only the chain selector value is settable
            (live_rack_macros chain_selector).
        """
        cmd = "racks.insert_chain"
        error = check_address(track, device, cmd)
        if error:
            return error
        if index < -1:
            return tool_error("index must be -1 (end) or >= 0", cmd=cmd)
        if name is not None and not name.strip():
            return tool_error("name must not be empty", cmd=cmd)
        if device_name is not None and not device_name.strip():
            return tool_error("device_name must not be empty", cmd=cmd)
        return bridge_call(bridge, cmd, {**drop_none(track=track, device=device, name=name,
                                                     in_note=in_note, device_name=device_name),
                                         "index": index})

    @mcp.tool()
    def live_rack_macros(
        track: int | str | None = None,
        device: int | str | None = None,
        values: dict[str, float | str] | None = None,
        normalized: bool = False,
        visible_count: int | None = None,
        chain_selector: float | str | None = None,
        include_hidden: bool = False,
    ) -> Any:
        """Read a rack's macro knobs (Macro 1..16) — and optionally set them in the same call.

        Args:
            track: Track index, name, "master" or LOM path; omitted = selected track.
            device: The rack; omitted = selected/first rack on the track.
            values: {macro: value} to set, all at once (one undo step). macro = "1".."16",
                "Macro 3" or the macro's current (mapped) name; value = 0..127 (raw), the text
                the knob shows, or 0..1 with normalized=true.
            normalized: Numeric values are 0..1.
            visible_count: Show this many macro knobs (1..16).
            chain_selector: Set the Chain Selector (raw number or display text).
            include_hidden: Also list macros beyond the visible count.

        Returns:
            {rack, visible_macro_count, has_macro_mappings, macros: [{number, index, name,
             value, display, mapped}], chain_selector?: {value, display}, variation_count?,
             selected_variation_index?}

        Gotchas:
            Without values/visible_count/chain_selector this only reads. A mapped macro's name
            is the name the user gave it (e.g. "Filter"); its `number` never changes.
            Mapping a parameter to a macro is not possible through Live's API
            (`macros_mapped` is read-only) — ask the user to right-click the parameter > Map
            to Macro N, or load a rack preset that has the mapping (live_plugin_expose
            `macros=` builds one for plug-in parameters); then set the macro here.
        """
        cmd = "racks.macros"
        error = check_address(track, device, cmd)
        if error:
            return error
        if values is not None and not values:
            return tool_error("values must not be empty", cmd=cmd)
        if visible_count is not None and not 1 <= visible_count <= 16:
            return tool_error("visible_count must be 1..16", cmd=cmd)
        address = drop_none(track=track, device=device)
        if values is None and visible_count is None and chain_selector is None:
            return bridge_call(bridge, cmd, {**address, "include_hidden": include_hidden})
        return bridge_call(bridge, "racks.set_macros", {
            **address, **drop_none(values=values, visible_count=visible_count,
                                   chain_selector=chain_selector),
            "normalized": normalized, "include_hidden": include_hidden})

    @mcp.tool()
    def live_rack_variations(
        action: str = "list",
        track: int | str | None = None,
        device: int | str | None = None,
        index: int | None = None,
    ) -> Any:
        """Macro variations (snapshots of all macro values): list, store, recall, delete, randomize.

        Args:
            action: "list" (default), "store" (save the current macros as a new variation;
                Live does not select it), "recall" (apply variation `index`, or the selected
                one), "recall_last", "select" (select `index` without applying), "delete"
                (`index` or the selected one; nothing is selected afterwards), "randomize"
                (Live's Rand button: randomizes mapped macros not excluded from randomization).
            track: Track index, name, "master" or LOM path; omitted = selected track.
            device: The rack; omitted = selected/first rack on the track.
            index: Variation index (0-based) for recall/select/delete.

        Returns:
            {rack, action, variation_count, selected_variation_index, macros: [{number, name,
             value, display}], note?}

        Gotchas:
            Live's variations and Rand only touch macros that are mapped to something — on a
            rack without macro mappings (a fresh empty rack) recall/randomize change nothing
            and the answer carries a `note`. Rack presets from the browser ("808 Core Kit")
            come with mapped macros.
        """
        cmd = "racks.variations"
        if action not in VARIATION_ACTIONS:
            return tool_error(f"action must be one of {', '.join(VARIATION_ACTIONS)}", cmd=cmd)
        error = check_address(track, device, cmd)
        if error:
            return error
        if index is not None and index < 0:
            return tool_error("index must be >= 0", cmd=cmd)
        return bridge_call(bridge, cmd, {"action": action,
                                         **drop_none(track=track, device=device, index=index)})

    @mcp.tool()
    def live_drumrack_overview(
        track: int | str | None = None,
        device: int | str | None = None,
        include_empty: bool = False,
        full_paths: bool = False,
    ) -> Any:
        """The whole Drum Rack pad map in one compact table (which pads hold what sample).

        Args:
            track: Track index, name, "master" or LOM path; omitted = selected track.
            device: The Drum Rack; omitted = selected/first Drum Rack on the track.
            include_empty: List all 128 pads, not only filled ones.
            full_paths: Add `path` (pad) and `chain` (first chain) columns and full sample file
                paths instead of file names.

        Returns:
            {rack, filled, with_samples, selected_pad?, visible_pads?: [first, last note],
             columns: ["note","key","name","chains","devices","sample","mute","solo","choke"
             (,"path","chain")], rows: [[36, "C1", "Kick", 1, "Simpler", "Kick.wav", false,
             false, 0], ...]}

        Gotchas:
            `devices` lists the pad's first chain joined with " > "; `sample` is the first
            Simpler sample found (Drum Sampler does not expose its file). Write MIDI on these
            `note` numbers to trigger the pads.
        """
        cmd = "racks.drum_pads"
        error = check_address(track, device, cmd)
        if error:
            return error
        return bridge_call(bridge, cmd, {**drop_none(track=track, device=device),
                                         "include_empty": include_empty,
                                         "full_paths": full_paths})

    @mcp.tool()
    def live_drumrack_set_pad(
        note: int | str,
        track: int | str | None = None,
        device: int | str | None = None,
        mute: bool | None = None,
        solo: bool | None = None,
        name: str | None = None,
        choke_group: int | None = None,
        out_note: int | str | None = None,
        select: bool | None = None,
        copy_to: int | str | None = None,
        clear: bool = False,
        file_path: str | None = None,
    ) -> Any:
        """Edit one Drum Rack pad: load a sample onto it, mute/solo, rename, choke group,
        select, copy to a pad, clear.

        Args:
            note: The pad: MIDI note (36), note name ("C1", "F#1"), pad name ("Kick") or the
                name of one of its chains (Live names a pad with several chains "Multi" and an
                empty pad after its note, e.g. "D1").
            track: Track index, name, "master" or LOM path; omitted = selected track.
            device: The Drum Rack; omitted = selected/first Drum Rack on the track.
            mute: Mute the pad.
            solo: Solo the pad.
            name: Rename the pad (renames its first chain).
            choke_group: 0 (none) .. 16 for all chains of the pad (e.g. open/closed hats = 1).
            out_note: Note sent to the pad's devices.
            select: Select the pad and scroll it into view.
            copy_to: Copy this pad's contents onto another pad (note / name) — replaces it.
            clear: Remove everything from the pad (runs last).
            file_path: Load this audio file (absolute path on the Live machine) onto the pad
                first — a pad with one Simpler gets its sample replaced, anything else on
                the pad is replaced by a new chain with a Simpler (Live 12.3+). Uses the
                track's top-level Drum Rack (one is inserted when the track has no
                instrument); pass `track`, not `device`.

        Returns:
            {rack, note, key, name, chains, mute, solo, choke, path, copied_to?, cleared?,
             loaded?: {route, file, pad, device}}

        Gotchas:
            Only the top-level Drum Rack has pad objects; for a nested Drum Rack use
            live_rack_set_chain on its chains instead. clear = DrumPad.delete_all_chains,
            copy_to = RackDevice.copy_pad. A Drum Sampler's sample cannot be replaced through
            the API — hot-swap the pad with a sample (live_browser_hotswap drum_pad=...,
            verified: a Simpler replaces the pad content) or use file_path here.
        """
        cmd = "racks.set_pad"
        error = check_address(track, device, cmd)
        if error:
            return error
        if isinstance(note, str) and not note.strip():
            return tool_error("note must be 0..127, a note name like 'C1' or a pad name", cmd=cmd)
        if isinstance(note, int) and not 0 <= note <= 127:
            return tool_error("note must be 0..127", cmd=cmd)
        if choke_group is not None and not 0 <= choke_group <= 16:
            return tool_error("choke_group must be 0..16", cmd=cmd)
        if name is not None and not name.strip():
            return tool_error("name must not be empty", cmd=cmd)
        changes = drop_none(mute=mute, solo=solo, name=name, choke_group=choke_group,
                            out_note=out_note, select=select, copy_to=copy_to)
        if clear:
            changes["clear"] = True
        if file_path is not None:
            if not file_path.strip():
                return tool_error("file_path must not be empty", cmd="samples.import")
            if device is not None:
                return tool_error("file_path loads onto the track's top-level Drum Rack — "
                                  "pass track and omit device", cmd="samples.import")
            if clear:
                return tool_error("file_path and clear contradict each other", cmd=cmd)
        if not changes and file_path is None:
            return tool_error("pass at least one change (use live_drumrack_overview to read)",
                              cmd=cmd)
        loaded = None
        if file_path is not None:
            loaded = bridge_call(bridge, "samples.import",
                                 drop_none(file_path=file_path, track=track, note=note,
                                           target="drum_pad", overwrite=True), timeout=40.0)
            if isinstance(loaded, dict) and "error" in loaded:
                return loaded
            note = (loaded.get("pad") or {}).get("note", note)
            if track is None:
                track = (loaded.get("track") or {}).get("path")
        result = bridge_call(bridge, cmd, {"note": note, **drop_none(track=track, device=device),
                                           **changes})
        if loaded is not None and isinstance(result, dict) and "error" not in result:
            result["loaded"] = {key: loaded.get(key) for key in ("route", "file", "pad",
                                                                 "device")}
        return result

    @mcp.tool()
    def live_drumrack_convert(
        action: str,
        track: int | str | None = None,
        device: int | str | None = None,
        note: int | str | None = None,
        select: bool = False,
    ) -> Any:
        """Live 12's Drum Rack conversions: pull a pad out onto its own track, or wrap a
        track's whole device chain into a new Drum Rack pad.

        Args:
            action: "pad_to_track" (a new MIDI track with a copy of pad `note`'s device
                chain — the pad stays) or "track_to_pad" (every device of `track` moves onto
                the C1 pad of a new Drum Rack on that track).
            track: Track index, name or LOM path; omitted = selected track.
            device: pad_to_track — the Drum Rack; omitted = selected/first Drum Rack.
            note: pad_to_track — 36, "C1", a pad or chain name.
            select: pad_to_track — select the new track.

        Returns:
            pad_to_track: {action, via, pad:{note, key, name}, new_track, devices}.
            track_to_pad: {action, via, track, rack, devices, result?}.

        Gotchas: Live.Conversions needs Live 12 (`unsupported` otherwise); regular tracks
        only; pad_to_track needs a top-level Drum Rack. Slicing a Simpler onto pads is
        live_simpler_action("to_drum_rack"); audio clip -> Simpler/Drum Rack is
        live_clip_convert.
        """
        cmd = "racks.convert"
        if action not in ("pad_to_track", "track_to_pad"):
            return tool_error("action must be 'pad_to_track' or 'track_to_pad'", cmd=cmd)
        error = check_address(track, device, cmd)
        if error:
            return error
        if action == "pad_to_track" and note is None:
            return tool_error("pad_to_track needs note (36, 'C1' or a pad name)", cmd=cmd)
        if action == "track_to_pad" and (device is not None or note is not None):
            return tool_error("track_to_pad takes only track", cmd=cmd)
        args = {"action": action, **drop_none(track=track, device=device, note=note)}
        if select:
            args["select"] = True
        return bridge_call(bridge, cmd, args)
