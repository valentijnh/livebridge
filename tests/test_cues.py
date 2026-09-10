"""Module F — cue points (locators) and song-time conversion: bridge commands on the stub song,
the MCP tools against the fake bridge, and end to end over TCP."""

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
from livebridge_mcp.tools import cues as cue_tools  # noqa: E402

from LiveBridge.dispatcher import Dispatcher  # noqa: E402
from LiveBridge.handlers import cues as cues_module  # noqa: E402
from LiveBridge.registry import BridgeError  # noqa: E402
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
    return {t.name for t in asyncio.run(app.list_tools())}


def cue_names(song):
    """Cues in time order (Live itself keeps ``song.cue_points`` in creation order)."""
    return sorted(((c.name, c.time) for c in song.cue_points), key=lambda row: row[1])


# --------------------------------------------------------------------------
# time helpers (both sides)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("to_bbs, to_beats", [
    (cues_module.beats_to_bbs, cues_module.bbs_to_beats),
    (cue_tools.beats_to_bbs, cue_tools.bbs_to_beats),
])
def test_bbs_conversion_both_sides(to_bbs, to_beats):
    assert to_bbs(0.0) == "1.1.1"
    assert to_bbs(8.0) == "3.1.1"
    assert to_bbs(9.75) == "3.2.4"
    assert to_bbs(16.0, is_length=True) == "4.0.0"
    assert to_bbs(3.0, 6, 8) == "2.1.1"          # a 6/8 bar = 3 quarter notes
    assert to_bbs(0.5, 6, 8) == "1.2.1"          # beats are eighth notes
    assert to_bbs(3.0, 3, 4) == "2.1.1"
    assert to_beats("3.1.1") == 8.0
    assert to_beats("3") == 8.0
    assert to_beats("3.2.4") == 9.75
    assert to_beats("4.0.0", is_length=True) == 16.0
    assert to_beats("2.1", 3, 4) == 3.0
    assert to_beats("1.0.2", 4, 4, True) == 4.5
    for bad in ("0.1.1", "1.5.1", "1.1.5", "x"):
        with pytest.raises(ValueError):
            to_beats(bad)
    with pytest.raises(ValueError):
        to_beats("1.4.0", is_length=True)
    for beats in (0.0, 0.25, 3.5, 17.75, 64.0):
        assert to_beats(to_bbs(beats)) == beats


def test_parse_helpers(song):
    parse = cues_module.parse_time
    assert parse(song, 5, "t") == 5.0
    assert parse(song, "2.1.1", "t") == 4.0
    with pytest.raises(BridgeError) as error:
        parse(song, -1, "t")
    assert error.value.type == "bad_args"
    with pytest.raises(BridgeError):
        parse(song, "9.9.9", "t")
    with pytest.raises(BridgeError):
        parse(song, True, "t")
    length = cues_module.parse_length
    assert length(song, "1/4", "p") == 1.0
    assert length(song, "1/16", "p") == 0.25
    assert abs(length(song, "1/8T", "p") - 1.0 / 3.0) < 1e-9
    assert length(song, "1/8D", "p") == 0.75
    assert length(song, "2 bars", "p") == 8.0
    assert length(song, "3 beats", "p") == 3.0
    assert length(song, "1.0.0", "p") == 4.0
    assert length(song, 0.5, "p") == 0.5
    with pytest.raises(BridgeError):
        length(song, 0, "p")
    assert cues_module.parse_signature("6/8") == (6, 8)
    assert cues_module.parse_signature([3, 4]) == (3, 4)
    with pytest.raises(BridgeError):
        cues_module.parse_signature("4/5")


# --------------------------------------------------------------------------
# bridge commands
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def next_tick_live(live_12_4_5):
    """Cue tests run against the deferred model: like Live 12.4.5, a playhead move is only
    visible on the next tick (``tick``), which happens between two requests."""
    live_12_4_5.defer(True)
    yield live_12_4_5
    live_12_4_5.defer(False)


def park(song, beats):
    """Put the playhead somewhere (and let Live apply it)."""
    song.current_song_time = beats
    transport_live.tick(song)


def run_live(bridge, song, cmd, **args):
    """Run a cue command the way a client does against Live 12.4.5: follow the
    pending/retry protocol, with a Live tick between requests (and after the last)."""
    result = run(bridge, cmd, **args)
    deleted, rounds = [], 0
    while result.get("pending"):
        rounds += 1
        assert rounds < 20, result
        assert result["retry_after_ms"] > 0 and result["reason"]
        deleted += result.get("deleted", [])
        transport_live.tick(song)
        result = run(bridge, result["retry"]["cmd"], **result["retry"]["args"])
    transport_live.tick(song)
    if deleted and "deleted" in result:
        result["deleted"] = deleted + result["deleted"]
    return result


