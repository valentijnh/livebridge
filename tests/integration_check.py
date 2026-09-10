#!/usr/bin/env python3
"""LiveBridge integration check against a REAL, running Ableton Live (stdlib only).

This is not a unit test (pytest does not collect it: the name does not start with ``test_``).
Run it by hand on the Live machine -- or from another machine in LAN mode -- after installing:

    python tests/integration_check.py                              # 127.0.0.1:9880, token auto-detected
    python tests/integration_check.py --host 192.168.1.20 --token TOKEN
    python tests/integration_check.py --read-only                  # never changes the set
    python tests/integration_check.py --scenario beat              # + an empty-set-to-beat chain
    python tests/integration_check.py --scenario arrangement       # + arrangement copy/resize/move
    python tests/integration_check.py --no-play                    # never starts the transport
    python tests/integration_check.py --json                       # machine-readable report

Sequence (each step prints PASS / FAIL / SKIP):

1. Read-only: ``system.hello``, ``system.ping``, ``system.commands``, a set snapshot, then every
   argument-free read command the installed Remote Script offers (``*.get``, ``*.list``,
   ``*.status``, ``*.summary``, ``*.overview``, ``*.state``, ``*.selection`` ...).
2. Mutating, always cleaned up: create a temporary MIDI track at the end of the set, add a MIDI
   clip + notes and read them back, load an instrument (browser search when the browser commands
   exist, otherwise ``devices.insert``), set a parameter and read it back, fire the clip, stop it,
   then delete the temporary track again (also after a failure or Ctrl-C).
3. ``--scenario beat`` (optional): the production chain Claude runs for "make me a beat", on
   three temporary ``LB_beat`` tracks -- a drum kit from the browser (fallback: an empty Drum
   Rack) + ``notes.write_pattern``; an instrument + ``notes.write_chords``; a display-string
   parameters ("-6 dB" / "200 Hz" on Utility's Output / Bass Freq); ``mixer.set_many`` in dB; an audio bus with a
   Compressor side-chained to the drums (``routing.route``); a clip envelope
   (``automation.write``); ``arrangement.duplicate_clip``; arming + disarming the drum track
   (``record.arm``, nothing is recorded); firing both clips. Every temporary
   track is deleted again afterwards. Commands the Remote Script lacks are SKIPped.

4. ``--scenario arrangement`` (optional): on a temporary ``LB_arr`` MIDI track without
   devices, a session clip with a Track Volume envelope is copied into the arrangement
   (``arrangement.duplicate_clip``; ``automation.list`` on the copy must show "Track Volume"
   -- Live 12.4.5 copies envelopes on a track without devices), resized to 16 beats with its
   4-beat loop repeating (``arrangement.resize_clip``) and moved (``arrangement.move_clip``
   keeps the 16 beats). The track is deleted afterwards.

``--no-play`` skips firing clips (the only steps that touch the transport).

Every mutating command is one undo step in Live, so the check leaves undo history behind but no
changes to the set. It does not save the set. Exit code: 0 all passed, 1 something failed,
2 Live not reachable.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dump_live_api import remote_script_dirs  # noqa: E402  (sibling dev tool, stdlib only)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9880
READ_VERBS = ("get", "list", "summary", "status", "overview", "state", "selection", "snapshot",
              "roots", "info")
#: Read commands that are pointless or noisy in the sweep (covered elsewhere).
SWEEP_SKIP = ("system.hello", "system.ping", "system.commands", "song.snapshot")


class BridgeFailure(Exception):
    """An error envelope from LiveBridge (``type`` as in docs/PROTOCOL.md)."""

    def __init__(self, error: Dict[str, Any]) -> None:
        self.type = str(error.get("type", "internal"))
        self.message = str(error.get("message", error))
        super().__init__("%s: %s" % (self.type, self.message))


class Client(object):
    """Minimal JSON-lines client for the LiveBridge Remote Script (docs/PROTOCOL.md)."""

    def __init__(self, host: str, port: int, token: Optional[str], timeout: float) -> None:
        self.host, self.port, self.token, self.timeout = host, port, token or None, timeout
        self.sock: Optional[socket.socket] = None
        self.buffer = b""

    def connect(self) -> None:
        self.sock = socket.create_connection((self.host, self.port), timeout=5.0)
        self.sock.settimeout(self.timeout + 5.0)

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def request(self, cmd: str, args: Optional[Dict[str, Any]] = None,
                timeout: Optional[float] = None) -> Any:
        """Send one command; returns ``result`` or raises :class:`BridgeFailure` / OSError."""
        if self.sock is None:
            self.connect()
        assert self.sock is not None
        message: Dict[str, Any] = {"id": uuid.uuid4().hex[:10], "cmd": cmd, "args": args or {},
                                   "timeout": timeout or self.timeout}
        if self.token:
            message["token"] = self.token
        self.sock.sendall(json.dumps(message).encode("utf-8") + b"\n")
        while True:
            while b"\n" not in self.buffer:
                chunk = self.sock.recv(1 << 20)
                if not chunk:
                    self.close()
                    raise ConnectionError("LiveBridge closed the connection")
                self.buffer += chunk
            line, self.buffer = self.buffer.split(b"\n", 1)
            if not line.strip():
                continue
            response = json.loads(line.decode("utf-8"))
            if response.get("id") != message["id"]:
                continue  # an event or a late answer -- not ours
            if response.get("ok"):
                return response.get("result")
            raise BridgeFailure(response.get("error") or {})


def find_token(environ: Optional[Dict[str, str]] = None, home: Optional[Path] = None) -> str:
    """Token from ``LIVEBRIDGE_TOKEN``, the MCP config (``LIVEBRIDGE_CONFIG`` or
    ``~/.livebridge/config.json``) or the installed Remote Script config -- located like
    ``tests/dump_live_api.py:remote_script_dirs`` does (install.json, Library.cfg, default and
    OneDrive-redirected User Library folders)."""
    environ = dict(os.environ if environ is None else environ)
    if environ.get("LIVEBRIDGE_TOKEN"):
        return environ["LIVEBRIDGE_TOKEN"]
    home = home or Path(environ.get("USERPROFILE") or environ.get("HOME") or Path.home())
    candidates = []
    if environ.get("LIVEBRIDGE_CONFIG"):
        candidates.append(Path(environ["LIVEBRIDGE_CONFIG"]))
    candidates.append(home / ".livebridge" / "config.json")
    for folder in remote_script_dirs(environ, str(home)):
        candidates.append(Path(folder) / "config.json")
    for path in candidates:
        try:
            token = json.loads(path.read_text(encoding="utf-8-sig")).get("token")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(token, str) and token:
            return token
    return ""


class Report(object):
    """Collects PASS/FAIL/SKIP lines and prints them as they happen."""

    def __init__(self, quiet: bool = False) -> None:
        self.rows: List[Dict[str, Any]] = []
        self.quiet = quiet

    def add(self, status: str, name: str, detail: str = "", ms: float = 0.0) -> None:
        self.rows.append({"status": status, "step": name, "detail": detail, "ms": round(ms, 1)})
        if not self.quiet:
            print("%-4s  %-34s %7.1f ms  %s" % (status, name, ms, detail), flush=True)

    def count(self, status: str) -> int:
        return sum(1 for row in self.rows if row["status"] == status)


class Checker(object):
    """Runs the check sequence against one :class:`Client`."""

    def __init__(self, client: Client, report: Report, instrument: str = "Operator",
                 play: bool = True, kit: str = "909 Core Kit") -> None:
        self.client = client
        self.report = report
        self.instrument = instrument
        self.play = play
        self.kit = kit
        self.catalogue: Dict[str, Dict[str, Any]] = {}
        self.track_path: Optional[str] = None
        self.was_playing = False
        #: --scenario beat: role -> temporary track name, and the roles created so far.
        self.beat: Dict[str, str] = {}
        self.beat_created: List[str] = []
        #: --scenario arrangement: path of the temporary track (None once deleted).
        self.arr_track: Optional[str] = None

    # -- helpers -------------------------------------------------------------
    def step(self, name: str, func: Callable[[], Tuple[Any, str]], soft: bool = False) -> Any:
        """Run ``func`` (returning ``(value, detail)``), record PASS/FAIL, return value or None.

        ``soft``: argument/context errors (``bad_args``, ``not_found``, ``invalid_state``) are a
        SKIP, not a FAIL -- used by the read sweep, where some commands need a selection.
        """
        started = time.perf_counter()
        skip_types = ["unsupported", "unknown_command"]
        if soft:
            skip_types += ["bad_args", "not_found", "invalid_state"]
        try:
            value, detail = func()
        except BridgeFailure as error:
            status = "SKIP" if error.type in skip_types else "FAIL"
            self.report.add(status, name, str(error), (time.perf_counter() - started) * 1000)
            return None
        except (OSError, ValueError, KeyError, TypeError, IndexError, AssertionError) as error:
            self.report.add("FAIL", name, "%s: %s" % (type(error).__name__, error),
                            (time.perf_counter() - started) * 1000)
            return None
        self.report.add("PASS", name, detail, (time.perf_counter() - started) * 1000)
        return value

    def has(self, cmd: str) -> bool:
        return cmd in self.catalogue if self.catalogue else True

    def params(self, cmd: str) -> List[str]:
        return [p.get("name") for p in self.catalogue.get(cmd, {}).get("params", [])
                if isinstance(p, dict)]

    def call(self, cmd: str, **args: Any) -> Any:
        return self.client.request(cmd, args)

    def need(self, *commands: str) -> None:
        """SKIP the current step when the Remote Script lacks one of ``commands``."""
        for cmd in commands:
            if not self.has(cmd):
                raise BridgeFailure({"type": "unknown_command",
                                     "message": "%s is not offered by this Remote Script" % cmd})

    # -- read-only part ------------------------------------------------------
    def run_read_only(self) -> bool:
        hello = self.step("system.hello", self._hello)
        if hello is None:
            return False
        self.step("system.ping", lambda: (self.call("system.ping"), "pong"))
        self.step("system.commands", self._commands)
        if self.has("song.snapshot"):
            self.step("song.snapshot", self._snapshot)
        elif self.has("song.summary"):
            self.step("song.summary", lambda: (self.call("song.summary"), "ok"))
        for cmd in sorted(self.catalogue):
            spec = self.catalogue[cmd]
            verb = cmd.rsplit(".", 1)[-1]
            if cmd in SWEEP_SKIP or spec.get("mutating") or verb not in READ_VERBS:
                continue
            if cmd.split(".", 1)[0] in ("eval", "lom"):
                continue
            required = [p.get("name") for p in spec.get("params", [])
                        if isinstance(p, dict) and p.get("required", "default" not in p)]
            if required:
                continue
            self.step(cmd, lambda c=cmd: _with_size(self.call(c)), soft=True)
        return True

    def _hello(self) -> Tuple[Any, str]:
        hello = self.call("system.hello")
        live = hello.get("live") or {}
        version = ".".join(str(live.get(k, "?")) for k in ("major", "minor", "bugfix"))
        return hello, "LiveBridge %s, Live %s %s, protocol %s, eval %s" % (
            hello.get("version"), version, live.get("edition", ""), hello.get("protocol"),
            "on" if hello.get("allow_eval") else "off")

    def _commands(self) -> Tuple[Any, str]:
        listing = self.call("system.commands", include_doc=False)
        # {"count", "namespaces", "commands": [...]} -- older builds returned the bare list
        commands = listing.get("commands", []) if isinstance(listing, dict) else listing
        self.catalogue = {item["cmd"]: item for item in commands
                          if isinstance(item, dict) and "cmd" in item}
        namespaces = sorted({cmd.split(".", 1)[0] for cmd in self.catalogue})
        return commands, "%d commands in %d namespaces (%s)" % (
            len(self.catalogue), len(namespaces), ", ".join(namespaces))

    def _snapshot(self) -> Tuple[Any, str]:
        snapshot = self.call("song.snapshot", detail="minimal")
        assert isinstance(snapshot, dict), "snapshot is not an object"
        song = snapshot.get("song") or {}
        return snapshot, "tempo %s, %d tracks, %d scenes, %s" % (
            song.get("tempo", "?"), len(snapshot.get("tracks") or []),
            len(snapshot.get("scenes") or []), _size(snapshot))

    # -- mutating part -------------------------------------------------------
    def run_mutating(self) -> None:
        self.step("remember transport", self._remember_transport)
        created = self.step("create temp MIDI track", self._create_track)
        if created is None:
            return
        try:
            clip = self.step("create MIDI clip", self._create_clip)
            if clip is not None:
                self.step("add notes", self._add_notes)
                self.step("read notes back", self._read_notes)
            device = self.step("load instrument", self._load_instrument)
            if device is not None:
                self.step("set a device parameter", self._set_parameter)
            if clip is not None and self.play:
                self.step("fire clip", self._fire)
                self.step("stop clip", self._stop)
        finally:
            self.step("delete temp track (cleanup)", self._cleanup)

    def _remember_transport(self) -> Tuple[Any, str]:
        state = self.call("transport.get")
        self.was_playing = bool(state.get("is_playing"))
        return state, "was %s" % ("playing" if self.was_playing else "stopped")

    def _create_track(self) -> Tuple[Any, str]:
        name = "LB_check %s" % uuid.uuid4().hex[:4]
        track = self.call("tracks.create", type="midi", name=name, index=-1)
        self.track_path = track["path"]
        return track, "%s at %s" % (track.get("name", name), self.track_path)

    def _create_clip(self) -> Tuple[Any, str]:
        clip = self.call("clips.create", track=self.track_path, slot=0, length=4,
                         name="LiveBridge check")
        return clip, "%s, length %s beats" % (clip.get("path"), clip.get("length"))

    def _add_notes(self) -> Tuple[Any, str]:
        notes = [[60, 0.0, 1.0, 100], [64, 1.0, 1.0, 90], [67, 2.0, 1.0, 80], [72, 3.0, 1.0, 110]]
        result = self.call("notes.add", track=self.track_path, slot=0, notes=notes)
        assert result.get("added") == 4, "expected 4 notes added, got %r" % result.get("added")
        return result, "4 notes (C major arpeggio)"

    def _read_notes(self) -> Tuple[Any, str]:
        result = self.call("notes.get", track=self.track_path, slot=0)
        rows = result.get("notes", [])
        count = result.get("total", result.get("count", len(rows)))
        assert count == 4, "expected 4 notes, read %r" % count
        return result, "%d notes read back" % count

    def _load_instrument(self) -> Tuple[Any, str]:
        how = self._load_via_browser()
        if how is None:
            self.call("devices.insert", name=self.instrument, track=self.track_path)
            how = "devices.insert(%r)" % self.instrument
        devices = self.call("devices.list", track=self.track_path)
        items = devices.get("devices", [])
        assert items, "no device on the temp track after loading"
        return items[0], "%s via %s" % (items[0].get("name"), how)

    def _load_via_browser(self) -> Optional[str]:
        """Search + load through the browser commands when this Remote Script has them."""
        if not (self.catalogue and "browser.load" in self.catalogue):
            return None
        load_params = self.params("browser.load")
        args: Dict[str, Any] = {}
        if "track" in load_params:
            args["track"] = self.track_path
        item = None
        if "browser.search" in self.catalogue:
            search_params = self.params("browser.search")
            search: Dict[str, Any] = {"query": self.instrument}
            for key in ("category", "root", "in_category"):
                if key in search_params:
                    search[key] = "instruments"
                    break
            if "limit" in search_params:
                search["limit"] = 10
            try:
                found = self.client.request("browser.search", search, timeout=60.0)
            except BridgeFailure:
                found = None
            results = found.get("results", found.get("items", [])) if isinstance(found, dict) \
                else (found or [])
            loadable = [r for r in results if isinstance(r, dict) and r.get("is_loadable", True)]
            exact = [r for r in loadable if str(r.get("name", "")).lower().startswith(
                self.instrument.lower())]
            item = (exact or loadable or [None])[0]
        if item is not None and "uri" in load_params and item.get("uri"):
            args["uri"] = item["uri"]
        elif item is not None and "path" in load_params and item.get("path"):
            args["path"] = item["path"]
        elif "query" in load_params:
            args["query"] = self.instrument
        else:
            return None
        try:
            self.client.request("browser.load", args, timeout=60.0)
        except BridgeFailure as error:
            self.report.add("SKIP", "browser load", "%s -- falling back to devices.insert" % error)
            return None
        return "browser.load(%s)" % ", ".join("%s=%r" % kv for kv in sorted(args.items())
                                              if kv[0] != "track")

    def _set_parameter(self) -> Tuple[Any, str]:
        page = self.call("devices.parameters", track=self.track_path, device=0, limit=64)
        target = None
        for param in page.get("parameters", []):
            quantized = param.get("is_quantized", param.get("quantized", False))
            if param.get("index", 0) == 0 or quantized:
                continue
            if isinstance(param.get("min"), (int, float)) and isinstance(param.get("max"),
                                                                         (int, float)):
                target = param
                break
        assert target is not None, "no continuous parameter found"
        value = target["min"] + (target["max"] - target["min"]) * 0.37
        self.call("devices.set_parameter", track=self.track_path, device=0,
                  parameter=target["index"], value=value)
        after = self.call("devices.get_parameter", track=self.track_path, device=0,
                          parameter=target["index"])
        span = abs(target["max"] - target["min"]) or 1.0
        assert abs(float(after.get("value")) - value) <= span * 0.01, \
            "read back %r, expected %r" % (after.get("value"), value)
        return after, "%s = %s" % (target.get("name"), after.get("display", after.get("value")))

    def _fire(self) -> Tuple[Any, str]:
        fired = self.call("clips.fire", track=self.track_path, slot=0)
        clip: Dict[str, Any] = fired if isinstance(fired, dict) else {}
        deadline = time.time() + 3.0
        while not (clip.get("is_playing") or clip.get("is_triggered")) and \
                time.time() < deadline:
            time.sleep(0.2)
            clip = self.call("clips.get", track=self.track_path, slot=0)
        assert clip.get("is_playing") or clip.get("is_triggered"), \
            "clip neither playing nor triggered 3 s after firing"
        return clip, "playing" if clip.get("is_playing") else "triggered (waiting for quantization)"

    def _stop(self) -> Tuple[Any, str]:
        self.call("clips.stop", track=self.track_path, slot=0, quantized=False)
        if not self.was_playing:
            self.call("transport.stop")
        return None, "stopped%s" % ("" if self.was_playing else " (transport stopped again)")

    def _cleanup(self) -> Tuple[Any, str]:
        if not self.track_path:
            return None, "nothing to clean up"
        result = self.call("tracks.delete", track=self.track_path)
        deleted = self.track_path
        self.track_path = None
        return result, "deleted %s" % deleted


    # -- --scenario beat ---------------------------------------------------------
    def run_beat_scenario(self) -> None:
        """An empty-set-to-beat chain on temporary tracks; every track is deleted afterwards."""
        tag = uuid.uuid4().hex[:4]
        self.beat = {role: "LB_beat %s %s" % (role, tag) for role in ("drums", "keys", "bus")}
        self.beat_created = []
        try:
            if self.step("beat: create drum track", lambda: self._beat_track("drums", "midi")) \
                    is None:
                return
            self.step("beat: load a drum kit", self._beat_kit)
            self.step("beat: write drum pattern", self._beat_pattern)
            if self.step("beat: create keys track", lambda: self._beat_track("keys", "midi")) \
                    is not None:
                self.step("beat: insert instrument", self._beat_instrument)
                self.step("beat: write chords", self._beat_chords)
                self.step("beat: display-string parameter", self._beat_display_parameter)
                self.step("beat: clip automation", self._beat_automation)
                self.step("beat: mixer set_many (dB)", self._beat_mixer)
            if self.step("beat: create bus + Compressor", self._beat_bus) is not None:
                self.step("beat: side-chain the bus to the drums", self._beat_sidechain)
            self.step("beat: duplicate to arrangement", self._beat_arrangement)
            self.step("beat: arm + disarm the drums (record.arm)", self._beat_arm)
            if self.play:
                self.step("beat: fire clips", self._beat_fire)
                self.step("beat: stop clips", self._beat_stop)
        finally:
            for role in reversed(list(self.beat_created)):
                self.step("beat: delete %s track (cleanup)" % role,
                          lambda r=role: self._beat_delete(r))

    def _beat_track(self, role: str, kind: str) -> Tuple[Any, str]:
        self.need("tracks.create")
        track = self.call("tracks.create", type=kind, name=self.beat[role], index=-1)
        self.beat_created.append(role)
        return track, "%s at %s" % (self.beat[role], track.get("path"))

    def _beat_kit(self) -> Tuple[Any, str]:
        track = self.beat["drums"]
        if self.has("browser.load"):
            try:
                loaded = self.client.request("browser.load", {
                    "query": self.kit, "category": "drums", "track": track,
                    "max_seconds": 20.0}, timeout=60.0)
                return loaded, "%s via browser.load" % (loaded.get("loaded") or {}).get("name")
            except BridgeFailure as error:
                fallback = "browser.load failed (%s)" % error
        else:
            fallback = "no browser.load"
        self.need("devices.insert")
        self.call("devices.insert", name="Drum Rack", track=track)
        return None, "empty Drum Rack via devices.insert (%s)" % fallback

    def _beat_pattern(self) -> Tuple[Any, str]:
        self.need("notes.write_pattern", "notes.get")
        track = self.beat["drums"]
        pattern = {"kick": "x...x...x...x...", "snare": "....X.......X...",
                   "hat": "x.x.x.x.x.x.x.xo"}
        result = self.call("notes.write_pattern", track=track, slot=0, pattern=pattern)
        # 4 kicks + 2 accented snares + 8 hats + 1 ghost hat
        assert result.get("added") == 15, "expected 15 notes, got %r" % result.get("added")
        notes = self.call("notes.get", track=track, slot=0)
        count = notes.get("total", notes.get("count", len(notes.get("notes", []))))
        assert count == 15, "read back %r notes" % count
        return result, "15 notes (kick/snare/hat + ghost), rows %s" % result.get("rows")

    def _beat_instrument(self) -> Tuple[Any, str]:
        self.need("devices.insert")
        device = self.call("devices.insert", name=self.instrument, track=self.beat["keys"])
        return device, "%s on %s" % (self.instrument, self.beat["keys"])

    def _beat_chords(self) -> Tuple[Any, str]:
        self.need("notes.write_chords")
        result = self.call("notes.write_chords", track=self.beat["keys"], slot=0,
                           chords="Am F C G", duration=4)
        chords = result.get("chords") or []
        assert len(chords) == 4 and result.get("added", 0) >= 12, \
            "expected 4 chords / 12+ notes, got %r" % result
        return result, " ".join("%s=%s" % (c.get("chord"), "/".join(c.get("notes") or []))
                                for c in chords)

    def _beat_display_parameter(self) -> Tuple[Any, str]:
        """Display strings against Live's non-linear curves: Utility's gain knob is the
        parameter "Output" (-inf..35 dB) and "Bass Freq" (50..500 Hz) in Live 12.4.5."""
        self.need("devices.insert", "devices.set_parameter", "devices.get_parameter",
                  "devices.parameters")
        track = self.beat["keys"]
        self.call("devices.insert", name="Utility", track=track)
        self._require_utility_parameters("Output", "Bass Freq")
        shown = []
        for parameter, text, number in (("Output", "-6 dB", -6.0), ("Bass Freq", "200 Hz", 200.0)):
            self.call("devices.set_parameter", track=track, device="Utility",
                      parameter=parameter, value=text)
            after = self.call("devices.get_parameter", track=track, device="Utility",
                              parameter=parameter)
            display = str(after.get("display", after.get("value")))
            match = re.search(r"-?\d+(?:\.\d+)?", display)
            assert match and abs(float(match.group(0)) - number) <= abs(number) * 0.02, \
                "Utility %s shows %r after setting %r" % (parameter, display, text)
            shown.append("%s %r -> %s" % (parameter, text, display))
        return None, "; ".join(shown)

    def _require_utility_parameters(self, *names: str) -> None:
        """SKIP (not FAIL) when the keys track's Utility lacks these parameters -- e.g. the
        unit-test stub, whose Utility only has generic parameters."""
        page = self.call("devices.parameters", track=self.beat["keys"], device="Utility",
                         limit=64)
        have = {p.get("name") for p in page.get("parameters", []) if isinstance(p, dict)}
        missing = [name for name in names if name not in have]
        if missing:
            raise BridgeFailure({"type": "unsupported", "message": "Utility has no %s here (%s)"
                                 % (", ".join(missing), ", ".join(sorted(map(str, have))))})

    def _beat_automation(self) -> Tuple[Any, str]:
        self.need("automation.write", "devices.parameters")
        self._require_utility_parameters("Output")
        result = self.call("automation.write", track=self.beat["keys"], slot=0,
                           device="Utility", parameter="Output",
                           points=[{"time": 0, "value": "-12 dB"}, {"time": 8, "value": "0 dB"}],
                           mode="linear", resolution=8)
        assert result.get("steps"), "no envelope steps written: %r" % result
        return result, "Utility Output ramp, %s steps, values %s" % (result.get("steps"),
                                                                      result.get("values"))

    def _beat_mixer(self) -> Tuple[Any, str]:
        self.need("mixer.set_many", "mixer.get")
        self.call("mixer.set_many", settings=[
            {"track": self.beat["drums"], "volume": "-6 dB"},
            {"track": self.beat["keys"], "volume": "-10 dB", "pan": "L20"}])
        mixer = self.call("mixer.get", track=self.beat["drums"])
        volume = mixer.get("volume")
        shown = volume.get("display") if isinstance(volume, dict) else volume
        assert "-6.0" in str(shown), "drums volume shows %r after '-6 dB'" % shown
        return mixer, "drums %s, keys -10 dB / L20" % shown

    def _beat_bus(self) -> Tuple[Any, str]:
        self._beat_track("bus", "audio")
        self.need("devices.insert")
        self.call("devices.insert", name="Compressor", track=self.beat["bus"])
        return True, "%s with a Compressor" % self.beat["bus"]

    def _beat_sidechain(self) -> Tuple[Any, str]:
        self.need("routing.route")
        result = self.call("routing.route", source=self.beat["drums"],
                           destination=self.beat["bus"], method="sidechain")
        routed = ((result.get("routing") or {}).get("input") or {}).get("type")
        assert routed == self.beat["drums"], "side-chain input is %r" % routed
        return result, "Compressor listens to %s (S/C On: %s)" % (
            routed, result.get("sidechain_enabled"))

    def _beat_arrangement(self) -> Tuple[Any, str]:
        self.need("arrangement.duplicate_clip", "arrangement.list")
        track = self.beat["drums"]
        self.call("arrangement.duplicate_clip", track=track, slot=0, time=0)
        listing = self.call("arrangement.list", track=track)
        clips = listing.get("clips", []) if isinstance(listing, dict) else listing
        assert clips, "no arrangement clip on %s" % track
        return listing, "%d arrangement clip(s) on %s" % (len(clips), track)

    def _beat_arm(self) -> Tuple[Any, str]:
        """Arm only the temporary drum track (``exclusive`` stays off, so the user's own armed
        tracks are untouched), read ``record.status``, disarm again. Nothing is recorded."""
        self.need("record.arm")
        track = self.beat["drums"]
        armed = self.call("record.arm", track=track, arm=True)
        names = [t.get("name") for t in armed.get("armed_tracks", []) if isinstance(t, dict)]
        assert track in names, "%s not in armed_tracks %r" % (track, names)
        if self.has("record.status"):
            self.call("record.status")
        self.call("record.arm", track=track, arm=False)
        return armed, "armed then disarmed %s" % track

    def _beat_fire(self) -> Tuple[Any, str]:
        self.need("clips.fire")
        if self.has("transport.get"):
            self.was_playing = bool(self.call("transport.get").get("is_playing"))
        for role in ("drums", "keys"):
            if role in self.beat_created:
                self.call("clips.fire", track=self.beat[role], slot=0)
        return None, "drums + keys fired"

    def _beat_stop(self) -> Tuple[Any, str]:
        for role in ("drums", "keys"):
            if role in self.beat_created:
                self.call("clips.stop", track=self.beat[role], slot=0, quantized=False)
        if not self.was_playing and self.has("transport.stop"):
            self.call("transport.stop")
        return None, "stopped"

    def _beat_delete(self, role: str) -> Tuple[Any, str]:
        result = self.call("tracks.delete", track=self.beat[role])
        self.beat_created.remove(role)
        return result, "deleted %s" % self.beat[role]

    # -- --scenario arrangement --------------------------------------------------
    def run_arrangement_scenario(self) -> None:
        """Arrangement copies on a temporary track without devices; deleted afterwards."""
        name = "LB_arr %s" % uuid.uuid4().hex[:4]
        self.arr_track = None
        created = self.step("arr: create track", lambda: self._arr_track(name))
        if created is None:
            return
        try:
            if self.step("arr: session clip + volume envelope", self._arr_clip) is not None:
                if self.step("arr: duplicate keeps the envelope", self._arr_duplicate) \
                        is not None:
                    self.step("arr: resize to 16 beats (loop repeats)", self._arr_resize)
                    self.step("arr: move keeps the length", self._arr_move)
        finally:
            self.step("arr: delete track (cleanup)", self._arr_delete)

    def _arr_track(self, name: str) -> Tuple[Any, str]:
        self.need("tracks.create", "arrangement.duplicate_clip")
        track = self.call("tracks.create", type="midi", name=name, index=-1)
        self.arr_track = track["path"]
        return track, "%s at %s" % (name, self.arr_track)

    def _arr_clip(self) -> Tuple[Any, str]:
        self.need("clips.create", "automation.write")
        self.call("clips.create", track=self.arr_track, slot=0, length=4)
        self.call("notes.add", track=self.arr_track, slot=0, notes=[[60, 0.0, 1.0, 100]])
        result = self.call("automation.write", track=self.arr_track, slot=0,
                           parameter="Volume", points=[[0, 0.3], [2, 0.9]])
        return result, "4-beat clip, Track Volume envelope 0.3 -> 0.9"

    def _arr_clip_row(self, index: int = 0) -> Dict[str, Any]:
        listing = self.call("arrangement.list", track=self.arr_track)
        clips = listing.get("clips", []) if isinstance(listing, dict) else listing
        assert len(clips) > index, "no arrangement clip %d on %s: %r" % (
            index, self.arr_track, clips)
        return clips[index]

    def _arr_duplicate(self) -> Tuple[Any, str]:
        self.need("automation.list", "arrangement.list")
        placed = self.call("arrangement.duplicate_clip", track=self.arr_track, slot=0, time=0)
        path = placed.get("path") or self._arr_clip_row().get("path")
        listing = self.call("automation.list", clip=path)
        names = [e.get("name") for e in listing.get("envelopes", []) if isinstance(e, dict)]
        assert "Track Volume" in names, "the arrangement copy has envelopes %r (reported %r)" \
            % (names, placed.get("envelopes"))
        return placed, "copy at %s carries %s (envelopes %s)" % (
            path, ", ".join(names), placed.get("envelopes"))

    def _arr_resize(self) -> Tuple[Any, str]:
        self.need("arrangement.resize_clip")
        self.call("arrangement.resize_clip", track=self.arr_track, index=0, length=16)
        row = self._arr_clip_row()
        clip = self.call("clips.get", clip=row["path"]) if self.has("clips.get") else row
        span = float(row["end_time"]) - float(row["start_time"])
        assert abs(span - 16.0) < 1e-6, "timeline length %r, expected 16" % span
        loop_end = clip.get("loop_end", row.get("loop_end"))
        assert loop_end is None or abs(float(loop_end) - 4.0) < 1e-6, \
            "loop_end %r, expected 4 (the loop repeats)" % loop_end
        return row, "%s..%s, loop_end %s" % (row["start_time"], row["end_time"], loop_end)

    def _arr_move(self) -> Tuple[Any, str]:
        self.need("arrangement.move_clip")
        moved = self.call("arrangement.move_clip", track=self.arr_track, index=0, start=32)
        row = self._arr_clip_row()
        span = float(row["end_time"]) - float(row["start_time"])
        assert abs(float(row["start_time"]) - 32.0) < 1e-6, "moved to %r" % row["start_time"]
        assert abs(span - 16.0) < 1e-6, "length after the move %r, expected 16" % span
        return moved, "now %s..%s" % (row["start_time"], row["end_time"])

    def _arr_delete(self) -> Tuple[Any, str]:
        if not self.arr_track:
            return None, "nothing to clean up"
        result = self.call("tracks.delete", track=self.arr_track)
        deleted, self.arr_track = self.arr_track, None
        return result, "deleted %s" % deleted


def _size(value: Any) -> str:
    return "%d bytes" % len(json.dumps(value, default=str))


def _with_size(value: Any) -> Tuple[Any, str]:
    return value, _size(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="integration_check.py",
        description="Check a running Ableton Live + LiveBridge end to end (cleans up after itself).")
    parser.add_argument("--host", default=os.environ.get("LIVEBRIDGE_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("LIVEBRIDGE_PORT", DEFAULT_PORT)))
    parser.add_argument("--token", default=None,
                        help="default: LIVEBRIDGE_TOKEN, ~/.livebridge/config.json or the "
                             "installed Remote Script config.json")
    parser.add_argument("--timeout", type=float, default=15.0,
                        help="seconds per command (default 15)")
    parser.add_argument("--read-only", action="store_true", help="skip every mutating step")
    parser.add_argument("--scenario", choices=["beat", "arrangement"], action="append",
                        default=[],
                        help="also run a workflow scenario on temporary tracks (cleaned up): "
                             "'beat' = drum kit + pattern, chords, dB mixer, side-chain, "
                             "automation, arrangement; 'arrangement' = envelope copy, resize, "
                             "move")
    parser.add_argument("--no-play", dest="play", action="store_false",
                        help="never fire clips (leaves the transport alone)")
    parser.add_argument("--kit", default="909 Core Kit",
                        help="drum kit the beat scenario loads (default '909 Core Kit')")
    parser.add_argument("--instrument", default="Operator",
                        help="instrument to load on the temp track (default Operator)")
    parser.add_argument("--json", action="store_true", help="print a JSON report at the end")
    return parser


def utf8_stdout() -> None:
    """Print UTF-8 even into a Windows pipe (the ANSI code page would crash on "C\u266f")."""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError, AttributeError):
            pass


def main(argv: Optional[List[str]] = None) -> int:
    utf8_stdout()
    args = build_parser().parse_args(argv)
    token = find_token() if args.token is None else args.token
    report = Report(quiet=args.json)
    client = Client(args.host, args.port, token, args.timeout)
    if not args.json:
        print("LiveBridge integration check -> %s:%d (token %s)%s" % (
            args.host, args.port, "set" if token else "none",
            ", read-only" if args.read_only else ""))
    try:
        client.connect()
    except OSError as error:
        message = ("Live is not reachable at %s:%d (%s). Is Live running with Preferences -> "
                   "Link, Tempo & MIDI -> Control Surface -> LiveBridge?"
                   % (args.host, args.port, error))
        if args.json:
            print(json.dumps({"ok": False, "unreachable": True, "error": message}))
        else:
            print("FAIL  connect  " + message)
        return 2
    checker = Checker(client, report, instrument=args.instrument, play=args.play, kit=args.kit)
    try:
        if checker.run_read_only() and not args.read_only:
            checker.run_mutating()
            if "beat" in args.scenario:
                checker.run_beat_scenario()
            if "arrangement" in args.scenario:
                checker.run_arrangement_scenario()
    except KeyboardInterrupt:
        report.add("FAIL", "interrupted", "Ctrl-C")
        if checker.track_path:
            checker.step("delete temp track (cleanup)", checker._cleanup)
        for role in reversed(list(checker.beat_created)):
            checker.step("beat: delete %s track (cleanup)" % role,
                         lambda r=role: checker._beat_delete(r))
        if checker.arr_track:
            checker.step("arr: delete track (cleanup)", checker._arr_delete)
    finally:
        client.close()
    passed, failed, skipped = report.count("PASS"), report.count("FAIL"), report.count("SKIP")
    if args.json:
        print(json.dumps({"ok": failed == 0, "passed": passed, "failed": failed,
                          "skipped": skipped, "steps": report.rows}, indent=1))
    else:
        print("-" * 72)
        print("%d passed, %d failed, %d skipped" % (passed, failed, skipped))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
