"""Real-Live behaviour of clips, notes, tracks and arrangement (T2 live test, Live 12.4.5).

Runs the handlers against the shared stub, which mirrors what the running Live 12.4.5 did
(unlooped clip braces, crop / duplicate-loop regions, same-pitch note merging,
``duplicate_clip_slot`` overwriting, ``force_legato`` on empty slots, ``pitch_fine`` carry).
See ``docs/live_test/T2-tracks-clips.md``.
"""

import pytest

import Live  # noqa: F401  (the stub, via conftest)

from live_stub import factory


def run(bridge, cmd, **args):
    response = bridge.dispatch({"id": "t", "cmd": cmd, "args": args})
    assert response["ok"], response
    return response["result"]


def fail(bridge, cmd, **args):
    response = bridge.dispatch({"id": "t", "cmd": cmd, "args": args})
    assert not response["ok"], response
    return response["error"]


def notes_of(clip):
    return sorted((n.pitch, n.start_time, n.duration) for n in clip.get_all_notes_extended())


def unlooped_clip(song, slot=3, length=16.0, notes=()):
    clip = factory.add_clip(song.tracks[0], slot=slot, length=length, name="Unlooped",
                            notes=notes)
    clip.looping = False
    return clip


# --------------------------------------------------------------------------
# the extension itself mirrors Live
# --------------------------------------------------------------------------

def test_extension_models_the_unlooped_brace(song):
    clip = song.tracks[0].clip_slots[0].clip        # looped 0..4
    clip.loop_start, clip.loop_end = 1.0, 3.0
    clip.start_marker = 0.5
    clip.looping = False                            # brace := markers 0.5..4
    assert (clip.loop_start, clip.loop_end, clip.length) == (0.5, 4.0, 3.5)
    clip.start_marker = 2.0                         # ignored, like Live
    assert clip.start_marker == 0.5
    clip.end_marker = 2.0                           # loop_end stays -> length stays
    assert (clip.end_marker, clip.loop_end, clip.length) == (2.0, 4.0, 3.5)
    clip.loop_start = 1.0                           # moves the start marker too
    assert clip.start_marker == 1.0
    with pytest.raises(RuntimeError):
        clip.loop_start = 5.0
    clip.looping = True                             # looped brace comes back
    assert (clip.loop_start, clip.loop_end) == (1.0, 3.0)


# --------------------------------------------------------------------------
# clips.set on unlooped clips
# --------------------------------------------------------------------------

def test_set_markers_of_an_unlooped_clip_moves_what_plays(bridge, song):
    clip = unlooped_clip(song)
    result = run(bridge, "clips.set", track="Bass", slot=3, start_marker=2, end_marker=10)
    assert "adjusted" not in result
    assert (clip.start_marker, clip.end_marker) == (2.0, 10.0)
    assert (clip.loop_start, clip.loop_end, clip.length) == (2.0, 10.0, 8.0)
    # moving the region later than its current end works in one call
    run(bridge, "clips.set", track="Bass", slot=3, start_marker=12, end_marker=14)
    assert (clip.start_marker, clip.end_marker, clip.length) == (12.0, 14.0, 2.0)
    run(bridge, "clips.set", track="Bass", slot=3, start_marker=1, end_marker=8)
    assert (clip.start_marker, clip.end_marker) == (1.0, 8.0)


def test_set_length_and_loop_aliases_on_an_unlooped_clip(bridge, song):
    clip = unlooped_clip(song)
    result = run(bridge, "clips.set", track="Bass", slot=3, length=6)
    assert result["changed"] == ["end_marker"]
    assert (clip.end_marker, clip.loop_end, clip.length) == (6.0, 6.0, 6.0)
    run(bridge, "clips.set", track="Bass", slot=3, loop_start=2)
    assert (clip.start_marker, clip.loop_start, clip.length) == (2.0, 2.0, 4.0)
    run(bridge, "clips.set", track="Bass", slot=3, loop_end=12, end_marker=12)
    assert (clip.end_marker, clip.loop_end) == (12.0, 12.0)
    clash = fail(bridge, "clips.set", track="Bass", slot=3, loop_start=2, start_marker=3)
    assert clash["type"] == "bad_args" and "does not loop" in clash["message"]
    backwards = fail(bridge, "clips.set", track="Bass", slot=3, start_marker=9, end_marker=4)
    assert backwards["type"] == "bad_args"


