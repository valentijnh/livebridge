"""Cross-module consistency (integration phase).

* every MCP tool runs end-to-end against the stub-backed Remote Script without an
  argument-name mismatch, an unknown command or an internal error;
* every bridge command has a tool (or a documented composite) and ``docs/TOOLS.md`` is
  generated from the current code (``installers/gen_tools_doc.py``);
* the shared resolvers (``LiveBridge/resolve.py``) behave the same in every module:
  track forms, colours, time signatures, ``bars.beats.sixteenths`` and paging shapes.
"""

from __future__ import annotations

import asyncio
import json
import os
import struct
import sys
import wave

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(TESTS_DIR)
for _path in (os.path.join(REPO, "mcp_server"), os.path.join(REPO, "installers")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from livebridge_mcp.client import BridgeClient  # noqa: E402
from livebridge_mcp.server import create_app  # noqa: E402

from LiveBridge import resolve  # noqa: E402
from LiveBridge.registry import BridgeError  # noqa: E402

import gen_tools_doc  # noqa: E402

from live_stub import factory  # noqa: E402


# --------------------------------------------------------------------------- helpers

def call_tool(app, name, args=None):
    result = asyncio.run(app.call_tool(name, args or {}))
    content = getattr(result, "content", None)
    if content is None:
        content = result[0] if isinstance(result, tuple) else result
    blocks = [b.text for b in content if getattr(b, "type", None) == "text"]

    def parse(text):
        try:
            return json.loads(text)
        except ValueError:
            return text

    if len(blocks) > 1:
        return [parse(b) for b in blocks]
    return parse("\n".join(blocks))


def run(bridge, cmd, **args):
    response = bridge.dispatch({"id": "c", "cmd": cmd, "args": args})
    assert response["ok"], response
    return response["result"]


def fail(bridge, cmd, **args):
    response = bridge.dispatch({"id": "c", "cmd": cmd, "args": args})
    assert not response["ok"], response
    return response["error"]


def write_wav(path, seconds=0.25, rate=22050):
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(struct.pack("<h", 0) * int(rate * seconds))
    return str(path)


#: One MCP app for the whole module; each test points its client at that test's bridge.
_APP = create_app(BridgeClient(host="127.0.0.1", port=9, timeout=10.0))
TOOL_NAMES = sorted(t.name for t in asyncio.run(_APP.list_tools()))

#: Arguments for tools that need some (everything else is called with no arguments).
SAMPLE_ARGS = {
    "live_arrangement_create_clip": {"track": "Bass", "start": 16},
    "live_arrangement_duplicate_clip": {"time": 32, "track": "Bass", "slot": 0},
    "live_arrangement_move_clip": {"start": 48, "track": "Bass", "index": 0},
    "live_arrangement_delete_clip": {"track": "Bass", "index": 0},
    "live_arrangement_from_scenes": {"sections": [0, {"scene": 1, "bars": 2}]},
    "live_arrangement_copy_range": {"start": 0, "end": 8, "destination": 64},
    "live_arrangement_resize_clip": {"track": "Bass", "index": 0, "length": 8},
    "live_audio_analyze": {"track": "Vocals", "slot": 0},
    "live_automation_overview": {"track": "Bass"},
    "live_automation_get": {"parameter": "Volume", "track": "Bass", "slot": 0},
    "live_automation_write": {"parameter": "Volume", "track": "Bass", "slot": 0,
                              "points": [{"time": 0, "value": 0.5}, {"time": 2, "value": 0.8}]},
    "live_automation_shape": {"parameter": "Volume", "track": "Bass", "slot": 0},
    "live_automation_list": {"track": "Bass", "slot": 0},
    "live_automation_clear": {"track": "Bass", "slot": 0, "all": True},
    "live_automation_copy": {"parameter": "Volume", "track": "Bass", "slot": 0, "targets": [1]},
    "live_automation_record": {"parameter": "Volume", "track": "Bass",
                               "points": [[0, 0.5], [4, 0.8]]},
    "live_browser_search": {"query": "reverb"},
    "live_browser_load": {"query": "Reverb", "track": "Vocals"},
    "live_browser_preview": {"stop": True},
    "live_clip_create": {"track": "Bass", "slot": 3},
    "live_clip_get": {"track": "Bass", "slot": 0},
    "live_clip_set": {"track": "Bass", "slot": 0, "name": "Renamed", "color": "red"},
    "live_clip_fire": {"track": "Bass", "slot": 0},
    "live_clip_stop": {"track": "Bass", "slot": 0},
    "live_clip_delete": {"track": "Bass", "slot": 1},
    "live_clip_duplicate": {"track": "Bass", "slot": 0},
    "live_clip_edit": {"action": "crop", "track": "Bass", "slot": 0},
    "live_clip_get_notes": {"track": "Bass", "slot": 0},
    "live_clip_add_notes": {"track": "Bass", "slot": 0,
                            "notes": [{"pitch": "C3", "start": 0, "duration": 1}]},
    "live_clip_remove_notes": {"track": "Bass", "slot": 0, "pitch": 60},
    "live_clip_modify_notes": {"track": "Bass", "slot": 0,
                               "changes": [{"note_id": 1, "velocity": 90}]},
    "live_clip_transform_notes": {"track": "Bass", "slot": 0, "transpose": 1},
    "live_clip_write_pattern": {"track": "Drums", "slot": 1, "pattern": {"kick": "x...x..."}},
    "live_clip_write_chords": {"track": "Bass", "slot": 2, "chords": ["C", "Am"]},
    "live_clip_write_arp": {"track": "Bass", "slot": 2, "chords": "Am F C G"},
    "live_clip_convert": {"to": "simpler", "track": "Vocals", "slot": 0},
    "live_clip_duplicate_notes": {"track": "Bass", "slot": 0, "destination": 4},
    "live_clip_select_notes": {"track": "Bass", "slot": 0, "all": True},
    "live_clip_warp": {"track": "Vocals", "slot": 0},
    "live_cue_set": {"cue": 0, "name": "Start"},
    "live_cue_loop": {"start": 0},
    "live_cue_add": {"time": 64, "name": "Outro"},
    "live_cue_delete": {"cue": 0},
    "live_cue_jump": {"direction": "next"},
    "live_cue_layout": {"cues": [{"name": "Verse", "bar": 5}, {"name": "Outro", "bar": 20}]},
    "live_time_convert": {"beats": 8},
    "live_device_list": {"track": "Bass"},
    "live_device_get": {"track": "Bass", "device": "Operator"},
    "live_device_parameters": {"track": "Bass", "device": "Operator"},
    "live_device_get_parameter": {"parameter": "Filter Freq", "track": "Bass",
                                  "device": "Operator"},
    "live_device_set_parameter": {"parameter": "Filter Freq", "value": 1000, "track": "Bass",
                                  "device": "Operator"},
    "live_device_set_parameters": {"values": {"Filter Freq": 1000}, "track": "Bass",
                                   "device": "Operator"},
    "live_device_reset_parameters": {"track": "Bass", "device": "Operator"},
    "live_device_randomize": {"track": "Bass", "device": "Operator", "seed": 1},
    "live_device_set_state": {"track": "Bass", "device": "Operator", "collapsed": True},
    "live_device_action": {"action": "stop", "track": "Bass", "device": "Operator"},
    "live_device_properties": {"track": "Bass", "device": "Operator"},
    "live_device_modulation": {"track": "Bass", "device": "Operator"},
    "live_device_delete": {"device": "Reverb", "track": "Vocals"},
    "live_device_insert": {"name": "Reverb", "track": "Vocals"},
    "live_device_duplicate": {"track": "Vocals", "device": "Reverb"},
    "live_device_move": {"device": "Reverb", "track": "Vocals", "target_track": "Bass"},
    "live_simpler_get": {"track": "Drums",
                         "device": "song.tracks[2].devices[0].drum_pads[36].chains[0].devices[0]"},
    "live_simpler_set": {"track": "Drums", "gain": 0.5,
                         "device": "song.tracks[2].devices[0].drum_pads[36].chains[0].devices[0]"},
    "live_simpler_action": {"action": "reverse", "track": "Drums",
                            "device": "song.tracks[2].devices[0].drum_pads[36].chains[0]"
                                      ".devices[0]"},
    "live_plugin_get": {"track": "Bass", "device": "Serum 2"},
    "live_plugin_presets": {"track": "Bass", "device": "Serum 2"},
    "live_plugin_parameters": {"track": "Bass", "device": "Serum 2"},
    "live_plugin_set": {"track": "Bass", "device": "Serum 2", "preset": 0},
    "live_plugin_configure": {"track": "Bass", "device": "Serum 2",
                              "parameters": ["Filter 1 Freq"]},
    "live_plugin_param_map": {"plugin": "Serum 2", "limit": 5},
    "live_plugin_preset_files": {"plugin": "Serum 2", "limit": 5},
    "live_rack_chains": {"track": "Bass", "device": 1},
    "live_rack_set_chain": {"chain": 0, "track": "Bass", "device": 1, "mute": True},
    "live_rack_insert_chain": {"track": "Bass", "device": 1},
    "live_rack_macros": {"track": "Bass", "device": 1},
    "live_rack_variations": {"track": "Bass", "device": 1},
    "live_drumrack_overview": {"track": "Drums"},
    "live_drumrack_set_pad": {"note": 36, "track": "Drums", "mute": True},
    "live_drumrack_convert": {"action": "pad_to_track", "track": "Drums", "note": 36},
    "live_lom_get": {"path": "song", "prop": "tempo"},
    "live_lom_set": {"path": "song", "prop": "tempo", "value": 120},
    "live_lom_call": {"path": "song", "method": "stop_playing"},
    "live_lom_describe": {"path": "song.tracks[0]"},
    "live_lom_children": {"path": "song.tracks"},
    "live_eval_python": {"expr": "song.tempo"},
    "live_mixer_set": {"track": "Bass", "volume": "-6 dB"},
    "live_mixer_set_many": {"settings": [{"track": "Bass", "volume": 0.5}]},
    "live_mixer_master": {"volume": "0 dB"},
    "live_record_arm": {"track": "Bass"},
    "live_record_settings": {"metronome": True},
    "live_record_resample": {"start": 0, "bars": 1},
    "live_routing_get": {"track": "Bass"},
    "live_routing_set": {"track": "Bass", "monitoring": "auto"},
    "live_routing_route": {"source": "Bass", "destination": "Vocals"},
    "live_scene_get": {"scene": 1},
    "live_scene_delete": {"scene": 3},
    "live_scene_duplicate": {"scene": 1},
    "live_scene_set": {"scene": 1, "name": "Verse 2"},
    "live_scene_fire": {"scene": 0},
    "live_command_call": {"cmd": "system.ping"},
    "live_command_batch": {"commands": [{"cmd": "system.ping"},
                                        {"cmd": "tracks.get", "args": {"track": "Bass"}}]},
    "live_dialog_press": {"button": 0, "expect": "Save"},
    "live_log": {"message": "consistency test"},
    "live_theory_analyze": {"clips": [{"track": "Bass", "slot": 0},
                                      {"track": "Drums", "slot": 0}]},
    "live_tracks_get": {"track": "Bass"},
    "live_tracks_find": {"name": "Ba"},
    "live_tracks_delete": {"track": "Vocals"},
    "live_tracks_duplicate": {"track": "Bass"},
    "live_tracks_set": {"track": "Bass", "mute": True},
    "live_tracks_group": {"track": "Bass"},
    "live_tracks_select": {"track": "Bass"},
    "live_transport_set": {"tempo": 125},
    "live_transport_set_position": {"position": "2.1.1"},
    "live_transport_set_loop": {"start": 0, "length": 8},
    "live_view_show": {"view": "Session"},
    "live_view_navigate": {"direction": "down"},
    "live_view_select": {"track": "Bass"},
    "live_view_set": {"follow_song": True},
}

#: Tools that need a third-party plug-in on "Bass" (the stub set has none by default).
NEEDS_PLUGIN = {"live_plugin_get", "live_plugin_presets", "live_plugin_parameters",
                "live_plugin_set", "live_plugin_configure"}

#: Tools whose sample call is expected to stop at the tool's own validation (reason).
TOOL_SIDE_REFUSAL = {
    "live_sample_upload": "a real upload would write into this machine's LiveBridge sample "
                          "inbox (covered with a temporary inbox in test_samples)",
}

#: Tools that are not about the bridge (or would wait): called with special handling.
SKIP = {
    "live_splice_watch_folder": "waits for a file to arrive (covered in test_splice)",
}

#: Tools whose command refuses to run on a bridge without a token.
NEEDS_TOKEN = {"live_eval_python"}

#: Error types a smoke call may legitimately produce on the stub set.
ALLOWED_ERRORS = {"not_found", "invalid_state", "unsupported", "bad_args"}


# --------------------------------------------------------------------------- every tool

@pytest.mark.parametrize("name", TOOL_NAMES)
def test_every_tool_runs_end_to_end(name, tcp_bridge, song, tmp_path):
    if name in SKIP:
        pytest.skip(SKIP[name])
    app = _APP
    token = ""
    if name in NEEDS_TOKEN:
        # eval.python is refused on a bridge without a token: give this one a token (the
        # server checks it on every request) and send the same token from the client.
        token = tcp_bridge.config.token = "consistency-token"
    app.bridge.switch(host="127.0.0.1", port=tcp_bridge.port, token=token)
    args = dict(SAMPLE_ARGS.get(name, {}))
    if name in NEEDS_PLUGIN:
        factory.add_plugin(song.tracks[0], "Serum 2", params=[("Filter 1 Freq", 0.5)],
                           presets=("Default",),
                           plugin_parameter_names=["Filter 1 Freq", "Filter 1 Res", "Main Vol"])
    wav = write_wav(tmp_path / "Loop 120.wav")
    if name in ("live_sample_import", "live_sample_inspect"):
        args["file_path"] = wav
    if name == "live_sample_list":
        args["folder"] = str(tmp_path)
    if name == "live_splice_import_downloaded":
        args["folder"] = str(tmp_path)
    if name == "live_connect":
        args = {"host": "127.0.0.1", "port": tcp_bridge.port}
    if name == "live_discover":
        args = {"seconds": 0.2, "port": 0}
    result = call_tool(app, name, args)
    if isinstance(result, dict) and "error" in result and "type" in result:
        kind = result["type"]
        assert kind in ALLOWED_ERRORS, (name, result)
        text = result["error"].lower()
        assert "unknown argument" not in text, (name, result)
        assert "missing required argument" not in text, (name, result)
        if kind == "bad_args" and name not in TOOL_SIDE_REFUSAL:
            # A refusal by the tool itself (not the bridge) means the sample call never
            # reached Live: the SAMPLE_ARGS are wrong for the tool's own validation.
            assert text.startswith("wrong arguments for"), (name, result)
    else:
        assert result is not None, name


#: Parameter names that legitimately take different JSON types in different tools.
MIXED_TYPES_OK = {
    "value",        # a parameter value, a LOM property value, a macro value ...
    "values",       # {name: value} maps vs lists of rows
    "args",         # lom.call positional list vs live_command_call's dict
    "points",       # automation point lists vs a resolution count (live_automation_get)
    "parameters",   # lists of names vs {name: value} maps
    "chords",       # a chord string "Am F C G" vs a list
    "pattern",      # drum pattern dict vs a pattern string
    "notes",        # note lists vs note names
    "time", "start", "end", "position", "length",   # beats or "bars.beats.sixteenths"
    "track", "device", "clip", "slot", "scene", "cue", "chain", "note", "parameter",
    "target_track", "source", "destination", "targets", "sections", "cues", "files",
    "settings", "steps", "filter", "color", "active", "arm", "mute", "solo", "macros",
    "preset", "sends", "tracks", "include", "kind", "detail", "mode", "category",
    "grid",         # arrangement overview: show a text grid (bool); clip quantize/editor grid
    "to",           # clip warp: a beat time; clip convert: the target kind ("simpler")
    "target",       # device modulation target (index/name); sample import target mode
}


def _json_types(schema):
    if not isinstance(schema, dict):
        return set()
    if "type" in schema:
        kinds = schema["type"]
        return set(kinds if isinstance(kinds, list) else [kinds]) - {"null"}
    found = set()
    for key in ("anyOf", "oneOf"):
        for option in schema.get(key, []) or []:
            found |= _json_types(option)
    return found


def test_same_named_arguments_share_a_json_type():
    """A name such as new_track or seconds means the same kind of value in every tool
    (this would have caught new_track str vs bool and points int vs list)."""
    by_name = {}
    for tool in asyncio.run(_APP.list_tools()):
        schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None) or {}
        for prop, sub in (schema.get("properties") or {}).items():
            by_name.setdefault(prop, {})[tool.name] = frozenset(_json_types(sub))
    problems = []
    for prop, tools in sorted(by_name.items()):
        if prop in MIXED_TYPES_OK:
            continue
        # "integer" and "number" are both numbers; a tool may accept MORE forms than another
        # (bool | "toggle"), but two tools whose forms do not overlap at all disagree.
        kinds = {tool: frozenset("number" if k == "integer" else k for k in kind)
                 for tool, kind in tools.items() if kind}
        names = sorted(kinds)
        clash = [(a, b) for i, a in enumerate(names) for b in names[i + 1:]
                 if not kinds[a] & kinds[b]]
        if clash:
            problems.append((prop, {t: sorted(k) for t, k in kinds.items()}))
    assert problems == []


