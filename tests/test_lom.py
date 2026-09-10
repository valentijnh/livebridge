"""LOM path grammar, get/set/call/describe/children, coercion and path_of."""

import pytest

from LiveBridge import lom
from LiveBridge.registry import BridgeError


@pytest.fixture
def ctx(bridge):
    return bridge.ctx


# --------------------------------------------------------------------------
# grammar
# --------------------------------------------------------------------------

def test_parse_path_accepts_the_documented_grammar():
    assert lom.parse_path("song") == [("song", [])]
    assert lom.parse_path("song.tracks[2]") == [("song", []), ("tracks", [2])]
    assert lom.parse_path("song.tracks[-1].devices[0].parameters[3]") == [
        ("song", []), ("tracks", [-1]), ("devices", [0]), ("parameters", [3])]
    assert lom.parse_path("app.view") == [("app", []), ("view", [])]
    assert lom.parse_path("browser.instruments")[0][0] == "browser"
    assert lom.parse_path("application.view")[0][0] == "app"


@pytest.mark.parametrize("path,error_type", [
    ("", "bad_args"),
    ("song..tracks", "bad_args"),
    ("song.tracks[a]", "bad_args"),
    ("song.tracks[0", "bad_args"),
    ("nope.tracks", "not_found"),
    ("song[0]", "bad_args"),
    (42, "bad_args"),
])
def test_parse_path_rejects_garbage(path, error_type):
    with pytest.raises(BridgeError) as info:
        lom.parse_path(path)
    assert info.value.type == error_type


def test_resolve_walks_the_whole_tree(ctx, song):
    assert lom.resolve("song", ctx) is song
    assert lom.resolve("song.tracks[0]", ctx) is song.tracks[0]
    assert lom.resolve("song.tracks[-1]", ctx) is song.tracks[-1]
    assert lom.resolve("song.return_tracks[0]", ctx) is song.return_tracks[0]
    assert lom.resolve("song.master_track", ctx) is song.master_track
    assert lom.resolve("song.scenes[1]", ctx) is song.scenes[1]
    assert lom.resolve("song.view.selected_track", ctx) is song.tracks[0]
    assert lom.resolve("song.tracks[0].clip_slots[0].clip", ctx).name == "Bass Loop"
    assert lom.resolve("song.tracks[0].arrangement_clips[0]", ctx).name == "Bass Arr"
    assert lom.resolve("song.tracks[0].devices[1].chains[0]", ctx).name == "Chain 1"
    assert lom.resolve("song.tracks[2].devices[0].drum_pads[36].chains[0]", ctx) is not None
    assert lom.resolve("browser.instruments", ctx) is ctx.browser.instruments
    assert lom.resolve("app.view", ctx) is ctx.app.view


def test_resolve_empty_clip_slot_is_none_not_an_error(ctx):
    assert lom.resolve("song.tracks[0].clip_slots[3].clip", ctx) is None
    with pytest.raises(BridgeError) as info:
        lom.resolve("song.tracks[0].clip_slots[3].clip.name", ctx)
    assert info.value.type == "not_found"


def test_resolve_error_messages_are_helpful(ctx):
    with pytest.raises(BridgeError) as info:
        lom.resolve("song.tracks[7]", ctx)
    assert info.value.message == "song.tracks[7]: index out of range (3 tracks)"
    with pytest.raises(BridgeError) as info:
        lom.resolve("song.trackz", ctx)
    assert "no attribute 'trackz' on Song" in info.value.message
    with pytest.raises(BridgeError) as info:
        lom.resolve("song.tempo[0]", ctx)
    assert "not indexable" in info.value.message


# --------------------------------------------------------------------------
# get / set
# --------------------------------------------------------------------------

def test_get_object_and_property(ctx, song):
    assert lom.get("song.tracks[1]", None, ctx) is song.tracks[1]
    assert lom.get("song.tracks[1]", "name", ctx) == "Vocals"
    assert lom.get("song", "tempo", ctx) == 124.0
    with pytest.raises(BridgeError) as info:
        lom.get("song.tracks[1]", "naem", ctx)
    assert info.value.type == "not_found"
    assert "did you mean" in info.value.message


