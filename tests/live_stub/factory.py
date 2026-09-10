"""Builders for the fake Live Object Model.

Module builders should **extend fixtures with these helpers instead of editing
``Live/_model.py``** — the stub is shared by every agent.  If you need extra
behaviour that the builders cannot express, add
``tests/live_stub_ext/<module>.py`` with a function ``install(stub)`` and call
it from your own test file (see ``docs/ARCHITECTURE.md`` §2).

Typical use::

    from live_stub import factory

    song = factory.make_song(midi_tracks=2, audio_tracks=1, scenes=4)
    app = factory.make_application(song)                 # also installs it on
                                                         # Live.Application
    synth = factory.add_device(song.tracks[0], "Operator", kind="instrument",
                               params=[("Volume", 0.8), ("Filter", 0.5)])
    clip = factory.add_clip(song.tracks[0], slot=0, length=4,
                            notes=[(60, 0.0, 0.5, 100), (64, 1.0, 0.5, 90)])

Remember the verified Live 12 facts the stub enforces (see
``docs/LIVE_API_VERIFIED.md``): every device's ``parameters[0]`` is
``"Device On"`` (your ``params`` start at index 1), ``device.is_active`` is
read-only, read-only properties raise ``AttributeError`` when assigned, and
``DeviceType.midi_effect == 4``.

Everything returns the created LOM object so you can keep building on it.
"""

import Live
from Live._model import (
    Application, BrowserItem, Clip, CuePoint, Device, DeviceParameter, DeviceType,
    MidiNote, PluginDevice, RackDevice, SimplerDevice, Song,
)

__all__ = [
    "make_song", "make_application", "make_c_instance", "FakeCInstance",
    "add_track", "add_return_track", "add_group_track", "add_scene",
    "add_cue_point", "add_device", "add_rack", "add_drum_rack", "add_plugin",
    "add_simpler", "add_parameter", "add_chain", "add_clip",
    "add_arrangement_clip", "add_notes", "add_browser_item", "add_user_folder",
    "populate_browser", "add_browser_devices", "DEVICE_TYPES",
]

DEVICE_TYPES = {
    "instrument": DeviceType.instrument,
    "audio_effect": DeviceType.audio_effect,
    "midi_effect": DeviceType.midi_effect,
    "undefined": DeviceType.undefined,
}


def _device_type(value):
    if isinstance(value, str):
        try:
            return int(DEVICE_TYPES[value])
        except KeyError:
            raise ValueError("unknown device type %r (use one of %s)"
                             % (value, ", ".join(sorted(DEVICE_TYPES))))
    return int(value)


def _param_specs(params):
    """Accept ``["Name", ("Name", 0.5), ("Name", 0.5, 0.0, 1.0), {...}]``."""
    specs = []
    for param in params or ():
        if isinstance(param, (DeviceParameter, dict)):
            specs.append(param)
        elif isinstance(param, str):
            specs.append({"name": param, "value": 0.0, "min": 0.0, "max": 1.0})
        elif isinstance(param, (tuple, list)):
            keys = ("name", "value", "min", "max")
            spec = dict(zip(keys, param))
            spec.setdefault("value", 0.0)
            spec.setdefault("min", 0.0)
            spec.setdefault("max", 1.0)
            specs.append(spec)
        else:
            raise TypeError("cannot build a DeviceParameter from %r" % (param,))
    return specs


# --------------------------------------------------------------------------
# song / application
# --------------------------------------------------------------------------

def make_song(midi_tracks=1, audio_tracks=1, return_tracks=2, scenes=4,
              tempo=120.0, names=None):
    """Build a :class:`Live.Song.Song` with the requested shape.

    ``names`` optionally overrides the generated track names (a list applied to
    the MIDI tracks first, then the audio tracks).  Returns the song; use
    :func:`make_application` to give it a browser and register it globally.
    """
    song = Song()
    song.tempo = float(tempo)
    for _ in range(scenes):
        song.create_scene(-1)
    names = list(names or [])
    for _ in range(midi_tracks):
        song.create_midi_track(-1)
    for _ in range(audio_tracks):
        song.create_audio_track(-1)
    for index, name in enumerate(names):
        if index < len(song.tracks):
            song.tracks[index].name = name
    for _ in range(return_tracks):
        song.create_return_track()
    if song.tracks:
        song.view.selected_track = song.tracks[0]
        if song.tracks[0].clip_slots:
            song.view.highlighted_clip_slot = song.tracks[0].clip_slots[0]
    if song.scenes:
        song.view.selected_scene = song.scenes[0]
    return song


