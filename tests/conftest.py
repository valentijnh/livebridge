"""Shared test fixtures.

``sys.path`` is prepared **before** anything imports the remote script, so
``import Live`` / ``import _Framework`` resolve to the fakes in
``tests/live_stub`` and ``import LiveBridge`` to ``remote_script/LiveBridge``.

Fixtures:

``song``          a realistic set (see :func:`song` for the exact shape)
``app``/``browser`` the fake application and its browser
``bridge``        a fully constructed LiveBridge; the test thread *is* its
                  main thread, so ``bridge.dispatch(request) -> response``
                  executes inline
``bridge_factory`` build extra bridges (own config, queue mode, token, ...)
``tcp_bridge``    the real TCP server on an ephemeral localhost port, driven by
                  a background "main thread" pump calling ``update_display``
``tcp_client``    factory for JSON-lines socket clients
"""

import json
import os
import socket
import sys
import threading
import time

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
STUB_DIR = os.path.join(TESTS_DIR, "live_stub")
REMOTE_SCRIPT_DIR = os.path.join(REPO_ROOT, "remote_script")

for _path in (STUB_DIR, REMOTE_SCRIPT_DIR, TESTS_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import Live  # noqa: E402  (must come after the sys.path juggling)
from live_stub import factory  # noqa: E402

from LiveBridge import config as config_module  # noqa: E402
from LiveBridge import log as log_module  # noqa: E402
from LiveBridge import registry  # noqa: E402
from LiveBridge.LiveBridge import LiveBridge  # noqa: E402


# --------------------------------------------------------------------------
# the set
# --------------------------------------------------------------------------

@pytest.fixture
def song():
    """A realistic Live set.

    * ``song.tracks[0]`` "Bass" — MIDI, an Operator with 7 parameters
      (``[0]`` "Device On" like every Live device, ``[1]`` Volume,
      ``[2]`` Filter Freq 20..20000, ``[3]`` Resonance, ``[4]`` Attack,
      ``[5]`` Release, ``[6]`` Osc Wave quantized) plus an Audio Effect Rack
      with 2 chains; MIDI clips with notes in slots 0 and 1.
    * ``song.tracks[1]`` "Vocals" — audio, an audio clip in slot 0 and a
      Reverb device.
    * ``song.tracks[2]`` "Drums" — MIDI, a Drum Rack with 3 pads (36 Kick,
      38 Snare, 42 Hat — each a DrumChain with a Simpler holding
      ``/Samples/Drums/<name>.wav``); a MIDI clip with notes in slot 0.
    * 2 return tracks named "Reverb" / "Delay" (Live reads them as "A-Reverb" /
      "B-Delay"), a master track, 4 scenes, 2 cue points and the browser tree from
      :func:`live_stub.factory.populate_browser`.
    """
    song = factory.make_song(midi_tracks=0, audio_tracks=0, return_tracks=0,
                             scenes=4, tempo=124.0)
    for index, name in enumerate(("Intro", "Verse", "Chorus", "Drop")):
        song.scenes[index].name = name

    bass = factory.add_track(song, "Bass", "midi")
    vocals = factory.add_track(song, "Vocals", "audio")
    drums = factory.add_track(song, "Drums", "midi")

    operator = factory.add_device(
        bass, "Operator", "Operator", kind="instrument",
        params=[("Volume", 0.8), ("Filter Freq", 800.0, 20.0, 20000.0),
                ("Resonance", 0.2), ("Attack", 0.01, 0.0, 10.0),
                ("Release", 0.5, 0.0, 60.0)])
    factory.add_parameter(operator, "Osc Wave", value=1.0, min=0.0, max=3.0,
                          is_quantized=True,
                          value_items=("Sine", "Saw", "Square", "Noise"))
    factory.add_rack(bass, "Bass Rack", kind="audio_effect", chains=2)

    factory.add_device(vocals, "Reverb", kind="audio_effect",
                       params=[("Dry/Wet", 0.3), ("Decay", 2.0, 0.1, 60.0)])
    factory.add_drum_rack(drums)

    factory.add_clip(bass, slot=0, length=4.0, name="Bass Loop",
                     notes=[(36, 0.0, 0.5, 100), (36, 1.0, 0.5, 90),
                            (43, 2.0, 0.5, 80), (36, 3.0, 0.5, 110)])
    factory.add_clip(bass, slot=1, length=8.0, name="Bass Fill",
                     notes=[(38, 0.0, 0.25, 120), (40, 0.5, 0.25, 100)])
    factory.add_clip(drums, slot=0, length=4.0, name="Beat",
                     notes=[(36, 0.0, 0.25, 127), (42, 0.5, 0.25, 90),
                            (38, 1.0, 0.25, 110), (42, 1.5, 0.25, 90)])
    factory.add_clip(vocals, slot=0, length=8.0, name="Vox Take", audio=True,
                     file_path="/Samples/vox.wav")
    factory.add_arrangement_clip(bass, start_time=0.0, length=4.0,
                                 name="Bass Arr",
                                 notes=[(36, 0.0, 1.0, 100)])

    factory.add_return_track(song, "Reverb")
    factory.add_return_track(song, "Delay")

    factory.add_cue_point(song, "Intro", 0.0)
    factory.add_cue_point(song, "Drop", 32.0)

    song.view.selected_track = bass
    song.view.highlighted_clip_slot = bass.clip_slots[0]
    song.view.selected_scene = song.scenes[0]

    factory.make_application(song)
    return song


@pytest.fixture
def app(song):
    """The ``Live.Application`` bound to the ``song`` fixture."""
    return Live.Application.get_application()


@pytest.fixture
def browser(app):
    """``app.browser`` with the tree from ``factory.populate_browser``."""
    return app.browser


# --------------------------------------------------------------------------
# the bridge
# --------------------------------------------------------------------------

def make_config(**overrides):
    """A :class:`LiveBridge.config.Config` for tests (loopback, no beacon)."""
    values = {"host": "127.0.0.1", "port": 0, "token": "", "allow_eval": True,
              "beacon": False, "beacon_port": 0, "name": "TestMachine",
              "max_clients": 4, "idle_timeout": 600.0, "default_timeout": 5.0,
              "max_timeout": 120.0, "log_level": "debug"}
    values.update(overrides)
    return config_module.Config(values)


@pytest.fixture
def bridge_factory(song):
    """Build LiveBridge instances; every one is disconnected at teardown.

    ``make(mode="immediate", auto_start=False, **config_overrides)`` returns the
    control surface.

    * ``mode="immediate"`` — the test thread is Live's main thread, so
      ``dispatch()`` called from the test runs the command inline (commands
      dispatched from *other* threads still queue until ``update_display``).
    * ``mode="queue"`` — no thread counts as the main thread: every request
      waits in the queue until somebody calls ``script.update_display()``
      (what Live does every ~100 ms, and what the ``tcp_bridge`` pump does).
    """
    created = []

    def make(mode="immediate", auto_start=False, **overrides):
        if mode not in ("immediate", "queue"):
            raise ValueError("mode must be 'immediate' or 'queue'")
        registry.discover()
        config = make_config(**overrides)
        c_instance = factory.make_c_instance(song)
        script = LiveBridge(c_instance, config=config, auto_start=auto_start)
        script.c_instance = c_instance
        if mode == "queue":
            script.main_thread_id = None
            log_module.set_main_thread(None)
        created.append(script)
        return script

    yield make
    for script in created:
        try:
            script.disconnect()
        except Exception:
            pass


@pytest.fixture
def bridge(bridge_factory):
    """A ready LiveBridge whose main thread is the test thread.

    ``bridge.dispatch({"id": "1", "cmd": "system.ping"})`` returns the response
    dict directly — no sockets, no threads.
    """
    log_module.clear()
    return bridge_factory()


class _Pump(object):
    """A background thread playing Live's main thread for the TCP tests."""

    def __init__(self, script, interval=0.005):
        self.script = script
        self.interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="test-main-thread")
        self._thread.daemon = True

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _run(self):
        while not self._stop.is_set():
            try:
                self.script.update_display()
            except Exception:
                pass
            self._stop.wait(self.interval)


