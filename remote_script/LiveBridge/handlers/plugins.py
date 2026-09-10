"""Third-party plug-ins (VST2 / VST3 / Audio Units): detection, presets,
exposed vs. available parameters, the Configure workflow, the editor window
and a set-wide plug-in report.

What Live 12.4.5 exposes of a plug-in (``Live.PluginDevice.PluginDevice``,
verified on real Live with Xfer Serum 2, Serum 2 FX and Apple's AUs):

* ``class_name`` is ``"PluginDevice"`` for VST2 *and* VST3 and
  ``"AuPluginDevice"`` for Audio Units.  VST2 vs VST3 (and the vendor) come
  from Live's browser: ``browser.plugins`` has one folder per format
  (``AUv2``, ``VST``, ``VST3``) with vendor sub-folders
  (``VST3/Xfer Records/Serum 2``; VST2 plug-ins sit directly under ``VST``).
* ``get_parameter_names()`` lists **every** parameter of the plug-in (Serum 2:
  2623 names: 541 synth parameters + 2082 VST3 MIDI proxies ``CC0 Chan 1`` ...), not only
  the exposed ones.  "Device On" is not part of it.
* ``parameters`` are only the parameters Live exposes — its **Configure**
  list (+ "Device On").  Live fills that list by itself for small plug-ins
  (AUNBandEQ 41/41, AUMIDISynth 4/4; but AUMatrixReverb only 2/17) and leaves
  it **empty for big ones — Serum 2 VST3 and AU expose 0 of ~2600**, VST3
  included.  There is no API to add parameters to a loaded device, but a
  generated rack preset can list them: ``plugin_racks.expose`` (MCP
  ``live_plugin_expose``) loads a VST3 plug-in in a rack that exposes up to 128
  chosen parameters with no click from the user (Serum 2 / Serum 2 FX maps
  ship with LiveBridge; other VST3s need ``plugin_racks.map`` /
  ``live_plugin_param_map`` once).  Manual fallback (AU / VST2): the user
  clicks the device's Configure button in Live, moves the wanted controls in
  the plug-in window and clicks Configure again (``plugins.exposure`` tells
  which of the wanted parameters are exposed; ``live_plugin_configure`` waits
  for them).  Saving the device as a Live preset (.adv) or the set keeps the
  Configure list.
* ``presets`` / ``selected_preset_index`` — the plug-in's program list; Serum 2
  (VST3 + AU) and Apple's AUs report just ``["Default"]``.
* ``is_editor_open`` (rw) — the plug-in window (Live opens it on load when
  "Auto-Open Plug-In Windows" is on).
* ``latency_in_ms`` / ``latency_in_samples``.

Addressing is the same as in ``devices.py`` (track + device index/name/path).
Parameter values are set with devices.set_parameter(s); ``find_plugin_parameter``
below gives those commands plug-in-aware name matching ("cutoff" -> "Filter 1
Freq", "Frequency #3" for duplicate names, a Configure hint for a parameter the
plug-in has but Live does not expose).
"""

import re

from .. import compat
from ..registry import BridgeError, command
from .devices import (
    check_detail, check_paging, device_brief, display_of, iter_devices, iter_tracks, num,
    parameter_info, param_path, parameters_of, prune, resolve_device, _name,
)

#: Top-level folder names of ``browser.plugins`` -> format (Live 12.4.5: AUv2, VST, VST3).
_FOLDER_FORMATS = (
    ("vst3", "VST3"), ("vst 3", "VST3"), ("audio unit", "AU"), ("auv2", "AU"),
    ("auv3", "AU"), ("au", "AU"), ("vst2", "VST2"), ("vst", "VST2"),
)
#: Sub-folders of the format folders that are not vendors.
_NOT_VENDORS = ("local", "custom", "system", "plug-ins", "plugins")
_BROWSER_BUDGET = 4000
_BROWSER_DEPTH = 4

INSTALL_HINT = ("Live's Plug-Ins browser lists no plug-ins. Plug-ins on disk only appear after "
                "enabling them in Live's Settings -> Plug-Ins ('Use Audio Units v2/v3' on macOS, "
                "'Use VST3 Plug-in System Folders', 'Use VST2 Plug-in System/Custom Folder') "
                "and rescanning; then browser.search(root='plugins') finds them.")

#: The no-click route for VST3 plug-ins (handlers/plugin_racks.py), offered before Configure.
EXPOSE_ALTERNATIVE = ("No user click needed for VST3 plug-ins: plugin_racks.expose (MCP "
                      "live_plugin_expose(plugin=..., parameters=[...], new_track=true)) loads "
                      "the plug-in in a generated rack that exposes up to 128 chosen parameters. "
                      "Serum 2 and Serum 2 FX are fully mapped (parameters=['sound_design']); "
                      "another VST3 needs plugin_racks.map (live_plugin_param_map) once first. "
                      "Configure by hand is the fallback for AU / VST2.")

