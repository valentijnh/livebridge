"""Generic Live Object Model access — the escape hatch.

Everything in Live is reachable through these five commands, even when no
curated handler exists.  Path grammar: ``docs/ARCHITECTURE.md`` §5.
"""

from .. import compat
from .. import lom as lom_module
from .. import resolve
from .. import serialize
from ..registry import BridgeError, command


def _summarise_or_scalar(value, detail, ctx):
    """LOM objects become summaries, plain values stay plain."""
    if serialize.kind_of(value) is not None or compat.is_sequence(value):
        return ctx.summarize(value, detail)
    return serialize.scalar(value)


@command("lom.get", doc="Read any LOM path or property")
def lom_get(ctx, path, prop=None, detail="summary"):
    """Read ``path`` (or ``path.prop``).

    Args:
        path: a LOM path, e.g. ``"song.tracks[2].devices[0]"``.
        prop: optional property name to read off that object.
        detail: "minimal" | "summary" | "full" for objects that get summarised.

    Returns:
        A summary dict for LOM objects, a list for collections, the plain value
        for numbers/strings/booleans.

    Gotchas:
        An empty clip slot resolves to ``null`` (that is not an error).
        Negative indices are allowed: ``song.tracks[-1]``.
        Live raises for properties that do not apply to the object (``arm``
        on a return track, ``warping`` on a MIDI clip, ``value_items`` of a
        continuous parameter): that comes back as ``invalid_state``.
        Enum-like properties are plain ints (``launch_quantization`` 0 =
        global; ``device.type`` 4 = midi_effect) — see
        docs/LIVE_API_VERIFIED.md.
    """
    if not isinstance(path, str):
        raise BridgeError("bad_args", "path must be a string")
    value = lom_module.get(path, prop, ctx)
    return _summarise_or_scalar(value, detail, ctx)


@command("lom.set", mutating=True, doc="Write any LOM property (with coercion)")
def lom_set(ctx, path, prop, value):
    """Set ``path.prop`` to ``value``.

    Args:
        path: LOM path of the object, e.g. ``"song.tracks[0].mixer_device.volume"``.
        prop: property name, e.g. ``"value"``, ``"name"``, ``"mute"``.
        value: number, string or boolean — coerced to the property's type.
            For a property that holds a LOM object (``song.view`` →
            ``selected_track`` / ``selected_scene`` / ``detail_clip``,
            ``browser.hotswap_target``, ``track.input_routing_type``,
            ``clip.groove`` ...) pass the **path** of the object to assign, e.g.
            ``"song.tracks[2]"`` or ``"song.tracks[0].available_input_routing_types[1]"``.

    Returns:
        {"path", "prop", "value"} with the value **after** the write (a minimal
        summary when it is a LOM object), plus ``display_value`` for device
        parameters.

    Gotchas:
        Device parameter values are clamped into ``[min, max]`` instead of
        failing, and quantized parameters accept one of their ``value_items``
        by name (``"On"``). Enum-valued properties accept the Live member
        name (``"repitch"``, ``"q_sixteenth"``, ``"off"``). Read-only
        properties (``clip.length``, ``device.is_active`` — toggle a device via
        ``parameters[0]`` "Device On" — ``cue_point.time``) and properties that
        do not apply to the object raise ``invalid_state``.
    """
    if not isinstance(path, str):
        raise BridgeError("bad_args", "path must be a string")
    return lom_module.set_property(path, prop, value, ctx)


@command("lom.call", mutating=True, doc="Call any LOM method")
def lom_call(ctx, path, method, args=None, kwargs=None, detail="summary"):
    """Call ``path.method(*args, **kwargs)``.

    Args:
        path: LOM path of the object.
        method: method name, e.g. ``"fire"``, ``"create_clip"``.
        args: optional list of positional arguments (JSON values, or LOM paths
            for arguments that must be objects — those are resolved for you).
        kwargs: optional object of keyword arguments (same rule).
        detail: detail level for a returned LOM object.

    Returns:
        The method's return value, summarised when it is a LOM object,
        ``null`` for methods that return nothing.

    Gotchas:
        This is marked mutating, so every call is one undo step. Strings that
        look like a LOM path (``"song..."``, ``"app..."``, ``"browser..."``)
        are resolved to objects — pass a non-path string if you meant text.
        Most Live methods only take positional ``args``; ``kwargs`` work only
        where Live names the argument (``insert_device(DeviceName,
        DeviceIndex)``, ``create_midi_track(Index)``, ``fire(record_length,
        launch_quantization, force_legato)`` ...). Several methods return
        ``null`` although they create something (``duplicate_track``,
        ``duplicate_scene``, ``set_or_delete_cue``).
    """
    if not isinstance(path, str):
        raise BridgeError("bad_args", "path must be a string")
    if args is not None and not isinstance(args, list):
        raise BridgeError("bad_args", "args must be a list")
    if kwargs is not None and not isinstance(kwargs, dict):
        raise BridgeError("bad_args", "kwargs must be an object")
    resolved_args = [_maybe_resolve(a, ctx) for a in (args or [])]
    resolved_kwargs = dict((k, _maybe_resolve(v, ctx)) for k, v in (kwargs or {}).items())
    value = lom_module.call(path, method, resolved_args, resolved_kwargs, ctx)
    return _summarise_or_scalar(value, detail, ctx)


def _maybe_resolve(value, ctx):
    if isinstance(value, str) and (value.startswith("song.") or value.startswith("app.")
                                   or value.startswith("browser.")):
        try:
            return lom_module.resolve(value, ctx)
        except BridgeError:
            return value
    return value


@command("lom.describe", doc="List properties, methods and children of any LOM object")
def lom_describe(ctx, path, include_methods=True):
    """Discover what an object can do — the way to explore unknown parts of Live.

    Args:
        path: LOM path, e.g. ``"song.tracks[0].devices[0]"``.
        include_methods: set to False for a shorter answer.

    Returns:
        {type, path, properties:[{name, value, type, writable?}],
         methods:[...], children:[{name, count, path}]}

    Gotchas:
        Listeners (``add_*_listener``) and private names are skipped;
        properties whose read raises in the current state are omitted.
    """
    if not isinstance(path, str):
        raise BridgeError("bad_args", "path must be a string")
    return lom_module.describe(path, ctx, include_methods=bool(include_methods))


@command("lom.children", doc="List the children of a LOM path")
def lom_children(ctx, path, detail="minimal", offset=0, limit=200):
    """List a collection (``song.tracks``) or an object's child collections.

    Args:
        path: LOM path.
        detail: "minimal" | "summary" | "full" for the listed items.
        offset: skip this many items (paging).
        limit: max items to return (paging; max 1000).

    Returns:
        For a collection: {"kind": "items", "path", "total", "offset",
        "count" (returned), "items": [summary, ...], "next_offset"?}.  For an
        object: {"kind": "collections", "path", "children": [{name, count,
        path}]}.

    Gotchas:
        Browser folders load lazily — listing ``browser.samples`` makes Live
        read that folder, which can take a moment on a big library.
    """
    if not isinstance(path, str):
        raise BridgeError("bad_args", "path must be a string")
    resolve.check_detail(detail)
    offset, limit = resolve.check_paging(offset, limit, maximum=1000)
    kind, payload = lom_module.children(path, ctx)
    if kind == "items":
        window = payload[offset:offset + limit]
        return resolve.paged("items", [ctx.summarize(item, detail) for item in window],
                             offset, len(payload), extra={"kind": "items", "path": path})
    return {"kind": "collections", "path": path, "children": payload}
