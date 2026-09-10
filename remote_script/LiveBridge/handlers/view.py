"""Views and selection: show/hide/focus Live's views, read and change what is
selected, the detail clip and its clip editor (envelope lane, grid, loop
zoom — ``Clip.View``), zoom/scroll, follow and draw mode.

View names (Live's ``Application.View`` identifiers): ``"Session"``,
``"Arranger"``, ``"Detail"``, ``"Detail/Clip"``, ``"Detail/DeviceChain"``,
``"Browser"`` — ``""`` means the visible main view.  Friendly aliases are
accepted (``"arrangement"``, ``"clip"``, ``"devices"``, ...).

Nothing here touches the Live document, so no command is ``mutating`` (view
changes and selection are not undoable in Live).
"""

from .. import compat
from .. import serialize
from ..registry import BridgeError, command
from . import clips as clip_handlers

#: Live's main view identifiers (``app.view.available_main_views()`` in 12.4.5).
MAIN_VIEWS = ("Browser", "Arranger", "Session", "Detail", "Detail/Clip",
              "Detail/DeviceChain")

_ALIASES = {
    "": "", "main": "", "current": "", "document": "",
    "session": "Session", "sessionview": "Session", "clipslots": "Session",
    "arranger": "Arranger", "arrangement": "Arranger", "arrangementview": "Arranger",
    "arrangerview": "Arranger", "timeline": "Arranger",
    "detail": "Detail", "detailview": "Detail",
    "detail/clip": "Detail/Clip", "clip": "Detail/Clip", "clipview": "Detail/Clip",
    "clipdetail": "Detail/Clip", "detailclip": "Detail/Clip", "editor": "Detail/Clip",
    "noteeditor": "Detail/Clip", "sampleeditor": "Detail/Clip",
    "detail/devicechain": "Detail/DeviceChain", "devicechain": "Detail/DeviceChain",
    "devices": "Detail/DeviceChain", "device": "Detail/DeviceChain",
    "deviceview": "Detail/DeviceChain", "detaildevicechain": "Detail/DeviceChain",
    "browser": "Browser",
}

_DIRECTIONS = {"up": 0, "down": 1, "left": 2, "right": 3}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _live(what, func, *args):
    try:
        return func(*args)
    except BridgeError:
        raise
    except Exception as error:
        raise BridgeError("invalid_state", "%s: Live refused (%s: %s)"
                          % (what, type(error).__name__, error))


def _assign(obj, prop, value, what):
    try:
        setattr(obj, prop, value)
    except AttributeError:
        raise BridgeError("unsupported", "%s cannot be set in this Live version" % what)
    except Exception as error:
        raise BridgeError("invalid_state", "%s: Live refused (%s)" % (what, error))


def _bool(value, name):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    raise BridgeError("bad_args", "%s must be true or false, got %r" % (name, value))


def _app_view(ctx):
    app_view = compat.safe_getattr(ctx.app, "view")
    if app_view is None:
        raise BridgeError("unsupported", "the application view is not available")
    return app_view


def available_views(ctx):
    app_view = compat.safe_getattr(ctx.app, "view")
    ok, names = compat.safe_call(app_view, "available_main_views")
    if ok and names:
        try:
            return [str(n) for n in names]
        except Exception:
            pass
    return list(MAIN_VIEWS)


def view_name(ctx, name, allow_empty=True):
    """Canonical Live view identifier for ``name`` (case/alias tolerant)."""
    if name is None:
        name = ""
    if not isinstance(name, str):
        raise BridgeError("bad_args", "view must be a string such as 'Session', "
                          "'Arranger', 'Detail/Clip', 'Detail/DeviceChain', 'Browser'")
    known = available_views(ctx)
    if name in known:
        return name
    key = name.strip().lower().replace(" ", "").replace("_", "").replace("-", "")
    for candidate in known:
        if candidate.lower().replace(" ", "") == key:
            return candidate
    if key in _ALIASES:
        resolved = _ALIASES[key]
        if resolved == "" and not allow_empty:
            raise BridgeError("bad_args", "a concrete view name is required here")
        return resolved
    raise BridgeError("bad_args", "unknown view %r — use one of %s (or '' for the main view)"
                      % (name, ", ".join(known)))


