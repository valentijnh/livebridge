"""Module C — clips: bridge commands (clips.*) on the stub set and the MCP tools
(tools/clips.py) against the fake bridge."""

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


@pytest.fixture
def wav(tmp_path):
    path = tmp_path / "loop.wav"
    path.write_bytes(b"RIFF0000WAVEfmt ")
    return str(path)


# --------------------------------------------------------------------------
# clips.list / clips.get
# --------------------------------------------------------------------------

def test_list_whole_set_and_one_track(bridge):
    result = run(bridge, "clips.list")
    assert result["count"] == 4
    names = [c["name"] for c in result["clips"]]
    assert names == ["Bass Loop", "Bass Fill", "Vox Take", "Beat"]
    first = result["clips"][0]
    assert first["path"] == "song.tracks[0].clip_slots[0].clip"
    assert first["track"] == "Bass" and first["slot"] == 0
    assert "kind" not in first and "is_audio" not in first

    one = run(bridge, "clips.list", track="Drums")
    assert [c["name"] for c in one["clips"]] == ["Beat"]


def test_list_arrangement_paging_detail_and_playing(bridge, song):
    result = run(bridge, "clips.list", track=0, include_arrangement=True)
    assert [c["name"] for c in result["clips"]] == ["Bass Loop", "Bass Fill", "Bass Arr"]
    arr = result["clips"][2]
    assert arr["path"] == "song.tracks[0].arrangement_clips[0]"
    assert arr["start_time"] == 0 and arr["end_time"] == 4

    paged = run(bridge, "clips.list", limit=2)
    assert len(paged["clips"]) == 2 and paged["next_offset"] == 2
    rest = run(bridge, "clips.list", offset=2, limit=2)
    assert "next_offset" not in rest and len(rest["clips"]) == 2

    full = run(bridge, "clips.list", track=1, detail="summary")
    assert full["clips"][0]["warp_mode"] == "beats"

    song.tracks[2].clip_slots[0].fire()
    playing = run(bridge, "clips.list", playing_only=True)
    assert [c["name"] for c in playing["clips"]] == ["Beat"]
    assert fail(bridge, "clips.list", detail="huge")["type"] == "bad_args"


def test_get_by_address_path_name_and_selected(bridge):
    by_slot = run(bridge, "clips.get", track="Bass", slot=1)
    assert by_slot["name"] == "Bass Fill" and by_slot["note_count"] == 2
    assert by_slot["launch_quantization"] == "global"
    assert by_slot["is_session_clip"] is True

    by_scene = run(bridge, "clips.get", track="Bass", slot="Verse")
    assert by_scene["name"] == "Bass Fill"
    by_path = run(bridge, "clips.get", clip="song.tracks[0].clip_slots[0]")
    assert by_path["name"] == "Bass Loop"
    by_name = run(bridge, "clips.get", clip="bass f")
    assert by_name["path"] == "song.tracks[0].clip_slots[1].clip"
    selected = run(bridge, "clips.get", clip="selected")
    assert selected["name"] == "Bass Loop"
    arrangement = run(bridge, "clips.get", clip="song.tracks[0].arrangement_clips[0]")
    assert arrangement["is_session_clip"] is False and arrangement["start_time"] == 0


def test_get_audio_clip_fields_and_warp_markers(bridge):
    audio = run(bridge, "clips.get", track="Vocals", slot=0, include_warp_markers=True)
    assert audio["file_path"] == "/Samples/vox.wav"
    assert audio["gain_db"] == 0
    assert audio["warp_markers"] == [[0, 0], [8, 8]]
    assert "repitch" in audio["available_warp_modes"]


def test_get_errors(bridge):
    assert fail(bridge, "clips.get", track="Bass", slot=5)["type"] == "not_found"
    assert fail(bridge, "clips.get", track="Bass")["type"] == "bad_args"
    assert fail(bridge, "clips.get")["type"] == "bad_args"
    assert fail(bridge, "clips.get", clip="nothing like this")["type"] == "not_found"
    assert fail(bridge, "clips.get", clip="song.tracks[0]")["type"] == "bad_args"
    assert fail(bridge, "clips.get", clip="bass")["type"] == "bad_args"  # ambiguous


