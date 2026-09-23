# 4 · Splice samples straight into your set

> **You:** Find a rolling off-beat hi-hat loop on Splice around 128 BPM, show me three options,
> and put the one I pick on a new track, warped to the song tempo.

|  |  |
|---|---|
| **Shows** | Splice's official MCP server and LiveBridge working together: search → you choose → download → import, warp, check |
| **Needs** | a Splice account (searching is free; downloads use your plan's credits) · any Live 12 edition |
| **Tool calls** | 3 Splice + 3 LiveBridge |

## How the two servers split the work

LiveBridge doesn't reimplement Splice. The installer registers **Splice's own MCP server**
(`https://mcp.splice.com/mcp`) next to LiveBridge, so Claude has both sets of tools:

| Step | Server | What happens |
|---|---|---|
| Search | Splice | Claude searches by instrument, BPM, key and genre and shows you a few candidates. Free. |
| Download | Splice | Only after you pick one: Splice downloads the file and reports its path. Uses a credit (max 100 per 24 h). |
| Import | LiveBridge | The reported file goes into Live: a session slot, the arrangement, a Simpler or a Drum Rack pad. |

## What Claude does

**1–2. Search and download with the Splice tools.** Claude presents the options (name, BPM,
key, pack) and waits for your choice before spending a credit.

**3. Import the file Splice reported.**

```python
live_splice_import_downloaded(files=["~/Splice/sounds/packs/.../hihat_loop_128.wav"],
                              mode="session", track_name="Hats", warp=true)
```

```json
{"imported": [{"created_track": true, "route": "clip_slot.create_audio_clip",
               "applied": {"warping": true},
               "track": {"name": "Hats", "type": "audio"},
               "clip": {"name": "hihat_loop_128", "is_audio": true, "length": 8.0}}]}
```

If Live runs on another computer, LiveBridge first sends the file there
(see [example 7](07-two-computers.md)).

**4. Measure it before it goes into the mix.**

```python
live_audio_analyze(track="Hats", slot=0)
```

Loudness (LUFS, true peak), spectral balance, stereo width, and, for loops long enough to
tell, tempo and key. Claude uses that to set the clip gain and, for tonal loops, to transpose to
the song key with `live_clip_set(pitch_coarse=...)`.

**5. Tidy up.**

```python
live_clip_set(track="Hats", slot=0, name="Splice hats", gain_db=-3)
```

## Other ways in

- **One-shots onto a Drum Rack**, one pad per file, in one call:
  ```python
  live_splice_import_downloaded(files=["~/Splice/.../kick.wav", "~/Splice/.../snare.wav"],
                                mode="drum_rack", track="Drums", note="C1")
  ```
- **Downloads you make yourself** in the Splice app: `live_splice_watch_folder` waits for the
  next file and imports it; `live_splice_import_downloaded(newest=3, max_age_minutes=10)`
  picks up recent ones.
- **Any file on disk**, Splice or not: `live_sample_import(file_path=..., mode="simpler")`.
- Splice missing in Claude? `live_splice_setup_info` explains how to add it.