def test_list(bridge, song):
    park(song, 12.0)
    result = run(bridge, "cues.list")
    assert result["count"] == 2 and result["signature"] == "4/4"
    assert result["cues"][0] == {"index": 0, "name": "Intro", "time": 0.0, "bbs": "1.1.1",
                                 "length": 32.0, "path": "song.cue_points[0]"}
    assert result["cues"][1]["bbs"] == "9.1.1" and result["cues"][1]["length"] is None
    position = result["position"]
    assert position["position"] == {"beats": 12.0, "bbs": "4.1.1"}
    assert position["section"] == "Intro" and position["previous"]["name"] == "Intro"
    assert position["next"]["name"] == "Drop" and position["until_next"] == 20.0
    assert position["since_section_start"] == 12.0 and position["at"] is None
    assert "position" not in run(bridge, "cues.list", include_position=False)


def test_position(bridge, song):
    park(song, 32.0)
    result = run(bridge, "cues.position")
    assert result["at"]["name"] == "Drop" and result["section"] == "Drop"
    assert result["next"] is None and "until_next" not in result
    run_live(bridge, song, "cues.delete", all=True)
    empty = run(bridge, "cues.position")
    assert empty["section"] is None and empty["previous"] is None


def test_add_at_time_is_two_requests_and_restores_playhead(bridge, song):
    park(song, 5.0)
    before = len(song._undo_steps)
    first = run(bridge, "cues.add", time="5.1.1", name="Verse")
    # Live 12.4.5: the playhead only moves on the next tick -> nothing created yet.
    assert first["pending"] is True and len(song.cue_points) == 2
    assert first["retry"] == {"cmd": "cues.add",
                              "args": {"time": 16.0, "restore": [5.0, 0.0], "name": "Verse"}}
    transport_live.tick(song)
    assert song.current_song_time == 16.0
    result = run(bridge, first["retry"]["cmd"], **first["retry"]["args"])
    assert result["created"] is True
    assert result["cue"] == {"index": 1, "name": "Verse", "time": 16.0, "bbs": "5.1.1"}
    assert result["count"] == 3
    transport_live.tick(song)
    assert song.current_song_time == 5.0                      # playhead put back
    assert cue_names(song) == [("Intro", 0.0), ("Verse", 16.0), ("Drop", 32.0)]
    assert len(song._undo_steps) == before + 2


def test_add_never_creates_at_the_old_playhead(bridge, song):
    # The bug found on real Live: toggling right after moving the playhead created the
    # cue at the *old* position.  Without the retry nothing may be created.
    park(song, 3.0)
    run(bridge, "cues.add", time=20, name="X")
    assert cue_names(song) == [("Intro", 0.0), ("Drop", 32.0)]


def test_add_snapped_by_the_arrangement_grid_zooms_in(bridge, song, app):
    # Real Live: a coarse Arrangement grid snaps the new locator (12.7 -> 12.0/16.0).  The
    # handler removes the snapped cue, zooms the Arrangement in and places it again.
    transport_live.set_grid(4.0)
    park(song, 1.0)
    result = run_live(bridge, song, "cues.add", time=13.5, name="Fine")
    assert result["created"] and result["cue"]["time"] == 13.5 and "snapped" not in result
    assert cue_names(song) == [("Intro", 0.0), ("Fine", 13.5), ("Drop", 32.0)]
    zooms = list(app.view.zoom_log)
    assert zooms.count(3) == zooms.count(2) > 0          # zoomed in, and back out
    assert transport_live.grid(song) == 4.0
    assert song.current_song_time == 1.0


def test_add_off_every_grid_keeps_the_closest(bridge, song):
    transport_live.set_grid(4.0)
    result = run_live(bridge, song, "cues.add", time=12.1, name="Odd")
    assert result["created"] and result["snapped"] is True   # finest grid: 1/256 beat
    assert result["requested"] == {"beats": 12.1, "bbs": "4.1.1"}
    assert abs(result["cue"]["time"] - 12.1) < 0.01
    assert [n for n, _t in cue_names(song)] == ["Intro", "Odd", "Drop"]


def test_add_never_deletes_a_cue_under_a_snapped_marker(bridge, song):
    # A coarse grid snaps the marker onto the existing "Drop" (32): toggling would delete it.
    transport_live.set_grid(32.0)
    result = run_live(bridge, song, "cues.add", time=30, name="Near")
    assert ("Drop", 32.0) in cue_names(song)
    assert result["cue"]["name"] == "Near"


def test_marker_differs_from_reported_playhead(bridge, song):
    # jump_by moves only the reported time in Live; the marker stays on the cue.
    park(song, 32.0)
    song.jump_by(1.0)
    transport_live.tick(song)
    assert song.current_song_time == 33.0 and song.is_cue_point_selected()
    result = run_live(bridge, song, "cues.add", time=33, name="After")
    assert result["created"] and result["cue"]["time"] == 33.0
    assert ("Drop", 32.0) in cue_names(song)


def test_delete_restores_playhead_and_start_marker(bridge, song):
    park(song, 3.0)
    song.start_time = 2.0
    result = run_live(bridge, song, "cues.delete", cue="Drop")
    assert result["deleted"][0]["name"] == "Drop"
    assert (song.current_song_time, song.start_time) == (3.0, 2.0)


