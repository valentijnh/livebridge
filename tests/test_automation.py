"""Module F — clip automation envelopes and automation state: bridge commands on the stub song,
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

import Live  # noqa: E402

from live_stub import factory  # noqa: E402

from fake_bridge import FakeBridge  # noqa: E402
from live_stub_ext import automation as automation_ext  # noqa: E402
from livebridge_mcp.client import BridgeClient  # noqa: E402
from livebridge_mcp.server import create_app  # noqa: E402

from LiveBridge.handlers import automation as automation_module  # noqa: E402

EXT = automation_ext.install(Live)


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


def bass(song):
    return song.tracks[0]


def operator(song):
    return bass(song).devices[0]


def clip0(song):
    return bass(song).clip_slots[0].clip


def envelope(song, param, clip=None):
    return (clip or clip0(song)).automation_envelope(param)


# --------------------------------------------------------------------------
# pure helpers
# --------------------------------------------------------------------------

def test_parse_display():
    parse = automation_module.parse_display
    assert parse("1.00 kHz") == (1000.0, "hz")
    assert parse("250 ms") == (0.25, "s")
    assert parse("-6.0 dB") == (-6.0, "db")
    assert parse("-inf dB") == (float("-inf"), "db")
    assert parse("25L") == (-25.0, "pan")
    assert parse("R 10") == (10.0, "pan")
    assert parse("C") == (0.0, "pan")
    assert parse("50 %") == (50.0, "%")
    assert parse("Saw") is None


def test_display_values_on_real_live_curves(song):
    device = operator(song)
    volume = EXT.add_display_parameter(device, "Gain", "volume", 0.85)
    pan = EXT.add_display_parameter(device, "Spread", "pan", 0.0)
    freq = EXT.add_display_parameter(device, "Cutoff", "freq", 0.5)
    native = automation_module.native_value
    value, clamped = native(volume, "-6 dB")
    assert volume.str_for_value(value) == "-6.0 dB" and not clamped
    assert native(volume, "0 dB")[0] == pytest.approx(0.85, abs=0.0005)
    assert native(volume, "6 dB") == (1.0, False)
    assert native(volume, "-inf dB")[0] == 0.0
    assert native(volume, "+12 dB") == (1.0, True)
    assert native(pan, "25L")[0] == pytest.approx(-0.5, abs=0.011)
    assert native(pan, "C")[0] == pytest.approx(0.0, abs=0.011)
    assert freq.str_for_value(native(freq, "1 kHz")[0]) == "1.00 kHz"
    assert freq.str_for_value(native(freq, "800 Hz")[0]) == "800 Hz"
    osc = device.parameters[6]
    assert native(osc, "square") == (2.0, False)
    assert native(osc, "No") == (3.0, False)          # prefix of "Noise"
    assert native(osc, 7) == (3.0, True)
    assert native(osc, 1.4) == (1.0, False)
    assert native(device.parameters[2], 0.5, True) == (pytest.approx(10010.0), False)
    assert native(device.parameters[2], True) == (20000.0, False)


def test_shape_steps_math():
    steps = automation_module.shape_steps
    ramp = steps("ramp_up", 0.0, 4.0, 0.0, 1.0, 4.0, 0.0, 1.0, 0.5, 1.0, None)
    assert [s[2] for s in ramp] == [0.0, 1.0 / 3, 2.0 / 3, 1.0]
    assert [s[0] for s in ramp] == [0.0, 1.0, 2.0, 3.0] and ramp[-1][1] == 1.0
    down = steps("ramp_down", 0.0, 2.0, 0.0, 1.0, 4.0, 0.0, 1.0, 0.5, 1.0, None)
    assert [s[2] for s in down] == [1.0, 0.0]
    sine = steps("sine", 0.0, 4.0, 0.0, 1.0, 4.0, 0.0, 1.0, 0.5, 1.0, None)
    assert [round(s[2], 6) for s in sine] == [0.5, 1.0, 0.5, 0.0]
    peak = steps("sine", 0.0, 1.0, 0.0, 1.0, 4.0, 0.25, 1.0, 0.5, 1.0, None)
    assert peak[0][2] == pytest.approx(1.0)
    tri = steps("triangle", 0.0, 4.0, 0.0, 1.0, 4.0, 0.0, 1.0, 0.5, 1.0, None)
    assert [s[2] for s in tri] == [0.0, 0.5, 1.0, 0.5]
    saw = steps("saw_down", 0.0, 2.0, 0.0, 1.0, 1.0, 0.0, 0.5, 0.5, 1.0, None)
    assert [s[2] for s in saw] == [1.0, 0.5, 1.0, 0.5]
    square = steps("square", 0.0, 4.0, 0.0, 1.0, 2.0, 0.0, 1.0, 0.25, 1.0, None)
    assert square == [(0.0, 0.5, 1.0), (0.5, 1.5, 0.0), (2.0, 0.5, 1.0), (2.5, 1.5, 0.0)]
    shifted = steps("square", 0.0, 2.0, 0.0, 1.0, 2.0, 0.5, 1.0, 0.5, 1.0, None)
    assert shifted == [(0.0, 1.0, 0.0), (1.0, 1.0, 1.0)]
    eased = steps("ramp_up", 0.0, 3.0, 0.0, 1.0, 4.0, 0.0, 1.0, 0.5, 2.0, None)
    assert [s[2] for s in eased] == [0.0, 0.25, 1.0]


# --------------------------------------------------------------------------
# parameter resolution
# --------------------------------------------------------------------------

def test_resolve_parameter_variants(bridge, song):
    ctx = bridge.ctx
    resolve = automation_module.resolve_parameter
    track = bass(song)
    assert resolve(ctx, track, "Filter Freq")[2] == "song.tracks[0].devices[0].parameters[2]"
    assert resolve(ctx, track, "filter")[0] is operator(song).parameters[2]
    assert resolve(ctx, track, "volume")[2] == "song.tracks[0].mixer_device.volume"
    assert resolve(ctx, track, "Operator > Volume")[0] is operator(song).parameters[1]
    assert resolve(ctx, track, 1, device="Operator")[0] is operator(song).parameters[1]
    assert resolve(ctx, track, "send B")[2] == "song.tracks[0].mixer_device.sends[1]"
    assert resolve(ctx, track, "send Reverb")[2] == "song.tracks[0].mixer_device.sends[0]"
    assert resolve(ctx, track, "sends[1]")[2] == "song.tracks[0].mixer_device.sends[1]"
    assert resolve(ctx, track, "pan")[1] == "Mixer"
    assert resolve(ctx, track, "track on")[2].endswith("track_activator")


def test_list_and_available(bridge, song):
    result = run(bridge, "automation.list", track="Bass", slot=0, include_available=True,
                 filter="freq")
    assert result["count"] == 0 and result["envelopes"] == []
    assert result["clip"] == {"path": "song.tracks[0].clip_slots[0].clip", "name": "Bass Loop"}
    assert result["range"] == [0.0, 4.0]
    assert result["available"] == [{"name": "Filter Freq", "device": "Operator",
                                    "path": "song.tracks[0].devices[0].parameters[2]"}]
    everything = run(bridge, "automation.list", track=0, slot="Intro", include_available=True,
                     limit=5)
    assert len(everything["available"]) == 5 and everything["available_total"] > 20
    devices = {row["device"] for row in run(bridge, "automation.list", track=0, slot=0,
                                            include_available=True, limit=5000)["available"]}
    assert {"Operator", "Bass Rack", "Mixer"} <= devices


def test_write_steps_and_read_back(bridge, song):
    before = len(song._undo_steps)
    result = run(bridge, "automation.write", track="Bass", slot=0, parameter="Filter Freq",
                  points=[{"time": 0, "value": 200}, {"time": "1.2.1", "value": 2000},
                          [3, 20000]])
    assert len(song._undo_steps) == before + 1
    assert result["created"] is True and result["mode"] == "step" and result["steps"] == 3
    assert result["parameter"]["path"] == "song.tracks[0].devices[0].parameters[2]"
    assert result["parameter"]["min"] == 20.0 and result["parameter"]["max"] == 20000.0
    assert result["range"] == [0.0, 4.0]
    assert result["values"] == {"min": 200.0, "max": 20000.0}
    env = envelope(song, operator(song).parameters[2])
    assert env.value_at_time(0.5) == 200.0 and env.value_at_time(1.5) == 2000.0
    assert env.value_at_time(3.5) == 20000.0
    got = run(bridge, "automation.get", track=0, slot=0, parameter="Filter Freq", points=4)
    assert got["has_envelope"] is True and got["values"] == [200.0, 2000.0, 2000.0, 20000.0]
    assert got["step"] == 1.0 and got["start"] == 0.0
    listing = run(bridge, "automation.list", track=0, slot=0, sample=4)
    assert listing["count"] == 1
    assert listing["envelopes"][0]["name"] == "Filter Freq"
    assert listing["envelopes"][0]["values"] == [200.0, 2000.0, 2000.0, 20000.0]


def test_write_linear_and_events(bridge, song):
    result = run(bridge, "automation.write", clip="song.tracks[0].clip_slots[0].clip",
                 parameter="Resonance", points=[[0, 0.0], [2, 1.0]], mode="linear",
                 resolution=4, end=4)
    assert result["steps"] == 5
    env = envelope(song, operator(song).parameters[3])
    assert [env.value_at_time(t) for t in (0.1, 0.6, 1.1, 1.6, 2.5)] == \
        [0.0, 0.25, 0.5, 0.75, 1.0]
    events = run(bridge, "automation.write", track=0, slot=0, parameter="Attack",
                 points=[[0, 0.0], [4, 5.0]], mode="events")
    assert events["mode"] == "events" and events["steps"] == 2
    got = run(bridge, "automation.get", track=0, slot=0, parameter="Attack",
              include_events=True)
    assert got["events"] == [[0.0, 0.0], [4.0, 5.0]]


def test_write_display_normalized_and_clamp(bridge, song):
    gain = EXT.add_display_parameter(operator(song), "Gain", "volume", 0.85)
    result = run(bridge, "automation.write", track=0, slot=0, parameter="Gain",
                 points=[[0, "-12 dB"], [2, "0 dB"]])
    env = envelope(song, gain)
    assert gain.str_for_value(env.value_at_time(0.5)) == "-12.0 dB"
    assert env.value_at_time(2.5) == pytest.approx(0.85, abs=1e-3)
    assert automation_module.parse_display(gain.str_for_value(env.value_at_time(2.5)))[0] == 0
    assert "clamped" not in result
    norm = run(bridge, "automation.write", track=0, slot=0, parameter="Filter Freq",
               points=[[0, 0.0], [1, 1.0], [2, 1.7]], normalized=True)
    assert norm["values"] == {"min": 20.0, "max": 20000.0} and norm["clamped"] == 1
    clamp = run(bridge, "automation.write", track=0, slot=0, parameter="Resonance",
                points=[[0, 5.0]])
    assert clamp["clamped"] == 1 and clamp["values"]["max"] == 1.0
    wave = run(bridge, "automation.write", track=0, slot=0, parameter="Osc Wave",
               points=[[0, "Sine"], [1, "Square"], [2, "Noise"]])
    assert wave["values"] == {"min": 0.0, "max": 3.0}
    bad = fail(bridge, "automation.write", track=0, slot=0, parameter="Filter Freq",
               points=[[0, "loud"]])
    assert bad["type"] == "bad_args"


def test_mixer_automation(bridge, song):
    result = run(bridge, "automation.write", track="Bass", slot=0, parameter="volume",
                 points=[[0, 0.5], [2, 0.85]])
    assert result["parameter"]["device"] == "Mixer"
    assert result["parameter"]["path"] == "song.tracks[0].mixer_device.volume"
    assert envelope(song, bass(song).mixer_device.volume).value_at_time(3.0) == 0.85
    send = run(bridge, "automation.shape", track=0, slot=0, parameter="send A",
               shape="ramp_up", step=1)
    assert send["parameter"]["path"] == "song.tracks[0].mixer_device.sends[0]"
    assert send["steps"] == 4
    pan = run(bridge, "automation.write", track=0, slot=0,
              parameter="song.tracks[0].mixer_device.panning", points=[[0, -1], [2, 1]])
    assert pan["parameter"]["name"] == "Track Panning"
    listing = run(bridge, "automation.list", track=0, slot=0)
    assert {r["name"] for r in listing["envelopes"]} == {"Track Volume", "A-Reverb",
                                                         "Track Panning"}


def test_shapes_write(bridge, song):
    sine = run(bridge, "automation.shape", track=0, slot=0, parameter="Resonance",
               shape="lfo", period="1/4", step="1/16")
    assert sine["shape"] == "sine" and sine["period"] == 1.0 and sine["steps"] == 16
    env = envelope(song, operator(song).parameters[3])
    # Like real Live, a value read exactly on a step border is the value before it.
    assert env.value_at_time(0.25 + 1e-3) == pytest.approx(1.0)
    assert env.value_at_time(0.75 + 1e-3) == pytest.approx(0.0)
    square = run(bridge, "automation.shape", track=0, slot=0, parameter="Resonance",
                 shape="square", cycles=2, low=0.2, high=0.8, clear=True)
    assert square["steps"] == 4 and square["values"] == {"min": 0.2, "max": 0.8}
    cleared = envelope(song, operator(song).parameters[3])
    assert cleared.value_at_time(0.5) == pytest.approx(0.8)
    assert cleared.value_at_time(1.5) == pytest.approx(0.2)
    ramp = run(bridge, "automation.shape", track=0, slot=0, parameter="Filter Freq",
               shape="fade_out", start="1.2.1", end="1.4.1", low="200 Hz", high=1.0)
    assert ramp["range"] == [1.0, 3.0] and ramp["values"]["max"] == 20000.0
    freq = envelope(song, operator(song).parameters[2])
    assert freq.value_at_time(1.0 + 1e-3) == 20000.0
    assert freq.value_at_time(2.99) == pytest.approx(ramp["values"]["min"])
    native = run(bridge, "automation.shape", track=0, slot=0, parameter="Attack",
                 shape="triangle", normalized=False, low=1.0, high=3.0, period=4, step=1)
    assert native["values"] == {"min": 1.0, "max": 3.0}


def test_random_shape_is_seeded(bridge, song):
    first = run(bridge, "automation.shape", track=0, slot=0, parameter="Resonance",
                shape="random", seed=42)
    values = [envelope(song, operator(song).parameters[3]).value_at_time(t / 4.0 + 1e-3)
              for t in range(16)]
    assert first["seed"] == 42 and first["steps"] == 16
    run(bridge, "automation.shape", track=0, slot=0, parameter="Resonance", shape="s&h",
        seed=42, clear=True)
    again = [envelope(song, operator(song).parameters[3]).value_at_time(t / 4.0 + 1e-3)
             for t in range(16)]
    assert values == again and len(set(values)) > 8
    unseeded = run(bridge, "automation.shape", track=0, slot=0, parameter="Resonance",
                   shape="random")
    assert isinstance(unseeded["seed"], int)


def test_shape_validation(bridge, song):
    for args in ({"shape": "wobble"}, {"shape": "sine", "duty": 2},
                 {"shape": "sine", "curve": 0}, {"shape": "sine", "cycles": 0},
                 {"shape": "sine", "period": "1/0"}, {"shape": "random", "step": "1/8192"},
                 {"shape": "sine", "start": 3, "end": 1}):
        error = fail(bridge, "automation.shape", track=0, slot=0, parameter="Resonance",
                     **args)
        assert error["type"] == "bad_args", (args, error)


def test_get_variants(bridge, song):
    run(bridge, "automation.write", track=0, slot=0, parameter="Filter Freq",
        points=[[0, 100], [2, 1000]])
    grid = run(bridge, "automation.get", track=0, slot=0, parameter="Filter Freq", step="1/8",
               display=True, start=1, end=3)
    assert grid["values"] == [100.0, 100.0, 1000.0, 1000.0]
    assert grid["display"][0] == "100.00" and grid["step"] == 0.5
    missing = run(bridge, "automation.get", track=0, slot=0, parameter="Release")
    assert missing["has_envelope"] is False and missing["value"] == 0.5
    assert fail(bridge, "automation.get", track=0, slot=0, parameter="Release",
                points=0)["type"] == "bad_args"
    assert fail(bridge, "automation.get", track=0, slot=0, parameter="Release",
                step="1/4096", start=0, end=4)["type"] == "bad_args"


def test_selected_clip_and_name_addressing(bridge, song):
    song.view.detail_clip = bass(song).clip_slots[1].clip
    result = run(bridge, "automation.write", clip="selected", parameter="Filter Freq",
                 points=[[0, 500]])
    assert result["clip"]["name"] == "Bass Fill"
    by_name = run(bridge, "automation.list", clip="bass fill")
    assert by_name["count"] == 1
    assert fail(bridge, "automation.list", clip="Nothing")["type"] == "not_found"
    assert fail(bridge, "automation.list", track=0)["type"] == "bad_args"
    assert fail(bridge, "automation.list")["type"] == "bad_args"
    assert fail(bridge, "automation.list", track=0, slot=3)["type"] == "not_found"


def test_arrangement_clip_is_unsupported(bridge, song):
    path = "song.tracks[0].arrangement_clips[0]"
    error = fail(bridge, "automation.write", clip=path, parameter="Filter Freq",
                 points=[[0, 100]])
    assert error["type"] == "unsupported" and "session clip" in error["message"]
    listing = run(bridge, "automation.list", clip=path)
    assert listing["clip"]["arrangement"] is True and "note" in listing
    got = run(bridge, "automation.get", clip=path, parameter="volume")
    assert got["has_envelope"] is False and "note" in got


def test_parameter_errors(bridge, song):
    other = song.tracks[1].devices[0].parameters[1]
    assert other.name == "Dry/Wet"
    error = fail(bridge, "automation.write", track=0, slot=0,
                 parameter="song.tracks[1].devices[0].parameters[1]", points=[[0, 0.5]])
    assert error["type"] == "bad_args" and "own track" in error["message"]
    ambiguous = fail(bridge, "automation.get", track="Drums", slot=0, parameter="Device On")
    assert ambiguous["type"] == "bad_args" and "ambiguous" in ambiguous["message"]
    nested = run(bridge, "automation.get", track="Drums", slot=0,
                 parameter="Kick > Device On")
    assert "chains[0]" in nested["parameter"]["path"]
    assert fail(bridge, "automation.get", track=0, slot=0,
                parameter="Nope")["type"] == "not_found"
    assert fail(bridge, "automation.get", track=0, slot=0, parameter=3)["type"] == "bad_args"
    assert fail(bridge, "automation.get", track=0, slot=0,
                parameter="song.tracks[0]")["type"] == "bad_args"
    assert fail(bridge, "automation.get", track=0, slot=0, parameter="send C")["type"] == \
        "not_found"
    assert fail(bridge, "automation.write", track=0, slot=0, parameter="Resonance",
                points=[])["type"] == "bad_args"
    assert fail(bridge, "automation.write", track=0, slot=0, parameter="Resonance",
                points=[{"time": 0}])["type"] == "bad_args"
    assert fail(bridge, "automation.write", track=0, slot=0, parameter="Resonance",
                points=[[0, 1]], mode="curvy")["type"] == "bad_args"


def test_clear(bridge, song):
    run(bridge, "automation.write", track=0, slot=0, parameter="Filter Freq",
        points=[[0, 100], [2, 1000]])
    run(bridge, "automation.write", track=0, slot=0, parameter="volume", points=[[0, 0.5]])
    ranged = run(bridge, "automation.clear", track=0, slot=0, parameter="Filter Freq",
                 start=1.5, end=4)
    assert ranged["cleared"][0]["range"] == [1.5, 4.0]
    one = run(bridge, "automation.clear", track=0, slot=0, parameter="Filter Freq")
    assert one["count"] == 1 and envelope(song, operator(song).parameters[2]) is None
    nothing = run(bridge, "automation.clear", track=0, slot=0, parameter="Filter Freq")
    assert nothing["count"] == 0 and "note" in nothing
    everything = run(bridge, "automation.clear", track=0, slot=0, all=True)
    assert everything["count"] == 1 and everything["cleared"][0]["name"] == "Track Volume"
    assert clip0(song).has_envelopes is False
    assert fail(bridge, "automation.clear", track=0, slot=0)["type"] == "bad_args"
    assert fail(bridge, "automation.clear", track=0, slot=0, all=True,
                parameter="volume")["type"] == "bad_args"


def test_overview_state_and_re_enable(bridge, song):
    run(bridge, "automation.write", track=0, slot=1, parameter="Filter Freq",
        points=[[0, 100]])
    freq = operator(song).parameters[2]
    volume = bass(song).mixer_device.volume
    EXT.set_automation_state(freq, 2)
    EXT.set_automation_state(volume, 1)
    EXT.set_re_enable_flag(song, True)
    overview = run(bridge, "automation.overview", track="Bass")
    assert overview["track"] == {"name": "Bass", "path": "song.tracks[0]"}
    assert overview["clip_count"] == 3
    assert overview["clips"] == [{"slot": 1, "name": "Bass Fill",
                                  "path": "song.tracks[0].clip_slots[1].clip",
                                  "envelopes": [{"name": "Filter Freq", "device": "Operator",
                                                 "path": freq_path()}]}]
    assert {(r["name"], r["state"]) for r in overview["automated"]} == \
        {("Filter Freq", "overridden"), ("Track Volume", "playing")}
    assert overview["song"]["re_enable_automation_enabled"] is True
    assert "note" in overview          # the track has an arrangement clip
    full = run(bridge, "automation.overview", track=0, include_empty=True)
    assert len(full["clips"]) == 3
    assert any("arrangement_index" in row for row in full["clips"])

    state = run(bridge, "automation.state", track=0, parameter="Filter Freq")
    assert state["parameter"]["state"] == "overridden" and state["parameter"]["value"] == 800.0
    assert state["song"]["re_enable_automation_enabled"] is True
    by_path = run(bridge, "automation.state", parameter=freq_path())
    assert by_path["parameter"]["name"] == "Filter Freq"
    whole = run(bridge, "automation.state")
    assert [t["name"] for t in whole["tracks"]] == ["Bass"]
    track_state = run(bridge, "automation.state", track=0)
    assert len(track_state["track"]["automated"]) == 2
    everything = run(bridge, "automation.state", track=0, include_all=True)
    assert len(everything["track"]["automated"]) > 20
    assert fail(bridge, "automation.state", parameter="Filter Freq")["type"] == "bad_args"

    track_result = run(bridge, "automation.re_enable", track="Bass")
    assert track_result["scope"] == "track"
    assert [r["name"] for r in track_result["re_enabled"]] == ["Filter Freq"]
    assert track_result["re_enabled"][0]["state"] == "playing"
    EXT.set_automation_state(freq, 2)
    one = run(bridge, "automation.re_enable", track=0, parameter="filter freq")
    assert one["scope"] == "parameter" and one["re_enabled"][0]["state"] == "playing"
    song_result = run(bridge, "automation.re_enable")
    assert song_result == {"scope": "song", "song": {"re_enable_automation_enabled": False,
                                                     "session_automation_record": False}}


def freq_path():
    return "song.tracks[0].devices[0].parameters[2]"


def test_every_automation_command_is_registered(bridge):
    listing = run(bridge, "system.commands", namespace="automation")
    names = {c["cmd"] for c in listing["commands"]}
    assert names == {"automation.list", "automation.overview", "automation.get",
                     "automation.write", "automation.shape", "automation.clear",
                     "automation.state", "automation.re_enable", "automation.copy",
                     "automation.record", "automation.record_status"}
    mutating = {c["cmd"] for c in listing["commands"] if c["mutating"]}
    assert mutating == {"automation.write", "automation.shape", "automation.clear",
                        "automation.re_enable", "automation.copy", "automation.record"}
    for entry in listing["commands"]:
        assert entry["doc"]


def test_live_refusal_becomes_invalid_state(bridge, song, monkeypatch):
    def refuse(self, time, length, value, /):
        raise RuntimeError("no")
    monkeypatch.setattr(Live.Envelope.Envelope, "insert_step", refuse)
    error = fail(bridge, "automation.write", track=0, slot=0, parameter="Resonance",
                 points=[[0, 0.5]])
    assert error["type"] == "invalid_state" and "insert_step" in error["message"]


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


AUTOMATION_TOOLS = {"live_automation_overview", "live_automation_list", "live_automation_get",
                    "live_automation_write", "live_automation_shape", "live_automation_clear",
                    "live_automation_state", "live_automation_copy", "live_automation_record"}


def test_automation_tools_are_registered(fake_app):
    _fake, app = fake_app
    assert "automation" in app.tool_modules
    assert AUTOMATION_TOOLS <= tool_names(app)


def test_automation_tools_forward_arguments(fake_app):
    fake, app = fake_app
    for cmd in ("automation.overview", "automation.list", "automation.get", "automation.write",
                "automation.shape", "automation.clear", "automation.state",
                "automation.re_enable"):
        fake.set_result(cmd, {"cmd": cmd})

    def last():
        return fake.requests[-1]["cmd"], fake.requests[-1].get("args", {})

    assert call_tool(app, "live_automation_overview", {"track": "Bass"}) == \
        {"cmd": "automation.overview"}
    assert last() == ("automation.overview", {"track": "Bass"})
    call_tool(app, "live_automation_list", {"track": 0, "slot": "Intro", "sample": 8})
    assert last() == ("automation.list", {"track": 0, "slot": "Intro", "sample": 8})
    call_tool(app, "live_automation_list", {"clip": " selected ", "include_available": True,
                                            "filter": "freq"})
    assert last() == ("automation.list", {"clip": "selected", "include_available": True,
                                          "filter": "freq", "limit": 200})
    call_tool(app, "live_automation_get", {"track": 0, "slot": 0, "parameter": "volume",
                                           "points": 32, "display": True})
    assert last() == ("automation.get", {"track": 0, "slot": 0, "parameter": "volume",
                                         "points": 32, "display": True})
    call_tool(app, "live_automation_get", {"clip": "song.tracks[0].clip_slots[0].clip",
                                           "parameter": 2, "device": "Operator",
                                           "step": "1/16", "start": "1.1.1"})
    assert last() == ("automation.get", {"clip": "song.tracks[0].clip_slots[0].clip",
                                         "parameter": 2, "device": "Operator",
                                         "step": "1/16", "start": "1.1.1"})
    points = [{"time": 0, "value": "-12 dB"}, [2, 0.85]]
    call_tool(app, "live_automation_write", {"track": "Bass", "slot": 0, "parameter": "volume",
                                             "points": points, "mode": "linear",
                                             "resolution": 8, "clear": True})
    assert last() == ("automation.write", {"track": "Bass", "slot": 0, "parameter": "volume",
                                           "points": points, "mode": "linear",
                                           "resolution": 8, "clear": True})
    call_tool(app, "live_automation_write", {"clip": "Bass Loop", "parameter": "pan",
                                             "points": [[0, 0.5]], "normalized": True})
    assert last() == ("automation.write", {"clip": "Bass Loop", "parameter": "pan",
                                           "points": [[0, 0.5]], "mode": "step",
                                           "normalized": True})
    call_tool(app, "live_automation_shape", {"track": 0, "slot": 0, "parameter": "Cutoff",
                                             "shape": "Square", "period": "1/8", "duty": 0.25,
                                             "low": "-24 dB", "seed": 7})
    assert last() == ("automation.shape", {"track": 0, "slot": 0, "parameter": "Cutoff",
                                           "shape": "square", "period": "1/8", "seed": 7,
                                           "low": "-24 dB", "high": 1.0, "normalized": True,
                                           "duty": 0.25})
    call_tool(app, "live_automation_clear", {"track": 0, "slot": 0, "all": True})
    assert last() == ("automation.clear", {"track": 0, "slot": 0, "all": True})
    call_tool(app, "live_automation_clear", {"track": 0, "slot": 0, "parameter": "volume",
                                             "start": 2})
    assert last() == ("automation.clear", {"track": 0, "slot": 0, "parameter": "volume",
                                           "start": 2.0})
    call_tool(app, "live_automation_state", {"track": "Bass"})
    assert last() == ("automation.state", {"track": "Bass"})
    call_tool(app, "live_automation_state", {"re_enable": True})
    assert last() == ("automation.re_enable", {})
    call_tool(app, "live_automation_state", {"track": 0, "parameter": "volume",
                                             "re_enable": True})
    assert last() == ("automation.re_enable", {"track": 0, "parameter": "volume"})


def test_automation_tools_validate_locally(fake_app):
    fake, app = fake_app
    count = len(fake.requests)
    bad = [
        ("live_automation_overview", {"track": " "}),
        ("live_automation_list", {}),
        ("live_automation_list", {"track": 0}),
        ("live_automation_list", {"track": 0, "slot": 0, "sample": 999}),
        ("live_automation_get", {"track": 0, "slot": 0, "parameter": ""}),
        ("live_automation_get", {"track": 0, "slot": 0, "parameter": "x", "points": 0}),
        ("live_automation_get", {"track": 0, "slot": 0, "parameter": "x", "start": -1}),
        ("live_automation_get", {"track": 0, "slot": 0, "parameter": "x", "step": 0}),
        ("live_automation_write", {"track": 0, "slot": 0, "parameter": "x", "points": []}),
        ("live_automation_write", {"track": 0, "slot": 0, "parameter": "x",
                                   "points": [{"time": 0}]}),
        ("live_automation_write", {"track": 0, "slot": 0, "parameter": "x",
                                   "points": [[0, 1, 2]]}),
        ("live_automation_write", {"track": 0, "slot": 0, "parameter": "x",
                                   "points": [[0, 1]], "mode": "smooth"}),
        ("live_automation_write", {"track": 0, "slot": 0, "parameter": "x",
                                   "points": [[0, 1]], "resolution": 0}),
        ("live_automation_write", {"clip": "", "parameter": "x", "points": [[0, 1]]}),
        ("live_automation_shape", {"track": 0, "slot": 0, "parameter": "x", "shape": "zig"}),
        ("live_automation_shape", {"track": 0, "slot": 0, "parameter": "x", "duty": 1.5}),
        ("live_automation_shape", {"track": 0, "slot": 0, "parameter": "x", "curve": 0}),
        ("live_automation_shape", {"track": 0, "slot": 0, "parameter": "x", "cycles": 0}),
        ("live_automation_shape", {"track": 0, "slot": 0, "parameter": "x", "period": 4,
                                   "cycles": 2}),
        ("live_automation_shape", {"track": 0, "slot": 0, "parameter": "x", "high": 3}),
        ("live_automation_clear", {"track": 0, "slot": 0}),
        ("live_automation_clear", {"track": 0, "slot": 0, "all": True, "parameter": "x"}),
        ("live_automation_clear", {"track": 0, "slot": 0, "all": True, "start": 1}),
        ("live_automation_state", {"parameter": " "}),
        ("live_automation_state", {"re_enable": True, "include_all": True}),
    ]
    for name, args in bad:
        result = call_tool(app, name, args)
        assert isinstance(result, dict) and result.get("type") == "bad_args", (name, result)
    assert len(fake.requests) == count


def test_automation_tool_reports_bridge_errors(fake_app):
    fake, app = fake_app
    fake.set_error("automation.write", "unsupported", "arrangement clips have no envelopes")
    result = call_tool(app, "live_automation_write", {
        "clip": "song.tracks[0].arrangement_clips[0]", "parameter": "volume",
        "points": [[0, 0.5]]})
    assert result["type"] == "unsupported" and "arrangement" in result["error"]


# --------------------------------------------------------------------------
# MCP tools — end to end through the real TCP server and the stub song
# --------------------------------------------------------------------------

def test_automation_tools_end_to_end(tcp_bridge, song):
    client = BridgeClient(host="127.0.0.1", port=tcp_bridge.port, timeout=5.0)
    try:
        app = create_app(client)
        written = call_tool(app, "live_automation_write", {
            "track": "Bass", "slot": 0, "parameter": "Filter Freq",
            "points": [{"time": 0, "value": 200}, {"time": 2, "value": 2000}]})
        assert written["steps"] == 2 and written["created"] is True
        shaped = call_tool(app, "live_automation_shape", {
            "track": "Bass", "slot": 0, "parameter": "volume", "shape": "ramp_up",
            "step": 1})
        assert shaped["steps"] == 4
        got = call_tool(app, "live_automation_get", {"track": 0, "slot": 0,
                                                     "parameter": "Filter Freq", "points": 4})
        assert got["values"] == [200.0, 200.0, 2000.0, 2000.0]
        listing = call_tool(app, "live_automation_list", {"track": 0, "slot": 0})
        assert listing["count"] == 2
        overview = call_tool(app, "live_automation_overview", {"track": "Bass"})
        assert overview["clips"][0]["slot"] == 0
        cleared = call_tool(app, "live_automation_clear", {"track": 0, "slot": 0, "all": True})
        assert cleared["count"] == 2
        state = call_tool(app, "live_automation_state", {"re_enable": True})
        assert state["scope"] == "song"
        unsupported = call_tool(app, "live_automation_write", {
            "clip": "song.tracks[0].arrangement_clips[0]", "parameter": "volume",
            "points": [[0, 0.5]]})
        assert unsupported["type"] == "unsupported"
    finally:
        client.close()


# --------------------------------------------------------------------------
# g2 fixes: square-shape runaway, loop warnings, smooth shapes, multi-clip,
# copy, arrangement clip envelopes, arrangement recording
# --------------------------------------------------------------------------

@pytest.fixture()
def arrangement_live():
    from live_stub_ext import arrangement_live
    uninstall = arrangement_live.install(Live)
    yield
    uninstall()


def test_square_shape_with_a_tiny_period_fails_fast():
    """Regression: shape_steps("square") looped period/span times on Live's main thread."""
    import time as clock
    began = clock.time()
    with pytest.raises(automation_module.BridgeError) as info:
        automation_module.shape_steps("square", 0.0, 4.0, 0.0, 1.0, 1e-9, 0.0, 0.25, 0.5,
                                      1.0, None)
    assert info.value.type == "bad_args" and clock.time() - began < 1.0
    # 1e-6 used to be cut off silently after 2049 steps (covering a sliver of the range)
    with pytest.raises(automation_module.BridgeError):
        automation_module.shape_steps("square", 0.0, 4.0, 0.0, 1.0, 1e-6, 0.0, 0.25, 0.5,
                                      1.0, None)
    ok = automation_module.shape_steps("square", 0.0, 4.0, 0.0, 1.0, 0.5, 0.0, 0.25, 0.5,
                                       1.0, None)
    assert len(ok) == 16 and ok[-1][0] + ok[-1][1] == 4.0


