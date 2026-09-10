"""The LiveBridge control surface: lifecycle, Context and the main-thread pump.

Threading model (``docs/ARCHITECTURE.md`` §3 as corrected in §10 and
``docs/LIVE_API_VERIFIED.md`` §1 — this is the critical part):

* Live's Object Model may only be touched from Live's **main thread**.
* :class:`~.server.BridgeServer` runs on daemon threads.  For every request it
  calls :meth:`LiveBridge.dispatch`, which validates the envelope (cheap, no
  LOM), appends a job to a lock-protected deque and waits on a
  ``threading.Event`` with the request's timeout.
* The main thread drains the deque from :meth:`update_display`, which Live
  calls about every 100 ms.  The socket threads **never** call
  ``schedule_message``: in Live's ``_Framework`` it only appends a task to the
  control surface's task group, which is not thread-safe and is itself run
  from ``ControlSurface.update_display`` — so it gains no latency and races
  with the main thread.  :meth:`update_display` always calls the base class
  so the framework's own scheduled tasks keep running.
* **GIL hand-off.** Inside Live the socket threads only get the GIL while the
  main thread runs Python, so a reader thread that has a request (or a finished
  response) waited several ticks — ~0.5 s per round trip measured on Live 12.4.5.
  While clients are connected, :meth:`update_display` therefore sleeps
  :data:`GIL_YIELD_SECONDS` before draining (readers enqueue what arrived) and
  again after running jobs (readers send the responses): ~0.1 s per round trip.
* Draining is idempotent, re-entrancy guarded and bounded
  (:data:`MAX_DRAIN_JOBS` commands or :data:`DRAIN_BUDGET_SECONDS`) so the UI
  stays responsive; a single long command still runs to completion.
* :meth:`dispatch` called *on* the main thread (tests, ``eval.python``)
  executes inline instead of queueing — waiting for itself would deadlock.
* Log lines produced on socket threads are buffered and handed to
  ``c_instance.log_message`` on the main thread (:func:`log.flush_pending`).
* Mutating commands are wrapped in ``song.begin_undo_step()`` /
  ``end_undo_step()`` so each command is exactly one undo step.
* Nothing here may raise into Live: every entry point is wrapped.
"""

import collections
import platform
import threading
import time

try:  # Live 9-12 remote scripts
    from _Framework.ControlSurface import ControlSurface
except ImportError:  # pragma: no cover - modern framework fallback
    from ableton.v2.control_surface import ControlSurface

from . import compat
from . import config as config_module
from . import dispatcher as dispatcher_module
from . import log
from . import lom
from . import registry
from . import resolve as resolve_module
from . import serialize
from . import server as server_module
from .registry import BridgeError

#: LiveBridge version (also reported by ``system.hello`` and the beacon).
VERSION = "1.0.0"

#: Most commands executed in one drain before yielding back to Live's UI.
MAX_DRAIN_JOBS = 32

#: Soft time budget for one drain, in seconds.
DRAIN_BUDGET_SECONDS = 0.04

#: How long the main thread releases the GIL around a drain while clients are
#: connected, so socket threads can enqueue requests and send responses in the
#: same tick (measured on Live 12.4.5: round trip 0.5 s -> 0.1 s).
GIL_YIELD_SECONDS = 0.001

#: When the port cannot be bound (the previous instance's connections still
#: linger after Live rebuilt the control surface, another Live holds it ...),
#: :meth:`LiveBridge.update_display` retries every :data:`REBIND_FAST_SECONDS`
#: for :data:`REBIND_FAST_PERIOD` seconds, then every :data:`REBIND_SLOW_SECONDS`.
REBIND_FAST_SECONDS = 1.0
REBIND_FAST_PERIOD = 180.0
REBIND_SLOW_SECONDS = 10.0


class _Job(object):
    """One queued request waiting for the main thread."""

    __slots__ = ("request", "event", "response", "abandoned", "queued_at")

    def __init__(self, request):
        self.request = request
        self.event = threading.Event()
        self.response = None
        self.abandoned = False
        self.queued_at = time.time()

    def finish(self, response):
        self.response = response
        self.event.set()


