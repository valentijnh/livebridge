"""Serializer tests: one per type from ``docs/ARCHITECTURE.md`` §6."""

import json

import pytest

from live_stub import factory
from LiveBridge import serialize


@pytest.fixture
def ctx(bridge):
    return bridge.ctx


def _json_safe(value):
    """A summary must survive json.dumps without a custom encoder."""
    json.dumps(value)
    return value


# --------------------------------------------------------------------------
# scalars and type detection
# --------------------------------------------------------------------------

def test_scalar_handles_everything():
    assert serialize.scalar(None) is None
    assert serialize.scalar(True) is True
    assert serialize.scalar(3) == 3
    assert serialize.scalar(1.23456789) == 1.234568
    assert serialize.scalar("text") == "text"
    assert serialize.scalar(float("inf")) is None
    assert serialize.scalar([1, 2, 3]) == [1, 2, 3]
    assert serialize.scalar({"a": 1}) == {"a": 1}


def test_scalar_of_an_enum_uses_its_name():
    import Live
    assert serialize.scalar(Live.Device.DeviceType.instrument) == "instrument"


def test_scalar_of_a_lom_object_is_a_marker(song):
    marker = serialize.scalar(song.tracks[0])
    assert marker.startswith("<Track ")
    assert "Bass" in marker


def test_kind_of_recognises_every_type(song, browser, app):
    track = song.tracks[0]
    assert serialize.kind_of(song) == "song"
    assert serialize.kind_of(track) == "track"
    assert serialize.kind_of(track.clip_slots[0]) == "clip_slot"
    assert serialize.kind_of(track.clip_slots[0].clip) == "clip"
    assert serialize.kind_of(track.devices[0]) == "device"
    assert serialize.kind_of(track.devices[0].parameters[0]) == "parameter"
    assert serialize.kind_of(track.devices[1].chains[0]) == "chain"
    assert serialize.kind_of(track.mixer_device) == "mixer_device"
    assert serialize.kind_of(song.scenes[0]) == "scene"
    assert serialize.kind_of(song.cue_points[0]) == "cue_point"
    assert serialize.kind_of(song.tracks[2].devices[0].drum_pads[36]) == "drum_pad"
    assert serialize.kind_of(browser.instruments) == "browser_item"
    assert serialize.kind_of(browser) == "browser"
    assert serialize.kind_of(app) == "application"
    assert serialize.kind_of(3) is None
    assert serialize.kind_of(None) is None


# --------------------------------------------------------------------------
# per type
# --------------------------------------------------------------------------

def test_song_summary(ctx, song):
    data = _json_safe(ctx.summarize(song))
    assert data["kind"] == "song"
    assert data["path"] == "song"
    assert data["tempo"] == 124.0
    assert data["signature"] == "4/4"
    assert data["track_count"] == 3
    assert data["return_count"] == 2
    assert data["scene_count"] == 4
    assert data["live_version"] == "12.4.5"
    assert data["selected_track"]["name"] == "Bass"
    assert data["cue_point_count"] == 2


def test_song_full_includes_the_whole_set(ctx, song):
    data = _json_safe(ctx.summarize(song, "full"))
    assert len(data["tracks"]) == 3
    assert len(data["return_tracks"]) == 2
    assert len(data["scenes"]) == 4
    assert data["master_track"]["type"] == "master"


def test_song_minimal_is_small(ctx, song):
    data = ctx.summarize(song, "minimal")
    assert "metronome" not in data
    assert data["track_count"] == 3