def test_undeclared_arguments_are_rejected_in_every_schema():
    for tool in asyncio.run(_APP.list_tools()):
        schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None) or {}
        assert schema.get("additionalProperties") is False, tool.name


#: Second branches of multi-command tools (the SAMPLE_ARGS call covers the first one).
SECOND_BRANCHES = [
    ("live_rack_macros", {"track": "Bass", "device": 1, "values": {"1": 64}}),
    ("live_automation_state", {"track": "Bass", "parameter": "Volume"}),
    ("live_cue_add", {"time": 32, "name": "Bridge"}),
    ("live_transport_play", {"mode": "continue"}),
    ("live_transport_undo", {"redo": True}),
    ("live_clip_create", {"track": "Vocals", "slot": 3, "file_path": "__WAV__"}),
    ("live_command_batch", {"commands": [{"cmd": "tracks.get", "args": {"track": "Nope"}}],
                            "stop_on_error": False}),
    ("live_mixer_meters", {"seconds": 0}),
]


@pytest.mark.parametrize("name, args", SECOND_BRANCHES,
                         ids=[f"{n}-{i}" for i, (n, _a) in enumerate(SECOND_BRANCHES)])
def test_second_branches_run_end_to_end(name, args, tcp_bridge, song, tmp_path):
    if name not in TOOL_NAMES:
        pytest.skip(f"{name} is not registered")
    app = _APP
    app.bridge.switch(host="127.0.0.1", port=tcp_bridge.port, token="")
    wav = write_wav(tmp_path / "Hit.wav")
    args = {k: (wav if v == "__WAV__" else v) for k, v in args.items()}
    result = call_tool(app, name, args)
    if isinstance(result, dict) and "error" in result and "type" in result:
        assert result["type"] in ALLOWED_ERRORS, (name, result)
        text = result["error"].lower()
        assert "unknown argument" not in text and "has no argument" not in text, (name, result)
        if result["type"] == "bad_args":
            assert text.startswith("wrong arguments for"), (name, result)
    else:
        assert result is not None, name


