"""Plug-ins as real Live 12.4.5 exposes them (T4, verified with Xfer Serum 2 VST3/AU and Apple
AUs on macOS): every plug-in parameter name but only the Configure list as parameters, Serum 2
starting with none, duplicate AU names, a "Default"-only preset list, the browser's
AUv2 / VST / VST3 + vendor folders — handlers through the dispatcher and the MCP tools."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from pathlib import Path

import pytest

import Live

from live_stub import factory
from live_stub_ext import plugins_live

_TESTS = Path(__file__).resolve().parent
for _path in (str(_TESTS.parent / "mcp_server"), str(_TESTS)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from fake_bridge import FakeBridge  # noqa: E402
from livebridge_mcp.client import BridgeClient  # noqa: E402
from livebridge_mcp.server import create_app  # noqa: E402

from LiveBridge.handlers import browser as browser_mod  # noqa: E402
from LiveBridge.handlers import plugins as plugins_mod  # noqa: E402

EXT = plugins_live.install(Live)

NBAND = ["Global Gain"] + ["Bypass", "Type", "Frequency", "Gain", "Bandwidth"] * 8


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


def _hz(value):
    hz = 8.0 * (2750.0 ** value)
    return "%.0f Hz" % hz if hz < 1000 else "%.2f kHz" % (hz / 1000.0)


def _db(value):
    return "%.2f dB" % (-24.0 + 36.0 * value)


@pytest.fixture
def plugin_browser(browser):
    """``browser.plugins`` exactly as Live 12.4.5 shows it on the test Mac (trimmed)."""
    root = browser.plugins
    au = factory.add_browser_item(root, "AUv2", uri="query:Plugins#AUv2", is_folder=True)
    apple = factory.add_browser_item(au, "Apple", uri="query:Plugins#AUv2:Apple",
                                     is_folder=True)
    for name in ("AUDelay", "AUNBandEQ", "AUMIDISynth"):
        factory.add_browser_item(apple, name, uri="query:Plugins#AUv2:Apple:" + name,
                                 load_kind="device")
    for fmt in ("AUv2", "VST3"):
        top = au if fmt == "AUv2" else factory.add_browser_item(
            root, "VST3", uri="query:Plugins#VST3", is_folder=True)
        xfer = factory.add_browser_item(top, "Xfer Records",
                                        uri="query:Plugins#%s:Xfer%%20Records" % fmt,
                                        is_folder=True)
        for name in ("Serum 2", "Serum 2 FX"):
            factory.add_browser_item(xfer, name, uri="query:Plugins#%s:Xfer%%20Records:%s"
                                     % (fmt, name.replace(" ", "%20")), load_kind="device")
    vst = factory.add_browser_item(root, "VST", uri="query:Plugins#VST", is_folder=True)
    factory.add_browser_item(vst, "MPC Beats", uri="query:Plugins#VST:Local:MPC%20Beats",
                             load_kind="device")
    browser_mod.clear_cache()
    yield browser
    browser_mod.clear_cache()


@pytest.fixture
def serum(song, plugin_browser):
    """Serum 2 VST3 right after loading: 2623 plug-in parameters, none exposed."""
    track = song.tracks[0]
    return EXT.add_real_plugin(track, "Serum 2", plugins_live.serum2_names(), index=0)


@pytest.fixture
def nband(song, plugin_browser):
    """AUNBandEQ: 41 parameters, all exposed, five names repeated eight times."""
    return EXT.add_real_plugin(song.tracks[1], "AUNBandEQ", NBAND,
                               [(n, 0.5) for n in NBAND], kind="audio_effect",
                               class_name="AuPluginDevice")


# --------------------------------------------------------------------------- stub fidelity

def test_serum2_names_match_real_live():
    names = plugins_live.serum2_names()
    assert len(names) == 2623
    assert names[:3] == ["Main Vol", "Main Tuning", "Amp"]
    assert names[205] == "Filter 1 Freq" and names[440] == "Macro 1"
    assert names[-3:] == ["CC127 Chan 16", "Mod Wheel", "Pitch Bend"]


# --------------------------------------------------------------------------- formats, vendor

def test_uri_and_folder_formats_of_live_12_4_5():
    assert plugins_mod._uri_format("query:Plugins#AUv2:Apple:AUDelay") == "AU"
    assert plugins_mod._uri_format("query:Plugins#VST3:Xfer%20Records:Serum%202") == "VST3"
    assert plugins_mod._uri_format("query:Plugins#VST:Local:MPC%20Beats") == "VST2"
    assert plugins_mod._folder_format("AUv2") == "AU"
    assert plugins_mod._folder_format("VST") == "VST2"


def test_format_index_records_vendors(plugin_browser):
    ctx = type("Ctx", (), {"browser": plugin_browser})()
    index, complete = plugins_mod.plugin_format_index(ctx)
    assert complete
    assert index["serum 2"] == {"AU": "Xfer Records", "VST3": "Xfer Records"}
    assert index["audelay"] == {"AU": "Apple"}
    assert index["mpc beats"] == {"VST2": None}        # "Local" is not a vendor


def test_get_serum_right_after_loading(bridge, serum):
    info = run(bridge, "plugins.get", track=0, device="Serum 2")
    assert info["format"] == "VST3" and info["vendor"] == "Xfer Records"
    assert info["parameter_count"] == 0 and info["parameter_names"] == []
    assert info["plugin_parameter_count"] == 2623 and info["not_exposed"] == 2623
    assert "EMPTY" in info["configure_hint"]
    assert info["selected_preset"] == "Default"
    listing = run(bridge, "plugins.list")
    entry = [p for p in listing["plugins"] if p["name"] == "Serum 2"][0]
    assert entry["path"] in listing["needs_configure"]
    assert entry["plugin_parameter_count"] == 2623
    assert listing["installed"]["formats"]["AU"] >= 4


def test_au_serum_is_au(bridge, song, plugin_browser):
    EXT.add_real_plugin(song.tracks[0], "Serum 2", plugins_live.serum2_names()[:-1],
                        class_name="AuPluginDevice", index=0)
    info = run(bridge, "plugins.get", track=0, device="Serum 2")
    assert info["format"] == "AU" and info["vendor"] == "Xfer Records"
    assert info["plugin_parameter_count"] == 2622


# --------------------------------------------------------------------------- parameters

def test_parameters_scope_all_searches_the_whole_plugin(bridge, serum):
    exposed = run(bridge, "plugins.parameters", track=0, device="Serum 2")
    assert exposed["total"] == 1 and exposed["exposed_count"] == 0
    assert exposed["plugin_parameter_count"] == 2623 and "configure_hint" in exposed

    def names(filter_text, **extra):
        result = run(bridge, "plugins.parameters", track=0, device="Serum 2", scope="all",
                     filter=filter_text, limit=50, **extra)
        return [p["name"] for p in result["parameters"]]
    assert names("cutoff") == ["Filter 1 Freq", "Filter 2 Freq", "Cutoff Rand"]
    assert names("filter 1 cutoff") == ["Filter 1 Freq"]
    assert names("master volume") == ["Main Vol"]
    assert names("osc a wavetable position") == ["A WT Pos", "A Uni WT Pos"]
    assert names("resonance") == ["Filter 1 Res", "Filter 2 Res"]
    assert len(names("macro")) == 8 and len(names("lfo rate")) == 10
    page = run(bridge, "plugins.parameters", track=0, device="Serum 2", scope="all", limit=2)
    assert page["parameters"][0] == {"plugin_index": 0, "name": "Main Vol", "exposed": False}
    assert page["next_offset"] == 2 and page["total"] == 2623
    assert fail(bridge, "plugins.parameters", track=0, device="Serum 2",
                scope="some")["type"] == "bad_args"


def test_unexposed_parameter_errors_explain_configure(bridge, serum):
    error = fail(bridge, "devices.set_parameter", track=0, device="Serum 2",
                 parameter="filter 1 cutoff", value="2 kHz")
    assert error["type"] == "not_found"
    assert "'Filter 1 Freq' is a parameter of 'Serum 2' but Live does not expose it" \
        in error["message"] and "Configure" in error["message"]
    error = fail(bridge, "devices.get_parameter", track=0, device="Serum 2", parameter="wobble")
    assert "nor the plug-in's 2623 parameters" in error["message"]
    error = fail(bridge, "devices.set_parameters", track=0, device="Serum 2",
                 values={"Main Vol": "-6 dB"})
    assert "does not expose" in error["message"]


def test_configured_parameters_work_with_fuzzy_names(bridge, serum):
    freq = EXT.configure(serum, "Filter 1 Freq", 0.5, _hz)
    EXT.configure(serum, "Env 1 Attack", 0.1)
    result = run(bridge, "devices.set_parameter", track=0, device="Serum 2",
                 parameter="filter 1 cutoff", value="2.00 kHz")
    assert result["name"] == "Filter 1 Freq" and result["display"] == "2.00 kHz"
    assert freq.str_for_value(freq.value) == "2.00 kHz"
    # a single exposed filter parameter: "cutoff" resolves to it through the synonyms
    assert run(bridge, "devices.get_parameter", track=0, device="Serum 2",
               parameter="cutoff")["name"] == "Filter 1 Freq"
    assert run(bridge, "devices.get_parameter", track=0, device="Serum 2",
               parameter="env 1 atk")["name"] == "Env 1 Attack"
    listing = run(bridge, "plugins.parameters", track=0, device="Serum 2", scope="all",
                  filter="filter 1 freq")
    assert listing["parameters"][0]["exposed"] is True
    assert listing["parameters"][0]["index"] == 1
    info = run(bridge, "plugins.get", track=0, device="Serum 2")
    assert info["parameter_names"] == ["Filter 1 Freq", "Env 1 Attack"]
    assert info["not_exposed"] == 2621 and "configure_hint" not in info


def test_exposure_status(bridge, serum):
    status = run(bridge, "plugins.exposure", track=0, device="Serum 2",
                 parameters=["filter 1 cutoff", "env 1 attack", "Macro 1", "cutoff", "wobble"])
    by_query = {p["query"]: p for p in status["parameters"]}
    assert by_query["filter 1 cutoff"]["name"] == "Filter 1 Freq"
    assert by_query["cutoff"]["error"] == "ambiguous"
    assert set(by_query["cutoff"]["candidates"]) == {"Filter 1 Freq", "Filter 2 Freq",
                                                     "Cutoff Rand"}
    assert "no parameter like" in by_query["wobble"]["error"]
    assert status["missing"] == ["Filter 1 Freq", "Env 1 Attack", "Macro 1"]
    assert status["all_exposed"] is False and len(status["steps"]) == 4
    EXT.configure(serum, "Filter 1 Freq")
    EXT.configure(serum, "Macro 1")
    status = run(bridge, "plugins.exposure", track=0, device="Serum 2",
                 parameters=["filter 1 cutoff", "Macro 1"])
    assert status["all_exposed"] is True and status["missing"] == []
    assert [p["index"] for p in status["parameters"]] == [1, 2]
    assert "steps" not in status
    assert fail(bridge, "plugins.exposure", track=0, device="Serum 2",
                parameters=[""])["type"] == "bad_args"


def test_duplicate_au_names(bridge, nband):
    error = fail(bridge, "devices.set_parameter", track=1, device="AUNBandEQ",
                 parameter="Frequency", value=0.2)
    assert error["type"] == "bad_args" and "occurs 8 times" in error["message"]
    third = run(bridge, "devices.set_parameter", track=1, device="AUNBandEQ",
                parameter="Frequency #3", value=0.2)
    assert third["index"] == 14 and nband.parameters[14].value == 0.2
    assert run(bridge, "devices.set_parameter", track=1, device="AUNBandEQ",
               parameter="bw #2", value=0.3)["index"] == 11
    assert run(bridge, "devices.get_parameter", track=1, device="AUNBandEQ",
               parameter="master volume")["name"] == "Global Gain"
    assert fail(bridge, "devices.get_parameter", track=1, device="AUNBandEQ",
                parameter="frequency #9")["type"] == "not_found"
    assert run(bridge, "plugins.get", track=1, device="AUNBandEQ")["parameter_count"] == 41


def test_dynamic_name_list_keeps_counts_sane(bridge, nband):
    EXT.hide(nband, "Bandwidth", 4)            # band 4 switched to a shelf
    info = run(bridge, "plugins.get", track=1, device="AUNBandEQ")
    assert info["plugin_parameter_count"] == 40 and info["parameter_count"] == 41
    assert "not_exposed" not in info
    listing = run(bridge, "plugins.parameters", track=1, device="AUNBandEQ", scope="all",
                  filter="bandwidth")
    assert listing["matched"] == 7 and all(p["exposed"] for p in listing["parameters"])


def test_presets_note_for_default_only(bridge, serum):
    presets = run(bridge, "plugins.presets", track=0, device="Serum 2")
    assert presets["presets"] == [{"index": 0, "name": "Default"}]
    assert "own preset browser" in presets["note"]
    assert run(bridge, "plugins.set", track=0, device="Serum 2",
               preset="default")["selected_preset"] == "Default"
    assert fail(bridge, "plugins.set", track=0, device="Serum 2", preset=1)["type"] == \
        "not_found"


def test_editor_window(bridge, serum):
    serum._is_editor_open = True                 # Live's Auto-Open Plug-In Windows
    assert run(bridge, "plugins.set", track=0, device="Serum 2",
               editor_open=False)["editor_open"] is False
    assert serum.is_editor_open is False
    assert fail(bridge, "plugins.set", track=0, device="Serum 2",
                editor_open="yes")["type"] == "bad_args"


# --------------------------------------------------------------------------- browser search

def _search(bridge, query, **extra):
    result = run(bridge, "browser.search", query=query, root="plugins", limit=5, **extra)
    return [r["path"] for r in result["results"]]


def test_plugin_search_prefers_the_exact_plugin(bridge, plugin_browser):
    assert _search(bridge, "Serum 2")[0] == "plugins/VST3/Xfer Records/Serum 2"
    assert _search(bridge, "serum2")[0] == "plugins/VST3/Xfer Records/Serum 2"
    assert _search(bridge, "Xfer Records/Serum 2")[:2] == [
        "plugins/VST3/Xfer Records/Serum 2", "plugins/AUv2/Xfer Records/Serum 2"]
    assert _search(bridge, "xfer/serum 2 fx")[0] == "plugins/VST3/Xfer Records/Serum 2 FX"
    assert _search(bridge, "Serum 2", plugin_format="au")[0] == \
        "plugins/AUv2/Xfer Records/Serum 2"
    # Live's plug-in items carry no device type: "FX" in the name decides for effects
    assert _search(bridge, "Serum 2", category="audio_effect")[0] == \
        "plugins/VST3/Xfer Records/Serum 2 FX"
    assert _search(bridge, "Serum 2", category="instrument")[0] == \
        "plugins/VST3/Xfer Records/Serum 2"


# --------------------------------------------------------------------------- MCP tools

@pytest.fixture
def fake_app():
    fake = FakeBridge().start()
    client = BridgeClient(host=fake.host, port=fake.port, timeout=5.0)
    try:
        yield fake, create_app(client)
    finally:
        client.close()
        fake.stop()


@pytest.fixture
def live_app(tcp_bridge):
    client = BridgeClient(host="127.0.0.1", port=tcp_bridge.port, timeout=5.0)
    try:
        yield create_app(client)
    finally:
        client.close()


def test_configure_tool_end_to_end(live_app, serum):
    status = call_tool(live_app, "live_plugin_configure",
                       {"track": 0, "device": "Serum 2",
                        "parameters": ["filter 1 cutoff", "Macro 1"]})
    assert status["missing"] == ["Filter 1 Freq", "Macro 1"] and "waited_s" not in status

    def user_configures():
        time.sleep(0.6)
        EXT.configure(serum, "Filter 1 Freq")
        time.sleep(0.6)
        EXT.configure(serum, "Macro 1")
    worker = threading.Thread(target=user_configures)
    worker.start()
    started = time.monotonic()
    status = call_tool(live_app, "live_plugin_configure",
                       {"track": 0, "device": "Serum 2", "wait_seconds": 20,
                        "parameters": ["filter 1 cutoff", "Macro 1"], "open_editor": True})
    worker.join()
    assert status["all_exposed"] is True and status["timed_out"] is False
    assert status["newly_exposed"] == ["Filter 1 Freq", "Macro 1"]
    assert time.monotonic() - started < 10
    assert serum.is_editor_open is True


def test_configure_tool_times_out(fake_app):
    fake, app = fake_app
    fake.set_result("plugins.exposure", {"all_exposed": False, "missing": ["Macro 1"],
                                         "parameters": [{"query": "m", "name": "Macro 1",
                                                         "exposed": False}]})
    status = call_tool(app, "live_plugin_configure",
                       {"parameters": ["Macro 1"], "wait_seconds": 1.2})
    assert status["timed_out"] is True and status["newly_exposed"] == []
    assert status["waited_s"] >= 1.0
    assert all(r["cmd"] == "plugins.exposure" for r in fake.requests)


@pytest.mark.parametrize("args", [
    {"parameters": []}, {"parameters": [""]}, {"parameters": ["a"], "wait_seconds": 601},
    {"parameters": ["a"], "device": ""},
])
def test_configure_tool_validates_locally(fake_app, args):
    fake, app = fake_app
    assert call_tool(app, "live_plugin_configure", args)["type"] == "bad_args"
    assert fake.requests == []


def test_parameters_tool_forwards_scope(fake_app):
    fake, app = fake_app
    call_tool(app, "live_plugin_parameters", {"scope": "all", "filter": "cutoff"})
    assert fake.requests[-1]["args"]["scope"] == "all"
    assert call_tool(app, "live_plugin_parameters", {"scope": "every"})["type"] == "bad_args"


def test_load_plugin_can_close_the_window(fake_app):
    fake, app = fake_app
    fake.set_result("browser.load", {"loaded": {"name": "Serum 2"},
                                     "inserted": [{"path": "song.tracks[4].devices[0]",
                                                   "class_name": "PluginDevice"}]})
    fake.set_result("plugins.set", {"editor_open": False})
    result = call_tool(app, "live_browser_load",
                       {"query": "Xfer Records/Serum 2", "category": "plugin",
                        "editor_open": False})
    assert result["editor_open"] is False
    assert [r["cmd"] for r in fake.requests[-2:]] == ["browser.load", "plugins.set"]
    assert fake.requests[-1]["args"] == {"device": "song.tracks[4].devices[0]",
                                         "editor_open": False}
    fake.requests.clear()
    call_tool(app, "live_browser_load", {"query": "Serum 2", "category": "plugin"})
    assert [r["cmd"] for r in fake.requests] == ["browser.load"]


def test_hints_point_to_plugin_racks_expose_first(bridge, serum):
    """No user click needed for VST3: every Configure hint names plugin_racks.expose first."""
    info = run(bridge, "plugins.get", track=0, device="Serum 2")
    hint = info["configure_hint"]
    assert hint.index("plugin_racks.expose") < hint.index("Configure on the device")
    assert "live_plugin_expose" in hint and "Serum 2" in hint
    error = fail(bridge, "devices.set_parameter", track=0, device="Serum 2",
                 parameter="filter 1 cutoff", value="2 kHz")
    assert "plugin_racks.expose" in error["message"]
    status = run(bridge, "plugins.exposure", track=0, device="Serum 2",
                 parameters=["filter 1 cutoff"])
    assert "live_plugin_expose" in status["alternative"]
    assert status["steps"][0].startswith("Fallback for AU / VST2")


def _serum_level(value):
    """Serum 2's compound level display: percent plus the dB value in brackets."""
    import math
    db = 20.0 * math.log10(value) if value > 0 else float("-inf")
    return "%d%% [%s dB]" % (round(value * 100), "-inf" if db == float("-inf") else "%.1f" % db)


