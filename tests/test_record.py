"""Module B — recording: every ``record.*`` command through the dispatcher on the stub song
and every ``live_record_*`` MCP tool (fake bridge + end-to-end through the TCP bridge)."""

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



def call(bridge, cmd, **args):
    return bridge.dispatch({"id": "rec", "cmd": cmd, "args": args})


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


@pytest.fixture()
def multi_arm(song):
    """Turn the stub's Exclusive Arm preference off (stub-only switch)."""
    song._exclusive_arm = False
    return song


# --------------------------------------------------------------------------
# status / arm
# --------------------------------------------------------------------------

def test_status_shape(bridge, song):
    status = ok(bridge, "record.status")
    for key in ("is_playing", "record_mode", "session_record", "session_record_status",
                "arrangement_overdub", "automation_arm", "punch_in", "punch_out",
                "punch_region", "metronome", "count_in", "is_counting_in",
                "midi_quantization", "exclusive_arm", "can_capture_midi", "armed_tracks",
                "recording_slots", "current_song_time"):
        assert key in status, key
    assert status["punch_region"] == {"start": 0.0, "end": 16.0, "loop_on": False}
    assert status["count_in"] == "none" and status["session_record_status"] == "off"
    assert status["armed_tracks"] == []


def test_arm_multi_with_monitoring(bridge, multi_arm):
    song = multi_arm
    result = ok(bridge, "record.arm", track=["Bass", "Vocals"], monitoring="in")
    assert result["changed"] == ["Bass", "Vocals"]
    assert [t["name"] for t in result["armed_tracks"]] == ["Bass", "Vocals"]
    assert all(t["monitoring"] == "in" for t in result["armed_tracks"])
    assert song.tracks[0].arm and song.tracks[1].arm
    assert any("audio interface" in n for n in result["notes"])


def test_arm_exclusive_and_disarm(bridge, multi_arm):
    song = multi_arm
    ok(bridge, "record.arm", track=["Bass", "Vocals"])
    ok(bridge, "record.arm", track="Drums", exclusive=True)
    assert [t.arm for t in song.tracks] == [False, False, True]
    ok(bridge, "record.arm", track="Drums", arm="toggle")
    assert not song.tracks[2].arm
    ok(bridge, "record.arm", track="all")
    assert all(t.arm for t in song.tracks)
    ok(bridge, "record.arm", track="all", arm=False)
    assert not any(t.arm for t in song.tracks)


def test_api_arm_ignores_the_exclusive_arm_preference(bridge, song):
    # Real Live 12.4.5 (exclusive_arm preference on): arming through the API keeps the other
    # armed tracks armed — only clicks in Live's UI are exclusive.
    assert song.exclusive_arm is True
    ok(bridge, "record.arm", track="Bass")
    result = ok(bridge, "record.arm", track="Drums")
    assert [t["name"] for t in result["armed_tracks"]] == ["Bass", "Drums"]
    assert not any("Exclusive Arm" in n for n in result.get("notes", []))
    assert "kind" not in result["armed_tracks"][0]
    exclusive = ok(bridge, "record.arm", track="Vocals", exclusive=True)
    assert [t["name"] for t in exclusive["armed_tracks"]] == ["Vocals"]


def test_arm_warns_about_no_input(bridge, song):
    song.tracks[0].input_routing_type = song.tracks[0].available_input_routing_types[-1]
    result = ok(bridge, "record.arm", track="Bass")
    assert "No Input" in result["notes"][0]


def test_arm_errors(bridge):
    assert err(bridge, "record.arm", track="A")[0] == "bad_args"
    assert err(bridge, "record.arm", track="master")[0] == "bad_args"
    assert err(bridge, "record.arm", track="Bass", monitoring="loud")[0] == "bad_args"
    assert err(bridge, "record.arm", track="Bass", arm="maybe")[0] == "bad_args"


# --------------------------------------------------------------------------
# session recording
# --------------------------------------------------------------------------