def test_restore_a_playhead_behind_the_song_end(bridge, song):
    # Real Live 12.4.5 (stop_playback run): playback had carried the playhead to 2457 beats,
    # far behind the material; stopped, song_length shrank below it and putting the playhead
    # back after the delete raised "Cannot set the Songtime behind the Songlength" (the cue
    # was already gone, the start marker left on it).
    song._current_song_time = song.song_length + 200          # where playback stopped
    song.start_time = 4.0
    far = song.current_song_time
    brace = (song.loop_start, song.loop_length)
    result = run_live(bridge, song, "cues.delete", cue="Drop")
    assert result["deleted"][0]["name"] == "Drop"
    assert (song.current_song_time, song.start_time) == (far, 4.0)
    assert (song.loop_start, song.loop_length) == brace


def test_add_existing_renames_never_deletes(bridge, song):
    result = run(bridge, "cues.add", time=32, name="Big Drop")
    assert result["created"] is False and result["cue"]["name"] == "Big Drop"
    assert len(song.cue_points) == 2
    unnamed = run(bridge, "cues.add", time=32)
    assert unnamed["created"] is False and song.cue_points[1].name == "Big Drop"


def test_add_at_playhead(bridge, song):
    park(song, 8.0)
    result = run(bridge, "cues.add")
    assert result["created"] and result["cue"]["time"] == 8.0
    assert result["cue"]["name"] == "3"                      # Live numbers unnamed cues
    assert song.current_song_time == 8.0


def test_toggle_at_playhead_is_live_button(bridge, song):
    park(song, 32.0)
    removed = run(bridge, "cues.toggle")
    assert removed["action"] == "deleted" and removed["cue"]["name"] == "Drop"
    added = run(bridge, "cues.toggle")
    assert added["action"] == "added" and added["cue"]["time"] == 32.0


def test_add_while_playing(bridge, song):
    park(song, 8.0)
    song._is_playing = True
    at_playhead = run(bridge, "cues.add", name="Here")
    assert at_playhead["created"] and at_playhead["cue"]["time"] == 8.0
    error = fail(bridge, "cues.add", time=20)
    assert error["type"] == "invalid_state" and "stopped" in error["message"]


def test_add_after_song_end_extends_the_song(bridge, song):
    # Live 12.4.5 refuses a playhead behind song_length (measured: a cue at 40.1.1 with
    # song_length = 48 failed).  The loop brace is stretched until the time is reachable,
    # the cue is placed, and the brace goes back — the cue then holds the song length.
    park(song, 3.0)
    brace = (song.loop_start, song.loop_length)
    target = song.song_length + 100
    first = run(bridge, "cues.add", time=target, name="Outro")
    assert first["pending"] is True
    assert first["retry"]["args"]["restore"] == [3.0, 0.0, brace[0], brace[1]]
    assert song.song_length >= target                          # stretched brace
    transport_live.tick(song)
    result = run_live(bridge, song, first["retry"]["cmd"], **first["retry"]["args"])
    assert result["created"] is True and result["cue"]["time"] == target
    assert (song.loop_start, song.loop_length) == brace         # put back
    assert song.song_length >= target and song.current_song_time == 3.0
    assert ("Outro", target) in cue_names(song)
    moved = run_live(bridge, song, "cues.set", cue="Outro", time=song.song_length + 40)
    assert moved["created"] is True and (song.loop_start, song.loop_length) == brace
    error = fail(bridge, "cues.add", time=song.song_length + 10000)
    assert error["type"] == "bad_args" and "song_length" in error["message"]
    assert (song.loop_start, song.loop_length) == brace


def test_toggle(bridge, song):
    added = run_live(bridge, song, "cues.toggle", time=24)
    assert added["action"] == "added" and added["cue"]["time"] == 24.0
    deleted = run_live(bridge, song, "cues.toggle", time=24)
    assert deleted["action"] == "deleted" and deleted["count"] == 2
    park(song, 0.0)
    removed = run(bridge, "cues.toggle")
    assert removed["action"] == "deleted" and removed["cue"]["name"] == "Intro"


def test_delete(bridge, song):
    park(song, 3.0)
    result = run_live(bridge, song, "cues.delete", cue="drop")
    assert result["deleted"][0]["name"] == "Drop" and result["count"] == 1
    assert song.current_song_time == 3.0
    run_live(bridge, song, "cues.add", time=16, name="A")
    run_live(bridge, song, "cues.delete", cue="@16")
    assert cue_names(song) == [("Intro", 0.0)]
    run_live(bridge, song, "cues.delete", cue=0)
    assert len(song.cue_points) == 0
    assert fail(bridge, "cues.delete", cue=0)["type"] == "not_found"
    assert fail(bridge, "cues.delete")["type"] == "bad_args"
    assert fail(bridge, "cues.delete", cue=0, all=True)["type"] == "bad_args"


def test_delete_at_playhead_is_immediate(bridge, song):
    park(song, 32.0)
    result = run(bridge, "cues.delete", cue="Drop")
    assert "pending" not in result and result["deleted"][0]["name"] == "Drop"


