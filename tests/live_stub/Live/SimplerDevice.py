"""``Live.SimplerDevice`` plus ``PlaybackMode`` / ``SlicingPlaybackMode``."""

from ._model import PlaybackMode, SimplerDevice, SlicingPlaybackMode  # noqa: F401
from ._model import SimplerDeviceView  # noqa: F401

SimplerDevice.View = SimplerDeviceView


def get_available_voice_numbers():
    """``Live.SimplerDevice.get_available_voice_numbers()``."""
    return (1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 14, 16, 20, 24, 32)
