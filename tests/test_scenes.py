"""Module A — scenes: bridge commands on the stub song and the MCP tools
(fake bridge for argument forwarding/validation, real TCP end to end)."""

import asyncio
import json
import sys
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parent
for _p in (str(_TESTS.parent / "mcp_server"), str(_TESTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import Live  # noqa: E402

from fake_bridge import FakeBridge  # noqa: E402
from livebridge_mcp.client import BridgeClient  # noqa: E402
from livebridge_mcp.server import create_app  # noqa: E402
from live_stub_ext import transport_live  # noqa: E402


@pytest.fixture(autouse=True)
def live_12_4_5():
    """Make the stub behave like the real Live 12.4.5 (tests/live_stub_ext/transport_live.py):
    song-length limits, scene creation, deferral switchable with ``live_12_4_5.defer()``."""
    uninstall = transport_live.install(Live)
    try:
        yield transport_live
    finally:
        uninstall()



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
# reading
# --------------------------------------------------------------------------

def test_list(bridge, song):
    data = run(bridge, "scenes.list")
    assert data["count"] == 4 and data["selected"] == 0
    names = [s["name"] for s in data["scenes"]]
    assert names == ["Intro", "Verse", "Chorus", "Drop"]
    first = data["scenes"][0]
    assert first["path"] == "song.scenes[0]" and first["index"] == 0
    assert "clips" not in first and "kind" not in first
    assert "tempo_enabled" not in first  # compact: false flags omitted
    minimal = run(bridge, "scenes.list", detail="minimal", offset=1, limit=2)
    assert [s["name"] for s in minimal["scenes"]] == ["Verse", "Chorus"]
    assert minimal["offset"] == 1 and "color_index" not in minimal["scenes"][0]
    with_clips = run(bridge, "scenes.list", include_clips=True)
    intro_clips = with_clips["scenes"][0]["clips"]
    assert [(c["track"], c["name"]) for c in intro_clips] == [
        (0, "Bass Loop"), (1, "Vox Take"), (2, "Beat")]
    assert intro_clips[0]["track_name"] == "Bass"
    assert with_clips["scenes"][2]["clips"] == []
    assert fail(bridge, "scenes.list", detail="big")["type"] == "bad_args"


def test_get_by_index_name_prefix_path(bridge, song):
    assert run(bridge, "scenes.get", scene=1)["name"] == "Verse"
    assert run(bridge, "scenes.get", scene="Chorus")["index"] == 2
    assert run(bridge, "scenes.get", scene="dr")["name"] == "Drop"
    assert run(bridge, "scenes.get", scene="song.scenes[3]")["name"] == "Drop"
    assert run(bridge, "scenes.get", scene=-1)["name"] == "Drop"
    data = run(bridge, "scenes.get", scene=0)
    assert data["tempo_enabled"] is False and data["time_signature_enabled"] is False
    assert len(data["clips"]) == 3
    assert fail(bridge, "scenes.get", scene=9)["type"] == "not_found"
    assert fail(bridge, "scenes.get", scene="Outro")["type"] == "not_found"


# --------------------------------------------------------------------------
# structure
# --------------------------------------------------------------------------

def test_create(bridge, song):
    created = run(bridge, "scenes.create", index=1, name="Break", color_index=12,
                  tempo=100, time_signature="3/4", select=True)
    assert created["index"] == 1 and created["name"] == "Break"
    assert created["color_index"] == 12
    assert created["tempo"] == 100.0 and created["signature"] == "3/4"
    assert [s.name for s in song.scenes] == ["Intro", "Break", "Verse", "Chorus", "Drop"]
    assert song.view.selected_scene == song.scenes[1]
    assert len(song.tracks[0].clip_slots) == 5
    end = run(bridge, "scenes.create")
    assert end["index"] == 5
    assert fail(bridge, "scenes.create", index=42)["type"] == "bad_args"
    assert fail(bridge, "scenes.create", color_index=99)["type"] == "bad_args"


def test_create_does_not_inherit_tempo_or_steal_the_selection(bridge, song):
    # Live 12.4.5: create_scene copies the tempo/signature of the scene above and selects the
    # new scene.  scenes.create gives a clean scene and keeps the selection (unless select).
    song.scenes[3].tempo = 128.0
    song.scenes[3].tempo_enabled = True
    song.scenes[3].time_signature_numerator = 3
    song.view.selected_scene = song.scenes[0]
    created = run(bridge, "scenes.create", name="Clean")
    assert created["index"] == 4 and "tempo" not in created and "signature" not in created
    assert song.scenes[4].tempo_enabled is False
    assert song.scenes[4].time_signature_enabled is False
    assert song.view.selected_scene == song.scenes[0]
    with_tempo = run(bridge, "scenes.create", tempo=90, select=True)
    assert with_tempo["tempo"] == 90.0 and "signature" not in with_tempo
    assert song.view.selected_scene == song.scenes[5]
    assert fail(bridge, "scenes.create", time_signature="7/3")["type"] == "bad_args"
    assert len(song.scenes) == 6                          # validated before creating


def test_scene_path_must_be_a_scene(bridge, song):
    error = fail(bridge, "scenes.delete", scene="song.tracks[0]")
    assert error["type"] == "bad_args" and "not a scene" in error["message"]
    assert len(song.scenes) == 4


def test_create_is_one_undo_step(bridge, song):
    before = len(song._undo_steps)
    run(bridge, "scenes.create", name="X", tempo=90)
    assert len(song._undo_steps) == before + 1


def test_delete(bridge, song):
    result = run(bridge, "scenes.delete", scene="Verse")
    assert result == {"deleted": {"index": 1, "name": "Verse"}, "scene_count": 3}
    assert [s.name for s in song.scenes] == ["Intro", "Chorus", "Drop"]
    run(bridge, "scenes.delete", scene=0)
    run(bridge, "scenes.delete", scene=0)
    error = fail(bridge, "scenes.delete", scene=0)  # the last scene stays
    assert error["type"] == "invalid_state"


def test_duplicate(bridge, song):
    copy = run(bridge, "scenes.duplicate", scene="Intro", name="Intro 2")
    assert copy["index"] == 1 and copy["name"] == "Intro 2"
    assert song.tracks[0].clip_slots[1].clip.name == "Bass Loop"
    assert song.view.selected_scene == song.scenes[1]
    plain = run(bridge, "scenes.duplicate", scene=3)
    assert plain["name"] == "Chorus" and plain["index"] == 4


def test_set_rename_color(bridge, song):
    result = run(bridge, "scenes.set", scene="Drop", name="Big Drop", color="#FF0000",
                 color_index=5)
    assert result["name"] == "Big Drop" and result["color_index"] == 5
    assert song.scenes[3].color == 0xFF0000
    assert run(bridge, "scenes.rename", scene=0, name="Start")["name"] == "Start"
    assert run(bridge, "scenes.rename", scene=0, name="")["name"] == ""
    assert fail(bridge, "scenes.set", scene=0)["type"] == "bad_args"
    assert fail(bridge, "scenes.set", scene=0, color="reddish")["type"] == "bad_args"
    assert run(bridge, "scenes.set", scene=0, color="blue")["color_index"] == 22
    assert fail(bridge, "scenes.set", scene=0, name=5)["type"] == "bad_args"


def test_scene_tempo_and_signature(bridge, song):
    scene = song.scenes[2]
    result = run(bridge, "scenes.set", scene=2, tempo=140)
    assert result["tempo"] == 140.0 and result["tempo_enabled"] is True
    assert scene.tempo_enabled is True and scene.tempo == 140.0
    result = run(bridge, "scenes.set", scene=2, tempo_enabled=False)
    assert "tempo" not in result and scene.tempo == -1.0
    run(bridge, "scenes.set", scene=2, tempo=90, tempo_enabled=False)
    assert scene.tempo_enabled is False
    result = run(bridge, "scenes.set", scene=2, time_signature="7/8")
    assert result["signature"] == "7/8" and scene.time_signature_enabled
    result = run(bridge, "scenes.set", scene=2, time_signature_enabled=False)
    assert scene.time_signature_enabled is False and "signature" not in result
    # enabling without a value uses the song signature (stub: read-only flag)
    run(bridge, "scenes.set", scene=2, time_signature_enabled=True)
    assert (scene.time_signature_numerator, scene.time_signature_denominator) == (4, 4)
    assert fail(bridge, "scenes.set", scene=2, tempo=5)["type"] == "bad_args"
    assert fail(bridge, "scenes.set", scene=2, time_signature="7/9")["type"] == "bad_args"
    assert fail(bridge, "scenes.set", scene=2, time_signature="3/4",
                time_signature_enabled=False)["type"] == "bad_args"


def test_writable_time_signature_enabled_is_used_when_available(bridge, song, monkeypatch):
    """Live 12.4.5 exposes Scene.time_signature_enabled as rw (runtime dump)."""
    calls = []
    scene_class = type(song.scenes[0])
    original = scene_class.time_signature_enabled

    def setter(self, value):
        calls.append(value)
        if not value:
            self._numerator = -1
            self._denominator = -1

    monkeypatch.setattr(scene_class, "time_signature_enabled",
                        property(original.fget, setter))
    song.scenes[1].time_signature_numerator = 3
    run(bridge, "scenes.set", scene=1, time_signature_enabled=False)
    assert calls == [False] and song.scenes[1].time_signature_enabled is False


# --------------------------------------------------------------------------
# launching / selection / capture
# --------------------------------------------------------------------------

def test_fire(bridge, song):
    result = run(bridge, "scenes.fire", scene="Intro")
    assert result["fired"] == {"index": 0, "name": "Intro"} and result["selected"] == 0
    assert song.tracks[0].playing_slot_index == 0
    song.scenes[1].tempo = 150.0
    song.scenes[1].tempo_enabled = True
    run(bridge, "scenes.fire", scene=1, force_legato=True, select=False)
    assert song.tempo == 150.0
    assert song.view.selected_scene == song.scenes[0]  # select=False kept it


def test_fire_reports_clip_count_and_applies_scene_signature(bridge, song):
    result = run(bridge, "scenes.fire", scene="Intro")
    assert result["clip_count"] == 3
    song.scenes[3].time_signature_numerator = 7
    song.scenes[3].time_signature_denominator = 8
    empty = run(bridge, "scenes.fire", scene="Drop")
    assert empty["clip_count"] == 0
    assert (song.signature_numerator, song.signature_denominator) == (7, 8)


def test_fire_selected_advances(bridge, song):
    song.view.selected_scene = song.scenes[0]
    result = run(bridge, "scenes.fire")
    assert result["fired"]["index"] == 0 and result["selected"] == 1
    result = run(bridge, "scenes.fire")
    assert result["fired"]["index"] == 1 and result["selected"] == 2


def test_stop_all_and_select(bridge, song):
    run(bridge, "scenes.fire", scene=0)
    assert run(bridge, "scenes.stop_all", quantized=False) == {"stopped": True,
                                                              "quantized": False}
    assert song.tracks[0].playing_slot_index == -1
    selected = run(bridge, "scenes.select", scene="Chorus")
    assert selected["selected"] == {"index": 2, "name": "Chorus", "path": "song.scenes[2]"}
    assert song.view.selected_scene == song.scenes[2]


def test_capture(bridge, song):
    song.view.selected_scene = song.scenes[1]
    new = run(bridge, "scenes.capture")
    assert new["index"] == 2 and len(song.scenes) == 5
    assert new["name"] == "Verse"                 # Live copies the selected scene's name
    assert fail(bridge, "scenes.capture", mode="some")["type"] == "bad_args"


def test_limitation_error_is_unsupported(bridge, song, monkeypatch):
    def refuse(index):
        raise Live.Base.LimitationError("Live Intro: at most 16 scenes")
    monkeypatch.setattr(song, "create_scene", refuse)
    error = fail(bridge, "scenes.create")
    assert error["type"] == "unsupported" and "16 scenes" in error["message"]


def test_every_scene_command_is_registered(bridge):
    listing = run(bridge, "system.commands", namespace="scenes")
    assert {c["cmd"] for c in listing["commands"]} == {
        "scenes.list", "scenes.get", "scenes.create", "scenes.delete", "scenes.duplicate",
        "scenes.set", "scenes.rename", "scenes.fire", "scenes.stop_all", "scenes.select",
        "scenes.capture"}
    mutating = {c["cmd"] for c in listing["commands"] if c["mutating"]}
    assert mutating == {"scenes.create", "scenes.delete", "scenes.duplicate", "scenes.set",
                        "scenes.rename", "scenes.capture"}


# --------------------------------------------------------------------------
# MCP tools
# --------------------------------------------------------------------------

SCENE_TOOLS = {"live_scene_list", "live_scene_get", "live_scene_create", "live_scene_delete",
               "live_scene_duplicate", "live_scene_set", "live_scene_fire",
               "live_scene_capture"}


@pytest.fixture()
def fake_app():
    fake = FakeBridge().start()
    client = BridgeClient(host=fake.host, port=fake.port, timeout=5.0)
    try:
        yield fake, create_app(client)
    finally:
        client.close()
        fake.stop()


def test_scene_tools_registered(fake_app):
    _fake, app = fake_app
    assert "scenes" in app.tool_modules
    names = {t.name for t in asyncio.run(app.list_tools())}
    assert SCENE_TOOLS <= names


def test_scene_tools_forward_arguments(fake_app):
    fake, app = fake_app
    for cmd in ("scenes.list", "scenes.get", "scenes.create", "scenes.delete",
                "scenes.duplicate", "scenes.set", "scenes.fire", "scenes.capture"):
        fake.set_result(cmd, {"ok": cmd})

    def last():
        return fake.requests[-1]["cmd"], fake.requests[-1].get("args", {})

    call_tool(app, "live_scene_list", {"include_clips": True, "limit": 3})
    assert last() == ("scenes.list", {"detail": "summary", "include_clips": True, "limit": 3})
    call_tool(app, "live_scene_get", {"scene": "Drop"})
    assert last() == ("scenes.get", {"scene": "Drop"})
    call_tool(app, "live_scene_get", {"scene": 2})
    assert last() == ("scenes.get", {"scene": 2})
    call_tool(app, "live_scene_create", {"index": 1, "name": "Break", "tempo": 90,
                                         "time_signature": "3/4"})
    assert last() == ("scenes.create", {"index": 1, "name": "Break", "tempo": 90.0,
                                        "time_signature": "3/4"})
    call_tool(app, "live_scene_delete", {"scene": 0})
    assert last() == ("scenes.delete", {"scene": 0})
    call_tool(app, "live_scene_duplicate", {"scene": "Intro", "name": "Intro B"})
    assert last() == ("scenes.duplicate", {"scene": "Intro", "name": "Intro B"})
    call_tool(app, "live_scene_set", {"scene": 1, "color": "#00FF00", "tempo_enabled": False})
    assert last() == ("scenes.set", {"scene": 1, "color": "#00FF00", "tempo_enabled": False})
    call_tool(app, "live_scene_fire", {})
    assert last() == ("scenes.fire", {"force_legato": False})
    call_tool(app, "live_scene_fire", {"scene": "Verse", "force_legato": True})
    assert last() == ("scenes.fire", {"force_legato": True, "scene": "Verse", "select": True})
    call_tool(app, "live_scene_capture", {"mode": "all_except_selected"})
    assert last() == ("scenes.capture", {"mode": "all_except_selected"})


def test_scene_tools_validate_locally(fake_app):
    fake, app = fake_app
    count = len(fake.requests)
    for name, args in [
        ("live_scene_list", {"detail": "all"}),
        ("live_scene_list", {"offset": -1}),
        ("live_scene_get", {"scene": " "}),
        ("live_scene_create", {"index": -5}),
        ("live_scene_create", {"color_index": 70}),
        ("live_scene_create", {"tempo": 1000}),
        ("live_scene_create", {"time_signature": "4-4"}),
        ("live_scene_set", {"scene": 0}),
        ("live_scene_set", {"scene": 0, "time_signature": "5/3"}),
        ("live_scene_capture", {"mode": "most"}),
    ]:
        result = call_tool(app, name, args)
        assert isinstance(result, dict) and result.get("type") == "bad_args", (name, result)
    assert len(fake.requests) == count


def test_scene_tools_end_to_end(tcp_bridge, song):
    client = BridgeClient(host="127.0.0.1", port=tcp_bridge.port, timeout=5.0)
    try:
        app = create_app(client)
        listing = call_tool(app, "live_scene_list")
        assert listing["count"] == 4
        created = call_tool(app, "live_scene_create", {"name": "Outro", "tempo": 100})
        assert created["index"] == 4 and created["tempo"] == 100.0
        fired = call_tool(app, "live_scene_fire", {"scene": "Intro"})
        assert fired["fired"]["name"] == "Intro" and "is_playing" in fired
        changed = call_tool(app, "live_scene_set", {"scene": "Outro", "name": "End"})
        assert changed["name"] == "End"
        error = call_tool(app, "live_scene_get", {"scene": "Nope"})
        assert error["type"] == "not_found"
        assert call_tool(app, "live_scene_delete", {"scene": "End"})["scene_count"] == 4
    finally:
        client.close()
