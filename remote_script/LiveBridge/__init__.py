"""LiveBridge — the Ableton Live 12 remote script that Claude talks to.

Live imports this package and calls :func:`create_instance` with its opaque
``c_instance`` object.  Everything else (TCP server, dispatcher, LOM access) is
wired up by :class:`~.LiveBridge.LiveBridge`.

Install: copy this folder into Live's User Library ``Remote Scripts`` folder and
enable it under Preferences -> Link, Tempo & MIDI -> Control Surface.

Standard library only — Live embeds its own Python 3.11 and there is no pip.
"""

from .LiveBridge import LiveBridge, Context, VERSION
from .registry import BridgeError, command

__all__ = ["create_instance", "LiveBridge", "Context", "BridgeError", "command",
           "VERSION"]


def create_instance(c_instance):
    """Entry point Live calls to build the control surface."""
    return LiveBridge(c_instance)
