"""Live's browser: roots, browsing, cached search, loading, hot-swap and preview.

What the Live API offers (``docs/LIVE_API_DUMP_12.4.5.md``, ``Live.Browser``):

* ``app.browser`` has BrowserItem roots ``instruments``, ``sounds``, ``drums``,
  ``audio_effects``, ``midi_effects``, ``plugins``, ``max_for_live``, ``clips``,
  ``samples``, ``packs``, ``user_library``, ``current_project`` and the *list*
  roots ``user_folders`` (Places) and ``colors`` (tags).  Extra roots a future
  Live adds (e.g. a Splice section) are picked up dynamically from ``dir()``.
* A BrowserItem has ``name``, ``uri`` (unique id), ``is_folder``,
  ``is_loadable``, ``is_device``, ``source`` and lazily loaded ``children``.
  Devices (``is_folder`` False) and packs still have children (their preset
  folders), so walks look at every item's children.  Items have no parent
  and no file path.
* ``browser.load_item(item)`` loads onto ``song.view.selected_track`` at
  ``track.view.device_insert_mode``; with ``browser.hotswap_target`` set it
  replaces that target instead.  Samples go to the highlighted clip slot on
  audio tracks; Live Clips (``.alc``) always get a new track with their own
  device chain; an instrument loaded onto an audio track also gets a new MIDI
  track — every load reports what actually changed by diffing the set.
* While a hot-swap target is set Live *filters the browser*: roots that cannot
  hold a replacement for the target list no children (an instrument target
  empties ``audio_effects``), ``app.view.browse_mode`` is true and
  ``filter_type`` still reads -1.  Setting the same target twice raises
  "Couldn't set hotswap target".  Walks made while a target is set are never
  cached, and loads clear/set the target before looking the item up.

There is no search API: search is a bounded breadth-first walk whose results
are cached per root (keyed by uri, TTL :data:`CACHE_TTL`), so repeated searches
and loads by uri are instant.  Walks run on Live's main thread and stop at the
visit/time/depth limits (the answer then says ``truncated``).
"""

import collections
import os
import re
import time

try:
    from urllib.parse import unquote as _unquote
except ImportError:  # pragma: no cover - Live always ships urllib
    def _unquote(text):
        return text

from .. import compat
from .. import resolve
from ..registry import BridgeError, command

#: Folder roots (BrowserItems) in search priority order.
ITEM_ROOTS = ("instruments", "sounds", "drums", "audio_effects", "midi_effects",
              "plugins", "max_for_live", "samples", "clips", "user_library",
              "current_project", "packs")

#: Roots that are Python lists of BrowserItems, not a folder item.
LIST_ROOTS = ("user_folders", "colors")

#: Everything a user can browse, in display order.
KNOWN_ROOTS = ("instruments", "sounds", "drums", "audio_effects", "midi_effects",
               "plugins", "max_for_live", "clips", "samples", "packs",
               "user_library", "user_folders", "current_project", "colors")

#: Searched when no root/category is given.  ``packs`` duplicates the category
#: roots (and is the biggest tree) and ``colors`` only holds tags.
DEFAULT_SEARCH_ROOTS = ("instruments", "sounds", "drums", "audio_effects", "midi_effects",
                        "plugins", "max_for_live", "samples", "clips", "user_library",
                        "user_folders", "current_project")

#: Browser attributes that are never roots.
_NOT_ROOTS = frozenset(["load_item", "preview_item", "stop_preview",
                        "relation_to_hotswap_target", "hotswap_target", "filter_type",
                        "canonical_parent", "legacy_libraries"])

ROOT_ALIASES = {
    "instrument": "instruments", "synths": "instruments",
    "sound": "sounds", "presets": "sounds",
    "drum": "drums", "kits": "drums", "drum_kits": "drums",
    "audio_effect": "audio_effects", "audiofx": "audio_effects", "effects": "audio_effects",
    "audio effects": "audio_effects", "fx": "audio_effects",
    "midi_effect": "midi_effects", "midifx": "midi_effects", "midi effects": "midi_effects",
    "plugin": "plugins", "plug-ins": "plugins", "plug_ins": "plugins", "plug-in": "plugins",
    "vst": "plugins", "vst3": "plugins", "au": "plugins",
    "m4l": "max_for_live", "max": "max_for_live", "max for live": "max_for_live",
    "clip": "clips", "sample": "samples",
    "pack": "packs", "live packs": "packs",
    "user library": "user_library", "user": "user_library", "library": "user_library",
    "userlibrary": "user_library",
    "current project": "current_project", "project": "current_project",
    "places": "user_folders", "user folders": "user_folders", "folders": "user_folders",
    "color": "colors", "tags": "colors",
}

#: ``category`` -> (roots searched, what counts as a match).
CATEGORIES = {
    "instrument": ("instruments", "sounds", "plugins", "max_for_live", "user_library",
                   "packs"),
    "audio_effect": ("audio_effects", "plugins", "max_for_live", "user_library", "packs"),
    "midi_effect": ("midi_effects", "max_for_live", "user_library", "packs"),
    "plugin": ("plugins",),
    "drum_kit": ("drums", "user_library", "packs"),
    "sound": ("sounds", "user_library", "packs"),
    "sample": ("samples", "user_folders", "user_library", "current_project", "packs"),
    "clip": ("clips", "user_folders", "user_library", "current_project", "packs"),
}

AUDIO_EXTENSIONS = (".wav", ".wave", ".aif", ".aiff", ".aifc", ".mp3", ".flac", ".ogg",
                    ".oga", ".m4a", ".mp4", ".aac", ".caf", ".alac")
CLIP_EXTENSIONS = (".alc", ".mid", ".midi")
_OTHER_EXTENSIONS = (".adg", ".adv", ".amxd", ".als", ".agr", ".ask", ".ams", ".alp",
                     ".asd", ".vstpreset", ".aupreset", ".fxp", ".fxb")
_ALL_EXTENSIONS = AUDIO_EXTENSIONS + CLIP_EXTENSIONS + _OTHER_EXTENSIONS

#: ``Live.Browser.FilterType`` (verified in the 12.4.5 dump).
FILTER_TYPES = {-1: "disabled", 0: "hotswap_off", 1: "instrument_hotswap",
                2: "audio_effect_hotswap", 3: "midi_effect_hotswap", 4: "drum_pad_hotswap",
                5: "midi_track_devices", 6: "samples"}

#: ``Live.Track.DeviceInsertMode``: where ``load_item`` puts a device.
INSERT_MODES = {"end": 0, "before_selected": 1, "after_selected": 2}

#: Plug-in format preference when the same plug-in is installed several times.
FORMAT_PREFERENCE = {"VST3": 30, "AU": 20, "VST2": 10}

#: Seconds a walked root stays cached.
CACHE_TTL = 300.0

DEFAULT_MAX_DEPTH = 12
DEFAULT_MAX_VISITS = 40000
DEFAULT_MAX_SECONDS = 10.0
_MAX_URI_CACHE = 200000


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def _get(obj, name, default=None):
    return compat.safe_getattr(obj, name, default)


def norm_text(text):
    """Lower-case, ``_``/``-`` become spaces, whitespace collapsed."""
    text = str(text or "").lower().replace("_", " ").replace("-", " ")
    return " ".join(text.split())


def stem(name):
    """``"808 Core Kit.adg"`` -> ``"808 Core Kit"`` (known extensions only)."""
    text = str(name or "")
    lowered = text.lower()
    for ext in _ALL_EXTENSIONS:
        if lowered.endswith(ext):
            return text[:-len(ext)]
    return text


def is_audio_name(name):
    return str(name or "").lower().endswith(AUDIO_EXTENSIONS)


def is_clip_name(name):
    return str(name or "").lower().endswith(CLIP_EXTENSIONS)


def _check_int(value, name, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, int):
        try:
            if isinstance(value, float) and value == int(value):
                value = int(value)
            else:
                raise ValueError
        except (TypeError, ValueError):
            raise BridgeError("bad_args", "%s must be an integer, got %r" % (name, value))
    if minimum is not None and value < minimum:
        raise BridgeError("bad_args", "%s must be >= %d" % (name, minimum))
    if maximum is not None and value > maximum:
        raise BridgeError("bad_args", "%s must be <= %d" % (name, maximum))
    return value


