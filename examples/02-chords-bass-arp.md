# 2 · Chords, bass and an arp that fit together

> **You:** Write a melancholic lo-fi progression in D minor at 80 BPM: electric piano chords, a
> simple bassline and a soft arpeggio on top. Then check that the parts don't clash.

|  |  |
|---|---|
| **Shows** | Roman-numeral chords in a key, bass and arp generated from the same progression, the theory check finding (and fixing) a clash, humanising |
| **Needs** | Live 12 Suite for the *E-Piano Basic* preset (other editions: any keys preset) |
| **Tool calls** | 13 |

## What Claude does

**1. Tempo, and Live's scale set to the key so scale highlighting and `fit_scale` agree.**

```python
live_transport_set(tempo=80, root_note="D", scale_name="Minor")
```

**2. Three instruments on new tracks.**

```python
live_browser_load(query="E-Piano Basic", category="instrument", new_track=true, track_name="Keys")
live_browser_load(query="Sub 808 Bass", category="instrument", new_track=true, track_name="Bass")
live_browser_load(query="Plucked Keys", category="instrument", new_track=true, track_name="Arp")
```

**3. Chords from Roman numerals: i7 – VImaj7 – iv7 – v7 in D minor, a bar each, slightly strummed.**

```python
live_clip_write_chords(track="Keys", slot=0, chords="i7 VImaj7 iv7 v7", key="D minor",
                       duration=4, voice_leading=true, strum=0.03, velocity=75)
```

```json
{"key": "D minor", "chords": [
  {"chord": "i7",     "notes": ["D3", "F3", "A3", "C4"]},
  {"chord": "VImaj7", "notes": ["D3", "F3", "A3", "Bb3"]},
  {"chord": "iv7",    "notes": ["D3", "F3", "G3", "Bb3"]},
  {"chord": "v7",     "notes": ["C3", "E3", "G3", "A3"]}]}
```

**4. Bass and arp from the same progression, as chord symbols this time.**

```python
live_clip_write_arp(track="Bass", slot=0, chords="Dm7 Bbmaj7 Gm7 Am7",
                    style="root_fifth", rate="1/4", octave=1, gate=0.9)

live_clip_write_arp(track="Arp", slot=0, chords="Dm7 Bbmaj7 Gm7 Am7",
                    style="updown", rate="1/8", octave=4, velocity=70, swing=0.3)
```

The bass alternates root and fifth (D1–A1, Bb1–F2, G1–D2, A1–E2); the arp runs up and down each
chord in swung eighths.

**5. Check that the three parts agree.**

```python
live_theory_analyze(clips=[{"track": "Keys", "slot": 0},
                           {"track": "Bass", "slot": 0},
                           {"track": "Arp", "slot": 0}])
```

```json
{"key": {"name": "D minor", "confidence": 1.0},
 "progression": "i7 VImaj7 iv7 v7",
 "out_of_key": [],
 "clashes": [{"kind": "semitone", "parts": ["Arp", "Keys"], "notes": ["A3", "Bb4"],
              "at": [4.06, 7.0], "beats": 0.74}],
 "suggestions": ["A3 against Bb4 (Arp + Keys) is a semitone rub sounding 0.74 beats (first at beat 4.06): move one of them to a chord tone unless the tension is intended.",
                 "Keys: every note has (almost) the same velocity (75-75) — accent the strong beats or humanize ..."]}
```

Everything is in key, but in bar 2 the major seventh of Bbmaj7 (A3, in the piano) rubs against
the arp's Bb4 a minor ninth higher. Claude explains it and offers to keep the tension or thin
the voicing.

**6. Thin the voicing and humanise the piano.**

```python
live_clip_remove_notes(track="Keys", slot=0, pitch="A3", start=4, end=8)
live_clip_transform_notes(track="Keys", slot=0, humanize_timing=0.02, humanize_velocity=8, seed=7)
```

**7. Check again.**

```python
live_theory_analyze(clips=[{"track": "Keys", "slot": 0},
                           {"track": "Bass", "slot": 0},
                           {"track": "Arp", "slot": 0}])
```

```json
{"key": {"name": "D minor", "confidence": 1.0},
 "progression": "i7 VI iv7 v7", "out_of_key": [], "clashes": [],
 "parts": [{"name": "Keys", "notes": 15, "velocity": [67, 82]}, ...]}
```

No clashes left; the piano now plays Bb instead of Bbmaj7 in bar 2, with natural velocities
(67–82).

## What you get

A 4-bar loop on three tracks that agree on key and harmony, checked by the same analysis Claude
uses on your own material (*"what key is this clip in?"*, *"which chords am I playing?"*).

## Take it further

- *"Write a melody over it that stays on chord tones on the downbeats."* → `live_clip_add_notes`
  with `live_theory_analyze` afterwards.
- *"Transpose everything to E minor."* → `live_clip_transform_notes(transpose=2)` on each clip.
- *"Make the arp a bit more random."* → `live_clip_write_arp(style="random", seed=...)`.