CONFIGURE_STEPS = (
    "Fallback for AU / VST2 (for VST3 prefer plugin_racks.expose / live_plugin_expose, which "
    "needs no click): in Live, click the Configure button (wrench/'Configure' in the plug-in device's title "
    "bar) — it lights up.",
    "In the plug-in window (open it with the wrench/spanner icon, or plugins.set "
    "editor_open=true), move every control you want Claude to control; each one touched "
    "is added to the device (up to 128).",
    "Click Configure again to leave Configure mode.",
    "To keep the list: save the set, or save the device as a Live preset (device title "
    "bar -> Save Preset) — loading that .adv brings the plug-in state and the parameters "
    "back.",
)

CONFIGURE_HINT = ("Live only exposes the plug-in parameters in the device's Configure list, and "
                  "for big plug-ins (Serum 2 VST3/AU, most synths) that list starts EMPTY. For a "
                  "VST3 plug-in use plugin_racks.expose (MCP live_plugin_expose) — it loads the "
                  "plug-in in a generated rack exposing up to 128 chosen parameters, no user "
                  "click (Serum 2 / Serum 2 FX maps ship with LiveBridge; other VST3s need "
                  "live_plugin_param_map once). Fallback for AU / VST2: ask the user to click "
                  "Configure on the device in Live, move the wanted controls in the plug-in "
                  "window, then click Configure again (live_plugin_configure lists what is "
                  "missing and waits for it).")

PRESETS_NOTE = ("Live reports no program list for this plug-in (only %r). Load sounds in the "
                "plug-in's own preset browser (plugins.set editor_open=true), from Live device "
                "presets (.adv saved with the plug-in's state, found with browser.search), or — "
                "for Splice presets — from ~/Splice/presets.")

#: Words that mean the same thing in plug-in parameter names (Serum, AUs, most synths).
_SYNONYM_GROUPS = (
    ("cutoff", "freq", "frequency", "cut"),
    ("resonance", "res", "reso", "q"),
    ("volume", "vol", "level", "gain", "lvl"),
    ("attack", "atk", "att"),
    ("decay", "dec"),
    ("sustain", "sus"),
    ("release", "rel"),
    ("position", "pos"),
    ("wavetable", "wt", "table"),
    ("envelope", "env", "eg"),
    ("master", "main", "global"),
    ("mix", "wet", "drywet", "blend"),
    ("amount", "amt", "depth"),
    ("semitone", "semitones", "semi", "coarse", "st"),
    ("octave", "oct"),
    ("tuning", "tune"),
    ("panning", "pan"),
    ("feedback", "fb", "fdbk"),
    ("portamento", "porta", "glide"),
    ("unison", "uni"),
    ("detune", "det"),
    ("width", "wid", "stereo"),
    ("modulation", "mod"),
    ("oscillator", "osc"),
    ("delay", "dly"),
    ("reverb", "verb", "rev"),
    ("distortion", "dist"),
    ("bandwidth", "bw"),
    ("lowpass", "lp", "lpf"),
    ("highpass", "hp", "hpf", "hipass"),
    ("drive", "drv"),
)
_SYNONYMS = {}
for _group in _SYNONYM_GROUPS:
    for _word in _group:
        _SYNONYMS.setdefault(_word, set()).update(_group)
#: Query words that carry no meaning in plug-in parameter names ("osc a level" -> "A Level").
_STOPWORDS = frozenset(("osc", "oscillator", "the", "of", "param", "parameter", "knob",
                        "control", "plugin"))
_OCCURRENCE_RE = re.compile(r"^(.*?)\s*#\s*(\d+)$")
_TOKEN_RE = re.compile(r"[0-9a-z]+")


# ==========================================================================
# formats and vendors (browser.plugins)
# ==========================================================================

def _folder_format(name):
    lowered = str(name or "").strip().lower()
    for prefix, fmt in _FOLDER_FORMATS:
        if lowered == prefix or lowered.startswith(prefix + " ") \
                or (" " in prefix and lowered.startswith(prefix)):
            return fmt
    return None


def _uri_format(uri):
    """``query:Plugins#VST3:Xfer%20Records:Serum%202`` -> "VST3" (``#AUv2:`` -> "AU",
    ``#VST:Local:...`` -> "VST2")."""
    text = str(uri or "").lower()
    for marker, fmt in (("vst3", "VST3"), ("#auv2", "AU"), ("#auv3", "AU"), ("#au:", "AU"),
                        (":au:", "AU"), ("audiounit", "AU"), ("vst", "VST2")):
        if marker in text:
            return fmt
    return None