def _mini(ctx, obj):
    if obj is None:
        return None
    data = ctx.summarize(obj, "minimal")
    if isinstance(data, dict):
        data.pop("kind", None)
    return data


def _mini_clip(ctx, clip):
    data = _mini(ctx, clip)
    if isinstance(data, dict):
        for key in ("is_audio",):
            data.pop(key, None)
    return data


def _slot_state(ctx, slot):
    if slot is None:
        return None
    clip = compat.safe_getattr(slot, "clip")
    data = {"path": ctx.path_of(slot), "has_clip": clip is not None}
    if clip is not None:
        data["clip_name"] = compat.safe_getattr(clip, "name")
    return data


def selection_state(ctx):
    """Everything currently selected in Live, compactly (``None`` = nothing)."""
    song = ctx.song
    song_view = compat.safe_getattr(song, "view")
    track = compat.safe_getattr(song_view, "selected_track")
    track_view = compat.safe_getattr(track, "view")
    device = compat.safe_getattr(track_view, "selected_device")
    app_view = compat.safe_getattr(ctx.app, "view")
    data = {
        "track": _mini(ctx, track),
        "scene": _mini(ctx, compat.safe_getattr(song_view, "selected_scene")),
        "clip_slot": _slot_state(ctx, compat.safe_getattr(song_view, "highlighted_clip_slot")),
        "detail_clip": _mini_clip(ctx, compat.safe_getattr(song_view, "detail_clip")),
        "device": _mini(ctx, device),
        "chain": _mini(ctx, compat.safe_getattr(song_view, "selected_chain")),
        "parameter": _mini(ctx, compat.safe_getattr(song_view, "selected_parameter")),
        "focused_view": compat.safe_getattr(app_view, "focused_document_view"),
    }
    appointed = compat.safe_getattr(song, "appointed_device")
    if appointed is not None and appointed != device:
        data["appointed_device"] = ctx.path_of(appointed)
    return dict((k, v) for k, v in data.items() if v is not None)


def _is_visible(app_view, name, main_window_only=True):
    if main_window_only:
        return bool(_live("is_view_visible", app_view.is_view_visible, name))
    return bool(_live("is_view_visible", app_view.is_view_visible, name, False))


def _visibility(ctx):
    app_view = _app_view(ctx)
    visible = {}
    for name in available_views(ctx):
        ok, value = compat.safe_call(app_view, "is_view_visible", name)
        if ok:
            visible[name] = bool(value)
    return visible


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------

@command("view.selection", doc="Everything currently selected (track, scene, slot, clip, device...)")
def view_selection(ctx):
    """What the user (or Claude) has selected right now, in one call.

    Returns:
        {track, scene, clip_slot:{path,has_clip,clip_name?}, detail_clip,
        device, chain, parameter, focused_view, appointed_device?} — each object
        as a minimal summary with its ``path``; keys are omitted when nothing is
        selected.

    Gotchas:
        ``device`` is the selected device of the selected track
        (``song.view.selected_track.view.selected_device``); ``parameter`` is
        the last clicked parameter (read-only in Live).
    """
    return selection_state(ctx)


@command("view.state", doc="Which views are visible/focused, browse mode, follow, draw mode")
def view_state(ctx, include_selection=False):
    """The state of Live's window.

    Args:
        include_selection: also include ``view.selection``.

    Returns:
        {focused_view, visible:{Session:bool, Arranger:bool, Detail:bool,
        "Detail/Clip":bool, "Detail/DeviceChain":bool, Browser:bool},
        browse_mode, follow_song, draw_mode, views:[...], selection?}
    """
    app_view = _app_view(ctx)
    song_view = compat.safe_getattr(ctx.song, "view")
    data = {
        "focused_view": compat.safe_getattr(app_view, "focused_document_view"),
        "visible": _visibility(ctx),
        "browse_mode": compat.safe_getattr(app_view, "browse_mode"),
        "follow_song": compat.safe_getattr(song_view, "follow_song"),
        "draw_mode": compat.safe_getattr(song_view, "draw_mode"),
        "views": available_views(ctx),
    }
    if _bool(include_selection, "include_selection"):
        data["selection"] = selection_state(ctx)
    return dict((k, v) for k, v in data.items() if v is not None)


