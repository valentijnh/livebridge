"""Module B — mixer: dB/pan parsing, every ``mixer.*`` command through the dispatcher and
every ``live_mixer_*`` MCP tool (fake bridge + end-to-end through the TCP bridge)."""

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

from LiveBridge.handlers import mixer as mixer_module  # noqa: E402
from LiveBridge.registry import BridgeError  # noqa: E402
from livebridge_mcp.client import BridgeClient  # noqa: E402
from livebridge_mcp.server import create_app  # noqa: E402


def call(bridge, cmd, **args):
    return bridge.dispatch({"id": "m", "cmd": cmd, "args": args})


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


# --------------------------------------------------------------------------
# conversions (reference points measured on Live 12.4.5 with str_for_value)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("value,db", [
    (1.0, 6.0), (0.95, 4.0), (0.85, 0.0), (0.7, -6.0), (0.5, -14.0), (0.4, -18.0),
    (0.3, -24.2), (0.2, -34.4), (0.1, -48.6), (0.05, -57.2), (0.03, -60.92),
    (0.02, -63.373), (0.01, -66.4), (0.001, -69.565),
])
def test_volume_curve_matches_live(value, db):
    assert mixer_module.volume_to_db(value) == pytest.approx(db, abs=0.01)
    assert mixer_module.db_to_volume(db) == pytest.approx(value, abs=1e-4)


@pytest.mark.parametrize("value,db", [
    (1.0, 0.0), (0.85, -6.0), (0.5, -20.0), (0.4, -24.0), (0.3, -30.2), (0.1, -54.6),
    (0.05, -63.2), (0.03, -66.92), (0.02, -68.373), (0.005, -69.695),
])
def test_send_curve_matches_live(value, db):
    assert mixer_module.volume_to_db(value, send=True) == pytest.approx(db, abs=0.01)
    assert mixer_module.db_to_volume(db, send=True) == pytest.approx(value, abs=1e-4)


def test_curve_edges():
    assert mixer_module.volume_to_db(0.0) == float("-inf")
    assert mixer_module.volume_to_db(0.0004) == float("-inf")
    assert mixer_module.volume_to_db(0.003, send=True) == float("-inf")
    assert mixer_module.db_to_volume(12.0) == 1.0
    assert mixer_module.db_to_volume(-90.0) == 0.0
    assert mixer_module.db_to_volume(float("-inf")) == 0.0
    assert mixer_module.db_value(float("-inf")) == "-inf"
    assert mixer_module.db_value(-6.004) == -6.0


@pytest.mark.parametrize("text,expected", [
    (0.5, 0.5), ("0.7", 0.7), ("-6 dB", 0.7), ("-6dB", 0.7), ("0 dB", 0.85), ("unity", 0.85),
    ("+6 dB", 1.0), ("-inf", 0.0), ("50%", 0.5), ("-14 DB", 0.5),
])
def test_parse_level_absolute(text, expected):
    assert mixer_module.parse_level(text, 0.85) == pytest.approx(expected, abs=1e-4)


def test_parse_level_relative_and_errors():
    assert mixer_module.parse_level("+3", 0.7) == pytest.approx(0.775, abs=1e-4)
    assert mixer_module.parse_level("-6", 0.85) == pytest.approx(0.7, abs=1e-4)
    assert mixer_module.parse_level("+20", 0.85) == 1.0
    assert mixer_module.parse_level("+3", 0.0) > 0.0
    for bad in (-6, 1.5, "loud", True, None, "150%", [1]):
        with pytest.raises(BridgeError) as info:
            mixer_module.parse_level(bad, 0.85)
        assert info.value.type == "bad_args"


@pytest.mark.parametrize("text,expected", [
    (0.25, 0.25), ("C", 0.0), ("center", 0.0), ("L50", -1.0), ("50L", -1.0), ("R20", 0.4),
    ("25 R", 0.5), ("left", -1.0), ("right", 1.0), ("-0.5", -0.5),
])
def test_parse_pan(text, expected):
    assert mixer_module.parse_pan(text) == pytest.approx(expected)


