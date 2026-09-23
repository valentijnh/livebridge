"""Internal implementation of the fake Live Object Model.

Everything lives in this single module so the public ``Live.*`` sub-modules
(``Live.Song``, ``Live.Track``, ...) can simply re-export names without any
circular-import problems.

**This stub mirrors the real Live 12.4 Python API** as recorded in
``docs/LIVE_API_VERIFIED.md`` (runtime capture of Live 12.4 + Cycling '74 LOM
docs + Ableton's own factory scripts).  Fidelity rules the stub follows so
that code which passes here also works inside Live:

* enum classes are Boost.Python-style int subclasses with ``.name`` on every
  member and ``values`` / ``names`` dicts on the class (no ``__members__``);
* read-only LOM properties are real read-only ``property`` objects (assigning
  raises ``AttributeError``, exactly like Boost.Python), writable ones are
  ``property`` objects with a setter that validates and notifies listeners;
* int properties reject floats (Boost's int converter does), bool properties
  accept ints;
* methods whose real arguments are unnamed (``arg2``...) are positional-only
  here, methods with real keyword names use those exact names
  (``Track.insert_device(DeviceName, DeviceIndex=-1)``);
* "only available for X" properties raise ``RuntimeError`` when read on the
  wrong kind of object (e.g. ``clip.warping`` on a MIDI clip,
  ``parameter.value_items`` on a non-quantized parameter);
* every device has ``parameters[0] == "Device On"`` and ``is_active`` is
  read-only (toggle a device through ``parameters[0].value``).

Only the standard library is used, and the code stays Python 3.11 compatible.
"""

import itertools
import os
import re

__all__ = [
    # enums
    "DeviceType", "AutomationState", "ParameterState", "WarpMode",
    "GridQuantization", "ClipLaunchQuantization", "LaunchMode",
    "ClipSlotPlayingState", "Quantization", "RecordingQuantization",
    "SessionRecordStatus", "CaptureDestination", "CaptureMode", "TimeFormat",
    "DeviceInsertMode", "RoutingChannelLayout", "RoutingTypeCategory",
    "FilterType", "Relation", "PlaybackMode", "SlicingPlaybackMode",
    "SlicingStyle", "TransientLoopMode", "SlicingBeatDivision", "GrooveBase",
    "NavDirection", "MessageButtons",
    # base
    "LomObject", "LimitationError", "Timer", "MidiNoteVector", "Vector",
    "BrowserItemVector",
    # objects
    "Application", "ApplicationView", "get_application", "set_application",
    "Song", "SongView", "CuePoint", "BeatTime", "SmptTime",
    "get_all_scales_ordered", "Track", "TrackView", "TakeLane", "RoutingType",
    "RoutingChannel", "ClipSlot", "Clip", "ClipView", "MidiNote",
    "MidiNoteSpecification", "WarpMarker", "Envelope", "EnvelopeEvent",
    "EnvelopeEventControlCoefficients", "Device", "DeviceView", "RackDevice",
    "RackDeviceView", "PluginDevice", "SimplerDevice", "Sample", "DrumPad",
    "Chain", "DrumChain", "ChainMixerDevice", "DeviceParameter", "MixerDevice",
    "Scene", "Groove", "GroovePool", "Browser", "BrowserItem",
    "BrowserItemIterator", "NATIVE_DEVICES", "MAX_FOR_LIVE_DEVICES", "live_note_name",
]


# ==========================================================================
# Boost.Python-style enums
# ==========================================================================

class _LiveEnum(int):
    """Base of every stub enum: an ``int`` with a ``name``.

    Mirrors ``Boost.Python.enum``: ``str(member)`` is the member name,
    ``repr(member)`` is ``Module.Enum.member``, the class carries ``values``
    (int -> member) and ``names`` (name -> member).  There is **no**
    ``__members__`` — code must not rely on it.
    """

    _qualname = "Enum"
    values = {}
    names = {}

    def __new__(cls, value=0):
        number = int(value)
        member = cls.values.get(number)
        if member is not None:
            return member
        obj = int.__new__(cls, number)
        obj.name = None
        return obj

    def __repr__(self):
        return "%s.%s" % (self._qualname, self.name if self.name else int(self))

    def __str__(self):
        return self.name if self.name else str(int(self))

    def __reduce__(self):
        return (int, (int(self),))


def _make_enum(qualname, members, doc=""):
    """Build an enum class ``qualname`` (e.g. ``"Clip.WarpMode"``)."""
    cls = type(qualname.split(".")[-1], (_LiveEnum,),
               {"__doc__": doc or qualname, "_qualname": qualname})
    values = {}
    names = {}
    for name, value in members:
        member = int.__new__(cls, value)
        member.name = name
        setattr(cls, name, member)
        values[value] = member
        names[name] = member
    cls.values = values
    cls.names = names
    return cls


DeviceType = _make_enum("Device.DeviceType", [
    ("undefined", 0), ("instrument", 1), ("audio_effect", 2), ("midi_effect", 4)],
    "Live.Device.DeviceType — NOTE midi_effect is 4, not 3.")
AutomationState = _make_enum("DeviceParameter.AutomationState", [
    ("none", 0), ("playing", 1), ("overridden", 2)])
ParameterState = _make_enum("DeviceParameter.ParameterState", [
    ("enabled", 0), ("irrelevant", 1), ("disabled", 2)])
WarpMode = _make_enum("Clip.WarpMode", [
    ("beats", 0), ("tones", 1), ("texture", 2), ("repitch", 3), ("complex", 4),
    ("rex", 5), ("complex_pro", 6), ("count", 7)])
GridQuantization = _make_enum("Clip.GridQuantization", [
    ("no_grid", 0), ("g_8_bars", 1), ("g_4_bars", 2), ("g_2_bars", 3),
    ("g_bar", 4), ("g_half", 5), ("g_quarter", 6), ("g_eighth", 7),
    ("g_sixteenth", 8), ("g_thirtysecond", 9), ("count", 10)],
    "Used by clip.view.grid_quantization only (NOT by clip.quantize).")
ClipLaunchQuantization = _make_enum("Clip.ClipLaunchQuantization", [
    ("q_global", 0), ("q_none", 1), ("q_8_bars", 2), ("q_4_bars", 3),
    ("q_2_bars", 4), ("q_bar", 5), ("q_half", 6), ("q_half_triplet", 7),
    ("q_quarter", 8), ("q_quarter_triplet", 9), ("q_eighth", 10),
    ("q_eighth_triplet", 11), ("q_sixteenth", 12), ("q_sixteenth_triplet", 13),
    ("q_thirtysecond", 14)], "clip.launch_quantization (0 = global).")
LaunchMode = _make_enum("Clip.LaunchMode", [
    ("trigger", 0), ("gate", 1), ("toggle", 2), ("repeat", 3)])
ClipSlotPlayingState = _make_enum("ClipSlot.ClipSlotPlayingState", [
    ("stopped", 0), ("started", 1), ("recording", 2)])
Quantization = _make_enum("Song.Quantization", [
    ("q_no_q", 0), ("q_8_bars", 1), ("q_4_bars", 2), ("q_2_bars", 3),
    ("q_bar", 4), ("q_half", 5), ("q_half_triplet", 6), ("q_quarter", 7),
    ("q_quarter_triplet", 8), ("q_eight", 9), ("q_eight_triplet", 10),
    ("q_sixtenth", 11), ("q_sixtenth_triplet", 12), ("q_thirtytwoth", 13)],
    "song.clip_trigger_quantization (Live's own spelling).")
RecordingQuantization = _make_enum("Song.RecordingQuantization", [
    ("rec_q_no_q", 0), ("rec_q_quarter", 1), ("rec_q_eight", 2),
    ("rec_q_eight_triplet", 3), ("rec_q_eight_eight_triplet", 4),
    ("rec_q_sixtenth", 5), ("rec_q_sixtenth_triplet", 6),
    ("rec_q_sixtenth_sixtenth_triplet", 7), ("rec_q_thirtysecond", 8)],
    "song.midi_recording_quantization AND the grid of clip.quantize().")
SessionRecordStatus = _make_enum("Song.SessionRecordStatus", [
    ("off", 0), ("on", 1), ("transition", 2)])
CaptureDestination = _make_enum("Song.CaptureDestination", [
    ("auto", 0), ("session", 1), ("arrangement", 2)])
CaptureMode = _make_enum("Song.CaptureMode", [("all", 0), ("all_except_selected", 1)])
TimeFormat = _make_enum("Song.TimeFormat", [
    ("ms_time", 0), ("smpte_24", 1), ("smpte_25", 2), ("smpte_30", 3),
    ("smpte_30_drop", 4), ("smpte_29", 5)])
DeviceInsertMode = _make_enum("Track.DeviceInsertMode", [
    ("default", 0), ("selected_left", 1), ("selected_right", 2), ("count", 3)])
RoutingChannelLayout = _make_enum("Track.RoutingChannelLayout", [
    ("midi", 0), ("mono", 1), ("stereo", 2)])
RoutingTypeCategory = _make_enum("Track.RoutingTypeCategory", [
    ("external", 0), ("rewire", 1), ("resampling", 2), ("master", 3),
    ("track", 4), ("parent_group_track", 5), ("none", 6), ("invalid", 7)],
    "Values 5-7 are inferred (the runtime dict repr is truncated).")
FilterType = _make_enum("Browser.FilterType", [
    ("disabled", -1), ("hotswap_off", 0), ("instrument_hotswap", 1),
    ("audio_effect_hotswap", 2), ("midi_effect_hotswap", 3),
    ("drum_pad_hotswap", 4), ("midi_track_devices", 5), ("samples", 6),
    ("count", 7)], "Values 3-7 are inferred (runtime dict repr truncated).")
Relation = _make_enum("Browser.Relation", [
    ("ancestor", 0), ("equal", 1), ("descendant", 2), ("none", 3)])
PlaybackMode = _make_enum("SimplerDevice.PlaybackMode", [
    ("classic", 0), ("one_shot", 1), ("slicing", 2)])
SlicingPlaybackMode = _make_enum("SimplerDevice.SlicingPlaybackMode", [
    ("mono", 0), ("poly", 1), ("thru", 2)])
SlicingStyle = _make_enum("Sample.SlicingStyle", [
    ("transient", 0), ("beat", 1), ("region", 2), ("manual", 3)])
TransientLoopMode = _make_enum("Sample.TransientLoopMode", [
    ("off", 0), ("forward", 1), ("alternate", 2)])
SlicingBeatDivision = _make_enum("Sample.SlicingBeatDivision", [
    ("sixteenth", 0), ("sixteenth_triplett", 1), ("eighth", 2),
    ("eighth_triplett", 3), ("quarter", 4), ("quarter_triplett", 5),
    ("half", 6), ("half_triplett", 7), ("one_bar", 8), ("two_bars", 9),
    ("four_bars", 10)], "Values 3-10 are inferred (runtime dict repr truncated).")
GrooveBase = _make_enum("Groove.Base", [
    ("gb_four", 0), ("gb_eight", 1), ("gb_eight_triplet", 2), ("gb_sixteen", 3),
    ("gb_sixteen_triplet", 4), ("gb_thirtytwo", 5), ("count", 6)])
NavDirection = _make_enum("Application.View.NavDirection", [
    ("up", 0), ("down", 1), ("left", 2), ("right", 3)])
MessageButtons = _make_enum("Application.MessageButtons", [
    ("OK_BUTTON", 0), ("OK_NEW_SET_BUTTON", 1), ("OK_RETRY_BUTTON", 2),
    ("SAVE_DONT_SAVE_BUTTON", 3), ("OK_ACCOUNT_BUTTON", 4),
    ("OK_PURCHASE_BUTTON", 5)])

_MONITORING_STATES = _make_enum("Track.monitoring_states", [
    ("IN", 0), ("AUTO", 1), ("OFF", 2)],
    "Live.Track.Track.monitoring_states — there is NO Live.Track.MonitoringState.")
_CROSSFADE_ASSIGNMENTS = _make_enum("MixerDevice.crossfade_assignments", [
    ("A", 0), ("NONE", 1), ("B", 2)],
    "Values verified (0=A, 1=none, 2=B); member spelling is UNVERIFIED.")
_PANNING_MODES = _make_enum("MixerDevice.panning_modes", [
    ("stereo", 0), ("stereo_split", 1)])


# ==========================================================================
# Live.Base
# ==========================================================================

class LimitationError(Exception):
    """``Live.Base.LimitationError`` — raised when an edition/track limit is hit."""


class Timer(object):
    """``Live.Base.Timer(callback, interval, repeat=False, start=False)``.

    ``interval`` is in milliseconds.  The stub never fires on its own; tests
    call :meth:`fire` to simulate Live's main-thread timer tick.
    """

    def __init__(self, callback, interval, repeat=False, start=False):
        self._callback = callback
        self._interval = int(interval)
        self._repeat = bool(repeat)
        self._running = bool(start)

    @property
    def running(self):
        return self._running

    def start(self):
        self._running = True

    def stop(self):
        self._running = False

    def restart(self):
        self._running = True

    def fire(self):
        """Stub-only: run the callback once (errors stop the timer, like Live)."""
        if not self._running:
            return
        try:
            self._callback()
        except Exception:
            self._running = False
            raise
        if not self._repeat:
            self._running = False


class Vector(object):
    """``Live.Base.Vector`` — "A simple read only container for returning objects from Live".

    On Live 12.4.5 every LOM collection (``song.tracks``, ``scenes``, ``clip_slots``,
    ``devices``, ``parameters``, ``chains``, ``arrangement_clips``, ``take_lanes``, mixer
    ``sends`` ...) is one of these: ``type(song.tracks).__mro__ == (Vector,
    Boost.Python.instance, object)`` — **not** a list or tuple, so ``isinstance(x, (list,
    tuple))`` is False.  Supports ``len()``, indexing (negative too), slicing, iteration and
    ``in``; there is no ``index()`` / ``count()`` and no ``+``; equality is identity (a
    Boost instance).  Wrap it in ``list()`` for anything else.
    """

    __slots__ = ("_items",)

    def __init__(self, items=()):
        self._items = tuple(items)

    def __len__(self):
        return len(self._items)

    def __iter__(self):
        return iter(self._items)

    def __contains__(self, item):
        return any(other is item or other == item for other in self._items)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return type(self)(self._items[index])
        if isinstance(index, bool) or not isinstance(index, int):
            raise TypeError("Invalid index type")
        return self._items[index]

    def append(self, item):
        raise RuntimeError("%s is a read only container" % type(self).__name__)

    def extend(self, items):
        raise RuntimeError("%s is a read only container" % type(self).__name__)

    def __repr__(self):
        return "<%s %r>" % (type(self).__name__, list(self._items))


class BrowserItemVector(Vector):
    """``Live.Browser.BrowserItemVector`` — what ``BrowserItem.children`` returns."""

    __slots__ = ()

_VECTOR_TOKEN = object()


class MidiNoteVector(list):
    """``Live.Clip.MidiNoteVector`` — returned by the ``get_*notes*`` calls.

    Cannot be instantiated from Python (like the real one).  Modify the notes
    it contains in place, then hand the *same vector* to
    ``clip.apply_note_modifications``.
    """

    def __init__(self, items=(), _token=None):
        if _token is not _VECTOR_TOKEN:
            raise RuntimeError("This class cannot be instantiated from Python")
        list.__init__(self, items)


def _note_vector(items):
    return MidiNoteVector(items, _token=_VECTOR_TOKEN)


# ==========================================================================
# LomObject: listeners + property helpers
# ==========================================================================

def _install_listener_methods(cls, prop):
    def add(self, callback, _p=prop):
        self._listeners.setdefault(_p, []).append(callback)

    def remove(self, callback, _p=prop):
        listeners = self._listeners.get(_p, [])
        if callback in listeners:
            listeners.remove(callback)

    def has(self, callback, _p=prop):
        return callback in self._listeners.get(_p, [])

    add.__name__ = "add_%s_listener" % prop
    remove.__name__ = "remove_%s_listener" % prop
    has.__name__ = "%s_has_listener" % prop
    setattr(cls, add.__name__, add)
    setattr(cls, remove.__name__, remove)
    setattr(cls, has.__name__, has)


class LomObject(object):
    """Base for every fake LOM object: listeners + ``canonical_parent``."""

    _LISTENABLE = ()

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        for prop in cls.__dict__.get("_LISTENABLE", ()):
            _install_listener_methods(cls, prop)

    def __init__(self, canonical_parent=None):
        self._listeners = {}
        self._canonical_parent = canonical_parent

    @property
    def canonical_parent(self):
        return self._canonical_parent

    # Live objects expose ``add_<x>_listener`` for nearly every property; the
    # explicit ones cover what the tests need, this catches the rest so a
    # handler that subscribes to something exotic does not explode.
    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if name.startswith("add_") and name.endswith("_listener"):
            return lambda callback: None
        if name.startswith("remove_") and name.endswith("_listener"):
            return lambda callback: None
        if name.endswith("_has_listener"):
            return lambda callback: False
        raise AttributeError("'%s' object has no attribute '%s'" % (type(self).__name__, name))

    def notify_listeners(self, prop):
        """Stub-only: call every listener registered for ``prop``."""
        for callback in tuple(self._listeners.get(prop, ())):
            callback()

    # Live's wrappers of deleted objects compare equal to None — Ableton's own
    # ``liveobj_valid(obj)`` is literally ``obj != None``.  Everything else
    # compares by identity.
    _deleted = False

    def __eq__(self, other):
        if other is None:
            return self._deleted
        return self is other

    def __ne__(self, other):
        return not self.__eq__(other)

    __hash__ = object.__hash__

    def __repr__(self):
        try:
            name = getattr(self, "name", None)
        except Exception:
            name = None
        if not isinstance(name, str):
            return "<%s>" % type(self).__name__
        return "<%s %r>" % (type(self).__name__, name)


def _boost_type_error(what, value):
    return TypeError("Python argument types in %s did not match C++ signature: got %s"
                     % (what, type(value).__name__))


def _as_bool(value, what="bool"):
    if isinstance(value, bool):
        return value
    if isinstance(value, int) or value is None:
        return bool(value)
    raise _boost_type_error(what, value)


def _as_int(value, what="int"):
    if isinstance(value, bool) or not isinstance(value, int):
        if isinstance(value, bool):
            return int(value)
        raise _boost_type_error(what, value)
    return int(value)


def _as_float(value, what="float"):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, bool):
        return float(value)
    raise _boost_type_error(what, value)


def _as_str(value, what="str"):
    if isinstance(value, str):
        return value
    raise _boost_type_error(what, value)


def _ro(name, doc=None):
    """A read-only property backed by ``self._<name>``."""
    key = "_" + name

    def fget(self):
        return getattr(self, key)
    return property(fget, doc=doc)


def _rw(name, cast=None, check=None, doc=None, after=None):
    """A writable property backed by ``self._<name>``.

    ``cast`` converts/validates the incoming value like Boost's converters,
    ``check(self, value)`` may raise (Live's own validation), ``after(self)``
    runs after the write.  Listeners of ``name`` are notified.
    """
    key = "_" + name

    def fget(self):
        return getattr(self, key)

    def fset(self, value):
        if cast is not None:
            value = cast(value, "%s.%s" % (type(self).__name__, name))
        if check is not None:
            check(self, value)
        setattr(self, key, value)
        if after is not None:
            after(self)
        self.notify_listeners(name)
    return property(fget, fset, doc=doc)


def _range_check(low, high, name):
    def check(_self, value):
        if value < low or value > high:
            raise ValueError("%s: %r out of range [%r, %r]" % (name, value, low, high))
    return check


# ==========================================================================
# DeviceParameter
# ==========================================================================

def _fader_display(value, send=False):
    """Live 12.4.5's display of a volume / send fader (T2 live test): ``"-inf dB"`` at 0,
    40*v - 34 dB for v >= 0.4 (0.85 -> "0.0 dB", 1.0 -> "6.0 dB"), -66.8 + 202*v - 200*v^2
    below; sends read 6 dB lower (send 1.0 = "0.0 dB").  Up to three decimals without
    trailing zeros, like the "-3.0 dB" / "-2.998 dB" Live showed."""
    if value <= 0.0:
        return "-inf dB"
    if value >= 0.4:
        db = 40.0 * value - 34.0
    else:
        db = -66.8 + 202.0 * value - 200.0 * value * value
    if send:
        db -= 6.0
    text = ("%.3f" % db).rstrip("0")
    if text.endswith("."):
        text += "0"
    if text == "-0.0":
        text = "0.0"
    return text + " dB"


