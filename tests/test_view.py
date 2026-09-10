"""Module A — views and selection: bridge commands on the stub song and the
MCP tools (fake bridge for argument forwarding/validation, TCP end to end)."""

import asyncio
import json
import sys
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parent
for _p in (str(_TESTS.parent / "mcp_server"), str(_TESTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fake_bridge import FakeBridge  # noqa: E402
from livebridge_mcp.client import BridgeClient  # noqa: E402
from livebridge_mcp.server import create_app  # noqa: E402
import Live  # noqa: E402
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
    response = bridge.dispatch({"id": "v", "cmd": cmd, "args": args})
    assert response["ok"], response
    return response["result"]


def fail(bridge, cmd, **args):
    response = bridge.dispatch({"id": "v", "cmd": cmd, "args": args})
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

def test_selection(bridge, song):
    data = run(bridge, "view.selection")
    assert data["track"] == {"path": "song.tracks[0]", "index": 0, "name": "Bass",
                             "type": "midi"}
    assert data["scene"]["name"] == "Intro"
    assert data["clip_slot"] == {"path": "song.tracks[0].clip_slots[0]", "has_clip": True,
                                 "clip_name": "Bass Loop"}
    assert data["focused_view"] == "Session"
    assert "device" not in data and "detail_clip" not in data  # nothing selected
    song.view.select_device(song.tracks[0].devices[1])
    song.view.detail_clip = song.tracks[0].clip_slots[1].clip
    data = run(bridge, "view.selection")
    assert data["device"]["name"] == "Bass Rack"
    assert data["device"]["path"] == "song.tracks[0].devices[1]"
    assert data["detail_clip"]["path"] == "song.tracks[0].clip_slots[1].clip"


def test_state(bridge, app):
    data = run(bridge, "view.state")
    assert data["focused_view"] == "Session"
    assert data["visible"]["Session"] is True and data["visible"]["Browser"] is False
    assert data["follow_song"] is False and data["draw_mode"] is False
    assert "Detail/DeviceChain" in data["views"] and "selection" not in data
    assert run(bridge, "view.state", include_selection=True)["selection"]["track"]["name"] \
        == "Bass"


def test_is_visible_and_aliases(bridge):
    assert run(bridge, "view.is_visible", view="session") == {"view": "Session",
                                                              "visible": True}
    assert run(bridge, "view.is_visible", view="clip")["view"] == "Detail/Clip"
    assert run(bridge, "view.is_visible", view="device chain")["view"] == "Detail/DeviceChain"
    assert run(bridge, "view.is_visible", view="Arrangement",
               main_window_only=False)["visible"] is False
    error = fail(bridge, "view.is_visible", view="Mixer")
    assert error["type"] == "bad_args" and "Session" in error["message"]


# --------------------------------------------------------------------------
# show / hide / focus / toggle
# --------------------------------------------------------------------------

def test_show_hide_focus_toggle(bridge, app):
    result = run(bridge, "view.show", view="arranger")
    assert result == {"view": "Arranger", "action": "show", "focused_view": "Arranger",
                      "visible": True}
    assert app.view.is_view_visible("Session") is False
    assert run(bridge, "view.focus", view="Session")["focused_view"] == "Session"
    assert run(bridge, "view.hide", view="Detail")["visible"] is False
    assert run(bridge, "view.toggle", view="Detail")["action"] == "show"
    assert run(bridge, "view.toggle", view="Detail")["action"] == "hide"
    assert run(bridge, "view.show", view="")["view"] == "Session"  # the main view
    assert fail(bridge, "view.toggle", view="")["type"] == "bad_args"
    assert fail(bridge, "view.show", view=3)["type"] == "bad_args"


def test_toggle_browser(bridge, app):
    assert run(bridge, "view.toggle_browser")["browser_visible"] is True
    assert run(bridge, "view.toggle_browser")["browser_visible"] is False
    assert run(bridge, "view.toggle_browser", visible=True)["browser_visible"] is True
    assert run(bridge, "view.toggle_browser", visible=True)["browser_visible"] is True
    result = run(bridge, "view.toggle_browser", hotswap=True)
    assert result["browse_mode"] is not None
    assert app.view.browse_mode is True


def test_show_view_refused_by_live_is_invalid_state(bridge, app, monkeypatch):
    def refuse(name):
        raise RuntimeError("show_view called in Live's initialization scope")
    monkeypatch.setattr(app.view, "show_view", refuse)
    assert fail(bridge, "view.show", view="Browser")["type"] == "invalid_state"


# --------------------------------------------------------------------------
# selection changes
# --------------------------------------------------------------------------

def test_select_track_scene_slot(bridge, song):
    data = run(bridge, "view.select", track="Drums", scene="Chorus")
    assert data["track"]["name"] == "Drums" and data["scene"]["name"] == "Chorus"
    assert song.view.selected_track == song.tracks[2]
    data = run(bridge, "view.select", track=1, slot=0)
    assert data["clip_slot"]["path"] == "song.tracks[1].clip_slots[0]"
    assert song.view.detail_clip == song.tracks[1].clip_slots[0].clip
    run(bridge, "view.select", slot="Verse")  # slot by scene name on the selected track
    assert song.view.highlighted_clip_slot == song.tracks[1].clip_slots[1]
    assert run(bridge, "view.select", track="master")["track"]["type"] == "master"
    assert run(bridge, "view.select", track="song.return_tracks[1]")["track"]["name"] \
        == "B-Delay"
    assert fail(bridge, "view.select")["type"] == "bad_args"
    assert fail(bridge, "view.select", track="Nope")["type"] == "not_found"
    assert fail(bridge, "view.select", track="master", slot=0)["type"] == "invalid_state"


def test_select_is_all_or_nothing(bridge, song):
    # Real Live run: view.select(track=<return>, slot=0) selected the return track before
    # failing on the slot.  Everything is resolved first now.
    before = song.view.selected_track
    error = fail(bridge, "view.select", track="song.return_tracks[1]", slot=0)
    assert error["type"] == "invalid_state"
    assert song.view.selected_track == before
    assert fail(bridge, "view.select", track="Drums", device="Nope")["type"] == "not_found"
    assert song.view.selected_track == before
    assert fail(bridge, "view.select", scene="Chorus", clip="tracks[0]")["type"] == "not_found"
    assert fail(bridge, "view.select", scene="Chorus", clip="song.tracks[0]")["type"] == \
        "bad_args"                                     # a track, not a clip
    assert song.view.selected_scene == song.scenes[0]


def test_select_device_and_show(bridge, song, app):
    data = run(bridge, "view.select", track=0, device="Operator", show=True)
    assert data["device"]["name"] == "Operator"
    assert song.tracks[0].view.selected_device == song.tracks[0].devices[0]
    assert app.view.is_view_visible("Detail/DeviceChain") is True
    nested = "song.tracks[0].devices[1].chains[0]"
    data = run(bridge, "view.select", chain=nested)
    assert data["chain"]["path"] == nested
    run(bridge, "view.select", track=2, device=0)
    assert song.tracks[2].view.selected_device == song.tracks[2].devices[0]
    assert fail(bridge, "view.select", track=0, device="Serum")["type"] == "not_found"
    assert fail(bridge, "view.select", chain="song.tracks[0]")["type"] == "not_found"


def test_select_arrangement_clip_and_set_detail_clip(bridge, song, app):
    path = "song.tracks[0].arrangement_clips[0]"
    data = run(bridge, "view.select", clip=path, show=True)
    assert data["detail_clip"]["path"] == path
    assert app.view.is_view_visible("Detail/Clip") is True
    result = run(bridge, "view.set_detail_clip", track="Bass", slot=1, show=False)
    assert result == {"detail_clip": {"path": "song.tracks[0].clip_slots[1].clip",
                                      "name": "Bass Fill", "is_midi": True, "length": 8.0},
                      "shown": False}
    assert run(bridge, "view.set_detail_clip", clip=path)["shown"] is True
    assert fail(bridge, "view.set_detail_clip")["type"] == "bad_args"
    assert fail(bridge, "view.set_detail_clip", track=0, slot=5)["type"] == "not_found"
    assert fail(bridge, "view.set_detail_clip", clip="song.tracks[0]")["type"] == "bad_args"
    assert fail(bridge, "view.set_detail_clip", clip="No Such Clip")["type"] == "not_found"


def test_select_accepts_every_clip_form(bridge, song):
    # Review finding: view.select only took LOM paths although every clip command (and the
    # server instructions) accept a path, a clip name or "selected".
    by_name = run(bridge, "view.select", clip="Bass Fill")
    assert by_name["detail_clip"]["path"] == "song.tracks[0].clip_slots[1].clip"
    assert song.view.detail_clip == song.tracks[0].clip_slots[1].clip
    run(bridge, "view.set_detail_clip", clip="bass loop", show=False)   # case-insensitive
    assert song.view.detail_clip == song.tracks[0].clip_slots[0].clip
    assert run(bridge, "view.select", clip="selected")["detail_clip"]["name"] == "Bass Loop"
    slot_path = run(bridge, "view.select", clip="song.tracks[0].clip_slots[1]")
    assert slot_path["detail_clip"]["name"] == "Bass Fill"


# --------------------------------------------------------------------------
# zoom / scroll / flags
# --------------------------------------------------------------------------

def test_zoom_and_scroll(bridge, app):
    result = run(bridge, "view.zoom", direction="left", view="Arranger", modifier=True)
    assert result == {"view": "Arranger", "direction": "left", "steps": 1, "modifier": True}
    assert app.view._last_zoom == (2, "Arranger", True)
    result = run(bridge, "view.scroll", direction=1, view="browser", steps=3)
    assert result["direction"] == "down" and result["steps"] == 3
    assert app.view._last_scroll == (1, "Browser", False)
    assert run(bridge, "view.scroll", direction="up")["view"] == "Session"
    assert fail(bridge, "view.zoom", direction="in")["type"] == "bad_args"
    assert fail(bridge, "view.scroll", direction="up", steps=0)["type"] == "bad_args"


def test_view_set_flags(bridge, song):
    assert run(bridge, "view.set", follow_song=True, draw_mode=True) == {
        "follow_song": True, "draw_mode": True}
    assert song.view.follow_song is True and song.view.draw_mode is True
    assert run(bridge, "view.set", draw_mode=False)["draw_mode"] is False
    assert fail(bridge, "view.set")["type"] == "bad_args"


def test_view_commands_do_not_create_undo_steps(bridge, song):
    before = len(song._undo_steps)
    run(bridge, "view.select", track=1)
    run(bridge, "view.show", view="Browser")
    run(bridge, "view.set", follow_song=True)
    assert len(song._undo_steps) == before


def test_clip_editor_opens_an_envelope_lane(bridge, song, app):
    clip = song.tracks[0].clip_slots[0].clip
    result = run(bridge, "view.clip_editor", track="Bass", slot=0, envelope="Filter Freq",
                 grid="1/8", triplet=True, show_loop=True)
    assert result["shown"] is True and result["detail_clip"]["name"] == "Bass Loop"
    assert result["envelope"]["name"] == "Filter Freq"
    assert result["envelope"]["path"].startswith("song.tracks[0].devices[0].parameters[")
    assert result["grid"] == {"quantization": "1/8", "triplet": True}
    assert result["loop_shown"] is True
    view = clip.view
    assert view._envelope_visible is True and view._showed_loop is True
    assert view._selected_envelope_parameter.name == "Filter Freq"
    assert song.view.detail_clip == clip and app.view.is_view_visible("Detail/Clip")
    # default: the clip in the Detail view; mixer aliases work; hide the lane again
    volume = run(bridge, "view.clip_editor", envelope="volume", show=False)
    assert volume["envelope"]["name"] == "Track Volume" and volume["shown"] is False
    hidden = run(bridge, "view.clip_editor", hide_envelope=True, grid=0)
    assert hidden["envelope"] == "hidden" and view._envelope_visible is False
    assert hidden["grid"]["quantization"] == "none"
    by_name = run(bridge, "view.clip_editor", clip="Bass Fill", grid="g_sixteenth")
    assert by_name["grid"]["quantization"] == "1/16"


def test_clip_editor_errors(bridge, song):
    song.view.detail_clip = None
    assert fail(bridge, "view.clip_editor")["type"] == "not_found"
    assert fail(bridge, "view.clip_editor", track="Bass")["type"] == "bad_args"
    assert fail(bridge, "view.clip_editor", clip="Bass Loop", grid="1/64")["type"] == "bad_args"
    assert fail(bridge, "view.clip_editor", clip="Bass Loop", grid=12)["type"] == "bad_args"
    assert fail(bridge, "view.clip_editor", clip="Bass Loop", envelope="Nope")["type"] in (
        "not_found", "bad_args")
    assert fail(bridge, "view.clip_editor", clip="Bass Loop", envelope="volume",
                hide_envelope=True)["type"] == "bad_args"
    assert song.view.detail_clip is None                  # nothing was shown on an error


def test_every_view_command_is_registered(bridge):
    listing = run(bridge, "system.commands", namespace="view")
    assert {c["cmd"] for c in listing["commands"]} == {
        "view.selection", "view.state", "view.is_visible", "view.show", "view.hide",
        "view.focus", "view.toggle", "view.toggle_browser", "view.select",
        "view.set_detail_clip", "view.zoom", "view.scroll", "view.set", "view.clip_editor"}


# --------------------------------------------------------------------------
# MCP tools
# --------------------------------------------------------------------------

VIEW_TOOLS = {"live_view_selection", "live_view_state", "live_view_show", "live_view_select",
              "live_view_navigate", "live_view_set", "live_view_clip_editor"}


@pytest.fixture()
def fake_app():
    fake = FakeBridge().start()
    client = BridgeClient(host=fake.host, port=fake.port, timeout=5.0)
    try:
        yield fake, create_app(client)
    finally:
        client.close()
        fake.stop()


def test_view_tools_registered(fake_app):
    _fake, app = fake_app
    assert "view" in app.tool_modules
    assert VIEW_TOOLS <= {t.name for t in asyncio.run(app.list_tools())}


def test_view_tools_forward_arguments(fake_app):
    fake, app = fake_app
    for cmd in ("view.selection", "view.state", "view.show", "view.hide", "view.focus",
                "view.toggle", "view.toggle_browser", "view.select", "view.zoom",
                "view.scroll", "view.set", "view.clip_editor"):
        fake.set_result(cmd, {"ok": cmd})

    def last():
        return fake.requests[-1]["cmd"], fake.requests[-1].get("args", {})

    call_tool(app, "live_view_selection")
    assert last() == ("view.selection", {})
    call_tool(app, "live_view_state", {"include_selection": True})
    assert last() == ("view.state", {"include_selection": True})
    call_tool(app, "live_view_show", {"view": "Arranger"})
    assert last() == ("view.show", {"view": "Arranger"})
    for action in ("hide", "focus", "toggle"):
        call_tool(app, "live_view_show", {"view": "Detail/Clip", "action": action})
        assert last() == ("view." + action, {"view": "Detail/Clip"})
    call_tool(app, "live_view_show", {"view": "Browser", "hotswap": True})
    assert last() == ("view.toggle_browser", {"hotswap": True})
    call_tool(app, "live_view_select", {"track": "Bass", "slot": 2, "show": True})
    assert last() == ("view.select", {"track": "Bass", "slot": 2, "show": True})
    call_tool(app, "live_view_select", {"track": 1, "device": "EQ"})
    assert last() == ("view.select", {"track": 1, "device": "EQ"})
    call_tool(app, "live_view_navigate", {"direction": "left", "action": "zoom",
                                          "view": "Arranger", "steps": 4})
    assert last() == ("view.zoom", {"direction": "left", "view": "Arranger", "steps": 4,
                                    "modifier": False})
    call_tool(app, "live_view_navigate", {"direction": "down"})
    assert last()[0] == "view.scroll"
    call_tool(app, "live_view_set", {"follow_song": False})
    assert last() == ("view.set", {"follow_song": False})
    call_tool(app, "live_view_select", {"clip": "Bass Loop"})
    assert last() == ("view.select", {"clip": "Bass Loop"})
    call_tool(app, "live_view_clip_editor", {"track": "Bass", "slot": 0, "envelope": "Volume",
                                             "grid": "1/8", "triplet": True})
    assert last() == ("view.clip_editor", {"track": "Bass", "slot": 0, "envelope": "Volume",
                                           "grid": "1/8", "triplet": True})
    call_tool(app, "live_view_clip_editor", {"hide_envelope": True, "show": False,
                                             "show_loop": True})
    assert last() == ("view.clip_editor", {"hide_envelope": True, "show": False,
                                           "show_loop": True})


def test_view_tools_validate_locally(fake_app):
    fake, app = fake_app
    count = len(fake.requests)
    for name, args in [
        ("live_view_show", {"view": "Session", "action": "maximize"}),
        ("live_view_show", {"view": "Session", "hotswap": True}),
        ("live_view_show", {"view": "", "action": "toggle"}),
        ("live_view_select", {}),
        ("live_view_select", {"clip": "  "}),
        ("live_view_clip_editor", {"envelope": "Volume", "hide_envelope": True}),
        ("live_view_clip_editor", {"track": "Bass"}),
        ("live_view_navigate", {"direction": "forward"}),
        ("live_view_navigate", {"direction": "up", "action": "pan"}),
        ("live_view_navigate", {"direction": "up", "steps": 99}),
        ("live_view_set", {}),
    ]:
        result = call_tool(app, name, args)
        assert isinstance(result, dict) and result.get("type") == "bad_args", (name, result)
    assert len(fake.requests) == count


def test_view_tools_end_to_end(tcp_bridge, song):
    client = BridgeClient(host="127.0.0.1", port=tcp_bridge.port, timeout=5.0)
    try:
        app = create_app(client)
        assert call_tool(app, "live_view_selection")["track"]["name"] == "Bass"
        selected = call_tool(app, "live_view_select", {"track": "Vocals", "scene": 2})
        assert selected["track"]["name"] == "Vocals" and selected["scene"]["index"] == 2
        shown = call_tool(app, "live_view_show", {"view": "arrangement"})
        assert shown["focused_view"] == "Arranger"
        state = call_tool(app, "live_view_state")
        assert state["visible"]["Arranger"] is True
        assert call_tool(app, "live_view_set", {"draw_mode": True})["draw_mode"] is True
        error = call_tool(app, "live_view_show", {"view": "Mixer"})
        assert error["type"] == "bad_args"
    finally:
        client.close()