def test_session_record_into_first_empty_slot(bridge, song):
    result = ok(bridge, "record.session", track="Bass", length_bars=2, launch_quantization="1 bar")
    assert result["slot"] == "song.tracks[0].clip_slots[2]" and result["slot_index"] == 2
    assert result["record_length_beats"] == 8.0
    assert result["fired"] is True and result["was_playing"] is False
    slot = song.tracks[0].clip_slots[2]
    assert slot.has_clip and slot.clip.length == 8.0
    assert song.tracks[0].arm is True
    assert song.view.selected_track is song.tracks[0]
    assert song.view.highlighted_clip_slot is slot
    assert song.view.selected_scene is song.scenes[2]
    status = ok(bridge, "record.status")
    assert status["recording_slots"][0]["path"] == "song.tracks[0].clip_slots[2]"


def test_session_record_length_follows_time_signature(bridge, song):
    song.signature_numerator = 3
    result = ok(bridge, "record.session", track="Vocals", slot="Verse", length_bars=2)
    assert result["record_length_beats"] == 6.0 and result["slot_index"] == 1
    assert ok(bridge, "record.session", track="Drums", length_beats=5)["record_length_beats"] \
        == 5.0


def test_session_record_defaults_to_selected_track(bridge, song):
    song.view.selected_track = song.tracks[2]
    result = ok(bridge, "record.session")
    assert result["track"] == "Drums" and result["slot_index"] == 1


def test_session_record_full_slot(bridge, song):
    kind, message = err(bridge, "record.session", track="Bass", slot=0)
    assert kind == "invalid_state" and "overwrite" in message
    result = ok(bridge, "record.session", track="Bass", slot=0, overwrite=True)
    assert result["slot_index"] == 0
    assert song.tracks[0].clip_slots[0].clip.is_recording


def test_session_record_adds_a_scene_when_full(bridge, song):
    for index in range(4):
        slot = song.tracks[1].clip_slots[index]
        if not slot.has_clip:
            slot.create_audio_clip("/Samples/fill.wav")
    song.scenes[3].tempo = 90.0
    song.scenes[3].tempo_enabled = True
    song.view.selected_scene = song.scenes[0]
    result = ok(bridge, "record.session", track="Vocals")
    assert result["slot_index"] == 4 and len(song.scenes) == 5
    # Live copied scene 3's tempo into the new scene: it is cleared again
    assert song.scenes[4].tempo_enabled is False and song.scenes[3].tempo_enabled is True


def test_session_record_trigger_method(bridge, song):
    result = ok(bridge, "record.session", track="Bass", method="trigger", length_bars=1)
    assert result["method"] == "trigger"
    assert song.session_record is True


def test_session_record_errors(bridge, song):
    assert err(bridge, "record.session", track="A")[0] == "bad_args"
    assert err(bridge, "record.session", track="Bass", length_bars=-1)[0] == "bad_args"
    assert err(bridge, "record.session", track="Bass", length_bars=1,
               length_beats=4)[0] == "bad_args"
    assert err(bridge, "record.session", track="Bass", launch_quantization="soon")[0] == \
        "bad_args"
    assert err(bridge, "record.session", track="Bass", method="loop")[0] == "bad_args"
    assert err(bridge, "record.session", track="Bass", slot=12)[0] == "not_found"


def test_session_record_launch_quantization_names():
    from LiveBridge.handlers import record as record_module
    parse = record_module._parse_launch_quantization
    assert parse("none") == 0 and parse("1 bar") == 4 and parse("1/16") == 11
    assert parse("2 bars") == 3 and parse("1/2T") == 6 and parse(7) == 7
    assert parse("global") is None and parse("q_eight") == 9


# --------------------------------------------------------------------------
# arrangement / stop / settings / capture
# --------------------------------------------------------------------------

