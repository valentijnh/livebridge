# Real-Live test T3 — devices, plug-ins, racks, browser, samples, Splice

Tested 2026-09-10 on **Ableton Live 12.4.5 Suite, macOS (arm64), Python 3.11.6 inside Live**,
with two other testers working in the same set at the same time. Every command was called
on the real Live through `tests/live_query.py` (and the composite MCP tools in-process through
`BridgeClient` + `create_app`), with realistic arguments and edge cases (name / index / path
addressing, out of range, wrong track type, bad values). Only tracks named
`LB_T3-devices-browser …` were created/changed; they were deleted at the end.

Legend: **PASS** works as documented · **FIXED** real Live differed, handler/tool/tests fixed ·
**UNSUPPORTED** cannot be exercised / not possible in this Live · **FAIL** still broken.

## Bridge commands (41)

### devices.* / simpler.* (handlers/devices.py)

| command | result | notes |
|---|---|---|
| `devices.list` | PASS | flat, `tree` through Instrument/Drum Racks (drum chains carry `in_note`/`key`), master/return tracks, selected device path |
| `devices.get` | PASS | name, class name ("Eq8", "StereoGain"), prefix, index, path; `full` with parameters; `can_compare_ab`/`compare_b` real on 12.4.5 native devices; latency (Compressor 10 ms) |
| `devices.parameters` | PASS | paging, filter, `only_changed`, value items ("Peak/RMS/Expand"), display strings |
| `devices.get_parameter` | FIXED | real names like Compressor "S/C EQ Freq": "sc eq freq" was not found → punctuation-free matching tiers added ("sc eq" stays ambiguous with a list) |
| `devices.set_parameter` | FIXED | display strings verified on real curves: "-20 dB", "10 ms", "250 ms", "1.5 kHz", "40 %"/"40%", "25L"/"L25"/"C"/"50R", "-6 dB"/"+6 dB" (Utility), "2.5 s"/"2500 ms", "-12 st", "12", value items ("Saw 3", "Bell", "left", "10 ms"), bools, clamping, `-inf dB`. Compressor ratios failed ("4:1" vs Live's "4.00 : 1") → ratio parsing added ("4:1", "inf:1", "1:1.5" expansion) |
| `devices.set_parameters` | PASS | dict and list forms, cross-device paths, atomic validation (bad item → nothing written); ratios via the shared resolver |
| `devices.reset_parameters` | PASS | quantized parameters really have no default in Live ("There is no default value available…") → skipped and listed |
| `devices.randomize` | PASS | seed, amount, include_quantized, explicit parameter list |
| `devices.set_state` | PASS | active/toggle via "Device On", rename, collapse, select, A/B compare (12.4.5), `show_chains` on racks; unsupported/bad_args paths |
| `devices.insert` | FIXED | Live's `insert_device` wants the exact case-sensitive UI name ("operator" → `ValueError: Device operator not found.`) and cannot create Max for Live based devices (LFO, Shaper, Drum Sampler, DS *, Note Echo, MIDI Monitor, …). Now: case/spacing/class-name aliases ("eq eight", "Eq8", "StereoGain", "glue"), `not_found` with close matches, Max for Live devices loaded through the browser at the end of a track (`via: "browser"`, `unsupported` + exact `browser.load` path for chains/indices). Live's refusals pass through verbatim ("Device chains cannot have more than one instrument each", "Only audio effects can be inserted into an audio track", "Insert MIDI effects before instruments. A valid index would be 0"). The real UI-name → class-name table of all 66 native devices is in `devices.NATIVE_DEVICE_CLASSES` |
| `devices.duplicate` | PASS | copy lands at index + 1 |
| `devices.move` | FIXED | works across tracks and to position 0 (Live moves an audio effect behind the instrument — reported position is the real one); Live's refusal is only "Couldn't move device." → hint added (instrument onto an audio track) |
| `devices.delete` | PASS | index/name/selected, out of range, empty track |
| `devices.find` | PASS | query, class name, kind, type, across all testers' tracks (read-only) |
| `simpler.get` | PASS | sample file, frames, rate, markers, warp, slicing, slices |
| `simpler.set` | PASS | playback/slicing modes, voices, style/division/markers/gain/warp mode; Live's "Cannot set marker outside of the sample" → invalid_state |
| `simpler.action` | FIXED | crop/reverse/warp_as/double/half/guess/insert/move/clear/reset/replace_sample all work (crop/reverse write processed files into the project). Live's `remove_slice` silently ignores a frame that is not exactly a slice, and `move_slice` keeps a slice between its neighbours → `remove_slices` now snaps to the nearest slice within 10 ms and returns `{removed, not_found}`; docs explain move_slice |

### plugins.* (handlers/plugins.py)

| command | result | notes |
|---|---|---|
| `plugins.list` | FIXED | works (0 plug-ins in the set). Live's Plug-Ins browser lists **0 plug-ins** on this Mac although AU + VST2 plug-ins exist on disk (FL Studio, MPC Beats in `/Library/Audio/Plug-Ins/Components` and `/VST`) — plug-in use is off in Live's Settings. `plugins.list` now reports `installed {count, formats, complete, hint}` so Claude can tell the user what to switch on |
| `plugins.get` | UNSUPPORTED | no plug-in can be loaded in this Live (see above); error path verified (`bad_args` "is not a third-party plug-in" for native devices) |
| `plugins.presets` | UNSUPPORTED | same reason; error path verified |
| `plugins.parameters` | UNSUPPORTED | same reason; error path verified |
| `plugins.set` | UNSUPPORTED | same reason; error path verified |

### racks.* (handlers/racks.py)

| command | result | notes |
|---|---|---|
| `racks.chains` | FIXED | chains, mixer display values, `muted_via_solo` (real), return chains; Live's `Chain.mute` **is** the chain activator → the duplicated `active` field was removed |
| `racks.set_chain` | FIXED | name, mute/solo/exclusive, "-6 dB"/"25L", color, select, drum out_note/choke. Real Live rejects `in_note=-1` ("Invalid note number.") — the documented "-1 = all notes" does not exist → now `bad_args` with an explanation; contradicting `mute`/`active` refused (one switch) |
| `racks.insert_chain` | FIXED | Instrument/Drum Racks, `device_name` now accepts the same names as devices.insert ("simpler"); a new Drum Rack chain always lands on C1 (36) whatever pad is selected (docs said "all notes") — documented; `in_note=-1` refused |
| `racks.macros` | PASS | 8/12/16 macros, mapped flags, display values of a preset kit ("Low Gain" "0.0 dB") |
| `racks.set_macros` | FIXED | values/normalized/visible_count/chain_selector; real preset macro names carry trailing spaces ("Low Gain  ") → name lookup now strips them |
| `racks.variations` | FIXED | store/recall/recall_last/select/delete/randomize verified on "808 Core Kit". Real Live: `store` does not select the new variation, `delete` leaves nothing selected, and recall/randomize only change **mapped** macros (no-op on an empty rack) → answer carries a `note`, docs updated |
| `racks.drum_pads` | PASS | real kits: 16 pads, samples, choke groups; Live names multi-chain pads "Multi" and empty pads after their note ("D1") |
| `racks.set_pad` | FIXED | mute/solo/name/choke/out_note/select/copy_to/clear work; a "Multi" pad was unreachable by its chains' names ("Kick") → pad lookup also matches chain names |

### browser.* (handlers/browser.py)

| command | result | notes |
|---|---|---|
| `browser.roots` | PASS | 14 roots with counts (plugins 0, packs 1 = Core Library, user_folders empty), no Splice root, hot-swap info |
| `browser.browse` | FIXED | paths, uris, names, filters, kinds, paging; now warns (`hotswap_filter`) when a hot-swap target filters the browser |
| `browser.search` | FIXED | ranking, categories, roots, cache, limits verified; pack copies of category items ("808 Core Kit.adg" twice) are now dropped; walks made while Live is in hot-swap mode are no longer cached (they only see the filtered browser) |
| `browser.load` | FIXED | uri/path/query, `new_track`, instruments, effects, presets, drum kits, clips, samples verified. Fixed: (1) Live raises "Couldn't set hotswap target" when the target is already set → no-op; (2) a pending hot-swap target filters the browser (instrument target → `audio_effects` has 0 children) so lookups failed with "it is empty" → normal loads clear the target and hot-swap loads set theirs *before* the lookup (restored on failure); (3) Live reuses the device object for a preset of the same device / a kit over a Drum Rack / Operator over Operator → reported as `changed` (`was` / `reloaded`) instead of "no change"; (4) Live Clips (.alc) always create a new track → their clip is reported and a note explains it; (5) samples/clips do nothing while the Arrangement view is focused → explicit note |
| `browser.hotswap` | FIXED | device and drum-pad targets, clear, info; setting the same target twice no longer errors; `drum_pad` accepts note names ("C1", "D1") and chain names |
| `browser.preview` | PASS | samples audition, stop; devices preview silently |
| `browser.cache` | FIXED | info/clear; hot-swap-filtered walks excluded (see search) |

### samples.* (handlers/samples.py)

| command | result | notes |
|---|---|---|
| `samples.import` | FIXED | Core Library WAV and AIFF into session slots (`ClipSlot.create_audio_clip`), overwrite, arrangement (`"9.1.1"` → beat 32, beats), Simpler on a new MIDI track and replace on an existing Simpler, MIDI/master/audio track refusals. Fixed: the `browser` route on an audio track does nothing while the Arrangement view is focused → refused up front (before an overwrite deletes a clip); on a MIDI track it no longer picks a clip slot; a load that changes nothing is an error; files whose header contradicts the extension (AIFF data named .wav — Live: "This file does not appear to be a valid WAV file") are refused before calling Live |
| `samples.inspect` | FIXED | WAV/AIFF details exact, Windows path on macOS, `~`, folders; now flags header/extension mismatches |
| `samples.list` | PASS | recursive, patterns, sorts, paging, missing folder |
| `samples.locations` | FIXED | now also reports Live's Core Library (derived from the running Live executable: `…/Contents/App-Resources/Core Library` on macOS, `…\Resources\Core Library` on Windows) and Factory Packs |

## MCP tools (in-process against the real bridge)

| tool | result |
|---|---|
| `live_device_list/get/parameters/get_parameter/set_parameter/set_parameters/reset_parameters/randomize/set_state/insert/duplicate/move/find/delete` | PASS (docstring of `live_device_insert` updated) |
| `live_simpler_get/set/action` | PASS |
| `live_plugin_list` | PASS (`installed` hint shown); `live_plugin_get/presets/set/parameters` UNSUPPORTED (no plug-in loadable), error path PASS |
| `live_rack_chains/set_chain/insert_chain/macros/variations`, `live_drumrack_overview/set_pad` | PASS (docstrings updated: activator = mute, no "all notes", C1 default, mapped-macro variations, chain names) |
| `live_browser_roots/browse/search/load/preview` | PASS |
| `live_browser_load_instrument/effect/drum_kit/sample` | PASS (load_sample docs: Session view only) |
| `live_browser_load_plugin` | UNSUPPORTED (no plug-ins in Live's browser; answers not_found) |
| `live_browser_hotswap` (info/set/load/clear, drum pad "D1" + sample) | PASS |
| `live_sample_import/list/inspect/locations` | PASS |
| `live_splice_setup_info` | PASS |
| `live_splice_import_downloaded` | PASS — temp folder with copied Core Library WAVs: newest=2 into session slots 5/6, arrangement end-to-end on a new track, simpler mode on a new MIDI track, pattern without matches (hint), missing folder (not_found) |
| `live_splice_watch_folder` | PASS — picks up a file copied 3 s after the start; a mislabelled file (AIFF named .wav) is reported in `errors`; timeout path returns `new_file: null` + hint |

## Live 12.4.5 facts learned (worth adding to docs/LIVE_API_VERIFIED.md §19)

* `Track/Chain.insert_device(name)`: exact, case-sensitive UI names; unknown → `ValueError("Device X not found.")`; Max for Live based browser devices are not insertable. Refusal texts: "Can not insert device 'X': Device chains cannot have more than one instrument each." / "…: Only audio effects can be inserted into an audio track." / "Invalid insert index for device 'X': Insert MIDI effects before instruments. A valid index would be 0."
* Real class names include `AutoFilter2`, `AutoPan2`, `Chorus2`, `Erosion2`, `Redux2`, `PhaserNew`, `Tube`, `Hybrid`, `Transmute`, `Spectral`, `SpectrumAnalyzer`, `Vinyl`, `Resonator`, `FilterEQ3`, `ChannelEq`, `LoungeLizard`, `StringStudio`, `InstrumentImpulse`, `ProxyInstrumentDevice`, `ProxyAudioEffectDevice`, `MidiCcControl`, `MidiNoteLength`, `MidiPitcher`, `MidiRandom`, `MidiVelocity` (full table: `devices.NATIVE_DEVICE_CLASSES`).
* `DrumChain.in_note`/`out_note` 0..127 only (−1 → "Invalid note number." / "Invalid note."), `choke_group` 0..16 ("Invalid choke group."); `RackDevice.insert_chain()` on a Drum Rack → `in_note` 36 regardless of the selected pad; `DrumPad.name` = chain name / "Multi" / note name.
* `Chain.mute` is the chain activator (`mixer_device.chain_activator`).
* Variations: `store_variation` keeps `selected_variation_index` (−1); `delete_selected_variation` → −1; recall/randomize only affect mapped macros.
* `browser.hotswap_target = X` while X is already the target → `RuntimeError("Couldn't set hotswap target")`; with a target set the browser is filtered (instrument target: `audio_effects.children` empty), `app.view.browse_mode` true, `filter_type` stays −1; after a hot-swap load the new device stays the target.
* `browser.load_item`: a preset of the same device / a kit over a Drum Rack / the same device again keeps the device object (name changes, parameters reset); `.alc` clips create a new track with their devices; samples/clips do nothing while the Arrangement view is focused; an instrument onto an audio track creates a new MIDI track.
* `DeviceParameter.display_value` exists but raises "Invalid display value" for some quantized parameters (e.g. Compressor "Env Mode") — `str_for_value` is the reliable formatter.
* `Sample.remove_slice(t)` ignores non-slice frames; `move_slice` keeps the slice between its neighbours and returns the real position.
* Live's own Core Library on macOS: `/Applications/Ableton Live 12 Suite.app/Contents/App-Resources/Core Library` (`sys.executable` inside Live is `…/Contents/MacOS/Live`).

## Stub mirrors

`tests/live_stub_ext/devices_live.py`, `racks_live.py` and `browser_live.py` patch the shared
stub to the behaviour above (installed per test by `tests/test_devices.py`, `test_racks.py`,
`test_browser.py`); the same changes are proposed for `tests/live_stub/Live/_model.py`.

## Test runs

* `tests/test_{devices,plugins,racks,browser,samples,splice}.py`: 252 passed.
* Full suite: 1145 passed, 2 skipped, 2 failed in other areas — `test_transport.py::test_back_to_arranger` (T1-global mid-edit) and `test_consistency.py::test_tools_doc_is_up_to_date` (docs/TOOLS.md is generated and stale after the docstring changes of several testers — regenerate with `installers/gen_tools_doc.py`).