def _check_number(value, name, minimum, maximum):
    if isinstance(value, bool):
        raise BridgeError("bad_args", "%s must be a number" % name)
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise BridgeError("bad_args", "%s must be a number, got %r" % (name, value))
    return max(minimum, min(maximum, value))


_check_detail = resolve.check_detail


def require_browser(ctx):
    """``ctx.browser`` or an ``unsupported`` error."""
    browser = ctx.browser
    if browser is None:
        raise BridgeError("unsupported", "Live's browser is not available to scripts in "
                                         "this Live session")
    return browser


# --------------------------------------------------------------------------
# roots
# --------------------------------------------------------------------------

class ListRoot(object):
    """A list root (``user_folders``, ``colors``) wrapped to look like a folder."""

    is_folder = True
    is_loadable = False
    is_device = False
    is_selected = False
    source = ""

    def __init__(self, name, items):
        self.name = name
        self.uri = "root:%s" % name
        self._items = tuple(items or ())

    @property
    def children(self):
        return self._items


def _is_item(value):
    return compat.has(value, "uri") and compat.has(value, "is_loadable")


def extra_roots(browser):
    """Root attributes this Live exposes beyond :data:`KNOWN_ROOTS` (a Splice
    section in a future Live, for example) — found via ``dir(browser)``."""
    found = []
    try:
        names = dir(browser)
    except Exception:
        return found
    for name in names:
        if name.startswith("_") or name in KNOWN_ROOTS or name in _NOT_ROOTS:
            continue
        if name.startswith(("add_", "remove_")) or name.endswith("_has_listener"):
            continue
        # Live's roots are class-level (Boost.Python) properties; instance
        # attributes are not part of the API.
        if not isinstance(getattr(type(browser), name, None), property):
            continue
        value = _get(browser, name)
        if value is None or callable(value) or isinstance(value, (str, bytes)):
            continue
        if _is_item(value):
            found.append(name)
            continue
        if not compat.has(value, "__len__"):
            continue
        try:
            items = list(value)
        except Exception:
            continue
        if items and all(_is_item(v) for v in items):
            found.append(name)
    return found


def root_names(browser):
    """Every browsable root name of this browser, known ones first."""
    names = [name for name in KNOWN_ROOTS if _get(browser, name) is not None]
    return names + extra_roots(browser)


def root_item(browser, name):
    """The root ``name`` as a folder-like item (list roots are wrapped)."""
    value = _get(browser, name)
    if value is None:
        return None
    if isinstance(value, (list, tuple)) or (not _is_item(value) and compat.has(value, "__len__")):
        try:
            return ListRoot(name, list(value))
        except Exception:
            return None
    return value


def resolve_root(browser, text):
    """A root name from user text: ``"instruments"``, ``"Plug-Ins"``,
    ``"browser.user_library"``, ``"places"`` ...  Raises ``not_found``."""
    raw = str(text or "").strip()
    if raw.startswith("browser."):
        raw = raw[len("browser."):]
    names = root_names(browser)
    lowered = raw.lower()
    candidates = [lowered, lowered.replace(" ", "_"), lowered.replace("-", "_")]
    for candidate in candidates:
        if candidate in names:
            return candidate
        alias = ROOT_ALIASES.get(candidate)
        if alias in names:
            return alias
    # display names ("Plug-Ins", "Max for Live", "User Library")
    for name in names:
        item = _get(browser, name)
        label = _get(item, "name") if _is_item(item) else None
        if isinstance(label, str) and norm_text(label) == norm_text(raw):
            return name
    if lowered == "splice":
        splice = splice_location(browser)
        if splice.get("root"):
            return splice["root"]
    raise BridgeError("not_found", "no browser root %r (have: %s)" % (text, ", ".join(names)))


def splice_location(browser):
    """Where Splice content shows up in Live's browser, if anywhere.

    Live 12.3+ has a Splice section in its browser UI, but the 12.4 Python API
    has no ``splice`` root.  We look for an extra root named like Splice and
    for a Places folder (``user_folders``) called "Splice".
    """
    for name in extra_roots(browser):
        if "splice" in name.lower():
            return {"available": True, "root": name, "path": name}
    for folder in _get(browser, "user_folders", ()) or ():
        label = _get(folder, "name", "")
        if "splice" in str(label).lower():
            return {"available": True, "root": "user_folders",
                    "path": "user_folders/%s" % label, "uri": _get(folder, "uri")}
    return {"available": False,
            "note": "Live's Splice section is not exposed to scripts. Use the official Splice "
                    "MCP to search/download and live_splice_import_downloaded to bring files "
                    "in, or add your Splice folder to Live's Places so it shows up under "
                    "user_folders."}


# --------------------------------------------------------------------------
# entries + cache
# --------------------------------------------------------------------------

class Entry(object):
    """A browser item plus where we found it.  Flags are read lazily."""

    __slots__ = ("item", "name", "uri", "path", "root", "depth", "t", "_flags")

    def __init__(self, item, path, root, depth):
        self.item = item
        name = _get(item, "name", "")
        self.name = name if isinstance(name, str) else str(name)
        uri = _get(item, "uri")
        self.uri = uri if isinstance(uri, str) else None
        self.path = path
        self.root = root
        self.depth = depth
        self.t = time.time()
        self._flags = None

    def flags(self):
        if self._flags is None:
            item = self.item
            self._flags = (bool(_get(item, "is_folder", False)),
                           bool(_get(item, "is_loadable", False)),
                           bool(_get(item, "is_device", False)),
                           _get(item, "source", "") or "")
        return self._flags

    @property
    def is_folder(self):
        return self.flags()[0]

    @property
    def is_loadable(self):
        return self.flags()[1]

    @property
    def is_device(self):
        return self.flags()[2]

    @property
    def source(self):
        return self.flags()[3]

    @property
    def plugin_format(self):
        if self.root != "plugins":
            return None
        return plugin_format(self.path, self.uri)

    def info(self, detail="summary", with_path=True):
        """Compact dict: name, uri, [path], flags (only when true), source."""
        data = {"name": self.name, "uri": self.uri}
        if with_path:
            data["path"] = self.path
        if detail == "minimal":
            return data
        is_folder, is_loadable, is_device, source = self.flags()
        if is_folder:
            data["is_folder"] = True
        if is_loadable:
            data["is_loadable"] = True
        if is_device:
            data["is_device"] = True
        if source:
            data["source"] = str(source)
        fmt = self.plugin_format
        if fmt:
            data["format"] = fmt
        if detail == "full":
            data["child_count"] = len(children_of(self.item))
        return data


class _RootIndex(object):
    __slots__ = ("entries", "complete", "max_depth", "t", "cut")

    def __init__(self, entries, complete, max_depth, cut):
        self.entries = entries
        self.complete = complete
        self.max_depth = max_depth
        self.t = time.time()
        self.cut = cut


#: root name -> _RootIndex
_INDEX = {}
#: uri -> Entry (every item we have seen, from walks and listings)
_URIS = {}


def clear_cache():
    """Forget every cached walk (also done automatically after ``CACHE_TTL``)."""
    _INDEX.clear()
    _URIS.clear()


def _remember(entry):
    if entry.uri:
        if len(_URIS) >= _MAX_URI_CACHE:
            _URIS.clear()
        _URIS[entry.uri] = entry
    return entry


def children_of(item):
    """``item.children`` as a list (``[]`` when unreadable)."""
    value = _get(item, "children")
    if value is None:
        return []
    try:
        return list(value)
    except Exception:
        return []


def plugin_format(path, uri):
    """"VST3", "VST2" or "AU" from the plug-in's folder (``plugins/VST3/...``)
    or its uri; ``None`` when unknown."""
    parts = str(path or "").split("/")
    candidates = parts[1:3] if len(parts) > 1 else []
    for part in candidates:
        text = part.strip().lower()
        if text.startswith("vst3") or text.startswith("vst 3"):
            return "VST3"
        if text.startswith("audio unit") or text in ("au", "auv2", "auv3", "components"):
            return "AU"
        if text in ("vst", "vst2", "vst 2", "vst plug-ins", "vst plugins"):
            return "VST2"
    text = str(uri or "").lower()
    if "vst3" in text:
        return "VST3"
    if "#au:" in text or ":au:" in text or "audiounit" in text or "audio%20units" in text:
        return "AU"
    if "vst" in text:
        return "VST2"
    return None


