"""Racks (Instrument / Audio Effect / MIDI Effect / Drum Rack): chains and
their mixers, macros, variations, chain selector, drum pads.

Addressing: ``track`` + ``device`` exactly as in ``devices.py``.  When
``device`` is omitted the selected device is used if it is a rack, otherwise
the first rack on the track (top level first, then nested) — for the drum
commands the first Drum Rack.

Chains (``chain`` argument): an index into ``rack.chains``, a chain name
(exact, case-insensitive, unique prefix) or a LOM path such as
``"song.tracks[2].devices[0].chains[3]"`` / ``"...return_chains[0]"`` (then
``track``/``device`` are not needed).

Drum pads (``note`` argument): a MIDI note 0..127, a note name in Live's
octave numbering (``"C1"`` = 36, ``"F#2"``, ``"Db1"``; C3 = 60), a pad name
(``"Kick"``) or the name of one of the pad's chains.

Live facts (docs/LIVE_API_DUMP_12.4.5.md, checked on Live 12.4.5): ``drum_pads``
has 128 pads only for the top-most Drum Rack (nested Drum Racks report none —
chains still carry ``in_note``); ``DrumPad.name`` is read-only: the chain's name
for one chain, "Multi" for several, the note name ("D1") for an empty pad;
``insert_chain`` and ``DrumChain.in_note`` need Live 12.3+; ``in_note`` /
``out_note`` accept 0..127 only (no "all notes" value through the API) and a new
Drum Rack chain always starts on C1 (36); ``choke_group`` is 0..16; a chain's
``mute`` is its activator switch (``mixer_device.chain_activator``); macros are
the parameters whose ``original_name`` is "Macro N" (their ``name`` changes when
mapped); variations need Live 11+ and only store/recall/randomize *mapped*
macros.
"""

import os
import re

from .. import compat
from .. import serialize
from ..registry import BridgeError, command
from .devices import (
    _track_of_device, canonical_device_name, check_detail, device_brief, display_of, index_in,
    is_drum_rack,
    is_rack, iter_devices, note_name, num, parameters_of, path_of, prune, resolve_device,
    resolve_track, resolve_value, write_parameter, _is_unknown_device_error, _name,
)

_MACRO_RE = re.compile(r"^macro\s*(\d+)$", re.IGNORECASE)
_NOTE_RE = re.compile(r"^([a-g])([#b]?)(-?\d+)$", re.IGNORECASE)
_PITCH_CLASSES = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}


# ==========================================================================
# resolution
# ==========================================================================

def resolve_rack(ctx, track=None, device=None, drum=False):
    """``(rack, path)`` — see the module docstring for the defaults."""
    wanted = is_drum_rack if drum else is_rack
    label = "Drum Rack" if drum else "rack"
    if device is None:
        track_obj = resolve_track(ctx, track)
        track_path = path_of(ctx, track_obj, "track")
        selected = compat.safe_getattr(compat.safe_getattr(track_obj, "view"),
                                       "selected_device")
        if selected is not None and wanted(selected):
            return selected, path_of(ctx, selected)
        found = sorted(((depth, i, dev, path) for i, (dev, path, depth)
                        in enumerate(iter_devices(track_obj, track_path)) if wanted(dev)),
                       key=lambda item: (item[0], item[1]))
        if not found:
            raise BridgeError("not_found", "%r has no %s" % (_name(track_obj, "track"), label))
        return found[0][2], found[0][3]
    dev, path = resolve_device(ctx, track, device)
    if not wanted(dev):
        raise BridgeError("bad_args", "%r (%s) is not a %s"
                          % (_name(dev), compat.safe_getattr(dev, "class_name", "?"), label))
    return dev, path


def _rack_of_chain(chain):
    obj = chain
    for _ in range(4):
        obj = compat.safe_getattr(obj, "canonical_parent")
        if obj is None:
            return None
        if is_rack(obj):
            return obj
    return None


def resolve_chain(ctx, track=None, device=None, chain=None):
    """``(chain, chain_path, rack, rack_path)``."""
    if isinstance(chain, bool) or chain is None:
        raise BridgeError("bad_args", "chain must be an index, a name or a LOM path")
    if isinstance(chain, str) and chain.strip().startswith("song."):
        text = chain.strip()
        obj = ctx.resolve(text)
        if obj is None or serialize.kind_of(obj) != "chain":
            raise BridgeError("bad_args", "%s is not a rack chain" % text)
        rack = _rack_of_chain(obj)
        return obj, path_of(ctx, obj, text), rack, path_of(ctx, rack) if rack is not None \
            else None
    rack, rack_path = resolve_rack(ctx, track, device)
    chains = list(compat.safe_getattr(rack, "chains", ()) or ())
    if isinstance(chain, int) or (isinstance(chain, str) and chain.strip().lstrip("-").isdigit()):
        index = int(chain)
        if -len(chains) <= index < len(chains):
            real = index % len(chains)
            return chains[real], "%s.chains[%d]" % (rack_path, real), rack, rack_path
        raise BridgeError("not_found", "%s.chains[%d]: index out of range (%d chains)"
                          % (rack_path, index, len(chains)))
    if not isinstance(chain, str) or not chain.strip():
        raise BridgeError("bad_args", "chain must be an index, a name or a LOM path")
    text = chain.strip()
    lowered = text.lower()
    indexed = list(enumerate(chains))
    for level, tier in enumerate((
            [(i, c) for i, c in indexed if _name(c) == text],
            [(i, c) for i, c in indexed if _name(c).lower() == lowered],
            [(i, c) for i, c in indexed if _name(c).lower().startswith(lowered)])):
        if len(tier) == 1 or (tier and level < 2):
            i, c = tier[0]
            return c, "%s.chains[%d]" % (rack_path, i), rack, rack_path
        if tier:
            raise BridgeError("bad_args", "chain %r is ambiguous: %s"
                              % (text, ", ".join(repr(_name(c)) for _i, c in tier[:8])))
    raise BridgeError("not_found", "no chain named %r in %r (have: %s)"
                      % (text, _name(rack), ", ".join(repr(_name(c)) for c in chains[:16])))


