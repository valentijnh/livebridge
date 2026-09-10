"""Module D — third-party plug-ins: handlers through the dispatcher and the MCP tools."""

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

from LiveBridge.handlers import plugins as plugins_mod  # noqa: E402

PLUGIN_TOOLS = ("live_plugin_list", "live_plugin_get", "live_plugin_presets", "live_plugin_set",
                "live_plugin_parameters")


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
def plugins(song, browser):
    """Serum (VST3 in the browser) on Vocals, Diva (installed as VST2 and VST3) on a return,
    an AU inside the Bass rack and a Configure-less plug-in.  Like real Live 12.4.5 the
    Configure-less ones still list their parameters in ``get_parameter_names()``."""
    vst2 = factory.add_browser_item(browser.plugins, "VST", is_folder=True, source="plugins")
    factory.add_browser_item(vst2, "Diva", uri="query:Plugins#VST:Diva", is_device=True,
                             load_kind="device")
    factory.add_browser_item(vst2, "OldSynth", uri="query:Plugins#VST:OldSynth",
                             is_device=True, load_kind="device")
    serum = factory.add_plugin(song.tracks[1], "Serum",
                               params=[("Cutoff", 0.5), ("Resonance", 0.1),
                                       ("Osc A Level", 0.75)],
                               presets=("Init", "Bass - Reese", "Lead - Saw", "Pad - Air"))
    diva = factory.add_plugin(song.return_tracks[0], "Diva", kind="audio_effect",
                              params=[("VCF Cutoff", 0.3)], presets=())
    au = factory.add_plugin(song.tracks[0].devices[1].chains[0], "AUSampler",
                            class_name="AuPluginDevice", kind="audio_effect", presets=("A",),
                            plugin_parameter_names=["Gain", "Tune"])
    old = factory.add_plugin(song.tracks[1], "OldSynth", params=[], presets=(),
                             plugin_parameter_names=["Osc Mix", "Filter Cutoff", "Drive"])
    old._latency_in_samples = 441
    return {"serum": serum, "diva": diva, "au": au, "old": old}


# --------------------------------------------------------------------------- handlers

def test_format_detection():
    assert plugins_mod._folder_format("VST3") == "VST3"
    assert plugins_mod._folder_format("VST Plug-Ins") == "VST2"
    assert plugins_mod._folder_format("Audio Units") == "AU"
    assert plugins_mod._folder_format("Auburn Sounds") is None
    assert plugins_mod._uri_format("query:Plugins#VST3:Serum") == "VST3"


def test_list_reports_every_plugin(bridge, plugins):
    result = run(bridge, "plugins.list")
    by_name = {p["name"]: p for p in result["plugins"]}
    assert result["count"] == 4
    assert by_name["Serum"]["format"] == "VST3" and by_name["Serum"]["format_source"] == "browser"
    assert by_name["Diva"]["format"] == "VST2/VST3"
    assert by_name["OldSynth"]["format"] == "VST2"
    assert by_name["AUSampler"]["format"] == "AU"
    assert by_name["AUSampler"]["path"] == "song.tracks[0].devices[1].chains[0].devices[0]"
    assert by_name["Serum"]["parameter_count"] == 3
    assert by_name["Serum"]["preset_count"] == 4
    assert by_name["OldSynth"]["latency_ms"] == 10.0
    assert set(result["needs_configure"]) == {by_name["OldSynth"]["path"],
                                              by_name["AUSampler"]["path"]}
    assert result["formats"] == {"VST3": 1, "VST2/VST3": 1, "VST2": 1, "AU": 1}
    assert result["installed"]["count"] >= 3 and result["installed"]["complete"] is True
    assert result["installed"]["formats"]["VST3"] >= 2
    assert "hint" not in result["installed"]
    flat = run(bridge, "plugins.list", include_nested=False, resolve_format=False)
    assert flat["count"] == 3
    assert {p["format"] for p in flat["plugins"]} == {"VST2/VST3"}
    assert "installed" not in flat


def test_list_explains_an_empty_plugin_browser(bridge, browser, monkeypatch):
    """Live 12.4.5 on the test Mac listed 0 plug-ins although AU/VST2 plug-ins were on disk
    (plug-in use off in Live's Settings) — plugins.list says how to fix it."""
    monkeypatch.setattr(browser, "_plugins", Live._model.BrowserItem("Plug-Ins",
                                                                     is_folder=True))
    result = run(bridge, "plugins.list")
    assert result["count"] == 0 and result["installed"]["count"] == 0
    assert "Settings -> Plug-Ins" in result["installed"]["hint"]


def test_get_plugin(bridge, plugins):
    info = run(bridge, "plugins.get", track="Vocals", device="Serum")
    assert info["parameter_names"] == ["Cutoff", "Resonance", "Osc A Level"]
    assert info["selected_preset"] == "Init"
    assert info["editor_open"] is False
    assert "configure_hint" not in info
    empty = run(bridge, "plugins.get", track="Vocals", device="OldSynth", max_names=0)
    assert "Configure" in empty["configure_hint"]
    capped = run(bridge, "plugins.get", track="Vocals", device="Serum", max_names=1)
    assert capped["parameter_names_truncated"] == 2
    error = fail(bridge, "plugins.get", track="Vocals", device="Reverb")
    assert error["type"] == "bad_args" and "not a third-party plug-in" in error["message"]
    assert fail(bridge, "plugins.get", track="Vocals", device="Serum",
                max_names=-1)["type"] == "bad_args"