class Context(object):
    """What every handler receives as its first argument.

    Attributes:
        song: ``Live.Song.Song`` — the open set.
        app: ``Live.Application.Application``.
        browser: ``app.browser`` (may be ``None`` on very old versions).
        view: ``song.view``.
        script: the :class:`LiveBridge` control surface.
        config: the effective :class:`~.config.Config`.
        version: Live's version as an ``(major, minor, bugfix)`` tuple.

    Helpers: :meth:`resolve`, :meth:`path_of`, :meth:`summarize`, :meth:`get`,
    :meth:`set`, :meth:`call`, :meth:`log`, :meth:`has`, plus the argument
    coercers :meth:`track`, :meth:`scene`, :meth:`clip_slot`, :meth:`clip`,
    :meth:`device` and :meth:`parameter` that turn the loose arguments Claude
    sends (an index, a name or a path) into real LOM objects.
    """

    def __init__(self, script, config):
        self.script = script
        self.config = config
        self._undo_depth = 0

    # -- roots -----------------------------------------------------------
    @property
    def song(self):
        return self.script.song()

    @property
    def app(self):
        return self.script.application()

    @property
    def browser(self):
        return compat.safe_getattr(self.app, "browser")

    @property
    def view(self):
        return compat.safe_getattr(self.song, "view")

    @property
    def version(self):
        return compat.live_version()

    # -- generic LOM access ----------------------------------------------
    def resolve(self, path):
        """LOM object at ``path`` (see ``docs/ARCHITECTURE.md`` §5)."""
        return lom.resolve(path, self)

    def path_of(self, obj):
        """Best-effort canonical path of ``obj`` (``None`` when unknown)."""
        return lom.path_of(obj, self)

    def summarize(self, obj, detail="summary"):
        """Compact JSON summary of a LOM object or a list of them."""
        return serialize.summarize(obj, detail, self)

    def get(self, path, prop=None):
        """Raw value of ``path`` / ``path.prop``."""
        return lom.get(path, prop, self)

    def set(self, path, prop, value):
        """Set ``path.prop`` with coercion; returns the value after the write."""
        return lom.set_property(path, prop, value, self)

    def call(self, path, method, args=None, kwargs=None):
        """Call ``path.method(*args, **kwargs)``."""
        return lom.call(path, method, args, kwargs, self)

    def has(self, obj, name):
        """Feature detection — ``True`` when ``obj`` really exposes ``name``."""
        return compat.has(obj, name)

    def log(self, message, *args):
        """Write a line into Live's ``Log.txt``."""
        log.info(message, *args)

    def show_message(self, message):
        """Show a message in Live's status bar (bottom of the window)."""
        self.script.show_status(message)

    # -- undo ------------------------------------------------------------
    def begin_undo_step(self):
        """Open an undo step (nesting-safe; the dispatcher calls this)."""
        song = self.song
        if song is None:
            return
        if self._undo_depth == 0:
            ok, _ = compat.safe_call(song, "begin_undo_step")
            if not ok:
                return
        self._undo_depth += 1

    def end_undo_step(self):
        """Close the undo step opened by :meth:`begin_undo_step`."""
        if self._undo_depth <= 0:
            return
        self._undo_depth -= 1
        if self._undo_depth == 0:
            compat.safe_call(self.song, "end_undo_step")

    # -- argument coercion ------------------------------------------------
    def track(self, spec, include_returns=True, include_master=True):
        """Resolve a ``track`` argument to a Track.

        Delegates to :func:`LiveBridge.resolve.track`, the single track
        resolver every handler shares: an int index into ``song.tracks``
        (negative counts from the end), a name (exact, case-insensitive,
        unique prefix, then a unique contains/punctuation-free match), a
        return letter (``"A"``), ``"master"``/``"main"``, ``"selected"``, a
        LOM path (``"song.return_tracks[0]"``) or a Track object.

        Raises:
            BridgeError: ``not_found`` when nothing matches, ``bad_args`` for
                an unusable or ambiguous argument.
        """
        return resolve_module.track(self, spec, include_returns, include_master)

    def scene(self, spec):
        """Resolve a ``scene`` argument (index, integral float, name or path) to a Scene.

        Delegates to :func:`LiveBridge.resolve.scene`: names match exactly,
        case-insensitively, then by unique prefix / contains; an ambiguous
        name answers ``bad_args`` listing the candidates, a path that is not a
        scene answers ``bad_args``.
        """
        return resolve_module.scene(self, spec)

    def clip_slot(self, track, slot):
        """The clip slot at ``slot`` of ``track`` (both loosely typed)."""
        track_obj = self.track(track)
        slots = list(compat.safe_getattr(track_obj, "clip_slots", ()) or ())
        if not slots:
            raise BridgeError("invalid_state", "%s has no clip slots (return/master track?)"
                              % compat.safe_getattr(track_obj, "name", "track"))
        if isinstance(slot, str) and not slot.lstrip("-").isdigit():
            scene = self.scene(slot)
            index = resolve_module.index_in(
                compat.safe_getattr(self.song, "scenes", ()) or (), scene)
            if index is None:
                raise BridgeError("not_found", "scene %r is not in this set" % slot)
        else:
            if isinstance(slot, bool) or (isinstance(slot, float) and not slot.is_integer()):
                raise BridgeError("bad_args", "slot must be a whole scene index or a scene "
                                  "name, got %r" % (slot,))
            try:
                index = int(slot)
            except (TypeError, ValueError):
                raise BridgeError("bad_args", "slot must be an index or a scene name")
        if -len(slots) <= index < len(slots):
            return slots[index]
        raise BridgeError("not_found", "%s.clip_slots[%d]: index out of range (%d slots)"
                          % (self.path_of(track_obj) or "track", index, len(slots)))

    def clip(self, track, slot):
        """The clip in ``track``/``slot``; ``not_found`` when the slot is empty."""
        clip_slot = self.clip_slot(track, slot)
        clip = compat.safe_getattr(clip_slot, "clip")
        if clip is None:
            raise BridgeError("not_found", "%s: the clip slot is empty"
                              % (self.path_of(clip_slot) or "clip slot"))
        return clip

    def device(self, track, device):
        """A device on ``track`` by index, name or path.

        Delegates to :func:`LiveBridge.resolve.device` (the resolver the
        ``devices.*`` commands use): names are matched exactly, then
        case-insensitively, then by class name, unique prefix and unique
        substring, also inside racks; paths may point into chains.
        """
        if device is None or isinstance(device, bool):
            raise BridgeError("bad_args", "device must be an index, a name or a path")
        return resolve_module.device(self, track, device)

    def parameter(self, device, parameter):
        """A parameter of ``device`` by index, name or path.

        Delegates to :func:`LiveBridge.resolve.parameter` (the resolver the
        ``devices.*`` commands use).
        """
        return resolve_module.parameter(self, device, parameter)


