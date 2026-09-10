"""``Live.Clip`` — clips, MIDI notes/specs, warp markers and the clip enums.

``Live.Clip.AutomationEnvelope`` no longer exists in Live 12 — envelopes are
``Live.Envelope.Envelope``.
"""

from ._model import (  # noqa: F401
    Clip, ClipLaunchQuantization, ClipView, GridQuantization, LaunchMode,
    MidiNote, MidiNoteSpecification, MidiNoteVector, WarpMarker, WarpMode,
)

Clip.View = ClipView
WarpMarkerVector = tuple
