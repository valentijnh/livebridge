"""Module C — arrangement: every arrangement.* command on the stub set and the arrangement
MCP tools (tools/arrangement.py) against the fake bridge."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parent
_REPO = _TESTS.parent
for _p in (str(_REPO / "mcp_server"), str(_TESTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from live_stub import factory  # noqa: E402


def run(bridge, cmd, **args):
    response = bridge.dispatch({"id": "t", "cmd": cmd, "args": args})
    assert response["ok"], response
    return response["result"]


def fail(bridge, cmd, **args):
    response = bridge.dispatch({"id": "t", "cmd": cmd, "args": args})
    assert not response["ok"], response
    return response["error"]


@pytest.fixture
def wav(tmp_path):
    path = tmp_path / "break.wav"
    path.write_bytes(b"RIFF0000WAVEfmt ")
    return str(path)


@pytest.fixture
def arranged(song):
    """Extra arrangement material: Bass 0-4 (fixture) + 8-16, Drums 4-8 and 16-20."""
    factory.add_arrangement_clip(song.tracks[0], start_time=8.0, length=8.0, name="Bass B",
                                 notes=[(38, 0.0, 1.0, 100)])
    factory.add_arrangement_clip(song.tracks[2], start_time=4.0, length=4.0, name="Beat A")
    factory.add_arrangement_clip(song.tracks[2], start_time=16.0, length=4.0, name="Beat B")
    return song


# --------------------------------------------------------------------------
# list / overview
# --------------------------------------------------------------------------

def test_list_all_and_one_track(bridge, arranged):
    result = run(bridge, "arrangement.list")
    assert result["count"] == 4
    names = [(c["track"], c["name"], c["index"]) for c in result["clips"]]
    assert names == [("Bass", "Bass Arr", 0), ("Bass", "Bass B", 1), ("Drums", "Beat A", 0),
                     ("Drums", "Beat B", 1)]
    bass_b = result["clips"][1]
    assert bass_b["start_time"] == 8 and bass_b["end_time"] == 16
    assert bass_b["start_bar"] == "3.1.1"
    assert bass_b["path"] == "song.tracks[0].arrangement_clips[1]"
    one = run(bridge, "arrangement.list", track="Drums", detail="summary")
    assert [c["name"] for c in one["clips"]] == ["Beat A", "Beat B"]
    assert "loop_start" in one["clips"][0]


def test_list_window_paging_and_take_lanes(bridge, arranged):
    window = run(bridge, "arrangement.list", start=3, end=5, unit="bars")  # beats 8..16
    assert [c["name"] for c in window["clips"]] == ["Bass B"]
    beats = run(bridge, "arrangement.list", start=2, end=5)
    assert [c["name"] for c in beats["clips"]] == ["Bass Arr", "Beat A"]
    paged = run(bridge, "arrangement.list", limit=3)
    assert paged["next_offset"] == 3
    lane = arranged.tracks[0].create_take_lane()
    lane.create_midi_clip(0.0, 4.0)
    lanes = run(bridge, "arrangement.list", track=0, include_take_lanes=True)
    assert lanes["count"] == 3 and lanes["clips"][-1]["take_lane"] == "Take 1"
    assert fail(bridge, "arrangement.list", unit="ticks")["type"] == "bad_args"
    assert fail(bridge, "arrangement.list", start=0, unit="bars")["type"] == "bad_args"


def test_overview_grid_loop_and_cues(bridge, arranged):
    arranged.loop = True
    arranged.loop_start = 8.0
    arranged.loop_length = 8.0
    result = run(bridge, "arrangement.overview")
    assert result["signature"] == "4/4" and result["beats_per_bar"] == 4
    assert result["window"] == [0, 20]
    assert result["loop"] == {"enabled": True, "start": 8, "end": 16}
    assert result["cue_points"] == [["Intro", 0], ["Drop", 32]]
    tracks = dict((t["name"], t) for t in result["tracks"])
    assert set(tracks) == {"Bass", "Drums"}
    assert tracks["Bass"]["lane"] == "#.##."
    assert tracks["Drums"]["lane"] == ".#..#"
    assert tracks["Drums"]["clips"] == [[4, 8, "Beat A"], [16, 20, "Beat B"]]
    assert result["bars_per_column"] == 1

    everything = run(bridge, "arrangement.overview", include_empty=True, grid=False)
    assert len(everything["tracks"]) == 3 and "lane" not in everything["tracks"][1]

    squeezed = run(bridge, "arrangement.overview", end=160, max_columns=8)
    assert squeezed["bars_per_column"] == 5 and len(squeezed["tracks"][0]["lane"]) == 8
    windowed = run(bridge, "arrangement.overview", start=3, end=5, unit="bars")
    assert windowed["window"] == [8, 16] and windowed["grid_starts_at_bar"] == 3
    assert [t["name"] for t in windowed["tracks"]] == ["Bass"]
    assert fail(bridge, "arrangement.overview", start=8, end=4)["type"] == "bad_args"


# --------------------------------------------------------------------------
# create
# --------------------------------------------------------------------------

def test_create_midi_clip(bridge, song):
    before = len(song._undo_steps)
    result = run(bridge, "arrangement.create_midi_clip", track="Bass", start=5, length=2,
                 unit="bars", name="Verse", color_index=3)
    assert len(song._undo_steps) == before + 1
    assert result["start_time"] == 16 and result["end_time"] == 24
    assert result["name"] == "Verse" and result["index"] == 1
    assert result["path"] == "song.tracks[0].arrangement_clips[1]"
    default = run(bridge, "arrangement.create_midi_clip", track="Drums", start=0)
    assert default["length"] == 4


def test_create_midi_clip_errors(bridge):
    assert fail(bridge, "arrangement.create_midi_clip", track="Vocals",
                start=0)["type"] == "invalid_state"
    assert fail(bridge, "arrangement.create_midi_clip", track="Bass", start=-1)["type"] == \
        "bad_args"
    assert fail(bridge, "arrangement.create_midi_clip", track="Bass", start=0,
                length=0)["type"] == "bad_args"
    assert fail(bridge, "arrangement.create_midi_clip", track="Bass",
                start=2000000)["type"] == "bad_args"


def test_create_audio_clip(bridge, song, wav):
    result = run(bridge, "arrangement.create_audio_clip", track="Vocals", file_path=wav,
                 start=3, unit="bars", name="Break")
    assert result["start_time"] == 8 and result["name"] == "Break"
    assert song.tracks[1].arrangement_clips[0].file_path == wav
    missing = fail(bridge, "arrangement.create_audio_clip", track="Vocals",
                   file_path=wav + ".nope")
    assert missing["type"] == "not_found"
    assert fail(bridge, "arrangement.create_audio_clip", track="Bass",
                file_path=wav)["type"] == "invalid_state"
    assert fail(bridge, "arrangement.create_audio_clip", track="Vocals",
                file_path="break.wav")["type"] == "bad_args"


def test_create_reports_unsupported_without_live12_api(bridge, song, monkeypatch):
    track_class = type(song.tracks[0])
    monkeypatch.delattr(track_class, "create_midi_clip")
    error = fail(bridge, "arrangement.create_midi_clip", track="Bass", start=0)
    assert error["type"] == "unsupported" and "Live 12" in error["message"]


# --------------------------------------------------------------------------
# duplicate / delete / move
# --------------------------------------------------------------------------

def test_duplicate_session_clip_into_arrangement(bridge, song):
    result = run(bridge, "arrangement.duplicate_clip", track="Bass", slot=0, time=9,
                 unit="bars")
    assert result["start_time"] == 32 and result["name"] == "Bass Loop"
    clip = song.tracks[0].arrangement_clips[1]
    assert len(clip.get_all_notes_extended()) == 4
    other = run(bridge, "arrangement.duplicate_clip", clip="Beat", time=0,
                target_track="Bass")
    assert other["track"] == "Bass" and other["start_time"] == 0
    mismatch = fail(bridge, "arrangement.duplicate_clip", track="Vocals", slot=0, time=0,
                    target_track="Bass")
    assert mismatch["type"] == "invalid_state"
    assert fail(bridge, "arrangement.duplicate_clip", track="Bass", slot=3,
                time=0)["type"] == "not_found"


def test_delete_by_index_time_and_path(bridge, arranged):
    by_time = run(bridge, "arrangement.delete_clip", track="Drums", at=5, unit="bars")
    assert by_time["name"] == "Beat B" and by_time["start_time"] == 16
    by_index = run(bridge, "arrangement.delete_clip", track="Bass", index=-1)
    assert by_index["name"] == "Bass B"
    by_path = run(bridge, "arrangement.delete_clip", clip="song.tracks[2].arrangement_clips[0]")
    assert by_path["name"] == "Beat A"
    by_name = run(bridge, "arrangement.delete_clip", clip="Bass Arr")
    assert by_name["deleted"] == "song.tracks[0].arrangement_clips[0]"
    assert fail(bridge, "arrangement.delete_clip", track="Bass", index=0)["type"] == "not_found"
    assert fail(bridge, "arrangement.delete_clip", track="Drums", at=100)["type"] == "not_found"
    assert fail(bridge, "arrangement.delete_clip", track="Drums")["type"] == "bad_args"
    session = fail(bridge, "arrangement.delete_clip", clip="song.tracks[0].clip_slots[0].clip")
    assert session["type"] == "bad_args"


def test_move_clip_in_time_and_to_other_track(bridge, arranged):
    moved = run(bridge, "arrangement.move_clip", track="Bass", index=1, start=32)
    assert moved["moved_from"] == 8 and moved["clip"]["start_time"] == 32
    bass = arranged.tracks[0].arrangement_clips
    assert [(c.name, c.start_time) for c in bass] == [("Bass Arr", 0.0), ("Bass B", 32.0)]
    assert len(bass[1].get_all_notes_extended()) == 1

    across = run(bridge, "arrangement.move_clip", clip="Beat A", start=2, unit="bars",
                 target_track="Bass")
    assert across["clip"]["track"] == "Bass" and across["clip"]["start_time"] == 4
    assert [c.name for c in arranged.tracks[2].arrangement_clips] == ["Beat B"]

    overlapping = run(bridge, "arrangement.move_clip", track="Bass", at=0, start=1)
    assert overlapping["clip"]["start_time"] == 1
    assert [c.start_time for c in arranged.tracks[0].arrangement_clips].count(0.0) == 0
    assert fail(bridge, "arrangement.move_clip", track="Bass", index=0,
                start=-4)["type"] == "bad_args"


# --------------------------------------------------------------------------
# loop / position / back_to_arranger
# --------------------------------------------------------------------------

def test_loop_read_and_write(bridge, song):
    read = run(bridge, "arrangement.loop")
    assert read == {"enabled": False, "start": 0, "end": 16, "length": 16,
                    "start_bar": "1.1.1", "end_bar": "5.1.1"}
    written = run(bridge, "arrangement.loop", enabled=True, start=9, end=17, unit="bars")
    assert written["start"] == 32 and written["end"] == 64 and written["enabled"] is True
    assert (song.loop, song.loop_start, song.loop_length) == (True, 32.0, 32.0)
    run(bridge, "arrangement.loop", length=2, unit="bars")
    assert song.loop_length == 8.0
    run(bridge, "arrangement.loop", start=4)
    assert song.loop_start == 4.0 and song.loop_length == 8.0
    assert fail(bridge, "arrangement.loop", end=2)["type"] == "bad_args"
    assert fail(bridge, "arrangement.loop", end=8, length=4)["type"] == "bad_args"
    assert fail(bridge, "arrangement.loop", length=0)["type"] == "bad_args"


def test_position_get_set_and_jump(bridge, song):
    read = run(bridge, "arrangement.position")
    assert read["time"] == 0 and read["bar_beat"] == "1.1.1"
    moved = run(bridge, "arrangement.position", time=17, unit="bars")
    assert moved["time"] == 64 and moved["bar_beat"] == "17.1.1"
    assert song.current_song_time == 64.0
    jumped = run(bridge, "arrangement.position", jump_by=-2.5)
    assert jumped["time"] == 61.5 and jumped["bar_beat"] == "16.2.3"
    run(bridge, "arrangement.position", jump_by=-1, unit="bars")
    assert song.current_song_time == 57.5
    assert fail(bridge, "arrangement.position", time=1, jump_by=1)["type"] == "bad_args"


def test_position_in_three_four(bridge, song):
    song.signature_numerator = 3
    result = run(bridge, "arrangement.position", time=3, unit="bars")
    assert result["time"] == 6 and result["bar_beat"] == "3.1.1"


def test_back_to_arranger(bridge, song):
    song.back_to_arranger = True
    result = run(bridge, "arrangement.back_to_arranger")
    assert result == {"was_overriding": True, "back_to_arranger": False}
    assert song.back_to_arranger is False
    song.tracks[0].back_to_arranger = True
    per_track = run(bridge, "arrangement.back_to_arranger", track="Bass")
    assert per_track["track"] == "Bass" and per_track["was_overriding"] is True
    assert song.tracks[0].back_to_arranger is False
    assert fail(bridge, "arrangement.back_to_arranger",
                track="master")["type"] == "bad_args"


# --------------------------------------------------------------------------
# MCP tools
# --------------------------------------------------------------------------

from fake_bridge import FakeBridge  # noqa: E402

from livebridge_mcp.client import BridgeClient  # noqa: E402
from livebridge_mcp.server import create_app  # noqa: E402

ARRANGEMENT_TOOLS = ["live_arrangement_overview", "live_arrangement_clips",
                     "live_arrangement_create_clip", "live_arrangement_duplicate_clip",
                     "live_arrangement_delete_clip", "live_arrangement_move_clip",
                     "live_arrangement_resize_clip", "live_arrangement_from_scenes",
                     "live_arrangement_copy_range"]


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
    return "\n".join(blocks), ([_p(b) for b in blocks] if len(blocks) > 1
                               else _p("\n".join(blocks)))


@pytest.fixture
def mcp():
    fake = FakeBridge().start()
    client = BridgeClient(host=fake.host, port=fake.port, timeout=5.0)
    app = create_app(client)
    try:
        yield app, fake
    finally:
        client.close()
        fake.stop()


def last(fake):
    return fake.requests[-1]


def test_arrangement_tools_registered(mcp):
    app, _fake = mcp
    tools = {t.name: t for t in asyncio.run(app.list_tools())}
    for name in ARRANGEMENT_TOOLS:
        assert name in tools, name
        assert len(tools[name].description or "") > 150, name
    assert "arrangement" in app.tool_modules
    assert "consolidat" in tools["live_arrangement_overview"].description.lower() or \
        "not part of the arrangement" in tools["live_arrangement_overview"].description


def test_tool_overview_and_clips(mcp):
    app, fake = mcp
    fake.set_result("arrangement.overview", {"tracks": []})
    call_tool(app, "live_arrangement_overview", {})
    assert last(fake)["args"] == {"unit": "beats", "grid": True, "max_columns": 64}
    fake.set_result("arrangement.list", {"clips": []})
    call_tool(app, "live_arrangement_clips", {"track": "Bass", "start": 9, "unit": "bars"})
    assert last(fake)["args"] == {"track": "Bass", "start": 9.0, "unit": "bars",
                                  "detail": "minimal", "offset": 0, "limit": 200}


def test_tool_create_routes_midi_and_audio(mcp):
    app, fake = mcp
    fake.set_result("arrangement.create_midi_clip", {"path": "p"})
    call_tool(app, "live_arrangement_create_clip", {"track": "Bass", "start": 17, "length": 4,
                                                    "unit": "bars", "name": "Chorus"})
    assert last(fake)["cmd"] == "arrangement.create_midi_clip"
    assert last(fake)["args"] == {"track": "Bass", "start": 17.0, "length": 4.0,
                                  "unit": "bars", "name": "Chorus"}
    fake.set_result("arrangement.create_audio_clip", {"path": "p"})
    call_tool(app, "live_arrangement_create_clip", {"track": "Vox", "start": 0,
                                                    "file_path": " /x/y.wav "})
    assert last(fake)["cmd"] == "arrangement.create_audio_clip"
    assert last(fake)["args"] == {"track": "Vox", "file_path": "/x/y.wav", "start": 0.0,
                                  "unit": "beats"}


def test_tool_duplicate_delete_move(mcp):
    app, fake = mcp
    fake.set_result("arrangement.duplicate_clip", {"path": "p"})
    call_tool(app, "live_arrangement_duplicate_clip", {"track": 0, "slot": "Intro", "time": 1,
                                                       "unit": "bars"})
    assert last(fake)["args"] == {"track": 0, "slot": "Intro", "time": 1.0, "unit": "bars"}
    fake.set_result("arrangement.delete_clip", {"deleted": "p"})
    call_tool(app, "live_arrangement_delete_clip", {"track": "Drums", "at": 16})
    assert last(fake)["args"] == {"track": "Drums", "at": 16.0, "unit": "beats"}
    call_tool(app, "live_arrangement_delete_clip", {"clip": "Beat A"})
    assert last(fake)["args"] == {"clip": "Beat A"}
    fake.set_result("arrangement.move_clip", {"moved_from": 0})
    call_tool(app, "live_arrangement_move_clip", {"track": 0, "index": 1, "start": 32,
                                                  "target_track": "Keys"})
    assert last(fake)["args"] == {"track": 0, "index": 1, "start": 32.0,
                                  "target_track": "Keys", "unit": "beats"}


def test_arrangement_tool_validation_stays_local(mcp):
    app, fake = mcp
    count = len(fake.requests)
    checks = [
        ("live_arrangement_overview", {"unit": "ticks"}),
        ("live_arrangement_overview", {"max_columns": 2}),
        ("live_arrangement_clips", {"start": 0, "unit": "bars"}),
        ("live_arrangement_clips", {"detail": "all"}),
        ("live_arrangement_create_clip", {"track": 0, "start": -1}),
        ("live_arrangement_create_clip", {"track": 0, "start": 0, "length": 0}),
        ("live_arrangement_create_clip", {"track": 0, "start": 0, "file_path": " "}),
        ("live_arrangement_create_clip", {"track": 0, "start": 0, "file_path": "/a.wav",
                                          "looping": True}),
        ("live_arrangement_duplicate_clip", {"time": 0}),
        ("live_arrangement_duplicate_clip", {"track": 0, "slot": 0, "time": -2}),
        ("live_arrangement_delete_clip", {"track": 0}),
        ("live_arrangement_delete_clip", {"track": 0, "index": 0, "at": 4}),
        ("live_arrangement_move_clip", {"start": 4}),
        ("live_arrangement_move_clip", {"track": 0, "index": 0, "start": 0, "unit": "bars"}),
    ]
    for name, args in checks:
        _t, data = call_tool(app, name, args)
        assert isinstance(data, dict) and data.get("type") == "bad_args", (name, args, data)
    assert len(fake.requests) == count


# --------------------------------------------------------------------------
# g2: fill / repeat / resize / scenes -> song / copy a range (real-Live behaviour
# from tests/live_stub_ext/arrangement_live.py)
# --------------------------------------------------------------------------

@pytest.fixture
def live_arrangement():
    import Live
    from live_stub_ext import arrangement_live
    uninstall = arrangement_live.install(Live)
    yield
    uninstall()


def spans(track):
    return [(c.name, c.start_time, c.end_time) for c in track.arrangement_clips]


def test_resize_clip_repeats_the_loop(bridge, song, live_arrangement):
    arr = song.tracks[0].arrangement_clips[0]           # "Bass Arr" 0..4, loop 0..4
    result = run(bridge, "arrangement.resize_clip", track="Bass", index=0, length=4,
                 unit="bars")
    assert result["length"] == 16 and "capped" not in result
    assert (arr.start_time, arr.end_time) == (0.0, 16.0)
    assert (arr.loop_start, arr.loop_end, arr.looping) == (0.0, 4.0, True)
    shorter = run(bridge, "arrangement.resize_clip", clip="Bass Arr", end=3)
    assert shorter["length"] == 3 and arr.end_time == 3.0 and arr.loop_end == 4.0
    factory.add_arrangement_clip(song.tracks[0], start_time=8.0, length=4.0, name="Next")
    capped = run(bridge, "arrangement.resize_clip", track="Bass", index=0, length=12)
    assert capped["capped"] is True and capped["length"] == 8 and "8" in capped["note"]
    unlooped = song.tracks[0].arrangement_clips[1]
    unlooped.looping = False
    run(bridge, "arrangement.resize_clip", track="Bass", index=1, length=2)
    assert unlooped.end_time == 10.0 and unlooped.looping is False
    assert fail(bridge, "arrangement.resize_clip", track="Bass", index=0)["type"] == "bad_args"
    assert fail(bridge, "arrangement.resize_clip", track="Bass", index=0, length=1,
                end=2)["type"] == "bad_args"
    assert fail(bridge, "arrangement.resize_clip", track="Bass", index=0,
                length=0)["type"] == "bad_args"


def test_resize_refuses_unwarped_audio(bridge, song, wav, live_arrangement):
    clip = song.tracks[1].create_audio_clip(wav, 0.0)
    clip.warping = False
    error = fail(bridge, "arrangement.resize_clip", track="Vocals", index=0, length=8)
    assert error["type"] == "unsupported" and "warp" in error["message"]


def test_duplicate_fills_a_length_with_one_looping_clip(bridge, song, live_arrangement):
    result = run(bridge, "arrangement.duplicate_clip", track="Bass", slot=0, time=5,
                 unit="bars", length=8)
    assert result["count"] == 1 and result["range"] == [16.0, 48.0]
    placed = song.tracks[0].arrangement_clips[-1]
    assert (placed.start_time, placed.end_time, placed.loop_end) == (16.0, 48.0, 4.0)
    assert len(placed.get_all_notes_extended()) == 4


def test_duplicate_count_and_non_looping_fill(bridge, song, live_arrangement):
    repeated = run(bridge, "arrangement.duplicate_clip", track="Bass", slot=0, time=16,
                   count=3)
    assert repeated["count"] == 3
    assert [c["start_time"] for c in repeated["clips"]] == [16.0, 20.0, 24.0]
    song.tracks[0].clip_slots[1].clip.looping = False    # "Bass Fill", 8 beats
    filled = run(bridge, "arrangement.duplicate_clip", track="Bass", slot=1, time=32,
                 length=20)
    assert filled["count"] == 3 and filled["range"] == [32.0, 52.0]
    assert [(c["start_time"], c["end_time"]) for c in filled["clips"]] == \
        [(32.0, 40.0), (40.0, 48.0), (48.0, 52.0)]
    assert fail(bridge, "arrangement.duplicate_clip", track="Bass", slot=0, time=0, count=2,
                length=4)["type"] == "bad_args"
    assert fail(bridge, "arrangement.duplicate_clip", track="Bass", slot=0, time=0,
                count=0)["type"] == "bad_args"


def test_duplicate_fill_replaces_clips_in_the_range(bridge, song, live_arrangement):
    factory.add_arrangement_clip(song.tracks[0], start_time=8.0, length=4.0, name="Old")
    result = run(bridge, "arrangement.duplicate_clip", track="Bass", slot=0, time=4,
                 length=12)
    assert "replaced 1" in result["note"]
    assert spans(song.tracks[0]) == [("Bass Arr", 0.0, 4.0), ("Bass Loop", 4.0, 16.0)]
    kept = run(bridge, "arrangement.duplicate_clip", track="Bass", slot=0, time=20,
               length=16, replace=False)
    assert kept["range"] == [20.0, 36.0]


def test_from_scenes_builds_sections(bridge, song, live_arrangement):
    song.tracks[0].arrangement_clips[0]  # the fixture's "Bass Arr" 0..4 is replaced
    result = run(bridge, "arrangement.from_scenes",
                 sections=[{"scene": "Intro", "bars": 2}, {"scene": "Verse"},
                           {"scene": 0, "bars": 1, "repeat": 2}])
    assert [(s["name"], s["start"], s["end"]) for s in result["sections"]] == \
        [("Intro", 0.0, 8.0), ("Verse", 8.0, 16.0), ("Intro", 16.0, 20.0),
         ("Intro", 20.0, 24.0)]
    assert result["sections"][0]["tracks"] == ["Bass", "Vocals", "Drums"]
    assert result["sections"][1]["tracks"] == ["Bass"]
    assert result["end_bar"] == "7.1.1" and result["placed"] == 10
    assert spans(song.tracks[0])[:2] == [("Bass Loop", 0.0, 8.0), ("Bass Fill", 8.0, 16.0)]
    assert spans(song.tracks[2])[0] == ("Beat", 0.0, 8.0)
    only = run(bridge, "arrangement.from_scenes", sections=["Intro"], start=33, unit="bars",
               tracks=["Drums"])
    assert only["start"] == 128.0 and only["sections"][0]["tracks"] == ["Drums"]
    empty = fail(bridge, "arrangement.from_scenes", sections=["Drop"])
    assert empty["type"] == "bad_args" and "length" in empty["message"]
    assert fail(bridge, "arrangement.from_scenes", sections=[])["type"] == "bad_args"
    assert fail(bridge, "arrangement.from_scenes",
                sections=[{"scene": 0, "beats": 4}])["type"] == "bad_args"
    assert fail(bridge, "arrangement.from_scenes", sections=["Nope"])["type"] == "not_found"


def test_copy_range_across_tracks(bridge, arranged, live_arrangement):
    # Bass 0-4 and 8-16, Drums 4-8 and 16-20
    result = run(bridge, "arrangement.copy_range", start=1, end=4, destination=9,
                 unit="bars")                               # beats 0..12 -> 32..44
    assert result["copied"] == 3 and result["destination"] == [32.0, 44.0]
    bass = spans(arranged.tracks[0])
    assert ("Bass Arr", 32.0, 36.0) in bass and ("Bass B", 40.0, 44.0) in bass  # cut at 12
    assert ("Beat A", 36.0, 40.0) in spans(arranged.tracks[2])
    skipped = run(bridge, "arrangement.copy_range", start=10, end=18, destination=64,
                  tracks=["Bass", "Drums"])
    assert skipped["skipped"][0]["name"] == "Bass B" and skipped["copied"] == 1
    assert fail(bridge, "arrangement.copy_range", start=0, end=8,
                destination=4)["type"] == "bad_args"
    assert fail(bridge, "arrangement.copy_range", start=8, end=4,
                destination=40)["type"] == "bad_args"


def test_move_keeps_timeline_length_and_envelopes(bridge, song, live_arrangement):
    run(bridge, "automation.write", track=0, slot=0, parameter="Filter Freq",
        points=[[0, 100], [2, 1000]])
    run(bridge, "arrangement.duplicate_clip", track="Bass", slot=0, time=16, length=16)
    moved = run(bridge, "arrangement.move_clip", track="Bass", at=16, start=64)
    clip = song.tracks[0].arrangement_clips[-1]
    assert moved["clip"]["start_time"] == 64 and clip.end_time == 80.0
    assert len(clip.automation_envelopes) == 1


def test_new_arrangement_tools(mcp):
    app, fake = mcp
    for cmd in ("arrangement.duplicate_clip", "arrangement.resize_clip",
                "arrangement.from_scenes", "arrangement.copy_range"):
        fake.set_result(cmd, {"ok": cmd})
    call_tool(app, "live_arrangement_duplicate_clip", {"track": 0, "slot": 0, "time": 5,
                                                       "unit": "bars", "length": 8,
                                                       "replace": False})
    assert last(fake)["args"] == {"track": 0, "slot": 0, "time": 5.0, "unit": "bars",
                                  "length": 8.0, "replace": False}
    call_tool(app, "live_arrangement_resize_clip", {"track": "Bass", "index": 0,
                                                    "length": "8.0.0"})
    assert last(fake)["args"] == {"track": "Bass", "index": 0, "length": "8.0.0",
                                  "unit": "beats"}
    sections = [{"scene": "Intro", "bars": 8}, "Verse"]
    call_tool(app, "live_arrangement_from_scenes", {"sections": sections, "start": 5,
                                                    "unit": "bars", "tracks": ["Bass"]})
    assert last(fake)["args"] == {"sections": sections, "start": 5.0, "unit": "bars",
                                  "tracks": ["Bass"]}
    call_tool(app, "live_arrangement_copy_range", {"start": "9.1.1", "end": "17.1.1",
                                                   "destination": "33.1.1"})
    assert last(fake)["args"] == {"start": "9.1.1", "end": "17.1.1",
                                  "destination": "33.1.1", "unit": "beats"}
    count = len(fake.requests)
    checks = [
        ("live_arrangement_duplicate_clip", {"track": 0, "slot": 0, "time": 0, "length": 0}),
        ("live_arrangement_duplicate_clip", {"track": 0, "slot": 0, "time": 0, "length": 4,
                                             "count": 2}),
        ("live_arrangement_duplicate_clip", {"track": 0, "slot": 0, "time": 0, "count": 0}),
        ("live_arrangement_resize_clip", {"track": 0, "index": 0}),
        ("live_arrangement_resize_clip", {"track": 0, "index": 0, "length": 4, "end": 8}),
        ("live_arrangement_resize_clip", {"length": 4}),
        ("live_arrangement_from_scenes", {"sections": []}),
        ("live_arrangement_from_scenes", {"sections": [{"bars": 4}]}),
        ("live_arrangement_from_scenes", {"sections": [{"scene": 0, "beats": 4}]}),
        ("live_arrangement_from_scenes", {"sections": [0], "start": 0, "unit": "bars"}),
        ("live_arrangement_copy_range", {"start": 0, "end": 4, "destination": -1}),
    ]
    for name, args in checks:
        _t, data = call_tool(app, name, args)
        assert isinstance(data, dict) and data.get("type") == "bad_args", (name, args, data)
    assert len(fake.requests) == count


# --------------------------------------------------------------------------
# cross fixer: clip envelopes are not reliably copied (Live 12.4.5)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("copy_envelopes", [True, False, "without_devices"])
def test_duplicate_reports_whether_envelopes_were_copied(bridge, song, copy_envelopes):
    """Live 12.4.5 copied the envelopes on a track without devices and dropped them all with
    Operator on the track: duplicate_clip / move_clip report {source, copied} and a note."""
    import Live
    from live_stub_ext import arrangement_live
    uninstall = arrangement_live.install(Live, copy_envelopes=copy_envelopes)
    try:
        run(bridge, "automation.write", track="Bass", slot=0, parameter="Volume",
            points=[[0, 0.3], [2, 0.9]])
        copied = copy_envelopes is True        # "Bass" holds Operator: the real rule drops them
        placed = run(bridge, "arrangement.duplicate_clip", track="Bass", slot=0, time=64)
        assert placed["envelopes"] == {"source": True, "copied": copied}
        assert ("note" in placed) is (not copied)
        if not copied:
            assert "did not copy the clip envelopes" in placed["note"]
        filled = run(bridge, "arrangement.duplicate_clip", track="Bass", slot=0, time=96,
                     count=2)
        assert filled["envelopes"] == {"source": True, "copied": copied}
        assert ("did not copy" in filled.get("note", "")) is (not copied)
        moved = run(bridge, "arrangement.move_clip", clip=placed["path"], start=128)
        assert moved["envelopes"]["copied"] is copied
        # a clip without envelopes: nothing to lose, no note
        plain = run(bridge, "arrangement.duplicate_clip", track="Bass", slot=1, time=160)
        assert plain["envelopes"] == {"source": False, "copied": False} and "note" not in plain
    finally:
        uninstall()


def test_envelopes_survive_on_a_track_without_devices(bridge, song):
    """The measured rule: an empty MIDI track keeps the copied envelopes."""
    import Live
    from live_stub_ext import arrangement_live
    uninstall = arrangement_live.install(Live, copy_envelopes="without_devices")
    try:
        track = run(bridge, "tracks.create", type="midi", name="Empty")
        run(bridge, "clips.create", track="Empty", slot=0, length=4)
        run(bridge, "automation.write", track="Empty", slot=0, parameter="Volume",
            points=[[0, 0.3], [2, 0.9]])
        placed = run(bridge, "arrangement.duplicate_clip", track="Empty", slot=0, time=8)
        assert placed["envelopes"] == {"source": True, "copied": True} and "note" not in placed
        assert track["name"] == "Empty"
    finally:
        uninstall()