def test_delete_all(bridge, song):
    run_live(bridge, song, "cues.add", time=8, name="B")
    park(song, 8.0)
    first = run(bridge, "cues.delete", all=True)
    assert first["pending"] and [c["name"] for c in first["deleted"]] == ["B"]
    result = run_live(bridge, song, "cues.delete", all=True)
    assert sorted(c["name"] for c in result["deleted"]) == ["Drop", "Intro"]
    assert result["count"] == 0 and len(song.cue_points) == 0
    assert song.current_song_time == 8.0


def test_resolve_cue_variants(bridge, song):
    run_live(bridge, song, "cues.add", time=16, name="Verse")
    run_live(bridge, song, "cues.add", time=48, name="Verse")   # duplicate names: earliest
    assert run(bridge, "cues.jump", cue="Verse")["target"]["time"] == 16.0
    assert run(bridge, "cues.jump", cue="VER")["target"]["time"] == 16.0
    assert run(bridge, "cues.jump", cue=-1)["target"]["time"] == 48.0
    assert run(bridge, "cues.jump", cue="3")["target"]["time"] == 48.0
    # a LOM path uses Live's raw (creation) order: Intro, Drop, Verse@16, Verse@48
    assert run(bridge, "cues.jump", cue="song.cue_points[1]")["target"]["name"] == "Drop"
    assert run(bridge, "cues.jump", cue="song.cue_points[3]")["target"]["time"] == 48.0
    assert run(bridge, "cues.jump", cue="@9.1.1")["target"]["name"] == "Drop"
    assert fail(bridge, "cues.jump", cue="@7")["type"] == "not_found"
    assert fail(bridge, "cues.jump", cue="Outro")["type"] == "not_found"
    assert fail(bridge, "cues.jump", cue=9)["type"] == "not_found"
    assert fail(bridge, "cues.jump", cue="song.tracks[0]")["type"] == "bad_args"


def test_set_rename_and_move(bridge, song):
    renamed = run(bridge, "cues.set", cue="Drop", name="Break")
    assert renamed["renamed"] and not renamed["moved"]
    assert song.cue_points[1].name == "Break"
    park(song, 2.0)
    step = run(bridge, "cues.set", cue="Break", time="13.1.1")
    assert step["pending"] and step["retry"]["cmd"] == "cues.set"
    transport_live.tick(song)
    step = run(bridge, step["retry"]["cmd"], **step["retry"]["args"])
    # old cue deleted at its time, re-created at the new one on the next round
    assert step["pending"] and step["retry"]["cmd"] == "cues.add"
    assert step["moved_from"] == {"name": "Break", "time": 32.0, "bbs": "9.1.1"}
    assert cue_names(song) == [("Intro", 0.0)]
    transport_live.tick(song)
    moved = run(bridge, step["retry"]["cmd"], **step["retry"]["args"])
    assert moved["created"] and moved["cue"] == {"index": 1, "name": "Break", "time": 48.0,
                                                 "bbs": "13.1.1"}
    transport_live.tick(song)
    assert cue_names(song) == [("Intro", 0.0), ("Break", 48.0)]
    assert song.current_song_time == 2.0
    both = run_live(bridge, song, "cues.set", cue=0, name="Start", time=4)
    assert both["cue"]["name"] == "Start"
    assert cue_names(song) == [("Start", 4.0), ("Break", 48.0)]
    merged = run_live(bridge, song, "cues.set", cue="Start", time=48, name="Big")
    assert merged["merged"] and merged["moved"] and cue_names(song) == [("Big", 48.0)]
    assert fail(bridge, "cues.set", cue=0)["type"] == "bad_args"
    assert fail(bridge, "cues.set", cue=0, name=5)["type"] == "bad_args"


def test_jump_directions(bridge, song):
    park(song, 10.0)
    nxt = run(bridge, "cues.jump", direction="next")
    assert nxt["jumped"] and nxt["target"]["name"] == "Drop"
    # Live moves the playhead on its next tick; the result reports the target
    assert nxt["position"]["beats"] == 32.0 and nxt["quantized"] is False
    assert song.current_song_time == 10.0
    transport_live.tick(song)
    assert song.current_song_time == 32.0
    none_after = run(bridge, "cues.jump", direction="next")
    assert none_after["jumped"] is False and "after" in none_after["reason"]
    prev = run(bridge, "cues.jump", direction="prev")
    transport_live.tick(song)
    assert prev["target"]["name"] == "Intro" and song.current_song_time == 0.0
    assert run(bridge, "cues.jump", direction="last")["target"]["name"] == "Drop"
    transport_live.tick(song)
    assert run(bridge, "cues.jump", direction="first")["position"]["beats"] == 0.0
    assert fail(bridge, "cues.jump", direction="sideways")["type"] == "bad_args"
    assert fail(bridge, "cues.jump")["type"] == "bad_args"
    assert fail(bridge, "cues.jump", cue=0, direction="next")["type"] == "bad_args"
    before = len(song._undo_steps)
    run(bridge, "cues.jump", cue="Drop")
    assert len(song._undo_steps) == before  # jumping is not an undo step
    transport_live.tick(song)
    run_live(bridge, song, "cues.delete", all=True)
    assert run(bridge, "cues.jump", direction="first")["jumped"] is False


