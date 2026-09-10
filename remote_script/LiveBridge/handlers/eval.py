"""Arbitrary Python inside Live — the last-resort escape hatch.

Gated by ``allow_eval`` in ``config.json`` **and** a configured token
(``docs/ARCHITECTURE.md`` §7: "always require the token for it").  The code
runs on Live's main thread, so it may touch the LOM freely — and it can also
freeze Live if you write an endless loop.
"""

import contextlib
import io

from .. import compat
from .. import log
from .. import serialize
from ..registry import BridgeError, command

#: Names every snippet gets for free.
SCOPE_NAMES = ("song", "app", "browser", "view", "ctx", "Live", "summarize",
               "resolve", "path_of")

#: The globals dict persists between calls so you can build state up.
_globals = {}


def _build_scope(ctx):
    try:
        import Live
    except ImportError:  # pragma: no cover - only outside Live
        Live = None
    scope = dict(_globals)
    scope.update({
        "__builtins__": __builtins__,
        "song": ctx.song,
        "app": ctx.app,
        "browser": ctx.browser,
        "view": ctx.view,
        "ctx": ctx,
        "Live": Live,
        "summarize": ctx.summarize,
        "resolve": ctx.resolve,
        "path_of": ctx.path_of,
    })
    return scope


@command("eval.python", mutating=True,
         doc="Execute Python inside Live (song/app/browser/ctx/Live in scope)")
def eval_python(ctx, code="", expr=None, detail="summary", reset=False):
    """Run Python on Live's main thread and return what it printed/produced.

    Args:
        code: statements to execute (may be empty when you only pass ``expr``).
        expr: a single expression evaluated *after* ``code``; its value is
            returned. Without it the result is ``null``.
        detail: detail level used when the result is a LOM object.
        reset: clear the persistent globals before running.

    Returns:
        {"result": <value>, "stdout": "<whatever was printed>"}

    Gotchas:
        * Requires ``allow_eval: true`` in the script's config.json **and** a
          ``token`` there (the installer writes one); every request then carries
          it. Without either: ``forbidden`` — a token-less bridge would let any
          local process run code inside Live.
        * ``sys.exit()`` / ``exit()`` / ``raise SystemExit`` (and
          ``KeyboardInterrupt``) are caught and answered as ``bad_args``; they
          never reach Live.
        * Names defined in ``code`` persist across calls (use ``reset`` to
          start clean); ``song``, ``app``, ``browser``, ``view``, ``ctx``,
          ``Live``, ``summarize``, ``resolve`` and ``path_of`` are always
          re-bound to the current values.
        * This runs on the main thread — a blocking loop freezes Live.
        * The whole call is one undo step.
    """
    if not ctx.config.allow_eval:
        raise BridgeError(
            "forbidden",
            "eval.python is disabled — set \"allow_eval\": true in the "
            "LiveBridge config.json next to the remote script and restart Live")
    if not ctx.config.token:
        raise BridgeError(
            "forbidden",
            "eval.python needs a token — this bridge has none, so any local process "
            "could run code inside Live. Re-run the installer (it writes a random "
            "\"token\" into the LiveBridge config.json) or add one there, give the same "
            "token to the MCP server (LIVEBRIDGE_TOKEN / ~/.livebridge/config.json) "
            "and restart Live")
    if code is None:
        code = ""
    if not isinstance(code, str):
        raise BridgeError("bad_args", "code must be a string")
    if expr is not None and not isinstance(expr, str):
        raise BridgeError("bad_args", "expr must be a string")
    if not code.strip() and not (expr or "").strip():
        raise BridgeError("bad_args", "pass 'code', 'expr' or both")
    if reset:
        _globals.clear()
    scope = _build_scope(ctx)
    buffer = io.StringIO()
    result = None
    try:
        with contextlib.redirect_stdout(buffer):
            with contextlib.redirect_stderr(buffer):
                if code.strip():
                    exec(compile(code, "<livebridge>", "exec"), scope)
                if expr and expr.strip():
                    result = eval(compile(expr.strip(), "<livebridge-expr>", "eval"), scope)
    except SyntaxError as error:
        raise BridgeError("bad_args", "syntax error in the snippet: %s" % error)
    except BridgeError:
        raise
    except (SystemExit, KeyboardInterrupt) as error:
        # Must never propagate: out of update_display it would reach Live itself.
        detail = str(error)
        raise BridgeError("bad_args", "the snippet called exit() / raised %s%s — "
                          "LiveBridge contained it; end a snippet by returning instead"
                          % (type(error).__name__, " (%s)" % detail if detail else ""))
    except Exception:
        printed = buffer.getvalue()
        if printed:
            log.warn("eval.python printed before failing: %s", printed.strip()[:2000])
        raise
    finally:
        for key, value in scope.items():
            if key not in SCOPE_NAMES and key != "__builtins__":
                _globals[key] = value
    stdout = buffer.getvalue()
    if len(stdout) > 200000:
        stdout = stdout[:200000] + "\n...[truncated]"
    if serialize.kind_of(result) is not None or compat.is_sequence(result):
        payload = ctx.summarize(result, detail)
    else:
        payload = serialize.scalar(result)
    return {"result": payload, "stdout": stdout}
