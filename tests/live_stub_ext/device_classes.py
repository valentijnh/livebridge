"""Stub extension: native device classes with class-specific API (Live 12.4.5 dump,
docs/LIVE_API_DUMP_12.4.5.md) — ``WavetableDevice``, ``LooperDevice``,
``HybridReverbDevice`` and ``Eq8Device``, plus their Boost-style enums.

Only what the real classes have is modelled (names, read-only-ness, enum types):

* Wavetable: ``oscillator_1/2_wavetable_category`` / ``_index`` (ints; the wavetable
  list follows the category), ``oscillator_wavetable_categories``, ``oscillator_1/2_
  wavetables``, ``oscillator_1/2_effect_mode`` (EffectMode), ``unison_mode`` (UnisonMode),
  ``filter_routing`` (FilterRouting), ``mono_poly`` (Voicing), ``poly_voices``
  (VoiceCount), ``unison_voice_count``, ``visible_modulation_target_names`` and the
  modulation matrix methods.
* Looper: ``record_length_index``/``record_length_list``, ``overdub_after_record``,
  ``loop_length``/``tempo`` (read-only), transport methods and ``export_to_clip_slot``.
* Hybrid Reverb: ``ir_category_index``/``ir_category_list``, ``ir_file_index``/
  ``ir_file_list`` (follows the category), ``ir_time_shaping_on``.
* Eq8: ``edit_mode`` (a bool in 12.4.5 for EditMode a/b), ``global_mode`` (GlobalMode),
  ``oversample``.

Like the running Live 12.4.5, the enum-typed properties answer plain ints.

``install(Live)`` returns a namespace with ``add_wavetable(host)``, ``add_looper(host)``,
``add_hybrid_reverb(host)``, ``add_eq8(host)``.
"""

import types

_CACHE = {}

_WAVETABLES = {"Basics": ["Basic Shapes", "Saw", "Square"],
               "Collection": ["Ana Fm", "Bells", "Harmonic"],
               "Complex": ["Digital", "Formant"]}
_IRS = {"Halls": ["Big Hall", "Concert Hall"], "Rooms": ["Small Room", "Studio Room"],
        "Plates": ["Plate A"]}