_ANALYSIS = {}


def _analysis():
    if not _ANALYSIS:
        _ANALYSIS.update(gen_tools_doc.analyse())
    return _ANALYSIS


def test_every_tool_has_a_docstring_and_follows_the_naming_rule():
    data = _analysis()
    assert data["problems"] == []


def test_tools_doc_is_up_to_date():
    data = _analysis()
    with open(gen_tools_doc.OUTPUT, encoding="utf-8") as handle:
        current = handle.read()
    assert current == gen_tools_doc.render(data), \
        "docs/TOOLS.md is stale — run .venv/bin/python installers/gen_tools_doc.py"


def test_every_command_is_reachable_from_a_tool():
    data = _analysis()
    uncovered = [name for name, tools in data["command_tools"].items()
                 if not tools and name not in gen_tools_doc.COVERED_BY]
    assert uncovered == []


# --------------------------------------------------------------------------- resolvers

@pytest.mark.parametrize("spec, expected", [
    (0, "Bass"), (-1, "Drums"), ("1", "Vocals"), ("Bass", "Bass"), ("bass", "Bass"),
    ("voc", "Vocals"), ("A", "A-Reverb"), ("return B", "B-Delay"), ("areverb", "A-Reverb"),
    ("master", None), ("main", None), ("song.tracks[2]", "Drums"),
    ("song.return_tracks[1]", "B-Delay"),
])
def test_track_forms_are_the_same_everywhere(bridge, song, spec, expected):
    """One resolver: tracks.get, mixer.get, devices.list and clips.list agree."""
    want = song.master_track if expected is None else \
        [t for t in list(song.tracks) + list(song.return_tracks) if t.name == expected][0]
    assert bridge.ctx.track(spec) == want
    assert run(bridge, "tracks.get", track=spec)["name"] == want.name
    mixer = run(bridge, "mixer.get", track=spec)
    assert mixer["tracks"][0]["name"] == want.name if "tracks" in mixer else \
        mixer.get("name") == want.name
    listing = run(bridge, "devices.list", track=spec)
    assert listing["track"]["name"] == want.name


