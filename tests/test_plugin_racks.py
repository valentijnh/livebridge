"""plugin_racks: generated rack presets that expose any VST3 parameter (Serum 2 first).

Covers the file side (``LiveBridge/plugin_racks_lib.py``: rack XML, .vstpreset / Xfer
containers, moduleinfo / Info.plist / Live plug-in database scanning, preset listing, the
ParameterId prober, the shipped Serum 2 map), the handlers through the dispatcher against
``tests/live_stub_ext/plugin_racks.py`` (Live's browser indexing + rack loading as measured on
Live 12.4.5) and the MCP tools (fake bridge + the real bridge over TCP).
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import os
import plistlib
import sqlite3
import struct
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import pytest

import Live

from live_stub import factory
from live_stub_ext import plugin_racks as racks_ext
from live_stub_ext import plugins_live

_TESTS = Path(__file__).resolve().parent
for _path in (str(_TESTS.parent / "mcp_server"), str(_TESTS)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from fake_bridge import FakeBridge  # noqa: E402
from livebridge_mcp.client import BridgeClient  # noqa: E402
from livebridge_mcp.server import create_app  # noqa: E402

from LiveBridge import plugin_racks_lib as lib  # noqa: E402
from LiveBridge.handlers import plugin_racks as handler  # noqa: E402

EXT = racks_ext.install(Live)
SERUM_CID = "56534558667350736572756D20320000"
SERUM_FX_CID = "56534558667351736572756D20322066"
TINY_CID = "ABCDEF0112345678ABCDEF0112345678"
SHIPPED = lib.load_map(os.path.join(lib.shipped_maps_dir(), "serum2_vst3.json"))


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
    text = "\n".join(blocks)
    try:
        return json.loads(text)
    except ValueError:
        return text


def tiny_spec():
    names = ["Tiny P%d" % i for i in range(150)] + ["Tiny B%d" % i for i in range(10)] + \
        ["CC%d Chan 1" % cc for cc in range(4)]
    ids = dict((i, "Tiny P%d" % i) for i in range(150))
    ids.update((1000000 + i, "Tiny B%d" % i) for i in range(10))
    return {"name": "Tiny Synth", "names": names, "ids": ids}


# --------------------------------------------------------------------------- fixtures

@pytest.fixture
def env(tmp_path, monkeypatch, browser):
    """A private User Library, fake VST3/AU bundles on disk and Live's rack loading."""
    library = tmp_path / "User Library"
    library.mkdir()
    monkeypatch.setenv("LIVEBRIDGE_USER_LIBRARY", str(library))
    vst3 = tmp_path / "VST3"
    racks_ext.fake_moduleinfo(str(vst3), "Serum 2", SERUM_CID, vendor="Xfer Records",
                              categories=("Instrument", "Synth"))
    racks_ext.fake_moduleinfo(str(vst3), "Serum 2 FX", SERUM_FX_CID, vendor="Xfer Records",
                              categories=("Fx",))
    racks_ext.fake_moduleinfo(str(vst3), "Tiny Synth", TINY_CID)
    au = tmp_path / "Components" / "Serum2.component" / "Contents"
    au.mkdir(parents=True)
    with open(au / "Info.plist", "wb") as handle:
        plistlib.dump({"CFBundleShortVersionString": "2.1.5", "AudioComponents": [
            {"name": "Xfer Records: Serum 2", "type": "aumu", "subtype": "Xf2X",
             "manufacturer": "XFER"}]}, handle)
    found = lib.scan_vst3([str(vst3)]) + lib.scan_au([str(tmp_path / "Components")])
    monkeypatch.setattr(lib, "installed_plugins", lambda env=None, refresh=False: found)
    handler._JOBS.clear()
    fx_spec = dict(racks_ext.serum_spec(SHIPPED), name="Serum 2 FX")
    controller = EXT.setup(browser, str(library), {
        SERUM_CID: racks_ext.serum_spec(SHIPPED), SERUM_FX_CID: fx_spec,
        TINY_CID: tiny_spec()})
    controller.library_path = library
    controller.tmp = tmp_path
    yield controller
    handler._JOBS.clear()


def expose(bridge, **args):
    """Call plugin_racks.expose until Live has indexed the rack file."""
    for _ in range(4):
        result = run(bridge, "plugin_racks.expose", **args)
        if result["status"] != "indexing":
            return result
    raise AssertionError("never indexed")


# --------------------------------------------------------------------------- the library