def test_set_looping_off_then_markers_in_one_call(bridge, song):
    clip = song.tracks[0].clip_slots[1].clip        # looped 0..8
    run(bridge, "clips.set", track="Bass", slot=1, looping=False, start_marker=1, end_marker=5)
    assert (clip.looping, clip.start_marker, clip.end_marker, clip.length) == \
        (False, 1.0, 5.0, 4.0)


def test_set_reports_positions_live_did_not_keep(bridge, song, monkeypatch):
    clip = song.tracks[0].clip_slots[0].clip
    original = type(clip).__dict__["end_marker"]

    def clamped(self, value):
        original.fset(self, min(float(value), 6.0))

    monkeypatch.setattr(type(clip), "end_marker", property(original.fget, clamped))
    result = run(bridge, "clips.set", track="Bass", slot=0, end_marker=10)
    assert result["adjusted"] == {"end_marker": 6}


def test_pitch_fine_carries_into_coarse(bridge, song):
    clip = song.tracks[1].clip_slots[0].clip        # audio "Vox Take"
    result = run(bridge, "clips.set", track="Vocals", slot=0, pitch_fine=60)
    assert (result["clip"]["pitch_coarse"], result["clip"]["pitch_fine"]) == (1, -40.0)
    run(bridge, "clips.set", track="Vocals", slot=0, pitch_coarse=0, pitch_fine=-50)
    assert (clip.pitch_coarse, clip.pitch_fine) == (-1, 50.0)


# --------------------------------------------------------------------------
# duplicate / fire
# --------------------------------------------------------------------------

def test_live_duplicate_clip_slot_overwrites_so_the_command_avoids_it(song):
    bass = song.tracks[0]
    assert bass.duplicate_clip_slot(0) == 1
    assert bass.clip_slots[1].clip.name == "Bass Loop"   # "Bass Fill" is gone


def test_duplicate_without_target_keeps_the_clip_below(bridge, song):
    result = run(bridge, "clips.duplicate", track="Bass", slot=0)
    assert result["target"] == "song.tracks[0].clip_slots[2]"
    assert song.tracks[0].clip_slots[1].clip.name == "Bass Fill"
    assert song.tracks[0].clip_slots[2].clip.name == "Bass Loop"
    assert "created_scene" not in result


def test_duplicate_adds_a_scene_when_no_slot_below_is_free(bridge, song):
    drums = song.tracks[2]
    last = len(song.scenes) - 1
    factory.add_clip(drums, slot=last, length=4.0, name="Last")
    before = len(song.scenes)
    result = run(bridge, "clips.duplicate", track="Drums", slot=last)
    assert len(song.scenes) == before + 1
    assert result["created_scene"] == before
    assert drums.clip_slots[before].clip.name == "Last"


def test_fire_empty_slot_ignores_force_legato(bridge, song):
    with pytest.raises(RuntimeError):
        song.tracks[0].clip_slots[3].fire(force_legato=True)
    result = run(bridge, "clips.fire", track="Bass", slot=3, force_legato=True,
                 launch_quantization="1/4")
    assert result["has_clip"] is False
    fired = run(bridge, "clips.fire", track="Bass", slot=0, force_legato=True)
    assert fired["is_playing"]


# --------------------------------------------------------------------------
# crop / duplicate loop
# --------------------------------------------------------------------------

def test_crop_keeps_the_lead_in_before_the_loop(bridge, song):
    clip = factory.add_clip(song.tracks[0], slot=3, length=12.0, name="Crop",
                            notes=[(60, 1.0, 0.5, 100), (61, 3.0, 0.5, 100),
                                   (62, 5.0, 0.5, 100), (63, 9.0, 0.5, 100)])
    clip.loop_start, clip.loop_end = 4.0, 8.0
    clip.start_marker = 2.0
    result = run(bridge, "clips.crop", track="Bass", slot=3)
    assert notes_of(clip) == [(61, 1.0, 0.5), (62, 3.0, 0.5)]
    assert (clip.start_marker, clip.loop_start, clip.loop_end, clip.end_marker) == \
        (0.0, 2.0, 6.0, 6.0)
    assert result["clip"]["length"] == 4