def test_parse_pan_errors_and_crossfader():
    for bad in (2, "L60", "sideways", True):
        with pytest.raises(BridgeError):
            mixer_module.parse_pan(bad)
    assert mixer_module.parse_crossfader("A") == -1.0
    assert mixer_module.parse_crossfader("25B") == 0.5
    assert mixer_module.parse_crossfader("a25") == -0.5
    assert mixer_module.parse_crossfader("center") == 0.0
    assert mixer_module.parse_crossfader(0.3) == 0.3
    assert mixer_module.parse_crossfade_assign("b") == 2
    assert mixer_module.parse_crossfade_assign(0) == 0
    for bad in (5, "left"):
        with pytest.raises(BridgeError):
            mixer_module.parse_crossfade_assign(bad)
    with pytest.raises(BridgeError):
        mixer_module.parse_crossfader("sideways")


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def test_get_one_track(bridge):
    result = ok(bridge, "mixer.get", track="Bass")
    assert result["volume"]["value"] == 0.85 and result["volume"]["db"] == 0.0
    assert result["pan"]["value"] == 0.0 and result["active"] is True
    assert [s["letter"] for s in result["sends"]] == ["A", "B"]
    assert result["sends"][0]["return"] == "A-Reverb" and result["sends"][0]["db"] == "-inf"
    assert result["crossfade"] == "none"


def test_get_whole_mixer_minimal_and_full(bridge):
    whole = ok(bridge, "mixer.get", detail="minimal")
    assert [t["name"] for t in whole["tracks"]] == ["Bass", "Vocals", "Drums"]
    assert whole["tracks"][0]["volume"] == 0.0
    assert whole["master"]["type"] == "master"
    assert whole["master"]["cue_volume"] == pytest.approx(-6.0, abs=0.01)
    assert "crossfader" in whole["master"] and "mute" not in whole["master"]
    full = ok(bridge, "mixer.get", track="Vocals", detail="full")
    assert full["panning_mode"] == "stereo"
    assert full["parameter_paths"]["volume"] == "song.tracks[1].mixer_device.volume"
    several = ok(bridge, "mixer.get", track=["Bass", "A"])
    assert [t["name"] for t in several["tracks"]] == ["Bass", "A-Reverb"]
    assert err(bridge, "mixer.get", detail="loud")[0] == "bad_args"


def test_set_volume_pan_sends(bridge, song):
    result = ok(bridge, "mixer.set", track="Bass", volume="-6 dB", pan="L20",
                sends={"A": "-12 dB", "Delay": 0.5})
    mixer = song.tracks[0].mixer_device
    assert mixer.volume.value == pytest.approx(0.7, abs=1e-4)
    assert mixer.panning.value == pytest.approx(-0.4)
    assert mixer.sends[0].value == pytest.approx(0.7, abs=1e-4)
    assert mixer.sends[1].value == 0.5
    assert result["volume"]["db"] == pytest.approx(-6.0, abs=0.01)
    assert result["changed"] == ["volume", "pan", "sends"]
    ok(bridge, "mixer.set", track="Bass", volume="+3")
    assert mixer.volume.value == pytest.approx(0.775, abs=1e-4)
    ok(bridge, "mixer.set", track="Bass", sends=[0.1, None])
    assert mixer.sends[0].value == 0.1 and mixer.sends[1].value == 0.5
    ok(bridge, "mixer.set", track="Bass", sends=[{"send": 1, "value": "0 dB"}])
    assert mixer.sends[1].value == 1.0
    ok(bridge, "mixer.set", track="Bass", sends={"b": "-inf", "0": 0.2})
    assert mixer.sends[1].value == 0.0 and mixer.sends[0].value == 0.2


def test_set_activator_crossfade_mute_solo_panning_mode(bridge, song):
    ok(bridge, "mixer.set", track="Vocals", activator=False, crossfade="B", mute=True,
       solo="toggle", panning_mode="split_stereo")
    track = song.tracks[1]
    assert track.mixer_device.track_activator.value == 0.0
    assert track.mixer_device.crossfade_assign == 2
    assert track.mute is True and track.solo is True
    assert track.mixer_device.panning_mode == 1
    full = ok(bridge, "mixer.get", track="Vocals", detail="full")
    assert "left_split_stereo" in full and full["active"] is False and full["crossfade"] == "B"
    ok(bridge, "mixer.set", track="Vocals", activator="toggle")
    assert track.mixer_device.track_activator.value == 1.0


def test_set_errors(bridge):
    assert err(bridge, "mixer.set", track="Bass")[0] == "bad_args"
    assert err(bridge, "mixer.set", track="Bass", volume=-6)[0] == "bad_args"
    assert err(bridge, "mixer.set", track="Bass", pan="L80")[0] == "bad_args"
    assert err(bridge, "mixer.set", track="Bass", sends={"Z": 0.5})[0] == "not_found"
    assert err(bridge, "mixer.set", track="Bass", sends={"Nope": 0.5})[0] == "not_found"
    assert err(bridge, "mixer.set", track="Bass", sends="A")[0] == "bad_args"
    assert err(bridge, "mixer.set", track="master", crossfade="A")[0] == "invalid_state"
    assert err(bridge, "mixer.set", track="master", mute=True)[0] == "invalid_state"
    assert err(bridge, "mixer.set", track="Bass", cue_volume=0.5)[0] == "invalid_state"
    assert err(bridge, "mixer.set", track="Bass", panning_mode="wide")[0] == "bad_args"


