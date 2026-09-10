"""Module E — Splice tools (MCP side only): setup info, importing the newest downloads and
watching the download folder. Runs against the fake bridge (validation / canned answers) and
the real stub bridge over TCP with real temp files."""

import asyncio
import json
import os
import sys
import threading
import time
import wave
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parent
for _p in (str(_TESTS.parent / "mcp_server"), str(_TESTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fake_bridge import FakeBridge  # noqa: E402
from livebridge_mcp.client import BridgeClient  # noqa: E402
from livebridge_mcp.server import create_app  # noqa: E402
import livebridge_mcp.tools.splice as splice_tools  # noqa: E402


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


def write_wav(path, seconds=0.25):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(22050)
        handle.writeframes(b"\x00" * int(seconds * 22050) * 2)
    return str(path)


def downloads(folder, names, pause=0.02):
    """Create files one after another so their arrival times are ordered."""
    paths = []
    for name in names:
        paths.append(write_wav(Path(folder) / name))
        time.sleep(pause)
    return paths


@pytest.fixture(autouse=True)
def _no_splice_env(monkeypatch):
    monkeypatch.delenv("LIVEBRIDGE_SPLICE_DIR", raising=False)


@pytest.fixture()
def fake_app():
    fake = FakeBridge().start()
    client = BridgeClient(host=fake.host, port=fake.port, timeout=5.0)
    try:
        yield fake, create_app(client)
    finally:
        client.close()
        fake.stop()


@pytest.fixture()
def live_app(tcp_bridge):
    client = BridgeClient(host="127.0.0.1", port=tcp_bridge.port, timeout=10.0)
    try:
        yield create_app(client)
    finally:
        client.close()


@pytest.fixture()
def fast_polling(monkeypatch):
    monkeypatch.setattr(splice_tools, "_POLL_SECONDS", 0.1)
    monkeypatch.setattr(splice_tools, "_STABLE_SECONDS", 0.1)


SPLICE_TOOLS = {"live_splice_setup_info", "live_splice_import_downloaded",
                "live_splice_watch_folder"}


def test_splice_tools_registered(fake_app):
    _fake, app = fake_app
    assert "splice" in app.tool_modules
    tools = {t.name: t for t in asyncio.run(app.list_tools())}
    assert SPLICE_TOOLS <= set(tools)
    for name in SPLICE_TOOLS:
        assert len(tools[name].description or "") > 200, name


def test_setup_info_static(fake_app):
    fake, app = fake_app
    count = len(fake.requests)
    info = call_tool(app, "live_splice_setup_info", {"check_live": False})
    assert info["splice_mcp_url"] == "https://mcp.splice.com/mcp"
    assert "claude mcp add --transport http splice https://mcp.splice.com/mcp" in \
        info["setup"]["claude_code"]
    assert any("Connectors" in step for step in info["setup"]["claude_desktop"])
    assert "Sounds+" in info["subscription"]["downloads"]
    assert "100" in info["subscription"]["limits"]
    assert "free" in info["subscription"]["search"]
    assert any("live_splice_import_downloaded" in step for step in info["workflow"])
    assert "Places" in info["live_browser"]
    assert "live" not in info and len(fake.requests) == count


def test_setup_info_asks_live(fake_app, monkeypatch):
    fake, app = fake_app
    fake.set_result("samples.locations", {"splice_folder": "/Users/v/Splice/sounds",
                                          "platform": "macOS", "splice": []})
    fake.set_result("browser.roots", {"roots": [], "splice": {"available": False,
                                                              "note": "n/a"}})
    monkeypatch.setenv("LIVEBRIDGE_SPLICE_DIR", "/custom")
    info = call_tool(app, "live_splice_setup_info")
    assert info["live"] == {"splice_folder": "/Users/v/Splice/sounds", "platform": "macOS",
                            "browser_splice": {"available": False, "note": "n/a"},
                            "LIVEBRIDGE_SPLICE_DIR": "/custom", "lan_mode": False}
    assert "download_asset" in info["splice_tools"]["names"]
    assert "files=[...]" in info["download_folder"]["save_to"]
    assert fake.requests[-1]["args"] == {"counts": False}


def test_setup_info_without_live():
    app = create_app(BridgeClient(host="127.0.0.1", port=1, timeout=1.0))
    info = call_tool(app, "live_splice_setup_info")
    assert info["splice_mcp_url"] == "https://mcp.splice.com/mcp"
    assert "error" in info["live"] and info["live"]["error"]


def test_import_validates_locally(fake_app):
    fake, app = fake_app
    count = len(fake.requests)
    for args in [
        {"mode": "loop"},
        {"newest": 0},
        {"newest": 99},
        {"mode": "arrangement", "slot": 1},
        {"time": 4},
        {"max_age_minutes": 0},
        {"track": 1, "track_name": "x"},
        {"pattern": " "},
        {"folder": " "},
    ]:
        result = call_tool(app, "live_splice_import_downloaded", args)
        assert isinstance(result, dict) and result.get("type") == "bad_args", (args, result)
    for args in [{"seconds": 0}, {"seconds": 1000}, {"mode": "x"}, {"pattern": ""}]:
        result = call_tool(app, "live_splice_watch_folder", args)
        assert isinstance(result, dict) and result.get("type") == "bad_args", (args, result)
    assert len(fake.requests) == count


def test_import_no_folder_found(fake_app):
    fake, app = fake_app
    fake.set_result("samples.locations", {"splice_folder": None, "splice": [
        {"path": "/Users/v/Splice/sounds", "exists": False},
        {"path": "/Users/v/Splice", "exists": False}]})
    result = call_tool(app, "live_splice_import_downloaded")
    assert result["type"] == "not_found"
    assert "/Users/v/Splice/sounds" in result["error"] and "folder=" in result["error"]


def test_import_newest_into_new_track(live_app, song, tmp_path):
    folder = tmp_path / "Splice" / "sounds" / "packs" / "Pack A"
    paths = downloads(folder, ["one.wav", "two.wav", "three.wav"])
    count = len(song.tracks)
    result = call_tool(live_app, "live_splice_import_downloaded",
                       {"folder": str(tmp_path / "Splice"), "newest": 2,
                        "track_name": "Splice"})
    assert result["folder_source"] == "argument"
    assert [f["name"] for f in result["found"]] == ["three.wav", "two.wav"]
    assert [r["route"] for r in result["imported"]] == ["clip_slot.create_audio_clip"] * 2
    assert "errors" not in result
    assert len(song.tracks) == count + 1
    track = song.tracks[-1]
    assert track.name == "Splice"
    assert [track.clip_slots[i].clip.file_path for i in (0, 1)] == [paths[2], paths[1]]
    assert result["imported"][1]["track"]["path"] == result["imported"][0]["track"]["path"]


def test_import_to_existing_track_slots_and_arrangement(live_app, song, tmp_path):
    folder = tmp_path / "dl"
    downloads(folder, ["a.wav", "b.wav"])
    session = call_tool(live_app, "live_splice_import_downloaded",
                        {"folder": str(folder), "newest": 2, "track": "Vocals", "slot": 2})
    assert [r["slot"] for r in session["imported"]] == [2, 3]
    vocals = song.tracks[1]
    assert vocals.clip_slots[2].clip.name == "b" and vocals.clip_slots[3].clip.name == "a"
    arrangement = call_tool(live_app, "live_splice_import_downloaded",
                            {"folder": str(folder), "newest": 2, "track": "Vocals",
                             "mode": "arrangement", "time": 4, "warp": True})
    starts = [r["clip"]["start_time"] for r in arrangement["imported"]]
    assert starts[0] == 4.0 and starts[1] == arrangement["imported"][0]["clip"]["end_time"]
    simpler = call_tool(live_app, "live_splice_import_downloaded",
                        {"folder": str(folder), "mode": "simpler"})
    assert simpler["imported"][0]["route"] == "track.insert_device + simpler.replace_sample"


def test_import_errors_are_collected(live_app, song, tmp_path):
    folder = tmp_path / "dl"
    downloads(folder, ["x.wav"])
    result = call_tool(live_app, "live_splice_import_downloaded",
                       {"folder": str(folder), "track": "Bass", "mode": "arrangement"})
    assert result["imported"] == []
    assert result["errors"][0]["type"] == "invalid_state"
    assert result["errors"][0]["file"].endswith("x.wav")


def test_import_nothing_found_and_age_filter(live_app, song, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = call_tool(live_app, "live_splice_import_downloaded", {"folder": str(empty)})
    assert result["imported"] == [] and "download with the Splice MCP" in result["hint"]
    folder = tmp_path / "old"
    path = write_wav(folder / "old.aif.wav")
    os.utime(path, (time.time() - 7200, time.time() - 7200))
    fresh = call_tool(live_app, "live_splice_import_downloaded",
                      {"folder": str(folder), "max_age_minutes": 60})
    # arrival time (ctime) is now even though the modified time is two hours old
    assert fresh["found"][0]["name"] == "old.aif.wav"
    missing = call_tool(live_app, "live_splice_import_downloaded",
                        {"folder": str(tmp_path / "does-not-exist")})
    assert missing["type"] == "not_found"


def test_import_uses_env_folder(live_app, song, tmp_path, monkeypatch):
    folder = tmp_path / "EnvSplice"
    downloads(folder, ["env.wav"])
    monkeypatch.setenv("LIVEBRIDGE_SPLICE_DIR", str(folder))
    result = call_tool(live_app, "live_splice_import_downloaded", {"track": "Vocals"})
    assert result["folder_source"] == "env LIVEBRIDGE_SPLICE_DIR"
    assert result["imported"][0]["clip"]["name"] == "env"


def test_import_detects_folder_on_live_machine(live_app, song, tmp_path, monkeypatch):
    home = tmp_path / "home"
    downloads(home / "Splice" / "sounds", ["detected.wav"])
    _isolate_home(home, monkeypatch)
    result = call_tool(live_app, "live_splice_import_downloaded", {"track": "Vocals"})
    assert result["folder_source"] == "detected on the Live machine"
    assert result["folder"] == str(home / "Splice" / "sounds")
    assert result["imported"][0]["file"]["name"] == "detected.wav"


def test_watch_folder_imports_new_file(live_app, song, tmp_path, fast_polling):
    folder = tmp_path / "watch"
    downloads(folder, ["existing.wav"])
    target = folder / "sub" / "fresh.wav"

    def later():
        time.sleep(0.4)
        write_wav(target)

    thread = threading.Thread(target=later)
    thread.start()
    try:
        result = call_tool(live_app, "live_splice_watch_folder",
                           {"seconds": 10, "folder": str(folder), "track": "Vocals"})
    finally:
        thread.join()
    assert result["new_file"]["path"] == str(target) and result["new_file"]["size"] > 0
    assert result["imported"]["route"] == "clip_slot.create_audio_clip"
    assert song.tracks[1].clip_slots[1].clip.file_path == str(target)
    assert result["waited_s"] < 10


def test_watch_folder_report_only_and_timeout(live_app, song, tmp_path, fast_polling):
    folder = tmp_path / "watch"
    folder.mkdir()
    result = call_tool(live_app, "live_splice_watch_folder",
                       {"seconds": 1, "folder": str(folder)})
    assert result["new_file"] is None and "nothing new" in result["hint"]
    assert result["waited_s"] >= 1

    def later():
        time.sleep(0.3)
        write_wav(folder / "report.wav")

    thread = threading.Thread(target=later)
    thread.start()
    try:
        result = call_tool(live_app, "live_splice_watch_folder",
                           {"seconds": 10, "folder": str(folder), "import_file": False})
    finally:
        thread.join()
    assert result["new_file"]["name"] == "report.wav" and "imported" not in result
    missing = call_tool(live_app, "live_splice_watch_folder",
                        {"seconds": 1, "folder": str(tmp_path / "nope")})
    assert missing["type"] == "not_found"


# --------------------------------------------------------------------------- review fixes

import livebridge_mcp.tools.samples as sample_tools  # noqa: E402
from LiveBridge.handlers import samples as samples_handlers  # noqa: E402


def test_truncated_scan_is_an_error_not_a_wrong_newest(live_app, song, tmp_path, monkeypatch):
    """A big library whose full scan stops early (entry cap / 5 s, in folder order, not by
    date) must not import an older file as "the newest" — the tool answers an error."""
    library = tmp_path / "Splice"
    downloads(library / "packA", ["old%03d.wav" % i for i in range(200)], pause=0)
    downloads(library / "packB", ["fresh.wav"], pause=0)
    time.sleep(0.2)
    monkeypatch.setattr(splice_tools, "_RECENT_SECONDS", 0.1)
    monkeypatch.setattr(splice_tools, "_FULL_SCAN_ENTRIES", 150)
    count = len(song.tracks)
    result = call_tool(live_app, "live_splice_import_downloaded",
                       {"folder": str(library), "newest": 2})
    assert result["type"] == "invalid_state" and "stopped before" in result["error"]
    assert "files=[...]" in result["error"] and len(song.tracks) == count
    # the fast path: a recent-files pass does not need the full scan at all
    monkeypatch.setattr(splice_tools, "_RECENT_SECONDS", 86400.0)
    fine = call_tool(live_app, "live_splice_import_downloaded",
                     {"folder": str(library), "track": "Vocals"})
    assert fine["found"][0]["name"] == "fresh.wav"


def test_simpler_mode_with_a_track_takes_one_file(fake_app):
    fake, app = fake_app
    count = len(fake.requests)
    for args in ({"mode": "simpler", "track": "OneShots", "newest": 3},
                 {"mode": "simpler", "track": 2, "files": ["/a.wav", "/b.wav"]}):
        result = call_tool(app, "live_splice_import_downloaded", args)
        assert result["type"] == "bad_args" and "drum_rack" in result["error"], args
    assert len(fake.requests) == count


def test_drum_rack_mode_builds_a_kit(live_app, song, tmp_path):
    folder = tmp_path / "shots"
    downloads(folder, ["kick.wav", "snare.wav", "hat.wav"])
    count = len(song.tracks)
    result = call_tool(live_app, "live_splice_import_downloaded",
                       {"folder": str(folder), "newest": 3, "mode": "drum_rack"})
    assert "errors" not in result, result
    assert [r["pad"]["note"] for r in result["imported"]] == [36, 37, 38]
    assert [r["pad"]["name"] for r in result["imported"]] == ["hat", "snare", "kick"]
    assert len(song.tracks) == count + 1 and song.tracks[-1].name == "Splice Kit"
    rack = song.tracks[-1].devices[0]
    assert rack.can_have_drum_pads and len(rack.chains) == 3
    more = call_tool(live_app, "live_splice_import_downloaded",
                     {"folder": str(folder), "newest": 2, "mode": "drum_rack",
                      "track": "Drums", "note": "E1"})
    assert [r["pad"]["note"] for r in more["imported"]] == [40, 41]


def test_files_argument_imports_exact_paths(live_app, song, tmp_path):
    first, second = downloads(tmp_path / "picked", ["b.wav", "a.wav"])
    midi = tmp_path / "picked" / "chords.mid"
    midi.write_bytes(b"MThd" + (6).to_bytes(4, "big") + (0).to_bytes(2, "big") +
                     (1).to_bytes(2, "big") + (96).to_bytes(2, "big") + b"MTrk" +
                     (12).to_bytes(4, "big") + b"\x00\x90\x3c\x64\x60\x80\x3c\x00\x00\xff\x2f"
                     b"\x00")
    result = call_tool(live_app, "live_splice_import_downloaded",
                       {"files": [first, str(tmp_path / "missing.wav"), second, str(midi)],
                        "track": "Vocals", "slot": "Chorus"})
    assert [f["name"] for f in result["found"]] == ["b.wav", "missing.wav", "a.wav",
                                                   "chords.mid"]
    assert [r["slot"] for r in result["imported"][:2]] == [2, 3]
    assert result["errors"][0]["type"] == "not_found"
    assert result["errors"][1]["type"] == "invalid_state"      # .mid onto an audio track
    midi_only = call_tool(live_app, "live_splice_import_downloaded", {"files": [str(midi)]})
    assert midi_only["imported"][0]["route"] == "clip_slot.create_clip + add_new_notes"
    arr = call_tool(live_app, "live_splice_import_downloaded",
                    {"files": [first], "track": "Vocals", "mode": "arrangement",
                     "time": "2.1.1"})
    assert arr["imported"][0]["clip"]["start_time"] == 4.0
    for args in ({"files": []}, {"files": [first], "newest": 2},
                 {"files": [first], "folder": str(tmp_path)}, {"upload": True},
                 {"note": 36}, {"mode": "arrangement", "slot": 1}):
        assert call_tool(live_app, "live_splice_import_downloaded", args)["type"] == \
            "bad_args", args


def _isolate_home(home, monkeypatch):
    """HOME/USERPROFILE/APPDATA and the Windows known folders -> ``home`` (never the tester's
    real User Library); returns the User Library path the Live side will use."""
    for sub in (("Music", "Ableton", "User Library"), ("Documents", "Ableton", "User Library")):
        home.joinpath(*sub).mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    monkeypatch.setattr(samples_handlers, "windows_shell_folders", lambda environ=None: {})
    return home / ("Documents" if os.name == "nt" else "Music") / "Ableton" / "User Library"


def test_files_are_uploaded_to_the_live_machine(live_app, song, tmp_path, monkeypatch):
    library = _isolate_home(tmp_path / "livehome", monkeypatch)
    monkeypatch.setattr(sample_tools, "UPLOAD_CHUNK", 1000)
    local = downloads(tmp_path / "claude_machine", ["Vox Chop.wav"])[0]
    result = call_tool(live_app, "live_splice_import_downloaded",
                       {"files": [local], "track": "Vocals", "upload": True})
    inbox = library / "Samples" / "LiveBridge" / "Splice"
    assert result["found"][0]["path"] == str(inbox / "Vox Chop.wav")
    assert result["imported"][0]["uploaded"]["from"] == local
    assert song.tracks[1].clip_slots[1].clip.file_path == str(inbox / "Vox Chop.wav")
    assert Path(local).read_bytes() == (inbox / "Vox Chop.wav").read_bytes()


def test_sample_import_uploads_automatically_in_lan_mode(fake_app, tmp_path, monkeypatch):
    """Claude's machine has the file, Live (on another machine) does not: it is sent over
    with samples.receive and the Live-side path is imported."""
    fake, app = fake_app
    real_is_remote = sample_tools.bridge_is_remote
    local = downloads(tmp_path, ["Splice Loop.wav"])[0]
    fake.set_result("samples.inspect", {"exists": False})
    fake.set_result("samples.receive", {"done": True, "path": "D:\\Inbox\\Splice Loop.wav",
                                        "size": 10})
    fake.set_result("samples.import", {"route": "clip_slot.create_audio_clip"})
    monkeypatch.setattr(sample_tools, "bridge_is_remote", lambda _bridge: True)
    result = call_tool(app, "live_sample_import", {"file_path": local, "track": 1})
    cmds = [r["cmd"] for r in fake.requests]
    assert cmds[-3:] == ["samples.inspect", "samples.receive", "samples.import"]
    assert fake.requests[-1]["args"]["file_path"] == "D:\\Inbox\\Splice Loop.wav"
    assert fake.requests[-2]["args"]["sha256"] and fake.requests[-2]["args"]["offset"] == 0
    assert result["uploaded"] == {"from": local, "to": "D:\\Inbox\\Splice Loop.wav",
                                  "size": 10}
    # Live sees the same path (same machine through a LAN address): nothing is sent
    fake.set_result("samples.inspect", {"exists": True})
    fake.requests.clear()
    call_tool(app, "live_sample_import", {"file_path": local, "track": 1})
    assert [r["cmd"] for r in fake.requests] == ["samples.inspect", "samples.import"]
    # on localhost nothing is checked or sent
    monkeypatch.setattr(sample_tools, "bridge_is_remote", lambda _bridge: False)
    fake.requests.clear()
    call_tool(app, "live_sample_import", {"file_path": local, "track": 1})
    assert [r["cmd"] for r in fake.requests] == ["samples.import"]
    assert real_is_remote(BridgeClient(host="192.168.1.20", port=1)) is True
    assert real_is_remote(BridgeClient(host="127.0.0.1", port=1)) is False
    assert real_is_remote(BridgeClient(host="localhost", port=1)) is False


def test_sample_upload_tool(live_app, tmp_path, monkeypatch):
    _isolate_home(tmp_path / "h", monkeypatch)
    local = downloads(tmp_path, ["Bass 01.wav"])[0]
    sent = call_tool(live_app, "live_sample_upload", {"local_path": local,
                                                      "subfolder": "Pack", "name": "B.wav"})
    assert sent["done"] and sent["path"].endswith(os.path.join("LiveBridge", "Pack", "B.wav"))
    missing = call_tool(live_app, "live_sample_upload", {"local_path": str(tmp_path / "x.wav")})
    assert missing["type"] == "not_found"
    bad = call_tool(live_app, "live_sample_upload", {"url": "ftp://example.com/a.wav"})
    assert bad["type"] == "bad_args"


def test_default_search_includes_downloads(live_app, song, tmp_path, monkeypatch):
    """No Splice app folder: a file saved to ~/Downloads (Splice MCP / web download) is
    found, and every searched folder is reported."""
    home = tmp_path / "home"
    downloads(home / "Downloads", ["from web.wav"])
    (home / "Desktop").mkdir()
    _isolate_home(home, monkeypatch)
    result = call_tool(live_app, "live_splice_import_downloaded",
                       {"track": "Vocals", "max_age_minutes": 5})
    assert result["folder_source"] == "detected on the Live machine"
    assert result["found"][0]["name"] == "from web.wav"
    assert str(home / "Downloads") in result.get("searched", [result["folder"]])


def test_watch_folder_schema_and_defaults(fake_app):
    _fake, app = fake_app
    tools = {t.name: t for t in asyncio.run(app.list_tools())}
    schema = tools["live_splice_watch_folder"].input_schema
    assert "ctx" not in schema.get("properties", {}), "the MCP context is injected"
    assert schema["properties"]["seconds"]["default"] <= 45
    assert splice_tools._POLL_SECONDS >= 3
    assert "Splice MCP" in tools["live_splice_watch_folder"].description
    import_schema = tools["live_splice_import_downloaded"].input_schema["properties"]
    types_of = {name: json.dumps(import_schema[name]) for name in ("slot", "time")}
    assert "string" in types_of["slot"] and "string" in types_of["time"]