# --------------------------------------------------------------------------
# create / delete / duplicate
# --------------------------------------------------------------------------

def test_create_midi_clip(bridge, song):
    before = len(song._undo_steps)
    result = run(bridge, "clips.create", track="Bass", slot=3, length=2, unit="bars",
                 name="New", color_index=5)
    assert len(song._undo_steps) == before + 1
    assert result["path"] == "song.tracks[0].clip_slots[3].clip"
    assert result["length"] == 8 and result["name"] == "New"
    assert song.tracks[0].clip_slots[3].clip.color_index == 5

    auto = run(bridge, "clips.create", track="Bass")
    assert auto["slot"] == 2 and auto["length"] == 4


def test_create_errors(bridge, song):
    assert fail(bridge, "clips.create", track="Bass", slot=0)["type"] == "invalid_state"
    assert fail(bridge, "clips.create", track="Vocals", slot=2)["type"] == "invalid_state"
    assert fail(bridge, "clips.create", track="Bass", slot=3, length=0)["type"] == "bad_args"
    assert fail(bridge, "clips.create", track="A-Reverb")["type"] == "invalid_state"
    assert fail(bridge, "clips.create", track="Bass", slot=3, unit="ticks")["type"] == "bad_args"


def test_create_audio_clip_from_file(bridge, song, wav):
    result = run(bridge, "clips.create", track="Vocals", slot=2, file_path=wav)
    assert result["is_midi"] is False
    assert song.tracks[1].clip_slots[2].clip.file_path == wav
    missing = fail(bridge, "clips.create", track="Vocals", slot=3,
                   file_path=str(Path(wav).with_name("missing.wav")))
    assert missing["type"] == "not_found" and "machine that runs Live" in missing["message"]
    assert fail(bridge, "clips.create", track="Vocals", slot=3,
                file_path="relative.wav")["type"] == "bad_args"
    assert fail(bridge, "clips.create", track="Bass", slot=3,
                file_path=wav)["type"] == "invalid_state"


def test_delete_session_and_arrangement(bridge, song):
    result = run(bridge, "clips.delete", track="Bass", slot=1)
    assert result == {"deleted": "song.tracks[0].clip_slots[1].clip", "name": "Bass Fill"}
    assert not song.tracks[0].clip_slots[1].has_clip
    arr = run(bridge, "clips.delete", clip="song.tracks[0].arrangement_clips[0]")
    assert arr["name"] == "Bass Arr"
    assert len(song.tracks[0].arrangement_clips) == 0
    assert fail(bridge, "clips.delete", track="Bass", slot=1)["type"] == "not_found"


def test_duplicate_next_free_and_target(bridge, song):
    auto = run(bridge, "clips.duplicate", track="Bass", slot=0)
    assert auto["target"] == "song.tracks[0].clip_slots[2]"
    assert song.tracks[0].clip_slots[2].clip.name == "Bass Loop"

    run(bridge, "clips.duplicate", track="Bass", slot=0, target_track="Drums", target_slot=3)
    assert song.tracks[2].clip_slots[3].clip.name == "Bass Loop"

    blocked = fail(bridge, "clips.duplicate", track="Bass", slot=0, target_slot=1)
    assert blocked["type"] == "invalid_state" and "overwrite" in blocked["message"]
    run(bridge, "clips.duplicate", track="Bass", slot=0, target_slot=1, overwrite=True)
    assert song.tracks[0].clip_slots[1].clip.name == "Bass Loop"

    cross = fail(bridge, "clips.duplicate", track="Bass", slot=0, target_track="Vocals",
                 target_slot=3)
    assert cross["type"] == "invalid_state"
    assert fail(bridge, "clips.duplicate", track="Bass", slot=5)["type"] == "not_found"


# --------------------------------------------------------------------------
# fire / stop
# --------------------------------------------------------------------------

