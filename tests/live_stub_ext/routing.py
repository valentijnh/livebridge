"""Stub extension for the routing tests: track-to-track routing like real Live 12.4.5.

The shared stub offers only fixed routing lists ("Master"/"Sends Only" outputs, no other
tracks as inputs). Real Live 12.4.5 (checked read-only on the running Live) offers:

* MIDI track inputs:  "All Ins", "Computer Keyboard", <other MIDI tracks> (category track,
  ``attached_object`` = the track), "No Input".
* Audio track inputs: "Ext. In", "Resampling", <other audio tracks>, <return tracks>,
  "Main" (category master), "No Input".
* Outputs of audio tracks / instrument MIDI tracks: "Ext. Out", "Main", <other audio
  tracks>, "Sends Only"; outputs of MIDI tracks without an instrument: <other MIDI tracks>,
  "No Output". Return tracks: "Ext. Out", "Main".
* Channel lists depend on the chosen type: a track input offers "Pre FX", "Post FX",
  "Post Mixer"; a track output offers "Track In"; "Main" output has one channel "".

``install(Live)`` patches ``Live.Track.Track`` (class level) and returns an ``uninstall()``
callable that restores the original properties — always call it (use the ``routing_ext``
fixture pattern in ``tests/test_routing.py``) so other test modules see the shared stub.

``add_compressor(track)`` adds a device that mirrors ``Live.CompressorDevice.CompressorDevice``
(side-chain ``available_input_routing_types`` / ``input_routing_type`` / channels) plus an
"S/C On" parameter.
"""

_PATCHED = ("available_input_routing_types", "available_input_routing_channels",
            "available_output_routing_types", "available_output_routing_channels",
            "input_routing_type", "input_routing_channel",
            "output_routing_type", "output_routing_channel")


def _song(track):
    return getattr(track, "_canonical_parent", None)


def install(Live):
    """Patch ``Live.Track.Track`` routing; returns ``uninstall()``."""
    Track = Live.Track.Track
    RoutingType = Live.Track.RoutingType
    RoutingChannel = Live.Track.RoutingChannel
    Category = Live.Track.RoutingTypeCategory
    Layout = Live.Track.RoutingChannelLayout
    originals = dict((name, Track.__dict__[name]) for name in _PATCHED)
    cache = {}

    def rtype(name, category, attached=None):
        key = (name, int(category), id(attached))
        if key not in cache:
            cache[key] = RoutingType(name, category, attached)
        return cache[key]

    def is_regular(track):
        return track._kind in ("midi", "audio")

    def input_types(self):
        self._need_input("available_input_routing_types")
        song = _song(self)
        others = [t for t in (song._tracks if song else []) if t is not self and is_regular(t)]
        if self._is_midi:
            result = [rtype("All Ins", Category.invalid), rtype("Computer Keyboard",
                                                                Category.invalid)]
            result += [rtype(t.name, Category.track, t) for t in others if t._is_midi]
        else:
            result = [rtype("Ext. In", Category.external), rtype("Resampling",
                                                                 Category.resampling)]
            result += [rtype(t.name, Category.track, t) for t in others if not t._is_midi]
            result += [rtype(t.name, Category.track, t) for t in (song._return_tracks
                                                                 if song else [])]
            result.append(rtype("Main", Category.master, song._master_track if song else None))
        result.append(rtype("No Input", Category.none))
        return tuple(result)

    def output_types(self):
        self._need_output("available_output_routing_types")
        song = _song(self)
        others = [t for t in (song._tracks if song else []) if t is not self and is_regular(t)]
        if self._kind == "midi" and self.has_midi_output:
            result = [rtype(t.name, Category.track, t) for t in others if t._is_midi]
            result.append(rtype("No Output", Category.none))
            return tuple(result)
        result = [rtype("Ext. Out", Category.external),
                  rtype("Main", Category.master, song._master_track if song else None)]
        if self._kind != "return":
            result += [rtype(t.name, Category.track, t) for t in others if not t._is_midi]
            result.append(rtype("Sends Only", Category.none))
        return tuple(result)

    def input_channels_for(self, kind):
        if kind is None:
            return ()
        name, category = kind.display_name, kind.category
        if category == int(Category.track):
            layout = Layout.midi if self._is_midi else Layout.stereo
            return tuple(RoutingChannel(n, layout) for n in ("Pre FX", "Post FX", "Post Mixer"))
        if name in ("All Ins", "Computer Keyboard"):
            return tuple([RoutingChannel("All Channels", Layout.midi)] +
                         [RoutingChannel("Ch. %d" % i, Layout.midi) for i in range(1, 17)])
        if name == "Ext. In":
            return (RoutingChannel("1/2", Layout.stereo), RoutingChannel("1", Layout.mono),
                    RoutingChannel("2", Layout.mono))
        if name in ("Resampling", "Main"):
            return (RoutingChannel("Post FX", Layout.stereo),)
        return ()

    def output_channels_for(self, kind):
        if kind is None:
            return ()
        if kind.category == int(Category.track):
            return (RoutingChannel("Track In", Layout.stereo),)
        if kind.display_name == "Ext. Out":
            return (RoutingChannel("1/2", Layout.stereo), RoutingChannel("1", Layout.mono))
        return (RoutingChannel("", Layout.stereo),)

    def state(self, which):
        key = "_ext_%s" % which
        if key not in self.__dict__:
            if which == "input_type":
                types = input_types(self)
                value = types[0]
            else:
                types = output_types(self)
                main = [t for t in types if t.display_name == "Main"]
                value = main[0] if main else types[0]
            self.__dict__[key] = value
        return self.__dict__[key]

    def make(which):
        if which in ("input_type", "input_channel"):
            need, types_fn, chans_fn = "_need_input", input_types, input_channels_for
        else:
            need, types_fn, chans_fn = "_need_output", output_types, output_channels_for
        type_key = which.split("_")[0] + "_type"

        def fget(self):
            getattr(self, need)(which)
            kind = state(self, type_key)
            if which.endswith("_type"):
                return kind
            channels = chans_fn(self, kind)
            chosen = self.__dict__.get("_ext_%s" % which)
            if chosen is None or chosen not in channels:
                chosen = channels[0] if channels else None
            return chosen

        def fset(self, value):
            getattr(self, need)(which)
            if which.endswith("_type"):
                if value not in types_fn(self):
                    raise ValueError("%s: pick one of available_%s_routing_types"
                                     % (which, type_key.split("_")[0]))
                self.__dict__["_ext_%s" % which] = value
                self.__dict__.pop("_ext_%s_channel" % type_key.split("_")[0], None)
            else:
                if value not in chans_fn(self, state(self, type_key)):
                    raise ValueError("%s: pick one of the available channels" % which)
                self.__dict__["_ext_%s" % which] = value
            self.notify_listeners("%s_routing_%s" % tuple(which.split("_")))
        return property(fget, fset)

    Track.available_input_routing_types = property(input_types)
    Track.available_output_routing_types = property(output_types)
    Track.available_input_routing_channels = property(
        lambda self: (self._need_input("available_input_routing_channels"),
                      input_channels_for(self, state(self, "input_type")))[1])
    Track.available_output_routing_channels = property(
        lambda self: (self._need_output("available_output_routing_channels"),
                      output_channels_for(self, state(self, "output_type")))[1])
    Track.input_routing_type = make("input_type")
    Track.input_routing_channel = make("input_channel")
    Track.output_routing_type = make("output_type")
    Track.output_routing_channel = make("output_channel")

    def uninstall():
        for name, value in originals.items():
            setattr(Track, name, value)

    return uninstall


