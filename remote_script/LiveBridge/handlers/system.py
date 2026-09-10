"""Core system commands: hello, ping, commands, log, status, batch, dialogs.

These are the commands an MCP client uses to find out what it is talking to,
plus ``system.batch`` (many commands in one round trip and one undo step) and
``system.dialog`` / ``system.dialog_press`` (Live's modal message boxes).
"""

import json
import re

from .. import compat
from .. import dispatcher as dispatcher_module
from .. import log as log_module
from .. import registry
from ..registry import BridgeError, command

#: Most steps one ``system.batch`` may run.
BATCH_MAX_STEPS = 100

#: ``"$<step>"`` / ``"$<step>.key.0.key"`` — a whole-string reference to an earlier
#: step's result (the value keeps its JSON type).
_REFERENCE_RE = re.compile(r"^\$(-?\d+)((?:\.[^.]+)*)$")

#: ``"${<step>.key}"`` inside a longer string — replaced by the value's text
#: (``"${0.path}.devices[0]"``).
_TEMPLATE_RE = re.compile(r"\$\{(-?\d+)((?:\.[^.}]+)*)\}")


@command("system.hello", doc="Identify the bridge: versions, platform, capabilities")
def system_hello(ctx):
    """Handshake — call this first.

    Args:
        none.

    Returns:
        {name, version, protocol, live:{major,minor,bugfix,version,edition},
         python, platform, machine, allow_eval, host, port, needs_token,
         commands, uptime}

    Gotchas:
        ``live.edition`` comes from ``app.get_variant()`` in Live 12
        ("suite", "standard", "intro", "lite", "trial", "beta"); older
        versions fall back to a browser heuristic.  ``needs_token`` tells you
        whether every request must carry the ``token`` field.
    """
    return ctx.script.info()


@command("system.ping", doc="Cheap liveness check; returns the current song time")
def system_ping(ctx):
    """Round-trip check.

    Args:
        none.

    Returns:
        {"pong": true, "t": <current_song_time in beats>, "is_playing": bool}

    Gotchas:
        Clients may send this every 30 s as a keep-alive; the server closes
        connections that are idle for 10 minutes.
    """
    song = ctx.song
    return {
        "pong": True,
        "t": compat.safe_getattr(song, "current_song_time", 0.0),
        "is_playing": bool(compat.safe_getattr(song, "is_playing", False)),
    }


@command("system.commands", doc="List every registered command with its docs")
def system_commands(ctx, namespace=None, include_doc=True):
    """Introspect the whole command surface — the source of truth for docs.

    Args:
        namespace: only commands of this namespace (``"lom"``, ``"tracks"``...).
        include_doc: set to False for a much smaller listing.

    Returns:
        {"count": n, "namespaces": [...],
         "commands": [{cmd, doc?, params:[{name, default?, required}], mutating}]}

    Gotchas:
        ``params`` is introspected from the handler signature; ``ctx`` is never
        listed. Arguments are always passed by keyword.
    """
    if namespace is not None and not isinstance(namespace, str):
        raise BridgeError("bad_args", "namespace must be a string")
    specs = registry.all_commands(namespace)
    if namespace and not specs:
        raise BridgeError("not_found", "no commands in namespace %r (have: %s)"
                          % (namespace, ", ".join(registry.namespaces())))
    commands = []
    for spec in specs:
        entry = spec.describe()
        if not include_doc:
            entry.pop("doc", None)
        commands.append(entry)
    return {"count": len(commands), "namespaces": registry.namespaces(),
            "commands": commands}


@command("system.log", doc="Write a line into Live's Log.txt (and read it back)")
def system_log(ctx, message=None, level="info", tail=0):
    """Write to Live's log, and optionally read the bridge's own log history.

    Args:
        message: the line to write (optional when you only want ``tail``).
        level: "debug", "info", "warn" or "error".
        tail: return the last N lines LiveBridge logged this session (0 = none).

    Returns:
        {"ok": true, "lines": [...]} — ``lines`` only when ``tail`` > 0.

    Gotchas:
        The log file lives next to Live's preferences (see
        ``docs/ARCHITECTURE.md`` §8); only lines written by this bridge can be
        read back.
    """
    if message is not None:
        if not isinstance(message, str):
            message = str(message)
        writer = {"debug": log_module.debug, "info": log_module.info,
                  "warn": log_module.warn, "error": log_module.error}.get(
                      str(level).lower())
        if writer is None:
            raise BridgeError("bad_args",
                              "level must be one of debug, info, warn, error")
        writer("%s", message)
    result = {"ok": True}
    try:
        count = int(tail)
    except (TypeError, ValueError):
        raise BridgeError("bad_args", "tail must be an integer")
    if count > 0:
        result["lines"] = log_module.history(min(count, 400))
    return result