def test_crop_unlooped_keeps_the_clip_start_to_end(bridge, song):
    notes = [(60, 1.0, 0.5, 100), (61, 3.0, 0.5, 100), (62, 5.0, 0.5, 100),
             (63, 9.0, 0.5, 100)]
    clip = unlooped_clip(song, notes=notes)
    run(bridge, "clips.set", track="Bass", slot=3, start_marker=2, end_marker=8)
    run(bridge, "clips.crop", track="Bass", slot=3)
    assert notes_of(clip) == [(61, 1.0, 0.5), (62, 3.0, 0.5)]
    assert (clip.start_marker, clip.end_marker, clip.loop_end, clip.length) == \
        (0.0, 6.0, 6.0, 6.0)


def test_raw_end_marker_does_not_end_an_unlooped_clip(bridge, song):
    notes = [(60, 1.0, 0.5, 100), (63, 9.0, 0.5, 100)]
    clip = unlooped_clip(song, notes=notes)
    clip.end_marker = 4.0            # Live keeps the clip end in loop_end (16)
    run(bridge, "clips.crop", track="Bass", slot=3)
    assert notes_of(clip) == [(60, 1.0, 0.5), (63, 9.0, 0.5)]
    assert clip.end_marker == clip.loop_end == 16.0   # aligned after the crop


def test_duplicate_loop_pushes_later_notes_back(bridge, song):
    clip = song.tracks[0].clip_slots[0].clip        # loop 0..4, notes at 0 1 2 3
    factory_notes = [(72, 5.0, 0.5, 100)]
    clip.end_marker = 6.0
    clip.add_new_notes(tuple(Live.Clip.MidiNoteSpecification(p, s, d, v)
                             for p, s, d, v in factory_notes))
    run(bridge, "clips.duplicate_loop", track="Bass", slot=0)
    starts = sorted(n.start_time for n in clip.get_all_notes_extended())
    assert starts == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 9.0]
    assert (clip.loop_end, clip.length) == (8.0, 8.0)


def test_duplicate_loop_unlooped_doubles_the_played_region(bridge, song):
    clip = unlooped_clip(song, length=8.0, notes=[(60, 1.0, 0.5, 100)])
    run(bridge, "clips.set", track="Bass", slot=3, end_marker=4)   # plays 0..4
    result = run(bridge, "clips.duplicate_loop", track="Bass", slot=3)
    assert notes_of(clip) == [(60, 1.0, 0.5), (60, 5.0, 0.5)]
    assert (clip.end_marker, clip.loop_end) == (8.0, 8.0)
    assert result["clip"]["length"] == 8


def test_unlooped_arrangement_clip_end_follows_the_clip_end(bridge, song):
    clip = song.tracks[0].create_midi_clip(16.0, 4.0)
    run(bridge, "clips.set", clip="song.tracks[0].arrangement_clips[1]", looping=False,
        end_marker=6)
    assert (clip.start_time, clip.end_time) == (16.0, 22.0)
    clip.looping = True                              # timeline length stays
    assert clip.end_time == 22.0


# --------------------------------------------------------------------------
# notes: Live merges same-pitch overlaps
# --------------------------------------------------------------------------

def test_add_reports_notes_live_merged(bridge, song):
    clip = factory.add_clip(song.tracks[0], slot=3, length=4.0, name="Merge")
    first = run(bridge, "notes.add", track="Bass", slot=3, notes=[[60, 0, 1]])
    assert first["added"] == 1 and "merged" not in first
    same = run(bridge, "notes.add", track="Bass", slot=3, notes=[[60, 0, 2, 50]])
    assert same["merged"] == 1 and same["added"] == 1
    assert notes_of(clip) == [(60, 0.0, 2.0)]
    inside = run(bridge, "notes.add", track="Bass", slot=3, notes=[[60, 0.5, 1]])
    assert "merged" not in inside
    assert notes_of(clip) == [(60, 0.0, 0.5), (60, 0.5, 1.0)]
    twins = run(bridge, "notes.add", track="Bass", slot=3, notes=[[62, 0, 1], [62, 0, 1]])
    assert twins["added"] == 1 and twins["merged"] == 1 and len(twins["note_ids"]) == 1
    swallow = run(bridge, "notes.add", track="Bass", slot=3, notes=[[64, 1, 1]])
    assert "merged" not in swallow
    covering = run(bridge, "notes.add", track="Bass", slot=3, notes=[[64, 0, 2]])
    assert covering["merged"] == 1
    assert [n for n in notes_of(clip) if n[0] == 64] == [(64, 0.0, 2.0)]