def test_rack_xml_holds_the_parameter_ids_and_the_state(tmp_path):
    identity = {"name": "Serum 2", "format": "VST3", "class_id": SERUM_CID,
                "kind": "instrument"}
    xml = lib.rack_xml(identity, [{"id": 1000001}, {"id": 2000003, "macro": 0},
                                  {"id": 7000000}],
                       macro_names={0: "Filter 1 Freq"}, processor=b"\x01\xab",
                       controller=b"\xff")
    root = ElementTree.fromstring(xml)
    group = root.find("GroupDevicePreset/Device/InstrumentGroupDevice")
    assert group is not None and group.find("MacroDisplayNames.0").get("Value") == "Filter 1 Freq"
    preset = root.find(".//DevicePresets/Vst3Preset")
    assert [int(preset.find("Uid/Fields.%d" % i).get("Value")) for i in range(4)] == \
        [1448297816, 1718833267, 1701999981, 540147712]
    settings = preset.findall("ParameterSettings/PluginParameterSettings")
    assert [int(s.find("ParameterId").get("Value")) for s in settings] == \
        [1000001, 2000003, 7000000]
    assert [int(s.find("Index").get("Value")) for s in settings] == [0, 1, 2]
    assert [s.find("MacroControlIndex").get("Value") for s in settings] == ["-1", "0", "-1"]
    # a wired macro needs the MidiControllerRange slot (an empty one crashed Live)
    slot = settings[1].find("MidiControllerRange/MidiControllerRange")
    assert (slot.get("Id"), slot.find("Min").get("Value"), slot.find("Max").get("Value")) == \
        ("0", str(lib.MACRO_RANGE[0]), str(lib.MACRO_RANGE[1]))
    assert list(settings[0].find("MidiControllerRange")) == []
    assert "".join(preset.find("ProcessorState").text.split()) == "01AB"
    assert "".join(preset.find("ControllerState").text.split()) == "FF"
    assert root.find(".//InstrumentBranchPreset/ZoneSettings") is not None
    assert preset.find("DeviceType").get("Value") == "1"


def test_effects_get_an_audio_effect_rack_and_limits_are_enforced():
    identity = {"name": "Serum 2 FX", "format": "VST3", "class_id": SERUM_FX_CID,
                "kind": "audio_effect"}
    root = ElementTree.fromstring(lib.rack_xml(identity, [{"id": 0}]))
    assert root.find("GroupDevicePreset/Device/AudioEffectGroupDevice") is not None
    assert root.find(".//AudioEffectBranchPreset") is not None
    assert root.find(".//ZoneSettings") is None
    assert root.find(".//Vst3Preset/DeviceType").get("Value") == "2"
    with pytest.raises(ValueError, match="at most 128"):      # 256 crashed Live 12.4.5
        lib.rack_xml(identity, [{"id": i} for i in range(129)])
    with pytest.raises(ValueError, match="macro index"):
        lib.rack_xml(identity, [{"id": 0, "macro": 16}])
    with pytest.raises(ValueError, match="instruments and audio effects"):
        lib.rack_xml(dict(identity, kind="midi_effect"), [{"id": 0}])


def test_au_rack_xml_uses_the_component_codes():
    identity = {"name": "Serum 2", "format": "AU", "kind": "instrument",
                "au": {"type": "aumu", "subtype": "Xf2X", "manufacturer": "XFER"}}
    preset = ElementTree.fromstring(lib.rack_xml(identity, [])).find(".//AuPreset")
    assert int(preset.find("Type").get("Value")) == lib.fourcc("aumu") == 1635085685
    assert int(preset.find("SubType").get("Value")) == struct.unpack(">I", b"Xf2X")[0]
    assert int(preset.find("Manufacturer").get("Value")) == struct.unpack(">I", b"XFER")[0]


def test_write_rack_is_gzip_atomic_and_idempotent(tmp_path):
    path = str(tmp_path / "Racks" / "LB x.adg")
    assert lib.write_rack(path, "<a>é</a>") is True
    with open(path, "rb") as handle:
        assert handle.read(2) == b"\x1f\x8b"
    assert lib.read_rack(path) == "<a>é</a>"
    assert lib.write_rack(path, "<a>é</a>") is False
    assert lib.write_rack(path, "<b/>") is True and lib.read_rack(path) == "<b/>"
    assert not os.path.exists(path + ".tmp")


def test_class_id_fields_are_signed_big_endian_ints():
    assert lib.class_id_fields(SERUM_CID) == [1448297816, 1718833267, 1701999981, 540147712]
    assert lib.class_id_fields("ABCDEF01" + "0" * 24)[0] == struct.unpack(">i", b"\xab\xcd\xef\x01")[0] < 0
    with pytest.raises(ValueError):
        lib.class_id_fields("1234")


def test_vstpreset_round_trip_and_state_loading(tmp_path):
    data = lib.build_vstpreset(SERUM_CID, {"Comp": b"proc", "Cont": b"ctrl", "Info": b"<x/>"})
    parsed = lib.parse_vstpreset(data)
    assert parsed["class_id"] == SERUM_CID and parsed["chunks"]["Comp"] == b"proc"
    path = tmp_path / "Warm Pad.vstpreset"
    path.write_bytes(data)
    serum = {"name": "Serum 2", "format": "VST3", "class_id": SERUM_CID}
    state = lib.load_state(str(path), serum)
    assert (state["processor"], state["controller"], state["kind"], state["preset_name"]) == \
        (b"proc", b"ctrl", "vstpreset", "Warm Pad")
    with pytest.raises(ValueError, match="another plug-in"):
        lib.load_state(str(path), dict(serum, class_id=TINY_CID))
    serum_preset = tmp_path / "Bass.SerumPreset"
    serum_preset.write_bytes(b"XferJson\x00" + b"\x00" * 8)
    with pytest.raises(ValueError, match="SerumPreset"):
        lib.load_state(str(serum_preset), serum)
    renamed = tmp_path / "Bass.vstpreset"          # sniffed by content, not by extension
    renamed.write_bytes(b"XferJson\x00" + b"\x00" * 8)
    with pytest.raises(ValueError, match="SerumPreset"):
        lib.load_state(str(renamed), serum)
    # a Live preset holding the plug-in gives its state back
    live = tmp_path / "My Serum.adv"
    live.write_bytes(gzip.compress(lib.rack_xml(dict(serum, kind="instrument"), [],
                                                processor=b"\x10\x20").encode("utf-8")))
    assert lib.load_state(str(live), serum)["processor"] == b"\x10\x20"
    with pytest.raises(ValueError, match="does not contain"):
        lib.load_state(str(live), dict(serum, class_id=TINY_CID))