def test_fire_and_stop(bridge, song):
    fired = run(bridge, "clips.fire", track="Drums", slot=0, launch_quantization="1 bar",
                force_legato=True)
    assert fired["has_clip"] and fired["is_playing"]
    assert song.tracks[2].playing_slot_index == 0
    stopped = run(bridge, "clips.stop", track="Drums", slot=0)
    assert stopped["stopped"] == "clip"
    assert not song.tracks[2].clip_slots[0].clip.is_playing

    run(bridge, "clips.fire", clip="Bass Loop")
    assert song.tracks[0].clip_slots[0].clip.is_playing
    assert run(bridge, "clips.stop", track="Bass", quantized=False)["stopped"] == "track"
    assert not song.tracks[0].clip_slots[0].clip.is_playing

    run(bridge, "clips.fire", track=0, slot=1)
    assert run(bridge, "clips.stop")["stopped"] == "all"
    assert fail(bridge, "clips.fire", track=0, slot=1,
                launch_quantization="1/5")["type"] == "bad_args"


def test_fire_empty_slot_hits_stop_button(bridge):
    result = run(bridge, "clips.fire", track="Bass", slot=3)
    assert result["has_clip"] is False


# --------------------------------------------------------------------------
# clips.set
# --------------------------------------------------------------------------

def test_set_basic_properties(bridge, song):
    before = len(song._undo_steps)
    result = run(bridge, "clips.set", track="Bass", slot=0, name="Renamed", color_index=12,
                 color="#FF8000", muted=True, launch_mode="gate", launch_quantization="1/16",
                 legato=True, velocity_amount=0.5, signature="3/4")
    assert len(song._undo_steps) == before + 1
    clip = song.tracks[0].clip_slots[0].clip
    assert clip.name == "Renamed" and clip.color_index == 12 and clip.color == 0xFF8000
    assert clip.muted is True and clip.launch_mode == 1 and clip.launch_quantization == 12
    assert clip.legato is True and clip.velocity_amount == 0.5
    assert clip.signature_numerator == 3
    assert result["clip"]["launch_mode"] == "gate"
    assert "name" in result["changed"]
    # Live's own enum member names work too
    run(bridge, "clips.set", track="Bass", slot=0, launch_quantization="q_eighth")
    assert clip.launch_quantization == 10


def test_set_loop_and_markers_in_safe_order(bridge, song):
    clip = song.tracks[0].clip_slots[0].clip  # loop 0..4
    run(bridge, "clips.set", track="Bass", slot=0, loop_start=8, loop_end=12)
    assert (clip.loop_start, clip.loop_end) == (8.0, 12.0)
    run(bridge, "clips.set", track="Bass", slot=0, loop_start=1, loop_end=3)
    assert (clip.loop_start, clip.loop_end) == (1.0, 3.0)
    run(bridge, "clips.set", track="Bass", slot=0, length=2, unit="bars")
    assert clip.loop_end == 9.0
    run(bridge, "clips.set", track="Bass", slot=0, start_marker=2, end_marker=16)
    assert (clip.start_marker, clip.end_marker) == (2.0, 16.0)
    run(bridge, "clips.set", track="Bass", slot=0, loop_start=2, unit="bars")
    assert clip.loop_start == 4.0
    run(bridge, "clips.set", track="Bass", slot=0, looping=False, length=4)
    assert clip.looping is False and clip.end_marker == 6.0
    bad = fail(bridge, "clips.set", track="Bass", slot=0, loop_start=5, loop_end=5)
    assert bad["type"] == "bad_args"
    assert fail(bridge, "clips.set", track="Bass", slot=0, loop_start=0,
                unit="bars")["type"] == "bad_args"


