# Live Python API dump — Live 12.4.5

Generated from a running Live by `tests/dump_live_api.py` (introspection of the `Live` module via eval.python).
Ground truth for this Live version. Signatures come from Boost.Python docstrings: `name( (Class)arg1, (type)arg2) -> ret`, where arg1 is self.

## Runtime

```json
{
 "live_version": "12.4.5",
 "python": "3.11.6 (main, Jul  6 2026, 02:23:45) [Clang 17.0.0 (clang-1700.0.13.5)]",
 "platform": "macOS-14.4.1-arm64-arm-64bit",
 "frameworks": {
  "_Framework": true,
  "_Framework.ControlSurface": true,
  "ableton": true,
  "ableton.v2": true,
  "ableton.v2.control_surface": true,
  "ableton.v3": true
 },
 "browser_roots": {
  "audio_effects": {
   "name": "Audio Effects",
   "uri": "query:AudioFx",
   "children": [
    "Align Delay",
    "Amp",
    "Audio Effect Rack",
    "Auto Filter",
    "Auto Pan-Tremolo",
    "Auto Shift",
    "Beat Repeat",
    "Cabinet",
    "Channel EQ",
    "Chorus-Ensemble",
    "Compressor",
    "Corpus",
    "Delay",
    "Drum Buss",
    "Dynamic Tube",
    "Echo",
    "Envelope Follower",
    "EQ Eight",
    "EQ Three",
    "Erosion",
    "External Audio Effect",
    "Filter Delay",
    "Gate",
    "Glue Compressor",
    "Grain Delay",
    "Hybrid Reverb",
    "LFO",
    "Limiter",
    "Looper",
    "Multiband Dynamics",
    "Overdrive",
    "Pedal",
    "Phaser-Flanger",
    "Redux",
    "Resonators",
    "Reverb",
    "Roar",
    "Saturator",
    "Shaper",
    "Shifter"
   ]
  },
  "clips": {
   "name": "Clips",
   "uri": "query:Clips",
   "children": [
    "505 Core Kit Intro Pattern 111 bpm.alc",
    "606 Core Kit Broken 140 bpm.alc",
    "606 Core Kit Hip Hop 104 bpm.alc",
    "707 Core Kit Halftime 117 bpm.alc",
    "707 Core Kit Pop 118 bpm.alc",
    "707 Core Kit RnB 90 bpm.alc",
    "808 Core Kit Electro 127 bpm.alc",
    "808 Core Kit House 130 bpm.alc",
    "808 Core Kit RnB 73 bpm.alc",
    "808 Core Kit Straight 130 bpm.alc",
    "909 Core Kit Disco 126 bpm.alc",
    "909 Core Kit House 127 bpm.alc",
    "909 Core Kit Off Hat 125 bpm.alc",
    "909 Core Kit Off Hat 126 bpm.alc",
    "Acoustic Perc Pattern 117 bpm .alc",
    "Ambivalent Ambience E Minor 133 bpm.alc",
    "Arp Bells C Minor 121 bpm.alc",
    "Arp Both Ways G Minor 122 bpm.alc",
    "Arp Build Up C Minor 130 bpm.alc",
    "Arp Dark G Minor 126 bpm.alc",
    "Arp Glissando C Minor 120 bpm.alc",
    "Arp RnB D Minor 90 bpm.alc",
    "Arp Tension C Minor 124 bpm.alc",
    "Arp Worn Out C Major 108 bpm.alc",
    "Backbeat Busy 123 bpm.alc",
    "Backbeat Clean 120 bpm.alc",
    "Backbeat Funky Swing 104 bpm.alc",
    "Backbeat Lazy 83 bpm.alc",
    "Backbeat Slight Swing 120 bpm.alc",
    "Backbeat Slow 72 bpm.alc",
    "Backbeat Slow 74 bpm.alc",
    "Backbeat Snap 106 bpm.alc",
    "Backbeat Support 130 bpm.alc",
    "Backbeat Swing 110 bpm.alc",
    "Backbeat Walking Halftime 80 bpm.alc",
    "Break Chill 64 bpm.alc",
    "Break Pattern 140 bpm.alc",
    "Broken Halftime Beat 125 bpm.alc",
    "C78 Core Kit Funk Retro 126 bpm.alc",
    "C78 Core Kit Percussion 116 bpm.alc"
   ]
  },
  "current_project": {
   "name": "Current Project",
   "uri": "query:CurrentProject",
   "children": []
  },
  "drums": {
   "name": "Drums",
   "uri": "query:Drums",
   "children": [
    "Drum Hits",
    "Drum Rack",
    "505 Core Kit.adg",
    "606 Core Kit.adg",
    "707 Core Kit.adg",
    "808 Core Kit.adg",
    "909 Core Kit.adg",
    "Acuff Kit.adg",
    "AG Techno Kit.adg",
    "Ahlimba Kit.adg",
    "Akustichord Kit.adg",
    "Alert Kit.adg",
    "Atom Kit.adg",
    "Battu Kit.adg",
    "Beard Kit.adg",
    "Beastly Kit.adg",
    "Bimini 828 Kit.adg",
    "Bird Kit.adg",
    "BNYX Boot Kit.adg",
    "Boom Bap Kit.adg",
    "Borja Kit.adg",
    "C78 Core Kit.adg",
    "Cage Kit.adg",
    "Caption Kit.adg",
    "Cardboard Kit.adg",
    "Cashon Kit.adg",
    "Cheetah Kit.adg",
    "Chicago Kit.adg",
    "Chromatone Kit.adg",
    "Citizen Drop Kit.adg",
    "Clint West Kit.adg",
    "Clockwork Kit.adg",
    "Coma Kit.adg",
    "Combine Kit.adg",
    "Control Kit.adg",
    "Coral Kit.adg",
    "Cortex Kit.adg",
    "Corvaire Kit.adg",
    "Count In Kit.adg",
    "Crisp Kit.adg"
   ]
  },
  "instruments": {
   "name": "Instruments",
   "uri": "query:Synths",
   "children": [
    "Analog",
    "Collision",
    "Drift",
    "Drum Rack",
    "Drum Sampler",
    "DS Clang",
    "DS Clap",
    "DS Cymbal",
    "DS FM",
    "DS HH",
    "DS Kick",
    "DS Snare",
    "DS Tom",
    "Electric",
    "External Instrument",
    "Impulse",
    "Instrument Rack",
    "Meld",
    "Operator",
    "Sampler",
    "Simpler",
    "Tension",
    "Wavetable"
   ]
  },
  "max_for_live": {
   "name": "Max for Live",
   "uri": "query:M4L",
   "children": [
    "Max Audio Effect",
    "Max Instrument",
    "Max MIDI Effect"
   ]
  },
  "midi_effects": {
   "name": "MIDI Effects",
   "uri": "query:MidiFx",
   "children": [
    "Arpeggiator",
    "CC Control",
    "Chord",
    "Envelope MIDI",
    "Expression Control",
    "MIDI Effect Rack",
    "MIDI Monitor",
    "MPE Control",
    "Note Echo",
    "Note Length",
    "Pitch",
    "Random",
    "Scale",
    "Shaper MIDI",
    "Velocity"
   ]
  },
  "packs": {
   "name": "Packs",
   "uri": "query:LivePacks",
   "children": [
    "Core Library"
   ]
  },
  "plugins": {
   "name": "Plug-Ins",
   "uri": "query:Plugins",
   "children": []
  },
  "samples": {
   "name": "Samples",
   "uri": "query:Samples",
   "children": [
    "80s Beat 90 bpm.wav",
    "80s Drum Machine Wingman 120 bpm.wav",
    "808 Heavy E.wav",
    "808 Huge G.wav",
    "808 Oracle 1.wav",
    "808 Oracle 2.wav",
    "808 Oracle 3.wav",
    "808 Oracle 4.wav",
    "808 Oracle 5.wav",
    "808 Oracle 6.wav",
    "808 Oracle 7.wav",
    "808 Oracle 8.wav",
    "808 Oracle 9.wav",
    "808 Oracle 10.wav",
    "808 Oracle 11.wav",
    "808 Oracle 12.wav",
    "808 Oracle 13.wav",
    "808 Oracle 14.wav",
    "808 Oracle 15.wav",
    "808 Oracle 16.wav",
    "808 Oracle 17.wav",
    "808 Oracle 18.wav",
    "808 Solid D.wav",
    "808 Taka Barely C.wav",
    "808 Time Stopper C.wav",
    "Acid Meltdown - C2.aif",
    "Acid Meltdown - C3.aif",
    "Acid Meltdown - C4.aif",
    "Acid Meltdown - C5.aif",
    "Acid Meltdown - C6.aif",
    "Agogo Atabaques 112 bpm.wav",
    "Agogo Bells 90 bpm.aif",
    "Airhorn Verb.wav",
    "Akeem Groove 120 bpm.wav",
    "Ambient Encounters - C1.aif",
    "Ambient Encounters - C2.aif",
    "Ambient Encounters - C3.aif",
    "Ambient Encounters - C4.aif",
    "Ambient Encounters - C5.aif",
    "Ambient Encounters - C6.aif"
   ]
  },
  "sounds": {
   "name": "Sounds",
   "uri": "query:Sounds",
   "children": [
    "Ambient & Evolving",
    "Bass",
    "Brass",
    "Effects",
    "Guitar & Plucked",
    "Mallets",
    "MPE Sounds",
    "Pad",
    "Percussive",
    "Piano & Keys",
    "Strings",
    "Synth Keys",
    "Synth Lead",
    "Synth Rhythmic",
    "Voices",
    "Winds"
   ]
  },
  "user_library": {
   "name": "User Library",
   "uri": "query:UserLibrary",
   "children": [
    "Clips",
    "Defaults",
    "Grooves",
    "HumLab",
    "MIDI Tools",
    "Presets",
    "Remote Scripts",
    "Samples",
    "Templates",
    "Tunings"
   ]
  }
 },
 "user_folders": [],
 "control_surfaces": [
  "LiveBridge",
  null,
  null,
  null,
  null,
  null,
  null
 ],
 "main_views": [
  "Browser",
  "Arranger",
  "Session",
  "Detail",
  "Detail/Clip",
  "Detail/DeviceChain"
 ],
 "song": {
  "tracks": 4,
  "returns": 2,
  "scenes": 8,
  "tempo": 120.0
 },
 "device_class_names_in_set": [
  "Delay",
  "Reverb"
 ]
}
```

## Live.Application.Application
bases: LomObject
> This class represents the Live application.

properties:
- `_live_ptr` (r) — 
- `average_process_usage` (r) — Reports Live's average CPU load.
- `browser` (r) — Returns an interface to the browser.
- `canonical_parent` (r) — Returns the canonical parent of the application.
- `control_surfaces` (r) — Const access to a list of the control surfaces selected in preferences, in the same order. The list contains None if no control surface is active at that index.
- `current_dialog_button_count` (r) — Number of buttons on the current dialog.
- `current_dialog_message` (r) — Text of the last dialog that appeared; Empty if all dialogs just disappeared.
- `number_of_push_apps_running` (r) — Returns the number of connected Push apps.
- `open_dialog_count` (r) — The number of open dialogs in Live. 0 if not dialog is open.
- `peak_process_usage` (r) — Reports Live's peak CPU load.
- `unavailable_features` (r) — List of features that are unavailable due to limitations of the current Live edition.
- `view` (r) — Returns the applications view component.

methods:
- This class represents the view aspects of the Live application.
- get_bugfix_version( (Application)arg1) -> int : Returns an integer representing the bugfix version of Live.
- get_build_id( (Application)arg1) -> str : Returns a string identifying the build.
- get_document( (Application)arg1) -> Song : Returns the current Live Set.
- get_major_version( (Application)arg1) -> int : Returns an integer representing the major version of Live.
- get_minor_version( (Application)arg1) -> int : Returns an integer representing the minor version of Live.
- get_variant( (Application)arg1) -> str : Returns one of the strings in Live.Application.Variants.
- get_version_string( (Application)arg1) -> str : Returns the full version string of Live.
- has_option( (Application)arg1, (object)arg2) -> bool : Returns True if the given entry exists in Options.txt, False otherwise.
- press_current_dialog_button( (Application)arg1, (int)arg2) -> None : Press a button, by index, on the current message box.
- show_message( (Application)arg1, (Text)text [, (int)buttons=Application.MessageButtons.OK_BUTTON [, (bool)enable_markup=False [, (bool)show_success_icon=False]]]) -> int : Shows a message box, returning the position of the pressed button.
- show_on_the_fly_message( (Application)arg1, (str)message [, (int)buttons=Application.MessageButtons.OK_BUTTON [, (bool)enable_markup=False [, (bool)show_success_icon=False [, (int)push_dialog_type=Application.PushDialogType.MESSAGE_BOX]]]]) -> int : Same as show_message, but for when there is no predefined Text object.

## Live.Application.ControlDescription
bases: instance
> Describes a control present in a control surface proxy

properties:
- `id` (r) — 
- `name` (r) — 

## Live.Application.ControlDescriptionVector
bases: instance
> A container for returning control descriptions.

methods:
- append( (ControlDescriptionVector)arg1, (object)arg2) -> None :
- extend( (ControlDescriptionVector)arg1, (object)arg2) -> None :

## Live.Application.ControlSurfaceProxy
bases: instance
> Represents a control surface running in a different process. For use by M4L

properties:
- `control_descriptions` (r) — 
- `pad_layout` (r) — The layout of pads on Push.
- `type_name` (r) — 

methods:
- enable_receive_midi( (ControlSurfaceProxy)arg1, (bool)arg2) -> None :
- fetch_received_midi_messages( (ControlSurfaceProxy)arg1) -> tuple :
- fetch_received_values( (ControlSurfaceProxy)arg1) -> tuple :
- grab_control( (ControlSurfaceProxy)arg1, (int)arg2) -> None :
- release_control( (ControlSurfaceProxy)arg1, (int)arg2) -> None :
- send_midi( (ControlSurfaceProxy)arg1, (tuple)arg2) -> None :
- send_value( (ControlSurfaceProxy)arg1, (tuple)arg2) -> None :
- subscribe_to_control( (ControlSurfaceProxy)arg1, (int)arg2) -> None :
- unsubscribe_from_control( (ControlSurfaceProxy)arg1, (int)arg2) -> None :

## Live.Application.MessageButtons
bases: enum
> Specifies the characteristics of the message box, e.g. which buttons to show.
enum: OK_BUTTON=0, OK_NEW_SET_BUTTON=1, OK_RETRY_BUTTON=2, SAVE_DONT_SAVE_BUTTON=3, OK_ACCOUNT_BUTTON=4, OK_PURCHASE_BUTTON=5

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.Application.PushDialogType
bases: enum
> Specifies the dialog type for Push.
enum: MESSAGE_BOX=0, OUT_OF_UNLOCKS_DIALOG=5, RENT_TO_OWN_LICENSE_EXPIRED_DIALOG=7

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.Application.UnavailableFeature
bases: enum
enum: note_velocity_ranges_and_probabilities=0

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.Application.UnavailableFeatureVector
bases: instance
> A container for returning unavailable features.

methods:
- append( (UnavailableFeatureVector)arg1, (object)arg2) -> None :
- extend( (UnavailableFeatureVector)arg1, (object)arg2) -> None :

## Live.Application.Variants
bases: instance
> Holds strings representing what type of Live is running.

## Live.Base.FloatVector
bases: instance
> A simple container for returning floats from Live.

methods:
- append( (FloatVector)arg1, (object)arg2) -> None :
- extend( (FloatVector)arg1, (object)arg2) -> None :

## Live.Base.IntU64Vector
bases: instance
> A simple container for returning unsigned long integers from Live.

methods:
- append( (IntU64Vector)arg1, (object)arg2) -> None :
- extend( (IntU64Vector)arg1, (object)arg2) -> None :

## Live.Base.IntVector
bases: instance
> A simple container for returning integers from Live.

methods:
- append( (IntVector)arg1, (object)arg2) -> None :
- extend( (IntVector)arg1, (object)arg2) -> None :

## Live.Base.LimitationError
bases: Exception

properties:
- `args` (r) — 

methods:
- Exception.add_note(note) -- add a note to the exception
- Exception.with_traceback(tb) -- set self.__traceback__ to tb and return self.

## Live.Base.ObjectVector
bases: instance
> A simple read only container for returning python objects.

methods:
- append( (ObjectVector)arg1, (object)arg2) -> None :
- extend( (ObjectVector)arg1, (object)arg2) -> None :

## Live.Base.StringVector
bases: instance
> A simple container for returning strings from Live.

methods:
- append( (StringVector)arg1, (object)arg2) -> None :
- extend( (StringVector)arg1, (object)arg2) -> None :

## Live.Base.Text
bases: instance
> A translatable, immutable string.

properties:
- `text` (r) — 

## Live.Base.Timer
bases: instance
> A timer that will trigger a callback after a certain inverval. The timer can be repeated and will trigger the callback every interval. Errors in the callback will stop the timer.

properties:
- `running` (r) — 

methods:
- restart( (Timer)arg1) -> None :
- start( (Timer)arg1) -> None :
- stop( (Timer)arg1) -> None :

## Live.Base.Vector
bases: instance
> A simple read only container for returning objects from Live.

methods:
- append( (Vector)arg1, (object)arg2) -> None :
- extend( (Vector)arg1, (object)arg2) -> None :

## Live.Browser.Browser
bases: LomObject
> This class represents the live browser data base.