@command("system.status", doc="Server status: clients, queue, config (token redacted)")
def system_status(ctx):
    """What the bridge itself is doing right now.

    Args:
        none.

    Returns:
        {config: {...}, server: {running, host, port, clients, authenticated,
         pending, max_clients}, bind: {bound, attempts, error, retry_in},
         beacon: {running, port}, eval: bool, dialog: {open, count, message,
         buttons}, commands: n}

    Gotchas:
        The token is redacted; ``server`` is ``null`` when the port could not
        be bound (check Live's Log.txt) — ``bind`` then says why and when the
        next retry is. ``clients`` counts every open connection, ``pending``
        the ones that have not sent a valid token yet (closed after 10 s).
        ``eval`` is whether ``eval.python`` runs (``allow_eval`` **and** a
        token). ``dialog.open`` true means a modal dialog is up — see
        ``system.dialog``.
    """
    script = ctx.script
    server = script.server
    beacon = script.beacon
    bind_state = getattr(script, "bind_state", None)
    return {
        "config": ctx.config.to_dict(),
        "server": {
            "running": bool(server and server.running),
            "host": ctx.config.host,
            "port": script.port,
            "clients": server.client_count if server else 0,
            "authenticated": getattr(server, "authenticated_count", server.client_count),
            "pending": getattr(server, "pending_count", 0),
            "max_clients": ctx.config.max_clients,
        } if server else None,
        "bind": bind_state() if callable(bind_state) else None,
        "beacon": {"running": bool(beacon and beacon.running),
                   "port": ctx.config.beacon_port} if beacon else None,
        "eval": bool(ctx.config.allow_eval and ctx.config.token),
        "dialog": _dialog_state(ctx),
        "commands": len(registry.all_commands()),
    }


# --------------------------------------------------------------------------
# modal dialogs
# --------------------------------------------------------------------------

def _dialog_state(ctx):
    app = ctx.app
    count = compat.safe_getattr(app, "open_dialog_count", 0) or 0
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = 0
    message = compat.safe_getattr(app, "current_dialog_message", "") or ""
    buttons = compat.safe_getattr(app, "current_dialog_button_count", 0) or 0
    try:
        buttons = int(buttons)
    except (TypeError, ValueError):
        buttons = 0
    return {"open": count > 0, "count": count, "message": str(message),
            "buttons": buttons if count > 0 else 0}


@command("system.dialog", doc="Read Live's open modal dialog: message and button count")
def system_dialog(ctx):
    """Is a modal dialog (message box) open in Live, and what does it say?

    Args:
        none.

    Returns:
        {"open": bool, "count": <open dialogs>, "message": "<text of the current
        dialog>", "buttons": <number of buttons, 0 when none is open>}

    Gotchas:
        * Uses ``Application.open_dialog_count`` / ``current_dialog_message`` /
          ``current_dialog_button_count`` (the API Push uses to mirror Live's
          dialogs). Button labels are not exposed — only their count; index 0
          is the first (leftmost/default) button.
        * A dialog that blocks Live's main thread also blocks this command:
          it then times out instead of answering. When commands suddenly time
          out, ask the user to look at Live's window.
        * ``message`` may still hold the last dialog's text right after it
          closed; trust ``open``.
    """
    return _dialog_state(ctx)


@command("system.dialog_press", mutating=True,
         doc="Press a button on Live's current modal dialog (ask the user first)")
