"""Module A — transport + song.summary/song.snapshot: bridge commands on the stub
song, and the MCP tools against the fake bridge and end-to-end over TCP."""

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

from LiveBridge.handlers import transport as transport_module  # noqa: E402
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



# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def run(bridge, cmd, **args):
    response = bridge.dispatch({"id": "t", "cmd": cmd, "args": args})
    assert response["ok"], response
    return response["result"]


def fail(bridge, cmd, **args):
    response = bridge.dispatch({"id": "t", "cmd": cmd, "args": args})
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


def tool_names(app):
    tools = asyncio.run(app.list_tools())
    return {t.name for t in tools}


# --------------------------------------------------------------------------
# time helpers
# --------------------------------------------------------------------------

def test_bbs_round_trip(song):
    to_bbs = transport_module.beats_to_bbs
    parse = transport_module.parse_time
    assert to_bbs(song, 0.0) == "1.1.1"
    assert to_bbs(song, 8.0) == "3.1.1"
    assert to_bbs(song, 9.75) == "3.2.4"
    assert to_bbs(song, 16.0, is_length=True) == "4.0.0"
    assert parse(song, "3.1.1", "p") == 8.0
    assert parse(song, "3", "p") == 8.0
    assert parse(song, "3.2.4", "p") == 9.75
    assert parse(song, 5, "p") == 5.0
    assert parse(song, "4.0.0", "l", is_length=True) == 16.0
    assert parse(song, "2.2", "l", is_length=True) == 10.0
    song.signature_numerator, song.signature_denominator = 6, 8
    assert to_bbs(song, 3.0) == "2.1.1"          # a 6/8 bar is 3 quarter notes
    assert to_bbs(song, 0.5) == "1.2.1"          # beats are eighth notes
    assert to_bbs(song, 0.25) == "1.1.2"
    assert parse(song, "2.1.1", "p") == 3.0
    with pytest.raises(Exception):
        parse(song, "1.7.1", "p")                # only 6 beats in 6/8
    with pytest.raises(Exception):
        parse(song, "0.1.1", "p")                # bars are 1-based
    with pytest.raises(Exception):
        parse(song, "abc", "p")
    with pytest.raises(Exception):
        parse(song, -1, "p")


# --------------------------------------------------------------------------
# song.summary / song.snapshot
# --------------------------------------------------------------------------

def test_song_summary(bridge):
    data = run(bridge, "song.summary")
    assert data["tempo"] == 124.0 and data["signature"] == "4/4"
    assert data["position_bbs"] == "1.1.1"
    minimal = run(bridge, "song.summary", detail="minimal")
    assert "loop" not in minimal and minimal["track_count"] == 3
    assert fail(bridge, "song.summary", detail="huge")["type"] == "bad_args"


def test_snapshot_summary_shape(bridge, song):
    snap = run(bridge, "song.snapshot")
    assert snap["song"]["tempo"] == 124.0
    assert snap["song"]["position"] == {"beats": 0.0, "bbs": "1.1.1"}
    assert snap["song"]["loop"]["length_bbs"] == "4.0.0"
    assert [t["name"] for t in snap["tracks"]] == ["Bass", "Vocals", "Drums"]
    bass = snap["tracks"][0]
    assert bass["path"] == "song.tracks[0]" and bass["type"] == "midi"
    assert [d["name"] for d in bass["devices"]] == ["Operator", "Bass Rack"]
    assert "path" not in bass["devices"][0]
    assert [c["slot"] for c in bass["clips"]] == [0, 1]
    assert bass["clips"][0]["name"] == "Bass Loop"
    assert bass["arrangement_clips"] == 1
    # compact: false flags and defaults are gone
    assert "mute" not in bass and "kind" not in bass and "clip_slot_count" not in bass
    assert [r["name"] for r in snap["returns"]] == ["A-Reverb", "B-Delay"]
    assert snap["master"]["type"] == "master"
    assert [s["name"] for s in snap["scenes"]] == ["Intro", "Verse", "Chorus", "Drop"]
    assert [c["name"] for c in snap["cue_points"]] == ["Intro", "Drop"]
    assert snap["selection"]["track"]["name"] == "Bass"
    assert snap["selection"]["clip_slot"]["clip_name"] == "Bass Loop"
    assert "paging" not in snap
    json.dumps(snap)  # JSON-serialisable