def test_shape_rejects_runaway_periods_and_cycles(bridge, song):
    for args in ({"shape": "square", "period": 1e-9}, {"shape": "square", "cycles": 1e12},
                 {"shape": "sine", "cycles": 1e9}, {"shape": "square", "period": 0.001}):
        error = fail(bridge, "automation.shape", track=0, slot=0, parameter="Resonance",
                     **args)
        assert error["type"] == "bad_args", args
    # many-but-legal cycles still work, fully covering the range
    many = run(bridge, "automation.shape", track=0, slot=0, parameter="Resonance",
               shape="square", period=1.0 / 64)
    assert many["steps"] == 512 and many["range"] == [0.0, 4.0]


def test_write_outside_the_loop_warns_and_extend_clip_grows_it(bridge, song):
    clip = clip0(song)                          # "Bass Loop", loop 0..4
    long = run(bridge, "automation.write", track=0, slot=0, parameter="Resonance",
               points=[[0, 0.0], [16, 1.0]])
    assert long["outside_loop"]["clip_range"] == [0.0, 4.0]
    assert long["outside_loop"]["written"][1] >= 16.0 and "extend_clip" in \
        long["outside_loop"]["note"]
    assert clip.loop_end == 4.0
    inside = run(bridge, "automation.write", track=0, slot=0, parameter="Resonance",
                 points=[[0, 0.0], [2, 1.0]])
    assert "outside_loop" not in inside
    grown = run(bridge, "automation.shape", track=0, slot=0, parameter="Attack",
                shape="ramp_up", start=0, end=32, extend_clip=True)
    assert grown["extended_to"] == 32.0 and clip.loop_end == 32.0
    assert "outside_loop" not in grown
    assert fail(bridge, "automation.write", track=0, slot=0, parameter="Attack",
                points=[[0, 1]], extend_clip="yes")["type"] == "bad_args"


