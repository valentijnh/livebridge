"""Module E — browser: bridge commands on the stub song/browser, and the MCP browser tools
(fake bridge for argument forwarding/validation, real TCP bridge end to end)."""

import asyncio
import json
import sys
from pathlib import Path

import pytest

import Live

_TESTS = Path(__file__).resolve().parent
for _p in (str(_TESTS.parent / "mcp_server"), str(_TESTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fake_bridge import FakeBridge  # noqa: E402
from live_stub import factory  # noqa: E402
from livebridge_mcp.client import BridgeClient  # noqa: E402
from livebridge_mcp.server import create_app  # noqa: E402

from LiveBridge.handlers import browser as browser_handlers  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_cache():
    browser_handlers.clear_cache()
    yield
    browser_handlers.clear_cache()


def run(bridge, cmd, **args):
    response = bridge.dispatch({"id": "b", "cmd": cmd, "args": args})
    assert response["ok"], response
    return response["result"]


def fail(bridge, cmd, **args):
    response = bridge.dispatch({"id": "b", "cmd": cmd, "args": args})
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


def names(items):
    return [item["name"] for item in items]


# --------------------------------------------------------------------------
# roots
# --------------------------------------------------------------------------

def test_roots(bridge, browser):
    data = run(bridge, "browser.roots")
    by_name = {root["name"]: root for root in data["roots"]}
    for name in ("instruments", "sounds", "drums", "audio_effects", "midi_effects", "plugins",
                 "max_for_live", "clips", "samples", "packs", "user_library", "user_folders",
                 "current_project", "colors"):
        assert name in by_name, name
    assert by_name["instruments"]["count"] == 5
    assert by_name["user_folders"]["list"] is True and by_name["user_folders"]["count"] == 1
    assert by_name["colors"]["count"] == 7
    assert "legacy_libraries" not in by_name
    assert data["extra_roots"] == []
    assert data["splice"]["available"] is False and "Splice MCP" in data["splice"]["note"]
    assert data["hotswap"]["target"] is None
    assert data["hotswap"]["filter_type"] == "disabled"
    assert "count" not in run(bridge, "browser.roots", counts=False)["roots"][0]


def test_extra_root_and_splice_detection(bridge, browser, monkeypatch):
    """A future Live exposing a Splice root is picked up from dir(browser)."""
    import Live
    splice_root = Live.Browser.BrowserItem("Splice", uri="query:Splice", is_folder=True)
    splice_root.add_child(Live.Browser.BrowserItem(
        "Splice Kick.wav", uri="query:Splice#Kick", is_loadable=True, load_kind="sample"))
    monkeypatch.setattr(type(browser), "splice", property(lambda self: splice_root),
                        raising=False)
    data = run(bridge, "browser.roots")
    assert data["extra_roots"] == ["splice"]
    assert data["splice"] == {"available": True, "root": "splice", "path": "splice"}
    listing = run(bridge, "browser.browse", path="splice")
    assert names(listing["items"]) == ["Splice Kick.wav"]
    hits = run(bridge, "browser.search", query="splice kick")
    assert hits["results"][0]["uri"] == "query:Splice#Kick"
    assert "splice" in hits["searched"]


def test_splice_folder_in_places(bridge, browser):
    import Live
    factory.add_user_folder(browser, "Splice", children=[
        Live.Browser.BrowserItem("Loop 120.wav", uri="userfolder:Splice:Loop%20120.wav",
                                 is_loadable=True, load_kind="sample")])
    data = run(bridge, "browser.roots")
    assert data["splice"]["available"] is True
    assert data["splice"]["path"] == "user_folders/Splice"
    listing = run(bridge, "browser.browse", path="splice")
    assert listing["path"] == "user_folders/Splice"
    assert names(listing["items"]) == ["Loop 120.wav"]


# --------------------------------------------------------------------------
# browse
# --------------------------------------------------------------------------

def test_browse_paths(bridge):
    data = run(bridge, "browser.browse", path="instruments")
    assert data["path"] == "instruments" and data["total"] == 5
    first = data["items"][0]
    assert first == {"name": "Operator", "uri": "query:Synths#Operator", "is_loadable": True,
                     "is_device": True, "source": "Live"}
    kits = run(bridge, "browser.browse", path="drums/drum kits")
    assert kits["path"] == "drums/Drum Kits"
    assert names(kits["items"]) == ["Kit-Core 808", "Kit-Core 909"]
    lazy = run(bridge, "browser.browse", path="samples/Drums")
    assert names(lazy["items"]) == ["Kick 1.wav", "Kick 2.wav", "Kick 3.wav"]
    lead = run(bridge, "browser.browse", path="user_library/My Sounds", detail="minimal")
    assert lead["items"] == [{"name": "My Lead.adg",
                              "uri": "userlibrary:My%20Sounds:My%20Lead.adg"}]
    places = run(bridge, "browser.browse", path="places/Sample Stash")
    assert places["path"] == "user_folders/Sample Stash"
    assert names(places["items"]) == ["Snare Top.wav"]
    assert run(bridge, "browser.browse", path="browser.sounds")["path"] == "sounds"
    assert run(bridge, "browser.browse", path="Plug-Ins")["path"] == "plugins"
    project = run(bridge, "browser.browse", path="current_project")
    assert names(project["items"]) == ["Project Samples"]
    assert project["items"][0]["is_folder"] is True


def test_browse_uri_name_paging_filters(bridge):
    listing = run(bridge, "browser.browse", path="plugins")
    vst3 = [i for i in listing["items"] if i["name"] == "VST3"][0]
    by_uri = run(bridge, "browser.browse", uri=vst3["uri"])
    assert names(by_uri["items"]) == ["Serum", "Diva"]
    assert by_uri["items"][0]["format"] == "VST3"
    by_name = run(bridge, "browser.browse", name="drum kits")
    assert by_name["path"] == "drums/Drum Kits"
    page = run(bridge, "browser.browse", path="audio_effects", limit=2, offset=1)
    assert names(page["items"]) == ["Delay", "EQ Eight"]
    assert page["total"] == 5 and page["next_offset"] == 3
    filtered = run(bridge, "browser.browse", path="audio_effects", filter="eq")
    assert names(filtered["items"]) == ["EQ Eight"]
    samples = run(bridge, "browser.browse", path="samples", kind="samples")
    assert names(samples["items"]) == ["Vocal Loop.wav"]
    folders = run(bridge, "browser.browse", path="samples", kind="folders")
    assert names(folders["items"]) == ["Drums"]
    full = run(bridge, "browser.browse", path="drums", detail="full")
    assert full["items"][0]["child_count"] == 2
    roots = run(bridge, "browser.browse")
    assert "roots" in roots


def test_browse_errors(bridge):
    error = fail(bridge, "browser.browse", path="instruments/Nope")
    assert error["type"] == "not_found"
    assert "instruments: no item 'Nope'" in error["message"] and "'Operator'" in error["message"]
    assert fail(bridge, "browser.browse", path="nowhere")["type"] == "not_found"
    assert fail(bridge, "browser.browse", path="instruments", uri="x")["type"] == "bad_args"
    assert fail(bridge, "browser.browse", path="instruments", limit=0)["type"] == "bad_args"
    assert fail(bridge, "browser.browse", path="instruments", kind="bogus")["type"] == "bad_args"
    assert fail(bridge, "browser.browse", uri="query:Nothing#Here")["type"] == "not_found"
    assert fail(bridge, "browser.browse", name="zzzz")["type"] == "not_found"


def test_browse_names_with_slashes(bridge, browser):
    factory.add_browser_item(browser.sounds, "Hip-Hop/R&B", is_folder=True, children=[
        factory.add_browser_item(None, "Boom Bap Keys", uri="query:Sounds#BoomBap",
                                 is_device=True, load_kind="device")])
    data = run(bridge, "browser.browse", path="sounds/Hip-Hop/R&B")
    assert data["path"] == "sounds/Hip-Hop/R&B"
    assert names(data["items"]) == ["Boom Bap Keys"]


# --------------------------------------------------------------------------
# search
# --------------------------------------------------------------------------

def test_search_ranking_and_fields(bridge):
    data = run(bridge, "browser.search", query="reverb")
    top = data["results"][0]
    assert top["name"] == "Reverb" and top["path"] == "audio_effects/Reverb"
    assert top["uri"] == "query:AudioFx#Reverb" and top["is_device"] is True
    assert data["truncated"] is False and "packs" not in data["searched"]
    kit = run(bridge, "browser.search", query="808")["results"]
    assert kit[0]["name"] == "Kit-Core 808"
    words = run(bridge, "browser.search", query="bass sub")["results"]
    assert words[0]["name"] == "Sub Bass"
    piano = run(bridge, "browser.search", query="grand piano")
    assert piano["total"] == 0
    piano = run(bridge, "browser.search", query="grand piano", root="all")
    assert piano["results"][0]["path"] == "packs/Core Library/Grand Piano"


def test_search_categories_roots_formats(bridge, browser):
    samples = run(bridge, "browser.search", query="kick", category="sample")
    assert names(samples["results"]) == ["Kick 1.wav", "Kick 2.wav", "Kick 3.wav"]
    assert samples["searched"][0] == "samples"
    clips = run(bridge, "browser.search", query="demo", category="clip")
    assert names(clips["results"]) == ["Demo Clip.alc"]
    only_user = run(bridge, "browser.search", query="my", root="user_library")
    assert set(names(only_user["results"])) >= {"My Sounds", "My Lead.adg", "My Loop.wav"}
    assert only_user["searched"] == ["user_library"]
    loadable = run(bridge, "browser.search", query="my", root=["user_library"],
                   loadable_only=True)
    assert "My Sounds" not in names(loadable["results"])
    # the same plug-in as VST3 and AU: VST3 ranks first unless a format is asked for
    au_folder = [c for c in browser.plugins.children if c.name == "Audio Units"][0]
    factory.add_browser_item(au_folder, "Serum", uri="query:Plugins#AU:Serum",
                             is_device=True, load_kind="device")
    serum = run(bridge, "browser.search", query="serum", category="plugin")
    assert [r["format"] for r in serum["results"]] == ["VST3", "AU"]
    au = run(bridge, "browser.search", query="serum", plugin_format="au")
    assert [r["uri"] for r in au["results"]] == ["query:Plugins#AU:Serum"]
    kits = run(bridge, "browser.search", query="kit", category="drum_kit")
    assert names(kits["results"])[:2] == ["Kit-Core 808", "Kit-Core 909"]
    paged = run(bridge, "browser.search", query="kick", category="sample", limit=1, offset=1)
    assert names(paged["results"]) == ["Kick 2.wav"] and paged["next_offset"] == 2
    minimal = run(bridge, "browser.search", query="reverb", detail="minimal")
    assert set(minimal["results"][0]) == {"name", "uri", "path"}


def test_search_cache_and_refresh(bridge, browser):
    first = run(bridge, "browser.search", query="operator")
    assert first["cached"] == []
    second = run(bridge, "browser.search", query="wavetable")
    assert set(second["cached"]) == set(second["searched"])
    factory.add_browser_item(browser.instruments, "Meld", uri="query:Synths#Meld",
                             is_device=True, load_kind="device")
    assert run(bridge, "browser.search", query="meld")["total"] == 0  # cached tree
    fresh = run(bridge, "browser.search", query="meld", refresh=True)
    assert fresh["results"][0]["name"] == "Meld" and fresh["cached"] == []
    info = run(bridge, "browser.cache")
    assert info["roots"]["instruments"]["items"] >= 6 and info["uris"] > 0
    assert run(bridge, "browser.cache", action="clear") == {"cleared": True}
    assert run(bridge, "browser.cache")["uris"] == 0
    assert fail(bridge, "browser.cache", action="nuke")["type"] == "bad_args"


def test_search_limits_truncate(bridge, browser):
    def many(item):
        return [factory.add_browser_item(None, "Pad %03d.wav" % i, is_loadable=True,
                                         load_kind="sample") for i in range(600)]
    factory.add_browser_item(browser.instruments, "Huge", is_folder=True,
                             children_factory=many)
    data = run(bridge, "browser.search", query="operator", max_visits=100)
    assert data["truncated"] is True and data["skipped"]
    assert data["visited"] <= 101
    # a partial walk is not reused: a bigger budget walks again and finds everything
    full = run(bridge, "browser.search", query="pad 599")
    assert full["results"][0]["name"] == "Pad 599.wav" and full["truncated"] is False
    shallow = run(bridge, "browser.search", query="kick", root="samples", max_depth=1,
                  refresh=True)
    assert shallow["total"] == 0


def test_uri_fast_path_follows_folder_names(bridge, browser):
    """Real Live uris look like ``query:Synths#Analog:Bass`` (root uri + folder names): they
    are resolved by walking just that path, without indexing the whole root."""
    import Live
    folder = factory.add_browser_item(browser.instruments, "Folder A",
                                      uri="query:instruments#Folder%20A", is_folder=True)
    folder.add_child(Live.Browser.BrowserItem("Deep Item", uri="query:instruments#Folder%20A:"
                                              "FileId_77", is_loadable=True,
                                              load_kind="device", is_device=True))
    entry = browser_handlers.find_by_uri(browser, "query:instruments#Folder%20A:FileId_77")
    assert entry.path == "instruments/Folder A/Deep Item" and entry.depth == 2
    assert browser_handlers._INDEX == {}  # no walk was needed
    root = browser_handlers.find_by_uri(browser, "query:instruments")
    assert root.path == "instruments" and root.depth == 0


def test_search_dedupes_the_same_file(bridge, browser):
    """Live lists one file under several roots; hits sharing a FileId collapse to one."""
    rack = factory.add_browser_item(browser.instruments, "Instrument Rack", is_folder=True)
    for parent, uri in ((browser.sounds, "query:Sounds#Keys:FileId_13853"),
                        (rack, "query:Synths#Rack:Keys:FileId_13853")):
        factory.add_browser_item(parent, "Grand Piano.adg", uri=uri, is_loadable=True,
                                 load_kind="device")
    data = run(bridge, "browser.search", query="grand piano")
    assert data["total"] == 1 and data["results"][0]["uri"] == "query:Sounds#Keys:FileId_13853"


def test_search_errors(bridge):
    assert fail(bridge, "browser.search", query="  ")["type"] == "bad_args"
    assert fail(bridge, "browser.search", query="x", category="synth")["type"] == "bad_args"
    assert fail(bridge, "browser.search", query="x", root="nowhere")["type"] == "not_found"
    assert fail(bridge, "browser.search", query="x", root=[])["type"] == "bad_args"
    assert fail(bridge, "browser.search", query="x", plugin_format="aax")["type"] == "bad_args"
    assert fail(bridge, "browser.search", query="x", limit=500)["type"] == "bad_args"


# --------------------------------------------------------------------------
# load
# --------------------------------------------------------------------------

def test_load_effect_by_query(bridge, song):
    vocals = song.tracks[1]
    data = run(bridge, "browser.load", query="delay", category="audio_effect", track="Vocals")
    assert data["via"] == "query" and data["loaded"]["path"] == "audio_effects/Delay"
    assert data["track"]["name"] == "Vocals"
    assert [d["name"] for d in data["inserted"]] == ["Delay"]
    assert data["inserted"][0]["path"] == "song.tracks[1].devices[1]"
    assert [d.name for d in vocals.devices] == ["Reverb", "Delay"]
    assert song.view.selected_track == vocals
    assert vocals.view.device_insert_mode == 0
    assert "removed" not in data


def test_load_by_uri_and_path(bridge, song):
    data = run(bridge, "browser.load", uri="query:MidiFx#Arpeggiator", track=0)
    assert data["via"] == "uri" and data["inserted"][0]["name"] == "Arpeggiator"
    data = run(bridge, "browser.load", path="instruments/Wavetable", track="Drums")
    assert data["via"] == "path" and data["inserted"][0]["name"] == "Wavetable"
    # uri of an item that was never listed (lazy folder): found by walking
    browser_handlers.clear_cache()
    data = run(bridge, "browser.load", uri="query:Samples#Drums:Kick%202.wav", track="Bass")
    assert data["loaded"]["name"] == "Kick 2.wav"
    assert data["inserted"][0]["class_name"] == "OriginalSimpler"


def test_load_new_track_and_default_track(bridge, song):
    count = len(song.tracks)
    data = run(bridge, "browser.load", query="Operator", category="instrument",
               new_track="midi", track_name="Lead")
    assert data["created_track"] is True and data["track"]["name"] == "Lead"
    assert len(song.tracks) == count + 1 and song.tracks[-1].devices[0].name == "Operator"
    song.view.selected_track = song.tracks[1]
    data = run(bridge, "browser.load", query="compressor")
    assert data["track"]["name"] == "Vocals" and data["inserted"][0]["name"] == "Compressor"
    named = run(bridge, "browser.load", uri="query:Synths#Analog", new_track="midi")
    assert named["track"]["name"] == "Analog"


def test_load_sample_into_slot(bridge, song):
    vocals = song.tracks[1]
    data = run(bridge, "browser.load", query="vocal loop", category="sample", track="Vocals",
               slot=2)
    assert data["clips"][0]["path"] == "song.tracks[1].clip_slots[2].clip"
    assert vocals.clip_slots[2].clip.name == "Vocal Loop"
    assert song.view.highlighted_clip_slot == vocals.clip_slots[2]
    error = fail(bridge, "browser.load", query="vocal loop", track="Vocals", slot=2)
    assert error["type"] == "invalid_state" and "not empty" in error["message"]


def test_load_position_and_restore(bridge, song):
    vocals = song.tracks[1]
    factory.add_device(vocals, "EQ", "Eq8", kind="audio_effect")
    song.view.select_device(vocals.devices[0])
    vocals.view.device_insert_mode = 0
    data = run(bridge, "browser.load", uri="query:AudioFx#Delay", track="Vocals",
               position="before_selected")
    assert data["inserted"][0]["path"] == "song.tracks[1].devices[0]"
    assert [d.name for d in vocals.devices][:2] == ["Delay", "Reverb"]
    assert vocals.view.device_insert_mode == 0  # restored
    data = run(bridge, "browser.load", uri="query:AudioFx#Auto%20Filter", track="Vocals",
               position=None)
    assert data["inserted"][0]["name"] == "Auto Filter"


def test_load_errors(bridge, song, browser):
    assert fail(bridge, "browser.load")["type"] == "bad_args"
    assert fail(bridge, "browser.load", uri="a", query="b")["type"] == "bad_args"
    error = fail(bridge, "browser.load", path="drums/Drum Kits", track=0)
    assert error["type"] == "invalid_state" and "not loadable" in error["message"]
    assert fail(bridge, "browser.load", query="zzzz nothing")["type"] == "not_found"
    assert fail(bridge, "browser.load", query="delay", position="middle")["type"] == "bad_args"
    assert fail(bridge, "browser.load", query="delay", new_track="group")["type"] == "bad_args"
    assert fail(bridge, "browser.load", query="delay", track=0,
                new_track="audio")["type"] == "bad_args"
    assert fail(bridge, "browser.load", query="delay", track=9)["type"] == "not_found"
    assert fail(bridge, "browser.load", query="delay", device=0)["type"] == "bad_args"
    assert fail(bridge, "browser.load", query="delay", drum_pad=36)["type"] == "bad_args"
    assert fail(bridge, "browser.load", query="delay", hotswap=True)["type"] == "invalid_state"


def test_load_is_one_undo_step(bridge, song):
    before = len(song._undo_steps)
    run(bridge, "browser.load", query="delay", track="Vocals")
    assert len(song._undo_steps) == before + 1


def test_normal_load_clears_hotswap_target(bridge, song, browser):
    browser.hotswap_target = song.tracks[1].devices[0]
    data = run(bridge, "browser.load", query="delay", track="Bass")
    assert "cleared the hot-swap target" in data["notes"][0]
    assert browser.hotswap_target is None
    assert data["inserted"][0]["name"] == "Delay"
    assert [d.name for d in song.tracks[1].devices] == ["Reverb"]


def test_hotswap_load_replaces_device(bridge, song, browser):
    data = run(bridge, "browser.load", query="delay", hotswap=True, track="Vocals",
               device="Reverb")
    assert data["hotswap"] is True and data["track"]["name"] == "Vocals"
    assert [d["name"] for d in data["inserted"]] == ["Delay"]
    assert data["removed"] == [{"name": "Reverb"}]
    assert [d.name for d in song.tracks[1].devices] == ["Delay"]
    # the current hot-swap target is used when no device is given
    data = run(bridge, "browser.load", uri="query:AudioFx#Compressor", hotswap=True)
    assert data["removed"] == [{"name": "Delay"}]
    assert data["inserted"][0]["name"] == "Compressor"
    error = fail(bridge, "browser.load", query="delay", hotswap=True, track="Vocals",
                 device=0, slot=1)
    assert error["type"] == "bad_args"


# --------------------------------------------------------------------------
# hotswap / preview
# --------------------------------------------------------------------------

def test_hotswap_info_set_clear(bridge, song, browser):
    data = run(bridge, "browser.hotswap", action="set", track="Vocals", device="Reverb")
    assert data["target"]["path"] == "song.tracks[1].devices[0]"
    assert browser.hotswap_target == song.tracks[1].devices[0]
    assert run(bridge, "browser.hotswap")["target"]["name"] == "Reverb"
    pad = run(bridge, "browser.hotswap", action="set", track="Drums", device=0, drum_pad=38)
    assert pad["target"]["kind"] == "drum_pad" and pad["target"]["note"] == 38
    assert pad["target"]["name"] == "Snare"
    by_name = run(bridge, "browser.hotswap", action="set", track="Drums", device="Drum Rack",
                  drum_pad="hat")
    assert by_name["target"]["note"] == 42
    by_path = run(bridge, "browser.hotswap", action="set",
                  device="song.tracks[0].devices[0]")
    assert by_path["target"]["name"] == "Operator"
    assert run(bridge, "browser.hotswap", action="clear")["target"] is None
    assert fail(bridge, "browser.hotswap", action="set")["type"] == "bad_args"
    assert fail(bridge, "browser.hotswap", action="zap")["type"] == "bad_args"
    assert fail(bridge, "browser.hotswap", action="set", track="Vocals", device=0,
                drum_pad=36)["type"] == "bad_args"
    assert fail(bridge, "browser.hotswap", action="set", track="Drums", device=0,
                drum_pad="cowbell")["type"] == "not_found"
    assert fail(bridge, "browser.hotswap", device=0)["type"] == "bad_args"


def test_preview(bridge, browser):
    data = run(bridge, "browser.preview", query="vocal loop", category="sample")
    assert data["previewing"]["name"] == "Vocal Loop.wav"
    assert browser.previewed_items[-1].name == "Vocal Loop.wav"
    assert run(bridge, "browser.preview", path="samples/Drums")["previewing"]["name"] == "Drums"
    assert run(bridge, "browser.preview", stop=True) == {"stopped": True}
    assert browser.previewed_items[-1] is None
    assert fail(bridge, "browser.preview", stop=True, query="x")["type"] == "bad_args"
    assert fail(bridge, "browser.preview")["type"] == "bad_args"


def test_helpers():
    assert browser_handlers.stem("808 Core Kit.adg") == "808 Core Kit"
    assert browser_handlers.stem("Name.with.dots") == "Name.with.dots"
    assert browser_handlers.norm_text(" Kit-Core_808 ") == "kit core 808"
    assert browser_handlers.plugin_format("plugins/VST3/Xfer/Serum", "") == "VST3"
    assert browser_handlers.plugin_format("plugins/Audio Units/Serum", "") == "AU"
    assert browser_handlers.plugin_format("plugins/VST/Serum", "") == "VST2"
    assert browser_handlers.plugin_format("plugins/Serum", "query:Plugins#VST3:Serum") == "VST3"
    assert browser_handlers.plugin_format("plugins/Serum", "query:x") is None
    assert browser_handlers.check_category("Drums") == "drum_kit"
    assert browser_handlers.check_format("VST") == "VST2"


# --------------------------------------------------------------------------
# MCP tools
# --------------------------------------------------------------------------

BROWSER_TOOLS = {"live_browser_roots", "live_browser_browse", "live_browser_search",
                 "live_browser_load", "live_browser_hotswap", "live_browser_preview"}

#: The former quick loaders were thin wrappers of browser.load: one tool with `category` now.
REMOVED_TOOLS = {"live_browser_load_instrument", "live_browser_load_effect",
                 "live_browser_load_plugin", "live_browser_load_drum_kit",
                 "live_browser_load_sample"}


@pytest.fixture()
def fake_app():
    fake = FakeBridge().start()
    client = BridgeClient(host=fake.host, port=fake.port, timeout=5.0)
    try:
        yield fake, create_app(client)
    finally:
        client.close()
        fake.stop()


def test_browser_tools_registered(fake_app):
    _fake, app = fake_app
    assert "browser" in app.tool_modules
    tools = {t.name: t for t in asyncio.run(app.list_tools())}
    assert BROWSER_TOOLS <= set(tools)
    assert not REMOVED_TOOLS & set(tools)
    for name in BROWSER_TOOLS:
        assert len(tools[name].description or "") > 150, name


def test_browser_tools_forward_arguments(fake_app):
    fake, app = fake_app
    for cmd in ("browser.roots", "browser.browse", "browser.search", "browser.load",
                "browser.hotswap", "browser.preview"):
        fake.set_result(cmd, {"ok": cmd})

    def last():
        return fake.requests[-1]["cmd"], fake.requests[-1].get("args", {})

    call_tool(app, "live_browser_roots")
    assert last() == ("browser.roots", {})
    call_tool(app, "live_browser_browse", {"path": "instruments", "limit": 10, "kind": "devices"})
    assert last() == ("browser.browse", {"path": "instruments", "limit": 10, "kind": "devices"})
    call_tool(app, "live_browser_search", {"query": "kick", "root": ["samples", "packs"],
                                           "category": "sample", "refresh": True})
    assert last() == ("browser.search", {"query": "kick", "root": ["samples", "packs"],
                                         "category": "sample", "refresh": True})
    call_tool(app, "live_browser_load", {"uri": "query:Synths#Operator", "track": "Bass",
                                         "slot": 1, "position": None})
    assert last() == ("browser.load", {"uri": "query:Synths#Operator", "track": "Bass",
                                       "slot": 1, "position": None})
    call_tool(app, "live_browser_load", {"query": "pad", "new_track": "midi",
                                         "track_name": "Pads"})
    assert last() == ("browser.load", {"query": "pad", "new_track": "midi",
                                       "track_name": "Pads"})
    # new_track=true (the bool form the other loaders used) is accepted: a MIDI track,
    # or an audio track for samples — no raw pydantic "Input should be a valid string"
    call_tool(app, "live_browser_load", {"query": "Operator", "category": "instrument",
                                         "new_track": True})
    assert last() == ("browser.load", {"query": "Operator", "category": "instrument",
                                       "new_track": "midi"})
    call_tool(app, "live_browser_load", {"query": "kick", "category": "sample",
                                         "new_track": True, "track_name": "Kick"})
    assert last() == ("browser.load", {"query": "kick", "category": "sample",
                                       "new_track": "audio", "track_name": "Kick"})
    call_tool(app, "live_browser_load", {"query": "x", "new_track": False, "track": 1})
    assert last() == ("browser.load", {"query": "x", "track": 1})
    call_tool(app, "live_browser_load", {"query": "Arp", "track": 0,
                                         "category": "midi_effect"})
    assert last() == ("browser.load", {"query": "Arp", "category": "midi_effect", "track": 0})
    call_tool(app, "live_browser_load", {"query": "Serum", "category": "plugin",
                                         "plugin_format": "au"})
    assert last() == ("browser.load", {"query": "Serum", "category": "plugin",
                                       "plugin_format": "au"})
    call_tool(app, "live_browser_hotswap")
    assert last() == ("browser.hotswap", {})
    call_tool(app, "live_browser_hotswap", {"action": "set", "track": 2, "device": 0,
                                            "drum_pad": 36})
    assert last() == ("browser.hotswap", {"action": "set", "track": 2, "device": 0,
                                          "drum_pad": 36})
    call_tool(app, "live_browser_hotswap", {"action": "load", "device": 1, "query": "delay"})
    assert last() == ("browser.load", {"query": "delay", "device": 1, "hotswap": True})
    call_tool(app, "live_browser_preview", {"uri": "query:Samples#x"})
    assert last() == ("browser.preview", {"uri": "query:Samples#x"})
    call_tool(app, "live_browser_preview", {"stop": True})
    assert last() == ("browser.preview", {"stop": True})


def test_browser_tools_validate_locally(fake_app):
    fake, app = fake_app
    count = len(fake.requests)
    for name, args in [
        ("live_browser_browse", {"path": "a", "uri": "b"}),
        ("live_browser_browse", {"limit": 0}),
        ("live_browser_browse", {"kind": "synths"}),
        ("live_browser_browse", {"detail": "huge"}),
        ("live_browser_search", {"query": " "}),
        ("live_browser_search", {"query": "x", "category": "synth"}),
        ("live_browser_search", {"query": "x", "plugin_format": "aax"}),
        ("live_browser_search", {"query": "x", "max_seconds": 100}),
        ("live_browser_search", {"query": "x", "root": []}),
        ("live_browser_load", {}),
        ("live_browser_load", {"uri": "a", "path": "b"}),
        ("live_browser_load", {"query": ""}),
        ("live_browser_load", {"query": "x", "new_track": "group"}),
        ("live_browser_load", {"query": "x", "new_track": "midi", "track": 1}),
        ("live_browser_load", {"query": "x", "track_name": "n"}),
        ("live_browser_load", {"query": "x", "position": "top"}),
        ("live_browser_load", {"query": "x", "track": 1, "new_track": True}),
        ("live_browser_load", {"query": "x", "track_name": "n", "new_track": False}),
        ("live_browser_load", {"query": "x", "plugin_format": "clap"}),
        ("live_browser_hotswap", {"action": "burn"}),
        ("live_browser_hotswap", {"action": "set"}),
        ("live_browser_hotswap", {"action": "load"}),
        ("live_browser_hotswap", {"action": "info", "query": "x"}),
        ("live_browser_hotswap", {"action": "load", "query": "x", "drum_pad": 36}),
        ("live_browser_preview", {}),
        ("live_browser_preview", {"stop": True, "uri": "x"}),
    ]:
        result = call_tool(app, name, args)
        assert isinstance(result, dict) and result.get("type") == "bad_args", (name, result)
    assert len(fake.requests) == count


def test_browser_tools_report_connection_errors():
    client = BridgeClient(host="127.0.0.1", port=1, timeout=1.0)
    app = create_app(client)
    result = call_tool(app, "live_browser_roots")
    assert result["type"] == "connection"


def test_browser_tools_end_to_end(tcp_bridge, song):
    client = BridgeClient(host="127.0.0.1", port=tcp_bridge.port, timeout=5.0)
    try:
        app = create_app(client)
        roots = call_tool(app, "live_browser_roots")
        assert any(r["name"] == "instruments" for r in roots["roots"])
        hits = call_tool(app, "live_browser_search", {"query": "eq eight"})
        assert hits["results"][0]["uri"] == "query:AudioFx#EQ%20Eight"
        loaded = call_tool(app, "live_browser_load", {"query": "eq eight", "track": "Vocals",
                                                      "category": "audio_effect"})
        assert loaded["inserted"][0]["name"] == "EQ Eight"
        inst = call_tool(app, "live_browser_load", {"query": "wavetable", "new_track": True,
                                                    "category": "instrument"})
        assert inst["created_track"] is True and inst["inserted"][0]["name"] == "Wavetable"
        kit = call_tool(app, "live_browser_load", {"query": "909", "new_track": True,
                                                   "category": "drum_kit"})
        assert kit["loaded"]["name"] == "Kit-Core 909"
        rack = call_tool(app, "live_browser_load", {"query": "Drum Rack", "track": "Bass",
                                                    "category": "instrument"})
        assert rack["loaded"]["name"] == "Drum Rack"
        plugin = call_tool(app, "live_browser_load", {"query": "diva", "track": "Bass",
                                                      "category": "plugin"})
        assert plugin["loaded"]["path"] == "plugins/VST3/Diva"
        sample = call_tool(app, "live_browser_load", {"query": "snare top", "category": "sample",
                                                      "track": "Vocals", "slot": 3})
        assert sample["clips"][0]["name"] == "Snare Top"
        swap = call_tool(app, "live_browser_hotswap", {"action": "load", "track": "Vocals",
                                                       "device": "Reverb",
                                                       "query": "delay"})
        assert swap["removed"] == [{"name": "Reverb"}]
        missing = call_tool(app, "live_browser_load", {"query": "does not exist anywhere"})
        assert missing["type"] == "not_found"
        preview = call_tool(app, "live_browser_preview", {"query": "vocal loop"})
        assert preview["previewing"]["name"] == "Vocal Loop.wav"
    finally:
        client.close()


# --------------------------------------------------------------------------
# real Live 12.4.5 behaviour (mirrored by the shared stub's Browser)
# --------------------------------------------------------------------------

@pytest.fixture
def real_browser(app):
    """Hot-swap filtering, same-target refusal, .alc -> new track, no sample loads in the
    Arrangement view — as measured on Live 12.4.5 (the shared stub's behaviour)."""
    yield app.browser


def test_hotswap_target_filters_the_browser_and_is_never_cached(bridge, song, real_browser):
    run(bridge, "browser.hotswap", action="set", track="Vocals", device="Reverb")
    assert len(real_browser.instruments.children) == 0  # audio effect target: filtered
    data = run(bridge, "browser.search", query="operator", root="instruments")
    assert data["total"] == 0 and "hot-swap target 'Reverb'" in data["hotswap_filter"]
    listing = run(bridge, "browser.browse", path="instruments")
    assert listing["total"] == 0 and "hotswap_filter" in listing
    assert "instruments" not in run(bridge, "browser.cache")["roots"]
    run(bridge, "browser.hotswap", action="clear")
    found = run(bridge, "browser.search", query="operator", root="instruments")
    assert found["total"] >= 1 and "hotswap_filter" not in found


def test_setting_the_same_hotswap_target_twice_is_fine(bridge, song, real_browser):
    """Live raises "Couldn't set hotswap target" for the current target; the handlers
    skip the write."""
    run(bridge, "browser.hotswap", action="set", track="Vocals", device="Reverb")
    again = run(bridge, "browser.hotswap", action="set", track="Vocals", device="Reverb")
    assert again["target"]["name"] == "Reverb"
    swapped = run(bridge, "browser.load", uri="query:AudioFx#Delay", hotswap=True)
    assert swapped["inserted"][0]["name"] == "Delay" and swapped["removed"] == [
        {"name": "Reverb"}]
    # Live keeps the new device as the target, so a second swap works too
    again = run(bridge, "browser.load", uri="query:AudioFx#Compressor", hotswap=True)
    assert again["removed"] == [{"name": "Delay"}]


def test_normal_load_clears_the_target_before_the_lookup(bridge, song, real_browser):
    real_browser.hotswap_target = song.tracks[0].devices[0]      # Operator: instrument
    assert len(real_browser.audio_effects.children) == 0
    data = run(bridge, "browser.load", path="audio_effects/Delay", track="Vocals")
    assert data["inserted"][0]["name"] == "Delay"
    assert "cleared the hot-swap target" in data["notes"][0]
    assert real_browser.hotswap_target is None


def test_hotswap_load_sets_its_target_before_the_lookup(bridge, song, real_browser):
    real_browser.hotswap_target = song.tracks[0].devices[0]      # Operator: filters effects
    data = run(bridge, "browser.load", path="audio_effects/Delay", hotswap=True,
               track="Vocals", device="Reverb")
    assert data["inserted"][0]["name"] == "Delay"
    # a failed lookup puts the previous target back
    real_browser.hotswap_target = song.tracks[0].devices[0]
    error = fail(bridge, "browser.load", path="audio_effects/Nope", hotswap=True,
                 track="Vocals", device="Delay")
    assert error["type"] == "not_found"
    assert real_browser.hotswap_target == song.tracks[0].devices[0]


def test_drum_pad_targets_accept_note_names(bridge, song, real_browser):
    by_note = run(bridge, "browser.hotswap", action="set", track="Drums", device=0,
                  drum_pad="D1")
    assert by_note["target"]["note"] == 38
    by_chain = run(bridge, "browser.hotswap", action="set", track="Drums", device=0,
                   drum_pad="kick")
    assert by_chain["target"]["note"] == 36


def test_live_clip_gets_a_new_track(bridge, song, browser, real_browser):
    factory.add_browser_item(browser.clips, "Beat 120 bpm.alc", uri="query:Clips#FileId_1",
                             is_loadable=True, load_kind="clip")
    count = len(song.tracks)
    data = run(bridge, "browser.load", uri="query:Clips#FileId_1", track="Bass", slot=2)
    assert len(song.tracks) == count + 1
    assert data["new_tracks"][0]["name"].endswith("Beat 120 bpm")
    assert data["clips"][0]["name"] == "Beat 120 bpm"
    assert "new track" in data["notes"][0]
    assert not song.tracks[0].clip_slots[2].has_clip


def test_samples_do_not_load_in_the_arrangement_view(bridge, song, app, real_browser):
    app.view.show_view("Arranger")
    data = run(bridge, "browser.load", query="vocal loop", category="sample", track="Vocals",
               slot=2)
    assert "Arrangement view is focused" in data["notes"][0]
    assert not song.tracks[1].clip_slots[2].has_clip
    app.view.show_view("Session")
    data = run(bridge, "browser.load", query="vocal loop", category="sample", track="Vocals",
               slot=2)
    assert data["clips"][0]["name"] == "Vocal Loop"


def test_hotswapped_preset_of_the_same_device_is_reported_as_changed(bridge, song, browser,
                                                                        monkeypatch):
    """Live keeps the device object when a preset of the same device is hot-swapped in:
    only its name changes (verified with Reverb -> "Arena Tail")."""
    reverb = song.tracks[1].devices[0]

    def load_preset(item):
        reverb.name = "Arena Tail"
    monkeypatch.setattr(browser, "load_item", load_preset)
    data = run(bridge, "browser.load", uri="query:AudioFx#Delay", hotswap=True,
               track="Vocals", device="Reverb")
    assert data["changed"][0]["name"] == "Arena Tail" and data["changed"][0]["was"] == "Reverb"
    assert "inserted" not in data and "removed" not in data


def test_search_drops_pack_copies_of_category_items(bridge, browser):
    """``packs`` mirrors the category roots with other uris; one hit per file."""
    factory.add_browser_item(browser.drums, "808 Core Kit.adg", uri="query:Drums#FileId_7",
                             is_loadable=True, load_kind="device", source="Core Library")
    folder = factory.add_browser_item(browser.packs, "Racks", is_folder=True)
    factory.add_browser_item(folder, "808 Core Kit.adg",
                             uri="query:LivePacks#core:Racks:808%20Core%20Kit.adg",
                             is_loadable=True, load_kind="device", source="Core Library")
    data = run(bridge, "browser.search", query="808 core kit", root=["drums", "packs"])
    uris = [hit["uri"] for hit in data["results"]]
    assert uris[0] == "query:Drums#FileId_7"
    assert not any(uri.startswith("query:LivePacks") for uri in uris)


def test_loading_the_same_device_again_is_reported_as_reloaded(bridge, song, browser,
                                                              monkeypatch):
    """Live 12.4.5 loads an Operator over an Operator in place: same device object, same
    name, parameters reset — nothing to diff, so the answer says what happened."""
    monkeypatch.setattr(browser, "load_item", lambda item: None)
    data = run(bridge, "browser.load", uri="query:Synths#Operator", track="Bass")
    assert data["changed"][0]["name"] == "Operator" and data["changed"][0]["reloaded"] is True
    assert "keeps the device object" in data["notes"][0]