def test_presets_paging_and_filter(bridge, plugins):
    result = run(bridge, "plugins.presets", track="Vocals", device="Serum", limit=2)
    assert [p["name"] for p in result["presets"]] == ["Init", "Bass - Reese"]
    assert result["next_offset"] == 2 and result["total"] == 4
    filtered = run(bridge, "plugins.presets", track="Vocals", device="Serum", filter="lead")
    assert filtered["presets"] == [{"index": 2, "name": "Lead - Saw"}]


def test_set_preset_and_editor(bridge, plugins):
    result = run(bridge, "plugins.set", track="Vocals", device="Serum", preset="pad")
    assert result["selected_preset"] == "Pad - Air"
    assert plugins["serum"].selected_preset_index == 3
    result = run(bridge, "plugins.set", track="Vocals", device="Serum", preset=1,
                 editor_open=True)
    assert result["selected_preset_index"] == 1 and result["editor_open"] is True
    assert plugins["serum"].is_editor_open is True
    assert fail(bridge, "plugins.set", track="Vocals", device="Serum",
                preset="e")["type"] == "bad_args"          # ambiguous
    assert fail(bridge, "plugins.set", track="Vocals", device="Serum",
                preset="Nope")["type"] == "not_found"
    assert fail(bridge, "plugins.set", track="Vocals", device="Serum",
                preset=9)["type"] == "not_found"
    assert fail(bridge, "plugins.set", track="Vocals", device="Serum")["type"] == "bad_args"
    # Live 12.4.5: a plug-in without programs reports ["Default"] (the stub's default too)
    assert run(bridge, "plugins.set", track="A-Reverb", device="Diva",
               preset=0)["selected_preset"] == "Default"
    plugins["diva"]._presets = ()           # an (older) plug-in with no list at all
    assert fail(bridge, "plugins.set", track="A-Reverb", device="Diva",
                preset=0)["type"] == "invalid_state"


def test_parameters_and_configure_hint(bridge, plugins):
    result = run(bridge, "plugins.parameters", track="Vocals", device="Serum", filter="cut")
    assert result["parameters"][0]["name"] == "Cutoff"
    assert "path" not in result["parameters"][0]
    assert "configure_hint" not in result
    none = run(bridge, "plugins.parameters", track="Vocals", device="OldSynth")
    assert none["total"] == 1 and "configure_hint" in none
    missing = run(bridge, "plugins.parameters", track="Vocals", device="Serum", filter="wobble")
    assert missing["matched"] == 0 and "configure_hint" in missing
    full = run(bridge, "plugins.parameters", track="Vocals", device="Serum", detail="full",
               offset=1, limit=1)
    assert full["parameters"][0]["path"] == "song.tracks[1].devices[1].parameters[1]"
    assert full["next_offset"] == 2
    # values are set through the generic device commands
    run(bridge, "devices.set_parameter", track="Vocals", device="Serum", parameter="Cutoff",
        value=0.9)
    assert plugins["serum"].parameters[1].value == 0.9


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


def test_plugin_tools_are_registered(fake_app):
    _fake, app = fake_app
    names = {tool.name for tool in asyncio.run(app.list_tools())}
    assert all(name in names for name in PLUGIN_TOOLS)


def test_plugin_tools_end_to_end(live_app, plugins):
    listing = call_tool(live_app, "live_plugin_list")
    assert listing["count"] == 4
    info = call_tool(live_app, "live_plugin_get", {"track": "Vocals", "device": "Serum"})
    assert info["format"] == "VST3"
    presets = call_tool(live_app, "live_plugin_presets",
                        {"track": "Vocals", "device": "Serum", "filter": "bass"})
    assert presets["presets"][0]["index"] == 1
    chosen = call_tool(live_app, "live_plugin_set",
                       {"track": "Vocals", "device": "Serum", "preset": "Lead - Saw"})
    assert chosen["selected_preset_index"] == 2
    params = call_tool(live_app, "live_plugin_parameters",
                       {"track": "Vocals", "device": "OldSynth"})
    assert "configure_hint" in params
    error = call_tool(live_app, "live_plugin_get", {"track": "Vocals", "device": "Reverb"})
    assert error["type"] == "bad_args"


@pytest.mark.parametrize("tool,args", [
    ("live_plugin_get", {"max_names": 5000}),
    ("live_plugin_get", {"device": ""}),
    ("live_plugin_presets", {"limit": 0}),
    ("live_plugin_set", {}),
    ("live_plugin_set", {"preset": " "}),
    ("live_plugin_parameters", {"detail": "everything"}),
    ("live_plugin_parameters", {"offset": -2}),
])
def test_plugin_tools_validate_locally(fake_app, tool, args):
    fake, app = fake_app
    assert call_tool(app, tool, args)["type"] == "bad_args"
    assert fake.requests == []


def test_plugin_list_uses_a_longer_timeout_and_forwards_flags(fake_app):
    fake, app = fake_app
    fake.set_result("plugins.list", {"count": 0, "plugins": []})
    call_tool(app, "live_plugin_list", {"resolve_format": False})
    request = fake.requests[-1]
    assert request["cmd"] == "plugins.list"
    assert request["args"] == {"include_nested": True, "resolve_format": False}
    assert request.get("timeout", 30) >= 30
