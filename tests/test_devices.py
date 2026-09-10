"""Module D — devices: handlers (through the dispatcher on the stub set) and MCP tools.

Handlers are exercised with ``bridge.dispatch`` (registry + serializer covered); tools run
through the FastMCP app, end-to-end against the real TCP server (``tcp_bridge``) and against
``tests/fake_bridge.py`` for argument forwarding/validation.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

import Live
from live_stub import factory
from live_stub_ext import devices as devices_ext

_TESTS = Path(__file__).resolve().parent
for _path in (str(_TESTS.parent / "mcp_server"), str(_TESTS)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from fake_bridge import FakeBridge  # noqa: E402
from livebridge_mcp.client import BridgeClient  # noqa: E402
from livebridge_mcp.server import create_app  # noqa: E402

from LiveBridge.handlers import devices as dev_mod  # noqa: E402

EXT = devices_ext.install(Live)

DEVICE_TOOLS = (
    "live_device_list", "live_device_get", "live_device_parameters", "live_device_get_parameter",
    "live_device_set_parameter", "live_device_set_parameters", "live_device_reset_parameters",
    "live_device_randomize", "live_device_set_state", "live_device_delete", "live_device_insert",
    "live_device_duplicate", "live_device_move", "live_device_find", "live_simpler_get",
    "live_simpler_set", "live_simpler_action", "live_device_properties", "live_device_action",
    "live_device_modulation",
)


# --------------------------------------------------------------------------- helpers

def run(bridge, cmd, **args):
    """Dispatch and return the result; fail loudly on an error envelope."""
    response = bridge.dispatch({"id": "t", "cmd": cmd, "args": args})
    assert response["ok"], response
    return response["result"]


def fail(bridge, cmd, **args):
    """Dispatch and return the error object (asserts the call failed)."""
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


def tool_names(app):
    return {tool.name for tool in asyncio.run(app.list_tools())}


@pytest.fixture
def operator(song):
    return song.tracks[0].devices[0]


@pytest.fixture
def curved(song):
    """An EQ-like device on Vocals with real-Live display curves."""
    device = factory.add_device(song.tracks[1], "Curvy", "Curvy", kind="audio_effect")
    EXT.add_curved_parameter(device, "Gain", "volume", 0.85)
    EXT.add_curved_parameter(device, "Freq", "freq", 0.5)
    EXT.add_curved_parameter(device, "Decay", "time", 0.5)
    EXT.add_curved_parameter(device, "Pan", "pan", 0.0)
    EXT.add_curved_parameter(device, "Dry/Wet", "percent", 0.5)
    return device


# --------------------------------------------------------------------------- pure helpers

@pytest.mark.parametrize("text,expected", [
    ("-12 dB", (-12.0, "db")), ("0.0 dB", (0.0, "db")), ("+3dB", (3.0, "db")),
    ("-inf dB", (float("-inf"), "db")), ("1.5 kHz", (1500.0, "hz")), ("830 Hz", (830.0, "hz")),
    ("250 ms", (0.25, "s")), ("2.50 s", (2.5, "s")), ("40 %", (40.0, "%")), ("25L", (-25.0, "pan")),
    ("R10", (10.0, "pan")), ("C", (0.0, "pan")), ("1/8", (0.125, "fraction")),
    ("16  / 16", (1.0, "fraction")), ("5.85 ", (5.85, "")), ("12 st", (12.0, "st")),
    # Compressor ratios as Live 12.4.5 displays them
    ("4:1", (4.0, "ratio")), ("4.00 : 1", (4.0, "ratio")), ("inf : 1", (float("inf"), "ratio")),
    ("1 : 2", (0.5, "ratio")),
])
def test_parse_display(text, expected):
    assert dev_mod.parse_display(text) == expected


def test_parse_display_rejects_words():
    assert dev_mod.parse_display("Saw") is None
    assert dev_mod.parse_display("") is None
    assert dev_mod.parse_display(None) is None


def test_note_names_use_live_octaves():
    assert dev_mod.note_name(36) == "C1"
    assert dev_mod.note_name(60) == "C3"
    assert dev_mod.note_name(0) == "C-2"
    assert dev_mod.note_name(128) is None


# --------------------------------------------------------------------------- listing

def test_list_flat_and_tree(bridge, song):
    flat = run(bridge, "devices.list", track="Bass")
    assert flat["track"] == {"path": "song.tracks[0]", "name": "Bass"}
    assert [d["name"] for d in flat["devices"]] == ["Operator", "Bass Rack"]
    assert flat["devices"][1]["path"] == "song.tracks[0].devices[1]"
    assert "chains" not in flat["devices"][1]
    tree = run(bridge, "devices.list", track="Drums", tree=True)
    rack = tree["devices"][0]
    assert [c["key"] for c in rack["chains"]] == ["C1", "D1", "F#1"]
    assert rack["chains"][0]["in_note"] == 36
    assert rack["chains"][0]["devices"][0]["path"] == \
        "song.tracks[2].devices[0].chains[0].devices[0]"


def test_list_defaults_to_selected_track_and_reports_selection(bridge, song, operator):
    song.view.select_device(operator)
    result = run(bridge, "devices.list")
    assert result["track"]["name"] == "Bass"
    assert result["selected"] == "song.tracks[0].devices[0]"


def test_list_validates_arguments(bridge):
    assert fail(bridge, "devices.list", detail="huge")["type"] == "bad_args"
    assert fail(bridge, "devices.list", max_depth=0)["type"] == "bad_args"
    assert fail(bridge, "devices.list", track=42)["type"] == "not_found"


def test_get_by_name_index_path_and_selection(bridge, song, operator):
    by_name = run(bridge, "devices.get", track="Bass", device="operator")
    assert by_name["path"] == "song.tracks[0].devices[0]"
    assert by_name["device_kind"] == "native"
    assert by_name["host"] == "song.tracks[0]"
    assert by_name["position"] == 0
    by_index = run(bridge, "devices.get", track=0, device=-1)
    assert by_index["name"] == "Bass Rack"
    assert by_index["chains"][1]["path"] == "song.tracks[0].devices[1].chains[1]"
    assert by_index["visible_macro_count"] == 8
    nested = run(bridge, "devices.get", device="song.tracks[2].devices[0].chains[1].devices[0]")
    assert nested["name"] == "Snare"
    assert nested["device_kind"] == "simpler"
    assert nested["host"] == "song.tracks[2].devices[0].chains[1]"
    song.view.select_device(operator)
    selected = run(bridge, "devices.get")
    assert selected["name"] == "Operator" and selected["selected"] is True


def test_get_nested_device_by_name_and_class(bridge):
    assert run(bridge, "devices.get", track="Drums", device="Hat")["path"] == \
        "song.tracks[2].devices[0].chains[2].devices[0]"
    assert run(bridge, "devices.get", track="Drums", device="DrumGroupDevice")["name"] == \
        "Drum Rack"


def test_get_full_includes_parameters(bridge):
    full = run(bridge, "devices.get", track=0, device=0, detail="full", max_parameters=3)
    assert [p["name"] for p in full["parameters"]] == ["Device On", "Volume", "Filter Freq"]
    assert full["parameters_truncated"] == 4


def test_device_resolution_errors(bridge, song):
    error = fail(bridge, "devices.get", track="Bass", device="Nope")
    assert error["type"] == "not_found" and "Operator" in error["message"]
    assert fail(bridge, "devices.get", track="Bass", device=5)["type"] == "not_found"
    assert fail(bridge, "devices.get", device="song.tracks[0]")["type"] == "bad_args"
    factory.add_device(song.tracks[1], "Echo Left", kind="audio_effect")
    factory.add_device(song.tracks[1], "Echo Right", kind="audio_effect")
    ambiguous = fail(bridge, "devices.get", track="Vocals", device="Echo")
    assert ambiguous["type"] == "bad_args" and "ambiguous" in ambiguous["message"]
    # two devices and nothing selected -> ask for one
    assert fail(bridge, "devices.get", track="Bass")["type"] == "bad_args"
    assert fail(bridge, "devices.get", track="A-Reverb")["type"] == "not_found"


def test_parameters_paging_filter_and_detail(bridge, operator):
    page = run(bridge, "devices.parameters", track=0, device=0, offset=1, limit=2)
    assert page["total"] == 7 and page["matched"] == 7
    assert [p["index"] for p in page["parameters"]] == [1, 2]
    assert page["next_offset"] == 3
    osc = run(bridge, "devices.parameters", track=0, device=0, filter="wave")["parameters"][0]
    assert osc["items"] == ["Sine", "Saw", "Square", "Noise"]
    assert osc["quantized"] is True and osc["display"] == "Saw"
    minimal = run(bridge, "devices.parameters", track=0, device=0, detail="minimal")
    assert set(minimal["parameters"][1]) == {"index", "name", "value", "display"}
    full = run(bridge, "devices.parameters", track=0, device=0, detail="full", filter="Filter")
    freq = full["parameters"][0]
    assert freq["path"] == "song.tracks[0].devices[0].parameters[2]"
    assert freq["default"] == 800.0 and freq["normalized"] == pytest.approx(0.0392, abs=1e-3)
    operator.parameters[2].value = 1000.0
    changed = run(bridge, "devices.parameters", track=0, device=0, only_changed=True)
    assert [p["name"] for p in changed["parameters"]] == ["Filter Freq"]
    assert fail(bridge, "devices.parameters", track=0, device=0, limit=0)["type"] == "bad_args"


def test_get_parameter_lookup_rules(bridge, song):
    assert run(bridge, "devices.get_parameter", track=0, device=0,
               parameter="filter freq")["index"] == 2
    assert run(bridge, "devices.get_parameter", track=0, device=0, parameter="Reso")["index"] == 3
    assert run(bridge, "devices.get_parameter", track=0, device=0, parameter="3")["index"] == 3
    mixer = run(bridge, "devices.get_parameter",
                parameter="song.tracks[0].mixer_device.volume")
    assert mixer["path"] == "song.tracks[0].mixer_device.volume"
    assert "device" not in mixer
    error = fail(bridge, "devices.get_parameter", track=0, device=0, parameter="e")
    assert error["type"] == "bad_args" and "ambiguous" in error["message"]
    assert fail(bridge, "devices.get_parameter", track=0, device=0,
                parameter="Zzz")["type"] == "not_found"
    assert fail(bridge, "devices.get_parameter", track=0, device=0,
                parameter=99)["type"] == "not_found"


def test_get_parameter_ignores_punctuation(bridge, song, operator):
    """Real Compressor names like "S/C EQ Freq": "sc eq freq" finds it, "sc eq" is
    ambiguous (verified wording on Live 12.4.5)."""
    for name in ("S/C EQ On", "S/C EQ Freq", "S/C EQ Gain"):
        factory.add_parameter(operator, name, value=0.5, min=0.0, max=1.0)
    found = run(bridge, "devices.get_parameter", track=0, device=0, parameter="sc eq freq")
    assert found["name"] == "S/C EQ Freq"
    error = fail(bridge, "devices.get_parameter", track=0, device=0, parameter="sc eq")
    assert error["type"] == "bad_args" and "ambiguous" in error["message"]


def _ratio_parameter(device, name="Ratio", value=0.75):
    """Compressor-like Ratio 0..1 displayed "1.00 : 1" .. "inf : 1" (0.75 = "4.00 : 1")."""
    base = Live._model.DeviceParameter

    class RatioParameter(base):
        def str_for_value(self, value, /):
            value = float(value)
            if value >= 1.0:
                return "inf : 1"
            return "%.2f : 1" % (1.0 / (1.0 - value))

    param = RatioParameter(name, value, 0.0, 1.0, canonical_parent=device)
    device._parameters.append(param)
    return param


def test_set_parameter_by_ratio_display(bridge, song, operator):
    _ratio_parameter(operator)
    result = run(bridge, "devices.set_parameter", track=0, device=0, parameter="Ratio",
                 value="2:1")
    assert result["display"] == "2.00 : 1" and result["matched"] == "display"
    result = run(bridge, "devices.set_parameter", track=0, device=0, parameter="Ratio",
                 value="inf:1")
    assert result["display"] == "inf : 1" and result["value"] == 1.0


# --------------------------------------------------------------------------- set parameter

def test_set_parameter_number_clamp_normalized_bool_item(bridge, song, operator):
    result = run(bridge, "devices.set_parameter", track=0, device=0, parameter="Filter Freq",
                 value=50000)
    assert result["value"] == 20000.0 and result["clamped"] is True
    assert result["previous"] == "800.00"
    result = run(bridge, "devices.set_parameter", track=0, device=0, parameter="Attack",
                 value=0.5, normalized=True)
    assert result["value"] == 5.0 and result["matched"] == "normalized"
    result = run(bridge, "devices.set_parameter", track=0, device=0, parameter=0, value=False)
    assert result["display"] == "Off" and operator.is_active is False
    result = run(bridge, "devices.set_parameter", track=0, device=0, parameter="Osc Wave",
                 value="noise")
    assert result["value"] == 3.0 and result["matched"] == "item"
    result = run(bridge, "devices.set_parameter", track=0, device=0, parameter="Osc Wave",
                 value=1.6)
    assert result["value"] == 2.0
    assert fail(bridge, "devices.set_parameter", track=0, device=0, parameter="Osc Wave",
                value="Triangle")["type"] == "bad_args"
    assert fail(bridge, "devices.set_parameter", track=0, device=0, parameter="Volume",
                value=[1])["type"] == "bad_args"


def test_set_parameter_is_one_undo_step(bridge, song):
    before = len(song._undo_steps)
    run(bridge, "devices.set_parameter", track=0, device=0, parameter="Volume", value=0.3)
    assert len(song._undo_steps) == before + 1


@pytest.mark.parametrize("name,text,expected_display", [
    ("Gain", "-12 dB", "-12.0 dB"), ("Gain", "0 dB", "0.0 dB"), ("Gain", "+6 dB", "6.0 dB"),
    ("Gain", "-inf dB", "-inf dB"), ("Gain", "-40 dB", "-40.0 dB"),
    ("Freq", "1 kHz", "1.00 kHz"), ("Freq", "830 Hz", "830 Hz"), ("Freq", "4.5 kHz", "4.50 kHz"),
    ("Freq", "50 Hz", "50.0 Hz"),
    ("Decay", "250 ms", "250.0 ms"), ("Decay", "2.5 s", "2.50 s"), ("Decay", "2500 ms", "2.50 s"),
    ("Pan", "25L", "25L"), ("Pan", "C", "C"), ("Pan", "R10", "10R"),
    ("Dry/Wet", "40 %", "40 %"), ("Dry/Wet", "40%", "40 %"),
])
def test_set_parameter_by_display_string(bridge, curved, name, text, expected_display):
    result = run(bridge, "devices.set_parameter", track="Vocals", device="Curvy",
                 parameter=name, value=text)
    assert result["display"] == expected_display
    assert result["matched"] == "display"


def test_display_search_lands_in_the_middle_of_the_rounding_band(bridge, curved):
    result = run(bridge, "devices.set_parameter", track="Vocals", device="Curvy",
                 parameter="Gain", value="-12 dB")
    assert EXT.volume_db(result["value"]) == pytest.approx(-12.0, abs=0.03)


def test_display_string_errors_are_helpful(bridge, curved):
    error = fail(bridge, "devices.set_parameter", track="Vocals", device="Curvy",
                 parameter="Gain", value="+20 dB")
    assert error["type"] == "bad_args" and "range" in error["message"]
    assert "dB" in error["message"]
    error = fail(bridge, "devices.set_parameter", track="Vocals", device="Curvy",
                 parameter="Gain", value="200 Hz")
    assert "unit" in error["message"]
    error = fail(bridge, "devices.set_parameter", track="Vocals", device="Curvy",
                 parameter="Pan", value="wobbly")
    assert error["type"] == "bad_args"


def test_disabled_parameter_cannot_be_set(bridge, operator):
    operator.parameters[1]._set_enabled(False)
    error = fail(bridge, "devices.set_parameter", track=0, device=0, parameter="Volume",
                 value=0.1)
    assert error["type"] == "invalid_state" and "macro" in error["message"]


def test_set_parameters_dict_and_list(bridge, song, operator, curved):
    result = run(bridge, "devices.set_parameters", track=0, device=0,
                 values={"Volume": 0.5, "2": "5000", "Osc Wave": "Square"})
    assert result["count"] == 3
    assert operator.parameters[1].value == 0.5
    assert operator.parameters[2].value == pytest.approx(5000.0, abs=1.0)
    assert operator.parameters[6].value == 2.0
    result = run(bridge, "devices.set_parameters", values=[
        {"parameter": "song.tracks[1].devices[1].parameters[1]", "value": "-6 dB"},
        {"parameter": "song.tracks[0].devices[0].parameters[3]", "value": 1.0,
         "normalized": True},
    ])
    assert result["parameters"][0]["display"] == "-6.0 dB"
    assert result["parameters"][0]["path"] == "song.tracks[1].devices[1].parameters[1]"
    assert operator.parameters[3].value == 1.0
    assert "device" not in result


def test_set_parameters_is_atomic(bridge, song, operator):
    before = [p.value for p in operator.parameters]
    error = fail(bridge, "devices.set_parameters", track=0, device=0,
                 values={"Volume": 0.1, "Osc Wave": "Triangle"})
    assert error["type"] == "bad_args"
    assert [p.value for p in operator.parameters] == before
    EXT.add_refusing_parameter(operator, "Locked")
    error = fail(bridge, "devices.set_parameters", track=0, device=0,
                 values=[{"parameter": "Volume", "value": 0.1},
                         {"parameter": "Locked", "value": 0.2}])
    assert error["type"] == "invalid_state"
    assert operator.parameters[1].value == before[1]


def test_set_parameters_validates_shape(bridge):
    assert fail(bridge, "devices.set_parameters", track=0, device=0,
                values=[])["type"] == "bad_args"
    assert fail(bridge, "devices.set_parameters", track=0, device=0,
                values=[{"value": 1}])["type"] == "bad_args"
    assert fail(bridge, "devices.set_parameters", track=0, device=0,
                values=[{"parameter": 1, "value": 1, "extra": 2}])["type"] == "bad_args"
    assert fail(bridge, "devices.set_parameters", track=0, device=0,
                values="nope")["type"] == "bad_args"


def test_reset_parameters(bridge, operator):
    operator.parameters[2].value = 5000.0
    operator.parameters[6].value = 3.0
    result = run(bridge, "devices.reset_parameters", track=0, device=0)
    assert operator.parameters[2].value == 800.0
    assert {"index": 6, "name": "Osc Wave", "reason": "quantized"} in result["skipped"]
    assert all(entry["name"] != "Device On" for entry in result["reset"])
    operator.parameters[1].value = 0.1
    only = run(bridge, "devices.reset_parameters", track=0, device=0, parameters=["Volume"])
    assert [entry["name"] for entry in only["reset"]] == ["Volume"]
    assert operator.parameters[1].value == 0.8


def test_randomize_is_reproducible_and_bounded(bridge, song, operator):
    first = run(bridge, "devices.randomize", track=0, device=0, seed=7)
    values = [p.value for p in operator.parameters]
    assert operator.parameters[0].value == 1.0  # Device On untouched
    assert first["seed"] == 7 and first["changed"] == 6
    run(bridge, "devices.randomize", track=0, device=0, seed=99)
    run(bridge, "devices.randomize", track=0, device=0, seed=7)
    assert [p.value for p in operator.parameters] == values
    for param in operator.parameters:
        assert param.min <= param.value <= param.max
    run(bridge, "devices.set_parameter", track=0, device=0, parameter="Release", value=30.0)
    run(bridge, "devices.randomize", track=0, device=0, parameters=["Release"], amount=0.1,
        seed=3)
    assert 30.0 - 6.0 <= operator.parameters[5].value <= 30.0 + 6.0
    no_quantized = run(bridge, "devices.randomize", track=0, device=0, include_quantized=False)
    assert "Osc Wave" not in [p["name"] for p in no_quantized["parameters"]]
    assert isinstance(no_quantized["seed"], int)
    assert fail(bridge, "devices.randomize", track=0, device=0, amount=0)["type"] == "bad_args"
    assert fail(bridge, "devices.randomize", track=0, device=0, seed="x")["type"] == "bad_args"


# --------------------------------------------------------------------------- state / edit

def test_set_state(bridge, song, operator, app):
    state = run(bridge, "devices.set_state", track=0, device=0, active="toggle")
    assert state["is_active"] is False
    state = run(bridge, "devices.set_state", track=0, device=0, active=True, name="FM Bass",
                collapsed=True, select=True, compare_b=True)
    assert state == {"path": "song.tracks[0].devices[0]", "name": "FM Bass", "is_active": True,
                     "collapsed": True, "selected": True, "compare_b": True}
    assert song.tracks[0].view.selected_device is operator
    rack_state = run(bridge, "devices.set_state", track=0, device="Bass Rack", show_chains=True)
    assert rack_state["show_chains"] is True
    assert fail(bridge, "devices.set_state", track=0, device=0,
                show_chains=True)["type"] == "unsupported"
    assert fail(bridge, "devices.set_state", track=0, device=0,
                active="maybe")["type"] == "bad_args"
    assert fail(bridge, "devices.set_state", track=0, device=0, name=" ")["type"] == "bad_args"


def test_set_state_saves_to_the_compare_slot(bridge, song, operator):
    state = run(bridge, "devices.set_state", track=0, device=0, save_to_compare_slot=True,
                compare_b=True)
    assert state["saved_to_compare_slot"] is True and state["compare_b"] is True
    assert operator._compare_slot_saves == 1
    assert "saved_to_compare_slot" not in run(bridge, "devices.set_state", track=0, device=0)
    operator._can_compare_ab = False
    error = fail(bridge, "devices.set_state", track=0, device=0, save_to_compare_slot=True)
    assert error["type"] == "invalid_state" and "can_compare_ab" in error["message"]
    assert operator._compare_slot_saves == 1
    assert fail(bridge, "devices.set_state", track=0, device=0,
                save_to_compare_slot="yes")["type"] == "bad_args"


def test_set_state_selects_device_on_another_track(bridge, song):
    run(bridge, "devices.set_state", track="Vocals", device="Reverb", select=True)
    assert song.view.selected_track is song.tracks[1]
    assert song.tracks[1].view.selected_device is song.tracks[1].devices[0]


def test_delete_top_level_and_nested(bridge, song):
    result = run(bridge, "devices.delete", track=0, device="Operator")
    assert result["deleted"]["name"] == "Operator"
    assert result["devices"] == ["Bass Rack"]
    nested = run(bridge, "devices.delete", device="song.tracks[2].devices[0].chains[0].devices[0]")
    assert nested["host"] == "song.tracks[2].devices[0].chains[0]"
    assert nested["devices"] == []


@pytest.fixture
def real_insert(browser):
    """The real Live 12.4.5 browser device list (the stub's insert_device already has
    Live's names and errors)."""
    factory.add_browser_devices(browser)
    yield


def test_insert_translates_names_like_live(bridge, song, real_insert):
    """Live's insert_device only knows exact UI names ("operator" -> "Device operator not
    found."); devices.insert maps case, spacing and class names."""
    eq = run(bridge, "devices.insert", name="eq eight", track="Vocals")
    assert eq["class_name"] == "Eq8" and eq["via"] == "insert_device"
    assert eq["requested"] == "eq eight"
    utility = run(bridge, "devices.insert", name="StereoGain", track="Vocals")
    assert utility["name"] == "Utility" and utility["requested"] == "StereoGain"
    glue = run(bridge, "devices.insert", name="glue", track="Vocals")
    assert glue["class_name"] == "GlueCompressor"
    exact = run(bridge, "devices.insert", name="Auto Filter", track="Vocals")
    assert exact["class_name"] == "AutoFilter2" and "requested" not in exact
    error = fail(bridge, "devices.insert", name="Reverbb", track="Vocals")
    assert error["type"] == "not_found" and "'Reverb'" in error["message"]


def test_insert_max_for_live_device_goes_through_the_browser(bridge, song, real_insert):
    """LFO/Shaper/DS Kick ... are Max for Live devices: insert_device cannot create them,
    so devices.insert loads the browser item (end of a track only)."""
    lfo = run(bridge, "devices.insert", name="lfo", track="Vocals")
    assert lfo["via"] == "browser" and lfo["name"] == "LFO"
    assert song.tracks[1].devices[-1].name == "LFO"
    error = fail(bridge, "devices.insert", name="LFO", track="Vocals", index=0)
    assert error["type"] == "unsupported" and "audio_effects/LFO" in error["message"]
    error = fail(bridge, "devices.insert", name="LFO",
                 chain="song.tracks[0].devices[1].chains[0]")
    assert error["type"] == "unsupported"


def test_insert_refusals_carry_lives_reason(bridge, song, real_insert):
    second = fail(bridge, "devices.insert", name="Wavetable", track="Bass")
    assert second["type"] == "invalid_state" and "more than one instrument" in second["message"]
    audio = fail(bridge, "devices.insert", name="Operator", track="Vocals")
    assert "Only audio effects" in audio["message"]
    order = fail(bridge, "devices.insert", name="Arpeggiator", track="Bass", index=1)
    assert "Insert MIDI effects before instruments" in order["message"]


def test_insert_native_device(bridge, song):
    result = run(bridge, "devices.insert", name="EQ Eight", track="Vocals")
    assert result["class_name"] == "Eq8"
    assert result["path"] == "song.tracks[1].devices[1]"
    first = run(bridge, "devices.insert", name="Utility", track="Vocals", index=0, select=True)
    assert first["path"] == "song.tracks[1].devices[0]"
    assert song.tracks[1].view.selected_device is song.tracks[1].devices[0]
    in_chain = run(bridge, "devices.insert", name="Reverb",
                   chain="song.tracks[0].devices[1].chains[0]")
    assert in_chain["path"] == "song.tracks[0].devices[1].chains[0].devices[0]"
    error = fail(bridge, "devices.insert", name="Operator", track="Bass", index=2)
    assert error["type"] == "invalid_state"
    assert fail(bridge, "devices.insert", name="Serum", track="Bass")["type"] == "not_found"
    assert fail(bridge, "devices.insert", name="Reverb", track="Bass",
                index=9)["type"] == "bad_args"
    assert fail(bridge, "devices.insert", name="Reverb",
                chain="song.tracks[0]")["type"] == "bad_args"


def test_insert_reports_unsupported_on_old_live(bridge, monkeypatch):
    monkeypatch.delattr(Live._model._DeviceContainer, "insert_device")
    assert fail(bridge, "devices.insert", name="Reverb", track="Bass")["type"] == "unsupported"


def test_duplicate_and_move(bridge, song):
    copy = run(bridge, "devices.duplicate", track="Vocals", device="Reverb")
    assert copy["path"] == "song.tracks[1].devices[1]"
    assert len(song.tracks[1].devices) == 2
    moved = run(bridge, "devices.move", track="Vocals", device=1, target_track="Bass")
    assert moved["host"] == "song.tracks[0]"
    assert moved["moved"]["path"] == "song.tracks[0].devices[2]"
    into_chain = run(bridge, "devices.move", track="Bass", device=2,
                     target_chain="song.tracks[0].devices[1].chains[1]", position=0)
    assert into_chain["moved"]["path"] == "song.tracks[0].devices[1].chains[1].devices[0]"
    reorder = run(bridge, "devices.move", track="Bass", device="Bass Rack", position=0)
    assert reorder["position"] == 0
    assert fail(bridge, "devices.move", track=0, device=0, position=-5)["type"] == "bad_args"


def test_move_refusal_explains_instruments(bridge, song, monkeypatch):
    """Live 12.4.5 refuses an instrument on an audio track with just "Couldn't move
    device." — the handler adds why."""
    def refuse(device, target, position):
        raise RuntimeError("Couldn't move device.")
    monkeypatch.setattr(song, "move_device", refuse)
    error = fail(bridge, "devices.move", track="Bass", device="Operator", target_track="Vocals")
    assert error["type"] == "invalid_state"
    assert "Couldn't move device." in error["message"] and "MIDI track" in error["message"]


def test_find_across_the_set(bridge, song):
    factory.add_plugin(song.return_tracks[0], "Valhalla", kind="audio_effect")
    everything = run(bridge, "devices.find")
    assert everything["count"] >= 8
    simplers = run(bridge, "devices.find", kind="simpler")
    assert [d["track"] for d in simplers["devices"]] == ["Drums"] * 3
    assert run(bridge, "devices.find", query="valh")["devices"][0]["kind"] == "plugin"
    assert run(bridge, "devices.find", query="valh", include_returns=False)["count"] == 0
    assert run(bridge, "devices.find", type="instrument", include_nested=False)["count"] == 2
    racks = run(bridge, "devices.find", kind="rack")
    assert {d["name"] for d in racks["devices"]} == {"Bass Rack", "Drum Rack"}
    assert run(bridge, "devices.find", class_name="operator")["devices"][0]["path"] == \
        "song.tracks[0].devices[0]"
    limited = run(bridge, "devices.find", limit=2)
    assert limited["count"] == 2 and limited["truncated"] is True
    assert fail(bridge, "devices.find", kind="synth")["type"] == "bad_args"
    assert fail(bridge, "devices.find", type="fx")["type"] == "bad_args"


# --------------------------------------------------------------------------- Simpler

KICK = "song.tracks[2].devices[0].chains[0].devices[0]"


def test_simpler_get(bridge):
    info = run(bridge, "simpler.get", device=KICK)
    assert info["playback_mode"] == "classic"
    assert info["sample"]["file_name"] == "Kick.wav"
    assert info["sample"]["seconds"] == 4.0
    assert info["empty"] is False
    assert fail(bridge, "simpler.get", track=0, device=0)["type"] == "bad_args"


def test_simpler_set_modes_and_sample_settings(bridge, song):
    info = run(bridge, "simpler.set", device=KICK, playback_mode="slicing",
               slicing_playback_mode="poly", voices=4, retrigger=True, warping=True,
               warp_mode="complex_pro", slicing_style="beat", slicing_beat_division="1/8T",
               slicing_sensitivity=0.7, start_marker=100, gain=0.5)
    assert info["playback_mode"] == "slicing"
    assert info["slicing_playback_mode"] == "poly"
    assert info["voices"] == 4
    assert info["sample"]["warp_mode"] == "complex_pro"
    assert info["sample"]["slicing_beat_division"] == "1/8T"
    assert info["sample"]["start_marker"] == 100
    again = run(bridge, "simpler.set", device=KICK, slicing_beat_division="eighth")
    assert again["sample"]["slicing_beat_division"] == "1/8"
    assert fail(bridge, "simpler.set", device=KICK, playback_mode="loop")["type"] == "bad_args"
    empty = factory.add_simpler(song.tracks[0], "Empty")
    error = fail(bridge, "simpler.set", device="song.tracks[0].devices[2]", warping=True)
    assert error["type"] == "invalid_state"
    assert run(bridge, "simpler.set", device="song.tracks[0].devices[2]",
               playback_mode=1)["playback_mode"] == "one_shot"
    assert empty.playback_mode == 1


def test_simpler_actions(bridge, song, tmp_path):
    guess = run(bridge, "simpler.action", device=KICK, action="guess_playback_length")
    assert guess["result"] == 4.0
    warped = run(bridge, "simpler.action", device=KICK, action="warp_as")
    assert warped["result"] == 4.0 and warped["simpler"]["sample"]["warping"] is True
    sliced = run(bridge, "simpler.action", device=KICK, action="insert_slices",
                 slices=[0.5, 1.0], unit="seconds")
    assert sliced["result"] == [22050, 44100]
    assert sliced["simpler"]["sample"]["slices"] == [22050, 44100]
    beats = run(bridge, "simpler.action", device=KICK, action="insert_slices", slices=[3.0],
                unit="beats")
    assert beats["simpler"]["sample"]["slice_count"] == 3
    moved = run(bridge, "simpler.action", device=KICK, action="move_slice", old_time=22050,
                new_time=30000)
    assert 30000 in moved["simpler"]["sample"]["slices"]
    removed = run(bridge, "simpler.action", device=KICK, action="remove_slices",
                  slices=[30100, 99])
    # Live ignores remove_slice for a frame that is not exactly a slice: the handler
    # snaps to the nearest slice within 10 ms and reports the rest
    assert removed["result"] == {"removed": [30000], "not_found": [99]}
    assert 30000 not in removed["simpler"]["sample"]["slices"]
    cleared = run(bridge, "simpler.action", device=KICK, action="clear_slices")
    assert cleared["simpler"]["sample"]["slice_count"] == 0
    for action in ("crop", "reverse", "warp_double", "warp_half", "reset_slices"):
        assert run(bridge, "simpler.action", device=KICK, action=action)["action"] == action
    sample = _wav(tmp_path / "Snare 2.wav")
    replaced = run(bridge, "simpler.action", device=KICK, action="replace_sample",
                   file_path=str(sample))
    assert replaced["simpler"]["sample"]["file_name"] == "Snare 2.wav"
    # the samples.import path rules: Explorer's quotes and file:// URLs work too
    quoted = run(bridge, "simpler.action", device=KICK, action="replace_sample",
                 file_path='"%s"' % sample)
    assert quoted["simpler"]["sample"]["file_name"] == "Snare 2.wav"
    url = run(bridge, "simpler.action", device=KICK, action="replace_sample",
              file_path=sample.as_uri())
    assert url["simpler"]["sample"]["file_name"] == "Snare 2.wav"


def _wav(path, seconds=0.1, rate=22050):
    import struct
    import wave

    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(struct.pack("<h", 0) * int(rate * seconds))
    return path


def test_simpler_action_errors(bridge, song, tmp_path):
    factory.add_simpler(song.tracks[0], "Empty")
    empty = "song.tracks[0].devices[2]"
    assert fail(bridge, "simpler.action", device=empty, action="crop")["type"] == "invalid_state"
    assert fail(bridge, "simpler.action", device=KICK, action="explode")["type"] == "bad_args"
    assert fail(bridge, "simpler.action", device=KICK, action="replace_sample",
                file_path="relative.wav")["type"] == "bad_args"
    missing = fail(bridge, "simpler.action", device=KICK, action="replace_sample",
                   file_path=str(tmp_path / "missing.wav"))
    assert missing["type"] == "not_found"
    (tmp_path / "not-audio.txt").write_text("hello", encoding="utf-8")
    assert fail(bridge, "simpler.action", device=KICK, action="replace_sample",
                file_path=str(tmp_path / "not-audio.txt"))["type"] == "bad_args"
    wrong_os = "/Users/me/kick.wav" if sys.platform.startswith("win") else \
        "C:\\Users\\me\\kick.wav"
    error = fail(bridge, "simpler.action", device=KICK, action="replace_sample",
                 file_path=wrong_os)
    assert error["type"] in ("bad_args", "not_found")
    assert "Windows" in error["message"] or "macOS" in error["message"], error
    # an OS-native absolute path: on Windows (Python 3.13+) "/tmp/x" is not absolute
    assert fail(bridge, "simpler.action", device=KICK, action="insert_slices")["type"] == \
        "bad_args"
    assert fail(bridge, "simpler.action", device=KICK, action="insert_slices", slices=[1.0],
                unit="beats")["type"] == "invalid_state"  # not warped yet
    assert fail(bridge, "simpler.action", device=KICK, action="warp_as",
                beats=-1)["type"] == "bad_args"
    assert fail(bridge, "simpler.action", device=KICK, action="move_slice")["type"] == "bad_args"


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


def test_device_tools_are_registered(fake_app):
    _fake, app = fake_app
    names = tool_names(app)
    missing = [name for name in DEVICE_TOOLS if name not in names]
    assert not missing


def test_device_tools_end_to_end(live_app, song, operator):
    listing = call_tool(live_app, "live_device_list", {"track": "Drums", "tree": True})
    assert listing["devices"][0]["chains"][0]["key"] == "C1"
    got = call_tool(live_app, "live_device_get", {"track": "Bass", "device": "Operator"})
    assert got["device_kind"] == "native"
    params = call_tool(live_app, "live_device_parameters",
                       {"track": 0, "device": 0, "filter": "osc"})
    assert params["parameters"][0]["items"][0] == "Sine"
    one = call_tool(live_app, "live_device_get_parameter",
                    {"track": 0, "device": 0, "parameter": "Osc Wave"})
    assert one["display"] == "Saw"
    changed = call_tool(live_app, "live_device_set_parameter",
                        {"track": 0, "device": 0, "parameter": "Osc Wave", "value": "Square"})
    assert changed["display"] == "Square"
    number = call_tool(live_app, "live_device_set_parameter",
                       {"track": 0, "device": 0, "parameter": "Volume", "value": 1})
    assert number["value"] == 1.0
    switch = call_tool(live_app, "live_device_set_parameter",
                       {"track": 0, "device": 0, "parameter": 0, "value": True})
    assert switch["display"] == "On"
    many = call_tool(live_app, "live_device_set_parameters",
                     {"track": 0, "device": 0, "values": {"Volume": 0.25, "Osc Wave": "Noise"}})
    assert many["count"] == 2
    listed = call_tool(live_app, "live_device_set_parameters", {
        "values": [{"parameter": "song.tracks[0].devices[0].parameters[1]", "value": 0.5}]})
    assert listed["parameters"][0]["value"] == 0.5
    reset = call_tool(live_app, "live_device_reset_parameters",
                      {"track": 0, "device": 0, "parameters": ["Volume"]})
    assert reset["reset"][0]["name"] == "Volume"
    rnd = call_tool(live_app, "live_device_randomize", {"track": 0, "device": 0, "seed": 5})
    assert rnd["seed"] == 5
    state = call_tool(live_app, "live_device_set_state",
                      {"track": 0, "device": 0, "active": "toggle", "select": True})
    assert state["selected"] is True
    inserted = call_tool(live_app, "live_device_insert", {"name": "Utility", "track": "Vocals"})
    assert inserted["class_name"] == "StereoGain"
    dup = call_tool(live_app, "live_device_duplicate", {"track": "Vocals", "device": "Utility"})
    assert dup["path"] == "song.tracks[1].devices[2]"
    moved = call_tool(live_app, "live_device_move",
                      {"track": "Vocals", "device": 2, "target_track": "Drums"})
    assert moved["host"] == "song.tracks[2]"
    found = call_tool(live_app, "live_device_find", {"kind": "simpler"})
    assert found["count"] == 3
    deleted = call_tool(live_app, "live_device_delete", {"track": "Drums", "device": "Utility"})
    assert deleted["deleted"]["name"] == "Utility"
    simpler = call_tool(live_app, "live_simpler_get", {"device": KICK})
    assert simpler["sample"]["file_name"] == "Kick.wav"
    simpler = call_tool(live_app, "live_simpler_set",
                        {"device": KICK, "playback_mode": "one_shot"})
    assert simpler["playback_mode"] == "one_shot"
    action = call_tool(live_app, "live_simpler_action",
                       {"device": KICK, "action": "insert_slices", "slices": [1000, 2000]})
    assert action["result"] == [1000, 2000]


def test_device_tool_errors_are_friendly(live_app):
    error = call_tool(live_app, "live_device_get", {"track": "Bass", "device": "Nope"})
    assert error["type"] == "not_found" and "Operator" in error["error"]


@pytest.mark.parametrize("tool,args", [
    ("live_device_list", {"detail": "huge"}),
    ("live_device_list", {"max_depth": 9}),
    ("live_device_get", {"device": " "}),
    ("live_device_get", {"max_parameters": 0}),
    ("live_device_parameters", {"limit": 5000}),
    ("live_device_parameters", {"offset": -1}),
    ("live_device_get_parameter", {"parameter": "  "}),
    ("live_device_set_parameter", {"parameter": "Volume", "value": " "}),
    ("live_device_set_parameters", {"values": {}}),
    ("live_device_reset_parameters", {"parameters": []}),
    ("live_device_randomize", {"amount": 1.5}),
    ("live_device_set_state", {"active": "sometimes"}),
    ("live_device_insert", {"name": " "}),
    ("live_device_insert", {"name": "Reverb", "index": -2}),
    ("live_device_insert", {"name": "Reverb", "chain": "tracks[0]"}),
    ("live_device_move", {"device": 0, "position": -3}),
    ("live_device_find", {"kind": "synth"}),
    ("live_device_find", {"type": "fx"}),
    ("live_simpler_get", {"max_slices": -1}),
    ("live_simpler_set", {}),
    ("live_simpler_set", {"slicing_sensitivity": 2.0}),
    ("live_simpler_action", {"action": "explode"}),
    ("live_simpler_action", {"action": "replace_sample"}),
    ("live_simpler_action", {"action": "insert_slices"}),
    ("live_simpler_action", {"action": "move_slice", "old_time": 1.0}),
    ("live_simpler_action", {"action": "crop", "unit": "bars"}),
])
def test_device_tools_validate_before_calling_live(fake_app, tool, args):
    fake, app = fake_app
    result = call_tool(app, tool, args)
    assert result["type"] == "bad_args", result
    assert fake.requests == []


def test_device_tools_forward_only_given_arguments(fake_app):
    fake, app = fake_app
    fake.set_result("devices.set_parameter", {"ok": True})
    fake.set_result("devices.find", {"count": 0, "devices": []})
    call_tool(app, "live_device_set_parameter", {"parameter": "Cutoff", "value": "2 kHz"})
    call_tool(app, "live_device_find", {"query": "eq"})
    sent = [(r["cmd"], r.get("args")) for r in fake.requests]
    assert sent[0] == ("devices.set_parameter", {"parameter": "Cutoff", "value": "2 kHz",
                                                 "normalized": False})
    assert sent[1] == ("devices.find", {"query": "eq", "include_nested": True,
                                        "include_returns": True, "limit": 100})


def test_list_full_detail_has_parameters_with_paths(bridge):
    result = run(bridge, "devices.list", track="Bass", detail="full")
    operator = result["devices"][0]
    assert operator["parameters"][6]["items"] == ["Sine", "Saw", "Square", "Noise"]
    assert "path" not in operator["parameters"][0]
    tree = run(bridge, "devices.list", track="Drums", tree=True, detail="full")
    kick = tree["devices"][0]["chains"][0]["devices"][0]
    assert kick["name"] == "Kick" and kick["parameters"][0]["name"] == "Device On"


def test_display_search_handles_synced_fraction_values(bridge, song):
    names = ["1/32", "1/16", "1/8", "1/4", "1/2", "1"]
    device = factory.add_device(song.tracks[1], "Synced", kind="audio_effect")
    param = EXT.CurvedParameter("Rate", lambda v: names[int(round(v))], 0.0, 5.0, 0.0, device)
    device._parameters.append(param)
    for text in ("1/8", "1/2", "1"):
        result = run(bridge, "devices.set_parameter", track="Vocals", device="Synced",
                     parameter="Rate", value=text)
        assert result["display"] == text


# --------------------------------------------------------------------------- class-specific API

from live_stub_ext import conversions_racks as conversions_ext  # noqa: E402
from live_stub_ext import device_classes as classes_ext  # noqa: E402

CLASSES = classes_ext.install(Live)


def test_device_properties_wavetable(bridge, song):
    """Wavetable's wavetable category/index, enum modes and voices are listed with their
    choices and can be set by name; the wavetable list follows the category."""
    wavetable = CLASSES.add_wavetable(factory.add_track(song, "Pad", "midi"))
    path = "song.tracks[3].devices[0]"
    data = run(bridge, "devices.properties", device=path)
    rows = {r["name"]: r for r in data["properties"]}
    assert rows["oscillator_1_wavetable_category"]["choices"] == ["Basics", "Collection",
                                                                  "Complex"]
    assert rows["oscillator_1_wavetable_index"]["choices"] == ["Basic Shapes", "Saw", "Square"]
    assert rows["oscillator_1_wavetable_index"]["value_name"] == "Basic Shapes"
    assert rows["unison_mode"]["value_name"] == "none" and "classic" in \
        rows["unison_mode"]["choices"]
    assert rows["poly_voices"]["value_name"] == "six" and rows["poly_voices"]["writable"]
    assert "oscillator_1_wavetables" not in rows, "a choice list is shown inside its property"
    assert "visible_modulation_target_names" in rows and \
        not rows["visible_modulation_target_names"]["writable"]
    assert "add_parameter_to_modulation_matrix" in data["actions"]
    changed = run(bridge, "devices.set_properties", device=path,
                  values={"oscillator_1_wavetable_category": "collection",
                          "oscillator_1_wavetable_index": "Bells",
                          "unison_mode": "Classic", "poly_voices": 7})
    assert wavetable.oscillator_1_wavetable_category == 1
    assert wavetable.oscillator_1_wavetable_index == 1, "Bells only exists after the category"
    assert int(wavetable.unison_mode) == 1 and int(wavetable.poly_voices) == 7
    names = {c["name"]: c for c in changed["changed"]}
    assert names["oscillator_1_wavetable_index"]["value_name"] == "Bells"
    assert names["unison_mode"]["value_name"] == "classic"
    got = run(bridge, "devices.get", device=path)
    assert "oscillator_1_wavetable_category" in got["class_properties"]


def test_device_properties_validation_is_atomic(bridge, song):
    eq = CLASSES.add_eq8(song.tracks[1])
    path = "song.tracks[1].devices[1]"
    rows = {r["name"]: r for r in run(bridge, "devices.properties", device=path)["properties"]}
    assert rows["edit_mode"]["choices"] == ["a", "b"] and rows["edit_mode"]["value"] is False
    assert rows["global_mode"]["value_name"] == "stereo"
    for values, kind in [({"edit_mode": "c"}, "not_found"), ({"nope": 1}, "not_found"),
                         ({"oversample": "yes"}, "bad_args"),
                         ({"visible_macro_count": 3}, "not_found")]:
        assert fail(bridge, "devices.set_properties", device=path, values=values)["type"] \
            == kind, values
    error = fail(bridge, "devices.set_properties", device=path,
                 values={"global_mode": "mid_side", "edit_mode": 7})
    assert error["type"] == "bad_args"
    assert int(eq.global_mode) == 0, "nothing is written when one value is invalid"
    done = run(bridge, "devices.set_properties", device=path,
               values={"global_mode": "Mid Side", "edit_mode": 1, "oversample": True})
    assert eq.global_mode == 2 and eq.edit_mode is True and eq.oversample is True
    assert {c["name"] for c in done["changed"]} == {"global_mode", "edit_mode", "oversample"}
    reverb = CLASSES.add_hybrid_reverb(song.tracks[1])
    run(bridge, "devices.set_properties", device="song.tracks[1].devices[2]",
        values={"ir_category_index": "Rooms", "ir_file_index": "studio"})
    assert reverb.ir_category_index == 1 and reverb.ir_file_index == 1
    read_only = CLASSES.add_looper(song.tracks[1])
    assert fail(bridge, "devices.set_properties", device="song.tracks[1].devices[3]",
                values={"loop_length": 4.0})["message"].endswith("read-only")
    assert read_only.loop_length == 0.0


def test_simpler_sample_properties(bridge, song):
    data = run(bridge, "devices.properties", track="Drums",
               device="song.tracks[2].devices[0].chains[0].devices[0]")
    rows = {r["name"]: r for r in data["properties"]}
    assert "note_pitch_bend_range" in rows and "sample" not in rows
    sample_rows = {r["name"]: r for r in data["sample_properties"]}
    assert "sample.warp_mode" in sample_rows and "sample.slices" not in sample_rows
    run(bridge, "devices.set_properties",
        device="song.tracks[2].devices[0].chains[0].devices[0]",
        values={"sample.gain": 0.5, "note_pitch_bend_range": 12})
    simpler = song.tracks[2].devices[0].chains[0].devices[0]
    assert simpler.note_pitch_bend_range == 12 and simpler.sample.gain == 0.5


def test_device_action_looper(bridge, song):
    looper = CLASSES.add_looper(song.tracks[1])
    path = "song.tracks[1].devices[1]"
    listed = run(bridge, "devices.properties", device=path)
    assert {"record", "overdub", "export_to_clip_slot", "double_speed"} <= \
        set(listed["actions"])
    assert run(bridge, "devices.action", device=path, action="record")["action"] == "record"
    exported = run(bridge, "devices.action", device=path, action="export_to_clip_slot",
                   target_track="Bass", slot=2)
    assert looper.calls == ["record", "export_to_clip_slot"]
    assert song.tracks[0].clip_slots[2].has_clip and exported["result"] is None
    error = fail(bridge, "devices.action", device=path, action="export_to_clip_slot",
                 target_track="Bass", slot=2)
    assert error["type"] == "invalid_state" and "not empty" in error["message"]
    assert fail(bridge, "devices.action", device=path, action="__init__")["type"] == "bad_args"
    assert fail(bridge, "devices.action", device=path, action="store_chosen_bank",
                args=[0, 0])["type"] == "bad_args"
    compare = run(bridge, "devices.action", track="Bass", device="Operator",
                  action="save_preset_to_compare_ab_slot")
    assert compare["action"] == "save_preset_to_compare_ab_slot"


def test_wavetable_modulation_matrix(bridge, song):
    wavetable = CLASSES.add_wavetable(factory.add_track(song, "Lead", "midi"))
    path = "song.tracks[3].devices[0]"
    data = run(bridge, "devices.modulation", device=path)
    assert data["sources"][3] == "lfo_1" and [t["name"] for t in data["targets"]] == \
        ["Osc 1 Pos", "Filter 1 Freq"]
    changed = run(bridge, "devices.modulation", device=path, op="set", target="filter 1",
                  source="LFO 1", amount=0.5)
    assert changed["changed"] == {"target": "Filter 1 Freq", "source": "lfo_1", "was": 0.0,
                                  "amount": 0.5}
    assert changed["targets"][1]["amounts"] == {"lfo_1": 0.5}
    assert wavetable.get_modulation_value(1, 3) == 0.5
    error = fail(bridge, "devices.modulation", device=path, op="add", parameter="Osc 1 Transp")
    assert error["type"] == "invalid_state"
    assert fail(bridge, "devices.modulation", device=path, op="set", target="Osc 9",
                source=0, amount=1.0)["type"] == "not_found"
    assert fail(bridge, "devices.modulation", device=path, op="set", target=0, source="lfo 9",
                amount=1.0)["type"] == "bad_args"
    assert fail(bridge, "devices.modulation", track="Bass", device="Operator")["type"] == \
        "unsupported"


def test_simpler_to_drum_rack_conversion(bridge, song, tmp_path):
    simpler = factory.add_simpler(factory.add_track(song, "Chops", "midi"), "Loop",
                                  file_path="/Samples/loop.wav")
    path = "song.tracks[3].devices[0]"
    conversions_ext.uninstall(Live)
    try:
        assert fail(bridge, "simpler.action", device=path, action="to_drum_rack")["type"] == \
            "invalid_state", "not in slicing mode yet"
        simpler.playback_mode = 2
        assert fail(bridge, "simpler.action", device=path, action="to_drum_rack")["type"] == \
            "unsupported"
        conversions_ext.install(Live)
        simpler.sample.insert_slice(1000)
        simpler.sample.insert_slice(50000)
        done = run(bridge, "simpler.action", device=path, action="to_drum_rack")
        assert done["via"] == "Live.Conversions.sliced_simpler_to_drum_rack"
        rack = song.tracks[3].devices[0]
        assert rack.can_have_drum_pads and done["rack"]["path"] == path
        assert done["rack"]["pads"] == len(rack.chains) == 2
    finally:
        conversions_ext.uninstall(Live)


def test_new_device_tools(live_app, song):
    CLASSES.add_eq8(song.tracks[1])
    CLASSES.add_wavetable(factory.add_track(song, "Pad", "midi"))
    read = call_tool(live_app, "live_device_properties", {"device": "song.tracks[1].devices[1]"})
    assert {r["name"] for r in read["properties"]} >= {"edit_mode", "global_mode", "oversample"}
    written = call_tool(live_app, "live_device_properties",
                        {"device": "song.tracks[1].devices[1]",
                         "values": {"global_mode": "left_right"}})
    assert written["changed"][0]["value_name"] == "left_right"
    matrix = call_tool(live_app, "live_device_modulation",
                       {"track": "Pad", "device": "Wavetable", "op": "set",
                        "target": "Osc 1 Pos", "source": "lfo_2", "amount": -0.25})
    assert matrix["changed"]["amount"] == -0.25
    action = call_tool(live_app, "live_device_action",
                       {"track": "Pad", "device": 0, "action": "is_parameter_modulatable",
                        "parameter": "Osc 1 Pos"})
    assert action["result"] is True
    for name, args in [("live_device_properties", {"values": {}}),
                       ("live_device_action", {"action": " "}),
                       ("live_device_action", {"action": "record", "slot": 1}),
                       ("live_device_modulation", {"op": "wipe"}),
                       ("live_device_modulation", {"op": "set", "target": 0})]:
        assert call_tool(live_app, name, args)["type"] == "bad_args", (name, args)