def plugin_format_index(ctx):
    """``({plug-in name (lower): {format: vendor or None}}, complete)`` from
    ``browser.plugins`` — a bounded walk (``_BROWSER_BUDGET`` items,
    ``_BROWSER_DEPTH`` levels)."""
    index = {}
    root = compat.safe_getattr(ctx.browser, "plugins")
    if root is None:
        return index, False
    visited = [0]
    complete = [True]

    def walk(item, depth, fmt, vendor):
        for child in compat.safe_getattr(item, "children", ()) or ():
            visited[0] += 1
            if visited[0] > _BROWSER_BUDGET:
                complete[0] = False
                return
            name = compat.safe_getattr(child, "name", "")
            if compat.safe_getattr(child, "is_folder", False):
                if depth == 0:
                    child_fmt, child_vendor = fmt or _folder_format(name), vendor
                else:
                    child_fmt = fmt
                    child_vendor = vendor
                    if depth == 1 and str(name).strip().lower() not in _NOT_VENDORS:
                        child_vendor = str(name)
                if depth < _BROWSER_DEPTH:
                    walk(child, depth + 1, child_fmt, child_vendor)
                continue
            found = fmt or _uri_format(compat.safe_getattr(child, "uri", ""))
            if found and name:
                formats = index.setdefault(str(name).lower(), {})
                if found not in formats or formats[found] is None:
                    formats[found] = vendor

    walk(root, 0, None, None)
    return index, complete[0]


def plugin_format(device, format_index=None):
    """``(format, source)`` — "AU", "VST3", "VST2", "VST2/VST3" or "unknown"."""
    class_name = compat.safe_getattr(device, "class_name", "")
    if class_name == "AuPluginDevice":
        return "AU", "class_name"
    if class_name != "PluginDevice":
        return "unknown", "class_name"
    if format_index:
        for key in (compat.safe_getattr(device, "class_display_name"), _name(device)):
            formats = format_index.get(str(key or "").lower())
            if formats:
                formats = set(formats) - set(["AU"])
                if len(formats) == 1:
                    return formats.pop(), "browser"
                if formats:
                    return "VST2/VST3", "browser (installed as both)"
    return "VST2/VST3", "class_name"


def plugin_vendor(device, fmt, format_index=None):
    """The vendor folder Live's browser files the plug-in under ("Xfer Records"), or None."""
    if not format_index:
        return None
    for key in (compat.safe_getattr(device, "class_display_name"), _name(device)):
        formats = format_index.get(str(key or "").lower())
        if not formats:
            continue
        if formats.get(fmt):
            return formats[fmt]
        for vendor in formats.values():
            if vendor:
                return vendor
    return None


def resolve_plugin(ctx, track, device):
    dev, path = resolve_device(ctx, track, device)
    if not compat.is_plugin_device(dev):
        raise BridgeError("bad_args", "%r (%s) is not a third-party plug-in — use the devices.* "
                          "commands" % (_name(dev), compat.safe_getattr(dev, "class_name", "?")))
    return dev, path


# ==========================================================================
# exposed vs. available parameters
# ==========================================================================

def _presets(device):
    try:
        return [str(p) for p in (compat.safe_getattr(device, "presets", ()) or ())]
    except Exception:
        return []


def _is_device_on(param, index):
    return index == 0 and str(compat.safe_getattr(param, "original_name", "")
                              or _name(param)) == "Device On"


def exposed_parameters(device):
    """``[(live_index, param)]`` of the plug-in's exposed parameters (no "Device On")."""
    return [(i, p) for i, p in enumerate(parameters_of(device)) if not _is_device_on(p, i)]


def _exposed_count(device):
    """Parameters beyond "Device On" (what the Configure list exposes)."""
    return len(exposed_parameters(device))


def plugin_parameter_names(device):
    """Every parameter name of the plug-in (``get_parameter_names()``), or None when
    Live cannot tell."""
    getter = compat.safe_getattr(device, "get_parameter_names")
    if not callable(getter):
        return None
    try:
        return [str(n) for n in getter()]
    except Exception:
        return None


def exposure_map(device, names=None):
    """``(names, {plugin_index: live_index})`` — which of the plug-in's parameters
    are exposed.  Matched by name in order, so duplicate names (AUNBandEQ's eight
    "Frequency") pair up by occurrence."""
    names = plugin_parameter_names(device) if names is None else names
    if names is None:
        names = [_name(p) for _i, p in exposed_parameters(device)]
    slots = {}
    for plugin_index, name in enumerate(names):
        slots.setdefault(name, []).append(plugin_index)
    mapping = {}
    for live_index, param in exposed_parameters(device):
        for key in (str(compat.safe_getattr(param, "original_name", "") or ""), _name(param)):
            queue = slots.get(key)
            if queue:
                mapping[queue.pop(0)] = live_index
                break
    return names, mapping


def _tokens(text):
    return _TOKEN_RE.findall(str(text).lower().replace("/", " "))


