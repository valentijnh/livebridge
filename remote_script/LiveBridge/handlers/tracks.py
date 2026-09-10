"""Track commands: list/get/find, create/delete/duplicate, name/colour/mute/solo/arm/fold,
group members, stopping clips and selection.

Also home of the loose-argument helpers the other module-B handlers
(``mixer``, ``routing``, ``record``) import: :func:`resolve_track`,
:func:`resolve_tracks`, :func:`parse_toggle`, :func:`track_kind`.

What the Live Object Model can NOT do (so no command pretends to):
reorder/move tracks, group or ungroup tracks, freeze or flatten tracks.
``is_frozen`` / ``can_be_frozen`` are readable (``tracks.get``).
"""


from .. import compat
from .. import resolve
from .. import serialize
from ..registry import BridgeError, command

#: Values accepted by the ``type`` filter / ``tracks.create``.
TRACK_TYPES = ("midi", "audio", "return", "master", "group")

_TOGGLE_TRUE = ("1", "true", "yes", "on", "enable", "enabled")
_TOGGLE_FALSE = ("0", "false", "no", "off", "disable", "disabled")

#: Named colours -> palette index (shared with clips and scenes).
COLOR_NAMES = resolve.COLOR_NAMES


# --------------------------------------------------------------------------
# shared helpers (imported by mixer.py, routing.py, record.py)
# --------------------------------------------------------------------------

def track_kind(ctx, track):
    """``"midi"``, ``"audio"``, ``"return"``, ``"master"`` or ``"group"``."""
    return serialize.track_type(track, ctx)


def _all_tracks(ctx, include_returns=True, include_master=True):
    return resolve.all_tracks(ctx.song, include_returns, include_master)


#: Index of a LOM object in a collection — shared implementation.
index_in = resolve.index_in


def resolve_track(ctx, spec, allow_returns=True, allow_master=True):
    """Resolve a loose ``track`` argument to a Track.

    Thin wrapper around :func:`LiveBridge.resolve.track` (the resolver every
    handler shares): an int index into ``song.tracks``, a numeric string, a
    name (exact, case-insensitive, unique prefix, then a unique contains /
    punctuation-insensitive match), a return letter (``"A"``,
    ``"return B"``), ``"master"``/``"main"``, ``"selected"``, a LOM path or a
    Track object.
    """
    return resolve.track(ctx, spec, allow_returns, allow_master)


def resolve_tracks(ctx, spec, allow_returns=True, allow_master=True):
    """Like :func:`resolve_track` but ``spec`` may also be a list or ``"all"``.

    ``"all"`` means every regular track (plus returns when allowed, never the
    master).  Duplicates are removed, order is kept.
    """
    return resolve.tracks(ctx, spec, allow_returns, allow_master)


