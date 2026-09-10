# Real-Live test T1-global — transport, song, scenes, view, cues, record

Tested on 2026-09-10 against **Ableton Live 12.4.5 Suite (macOS, arm64)** through the running
LiveBridge (`tests/live_query.py`, in-process MCP tools via `BridgeClient`), in parallel with
the T2/T3 testers. Every command of the `transport.*`, `song.*`, `scenes.*`, `view.*`, `cues.*`
and `record.*` namespaces was called with realistic arguments and edge cases (index / name /
path, out of range, wrong track type, bad types, song-length limits). Only objects named
`LB_T1-global …` were created (2 tracks, scenes A/B/C/D/E + copies, cues) and all were deleted
afterwards; every song-level and view setting was restored to its original value (verified: no
difference in 33 song properties, identical view state and selection).

Result: **58 commands — 28 PASS, 28 FIXED, 2 UNSUPPORTED (by test rule), 0 FAIL.**
All 38 MCP tools of the areas were exercised in-process against the real bridge.

## Key facts about Live 12.4.5 learned here (the stub now mirrors them)

1. **Deferred properties.** Live applies `is_playing` (start/stop/continue), `current_song_time`
   (also `jump_by`, `CuePoint.jump`), `loop`, `punch_in`, `punch_out`,
   `session_automation_record`, `record_mode`, `back_to_arranger` and clip-slot recordings on
   its *next* main-thread tick: reading them back in the same command returns the old value.
   `start_time`, tempo, signature, metronome, `arrangement_overdub`, quantization, groove,
   scale settings are immediate. Handlers now report the requested state for deferred props.
2. **`continue_playing()` after a `current_song_time` write in the same tick starts from the
   *old* playhead** (the write is lost). `transport.play(position)` now moves the (immediate)
   start marker and calls `start_playing()`.
3. **Song length.** `current_song_time`, `start_time` and the loop brace must stay inside
   `song.song_length` (= end of arrangement material or loop brace + 32 beats); Live raises
   "Cannot set the Songtime behind the Songlength" / "…Loopstart…" / "…Loop behind the song
   length", checking `loop_start + loop_length` on *each* write (so a long brace cannot be
   moved later in one step). Loop lengths < 1 beat are raised to 1 beat.
4. **Cue points.** `song.cue_points` is in **creation order**, not time order. Stopped,
   `set_or_delete_cue()` acts at Live's *insert marker*, which a `current_song_time` write moves
   to the nearest line of the **zoom-dependent Arrangement grid** (12.7 → 12.0 / 12.75 / 16.0
   depending on zoom; `jump_by` moves only the reported time, `CuePoint.jump()` is exact and
   also moves the start marker). `is_cue_point_selected()` tells whether the marker sits on a
   cue. New cues are named "1", "2", …
5. `app.view.zoom_view(3, "Arranger", False)` zooms the time axis **in** (finer grid, down to
   1/256 beat), direction 2 zooms out; this works while the Session view is shown.
6. **Scenes.** `create_scene(i)` copies the tempo/time signature of the scene above and selects
   the new scene. `capture_and_insert_scene()` inserts a scene even when nothing plays and copies
   the selected scene's name/tempo/signature. `duplicate_scene` keeps the name. Firing an empty
   scene does not start the transport but applies its tempo *and* signature at once.
   `Scene.color_index` is `None` for a scene without colour.
7. **Recording.** Arming via the API ignores Live's "Exclusive Arm" preference (other armed tracks
   stay armed; `song.exclusive_arm` was True). `record_mode = True` starts playback immediately
   (default "Start Playback with Record" preference) and records every armed track.
   `overdub = True` switches Session Record on without starting playback; `session_record = True`
   starts playback and records/overdubs the selected scene's slots of armed tracks.
   `stop_playing()` while already stopped returns playhead and start marker to 1.1.1.
8. **Signature per position.** When the arrangement contains time-signature changes (Live writes
   them when scenes with a signature are launched while the arrangement records),
   `song.signature_*` is the signature *at the playhead* and writing it changes that section.
   The LOM has no API to list or delete these markers.