def test_selected_track_works_in_every_module(bridge, song):
    song.view.selected_track = song.tracks[2]
    assert run(bridge, "tracks.get", track="selected")["name"] == "Drums"
    assert run(bridge, "devices.list", track="selected")["track"]["name"] == "Drums"
    clips = run(bridge, "clips.list", track="selected")
    assert all(c["track"] == 2 or c.get("track_name") == "Drums"
               or "tracks[2]" in c["path"] for c in clips["clips"])
    assert run(bridge, "automation.overview", track="selected")["track"]["name"] == "Drums"


def test_track_errors_are_the_same_everywhere(bridge):
    for cmd, extra in (("tracks.get", {}), ("clips.list", {}), ("devices.list", {}),
                       ("routing.get", {}), ("automation.overview", {})):
        error = fail(bridge, cmd, track="Nope", **extra)
        assert error["type"] == "not_found" and "no track named 'Nope'" in error["message"]
        error = fail(bridge, cmd, track=7, **extra)
        assert error["type"] == "not_found" and "index out of range" in error["message"]


def test_resolve_track_rejects_non_tracks(bridge):
    with pytest.raises(BridgeError) as error:
        resolve.track(bridge.ctx, "song.scenes[0]")
    assert error.value.type == "bad_args"
    with pytest.raises(BridgeError) as error:
        resolve.track(bridge.ctx, "A-Reverb", include_returns=False)
    assert error.value.type == "bad_args"
    assert "'A-Reverb' is a return track — not allowed here" in error.value.message
    with pytest.raises(BridgeError) as error:
        resolve.track(bridge.ctx, "A", include_returns=False)
    assert error.value.type == "bad_args" and "return track A" in error.value.message
    with pytest.raises(BridgeError) as error:
        resolve.track(bridge.ctx, "Main", include_master=False)
    assert error.value.type == "bad_args"
    with pytest.raises(BridgeError) as error:
        resolve.track(bridge.ctx, "master", include_master=False)
    assert error.value.type == "bad_args"
    with pytest.raises(BridgeError) as error:
        resolve.track(bridge.ctx, "Nope", include_returns=False)
    assert error.value.type == "not_found"
    assert resolve.track(bridge.ctx, 2.0).name == "Drums"
    with pytest.raises(BridgeError) as error:
        resolve.track(bridge.ctx, 1.5)
    assert error.value.type == "bad_args"