def test_shape_events_mode_is_smooth_and_compact(bridge, song):
    freq = operator(song).parameters[2]
    ramp = run(bridge, "automation.shape", track=0, slot=0, parameter="Filter Freq",
               shape="ramp_up", mode="events", normalized=False, low=100, high=900)
    assert ramp["mode"] == "events" and ramp["steps"] == 2
    env = envelope(song, freq)
    assert [env.value_at_time(t) for t in (0.0, 1.0, 2.0, 3.0)] == \
        pytest.approx([100.0, 300.0, 500.0, 700.0])
    tri = run(bridge, "automation.shape", track=0, slot=0, parameter="Resonance",
              shape="triangle", mode="events", period=2)
    assert tri["steps"] == 5            # 0, peak, 0, peak, 0
    res = envelope(song, operator(song).parameters[3])
    assert res.value_at_time(0.5) == pytest.approx(0.5)
    assert res.value_at_time(1.0) == pytest.approx(1.0)
    square = run(bridge, "automation.shape", track=0, slot=0, parameter="Attack",
                 shape="square", mode="events", period=2, normalized=False, low=1, high=3)
    attack = envelope(song, operator(song).parameters[4])
    assert attack.value_at_time(0.5) == 3.0 and attack.value_at_time(1.5) == 1.0
    assert attack.value_at_time(2.5) == 3.0 and square["steps"] == 8
    sine = automation_module.shape_points("sine", 0.0, 4.0, 0.0, 1.0, 4.0, 0.0, 0.25,
                                          0.5, 1.0, None)
    assert len(sine) == 17 and sine[4] == (1.0, pytest.approx(1.0))
    saw = automation_module.shape_points("saw_up", 0.0, 2.0, 0.0, 1.0, 1.0, 0.0, 0.25,
                                         0.5, 1.0, None)
    assert saw == [(0.0, 0.0), (1.0, 1.0), (1.0, 0.0), (2.0, 1.0)]
    shifted = automation_module.shape_points("triangle", 0.0, 1.0, 0.0, 1.0, 2.0, 0.25,
                                             0.25, 0.5, 1.0, None)
    assert shifted[0] == (0.0, pytest.approx(0.5)) and shifted[1] == (0.5, 1.0)
    assert fail(bridge, "automation.shape", track=0, slot=0, parameter="Resonance",
                mode="smooth")["type"] == "bad_args"