def test_sends_need_return_tracks(bridge, song):
    song.delete_return_track(0)
    song.delete_return_track(0)
    kind, message = err(bridge, "mixer.set", track="Bass", sends={"A": 0.5})
    assert kind == "invalid_state" and "no sends" in message


def test_set_many_is_one_undo_step(bridge, song):
    before = len(song._undo_steps)
    result = ok(bridge, "mixer.set_many", settings=[
        {"track": "Bass", "volume": "-3 dB", "pan": "R10"},
        {"track": "Vocals", "sends": {"A": "-12 dB"}, "mute": True},
        {"track": "Nope", "volume": 0.5},
        {"track": "Drums", "loudness": 3},
        {"volume": 0.2},
        {"track": "master", "volume": "-1 dB", "cue_volume": "0 dB", "crossfader": "A"},
    ])
    assert len(song._undo_steps) == before + 1
    assert result["applied"] == 3 and result["failed"] == 3
    assert result["results"][0]["volume_db"] == pytest.approx(-3.0, abs=0.01)
    assert result["results"][2]["ok"] is False and "Nope" in result["results"][2]["error"]
    assert "loudness" in result["results"][3]["error"]
    assert song.tracks[1].mute is True
    master = song.master_track.mixer_device
    assert master.cue_volume.value == pytest.approx(0.85, abs=1e-4)
    assert master.crossfader.value == -1.0


def test_set_many_stop_on_error(bridge, song):
    result = ok(bridge, "mixer.set_many", stop_on_error=True, settings=[
        {"track": "Nope", "volume": 0.5}, {"track": "Bass", "volume": 0.5}])
    assert result["applied"] == 0 and len(result["results"]) == 1
    assert song.tracks[0].mixer_device.volume.value == 0.85
    assert err(bridge, "mixer.set_many", settings=[])[0] == "bad_args"


def test_master(bridge, song):
    read = ok(bridge, "mixer.master")
    assert read["type"] == "master" and read["changed"] == []
    result = ok(bridge, "mixer.master", volume="-2 dB", cue_volume=0.5, crossfader="25B",
                pan="C")
    master = song.master_track.mixer_device
    assert master.volume.value == pytest.approx(0.8, abs=1e-4)
    assert master.cue_volume.value == 0.5 and master.crossfader.value == 0.5
    assert set(result["changed"]) == {"volume", "pan", "cue_volume", "crossfader"}


def test_reset(bridge, song):
    ok(bridge, "mixer.set", track="Bass", volume=0.3, pan=0.5, sends={"A": 0.9},
       activator=False, crossfade="A", mute=True)
    result = ok(bridge, "mixer.reset", track="Bass")
    mixer = song.tracks[0].mixer_device
    assert mixer.volume.value == 0.85 and mixer.panning.value == 0.0
    assert mixer.sends[0].value == 0.0 and mixer.track_activator.value == 1.0
    assert mixer.crossfade_assign == 1
    assert song.tracks[0].mute is True  # mute is only reset when asked
    assert "volume" in result["reset"][0]["done"]
    ok(bridge, "mixer.reset", track="Bass", what=["mute"])
    assert song.tracks[0].mute is False
    ok(bridge, "mixer.set", track="Drums", volume=0.1, solo=True)
    everything = ok(bridge, "mixer.reset", what="all")
    assert song.tracks[2].mixer_device.volume.value == 0.85 and song.tracks[2].solo is False
    assert [r["track"] for r in everything["reset"]][-1] == "Main"
    assert err(bridge, "mixer.reset", what=["loudness"])[0] == "bad_args"


def test_mixer_commands_registered(bridge):
    names = {c["cmd"] for c in ok(bridge, "system.commands", namespace="mixer")["commands"]}
    assert names == {"mixer.get", "mixer.set", "mixer.set_many", "mixer.master",
                     "mixer.reset", "mixer.meters"}


# --------------------------------------------------------------------------
# MCP tools
# --------------------------------------------------------------------------

MIXER_TOOLS = {"live_mixer_get", "live_mixer_set", "live_mixer_set_many", "live_mixer_master",
               "live_mixer_reset", "live_mixer_meters"}


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


def test_mixer_tools_registered(fake_app):
    app, _fake = fake_app
    tools = {t.name: t for t in asyncio.run(app.list_tools())}
    assert MIXER_TOOLS <= set(tools)
    assert all("Returns" in (tools[n].description or "") for n in MIXER_TOOLS)