def test_resolve_scene_forms_and_errors(bridge, song):
    ctx = bridge.ctx
    song.scenes[0].name = "LB Verse A"
    song.scenes[1].name = "LB Verse B"
    song.scenes[2].name = "Chorus"
    assert resolve.scene(ctx, 2) == song.scenes[2]
    assert resolve.scene(ctx, 2.0) == song.scenes[2]            # integral floats
    assert resolve.scene(ctx, "-1") == song.scenes[-1]
    assert resolve.scene(ctx, "chorus") == song.scenes[2]
    assert resolve.scene(ctx, "LB Verse B") == song.scenes[1]
    assert resolve.scene(ctx, "song.scenes[1]") == song.scenes[1]
    assert ctx.scene("cho") == song.scenes[2]                    # Context delegates
    with pytest.raises(BridgeError) as error:
        resolve.scene(ctx, "LB Verse")                           # ambiguous prefix
    assert error.value.type == "bad_args"
    assert "'LB Verse A' (#0)" in error.value.message and "'LB Verse B' (#1)" in error.value.message
    with pytest.raises(BridgeError) as error:
        resolve.scene(ctx, "song.tracks[0]")
    assert error.value.type == "bad_args" and "not a scene" in error.value.message
    for bad in (1.5, True, None, [1]):
        with pytest.raises(BridgeError) as error:
            resolve.scene(ctx, bad)
        assert error.value.type == "bad_args"
    with pytest.raises(BridgeError) as error:
        resolve.scene(ctx, "Nope")
    assert error.value.type == "not_found" and "'Chorus'" in error.value.message
    with pytest.raises(BridgeError) as error:
        resolve.scene(ctx, 40)
    assert error.value.type == "not_found"
    with pytest.raises(BridgeError) as error:
        ctx.clip_slot("Bass", 1.5)
    assert error.value.type == "bad_args"
    assert ctx.clip_slot("Bass", 1.0) == song.tracks[0].clip_slots[1]