def test_set_coerces_by_current_type(ctx, song):
    assert lom.set_property("song", "tempo", "128.5", ctx)["value"] == 128.5
    assert song.tempo == 128.5
    lom.set_property("song.tracks[0]", "mute", "true", ctx)
    assert song.tracks[0].mute is True
    lom.set_property("song.tracks[0]", "mute", 0, ctx)
    assert song.tracks[0].mute is False
    lom.set_property("song.tracks[0]", "name", "Sub Bass", ctx)
    assert song.tracks[0].name == "Sub Bass"
    lom.set_property("song", "signature_numerator", 3.0, ctx)
    assert song.signature_numerator == 3


def test_set_rejects_wrong_types(ctx):
    with pytest.raises(BridgeError) as info:
        lom.set_property("song", "tempo", "fast", ctx)
    assert info.value.type == "bad_args"
    with pytest.raises(BridgeError) as info:
        lom.set_property("song.tracks[0]", "mute", "maybe", ctx)
    assert info.value.type == "bad_args"
    with pytest.raises(BridgeError) as info:
        lom.set_property("song", "signature_numerator", 3.5, ctx)
    assert "whole number" in info.value.message


def test_set_rejects_collections_and_methods(ctx):
    with pytest.raises(BridgeError) as info:
        lom.set_property("song", "tracks", [], ctx)
    assert info.value.type == "bad_args"
    with pytest.raises(BridgeError) as info:
        lom.set_property("song", "start_playing", 1, ctx)
    assert "use lom.call" in info.value.message


def test_set_read_only_property_is_invalid_state(ctx):
    with pytest.raises(BridgeError) as info:
        lom.set_property("song.tracks[0].clip_slots[0]", "has_clip", True, ctx)
    assert info.value.type == "invalid_state"


def test_parameter_values_are_clamped(ctx, song):
    path = "song.tracks[0].devices[0].parameters[2]"   # Filter Freq 20..20000
    result = lom.set_property(path, "value", 999999, ctx)
    assert result["value"] == 20000.0
    assert result["display_value"] == "20000.00"
    assert lom.set_property(path, "value", -5, ctx)["value"] == 20.0
    assert lom.set_property(path, "value", "440", ctx)["value"] == 440.0


def test_quantized_parameters_accept_value_item_names(ctx, song):
    path = "song.tracks[0].devices[0].parameters[6]"   # Osc Wave
    parameter = ctx.resolve(path)
    assert parameter.is_quantized is True
    assert lom.set_property(path, "value", "Square", ctx)["value"] == 2.0
    assert lom.set_property(path, "value", 3, ctx)["display_value"] == "Noise"
    assert lom.set_property(path, "value", 1.4, ctx)["value"] == 1.0
    with pytest.raises(BridgeError) as info:
        lom.set_property(path, "value", "Triangle", ctx)
    assert info.value.type == "bad_args"
    assert "Sine" in info.value.message


def test_enum_properties_accept_names(ctx, song):
    clip = song.tracks[1].clip_slots[0].clip
    path = "song.tracks[1].clip_slots[0].clip"
    assert lom.set_property(path, "warp_mode", "repitch", ctx)["value"] == 3
    assert clip.warp_mode == 3
    assert lom.set_property(path, "warp_mode", 1, ctx)["value"] == 1


# --------------------------------------------------------------------------
# call
# --------------------------------------------------------------------------

def test_call_methods(ctx, song):
    lom.call("song", "start_playing", None, None, ctx)
    assert song.is_playing is True
    lom.call("song.tracks[0].clip_slots[3]", "create_clip", [2.0], None, ctx)
    assert song.tracks[0].clip_slots[3].has_clip is True
    result = lom.call("song.tracks[0].devices[0].parameters[0]", "str_for_value",
                      [0.5], None, ctx)
    assert isinstance(result, str)


