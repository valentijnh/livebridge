"""Scenes: list, inspect, create, delete, duplicate, rename/colour, scene tempo
and time signature, fire (legato), capture, stop all, select.

A ``scene`` argument is an index into ``song.scenes`` (negative counts from
the end), a scene name (exact first, then case-insensitive prefix) or a LOM
path (``"song.scenes[2]"``).  Scene ``i`` owns ``song.tracks[t].clip_slots[i]``
on every track.
"""

from .. import compat
from .. import resolve
from ..registry import BridgeError, command



# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

#: Call into Live with protocol errors — shared implementation (``resolve.live_call``).
_live = resolve.live_call


def _assign(obj, prop, value, what):
    try:
        setattr(obj, prop, value)
    except AttributeError:
        raise BridgeError("unsupported", "%s cannot be set in this Live version" % what)
    except Exception as error:
        raise BridgeError("invalid_state", "%s: Live refused %r (%s)" % (what, value, error))


def _bool(value, name):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    raise BridgeError("bad_args", "%s must be true or false, got %r" % (name, value))


def _int(value, name, low=None, high=None):
    if isinstance(value, bool) or value is None:
        raise BridgeError("bad_args", "%s must be an integer, got %r" % (name, value))
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise BridgeError("bad_args", "%s must be an integer, got %r" % (name, value))
    if abs(number - round(number)) > 1e-9:
        raise BridgeError("bad_args", "%s must be a whole number, got %r" % (name, value))
    number = int(round(number))
    if (low is not None and number < low) or (high is not None and number > high):
        if high is None:
            raise BridgeError("bad_args", "%s must be >= %s, got %d" % (name, low, number))
        raise BridgeError("bad_args", "%s must be within %s..%s, got %d"
                          % (name, low, high, number))
    return number


def _float(value, name, low, high):
    if isinstance(value, bool) or value is None:
        raise BridgeError("bad_args", "%s must be a number, got %r" % (name, value))
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise BridgeError("bad_args", "%s must be a number, got %r" % (name, value))
    if not low <= number <= high:
        raise BridgeError("bad_args", "%s must be within %s..%s, got %s"
                          % (name, low, high, value))
    return number


def _round(value, digits=4):
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _scenes(ctx):
    return list(compat.safe_getattr(ctx.song, "scenes", ()) or ())


def _index_of(ctx, scene):
    for index, candidate in enumerate(_scenes(ctx)):
        if candidate == scene:
            return index
    raise BridgeError("not_found", "the scene is no longer in this set")


def _resolve(ctx, scene):
    """``(index, Scene)`` for a loose scene argument."""
    obj = ctx.scene(scene)
    for index, candidate in enumerate(_scenes(ctx)):
        if candidate == obj:
            return index, obj
    if isinstance(scene, str) and scene.strip().startswith(("song.", "app.", "browser.")):
        raise BridgeError("bad_args", "%s is not a scene — use an index, a scene name or "
                          "'song.scenes[i]'" % scene.strip())
    raise BridgeError("not_found", "the scene is no longer in this set")


def _parse_color(value):
    """A colour argument -> ``(prop, value)`` via the shared parser.

    Palette indices (0..69) and colour names set ``color_index``; RGB
    (``"#RRGGBB"``, ``0xRRGGBB``, ``[r, g, b]``) sets ``color``.
    """
    mode, number = resolve.parse_color(value)
    return ("color_index", number) if mode == "index" else ("color", number)


def _parse_signature(value):
    return resolve.parse_signature(value, "time_signature")


def _scene_clips(ctx, scene):
    """Non-empty slots of a scene: [{track, track_name, name, length, ...}]."""
    get = compat.safe_getattr
    tracks = list(get(ctx.song, "tracks", ()) or ())
    clips = []
    for track_index, slot in enumerate(get(scene, "clip_slots", ()) or ()):
        clip = get(slot, "clip")
        if clip is None:
            continue
        entry = {"track": track_index,
                 "track_name": get(tracks[track_index], "name") if track_index < len(tracks)
                 else None,
                 "name": get(clip, "name"),
                 "length": _round(get(clip, "length"))}
        for key, label in (("is_playing", "playing"), ("is_triggered", "triggered"),
                           ("is_recording", "recording")):
            if get(clip, key, False):
                entry[label] = True
        clips.append(dict((k, v) for k, v in entry.items() if v is not None))
    return clips


