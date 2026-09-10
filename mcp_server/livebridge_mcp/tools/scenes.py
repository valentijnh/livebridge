"""Scene tools: list/inspect, create, delete, duplicate, rename/recolour, scene tempo and time
signature, launch (with legato), capture playing clips into a new scene.

A `scene` argument is an index into `song.scenes` (0-based, negative counts from the end), a
scene name (exact, then case-insensitive prefix) or a LOM path (`"song.scenes[2]"`). Scene `i`
owns clip slot `i` of every track. Select a scene with `live_view_select(scene=...)`; stop
everything with `live_transport_stop_all_clips`.
"""

from __future__ import annotations

from typing import Any

from . import bridge_call, drop_none, tool_error
from ..client import BridgeClient

_DETAILS = ("minimal", "summary", "full")


def _bad_scene(scene: Any, cmd: str) -> dict[str, Any] | None:
    if isinstance(scene, str) and not scene.strip():
        return tool_error("scene must be an index, a name or a path like 'song.scenes[0]'",
                          cmd=cmd)
    return None


def _bad_signature(value: str | None, cmd: str) -> dict[str, Any] | None:
    if value is None:
        return None
    top, slash, bottom = value.partition("/")
    if not slash or not top.strip().isdigit() or bottom.strip() not in ("1", "2", "4", "8", "16"):
        return tool_error("time_signature must look like '3/4' (denominator 1, 2, 4, 8 or 16)",
                          cmd=cmd)
    if not 1 <= int(top) <= 99:
        return tool_error("time signature numerator must be 1..99", cmd=cmd)
    return None