def test_arrangement_record_start_and_stop(bridge, song):
    ok(bridge, "record.arm", track="Bass")
    status = ok(bridge, "record.arrangement", time=8, overdub=True, punch_in=True)
    assert status["record_mode"] is True and status["is_playing"] is True
    assert status["arrangement_overdub"] is True and status["punch_in"] is True
    assert song.start_time == 8.0 and "notes" not in status
    ok(bridge, "record.arrangement", time=16)
    assert song.current_song_time == 16.0
    stopped = ok(bridge, "record.arrangement", start=False)
    assert stopped["record_mode"] is False and stopped["is_playing"] is True


def test_session_record_starts_on_the_next_tick(bridge, song, live_12_4_5):
    live_12_4_5.defer(True)
    result = ok(bridge, "record.session", track="Bass", length_bars=1)
    slot = song.tracks[0].clip_slots[2]
    assert result["fired"] is True and not slot.has_clip     # Live: nothing yet
    live_12_4_5.tick(song)
    assert slot.clip.is_recording and slot.clip.name == "Bass 3" and song.is_playing
    status = ok(bridge, "record.status")
    assert status["session_record"] is True and status["session_record_status"] == "on"
    assert status["recording_slots"][0]["slot"] == 2
    stopped = ok(bridge, "record.stop", stop_transport=True)
    assert stopped["is_playing"] is False and stopped["session_record"] is False
    assert song.is_playing is True                           # still the old value ...
    live_12_4_5.tick(song)
    assert song.is_playing is False                          # ... until Live's next tick


def test_arrangement_record_reports_the_requested_state(bridge, song, live_12_4_5):
    live_12_4_5.defer(True)
    ok(bridge, "record.arm", track="Bass")
    status = ok(bridge, "record.arrangement", time="3.1.1", punch_in=True)
    assert status["record_mode"] is True and status["is_playing"] is True
    assert status["punch_in"] is True
    assert song.record_mode is False and song.is_playing is False and song.start_time == 8.0
    live_12_4_5.tick(song)
    assert song.record_mode and song.is_playing and song.punch_in
    assert song.current_song_time == 8.0
    released = ok(bridge, "record.arrangement", start=False)
    assert released["record_mode"] is False
    assert err(bridge, "record.arrangement", time=song.song_length + 10000)[0] == "bad_args"
    assert err(bridge, "record.arrangement", start="yes")[0] == "bad_args"


def test_arrangement_record_behind_the_song_end_extends_the_song(bridge, song):
    # Live refuses a start marker behind song_length: the song is extended (loop brace
    # stretched and put back) instead of answering bad_args.
    target = song.song_length + 20
    status = ok(bridge, "record.arrangement", time=target, start=False)
    assert song.start_time == target and status["record_mode"] is False
    assert (song.loop_start, song.loop_length) == (0.0, 16.0)


def test_arrangement_record_validates_before_writing(bridge, song):
    assert err(bridge, "record.arrangement", overdub=True, punch_in="maybe")[0] == "bad_args"
    assert song.arrangement_overdub is False and song.record_mode is False


def test_arrangement_record_warns_without_armed_tracks(bridge, song):
    status = ok(bridge, "record.arrangement")
    assert "no track is armed" in status["notes"][0]
    assert err(bridge, "record.arrangement", time=-1)[0] == "bad_args"


def test_stop_everything(bridge, multi_arm):
    song = multi_arm
    ok(bridge, "record.session", track="Bass")
    song.record_mode = True
    song.session_record = True
    song.is_playing = True
    result = ok(bridge, "record.stop", stop_transport=True, stop_clips=True, disarm=True)
    assert set(result["stopped"]) == {"arrangement_record", "session_record", "clips",
                                      "transport", "arm"}
    assert result["recording_slots"] == [] and result["armed_tracks"] == []
    assert song.is_playing is False and song.record_mode is False


def test_stop_keeps_clips_by_default(bridge, song):
    ok(bridge, "record.session", track="Bass")
    result = ok(bridge, "record.stop")
    assert result["stopped"] == []
    assert song.tracks[0].clip_slots[2].clip.is_playing


