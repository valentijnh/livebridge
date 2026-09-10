"""Routing commands: input/output routing types and channels, monitoring, a whole-set
routing summary and a helper that routes one track into another (resampling, bus
and side-chain setups).

Live 12 routing API (``docs/LIVE_API_VERIFIED.md`` §5): assign an element of
``track.available_{input,output}_routing_{types,channels}`` — matched here by
``display_name``. Channels change after the type changes, so types are set
first. Monitoring (and arm) exist on MIDI/audio tracks only. Which routings a
return or the master track has differs between sources: Live 12.4.5 (checked
on the running Live) exposes input AND output routing on return tracks and on
the master, while older docs (and the test stub) have none on the master — so
every command here feature-detects and reports what Live offers. Real Live
12.4.5 names the master bus "Main" (older sets/scripts say "Master" — both
are accepted).
"""

from .. import compat
from ..registry import BridgeError, command
from .tracks import resolve_track, track_kind

#: RoutingTypeCategory (verified on Live 12.4.5).
CATEGORIES = {0: "external", 1: "rewire", 2: "resampling", 3: "main", 4: "track",
              5: "parent_group_track", 6: "none", 7: "other"}
LAYOUTS = {0: "midi", 1: "mono", 2: "stereo"}
MONITORING = {0: "in", 1: "auto", 2: "off"}
_MONITORING_NAMES = {"in": 0, "input": 0, "auto": 1, "automatic": 1, "off": 2, "none": 2}
_ALIASES = {"master": "main", "main": "master"}

#: Device parameters that switch a sidechain on (Compressor "S/C On"; UNVERIFIED names).
_SIDECHAIN_SWITCHES = ("s/c on", "sidechain on", "side chain on", "sidechain")


def parse_monitoring(value):
    """``"in"``/``"auto"``/``"off"`` or 0/1/2 -> 0/1/2."""
    if isinstance(value, int) and not isinstance(value, bool) and value in (0, 1, 2):
        return value
    if isinstance(value, str) and value.strip().lower() in _MONITORING_NAMES:
        return _MONITORING_NAMES[value.strip().lower()]
    raise BridgeError("bad_args", "monitoring must be 'in', 'auto' or 'off'")


def set_monitoring(track, value):
    """Set ``current_monitoring_state``; returns the new state name."""
    state = parse_monitoring(value)
    if not compat.has(track, "current_monitoring_state"):
        raise BridgeError("invalid_state", "%r has no monitoring (return/master track)"
                          % compat.safe_getattr(track, "name", ""))
    try:
        track.current_monitoring_state = state
    except (RuntimeError, ValueError, TypeError, AttributeError) as error:
        raise BridgeError("invalid_state", "monitoring refused on %r: %s"
                          % (compat.safe_getattr(track, "name", ""), error))
    return MONITORING[state]


def monitoring_of(track):
    """``"in"``/``"auto"``/``"off"`` or ``None`` (returns/master)."""
    state = compat.safe_getattr(track, "current_monitoring_state")
    if state is None:
        return None
    try:
        return MONITORING.get(int(state), int(state))
    except (TypeError, ValueError):
        return None


def _name(option):
    return str(compat.safe_getattr(option, "display_name", "") or "")


def _type_entry(ctx, option, index):
    entry = {"index": index, "name": _name(option)}
    category = compat.safe_getattr(option, "category")
    if category is not None:
        try:
            entry["category"] = CATEGORIES.get(int(category), int(category))
        except (TypeError, ValueError):
            pass
    attached = compat.safe_getattr(option, "attached_object")
    if attached is not None and compat.has(attached, "mixer_device"):
        entry["track"] = ctx.path_of(attached)
    return entry


def _channel_entry(option, index):
    entry = {"index": index, "name": _name(option)}
    layout = compat.safe_getattr(option, "layout")
    if layout is not None:
        try:
            entry["layout"] = LAYOUTS.get(int(layout), int(layout))
        except (TypeError, ValueError):
            pass
    return entry


def _options(track, prop):
    return list(compat.safe_getattr(track, prop, ()) or ())