def parse_note(value, pads=None):
    """A MIDI note from an int, a note name ("C1" = 36) or a pad name."""
    if isinstance(value, bool):
        raise BridgeError("bad_args", "note must be 0..127, a note name like 'C1' or a pad name")
    if isinstance(value, int):
        if 0 <= value <= 127:
            return value
        raise BridgeError("bad_args", "note must be 0..127, got %d" % value)
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return parse_note(int(text), pads)
        match = _NOTE_RE.match(text)
        if match:
            pitch = _PITCH_CLASSES[match.group(1).lower()]
            if match.group(2) == "#":
                pitch += 1
            elif match.group(2).lower() == "b":
                pitch -= 1
            note = (int(match.group(3)) + 2) * 12 + pitch
            if 0 <= note <= 127:
                return note
            raise BridgeError("bad_args", "%r is outside the MIDI range" % text)
        if pads is not None:
            lowered = text.lower()
            named = [(n, name) for n, name in pads if name and name.lower() == lowered]
            if not named:
                named = [(n, name) for n, name in pads if name and name.lower().startswith(lowered)]
            if len(set(n for n, _name_ in named)) == 1 or \
                    (named and named[0][1].lower() == lowered):
                return named[0][0]
            if named:
                raise BridgeError("bad_args", "pad %r is ambiguous: %s"
                                  % (text, ", ".join("%s (%d)" % (name, n)
                                                     for n, name in named[:8])))
            raise BridgeError("not_found", "no pad named %r (filled pads: %s)"
                              % (text, ", ".join("%s=%s" % (note_name(n), name)
                                                 for n, name in pads[:16]) or "none"))
    raise BridgeError("bad_args", "note must be 0..127, a note name like 'C1' or a pad name")


# ==========================================================================
# chain info
# ==========================================================================

def _param_value(param):
    if param is None:
        return None, None
    value = compat.safe_getattr(param, "value")
    return num(value), display_of(param, value)


def chain_info(chain, index, path, detail="summary"):
    """Compact dict for one chain."""
    get = compat.safe_getattr
    data = {"index": index, "name": _name(chain), "path": path,
            "mute": bool(get(chain, "mute", False)), "solo": bool(get(chain, "solo", False))}
    in_note = get(chain, "in_note")
    if isinstance(in_note, int) and not isinstance(in_note, bool):
        data["in_note"] = in_note
        data["key"] = note_name(in_note) if in_note >= 0 else "all"
    if detail == "minimal":
        return data
    if get(chain, "muted_via_solo", False):
        data["muted_via_solo"] = True
    mixer = get(chain, "mixer_device")
    if mixer is not None:
        volume, volume_display = _param_value(get(mixer, "volume"))
        panning, panning_display = _param_value(get(mixer, "panning"))
        data["volume"] = volume
        data["volume_display"] = volume_display
        data["panning"] = panning
        data["panning_display"] = panning_display
        sends = list(get(mixer, "sends", ()) or ())
        if sends and detail == "full":
            data["sends"] = [display_of(s) for s in sends]
    out_note = get(chain, "out_note")
    if isinstance(out_note, int) and not isinstance(out_note, bool):
        data["out_note"] = out_note
        choke = get(chain, "choke_group")
        if choke:
            data["choke_group"] = choke
    devices = list(get(chain, "devices", ()) or ())
    if detail == "full":
        data["devices"] = [prune({"index": i, "name": _name(d),
                                  "class_name": get(d, "class_name"),
                                  "is_active": bool(get(d, "is_active", True)),
                                  "path": "%s.devices[%d]" % (path, i) if path else None})
                           for i, d in enumerate(devices)]
        data["color_index"] = get(chain, "color_index")
    else:
        data["devices"] = [_name(d) for d in devices]
    return prune(data)


# ==========================================================================
# commands — chains
# ==========================================================================

@command("racks.chains", doc="List a rack's chains with mixer, mute/solo and devices")
def racks_chains(ctx, track=None, device=None, include_return_chains=True, detail="summary"):
    """List the chains of a rack.

    Args:
        track, device: the rack (see the module docstring for defaults).
        include_return_chains: also list the rack's return chains.
        detail: "minimal" (index, name, path, mute, solo, in_note/key),
            "summary" (+ volume/panning with display text, active, devices
            names, drum out_note/choke_group) or "full" (+ sends, device
            entries with paths, color).

    Returns:
        {rack: {path, name, class_name}, chain_count, selected_chain,
         chain_selector?: {value, display}, chains: [...], return_chains?: [...]}
    """
    check_detail(detail)
    rack, path = resolve_rack(ctx, track, device)
    get = compat.safe_getattr
    chains = list(get(rack, "chains", ()) or ())
    view = get(rack, "view")
    selected = get(view, "selected_chain") if view is not None else None
    result = {
        "rack": device_brief(rack, path),
        "chain_count": len(chains),
        "selected_chain": index_in(chains, selected) if selected is not None else None,
        "chains": [chain_info(c, i, "%s.chains[%d]" % (path, i), detail)
                   for i, c in enumerate(chains)],
    }
    selector = get(rack, "chain_selector")
    if selector is not None:
        value, display = _param_value(selector)
        result["chain_selector"] = {"value": value, "display": display}
    if include_return_chains:
        returns = list(get(rack, "return_chains", ()) or ())
        if returns:
            result["return_chains"] = [chain_info(c, i, "%s.return_chains[%d]" % (path, i),
                                                  detail)
                                       for i, c in enumerate(returns)]
    return prune(result)


def _set_chain_attr(chain, attr, value, what):
    try:
        setattr(chain, attr, value)
    except Exception as error:
        raise BridgeError("invalid_state", "cannot set %s to %r: %s" % (what, value, error))


def _set_mixer_param(param, value, what):
    if param is None:
        raise BridgeError("unsupported", "this chain has no %s" % what)
    target, _how, _clamped = resolve_value(param, value)
    write_parameter(param, target)