properties:
- `_live_ptr` (r) — 
- `audio_effects` (r) — Returns a browser item with access to all the Audio Effects content.
- `clips` (r) — Returns a browser item with access to all the Clips content.
- `colors` (r) — Returns a list of browser items containing the configured colors.
- `current_project` (r) — Returns a browser item with access to all the Current Project content.
- `drums` (r) — Returns a browser item with access to all the Drums content.
- `filter_type` (rw) — Bang triggered when the hotswap target has changed.
- `hotswap_target` (rw) — Bang triggered when the hotswap target has changed.
- `instruments` (r) — Returns a browser item with access to all the Instruments content.
- `legacy_libraries` (r) — Returns a list of browser items containing the installed legacy libraries. The list is always empty as legacy library handling has been removed.
- `max_for_live` (r) — Returns a browser item with access to all the Max For Live content.
- `midi_effects` (r) — Returns a browser item with access to all the Midi Effects content.
- `packs` (r) — Returns a browser item with access to all the Packs content.
- `plugins` (r) — Returns a browser item with access to all the Plugins content.
- `samples` (r) — Returns a browser item with access to all the Samples content.
- `sounds` (r) — Returns a browser item with access to all the Sounds content.
- `user_folders` (r) — Returns a list of browser items containing all the user folders.
- `user_library` (r) — Returns a browser item with access to all the User Library content.

methods:
- load_item( (Browser)arg1, (BrowserItem)arg2) -> None : Loads the provided browser item.
- preview_item( (Browser)arg1, (BrowserItem)arg2) -> None : Previews the provided browser item.
- relation_to_hotswap_target( (Browser)arg1, (BrowserItem)arg2) -> Relation : Returns the relation between the given browser item and the current hotswap target
- stop_preview( (Browser)arg1) -> None : Stop the current preview.

## Live.Browser.BrowserItem
bases: instance
> This class represents an item of the browser hierarchy.

properties:
- `children` (r) — Const access to the descendants of this browser item.
- `is_device` (r) — Indicates if the browser item represents a device.
- `is_folder` (r) — Indicates if the browser item represents folder.
- `is_loadable` (r) — True if item can be loaded via the Browser's 'load_item' method.
- `is_selected` (r) — True if the item is ancestor of or the actual selection.
- `iter_children` (r) — Const iterable access to the descendants of this browser item.
- `name` (r) — Const access to the canonical display name of this browser item.
- `source` (r) — Specifies where does item come from -- i.e. Live pack, user library...
- `uri` (r) — The uri describes a unique identifier for a browser item.

## Live.Browser.BrowserItemIterator
bases: instance
> This class iterates over children of another BrowserItem.

## Live.Browser.BrowserItemVector
bases: instance
> A container for returning browser items from Live.

methods:
- append( (BrowserItemVector)arg1, (object)arg2) -> None :
- extend( (BrowserItemVector)arg1, (object)arg2) -> None :

## Live.Browser.FilterType
bases: enum
enum: disabled=-1, hotswap_off=0, instrument_hotswap=1, audio_effect_hotswap=2, midi_effect_hotswap=3, drum_pad_hotswap=4, midi_track_devices=5, samples=6, count=7

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.Browser.Relation
bases: enum
enum: ancestor=0, equal=1, descendant=2, none=3

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.CcControlDevice.CcControlDevice
bases: Device
> This class represents a CcControl device.

properties:
- `_live_ptr` (r) — 
- `can_compare_ab` (r) — Returns true if the Device has the capability to AB compare.
- `can_have_chains` (r) — Returns true if the device is a rack.
- `can_have_drum_pads` (r) — Returns true if the device is a drum rack.
- `canonical_parent` (r) — Get the canonical parent of the Device.
- `class_display_name` (r) — Return const access to the name of the device's class name as displayed in Live's browser and device chain
- `class_name` (r) — Return const access to the name of the device's class.
- `custom_bool_target` (rw) — Return the custom bool target
- `custom_bool_target_list` (r) — Return the custom bool target list
- `custom_float_target_0` (rw) — Return the custom float target 0
- `custom_float_target_0_list` (r) — Return the custom float target 0 list
- `custom_float_target_1` (rw) — Return the custom float target 1
- `custom_float_target_10` (rw) — Return the custom float target 10
- `custom_float_target_10_list` (r) — Return the custom float target 10 list
- `custom_float_target_11` (rw) — Return the custom float target 11
- `custom_float_target_11_list` (r) — Return the custom float target 11 list
- `custom_float_target_1_list` (r) — Return the custom float target 1 list
- `custom_float_target_2` (rw) — Return the custom float target 2
- `custom_float_target_2_list` (r) — Return the custom float target 2 list
- `custom_float_target_3` (rw) — Return the custom float target 3
- `custom_float_target_3_list` (r) — Return the custom float target 3 list
- `custom_float_target_4` (rw) — Return the custom float target 4
- `custom_float_target_4_list` (r) — Return the custom float target 4 list
- `custom_float_target_5` (rw) — Return the custom float target 5
- `custom_float_target_5_list` (r) — Return the custom float target 5 list
- `custom_float_target_6` (rw) — Return the custom float target 6
- `custom_float_target_6_list` (r) — Return the custom float target 6 list
- `custom_float_target_7` (rw) — Return the custom float target 7
- `custom_float_target_7_list` (r) — Return the custom float target 7 list
- `custom_float_target_8` (rw) — Return the custom float target 8
- `custom_float_target_8_list` (r) — Return the custom float target 8 list
- `custom_float_target_9` (rw) — Return the custom float target 9
- `custom_float_target_9_list` (r) — Return the custom float target 9 list
- `is_active` (r) — Return const access to whether this device is active. This will be false bothwhen the device is off and when it's inside a rack device which is off.
- `is_using_compare_preset_b` (rw) — Returns whether the Device has loaded the preset in compare slot B. Only relevant if can_compare_ab, otherwise errors.
- `latency_in_ms` (r) — Returns the latency of the device in ms.
- `latency_in_samples` (r) — Returns the latency of the device in samples.
- `name` (rw) — Return access to the name of the device.
- `parameters` (r) — Const access to the list of available automatable parameters for this device.
- `type` (r) — Return the type of the device.
- `view` (r) — Representing the view aspects of a device.

methods:
- Representing the view aspects of a device.
- resend( (CcControlDevice)self) -> None : Resend all CC values.
- save_preset_to_compare_ab_slot( (Device)arg1) -> None : Saves the current state of the device to the compare AB slot. Only relevant if can_compare_ab, otherwise throws.
- store_chosen_bank( (Device)arg1, (int)arg2, (int)arg3) -> None : Set the selected bank in the device for persistency.

## Live.Chain.Chain
bases: DeviceContainer
> This class represents a group device chain in Live.

properties:
- `_live_ptr` (r) — 
- `canonical_parent` (r) — Get the canonical parent of the chain.
- `color` (rw) — Access the color index of the Chain.
- `color_index` (rw) — Access the color index of the Chain.
- `devices` (r) — Return const access to all available Devices that are present in the chains
- `has_audio_input` (r) — return True, if this Chain can be feed with an Audio signal. This is true for all Audio Chains.
- `has_audio_output` (r) — return True, if this Chain sends out an Audio signal. This is true for all Audio Chains, and MIDI chains with an Instrument.
- `has_midi_input` (r) — return True, if this Chain can be feed with an Audio signal. This is true for all MIDI Chains.
- `has_midi_output` (r) — return True, if this Chain sends out MIDI events. This is true for all MIDI Chains with no Instruments.
- `is_auto_colored` (rw) — Get/set access to the auto color flag of the Chain. If True, the Chain will always have the same color as the containing Track or Chain.
- `mixer_device` (r) — Return access to the mixer device that holds the chain's mixer parameters: the Volume, Pan, and Sendamounts.
- `mute` (rw) — Mute/unmute the chain.
- `muted_via_solo` (r) — Return const access to whether this chain is muted due to some other chain being soloed.
- `name` (rw) — Read/write access to the name of the Chain, as visible in the track header.
- `solo` (rw) — Get/Set the solo status of the chain. Note that this will not disable the solo state of any other Chain in the same rack. If you want exclusive solo,  you have to disable the solo state of the other Chains manually.

methods:
- delete_device( (Chain)arg1, (int)arg2) -> None : Remove a device identified by its index from the chain. Throws runtime error if bad index.
- duplicate_device( (Chain)arg1, (int)arg2) -> None : Duplicate the device at the given index in the chain.
- insert_device( (Chain)arg1, (str)DeviceName [, (int)DeviceIndex=-1]) -> LomObject : Add a device at a given index in the chain. At end if -1.

## Live.ChainMixerDevice.ChainMixerDevice
bases: LomObject
> This class represents a Chain's Mixer Device in Live, which gives you access to the Volume, Panning, and Send properties of a Chain.

properties:
- `_live_ptr` (r) — 
- `canonical_parent` (r) — Get the canonical parent of the mixer device.
- `chain_activator` (r) — Const access to the Chain's Activator Device Parameter.
- `panning` (r) — Const access to the Chain's Panning Device Parameter.
- `sends` (r) — Const access to the Chain's list of Send Amount Device Parameters.
- `volume` (r) — Const access to the Chain's Volume Device Parameter.

methods:

## Live.Clip.Clip
bases: LomObject
> This class represents a Clip in Live. It can be either an Audio Clip or a MIDI Clip, in an Arrangement or the Session, depending on the Track (Slot) it lives in.

properties:
- `_live_ptr` (r) — 
- `automation_envelopes` (r) — Const access to a list of all automation envelopes for this clip.
- `available_warp_modes` (r) — Available for AudioClips only. Get/Set the available warp modes, that can be used.
- `canonical_parent` (r) — Get the canonical parent of the Clip.
- `color` (rw) — Get/set access to the color of the Clip (RGB).
- `color_index` (rw) — Get/set access to the color index of the Clip.
- `end_marker` (rw) — Get/Set the Clips end marker pos in beats/seconds (unit depends on warping).
- `end_time` (r) — Get the clip's end time.
- `file_path` (r) — Get the path of the file represented by the Audio Clip.
- `gain` (rw) — Available for AudioClips only. Read/write access to the gain setting of the Audio Clip
- `gain_display_string` (r) — Return a string with the gain as dB value
- `groove` (rw) — Get the groove associated with this clip.
- `has_envelopes` (r) — Will notify if the clip gets his first envelope or the last envelope is removed.
- `has_groove` (r) — Returns true if a groove is associated with this clip.
- `is_arrangement_clip` (r) — return true if this Clip is an Arrangement Clip. A Clip can be either a Session or Arrangement Clip.
- `is_audio_clip` (r) — Return true if this Clip is an Audio Clip. A Clip can be either an Audioclip or a MIDI Clip.
- `is_midi_clip` (r) — return true if this Clip is a MIDI Clip. A Clip can be either an Audioclip or a MIDI Clip.
- `is_overdubbing` (r) — returns true if the Clip is recording overdubs
- `is_playing` (rw) — Get/Set if this Clip is currently playing. If the Clips trigger mode is set to a quantization value, the Clip will not start playing immediately. If you need to know wether the Clip was triggered, use the is_triggered pr
- `is_recording` (r) — returns true if the Clip was triggered to record or is recording.
- `is_session_clip` (r) — return true if this Clip is a Session Clip. A Clip can be either a Session or Arrangement Clip.
- `is_take_lane_clip` (r) — return true if this Clip is a Take Lane Clip. A Take Lane Clip is also always an Arrangement Clip.
- `is_triggered` (r) — returns true if the Clip was triggered or is playing.
- `launch_mode` (rw) — Get/Set access to the launch mode setting of the Clip.
- `launch_quantization` (rw) — Get/Set access to the launch quantization setting of the Clip.
- `legato` (rw) — Get/Set access to the legato setting of the Clip
- `length` (r) — Get to the Clips length in beats/seconds (unit depends on warping).
- `loop_end` (rw) — Get/Set the loop end pos of this Clip in beats/seconds (unit depends on warping).
- `loop_start` (rw) — Get/Set the Clips loopstart pos in beats/seconds (unit depends on warping).
- `looping` (rw) — Get/Set the Clips 'loop is enabled' flag .Only Warped Audio Clips or MIDI Clip can be looped.
- `muted` (rw) — Read/write access to the mute state of the Clip.
- `name` (rw) — Read/write access to the name of the Clip.
- `pitch_coarse` (rw) — Available for AudioClips only. Read/write access to the pitch (in halftones) setting of the Audio Clip, ranging from -48 to 48
- `pitch_fine` (rw) — Available for AudioClips only. Read/write access to the pitch fine setting of the Audio Clip, ranging from -500 to 500
- `playing_position` (r) — Constant access to the current playing position of the clip. The returned value is the position in beats for midi and warped audio clips, or in seconds for unwarped audio clips. Stopped clips will return 0.
- `position` (rw) — Get/Set the loop position of this Clip in beats/seconds (unit depends on warping).
- `ram_mode` (rw) — Available for AudioClips only. Read/write access to the Ram mode setting of the Audio Clip
- `sample_length` (r) — Available for AudioClips only. Get the sample length in sample time or -1 if there is no sample available.
- `sample_rate` (r) — Available for AudioClips only. Read-only access to the Clip's sampling rate.
- `signature_denominator` (rw) — Get/Set access to the global signature denominator of the Clip.
- `signature_numerator` (rw) — Get/Set access to the global signature numerator of the Clip.
- `start_marker` (rw) — Get/Set the Clips start marker pos in beats/seconds (unit depends on warping).
- `start_time` (r) — Get the clip's start time offset. For Session View clips, this is the time the clip was started. For Arrangement View clips, this is the offset within the arrangement.
- `velocity_amount` (rw) — Get/Set access to the velocity to volume amount of the Clip.
- `view` (r) — Get the view of the Clip.
- `warp_markers` (r) — Available for AudioClips only. Get the warp markers for this audio clip.
- `warp_mode` (rw) — Available for AudioClips only. Get/Set the warp mode for this audio clip.
- `warping` (rw) — Available for AudioClips only. Get/Set if this Clip is timestreched.
- `will_record_on_start` (r) — returns true if the Clip will record on being started.

methods:
- Representing the view aspects of a Clip.
- add_new_notes( (Clip)arg1, (object)arg2) -> IntU64Vector : Expects a Python iterable holding a number of Live.Clip.MidiNoteSpecification objects. The objects will be used to construct new notes in the clip.
- add_warp_marker( (Clip)self, (object)warp_marker) -> None : Available for AudioClips only. Adds the specified warp marker, if possible.
- apply_note_modifications( (Clip)arg1, (MidiNoteVector)arg2) -> None : Expects a list of notes as returned from get_notes_extended. The content of the list will be used to modify existing notes in the clip, based on matching note IDs. This function should be used when modifying existing notes, e.g. changing the velocity or start time. The function ensures that per-note events attached to the modified notes are preserv
- automation_envelope( (Clip)arg1, (DeviceParameter)arg2) -> Envelope : Return the envelope for the given parameter.Returns None if the envelope doesn't exist.Returns None for Arrangement clips.Returns None for parameters from a different track.
- beat_to_sample_time( (Clip)self, (float)beat_time) -> float : Available for AudioClips only. Converts the given beat time to sample time. Raises an error if the sample is not warped.
- clear_all_envelopes( (Clip)arg1) -> None : Clears all envelopes for this clip.
- clear_envelope( (Clip)arg1, (DeviceParameter)arg2) -> None : Clears the envelope of this clips given parameter.
- create_automation_envelope( (Clip)arg1, (DeviceParameter)arg2) -> Envelope : Creates an envelope for a given parameter and returns it.This should only be used if the envelope doesn't exist.Raises an error if the envelope can't be created.
- crop( (Clip)arg1) -> None : Crops the clip. The region that is cropped depends on whether the clip is looped or not. If looped, the region outside of the loop is removed. If not looped, the region outside the start and end markers is removed.
- deselect_all_notes( (Clip)arg1) -> None : De-selects all notes present in the clip.
- duplicate_loop( (Clip)arg1) -> None : Make the loop two times longer and duplicates notes and envelopes. Duplicates the clip start/end range if the clip is not looped.
- duplicate_notes_by_id( (Clip)self, (object)note_ids [, (object)destination_time=None [, (int)transposition_amount=0]]) -> IntU64Vector : Duplicate all notes matching the given note IDs. If the optional destination_time is not provided, new notes will be inserted after the last selected note. This behavior can be observed when duplicating notes in the Live GUI. If the transposition_amount is specified, the notes in th
- duplicate_region( (Clip)self, (float)region_start, (float)region_length, (float)destination_time [, (int)pitch=-1 [, (int)transposition_amount=0]]) -> None : Duplicate the notes in the specified region to the destination_time. Only notes of the specified pitch are duplicated or all if pitch is -1. If the transposition_amount is not 0, the notes in the region will be transposed by the transpose_amount of semitones.Rai
- fire( (Clip)arg1) -> None : (Re)Start playing this Clip.
- get_all_notes_extended( (Clip)arg1) -> MidiNoteVector : Returns a list of all MIDI notes from the clip, regardless of their position relative to the start and end markers/loop start and loop end. Each note is represented by a Live.Clip.MidiNote object. The returned list can be modified freely, but modifications will not be reflected in the MIDI clip until apply_note_modifications is called.
- get_notes( (Clip)self, (float)from_time, (int)from_pitch, (float)time_span, (int)pitch_span) -> tuple : Returns a tuple of tuples where each inner tuple represents a note starting in the given pitch- and time range. The inner tuple contains pitch, time, duration, velocity, and mute state.
- get_notes_by_id( (Clip)arg1, (object)note_ids) -> MidiNoteVector : Return a list of MIDI notes matching the given note IDs.
- get_notes_extended( (Clip)arg1, (int)from_pitch, (int)pitch_span, (float)from_time, (float)time_span) -> MidiNoteVector : Returns a list of MIDI notes from the given pitch and time range. Each note is represented by a Live.Clip.MidiNote object. The returned list can be modified freely, but modifications will not be reflected in the MIDI clip until apply_note_modifications is called.
- get_selected_notes( (Clip)arg1) -> tuple : Returns a tuple of tuples where each inner tuple represents a selected note. The inner tuple contains pitch, time, duration, velocity, and mute state.
- get_selected_notes_extended( (Clip)arg1) -> MidiNoteVector : Returns a list of all MIDI notes from the clip that are currently selected. Each note is represented by a Live.Clip.MidiNote object. The returned list can be modified freely, but modifications will not be reflected in the MIDI clip until apply_note_modifications is called.
- move_playing_pos( (Clip)arg1, (float)arg2) -> None : Jump forward or backward by the specified relative amount in beats. Will do nothing, if the Clip is not playing.
- move_warp_marker( (Clip)self, (float)marker_beat_time, (float)beat_time_distance) -> None : Available for AudioClips only. Moves the specified warp marker by the specified beat time amount, if possible.
- note_number_to_name( (Clip)self, (int)midi_pitch) -> str : Return a human-readable name for the given MIDI note number. Takes into account the scale and tonal spelling settings of the clip, as well as the current tuning system (if any)
- quantize( (Clip)arg1, (int)arg2, (float)arg3) -> None : Quantize all notes in a clip or align warp markers.
- quantize_pitch( (Clip)arg1, (int)arg2, (int)arg3, (float)arg4) -> None : Quantize all the notes of a given pitch. Raises an error on audio clips.
- remove_notes( (Clip)arg1, (float)arg2, (int)arg3, (float)arg4, (int)arg5) -> None : Delete all notes starting in the given pitch- and time range.
- remove_notes_by_id( (Clip)arg1, (object)arg2) -> None : Delete all notes matching the given note IDs. This function should NOT be used to implement modification of existing notes (i.e. in combination with add_new_notes), as that leads to loss of per-note events. apply_note_modifications must be used instead for modifying existing notes.
- remove_notes_extended( (Clip)arg1, (int)from_pitch, (int)pitch_span, (float)from_time, (float)time_span) -> None : Delete all notes starting in the given pitch and time range. This function should NOT be used to implement modification of existing notes (i.e. in combination with add_new_notes), as that leads to loss of per-note events. apply_note_modifications must be used instead for modifying existing notes.
- remove_warp_marker( (Clip)self, (float)beat_time) -> None : Available for AudioClips only. Removes the specified warp marker, if possible.
- replace_selected_notes( (Clip)arg1, (tuple)arg2) -> None : Called with a tuple of tuples where each inner tuple represents a note in the same format as returned by get_selected_notes. The notes described that way will then be used to replace the old selection.
- sample_to_beat_time( (Clip)self, (float)sample_time) -> float : Available for AudioClips only. Converts the given sample time to beat time. Raises an error if the sample is not warped.
- scrub( (Clip)self, (float)scrub_position) -> None : Scrubs inside a clip. scrub_position defines the position in beats that the scrub will start from. The scrub will continue until stop_scrub is called. Global quantization applies to the scrub's position and length.
- seconds_to_sample_time( (Clip)self, (float)seconds) -> float : Available for AudioClips only. Converts the given seconds to sample time. Raises an error if the sample is warped.
- select_all_notes( (Clip)arg1) -> None : Selects all notes present in the clip.
- select_notes_by_id( (Clip)arg1, (object)arg2) -> None : Selects all notes matching the given note IDs.
- set_fire_button_state( (Clip)arg1, (bool)arg2) -> None : Set the clip's fire button state directly. Supports all launch modes.
- set_notes( (Clip)arg1, (tuple)arg2) -> None : Called with a tuple of tuples where each inner tuple represents a note in the same format as returned by get_notes. The notes described that way will then be added to the clip.
- stop( (Clip)arg1) -> None : Stop playing this Clip.
- stop_scrub( (Clip)arg1) -> None : Stops the current scrub.