class DeviceParameter(LomObject):
    """A device parameter.

    ``value`` must lie in ``[min, max]`` — out-of-range writes raise (callers
    clamp).  ``value_items`` / ``short_value_items`` raise for non-quantized
    parameters and ``default_value`` raises for quantized ones, like Live.
    ``name``/``min``/``max``/``is_quantized`` are read-only.
    """

    _LISTENABLE = ("value", "name", "state", "automation_state")

    def __init__(self, name, value=0.0, min=0.0, max=1.0, default_value=None,
                 is_quantized=False, value_items=(), original_name=None,
                 unit="", canonical_parent=None):
        LomObject.__init__(self, canonical_parent)
        self._name = str(name)
        self._original_name = str(original_name) if original_name is not None else str(name)
        self._min = float(min)
        self._max = float(max)
        self._is_quantized = bool(is_quantized)
        self._value_items = tuple(str(item) for item in value_items)
        self._unit = unit
        self._default_value = float(default_value if default_value is not None else value)
        self._is_enabled = True
        self._automation_state = int(AutomationState.none)
        self._state = int(ParameterState.enabled)
        self._value = float(value)
        self._gesture = False

    name = _ro("name", "The short parameter name (read-only).")
    original_name = _ro("original_name")
    min = _ro("min")
    max = _ro("max")
    is_quantized = _ro("is_quantized")
    is_enabled = _ro("is_enabled")
    state = _ro("state", "ParameterState: 0 enabled, 1 irrelevant, 2 disabled.")
    automation_state = _ro("automation_state", "AutomationState: 0 none, 1 playing, 2 overridden.")

    @property
    def default_value(self):
        if self._is_quantized:
            raise RuntimeError("There is no default value available for this type of parameter")
        return self._default_value

    @property
    def value_items(self):
        if not self._is_quantized:
            raise RuntimeError("Only quantized parameters have value items")
        return self._value_items

    @property
    def short_value_items(self):
        return self.value_items

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, new_value):
        new_value = _as_float(new_value, "DeviceParameter.value")
        if new_value < self._min or new_value > self._max:
            raise ValueError("%s: value %r out of range [%r, %r]"
                             % (self._name, new_value, self._min, self._max))
        if self._is_quantized:
            new_value = float(int(round(new_value)))
        self._value = new_value
        if self._automation_state == int(AutomationState.playing):
            self._automation_state = int(AutomationState.overridden)
        self.notify_listeners("value")

    @property
    def display_value(self):
        """Stub: identical to ``value`` (Live converts to GUI units)."""
        return self._value

    @display_value.setter
    def display_value(self, new_value):
        self.value = new_value

    def str_for_value(self, value, /):
        value = _as_float(value, "DeviceParameter.str_for_value")
        if self._is_quantized and self._value_items:
            index = int(round(value))
            if 0 <= index < len(self._value_items):
                return self._value_items[index]
            return str(index)
        if self._unit in ("fader", "send"):
            return _fader_display(value, self._unit == "send")
        if self._unit:
            return "%.2f %s" % (value, self._unit)
        return "%.2f" % value

    def __str__(self):
        return self.str_for_value(self._value)

    def re_enable_automation(self):
        if self._automation_state == int(AutomationState.overridden):
            self._automation_state = int(AutomationState.playing)

    def begin_gesture(self):
        self._gesture = True

    def end_gesture(self):
        self._gesture = False

    # -- stub-only helpers ------------------------------------------------
    def _set_enabled(self, enabled):
        self._is_enabled = bool(enabled)
        self._state = int(ParameterState.enabled if enabled else ParameterState.disabled)


def _device_on_parameter(owner):
    return DeviceParameter("Device On", 1.0, 0.0, 1.0, is_quantized=True,
                           value_items=("Off", "On"), canonical_parent=owner)


def _make_parameters(owner, specs):
    """``specs``: DeviceParameter objects, kwargs dicts or plain names."""
    params = []
    for spec in specs:
        if isinstance(spec, DeviceParameter):
            spec._canonical_parent = owner
            params.append(spec)
        elif isinstance(spec, dict):
            params.append(DeviceParameter(canonical_parent=owner, **spec))
        else:
            params.append(DeviceParameter(spec, canonical_parent=owner))
    return params


# ==========================================================================
# Devices
# ==========================================================================

#: UI name -> (class_name, device type) for the native devices
#: ``Track.insert_device`` / ``Chain.insert_device`` know (Live 12.3+): every native device of
#: Live 12.4.5 Suite, verified on the running Live by inserting each one (2026-09-10, T3 live
#: test).  Names are case-sensitive UI names; Max for Live based devices the browser lists next
#: to them (LFO, Shaper, Drum Sampler, DS Kick ...) are not insertable.
NATIVE_DEVICES = dict((name, (class_name, DeviceType(device_type))) for name, (class_name,
                                                                       device_type) in {
    "Analog": ("UltraAnalog", 1), "Collision": ("Collision", 1), "Drift": ("Drift", 1),
    "Drum Rack": ("DrumGroupDevice", 1), "Electric": ("LoungeLizard", 1),
    "External Instrument": ("ProxyInstrumentDevice", 1), "Impulse": ("InstrumentImpulse", 1),
    "Instrument Rack": ("InstrumentGroupDevice", 1), "Meld": ("InstrumentMeld", 1),
    "Operator": ("Operator", 1), "Sampler": ("MultiSampler", 1),
    "Simpler": ("OriginalSimpler", 1), "Tension": ("StringStudio", 1),
    "Wavetable": ("InstrumentVector", 1),
    "Arpeggiator": ("MidiArpeggiator", 4), "CC Control": ("MidiCcControl", 4),
    "Chord": ("MidiChord", 4), "MIDI Effect Rack": ("MidiEffectGroupDevice", 4),
    "Note Length": ("MidiNoteLength", 4), "Pitch": ("MidiPitcher", 4),
    "Random": ("MidiRandom", 4), "Scale": ("MidiScale", 4), "Velocity": ("MidiVelocity", 4),
    "Amp": ("Amp", 2), "Audio Effect Rack": ("AudioEffectGroupDevice", 2),
    "Auto Filter": ("AutoFilter2", 2), "Auto Pan-Tremolo": ("AutoPan2", 2),
    "Auto Shift": ("AutoShift", 2), "Beat Repeat": ("BeatRepeat", 2), "Cabinet": ("Cabinet", 2),
    "Channel EQ": ("ChannelEq", 2), "Chorus-Ensemble": ("Chorus2", 2),
    "Compressor": ("Compressor2", 2), "Corpus": ("Corpus", 2), "Delay": ("Delay", 2),
    "Drum Buss": ("DrumBuss", 2), "Dynamic Tube": ("Tube", 2), "Echo": ("Echo", 2),
    "EQ Eight": ("Eq8", 2), "EQ Three": ("FilterEQ3", 2), "Erosion": ("Erosion2", 2),
    "External Audio Effect": ("ProxyAudioEffectDevice", 2), "Filter Delay": ("FilterDelay", 2),
    "Gate": ("Gate", 2), "Glue Compressor": ("GlueCompressor", 2),
    "Grain Delay": ("GrainDelay", 2), "Hybrid Reverb": ("Hybrid", 2), "Limiter": ("Limiter", 2),
    "Looper": ("Looper", 2), "Multiband Dynamics": ("MultibandDynamics", 2),
    "Overdrive": ("Overdrive", 2), "Pedal": ("Pedal", 2), "Phaser-Flanger": ("PhaserNew", 2),
    "Redux": ("Redux2", 2), "Resonators": ("Resonator", 2), "Reverb": ("Reverb", 2),
    "Roar": ("Roar", 2), "Saturator": ("Saturator", 2), "Shifter": ("Shifter", 2),
    "Spectral Resonator": ("Transmute", 2), "Spectral Time": ("Spectral", 2),
    "Spectrum": ("SpectrumAnalyzer", 2), "Tuner": ("Tuner", 2), "Utility": ("StereoGain", 2),
    "Vinyl Distortion": ("Vinyl", 2), "Vocoder": ("Vocoder", 2),
}.items())

#: Browser devices that are Max for Live based (listed by the browser, not insertable with
#: ``insert_device``) — Live 12.4.5 Suite.
MAX_FOR_LIVE_DEVICES = {
    "instruments": ("Drum Sampler", "DS Clang", "DS Clap", "DS Cymbal", "DS FM", "DS HH",
                    "DS Kick", "DS Snare", "DS Tom"),
    "midi_effects": ("Envelope MIDI", "Expression Control", "MIDI Monitor", "MPE Control",
                     "Note Echo", "Shaper MIDI"),
    "audio_effects": ("Align Delay", "Envelope Follower", "LFO", "Shaper"),
}

_RACK_CLASSES = ("DrumGroupDevice", "InstrumentGroupDevice", "AudioEffectGroupDevice",
                 "MidiEffectGroupDevice")


class DeviceView(LomObject):
    """``Live.Device.Device.View`` — only ``is_collapsed``."""

    def __init__(self, device):
        LomObject.__init__(self, device)
        self._is_collapsed = False

    is_collapsed = _rw("is_collapsed", _as_bool)


class Device(LomObject):
    """``Live.Device.Device``.

    ``parameters[0]`` is always ``"Device On"``; ``is_active`` is read-only
    and follows it (and the enclosing rack).  ``name`` is writable.
    """

    _LISTENABLE = ("name", "parameters", "is_active")

    def __init__(self, name="Device", class_name=None, type=DeviceType.audio_effect,
                 parameters=(), canonical_parent=None, class_display_name=None):
        LomObject.__init__(self, canonical_parent)
        self._name = str(name)
        self._class_name = class_name if class_name is not None else str(name).replace(" ", "")
        self._class_display_name = class_display_name if class_display_name is not None \
            else str(name)
        self._type = int(type)
        self._view = self._make_view()
        self._parameters = [_device_on_parameter(self)] + _make_parameters(self, parameters)
        self._latency_in_samples = 0
        self._chosen_bank = None

    def _make_view(self):
        return DeviceView(self)

    name = _rw("name", _as_str)
    class_name = _ro("class_name")
    class_display_name = _ro("class_display_name")
    view = _ro("view")
    can_have_chains = property(lambda self: False)
    can_have_drum_pads = property(lambda self: False)
    latency_in_samples = _ro("latency_in_samples")
    _can_compare_ab = True        # native devices support AB compare (12.3+)

    @property
    def type(self):
        """A ``Live.Device.DeviceType`` member (an int subclass) — verified in
        Live 12.4.5: 0 undefined, 1 instrument, 2 audio_effect, 4 midi_effect."""
        return DeviceType(self._type)

    @property
    def can_compare_ab(self):
        return self._can_compare_ab

    @property
    def latency_in_ms(self):
        return self._latency_in_samples / 44.1

    @property
    def is_using_compare_preset_b(self):
        if not self._can_compare_ab:
            raise RuntimeError("This device does not support AB compare")
        return getattr(self, "_compare_b", False)

    @is_using_compare_preset_b.setter
    def is_using_compare_preset_b(self, value):
        if not self._can_compare_ab:
            raise RuntimeError("This device does not support AB compare")
        self._compare_b = _as_bool(value)

    @property
    def parameters(self):
        return Vector(self._parameters)

    @property
    def is_active(self):
        on = self._parameters[0].value != 0.0 if self._parameters else True
        parent = self._canonical_parent
        while parent is not None and not isinstance(parent, Track):
            if isinstance(parent, Device):
                return on and parent.is_active
            parent = parent.canonical_parent
        return on

    def store_chosen_bank(self, bank_index, preset_index, /):
        self._chosen_bank = (_as_int(bank_index), _as_int(preset_index))

    def save_preset_to_compare_ab_slot(self):
        """Store the current state in the A/B slot (recorded as ``_compare_slot_saves``)."""
        if not self._can_compare_ab:
            raise RuntimeError("This device does not support AB compare")
        self._compare_slot_saves = getattr(self, "_compare_slot_saves", 0) + 1


class ChainMixerDevice(LomObject):
    """``Live.ChainMixerDevice.ChainMixerDevice``."""

    def __init__(self, chain, send_count=0):
        LomObject.__init__(self, chain)
        self._volume = DeviceParameter("Chain Volume", 0.85, 0.0, 1.0, unit="fader",
                                       canonical_parent=self)
        self._panning = DeviceParameter("Chain Pan", 0.0, -1.0, 1.0, canonical_parent=self)
        self._chain_activator = DeviceParameter(
            "Chain Activator", 1.0, 0.0, 1.0, is_quantized=True,
            value_items=("Off", "On"), canonical_parent=self)
        self._sends = [DeviceParameter("Send %s" % chr(ord("A") + i), 0.0, 0.0, 1.0,
                                       canonical_parent=self) for i in range(send_count)]

    volume = _ro("volume")
    panning = _ro("panning")
    chain_activator = _ro("chain_activator")

    @property
    def sends(self):
        return Vector(self._sends)


class _DeviceContainer(LomObject):
    """Shared device-chain behaviour of Track and Chain (``DeviceContainer``)."""

    def _init_devices(self):
        self._devices = []

    @property
    def devices(self):
        return Vector(self._devices)

    def _device_host_type(self):
        return None

    def delete_device(self, index, /):
        index = _as_int(index, "delete_device")
        if not (0 <= index < len(self._devices)):
            raise RuntimeError("delete_device: index %d out of range (%d devices)"
                               % (index, len(self._devices)))
        _mark_deleted(self._devices.pop(index))
        self.notify_listeners("devices")

    def duplicate_device(self, index, /):
        index = _as_int(index, "duplicate_device")
        if not (0 <= index < len(self._devices)):
            raise RuntimeError("duplicate_device: index %d out of range" % index)
        source = self._devices[index]
        clone = _clone_device(source, self)
        self._devices.insert(index + 1, clone)
        self.notify_listeners("devices")

    def insert_device(self, DeviceName, DeviceIndex=-1):
        """Live 12.3+: insert a *native* device by its exact (case-sensitive) UI name.

        Real Live 12.4.5 errors: unknown names (also "operator", Max for Live devices)
        raise ``ValueError("Device <name> not found.")``; anything but an audio effect on
        an audio/return/master track, a second instrument and a wrong position raise
        ``RuntimeError("Can not insert device ...")`` / ``("Invalid insert index ...")``.
        """
        name = _as_str(DeviceName, "insert_device")
        index = _as_int(DeviceIndex, "insert_device")
        if name not in NATIVE_DEVICES:
            raise ValueError("Device %s not found." % name)
        class_name, device_type = NATIVE_DEVICES[name]
        device_type = int(device_type)
        devices = self._devices
        if isinstance(self, Track) and getattr(self, "_kind", "midi") != "midi" \
                and device_type != int(DeviceType.audio_effect):
            raise RuntimeError("Can not insert device '%s': Only audio effects can be "
                               "inserted into an audio track." % name)
        if device_type == int(DeviceType.instrument) and \
                any(int(d.type) == int(DeviceType.instrument) for d in devices):
            raise RuntimeError("Can not insert device '%s': Device chains cannot have more "
                               "than one instrument each." % name)
        if index == -1:
            index = len(devices)
        _check_device_order(devices, index, device_type, name)
        device = _build_native_device(name, class_name, DeviceType(device_type), self)
        devices.insert(index, device)
        self.notify_listeners("devices")
        return device


_TYPE_ORDER = {int(DeviceType.midi_effect): 0, int(DeviceType.instrument): 1,
               int(DeviceType.audio_effect): 2, int(DeviceType.undefined): 2}


def _check_device_order(devices, index, device_type, name="device"):
    """MIDI effects before the instrument before audio effects (Live's rule and message)."""
    rank = _TYPE_ORDER.get(device_type, 2)
    valid = [i for i in range(len(devices) + 1)
             if all(_TYPE_ORDER.get(int(d.type), 2) <= rank for d in devices[:i])
             and all(_TYPE_ORDER.get(int(d.type), 2) >= rank for d in devices[i:])]
    if index not in valid:
        kind = {int(DeviceType.midi_effect): "MIDI effects before instruments",
                int(DeviceType.instrument): "instruments after MIDI effects",
                int(DeviceType.audio_effect): "audio effects after instruments"}.get(
                    device_type, "devices")
        raise RuntimeError("Invalid insert index for device '%s': Insert %s. A valid "
                           "index would be %d." % (name, kind, valid[0] if valid else 0))


def _build_native_device(name, class_name, device_type, host):
    if class_name in _RACK_CLASSES:
        rack = RackDevice(name, class_name, device_type, (), host,
                          can_have_drum_pads=(class_name == "DrumGroupDevice"))
        return rack
    if class_name == "OriginalSimpler":
        return SimplerDevice(name, class_name, device_type, (), host)
    return Device(name, class_name, device_type,
                  ({"name": "Param 1", "value": 0.5}, {"name": "Param 2", "value": 0.5}),
                  host)


def _clone_device(source, host):
    params = [DeviceParameter(p.name, p.value, p.min, p.max, None, p.is_quantized,
                              p._value_items, p.original_name, p._unit)
              for p in source._parameters[1:]]
    if isinstance(source, RackDevice):
        clone = RackDevice(source.name, source.class_name, source.type, params, host,
                           can_have_drum_pads=source.can_have_drum_pads)
    elif isinstance(source, SimplerDevice):
        clone = SimplerDevice(source.name, source.class_name, source.type, params, host,
                              source._sample.file_path if source._sample else "")
    elif isinstance(source, PluginDevice):
        clone = PluginDevice(source.name, source.class_name, source.type, params, host,
                             source._presets, source._plugin_names)
    else:
        clone = Device(source.name, source.class_name, source.type, params, host)
    return clone


class Chain(_DeviceContainer):
    """``Live.Chain.Chain`` — a rack chain."""

    _LISTENABLE = ("name", "devices", "mute", "solo", "color")

    def __init__(self, name="Chain", canonical_parent=None):
        LomObject.__init__(self, canonical_parent)
        self._name = str(name)
        self._solo = False
        self._color_index = 0
        self._color = 0
        self._is_auto_colored = True
        self._init_devices()
        self._mixer_device = ChainMixerDevice(self)

    name = _rw("name", _as_str)
    solo = _rw("solo", _as_bool)
    color = _rw("color", _as_int)
    color_index = _rw("color_index", _as_int)
    is_auto_colored = _rw("is_auto_colored", _as_bool)
    mixer_device = _ro("mixer_device")

    @property
    def mute(self):
        """Live 12.4.5: the same switch as ``mixer_device.chain_activator`` (0 = muted)."""
        return not float(self._mixer_device._chain_activator._value)

    @mute.setter
    def mute(self, value):
        self._mixer_device._chain_activator._value = 0.0 if _as_bool(value) else 1.0
        self.notify_listeners("mute")

    @property
    def muted_via_solo(self):
        """True while another chain of the same rack is soloed and this one is not."""
        rack = self._canonical_parent
        siblings = list(getattr(rack, "_chains", ()) or ())
        return (not self._solo) and any(c._solo for c in siblings if c is not self)

    @property
    def has_midi_input(self):
        return any(d.type == int(DeviceType.instrument) for d in self._devices) or \
            self._rack_type() in (int(DeviceType.instrument), int(DeviceType.midi_effect))

    @property
    def has_audio_input(self):
        return self._rack_type() == int(DeviceType.audio_effect)

    @property
    def has_midi_output(self):
        return self._rack_type() == int(DeviceType.midi_effect)

    @property
    def has_audio_output(self):
        return self._rack_type() != int(DeviceType.midi_effect)

    def _rack_type(self):
        parent = self._canonical_parent
        return getattr(parent, "_type", int(DeviceType.undefined))


def _drum_range(name, low, high, message):
    key = "_" + name

    def fget(self):
        return getattr(self, key)

    def fset(self, value):
        value = _as_int(value, "DrumChain.%s" % name)
        if value < low or value > high:
            raise RuntimeError(message)
        setattr(self, key, value)
        self.notify_listeners(name)
    return property(fget, fset)


class DrumChain(Chain):
    """``Live.DrumChain.DrumChain`` — a Drum Rack chain.

    ``in_note`` (12.3+) decides which pad triggers it: 0..127 only — Live 12.4.5 raises
    ``RuntimeError("Invalid note number.")`` for -1, so the UI's "All Notes" is not reachable
    through the API.  ``out_note`` 0..127 (``"Invalid note."``, default 60 = C3),
    ``choke_group`` 0..16 (``"Invalid choke group."``, 0 = none).  A new chain lands on C1 (36).
    """

    _LISTENABLE = ("in_note", "out_note", "choke_group")

    def __init__(self, name="Chain", canonical_parent=None, in_note=36):
        Chain.__init__(self, name, canonical_parent)
        self._in_note = int(in_note)
        self._out_note = 60
        self._choke_group = 0

    in_note = _drum_range("in_note", 0, 127, "Invalid note number.")
    out_note = _drum_range("out_note", 0, 127, "Invalid note.")
    choke_group = _drum_range("choke_group", 0, 16, "Invalid choke group.")


class RackDeviceView(DeviceView):
    """``Live.RackDevice.RackDevice.View``."""

    def __init__(self, device):
        DeviceView.__init__(self, device)
        self._selected_chain = None
        self._selected_drum_pad = None
        self._drum_pads_scroll_position = 9
        self._is_showing_chain_devices = False

    selected_chain = _rw("selected_chain")
    is_showing_chain_devices = _rw("is_showing_chain_devices", _as_bool)

    @property
    def selected_drum_pad(self):
        if not self._canonical_parent.can_have_drum_pads:
            raise RuntimeError("selected_drum_pad: not a Drum Rack")
        return self._selected_drum_pad

    @selected_drum_pad.setter
    def selected_drum_pad(self, pad):
        if not self._canonical_parent.can_have_drum_pads:
            raise RuntimeError("selected_drum_pad: not a Drum Rack")
        self._selected_drum_pad = pad

    @property
    def drum_pads_scroll_position(self):
        if not self._canonical_parent.can_have_drum_pads:
            raise RuntimeError("drum_pads_scroll_position: not a Drum Rack")
        return self._drum_pads_scroll_position

    @drum_pads_scroll_position.setter
    def drum_pads_scroll_position(self, value):
        value = _as_int(value, "drum_pads_scroll_position")
        if not (0 <= value <= 28):
            raise ValueError("drum_pads_scroll_position must be 0..28")
        self._drum_pads_scroll_position = value


