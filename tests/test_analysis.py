"""Theory and audio analysis: the pure engines and the two MCP tools end to end."""

import math
import struct
import sys
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp_server"))

from livebridge_mcp import audio_analysis, theory  # noqa: E402

from test_consistency import _APP, call_tool  # noqa: E402


def chord(pitches, start, duration=4.0, velocity=90):
    return [[p, start, duration, velocity, False] for p in pitches]


F_MINOR_PAD = (chord([53, 56, 60], 0) + chord([49, 53, 56], 4) + chord([56, 60, 63], 8)
               + chord([51, 55, 58], 12))


def test_key_chords_and_progression():
    bass = [[29, 0, 3, 100], [25, 4, 3, 96], [32, 8, 3, 100], [27, 12, 3, 104]]
    result = theory.analyze_parts([{"name": "Pad", "notes": F_MINOR_PAD, "length": 16},
                                   {"name": "Bass", "notes": bass, "length": 16}])
    assert result["key"]["name"] == "F minor"
    assert [c["name"] for c in result["chords"]] == ["Fm", "Db", "Ab", "Eb"]
    assert result["progression"] == "i VI III VII"
    assert result["out_of_key"] == [] and result["clashes"] == []
    assert result["scale_notes"] == ["F", "G", "Ab", "Bb", "C", "Db", "Eb"]


def test_wrong_bass_note_is_reported_as_clash_and_out_of_key():
    bass = [[29, 0, 2, 100], [33, 2, 1, 100], [25, 4, 3, 100], [32, 8, 3, 100], [27, 12, 3, 100]]
    result = theory.analyze_parts([{"name": "Pad", "notes": F_MINOR_PAD, "length": 16},
                                   {"name": "Bass", "notes": bass, "length": 16}])
    assert result["out_of_key"][0]["note"] == "A" and result["out_of_key"][0]["part"] == "Bass"
    clash = result["clashes"][0]
    assert clash["kind"] == "semitone" and clash["at"] == [2.0]
    assert any("semitone rub" in text for text in result["suggestions"])


def test_major_seventh_inside_the_key_is_not_a_clash():
    pad = chord([53, 57, 60, 64], 0)                       # Fmaj7: F bass against E is colour
    result = theory.analyze_parts([{"name": "Pad", "notes": pad, "length": 4},
                                   {"name": "Bass", "notes": [[29, 0, 4, 100]], "length": 4}],
                                  key={"tonic": 0, "mode": "major"})
    assert result["clashes"] == []
    assert result["chords"][0]["name"] == "Fmaj7" and result["chords"][0]["roman"] == "IVmaj7"


def test_low_mud_mode_hint_and_drums():
    low = [[36, 0, 4, 100], [39, 0, 4, 100]]               # C1 + Eb1 together
    result = theory.analyze_parts([{"name": "Low", "notes": low, "length": 4}])
    assert result["clashes"][0]["kind"] == "low_mud"
    stab = [[57, 0, .5, 100], [58, 1, .5, 90], [57, 2, .5, 100], [60, 3, .5, 100],
            [64, 3.5, .5, 80], [55, 2.5, .25, 80]]
    assert theory.analyze_parts([{"name": "Stab", "notes": stab}])["key"]["name"] == "A phrygian"
    drums = theory.analyze_parts([{"name": "Kit", "drums": True,
                                   "notes": [[36, b, .25, 100] for b in range(8)]}])
    assert drums["key"] is None and drums["parts"][0]["drums"] is True
    assert any("same velocity" in text for text in drums["suggestions"])
    assert theory.analyze_parts([])["parts"] == []


# --------------------------------------------------------------------------- tools

def test_theory_tool_end_to_end(tcp_bridge, song):
    _APP.bridge.switch(host="127.0.0.1", port=tcp_bridge.port, token="")
    result = call_tool(_APP, "live_theory_analyze",
                       {"clips": [{"track": "Bass", "slot": 0}, {"track": "Drums", "slot": 0}]})
    names = [p["name"] for p in result["parts"]]
    assert names[0].startswith("Bass") and result["parts"][1].get("drums") is True
    assert result["key"]["name"].startswith("C")            # C1 + G1 bassline
    skipped = call_tool(_APP, "live_theory_analyze",
                        {"clips": [{"track": "Drums", "slot": 0}], "drums": "skip"})
    assert skipped["parts"] == [] and skipped["key"] is None
    forced = call_tool(_APP, "live_theory_analyze", {"track": "Bass", "slot": 0, "key": "song"})
    assert forced["key"]["name"] == "C major" and "confidence" not in forced["key"]
    for bad in ({"track": "Bass", "slot": 0, "key": "H minor"},
                {"track": "Bass", "slot": 0, "clips": ["x"]}, {"clips": []},
                {"track": "Bass", "slot": 0, "drums": "maybe"}):
        assert call_tool(_APP, "live_theory_analyze", bad)["type"] == "bad_args"