class Budget(object):
    """Visit/time limit shared by one command's walks."""

    def __init__(self, max_visits=DEFAULT_MAX_VISITS, max_seconds=DEFAULT_MAX_SECONDS):
        self.max_visits = max_visits
        self.deadline = time.time() + max_seconds
        self.visits = 0
        self.cut = False

    def take(self):
        if self.cut:
            return False
        self.visits += 1
        if self.visits > self.max_visits or \
                ((self.visits & 63) == 0 and time.time() > self.deadline):
            self.cut = True
            return False
        return True


def _walk(item, path, root, max_depth, budget, entries):
    """Breadth-first walk under ``item``.  Returns ``(complete, cut)``."""
    queue = collections.deque([(item, path, 0)])
    complete = True
    while queue:
        current, current_path, depth = queue.popleft()
        children = children_of(current)
        if not children:
            continue
        if depth >= max_depth:
            complete = False
            continue
        for child in children:
            if not budget.take():
                return False, True
            entry = _remember(Entry(child, "%s/%s" % (current_path, _get(child, "name", "")),
                                    root, depth + 1))
            entries.append(entry)
            queue.append((child, entry.path, depth + 1))
    return complete, False


def root_index(browser, root, budget, max_depth=DEFAULT_MAX_DEPTH, refresh=False):
    """Cached (or freshly walked) entries of one root.

    Returns ``(index, from_cache)``.
    """
    index = _INDEX.get(root)
    now = time.time()
    if index is not None and not refresh and now - index.t < CACHE_TTL and \
            (index.complete or (not index.cut and index.max_depth >= max_depth)):
        return index, True
    item = root_item(browser, root)
    entries = []
    if item is None:
        index = _RootIndex(entries, True, max_depth, False)
    else:
        complete, cut = _walk(item, root, root, max_depth, budget, entries)
        index = _RootIndex(entries, complete, max_depth, cut)
    if not hotswap_active(browser):
        # a walk made in hot-swap mode only sees the filtered browser — never keep it
        _INDEX[root] = index
    return index, False


def hotswap_active(browser):
    """True while Live's browser has a hot-swap target (and is filtered by it)."""
    return _get(browser, "hotswap_target") is not None


# --------------------------------------------------------------------------
# search
# --------------------------------------------------------------------------

def _score(entry, query, words):
    name = norm_text(entry.name)
    base = norm_text(stem(entry.name))
    if base == query or name == query:
        score = 100.0
    elif base.startswith(query):
        score = 85.0
    elif all(word in name for word in words):
        score = 70.0 if query in name else 60.0
        tokens = base.split()
        if all(any(token.startswith(word) for token in tokens) for word in words):
            score += 5.0
    elif all(word in norm_text(entry.path) for word in words):
        score = 30.0
    else:
        return 0.0
    return score - min(entry.depth, 12) * 0.5


def _plugin_score(entry, query, score):
    """Plug-in extras on top of :func:`_score`: vendor-qualified queries
    ("Xfer Records/Serum 2" scores the name part against the name and needs the
    vendor words in the folders) and space-free names ("serum2")."""
    if "/" in query:
        vendor, _sep, name = query.rpartition("/")
        vendor, name = vendor.strip(), name.strip()
        folders = norm_text("/".join(entry.path.split("/")[1:-1]))
        if vendor and name and all(word in folders for word in vendor.split()):
            return max(score, _score(entry, name, name.split()))
        return score
    if not score:
        compact = query.replace(" ", "")
        name = norm_text(stem(entry.name)).replace(" ", "")
        if compact and name == compact:
            return 95.0 - min(entry.depth, 12) * 0.5
        if compact and name.startswith(compact):
            return 80.0 - min(entry.depth, 12) * 0.5
    return score


def _category_filter(category, entry):
    """True when ``entry`` fits ``category`` (``None`` = anything)."""
    name = entry.name
    if category is None:
        return True
    if category == "sample":
        return is_audio_name(name)
    if category == "clip":
        return is_clip_name(name)
    if is_audio_name(name) or is_clip_name(name):
        return False
    if category == "drum_kit":
        lowered = name.lower()
        return entry.root == "drums" or "kit" in lowered or "drum" in lowered
    return True


def _category_bonus(category, entry):
    bonus = 0.0
    if category == "drum_kit":
        lowered = entry.name.lower()
        if lowered.endswith(".adg") and "kit" in lowered:
            bonus += 10.0
    if category in ("instrument", "audio_effect", "midi_effect") and entry.is_device:
        bonus += 5.0
    if entry.root == "plugins" and category in ("instrument", "audio_effect"):
        # Live's plug-in items carry no type: "Serum 2 FX" is the effect, "Serum 2" the synth
        is_fx = bool(re.search(r"\b(fx|effects?)\b", entry.name.lower()))
        if is_fx:
            bonus += 20.0 if category == "audio_effect" else -8.0
    return bonus


def normalize_roots(browser, root=None, category=None):
    """The ordered list of roots a search walks."""
    if root is not None:
        values = root if isinstance(root, (list, tuple)) else [root]
        if not values:
            raise BridgeError("bad_args", "root must be a root name or a non-empty list")
        roots = []
        for value in values:
            if not isinstance(value, str):
                raise BridgeError("bad_args", "root must be a string or a list of strings")
            if value.strip().lower() in ("all", "*", "everything"):
                roots.extend(n for n in root_names(browser) if n != "colors")
                continue
            roots.append(resolve_root(browser, value))
    elif category is not None:
        roots = list(CATEGORIES[category])
    else:
        roots = list(DEFAULT_SEARCH_ROOTS)
        roots.extend(extra_roots(browser))
    available = set(root_names(browser))
    ordered = []
    for name in roots:
        if name in available and name not in ordered:
            ordered.append(name)
    return ordered


def check_category(category):
    if category is None:
        return None
    if not isinstance(category, str):
        raise BridgeError("bad_args", "category must be a string")
    key = category.strip().lower().replace(" ", "_").replace("-", "_")
    key = {"instruments": "instrument", "effect": "audio_effect", "effects": "audio_effect",
           "audio_effects": "audio_effect", "midi_effects": "midi_effect",
           "plugins": "plugin", "drums": "drum_kit", "kit": "drum_kit", "drum": "drum_kit",
           "sounds": "sound", "preset": "sound", "samples": "sample", "clips": "clip"
           }.get(key, key)
    if key not in CATEGORIES:
        raise BridgeError("bad_args", "category must be one of %s"
                          % ", ".join(sorted(CATEGORIES)))
    return key


def check_format(plugin_format_arg):
    if plugin_format_arg is None:
        return None
    key = str(plugin_format_arg).strip().upper().replace(" ", "")
    key = {"VST": "VST2", "VST2": "VST2", "VST3": "VST3", "AU": "AU", "AUDIOUNIT": "AU",
           "AUDIOUNITS": "AU", "AUV2": "AU", "COMPONENT": "AU"}.get(key)
    if key is None:
        raise BridgeError("bad_args", "plugin_format must be 'vst3', 'vst2' or 'au'")
    return key


_FILE_ID = re.compile(r"FileId_\d+")


def _dedupe_key(entry):
    """Live lists the same file under several roots (``...:FileId_13853`` in
    sounds, instruments and packs); keep one hit per file."""
    uri = entry.uri or ""
    match = _FILE_ID.search(uri)
    if match:
        return match.group(0)
    return uri or ("%s|%s" % (entry.root, entry.path))