def test_settings(bridge, song):
    result = ok(bridge, "record.settings", metronome=True, overdub="toggle", punch_in=True,
                punch_start=8, punch_end=24, midi_quantization="1/16", automation_arm=True)
    assert song.metronome and song.arrangement_overdub and song.punch_in
    assert song.loop_start == 8.0 and song.loop_length == 16.0
    assert song.midi_recording_quantization == 5 and song.session_automation_record
    assert result["punch_region"] == {"start": 8.0, "end": 24.0, "loop_on": False}
    assert result["midi_quantization"] == "1/16"
    assert set(result["changed"]) == {"metronome", "overdub", "punch_in", "punch_region",
                                      "midi_quantization", "automation_arm"}
    ok(bridge, "record.settings", metronome="toggle", punch_from_loop=True, punch_end=12)
    assert not song.metronome and song.punch_in and song.punch_out
    assert song.loop_start == 8.0 and song.loop_length == 4.0
    ok(bridge, "record.settings", midi_quantization="1/8+1/8T")
    assert song.midi_recording_quantization == 4


def test_settings_report_requested_deferred_flags(bridge, song, live_12_4_5):
    live_12_4_5.defer(True)
    result = ok(bridge, "record.settings", punch_in=True, punch_out=True, automation_arm=True,
                metronome=True)
    assert result["punch_in"] and result["punch_out"] and result["automation_arm"]
    assert result["metronome"] is True
    assert song.punch_in is False                            # Live applies it next tick
    live_12_4_5.tick(song)
    assert song.punch_in and song.punch_out and song.session_automation_record


def test_settings_validate_before_writing(bridge, song):
    assert err(bridge, "record.settings", metronome=True, midi_quantization="1/64")[0] == \
        "bad_args"
    assert song.metronome is False
    kind, message = err(bridge, "record.settings", metronome=True, punch_start=8,
                        punch_end=song.song_length + 10000)
    assert kind == "bad_args" and "song_length" in message
    assert song.metronome is False and (song.loop_start, song.loop_length) == (0.0, 16.0)


def test_punch_region_behind_the_song_end_extends_the_song(bridge, song):
    # Live refuses a loop brace (= punch region) behind song_length; the brace is walked
    # there in steps Live accepts, and then holds the longer song by itself.
    end = song.song_length + 40
    result = ok(bridge, "record.settings", punch_start=end - 8, punch_end=end)
    assert result["punch_region"]["start"] == end - 8 and result["punch_region"]["end"] == end
    assert (song.loop_start, song.loop_length) == (end - 8, 8.0)


def test_settings_errors(bridge):
    assert err(bridge, "record.settings")[0] == "bad_args"
    assert err(bridge, "record.settings", punch_start=8, punch_end=4)[0] == "bad_args"
    assert err(bridge, "record.settings", punch_start=-2)[0] == "bad_args"
    assert err(bridge, "record.settings", midi_quantization="1/64")[0] == "bad_args"
    assert err(bridge, "record.settings", metronome="loud")[0] == "bad_args"


def test_capture_midi(bridge, song, monkeypatch):
    kind, message = err(bridge, "record.capture_midi")
    assert kind == "invalid_state" and "nothing to capture" in message
    monkeypatch.setattr(type(song), "can_capture_midi", property(lambda self: True))
    assert ok(bridge, "record.capture_midi", destination="session") == \
        {"captured": True, "destination": "session"}
    assert song._captured_midi == 1
    assert ok(bridge, "record.capture_midi", destination=2)["destination"] == "arrangement"
    assert err(bridge, "record.capture_midi", destination="clipboard")[0] == "bad_args"


def test_record_commands_registered(bridge):
    listing = ok(bridge, "system.commands", namespace="record")["commands"]
    assert {c["cmd"] for c in listing} == {"record.status", "record.arm", "record.session",
                                           "record.arrangement", "record.stop",
                                           "record.settings", "record.capture_midi",
                                           "record.resample", "record.resample_status"}
    readers = ("record.status", "record.resample_status")
    assert all(c["mutating"] for c in listing if c["cmd"] not in readers)
    assert not any(c["mutating"] for c in listing if c["cmd"] in readers)