def test_compound_displays_accept_percent_and_db(bridge, serum):
    """'Main Vol'='70%' and '-6 dB' both work on Serum 2's '50% [-9.0 dB]' displays."""
    from LiveBridge.handlers import devices as devices_mod

    assert devices_mod.parse_display("50% [-9.0 dB]") == (50.0, "%")
    assert devices_mod.display_readings("50% [-9.0 dB]") == [(50.0, "%"), (-9.0, "db")]
    assert devices_mod.display_readings("0% [-inf dB]")[1] == (float("-inf"), "db")
    assert devices_mod.display_readings("800 Hz") == [(800.0, "hz")]
    main = EXT.configure(serum, "Main Vol", 0.5, _serum_level)
    level = EXT.configure(serum, "A Level", 0.5, _serum_level)
    result = run(bridge, "devices.set_parameter", track=0, device="Serum 2",
                 parameter="Main Vol", value="70%")
    assert result["display"].startswith("70% [")
    assert abs(main.value - 0.70) < 0.006
    result = run(bridge, "devices.set_parameter", track=0, device="Serum 2",
                 parameter="A Level", value="-6 dB")
    assert result["display"].endswith("[-6.0 dB]"), result
    assert abs(level.value - 10 ** (-6 / 20.0)) < 0.002
    run(bridge, "devices.set_parameters", track=0, device="Serum 2",
        values={"Main Vol": "-12 dB", "A Level": "25%"})
    assert _serum_level(main.value).endswith("[-12.0 dB]")
    assert _serum_level(level.value).startswith("25% [")
    error = fail(bridge, "devices.set_parameter", track=0, device="Serum 2",
                 parameter="Main Vol", value="800 Hz")
    assert "does not match" in error["message"] and "'db'" in error["message"]