def add_compressor(track, name="Compressor"):
    """Add a Compressor-like device with side-chain routing to ``track``.

    Mirrors ``Live.CompressorDevice.CompressorDevice`` (Live 12.4.5 dump): side-chain
    ``available_input_routing_types`` = the other tracks + "No Input"; parameters
    ``Device On``, ``Threshold``, ``Ratio``, ``S/C On`` (the switch name is an assumption,
    see routing.py).
    """
    import Live
    from Live import _model

    RoutingType = Live.Track.RoutingType
    RoutingChannel = Live.Track.RoutingChannel
    Category = Live.Track.RoutingTypeCategory

    class CompressorDevice(_model.Device):
        def __init__(self, host):
            _model.Device.__init__(self, name, "Compressor2",
                                   Live.Device.DeviceType.audio_effect, canonical_parent=host)
            self._sc_type = None
            self._sc_channel = None

        def _choices(self):
            song = getattr(self._canonical_parent, "_canonical_parent", None)
            tracks = list(song._tracks) + list(song._return_tracks) if song else []
            result = [RoutingType(t.name, Category.track, t) for t in tracks
                      if t is not self._canonical_parent]
            return tuple(result + [RoutingType("No Input", Category.none)])

        @property
        def available_input_routing_types(self):
            return self._choices()

        @property
        def available_input_routing_channels(self):
            if self._sc_type is None or self._sc_type.display_name == "No Input":
                return ()
            return tuple(RoutingChannel(n) for n in ("Pre FX", "Post FX", "Post Mixer"))

        @property
        def input_routing_type(self):
            return self._sc_type or self._choices()[-1]

        @input_routing_type.setter
        def input_routing_type(self, value):
            if value not in self._choices():
                raise ValueError("pick one of available_input_routing_types")
            self._sc_type = value
            channels = self.available_input_routing_channels
            self._sc_channel = channels[0] if channels else None

        @property
        def input_routing_channel(self):
            return self._sc_channel

        @input_routing_channel.setter
        def input_routing_channel(self, value):
            if value not in self.available_input_routing_channels:
                raise ValueError("pick one of available_input_routing_channels")
            self._sc_channel = value

    device = CompressorDevice(track)
    device._parameters = list(device._parameters) + [
        _model.DeviceParameter("Threshold", 0.5, canonical_parent=device),
        _model.DeviceParameter("Ratio", 0.3, canonical_parent=device),
        _model.DeviceParameter("S/C On", 0.0, 0.0, 1.0, is_quantized=True,
                               value_items=("Off", "On"), canonical_parent=device),
    ]
    track._devices.append(device)
    return device