@command("view.is_visible", doc="Is a view currently visible?")
def view_is_visible(ctx, view, main_window_only=True):
    """Check one view.

    Args:
        view: view name or alias ("Session", "Arranger", "Detail/Clip", "browser" ...).
        main_window_only: false also checks Live's second window.

    Returns:
        {"view": <canonical name>, "visible": bool}
    """
    name = view_name(ctx, view)
    return {"view": name,
            "visible": _is_visible(_app_view(ctx), name,
                                   _bool(main_window_only, "main_window_only"))}


# --------------------------------------------------------------------------
# show / hide / focus
# --------------------------------------------------------------------------

def _show(ctx, view, action):
    app_view = _app_view(ctx)
    name = view_name(ctx, view, allow_empty=action != "toggle")
    if action == "toggle":
        action = "hide" if _is_visible(app_view, name) else "show"
    method = {"show": "show_view", "hide": "hide_view", "focus": "focus_view"}[action]
    if not compat.has(app_view, method):
        raise BridgeError("unsupported", "%s is not available in this Live" % method)
    _live(method, getattr(app_view, method), name)
    result = {"view": name or compat.safe_getattr(app_view, "focused_document_view"),
              "action": action,
              "focused_view": compat.safe_getattr(app_view, "focused_document_view")}
    ok, visible = compat.safe_call(app_view, "is_view_visible", name)
    if ok:
        result["visible"] = bool(visible)
    return result


@command("view.show", doc="Show a view (Session, Arranger, Detail/Clip, Browser...)")
def view_show(ctx, view):
    """Make a view visible.  Showing "Session" or "Arranger" switches the main view.

    Args:
        view: "Session", "Arranger", "Detail", "Detail/Clip",
            "Detail/DeviceChain", "Browser" (aliases: "arrangement", "clip",
            "devices", ...) or "" for the current main view.

    Returns:
        {view, action:"show", focused_view, visible}
    """
    return _show(ctx, view, "show")


@command("view.hide", doc="Hide a view")
def view_hide(ctx, view):
    """Hide a view (e.g. "Browser" or "Detail").

    Args:
        view: see ``view.show``.

    Returns:
        {view, action:"hide", focused_view, visible}
    """
    return _show(ctx, view, "hide")


@command("view.focus", doc="Show and focus a view (keyboard focus)")
def view_focus(ctx, view):
    """Show a view and give it keyboard focus.

    Args:
        view: see ``view.show``.

    Returns:
        {view, action:"focus", focused_view, visible}
    """
    return _show(ctx, view, "focus")


@command("view.toggle", doc="Toggle a view's visibility")
def view_toggle(ctx, view):
    """Show the view when hidden, hide it when visible.

    Args:
        view: see ``view.show`` ("" is not allowed here).

    Returns:
        {view, action:"show"|"hide", focused_view, visible}
    """
    return _show(ctx, view, "toggle")


@command("view.toggle_browser", doc="Show/hide the browser (or toggle hot-swap mode)")
def view_toggle_browser(ctx, hotswap=False, visible=None):
    """Toggle Live's browser.

    Args:
        hotswap: true = Live's hot-swap toggle (``toggle_browse``): reveals the
            device chain and browser and starts hot-swapping the selected
            device; call again to stop.
        visible: force the browser shown (true) or hidden (false) instead of
            toggling (ignored with ``hotswap``).

    Returns:
        {"browser_visible": bool, "browse_mode": bool}
    """
    app_view = _app_view(ctx)
    if _bool(hotswap, "hotswap"):
        if not compat.has(app_view, "toggle_browse"):
            raise BridgeError("unsupported", "toggle_browse is not available in this Live")
        _live("toggle_browse", app_view.toggle_browse)
    else:
        if visible is None:
            show = not _is_visible(app_view, "Browser")
        else:
            show = _bool(visible, "visible")
        method = app_view.show_view if show else app_view.hide_view
        _live("show_view" if show else "hide_view", method, "Browser")
    ok, now_visible = compat.safe_call(app_view, "is_view_visible", "Browser")
    return {"browser_visible": bool(now_visible) if ok else None,
            "browse_mode": compat.safe_getattr(app_view, "browse_mode")}