def test_set_audio_properties(bridge, song):
    clip = song.tracks[1].clip_slots[0].clip
    result = run(bridge, "clips.set", track="Vocals", slot=0, warp_mode="complex pro",
                 pitch_coarse=-3, pitch_fine=12.5, gain_db=-6, ram_mode=True)
    assert clip.warp_mode == 6 and clip.pitch_coarse == -3 and clip.pitch_fine == 12.5
    assert clip.ram_mode is True
    assert abs(result["clip"]["gain_db"] - (-6.0)) <= 0.2
    run(bridge, "clips.set", track="Vocals", slot=0, gain=0.4, warping=False)
    assert clip.gain == 0.4 and clip.warping is False
    rex = fail(bridge, "clips.set", track="Vocals", slot=0, warp_mode="rex")
    assert rex["type"] == "invalid_state" and "not available" in rex["message"]
    loop = fail(bridge, "clips.set", track="Vocals", slot=0, looping=True)
    assert loop["type"] == "invalid_state"  # unwarped audio cannot loop


def test_set_color_uses_the_shared_parser(bridge, song):
    """Same colour forms as tracks.set: a name/index sets color_index, RGB sets color."""
    run(bridge, "clips.set", track="Bass", slot=0, color="red")
    assert song.tracks[0].clip_slots[0].clip.color_index == 14
    run(bridge, "clips.set", track="Bass", slot=0, color=[0, 128, 255])
    assert song.tracks[0].clip_slots[0].clip.color == 0x0080FF


def test_set_rejects_nonsense(bridge):
    midi_audio = fail(bridge, "clips.set", track="Bass", slot=0, warp_mode="beats")
    assert midi_audio["type"] == "invalid_state"
    assert fail(bridge, "clips.set", track="Bass", slot=0)["type"] == "bad_args"
    assert fail(bridge, "clips.set", track="Bass", slot=0, color="reddish")["type"] == "bad_args"
    assert fail(bridge, "clips.set", track="Bass", slot=0,
                color_index=99)["type"] == "bad_args"
    assert fail(bridge, "clips.set", track="Bass", slot=0,
                launch_mode="bounce")["type"] == "bad_args"
    assert fail(bridge, "clips.set", track="Vocals", slot=0, gain=0.5,
                gain_db=1)["type"] == "bad_args"
    assert fail(bridge, "clips.set", track="Bass", slot=0,
                signature="waltz")["type"] == "bad_args"
    assert fail(bridge, "clips.set", track="Bass", slot=0, bogus=1)["type"] == "bad_args"


# --------------------------------------------------------------------------
# quantize / crop / duplicate_loop
# --------------------------------------------------------------------------

def test_quantize(bridge, song):
    clip = song.tracks[0].clip_slots[1].clip
    clip.get_all_notes_extended()
    notes = clip.get_all_notes_extended()
    notes[1].start_time = 0.6
    clip.apply_note_modifications(notes)
    result = run(bridge, "clips.quantize", track="Bass", slot=1, grid="1/8", amount=1.0)
    assert result["grid"] == "1/8"
    starts = sorted(n.start_time for n in clip.get_all_notes_extended())
    assert starts == [0.0, 0.5]
    assert fail(bridge, "clips.quantize", track="Bass", slot=1, grid="none")["type"] == "bad_args"
    assert fail(bridge, "clips.quantize", track="Bass", slot=1, grid="1/7")["type"] == "bad_args"
    assert fail(bridge, "clips.quantize", track="Bass", slot=1,
                amount=2)["type"] == "bad_args"


def test_crop_and_duplicate_loop(bridge, song):
    clip = song.tracks[0].clip_slots[0].clip
    doubled = run(bridge, "clips.duplicate_loop", track="Bass", slot=0)
    assert doubled["clip"]["length"] == 8
    assert len(clip.get_all_notes_extended()) == 8
    clip.loop_start = 4.0
    clip.start_marker = 4.0        # Live's crop keeps a lead-in before the loop start
    cropped = run(bridge, "clips.crop", track="Bass", slot=0)
    assert cropped["clip"]["length"] == 4
    assert len(clip.get_all_notes_extended()) == 4