def test_shape_events_keeps_values_outside_the_range(bridge, song):
    run(bridge, "automation.write", track=0, slot=0, parameter="Resonance",
        points=[[0, 0.2], [3, 0.9]])
    run(bridge, "automation.shape", track=0, slot=0, parameter="Resonance",
        shape="ramp_up", mode="events", start=1, end=2)
    env = envelope(song, operator(song).parameters[3])
    assert env.value_at_time(0.5) == pytest.approx(0.2)
    assert env.value_at_time(1.5) == pytest.approx(0.5)
    assert env.value_at_time(3.5) == pytest.approx(0.9)


def test_shape_across_several_clips(bridge, song):
    track = bass(song)
    factory.add_clip(track, slot=2, length=4.0, name="Bass C")
    sweep = run(bridge, "automation.shape", track="Bass", slots=[0, "Verse", 2],
                parameter="Filter Freq", shape="ramp_up", normalized=False, low=0 + 20,
                high=20000, mode="events")
    assert sweep["total_length"] == 16.0 and len(sweep["clips"]) == 3
    assert [c["range"] for c in sweep["clips"]] == [[0.0, 4.0], [0.0, 8.0], [0.0, 4.0]]
    freq = operator(song).parameters[2]
    first = track.clip_slots[0].clip.automation_envelope(freq)
    second = track.clip_slots[1].clip.automation_envelope(freq)
    third = track.clip_slots[2].clip.automation_envelope(freq)
    total = 20000.0 - 20.0
    assert first.value_at_time(0.0) == pytest.approx(20.0)
    assert first.value_at_time(3.99) == pytest.approx(20 + total * 3.99 / 16, rel=1e-3)
    assert second.value_at_time(0.0 + 1e-6) == pytest.approx(20 + total * 4 / 16, rel=1e-3)
    assert third.value_at_time(4.0) == pytest.approx(20000.0)
    stepped = run(bridge, "automation.shape", track="Bass", slots=[0, 2],
                  parameter="Resonance", shape="ramp_up", step=1)
    assert [c["steps"] for c in stepped["clips"]] == [4, 4]
    assert track.clip_slots[2].clip.automation_envelope(
        operator(song).parameters[3]).value_at_time(3.5) == pytest.approx(1.0)
    for args in ({"slots": [0], "clip": "Bass Loop"}, {"slots": [0], "start": 1},
                 {"slots": []}, {"slots": [0, 0]}, {"slots": [0], "extend_clip": True}):
        call = dict(track="Bass", parameter="Resonance", shape="sine")
        call.update(args)
        assert fail(bridge, "automation.shape", **call)["type"] in ("bad_args",), args


