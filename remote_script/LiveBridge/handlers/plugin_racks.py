"""Full control of big plug-ins (Serum 2 first, any VST3) through generated rack presets.

Live 12.4.5 exposes a plug-in's parameters only through the device's Configure
list, which starts empty for big plug-ins (Serum 2: 0 of 2623), and no API
fills it.  A rack preset (``.adg``), however, stores that list as plain
``ParameterId`` numbers and Live resolves the names itself when the preset
loads.  So these commands *write* such a preset (``plugin_racks_lib``), let Live
index it in ``<User Library>/LiveBridge/Racks`` and load it:

* ``plugin_racks.map`` — finds a plug-in's name -> ParameterId map by loading
  probe racks (128 candidate ids each) on a temporary "LB_MAP" track and
  reading the names back; cached in ``<User Library>/LiveBridge/Maps``.  Serum
  2's complete map ships in ``plugin_maps/serum2_vst3.json``.
* ``plugin_racks.expose`` — puts the plug-in in an Instrument Rack (effects: an
  Audio Effect Rack) that exposes up to 128 chosen parameters (names or group
  keywords such as "sound_design"); they then work with devices.* and the
  automation commands like any exposed parameter.
* ``plugin_racks.presets`` — preset files of a plug-in on disk (``.vstpreset``,
  Live presets holding it, Serum 2 ``.SerumPreset``) to pick a starting sound.

Measured on Live 12.4.5 (macOS, Serum 2 v2.1.5 VST3 + AU, 2026-09-10):

* a rack preset lists at most 128 plug-in parameters — **256 crashed Live**, so
  the limit is enforced before anything is written;
* rack macros are wired in the preset (``MacroControlIndex`` + a
  ``<MidiControllerRange>`` slot; an empty slot crashed Live).  Live keeps the
  range in the plug-in's value units and clamps it, so the generated range
  -1e9..1e9 makes macro/127 == the parameter's normalized value;
* Live indexes a new file after ~3 s; a file it has indexed can be rewritten and
  loaded again at once (Live reads it on load) — probing reuses one file;
* the plug-in state inside the preset is the VST3 ``Comp``/``Cont`` chunk pair
  (== a ``.vstpreset``); without one the plug-in starts from its default state.
  Serum 2 refuses ``.SerumPreset`` data as a state;
* Audio Units load in a generated rack (``<AuPreset>``) but Live exposed none of
  the listed ids (Serum 2 AU) — exposing is VST3-only;
* the plug-in's current sound cannot be read through the API, so replacing a
  device starts from ``preset_file`` or the map's template state (Serum 2: its
  "- Init -" patch).
"""

import os
import re
import time

from .. import compat
from .. import plugin_racks_lib as lib
from .. import resolve
from ..registry import BridgeError, command
from . import browser as browser_handlers
from .devices import display_of, iter_devices, num, parameters_of, prune, _name
from .plugins import match_names

#: Name of the temporary track the map probes load on.
MAP_TRACK = "LB_MAP"
#: Rack file probing rewrites (one per plug-in).
PROBE_FILE = "LB_probe_%s.adg"
#: Rack file an exposure writes (one per plug-in, rewritten each time).
EXPOSE_FILE = "LB %s.adg"
_MAX_BUDGET = 60.0
_JOBS = {}


# ==========================================================================
# helpers
# ==========================================================================

def _library(ctx):
    version = _live_version(ctx)
    path, source = lib.user_library(live_version=None if version == "?" else version)
    if not path:
        raise BridgeError("unsupported", "cannot find Live's User Library (not next to the "
                          "Remote Script, no Library.cfg) — set LIVEBRIDGE_USER_LIBRARY")
    return path, source


def _check_format(plugin_format):
    if plugin_format is None:
        return None
    text = str(plugin_format).strip().upper()
    if text in ("VST3", "VST 3"):
        return "VST3"
    if text in ("AU", "AUV2", "AUDIO UNIT", "AUDIOUNIT"):
        return "AU"
    if text in ("VST", "VST2"):
        return "VST2"
    raise BridgeError("bad_args", "plugin_format must be 'VST3', 'AU' or 'VST2'")


def _installed(refresh=False):
    return lib.installed_plugins(refresh=refresh)


def _find_identity(name, fmt=None, kind=None):
    try:
        return lib.find_plugin(name, _installed(), fmt=fmt, kind=kind)
    except LookupError as error:
        raise BridgeError("not_found", "%s — installed: %s" % (error, ", ".join(
            sorted(set("%s (%s)" % (p.get("name"), p.get("format")) for p in _installed()))[:40])))


