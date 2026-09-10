"""Stub extension for the plug-in tests: ``Live.PluginDevice.PluginDevice`` as real Live 12.4.5
behaves (verified 2026-09-10 on macOS with Xfer Serum 2 VST3 + AU, Serum 2 FX and Apple's AUs).

The shared stub's ``PluginDevice`` already behaves like Live for the name list (every
plug-in parameter from ``get_parameter_names()``, never "Device On"; default = the exposed
ones). This module adds the measured plug-ins themselves and the user's Configure action:

* ``get_parameter_names(begin=0, end=-1)`` lists **every** parameter of the plug-in — Serum 2
  VST3: 2623 names (541 synth parameters, then 16 x 130 MIDI proxies "Pitch Bend Chan n",
  "Aftertouch Chan n", "CC0 Chan n" .. "CC127 Chan n", then "Mod Wheel", "Pitch Bend"), AU: 2622 — and
  never "Device On".
* ``parameters`` is "Device On" + the **Configure** list only: Serum 2 (VST3 and AU) and
  Serum 2 FX start with ``["Device On"]`` alone; small AUs are filled automatically
  (AUNBandEQ 41/41 with eight parameters each named "Frequency", "Gain", "Bandwidth",
  "Type", "Bypass"; AUMIDISynth 4/4; AUMatrixReverb 2/17; AUDelay 3/4).
* The name list is dynamic: switching an AUNBandEQ band to "Low Shelf" drops that band's
  "Bandwidth" from ``get_parameter_names`` while the exposed parameter stays.
* ``presets`` is ``("Default",)`` for Serum 2 and every Apple AU tried; ``selected_preset_index``
  0; ``is_editor_open`` is true right after a browser load (Live's "Auto-Open Plug-In Windows").

``install(Live)`` returns a namespace with:

* ``add_real_plugin(host, name, available, exposed=(), kind="instrument",
  class_name="PluginDevice", presets=("Default",), editor_open=False)`` — ``available`` is the
  plug-in's full parameter-name list, ``exposed`` the Configure list: names or
  ``(name, value)`` / ``(name, value, formatter)`` tuples (``formatter(value) -> str``).
* ``configure(device, name, value=0.5, formatter=None)`` — what the user does in Configure
  mode: appends the exposed parameter and fires the ``parameters`` listeners.
* ``hide(device, name, occurrence=1)`` — the plug-in drops a name from its list (AU mode switch).
* ``serum2_names()`` — Serum 2's real VST3 parameter names in Live's order (2623).
"""

import types

_CACHE = {}

_SERUM_OSC = (
    "Enable", "Level", "Pan", "Octave", "Semi", "Fine", "Ratio", "Hz Offset", "Coarse Pitch",
    "Pitch Track", "Start", "End", "Reverse", "Scan Rate", "Scan BPM Rate", "Scan Key Track",
    "Position", "Loop Start", "Loop End", "Loop X-Fade", "Loop Mode", "Relative Loop",
    "Single Slice", "Slice Play Mode", "Unison", "Uni Stack", "Uni Detune", "Uni Blend",
    "Uni Width", "Uni Span", "Uni Rand Start", "Uni Warp", "Uni Warp 2", "Warp", "Warp Var",
    "Warp Mode", "Warp 2", "Warp 2 Var", "Warp 2 Mode", "WT Pos", "Uni WT Pos", "Phase",
    "Rand Phase") + tuple("Param%d" % n for n in range(44, 56))