# --------------------------------------------------------------------------
# selection
# --------------------------------------------------------------------------

@command("view.select", doc="Select a track, scene, clip slot, device and/or detail clip")
def view_select(ctx, track=None, scene=None, slot=None, device=None, clip=None,
                chain=None, show=False):
    """Change the selection — any combination in one call.

    Args:
        track: index, name (exact, then case-insensitive prefix), "master",
            or a LOM path ("song.return_tracks[0]").
        scene: index, name or path.
        slot: clip slot index (or scene name) on ``track`` (default: the
            selected track) — highlights it and, when it holds a clip, shows
            that clip in the Detail view.
        device: index, name or path of a device on ``track`` (default: the
            selected track); paths may point inside racks.
        clip: a clip to show in the Detail view — a LOM path (also an
            arrangement clip "song.tracks[0].arrangement_clips[2]" or a clip
            slot), a clip name, or "selected" — the same forms every clip
            command accepts.
        chain: LOM path of a rack chain to select.
        show: also reveal the matching detail view (Detail/Clip for clips,
            Detail/DeviceChain for devices).

    Returns:
        The new selection (same shape as ``view.selection``).

    Gotchas:
        Selecting a clip slot also selects its track and scene.  Selecting a
        device on another track does not reveal that track — pass ``track``
        too.  Return/master tracks have no clip slots.  Every argument is
        resolved before anything is selected, so an error changes nothing.
    """
    song = ctx.song
    song_view = compat.safe_getattr(song, "view")
    if song_view is None:
        raise BridgeError("unsupported", "song.view is not available")
    if all(v is None for v in (track, scene, slot, device, clip, chain)):
        raise BridgeError("bad_args", "pass at least one of track, scene, slot, device, "
                          "clip or chain")
    show = _bool(show, "show")
    # Resolve and validate everything first, so a bad argument changes nothing.
    track_obj = ctx.track(track) if track is not None else None
    owner = track_obj if track_obj is not None else compat.safe_getattr(
        song_view, "selected_track")
    scene_obj = ctx.scene(scene) if scene is not None else None
    clip_slot = slot_clip = None
    if slot is not None:
        if owner is None:
            raise BridgeError("bad_args", "pass track together with slot")
        clip_slot = ctx.clip_slot(owner, slot)
        slot_clip = compat.safe_getattr(clip_slot, "clip")
    clip_obj = None
    if clip is not None:
        clip_obj = clip_handlers.resolve_clip(ctx, clip=clip)
    device_obj = None
    if device is not None:
        if owner is None and not (isinstance(device, str) and device.startswith("song.")):
            raise BridgeError("bad_args", "pass track together with device")
        device_obj = ctx.device(owner, device) if owner is not None else ctx.resolve(device)
        if device_obj is None or serialize.kind_of(device_obj) != "device":
            raise BridgeError("not_found", "%r: no device there" % (device,))
        if not compat.has(song_view, "select_device"):
            raise BridgeError("unsupported", "select_device is not available in this Live")
    chain_obj = None
    if chain is not None:
        chain_obj = ctx.resolve(chain) if isinstance(chain, str) else None
        if chain_obj is None or serialize.kind_of(chain_obj) != "chain":
            raise BridgeError("not_found", "%r: no chain there (pass a LOM path)" % (chain,))
    reveal = None
    if track_obj is not None:
        _assign(song_view, "selected_track", track_obj, "selected_track")
    if scene_obj is not None:
        _assign(song_view, "selected_scene", scene_obj, "selected_scene")
    if clip_slot is not None:
        _assign(song_view, "highlighted_clip_slot", clip_slot, "highlighted_clip_slot")
        if slot_clip is not None:
            _assign(song_view, "detail_clip", slot_clip, "detail_clip")
            reveal = "Detail/Clip"
    if clip_obj is not None:
        _assign(song_view, "detail_clip", clip_obj, "detail_clip")
        reveal = "Detail/Clip"
    if device_obj is not None:
        _live("select_device", song_view.select_device, device_obj)
        reveal = "Detail/DeviceChain"
    if chain_obj is not None:
        _assign(song_view, "selected_chain", chain_obj, "selected_chain")
    if show and reveal:
        app_view = _app_view(ctx)
        _live("show_view", app_view.show_view, reveal)
    return selection_state(ctx)


