"""Command registry: the ``@command`` decorator and handler auto-discovery.

Handler modules live in ``handlers/`` and are imported automatically by
:func:`discover` (``pkgutil.iter_modules``), so nobody ever edits a central
list.  A handler looks like this::

    from ..registry import command, BridgeError

    @command("tracks.list", mutating=False, doc="List all tracks")
    def tracks_list(ctx, include_returns=True, detail="summary"):
        \"\"\"List every track.

        Args:
            include_returns: also list return tracks.
            detail: "minimal" | "summary" | "full".
        Returns:
            A list of track summaries.
        \"\"\"
        return [ctx.summarize(t, detail) for t in ctx.song.tracks]

Rules (``docs/ARCHITECTURE.md`` §4):

* the first parameter is always ``ctx`` (the :class:`~.LiveBridge.Context`);
* every other parameter is filled from the request's ``args`` object by
  keyword — an unknown or missing argument yields ``error.type = "bad_args"``;
* ``mutating=True`` makes the dispatcher wrap the call in
  ``song.begin_undo_step()`` / ``end_undo_step()`` so it is one undo step;
* handlers return JSON-serialisable data only.
"""

import importlib
import inspect
import pkgutil

from . import log

#: Error ``type`` values allowed by ``docs/PROTOCOL.md``.
ERROR_TYPES = ("auth", "bad_request", "unknown_command", "bad_args", "not_found",
               "invalid_state", "unsupported", "timeout", "forbidden", "internal")


class BridgeError(Exception):
    """An error with a protocol error ``type``.

    ``raise BridgeError("not_found", "song.tracks[7]: index out of range (5 tracks)")``
    becomes exactly that error envelope.  Unknown types are coerced to
    ``"internal"`` so the wire format stays valid.
    """

    def __init__(self, type, message, data=None):
        Exception.__init__(self, message)
        self.type = type if type in ERROR_TYPES else "internal"
        self.message = str(message)
        self.data = data

    def to_dict(self, cmd=None, traceback_text=None):
        """The ``error`` object of a response envelope."""
        error = {"type": self.type, "message": self.message}
        if cmd:
            error["cmd"] = cmd
        if traceback_text:
            error["traceback"] = traceback_text
        if self.data is not None:
            error["data"] = self.data
        return error

    def __repr__(self):
        return "BridgeError(%r, %r)" % (self.type, self.message)


_EMPTY = object()


class CommandSpec(object):
    """One registered command."""

    __slots__ = ("name", "func", "mutating", "doc", "params", "accepts_kwargs",
                 "module")

    def __init__(self, name, func, mutating=False, doc=None):
        self.name = name
        self.func = func
        self.mutating = bool(mutating)
        self.doc = (doc or inspect.getdoc(func) or "").strip()
        self.module = getattr(func, "__module__", "")
        self.params, self.accepts_kwargs = _introspect(func)

    @property
    def namespace(self):
        return self.name.split(".", 1)[0]

    def param_names(self):
        return [p["name"] for p in self.params]

    def describe(self):
        """The dict returned by ``system.commands``."""
        return {
            "cmd": self.name,
            "doc": self.doc,
            "params": [dict(p) for p in self.params],
            "mutating": self.mutating,
        }

    def __repr__(self):
        return "<CommandSpec %s>" % self.name


def _introspect(func):
    """Parameter names + defaults of a handler, skipping ``ctx``."""
    params = []
    accepts_kwargs = False
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):  # pragma: no cover - builtins only
        return params, True
    for index, (name, parameter) in enumerate(signature.parameters.items()):
        if index == 0 and name in ("ctx", "context", "self"):
            continue
        if parameter.kind == parameter.VAR_KEYWORD:
            accepts_kwargs = True
            continue
        if parameter.kind == parameter.VAR_POSITIONAL:
            continue
        entry = {"name": name}
        if parameter.default is not parameter.empty:
            entry["default"] = _jsonable_default(parameter.default)
            entry["required"] = False
        else:
            entry["required"] = True
        params.append(entry)
    return params, accepts_kwargs


def _jsonable_default(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable_default(v) for v in value]
    if isinstance(value, dict):
        return dict((str(k), _jsonable_default(v)) for k, v in value.items())
    return repr(value)