def _device_identity(device, fmt=None):
    """Identity of a loaded plug-in device (by its plug-in name and wrapper class)."""
    name = compat.safe_getattr(device, "class_display_name") or _name(device)
    if compat.safe_getattr(device, "class_name") == "AuPluginDevice":
        fmt = fmt or "AU"
    kind = {1: "instrument", 2: "audio_effect", 4: "midi_effect"}.get(
        int(compat.safe_getattr(device, "type", 0) or 0))
    try:
        return lib.find_plugin(name, _installed(), fmt=fmt or None, kind=kind)
    except LookupError:
        if fmt is None:
            try:
                return lib.find_plugin(name, _installed(), kind=kind)
            except LookupError:
                pass
    raise BridgeError("not_found", "cannot identify the plug-in %r on disk (no moduleinfo.json / "
                      "Live plug-in database entry) — pass plugin=<name>" % name)


def _require_vst3(identity):
    if identity.get("format") == "VST3":
        return
    if identity.get("format") == "AU":
        raise BridgeError("unsupported", "%s (Audio Unit): Live loads a generated rack with the AU "
                          "but exposes none of the listed parameters (tested with Serum 2 AU) — "
                          "use the VST3 version (plugin_format='VST3')" % identity.get("name"))
    raise BridgeError("unsupported", "%s (%s): generated racks support VST3 plug-ins only"
                      % (identity.get("name"), identity.get("format")))


def _map_paths(identity, library):
    key = lib.map_key(identity)
    return (os.path.join(lib.maps_dir(library), key + ".json"),
            os.path.join(lib.shipped_maps_dir(), key + ".json"))


def load_param_map(identity, library):
    """``(map dict, source)`` — the User Library cache first, then the shipped map."""
    cached, shipped_path = _map_paths(identity, library)
    found = []
    for path, source in ((cached, "cache"), (shipped_path, "shipped")):
        data = lib.load_map(path)
        if data and (not data.get("class_id") or not identity.get("class_id")
                     or data["class_id"].upper() == identity["class_id"].upper()):
            data.setdefault("path", path)
            found.append((data, source))
    if not found:
        return None, None
    data, source = found[0]
    if len(found) > 1:   # a probed cache keeps the shipped groups / template / folders
        for key in ("groups", "template_state", "preset_folders", "notes"):
            if key not in data and key in found[1][0]:
                data[key] = found[1][0][key]
    return data, source


def _rack_item(ctx, file_name):
    """The browser item of ``<User Library>/LiveBridge/Racks/<file_name>`` or None
    (not indexed yet)."""
    root = compat.safe_getattr(ctx.browser, "user_library")
    for folder_name in ("LiveBridge", "Racks"):
        found = None
        for child in browser_handlers.children_of(root):
            if compat.safe_getattr(child, "name") == folder_name:
                found = child
                break
        if found is None:
            return None
        root = found
    for child in browser_handlers.children_of(root):
        if compat.safe_getattr(child, "name") == file_name:
            return child
    return None


def _plugin_in_rack(rack):
    for chain in compat.safe_getattr(rack, "chains", ()) or ():
        for device in compat.safe_getattr(chain, "devices", ()) or ():
            if compat.is_plugin_device(device):
                return device
    return None


def _same(a, b):
    try:
        return a is b or a == b
    except Exception:
        return False


def _find_track_plugin(track, name=None):
    """``(plugin device, top-level device holding it)`` — the first plug-in on the
    track (matching ``name`` when given), or ``(None, None)``."""
    for device in compat.safe_getattr(track, "devices", ()) or ():
        candidates = [device] if compat.is_plugin_device(device) else []
        if not candidates and compat.safe_getattr(device, "can_have_chains", False):
            for chain in compat.safe_getattr(device, "chains", ()) or ():
                for inner, _path, _depth in iter_devices(chain, "chain"):
                    if compat.is_plugin_device(inner):
                        candidates.append(inner)
        for plugin in candidates:
            label = compat.safe_getattr(plugin, "class_display_name") or _name(plugin)
            wanted = None if name is None else lib.norm(str(name).rsplit("/", 1)[-1])
            if wanted is None or wanted in (lib.norm(label), lib.norm(_name(plugin))):
                return plugin, device
    return None, None


def _is_single_plugin_rack(rack, plugin):
    chains = list(compat.safe_getattr(rack, "chains", ()) or ())
    if len(chains) != 1:
        return False
    devices = list(compat.safe_getattr(chains[0], "devices", ()) or ())
    return len(devices) == 1 and _same(devices[0], plugin)


def _device_path(ctx, device):
    try:
        return ctx.path_of(device)
    except Exception:
        return None


def _select_device(ctx, track, device):
    view = compat.safe_getattr(ctx.song, "view")
    try:
        view.select_device(device)
        return True
    except Exception:
        pass
    try:
        track.view.selected_device = device
        return True
    except Exception:
        return False


