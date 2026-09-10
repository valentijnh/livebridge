# Real-Live test T4 — third-party plug-ins (Serum 2, Apple AUs), plug-in browser, Splice folder

Tested 2026-09-10 on **Ableton Live 12.4.5 Suite, macOS (arm64), Python 3.11.6 inside Live**,
right after the user enabled Audio Units v2/v3 and the VST2/VST3 system folders. Every command
was called on the real Live through `tests/live_query.py`, the MCP tools in-process through
`BridgeClient` + `create_app`. Other testers worked in the same Live at the same time; only
tracks named `LB_PLUG …` were created and all were deleted at the end. At most one Serum 2
instance and at most three plug-in devices were loaded at any moment; FL Studio and MPC Beats
(heavy) and the Splice Sounds plug-in (needs a login) were never loaded.

Legend: **PASS** works as documented · **FIXED** real Live differed, handler/tool/tests fixed ·
**UNSUPPORTED** cannot be done through Live's API · **FAIL** still broken.

## Headline for the user

* Loading works: `live_browser_load_plugin("Serum 2")` picks **VST3** Serum 2 (not "Serum 2 FX",
  not the AU), `plugin_format="au"` picks the AU, "Xfer Records/Serum 2" and "serum2" work,
  `live_browser_load_effect("Serum 2")` picks Serum 2 FX.
* **Live exposes none of Serum 2's parameters after loading — VST3 and AU alike (0 of 2623 /
  2622).** Live 12.4.5 does *not* expose all automatable VST3 parameters automatically; it only
  exposes the device's **Configure** list, which Live fills by itself only for small plug-ins
  (Apple AUNBandEQ 41/41, AUMIDISynth 4/4, DLSMusicDevice 3/3, but AUMatrixReverb 2/17 and AUDelay
  3/4) and leaves empty for big ones. No API can add to that list (checked the 12.4.5 dump and
  `dir()` of the real `PluginDevice`: no configure method). The user has to do it **once per
  device**: click **Configure** in the Serum 2 device title bar in Live, move the wanted knobs in
  Serum's window, click Configure again, then save the set or the device as a Live preset (.adv)
  so the list is kept. `live_plugin_configure` now lists the exact Serum names to move
  (`missing`), explains the steps and waits until they appear. After that every exposed Serum
  parameter works with the normal device/automation tools (set by name/index/display string,
  set-many, reset, randomize, clip automation) — proven on the exposed Apple AU parameters, which
  are the same `PluginDevice` code path.
* Presets: Live lists only `["Default"]` for Serum 2 (VST3 + AU) and every Apple AU tried —
  plug-in presets are not reachable through the API; use Serum's own browser (plug-in window),
  Live .adv presets, or `~/Splice/presets`.
* The plug-in window can be opened/closed: `PluginDevice.is_editor_open` (rw) works
  (`live_plugin_set editor_open`, and `live_browser_load_plugin(editor_open=false)`).

## Bridge commands

### plugins.* (handlers/plugins.py)