class RackDevice(Device):
    """``Live.RackDevice.RackDevice`` (Instrument/Audio/MIDI/Drum Rack).

    Parameters: ``[Device On, Macro 1 .. Macro 16, Chain Selector]`` (Live 11+
    racks have 16 macros; ``visible_macro_count`` defaults to 8).
    ``drum_pads`` (128 pads, topmost Drum Rack only) / ``visible_drum_pads``
    (16) / ``has_drum_pads`` raise on non-drum racks.  For drum racks,
    ``chains`` is the list of all DrumChains and each pad's chains are the
    ones whose ``in_note`` equals the pad's note.
    """

    _LISTENABLE = ("chains", "return_chains", "variation_count",
                   "visible_macro_count", "has_macro_mappings")

    MACRO_COUNT = 16

    def __init__(self, name="Rack", class_name="InstrumentGroupDevice",
                 type=DeviceType.instrument, parameters=(), canonical_parent=None,
                 can_have_drum_pads=False):
        self._is_drum_rack = bool(can_have_drum_pads)
        if not parameters:
            parameters = [{"name": "Macro %d" % (i + 1), "value": 0.0, "min": 0.0,
                           "max": 127.0} for i in range(self.MACRO_COUNT)]
            parameters.append({"name": "Chain Selector", "value": 0.0, "min": 0.0,
                               "max": 127.0})
        Device.__init__(self, name, class_name, type, parameters, canonical_parent)
        self._chains = []
        self._return_chains = []
        self._drum_pads = [DrumPad(note, self) for note in range(128)] \
            if self._is_drum_rack else []
        self._visible_macro_count = 8
        self._variations = []
        self._selected_variation_index = -1
        self._last_recalled_variation = None
        self._macro_mappings = [False] * self.MACRO_COUNT

    def _make_view(self):
        return RackDeviceView(self)

    can_have_chains = property(lambda self: True)
    can_have_drum_pads = property(lambda self: self._is_drum_rack)
    visible_macro_count = _ro("visible_macro_count")
    can_show_chains = property(lambda self: self.type == int(DeviceType.instrument)
                               and bool(self._chains))

    @property
    def is_showing_chains(self):
        return getattr(self, "_is_showing_chains", False)

    @is_showing_chains.setter
    def is_showing_chains(self, value):
        self._is_showing_chains = _as_bool(value)

    # -- chains / pads -----------------------------------------------------
    @property
    def chains(self):
        return Vector(self._chains)

    @property
    def return_chains(self):
        return Vector(self._return_chains)

    def _is_topmost(self):
        return isinstance(self._canonical_parent, Track)

    @property
    def drum_pads(self):
        if not self._is_drum_rack:
            raise RuntimeError("drum_pads: can_have_drum_pads is false")
        return Vector(self._drum_pads) if self._is_topmost() else Vector()

    @property
    def visible_drum_pads(self):
        if not self._is_drum_rack:
            raise RuntimeError("visible_drum_pads: can_have_drum_pads is false")
        if not self._is_topmost():
            return Vector()
        start = self._view._drum_pads_scroll_position * 4
        return Vector(self._drum_pads[start:start + 16])

    @property
    def has_drum_pads(self):
        if not self._is_drum_rack:
            raise RuntimeError("has_drum_pads: can_have_drum_pads is false")
        return self._is_topmost()

    @property
    def chain_selector(self):
        return self._parameters[-1]

    # -- macros --------------------------------------------------------------
    @property
    def macros_mapped(self):
        return tuple(self._macro_mappings)

    @property
    def has_macro_mappings(self):
        return any(self._macro_mappings)

    def add_macro(self):
        if self._visible_macro_count >= self.MACRO_COUNT:
            raise RuntimeError("add_macro: maximum number of macros reached")
        self._visible_macro_count += 1
        self.notify_listeners("visible_macro_count")

    def remove_macro(self):
        if self._visible_macro_count <= 1:
            raise RuntimeError("remove_macro: minimum number of macros reached")
        self._visible_macro_count -= 1
        self.notify_listeners("visible_macro_count")

    def randomize_macros(self):
        """Only *mapped* macros change (Live 12.4.5); unmapped ones keep their value."""
        for index, param in enumerate(self._parameters[1:1 + self.MACRO_COUNT]):
            if self._macro_mappings[index]:
                param.value = float((index * 37 + 11) % 128)

    # -- variations ------------------------------------------------------------
    @property
    def variation_count(self):
        return len(self._variations)

    @property
    def selected_variation_index(self):
        return self._selected_variation_index

    @selected_variation_index.setter
    def selected_variation_index(self, value):
        value = _as_int(value, "selected_variation_index")
        if not (-1 <= value < len(self._variations)):
            raise RuntimeError("selected_variation_index %d out of range" % value)
        self._selected_variation_index = value

    def _macro_values(self):
        return [p.value for p in self._parameters[1:1 + self.MACRO_COUNT]]

    def store_variation(self):
        """Store the macros as a new variation; the selection does not change (12.4.5)."""
        self._variations.append(self._macro_values())
        self.notify_listeners("variation_count")

    def recall_selected_variation(self):
        """Recall the selected variation — only *mapped* macros change (12.4.5)."""
        index = self._selected_variation_index
        if 0 <= index < len(self._variations):
            macros = self._parameters[1:1 + self.MACRO_COUNT]
            for number, (param, value) in enumerate(zip(macros, self._variations[index])):
                if self._macro_mappings[number]:
                    param.value = value
            self._last_recalled_variation = index

    def recall_last_used_variation(self):
        if self._last_recalled_variation is not None:
            self._selected_variation_index = self._last_recalled_variation
            self.recall_selected_variation()

    def delete_selected_variation(self):
        index = self._selected_variation_index
        if 0 <= index < len(self._variations):
            del self._variations[index]
            self._selected_variation_index = -1      # Live 12.4.5: nothing selected after
            self.notify_listeners("variation_count")

    # -- editing -----------------------------------------------------------------
    def insert_chain(self, Index=-1):
        """Live 12.3+: insert a chain; a Drum Rack chain always lands on C1 (in_note 36),
        whatever pad is selected (Live 12.4.5)."""
        index = _as_int(Index, "insert_chain")
        if index == -1:
            index = len(self._chains)
        if not (0 <= index <= len(self._chains)):
            raise RuntimeError("insert_chain: invalid index %d" % index)
        if self._is_drum_rack:
            chain = DrumChain("Chain", self, in_note=36)
        else:
            chain = Chain("Chain", self)
        self._chains.insert(index, chain)
        self.notify_listeners("chains")
        return chain

    def copy_pad(self, source_index, destination_index, /):
        if not self._is_drum_rack:
            raise RuntimeError("copy_pad: not a Drum Rack")
        source = _as_int(source_index)
        destination = _as_int(destination_index)
        if not (0 <= source <= 127 and 0 <= destination <= 127):
            raise RuntimeError("copy_pad: indices must be 0-127")
        chains = [c for c in self._chains if c.in_note == source]
        if not chains:
            raise RuntimeError("copy_pad: source pad %d is empty" % source)
        for chain in [c for c in self._chains if c.in_note == destination]:
            self._chains.remove(chain)
        for chain in chains:
            clone = DrumChain(chain.name, self, in_note=destination)
            for device in chain._devices:
                clone._devices.append(_clone_device(device, clone))
            self._chains.append(clone)
        self.notify_listeners("chains")

    # -- stub-only helpers -------------------------------------------------------
    def add_chain(self, name=None, in_note=None):
        """Stub-only: append a chain (drum racks: triggered by ``in_note``, default C1)."""
        if self._is_drum_rack:
            chain = DrumChain(name or "Chain", self,
                              in_note=36 if in_note is None else in_note)
        else:
            chain = Chain(name or "Chain %d" % (len(self._chains) + 1), self)
        self._chains.append(chain)
        self.notify_listeners("chains")
        return chain

    def _map_macro(self, macro_index, mapped=True):
        """Stub-only: mark macro ``macro_index`` (0-based) as mapped."""
        self._macro_mappings[macro_index] = bool(mapped)