def _token_matches(word, token):
    if len(word) <= 2 or len(token) <= 2:
        return word == token
    return token.startswith(word) or (len(token) >= 3 and word.startswith(token))


def _word_matches(word, tokens):
    options = _SYNONYMS.get(word, (word,))
    return any(_token_matches(option, token) for option in options for token in tokens)


def fuzzy_matches(query, names):
    """Indices of ``names`` matching every meaningful word of ``query`` (synonyms and
    prefixes allowed: "filter 1 cutoff" -> "Filter 1 Freq", "osc a level" -> "A Level"),
    best (fewest extra words) first — ``[]`` when nothing fits."""
    words = [w for w in _tokens(query) if w not in _STOPWORDS] or _tokens(query)
    if not words:
        return []
    scored = []
    for index, name in enumerate(names):
        tokens = _tokens(name)
        if tokens and all(_word_matches(word, tokens) for word in words):
            scored.append((len(tokens) - len(words), index))
    scored.sort()
    return [index for _extra, index in scored]


def match_names(names, text):
    """Resolve ``text`` against a list of parameter names.

    Returns ``(indices, how)``: one index for a unique hit, several when the
    name is ambiguous, none when nothing matches.  Tiers: ``"Name #n"`` (n-th
    of duplicate names), exact, case-insensitive, prefix, substring, then the
    synonym/word match of :func:`fuzzy_matches`.
    """
    text = str(text).strip()
    occurrence = _OCCURRENCE_RE.match(text)
    if occurrence and occurrence.group(1):
        nth = int(occurrence.group(2))
        base_hits, _how = match_names(names, occurrence.group(1))
        distinct = set(names[i].lower() for i in base_hits)
        if len(distinct) == 1:      # "Frequency #3", "bw #2" -> the n-th "Bandwidth"
            key = distinct.pop()
            same = [i for i, n in enumerate(names) if n.lower() == key]
            if 1 <= nth <= len(same):
                return [same[nth - 1]], "occurrence"
            raise BridgeError("not_found", "%r: there %s only %d parameter%s named %r"
                              % (text, "is" if len(same) == 1 else "are", len(same),
                                 "" if len(same) == 1 else "s", names[same[0]]))
    lowered = text.lower()
    for how, hits in (
            ("exact", [i for i, n in enumerate(names) if n == text]),
            ("exact", [i for i, n in enumerate(names) if n.lower() == lowered])):
        if hits:
            return hits, how
    words = fuzzy_matches(text, names)
    for how, hits in (
            ("prefix", [i for i, n in enumerate(names) if n.lower().startswith(lowered)]),
            ("substring", [i for i, n in enumerate(names) if lowered in n.lower()])):
        if hits:
            # "cutoff" is a substring of Serum's "Cutoff Rand" but means "Filter 1/2 Freq":
            # synonym hits that the text match missed make it ambiguous
            return hits + [i for i in words if i not in hits], how
    hits = words
    if len(hits) > 1:
        extra = [len(_tokens(names[i])) for i in hits]
        best = [i for i, e in zip(hits, extra) if e == extra[0]]
        if len(best) == 1:
            return best, "words"
    return hits, "words"


def _describe(names, indices, limit=10):
    return ", ".join("%r" % names[i] for i in indices[:limit]) + \
        (" ..." if len(indices) > limit else "")


def find_plugin_parameter(device, params, text, stage):
    """Plug-in-aware parameter lookup used by ``devices.find_parameter``.

    ``stage="before"`` (ahead of the generic tiers): ``"Name #n"`` and exact
    names that occur several times (raises ``bad_args`` listing the choices).
    ``stage="after"`` (nothing matched generically): synonym/word matching over
    the exposed parameters, then a ``not_found`` that says whether the plug-in
    has the parameter but Live does not expose it (Configure).
    Returns ``(param, index)`` or ``None`` (continue with the generic lookup).
    """
    exposed = exposed_parameters(device)
    names = [_name(p) for _i, p in exposed]
    if stage == "before":
        occurrence = _OCCURRENCE_RE.match(text)
        if occurrence and occurrence.group(1):
            hits, _how = match_names(names, text)
            if len(hits) == 1:
                return exposed[hits[0]][1], exposed[hits[0]][0]
            return None
        same = [i for i, n in enumerate(names) if n.lower() == text.lower()]
        if len(same) > 1:
            raise BridgeError("bad_args", "%r occurs %d times on %r (indices %s) — use "
                              "'%s #2' style names (1-based occurrence) or the index"
                              % (text, len(same), _name(device),
                                 ", ".join(str(exposed[i][0]) for i in same),
                                 names[same[0]]))
        return None
    hits = fuzzy_matches(text, names)
    if hits:
        extra = [len(_tokens(names[i])) for i in hits]
        best = [i for i, e in zip(hits, extra) if e == extra[0]]
        if len(best) == 1:
            return exposed[best[0]][1], exposed[best[0]][0]
        raise BridgeError("bad_args", "parameter %r is ambiguous on %r: %s — use the exact "
                          "name or the index"
                          % (text, _name(device),
                             ", ".join("%d %r" % (exposed[i][0], names[i]) for i in hits[:10])))
    all_names = plugin_parameter_names(device) or []
    if all_names:
        found, _how = match_names(all_names, text)
        total = len(all_names)
        if found:
            raise BridgeError("not_found", "%s %s of %r but Live does not expose %s (%d of %d "
                              "plug-in parameters exposed). %s"
                              % (_describe(all_names, found, 6),
                                 "is a parameter" if len(found) == 1 else "are parameters",
                                 _name(device), "it" if len(found) == 1 else "them",
                                 len(exposed), total, CONFIGURE_HINT))
        raise BridgeError("not_found", "no parameter named %r on %r — neither among the %d "
                          "exposed parameters (%s) nor the plug-in's %d parameters (search them "
                          "with plugins.parameters scope='all' filter=...)"
                          % (text, _name(device), len(exposed),
                             _describe(names, list(range(len(names))), 8) or "none", total))
    return None