@command("racks.set_chain", mutating=True,
         doc="Chain settings: name, mute/solo, volume/pan (display text ok), select, drum notes")
def racks_set_chain(ctx, chain, track=None, device=None, name=None, mute=None, solo=None,
                    exclusive_solo=False, volume=None, panning=None, active=None, sends=None,
                    color_index=None, select=None, in_note=None, out_note=None,
                    choke_group=None):
    """Change a chain; every setting is optional.

    Args:
        chain: index, name or LOM path (see the module docstring).
        track, device: the rack when ``chain`` is not a path.
        name: rename the chain (for Drum Racks this renames the pad).
        mute, solo: booleans; ``exclusive_solo`` un-solos the other chains.
        volume, panning: number (internal: volume 0..1 where 0.85 = 0 dB,
            pan -1..1) or display text ("-6 dB", "0 dB", "25L", "C", "10R").
        active: chain activator on/off — the very same switch as ``mute``
            (active=false is mute=true); kept for symmetry with tracks.
        sends: {send index or name: value} for the rack's return chains.
        color_index: Live palette index.
        select: select the chain in the rack (and in Live's view).
        in_note: Drum Rack only (Live 12.3+) — the pad note that triggers the
            chain (0..127 or "C1"; moving it moves the chain to that pad).  Live
            12.4.5 rejects -1: there is no "all notes" setting via the API.
        out_note: Drum Rack only — note sent to the chain's devices (0..127).
        choke_group: Drum Rack only — 0 (none) .. 16.

    Returns:
        The chain in ``full`` detail.
    """
    if isinstance(mute, bool) and isinstance(active, bool) and mute == active:
        raise BridgeError("bad_args", "mute=%s and active=%s contradict each other — in Live "
                          "they are one switch (the chain activator)"
                          % (str(mute).lower(), str(active).lower()))
    ch, path, rack, _rack_path = resolve_chain(ctx, track, device, chain)
    get = compat.safe_getattr
    if name is not None:
        if not isinstance(name, str) or not name.strip():
            raise BridgeError("bad_args", "name must be a non-empty string")
        _set_chain_attr(ch, "name", name, "name")
    for attr, value in (("mute", mute), ("solo", solo)):
        if value is None:
            continue
        if not isinstance(value, bool):
            raise BridgeError("bad_args", "%s must be a boolean" % attr)
        _set_chain_attr(ch, attr, value, attr)
    if solo and exclusive_solo and rack is not None:
        for other in get(rack, "chains", ()) or ():
            if other != ch and get(other, "solo", False):
                _set_chain_attr(other, "solo", False, "solo")
    mixer = get(ch, "mixer_device")
    if volume is not None:
        _set_mixer_param(get(mixer, "volume"), volume, "volume")
    if panning is not None:
        _set_mixer_param(get(mixer, "panning"), panning, "panning")
    if active is not None:
        if not isinstance(active, bool):
            raise BridgeError("bad_args", "active must be a boolean")
        _set_mixer_param(get(mixer, "chain_activator"), active, "chain activator")
    if sends is not None:
        if not isinstance(sends, dict) or not sends:
            raise BridgeError("bad_args", "sends must be an object {send index or name: value}")
        send_params = list(get(mixer, "sends", ()) or ())
        for key, value in sends.items():
            param = None
            if str(key).strip().lstrip("-").isdigit():
                index = int(key)
                if -len(send_params) <= index < len(send_params):
                    param = send_params[index]
            else:
                for candidate in send_params:
                    if _name(candidate).lower() == str(key).strip().lower():
                        param = candidate
                        break
            if param is None:
                raise BridgeError("not_found", "no send %r on this chain (%d sends: %s)"
                                  % (key, len(send_params),
                                     ", ".join(repr(_name(s)) for s in send_params)))
            _set_mixer_param(param, value, "send")
    if color_index is not None:
        if isinstance(color_index, bool) or not isinstance(color_index, int) or color_index < 0:
            raise BridgeError("bad_args", "color_index must be an integer >= 0")
        _set_chain_attr(ch, "color_index", color_index, "color_index")
    drum_args = (("in_note", in_note), ("out_note", out_note), ("choke_group", choke_group))
    for attr, value in drum_args:
        if value is None:
            continue
        if not compat.has(ch, attr):
            raise BridgeError("unsupported", "%s only exists on Drum Rack chains%s"
                              % (attr, " (Live 12.3+)" if attr == "in_note" else ""))
        if value == -1 or value == "-1":
            raise BridgeError("bad_args", "%s must be 0..127 or a note name — Live 12.4.5 has "
                              "no 'all notes' value for drum chains" % attr)
        if attr == "choke_group":
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 16:
                raise BridgeError("bad_args", "choke_group must be 0..16")
            number = value
        else:
            number = parse_note(value)
        _set_chain_attr(ch, attr, number, attr)
    if select is not None:
        if not isinstance(select, bool):
            raise BridgeError("bad_args", "select must be a boolean")
        if select:
            rack_view = get(rack, "view") if rack is not None else None
            if rack_view is not None and compat.has(rack_view, "selected_chain"):
                _set_chain_attr(rack_view, "selected_chain", ch, "selected_chain")
            song_view = ctx.view
            if song_view is not None and compat.has(song_view, "selected_chain"):
                try:
                    song_view.selected_chain = ch
                except Exception as error:
                    ctx.log("racks.set_chain: song.view.selected_chain failed: %s", error)
    chains = list(get(rack, "chains", ()) or ()) if rack is not None else []
    return chain_info(ch, index_in(chains, ch), path, "full")


@command("racks.insert_chain", mutating=True,
         doc="Add a chain to a rack (Live 12.3+), optionally with a native device")