def test_device_forms_are_the_same_everywhere(bridge, song):
    """ctx.device (automation, routing, browser, view) uses the devices.* resolver."""
    ctx = bridge.ctx
    operator = song.tracks[0].devices[0]
    assert ctx.device("Bass", "Operator") == operator
    assert ctx.device("Bass", "operator") == operator
    assert ctx.device("Bass", 0) == operator
    assert ctx.device("Bass", "song.tracks[0].devices[0]") == operator
    # nested devices are found by name too (inside the Drum Rack's pads)
    nested = ctx.device("Drums", "song.tracks[2].devices[0].drum_pads[36].chains[0].devices[0]")
    assert nested.class_name in ("OriginalSimpler", "Simpler") or nested is not None
    assert ctx.parameter(operator, "filter freq").name == "Filter Freq"


def test_colours_are_parsed_the_same_everywhere(bridge, song):
    assert resolve.parse_color("red") == ("index", 14)
    assert resolve.parse_color(5) == ("index", 5)
    assert resolve.parse_color("#FF8000") == ("rgb", 0xFF8000)
    assert resolve.parse_color([255, 0, 0]) == ("rgb", 0xFF0000)
    with pytest.raises(BridgeError):
        resolve.parse_color("reddish")
    run(bridge, "tracks.set", track="Bass", color="blue")
    run(bridge, "clips.set", track="Bass", slot=0, color="blue")
    run(bridge, "scenes.set", scene=0, color="blue")
    assert song.tracks[0].color_index == 22
    assert song.tracks[0].clip_slots[0].clip.color_index == 22
    assert song.scenes[0].color_index == 22


