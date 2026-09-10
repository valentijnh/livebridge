"""``Live.Song`` — the document (set), its view, cue points and enums."""

from ._model import (  # noqa: F401
    BeatTime, CaptureDestination, CaptureMode, CuePoint, Quantization,
    RecordingQuantization, SessionRecordStatus, SmptTime, Song, SongView,
    TimeFormat, get_all_scales_ordered,
)

Song.View = SongView