def racks_insert_chain(ctx, track=None, device=None, index=-1, name=None, in_note=None,
                       device_name=None):
    """Insert a new, empty chain into a rack.

    Args:
        track, device: the rack (see the module docstring).
        index: position, -1 = end.
        name: chain name.
        in_note: Drum Racks — the pad that triggers it (0..127 or "C1").
            Without it Live puts every new Drum Rack chain on C1 (36) — when
            C1 already has a chain the pad becomes a "Multi" pad, so pass the
            note you want.
        device_name: optional native device to put in the chain ("Simpler",
            "Operator", "Reverb" ... — same names as devices.insert).

    Returns:
        The new chain in ``full`` detail.

    Gotchas:
        Live 12.3+ only (``unsupported`` otherwise).  To fill a drum pad with
        a sample, insert a chain with ``device_name="Simpler"`` and then call
        simpler.action(action="replace_sample") on the new Simpler.
    """
    if isinstance(index, bool) or not isinstance(index, int) or index < -1:
        raise BridgeError("bad_args", "index must be -1 (end) or a position >= 0")
    rack, rack_path = resolve_rack(ctx, track, device)
    if not compat.has(rack, "insert_chain"):
        raise BridgeError("unsupported", "insert_chain needs Live 12.3+")
    count = len(compat.safe_getattr(rack, "chains", ()) or ())
    if index > count:
        raise BridgeError("bad_args", "index %d is past the end (%d chains)" % (index, count))
    try:
        new_chain = rack.insert_chain(index)
    except Exception as error:
        raise BridgeError("invalid_state", "Live refused to insert a chain: %s" % error)
    chains = list(compat.safe_getattr(rack, "chains", ()) or ())
    if new_chain is None:
        position = count if index == -1 else index
        new_chain = chains[position] if 0 <= position < len(chains) else None
    if new_chain is None:
        raise BridgeError("internal", "the new chain did not appear")
    if name is not None:
        if not isinstance(name, str) or not name.strip():
            raise BridgeError("bad_args", "name must be a non-empty string")
        _set_chain_attr(new_chain, "name", name, "name")
    if in_note is not None:
        if not compat.has(new_chain, "in_note"):
            raise BridgeError("unsupported", "in_note only exists on Drum Rack chains "
                              "(Live 12.3+)")
        if in_note in (-1, "-1"):
            raise BridgeError("bad_args", "in_note must be 0..127 or a note name — Live "
                              "12.4.5 has no 'all notes' value for drum chains")
        _set_chain_attr(new_chain, "in_note", parse_note(in_note), "in_note")
    if device_name is not None:
        if not isinstance(device_name, str) or not device_name.strip():
            raise BridgeError("bad_args", "device_name must be a device name")
        if not compat.has(new_chain, "insert_device"):
            raise BridgeError("unsupported", "Chain.insert_device needs Live 12.3+")
        wanted = device_name.strip()
        try:
            new_chain.insert_device(wanted, -1)
        except Exception as error:
            canonical, browser_path = (None, None)
            if _is_unknown_device_error(error):
                canonical, browser_path = canonical_device_name(ctx, wanted)
            if canonical is not None and canonical != wanted:
                try:
                    new_chain.insert_device(canonical, -1)
                except Exception as second:
                    raise BridgeError("invalid_state", "Live refused to insert %r into the "
                                      "chain: %s" % (canonical, second))
            elif browser_path is not None:
                raise BridgeError("unsupported", "%r is a Max for Live device — "
                                  "insert_device cannot put it into a chain (the chain was "
                                  "created empty)" % wanted)
            else:
                raise BridgeError("invalid_state" if not _is_unknown_device_error(error)
                                  else "not_found",
                                  "Live refused to insert %r into the chain: %s (the chain "
                                  "was created empty)" % (device_name, error))
    position = index_in(chains, new_chain)
    return chain_info(new_chain, position, "%s.chains[%d]" % (rack_path, position)
                      if position is not None else None, "full")


# ==========================================================================
# commands — macros, variations
# ==========================================================================

def macro_parameters(rack):
    """``[(number 1..16, parameter index, parameter)]``."""
    params = parameters_of(rack)
    macros = []
    for index, param in enumerate(params):
        original = compat.safe_getattr(param, "original_name", None) or _name(param)
        match = _MACRO_RE.match(str(original).strip())
        if match:
            macros.append((int(match.group(1)), index, param))
    if not macros:
        macros = [(i, i, params[i]) for i in range(1, min(17, len(params)))]
    macros.sort(key=lambda item: item[0])
    return macros


def _macro_list(rack, include_hidden):
    get = compat.safe_getattr
    visible = get(rack, "visible_macro_count")
    mapped = list(get(rack, "macros_mapped", ()) or ())
    entries = []
    for number, index, param in macro_parameters(rack):
        if not include_hidden and isinstance(visible, int) and number > visible:
            continue
        value = get(param, "value")
        entry = {"number": number, "index": index, "name": _name(param), "value": num(value, 4),
                 "display": display_of(param, value)}
        if number - 1 < len(mapped):
            entry["mapped"] = bool(mapped[number - 1])
        entries.append(entry)
    return entries


def _find_macro(rack, key):
    macros = macro_parameters(rack)
    if isinstance(key, bool):
        raise BridgeError("bad_args", "macro keys are numbers 1..16 or names")
    text = str(key).strip()
    if text.isdigit():
        number = int(text)
        for num_, index, param in macros:
            if num_ == number:
                return number, index, param
        raise BridgeError("not_found", "macro %d does not exist (1..%d)" % (number, len(macros)))
    match = _MACRO_RE.match(text)
    if match:
        return _find_macro(rack, match.group(1))
    lowered = text.lower()
    for tier in ([m for m in macros if _name(m[2]) == text],
                 [m for m in macros if _name(m[2]).strip().lower() == lowered],
                 [m for m in macros if _name(m[2]).strip().lower().startswith(lowered)]):
        if len(tier) == 1:
            return tier[0]
        if tier:
            raise BridgeError("bad_args", "macro %r is ambiguous: %s"
                              % (text, ", ".join("%d %r" % (m[0], _name(m[2])) for m in tier)))
    raise BridgeError("not_found", "no macro named %r (macros: %s)"
                      % (text, ", ".join("%d %r" % (m[0], _name(m[2])) for m in macros)))