@pytest.fixture
def tcp_bridge(bridge_factory):
    """A LiveBridge with the **real** TCP server on an ephemeral port.

    Yields the control surface; ``tcp_bridge.port`` is the bound port.  A
    background pump thread drains the command queue, mimicking Live's main
    thread.
    """
    script = bridge_factory(mode="queue", auto_start=True, port=0)
    pump = _Pump(script)
    pump.start()
    try:
        yield script
    finally:
        pump.stop()


class LineClient(object):
    """Minimal JSON-lines client for the socket tests."""

    def __init__(self, port, host="127.0.0.1", timeout=10.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self._buffer = bytearray()

    def send(self, message):
        """Send one request dict."""
        self.sock.sendall(json.dumps(message).encode("utf-8") + b"\n")

    def send_raw(self, data):
        """Send raw bytes (malformed-input tests)."""
        self.sock.sendall(data)

    def receive(self):
        """Read one response dict (blocks until a full line arrives)."""
        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                line = bytes(self._buffer[:newline])
                del self._buffer[:newline + 1]
                return json.loads(line.decode("utf-8"))
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError("server closed the connection")
            self._buffer.extend(chunk)

    def request(self, cmd, args=None, **extra):
        """Send a request and return its response."""
        message = {"id": extra.pop("id", "t%d" % int(time.time() * 1000000 % 1000000)),
                   "cmd": cmd}
        if args is not None:
            message["args"] = args
        message.update(extra)
        self.send(message)
        return self.receive()

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


@pytest.fixture
def tcp_client():
    """Factory: ``tcp_client(port)`` -> :class:`LineClient` (closed at teardown)."""
    clients = []

    def make(port, **kwargs):
        client = LineClient(port, **kwargs)
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.close()


@pytest.fixture
def udp_listener():
    """Factory: ``udp_listener()`` -> a bound UDP socket + its port.

    Use it to catch discovery beacons: ``sock, port = udp_listener()``.
    """
    sockets = []

    def make(host="127.0.0.1", timeout=5.0):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, 0))
        sock.settimeout(timeout)
        sockets.append(sock)
        return sock, sock.getsockname()[1]

    yield make
    for sock in sockets:
        try:
            sock.close()
        except Exception:
            pass