# --------------------------------------------------------------------------
# record.resample (bounce a section onto an audio track)
# --------------------------------------------------------------------------

@pytest.fixture()
def resampler():
    """record.resample keeps its running/last pass in module state — isolate each test."""
    from LiveBridge.handlers import record as record_module
    record_module._RESAMPLER.update(current=None, last=None)
    yield record_module
    record_module._RESAMPLER.update(current=None, last=None)


def _play_pass(bridge, song, dest, frm, to, record_clip=True, step=1.0):
    """Play the stub song from ``frm`` to ``to`` one display tick per ``step`` beats; like
    Live, the armed track gets an audio clip over the punch region (the loop brace)."""
    time, made = frm, False
    region = (song.loop_start, song.loop_start + song.loop_length)
    while time <= to + 1e-9:
        song.current_song_time = time
        if record_clip and not made and time >= region[1] and dest.arm and song.record_mode:
            clip = Live.Clip.Clip("%s 0001" % dest.name, region[1] - region[0], False, dest,
                                  "/Recorded/%s 0001.wav" % dest.name, arrangement=True)
            clip._start_time = region[0]
            dest._add_arrangement_clip(clip)
            made = True
        bridge.update_display()
        time += step
    for _ in range(4):                      # stopping -> restoring -> collecting -> done
        bridge.update_display()


def test_resample_the_master_into_a_new_track(bridge, song, resampler):
    drums = song.tracks[2]
    drums.arm = True
    song.loop = True
    song.current_song_time = 2.0
    tracks = len(song.tracks)
    started = ok(bridge, "record.resample", start="3.1.1", bars=2, preroll=2)
    assert started["phase"] == "arming" and started["source"] == "master"
    assert started["range"] == {"start": 8.0, "end": 16.0, "start_bbs": "3.1.1",
                                "end_bbs": "5.1.1"}
    assert started["expected_seconds"] == pytest.approx(10 * 60 / 124.0, abs=0.1)
    assert started["track"]["created"] is True and len(song.tracks) == tracks + 1
    dest = song.tracks[-1]
    assert dest.name == "Resample 3.1.1-5.1.1" == started["track"]["name"]
    assert dest.input_routing_type.display_name == "Resampling"
    assert dest.current_monitoring_state == 2 and dest.arm is True      # monitoring off
    assert drums.arm is False                             # only the destination records
    assert (song.loop, song.punch_in, song.punch_out) == (False, True, True)
    assert (song.loop_start, song.loop_length) == (8.0, 8.0)             # the punch region
    assert song.start_time == 6.0                                        # preroll
    busy = err(bridge, "record.resample", start=0, bars=1)
    assert busy[0] == "invalid_state"
    bridge.update_display()                               # arming -> Arrangement Record on
    assert song.record_mode is True and song.is_playing is True
    assert ok(bridge, "record.resample_status")["phase"] == "waiting"
    _play_pass(bridge, song, dest, 6.0, 17.0)
    status = ok(bridge, "record.resample_status")
    assert status["phase"] == "done" and status["finished"] is True, status
    assert status["clips"] == [{"name": "Resample 3.1.1-5.1.1 0001",
                                "path": "song.tracks[3].arrangement_clips[0]",
                                "start": 8.0, "end": 16.0,
                                "file_path": "/Recorded/Resample 3.1.1-5.1.1 0001.wav"}]
    assert song.record_mode is False and song.is_playing is False
    assert (song.loop, song.punch_in, song.punch_out) == (True, False, False)
    assert (song.loop_start, song.loop_length) == (0.0, 16.0)
    assert drums.arm is True and dest.arm is False and status["restored"]["rearmed"] == ["Drums"]
    assert dest.input_routing_type.display_name == "No Input"
    assert dest.current_monitoring_state == 1                  # auto: plays its new clip
    assert song.current_song_time == 2.0