def test_copy_envelope_between_clips(bridge, song, arrangement_live):
    """(arrangement_live: Live's +-1576800-beat bound on envelope ranges — the first copy
    version asked for -1e6..1e7 and real Live refused it.)"""
    run(bridge, "automation.write", track=0, slot=0, parameter="Filter Freq",
        points=[[0, 100], [2, 1000]], mode="events")
    copied = run(bridge, "automation.copy", track=0, slot=0, parameter="Filter Freq",
                 targets=[1], shift=4)
    assert copied["count"] == 1 and copied["copied"][0]["range"] == [4.0, 6.0]
    freq = operator(song).parameters[2]
    target = bass(song).clip_slots[1].clip.automation_envelope(freq)
    assert target.value_at_time(5.0) == pytest.approx(550.0)
    stretched = run(bridge, "automation.copy", track=0, slot=0, parameter="Filter Freq",
                    targets=["Bass Fill"], scale=2)
    assert stretched["copied"][0]["range"] == [0.0, 4.0]
    assert target.value_at_time(2.0) == pytest.approx(550.0)
    # onto another track: the parameter is looked up by name there
    vox = song.tracks[1].clip_slots[0].clip
    run(bridge, "automation.write", track=0, slot=0, parameter="volume",
        points=[[0, 0.2], [4, 0.8]], mode="events")
    other = run(bridge, "automation.copy", track=0, slot=0, parameter="volume",
                targets=["song.tracks[1].clip_slots[0].clip"])
    assert other["copied"][0]["parameter"]["path"] == "song.tracks[1].mixer_device.volume"
    assert vox.automation_envelope(song.tracks[1].mixer_device.volume).value_at_time(2.0) \
        == pytest.approx(0.5)
    assert fail(bridge, "automation.copy", track=0, slot=0, parameter="Release",
                targets=[1])["type"] == "not_found"
    assert fail(bridge, "automation.copy", track=0, slot=0, parameter="Filter Freq",
                targets=[0])["type"] == "bad_args"
    assert fail(bridge, "automation.copy", track=0, slot=0, parameter="Filter Freq",
                targets=[])["type"] == "bad_args"