def _load_rack(ctx, item, file_name, track, replace_index=None):
    """Load a rack file onto ``track``; with ``replace_index`` the top-level device
    there is deleted first and the rack lands at its position.

    Hot-swapping is deliberately not used: Live 12.4.5 then keeps the old plug-in
    instance ("preset transfer") and neither its Configure list nor the new state
    are reliably replaced (measured with Serum 2)."""
    browser = ctx.browser
    entry = browser_handlers.Entry(item, "user_library/LiveBridge/Racks/" + file_name,
                                   "user_library", 3)
    if compat.safe_getattr(browser, "hotswap_target") is not None:
        browser_handlers.set_hotswap_target(browser, None)
    position = "end"
    if replace_index is not None:
        try:
            track.delete_device(replace_index)
        except Exception as error:
            raise BridgeError("invalid_state", "cannot remove the old device: %s" % error)
        remaining = list(compat.safe_getattr(track, "devices", ()) or ())
        if replace_index < len(remaining) and _select_device(ctx, track, remaining[replace_index]):
            position = "before_selected"
    before = list(compat.safe_getattr(track, "devices", ()) or ())
    browser_handlers.load_entry(ctx, browser, entry, track, position=position)
    after = list(compat.safe_getattr(track, "devices", ()) or ())
    fresh = [d for d in after if not any(_same(d, old) for old in before)]
    stem = os.path.splitext(file_name)[0]
    for device in fresh + after:
        if compat.safe_getattr(device, "can_have_chains", False) and _plugin_in_rack(device):
            if any(_same(device, f) for f in fresh) or _name(device) == stem:
                return device
    raise BridgeError("invalid_state", "Live did not load %s onto %r (check Live's Log.txt for "
                      "'corrupt')" % (file_name, _name(track)))


def _write_and_find(ctx, identity, library, file_name, xml):
    path = os.path.join(lib.racks_dir(library), file_name)
    try:
        lib.write_rack(path, xml)
    except OSError as error:
        raise BridgeError("invalid_state", "cannot write %s: %s" % (path, error))
    return path, _rack_item(ctx, file_name)