def _scene_data(ctx, index, scene, detail="summary", include_clips=False):
    get = compat.safe_getattr
    data = ctx.summarize(scene, "minimal" if detail == "minimal" else "summary")
    if not isinstance(data, dict):
        data = {}
    data.pop("kind", None)
    data["index"] = index
    if detail != "minimal":
        data["tempo_enabled"] = bool(get(scene, "tempo_enabled", False))
        data["time_signature_enabled"] = bool(get(scene, "time_signature_enabled", False))
        if data["tempo_enabled"]:
            data["tempo"] = _round(get(scene, "tempo"), 3)
        if data["time_signature_enabled"]:
            data["signature"] = "%s/%s" % (get(scene, "time_signature_numerator"),
                                           get(scene, "time_signature_denominator"))
        if detail == "summary":
            for key in ("tempo_enabled", "time_signature_enabled", "is_triggered"):
                if data.get(key) is False:
                    data.pop(key)
        data.pop("clip_slots", None)
    if include_clips or detail == "full":
        data["clips"] = _scene_clips(ctx, scene)
    return data


def _set_time_signature_enabled(ctx, scene, enabled):
    """Enable/disable a scene time signature.

    Live 12.4.5 exposes ``time_signature_enabled`` as writable (runtime dump);
    older builds only have it read-only — there the numerator/denominator
    are set (-1 = off) instead.
    """
    try:
        scene.time_signature_enabled = enabled
        return
    except AttributeError:
        pass
    except Exception as error:
        raise BridgeError("invalid_state", "time_signature_enabled: Live refused (%s)" % error)
    if enabled:
        song = ctx.song
        if compat.safe_getattr(scene, "time_signature_numerator", -1) in (-1, None):
            _assign(scene, "time_signature_numerator",
                    int(compat.safe_getattr(song, "signature_numerator", 4) or 4),
                    "time_signature_numerator")
        if compat.safe_getattr(scene, "time_signature_denominator", -1) in (-1, None):
            _assign(scene, "time_signature_denominator",
                    int(compat.safe_getattr(song, "signature_denominator", 4) or 4),
                    "time_signature_denominator")
    else:
        _assign(scene, "time_signature_numerator", -1, "time_signature_numerator")
        _assign(scene, "time_signature_denominator", -1, "time_signature_denominator")


def _apply(ctx, scene, name=None, color_index=None, color=None, tempo=None,
           tempo_enabled=None, time_signature=None, time_signature_enabled=None):
    """Validate everything, then write — shared by create/duplicate/set."""
    plan = []
    if name is not None:
        if not isinstance(name, str):
            raise BridgeError("bad_args", "name must be a string")
        plan.append(("name", name))
    if color_index is not None:
        plan.append(("color_index", _int(color_index, "color_index", 0, 69)))
    if color is not None:
        plan.append(_parse_color(color))
    if tempo is not None:
        plan.append(("tempo", _float(tempo, "tempo", 20.0, 999.0)))
    if tempo_enabled is not None:
        tempo_enabled = _bool(tempo_enabled, "tempo_enabled")
    signature = _parse_signature(time_signature) if time_signature is not None else None
    if time_signature_enabled is not None:
        time_signature_enabled = _bool(time_signature_enabled, "time_signature_enabled")
    if signature is not None and time_signature_enabled is False:
        raise BridgeError("bad_args", "time_signature together with "
                          "time_signature_enabled=false makes no sense")
    for prop, value in plan:
        if prop == "tempo":
            continue
        _assign(scene, prop, value, prop)
    if tempo is not None:
        value = dict(plan)["tempo"]
        _assign(scene, "tempo", value, "tempo")
        if tempo_enabled is not False:
            _assign(scene, "tempo_enabled", True, "tempo_enabled")
            if abs((compat.safe_getattr(scene, "tempo", value) or value) - value) > 1e-6:
                _assign(scene, "tempo", value, "tempo")
    if tempo_enabled is not None:
        _assign(scene, "tempo_enabled", tempo_enabled, "tempo_enabled")
    if signature is not None:
        _assign(scene, "time_signature_numerator", signature[0], "time_signature_numerator")
        _assign(scene, "time_signature_denominator", signature[1],
                "time_signature_denominator")
        if not compat.safe_getattr(scene, "time_signature_enabled", True):
            _set_time_signature_enabled(ctx, scene, True)
    elif time_signature_enabled is not None:
        _set_time_signature_enabled(ctx, scene, time_signature_enabled)


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------