def test_arrangement_copies_keep_editable_envelopes(bridge, song, arrangement_live):
    """Live 12.4.5: duplicate_clip_to_arrangement carries envelopes along; they are listed in
    automation_envelopes (automation_envelope(p) is None) and can be read and edited."""
    run(bridge, "automation.write", track=0, slot=0, parameter="Filter Freq",
        points=[[0, 100], [2, 1000]])
    placed = run(bridge, "arrangement.duplicate_clip", track=0, slot=0, time=16)
    path = placed["path"]
    freq = operator(song).parameters[2]
    arr = bass(song).arrangement_clips[placed["index"]]
    assert arr.automation_envelope(freq) is None and len(arr.automation_envelopes) == 1
    listing = run(bridge, "automation.list", clip=path)
    assert [e["name"] for e in listing["envelopes"]] == ["Filter Freq"]
    got = run(bridge, "automation.get", clip=path, parameter="Filter Freq", points=4)
    assert got["has_envelope"] is True and got["values"] == [100.0, 100.0, 1000.0, 1000.0]
    edited = run(bridge, "automation.write", clip=path, parameter="Filter Freq",
                 points=[[0, 5000]])
    assert edited["created"] is False
    assert arr.automation_envelopes[0].value_at_time(0.5) == 5000.0
    shaped = run(bridge, "automation.shape", clip=path, parameter="Filter Freq",
                 shape="ramp_down", mode="events", clear=True)
    assert shaped["created"] is False and len(arr.automation_envelopes) == 1
    overview = run(bridge, "automation.overview", track="Bass")
    rows = [c for c in overview["clips"] if c.get("arrangement_index") is not None]
    assert rows and rows[0]["envelopes"][0]["name"] == "Filter Freq"
    # a parameter the clip has no envelope for cannot be created there
    error = fail(bridge, "automation.write", clip=path, parameter="Resonance",
                 points=[[0, 0.5]])
    assert error["type"] == "unsupported" and "session clip" in error["message"]
    cleared = run(bridge, "automation.clear", clip=path, parameter="Filter Freq")
    assert cleared["count"] == 1 and len(arr.automation_envelopes) == 0