def _macros_result(rack, path, include_hidden):
    get = compat.safe_getattr
    result = {
        "rack": device_brief(rack, path),
        "visible_macro_count": get(rack, "visible_macro_count"),
        "has_macro_mappings": get(rack, "has_macro_mappings"),
        "macros": _macro_list(rack, bool(include_hidden)),
        "variation_count": get(rack, "variation_count"),
        "selected_variation_index": get(rack, "selected_variation_index"),
    }
    selector = get(rack, "chain_selector")
    if selector is not None:
        value, display = _param_value(selector)
        result["chain_selector"] = {"value": value, "display": display}
    return prune(result)


@command("racks.macros", doc="Read a rack's macros (Macro 1..16), chain selector, variations")
def racks_macros(ctx, track=None, device=None, include_hidden=False):
    """Read a rack's macro knobs.

    Args:
        track, device: the rack (see the module docstring).
        include_hidden: also list macros beyond ``visible_macro_count``.

    Returns:
        {rack, visible_macro_count, has_macro_mappings, macros: [{number,
         index (parameter index), name, value (0..127), display, mapped}],
         chain_selector?: {value, display}, variation_count?,
         selected_variation_index?}

    Gotchas:
        A macro's ``name`` becomes the mapped parameter's name once mapped;
        ``number`` stays stable.
    """
    rack, path = resolve_rack(ctx, track, device)
    return _macros_result(rack, path, include_hidden)


@command("racks.set_macros", mutating=True,
         doc="Set rack macros atomically, the visible macro count and the chain selector")
def racks_set_macros(ctx, track=None, device=None, values=None, normalized=False,
                     visible_count=None, chain_selector=None, include_hidden=False):
    """Change macros (and friends) in one undo step.

    Args:
        track, device: the rack (see the module docstring).
        values: {macro: value} — macro = number 1..16 (as a string key),
            "Macro 3" or its current (mapped) name; value = number 0..127
            (internal), display text as shown on the knob, or 0..1 with
            ``normalized``.  All are validated before anything is written.
        normalized: numeric values are 0..1.
        visible_count: show this many macros (1..16; Live 11+ racks).
        chain_selector: value for the Chain Selector (number or display).
        include_hidden: list hidden macros in the result too.

    Returns:
        The racks.macros shape after the change.
    """
    rack, path = resolve_rack(ctx, track, device)
    get = compat.safe_getattr
    if values is None and visible_count is None and chain_selector is None:
        raise BridgeError("bad_args", "pass values, visible_count and/or chain_selector")
    if values is not None:
        if not isinstance(values, dict) or not values:
            raise BridgeError("bad_args", "values must be an object {macro: value}")
        plan = []
        for key, value in values.items():
            _number, _index, param = _find_macro(rack, key)
            target, _how, _clamped = resolve_value(param, value, bool(normalized))
            if not get(param, "is_enabled", True):
                raise BridgeError("invalid_state", "macro %r is disabled — nothing was changed"
                                  % key)
            plan.append((param, target))
        done = []
        try:
            for param, target in plan:
                before = get(param, "value")
                write_parameter(param, target)
                done.append((param, before))
        except BridgeError:
            for param, before in reversed(done):
                try:
                    param.value = before
                except Exception:
                    ctx.log("racks.set_macros: rollback failed for %s", _name(param))
            raise
    if visible_count is not None:
        if isinstance(visible_count, bool) or not isinstance(visible_count, int) \
                or not 1 <= visible_count <= 16:
            raise BridgeError("bad_args", "visible_count must be 1..16")
        if not compat.has(rack, "add_macro") or get(rack, "visible_macro_count") is None:
            raise BridgeError("unsupported", "this Live cannot change the macro count")
        for _ in range(16):
            current = get(rack, "visible_macro_count")
            if current == visible_count:
                break
            try:
                if current < visible_count:
                    rack.add_macro()
                else:
                    rack.remove_macro()
            except Exception as error:
                raise BridgeError("invalid_state", "cannot change the macro count: %s" % error)
    if chain_selector is not None:
        selector = get(rack, "chain_selector")
        if selector is None:
            raise BridgeError("unsupported", "this rack has no chain selector")
        target, _how, _clamped = resolve_value(selector, chain_selector, bool(normalized))
        write_parameter(selector, target)
    return _macros_result(rack, path, include_hidden)


VARIATION_ACTIONS = ("list", "store", "recall", "recall_last", "select", "delete", "randomize")


@command("racks.variations", mutating=True,
         doc="Macro variations: list, store, recall, select, delete, randomize macros")
def racks_variations(ctx, track=None, device=None, action="list", index=None):
    """Work with a rack's macro variations (Live 11+).

    Args:
        track, device: the rack (see the module docstring).
        action: "list" (default), "store" (save current macro values as a new
            variation — Live does not select it), "recall" (apply variation
            ``index``, or the selected one), "recall_last" (the most recently
            recalled one), "select" (just select ``index``), "delete" (delete
            ``index`` or the selected one; afterwards nothing is selected),
            "randomize" (Live's Rand button — randomizes the mapped macros not
            excluded from randomization).
        index: variation index (0-based) for recall/select/delete.

    Returns:
        {rack, action, variation_count, selected_variation_index,
         macros: [{number, name, value, display}]}

    Gotchas:
        Live's variations only touch macros that are mapped to something: on
        a rack without macro mappings recall/randomize change nothing (the
        answer then carries a ``note``).  The command is declared mutating, so
        even "list" runs inside an (empty) undo step; racks.macros is the pure
        read.
    """
    if action not in VARIATION_ACTIONS:
        raise BridgeError("bad_args", "action must be one of %s" % ", ".join(VARIATION_ACTIONS))
    if index is not None and (isinstance(index, bool) or not isinstance(index, int)
                              or index < 0):
        raise BridgeError("bad_args", "index must be an integer >= 0")
    rack, path = resolve_rack(ctx, track, device)
    get = compat.safe_getattr
    if get(rack, "variation_count") is None:
        raise BridgeError("unsupported", "macro variations need Live 11+")
    count = get(rack, "variation_count", 0)
    if index is not None and action in ("recall", "select", "delete"):
        if index >= count:
            raise BridgeError("not_found", "variation %d does not exist (%d variations)"
                              % (index, count))
        try:
            rack.selected_variation_index = index
        except Exception as error:
            raise BridgeError("invalid_state", "cannot select variation %d: %s" % (index, error))
    method = {"store": "store_variation", "recall": "recall_selected_variation",
              "recall_last": "recall_last_used_variation", "delete": "delete_selected_variation",
              "randomize": "randomize_macros"}.get(action)
    if method is not None:
        if action in ("recall", "delete") and index is None \
                and get(rack, "selected_variation_index", -1) < 0:
            raise BridgeError("invalid_state", "no variation is selected — pass index")
        target = get(rack, method)
        if not callable(target):
            raise BridgeError("unsupported", "%s is not available in this Live" % method)
        try:
            target()
        except Exception as error:
            raise BridgeError("invalid_state", "%s failed: %s" % (action, error))
    macros = [{"number": m["number"], "name": m["name"], "value": m["value"],
               "display": m["display"]} for m in _macro_list(rack, False)]
    result = {"rack": device_brief(rack, path), "action": action,
              "variation_count": get(rack, "variation_count"),
              "selected_variation_index": get(rack, "selected_variation_index"),
              "macros": macros}
    if action in ("store", "recall", "recall_last", "randomize") \
            and get(rack, "has_macro_mappings") is False:
        result["note"] = ("this rack has no macro mappings — Live's variations and "
                          "randomize only store/change mapped macros, so nothing changed")
    return result