def make_application(song=None, version=(12, 4, 5), install=True,
                     browser=True, variant="Suite"):
    """Create an :class:`Application` for ``song`` (and install it globally so
    ``Live.Application.get_application()`` returns it).

    ``variant`` is what ``app.get_variant()`` reports (``"Suite"``,
    ``"Standard"``, ``"Intro"``, ``"Lite"``, ``"Trial"``, ``"Beta"``).
    """
    if song is None:
        song = make_song()
    app = Application(song, version, variant)
    if browser:
        populate_browser(app.browser)
    if install:
        Live.Application.set_application(app)
    return app


# --------------------------------------------------------------------------
# tracks / scenes / cues
# --------------------------------------------------------------------------

def add_track(song, name=None, kind="midi", index=-1):
    """Insert a track. ``kind`` is ``"midi"``, ``"audio"`` or ``"return"``."""
    if kind == "return":
        return add_return_track(song, name)
    if kind == "midi":
        track = song.create_midi_track(index)
    elif kind == "audio":
        track = song.create_audio_track(index)
    else:
        raise ValueError("unknown track kind %r" % (kind,))
    if name:
        track.name = name
    return track


def add_return_track(song, name=None):
    """Append a return track (and a matching send on every track)."""
    track = song.create_return_track()
    if name:
        track.name = name
    return track


def add_group_track(song, name="Group", members=(), index=-1):
    """Create a group track holding ``members`` (tracks of ``song``).

    The LOM has no API to group tracks, so this is a stub-only builder: the
    group gets ``is_foldable``/``fold_state``, members get ``is_grouped`` /
    ``group_track``.
    """
    group = song.create_audio_track(index)
    group.name = name
    group._make_group(members)
    return group


def add_scene(song, name=None, index=-1):
    """Append/insert a scene (clip slots are kept in sync automatically)."""
    scene = song.create_scene(index)
    if name:
        scene.name = name
    return scene


def add_cue_point(song, name="Cue", time=0.0):
    """Add an arrangement cue point (locator) at ``time`` beats."""
    cue = CuePoint(name, time, song)
    song._cue_points.append(cue)
    song._cue_points.sort(key=lambda c: c.time)
    return cue


# --------------------------------------------------------------------------
# devices
# --------------------------------------------------------------------------

def add_device(host, name, class_name=None, kind="audio_effect", params=None,
               index=-1):
    """Append a plain :class:`Device` to a track or chain.

    ``host``  — a Track or a Chain.
    ``kind``  — ``"instrument"``, ``"audio_effect"``, ``"midi_effect"``.
    ``params`` — see :func:`_param_specs`; they follow the automatic
    ``"Device On"`` parameter, so the first one is ``parameters[1]``.
    """
    device = Device(name, class_name, _device_type(kind), _param_specs(params), host)
    return _attach_device(host, device, index)


def _attach_device(host, device, index=-1):
    devices = host._devices
    if index < 0 or index >= len(devices):
        devices.append(device)
    else:
        devices.insert(index, device)
    device._canonical_parent = host
    host.notify_listeners("devices")
    return device


def add_rack(host, name="Instrument Rack", kind="instrument", chains=2,
             params=None, class_name=None, index=-1):
    """Append a rack with ``chains`` (empty) chains, 16 macros + Chain Selector."""
    class_names = {"instrument": "InstrumentGroupDevice",
                   "audio_effect": "AudioEffectGroupDevice",
                   "midi_effect": "MidiEffectGroupDevice"}
    rack = RackDevice(name, class_name or class_names.get(kind, "AudioEffectGroupDevice"),
                      _device_type(kind), _param_specs(params), host)
    for i in range(chains):
        rack.add_chain("Chain %d" % (i + 1))
    return _attach_device(host, rack, index)


def add_drum_rack(host, name="Drum Rack", pads=((36, "Kick"), (38, "Snare"),
                                                (42, "Hat")), index=-1):
    """Append a Drum Rack; ``pads`` is a list of ``(note, name)`` pairs, each
    getting one DrumChain (``in_note`` = note) with a Simpler holding
    ``/Samples/Drums/<name>.wav``."""
    rack = RackDevice(name, "DrumGroupDevice", DeviceType.instrument, (), host,
                      can_have_drum_pads=True)
    _attach_device(host, rack, index)
    for note, pad_name in pads:
        chain = rack.add_chain(pad_name, in_note=note)
        add_simpler(chain, pad_name, file_path="/Samples/Drums/%s.wav" % pad_name)
    return rack


