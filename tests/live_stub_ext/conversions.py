"""Stub extension: ``Live.Conversions`` (absent from the shared stub).

Measured on Live 12.4.5 (g2 fixer, 2026-09-10, scratch audio track with a Core Library loop):

* ``is_convertible_to_midi(song, audio_clip)`` -> bool (raises for a MIDI clip);
* ``audio_to_midi_clip(song, audio_clip, type)`` returns None and runs in the **background**:
  the new MIDI track ("Drums to MIDI" for type 2) appears after the call returned, right
  after the source track, with a Drum Rack and the extracted clip in slot 0;
* ``create_drum_rack_from_audio_clip(song, clip)`` and ``create_midi_track_with_simpler(song,
  clip)`` return None and add their track at once ("Drum Rack" / named after the clip) and
  select it.

``install(Live)`` adds ``Live.Conversions`` and returns a namespace with ``calls`` (a list of
(name, args)), ``finish_background()`` (creates the pending "… to MIDI" tracks) and
``uninstall()``.
"""

import types

from live_stub import factory

_TYPES = {0: "Harmony", 1: "Melody", 2: "Drums"}


def install(Live):
    """Add a fake ``Live.Conversions`` module."""
    calls = []
    pending = []

    def _check_audio(clip):
        if clip.is_midi_clip:
            raise RuntimeError("Only audio clips can be converted")

    def is_convertible_to_midi(song, audio_clip):
        _check_audio(audio_clip)
        calls.append(("is_convertible_to_midi", (audio_clip,)))
        return True

    def audio_to_midi_clip(song, audio_clip, audio_to_midi_type):
        _check_audio(audio_clip)
        calls.append(("audio_to_midi_clip", (audio_clip, int(audio_to_midi_type))))
        pending.append((song, audio_clip, int(audio_to_midi_type)))

    def create_drum_rack_from_audio_clip(song, audio_clip):
        _check_audio(audio_clip)
        calls.append(("create_drum_rack_from_audio_clip", (audio_clip,)))
        track = factory.add_track(song, "Drum Rack", "midi")
        factory.add_drum_rack(track, pads=((36, audio_clip.name),))
        song.view.selected_track = track

    def create_midi_track_with_simpler(song, audio_clip):
        _check_audio(audio_clip)
        calls.append(("create_midi_track_with_simpler", (audio_clip,)))
        track = factory.add_track(song, audio_clip.name, "midi")
        factory.add_simpler(track, audio_clip.name, file_path=audio_clip.file_path)
        song.view.selected_track = track

    def finish_background():
        while pending:
            song, clip, kind = pending.pop(0)
            track = factory.add_track(song, "%s to MIDI" % _TYPES[kind], "midi")
            factory.add_clip(track, slot=0, length=clip.length, name="MIDI " + clip.name)

    module = types.SimpleNamespace(
        is_convertible_to_midi=is_convertible_to_midi, audio_to_midi_clip=audio_to_midi_clip,
        create_drum_rack_from_audio_clip=create_drum_rack_from_audio_clip,
        create_midi_track_with_simpler=create_midi_track_with_simpler,
        AudioToMidiType=types.SimpleNamespace(harmony_to_midi=0, melody_to_midi=1,
                                              drums_to_midi=2))
    had = hasattr(Live, "Conversions")
    previous = getattr(Live, "Conversions", None)
    Live.Conversions = module

    def uninstall():
        if had:
            Live.Conversions = previous
        else:
            del Live.Conversions

    return types.SimpleNamespace(calls=calls, finish_background=finish_background,
                                 uninstall=uninstall)
