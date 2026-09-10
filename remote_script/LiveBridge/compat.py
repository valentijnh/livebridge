"""Feature detection helpers.

The Live Object Model differs between Live versions and editions (Intro /
Standard / Suite).  Never branch on the version number when you can ask the
object itself — use :func:`has`.  See ``docs/ARCHITECTURE.md`` §10.
"""

import platform
import sys

from . import log

#: Class names Live uses for third party plugin wrappers: ``PluginDevice``
#: (VST2 and VST3) and ``AuPluginDevice`` (Audio Units, macOS).  Verified in
#: Ableton's factory scripts (``docs/LIVE_API_VERIFIED.md``).
PLUGIN_CLASS_NAMES = ("PluginDevice", "AuPluginDevice")

#: Max for Live device class names.
MAX_DEVICE_CLASS_NAMES = ("MxDeviceInstrument", "MxDeviceAudioEffect",
                          "MxDeviceMidiEffect")


def has(obj, name):
    """True when ``obj`` really exposes ``name``.

    ``hasattr`` on a LOM object can raise (Live raises ``RuntimeError`` for
    properties that are invalid in the current state), so this wraps it.
    """
    if obj is None:
        return False
    try:
        getattr(obj, name)
        return True
    except Exception:
        return False


def is_sequence(value):
    """True for collections: lists/tuples and Live's Boost ``*Vector`` types.

    On Live 12.4.5 every LOM collection (``song.tracks``, ``clip_slots``,
    ``devices``, ``arrangement_clips``, ``take_lanes``, mixer ``sends`` ...) is a
    ``Base.Vector`` (``type(song.tracks).__mro__ == (Vector, Boost.Python.instance,
    object)``) — neither a list nor a tuple, so ``isinstance(x, (list, tuple))``
    misses it.  The other vector classes (``BrowserItemVector``, ``StringVector``,
    ``IntU64Vector`` ...) follow the same naming.  Strings are never sequences.
    """
    if isinstance(value, (str, bytes)):
        return False
    if isinstance(value, (list, tuple)):
        return True
    return (type(value).__name__.endswith("Vector") and hasattr(value, "__len__")
            and hasattr(value, "__getitem__"))


def safe_getattr(obj, name, default=None):
    """``getattr`` that never raises — returns ``default`` on any failure."""
    if obj is None:
        return default
    try:
        value = getattr(obj, name)
    except Exception:
        return default
    return value


def safe_call(obj, name, *args, **kwargs):
    """Call ``obj.name(*args)`` if it exists; return ``(ok, result)``."""
    method = safe_getattr(obj, name)
    if not callable(method):
        return False, None
    try:
        return True, method(*args, **kwargs)
    except Exception:
        log.exception("call to %s.%s failed", type(obj).__name__, name)
        return False, None


def _application():
    try:
        import Live
        return Live.Application.get_application()
    except Exception:
        return None


def live_version():
    """Live's version as an ``(major, minor, bugfix)`` int tuple.

    Returns ``(0, 0, 0)`` when it cannot be determined (outside Live).
    """
    app = _application()
    if app is None:
        return (0, 0, 0)
    parts = []
    for getter in ("get_major_version", "get_minor_version", "get_bugfix_version"):
        method = safe_getattr(app, getter)
        try:
            parts.append(int(method()) if callable(method) else 0)
        except Exception:
            parts.append(0)
    return tuple(parts)


def live_version_string():
    """Live's version as ``"12.4.5"``."""
    app = _application()
    if app is not None:
        getter = safe_getattr(app, "get_version_string")
        if callable(getter):
            try:
                text = getter()
                if text:
                    return str(text)
            except Exception:
                pass
    return "%d.%d.%d" % live_version()


def variant():
    """``app.get_variant()`` (Live 12+): ``"Suite"``, ``"Standard"``,
    ``"Intro"``, ``"Lite"``, ``"Trial"`` or ``"Beta"`` — ``None`` when Live
    does not offer it."""
    app = _application()
    getter = safe_getattr(app, "get_variant")
    if not callable(getter):
        return None
    try:
        value = getter()
    except Exception:
        return None
    return str(value) if value else None


def is_suite():
    """Edition detection.

    Live 12 reports it through ``app.get_variant()``; older versions have no
    edition property, so this falls back to looking for Suite-only content (a
    non-empty ``browser.max_for_live`` root).  Returns ``True``, ``False`` or
    ``None`` when it really cannot tell.
    """
    name = variant()
    if name is not None:
        return name.lower() in ("suite", "trial", "beta")
    app = _application()
    browser = safe_getattr(app, "browser")
    if browser is None:
        return None
    root = safe_getattr(browser, "max_for_live")
    if root is None:
        return None
    children = safe_getattr(root, "children")
    if children is None:
        return None
    try:
        return len(children) > 0
    except Exception:
        return None


def edition():
    """``"suite"``, ``"standard"``, ``"intro"``, ``"lite"``, ``"trial"``,
    ``"beta"`` (from ``get_variant``), else ``"suite"`` / ``"standard/intro"``
    / ``"unknown"`` from the browser heuristic."""
    name = variant()
    if name is not None:
        return name.lower()
    suite = is_suite()
    if suite is None:
        return "unknown"
    return "suite" if suite else "standard/intro"


def is_plugin_device(device):
    """True when ``device`` is a VST/VST3/AU wrapper."""
    class_name = safe_getattr(device, "class_name", "")
    return str(class_name) in PLUGIN_CLASS_NAMES


def is_max_device(device):
    """True when ``device`` is a Max for Live device."""
    class_name = safe_getattr(device, "class_name", "")
    return str(class_name) in MAX_DEVICE_CLASS_NAMES


def live_enum(path):
    """Resolve ``"Clip.WarpMode"`` / ``"Track.Track.monitoring_states"`` to the
    enum class inside the ``Live`` module (``None`` when unavailable)."""
    try:
        import Live
    except Exception:
        return None
    obj = Live
    for part in str(path).split("."):
        obj = safe_getattr(obj, part)
        if obj is None:
            return None
    return obj


def enum_names(enum_class):
    """``{int value: name}`` for a Boost.Python-style Live enum (never raises)."""
    result = {}
    values = safe_getattr(enum_class, "values")
    if isinstance(values, dict):
        for number, member in values.items():
            name = safe_getattr(member, "name")
            try:
                result[int(number)] = str(name) if name else str(member)
            except (TypeError, ValueError):
                continue
    return result


def python_version():
    """The Python version Live embeds, e.g. ``"3.11.6"``."""
    return "%d.%d.%d" % sys.version_info[:3]


def platform_name():
    """``"Darwin"``, ``"Windows"`` or ``"Linux"`` plus the release."""
    try:
        return "%s %s" % (platform.system(), platform.release())
    except Exception:
        return sys.platform