def register(mcp: Any, bridge: BridgeClient) -> None:
    """Register the scene tools on the MCP app."""

    @mcp.tool()
    def live_scene_list(
        detail: str = "summary",
        include_clips: bool = False,
        offset: int = 0,
        limit: int | None = None,
    ) -> Any:
        """List the scenes of the set.

        Args:
            detail: "minimal" (index, name, path), "summary" (+ colour, tempo/signature when
                enabled, is_empty, triggered) or "full" (+ the clips in each scene).
            include_clips: add the non-empty slots per scene
                (`[{track, track_name, name, length, playing?}]`).
            offset / limit: paging for sets with many scenes.

        Returns:
            `{count, offset, selected: <index of the selected scene>, scenes:[{path, index,
            name, color_index, is_empty, tempo?, signature?, clips?}]}`
        """
        cmd = "scenes.list"
        if detail not in _DETAILS:
            return tool_error("detail must be 'minimal', 'summary' or 'full'", cmd=cmd)
        if offset < 0 or (limit is not None and limit < 1):
            return tool_error("offset must be >= 0 and limit >= 1", cmd=cmd)
        return bridge_call(bridge, cmd, drop_none(detail=detail,
                                                  include_clips=include_clips or None,
                                                  offset=offset or None, limit=limit))

    @mcp.tool()
    def live_scene_get(scene: int | str) -> Any:
        """Everything about one scene, including which clips it launches.

        Args:
            scene: index, name or path.

        Returns:
            `{path, index, name, color_index, is_empty, is_triggered, tempo_enabled, tempo?,
            time_signature_enabled, signature?, clips:[{track, track_name, name, length,
            playing?}]}`
        """
        cmd = "scenes.get"
        return _bad_scene(scene, cmd) or bridge_call(bridge, cmd, {"scene": scene})

    @mcp.tool()
    def live_scene_create(
        index: int = -1,
        name: str | None = None,
        color_index: int | None = None,
        tempo: float | None = None,
        time_signature: str | None = None,
        select: bool = False,
    ) -> Any:
        """Insert a new empty scene.

        Args:
            index: 0-based position; -1 (default) = at the end.
            name: optional name.
            color_index: optional colour 0..69.
            tempo: optional scene tempo (applied when the scene is fired).
            time_signature: optional "3/4" (applied when the scene is fired).
            select: also select the new scene.

        Returns:
            The new scene `{path, index, name, ...}`.

        Gotchas: later scene indices shift by one. Live itself would copy the tempo/signature of
        the scene above and select the new scene — this tool gives a clean scene and keeps the
        selection unless `select=true`.
        """
        cmd = "scenes.create"
        if index < -1:
            return tool_error("index must be >= 0, or -1 for the end", cmd=cmd)
        if color_index is not None and not 0 <= color_index <= 69:
            return tool_error("color_index must be within 0..69", cmd=cmd)
        if tempo is not None and not 20.0 <= tempo <= 999.0:
            return tool_error("tempo must be within 20..999", cmd=cmd)
        problem = _bad_signature(time_signature, cmd)
        if problem:
            return problem
        return bridge_call(bridge, cmd, drop_none(index=index, name=name,
                                                  color_index=color_index, tempo=tempo,
                                                  time_signature=time_signature,
                                                  select=select or None))

    @mcp.tool()
    def live_scene_delete(scene: int | str) -> Any:
        """Delete a scene together with every clip in it (one undo step).

        Args:
            scene: index, name or path.

        Returns:
            `{"deleted": {index, name}, "scene_count": n}`

        Gotcha: Live keeps at least one scene; later indices shift down by one.
        """
        cmd = "scenes.delete"
        return _bad_scene(scene, cmd) or bridge_call(bridge, cmd, {"scene": scene})

    @mcp.tool()
    def live_scene_duplicate(scene: int | str, name: str | None = None) -> Any:
        """Duplicate a scene with its clips; the copy lands right after it and is selected.

        Args:
            scene: index, name or path.
            name: optional name for the copy (Live keeps the original name otherwise, so two
                scenes share it and name lookups pick the first).

        Returns:
            The new scene `{path, index, name, ...}`.
        """
        cmd = "scenes.duplicate"
        return _bad_scene(scene, cmd) or bridge_call(bridge, cmd,
                                                     drop_none(scene=scene, name=name))

    @mcp.tool()
    def live_scene_set(
        scene: int | str,
        name: str | None = None,
        color_index: int | None = None,
        color: int | str | list[int] | None = None,
        tempo: float | None = None,
        tempo_enabled: bool | None = None,
        time_signature: str | None = None,
        time_signature_enabled: bool | None = None,
    ) -> Any:
        """Rename / recolour a scene and set its tempo and time signature (one undo step).

        Args:
            scene: index, name or path.
            name: new name ("" clears it).
            color_index: palette colour 0..69.
            color: "#RRGGBB", [r, g, b] (Live picks the nearest palette colour), a palette
                index 0..69 or a colour name ("red", "blue") — same forms as live_tracks_set.
            tempo: scene tempo 20..999 — enables it unless `tempo_enabled=false`.
            tempo_enabled: scene tempo on/off (off = the song tempo stays when fired).
            time_signature: "3/4" — enables the scene signature.
            time_signature_enabled: scene signature on/off.

        Returns:
            The scene after the change `{path, index, name, color_index, tempo?, signature?}`.

        Gotcha: a scene's tempo/signature takes effect when the scene is fired.
        """
        cmd = "scenes.set"
        problem = _bad_scene(scene, cmd) or _bad_signature(time_signature, cmd)
        if problem:
            return problem
        if color_index is not None and not 0 <= color_index <= 69:
            return tool_error("color_index must be within 0..69", cmd=cmd)
        if tempo is not None and not 20.0 <= tempo <= 999.0:
            return tool_error("tempo must be within 20..999", cmd=cmd)
        args = drop_none(name=name, color_index=color_index, color=color, tempo=tempo,
                         tempo_enabled=tempo_enabled, time_signature=time_signature,
                         time_signature_enabled=time_signature_enabled)
        if not args:
            return tool_error("nothing to change — pass name, color, tempo or time signature",
                              cmd=cmd)
        args["scene"] = scene
        return bridge_call(bridge, cmd, args)

    @mcp.tool()
    def live_scene_fire(
        scene: int | str | None = None,
        force_legato: bool = False,
        select: bool = True,
    ) -> Any:
        """Launch a scene (all its clip slots), like clicking its launch button.

        Args:
            scene: index, name or path. Omit to fire the *selected* scene and move the selection
                to the next one (Live's Enter-key behaviour — handy for stepping through a song).
            force_legato: new clips continue from the play position of the clips they replace.
            select: select the fired scene (only with `scene`).

        Returns:
            `{"fired": {index, name}, "selected": <selected scene index>, "is_playing": bool}`

        Returns also `clip_count` (clips the scene launches).

        Gotchas: a scene with clips starts the transport (on Live's next tick — `is_playing` is
        the state before); an empty scene does not, but its empty slots stop their tracks and an
        enabled scene tempo / signature is applied to the song at once. Launch waits for the
        global clip trigger quantization (`live_transport_set`).
        """
        cmd = "scenes.fire"
        problem = _bad_scene(scene, cmd)
        if problem:
            return problem
        args: dict[str, Any] = {"force_legato": force_legato}
        if scene is not None:
            args["scene"] = scene
            args["select"] = select
        return bridge_call(bridge, cmd, args)

    @mcp.tool()
    def live_scene_capture(mode: str = "all") -> Any:
        """Capture and Insert Scene: copy the clips that are playing right now into a new scene
        inserted after the selected scene.

        Args:
            mode: "all" or "all_except_selected" (leave out the selected track).

        Returns:
            The new scene with its clips `{path, index, name, clips:[...]}`.

        Gotchas: Live inserts the scene even when nothing plays (then it is empty) and copies
        the selected scene's name, tempo and signature into it; later indices shift by one.
        """
        cmd = "scenes.capture"
        if mode not in ("all", "all_except_selected"):
            return tool_error("mode must be 'all' or 'all_except_selected'", cmd=cmd)
        return bridge_call(bridge, cmd, {"mode": mode})