## Live.Clip.ClipLaunchQuantization
bases: enum
enum: q_global=0, q_none=1, q_8_bars=2, q_4_bars=3, q_2_bars=4, q_bar=5, q_half=6, q_half_triplet=7, q_quarter=8, q_quarter_triplet=9, q_eighth=10, q_eighth_triplet=11, q_sixteenth=12, q_sixteenth_triplet=13, q_thirtysecond=14

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.Clip.GridQuantization
bases: enum
enum: no_grid=0, g_8_bars=1, g_4_bars=2, g_2_bars=3, g_bar=4, g_half=5, g_quarter=6, g_eighth=7, g_sixteenth=8, g_thirtysecond=9, count=10

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.Clip.LaunchMode
bases: enum
enum: trigger=0, gate=1, toggle=2, repeat=3

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.Clip.MidiNote
bases: instance
> An object representing a MIDI Note

properties:
- `duration` (rw) — 
- `mute` (rw) — 
- `note_id` (r) — A numerical ID that's unique within the originating clip of the note. Not to be used directly, but important for other API calls, namely apply_note_modifications.
- `pitch` (rw) — 
- `probability` (rw) — 
- `release_velocity` (rw) — 
- `start_time` (rw) — 
- `velocity` (rw) — 
- `velocity_deviation` (rw) — 

## Live.Clip.MidiNoteSpecification
bases: instance
> An object specifying the data for creating a MIDI note. To be used with the  add_new_notes function.

## Live.Clip.MidiNoteVector
bases: instance
> A container for holding MIDI notes from Live.

methods:
- append( (MidiNoteVector)arg1, (object)arg2) -> None :
- extend( (MidiNoteVector)arg1, (object)arg2) -> None :

## Live.Clip.WarpMarker
bases: instance
> This class represents a WarpMarker type.

properties:
- `beat_time` (r) — A WarpMarker's beat time.
- `sample_time` (r) — A WarpMarker's sample time.

## Live.Clip.WarpMarkerVector
bases: instance
> A container for returning warp markers from Live.

methods:
- append( (WarpMarkerVector)arg1, (object)arg2) -> None :
- extend( (WarpMarkerVector)arg1, (object)arg2) -> None :

## Live.Clip.WarpMode
bases: enum
enum: beats=0, complex=4, complex_pro=6, repitch=3, rex=5, texture=2, tones=1, count=7

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.ClipSlot.ClipSlot
bases: LomObject
> This class represents an entry in Lives Session view matrix.

properties:
- `_live_ptr` (r) — 
- `canonical_parent` (r) — Get the canonical parent of the ClipSlot.
- `clip` (r) — Returns the Clip which this clipslots currently owns. Might be None.
- `color` (r) — Returns the canonical color for the clip slot or None if it does not exist.
- `color_index` (r) — Returns the canonical color index for the clip slot or None if it does not exist.
- `controls_other_clips` (r) — Returns true if firing this slot will fire clips in other slots. Can only be true for slots in group tracks.
- `has_clip` (r) — Returns true if this Clipslot owns a Clip.
- `has_stop_button` (rw) — Get/Set if this Clip has a stop button, which will, if fired, stop any other Clip that is currently playing the Track we do belong to.
- `is_group_slot` (r) — Returns whether this clip slot is a group track slot (group slot).
- `is_playing` (r) — Returns whether the clip associated with the slot is playing.
- `is_recording` (r) — Returns whether the clip associated with the slot is recording.
- `is_triggered` (r) — Const access to the triggering state of the clip slot.
- `playing_status` (r) — Const access to the playing state of the clip slot. Can be either stopped, playing, or recording.
- `will_record_on_start` (r) — returns true if the clip slot will record on being fired.

methods:
- create_audio_clip( (ClipSlot)arg1, (object)arg2) -> Clip : Creates an audio clip referencing the file at the given absolute path in the slot. Throws an error when called on non-empty slots or slots in non-audio or frozen tracks, or when the path doesn't point at a valid audio file.
- create_clip( (ClipSlot)arg1, (float)arg2) -> Clip : Creates an empty clip with the given length in the slot. Throws an error when called on non-empty slots or slots in non-MIDI tracks.
- delete_clip( (ClipSlot)arg1) -> None : Removes the clip contained in the slot. Raises an exception if the slot was empty.
- duplicate_clip_to( (ClipSlot)arg1, (ClipSlot)arg2) -> None : Duplicates the slot's clip to the passed in target slot. Overrides the target's clip if it's not empty. Raises an exception if the (source) slot itself is empty, or if source and target have different track types (audio vs. MIDI). Also raises if the source or target slot is in a group track (so called group slot).
- fire( (ClipSlot)arg1) -> None : Fire a Clip if this Clipslot owns one, else trigger the stop button, if we have one.
- set_fire_button_state( (ClipSlot)arg1, (bool)arg2) -> None : Set the clipslot's fire button state directly. Supports all launch modes.
- stop( (ClipSlot)arg1) -> None : Stop playing the contained Clip, if there is a Clip and its currently playing.

## Live.ClipSlot.ClipSlotPlayingState
bases: enum
enum: stopped=0, started=1, recording=2

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.CompressorDevice.CompressorDevice
bases: Device
> This class represents a Compressor device.

properties:
- `_live_ptr` (r) — 
- `available_input_routing_channels` (r) — Return a list of source channels for input routing in the sidechain.
- `available_input_routing_types` (r) — Return a list of source types for input routing in the sidechain.
- `can_compare_ab` (r) — Returns true if the Device has the capability to AB compare.
- `can_have_chains` (r) — Returns true if the device is a rack.
- `can_have_drum_pads` (r) — Returns true if the device is a drum rack.
- `canonical_parent` (r) — Get the canonical parent of the Device.
- `class_display_name` (r) — Return const access to the name of the device's class name as displayed in Live's browser and device chain
- `class_name` (r) — Return const access to the name of the device's class.
- `input_routing_channel` (rw) — Get and set the current source channel for input routing in the sidechain. Raises ValueError if the channel isn't one of the current values in available_input_routing_channels.
- `input_routing_type` (rw) — Get and set the current source type for input routing in the sidechain. Raises ValueError if the type isn't one of the current values in available_input_routing_types.
- `is_active` (r) — Return const access to whether this device is active. This will be false bothwhen the device is off and when it's inside a rack device which is off.
- `is_using_compare_preset_b` (rw) — Returns whether the Device has loaded the preset in compare slot B. Only relevant if can_compare_ab, otherwise errors.
- `latency_in_ms` (r) — Returns the latency of the device in ms.
- `latency_in_samples` (r) — Returns the latency of the device in samples.
- `name` (rw) — Return access to the name of the device.
- `parameters` (r) — Const access to the list of available automatable parameters for this device.
- `type` (r) — Return the type of the device.
- `view` (r) — Representing the view aspects of a device.

methods:
- Representing the view aspects of a device.
- save_preset_to_compare_ab_slot( (Device)arg1) -> None : Saves the current state of the device to the compare AB slot. Only relevant if can_compare_ab, otherwise throws.
- store_chosen_bank( (Device)arg1, (int)arg2, (int)arg3) -> None : Set the selected bank in the device for persistency.

## Live.Conversions.AudioToMidiType
bases: enum
enum: harmony_to_midi=0, melody_to_midi=1, drums_to_midi=2

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.Device.ATimeableValueVector
bases: instance

methods:
- append( (ATimeableValueVector)arg1, (object)arg2) -> None :
- extend( (ATimeableValueVector)arg1, (object)arg2) -> None :

## Live.Device.Device
bases: LomObject
> This class represents a MIDI or Audio DSP-Device in Live.

properties:
- `_live_ptr` (r) — 
- `can_compare_ab` (r) — Returns true if the Device has the capability to AB compare.
- `can_have_chains` (r) — Returns true if the device is a rack.
- `can_have_drum_pads` (r) — Returns true if the device is a drum rack.
- `canonical_parent` (r) — Get the canonical parent of the Device.
- `class_display_name` (r) — Return const access to the name of the device's class name as displayed in Live's browser and device chain
- `class_name` (r) — Return const access to the name of the device's class.
- `is_active` (r) — Return const access to whether this device is active. This will be false bothwhen the device is off and when it's inside a rack device which is off.
- `is_using_compare_preset_b` (rw) — Returns whether the Device has loaded the preset in compare slot B. Only relevant if can_compare_ab, otherwise errors.
- `latency_in_ms` (r) — Returns the latency of the device in ms.
- `latency_in_samples` (r) — Returns the latency of the device in samples.
- `name` (rw) — Return access to the name of the device.
- `parameters` (r) — Const access to the list of available automatable parameters for this device.
- `type` (r) — Return the type of the device.
- `view` (r) — Representing the view aspects of a device.

methods:
- Representing the view aspects of a device.
- save_preset_to_compare_ab_slot( (Device)arg1) -> None : Saves the current state of the device to the compare AB slot. Only relevant if can_compare_ab, otherwise throws.
- store_chosen_bank( (Device)arg1, (int)arg2, (int)arg3) -> None : Set the selected bank in the device for persistency.

## Live.Device.DeviceType
bases: enum
> The type of the device.
enum: undefined=0, instrument=1, audio_effect=2, midi_effect=4

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.DeviceIO.DeviceIO
bases: LomObject
> This class represents a specific input or output bus of a device.

properties:
- `_live_ptr` (r) — 
- `available_routing_channels` (r) — Return a list of channels for this IO endpoint.
- `available_routing_types` (r) — Return a list of available routing types for this IO endpoint.
- `canonical_parent` (r) — Get the canonical parent of the device IO.
- `default_external_routing_channel_is_none` (rw) — Get and set whether the default routing channel for External routing types is none.
- `routing_channel` (rw) — Get and set the current routing channel. Raises ValueError if the channel isn't one of the current values in available_routing_channels.
- `routing_type` (rw) — Get and set the current routing type. Raises ValueError if the type isn't one of the current values in available_routing_types.

methods:

## Live.DeviceParameter.AutomationState
bases: enum
enum: none=0, playing=1, overridden=2

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.DeviceParameter.DeviceParameter
bases: LomObject
> This class represents a (automatable) parameter within a MIDI or Audio DSP-Device.

properties:
- `_live_ptr` (r) — 
- `automation_state` (r) — Returns state of type AutomationState.
- `canonical_parent` (r) — Get the canonical parent of the device parameter.
- `default_value` (r) — Return the default value for this parameter.  A Default value is only available for non-quantized parameter types (see 'is_quantized').
- `display_value` (rw) — Get/Set the current value (as visible in the GUI) this parameter. The value must be inside the min/max properties of this device.
- `is_enabled` (r) — Returns false if the parameter has been macro mapped or disabled by Max.
- `is_quantized` (r) — Returns True, if this value is a boolean or integer like switch. Non quantized values are continues float values.
- `max` (r) — Returns const access to the upper value of the allowed range for this parameter
- `min` (r) — Returns const access to the lower value of the allowed range for this parameter
- `name` (r) — Returns const access the name of this parameter, as visible in Lives automation choosers.
- `original_name` (r) — Returns const access the original name of this parameter, unaffected of any renamings.
- `short_value_items` (r) — Return the list of possible values for this parameter. Like value_items, but prefers short value names if available. Raises an error if 'is_quantized' is False.
- `state` (r) — Returns the state of the parameter: - enabled - the parameter's value can be changed, - irrelevant - the parameter is enabled, but value changes will not take any effect until it gets enabled, - disabled - the parameter'
- `value` (rw) — Get/Set the current internal value of this parameter. The value must be inside the min/max properties of this device.
- `value_items` (r) — Return the list of possible values for this parameter. Raises an error if 'is_quantized' is False.

methods:
- begin_gesture( (DeviceParameter)arg1) -> None : Notify the begin of a modification of the parameter, when a sequence of modifications have to be consider a consistent group -- for Sexample, when recording automation.
- end_gesture( (DeviceParameter)arg1) -> None : Notify the end of a modification of the parameter. See begin_gesture.
- re_enable_automation( (DeviceParameter)arg1) -> None : Reenable automation for this parameter.
- str_for_value( (DeviceParameter)arg1, (float)arg2) -> str : Return a string representation of the given value. To be used for display purposes only. This value can include characters like 'db' or 'hz', depending on the type of the parameter.

## Live.DeviceParameter.ParameterState
bases: enum
enum: enabled=0, irrelevant=1, disabled=2

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.DriftDevice.DriftDevice
bases: Device
> This class represents a Drift device.

properties:
- `_live_ptr` (r) — 
- `can_compare_ab` (r) — Returns true if the Device has the capability to AB compare.
- `can_have_chains` (r) — Returns true if the device is a rack.
- `can_have_drum_pads` (r) — Returns true if the device is a drum rack.
- `canonical_parent` (r) — Get the canonical parent of the Device.
- `class_display_name` (r) — Return const access to the name of the device's class name as displayed in Live's browser and device chain
- `class_name` (r) — Return const access to the name of the device's class.
- `is_active` (r) — Return const access to whether this device is active. This will be false bothwhen the device is off and when it's inside a rack device which is off.
- `is_using_compare_preset_b` (rw) — Returns whether the Device has loaded the preset in compare slot B. Only relevant if can_compare_ab, otherwise errors.
- `latency_in_ms` (r) — Returns the latency of the device in ms.
- `latency_in_samples` (r) — Returns the latency of the device in samples.
- `mod_matrix_filter_source_1_index` (rw) — Return the filter mod source 1 index
- `mod_matrix_filter_source_1_list` (r) — Return the filter mod source 1 list
- `mod_matrix_filter_source_2_index` (rw) — Return the filter mod source 2 index
- `mod_matrix_filter_source_2_list` (r) — Return the filter mod source 2 list
- `mod_matrix_lfo_source_index` (rw) — Return the lfo mod source index
- `mod_matrix_lfo_source_list` (r) — Return the lfo mod source list
- `mod_matrix_pitch_source_1_index` (rw) — Return the pitch mod source 1 index
- `mod_matrix_pitch_source_1_list` (r) — Return the pitch mod source 1 list
- `mod_matrix_pitch_source_2_index` (rw) — Return the pitch mod source 2 index
- `mod_matrix_pitch_source_2_list` (r) — Return the pitch mod source 2 list
- `mod_matrix_shape_source_index` (rw) — Return the shape mod source index
- `mod_matrix_shape_source_list` (r) — Return the shape mod source list
- `mod_matrix_source_1_index` (rw) — Return the custom mod source 1 index
- `mod_matrix_source_1_list` (r) — Return the custom mod source 1 list
- `mod_matrix_source_2_index` (rw) — Return the custom mod source 2 index
- `mod_matrix_source_2_list` (r) — Return the custom mod source 2 list
- `mod_matrix_source_3_index` (rw) — Return the custom mod source 3 index
- `mod_matrix_source_3_list` (r) — Return the custom mod source 3 list
- `mod_matrix_target_1_index` (rw) — Return the custom mod target 1 index
- `mod_matrix_target_1_list` (r) — Return the custom mod target 1 list
- `mod_matrix_target_2_index` (rw) — Return the custom mod target 2 index
- `mod_matrix_target_2_list` (r) — Return the custom mod target 2 list
- `mod_matrix_target_3_index` (rw) — Return the custom mod target 3 index
- `mod_matrix_target_3_list` (r) — Return the custom mod target 3 list
- `name` (rw) — Return access to the name of the device.
- `parameters` (r) — Const access to the list of available automatable parameters for this device.
- `pitch_bend_range` (rw) — Return the Pitch Bend Range
- `type` (r) — Return the type of the device.
- `view` (r) — Representing the view aspects of a device.
- `voice_count_index` (rw) — Return the voice count index
- `voice_count_list` (r) — Return the voice count list
- `voice_mode_index` (rw) — Return the voice mode index
- `voice_mode_list` (r) — Return the voice mode list