def test_snapshot_flags_and_paging(bridge, song):
    song.tracks[1].mute = True
    minimal = run(bridge, "song.snapshot", detail="minimal", include_clips=False,
                  include_devices=False, include_returns=False, include_scenes=False)
    assert minimal["tracks"][0] == {"path": "song.tracks[0]", "index": 0, "name": "Bass",
                                    "type": "midi"}
    assert "returns" not in minimal and "scenes" not in minimal
    summary = run(bridge, "song.snapshot", include_clips=False)
    assert summary["tracks"][1]["mute"] is True
    assert "clips" not in summary["tracks"][0]
    page = run(bridge, "song.snapshot", offset=1, limit=1)
    assert [t["name"] for t in page["tracks"]] == ["Vocals"]
    assert page["paging"] == {"total": 3, "offset": 1, "count": 1, "next_offset": 2}
    full = run(bridge, "song.snapshot", detail="full")
    operator = full["tracks"][0]["devices"][0]
    assert operator["name"] == "Operator" and operator["parameter_count"] == 7
    assert full["tracks"][0]["clips"][0]["loop_end"] == 4.0
    assert full["tracks"][0]["arrangement_clips"][0]["name"] == "Bass Arr"
    assert full["tracks"][1]["clips"][0]["file_path"] == "/Samples/vox.wav"
    assert fail(bridge, "song.snapshot", limit=0)["type"] == "bad_args"


def test_snapshot_marks_playing_clips_and_scene_tempo(bridge, song):
    song.tracks[0].clip_slots[0].fire()
    song.scenes[2].tempo = 140.0
    song.scenes[2].tempo_enabled = True
    snap = run(bridge, "song.snapshot")
    assert snap["tracks"][0]["clips"][0]["playing"] is True
    assert snap["tracks"][0]["playing_slot_index"] == 0
    assert snap["scenes"][2]["tempo"] == 140.0
    assert "tempo" not in snap["scenes"][0]


# --------------------------------------------------------------------------
# transport commands
# --------------------------------------------------------------------------

def test_transport_get(bridge, song):
    song.loop_start, song.loop_length = 8.0, 16.0
    state = run(bridge, "transport.get")
    assert state["is_playing"] is False and state["tempo"] == 124.0
    assert state["loop"]["start_bbs"] == "3.1.1" and state["loop"]["end_bbs"] == "7.1.1"
    assert state["clip_trigger_quantization"] == "1 bar"
    assert state["scale"]["name"] == "Major" and state["scale"]["root"] == "C"
    assert state["metronome"] is False and "scales" not in state
    with_scales = run(bridge, "transport.get", include_scales=True)
    assert "Dorian" in with_scales["scales"]


def test_play_stop_continue_toggle(bridge, song):
    result = run(bridge, "transport.play")
    assert result["is_playing"] is True
    assert run(bridge, "transport.stop")["is_playing"] is False
    song.current_song_time = 12.0
    result = run(bridge, "transport.continue")
    assert result["is_playing"] and result["position"]["beats"] == 12.0
    assert run(bridge, "transport.toggle")["is_playing"] is False
    assert run(bridge, "transport.toggle")["is_playing"] is True
    run(bridge, "transport.stop")
    result = run(bridge, "transport.play", position="5.1.1")
    assert result["position"] == {"beats": 16.0, "bbs": "5.1.1"}
    assert result["start_time"]["beats"] == 16.0
    run(bridge, "transport.stop")
    assert run(bridge, "transport.play", mode="continue")["position"]["beats"] == 16.0
    assert run(bridge, "transport.play", mode="selection")["is_playing"] is True
    assert fail(bridge, "transport.play", mode="backwards")["type"] == "bad_args"


def test_stop_with_clips(bridge, song):
    song.tracks[0].clip_slots[0].fire()
    run(bridge, "transport.play")
    run(bridge, "transport.stop", stop_clips=True)
    assert song.tracks[0].playing_slot_index == -1
    assert song.is_playing is False