def parse_toggle(value, current, what):
    """``True/False``, ``1/0``, ``"on"/"off"`` or ``"toggle"`` -> bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("toggle", "flip", "invert"):
            return not bool(current)
        if text in _TOGGLE_TRUE:
            return True
        if text in _TOGGLE_FALSE:
            return False
    raise BridgeError("bad_args", "%s must be true, false or \"toggle\", got %r" % (what, value))


#: Colour helpers — shared with clips and scenes (see LiveBridge/resolve.py).
parse_color = resolve.parse_color
apply_color = resolve.apply_color
color_info = resolve.color_info


def _track_ref(ctx, track):
    return ctx.summarize(track, "minimal")


_detail = resolve.check_detail
_normalise = resolve.normalise


def _limitation(error):
    return type(error).__name__ == "LimitationError"


def _state(ctx, track):
    """Compact mute/solo/arm/fold state of a track."""
    get = compat.safe_getattr
    data = _track_ref(ctx, track)
    kind = data.get("type")
    if kind != "master":
        data["mute"] = bool(get(track, "mute", False))
        data["solo"] = bool(get(track, "solo", False))
    if get(track, "can_be_armed", False):
        data["arm"] = bool(get(track, "arm", False))
    if get(track, "is_foldable", False):
        data["folded"] = bool(get(track, "fold_state", 0))
    data.update(color_info(track))
    return data


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------

@command("tracks.list", doc="List tracks (regular/return/master) with filters and paging")
def tracks_list(ctx, include_returns=True, include_master=True, type=None, name=None,
                frozen=None, armed=None, detail="summary", offset=0, limit=None):
    """List tracks.

    Args:
        include_returns: include return tracks (after the regular tracks).
        include_master: include the master ("Main") track (last).
        type: only tracks of this type — "midi", "audio", "return", "master",
            "group" — or a list of them.
        name: only tracks whose name contains this text (case-insensitive).
        frozen: True = only frozen tracks, False = only unfrozen ones.
        armed: True = only armed tracks, False = only unarmed ones.
        detail: "minimal" | "summary" | "full" (see serialize §6).
        offset / limit: paging over the filtered list.

    Returns:
        {"total": n_matching, "offset", "count" (returned), "tracks": [track
        summaries], "next_offset"?}.
        Every summary carries ``path`` (``song.tracks[i]``,
        ``song.return_tracks[i]`` or ``song.master_track``) and ``type``.

    Gotchas:
        Track order cannot be changed through the LOM. ``index`` of a return
        track is its index in ``song.return_tracks``.
    """
    detail = _detail(detail)
    kinds = None
    if type is not None:
        kinds = [type] if isinstance(type, str) else list(type)
        kinds = [str(k).strip().lower() for k in kinds]
        bad = [k for k in kinds if k not in TRACK_TYPES]
        if bad:
            raise BridgeError("bad_args", "unknown track type %s (use %s)"
                              % (", ".join(repr(b) for b in bad), ", ".join(TRACK_TYPES)))
    offset, limit = resolve.check_paging(offset, limit, maximum=10000, allow_none=True)
    matches = []
    for track in _all_tracks(ctx, bool(include_returns), bool(include_master)):
        kind = track_kind(ctx, track)
        if kinds is not None and kind not in kinds:
            continue
        if name is not None and str(name).lower() not in \
                str(compat.safe_getattr(track, "name", "")).lower():
            continue
        if frozen is not None and bool(compat.safe_getattr(track, "is_frozen", False)) \
                != bool(frozen):
            continue
        if armed is not None:
            is_armed = bool(compat.safe_getattr(track, "arm", False)) \
                if compat.safe_getattr(track, "can_be_armed", False) else False
            if is_armed != bool(armed):
                continue
        matches.append(track)
    page = matches[offset:] if limit is None else matches[offset:offset + limit]
    return resolve.paged("tracks", [ctx.summarize(t, detail) for t in page], offset,
                         len(matches))


@command("tracks.get", doc="One track in detail (+ freeze and group info)")
def tracks_get(ctx, track, detail="full"):
    """Everything about one track.

    Args:
        track: index, name, return letter ("A"), "master", "selected" or path.
        detail: "minimal" | "summary" | "full" (default: clips, devices).

    Returns:
        The track summary plus ``color`` (hex), ``is_visible``,
        ``muted_via_solo``, ``freeze: {is_frozen, can_be_frozen}`` and, for
        group tracks, ``members`` (minimal summaries).

    Gotchas:
        Freezing / flattening is not available through the LOM — only the
        state can be read.
    """
    detail = _detail(detail)
    obj = resolve_track(ctx, track)
    data = ctx.summarize(obj, detail)
    get = compat.safe_getattr
    data.update(color_info(obj))
    data["is_visible"] = bool(get(obj, "is_visible", True))
    if compat.has(obj, "muted_via_solo"):
        data["muted_via_solo"] = bool(get(obj, "muted_via_solo", False))
    data["freeze"] = {"is_frozen": bool(get(obj, "is_frozen", False)),
                      "can_be_frozen": bool(get(obj, "can_be_frozen", False))}
    if get(obj, "is_foldable", False):
        data["members"] = [dict(_track_ref(ctx, t), depth=d) for t, d in _members(ctx, obj)]
    return data


@command("tracks.find", doc="Find tracks by (part of) their name")
def tracks_find(ctx, name, limit=20):
    """Find tracks, returns and the master by name.

    Args:
        name: text to look for (case-insensitive; exact matches rank first).
        limit: maximum number of matches (default 20).

    Returns:
        {"query", "count", "matches": [{path, index, name, type}]} — empty
        ``matches`` when nothing matches (not an error).
    """
    if not isinstance(name, str) or not name.strip():
        raise BridgeError("bad_args", "name must be a non-empty string")
    query = name.strip().lower()
    wanted = _normalise(query)
    ranked = []
    for track in _all_tracks(ctx):
        label = str(compat.safe_getattr(track, "name", ""))
        lowered = label.lower()
        if lowered == query:
            rank = 0
        elif lowered.startswith(query):
            rank = 1
        elif query in lowered:
            rank = 2
        elif wanted and wanted in _normalise(label):
            rank = 3
        else:
            continue
        ranked.append((rank, len(ranked), track))
    ranked.sort(key=lambda item: (item[0], item[1]))
    try:
        limit = max(1, int(limit))
    except (TypeError, ValueError):
        raise BridgeError("bad_args", "limit must be an integer")
    matches = [_track_ref(ctx, t) for _r, _i, t in ranked[:limit]]
    return {"query": name, "count": len(matches), "matches": matches}


# --------------------------------------------------------------------------
# create / delete / duplicate
# --------------------------------------------------------------------------

@command("tracks.create", mutating=True, doc="Create a MIDI, audio or return track")
def tracks_create(ctx, type="midi", name=None, index=-1, color=None):
    """Create a track.

    Args:
        type: "midi" (default), "audio" or "return".
        name: optional name.
        index: position in ``song.tracks``; -1 (default) = at the end,
            ``null`` = right after the selected track (Live's default).
            Ignored for return tracks (always appended).
        color: palette index 0-69, "#RRGGBB", [r,g,b] or a colour name.

    Returns:
        The new track's summary (``path`` tells you where it landed).

    Gotchas:
        Group tracks cannot be created through the LOM. Intro/Lite track
        limits raise ``unsupported``. The LOM cannot move a track afterwards,
        so pick ``index`` now. Live 12.4.5 prefixes return-track names with
        their letter: name="Verb" on the third return reads back "C-Verb"
        (the returned summary shows the real name; the plain name still
        resolves as a track argument).
    """
    kind = str(type or "midi").strip().lower()
    if kind not in ("midi", "audio", "return"):
        raise BridgeError("bad_args", "type must be 'midi', 'audio' or 'return' "
                          "(group tracks cannot be created through the LOM)")
    song = ctx.song
    count = len(compat.safe_getattr(song, "tracks", ()) or ())
    if index is not None and kind != "return":
        if isinstance(index, bool) or not isinstance(index, int):
            raise BridgeError("bad_args", "index must be an integer (-1 = end)")
        if index < -1 or index > count:
            raise BridgeError("not_found", "index %d is out of range (0..%d, -1 = end)"
                              % (index, count))
    try:
        if kind == "midi":
            track = song.create_midi_track(index)
        elif kind == "audio":
            track = song.create_audio_track(index)
        else:
            track = song.create_return_track()
    except Exception as error:
        if _limitation(error):
            raise BridgeError("unsupported", "Live refused to create another %s track "
                              "(edition limit): %s" % (kind, error))
        raise BridgeError("invalid_state", "could not create a %s track: %s" % (kind, error))
    if track is None:  # pragma: no cover - every 12.x returns the track
        tracks = list(song.return_tracks if kind == "return" else song.tracks)
        track = tracks[-1] if index in (-1, None) or kind == "return" else tracks[index]
    if name is not None:
        track.name = str(name)
    if color is not None:
        apply_color(track, color)
    return ctx.summarize(track, "summary")


@command("tracks.delete", mutating=True, doc="Delete a track or return track")
def tracks_delete(ctx, track):
    """Delete one track (regular or return).

    Args:
        track: index, name, return letter, or path. The master cannot be deleted.

    Returns:
        {"deleted": name, "type", "path" (before deletion), "track_count", "return_count"}.

    Gotchas:
        Indices of the following tracks shift down by one. A set must keep at
        least one regular track. Undo with Live's undo (one step).
    """
    obj = resolve_track(ctx, track, allow_master=False)
    song = ctx.song
    kind = track_kind(ctx, obj)
    name = compat.safe_getattr(obj, "name", "")
    path = ctx.path_of(obj)
    try:
        if kind == "return":
            song.delete_return_track(index_in(song.return_tracks, obj))
        else:
            if len(song.tracks) <= 1:
                raise BridgeError("invalid_state", "a Live set needs at least one track")
            song.delete_track(index_in(song.tracks, obj))
    except BridgeError:
        raise
    except Exception as error:
        raise BridgeError("invalid_state", "could not delete %r: %s" % (name, error))
    return {"deleted": name, "type": kind, "path": path,
            "track_count": len(song.tracks), "return_count": len(song.return_tracks)}


@command("tracks.duplicate", mutating=True, doc="Duplicate a track (inserted right after it)")
def tracks_duplicate(ctx, track, name=None):
    """Duplicate a regular track with its devices and clips.

    Args:
        track: index, name or path of a regular (MIDI/audio/group) track.
        name: optional name for the copy.

    Returns:
        The summary of the copy (right after the original — for a group, after
        the original's members — and selected by Live).

    Gotchas:
        Return tracks and the master cannot be duplicated. Duplicating a group
        duplicates its members too.
    """
    obj = resolve_track(ctx, track)
    kind = track_kind(ctx, obj)
    if kind in ("return", "master"):
        raise BridgeError("bad_args", "%r is the %s track — Live's API can only duplicate "
                          "regular (MIDI/audio/group) tracks"
                          % (compat.safe_getattr(obj, "name", ""), kind))
    song = ctx.song
    index = index_in(song.tracks, obj)
    member_count = len(_members(ctx, obj)) if compat.safe_getattr(obj, "is_foldable", False) \
        else 0
    try:
        song.duplicate_track(index)
    except Exception as error:
        if _limitation(error):
            raise BridgeError("unsupported", "edition track limit reached: %s" % error)
        raise BridgeError("invalid_state", "could not duplicate %r: %s"
                          % (compat.safe_getattr(obj, "name", ""), error))
    # Live selects the copy; a group's copy lands after the original's members.
    copy = compat.safe_getattr(ctx.view, "selected_track")
    tracks = list(song.tracks)
    if copy is None or copy == obj or index_in(tracks, copy) is None:
        copy = tracks[min(index + 1 + member_count, len(tracks) - 1)]
    if name is not None:
        copy.name = str(name)
    return ctx.summarize(copy, "summary")


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------

def _set_bool(obj, prop, value, label):
    try:
        setattr(obj, prop, value)
    except (RuntimeError, AttributeError, ValueError, TypeError) as error:
        raise BridgeError("invalid_state", "%s: cannot set %s (%s)" % (label, prop, error))


def _others(ctx, track, armable_only=False):
    for other in _all_tracks(ctx, include_returns=not armable_only, include_master=False):
        if other == track:
            continue
        if armable_only and not compat.safe_getattr(other, "can_be_armed", False):
            continue
        yield other


def apply_track_state(ctx, obj, name=None, color=None, mute=None, solo=None, arm=None,
                      fold=None, exclusive=False):
    """Apply the ``tracks.set`` changes to one track; returns the changed keys."""
    get = compat.safe_getattr
    label = str(get(obj, "name", "track"))
    kind = track_kind(ctx, obj)
    changed = []
    if name is not None:
        obj.name = str(name)
        changed.append("name")
    if color is not None:
        apply_color(obj, color)
        changed.append("color")
    if mute is not None:
        if kind == "master":
            raise BridgeError("invalid_state", "the master track has no mute")
        _set_bool(obj, "mute", parse_toggle(mute, get(obj, "mute", False), "mute"), label)
        changed.append("mute")
    if solo is not None:
        if kind == "master":
            raise BridgeError("invalid_state", "the master track has no solo")
        value = parse_toggle(solo, get(obj, "solo", False), "solo")
        if value and exclusive:
            for other in _others(ctx, obj):
                if get(other, "solo", False):
                    _set_bool(other, "solo", False, str(get(other, "name", "")))
        _set_bool(obj, "solo", value, label)
        changed.append("solo")
    if arm is not None:
        if not get(obj, "can_be_armed", False):
            raise BridgeError("invalid_state", "%r cannot be armed (%s track)" % (label, kind))
        value = parse_toggle(arm, get(obj, "arm", False), "arm")
        if value and exclusive:
            for other in _others(ctx, obj, armable_only=True):
                if get(other, "arm", False):
                    _set_bool(other, "arm", False, str(get(other, "name", "")))
        _set_bool(obj, "arm", value, label)
        changed.append("arm")
    if fold is not None:
        if not get(obj, "is_foldable", False):
            raise BridgeError("invalid_state", "%r is not a group track (only groups fold)"
                              % label)
        value = parse_toggle(fold, get(obj, "fold_state", 0), "fold")
        try:
            obj.fold_state = 1 if value else 0
        except (RuntimeError, ValueError, TypeError) as error:
            raise BridgeError("invalid_state", "%s: cannot fold (%s)" % (label, error))
        changed.append("fold")
    return changed


@command("tracks.set", mutating=True,
         doc="Rename/colour/mute/solo/arm/fold one or more tracks in one step")
def tracks_set(ctx, track, name=None, color=None, mute=None, solo=None, arm=None,
               fold=None, exclusive=False):
    """Change track properties — one track or several at once (one undo step).

    Args:
        track: index, name, return letter, "master", "selected", path — or a
            LIST of those, or "all" (every regular + return track).
        name: new name (only when exactly one track is given).
        color: palette index 0-69, "#RRGGBB", [r,g,b] or a colour name
            ("red", "blue", ...); ints above 69 are read as 0xRRGGBB.
        mute / solo / arm / fold: true, false or "toggle".
        exclusive: with solo=true un-solo every other track; with arm=true
            disarm every other track.

    Returns:
        {"tracks": [{path, name, type, mute, solo, arm?, folded?, color_index,
        color}], "errors": [{track, error}] (only when some tracks failed)}.

    Gotchas:
        Return/master/group tracks cannot be armed; the master has no
        mute/solo; only group tracks fold. With several tracks, a failure on
        one does not stop the others (it is listed in ``errors``); with one
        track the failure is raised.
    """
    if all(v is None for v in (name, color, mute, solo, arm, fold)):
        raise BridgeError("bad_args", "nothing to change: pass name, color, mute, solo, "
                          "arm or fold")
    targets = resolve_tracks(ctx, track)
    if name is not None and len(targets) != 1:
        raise BridgeError("bad_args", "name can only be set on a single track")
    results, errors = [], []
    for obj in targets:
        try:
            apply_track_state(ctx, obj, name, color, mute, solo, arm, fold, bool(exclusive))
        except BridgeError as error:
            if len(targets) == 1:
                raise
            errors.append({"track": compat.safe_getattr(obj, "name", ""),
                           "error": error.message})
            continue
        results.append(_state(ctx, obj))
    data = {"tracks": results}
    if errors:
        data["errors"] = errors
    return data


# --------------------------------------------------------------------------
# groups, clips, selection
# --------------------------------------------------------------------------

def _group_depth(track, group):
    depth = 0
    parent = compat.safe_getattr(track, "group_track")
    while parent is not None and depth < 32:
        depth += 1
        if parent == group:
            return depth
        parent = compat.safe_getattr(parent, "group_track")
    return None


def _members(ctx, group):
    """(track, depth) for every track inside ``group`` (nested groups too)."""
    members = []
    for track in compat.safe_getattr(ctx.song, "tracks", ()) or ():
        if track == group:
            continue
        depth = _group_depth(track, group)
        if depth is not None:
            members.append((track, depth))
    return members


@command("tracks.group", mutating=True, doc="Group info: members and fold state; fold/unfold")
def tracks_group(ctx, track, fold=None):
    """Inspect a group track (and fold/unfold it).

    Args:
        track: a group track, or any track inside a group (then its group is
            used).
        fold: optional true (fold), false (unfold) or "toggle".

    Returns:
        {"group": {path, name, ...}, "folded": bool, "grouped_in": parent group
        path or null, "members": [{path, name, type, depth}]} — ``depth`` 1 =
        direct member, 2 = inside a nested group, ...

    Gotchas:
        The LOM cannot create, ungroup or change group membership — only read
        it and fold/unfold.
    """
    obj = resolve_track(ctx, track, allow_master=False)
    get = compat.safe_getattr
    if not get(obj, "is_foldable", False):
        parent = get(obj, "group_track")
        if parent is None:
            raise BridgeError("invalid_state", "%r is not a group track and is not inside one "
                              "(groups cannot be created through the LOM)"
                              % get(obj, "name", ""))
        obj = parent
    if fold is not None:
        value = parse_toggle(fold, get(obj, "fold_state", 0), "fold")
        try:
            obj.fold_state = 1 if value else 0
        except (RuntimeError, ValueError, TypeError) as error:
            raise BridgeError("invalid_state", "cannot fold %r: %s" % (get(obj, "name", ""),
                                                                      error))
    parent = get(obj, "group_track")
    return {
        "group": _track_ref(ctx, obj),
        "folded": bool(get(obj, "fold_state", 0)),
        "grouped_in": ctx.path_of(parent) if parent is not None else None,
        "members": [dict(_track_ref(ctx, t), depth=d) for t, d in _members(ctx, obj)],
    }


@command("tracks.stop_clips", mutating=True, doc="Stop the clips of one/several/all tracks")
def tracks_stop_clips(ctx, track=None, quantized=True):
    """Stop playing (and triggered) session clips.

    Args:
        track: one track, a list, or omitted/"all" = every track
            (``song.stop_all_clips``).
        quantized: true (default) = stop at the next launch-quantization
            boundary; false = immediately.

    Returns:
        {"stopped": [track names] or "all", "quantized": bool}.

    Gotchas:
        Transport keeps running; arrangement playback is not affected.
    """
    quantized = bool(quantized)
    if track is None or (isinstance(track, str) and track.strip().lower() == "all"):
        try:
            ctx.song.stop_all_clips(quantized)
        except TypeError:
            ctx.song.stop_all_clips()
        return {"stopped": "all", "quantized": quantized}
    stopped = []
    for obj in resolve_tracks(ctx, track, allow_master=False):
        try:
            obj.stop_all_clips(quantized)
        except TypeError:
            obj.stop_all_clips()
        stopped.append(compat.safe_getattr(obj, "name", ""))
    return {"stopped": stopped, "quantized": quantized}


@command("tracks.select", doc="Select a track in Live's UI")
def tracks_select(ctx, track):
    """Make ``track`` the selected track (``song.view.selected_track``).

    Args:
        track: index, name, return letter, "master" or path.

    Returns:
        {"selected": {path, index, name, type}}.

    Gotchas:
        Selection decides where browser loads and ``trigger_session_record``
        go. It does not scroll a folded group open.
    """
    obj = resolve_track(ctx, track)
    try:
        ctx.view.selected_track = obj
    except Exception as error:
        raise BridgeError("invalid_state", "could not select %r: %s"
                          % (compat.safe_getattr(obj, "name", ""), error))
    return {"selected": _track_ref(ctx, obj)}