class LiveBridge(ControlSurface):
    """The control surface Live instantiates.

    Live calls :meth:`__init__` once at startup (and again after a reload),
    :meth:`update_display` every ~100 ms and :meth:`disconnect` on shutdown.
    Everything else happens on the socket threads.
    """

    def __init__(self, c_instance, config=None, auto_start=True):
        """Set up logging, config, the registry, the server and the beacon.

        Args:
            c_instance: the opaque object Live hands to the script.
            config: an already-built :class:`~.config.Config` (tests); by
                default ``config.json`` next to this file is loaded.
            auto_start: set to ``False`` to construct without binding a port.
        """
        ControlSurface.__init__(self, c_instance)
        self._c_instance = c_instance
        #: ``threading.get_ident()`` of Live's main thread (the one that builds
        #: the script).  :meth:`dispatch` on this thread runs inline; ``None``
        #: forces every request through the queue (tests use that).
        self.main_thread_id = threading.get_ident()
        self._live_version = "0.0.0"
        self._queue = collections.deque()
        self._queue_lock = threading.Lock()
        self._draining = False
        self._running = False
        self._server = None
        self._beacon = None
        self._auto_start = bool(auto_start)
        self._bind_attempts = 0
        self._bind_error = None
        self._bind_failed_at = None
        self._next_bind_at = None
        self._started_at = time.time()
        self.config = None
        self.ctx = None
        self.dispatcher = None
        try:
            log.set_sink(getattr(c_instance, "log_message", None))
            log.set_main_thread(self.main_thread_id)
            # Read Live's version once, here on the main thread: the beacon
            # thread must never call into the LOM to get it.
            self._live_version = compat.live_version_string()
            self.config = config or config_module.load_config()
            self.ctx = Context(self, self.config)
            self.dispatcher = dispatcher_module.Dispatcher(self.ctx, self.config)
            registry.discover()
            self._running = True
            if auto_start:
                self._start_server()
                self._start_beacon()
            log.info("LiveBridge v%s %s %s:%d (Live %s, %d commands%s)",
                     VERSION, "ready on" if self._server or not auto_start
                     else "waiting to bind", self.config.host,
                     self._server.port if self._server else self.config.port,
                     compat.live_version_string(), len(registry.all_commands()),
                     ", token required" if self.config.requires_token else "")
            if self._server is not None or not auto_start:
                self.show_status("LiveBridge v%s ready on port %d"
                                 % (VERSION, self._server.port if self._server
                                    else self.config.port))
        except Exception:
            log.exception("LiveBridge failed to start")

    # -- lifecycle -------------------------------------------------------
    def _start_server(self):
        """Bind and start the TCP server; ``True`` on success.

        A failure is logged once (the first time) and retried from
        :meth:`update_display` (see :data:`REBIND_FAST_SECONDS`): on Windows
        ``SO_EXCLUSIVEADDRUSE`` refuses the port while the previous instance's
        accepted connections are in TIME_WAIT, which happens every time Live
        rebuilds the control surface with an MCP client connected.
        """
        server = server_module.BridgeServer(
            self.config.host, self.config.port, self.dispatch,
            max_clients=self.config.max_clients,
            idle_timeout=self.config.idle_timeout,
            authenticate=self._authenticate)
        self._bind_attempts += 1
        now = time.time()
        try:
            server.start()
        except OSError as error:
            first = self._bind_error is None
            self._bind_error = "%s" % (error,)
            if self._bind_failed_at is None:
                self._bind_failed_at = now
            fast = now - self._bind_failed_at < REBIND_FAST_PERIOD
            self._next_bind_at = now + (REBIND_FAST_SECONDS if fast else REBIND_SLOW_SECONDS)
            if first:
                log.error("could not bind %s:%d (%s) — retrying every %.0fs; is another "
                          "Live instance running with LiveBridge, or are the previous "
                          "script's connections still closing?",
                          self.config.host, self.config.port, error, REBIND_FAST_SECONDS)
                self.show_status("LiveBridge: port %d is in use — retrying" % self.config.port)
            else:
                log.debug("bind attempt %d failed: %s", self._bind_attempts, error)
            return False
        self._server = server
        if self._bind_error is not None:
            log.info("LiveBridge bound %s:%d after %d attempts", self.config.host,
                     server.port, self._bind_attempts)
            self.show_status("LiveBridge v%s ready on port %d" % (VERSION, server.port))
        self._bind_error = None
        self._bind_failed_at = None
        self._next_bind_at = None
        return True

    def _maybe_rebind(self):
        """Retry a failed bind (throttled); main thread, from :meth:`update_display`."""
        if (not self._running or not self._auto_start or self._server is not None
                or self._next_bind_at is None or time.time() < self._next_bind_at):
            return False
        return self._start_server()

    def _authenticate(self, message):
        """``True`` when ``message`` (``None`` = a new connection) passes the token check.

        Runs on socket threads; only reads the config (no LOM).
        """
        dispatcher = self.dispatcher
        if dispatcher is None:
            return False
        token = message.get("token") if isinstance(message, dict) else None
        return dispatcher.check_token(token) is None

    def bind_state(self):
        """``{bound, attempts, error, retry_in}`` — the TCP bind status (``system.status``)."""
        retry_in = None
        if self._server is None and self._next_bind_at is not None:
            retry_in = round(max(0.0, self._next_bind_at - time.time()), 1)
        return {"bound": self._server is not None, "attempts": self._bind_attempts,
                "error": self._bind_error, "retry_in": retry_in}

    def _start_beacon(self):
        if not self.config.beacon:
            return
        self._beacon = server_module.Beacon(self.beacon_payload,
                                            self.config.beacon_port)
        self._beacon.start()

    def beacon_payload(self):
        """The UDP discovery payload (``docs/PROTOCOL.md``)."""
        return {
            "livebridge": 1,
            "name": self.config.name,
            "host": server_module.local_ip(),
            "port": self._server.port if self._server else self.config.port,
            "live": self._live_version,
            "version": VERSION,
            "needs_token": bool(self.config.requires_token),
        }

    def disconnect(self):
        """Tear everything down; Live calls this on reload and on quit."""
        self._running = False
        # Wake the socket threads waiting on queued jobs first, so stopping the
        # server does not block Live's main thread ~1 s per such client join.
        self._fail_pending("LiveBridge is shutting down")
        try:
            if self._beacon is not None:
                self._beacon.stop()
        except Exception:
            log.exception("stopping the beacon failed")
        finally:
            self._beacon = None
        try:
            if self._server is not None:
                self._server.stop()
        except Exception:
            log.exception("stopping the server failed")
        finally:
            self._server = None
        self._fail_pending("LiveBridge is shutting down")
        log.info("LiveBridge v%s disconnected", VERSION)
        try:
            ControlSurface.disconnect(self)
        except Exception:
            log.exception("ControlSurface.disconnect failed")
        log.flush_pending()
        log.set_sink(None)
        log.set_main_thread(None)

    def show_status(self, message):
        """Show a message in Live's status bar (never raises)."""
        try:
            self.show_message(str(message))
        except Exception:
            pass

    # -- server side (any thread) ----------------------------------------
    def dispatch(self, message):
        """Handle one request end-to-end and return the response envelope.

        Called from the socket threads (and directly from the tests).  The
        actual command runs on Live's main thread: off the main thread this
        call enqueues the job and blocks on an ``Event`` until
        :meth:`update_display` has run it or the timeout expires; on the main
        thread itself it executes inline.
        """
        if self.dispatcher is None:
            return dispatcher_module.error_response(
                (message or {}).get("id") if isinstance(message, dict) else None,
                "internal", "LiveBridge did not start correctly — see Live's Log.txt")
        request, error = self.dispatcher.validate(message)
        if error is not None:
            return error
        if not self._running:
            return dispatcher_module.error_response(
                request.id, "invalid_state", "LiveBridge is shutting down", request.cmd)
        job = _Job(request)
        if self.main_thread_id is not None and threading.get_ident() == self.main_thread_id:
            self._execute(job)
            return job.response
        with self._queue_lock:
            self._queue.append(job)
        if job.event.wait(request.timeout):
            return job.response
        job.abandoned = True
        log.warn("timeout after %.1fs waiting for %s", request.timeout, request.cmd)
        return self.dispatcher.timeout_response(request)

    # -- main thread ------------------------------------------------------
    def update_display(self):
        """Live's ~100 ms main-thread tick: framework tasks, our queue, logs.

        Always runs the base class first — in ``_Framework`` that is what
        executes ``schedule_message`` callbacks and component tasks.  Also
        retries a failed port bind (:meth:`_maybe_rebind`).  Never raises into
        Live — not even ``SystemExit`` / ``KeyboardInterrupt``.
        """
        try:
            ControlSurface.update_display(self)
        except Exception:
            log.exception("ControlSurface.update_display failed")
        try:
            self._maybe_rebind()
            connected = self._has_clients()
            if connected:
                self._yield_gil()       # readers enqueue what arrived since the last tick
            executed = self._drain()
            if executed and connected:
                self._yield_gil()       # readers send the responses now, not next tick
        except BaseException as error:  # never propagate into Live's embedding
            log.error("update_display contained %s: %s", type(error).__name__, error)
        finally:
            log.flush_pending()

    def _has_clients(self):
        server = self._server
        try:
            return bool(server is not None and server.client_count)
        except Exception:
            return False

    @staticmethod
    def _yield_gil():
        """Release the GIL briefly so waiting socket threads run (see module docs)."""
        try:
            time.sleep(GIL_YIELD_SECONDS)
        except Exception:
            pass

    def pump(self):
        """Public alias of the drain, for tests and integration checks."""
        return self._drain()

    def _drain(self):
        """Run queued commands on the main thread; bounded and re-entrant-safe.

        Returns the number of commands executed.
        """
        if self._draining:
            return 0
        self._draining = True
        executed = 0
        ran = 0
        try:
            deadline = time.time() + DRAIN_BUDGET_SECONDS
            while True:
                with self._queue_lock:
                    job = self._queue.popleft() if self._queue else None
                if job is None:
                    break
                # Abandoned jobs (the client already timed out) still run so
                # Live's state matches what was asked; their response is
                # dropped.  They count towards the budget like any other.
                self._execute(job)
                if not job.abandoned:
                    executed += 1
                ran += 1
                if ran >= MAX_DRAIN_JOBS or time.time() >= deadline:
                    break
        except BaseException:
            log.exception("drain failed")
        finally:
            self._draining = False
        # Whatever is left (budget exhausted) runs on the next ~100 ms tick.
        return executed

    def _execute(self, job):
        """Run one job and always finish it (``BaseException`` included)."""
        try:
            response = self.dispatcher.execute(job.request)
        except BaseException as error:
            log.exception("dispatcher.execute crashed on %s", job.request.cmd)
            response = dispatcher_module.error_response(
                job.request.id, "internal",
                "%s: %s" % (type(error).__name__, error), job.request.cmd,
                log.current_traceback())
        job.finish(response)

    def _fail_pending(self, message):
        with self._queue_lock:
            jobs = list(self._queue)
            self._queue.clear()
        for job in jobs:
            job.finish(dispatcher_module.error_response(
                job.request.id, "invalid_state", message, job.request.cmd))

    # -- introspection ----------------------------------------------------
    @property
    def server(self):
        """The :class:`~.server.BridgeServer` (``None`` when it failed to bind)."""
        return self._server

    @property
    def beacon(self):
        """The :class:`~.server.Beacon` (``None`` when disabled)."""
        return self._beacon

    @property
    def port(self):
        """The port actually bound (useful with ``port: 0`` in tests)."""
        if self._server is not None:
            return self._server.port
        return self.config.port if self.config else None

    def info(self):
        """The payload of ``system.hello``."""
        version = compat.live_version()
        return {
            "name": "LiveBridge",
            "version": VERSION,
            "protocol": dispatcher_module.PROTOCOL_VERSION,
            "live": {"major": version[0], "minor": version[1], "bugfix": version[2],
                     "version": compat.live_version_string(),
                     "edition": compat.edition()},
            "python": compat.python_version(),
            "platform": "%s %s" % (platform.system(), platform.machine()),
            "machine": self.config.name if self.config else "",
            # effective: eval.python also needs a token (docs/ARCHITECTURE.md §7)
            "allow_eval": bool(self.config.eval_enabled) if self.config else False,
            "host": self.config.host if self.config else "",
            "port": self.port,
            "needs_token": bool(self.config.requires_token) if self.config else False,
            "commands": len(registry.all_commands()),
            "uptime": round(time.time() - self._started_at, 1),
        }