def test_jump_while_playing_is_quantized(bridge, song):
    park(song, 4.0)
    song._is_playing = True
    result = run(bridge, "cues.jump", cue="Drop")
    assert result["quantized"] is True and result["target"]["time"] == 32.0
    assert result["position"]["beats"] == 4.0


def test_loop_between_cues(bridge, song):
    run_live(bridge, song, "cues.add", time=16, name="Verse")
    result = run(bridge, "cues.loop", start="Intro")
    assert result["loop"]["on"] is True                      # requested (Live: next tick)
    assert result["loop"]["start"] == 0.0 and result["loop"]["end"] == 16.0
    assert result["to"]["name"] == "Verse" and result["loop"]["length_bbs"] == "4.0.0"
    transport_live.tick(song)
    assert (song.loop, song.loop_start, song.loop_length) == (True, 0.0, 16.0)
    park(song, 2.0)
    explicit = run(bridge, "cues.loop", start="Verse", end="Drop", jump=True, enable=False)
    assert explicit["loop"]["start_bbs"] == "5.1.1" and explicit["loop"]["end_bbs"] == "9.1.1"
    transport_live.tick(song)
    assert song.current_song_time == 16.0
    assert fail(bridge, "cues.loop", start="Drop")["type"] == "invalid_state"
    assert fail(bridge, "cues.loop", start="Drop", end="Intro")["type"] == "bad_args"


def test_convert_time(bridge, song):
    song.tempo = 120.0
    result = run(bridge, "cues.convert_time", beats=[0, 8, 9.75], bbs="5.1.1")
    assert result["signature"] == "4/4" and result["beats_per_bar"] == 4.0
    rows = result["results"]
    assert [r["bbs"] for r in rows] == ["1.1.1", "3.1.1", "3.2.4", "5.1.1"]
    assert rows[1]["seconds"] == 4.0 and rows[3]["beats"] == 16.0 and rows[1]["bars"] == 2.0
    lengths = run(bridge, "cues.convert_time", bbs=["2.0.0"], is_length=True, signature="3/4")
    assert lengths["results"][0]["beats"] == 6.0 and lengths["signature"] == "3/4"
    assert fail(bridge, "cues.convert_time")["type"] == "bad_args"
    assert fail(bridge, "cues.convert_time", beats=-1)["type"] == "bad_args"
    assert fail(bridge, "cues.convert_time", bbs="0.0.0")["type"] == "bad_args"
    assert fail(bridge, "cues.convert_time", beats="x")["type"] == "bad_args"


def test_index_is_time_order_even_though_live_keeps_creation_order(bridge, song):
    run_live(bridge, song, "cues.add", time=16, name="Verse")
    assert [c.name for c in song.cue_points] == ["Intro", "Drop", "Verse"]   # Live's order
    listing = run(bridge, "cues.list")
    assert [(c["index"], c["name"], c["path"]) for c in listing["cues"]] == [
        (0, "Intro", "song.cue_points[0]"), (1, "Verse", "song.cue_points[2]"),
        (2, "Drop", "song.cue_points[1]")]
    assert listing["cues"][1]["length"] == 16.0
    assert run(bridge, "cues.jump", cue=1)["target"]["name"] == "Verse"
    assert run(bridge, "cues.jump", direction="last")["target"]["name"] == "Drop"
    park(song, 20.0)
    where = run(bridge, "cues.position")
    assert where["previous"]["name"] == "Verse" and where["next"]["name"] == "Drop"
    snapshot = run(bridge, "song.snapshot", detail="minimal")
    assert [c["name"] for c in snapshot["cue_points"]] == ["Intro", "Verse", "Drop"]


def test_signature_aware(bridge, song):
    song.signature_numerator, song.signature_denominator = 3, 4
    result = run_live(bridge, song, "cues.add", time="3.1.1", name="Bar 3")
    assert result["cue"]["time"] == 6.0
    listing = run(bridge, "cues.list")
    assert listing["signature"] == "3/4" and listing["cues"][2]["bbs"] == "11.3.1"


def test_layout_places_a_whole_song_structure(bridge, song):
    park(song, 5.0)
    song.start_time = 4.0
    sections = [{"name": "Intro", "bar": 1}, {"name": "Verse", "bar": 9},
                {"name": "Chorus", "time": "17.1.1"}, [96, "Bridge"], {"name": "Outro",
                                                                        "bar": 41}]
    result = run_live(bridge, song, "cues.layout", cues=sections)
    assert [(c["name"], c["time"]) for c in result["cues"]] == [
        ("Intro", 0.0), ("Verse", 32.0), ("Chorus", 64.0), ("Bridge", 96.0), ("Outro", 160.0)]
    # "Intro" existed at 0 and the "Drop" at 32 was renamed to "Verse" — never deleted
    assert [r["name"] for r in result["created"]] == ["Chorus", "Bridge", "Outro"]
    assert [r["name"] for r in result["renamed"]] == ["Verse"]
    assert result["deleted"] == [] and result["count"] == 5
    assert (song.current_song_time, song.start_time) == (5.0, 4.0)
    assert (song.loop_start, song.loop_length) == (0.0, 16.0)     # Outro was behind the end
    assert song.song_length >= 160.0


