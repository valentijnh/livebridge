"""Module B — routing: every ``routing.*`` command through the dispatcher (with
``tests/live_stub_ext/routing.py`` giving the stub Live 12.4.5's track-to-track routing lists)
and every ``live_routing_*`` MCP tool (fake bridge + end-to-end through the TCP bridge)."""

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

import Live  # noqa: E402
from fake_bridge import FakeBridge  # noqa: E402
from live_stub import factory  # noqa: E402
from live_stub_ext import routing as routing_ext  # noqa: E402

from LiveBridge.handlers import routing as routing_module  # noqa: E402
from LiveBridge.registry import BridgeError  # noqa: E402
from livebridge_mcp.client import BridgeClient  # noqa: E402
from livebridge_mcp.server import create_app  # noqa: E402


def call(bridge, cmd, **args):
    return bridge.dispatch({"id": "r", "cmd": cmd, "args": args})


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
def ext():
    """Live 12.4.5-like routing lists on the stub, restored afterwards."""
    uninstall = routing_ext.install(Live)
    try:
        yield routing_ext
    finally:
        uninstall()


def names(entries):
    return [e["name"] for e in entries]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def test_match_option_rules(song):
    options = song.tracks[1].available_input_routing_types  # Ext. In, Resampling, No Input
    assert routing_module.match_option(options, "ext. in", "t").display_name == "Ext. In"
    assert routing_module.match_option(options, "res", "t").display_name == "Resampling"
    assert routing_module.match_option(options, "input", "t").display_name == "No Input"
    assert routing_module.match_option(options, 1, "t").display_name == "Resampling"
    with pytest.raises(BridgeError) as info:
        routing_module.match_option(options, "in", "t")
    assert info.value.type == "bad_args"
    with pytest.raises(BridgeError) as info:
        routing_module.match_option(options, "Guitar", "t")
    assert info.value.type == "not_found" and "Resampling" in info.value.message
    with pytest.raises(BridgeError):
        routing_module.match_option(options, 7, "t")
    outputs = song.tracks[1].available_output_routing_types  # stub: Master, Sends Only
    assert routing_module.match_option(outputs, "main", "t").display_name == "Master"


def test_parse_monitoring():
    assert routing_module.parse_monitoring("IN") == 0
    assert routing_module.parse_monitoring("auto") == 1
    assert routing_module.parse_monitoring(2) == 2
    with pytest.raises(BridgeError):
        routing_module.parse_monitoring("loud")


# --------------------------------------------------------------------------
# commands on the plain stub
# --------------------------------------------------------------------------

def test_get_audio_track(bridge):
    result = ok(bridge, "routing.get", track="Vocals")
    assert result["input"] == {"type": "Ext. In", "channel": "1/2", "category": "external"}
    assert result["output"]["type"] == "Master" and result["monitoring"] == "auto"
    assert names(result["available"]["input_types"]) == ["Ext. In", "Resampling", "No Input"]
    assert names(result["available"]["input_channels"]) == ["1/2", "1", "2"]
    assert result["available"]["input_channels"][1]["layout"] == "mono"
    slim = ok(bridge, "routing.get", track="Vocals", include_available=False)
    assert "available" not in slim


def test_get_return_and_master(bridge):
    ret = ok(bridge, "routing.get", track="A")
    assert ret["input"] is None and ret["monitoring"] is None and ret["output"] is not None
    assert "input_types" not in ret["available"] and "output_types" in ret["available"]
    master = ok(bridge, "routing.get", track="master")
    assert master["input"] is None and master["output"] is None and master["available"] == {}


def test_set_by_name_and_monitoring(bridge, song):
    result = ok(bridge, "routing.set", track="Vocals", input_type="resampling",
                monitoring="in")
    assert result["input"]["type"] == "Resampling" and result["monitoring"] == "in"
    assert result["changed"] == ["input_type", "monitoring"]
    ok(bridge, "routing.set", track="Vocals", input_type="Ext", input_channel="2")
    assert song.tracks[1].input_routing_channel.display_name == "2"
    ok(bridge, "routing.set", track="Bass", input_channel="Ch. 3", output_type="Sends")
    assert song.tracks[0].input_routing_channel.display_name == "Ch. 3"
    assert song.tracks[0].output_routing_type.display_name == "Sends Only"
    ok(bridge, "routing.set", track="Bass", output_type="master", output_channel=0)
    assert song.tracks[0].output_routing_type.display_name == "Master"