# ==========================================================================
# commands
# ==========================================================================

def plugin_entry(device, path, track_name=None, format_index=None):
    get = compat.safe_getattr
    fmt, source = plugin_format(device, format_index)
    presets = _presets(device)
    selected = get(device, "selected_preset_index")
    names = plugin_parameter_names(device)
    return prune({
        "path": path,
        "track": track_name,
        "name": _name(device),
        "plugin": get(device, "class_display_name"),
        "format": fmt,
        "format_source": source if source != "class_name" or fmt == "VST2/VST3" else None,
        "vendor": plugin_vendor(device, fmt, format_index),
        "type": {1: "instrument", 2: "audio_effect", 4: "midi_effect"}.get(
            int(get(device, "type", 0) or 0), "undefined"),
        "is_active": bool(get(device, "is_active", True)),
        "parameter_count": _exposed_count(device),
        "plugin_parameter_count": len(names) if names is not None else None,
        "preset_count": len(presets),
        "selected_preset": presets[selected]
        if isinstance(selected, int) and 0 <= selected < len(presets) else None,
        "latency_ms": num(get(device, "latency_in_ms"), 3) or None,
    })


@command("plugins.list",
         doc="Every third-party plug-in in the set with format, vendor and parameter counts")
def plugins_list(ctx, include_nested=True, resolve_format=True):
    """Report all VST2/VST3/AU plug-ins in the set.

    Args:
        include_nested: also look inside rack chains.
        resolve_format: look plug-ins up in Live's browser to tell VST2 from
            VST3 and find the vendor (bounded walk of ``browser.plugins``) —
            also fills ``installed``.

    Returns:
        {count, formats: {"VST3": n, "AU": n, ...}, needs_configure: [paths of
         plug-ins exposing 0 of their parameters], plugins: [{path, track, name,
         plugin, format, vendor?, type, is_active, parameter_count (exposed),
         plugin_parameter_count (all the plug-in has), preset_count,
         selected_preset?, latency_ms?}],
         installed?: {count, formats: {"VST3": n, ...}, complete, hint?} — what
         Live's Plug-Ins browser offers (loadable with browser.load)}

    Gotchas:
        ``parameter_count`` excludes "Device On" and only counts parameters
        Live exposes (Configure list); big plug-ins such as Serum 2 expose 0 of
        ~2600 until the user configures them.  An ``installed.count`` of 0
        means Live lists no plug-ins at all (plug-in use is off in Live's
        Settings → Plug-Ins).
    """
    format_index, complete = plugin_format_index(ctx) if resolve_format else (None, True)
    plugins = []
    for track_obj, track_path in iter_tracks(ctx):
        for dev, path, _depth in iter_devices(track_obj, track_path, bool(include_nested)):
            if compat.is_plugin_device(dev):
                plugins.append(plugin_entry(dev, path, _name(track_obj), format_index))
    formats = {}
    for entry in plugins:
        formats[entry["format"]] = formats.get(entry["format"], 0) + 1
    result = {
        "count": len(plugins),
        "formats": formats,
        "needs_configure": [p["path"] for p in plugins if not p.get("parameter_count")
                            and p.get("plugin_parameter_count", 1)],
        "plugins": plugins,
    }
    if format_index is not None:
        installed_formats = {}
        for found in format_index.values():
            for fmt in found:
                installed_formats[fmt] = installed_formats.get(fmt, 0) + 1
        installed = {"count": len(format_index), "formats": installed_formats,
                     "complete": complete}
        if not format_index:
            installed["hint"] = INSTALL_HINT
        result["installed"] = installed
    return result