@command("view.set_detail_clip", doc="Show a clip in the Detail view")
def view_set_detail_clip(ctx, track=None, slot=None, clip=None, show=True):
    """Put a clip into Live's Detail view (clip editor).

    Args:
        track + slot: a session clip (track by index/name/path, slot index or
            scene name), or
        clip: any clip — LOM path (arrangement clips included), clip name or
            "selected".
        show: also reveal Detail/Clip (default true).

    Returns:
        {"detail_clip": {path, name, is_midi, length}, "shown": bool}
    """
    song_view = compat.safe_getattr(ctx.song, "view")
    if clip is None and (track is None or slot is None):
        raise BridgeError("bad_args", "pass track and slot, or clip (a path, name or "
                          "'selected')")
    target = clip_handlers.resolve_clip(ctx, track, slot, clip)
    _assign(song_view, "detail_clip", target, "detail_clip")
    shown = False
    if _bool(show, "show"):
        _live("show_view", _app_view(ctx).show_view, "Detail/Clip")
        shown = True
    return {"detail_clip": _mini_clip(ctx, compat.safe_getattr(song_view, "detail_clip")),
            "shown": shown}


# --------------------------------------------------------------------------
# clip editor (Clip.View)
# --------------------------------------------------------------------------

#: ``Live.Clip.GridQuantization`` (clip.view.grid_quantization) — 12.4.5 dump.
GRID_NAMES = {0: "none", 1: "8 bars", 2: "4 bars", 3: "2 bars", 4: "1 bar", 5: "1/2",
              6: "1/4", 7: "1/8", 8: "1/16", 9: "1/32"}
_GRID_ALIASES = {"off": 0, "no": 0, "nogrid": 0, "bar": 4, "1bars": 4, "8bar": 1, "4bar": 2,
                 "2bar": 3, "half": 5, "quarter": 6, "eighth": 7, "sixteenth": 8,
                 "thirtysecond": 9, "1/1": 4}


def parse_grid(value):
    """``"1/16"`` / ``"1 bar"`` / ``"none"`` / 0..9 / ``"g_sixteenth"`` -> GridQuantization int."""
    if isinstance(value, bool) or value is None:
        raise BridgeError("bad_args", "grid must be one of %s (or 0..9)"
                          % ", ".join(repr(v) for v in GRID_NAMES.values()))
    if isinstance(value, (int, float)):
        if float(value).is_integer() and int(value) in GRID_NAMES:
            return int(value)
        raise BridgeError("bad_args", "grid %r is out of range (0..9)" % (value,))
    text = str(value).strip().lower().replace(" ", "").replace("_", "")
    for number, label in GRID_NAMES.items():
        if label.replace(" ", "") == text:
            return number
    if text in _GRID_ALIASES:
        return _GRID_ALIASES[text]
    if text.isdigit():
        return parse_grid(int(text))
    names = compat.safe_getattr(compat.live_enum("Clip.GridQuantization"), "names")
    if isinstance(names, dict):
        for member, number in names.items():
            if str(member).lower().replace("_", "") == text and int(number) in GRID_NAMES:
                return int(number)
    raise BridgeError("bad_args", "unknown grid %r — use one of %s"
                      % (value, ", ".join(repr(v) for v in GRID_NAMES.values())))


@command("view.clip_editor",
         doc="Clip editor: show a clip, open a parameter's envelope lane, set the grid, "
             "show the loop")
