"""Browser tools: Live's browser roots, browsing, cached search, loading (instruments, effects,
plug-ins, drum kits, presets, samples, clips — one tool with a `category`), hot-swap and
preview.

Browser item addressing (every tool that takes an item accepts exactly one of these):
- `uri`   — the stable id from any browse/search result (fastest, preferred).
- `path`  — "<root>/<item>/<item>", e.g. "instruments/Analog/Bass", "drums/808 Core Kit",
            "user_library/Samples/My Kick.wav", "user_folders/My Samples", "current_project".
            Root names: instruments, sounds, drums, audio_effects, midi_effects, plugins,
            max_for_live, clips, samples, packs, user_library, user_folders (Places),
            current_project, colors (+ any extra root a newer Live exposes).
- `query` — words to search for; the best loadable hit is used.

Live's Python API has no search: the Remote Script walks the browser (bounded by time/visits)
and caches every root for 5 minutes, so the first search of a big library can take a few
seconds (Live's UI pauses meanwhile) and later ones are instant.

Live facts (verified on 12.4.5): while a hot-swap target is set Live *filters* the browser
(an instrument target empties Audio Effects) — results then carry `hotswap_filter`; clear it
with live_browser_hotswap(action="clear"). Samples and clips only load into clip slots while
the Session view is focused (live_sample_import works in any view). Live Clips (.alc) always
arrive on a new track with their own devices.
"""

from __future__ import annotations

from typing import Any

from . import bridge_call, drop_none, tool_error
from ..client import BridgeClient

_DETAILS = ("minimal", "summary", "full")
_KINDS = ("folders", "loadable", "devices", "samples", "clips", "presets")
_CATEGORIES = ("instrument", "audio_effect", "midi_effect", "plugin", "drum_kit", "sound",
               "sample", "clip")
_FORMATS = ("vst3", "vst2", "vst", "au")
_POSITIONS = ("end", "before_selected", "after_selected")
_HOTSWAP_ACTIONS = ("info", "set", "clear", "load")

#: Seconds the MCP side waits for commands that may walk the browser.
_WALK_TIMEOUT = 75.0


def _one_item(uri: str | None, path: str | None, query: str | None, cmd: str,
              required: bool = True) -> dict[str, Any] | None:
    """Validate the uri/path/query triple; returns a tool error or None."""
    given = [name for name, value in (("uri", uri), ("path", path), ("query", query))
             if value is not None]
    if len(given) > 1:
        return tool_error(f"pass only one of uri, path or query (got {', '.join(given)})",
                          cmd=cmd)
    if required and not given:
        return tool_error("pass one of uri, path or query", cmd=cmd)
    for name, value in (("uri", uri), ("path", path), ("query", query)):
        if value is not None and not value.strip():
            return tool_error(f"{name} must not be empty", cmd=cmd)
    return None


def _check_enum(value: str | None, allowed: tuple[str, ...], name: str,
                cmd: str) -> dict[str, Any] | None:
    if value is not None and value not in allowed:
        return tool_error(f"{name} must be one of {', '.join(allowed)}", cmd=cmd)
    return None


def _timeout(max_seconds: float | None) -> float:
    return max(_WALK_TIMEOUT, (max_seconds or 0) + 20.0)