@command("plugins.get",
         doc="One plug-in: format, vendor, presets, exposed vs. available parameters, window")
def plugins_get(ctx, track=None, device=None, resolve_format=True, max_names=128):
    """Describe one third-party plug-in.

    Args:
        track, device: see devices.py addressing (name, index or LOM path).
        resolve_format: tell VST2 from VST3 (and find the vendor) through the browser.
        max_names: how many exposed parameter names to include (0..1024).

    Returns:
        {path, name, plugin, format, format_source?, vendor?, type, is_active,
         parameter_count, parameter_names: [exposed names], plugin_parameter_count,
         not_exposed?: n, preset_count, selected_preset_index, selected_preset?,
         editor_open, latency_ms?, latency_samples, configure_hint?}

    Gotchas:
        ``parameter_names`` are the parameters Live exposes (settable,
        automatable); ``plugin_parameter_count`` is everything the plug-in has
        (search it with plugins.parameters scope="all").  ``configure_hint``
        appears when nothing is exposed yet.
    """
    if isinstance(max_names, bool) or not isinstance(max_names, int) \
            or not 0 <= max_names <= 1024:
        raise BridgeError("bad_args", "max_names must be an integer 0..1024")
    dev, path = resolve_plugin(ctx, track, device)
    format_index = plugin_format_index(ctx)[0] if resolve_format else None
    data = plugin_entry(dev, path, None, format_index)
    get = compat.safe_getattr
    names = [_name(p) for _i, p in exposed_parameters(dev)]
    data["parameter_names"] = names[:max_names]
    if len(names) > max_names:
        data["parameter_names_truncated"] = len(names) - max_names
    total = data.get("plugin_parameter_count")
    if total is not None and total > len(names):
        data["not_exposed"] = total - len(names)
    data["selected_preset_index"] = get(dev, "selected_preset_index")
    data["editor_open"] = get(dev, "is_editor_open")
    data["latency_samples"] = get(dev, "latency_in_samples")
    if not names and total != 0:
        data["configure_hint"] = CONFIGURE_HINT
    return prune(data)


@command("plugins.presets", doc="A plug-in's preset/program list (paged, filterable)")
def plugins_presets(ctx, track=None, device=None, filter=None, offset=0, limit=100):
    """List a plug-in's presets (its program list).

    Args:
        track, device: see devices.py addressing.
        filter: case-insensitive substring of the preset name.
        offset, limit: paging (limit 1..1000).

    Returns:
        {device, total, matched, offset, count, selected_preset_index,
         selected_preset?, presets: [{index, name}], next_offset?, note?}

    Gotchas:
        Serum 2 (VST3 + AU) and Apple's AUs report only ["Default"] — ``note``
        then says where their presets are (the plug-in's own browser, .adv
        Live presets, ~/Splice/presets).
    """
    offset, limit = check_paging(offset, limit)
    if filter is not None and not isinstance(filter, str):
        raise BridgeError("bad_args", "filter must be a string")
    dev, path = resolve_plugin(ctx, track, device)
    presets = _presets(dev)
    indexed = list(enumerate(presets))
    if filter:
        needle = filter.strip().lower()
        indexed = [(i, p) for i, p in indexed if needle in p.lower()]
    selected = compat.safe_getattr(dev, "selected_preset_index")
    result = {
        "device": device_brief(dev, path),
        "total": len(presets),
        "matched": len(indexed),
        "offset": offset,
        "count": len(indexed[offset:offset + limit]),
        "selected_preset_index": selected,
        "presets": [{"index": i, "name": p} for i, p in indexed[offset:offset + limit]],
    }
    if isinstance(selected, int) and 0 <= selected < len(presets):
        result["selected_preset"] = presets[selected]
    if offset + limit < len(indexed):
        result["next_offset"] = offset + limit
    if len(presets) <= 1:
        result["note"] = PRESETS_NOTE % (presets[0] if presets else "nothing")
    return result


def _preset_index(presets, preset):
    if isinstance(preset, bool):
        raise BridgeError("bad_args", "preset must be an index or a name")
    if isinstance(preset, int) or (isinstance(preset, str) and preset.strip().isdigit()
                                   and not any(p == preset for p in presets)):
        index = int(preset)
        if 0 <= index < len(presets):
            return index
        raise BridgeError("not_found", "preset index %d out of range (%d presets)"
                          % (index, len(presets)))
    if not isinstance(preset, str) or not preset.strip():
        raise BridgeError("bad_args", "preset must be an index or a name")
    text = preset.strip()
    lowered = text.lower()
    for tier in ([i for i, p in enumerate(presets) if p == text],
                 [i for i, p in enumerate(presets) if p.lower() == lowered],
                 [i for i, p in enumerate(presets) if p.lower().startswith(lowered)],
                 [i for i, p in enumerate(presets) if lowered in p.lower()]):
        if len(tier) == 1 or (tier and presets[tier[0]].lower() == lowered):
            return tier[0]
        if tier:
            raise BridgeError("bad_args", "preset %r is ambiguous: %s"
                              % (text, ", ".join(repr(presets[i]) for i in tier[:8])))
    raise BridgeError("not_found", "no preset named %r (%d presets; list them with "
                      "plugins.presets)" % (text, len(presets)))