def _file_safe(text):
    """A file-name-safe version of a plug-in name (Windows forbids <>:"/\\|?*)."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(text)).strip(" .") or "plugin"


def _names_of(plugin):
    return [_name(p) for p in parameters_of(plugin)[1:]]


# ==========================================================================
# plugin_racks.map
# ==========================================================================

class _MapJob(object):
    def __init__(self, identity, library, gap):
        self.identity = identity
        self.library = library
        self.prober = lib.Prober(gap=gap)
        self.expected = None
        self.pending = None
        self.loads = 0
        self.started = time.time()
        self.file_name = PROBE_FILE % lib.map_key(identity)


def _map_track(ctx, identity):
    for track in compat.safe_getattr(ctx.song, "tracks", ()) or ():
        if _name(track) == MAP_TRACK:
            return track
    kind = "audio" if identity.get("kind") == "audio_effect" else "midi"
    return browser_handlers._create_track(ctx, kind, MAP_TRACK)


def _delete_map_track(ctx):
    tracks = list(compat.safe_getattr(ctx.song, "tracks", ()) or ())
    for index in range(len(tracks) - 1, -1, -1):
        if _name(tracks[index]) == MAP_TRACK:
            try:
                ctx.song.delete_track(index)
            except Exception as error:
                raise BridgeError("invalid_state", "cannot delete the %s track: %s"
                                  % (MAP_TRACK, error))


def _clear_devices(track):
    for index in range(len(list(compat.safe_getattr(track, "devices", ()) or ())) - 1, -1, -1):
        track.delete_device(index)


def _map_summary(data, source, filter=None, offset=0, limit=50):
    names = [pair[0] for pair in data.get("parameters", [])]
    ids = dict((pair[0], pair[1]) for pair in data.get("parameters", []))
    if filter:
        indices = match_names(names, filter)[0] or []
        lowered = filter.strip().lower()
        indices = sorted(set(indices).union(i for i, n in enumerate(names)
                                            if lowered in n.lower()))
    else:
        indices = list(range(len(names)))
    window = indices[offset:offset + limit]
    result = {"status": "done", "plugin": data.get("plugin"), "format": data.get("format"),
              "source": source, "path": data.get("path"), "count": len(names),
              "plugin_parameter_count": data.get("plugin_parameter_count"),
              "missing": data.get("missing") or [],
              "groups": dict((g, len(v)) for g, v in sorted((data.get("groups") or {}).items())),
              "matched": len(indices), "offset": offset, "returned": len(window),
              "parameters": [{"name": names[i], "id": ids[names[i]]} for i in window]}
    if offset + limit < len(indices):
        result["next_offset"] = offset + limit
    return prune(result)


def _finish_job(ctx, job):
    _delete_map_track(ctx)
    found = job.prober.parameters()
    by_name = {}
    for name, pid in found:
        by_name.setdefault(name, pid)
    ordered, seen = [], set()
    for name in job.expected or ():
        if name in by_name and name not in seen:
            ordered.append([name, by_name[name]])
            seen.add(name)
    for name, pid in found:
        if name not in seen:
            ordered.append([name, pid])
            seen.add(name)
    missing = lib.missing_names(job.expected, [n for n, _p in found])
    identity = job.identity
    data = {"plugin": identity.get("name"), "vendor": identity.get("vendor"),
            "format": identity.get("format"), "class_id": identity.get("class_id"),
            "plugin_version": identity.get("version"),
            "source": "probed by plugin_racks.map on Live %s" % _live_version(ctx),
            "created": time.strftime("%Y-%m-%d %H:%M:%S"), "count": len(ordered),
            "plugin_parameter_count": len(job.expected) if job.expected is not None else None,
            "missing": missing, "probed": len(job.prober.probed), "loads": job.loads,
            "seconds": round(time.time() - job.started, 1), "parameters": ordered}
    cached, shipped_path = _map_paths(identity, job.library)
    shipped = lib.load_map(shipped_path)
    if shipped:   # keep the curated groups / template state / preset folders
        for key in ("groups", "template_state", "preset_folders", "notes"):
            if key in shipped:
                data[key] = shipped[key]
    lib.save_map(cached, data)
    data["path"] = cached
    return data


def _live_version(ctx):
    try:
        return "%d.%d.%d" % tuple(ctx.version[:3])
    except Exception:
        return "?"


@command("plugin_racks.map", mutating=True,
         doc="Find (or read the cached) name -> ParameterId map of a plug-in by probing racks")
def plugin_racks_map(ctx, plugin=None, track=None, device=None, plugin_format=None,
                     refresh=False, max_seconds=5.0, gap=16, cancel=False, filter=None,
                     offset=0, limit=50):
    """A plug-in's name -> VST3 ParameterId map (what ``plugin_racks.expose`` needs).

    Returns the cached map (User Library ``LiveBridge/Maps``) or the shipped one
    (Serum 2) at once.  Otherwise — or with ``refresh`` — it probes: each call
    loads probe racks of 128 candidate ids on a temporary "LB_MAP" track for up
    to ``max_seconds`` and reports ``status: "running"``; call again with the
    same arguments until ``status`` is ``"done"`` (the MCP tool
    live_plugin_param_map loops for you).  The finished map is cross-checked
    against the plug-in's ``get_parameter_names()`` (``missing``; VST3 MIDI
    proxies "CCn Chan m" are skipped), saved, and "LB_MAP" is deleted.

    Args:
        plugin: plug-in name ("Serum 2", "Xfer Records/Serum 2"), else the
            plug-in on ``track`` / ``device``.
        track, device: a loaded plug-in to identify (devices.py addressing).
        plugin_format: "VST3" (default preference), "AU", "VST2".
        refresh: probe again even when a map exists.
        max_seconds: work per call (0.5..60).
        gap: unknown ids in a row that end a region (4..128).
        cancel: stop a running probe and delete "LB_MAP".
        filter, offset, limit: page through the finished map's names.

    Returns:
        running: {status: "running"|"indexing", plugin, found, probed, loads,
         batches, seconds}
        done: {status: "done", plugin, format, source ("cache"|"shipped"|
         "probed"), path, count, plugin_parameter_count?, missing: [names not
         found], groups: {keyword: size}, matched, offset, returned,
         parameters: [{name, id}], next_offset?}

    Gotchas:
        Probing loads the plug-in ~20-40 times (Serum 2: 2939 ids, 27 loads,
        ~25 s) and briefly holds one instance on "LB_MAP".  VST3 only.
        Plug-ins whose ParameterIds are hashes (some JUCE builds) cannot be
        found by probing — ``count`` stays 0.
    """
    fmt = _check_format(plugin_format)
    if isinstance(max_seconds, bool) or not isinstance(max_seconds, (int, float)) \
            or not 0.5 <= float(max_seconds) <= _MAX_BUDGET:
        raise BridgeError("bad_args", "max_seconds must be 0.5..%g" % _MAX_BUDGET)
    if isinstance(gap, bool) or not isinstance(gap, int) or not 4 <= gap <= 128:
        raise BridgeError("bad_args", "gap must be an integer 4..128")
    offset, limit = resolve.check_paging(offset, limit, maximum=1000)
    if filter is not None and not isinstance(filter, str):
        raise BridgeError("bad_args", "filter must be a string")
    library, _source = _library(ctx)
    if plugin is not None:
        if not isinstance(plugin, str) or not plugin.strip():
            raise BridgeError("bad_args", "plugin must be a plug-in name")
        identity = _find_identity(plugin, fmt)
    else:
        if track is None and device is None:
            raise BridgeError("bad_args", "pass plugin (a name) or the track/device of a loaded "
                              "plug-in")
        from .plugins import resolve_plugin
        dev, _path = resolve_plugin(ctx, track, device)
        identity = _device_identity(dev, fmt)
    key = lib.map_key(identity)
    if cancel:
        _JOBS.pop(key, None)
        _delete_map_track(ctx)
        return {"status": "cancelled", "plugin": identity.get("name")}
    job = _JOBS.get(key)
    if job is None and not refresh:
        data, source = load_param_map(identity, library)
        if data:
            return _map_summary(data, source, filter, offset, limit)
    _require_vst3(identity)
    if job is None:
        job = _JOBS[key] = _MapJob(identity, library, gap)
    deadline = time.time() + float(max_seconds)
    first = True
    try:
        while first or time.time() < deadline:      # at least one batch per call
            first = False
            ids = job.pending or job.prober.next_ids()
            if not ids:
                data = _finish_job(ctx, job)
                _JOBS.pop(key, None)
                return _map_summary(data, "probed", filter, offset, limit)
            job.pending = ids
            xml = lib.rack_xml(identity, [{"id": pid} for pid in ids])
            _path, item = _write_and_find(ctx, identity, library, job.file_name, xml)
            if item is None:
                return {"status": "indexing", "plugin": identity.get("name"),
                        "note": "Live is indexing %s — call again in a second" % job.file_name}
            map_track = _map_track(ctx, identity)
            _clear_devices(map_track)
            rack = _load_rack(ctx, item, job.file_name, map_track)
            plugin_device = _plugin_in_rack(rack)
            job.loads += 1
            if job.expected is None:
                getter = compat.safe_getattr(plugin_device, "get_parameter_names")
                try:
                    job.expected = [str(n) for n in getter()] if callable(getter) else []
                except Exception:
                    job.expected = []
            names = _names_of(plugin_device)
            if len(names) != len(ids):
                raise BridgeError("invalid_state", "the probe rack exposed %d of %d ids — Live "
                                  "may have changed how it loads plug-in presets"
                                  % (len(names), len(ids)))
            job.prober.record(ids, names)
            job.pending = None
            _clear_devices(map_track)
    except BridgeError:
        _JOBS.pop(key, None)
        raise
    except Exception as error:
        _JOBS.pop(key, None)
        raise BridgeError("internal", "probing failed: %s: %s" % (type(error).__name__, error))
    return {"status": "running", "plugin": identity.get("name"),
            "found": len(job.prober.found), "probed": len(job.prober.probed),
            "loads": job.loads, "batches": job.prober.batches,
            "seconds": round(time.time() - job.started, 1)}


# ==========================================================================
# plugin_racks.expose
# ==========================================================================

def _group(data, keyword):
    """Names of a group keyword ("sound_design", "osc a", "filter") from the map,
    else a generic word match over the names; None when ``keyword`` is no group."""
    groups = data.get("groups") or {}
    key = " ".join(str(keyword).lower().replace("_", " ").replace("-", " ").split())
    for name, members in groups.items():
        if " ".join(name.lower().replace("_", " ").split()) == key:
            return list(members)
    return None


def _select(data, parameters, macros):
    """``(selection [(name, id)], problems [...], macro map {macro: name})``."""
    names = [pair[0] for pair in data.get("parameters", [])]
    ids = dict((pair[0], pair[1]) for pair in data.get("parameters", []))
    wanted = []
    problems = []
    for query in parameters:
        members = _group(data, query)
        if members is not None:
            wanted.extend(m for m in members if m in ids)
            continue
        try:
            hits, _how = match_names(names, query)
        except BridgeError as error:
            problems.append({"query": query, "error": error.message})
            continue
        text = " ".join(query.lower().split())
        exact = [i for i in hits if names[i].lower() == text]
        family = [n for n in names if n.lower().startswith(text + " ")]
        if exact:
            wanted.append(names[exact[0]])
        elif len(family) > 1:
            wanted.extend(family)        # "filter 1" -> every "Filter 1 ..." parameter
        elif len(hits) == 1:
            wanted.append(names[hits[0]])
        elif not hits:
            problems.append({"query": query, "error": "no parameter or group like this",
                             "groups": sorted(data.get("groups") or {})})
        else:
            problems.append({"query": query, "error": "ambiguous",
                             "candidates": [names[i] for i in hits[:12]]})
    macro_map = {}
    for key, query in sorted((macros or {}).items(), key=lambda kv: int(kv[0])):
        hits, _how = match_names(names, query)
        if len(hits) != 1:
            problems.append({"query": query, "macro": int(key),
                             "error": "ambiguous" if hits else "no such parameter",
                             "candidates": [names[i] for i in hits[:12]]})
            continue
        macro_map[int(key)] = names[hits[0]]
        wanted.append(names[hits[0]])
    selection, seen = [], set()
    for name in wanted:
        if name not in seen:
            seen.add(name)
            selection.append((name, ids[name]))
    return selection, problems, macro_map


def _check_macros(macros):
    if macros is None:
        return None
    if not isinstance(macros, dict) or not macros:
        raise BridgeError("bad_args", "macros must be an object {macro number 1..16: parameter}")
    out = {}
    for key, value in macros.items():
        try:
            number = int(key)
        except (TypeError, ValueError):
            raise BridgeError("bad_args", "macro keys must be numbers 1..16, got %r" % (key,))
        if not 1 <= number <= lib.MAX_MACROS or not isinstance(value, str) or not value.strip():
            raise BridgeError("bad_args", "macros: {1..16: parameter name}")
        out[number] = value.strip()
    return out


def _state_for(identity, data, preset_file):
    if preset_file:
        path = os.path.expanduser(str(preset_file))
        if not os.path.isfile(path):
            raise BridgeError("not_found", "preset_file %s does not exist" % path)
        try:
            state = lib.load_state(path, identity)
        except (ValueError, OSError, EOFError) as error:
            raise BridgeError("bad_args", "preset_file: %s" % error)
        state["source"] = "preset_file"
        state["file"] = path
        return state
    template = (data or {}).get("template_state") or {}
    if template.get("processor"):
        return {"processor": bytes.fromhex(template["processor"]),
                "controller": bytes.fromhex(template.get("controller") or "") or None,
                "source": "template", "preset_name": template.get("name")}
    return {"source": "plugin_default"}


def _sync_macros(ctx, rack, plugin_device, macro_map):
    """Report the wired macros and move each one to its parameter's current value
    (Live does not push macro values on load, so a macro would otherwise show 0
    while the parameter keeps the preset's value)."""
    by_name = {}
    for index, param in enumerate(parameters_of(plugin_device)):
        by_name.setdefault(_name(param), (index, param))
    rack_params = parameters_of(rack)
    rack_path = _device_path(ctx, rack)
    out = {}
    for number, pname in sorted(macro_map.items()):
        index, param = by_name.get(pname, (None, None))
        entry = {"parameter": pname, "index": index}
        if number < len(rack_params):
            macro = rack_params[number]
            entry["macro"] = _name(macro)
            if rack_path:
                entry["path"] = "%s.parameters[%d]" % (rack_path, number)
            value = compat.safe_getattr(param, "value")
            if value is not None:
                try:
                    macro.value = max(0.0, min(127.0, float(value) * 127.0))
                    entry["value"] = num(macro.value, 3)
                except Exception as error:
                    entry["error"] = "could not move the macro: %s" % error
        out[str(number)] = prune(entry)
    return out


@command("plugin_racks.expose", mutating=True,
         doc="Load a plug-in in a generated rack that exposes up to 128 chosen parameters")
def plugin_racks_expose(ctx, track=None, plugin=None, parameters=None, macros=None,
                        preset_file=None, replace=True, name=None, new_track=False,
                        track_name=None, plugin_format=None, editor_open=None):
    """Expose a big plug-in's parameters (Serum 2: any of its 541) to Live.

    Writes an Instrument Rack preset (effects: Audio Effect Rack) holding the
    plug-in with the chosen parameters in its Configure list and loads it: over
    the plug-in on the track (``replace``) or as a new device.  The exposed
    parameters then work with devices.get/set_parameter(s) (display strings
    such as "800 Hz", "20 ms"), devices.parameters and the clip automation
    commands — address the plug-in by the returned ``device`` path or its name.

    Args:
        track: target track (index, name, path); default the selected track.
            With ``new_track`` a new track is created instead.
        plugin: plug-in name to load ("Serum 2"); default the plug-in already
            on the track.
        parameters: names (fuzzy: "filter 1 cutoff", "env 1 attack") and/or group
            keywords from the map — Serum 2: "sound_design" (120 curated sound-design
            controls), "osc", "osc a|b|c", "sub", "noise", "filter", "filter 1|2", "env",
            "lfo", "macros", "fx", "unison", "routing", "mod", "arp", "global".
            Any other words that start several names expose all of them
            ("filter 1" -> every "Filter 1 ..." parameter; works for any map).
            Default: "sound_design" when the map has it.  At most 128 in total.
        macros: {1..16: parameter} — wire rack macro n to that parameter over its
            full range (macro 0..127 = parameter 0..1; the macro is named after it
            and added to the exposure).  Move them with devices.set_parameter on
            the rack (``macros[n].path``) or automate them.
        preset_file: a .vstpreset, or a Live preset (.adv/.adg) holding the
            plug-in, whose sound is embedded (list them with plugin_racks.presets).
        replace: replace the plug-in already on the track (at its position; a
            LiveBridge rack around it is replaced as a whole).
        name: the rack's name (default "<plug-in> Rack").
        new_track: create a MIDI (effects: audio) track for the rack.
        track_name: name of the new track.
        plugin_format: "VST3" (default) — Audio Units cannot be exposed.
        editor_open: true / false opens / closes the plug-in window after loading
            (Live opens it when "Auto-Open Plug-In Windows" is on); null leaves it.

    Returns:
        {status: "done", track, rack: path, device: plug-in path, plugin, format,
         exposed_count, parameters: [{index, name, value, display}], missing?:
         [requested names Live did not expose], problems?: [...], state:
         "preset_file"|"template"|"plugin_default", state_note, replaced?, editor_open,
         macros?: {n: {parameter, index, macro, path, value}}, file}
        or {status: "indexing", file, note} — Live has not indexed the new rack file
        yet; call again with the same arguments (the MCP tool waits for you).

    Gotchas:
        The plug-in's current sound cannot be read through Live's API: without
        ``preset_file`` the replaced plug-in restarts from the map's template state
        (Serum 2: "- Init -") — ``state_note`` says so.  The rack file
        ``<User Library>/LiveBridge/Racks/LB <plug-in>.adg`` is rewritten by every
        exposure.  One undo step.
    """
    fmt = _check_format(plugin_format)
    macros = _check_macros(macros)
    if parameters is None:
        parameters = []
    elif isinstance(parameters, str):
        parameters = [parameters]
    if not isinstance(parameters, (list, tuple)) or not all(
            isinstance(p, str) and p.strip() for p in parameters):
        raise BridgeError("bad_args", "parameters must be a list of names / group keywords")
    if name is not None and (not isinstance(name, str) or not name.strip()):
        raise BridgeError("bad_args", "name must be a non-empty string")
    if not isinstance(replace, bool) or not isinstance(new_track, bool):
        raise BridgeError("bad_args", "replace and new_track must be booleans")
    if editor_open is not None and not isinstance(editor_open, bool):
        raise BridgeError("bad_args", "editor_open must be a boolean or null")
    if new_track and track is not None:
        raise BridgeError("bad_args", "pass either track or new_track, not both")
    library, _source = _library(ctx)

    target_track = None
    existing, holder = None, None
    if not new_track:
        target_track = ctx.track(track) if track is not None else \
            compat.safe_getattr(ctx.view, "selected_track")
        if target_track is None:
            raise BridgeError("bad_args", "no track selected — pass track or new_track=true")
        existing, holder = _find_track_plugin(target_track, plugin)
    if plugin is not None:
        if not isinstance(plugin, str) or not plugin.strip():
            raise BridgeError("bad_args", "plugin must be a plug-in name")
        identity = _find_identity(plugin, fmt)
    elif existing is not None:
        identity = _device_identity(existing, fmt)
    else:
        raise BridgeError("bad_args", "no plug-in on %r — pass plugin (e.g. 'Serum 2')"
                          % _name(target_track))
    _require_vst3(identity)
    data, map_source = load_param_map(identity, library)
    if not data:
        raise BridgeError("invalid_state", "no parameter map for %s yet — build it once with "
                          "plugin_racks.map plugin=%r (MCP: live_plugin_param_map)"
                          % (identity.get("name"), identity.get("name")))
    if not parameters:
        if _group(data, "sound_design") is None and not macros:
            raise BridgeError("bad_args", "pass parameters (names or group keywords: %s)"
                              % ", ".join(sorted(data.get("groups") or {})) or "names")
        parameters = ["sound_design"] if _group(data, "sound_design") is not None else []
    selection, problems, macro_map = _select(data, parameters, macros)
    if not selection:
        raise BridgeError("not_found", "none of the requested parameters exist in the map: %s"
                          % problems)
    if len(selection) > lib.MAX_PARAMETERS:
        raise BridgeError("bad_args", "%d parameters requested — Live allows %d per plug-in "
                          "(more crashes Live); pick fewer or smaller groups"
                          % (len(selection), lib.MAX_PARAMETERS))
    state = _state_for(identity, data, preset_file)
    macro_of = dict((pname, number - 1) for number, pname in macro_map.items())
    xml = lib.rack_xml(identity, [{"id": pid, "macro": macro_of.get(pname, -1)}
                                  for pname, pid in selection],
                       macro_names=dict((number - 1, pname)
                                        for number, pname in macro_map.items()),
                       processor=state.get("processor"), controller=state.get("controller"))
    file_name = EXPOSE_FILE % _file_safe(identity.get("name") or "plugin")
    file_path, item = _write_and_find(ctx, identity, library, file_name, xml)
    if item is None:
        return {"status": "indexing", "file": file_path,
                "note": "Live is indexing the new rack file — call again in a second"}

    if new_track:
        kind = "audio" if identity.get("kind") == "audio_effect" else "midi"
        target_track = browser_handlers._create_track(
            ctx, kind, track_name or ("%s" % (identity.get("name") or "Plug-in")))
    replace_index = None
    replaced = None
    if replace and existing is not None:
        target = holder if (holder is not None and not _same(holder, existing)
                            and _is_single_plugin_rack(holder, existing)) else existing
        top = list(compat.safe_getattr(target_track, "devices", ()) or ())
        positions = [i for i, d in enumerate(top) if _same(d, target)]
        if not positions:
            raise BridgeError("bad_args", "%r sits inside a rack with other devices — expose it "
                              "with replace=false (a new rack is added) or new_track=true"
                              % _name(existing))
        replace_index = positions[0]
        replaced = _name(target)
    rack = _load_rack(ctx, item, file_name, target_track, replace_index)
    rack_name = name.strip() if name else "%s Rack" % (identity.get("name") or "Plug-in")
    try:
        rack.name = rack_name
    except Exception:
        pass
    plugin_device = _plugin_in_rack(rack)
    if editor_open is not None and compat.has(plugin_device, "is_editor_open"):
        try:
            plugin_device.is_editor_open = editor_open
        except Exception:
            pass
    params = parameters_of(plugin_device)
    exposed = []
    for index, param in enumerate(params):
        if index == 0:
            continue
        exposed.append(prune({"index": index, "name": _name(param),
                              "value": num(compat.safe_getattr(param, "value")),
                              "display": display_of(param)}))
    exposed_names = set(p["name"] for p in exposed)
    result = {"status": "done", "track": _name(target_track),
              "rack": _device_path(ctx, rack), "rack_name": _name(rack),
              "device": _device_path(ctx, plugin_device), "plugin": identity.get("name"),
              "format": identity.get("format"), "map": map_source,
              "exposed_count": len(exposed), "parameters": exposed, "file": file_path,
              "state": state["source"],
              "editor_open": compat.safe_getattr(plugin_device, "is_editor_open")}
    missing = [n for n, _pid in selection if n not in exposed_names]
    if missing:
        result["missing"] = missing
    if problems:
        result["problems"] = problems
    if replaced:
        result["replaced"] = replaced
    if state["source"] == "preset_file":
        result["state_note"] = "sound from %s" % state.get("file")
    elif state["source"] == "template":
        result["state_note"] = ("the plug-in starts from the template state %r — Live's API "
                                "cannot read the previous sound; pass preset_file to keep a "
                                "saved sound" % state.get("preset_name"))
    else:
        result["state_note"] = ("the plug-in starts from its default state — Live's API cannot "
                                "read the previous sound; pass preset_file to load a saved one")
    if macro_map:
        result["macros"] = _sync_macros(ctx, rack, plugin_device, macro_map)
    return prune(result)


# ==========================================================================
# plugin_racks.presets
# ==========================================================================

@command("plugin_racks.presets", doc="Preset files of a plug-in on disk (to pick a starting sound)")
def plugin_racks_presets(ctx, plugin, filter=None, offset=0, limit=50, plugin_format=None,
                         embeddable_only=False):
    """List a plug-in's preset files so Claude can pick a starting sound by name.

    Looks in the plug-in's own folders (Serum 2: ~/Documents/Xfer/Serum 2 Presets,
    /Library/Audio/Presets/Xfer Records/Serum 2 Presets/Presets; Windows:
    %USERPROFILE%\\Documents\\Xfer\\Serum 2 Presets, %PUBLIC%\\Documents\\Xfer\\Serum 2
    Presets), the VST3 preset folders (<Library|Documents>/…/VST3 Presets/<vendor>/<name>),
    ~/Splice/presets and the User Library.

    Args:
        plugin: plug-in name ("Serum 2").
        filter: words that must all occur in the preset's name or folder
            ("bass", "pad warm").
        offset, limit: paging (limit 1..500).
        plugin_format: "VST3" (default) / "AU".
        embeddable_only: only files plugin_racks.expose preset_file can embed.

    Returns:
        {plugin, format, folders: [{path, exists}], total, matched, offset, count,
         presets: [{name, path, kind: "vstpreset"|"live_preset"|"SerumPreset"|
         "aupreset", embeddable, category?}], next_offset?, truncated?, note?}

    Gotchas:
        ``.SerumPreset`` files (Serum 2's own format, all 626 factory presets) are
        listed but cannot be embedded — Serum rejects them as a VST3 state; load
        them in Serum's browser (plugins.set editor_open=true) or save the sound
        as a Live preset / .vstpreset first.
    """
    fmt = _check_format(plugin_format)
    if not isinstance(plugin, str) or not plugin.strip():
        raise BridgeError("bad_args", "plugin must be a plug-in name")
    if filter is not None and not isinstance(filter, str):
        raise BridgeError("bad_args", "filter must be a string")
    offset, limit = resolve.check_paging(offset, limit, maximum=500)
    identity = _find_identity(plugin, fmt)
    library, _source = _library(ctx)
    data, _map_source = load_param_map(identity, library)
    folders = lib.preset_folders(identity, library, (data or {}).get("preset_folders"))
    entries, truncated = lib.list_presets(folders, identity)
    if embeddable_only:
        entries = [e for e in entries if e.get("embeddable")]
    matched = entries
    if filter and filter.strip():
        words = filter.lower().split()
        matched = [e for e in entries
                   if all(w in (e["name"] + " " + e.get("category", "")).lower() for w in words)]
    window = matched[offset:offset + limit]
    result = {"plugin": identity.get("name"), "format": identity.get("format"),
              "folders": [{"path": f, "exists": os.path.isdir(f)} for f in folders],
              "total": len(entries), "matched": len(matched), "offset": offset,
              "count": len(window),
              "presets": [prune({"name": e["name"], "path": e["path"], "kind": e["kind"],
                                 "embeddable": e["embeddable"], "category": e.get("category")})
                          for e in window]}
    if offset + limit < len(matched):
        result["next_offset"] = offset + limit
    if truncated:
        result["truncated"] = True
    if any(e["kind"] == "SerumPreset" for e in window):
        result["note"] = (".SerumPreset files cannot be embedded (Serum 2 rejects them as a "
                          "VST3 state): open Serum's window (plugins.set editor_open=true) and "
                          "load it in Serum's own browser, or use a .vstpreset / Live preset")
    return result