def test_call_errors(ctx):
    with pytest.raises(BridgeError) as info:
        lom.call("song", "not_a_method", None, None, ctx)
    assert info.value.type == "not_found"
    with pytest.raises(BridgeError) as info:
        lom.call("song", "tempo", None, None, ctx)
    assert info.value.type == "bad_args"
    with pytest.raises(BridgeError) as info:
        lom.call("song", "delete_track", [99], None, ctx)
    assert info.value.type == "invalid_state"
    with pytest.raises(BridgeError) as info:
        lom.call("song", "start_playing", [1, 2, 3], None, ctx)
    assert info.value.type == "bad_args"


def test_call_on_a_full_clip_slot_is_invalid_state(ctx):
    with pytest.raises(BridgeError) as info:
        lom.call("song.tracks[0].clip_slots[0]", "create_clip", [4.0], None, ctx)
    assert info.value.type == "invalid_state"


# --------------------------------------------------------------------------
# describe / children
# --------------------------------------------------------------------------

def test_describe_a_track(ctx):
    described = lom.describe("song.tracks[0]", ctx)
    assert described["type"] == "Track"
    assert described["path"] == "song.tracks[0]"
    names = [p["name"] for p in described["properties"]]
    assert "name" in names and "mute" in names
    assert not any(n.endswith("_listener") for n in names)
    assert not any(n.startswith("_") for n in names)
    children = dict((c["name"], c["count"]) for c in described["children"])
    assert children["devices"] == 2
    assert children["clip_slots"] == 4
    assert any(m.startswith("stop_all_clips(") for m in described["methods"])


def test_describe_skips_listeners_on_every_type(ctx):
    for path in ("song", "song.tracks[0].devices[0]",
                 "song.tracks[0].devices[0].parameters[0]",
                 "song.tracks[0].clip_slots[0].clip", "song.scenes[0]",
                 "browser.instruments", "app"):
        described = lom.describe(path, ctx)
        for entry in described["properties"] + described["children"]:
            assert not entry["name"].endswith("_listener")
        assert not any(m.startswith("add_") and "_listener" in m
                       for m in described["methods"])


def test_describe_marks_writability(ctx):
    # Boost.Python exposes LOM properties as real ``property`` objects, so the
    # writability introspection works in Live exactly like on the stub.
    described = lom.describe("song.tracks[0].devices[0].parameters[2]", ctx)
    entries = dict((p["name"], p) for p in described["properties"])
    assert entries["value"]["writable"] is True
    assert entries["min"]["writable"] is False
    assert entries["name"]["writable"] is False
    assert entries["is_quantized"]["value"] is False
    # value_items raises on a non-quantized parameter: it is simply omitted
    assert "value_items" not in entries


def test_describe_empty_slot_is_not_found(ctx):
    with pytest.raises(BridgeError) as info:
        lom.describe("song.tracks[0].clip_slots[3].clip", ctx)
    assert info.value.type == "not_found"


def test_children_of_a_collection_and_of_an_object(ctx):
    kind, items = lom.children("song.tracks", ctx)
    assert kind == "items"
    assert len(items) == 3
    kind, collections = lom.children("song", ctx)
    assert kind == "collections"
    names = dict((c["name"], c) for c in collections)
    assert names["tracks"]["count"] == 3
    assert names["scenes"]["path"] == "song.scenes"
    assert "cue_points" in names


def test_children_of_a_browser_folder(ctx):
    kind, items = lom.children("browser.instruments.children", ctx)
    assert kind == "items"
    assert any(item.name == "Operator" for item in items)


# --------------------------------------------------------------------------
# path_of
# --------------------------------------------------------------------------