def test_set_errors(bridge):
    assert err(bridge, "routing.set", track="Vocals")[0] == "bad_args"
    kind, message = err(bridge, "routing.set", track="Vocals", input_type="Guitar")
    assert kind == "not_found" and "Ext. In" in message
    assert err(bridge, "routing.set", track="A", input_type="Ext. In")[0] == "invalid_state"
    assert err(bridge, "routing.set", track="A", monitoring="in")[0] == "invalid_state"
    assert err(bridge, "routing.set", track="master", output_type="Main")[0] == "invalid_state"
    assert err(bridge, "routing.set", track="Vocals", monitoring="loud")[0] == "bad_args"


def test_set_is_one_undo_step(bridge, song):
    before = len(song._undo_steps)
    ok(bridge, "routing.set", track="Vocals", input_type="No Input", monitoring="off")
    assert len(song._undo_steps) == before + 1


def test_summary(bridge, song):
    song.tracks[1].arm = True
    rows = ok(bridge, "routing.summary")["tracks"]
    assert [r["name"] for r in rows] == ["Bass", "Vocals", "Drums", "A-Reverb", "B-Delay",
                                         "Main"]
    assert rows[1]["in"] == "Ext. In | 1/2" and rows[1]["arm"] is True
    assert rows[3]["in"] is None and rows[5]["out"] is None
    only = ok(bridge, "routing.summary", include_returns=False, include_master=False)
    assert len(only["tracks"]) == 3


# --------------------------------------------------------------------------
# Live 12.4.5-like routing (extension)
# --------------------------------------------------------------------------

def test_ext_lists_other_tracks(bridge, song, ext):
    factory.add_track(song, "Guitar", "audio")
    result = ok(bridge, "routing.get", track="Vocals")
    inputs = result["available"]["input_types"]
    assert names(inputs) == ["Ext. In", "Resampling", "Guitar", "A-Reverb", "B-Delay", "Main",
                             "No Input"]
    guitar = [e for e in inputs if e["name"] == "Guitar"][0]
    assert guitar["category"] == "track" and guitar["track"] == "song.tracks[3]"
    assert result["output"]["type"] == "Main"
    ok(bridge, "routing.set", track="Vocals", input_type="Guitar", input_channel="post mixer")
    assert song.tracks[1].input_routing_channel.display_name == "Post Mixer"


def test_route_input_resampling_style(bridge, song, ext):
    factory.add_track(song, "Resample", "audio")
    result = ok(bridge, "routing.route", source="Vocals", destination="Resample",
                channel="Post FX", monitoring="in")
    assert result["method"] == "input"
    assert result["routing"]["input"] == {"type": "Vocals", "channel": "Post FX",
                                          "category": "track"}
    assert song.tracks[3].current_monitoring_state == 0
    assert "notes" not in result
    quiet = ok(bridge, "routing.route", source="Vocals", destination="Resample",
               monitoring="auto")
    assert "not armed" in quiet["notes"][0]
    assert err(bridge, "routing.route", source="Vocals", destination="Resample",
               device=0)[0] == "bad_args"


def test_route_master_into_track_uses_main(bridge, song, ext):
    factory.add_track(song, "Print", "audio")
    result = ok(bridge, "routing.route", source="master", destination="Print")
    assert result["routing"]["input"]["type"] == "Main"


def test_route_output_bus(bridge, song, ext):
    factory.add_track(song, "Bus", "audio")
    result = ok(bridge, "routing.route", source="Vocals", destination="Bus", method="output",
                channel="Track In")
    assert result["routing"]["output"]["type"] == "Bus"
    assert song.tracks[1].output_routing_type.display_name == "Bus"


def test_route_midi_between_midi_tracks(bridge, song, ext):
    ok(bridge, "routing.route", source="Bass", destination="Drums")
    assert song.tracks[2].input_routing_type.display_name == "Bass"


def test_route_incompatible_kinds(bridge, song, ext):
    kind, message = err(bridge, "routing.route", source="Bass", destination="Vocals")
    assert kind == "not_found" and "Ext. In" in message
    assert err(bridge, "routing.route", source="Bass", destination="Bass")[0] == "bad_args"
    assert err(bridge, "routing.route", source="Vocals", destination="Bass",
               method="teleport")[0] == "bad_args"
    assert err(bridge, "routing.route", source="Bass", destination="Drums", method="output",
               monitoring="in")[0] == "bad_args"


