"""The "make me an 8-bar beat" workflow through the MCP tool layer, on the stub set.

What Claude does for a beat, tool by tool (the skill's recipe): tempo -> drum kit from the
browser on a new track -> drum pattern -> an instrument + chords -> mix in dB -> a
compressor bus side-chained to the drums -> clip automation -> an arrangement copy ->
cleanup. Every step is checked against the stub song, so a tool whose arguments or result
drift from what the recipe relies on fails here. (``tests/integration_check.py --scenario
beat`` runs the same chain at the bridge level against the real Live.)
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

import Live
from live_stub_ext import routing as routing_ext

_TESTS = Path(__file__).resolve().parent
for _path in (str(_TESTS.parent / "mcp_server"), str(_TESTS)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from livebridge_mcp.client import BridgeClient  # noqa: E402
from livebridge_mcp.server import create_app  # noqa: E402

_APP = create_app(BridgeClient(host="127.0.0.1", port=9, timeout=10.0))


def tool(tool_name, **args):
    result = asyncio.run(_APP.call_tool(tool_name, args))
    content = getattr(result, "content", None)
    if content is None:
        content = result[0] if isinstance(result, tuple) else result
    text = "\n".join(b.text for b in content if getattr(b, "type", None) == "text")
    data = json.loads(text)
    assert not (isinstance(data, dict) and "error" in data and "type" in data), \
        (tool_name, data)
    return data


@pytest.fixture()
def real_routing():
    """Track-to-track routing and a side-chain capable Compressor as in Live 12.4.5."""
    uninstall = routing_ext.install(Live)
    try:
        yield routing_ext
    finally:
        uninstall()


def by_name(song, name):
    return [t for t in song.tracks if t.name == name][0]


def test_eight_bar_beat_through_the_tools(tcp_bridge, song, real_routing):
    _APP.bridge.switch(host="127.0.0.1", port=tcp_bridge.port, token="")
    before = [t.name for t in song.tracks]

    # 1. tempo
    tool("live_transport_set", tempo=124)
    assert song.tempo == 124.0

    # 2. a drum kit from the browser on a new track
    loaded = tool("live_browser_load", query="909", category="drum_kit", new_track=True,
                  track_name="LB Drums")
    drums = by_name(song, "LB Drums")
    assert loaded["created_track"] is True and loaded["loaded"]["name"] == "Kit-Core 909"
    assert [d.name for d in drums.devices] == ["Kit-Core 909"]

    # 3. an 8-bar drum pattern (16ths, repeated over 8 bars)
    pattern = tool("live_clip_write_pattern", track="LB Drums", slot=0,
                   pattern={"kick": "x...x...x...x...", "snare": "....x.......x...",
                            "hat": "x.x.x.x.x.x.x.x."}, repeat=8)
    clip = drums.clip_slots[0].clip
    assert pattern["added"] == (4 + 2 + 8) * 8
    assert len(clip.get_all_notes_extended()) == 112 and clip.length == 32.0

    # 4. an instrument on a new MIDI track + chords
    tool("live_tracks_create", type="midi", name="LB Keys")
    tool("live_device_insert", name="Operator", track="LB Keys")
    keys = by_name(song, "LB Keys")
    assert keys.devices[0].class_name == "Operator"
    chords = tool("live_clip_write_chords", track="LB Keys", slot=0, chords="Am F C G",
                  duration=8)
    assert len(chords["chords"]) == 4 and chords["added"] >= 12
    assert keys.clip_slots[0].clip.length == 32.0

    # 5. mix in dB
    tool("live_mixer_set_many", settings=[{"track": "LB Drums", "volume": "-6 dB"},
                                          {"track": "LB Keys", "volume": "-10 dB",
                                           "pan": "L20"}])
    drums_volume = drums.mixer_device.volume
    assert "-6" in drums_volume.str_for_value(drums_volume.value)
    assert keys.mixer_device.panning.value < 0

    # 6. a compressor bus side-chained to the drums
    tool("live_tracks_create", type="audio", name="LB Bus")
    inserted = tool("live_device_insert", name="Compressor", track="LB Bus")
    bus = by_name(song, "LB Bus")
    assert inserted["name"] == "Compressor" and len(bus.devices) == 1
    # the stub's inserted Compressor has no side-chain routing: swap in the 12.4.5 model
    tool("live_device_delete", track="LB Bus", device="Compressor")
    real_routing.add_compressor(bus)
    routed = tool("live_routing_route", source="LB Drums", destination="LB Bus",
                  method="sidechain")
    assert routed["routing"]["input"]["type"] == "LB Drums"

    # 7. clip automation: a volume fade-in on the keys clip (display strings)
    written = tool("live_automation_write", track="LB Keys", slot=0, parameter="Volume",
                   points=[{"time": 0, "value": "-24 dB"}, {"time": 16, "value": "-10 dB"}],
                   mode="linear")
    assert written["steps"] and keys.clip_slots[0].clip.has_envelopes

    # 8. the 8 bars into the arrangement
    placed = tool("live_arrangement_duplicate_clip", track="LB Drums", slot=0, time=0)
    assert placed["start_time"] == 0.0 and placed["end_time"] == 32.0
    assert len(drums.arrangement_clips) == 1
    assert "envelopes" in tool("live_arrangement_duplicate_clip", track="LB Keys", slot=0,
                               time=0)

    # 9. cleanup: the set is back to what it was
    for name in ("LB Bus", "LB Keys", "LB Drums"):
        tool("live_tracks_delete", track=name)
    assert [t.name for t in song.tracks] == before