def test_path_of_every_important_type(ctx, song):
    assert lom.path_of(song, ctx) == "song"
    assert lom.path_of(ctx.app, ctx) == "app"
    assert lom.path_of(ctx.browser, ctx) == "browser"
    assert lom.path_of(song.tracks[2], ctx) == "song.tracks[2]"
    assert lom.path_of(song.return_tracks[1], ctx) == "song.return_tracks[1]"
    assert lom.path_of(song.master_track, ctx) == "song.master_track"
    assert lom.path_of(song.scenes[3], ctx) == "song.scenes[3]"
    assert lom.path_of(song.cue_points[1], ctx) == "song.cue_points[1]"
    assert lom.path_of(song.tracks[0].clip_slots[1], ctx) == "song.tracks[0].clip_slots[1]"
    assert lom.path_of(song.tracks[0].clip_slots[1].clip, ctx) == \
        "song.tracks[0].clip_slots[1].clip"
    assert lom.path_of(song.tracks[0].devices[0], ctx) == "song.tracks[0].devices[0]"
    assert lom.path_of(song.tracks[0].devices[0].parameters[2], ctx) == \
        "song.tracks[0].devices[0].parameters[2]"
    assert lom.path_of(song.tracks[0].mixer_device, ctx) == "song.tracks[0].mixer_device"
    assert lom.path_of(song.tracks[0].mixer_device.volume, ctx) == \
        "song.tracks[0].mixer_device.volume"
    assert lom.path_of(song.tracks[0].devices[1].chains[1], ctx) == \
        "song.tracks[0].devices[1].chains[1]"
    assert lom.path_of(song.tracks[2].devices[0].drum_pads[36], ctx) == \
        "song.tracks[2].devices[0].drum_pads[36]"
    assert lom.path_of(None, ctx) is None


def test_path_of_survives_a_detached_object(ctx):
    class Detached(object):
        name = "nowhere"

    assert lom.path_of(Detached(), ctx) is None


def test_path_round_trips_through_resolve(ctx, song):
    for obj in (song.tracks[1], song.scenes[2],
                song.tracks[0].devices[0].parameters[1],
                song.tracks[0].clip_slots[0].clip):
        path = lom.path_of(obj, ctx)
        assert lom.resolve(path, ctx) is obj


# --------------------------------------------------------------------------
# through the dispatcher (the handlers)
# --------------------------------------------------------------------------

def test_lom_handlers_end_to_end(bridge, song):
    response = bridge.dispatch({"id": "1", "cmd": "lom.get",
                                "args": {"path": "song.tracks[0]", "detail": "full"}})
    assert response["result"]["name"] == "Bass"
    assert response["result"]["clips"][0]["name"] == "Bass Loop"

    response = bridge.dispatch({"id": "2", "cmd": "lom.get",
                                "args": {"path": "song", "prop": "tempo"}})
    assert response["result"] == 124.0

    response = bridge.dispatch({"id": "3", "cmd": "lom.set",
                                "args": {"path": "song.tracks[0]", "prop": "name",
                                         "value": "Renamed"}})
    assert response["result"]["value"] == "Renamed"

    response = bridge.dispatch({"id": "4", "cmd": "lom.call",
                                "args": {"path": "song.tracks[0].clip_slots[0]",
                                         "method": "fire"}})
    assert response["ok"] is True
    assert song.tracks[0].playing_slot_index == 0

    response = bridge.dispatch({"id": "5", "cmd": "lom.describe",
                                "args": {"path": "song.scenes[0]"}})
    assert response["result"]["type"] == "Scene"

    response = bridge.dispatch({"id": "6", "cmd": "lom.children",
                                "args": {"path": "song.tracks", "detail": "minimal"}})
    assert response["result"]["count"] == 3
    assert response["result"]["items"][0]["path"] == "song.tracks[0]"


def test_lom_children_paging(bridge):
    response = bridge.dispatch({"id": "1", "cmd": "lom.children",
                                "args": {"path": "song.tracks", "offset": 1,
                                         "limit": 1}})
    result = response["result"]
    assert (result["total"], result["offset"], result["count"]) == (3, 1, 1)
    assert result["next_offset"] == 2
    assert len(result["items"]) == 1
    assert response["result"]["items"][0]["name"] == "Vocals"


def test_lom_call_resolves_path_arguments(bridge, song):
    response = bridge.dispatch({
        "id": "1", "cmd": "lom.call",
        "args": {"path": "song.view", "method": "select_device",
                 "args": ["song.tracks[0].devices[0]"]}})
    assert response["ok"] is True
    # Song.View has no selected_device — the selection lives on the track view
    assert song.tracks[0].view.selected_device is song.tracks[0].devices[0]