def add_plugin(host, name="Serum", params=None, kind="instrument",
               presets=("Init", "Bass 1"), class_name="PluginDevice", index=-1,
               plugin_parameter_names=None):
    """Append a VST/AU wrapper device (``class_name`` ``PluginDevice`` for
    VST2/VST3, ``AuPluginDevice`` for Audio Units).  ``params`` are the exposed
    (Configure) parameters; ``plugin_parameter_names`` is everything the plug-in has
    (``get_parameter_names()``), default: the exposed names."""
    plugin = PluginDevice(name, class_name, _device_type(kind),
                          _param_specs(params), host, presets, plugin_parameter_names)
    return _attach_device(host, plugin, index)


def add_simpler(host, name="Simpler", file_path="", params=None, index=-1):
    """Append a Simpler (``sample`` is None when ``file_path`` is empty)."""
    if params is None:
        params = [("Volume", 0.8), ("Attack", 0.0, 0.0, 10.0),
                  ("Release", 1.0, 0.0, 60.0)]
    simpler = SimplerDevice(name, "OriginalSimpler", DeviceType.instrument,
                            _param_specs(params), host, file_path)
    return _attach_device(host, simpler, index)


def add_parameter(device, name, value=0.0, min=0.0, max=1.0, is_quantized=False,
                  value_items=(), unit="", default_value=None):
    """Append a :class:`DeviceParameter` to an existing device."""
    param = DeviceParameter(name, value, min, max, default_value, is_quantized,
                            value_items, unit=unit, canonical_parent=device)
    device._parameters.append(param)
    return param


def add_chain(rack, name=None, devices=(), in_note=None):
    """Add a chain to a rack; ``devices`` is a list of names or kwargs dicts.

    For Drum Racks pass ``in_note`` (the pad note that triggers the chain).
    """
    chain = rack.add_chain(name, in_note=in_note)
    for device in devices:
        if isinstance(device, dict):
            add_device(chain, **device)
        else:
            add_device(chain, device)
    return chain


# --------------------------------------------------------------------------
# clips / notes
# --------------------------------------------------------------------------

def _note_objects(notes):
    result = []
    for note in notes or ():
        if isinstance(note, MidiNote):
            result.append(note)
        elif isinstance(note, dict):
            result.append(MidiNote(**note))
        else:
            keys = ("pitch", "start_time", "duration", "velocity", "mute")
            result.append(MidiNote(**dict(zip(keys, note))))
    return result


def add_clip(track, slot=0, length=4.0, name="", notes=(), audio=False,
             file_path="", looping=True):
    """Put a clip in ``track.clip_slots[slot]`` and return it.

    MIDI clips get ``notes`` (``(pitch, start, duration, velocity)`` tuples,
    dicts or :class:`MidiNote` objects).  Audio clips are created directly
    with ``file_path`` (in Live use ``ClipSlot.create_audio_clip(path)``).
    """
    if slot >= len(track.clip_slots):
        raise IndexError("track %r has only %d clip slots"
                         % (track.name, len(track.clip_slots)))
    clip_slot = track.clip_slots[slot]
    if clip_slot.has_clip:
        clip_slot.delete_clip()
    clip = Clip(name, float(length), not audio, clip_slot, file_path)
    clip._looping = bool(looping)
    clip._notes = _note_objects(notes)
    clip_slot._set_clip(clip)
    return clip


def add_arrangement_clip(track, start_time=0.0, length=4.0, name="", notes=(),
                         audio=False, file_path="/Samples/stub.wav"):
    """Add a clip to the track's arrangement timeline (via the real API)."""
    if audio:
        clip = track.create_audio_clip(file_path, float(start_time))
        clip._arrangement_length = float(length)
        clip._loop_end = clip._end_marker = float(length)
    else:
        clip = track.create_midi_clip(float(start_time), float(length))
        clip._notes = _note_objects(notes)
    clip.name = name
    return clip


def add_notes(clip, notes):
    """Append notes to an existing MIDI clip (same formats as :func:`add_clip`)."""
    clip._notes.extend(_note_objects(notes))
    clip.notify_listeners("notes")
    return clip