def test_signatures_are_parsed_the_same_everywhere(bridge, song):
    assert resolve.parse_signature("6/8") == (6, 8)
    assert resolve.parse_signature([3, 4]) == (3, 4)
    for bad in ("7/9", "0/4", "waltz", [4], True):
        with pytest.raises(BridgeError):
            resolve.parse_signature(bad)
    for cmd, args in (("transport.set", {"signature": "7/9"}),
                      ("scenes.set", {"scene": 0, "time_signature": "7/9"}),
                      ("clips.set", {"track": "Bass", "slot": 0, "signature": "7/9"}),
                      ("cues.convert_time", {"beats": 4, "signature": "7/9"})):
        assert fail(bridge, cmd, **args)["type"] == "bad_args", cmd


def test_bbs_strings_work_in_every_time_argument(bridge, song):
    """"bars.beats.sixteenths" is accepted by transport, cues, automation AND clips."""
    assert resolve.beats_to_bbs(8.0) == "3.1.1"
    assert resolve.bbs_to_beats("3.1.1") == 8.0
    assert resolve.parse_time(song, "2.1.1", "t") == 4.0
    run(bridge, "transport.set_position", position="2.1.1")
    assert song.current_song_time == 4.0
    run(bridge, "clips.set", track="Bass", slot=0, loop_start="1.2.1", loop_end="2.1.1")
    clip = song.tracks[0].clip_slots[0].clip
    assert (clip.loop_start, clip.loop_end) == (1.0, 4.0)
    run(bridge, "clips.set", track="Bass", slot=0, length="1.0.0")
    assert clip.loop_end - clip.loop_start == 4.0
    created = run(bridge, "arrangement.create_midi_clip", track="Bass", start="5.1.1",
                  length="1.0.0")
    assert created["start_time"] == 16.0


