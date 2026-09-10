# Plug-in racks — full Serum 2 (and any VST3) control without Configure

Live 12.4.5 only exposes the plug-in parameters in a device's **Configure** list, and for big
plug-ins that list starts empty: Serum 2 exposes 0 of its 2623 parameters after loading, and
no API adds to it (`docs/live_test/T4-plugins.md`). LiveBridge gets around this without the
user: it **writes a rack preset** that lists the wanted parameters and lets Live load it.

Files: `remote_script/LiveBridge/handlers/plugin_racks.py` (bridge commands),
`remote_script/LiveBridge/plugin_racks_lib.py` (XML, presets, scanning, prober; stdlib only),
`remote_script/LiveBridge/plugin_maps/*.json` (shipped maps), `mcp_server/livebridge_mcp/
tools/plugin_racks.py` (MCP tools), `tests/test_plugin_racks.py` +
`tests/live_stub_ext/plugin_racks.py` (tests + the stub's Live behaviour).

## How it works

A rack preset (`.adg`, gzip XML) stores a plug-in's Configure list as
`<PluginParameterSettings>` entries. Each entry holds only the plug-in's own **ParameterId**
(the VST3 `ParamID`). Live resolves the names itself when it loads the preset.

1. **Identify the plug-in.** The VST3 class id comes from
   `<bundle>.vst3/Contents/Resources/moduleinfo.json` ("Audio Module Class"). The fallback is
   Live's own plug-in database (`Live-plugins-1.db`). Live's Python has no `_sqlite3`, so the
   file is read as raw SQLite records. Live's `<Uid>` = the class id as 4 big-endian int32
   (Serum 2 `56534558667350736572756D20320000` → 1448297816, 1718833267, 1701999981,
   540147712). Folders:
   - macOS: `/Library/Audio/Plug-Ins/VST3` and `~/Library/Audio/Plug-Ins/VST3`
   - Windows: `%COMMONPROGRAMFILES%\VST3` and `%LOCALAPPDATA%\Programs\Common\VST3`
   - database: `~/Library/Application Support/Ableton/Live Database` (macOS) or
     `%LOCALAPPDATA%\Ableton\Live Database` (Windows)
   - extra folders: `LIVEBRIDGE_VST3_PATH`
2. **Map names to ParameterIds** (`plugin_racks.map`). The map is shipped for Serum 2 and
   Serum 2 FX. For other plug-ins it is probed once and cached in
   `<User Library>/LiveBridge/Maps/<plugin>_vst3.json`.
3. **Write the rack.** The plug-in goes into an Instrument Rack (`InstrumentGroupDevice`),
   or into an Audio Effect Rack (`AudioEffectGroupDevice`) for effects. The rack holds the
   chosen ids (max 128), optional macro mappings and the plug-in state. It is written to
   `<User Library>/LiveBridge/Racks/LB <plugin>.adg`. The User Library is the folder two
   levels above the Remote Script, else it is read from `Library.cfg`
   (`LIVEBRIDGE_USER_LIBRARY` overrides both).
4. **Load it through Live's browser.** Live lists the new file after its indexer saw it
   (~3–5 s). Until then the command answers `status: "indexing"` and the MCP tools poll.
   To replace a device, the old top-level device is deleted and the rack is inserted at its
   position.

## Commands and tools

| bridge command | MCP tool | what |
|---|---|---|
| `plugin_racks.expose` | `live_plugin_expose` | load the plug-in in a generated rack exposing up to 128 parameters (names, group keywords, `"sound_design"`), optional `macros`, `preset_file`, `replace`, `new_track`, `editor_open` |
| `plugin_racks.map` | `live_plugin_param_map` | shipped / cached map, or probe a new plug-in (status `running` → `done`); filter + page the names |
| `plugin_racks.presets` | `live_plugin_preset_files` | preset files on disk (`.vstpreset`, Live presets holding the plug-in, `.SerumPreset`) |

(`live_plugin_presets` already exists: it lists Live's program list of a loaded plug-in. The
disk listing therefore has its own tool, `live_plugin_preset_files`.)

Claude's flow for Serum 2:

1. `live_plugin_expose(plugin="Serum 2", parameters=["sound_design"], new_track=true)`
2. `live_device_set_parameters(device=<returned device path>, values={"Filter 1 Freq": "800 Hz", "Env 1 Attack": "20 ms", "A WT Pos": 0.5, ...})`
3. `live_automation_write(...)` on the same parameters.

For another big VST3 plug-in, call `live_plugin_param_map` once, then expose it.

## Serum 2 facts (VST3 v2.1.5, probed on Live 12.4.5)

- **Parameter ids.** `ParameterId = block * 1_000_000 + instance * 1000 + index`, with the
  index in `get_parameter_names()` order inside a block.
  - block 0: global (Main Vol 0 … Bus 2 Vol 20)
  - block 1: oscillators, 55 each. A = 1000000.., B = 1001000.., C = 1002000... Noise
    (1003000..) and Sub (1004000..) reuse the oscillator indices sparsely: Noise Pitch Track is
    1003009.
  - block 2: Filter 1 / 2. The order differs from the name list: On 0, Wet 1, Type 2, Freq 3.
  - block 3: Env 1–4. Block 4: LFO 1–10. Block 6: Mod 1–64 (Amount, Out). Block 7: Macro 1–8
    (7000000, 7001000 …).
  - block 9: routing. Block 10: clip player. Block 12: arp. Block 14: key/scale. Block 15:
    randomization. Block 17: FX Main / Bus 1 / Bus 2 Param 1–16.
  - Id 9 shows as "Bank Prog" in the device, "Bank" in `get_parameter_names()`.
  - An unknown id shows as `Parameter #<slot>`.
- **Counts.** 541 synth parameters are mapped, with nothing missing. The other 2082 names are
  VST3 MIDI proxies ("CCn Chan m", "Pitch Bend Chan n", "Aftertouch Chan n", and a second "Mod
  Wheel" / "Pitch Bend" at the end of the list); they are skipped.
- **Serum 2 FX** has the same 541 parameters with the same ids (shipped as
  `serum2fx_vst3.json`).
- **Groups** (in the map): `sound_design` (120 curated controls), `osc`, `osc a|b|c`, `sub`,
  `noise`, `filter`, `filter 1|2`, `env`, `lfo`, `macros`, `fx`, `unison`, `routing`, `mod`,
  `arp`, `global`, `clip`. Any words that start several names act as a group for any map:
  "filter 1" gives every "Filter 1 …" parameter.
- **Template state.** `template_state` in the map is Serum's "- Init -" patch (the `Comp` /
  `Cont` chunks of a Live-saved Serum 2 rack). An exposure without `preset_file` starts from
  it.
- **Presets.** 626 factory presets (`.SerumPreset`) sit in
  `/Library/Audio/Presets/Xfer Records/Serum 2 Presets/Presets` on macOS. On Windows, look
  in `%USERPROFILE%\Documents\Xfer\Serum 2 Presets` or `%PUBLIC%\Documents\Xfer\Serum 2
  Presets`.

## What real Live did (2026-09-10, Live 12.4.5 Suite, macOS arm64)

| experiment | result |
|---|---|
| generated `.adg` with 13 ids (orchestrator), then 128 ids | **PASS**: names resolved, invalid ids → `Parameter #n` |
| **256 `PluginParameterSettings`** | **Live crashed** (log ends after "VST3: parameter count is 2623"). The 128 limit is enforced in `rack_xml`, and the stub raises `LiveWouldCrash`. |
| **`MacroControlIndex` with an empty `<MidiControllerRange />`** | **Live crashed**. The field is `ASlot<AMidiControllerRange>` (found in the Live binary). |
| `<MidiControllerRange><Min/><Max/></MidiControllerRange>` | "Unknown class 'Min'": the document is reported as corrupt (no crash) |
| `<MidiControllerRange><MidiControllerRange Id="0"><Min/><Max/>…` | **PASS**: macros are mapped (`macros_mapped` true). Min/Max are in the plug-in's value units (Hz, ms, %) and are clamped to the parameter's range. A range of -1e9..1e9 makes macro/127 = the normalized value: measured on Freq, Attack, Release, Main Vol, Pan, Type, Unison (0, 31.75, 63.5, 95.25, 127 → 0, .25, .5, .75, 1). |
| macro value on load | Live does not push macro values when loading, so the parameter keeps the preset value. `expose` then moves each macro to its parameter's value. Macro moves reach the plug-in on the next tick. |
| Audio Effect Rack (`AudioEffectGroupDevice` + `AudioEffectBranchPreset`, no `ZoneSettings`) with Serum 2 FX | **PASS**, on an audio track |
| AU (`<AuPreset>` with `Manufacturer` / `SubType` / `Type`, empty `<Buffer>`) | Serum 2 AU **loads**, but Live exposes **none** of the ids (tried 0..7 and Serum ids). Exposing is VST3-only and AU returns `unsupported`. |
| empty `<ProcessorState/>` | PASS: the plug-in starts from its constructor default (Serum: Filter 1 Freq 425 Hz, not Init's 1011 Hz) |
| `.vstpreset` `Comp` / `Cont` vs Live's `<ProcessorState>` / `<ControllerState>` | byte-identical. The Xfer container is `XferJson\0`, u64 length, JSON header (`hash` = MD5 of the zstd frame), u32 size, u32 2, then the zstd frame. |
| `.SerumPreset` as a processor state (verbatim, and re-wrapped with a processor header and a valid hash) | **Refused**: "VST3: couldn't set processor state: false". `.SerumPreset` files are listed but marked `embeddable: false`. |
| new file → browser | appears after ~3–5 s. `LiveBridge/Racks` is shown because `LiveBridge` is a top-level User Library folder. |
| **rewriting an indexed file, then loading at once** | **PASS**: Live reads the file on load. Probing reuses one file, and exposures reuse `LB <plugin>.adg`. |
| rack name | taken from the file name. `UserName` is ignored, so `expose` renames the rack afterwards (`device.name` is writable). |
| **hot-swapping** a LiveBridge rack with another one for the same plug-in | Live keeps the old plug-in instance ("preset transfer", no "Going to create" in Log.txt): neither the new Configure list nor the new state were applied reliably. `replace` therefore deletes and inserts. |
| plug-in window | Live opens it on the tick after a rack load. `live_plugin_expose(editor_open=false)` closes it with a second command: still closed 1 s later. |
| load time | about 0.3 s per probe rack with Serum 2 |

End-to-end proof, run through the MCP tools in-process against the real bridge:

- `live_plugin_param_map(plugin="Serum 2", refresh=true)`: 541 mapped, 0 missing, 27 probe
  loads, 2939 ids, 12.1 s probing (12.4 s including the indexing wait). The result is
  identical to the shipped map.
- `live_plugin_param_map(plugin="Serum 2 FX")`: the same 541 parameters and ids, in 9.3 s.
- `live_plugin_expose(plugin="Serum 2", parameters=["sound_design"], new_track=true,
  track_name="LB_SERUM_DESIGN")`: 120 parameters exposed, state `template`.
- `live_device_set_parameters` set 16 parameters, and each read back with
  `live_device_get_parameter`: Filter 1 On "On", Filter 1 Freq "800 Hz", Res "35 %", Env 1
  Attack "20 ms", Release "400 ms", Sustain "-6.0 dB", A WT Pos 0.5, A Unison "4", A Uni Detune
  0.3, B Enable "On", B Octave "-1 oct", Sub Enable "On", Sub Level 0.6, Porta Time "50 ms",
  Main Vol 0.62, LFO 1 Rate "1/8".
- The clip test used a 4-beat clip with 8 notes:
  - `live_automation_write` wrote Filter 1 Freq as a linear ramp: "300 Hz" → "4000 Hz" →
    "300 Hz".
  - `live_automation_get` read it back as 300, 573, 1095, 2093, 4000, 2093, 1095, 573 Hz.
  - The clip was fired and played (`playing_position` 1.24). The track meter read 0.92.
    Filter 1 Freq followed the envelope (2461 Hz). The clip was then stopped.
- `live_plugin_preset_files(plugin="Serum 2", embeddable_only=true)` returned
  `User Library/Serum 2.vstpreset` and one Live preset. With `filter="bass reese"` it found 8
  factory `.SerumPreset` files.
- `live_plugin_expose(..., preset_file="…/Serum 2.vstpreset", macros={1: Filter 1 Freq,
  2: Filter 1 Res, 3: A WT Pos, 4: Env 1 Attack})` replaced the rack. The state came from the
  file. The 4 macros were named and wired, and each synced to its parameter (77.4 = 0.6096 ×
  127).
- Moving the macros to 100 / 30 / 127 / 64 moved the parameters to 0.7872 (4112 Hz) /
  0.2362 / 1.0 / 0.5039 (1.04 s). The expected value is macro/127 each time.
- A plain Serum 2 loaded through the browser, between an Arpeggiator and a Reverb, was
  replaced by the rack at the same position.
- Serum 2 FX was exposed in an Audio Effect Rack on an audio track, with 2 macros.
- Cleanup: every `LB_` track and generated file was deleted, including the orchestrator's
  `LB_SERUM_TEMPLATE` / `LB_PROBE_A` / `LB_PROBE_B` tracks and its
  `User Library/LiveBridge/LB_probe_*` files.

## Limits

- **VST3 only.** AU racks load, but Live exposes nothing through them. VST2 is not generated.
- **At most 128 parameters per plug-in device**: more crashed Live. Expose several racks, or
  re-expose a different set.
- **The current sound is lost on replace.** Live's API cannot read a plug-in's state, so a
  replaced or re-exposed plug-in restarts from `preset_file` or the template. Expose first,
  then design.
- **`.SerumPreset` files cannot be embedded.** Load them in Serum's browser
  (`live_plugin_set editor_open=true`), or save the sound as a Live preset (`.adv`) or
  `.vstpreset` and pass that file.
- **Some ids cannot be probed.** Plug-ins with hashed ParameterIds (some JUCE builds) give 0
  results, and `map` says so. Such plug-ins need a map written by hand (the same JSON format).
- **Windows is unverified.** Paths and scanning are implemented and unit-tested; real-Live
  verification was done on macOS only. The class-id → `<Uid>` mapping is assumed to be
  platform independent.