def register(mcp: Any, bridge: BridgeClient) -> None:
    """Register the browser tools on the MCP app."""

    def _load(cmd_args: dict[str, Any], max_seconds: float | None = None) -> Any:
        return bridge_call(bridge, "browser.load", cmd_args, timeout=_timeout(max_seconds))

    @mcp.tool()
    def live_browser_roots() -> Any:
        """Live's browser categories (roots) with item counts, plus Splice and hot-swap info.

        Returns:
            `{roots:[{name:"instruments", label:"Instruments", uri, count, list?}], extra_roots:
            [...], splice:{available, path?|note?}, hotswap:{target, filter_type, browse_mode}}`
            `name` is what you pass as a path's first segment or as `root`.

        Gotchas: `user_folders` = the folders the user added under Places (a Splice folder
        added there shows up as `splice.path`); `packs` mirrors content that is also in the
        category roots. Live 12.3+'s Splice section is not reachable from scripts — use the
        Splice MCP + `live_splice_import_downloaded` (see `live_splice_setup_info`).
        """
        return bridge_call(bridge, "browser.roots")

    @mcp.tool()
    def live_browser_browse(
        path: str | None = None,
        uri: str | None = None,
        name: str | None = None,
        offset: int = 0,
        limit: int = 100,
        filter: str | None = None,
        kind: str | None = None,
        detail: str = "summary",
    ) -> Any:
        """List the contents of a browser folder (also user library, current project, Places).

        Args:
            path: "<root>/<item>/..." e.g. "instruments", "instruments/Analog",
                "user_library/Samples", "current_project", "user_folders/My Samples",
                "plugins/VST3". Item names are case-insensitive; extensions optional.
            uri: an item's uri from an earlier result (fastest).
            name: find a folder by name anywhere and list it (best match).
            offset, limit: paging (limit 1-500, default 100); the answer has `next_offset`
                when there is more.
            filter: only children whose name contains all these words.
            kind: only "folders", "loadable", "devices", "samples", "clips" or "presets".
            detail: "minimal" (name+uri), "summary" (default; + flags/source/format),
                "full" (+ child_count per item, slower).
            No path/uri/name: returns the roots (same as `live_browser_roots`).

        Returns:
            `{path, uri, name, total, offset, count, next_offset?, items:[{name, uri, is_folder?,
            is_loadable?, is_device?, source?, format?}]}` — flags only appear when true.

        Gotchas: devices ("Analog") and packs are not folders but contain preset folders —
        browse their uri. Pass a loadable item's uri to `live_browser_load`.
        """
        cmd = "browser.browse"
        if sum(v is not None for v in (path, uri, name)) > 1:
            return tool_error("pass only one of path, uri or name", cmd=cmd)
        if not 1 <= limit <= 500:
            return tool_error("limit must be between 1 and 500", cmd=cmd)
        if offset < 0:
            return tool_error("offset must be >= 0", cmd=cmd)
        for error in (_check_enum(kind, _KINDS, "kind", cmd),
                      _check_enum(detail, _DETAILS, "detail", cmd)):
            if error:
                return error
        args = drop_none(path=path, uri=uri, name=name, filter=filter, kind=kind)
        if offset:
            args["offset"] = offset
        if limit != 100:
            args["limit"] = limit
        if detail != "summary":
            args["detail"] = detail
        return bridge_call(bridge, cmd, args, timeout=_WALK_TIMEOUT if name else None)

    @mcp.tool()
    def live_browser_search(
        query: str,
        root: str | list[str] | None = None,
        category: str | None = None,
        limit: int = 20,
        offset: int = 0,
        loadable_only: bool = False,
        plugin_format: str | None = None,
        max_seconds: float | None = None,
        refresh: bool = False,
        detail: str = "summary",
    ) -> Any:
        """Search Live's browser by name (all words must match, any order, case-insensitive).

        Args:
            query: e.g. "grand piano", "808 kit", "reverb", "kick", "serum".
            root: limit to a root or list of roots ("instruments", "samples", "user_library",
                "user_folders", "current_project", "packs", "plugins", "all"). Default: every
                root except packs and colors.
            category: "instrument", "audio_effect", "midi_effect", "plugin", "drum_kit", "sound",
                "sample" (audio files only) or "clip" — chooses sensible roots and filters.
            limit, offset: paging of the ranked results (limit 1-200, default 20).
            loadable_only: skip folders and other unloadable items.
            plugin_format: "vst3", "vst2" or "au" — only that format (default: all formats,
                VST3 ranked above AU above VST2 when a plug-in is installed several times).
            max_seconds: time budget for walking the browser (default 10, max 60).
            refresh: ignore the 5-minute cache (after adding files/packs/plug-ins).
            detail: "minimal", "summary" (default) or "full".

        Returns:
            `{query, total, count, results:[{name, uri, path, is_folder?, is_loadable?,
            is_device?, source?, format?}], searched:[roots], cached:[roots], visited,
            truncated, skipped?, next_offset?}` ranked exact name > prefix > all words in
            name > words in folder path.

        Gotchas: `truncated: true` means the walk hit its limit — narrow `root`/`category` or
        raise `max_seconds`. Load a hit with `live_browser_load(uri=...)`.
        """
        cmd = "browser.search"
        if not query.strip():
            return tool_error("query must not be empty", cmd=cmd)
        if not 1 <= limit <= 200:
            return tool_error("limit must be between 1 and 200", cmd=cmd)
        if offset < 0:
            return tool_error("offset must be >= 0", cmd=cmd)
        if max_seconds is not None and not 0.5 <= max_seconds <= 60:
            return tool_error("max_seconds must be between 0.5 and 60", cmd=cmd)
        if isinstance(root, list) and not root:
            return tool_error("root must not be an empty list", cmd=cmd)
        for error in (_check_enum(category, _CATEGORIES, "category", cmd),
                      _check_enum(plugin_format.lower() if plugin_format else None, _FORMATS,
                                  "plugin_format", cmd),
                      _check_enum(detail, _DETAILS, "detail", cmd)):
            if error:
                return error
        args = drop_none(query=query, root=root, category=category,
                         plugin_format=plugin_format, max_seconds=max_seconds)
        if limit != 20:
            args["limit"] = limit
        if offset:
            args["offset"] = offset
        if loadable_only:
            args["loadable_only"] = True
        if refresh:
            args["refresh"] = True
        if detail != "summary":
            args["detail"] = detail
        return bridge_call(bridge, cmd, args, timeout=_timeout(max_seconds))

    @mcp.tool()
    def live_browser_load(
        uri: str | None = None,
        path: str | None = None,
        query: str | None = None,
        root: str | list[str] | None = None,
        category: str | None = None,
        track: int | str | None = None,
        slot: int | str | None = None,
        new_track: bool | str | None = None,
        track_name: str | None = None,
        position: str | None = "end",
        plugin_format: str | None = None,
        editor_open: bool | None = None,
        max_seconds: float | None = None,
    ) -> Any:
        """Load any browser item — instrument, effect, preset, rack, plug-in, drum kit,
        sample, clip — onto a track and report exactly what changed. One undo step.

        Typical calls:
        - instrument on a new track: `query="Operator", category="instrument", new_track=true,
          track_name="Lead"` ("Wavetable", "Grand Piano", "Sub Bass" presets work too);
        - effect: `query="Reverb", category="audio_effect", track="Vocals"` (MIDI effects:
          `category="midi_effect"`; returns "A" and "master" are valid tracks);
        - plug-in: `query="Serum 2", category="plugin", plugin_format="vst3",
          editor_open=false` (vendor-qualified "Xfer Records/Serum 2" works; exact names beat
          longer ones, so "Serum 2" never picks "Serum 2 FX");
        - drum kit: `query="909 Core Kit", category="drum_kit", new_track=true` (an empty Drum
          Rack: `query="Drum Rack", category="instrument"` or live_device_insert);
        - sample from Live's browser: `query="kick 808", category="sample", track="Drums
          Audio", slot=2` (files on disk: live_sample_import).

        Args:
            uri / path / query: the item — exactly one (see the module doc; `query` picks the
                best loadable hit, narrowed by `root`/`category`/`plugin_format`).
            root: browser root(s) to search (instruments, sounds, drums, audio_effects,
                midi_effects, plugins, samples, packs, user_library, user_folders ...).
            category: "instrument", "audio_effect", "midi_effect", "plugin", "drum_kit",
                "sound", "sample" or "clip" — filters and ranks `query` hits (instruments
                search instruments, sounds, plug-ins (VST3 first), Max for Live, the user
                library and packs; drum kits rank "... Kit.adg" first).
            track: index, name (exact, then prefix), "master" or path. Default: the selected
                track. LiveBridge selects it first because Live loads onto the selected track.
            slot: clip slot index (or scene name) to highlight first — samples land in the
                highlighted slot of an audio track (Session view only).
            new_track: create a track at the end and load onto it: true (a MIDI track, or an
                audio track for category="sample"), "midi" or "audio".
            track_name: name for that new track (default: the item's name).
            position: "end" (default), "before_selected", "after_selected" (relative to the
                track's selected device), or null for Live's current insert mode.
            plugin_format: "vst3", "vst2" or "au" when loading plug-ins by query (default:
                VST3, then AU, then VST2 when a plug-in exists in several formats).
            editor_open: plug-ins — true/false opens/closes the plug-in window after loading;
                omitted = Live's "Auto-Open Plug-In Windows" setting decides.
            max_seconds: search time budget (default 10).

        Returns:
            `{loaded:{name, uri, path}, via, track:{path,name,type}, created_track?,
            inserted?:[device summaries with path], removed?:[{name}], changed?:[device
            summaries with "was" (old name) or "reloaded": true], clips?:[clip summaries],
            new_tracks?, alternatives?:[other hits], notes?, editor_open?}`

        Gotchas: an instrument loaded onto a track that already has one replaces it (see
        `removed`, or `changed` when Live reuses the device object — e.g. a kit loaded over a
        Drum Rack, or Operator over Operator, which resets it); loading an instrument onto an
        audio track makes Live create a MIDI track (`new_tracks`). Live keeps MIDI effects
        before the instrument and audio effects after it whatever `position` says. Live
        Clips (.alc) always get a new track (their clip is in `clips`), whatever track/slot
        you pass. Samples/clips load only while the Session view is focused — otherwise
        nothing happens and `notes` says so. Plug-ins only appear when Live's "Use VST/AU
        plug-ins" options are on; a plain load of a big plug-in (Serum 2) exposes 0
        parameters, so for Serum 2 (or any big VST3) prefer
        `live_plugin_expose(plugin="Serum 2", parameters=["sound_design"], new_track=true)`,
        which loads it already controllable. For built-in devices without presets
        `live_device_insert` (no browser walk) is faster. To *replace* a specific device use
        `live_browser_hotswap(action="load")`. An empty change list can also mean Live is
        still loading a big preset/plug-in.
        """
        cmd = "browser.load"
        error = _one_item(uri, path, query, cmd)
        if error:
            return error
        if isinstance(new_track, bool):
            new_track = ("audio" if category == "sample" else "midi") if new_track else None
        for error in (_check_enum(category, _CATEGORIES, "category", cmd),
                      _check_enum(plugin_format.lower() if plugin_format else None, _FORMATS,
                                  "plugin_format", cmd),
                      _check_enum(new_track, ("midi", "audio"), "new_track", cmd),
                      _check_enum(position, _POSITIONS, "position", cmd)):
            if error:
                return error
        if new_track and track is not None:
            return tool_error("pass either track or new_track, not both", cmd=cmd)
        if track_name is not None and not new_track:
            return tool_error("track_name only applies with new_track", cmd=cmd)
        if max_seconds is not None and not 0.5 <= max_seconds <= 60:
            return tool_error("max_seconds must be between 0.5 and 60", cmd=cmd)
        args = drop_none(uri=uri, path=path, query=query, root=root, category=category,
                         track=track, slot=slot, new_track=new_track, track_name=track_name,
                         plugin_format=plugin_format, max_seconds=max_seconds)
        if position != "end":
            args["position"] = position
        result = _load(args, max_seconds)
        if editor_open is None or not isinstance(result, dict) or "error" in result:
            return result
        devices = [d for d in result.get("inserted") or result.get("changed") or []
                   if isinstance(d, dict) and d.get("path")]
        plugins = [d for d in devices if d.get("is_plugin") or str(d.get("class_name", ""))
                   in ("PluginDevice", "AuPluginDevice", "VstPluginDevice",
                       "Vst3PluginDevice")] or devices
        if plugins:
            window = bridge_call(bridge, "plugins.set", {"device": plugins[0]["path"],
                                                         "editor_open": editor_open})
            result["editor_open"] = window.get("editor_open") if "error" not in window \
                else window
        return result

    @mcp.tool()
    def live_browser_hotswap(
        action: str = "info",
        track: int | str | None = None,
        device: int | str | None = None,
        drum_pad: int | str | None = None,
        uri: str | None = None,
        path: str | None = None,
        query: str | None = None,
        category: str | None = None,
    ) -> Any:
        """Hot-swap: see/set/clear the device (or drum pad) the next load replaces, or replace it
        right away.

        Args:
            action: "info" (default), "set", "clear", or "load" (replace `device` — or the
                current target — with the item given by uri/path/query).
            track: track of the device (default: the selected track).
            device: device index/name on `track`, or a LOM path
                ("song.tracks[0].devices[1]", "song.tracks[2].devices[0].chains[0].devices[0]").
            drum_pad: with a Drum Rack `device`: MIDI note (36), note name ("C1") or pad/chain
                name ("Kick") — that pad is the target (a sample loads into a new Simpler there).
            uri / path / query / category: the replacement item for action="load".

        Returns:
            info/set/clear: `{target:{path,name,...}|{kind:"drum_pad",name,note,path}|null,
            filter_type, browse_mode}`; load: like `live_browser_load` with `hotswap: true`,
            `inserted` (new device) and `removed` (the replaced one).

        Gotchas: setting a target switches Live's browser into hot-swap mode and filters it
        to items that can replace the target (an instrument target hides audio effects) until
        it is cleared; searches/listings then say `hotswap_filter`. After a hot-swap load Live
        keeps the new device as the target, so repeated action="load" calls without `device`
        keep swapping it. Normal loads clear the target first so they add instead of replace.
        """
        if action not in _HOTSWAP_ACTIONS:
            return tool_error(f"action must be one of {', '.join(_HOTSWAP_ACTIONS)}",
                              cmd="browser.hotswap")
        if action == "load":
            error = _one_item(uri, path, query, "browser.load") or \
                _check_enum(category, _CATEGORIES, "category", "browser.load")
            if error:
                return error
            if drum_pad is not None and device is None:
                return tool_error("drum_pad needs device (the Drum Rack)", cmd="browser.load")
            args = drop_none(uri=uri, path=path, query=query, category=category, track=track,
                             device=device, drum_pad=drum_pad)
            args["hotswap"] = True
            return _load(args)
        if any(v is not None for v in (uri, path, query, category)):
            return tool_error("uri/path/query/category only apply to action='load'",
                              cmd="browser.hotswap")
        if action == "set" and device is None:
            return tool_error("action='set' needs device", cmd="browser.hotswap")
        if action != "set" and (device is not None or drum_pad is not None):
            return tool_error("device/drum_pad only apply to action='set' or 'load'",
                              cmd="browser.hotswap")
        args = drop_none(track=track, device=device, drum_pad=drum_pad)
        if action != "info":
            args["action"] = action
        return bridge_call(bridge, "browser.hotswap", args)

    @mcp.tool()
    def live_browser_preview(
        uri: str | None = None,
        path: str | None = None,
        query: str | None = None,
        category: str | None = None,
        stop: bool = False,
    ) -> Any:
        """Audition a sample/clip from the browser (Live's preview), or stop the preview.

        Args:
            uri / path / query: the item (one of them); `category` narrows a query
                (e.g. "sample").
            stop: stop the running preview instead (no item needed).

        Returns: `{previewing:{name, uri, path}}` or `{stopped: true}`.

        Gotchas: Live's browser "Preview" switch must be on; audio goes to the Cue output.
        Devices and presets make no sound when previewed.
        """
        cmd = "browser.preview"
        if stop:
            if any(v is not None for v in (uri, path, query)):
                return tool_error("stop=true takes no item", cmd=cmd)
            return bridge_call(bridge, cmd, {"stop": True})
        error = _one_item(uri, path, query, cmd) or \
            _check_enum(category, _CATEGORIES, "category", cmd)
        if error:
            return error
        return bridge_call(bridge, cmd, drop_none(uri=uri, path=path, query=query,
                                                  category=category),
                           timeout=_WALK_TIMEOUT if query else None)