def test_set_position(bridge, song):
    result = run(bridge, "transport.set_position", position=32)
    assert result["position"] == {"beats": 32.0, "bbs": "9.1.1"}
    assert song.start_time == 32.0  # stopped: the start marker follows
    result = run(bridge, "transport.set_position", jump_by=-4)
    assert result["position"]["beats"] == 28.0
    song.start_playing()
    run(bridge, "transport.set_position", position="2.1.1")
    assert song.current_song_time == 4.0 and song.start_time != 4.0
    run(bridge, "transport.set_position", position="2.1.1", set_start_marker=True)
    assert song.start_time == 4.0
    assert fail(bridge, "transport.set_position")["type"] == "bad_args"
    assert fail(bridge, "transport.set_position", position=1, jump_by=1)["type"] == "bad_args"
    assert fail(bridge, "transport.set_position", position="1.9.1")["type"] == "bad_args"


def test_tempo_signature_tap(bridge, song):
    assert run(bridge, "transport.set_tempo", bpm=98.5) == {"tempo": 98.5}
    assert fail(bridge, "transport.set_tempo", bpm=5)["type"] == "bad_args"
    assert run(bridge, "transport.tap_tempo")["tempo"] == 99.5  # stub: +1 per tap
    assert run(bridge, "transport.set_time_signature", signature="6/8") == {"signature": "6/8"}
    assert run(bridge, "transport.set_time_signature", numerator=7) == {"signature": "7/8"}
    assert fail(bridge, "transport.set_time_signature", denominator=3)["type"] == "bad_args"
    assert fail(bridge, "transport.set_time_signature")["type"] == "bad_args"


def test_mutating_transport_commands_are_undo_steps(bridge, song):
    before = len(song._undo_steps)
    run(bridge, "transport.set_tempo", bpm=100)
    assert len(song._undo_steps) == before + 1
    run(bridge, "transport.play")
    run(bridge, "transport.stop")
    assert len(song._undo_steps) == before + 1  # transport actions are not undo steps


def test_set_loop(bridge, song):
    loop = run(bridge, "transport.set_loop", start_bar=9, bars=8, enabled=True)
    assert loop == {"on": True, "start": 32.0, "length": 32.0, "end": 64.0,
                    "start_bbs": "9.1.1", "end_bbs": "17.1.1", "length_bbs": "8.0.0"}
    loop = run(bridge, "transport.set_loop", start="3.1.1", end="5.1.1")
    assert (loop["start"], loop["length"]) == (8.0, 8.0)
    loop = run(bridge, "transport.set_loop", length="2.2.0")
    assert loop["length"] == 10.0 and loop["start"] == 8.0
    loop = run(bridge, "transport.set_loop", start=4, length=4)
    assert (song.loop_start, song.loop_length) == (4.0, 4.0)
    assert run(bridge, "transport.set_loop", enabled=False)["on"] is False
    assert fail(bridge, "transport.set_loop")["type"] == "bad_args"
    assert fail(bridge, "transport.set_loop", start=8, end=4)["type"] == "bad_args"
    assert fail(bridge, "transport.set_loop", length=4, bars=2)["type"] == "bad_args"
    assert fail(bridge, "transport.set_loop", start=1, start_bar=1)["type"] == "bad_args"


def test_transport_set_many(bridge, song):
    result = run(bridge, "transport.set", tempo=90, signature="3/4", metronome=True,
                 punch_in=True, punch_out=True, arrangement_overdub=True,
                 session_automation_record=True, loop=True,
                 clip_trigger_quantization="1/16", midi_recording_quantization="1/8T",
                 groove_amount=0.5, swing_amount=0.25, scale_name="dorian", root_note="F#",
                 scale_mode=True, record_mode=True, session_record=True)
    changed = result["changed"]
    assert changed["tempo"] == 90.0 and changed["signature_numerator"] == 3
    assert changed["clip_trigger_quantization"] == "1/16"
    assert changed["midi_recording_quantization"] == "1/8T"
    assert changed["scale_name"] == "Dorian" and changed["root_note"] == 6
    assert changed["root"] == "F#"
    assert song.metronome and song.punch_in and song.punch_out and song.loop
    assert song.clip_trigger_quantization == 11 and song.midi_recording_quantization == 3
    assert (song.signature_numerator, song.signature_denominator) == (3, 4)
    assert song.scale_mode is True and song.record_mode and song.session_record
    # Live enum member names and ints work too
    run(bridge, "transport.set", clip_trigger_quantization="q_2_bars")
    assert song.clip_trigger_quantization == 3
    run(bridge, "transport.set", clip_trigger_quantization=0, root_note=11)
    assert song.clip_trigger_quantization == 0 and song.root_note == 11
    run(bridge, "transport.set", back_to_arranger=False, overdub=True)