def test_reverse_midi_and_audio(bridge, song):
    clip = song.tracks[0].clip_slots[0].clip  # 36@0 36@1 43@2 36@3, 0.5 long, loop 0..4
    result = run(bridge, "clips.reverse", track="Bass", slot=0)
    assert result["reversed"] == 4
    rows = sorted((n.start_time, n.pitch, n.duration, n.velocity)
                  for n in clip.get_all_notes_extended())
    assert rows == [(0.5, 36, 0.5, 110.0), (1.5, 43, 0.5, 80.0), (2.5, 36, 0.5, 90.0),
                    (3.5, 36, 0.5, 100.0)]
    audio = fail(bridge, "clips.reverse", track="Vocals", slot=0)
    assert audio["type"] == "unsupported" and "Rev" in audio["message"]


# --------------------------------------------------------------------------
# MCP tools
# --------------------------------------------------------------------------

from fake_bridge import FakeBridge  # noqa: E402

from livebridge_mcp.client import BridgeClient  # noqa: E402
from livebridge_mcp.server import create_app  # noqa: E402

CLIP_TOOLS = ["live_clip_list", "live_clip_get", "live_clip_create", "live_clip_delete",
              "live_clip_duplicate", "live_clip_fire", "live_clip_stop", "live_clip_set",
              "live_clip_edit", "live_clip_warp", "live_clip_convert", "live_groove_pool"]


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


def test_tools_are_registered_with_docs(mcp):
    app, _fake = mcp
    tools = {t.name: t for t in asyncio.run(app.list_tools())}
    for name in CLIP_TOOLS:
        assert name in tools, name
        assert len(tools[name].description or "") > 120, name
    assert "clips" in app.tool_modules


def test_tool_list_and_get_forward_arguments(mcp):
    app, fake = mcp
    fake.set_result("clips.list", {"count": 0, "offset": 0, "clips": []})
    _text, data = call_tool(app, "live_clip_list", {"track": "Bass"})
    assert data["count"] == 0
    assert last(fake)["args"] == {"track": "Bass", "detail": "minimal", "offset": 0,
                                  "limit": 200}
    fake.set_result("clips.get", {"name": "x"})
    call_tool(app, "live_clip_get", {"track": 0, "slot": "Verse", "warp_markers": True})
    assert last(fake)["args"] == {"track": 0, "slot": "Verse", "detail": "full",
                                  "include_warp_markers": True}
    call_tool(app, "live_clip_get", {"clip": "selected"})
    assert last(fake)["args"]["clip"] == "selected"


def test_tool_validation_never_reaches_live(mcp):
    app, fake = mcp
    count = len(fake.requests)
    _t, data = call_tool(app, "live_clip_get", {})
    assert data["type"] == "bad_args"
    _t, data = call_tool(app, "live_clip_get", {"track": 0})
    assert data["type"] == "bad_args" and "slot" in data["error"]
    _t, data = call_tool(app, "live_clip_list", {"detail": "everything"})
    assert data["type"] == "bad_args"
    _t, data = call_tool(app, "live_clip_create", {"track": 0, "length": -1})
    assert data["type"] == "bad_args"
    _t, data = call_tool(app, "live_clip_set", {"track": 0, "slot": 0})
    assert data["type"] == "bad_args" and "nothing" in data["error"]
    _t, data = call_tool(app, "live_clip_set", {"track": 0, "slot": 0, "pitch_coarse": 99})
    assert data["type"] == "bad_args"
    _t, data = call_tool(app, "live_clip_edit", {"track": 0, "slot": 0, "action": "explode"})
    assert data["type"] == "bad_args"
    _t, data = call_tool(app, "live_clip_stop", {"slot": 2})
    assert data["type"] == "bad_args"
    _t, data = call_tool(app, "live_clip_edit", {"track": 0, "slot": 0, "action": "quantize",
                                                 "grid": "1/5"})
    assert data["type"] == "bad_args"
    assert len(fake.requests) == count