def match_option(options, wanted, what):
    """Pick one routing option by index or name.

    Order: exact (case-insensitive) name, "master"/"main" alias, unique
    prefix, unique contains. Raises ``bad_args`` when ambiguous and
    ``not_found`` (listing the choices) when nothing matches.
    """
    names = [_name(o) for o in options]
    if isinstance(wanted, int) and not isinstance(wanted, bool):
        if -len(options) <= wanted < len(options):
            return options[wanted]
        raise BridgeError("not_found", "%s index %d out of range (%d choices: %s)"
                          % (what, wanted, len(options), ", ".join(repr(n) for n in names)))
    if not isinstance(wanted, str):
        raise BridgeError("bad_args", "%s must be a name or an index" % what)
    text = wanted.strip().lower()
    candidates = [text]
    if text in _ALIASES:
        candidates.append(_ALIASES[text])
    for candidate in candidates:
        exact = [o for o, n in zip(options, names) if n.lower() == candidate]
        if exact:
            return exact[0]
    for test in (lambda n: n.startswith(text), lambda n: text in n):
        hits = [o for o, n in zip(options, names) if test(n.lower())]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise BridgeError("bad_args", "%s %r is ambiguous: %s"
                              % (what, wanted, ", ".join(repr(_name(h)) for h in hits)))
    raise BridgeError("not_found", "no %s matching %r (choices: %s)"
                      % (what, wanted, ", ".join(repr(n) for n in names) or "none"))


def _assign(obj, prop, option, label):
    try:
        setattr(obj, prop, option)
    except (RuntimeError, ValueError, TypeError, AttributeError) as error:
        raise BridgeError("invalid_state", "%s: Live refused %s = %r (%s)"
                          % (label, prop, _name(option), error))


def _current(obj, which):
    kind = compat.safe_getattr(obj, "%s_routing_type" % which)
    if kind is None:
        return None
    channel = compat.safe_getattr(obj, "%s_routing_channel" % which)
    data = {"type": _name(kind), "channel": _name(channel) if channel is not None else None}
    category = compat.safe_getattr(kind, "category")
    if category is not None:
        try:
            data["category"] = CATEGORIES.get(int(category), int(category))
        except (TypeError, ValueError):
            pass
    return data


def routing_state(ctx, track, include_available=True):
    """The routing of one track (see ``routing.get``)."""
    data = {"track": ctx.summarize(track, "minimal"),
            "input": _current(track, "input"),
            "output": _current(track, "output"),
            "monitoring": monitoring_of(track)}
    if compat.safe_getattr(track, "can_be_armed", False):
        data["arm"] = bool(compat.safe_getattr(track, "arm", False))
    if include_available:
        available = {}
        for key, prop, maker in (
                ("input_types", "available_input_routing_types", _type_entry),
                ("input_channels", "available_input_routing_channels", None),
                ("output_types", "available_output_routing_types", _type_entry),
                ("output_channels", "available_output_routing_channels", None)):
            options = _options(track, prop)
            if not options and not compat.has(track, prop):
                continue
            available[key] = [maker(ctx, o, i) if maker else _channel_entry(o, i)
                              for i, o in enumerate(options)]
        data["available"] = available
    return data


def apply_routing(ctx, track, input_type=None, input_channel=None, output_type=None,
                  output_channel=None, monitoring=None):
    """Set routing on one track (types before channels); returns changed keys."""
    label = str(compat.safe_getattr(track, "name", "track"))
    changed = []
    steps = (("input", input_type, input_channel), ("output", output_type, output_channel))
    for which, kind, channel in steps:
        if kind is None and channel is None:
            continue
        types_prop = "available_%s_routing_types" % which
        if not compat.has(track, types_prop):
            raise BridgeError("invalid_state", "%r has no %s routing (%s track)"
                              % (label, which, track_kind(ctx, track)))
        if kind is not None:
            option = match_option(_options(track, types_prop), kind, "%s routing type" % which)
            _assign(track, "%s_routing_type" % which, option, label)
            changed.append("%s_type" % which)
        if channel is not None:
            option = match_option(_options(track, "available_%s_routing_channels" % which),
                                  channel, "%s routing channel" % which)
            _assign(track, "%s_routing_channel" % which, option, label)
            changed.append("%s_channel" % which)
    if monitoring is not None:
        set_monitoring(track, monitoring)
        changed.append("monitoring")
    return changed


@command("routing.get", doc="Current + available input/output routing of a track")
def routing_get(ctx, track, include_available=True):
    """Routing of one track, with every choice Live offers right now.

    Args:
        track: index, name, return letter, "master", "selected" or path.
        include_available: also list the available types/channels (default).

    Returns:
        {track, input: {type, channel, category} | null, output: {...} | null,
        monitoring: "in"|"auto"|"off"|null, arm?, available: {input_types:
        [{index, name, category, track?}], input_channels: [{index, name,
        layout}], output_types, output_channels}}.

    Gotchas:
        The channel lists belong to the *current* type — after changing the
        type, read again. A type whose ``category`` is "track" carries the
        ``track`` path it refers to. Monitoring/arm exist on MIDI/audio
        tracks only; ``input``/``output`` are null where Live offers no
        routing for that track.
    """
    return routing_state(ctx, resolve_track(ctx, track), bool(include_available))