def test_real_vstpreset_chunks_are_lives_processor_and_controller_state():
    """The Live-saved rack's <ProcessorState>/<ControllerState> are the Comp/Cont chunks of a
    .vstpreset (verified byte for byte on the real files) — the shipped template holds them."""
    template = SHIPPED["template_state"]
    processor = bytes.fromhex(template["processor"])
    header, raw_size, fmt, frame = lib.parse_xfer(processor)
    assert header["component"] == "processor" and header["product"] == "Serum2"
    assert header["hash"] == hashlib.md5(frame).hexdigest() and fmt == 2
    assert lib.build_xfer(header, raw_size, fmt, frame) == processor    # "version":11.0 kept
    data = lib.build_vstpreset(SERUM_CID, {"Comp": processor,
                                           "Cont": bytes.fromhex(template["controller"])})
    assert lib.parse_vstpreset(data)["chunks"]["Comp"] == processor


def test_moduleinfo_au_plist_and_live_database_scans(tmp_path):
    bundle = racks_ext.fake_moduleinfo(str(tmp_path / "VST3"), "Serum 2", SERUM_CID,
                                       vendor="Xfer Records")
    found = lib.read_moduleinfo(bundle)
    assert found == [{"name": "Serum 2", "format": "VST3", "vendor": "Xfer Records",
                      "version": "1.0.0", "class_id": SERUM_CID, "kind": "instrument",
                      "path": bundle, "source": "moduleinfo.json"}]
    assert lib.scan_vst3([str(tmp_path / "VST3")]) == found
    # Live's plug-in database (Live's Python has no _sqlite3: parsed from raw bytes)
    db = tmp_path / "Live-plugins-1.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE plugins (plugin_id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "module_id INTEGER DEFAULT 0, dev_identifier TEXT, name TEXT, vendor TEXT, "
                "version TEXT, sdk_version TEXT, flags INTEGER DEFAULT 0, "
                "scanstate INTEGER DEFAULT 0, subcategories TEXT, enabled INTEGER DEFAULT 0)")
    rows = [(4, "device:vst3:instr:56534558-6673-5073-6572-756d20320000", "Serum 2",
             "Xfer Records", "2.1.5", "VST 3.7.12", 1, 1, "Instrument|Synth", 1),
            (1, "device:vst:instr:1299211074?n=MPC%20Beats", "MPC Beats", "Akai Professional",
             "134147", "2400", 0, 1, "", 1),
            (5, "device:vst3:audiofx:56534558-6673-5173-6572-756d20322066", "Serum 2 FX",
             "Xfer Records", "2.1.5", "VST 3.7.12", 1, 1, "Fx", 1)]
    con.executemany("INSERT INTO plugins (module_id, dev_identifier, name, vendor, version, "
                    "sdk_version, flags, scanstate, subcategories, enabled) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    entries = lib.scan_live_database([str(db)])
    by_name = dict((e["name"], e) for e in entries)
    assert by_name["Serum 2"]["class_id"] == SERUM_CID and by_name["Serum 2"]["kind"] == "instrument"
    assert by_name["Serum 2 FX"]["class_id"] == SERUM_FX_CID
    assert by_name["Serum 2 FX"]["kind"] == "audio_effect"
    assert (by_name["MPC Beats"]["format"], by_name["MPC Beats"]["uid"]) == ("VST2", 1299211074)
    # AU components from Info.plist
    comp = tmp_path / "AU" / "Serum2.component" / "Contents"
    comp.mkdir(parents=True)
    with open(comp / "Info.plist", "wb") as handle:
        plistlib.dump({"AudioComponents": [
            {"name": "Xfer Records: Serum 2 FX", "type": "aumf", "subtype": "Xf2Y",
             "manufacturer": "XFER"}]}, handle)
    au = lib.scan_au([str(tmp_path / "AU")])
    assert au[0]["name"] == "Serum 2 FX" and au[0]["vendor"] == "Xfer Records"
    assert au[0]["kind"] == "audio_effect" and au[0]["au"]["subtype"] == "Xf2Y"


def test_lenient_json_keeps_urls_and_drops_comments():
    text = '{\n // c\n "URL": "https://x.com/a//b", /* block */ "L": [1, 2,],\n}'
    assert lib.loads_lenient_json(text) == {"URL": "https://x.com/a//b", "L": [1, 2]}


def test_find_plugin_prefers_vst3_and_explains_misses():
    plugins = [{"name": "Serum 2", "format": "AU", "vendor": "Xfer Records"},
               {"name": "Serum 2", "format": "VST3", "vendor": "Xfer Records"},
               {"name": "Serum 2 FX", "format": "VST3", "vendor": "Xfer Records"},
               {"name": "Tiny Synth", "format": "VST3", "vendor": "Test"}]
    assert lib.find_plugin("Serum 2", plugins)["format"] == "VST3"
    assert lib.find_plugin("serum2", plugins, fmt="AU")["format"] == "AU"
    assert lib.find_plugin("Xfer Records/Serum 2 FX", plugins)["name"] == "Serum 2 FX"
    assert lib.find_plugin("tiny", plugins)["name"] == "Tiny Synth"
    with pytest.raises(LookupError, match="ambiguous"):
        lib.find_plugin("seru", plugins)
    with pytest.raises(LookupError, match="no installed plug-in"):
        lib.find_plugin("Massive", plugins)


def test_user_library_resolution(tmp_path, monkeypatch):
    monkeypatch.delenv("LIVEBRIDGE_USER_LIBRARY", raising=False)
    script = tmp_path / "My Library" / "Remote Scripts" / "LiveBridge"
    script.mkdir(parents=True)
    assert lib.user_library(str(script)) == (str(tmp_path / "My Library"), "script folder")
    prefs = tmp_path / "Prefs" / "Live 12.4.5"
    prefs.mkdir(parents=True)
    (tmp_path / "Music" / "User Library").mkdir(parents=True)
    (prefs / "Library.cfg").write_text(
        '<?xml version="1.0" encoding="UTF-8"?><Ableton><ContentLibrary><UserLibrary>'
        '<LibraryProject Id="0"><ProjectName Value="User Library" /><ProjectPath Value="%s" />'
        '</LibraryProject></UserLibrary></ContentLibrary></Ableton>' % (tmp_path / "Music"),
        encoding="utf-8")
    monkeypatch.setattr(lib, "live_preference_dirs", lambda env=None: [str(prefs)])
    assert lib.user_library(str(tmp_path / "elsewhere" / "LiveBridge")) == \
        (str(tmp_path / "Music" / "User Library"), "Library.cfg")
    assert lib.user_library(env={"LIVEBRIDGE_USER_LIBRARY": "/x"}) == \
        ("/x", "LIVEBRIDGE_USER_LIBRARY")


def test_live_preference_dirs_are_sorted_newest_first(tmp_path, monkeypatch):
    base = tmp_path / "Library" / "Preferences" / "Ableton"
    for name in ("Live 12.0.20", "Live 12.4.5", "Live 11.3.1"):
        (base / name).mkdir(parents=True)
    monkeypatch.setattr(lib, "is_windows", lambda: False)
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path) if p == "~" else p)
    assert [os.path.basename(d) for d in lib.live_preference_dirs()] == \
        ["Live 12.4.5", "Live 12.0.20", "Live 11.3.1"]


