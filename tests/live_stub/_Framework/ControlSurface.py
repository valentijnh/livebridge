"""Fake ``_Framework.ControlSurface`` — mirrors Live 12's real base class.

Verified against the decompiled Live 12.4 ``_Framework/ControlSurface.py``
(``docs/LIVE_API_VERIFIED.md`` §1):

* ``__init__(c_instance=None, *a, **k)`` stores ``c_instance``;
* ``song()`` -> ``c_instance.song()``; ``application()`` ->
  ``Live.Application.get_application()``;
* ``schedule_message(delay_in_ticks, callback, parameter=None)`` does **not**
  run anything by itself: it queues a task that the base class's
  ``update_display()`` executes on a later tick (Live calls update_display
  about every 100 ms).  A subclass that overrides ``update_display`` without
  calling ``super().update_display()`` never gets its scheduled messages run —
  exactly like in Live.  The callback is called as ``callback(parameter)``
  when ``parameter`` is truthy, else ``callback()``.
* ``schedule_message`` is **not thread-safe** in Live (it mutates the task
  group the main thread iterates) — never call it from a socket thread.
* ``log_message(*message)`` writes ``"(ClassName) msg"`` to ``c_instance``;
  ``show_message(message)`` shows it in the status bar.
* ``disconnect()`` must be called by subclasses.
"""

import contextlib

import Live


class ControlSurface(object):
    """Base class for remote scripts."""

    def __init__(self, c_instance=None, *a, **k):
        self.canonical_parent = None
        self._c_instance = c_instance
        self._connected = True
        self._scheduled = []          # [remaining_ticks, callback, parameter]
        self._in_tick = False
        self._suppress_rebuild = False
        self.log_message("Initializing...")

    # -- Live services ---------------------------------------------------
    def song(self):
        """The current ``Live.Song.Song``."""
        return self._c_instance.song()

    def application(self):
        """The ``Live.Application.Application`` singleton."""
        return Live.Application.get_application()

    def schedule_message(self, delay_in_ticks, callback, parameter=None):
        """Queue ``callback`` for a later ``update_display`` tick (main thread)."""
        delay = int(delay_in_ticks)
        if not self._in_tick:
            delay -= 1
        self._scheduled.append([delay, callback, parameter])

    def log_message(self, *message):
        """Write a line into Live's ``Log.txt``."""
        text = "(%s) %s" % (self.__class__.__name__, " ".join(map(str, message)))
        if self._c_instance:
            self._c_instance.log_message(text)

    def show_message(self, message):
        """Show a message in Live's status bar."""
        self._c_instance.show_message(message)

    def instance_identifier(self):
        return self._c_instance.instance_identifier()

    def request_rebuild_midi_map(self):
        self._c_instance.request_rebuild_midi_map()

    @contextlib.contextmanager
    def suppressing_rebuild_requests(self):
        previous = self._suppress_rebuild
        self._suppress_rebuild = True
        try:
            yield
        finally:
            self._suppress_rebuild = previous

    @contextlib.contextmanager
    def component_guard(self):
        yield

    # -- lifecycle -------------------------------------------------------
    def update_display(self):
        """Called by Live roughly every 100 ms on the main thread.

        Runs the scheduled messages that are due (like the real task group):
        messages scheduled *during* this tick with delay 0 run in the same tick.
        """
        self._in_tick = True
        try:
            index = 0
            while index < len(self._scheduled):
                entry = self._scheduled[index]
                if entry[0] <= 0:
                    del self._scheduled[index]
                    callback, parameter = entry[1], entry[2]
                    if parameter:
                        callback(parameter)
                    else:
                        callback()
                    continue
                entry[0] -= 1
                index += 1
        finally:
            self._in_tick = False

    def refresh_state(self):
        pass

    def update(self):
        pass

    def disconnect(self):
        """Called by Live when the script is unloaded / reloaded."""
        self._connected = False
        self._scheduled = []

    def connect_script_instances(self, instances):
        pass

    def port_settings_changed(self):
        self.refresh_state()

    def receive_midi(self, midi_bytes):
        pass

    def build_midi_map(self, midi_map_handle):
        pass

    def suggest_input_port(self):
        return ""

    def suggest_output_port(self):
        return ""

    def can_lock_to_devices(self):
        return False

    # -- stub-only introspection -------------------------------------------
    @property
    def pending_scheduled_messages(self):
        """Stub-only: how many scheduled messages have not run yet."""
        return len(self._scheduled)