def search_entries(browser, query, roots, category=None, loadable_only=False,
                   plugin_format_arg=None, max_depth=DEFAULT_MAX_DEPTH,
                   max_visits=DEFAULT_MAX_VISITS, max_seconds=DEFAULT_MAX_SECONDS,
                   refresh=False, folders_only=False):
    """Walk (or reuse) the root indexes and score every entry.

    Returns ``(ranked [(score, entry)], stats)``.
    """
    q = norm_text(query)
    words = q.split()
    if not words:
        raise BridgeError("bad_args", "query must contain at least one word")
    budget = Budget(max_visits, max_seconds)
    stats = {"searched": [], "cached": [], "skipped": [], "partial": []}
    best = {}
    for root in roots:
        if budget.cut:
            stats["skipped"].append(root)
            continue
        index, cached = root_index(browser, root, budget, max_depth, refresh)
        stats["searched"].append(root)
        if cached:
            stats["cached"].append(root)
        if not index.complete:
            stats["partial"].append(root)
        for entry in index.entries:
            score = _score(entry, q, words)
            if entry.root == "plugins":
                score = _plugin_score(entry, q, score)
            if not score:
                continue
            if not _category_filter(category, entry):
                continue
            if loadable_only and not entry.is_loadable:
                continue
            if folders_only and not (entry.is_folder or children_of(entry.item)):
                continue
            if entry.root == "plugins":
                fmt = entry.plugin_format
                if plugin_format_arg is not None and fmt is not None \
                        and fmt != plugin_format_arg:
                    continue
                if plugin_format_arg is not None and fmt == plugin_format_arg:
                    score += 40.0
                else:
                    score += FORMAT_PREFERENCE.get(fmt, 0) / 3.0
            score += _category_bonus(category, entry)
            key = _dedupe_key(entry)
            previous = best.get(key)
            if previous is None or previous[0] < score:
                best[key] = (score, entry)
    # ``packs`` mirrors the category roots with different uris: drop a pack hit
    # when the same file (name + source) was found in a category root.
    elsewhere = set((e.name.lower(), e.source) for _s, e in best.values() if e.root != "packs")
    ranked = sorted((pair for pair in best.values()
                     if pair[1].root != "packs"
                     or (pair[1].name.lower(), pair[1].source) not in elsewhere),
                    key=lambda pair: (-pair[0], pair[1].depth, pair[1].name))
    stats["visited"] = budget.visits
    stats["truncated"] = bool(budget.cut or stats["skipped"])
    return ranked, stats


# --------------------------------------------------------------------------
# lookup by uri / path / query
# --------------------------------------------------------------------------

def _match_child(children, wanted):
    """Child named ``wanted``: exact, case-insensitive, without extension,
    then a unique case-insensitive prefix."""
    names = [(child, str(_get(child, "name", ""))) for child in children]
    for child, name in names:
        if name == wanted:
            return child
    lowered = wanted.lower()
    for child, name in names:
        if name.lower() == lowered:
            return child
    for child, name in names:
        if stem(name).lower() == lowered:
            return child
    prefix = [child for child, name in names if name.lower().startswith(lowered)]
    if len(prefix) == 1:
        return prefix[0]
    return None


def find_by_path(browser, path):
    """Navigate ``"instruments/Analog/Bass"`` (root name + item names; ``/``
    inside item names is handled greedily).  Raises ``not_found``."""
    text = str(path or "").strip().strip("/")
    if not text:
        raise BridgeError("bad_args", "path must not be empty")
    if text.startswith("browser."):
        text = text[len("browser."):]
    segments = [segment for segment in text.split("/")]
    if segments[0].strip().lower() == "splice":
        splice = splice_location(browser)
        if splice.get("path") and splice["path"].lower() != "splice":
            segments = splice["path"].split("/") + segments[1:]
    root = resolve_root(browser, segments[0])
    item = root_item(browser, root)
    if item is None:
        raise BridgeError("not_found", "browser root %r is empty in this Live" % root)
    entry = Entry(item, root, root, 0)
    rest = segments[1:]
    while rest:
        children = children_of(entry.item)
        matched = None
        for count in range(len(rest), 0, -1):
            child = _match_child(children, "/".join(rest[:count]))
            if child is not None:
                matched = (child, count)
                break
        if matched is None:
            names = ", ".join(repr(str(_get(c, "name", ""))) for c in children[:15])
            more = " ..." if len(children) > 15 else ""
            raise BridgeError("not_found", "%s: no item %r (%s)"
                              % (entry.path, rest[0],
                                 "have: %s%s" % (names, more) if children else "it is empty"))
        child, count = matched
        entry = _remember(Entry(child, "%s/%s" % (entry.path, _get(child, "name", "")),
                                root, entry.depth + 1))
        rest = rest[count:]
    return entry


def _roots_for_uri(browser, uri):
    """Roots whose uri prefix matches ``uri`` first, then the rest."""
    base = uri.partition("#")[0]
    names = [n for n in root_names(browser) if n != "colors"]
    first = [n for n in names if _get(root_item(browser, n), "uri") == base]
    return first + [n for n in names if n not in first] + \
        (["colors"] if "colors" in root_names(browser) else [])


def _navigate_uri(browser, root, uri):
    """Fast path: follow the ``Folder:Sub:Item`` part of a ``query:X#...`` uri."""
    item = root_item(browser, root)
    if item is None:
        return None
    base, _sep, rest = uri.partition("#")
    entry = Entry(item, root, root, 0)
    if not rest:
        return entry if _get(item, "uri") == uri else None
    segments = [_unquote(segment) for segment in rest.split(":")]
    for segment in segments:
        children = children_of(entry.item)
        for child in children:
            if _get(child, "uri") == uri:
                return _remember(Entry(child, "%s/%s" % (entry.path, _get(child, "name", "")),
                                       root, entry.depth + 1))
        child = None
        for candidate in children:
            if _get(candidate, "name") == segment:
                child = candidate
                break
        if child is None:
            return None
        entry = Entry(child, "%s/%s" % (entry.path, _get(child, "name", "")), root,
                      entry.depth + 1)
    return _remember(entry) if entry.uri == uri else None


def find_by_uri(browser, uri, budget=None):
    """The entry with this uri: cache, then the uri's own folder path, then a
    bounded walk of every root.  Raises ``not_found``."""
    if not isinstance(uri, str) or not uri.strip():
        raise BridgeError("bad_args", "uri must be a non-empty string")
    uri = uri.strip()
    cached = _URIS.get(uri)
    if cached is not None and time.time() - cached.t < CACHE_TTL:
        return cached
    names = root_names(browser)
    for name in names:
        item = root_item(browser, name)
        if item is not None and _get(item, "uri") == uri:
            return Entry(item, name, name, 0)
    ordered = _roots_for_uri(browser, uri)
    base = uri.partition("#")[0]
    for name in ordered:
        if _get(root_item(browser, name), "uri") == base:
            entry = _navigate_uri(browser, name, uri)
            if entry is not None:
                return entry
    budget = budget or Budget()
    for name in ordered:
        if budget.cut:
            break
        index, _cached = root_index(browser, name, budget)
        for entry in index.entries:
            if entry.uri == uri:
                return entry
    raise BridgeError("not_found", "no browser item with uri %r%s — uris come from "
                      "browser.search / browser.browse results"
                      % (uri, " (search stopped at the visit/time limit)" if budget.cut else ""))


def resolve_entry(browser, uri=None, path=None, query=None, root=None, category=None,
                  plugin_format_arg=None, loadable=True, max_seconds=DEFAULT_MAX_SECONDS):
    """One item from ``uri`` / ``path`` / ``query``.

    Returns ``(entry, how, alternatives)`` where ``alternatives`` are the next
    best search hits (only for ``query``).
    """
    given = [name for name, value in (("uri", uri), ("path", path), ("query", query))
             if value is not None]
    if not given:
        raise BridgeError("bad_args", "pass one of uri, path or query")
    if len(given) > 1:
        raise BridgeError("bad_args", "pass only one of uri, path or query (got %s)"
                          % ", ".join(given))
    if uri is not None:
        return find_by_uri(browser, uri, Budget(max_seconds=max_seconds)), "uri", []
    if path is not None:
        return find_by_path(browser, path), "path", []
    if not isinstance(query, str) or not query.strip():
        raise BridgeError("bad_args", "query must be a non-empty string")
    roots = normalize_roots(browser, root, category)
    ranked, stats = search_entries(browser, query, roots, category=category,
                                   loadable_only=loadable,
                                   plugin_format_arg=plugin_format_arg,
                                   max_seconds=max_seconds)
    if not ranked:
        where = ", ".join(roots)
        extra = " (search was cut short by the visit/time limit)" if stats["truncated"] else ""
        what = "loadable item" if loadable else "item"
        if plugin_format_arg:
            what = "%s plug-in" % plugin_format_arg
        raise BridgeError("not_found", "no %s matching %r in %s%s"
                          % (what, query, where, extra))
    entry = ranked[0][1]
    alternatives = [pair[1].info("minimal") for pair in ranked[1:5]]
    return entry, "query", alternatives


# --------------------------------------------------------------------------
# set snapshots (to report what a load changed)
# --------------------------------------------------------------------------

def _same(a, b):
    try:
        return a is b or a == b
    except Exception:
        return False


def _contains(collection, obj):
    return any(_same(obj, other) for other in collection)


def _is_drum_pad(obj):
    return compat.has(obj, "note") and compat.has(obj, "chains") and \
        not compat.has(obj, "parameters")