def test_mixer_tools_forward_arguments(fake_app):
    app, fake = fake_app
    for cmd in ("mixer.get", "mixer.set", "mixer.set_many", "mixer.master", "mixer.reset"):
        fake.set_result(cmd, {"ok": cmd})
    assert call_tool(app, "live_mixer_get", {}) == {"ok": "mixer.get"}
    assert _last(fake, "mixer.get") == {"detail": "summary"}
    call_tool(app, "live_mixer_set", {"track": "Bass", "volume": "-6 dB", "pan": "L20",
                                      "sends": {"A": "-12 dB"}, "crossfade": "A"})
    assert _last(fake, "mixer.set") == {"track": "Bass", "volume": "-6 dB", "pan": "L20",
                                        "sends": {"A": "-12 dB"}, "crossfade": "A"}
    settings = [{"track": "Bass", "volume": 0.5}]
    call_tool(app, "live_mixer_set_many", {"settings": settings})
    assert _last(fake, "mixer.set_many") == {"settings": settings, "stop_on_error": False}
    call_tool(app, "live_mixer_master", {"cue_volume": "-3 dB"})
    assert _last(fake, "mixer.master") == {"cue_volume": "-3 dB"}
    call_tool(app, "live_mixer_reset", {"track": ["Bass"], "what": ["volume"]})
    assert _last(fake, "mixer.reset") == {"track": ["Bass"], "what": ["volume"]}


@pytest.mark.parametrize("name,args", [
    ("live_mixer_get", {"detail": "huge"}),
    ("live_mixer_set", {"track": "Bass"}),
    ("live_mixer_set", {"track": "Bass", "volume": -6}),
    ("live_mixer_set", {"track": "Bass", "pan": 3}),
    ("live_mixer_set", {"track": "Bass", "crossfade": "C"}),
    ("live_mixer_set", {"track": "Bass", "panning_mode": "wide"}),
    ("live_mixer_set_many", {"settings": []}),
    ("live_mixer_set_many", {"settings": [{"volume": 1}]}),
    ("live_mixer_set_many", {"settings": [{"track": 0, "loud": 1}]}),
    ("live_mixer_master", {"volume": 4}),
    ("live_mixer_reset", {"what": ["everything"]}),
    ("live_mixer_meters", {"seconds": 11}),
    ("live_mixer_meters", {"seconds": -1}),
])
def test_mixer_tools_validate_locally(fake_app, name, args):
    app, fake = fake_app
    result = call_tool(app, name, args)
    assert result["type"] == "bad_args", result
    assert not [r for r in fake.requests if r["cmd"].startswith("mixer.")]


def test_mixer_tools_end_to_end(tcp_bridge, song):
    client = BridgeClient(host="127.0.0.1", port=tcp_bridge.port, timeout=5.0)
    try:
        app = create_app(client)
        result = call_tool(app, "live_mixer_set", {"track": "Bass", "volume": "-6 dB",
                                                   "sends": {"Reverb": "-12 dB"}})
        assert result["volume"]["db"] == pytest.approx(-6.0, abs=0.01)
        many = call_tool(app, "live_mixer_set_many", {"settings": [
            {"track": "Drums", "pan": "R10"}, {"track": "Vocals", "volume": "+2"}]})
        assert many["applied"] == 2
        assert call_tool(app, "live_mixer_get", {"track": "Drums"})["pan"]["value"] == 0.2
        assert call_tool(app, "live_mixer_master", {"volume": "0 dB"})["volume"]["db"] == 0.0
        reset = call_tool(app, "live_mixer_reset", {"track": "Bass"})
        assert reset["reset"][0]["track"] == "Bass"
        assert song.tracks[0].mixer_device.volume.value == 0.85
    finally:
        client.close()


# --------------------------------------------------------------------------
# meters (g2): the only audio feedback in the API
# --------------------------------------------------------------------------

@pytest.fixture()
def meters():
    import Live
    from live_stub_ext import mixer_meters
    ext = mixer_meters.install(Live)
    yield ext
    ext.uninstall()


