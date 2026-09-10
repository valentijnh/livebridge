"""Module D — racks, chains, macros, variations and Drum Rack pads: handlers + MCP tools.

The shared stub behaves like the real Live 12.4.5 measured on 2026-09-10 (drum-chain note
ranges, new drum chains on C1, "Multi" pad names, variations only touching mapped macros,
insert_device errors).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

import Live
from live_stub import factory

_TESTS = Path(__file__).resolve().parent
for _path in (str(_TESTS.parent / "mcp_server"), str(_TESTS)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from fake_bridge import FakeBridge  # noqa: E402
from livebridge_mcp.client import BridgeClient  # noqa: E402
from livebridge_mcp.server import create_app  # noqa: E402

from LiveBridge.handlers import racks as racks_mod  # noqa: E402
from LiveBridge.registry import BridgeError  # noqa: E402

RACK_TOOLS = ("live_rack_chains", "live_rack_set_chain", "live_rack_insert_chain",
              "live_rack_macros", "live_rack_variations", "live_drumrack_overview",
              "live_drumrack_set_pad", "live_drumrack_convert")


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

    def _p(s):
        try:
            return json.loads(s)
        except ValueError:
            return s
    return _p("\n".join(blocks)) if len(blocks) <= 1 else [_p(b) for b in blocks]


@pytest.fixture
def bass_rack(song):
    rack = song.tracks[0].devices[1]
    factory.add_device(rack.chains[0], "Saturator", kind="audio_effect")
    rack.chains[1].name = "Wet"
    return rack


@pytest.fixture
def drum_rack(song):
    return song.tracks[2].devices[0]


# --------------------------------------------------------------------------- helpers

@pytest.mark.parametrize("value,expected", [
    (36, 36), ("36", 36), ("C1", 36), ("c3", 60), ("F#1", 42), ("Db1", 37), ("C-2", 0),
    ("G8", 127),
])
def test_parse_note(value, expected):
    assert racks_mod.parse_note(value) == expected


def test_parse_note_by_pad_name_and_errors():
    pads = [(36, "Kick"), (38, "Snare"), (39, "Snap")]
    assert racks_mod.parse_note("kick", pads) == 36
    with pytest.raises(BridgeError) as info:
        racks_mod.parse_note("Sn", pads)
    assert info.value.type == "bad_args"
    with pytest.raises(BridgeError):
        racks_mod.parse_note("Tom", pads)
    with pytest.raises(BridgeError):
        racks_mod.parse_note(200)
    with pytest.raises(BridgeError):
        racks_mod.parse_note(True)


# --------------------------------------------------------------------------- chains

def test_chains_listing(bridge, bass_rack):
    result = run(bridge, "racks.chains", track="Bass")
    assert result["rack"]["name"] == "Bass Rack"
    assert result["chain_count"] == 2
    first = result["chains"][0]
    assert first["devices"] == ["Saturator"]
    assert first["volume"] == 0.85 and first["volume_display"] == "0.0 dB"
    assert result["chain_selector"]["value"] == 0.0
    minimal = run(bridge, "racks.chains", track="Bass", detail="minimal")
    assert set(minimal["chains"][0]) == {"index", "name", "path", "mute", "solo"}
    full = run(bridge, "racks.chains", track="Bass", detail="full")
    assert full["chains"][0]["devices"][0]["path"] == \
        "song.tracks[0].devices[1].chains[0].devices[0]"
    drums = run(bridge, "racks.chains", track="Drums")
    assert [(c["key"], c["out_note"]) for c in drums["chains"]] == \
        [("C1", 60), ("D1", 60), ("F#1", 60)]


def test_chains_default_rack_resolution(bridge, song, bass_rack):
    assert fail(bridge, "racks.chains", track="Vocals")["type"] == "not_found"
    assert fail(bridge, "racks.chains", track="Bass", device="Operator")["type"] == "bad_args"
    song.view.select_device(song.tracks[2].devices[0])
    assert run(bridge, "racks.chains", track="Drums")["rack"]["class_name"] == "DrumGroupDevice"


def test_set_chain_settings(bridge, song, bass_rack):
    result = run(bridge, "racks.set_chain", track="Bass", chain="wet", name="Wet Bus",
                 mute=True, volume=0.5, panning="0.5", active=False, color_index=4,
                 select=True)
    chain = bass_rack.chains[1]
    assert result["name"] == "Wet Bus" and chain.mute is True
    assert chain.mixer_device.volume.value == 0.5
    assert chain.mixer_device.panning.value == 0.5
    assert chain.mixer_device.chain_activator.value == 0.0
    assert "active" not in result          # Live's activator is the mute switch
    assert result["mute"] is True and chain.color_index == 4
    assert bass_rack.view.selected_chain is chain
    assert song.view.selected_chain is chain
    run(bridge, "racks.set_chain", chain="song.tracks[0].devices[1].chains[0]", solo=True)
    run(bridge, "racks.set_chain", track="Bass", chain=1, solo=True, exclusive_solo=True)
    assert bass_rack.chains[1].solo is True and bass_rack.chains[0].solo is False


def test_set_chain_errors(bridge, bass_rack):
    assert fail(bridge, "racks.set_chain", track="Bass", chain=9)["type"] == "not_found"
    assert fail(bridge, "racks.set_chain", track="Bass", chain="Nope")["type"] == "not_found"
    assert fail(bridge, "racks.set_chain", track="Bass", chain=0,
                in_note=36)["type"] == "unsupported"
    assert fail(bridge, "racks.set_chain", track="Bass", chain=0,
                sends={"0": 0.5})["type"] == "not_found"
    assert fail(bridge, "racks.set_chain", track="Bass", chain=0, mute="yes")["type"] == \
        "bad_args"
    assert fail(bridge, "racks.set_chain", chain="song.tracks[0]", mute=True)["type"] == \
        "bad_args"
    contradiction = fail(bridge, "racks.set_chain", track="Bass", chain=0, mute=True,
                         active=True)
    assert contradiction["type"] == "bad_args" and "one switch" in contradiction["message"]


def test_chain_mute_is_the_activator(bridge, bass_rack):
    """Live 12.4.5: Chain.mute and mixer_device.chain_activator are the same switch."""
    run(bridge, "racks.set_chain", track="Bass", chain=0, active=False)
    assert bass_rack.chains[0].mute is True
    listed = run(bridge, "racks.chains", track="Bass")
    assert listed["chains"][0]["mute"] is True and "active" not in listed["chains"][0]
    run(bridge, "racks.set_chain", track="Bass", chain=0, mute=False)
    assert bass_rack.chains[0].mixer_device.chain_activator.value == 1.0
    soloed = run(bridge, "racks.set_chain", track="Bass", chain=1, solo=True)
    assert soloed["solo"] is True
    assert run(bridge, "racks.chains", track="Bass")["chains"][0]["muted_via_solo"] is True


def test_set_drum_chain_notes(bridge, drum_rack):
    result = run(bridge, "racks.set_chain", track="Drums", chain="Hat", in_note="G#1",
                 out_note="C3", choke_group=1)
    assert result["in_note"] == 44 and result["key"] == "G#1"
    assert result["choke_group"] == 1
    assert drum_rack.chains[2].out_note == 60
    # Live 12.4.5: in_note is 0..127 only — no "all notes" value through the API
    error = fail(bridge, "racks.set_chain", track="Drums", chain="Hat", in_note=-1)
    assert error["type"] == "bad_args" and "all notes" in error["message"]
    assert fail(bridge, "racks.set_chain", track="Drums", chain="Hat",
                choke_group=17)["type"] == "bad_args"
    assert fail(bridge, "racks.set_chain", track="Drums", chain="Hat",
                out_note=200)["type"] == "bad_args"
    with pytest.raises(RuntimeError, match="Invalid note number"):
        drum_rack.chains[2].in_note = -1       # what the stub (= Live) itself does


def test_insert_chain(bridge, bass_rack, drum_rack):
    plain = run(bridge, "racks.insert_chain", track="Bass", name="Dry", device_name="Utility")
    assert plain["index"] == 2 and plain["devices"][0]["class_name"] == "StereoGain"
    first = run(bridge, "racks.insert_chain", track="Bass", index=0)
    assert first["index"] == 0
    pad = run(bridge, "racks.insert_chain", track="Drums", in_note="E1", name="Clap",
              device_name="Simpler")
    assert pad["in_note"] == 40 and pad["devices"][0]["class_name"] == "OriginalSimpler"
    overview = run(bridge, "racks.drum_pads", track="Drums")
    assert [40, "E1", "Clap"] == overview["rows"][2][:3]
    assert fail(bridge, "racks.insert_chain", track="Bass", in_note=36)["type"] == "unsupported"
    assert fail(bridge, "racks.insert_chain", track="Bass", index=99)["type"] == "bad_args"
    assert fail(bridge, "racks.insert_chain", track="Bass",
                device_name="Serum")["type"] == "not_found"
    assert fail(bridge, "racks.insert_chain", track="Drums",
                in_note=-1)["type"] == "bad_args"


def test_insert_chain_device_names_like_live(bridge, bass_rack, drum_rack):
    # insert_device only knows exact UI names; the handler maps case/class names
    lower = run(bridge, "racks.insert_chain", track="Drums", in_note="C#1",
                device_name="simpler")
    assert lower["devices"][0]["class_name"] == "OriginalSimpler"
    by_class = run(bridge, "racks.insert_chain", track="Bass", device_name="StereoGain")
    assert by_class["devices"][0]["name"] == "Utility"


def test_new_drum_chain_lands_on_c1(bridge, drum_rack):
    """Live 12.4.5 puts every new Drum Rack chain on C1 (36); C1 then holds two chains
    and the pad is called "Multi"."""
    chain = run(bridge, "racks.insert_chain", track="Drums", name="Extra")
    assert chain["in_note"] == 36 and chain["key"] == "C1"
    overview = run(bridge, "racks.drum_pads", track="Drums")
    assert overview["rows"][0][:4] == [36, "C1", "Multi", 2]
    # the pad is still reachable through its chains' names
    pad = run(bridge, "racks.set_pad", track="Drums", note="Extra", mute=True)
    assert pad["note"] == 36 and pad["name"] == "Multi" and pad["mute"] is True
    assert drum_rack.drum_pads[50].name == "D2"    # an empty pad is named after its note


def test_insert_chain_unsupported_on_old_live(bridge, monkeypatch, bass_rack):
    monkeypatch.delattr(Live._model.RackDevice, "insert_chain")
    assert fail(bridge, "racks.insert_chain", track="Bass")["type"] == "unsupported"


# --------------------------------------------------------------------------- macros

def test_macros_read(bridge, bass_rack):
    macro = bass_rack.parameters[3]
    macro._name = "Drive"          # a mapped macro takes the mapped parameter's name
    bass_rack._map_macro(2)
    result = run(bridge, "racks.macros", track="Bass")
    assert result["visible_macro_count"] == 8 and len(result["macros"]) == 8
    third = result["macros"][2]
    assert third == {"number": 3, "index": 3, "name": "Drive", "value": 0.0,
                     "display": "0.00", "mapped": True}
    assert len(run(bridge, "racks.macros", track="Bass", include_hidden=True)["macros"]) == 16


def test_set_macros(bridge, song, bass_rack):
    bass_rack.parameters[3]._name = "Drive"
    before = len(song._undo_steps)
    result = run(bridge, "racks.set_macros", track="Bass",
                 values={"1": 64, "Macro 2": "100", "drive": 1.0}, visible_count=4,
                 chain_selector=10)
    assert len(song._undo_steps) == before + 1
    assert [m["value"] for m in result["macros"]] == [64.0, 100.0, 1.0, 0.0]
    assert bass_rack.chain_selector.value == 10.0
    normalized = run(bridge, "racks.set_macros", track="Bass", values={"4": 0.5},
                     normalized=True, visible_count=16)
    assert bass_rack.parameters[4].value == 63.5
    assert normalized["visible_macro_count"] == 16


def test_set_macros_is_atomic_and_validated(bridge, bass_rack):
    error = fail(bridge, "racks.set_macros", track="Bass", values={"1": 10, "Nope": 3})
    assert error["type"] == "not_found"
    assert bass_rack.parameters[1].value == 0.0
    assert fail(bridge, "racks.set_macros", track="Bass",
                values={"17": 1})["type"] == "not_found"
    assert fail(bridge, "racks.set_macros", track="Bass")["type"] == "bad_args"
    assert fail(bridge, "racks.set_macros", track="Bass", visible_count=0)["type"] == "bad_args"
    bass_rack.parameters[2]._set_enabled(False)
    assert fail(bridge, "racks.set_macros", track="Bass",
                values={"2": 5})["type"] == "invalid_state"


def test_variations(bridge, bass_rack):
    bass_rack._map_macro(0)                 # variations only store/recall mapped macros
    run(bridge, "racks.set_macros", track="Bass", values={"1": 10})
    stored = run(bridge, "racks.variations", track="Bass", action="store")
    # Live 12.4.5 does not select a stored variation
    assert stored["variation_count"] == 1 and stored["selected_variation_index"] == -1
    assert "note" not in stored
    run(bridge, "racks.set_macros", track="Bass", values={"1": 90})
    run(bridge, "racks.variations", track="Bass", action="store")
    recalled = run(bridge, "racks.variations", track="Bass", action="recall", index=0)
    assert recalled["macros"][0]["value"] == 10.0
    selected = run(bridge, "racks.variations", track="Bass", action="select", index=1)
    assert selected["selected_variation_index"] == 1
    last = run(bridge, "racks.variations", track="Bass", action="recall_last")
    assert last["macros"][0]["value"] == 10.0
    deleted = run(bridge, "racks.variations", track="Bass", action="delete", index=1)
    assert deleted["variation_count"] == 1 and deleted["selected_variation_index"] == -1
    randomized = run(bridge, "racks.variations", track="Bass", action="randomize")
    assert randomized["macros"][0]["value"] != 10.0
    listed = run(bridge, "racks.variations", track="Bass")
    assert listed["action"] == "list"
    assert fail(bridge, "racks.variations", track="Bass", action="recall",
                index=5)["type"] == "not_found"
    assert fail(bridge, "racks.variations", track="Bass", action="spin")["type"] == "bad_args"


def test_variations_without_mappings_change_nothing(bridge, bass_rack):
    """On a rack without macro mappings Live's recall/randomize are no-ops — the answer
    says so instead of pretending."""
    run(bridge, "racks.set_macros", track="Bass", values={"1": 10})
    run(bridge, "racks.variations", track="Bass", action="store")
    run(bridge, "racks.set_macros", track="Bass", values={"1": 90})
    recalled = run(bridge, "racks.variations", track="Bass", action="recall", index=0)
    assert recalled["macros"][0]["value"] == 90.0
    assert "no macro mappings" in recalled["note"]
    randomized = run(bridge, "racks.variations", track="Bass", action="randomize")
    assert randomized["macros"][0]["value"] == 90.0 and "note" in randomized


def test_macro_names_with_trailing_spaces(bridge, bass_rack):
    bass_rack.parameters[1]._name = "Low Gain  "     # as in Live's 808 Core Kit
    result = run(bridge, "racks.set_macros", track="Bass", values={"low gain": 20})
    assert result["macros"][0]["value"] == 20.0


# --------------------------------------------------------------------------- drum pads

def test_drum_pad_overview(bridge, drum_rack):
    result = run(bridge, "racks.drum_pads", track="Drums")
    assert result["columns"][:6] == ["note", "key", "name", "chains", "devices", "sample"]
    assert result["rows"][0] == [36, "C1", "Kick", 1, "Kick", "Kick.wav", False, False, 0]
    assert result["filled"] == 3 and result["with_samples"] == 3
    assert result["visible_pads"] == [36, 51]
    everything = run(bridge, "racks.drum_pads", track="Drums", include_empty=True)
    assert len(everything["rows"]) == 128 and everything["filled"] == 3
    full = run(bridge, "racks.drum_pads", track="Drums", full_paths=True)
    assert full["rows"][1][5] == "/Samples/Drums/Snare.wav"
    assert full["rows"][1][-2:] == ["song.tracks[2].devices[0].drum_pads[38]",
                                    "song.tracks[2].devices[0].chains[1]"]
    assert fail(bridge, "racks.drum_pads", track="Bass")["type"] == "not_found"


def test_nested_drum_rack_uses_chain_notes(bridge, song):
    outer = factory.add_rack(song.tracks[1], "Outer", kind="audio_effect", chains=1)
    inner = Live._model.RackDevice("Inner Kit", "DrumGroupDevice", 1, (), outer.chains[0],
                                   can_have_drum_pads=True)
    outer.chains[0]._devices.append(inner)
    chain = inner.add_chain("Clap", in_note=39)
    factory.add_simpler(chain, "Clap", file_path="/Samples/Clap.wav")
    result = run(bridge, "racks.drum_pads",
                 device="song.tracks[1].devices[1].chains[0].devices[0]", full_paths=True)
    assert result["rows"] == [[39, "D#1", "Clap", 1, "Clap", "/Samples/Clap.wav", False, False,
                               0, None, "song.tracks[1].devices[1].chains[0].devices[0]"
                               ".chains[0]"]]
    error = fail(bridge, "racks.set_pad", device="song.tracks[1].devices[1].chains[0].devices[0]",
                 note=39, mute=True)
    assert error["type"] == "unsupported"
    renamed = run(bridge, "racks.set_pad",
                  device="song.tracks[1].devices[1].chains[0].devices[0]", note="Clap",
                  name="Clap 2", choke_group=2)
    assert renamed["name"] == "Clap 2" and renamed["choke"] == 2


def test_set_pad(bridge, song, drum_rack):
    result = run(bridge, "racks.set_pad", track="Drums", note="Kick", mute=True, solo=True,
                 name="Kick In", choke_group=3, out_note=62, select=True)
    pad = drum_rack.drum_pads[36]
    assert pad.mute is True and pad.solo is True and pad.name == "Kick In"
    assert result["choke"] == 3 and drum_rack.chains[0].out_note == 62
    assert drum_rack.view.selected_drum_pad is pad
    assert result["path"] == "song.tracks[2].devices[0].drum_pads[36]"
    copied = run(bridge, "racks.set_pad", track="Drums", note="D1", copy_to="A2")
    assert copied["copied_to"] == 57
    assert drum_rack.drum_pads[57].chains
    cleared = run(bridge, "racks.set_pad", track="Drums", note=57, clear=True)
    assert cleared["cleared"] is True and cleared["chains"] == 0
    assert not drum_rack.drum_pads[57].chains


def test_set_pad_scrolls_far_pads_into_view(bridge, song, drum_rack):
    factory.add_chain(drum_rack, "Crash", devices=["Simpler"], in_note=100)
    run(bridge, "racks.set_pad", track="Drums", note=100, select=True)
    start = drum_rack.view.drum_pads_scroll_position * 4
    assert start <= 100 <= start + 15


def test_set_pad_errors(bridge, drum_rack):
    assert fail(bridge, "racks.set_pad", track="Drums", note="Tom", mute=True)["type"] == \
        "not_found"
    assert fail(bridge, "racks.set_pad", track="Drums", note=50,
                name="Empty")["type"] == "invalid_state"
    assert fail(bridge, "racks.set_pad", track="Drums", note=50,
                clear=True)["type"] == "invalid_state"
    assert fail(bridge, "racks.set_pad", track="Drums", note=36,
                copy_to=36)["type"] == "bad_args"
    assert fail(bridge, "racks.set_pad", track="Drums", note=50,
                copy_to=51)["type"] == "invalid_state"
    assert fail(bridge, "racks.set_pad", track="Drums", note=36,
                choke_group=20)["type"] == "bad_args"


# --------------------------------------------------------------------------- MCP tools

@pytest.fixture
def live_app(tcp_bridge):
    client = BridgeClient(host="127.0.0.1", port=tcp_bridge.port, timeout=5.0)
    try:
        yield create_app(client)
    finally:
        client.close()


@pytest.fixture
def fake_app():
    fake = FakeBridge().start()
    client = BridgeClient(host=fake.host, port=fake.port, timeout=5.0)
    try:
        yield fake, create_app(client)
    finally:
        client.close()
        fake.stop()


def test_rack_tools_are_registered(fake_app):
    _fake, app = fake_app
    names = {tool.name for tool in asyncio.run(app.list_tools())}
    assert all(name in names for name in RACK_TOOLS)


def test_rack_tools_end_to_end(live_app, song, bass_rack, drum_rack):
    chains = call_tool(live_app, "live_rack_chains", {"track": "Bass"})
    assert chains["chains"][0]["devices"] == ["Saturator"]
    changed = call_tool(live_app, "live_rack_set_chain",
                        {"track": "Bass", "chain": 1, "mute": True, "volume": 0.4})
    assert changed["mute"] is True and changed["volume"] == 0.4
    new_chain = call_tool(live_app, "live_rack_insert_chain",
                          {"track": "Drums", "in_note": "C#1", "device_name": "Simpler"})
    assert new_chain["key"] == "C#1"
    macros = call_tool(live_app, "live_rack_macros", {"track": "Bass"})
    assert len(macros["macros"]) == 8
    set_macros = call_tool(live_app, "live_rack_macros",
                           {"track": "Bass", "values": {"1": 127}, "visible_count": 2})
    assert [m["value"] for m in set_macros["macros"]] == [127.0, 0.0]
    variations = call_tool(live_app, "live_rack_variations", {"track": "Bass", "action": "store"})
    assert variations["variation_count"] == 1
    overview = call_tool(live_app, "live_drumrack_overview", {"track": "Drums"})
    assert overview["rows"][0][:3] == [36, "C1", "Kick"]
    pad = call_tool(live_app, "live_drumrack_set_pad",
                    {"track": "Drums", "note": "C1", "mute": True})
    assert pad["mute"] is True and drum_rack.drum_pads[36].mute is True


def test_rack_macros_tool_routes_reads_and_writes(fake_app):
    fake, app = fake_app
    fake.set_result("racks.macros", {"macros": []})
    fake.set_result("racks.set_macros", {"macros": []})
    call_tool(app, "live_rack_macros", {"track": "Bass"})
    call_tool(app, "live_rack_macros", {"track": "Bass", "values": {"1": "50 %"}})
    call_tool(app, "live_rack_macros", {"chain_selector": 3})
    cmds = [(r["cmd"], r["args"]) for r in fake.requests]
    assert cmds[0] == ("racks.macros", {"track": "Bass", "include_hidden": False})
    assert cmds[1] == ("racks.set_macros", {"track": "Bass", "values": {"1": "50 %"},
                                            "normalized": False, "include_hidden": False})
    assert cmds[2][0] == "racks.set_macros" and cmds[2][1]["chain_selector"] == 3


@pytest.mark.parametrize("tool,args", [
    ("live_rack_chains", {"detail": "all"}),
    ("live_rack_set_chain", {"chain": 0}),
    ("live_rack_set_chain", {"chain": " ", "mute": True}),
    ("live_rack_set_chain", {"chain": 0, "choke_group": 30}),
    ("live_rack_insert_chain", {"index": -4}),
    ("live_rack_insert_chain", {"device_name": " "}),
    ("live_rack_macros", {"values": {}}),
    ("live_rack_macros", {"visible_count": 17}),
    ("live_rack_variations", {"action": "spin"}),
    ("live_rack_variations", {"action": "recall", "index": -1}),
    ("live_drumrack_set_pad", {"note": 36}),
    ("live_drumrack_set_pad", {"note": 200, "mute": True}),
    ("live_drumrack_set_pad", {"note": "", "mute": True}),
    ("live_drumrack_set_pad", {"note": 36, "choke_group": 17}),
    ("live_drumrack_set_pad", {"note": 36, "file_path": " "}),
    ("live_drumrack_set_pad", {"note": 36, "file_path": "/a.wav", "device": 0}),
    ("live_drumrack_set_pad", {"note": 36, "file_path": "/a.wav", "clear": True}),
    ("live_drumrack_convert", {"action": "flip"}),
    ("live_drumrack_convert", {"action": "pad_to_track"}),
    ("live_drumrack_convert", {"action": "track_to_pad", "note": 36}),
])
def test_rack_tools_validate_locally(fake_app, tool, args):
    fake, app = fake_app
    assert call_tool(app, tool, args)["type"] == "bad_args"
    assert fake.requests == []


# --------------------------------------------------------------------------- conversions / samples

from live_stub_ext import conversions_racks as conversions_ext  # noqa: E402


@pytest.fixture
def conversions():
    module = conversions_ext.install(Live)
    yield module
    conversions_ext.uninstall(Live)


def test_convert_pad_to_track_and_back(bridge, song, conversions):
    count = len(song.tracks)
    done = run(bridge, "racks.convert", action="pad_to_track", track="Drums", note="Snare",
               select=True)
    assert done["pad"] == {"note": 38, "key": "D1", "name": "Snare"}
    assert len(song.tracks) == count + 1 and done["new_track"]["name"] == "Snare"
    assert done["devices"][0]["class_name"] == "OriginalSimpler"
    assert song.view.selected_track == song.tracks[-1]
    assert len(song.tracks[2].devices[0].drum_pads[38].chains) == 1, "the pad stays"
    new_track = done["new_track"]["path"]
    song.view.selected_track = song.tracks[0]     # Live rebuilds the track after this one
    moved = run(bridge, "racks.convert", action="track_to_pad", track=new_track)
    track = song.tracks[1]
    assert moved["track"]["path"] == "song.tracks[1]" and track.name == "Snare"
    assert len(track.devices) == 1 and track.devices[0].can_have_drum_pads
    assert moved["rack"]["class_name"] == "DrumGroupDevice"
    assert moved["pad"]["kind"] == "drum_pad" and moved["pad"]["note"] == 36
    assert moved["track"]["name"] == "Snare"
    assert track.devices[0].drum_pads[36].chains[0].devices[0].class_name == "OriginalSimpler"


def test_convert_refusals(bridge, song, conversions):
    assert fail(bridge, "racks.convert", action="pad_to_track", track="Drums",
                note=40)["type"] == "invalid_state"          # empty pad
    assert fail(bridge, "racks.convert", action="pad_to_track", track="Drums")["type"] == \
        "bad_args"
    assert fail(bridge, "racks.convert", action="track_to_pad", track="A")["type"] == "bad_args"
    empty = factory.add_track(song, "Empty", "midi")
    assert fail(bridge, "racks.convert", action="track_to_pad",
                track=empty.name)["type"] == "invalid_state"
    conversions_ext.uninstall(Live)
    assert fail(bridge, "racks.convert", action="pad_to_track", track="Drums",
                note=36)["type"] == "unsupported"


def test_drumrack_tools_load_samples_and_convert(live_app, song, tmp_path, conversions):
    import wave
    path = tmp_path / "Clap 01.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(22050)
        handle.writeframes(b"\x00" * 2000)
    loaded = call_tool(live_app, "live_drumrack_set_pad",
                       {"track": "Drums", "note": "E1", "file_path": str(path),
                        "choke_group": 2})
    assert loaded["note"] == 40 and loaded["choke"] == 2
    assert loaded["loaded"]["route"].startswith("rack.insert_chain")
    pad = song.tracks[2].devices[0].drum_pads[40]
    assert pad.chains[0].devices[0].sample.file_path == str(path)
    # a pad holding one Simpler: its sample is replaced (the Kick pad)
    again = call_tool(live_app, "live_drumrack_set_pad",
                      {"track": "Drums", "note": "Kick", "file_path": str(path)})
    assert again["loaded"]["route"] == "drum_pad simpler.replace_sample"
    converted = call_tool(live_app, "live_drumrack_convert",
                          {"action": "pad_to_track", "track": "Drums", "note": 40})
    assert converted["new_track"]["name"] == "Clap 01"
