"""Logging for the LiveBridge remote script.

Everything goes into Live's ``Log.txt`` through ``c_instance.log_message`` (the
only logging channel a remote script has).  A small in-memory ring buffer keeps
the last lines so ``system.log`` / tests can read them back without touching
the file system.

``c_instance`` is part of Live, so it is only called from Live's main thread:
lines logged on the socket/beacon threads are parked in a bounded pending
buffer and handed to Live by :func:`flush_pending`, which
``LiveBridge.update_display`` calls every tick.  The ring buffer is updated
immediately from any thread.

Usage::

    from . import log
    log.set_sink(c_instance.log_message)     # done once by LiveBridge.__init__
    log.info("ready on %s:%d", host, port)
    log.exception("while handling %s", cmd)  # adds the traceback

No module ever prints; ``docs/ARCHITECTURE.md`` §12 forbids it.
"""

import collections
import sys
import threading
import traceback

PREFIX = "LiveBridge: "

LEVELS = ("debug", "info", "warn", "error")

_sink = None
_level = "info"
_buffer = collections.deque(maxlen=400)
_main_thread = None
_pending = collections.deque(maxlen=1000)


def set_sink(sink):
    """Install the callable that receives finished log lines.

    ``sink`` is normally ``c_instance.log_message``.  Pass ``None`` to log to
    the ring buffer only (what the tests do by default).
    """
    global _sink
    _sink = sink


def set_main_thread(ident):
    """Declare which thread may call the sink (Live's main thread).

    ``ident`` is a ``threading.get_ident()`` value, or ``None`` to allow every
    thread (the default — used outside Live and by most tests).
    """
    global _main_thread
    _main_thread = ident


def flush_pending():
    """Hand buffered off-main-thread lines to the sink.  Main thread only.

    Returns the number of lines flushed; never raises.
    """
    sink = _sink
    flushed = 0
    while _pending:
        try:
            line = _pending.popleft()
        except IndexError:
            break
        flushed += 1
        if sink is None:
            continue
        try:
            sink(line)
        except Exception:
            pass
    return flushed


def pending_count():
    """How many lines wait for :func:`flush_pending`."""
    return len(_pending)


def set_level(level):
    """Set the minimum level: ``debug``, ``info``, ``warn`` or ``error``."""
    global _level
    level = str(level).lower()
    if level not in LEVELS:
        level = "info"
    _level = level


def get_level():
    return _level


def history(limit=100):
    """Return the last ``limit`` log lines (newest last)."""
    lines = list(_buffer)
    if limit is not None and limit > 0:
        lines = lines[-int(limit):]
    return lines


def clear():
    """Drop the in-memory history (tests)."""
    _buffer.clear()


def _emit(level, message, args):
    if LEVELS.index(level) < LEVELS.index(_level):
        return
    if args:
        try:
            message = message % args
        except Exception:
            message = "%s %r" % (message, (args,))
    line = "%s[%s] %s" % (PREFIX, level, message)
    _buffer.append(line)
    sink = _sink
    if sink is None:
        return
    if _main_thread is not None and threading.get_ident() != _main_thread:
        _pending.append(line)
        return
    if _pending:
        flush_pending()
    try:
        sink(line)
    except Exception:
        # Logging must never take Live down, not even when the sink is gone
        # because the script is being reloaded.
        pass


def debug(message, *args):
    """Log a developer-level message (hidden unless level == 'debug')."""
    _emit("debug", message, args)


def info(message, *args):
    """Log a normal lifecycle message."""
    _emit("info", message, args)


def warn(message, *args):
    """Log something suspicious that did not stop the script."""
    _emit("warn", message, args)


def error(message, *args):
    """Log a failure."""
    _emit("error", message, args)


def exception(message, *args):
    """Log a failure together with the traceback of the exception being
    handled.  Call it from inside an ``except`` block."""
    _emit("error", message, args)
    try:
        text = traceback.format_exc()
    except Exception:
        text = "<no traceback available>"
    for line in text.rstrip().splitlines():
        _emit("error", "    %s", (line,))


def current_traceback():
    """The traceback of the exception being handled, as a string.

    Returns ``""`` when there is nothing to format — never raises.
    """
    try:
        if sys.exc_info()[0] is None:
            return ""
        return traceback.format_exc()
    except Exception:
        return ""
