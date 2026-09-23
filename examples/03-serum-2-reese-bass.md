# 3 · A Reese bass in Serum 2

> **You:** Load Serum 2 on a new track and design a dark Reese bass for drum & bass: two
> detuned saw stacks, a sub, a low-pass filter I can ride on macro 1 and detune on macro 2.
> Then give it a slow filter wobble.

|  |  |
|---|---|
| **Shows** | a VST3 plug-in made fully controllable without clicking *Configure*, 16 parameters set by their display values, rack macros, notes, an LFO-shaped clip envelope |
| **Needs** | Serum 2 by [Xfer Records](https://xferrecords.com/) (VST3) · any Live 12 edition |
| **Tool calls** | 5 |

## Why this is special

Ableton Live shows a plug-in's parameters only after you add them one by one with the device's
**Configure** button, and Live's API can't do that for you: a freshly loaded Serum 2 exposes 0
of its 2,623 parameters. LiveBridge gets around it by writing a rack preset that already lists
the parameters it needs, then loading Serum inside it
([how it works](../docs/PLUGIN_RACKS.md)).

## What Claude does

**1. Load Serum 2 with 120 sound-design parameters exposed, and wire two macros.**

```python
live_plugin_expose(plugin="Serum 2", parameters=["sound_design"], new_track=true,
                   track_name="Reese", macros={"1": "Filter 1 Freq", "2": "A Uni Detune"},
                   editor_open=false)
```

```json
{"status": "done", "track": "Reese", "rack_name": "Serum 2 Rack",
 "device": "song.tracks[3].devices[0].chains[0].devices[0]",
 "plugin": "Serum 2", "format": "VST3", "exposed_count": 120,
 "parameters": [{"name": "A Unison", "display": "1"}, {"name": "Filter 1 Type", "display": "MG Low 12"},
                {"name": "Filter 1 Freq", "display": "1011 Hz"}, {"name": "Main Vol", "display": "50% [-9.0 dB]"}, ...],
 "macros": {"1": {"parameter": "Filter 1 Freq"}, "2": {"parameter": "A Uni Detune"}}}
```

**2. Design the sound, with the same values you would type into Serum.**

```python
live_device_set_parameters(track="Reese", device="song.tracks[3].devices[0].chains[0].devices[0]",
                           values={
    "A Unison": "4",           "B Enable": "On",          "B Fine": "15 cents",
    "B Unison": "4",           "Sub Enable": "On",        "Sub Level": "60%",
    "Filter 1 On": "On",       "Filter 1 Type": "MG Low 24",
    "Filter 1 Res": "20 %",    "Filter 1 Drive": "30 %",
    "Mono Toggle": "On",       "Legato": "On",            "Porta Time": "40 ms",
    "Env 1 Attack": "5 ms",    "Env 1 Release": "120 ms", "Main Vol": "-6 dB",
})
```

All 16 are validated first and written as one undo step. Serum reads them back as
`"B Fine": "15 cents"`, `"Filter 1 Type": "MG Low 24"`, `"Main Vol": "60% [-6.0 dB]"`.

**3. Set the two macros: filter fairly closed, a wide detune.**

```python
live_rack_macros(track="Reese", values={"Filter 1 Freq": 50, "A Uni Detune": 80})
```

**4. A 4-bar Reese line in F minor.**

```python
live_clip_add_notes(track="Reese", slot=0, notes=[
    ["F1", 0, 3], ["F1", 3.5, 0.5], ["G#1", 4, 2], ["G1", 6, 2],
    ["F1", 8, 3], ["C2", 11.5, 0.5], ["D#1", 12, 4],
])
```

**5. A slow filter wobble: a sine on macro 1, one cycle every 2 bars.**

```python
live_automation_shape(track="Reese", slot=0, device="Serum 2 Rack", parameter="Filter 1 Freq",
                      shape="sine", period="2 bars", low=0.25, high=0.6, mode="events")
```

`device="Serum 2 Rack"` targets the macro rather than Serum's own knob of the same name.
`mode="events"` writes smooth breakpoints instead of steps.

## What you get

A Serum 2 Reese on its own track that Claude can keep tweaking (*"more grit"*, *"open the
filter in the second half"*), and that you can play with macros 1 and 2 or a MIDI controller.

## Take it further

- *"Start from one of my Serum presets instead."*
  ```python
  live_plugin_preset_files(plugin="Serum 2", filter="bass")
  ```
  then `live_plugin_expose(..., preset_file=<one of the files it lists>)`.
- *"Use another VST3 synth."* → `live_plugin_param_map(plugin="<name>")` maps it once
  (about 30 s), after which `live_plugin_expose` works the same way.
- Audio Units and VST2 plug-ins can't be exposed this way; use the VST3 version or the
  *Configure* button ([details](../docs/PLUGIN_RACKS.md)).