def test_tool_create_delete_duplicate(mcp):
    app, fake = mcp
    fake.set_result("clips.create", {"path": "p"})
    call_tool(app, "live_clip_create", {"track": "Bass", "length": 2, "unit": "bars",
                                        "name": "Hook"})
    assert last(fake)["cmd"] == "clips.create"
    assert last(fake)["args"] == {"track": "Bass", "length": 2.0, "unit": "bars",
                                  "name": "Hook"}
    fake.set_result("clips.delete", {"deleted": "p"})
    call_tool(app, "live_clip_delete", {"clip": "song.tracks[0].arrangement_clips[0]"})
    assert last(fake)["args"] == {"clip": "song.tracks[0].arrangement_clips[0]"}
    fake.set_result("clips.duplicate", {"target": "t"})
    call_tool(app, "live_clip_duplicate", {"track": 0, "slot": 0, "target_slot": 3,
                                           "overwrite": True})
    assert last(fake)["args"] == {"track": 0, "slot": 0, "target_slot": 3, "overwrite": True}


def test_tool_fire_stop_set_edit(mcp):
    app, fake = mcp
    fake.set_result("clips.fire", {"is_triggered": True})
    call_tool(app, "live_clip_fire", {"track": 0, "slot": 1, "launch_quantization": "1 bar"})
    assert last(fake)["args"] == {"track": 0, "slot": 1, "launch_quantization": "1 bar"}
    fake.set_result("clips.stop", {"stopped": "all"})
    call_tool(app, "live_clip_stop", {})
    assert last(fake)["args"] == {"quantized": True}
    fake.set_result("clips.set", {"changed": ["name"]})
    call_tool(app, "live_clip_set", {"track": 0, "slot": 0, "name": "A", "loop_end": 8,
                                     "warp_mode": "Complex Pro"})
    assert last(fake)["args"] == {"track": 0, "slot": 0, "name": "A", "loop_end": 8.0,
                                  "warp_mode": "Complex Pro", "unit": "beats"}
    for action, cmd in (("quantize", "clips.quantize"), ("reverse", "clips.reverse"),
                        ("crop", "clips.crop"), ("duplicate_loop", "clips.duplicate_loop")):
        fake.set_result(cmd, {"ok": 1})
        call_tool(app, "live_clip_edit", {"track": 0, "slot": 0, "action": action})
        assert last(fake)["cmd"] == cmd
    assert last(fake)["args"] == {"track": 0, "slot": 0}


def test_tool_passes_bridge_errors_through(mcp):
    app, fake = mcp
    fake.set_error("clips.get", "not_found", "song.tracks[0].clip_slots[5]: the clip slot is "
                   "empty")
    _t, data = call_tool(app, "live_clip_get", {"track": 0, "slot": 5})
    assert data["type"] == "not_found" and "empty" in data["error"]


# --------------------------------------------------------------------------
# g2: grooves, warp markers, conversions, path forms
# --------------------------------------------------------------------------

import Live  # noqa: E402

from live_stub import factory  # noqa: E402


def add_grooves(song, *names):
    """Grooves like Live 12.4.5 reports them: amounts in percent (a pool groove read
    timing_amount 100.0 on the running Live — the shared stub's default now)."""
    pool = song.groove_pool
    for name in names:
        groove = Live._model.Groove(name, pool)
        assert groove.timing_amount == 100.0
        pool._grooves.append(groove)
    return list(pool.grooves)