@command("routing.set", mutating=True, doc="Set input/output routing type/channel and monitoring")
def routing_set(ctx, track, input_type=None, input_channel=None, output_type=None,
                output_channel=None, monitoring=None):
    """Change a track's routing by name.

    Args:
        track: index, name, return letter, "master", "selected" or path.
        input_type: e.g. "Ext. In", "All Ins", "Computer Keyboard",
            "Resampling", "No Input", or another track's name.
        input_channel: e.g. "1/2", "1", "All Channels", "Ch. 3", "Post FX".
        output_type: e.g. "Main" (alias "Master"), "Sends Only", "Ext. Out",
            or another track's name.
        output_channel: e.g. "Track In", "1/2".
        monitoring: "in", "auto" or "off" (MIDI/audio tracks only).
        Names match case-insensitively: exact, then unique prefix, then
        unique contains; an int picks by index from ``routing.get``.

    Returns:
        The routing after the change (``routing.get`` shape with the new
        available channel lists) plus ``changed``.

    Gotchas:
        Types are applied before channels because the channel list depends
        on the type. Hardware inputs ("Ext. In" channels) exist only when an
        audio interface is configured in Live's Preferences → Audio.
    """
    values = (input_type, input_channel, output_type, output_channel, monitoring)
    if all(v is None for v in values):
        raise BridgeError("bad_args", "nothing to change: pass input_type, input_channel, "
                          "output_type, output_channel or monitoring")
    obj = resolve_track(ctx, track)
    changed = apply_routing(ctx, obj, *values)
    data = routing_state(ctx, obj, True)
    data["changed"] = changed
    return data


@command("routing.summary", doc="Compact routing overview of every track")
def routing_summary(ctx, include_returns=True, include_master=True):
    """One line of routing per track.

    Args:
        include_returns / include_master: include those tracks (default yes).

    Returns:
        {"tracks": [{name, path, type, in: "All Ins | All Channels", out:
        "Main", monitoring, arm?}]} — ``in``/``out`` are null where a track has
        none.
    """
    song = ctx.song
    tracks = list(song.tracks)
    if include_returns:
        tracks += list(song.return_tracks)
    master = compat.safe_getattr(song, "master_track")
    if include_master and master is not None:
        tracks.append(master)
    rows = []
    for track in tracks:
        row = {"name": compat.safe_getattr(track, "name"), "path": ctx.path_of(track),
               "type": track_kind(ctx, track)}
        for which, key in (("input", "in"), ("output", "out")):
            current = _current(track, which)
            row[key] = None if current is None else " | ".join(
                part for part in (current["type"], current["channel"]) if part)
        row["monitoring"] = monitoring_of(track)
        if compat.safe_getattr(track, "can_be_armed", False):
            row["arm"] = bool(compat.safe_getattr(track, "arm", False))
        rows.append(row)
    return {"tracks": rows}


def _option_for_track(ctx, options, source):
    """The routing option that refers to ``source`` (attached object, then name)."""
    kind = track_kind(ctx, source)
    for option in options:
        attached = compat.safe_getattr(option, "attached_object")
        if attached is not None and (attached is source or attached == source):
            return option
    name = str(compat.safe_getattr(source, "name", "")).lower()
    for option in options:
        if _name(option).lower() == name:
            return option
    if kind == "master":
        for wanted in ("main", "master", "resampling"):
            for option in options:
                if _name(option).lower() == wanted:
                    return option
    return None


def _sidechain_device(ctx, track, device):
    if device is not None:
        obj = ctx.device(track, device)
        if not compat.has(obj, "available_input_routing_types"):
            raise BridgeError("unsupported", "%r has no side-chain routing in the Python API "
                              "(Live 12.4.5 exposes it on Compressor only)"
                              % compat.safe_getattr(obj, "name", ""))
        return obj
    for obj in compat.safe_getattr(track, "devices", ()) or ():
        if compat.has(obj, "available_input_routing_types"):
            return obj
    raise BridgeError("unsupported", "no device on %r exposes side-chain routing to the Python "
                      "API (Live 12.4.5: Compressor only) — add a Compressor first"
                      % compat.safe_getattr(track, "name", ""))


def _enable_sidechain(device):
    for parameter in compat.safe_getattr(device, "parameters", ()) or ():
        name = str(compat.safe_getattr(parameter, "name", "")).lower()
        if name in _SIDECHAIN_SWITCHES:
            try:
                parameter.value = float(compat.safe_getattr(parameter, "max", 1.0))
                return True
            except Exception:
                return False
    return None


