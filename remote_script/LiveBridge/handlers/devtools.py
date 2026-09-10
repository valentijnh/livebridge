"""Developer tools: hot-reload LiveBridge code inside a running Live.

Live only imports a Remote Script when it starts (or when the control surface is
re-selected), so without this every code change would need a Live restart.
``system.reload`` re-imports the helper modules and the handler modules in place
and rebuilds the command table.  The TCP server, the dispatcher and the
``LiveBridge`` control-surface instance are deliberately NOT reloaded (they own
threads and sockets) — changes to ``server.py``, ``dispatcher.py``,
``LiveBridge.py``, ``config.py`` or ``registry.py`` still need a Live restart.

Typical dev loop (see ``tests/live_dev.py``): edit the repo, copy the changed
files into Live's ``Remote Scripts/LiveBridge`` folder, call ``system.reload``.
"""

import importlib
import importlib.util
import os
import pkgutil
import sys

from .. import log
from .. import registry
from ..registry import BridgeError, command

#: Helper modules handlers use; reloaded first, in this order (dependencies first).
#: Every other top-level module of the package (e.g. ``plugin_racks_lib``) follows,
#: alphabetically — see :func:`helper_modules`.
HELPER_MODULES = ("compat", "lom", "serialize", "resolve")

#: Top-level modules that are never reloaded as helpers: they own threads,
#: sockets, the command table or the running control surface (Live restart).
NOT_RELOADED = ("__init__", "LiveBridge", "config", "dispatcher", "log", "registry", "server")

#: Never reloaded implicitly (it is the module running this command).
SELF_MODULE = "devtools"


def _package():
    """``"LiveBridge"`` (or whatever the script folder is called)."""
    return __name__.rsplit(".", 2)[0]


def _drop_bytecode(source_path):
    """Delete the cached .pyc for ``source_path`` so a same-size edit made within
    the same second (mtime granularity) is never served from stale bytecode."""
    try:
        cached = importlib.util.cache_from_source(source_path)
        if os.path.exists(cached):
            os.remove(cached)
    except Exception:  # read-only folder or no cache: the normal mtime check still applies
        pass


def _reload_core(ctx, package):
    """Reload ``LiveBridge.py`` and hot-swap the live Context object's class.

    The control-surface instance, the TCP server and the dispatcher keep running
    with their old code (they own threads/sockets); only ``Context`` - the object
    every handler receives - switches to the new class, in place, so every
    reference to it (dispatcher, eval scope) sees the new methods at once.
    """
    name = package + ".LiveBridge"
    module = sys.modules.get(name)
    if module is None:
        return {"error": "module %s is not loaded" % name}
    old_cls = type(ctx)
    try:
        if getattr(module, "__file__", None):
            _drop_bytecode(module.__file__)
        importlib.reload(module)
    except Exception as error:
        log.exception("system.reload: core module %s failed", name)
        return {"error": "%s: %s" % (type(error).__name__, error)}
    new_cls = getattr(module, "Context", None)
    swapped = False
    if new_cls is not None and old_cls.__name__ == "Context" and old_cls is not new_cls:
        try:
            ctx.__class__ = new_cls
            swapped = True
        except TypeError as error:
            return {"error": "could not swap the Context class: %s" % error}
    pkg = sys.modules.get(package)
    if pkg is not None:
        for attr in ("Context", "VERSION"):
            if hasattr(module, attr):
                setattr(pkg, attr, getattr(module, attr))
    return {"context_swapped": swapped,
            "note": "server, dispatcher and the control-surface class keep their old code until Live restarts"}


def helper_modules(package):
    """The helper modules ``system.reload`` reloads, dependencies first.

    :data:`HELPER_MODULES` in their fixed order, then every other top-level module
    of ``package`` (files handlers import, such as ``plugin_racks_lib``) except
    :data:`NOT_RELOADED`, alphabetically — so a new helper file is picked up
    without editing a list.
    """
    names = list(HELPER_MODULES)
    pkg = sys.modules.get(package) or importlib.import_module(package)
    paths = list(getattr(pkg, "__path__", []) or [])
    extra = sorted(name for _finder, name, is_pkg in pkgutil.iter_modules(paths)
                   if not is_pkg and not name.startswith("_") and name not in NOT_RELOADED
                   and name not in names)
    return names + extra


