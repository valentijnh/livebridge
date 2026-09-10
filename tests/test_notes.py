"""Module C — MIDI notes: note/chord theory helpers, every notes.* command on the stub
set, and the note MCP tools (tools/notes.py) against the fake bridge."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parent
_REPO = _TESTS.parent
for _p in (str(_REPO / "mcp_server"), str(_TESTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from LiveBridge.handlers import notes as theory  # noqa: E402
from LiveBridge.registry import BridgeError  # noqa: E402


def run(bridge, cmd, **args):
    response = bridge.dispatch({"id": "t", "cmd": cmd, "args": args})
    assert response["ok"], response
    return response["result"]


def fail(bridge, cmd, **args):
    response = bridge.dispatch({"id": "t", "cmd": cmd, "args": args})
    assert not response["ok"], response
    return response["error"]


def live_notes(clip):
    return sorted((n.pitch, round(n.start_time, 4), round(n.duration, 4), round(n.velocity, 2))
                  for n in clip.get_all_notes_extended())


# --------------------------------------------------------------------------
# theory helpers (Remote Script side)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("C3", 60), ("c3", 60), ("C-2", 0), ("G8", 127), ("F#4", 78), ("Db2", 49), ("Bb1", 46),
    ("E#3", 65), ("Cb3", 59), (60, 60), ("60", 60), (36.0, 36), ("kick", 36), ("Snare", 38),
    ("closed hat", 42), ("tom_low", 45), ("OHH", 46),
])
def test_note_to_midi(value, expected):
    assert theory.note_to_midi(value) == expected


@pytest.mark.parametrize("value", ["H3", "C", "G#8", "C-3", 128, -1, True, None, "x", 1.5])
def test_note_to_midi_rejects(value):
    with pytest.raises(BridgeError) as info:
        theory.note_to_midi(value)
    assert info.value.type == "bad_args"


def test_midi_to_note_roundtrip():
    assert theory.midi_to_note(60) == "C3"
    assert theory.midi_to_note(0) == "C-2"
    assert theory.midi_to_note(127) == "G8"
    for pitch in range(128):
        assert theory.note_to_midi(theory.midi_to_note(pitch)) == pitch


@pytest.mark.parametrize("value,expected", [
    ("1/4", 1.0), ("1/8", 0.5), ("1/16", 0.25), ("1/32", 0.125), ("1/8T", 1 / 3.0),
    ("1/4.", 1.5), ("bar", 4.0), (0.25, 0.25), ("0.5", 0.5),
])
def test_parse_grid(value, expected):
    assert abs(theory.parse_grid(value) - expected) < 1e-9


@pytest.mark.parametrize("value", ["1/0", "fast", 0, -1, True, 100])
def test_parse_grid_rejects(value):
    with pytest.raises(BridgeError):
        theory.parse_grid(value)


@pytest.mark.parametrize("symbol,root,intervals,bass", [
    ("C", 0, (0, 4, 7), None), ("Am", 9, (0, 3, 7), None), ("Am7", 9, (0, 3, 7, 10), None),
    ("Fmaj7", 5, (0, 4, 7, 11), None), ("G7", 7, (0, 4, 7, 10), None),
    ("Bdim", 11, (0, 3, 6), None), ("Caug", 0, (0, 4, 8), None),
    ("Dsus4", 2, (0, 5, 7), None), ("Dsus2", 2, (0, 2, 7), None),
    ("Bm7b5", 11, (0, 3, 6, 10), None), ("Bø", 11, (0, 3, 6, 10), None),
    ("Cdim7", 0, (0, 3, 6, 9), None), ("Ebmaj9", 3, (0, 4, 7, 11, 14), None),
    ("Bb", 10, (0, 4, 7), None), ("F#m", 6, (0, 3, 7), None),
    ("C6/9", 0, (0, 4, 7, 9, 14), None), ("G/B", 7, (0, 4, 7), 11),
    ("Am7/G", 9, (0, 3, 7, 10), 7), ("E7#9", 4, (0, 4, 7, 10, 15), None),
    ("Cmaj7#11", 0, (0, 4, 7, 11, 18), None), ("C7b5", 0, (0, 4, 6, 10), None),
    ("C7(b9)", 0, (0, 4, 7, 10, 13), None), ("Cadd9", 0, (0, 4, 7, 14), None),
    ("C5", 0, (0, 7), None), ("Cm(add9)", 0, (0, 3, 7, 14), None),
    ("C7sus4", 0, (0, 5, 7, 10), None), ("Cno3", 0, (0, 7), None),
    ("CmMaj7", 0, (0, 3, 7, 11), None), ("C13", 0, (0, 4, 7, 10, 14, 21), None),
    ("CMaj7", 0, (0, 4, 7, 11), None), ("CM7", 0, (0, 4, 7, 11), None),
    ("DMin7", 2, (0, 3, 7, 10), None),
])
def test_parse_chord(symbol, root, intervals, bass):
    assert theory.parse_chord(symbol) == (root, intervals, bass)


def test_parse_chord_rest_and_errors():
    assert theory.parse_chord("N.C.") is None
    for bad in ("H7", "Cfoo", "", "7"):
        with pytest.raises(BridgeError):
            theory.parse_chord(bad)


def test_voicings():
    root, intervals, _ = theory.parse_chord("Cmaj7")
    assert theory.voice_chord(root, intervals, 3) == [60, 64, 67, 71]
    assert theory.voice_chord(root, intervals, 3, inversion=1) == [64, 67, 71, 72]
    assert theory.voice_chord(root, intervals, 3, voicing="drop2") == [55, 60, 64, 71]
    assert theory.voice_chord(root, intervals, 3, voicing="open") == [60, 67, 76, 83]
    assert theory.voice_chord(root, intervals, 3, voicing="spread")[0] == 48
    assert theory.voice_chord(root, intervals, 3, voicing="drop3") == [52, 60, 67, 71]


# --------------------------------------------------------------------------
# notes.get
# --------------------------------------------------------------------------

def test_get_compact_rows(bridge):
    result = run(bridge, "notes.get", track="Bass", slot=0)
    assert result["fields"] == ["pitch", "start", "duration", "velocity", "mute",
                                "probability", "note_id"]
    assert result["count"] == 4 and result["loop"] == [0, 4]
    first = result["notes"][0]
    assert first[:6] == [36, 0, 0.5, 100, False, 1]
    assert isinstance(first[6], int)
    assert result["clip"] == "song.tracks[0].clip_slots[0].clip"


def test_get_filters_paging_names_and_expression(bridge):
    window = run(bridge, "notes.get", track="Bass", slot=0, start=1, end=3)
    assert [n[1] for n in window["notes"]] == [1, 2]
    by_pitch = run(bridge, "notes.get", track="Drums", slot=0, pitch="hat")
    assert [n[0] for n in by_pitch["notes"]] == [42, 42]
    several = run(bridge, "notes.get", track="Drums", slot=0, pitch=["kick", "D1"])
    assert sorted(n[0] for n in several["notes"]) == [36, 38]
    ranged = run(bridge, "notes.get", track="Bass", slot=0, pitch_min="C1", pitch_max=40)
    assert ranged["count"] == 3
    paged = run(bridge, "notes.get", track="Bass", slot=0, limit=3)
    assert len(paged["notes"]) == 3 and paged["next_offset"] == 3
    names = run(bridge, "notes.get", track="Bass", slot=0, pitch_names=True,
                include_expression=True)
    assert names["notes"][0][0] == "C1" and len(names["notes"][0]) == 9
    arrangement = run(bridge, "notes.get", clip="song.tracks[0].arrangement_clips[0]")
    assert arrangement["count"] == 1


def test_get_errors(bridge):
    assert fail(bridge, "notes.get", track="Vocals", slot=0)["type"] == "invalid_state"
    assert fail(bridge, "notes.get", track="Bass", slot=3)["type"] == "not_found"
    assert fail(bridge, "notes.get", track="Bass", slot=0, start=3, end=1)["type"] == "bad_args"
    assert fail(bridge, "notes.get", track="Bass", slot=0, pitch="Q9")["type"] == "bad_args"
    assert fail(bridge, "notes.get", track="Bass", slot=0,
                pitch_min=50, pitch_max=40)["type"] == "bad_args"


# --------------------------------------------------------------------------
# add / replace / clear / modify
# --------------------------------------------------------------------------

def test_add_notes_dicts_and_lists(bridge, song):
    clip = song.tracks[0].clip_slots[0].clip
    before = len(song._undo_steps)
    result = run(bridge, "notes.add", track="Bass", slot=0, notes=[
        {"pitch": "C3", "start": 0.5, "duration": 0.25, "velocity": 64, "probability": 0.5},
        ["E3", 1.5, 0.25],
        [67, 2.5, 0.5, 90, True],
    ])
    assert len(song._undo_steps) == before + 1
    assert result["added"] == 3 and len(result["note_ids"]) == 3
    notes = dict((n.pitch, n) for n in clip.get_all_notes_extended() if n.pitch >= 60)
    assert notes[60].velocity == 64 and notes[60].probability == 0.5
    assert notes[64].velocity == 100
    assert notes[67].mute is True


def test_add_creates_and_extends(bridge, song):
    created = run(bridge, "notes.add", track="Bass", slot=3, create=True,
                  notes=[{"pitch": 48, "start": 5, "duration": 1}])
    assert created["created"] is True and created["length"] == 8
    assert created["clip"] == "song.tracks[0].clip_slots[3].clip"
    auto = run(bridge, "notes.add", track="Drums", create=True,
               notes=[[36, 0, 0.25]])
    assert auto["clip"] == "song.tracks[2].clip_slots[1].clip"
    extended = run(bridge, "notes.add", track="Bass", slot=0, extend=True,
                   notes=[[40, 6, 1]])
    assert extended["extended_to"] == 8
    assert song.tracks[0].clip_slots[0].clip.loop_end == 8.0


def test_add_errors(bridge):
    assert fail(bridge, "notes.add", track="Bass", slot=0, notes=[])["type"] == "bad_args"
    bad = fail(bridge, "notes.add", track="Bass", slot=0, notes=[{"pitch": "C3", "start": 0}])
    assert bad["type"] == "bad_args"
    assert fail(bridge, "notes.add", track="Bass", slot=0,
                notes=[[60, 0, 0]])["type"] == "bad_args"
    assert fail(bridge, "notes.add", track="Bass", slot=0,
                notes=[[60, -1, 1]])["type"] == "bad_args"
    assert fail(bridge, "notes.add", track="Bass", slot=0,
                notes=[{"pitch": 60, "start": 0, "duration": 1, "vel": 3}])["type"] == "bad_args"
    assert fail(bridge, "notes.add", track="Bass", slot=3,
                notes=[[60, 0, 1]])["type"] == "not_found"
    assert fail(bridge, "notes.add", track="Vocals", slot=0,
                notes=[[60, 0, 1]])["type"] == "invalid_state"


def test_replace_region_and_whole(bridge, song):
    clip = song.tracks[0].clip_slots[0].clip
    region = run(bridge, "notes.replace", track="Bass", slot=0, start=2, end=4,
                 notes=[[50, 2, 1]])
    assert region["removed"] == 2 and region["added"] == 1
    assert live_notes(clip) == [(36, 0.0, 0.5, 100.0), (36, 1.0, 0.5, 90.0),
                                (50, 2.0, 1.0, 100.0)]
    whole = run(bridge, "notes.replace", track="Bass", slot=0, notes=[])
    assert whole["removed"] == 3 and live_notes(clip) == []


def test_clear_variants(bridge, song):
    drums = song.tracks[2].clip_slots[0].clip
    assert run(bridge, "notes.clear", track="Drums", slot=0, pitch="hat")["removed"] == 2
    assert sorted(n.pitch for n in drums.get_all_notes_extended()) == [36, 38]
    ids = [n.note_id for n in drums.get_all_notes_extended() if n.pitch == 36]
    assert run(bridge, "notes.clear", track="Drums", slot=0, note_ids=ids)["removed"] == 1
    assert run(bridge, "notes.clear", track="Bass", slot=0, start=0, end=2)["removed"] == 2
    assert run(bridge, "notes.clear", track="Bass", slot=1)["removed"] == 2
    assert fail(bridge, "notes.clear", track="Bass", slot=0,
                note_ids="1")["type"] == "bad_args"


def test_modify_by_id(bridge, song):
    clip = song.tracks[0].clip_slots[0].clip
    first, second = clip.get_all_notes_extended()[:2]
    result = run(bridge, "notes.modify", track="Bass", slot=0, changes=[
        {"note_id": first.note_id, "velocity": 30, "pitch": "D1", "mute": True},
        {"note_id": second.note_id, "start": 1.25, "duration": 0.75, "probability": 0.6,
         "velocity_deviation": 10, "release_velocity": 20},
    ])
    assert result["modified"] == 2
    after = dict((n.note_id, n) for n in clip.get_all_notes_extended())
    assert after[first.note_id].velocity == 30 and after[first.note_id].pitch == 38
    assert after[first.note_id].mute is True
    assert after[second.note_id].start_time == 1.25
    assert after[second.note_id].duration == 0.75
    assert after[second.note_id].probability == 0.6
    assert after[second.note_id].velocity_deviation == 10
    missing = fail(bridge, "notes.modify", track="Bass", slot=0,
                   changes=[{"note_id": 999999, "velocity": 1}])
    assert missing["type"] == "not_found"
    assert fail(bridge, "notes.modify", track="Bass", slot=0,
                changes=[{"velocity": 1}])["type"] == "bad_args"
    assert fail(bridge, "notes.modify", track="Bass", slot=0,
                changes=[{"note_id": first.note_id, "colour": 1}])["type"] == "bad_args"
    assert fail(bridge, "notes.modify", track="Bass", slot=0,
                changes=[{"note_id": first.note_id, "duration": 0}])["type"] == "bad_args"


# --------------------------------------------------------------------------
# transform
# --------------------------------------------------------------------------

def _set_notes(clip, rows):
    doomed = [n.note_id for n in clip.get_all_notes_extended()]
    clip.remove_notes_by_id(doomed)
    from live_stub import factory
    factory.add_notes(clip, rows)


def test_transform_quantize_with_strength_swing_and_ends(bridge, song):
    clip = song.tracks[0].clip_slots[1].clip
    _set_notes(clip, [(60, 0.1, 0.4, 100), (62, 0.3, 0.2, 100), (64, 0.9, 0.3, 100)])
    run(bridge, "notes.transform", track="Bass", slot=1, quantize="1/4")
    assert [n[1] for n in live_notes(clip)] == [0.0, 0.0, 1.0]
    _set_notes(clip, [(60, 0.1, 0.4, 100)])
    run(bridge, "notes.transform", track="Bass", slot=1, quantize=0.5, strength=0.5)
    assert live_notes(clip)[0][1] == 0.05
    _set_notes(clip, [(60, 0.5, 0.3, 100), (62, 1.0, 0.3, 100)])
    run(bridge, "notes.transform", track="Bass", slot=1, quantize="1/8", swing=0.5)
    assert [n[1] for n in live_notes(clip)] == [0.75, 1.0]
    _set_notes(clip, [(60, 0.1, 0.3, 100)])
    run(bridge, "notes.transform", track="Bass", slot=1, quantize="1/4", quantize_ends=True)
    assert live_notes(clip)[0][1:3] == (0.0, 0.25)  # end snapped, kept >= 1/4 grid
    _set_notes(clip, [(60, 0.1, 1.3, 100)])
    run(bridge, "notes.transform", track="Bass", slot=1, quantize="1/4", quantize_ends=True)
    assert live_notes(clip)[0][1:3] == (0.0, 1.0)


def test_transform_humanize_is_seeded(bridge, song):
    clip = song.tracks[2].clip_slots[0].clip
    original = live_notes(clip)
    first = run(bridge, "notes.transform", track="Drums", slot=0, humanize_timing=0.05,
                humanize_velocity=10, seed=42)
    assert first["seed"] == 42 and first["operations"] == ["humanize"]
    once = live_notes(clip)
    assert once != original
    for (p0, s0, _d0, v0), (p1, s1, _d1, v1) in zip(original, once):
        assert abs(s1 - s0) <= 0.05 + 1e-9 or s1 == 0.0
        assert abs(v1 - v0) <= 10 + 1e-9
    random_seed = run(bridge, "notes.transform", track="Drums", slot=0, humanize_velocity=5)
    assert isinstance(random_seed["seed"], int)


def test_transform_transpose_velocity_and_selection(bridge, song):
    clip = song.tracks[0].clip_slots[0].clip
    result = run(bridge, "notes.transform", track="Bass", slot=0, transpose=12,
                 pitch="C1", velocity_scale=0.5, velocity_offset=10)
    assert result["selected"] == 3 and result["operations"] == ["transpose", "velocity"]
    assert live_notes(clip) == [(43, 2.0, 0.5, 80.0), (48, 0.0, 0.5, 60.0),
                                (48, 1.0, 0.5, 55.0), (48, 3.0, 0.5, 65.0)]
    run(bridge, "notes.transform", track="Bass", slot=0, velocity_set=127, start=2)
    assert [n[3] for n in live_notes(clip) if n[1] >= 2] == [127.0, 127.0]
    run(bridge, "notes.transform", track="Bass", slot=0, velocity_min=70, velocity_max=100)
    assert all(70 <= n[3] <= 100 for n in live_notes(clip))
    out = fail(bridge, "notes.transform", track="Bass", slot=0, transpose=100)
    assert out["type"] == "invalid_state"
    assert fail(bridge, "notes.transform", track="Bass", slot=0)["type"] == "bad_args"
    assert fail(bridge, "notes.transform", track="Bass", slot=0, velocity_min=100,
                velocity_max=10)["type"] == "bad_args"


def test_transform_legato_and_overlaps(bridge, song):
    clip = song.tracks[0].clip_slots[1].clip  # loop 0..8
    _set_notes(clip, [(60, 0.0, 0.25, 100), (64, 0.0, 0.25, 100), (62, 1.0, 0.25, 100),
                      (65, 3.0, 0.5, 100)])
    run(bridge, "notes.transform", track="Bass", slot=1, legato=True, gap=0.05)
    durations = dict(((n[0], n[1]), n[2]) for n in live_notes(clip))
    assert durations[(60, 0.0)] == 0.95 and durations[(64, 0.0)] == 0.95
    assert durations[(62, 1.0)] == 1.95 and durations[(65, 3.0)] == 4.95
    _set_notes(clip, [(60, 0.0, 2.0, 100), (60, 1.0, 0.5, 100), (61, 0.0, 4.0, 100)])
    run(bridge, "notes.transform", track="Bass", slot=1, fix_overlaps=True)
    assert live_notes(clip) == [(60, 0.0, 1.0, 100.0), (60, 1.0, 0.5, 100.0),
                                (61, 0.0, 4.0, 100.0)]


# --------------------------------------------------------------------------
# write_pattern
# --------------------------------------------------------------------------

def test_write_pattern_into_existing_clip(bridge, song):
    clip = song.tracks[2].clip_slots[0].clip  # Beat: 36, 42, 38, 42 in the first 2 beats
    result = run(bridge, "notes.write_pattern", track="Drums", slot=0, pattern={
        "kick": "x...x...x...x...",
        "snare": "....X.......X...",
        "F#1": "x.x.x.x.x.x.x.xo",
    })
    assert result["rows"] == {"kick": 36, "snare": 38, "F#1": 42}
    assert result["removed"] == 4 and result["added"] == 4 + 2 + 9
    assert result["pattern_length"] == 4
    notes = live_notes(clip)
    kicks = [n for n in notes if n[0] == 36]
    assert [n[1] for n in kicks] == [0.0, 1.0, 2.0, 3.0]
    assert all(abs(n[2] - 0.225) < 1e-9 for n in kicks)
    snares = [n for n in notes if n[0] == 38]
    assert [n[3] for n in snares] == [127.0, 127.0]
    ghost = [n for n in notes if n[0] == 42 and n[1] == 3.75]
    assert ghost[0][3] == 50.0


def test_write_pattern_holds_levels_repeat_swing_and_create(bridge, song):
    result = run(bridge, "notes.write_pattern", track="Bass", pattern={"C2": "x__.9-1."},
                 step="1/8", repeat=2, swing=0.5, gate=1.0)
    assert result["created"] is True and result["clip"] == "song.tracks[0].clip_slots[2].clip"
    clip = song.tracks[0].clip_slots[2].clip
    notes = live_notes(clip)
    assert result["added"] == 6 and result["pattern_length"] == 4
    # first hit held for 3 steps of 1/8
    assert notes[0] == (48, 0.0, 1.5, 100.0)
    # "9" on step 4 (even), "1" on step 6 (even) -> no swing; velocity levels
    assert (48, 2.0, 0.5, 127.0) in notes and (48, 3.0, 0.5, 14.0) in notes
    assert (48, 4.0, 1.5, 100.0) in notes
    assert clip.length == 8.0
    # swing shifts odd steps
    swung = run(bridge, "notes.write_pattern", track="Bass", slot=3, pattern={"C2": ".x"},
                step=0.5, swing=0.5)
    assert live_notes(song.tracks[0].clip_slots[3].clip)[0][1] == 0.75
    assert swung["added"] == 1


def test_write_pattern_extend_and_errors(bridge, song):
    clip = song.tracks[0].clip_slots[0].clip  # 4 beats
    result = run(bridge, "notes.write_pattern", track="Bass", slot=0,
                 pattern={"C1": "x" * 32}, start=0)
    assert result["extended_to"] == 8 and clip.loop_end == 8.0
    assert fail(bridge, "notes.write_pattern", track="Bass", slot=0,
                pattern={"C1": "x?x"})["type"] == "bad_args"
    assert fail(bridge, "notes.write_pattern", track="Bass", slot=0,
                pattern={"nope": "x"})["type"] == "bad_args"
    assert fail(bridge, "notes.write_pattern", track="Bass", slot=0,
                pattern={})["type"] == "bad_args"
    assert fail(bridge, "notes.write_pattern", track="Bass", slot=0,
                pattern={"C1": "x"}, step="1/0")["type"] == "bad_args"
    assert fail(bridge, "notes.write_pattern", track="Vocals", slot=0,
                pattern={"C1": "x"})["type"] == "invalid_state"
    assert fail(bridge, "notes.write_pattern", track="Bass", slot=4, create=False,
                pattern={"C1": "x"})["type"] == "not_found"


# --------------------------------------------------------------------------
# write_chords
# --------------------------------------------------------------------------

def test_write_chords_basic_progression(bridge, song):
    result = run(bridge, "notes.write_chords", track="Bass", slot=3,
                 chords=["Am7", "F", "C", "G/B"])
    assert result["created"] is True and result["added"] == 4 + 3 + 3 + 4
    chords = result["chords"]
    assert chords[0]["pitches"] == [69, 72, 76, 79] and chords[0]["notes"][0] == "A3"
    assert [c["start"] for c in chords] == [0, 4, 8, 12]
    assert chords[3]["pitches"] == [59, 67, 71, 74]  # B2 below G B D
    clip = song.tracks[0].clip_slots[3].clip
    assert clip.length == 16.0


def test_write_chords_options(bridge, song):
    result = run(bridge, "notes.write_chords", track="Bass", slot=0, chords="C F G C",
                 duration=2, octave=2, voice_leading=True, bass=True, strum=0.05, velocity=70)
    chords = result["chords"]
    assert result["removed"] == 4
    assert chords[0]["pitches"][0] == 36 and chords[0]["pitches"][1:] == [48, 52, 55]
    assert [c["start"] for c in chords] == [0, 2, 4, 6]
    # voice leading keeps later chords close to the first
    for chord in chords[1:]:
        upper = chord["pitches"][1:]
        assert max(abs(a - b) for a, b in zip(upper, [48, 52, 55])) <= 7
    clip = song.tracks[0].clip_slots[0].clip
    first_chord = sorted(n for n in live_notes(clip) if n[1] < 1)
    starts = sorted(n[1] for n in first_chord)
    assert starts == [0.0, 0.05, 0.1, 0.15]
    assert all(n[3] == 70.0 for n in first_chord)


def test_write_chords_objects_rests_and_errors(bridge, song):
    result = run(bridge, "notes.write_chords", track="Drums", slot=2, chords=[
        {"chord": "Dm9", "start": 0, "duration": 3, "inversion": 1, "velocity": 50},
        "N.C.",
        {"chord": "G7", "octave": 2},
    ], duration=1, voicing="drop2")
    chords = result["chords"]
    assert chords[1]["pitches"] == [] and chords[1]["start"] == 3
    assert chords[2]["start"] == 4
    assert result["added"] == 5 + 4
    assert fail(bridge, "notes.write_chords", track="Bass", slot=3,
                chords=["Hmaj7"])["type"] == "bad_args"
    assert fail(bridge, "notes.write_chords", track="Bass", slot=3,
                chords=[])["type"] == "bad_args"
    assert fail(bridge, "notes.write_chords", track="Bass", slot=3, chords=["C"],
                voicing="weird")["type"] == "bad_args"
    assert fail(bridge, "notes.write_chords", track="Bass", slot=3, chords=["C13"],
                octave=8)["type"] == "bad_args"
    assert fail(bridge, "notes.write_chords", track="Bass", slot=3,
                chords=[{"chord": "C", "len": 2}])["type"] == "bad_args"


def test_theory_command(bridge):
    result = run(bridge, "notes.theory", notes=["C3", 61, "kick"], chords=["G/B", "N.C."])
    assert result["notes"][0] == {"input": "C3", "pitch": 60, "name": "C3"}
    assert result["notes"][2]["pitch"] == 36
    assert result["chords"][0]["pitches"] == [59, 67, 71, 74]
    assert fail(bridge, "notes.theory")["type"] == "bad_args"


# --------------------------------------------------------------------------
# MCP tools
# --------------------------------------------------------------------------

from fake_bridge import FakeBridge  # noqa: E402

from livebridge_mcp.client import BridgeClient  # noqa: E402
from livebridge_mcp.server import create_app  # noqa: E402
from livebridge_mcp.tools import notes as note_tools  # noqa: E402

NOTE_TOOLS = ["live_clip_get_notes", "live_clip_add_notes", "live_clip_remove_notes",
              "live_clip_modify_notes", "live_clip_transform_notes", "live_clip_write_pattern",
              "live_clip_write_chords", "live_clip_write_arp", "live_clip_duplicate_notes",
              "live_clip_select_notes"]


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
    return "\n".join(blocks), ([_p(b) for b in blocks] if len(blocks) > 1
                               else _p("\n".join(blocks)))


@pytest.fixture
def mcp():
    fake = FakeBridge().start()
    client = BridgeClient(host=fake.host, port=fake.port, timeout=5.0)
    app = create_app(client)
    try:
        yield app, fake
    finally:
        client.close()
        fake.stop()


def last(fake):
    return fake.requests[-1]


def test_tool_side_note_names_match_remote_script():
    for pitch in range(128):
        name = note_tools.midi_to_note(pitch)
        assert note_tools.note_to_midi(name) == pitch == theory.note_to_midi(name)
    for drum, pitch in theory.DRUM_NOTES.items():
        assert note_tools.DRUM_NOTES[drum] == pitch
    with pytest.raises(ValueError):
        note_tools.note_to_midi("H2")


def test_note_tools_registered(mcp):
    app, _fake = mcp
    tools = {t.name: t for t in asyncio.run(app.list_tools())}
    for name in NOTE_TOOLS:
        assert name in tools, name
        assert len(tools[name].description or "") > 150, name
    assert "notes" in app.tool_modules


def test_tool_get_notes_normalises_pitches(mcp):
    app, fake = mcp
    fake.set_result("notes.get", {"count": 0, "notes": []})
    call_tool(app, "live_clip_get_notes", {"track": "Drums", "slot": 0, "pitch": ["kick", "D1"],
                                           "pitch_names": True})
    assert last(fake)["args"] == {"track": "Drums", "slot": 0, "pitch": [36, 38], "offset": 0,
                                  "limit": 512, "pitch_names": True}
    call_tool(app, "live_clip_get_notes", {"clip": "selected", "pitch_min": "C3", "start": 1})
    assert last(fake)["args"] == {"clip": "selected", "pitch_min": 60, "start": 1.0,
                                  "offset": 0, "limit": 512}


def test_tool_add_and_replace_notes(mcp):
    app, fake = mcp
    fake.set_result("notes.add", {"added": 2})
    call_tool(app, "live_clip_add_notes", {
        "track": 0, "slot": 1,
        "notes": [{"pitch": "C3", "start": 0, "duration": 1}, ["E3", 1, 0.5, 90]]})
    assert last(fake)["cmd"] == "notes.add"
    assert last(fake)["args"]["notes"] == [{"pitch": 60, "start": 0, "duration": 1},
                                           [64, 1, 0.5, 90]]
    fake.set_result("notes.replace", {"added": 1})
    call_tool(app, "live_clip_add_notes", {"clip": "selected", "notes": [[60, 0, 1]],
                                           "replace": True, "replace_start": 0,
                                           "replace_end": 4, "extend": True})
    assert last(fake)["cmd"] == "notes.replace"
    assert last(fake)["args"] == {"clip": "selected", "notes": [[60, 0, 1]], "start": 0.0,
                                  "end": 4.0, "create": True, "extend": True}
    # create/extend are on by default: "new clip with these notes" is one call
    call_tool(app, "live_clip_add_notes", {"track": "Bass", "notes": [[60, 0, 1]]})
    assert last(fake)["args"] == {"track": "Bass", "notes": [[60, 0, 1]], "create": True,
                                  "extend": True}
    call_tool(app, "live_clip_add_notes", {"track": "Bass", "slot": 2, "create": False,
                                           "extend": False, "notes": [[60, 0, 1]]})
    assert last(fake)["args"]["create"] is False and last(fake)["args"]["extend"] is False


def test_tool_remove_modify_transform(mcp):
    app, fake = mcp
    fake.set_result("notes.clear", {"removed": 1})
    call_tool(app, "live_clip_remove_notes", {"track": 0, "slot": 0, "pitch": "hat",
                                              "note_ids": [3, 4]})
    assert last(fake)["args"] == {"track": 0, "slot": 0, "pitch": 42, "note_ids": [3, 4]}
    fake.set_result("notes.modify", {"modified": 1})
    call_tool(app, "live_clip_modify_notes", {"track": 0, "slot": 0,
                                              "changes": [{"note_id": 3, "pitch": "G3"}]})
    assert last(fake)["args"]["changes"] == [{"note_id": 3, "pitch": 67}]
    fake.set_result("notes.transform", {"selected": 4})
    call_tool(app, "live_clip_transform_notes", {"track": 0, "slot": 0, "quantize": "1/16",
                                                 "strength": 0.75, "transpose": -12,
                                                 "humanize_velocity": 6, "seed": 7,
                                                 "pitch_min": "C1"})
    assert last(fake)["args"] == {"track": 0, "slot": 0, "pitch_min": 36, "quantize": "1/16",
                                  "strength": 0.75, "transpose": -12, "humanize_velocity": 6.0,
                                  "seed": 7}


def test_tool_patterns_and_chords(mcp):
    app, fake = mcp
    fake.set_result("notes.write_pattern", {"added": 4})
    call_tool(app, "live_clip_write_pattern", {"track": "Drums",
                                               "pattern": {"kick": "x...|x...", "C#1": "..x."}})
    args = last(fake)["args"]
    assert args["pattern"] == {"kick": "x...|x...", "C#1": "..x."}
    assert args["track"] == "Drums" and args["step"] == 0.25 and args["create"] is True
    fake.set_result("notes.write_chords", {"added": 12})
    call_tool(app, "live_clip_write_chords", {"track": "Keys", "slot": 0,
                                              "chords": "Am7 | F | C | G/B",
                                              "voice_leading": True})
    args = last(fake)["args"]
    assert args["chords"] == ["Am7", "F", "C", "G/B"] and args["voice_leading"] is True
    assert args["octave"] == 3 and args["voicing"] == "close"


def test_note_tool_validation_stays_local(mcp):
    app, fake = mcp
    count = len(fake.requests)
    checks = [
        ("live_clip_get_notes", {"track": 0}),
        ("live_clip_get_notes", {"track": 0, "slot": 0, "pitch": "H9"}),
        ("live_clip_get_notes", {"track": 0, "slot": 0, "start": 4, "end": 2}),
        ("live_clip_add_notes", {"track": 0, "slot": 0, "notes": []}),
        ("live_clip_add_notes", {"track": 0, "notes": [[60, 0, 1]], "create": False}),
        ("live_clip_add_notes", {"notes": [[60, 0, 1]]}),
        ("live_clip_add_notes", {"track": 0, "slot": 0, "notes": [{"pitch": "C3"}]}),
        ("live_clip_add_notes", {"track": 0, "slot": 0, "notes": [["C9", 0, 1]]}),
        ("live_clip_add_notes", {"track": 0, "slot": 0, "notes": [[60, 0, 0]]}),
        ("live_clip_add_notes", {"track": 0, "slot": 0,
                                 "notes": [{"pitch": 60, "start": 0, "duration": 1, "x": 1}]}),
        ("live_clip_modify_notes", {"track": 0, "slot": 0, "changes": [{"velocity": 3}]}),
        ("live_clip_modify_notes", {"track": 0, "slot": 0, "changes": [{"note_id": 3}]}),
        ("live_clip_transform_notes", {"track": 0, "slot": 0}),
        ("live_clip_transform_notes", {"track": 0, "slot": 0, "transpose": 200}),
        ("live_clip_transform_notes", {"track": 0, "slot": 0, "velocity_set": 0}),
        ("live_clip_write_pattern", {"track": 0, "pattern": {"kick": "x?x"}}),
        ("live_clip_write_pattern", {"track": 0, "pattern": {"H1": "x"}}),
        ("live_clip_write_pattern", {"pattern": {"kick": "x"}}),
        ("live_clip_write_pattern", {"track": 0, "pattern": {"kick": "x"}, "gate": 2}),
        ("live_clip_write_chords", {"track": 0, "chords": []}),
        ("live_clip_write_chords", {"track": 0, "chords": ["C"], "voicing": "wide"}),
        ("live_clip_write_chords", {"track": 0, "chords": [{"start": 1}]}),
    ]
    for name, args in checks:
        _t, data = call_tool(app, name, args)
        assert isinstance(data, dict) and data.get("type") == "bad_args", (name, args, data)
    assert len(fake.requests) == count


# --------------------------------------------------------------------------
# g2: keys and scales, Roman numerals, arpeggios, note editing, selection, kits
# --------------------------------------------------------------------------

from live_stub import factory  # noqa: E402


def test_add_notes_tool_creates_a_clip_by_default(tcp_bridge, song):
    """Regression (low finding): live_clip_add_notes defaulted create/extend to false, so the
    common "new clip with these notes" failed on an empty slot."""
    client = BridgeClient(host="127.0.0.1", port=tcp_bridge.port, timeout=5.0)
    try:
        app = create_app(client)
        _t, result = call_tool(app, "live_clip_add_notes", {
            "track": "Bass", "slot": 3, "notes": [["C3", 0, 1], ["E3", 6, 1]]})
        assert result["created"] is True and result["added"] == 2
        assert song.tracks[0].clip_slots[3].clip.loop_end == 8.0
    finally:
        client.close()


def test_parse_key_and_scale_math(bridge, song):
    ctx = bridge.ctx
    song.root_note, song.scale_name = 9, "Minor"
    key = theory.parse_key(ctx, None)
    assert (key.root, key.intervals, key.name) == (9, (0, 2, 3, 5, 7, 8, 10), "Minor")
    assert theory.parse_key(ctx, "F# dorian").label() == "F# dorian"
    assert theory.parse_key(ctx, "Bb").flats is True and theory.parse_key(ctx, "Bb").name == \
        "major"
    assert theory.parse_key(ctx, "Am").intervals == theory.SCALES["minor"]
    assert theory.parse_key(ctx, "d minor").flats is True           # one flat
    assert theory.parse_key(ctx, {"root": "E", "scale": "phrygian"}).intervals == \
        (0, 1, 3, 5, 7, 8, 10)
    assert theory.parse_key(ctx, {"root": 2, "intervals": [0, 3, 7]}).intervals == (0, 3, 7)
    c_major = theory.parse_key(ctx, "C major")
    assert c_major.step(60, 2) == 64 and c_major.step(64, -2) == 60
    assert c_major.step(71, 1) == 72 and c_major.step(61, 1) == 63     # C# keeps its offset
    assert c_major.snap(61) == 60 and c_major.snap(61, "up") == 62
    assert c_major.snap(66) == 65
    for bad in ("H minor", {"root": "C", "scale": "zigzag"}, 5, {"scale": "minor"}):
        with pytest.raises(BridgeError):
            theory.parse_key(ctx, bad)


def test_flat_spelling_and_theory_with_keys(bridge, song):
    result = run(bridge, "notes.theory", chords=["Bbsus2", "Ebmaj7", "F#m"])
    assert result["chords"][0]["notes"] == ["Bb3", "C4", "F4"]
    assert result["chords"][1]["notes"][0] == "Eb3" and result["chords"][2]["notes"][0] == "F#3"
    roman = run(bridge, "notes.theory", chords="I V vi IV ii7 vii° bVII", key="C major")
    assert [c["notes"] for c in roman["chords"]][:4] == [
        ["C3", "E3", "G3"], ["G3", "B3", "D4"], ["A3", "C4", "E4"], ["F3", "A3", "C4"]]
    assert roman["chords"][4]["notes"] == ["D3", "F3", "A3", "C4"]
    assert roman["chords"][5]["notes"] == ["B3", "D4", "F4"]
    assert roman["chords"][6]["notes"] == ["Bb3", "D4", "F4"]
    minor = run(bridge, "notes.theory", chords=["i", "III", "57"], key="A minor", scale=True)
    assert minor["chords"][0]["notes"] == ["A3", "C4", "E4"]
    assert minor["chords"][1]["notes"] == ["C3", "E3", "G3"]
    assert minor["chords"][2]["notes"] == ["E3", "G3", "B3", "D4"]    # v7 in natural minor
    assert minor["key"]["notes"] == ["A3", "B3", "C4", "D4", "E4", "F4", "G4"]
    flats = run(bridge, "notes.theory", notes=[70, 61], key="F major")
    assert [n["name"] for n in flats["notes"]] == ["Bb3", "Db3"]
    assert [n["in_key"] for n in flats["notes"]] == [True, False]
    assert fail(bridge, "notes.theory", chords=["V/V"], key="C")["type"] == "bad_args"


def test_write_chords_with_numerals_in_the_song_key(bridge, song):
    song.root_note, song.scale_name = 2, "Minor"                       # D minor
    result = run(bridge, "notes.write_chords", track="Bass", slot=2, chords="i iv v VI",
                 duration=2)
    assert result["key"] == "D Minor"
    assert [c["notes"][0] for c in result["chords"]] == ["D3", "G3", "A3", "Bb3"]
    degrees = run(bridge, "notes.write_chords", track="Bass", slot=3, chords=["1", "57"],
                  key="G major")
    assert degrees["chords"][1]["pitches"] == [62, 66, 69, 72]         # D7


def test_transform_scale_steps_fit_invert_retrograde_and_shift(bridge, song):
    clip = factory.add_clip(song.tracks[0], slot=2, length=4.0, name="Line",
                            notes=[(60, 0.0, 0.5, 100), (62, 1.0, 0.5, 100), (64, 2.0, 1.0, 100),
                                   (66, 3.0, 0.5, 100)])
    steps = run(bridge, "notes.transform", track=0, slot=2, transpose_steps=2, key="C major",
                pitch_max=64)
    assert steps["key"] == "C major" and steps["operations"] == ["transpose_steps"]
    by_time = lambda: [(n.start_time, n.pitch) for n in  # noqa: E731
                       sorted(clip.get_all_notes_extended(), key=lambda n: n.start_time)]
    assert by_time() == [(0.0, 64), (1.0, 65), (2.0, 67), (3.0, 66)]   # F# left alone
    fitted = run(bridge, "notes.transform", track=0, slot=2, fit_scale=True, key="C major")
    assert fitted["fitted"] == 1 and by_time()[3] == (3.0, 65)          # tie -> down
    run(bridge, "notes.replace", track=0, slot=2,
        notes=[[60, 0, 1], [64, 1, 1], [67, 2, 2]])
    inverted = run(bridge, "notes.transform", track=0, slot=2, invert=True)
    assert inverted["axis"] == 60
    assert sorted((n.start_time, n.pitch) for n in clip.get_all_notes_extended()) == \
        [(0.0, 60), (1.0, 56), (2.0, 53)]
    run(bridge, "notes.transform", track=0, slot=2, retrograde=True)
    assert sorted((n.start_time, n.pitch, n.duration) for n in clip.get_all_notes_extended()) \
        == [(0.0, 53, 2.0), (2.0, 56, 1.0), (3.0, 60, 1.0)]
    shifted = run(bridge, "notes.transform", track=0, slot=2, shift=0.5, pitch=53)
    assert shifted["operations"] == ["shift"]
    assert sorted(n.start_time for n in clip.get_all_notes_extended()) == [0.5, 2.0, 3.0]
    early = fail(bridge, "notes.transform", track=0, slot=2, shift=-1)
    assert early["type"] == "invalid_state"
    assert fail(bridge, "notes.transform", track=0, slot=2,
                fit_scale="sideways")["type"] == "bad_args"
    assert fail(bridge, "notes.transform", track=0, slot=2, invert="C3",
                transpose=100)["type"] == "invalid_state"


def test_duplicate_notes_region(bridge, song):
    clip = song.tracks[0].clip_slots[0].clip          # Bass Loop: 36@0 36@1 43@2 36@3
    result = run(bridge, "notes.duplicate", track=0, slot=0, start=0, end=2, times=3)
    assert result["at"] == [2.0, 4.0, 6.0] and result["copied"] == 2
    assert result["extended_to"] == 8.0 and clip.loop_end == 8.0
    starts = sorted((n.start_time, n.pitch) for n in clip.get_all_notes_extended())
    assert (4.0, 36) in starts and (5.0, 36) in starts and (6.0, 36) in starts
    up = run(bridge, "notes.duplicate", track=0, slot=0, start=0, end=1, destination=16,
             transpose=7, extend=False)
    assert "extended_to" not in up
    assert (16.0, 43) in [(n.start_time, n.pitch) for n in clip.get_all_notes_extended()]
    in_key = run(bridge, "notes.duplicate", track=0, slot=0, start=2, end=3, destination=12,
                 transpose_steps=1, key="C major")
    assert in_key["key"] == "C major"
    assert (12.0, 45) in [(n.start_time, n.pitch) for n in clip.get_all_notes_extended()]
    assert fail(bridge, "notes.duplicate", track=0, slot=0, start=0.1,
                end=0.2)["type"] == "not_found"
    assert fail(bridge, "notes.duplicate", track=0, slot=0, start=2,
                end=1)["type"] == "bad_args"
    assert fail(bridge, "notes.duplicate", track=0, slot=0, transpose=100)["type"] == \
        "invalid_state"


def test_select_notes_and_work_on_the_selection(bridge, song):
    clip = song.tracks[0].clip_slots[0].clip
    empty = run(bridge, "notes.select", track=0, slot=0)
    assert empty["selected"] == 0
    chosen = run(bridge, "notes.select", track=0, slot=0, pitch=36)
    assert chosen["selected"] == 3
    more = run(bridge, "notes.select", track=0, slot=0, pitch=43, add=True)
    assert more["selected"] == 4
    run(bridge, "notes.select", track=0, slot=0, none=True)
    run(bridge, "notes.select", track=0, slot=0, start=2, end=4)
    got = run(bridge, "notes.get", track=0, slot=0, selected=True)
    assert [row[1] for row in got["notes"]] == [2.0, 3.0]
    run(bridge, "notes.transform", track=0, slot=0, selected=True, transpose=12)
    assert sorted(n.pitch for n in clip.get_all_notes_extended()) == [36, 36, 48, 55]
    removed = run(bridge, "notes.clear", track=0, slot=0, selected=True)
    assert removed["removed"] == 2
    everything = run(bridge, "notes.select", track=0, slot=0, all=True)
    assert everything["selected"] == 2
    assert fail(bridge, "notes.select", track=0, slot=0, all=True,
                none=True)["type"] == "bad_args"


def test_get_pitch_names_use_lives_spelling(bridge, song, monkeypatch):
    clip = song.tracks[0].clip_slots[0].clip
    monkeypatch.setattr(type(clip), "note_number_to_name",
                        lambda self, pitch: "B♭%d" % (pitch // 12 - 2) if pitch % 12 == 10
                        else theory.midi_to_note(pitch))
    run(bridge, "notes.add", track=0, slot=0, notes=[[70, 0.5, 0.25]])
    got = run(bridge, "notes.get", track=0, slot=0, pitch_names=True, pitch=70)
    assert got["notes"][0][0] == "B♭3"


def test_write_arp_styles(bridge, song):
    up = run(bridge, "notes.write_arp", track="Bass", slot=2, chords="Am C", rate="1/8",
             duration=2, octaves=1)
    clip = song.tracks[0].clip_slots[2].clip
    first = sorted((n.start_time, n.pitch) for n in clip.get_all_notes_extended())[:4]
    assert first == [(0.0, 69), (0.5, 72), (1.0, 76), (1.5, 69)]
    assert up["chords"][0]["notes"] == ["A3", "C4", "E4"] and up["style"] == "up"
    bass = run(bridge, "notes.write_arp", track="Bass", slot=3, chords="i iv", key="A minor",
               style="root_fifth", octave=1, rate="1/4", duration=4)
    clip3 = song.tracks[0].clip_slots[3].clip
    assert [n.pitch for n in sorted(clip3.get_all_notes_extended(),
                                    key=lambda n: n.start_time)][:4] == [45, 52, 45, 52]
    assert bass["key"] == "A minor"
    chord = run(bridge, "notes.write_arp", track="Drums", slot=1, chords=["C"], style="chord",
                rate="1/4", duration=1)
    assert chord["added"] == 3
    rnd1 = run(bridge, "notes.write_arp", track="Bass", slot=2, chords="C", style="random",
               seed=5)
    assert rnd1["seed"] == 5
    for args in ({"chords": "C", "style": "sideways"}, {"chords": [], "style": "up"},
                 {"chords": "C", "octaves": 9}, {"chords": "N.C."}):
        assert fail(bridge, "notes.write_arp", track="Bass", slot=2, **args)["type"] == \
            "bad_args", args


def test_write_pattern_maps_names_to_the_loaded_kit(bridge, song):
    track = factory.add_track(song, "Kit808", "midi")
    factory.add_drum_rack(track, name="808 Kit",
                          pads=((36, "BD 808"), (37, "HH Closed"), (38, "Clap Big"),
                                (39, "Open Hat"), (40, "Perc 2")))
    result = run(bridge, "notes.write_pattern", track="Kit808",
                 pattern={"kick": "x...", "hat": "..x.", "clap": "....", "ohh": "...x",
                          "Perc 2": "x...", "snare": "x..."})
    assert result["rows"] == {"kick": 36, "hat": 37, "clap": 38, "ohh": 39, "Perc 2": 40,
                              "snare": 38}
    assert result["kit"]["rows"]["snare"] == {"note": 38, "how": "general midi",
                                             "pad": "Clap Big"}
    kit = result["kit"]
    assert kit["device"] == "808 Kit" and kit["rows"]["hat"] == {"note": 37, "how": "pad name",
                                                               "pad": "HH Closed"}
    assert kit["rows"]["kick"]["pad"] == "BD 808"
    plain = run(bridge, "notes.write_pattern", track="Kit808", slot=1, kit=False,
                pattern={"hat": "x...", "ride": "x..."})
    assert plain["rows"] == {"hat": 42, "ride": 51} and "kit" not in plain
    gm = run(bridge, "notes.write_pattern", track="Kit808", slot=2, pattern={"ride": "x..."})
    assert gm["rows"] == {"ride": 51} and "empty" in gm["warnings"][0]
    missing = fail(bridge, "notes.write_pattern", track="Kit808", slot=3, pattern={"Tabla": "x"})
    assert missing["type"] == "bad_args" and "Perc 2" in missing["message"]
    no_rack = fail(bridge, "notes.write_pattern", track="Bass", slot=3,
                   pattern={"Perc 2": "x"})
    assert no_rack["type"] == "bad_args"
    # the GM-laid-out fixture kit keeps its notes
    gm_kit = run(bridge, "notes.write_pattern", track="Drums", slot=1,
                 pattern={"kick": "x...", "snare": "..x.", "hat": "xxxx"})
    assert gm_kit["rows"] == {"kick": 36, "snare": 38, "hat": 42}


def test_new_note_tools_forward(mcp):
    app, fake = mcp
    for cmd in ("notes.transform", "notes.duplicate", "notes.select", "notes.write_arp",
                "notes.write_chords", "notes.write_pattern", "notes.get"):
        fake.set_result(cmd, {"ok": cmd})
    call_tool(app, "live_clip_transform_notes", {"track": 0, "slot": 0, "transpose_steps": 2,
                                                 "fit_scale": "up", "key": "A minor",
                                                 "invert": "C3", "retrograde": True,
                                                 "shift": 1, "selected": True})
    assert last(fake)["args"] == {"track": 0, "slot": 0, "selected": True, "shift": 1.0,
                                  "retrograde": True, "transpose_steps": 2, "invert": 60,
                                  "fit_scale": "up", "key": "A minor"}
    call_tool(app, "live_clip_duplicate_notes", {"track": 0, "slot": 0, "start": 0, "end": 4,
                                                 "times": 3, "transpose_steps": 1,
                                                 "key": "song", "pitch": "kick"})
    assert last(fake)["args"] == {"track": 0, "slot": 0, "start": 0.0, "end": 4.0,
                                  "pitch": 36, "times": 3, "transpose_steps": 1,
                                  "key": "song"}
    call_tool(app, "live_clip_select_notes", {"clip": "selected", "pitch_min": "C3",
                                              "add": True})
    assert last(fake)["args"] == {"clip": "selected", "pitch_min": 60, "add": True}
    call_tool(app, "live_clip_write_arp", {"track": "Keys", "chords": "i VI III VII",
                                           "key": "A minor", "style": "updown"})
    args = last(fake)["args"]
    assert args["chords"] == ["i", "VI", "III", "VII"] and args["style"] == "updown"
    assert args["key"] == "A minor" and args["rate"] == "1/16"
    call_tool(app, "live_clip_write_chords", {"track": "Keys", "chords": "I V vi IV",
                                              "key": "G major"})
    assert last(fake)["args"]["key"] == "G major"
    call_tool(app, "live_clip_write_pattern", {"track": "Drums", "pattern": {"Perc 2": "x."},
                                               "kit": False})
    assert last(fake)["args"]["kit"] is False and "Perc 2" in last(fake)["args"]["pattern"]
    call_tool(app, "live_clip_get_notes", {"track": 0, "slot": 0, "selected": True})
    assert last(fake)["args"]["selected"] is True
    count = len(fake.requests)
    for name, args in [
        ("live_clip_transform_notes", {"track": 0, "slot": 0, "fit_scale": "sideways"}),
        ("live_clip_transform_notes", {"track": 0, "slot": 0, "invert": "H9"}),
        ("live_clip_duplicate_notes", {"track": 0, "slot": 0, "times": 0}),
        ("live_clip_duplicate_notes", {"track": 0, "slot": 0, "start": 4, "end": 2}),
        ("live_clip_select_notes", {"track": 0, "slot": 0, "all": True, "none": True}),
        ("live_clip_write_arp", {"track": 0, "chords": "C", "style": "zigzag"}),
        ("live_clip_write_arp", {"chords": "C"}),
        ("live_clip_write_arp", {"track": 0, "chords": "C", "octaves": 9}),
        ("live_clip_write_pattern", {"track": 0, "pattern": {"X9": "x"}}),
    ]:
        _t, data = call_tool(app, name, args)
        assert isinstance(data, dict) and data.get("type") == "bad_args", (name, data)
    assert len(fake.requests) == count