def test_route_sidechain(bridge, song, ext):
    compressor = ext.add_compressor(song.tracks[1])
    result = ok(bridge, "routing.route", source="Drums", destination="Vocals",
                method="sidechain", channel="Pre FX")
    assert result["device"] == "song.tracks[1].devices[1]"
    assert result["routing"]["input"]["type"] == "Drums"
    assert result["routing"]["input"]["channel"] == "Pre FX"
    assert result["sidechain_enabled"] is True
    assert compressor.parameters[-1].value == 1.0
    by_name = ok(bridge, "routing.route", source="Bass", destination="Vocals",
                 method="sidechain", device="Compressor")
    assert by_name["routing"]["input"]["type"] == "Bass"
    assert err(bridge, "routing.route", source="Bass", destination="Vocals",
               method="sidechain", device="Reverb")[0] == "unsupported"


def test_route_sidechain_needs_a_capable_device(bridge, ext):
    kind, message = err(bridge, "routing.route", source="Drums", destination="Vocals",
                        method="sidechain")
    assert kind == "unsupported" and "Compressor" in message


def test_routing_commands_registered(bridge):
    listing = ok(bridge, "system.commands", namespace="routing")["commands"]
    assert {c["cmd"] for c in listing} == {"routing.get", "routing.set", "routing.summary",
                                           "routing.route"}


# --------------------------------------------------------------------------
# MCP tools
# --------------------------------------------------------------------------

ROUTING_TOOLS = {"live_routing_get", "live_routing_set", "live_routing_summary",
                 "live_routing_route"}


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


def test_routing_tools_registered(fake_app):
    app, _fake = fake_app
    tools = {t.name: t for t in asyncio.run(app.list_tools())}
    assert ROUTING_TOOLS <= set(tools)
    assert all("Returns" in (tools[n].description or "") for n in ROUTING_TOOLS)


def test_routing_tools_forward_arguments(fake_app):
    app, fake = fake_app
    for cmd in ("routing.get", "routing.set", "routing.summary", "routing.route"):
        fake.set_result(cmd, {"ok": cmd})
    assert call_tool(app, "live_routing_get", {"track": "Bass"}) == {"ok": "routing.get"}
    assert _last(fake, "routing.get") == {"track": "Bass", "include_available": True}
    call_tool(app, "live_routing_set", {"track": 1, "input_type": "Ext. In",
                                        "monitoring": "IN"})
    assert _last(fake, "routing.set") == {"track": 1, "input_type": "Ext. In",
                                          "monitoring": "in"}
    call_tool(app, "live_routing_summary", {"include_master": False})
    assert _last(fake, "routing.summary") == {"include_returns": True, "include_master": False}
    call_tool(app, "live_routing_route", {"source": "Drums", "destination": "Bass",
                                          "method": "Sidechain", "device": 0})
    assert _last(fake, "routing.route") == {"source": "Drums", "destination": "Bass",
                                            "method": "sidechain", "device": 0}


@pytest.mark.parametrize("name,args", [
    ("live_routing_get", {"track": ""}),
    ("live_routing_set", {"track": "Bass"}),
    ("live_routing_set", {"track": "Bass", "monitoring": "loud"}),
    ("live_routing_route", {"source": "a", "destination": "b", "method": "magic"}),
    ("live_routing_route", {"source": "a", "destination": "b", "method": "output",
                            "monitoring": "in"}),
    ("live_routing_route", {"source": "a", "destination": "b", "device": 1}),
])
def test_routing_tools_validate_locally(fake_app, name, args):
    app, fake = fake_app
    assert call_tool(app, name, args)["type"] == "bad_args"
    assert not [r for r in fake.requests if r["cmd"].startswith("routing.")]


def test_routing_tools_end_to_end(tcp_bridge, song, ext):
    factory.add_track(song, "Resample", "audio")
    client = BridgeClient(host="127.0.0.1", port=tcp_bridge.port, timeout=5.0)
    try:
        app = create_app(client)
        got = call_tool(app, "live_routing_get", {"track": "Resample"})
        assert "Vocals" in names(got["available"]["input_types"])
        routed = call_tool(app, "live_routing_route", {"source": "Vocals",
                                                       "destination": "Resample",
                                                       "monitoring": "off"})
        assert routed["routing"]["input"]["type"] == "Vocals"
        changed = call_tool(app, "live_routing_set", {"track": "Resample",
                                                      "input_type": "No Input"})
        assert changed["input"]["type"] == "No Input"
        rows = call_tool(app, "live_routing_summary", {})["tracks"]
        assert rows[3]["in"].startswith("No Input")
        error = call_tool(app, "live_routing_set", {"track": "Resample",
                                                    "input_type": "Nowhere"})
        assert error["type"] == "not_found"
    finally:
        client.close()