methods:
- Representing the view aspects of a device.
- save_preset_to_compare_ab_slot( (Device)arg1) -> None : Saves the current state of the device to the compare AB slot. Only relevant if can_compare_ab, otherwise throws.
- store_chosen_bank( (Device)arg1, (int)arg2, (int)arg3) -> None : Set the selected bank in the device for persistency.

## Live.DrumCellDevice.DrumCellDevice
bases: Device
> This class represents a DrumCell device.

properties:
- `_live_ptr` (r) — 
- `can_compare_ab` (r) — Returns true if the Device has the capability to AB compare.
- `can_have_chains` (r) — Returns true if the device is a rack.
- `can_have_drum_pads` (r) — Returns true if the device is a drum rack.
- `canonical_parent` (r) — Get the canonical parent of the Device.
- `class_display_name` (r) — Return const access to the name of the device's class name as displayed in Live's browser and device chain
- `class_name` (r) — Return const access to the name of the device's class.
- `gain` (rw) — Return the Gain value
- `is_active` (r) — Return const access to whether this device is active. This will be false bothwhen the device is off and when it's inside a rack device which is off.
- `is_using_compare_preset_b` (rw) — Returns whether the Device has loaded the preset in compare slot B. Only relevant if can_compare_ab, otherwise errors.
- `latency_in_ms` (r) — Returns the latency of the device in ms.
- `latency_in_samples` (r) — Returns the latency of the device in samples.
- `name` (rw) — Return access to the name of the device.
- `parameters` (r) — Const access to the list of available automatable parameters for this device.
- `type` (r) — Return the type of the device.
- `view` (r) — Representing the view aspects of a device.

methods:
- Representing the view aspects of a device.
- save_preset_to_compare_ab_slot( (Device)arg1) -> None : Saves the current state of the device to the compare AB slot. Only relevant if can_compare_ab, otherwise throws.
- store_chosen_bank( (Device)arg1, (int)arg2, (int)arg3) -> None : Set the selected bank in the device for persistency.

## Live.DrumChain.DrumChain
bases: Chain
> This class represents a drum group device chain in Live.

properties:
- `_live_ptr` (r) — 
- `canonical_parent` (r) — Get the canonical parent of the chain.
- `choke_group` (rw) — Access to the chain's choke group setting.
- `color` (rw) — Access the color index of the Chain.
- `color_index` (rw) — Access the color index of the Chain.
- `devices` (r) — Return const access to all available Devices that are present in the chains
- `has_audio_input` (r) — return True, if this Chain can be feed with an Audio signal. This is true for all Audio Chains.
- `has_audio_output` (r) — return True, if this Chain sends out an Audio signal. This is true for all Audio Chains, and MIDI chains with an Instrument.
- `has_midi_input` (r) — return True, if this Chain can be feed with an Audio signal. This is true for all MIDI Chains.
- `has_midi_output` (r) — return True, if this Chain sends out MIDI events. This is true for all MIDI Chains with no Instruments.
- `in_note` (rw) — Access to the incoming MIDI note that will trigger this chain.
- `is_auto_colored` (rw) — Get/set access to the auto color flag of the Chain. If True, the Chain will always have the same color as the containing Track or Chain.
- `mixer_device` (r) — Return access to the mixer device that holds the chain's mixer parameters: the Volume, Pan, and Sendamounts.
- `mute` (rw) — Mute/unmute the chain.
- `muted_via_solo` (r) — Return const access to whether this chain is muted due to some other chain being soloed.
- `name` (rw) — Read/write access to the name of the Chain, as visible in the track header.
- `out_note` (rw) — Access to the MIDI note sent to the devices in the chain.
- `solo` (rw) — Get/Set the solo status of the chain. Note that this will not disable the solo state of any other Chain in the same rack. If you want exclusive solo,  you have to disable the solo state of the other Chains manually.

methods:
- delete_device( (Chain)arg1, (int)arg2) -> None : Remove a device identified by its index from the chain. Throws runtime error if bad index.
- duplicate_device( (Chain)arg1, (int)arg2) -> None : Duplicate the device at the given index in the chain.
- insert_device( (Chain)arg1, (str)DeviceName [, (int)DeviceIndex=-1]) -> LomObject : Add a device at a given index in the chain. At end if -1.

## Live.DrumPad.DrumPad
bases: LomObject
> This class represents a drum group device pad in Live.

properties:
- `_live_ptr` (r) — 
- `canonical_parent` (r) — Get the canonical parent of the drum pad.
- `chains` (r) — Return const access to the list of chains in this drum pad.
- `mute` (rw) — Mute/unmute the pad.
- `name` (r) — Return const access to the drum pad's name. It depends on the contained chains.
- `note` (r) — Get the MIDI note of the drum pad.
- `solo` (rw) — Solo/unsolo the pad.

methods:
- delete_all_chains( (DrumPad)arg1) -> None : Deletes all chains associated with a drum pad. This is equivalent to deleting a drum rack pad in Live.

## Live.Envelope.Envelope
bases: LomObject
> This class represents an automation or modulation envelope in Live.

properties:
- `_live_ptr` (r) — 
- `canonical_parent` (r) — Get the canonical parent of the envelope.
- `parameter` (r) — Read-only access to the parameter controlled by the envelope.

methods:
- create_event( (Envelope)arg1, (EnvelopeEvent)arg2) -> None : Creates a new event at the specified time with the given value and, optionally, control coefficients.
- delete_events_in_range( (Envelope)arg1, (float)arg2, (float)arg3) -> None : Deletes the events in the specified time range.
- events_in_range( (Envelope)arg1, (float)arg2, (float)arg3) -> EnvelopeEventVector : Returns the events in the specified time range.
- insert_step( (Envelope)arg1, (float)arg2, (float)arg3, (float)arg4) -> None : Given a start time, a step length and a value, creates a step in the envelope.
- value_at_time( (Envelope)arg1, (float)arg2) -> float : Returns the parameter value at the specified time.

## Live.Envelope.EnvelopeEvent
bases: instance
> This is a class that represents an envelope event.

properties:
- `control_coefficients` (rw) — 
- `time` (rw) — 
- `value` (rw) — 

## Live.Envelope.EnvelopeEventControlCoefficients
bases: instance
> This class represents the control coefficients of an envelope event.

properties:
- `x1` (rw) — 
- `x2` (rw) — 
- `y1` (rw) — 
- `y2` (rw) — 

## Live.Envelope.EnvelopeEventVector
bases: instance
> A container for holding envelope events.

methods:
- append( (EnvelopeEventVector)arg1, (object)arg2) -> None :
- extend( (EnvelopeEventVector)arg1, (object)arg2) -> None :

## Live.Eq8Device.EditMode
bases: enum
enum: a=0, b=1

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.Eq8Device.Eq8Device
bases: Device
> This class represents an Eq8 device.

properties:
- `_live_ptr` (r) — 
- `can_compare_ab` (r) — Returns true if the Device has the capability to AB compare.
- `can_have_chains` (r) — Returns true if the device is a rack.
- `can_have_drum_pads` (r) — Returns true if the device is a drum rack.
- `canonical_parent` (r) — Get the canonical parent of the Device.
- `class_display_name` (r) — Return const access to the name of the device's class name as displayed in Live's browser and device chain
- `class_name` (r) — Return const access to the name of the device's class.
- `edit_mode` (rw) — Access to Eq8's edit mode.
- `global_mode` (rw) — Access to Eq8's global mode.
- `is_active` (r) — Return const access to whether this device is active. This will be false bothwhen the device is off and when it's inside a rack device which is off.
- `is_using_compare_preset_b` (rw) — Returns whether the Device has loaded the preset in compare slot B. Only relevant if can_compare_ab, otherwise errors.
- `latency_in_ms` (r) — Returns the latency of the device in ms.
- `latency_in_samples` (r) — Returns the latency of the device in samples.
- `name` (rw) — Return access to the name of the device.
- `oversample` (rw) — Access to Eq8's oversample value.
- `parameters` (r) — Const access to the list of available automatable parameters for this device.
- `type` (r) — Return the type of the device.
- `view` (r) — Representing the view aspects of a device.

methods:
- Representing the view aspects of an Eq8 device.
- save_preset_to_compare_ab_slot( (Device)arg1) -> None : Saves the current state of the device to the compare AB slot. Only relevant if can_compare_ab, otherwise throws.
- store_chosen_bank( (Device)arg1, (int)arg2, (int)arg3) -> None : Set the selected bank in the device for persistency.

## Live.Eq8Device.GlobalMode
bases: enum
enum: stereo=0, left_right=1, mid_side=2

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.Groove.Base
bases: enum
enum: gb_four=0, gb_eight=1, gb_eight_triplet=2, gb_sixteen=3, gb_sixteen_triplet=4, gb_thirtytwo=5, count=6

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.Groove.Groove
bases: LomObject
> This class represents a groove in Live.

properties:
- `_live_ptr` (r) — 
- `base` (rw) — Get/set the groove's base grid.
- `canonical_parent` (r) — Get the canonical parent of the groove.
- `name` (rw) — Read/write/listen access to the groove's name
- `quantization_amount` (rw) — Read/write/listen access to the groove's quantization amount.
- `random_amount` (rw) — Read/write/listen access to the groove's random amount.
- `timing_amount` (rw) — Read/write/listen access to the groove's timing amount.
- `velocity_amount` (rw) — Read/write/listen access to the groove's velocity amount.

methods:

## Live.GroovePool.GroovePool
bases: LomObject
> This class represents the groove pool in Live.

properties:
- `_live_ptr` (r) — 
- `canonical_parent` (r) — Get the canonical parent of the groove pool.
- `grooves` (r) — Access to the list of grooves

methods:

## Live.HybridReverbDevice.HybridReverbDevice
bases: Device
> This class represents a Hybrid Reverb device.

properties:
- `_live_ptr` (r) — 
- `can_compare_ab` (r) — Returns true if the Device has the capability to AB compare.
- `can_have_chains` (r) — Returns true if the device is a rack.
- `can_have_drum_pads` (r) — Returns true if the device is a drum rack.
- `canonical_parent` (r) — Get the canonical parent of the Device.
- `class_display_name` (r) — Return const access to the name of the device's class name as displayed in Live's browser and device chain
- `class_name` (r) — Return const access to the name of the device's class.
- `ir_attack_time` (rw) — Return the current IrAttackTime
- `ir_category_index` (rw) — Return the current IR category index
- `ir_category_list` (r) — Return the current IR categories list
- `ir_decay_time` (rw) — Return the current IrDecayTime
- `ir_file_index` (rw) — Return the current IR file index
- `ir_file_list` (r) — Return the current IR file list
- `ir_size_factor` (rw) — Return the current IrSizeFactor
- `ir_time_shaping_on` (rw) — Return the current IrTimeShapingOn
- `is_active` (r) — Return const access to whether this device is active. This will be false bothwhen the device is off and when it's inside a rack device which is off.
- `is_using_compare_preset_b` (rw) — Returns whether the Device has loaded the preset in compare slot B. Only relevant if can_compare_ab, otherwise errors.
- `latency_in_ms` (r) — Returns the latency of the device in ms.
- `latency_in_samples` (r) — Returns the latency of the device in samples.
- `name` (rw) — Return access to the name of the device.
- `parameters` (r) — Const access to the list of available automatable parameters for this device.
- `type` (r) — Return the type of the device.
- `view` (r) — Representing the view aspects of a device.

methods:
- Representing the view aspects of a device.
- save_preset_to_compare_ab_slot( (Device)arg1) -> None : Saves the current state of the device to the compare AB slot. Only relevant if can_compare_ab, otherwise throws.
- store_chosen_bank( (Device)arg1, (int)arg2, (int)arg3) -> None : Set the selected bank in the device for persistency.

## Live.Licensing.ProgressDialog
bases: instance
> A modal dialog showing a message and a progress animation.

methods:
- end_modal_loop( (ProgressDialog)arg1) -> None :
- run_in_modal_loop( (ProgressDialog)arg1) -> None :
- set_status_message( (object)arg1, (str)msg) -> None :

## Live.Licensing.PythonLicensingBridge
bases: instance
> Interface to the internal licensing services.

properties:
- `base_product_id` (r) — Returns Live's current base product ID.
- `in_sassafras_mode` (r) — 
- `license_must_match_variant` (r) — Returns a bool indicating if we require the license information returned by the server to match the variant of Live.
- `random_number_for_trial_authorization` (r) — Returns the integer to send along with the Trial authorization request. This same integer will be checked for in `process_trial_response` (and then changed).
- `set_has_unsaved_changes` (r) — Returns true if the set has unsaved changes.

methods:
- authorize_with_sassafras( (PythonLicensingBridge)arg1) -> None :
- create_new_live_set( (PythonLicensingBridge)arg1) -> None : Creates a new live set and discards unsaved changes.
- deauthenticate_user( (PythonLicensingBridge)arg1) -> None : Deletes the current session ID.
- get_progress_dialog( (PythonLicensingBridge)arg1) -> ProgressDialog : Retrieves an instance of ProgressDialog.
- get_session_id( (PythonLicensingBridge)arg1) -> str : Retrieve stored session ID.
- get_startup_dialog( (PythonLicensingBridge)arg1, (object)authorize_callable, (object)authorize_later_callable) -> StartupDialogServes as an entry point for the user to authorize Live on first launch. : Retrieves an instance of the startup dialog with the passed callables connected to its buttons.
- get_trial_time_left( (PythonLicensingBridge)arg1) -> str : Returns remaining time on a trial as a formatted string.
- invoke_pack_installation_callback( (PythonLicensingBridge)arg1) -> None : Call package installation callback.
- invoke_promotions_callback( (PythonLicensingBridge)arg1, (object)arg2) -> None : Call promotions callback.
- load_and_convert_legacy_unlock_cfg( (PythonLicensingBridge)arg1) -> dict : Loads the Unlock.cfg file and returns either an empty dict or one that can be converted to an UnlockData object.
- process_license_response( (PythonLicensingBridge)arg1, (list)license_response_lines) -> UnlockStatus : Processes a list of strings, each representing a server response to a product authorization.
- process_trial_response( (PythonLicensingBridge)arg1, (str)trial_response_line) -> bool : Process the server's response to a Trial authorization.
- request_exit( (PythonLicensingBridge)arg1 [, (int)exit_code=0]) -> None :
- save_current_set( (PythonLicensingBridge)arg1) -> None : Saves the current Live session.
- set_network_timer( (PythonLicensingBridge)arg1, (object)callback, (int)interval_in_ms) -> None : Starts or stops a timer meant for driving network operations. Pass None as callback to stop the timer. If any callback invocation raises an exception, the timer is stopped.
- store_session_identifiers( (PythonLicensingBridge)arg1, (str)session_id, (str)external_session_id) -> None : Securely stores the user's session Identifiers (aka credentials).

## Live.Licensing.StartupDialogServes as an entry point for the user to authorize Live on first launch.
bases: instance

methods:
- end_modal_loop( (StartupDialogServes as an entry point for the user to authorize Live on first launch.)arg1) -> None :
- run_in_modal_loop( (StartupDialogServes as an entry point for the user to authorize Live on first launch.)arg1, (bool)show_only_offline_auth_instructions) -> None :
- set_notification_message( (StartupDialogServes as an entry point for the user to authorize Live on first launch.)arg1, (object)notification_text, (bool)show_progress_bar) -> None :

## Live.Licensing.TrialContext
bases: enum
enum: SAVE=0, FORCE_UPDATE=2, STARTUP=3

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.Licensing.UnlockStatus
bases: instance
> Returns relevant information after unlock

properties:
- `authorization_deactivated` (r) — 
- `authorization_expired` (r) — 
- `has_max_unlock_products` (r) — 
- `temp_demo_mode` (r) — 
- `time_limited` (r) — 
- `unlock_error` (r) — 
- `unlocked` (r) — 

## Live.Listener.ListenerHandle
bases: instance
> This class represents a Python listener when connected to a Live property.

properties:
- `listener_func` (r) — Returns the original function
- `listener_self` (r) — Returns the weak reference to original self, if it was a bound method
- `name` (r) — Prints the name of the property that this listener is connected to

methods:
- disconnect( (ListenerHandle)arg1) -> None : Disconnects the listener from its property

