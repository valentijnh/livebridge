"""Generic Live Object Model tools — the escape hatch.

Anything in Live that has no curated tool is still reachable here: read/write any property, call
any method, inspect any object, and (when allowed) run Python inside Live.

LOM path grammar (``docs/ARCHITECTURE.md`` §5)::

    path    := root ("." segment)*
    root    := "song" | "app" | "browser"
    segment := name | name "[" index "]"        # index is 0-based, negatives allowed

Examples::

    song                                          the Live set
    song.tracks[2]                                third regular track
    song.tracks[2].mixer_device.volume            its volume parameter
    song.tracks[2].devices[0].parameters[3]       fourth parameter of its first device
    song.tracks[0].clip_slots[3].clip             clip in session slot 4 (None if empty)
    song.tracks[0].arrangement_clips[1]           second clip on the arrangement timeline
    song.tracks[1].devices[0].chains[0].devices[0]   first device inside a rack chain
    song.tracks[0].devices[0].drum_pads[36].chains[0] chain under drum pad C1 (note 36)
    song.return_tracks[0]   song.master_track   song.scenes[1]   song.cue_points[0]
    song.view.selected_track   app.view   browser.instruments

Every object summary returned by LiveBridge carries its own canonical ``path``, so navigate by
copying paths out of results rather than guessing them.
"""

from __future__ import annotations

from typing import Any

from . import bridge_call, drop_none, tool_error
from ..client import BridgeClient

_DETAILS = ("minimal", "summary", "full")

_PATH_HELP = (
    "Paths look like song.tracks[0].devices[1].parameters[2] — roots are 'song', 'app' and "
    "'browser'; indices are 0-based."
)