def test_resample_without_a_recorded_clip_fails_and_removes_the_new_track(bridge, song,
                                                                          resampler):
    tracks = len(song.tracks)
    ok(bridge, "record.resample", start=0, end=4, preroll=0)
    dest = song.tracks[-1]
    bridge.update_display()
    _play_pass(bridge, song, dest, 0.0, 5.0, record_clip=False)
    for _ in range(12):
        bridge.update_display()                         # waits for Live's clip, then gives up
    status = ok(bridge, "record.resample_status")
    assert status["phase"] == "failed" and "audio engine" in status["error"]
    assert status["track"]["deleted"] is True and len(song.tracks) == tracks


def test_resample_a_track_onto_an_existing_track_and_stop(bridge, song, resampler):
    from live_stub_ext import routing as routing_ext
    uninstall = routing_ext.install(Live)
    try:
        target = factory_track(song, "Bounce")
        target.current_monitoring_state = 0
        started = ok(bridge, "record.resample", start=4, length="1.0.0", source="Vocals",
                     track="Bounce", preroll=0)
        assert started["track"] == {"name": "Bounce", "path": "song.tracks[3]",
                                    "created": False}
        assert target.input_routing_type.display_name == "Vocals"
        bridge.update_display()
        song.current_song_time = 5.0
        bridge.update_display()
        assert ok(bridge, "record.resample_status")["phase"] == "recording"
        stopped = ok(bridge, "record.resample", action="stop")
        assert stopped["phase"] == "stopping" and "stopped" in stopped["error"]
        for _ in range(4):
            bridge.update_display()
        status = ok(bridge, "record.resample_status")
        assert status["phase"] == "aborted" and status["clips"] == []
        assert "deleted" not in status["track"] and song.tracks[3] == target  # kept (not ours)
        assert target.input_routing_type.display_name == "Ext. In"            # put back
        assert target.current_monitoring_state == 0 and target.arm is False
    finally:
        uninstall()


def factory_track(song, name):
    from live_stub import factory
    return factory.add_track(song, name, "audio")


def test_resample_validation(bridge, song, resampler):
    tracks = len(song.tracks)
    for args in ({}, {"start": 0}, {"start": 0, "end": 4, "bars": 1}, {"start": 8, "end": 4},
                 {"start": 0, "bars": 0}, {"start": 0, "bars": 1, "preroll": 20},
                 {"start": 0, "bars": 1, "track": "Bass"},           # a MIDI track
                 {"start": 0, "bars": 1, "track": "Vocals", "source": "Vocals"},
                 {"start": 0, "bars": 1, "name": 5}, {"start": 0, "bars": 1000},
                 {"start": 0, "bars": 1, "action": "pause"},
                 {"start": song.song_length + 9000, "bars": 1}):
        assert err(bridge, "record.resample", **args)[0] == "bad_args", args
    assert err(bridge, "record.resample", action="stop")[0] == "invalid_state"
    assert ok(bridge, "record.resample_status") == {"phase": "idle", "finished": True}
    song._is_playing = True
    assert err(bridge, "record.resample", start=0, bars=1)[0] == "invalid_state"
    song._is_playing = False
    missing = err(bridge, "record.resample", start=0, bars=1, source="Bass")  # MIDI source
    assert missing[0] == "not_found"
    assert len(song.tracks) == tracks                       # the new track was removed again
    assert (song.loop_start, song.loop_length) == (0.0, 16.0) and song.punch_in is False


# --------------------------------------------------------------------------
# MCP tools
# --------------------------------------------------------------------------

RECORD_TOOLS = {"live_record_status", "live_record_arm", "live_record_session",
                "live_record_arrangement", "live_record_stop", "live_record_settings",
                "live_record_capture_midi", "live_record_resample"}


@pytest.fixture()
def fake_app():
    fake = FakeBridge().start()
    client = BridgeClient(host=fake.host, port=fake.port, timeout=5.0)
    try:
        yield create_app(client), fake
    finally:
        client.close()
        fake.stop()


def _last(fake, cmd):
    return [r for r in fake.requests if r["cmd"] == cmd][-1]["args"]


