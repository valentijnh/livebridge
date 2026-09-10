"""Fake ``Live`` package used by the LiveBridge test-suite.

It mirrors the module layout and the API of the real Live 12.4 Object Model
(see ``docs/LIVE_API_VERIFIED.md``) closely enough that remote-script code
written against Live 12 runs unchanged:

    import Live
    Live.Application.get_application()
    Live.Clip.MidiNoteSpecification(pitch=60, start_time=0, duration=1, velocity=100)
    Live.Device.DeviceType.instrument
    Live.Track.Track.monitoring_states.AUTO

See ``tests/live_stub/README.md`` and ``tests/live_stub/factory.py``.
"""

from . import Base              # noqa: F401
from . import LomObject         # noqa: F401
from . import Application       # noqa: F401
from . import Song              # noqa: F401
from . import Track             # noqa: F401
from . import TakeLane          # noqa: F401
from . import ClipSlot          # noqa: F401
from . import Clip              # noqa: F401
from . import Envelope          # noqa: F401
from . import Device            # noqa: F401
from . import DeviceParameter   # noqa: F401
from . import RackDevice        # noqa: F401
from . import PluginDevice      # noqa: F401
from . import SimplerDevice     # noqa: F401
from . import Sample            # noqa: F401
from . import DrumPad           # noqa: F401
from . import Chain             # noqa: F401
from . import DrumChain         # noqa: F401
from . import ChainMixerDevice  # noqa: F401
from . import MixerDevice       # noqa: F401
from . import Scene             # noqa: F401
from . import Groove            # noqa: F401
from . import GroovePool        # noqa: F401
from . import Browser           # noqa: F401

__version__ = "12.4.5"
