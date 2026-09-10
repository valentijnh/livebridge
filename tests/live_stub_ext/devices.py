"""Stub extension for the device/plugin/rack tests: parameters that format their values the way
real Live 12.4.5 does (checked read-only against the running Live).

The shared stub's ``DeviceParameter.str_for_value`` is linear ("%.2f dB"). Real Live maps most
knobs non-linearly and formats them with unit-dependent precision:

* Track/Chain volume 0..1: 0 -> "-inf dB", 0.1 -> "-48.6 dB", 0.4 -> "-18.0 dB",
  0.5 -> "-14.0 dB", 0.85 -> "0.0 dB", 1.0 -> "6.0 dB" (40 dB per unit above ~0.4).
* Frequencies: "830 Hz", "90.0 Hz", "4.50 kHz", "1.00 kHz".
* Times: "2.50 ms", "22.1 ms", "1.00 s", "2.50 s".
* Panning -1..1: "50L" .. "C" .. "50R".
* Percentages: "21 %", "100 %".

``install(Live)`` returns a namespace with:

* ``add_curved_parameter(device, name, curve, value=None)`` — append a parameter whose
  ``str_for_value`` follows ``curve`` ("volume", "freq", "time", "pan", "percent").
* ``add_refusing_parameter(device, name)`` — a parameter whose ``value`` setter raises
  ``RuntimeError`` like Live does for a locked parameter (rollback tests).

Only ``str_for_value`` / the setter differ from the shared stub; everything else (ranges,
read-only-ness, listeners) is the shared ``DeviceParameter``.
"""

import math
import types

_CACHE = {}


def _volume_db(value):
    if value <= 0.0:
        return float("-inf")
    if value >= 0.4:
        return 40.0 * (value - 0.85)
    return -18.0 + 53.0 * math.log10(value / 0.4)


def _format_volume(value):
    db = _volume_db(value)
    if db == float("-inf"):
        return "-inf dB"
    return "%.1f dB" % db


def _format_freq(value):
    hz = 20.0 * (1000.0 ** value)
    if hz >= 1000.0:
        return "%.2f kHz" % (hz / 1000.0)
    if hz >= 100.0:
        return "%d Hz" % int(round(hz))
    return "%.1f Hz" % hz


def _format_time(value):
    ms = 10000.0 ** value
    if ms >= 1000.0:
        return "%.2f s" % (ms / 1000.0)
    if ms >= 10.0:
        return "%.1f ms" % ms
    return "%.2f ms" % ms


def _format_pan(value):
    amount = int(round(abs(value) * 50.0))
    if amount == 0:
        return "C"
    return "%d%s" % (amount, "L" if value < 0 else "R")


def _format_percent(value):
    return "%d %%" % int(round(value * 100.0))


CURVES = {
    "volume": (_format_volume, 0.0, 1.0),
    "freq": (_format_freq, 0.0, 1.0),
    "time": (_format_time, 0.0, 1.0),
    "pan": (_format_pan, -1.0, 1.0),
    "percent": (_format_percent, 0.0, 1.0),
}


def install(Live):
    """Build the helper namespace (idempotent)."""
    if "ns" in _CACHE:
        return _CACHE["ns"]
    base = Live._model.DeviceParameter

    class CurvedParameter(base):
        """A DeviceParameter with a real-Live-like display curve."""

        def __init__(self, name, formatter, minimum, maximum, value, owner):
            base.__init__(self, name, value, minimum, maximum, canonical_parent=owner)
            self._formatter = formatter

        def str_for_value(self, value, /):
            return self._formatter(float(value))

    class RefusingParameter(base):
        """A DeviceParameter whose writes fail like a locked Live parameter."""

        @property
        def value(self):
            return self._value

        @value.setter
        def value(self, new_value):
            raise RuntimeError("Parameter is locked")

    def add_curved_parameter(device, name, curve, value=None):
        formatter, minimum, maximum = CURVES[curve]
        if value is None:
            value = (minimum + maximum) / 2.0
        param = CurvedParameter(name, formatter, minimum, maximum, value, device)
        device._parameters.append(param)
        return param

    def add_refusing_parameter(device, name, value=0.5):
        param = RefusingParameter(name, value, 0.0, 1.0, canonical_parent=device)
        device._parameters.append(param)
        return param

    namespace = types.SimpleNamespace(
        add_curved_parameter=add_curved_parameter,
        add_refusing_parameter=add_refusing_parameter,
        CurvedParameter=CurvedParameter,
        RefusingParameter=RefusingParameter,
        volume_db=_volume_db,
    )
    _CACHE["ns"] = namespace
    return namespace
