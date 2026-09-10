"""Third-party plug-in tools (VST2 / VST3 / Audio Units): set-wide report, format/vendor
detection, presets, exposed vs. available parameters, the guided Configure workflow and the
plug-in window.

What Live lets a script see of a plug-in (verified on Live 12.4.5 with Serum 2 and Apple AUs):
every parameter *name* the plug-in has, but values only for the parameters in the device's
**Configure** list — and for big plug-ins (Serum 2 VST3 and AU: 0 of ~2600) that list starts
empty. Nothing in Live's API can add to a loaded device's list, but live_plugin_expose
(tools/plugin_racks.py) loads a VST3 plug-in in a generated rack that exposes up to 128 chosen
parameters without any user click — Serum 2 / Serum 2 FX are fully mapped. The manual Configure
route (live_plugin_configure) is the fallback for AU / VST2. Exposed values are set with
live_device_set_parameter(s) — display strings like "-6 dB" work there too — and automated with
the automation tools.

Addressing: `track` (index, name, "master", LOM path; omitted = selected track) and `device`
(index, name or LOM path; omitted = the track's selected device) — as in the device tools.
"""

from __future__ import annotations

import asyncio
import time as _time
from typing import Any

from . import bridge_call, drop_none, tool_error
from ..client import BridgeClient
from .devices import check_address, check_detail, check_paging

_SLOW = 30.0
_POLL_SECONDS = 1.0
_MAX_WAIT_SECONDS = 600.0
_SCOPES = ("exposed", "all")


def _is_error(value: Any) -> bool:
    return isinstance(value, dict) and "error" in value and "type" in value


