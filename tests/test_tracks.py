"""Module B — tracks: every ``tracks.*`` command through the dispatcher on the stub song, and
every ``live_tracks_*`` MCP tool (argument forwarding against the fake bridge, plus an
end-to-end run through the real TCP bridge)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parent
for _path in (str(_TESTS.parent / "mcp_server"), str(_TESTS)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from fake_bridge import FakeBridge  # noqa: E402
from live_stub import factory  # noqa: E402

from livebridge_mcp.client import BridgeClient  # noqa: E402
from livebridge_mcp.server import create_app  # noqa: E402


def call(bridge, cmd, **args):
    response = bridge.dispatch({"id": "t", "cmd": cmd, "args": args})
    return response


def ok(bridge, cmd, **args):
    response = call(bridge, cmd, **args)
    assert response["ok"], response
    return response["result"]


def err(bridge, cmd, **args):
    response = call(bridge, cmd, **args)
    assert not response["ok"], response
    return response["error"]["type"], response["error"]["message"]


def call_tool(app, name, args=None):
    result = asyncio.run(app.call_tool(name, args or {}))
    content = getattr(result, "content", None)
    if content is None:
        content = result[0] if isinstance(result, tuple) else result
    blocks = [b.text for b in content if getattr(b, "type", None) == "text"]

    def _parse(chunk):
        try:
            return json.loads(chunk)
        except ValueError:
            return chunk
    return [_parse(b) for b in blocks] if len(blocks) > 1 else _parse("\n".join(blocks))


def tool_names(app):
    return {t.name for t in asyncio.run(app.list_tools())}


# --------------------------------------------------------------------------
# tracks.list / get / find
# --------------------------------------------------------------------------

def test_list_all_tracks_in_order(bridge):
    result = ok(bridge, "tracks.list", detail="minimal")
    names = [t["name"] for t in result["tracks"]]
    assert names == ["Bass", "Vocals", "Drums", "A-Reverb", "B-Delay", "Main"]
    assert result["total"] == 6
    assert result["tracks"][3]["path"] == "song.return_tracks[0]"
    assert result["tracks"][-1]["type"] == "master"


def test_list_filters_and_paging(bridge, song):
    assert [t["name"] for t in ok(bridge, "tracks.list", type="midi")["tracks"]] == \
        ["Bass", "Drums"]
    assert [t["name"] for t in ok(bridge, "tracks.list", type=["audio", "return"],
                                  detail="minimal")["tracks"]] == \
        ["Vocals", "A-Reverb", "B-Delay"]
    assert [t["name"] for t in ok(bridge, "tracks.list", name="RUM")["tracks"]] == ["Drums"]
    page = ok(bridge, "tracks.list", offset=1, limit=2, detail="minimal")
    assert page["total"] == 6 and page["count"] == 2
    assert [t["name"] for t in page["tracks"]] == ["Vocals", "Drums"]
    assert ok(bridge, "tracks.list", include_returns=False, include_master=False)["total"] == 3
    song.tracks[1].arm = True
    assert [t["name"] for t in ok(bridge, "tracks.list", armed=True)["tracks"]] == ["Vocals"]
    assert ok(bridge, "tracks.list", frozen=True)["total"] == 0
    song.tracks[2]._is_frozen = True  # stub-only switch: freezing is not in the LOM
    assert [t["name"] for t in ok(bridge, "tracks.list", frozen=True)["tracks"]] == ["Drums"]


def test_list_rejects_bad_arguments(bridge):
    assert err(bridge, "tracks.list", type="bus")[0] == "bad_args"
    assert err(bridge, "tracks.list", detail="huge")[0] == "bad_args"
    assert err(bridge, "tracks.list", offset="x")[0] == "bad_args"
    assert err(bridge, "tracks.list", bogus=1)[0] == "bad_args"


def test_list_group_type(bridge, song):
    factory.add_group_track(song, "Bus", members=[song.tracks[0], song.tracks[1]])
    groups = ok(bridge, "tracks.list", type="group", detail="minimal")["tracks"]
    assert [t["name"] for t in groups] == ["Bus"]


def test_get_track_by_many_specs(bridge, song):
    full = ok(bridge, "tracks.get", track="Bass")
    assert full["name"] == "Bass" and full["type"] == "midi"
    assert full["freeze"] == {"is_frozen": False, "can_be_frozen": True}
    assert full["color"].startswith("#")
    assert "clips" in full
    assert ok(bridge, "tracks.get", track=1, detail="minimal")["name"] == "Vocals"
    assert ok(bridge, "tracks.get", track="song.tracks[2]", detail="minimal")["name"] == "Drums"
    assert ok(bridge, "tracks.get", track="master", detail="minimal")["type"] == "master"
    assert ok(bridge, "tracks.get", track="main", detail="minimal")["type"] == "master"
    assert ok(bridge, "tracks.get", track="A", detail="minimal")["name"] == "A-Reverb"
    assert ok(bridge, "tracks.get", track="return b", detail="minimal")["name"] == "B-Delay"
    assert ok(bridge, "tracks.get", track="Reverb", detail="minimal")["name"] == "A-Reverb"
    assert ok(bridge, "tracks.get", track="areverb", detail="minimal")["name"] == "A-Reverb"
    assert ok(bridge, "tracks.get", track="selected", detail="minimal")["name"] == "Bass"
    assert ok(bridge, "tracks.get", track="voc", detail="minimal")["name"] == "Vocals"


def test_get_errors(bridge):
    assert err(bridge, "tracks.get", track="Nope")[0] == "not_found"
    assert err(bridge, "tracks.get", track=9)[0] == "not_found"
    assert err(bridge, "tracks.get", track=True)[0] == "bad_args"
    assert err(bridge, "tracks.get")[0] == "bad_args"


def test_get_group_lists_members(bridge, song):
    group = factory.add_group_track(song, "Bus", members=[song.tracks[0], song.tracks[2]])
    result = ok(bridge, "tracks.get", track="Bus", detail="summary")
    assert [m["name"] for m in result["members"]] == ["Bass", "Drums"]
    assert all(m["depth"] == 1 for m in result["members"])
    assert group.is_foldable


def test_find_ranks_exact_first(bridge, song):
    factory.add_track(song, "Bass 2", "midi")
    found = ok(bridge, "tracks.find", name="bass")
    assert [m["name"] for m in found["matches"]] == ["Bass", "Bass 2"]
    assert ok(bridge, "tracks.find", name="reverb")["matches"][0]["type"] == "return"
    assert ok(bridge, "tracks.find", name="zzz")["count"] == 0
    assert ok(bridge, "tracks.find", name="a", limit=1)["count"] == 1
    assert err(bridge, "tracks.find", name="  ")[0] == "bad_args"


# --------------------------------------------------------------------------
# create / delete / duplicate
# --------------------------------------------------------------------------

def test_create_midi_audio_return(bridge, song):
    midi = ok(bridge, "tracks.create", type="midi", name="Lead", color="red")
    assert midi["name"] == "Lead" and midi["path"] == "song.tracks[3]"
    assert song.tracks[3].color_index == 14
    audio = ok(bridge, "tracks.create", type="audio", index=0, color="#ff0000")
    assert audio["path"] == "song.tracks[0]" and audio["type"] == "audio"
    assert song.tracks[0].color == 0xFF0000
    ret = ok(bridge, "tracks.create", type="return", name="C Chorus", color=5)
    assert ret["path"] == "song.return_tracks[2]"
    assert len(song.tracks[1].mixer_device.sends) == 3


def test_create_after_selected_track(bridge, song):
    song.view.selected_track = song.tracks[0]
    result = ok(bridge, "tracks.create", type="midi", index=None, name="Next")
    assert result["path"] == "song.tracks[1]"


def test_create_is_one_undo_step(bridge, song):
    before = len(song._undo_steps)
    ok(bridge, "tracks.create", type="audio", name="X", color=[0, 128, 255])
    assert len(song._undo_steps) == before + 1


def test_create_errors(bridge, song):
    assert err(bridge, "tracks.create", type="group")[0] == "bad_args"
    assert err(bridge, "tracks.create", index=99)[0] == "not_found"
    assert err(bridge, "tracks.create", index=1.5)[0] == "bad_args"
    assert err(bridge, "tracks.create", color="ultraviolet")[0] == "bad_args"
    assert err(bridge, "tracks.create", color=[1, 2])[0] == "bad_args"


def test_create_edition_limit_is_unsupported(bridge, song, monkeypatch):
    import Live

    def refuse(self, Index=None):
        raise Live.Base.LimitationError("Intro allows 16 tracks")
    monkeypatch.setattr(type(song), "create_midi_track", refuse)
    kind, message = err(bridge, "tracks.create", type="midi")
    assert kind == "unsupported" and "edition" in message


def test_delete_regular_and_return(bridge, song):
    result = ok(bridge, "tracks.delete", track="Vocals")
    assert result["deleted"] == "Vocals" and result["track_count"] == 2
    assert [t.name for t in song.tracks] == ["Bass", "Drums"]
    result = ok(bridge, "tracks.delete", track="B")
    assert result["type"] == "return" and result["return_count"] == 1
    assert len(song.tracks[0].mixer_device.sends) == 1
    assert err(bridge, "tracks.delete", track="master")[0] == "bad_args"


def test_cannot_delete_the_last_track(bridge, song):
    ok(bridge, "tracks.delete", track=0)
    ok(bridge, "tracks.delete", track=0)
    assert err(bridge, "tracks.delete", track=0)[0] == "invalid_state"


def test_duplicate(bridge, song):
    result = ok(bridge, "tracks.duplicate", track="Bass", name="Bass Copy")
    assert result["path"] == "song.tracks[1]" and result["name"] == "Bass Copy"
    assert song.tracks[1].clip_slots[0].has_clip
    assert err(bridge, "tracks.duplicate", track="A")[0] == "bad_args"


def test_duplicate_returns_the_copy_live_selected(bridge, song):
    factory.add_group_track(song, "Bus", members=[song.tracks[0]], index=0)
    result = ok(bridge, "tracks.duplicate", track="Bus")
    assert result["name"] == "Bus" and song.view.selected_track is song.tracks[1]
    assert result["path"] == "song.tracks[1]"


# --------------------------------------------------------------------------
# tracks.set
# --------------------------------------------------------------------------

def test_set_rename_colour_mute_solo(bridge, song):
    result = ok(bridge, "tracks.set", track="Bass", name="Sub", color="blue", mute=True)
    state = result["tracks"][0]
    assert state["name"] == "Sub" and state["mute"] is True and state["color_index"] == 22
    ok(bridge, "tracks.set", track="Sub", mute="toggle")
    assert song.tracks[0].mute is False
    ok(bridge, "tracks.set", track=0, color=0x00FF00)
    assert song.tracks[0].color == 0x00FF00


def test_set_many_tracks_and_exclusive_solo(bridge, song):
    ok(bridge, "tracks.set", track=["Bass", "Vocals"], solo=True)
    assert song.tracks[0].solo and song.tracks[1].solo
    ok(bridge, "tracks.set", track="Drums", solo=True, exclusive=True)
    assert [t.solo for t in song.tracks] == [False, False, True]
    ok(bridge, "tracks.set", track="all", mute=True)
    assert all(t.mute for t in song.tracks) and all(t.mute for t in song.return_tracks)


def test_set_arm_exclusive_and_errors(bridge, song):
    song._exclusive_arm = False  # stub-only preference switch
    ok(bridge, "tracks.set", track=["Bass", "Vocals"], arm=True)
    assert song.tracks[0].arm and song.tracks[1].arm
    ok(bridge, "tracks.set", track="Drums", arm=True, exclusive=True)
    assert [t.arm for t in song.tracks] == [False, False, True]
    kind, message = err(bridge, "tracks.set", track="A", arm=True)
    assert kind == "invalid_state" and "cannot be armed" in message
    assert err(bridge, "tracks.set", track="master", mute=True)[0] == "invalid_state"


def test_set_partial_failures_are_listed(bridge, song):
    result = ok(bridge, "tracks.set", track=["Bass", "A"], arm=True)
    assert [t["name"] for t in result["tracks"]] == ["Bass"]
    assert result["errors"][0]["track"] == "A-Reverb"


def test_set_validation(bridge):
    assert err(bridge, "tracks.set", track="Bass")[0] == "bad_args"
    assert err(bridge, "tracks.set", track=["Bass", "Drums"], name="x")[0] == "bad_args"
    assert err(bridge, "tracks.set", track="Bass", mute="maybe")[0] == "bad_args"
    assert err(bridge, "tracks.set", track=[], mute=True)[0] == "bad_args"
    assert err(bridge, "tracks.set", track="Bass", fold=True)[0] == "invalid_state"


def test_set_fold_on_group(bridge, song):
    group = factory.add_group_track(song, "Bus", members=[song.tracks[0]])
    ok(bridge, "tracks.set", track="Bus", fold=True)
    assert group.fold_state == 1
    result = ok(bridge, "tracks.set", track="Bus", fold="toggle")
    assert result["tracks"][0]["folded"] is False


# --------------------------------------------------------------------------
# group / stop_clips / select
# --------------------------------------------------------------------------

def test_group_members_nested_and_fold(bridge, song):
    outer = factory.add_group_track(song, "Outer", members=[song.tracks[0]])
    inner = factory.add_group_track(song, "Inner", members=[song.tracks[1]])
    inner._group_track = outer
    result = ok(bridge, "tracks.group", track="Outer", fold=True)
    members = dict((m["name"], m["depth"]) for m in result["members"])
    assert members == {"Bass": 1, "Vocals": 2, "Inner": 1}
    assert result["folded"] is True and outer.fold_state == 1
    via_member = ok(bridge, "tracks.group", track="Vocals")
    assert via_member["group"]["name"] == "Inner"
    assert via_member["grouped_in"] == ctx_path(bridge, outer)
    assert err(bridge, "tracks.group", track="Drums")[0] == "invalid_state"


def ctx_path(bridge, obj):
    return bridge.ctx.path_of(obj)


def test_stop_clips(bridge, song):
    song.tracks[0].clip_slots[0].fire()
    song.tracks[2].clip_slots[0].fire()
    assert song.tracks[0].playing_slot_index == 0
    result = ok(bridge, "tracks.stop_clips", track="Bass", quantized=False)
    assert result == {"stopped": ["Bass"], "quantized": False}
    assert song.tracks[0].playing_slot_index == -1
    assert song.tracks[2].playing_slot_index == 0
    assert ok(bridge, "tracks.stop_clips")["stopped"] == "all"
    assert song.tracks[2].playing_slot_index == -1
    assert ok(bridge, "tracks.stop_clips", track=["Bass", "Drums"])["stopped"] == \
        ["Bass", "Drums"]


def test_select(bridge, song):
    assert ok(bridge, "tracks.select", track="Drums")["selected"]["name"] == "Drums"
    assert song.view.selected_track is song.tracks[2]
    ok(bridge, "tracks.select", track="master")
    assert song.view.selected_track is song.master_track
    assert err(bridge, "tracks.select", track="nope")[0] == "not_found"


def test_commands_are_registered_with_docs(bridge):
    listing = ok(bridge, "system.commands", namespace="tracks")
    names = {c["cmd"] for c in listing["commands"]}
    assert names == {"tracks.list", "tracks.get", "tracks.find", "tracks.create",
                     "tracks.delete", "tracks.duplicate", "tracks.set", "tracks.group",
                     "tracks.stop_clips", "tracks.select"}
    assert all(c["doc"] for c in listing["commands"])


# --------------------------------------------------------------------------
# MCP tools
# --------------------------------------------------------------------------

TRACK_TOOLS = {"live_tracks_list", "live_tracks_get", "live_tracks_find", "live_tracks_create",
               "live_tracks_delete", "live_tracks_duplicate", "live_tracks_set",
               "live_tracks_group", "live_tracks_stop_clips", "live_tracks_select"}


@pytest.fixture()
def fake():
    server = FakeBridge().start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture()
def fake_app(fake):
    client = BridgeClient(host=fake.host, port=fake.port, timeout=5.0)
    try:
        yield create_app(client), fake
    finally:
        client.close()


def _last(fake, cmd):
    return [r for r in fake.requests if r["cmd"] == cmd][-1]["args"]


def test_track_tools_are_registered_with_docstrings(fake_app):
    app, _fake = fake_app
    tools = {t.name: t for t in asyncio.run(app.list_tools())}
    assert TRACK_TOOLS <= set(tools)
    for name in TRACK_TOOLS:
        assert "Returns" in (tools[name].description or ""), name


def test_track_tools_forward_arguments(fake_app):
    app, fake = fake_app
    for cmd in ("tracks.list", "tracks.get", "tracks.find", "tracks.create", "tracks.delete",
                "tracks.duplicate", "tracks.set", "tracks.group", "tracks.stop_clips",
                "tracks.select"):
        fake.set_result(cmd, {"ok": cmd})
    assert call_tool(app, "live_tracks_list", {"type": "midi", "limit": 2}) == \
        {"ok": "tracks.list"}
    assert _last(fake, "tracks.list") == {"type": "midi", "include_returns": True,
                                          "include_master": True, "detail": "summary",
                                          "offset": 0, "limit": 2}
    call_tool(app, "live_tracks_get", {"track": 2})
    assert _last(fake, "tracks.get") == {"track": 2, "detail": "full"}
    call_tool(app, "live_tracks_find", {"name": "bass"})
    assert _last(fake, "tracks.find") == {"name": "bass", "limit": 20}
    call_tool(app, "live_tracks_create", {"type": "Audio", "name": "Vox", "color": "red"})
    assert _last(fake, "tracks.create") == {"type": "audio", "name": "Vox", "color": "red",
                                            "index": -1}
    call_tool(app, "live_tracks_create", {"after_selected": True})
    assert _last(fake, "tracks.create") == {"type": "midi", "index": None}
    call_tool(app, "live_tracks_delete", {"track": "Bass"})
    assert _last(fake, "tracks.delete") == {"track": "Bass"}
    call_tool(app, "live_tracks_duplicate", {"track": 0, "name": "B2"})
    assert _last(fake, "tracks.duplicate") == {"track": 0, "name": "B2"}
    call_tool(app, "live_tracks_set", {"track": ["Bass", 1], "solo": True, "exclusive": True})
    assert _last(fake, "tracks.set") == {"track": ["Bass", 1], "solo": True, "exclusive": True}
    call_tool(app, "live_tracks_group", {"track": "Bus", "fold": "toggle"})
    assert _last(fake, "tracks.group") == {"track": "Bus", "fold": "toggle"}
    call_tool(app, "live_tracks_stop_clips", {})
    assert _last(fake, "tracks.stop_clips") == {"quantized": True}
    call_tool(app, "live_tracks_select", {"track": "master"})
    assert _last(fake, "tracks.select") == {"track": "master"}


@pytest.mark.parametrize("name,args", [
    ("live_tracks_list", {"detail": "everything"}),
    ("live_tracks_list", {"type": "bus"}),
    ("live_tracks_list", {"offset": -1}),
    ("live_tracks_get", {"track": ""}),
    ("live_tracks_find", {"name": " "}),
    ("live_tracks_create", {"type": "group"}),
    ("live_tracks_create", {"index": -5}),
    ("live_tracks_set", {"track": "Bass"}),
    ("live_tracks_set", {"track": [], "mute": True}),
    ("live_tracks_set", {"track": ["a", "b"], "name": "x"}),
    ("live_tracks_set", {"track": "Bass", "mute": "perhaps"}),
    ("live_tracks_delete", {"track": "  "}),
])
def test_track_tools_validate_locally(fake_app, name, args):
    app, fake = fake_app
    result = call_tool(app, name, args)
    assert result["type"] == "bad_args", result
    assert not [r for r in fake.requests if r["cmd"].startswith("tracks.")]


def test_track_tool_reports_bridge_errors(fake_app):
    app, fake = fake_app
    fake.set_error("tracks.get", "not_found", "no track named 'X'")
    result = call_tool(app, "live_tracks_get", {"track": "X"})
    assert result["type"] == "not_found" and "X" in result["error"]


def test_track_tools_end_to_end(tcp_bridge, song):
    client = BridgeClient(host="127.0.0.1", port=tcp_bridge.port, timeout=5.0)
    try:
        app = create_app(client)
        listed = call_tool(app, "live_tracks_list", {"detail": "minimal"})
        assert listed["total"] == 6
        created = call_tool(app, "live_tracks_create", {"type": "audio", "name": "Guitar",
                                                        "color": "green"})
        assert created["name"] == "Guitar"
        changed = call_tool(app, "live_tracks_set", {"track": "Guitar", "mute": True})
        assert changed["tracks"][0]["mute"] is True
        assert call_tool(app, "live_tracks_find", {"name": "guit"})["count"] == 1
        assert call_tool(app, "live_tracks_get", {"track": "Guitar",
                                                  "detail": "minimal"})["type"] == "audio"
        dup = call_tool(app, "live_tracks_duplicate", {"track": "Guitar"})
        assert dup["path"] == "song.tracks[4]"
        assert call_tool(app, "live_tracks_select", {"track": "Drums"})["selected"]["index"] == 2
        assert call_tool(app, "live_tracks_stop_clips", {"track": "Drums"})["stopped"] == \
            ["Drums"]
        assert call_tool(app, "live_tracks_delete", {"track": 4})["deleted"] == "Guitar"
        missing = call_tool(app, "live_tracks_group", {"track": "Bass"})
        assert missing["type"] == "invalid_state"
    finally:
        client.close()