def test_automation_display_values_read_compound_displays(bridge, serum):
    from LiveBridge.handlers import automation as automation_mod

    main = EXT.configure(serum, "Main Vol", 0.5, _serum_level)
    value, clamped = automation_mod.value_for_display(main, "-6 dB")
    assert not clamped and _serum_level(value).endswith("[-6.0 dB]")
    value, _clamped = automation_mod.value_for_display(main, "40%")
    assert _serum_level(value).startswith("40% [")
    assert automation_mod.parse_display("50% [-9.0 dB]") == (50.0, "%")


def test_device_summaries_show_exposed_vs_total_plugin_parameters(bridge, serum):
    """devices.get / devices.list tell at a glance that Serum 2 exposes 0 of 2623."""
    got = run(bridge, "devices.get", track=0, device="Serum 2")
    assert got["exposed_parameter_count"] == 0 and got["plugin_parameter_count"] == 2623
    listing = run(bridge, "devices.list", track=0, detail="summary")
    row = [d for d in listing["devices"] if d["name"] == "Serum 2"][0]
    assert (row["exposed_parameter_count"], row["plugin_parameter_count"]) == (0, 2623)
    EXT.configure(serum, "Filter 1 Freq", 0.5, _hz)
    assert run(bridge, "devices.get", track=0, device="Serum 2")["exposed_parameter_count"] == 1
    native = run(bridge, "devices.get", track="Vocals", device=0)
    assert "plugin_parameter_count" not in native