9. `groove_amount` accepts 0..1.3125 (Live's 131 %; larger values clamp); `swing_amount` accepts
   odd values (-0.1, 1.3) silently — LiveBridge keeps 0..1.
10. Limits: tempo 20..999 (`RuntimeError('Tempo out of range')`), numerator 1..99, denominator
    1/2/4/8/16 (32 raises; 3 is silently turned into 4).

## transport.* / song.*

| Command | Status | Notes |
|---|---|---|
| `transport.get` | PASS | all fields match Live; `include_scales` returns Live's 35 scales |
| `transport.play` | FIXED | stopped + `position` started from the old playhead (see fact 2); `is_playing`/`position` were stale; positions after the song length now `bad_args`; `quantized: true` while playing |
| `transport.continue` | FIXED | result showed `is_playing: false` (deferred) |
| `transport.stop` | FIXED | result showed `is_playing: true`; documented the double-stop reset |
| `transport.toggle` | FIXED | stale `is_playing` |
| `transport.set_position` | FIXED | reported the previous position (deferred write); song-length check up front; `jump_by` target computed |
| `transport.set_tempo` | PASS | 20/999 accepted, 19.9 rejected |
| `transport.tap_tempo` | PASS | taps change the tempo; the reported tempo can lag one tap (documented) |
| `transport.set_time_signature` | PASS | 6/8, 7/16, [5,4]; 32 / 3 / 100 rejected; documented per-position signature (fact 8) |
| `transport.set_loop` | FIXED | `on` was stale; a brace beyond the song length failed *after* moving the start (partial change) — now validated up front and written in an order Live accepts; 1-beat minimum documented |
| `transport.set` | FIXED | punch/loop/automation-arm/record flags reported stale values; `groove_amount` range is 0..1.3125 (was 0..1); `record_mode` starts playback — documented |
| `transport.back_to_arranger` | FIXED | returned the stale `true`; now `{back_to_arranger: false, was}` |
| `transport.undo` | UNSUPPORTED | not called on the shared Live by rule (would undo other testers' work); `can_undo`/`can_redo` read fine; covered by stub tests |
| `transport.redo` | UNSUPPORTED | same as undo |
| `transport.capture_midi` | PASS | `invalid_state` "nothing to capture" as `can_capture_midi` is false; a real capture needs MIDI played into an armed track (not possible from the bridge) |
| `transport.stop_all_clips` | PASS | |
| `song.summary` | FIXED | dropped the serializer's `kind` tags (token economy) |
| `song.snapshot` | FIXED | `limit=0` said "within 1..?"; cue points listed in time order (Live's list is creation order) |

## scenes.*

| Command | Status | Notes |
|---|---|---|
| `scenes.list` | PASS | paging, `include_clips`, `detail` levels |
| `scenes.get` | PASS | name / case-insensitive / prefix / -1 / digit string / path; 999 and unknown names `not_found` |
| `scenes.create` | FIXED | the new scene inherited the tempo/signature of the scene above and stole the selection (fact 6); now clean unless `tempo`/`time_signature`, selection kept unless `select`; all args validated before creating |
| `scenes.delete` | FIXED | a track path gave "the scene is no longer in this set" — now `bad_args` "is not a scene" |
| `scenes.duplicate` | PASS | copy lands after the source and is selected; Live keeps the name (documented) |
| `scenes.set` | PASS | name, colour (index / #hex / name / rgb), tempo, signature, enable/disable; range errors |
| `scenes.rename` | PASS | also by path |
| `scenes.fire` | FIXED | docs claimed firing always starts the transport (an empty scene does not; signature applied too); added `clip_count`; `is_playing` documented as pre-launch |
| `scenes.stop_all` | PASS | |
| `scenes.select` | PASS | |
| `scenes.capture` | PASS | inserted after the selected (own, last) scene even with nothing playing; copies name/tempo/signature (documented) |

## view.*

| Command | Status | Notes |
|---|---|---|
| `view.selection` | PASS | |
| `view.state` | PASS | matches `is_view_visible` for all six views |
| `view.is_visible` | PASS | aliases, `main_window_only=false` |
| `view.show` | PASS | Session/Arranger switch the main view; `""` = current main view |
| `view.hide` | PASS | |
| `view.focus` | PASS | `focused_document_view` only reports Session/Arranger |
| `view.toggle` | PASS | `""` rejected |
| `view.toggle_browser` | PASS | toggle, forced visible/hidden, hot-swap on/off (hotswap_target = selected device) |
| `view.select` | FIXED | `track=<return>, slot=0` selected the track before failing — now everything is resolved first (all-or-nothing) |
| `view.set_detail_clip` | PASS | session slot and arrangement clip paths; empty slot `not_found` |
| `view.zoom` | PASS | directions verified: right = zoom time in, left = out (doc updated) |
| `view.scroll` | PASS | Session, Browser, Detail/DeviceChain with modifier; steps > 50 rejected |
| `view.set` | PASS | follow_song / draw_mode read back correctly |

## cues.*

| Command | Status | Notes |
|---|---|---|
| `cues.list` | FIXED | Live's `cue_points` order is creation order: rows are now sorted by time (`index` = time order) and carry the LOM `path` |
| `cues.position` | FIXED | previous/next/section were computed on the unsorted list |
| `cues.add` | FIXED | **created the cue at the old playhead** (deferred write) and snapped positions — new pending/retry protocol (park playhead, toggle on retry, verify), grid snapping handled by zooming the Arrangement in and retrying (zoom undone), existing cue under a snapped marker never deleted, playhead + start marker restored; stopped transport required for times away from the playhead; song-length check |
| `cues.toggle` | FIXED | same protocol; at the playhead it is Live's Set/Delete button |
| `cues.delete` | FIXED | same bug; deletion now parks with the cue's exact `jump()`; `all=true` loops per cue |
| `cues.set` | FIXED | move = delete + re-create through the protocol; the MCP tool reports `moved`/`moved_from`/`renamed` |
| `cues.jump` | FIXED | `position` was stale; first/last used creation order |
| `cues.loop` | FIXED | `on` stale; brace validated against the song length |
| `cues.convert_time` | PASS | lists, lengths, 6/8 override, errors |

Verified end to end on real Live with the MCP tools: cues at 1.1.1, 5.1.1, 9.2.3 and 12.7 beats
landed exactly; toggle add/delete, move to 17.1.1, rename, delete one / delete all — playhead
and start marker came back each time.

## record.*

| Command | Status | Notes |
|---|---|---|
| `record.status` | FIXED | armed track rows carried the `kind` tag; `exclusive_arm` documented as UI-only |
| `record.arm` | FIXED (doc + stub) | single, list, `toggle`, monitoring in/auto/off, master/return/ambiguous/unknown errors; API arming ignores Exclusive Arm (the stub disarmed others — mirrored in the extension, shared change requested); `exclusive=true` tested when no foreign track was armed |
| `record.session` | FIXED | `status`/`slot_state` were stale (nothing recording yet) — now `fired`/`was_playing`; the MCP tool reads `record.status` back ~0.3 s later; a scene created for recording no longer inherits a tempo/signature. Slot method (fixed 1 bar, launch quantization none) and trigger method recorded MIDI clips "LB_T1-global MIDI n" on the own track only |
| `record.arrangement` | FIXED | stale `record_mode`/`is_playing`; arguments were written before all were validated; song-length check; recorded 8.0–13.4 on the own armed MIDI track only (guarded: run atomically only when no foreign track was armed) |
| `record.stop` | FIXED | stale state after stopping |
| `record.settings` | FIXED | punch/automation-arm stale; a bad `midi_quantization` left earlier switches changed; punch region validated against the song length via the shared loop helper |
| `record.capture_midi` | PASS | `invalid_state` path; destinations by name / int; bad destination `bad_args` |

## Incidents on the shared set (all cleaned up)

* An early `transport.set(record_mode=true)` started playback (fact 7) while T3's MIDI track was
  armed and recorded an empty 3.7-beat MIDI clip into that track's arrangement; it was deleted at
  once (0 notes). Afterwards every recording test ran inside one `eval` that first checked that no
  foreign track was armed.
* Probing cue placement created and deleted a few own cues; one own cue was deleted by a toggle
  while another agent had started playback — all own cues were removed at the end.
* Time-signature changes appeared in the arrangement (3/4 at 0 and from ~15 beats, 4/4 between;
  most likely written while a scene with a signature was launched during arrangement recording).
  They cannot be removed via the LOM; each section was set back to 4/4 (verified 4/4 at 0, 3, 5,
  6, 10, 14, 15, 16, 20, 30, 100, 200 beats). The markers may still be visible (valued 4/4) in the
  Arrangement's signature lane.
* The Arrangement's horizontal zoom was changed by the zoom experiments and cannot be restored
  exactly (zoom steps clamp).
* "too many clients (max 4)" was returned now and then while three testers plus MCP clients were
  connected.