def test_track_summary(ctx, song):
    data = _json_safe(ctx.summarize(song.tracks[0]))
    assert data["kind"] == "track"
    assert data["path"] == "song.tracks[0]"
    assert data["index"] == 0
    assert data["name"] == "Bass"
    assert data["type"] == "midi"
    assert data["mute"] is False
    assert data["has_midi_input"] is True
    assert data["monitoring"] == "auto"
    assert data["device_count"] == 2
    assert data["devices"][0] == {"path": "song.tracks[0].devices[0]", "index": 0,
                                  "name": "Operator", "class_name": "Operator",
                                  "type": "instrument", "is_active": True,
                                  "kind": "device"}
    assert data["volume"] == 0.85
    assert data["sends"] == [0.0, 0.0]
    assert data["input_routing"] == "All Ins"          # MIDI track input
    assert data["clip_slot_count"] == 4
    assert "clips" not in data


def test_track_types(ctx, song):
    assert ctx.summarize(song.tracks[1])["type"] == "audio"
    assert ctx.summarize(song.return_tracks[0])["type"] == "return"
    assert ctx.summarize(song.master_track)["type"] == "master"
    assert ctx.summarize(song.return_tracks[1])["path"] == "song.return_tracks[1]"


def test_track_full_lists_only_non_empty_slots(ctx, song):
    data = _json_safe(ctx.summarize(song.tracks[0], "full"))
    assert [clip["index"] for clip in data["clips"]] == [0, 1]
    assert data["clips"][0]["name"] == "Bass Loop"
    assert data["arrangement_clip_count"] == 1
    assert data["devices"][0]["parameter_count"] == 7   # incl. "Device On"


def test_clip_slot_summary(ctx, song):
    filled = _json_safe(ctx.summarize(song.tracks[0].clip_slots[0]))
    assert filled["kind"] == "clip_slot"
    assert filled["has_clip"] is True
    assert filled["clip"]["name"] == "Bass Loop"
    empty = ctx.summarize(song.tracks[0].clip_slots[3])
    assert empty["has_clip"] is False
    assert "clip" not in empty


def test_midi_clip_summary(ctx, song):
    data = _json_safe(ctx.summarize(song.tracks[0].clip_slots[0].clip))
    assert data["kind"] == "clip"
    assert data["path"] == "song.tracks[0].clip_slots[0].clip"
    assert data["name"] == "Bass Loop"
    assert data["is_midi"] is True
    assert data["is_audio"] is False
    assert data["length"] == 4.0
    assert data["loop_end"] == 4.0
    assert data["signature"] == "4/4"
    assert data["note_count"] == 4
    assert "warp_mode" not in data


def test_audio_clip_summary(ctx, song):
    data = _json_safe(ctx.summarize(song.tracks[1].clip_slots[0].clip))
    assert data["is_audio"] is True
    assert data["warping"] is True
    assert data["warp_mode"] == "beats"
    assert data["file_path"] == "/Samples/vox.wav"
    assert data["gain_display"] == "0.0 dB"
    assert "note_count" not in data


def test_arrangement_clip_has_times(ctx, song):
    data = ctx.summarize(song.tracks[0].arrangement_clips[0])
    assert data["is_arrangement_clip"] is True
    assert data["start_time"] == 0.0
    assert data["end_time"] == 4.0


def test_device_summary(ctx, song):
    data = _json_safe(ctx.summarize(song.tracks[0].devices[0]))
    assert data["kind"] == "device"
    assert data["name"] == "Operator"
    assert data["class_name"] == "Operator"
    assert data["type"] == "instrument"
    assert data["parameter_count"] == 7
    assert data["is_plugin"] is False
    assert data["can_have_chains"] is False
    assert "parameters" not in data


def test_rack_and_drum_rack_summary(ctx, song):
    rack = _json_safe(ctx.summarize(song.tracks[0].devices[1]))
    assert rack["can_have_chains"] is True
    assert rack["chain_count"] == 2
    drum_rack = _json_safe(ctx.summarize(song.tracks[2].devices[0], "full"))
    assert drum_rack["can_have_drum_pads"] is True
    assert drum_rack["drum_pad_count"] == 3
    assert len(drum_rack["drum_pads"]) == 3
    assert drum_rack["drum_pads"][0]["note"] == 36