## Live.Listener.ListenerVector
bases: instance
> A read only container for accessing a list of listeners.

methods:
- append( (ListenerVector)arg1, (object)arg2) -> None :
- extend( (ListenerVector)arg1, (object)arg2) -> None :

## Live.LomObject.LomObject
bases: instance
> this is the base class for an object that is accessible via the LOM

properties:
- `_live_ptr` (r) — 

## Live.LooperDevice.LooperDevice
bases: Device
> This class represents a Looper device.

properties:
- `_live_ptr` (r) — 
- `can_compare_ab` (r) — Returns true if the Device has the capability to AB compare.
- `can_have_chains` (r) — Returns true if the device is a rack.
- `can_have_drum_pads` (r) — Returns true if the device is a drum rack.
- `canonical_parent` (r) — Get the canonical parent of the Device.
- `class_display_name` (r) — Return const access to the name of the device's class name as displayed in Live's browser and device chain
- `class_name` (r) — Return const access to the name of the device's class.
- `is_active` (r) — Return const access to whether this device is active. This will be false bothwhen the device is off and when it's inside a rack device which is off.
- `is_using_compare_preset_b` (rw) — Returns whether the Device has loaded the preset in compare slot B. Only relevant if can_compare_ab, otherwise errors.
- `latency_in_ms` (r) — Returns the latency of the device in ms.
- `latency_in_samples` (r) — Returns the latency of the device in samples.
- `loop_length` (r) — The length of Looper's buffer.
- `name` (rw) — Return access to the name of the device.
- `overdub_after_record` (rw) — If true, Looper will switch to overdub after recording, when recording a fixed number of bars. Otherwise, the switch will be to playback without overdubbing.
- `parameters` (r) — Const access to the list of available automatable parameters for this device.
- `record_length_index` (rw) — Access to the Record Length chooser entry index.
- `record_length_list` (r) — Read-only access to the list of Record Length chooser entry strings.
- `tempo` (r) — The tempo of Looper's buffer.
- `type` (r) — Return the type of the device.
- `view` (r) — Representing the view aspects of a device.

methods:
- Representing the view aspects of a device.
- clear( (LooperDevice)arg1) -> None : Erase Looper's recorded content.
- double_length( (LooperDevice)arg1) -> None : Double the length of Looper's buffer.
- double_speed( (LooperDevice)arg1) -> None : Double the speed of Looper's playback.
- export_to_clip_slot( (LooperDevice)arg1, (ClipSlot)arg2) -> None : Export Looper's content to a Session Clip Slot.
- half_length( (LooperDevice)arg1) -> None : Halve the length of Looper's buffer.
- half_speed( (LooperDevice)arg1) -> None : Halve the speed of Looper's playback.
- overdub( (LooperDevice)arg1) -> None : Play back while adding additional layers of incoming audio.
- play( (LooperDevice)arg1) -> None : Play back without overdubbing.
- record( (LooperDevice)arg1) -> None : Record incoming audio.
- save_preset_to_compare_ab_slot( (Device)arg1) -> None : Saves the current state of the device to the compare AB slot. Only relevant if can_compare_ab, otherwise throws.
- stop( (LooperDevice)arg1) -> None : Stop Looper's playback.
- store_chosen_bank( (Device)arg1, (int)arg2, (int)arg3) -> None : Set the selected bank in the device for persistency.
- undo( (LooperDevice)arg1) -> None : Erase everything that was recorded since the last time Overdub was enabled. Calling a second time will restore the material erased by the previous undooperation.

## Live.MaxDevice.MaxDevice
bases: Device
> This class represents a Max for Live device.

properties:
- `_live_ptr` (r) — 
- `audio_inputs` (r) — Const access to a list of all audio inputs of the device.
- `audio_outputs` (r) — Const access to a list of all audio outputs of the device.
- `can_compare_ab` (r) — Returns true if the Device has the capability to AB compare.
- `can_have_chains` (r) — Returns true if the device is a rack.
- `can_have_drum_pads` (r) — Returns true if the device is a drum rack.
- `canonical_parent` (r) — Get the canonical parent of the Device.
- `class_display_name` (r) — Return const access to the name of the device's class name as displayed in Live's browser and device chain
- `class_name` (r) — Return const access to the name of the device's class.
- `is_active` (r) — Return const access to whether this device is active. This will be false bothwhen the device is off and when it's inside a rack device which is off.
- `is_using_compare_preset_b` (rw) — Returns whether the Device has loaded the preset in compare slot B. Only relevant if can_compare_ab, otherwise errors.
- `latency_in_ms` (r) — Returns the latency of the device in ms.
- `latency_in_samples` (r) — Returns the latency of the device in samples.
- `midi_inputs` (r) — Const access to a list of all midi outputs of the device.
- `midi_outputs` (r) — Const access to a list of all midi outputs of the device.
- `name` (rw) — Return access to the name of the device.
- `parameters` (r) — Const access to the list of available automatable parameters for this device.
- `type` (r) — Return the type of the device.
- `view` (r) — Representing the view aspects of a device.

methods:
- Representing the view aspects of a device.
- get_bank_count( (MaxDevice)arg1) -> int : Get the number of parameter banks. This is related to hardware control surfaces.
- get_bank_name( (MaxDevice)arg1, (int)arg2) -> str : Get the name of a parameter bank given by index. This is related to hardware control surfaces.
- get_bank_parameters( (MaxDevice)arg1, (int)arg2) -> list : Get the indices of parameters of the given bank index. Empty slots are marked as -1. Bank index -1 refers to the best-of bank. This function is related to hardware control surfaces.
- get_value_item_icons( (MaxDevice)arg1, (DeviceParameter)arg2) -> list : Get a list of icon identifier strings for a list parameter's values.An empty string is given where no icon should be displayed.An empty list is given when no icons should be displayed.This is related to hardware control surfaces.
- save_preset_to_compare_ab_slot( (Device)arg1) -> None : Saves the current state of the device to the compare AB slot. Only relevant if can_compare_ab, otherwise throws.
- store_chosen_bank( (Device)arg1, (int)arg2, (int)arg3) -> None : Set the selected bank in the device for persistency.

## Live.MeldDevice.MeldDevice
bases: Device
> This class represents a Meld device.

properties:
- `_live_ptr` (r) — 
- `can_compare_ab` (r) — Returns true if the Device has the capability to AB compare.
- `can_have_chains` (r) — Returns true if the device is a rack.
- `can_have_drum_pads` (r) — Returns true if the device is a drum rack.
- `canonical_parent` (r) — Get the canonical parent of the Device.
- `class_display_name` (r) — Return const access to the name of the device's class name as displayed in Live's browser and device chain
- `class_name` (r) — Return const access to the name of the device's class.
- `is_active` (r) — Return const access to whether this device is active. This will be false bothwhen the device is off and when it's inside a rack device which is off.
- `is_using_compare_preset_b` (rw) — Returns whether the Device has loaded the preset in compare slot B. Only relevant if can_compare_ab, otherwise errors.
- `latency_in_ms` (r) — Returns the latency of the device in ms.
- `latency_in_samples` (r) — Returns the latency of the device in samples.
- `mono_poly` (rw) — Returns the mode of Polyphony
- `name` (rw) — Return access to the name of the device.
- `parameters` (r) — Const access to the list of available automatable parameters for this device.
- `poly_voices` (rw) — Return the Poly Voice count
- `selected_engine` (rw) — Return what Voice Engine is selected
- `type` (r) — Return the type of the device.
- `unison_voices` (rw) — Return the Unison Voice count
- `view` (r) — Representing the view aspects of a device.

methods:
- Representing the view aspects of a device.
- save_preset_to_compare_ab_slot( (Device)arg1) -> None : Saves the current state of the device to the compare AB slot. Only relevant if can_compare_ab, otherwise throws.
- store_chosen_bank( (Device)arg1, (int)arg2, (int)arg3) -> None : Set the selected bank in the device for persistency.

## Live.MidiMap.CCFeedbackRule
bases: instance
> Structure to define feedback properties of MIDI mappings.

properties:
- `cc_no` (rw) — 
- `cc_value_map` (rw) — 
- `channel` (rw) — 
- `delay_in_ms` (rw) — 
- `enabled` (rw) — 

## Live.MidiMap.MapMode
bases: enum
enum: absolute=0, relative_signed_bit=1, relative_binary_offset=2, relative_two_compliment=3, relative_signed_bit2=4, absolute_14_bit=5, relative_smooth_signed_bit=6, relative_smooth_binary_offset=7, relative_smooth_two_compliment=8, relative_smooth_signed_bit2=9

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.MidiMap.NoteFeedbackRule
bases: instance
> Structure to define feedback properties of MIDI mappings.

properties:
- `channel` (rw) — 
- `delay_in_ms` (rw) — 
- `enabled` (rw) — 
- `note_no` (rw) — 
- `vel_map` (rw) — 

## Live.MidiMap.PitchBendFeedbackRule
bases: instance
> Structure to define feedback properties of MIDI mappings.

properties:
- `channel` (rw) — 
- `delay_in_ms` (rw) — 
- `enabled` (rw) — 
- `value_pair_map` (rw) — 

## Live.MixerDevice.MixerDevice
bases: LomObject
> This class represents a Mixer Device in Live, which gives you access to the Volume and Panning properties of a Track.

properties:
- `_live_ptr` (r) — 
- `canonical_parent` (r) — Get the canonical parent of the mixer device.
- `crossfade_assign` (rw) — Player- and ReturnTracks only: Access to the Track's Crossfade Assign State.
- `crossfader` (r) — MainTrack only: Const access to the Crossfader.
- `cue_volume` (r) — MainTrack only: Const access to the Cue Volume Parameter.
- `left_split_stereo` (r) — Const access to the Track's Left Split Stereo Panning Device Parameter.
- `panning` (r) — Const access to the Tracks Panning Device Parameter.
- `panning_mode` (rw) — Access to the Track's Panning Mode.
- `right_split_stereo` (r) — Const access to the Track's Right Split Stereo Panning Device Parameter.
- `sends` (r) — Const access to the Tracks list of Send Amount Device Parameters.
- `song_tempo` (r) — MainTrack only: Const access to the Song's Tempo.
- `track_activator` (r) — Const access to the Tracks Activator Device Parameter.
- `volume` (r) — Const access to the Tracks Volume Device Parameter.

methods:
- `crossfade_assignments`
- `panning_modes`

## Live.PluginDevice.PluginDevice
bases: Device
> This class represents a plugin device.

properties:
- `_live_ptr` (r) — 
- `can_compare_ab` (r) — Returns true if the Device has the capability to AB compare.
- `can_have_chains` (r) — Returns true if the device is a rack.
- `can_have_drum_pads` (r) — Returns true if the device is a drum rack.
- `canonical_parent` (r) — Get the canonical parent of the Device.
- `class_display_name` (r) — Return const access to the name of the device's class name as displayed in Live's browser and device chain
- `class_name` (r) — Return const access to the name of the device's class.
- `is_active` (r) — Return const access to whether this device is active. This will be false bothwhen the device is off and when it's inside a rack device which is off.
- `is_editor_open` (rw) — Access to the opened state of the plugin's editor window.
- `is_using_compare_preset_b` (rw) — Returns whether the Device has loaded the preset in compare slot B. Only relevant if can_compare_ab, otherwise errors.
- `latency_in_ms` (r) — Returns the latency of the device in ms.
- `latency_in_samples` (r) — Returns the latency of the device in samples.
- `name` (rw) — Return access to the name of the device.
- `parameters` (r) — Const access to the list of available automatable parameters for this device.
- `presets` (r) — Get the list of presets the plugin offers.
- `selected_preset_index` (rw) — Access to the index of the currently selected preset.
- `type` (r) — Return the type of the device.
- `view` (r) — Representing the view aspects of a device.

methods:
- Representing the view aspects of a device.
- get_parameter_names( (PluginDevice)arg1 [, (int)begin=0 [, (int)end=-1]]) -> StringVector : Get the range of plugin parameter names, bound by begin and end. If end is smaller than 0 it is interpreted as the parameter count.
- save_preset_to_compare_ab_slot( (Device)arg1) -> None : Saves the current state of the device to the compare AB slot. Only relevant if can_compare_ab, otherwise throws.
- store_chosen_bank( (Device)arg1, (int)arg2, (int)arg3) -> None : Set the selected bank in the device for persistency.

## Live.RackDevice.RackDevice
bases: Device
> This class represents a Rack device.

properties:
- `_live_ptr` (r) — 
- `can_compare_ab` (r) — Returns true if the Device has the capability to AB compare.
- `can_have_chains` (r) — Returns true if the device is a rack.
- `can_have_drum_pads` (r) — Returns true if the device is a drum rack.
- `can_show_chains` (r) — return True, if this Rack contains a rack instrument device that is capable of showing its chains in session view.
- `canonical_parent` (r) — Get the canonical parent of the Device.
- `chain_selector` (r) — Const access to the chain selector parameter.
- `chains` (r) — Return const access to the list of chains in this device. Throws an exception if can_have_chains is false.
- `class_display_name` (r) — Return const access to the name of the device's class name as displayed in Live's browser and device chain
- `class_name` (r) — Return const access to the name of the device's class.
- `drum_pads` (r) — Return const access to the list of drum pads in this device. Throws an exception if can_have_drum_pads is false.
- `has_drum_pads` (r) — Returns true if the device is a drum rack which has drum pads. Throws an exception if can_have_drum_pads is false.
- `has_macro_mappings` (r) — Returns true if any of the rack's macros are mapped to a parameter.
- `is_active` (r) — Return const access to whether this device is active. This will be false bothwhen the device is off and when it's inside a rack device which is off.
- `is_showing_chains` (rw) — Returns True, if it is showing chains.
- `is_using_compare_preset_b` (rw) — Returns whether the Device has loaded the preset in compare slot B. Only relevant if can_compare_ab, otherwise errors.
- `latency_in_ms` (r) — Returns the latency of the device in ms.
- `latency_in_samples` (r) — Returns the latency of the device in samples.
- `macros_mapped` (r) — A list of booleans, one for each macro parameter, which is True iffthat macro is mapped to something
- `name` (rw) — Return access to the name of the device.
- `parameters` (r) — Const access to the list of available automatable parameters for this device.
- `return_chains` (r) — Return const access to the list of return chains in this device. Throws an exception if can_have_chains is false.
- `selected_variation_index` (rw) — Access to the index of the currently selected macro variation.Throws an exception if the index is out of range.
- `type` (r) — Return the type of the device.
- `variation_count` (r) — Access to the number of macro variations currently stored.
- `view` (r) — Representing the view aspects of a device.
- `visible_drum_pads` (r) — Return const access to the list of visible drum pads in this device. Throws an exception if can_have_drum_pads is false.
- `visible_macro_count` (r) — Access to the number of macros that are currently visible.

methods:
- Representing the view aspects of a rack device.
- add_macro( (RackDevice)arg1) -> None : Increases the number of visible macro controls in the rack. Throws an exception if the maximum number of macro controls is reached.
- copy_pad( (RackDevice)arg1, (int)arg2, (int)arg3) -> None : Copies all contents of a drum pad from a source pad into a destination pad. copy_pad(source_index, destination_index) where source_index and destination_index correspond to the note number/index of the drum pad in a drum rack. Throws an exception when the source pad is empty, or when the source or destination indices are not between 0 - 127.
- delete_selected_variation( (Device)arg1) -> None : Deletes the currently selected macro variation.Does nothing if there is no selected variation.
- insert_chain( (RackDevice)arg1 [, (int)Index=-1]) -> LomObject : Inserts a new chain, either at the specified index or, if not index was specified, at the end of the chain sequence.
- randomize_macros( (RackDevice)arg1) -> None : Randomizes the values for all macro controls not excluded from randomization.
- recall_last_used_variation( (Device)arg1) -> None : Recalls the macro variation that was recalled most recently.Does nothing if no variation has been recalled yet.
- recall_selected_variation( (Device)arg1) -> None : Recalls the currently selected macro variation.Does nothing if there are no variations.
- remove_macro( (RackDevice)arg1) -> None : Decreases the number of visible macro controls in the rack. Throws an exception if the minimum number of macro controls is reached.
- save_preset_to_compare_ab_slot( (Device)arg1) -> None : Saves the current state of the device to the compare AB slot. Only relevant if can_compare_ab, otherwise throws.
- store_chosen_bank( (Device)arg1, (int)arg2, (int)arg3) -> None : Set the selected bank in the device for persistency.
- store_variation( (Device)arg1) -> None : Stores a new variation of the values of all currently mapped macros

## Live.RoarDevice.RoarDevice
bases: Device
> This class represents a Roar device.

properties:
- `_live_ptr` (r) — 
- `can_compare_ab` (r) — Returns true if the Device has the capability to AB compare.
- `can_have_chains` (r) — Returns true if the device is a rack.
- `can_have_drum_pads` (r) — Returns true if the device is a drum rack.
- `canonical_parent` (r) — Get the canonical parent of the Device.
- `class_display_name` (r) — Return const access to the name of the device's class name as displayed in Live's browser and device chain
- `class_name` (r) — Return const access to the name of the device's class.
- `env_listen` (rw) — Return the Envelope Input Listen toggle state
- `is_active` (r) — Return const access to whether this device is active. This will be false bothwhen the device is off and when it's inside a rack device which is off.
- `is_using_compare_preset_b` (rw) — Returns whether the Device has loaded the preset in compare slot B. Only relevant if can_compare_ab, otherwise errors.
- `latency_in_ms` (r) — Returns the latency of the device in ms.
- `latency_in_samples` (r) — Returns the latency of the device in samples.
- `name` (rw) — Return access to the name of the device.
- `parameters` (r) — Const access to the list of available automatable parameters for this device.
- `routing_mode_index` (rw) — Return the routing mode index
- `routing_mode_list` (r) — Return the routing mode list
- `type` (r) — Return the type of the device.
- `view` (r) — Representing the view aspects of a device.

