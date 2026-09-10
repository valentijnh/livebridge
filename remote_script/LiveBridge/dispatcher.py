"""Request validation and execution (``docs/PROTOCOL.md``).

Two halves, because they run on different threads:

* :func:`validate` runs on the **socket thread**.  It checks the envelope and
  the token — cheap, and it never touches the LOM.
* :meth:`Dispatcher.execute` runs on Live's **main thread**.  It looks the
  command up, validates the arguments, wraps mutating commands in an undo step,
  times the call and turns every exception into an error envelope.

Error types are exactly the ones in ``docs/PROTOCOL.md``: ``auth``,
``bad_request``, ``unknown_command``, ``bad_args``, ``not_found``,
``invalid_state``, ``unsupported``, ``timeout``, ``forbidden``, ``internal``.
"""

import hmac
import time

from . import log
from . import registry
from .registry import BridgeError

#: Protocol version this implementation speaks.
PROTOCOL_VERSION = 1


class Request(object):
    """A validated request."""

    __slots__ = ("id", "cmd", "args", "timeout", "raw")

    def __init__(self, id, cmd, args, timeout, raw=None):
        self.id = id
        self.cmd = cmd
        self.args = args
        self.timeout = timeout
        self.raw = raw

    def __repr__(self):
        return "<Request %s %s>" % (self.id, self.cmd)


def error_response(request_id, type, message, cmd=None, traceback_text=None,
                   ms=None):
    """Build an error envelope."""
    error = {"type": type, "message": str(message)}
    if cmd:
        error["cmd"] = cmd
    if traceback_text:
        error["traceback"] = traceback_text
    response = {"id": request_id, "ok": False, "error": error}
    if ms is not None:
        response["ms"] = ms
    return response


def ok_response(request_id, result, ms=None):
    """Build a success envelope."""
    response = {"id": request_id, "ok": True, "result": result}
    if ms is not None:
        response["ms"] = ms
    return response