_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def live_note_name(note):
    """60 -> ``"C3"``, 36 -> ``"C1"`` (Live's octave numbering)."""
    return "%s%d" % (_NOTE_NAMES[note % 12], note // 12 - 2)


class DrumPad(LomObject):
    """``Live.DrumPad.DrumPad``.

    ``name`` and ``note`` are read-only; the pad's chains are the rack chains
    whose ``in_note`` equals ``note``.
    """

    _LISTENABLE = ("name", "chains", "mute", "solo")

    def __init__(self, note, canonical_parent=None):
        LomObject.__init__(self, canonical_parent)
        self._note = int(note)
        self._mute = False
        self._solo = False

    note = _ro("note")
    mute = _rw("mute", _as_bool)
    solo = _rw("solo", _as_bool)

    @property
    def chains(self):
        rack = self._canonical_parent
        return Vector(c for c in rack._chains if getattr(c, "in_note", None) == self._note)

    @property
    def name(self):
        """Live 12.4.5: the chain name for one chain, ``"Multi"`` for several, the note
        name (``"C1"`` for 36, C3 = 60) for an empty pad."""
        chains = self.chains
        if not chains:
            return live_note_name(self._note)
        if len(chains) > 1:
            return "Multi"
        return chains[0].name

    def delete_all_chains(self):
        rack = self._canonical_parent
        for chain in self.chains:
            _mark_deleted(chain)
        rack._chains = [c for c in rack._chains if getattr(c, "in_note", None) != self._note]
        rack.notify_listeners("chains")


class PluginDevice(Device):
    """``Live.PluginDevice.PluginDevice`` (VST2/VST3: class_name ``PluginDevice``;
    Audio Units: ``AuPluginDevice``).

    Live 12.4.5 (measured with Serum 2 and Apple AUs, ``docs/live_test/T4-plugins.md``):
    ``parameters`` is "Device On" + the plug-in's **Configure** list only (Live fills it by
    itself for small plug-ins and leaves it empty for big ones such as Serum 2), while
    ``get_parameter_names()`` lists **every** parameter the plug-in has (never "Device On").
    ``plugin_parameter_names`` sets that full list; without it the stub's plug-in has exactly
    its exposed parameters (a small plug-in Live configured by itself).
    ``tests/live_stub_ext/plugins_live.py`` adds Serum 2's real names and the Configure
    action."""

    _LISTENABLE = ("presets", "selected_preset_index")

    def __init__(self, name="Serum", class_name="PluginDevice",
                 type=DeviceType.instrument, parameters=(), canonical_parent=None,
                 presets=(), plugin_parameter_names=None):
        Device.__init__(self, name, class_name, type, parameters, canonical_parent)
        # Live 12.4.5: plug-ins without a program list (Serum 2, Apple AUs) report ["Default"].
        self._presets = tuple(presets) or ("Default",)
        self._selected_preset_index = 0
        self._is_editor_open = False
        self._plugin_names = (None if plugin_parameter_names is None
                              else [str(n) for n in plugin_parameter_names])

    is_editor_open = _rw("is_editor_open", _as_bool)

    @property
    def presets(self):
        return tuple(self._presets)

    @property
    def selected_preset_index(self):
        return self._selected_preset_index

    @selected_preset_index.setter
    def selected_preset_index(self, value):
        value = _as_int(value, "selected_preset_index")
        if not (0 <= value < max(1, len(self._presets))):
            raise RuntimeError("selected_preset_index %d out of range" % value)
        self._selected_preset_index = value
        self.notify_listeners("selected_preset_index")

    def get_parameter_names(self, begin=0, end=-1):
        """Every parameter name of the plug-in (not only the exposed ones), never
        "Device On"."""
        if self._plugin_names is not None:
            names = list(self._plugin_names)
        else:
            params = list(self._parameters)
            if params and params[0].name == "Device On":
                params = params[1:]
            names = [p.name for p in params]
        end = len(names) if end < 0 else end
        return tuple(names[begin:end])


_AUDIO_EXTENSIONS = (".wav", ".aif", ".aiff", ".mp3", ".flac", ".ogg", ".m4a",
                     ".mp4", ".alac", ".caf")


def _check_audio_path(path, what):
    path = _as_str(path, what)
    # absolute on macOS or Windows, whatever OS runs the tests (not os.path.isabs: since
    # Python 3.13 "/Samples/a.wav" is no longer absolute on Windows)
    absolute = path.startswith(("/", "\\\\")) or bool(re.match(r"^[A-Za-z]:[\\/]", path))
    if not absolute:
        raise RuntimeError("%s: the path must be absolute: %r" % (what, path))
    if not path.lower().endswith(_AUDIO_EXTENSIONS):
        raise RuntimeError("%s: not a supported audio file: %r" % (what, path))
    return path


class WarpMarker(object):
    """``Live.Clip.WarpMarker(sample_time, beat_time)`` — note the argument order."""

    def __init__(self, sample_time, beat_time):
        self._sample_time = float(sample_time)
        self._beat_time = float(beat_time)

    sample_time = property(lambda self: self._sample_time)
    beat_time = property(lambda self: self._beat_time)

    def __repr__(self):
        return "<WarpMarker sample_time=%s beat_time=%s>" % (self._sample_time,
                                                             self._beat_time)


class Sample(LomObject):
    """``Live.Sample.Sample`` — the sample loaded in a Simpler.

    Times (``start_marker``, ``end_marker``, ``slices``) are in sample frames.
    ``gain_display_string()`` is a method here (unlike on Clip).
    """

    _LISTENABLE = ("slices", "warping", "warp_mode", "gain")

    def __init__(self, file_path="", length=176400, sample_rate=44100,
                 canonical_parent=None):
        LomObject.__init__(self, canonical_parent)
        self._file_path = file_path
        self._length = int(length)
        self._sample_rate = int(sample_rate)
        self._start_marker = 0
        self._end_marker = int(length)
        self._gain = 0.4
        self._warping = False
        self._warp_mode = int(WarpMode.beats)
        self._slices = []
        self._original_slices = []
        self._slicing_style = int(SlicingStyle.transient)
        self._slicing_sensitivity = 0.5
        self._slicing_beat_division = int(SlicingBeatDivision.sixteenth)
        self._slicing_region_count = 8
        self._beats_granulation_resolution = 6
        self._beats_transient_envelope = 100.0
        self._beats_transient_loop_mode = int(TransientLoopMode.off)
        self._complex_pro_envelope = 128.0
        self._complex_pro_formants = 100.0
        self._texture_flux = 0.0
        self._texture_grain_size = 50.0
        self._tones_grain_size = 50.0
        self._warp_markers = [WarpMarker(0.0, 0.0),
                              WarpMarker(self._length / float(self._sample_rate), 8.0)]

    file_path = _ro("file_path")
    length = _ro("length")
    sample_rate = _ro("sample_rate")
    start_marker = _rw("start_marker", _as_int)
    end_marker = _rw("end_marker", _as_int)
    gain = _rw("gain", _as_float)
    warping = _rw("warping", _as_bool)
    warp_mode = _rw("warp_mode", _as_int, _range_check(0, 6, "warp_mode"))
    slicing_style = _rw("slicing_style", _as_int, _range_check(0, 3, "slicing_style"))
    slicing_sensitivity = _rw("slicing_sensitivity", _as_float,
                              _range_check(0.0, 1.0, "slicing_sensitivity"))
    slicing_beat_division = _rw("slicing_beat_division", _as_int,
                                _range_check(0, 10, "slicing_beat_division"))
    slicing_region_count = _rw("slicing_region_count", _as_int)
    beats_granulation_resolution = _rw("beats_granulation_resolution", _as_int,
                                       _range_check(0, 6, "beats_granulation_resolution"))
    beats_transient_envelope = _rw("beats_transient_envelope", _as_float)
    beats_transient_loop_mode = _rw("beats_transient_loop_mode", _as_int,
                                    _range_check(0, 2, "beats_transient_loop_mode"))
    complex_pro_envelope = _rw("complex_pro_envelope", _as_float)
    complex_pro_formants = _rw("complex_pro_formants", _as_float)
    texture_flux = _rw("texture_flux", _as_float)
    texture_grain_size = _rw("texture_grain_size", _as_float)
    tones_grain_size = _rw("tones_grain_size", _as_float)

    @property
    def slices(self):
        return tuple(self._slices)

    @property
    def warp_markers(self):
        return tuple(self._warp_markers)

    def gain_display_string(self):
        return "0.0 dB"

    def insert_slice(self, slice_time):
        slice_time = _as_int(slice_time, "insert_slice")
        if slice_time not in self._slices:
            self._slices.append(slice_time)
            self._slices.sort()
            self.notify_listeners("slices")

    def move_slice(self, old_time, new_time):
        old_time = _as_int(old_time)
        new_time = _as_int(new_time)
        if old_time not in self._slices:
            raise RuntimeError("move_slice: no slice at %d" % old_time)
        self._slices.remove(old_time)
        self._slices.append(new_time)
        self._slices.sort()
        return new_time

    def remove_slice(self, slice_time):
        slice_time = _as_int(slice_time, "remove_slice")
        if slice_time in self._slices:
            self._slices.remove(slice_time)
            self.notify_listeners("slices")

    def clear_slices(self):
        self._slices = []
        self.notify_listeners("slices")

    def reset_slices(self):
        self._slices = list(self._original_slices)

    def beat_to_sample_time(self, beat_time):
        if not self._warping:
            raise RuntimeError("beat_to_sample_time: the sample is not warped")
        return float(beat_time) * self._length / 8.0

    def sample_to_beat_time(self, sample_time):
        if not self._warping:
            raise RuntimeError("sample_to_beat_time: the sample is not warped")
        return float(sample_time) * 8.0 / self._length


class SimplerDeviceView(DeviceView):
    """``Live.SimplerDevice.SimplerDevice.View`` (sample_* positions in frames)."""

    def __init__(self, device):
        DeviceView.__init__(self, device)
        self._selected_slice = 0

    selected_slice = _rw("selected_slice", _as_int)


class SimplerDevice(Device):
    """``Live.SimplerDevice.SimplerDevice`` (class_name ``OriginalSimpler``).

    ``sample`` is ``None`` for an empty Simpler; ``crop``/``reverse``/
    ``warp_*``/``guess_playback_length`` raise on an empty Simpler.
    ``replace_sample(file_path)`` loads a file (present in the 12.4 runtime).
    """

    _LISTENABLE = ("sample", "playback_mode", "slicing_playback_mode", "voices")

    def __init__(self, name="Simpler", class_name="OriginalSimpler",
                 type=DeviceType.instrument, parameters=(), canonical_parent=None,
                 file_path=""):
        Device.__init__(self, name, class_name, type, parameters, canonical_parent)
        self._sample = Sample(file_path, canonical_parent=self) if file_path else None
        self._playback_mode = int(PlaybackMode.classic)
        self._slicing_playback_mode = int(SlicingPlaybackMode.mono)
        self._multi_sample_mode = False
        self._pad_slicing = False
        self._retrigger = False
        self._voices = 1
        self._pitch_bend_range = 5
        self._note_pitch_bend_range = 48
        self._reversed = False

    def _make_view(self):
        return SimplerDeviceView(self)

    sample = _ro("sample")
    playback_mode = _rw("playback_mode", _as_int, _range_check(0, 2, "playback_mode"))
    slicing_playback_mode = _rw("slicing_playback_mode", _as_int,
                                _range_check(0, 2, "slicing_playback_mode"))
    multi_sample_mode = _ro("multi_sample_mode")
    pad_slicing = _rw("pad_slicing", _as_bool)
    retrigger = _rw("retrigger", _as_bool)
    voices = _rw("voices", _as_int)
    pitch_bend_range = _rw("pitch_bend_range", _as_int)
    note_pitch_bend_range = _rw("note_pitch_bend_range", _as_int)
    playing_position = property(lambda self: 0.0)
    playing_position_enabled = property(lambda self: False)
    can_warp_as = property(lambda self: self._sample is not None)
    can_warp_double = property(lambda self: self._sample is not None
                               and self._sample._warping)
    can_warp_half = property(lambda self: self._sample is not None
                             and self._sample._warping)

    def _require_sample(self, what):
        if self._sample is None:
            raise RuntimeError("%s: Simpler is empty" % what)
        return self._sample

    def crop(self):
        sample = self._require_sample("crop")
        sample._length = sample._end_marker - sample._start_marker
        sample._start_marker = 0
        sample._end_marker = sample._length

    def reverse(self):
        self._require_sample("reverse")
        self._reversed = not self._reversed

    def warp_as(self, beat_time):
        sample = self._require_sample("warp_as")
        sample._warping = True
        self._warped_as = _as_float(beat_time)

    def warp_double(self):
        self._require_sample("warp_double")

    def warp_half(self):
        self._require_sample("warp_half")

    def guess_playback_length(self):
        self._require_sample("guess_playback_length")
        return 4.0

    def replace_sample(self, file_path):
        path = _check_audio_path(file_path, "replace_sample")
        self._sample = Sample(path, canonical_parent=self)
        self.notify_listeners("sample")


# ==========================================================================
# MixerDevice
# ==========================================================================

class _SendParameter(DeviceParameter):
    """A send amount; Live names it after the return track ("A-Reverb")."""

    def __init__(self, mixer, index):
        DeviceParameter.__init__(self, "Send", 0.0, 0.0, 1.0, unit="send",
                                 canonical_parent=mixer)
        self._index = index

    def _return_name(self):
        track = self._canonical_parent._canonical_parent
        song = getattr(track, "_canonical_parent", None)
        returns = getattr(song, "_return_tracks", None) or []
        if self._index < len(returns):
            return returns[self._index].name
        return "%s-Return" % chr(ord("A") + self._index)

    name = property(lambda self: self._return_name())
    original_name = property(lambda self: self._return_name())


class MixerDevice(LomObject):
    """``Live.MixerDevice.MixerDevice``.

    ``crossfade_assign`` is a plain int (0 = A, 1 = none, 2 = B; not on the
    master track) — there is no ``crossfader_assign`` parameter.
    ``panning_mode`` is 0 = stereo, 1 = split stereo.  ``cue_volume``,
    ``crossfader`` and ``song_tempo`` exist on the master track only.
    """

    _LISTENABLE = ("sends", "crossfade_assign", "panning_mode")

    crossfade_assignments = _CROSSFADE_ASSIGNMENTS
    panning_modes = _PANNING_MODES

    def __init__(self, track, send_count=0, is_master=False):
        LomObject.__init__(self, track)
        self._is_master = bool(is_master)
        self._volume = DeviceParameter("Track Volume", 0.85, 0.0, 1.0, unit="fader",
                                       canonical_parent=self)
        self._panning = DeviceParameter("Track Panning", 0.0, -1.0, 1.0,
                                        canonical_parent=self)
        self._track_activator = DeviceParameter(
            "Speaker On", 1.0, 0.0, 1.0, is_quantized=True,
            value_items=("Off", "On"), canonical_parent=self)
        self._left_split_stereo = DeviceParameter("Left Split Stereo", -1.0, -1.0, 1.0,
                                                  canonical_parent=self)
        self._right_split_stereo = DeviceParameter("Right Split Stereo", 1.0, -1.0, 1.0,
                                                   canonical_parent=self)
        self._crossfade_assign = 1
        self._panning_mode = 0
        if self._is_master:
            self._cue_volume = DeviceParameter("Preview Volume", 0.7, 0.0, 1.0, unit="fader",
                                               canonical_parent=self)
            self._crossfader = DeviceParameter("Crossfade", 0.0, -1.0, 1.0,
                                               canonical_parent=self)
            self._song_tempo = DeviceParameter("Song Tempo", 120.0, 20.0, 999.0,
                                               canonical_parent=self)
        self._sends = [self._make_send(i) for i in range(send_count)]

    def _make_send(self, index):
        return _SendParameter(self, index)

    volume = _ro("volume")
    panning = _ro("panning")
    track_activator = _ro("track_activator")
    left_split_stereo = _ro("left_split_stereo")
    right_split_stereo = _ro("right_split_stereo")
    panning_mode = _rw("panning_mode", _as_int, _range_check(0, 1, "panning_mode"))

    @property
    def sends(self):
        return Vector(self._sends)

    def _master_only(self, name):
        if not self._is_master:
            if name == "cue_volume":
                raise RuntimeError("Cue volume available on the main track only!")
            raise RuntimeError("%s is only available on the main track" % name)
        return getattr(self, "_" + name)

    cue_volume = property(lambda self: self._master_only("cue_volume"))
    crossfader = property(lambda self: self._master_only("crossfader"))
    song_tempo = property(lambda self: self._master_only("song_tempo"))

    @property
    def crossfade_assign(self):
        if self._is_master:
            raise RuntimeError("Main track has no crossfader assignment!")
        return self._crossfade_assign

    @crossfade_assign.setter
    def crossfade_assign(self, value):
        if self._is_master:
            raise RuntimeError("Main track has no crossfader assignment!")
        value = _as_int(value, "crossfade_assign")
        if value not in (0, 1, 2):
            raise ValueError("crossfade_assign must be 0 (A), 1 (none) or 2 (B)")
        self._crossfade_assign = value
        self.notify_listeners("crossfade_assign")


# ==========================================================================
# MIDI notes, envelopes, clips
# ==========================================================================

_note_ids = itertools.count(1)


class MidiNoteSpecification(object):
    """``Live.Clip.MidiNoteSpecification(pitch, start_time, duration,
    velocity=100.0, mute=False, probability=1.0, velocity_deviation=0.0,
    release_velocity=64.0)``.

    Write-only value object: the real class exposes **no readable
    attributes**, so the stub keeps them private.
    """

    def __init__(self, pitch, start_time, duration, velocity=100.0, mute=False,
                 probability=1.0, velocity_deviation=0.0, release_velocity=64.0):
        self._pitch = _as_int(pitch, "MidiNoteSpecification.pitch")
        self._start_time = _as_float(start_time, "MidiNoteSpecification.start_time")
        self._duration = _as_float(duration, "MidiNoteSpecification.duration")
        self._velocity = _as_float(velocity, "MidiNoteSpecification.velocity")
        self._mute = _as_bool(mute, "MidiNoteSpecification.mute")
        self._probability = _as_float(probability)
        self._velocity_deviation = _as_float(velocity_deviation)
        self._release_velocity = _as_float(release_velocity)

    def __repr__(self):
        return "<MidiNoteSpecification>"


class MidiNote(object):
    """``Live.Clip.MidiNote`` — what the ``get_*notes*`` calls hand back.

    Attributes are writable; changes reach the clip only through
    ``clip.apply_note_modifications(vector)``.
    """

    __slots__ = ("note_id", "pitch", "start_time", "duration", "velocity", "mute",
                 "probability", "velocity_deviation", "release_velocity")

    def __init__(self, pitch=60, start_time=0.0, duration=0.25, velocity=100.0,
                 mute=False, probability=1.0, velocity_deviation=0.0,
                 release_velocity=64.0, note_id=None):
        self.note_id = next(_note_ids) if note_id is None else int(note_id)
        self.pitch = int(pitch)
        self.start_time = float(start_time)
        self.duration = float(duration)
        self.velocity = float(velocity)
        self.mute = bool(mute)
        self.probability = float(probability)
        self.velocity_deviation = float(velocity_deviation)
        self.release_velocity = float(release_velocity)

    def copy(self):
        return MidiNote(self.pitch, self.start_time, self.duration, self.velocity,
                        self.mute, self.probability, self.velocity_deviation,
                        self.release_velocity, self.note_id)

    def __repr__(self):
        return "<MidiNote id=%d pitch=%d start=%s>" % (self.note_id, self.pitch,
                                                       self.start_time)


class EnvelopeEventControlCoefficients(object):
    """``Live.Envelope.EnvelopeEventControlCoefficients(x1=0.5, y1=0.5, x2=0.5, y2=0.5)``."""

    def __init__(self, x1=0.5, y1=0.5, x2=0.5, y2=0.5):
        self.x1, self.y1, self.x2, self.y2 = float(x1), float(y1), float(x2), float(y2)


class EnvelopeEvent(object):
    """``Live.Envelope.EnvelopeEvent(time, value, control_coefficients=...)``."""

    def __init__(self, time, value, control_coefficients=None):
        self.time = float(time)
        self.value = float(value)
        self.control_coefficients = control_coefficients or EnvelopeEventControlCoefficients()


class Envelope(LomObject):
    """``Live.Envelope.Envelope`` (was ``Live.Clip.AutomationEnvelope`` in Live 11).

    A breakpoint model, as measured on Live 12.4.5 (T2 live test, session clip):

    * a new envelope holds one breakpoint at time 0 with the parameter's value at creation;
    * ``insert_step(t, length, v)`` replaces the breakpoints in ``[t, t + length]`` by four:
      ``(t, value before t)``, ``(t, v)``, ``(t + length, v)``, ``(t + length, value
      after)`` — a step border is two breakpoints at the same time;
    * ``value_at_time(t)`` interpolates linearly; exactly on a time with two breakpoints it
      returns the FIRST one (the value before the border); before the first / after the
      last breakpoint it holds that breakpoint's value;
    * ``create_event(EnvelopeEvent(t, v))`` adds a breakpoint (``v`` in the parameter's
      range); an existing breakpoint at the same time stays and the new one goes after it;
    * ``delete_events_in_range(a, b)`` removes breakpoints with ``a <= time <= b``
      (inclusive); ``events_in_range(a, b)`` lists them.

    Real Live reports ``EnvelopeEvent.value`` in *internal* units (linear gain for
    volume/sends, Hz for filter frequencies) while ``value_at_time`` / ``create_event`` use
    the parameter's range.  The stub keeps the parameter's range everywhere (it cannot know
    every internal curve); handlers read breakpoint values through ``value_at_time``.
    """

    def __init__(self, parameter, clip):
        LomObject.__init__(self, clip)
        self._parameter = parameter
        self._points = [[0.0, float(parameter.value)]]

    parameter = _ro("parameter")

    def _left_value(self, time):
        """Value at ``time`` as Live's value_at_time reports it (before a border)."""
        pts = self._points
        if not pts:
            return float(self._parameter.value)
        if time <= pts[0][0]:
            return pts[0][1]
        for p_time, p_value in pts:
            if abs(p_time - time) < 1e-9:
                return p_value
        previous = None
        for p_time, p_value in pts:
            if p_time > time:
                if previous is None:
                    return p_value
                span = p_time - previous[0]
                if span <= 0:
                    return p_value
                return previous[1] + (p_value - previous[1]) * (time - previous[0]) / span
            previous = (p_time, p_value)
        return pts[-1][1]

    def _right_value(self, time):
        """Value just after ``time`` (the last breakpoint at that time, else interpolated)."""
        same = [p for p in self._points if abs(p[0] - time) < 1e-9]
        if same:
            return same[-1][1]
        return self._left_value(time)

    def insert_step(self, time, length, value, /):
        time, length, value = _as_float(time), _as_float(length), _as_float(value)
        parameter = self._parameter
        if value < parameter.min or value > parameter.max:
            raise RuntimeError("insert_step: value %r outside the parameter range" % value)
        end = time + length
        before = self._left_value(time)
        after = self._right_value(end)
        kept = [p for p in self._points if not (time - 1e-9 <= p[0] <= end + 1e-9)]
        head = [p for p in kept if p[0] < time]
        tail = [p for p in kept if p[0] > end]
        self._points = head + [[time, before], [time, value], [end, value],
                               [end, after]] + tail

    def value_at_time(self, time, /):
        return self._left_value(_as_float(time))

    def events_in_range(self, start_time, end_time, /):
        start_time, end_time = _as_float(start_time), _as_float(end_time)
        return tuple(EnvelopeEvent(t, v) for t, v in self._points
                     if start_time - 1e-9 <= t <= end_time + 1e-9)

    def create_event(self, event, /):
        if not isinstance(event, EnvelopeEvent):
            raise _boost_type_error("Envelope.create_event", event)
        pts = list(self._points)
        pts.append([float(event.time), float(event.value)])
        pts.sort(key=lambda p: p[0])  # stable: a second event at a time goes after the first
        self._points = pts

    def delete_events_in_range(self, start_time, end_time, /):
        start_time, end_time = _as_float(start_time), _as_float(end_time)
        self._points = [p for p in self._points
                        if not (start_time - 1e-9 <= p[0] <= end_time + 1e-9)]


class ClipView(LomObject):
    """``Live.Clip.Clip.View``."""

    def __init__(self, clip):
        LomObject.__init__(self, clip)
        self._grid_quantization = int(GridQuantization.g_sixteenth)
        self._grid_is_triplet = False

    grid_quantization = _rw("grid_quantization", _as_int,
                            _range_check(0, 9, "grid_quantization"))
    grid_is_triplet = _rw("grid_is_triplet", _as_bool)

    def show_loop(self):
        self._showed_loop = True

    def hide_envelope(self):
        self._envelope_visible = False

    def show_envelope(self):
        self._envelope_visible = True

    def select_envelope_parameter(self, parameter, /):
        self._selected_envelope_parameter = parameter


#: clip.quantize() grid (a Song.RecordingQuantization value) -> step in beats.
_RECORD_QUANT_STEP = {1: 1.0, 2: 0.5, 3: 1.0 / 3.0, 4: 0.5, 5: 0.25, 6: 1.0 / 6.0,
                      7: 0.25, 8: 0.125}

#: Maximum arrangement time accepted by create_midi_clip/create_audio_clip.
MAX_ARRANGEMENT_TIME = 1576800.0


def _track_of(obj):
    """The Track that owns ``obj`` (walking canonical_parent), or None."""
    node = obj
    for _ in range(12):
        if node is None:
            return None
        if isinstance(node, Track):
            return node
        node = getattr(node, "_canonical_parent", None)
    return None


class Clip(LomObject):
    """``Live.Clip.Clip`` — a session or arrangement clip (MIDI or audio).

    Audio-only properties raise ``RuntimeError`` on MIDI clips and the note
    API raises on audio clips.  ``launch_quantization`` is a
    ``Clip.ClipLaunchQuantization`` (0 = global), ``quantize(grid, amount)``
    takes a ``Song.RecordingQuantization`` grid.

    Measured on Live 12.4.5 (T2 live test, 2026-09-10):

    * **Unlooped clips** — Live keeps two loop braces per clip.  While ``looping`` is off
      the clip plays ``loop_start..loop_end`` (``length``, crop, duplicate-loop and an
      arrangement clip's ``end_time`` follow it); ``start_marker`` is tied to
      ``loop_start`` (writing ``start_marker`` is silently ignored, writing ``loop_start``
      moves both; ``loop_start >= loop_end`` raises "Cannot set LoopStart behind LoopEnd");
      ``end_marker`` is a separate value that does not move the end.  Switching looping
      off restores the previous unlooped brace (the first time: the start..end markers)
      and remembers the looped brace; switching it on restores the looped brace — an
      arrangement clip keeps its timeline length then.  ``position`` moves the brace and
      the start marker (not the end marker).  ``length`` = ``loop_end - loop_start``.
    * ``crop()`` keeps ``min(start_marker, loop_start) .. loop_end`` of a looped clip (the
      lead-in survives) and the brace of an unlooped clip, moved to beat 0;
      ``duplicate_loop()`` copies the notes of the brace one brace-length later and pushes
      notes after the brace back by the same amount (unlooped: the end marker stays).
    * **Notes** — Live never keeps two overlapping notes of one pitch.  ``add_new_notes``:
      a new note replaces a same-pitch note with the same start (also within one batch),
      swallows same-pitch notes that start inside it and shortens one it starts inside
      of; only the ids of surviving notes are returned.  ``apply_note_modifications``:
      afterwards, of two same-pitch notes on one start the newer id survives, and a note
      reaching into the next same-pitch note is cut at that note's start.
    * ``pitch_fine`` accepts any cents value and carries whole semitones into
      ``pitch_coarse`` while ``|fine| >= 50`` (60 -> +1 / -40, 50 -> +1 / -50,
      -50 -> -1 / +50); at the coarse limits (+-48) the fine value clamps to +-50.
    """

    _LISTENABLE = ("name", "notes", "playing_status", "loop_start", "loop_end",
                   "start_marker", "end_marker", "looping", "color", "muted",
                   "warping", "warp_mode", "gain", "pitch_coarse", "pitch_fine",
                   "playing_position", "is_recording", "has_envelopes")

    def __init__(self, name="", length=4.0, is_midi=True, canonical_parent=None,
                 file_path="", arrangement=False, take_lane=False):
        LomObject.__init__(self, canonical_parent)
        length = float(length)
        self._name = str(name)
        self._color_index = 0
        self._color = 0
        self._is_midi = bool(is_midi)
        self._is_arrangement = bool(arrangement)
        self._is_take_lane = bool(take_lane)
        self._loop_start = 0.0
        self._loop_end = length
        self._start_marker = 0.0
        self._end_marker = length
        self._looping = True
        self._muted = False
        self._is_playing = False
        self._is_recording = False
        self._is_triggered = False
        self._is_overdubbing = False
        self._playing_position = 0.0
        self._start_time = 0.0
        self._arrangement_length = length
        self._signature_numerator = 4
        self._signature_denominator = 4
        self._launch_mode = int(LaunchMode.trigger)
        self._launch_quantization = int(ClipLaunchQuantization.q_global)
        self._legato = False
        self._velocity_amount = 0.0
        self._groove = None
        self._view = ClipView(self)
        self._will_record_on_start = False
        # audio only
        self._warping = not is_midi
        self._warp_mode = int(WarpMode.beats)
        self._gain = 0.4
        self._pitch_coarse = 0
        self._pitch_fine = 0.0
        self._file_path = file_path
        self._sample_length = 0 if is_midi else 352800
        self._sample_rate = 44100.0
        self._ram_mode = False
        self._warp_markers = [] if is_midi else [WarpMarker(0.0, 0.0),
                                                 WarpMarker(8.0, length)]
        # notes / envelopes
        self._notes = []
        self._selected_ids = set()
        self._envelopes = {}

    # -- identity --------------------------------------------------------------
    name = _rw("name", _as_str)
    color = _rw("color", _as_int)
    color_index = _rw("color_index", _as_int, _range_check(0, 69, "color_index"))
    muted = _rw("muted", _as_bool)
    view = _ro("view")
    is_midi_clip = property(lambda self: self._is_midi)
    is_audio_clip = property(lambda self: not self._is_midi)
    is_arrangement_clip = property(lambda self: self._is_arrangement)
    is_session_clip = property(lambda self: not self._is_arrangement)
    is_take_lane_clip = property(lambda self: self._is_take_lane)
    is_recording = _ro("is_recording")
    is_triggered = _ro("is_triggered")
    is_overdubbing = _ro("is_overdubbing")
    will_record_on_start = _ro("will_record_on_start")
    playing_position = _ro("playing_position")
    has_groove = property(lambda self: self._groove is not None)
    groove = _rw("groove")
    signature_numerator = _rw("signature_numerator", _as_int,
                              _range_check(1, 99, "signature_numerator"))
    signature_denominator = _rw("signature_denominator", _as_int,
                                lambda self, v: _check_denominator(v))
    launch_mode = _rw("launch_mode", _as_int, _range_check(0, 3, "launch_mode"))
    launch_quantization = _rw("launch_quantization", _as_int,
                              _range_check(0, 14, "launch_quantization"))
    legato = _rw("legato", _as_bool)
    velocity_amount = _rw("velocity_amount", _as_float,
                          _range_check(0.0, 1.0, "velocity_amount"))

    # -- playback ----------------------------------------------------------------
    @property
    def is_playing(self):
        return self._is_playing

    @is_playing.setter
    def is_playing(self, value):
        if _as_bool(value):
            self.fire()
        else:
            self.stop()

    # -- markers / loop ------------------------------------------------------------
    @property
    def looping(self):
        return self._looping

    @looping.setter
    def looping(self, value):
        value = _as_bool(value, "Clip.looping")
        if value and not self._is_midi and not self._warping:
            raise RuntimeError("looping: unwarped audio clips cannot be looped")
        if value == self._looping:
            return
        if not value:
            self._looped_brace = (self._loop_start, self._loop_end)
            brace = getattr(self, "_unlooped_brace", None) or (self._start_marker,
                                                                self._end_marker)
            self._loop_start, self._loop_end = brace
            self._start_marker = self._loop_start
        else:
            self._unlooped_brace = (self._loop_start, self._loop_end)
            if self._is_arrangement:
                self._arrangement_length = self._loop_end - self._loop_start
            brace = getattr(self, "_looped_brace", None)
            if brace is not None:
                self._loop_start, self._loop_end = brace
        self._looping = value
        self.notify_listeners("looping")

    @property
    def loop_start(self):
        return self._loop_start

    @loop_start.setter
    def loop_start(self, value):
        value = _as_float(value, "Clip.loop_start")
        if value >= self._loop_end:
            raise RuntimeError("Cannot set LoopStart behind LoopEnd")
        self._loop_start = value
        if not self._looping:
            self._start_marker = value
        self.notify_listeners("loop_start")

    @property
    def loop_end(self):
        return self._loop_end

    @loop_end.setter
    def loop_end(self, value):
        value = _as_float(value, "Clip.loop_end")
        if value <= self._loop_start:
            raise RuntimeError("Cannot set LoopEnd in front of LoopStart")
        self._loop_end = value
        self.notify_listeners("loop_end")

    @property
    def start_marker(self):
        return self._start_marker

    @start_marker.setter
    def start_marker(self, value):
        value = _as_float(value, "Clip.start_marker")
        if not self._looping:
            return  # Live ignores it: the start marker follows loop_start
        if value >= self._end_marker:
            raise RuntimeError("start_marker cannot be set behind the end marker")
        self._start_marker = value
        self.notify_listeners("start_marker")

    @property
    def end_marker(self):
        return self._end_marker

    @end_marker.setter
    def end_marker(self, value):
        value = _as_float(value, "Clip.end_marker")
        if value <= self._start_marker:
            raise RuntimeError("end_marker cannot be set before the start marker")
        self._end_marker = value
        self.notify_listeners("end_marker")

    @property
    def position(self):
        return self._loop_start

    @position.setter
    def position(self, value):
        value = _as_float(value, "Clip.position")
        span = self._loop_end - self._loop_start
        self._loop_start, self._loop_end = value, value + span
        if not self._looping:
            self._start_marker = value

    @property
    def length(self):
        return self._loop_end - self._loop_start

    @property
    def start_time(self):
        return self._start_time

    @property
    def end_time(self):
        if self._is_arrangement:
            if not self._looping:
                return self._start_time + (self._loop_end - self._loop_start)
            return self._start_time + self._arrangement_length
        return self._loop_end

    # -- audio only ------------------------------------------------------------------
    def _audio(self, name):
        if self._is_midi:
            raise RuntimeError("%s is only available for audio clips" % name)
        return getattr(self, "_" + name)

    def _set_audio(self, name, value, check=None):
        if self._is_midi:
            raise RuntimeError("%s is only available for audio clips" % name)
        if check is not None:
            check(self, value)
        setattr(self, "_" + name, value)
        self.notify_listeners(name)

    file_path = property(lambda self: self._audio("file_path"))
    sample_length = property(lambda self: self._audio("sample_length"))
    sample_rate = property(lambda self: self._audio("sample_rate"))

    @property
    def available_warp_modes(self):
        self._audio("warping")
        return (0, 1, 2, 3, 4, 6)

    warp_markers = property(lambda self: tuple(self._audio("warp_markers")))
    warping = property(lambda self: self._audio("warping"),
                       lambda self, v: self._set_audio("warping", _as_bool(v)))
    warp_mode = property(lambda self: self._audio("warp_mode"),
                         lambda self, v: self._set_audio(
                             "warp_mode", _as_int(v, "Clip.warp_mode"),
                             lambda _s, x: x in (0, 1, 2, 3, 4, 6) or _raise(
                                 ValueError("warp_mode %r not available" % x))))
    gain = property(lambda self: self._audio("gain"),
                    lambda self, v: self._set_audio("gain", _as_float(v, "Clip.gain"),
                                                    _range_check(0.0, 1.0, "gain")))
    pitch_coarse = property(lambda self: self._audio("pitch_coarse"),
                            lambda self, v: self._set_audio(
                                "pitch_coarse", _as_int(v, "Clip.pitch_coarse"),
                                _range_check(-48, 48, "pitch_coarse")))

    @property
    def pitch_fine(self):
        return self._audio("pitch_fine")

    @pitch_fine.setter
    def pitch_fine(self, value):
        """Any cents; whole semitones carry into ``pitch_coarse`` while |fine| >= 50."""
        self._audio("pitch_fine")
        fine = _as_float(value, "Clip.pitch_fine")
        carry = 0
        if fine >= 50.0:
            carry = int((fine - 50.0) // 100.0) + 1
        elif fine <= -50.0:
            carry = -(int((-fine - 50.0) // 100.0) + 1)
        coarse = self._pitch_coarse + carry
        if -48 <= coarse <= 48:
            fine -= carry * 100.0
        else:
            coarse = max(-48, min(48, coarse))
            fine = max(-50.0, min(50.0, fine))
        self._pitch_coarse = coarse
        self._pitch_fine = fine
        self.notify_listeners("pitch_fine")

    ram_mode = property(lambda self: self._audio("ram_mode"),
                        lambda self, v: self._set_audio("ram_mode", _as_bool(v)))

    @property
    def gain_display_string(self):
        self._audio("gain")
        return "0.0 dB" if abs(self._gain - 0.4) < 1e-6 else "%.1f dB" % (
            (self._gain - 0.4) * 60.0)

    def add_warp_marker(self, warp_marker):
        self._audio("warp_markers")
        if not self._warping:
            raise RuntimeError("add_warp_marker: the clip is not warped")
        if isinstance(warp_marker, WarpMarker):
            marker = warp_marker
        elif isinstance(warp_marker, dict) and "beat_time" in warp_marker:
            marker = WarpMarker(float(warp_marker.get("sample_time",
                                                      warp_marker["beat_time"] * 0.5)),
                                float(warp_marker["beat_time"]))
        else:
            raise _boost_type_error("Clip.add_warp_marker", warp_marker)
        self._warp_markers.append(marker)
        self._warp_markers.sort(key=lambda m: m.beat_time)

    def move_warp_marker(self, marker_beat_time, beat_time_distance):
        self._audio("warp_markers")
        for index, marker in enumerate(self._warp_markers):
            if abs(marker.beat_time - marker_beat_time) < 1e-9:
                self._warp_markers[index] = WarpMarker(
                    marker.sample_time, marker.beat_time + beat_time_distance)
                return
        raise RuntimeError("move_warp_marker: no marker at %r" % marker_beat_time)

    def remove_warp_marker(self, beat_time):
        self._audio("warp_markers")
        self._warp_markers = [m for m in self._warp_markers
                              if abs(m.beat_time - beat_time) >= 1e-9]

    def beat_to_sample_time(self, beat_time):
        self._audio("warping")
        if not self._warping:
            raise RuntimeError("beat_to_sample_time: the clip is not warped")
        return float(beat_time) * self._sample_rate * 0.5

    def sample_to_beat_time(self, sample_time):
        self._audio("warping")
        if not self._warping:
            raise RuntimeError("sample_to_beat_time: the clip is not warped")
        return float(sample_time) / (self._sample_rate * 0.5)

    def seconds_to_sample_time(self, seconds):
        self._audio("warping")
        if self._warping:
            raise RuntimeError("seconds_to_sample_time: the clip is warped")
        return float(seconds) * self._sample_rate

    # -- MIDI --------------------------------------------------------------------------
    def _midi(self, what):
        if not self._is_midi:
            raise RuntimeError("%s: not available on audio clips" % what)

    def add_new_notes(self, specs, /):
        """Returns a tuple (IntU64Vector) with the new note ids."""
        self._midi("add_new_notes")
        ids = []
        for spec in specs:
            if not isinstance(spec, MidiNoteSpecification):
                raise _boost_type_error("Clip.add_new_notes", spec)
            if not (0 <= spec._pitch <= 127):
                raise ValueError("add_new_notes: pitch %d outside 0..127" % spec._pitch)
            if spec._duration <= 0.0:
                raise ValueError("add_new_notes: duration must be > 0")
            note = MidiNote(spec._pitch, spec._start_time, spec._duration,
                            spec._velocity, spec._mute, spec._probability,
                            spec._velocity_deviation, spec._release_velocity)
            self._merge_note(note)
            ids.append(note)
        self.notify_listeners("notes")
        alive = set(id(n) for n in self._notes)
        return tuple(n.note_id for n in ids if id(n) in alive)

    def _merge_note(self, note):
        """Add ``note`` the way Live does: same pitch + start replaces, same-pitch notes
        starting inside it are swallowed, one it starts inside of is shortened."""
        eps = 1e-9
        end = note.start_time + note.duration
        kept = []
        for other in self._notes:
            if other.pitch != note.pitch:
                kept.append(other)
                continue
            if abs(other.start_time - note.start_time) < eps:
                continue  # replaced
            if note.start_time < other.start_time < end - eps:
                continue  # swallowed
            if other.start_time < note.start_time < other.start_time + other.duration - eps:
                other.duration = note.start_time - other.start_time
            kept.append(other)
        kept.append(note)
        self._notes = kept

    def _resolve_overlaps(self):
        """After a modification: same pitch + start keeps the newest id, a note reaching
        into the next same-pitch note is cut at its start."""
        eps = 1e-9
        by_pitch = {}
        for note in self._notes:
            by_pitch.setdefault(note.pitch, []).append(note)
        survivors = []
        for group in by_pitch.values():
            group.sort(key=lambda n: (n.start_time, -n.note_id))
            kept = []
            for note in group:
                if kept and abs(kept[-1].start_time - note.start_time) < eps:
                    continue  # same start: the newer id (first after the sort) survives
                kept.append(note)
            for current, following in zip(kept, kept[1:]):
                if current.start_time + current.duration > following.start_time + eps:
                    current.duration = following.start_time - current.start_time
            survivors.extend(kept)
        alive = set(id(n) for n in survivors)
        self._notes = [n for n in self._notes if id(n) in alive]

    def _select(self, from_pitch, pitch_span, from_time, time_span):
        return [n for n in self._notes
                if from_pitch <= n.pitch < from_pitch + pitch_span
                and from_time <= n.start_time < from_time + time_span]

    def get_notes_extended(self, from_pitch, pitch_span, from_time, time_span):
        """MidiNoteVector of copies of the notes *starting* in the area."""
        self._midi("get_notes_extended")
        notes = self._select(_as_int(from_pitch), _as_int(pitch_span),
                             _as_float(from_time), _as_float(time_span))
        notes.sort(key=lambda n: (n.start_time, n.pitch))
        return _note_vector(n.copy() for n in notes)

    def get_all_notes_extended(self):
        self._midi("get_all_notes_extended")
        notes = sorted(self._notes, key=lambda n: (n.start_time, n.pitch))
        return _note_vector(n.copy() for n in notes)

    def get_notes_by_id(self, note_ids):
        self._midi("get_notes_by_id")
        wanted = set(int(i) for i in note_ids)
        return _note_vector(n.copy() for n in self._notes if n.note_id in wanted)

    def get_selected_notes_extended(self):
        self._midi("get_selected_notes_extended")
        return _note_vector(n.copy() for n in self._notes
                            if n.note_id in self._selected_ids)

    def apply_note_modifications(self, notes, /):
        """Only accepts a MidiNoteVector (as returned by the get_* calls)."""
        self._midi("apply_note_modifications")
        if not isinstance(notes, MidiNoteVector):
            raise _boost_type_error("Clip.apply_note_modifications (expects the "
                                    "MidiNoteVector returned by get_notes_extended)",
                                    notes)
        by_id = dict((n.note_id, n) for n in self._notes)
        for note in notes:
            if note.note_id not in by_id:
                raise RuntimeError("apply_note_modifications: note id %d is not in "
                                   "the clip" % note.note_id)
        for note in notes:
            target = by_id[note.note_id]
            for attr in ("pitch", "start_time", "duration", "velocity", "mute",
                         "probability", "velocity_deviation", "release_velocity"):
                setattr(target, attr, getattr(note, attr))
        self._resolve_overlaps()
        self.notify_listeners("notes")

    def remove_notes_extended(self, from_pitch, pitch_span, from_time, time_span):
        self._midi("remove_notes_extended")
        doomed = set(id(n) for n in self._select(_as_int(from_pitch), _as_int(pitch_span),
                                                 _as_float(from_time), _as_float(time_span)))
        self._notes = [n for n in self._notes if id(n) not in doomed]
        self.notify_listeners("notes")

    def remove_notes_by_id(self, note_ids, /):
        self._midi("remove_notes_by_id")
        wanted = set(int(i) for i in note_ids)
        self._notes = [n for n in self._notes if n.note_id not in wanted]
        self._selected_ids -= wanted
        self.notify_listeners("notes")

    def select_all_notes(self):
        self._midi("select_all_notes")
        self._selected_ids = set(n.note_id for n in self._notes)

    def deselect_all_notes(self):
        self._midi("deselect_all_notes")
        self._selected_ids = set()

    def select_notes_by_id(self, note_ids, /):
        self._midi("select_notes_by_id")
        self._selected_ids |= set(int(i) for i in note_ids)

    def duplicate_notes_by_id(self, note_ids, destination_time=None,
                              transposition_amount=0):
        self._midi("duplicate_notes_by_id")
        wanted = set(int(i) for i in note_ids)
        source = [n for n in self._notes if n.note_id in wanted]
        if not source:
            return ()
        first = min(n.start_time for n in source)
        if destination_time is None:
            destination_time = max(n.start_time + n.duration for n in source)
        ids = []
        for note in source:
            clone = MidiNote(note.pitch + int(transposition_amount),
                             float(destination_time) + (note.start_time - first),
                             note.duration, note.velocity, note.mute, note.probability,
                             note.velocity_deviation, note.release_velocity)
            self._notes.append(clone)
            ids.append(clone.note_id)
        self.notify_listeners("notes")
        return tuple(ids)

    def duplicate_region(self, region_start, region_length, destination_time,
                         pitch=-1, transposition_amount=0):
        self._midi("duplicate_region")
        extra = []
        for note in self._notes:
            if region_start <= note.start_time < region_start + region_length:
                if pitch != -1 and note.pitch != pitch:
                    continue
                extra.append(MidiNote(
                    note.pitch + transposition_amount,
                    destination_time + (note.start_time - region_start),
                    note.duration, note.velocity, note.mute, note.probability,
                    note.velocity_deviation, note.release_velocity))
        self._notes.extend(extra)
        self.notify_listeners("notes")

    def quantize(self, grid, amount, /):
        """``grid`` is a Song.RecordingQuantization value (1..8)."""
        grid = _as_int(grid, "Clip.quantize")
        amount = _as_float(amount, "Clip.quantize")
        step = _RECORD_QUANT_STEP.get(grid)
        if step is None:
            return
        for note in self._notes:
            target = round(note.start_time / step) * step
            note.start_time += (target - note.start_time) * amount
        self.notify_listeners("notes")

    def quantize_pitch(self, pitch, grid, amount, /):
        self._midi("quantize_pitch")
        step = _RECORD_QUANT_STEP.get(_as_int(grid))
        if step is None:
            return
        for note in self._notes:
            if note.pitch == _as_int(pitch):
                target = round(note.start_time / step) * step
                note.start_time += (target - note.start_time) * _as_float(amount)

    def note_number_to_name(self, midi_pitch):
        names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
        pitch = _as_int(midi_pitch)
        return "%s%d" % (names[pitch % 12], pitch // 12 - 2)

    # -- legacy (pre-Live 11) note API: still present, do not use ---------------------
    def get_notes(self, from_time, from_pitch, time_span, pitch_span):
        self._midi("get_notes")
        notes = self._select(from_pitch, pitch_span, from_time, time_span)
        return tuple((n.pitch, n.start_time, n.duration, int(n.velocity), n.mute)
                     for n in notes)

    def get_selected_notes(self):
        self._midi("get_selected_notes")
        return tuple((n.pitch, n.start_time, n.duration, int(n.velocity), n.mute)
                     for n in self._notes if n.note_id in self._selected_ids)

    def set_notes(self, notes, /):
        self._midi("set_notes")
        for pitch, start, duration, velocity, mute in notes:
            self._notes.append(MidiNote(pitch, start, duration, velocity, mute))
        self.notify_listeners("notes")

    def remove_notes(self, from_time, from_pitch, time_span, pitch_span, /):
        self._midi("remove_notes")
        self.remove_notes_extended(from_pitch, pitch_span, from_time, time_span)

    def replace_selected_notes(self, notes, /):
        self._midi("replace_selected_notes")
        self._notes = [n for n in self._notes if n.note_id not in self._selected_ids]
        self.set_notes(notes)

    # -- transport / editing ---------------------------------------------------------
    def fire(self):
        self._is_playing = True
        self._is_triggered = False
        slot = self._canonical_parent
        track = getattr(slot, "_canonical_parent", None)
        if isinstance(slot, ClipSlot) and isinstance(track, Track):
            for other in track._clip_slots:
                if other is not slot and other._clip is not None:
                    other._clip._is_playing = False
            track._playing_slot_index = track._clip_slots.index(slot)
        self.notify_listeners("playing_status")

    def stop(self):
        slot = self._canonical_parent
        track = getattr(slot, "_canonical_parent", None)
        if self._is_playing and isinstance(track, Track):
            track._playing_slot_index = -1
        self._is_playing = False
        self.notify_listeners("playing_status")

    def set_fire_button_state(self, state, /):
        self._is_triggered = _as_bool(state)

    def move_playing_pos(self, beats, /):
        if self._is_playing:
            self._playing_position += _as_float(beats)

    def scrub(self, scrub_position):
        self._playing_position = _as_float(scrub_position)

    def stop_scrub(self):
        pass

    def crop(self):
        """Looped: keeps ``min(start_marker, loop_start) .. loop_end``; unlooped: the
        brace — moved to beat 0 (Live 12.4.5)."""
        eps = 1e-9
        start = min(self._start_marker, self._loop_start) if self._looping \
            else self._loop_start
        end = self._loop_end
        if self._is_midi:
            self._notes = [n for n in self._notes if start - eps <= n.start_time < end - eps]
            for note in self._notes:
                note.start_time -= start
        self._loop_start -= start
        self._loop_end -= start
        self._start_marker -= start
        if self._looping:
            self._end_marker = self._loop_end
        else:
            self._end_marker = max(self._start_marker + 1e-3, self._end_marker - start)

    def duplicate_loop(self):
        """Copies the brace one brace-length later; later notes are pushed back."""
        eps = 1e-9
        start, end = self._loop_start, self._loop_end
        span = end - start
        if self._is_midi:
            for note in self._notes:
                if note.start_time >= end - eps:
                    note.start_time += span
            extra = [MidiNote(n.pitch, n.start_time + span, n.duration, n.velocity,
                              n.mute, n.probability, n.velocity_deviation,
                              n.release_velocity)
                     for n in self._notes if start - eps <= n.start_time < end - eps]
            self._notes.extend(extra)
        self._loop_end = end + span
        if self._looping:
            self._end_marker = max(self._end_marker, end + span)

    # -- automation ------------------------------------------------------------------
    def _envelope_allowed(self, parameter):
        if self._is_arrangement:
            return False
        own = _track_of(self)
        other = _track_of(parameter)
        return own is None or other is None or own is other

    def automation_envelope(self, parameter, /):
        """None for arrangement clips and for parameters of another track."""
        if not self._envelope_allowed(parameter):
            return None
        return self._envelopes.get(id(parameter))

    def create_automation_envelope(self, parameter, /):
        if not isinstance(parameter, DeviceParameter):
            raise _boost_type_error("Clip.create_automation_envelope", parameter)
        if not self._envelope_allowed(parameter):
            raise RuntimeError("create_automation_envelope: not possible for this "
                               "clip/parameter (arrangement clip or other track)")
        if id(parameter) in self._envelopes:
            raise RuntimeError("create_automation_envelope: the envelope already exists")
        envelope = Envelope(parameter, self)
        self._envelopes[id(parameter)] = envelope
        self.notify_listeners("has_envelopes")
        return envelope

    @property
    def automation_envelopes(self):
        return Vector(self._envelopes.values())

    @property
    def has_envelopes(self):
        return bool(self._envelopes)

    def clear_envelope(self, parameter, /):
        self._envelopes.pop(id(parameter), None)
        self.notify_listeners("has_envelopes")

    def clear_all_envelopes(self):
        self._envelopes = {}
        self.notify_listeners("has_envelopes")


def _raise(error):
    raise error


def _mark_deleted(obj):
    """Deleted LOM objects compare equal to None (see LomObject.__eq__)."""
    if isinstance(obj, LomObject):
        obj._deleted = True
        for child in getattr(obj, "_devices", ()) or ():
            _mark_deleted(child)
        for slot in getattr(obj, "_clip_slots", ()) or ():
            _mark_deleted(slot)
            _mark_deleted(slot._clip)
        for clip in getattr(obj, "_arrangement_clips", ()) or ():
            _mark_deleted(clip)
        for chain in getattr(obj, "_chains", ()) or ():
            _mark_deleted(chain)
        for param in getattr(obj, "_parameters", ()) or ():
            _mark_deleted(param)


def _check_denominator(value):
    if value not in (1, 2, 4, 8, 16):
        raise ValueError("signature_denominator must be 1, 2, 4, 8 or 16")


# ==========================================================================
# ClipSlot
# ==========================================================================

class ClipSlot(LomObject):
    """``Live.ClipSlot.ClipSlot``.

    ``create_clip(length)`` (MIDI tracks, empty slot, length > 0),
    ``create_audio_clip(path)`` (audio tracks, absolute path),
    ``fire(record_length=..., launch_quantization=..., force_legato=False)``,
    ``duplicate_clip_to(target)`` returns None.
    """

    _LISTENABLE = ("has_clip", "playing_status", "is_triggered", "color",
                   "has_stop_button")

    def __init__(self, track=None):
        LomObject.__init__(self, track)
        self._clip = None
        self._is_triggered = False
        self._has_stop_button = True
        self._will_record_on_start = False

    clip = _ro("clip")
    is_triggered = _ro("is_triggered")
    has_stop_button = _rw("has_stop_button", _as_bool)
    will_record_on_start = _ro("will_record_on_start")
    is_group_slot = property(lambda self: bool(getattr(self._canonical_parent,
                                                       "_is_group", False)))
    controls_other_clips = property(lambda self: False)

    @property
    def has_clip(self):
        return self._clip is not None

    @property
    def is_playing(self):
        return self._clip is not None and self._clip._is_playing

    @property
    def is_recording(self):
        return self._clip is not None and self._clip._is_recording

    @property
    def playing_status(self):
        if self.is_recording:
            return int(ClipSlotPlayingState.recording)
        return int(ClipSlotPlayingState.started if self.is_playing
                   else ClipSlotPlayingState.stopped)

    @property
    def color(self):
        return self._clip._color if self._clip is not None else None

    @property
    def color_index(self):
        return self._clip._color_index if self._clip is not None else None

    def _track(self):
        return self._canonical_parent

    def _set_clip(self, clip):
        self._clip = clip
        self.notify_listeners("has_clip")

    def create_clip(self, length, /):
        length = _as_float(length, "ClipSlot.create_clip")
        if self._clip is not None:
            raise RuntimeError("create_clip: the clip slot is not empty")
        track = self._track()
        if track is not None and not track.has_midi_input:
            raise RuntimeError("create_clip: can only create MIDI clips on MIDI tracks")
        if track is not None and track._is_frozen:
            raise RuntimeError("create_clip: the track is frozen")
        if length <= 0.0:
            raise RuntimeError("create_clip: length must be greater than 0")
        clip = Clip("", length, True, self)
        self._set_clip(clip)
        return clip

    def create_audio_clip(self, path, /):
        path = _check_audio_path(path, "ClipSlot.create_audio_clip")
        if self._clip is not None:
            raise RuntimeError("create_audio_clip: the clip slot is not empty")
        track = self._track()
        if track is not None and not track.has_audio_input:
            raise RuntimeError("create_audio_clip: not an audio track")
        if track is not None and track._is_frozen:
            raise RuntimeError("create_audio_clip: the track is frozen")
        clip = Clip(os.path.splitext(os.path.basename(path.replace("\\", "/")))[0],
                    8.0, False, self, path)
        self._set_clip(clip)
        return clip

    def delete_clip(self):
        if self._clip is None:
            raise RuntimeError("delete_clip: the clip slot is empty")
        _mark_deleted(self._clip)
        self._set_clip(None)

    def duplicate_clip_to(self, target_slot, /):
        if self._clip is None:
            raise RuntimeError("duplicate_clip_to: the source slot is empty")
        if not isinstance(target_slot, ClipSlot):
            raise _boost_type_error("ClipSlot.duplicate_clip_to", target_slot)
        source_track, target_track = self._track(), target_slot._track()
        if source_track is not None and target_track is not None and \
                source_track.has_midi_input != target_track.has_midi_input:
            raise RuntimeError("duplicate_clip_to: source and target track types differ")
        target_slot._set_clip(_clone_clip(self._clip, target_slot))

    def fire(self, record_length=None, launch_quantization=None, force_legato=False):
        """Fire the clip, or the stop button when empty (records when armed).

        Live 12.4.5 refuses ``force_legato`` on an empty slot."""
        if force_legato and self._clip is None:
            raise RuntimeError("Can only pass force_legato to non-empty slots.")
        self._is_triggered = False
        track = self._track()
        if self._clip is not None:
            if record_length is not None:
                raise RuntimeError("fire: record_length is only allowed on empty slots")
            self._clip.fire()
        elif track is not None and getattr(track, "_arm", False):
            clip = Clip("", float(record_length or 4.0), track.has_midi_input, self)
            clip._is_recording = True
            clip._is_playing = True
            self._set_clip(clip)
            track._playing_slot_index = track._clip_slots.index(self)
        elif track is not None and self._has_stop_button:
            track.stop_all_clips()
        self.notify_listeners("playing_status")

    def stop(self):
        track = self._track()
        if track is not None:
            track.stop_all_clips(False)

    def set_fire_button_state(self, state, /):
        self._is_triggered = _as_bool(state)


def _clone_clip(source, parent, arrangement=False):
    clone = Clip(source._name, source.length, source._is_midi, parent,
                 source._file_path, arrangement=arrangement)
    clone._notes = [MidiNote(n.pitch, n.start_time, n.duration, n.velocity, n.mute,
                             n.probability, n.velocity_deviation, n.release_velocity)
                    for n in source._notes]
    clone._color_index = source._color_index
    clone._color = source._color
    clone._looping = source._looping
    clone._loop_start, clone._loop_end = source._loop_start, source._loop_end
    clone._start_marker, clone._end_marker = source._start_marker, source._end_marker
    for brace in ("_looped_brace", "_unlooped_brace"):
        if brace in source.__dict__:
            setattr(clone, brace, source.__dict__[brace])
    clone._warping, clone._warp_mode = source._warping, source._warp_mode
    clone._gain = source._gain
    return clone


# ==========================================================================
# Routing / Track
# ==========================================================================

class RoutingType(object):
    """``Live.Track.RoutingType`` — compare by value, assign one of
    ``track.available_*_routing_types``."""

    def __init__(self, display_name, category=RoutingTypeCategory.external,
                 attached_object=None):
        self._display_name = display_name
        self._category = int(category)
        self._attached_object = attached_object

    display_name = property(lambda self: self._display_name)
    category = property(lambda self: self._category)
    attached_object = property(lambda self: self._attached_object)

    def __eq__(self, other):
        return isinstance(other, RoutingType) and other._display_name == self._display_name

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(("RoutingType", self._display_name))

    def __repr__(self):
        return "<RoutingType %r>" % self._display_name


class RoutingChannel(object):
    """``Live.Track.RoutingChannel`` (``layout``: 0 midi, 1 mono, 2 stereo)."""

    def __init__(self, display_name, layout=RoutingChannelLayout.stereo):
        self._display_name = display_name
        self._layout = int(layout)

    display_name = property(lambda self: self._display_name)
    layout = property(lambda self: self._layout)

    def __eq__(self, other):
        return isinstance(other, RoutingChannel) and \
            other._display_name == self._display_name

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(("RoutingChannel", self._display_name))

    def __repr__(self):
        return "<RoutingChannel %r>" % self._display_name


class TrackView(LomObject):
    """``Live.Track.Track.View``.

    ``selected_device`` is read-only here (Cycling '74 documents get/observe)
    — select a device with ``song.view.select_device(device)``.
    """

    _LISTENABLE = ("selected_device", "device_insert_mode", "is_collapsed")

    def __init__(self, track):
        LomObject.__init__(self, track)
        self._selected_device = None
        self._device_insert_mode = int(DeviceInsertMode.default)
        self._is_collapsed = False

    selected_device = _ro("selected_device")
    device_insert_mode = _rw("device_insert_mode", _as_int,
                             _range_check(0, 2, "device_insert_mode"))
    is_collapsed = _rw("is_collapsed", _as_bool)

    def select_instrument(self):
        """Selects the track's instrument (or first device); False if none."""
        track = self._canonical_parent
        devices = track._devices
        for device in devices:
            if device.type == int(DeviceType.instrument):
                self._selected_device = device
                return True
        if devices:
            self._selected_device = devices[0]
            return True
        return False


class TakeLane(LomObject):
    """``Live.TakeLane.TakeLane`` (Live 12.0+)."""

    def __init__(self, track, name="Take 1"):
        LomObject.__init__(self, track)
        self._name = name
        self._arrangement_clips = []

    name = _rw("name", _as_str)

    @property
    def arrangement_clips(self):
        return Vector(self._arrangement_clips)

    def create_midi_clip(self, start_time, length, /):
        track = self._canonical_parent
        clip = track._new_arrangement_midi_clip(start_time, length, owner=self)
        clip._is_take_lane = True
        self._arrangement_clips.append(clip)
        return clip

    def create_audio_clip(self, file_path, start_time, /):
        track = self._canonical_parent
        clip = track._new_arrangement_audio_clip(file_path, start_time, owner=self)
        clip._is_take_lane = True
        self._arrangement_clips.append(clip)
        return clip


_TRACK_KINDS = ("midi", "audio", "return", "master")


class Track(_DeviceContainer):
    """``Live.Track.Track`` — MIDI, audio, return or main (master) track.

    Stub-only construction argument ``kind`` (midi/audio/return/master) is
    stored as ``_kind``; real Live has no such property — use
    ``has_midi_input`` / membership in ``song.return_tracks`` /
    ``song.master_track`` instead.

    ``arm`` raises on tracks that cannot be armed, ``fold_state`` raises on
    non-group tracks, input routing is not available on return/main tracks
    and output routing is not available on the main track.
    """

    _LISTENABLE = ("name", "mute", "solo", "arm", "devices", "playing_slot_index",
                   "color", "color_index", "fired_slot_index", "clip_slots",
                   "arrangement_clips", "current_monitoring_state",
                   "input_routing_type", "output_routing_type", "fold_state",
                   "implicit_arm", "is_frozen", "take_lanes")

    monitoring_states = _MONITORING_STATES

    def __init__(self, name="Track", has_midi_input=True, slot_count=8,
                 send_count=0, canonical_parent=None, kind="midi"):
        LomObject.__init__(self, canonical_parent)
        if kind not in _TRACK_KINDS:
            raise ValueError("unknown track kind %r" % (kind,))
        self._kind = kind
        self._name = str(name)
        self._color_index = 0
        self._color = 0
        self._mute = False
        self._solo = False
        self._arm = False
        self._implicit_arm = False
        self._is_midi = (kind == "midi") and bool(has_midi_input)
        self._is_group = False
        self._fold_state = 0
        self._group_track = None
        self._is_frozen = False
        self._is_visible = True
        self._is_showing_chains = False
        self._back_to_arranger = False
        self._playing_slot_index = -1
        self._fired_slot_index = -1
        self._current_monitoring_state = int(_MONITORING_STATES.AUTO)
        self._view = TrackView(self)
        self._mixer_device = MixerDevice(self, send_count, is_master=(kind == "master"))
        self._init_devices()
        self._clip_slots = [ClipSlot(self) for _ in range(slot_count)] \
            if kind in ("midi", "audio") else []
        self._arrangement_clips = []
        self._take_lanes = []
        self._data = {}
        if self._is_midi:
            self._available_input_routing_types = (
                RoutingType("All Ins", RoutingTypeCategory.external),
                RoutingType("Computer Keyboard", RoutingTypeCategory.external),
                RoutingType("No Input", RoutingTypeCategory.none))
            self._available_input_routing_channels = tuple(
                [RoutingChannel("All Channels", RoutingChannelLayout.midi)] +
                [RoutingChannel("Ch. %d" % i, RoutingChannelLayout.midi)
                 for i in range(1, 17)])
        else:
            self._available_input_routing_types = (
                RoutingType("Ext. In", RoutingTypeCategory.external),
                RoutingType("Resampling", RoutingTypeCategory.resampling),
                RoutingType("No Input", RoutingTypeCategory.none))
            self._available_input_routing_channels = (
                RoutingChannel("1/2", RoutingChannelLayout.stereo),
                RoutingChannel("1", RoutingChannelLayout.mono),
                RoutingChannel("2", RoutingChannelLayout.mono))
        self._available_output_routing_types = (
            RoutingType("Master", RoutingTypeCategory.master),
            RoutingType("Sends Only", RoutingTypeCategory.none))
        self._available_output_routing_channels = (
            RoutingChannel("Track In", RoutingChannelLayout.stereo),)
        self._input_routing_type = self._available_input_routing_types[0]
        self._input_routing_channel = self._available_input_routing_channels[0]
        self._output_routing_type = self._available_output_routing_types[0]
        self._output_routing_channel = self._available_output_routing_channels[0]

    # -- identity / state ------------------------------------------------------------
    @property
    def name(self):
        """Live 12.4.5 prefixes return-track names with their letter: a return named
        ``"Reverb"`` reads ``"A-Reverb"`` (``name = "X"`` on the third return reads
        ``"C-X"``); the letter follows the return's position."""
        if self._kind == "return":
            song = self._canonical_parent
            returns = getattr(song, "_return_tracks", None) or []
            if self in returns:
                return "%s-%s" % (chr(ord("A") + returns.index(self)), self._name)
        return self._name

    @name.setter
    def name(self, value):
        self._name = _as_str(value, "Track.name")
        self.notify_listeners("name")
    color = _rw("color", _as_int)
    color_index = _rw("color_index", _as_int, _range_check(0, 69, "color_index"))
    view = _ro("view")
    mixer_device = _ro("mixer_device")
    implicit_arm = _rw("implicit_arm", _as_bool)
    is_showing_chains = _rw("is_showing_chains", _as_bool)
    back_to_arranger = _rw("back_to_arranger", _as_bool)
    is_frozen = _ro("is_frozen")
    is_visible = _ro("is_visible")
    group_track = _ro("group_track")
    playing_slot_index = _ro("playing_slot_index")
    fired_slot_index = _ro("fired_slot_index")
    is_part_of_selection = property(lambda self: self is getattr(
        getattr(self._canonical_parent, "_view", None), "_selected_track", None))
    muted_via_solo = property(lambda self: False)
    can_be_frozen = property(lambda self: self._kind in ("midi", "audio"))
    can_show_chains = property(lambda self: any(
        isinstance(d, RackDevice) and d.type == int(DeviceType.instrument)
        for d in self._devices))
    performance_impact = property(lambda self: 0.0)
    input_meter_level = property(lambda self: 0.0)
    input_meter_left = property(lambda self: 0.0)
    input_meter_right = property(lambda self: 0.0)
    output_meter_level = property(lambda self: 0.0)
    output_meter_left = property(lambda self: 0.0)
    output_meter_right = property(lambda self: 0.0)

    @property
    def mute(self):
        if self._kind == "master":
            raise RuntimeError("Main track has no 'mute' property!")
        return self._mute

    @mute.setter
    def mute(self, value):
        if self._kind == "master":
            raise RuntimeError("Main track has no 'mute' property!")
        self._mute = _as_bool(value, "Track.mute")
        self.notify_listeners("mute")

    @property
    def solo(self):
        if self._kind == "master":
            raise RuntimeError("Main track has no 'solo' property!")
        return self._solo

    @solo.setter
    def solo(self, value):
        if self._kind == "master":
            raise RuntimeError("Main track has no 'solo' property!")
        self._solo = _as_bool(value, "Track.solo")
        self.notify_listeners("solo")

    @property
    def can_be_armed(self):
        return self._kind in ("midi", "audio") and not self._is_group

    @property
    def arm(self):
        if not self.can_be_armed:
            raise RuntimeError("Main and Return Tracks have no 'Arm' state!")
        return self._arm

    @arm.setter
    def arm(self, value):
        if not self.can_be_armed:
            raise RuntimeError("Main and Return Tracks have no 'Arm' state!")
        # Live 12.4.5: arming through the API never disarms the other tracks, whatever the
        # "Exclusive Arm" preference (song.exclusive_arm) says — it only governs UI clicks.
        self._arm = _as_bool(value, "Track.arm")
        self.notify_listeners("arm")

    @property
    def is_foldable(self):
        return self._is_group

    @property
    def is_grouped(self):
        return self._group_track is not None

    @property
    def fold_state(self):
        if not self._is_group:
            raise RuntimeError("This Track can not be collapsed")
        return self._fold_state

    @fold_state.setter
    def fold_state(self, value):
        if not self._is_group:
            raise RuntimeError("This Track can not be collapsed")
        self._fold_state = 1 if _as_int(value, "Track.fold_state") else 0
        self.notify_listeners("fold_state")

    @property
    def has_midi_input(self):
        return self._is_midi

    @property
    def has_audio_input(self):
        return self._kind in ("audio", "return", "master")

    @property
    def has_midi_output(self):
        return self._is_midi and not any(
            d.type in (int(DeviceType.instrument), int(DeviceType.audio_effect))
            for d in self._devices)

    @property
    def has_audio_output(self):
        return not self.has_midi_output

    @property
    def current_monitoring_state(self):
        if self._kind not in ("midi", "audio"):
            raise RuntimeError("Main and Return Tracks have no monitoring state!")
        return self._current_monitoring_state

    @current_monitoring_state.setter
    def current_monitoring_state(self, value):
        if self._kind not in ("midi", "audio"):
            raise RuntimeError("Main and Return Tracks have no monitoring state!")
        value = _as_int(value, "Track.current_monitoring_state")
        if value not in (0, 1, 2):
            raise ValueError("current_monitoring_state must be 0 (In), 1 (Auto) or 2 (Off)")
        self._current_monitoring_state = value
        self.notify_listeners("current_monitoring_state")

    # -- collections --------------------------------------------------------------
    @property
    def clip_slots(self):
        return Vector(self._clip_slots)

    @property
    def arrangement_clips(self):
        if self._kind not in ("midi", "audio") or self._is_group:
            raise RuntimeError("Main, Group and Return Tracks have no arrangement clips")
        return Vector(self._arrangement_clips)

    @property
    def take_lanes(self):
        return Vector(self._take_lanes)

    # -- routing -----------------------------------------------------------------------
    def _need_input(self, what):
        if self._kind not in ("midi", "audio"):
            raise RuntimeError("%s is only available on MIDI and audio tracks" % what)

    def _need_output(self, what):
        if self._kind == "master":
            raise RuntimeError("%s is not available on the main track" % what)

    @property
    def available_input_routing_types(self):
        self._need_input("available_input_routing_types")
        return self._available_input_routing_types

    @property
    def available_input_routing_channels(self):
        self._need_input("available_input_routing_channels")
        return self._available_input_routing_channels

    @property
    def available_output_routing_types(self):
        self._need_output("available_output_routing_types")
        return self._available_output_routing_types

    @property
    def available_output_routing_channels(self):
        self._need_output("available_output_routing_channels")
        return self._available_output_routing_channels

    def _routing_setter(name, pool, need):
        def fget(self):
            getattr(self, need)(name)
            return getattr(self, "_" + name)

        def fset(self, value):
            getattr(self, need)(name)
            if value not in getattr(self, "_" + pool):
                raise ValueError("%s: pick one of %s" % (name, pool))
            setattr(self, "_" + name, value)
            self.notify_listeners(name)
        return property(fget, fset)

    input_routing_type = _routing_setter("input_routing_type",
                                         "available_input_routing_types", "_need_input")
    input_routing_channel = _routing_setter("input_routing_channel",
                                            "available_input_routing_channels",
                                            "_need_input")
    output_routing_type = _routing_setter("output_routing_type",
                                          "available_output_routing_types",
                                          "_need_output")
    output_routing_channel = _routing_setter("output_routing_channel",
                                             "available_output_routing_channels",
                                             "_need_output")
    del _routing_setter

    # -- arrangement ------------------------------------------------------------------
    def _check_arrangement(self, time, what):
        if self._is_frozen:
            raise RuntimeError("%s: the track is frozen" % what)
        if not (0.0 <= time <= MAX_ARRANGEMENT_TIME):
            raise RuntimeError("%s: time %r outside [0, %s]" % (what, time,
                                                               MAX_ARRANGEMENT_TIME))

    def _new_arrangement_midi_clip(self, start_time, length, owner=None):
        start_time = _as_float(start_time, "create_midi_clip")
        length = _as_float(length, "create_midi_clip")
        if not self._is_midi:
            raise RuntimeError("create_midi_clip: not a MIDI track")
        self._check_arrangement(start_time, "create_midi_clip")
        if length <= 0.0:
            raise RuntimeError("create_midi_clip: length must be greater than 0")
        clip = Clip("", length, True, owner or self, arrangement=True)
        clip._start_time = start_time
        return clip

    def _new_arrangement_audio_clip(self, file_path, position, owner=None):
        path = _check_audio_path(file_path, "create_audio_clip")
        position = _as_float(position, "create_audio_clip")
        if self._kind != "audio":
            raise RuntimeError("create_audio_clip: not an audio track")
        self._check_arrangement(position, "create_audio_clip")
        clip = Clip(os.path.splitext(os.path.basename(path.replace("\\", "/")))[0],
                    8.0, False, owner or self, path, arrangement=True)
        clip._start_time = position
        return clip

    def _add_arrangement_clip(self, clip):
        self._arrangement_clips.append(clip)
        self._arrangement_clips.sort(key=lambda c: c._start_time)
        self.notify_listeners("arrangement_clips")
        return clip

    def create_midi_clip(self, start_time, length, /):
        """Live 12.0+: empty MIDI clip in the arrangement; returns it."""
        return self._add_arrangement_clip(self._new_arrangement_midi_clip(start_time,
                                                                          length))

    def create_audio_clip(self, file_path, position, /):
        """Audio clip referencing ``file_path`` (absolute) at ``position`` beats."""
        return self._add_arrangement_clip(self._new_arrangement_audio_clip(file_path,
                                                                           position))

    def duplicate_clip_to_arrangement(self, clip, destination_time):
        if not isinstance(clip, Clip):
            raise _boost_type_error("Track.duplicate_clip_to_arrangement", clip)
        if clip._is_midi != self._is_midi or self._kind not in ("midi", "audio"):
            raise RuntimeError("duplicate_clip_to_arrangement: clip and track types "
                               "are incompatible")
        destination_time = _as_float(destination_time)
        self._check_arrangement(destination_time, "duplicate_clip_to_arrangement")
        clone = _clone_clip(clip, self, arrangement=True)
        clone._start_time = destination_time
        clone._arrangement_length = clip.length
        return self._add_arrangement_clip(clone)

    def delete_clip(self, clip, /):
        if clip in self._arrangement_clips:
            self._arrangement_clips.remove(clip)
            _mark_deleted(clip)
            self.notify_listeners("arrangement_clips")
            return
        for lane in self._take_lanes:
            if clip in lane._arrangement_clips:
                lane._arrangement_clips.remove(clip)
                return
        for slot in self._clip_slots:
            if slot._clip is clip:
                slot.delete_clip()
                return
        raise RuntimeError("delete_clip: the clip belongs to another track")

    def duplicate_clip_slot(self, index, /):
        """Live 12.4.5: copies into slot ``index + 1`` and OVERWRITES a clip there (it does
        not look for a free slot); from the last scene a scene is added first."""
        index = _as_int(index, "duplicate_clip_slot")
        source = self._clip_slots[index]
        if source._clip is None:
            raise RuntimeError("duplicate_clip_slot: the slot is empty")
        if index + 1 >= len(self._clip_slots):
            self._canonical_parent.create_scene(-1)
        target = self._clip_slots[index + 1]
        if target._clip is not None:
            _mark_deleted(target._clip)
        target._set_clip(_clone_clip(source._clip, target))
        return index + 1

    def create_take_lane(self):
        lane = TakeLane(self, "Take %d" % (len(self._take_lanes) + 1))
        self._take_lanes.append(lane)
        self.notify_listeners("take_lanes")
        return lane

    # -- transport -------------------------------------------------------------------------
    def stop_all_clips(self, Quantized=True):
        self._playing_slot_index = -1
        self._fired_slot_index = -1
        for slot in self._clip_slots:
            if slot._clip is not None:
                slot._clip._is_playing = False
                slot._clip._is_recording = False
        self.notify_listeners("playing_slot_index")

    def jump_in_running_session_clip(self, beats, /):
        if 0 <= self._playing_slot_index < len(self._clip_slots):
            clip = self._clip_slots[self._playing_slot_index]._clip
            if clip is not None:
                clip.move_playing_pos(beats)

    # -- persistence ------------------------------------------------------------------------
    def get_data(self, key, default_value):
        return self._data.get(key, default_value)

    def set_data(self, key, value):
        self._data[key] = value

    # -- stub-only helpers ------------------------------------------------------------------
    def _make_group(self, members):
        """Stub-only: turn this (audio-kind) track into a group of ``members``."""
        self._is_group = True
        self._clip_slots = [ClipSlot(self) for _ in self._clip_slots]
        for member in members:
            member._group_track = self


# ==========================================================================
# Scene / CuePoint / Groove
# ==========================================================================

class Scene(LomObject):
    """``Live.Scene.Scene``.

    ``tempo`` returns -1 while ``tempo_enabled`` is False; the time signature
    returns -1/-1 while disabled.  ``time_signature_enabled`` is writable in 12.4.5:
    True fills in the song's signature, False sets -1/-1.
    ``fire(force_legato=False, can_select_scene_on_launch=True)`` applies the scene's
    tempo *and* time signature to the song.
    """

    _LISTENABLE = ("name", "is_triggered", "color", "color_index", "tempo",
                   "tempo_enabled", "time_signature_numerator",
                   "time_signature_denominator")

    def __init__(self, name="", canonical_parent=None):
        LomObject.__init__(self, canonical_parent)
        self._name = str(name)
        self._color_index = 0
        self._color = 0
        self._is_triggered = False
        self._tempo_value = 120.0
        self._tempo_enabled = False
        self._numerator = -1
        self._denominator = -1

    name = _rw("name", _as_str)
    color = _rw("color", _as_int)
    color_index = _rw("color_index", _as_int, _range_check(0, 69, "color_index"))
    is_triggered = _ro("is_triggered")
    tempo_enabled = _rw("tempo_enabled", _as_bool)

    @property
    def tempo(self):
        return self._tempo_value if self._tempo_enabled else -1.0

    @tempo.setter
    def tempo(self, value):
        value = _as_float(value, "Scene.tempo")
        if not (20.0 <= value <= 999.0):
            raise ValueError("Scene.tempo must be within 20..999")
        self._tempo_value = value
        self.notify_listeners("tempo")

    @property
    def time_signature_enabled(self):
        return self._numerator != -1 and self._denominator != -1

    @time_signature_enabled.setter
    def time_signature_enabled(self, value):
        if _as_bool(value, "Scene.time_signature_enabled"):
            song = self._canonical_parent
            if self._numerator == -1:
                self._numerator = song._signature_numerator if song is not None else 4
            if self._denominator == -1:
                self._denominator = song._signature_denominator if song is not None else 4
        else:
            self._numerator = self._denominator = -1

    @property
    def time_signature_numerator(self):
        return self._numerator if self.time_signature_enabled else -1

    @time_signature_numerator.setter
    def time_signature_numerator(self, value):
        value = _as_int(value, "Scene.time_signature_numerator")
        if value != -1 and not (1 <= value <= 99):
            raise ValueError("time_signature_numerator must be 1..99 (or -1)")
        self._numerator = value
        if value != -1 and self._denominator == -1:
            self._denominator = 4

    @property
    def time_signature_denominator(self):
        return self._denominator if self.time_signature_enabled else -1

    @time_signature_denominator.setter
    def time_signature_denominator(self, value):
        value = _as_int(value, "Scene.time_signature_denominator")
        if value != -1:
            _check_denominator(value)
        self._denominator = value
        if value != -1 and self._numerator == -1:
            self._numerator = 4

    @property
    def clip_slots(self):
        song = self._canonical_parent
        if song is None:
            return Vector()
        index = song._scenes.index(self)
        return Vector(track._clip_slots[index] for track in song._tracks
                     if index < len(track._clip_slots))

    @property
    def is_empty(self):
        return not any(slot.has_clip for slot in self.clip_slots)

    def fire(self, force_legato=False, can_select_scene_on_launch=True):
        for slot in self.clip_slots:
            if slot._clip is not None or slot._has_stop_button:
                slot.fire()
        self._is_triggered = False
        song = self._canonical_parent
        if song is not None and can_select_scene_on_launch:
            song._view._selected_scene = self
        if song is not None and self._tempo_enabled:
            song._tempo = self._tempo_value
        if song is not None and self.time_signature_enabled:
            song._signature_numerator = self._numerator
            song._signature_denominator = self._denominator

    def _copy_settings(self, source):
        """Stub-internal: take over tempo and time-signature settings (create_scene)."""
        self._tempo_value = source._tempo_value
        self._tempo_enabled = source._tempo_enabled
        self._numerator = source._numerator
        self._denominator = source._denominator

    def fire_as_selected(self, force_legato=False):
        song = self._canonical_parent
        selected = song._view._selected_scene if song is not None else None
        target = selected or self
        target.fire(force_legato)
        if song is not None and target in song._scenes:
            index = song._scenes.index(target)
            if index + 1 < len(song._scenes):
                song._view._selected_scene = song._scenes[index + 1]

    def set_fire_button_state(self, state, /):
        self._is_triggered = _as_bool(state)


class CuePoint(LomObject):
    """``Live.Song.CuePoint`` — ``name`` is writable, ``time`` is **read-only**
    (to move a locator delete it and create a new one with
    ``song.set_or_delete_cue()`` at the new position)."""

    _LISTENABLE = ("name", "time")

    def __init__(self, name="", time=0.0, canonical_parent=None):
        LomObject.__init__(self, canonical_parent)
        self._name = str(name)
        self._time = float(time)

    name = _rw("name", _as_str)
    time = _ro("time")

    def jump(self):
        """Exact: puts playhead and insert marker on the cue (stopped: also the start marker)."""
        song = self._canonical_parent
        if song is not None:
            song._jump_to(self._time)


class Groove(LomObject):
    """``Live.Groove.Groove``."""

    def __init__(self, name="Groove", canonical_parent=None):
        LomObject.__init__(self, canonical_parent)
        self._name = name
        self._base = int(GrooveBase.gb_sixteen)
        self._quantization_amount = 0.0
        self._random_amount = 0.0
        self._timing_amount = 100.0     # percent, as Live 12.4.5 reports it (g2 fixer)
        self._velocity_amount = 0.0

    name = _rw("name", _as_str)
    base = _rw("base", _as_int)
    quantization_amount = _rw("quantization_amount", _as_float)
    random_amount = _rw("random_amount", _as_float)
    timing_amount = _rw("timing_amount", _as_float)
    velocity_amount = _rw("velocity_amount", _as_float)


class GroovePool(LomObject):
    """``Live.GroovePool.GroovePool`` — only ``grooves``."""

    def __init__(self, song):
        LomObject.__init__(self, song)
        self._grooves = []

    @property
    def grooves(self):
        return Vector(self._grooves)


class BeatTime(object):
    """``Live.Song.BeatTime`` (bars, beats, sub_division, ticks)."""

    def __init__(self, bars, beats, sub_division, ticks):
        self.bars, self.beats = bars, beats
        self.sub_division, self.ticks = sub_division, ticks


class SmptTime(object):
    """``Live.Song.SmptTime`` (hours, minutes, seconds, frames)."""

    def __init__(self, hours, minutes, seconds, frames):
        self.hours, self.minutes, self.seconds, self.frames = hours, minutes, seconds, frames


# ==========================================================================
# Song
# ==========================================================================

#: A subset of Live 12's scale list (names + intervals) — UNVERIFIED as complete.
_SCALES = (
    ("Major", (0, 2, 4, 5, 7, 9, 11)), ("Minor", (0, 2, 3, 5, 7, 8, 10)),
    ("Dorian", (0, 2, 3, 5, 7, 9, 10)), ("Mixolydian", (0, 2, 4, 5, 7, 9, 10)),
    ("Lydian", (0, 2, 4, 6, 7, 9, 11)), ("Phrygian", (0, 1, 3, 5, 7, 8, 10)),
    ("Locrian", (0, 1, 3, 5, 6, 8, 10)), ("Whole Tone", (0, 2, 4, 6, 8, 10)),
    ("Half-whole Dim.", (0, 1, 3, 4, 6, 7, 9, 10)),
    ("Whole-half Dim.", (0, 2, 3, 5, 6, 8, 9, 11)),
    ("Minor Blues", (0, 3, 5, 6, 7, 10)), ("Minor Pentatonic", (0, 3, 5, 7, 10)),
    ("Major Pentatonic", (0, 2, 4, 7, 9)), ("Harmonic Minor", (0, 2, 3, 5, 7, 8, 11)),
    ("Harmonic Major", (0, 2, 4, 5, 7, 8, 11)), ("Melodic Minor", (0, 2, 3, 5, 7, 9, 11)),
    ("Chromatic", tuple(range(12))),
)


def get_all_scales_ordered():
    """``Live.Song.get_all_scales_ordered()`` -> tuple of (name, intervals)."""
    return tuple(_SCALES)


class SongView(LomObject):
    """``Live.Song.Song.View``.

    ``select_device(device, ShouldAppointDevice=True)`` is the way to select a
    device (there is no ``selected_device`` here — read
    ``song.view.selected_track.view.selected_device``).  ``selected_parameter``
    is read-only.
    """

    _LISTENABLE = ("selected_track", "selected_scene", "detail_clip",
                   "selected_chain", "selected_parameter", "draw_mode", "follow_song")

    def __init__(self, song):
        LomObject.__init__(self, song)
        self._selected_track = None
        self._selected_scene = None
        self._highlighted_clip_slot = None
        self._detail_clip = None
        self._selected_chain = None
        self._selected_parameter = None
        self._follow_song = False
        self._draw_mode = False

    selected_track = _rw("selected_track")
    selected_scene = _rw("selected_scene")
    highlighted_clip_slot = _rw("highlighted_clip_slot")
    detail_clip = _rw("detail_clip")
    selected_chain = _rw("selected_chain")
    selected_parameter = _ro("selected_parameter")
    follow_song = _rw("follow_song", _as_bool)
    draw_mode = _rw("draw_mode", _as_bool)

    def select_device(self, device, /, ShouldAppointDevice=True):
        if not isinstance(device, Device):
            raise _boost_type_error("View.select_device", device)
        track = _track_of(device)
        if track is not None:
            track._view._selected_device = device
            track._view.notify_listeners("selected_device")
        song = self._canonical_parent
        if ShouldAppointDevice and song is not None and track is self._selected_track:
            song._appointed_device = device


class Song(LomObject):
    """``Live.Song.Song`` — the Live Set."""

    _LISTENABLE = ("tempo", "is_playing", "tracks", "scenes", "current_song_time",
                   "metronome", "record_mode", "session_record", "cue_points",
                   "return_tracks", "visible_tracks", "loop", "loop_start",
                   "loop_length", "signature_numerator", "signature_denominator",
                   "arrangement_overdub", "overdub", "punch_in", "punch_out",
                   "clip_trigger_quantization", "midi_recording_quantization",
                   "session_record_status", "scale_name", "root_note", "scale_mode",
                   "back_to_arranger", "re_enable_automation_enabled",
                   "appointed_device", "is_counting_in", "song_length")

    def __init__(self):
        LomObject.__init__(self, None)
        self._tempo = 120.0
        self._signature_numerator = 4
        self._signature_denominator = 4
        self._is_playing = False
        self._current_song_time = 0.0
        self._start_time = 0.0
        self._loop = False
        self._loop_start = 0.0
        self._loop_length = 16.0
        self._metronome = False
        self._record_mode = False
        self._session_record = False
        self._session_record_status = int(SessionRecordStatus.off)
        self._session_automation_record = False
        self._arrangement_overdub = False
        self._punch_in = False
        self._punch_out = False
        self._nudge_down = False
        self._nudge_up = False
        self._clip_trigger_quantization = int(Quantization.q_bar)
        self._midi_recording_quantization = int(RecordingQuantization.rec_q_no_q)
        self._count_in_duration = 0
        self._groove_amount = 1.0
        self._swing_amount = 0.0
        self._exclusive_arm = True
        self._exclusive_solo = True
        self._select_on_launch = True
        self._back_to_arranger = False
        self._re_enable_automation_enabled = False
        self._scale_name = "Major"
        self._root_note = 0
        self._scale_mode = False
        self._tempo_follower_enabled = False
        self._is_ableton_link_enabled = False
        self._is_ableton_link_start_stop_sync_enabled = False
        self._is_counting_in = False
        self._appointed_device = None
        self._name = ""
        self._file_path = ""
        self._view = SongView(self)
        self._groove_pool = GroovePool(self)
        self._tuning_system = None
        self._master_track = Track("Main", False, 0, 0, self, kind="master")
        self._tracks = []
        self._return_tracks = []
        self._scenes = []
        self._cue_points = []
        self._data = {}
        # Live's Arrangement insert marker (stopped transport) and the grid it snaps to —
        # derived from the Arrangement zoom (app.view.zoom_view), stub default 1 beat.
        self._insert_marker = None          # None = on current_song_time
        self._arrangement_grid = 1.0
        # undo bookkeeping (stub-only introspection helpers)
        self._undo_depth = 0
        self._undo_steps = []
        self._undo_stack = []
        self._redo_stack = []

    # -- properties -----------------------------------------------------------------
    tempo = _rw("tempo", _as_float, _range_check(20.0, 999.0, "tempo"))
    signature_numerator = _rw("signature_numerator", _as_int,
                              _range_check(1, 99, "signature_numerator"))
    signature_denominator = _rw("signature_denominator", _as_int,
                                lambda self, v: _check_denominator(v))
    loop = _rw("loop", _as_bool)
    metronome = _rw("metronome", _as_bool)
    session_record = _rw("session_record", _as_bool)
    session_automation_record = _rw("session_automation_record", _as_bool)
    arrangement_overdub = _rw("arrangement_overdub", _as_bool)
    punch_in = _rw("punch_in", _as_bool)
    punch_out = _rw("punch_out", _as_bool)
    nudge_down = _rw("nudge_down", _as_bool)
    nudge_up = _rw("nudge_up", _as_bool)
    clip_trigger_quantization = _rw("clip_trigger_quantization", _as_int,
                                    _range_check(0, 13, "clip_trigger_quantization"))
    midi_recording_quantization = _rw("midi_recording_quantization", _as_int,
                                      _range_check(0, 8, "midi_recording_quantization"))
    swing_amount = _rw("swing_amount", _as_float, _range_check(0.0, 1.0, "swing_amount"))
    back_to_arranger = _rw("back_to_arranger", _as_bool)
    root_note = _rw("root_note", _as_int, _range_check(0, 11, "root_note"))
    scale_mode = _rw("scale_mode", _as_bool)
    tempo_follower_enabled = _rw("tempo_follower_enabled", _as_bool)
    is_ableton_link_enabled = _rw("is_ableton_link_enabled", _as_bool)
    is_ableton_link_start_stop_sync_enabled = _rw(
        "is_ableton_link_start_stop_sync_enabled", _as_bool)
    appointed_device = _rw("appointed_device")
    count_in_duration = _ro("count_in_duration", "0 none, 1 = 1 bar, 2 = 2 bars, 3 = 4 bars")
    session_record_status = _ro("session_record_status")
    exclusive_arm = _ro("exclusive_arm")
    exclusive_solo = _ro("exclusive_solo")
    select_on_launch = _ro("select_on_launch")
    re_enable_automation_enabled = _ro("re_enable_automation_enabled")
    is_counting_in = _ro("is_counting_in")
    name = _ro("name")
    file_path = _ro("file_path")
    view = _ro("view")
    groove_pool = _ro("groove_pool")
    tuning_system = _ro("tuning_system")
    master_track = _ro("master_track")

    #: Live pads ``song_length`` by 32 beats behind the arrangement material or the loop
    #: brace, whichever ends later (measured on 12.4.5: material to 200 -> 232).
    SONG_LENGTH_PADDING = 32.0
    #: Largest ``groove_amount`` Live accepts (131.25 %); larger values clamp to it.
    GROOVE_MAX = 1.3125

    @property
    def is_playing(self):
        return self._is_playing

    @is_playing.setter
    def is_playing(self, value):
        if _as_bool(value, "Song.is_playing"):
            self.start_playing()
        else:
            self.stop_playing()

    @property
    def current_song_time(self):
        return self._current_song_time

    @current_song_time.setter
    def current_song_time(self, value):
        """Live 12.4.5: raises behind ``song_length``; while stopped the insert marker
        snaps to the Arrangement grid (the reported time stays exact)."""
        value = _as_float(value, "Song.current_song_time")
        if value < 0.0:
            raise ValueError("current_song_time must be >= 0")
        if value > self.song_length + 1e-9:
            raise RuntimeError("Cannot set the Songtime behind the Songlength")
        self._move(value, None if self._is_playing else self._snap(value))

    @property
    def start_time(self):
        return self._start_time

    @start_time.setter
    def start_time(self, value):
        value = _as_float(value, "Song.start_time")
        if value > self.song_length + 1e-9:
            raise RuntimeError("Cannot set the start time after the song length")
        self._start_time = value

    @property
    def loop_start(self):
        return self._loop_start

    @loop_start.setter
    def loop_start(self, value):
        value = _as_float(value, "Song.loop_start")
        if value < 0.0:
            raise ValueError("loop_start must be >= 0")
        if value + self._loop_length > self.song_length + 1e-9:
            raise RuntimeError("Cannot set the Loopstart behind the Songlength")
        self._loop_start = value
        self.notify_listeners("loop_start")

    @property
    def loop_length(self):
        return self._loop_length

    @loop_length.setter
    def loop_length(self, value):
        """Below one beat is silently raised to 1.0 (Live 12.4.5)."""
        value = max(1.0, _as_float(value, "Song.loop_length"))
        if self._loop_start + value > self.song_length + 1e-9:
            raise RuntimeError("Cannot set the Loop behind the song length")
        self._loop_length = value
        self.notify_listeners("loop_length")

    @property
    def groove_amount(self):
        return self._groove_amount

    @groove_amount.setter
    def groove_amount(self, value):
        """0..1.3125: larger values clamp, negative values raise (Live 12.4.5)."""
        value = _as_float(value, "Song.groove_amount")
        if value < 0.0:
            raise RuntimeError("Groove Amount out of range")
        self._groove_amount = min(self.GROOVE_MAX, value)

    @property
    def record_mode(self):
        return self._record_mode

    @record_mode.setter
    def record_mode(self, value):
        """``True`` also starts playback while stopped — Live's default "Start Playback
        with Record" preference (measured on 12.4.5)."""
        self._record_mode = _as_bool(value, "Song.record_mode")
        self.notify_listeners("record_mode")
        if self._record_mode and not self._is_playing:
            self._transport("start")

    # -- stub-internal transport model (tests/live_stub_ext/transport_live.py defers it) --
    def _marker(self):
        """Where Live's insert marker is (``current_song_time`` unless snapped)."""
        return self._current_song_time if self._insert_marker is None else self._insert_marker

    def _snap(self, value):
        grid = self._arrangement_grid
        return round(value / grid) * grid

    def _move(self, reported, marker=None, start=None):
        """Playhead move request (hook: transport_live defers it to Live's next tick)."""
        self._apply_move(reported, marker, start)

    def _apply_move(self, reported, marker=None, start=None):
        """Set the reported playhead, the insert marker and (optionally) the start marker."""
        self._current_song_time = reported
        self._insert_marker = None if marker is None or marker == reported else marker
        if start is not None:
            self._start_time = start
        self.notify_listeners("current_song_time")

    def _jump_to(self, time):
        """Exact jump (cues): playhead and marker; while stopped also the start marker."""
        self._move(time, time, None if self._is_playing else time)

    def _transport(self, kind):
        """Transport request (hook: transport_live defers it to Live's next tick)."""
        self._apply_transport(kind)

    def _apply_transport(self, kind):
        """``"start"`` / ``"stop"`` / ``"continue"`` — applied at once."""
        if kind == "stop":
            if not self._is_playing:
                # stop while stopped: Live returns playhead and start marker to the start
                self._start_time = 0.0
                self._apply_move(0.0)
            self._is_playing = False
        else:
            if kind == "start":
                self._apply_move(self._start_time)
            self._is_playing = True
        self.notify_listeners("is_playing")

    @property
    def overdub(self):
        return self._arrangement_overdub

    @overdub.setter
    def overdub(self, value):
        self.arrangement_overdub = value

    @property
    def scale_name(self):
        return self._scale_name

    @scale_name.setter
    def scale_name(self, value):
        self._scale_name = _as_str(value, "Song.scale_name")
        self.notify_listeners("scale_name")

    @property
    def scale_intervals(self):
        for name, intervals in _SCALES:
            if name == self._scale_name:
                return intervals
        return (0, 2, 4, 5, 7, 9, 11)

    @property
    def can_undo(self):
        return bool(self._undo_stack)

    @property
    def can_redo(self):
        return bool(self._redo_stack)

    @property
    def can_capture_midi(self):
        return False

    @property
    def can_jump_to_next_cue(self):
        return any(c._time > self._current_song_time for c in self._cue_points)

    @property
    def can_jump_to_prev_cue(self):
        return any(c._time < self._current_song_time for c in self._cue_points)

    @property
    def last_event_time(self):
        ends = [c.end_time for t in self._tracks for c in t._arrangement_clips]
        ends += [c._time for c in self._cue_points]
        return max(ends) if ends else 0.0

    @property
    def song_length(self):
        loop_end = self._loop_start + self._loop_length
        return max(self.last_event_time, loop_end) + self.SONG_LENGTH_PADDING

    # -- collections --------------------------------------------------------------------
    @property
    def tracks(self):
        return Vector(self._tracks)

    @property
    def visible_tracks(self):
        return Vector(t for t in self._tracks if t._is_visible)

    @property
    def return_tracks(self):
        return Vector(self._return_tracks)

    @property
    def scenes(self):
        return Vector(self._scenes)

    @property
    def cue_points(self):
        return Vector(self._cue_points)

    # -- transport ------------------------------------------------------------------------
    def start_playing(self):
        self._transport("start")

    def stop_playing(self):
        self._transport("stop")

    def continue_playing(self):
        self._transport("continue")

    def stop_all_clips(self, Quantized=True):
        for track in self._tracks:
            track.stop_all_clips(Quantized)

    def play_selection(self):
        self._transport("continue")

    def jump_by(self, beats, /):
        """Only the reported time moves; Live's insert marker stays (measured)."""
        self._move(max(0.0, self._current_song_time + _as_float(beats)), self._marker())

    def scrub_by(self, beats, /):
        self.jump_by(beats)

    def jump_to_next_cue(self):
        later = [c._time for c in self._cue_points if c._time > self._current_song_time]
        if later:
            self._jump_to(min(later))

    def jump_to_prev_cue(self):
        earlier = [c._time for c in self._cue_points if c._time < self._current_song_time]
        if earlier:
            self._jump_to(max(earlier))

    def _cue_position(self):
        """Stopped: the insert marker; playing: the playhead."""
        return self._current_song_time if self._is_playing else self._marker()

    def is_cue_point_selected(self):
        position = self._cue_position()
        return any(abs(c._time - position) < 1e-6 for c in self._cue_points)

    def set_or_delete_cue(self):
        """Toggle a cue at the insert marker (stopped) / playhead; returns None (like Live).

        Live 12.4.5: ``cue_points`` is in *creation order* (not sorted by time) and a new
        cue is named by its count ("1", "2", ...).
        """
        position = self._cue_position()
        existing = [c for c in self._cue_points if abs(c._time - position) < 1e-6]
        if existing:
            self._cue_points.remove(existing[0])
            _mark_deleted(existing[0])
        else:
            cue = CuePoint("", position, self)
            self._cue_points.append(cue)
            cue._name = str(len(self._cue_points))
        self.notify_listeners("cue_points")

    def tap_tempo(self):
        self._tempo = min(999.0, self._tempo + 1.0)
        self.notify_listeners("tempo")

    def capture_midi(self, Destination=0):
        _as_int(Destination, "Song.capture_midi")
        self._captured_midi = int(Destination)

    def capture_and_insert_scene(self, CaptureMode=0):
        """Insert after the selected scene a scene copying its name, tempo and signature
        (also when nothing plays — Live 12.4.5)."""
        selected = self._view._selected_scene
        index = self._scenes.index(selected) + 1 if selected in self._scenes \
            else len(self._scenes)
        scene = self._new_scene(index)
        if selected in self._scenes:
            scene._name = selected._name
            scene._copy_settings(selected)

    def re_enable_automation(self):
        self._re_enable_automation_enabled = False

    def trigger_session_record(self, record_length=None):
        self._session_record = True

    def force_link_beat_time(self):
        pass

    def get_current_beats_song_time(self):
        beats_per_bar = self._signature_numerator
        total = self._current_song_time
        return BeatTime(int(total // beats_per_bar) + 1, int(total % beats_per_bar) + 1, 1, 0)

    def get_beats_loop_start(self):
        return BeatTime(int(self._loop_start // self._signature_numerator) + 1, 1, 1, 0)

    def get_beats_loop_length(self):
        return BeatTime(int(self._loop_length // self._signature_numerator), 0, 0, 0)

    def get_current_smpte_song_time(self, time_format, /):
        seconds = self._current_song_time * 60.0 / self._tempo
        return SmptTime(int(seconds // 3600), int(seconds % 3600 // 60), int(seconds % 60), 0)

    def get_data(self, key, default_value):
        return self._data.get(key, default_value)

    def set_data(self, key, value):
        self._data[key] = value

    # -- scenes -----------------------------------------------------------------------------
    def create_scene(self, index, /):
        """Create a scene at ``index`` (-1 = end); returns it.

        Live 12.4.5: the new scene copies tempo / time-signature settings of the scene
        before it (``scenes[index - 1]``) and becomes the selected scene.
        """
        scene = self._new_scene(index)
        position = self._scenes.index(scene)
        if position > 0:
            scene._copy_settings(self._scenes[position - 1])
        self._view._selected_scene = scene
        return scene

    def _new_scene(self, index):
        index = _as_int(index, "Song.create_scene")
        if index == -1:
            index = len(self._scenes)
        if not (0 <= index <= len(self._scenes)):
            raise RuntimeError("create_scene: invalid index %d" % index)
        scene = Scene("", self)
        self._scenes.insert(index, scene)
        for track in self._tracks:
            track._clip_slots.insert(index, ClipSlot(track))
        self.notify_listeners("scenes")
        return scene

    def delete_scene(self, index, /):
        index = _as_int(index, "Song.delete_scene")
        if not (0 <= index < len(self._scenes)):
            raise RuntimeError("delete_scene: no scene with index %d" % index)
        if len(self._scenes) == 1:
            raise RuntimeError("delete_scene: a set needs at least one scene")
        _mark_deleted(self._scenes.pop(index))
        for track in self._tracks:
            if index < len(track._clip_slots):
                del track._clip_slots[index]
        self.notify_listeners("scenes")

    def duplicate_scene(self, index, /):
        """Duplicate scene ``index`` (inserted after it and selected); returns None."""
        index = _as_int(index, "Song.duplicate_scene")
        if not (0 <= index < len(self._scenes)):
            raise RuntimeError("duplicate_scene: no scene with index %d" % index)
        source = self._scenes[index]
        scene = Scene(source._name, self)
        scene._color_index = source._color_index
        self._scenes.insert(index + 1, scene)
        for track in self._tracks:
            slot = ClipSlot(track)
            original = track._clip_slots[index]._clip if index < len(track._clip_slots) \
                else None
            if original is not None:
                slot._clip = _clone_clip(original, slot)
            track._clip_slots.insert(index + 1, slot)
        self._view._selected_scene = scene
        self.notify_listeners("scenes")

    # -- tracks -------------------------------------------------------------------------------
    def _insert_track(self, track, index):
        if index is None:
            selected = self._view._selected_track
            index = self._tracks.index(selected) + 1 if selected in self._tracks \
                else len(self._tracks)
        index = _as_int(index, "Song.create_track")
        if index == -1:
            index = len(self._tracks)
        if not (0 <= index <= len(self._tracks)):
            raise RuntimeError("create track: invalid index %d" % index)
        self._tracks.insert(index, track)
        self.notify_listeners("tracks")
        return track

    def create_midi_track(self, Index=None):
        """Create a MIDI track at ``Index`` (-1 = end, None = after the selection)."""
        track = Track("%d-MIDI" % (len(self._tracks) + 1), True, len(self._scenes),
                      len(self._return_tracks), self, kind="midi")
        return self._insert_track(track, Index)

    def create_audio_track(self, Index=None):
        """Create an audio track at ``Index`` (-1 = end, None = after the selection)."""
        track = Track("%d-Audio" % (len(self._tracks) + 1), False, len(self._scenes),
                      len(self._return_tracks), self, kind="audio")
        return self._insert_track(track, Index)

    def create_return_track(self):
        """Append a return track (and a send on every track and return)."""
        if len(self._return_tracks) >= 12:
            raise RuntimeError("create_return_track: maximum number of return tracks")
        track = Track("Return", False, 0, len(self._return_tracks), self, kind="return")
        self._return_tracks.append(track)
        for other in self._tracks + self._return_tracks[:-1]:
            other._mixer_device._sends.append(
                other._mixer_device._make_send(len(other._mixer_device._sends)))
        track._mixer_device._sends = [track._mixer_device._make_send(i)
                                      for i in range(len(self._return_tracks))]
        self.notify_listeners("return_tracks")
        return track

    def delete_track(self, index, /):
        index = _as_int(index, "Song.delete_track")
        if not (0 <= index < len(self._tracks)):
            raise RuntimeError("delete_track: no track with index %d" % index)
        _mark_deleted(self._tracks.pop(index))
        self.notify_listeners("tracks")

    def delete_return_track(self, index, /):
        index = _as_int(index, "Song.delete_return_track")
        if not (0 <= index < len(self._return_tracks)):
            raise RuntimeError("delete_return_track: no return track with index %d" % index)
        _mark_deleted(self._return_tracks.pop(index))
        for other in self._tracks + self._return_tracks:
            if index < len(other._mixer_device._sends):
                del other._mixer_device._sends[index]
        self.notify_listeners("return_tracks")

    def duplicate_track(self, index, /):
        """Duplicate track ``index`` (inserted after it and selected); returns None."""
        index = _as_int(index, "Song.duplicate_track")
        if not (0 <= index < len(self._tracks)):
            raise RuntimeError("duplicate_track: no track with index %d" % index)
        source = self._tracks[index]
        clone = Track(source._name, source._is_midi, len(self._scenes),
                      len(self._return_tracks), self, kind=source._kind)
        clone._color_index = source._color_index
        for device in source._devices:
            clone._devices.append(_clone_device(device, clone))
        for slot_index, slot in enumerate(source._clip_slots):
            if slot._clip is not None and slot_index < len(clone._clip_slots):
                target = clone._clip_slots[slot_index]
                target._clip = _clone_clip(slot._clip, target)
        self._tracks.insert(index + 1, clone)
        self._view._selected_track = clone
        self.notify_listeners("tracks")

    def move_device(self, device, target, target_position):
        """Move ``device`` into ``target`` (Track/Chain); returns the new index."""
        source = device._canonical_parent
        if device in source._devices:
            source._devices.remove(device)
        position = max(0, min(_as_int(target_position), len(target._devices)))
        target._devices.insert(position, device)
        device._canonical_parent = target
        return position

    def find_device_position(self, device, target, target_position):
        return max(0, min(_as_int(target_position), len(target._devices)))

    # -- undo ---------------------------------------------------------------------------------
    def begin_undo_step(self):
        self._undo_depth += 1

    def end_undo_step(self):
        if self._undo_depth <= 0:
            raise RuntimeError("end_undo_step without begin_undo_step")
        self._undo_depth -= 1
        if self._undo_depth == 0:
            self._undo_steps.append(len(self._undo_steps) + 1)
            self._undo_stack.append("step")

    def undo(self):
        """Returns the name of the undone action (a str)."""
        if self._undo_stack:
            self._redo_stack.append(self._undo_stack.pop())
            return "Undo"
        return ""

    def redo(self):
        if self._redo_stack:
            self._undo_stack.append(self._redo_stack.pop())
            return "Redo"
        return ""


# ==========================================================================
# Browser
# ==========================================================================

class BrowserItemIterator(object):
    """``Live.Browser.BrowserItemIterator`` — what ``item.iter_children``
    returns: iterable and ``len()``-able (Push uses both)."""

    def __init__(self, items):
        self._items = tuple(items)
        self._index = 0

    def __iter__(self):
        return iter(self._items)

    def __next__(self):
        if self._index >= len(self._items):
            raise StopIteration
        item = self._items[self._index]
        self._index += 1
        return item

    def __len__(self):
        return len(self._items)


class BrowserItem(object):
    """``Live.Browser.BrowserItem`` (a plain instance, not a LomObject: it has
    no ``canonical_parent``).

    ``children`` is lazy (``children_factory``), ``iter_children`` is a
    **property** returning a :class:`BrowserItemIterator`.
    ``load_kind``/``device_type`` are stub-only (private) hints for
    ``Browser.load_item``.
    """

    def __init__(self, name, uri=None, is_folder=False, is_device=False,
                 is_loadable=False, source="", children=None,
                 children_factory=None, load_kind=None, canonical_parent=None,
                 device_type=DeviceType.audio_effect):
        self._name = name
        self._uri = uri if uri is not None else "query:Stub#" + name.replace(" ", "%20")
        self._is_folder = bool(is_folder)
        self._is_device = bool(is_device)
        self._is_loadable = bool(is_loadable)
        self._is_selected = False
        self._source = source
        self._device_type = int(device_type)
        self._load_kind = load_kind or ("device" if is_device else None)
        self._children_loaded = False
        self._children_factory = children_factory
        self._parent = canonical_parent
        self._children = []
        for child in (children or ()):
            self.add_child(child)

    name = property(lambda self: self._name)
    uri = property(lambda self: self._uri)
    is_folder = property(lambda self: self._is_folder)
    is_device = property(lambda self: self._is_device)
    is_loadable = property(lambda self: self._is_loadable)
    is_selected = property(lambda self: self._is_selected)
    source = property(lambda self: self._source)

    def add_child(self, child):
        """Stub-only: attach ``child`` under this item."""
        child._parent = self
        self._children.append(child)
        return child

    @property
    def children(self):
        if self._children_factory is not None and not self._children_loaded:
            self._children_loaded = True
            for child in self._children_factory(self):
                self.add_child(child)
        self._children_loaded = True
        return BrowserItemVector(self._children)

    @property
    def iter_children(self):
        return BrowserItemIterator(self.children)

    def __repr__(self):
        return "<BrowserItem %r>" % self._name


class Browser(LomObject):
    """``Live.Browser.Browser`` (``app.browser``).

    Roots are BrowserItems except ``colors``, ``user_folders`` and
    ``legacy_libraries`` which are tuples of BrowserItems.  There is no
    ``splice`` root.  ``hotswap_target`` and ``filter_type`` are writable.

    ``load_item(item)`` in the stub: with a hotswap target it replaces the target
    device / loads the sample into the target Simpler (the new device stays the
    target); a device lands on the selected track at ``track.view.device_insert_mode``;
    a sample on a MIDI track creates a Simpler, on an audio track it fills the
    highlighted clip slot; a clip fills the highlighted clip slot.

    Measured on Live 12.4.5 (T3 live test, 2026-09-10):

    * **Hot-swap filtering** — while ``hotswap_target`` is set the browser is filtered:
      roots that cannot hold a replacement list no children (an instrument target empties
      ``audio_effects`` / ``midi_effects``; an audio-effect target empties ``instruments``,
      ``sounds``, ``drums`` and ``midi_effects``), ``app.view.browse_mode`` is true and
      ``filter_type`` still reads -1.
    * Setting the target that already is the target raises
      ``RuntimeError("Couldn't set hotswap target")``.
    * ``load_item`` of a Live Clip (``.alc``) always creates a new MIDI track
      (``"<n>-<clip>"``) holding the clip in slot 0 — selection is ignored.
    * ``load_item`` of a sample/clip that would fill a clip slot (a sample on an audio
      track, any clip) does nothing while the Arrangement view is focused (no exception,
      no clip); a sample onto a MIDI track still becomes a Simpler.
    """

    _LISTENABLE = ("hotswap_target", "filter_type")

    ROOTS = ("audio_effects", "clips", "current_project", "drums", "instruments",
             "max_for_live", "midi_effects", "packs", "plugins", "samples", "sounds",
             "user_library")
    LIST_ROOTS = ("colors", "user_folders", "legacy_libraries")

    #: roots Live empties while a hot-swap target of this device type is set
    _EMPTY_WHEN = {
        1: ("audio_effects", "midi_effects"),                     # instrument target
        2: ("instruments", "sounds", "drums", "midi_effects"),    # audio effect target
        4: ("instruments", "sounds", "drums", "audio_effects"),   # midi effect target
    }

    def __init__(self, song=None):
        LomObject.__init__(self, None)
        self._song = song
        self._app = None
        self._filtered_roots = {}
        self._hotswap_target = None
        self._filter_type = int(FilterType.disabled)
        self.loaded_items = []      # stub-only log
        self.previewed_items = []   # stub-only log
        for root in self.ROOTS:
            setattr(self, "_" + root, BrowserItem(
                root.replace("_", " ").title(), uri="query:%s" % root.replace("_", ""),
                is_folder=True, source="Live"))
        self._colors = tuple(BrowserItem(name, uri="query:Colors#" + name, is_folder=True)
                             for name in ("Red", "Orange", "Yellow", "Green", "Blue",
                                          "Purple", "Gray"))
        self._user_folders = ()
        self._legacy_libraries = ()

    def _target_type(self):
        target = self._hotswap_target
        if target is None or not hasattr(target, "type"):
            return None
        try:
            return int(target.type)
        except (TypeError, ValueError):
            return None

    def _filtered_root(name):
        def fget(self):
            item = getattr(self, "_" + name)
            ttype = self._target_type()
            if ttype is not None and name in self._EMPTY_WHEN.get(ttype, ()):
                if name not in self._filtered_roots:
                    self._filtered_roots[name] = BrowserItem(item.name, uri=item.uri,
                                                             is_folder=True,
                                                             source=item.source)
                return self._filtered_roots[name]
            return item
        return property(fget)

    audio_effects = _filtered_root("audio_effects")
    clips = _ro("clips")
    current_project = _ro("current_project")
    drums = _filtered_root("drums")
    instruments = _filtered_root("instruments")
    max_for_live = _ro("max_for_live")
    midi_effects = _filtered_root("midi_effects")
    packs = _ro("packs")
    plugins = _ro("plugins")
    samples = _ro("samples")
    sounds = _filtered_root("sounds")
    user_library = _ro("user_library")
    colors = _ro("colors")
    user_folders = _ro("user_folders")
    legacy_libraries = _ro("legacy_libraries")
    filter_type = _rw("filter_type", _as_int)
    del _filtered_root

    @property
    def hotswap_target(self):
        return self._hotswap_target

    @hotswap_target.setter
    def hotswap_target(self, target):
        current = self._hotswap_target
        if target is not None and current is not None and (target is current
                                                           or target == current):
            raise RuntimeError("Couldn't set hotswap target")
        self._hotswap_target = target
        view = getattr(self._app, "_view", None)
        if view is not None:
            view._browse_mode = target is not None
        self.notify_listeners("hotswap_target")

    def _focused_view(self):
        view = getattr(self._app, "_view", None)
        return getattr(view, "_focused_document_view", "Session") if view else "Session"

    # -- loading -------------------------------------------------------------------------
    def load_item(self, item, /):
        if not isinstance(item, BrowserItem):
            raise _boost_type_error("Browser.load_item", item)
        if not item._is_loadable:
            raise RuntimeError("browser item %r is not loadable" % item._name)
        self.loaded_items.append(item)
        song = self._song
        if song is None:
            return None
        kind = item._load_kind
        target = self._hotswap_target
        if target is not None:
            return self._hotswap(item, kind, target)
        if kind == "clip" and item._name.lower().endswith(".alc"):
            stem = os.path.splitext(item._name)[0]
            new_track = song.create_midi_track(-1)
            new_track.name = "%d-%s" % (len(song._tracks), stem)
            slot = new_track._clip_slots[0]
            clip = Clip(stem, 4.0, True, slot, "")
            slot._set_clip(clip)
            return clip
        track = song._view._selected_track
        if track is None:
            raise RuntimeError("load_item: no track selected")
        arranger = self._focused_view() == "Arranger"
        if arranger and (kind == "clip" or (kind == "sample" and not track._is_midi)):
            return None       # Live 12.4.5: no clip-slot loads while the Arrangement is focused
        if kind == "device":
            return self._load_device(item, track)
        if kind == "sample":
            if track._is_midi:
                device = SimplerDevice(os.path.splitext(item._name)[0], "OriginalSimpler",
                                       DeviceType.instrument, (), track,
                                       "/Stub/" + item._name)
                return self._insert(track, device)
            return self._fill_slot(item, track, is_midi=False)
        if kind == "clip":
            return self._fill_slot(item, track, is_midi=track._is_midi)
        raise RuntimeError("browser item %r cannot be loaded here" % item._name)

    def _insert(self, track, device):
        mode = track._view._device_insert_mode
        selected = track._view._selected_device
        index = len(track._devices)
        if selected in track._devices and mode == int(DeviceInsertMode.selected_left):
            index = track._devices.index(selected)
        elif selected in track._devices and mode == int(DeviceInsertMode.selected_right):
            index = track._devices.index(selected) + 1
        device._canonical_parent = track
        track._devices.insert(index, device)
        track._view._selected_device = device
        track.notify_listeners("devices")
        return device

    def _load_device(self, item, track):
        class_name = item._name.replace(" ", "")
        if item._name in NATIVE_DEVICES:
            class_name = NATIVE_DEVICES[item._name][0]
        device = _build_native_device(item._name, class_name, item._device_type, track) \
            if item._name in NATIVE_DEVICES else \
            Device(item._name, class_name, item._device_type,
                   ({"name": "Param 1", "value": 0.5}, {"name": "Param 2", "value": 0.5}),
                   track)
        return self._insert(track, device)

    def _fill_slot(self, item, track, is_midi):
        slot = self._song._view._highlighted_clip_slot
        if slot is None or slot._canonical_parent is not track:
            raise RuntimeError("load_item: no clip slot of the selected track is highlighted")
        if slot._clip is not None:
            raise RuntimeError("load_item: the highlighted clip slot is not empty")
        clip = Clip(os.path.splitext(item._name)[0], 4.0, is_midi, slot,
                    "" if is_midi else "/Stub/" + item._name)
        slot._set_clip(clip)
        return clip

    def _hotswap(self, item, kind, target):
        if kind == "sample" and isinstance(target, SimplerDevice):
            target.replace_sample("/Stub/" + item._name)
            return target
        if kind == "device" and isinstance(target, Device):
            host = target._canonical_parent
            index = host._devices.index(target)
            host._devices.pop(index)
            device = Device(item._name, item._name.replace(" ", ""), item._device_type,
                            ({"name": "Param 1", "value": 0.5},), host)
            host._devices.insert(index, device)
            self._hotswap_target = device
            return device
        raise RuntimeError("load_item: %r cannot replace the hotswap target" % item._name)

    def preview_item(self, item, /):
        self.previewed_items.append(item)

    def stop_preview(self):
        self.previewed_items.append(None)

    def relation_to_hotswap_target(self, item, /):
        """A ``Browser.Relation`` value (3 = none)."""
        if self._hotswap_target is None:
            return Relation.none
        if getattr(self._hotswap_target, "name", None) == item._name:
            return Relation.equal
        return Relation.none


# ==========================================================================
# Application
# ==========================================================================

class ApplicationView(LomObject):
    """``Live.Application.Application.View``.

    View names: Browser, Arranger, Session, Detail, Detail/Clip,
    Detail/DeviceChain; ``""`` means the visible main view.
    ``scroll_view``/``zoom_view(direction, view_name, modifier_pressed)`` take
    all three arguments (direction 0 up, 1 down, 2 left, 3 right).
    ``focused_document_view`` and ``browse_mode`` are read-only.
    """

    _LISTENABLE = ("focused_document_view", "is_view_visible", "browse_mode")

    NAMES = ("Browser", "Arranger", "Session", "Detail", "Detail/Clip",
             "Detail/DeviceChain")

    NavDirection = NavDirection

    def __init__(self, application):
        LomObject.__init__(self, application)
        self._focused_document_view = "Session"
        self._browse_mode = False
        self._visible = {"Session": True, "Detail": True, "Detail/Clip": True}
        self.zoom_log = []          # stub-only: directions passed to zoom_view

    focused_document_view = _ro("focused_document_view")
    browse_mode = _ro("browse_mode")

    def _check(self, name):
        name = _as_str(name, "View identifier")
        if name.strip() == "":
            return self._focused_document_view
        if name not in self.NAMES:
            raise RuntimeError("unknown view %r (use one of %s)" % (name, ", ".join(self.NAMES)))
        return name

    def available_main_views(self):
        return self.NAMES

    def show_view(self, name, /):
        name = self._check(name)
        self._visible[name] = True
        if name in ("Session", "Arranger"):
            self._focused_document_view = name
            self._visible["Session" if name == "Arranger" else "Arranger"] = False
            self.notify_listeners("focused_document_view")

    def hide_view(self, name, /):
        name = self._check(name)
        self._visible[name] = False

    def focus_view(self, name, /):
        name = self._check(name)
        self.show_view(name)

    def is_view_visible(self, identifier, main_window_only=True):
        return bool(self._visible.get(self._check(identifier), False))

    def scroll_view(self, direction, view_name, modifier_pressed, /):
        self._last_scroll = (_as_int(direction), self._check(view_name),
                             _as_bool(modifier_pressed))

    def zoom_view(self, direction, view_name, modifier_pressed, /):
        """Direction 3 zooms the Arrangement in (finer grid), 2 zooms out (Live 12.4.5)."""
        direction = _as_int(direction)
        name = self._check(view_name)
        self._last_zoom = (direction, name, _as_bool(modifier_pressed))
        self.zoom_log.append(direction)          # stub-only log
        song = getattr(self._canonical_parent, "_song", None)
        if song is not None and name == "Arranger":
            if direction == 3:
                song._arrangement_grid = max(1.0 / 256, song._arrangement_grid / 2.0)
            elif direction == 2:
                song._arrangement_grid = min(32.0, song._arrangement_grid * 2.0)

    def toggle_browse(self):
        self._browse_mode = not self._browse_mode
        self._visible["Browser"] = self._browse_mode


class Application(LomObject):
    """``Live.Application.Application``.

    ``get_variant()`` (Live 12) returns ``"Suite"``, ``"Standard"``,
    ``"Intro"``, ``"Lite"``, ``"Trial"`` or ``"Beta"``.  There is no
    ``get_major_minor_version``.
    """

    _LISTENABLE = ("open_dialog_count", "control_surfaces", "average_process_usage")

    View = ApplicationView
    MessageButtons = MessageButtons

    def __init__(self, song=None, version=(12, 4, 5), variant="Suite"):
        LomObject.__init__(self, None)
        self._song = song
        self._version = tuple(version)
        self._variant = variant
        self._view = ApplicationView(self)
        self._browser = Browser(song)
        self._browser._app = self
        self._control_surfaces = ()
        self._open_dialog_count = 0
        self._current_dialog_message = ""
        self._current_dialog_button_count = 0
        self._options = set()
        self.shown_messages = []   # stub-only log

    view = _ro("view")
    browser = _ro("browser")
    control_surfaces = _ro("control_surfaces")
    open_dialog_count = _ro("open_dialog_count")
    current_dialog_message = _ro("current_dialog_message")
    current_dialog_button_count = _ro("current_dialog_button_count")
    average_process_usage = property(lambda self: 0.1)
    peak_process_usage = property(lambda self: 0.2)
    number_of_push_apps_running = property(lambda self: 0)
    unavailable_features = property(lambda self: ())

    def get_document(self):
        return self._song

    def get_major_version(self):
        return self._version[0]

    def get_minor_version(self):
        return self._version[1]

    def get_bugfix_version(self):
        return self._version[2]

    def get_version_string(self):
        return "%d.%d.%d" % self._version

    def get_build_id(self):
        return "stub-%d.%d.%d" % self._version

    def get_variant(self):
        return self._variant

    def has_option(self, name, /):
        return name in self._options

    def press_current_dialog_button(self, index, /):
        self._open_dialog_count = max(0, self._open_dialog_count - 1)
        self._current_dialog_message = ""

    def show_message(self, text, buttons=0, enable_markup=False, show_success_icon=False):
        self.shown_messages.append(text)
        return 0


_APPLICATION = None


def set_application(application):
    """Stub-only: install the object returned by :func:`get_application`."""
    global _APPLICATION
    _APPLICATION = application
    return application


def get_application():
    """Mirrors ``Live.Application.get_application()``."""
    global _APPLICATION
    if _APPLICATION is None:
        _APPLICATION = Application(Song())
    return _APPLICATION