def devices_of(host):
    """Devices directly on a track/chain, or on every chain of a drum pad."""
    if _is_drum_pad(host):
        devices = []
        for chain in _get(host, "chains", ()) or ():
            devices.extend(_get(chain, "devices", ()) or ())
        return devices
    return list(_get(host, "devices", ()) or ())


def snapshot(ctx, track, extra_hosts=()):
    """What a load can change: the track's devices, clip slots, arrangement
    clips, the song's tracks and the devices of ``extra_hosts``."""
    song = ctx.song
    hosts = []
    for host in [track] + list(extra_hosts):
        if host is not None and not _contains(hosts, host):
            hosts.append(host)
    devices = []
    for host in hosts:
        devices.extend((d, _get(d, "name", "")) for d in devices_of(host))
    slots = list(_get(track, "clip_slots", ()) or ()) if track is not None else []
    return {
        "hosts": hosts,
        "devices": devices,
        "slots": [(slot, bool(_get(slot, "has_clip", False))) for slot in slots],
        "arrangement": list(_get(track, "arrangement_clips", ()) or ())
        if track is not None and compat.has(track, "arrangement_clips") else [],
        "tracks": list(_get(song, "tracks", ()) or ()),
    }


def diff(ctx, before, after_track=None):
    """``{inserted, removed, clips, arrangement_clips, new_tracks}`` between a
    snapshot and now (keys omitted when empty)."""
    song = ctx.song
    result = {}
    old_devices = [pair[0] for pair in before["devices"]]
    now_devices = []
    for host in before["hosts"]:
        now_devices.extend(devices_of(host))
    inserted = [d for d in now_devices if not _contains(old_devices, d)]
    removed = [name for device, name in before["devices"]
               if not _contains(now_devices, device)]
    # A hot-swapped preset of the same device class keeps the LOM object — only its
    # name (and parameters) change.
    changed = []
    for device, name in before["devices"]:
        if _contains(now_devices, device):
            now_name = _get(device, "name", "")
            if now_name != name:
                summary = ctx.summarize(device, "minimal")
                if isinstance(summary, dict):
                    summary["was"] = name
                changed.append(summary)
    if inserted:
        result["inserted"] = [ctx.summarize(d, "minimal") for d in inserted]
    if removed:
        result["removed"] = [{"name": name} for name in removed]
    if changed:
        result["changed"] = changed
    clips = []
    for slot, had_clip in before["slots"]:
        clip = _get(slot, "clip")
        if not had_clip and clip is not None:
            clips.append(ctx.summarize(clip, "minimal"))
    if clips:
        result["clips"] = clips
    track = after_track
    if track is not None and before["arrangement"] is not None:
        now = list(_get(track, "arrangement_clips", ()) or ()) \
            if compat.has(track, "arrangement_clips") else []
        added = [c for c in now if not _contains(before["arrangement"], c)]
        if added:
            result["arrangement_clips"] = [ctx.summarize(c, "minimal") for c in added]
    new_tracks = [t for t in (_get(song, "tracks", ()) or ())
                  if not _contains(before["tracks"], t)]
    if new_tracks:
        result["new_tracks"] = [ctx.summarize(t, "minimal") for t in new_tracks]
        for new_track in new_tracks:
            for device in devices_of(new_track):
                result.setdefault("inserted", []).append(ctx.summarize(device, "minimal"))
            for slot in _get(new_track, "clip_slots", ()) or ():
                clip = _get(slot, "clip")
                if clip is not None:
                    result.setdefault("clips", []).append(ctx.summarize(clip, "minimal"))
    return result


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------

def _create_track(ctx, kind, name=None):
    song = ctx.song
    method = "create_midi_track" if kind == "midi" else "create_audio_track"
    if not compat.has(song, method):
        raise BridgeError("unsupported", "song.%s is not available in this Live" % method)
    try:
        track = getattr(song, method)(-1)
    except Exception as error:
        raise BridgeError("invalid_state", "Live could not create a %s track: %s"
                          % (kind, error))
    if track is None:
        tracks = list(_get(song, "tracks", ()) or ())
        track = tracks[-1] if tracks else None
    if track is not None and name:
        try:
            track.name = str(name)
        except Exception:
            pass
    return track


def select_track(ctx, track):
    view = ctx.view
    try:
        view.selected_track = track
    except Exception as error:
        raise BridgeError("invalid_state", "could not select track %r: %s"
                          % (_get(track, "name", ""), error))


def highlight_slot(ctx, clip_slot):
    try:
        ctx.view.highlighted_clip_slot = clip_slot
    except Exception as error:
        raise BridgeError("invalid_state", "could not highlight the clip slot: %s" % error)


def resolve_target_device(ctx, track, device):
    """A device (or drum pad) from ``device`` — an index/name on ``track`` or a
    LOM path such as ``song.tracks[2].devices[0].drum_pads[36]``."""
    if isinstance(device, str) and device.strip().startswith("song."):
        obj = ctx.resolve(device.strip())
        if obj is None:
            raise BridgeError("not_found", "%s: nothing there" % device)
        return obj
    if track is None:
        track = _get(ctx.view, "selected_track")
        if track is None:
            raise BridgeError("bad_args", "pass track (no track is selected)")
    return ctx.device(track, device)


def resolve_drum_pad(ctx, rack, drum_pad):
    """A pad of a (top-level) Drum Rack from a MIDI note (36), a note name ("C1",
    Live's octave numbering) or a pad / chain name ("Kick")."""
    from . import racks as racks_handlers
    pads = list(_get(rack, "drum_pads", ()) or ())
    if not pads:
        raise BridgeError("bad_args", "%r is not a top-level Drum Rack (no drum pads)"
                          % _get(rack, "name", ""))
    if isinstance(drum_pad, bool) or not isinstance(drum_pad, (int, str)):
        raise BridgeError("bad_args", "drum_pad must be a MIDI note (0-127), a note name "
                          "like 'C1' or a pad name")
    groups = [(_get(pad, "note"), pad, list(_get(pad, "chains", ()) or ())) for pad in pads]
    note = racks_handlers.parse_note(drum_pad, racks_handlers._pad_names(groups))
    for pad in pads:
        if _get(pad, "note") == note:
            return pad
    if 0 <= note < len(pads):
        return pads[note]
    raise BridgeError("not_found", "no drum pad for note %d" % note)


def hotswap_target_info(ctx, browser):
    target = _get(browser, "hotswap_target")
    info = {"target": None}
    if target is not None:
        if _is_drum_pad(target):
            info["target"] = {"kind": "drum_pad", "name": _get(target, "name"),
                              "note": _get(target, "note"), "path": ctx.path_of(target)}
        else:
            summary = ctx.summarize(target, "minimal")
            info["target"] = summary if isinstance(summary, dict) else {"name": str(summary)}
    filter_type = _get(browser, "filter_type")
    try:
        info["filter_type"] = FILTER_TYPES.get(int(filter_type), int(filter_type))
    except (TypeError, ValueError):
        info["filter_type"] = None
    app_view = _get(ctx.app, "view")
    if compat.has(app_view, "browse_mode"):
        info["browse_mode"] = bool(_get(app_view, "browse_mode", False))
    return info


def set_hotswap_target(browser, target):
    """Set (or clear with ``None``) the hot-swap target.  Live raises "Couldn't set
    hotswap target" when the target is already the current one, so that is a no-op."""
    current = _get(browser, "hotswap_target")
    if _same(current, target) or (current is None and target is None):
        return
    try:
        browser.hotswap_target = target
    except Exception as error:
        raise BridgeError("invalid_state", "Live refused to change the hot-swap target: %s"
                          % error)