#: name -> CommandSpec
_commands = {}
_discovered = False


def command(name, mutating=False, doc=None):
    """Register a handler under ``name`` (``"namespace.verb"``).

    Args:
        name: the wire command name, e.g. ``"tracks.list"``.
        mutating: wrap the call in an undo step (any change to the set).
        doc: overrides the function docstring in ``system.commands``.

    Raises:
        ValueError: on a malformed or duplicate name (logged as well, so the
            script keeps loading the other handlers).
    """
    def decorator(func):
        register(CommandSpec(name, func, mutating, doc))
        return func
    return decorator


def register(spec):
    """Add a :class:`CommandSpec` (used by :func:`command`)."""
    name = spec.name
    if not isinstance(name, str) or "." not in name or name.startswith("."):
        raise ValueError("command name %r must look like 'namespace.verb'" % (name,))
    existing = _commands.get(name)
    if existing is not None and existing.func is not spec.func:
        message = ("duplicate command %r (%s and %s)"
                   % (name, existing.module, spec.module))
        log.error(message)
        raise ValueError(message)
    _commands[name] = spec
    return spec


def get(name):
    """The :class:`CommandSpec` for ``name`` or ``None``."""
    return _commands.get(name)


def all_commands(namespace=None):
    """Every registered command, sorted by name; optionally one namespace."""
    specs = sorted(_commands.values(), key=lambda s: s.name)
    if namespace:
        specs = [s for s in specs if s.namespace == namespace]
    return specs


def namespaces():
    """Sorted list of registered namespaces."""
    return sorted(set(spec.namespace for spec in _commands.values()))


def clear():
    """Forget every command (tests)."""
    global _discovered
    _commands.clear()
    _discovered = False


def discover(package=None, force=False):
    """Import every module in ``handlers/`` so their decorators run.

    Args:
        package: dotted name of the handler package; defaults to
            ``LiveBridge.handlers`` relative to this module.
        force: re-import even when discovery already ran.

    Returns:
        The number of registered commands.  A handler module that fails to
        import is logged and skipped — one broken module must not take the
        whole bridge down.
    """
    global _discovered
    if _discovered and not force:
        return len(_commands)
    if package is None:
        package = __name__.rsplit(".", 1)[0] + ".handlers"
    try:
        handlers = importlib.import_module(package)
    except Exception:
        log.exception("could not import handler package %s", package)
        _discovered = True
        return len(_commands)
    for _finder, module_name, _is_pkg in pkgutil.iter_modules(handlers.__path__):
        if module_name.startswith("_"):
            continue
        full_name = "%s.%s" % (package, module_name)
        try:
            importlib.import_module(full_name)
        except Exception:
            log.exception("handler module %s failed to load", full_name)
    _discovered = True
    log.info("registered %d commands from %d namespaces",
             len(_commands), len(namespaces()))
    return len(_commands)


def validate_args(spec, args):
    """Turn a request's ``args`` object into keyword arguments.

    Raises:
        BridgeError: ``bad_args`` when ``args`` is not an object, when an
            unknown argument is passed (unless the handler takes ``**kwargs``)
            or when a required argument is missing.
    """
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise BridgeError("bad_args", "%s: 'args' must be a JSON object, got %s"
                          % (spec.name, type(args).__name__))
    for key in args:
        if not isinstance(key, str):
            raise BridgeError("bad_args", "%s: argument names must be strings" % spec.name)
    known = set(spec.param_names())
    if not spec.accepts_kwargs:
        unknown = sorted(set(args) - known)
        if unknown:
            raise BridgeError(
                "bad_args",
                "%s: unknown argument%s %s (accepts: %s)"
                % (spec.name, "s" if len(unknown) > 1 else "",
                   ", ".join(repr(u) for u in unknown),
                   ", ".join(spec.param_names()) or "none"))
    missing = [p["name"] for p in spec.params
               if p.get("required") and p["name"] not in args]
    if missing:
        raise BridgeError(
            "bad_args", "%s: missing required argument%s %s"
            % (spec.name, "s" if len(missing) > 1 else "",
               ", ".join(repr(m) for m in missing)))
    return dict(args)