def test_transport_set_validates_before_writing(bridge, song):
    error = fail(bridge, "transport.set", tempo=100, scale_name="Nonexistent Scale")
    assert error["type"] == "bad_args" and "Dorian" in error["message"]
    assert song.tempo == 124.0  # nothing was written
    assert fail(bridge, "transport.set", clip_trigger_quantization="1/5")["type"] == "bad_args"
    assert fail(bridge, "transport.set", root_note="H")["type"] == "bad_args"
    assert fail(bridge, "transport.set", groove_amount=2)["type"] == "bad_args"
    assert fail(bridge, "transport.set", metronome="maybe")["type"] == "bad_args"
    assert fail(bridge, "transport.set")["type"] == "bad_args"
    assert fail(bridge, "transport.set", signature="3/4",
                signature_numerator=3)["type"] == "bad_args"


def test_back_to_arranger(bridge, song):
    song.back_to_arranger = True
    assert run(bridge, "transport.back_to_arranger") == {"back_to_arranger": False,
                                                          "was": True}
    assert song.back_to_arranger is False


def test_undo_redo(bridge, song):
    run(bridge, "transport.set_tempo", bpm=100)
    run(bridge, "transport.set_tempo", bpm=110)
    result = run(bridge, "transport.undo", steps=5)
    assert result["count"] == 2 and result["can_undo"] is False and result["can_redo"]
    result = run(bridge, "transport.redo")
    assert result["count"] == 1 and result["redone"] == ["Redo"]
    assert fail(bridge, "transport.undo", steps=0)["type"] == "bad_args"


def test_capture_midi(bridge, song, monkeypatch):
    error = fail(bridge, "transport.capture_midi")
    assert error["type"] == "invalid_state"
    monkeypatch.setattr(type(song), "can_capture_midi", property(lambda self: True))
    assert run(bridge, "transport.capture_midi", destination="session") == {
        "captured": True, "destination": "session"}
    assert song._captured_midi == 1
    assert fail(bridge, "transport.capture_midi", destination="tape")["type"] == "bad_args"


def test_stop_all_clips(bridge, song):
    song.tracks[0].clip_slots[0].fire()
    song.tracks[2].clip_slots[0].fire()
    result = run(bridge, "transport.stop_all_clips", quantized=False)
    assert result["stopped"] and result["quantized"] is False
    assert song.tracks[0].playing_slot_index == -1


def test_limitation_error_maps_to_unsupported(bridge, song, monkeypatch):
    def boom(*args):
        raise Live.Base.LimitationError("Intro allows only 16 scenes")
    monkeypatch.setattr(song, "tap_tempo", boom)
    error = fail(bridge, "transport.tap_tempo")
    assert error["type"] == "unsupported" and "Intro" in error["message"]


def test_every_transport_command_is_registered(bridge):
    listing = run(bridge, "system.commands", namespace="transport")
    names = {c["cmd"] for c in listing["commands"]}
    assert names == {
        "transport.get", "transport.play", "transport.continue", "transport.stop",
        "transport.toggle", "transport.set_position", "transport.set_tempo",
        "transport.tap_tempo", "transport.set_time_signature", "transport.set_loop",
        "transport.set", "transport.back_to_arranger", "transport.undo", "transport.redo",
        "transport.capture_midi", "transport.stop_all_clips"}
    assert {c["cmd"] for c in run(bridge, "system.commands", namespace="song")["commands"]} \
        == {"song.summary", "song.snapshot"}
    for command in listing["commands"]:
        assert command["doc"]