def test_device_full_includes_parameters(ctx, song):
    data = _json_safe(ctx.summarize(song.tracks[0].devices[0], "full"))
    names = [p["name"] for p in data["parameters"]]
    assert names[:3] == ["Device On", "Volume", "Filter Freq"]
    quantized = data["parameters"][6]
    assert quantized["is_quantized"] is True
    assert quantized["value_items"] == ["Sine", "Saw", "Square", "Noise"]


def test_plugin_and_simpler_devices(ctx, song):
    plugin = factory.add_plugin(song.tracks[0], "Serum", params=[("Macro 1", 0.5)])
    data = _json_safe(ctx.summarize(plugin))
    assert data["is_plugin"] is True
    assert data["preset_count"] == 2
    simpler = song.tracks[2].devices[0].drum_pads[36].chains[0].devices[0]
    data = _json_safe(ctx.summarize(simpler))
    assert data["sample"]["file_path"] == "/Samples/Drums/Kick.wav"
    assert data["playback_mode"] == "classic"
    empty = factory.add_simpler(song.tracks[0], "Empty Simpler")
    assert "sample" not in _json_safe(ctx.summarize(empty))   # sample is None


def test_parameter_summary(ctx, song):
    parameter = song.tracks[0].devices[0].parameters[2]
    data = _json_safe(ctx.summarize(parameter))
    assert data["kind"] == "parameter"
    assert data["path"] == "song.tracks[0].devices[0].parameters[2]"
    assert data["index"] == 2
    assert data["name"] == "Filter Freq"
    assert data["value"] == 800.0
    assert data["min"] == 20.0
    assert data["max"] == 20000.0
    assert data["is_quantized"] is False
    assert data["display_value"] == "800.00"
    assert data["automation_state"] == "none"
    assert "value_items" not in data


def test_quantized_parameter_summary(ctx, song):
    data = ctx.summarize(song.tracks[0].devices[0].parameters[6])
    assert data["value_items"] == ["Sine", "Saw", "Square", "Noise"]
    assert data["display_value"] == "Saw"
    assert "default_value" not in data   # Live raises for quantized parameters
    device_on = ctx.summarize(song.tracks[0].devices[0].parameters[0])
    assert device_on["name"] == "Device On"
    assert device_on["value_items"] == ["Off", "On"]


def test_mixer_device_summary(ctx, song):
    data = _json_safe(ctx.summarize(song.tracks[0].mixer_device))
    assert data["kind"] == "mixer_device"
    assert data["path"] == "song.tracks[0].mixer_device"
    assert data["volume"] == 0.85
    assert data["panning"] == 0.0
    assert data["sends"] == [0.0, 0.0]
    assert data["crossfade_assign"] == "none"     # a plain int in Live, named here
    assert data["panning_mode"] == "stereo"
    assert "cue_volume" not in data               # master track only
    full = ctx.summarize(song.tracks[0].mixer_device, "full")
    assert full["volume"]["name"] == "Track Volume"
    # Real Live 12.4.5 names a send parameter after its return track ("A-Reverb"), not "Send A"
    assert full["sends"][0]["name"] == song.return_tracks[0].name
    master = ctx.summarize(song.master_track.mixer_device)
    # Real Live 12.4.5: the master's cue ("Preview Volume") sits at ~0.70 in a new set (default_value 0.85)
    assert master["cue_volume"] == pytest.approx(0.7, abs=1e-3)
    assert "crossfade_assign" not in master


def test_scene_summary(ctx, song):
    data = _json_safe(ctx.summarize(song.scenes[2]))
    assert data["kind"] == "scene"
    assert data["path"] == "song.scenes[2]"
    assert data["index"] == 2
    assert data["name"] == "Chorus"
    assert data["is_triggered"] is False
    assert data["is_empty"] is True
    song.scenes[0].tempo_enabled = True
    song.scenes[0].tempo = 90.0
    assert ctx.summarize(song.scenes[0])["tempo"] == 90.0