methods:
- Representing the view aspects of a device.
- save_preset_to_compare_ab_slot( (Device)arg1) -> None : Saves the current state of the device to the compare AB slot. Only relevant if can_compare_ab, otherwise throws.
- store_chosen_bank( (Device)arg1, (int)arg2, (int)arg3) -> None : Set the selected bank in the device for persistency.

## Live.Sample.Sample
bases: LomObject
> This class represents a sample file loaded into a Simpler instance.

properties:
- `_live_ptr` (r) — 
- `beats_granulation_resolution` (rw) — Access to the Granulation Resolution parameter in Beats Warp Mode.
- `beats_transient_envelope` (rw) — Access to the Transient Envelope parameter in Beats Warp Mode.
- `beats_transient_loop_mode` (rw) — Access to the Transient Loop Mode parameter in Beats Warp Mode.
- `canonical_parent` (r) — Access to the sample's canonical parent.
- `complex_pro_envelope` (rw) — Access to the Envelope parameter in Complex Pro Mode.
- `complex_pro_formants` (rw) — Access to the Formants parameter in Complex Pro Warp Mode.
- `end_marker` (rw) — Access to the position of the sample's end marker.
- `file_path` (r) — Get the path of the sample file.
- `gain` (rw) — Access to the sample gain.
- `length` (r) — Get the length of the sample file in sample frames.
- `sample_rate` (r) — Access to the audio sample rate of the sample.
- `slices` (r) — Access to the list of slice points in sample time in the sample.
- `slicing_beat_division` (rw) — Access to sample's slicing step size.
- `slicing_region_count` (rw) — Access to sample's slicing split count.
- `slicing_sensitivity` (rw) — Access to sample's slicing sensitivity whose sensitivity is in between 0.0 and 1.0. The higher the sensitivity, the more slices will be available.
- `slicing_style` (rw) — Access to sample's slicing style.
- `start_marker` (rw) — Access to the position of the sample's start marker.
- `texture_flux` (rw) — Access to the Flux parameter in Texture Warp Mode.
- `texture_grain_size` (rw) — Access to the Grain Size parameter in Texture Warp Mode.
- `tones_grain_size` (rw) — Access to the Grain Size parameter in Tones Warp Mode.
- `warp_markers` (r) — Get the warp markers for this sample.
- `warp_mode` (rw) — Access to the sample's warp mode.
- `warping` (rw) — Access to the sample's warping property.

methods:
- beat_to_sample_time( (Sample)self, (float)beat_time) -> float : Converts the given beat time to sample time. Raises an error if the sample is not warped.
- clear_slices( (Sample)self) -> None : Clears all slices created in Simpler's manual mode.
- gain_display_string( (Sample)self) -> str : Get the gain's display value as a string.
- insert_slice( (Sample)self, (int)slice_time) -> None : Add a slice point at the provided time if there is none.
- move_slice( (Sample)self, (int)old_time, (int)new_time) -> int : Move the slice point at the provided time.
- remove_slice( (Sample)self, (int)slice_time) -> None : Remove the slice point at the provided time if there is one.
- reset_slices( (Sample)self) -> None : Resets all edited slices to their original positions.
- sample_to_beat_time( (Sample)self, (float)sample_time) -> float : Converts the given sample time to beat time. Raises an error if the sample is not warped.

## Live.Sample.SlicingBeatDivision
bases: enum
enum: sixteenth=0, sixteenth_triplett=1, eighth=2, eighth_triplett=3, quarter=4, quarter_triplett=5, half=6, half_triplett=7, one_bar=8, two_bars=9, four_bars=10

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.Sample.SlicingStyle
bases: enum
enum: transient=0, beat=1, region=2, manual=3

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.Sample.TransientLoopMode
bases: enum
enum: off=0, forward=1, alternate=2

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.Scene.Scene
bases: LomObject
> This class represents an series of ClipSlots in Lives Sessionview matrix.

properties:
- `_live_ptr` (r) — 
- `canonical_parent` (r) — Get the canonical parent of the scene.
- `clip_slots` (r) — return a list of clipslots (see class AClipSlot) that this scene covers.
- `color` (rw) — Get/set access to the color of the scene (RGB).
- `color_index` (rw) — Get/set access to the color index of the scene. Can be None for no color.
- `is_empty` (r) — Returns True if all clip slots of this scene are empty.
- `is_triggered` (r) — Const access to the scene's trigger state.
- `name` (rw) — Get/Set the name of the scene.
- `tempo` (rw) — Get/Set the tempo value of the scene. The song will use the scene's tempo as soon as the scene is fired. Returns -1 if the scene has no tempo property.
- `tempo_enabled` (rw) — Get/Set the active state of the scene tempo. When disabled, the scene will use the song's tempo,and the tempo value returned will be -1Returns a bool indicating the state of the scene's tempo
- `time_signature_denominator` (rw) — Get/Set the scene's time signature denominator. The song will use the scene's time signature as soon as the scene is fired. Returns -1 if the scene has no time signature property.
- `time_signature_enabled` (rw) — Get the active state of the scene time signature. When disabled, the scene will use the song's time signature,and the time signature values returned will be -1Returns a bool indicating the state of the scene's time signa
- `time_signature_numerator` (rw) — Get/Set the scene's time signature numerator. The song will use the scene's time signature as soon as the scene is fired. Returns -1 if the scene has no time signature property.

methods:
- fire( (Scene)arg1 [, (bool)force_legato=False [, (bool)can_select_scene_on_launch=True]]) -> None : Fire the scene directly. Will fire all clipslots that this scene owns and select the scene itself.
- fire_as_selected( (Scene)arg1 [, (bool)force_legato=False]) -> None : Fire the selected scene. Will fire all clipslots that this scene owns and select the next scene if necessary.
- set_fire_button_state( (Scene)arg1, (bool)arg2) -> None : Set the scene's fire button state directly. Supports all launch modes.

## Live.ShifterDevice.ShifterDevice
bases: Device
> This class represents a Shifter device.

properties:
- `_live_ptr` (r) — 
- `can_compare_ab` (r) — Returns true if the Device has the capability to AB compare.
- `can_have_chains` (r) — Returns true if the device is a rack.
- `can_have_drum_pads` (r) — Returns true if the device is a drum rack.
- `canonical_parent` (r) — Get the canonical parent of the Device.
- `class_display_name` (r) — Return const access to the name of the device's class name as displayed in Live's browser and device chain
- `class_name` (r) — Return const access to the name of the device's class.
- `is_active` (r) — Return const access to whether this device is active. This will be false bothwhen the device is off and when it's inside a rack device which is off.
- `is_using_compare_preset_b` (rw) — Returns whether the Device has loaded the preset in compare slot B. Only relevant if can_compare_ab, otherwise errors.
- `latency_in_ms` (r) — Returns the latency of the device in ms.
- `latency_in_samples` (r) — Returns the latency of the device in samples.
- `name` (rw) — Return access to the name of the device.
- `parameters` (r) — Const access to the list of available automatable parameters for this device.
- `pitch_bend_range` (rw) — Return the pitch bend range for MIDI pitch mode
- `pitch_mode_index` (rw) — Return the current pitch mode index
- `pitch_mode_list` (r) — Return the current pitch mode list
- `type` (r) — Return the type of the device.
- `view` (r) — Representing the view aspects of a device.

methods:
- Representing the view aspects of a device.
- save_preset_to_compare_ab_slot( (Device)arg1) -> None : Saves the current state of the device to the compare AB slot. Only relevant if can_compare_ab, otherwise throws.
- store_chosen_bank( (Device)arg1, (int)arg2, (int)arg3) -> None : Set the selected bank in the device for persistency.

## Live.SimplerDevice.PlaybackMode
bases: enum
enum: classic=0, one_shot=1, slicing=2

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.SimplerDevice.SimplerDevice
bases: Device
> This class represents a Simpler device.

properties:
- `_live_ptr` (r) — 
- `can_compare_ab` (r) — Returns true if the Device has the capability to AB compare.
- `can_have_chains` (r) — Returns true if the device is a rack.
- `can_have_drum_pads` (r) — Returns true if the device is a drum rack.
- `can_warp_as` (r) — Returns true if warp_as is available.
- `can_warp_double` (r) — Returns true if warp_double is available.
- `can_warp_half` (r) — Returns true if warp_half is available.
- `canonical_parent` (r) — Get the canonical parent of the Device.
- `class_display_name` (r) — Return const access to the name of the device's class name as displayed in Live's browser and device chain
- `class_name` (r) — Return const access to the name of the device's class.
- `is_active` (r) — Return const access to whether this device is active. This will be false bothwhen the device is off and when it's inside a rack device which is off.
- `is_using_compare_preset_b` (rw) — Returns whether the Device has loaded the preset in compare slot B. Only relevant if can_compare_ab, otherwise errors.
- `latency_in_ms` (r) — Returns the latency of the device in ms.
- `latency_in_samples` (r) — Returns the latency of the device in samples.
- `multi_sample_mode` (r) — Returns whether Simpler is in mulit-sample mode.
- `name` (rw) — Return access to the name of the device.
- `note_pitch_bend_range` (rw) — Access to the Note Pitch Bend Range in Simpler.
- `pad_slicing` (rw) — When set to true, slices can be added in slicing mode by playing notes  .that are not assigned to slices, yet.
- `parameters` (r) — Const access to the list of available automatable parameters for this device.
- `pitch_bend_range` (rw) — Access to the Pitch Bend Range in Simpler.
- `playback_mode` (rw) — Access to Simpler's playback mode.
- `playing_position` (r) — Constant access to the current playing position in the sample. The returned value is the normalized position between sample start and end.
- `playing_position_enabled` (r) — Returns whether Simpler is showing the playing position. The returned value is True while the sample is played back
- `retrigger` (rw) — Access to Simpler's retrigger mode.
- `sample` (r) — Get the loaded Sample.
- `slicing_playback_mode` (rw) — Access to Simpler's slicing playback mode.
- `type` (r) — Return the type of the device.
- `view` (r) — Representing the view aspects of a device.
- `voices` (rw) — Access to the number of voices in Simpler.

methods:
- Representing the view aspects of a simpler device.
- crop( (SimplerDevice)self) -> None : Crop the loaded sample to the active area between start- and end marker. Calling this method on an empty simpler raises an error.
- guess_playback_length( (SimplerDevice)self) -> float : Return an estimated beat time for the playback length between start- and end-marker. Calling this method on an empty simpler raises an error.
- replace_sample( (SimplerDevice)self, (object)file_path) -> None : Replaces the loaded samples with the one at the provided path.
- reverse( (SimplerDevice)self) -> None : Reverse the loaded sample. Calling this method on an empty simpler raises an error.
- save_preset_to_compare_ab_slot( (Device)arg1) -> None : Saves the current state of the device to the compare AB slot. Only relevant if can_compare_ab, otherwise throws.
- store_chosen_bank( (Device)arg1, (int)arg2, (int)arg3) -> None : Set the selected bank in the device for persistency.
- warp_as( (SimplerDevice)self, (float)beat_time) -> None : Warp the playback region between start- and end-marker as the given length. Calling this method on an empty simpler raises an error.
- warp_double( (SimplerDevice)self) -> None : Doubles the tempo for region between start- and end-marker.
- warp_half( (SimplerDevice)self) -> None : Halves the tempo for region between start- and end-marker.

## Live.SimplerDevice.SlicingPlaybackMode
bases: enum
enum: mono=0, poly=1, thru=2

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.Song.BeatTime
bases: instance
> Represents a Time, splitted into Bars, Beats, SubDivision and Ticks.

properties:
- `bars` (rw) — 
- `beats` (rw) — 
- `sub_division` (rw) — 
- `ticks` (rw) — 

## Live.Song.CaptureDestination
bases: enum
> The destination for MIDI capture.
enum: auto=0, session=1, arrangement=2

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.Song.CaptureMode
bases: enum
> The capture mode that is used for capture and insert scene.
enum: all=0, all_except_selected=1

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.Song.CuePoint
bases: LomObject
> Represents a 'Marker' in the arrangement.

properties:
- `_live_ptr` (r) — 
- `canonical_parent` (r) — Get the canonical parent of the cue point.
- `name` (rw) — Get/Set/Listen to the name of this CuePoint, as visible in the arranger.
- `time` (r) — Get/Listen to the CuePoint's time in beats.

methods:
- jump( (CuePoint)arg1) -> None : When the Song is playing, set the playing-position quantized to this Cuepoint's time. When not playing, simply move the start playing position.

## Live.Song.Quantization
bases: enum
enum: q_no_q=0, q_8_bars=1, q_4_bars=2, q_2_bars=3, q_bar=4, q_half=5, q_half_triplet=6, q_quarter=7, q_quarter_triplet=8, q_eight=9, q_eight_triplet=10, q_sixtenth=11, q_sixtenth_triplet=12, q_thirtytwoth=13

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.Song.RecordingQuantization
bases: enum
enum: rec_q_no_q=0, rec_q_quarter=1, rec_q_eight=2, rec_q_eight_triplet=3, rec_q_eight_eight_triplet=4, rec_q_sixtenth=5, rec_q_sixtenth_triplet=6, rec_q_sixtenth_sixtenth_triplet=7, rec_q_thirtysecond=8

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.Song.SessionRecordStatus
bases: enum
enum: off=0, transition=2, on=1

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.Song.SmptTime
bases: instance
> Represents a Time, split into Hours, Minutes, Seconds and Frames. The frame type must be specified when calling a function that returns a SmptTime.

properties:
- `frames` (rw) — 
- `hours` (rw) — 
- `minutes` (rw) — 
- `seconds` (rw) — 

## Live.Song.Song
bases: LomObject
> This class represents a Live set.

properties:
- `_live_ptr` (r) — 
- `appointed_device` (rw) — Read, write, and listen access to the appointed Device
- `arrangement_overdub` (rw) — Get/Set the global arrangement overdub state.
- `back_to_arranger` (rw) — Get/Set if triggering a Clip in the Session, disabled the playback of Clips in the Arranger.
- `can_capture_midi` (r) — Get whether there currently is material to be captured on any tracks.
- `can_jump_to_next_cue` (r) — Returns true when there is a cue marker right to the playing pos that we could jump to.
- `can_jump_to_prev_cue` (r) — Returns true when there is a cue marker left to the playing pos that we could jump to.
- `can_redo` (r) — Returns true if there is an undone action that we can redo.
- `can_undo` (r) — Returns true if there is an action that we can restore.
- `canonical_parent` (r) — Get the canonical parent of the song.
- `clip_trigger_quantization` (rw) — Get/Set access to the quantization settings that are used to fire Clips in the Session.
- `count_in_duration` (r) — Get the count in duration. Returns an index, mapped as follows:  0 - None, 1 - 1 Bar, 2 - 2 Bars, 3 - 4 Bars.
- `cue_points` (r) — Const access to a list of all cue points of the Live Song.
- `current_song_time` (rw) — Get/Set access to the songs current playing position in beats.
- `exclusive_arm` (r) — Get if Tracks should be armed exclusively by default.
- `exclusive_solo` (r) — Get if Tracks should be soloed exclusively by default.
- `file_path` (r) — Get the current Live Set's path on disk.
- `groove_amount` (rw) — Get/Set the global groove amount, that adjust all setup grooves in all clips.
- `groove_pool` (r) — Get the groove pool.
- `is_ableton_link_enabled` (rw) — Enable/disable Ableton Link.
- `is_ableton_link_start_stop_sync_enabled` (rw) — Enable/disable Ableton Link Start Stop Sync.
- `is_counting_in` (r) — Get whether currently counting in.
- `is_playing` (rw) — Returns true if the Song is currently playing.
- `last_event_time` (r) — Return the time of the last set event in the song. In contrary to song_length, this will not add some extra beats that are mostly needed for Display purposes in the Arrangerview.
- `loop` (rw) — Get/Set the looping flag that en/disables the usage of the global loop markers in the song.
- `loop_length` (rw) — Get/Set the length of the global loop marker position in beats.
- `loop_start` (rw) — Get/Set the start of the global loop marker position in beats.
- `master_track` (r) — Access to the Main Track (always available)
- `metronome` (rw) — Get/Set if the metronom is audible.
- `midi_recording_quantization` (rw) — Get/Set access to the settings that are used to quantize MIDI recordings.
- `name` (r) — Get the current Live Set's name.
- `nudge_down` (rw) — Get/Set the status of the nudge down button.
- `nudge_up` (rw) — Get/Set the status of the nudge up button.
- `overdub` (rw) — Legacy hook for Live 8 overdub state. Now hooks to session record, but never starts playback.
- `punch_in` (rw) — Get/Set the flag that will enable recording as soon as the Song plays and hits the global loop start region.
- `punch_out` (rw) — Get/Set the flag that will disable recording as soon as the Song plays and hits the global loop end region.
- `re_enable_automation_enabled` (r) — Returns true if some automated parameter has been overriden
- `record_mode` (rw) — Get/Set the state of the global recording flag.
- `return_tracks` (r) — Const access to the list of available Return Tracks.
- `root_note` (rw) — Set and access the root (i.e. key) of the song. The root can be a number between 0 and 11, with 0 corresponding to C and 11 corresponding to B.
- `scale_intervals` (r) — Reports the current scale's intervals as a list of integers, starting with the root and representing the number of halfsteps (e.g. Major -> 0, 2, 4, 5, 7, 9, 11)
- `scale_mode` (rw) — Access to the Scale Mode setting in Live. When on, key tracks that belong to the currently selected scale are highlighted in Live's MIDI Note Editor, and pitch-based parameters in MIDI Tools and Devices can be edited in 
- `scale_name` (rw) — Set and access the currently selected scale by name. The default scale names that can be saved with a set and recalled are 'Major', 'Minor', 'Dorian', 'Mixolydian' ,'Lydian' ,'Phrygian' ,'Locrian',  'Whole Tone', 'Half-w
- `scenes` (r) — Const access to a list of all Scenes in the Live Song.
- `select_on_launch` (r) — Get if Scenes and Clips should be selected when fired.
- `session_automation_record` (rw) — Returns true if automation recording is enabled.
- `session_record` (rw) — Get/Set the session record state.
- `session_record_status` (r) — Get the session slot-recording state.
- `signature_denominator` (rw) — Get/Set access to the global signature denominator of the Song.
- `signature_numerator` (rw) — Get/Set access to the global signature numerator of the Song.
- `song_length` (r) — Return the time of the last set event in the song, plus som extra beats that are usually added for better navigation in the arrangerview.
- `start_time` (rw) — Get/Set access to the songs current start time in beats. The set time may be overridden by the current loop/locator start time.
- `swing_amount` (rw) — Get/Set access to the amount of swing that is applied when adding or quantizing notes to MIDI clips
- `tempo` (rw) — Get/Set the global project tempo.
- `tempo_follower_enabled` (rw) — Get/Set whether the Tempo Follower is controlling the tempo. The Tempo Follower Toggle must be made visible in the preferences for this property to be effective.
- `tracks` (r) — Const access to a list of all Player Tracks in the Live Song, excluding the return and Main Track (see also Song.send_tracks and Song.master_track). At least one MIDI or Audio Track is always available.
- `tuning_system` (r) — Access the currently active tuning system.
- `view` (r) — Representing the view aspects of a Live document:  The Session and Arrangerview.
- `visible_tracks` (r) — Const access to a list of all visible Player Tracks in the Live Song, excluding the return and Main Track (see also Song.send_tracks and Song.master_track). At least one MIDI or Audio Track is always available.