def test_meters_snapshot(bridge, song, meters):
    bass, vocals = song.tracks[0], song.tracks[1]
    meters.set_meters(bass, 0.7, 0.72, 0.68)
    meters.set_meters(vocals, 0.0, 0.0, 0.0, input_level=0.4, input_left=0.4, input_right=0.35)
    meters.set_meters(song.tracks[2], 0.3)                  # MIDI-only output
    meters.set_meters(song.master_track, 0.9, 0.95, 0.91)
    meters.set_cpu(3.5, 7.25)
    song.start_playing()
    snap = ok(bridge, "mixer.meters")
    assert snap["playing"] is True and snap["cpu"] == {"average": 3.5, "peak": 7.25}
    rows = {r["name"]: r for r in snap["tracks"]}
    assert rows["Bass"] == {"name": "Bass", "type": "midi", "level": 0.7, "left": 0.72,
                           "right": 0.68, "peak": 0.72, "audio": True}
    assert rows["Drums"]["audio"] is False and "left" not in rows["Drums"]
    assert rows["Drums"]["peak"] == 0.3
    assert [r["name"] for r in snap["returns"]] == ["A-Reverb", "B-Delay"]
    assert snap["master"]["peak"] == 0.95
    one = ok(bridge, "mixer.meters", track="Vocals", include_input=True, include_cpu=False,
             include_impact=True)
    assert set(one) == {"playing", "tracks"}
    assert one["tracks"][0]["input"] == {"level": 0.4, "left": 0.4, "right": 0.35}
    assert one["tracks"][0]["performance_impact"] == 0.0
    listed = ok(bridge, "mixer.meters", track=["Bass", "master"])
    assert [r["name"] for r in listed["tracks"]] == ["Bass", "Main"]
    assert err(bridge, "mixer.meters", include_input="yes")[0] == "bad_args"


def test_aggregate_meters_flags_hot_and_silent():
    from livebridge_mcp.tools import mixer as mixer_tools
    snaps = [
        {"playing": True, "cpu": {"average": 2.0, "peak": 3.0},
         "tracks": [{"name": "Kick", "type": "audio", "level": 0.5, "peak": 0.6},
                    {"name": "Pad", "type": "midi", "level": 0.0, "peak": 0.0}],
         "returns": [], "master": {"name": "Main", "type": "master", "level": 0.8,
                                   "peak": 0.99}},
        {"playing": True, "cpu": {"average": 4.0, "peak": 5.0},
         "tracks": [{"name": "Kick", "type": "audio", "level": 0.7, "peak": 1.0,
                     "input": {"level": 0.2}},
                    {"name": "Pad", "type": "midi", "level": 0.0, "peak": 0.0}],
         "returns": [], "master": {"name": "Main", "type": "master", "level": 0.6,
                                   "peak": 0.7}},
    ]
    result = mixer_tools.aggregate_meters(snaps, 1.0)
    kick, pad = result["tracks"]
    assert kick == {"name": "Kick", "type": "audio", "peak": 1.0, "avg": 0.6,
                    "input_peak": 0.2, "hot": True}
    assert pad["silent"] is True and result["master"]["hot"] is True
    assert result["cpu"] == {"average": 3.0, "peak": 5.0} and result["samples"] == 2
    assert any("clipping" in w for w in result["warnings"])
    assert any("no signal: Pad" in w for w in result["warnings"])
    stopped = mixer_tools.aggregate_meters([dict(snaps[0], playing=False)], 0.0)
    assert "stopped" in stopped["warnings"][0]


def test_meters_tool_snapshot_and_sampling(fake_app):
    app, fake = fake_app
    fake.set_result("mixer.meters", {"playing": True, "tracks": [
        {"name": "Bass", "type": "midi", "level": 0.5, "peak": 0.5}], "returns": [],
        "master": None, "cpu": {"average": 1.0, "peak": 2.0}})
    snap = call_tool(app, "live_mixer_meters", {"track": "Bass", "include_input": True})
    assert snap["tracks"][0]["peak"] == 0.5
    assert _last(fake, "mixer.meters") == {"track": "Bass", "include_input": True}
    before = len([r for r in fake.requests if r["cmd"] == "mixer.meters"])
    sampled = call_tool(app, "live_mixer_meters", {"seconds": 0.35})
    count = len([r for r in fake.requests if r["cmd"] == "mixer.meters"]) - before
    assert 3 <= count <= 6 and sampled["samples"] == count
    assert sampled["tracks"][0] == {"name": "Bass", "type": "midi", "peak": 0.5, "avg": 0.5}


def test_meters_tool_end_to_end(tcp_bridge, song, meters):
    meters.set_meters(song.tracks[0], 0.4, 0.4, 0.41)
    client = BridgeClient(host="127.0.0.1", port=tcp_bridge.port, timeout=5.0)
    try:
        app = create_app(client)
        result = call_tool(app, "live_mixer_meters", {"seconds": 0.2})
        assert result["tracks"][0]["name"] == "Bass" and result["tracks"][0]["peak"] == 0.41
        assert "cpu" in result and result["samples"] >= 2
    finally:
        client.close()