# --------------------------------------------------------------------------
# browser
# --------------------------------------------------------------------------

def add_browser_item(parent, name, uri=None, is_folder=False, is_device=False,
                     is_loadable=None, load_kind=None, children=(), source="",
                     device_type=DeviceType.audio_effect, children_factory=None):
    """Add a :class:`BrowserItem` under ``parent`` (a root or another item).

    ``load_kind`` drives what ``browser.load_item()`` does in the stub:
    ``"device"`` inserts a device on the selected track, ``"sample"`` makes a
    Simpler (MIDI track) or fills the highlighted clip slot (audio track),
    ``"clip"`` fills the highlighted clip slot.  ``children_factory`` (a
    callable taking the item) makes the children lazy, like Live does.
    """
    if is_loadable is None:
        is_loadable = not is_folder
    item = BrowserItem(name, uri, is_folder, is_device, is_loadable,
                       source or getattr(parent, "source", ""), None,
                       children_factory, load_kind, parent, device_type)
    for child in children:
        item.add_child(child)
    if hasattr(parent, "add_child"):
        parent.add_child(item)
    return item


def add_user_folder(browser, name, children=()):
    """Add a folder to ``browser.user_folders`` (a tuple in Live)."""
    folder = BrowserItem(name, uri="userfolder:%s" % name.replace(" ", "%20"),
                         is_folder=True, source="User Folder")
    for child in children:
        folder.add_child(child)
    browser._user_folders = tuple(browser._user_folders) + (folder,)
    return folder


def add_browser_devices(browser):
    """Give the browser's device roots every top-level device of Live 12.4.5 Suite — the
    native ones (``Live._model.NATIVE_DEVICES``) and the Max for Live based ones the
    browser lists next to them (``MAX_FOR_LIVE_DEVICES``: LFO, Shaper, DS Kick ...) — so
    name lookups behave like on the real Live."""
    from Live import _model as model
    for root_name, device_type in (("instruments", 1), ("midi_effects", 4),
                                   ("audio_effects", 2)):
        root = getattr(browser, root_name)
        existing = set(child.name for child in root.children)
        names = [n for n, (_c, t) in model.NATIVE_DEVICES.items() if int(t) == device_type]
        names += list(model.MAX_FOR_LIVE_DEVICES[root_name])
        for name in sorted(names):
            if name in existing:
                continue
            root.add_child(model.BrowserItem(
                name, uri="query:%s#%s" % (root_name, name.replace(" ", "%20")),
                is_device=True, is_loadable=True, source="Built-in",
                device_type=model.DeviceType(device_type)))