def _probe_all(prober, id_names):
    batches = []
    while True:
        ids = prober.next_ids()
        if not ids:
            return batches
        assert len(ids) <= lib.MAX_PARAMETERS and len(set(ids)) == len(ids)
        batches.append(ids)
        prober.record(ids, [id_names.get(i, "Parameter #%d" % n) for n, i in
                            enumerate(ids, 1)])


def test_prober_finds_every_serum2_parameter():
    """Serum 2's real id layout (block * 1e6 + instance * 1000 + index; Noise/Sub use the
    oscillators' sparse indices) — the same run on real Live took 27 loads."""
    id_names = dict((pid, name) for name, pid in SHIPPED["parameters"])
    prober = lib.Prober(gap=16)
    batches = _probe_all(prober, id_names)
    assert sorted(prober.found) == sorted(id_names)
    assert lib.missing_names(plugins_live.serum2_names(),
                             [n for n, _i in prober.parameters()]) == []
    assert len(batches) <= 40 and prober.done()


def test_prober_handles_sequential_and_unprobeable_plugins():
    names = dict((i, "P%d" % i) for i in range(300) if i not in (57, 58, 59))
    prober = lib.Prober(gap=16)
    _probe_all(prober, names)
    assert sorted(prober.found) == sorted(names)
    hashed = lib.Prober(gap=16)          # JUCE-style hashed ids: nothing to find, but it ends
    assert _probe_all(hashed, {123456789: "Cutoff"}) and hashed.found == {}


def test_missing_names_skips_midi_proxies_and_trailing_duplicates():
    expected = ["Main Vol", "Pitch Bend", "Mod Wheel", "Cutoff", "CC0 Chan 1",
                "Pitch Bend Chan 1", "Aftertouch Chan 16", "Mod Wheel", "Pitch Bend"]
    assert lib.missing_names(expected, ["Main Vol", "Pitch Bend", "Mod Wheel"]) == ["Cutoff"]


def test_preset_listing(tmp_path):
    serum = {"name": "Serum 2", "vendor": "Xfer Records", "format": "VST3",
             "class_id": SERUM_CID, "kind": "instrument"}
    factory_dir = tmp_path / "Serum 2 Presets" / "Factory" / "Bass"
    factory_dir.mkdir(parents=True)
    (factory_dir / "BA - Growl.SerumPreset").write_bytes(b"XferJson\x00")
    library = tmp_path / "User Library"
    (library / "Presets").mkdir(parents=True)
    (library / "Serum 2.vstpreset").write_bytes(lib.build_vstpreset(SERUM_CID, {"Comp": b"x"}))
    (library / "Other.vstpreset").write_bytes(lib.build_vstpreset(TINY_CID, {"Comp": b"x"}))
    (library / "Presets" / "Pad.adv").write_bytes(
        gzip.compress(lib.rack_xml(serum, []).encode("utf-8")))
    (library / "Presets" / "Operator.adv").write_bytes(gzip.compress(b"<Ableton/>"))
    (library / "LiveBridge" / "Racks").mkdir(parents=True)
    (library / "LiveBridge" / "Racks" / "LB Serum 2.adg").write_bytes(
        gzip.compress(lib.rack_xml(serum, []).encode("utf-8")))
    folders = lib.preset_folders(serum, str(library),
                                 {"macos": ["%s/Serum 2 Presets" % tmp_path],
                                  "windows": ["%s/Serum 2 Presets" % tmp_path]})
    assert folders[0] == "%s/Serum 2 Presets" % tmp_path and folders[-1] == str(library)
    entries, truncated = lib.list_presets(folders, serum)
    kinds = sorted((e["name"], e["kind"], e["embeddable"]) for e in entries)
    assert kinds == [("BA - Growl", "SerumPreset", False), ("Pad", "live_preset", True),
                     ("Serum 2", "vstpreset", True)] and not truncated
    assert [e.get("category") for e in entries if e["name"] == "BA - Growl"] == ["Factory/Bass"]
    tiny = dict(serum, name="Tiny Synth", class_id=TINY_CID)
    assert [e["name"] for e in lib.list_presets(folders, tiny)[0]] == ["Other"]