def load_entry(ctx, browser, entry, track, slot=None, position="end", hotswap_target=None,
               keep_hotswap=False):
    """Select ``track`` (+ highlight ``slot``), ``load_item`` and diff the set.

    Returns the change dict from :func:`diff` plus ``{"notes": [...]}``.
    """
    if not compat.has(browser, "load_item"):
        raise BridgeError("unsupported", "browser.load_item is not available in this Live")
    if not entry.is_loadable:
        raise BridgeError("invalid_state", "%r is not loadable (%s) — browse into it and pick "
                          "an item" % (entry.name, "a folder" if entry.is_folder
                                       else "not a loadable item"))
    notes = []
    extra_hosts = []
    if hotswap_target is not None:
        set_hotswap_target(browser, hotswap_target)
        if _is_drum_pad(hotswap_target):
            extra_hosts.append(hotswap_target)  # its chains may not exist yet
        else:
            host = _get(hotswap_target, "canonical_parent")
            if host is not None and compat.has(host, "devices"):
                extra_hosts.append(host)
    elif not keep_hotswap and _get(browser, "hotswap_target") is not None:
        try:
            browser.hotswap_target = None
            notes.append("cleared the hot-swap target so the item is added, not swapped in")
        except Exception:
            raise BridgeError("invalid_state", "Live is in hot-swap mode (a device is the "
                              "hot-swap target) — exit hot-swap mode or load with hotswap=true")
    old_mode = None
    track_view = _get(track, "view") if track is not None else None
    if track is not None and hotswap_target is None:
        select_track(ctx, track)
        if slot is not None:
            highlight_slot(ctx, slot)
        if position is not None and compat.has(track_view, "device_insert_mode"):
            old_mode = _get(track_view, "device_insert_mode")
            try:
                track_view.device_insert_mode = INSERT_MODES[position]
            except Exception:
                old_mode = None
    before = snapshot(ctx, track, extra_hosts)
    try:
        try:
            browser.load_item(entry.item)
        except Exception as error:
            fresh = None
            if entry.uri and time.time() - entry.t > 1.0:
                _URIS.pop(entry.uri, None)
                try:
                    fresh = find_by_uri(browser, entry.uri)
                except BridgeError:
                    fresh = None
            if fresh is None or fresh.item is entry.item:
                raise BridgeError("invalid_state", "Live could not load %r: %s"
                                  % (entry.name, error))
            try:
                browser.load_item(fresh.item)
            except Exception as second:
                raise BridgeError("invalid_state", "Live could not load %r: %s"
                                  % (entry.name, second))
    finally:
        if old_mode is not None:
            try:
                track_view.device_insert_mode = old_mode
            except Exception:
                pass
    changes = diff(ctx, before, track)
    if not changes and not (is_audio_name(entry.name) or is_clip_name(entry.name)):
        # Loading a device/preset over a device of the same kind keeps Live's device
        # object (and often its name): it is reset in place — verified with Operator.
        wanted = stem(entry.name)
        reloaded = []
        for host in before["hosts"]:
            for device in devices_of(host):
                if _get(device, "name", "") == wanted:
                    summary = ctx.summarize(device, "minimal")
                    if isinstance(summary, dict):
                        summary["reloaded"] = True
                    reloaded.append(summary)
        if reloaded:
            changes["changed"] = reloaded
            notes.append("Live loaded %r over the existing device of the same kind: it keeps "
                         "the device object and resets its parameters to the loaded "
                         "preset/defaults" % wanted)
    if not changes:
        focused = _get(_get(ctx.app, "view"), "focused_document_view")
        if (is_audio_name(entry.name) or is_clip_name(entry.name)) and focused == "Arranger":
            notes.append("nothing was loaded: Live's Arrangement view is focused, and browser "
                         "loads of samples/clips only land in the Session view — show the "
                         "Session view first, or use samples.import (works in any view)")
        else:
            notes.append("Live reported no change yet — big presets and plug-ins can finish "
                         "loading asynchronously; check the track's devices/clips in a "
                         "moment")
    elif changes.get("new_tracks") and hotswap_target is None:
        if is_clip_name(entry.name):
            notes.append("Live loads a Live Clip (.alc) onto a new track together with its "
                         "device chain — the requested track/slot are not used")
        else:
            notes.append("the target track could not hold this item, so Live created a new "
                         "track for it (see new_tracks)")
    if notes:
        changes["notes"] = notes
    return changes


def _pick_track(ctx, track, new_track, track_name, default_name):
    """Target track for a load, creating one when asked."""
    created = False
    if new_track is not None:
        if new_track not in ("midi", "audio"):
            raise BridgeError("bad_args", "new_track must be 'midi' or 'audio'")
        if track is not None:
            raise BridgeError("bad_args", "pass either track or new_track, not both")
        return _create_track(ctx, new_track, track_name or default_name), True
    if track is not None:
        return ctx.track(track), created
    selected = _get(ctx.view, "selected_track")
    if selected is None:
        raise BridgeError("invalid_state", "no track is selected — pass track or new_track")
    return selected, created


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

@command("browser.roots", doc="Browser roots with item counts, extra roots, Splice and hot-swap info")
def browser_roots(ctx, counts=True):
    """List the browser's top-level categories.

    Args:
        counts: include ``count`` (number of direct children) per root — reading
            it makes Live list the folder, cheap for every root.

    Returns:
        {"roots": [{"name": "instruments", "label": "Instruments", "uri", "count"}, ...],
         "extra_roots": [...names not known to LiveBridge, e.g. a Splice section...],
         "splice": {"available": bool, "path"?, "note"?},
         "hotswap": {"target", "filter_type", "browse_mode"}}

    Gotchas:
        ``user_folders`` (Places you added) and ``colors`` (tags) are lists, not
        folders; browse them like any other root ("user_folders/My Samples").
        ``packs`` mirrors content that also appears in the category roots.
    """
    browser = require_browser(ctx)
    roots = []
    for name in root_names(browser):
        item = root_item(browser, name)
        if item is None:
            continue
        entry = {"name": name, "label": _get(item, "name") if not isinstance(item, ListRoot)
                 else name.replace("_", " ").title(), "uri": _get(item, "uri")}
        if isinstance(item, ListRoot):
            entry["list"] = True
        if counts:
            entry["count"] = len(children_of(item))
        roots.append(entry)
    return {"roots": roots, "extra_roots": extra_roots(browser),
            "splice": splice_location(browser),
            "hotswap": hotswap_target_info(ctx, browser)}


_KINDS = ("folders", "loadable", "devices", "samples", "clips", "presets")


def _kind_ok(kind, entry):
    if kind is None:
        return True
    if kind == "folders":
        return entry.is_folder
    if kind == "loadable":
        return entry.is_loadable
    if kind == "devices":
        return entry.is_device
    if kind == "samples":
        return is_audio_name(entry.name)
    if kind == "clips":
        return is_clip_name(entry.name)
    if kind == "presets":
        return entry.is_loadable and not entry.is_device and not is_audio_name(entry.name) \
            and not is_clip_name(entry.name)
    return True


@command("browser.browse", doc="List a browser folder's children by path, uri or name (paged)")
def browser_browse(ctx, path=None, uri=None, name=None, offset=0, limit=100, filter=None,
                   kind=None, detail="summary"):
    """List what is inside a browser folder (or device/pack with presets).

    Args:
        path: ``"<root>/<item>/<item>"`` — root is a root name ("instruments",
            "user_library", "user_folders", "current_project", "plugins" ...),
            then item names (case-insensitive; extensions optional).  ``"browser.sounds"``
            works too.
        uri: an item uri from an earlier result (fastest).
        name: search for a folder by name and list it (best match).
        offset, limit: paging (limit 1..500, default 100).
        filter: only children whose name contains all these words.
        kind: only "folders", "loadable", "devices", "samples", "clips" or "presets".
        detail: "minimal" (name+uri), "summary" (default: + flags, source,
            plug-in format) or "full" (+ child_count per item — slower).

    Returns:
        {"path", "uri", "name", "total", "offset", "count", "items": [{name, uri,
         is_folder?, is_loadable?, is_device?, source?, format?}], "next_offset"?}
        With no path/uri/name: the roots (like browser.roots).  Flags are only
        present when true.

    Gotchas:
        Device items (e.g. "Analog") and packs have ``is_folder`` false but
        contain preset folders — browse their uri.  Paths change when Live
        re-scans; uris are the stable handle.
    """
    browser = require_browser(ctx)
    offset = _check_int(offset, "offset", 0)
    limit = _check_int(limit, "limit", 1, 500)
    _check_detail(detail)
    if kind is not None and kind not in _KINDS:
        raise BridgeError("bad_args", "kind must be one of %s" % ", ".join(_KINDS))
    given = [v for v in (path, uri, name) if v is not None]
    if len(given) > 1:
        raise BridgeError("bad_args", "pass only one of path, uri or name")
    if not given:
        return browser_roots(ctx)
    if name is not None:
        if not isinstance(name, str) or not name.strip():
            raise BridgeError("bad_args", "name must be a non-empty string")
        ranked, _stats = search_entries(browser, name, normalize_roots(browser),
                                        folders_only=True)
        if not ranked:
            raise BridgeError("not_found", "no browser folder matching %r" % name)
        entry = ranked[0][1]
    elif uri is not None:
        entry = find_by_uri(browser, uri)
    else:
        entry = find_by_path(browser, path)
    words = norm_text(filter).split() if filter else []
    items = []
    for child in children_of(entry.item):
        child_entry = _remember(Entry(child, "%s/%s" % (entry.path, _get(child, "name", "")),
                                      entry.root, entry.depth + 1))
        if words and not all(w in norm_text(child_entry.name) for w in words):
            continue
        if not _kind_ok(kind, child_entry):
            continue
        items.append(child_entry)
    page = items[offset:offset + limit]
    result = {"path": entry.path, "uri": entry.uri, "name": entry.name, "total": len(items),
              "offset": offset, "count": len(page),
              "items": [e.info(detail, with_path=False) for e in page]}
    if offset + limit < len(items):
        result["next_offset"] = offset + limit
    return _note_hotswap(browser, result)