def test_layout_is_one_round_per_cue_and_replace_deletes_the_rest(bridge, song):
    rounds = []
    result = run(bridge, "cues.layout", cues=[[8, "A"], [24, "B"]], replace=True)
    while result.get("pending"):
        rounds.append(result["left"])
        assert result["retry"]["cmd"] == "cues.layout" and "deleted" not in result
        transport_live.tick(song)
        result = run(bridge, result["retry"]["cmd"], **result["retry"]["args"])
    transport_live.tick(song)
    assert cue_names(song) == [("A", 8.0), ("B", 24.0)]
    assert sorted(r["name"] for r in result["deleted"]) == ["Drop", "Intro"]
    # "Intro" sits under the playhead (deleted at once); Drop, A and B take a round each
    assert rounds == [3, 2, 1]


def test_layout_handles_the_arrangement_grid(bridge, song, app):
    transport_live.set_grid(4.0)
    result = run_live(bridge, song, "cues.layout", cues=[[13.5, "Fine"], [12.1, "Odd"]])
    assert ("Fine", 13.5) in cue_names(song)
    assert result["snapped"][0]["requested"] == "4.1.1"          # 12.1 is off every grid
    assert abs(result["snapped"][0]["time"] - 12.1) < 0.01
    assert [c["name"] for c in result["cues"]] == ["Odd", "Fine"]
    again = run_live(bridge, song, "cues.layout", cues=[[13.5, "Fine"]], replace=True)
    assert [c["name"] for c in again["deleted"]] == ["Intro", "Odd", "Drop"]


def test_layout_validates_before_writing(bridge, song):
    before = cue_names(song)
    for bad in ([], [{"name": "x"}], [{"name": "x", "time": 4, "bar": 2}], [{"bar": 0}],
                [[4, "a"], [4, "b"]], [{"name": 5, "time": 4}], ["Verse"],
                [{"name": "x", "time": 4, "colour": 1}]):
        assert fail(bridge, "cues.layout", cues=bad)["type"] == "bad_args", bad
    assert fail(bridge, "cues.layout", cues=[[0, "Start"]], replace="yes")["type"] == "bad_args"
    too_far = fail(bridge, "cues.layout", cues=[[0, "Start"], [song.song_length + 9999, "X"]])
    assert too_far["type"] == "bad_args"
    song._is_playing = True
    playing = fail(bridge, "cues.layout", cues=[[0, "Start"], [20, "New"]])
    assert playing["type"] == "invalid_state" and "stop_playback" in playing["message"]
    assert cue_names(song) == before                         # not even the rename happened
    renamed = run(bridge, "cues.layout", cues=[[0, "Start"]])  # renames work while playing
    assert renamed["renamed"][0]["name"] == "Start" and "pending" not in renamed


def test_every_cue_command_is_registered(bridge):
    listing = run(bridge, "system.commands", namespace="cues")
    names = {c["cmd"] for c in listing["commands"]}
    assert names == {"cues.list", "cues.position", "cues.add", "cues.toggle", "cues.delete",
                     "cues.set", "cues.jump", "cues.loop", "cues.convert_time", "cues.layout"}
    for entry in listing["commands"]:
        assert entry["doc"]
    mutating = {c["cmd"] for c in listing["commands"] if c["mutating"]}
    assert mutating == {"cues.add", "cues.toggle", "cues.delete", "cues.set", "cues.loop",
                        "cues.layout"}


def test_create_cue_detects_live_refusal(bridge, song, monkeypatch):
    # Live answers set_or_delete_cue with nothing — must not be reported as success.
    monkeypatch.setattr(type(song), "set_or_delete_cue", lambda self: None)
    park(song, 16.0)
    error = fail(bridge, "cues.add", time=16)
    assert error["type"] == "invalid_state"
    park(song, 32.0)
    assert fail(bridge, "cues.delete", cue="Drop")["type"] == "invalid_state"


# --------------------------------------------------------------------------
# MCP tools — fake bridge
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


CUE_TOOLS = {"live_cue_list", "live_cue_add", "live_cue_delete", "live_cue_set",
             "live_cue_jump", "live_cue_loop", "live_time_convert", "live_cue_layout"}


def test_cue_tools_are_registered(fake_app):
    _fake, app = fake_app
    assert "cues" in app.tool_modules
    assert CUE_TOOLS <= tool_names(app)