def test_preset_folders_expand_windows_variables(monkeypatch):
    monkeypatch.setattr(lib, "is_windows", lambda: True)
    folders = lib.preset_folders({"name": "Serum 2", "vendor": "Xfer Records"}, None,
                                 {"windows": [r"%PUBLIC%\Documents\Xfer\Serum 2 Presets"]},
                                 env={"PUBLIC": r"C:\Users\Public",
                                      "USERPROFILE": r"C:\Users\me"})
    assert folders[0] == r"C:\Users\Public\Documents\Xfer\Serum 2 Presets"
    assert any(f.endswith(os.path.join("VST3 Presets", "Xfer Records", "Serum 2"))
               for f in folders)


def test_shipped_serum2_map_is_complete_and_consistent():
    params = SHIPPED["parameters"]
    names = [n for n, _i in params]
    ids = [i for _n, i in params]
    assert SHIPPED["class_id"] == SERUM_CID and SHIPPED["format"] == "VST3"
    assert len(params) == 541 == len(set(names)) == len(set(ids))
    assert lib.missing_names(plugins_live.serum2_names(), names) == []
    lookup = dict(params)
    assert (lookup["A Enable"], lookup["A WT Pos"], lookup["Filter 1 Freq"],
            lookup["Macro 1"], lookup["B Enable"], lookup["Env 2 Attack"]) == \
        (1000000, 1000039, 2000003, 7000000, 1001000, 3001000)
    for group, members in SHIPPED["groups"].items():
        assert len(members) <= lib.MAX_PARAMETERS and set(members) <= set(names), group
    design = SHIPPED["groups"]["sound_design"]
    assert len(design) == 120 and len(set(design)) == 120
    for wanted in ("A Enable", "C Uni Blend", "Sub Level", "Noise Pitch", "Filter 2 Wet",
                   "Env 4 Release", "LFO 4 Rate", "Macro 8", "Main Vol", "Porta Time",
                   "FX Main Param 1"):
        assert wanted in design
    assert set(SHIPPED["preset_folders"]) == {"macos", "windows"}
    fx = lib.load_map(os.path.join(lib.shipped_maps_dir(), "serum2fx_vst3.json"))
    assert fx["class_id"] == SERUM_FX_CID and fx["parameters"] == params      # same ids
    assert "template_state" not in fx and fx["groups"] == SHIPPED["groups"]


# --------------------------------------------------------------------------- handlers

def test_map_returns_the_shipped_serum2_map_at_once(bridge, env):
    result = run(bridge, "plugin_racks.map", plugin="Serum 2", filter="filter 1", limit=5)
    assert (result["status"], result["source"], result["count"]) == ("done", "shipped", 541)
    assert result["missing"] == [] and result["groups"]["sound_design"] == 120
    assert [p["name"] for p in result["parameters"]][:3] == \
        ["Filter 1 Level", "Filter 1 On", "Filter 1 Type"]
    assert result["matched"] == 13 and result["next_offset"] == 5     # + "Filter 1>BUS1/2"
    assert env.loads == []            # nothing loaded, nothing written
    assert not (env.library_path / "LiveBridge").exists()


def test_map_probes_an_unknown_plugin_and_caches_it(bridge, env, song):
    tracks_before = len(song.tracks)
    first = run(bridge, "plugin_racks.map", plugin="Tiny Synth")
    assert first["status"] == "indexing"
    assert (env.library_path / "LiveBridge" / "Racks" / "LB_probe_tinysynth_vst3.adg").exists()
    for _ in range(10):
        result = run(bridge, "plugin_racks.map", plugin="Tiny Synth", max_seconds=30)
        if result["status"] == "done":
            break
    assert (result["status"], result["source"], result["count"]) == ("done", "probed", 160)
    assert result["missing"] == []                        # the CC proxies are skipped
    assert len(song.tracks) == tracks_before              # LB_MAP deleted again
    assert all(len(load["settings"]) <= 128 for load in env.loads)
    cached = lib.load_map(str(env.library_path / "LiveBridge" / "Maps" / "tinysynth_vst3.json"))
    assert dict(cached["parameters"])["Tiny B3"] == 1000003
    assert cached["parameters"][0] == ["Tiny P0", 0]        # get_parameter_names() order
    again = run(bridge, "plugin_racks.map", plugin="Tiny Synth", filter="Tiny B", limit=2)
    assert (again["source"], again["matched"], again["returned"]) == ("cache", 10, 2)


