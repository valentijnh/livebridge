"""Stub extension: the Drum Rack / Simpler functions of ``Live.Conversions`` (Live 12,
docs/LIVE_API_DUMP_12.4.5.md "Module-level functions") used by racks.convert and
simpler.action "to_drum_rack"::

    create_midi_track_from_drum_pad(song, drum_pad) -> None
    move_devices_on_track_to_new_drum_rack_pad(song, track_index) -> DrumPad
    sliced_simpler_to_drum_rack(song, simpler) -> None  (raises on a non-sliced Simpler)

Verified on Live 12.4.5 (g3 fixer): ``create_midi_track_from_drum_pad`` adds a MIDI track
named after the pad's chain with a copy of its devices (the pad stays);
``move_devices_on_track_to_new_drum_rack_pad`` returns the new Drum Rack's C1 pad.

The audio-clip conversions live in ``live_stub_ext/conversions.py`` (clips tests). This
module adds its functions to whatever ``Live.Conversions`` exists (creating it when there is
none); ``uninstall(Live)`` removes exactly what ``install`` added.
"""

import types

_STATE = {}


def _new_midi_track(song):
    return song.create_midi_track(-1)


def _copy_devices(devices, host):
    for device in devices:
        sample = getattr(device, "sample", None)
        name = "Simpler" if device.class_name == "OriginalSimpler" else device.name
        copy = host.insert_device(name, -1)
        if sample is not None:
            copy.replace_sample(sample.file_path)


def create_midi_track_from_drum_pad(song, drum_pad, /):
    chains = list(drum_pad.chains)
    if not chains:
        raise RuntimeError("create_midi_track_from_drum_pad: the pad is empty")
    track = _new_midi_track(song)
    track.name = chains[0].name
    _copy_devices(list(chains[0].devices), track)


def move_devices_on_track_to_new_drum_rack_pad(song, track_index, /):
    """Like Live 12.4.5: a new track is built after the *selected* track, the devices go
    onto the C1 pad of its new Drum Rack, and the original track is deleted — so the
    track's index can change and the old Track object is dead."""
    old = song.tracks[track_index]
    selected = song.view.selected_track
    tracks = list(song.tracks)
    position = next((i for i, t in enumerate(tracks) if t is selected), track_index) + 1
    track = song.create_midi_track(position)
    track.name = old.name
    rack = track.insert_device("Drum Rack", -1)
    chain = rack.insert_chain(-1)
    chain.in_note = 36
    chain.name = old.name
    _copy_devices(list(old.devices), chain)
    song.delete_track([i for i, t in enumerate(song.tracks) if t is old][0])
    return rack.drum_pads[36]


def sliced_simpler_to_drum_rack(song, simpler, /):
    if int(simpler.playback_mode) != 2 or simpler.sample is None:
        raise RuntimeError("Simpler is not in slicing mode")
    host = simpler.canonical_parent
    slices = list(simpler.sample.slices) or [0]
    file_path = simpler.sample.file_path
    index = [i for i, d in enumerate(host.devices) if d is simpler][0]
    host.delete_device(index)
    rack = host.insert_device("Drum Rack", index)
    for number in range(min(len(slices), 92)):
        chain = rack.insert_chain(-1)
        chain.in_note = 36 + number
        chain.name = "Slice %d" % (number + 1)
        chain.insert_device("Simpler", -1).replace_sample(file_path)


_FUNCTIONS = (create_midi_track_from_drum_pad, move_devices_on_track_to_new_drum_rack_pad,
              sliced_simpler_to_drum_rack)


def install(Live):
    """Add the functions (idempotent); returns the ``Live.Conversions`` object."""
    if "added" in _STATE:
        return Live.Conversions
    module = getattr(Live, "Conversions", None)
    created = module is None
    if created:
        module = types.SimpleNamespace()
        Live.Conversions = module
    added = []
    for func in _FUNCTIONS:
        if not hasattr(module, func.__name__):
            setattr(module, func.__name__, func)
            added.append(func.__name__)
    _STATE.update(added=added, created=created, module=module)
    return module


def uninstall(Live):
    """Remove what ``install`` added (and ``Live.Conversions`` itself if it created it)."""
    if "added" not in _STATE:
        return
    module = _STATE["module"]
    for name in _STATE["added"]:
        if hasattr(module, name):
            delattr(module, name)
    if _STATE["created"] and getattr(Live, "Conversions", None) is module:
        del Live.Conversions
    _STATE.clear()