def test_record_tools_registered(fake_app):
    app, _fake = fake_app
    tools = {t.name: t for t in asyncio.run(app.list_tools())}
    assert RECORD_TOOLS <= set(tools)
    assert all("Returns" in (tools[n].description or "") for n in RECORD_TOOLS)


def test_record_tools_forward_arguments(fake_app):
    app, fake = fake_app
    for cmd in ("record.status", "record.arm", "record.session", "record.arrangement",
                "record.stop", "record.settings", "record.capture_midi"):
        fake.set_result(cmd, {"ok": cmd})
    assert call_tool(app, "live_record_status") == {"ok": "record.status"}
    call_tool(app, "live_record_arm", {"track": ["Bass", 2], "monitoring": "Off"})
    assert _last(fake, "record.arm") == {"track": ["Bass", 2], "arm": True, "monitoring": "off"}
    fake.set_result("record.status", {"is_playing": True, "session_record": True,
                                      "session_record_status": "on", "is_counting_in": False,
                                      "recording_slots": [{"slot": 2}], "metronome": False})
    session = call_tool(app, "live_record_session", {"track": "Bass", "length_bars": 4,
                                                     "launch_quantization": "1 bar"})
    assert _last(fake, "record.session") == {"track": "Bass", "length_bars": 4,
                                             "launch_quantization": "1 bar", "method": "slot"}
    # Live starts recording on its next tick: the tool reads the state back
    assert fake.requests[-1]["cmd"] == "record.status"
    assert session["status"] == {"is_playing": True, "session_record": True,
                                 "session_record_status": "on", "is_counting_in": False,
                                 "recording_slots": [{"slot": 2}]}
    fake.set_result("record.status", {"ok": "record.status"})
    call_tool(app, "live_record_session", {"launch_quantization": "global",
                                           "method": "Trigger", "overwrite": True})
    assert _last(fake, "record.session") == {"method": "trigger", "overwrite": True}
    call_tool(app, "live_record_arrangement", {"time": 16, "overdub": True})
    assert _last(fake, "record.arrangement") == {"start": True, "time": 16, "overdub": True}
    call_tool(app, "live_record_stop", {"stop_transport": True})
    assert _last(fake, "record.stop") == {"stop_transport": True, "stop_clips": False,
                                          "disarm": False}
    call_tool(app, "live_record_settings", {"metronome": "toggle", "punch_from_loop": True,
                                            "midi_quantization": "1/16T"})
    assert _last(fake, "record.settings") == {"metronome": "toggle", "punch_from_loop": True,
                                              "midi_quantization": "1/16T"}
    call_tool(app, "live_record_capture_midi", {"destination": "Session"})
    assert _last(fake, "record.capture_midi") == {"destination": "session"}


def test_resample_tool_waits_for_the_pass(fake_app, monkeypatch):
    from livebridge_mcp.tools import record as record_tools
    monkeypatch.setattr(record_tools, "RESAMPLE_POLL", 0.01)
    app, fake = fake_app
    fake.set_result("record.resample", {"phase": "arming", "finished": False,
                                        "expected_seconds": 0.1})
    fake.set_result("record.resample_status", {"phase": "done", "finished": True,
                                               "clips": [{"name": "x"}]})
    result = call_tool(app, "live_record_resample", {"start": "9.1.1", "bars": 8,
                                                     "source": "Drums", "channel": "Post FX"})
    assert result == {"phase": "done", "finished": True, "clips": [{"name": "x"}]}
    assert _last(fake, "record.resample") == {"start": "9.1.1", "bars": 8, "source": "Drums",
                                              "channel": "Post FX"}
    assert fake.requests[-1]["cmd"] == "record.resample_status"
    started = call_tool(app, "live_record_resample", {"start": 0, "end": 16, "wait": False,
                                                      "preroll": 0})
    assert started["phase"] == "arming"
    assert _last(fake, "record.resample") == {"start": 0, "end": 16, "preroll": 0.0}
    fake.set_result("record.resample", {"phase": "arming", "expected_seconds": 500.0})
    long_pass = call_tool(app, "live_record_resample", {"start": 0, "bars": 200})
    assert "poll" in long_pass["note"]
    call_tool(app, "live_record_resample", {"action": "status"})
    assert fake.requests[-1]["cmd"] == "record.resample_status"
    call_tool(app, "live_record_resample", {"action": "stop"})
    assert _last(fake, "record.resample") == {"action": "stop"}