def test_map_can_be_cancelled_and_refuses_audio_units(bridge, env, song, monkeypatch):
    clock = [1000.0]

    def slow_time():          # every clock read costs a second: one batch per call
        clock[0] += 1.0
        return clock[0]
    monkeypatch.setattr(handler, "time", type("T", (), {"time": staticmethod(slow_time),
                                                        "strftime": staticmethod(
                                                            lambda *a: "now")}))
    run(bridge, "plugin_racks.map", plugin="Tiny Synth", refresh=True)        # indexing
    running = run(bridge, "plugin_racks.map", plugin="Tiny Synth", max_seconds=0.5)
    assert running["status"] == "running" and running["loads"] == 1
    assert any(t.name == "LB_MAP" for t in song.tracks)
    assert run(bridge, "plugin_racks.map", plugin="Tiny Synth", cancel=True)["status"] == \
        "cancelled"
    assert not any(t.name == "LB_MAP" for t in song.tracks) and handler._JOBS == {}
    error = fail(bridge, "plugin_racks.map", plugin="Serum 2", plugin_format="AU", refresh=True)
    assert error["type"] == "unsupported" and "VST3" in error["message"]
    assert fail(bridge, "plugin_racks.map")["type"] == "bad_args"
    assert fail(bridge, "plugin_racks.map", plugin="Massive")["type"] == "not_found"


def test_expose_sound_design_on_a_new_track(bridge, env, song):
    result = expose(bridge, plugin="Serum 2", new_track=True, track_name="LB_SERUM_DESIGN",
                    parameters=["sound_design", "LFO 5 Rate"])
    assert result["status"] == "done" and result["track"] == "LB_SERUM_DESIGN"
    assert result["exposed_count"] == 121 and "missing" not in result
    assert result["state"] == "template" and "Init" in result["state_note"]
    track = song.tracks[-1]
    rack = track.devices[0]
    assert rack.class_name == "InstrumentGroupDevice" and rack.name == "Serum 2 Rack"
    assert result["device"] == "song.tracks[%d].devices[0].chains[0].devices[0]" % (
        len(song.tracks) - 1)
    plugin = rack.chains[0].devices[0]
    assert plugin._lb_state == SHIPPED["template_state"]["processor"]
    names = [p.name for p in plugin.parameters]
    assert names[:4] == ["Device On", "Main Vol", "Main Tuning", "Transpose"]
    assert names[-1] == "LFO 5 Rate"
    load = env.loads[-1]
    assert [s["id"] for s in load["settings"]][:2] == [0, 1]
    # the exposed parameters work with the normal device and automation commands
    set_result = run(bridge, "devices.set_parameter", track="LB_SERUM_DESIGN",
                     device="Serum 2", parameter="Filter 1 Freq", value="800 Hz")
    assert set_result["display"] == "800 Hz"
    got = run(bridge, "devices.get_parameter", track="LB_SERUM_DESIGN", device=result["device"],
              parameter="filter 1 cutoff")
    assert got["name"] == "Filter 1 Freq" and got["display"] == "800 Hz"
    factory.add_clip(track, slot=0, length=4.0, name="LB pattern",
                     notes=[(48, 0.0, 1.0, 100), (51, 1.0, 1.0, 100)])
    run(bridge, "automation.write", track="LB_SERUM_DESIGN", slot=0, device=result["device"],
        parameter="Filter 1 Freq", points=[{"time": 0, "value": 0.2}, {"time": 2, "value": 0.8}])
    envelope = run(bridge, "automation.get", track="LB_SERUM_DESIGN", slot=0,
                   device=result["device"], parameter="Filter 1 Freq", points=4)
    assert envelope["has_envelope"] is True
    assert envelope["values"][0] == pytest.approx(0.2)
    assert envelope["parameter"]["name"] == "Filter 1 Freq"


def test_expose_replaces_the_plugin_at_its_position(bridge, env, song):
    track = factory.add_track(song, "LB_SYNTH", "midi")
    factory.add_device(track, "Arpeggiator", kind="midi_effect")
    plugins_live.install(Live).add_real_plugin(track, "Serum 2", plugins_live.serum2_names())
    factory.add_device(track, "Reverb", kind="audio_effect")
    result = expose(bridge, track="LB_SYNTH", parameters=["filter 1", "Macro 1"],
                    editor_open=False)
    assert result["replaced"] == "Serum 2" and result["exposed_count"] == 12
    assert result["editor_open"] is False
    assert fail(bridge, "plugin_racks.expose", track="LB_SYNTH", editor_open="yes")["type"] == \
        "bad_args"
    assert [d.name for d in track.devices] == ["Arpeggiator", "Serum 2 Rack", "Reverb"]
    # again: the LiveBridge rack around the plug-in is replaced as a whole, sound restarts
    second = expose(bridge, track="LB_SYNTH", parameters=["env"], name="Lead")
    assert second["replaced"] == "Serum 2 Rack" and second["rack_name"] == "Lead"
    assert [d.name for d in track.devices] == ["Arpeggiator", "Lead", "Reverb"]
    assert "cannot read the previous sound" in second["state_note"]
    # replace=false adds a second rack instead
    third = expose(bridge, track="LB_SYNTH", parameters=["Main Vol"], replace=False)
    assert "replaced" not in third and len(track.devices) == 4