def view_clip_editor(ctx, clip=None, track=None, slot=None, envelope=None, device=None,
                     hide_envelope=False, grid=None, triplet=None, show_loop=False, show=True):
    """Drive Live's clip editor (``Clip.View``) — e.g. show the user the
    automation just written.

    Args:
        clip / track + slot: the clip — path (session or arrangement clip),
            clip name, "selected", or a session address; default: the clip in
            the Detail view.
        envelope: open this parameter's envelope in the clip's Envelopes box
            (``select_envelope_parameter`` + ``show_envelope``): a parameter
            of the clip's own track — "volume", "pan", "send A", a device
            parameter name ("Filter Freq", "Operator > Filter Freq"), an index
            with ``device``, or a LOM path.
        device: the device of a parameter name/index (see ``automation.get``).
        hide_envelope: close the envelope lane (back to notes / waveform).
        grid: the editor grid — "none", "8 bars", "4 bars", "2 bars", "1 bar",
            "1/2", "1/4", "1/8", "1/16", "1/32" (or 0..9, Live's
            ``GridQuantization``).
        triplet: triplet grid on/off.
        show_loop: zoom the editor so the whole loop is visible.
        show: make it the detail clip and reveal Detail/Clip (default true —
            Live only shows the envelope of the clip in the Detail view).

    Returns:
        {"detail_clip": {path, name, is_midi, length}, "shown": bool,
         "envelope"?: {"name", "device", "path"} | "hidden",
         "grid": {"quantization": "1/16", "triplet": bool}, "loop_shown"?: true}

    Gotchas:
        Live gives no read-back of which envelope lane is open.  The lane
        list only holds parameters of the clip's own track (mixer + devices).
        The grid is the clip editor's own (not the Arrangement grid that
        new locators snap to).  Nothing here changes the set (no undo step).
    """
    if track is not None and slot is None and clip is None:
        raise BridgeError("bad_args", "slot is required with track")
    if envelope is not None and _bool(hide_envelope, "hide_envelope"):
        raise BridgeError("bad_args", "pass envelope or hide_envelope, not both")
    show = _bool(show, "show")
    show_loop = _bool(show_loop, "show_loop")
    hide = _bool(hide_envelope, "hide_envelope")
    if triplet is not None:
        triplet = _bool(triplet, "triplet")
    grid_value = parse_grid(grid) if grid is not None else None
    song_view = compat.safe_getattr(ctx.song, "view")
    if clip is None and track is None:
        target = compat.safe_getattr(song_view, "detail_clip")
        if target is None:
            raise BridgeError("not_found", "no clip is in the Detail view — pass clip or "
                              "track + slot")
    else:
        target = clip_handlers.resolve_clip(ctx, track, slot, clip)
    clip_view = compat.safe_getattr(target, "view")
    if clip_view is None:
        raise BridgeError("unsupported", "this clip has no editor view (Clip.View)")
    param = label = path = None
    if envelope is not None:
        from . import automation as automation_handlers   # lazy: automation imports cues
        owner = clip_handlers.owner_track(target)
        if owner is None:
            raise BridgeError("not_found", "cannot tell which track owns this clip")
        param, label, path = automation_handlers.resolve_parameter(ctx, owner, envelope, device)
        for method in ("select_envelope_parameter", "show_envelope"):
            if not compat.has(clip_view, method):
                raise BridgeError("unsupported", "Clip.View.%s is not available in this Live"
                                  % method)
    if show:
        _assign(song_view, "detail_clip", target, "detail_clip")
        _live("show_view", _app_view(ctx).show_view, "Detail/Clip")
    result = {"detail_clip": _mini_clip(ctx, target), "shown": show}
    if grid_value is not None:
        _assign(clip_view, "grid_quantization", grid_value, "grid_quantization")
    if triplet is not None:
        _assign(clip_view, "grid_is_triplet", triplet, "grid_is_triplet")
    if param is not None:
        _live("select_envelope_parameter", clip_view.select_envelope_parameter, param)
        _live("show_envelope", clip_view.show_envelope)
        result["envelope"] = {"name": compat.safe_getattr(param, "name"), "device": label,
                              "path": path}
    elif hide:
        if not compat.has(clip_view, "hide_envelope"):
            raise BridgeError("unsupported", "Clip.View.hide_envelope is not available")
        _live("hide_envelope", clip_view.hide_envelope)
        result["envelope"] = "hidden"
    if show_loop:
        if not compat.has(clip_view, "show_loop"):
            raise BridgeError("unsupported", "Clip.View.show_loop is not available")
        _live("show_loop", clip_view.show_loop)
        result["loop_shown"] = True
    quantization = compat.safe_getattr(clip_view, "grid_quantization")
    try:
        quantization = GRID_NAMES.get(int(quantization), quantization)
    except (TypeError, ValueError):
        pass
    result["grid"] = {"quantization": quantization,
                      "triplet": bool(compat.safe_getattr(clip_view, "grid_is_triplet", False))}
    return result