def test_lom_get_of_browser_root(bridge):
    response = bridge.dispatch({"id": "1", "cmd": "lom.get",
                                "args": {"path": "browser.instruments",
                                         "detail": "full"}})
    result = response["result"]
    assert result["is_folder"] is True
    assert any(child["name"] == "Operator" for child in result["children"])


def test_properties_live_refuses_to_read_are_invalid_state(ctx, song):
    """Live raises when a property does not apply (arm on a return track,
    warping on a MIDI clip): that is invalid_state, not not_found."""
    with pytest.raises(BridgeError) as info:
        lom.get("song.return_tracks[0]", "arm", ctx)
    assert info.value.type == "invalid_state"
    # Real Live 12.4.5 raises RuntimeError("Main and Return Tracks have no 'Arm' state!")
    assert "no 'Arm' state" in info.value.message
    with pytest.raises(BridgeError) as info:
        lom.set_property("song.tracks[0].clip_slots[0].clip", "warping", True, ctx)
    assert info.value.type == "invalid_state"
    with pytest.raises(BridgeError) as info:
        lom.get("song.tracks[0]", "no_such_thing", ctx)
    assert info.value.type == "not_found"


def test_enum_properties_use_the_verified_live_enums(ctx, song):
    clip_path = "song.tracks[0].clip_slots[0].clip"
    assert lom.set_property(clip_path, "launch_quantization", "q_sixteenth",
                            ctx)["value"] == 12          # Clip.ClipLaunchQuantization
    assert lom.set_property("song.tracks[0]", "current_monitoring_state", "off",
                            ctx)["value"] == 2           # Track.monitoring_states
    assert lom.set_property("song.tracks[0].mixer_device", "crossfade_assign", "B",
                            ctx)["value"] == 2
    assert lom.set_property("song", "midi_recording_quantization", "rec_q_sixtenth",
                            ctx)["value"] == 5


def test_set_object_valued_properties_takes_a_path(bridge, song):
    """lom.set used to hand Live the path *string* for properties that hold a LOM
    object (selected_track, detail_clip, hotswap_target, routing types), so it always
    failed although lom.call resolves path arguments."""
    response = bridge.dispatch({"id": "1", "cmd": "lom.set",
                                "args": {"path": "song.view", "prop": "selected_track",
                                         "value": "song.tracks[2]"}})
    assert response["ok"] is True, response
    assert song.view.selected_track == song.tracks[2]
    assert response["result"]["value"]["path"] == "song.tracks[2]"
    assert response["result"]["value"]["name"] == "Drums"

    response = bridge.dispatch({"id": "2", "cmd": "lom.set",
                                "args": {"path": "song.view", "prop": "detail_clip",
                                         "value": "song.tracks[0].clip_slots[1].clip"}})
    assert response["ok"] is True, response
    assert song.view.detail_clip == song.tracks[0].clip_slots[1].clip

    track = song.tracks[1]
    choices = list(track.available_input_routing_types)
    assert len(choices) > 1
    response = bridge.dispatch({"id": "3", "cmd": "lom.set",
                                "args": {"path": "song.tracks[1]",
                                         "prop": "input_routing_type",
                                         "value": "song.tracks[1].available_input_routing_types[1]"}})
    assert response["ok"] is True, response
    assert track.input_routing_type == choices[1]

    response = bridge.dispatch({"id": "4", "cmd": "lom.set",
                                "args": {"path": "song.view", "prop": "selected_track",
                                         "value": "song.tracks[42]"}})
    assert response["error"]["type"] == "not_found"


def test_looks_like_path():
    for text in ("song", "song.tracks[0]", "app.view", "browser.instruments",
                 "live_set.tracks[1]", " song.view "):
        assert lom.looks_like_path(text), text
    for text in ("songbird", "Song", "tracks[0]", "", "my song.tracks", 3, None):
        assert not lom.looks_like_path(text), text