# ==========================================================================
# commands — drum racks
# ==========================================================================

def _sample_name(devices, full, depth=0):
    """File name (or path) of the first sample found in a device list."""
    for dev in devices:
        sample = compat.safe_getattr(dev, "sample")
        if sample is not None:
            file_path = compat.safe_getattr(sample, "file_path")
            if file_path:
                return str(file_path) if full else \
                    os.path.basename(str(file_path).replace("\\", "/"))
        if depth < 2 and is_rack(dev):
            for chain in compat.safe_getattr(dev, "chains", ()) or ():
                found = _sample_name(compat.safe_getattr(chain, "devices", ()) or (), full,
                                     depth + 1)
                if found:
                    return found
    return None


def _pad_groups(rack):
    """``[(note, pad_or_None, [chains])]`` for every note 0..127."""
    get = compat.safe_getattr
    pads = list(get(rack, "drum_pads", ()) or ())
    if pads:
        groups = []
        for pad in pads:
            note = get(pad, "note")
            groups.append((note, pad, list(get(pad, "chains", ()) or ())))
        return groups, True
    by_note = {}
    for chain in get(rack, "chains", ()) or ():
        note = get(chain, "in_note")
        if isinstance(note, int) and not isinstance(note, bool) and note >= 0:
            by_note.setdefault(note, []).append(chain)
    return [(n, None, by_note.get(n, [])) for n in range(128)], False


PAD_COLUMNS = ["note", "key", "name", "chains", "devices", "sample", "mute", "solo", "choke"]


@command("racks.drum_pads", doc="Drum Rack pad map: note, name, devices, sample, mute/solo/choke")
def racks_drum_pads(ctx, track=None, device=None, include_empty=False, full_paths=False):
    """One compact table of a Drum Rack's pads.

    Args:
        track, device: the Drum Rack (default: selected/first Drum Rack on the
            track).
        include_empty: list all 128 pads, not only filled ones.
        full_paths: add ``path`` (pad) and ``chain`` (first chain) columns
            and full sample paths instead of file names.

    Returns:
        {rack: {path, name}, filled, with_samples, selected_pad (note)?,
         visible_pads: [first note, last note]?, columns: [note, key, name,
         chains, devices, sample, mute, solo, choke(, path, chain)],
         rows: [[36, "C1", "Kick", 1, "Simpler", "Kick.wav", false, false,
         0], ...]}

    Gotchas:
        ``key`` uses Live's octave numbering (C1 = 36, C3 = 60).  ``devices``
        lists the first chain's devices joined with " > ".  Nested Drum Racks
        have no pad objects in Live; their rows come from chain ``in_note``
        values and ``path`` is empty.
    """
    rack, path = resolve_rack(ctx, track, device, drum=True)
    get = compat.safe_getattr
    groups, have_pads = _pad_groups(rack)
    rows = []
    with_samples = 0
    filled = 0
    for note, pad, chains in groups:
        if chains:
            filled += 1
        elif not include_empty:
            continue
        first = chains[0] if chains else None
        devices = list(get(first, "devices", ()) or ()) if first is not None else []
        sample = None
        for chain in chains:
            sample = _sample_name(list(get(chain, "devices", ()) or ()), bool(full_paths))
            if sample:
                break
        if sample:
            with_samples += 1
        name = get(pad, "name") if pad is not None else (_name(first) if first else "")
        row = [note, note_name(note), name or "", len(chains),
               " > ".join(_name(d) for d in devices), sample,
               bool(get(pad, "mute", False)) if pad is not None else
               bool(first is not None and get(first, "mute", False)),
               bool(get(pad, "solo", False)) if pad is not None else
               bool(first is not None and get(first, "solo", False)),
               get(first, "choke_group", 0) if first is not None else 0]
        if full_paths:
            chain_index = index_in(get(rack, "chains", ()) or (), first) \
                if first is not None else None
            row.append("%s.drum_pads[%d]" % (path, note) if have_pads else None)
            row.append("%s.chains[%d]" % (path, chain_index) if chain_index is not None
                       else None)
        rows.append(row)
    result = {
        "rack": device_brief(rack, path),
        "filled": filled,
        "with_samples": with_samples,
        "columns": PAD_COLUMNS + (["path", "chain"] if full_paths else []),
        "rows": rows,
    }
    view = get(rack, "view")
    if view is not None and have_pads:
        selected = get(view, "selected_drum_pad")
        if selected is not None:
            result["selected_pad"] = get(selected, "note")
        scroll = get(view, "drum_pads_scroll_position")
        if isinstance(scroll, int):
            result["visible_pads"] = [scroll * 4, min(127, scroll * 4 + 15)]
    return prune(result)