def install(Live):
    """Build the device classes once (idempotent)."""
    if "ns" in _CACHE:
        return _CACHE["ns"]
    model = Live._model
    make_enum = model._make_enum
    Device = model.Device
    DeviceType = model.DeviceType

    EffectMode = make_enum("WavetableDevice.EffectMode", [
        ("none", 0), ("frequency_modulation", 1), ("sync_and_pulse_width", 2),
        ("warp_and_fold", 3)])
    FilterRouting = make_enum("WavetableDevice.FilterRouting", [
        ("serial", 0), ("parallel", 1), ("split", 2)])
    ModulationSource = make_enum("WavetableDevice.ModulationSource", [
        ("amp_envelope", 0), ("envelope_2", 1), ("envelope_3", 2), ("lfo_1", 3),
        ("lfo_2", 4), ("midi_velocity", 5), ("midi_note", 6), ("midi_pitch_bend", 7),
        ("midi_channel_pressure", 8), ("midi_mod_wheel", 9), ("midi_random", 10)])
    UnisonMode = make_enum("WavetableDevice.UnisonMode", [
        ("none", 0), ("classic", 1), ("slow_shimmer", 2), ("fast_shimmer", 3),
        ("phase_sync", 4), ("position_spread", 5), ("random_note", 6)])
    VoiceCount = make_enum("WavetableDevice.VoiceCount", [
        ("two", 0), ("three", 1), ("four", 2), ("five", 3), ("six", 4), ("seven", 5),
        ("eight", 6), ("sixteen", 7)])
    Voicing = make_enum("WavetableDevice.Voicing", [("mono", 0), ("poly", 1)])
    EditMode = make_enum("Eq8Device.EditMode", [("a", 0), ("b", 1)])
    GlobalMode = make_enum("Eq8Device.GlobalMode", [
        ("stereo", 0), ("left_right", 1), ("mid_side", 2)])

    def enum_prop(name, enum):
        """Verified on Live 12.4.5: these properties answer a plain int (not the enum
        member) and accept ints and enum members."""
        key = "_" + name

        def fget(self):
            return int(getattr(self, key))

        def fset(self, value):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError("Python argument types did not match C++ signature: "
                                "%s expects %s" % (name, enum.__name__))
            if int(value) not in enum.values:
                raise RuntimeError("%s: invalid value %d" % (name, int(value)))
            setattr(self, key, int(value))
        return property(fget, fset)

    def int_prop(name, limit=None):
        key = "_" + name

        def fget(self):
            return getattr(self, key)

        def fset(self, value):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError("%s: int expected" % name)
            maximum = limit(self) if limit else None
            if value < 0 or (maximum is not None and value >= maximum):
                raise RuntimeError("%s: index out of range" % name)
            setattr(self, key, value)
        return property(fget, fset)

    def bool_prop(name):
        key = "_" + name

        def fget(self):
            return getattr(self, key)

        def fset(self, value):
            setattr(self, key, bool(value))
        return property(fget, fset)

    categories = list(_WAVETABLES)

    class WavetableDevice(Device):
        """``Live.WavetableDevice.WavetableDevice``."""

        def __init__(self, host):
            Device.__init__(self, "Wavetable", "InstrumentVector", DeviceType.instrument,
                            ({"name": "Osc 1 Pos", "value": 0.0},
                             {"name": "Filter 1 Freq", "value": 0.5},
                             {"name": "Osc 1 Transp", "value": 0.0}), host)
            self._oscillator_1_wavetable_category = 0
            self._oscillator_1_wavetable_index = 0
            self._oscillator_2_wavetable_category = 0
            self._oscillator_2_wavetable_index = 1
            self._oscillator_1_effect_mode = 0
            self._oscillator_2_effect_mode = 0
            self._unison_mode = 0
            self._unison_voice_count = 2
            self._filter_routing = 0
            self._mono_poly = 1
            self._poly_voices = 4
            self._targets = ["Osc 1 Pos", "Filter 1 Freq"]
            self._matrix = {}

        oscillator_wavetable_categories = property(lambda self: list(categories))
        oscillator_1_wavetables = property(
            lambda self: list(_WAVETABLES[categories[self._oscillator_1_wavetable_category]]))
        oscillator_2_wavetables = property(
            lambda self: list(_WAVETABLES[categories[self._oscillator_2_wavetable_category]]))

        def _set_category(self, osc, value):
            if isinstance(value, bool) or not isinstance(value, int) or \
                    not 0 <= value < len(categories):
                raise RuntimeError("invalid wavetable category")
            setattr(self, "_oscillator_%d_wavetable_category" % osc, value)
            setattr(self, "_oscillator_%d_wavetable_index" % osc, 0)

        oscillator_1_wavetable_category = property(
            lambda self: self._oscillator_1_wavetable_category,
            lambda self, v: self._set_category(1, v))
        oscillator_2_wavetable_category = property(
            lambda self: self._oscillator_2_wavetable_category,
            lambda self, v: self._set_category(2, v))
        oscillator_1_wavetable_index = int_prop(
            "oscillator_1_wavetable_index", lambda self: len(self.oscillator_1_wavetables))
        oscillator_2_wavetable_index = int_prop(
            "oscillator_2_wavetable_index", lambda self: len(self.oscillator_2_wavetables))
        oscillator_1_effect_mode = enum_prop("oscillator_1_effect_mode", EffectMode)
        oscillator_2_effect_mode = enum_prop("oscillator_2_effect_mode", EffectMode)
        unison_mode = enum_prop("unison_mode", UnisonMode)
        unison_voice_count = int_prop("unison_voice_count")
        filter_routing = enum_prop("filter_routing", FilterRouting)
        mono_poly = enum_prop("mono_poly", Voicing)
        poly_voices = enum_prop("poly_voices", VoiceCount)
        visible_modulation_target_names = property(lambda self: tuple(self._targets))

        def add_parameter_to_modulation_matrix(self, parameter):
            if "Transp" in parameter.name:
                raise RuntimeError("Pitch parameters cannot be added")
            if parameter.name not in self._targets:
                self._targets.append(parameter.name)
            return self._targets.index(parameter.name)

        def is_parameter_modulatable(self, parameter):
            return "Transp" not in parameter.name

        def get_modulation_target_parameter_name(self, target_index):
            return self._targets[target_index]

        def get_modulation_value(self, target_index, source):
            if not 0 <= target_index < len(self._targets):
                raise RuntimeError("invalid target index")
            return self._matrix.get((target_index, int(source)), 0.0)

        def set_modulation_value(self, target_index, source, value):
            if not 0 <= target_index < len(self._targets):
                raise RuntimeError("invalid target index")
            self._matrix[(target_index, int(source))] = float(value)

    class LooperDevice(Device):
        """``Live.LooperDevice.LooperDevice``."""

        def __init__(self, host):
            Device.__init__(self, "Looper", "Looper", DeviceType.audio_effect,
                            ({"name": "Feedback", "value": 1.0},), host)
            self._record_length_index = 0
            self._overdub_after_record = True
            self._loop_length = 0.0
            self._tempo = 120.0
            self.calls = []
            self._has_audio = False

        record_length_list = property(lambda self: ["x", "1 Bar", "2 Bars", "4 Bars"])
        record_length_index = int_prop("record_length_index", lambda self: 4)
        overdub_after_record = bool_prop("overdub_after_record")
        loop_length = property(lambda self: self._loop_length)
        tempo = property(lambda self: self._tempo)

        def _call(self, name):
            self.calls.append(name)

        def record(self):
            self._call("record")
            self._has_audio = True
            self._loop_length = 8.0

        def overdub(self):
            self._call("overdub")

        def play(self):
            self._call("play")

        def stop(self):
            self._call("stop")

        def clear(self):
            self._call("clear")
            self._has_audio = False

        def undo(self):
            self._call("undo")

        def double_length(self):
            self._call("double_length")

        def half_length(self):
            self._call("half_length")

        def double_speed(self):
            self._call("double_speed")

        def half_speed(self):
            self._call("half_speed")

        def export_to_clip_slot(self, clip_slot):
            # the stub's ClipSlot only makes MIDI clips from code; real Live makes an
            # audio clip — the tests export into a MIDI track's slot
            if clip_slot.has_clip:
                raise RuntimeError("Clip slot is not empty")
            clip_slot.create_clip(self._loop_length or 4.0)
            self.calls.append("export_to_clip_slot")

    ir_categories = list(_IRS)

    class HybridReverbDevice(Device):
        """``Live.HybridReverbDevice.HybridReverbDevice``."""

        def __init__(self, host):
            Device.__init__(self, "Hybrid Reverb", "Hybrid", DeviceType.audio_effect,
                            ({"name": "Dry/Wet", "value": 0.5},), host)
            self._ir_category_index = 0
            self._ir_file_index = 0
            self._ir_time_shaping_on = False
            self._ir_decay_time = 1.0

        ir_category_list = property(lambda self: list(ir_categories))
        ir_file_list = property(lambda self: list(_IRS[ir_categories[
            self._ir_category_index]]))

        def _set_ir_category(self, value):
            if isinstance(value, bool) or not isinstance(value, int) or \
                    not 0 <= value < len(ir_categories):
                raise RuntimeError("invalid IR category")
            self._ir_category_index = value
            self._ir_file_index = 0

        ir_category_index = property(lambda self: self._ir_category_index, _set_ir_category)
        ir_file_index = int_prop("ir_file_index", lambda self: len(self.ir_file_list))
        ir_time_shaping_on = bool_prop("ir_time_shaping_on")

        @property
        def ir_decay_time(self):
            return self._ir_decay_time

        @ir_decay_time.setter
        def ir_decay_time(self, value):
            self._ir_decay_time = float(value)

    class Eq8Device(Device):
        """``Live.Eq8Device.Eq8Device``."""

        def __init__(self, host):
            Device.__init__(self, "EQ Eight", "Eq8", DeviceType.audio_effect,
                            ({"name": "1 Frequency A", "value": 0.2},), host)
            self._edit_mode = False
            self._global_mode = 0
            self._oversample = False

        edit_mode = bool_prop("edit_mode")   # Live 12.4.5 answers a bool (False = a)
        global_mode = enum_prop("global_mode", GlobalMode)
        oversample = bool_prop("oversample")

    def _attach(host, device):
        host._devices.append(device)
        host.notify_listeners("devices")
        return device

    modules = {
        "WavetableDevice": dict(WavetableDevice=WavetableDevice, EffectMode=EffectMode,
                                FilterRouting=FilterRouting,
                                ModulationSource=ModulationSource, UnisonMode=UnisonMode,
                                VoiceCount=VoiceCount, Voicing=Voicing),
        "LooperDevice": dict(LooperDevice=LooperDevice),
        "HybridReverbDevice": dict(HybridReverbDevice=HybridReverbDevice),
        "Eq8Device": dict(Eq8Device=Eq8Device, EditMode=EditMode, GlobalMode=GlobalMode),
    }
    for module_name, members in modules.items():
        if not hasattr(Live, module_name):
            setattr(Live, module_name, types.SimpleNamespace(**members))

    namespace = types.SimpleNamespace(
        add_wavetable=lambda host: _attach(host, WavetableDevice(host)),
        add_looper=lambda host: _attach(host, LooperDevice(host)),
        add_hybrid_reverb=lambda host: _attach(host, HybridReverbDevice(host)),
        add_eq8=lambda host: _attach(host, Eq8Device(host)),
        WavetableDevice=WavetableDevice, LooperDevice=LooperDevice,
        HybridReverbDevice=HybridReverbDevice, Eq8Device=Eq8Device,
        ModulationSource=ModulationSource,
    )
    _CACHE["ns"] = namespace
    return namespace