def serum2_names():
    """Serum 2 (VST3, v2.1.5) parameter names exactly as Live 12.4.5 lists them."""
    names = ["Main Vol", "Main Tuning", "Amp", "Porta Time", "Porta Curve", "Bend Up",
             "Bend Down", "Pitch Bend", "Mod Wheel", "Mono Toggle", "Legato", "Porta Always",
             "Porta Scaled", "Swing", "Swing Div", "Transpose", "Bypass", "Direct Vol",
             "Bus 1 Vol", "Bus 2 Vol"]
    for osc in "ABC":
        names += ["%s %s" % (osc, p) for p in _SERUM_OSC]
    names += ["Noise Enable", "Noise Level", "Noise Pan", "Noise Pitch Track", "Noise Pitch",
              "Noise Fine", "Noise Phase", "Noise Rand Phase", "Sub Enable", "Sub Level",
              "Sub Pan", "Sub Octave", "Sub Coarse Pitch", "Sub Pitch Track", "Sub Shape",
              "Sub Phase", "Sub Cont. Phase"]
    for n in (1, 2):
        names += ["Filter %d %s" % (n, p) for p in ("Level", "On", "Type", "Freq", "Res",
                                                     "Drive", "Var", "Wet", "Stereo", "X", "Y")]
    for n in range(1, 5):
        names += ["Env %d %s" % (n, p) for p in ("Attack", "Hold", "Decay", "Sustain",
                                                  "Release", "Atk Curve", "Dec Curve",
                                                  "Rel Curve")]
        if n > 1:
            names += ["Env %d Start" % n, "Env %d End" % n]
    for n in range(1, 11):
        names += ["LFO %d %s" % (n, p) for p in ("Rate", "Smooth", "Rise", "Delay", "Phase")]
    for n in range(1, 65):
        names += ["Mod %d Amount" % n, "Mod %d Out" % n]
    names += ["Macro %d" % n for n in range(1, 9)]
    for source in ("A", "B", "C", "Noise", "Sub Osc"):
        names += ["%s>Filter Balance" % source, "%s>BUS1" % source, "%s>BUS2" % source]
    names += ["Filter 1>BUS1", "Filter 1>BUS2", "Filter 2>BUS1", "Filter 2>BUS2",
              "Osc Detune Rnd", "Osc Pan Rand", "Env Rand", "Cutoff Rand"]
    for bus in ("Main", "Bus 1", "Bus 2"):
        names += ["FX %s Param %d" % (bus, n) for n in range(1, 17)]
    names += ["Clip Player Enable", "Clip Player Transpose", "Clip Player Rate",
              "Clip Player Offset", "Arp Enable", "Arp Rate", "Arp Shift", "Arp Range",
              "Arp Offset", "Arp Repeats", "Arp Gate", "Arp Chance", "Arp Retrig Rate",
              "Arp Velo Decay", "Arp Velo Target", "Arp Transpose", "Arp Wrap Transpose",
              "Arp Wrap Range", "Arp Wrap Phantom Note", "Key", "Scale", "Bank"]
    for channel in range(1, 17):      # VST3 MIDI-mapping proxies
        names += ["Pitch Bend Chan %d" % channel, "Aftertouch Chan %d" % channel]
        names += ["CC%d Chan %d" % (cc, channel) for cc in range(128)]
    names += ["Mod Wheel", "Pitch Bend"]
    return names


def install(Live):
    """Build the helper namespace (idempotent)."""
    if "ns" in _CACHE:
        return _CACHE["ns"]
    from live_stub import factory
    model = Live._model
    base_device = model.PluginDevice
    base_param = model.DeviceParameter

    class FormattedParameter(base_param):
        """A plug-in parameter (0..1) with the plug-in's own display formatter."""

        def __init__(self, name, value, owner, formatter=None):
            base_param.__init__(self, name, value, 0.0, 1.0, canonical_parent=owner)
            self._formatter = formatter

        def str_for_value(self, value, /):
            if self._formatter is None:
                return base_param.str_for_value(self, value)
            return self._formatter(float(value))

    class RealPluginDevice(base_device):
        """The shared stub's ``PluginDevice`` (whose ``get_parameter_names`` already lists
        every plug-in name, like Live) with an explicit name list and an empty Configure
        list."""

        def __init__(self, name, class_name, type, host, presets, available):
            base_device.__init__(self, name, class_name, type, (), host, presets, available)

    def _param(device, spec):
        if isinstance(spec, str):
            return FormattedParameter(spec, 0.5, device)
        name, value = spec[0], spec[1]
        formatter = spec[2] if len(spec) > 2 else None
        return FormattedParameter(name, value, device, formatter)

    def add_real_plugin(host, name, available, exposed=(), kind="instrument",
                        class_name="PluginDevice", presets=("Default",), editor_open=False,
                        index=-1):
        device = RealPluginDevice(name, class_name, factory._device_type(kind), host, presets,
                                  available)
        for spec in exposed:
            device._parameters.append(_param(device, spec))
        device._is_editor_open = bool(editor_open)
        devices = host._devices
        if index < 0 or index >= len(devices):
            devices.append(device)
        else:
            devices.insert(index, device)
        host.notify_listeners("devices")
        return device

    def configure(device, name, value=0.5, formatter=None):
        param = FormattedParameter(name, value, device, formatter)
        device._parameters.append(param)
        device.notify_listeners("parameters")
        return param

    def hide(device, name, occurrence=1):
        seen = 0
        for i, current in enumerate(device._plugin_names):
            if current == name:
                seen += 1
                if seen == occurrence:
                    del device._plugin_names[i]
                    return
        raise ValueError("%r #%d not in the plug-in's names" % (name, occurrence))

    namespace = types.SimpleNamespace(
        add_real_plugin=add_real_plugin, configure=configure, hide=hide,
        serum2_names=serum2_names, RealPluginDevice=RealPluginDevice,
        FormattedParameter=FormattedParameter,
    )
    _CACHE["ns"] = namespace
    return namespace