def _pad_names(groups):
    """``[(note, name)]`` for filled pads: the pad name, then each chain's name
    (a pad with several chains is called "Multi" by Live)."""
    named = []
    for number, pad, chains in groups:
        if not chains:
            continue
        pad_name = compat.safe_getattr(pad, "name") if pad is not None else None
        names = [pad_name] if pad_name else []
        names += [_name(chain) for chain in chains if _name(chain)]
        seen = set()
        for name in names:
            if name.lower() not in seen:
                seen.add(name.lower())
                named.append((number, name))
    return named


def _resolve_pad(rack, note):
    groups, have_pads = _pad_groups(rack)
    number = parse_note(note, _pad_names(groups))
    return number, groups[number][1], groups[number][2], have_pads


@command("racks.set_pad", mutating=True,
         doc="Drum pad: mute/solo, rename, choke group, out note, select, copy, clear")
def racks_set_pad(ctx, note, track=None, device=None, mute=None, solo=None, name=None,
                  choke_group=None, out_note=None, select=None, copy_to=None, clear=False):
    """Edit one Drum Rack pad; every setting is optional.

    Args:
        note: 36, "C1", a pad name ("Kick") or the name of one of the pad's
            chains (Live calls a pad with several chains "Multi").
        track, device: the Drum Rack (default: selected/first Drum Rack).
        mute, solo: pad mute/solo.
        name: renames the pad (= its first chain; Live derives pad names from
            chains).
        choke_group: 0 (none) .. 16, applied to every chain of the pad.
        out_note: note sent into the pad's devices (0..127 or "C3").
        select: select the pad in the Drum Rack and scroll it into view.
        copy_to: copy the pad's contents to another pad (note/name) —
            replaces what is there.
        clear: delete every chain of the pad (empties it).

    Returns:
        {rack, note, key, name, chains, mute, solo, choke, copied_to?,
         cleared?} — ``clear`` runs last.

    Gotchas:
        Pad objects exist only for the top-level Drum Rack; for nested ones
        mute/solo/select/copy/clear are ``unsupported`` (use racks.set_chain).
    """
    rack, path = resolve_rack(ctx, track, device, drum=True)
    get = compat.safe_getattr
    number, pad, chains, have_pads = _resolve_pad(rack, note)
    needs_pad = [k for k, v in (("mute", mute), ("solo", solo), ("select", select or None),
                                ("copy_to", copy_to)) if v is not None] + \
        (["clear"] if clear else [])
    if needs_pad and pad is None:
        raise BridgeError("unsupported", "%s need(s) the pad objects of a top-level Drum Rack; "
                          "this one is nested — use racks.set_chain" % ", ".join(needs_pad))
    for attr, value in (("mute", mute), ("solo", solo)):
        if value is None:
            continue
        if not isinstance(value, bool):
            raise BridgeError("bad_args", "%s must be a boolean" % attr)
        _set_chain_attr(pad, attr, value, attr)
    if name is not None:
        if not isinstance(name, str) or not name.strip():
            raise BridgeError("bad_args", "name must be a non-empty string")
        if not chains:
            raise BridgeError("invalid_state", "pad %s is empty — nothing to rename"
                              % note_name(number))
        _set_chain_attr(chains[0], "name", name, "name")
    if choke_group is not None:
        if isinstance(choke_group, bool) or not isinstance(choke_group, int) \
                or not 0 <= choke_group <= 16:
            raise BridgeError("bad_args", "choke_group must be 0..16")
        if not chains:
            raise BridgeError("invalid_state", "pad %s is empty" % note_name(number))
        for chain in chains:
            _set_chain_attr(chain, "choke_group", choke_group, "choke_group")
    if out_note is not None:
        target = parse_note(out_note)
        if not chains:
            raise BridgeError("invalid_state", "pad %s is empty" % note_name(number))
        for chain in chains:
            _set_chain_attr(chain, "out_note", target, "out_note")
    result = {}
    if copy_to is not None:
        destination = parse_note(copy_to, _pad_names(_pad_groups(rack)[0]))
        if destination == number:
            raise BridgeError("bad_args", "copy_to is the same pad")
        try:
            rack.copy_pad(number, destination)
        except Exception as error:
            raise BridgeError("invalid_state", "copy_pad failed: %s" % error)
        result["copied_to"] = destination
    if select:
        view = get(rack, "view")
        if view is None:
            raise BridgeError("unsupported", "this rack has no view")
        _set_chain_attr(view, "selected_drum_pad", pad, "selected_drum_pad")
        scroll = get(view, "drum_pads_scroll_position")
        if isinstance(scroll, int) and not scroll * 4 <= number <= scroll * 4 + 15:
            try:
                view.drum_pads_scroll_position = max(0, min(28, number // 4 - 1))
            except Exception as error:
                ctx.log("racks.set_pad: scrolling failed: %s", error)
    if clear:
        if not chains:
            raise BridgeError("invalid_state", "pad %s is already empty" % note_name(number))
        try:
            pad.delete_all_chains()
        except Exception as error:
            raise BridgeError("invalid_state", "delete_all_chains failed: %s" % error)
        result["cleared"] = True
    number, pad, chains, have_pads = _resolve_pad(rack, number)
    first = chains[0] if chains else None
    result.update({
        "rack": device_brief(rack, path),
        "note": number, "key": note_name(number),
        "name": (get(pad, "name") if pad is not None else _name(first)) or "",
        "chains": len(chains),
        "mute": bool(get(pad, "mute", False)) if pad is not None else None,
        "solo": bool(get(pad, "solo", False)) if pad is not None else None,
        "choke": get(first, "choke_group") if first is not None else None,
        "path": "%s.drum_pads[%d]" % (path, number) if have_pads else None,
    })
    return prune(result)


# ==========================================================================
# commands — Live.Conversions (pad <-> track)
# ==========================================================================

RACK_CONVERSIONS = ("pad_to_track", "track_to_pad")


def _conversion(name):
    func = compat.live_enum("Conversions." + name)
    if func is None or not callable(func):
        raise BridgeError("unsupported", "Live.Conversions.%s is not available in this Live "
                          "(needs Live 12)" % name)
    return func


@command("racks.convert", mutating=True,
         doc="Drum pad -> its own MIDI track, or a track's devices -> a new Drum Rack pad")
def racks_convert(ctx, action, track=None, device=None, note=None, select=False):
    """Live 12's Drum Rack conversions (``Live.Conversions``).

    Args:
        action: "pad_to_track" — copy the device chain of pad ``note`` of the
            Drum Rack (``track``/``device``) onto a new MIDI track
            (``create_midi_track_from_drum_pad``; the pad stays as it is);
            "track_to_pad" — move every device of ``track`` onto the C1 (36) pad
            of a new Drum Rack on that same track
            (``move_devices_on_track_to_new_drum_rack_pad``; Live rebuilds the track
            after the selected track, so its index can change — ``track`` in the
            answer is where it is now).
        track: the track (for pad_to_track the Drum Rack's track; default: the
            selected track).
        device: pad_to_track — the Drum Rack (default: selected/first Drum Rack).
        note: pad_to_track — the pad: 36, "C1", a pad or chain name.
        select: select the new track (pad_to_track) afterwards.

    Returns:
        pad_to_track: {action, via, pad: {note, key, name}, new_track: {path, name,
        type}, devices: [summaries]}.
        track_to_pad: {action, via, track, rack: summary with path, pad?: the C1
        pad Live returns (``song.tracks[i].devices[0].drum_pads[36]``), devices:
        [summaries of the track's devices after the move]}.

    Gotchas:
        Regular tracks only (not returns/master); pad objects exist only for a
        top-level Drum Rack.  Live creates the new track at its own position
        (usually right after the source track).
    """
    if action not in RACK_CONVERSIONS:
        raise BridgeError("bad_args", "action must be one of %s" % ", ".join(RACK_CONVERSIONS))
    song = ctx.song
    get = compat.safe_getattr
    if action == "pad_to_track":
        if note is None:
            raise BridgeError("bad_args", "pad_to_track needs note (36, 'C1' or a pad name)")
        rack, _path = resolve_rack(ctx, track, device, drum=True)
        number, pad, chains, _have_pads = _resolve_pad(rack, note)
        if pad is None:
            raise BridgeError("unsupported", "this Drum Rack is nested — Live has no pad "
                              "objects for it")
        if not chains:
            raise BridgeError("invalid_state", "pad %s is empty" % note_name(number))
        func = _conversion("create_midi_track_from_drum_pad")
        before = list(get(song, "tracks", ()) or ())
        try:
            func(song, pad)
        except Exception as error:
            raise BridgeError("invalid_state", "create_midi_track_from_drum_pad failed: %s"
                              % error)
        added = [t for t in get(song, "tracks", ()) or ()
                 if not any(t == old for old in before)]
        if not added:
            raise BridgeError("invalid_state", "Live did not create a track for pad %s"
                              % note_name(number))
        new_track = added[-1]
        base = path_of(ctx, new_track, "") or ""
        if select:
            try:
                ctx.view.selected_track = new_track
            except Exception as error:
                ctx.log("racks.convert: selecting the new track failed: %s", error)
        return prune({
            "action": action, "via": "Live.Conversions.create_midi_track_from_drum_pad",
            "pad": {"note": number, "key": note_name(number), "name": get(pad, "name")},
            "new_track": ctx.summarize(new_track, "minimal"),
            "devices": [device_brief(d, "%s.devices[%d]" % (base, i))
                        for i, d in enumerate(get(new_track, "devices", ()) or ())],
        })
    if device is not None or note is not None:
        raise BridgeError("bad_args", "track_to_pad takes only track")
    track_obj = resolve_track(ctx, track)
    tracks = list(get(song, "tracks", ()) or ())
    index = index_in(tracks, track_obj)
    if index is None:
        raise BridgeError("bad_args", "%r is not a regular track (returns/master cannot be "
                          "converted)" % _name(track_obj, "track"))
    if not list(get(track_obj, "devices", ()) or ()):
        raise BridgeError("invalid_state", "%r has no devices to move"
                          % _name(track_obj, "track"))
    func = _conversion("move_devices_on_track_to_new_drum_rack_pad")
    try:
        returned = func(song, index)
    except Exception as error:
        raise BridgeError("invalid_state", "move_devices_on_track_to_new_drum_rack_pad "
                          "failed: %s" % error)
    # Live 12.4.5 rebuilds the track: the old Python Track object turns into an empty
    # "<Track>" and the new one is created after the *selected* track (then the old one is
    # deleted), so its index can change — find it through the pad Live returns
    tracks = list(get(song, "tracks", ()) or ())
    moved = _track_of_device(returned) if returned is not None else None
    if moved is not None:
        track_obj = moved
    elif 0 <= index < len(tracks):
        track_obj = tracks[index]
    new_index = index_in(tracks, track_obj)
    base = "song.tracks[%d]" % (index if new_index is None else new_index)
    devices = list(get(track_obj, "devices", ()) or ())
    result = {"action": action, "via": "Live.Conversions.move_devices_on_track_to_new_drum_"
                                       "rack_pad",
              "track": ctx.summarize(track_obj, "minimal"),
              "devices": [device_brief(d, "%s.devices[%d]" % (base, i))
                          for i, d in enumerate(devices)]}
    racks_found = [(i, d) for i, d in enumerate(devices) if is_drum_rack(d)]
    if racks_found:
        i, rack = racks_found[0]
        result["rack"] = device_brief(rack, "%s.devices[%d]" % (base, i))
    if returned is not None:
        # Live returns the new Drum Rack's C1 pad (verified on 12.4.5)
        try:
            summary = ctx.summarize(returned, "minimal")
        except Exception:
            summary = str(returned)
        result["pad" if isinstance(summary, dict) and summary.get("kind") == "drum_pad"
               else "result"] = summary
    return prune(result)