def test_modify_and_transform_report_merges(bridge, song):
    clip = factory.add_clip(song.tracks[0], slot=3, length=4.0, name="Merge",
                            notes=[(62, 0.0, 1.0, 100), (62, 2.0, 1.0, 100)])
    ids = sorted((n.start_time, n.note_id) for n in clip.get_all_notes_extended())
    longer = run(bridge, "notes.modify", track="Bass", slot=3,
                 changes=[{"note_id": ids[0][1], "duration": 3}])
    assert "merged" not in longer
    assert notes_of(clip) == [(62, 0.0, 2.0), (62, 2.0, 1.0)]
    moved = run(bridge, "notes.modify", track="Bass", slot=3,
                changes=[{"note_id": ids[0][1], "start": 2}])
    assert moved["merged"] == 1 and len(notes_of(clip)) == 1
    factory.add_clip(song.tracks[2], slot=1, length=4.0, name="Q",
                     notes=[(60, 0.0, 0.2, 100), (60, 0.1, 0.2, 100), (61, 1.0, 0.5, 100)])
    quantized = run(bridge, "notes.transform", track="Drums", slot=1, quantize="1/4")
    assert quantized["merged"] == 1


def test_pattern_writer_reports_merges(bridge, song):
    result = run(bridge, "notes.write_pattern", track="Bass", slot=3,
                 pattern={"C1": "x...", "36": "..x."}, create=True)
    assert result["added"] == 2 and "merged" not in result


# --------------------------------------------------------------------------
# tracks / arrangement
# --------------------------------------------------------------------------

def test_duplicate_return_track_says_why(bridge):
    error = fail(bridge, "tracks.duplicate", track="A-Reverb")
    assert error["type"] == "bad_args" and "return track" in error["message"]
    master = fail(bridge, "tracks.duplicate", track="master")
    assert "master" in master["message"]


def test_take_lane_rows_are_addressable(bridge, song):
    lane = song.tracks[0].create_take_lane()
    lane.create_midi_clip(16.0, 4.0)
    listing = run(bridge, "arrangement.list", track=0, include_take_lanes=True)
    row = listing["clips"][-1]
    assert row["take_lane"] == lane.name
    assert row["path"] == "song.tracks[0].take_lanes[0].arrangement_clips[0]"
    added = run(bridge, "notes.add", clip=row["path"], notes=[[60, 0, 1]])
    assert added["added"] == 1


def test_back_to_arranger_reports_the_requested_state(bridge, song):
    track = song.tracks[0]
    if not hasattr(track, "back_to_arranger"):
        pytest.skip("stub has no Track.back_to_arranger")
    result = run(bridge, "arrangement.back_to_arranger", track="Bass")
    assert result["back_to_arranger"] is False and "pending" not in result


def test_extend_on_a_looped_arrangement_clip_warns_about_the_timeline(bridge, song):
    clip = song.tracks[0].create_midi_clip(16.0, 4.0)
    path = "song.tracks[0].arrangement_clips[1]"
    result = run(bridge, "notes.add", clip=path, notes=[[60, 6, 1]], extend=True)
    assert result["extended_to"] == 8
    assert result["timeline_end"] == 20 and "looping=false" in result["note"]
    assert clip.end_time == 20.0                      # Live keeps the timeline length
    run(bridge, "clips.set", clip=path, looping=False, end_marker=8)
    run(bridge, "clips.set", clip=path, looping=True)
    assert clip.end_time == 24.0                      # the documented recipe works
    again = run(bridge, "notes.add", clip=path, notes=[[62, 7, 1]], extend=True)
    assert "note" not in again