@pytest.fixture(autouse=True)
def _fresh_recorder():
    """automation.record keeps its running/last pass in module state — isolate each test."""
    automation_module._RECORDER.update(current=None, last=None)
    yield
    automation_module._RECORDER.update(current=None, last=None)


def _record_ticks(bridge, song, start, stop, step=0.25):
    """Play the stub song from ``start`` to ``stop``: one display tick per ``step`` beats."""
    time = start
    while time <= stop + 1e-9:
        song.current_song_time = time
        bridge.update_display()
        time += step


def test_record_arrangement_automation(bridge, song):
    param = operator(song).parameters[2]
    gestures = []
    original_begin, original_end = type(param).begin_gesture, type(param).end_gesture

    def begin(self):
        gestures.append(("begin", song.current_song_time))
        original_begin(self)

    def end(self):
        gestures.append(("end", song.current_song_time))
        original_end(self)

    type(param).begin_gesture, type(param).end_gesture = begin, end
    try:
        drums = song.tracks[2]
        drums.arm = True
        song.loop = True
        song.session_automation_record = False
        song.current_song_time = 2.0
        started = run(bridge, "automation.record", track="Bass", parameter="Filter Freq",
                      points=[["2.1.1", 100], [8, 1000]], preroll=2)
        assert started["phase"] == "arming" and started["range"] == [4.0, 8.0]
        assert started["expected_seconds"] == pytest.approx(6 * 60 / 124.0, abs=0.1)
        assert song.current_song_time == 2.0 and drums.arm is False and song.loop is False
        assert song.session_automation_record is True
        bridge.update_display()                      # arming -> Arrangement Record on
        assert song.record_mode is True and song.is_playing is True
        busy = fail(bridge, "automation.record", track="Bass", parameter="Resonance",
                    points=[[0, 0], [4, 1]])
        assert busy["type"] == "invalid_state"
        seen = []
        time = 2.0
        while time <= 9.0:
            song.current_song_time = time
            bridge.update_display()
            seen.append((time, param.value))
            time += 0.5
        assert dict(seen)[5.0] == pytest.approx(325.0) and dict(seen)[8.0] == 1000.0
        assert dict(seen)[3.0] == 800.0             # untouched during the preroll
        assert gestures == [("begin", 4.0), ("end", 8.0)]
        for _ in range(3):
            bridge.update_display()
        status = run(bridge, "automation.record_status")
        assert status["phase"] == "done" and status["finished"] is True
        assert status["recorded"] == [4.0, 8.0] and status["writes"] >= 8
        assert song.record_mode is False and song.is_playing is False
        assert drums.arm is True and song.loop is True
        assert song.session_automation_record is False and song.current_song_time == 2.0
    finally:
        type(param).begin_gesture, type(param).end_gesture = original_begin, original_end