def _handler_modules(handlers_pkg):
    return sorted(name for _finder, name, _is_pkg in pkgutil.iter_modules(handlers_pkg.__path__)
                  if not name.startswith("_"))


@command("system.reload", mutating=False,
         doc="Hot-reload handler (and helper) modules inside Live without a restart")
def system_reload(ctx, modules=None, helpers=True, core=False):
    """Re-import LiveBridge handler modules in the running Live and rebuild the command table.

    Args:
        modules: list of handler module names to reload (e.g. ``["clips", "notes"]``);
            ``None`` reloads every module in ``handlers/`` except this one, and also
            imports handler files that were added after Live started.
        helpers: also reload the helper modules first (default ``True``):
            ``compat``, ``lom``, ``serialize``, ``resolve``, then every other
            top-level helper file such as ``plugin_racks_lib`` (``helper_modules``).
        core: also reload ``LiveBridge.py`` and hot-swap the running Context object
            to the new ``Context`` class (default ``False``) - use it after changing
            Context methods so no Live restart is needed.

    Returns:
        ``{"helpers": [...], "core": {...}|null, "reloaded": [...], "added": [...],
        "failed": {module: error}, "commands": <count>}``.

    Gotchas:
        * A module that fails to import keeps its previous commands registered, so a
          typo never takes the bridge down — fix it and reload again.
        * ``server.py``, ``dispatcher.py``, ``config.py``, ``registry.py`` and the
          control-surface class in ``LiveBridge.py`` are not reloaded; changes there
          need a Live restart. ``Context`` changes are covered by ``core=True``.
        * Handler modules that were not reloaded keep references to the old helper
          functions they imported by name; reload everything when in doubt.
    """
    if modules is not None:
        if isinstance(modules, str):
            modules = [modules]
        if not isinstance(modules, (list, tuple)) or not all(isinstance(m, str) for m in modules):
            raise BridgeError("bad_args", "modules must be a list of handler module names")
    package = _package()
    handlers_name = package + ".handlers"
    handlers_pkg = importlib.import_module(handlers_name)
    importlib.invalidate_caches()
    available = _handler_modules(handlers_pkg)
    if modules is None:
        targets = [m for m in available if m != SELF_MODULE]
    else:
        unknown = [m for m in modules if m not in available]
        if unknown:
            raise BridgeError("bad_args", "unknown handler module(s): %s (available: %s)"
                              % (", ".join(unknown), ", ".join(available)))
        targets = list(modules)

    reloaded_helpers = []
    failed = {}
    if helpers:
        for name in helper_modules(package):
            full = "%s.%s" % (package, name)
            module = sys.modules.get(full)
            try:
                if module is not None and getattr(module, "__file__", None):
                    _drop_bytecode(module.__file__)
                if module is None:
                    importlib.import_module(full)
                else:
                    importlib.reload(module)
                reloaded_helpers.append(name)
            except Exception as error:  # keep going: the old module object stays usable
                failed[name] = "%s: %s" % (type(error).__name__, error)
                log.exception("system.reload: helper %s failed", full)

    core_result = None
    if core:
        core_result = _reload_core(ctx, package)
        if core_result.get("error"):
            failed["LiveBridge"] = core_result["error"]

    reloaded, added = [], []
    for name in targets:
        full = "%s.%s" % (handlers_name, name)
        previous = dict((cmd, spec) for cmd, spec in registry._commands.items()
                        if spec.module == full)
        for cmd in previous:
            registry._commands.pop(cmd, None)
        try:
            module = sys.modules.get(full)
            _drop_bytecode(os.path.join(handlers_pkg.__path__[0], name + ".py"))
            if module is None:
                importlib.import_module(full)
                added.append(name)
            else:
                importlib.reload(module)
                reloaded.append(name)
        except Exception as error:
            # Restore the old commands so a broken edit never removes working ones.
            for cmd, spec in previous.items():
                registry._commands.setdefault(cmd, spec)
            failed[name] = "%s: %s" % (type(error).__name__, error)
            log.exception("system.reload: handler module %s failed", full)
    count = len(registry.all_commands())
    log.info("system.reload: helpers=%s reloaded=%s added=%s failed=%s -> %d commands",
             reloaded_helpers, reloaded, added, sorted(failed), count)
    return {"helpers": reloaded_helpers, "core": core_result, "reloaded": reloaded,
            "added": added, "failed": failed, "commands": count}
