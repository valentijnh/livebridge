"""View & selection tools: what is selected, which views are visible, show/hide/focus views,
select tracks/scenes/clip slots/devices/clips, the clip editor (envelope lane, grid, loop zoom),
zoom/scroll, Follow and Draw Mode.

View names: "Session", "Arranger", "Detail", "Detail/Clip", "Detail/DeviceChain", "Browser"
(aliases such as "arrangement", "clip", "devices" work too; "" = the visible main view).
None of these tools change the Live set itself, so nothing here adds undo steps.
"""

from __future__ import annotations

from typing import Any

from . import bridge_call, drop_none, tool_error
from ..client import BridgeClient

#: action -> bridge command (literal names so docs/TOOLS.md can map tools to commands).
_VIEW_COMMANDS = {"show": "view.show", "hide": "view.hide", "focus": "view.focus",
                  "toggle": "view.toggle"}
_NAVIGATE_COMMANDS = {"scroll": "view.scroll", "zoom": "view.zoom"}


def register(mcp: Any, bridge: BridgeClient) -> None:
    """Register the view tools on the MCP app."""

    @mcp.tool()
    def live_view_selection() -> Any:
        """Everything currently selected in Live, in one call — use it to find out what the user
        is looking at ("this clip", "the selected track", "that device").

        Returns:
            `{track:{path,index,name,type}, scene:{path,index,name},
            clip_slot:{path, has_clip, clip_name?}, detail_clip:{path,name,is_midi,length},
            device:{path,index,name,class_name,type,is_active}, chain?, parameter?,
            focused_view:"Session"|"Arranger", appointed_device?}` — keys are omitted when
            nothing of that kind is selected.

        Gotcha: `device` is the selected device of the selected track; `parameter` is the
        last-clicked parameter (read-only in Live).
        """
        return bridge_call(bridge, "view.selection")

    @mcp.tool()
    def live_view_state(include_selection: bool = False) -> Any:
        """Which views are visible/focused, plus hot-swap browse mode, Follow and Draw Mode.

        Args:
            include_selection: also include the selection (same as `live_view_selection`).

        Returns:
            `{focused_view, visible:{Session, Arranger, Detail, "Detail/Clip",
            "Detail/DeviceChain", Browser: bool}, browse_mode, follow_song, draw_mode, views,
            selection?}`
        """
        return bridge_call(bridge, "view.state",
                           drop_none(include_selection=include_selection or None))

    @mcp.tool()
    def live_view_show(view: str, action: str = "show", hotswap: bool = False) -> Any:
        """Show, hide, focus or toggle one of Live's views.

        Args:
            view: "Session", "Arranger", "Detail", "Detail/Clip" (clip editor),
                "Detail/DeviceChain" (devices), "Browser" — aliases like "arrangement", "clip",
                "devices" work; "" = the visible main view.
            action: "show" (default), "hide", "focus" (show + keyboard focus) or "toggle".
            hotswap: with view="Browser": use Live's hot-swap toggle instead (reveals the
                device chain + browser and hot-swaps the selected device; call again to stop).

        Returns:
            `{view, action, focused_view, visible}` (or `{browser_visible, browse_mode}` for
            the browser toggle).

        Gotcha: showing "Session" or "Arranger" switches the main window between the two.
        """
        if action not in _VIEW_COMMANDS:
            return tool_error("action must be 'show', 'hide', 'focus' or 'toggle'",
                              cmd="view.show")
        is_browser = view.strip().lower() == "browser"
        if hotswap:
            if not is_browser:
                return tool_error("hotswap only applies to view='Browser'",
                                  cmd="view.toggle_browser")
            return bridge_call(bridge, "view.toggle_browser", {"hotswap": True})
        if action == "toggle" and not view.strip():
            return tool_error("toggle needs a concrete view name", cmd="view.toggle")
        return bridge_call(bridge, _VIEW_COMMANDS[action], {"view": view})

    @mcp.tool()
    def live_view_select(
        track: int | str | None = None,
        scene: int | str | None = None,
        slot: int | str | None = None,
        device: int | str | None = None,
        clip: str | None = None,
        chain: str | None = None,
        show: bool = False,
    ) -> Any:
        """Select things in Live — any combination in one call (does not change the set).

        Args:
            track: index, name (exact, then prefix), "master" or a path ("song.return_tracks[0]").
            scene: index, name or path.
            slot: clip slot index (or scene name) on `track` (default: the selected track);
                highlights it and puts its clip, if any, into the Detail view.
            device: index, name or path of a device on `track` (default: the selected track);
                paths may point inside racks ("song.tracks[0].devices[1].chains[0].devices[0]").
            clip: clip to show in the Detail view — a path (also an arrangement clip
                "song.tracks[0].arrangement_clips[2]"), a clip name or "selected".
            chain: LOM path of a rack chain to select.
            show: also reveal the matching detail view (clip editor / device chain).

        Returns:
            The new selection (same shape as `live_view_selection`).

        Gotchas: selecting a slot also selects its track and scene; return/master tracks have
        no clip slots. Selecting a device on another track needs `track` too.
        """
        if all(v is None for v in (track, scene, slot, device, clip, chain)):
            return tool_error("pass at least one of track, scene, slot, device, clip or chain",
                              cmd="view.select")
        if clip is not None and not clip.strip():
            return tool_error("clip must be a path, a clip name or 'selected'",
                              cmd="view.select")
        args = drop_none(track=track, scene=scene, slot=slot, device=device, clip=clip,
                         chain=chain, show=show or None)
        return bridge_call(bridge, "view.select", args)

    @mcp.tool()
    def live_view_clip_editor(
        clip: str | None = None,
        track: int | str | None = None,
        slot: int | str | None = None,
        envelope: int | str | None = None,
        device: int | str | None = None,
        hide_envelope: bool = False,
        grid: str | int | None = None,
        triplet: bool | None = None,
        show_loop: bool = False,
        show: bool = True,
    ) -> Any:
        """Drive Live's clip editor (Detail/Clip): show a clip, open a parameter's envelope
        lane (e.g. to show the user automation you just wrote), set the editor grid, zoom to
        the loop.

        Args:
            clip: The clip — path (session or arrangement clip), clip name or "selected".
                Or track + slot (session address). Default: the clip already in the Detail view.
            track, slot: Session address of the clip.
            envelope: Parameter whose envelope lane to open — a parameter of the clip's own
                track: "volume", "pan", "send A", a device parameter name ("Filter Freq",
                "Operator > Filter Freq"), an index with `device`, or a LOM path.
            device: Device of a parameter name/index.
            hide_envelope: Close the envelope lane (back to notes / waveform).
            grid: Editor grid — "none", "8 bars", "4 bars", "2 bars", "1 bar", "1/2", "1/4",
                "1/8", "1/16", "1/32".
            triplet: Triplet grid on/off.
            show_loop: Zoom the editor so the whole loop is visible.
            show: Put the clip into the Detail view and reveal Detail/Clip (default true).

        Returns:
            `{detail_clip: {path, name, is_midi, length}, shown, envelope?: {name, device, path}
            | "hidden", grid: {quantization, triplet}, loop_shown?}`

        Gotchas: Live gives no read-back of which lane is open; only parameters of the clip's own
        track have lanes. The grid is the clip editor's, not the Arrangement grid. Does not
        change the set (no undo step).
        """
        cmd = "view.clip_editor"
        if envelope is not None and hide_envelope:
            return tool_error("pass envelope or hide_envelope, not both", cmd=cmd)
        if track is not None and slot is None and clip is None:
            return tool_error("slot is required with track", cmd=cmd)
        args = drop_none(clip=clip, track=track, slot=slot, envelope=envelope, device=device,
                         hide_envelope=hide_envelope or None, grid=grid, triplet=triplet,
                         show_loop=show_loop or None)
        if not show:
            args["show"] = False
        return bridge_call(bridge, cmd, args)

    @mcp.tool()
    def live_view_navigate(
        direction: str,
        action: str = "scroll",
        view: str = "",
        steps: int = 1,
        modifier: bool = False,
    ) -> Any:
        """Scroll or zoom a view, like the arrow/zoom keys.

        Args:
            direction: "up", "down", "left" or "right".
            action: "scroll" (Arranger, Session, Browser, Detail/DeviceChain) or "zoom"
                (Arranger, Session).
            view: the view to act on; "" (default) = the visible main view.
            steps: repeat 1..50 times.
            modifier: behave as if the modifier key were held (Live's own meaning).

        Returns:
            `{view, direction, steps, modifier}`

        Gotchas: for zoom, "right" zooms the time axis in and "left" out (verified; the
        Arrangement grid follows — it is what new locators snap to), "up"/"down" zoom
        vertically. Live silently ignores views that cannot scroll/zoom and clamps at the
        zoom limits.
        """
        if action not in _NAVIGATE_COMMANDS:
            return tool_error("action must be 'scroll' or 'zoom'", cmd="view.scroll")
        cmd = _NAVIGATE_COMMANDS[action]
        if direction not in ("up", "down", "left", "right"):
            return tool_error("direction must be 'up', 'down', 'left' or 'right'", cmd=cmd)
        if not 1 <= steps <= 50:
            return tool_error("steps must be within 1..50", cmd=cmd)
        return bridge_call(bridge, cmd, {"direction": direction, "view": view, "steps": steps,
                                         "modifier": modifier})

    @mcp.tool()
    def live_view_set(follow_song: bool | None = None, draw_mode: bool | None = None) -> Any:
        """Toggle Follow (the Arranger scrolls with the playhead) and Draw Mode (pencil).

        Args:
            follow_song: Follow on/off.
            draw_mode: envelope/note Draw Mode on/off.

        Returns:
            `{"follow_song": bool, "draw_mode": bool}`
        """
        if follow_song is None and draw_mode is None:
            return tool_error("pass follow_song and/or draw_mode", cmd="view.set")
        return bridge_call(bridge, "view.set",
                           drop_none(follow_song=follow_song, draw_mode=draw_mode))