def test_cue_tools_forward_arguments(fake_app):
    fake, app = fake_app
    for cmd in ("cues.list", "cues.position", "cues.add", "cues.toggle", "cues.delete",
                "cues.set", "cues.jump", "cues.loop", "cues.convert_time"):
        fake.set_result(cmd, {"cmd": cmd})

    def last():
        return fake.requests[-1]["cmd"], fake.requests[-1].get("args", {})

    assert call_tool(app, "live_cue_list") == {"cmd": "cues.list"}
    assert last() == ("cues.list", {"include_position": True})
    call_tool(app, "live_cue_list", {"position_only": True})
    assert last() == ("cues.position", {})
    call_tool(app, "live_cue_add", {"time": "17.1.1", "name": "Chorus"})
    assert last() == ("cues.add", {"time": "17.1.1", "name": "Chorus"})
    call_tool(app, "live_cue_add", {})
    assert last() == ("cues.add", {})
    call_tool(app, "live_cue_add", {"time": 8, "toggle": True})
    assert last() == ("cues.toggle", {"time": 8.0})
    call_tool(app, "live_cue_delete", {"cue": "Drop"})
    assert last() == ("cues.delete", {"cue": "Drop"})
    call_tool(app, "live_cue_delete", {"all": True})
    assert last() == ("cues.delete", {"all": True})
    call_tool(app, "live_cue_set", {"cue": 1, "name": "Break", "time": 48})
    assert last() == ("cues.set", {"cue": 1, "name": "Break", "time": 48.0})
    call_tool(app, "live_cue_jump", {"direction": "next"})
    assert last() == ("cues.jump", {"direction": "next"})
    call_tool(app, "live_cue_jump", {"cue": "@32"})
    assert last() == ("cues.jump", {"cue": "@32"})
    call_tool(app, "live_cue_loop", {"start": "Verse", "jump": True})
    assert last() == ("cues.loop", {"start": "Verse", "enable": True, "jump": True})
    call_tool(app, "live_time_convert", {"beats": [8, 16], "is_length": True})
    assert last() == ("cues.convert_time", {"beats": [8.0, 16.0], "is_length": True})
    fake.set_result("cues.layout", {"cues": [], "created": []})
    call_tool(app, "live_cue_layout", {"cues": [{"name": "Verse", "bar": 9}, [64, "Drop"]],
                                       "replace": True})
    assert last() == ("cues.layout", {"cues": [{"name": "Verse", "bar": 9}, [64, "Drop"]],
                                      "replace": True})


def test_cue_tools_validate_locally(fake_app):
    fake, app = fake_app
    count = len(fake.requests)
    bad = [
        ("live_cue_add", {"time": -1}),
        ("live_cue_add", {"time": "bar nine"}),
        ("live_cue_add", {"toggle": True, "name": "x"}),
        ("live_cue_delete", {}),
        ("live_cue_delete", {"cue": 1, "all": True}),
        ("live_cue_delete", {"cue": "  "}),
        ("live_cue_set", {"cue": 0}),
        ("live_cue_set", {"cue": 0, "time": -2}),
        ("live_cue_jump", {}),
        ("live_cue_jump", {"cue": 1, "direction": "next"}),
        ("live_cue_jump", {"direction": "up"}),
        ("live_cue_loop", {"start": ""}),
        ("live_cue_layout", {"cues": []}),
        ("live_cue_layout", {"cues": [{"name": "x", "time": -4}]}),
        ("live_cue_layout", {"cues": [[4, "a", "b"]]}),
        ("live_cue_layout", {"cues": [[i, str(i)] for i in range(65)]}),
        ("live_time_convert", {}),
        ("live_time_convert", {"beats": -4}),
        ("live_time_convert", {"bbs": "0.1.1", "signature": "4/4"}),
        ("live_time_convert", {"beats": 4, "signature": "4-4"}),
        ("live_time_convert", {"beats": 4, "signature": "4/4", "tempo": 5}),
    ]
    for name, args in bad:
        result = call_tool(app, name, args)
        assert isinstance(result, dict) and result.get("type") == "bad_args", (name, result)
    assert len(fake.requests) == count  # nothing reached the bridge


def test_time_convert_offline(fake_app):
    fake, app = fake_app
    count = len(fake.requests)
    result = call_tool(app, "live_time_convert", {"beats": [0, 3, 7.5], "bbs": "3.2",
                                                  "signature": "6/8", "tempo": 90})
    assert len(fake.requests) == count
    assert result["signature"] == "6/8" and result["beats_per_bar"] == 3.0
    rows = result["results"]
    assert [r["bbs"] for r in rows] == ["1.1.1", "2.1.1", "3.4.1", "3.2.1"]
    assert rows[3]["beats"] == 6.5 and rows[1]["seconds"] == 2.0 and rows[1]["bars"] == 1.0


def test_cue_tools_follow_the_pending_protocol(fake_app):
    fake, app = fake_app
    fake.set_result("cues.delete", {"pending": True, "retry_after_ms": 10,
                                    "retry": {"cmd": "cues.toggle", "args": {"time": 8.0}},
                                    "deleted": [{"name": "A"}], "count": 1})
    fake.set_result("cues.toggle", {"deleted": [{"name": "B"}], "count": 0})
    result = call_tool(app, "live_cue_delete", {"all": True})
    assert [r["name"] for r in result["deleted"]] == ["A", "B"] and result["count"] == 0
    assert [r["cmd"] for r in fake.requests[-2:]] == ["cues.delete", "cues.toggle"]
    assert fake.requests[-1]["args"] == {"time": 8.0}
    fake.set_result("cues.set", {"pending": True, "retry_after_ms": 10,
                                 "retry": {"cmd": "cues.add",
                                           "args": {"time": 48.0, "name": "Break"}},
                                 "moved_from": {"name": "Drop", "time": 32.0, "bbs": "9.1.1"}})
    fake.set_result("cues.add", {"created": True, "count": 2,
                                 "cue": {"index": 1, "name": "Break", "time": 48.0}})
    moved = call_tool(app, "live_cue_set", {"cue": "Drop", "name": "Break", "time": 48})
    assert moved == {"cue": {"index": 1, "name": "Break", "time": 48.0}, "moved": True,
                     "moved_from": {"name": "Drop", "time": 32.0, "bbs": "9.1.1"},
                     "renamed": True, "count": 2}
    fake.set_result("cues.add", {"pending": True, "retry_after_ms": 10,
                                 "retry": {"cmd": "system.reload", "args": {}}})
    odd = call_tool(app, "live_cue_add", {"time": 4})
    assert odd["type"] == "internal" and "unexpected retry" in odd["error"]