def populate_browser(browser):
    """Fill a :class:`Browser` with a small but realistic tree.

    Roots touched: ``instruments``, ``audio_effects``, ``midi_effects``,
    ``drums``, ``sounds``, ``plugins``, ``samples``, ``packs``,
    ``user_library``, ``clips``, ``max_for_live``, ``current_project`` and one
    entry in ``user_folders``.
    """
    instruments = browser.instruments
    for name in ("Operator", "Wavetable", "Analog", "Simpler"):
        add_browser_item(instruments, name, uri="query:Synths#" + name,
                         is_device=True, load_kind="device",
                         device_type=DeviceType.instrument, source="Live")
    add_browser_item(instruments, "Drum Rack", uri="query:Drums#Drum%20Rack",
                     is_device=True, load_kind="device",
                     device_type=DeviceType.instrument, source="Live")

    audio_effects = browser.audio_effects
    for name in ("Reverb", "Delay", "EQ Eight", "Compressor", "Auto Filter"):
        add_browser_item(audio_effects, name,
                         uri="query:AudioFx#" + name.replace(" ", "%20"),
                         is_device=True, load_kind="device",
                         device_type=DeviceType.audio_effect, source="Live")

    for name in ("Arpeggiator", "Chord", "Scale"):
        add_browser_item(browser.midi_effects, name,
                         uri="query:MidiFx#" + name, is_device=True,
                         load_kind="device", device_type=DeviceType.midi_effect,
                         source="Live")

    kits = add_browser_item(browser.drums, "Drum Kits", is_folder=True)
    for name in ("Kit-Core 808", "Kit-Core 909"):
        add_browser_item(kits, name, uri="query:Drums#" + name.replace(" ", "%20"),
                         is_device=True, load_kind="device",
                         device_type=DeviceType.instrument)

    sounds = add_browser_item(browser.sounds, "Bass", is_folder=True)
    add_browser_item(sounds, "Sub Bass", uri="query:Sounds#Sub%20Bass",
                     is_device=True, load_kind="device",
                     device_type=DeviceType.instrument)

    plugins = browser.plugins
    vst3 = add_browser_item(plugins, "VST3", is_folder=True, source="plugins")
    for name in ("Serum", "Diva"):
        add_browser_item(vst3, name, uri="query:Plugins#VST3:" + name,
                         is_device=True, load_kind="device",
                         device_type=DeviceType.instrument, source="plugins")
    au = add_browser_item(plugins, "Audio Units", is_folder=True, source="plugins")
    add_browser_item(au, "AUSampler", uri="query:Plugins#AU:AUSampler",
                     is_device=True, load_kind="device",
                     device_type=DeviceType.instrument, source="plugins")

    samples = browser.samples

    def _sample_children(item):
        return [BrowserItem("Kick %d.wav" % i,
                            uri="query:Samples#Drums:Kick%%20%d.wav" % i,
                            is_loadable=True, load_kind="sample", source="Live")
                for i in range(1, 4)]

    add_browser_item(samples, "Drums", is_folder=True, source="Live",
                     children_factory=_sample_children)
    add_browser_item(samples, "Vocal Loop.wav",
                     uri="query:Samples#Vocal%20Loop.wav", is_loadable=True,
                     load_kind="sample", source="Live")

    packs = add_browser_item(browser.packs, "Core Library", is_folder=True,
                             source="Core Library")
    add_browser_item(packs, "Grand Piano", uri="query:Packs#Grand%20Piano",
                     is_device=True, load_kind="device",
                     device_type=DeviceType.instrument, source="Core Library")

    user = browser.user_library
    my_sounds = add_browser_item(user, "My Sounds", is_folder=True,
                                 source="User Library")
    add_browser_item(my_sounds, "My Lead.adg", uri="userlibrary:My%20Sounds:My%20Lead.adg",
                     is_device=True, load_kind="device",
                     device_type=DeviceType.instrument, source="User Library")
    add_browser_item(user, "My Loop.wav", uri="userlibrary:My%20Loop.wav",
                     is_loadable=True, load_kind="sample", source="User Library")

    add_browser_item(browser.clips, "Demo Clip.alc", uri="query:Clips#Demo%20Clip.alc",
                     is_loadable=True, load_kind="clip", source="Live")
    add_browser_item(browser.max_for_live, "Max Instrument",
                     uri="query:M4L#Max%20Instrument", is_device=True,
                     load_kind="device", device_type=DeviceType.instrument,
                     source="Live")
    add_browser_item(browser.current_project, "Project Samples", is_folder=True,
                     source="Current Project")
    add_user_folder(browser, "Sample Stash", children=[
        BrowserItem("Snare Top.wav", uri="userfolder:Sample%20Stash:Snare%20Top.wav",
                    is_loadable=True, load_kind="sample", source="User Folder")])
    return browser


# --------------------------------------------------------------------------
# c_instance
# --------------------------------------------------------------------------

class FakeCInstance(object):
    """Stand-in for the opaque ``c_instance`` Live hands to a remote script.

    Only what the real one offers (verified from Ableton's framework code):
    ``song()``, ``log_message``, ``show_message``, ``send_midi``,
    ``instance_identifier``, ``request_rebuild_midi_map``, ``handle`` ...
    There is **no** ``schedule_message`` on the real c_instance — scheduling
    lives in ``ControlSurface`` and runs from ``update_display``.

    Records ``.log``, ``.messages`` and ``.midi`` for assertions.
    """

    def __init__(self, song):
        self._song = song
        self.log = []
        self.messages = []
        self.midi = []

    def song(self):
        return self._song

    def log_message(self, message):
        self.log.append(str(message))

    def show_message(self, message):
        self.messages.append(str(message))

    def send_midi(self, midi_bytes):
        self.midi.append(tuple(midi_bytes))

    def instance_identifier(self):
        return "LiveBridgeTest"

    def handle(self):
        return 0

    def request_rebuild_midi_map(self):
        pass

    def set_pad_translation(self, translations):
        pass

    def set_feedback_channels(self, channels):
        pass


def make_c_instance(song):
    """Convenience wrapper around :class:`FakeCInstance`."""
    return FakeCInstance(song)