def test_groove_pool_and_clip_groove(bridge, song):
    empty = run(bridge, "clips.grooves")
    assert empty["count"] == 0 and "empty" in empty["note"]
    assert fail(bridge, "clips.set", track=0, slot=0, groove=0)["type"] == "not_found"
    swing, mpc = add_grooves(song, "Swing 16ths 66", "MPC 16 Swing-62")
    listed = run(bridge, "clips.grooves")
    assert listed["grooves"][1] == {"index": 1, "name": "MPC 16 Swing-62", "base": "1/16",
                                    "timing": 100.0, "random": 0.0, "velocity": 0.0,
                                    "quantization": 0.0}
    result = run(bridge, "clips.set", track=0, slot=0, groove="mpc")
    clip = song.tracks[0].clip_slots[0].clip
    assert "groove" in result["changed"] and clip.groove is mpc
    assert result["clip"]["groove"] == "MPC 16 Swing-62"
    run(bridge, "clips.set", track=0, slot=1, groove=0)
    assert song.tracks[0].clip_slots[1].clip.groove is swing
    changed = run(bridge, "clips.grooves", groove="MPC", timing=70, velocity=-20, base="1/8",
                  random=5, quantize=10, name="MPC Lazy", include_clips=True)
    assert changed["changed"] == ["timing", "random", "velocity", "quantization", "base",
                                  "name"]
    row = changed["grooves"][1]
    assert (row["name"], row["timing"], row["velocity"], row["base"]) == \
        ("MPC Lazy", 70.0, -20.0, "1/8")
    assert row["clips"] == ["song.tracks[0].clip_slots[0].clip"]
    none = fail(bridge, "clips.set", track=0, slot=0, groove="none")
    assert none["type"] == "unsupported" and "cannot remove" in none["message"]
    for args in ({"groove": 0}, {"timing": 50}, {"groove": 0, "timing": 150},
                 {"groove": 0, "base": "1/3"}, {"groove": "nope", "timing": 1}):
        assert fail(bridge, "clips.grooves", **args)["type"] in ("bad_args", "not_found"), args
    add_grooves(song, "MPC 16 Swing-58")
    assert fail(bridge, "clips.set", track=0, slot=0,
                groove="MPC")["type"] == "bad_args"          # "MPC Lazy" / "MPC 16 ..." 


def test_warp_markers(bridge, song):
    listed = run(bridge, "clips.warp", track="Vocals", slot=0)
    assert listed["warp_markers"][0] == [0.0, 0.0] and listed["action"] == "list"
    clip = song.tracks[1].clip_slots[0].clip
    before = len(clip.warp_markers)
    pinned = run(bridge, "clips.warp", action="add", track="Vocals", slot=0, beat_time=2)
    assert pinned["count"] == before + 1
    assert [0.0 + 1.0, 2.0] in pinned["warp_markers"]         # stub: 0.5 s per beat
    explicit = run(bridge, "clips.warp", action="add", track="Vocals", slot=0, beat_time=4,
                   sample_time=2.1)
    assert [2.1, 4.0] in explicit["warp_markers"]
    moved = run(bridge, "clips.warp", action="move", track="Vocals", slot=0, beat_time=4,
                to=4.25)
    assert [2.1, 4.25] in moved["warp_markers"]
    run(bridge, "clips.warp", action="move", track="Vocals", slot=0, beat_time=4.25,
        distance=-0.25)
    removed = run(bridge, "clips.warp", action="remove", track="Vocals", slot=0, beat_time=4)
    assert all(row[1] != 4.0 for row in removed["warp_markers"])
    assert fail(bridge, "clips.warp", action="remove", track="Vocals", slot=0,
                beat_time=5.5)["type"] == "not_found"
    assert fail(bridge, "clips.warp", action="add", track="Vocals",
                slot=0)["type"] == "bad_args"
    assert fail(bridge, "clips.warp", action="move", track="Vocals", slot=0,
                beat_time=2)["type"] == "bad_args"
    assert fail(bridge, "clips.warp", track=0, slot=0)["type"] == "invalid_state"   # MIDI
    clip.warping = False
    assert fail(bridge, "clips.warp", action="add", track="Vocals", slot=0,
                beat_time=1)["type"] == "invalid_state"


@pytest.fixture
def conversions():
    from live_stub_ext import conversions as ext
    installed = ext.install(Live)
    yield installed
    installed.uninstall()


def test_convert_audio_clips(bridge, song, conversions):
    pending = run(bridge, "clips.convert", to="midi", track="Vocals", slot=0, type="melody")
    assert pending["pending"] is True and pending["new_tracks"] == []
    assert conversions.calls[-1] == ("audio_to_midi_clip",
                                     (song.tracks[1].clip_slots[0].clip, 1))
    conversions.finish_background()
    assert song.tracks[-1].name == "Melody to MIDI"
    rack = run(bridge, "clips.convert", to="drum_rack", track="Vocals", slot=0)
    assert rack["new_tracks"][0]["name"] == "Drum Rack" and "pending" not in rack
    simpler = run(bridge, "clips.convert", to="simpler", track="Vocals", slot=0)
    assert simpler["new_tracks"][0]["name"] == "Vox Take"
    assert fail(bridge, "clips.convert", to="midi", track="Bass", slot=0)["type"] == \
        "invalid_state"                                          # a MIDI clip
    assert fail(bridge, "clips.convert", to="video", track="Vocals",
                slot=0)["type"] == "bad_args"
    assert fail(bridge, "clips.convert", to="midi", type="speech", track="Vocals",
                slot=0)["type"] == "bad_args"