@command("scenes.list", doc="List scenes (name, colour, tempo/signature, clips)")
def scenes_list(ctx, detail="summary", include_clips=False, offset=0, limit=None):
    """List the scenes of the set.

    Args:
        detail: "minimal" (index, name, path), "summary" (+ colour, tempo /
            signature when enabled, is_empty, triggered) or "full" (+ clips).
        include_clips: add the non-empty slots of each scene
            ([{track, track_name, name, length, playing?}]).
        offset / limit: paging.

    Returns:
        {"total", "offset", "count" (returned), "selected": <index or null>,
         "scenes": [...], "next_offset"?}
    """
    resolve.check_detail(detail)
    include_clips = _bool(include_clips, "include_clips")
    offset, limit = resolve.check_paging(offset, limit, maximum=10000, allow_none=True)
    scenes = _scenes(ctx)
    end = len(scenes) if limit is None else offset + limit
    selected = compat.safe_getattr(compat.safe_getattr(ctx.song, "view"), "selected_scene")
    selected_index = None
    for index, scene in enumerate(scenes):
        if selected is not None and scene == selected:
            selected_index = index
            break
    return resolve.paged("scenes", [_scene_data(ctx, i, scenes[i], detail, include_clips)
                                    for i in range(offset, min(end, len(scenes)))],
                         offset, len(scenes), extra={"selected": selected_index})


@command("scenes.get", doc="One scene with its clips")
def scenes_get(ctx, scene):
    """Everything about one scene.

    Args:
        scene: index, name or path.

    Returns:
        {path, index, name, color_index, is_empty, is_triggered, tempo_enabled,
        tempo?, time_signature_enabled, signature?, clips:[{track, track_name,
        name, length, playing?}]}
    """
    index, obj = _resolve(ctx, scene)
    return _scene_data(ctx, index, obj, "full")


# --------------------------------------------------------------------------
# structure
# --------------------------------------------------------------------------

@command("scenes.create", mutating=True, doc="Create a scene at an index (optionally named)")
def scenes_create(ctx, index=-1, name=None, color_index=None, tempo=None,
                  time_signature=None, select=False):
    """Insert a new, empty scene.

    Args:
        index: position (0-based; -1 = at the end).
        name: optional name.
        color_index: optional colour 0..69.
        tempo: optional scene tempo (enables the scene tempo).
        time_signature: optional "3/4" (enables the scene signature).
        select: also select the new scene.

    Returns:
        The new scene: {path, index, name, ...}

    Gotchas:
        Every track gets an empty clip slot at that index; later scene
        indices shift by one.  Edition limits raise ``unsupported``.  Live
        12.4.5 copies the tempo / time signature of the scene above into the
        new scene and selects it — LiveBridge clears both unless ``tempo`` /
        ``time_signature`` are passed and keeps the previous selection unless
        ``select`` is true.
    """
    song = ctx.song
    count = len(_scenes(ctx))
    index = _int(index, "index", -1, count)
    select = _bool(select, "select")
    if name is not None and not isinstance(name, str):
        raise BridgeError("bad_args", "name must be a string")
    if color_index is not None:
        _int(color_index, "color_index", 0, 69)
    if tempo is not None:
        _float(tempo, "tempo", 20.0, 999.0)
    if time_signature is not None:
        _parse_signature(time_signature)
    song_view = compat.safe_getattr(song, "view")
    previous = compat.safe_getattr(song_view, "selected_scene")
    scene = _live("create_scene", song.create_scene, index)
    if scene is None:
        scene = _scenes(ctx)[count if index == -1 else index]
    _apply(ctx, scene, name=name, color_index=color_index, tempo=tempo,
           time_signature=time_signature)
    if tempo is None and compat.safe_getattr(scene, "tempo_enabled", False):
        _assign(scene, "tempo_enabled", False, "tempo_enabled")
    if time_signature is None and compat.safe_getattr(scene, "time_signature_enabled", False):
        _set_time_signature_enabled(ctx, scene, False)
    if select:
        _assign(song_view, "selected_scene", scene, "selected_scene")
    elif previous is not None and compat.safe_getattr(song_view, "selected_scene") != previous:
        _assign(song_view, "selected_scene", previous, "selected_scene")
    return _scene_data(ctx, _index_of(ctx, scene), scene)