# --------------------------------------------------------------------------
# MCP tools — fake bridge (argument forwarding + local validation)
# --------------------------------------------------------------------------

@pytest.fixture()
def fake_app():
    fake = FakeBridge().start()
    client = BridgeClient(host=fake.host, port=fake.port, timeout=5.0)
    try:
        yield fake, create_app(client)
    finally:
        client.close()
        fake.stop()


TRANSPORT_TOOLS = {
    "live_set_snapshot", "live_transport_get", "live_transport_play", "live_transport_stop",
    "live_transport_set_position", "live_transport_set", "live_transport_set_loop",
    "live_transport_tap_tempo", "live_transport_undo", "live_transport_stop_all_clips",
}


def test_transport_tools_are_registered(fake_app):
    _fake, app = fake_app
    assert "transport" in app.tool_modules
    assert TRANSPORT_TOOLS <= tool_names(app)


def test_tools_forward_arguments(fake_app):
    fake, app = fake_app
    for cmd in ("song.snapshot", "song.summary", "transport.get", "transport.play",
                "transport.toggle", "transport.continue", "transport.stop",
                "transport.set_position", "transport.set", "transport.set_loop",
                "transport.tap_tempo", "transport.undo", "transport.redo",
                "transport.capture_midi", "transport.stop_all_clips"):
        fake.set_result(cmd, {"cmd": cmd})

    def last():
        return fake.requests[-1]["cmd"], fake.requests[-1].get("args", {})

    assert call_tool(app, "live_set_snapshot") == {"cmd": "song.snapshot"}
    assert last() == ("song.snapshot", {"detail": "summary", "include_clips": True,
                                        "include_devices": True, "include_returns": True,
                                        "include_scenes": True})
    call_tool(app, "live_set_snapshot", {"include_tracks": False, "detail": "full"})
    assert last() == ("song.summary", {"detail": "summary"})
    call_tool(app, "live_set_snapshot", {"offset": 2, "limit": 5})
    assert last()[1]["offset"] == 2 and last()[1]["limit"] == 5
    call_tool(app, "live_transport_get", {"include_scales": True})
    assert last() == ("transport.get", {"include_scales": True})
    call_tool(app, "live_transport_play", {"position": "17.1.1"})
    assert last() == ("transport.play", {"position": "17.1.1", "mode": "start"})
    call_tool(app, "live_transport_play", {"position": 8})
    assert last()[1]["position"] == 8
    call_tool(app, "live_transport_play", {"mode": "toggle"})
    assert last() == ("transport.toggle", {})
    call_tool(app, "live_transport_play", {"mode": "continue"})
    assert last() == ("transport.continue", {})
    call_tool(app, "live_transport_stop", {"stop_clips": True})
    assert last() == ("transport.stop", {"stop_clips": True, "quantized": False})
    call_tool(app, "live_transport_set_position", {"jump_by": -4})
    assert last() == ("transport.set_position", {"jump_by": -4.0})
    call_tool(app, "live_transport_set", {"tempo": 128, "link": True,
                                          "clip_trigger_quantization": "1/16",
                                          "root_note": "F#"})
    assert last() == ("transport.set", {"tempo": 128.0, "is_ableton_link_enabled": True,
                                        "clip_trigger_quantization": "1/16",
                                        "root_note": "F#"})
    call_tool(app, "live_transport_set_loop", {"start_bar": 9, "bars": 8, "enabled": True})
    assert last() == ("transport.set_loop", {"start_bar": 9, "bars": 8.0, "enabled": True})
    call_tool(app, "live_transport_tap_tempo")
    assert last()[0] == "transport.tap_tempo"
    call_tool(app, "live_transport_undo", {"steps": 2, "redo": True})
    assert last() == ("transport.redo", {"steps": 2})
    call_tool(app, "live_transport_stop_all_clips", {"quantized": False})
    assert last() == ("transport.stop_all_clips", {"quantized": False})