def test_cue_point_summary(ctx, song):
    data = _json_safe(ctx.summarize(song.cue_points[1]))
    assert data == {"kind": "cue_point", "path": "song.cue_points[1]", "index": 1,
                    "name": "Drop", "time": 32.0}


def test_chain_summary(ctx, song):
    data = _json_safe(ctx.summarize(song.tracks[0].devices[1].chains[0]))
    assert data["kind"] == "chain"
    assert data["name"] == "Chain 1"
    assert data["device_count"] == 0


def test_drum_pad_summary(ctx, song):
    pad = song.tracks[2].devices[0].drum_pads[38]
    data = _json_safe(ctx.summarize(pad))
    assert data["kind"] == "drum_pad"
    assert data["note"] == 38
    assert data["name"] == "Snare"
    assert data["chain_count"] == 1


def test_browser_item_summary(ctx, browser):
    item = browser.instruments.children[0]
    data = _json_safe(ctx.summarize(item))
    assert data["kind"] == "browser_item"
    assert data["name"] == "Operator"
    assert data["is_device"] is True
    assert data["is_loadable"] is True
    assert data["uri"].startswith("query:")
    full = ctx.summarize(browser.instruments, "full")
    assert full["child_count"] == 5
    assert full["children"][0]["name"] == "Operator"


def test_browser_summary(ctx, browser):
    data = _json_safe(ctx.summarize(browser))
    names = [root["name"] for root in data["roots"]]
    for expected in ("instruments", "audio_effects", "plugins", "samples",
                     "user_library"):
        assert expected in names
    instruments = [r for r in data["roots"] if r["name"] == "instruments"][0]
    assert instruments["child_count"] == 5
    assert instruments["path"] == "browser.instruments"


def test_application_summary(ctx, app):
    data = _json_safe(ctx.summarize(app))
    assert data["version"] == "12.4.5"
    assert data["focused_document_view"] == "Session"


# --------------------------------------------------------------------------
# robustness
# --------------------------------------------------------------------------

def test_summarize_a_list_and_a_dict(ctx, song):
    data = ctx.summarize(song.tracks, "minimal")
    assert [entry["name"] for entry in data] == ["Bass", "Vocals", "Drums"]
    assert ctx.summarize({"a": song.scenes[0]}, "minimal")["a"]["name"] == "Intro"


def test_summarize_never_raises_on_a_hostile_object(ctx):
    class Hostile(object):
        @property
        def name(self):
            raise RuntimeError("nope")

        @property
        def tracks(self):
            raise RuntimeError("nope")

    assert isinstance(ctx.summarize(Hostile()), str)


def test_summary_of_a_whole_set_stays_small(ctx, song):
    payload = json.dumps(ctx.summarize(song, "full"))
    assert len(payload) < 12000, "a full set summary must stay in a few KB"


def test_detail_level_is_forgiving(ctx, song):
    assert ctx.summarize(song.tracks[0], "nonsense") == ctx.summarize(song.tracks[0])
    assert ctx.summarize(song.tracks[0], None) == ctx.summarize(song.tracks[0])


def test_take_lanes_and_vectors_are_summarised(ctx, song):
    """Live 12 take lanes have their own summary; Vector collections summarise as lists."""
    lane = song.tracks[0].create_take_lane()
    lane.create_midi_clip(0.0, 4.0)
    assert serialize.kind_of(lane) == "take_lane"
    data = serialize.summarize(lane, "full", ctx)
    assert data["path"] == "song.tracks[0].take_lanes[0]" and data["clip_count"] == 1
    assert data["clips"][0]["path"] == "song.tracks[0].take_lanes[0].arrangement_clips[0]"
    lanes = serialize.summarize(song.tracks[0].take_lanes, "minimal", ctx)
    assert isinstance(lanes, list) and lanes[0]["kind"] == "take_lane"
    scalars = serialize.scalar(song.tracks[0].take_lanes)
    assert isinstance(scalars, list) and len(scalars) == 1
