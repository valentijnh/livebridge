"""Stub extension for the automation tests.

Adds what the shared stub cannot express through ``factory`` — all of it exists in real Live:

* ``add_display_parameter(device, name, curve, value=None)`` — a parameter whose
  ``str_for_value`` formats like real Live 12.4.5 (checked read-only on the running Live):
  ``"volume"`` 0..1 -> "-inf dB" / "-18.0 dB" / "0.0 dB" (0.85) / "6.0 dB";
  ``"pan"`` -1..1 -> "50L" / "C" / "50R"; ``"freq"`` 0..1 -> "20.0 Hz" .. "830 Hz" ..
  "20.00 kHz".
* ``set_automation_state(param, state)`` — Live sets ``automation_state`` itself (1 playing
  when a parameter has arrangement automation, 2 overridden after a manual change); the stub
  keeps it in ``_automation_state``.
* ``set_re_enable_flag(song, on)`` — ``song.re_enable_automation_enabled`` (read-only in Live).

``install(Live)`` returns a namespace with these helpers; it patches nothing globally.
"""

import math
import types


def _format_volume(value):
    if value <= 0.0:
        return "-inf dB"
    if value >= 0.4:
        db = 40.0 * (value - 0.85)
    else:
        db = -18.0 + 53.0 * math.log10(value / 0.4)
    return "%.1f dB" % db


def _format_pan(value):
    amount = int(round(abs(value) * 50.0))
    if amount == 0:
        return "C"
    return "%d%s" % (amount, "L" if value < 0 else "R")


def _format_freq(value):
    hz = 20.0 * (1000.0 ** value)
    if hz >= 1000.0:
        return "%.2f kHz" % (hz / 1000.0)
    if hz >= 100.0:
        return "%d Hz" % int(round(hz))
    return "%.1f Hz" % hz


CURVES = {
    "volume": (_format_volume, 0.0, 1.0),
    "pan": (_format_pan, -1.0, 1.0),
    "freq": (_format_freq, 0.0, 1.0),
}


def install(Live):
    """Build the helper namespace."""
    base = Live._model.DeviceParameter

    class DisplayParameter(base):
        """A DeviceParameter with a real-Live-like display curve."""

        def __init__(self, name, formatter, minimum, maximum, value, owner):
            base.__init__(self, name, value, minimum, maximum, canonical_parent=owner)
            self._formatter = formatter

        def str_for_value(self, value, /):
            return self._formatter(float(value))

    def add_display_parameter(device, name, curve, value=None):
        formatter, minimum, maximum = CURVES[curve]
        if value is None:
            value = (minimum + maximum) / 2.0
        param = DisplayParameter(name, formatter, minimum, maximum, value, device)
        device._parameters.append(param)
        return param

    def set_automation_state(param, state):
        param._automation_state = int(state)

    def set_re_enable_flag(song, on=True):
        song._re_enable_automation_enabled = bool(on)

    return types.SimpleNamespace(add_display_parameter=add_display_parameter,
                                 set_automation_state=set_automation_state,
                                 set_re_enable_flag=set_re_enable_flag,
                                 DisplayParameter=DisplayParameter)