def register(mcp: Any, bridge: BridgeClient) -> None:
    """Register the plug-in tools on the MCP app."""

    @mcp.tool()
    def live_plugin_list(
        include_nested: bool = True,
        resolve_format: bool = True,
    ) -> Any:
        """Report every third-party plug-in in the set: format, vendor, exposed parameter and
        preset counts.

        Args:
            include_nested: Also look inside rack chains.
            resolve_format: Tell VST2 from VST3 and find the vendor by looking the plug-in up in
                Live's browser (Live itself reports both VST formats as "PluginDevice"; Audio
                Units are "AuPluginDevice").

        Returns:
            {count, formats: {"VST3": n, "VST2": n, "AU": n, "VST2/VST3": n},
             needs_configure: [paths of plug-ins exposing none of their parameters],
             plugins: [{path, track, name, plugin, format, format_source?, vendor?, type,
             is_active, parameter_count (exposed), plugin_parameter_count (all it has),
             preset_count, selected_preset?, latency_ms?}],
             installed?: {count, formats, complete, hint?} — what Live's Plug-Ins browser
             offers for loading (with resolve_format=true)}

        Gotchas:
            Plug-ins in `needs_configure` (Serum 2 right after loading) cannot be controlled or
            automated until parameters are exposed: use live_plugin_expose instead of asking the
            user to Configure (VST3; Serum 2 is fully mapped — parameters=["sound_design"]);
            live_plugin_configure (manual Configure) only for AU / VST2.
            `installed.count` 0 means Live lists no plug-ins at all (plug-in use is switched
            off in Live's Settings → Plug-Ins) — `installed.hint` says what to switch on.
        """
        return bridge_call(bridge, "plugins.list",
                           {"include_nested": include_nested, "resolve_format": resolve_format},
                           timeout=_SLOW)

    @mcp.tool()
    def live_plugin_get(
        track: int | str | None = None,
        device: int | str | None = None,
        resolve_format: bool = True,
        max_names: int = 128,
    ) -> Any:
        """Describe one plug-in: format, vendor, presets, exposed vs. available parameters,
        window, latency.

        Args:
            track: Track index, name, "master" or LOM path; omitted = selected track.
            device: Plug-in index, name or LOM path; omitted = selected device.
            resolve_format: Tell VST2 from VST3 (and find the vendor) through Live's browser.
            max_names: How many exposed parameter names to list (0..1024).

        Returns:
            {path, name, plugin, format ("VST3"|"VST2"|"AU"|"VST2/VST3"), format_source?,
             vendor? ("Xfer Records"), type, is_active, parameter_count (exposed),
             parameter_names: [exposed names], plugin_parameter_count (all the plug-in has),
             not_exposed?, preset_count, selected_preset_index, selected_preset?, editor_open,
             latency_ms?, latency_samples, configure_hint? (when nothing is exposed yet)}

        Gotcha: `parameter_count` 0 with a large `plugin_parameter_count` (Serum 2: 0 of 2623)
        means nothing is controllable yet — use live_plugin_expose instead of asking the user to
        Configure (VST3; Serum 2 is fully mapped).
        """
        cmd = "plugins.get"
        error = check_address(track, device, cmd)
        if error:
            return error
        if not 0 <= max_names <= 1024:
            return tool_error("max_names must be 0..1024", cmd=cmd)
        return bridge_call(bridge, cmd, {**drop_none(track=track, device=device),
                                         "resolve_format": resolve_format,
                                         "max_names": max_names}, timeout=_SLOW)

    @mcp.tool()
    def live_plugin_presets(
        track: int | str | None = None,
        device: int | str | None = None,
        filter: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> Any:
        """List a plug-in's presets/programs as Live sees them (paged, filterable).

        Args:
            track: Track index, name, "master" or LOM path; omitted = selected track.
            device: Plug-in index, name or LOM path; omitted = selected device.
            filter: Case-insensitive substring of the preset name.
            offset: Start of the page.
            limit: Page size 1..1000.

        Returns:
            {device, total, matched, offset, count, selected_preset_index, selected_preset?,
             presets: [{index, name}], next_offset?, note? (when Live lists no real programs)}

        Gotchas:
            Serum 2 (VST3 and AU) and Apple's AUs report only "Default" — their sounds are
            loaded in the plug-in's own browser (open it with live_plugin_set
            editor_open=true; Serum 2's factory presets live in /Library/Audio/Presets/Xfer
            Records/Serum 2 Presets on macOS), from Live device presets (.adv) saved with the
            plug-in's state (live_browser_search finds them), or from ~/Splice/presets.
            For the plug-in's preset FILES on disk (.vstpreset, .adv/.adg, .SerumPreset) use
            live_plugin_preset_files; pass a file's path to live_plugin_expose(preset_file=...)
            to start from that sound. (This tool is Live's program list; the disk listing has
            its own tool because this name was already taken.)
        """
        cmd = "plugins.presets"
        error = check_address(track, device, cmd) or check_paging(offset, limit, cmd)
        if error:
            return error
        return bridge_call(bridge, cmd, {**drop_none(track=track, device=device, filter=filter),
                                         "offset": offset, "limit": limit})

    @mcp.tool()
    def live_plugin_set(
        track: int | str | None = None,
        device: int | str | None = None,
        preset: int | str | None = None,
        editor_open: bool | None = None,
    ) -> Any:
        """Select a plug-in preset and/or open or close the plug-in window.

        Args:
            track: Track index, name, "master" or LOM path; omitted = selected track.
            device: Plug-in index, name or LOM path; omitted = selected device.
            preset: Preset index or name (exact, case-insensitive, prefix or substring — must
                be unambiguous; see live_plugin_presets).
            editor_open: true opens the plug-in's window (e.g. for its own preset browser or
                for Configure), false closes it.

        Returns:
            {device, selected_preset_index, selected_preset?, editor_open}

        Gotchas:
            Loading a preset discards unsaved changes inside the plug-in. One undo step.
            Live opens plug-in windows on load when its "Auto-Open Plug-In Windows" setting is
            on — close them with editor_open=false.
        """
        cmd = "plugins.set"
        error = check_address(track, device, cmd)
        if error:
            return error
        if preset is None and editor_open is None:
            return tool_error("pass preset and/or editor_open", cmd=cmd)
        if isinstance(preset, str) and not preset.strip():
            return tool_error("preset must not be empty", cmd=cmd)
        return bridge_call(bridge, cmd, drop_none(track=track, device=device, preset=preset,
                                                  editor_open=editor_open))

    @mcp.tool()
    def live_plugin_parameters(
        track: int | str | None = None,
        device: int | str | None = None,
        filter: str | None = None,
        offset: int = 0,
        limit: int = 64,
        detail: str = "summary",
        scope: str = "exposed",
    ) -> Any:
        """List a plug-in's parameters, paged: the ones Live exposes (default) or all of them.

        Live only exposes (lets Claude read, set and automate) the plug-in parameters in the
        device's **Configure** list. Small plug-ins get theirs automatically (Apple AUNBandEQ:
        all 41); big ones start with NONE — Serum 2 (VST3 and AU) exposes 0 of its ~2600
        parameters (541 synth parameters plus 2082 "CCn Chan m" MIDI proxies) right after
        loading. Use scope="all" to search what the plug-in has, then live_plugin_expose to get
        the wanted ones exposed without asking the user to Configure (VST3; Serum 2 is fully
        mapped); live_plugin_configure (manual Configure) is the fallback for AU / VST2.

        Args:
            track: Track index, name, "master" or LOM path; omitted = selected track.
            device: Plug-in index, name or LOM path; omitted = selected device.
            filter: Name filter — substring, else words with synonyms: "cutoff" finds
                "Filter 1 Freq", "osc a level" finds "A Level", "master volume" finds "Main Vol".
            offset: Start of the page.
            limit: Page size 1..1000.
            detail: "minimal" (index, name, value, display), "summary" (default) or "full"
                (scope "exposed").
            scope: "exposed" (default) or "all" (every parameter the plug-in has, each with
                `exposed` and — when exposed — its Live `index`, value and display).

        Returns:
            {device, scope, total, matched, offset, count, parameters: [...], next_offset?,
             exposed_count, plugin_parameter_count, configure_hint?}
            exposed items: {index, name, value, display, min, max, quantized?, items?}
            all items: {plugin_index, name, exposed, index?, value?, display?}

        Gotchas:
            parameters[0] is "Device On" (Live's switch, not the plug-in's). Plug-in values are
            0..1 internally — set them with live_device_set_parameter using `display` strings
            in the plug-in's units ("2000 Hz", "-6 dB", "30 %", "+12 st"). Duplicate names
            (AUNBandEQ has eight "Frequency") are addressed as "Frequency #3" or by index.
            Some plug-ins hide parameters that do not apply to the current mode (a shelf band
            has no Bandwidth), so the "all" list can change.
        """
        cmd = "plugins.parameters"
        error = check_detail(detail, cmd) or check_address(track, device, cmd) or \
            check_paging(offset, limit, cmd)
        if error:
            return error
        if scope not in _SCOPES:
            return tool_error("scope must be 'exposed' or 'all'", cmd=cmd)
        return bridge_call(bridge, cmd, {**drop_none(track=track, device=device, filter=filter),
                                         "offset": offset, "limit": limit, "detail": detail,
                                         "scope": scope})

    @mcp.tool()
    async def live_plugin_configure(
        parameters: list[str],
        track: int | str | None = None,
        device: int | str | None = None,
        wait_seconds: float = 0.0,
        open_editor: bool = False,
    ) -> Any:
        """Guided Configure: check which wanted plug-in parameters Live exposes and (optionally)
        wait while the user exposes the rest in Live.

        Prefer live_plugin_expose for VST3 plug-ins: it exposes up to 128 parameters with no
        user click (Serum 2 is fully mapped; another VST3 needs live_plugin_param_map once).
        This tool is the fallback for AU / VST2 (or when the user already configured the
        device): Live's API cannot add to a loaded device's Configure list, so the user does it
        once — click the device's Configure button in Live, move each wanted control in the
        plug-in window, click Configure again. Call this first with
        wait_seconds=0 to get the exact names to ask for (`missing`) and the `steps`; tell the
        user; then call it again with wait_seconds (e.g. 120) — it returns as soon as all of
        them are exposed, or at the timeout with what is still missing.

        Args:
            parameters: Wanted parameter names, fuzzy ("filter 1 cutoff", "env 1 attack",
                "Macro 1", "osc a wavetable position", "Frequency #3"). 1..64 names.
            track: Track index, name or LOM path; omitted = selected track.
            device: Plug-in index, name or LOM path; omitted = selected device.
            wait_seconds: 0 = answer immediately; up to 600 = poll every second until every
                wanted parameter is exposed.
            open_editor: Open the plug-in window first (the user needs it for Configure).

        Returns:
            {device, exposed_count, plugin_parameter_count, all_exposed,
             parameters: [{query, name?, exposed, index?, display?, candidates?, error?}],
             missing: [plug-in parameter names still to move], steps?: [...],
             waited_s?, newly_exposed?: [names], timed_out?: bool}
            Exposed parameters' `index` works with live_device_set_parameter and automation.

        Gotchas:
            Save the set or the device as a Live preset (.adv) afterwards so the Configure list
            survives (a freshly loaded plug-in starts empty again). Live shows at most 128
            configured parameters per plug-in.
        """
        cmd = "plugins.exposure"
        error = check_address(track, device, cmd)
        if error:
            return error
        if not isinstance(parameters, list) or not parameters or len(parameters) > 64 or \
                not all(isinstance(p, str) and p.strip() for p in parameters):
            return tool_error("parameters must be a list of 1..64 non-empty names", cmd=cmd)
        if not 0 <= wait_seconds <= _MAX_WAIT_SECONDS:
            return tool_error(f"wait_seconds must be 0..{_MAX_WAIT_SECONDS:g}", cmd=cmd)
        args = {**drop_none(track=track, device=device), "parameters": parameters}
        if open_editor:
            opened = await asyncio.to_thread(
                bridge_call, bridge, "plugins.set",
                {**drop_none(track=track, device=device), "editor_open": True})
            if _is_error(opened):
                return opened
        status = await asyncio.to_thread(bridge_call, bridge, cmd, args)
        if _is_error(status) or not wait_seconds or status.get("all_exposed"):
            return status
        before = {p.get("name") for p in status.get("parameters", []) if p.get("exposed")}
        started = _time.monotonic()
        deadline = started + wait_seconds
        while _time.monotonic() < deadline:
            await asyncio.sleep(_POLL_SECONDS)
            current = await asyncio.to_thread(bridge_call, bridge, cmd, args)
            if _is_error(current):
                return current
            status = current
            if status.get("all_exposed"):
                break
        now = {p.get("name") for p in status.get("parameters", []) if p.get("exposed")}
        status["waited_s"] = round(_time.monotonic() - started, 1)
        status["newly_exposed"] = sorted(n for n in now - before if n)
        status["timed_out"] = not status.get("all_exposed")
        return status