def test_expose_wires_rack_macros(bridge, env, song):
    result = expose(bridge, plugin="Serum 2", new_track=True, track_name="LB_MACROS",
                    parameters=["filter 1"],
                    macros={"1": "Filter 1 Freq", "2": "filter 1 res", "3": "A WT Pos",
                            "4": "env 1 attack"})
    macros = result["macros"]
    assert [macros[k]["parameter"] for k in "1234"] == \
        ["Filter 1 Freq", "Filter 1 Res", "A WT Pos", "Env 1 Attack"]
    rack = song.tracks[-1].devices[0]
    assert list(rack.macros_mapped)[:5] == [True, True, True, True, False]
    assert [rack.parameters[i].name for i in range(1, 5)] == \
        ["Filter 1 Freq", "Filter 1 Res", "A WT Pos", "Env 1 Attack"]
    assert macros["1"]["value"] == pytest.approx(0.5 * 127)       # synced to the parameter
    load = env.loads[-1]
    wired = [(s["macro"], s["range"]) for s in load["settings"] if s["macro"] >= 0]
    assert wired == [(i, (float(lib.MACRO_RANGE[0]), float(lib.MACRO_RANGE[1])))
                     for i in range(4)]
    # moving the macro moves the parameter over its whole range
    run(bridge, "devices.set_parameter", parameter=macros["3"]["path"], value=127)
    run(bridge, "devices.set_parameter", device=result["rack"], parameter="Filter 1 Res",
        value=0)
    plugin = rack.chains[0].devices[0]
    assert plugin.parameters[macros["3"]["index"]].value == pytest.approx(1.0)
    assert plugin.parameters[macros["2"]["index"]].value == pytest.approx(0.0)
    errors = fail(bridge, "plugin_racks.expose", plugin="Serum 2", new_track=True,
                  macros={"17": "Macro 1"})
    assert errors["type"] == "bad_args"


def test_expose_embeds_a_preset_file(bridge, env, song, tmp_path):
    preset = tmp_path / "Warm Pad.vstpreset"
    preset.write_bytes(lib.build_vstpreset(SERUM_CID, {"Comp": b"\x01\x02", "Cont": b"\x03"}))
    result = expose(bridge, plugin="Serum 2", new_track=True, parameters=["osc a"],
                    preset_file=str(preset))
    assert result["state"] == "preset_file" and "Warm Pad" in result["state_note"]
    assert env.loads[-1]["processor"] == "0102" and env.loads[-1]["controller"] == "03"
    other = tmp_path / "Tiny.vstpreset"
    other.write_bytes(lib.build_vstpreset(TINY_CID, {"Comp": b"x"}))
    error = fail(bridge, "plugin_racks.expose", plugin="Serum 2", new_track=True,
                 preset_file=str(other))
    assert error["type"] == "bad_args" and "another plug-in" in error["message"]
    assert fail(bridge, "plugin_racks.expose", plugin="Serum 2", new_track=True,
                preset_file=str(tmp_path / "nope.vstpreset"))["type"] == "not_found"


def test_expose_effect_goes_into_an_audio_effect_rack(bridge, env, song):
    shipped = run(bridge, "plugin_racks.map", plugin="Serum 2 FX", limit=1)
    assert (shipped["source"], shipped["count"]) == ("shipped", 541)
    for _ in range(5):          # probe it again (on an audio "LB_MAP" track)
        mapped = run(bridge, "plugin_racks.map", plugin="Serum 2 FX", max_seconds=60,
                     refresh=True)
        if mapped["status"] == "done":
            break
    assert mapped["count"] == 541 and mapped["source"] == "probed"
    assert all(load["group"] == "AudioEffectGroupDevice" for load in env.loads)
    result = expose(bridge, plugin="Serum 2 FX", new_track=True, parameters=["Main Vol"])
    rack = song.tracks[-1].devices[0]
    assert rack.class_name == "AudioEffectGroupDevice" and song.tracks[-1].has_audio_input
    assert result["exposed_count"] == 1


def test_expose_validates_before_touching_live(bridge, env, song):
    tracks = len(song.tracks)
    error = fail(bridge, "plugin_racks.expose", plugin="Serum 2", new_track=True,
                 parameters=["sound_design", "osc b"])
    assert error["type"] == "bad_args" and "128" in error["message"]     # 120 + 43 > 128
    assert fail(bridge, "plugin_racks.expose", plugin="Serum 2", new_track=True,
                parameters=["Warble"])["type"] == "not_found"
    assert fail(bridge, "plugin_racks.expose", plugin="Serum 2", plugin_format="AU",
                new_track=True)["type"] == "unsupported"
    assert fail(bridge, "plugin_racks.expose", plugin="Tiny Synth",
                new_track=True)["type"] == "invalid_state"               # map it first
    assert fail(bridge, "plugin_racks.expose", track="Bass")["type"] == "bad_args"  # no plug-in
    assert fail(bridge, "plugin_racks.expose", track="Bass", new_track=True,
                plugin="Serum 2")["type"] == "bad_args"
    assert len(song.tracks) == tracks and env.loads == []
    partial = expose(bridge, plugin="Serum 2", new_track=True,
                     parameters=["A Level", "Warble", "cutoff", "env 1", "osc b level"])
    assert partial["problems"][0]["query"] == "Warble"
    assert partial["problems"][1]["error"] == "ambiguous"        # Filter 1/2 Freq, Cutoff Rand
    assert "Filter 2 Freq" in partial["problems"][1]["candidates"]
    names = [p["name"] for p in partial["parameters"]]
    assert names[0] == "A Level" and names[-1] == "B Level"
    assert names[1:-1] == [n for n, _i in SHIPPED["parameters"] if n.startswith("Env 1 ")]