def test_tools_validate_locally(fake_app):
    fake, app = fake_app
    count = len(fake.requests)
    bad = [
        ("live_set_snapshot", {"detail": "everything"}),
        ("live_set_snapshot", {"limit": 0}),
        ("live_transport_play", {"mode": "rewind"}),
        ("live_transport_play", {"position": -1}),
        ("live_transport_set_position", {}),
        ("live_transport_set_position", {"position": 1, "jump_by": 2}),
        ("live_transport_set", {}),
        ("live_transport_set", {"tempo": 5}),
        ("live_transport_set", {"swing_amount": 3}),
        ("live_transport_set", {"groove_amount": 1.4}),
        ("live_transport_set", {"signature": "34"}),
        ("live_transport_set_loop", {}),
        ("live_transport_set_loop", {"start": 1, "start_bar": 1}),
        ("live_transport_set_loop", {"bars": 0}),
        ("live_transport_set_loop", {"length": 4, "end": 8}),
        ("live_transport_undo", {"steps": 0}),
    ]
    for name, args in bad:
        result = call_tool(app, name, args)
        assert isinstance(result, dict) and result.get("type") == "bad_args", (name, result)
    assert len(fake.requests) == count  # nothing reached the bridge


def test_tool_reports_bridge_errors(fake_app):
    fake, app = fake_app
    fake.set_error("transport.set", "bad_args", "unknown scale 'X'")
    result = call_tool(app, "live_transport_set", {"scale_name": "X"})
    assert result["type"] == "bad_args" and "unknown scale" in result["error"]


# --------------------------------------------------------------------------
# MCP tools — end to end through the real TCP server and the stub song
# --------------------------------------------------------------------------

def test_tools_end_to_end(tcp_bridge, song):
    client = BridgeClient(host="127.0.0.1", port=tcp_bridge.port, timeout=5.0)
    try:
        app = create_app(client)
        snap = call_tool(app, "live_set_snapshot")
        assert snap["tracks"][0]["name"] == "Bass"
        assert snap["song"]["tempo"] == 124.0
        assert call_tool(app, "live_transport_set", {"tempo": 130})["changed"]["tempo"] == 130
        assert song.tempo == 130.0
        loop = call_tool(app, "live_transport_set_loop", {"start_bar": 5, "bars": 4,
                                                          "enabled": True})
        assert loop["start_bbs"] == "5.1.1" and loop["end_bbs"] == "9.1.1"
        played = call_tool(app, "live_transport_play", {"position": "3.1.1"})
        assert played["is_playing"] is True and played["position"]["beats"] == 8.0
        assert call_tool(app, "live_transport_stop")["is_playing"] is False
        state = call_tool(app, "live_transport_get")
        assert state["loop"]["on"] is True and state["tempo"] == 130.0
        summary = call_tool(app, "live_set_snapshot", {"include_tracks": False})
        assert summary["track_count"] == 3
        error = call_tool(app, "live_transport_set", {"scale_name": "Klingon"})
        assert error["type"] == "bad_args"
    finally:
        client.close()


# --------------------------------------------------------------------------
# real Live 12.4.5 behaviour (tests/live_stub_ext/transport_live.py, deferred model)
# --------------------------------------------------------------------------

def test_transport_results_report_the_requested_state(bridge, song, live_12_4_5):
    # Live applies start/stop/continue and playhead moves on its next tick: reading them
    # back in the same command still gives the old values.
    live_12_4_5.defer(True)
    song.start_time = 8.0
    played = run(bridge, "transport.play")
    assert played["is_playing"] is True and played["position"]["beats"] == 8.0
    assert song.is_playing is False                    # not yet ...
    live_12_4_5.tick(song)
    assert song.is_playing is True and song.current_song_time == 8.0
    assert run(bridge, "transport.stop")["is_playing"] is False
    live_12_4_5.tick(song)
    assert song.is_playing is False
    assert run(bridge, "transport.toggle")["is_playing"] is True
    live_12_4_5.tick(song)
    assert run(bridge, "transport.toggle")["is_playing"] is False
    live_12_4_5.tick(song)
    assert run(bridge, "transport.continue")["is_playing"] is True
    live_12_4_5.tick(song)
    run(bridge, "transport.stop")
    live_12_4_5.tick(song)