class Dispatcher(object):
    """Validates and runs commands against a :class:`Context`.

    Args:
        ctx: the command Context (``song``, ``app``, ``browser``, ...).
        config: the :class:`~.config.Config` (token, timeouts, allow_eval).
    """

    def __init__(self, ctx, config):
        self.ctx = ctx
        self.config = config

    # -- socket thread ---------------------------------------------------
    def validate(self, message):
        """Check an incoming message.

        Returns ``(request, None)`` when it is usable, or ``(None, response)``
        with a ready-to-send error envelope.  Safe to call off the main thread.
        """
        if not isinstance(message, dict):
            return None, error_response(
                None, "bad_request",
                "request must be a JSON object, got %s" % type(message).__name__)
        request_id = message.get("id")
        if request_id is None:
            return None, error_response(None, "bad_request", "request is missing 'id'")
        if not isinstance(request_id, (str, int)):
            return None, error_response(None, "bad_request", "'id' must be a string")
        request_id = str(request_id)
        version = message.get("v", PROTOCOL_VERSION)
        if version not in (None, PROTOCOL_VERSION):
            return None, error_response(
                request_id, "bad_request",
                "unsupported protocol version %r (this bridge speaks %d)"
                % (version, PROTOCOL_VERSION))
        cmd = message.get("cmd")
        if not isinstance(cmd, str) or not cmd:
            return None, error_response(request_id, "bad_request",
                                        "request is missing 'cmd'")
        unknown = [k for k in message
                   if k not in ("id", "cmd", "args", "token", "timeout", "v")]
        if unknown:
            return None, error_response(
                request_id, "bad_request",
                "unknown request field%s %s" % ("s" if len(unknown) > 1 else "",
                                                ", ".join(sorted(unknown))), cmd)
        auth_error = self.check_token(message.get("token"))
        if auth_error is not None:
            return None, error_response(request_id, "auth", auth_error, cmd)
        args = message.get("args")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return None, error_response(
                request_id, "bad_args",
                "'args' must be a JSON object, got %s" % type(args).__name__, cmd)
        timeout = self.clamp_timeout(message.get("timeout"))
        return Request(request_id, cmd, args, timeout, message), None

    def check_token(self, token):
        """``None`` when the token is acceptable, else the error message.

        * No token configured on localhost: everything is accepted
          (``docs/ARCHITECTURE.md`` §7) — ``eval.python`` still refuses.
        * No token configured in LAN mode: nothing is accepted (defence in
          depth — :func:`~.config.load_config` already falls back to
          127.0.0.1 in that case).
        * Otherwise the token must match; compared in constant time.
        """
        expected = self.config.token
        if not expected:
            if getattr(self.config, "lan_mode", False):
                return ("LAN mode requires a token — set \"token\" in the LiveBridge "
                        "config.json (or re-run the installer with --network)")
            return None
        if token is None:
            return "this bridge requires a token (set LIVEBRIDGE_TOKEN for the MCP server)"
        if not isinstance(token, str) or not hmac.compare_digest(
                token.encode("utf-8"), expected.encode("utf-8")):
            return "invalid token"
        return None

    def clamp_timeout(self, value):
        """Clamp the client's timeout into ``[0.1, max_timeout]`` seconds."""
        default = self.config.default_timeout
        try:
            timeout = float(value)
        except (TypeError, ValueError):
            return default
        if timeout != timeout or timeout <= 0:
            return default
        return max(0.1, min(self.config.max_timeout, timeout))

    # -- main thread -----------------------------------------------------
    def execute(self, request):
        """Run a validated request.  **Live main thread only.**

        Returns a complete response envelope; never raises.
        """
        started = time.time()
        spec = registry.get(request.cmd)
        if spec is None:
            return error_response(
                request.id, "unknown_command",
                "unknown command %r — call system.commands for the list" % request.cmd,
                request.cmd, ms=_ms(started))
        try:
            kwargs = registry.validate_args(spec, request.args)
        except BridgeError as error:
            return error_response(request.id, error.type, error.message, request.cmd,
                                  ms=_ms(started))
        undo = spec.mutating and self.ctx is not None
        try:
            if undo:
                self.ctx.begin_undo_step()
            try:
                result = spec.func(self.ctx, **kwargs)
            finally:
                if undo:
                    self.ctx.end_undo_step()
        except BridgeError as error:
            log.debug("%s -> %s: %s", request.cmd, error.type, error.message)
            return error_response(request.id, error.type, error.message, request.cmd,
                                  ms=_ms(started))
        except TypeError as error:
            # Wrong argument types reaching the handler read as bad_args, but a
            # TypeError raised *inside* the handler is a real bug — keep the
            # traceback either way.
            text = log.current_traceback()
            log.exception("%s failed", request.cmd)
            return error_response(request.id, "bad_args", "%s: %s" % (request.cmd, error),
                                  request.cmd, text, ms=_ms(started))
        except Exception as error:
            text = log.current_traceback()
            log.exception("%s raised %s", request.cmd, type(error).__name__)
            return error_response(
                request.id, "internal", "%s: %s: %s"
                % (request.cmd, type(error).__name__, error),
                request.cmd, text, ms=_ms(started))
        except BaseException as error:
            # SystemExit / KeyboardInterrupt / GeneratorExit from a handler (``sys.exit()``
            # in a library) must never escape into Live's update_display: answer instead.
            text = log.current_traceback()
            log.error("%s raised %s — contained", request.cmd, type(error).__name__)
            return error_response(
                request.id, "internal", "%s: %s escaped the handler: %s"
                % (request.cmd, type(error).__name__, error),
                request.cmd, text, ms=_ms(started))
        return ok_response(request.id, result, _ms(started))

    def timeout_response(self, request):
        """The envelope sent when the main thread did not answer in time."""
        return error_response(
            request.id, "timeout",
            "Live did not answer within %.1fs — the command may still complete "
            "(is Live busy or showing a modal dialog?)" % request.timeout,
            request.cmd)


def _ms(started):
    return round((time.time() - started) * 1000.0, 3)