def write_loop(path, seconds=12.0, bpm=124.0, rate=22050):
    """A kick on every beat plus an A minor triad (16-bit stereo WAV, stdlib only)."""
    beat = 60.0 / bpm
    frames = bytearray()
    for n in range(int(seconds * rate)):
        t = n / rate
        phase = t % beat
        kick = 0.7 * math.sin(2 * math.pi * (50 + 80 * math.exp(-phase * 30)) * phase) \
            * math.exp(-phase * 14)
        tone = sum(0.06 * math.sin(2 * math.pi * f * t) for f in (220.0, 261.63, 329.63))
        value = int(max(-1.0, min(1.0, kick + tone)) * 32000)
        frames += struct.pack("<hh", value, value)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))
    return str(path)


def test_audio_tool_measures_a_loop(tcp_bridge, song, tmp_path):
    pytest.importorskip("soundfile")
    pytest.importorskip("pyloudnorm")
    _APP.bridge.switch(host="127.0.0.1", port=tcp_bridge.port, token="")
    loop = write_loop(tmp_path / "loop.wav")
    result = call_tool(_APP, "live_audio_analyze", {"file_path": loop, "reference": loop})
    assert result["kind"] == "loop"
    assert abs(result["tempo"]["bpm"] - 124.0) < 1.5
    assert result["key"]["name"] == "A minor"
    assert -30 < result["loudness"]["lufs"] < -5 and result["stereo"]["width"] == 0.0
    assert set(result["spectrum"]["bands_db"]) >= {"sub", "bass", "mud", "presence"}
    assert result["comparison"]["lufs"] == 0.0 and result["comparison"]["notes"] == []
    window = call_tool(_APP, "live_audio_analyze", {"file_path": loop, "start": 2, "duration": 1})
    assert window["kind"] == "one_shot" and window["file"]["analysed"] == [2.0, 3.0]


def test_audio_tool_refusals(tcp_bridge, song, tmp_path, monkeypatch):
    _APP.bridge.switch(host="127.0.0.1", port=tcp_bridge.port, token="")
    assert call_tool(_APP, "live_audio_analyze", {})["type"] == "bad_args"
    assert call_tool(_APP, "live_audio_analyze", {"file_path": "x", "kind": "song"})["type"] \
        == "bad_args"
    missing = call_tool(_APP, "live_audio_analyze", {"file_path": str(tmp_path / "no.wav")})
    assert missing["type"] == "not_found"
    midi = call_tool(_APP, "live_audio_analyze", {"track": "Bass", "slot": 0})
    assert midi["type"] == "invalid_state" and "live_theory_analyze" in midi["error"]
    # the audio clip of the stub set points at a file that is not on this disk
    assert call_tool(_APP, "live_audio_analyze", {"track": "Vocals", "slot": 0})["type"] \
        == "not_found"

    def no_deps():
        raise audio_analysis.MissingDependency("audio analysis needs numpy: "
                                               + audio_analysis.INSTALL_HINT)
    monkeypatch.setattr(audio_analysis, "_deps", no_deps)
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF")
    refused = call_tool(_APP, "live_audio_analyze", {"file_path": str(wav)})
    assert refused["type"] == "unsupported" and "pip install" in refused["error"]


def test_production_guide_is_served_and_names_real_tools():
    import asyncio
    import re

    from livebridge_mcp import server as server_mod

    assert server_mod.PRODUCTION_PATH.is_file()
    resources = asyncio.run(_APP.list_resources())
    assert any(str(r.uri) == server_mod.PRODUCTION_URI for r in resources)
    text = server_mod.PRODUCTION_PATH.read_text(encoding="utf-8")
    tools = {t.name for t in asyncio.run(_APP.list_tools())}
    mentioned = set(re.findall(r"`(live_[a-z_]+)", text))
    assert mentioned and mentioned <= tools, sorted(mentioned - tools)
