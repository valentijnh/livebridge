"""Module E — samples: importing audio files, listing folders, inspecting files, path
validation and sample locations (bridge commands on the stub song with real temp files), plus
the MCP sample tools (fake bridge for forwarding/validation, real TCP bridge end to end)."""

import asyncio
import json
import os
import struct
import sys
import time
import wave
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parent
for _p in (str(_TESTS.parent / "mcp_server"), str(_TESTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fake_bridge import FakeBridge  # noqa: E402
from live_stub import factory  # noqa: E402
from livebridge_mcp.client import BridgeClient  # noqa: E402
from livebridge_mcp.server import create_app  # noqa: E402

from LiveBridge.handlers import browser as browser_handlers  # noqa: E402
from LiveBridge.handlers import samples as samples_handlers  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_cache():
    browser_handlers.clear_cache()
    yield
    browser_handlers.clear_cache()


def run(bridge, cmd, **args):
    response = bridge.dispatch({"id": "s", "cmd": cmd, "args": args})
    assert response["ok"], response
    return response["result"]


def fail(bridge, cmd, **args):
    response = bridge.dispatch({"id": "s", "cmd": cmd, "args": args})
    assert not response["ok"], response
    return response["error"]


def call_tool(app, name, args=None):
    result = asyncio.run(app.call_tool(name, args or {}))
    content = getattr(result, "content", None)
    if content is None:
        content = result[0] if isinstance(result, tuple) else result
    blocks = [b.text for b in content if getattr(b, "type", None) == "text"]

    def _p(text):
        try:
            return json.loads(text)
        except ValueError:
            return text
    return [_p(b) for b in blocks] if len(blocks) > 1 else _p("\n".join(blocks))


# --------------------------------------------------------------------------
# audio file writers (stdlib only)
# --------------------------------------------------------------------------

def write_wav(path, seconds=1.0, rate=44100, channels=2, width=2):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(seconds * rate)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        handle.writeframes(b"\x00" * frames * channels * width)
    return str(path)


def write_float_wav(path, seconds=1.0, rate=48000, channels=1):
    """IEEE-float WAV (format 3) — the ``wave`` module refuses these."""
    data = b"\x00" * int(seconds * rate) * channels * 4
    fmt = struct.pack("<HHIIHH", 3, channels, rate, rate * channels * 4, channels * 4, 32)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + \
        b"data" + struct.pack("<I", len(data)) + data
    Path(path).write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return str(path)


def _extended(value):
    """float -> 80-bit IEEE extended (big endian), for AIFF COMM chunks."""
    import math
    mantissa, exponent = math.frexp(value)
    exponent += 16382
    mantissa = int(mantissa * (1 << 64))
    return struct.pack(">HQ", exponent, mantissa)


def write_aiff(path, frames=22050, rate=44100, channels=2, bits=16):
    comm = struct.pack(">hIh", channels, frames, bits) + _extended(rate)
    ssnd = struct.pack(">II", 0, 0) + b"\x00" * frames * channels * (bits // 8)
    body = b"AIFF" + b"COMM" + struct.pack(">I", len(comm)) + comm + \
        b"SSND" + struct.pack(">I", len(ssnd)) + ssnd
    Path(path).write_bytes(b"FORM" + struct.pack(">I", len(body)) + body)
    return str(path)


def write_flac(path, rate=44100, channels=2, bits=24, frames=88200):
    packed = (rate << 44) | ((channels - 1) << 41) | ((bits - 1) << 36) | frames
    streaminfo = struct.pack(">HH", 4096, 4096) + b"\x00" * 6 + struct.pack(">Q", packed) + \
        b"\x00" * 16
    Path(path).write_bytes(b"fLaC" + bytes([0x80, 0, 0, 34]) + streaminfo)
    return str(path)


def write_mp3(path, frames=100, id3=True):
    header = bytes([0xFF, 0xFB, 0x90, 0x00])  # MPEG1 layer III, 128 kbps, 44.1 kHz, stereo
    frame = header + b"\x00" * (417 - 4)
    tag = b""
    if id3:
        tag = b"ID3" + bytes([4, 0, 0, 0, 0, 0, 10]) + b"\x00" * 10
    Path(path).write_bytes(tag + frame * frames)
    return str(path)


# --------------------------------------------------------------------------
# path handling (pure functions, both platforms)
# --------------------------------------------------------------------------

def test_normalize_posix():
    env = {"HOME": "/Users/v", "SPLICE": "/Volumes/Samples"}
    norm = samples_handlers.normalize_path
    assert norm("~/Music/kick.wav", False, env) == ("/Users/v/Music/kick.wav", [], [])
    assert norm("$HOME/a.wav", False, env)[0] == "/Users/v/a.wav"
    assert norm("${SPLICE}/b.wav", False, env)[0] == "/Volumes/Samples/b.wav"
    assert norm("%SPLICE%/c.wav", False, env)[0] == "/Volumes/Samples/c.wav"
    assert norm('  "/Users/v/My Kick.wav"  ', False, env)[0] == "/Users/v/My Kick.wav"
    path, problems, hints = norm("file:///Users/v/My%20Kick.wav", False, env)
    assert path == "/Users/v/My Kick.wav" and not problems and "file://" in hints[0]
    assert norm("/Users/v/../v/./x.wav", False, env)[0] == "/Users/v/x.wav"
    path, problems, _ = norm("C:\\Users\\v\\kick.wav", False, env)
    assert problems and "Windows path" in problems[0]
    path, problems, hints = norm("Samples\\kick.wav", False, env)
    assert path == "Samples/kick.wav" and "not an absolute path" in problems[0]
    assert norm("", False, env)[1] == ["the path is empty"]


def test_normalize_windows():
    env = {"USERPROFILE": "C:\\Users\\v", "HOME": "/ignored"}
    norm = samples_handlers.normalize_path
    assert norm("C:/Users/v/Music/kick.wav", True, env)[0] == "C:\\Users\\v\\Music\\kick.wav"
    assert norm("%USERPROFILE%\\Splice\\a.wav", True, env)[0] == "C:\\Users\\v\\Splice\\a.wav"
    assert norm("~\\Splice\\b.wav", True, env)[0] == "C:\\Users\\v\\Splice\\b.wav"
    assert norm("file:///C:/Users/v/My%20Loop.wav", True, env)[0] == \
        "C:\\Users\\v\\My Loop.wav"
    assert norm("\\\\nas\\share\\x.wav", True, env) == ("\\\\nas\\share\\x.wav", [], [])
    path, problems, _ = norm("/Users/v/kick.wav", True, env)
    assert problems and "macOS/Linux path" in problems[0]


def test_splice_and_library_candidates():
    mac = samples_handlers.splice_candidates(environ={}, windows=False, home="/Users/v")
    assert mac[:2] == ["/Users/v/Splice/sounds", "/Users/v/Splice"]
    win = samples_handlers.splice_candidates(environ={}, windows=True, home="C:\\Users\\v")
    assert win[:2] == ["C:\\Users\\v\\Splice\\sounds", "C:\\Users\\v\\Splice"]
    assert "C:\\Users\\v\\Documents\\Splice" in win
    custom = samples_handlers.splice_candidates(
        environ={"LIVEBRIDGE_SPLICE_DIR": "~/Elsewhere", "HOME": "/Users/v"}, windows=False,
        home="/Users/v")
    assert custom[0] == "/Users/v/Elsewhere"
    assert samples_handlers.user_library_candidates(environ={}, windows=True,
                                                    home="C:\\Users\\v") == \
        ["C:\\Users\\v\\Documents\\Ableton\\User Library",
         "C:\\Users\\v\\OneDrive\\Documents\\Ableton\\User Library"]
    assert samples_handlers.user_library_candidates(environ={}, windows=False,
                                                    home="/Users/v") == \
        ["/Users/v/Music/Ableton/User Library"]


def test_core_library_and_factory_packs_candidates():
    """Derived from the running Live executable (paths verified on Live 12.4.5 Suite, macOS;
    the Windows layout is Live's documented ProgramData install)."""
    mac = samples_handlers.core_library_candidates(
        "/Applications/Ableton Live 12 Suite.app/Contents/MacOS/Live", windows=False)
    assert "/Applications/Ableton Live 12 Suite.app/Contents/App-Resources/Core Library" in mac
    win = samples_handlers.core_library_candidates(
        "C:\\ProgramData\\Ableton\\Live 12 Suite\\Program\\Ableton Live 12 Suite.exe",
        windows=True)
    assert "C:\\ProgramData\\Ableton\\Live 12 Suite\\Resources\\Core Library" in win
    assert samples_handlers.core_library_candidates("", windows=False) == []
    assert samples_handlers.factory_packs_candidates(environ={}, windows=False,
                                                     home="/Users/v") == \
        ["/Users/v/Music/Ableton/Factory Packs"]
    assert samples_handlers.factory_packs_candidates(environ={}, windows=True,
                                                     home="C:\\Users\\v") == \
        ["C:\\Users\\v\\Documents\\Ableton\\Factory Packs",
         "C:\\Users\\v\\OneDrive\\Documents\\Ableton\\Factory Packs"]


# --------------------------------------------------------------------------
# samples.inspect
# --------------------------------------------------------------------------

def test_inspect_formats(bridge, tmp_path):
    wav = run(bridge, "samples.inspect", file_path=write_wav(tmp_path / "a.wav", 1.5))
    assert wav["ok"] and wav["is_audio"] and wav["size"] > 0 and "mtime" in wav
    assert wav["audio"] == {"format": "wav", "encoding": "pcm", "channels": 2,
                            "sample_rate": 44100, "bit_depth": 16, "frames": 66150,
                            "duration": 1.5}
    flt = run(bridge, "samples.inspect", file_path=write_float_wav(tmp_path / "f.wav"))["audio"]
    assert flt["encoding"] == "float" and flt["bit_depth"] == 32 and flt["duration"] == 1.0
    assert flt["sample_rate"] == 48000 and flt["channels"] == 1
    aiff = run(bridge, "samples.inspect", file_path=write_aiff(tmp_path / "b.aif"))["audio"]
    assert aiff["channels"] == 2 and aiff["sample_rate"] == 44100 and aiff["duration"] == 0.5
    assert aiff["frames"] == 22050 and aiff["bit_depth"] == 16
    flac = run(bridge, "samples.inspect", file_path=write_flac(tmp_path / "c.flac"))["audio"]
    assert flac == {"format": "flac", "sample_rate": 44100, "channels": 2, "bit_depth": 24,
                    "frames": 88200, "duration": 2.0}
    mp3 = run(bridge, "samples.inspect", file_path=write_mp3(tmp_path / "d.mp3"))["audio"]
    assert mp3["format"] == "mp3" and mp3["sample_rate"] == 44100 and mp3["channels"] == 2
    assert mp3["bitrate_kbps"] == 128 and abs(mp3["duration"] - 2.606) < 0.01
    ogg = tmp_path / "e.ogg"
    ogg.write_bytes(b"OggS" + b"\x00" * 60)
    assert run(bridge, "samples.inspect", file_path=str(ogg))["audio"] == {"format": "ogg"}
    broken = tmp_path / "broken.wav"
    broken.write_bytes(b"not a wav at all")
    assert run(bridge, "samples.inspect", file_path=str(broken))["audio"] == {}


def test_inspect_problems_are_reported(bridge, tmp_path):
    write_wav(tmp_path / "Kick Hard.wav")
    missing = run(bridge, "samples.inspect", file_path=str(tmp_path / "Kick Soft.wav"))
    assert missing["ok"] is False and missing["exists"] is False
    assert "does not exist on the machine that runs Live" in missing["problems"][0]
    assert "Kick Hard.wav" in missing["hints"][0]
    folder = run(bridge, "samples.inspect", file_path=str(tmp_path))
    assert folder["ok"] is True and folder["is_dir"] is True
    text = tmp_path / "readme.txt"
    text.write_text("hi", encoding="utf-8")
    info = run(bridge, "samples.inspect", file_path=str(text))
    assert info["is_audio"] is False and "not an audio file" in info["hints"][-1]
    relative = run(bridge, "samples.inspect", file_path="kick.wav")
    assert "not an absolute path" in relative["problems"][0]
    if os.name != "nt":
        windows = run(bridge, "samples.inspect", file_path="C:\\Samples\\kick.wav")
        assert "Windows path" in windows["problems"][0]
    assert fail(bridge, "samples.inspect", file_path=5)["type"] == "bad_args"


# --------------------------------------------------------------------------
# samples.list / samples.locations
# --------------------------------------------------------------------------

def _tree(tmp_path):
    root = tmp_path / "lib"
    now = time.time()
    files = {
        "old kick.wav": now - 3000,
        "sub/new kick.wav": now - 10,
        "sub/deeper/snare.aif": now - 500,
        "loop.mp3": now - 100,
    }
    for rel, mtime in files.items():
        path = write_wav(root / rel, 0.1) if rel.endswith(".wav") else str(root / rel)
        if not rel.endswith(".wav"):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(b"\x00" * 64)
        os.utime(path, (mtime, mtime))
    (root / "notes.txt").write_text("x", encoding="utf-8")
    (root / "partial.wav.part").write_bytes(b"\x00")
    (root / ".hidden.wav").write_bytes(b"\x00")
    (root / ".cache").mkdir()
    write_wav(root / ".cache" / "ignored.wav", 0.1)
    return root


def test_list_folder(bridge, tmp_path):
    root = _tree(tmp_path)
    data = run(bridge, "samples.list", folder=str(root))
    assert [f["name"] for f in data["files"]] == ["new kick.wav", "loop.mp3", "snare.aif",
                                                  "old kick.wav"]
    assert data["total"] == 4 and data["truncated"] is False and data["now"] > 0
    first = data["files"][0]
    assert first["path"] == str(root / "sub" / "new kick.wav") and first["size"] > 0
    assert first["age_s"] >= 0
    assert [f["name"] for f in run(bridge, "samples.list", folder=str(root),
                                   pattern="*kick*")["files"]] == ["new kick.wav",
                                                                   "old kick.wav"]
    assert [f["name"] for f in run(bridge, "samples.list", folder=str(root),
                                   pattern="*.aif, *.mp3", sort="name")["files"]] == \
        ["loop.mp3", "snare.aif"]
    flat = run(bridge, "samples.list", folder=str(root), recursive=False)
    assert [f["name"] for f in flat["files"]] == ["loop.mp3", "old kick.wav"]
    anything = run(bridge, "samples.list", folder=str(root), extensions="*", sort="oldest")
    assert "notes.txt" in [f["name"] for f in anything["files"]]
    assert "partial.wav.part" not in [f["name"] for f in anything["files"]]
    only_wav = run(bridge, "samples.list", folder=str(root), extensions="wav")
    assert {f["name"] for f in only_wav["files"]} == {"new kick.wav", "old kick.wav"}
    page = run(bridge, "samples.list", folder=str(root), limit=1, offset=1)
    assert [f["name"] for f in page["files"]] == ["loop.mp3"] and page["next_offset"] == 2
    assert run(bridge, "samples.list", folder=str(root),
               newer_than=time.time() + 60)["total"] == 0
    assert run(bridge, "samples.list", folder=str(root),
               newer_than=time.time() - 600)["total"] == 4  # arrival (ctime) is now
    truncated = run(bridge, "samples.list", folder=str(root), max_scan=100)
    assert truncated["truncated"] is False
    by_size = run(bridge, "samples.list", folder=str(root), sort="size")
    assert by_size["files"][0]["size"] >= by_size["files"][-1]["size"]


def test_list_errors(bridge, tmp_path):
    error = fail(bridge, "samples.list", folder=str(tmp_path / "missing"))
    assert error["type"] == "not_found"
    wav = write_wav(tmp_path / "a.wav")
    assert fail(bridge, "samples.list", folder=wav)["type"] == "bad_args"
    assert fail(bridge, "samples.list", folder="relative/dir")["type"] == "bad_args"
    assert fail(bridge, "samples.list", folder=str(tmp_path), sort="random")["type"] == \
        "bad_args"
    assert fail(bridge, "samples.list", folder=str(tmp_path), limit=0)["type"] == "bad_args"
    assert fail(bridge, "samples.list", folder=str(tmp_path),
                newer_than="yesterday")["type"] == "bad_args"


def test_list_scan_limit(bridge, tmp_path):
    root = tmp_path / "many"
    root.mkdir()
    for index in range(150):
        (root / ("s%03d.wav" % index)).write_bytes(b"\x00")
    data = run(bridge, "samples.list", folder=str(root), max_scan=100)
    assert data["truncated"] is True and data["scanned"] == 101 and data["total"] <= 100


def test_locations(bridge, tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "Splice" / "sounds").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("LIVEBRIDGE_SPLICE_DIR", raising=False)
    data = run(bridge, "samples.locations")
    assert data["splice_folder"] == str(home / "Splice" / "sounds")
    assert data["splice"][0] == {"path": str(home / "Splice" / "sounds"), "exists": True}
    assert data["downloads"]["path"] == str(home / "Downloads")
    assert data["env"] == {}
    assert data["factory_packs"]["path"] == str(home / "Music" / "Ableton" / "Factory Packs") \
        or data["factory_packs"]["path"].endswith("Factory Packs")
    assert "core_library" in data            # null outside Live (no Core Library there)
    custom = tmp_path / "custom"
    custom.mkdir()
    monkeypatch.setenv("LIVEBRIDGE_SPLICE_DIR", str(custom))
    data = run(bridge, "samples.locations")
    assert data["splice_folder"] == str(custom)
    assert data["env"] == {"LIVEBRIDGE_SPLICE_DIR": str(custom)}


# --------------------------------------------------------------------------
# samples.import
# --------------------------------------------------------------------------

def test_import_session_into_audio_track(bridge, song, tmp_path):
    vocals = song.tracks[1]
    path = write_wav(tmp_path / "Vox Chop.wav")
    data = run(bridge, "samples.import", file_path=path, track="Vocals")
    assert data["route"] == "clip_slot.create_audio_clip"
    assert data["slot"] == 1  # slot 0 already has a clip
    assert data["clip"]["path"] == "song.tracks[1].clip_slots[1].clip"
    assert data["clip"]["name"] == "Vox Chop"
    assert data["file"] == {"path": path, "name": "Vox Chop.wav",
                            "size": os.path.getsize(path)}
    assert data["track"] == {"path": "song.tracks[1]", "index": 1, "name": "Vocals",
                             "type": "audio", "kind": "track"}
    assert vocals.clip_slots[1].clip.file_path == path
    error = fail(bridge, "samples.import", file_path=path, track="Vocals", slot=0)
    assert error["type"] == "invalid_state" and "overwrite" in error["message"]
    data = run(bridge, "samples.import", file_path=path, track="Vocals", slot="Intro",
               overwrite=True, name="Chop", warp=False, select=True)
    assert data["slot"] == 0 and data["notes"] == ["replaced the clip that was in the slot"]
    assert data["applied"] == {"name": "Chop", "warping": False}
    assert vocals.clip_slots[0].clip.name == "Chop"
    assert vocals.clip_slots[0].clip.warping is False
    assert song.view.selected_track == vocals
    assert song.view.highlighted_clip_slot == vocals.clip_slots[0]


def test_import_prefers_the_highlighted_empty_slot(bridge, song, tmp_path):
    vocals = song.tracks[1]
    path = write_wav(tmp_path / "hl.wav")
    song.view.highlighted_clip_slot = vocals.clip_slots[3]
    data = run(bridge, "samples.import", file_path=path, track="Vocals")
    assert data["slot"] == 3 and "used the highlighted clip slot" in data["notes"]
    # highlighted slot now full -> first empty slot
    assert run(bridge, "samples.import", file_path=path, track="Vocals")["slot"] == 1


def test_import_fills_track_then_adds_scene(bridge, song, tmp_path):
    path = write_wav(tmp_path / "hit.wav")
    scenes = len(song.scenes)
    slots = [run(bridge, "samples.import", file_path=path, track="Vocals")["slot"] for _ in range(3)]
    assert slots == [1, 2, 3]
    data = run(bridge, "samples.import", file_path=path, track="Vocals")
    assert data["created_scene"] is True and data["slot"] == scenes
    assert len(song.scenes) == scenes + 1


def test_import_creates_track(bridge, song, tmp_path):
    count = len(song.tracks)
    path = write_wav(tmp_path / "Break 90.wav")
    data = run(bridge, "samples.import", file_path=path)
    assert data["created_track"] is True and data["track"]["name"] == "Break 90"
    assert data["track"]["type"] == "audio" and data["slot"] == 0
    assert len(song.tracks) == count + 1
    named = run(bridge, "samples.import", file_path=path, track_name="Drums Bus",
                mode="arrangement", time=4)
    assert named["track"]["name"] == "Drums Bus" and named["route"] == "track.create_audio_clip"
    assert fail(bridge, "samples.import", file_path=path, track="Vocals",
                track_name="x")["type"] == "bad_args"


def test_import_arrangement(bridge, song, tmp_path):
    vocals = song.tracks[1]
    path = write_wav(tmp_path / "pad.wav")
    data = run(bridge, "samples.import", file_path=path, track="Vocals", mode="arrangement",
               time=8)
    assert data["route"] == "track.create_audio_clip" and data["time"] == 8.0
    assert data["clip"]["start_time"] == 8.0 and data["clip"]["end_time"] > 8.0
    assert [c.file_path for c in vocals.arrangement_clips] == [path]
    song.current_song_time = 16.0
    data = run(bridge, "samples.import", file_path=path, track="Vocals", mode="arrangement")
    assert data["time"] == 16.0
    error = fail(bridge, "samples.import", file_path=path, track="Bass", mode="arrangement")
    assert error["type"] == "invalid_state" and "MIDI track" in error["message"]
    assert fail(bridge, "samples.import", file_path=path, track="Vocals", mode="arrangement",
                time=-1)["type"] == "bad_args"
    assert fail(bridge, "samples.import", file_path=path, track="Vocals", mode="arrangement",
                slot=1)["type"] == "bad_args"
    assert fail(bridge, "samples.import", file_path=path, track="Vocals",
                time=4)["type"] == "bad_args"
    assert fail(bridge, "samples.import", file_path=path, track="Vocals", mode="arrangement",
                target="simpler")["type"] == "bad_args"


def test_import_simpler(bridge, song, tmp_path):
    path = write_wav(tmp_path / "808.wav")
    data = run(bridge, "samples.import", file_path=path, mode="simpler")
    assert data["created_track"] is True and data["track"]["type"] == "midi"
    assert data["route"] == "track.insert_device + simpler.replace_sample"
    track = song.tracks[-1]
    assert track.devices[0].class_name == "OriginalSimpler"
    assert track.devices[0].sample.file_path == path
    assert data["device"]["path"] == "%s.devices[0]" % data["track"]["path"]
    # an existing Simpler gets its sample replaced
    other = write_wav(tmp_path / "909.wav")
    data = run(bridge, "samples.import", file_path=other, track=data["track"]["path"])
    assert data["route"] == "simpler.replace_sample"
    assert track.devices[0].sample.file_path == other
    assert data["notes"] == ["replaced the sample of the existing Simpler"]
    # an instrument after MIDI effects, before audio effects
    keys = factory.add_track(song, "Keys", "midi")
    factory.add_device(keys, "Arpeggiator", "MidiArpeggiator", kind="midi_effect")
    factory.add_device(keys, "Reverb", kind="audio_effect")
    data = run(bridge, "samples.import", file_path=path, track="Keys")
    assert [d.name for d in keys.devices] == ["Arpeggiator", "Simpler", "Reverb"]
    assert data["device"]["name"] == "Simpler"
    # a track with another instrument is refused
    error = fail(bridge, "samples.import", file_path=path, track="Bass")
    assert error["type"] == "invalid_state" and "Operator" in error["message"]
    error = fail(bridge, "samples.import", file_path=path, track="Vocals", target="simpler")
    assert error["type"] == "invalid_state" and "audio track" in error["message"]
    error = fail(bridge, "samples.import", file_path=path, track="Bass", target="clip")
    assert error["type"] == "invalid_state"
    assert fail(bridge, "samples.import", file_path=path, track="Keys", target="simpler",
                slot=1)["type"] == "bad_args"


def test_import_via_browser(bridge, song, tmp_path):
    vocals = song.tracks[1]
    stash = tmp_path / "Sample Stash"
    path = write_wav(stash / "Snare Top.wav")
    data = run(bridge, "samples.import", file_path=path, track="Vocals", target="browser")
    assert data["route"] == "browser.load_item"
    assert data["browser_item"]["path"] == "user_folders/Sample Stash/Snare Top.wav"
    assert data["slot"] == 1 and data["clips"][0]["name"] == "Snare Top"
    assert data["clip"]["path"] == "song.tracks[1].clip_slots[1].clip"
    assert vocals.clip_slots[1].has_clip
    unknown = write_wav(tmp_path / "Not In Browser.wav")
    error = fail(bridge, "samples.import", file_path=unknown, track="Vocals", target="browser")
    assert error["type"] == "not_found" and "Places" in error["message"]


def test_import_via_browser_needs_the_session_view(bridge, song, app, tmp_path):
    """Live 12.4.5 ignores browser sample loads into clip slots while the Arrangement view
    is focused — refused up front (before an overwrite deletes anything)."""
    stash = tmp_path / "Sample Stash"
    path = write_wav(stash / "Snare Top.wav")
    app.view.show_view("Arranger")
    error = fail(bridge, "samples.import", file_path=path, track="Vocals", target="browser",
                 slot=0, overwrite=True)
    assert error["type"] == "invalid_state" and "Session view" in error["message"]
    assert song.tracks[1].clip_slots[0].has_clip          # nothing was deleted
    # a MIDI track gets a Simpler (no clip slot involved), in any view
    keys = factory.add_track(song, "Keys", "midi")
    data = run(bridge, "samples.import", file_path=path, track="Keys", target="browser")
    assert data["route"] == "browser.load_item" and "slot" not in data
    assert data["inserted"][0]["class_name"] == "OriginalSimpler"
    assert keys.devices[0].class_name == "OriginalSimpler"
    assert fail(bridge, "samples.import", file_path=path, track="Keys", target="browser",
                slot=1)["type"] == "bad_args"
    app.view.show_view("Session")


def test_import_path_and_arg_errors(bridge, song, tmp_path):
    missing = fail(bridge, "samples.import", file_path=str(tmp_path / "nope.wav"), track="Vocals")
    assert missing["type"] == "not_found"
    assert "machine that runs Live" in missing["message"]
    assert fail(bridge, "samples.import", file_path="nope.wav")["type"] == "bad_args"
    text = tmp_path / "a.txt"
    text.write_text("x", encoding="utf-8")
    error = fail(bridge, "samples.import", file_path=str(text))
    assert error["type"] == "bad_args" and "not an audio file" in error["message"]
    assert fail(bridge, "samples.import", file_path=str(tmp_path))["type"] == "bad_args"
    part = tmp_path / "dl.wav.part"
    part.write_bytes(b"\x00")
    assert fail(bridge, "samples.import", file_path=str(part))["type"] == "bad_args"
    path = write_wav(tmp_path / "ok.wav")
    assert fail(bridge, "samples.import", file_path=path, mode="loop")["type"] == "bad_args"
    assert fail(bridge, "samples.import", file_path=path, target="pad")["type"] == "bad_args"
    assert fail(bridge, "samples.import", file_path=path, mode="simpler",
                target="clip")["type"] == "bad_args"
    error = fail(bridge, "samples.import", file_path=path, track="A-Reverb")
    assert error["type"] == "invalid_state" and "return" in error["message"]
    assert fail(bridge, "samples.import", file_path=path, track="master")["type"] == "invalid_state"
    assert fail(bridge, "samples.import", file_path=path, track=42)["type"] == "not_found"
    if os.name != "nt":
        error = fail(bridge, "samples.import", file_path="C:\\Users\\me\\kick.wav")
        assert error["type"] == "bad_args" and "Windows path" in error["message"]


def test_import_is_one_undo_step(bridge, song, tmp_path):
    path = write_wav(tmp_path / "u.wav")
    before = len(song._undo_steps)
    run(bridge, "samples.import", file_path=path)  # creates a track + a clip
    assert len(song._undo_steps) == before + 1


def test_import_home_relative_path(bridge, song, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    write_wav(tmp_path / "Music" / "tilde.wav")
    data = run(bridge, "samples.import", file_path="~/Music/tilde.wav", track="Vocals")
    assert data["file"]["path"] == str(tmp_path / "Music" / "tilde.wav")


# --------------------------------------------------------------------------
# MCP tools
# --------------------------------------------------------------------------

SAMPLE_TOOLS = {"live_sample_import", "live_sample_list", "live_sample_inspect",
                "live_sample_upload"}


@pytest.fixture()
def fake_app():
    fake = FakeBridge().start()
    client = BridgeClient(host=fake.host, port=fake.port, timeout=5.0)
    try:
        yield fake, create_app(client)
    finally:
        client.close()
        fake.stop()


def test_sample_tools_registered(fake_app):
    _fake, app = fake_app
    assert "samples" in app.tool_modules
    tools = {t.name: t for t in asyncio.run(app.list_tools())}
    assert SAMPLE_TOOLS <= set(tools)
    assert "live_sample_locations" not in tools, "folded into live_sample_list(folder=None)"
    for name in SAMPLE_TOOLS:
        assert len(tools[name].description or "") > 150, name


def test_sample_tools_forward_arguments(fake_app):
    fake, app = fake_app
    for cmd in ("samples.import", "samples.list", "samples.inspect", "samples.locations"):
        fake.set_result(cmd, {"ok": cmd})

    def last():
        return fake.requests[-1]["cmd"], fake.requests[-1].get("args", {})

    call_tool(app, "live_sample_import", {"file_path": "/x/a.wav"})
    assert last() == ("samples.import", {"file_path": "/x/a.wav"})
    call_tool(app, "live_sample_import", {"file_path": "/x/a.wav", "track": "Vox",
                                          "mode": "arrangement", "time": 8, "warp": False,
                                          "clip_name": "A", "select": True})
    assert last() == ("samples.import", {"file_path": "/x/a.wav", "track": "Vox", "time": 8,
                                         "name": "A", "warp": False, "mode": "arrangement",
                                         "select": True})
    call_tool(app, "live_sample_import", {"file_path": "/x/a.wav", "slot": 2,
                                          "target": "browser", "overwrite": True,
                                          "track_name": None})
    assert last() == ("samples.import", {"file_path": "/x/a.wav", "slot": 2, "target": "browser",
                                         "overwrite": True})
    call_tool(app, "live_sample_list", {"folder": "~/Splice", "pattern": "*.wav",
                                        "sort": "added", "limit": 5, "recursive": False})
    assert last() == ("samples.list", {"folder": "~/Splice", "pattern": "*.wav",
                                       "recursive": False, "sort": "added", "limit": 5})
    call_tool(app, "live_sample_inspect", {"file_path": "~/a.wav"})
    assert last() == ("samples.inspect", {"file_path": "~/a.wav"})
    call_tool(app, "live_sample_list")
    assert last() == ("samples.locations", {}), "no folder = the known sample folders"
    call_tool(app, "live_sample_list", {"folder": "/x", "max_age_minutes": 2})
    assert last() == ("samples.list", {"folder": "/x", "max_age_s": 120.0})
    call_tool(app, "live_sample_import", {"file_path": "/x/a.wav", "note": "C1", "track": 2})
    assert last() == ("samples.import", {"file_path": "/x/a.wav", "track": 2, "note": "C1"})
    call_tool(app, "live_sample_import", {"file_path": "/x/a.mid", "track": 2,
                                          "midi_track": "Piano"})
    assert last() == ("samples.import_midi", {"file_path": "/x/a.mid", "track": 2,
                                              "midi_track": "Piano"})


def test_sample_tools_validate_locally(fake_app):
    fake, app = fake_app
    count = len(fake.requests)
    for name, args in [
        ("live_sample_import", {"file_path": " "}),
        ("live_sample_import", {"file_path": "/a.wav", "mode": "loop"}),
        ("live_sample_import", {"file_path": "/a.wav", "target": "pad"}),
        ("live_sample_import", {"file_path": "/a.wav", "mode": "arrangement", "slot": 1}),
        ("live_sample_import", {"file_path": "/a.wav", "time": 4}),
        ("live_sample_import", {"file_path": "/a.wav", "mode": "arrangement", "time": -2}),
        ("live_sample_import", {"file_path": "/a.wav", "track": 1, "track_name": "x"}),
        ("live_sample_list", {"folder": ""}),
        ("live_sample_list", {"folder": "/x", "sort": "random"}),
        ("live_sample_list", {"folder": "/x", "limit": 0}),
        ("live_sample_list", {"folder": "/x", "offset": -1}),
        ("live_sample_inspect", {"file_path": ""}),
        ("live_sample_import", {"file_path": "/a.wav", "note": 36, "target": "clip"}),
        ("live_sample_import", {"file_path": "/a.mid", "mode": "simpler"}),
        ("live_sample_import", {"file_path": "/a.mid", "note": 36}),
        ("live_sample_import", {"file_path": "/a.wav", "midi_track": 1}),
        ("live_sample_list", {"pattern": "*.wav"}),
        ("live_sample_list", {"folder": "/x", "max_age_minutes": 0}),
        ("live_sample_upload", {}),
        ("live_sample_upload", {"local_path": "/a.wav", "url": "https://x/a.wav"}),
    ]:
        result = call_tool(app, name, args)
        assert isinstance(result, dict) and result.get("type") == "bad_args", (name, result)
    assert len(fake.requests) == count


def test_sample_tools_end_to_end(tcp_bridge, song, tmp_path):
    client = BridgeClient(host="127.0.0.1", port=tcp_bridge.port, timeout=5.0)
    try:
        app = create_app(client)
        path = write_wav(tmp_path / "E2E Loop.wav", 0.5)
        info = call_tool(app, "live_sample_inspect", {"file_path": path})
        assert info["ok"] and info["audio"]["duration"] == 0.5
        listing = call_tool(app, "live_sample_list", {"folder": str(tmp_path)})
        assert listing["files"][0]["path"] == path
        imported = call_tool(app, "live_sample_import", {"file_path": path, "track": "Vocals"})
        assert imported["route"] == "clip_slot.create_audio_clip"
        assert song.tracks[1].clip_slots[1].clip.name == "E2E Loop"
        arr = call_tool(app, "live_sample_import", {"file_path": path, "mode": "arrangement",
                                                    "track": "Vocals", "time": 2})
        assert arr["clip"]["start_time"] == 2.0
        missing = call_tool(app, "live_sample_import",
                            {"file_path": str(tmp_path / "gone.wav")})
        assert missing["type"] == "not_found" and "gone.wav" in missing["error"]
        locations = call_tool(app, "live_sample_list")
        assert "splice" in locations and "platform" in locations and "inbox" in locations
    finally:
        client.close()


def test_extension_that_lies_about_the_format(bridge, song, tmp_path):
    """Live 12.4.5 refuses AIFF data named .wav ("This file does not appear to be a valid
    WAV file") — inspect reports it and import refuses it before asking Live."""
    fake = write_aiff(tmp_path / "Download.wav")
    report = run(bridge, "samples.inspect", file_path=fake)
    assert report["ok"] is False and "AIFF data" in report["problems"][0]
    error = fail(bridge, "samples.import", file_path=fake, track="Vocals")
    assert error["type"] == "bad_args" and "rename it to .aif" in error["message"]
    good = write_aiff(tmp_path / "Fine.aif")
    assert run(bridge, "samples.inspect", file_path=good)["ok"] is True
    assert samples_handlers.sniff_format(write_wav(tmp_path / "a.wav")) == "wav"
    assert samples_handlers.format_mismatch(write_wav(tmp_path / "b.wav")) is None


# --------------------------------------------------------------------------
# drum pads
# --------------------------------------------------------------------------

def test_import_onto_drum_pads_builds_a_kit(bridge, song, tmp_path):
    """One call per pad: a new MIDI track + Drum Rack for the first file, the next empty
    pad (C1 upward) for the following ones, and a named note on an existing kit."""
    kick = write_wav(tmp_path / "Kick A.wav", 0.1)
    snare = write_wav(tmp_path / "Snare B.wav", 0.1)
    count = len(song.tracks)
    first = run(bridge, "samples.import", file_path=kick, target="drum_pad", track_name="Kit")
    assert first["created_track"] and len(song.tracks) == count + 1
    kit = song.tracks[-1]
    assert kit.name == "Kit" and kit.devices[0].can_have_drum_pads
    assert first["pad"]["note"] == 36 and first["pad"]["key"] == "C1"
    assert first["route"].startswith("rack.insert_chain")
    assert "inserted a Drum Rack" in first["notes"]
    second = run(bridge, "samples.import", file_path=snare, track=first["track"]["path"])
    assert second["pad"]["note"] == 37, "auto target on a Drum Rack track = next empty pad"
    rack = kit.devices[0]
    assert [c.in_note for c in rack.chains] == [36, 37]
    assert rack.chains[1].name == "Snare B"
    assert rack.chains[1].devices[0].sample.file_path == snare
    # an existing kit: C1 holds a Simpler -> its sample is replaced, nothing added
    drums = song.tracks[2]
    chains = len(drums.devices[0].chains)
    replaced = run(bridge, "samples.import", file_path=snare, track="Drums", note="C1")
    assert replaced["route"] == "drum_pad simpler.replace_sample"
    assert len(drums.devices[0].chains) == chains
    assert drums.devices[0].drum_pads[36].chains[0].devices[0].sample.file_path == snare


def test_drum_pad_import_refusals(bridge, song, tmp_path):
    path = write_wav(tmp_path / "Hit.wav", 0.1)
    error = fail(bridge, "samples.import", file_path=path, track="Bass", target="drum_pad")
    assert error["type"] == "invalid_state" and "Operator" in error["message"]
    error = fail(bridge, "samples.import", file_path=path, track="Vocals", note=36)
    assert error["type"] == "invalid_state" and "audio track" in error["message"]
    error = fail(bridge, "samples.import", file_path=path, track="Drums", note=40, slot=1)
    assert error["type"] == "bad_args"
    error = fail(bridge, "samples.import", file_path=path, track="Drums", note="H9")
    assert error["type"] == "not_found" and "Kick" in error["message"]
    error = fail(bridge, "samples.import", file_path=path, track="Drums", note=True)
    assert error["type"] == "bad_args"
    error = fail(bridge, "samples.import", file_path=path, target="clip", note=36)
    assert error["type"] == "bad_args"


# --------------------------------------------------------------------------
# fast scans: quiet folders, folder cache, depth, max_age_s
# --------------------------------------------------------------------------

def _age(path, seconds):
    old = time.time() - seconds
    os.utime(path, (old, old))


def test_scan_skips_quiet_folders_and_caches_them(tmp_path, monkeypatch):
    """With newer_than, a folder whose own time is older cannot hold a new file: its files
    are not stat'ed and do not count towards max_scan; a new file in an existing nested
    folder is still found (adding a file only touches its own folder, never the parents —
    the bug a "prune by the parent's time" shortcut would have)."""
    monkeypatch.setattr(samples_handlers, "QUIET_MARGIN", 0.0)
    lib = tmp_path / "lib"
    for pack in range(3):
        for index in range(40):
            write_wav(lib / ("pack%d" % pack) / ("old%02d.wav" % index), 0.01)
    write_wav(lib / "pack1" / "sub" / "old.wav", 0.01)
    time.sleep(0.05)
    since = time.time()
    time.sleep(0.05)
    fresh = write_wav(lib / "pack1" / "sub" / "fresh.wav", 0.01)
    full, scanned_full, _t = samples_handlers.scan_folder(str(lib))
    assert len(full) == 122 and scanned_full >= 122
    files, scanned, truncated = samples_handlers.scan_folder(str(lib), newer_than=since,
                                                             max_scan=30)
    assert [f["path"] for f in files] == [fresh] and not truncated
    assert scanned < 30, "files of quiet folders are not counted"
    cache = {}
    samples_handlers.scan_folder(str(lib), newer_than=since, dir_cache=cache)
    assert str(lib / "pack0") in cache
    calls = []
    real_scandir = os.scandir
    monkeypatch.setattr(samples_handlers.os, "scandir",
                        lambda p: calls.append(p) or real_scandir(p))
    again, _s, _t = samples_handlers.scan_folder(str(lib), newer_than=since, dir_cache=cache)
    assert [f["path"] for f in again] == [fresh]
    assert str(lib / "pack0") not in calls, "an unchanged quiet folder is not listed again"
    assert str(lib / "pack1" / "sub") in calls


def test_samples_list_max_age_and_depth(bridge, tmp_path):
    root = tmp_path / "Downloads"
    write_wav(root / "top.wav", 0.01)
    write_wav(root / "a" / "b" / "deep.wav", 0.01)
    old = write_wav(root / "old.wav", 0.01)
    os.utime(old, (time.time() - 7200, time.time() - 7200))
    # an old download keeps its old mtime but its ctime (arrival) is new: still recent
    listing = run(bridge, "samples.list", folder=str(root), max_age_s=600, sort="added")
    assert {f["name"] for f in listing["files"]} == {"top.wav", "deep.wav", "old.wav"}
    shallow = run(bridge, "samples.list", folder=str(root), max_depth=1)
    assert {f["name"] for f in shallow["files"]} == {"top.wav", "old.wav"}
    assert run(bridge, "samples.list", folder=str(root), max_depth=2)["total"] == 3
    assert fail(bridge, "samples.list", folder=str(root), max_age_s=0)["type"] == "bad_args"
    assert fail(bridge, "samples.list", folder=str(root), max_depth=-1)["type"] == "bad_args"
    midi = root / "loop.mid"
    midi.write_bytes(_smf([[(0, 0x90, 60, 100), (96, 0x80, 60, 0)]]))
    listed = run(bridge, "samples.list", folder=str(root), extensions="mid")
    assert [f["name"] for f in listed["files"]] == ["loop.mid"]


# --------------------------------------------------------------------------
# receiving files (LAN mode)
# --------------------------------------------------------------------------

def _b64(data):
    import base64
    return base64.b64encode(data).decode("ascii")


def isolate_home(home, monkeypatch):
    """Point every "where is the User Library / Downloads" lookup at ``home`` — HOME,
    USERPROFILE, APPDATA (Live's Library.cfg) and the Windows known folders — so no test
    reads or writes the tester's real User Library."""
    for sub in (("Music", "Ableton", "User Library"), ("Documents", "Ableton", "User Library")):
        home.joinpath(*sub).mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    monkeypatch.setattr(samples_handlers, "windows_shell_folders", lambda environ=None: {})
    return home


def user_library(home):
    return home / ("Documents" if os.name == "nt" else "Music") / "Ableton" / "User Library"


@pytest.fixture
def inbox_home(tmp_path, monkeypatch):
    return isolate_home(tmp_path / "home", monkeypatch)


def test_receive_writes_chunks_into_the_inbox(bridge, inbox_home, tmp_path):
    import hashlib
    source = Path(write_wav(tmp_path / "src" / "Kick 01.wav", 0.2))
    data = source.read_bytes()
    half = len(data) // 2
    first = run(bridge, "samples.receive", name="Kick 01.wav", data=_b64(data[:half]),
                upload_id="abc123", total_size=len(data))
    assert first == {"done": False, "received": half, "total_size": len(data)}
    inbox = user_library(inbox_home) / "Samples" / "LiveBridge"
    assert [p.name for p in inbox.iterdir()] == [".Kick 01.wav.abc123.part"]
    # samples.list never shows an unfinished upload
    assert run(bridge, "samples.list", folder=str(inbox))["total"] == 0
    wrong = fail(bridge, "samples.receive", name="Kick 01.wav", data=_b64(data[half:-1]),
                 upload_id="abc123", total_size=len(data), offset=half - 1)
    assert wrong["type"] == "invalid_state" and "resend from offset %d" % half in \
        wrong["message"]
    done = run(bridge, "samples.receive", name="Kick 01.wav", data=_b64(data[half:]),
               upload_id="abc123", total_size=len(data), offset=half,
               sha256=hashlib.sha256(data).hexdigest())
    assert done["done"] and done["path"] == str(inbox / "Kick 01.wav")
    assert Path(done["path"]).read_bytes() == data and done["audio"]["format"] == "wav"
    again = run(bridge, "samples.receive", name="Kick 01.wav", data=_b64(data),
                upload_id="def456", total_size=len(data), subfolder="Splice/Pack A")
    assert again["path"] == str(inbox / "Splice" / "Pack A" / "Kick 01.wav")
    dup = run(bridge, "samples.receive", name="Kick 01.wav", data=_b64(data),
              upload_id="ghi789", total_size=len(data))
    assert dup["renamed"] and dup["name"] == "Kick 01 (2).wav"
    over = run(bridge, "samples.receive", name="Kick 01.wav", data=_b64(b"RIFF"),
               upload_id="jkl012", total_size=4, overwrite=True)
    assert over["path"] == str(inbox / "Kick 01.wav") and "renamed" not in over
    imported = run(bridge, "samples.import", file_path=again["path"], track="Vocals")
    assert imported["route"] == "clip_slot.create_audio_clip"


def test_receive_refusals(bridge, inbox_home):
    base = dict(data=_b64(b"abcd"), upload_id="abc123", total_size=4)
    for args, kind in [
        (dict(base, name="evil.py"), "bad_args"),
        (dict(base, name="../../x.wav", subfolder="../up"), "bad_args"),
        (dict(base, name="a.wav", upload_id="../../x"), "bad_args"),
        (dict(base, name="a.wav", data="not base64!!"), "bad_args"),
        (dict(base, name="a.wav", total_size=2), "bad_args"),
        (dict(base, name="a.wav", offset=2, data=_b64(b"ab")), "invalid_state"),
        (dict(base, name=".hidden.wav"), "bad_args"),
    ]:
        assert fail(bridge, "samples.receive", **args)["type"] == kind, args
    bad = fail(bridge, "samples.receive", name="a.wav", sha256="00" * 32, **base)
    assert bad["type"] == "invalid_state" and "sha256" in bad["message"]
    inbox = user_library(inbox_home) / "Samples" / "LiveBridge"
    assert list(inbox.iterdir()) == [], "a failed checksum leaves nothing behind"
    # folders in the name are stripped, Windows-invalid characters replaced
    ok = run(bridge, "samples.receive", name="C:\\\\dl\\\\Hat: 1?.wav", **base)
    assert ok["name"] == "Hat_ 1_.wav"
    assert samples_handlers.safe_file_name("CON.wav") == "_CON.wav"


def test_receive_cleans_up_stale_parts(bridge, inbox_home):
    inbox = user_library(inbox_home) / "Samples" / "LiveBridge"
    inbox.mkdir(parents=True)
    stale = inbox / ".old.wav.zzzzzz.part"
    stale.write_bytes(b"x")
    os.utime(stale, (time.time() - 3 * 86400, time.time() - 3 * 86400))
    run(bridge, "samples.receive", name="new.wav", data=_b64(b"ab"), upload_id="qqqqqq",
        total_size=4)
    assert not stale.exists() and (inbox / ".new.wav.qqqqqq.part").exists()


# --------------------------------------------------------------------------
# MIDI files
# --------------------------------------------------------------------------

def _varlen(value):
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.insert(0, (value & 0x7F) | 0x80)
        value >>= 7
    return bytes(out)


def _smf(tracks, division=96, fmt=1, meta=True):
    """A Standard MIDI File: ``tracks`` = [[(delta, status, data1, data2), ...]]; the
    first track also gets a name, 90 BPM and 3/4 when ``meta``."""
    chunks = []
    for index, events in enumerate(tracks):
        body = b""
        if meta:
            name = ("Track %d" % index).encode()
            body += b"\x00\xff\x03" + _varlen(len(name)) + name
            if index == 0:
                body += b"\x00\xff\x51\x03" + (666667).to_bytes(3, "big")
                body += b"\x00\xff\x58\x04\x03\x02\x18\x08"
        running = None
        for delta, status, data1, data2 in events:
            body += _varlen(delta)
            if status != running:
                body += bytes([status])
                running = status
            body += bytes([data1]) if (status & 0xF0) in (0xC0, 0xD0) else \
                bytes([data1, data2])
        body += b"\x00\xff\x2f\x00"
        chunks.append(b"MTrk" + struct.pack(">I", len(body)) + body)
    header = b"MThd" + struct.pack(">IHHH", 6, fmt, len(tracks), division)
    return header + b"".join(chunks)


def test_parse_midi_file():
    data = _smf([
        [(0, 0x90, 60, 100), (48, 0x90, 64, 90), (48, 0x80, 60, 0), (0, 0x90, 64, 0)],
        [(0, 0xC0, 5, 0), (96, 0x99, 36, 127), (24, 0x89, 36, 0), (0, 0x99, 38, 110)],
    ])
    parsed = samples_handlers.parse_midi_file(data)
    assert parsed["format"] == 1 and parsed["ticks_per_beat"] == 96
    assert parsed["tempo"] == 90.0 and parsed["signature"] == [3, 4]
    first, second = parsed["tracks"]
    assert first["name"] == "Track 0"
    assert first["notes"] == [(60, 0.0, 1.0, 100), (64, 0.5, 0.5, 90)]
    # running status, note-on velocity 0 = off, channel 10, an unterminated note
    assert second["notes"][0] == (36, 1.0, 0.25, 127)
    assert second["notes"][1][0] == 38 and second["channels"] == [10]
    with pytest.raises(ValueError):
        samples_handlers.parse_midi_file(b"RIFF....")
    smpte = bytearray(data)
    smpte[12:14] = struct.pack(">H", 0xE728)
    with pytest.raises(ValueError):
        samples_handlers.parse_midi_file(bytes(smpte))


def test_import_midi_file_session_and_arrangement(bridge, song, tmp_path):
    path = tmp_path / "Chords 01.mid"
    path.write_bytes(_smf([
        [(0, 0x90, 60, 100), (0, 0x90, 64, 100), (384, 0x80, 60, 0), (0, 0x80, 64, 0)],
        [(0, 0x90, 36, 120), (96, 0x80, 36, 0)],
    ]))
    count = len(song.tracks)
    made = run(bridge, "samples.import_midi", file_path=str(path))
    assert made["created_track"] and len(song.tracks) == count + 1
    clip = song.tracks[-1].clip_slots[made["slot"]].clip
    assert made["notes_added"] == 3 and clip.name == "Chords 01"
    assert made["length"] == 6.0, "4 beats of notes rounded up to whole 3/4 bars"
    assert sorted(n.pitch for n in clip.get_all_notes_extended()) == [36, 60, 64]
    assert made["route"] == "clip_slot.create_clip + add_new_notes"
    assert made["midi"]["tempo"] == 90.0
    picked = run(bridge, "samples.import_midi", file_path=str(path), track="Bass", slot=3,
                 midi_track="track 1")
    bass_clip = song.tracks[0].clip_slots[3].clip
    assert picked["notes_added"] == 1 and bass_clip.name == "Track 1"
    arr = run(bridge, "samples.import_midi", file_path=str(path), track="Bass",
              mode="arrangement", time="3.1.1")
    assert arr["route"] == "track.create_midi_clip + add_new_notes"
    assert arr["time"] == 8.0 and arr["clip"]["start_time"] == 8.0


def test_import_midi_refusals(bridge, song, tmp_path):
    path = tmp_path / "x.mid"
    path.write_bytes(_smf([[(0, 0x90, 60, 100), (96, 0x80, 60, 0)]]))
    assert fail(bridge, "samples.import_midi", file_path=str(path),
                track="Vocals")["type"] == "invalid_state"
    assert fail(bridge, "samples.import_midi", file_path=str(path), track="Bass",
                slot=0)["type"] == "invalid_state"          # slot 0 already has a clip
    assert fail(bridge, "samples.import_midi", file_path=str(path), midi_track=4)["type"] == \
        "not_found"
    wav = write_wav(tmp_path / "a.wav", 0.01)
    assert fail(bridge, "samples.import_midi", file_path=wav)["type"] == "bad_args"
    empty = tmp_path / "empty.mid"
    empty.write_bytes(_smf([[(0, 0xC0, 1, 0)]]))
    assert fail(bridge, "samples.import_midi", file_path=str(empty))["type"] == \
        "invalid_state"
    assert fail(bridge, "samples.import_midi", file_path=str(tmp_path / "nope.mid"))[
        "type"] == "not_found"


# --------------------------------------------------------------------------
# locations: Library.cfg, Windows known folders
# --------------------------------------------------------------------------

LIBRARY_CFG = """<?xml version="1.0" encoding="UTF-8"?>
<Ableton MajorVersion="5" MinorVersion="12.0_12402" Creator="Ableton Live 12.4.5">
  <ContentLibrary>
    <UserLibrary>
      <LibraryProject Id="0">
        <ProjectLocation />
        <ProjectName Value="User Library" />
        <ProjectPath Value="{base}" />
      </LibraryProject>
    </UserLibrary>
    <PreferredFactoryPacksInstallationPath Value="{packs}" />
    <SpliceDownloadFolderModeMember Value="{mode}" />
    <CustomSpliceDownloadPathMember Value="{custom}" />
  </ContentLibrary>
</Ableton>
"""


def test_library_cfg_is_read_for_user_library_and_splice(tmp_path):
    home = tmp_path / "home"
    prefs = home / "Library" / "Preferences" / "Ableton"
    base = tmp_path / "Custom Place"
    for version, mode in (("Live 12.0.10", "UserLibrary"), ("Live 12.4.5", "Custom")):
        (prefs / version).mkdir(parents=True)
        (prefs / version / "Library.cfg").write_text(LIBRARY_CFG.format(
            base=str(base) if version.endswith("4.5") else "/old", packs="/Packs", mode=mode,
            custom="/Volumes/SSD/Splice"), encoding="utf-8")
    (prefs / "Live 11.3.2").mkdir()
    dirs = samples_handlers.live_preferences_dirs(environ={"HOME": str(home)},
                                                  windows=False, home=str(home))
    assert [os.path.basename(d) for d in dirs] == ["Live 12.4.5", "Live 12.0.10", "Live 11.3.2"]
    running_first = samples_handlers.live_preferences_dirs(
        environ={"HOME": str(home)}, windows=False, home=str(home), version="12.0.10")
    assert os.path.basename(running_first[0]) == "Live 12.0.10"
    library = samples_handlers.live_library_config(environ={"HOME": str(home)}, windows=False,
                                                   home=str(home))
    assert library["user_library"] == str(base) + ("\\" if os.name == "nt" else "/") + \
        "User Library"
    assert library["factory_packs"] == "/Packs"
    assert library["splice_download_mode"] == "Custom"
    candidates = samples_handlers.user_library_candidates(
        environ={"HOME": str(home)}, windows=False, home=str(home), library=library)
    assert candidates[0] == library["user_library"]
    splice = samples_handlers.splice_candidates(environ={"HOME": str(home)}, windows=False,
                                                home=str(home), library=library)
    assert "/Volumes/SSD/Splice" in splice
    packs = samples_handlers.factory_packs_candidates(environ={"HOME": str(home)},
                                                      windows=False, home=str(home),
                                                      library=library)
    assert packs[0] == "/Packs"
    assert samples_handlers.read_library_cfg(str(tmp_path / "missing.cfg")) == {}
    broken = tmp_path / "broken.cfg"
    broken.write_text("<Ableton><", encoding="utf-8")
    assert samples_handlers.read_library_cfg(str(broken)) == {}


def test_library_cfg_windows_layout(tmp_path):
    appdata = tmp_path / "AppData" / "Roaming"
    folder = appdata / "Ableton" / "Live 12.4.5" / "Preferences"
    folder.mkdir(parents=True)
    (folder / "Library.cfg").write_text(LIBRARY_CFG.format(
        base="D:\\Music\\Ableton", packs="", mode="UserLibrary", custom=""), encoding="utf-8")
    library = samples_handlers.live_library_config(
        environ={"APPDATA": str(appdata), "USERPROFILE": "C:\\Users\\V"}, windows=True,
        home="C:\\Users\\V")
    assert library["user_library"] == "D:\\Music\\Ableton\\User Library"
    assert "factory_packs" not in library
    splice = samples_handlers.splice_candidates(environ={"USERPROFILE": "C:\\Users\\V"},
                                                windows=True, library=library,
                                                shell_folders={})
    assert "D:\\Music\\Ableton\\User Library\\Samples\\Splice" in splice


def test_windows_known_folders_come_first(monkeypatch):
    """OneDrive folder backup / a moved Documents folder: the registry's known folders
    (User Shell Folders) win over %USERPROFILE%\\Documents."""
    env = {"USERPROFILE": "C:\\Users\\V"}
    shell = {"Personal": "E:\\Docs", "Desktop": "E:\\Desk",
             "{374DE290-123F-4565-9164-39C4925E467B}": "E:\\Downloads"}
    library = samples_handlers.user_library_candidates(environ=env, windows=True,
                                                       shell_folders=shell)
    assert library[:2] == ["E:\\Docs\\Ableton\\User Library",
                           "C:\\Users\\V\\Documents\\Ableton\\User Library"]
    splice = samples_handlers.splice_candidates(environ=env, windows=True, shell_folders=shell)
    assert "E:\\Docs\\Splice\\sounds" in splice
    downloads, desktop = samples_handlers.download_folders(environ=env, windows=True,
                                                           shell_folders=shell)
    assert downloads == ["E:\\Downloads", "C:\\Users\\V\\Downloads"]
    assert desktop[:2] == ["E:\\Desk", "C:\\Users\\V\\Desktop"]
    # the registry reader itself: %USERPROFILE% in the stored value is expanded
    fake_values = {"Personal": "%USERPROFILE%\\OneDrive\\Documents"}

    class FakeWinreg:
        HKEY_CURRENT_USER = object()

        @staticmethod
        def OpenKey(root, path):
            assert path.endswith("User Shell Folders")
            return "key"

        @staticmethod
        def QueryValueEx(key, name):
            if name not in fake_values:
                raise OSError(name)
            return fake_values[name], 2

        @staticmethod
        def CloseKey(key):
            return None

    monkeypatch.setitem(sys.modules, "winreg", FakeWinreg)
    assert samples_handlers.windows_shell_folders(env) == \
        {"Personal": "C:\\Users\\V\\OneDrive\\Documents"}


def test_locations_report_inbox_and_download_folders(bridge, inbox_home):
    (inbox_home / "Downloads").mkdir()
    data = run(bridge, "samples.locations")
    assert data["inbox"]["path"].endswith(os.path.join("User Library", "Samples", "LiveBridge"))
    assert data["downloads"] == {"path": str(inbox_home / "Downloads"), "exists": True}
    assert any(e["path"] == str(inbox_home / "Desktop") for e in data["download_folders"])
    assert "live_library_cfg" in data