@command("plugins.set", mutating=True, doc="Select a plug-in preset and/or open/close its window")
def plugins_set(ctx, track=None, device=None, preset=None, editor_open=None):
    """Change a plug-in's preset or editor window.

    Args:
        track, device: see devices.py addressing.
        preset: preset index or name (exact, case-insensitive, prefix,
            substring — must be unambiguous).
        editor_open: true opens the plug-in window, false closes it.

    Returns:
        {device, selected_preset_index, selected_preset?, editor_open}

    Gotchas:
        Selecting a preset replaces the plug-in's current (unsaved) state.
        Many plug-ins (Serum 2, Apple AUs) only list "Default".
    """
    dev, path = resolve_plugin(ctx, track, device)
    if preset is None and editor_open is None:
        raise BridgeError("bad_args", "pass preset and/or editor_open")
    if editor_open is not None and not isinstance(editor_open, bool):
        raise BridgeError("bad_args", "editor_open must be a boolean")
    if preset is not None:
        presets = _presets(dev)
        if not presets:
            raise BridgeError("invalid_state", "%r exposes no presets to Live" % _name(dev))
        index = _preset_index(presets, preset)
        try:
            dev.selected_preset_index = index
        except Exception as error:
            raise BridgeError("invalid_state", "Live refused preset %d: %s" % (index, error))
    if editor_open is not None:
        if not compat.has(dev, "is_editor_open"):
            raise BridgeError("unsupported", "this Live cannot open plug-in windows via the API")
        try:
            dev.is_editor_open = editor_open
        except Exception as error:
            raise BridgeError("invalid_state", "cannot change the plug-in window: %s" % error)
    presets = _presets(dev)
    selected = compat.safe_getattr(dev, "selected_preset_index")
    return prune({
        "device": device_brief(dev, path),
        "selected_preset_index": selected,
        "selected_preset": presets[selected]
        if isinstance(selected, int) and 0 <= selected < len(presets) else None,
        "editor_open": compat.safe_getattr(dev, "is_editor_open"),
    })


def _filter_indices(names, needle):
    """Indices of ``names`` matching a filter: substring, else the word/synonym match."""
    lowered = needle.strip().lower()
    hits = set(i for i, n in enumerate(names) if lowered in n.lower())
    return sorted(hits.union(fuzzy_matches(needle, names)))


@command("plugins.parameters",
         doc="A plug-in's exposed (Configure) parameters — or all its parameters — paged")
def plugins_parameters(ctx, track=None, device=None, filter=None, offset=0, limit=64,
                       detail="summary", scope="exposed"):
    """Page through a plug-in's parameters.

    Args:
        track, device: see devices.py addressing.
        filter: parameter name filter — substring, else words with synonyms
            ("cutoff" finds "Filter 1 Freq", "osc a level" finds "A Level").
        offset, limit: paging (limit 1..1000).
        detail: "minimal" | "summary" | "full" (as devices.parameters; exposed scope).
        scope: "exposed" (default) — the parameters Live exposes (values,
            settable, automatable); "all" — every parameter the plug-in has
            (``get_parameter_names``), each with ``exposed`` and, when exposed,
            its Live ``index`` / value / display.

    Returns:
        {device, scope, total, matched, offset, count, parameters: [...],
         next_offset?, plugin_parameter_count, exposed_count, configure_hint?}
        scope "all" items: {plugin_index, name, exposed, index?, value?, display?}

    Gotchas:
        Only exposed parameters can be read/set/automated; to expose more the
        user uses Live's Configure mode (``configure_hint``).  Set values with
        devices.set_parameter / devices.set_parameters (display strings work).
    """
    check_detail(detail)
    offset, limit = check_paging(offset, limit)
    if filter is not None and not isinstance(filter, str):
        raise BridgeError("bad_args", "filter must be a string")
    if scope not in ("exposed", "all"):
        raise BridgeError("bad_args", "scope must be 'exposed' or 'all'")
    dev, path = resolve_plugin(ctx, track, device)
    all_names = plugin_parameter_names(dev)
    exposed = exposed_parameters(dev)
    result = {"device": device_brief(dev, path), "scope": scope}
    if scope == "all":
        if all_names is None:
            raise BridgeError("unsupported", "Live cannot list this plug-in's parameters "
                              "(no get_parameter_names)")
        _names, mapping = exposure_map(dev, all_names)
        indices = list(range(len(all_names)))
        if filter:
            indices = _filter_indices(all_names, filter)
        params = parameters_of(dev)
        items = []
        for plugin_index in indices[offset:offset + limit]:
            live_index = mapping.get(plugin_index)
            item = {"plugin_index": plugin_index, "name": all_names[plugin_index],
                    "exposed": live_index is not None}
            if live_index is not None:
                param = params[live_index]
                item["index"] = live_index
                item["value"] = num(compat.safe_getattr(param, "value"))
                item["display"] = display_of(param)
            items.append(prune(item))
        result.update({"total": len(all_names), "matched": len(indices), "offset": offset,
                       "count": len(items), "parameters": items})
        page_end = len(indices)
    else:
        params = list(enumerate(parameters_of(dev)))
        if filter:
            names = [_name(p) for _i, p in params]
            params = [params[i] for i in _filter_indices(names, filter)]
        result.update({
            "total": len(parameters_of(dev)),
            "matched": len(params),
            "offset": offset,
            "count": len(params[offset:offset + limit]),
            "parameters": [parameter_info(p, i, param_path(path, i), detail)
                           for i, p in params[offset:offset + limit]],
        })
        page_end = len(params)
    if offset + limit < page_end:
        result["next_offset"] = offset + limit
    result["exposed_count"] = len(exposed)
    if all_names is not None:
        result["plugin_parameter_count"] = len(all_names)
    if (not exposed and all_names != []) or (scope == "exposed" and filter
                                               and not result["matched"]):
        result["configure_hint"] = CONFIGURE_HINT
    return result