@command("browser.search", doc="Search the browser (cached bounded walk) by words, root, category")
def browser_search(ctx, query, root=None, category=None, limit=20, offset=0,
                   loadable_only=False, plugin_format=None, max_depth=DEFAULT_MAX_DEPTH,
                   max_visits=DEFAULT_MAX_VISITS, max_seconds=DEFAULT_MAX_SECONDS,
                   refresh=False, detail="summary"):
    """Find browser items whose name contains every query word.

    Args:
        query: words, any order, case-insensitive ("808 kit", "grand piano", "reverb").
        root: a root name or list of them ("instruments", "samples", "user_library",
            "user_folders", "packs", "all" ...).  Default: every root except
            packs and colors (or the category's roots).
        category: "instrument", "audio_effect", "midi_effect", "plugin", "drum_kit",
            "sound", "sample" or "clip" — picks the roots and filters by type
            (e.g. sample = audio files only).
        limit, offset: result paging (limit 1..200, default 20).
        loadable_only: only items that can be loaded.
        plugin_format: "vst3", "vst2" or "au" — only that plug-in format
            (default: all, VST3 ranked first, then AU, then VST2).
        max_depth: folder depth limit (1..30, default 12).
        max_visits: items walked at most (100..200000, default 40000).
        max_seconds: time budget of the walk (0.5..60, default 10).
        refresh: ignore the cache (TTL 300 s) and walk again.
        detail: "minimal" | "summary" | "full".

    Returns:
        {"query", "total", "count", "results": [{name, uri, path, flags..., source?,
         format?}], "searched": [roots], "cached": [roots served from cache],
         "visited": n, "truncated": bool, "skipped"?: [roots not reached]}
        Ranking: exact name > name prefix > all words in the name > words in
        the folder path; shallower items first.  A file listed both in a
        category root and in ``packs`` is returned once.  ``hotswap_filter``
        appears while a hot-swap target filters the browser.

    Gotchas:
        The first search of a root walks it on Live's main thread (a big
        library can take seconds and briefly freezes Live's UI); afterwards the
        cache answers instantly.  ``truncated`` means limits stopped the walk —
        narrow ``root``/``category`` or raise the limits.
    """
    browser = require_browser(ctx)
    if not isinstance(query, str) or not query.strip():
        raise BridgeError("bad_args", "query must be a non-empty string")
    limit = _check_int(limit, "limit", 1, 200)
    offset = _check_int(offset, "offset", 0)
    max_depth = _check_int(max_depth, "max_depth", 1, 30)
    max_visits = _check_int(max_visits, "max_visits", 100, 200000)
    max_seconds = _check_number(max_seconds, "max_seconds", 0.5, 60.0)
    _check_detail(detail)
    category = check_category(category)
    fmt = check_format(plugin_format)
    roots = normalize_roots(browser, root, category)
    if not roots:
        raise BridgeError("not_found", "none of the requested roots exist in this Live")
    ranked, stats = search_entries(browser, query, roots, category=category,
                                   loadable_only=bool(loadable_only), plugin_format_arg=fmt,
                                   max_depth=max_depth, max_visits=max_visits,
                                   max_seconds=max_seconds, refresh=bool(refresh))
    page = ranked[offset:offset + limit]
    result = {"query": query, "total": len(ranked), "offset": offset, "count": len(page),
              "results": [pair[1].info(detail) for pair in page],
              "searched": stats["searched"], "cached": stats["cached"],
              "visited": stats["visited"], "truncated": stats["truncated"]}
    if stats["skipped"]:
        result["skipped"] = stats["skipped"]
    if offset + limit < len(ranked):
        result["next_offset"] = offset + limit
    _note_hotswap(browser, result)
    return result


def _note_hotswap(browser, result):
    """Warn that a pending hot-swap target filters what the browser shows."""
    if hotswap_active(browser):
        target = _get(browser, "hotswap_target")
        result["hotswap_filter"] = ("Live's browser is filtered by the hot-swap target %r — "
                                    "only items that can replace it are listed; clear it "
                                    "with browser.hotswap(action='clear')"
                                    % str(_get(target, "name", "") or "?"))
    return result


@command("browser.load", mutating=True,
         doc="Load a browser item (by uri, path or search) onto a track; reports what changed")
def browser_load(ctx, uri=None, path=None, query=None, root=None, category=None, track=None,
                 slot=None, new_track=None, track_name=None, position="end", hotswap=False,
                 device=None, drum_pad=None, plugin_format=None,
                 max_seconds=DEFAULT_MAX_SECONDS):
    """Load an instrument, effect, preset, plug-in, drum kit, sample or clip.

    Args:
        uri / path / query: the item — exactly one.  ``query`` picks the best
            loadable search hit (see browser.search for ranking).
        root, category, plugin_format: narrow a ``query`` (as in browser.search;
            plugin duplicates resolve to VST3, then AU, then VST2 unless
            ``plugin_format`` says otherwise).
        track: target track (index, name, "master", path).  Default: the
            selected track.  The track is selected first — Live loads there.
        slot: clip slot index / scene name on that track to highlight before
            loading (samples and clips land in the highlighted slot of an
            audio track).
        new_track: "midi" or "audio" — create a track at the end and load onto it.
        track_name: name for the new track (default: the item's name).
        position: "end" (default), "before_selected" or "after_selected" — the
            device insert position (``track.view.device_insert_mode``, restored
            afterwards); null leaves Live's current mode.
        hotswap: replace a device instead of adding: the target is ``device``
            (index/name on ``track`` or a LOM path; with ``drum_pad`` a pad of
            that Drum Rack: MIDI note or pad name), else the current hot-swap target.
        max_seconds: time budget for the search walk.

    Returns:
        {"loaded": {name, uri, path}, "via": "uri"|"path"|"query", "track": {path,
         name, type}, "created_track"?: true, "inserted"?: [device summaries],
         "removed"?: [{name}], "changed"?: [device summaries + "was" (old name)
         or "reloaded": true — loading a device/preset over a device of the same
         kind keeps Live's device object and resets it in place],
         "clips"?: [clip summaries], "arrangement_clips"?, "new_tracks"?,
         "alternatives"?: [other hits], "notes"?: [...]}

    Gotchas:
        Loading an instrument onto a track that has one replaces it (see
        ``removed``); onto an audio track Live creates a new MIDI track
        (``new_tracks``).  Live Clips (.alc) always land on a new track with
        their own device chain (the clip is listed in ``clips``) — the target
        track/slot are ignored by Live.  A non-hot-swap load clears a pending
        hot-swap target first; a hot-swap load sets its target before looking
        the item up (Live filters the browser by the target).  An empty change
        set can mean Live is still loading — or, for samples and clips, that
        the Arrangement view is focused (they only load in the Session view;
        the ``notes`` say so).  After a hot-swap load Live keeps the new device
        as hot-swap target (the browser stays filtered) so the next
        ``hotswap=true`` load without ``device`` swaps it again; clear it with
        browser.hotswap(action="clear").
    """
    browser = require_browser(ctx)
    category = check_category(category)
    fmt = check_format(plugin_format)
    max_seconds = _check_number(max_seconds, "max_seconds", 0.5, 60.0)
    if position is not None and position not in INSERT_MODES:
        raise BridgeError("bad_args", "position must be one of %s or null"
                          % ", ".join(INSERT_MODES))
    if drum_pad is not None and not hotswap:
        raise BridgeError("bad_args", "drum_pad only applies with hotswap=true")
    prior_target = _get(browser, "hotswap_target")
    pre_notes = []
    if hotswap:
        if new_track is not None or slot is not None:
            raise BridgeError("bad_args", "hotswap cannot be combined with new_track or slot")
        if device is not None:
            track_obj = ctx.track(track) if track is not None else None
            target = resolve_target_device(ctx, track_obj, device)
            if drum_pad is not None:
                target = resolve_drum_pad(ctx, target, drum_pad)
        else:
            target = prior_target
            if target is None:
                raise BridgeError("invalid_state", "no hot-swap target — pass device (and "
                                  "optionally drum_pad) or set one with browser.hotswap")
        # Live filters the browser by the hot-swap target: set it before the lookup
        set_hotswap_target(browser, target)
        try:
            entry, how, alternatives = resolve_entry(browser, uri, path, query, root,
                                                     category, fmt, loadable=True,
                                                     max_seconds=max_seconds)
        except BridgeError:
            if not _same(prior_target, target):
                try:
                    set_hotswap_target(browser, prior_target)
                except BridgeError:
                    pass
            raise
        result = {"loaded": entry.info("minimal"), "via": how}
        host_track = _owning_track(ctx, target)
        changes = load_entry(ctx, browser, entry, host_track, hotswap_target=target)
        result["hotswap"] = True
        if host_track is not None:
            result["track"] = ctx.summarize(host_track, "minimal")
    else:
        if device is not None:
            raise BridgeError("bad_args", "device only applies with hotswap=true")
        if prior_target is not None:
            # a pending hot-swap target filters the browser and would swap instead of add
            set_hotswap_target(browser, None)
            pre_notes.append("cleared the hot-swap target so the item is added, not swapped in")
        entry, how, alternatives = resolve_entry(browser, uri, path, query, root, category,
                                                 fmt, loadable=True, max_seconds=max_seconds)
        result = {"loaded": entry.info("minimal"), "via": how}
        track_obj, created = _pick_track(ctx, track, new_track, track_name, stem(entry.name))
        clip_slot = ctx.clip_slot(track_obj, slot) if slot is not None else None
        changes = load_entry(ctx, browser, entry, track_obj, slot=clip_slot, position=position)
        result["track"] = ctx.summarize(track_obj, "minimal")
        if created:
            result["created_track"] = True
    if pre_notes:
        changes["notes"] = pre_notes + list(changes.get("notes", []))
    result.update(changes)
    if alternatives:
        result["alternatives"] = alternatives
    return result