def test_play_from_a_position_while_stopped_really_starts_there(bridge, song, live_12_4_5):
    # The bug found on real Live: current_song_time + continue_playing() in one tick started
    # from the OLD playhead.  The start marker is immediate, so play starts from it.
    live_12_4_5.defer(True)
    song.current_song_time = 3.0
    live_12_4_5.tick(song)
    result = run(bridge, "transport.play", position=16)
    assert result["position"]["beats"] == 16.0 and result["is_playing"] is True
    live_12_4_5.tick(song)
    assert song.is_playing and song.current_song_time == 16.0 and song.start_time == 16.0
    jumped = run(bridge, "transport.play", position="9.1.1")    # playing: a quantized jump
    assert jumped["quantized"] is True and jumped["position"]["beats"] == 32.0
    live_12_4_5.tick(song)
    assert song.current_song_time == 32.0 and song.start_time == 16.0


def test_set_position_reports_the_target(bridge, song, live_12_4_5):
    live_12_4_5.defer(True)
    result = run(bridge, "transport.set_position", position="3.1.1")
    assert result["position"]["beats"] == 8.0 and result["start_time"]["beats"] == 8.0
    assert song.current_song_time == 0.0              # Live moves it on the next tick
    live_12_4_5.tick(song)
    assert song.current_song_time == 8.0
    back = run(bridge, "transport.set_position", jump_by=-100)
    assert back["position"]["beats"] == 0.0
    live_12_4_5.tick(song)
    assert song.current_song_time == 0.0 and song.start_time == 0.0


def test_positions_after_the_song_length_extend_the_song(bridge, song, live_12_4_5):
    # Live 12.4.5 refuses a playhead / start marker behind song_length ("Cannot set the
    # Songtime behind the Songlength"); LiveBridge stretches the loop brace in 32-beat steps
    # (song_length follows at once), writes the position and puts the brace back.
    live_12_4_5.defer(True)
    limit = song.song_length
    brace = (song.loop_start, song.loop_length)
    target = limit + 100
    result = run(bridge, "transport.set_position", position=target)
    assert result["song_extended"] is True and result["position"]["beats"] == target
    assert result["start_time"]["beats"] == target
    assert (song.loop_start, song.loop_length) == brace          # brace put back
    assert song.start_time == target                              # immediate
    live_12_4_5.tick(song)
    assert song.current_song_time == target                      # deferred write survived
    inside = run(bridge, "transport.set_position", position=4)
    assert "song_extended" not in inside
    played = run(bridge, "transport.play", position=song.song_length + 40)
    assert played["song_extended"] is True and played["is_playing"] is True
    assert (song.loop_start, song.loop_length) == brace
    live_12_4_5.tick(song)
    assert song.is_playing and song.start_time == played["position"]["beats"]


def test_positions_too_far_behind_the_song_are_refused(bridge, song):
    from LiveBridge.handlers import transport as transport_module
    limit = song.song_length
    too_far = limit + transport_module.MAX_EXTEND_STEPS * 32 + 8
    brace = (song.loop_start, song.loop_length)
    error = fail(bridge, "transport.set_position", position=too_far)
    assert error["type"] == "bad_args" and "after the end of the song" in error["message"]
    assert fail(bridge, "transport.play", position=too_far)["type"] == "bad_args"
    assert fail(bridge, "transport.set_position", jump_by=too_far * 2)["type"] == "bad_args"
    assert (song.loop_start, song.loop_length) == brace and song.current_song_time == 0.0
    assert run(bridge, "transport.set_position", position=limit)["position"]["beats"] == limit


def test_extend_song_steps_the_loop_brace(bridge, song):
    from LiveBridge.handlers import transport as transport_module
    writes = []
    cls = type(song)
    original = cls.__dict__["loop_length"]

    def fset(self, value):
        writes.append(value)
        original.fset(self, value)

    cls.loop_length = property(original.fget, fset)
    try:
        limit = song.song_length
        assert transport_module.extend_song(song, limit) is None and writes == []
        saved = transport_module.extend_song(song, limit + 70)
        assert saved == (0.0, 16.0)
        assert song.song_length >= limit + 70
        assert len(writes) == 3                         # 32 beats per step: 3 steps for 70
        assert transport_module.restore_loop(song, saved)
        assert (song.loop_start, song.loop_length) == (0.0, 16.0)
    finally:
        cls.loop_length = original