def system_dialog_press(ctx, button, expect=None):
    """Answer Live's current modal dialog by pressing one of its buttons.

    Args:
        button: button index, 0-based (``0`` = first button), below the
            ``buttons`` count reported by ``system.dialog``.
        expect: optional text that must appear in the dialog's message
            (case-insensitive) — when it does not, nothing is pressed. Pass it
            so a different dialog that popped up meanwhile is never answered.

    Returns:
        {"pressed": <index>, "message": "<text of the dialog that was answered>",
         "open_after": <dialogs still open>}

    Gotchas:
        * Dialogs can ask destructive questions ("Save changes?", "Delete?",
          "Replace?"): read ``system.dialog`` first and **confirm with the user**
          which button to press — button labels are not available, only the
          message and the count.
        * ``invalid_state`` when no dialog is open or ``expect`` does not match;
          ``bad_args`` for an index outside ``0..buttons-1``.
        * A dialog that blocks Live's main thread blocks this command too (it
          times out); the user must answer that one in Live.
    """
    if isinstance(button, bool) or not isinstance(button, (int, float)) or (
            isinstance(button, float) and not button.is_integer()):
        raise BridgeError("bad_args", "button must be a whole button index (0 = first)")
    button = int(button)
    if expect is not None and not isinstance(expect, str):
        raise BridgeError("bad_args", "expect must be a string")
    app = ctx.app
    if not compat.has(app, "press_current_dialog_button"):
        raise BridgeError("unsupported", "this Live version cannot press dialog buttons")
    state = _dialog_state(ctx)
    if not state["open"]:
        raise BridgeError("invalid_state", "no dialog is open in Live")
    if expect and expect.strip().lower() not in state["message"].lower():
        raise BridgeError("invalid_state", "the open dialog says %r, which does not contain "
                          "%r — nothing was pressed" % (state["message"], expect))
    if state["buttons"] and not 0 <= button < state["buttons"]:
        raise BridgeError("bad_args", "button must be 0..%d (the dialog has %d buttons), got %d"
                          % (state["buttons"] - 1, state["buttons"], button))
    if button < 0:
        raise BridgeError("bad_args", "button must be 0 or more, got %d" % button)
    try:
        app.press_current_dialog_button(button)
    except (RuntimeError, ValueError, IndexError) as error:
        raise BridgeError("invalid_state", "Live refused to press button %d: %s"
                          % (button, error))
    return {"pressed": button, "message": state["message"],
            "open_after": _dialog_state(ctx)["count"]}


# --------------------------------------------------------------------------
# batch
# --------------------------------------------------------------------------

def _lookup_reference(step, keys, index, outputs, text):
    """Value of step ``step`` (walked by ``keys``) for step ``index``; ``BridgeError`` if unusable."""
    step = int(step)
    target = index + step if step < 0 else step
    if not 0 <= target < index:
        raise BridgeError("bad_args", "step %d: %r refers to step %d — only earlier steps "
                          "(0..%d, or -1 for the previous one) can be referenced"
                          % (index, text, target, index - 1))
    if target not in outputs:
        raise BridgeError("bad_args", "step %d: %r refers to step %d, which failed"
                          % (index, text, target))
    value = outputs[target]
    walked = "$%d" % target
    for key in [k for k in keys.split(".") if k]:
        if isinstance(value, dict):
            if key not in value:
                raise BridgeError("bad_args", "step %d: %s has no key %r (keys: %s)"
                                  % (index, walked, key, ", ".join(sorted(map(str, value)))))
            value = value[key]
        elif isinstance(value, list):
            try:
                value = value[int(key)]
            except (ValueError, IndexError):
                raise BridgeError("bad_args", "step %d: %s is a list of %d — %r is not an "
                                  "index in it" % (index, walked, len(value), key))
        else:
            raise BridgeError("bad_args", "step %d: %s is %r, there is no %r in it"
                              % (index, walked, value, key))
        walked = "%s.%s" % (walked, key)
    return value


def _as_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, bool) or value is None:
        return json.dumps(value)
    if isinstance(value, (int, float)):
        return repr(value)
    return json.dumps(value, sort_keys=True, default=str)


def _substitute(value, index, outputs):
    """Replace ``"$n..."`` / ``"${n...}"`` references (``"$$"`` escapes) inside ``value``."""
    if isinstance(value, str):
        if value.startswith("$$"):
            return value[1:]
        match = _REFERENCE_RE.match(value) or _TEMPLATE_RE.fullmatch(value)
        if match:
            return _lookup_reference(match.group(1), match.group(2), index, outputs, value)
        if "${" not in value:
            return value
        return _TEMPLATE_RE.sub(
            lambda m: _as_text(_lookup_reference(m.group(1), m.group(2), index, outputs,
                                                 m.group(0))), value)
    if isinstance(value, dict):
        return dict((k, _substitute(v, index, outputs)) for k, v in value.items())
    if isinstance(value, list):
        return [_substitute(v, index, outputs) for v in value]
    return value


@command("system.batch", mutating=True,
         doc="Run several commands in one round trip, on one main-thread pass, as one undo step")