methods:
- Representing the view aspects of a Live document: The Session and Arrangerview.
- begin_undo_step( (Song)arg1) -> None :
- capture_and_insert_scene( (Song)arg1 [, (int)CaptureMode=Song.CaptureMode.all]) -> None : Capture currently playing clips and insert them as a new scene after the selected scene. Raises a runtime error if creating a new scene would exceed the limitations.
- capture_midi( (Song)arg1 [, (int)Destination=Song.CaptureDestination.auto]) -> None : Capture recently played MIDI material from audible tracks. If no Destination is given or Destination is set to CaptureDestination.auto, the captured material is inserted into the Session or Arrangement depending on which is visible. If Destination is set to CaptureDestination.session or CaptureDestination.arrangement, inserts the ma
- continue_playing( (Song)arg1) -> None : Continue playing the song from the current position
- create_audio_track( (Song)arg1 [, (object)Index=None]) -> Track : Create a new audio track at the optional given index and return it.If the index is -1, the new track is added at the end. It will create a default audio track if possible. If the index is invalid or the new track would exceed the limitations, a limitation error is raised.If the index is missing, the track is created after the last selected item
- create_midi_track( (Song)arg1 [, (object)Index=None]) -> Track : Create a new midi track at the optional given index and return it.If the index is -1, the new track is added at the end.It will create a default midi track if possible. If the index is invalid or the new track would exceed the limitations, a limitation error is raised.If the index is missing, the track is created after the last selected item
- create_return_track( (Song)arg1) -> Track : Create a new return track at the end and return it. If the new track would exceed the limitations, a limitation error is raised. If the maximum number of return tracks is exceeded, a RuntimeError is raised.
- create_scene( (Song)arg1, (int)arg2) -> Scene : Create a new scene at the given index. If the index is -1, the new scene is added at the end. If the index is invalid or the new scene would exceed the limitations, a limitation error is raised.
- delete_return_track( (Song)arg1, (int)arg2) -> None : Delete the return track with the given index. If no track with this index exists, an exception will be raised.
- delete_scene( (Song)arg1, (int)arg2) -> None : Delete the scene with the given index. If no scene with this index exists, an exception will be raised.
- delete_track( (Song)arg1, (int)arg2) -> None : Delete the track with the given index. If no track with this index exists, an exception will be raised.
- duplicate_scene( (Song)arg1, (int)arg2) -> None : Duplicates a scene and selects the new one. Raises a limitation error if creating a new scene would exceed the limitations.
- duplicate_track( (Song)arg1, (int)arg2) -> None : Duplicates a track and selects the new one. If the track is inside a folded group track, the group track is unfolded. Raises a limitation error if creating a new track would exceed the limitations.
- end_undo_step( (Song)arg1) -> None :
- find_device_position( (Song)arg1, (Device)device, (LomObject)target, (int)target_position) -> int : Returns the closest possible position to the given target, where the device can be inserted. If inserting is not possible at all (i.e. if the device type is wrong), -1 is returned.
- force_link_beat_time( (Song)arg1) -> None : Force the Link timeline to jump to Lives current beat time. Danger: This can cause beat time discontinuities in other connected apps.
- get_beats_loop_length( (Song)arg1) -> BeatTime : Get const access to the songs loop length, using a BeatTime class with the current global set signature.
- get_beats_loop_start( (Song)arg1) -> BeatTime : Get const access to the songs loop start, using a BeatTime class with the current global set signature.
- get_current_beats_song_time( (Song)arg1) -> BeatTime : Get const access to the songs current playing position, using a BeatTime class with the current global set signature.
- get_current_smpte_song_time( (Song)arg1, (int)arg2) -> SmptTime : Get const access to the songs current playing position, by specifying the SMPTE format in which you would like to receive the time.
- get_data( (Song)arg1, (object)key, (object)default_value) -> object : Get data for the given key, that was previously stored using set_data.
- is_cue_point_selected( (Song)arg1) -> bool : Return true if the global playing pos is currently on a cue point.
- jump_by( (Song)arg1, (float)arg2) -> None : Set a new playing pos, relative to the current one.
- jump_to_next_cue( (Song)arg1) -> None : Jump to the next cue (marker) if possible.
- jump_to_prev_cue( (Song)arg1) -> None : Jump to the prior cue (marker) if possible.
- move_device( (Song)arg1, (Device)device, (LomObject)target, (int)target_position) -> int : Move a device into the target at the given position, where 0 moves it before the first device and len(devices) moves it to the end of the device chain.If the device cannot be moved to this position, the nearest possible position is chosen. If the device type is not valid, a runtime error is raised.Returns the index, where the d
- play_selection( (Song)arg1) -> None : Start playing the current set selection, or do nothing if no selection is set.
- re_enable_automation( (Song)arg1) -> None : Discards overrides of automated parameters.
- redo( (Song)arg1) -> str : Redo the last action that was undone.
- scrub_by( (Song)arg1, (float)arg2) -> None : Same as jump_by, but does not stop playback.
- set_data( (Song)arg1, (object)key, (object)value) -> None : Store data for the given key in this object. The data is persistent and will be restored when loading the Live Set.
- set_or_delete_cue( (Song)arg1) -> None : When a cue is selected, it gets deleted. If no cue is selected, a new cue is created at the current global songtime.
- start_playing( (Song)arg1) -> None : Start playing from the startmarker
- stop_all_clips( (Song)arg1 [, (bool)Quantized=True]) -> None : Stop all playing Clips (if any) but continue playing the Song.
- stop_playing( (Song)arg1) -> None : Stop playing the Song.
- sync_parameter_changes( (Song)arg1) -> None : Synchronize parameter changes with the UI thread. For performance reasons, parameter changes are not synchronized individually. This function is available in case synchronization of the parameter change into the document is needed before subsequent device operations.
- tap_tempo( (Song)arg1) -> None : Trigger the tap tempo function.
- trigger_session_record( (Song)self [, (float)record_length=1.7976931348623157e+308]) -> None : Triggers a new session recording.
- undo( (Song)arg1) -> str : Undo the last action that was made.

## Live.Song.TimeFormat
bases: enum
enum: ms_time=0, smpte_24=1, smpte_25=2, smpte_30=3, smpte_30_drop=4, smpte_29=5

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.SpectralResonatorDevice.SpectralResonatorDevice
bases: Device
> This class represents a Spectral Resonator device.

properties:
- `_live_ptr` (r) — 
- `can_compare_ab` (r) — Returns true if the Device has the capability to AB compare.
- `can_have_chains` (r) — Returns true if the device is a rack.
- `can_have_drum_pads` (r) — Returns true if the device is a drum rack.
- `canonical_parent` (r) — Get the canonical parent of the Device.
- `class_display_name` (r) — Return const access to the name of the device's class name as displayed in Live's browser and device chain
- `class_name` (r) — Return const access to the name of the device's class.
- `frequency_dial_mode` (rw) — Return the current frequency dial mode index
- `frequency_dial_mode_list` (r) — Return the current frequency dial mode list
- `is_active` (r) — Return const access to whether this device is active. This will be false bothwhen the device is off and when it's inside a rack device which is off.
- `is_using_compare_preset_b` (rw) — Returns whether the Device has loaded the preset in compare slot B. Only relevant if can_compare_ab, otherwise errors.
- `latency_in_ms` (r) — Returns the latency of the device in ms.
- `latency_in_samples` (r) — Returns the latency of the device in samples.
- `midi_gate` (rw) — Return the current midi gate index
- `midi_gate_list` (r) — Return the current midi gate list
- `mod_mode` (rw) — Return the current mod mode index
- `mod_mode_list` (r) — Return the current mod mode list
- `mono_poly` (rw) — Return the current mono poly mode index
- `mono_poly_list` (r) — Return the current mono poly mode list
- `name` (rw) — Return access to the name of the device.
- `parameters` (r) — Const access to the list of available automatable parameters for this device.
- `pitch_bend_range` (rw) — Return the current pitch bend range
- `pitch_mode` (rw) — Return the current pitch mode index
- `pitch_mode_list` (r) — Return the current pitch mode list
- `polyphony` (rw) — Return the current polyphony
- `type` (r) — Return the type of the device.
- `view` (r) — Representing the view aspects of a device.

methods:
- Representing the view aspects of a device.
- save_preset_to_compare_ab_slot( (Device)arg1) -> None : Saves the current state of the device to the compare AB slot. Only relevant if can_compare_ab, otherwise throws.
- store_chosen_bank( (Device)arg1, (int)arg2, (int)arg3) -> None : Set the selected bank in the device for persistency.

## Live.TakeLane.TakeLane
bases: LomObject
> This class represents a take lane in Live.

properties:
- `_live_ptr` (r) — 
- `arrangement_clips` (r) — Read-only access to the arrangement clips in the take lane.
- `canonical_parent` (r) — Get the canonical parent of the take lane.
- `name` (rw) — Read/write access to the name of the TakeLane, as visible in the take lane header.

methods:
- create_audio_clip( (TakeLane)arg1, (object)arg2, (float)arg3) -> Clip : Creates an audio clip referencing the file at the given path and inserts it into the arrangement at the specified time. Throws an error when called on a non-audio or a frozen track, when the specified time is outside the [0., 1576800.] range, when the track is currently being recorded into, or when the path doesn't point to a valid audio file.
- create_midi_clip( (TakeLane)arg1, (float)arg2, (float)arg3) -> Clip : Creates an empty MIDI clip and inserts it into the arrangement at the specified time. Throws an error when called on a non-MIDI track or a frozen track, when the specified time is outside the [0., 1576800.] range, or when the track is currently being recorded into.

## Live.Track.DeviceContainer
bases: LomObject
> This class is a common super class of Track and Chain

properties:
- `_live_ptr` (r) — 

## Live.Track.DeviceInsertMode
bases: enum
enum: default=0, selected_left=1, selected_right=2, count=3

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.Track.RoutingChannel
bases: instance
> This class represents a routing channel.

properties:
- `display_name` (r) — Display name of routing channel.
- `layout` (r) — The routing channel's Layout, e.g., mono or stereo.

## Live.Track.RoutingChannelLayout
bases: enum
enum: mono=1, stereo=2, midi=0

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.Track.RoutingChannelVector
bases: instance
> A container for returning routing channels from Live.

methods:
- append( (RoutingChannelVector)arg1, (object)arg2) -> None :
- extend( (RoutingChannelVector)arg1, (object)arg2) -> None :

## Live.Track.RoutingType
bases: instance
> This class represents a routing type.

properties:
- `attached_object` (r) — Live object associated with the routing type.
- `category` (r) — Category of the routing type.
- `display_name` (r) — Display name of routing type.

## Live.Track.RoutingTypeCategory
bases: enum
enum: external=0, rewire=1, resampling=2, master=3, track=4, parent_group_track=5, none=6, invalid=7

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.Track.RoutingTypeVector
bases: instance
> A container for returning routing types from Live.

methods:
- append( (RoutingTypeVector)arg1, (object)arg2) -> None :
- extend( (RoutingTypeVector)arg1, (object)arg2) -> None :

## Live.Track.Track
bases: DeviceContainer
> This class represents a track in Live. It can be either an Audio  track, a MIDI Track, a Return Track or the Main track. The Main  Track and at least one Audio or MIDI track will be always present. Return Tracks are optional.

properties:
- `_live_ptr` (r) — 
- `arm` (rw) — Arm the track for recording. Not available for Main- and Send Tracks.
- `arrangement_clips` (r) — const access to the list of clips in arrangement viewThe list will be empty for the main, send and group tracks.
- `available_input_routing_channels` (r) — Return a list of source channels for input routing.
- `available_input_routing_types` (r) — Return a list of source types for input routing.
- `available_output_routing_channels` (r) — Return a list of destination channels for output routing.
- `available_output_routing_types` (r) — Return a list of destination types for output routing.
- `back_to_arranger` (rw) — Indicates if it's possible to go back to playing back the clips in the Arranger.Setting a value 0 will go back to the Arranger playback. Setting on grouptracks will go back to the Arranger on all grouped tracks.
- `can_be_armed` (r) — return True, if this Track has a valid arm property. Not all tracks can be armed (for example return Tracks or the Main Tracks).
- `can_be_frozen` (r) — return True, if this Track can be frozen.
- `can_show_chains` (r) — return True, if this Track contains a rack instrument device that is capable of showing its chains in session view.
- `canonical_parent` (r) — Get the canonical parent of the track.
- `clip_slots` (r) — const access to the list of clipslots (see class AClipSlot) for this track. The list will be empty for the main and sendtracks.
- `color` (rw) — Get/set access to the color of the Track (RGB).
- `color_index` (rw) — Get/Set access to the color index of the track. Can be None for no color.
- `current_input_routing` (rw) — Get/Set the name of the current active input routing. When setting a new routing, the new routing must be one of the available ones.
- `current_input_sub_routing` (rw) — Get/Set the current active input sub routing. When setting a new routing, the new routing must be one of the available ones.
- `current_monitoring_state` (rw) — Get/Set the track's current monitoring state.
- `current_output_routing` (rw) — Get/Set the current active output routing. When setting a new routing, the new routing must be one of the available ones.
- `current_output_sub_routing` (rw) — Get/Set the current active output sub routing. When setting a new routing, the new routing must be one of the available ones.
- `devices` (r) — Return const access to all available Devices that are present in the Tracks Devicechain. This tuple will also include the 'mixer_device' that every Track always has.
- `fired_slot_index` (r) — const access to the index of the fired (and thus blinking) clipslot in this track. This index is -1 if no slot is fired and -2 if the track's stop button has been fired.
- `fold_state` (rw) — Get/Set whether the track is folded or not. Only available if is_foldable is True.
- `group_track` (r) — return the group track if is_grouped.
- `has_audio_input` (r) — return True, if this Track can be feed with an Audio signal. This is true for all Audio Tracks.
- `has_audio_output` (r) — return True, if this Track sends out an Audio signal. This is true for all Audio Tracks, and MIDI tracks with an Instrument.
- `has_midi_input` (r) — return True, if this Track can be feed with an Audio signal. This is true for all MIDI Tracks.
- `has_midi_output` (r) — return True, if this Track sends out MIDI events. This is true for all MIDI Tracks with no Instruments.
- `implicit_arm` (rw) — Arm the track for recording. When The track is implicitly armed, it showsin a weaker color in the live GUI and is not saved in the set.
- `input_meter_left` (r) — Momentary value of left input channel meter, 0.0 to 1.0. For Audio Tracks only.
- `input_meter_level` (r) — Return the MIDI or Audio meter value of the Tracks input, depending on the type of the Track input. Meter values (MIDI or Audio) are always scaled from 0.0 to 1.0.
- `input_meter_right` (r) — Momentary value of right input channel meter, 0.0 to 1.0. For Audio Tracks only.
- `input_routing_channel` (rw) — Get and set the current source channel for input routing. Raises ValueError if the type isn't one of the current values in available_input_routing_channels.
- `input_routing_type` (rw) — Get and set the current source type for input routing. Raises ValueError if the type isn't one of the current values in available_input_routing_types.
- `input_routings` (r) — Const access to the list of available input routings.
- `input_sub_routings` (r) — Return a list of all available input sub routings.
- `is_foldable` (r) — return True if the track can be (un)folded to hide/reveal contained tracks.
- `is_frozen` (r) — return True if this Track is currently frozen. No changes should be applied to the track's devices or clips while it is frozen.
- `is_grouped` (r) — return True if this Track is current part of a group track.
- `is_part_of_selection` (r) — return False if the track is not selected.
- `is_showing_chains` (rw) — Get/Set whether a track with a rack device is showing its chains in session view.
- `is_visible` (r) — return False if the track is hidden within a folded group track.
- `mixer_device` (r) — Return access to the special Device that every Track has: This Device contains the Volume, Pan, Sendamounts, and Crossfade assignment parameters.
- `mute` (rw) — Mute/unmute the track.
- `muted_via_solo` (r) — Returns true if the track is muted because another track is soloed.
- `name` (rw) — Read/write access to the name of the Track, as visible in the track header.
- `output_meter_left` (r) — Momentary value of left output channel meter, 0.0 to 1.0. For tracks with audio output only.
- `output_meter_level` (r) — Return the MIDI or Audio meter value of the Track output (behind the mixer_device), depending on the type of the Track input, this can be a MIDI or Audio meter. Meter values (MIDI or Audio) are always scaled from 0.0 to 
- `output_meter_right` (r) — Momentary value of right output channel meter, 0.0 to 1.0. For tracks with audio output only.
- `output_routing_channel` (rw) — Get and set the current destination channel for output routing. Raises ValueError if the channel isn't one of the current values in available_output_routing_channels.
- `output_routing_type` (rw) — Get and set the current destination type for output routing. Raises ValueError if the type isn't one of the current values in available_output_routing_types.
- `output_routings` (r) — Const access to the list of all available output routings.
- `output_sub_routings` (r) — Return a list of all available output sub routings.
- `performance_impact` (r) — Reports the performance impact of this track.
- `playing_slot_index` (r) — const access to the index of the currently playing clip in the track. Will be -1 when no clip is playing.
- `solo` (rw) — Get/Set the solo status of the track. Note that this will not disable the solo state of any other track. If you want exclusive solo, you have to  disable the solo state of the other Tracks manually.
- `take_lanes` (r) — returns the take lanes.
- `view` (r) — Representing the view aspects of a Track.