@pytest.mark.parametrize("name,args", [
    ("live_record_arm", {"track": []}),
    ("live_record_arm", {"track": "Bass", "monitoring": "loud"}),
    ("live_record_session", {"method": "loop"}),
    ("live_record_session", {"length_bars": 0}),
    ("live_record_session", {"length_bars": 1, "length_beats": 4}),
    ("live_record_session", {"launch_quantization": "soon"}),
    ("live_record_arrangement", {"time": -4}),
    ("live_record_settings", {}),
    ("live_record_settings", {"midi_quantization": "1/64"}),
    ("live_record_settings", {"punch_start": 8, "punch_end": 2}),
    ("live_record_capture_midi", {"destination": "clipboard"}),
    ("live_record_resample", {}),
    ("live_record_resample", {"start": 0}),
    ("live_record_resample", {"start": 0, "end": 8, "bars": 2}),
    ("live_record_resample", {"start": -1, "bars": 2}),
    ("live_record_resample", {"start": 0, "bars": 0}),
    ("live_record_resample", {"start": 0, "bars": 2, "preroll": 30}),
    ("live_record_resample", {"action": "later"}),
])
def test_record_tools_validate_locally(fake_app, name, args):
    app, fake = fake_app
    assert call_tool(app, name, args)["type"] == "bad_args"
    assert not [r for r in fake.requests if r["cmd"].startswith("record.")]


def test_record_tools_end_to_end(tcp_bridge, song):
    song._exclusive_arm = False
    client = BridgeClient(host="127.0.0.1", port=tcp_bridge.port, timeout=5.0)
    try:
        app = create_app(client)
        armed = call_tool(app, "live_record_arm", {"track": ["Bass", "Drums"]})
        assert [t["name"] for t in armed["armed_tracks"]] == ["Bass", "Drums"]
        session = call_tool(app, "live_record_session", {"track": "Drums", "length_bars": 1})
        assert session["slot_index"] == 1 and session["record_length_beats"] == 4.0
        assert session["status"]["recording_slots"][0]["track"] == "Drums"
        status = call_tool(app, "live_record_status")
        assert status["recording_slots"][0]["track"] == "Drums"
        settings = call_tool(app, "live_record_settings", {"metronome": True})
        assert settings["metronome"] is True
        arrangement = call_tool(app, "live_record_arrangement", {"time": 4})
        assert arrangement["record_mode"] is True
        stopped = call_tool(app, "live_record_stop", {"stop_transport": True,
                                                      "disarm": True})
        assert stopped["record_mode"] is False and stopped["armed_tracks"] == []
        capture = call_tool(app, "live_record_capture_midi", {})
        assert capture["type"] == "invalid_state"
    finally:
        client.close()


def test_status_reports_input_levels_of_armed_tracks(bridge, multi_arm):
    """record.status tells whether an armed track's input receives signal (mixer meters)."""
    from live_stub_ext import mixer_meters
    song = multi_arm
    ext = mixer_meters.install(Live)
    try:
        ok(bridge, "record.arm", track=["Bass", "Vocals"])
        ext.set_meters(song.tracks[1], 0.0, 0.0, 0.0, input_level=0.4, input_left=0.42,
                       input_right=0.38)
        status = ok(bridge, "record.status")
        rows = {t["name"]: t for t in status["armed_tracks"]}
        assert rows["Vocals"]["input_level"] == {"level": 0.4, "left": 0.42, "right": 0.38}
        assert rows["Bass"]["input_level"]["level"] == 0.0
        # other record commands keep their compact armed_tracks rows
        assert "input_level" not in ok(bridge, "record.arm", track="Bass")["armed_tracks"][0]
    finally:
        ext.uninstall()