def register(mcp: Any, bridge: BridgeClient) -> None:
    """Register the generic LOM tools on the MCP app."""

    @mcp.tool()
    def live_lom_get(path: str, prop: str | None = None, detail: str = "summary") -> Any:
        """Read any object or property from the Live Object Model.

        Args:
            path: LOM path, e.g. `"song"`, `"song.tracks[2]"`,
                `"song.tracks[0].clip_slots[3].clip"`, `"song.master_track.mixer_device.volume"`,
                `"browser.instruments"`. Roots: `song`, `app`, `browser`. Indices are 0-based and
                may be negative (`song.tracks[-1]` = last track).
            prop: Property to read on that object, e.g. `"name"`, `"tempo"`, `"is_playing"`,
                `"value"`, `"length"`. Omit to get a compact summary of the object itself.
            detail: How much to include when the answer is a LOM object: `"minimal"`,
                `"summary"` (default) or `"full"`.

        Returns:
            With `prop`: the plain value (number/string/bool/None), or a summary dict when the
            property is itself a LOM object. Without `prop`: a summary dict including its `path`,
            so you can navigate deeper.

        Gotchas:
            - An empty clip slot has `clip = None`; check before addressing `....clip.name`.
            - Indices shift after adding/deleting tracks, scenes or clips — re-read a summary
              instead of reusing an old index.
            - Use `live_lom_describe` when you do not know which properties an object has.
        """
        if not isinstance(path, str) or not path.strip():
            return tool_error(f"path must be a non-empty string. {_PATH_HELP}", cmd="lom.get")
        if detail not in _DETAILS:
            return tool_error(f"detail must be 'minimal', 'summary' or 'full', got {detail!r}",
                              cmd="lom.get")
        return bridge_call(bridge, "lom.get",
                           drop_none(path=path.strip(), prop=prop,
                                     detail=None if detail == "summary" else detail))

    @mcp.tool()
    def live_lom_set(path: str, prop: str, value: Any) -> Any:
        """Write any property in the Live Object Model.

        Args:
            path: LOM path of the object, e.g. `"song"`, `"song.tracks[1]"`,
                `"song.tracks[1].devices[0].parameters[2]"`.
            prop: Property name, e.g. `"tempo"`, `"name"`, `"mute"`, `"value"`, `"looping"`.
            value: New value. Numbers, strings, booleans and lists are converted to what Live
                expects; a `DeviceParameter` value is clamped to its `min`/`max`.

        Returns:
            ``{"path", "prop", "value"}`` with the value **after** the write (Live may clamp or
            quantise it), or the friendly error shape.

        Gotchas:
            - Read-only properties come back as `type: "invalid_state"` or `"unsupported"`;
              many Live properties (e.g. `is_playing` on a clip) are read-only by design.
            - Mixer values are normalised 0.0–1.0, not dB. Use
              `song.tracks[i].mixer_device.volume` + `value`, and read `display_value` (via
              `live_lom_get(path)`) to see the dB Live shows.
            - Quantised parameters only accept the indices listed in their `value_items`.
            - Each write is one undo step in Live.
        """
        if not isinstance(path, str) or not path.strip():
            return tool_error(f"path must be a non-empty string. {_PATH_HELP}", cmd="lom.set")
        if not isinstance(prop, str) or not prop.strip():
            return tool_error(
                "prop must be the property name to write, e.g. 'tempo' or 'name'", cmd="lom.set"
            )
        return bridge_call(bridge, "lom.set", {"path": path.strip(), "prop": prop.strip(), "value": value})

    @mcp.tool()
    def live_lom_call(
        path: str,
        method: str,
        args: list[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
        detail: str = "summary",
    ) -> Any:
        """Call any method on a Live Object Model object.

        Args:
            path: LOM path of the object, e.g. `"song"`, `"song.tracks[0].clip_slots[2]"`,
                `"song.view"`.
            method: Method name, e.g. `"start_playing"`, `"stop_all_clips"`, `"fire"`,
                `"create_clip"`, `"delete_clip"`, `"duplicate_track"`, `"tap_tempo"`.
            args: Positional arguments as a list, e.g. `[4.0]` for `create_clip(length)`,
                `[2]` for `delete_track(index)`. Omit or `[]` for methods without arguments.
                Strings that are LOM paths (`"song.tracks[0].clip_slots[0].clip"`) are resolved
                to the object, so methods that take a Live object work too.
            kwargs: Keyword arguments where Live names them, e.g.
                `{"DeviceName": "Reverb", "DeviceIndex": -1}` for `insert_device`,
                `{"Index": -1}` for `create_midi_track` (same path rule as `args`).
            detail: Detail level when the method returns a LOM object.

        Returns:
            The method's return value (LOM objects come back summarised), or `null` for methods
            that return nothing, or the friendly error shape.

        Gotchas:
            - Arguments are positional, in the order the Live API defines them.
            - `clip_slot.create_clip(length)` only works on MIDI tracks and fails with
              `invalid_state` when the slot already holds a clip.
            - Objects that have no LOM path (e.g. a browser item for `browser.load_item`) cannot
              be passed — use the curated tools (`live_browser_load`) or `live_eval_python`.
            - Mutating calls are wrapped in one undo step.
        """
        if not isinstance(path, str) or not path.strip():
            return tool_error(f"path must be a non-empty string. {_PATH_HELP}", cmd="lom.call")
        if not isinstance(method, str) or not method.strip():
            return tool_error("method must be the method name to call, e.g. 'fire'", cmd="lom.call")
        if args is not None and not isinstance(args, list):
            return tool_error("args must be a list of positional arguments, e.g. [4.0]", cmd="lom.call")
        if detail not in _DETAILS:
            return tool_error(f"detail must be 'minimal', 'summary' or 'full', got {detail!r}",
                              cmd="lom.call")
        request: dict[str, Any] = {"path": path.strip(), "method": method.strip(),
                                   "args": args or []}
        if kwargs:
            request["kwargs"] = kwargs
        if detail != "summary":
            request["detail"] = detail
        return bridge_call(bridge, "lom.call", request)

    @mcp.tool()
    def live_lom_describe(path: str, include_methods: bool = True) -> Any:
        """Discover what an object offers: its properties (with current values), methods and children.

        This is how you find out what is possible on an object you have never touched — a
        third-party plugin, a rack, a Live 12 feature this build exposes.

        Args:
            path: LOM path, e.g. `"song"`, `"song.tracks[0]"`,
                `"song.tracks[0].devices[0]"`, `"browser"`, `"app.view"`.
            include_methods: False leaves out the method list (a shorter answer).

        Returns:
            ``{"type": "<Live class>", "path": ..., "properties": [{"name", "value", "type",
            "writable"}], "methods": [...], "children": [{"name", "count", "path"}]}``.

        Gotchas:
            - Listener add/remove functions are filtered out; they are useless over the wire.
            - Values that raise when read are omitted rather than failing the whole call.
            - The result can be long for devices with many parameters — read `children` counts and
              then page with `live_lom_children`.
        """
        if not isinstance(path, str) or not path.strip():
            return tool_error(f"path must be a non-empty string. {_PATH_HELP}", cmd="lom.describe")
        return bridge_call(bridge, "lom.describe",
                           drop_none(path=path.strip(),
                                     include_methods=None if include_methods else False))

    @mcp.tool()
    def live_lom_children(path: str, detail: str = "summary", offset: int = 0,
                          limit: int = 200) -> Any:
        """List the children of a LOM collection (tracks, devices, parameters, clip slots, scenes...).

        Args:
            path: LOM path of a collection or of an object with one, e.g. `"song.tracks"`,
                `"song.tracks[0].devices"`, `"song.tracks[0].devices[0].parameters"`,
                `"song.scenes"`, `"song.tracks[0].clip_slots"`, `"browser.instruments"`.
            detail: `"minimal"` (name/index/path only — cheapest), `"summary"` (default, the
                useful fields) or `"full"` (everything the serializer knows; can be large).
            offset / limit: page through a long collection (limit 1..1000).

        Returns:
            For a collection: ``{"kind": "items", "path", "total", "offset", "count",
            "items": [summary, ...], "next_offset"?}`` — every item carries its own `path`.
            For an object path (e.g. `"song.tracks[0]"`): ``{"kind": "collections", "path",
            "children": [{"name", "count", "path"}]}`` — the collections you can list next.

        Gotcha: `detail="full"` on `song.tracks` of a big set is a lot of tokens — prefer
        `live_set_snapshot` for an overview and `full` only on the one object you are working on.
        """
        if not isinstance(path, str) or not path.strip():
            return tool_error(f"path must be a non-empty string. {_PATH_HELP}", cmd="lom.children")
        if detail not in _DETAILS:
            return tool_error(
                f"detail must be 'minimal', 'summary' or 'full', got {detail!r}", cmd="lom.children"
            )
        if offset < 0 or not 1 <= limit <= 1000:
            return tool_error("offset must be >= 0 and limit within 1..1000", cmd="lom.children")
        request: dict[str, Any] = {"path": path.strip(), "detail": detail}
        if offset:
            request["offset"] = offset
        if limit != 200:
            request["limit"] = limit
        return bridge_call(bridge, "lom.children", request)

    @mcp.tool()
    def live_eval_python(code: str = "", expr: str | None = None, reset: bool = False) -> Any:
        """Run Python inside Ableton Live (last resort — anything the LOM allows).

        The code runs on Live's main thread with `song`, `app`, `browser`, `ctx` and the `Live`
        module in scope. Use it for things no tool covers: loading a browser item by object,
        bulk edits, exploring an unknown API.

        Args:
            code: Python statements to execute. Multi-line is fine. Standard library only —
                Live embeds Python 3.11 and has no third-party packages. May be empty when
                you only pass `expr`.
            expr: An expression evaluated **after** `code`, whose value is returned (summarised
                when it is a LOM object). Example: `code="t = song.tracks[0]"`,
                `expr="t.name"`.
            reset: Forget the names earlier snippets defined (they persist between calls).

        Returns:
            ``{"result": <value of expr>, "stdout": "<captured prints>"}`` or the friendly error
            shape.

        Gotchas:
            - Disabled (`type: "forbidden"`) when the Remote Script config has
              `"allow_eval": false` OR has no `"token"` (eval is always token-protected; the
              installer writes a token). `live_status` shows `allow_eval` as the effective value.
            - `exit()` / `sys.exit()` in a snippet is contained and comes back as `bad_args`.
            - This runs on Live's main thread: an endless loop freezes Live. Keep it short, never
              sleep, never start threads that touch the LOM.
            - It can destroy the user's work — prefer a curated tool, and tell the user what you
              are about to run when it is destructive.
        """
        if not (code or "").strip() and not (expr or "").strip():
            return tool_error(
                "pass code (a Python snippet, e.g. \"song.tempo = 120\") and/or expr",
                cmd="eval.python",
            )
        return bridge_call(bridge, "eval.python",
                           drop_none(code=code or None, expr=expr, reset=reset or None))