# --------------------------------------------------------------------------
# zoom / scroll / flags
# --------------------------------------------------------------------------

def _direction(direction):
    if isinstance(direction, bool):
        raise BridgeError("bad_args", "direction must be up, down, left or right")
    if isinstance(direction, int) and direction in (0, 1, 2, 3):
        return direction
    if isinstance(direction, str) and direction.strip().lower() in _DIRECTIONS:
        return _DIRECTIONS[direction.strip().lower()]
    raise BridgeError("bad_args", "direction must be up, down, left or right (or 0..3)")


def _navigate(ctx, method, direction, view, modifier, steps):
    app_view = _app_view(ctx)
    if not compat.has(app_view, method):
        raise BridgeError("unsupported", "%s is not available in this Live" % method)
    code = _direction(direction)
    name = view_name(ctx, view)
    modifier = _bool(modifier, "modifier")
    if isinstance(steps, bool) or not isinstance(steps, int) or not 1 <= steps <= 50:
        raise BridgeError("bad_args", "steps must be an integer 1..50")
    function = getattr(app_view, method)
    for _ in range(steps):
        _live(method, function, code, name, modifier)
    return {"view": name or compat.safe_getattr(app_view, "focused_document_view"),
            "direction": [k for k, v in _DIRECTIONS.items() if v == code][0],
            "steps": steps, "modifier": modifier}


@command("view.zoom", doc="Zoom the Arranger or Session view")
def view_zoom(ctx, direction, view="", modifier=False, steps=1):
    """Zoom a view, like the zoom keys.

    Args:
        direction: "right" zooms the time axis in, "left" zooms it out
            (verified on Live 12.4.5: the Arrangement grid gets finer/coarser);
            "up" / "down" are Live's vertical zoom (0..3 = up, down, left, right).
        view: "Arranger" or "Session" ("" = the visible main view); the
            Arrangement zoom also works while the Session view is shown.
        modifier: act as if the modifier key were held (Live's own meaning,
            e.g. all tracks instead of the selected one).
        steps: repeat 1..50 times.

    Returns:
        {view, direction, steps, modifier}

    Gotchas:
        Live silently ignores views that cannot zoom and clamps at the
        maximum zoom, so zooming in N steps and out N steps may not return
        exactly to the old zoom.  The Arrangement zoom sets the grid that new
        locators and playhead moves snap to while stopped.
    """
    return _navigate(ctx, "zoom_view", direction, view, modifier, steps)


@command("view.scroll", doc="Scroll the Arranger, Session, Browser or device chain")
def view_scroll(ctx, direction, view="", modifier=False, steps=1):
    """Scroll a view, like the arrow keys.

    Args:
        direction: "up", "down", "left" or "right" (0..3).
        view: "Arranger", "Session", "Browser" or "Detail/DeviceChain"
            ("" = the visible main view).
        modifier: act as if the modifier key were held.
        steps: repeat 1..50 times.

    Returns:
        {view, direction, steps, modifier}

    Gotchas:
        Live silently ignores views that cannot scroll.
    """
    return _navigate(ctx, "scroll_view", direction, view, modifier, steps)


@command("view.set", doc="Set Follow (arrangement auto-scroll) and Draw Mode")
def view_set(ctx, follow_song=None, draw_mode=None):
    """Toggle view options of the song.

    Args:
        follow_song: the Arranger follows the playhead (Follow button).
        draw_mode: envelope/note Draw Mode (pencil, "B" key).

    Returns:
        {"follow_song": bool, "draw_mode": bool}
    """
    song_view = compat.safe_getattr(ctx.song, "view")
    if follow_song is None and draw_mode is None:
        raise BridgeError("bad_args", "pass follow_song and/or draw_mode")
    if follow_song is not None:
        _assign(song_view, "follow_song", _bool(follow_song, "follow_song"), "follow_song")
    if draw_mode is not None:
        _assign(song_view, "draw_mode", _bool(draw_mode, "draw_mode"), "draw_mode")
    return {"follow_song": bool(compat.safe_getattr(song_view, "follow_song", False)),
            "draw_mode": bool(compat.safe_getattr(song_view, "draw_mode", False))}