def test_convert_without_live_conversions_is_unsupported(bridge, song):
    assert not hasattr(Live, "Conversions")
    assert fail(bridge, "clips.convert", to="simpler", track="Vocals",
                slot=0)["type"] == "unsupported"


def test_audio_paths_are_normalised_like_samples_import(bridge, song, wav):
    """Regression (low finding): clips.create / arrangement.create_audio_clip refused paths
    samples.import accepts — Explorer's quoted "Copy as path" and file:// URLs."""
    quoted = run(bridge, "clips.create", track="Vocals", slot=2, file_path='"%s"' % wav)
    assert song.tracks[1].clip_slots[2].clip.file_path == wav and quoted["is_midi"] is False
    url = run(bridge, "arrangement.create_audio_clip", track="Vocals",
              file_path="file://" + wav.replace(" ", "%20"), start=8)
    assert url["file_path"] == wav or url.get("path")
    text = Path(wav).with_suffix(".txt")
    text.write_text("x", encoding="utf-8")
    assert fail(bridge, "clips.create", track="Vocals", slot=3,
                file_path=str(text))["type"] == "bad_args"


def test_new_clip_tools(mcp):
    app, fake = mcp
    for cmd in ("clips.warp", "clips.convert", "clips.grooves", "clips.set"):
        fake.set_result(cmd, {"ok": cmd})
    call_tool(app, "live_clip_warp", {"track": "Vox", "slot": 0, "action": "move",
                                      "beat_time": 4, "to": 4.25})
    assert last(fake)["args"] == {"track": "Vox", "slot": 0, "action": "move",
                                  "beat_time": 4.0, "to": 4.25}
    call_tool(app, "live_clip_convert", {"clip": "Loop", "to": "midi", "type": "harmony"})
    assert last(fake)["args"] == {"clip": "Loop", "to": "midi", "type": "harmony"}
    call_tool(app, "live_clip_convert", {"track": 0, "slot": 0, "to": "simpler"})
    assert last(fake)["args"] == {"track": 0, "slot": 0, "to": "simpler"}
    call_tool(app, "live_groove_pool", {})
    assert last(fake)["args"] == {}
    call_tool(app, "live_groove_pool", {"groove": "MPC", "timing": 60, "base": "1/8T"})
    assert last(fake)["args"] == {"groove": "MPC", "timing": 60.0, "base": "1/8T"}
    call_tool(app, "live_clip_set", {"track": 0, "slot": 0, "groove": "MPC"})
    assert last(fake)["args"]["groove"] == "MPC"
    count = len(fake.requests)
    for name, args in [
        ("live_clip_warp", {"track": 0, "slot": 0, "action": "bend"}),
        ("live_clip_warp", {"track": 0, "slot": 0, "action": "add"}),
        ("live_clip_warp", {"track": 0, "slot": 0, "action": "move", "beat_time": 1}),
        ("live_clip_convert", {"track": 0, "slot": 0, "to": "mp3"}),
        ("live_clip_convert", {"track": 0, "slot": 0, "to": "midi", "type": "voice"}),
        ("live_groove_pool", {"timing": 50}),
        ("live_groove_pool", {"groove": 0}),
        ("live_groove_pool", {"groove": 0, "velocity": 200}),
        ("live_groove_pool", {"groove": 0, "base": "1/5"}),
    ]:
        _t, data = call_tool(app, name, args)
        assert isinstance(data, dict) and data.get("type") == "bad_args", (name, data)
    assert len(fake.requests) == count