| command | result | notes |
|---|---|---|
| `plugins.list` | FIXED | lists VST3/AU devices with format, **vendor** (new, from the browser's vendor folder: "Xfer Records", "Apple"), `parameter_count` (exposed) and new `plugin_parameter_count` (all the plug-in has); `needs_configure` now only lists plug-ins that have parameters but expose none (Serum 2). `installed`: 33 plug-ins, AU 32 / VST3 4 / VST2 2 (names in several formats are counted once) |
| `plugins.get` | FIXED | real Live's `get_parameter_names()` returns **every** plug-in parameter (Serum 2: 2623), not the Configure list as assumed → `parameter_names` reported 2623 names while nothing was settable. Now: `parameter_names` = exposed, `plugin_parameter_count`, `not_exposed`, `vendor`, `configure_hint` rewritten (the old "VST3 plug-ins usually expose their parameters already" was false) |
| `plugins.presets` | FIXED | Serum 2 VST3/AU and all Apple AUs report `["Default"]` (AU factory presets of AUMatrixReverb/DLSMusicDevice are not listed) → a `note` explains where presets live instead |
| `plugins.set` | PASS | `preset` "Default"/0 PASS, index 1 → not_found; `editor_open` true/false verified on the real window (`is_editor_open` read back); non-bool `editor_open` now refused before touching the preset |
| `plugins.parameters` | FIXED | new `scope="all"`: pages through all 2623 Serum names with `exposed`, Live `index`, value/display when exposed; `filter` now also matches words + synonyms ("cutoff" → Filter 1 Freq, Filter 2 Freq, Cutoff Rand; "osc a wavetable position" → A WT Pos; "master volume" → Main Vol); `exposed_count`, `plugin_parameter_count` |
| `plugins.exposure` | FIXED (new) | which wanted parameters are exposed, their Live index and display, `missing` (exact Serum names to move in Configure mode), `candidates` for ambiguous names, the steps. Real Serum 2: 14 wanted names (filter 1 cutoff/resonance, env 1 ADSR, lfo 1 rate, macro 1/2, osc a level/wavetable position/warp, main vol, fx main param 1) all resolved to the real names, all reported missing |

### devices.* on plug-in devices (handlers/devices.py, plug-in-specific part)

| command | result | notes |
|---|---|---|
| `devices.get_parameter` / `set_parameter` / `set_parameters` | FIXED | exposed AU parameters: display strings with the plug-in's units verified — AUDelay "2 kHz"/"2000 Hz" (Lowpass Cutoff Frequency), "30 %"/"30%", "-20 %" (Feedback), normalized 0.25; AUMIDISynth "-6 dB", "12 st" → "+12 st", "-25 ct", "-20" → "20.00L"; AUNBandEQ "1 kHz", "-3 dB", "+2 dB", "Low Shelf" (value item). **Fixed:** (1) AUNBandEQ names its eight bands' parameters identically ("Frequency", "Gain", "Bandwidth", "Type", "Bypass") — the exact name silently hit band 1; now a duplicate exact name is `bad_args` listing the indices and `"Frequency #3"` / `"bw #2"` address the n-th one; (2) a parameter the plug-in has but Live does not expose (Serum's "Filter 1 Freq", AUDelay's "Delay Time") now fails with "… is a parameter of 'Serum 2' but Live does not expose it (0 of 2623 …)" + the Configure steps instead of a bare not_found; (3) plug-in synonyms as a last tier ("master volume" → AUNBandEQ "Global Gain", "cutoff" → the only exposed "…Freq", "env 1 atk") |
| `devices.reset_parameters` | PASS | AUMIDISynth: all four back to default ("0.00 dB", "0 st", "0 ct", "0"); Serum 2 with nothing exposed → empty `reset` |
| `devices.randomize` | PASS | subset with seed/amount on AUMIDISynth (Fine Tuning, Stereo Pan); Serum 2 with nothing exposed → `changed: 0` |
| `devices.get` / `devices.parameters` | PASS | Serum 2 VST3: class_name `PluginDevice`, AU: `AuPluginDevice`, `is_plugin: true`, `device_kind: plugin`, `plugin` sub-dict (presets, editor); `parameters` = ["Device On"] |

### browser.* for plug-ins (handlers/browser.py, plug-in-specific part)

| command | result | notes |
|---|---|---|
| `browser.search` (root/category plugin) | FIXED | real tree: `plugins/AUv2/<vendor>/<name>`, `plugins/VST/<name>` (uri `…#VST:Local:<name>`), `plugins/VST3/<vendor>/<name>`; plug-in items are `is_loadable` but **`is_device` False**. "Serum 2" → VST3 first, AU second, then the FX. **Fixed:** vendor-qualified queries ("Xfer Records/Serum 2", "xfer/serum 2 fx") score the name part; space-free names ("serum2"); Live's plug-in items carry no device type, so with `category="audio_effect"` names with "FX"/"Effect" win (live_browser_load_effect("Serum 2") → Serum 2 FX, live_browser_load_instrument("Serum 2") → Serum 2) |
| `browser.load` (plug-ins) | PASS | Serum 2 VST3 onto the MIDI track `LB_PLUG Serum` → `song.tracks[4].devices[0]`, class `PluginDevice`, type instrument; AU Serum 2 with `plugin_format="au"` → `AuPluginDevice`; Serum 2 FX VST3 onto an audio track as audio_effect; AUDelay / AUNBandEQ / AUMatrixReverb on an audio track; DLSMusicDevice then AUMIDISynth on a MIDI track (instrument replaced, `removed` reported). Live opens the plug-in window on every load ("Auto-Open Plug-In Windows") |

## MCP tools (in-process against the real bridge)

| tool | result |
|---|---|
| `live_browser_load_plugin` | FIXED — new `editor_open` (closes Serum's window right after loading, verified `editor_open: false`), docstring: vendor-qualified names, VST3 default, Serum exposes nothing after loading |
| `live_browser_load_instrument` / `live_browser_load_effect` | PASS — "serum2" → Serum 2 VST3 instrument; "Serum 2" as effect → Serum 2 FX VST3 |
| `live_plugin_list`, `live_plugin_get`, `live_plugin_presets`, `live_plugin_set` | PASS (docstrings rewritten with the real facts) |
| `live_plugin_parameters` | FIXED — `scope` ("exposed" / "all"), synonym filter |
| `live_plugin_configure` | FIXED (new) — guided Configure: resolves the wanted names, returns `missing` + steps; with `wait_seconds` polls until all are exposed (`newly_exposed`, `timed_out`); real Live: timeout path (3.2 s, nothing exposed — nobody clicked Configure). The success path (parameters appearing while it waits) needs a human in Live's UI; covered by `tests/test_plugins_live.py` with the stub |
| `live_device_*` on plug-ins | PASS / FIXED as in the device table |
| `live_automation_write/get` on plug-ins | PASS — see the music check |
| `live_splice_setup_info` | FIXED — documents `<Splice folder>/presets` (Serum presets from Splice) |

## Music check (Serum 2 VST3 + AUMIDISynth)

1. `notes.write_pattern` wrote an 8-note C3/D#3/G3 pattern into a new 4-beat clip on
   `LB_PLUG Serum` (Serum 2 VST3); `notes.write_chords` Cm–Ab–Eb–Bb into `LB_PLUG Synth`
   (AUMIDISynth). PASS.
2. Automation: `automation.write` of Serum 2's "Device On" (step On/Off/On) and of AUMIDISynth
   "Gain" (linear "-12 dB" → "0 dB", 9 steps) read back with `automation.get`
   ("-12.00 dB", "-10.50 dB", "-7.50 dB", "-6.00 dB", "-3.00 dB"; Device On … "Off" at beat 3).
   `automation.write` on "filter 1 cutoff" fails with the not-exposed/Configure message — Serum
   parameters can only be automated after Configure. PASS / UNSUPPORTED (Serum params).
3. Both clips fired: `is_playing` true, `playing_position` 2.92, **Serum 2 output meter 0.84**
   (it really sounds with its init patch), AUMIDISynth 0.81; "Coarse Tuning" set to "+7 st"
   while playing (automated Gain showed `automation_state` 1 = playing). Both clips stopped
   (`playing_slot_index` −2). The transport had been stopped and firing a clip starts it, so it
   was stopped again right after (no other track was playing); the song position moved to
   beat 13.2. PASS.

## Live 12.4.5 facts learned

* `Live.PluginDevice.PluginDevice`: `class_name` "PluginDevice" (VST2 and VST3) /
  "AuPluginDevice" (AU); `class_display_name` = plug-in name ("Serum 2"); `can_compare_ab`
  false; `latency_in_samples` 0 for Serum 2 and the Apple AUs; `is_editor_open` rw;
  `presets` ["Default"], `selected_preset_index` 0; `get_parameter_names(begin=0, end=-1)` =
  all plug-in parameters (no "Device On"), **dynamic** (AUNBandEQ band 4 switched to "Low Shelf"
  drops that band's "Bandwidth" from the list: 41 → 40 names, 41 exposed parameters stay).
  No method to configure parameters; `view` only has `is_collapsed`.
* Serum 2 v2.1.5 VST3 (`/Library/Audio/Plug-Ins/VST3/Serum2.vst3`, CID
  `56534558667350736572756D20320000`): 2623 names = 541 synth parameters + 16 × 130 MIDI proxies
  ("Pitch Bend Chan n", "Aftertouch Chan n", "CC0 Chan n" … "CC127 Chan n") + "Mod Wheel",
  "Pitch Bend". AU: 2622 (no "Bypass", has "Bank" earlier). The real names (exact list in
  `tests/live_stub_ext/plugins_live.py:serum2_names`): "Main Vol", "Main Tuning", "Amp",
  "Porta Time", "Transpose", "Direct Vol", "Bus 1 Vol", "Bus 2 Vol"; oscillators "A Level",
  "A Pan", "A Octave", "A Semi", "A Fine", "A Unison", "A Uni Detune", "A Uni Blend",
  "A WT Pos", "A Warp", "A Warp 2", "A Phase", … (55 each for A/B/C, incl. "A Param44…55"),
  "Noise Level", "Sub Level"; "Filter 1 Freq", "Filter 1 Res", "Filter 1 Drive", "Filter 1 Wet",
  "Filter 1 Type", … (and Filter 2); "Env 1 Attack/Hold/Decay/Sustain/Release" (Env 1–4);
  "LFO 1 Rate/Smooth/Rise/Delay/Phase" (LFO 1–10); "Mod 1 Amount/Out" (1–64); "Macro 1…8";
  routing "A>BUS1", "A>Filter Balance"; FX slots are generic: "FX Main Param 1…16",
  "FX Bus 1 Param 1…16", "FX Bus 2 Param 1…16" (there is no named "FX mix"); "Arp …",
  "Clip Player …", "Key", "Scale". All exposed = 0 until Configure. Serum 2 factory presets:
  `/Library/Audio/Presets/Xfer Records/Serum 2 Presets/Presets` (626 `.SerumPreset`) — not
  loadable through Live's API.
* Apple AUs: AUDelay exposes Dry/Wet Mix ("50 %"), Feedback ("50 %" at 0.75 = −100…100 %),
  Lowpass Cutoff Frequency ("15000 Hz") but not "Delay Time"; AUMatrixReverb 2 of 17
  (Dry/Wet Mix, Small/Large Mix); AUNBandEQ all 41 ("0.00 dB", "40.0 Hz", "0 Octaves" — the
  display rounds octaves to integers while the value is exact, 1.5 octaves shows "2 Octaves");
  AUMIDISynth Gain "0.00 dB", Coarse Tuning "0 st", Fine Tuning "0 ct", Stereo Pan "0";
  DLSMusicDevice Tuning "0 ct", Volume / Reverb Volume "0.00 dB". Values are 0..1 internally,
  `display_value` is the plain number (50.0, 15000.0), `str_for_value` the unit string.
* Browser: `browser.plugins` → `AUv2` (vendor folders: Akai Professional, Apple, Image-Line,
  Splice, Xfer Records), `VST` (flat: FL Studio VSTi, MPC Beats; uri `…#VST:Local:…`), `VST3`
  (Splice, Xfer Records). Plug-in items: `is_loadable` true, `is_device` **false**, no type.
* Plug-in window API: `PluginDevice.is_editor_open` (rw, listenable) — the only UI control.
  Live's Configure mode itself cannot be toggled from a script.

## Splice folder

`~/Splice` exists on this Mac with `sounds` and `presets` (both empty). `samples.locations`
(used by `tools/splice.py` for the default folder) reports `splice_folder =
~/Splice/sounds` → PASS. Windows: `%USERPROFILE%\Splice\sounds`, `%USERPROFILE%\Splice`,
`%USERPROFILE%\Documents\Splice(\sounds)` and `LIVEBRIDGE_SPLICE_DIR` first (unit-tested in
`tests/test_splice_folders.py`). A custom location chosen in the Splice app could not be read:
the macOS app (5.4.12) keeps no folder setting in `~/Library/Preferences/com.splice.Splice.plist`
or `…/com.splice.Splice/appState.json` (that file holds account data and was not used further) —
the user passes `folder=` or sets `LIVEBRIDGE_SPLICE_DIR`. Not covered: OneDrive-redirected
Documents on Windows (`%USERPROFILE%\OneDrive\Documents\Splice`) — proposed for
`handlers/samples.py:splice_candidates`.

## Stub mirror and tests

* `tests/live_stub_ext/plugins_live.py` — `RealPluginDevice` (all plug-in names from
  `get_parameter_names`, Configure list as `parameters`, "Default" presets), `configure()`
  (the user's Configure action, fires the `parameters` listeners), `hide()` (dynamic AU names),
  `serum2_names()` (Serum 2's exact 2623 names, asserted equal to the real list).
* `tests/test_plugins_live.py` (22 tests) and `tests/test_splice_folders.py` (4 tests).
* `tests/test_plugins*.py tests/test_browser*.py tests/test_devices*.py`: 187 passed.