def test_presets_command_lists_embeddable_files(bridge, env, monkeypatch):
    library = env.library_path
    seen = {}

    def folders(identity, lib_path=None, extra=None, env=None):
        seen.update(identity=identity["name"], extra=extra)
        return [lib_path]           # hermetic: not this machine's real preset folders
    monkeypatch.setattr(lib, "preset_folders", folders)
    (library / "Serum 2.vstpreset").write_bytes(lib.build_vstpreset(SERUM_CID, {"Comp": b"x"}))
    result = run(bridge, "plugin_racks.presets", plugin="Serum 2", filter="serum")
    assert [p["name"] for p in result["presets"]] == ["Serum 2"]
    assert result["presets"][0]["embeddable"] is True
    assert any(f["path"] == str(library) for f in result["folders"])
    assert seen == {"identity": "Serum 2", "extra": SHIPPED["preset_folders"]}
    assert fail(bridge, "plugin_racks.presets", plugin="")["type"] == "bad_args"


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
    client = BridgeClient(host="127.0.0.1", port=tcp_bridge.port, timeout=10.0)
    try:
        yield create_app(client)
    finally:
        client.close()


def test_tools_validate_locally(fake_app):
    fake, app = fake_app
    assert call_tool(app, "live_plugin_expose", {"plugin_format": "LV2"})["type"] == "bad_args"
    assert call_tool(app, "live_plugin_expose", {"parameters": [""]})["type"] == "bad_args"
    assert call_tool(app, "live_plugin_expose", {"track": 1, "new_track": True})["type"] == \
        "bad_args"
    assert call_tool(app, "live_plugin_param_map", {})["type"] == "bad_args"
    assert call_tool(app, "live_plugin_param_map",
                     {"plugin": "x", "limit": 0})["type"] == "bad_args"
    assert call_tool(app, "live_plugin_preset_files", {})["type"] == "bad_args"
    assert fake.requests == []


def test_expose_tool_returns_a_timeout_while_indexing(fake_app):
    fake, app = fake_app
    fake.set_result("plugin_racks.expose", {"status": "indexing", "file": "x"})
    result = call_tool(app, "live_plugin_expose", {"plugin": "Serum 2", "wait_seconds": 1.2})
    assert result["status"] == "indexing" and result["timed_out"] is True
    assert len(fake.requests) >= 2
    assert fake.requests[0]["args"] == {"plugin": "Serum 2", "replace": True, "new_track": False}


def test_tools_end_to_end_against_the_stub(live_app, env, song):
    result = call_tool(live_app, "live_plugin_expose",
                       {"plugin": "Serum 2", "parameters": ["macros"], "new_track": True,
                        "track_name": "LB_TOOL"})
    assert result["status"] == "done" and result["exposed_count"] == 8
    mapped = call_tool(live_app, "live_plugin_param_map", {"plugin": "Tiny Synth"})
    assert mapped["status"] == "done" and mapped["count"] == 160
    shipped = call_tool(live_app, "live_plugin_param_map", {"plugin": "Serum 2",
                                                            "filter": "macro", "limit": 8})
    assert [p["name"] for p in shipped["parameters"]] == ["Macro %d" % i for i in range(1, 9)]
    presets = call_tool(live_app, "live_plugin_preset_files", {"plugin": "Serum 2"})
    assert presets["plugin"] == "Serum 2" and "presets" in presets


def test_expose_tool_closes_the_window_after_lives_auto_open(fake_app):
    """Live re-opens the plug-in window on the tick after a load (real Live: the handler's
    own close did not stick), so the tool sets it again with a second command."""
    fake, app = fake_app
    fake.set_result("plugin_racks.expose", {"status": "done", "device": "song.tracks[4]."
                                            "devices[1].chains[0].devices[0]",
                                            "editor_open": True})
    fake.set_result("plugins.set", {"editor_open": False})
    result = call_tool(app, "live_plugin_expose", {"track": 4, "editor_open": False})
    assert result["editor_open"] is False
    assert [r["cmd"] for r in fake.requests] == ["plugin_racks.expose", "plugins.set"]
    assert fake.requests[1]["args"] == {"device": "song.tracks[4].devices[1].chains[0]."
                                                  "devices[0]", "editor_open": False}
    fake.requests.clear()
    call_tool(app, "live_plugin_expose", {"track": 4})
    assert [r["cmd"] for r in fake.requests] == ["plugin_racks.expose"]


def test_rack_file_names_are_safe_on_windows():
    assert handler._file_safe("Kontakt: 7/8?") == "Kontakt_ 7_8_"
    assert handler._file_safe("Serum 2") == "Serum 2"
    assert handler.EXPOSE_FILE % handler._file_safe("Serum 2") == "LB Serum 2.adg"


def test_expose_finds_the_plugin_by_vendor_qualified_name(bridge, env, song):
    track = factory.add_track(song, "LB_VENDOR", "midi")
    plugins_live.install(Live).add_real_plugin(track, "Serum 2", plugins_live.serum2_names())
    result = expose(bridge, track="LB_VENDOR", plugin="Xfer Records/Serum 2",
                    parameters=["macros"])
    assert result["replaced"] == "Serum 2" and [d.name for d in track.devices] == ["Serum 2 Rack"]