@command("scenes.delete", mutating=True, doc="Delete a scene")
def scenes_delete(ctx, scene):
    """Delete a scene and all clips in it.

    Args:
        scene: index, name or path.

    Returns:
        {"deleted": {index, name}, "scene_count": n}

    Gotchas:
        A set always keeps at least one scene (Live refuses -> invalid_state).
        Later scene indices shift down by one.
    """
    index, obj = _resolve(ctx, scene)
    name = compat.safe_getattr(obj, "name")
    _live("delete_scene", ctx.song.delete_scene, index)
    return {"deleted": {"index": index, "name": name}, "scene_count": len(_scenes(ctx))}


@command("scenes.duplicate", mutating=True, doc="Duplicate a scene (with its clips)")
def scenes_duplicate(ctx, scene, name=None):
    """Duplicate a scene; the copy is inserted right after it and selected.

    Args:
        scene: index, name or path.
        name: optional name for the copy (Live 12.4.5 keeps the original name,
            so two scenes then share it — name lookups pick the first).

    Returns:
        The new scene: {path, index, name, ...} — clips, colour, tempo and
        signature are copied; later scene indices shift by one.
    """
    index, _obj = _resolve(ctx, scene)
    before = len(_scenes(ctx))
    _live("duplicate_scene", ctx.song.duplicate_scene, index)
    scenes = _scenes(ctx)
    if len(scenes) != before + 1 or index + 1 >= len(scenes):
        raise BridgeError("internal", "duplicate_scene did not add a scene")
    copy = scenes[index + 1]
    if name is not None:
        _apply(ctx, copy, name=name)
    return _scene_data(ctx, index + 1, copy)


@command("scenes.set", mutating=True, doc="Rename/recolour a scene, set its tempo/signature")
def scenes_set(ctx, scene, name=None, color_index=None, color=None, tempo=None,
               tempo_enabled=None, time_signature=None, time_signature_enabled=None):
    """Change a scene's properties in one undo step.

    Args:
        scene: index, name or path.
        name: new name.
        color_index: 0..69 (Live's palette).
        color: "#RRGGBB", 0xRRGGBB, [r, g, b] (Live picks the nearest palette
            colour), a palette index 0..69 or a colour name ("red", "blue").
        tempo: scene tempo 20..999 — also enables it unless tempo_enabled=false.
        tempo_enabled: switch the scene tempo on/off (off: the song tempo is kept).
        time_signature: "3/4" — also enables the scene signature.
        time_signature_enabled: switch the scene signature on/off.

    Returns:
        The scene after the change: {path, index, name, color_index, tempo?, signature?, ...}

    Gotchas:
        A scene's tempo/signature applies when the scene is fired.  While
        disabled Live reports tempo / signature as -1 (omitted here).
    """
    index, obj = _resolve(ctx, scene)
    if all(v is None for v in (name, color_index, color, tempo, tempo_enabled,
                               time_signature, time_signature_enabled)):
        raise BridgeError("bad_args", "nothing to change — pass name, color_index, color, "
                          "tempo, tempo_enabled, time_signature or time_signature_enabled")
    _apply(ctx, obj, name=name, color_index=color_index, color=color, tempo=tempo,
           tempo_enabled=tempo_enabled, time_signature=time_signature,
           time_signature_enabled=time_signature_enabled)
    return _scene_data(ctx, index, obj)


@command("scenes.rename", mutating=True, doc="Rename a scene")
def scenes_rename(ctx, scene, name):
    """Rename a scene (shortcut for ``scenes.set`` with ``name``).

    Args:
        scene: index, name or path.
        name: the new name ("" clears it).

    Returns:
        {path, index, name, ...}
    """
    index, obj = _resolve(ctx, scene)
    _apply(ctx, obj, name=name)
    return _scene_data(ctx, index, obj)


# --------------------------------------------------------------------------
# launching
# --------------------------------------------------------------------------