def _owning_track(ctx, obj):
    """The track that (indirectly) holds a device / drum pad, or ``None``."""
    current = obj
    for _ in range(12):
        if current is None:
            return None
        if compat.has(current, "clip_slots") and compat.has(current, "mixer_device"):
            return current
        current = _get(current, "canonical_parent")
    return None


@command("browser.hotswap", doc="Hot-swap target: info, set (device / drum pad) or clear")
def browser_hotswap(ctx, action="info", track=None, device=None, drum_pad=None):
    """Inspect or change Live's hot-swap target (what the next load replaces).

    Args:
        action: "info" (default), "set" or "clear".
        track: track of the device (default: the selected track).
        device: device index/name on ``track``, or a LOM path
            (``"song.tracks[0].devices[1]"``, nested rack devices too).
        drum_pad: with a Drum Rack ``device``: MIDI note (36) or pad name — the
            pad becomes the target (loading a sample/instrument fills that pad).

    Returns:
        {"target": device summary | {kind:"drum_pad", name, note, path} | null,
         "filter_type": "instrument_hotswap"|..., "browse_mode"?: bool}

    Gotchas:
        Setting a target switches Live's browser into hot-swap mode
        (``browse_mode`` true) and *filters* it: roots that cannot replace the
        target list nothing (an instrument target empties audio_effects) until
        the target is cleared.  ``browser.load`` with ``hotswap=true`` then
        replaces the target; normal loads clear it first.  ``drum_pad`` also
        takes note names ("C1") and chain names.
    """
    browser = require_browser(ctx)
    if action not in ("info", "set", "clear"):
        raise BridgeError("bad_args", "action must be 'info', 'set' or 'clear'")
    if action == "set":
        if device is None:
            raise BridgeError("bad_args", "action='set' needs device (and optionally drum_pad)")
        track_obj = ctx.track(track) if track is not None else None
        target = resolve_target_device(ctx, track_obj, device)
        if drum_pad is not None:
            target = resolve_drum_pad(ctx, target, drum_pad)
        set_hotswap_target(browser, target)
    elif action == "clear":
        set_hotswap_target(browser, None)
    elif device is not None or drum_pad is not None:
        raise BridgeError("bad_args", "device/drum_pad only apply to action='set'")
    return hotswap_target_info(ctx, browser)


@command("browser.preview", doc="Preview (audition) a browser item, or stop the preview")
def browser_preview(ctx, uri=None, path=None, query=None, root=None, category=None,
                    stop=False):
    """Play a browser item through Live's preview (the headphone button).

    Args:
        uri / path / query: the item (one of them); ``root``/``category``
            narrow a query.
        stop: stop the current preview instead (no item needed).

    Returns:
        {"previewing": {name, uri, path}} or {"stopped": true}

    Gotchas:
        Only samples and clips make sound; Live's "Preview" switch in the
        browser must be on.  Previews play through the Cue output.
    """
    browser = require_browser(ctx)
    if stop:
        if any(v is not None for v in (uri, path, query)):
            raise BridgeError("bad_args", "stop=true takes no item")
        if not compat.has(browser, "stop_preview"):
            raise BridgeError("unsupported", "browser.stop_preview is not available")
        try:
            browser.stop_preview()
        except Exception as error:
            raise BridgeError("invalid_state", "Live could not stop the preview: %s" % error)
        return {"stopped": True}
    if not compat.has(browser, "preview_item"):
        raise BridgeError("unsupported", "browser.preview_item is not available")
    entry, _how, _alternatives = resolve_entry(browser, uri, path, query, root,
                                               check_category(category), None,
                                               loadable=query is not None)
    try:
        browser.preview_item(entry.item)
    except Exception as error:
        raise BridgeError("invalid_state", "Live could not preview %r: %s" % (entry.name, error))
    return {"previewing": entry.info("minimal")}


@command("browser.cache", doc="Browser search cache: info or clear")
def browser_cache(ctx, action="info"):
    """Inspect or clear the search cache.

    Args:
        action: "info" (default) or "clear".

    Returns:
        {"roots": {root: {"items", "complete", "age_s"}}, "uris": n, "ttl_s"}
        (after "clear": {"cleared": true}).

    Gotchas:
        Clear (or search with refresh=true) after adding files to a Places
        folder or installing packs/plug-ins; the cache also expires after 300 s.
    """
    if action not in ("info", "clear"):
        raise BridgeError("bad_args", "action must be 'info' or 'clear'")
    if action == "clear":
        clear_cache()
        return {"cleared": True}
    now = time.time()
    return {"roots": dict((name, {"items": len(index.entries), "complete": index.complete,
                                  "age_s": round(now - index.t, 1)})
                          for name, index in _INDEX.items()),
            "uris": len(_URIS), "ttl_s": CACHE_TTL}


def find_file_item(browser, file_path, roots=None, max_seconds=DEFAULT_MAX_SECONDS):
    """The browser item of an audio file on disk (browser items carry no path,
    so this matches by file name and prefers items whose folder names match
    the file's parent folders).  Returns an Entry or ``None``."""
    base = os.path.basename(str(file_path).replace("\\", "/"))
    if not base:
        return None
    parents = [p.lower() for p in str(file_path).replace("\\", "/").split("/")[:-1] if p]
    roots = roots or ("user_folders", "current_project", "user_library", "samples")
    roots = [r for r in roots if r in root_names(browser)]
    ranked, _stats = search_entries(browser, stem(base), roots, loadable_only=True,
                                    max_seconds=max_seconds)
    best = None
    best_score = -1
    for _score_value, entry in ranked:
        if entry.name.lower() != base.lower():
            continue
        folders = [p.lower() for p in entry.path.split("/")[1:-1]]
        score = 0
        for a, b in zip(reversed(folders), reversed(parents)):
            if a != b:
                break
            score += 1
        if score > best_score:
            best, best_score = entry, score
    return best