def test_paged_results_share_one_shape(bridge, song):
    for cmd, args, key in (("tracks.list", {"limit": 1}, "tracks"),
                           ("scenes.list", {"limit": 1}, "scenes"),
                           ("clips.list", {"limit": 1}, "clips"),
                           ("lom.children", {"path": "song.tracks", "limit": 1}, "items"),
                           ("notes.get", {"track": "Bass", "slot": 0, "limit": 1}, "notes")):
        result = run(bridge, cmd, **args)
        assert result["offset"] == 0 and result["count"] == len(result[key]) == 1, cmd
        assert result["total"] > 1 and result["next_offset"] == 1, cmd
    assert "next_offset" not in run(bridge, "tracks.list")
    for cmd, args in (("tracks.list", {}), ("scenes.list", {}), ("clips.list", {}),
                      ("lom.children", {"path": "song.tracks"}),
                      ("devices.parameters", {"track": "Bass", "device": "Operator"}),
                      ("notes.get", {"track": "Bass", "slot": 0})):
        assert fail(bridge, cmd, offset=-1, **args)["type"] == "bad_args", cmd
        assert fail(bridge, cmd, limit=0, **args)["type"] == "bad_args", cmd


def test_detail_is_validated_the_same_everywhere(bridge):
    for cmd, args in (("tracks.list", {}), ("scenes.list", {}), ("clips.list", {}),
                      ("devices.list", {"track": "Bass"}), ("mixer.get", {"track": "Bass"}),
                      ("song.snapshot", {}), ("lom.children", {"path": "song.tracks"}),
                      ("browser.browse", {"path": "instruments"})):
        error = fail(bridge, cmd, detail="everything", **args)
        assert error["type"] == "bad_args" and "detail" in error["message"], cmd


def test_bbs_strings_pass_through_the_mcp_tools(tcp_bridge, song, tmp_path):
    """The tool schemas accept "bars.beats.sixteenths" wherever the handlers do."""
    app = _APP
    app.bridge.switch(host="127.0.0.1", port=tcp_bridge.port, token="")
    result = call_tool(app, "live_clip_set", {"track": "Bass", "slot": 0,
                                              "loop_start": "1.2.1", "loop_end": "2.1.1"})
    assert "error" not in result, result
    clip = song.tracks[0].clip_slots[0].clip
    assert (clip.loop_start, clip.loop_end) == (1.0, 4.0)
    created = call_tool(app, "live_arrangement_create_clip",
                        {"track": "Bass", "start": "9.1.1", "length": "1.0.0"})
    assert created["start_time"] == 32.0, created
    bad = call_tool(app, "live_clip_set", {"track": "Bass", "slot": 0, "loop_start": "soon"})
    assert bad["type"] == "bad_args"
    settings = call_tool(app, "live_record_settings", {"punch_start": "3.1.1",
                                                       "punch_end": "5.1.1"})
    assert "error" not in settings, settings
    assert (song.loop_start, song.loop_length) == (8.0, 8.0)
    wav = write_wav(tmp_path / "Hit.wav")
    imported = call_tool(app, "live_sample_import", {"file_path": wav, "track": "Vocals",
                                                     "mode": "arrangement", "time": "2.1.1"})
    assert "error" not in imported, imported
    assert imported["clip"]["start_time"] == 4.0