@command("scenes.fire", doc="Launch a scene (or the selected one)")
def scenes_fire(ctx, scene=None, force_legato=False, select=True):
    """Launch every clip slot of a scene.

    Args:
        scene: index, name or path; omit to fire the *selected* scene and
            move the selection to the next one (Live's Enter key behaviour).
        force_legato: clips take over the play position of the clips they
            replace instead of starting from their start.
        select: select the fired scene (ignored when ``scene`` is omitted).

    Returns:
        {"fired": {index, name}, "selected": <index of the selected scene>,
        "clip_count": <clips the scene launches>, "is_playing": bool}

    Gotchas:
        A scene with clips starts the transport (Live does it on its next
        tick, so ``is_playing`` is still the state before the launch); an
        empty scene does not start it.  Slots without clips fire their stop
        buttons (stopping that track).  An enabled scene tempo / time
        signature is applied to the song immediately.  Launch is quantized
        by the global clip trigger quantization.
    """
    song = ctx.song
    force_legato = _bool(force_legato, "force_legato")
    if scene is None:
        selected = compat.safe_getattr(song.view, "selected_scene")
        if selected is None:
            raise BridgeError("invalid_state", "no scene is selected")
        index = _index_of(ctx, selected)
        _live("fire_as_selected", selected.fire_as_selected, force_legato)
        obj = selected
    else:
        index, obj = _resolve(ctx, scene)
        _live("fire", obj.fire, force_legato, _bool(select, "select"))
    now_selected = compat.safe_getattr(song.view, "selected_scene")
    selected_index = None
    for i, candidate in enumerate(_scenes(ctx)):
        if now_selected is not None and candidate == now_selected:
            selected_index = i
            break
    return {"fired": {"index": index, "name": compat.safe_getattr(obj, "name")},
            "selected": selected_index,
            "clip_count": sum(1 for slot in compat.safe_getattr(obj, "clip_slots", ()) or ()
                              if compat.safe_getattr(slot, "clip") is not None),
            "is_playing": bool(compat.safe_getattr(song, "is_playing", False))}


@command("scenes.stop_all", doc="Stop all clips (Session 'Stop All Clips' button)")
def scenes_stop_all(ctx, quantized=True):
    """Stop every playing session clip; the transport keeps running.

    Args:
        quantized: true = at the next launch-quantization boundary,
            false = immediately.

    Returns:
        {"stopped": true, "quantized": bool}
    """
    quantized = _bool(quantized, "quantized")
    _live("stop_all_clips", ctx.song.stop_all_clips, quantized)
    return {"stopped": True, "quantized": quantized}


@command("scenes.select", doc="Select a scene")
def scenes_select(ctx, scene):
    """Select (highlight) a scene without firing it.

    Args:
        scene: index, name or path.

    Returns:
        {"selected": {index, name, path}}
    """
    index, obj = _resolve(ctx, scene)
    _assign(ctx.song.view, "selected_scene", obj, "selected_scene")
    return {"selected": {"index": index, "name": compat.safe_getattr(obj, "name"),
                         "path": ctx.path_of(obj)}}


@command("scenes.capture", mutating=True, doc="Capture playing clips into a new scene")
def scenes_capture(ctx, mode="all"):
    """Live's "Capture and Insert Scene": copy the currently playing clips into a
    new scene inserted after the selected scene.

    Args:
        mode: "all" or "all_except_selected" (leave out the selected track).

    Returns:
        The new scene: {path, index, name, clips:[...], ...}

    Gotchas:
        Live 12.4.5 inserts the scene even when nothing plays (it is then
        empty) and gives it the selected scene's name, tempo and signature.
        Later scene indices shift by one.  Edition scene limits raise
        ``unsupported``.
    """
    song = ctx.song
    modes = {"all": 0, "all_except_selected": 1}
    if mode not in modes:
        raise BridgeError("bad_args", "mode must be 'all' or 'all_except_selected'")
    value = modes[mode]
    enum_class = compat.live_enum("Song.CaptureMode")
    names = compat.safe_getattr(enum_class, "names")
    if isinstance(names, dict) and mode in names:
        value = int(names[mode])
    before = _scenes(ctx)
    if not compat.has(song, "capture_and_insert_scene"):
        raise BridgeError("unsupported", "capture_and_insert_scene is not available")
    _live("capture_and_insert_scene", song.capture_and_insert_scene, value)
    after = _scenes(ctx)
    for index, scene in enumerate(after):
        if not any(scene == old for old in before):
            return _scene_data(ctx, index, scene, "summary", include_clips=True)
    raise BridgeError("invalid_state", "Live did not insert a scene (nothing playing?)")