def test_set_loop_follows_live_limits(bridge, song, live_12_4_5):
    limit = song.song_length
    # a brace behind the end extends the song (the brace then holds the length itself)
    far = run(bridge, "transport.set_loop", start=limit + 60, length=8)
    assert far["song_extended"] is True and (far["start"], far["length"]) == (limit + 60, 8.0)
    assert (song.loop_start, song.loop_length) == (limit + 60, 8.0)
    assert song.song_length >= limit + 68
    run(bridge, "transport.set_loop", start=0, length=16)
    limit = song.song_length
    too_far = fail(bridge, "transport.set_loop", start=limit + 5000, length=4)
    assert too_far["type"] == "bad_args" and "after the end of the song" in too_far["message"]
    assert (song.loop_start, song.loop_length) == (0.0, 16.0)      # nothing changed
    # moving a long brace later must resize first (Live checks each write)
    loop = run(bridge, "transport.set_loop", start=limit - 4, length=4)
    assert (loop["start"], loop["length"]) == (limit - 4, 4.0) and "song_extended" not in loop
    loop = run(bridge, "transport.set_loop", start=0, length=16)
    assert (loop["start"], loop["length"]) == (0.0, 16.0)
    assert run(bridge, "transport.set_loop", length=0.25)["length"] == 1.0   # Live's minimum
    live_12_4_5.defer(True)
    assert run(bridge, "transport.set_loop", enabled=True)["on"] is True
    assert song.loop is False
    live_12_4_5.tick(song)
    assert song.loop is True


def test_transport_set_reports_requested_deferred_flags(bridge, song, live_12_4_5):
    live_12_4_5.defer(True)
    changed = run(bridge, "transport.set", punch_in=True, punch_out=True, loop=True,
                  session_automation_record=True, metronome=True)["changed"]
    assert changed == {"metronome": True, "punch_in": True, "punch_out": True,
                       "session_automation_record": True, "loop": True}
    assert song.punch_in is False and song.metronome is True
    live_12_4_5.tick(song)
    assert song.punch_in and song.punch_out and song.loop and song.session_automation_record


def test_groove_amount_goes_to_131_percent(bridge, song):
    assert run(bridge, "transport.set", groove_amount=1.3125)["changed"] == \
        {"groove_amount": 1.3125}
    error = fail(bridge, "transport.set", groove_amount=1.4)
    assert error["type"] == "bad_args" and "1.3125" in error["message"]


def test_record_mode_starts_playback_like_live(bridge, song, live_12_4_5):
    # Live's default "Start Playback with Record" preference: record_mode on starts playing.
    live_12_4_5.defer(True)
    assert run(bridge, "transport.set", record_mode=True)["changed"] == {"record_mode": True}
    live_12_4_5.tick(song)
    assert song.record_mode is True and song.is_playing is True


def test_summary_and_errors_are_compact(bridge):
    summary = run(bridge, "song.summary")
    assert "kind" not in summary and "kind" not in summary["selected_track"]
    error = fail(bridge, "song.snapshot", limit=0)
    assert error["message"] == "limit must be >= 1, got 0"


def test_loop_brace_walks_when_it_alone_defines_the_song_length(bridge, song):
    # Empty arrangement: song_length = loop end + 32 and shrinks with the brace (Live 12.4.5),
    # so "resize first, then move" fails and one move later is limited to 32 beats.
    song._cue_points[:] = []
    for track in song.tracks:
        track._arrangement_clips[:] = []
    assert song.song_length == 48.0                       # brace 0..16 + 32
    loop = run(bridge, "transport.set_loop", start=40, length=4)
    assert (loop["start"], loop["length"]) == (40.0, 4.0) and "song_extended" not in loop
    far = run(bridge, "transport.set_loop", start=400, length=8)
    assert (song.loop_start, song.loop_length) == (400.0, 8.0) and far["song_extended"]
    back = run(bridge, "transport.set_loop", start=0, length=16)
    assert (back["start"], back["length"]) == (0.0, 16.0) and song.song_length == 48.0