@command("routing.route", mutating=True,
         doc="Route track A into track B (input, output or compressor side-chain)")
def routing_route(ctx, source, destination, method="input", channel=None, monitoring=None,
                  device=None):
    """Connect two tracks.

    Args:
        source: the track whose signal is used (index, name, path, "master").
        destination: the track (or its device) that receives it.
        method:
            "input" (default) — destination's INPUT = source (resampling /
            recording one track onto another; source still plays to Main);
            "output" — source's OUTPUT = destination (bus/submix: source no
            longer goes to Main directly);
            "sidechain" — a side-chain capable device on destination (the
            Compressor) listens to source.
        channel: optional channel/tap, e.g. "Post FX", "Pre FX", "Post Mixer"
            (input), "Track In" (output). Default = Live's default.
        monitoring: optional "in"/"auto"/"off" for the destination (input
            method) — use "in" to hear the routed signal, "off" when you only
            want to record it.
        device: sidechain method only — which device on destination (index or
            name); default = the first one with side-chain routing.

    Returns:
        {method, source, destination, routing: {...} (what was changed, as in
        ``routing.get`` without the available lists), notes: [...]}.

    Gotchas:
        Live only offers tracks of a compatible kind (MIDI into MIDI tracks,
        audio into audio tracks); otherwise ``not_found`` lists the choices.
        Routing the master into a track uses its "Main"/"Resampling" input.
        Only Compressor exposes side-chain routing to the Python API.
    """
    method = str(method or "input").strip().lower()
    if method not in ("input", "output", "sidechain"):
        raise BridgeError("bad_args", "method must be 'input', 'output' or 'sidechain'")
    if monitoring is not None:
        if method != "input":
            raise BridgeError("bad_args", "monitoring only applies to method='input'")
        parse_monitoring(monitoring)
    if device is not None and method != "sidechain":
        raise BridgeError("bad_args", "device only applies to method='sidechain'")
    src = resolve_track(ctx, source)
    dst = resolve_track(ctx, destination)
    if src == dst:
        raise BridgeError("bad_args", "source and destination are the same track")
    notes = []
    if method == "input":
        target, prop_prefix, label = dst, "input", compat.safe_getattr(dst, "name", "")
    elif method == "output":
        target, prop_prefix, label = src, "output", compat.safe_getattr(src, "name", "")
    else:
        target = _sidechain_device(ctx, dst, device)
        prop_prefix, label = "input", compat.safe_getattr(target, "name", "")
    types_prop = "available_%s_routing_types" % prop_prefix
    if not compat.has(target, types_prop):
        raise BridgeError("invalid_state", "%r has no %s routing" % (label, prop_prefix))
    options = _options(target, types_prop)
    wanted = dst if method == "output" else src
    option = _option_for_track(ctx, options, wanted)
    if option is None:
        raise BridgeError("not_found", "%r does not offer %r as %s routing (choices: %s)"
                          % (label, compat.safe_getattr(wanted, "name", ""), prop_prefix,
                             ", ".join(repr(_name(o)) for o in options)))
    _assign(target, "%s_routing_type" % prop_prefix, option, str(label))
    if channel is not None:
        choice = match_option(_options(target, "available_%s_routing_channels" % prop_prefix),
                              channel, "%s routing channel" % prop_prefix)
        _assign(target, "%s_routing_channel" % prop_prefix, choice, str(label))
    if monitoring is not None:
        set_monitoring(dst, monitoring)
    result = {"method": method,
              "source": compat.safe_getattr(src, "name"),
              "destination": compat.safe_getattr(dst, "name")}
    if method == "sidechain":
        enabled = _enable_sidechain(target)
        result["device"] = ctx.path_of(target)
        result["routing"] = {"input": _current(target, "input")}
        result["sidechain_enabled"] = enabled
        if enabled is None:
            notes.append("could not find the device's side-chain on/off switch — enable "
                         "'Sidechain' on the device (or via its parameters)")
    else:
        result["routing"] = routing_state(ctx, target, include_available=False)
        if method == "input" and compat.safe_getattr(dst, "can_be_armed", False) and \
                not compat.safe_getattr(dst, "arm", False) and monitoring_of(dst) == "auto":
            notes.append("monitoring is 'auto' and %r is not armed — you will not hear the "
                         "routed signal until you arm it or set monitoring='in'"
                         % compat.safe_getattr(dst, "name", ""))
    if notes:
        result["notes"] = notes
    return result