def test_record_shape_on_the_master_tempo_and_stop(bridge, song):
    tempo = song.master_track.mixer_device.song_tempo
    started = run(bridge, "automation.record", track="master", parameter="tempo",
                  shape="ramp_up", start=0, end=16, normalized=False, low=120, high=140,
                  preroll=0)
    assert started["parameter"]["name"] == "Song Tempo" and started["shape"] == "ramp_up"
    bridge.update_display()
    _record_ticks(bridge, song, 0.0, 8.0, 1.0)
    assert tempo.value == pytest.approx(130.0)
    stopped = run(bridge, "automation.record", action="stop")
    assert stopped["phase"] in ("stopping", "aborted")
    for _ in range(3):
        bridge.update_display()
    status = run(bridge, "automation.record_status")
    assert status["phase"] == "aborted" and "stop" in status["error"]
    assert song.record_mode is False and song.is_playing is False


def test_record_aborts_when_the_transport_stops(bridge, song):
    run(bridge, "automation.record", track=0, parameter="Resonance",
        points=[[0, 0.0], [8, 1.0]], preroll=0)
    bridge.update_display()
    _record_ticks(bridge, song, 0.0, 2.0)
    song.stop_playing()
    for _ in range(4):
        bridge.update_display()
    status = run(bridge, "automation.record_status")
    assert status["phase"] == "aborted" and "stopped" in status["error"]
    assert status["recorded"][0] == 0.0 and status["progress"] == 0.25


def test_record_validation(bridge, song):
    assert run(bridge, "automation.record_status") == {"phase": "idle", "finished": True}
    assert fail(bridge, "automation.record", action="stop")["type"] == "invalid_state"
    bad = [dict(track=0), dict(track=0, parameter="Resonance"),
           dict(track=0, parameter="Resonance", points=[[0, 0]], shape="sine"),
           dict(track=0, parameter="Resonance", shape="sine", start=0),
           dict(track=0, parameter="Resonance", shape="sine", start=4, end=2),
           dict(track=0, parameter="Resonance", shape="sine", start=0, end=8, period="1/32"),
           dict(track=0, parameter="Resonance", points=[[4, 0.5]]),
           dict(track=0, parameter="Resonance", points=[[0, 0], [4, 1]], mode="curvy"),
           dict(track=0, parameter="Resonance", points=[[0, 0], [4, 1]], preroll=99),
           dict(track=0, parameter="Resonance", points=[[0, 0], [8192, 1]]),
           dict(action="rewind")]
    for args in bad:
        assert fail(bridge, "automation.record", **args)["type"] == "bad_args", args
    song.start_playing()
    assert fail(bridge, "automation.record", track=0, parameter="Resonance",
                points=[[0, 0], [4, 1]])["type"] == "invalid_state"
    song.stop_playing()
    song.record_mode = True
    song.stop_playing()
    assert fail(bridge, "automation.record", track=0, parameter="Resonance",
                points=[[0, 0], [4, 1]])["type"] == "invalid_state"
    song.record_mode = False
    song.stop_playing()
    beyond = fail(bridge, "automation.record", track=0, parameter="Resonance",
                  points=[[song.song_length + 64, 0], [song.song_length + 68, 1]])
    assert beyond["type"] == "invalid_state" and "song ends" in beyond["message"]
    disabled = operator(song).parameters[3]
    disabled._set_enabled(False)
    assert fail(bridge, "automation.record", track=0, parameter="Resonance",
                points=[[0, 0], [4, 1]])["type"] == "invalid_state"


def test_new_automation_tools_forward_and_validate(fake_app):
    fake, app = fake_app
    for cmd in ("automation.copy", "automation.record", "automation.record_status",
                "automation.shape", "automation.write"):
        fake.set_result(cmd, {"cmd": cmd})

    def last():
        return fake.requests[-1]["cmd"], fake.requests[-1].get("args", {})

    call_tool(app, "live_automation_write", {"track": 0, "slot": 0, "parameter": "x",
                                             "points": [[0, 1]], "extend_clip": True})
    assert last()[1]["extend_clip"] is True
    call_tool(app, "live_automation_shape", {"track": "Bass", "slots": [1, "Verse"],
                                             "parameter": "Cutoff", "mode": "events"})
    assert last() == ("automation.shape", {"track": "Bass", "slots": [1, "Verse"],
                                           "parameter": "Cutoff", "shape": "sine",
                                           "low": 0.0, "high": 1.0, "normalized": True,
                                           "mode": "events"})
    call_tool(app, "live_automation_copy", {"track": 0, "slot": 0, "parameter": "volume",
                                            "targets": [1, 2], "shift": 4, "clear": False})
    assert last() == ("automation.copy", {"track": 0, "slot": 0, "parameter": "volume",
                                          "targets": [1, 2], "shift": 4.0, "clear": False})
    call_tool(app, "live_automation_record", {"track": "master", "parameter": "tempo",
                                              "shape": "Ramp_Up", "start": "17.1.1",
                                              "end": "25.1.1", "low": 120, "high": 128,
                                              "normalized": False})
    assert last() == ("automation.record", {"track": "master", "parameter": "tempo",
                                            "start": "17.1.1", "end": "25.1.1",
                                            "normalized": False, "shape": "ramp_up",
                                            "low": 120.0, "high": 128.0})
    call_tool(app, "live_automation_record", {"action": "status"})
    assert last() == ("automation.record_status", {})
    call_tool(app, "live_automation_record", {"action": "stop"})
    assert last() == ("automation.record", {"action": "stop"})
    count = len(fake.requests)
    bad = [
        ("live_automation_shape", {"track": 0, "slots": [], "parameter": "x"}),
        ("live_automation_shape", {"slots": [1], "parameter": "x"}),
        ("live_automation_shape", {"track": 0, "slots": [1], "slot": 0, "parameter": "x"}),
        ("live_automation_shape", {"track": 0, "slot": 0, "parameter": "x", "mode": "curvy"}),
        ("live_automation_copy", {"track": 0, "slot": 0, "parameter": "x", "targets": []}),
        ("live_automation_copy", {"track": 0, "slot": 0, "parameter": "x", "targets": [1],
                                  "scale": 0}),
        ("live_automation_record", {"track": 0}),
        ("live_automation_record", {"parameter": "volume", "points": [[0, 1]]}),
        ("live_automation_record", {"track": 0, "parameter": "volume"}),
        ("live_automation_record", {"track": 0, "parameter": "volume", "shape": "sine"}),
        ("live_automation_record", {"track": 0, "parameter": "volume", "shape": "zig",
                                    "start": 0, "end": 4}),
        ("live_automation_record", {"track": 0, "parameter": "volume", "points": [[0, 1]],
                                    "preroll": 20}),
        ("live_automation_record", {"action": "pause"}),
    ]
    for name, args in bad:
        result = call_tool(app, name, args)
        assert isinstance(result, dict) and result.get("type") == "bad_args", (name, result)
    assert len(fake.requests) == count