methods:
- Representing the view aspects of a Track.
- create_audio_clip( (Track)arg1, (object)arg2, (float)arg3) -> Clip : Creates an audio clip referencing the file at the given path and inserts it into the arrangement at the specified time. Throws an error when called on a non-audio or a frozen track, when the specified time is outside the [0., 1576800.] range, when the track is currently being recorded into, or when the path doesn't point to a valid audio file.
- create_midi_clip( (Track)arg1, (float)arg2, (float)arg3) -> Clip : Creates an empty MIDI clip and inserts it into the arrangement at the specified time. Throws an error when called on a non-MIDI track or a frozen track, when the specified time is outside the [0., 1576800.] range, or when the track is currently being recorded into.
- create_take_lane( (Track)arg1) -> LomObject : Create a new TakeLane for this track.
- delete_clip( (Track)arg1, (Clip)arg2) -> None : Delete the given clip. Raises a runtime error when the clip belongs to another track.
- delete_device( (Track)arg1, (int)arg2) -> None : Delete a device identified by the index in the 'devices' list.
- duplicate_clip_slot( (Track)arg1, (int)arg2) -> int : Duplicate a clip and put it into the next free slot and return the index of the destination slot. A new scene is created if no free slot is available. If creating the new scene would exceed the limitations, a runtime error is raised.
- duplicate_clip_to_arrangement( (Track)self, (Clip)clip, (float)destination_time) -> Clip : Duplicate the given clip into the arrangement of this track at the provided destination time and return it. When the type of the clip and the type of the track are incompatible, a runtime error is raised.
- duplicate_device( (Track)arg1, (int)arg2) -> None : Duplicate a device at a given index in the 'devices' list.
- get_data( (Track)arg1, (object)key, (object)default_value) -> object : Get data for the given key, that was previously stored using set_data.
- insert_device( (Track)arg1, (str)DeviceName [, (int)DeviceIndex=-1]) -> LomObject : Add a device at a given index in the 'devices' list. At end if -1.
- jump_in_running_session_clip( (Track)arg1, (float)arg2) -> None : Jump forward or backward in the currently running Sessionclip (if any) by the specified relative amount in beats. Does nothing if no Session Clip is currently running.
- `monitoring_states`
- set_data( (Track)arg1, (object)key, (object)value) -> None : Store data for the given key in this object. The data is persistent and will be restored when loading the Live Set.
- stop_all_clips( (Track)arg1 [, (bool)Quantized=True]) -> None : Stop running and triggered clip and slots on this track.

## Live.TuningSystem.PitchClassAndOctave
bases: instance
> This class represents a PitchClassAndOctave type.

properties:
- `index_in_octave` (r) — A PitchClassAndOctave's index within the pseudo octave.
- `octave` (r) — A PitchClassAndOctave's octave.

## Live.TuningSystem.ReferencePitch
bases: instance
> This class represents a ReferencePitch type.

properties:
- `frequency` (r) — A ReferencePitch's frequency in Hz.
- `index_in_octave` (r) — A ReferencePitch's index within the pseudo octave.
- `octave` (r) — A ReferencePitch's octave.

## Live.TuningSystem.TuningSystem
bases: LomObject
> Represents a Tuning System and its properties.

properties:
- `_live_ptr` (r) — 
- `canonical_parent` (r) — Get the canonical parent of the TuningSystem.
- `highest_note` (rw) — Get/Set the highest note of the current tuning system, where the first entry is the index within the pseudo octave and the second entry is the octave.
- `lowest_note` (rw) — Get/Set the lowest note of the current tuning system, where the first entry is the index within the pseudo octave and the second entry is the octave.
- `name` (rw) — Get/Set the name of the currently active tuning system.
- `note_tunings` (rw) — Get/Set the currently active tuning system's note tunings, specified in Cents, where 100 Cents is one semi-tone in equal temperament.
- `number_of_notes_in_pseudo_octave` (r) — Get the number of notes in the pseudo octave.
- `pseudo_octave_in_cents` (r) — Get the pseudo octave in cents for the currently active tuning system.
- `reference_pitch` (rw) — Get/Set the reference pitch the currently active tuning system.

methods:

## Live.WavetableDevice.EffectMode
bases: enum
enum: none=0, frequency_modulation=1, sync_and_pulse_width=2, warp_and_fold=3

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.WavetableDevice.FilterRouting
bases: enum
enum: serial=0, parallel=1, split=2

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.WavetableDevice.ModulationSource
bases: enum
enum: amp_envelope=0, envelope_2=1, envelope_3=2, lfo_1=3, lfo_2=4, midi_velocity=5, midi_note=6, midi_pitch_bend=7, midi_channel_pressure=8, midi_mod_wheel=9, midi_random=10

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.WavetableDevice.UnisonMode
bases: enum
enum: none=0, classic=1, slow_shimmer=2, fast_shimmer=3, phase_sync=4, position_spread=5, random_note=6

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.WavetableDevice.VoiceCount
bases: enum
enum: two=0, three=1, four=2, five=3, six=4, seven=5, eight=6, sixteen=7

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.WavetableDevice.Voicing
bases: enum
enum: mono=0, poly=1

properties:
- `denominator` (r) — the denominator of a rational number in lowest terms
- `imag` (r) — the imaginary part of a complex number
- `numerator` (r) — the numerator of a rational number in lowest terms
- `real` (r) — the real part of a complex number

methods:
- Return integer ratio. Return a pair of integers, whose ratio is exactly equal to the original int and with a positive denominator. >>> (10).as_integer_ratio() (10, 1) >>> (-10).as_integer_ratio() (-10, 1) >>> (0).as_integer_ratio() (0, 1)
- Number of ones in the binary representation of the absolute value of self. Also known as the population count. >>> bin(13) '0b1101' >>> (13).bit_count() 3
- Number of bits necessary to represent self in binary. >>> bin(37) '0b100101' >>> (37).bit_length() 6
- Returns self, the complex conjugate of any int.
- Return the integer represented by the given array of bytes. bytes Holds the array of bytes to convert. The argument must either support the buffer protocol or be an iterable object producing bytes. Bytes and bytearray are examples of built-in objects that support the buffer protocol. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byt
- Return an array of bytes representing an integer. length Length of bytes object to use. An OverflowError is raised if the integer is not representable with the given number of bytes. Default is length 1. byteorder The byte order used to represent the integer. If byteorder is 'big', the most significant byte is at the beginning of the byte array. If byteorder is 'little', the most significant byte is at the end of the

## Live.WavetableDevice.WavetableDevice
bases: Device
> This class represents a Wavetable device.

properties:
- `_live_ptr` (r) — 
- `can_compare_ab` (r) — Returns true if the Device has the capability to AB compare.
- `can_have_chains` (r) — Returns true if the device is a rack.
- `can_have_drum_pads` (r) — Returns true if the device is a drum rack.
- `canonical_parent` (r) — Get the canonical parent of the Device.
- `class_display_name` (r) — Return const access to the name of the device's class name as displayed in Live's browser and device chain
- `class_name` (r) — Return const access to the name of the device's class.
- `filter_routing` (rw) — Return the current filter routing.
- `is_active` (r) — Return const access to whether this device is active. This will be false bothwhen the device is off and when it's inside a rack device which is off.
- `is_using_compare_preset_b` (rw) — Returns whether the Device has loaded the preset in compare slot B. Only relevant if can_compare_ab, otherwise errors.
- `latency_in_ms` (r) — Returns the latency of the device in ms.
- `latency_in_samples` (r) — Returns the latency of the device in samples.
- `mono_poly` (rw) — Return the current voicing mode.
- `name` (rw) — Return access to the name of the device.
- `oscillator_1_effect_mode` (rw) — Return the current effect mode of the oscillator 1.
- `oscillator_1_wavetable_category` (rw) — Return the current wavetable category of the oscillator 1.
- `oscillator_1_wavetable_index` (rw) — Return the current wavetable index of the oscillator 1.
- `oscillator_1_wavetables` (r) — Get a vector of oscillator 1's wavetable names.
- `oscillator_2_effect_mode` (rw) — Return the current effect mode of the oscillator 2.
- `oscillator_2_wavetable_category` (rw) — Return the current wavetable category of the oscillator 2.
- `oscillator_2_wavetable_index` (rw) — Return the current wavetable index of the oscillator 2.
- `oscillator_2_wavetables` (r) — Get a vector of oscillator 2's wavetable names.
- `oscillator_wavetable_categories` (r) — Get a vector of the available wavetable categories.
- `parameters` (r) — Const access to the list of available automatable parameters for this device.
- `poly_voices` (rw) — Return the current number of polyphonic voices. Uses the VoiceCount enumeration.
- `type` (r) — Return the type of the device.
- `unison_mode` (rw) — Return the current unison mode.
- `unison_voice_count` (rw) — Return the current number of unison voices.
- `view` (r) — Representing the view aspects of a device.
- `visible_modulation_target_names` (r) — Get the names of all the visible modulation targets.

methods:
- Representing the view aspects of a device.
- add_parameter_to_modulation_matrix( (WavetableDevice)self, (DeviceParameter)parameter) -> int : Add a non-pitch parameter to the modulation matrix.
- get_modulation_target_parameter_name( (WavetableDevice)self, (int)target_index) -> str : Get the parameter name of the modulation target at the given index.
- get_modulation_value( (WavetableDevice)self, (int)target_index, (int)source) -> float : Get the value of a modulation amount for the given target-source connection.
- is_parameter_modulatable( (WavetableDevice)self, (DeviceParameter)parameter) -> bool : Indicate whether the parameter is modulatable. Note that pitch parameters only exist in python and must be handled there.
- save_preset_to_compare_ab_slot( (Device)arg1) -> None : Saves the current state of the device to the compare AB slot. Only relevant if can_compare_ab, otherwise throws.
- set_modulation_value( (WavetableDevice)self, (int)target_index, (int)source, (float)value) -> None : Set the value of a modulation amount for the given target-source connection.
- store_chosen_bank( (Device)arg1, (int)arg2, (int)arg3) -> None : Set the selected bank in the device for persistency.

## Module-level functions and constants
- `Live.Application.combine_apcs` (function) combine_apcs() -> bool : Returns true if multiple APCs should be combined.
- `Live.Application.encrypt_challenge` (function) encrypt_challenge( (int)dongle1, (int)dongle2 [, (int)key_index=0]) -> tuple : Returns an encrypted challenge based on the TEA algortithm
- `Live.Application.encrypt_challenge2` (function) encrypt_challenge2( (int)arg1) -> int : Returns the UMAC hash for the given challenge.
- `Live.Application.get_application` (function) get_application() -> Application : Returns the application instance.
- `Live.Application.get_random_int` (function) get_random_int( (int)arg1, (int)arg2) -> int : Returns a random integer from the given range.
- `Live.Base.get_text` (function) get_text( (str)classname, (str)textname) -> Text : Retrieves the (translated) Text identified by `classname` and `textname`.
- `Live.Base.log` (function) log( (str)arg1) -> None :
- `Live.Base.subst_args` (function) subst_args( (Text)text [, (str)arg1='' [, (str)arg2='' [, (str)arg3='' [, (str)arg4='' [, (str)arg5='']]]]]) -> str :
- `Live.Conversions.audio_to_midi_clip` (function) audio_to_midi_clip( (Song)song, (Clip)audio_clip, (int)audio_to_midi_type) -> None : Creates a MIDI clip in a new MIDI track with the notes extracted from the given audio_clip. The `audio_to_midi_type
- `Live.Conversions.create_drum_rack_from_audio_clip` (function) create_drum_rack_from_audio_clip( (Song)song, (Clip)audio_clip) -> None : Creates a new track with a drum rack with a simpler on the first pad with the specified audio clip.
- `Live.Conversions.create_midi_track_from_drum_pad` (function) create_midi_track_from_drum_pad( (Song)song, (DrumPad)drum_pad) -> None : Creates a new Midi track containing the specified Drum Pad's device chain.
- `Live.Conversions.create_midi_track_with_simpler` (function) create_midi_track_with_simpler( (Song)song, (Clip)audio_clip) -> None : Creates a new Midi track with a simpler including the specified audio clip.
- `Live.Conversions.is_convertible_to_midi` (function) is_convertible_to_midi( (Song)song, (Clip)audio_clip) -> bool : Returns whether `audio_clip` can be converted to MIDI. Raises error when called with a MIDI clip
- `Live.Conversions.move_devices_on_track_to_new_drum_rack_pad` (function) move_devices_on_track_to_new_drum_rack_pad( (Song)song, (int)track_index) -> LomObject : Moves the entire device chain of the track according to the track index onto the C1 (note 36) drum pad of a new
- `Live.Conversions.sliced_simpler_to_drum_rack` (function) sliced_simpler_to_drum_rack( (Song)song, (SimplerDevice)simpler) -> None : Converts the Simpler into a Drum Rack, assigning each slice to a drum pad. Calling it on a non-sliced simpler raises an error
- `Live.Licensing.authorization_clock_days_ahead` (function) authorization_clock_days_ahead() -> int : Advances the current date by the number of days specified by _AuthClockDaysAhead
- `Live.Licensing.get_authorization_page_url` (function) get_authorization_page_url( (bool)reauthorize, (bool)is_trial) -> str : Retrieves the appopriate URL on ableton.com where the unser can initiate the authorization.
- `Live.Licensing.get_purchase_live_url` (function) get_purchase_live_url() -> str : Returns the environment-aware purchase URL for purchasing Live licenses
- `Live.Licensing.get_services_url` (function) get_services_url() -> str : Returns the URL against which service calls (e.g. for authorization) can be performed.
- `Live.Licensing.get_unlock_dir` (function) get_unlock_dir() -> tuple : Returns a tuple containing the unlock file directory and a flag indicating if the unlock file is in the system domain.
- `Live.Licensing.launch_web_browser` (function) launch_web_browser( (str)url) -> None : Opens a web browser at the specified URL on the user's computer.
- `Live.MidiMap.forward_midi_cc` (function) forward_midi_cc( (int)arg1, (int)arg2, (int)arg3, (int)arg4 [, (bool)ShouldConsumeEvent=True]) -> bool :
- `Live.MidiMap.forward_midi_note` (function) forward_midi_note( (int)arg1, (int)arg2, (int)arg3, (int)arg4 [, (bool)ShouldConsumeEvent=True]) -> bool :
- `Live.MidiMap.forward_midi_pitchbend` (function) forward_midi_pitchbend( (int)arg1, (int)arg2, (int)arg3) -> bool :
- `Live.MidiMap.map_midi_cc` (function) map_midi_cc( (int)midi_map_handle, (DeviceParameter)parameter, (int)midi_channel, (int)controller_number, (MapMode)map_mode, (bool)avoid_takeover [, (float)sensitivity=1.0]) -> bool :
- `Live.MidiMap.map_midi_cc_with_feedback_map` (function) map_midi_cc_with_feedback_map( (int)midi_map_handle, (DeviceParameter)parameter, (int)midi_channel, (int)controller_number, (MapMode)map_mode, (CCFeedbackRule)feedback_rule, (bool)avoid_takeover [, (f
- `Live.MidiMap.map_midi_note` (function) map_midi_note( (int)arg1, (DeviceParameter)arg2, (int)arg3, (int)arg4) -> bool :
- `Live.MidiMap.map_midi_note_with_feedback_map` (function) map_midi_note_with_feedback_map( (int)arg1, (DeviceParameter)arg2, (int)arg3, (int)arg4, (NoteFeedbackRule)arg5) -> bool :
- `Live.MidiMap.map_midi_pitchbend` (function) map_midi_pitchbend( (int)arg1, (DeviceParameter)arg2, (int)arg3, (bool)arg4) -> bool :
- `Live.MidiMap.map_midi_pitchbend_with_feedback_map` (function) map_midi_pitchbend_with_feedback_map( (int)arg1, (DeviceParameter)arg2, (int)arg3, (PitchBendFeedbackRule)arg4, (bool)arg5) -> bool :
- `Live.MidiMap.send_feedback_for_parameter` (function) send_feedback_for_parameter( (int)arg1, (DeviceParameter)arg2) -> None :
- `Live.SimplerDevice.get_available_voice_numbers` (function) get_available_voice_numbers() -> IntVector : Get a vector of valid Simpler voice numbers.
- `Live.Song.get_all_scales_ordered` (function) get_all_scales_ordered() -> tuple : Get an ordered tuple of tuples of all available scale names to intervals.