@command("plugins.exposure",
         doc="Which wanted plug-in parameters Live exposes, and how to expose the rest")
def plugins_exposure(ctx, track=None, device=None, parameters=None):
    """Check wanted plug-in parameters against Live's Configure list.

    Args:
        track, device: see devices.py addressing.
        parameters: list of parameter names (fuzzy: "filter 1 cutoff",
            "env 1 attack", "Macro 1", "Frequency #3") — or omit to only get
            the counts and the steps.

    Returns:
        {device, exposed_count, plugin_parameter_count, all_exposed,
         parameters: [{query, name?, plugin_index?, exposed, index?, display?,
         candidates?, error?}], missing: [names to move in Configure mode],
         alternative?: "<plugin_racks.expose route>", steps?: [...] (when
         something is missing)}

    Gotchas:
        Read-only.  A loaded device's Configure list cannot be changed through
        the API: for VST3 plug-ins use ``plugin_racks.expose`` (``alternative``;
        no user click, Serum 2 fully mapped); for AU / VST2 the user does it in
        Live (``steps``) — poll this (or the MCP tool live_plugin_configure,
        which waits) until ``all_exposed`` is true.
    """
    if parameters is not None:
        if isinstance(parameters, str):
            parameters = [parameters]
        if not isinstance(parameters, (list, tuple)) or not all(
                isinstance(p, str) and p.strip() for p in parameters):
            raise BridgeError("bad_args", "parameters must be a list of parameter names")
        if len(parameters) > 256:
            raise BridgeError("bad_args", "at most 256 parameters per call")
    dev, path = resolve_plugin(ctx, track, device)
    all_names = plugin_parameter_names(dev)
    exposed = exposed_parameters(dev)
    if all_names is None:
        all_names = [_name(p) for _i, p in exposed]
    _names, mapping = exposure_map(dev, all_names)
    params = parameters_of(dev)
    items = []
    missing = []
    for query in parameters or ():
        item = {"query": query}
        try:
            hits, _how = match_names(all_names, query)
        except BridgeError as error:
            item.update({"exposed": False, "error": error.message})
            items.append(item)
            continue
        if not hits:
            item.update({"exposed": False,
                         "error": "the plug-in has no parameter like %r" % query})
        elif len(hits) > 1:
            item.update({"exposed": False, "error": "ambiguous",
                         "candidates": [all_names[i] for i in hits[:12]]})
        else:
            plugin_index = hits[0]
            live_index = mapping.get(plugin_index)
            item.update({"name": all_names[plugin_index], "plugin_index": plugin_index,
                         "exposed": live_index is not None})
            if live_index is not None:
                item["index"] = live_index
                item["display"] = display_of(params[live_index])
            else:
                missing.append(all_names[plugin_index])
        items.append(item)
    result = {
        "device": device_brief(dev, path),
        "exposed_count": len(exposed),
        "plugin_parameter_count": len(all_names),
        "all_exposed": bool(items) and all(i.get("exposed") for i in items),
        "parameters": items,
        "missing": missing,
    }
    if missing or not exposed or not items:
        result["alternative"] = EXPOSE_ALTERNATIVE
        result["steps"] = list(CONFIGURE_STEPS)
    return result