def test_cue_tools_stop_and_resume_playback(fake_app, monkeypatch):
    # stop_playback: a running transport is stopped, the operation runs, playback continues.
    monkeypatch.setattr(cue_tools, "_TICK_WAIT", 0.0)
    fake, app = fake_app
    fake.set_result("cues.position", {"is_playing": True})
    fake.set_result("transport.stop", {"is_playing": False})
    fake.set_result("transport.continue", {"is_playing": True})
    fake.set_result("cues.layout", {"cues": [], "created": [], "count": 2})
    count = len(fake.requests)
    result = call_tool(app, "live_cue_layout", {"cues": [[8, "A"]], "stop_playback": True})
    assert result["playback"] == "resumed"
    assert [r["cmd"] for r in fake.requests[count:]] == [
        "cues.position", "transport.stop", "cues.layout", "transport.continue"]
    fake.set_result("cues.position", {"is_playing": False})
    fake.set_result("cues.add", {"created": True, "count": 3})
    count = len(fake.requests)
    stopped = call_tool(app, "live_cue_add", {"time": 8, "stop_playback": True})
    assert "playback" not in stopped
    assert [r["cmd"] for r in fake.requests[count:]] == ["cues.position", "cues.add"]
    count = len(fake.requests)
    fake.set_result("cues.set", {"cue": {}, "moved": False, "renamed": True})
    call_tool(app, "live_cue_set", {"cue": 0, "name": "X", "stop_playback": True})
    assert [r["cmd"] for r in fake.requests[count:]] == ["cues.set"]   # a rename needs no stop


def test_cue_tool_reports_bridge_errors(fake_app):
    fake, app = fake_app
    fake.set_error("cues.jump", "not_found", "no cue point named 'Outro'")
    result = call_tool(app, "live_cue_jump", {"cue": "Outro"})
    assert result["type"] == "not_found" and "Outro" in result["error"]


# --------------------------------------------------------------------------
# MCP tools — end to end through the real TCP server and the stub song
# --------------------------------------------------------------------------

def test_cue_tools_end_to_end(tcp_bridge, song, monkeypatch):
    # Live ticks between two requests: apply the deferred playhead before each command.
    original = Dispatcher.execute

    def execute(self, request):
        transport_live.tick(song)
        return original(self, request)

    monkeypatch.setattr(Dispatcher, "execute", execute)
    client = BridgeClient(host="127.0.0.1", port=tcp_bridge.port, timeout=5.0)
    try:
        app = create_app(client)
        added = call_tool(app, "live_cue_add", {"time": "5.1.1", "name": "Verse"})
        assert added["created"] is True and added["cue"]["index"] == 1
        listing = call_tool(app, "live_cue_list")
        assert [c["name"] for c in listing["cues"]] == ["Intro", "Verse", "Drop"]
        loop = call_tool(app, "live_cue_loop", {"start": "Verse"})
        assert loop["loop"]["start"] == 16.0 and loop["loop"]["end"] == 32.0
        jump = call_tool(app, "live_cue_jump", {"cue": "Verse"})
        assert jump["position"]["bbs"] == "5.1.1"
        where = call_tool(app, "live_cue_list", {"position_only": True})
        assert where["section"] == "Verse"
        moved = call_tool(app, "live_cue_set", {"cue": "Verse", "time": 20})
        assert moved["cue"]["time"] == 20.0 and moved["moved"] is True
        assert moved["moved_from"]["time"] == 16.0 and moved["renamed"] is False
        converted = call_tool(app, "live_time_convert", {"bbs": ["9.1.1"]})
        assert converted["results"][0]["beats"] == 32.0
        layout = call_tool(app, "live_cue_layout", {
            "cues": [{"name": "Intro", "bar": 1}, {"name": "Verse", "bar": 5},
                     {"name": "End", "bar": 60}], "replace": True})
        assert [c["name"] for c in layout["cues"]] == ["Intro", "Verse", "End"]
        assert layout["count"] == 3 and layout["cues"][2]["time"] == 236.0
        deleted = call_tool(app, "live_cue_delete", {"all": True})
        assert deleted["count"] == 0
        missing = call_tool(app, "live_cue_jump", {"cue": "Nope"})
        assert missing["type"] == "not_found"
    finally:
        client.close()