def system_batch(ctx, commands, stop_on_error=True):
    """Run a list of bridge commands back to back — one request, one undo step.

    Args:
        commands: list of steps, each ``{"cmd": "tracks.create", "args": {...}}``
            (``args`` optional; a bare ``"cmd"`` string is a step without args).
            At most 100 steps. A string argument that is exactly ``"$<n>"`` or
            ``"$<n>.<key>.<key>..."`` is replaced by (a part of) step ``n``'s
            result, keeping its type — ``n`` is 0-based, ``-1`` is the previous
            step, keys walk dicts and list indices: ``{"track": "$0.path"}``,
            ``{"index": "$-1.index"}``. Inside a longer string use
            ``"${<n>.<key>}"``: ``{"path": "${0.path}.devices[0]"}``. Start a
            literal string with ``"$$"`` to send it with one ``"$"`` less.
        stop_on_error: stop at the first failing step (default). ``false`` runs
            every step and reports each outcome.

    Returns:
        {"count": <steps>, "ran": <steps executed>, "ok": <succeeded>,
         "failed": <failed>, "results": [{"index", "cmd", "ok", "result" |
         "error": {type, message}}], "stopped_at"?: <index of the failing step>}

    Gotchas:
        * The whole batch is **one undo step** and there is no rollback: steps
          before a failure stay applied (one Ctrl+Z / ``transport.undo``
          reverts them all).
        * Every step runs in the same main-thread tick. Next-tick state (the
          playhead after ``transport.play``, ``is_playing``, loop/punch, clip
          recording) reads stale inside the batch.
        * The request ``timeout`` covers the whole batch and Live's UI is busy
          meanwhile — pass a larger timeout for long batches (browser loads,
          plug-ins) and keep them focused.
        * Each step is checked like a request of its own (unknown arguments →
          ``bad_args`` for that step); ``system.batch`` cannot be nested.
    """
    if not isinstance(commands, list) or not commands:
        raise BridgeError("bad_args", "commands must be a non-empty list of "
                          "{\"cmd\": ..., \"args\": {...}} steps")
    if len(commands) > BATCH_MAX_STEPS:
        raise BridgeError("bad_args", "at most %d steps per batch, got %d — split it"
                          % (BATCH_MAX_STEPS, len(commands)))
    dispatcher = getattr(ctx.script, "dispatcher", None)
    if dispatcher is None:
        raise BridgeError("invalid_state", "the dispatcher is not running")
    results = []
    outputs = {}
    failed = 0
    stopped_at = None
    for index, step in enumerate(commands):
        if isinstance(step, str):
            step = {"cmd": step}
        cmd = step.get("cmd") if isinstance(step, dict) else None
        entry = {"index": index, "cmd": cmd if isinstance(cmd, str) else None}
        try:
            if not isinstance(step, dict):
                raise BridgeError("bad_args", "step %d must be an object "
                                  "{\"cmd\": ..., \"args\": {...}}" % index)
            unknown = sorted(k for k in step if k not in ("cmd", "args"))
            if unknown:
                raise BridgeError("bad_args", "step %d: unknown key%s %s (a step has 'cmd' "
                                  "and 'args')" % (index, "s" if len(unknown) > 1 else "",
                                                   ", ".join(unknown)))
            if not isinstance(cmd, str) or not cmd:
                raise BridgeError("bad_args", "step %d is missing 'cmd'" % index)
            if cmd == "system.batch":
                raise BridgeError("bad_args", "step %d: system.batch cannot be nested" % index)
            args = step.get("args")
            if args is None:
                args = {}
            if not isinstance(args, dict):
                raise BridgeError("bad_args", "step %d: 'args' must be an object" % index)
            args = _substitute(args, index, outputs)
        except BridgeError as error:
            response = dispatcher_module.error_response(None, error.type, error.message)
        else:
            request = dispatcher_module.Request("batch-%d" % index, cmd, args, 0.0)
            response = dispatcher.execute(request)
        if response.get("ok"):
            outputs[index] = response.get("result")
            entry["ok"] = True
            entry["result"] = response.get("result")
        else:
            failed += 1
            error = dict(response.get("error") or {})
            error.pop("cmd", None)
            entry["ok"] = False
            entry["error"] = error
        results.append(entry)
        if not entry["ok"] and stop_on_error:
            stopped_at = index
            break
    summary = {"count": len(commands), "ran": len(results), "ok": len(results) - failed,
               "failed": failed, "results": results}
    if stopped_at is not None:
        summary["stopped_at"] = stopped_at
    return summary
