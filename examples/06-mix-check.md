# 6 · Mix check: levels, low end, side-chain, reference track

> **You:** Check my mix. Is anything too loud or clipping, is the low end muddy, and how does it
> compare with my reference track (`~/Music/reference.wav`)? Fix what's obvious.

|  |  |
|---|---|
| **Shows** | reading levels and meters, a real-time bounce, loudness and spectrum analysis against a reference, EQ, side-chain compression, a limiter |
| **Needs** | any Live 12 edition · the audio analysis libraries (the installer adds them unless you pass `--no-audio`) |
| **Tool calls** | about 12 |

Claude can't hear audio. It reads Live's level meters and measures bounced audio instead, and
tells you what it measured, so the final judgement stays with your ears.

## What Claude does

**1. Read the mixer.**

```python
live_mixer_get(track="Bass")
```

```json
{"name": "Bass", "volume": {"db": -9.0, "display": "-9.0 dB"}, "pan": {"display": "C"},
 "sends": [{"letter": "A", "return": "A-Reverb", "display": "-inf dB"},
           {"letter": "B", "return": "B-Delay", "display": "-inf dB"}]}
```

**2. Listen to the meters** while the drop plays: peak level per track and on the master, and
Live's CPU load.

```python
live_scene_fire(scene="Drop")
live_mixer_meters(seconds=3)
live_transport_stop()
```

A master peak near 1.0 means no headroom; an instrument track at 0 means it isn't sounding
(muted device, wrong routing, notes outside the sample's range).

**3. Bounce 8 bars and measure them against the reference.** Live's API cannot export audio, so
`live_record_resample` records the master onto a new audio track in real time (you'll hear it).

```python
bounce = live_record_resample(start="13.1.1", bars=8, source="master", name="Mix check")
live_audio_analyze(file_path=bounce["clips"][0]["file_path"], kind="mix",
                   reference="~/Music/reference.wav")
```

This is what a comparison looks like (two drum loops from Live's Core Library):

```json
{"loudness": {"lufs": -12.7, "true_peak_db": -0.8, "crest_db": 13.6},
 "spectrum": {"bands_db": {"sub": -9.1, "bass": -3.7, "low_mid": -4.4, "mud": -18.0,
                           "mid": -18.2, "presence": -15.5, "high": -16.2, "air": -25.1}},
 "reference": {"loudness": {"lufs": -8.4, "true_peak_db": 0.4, "clipped_samples": 1223}},
 "comparison": {"lufs": -4.3, "notes": [
   "low_mid (120-250 Hz) is 8.5 dB louder than the reference.",
   "sub (20-60 Hz) is 8.2 dB quieter than the reference.",
   "bass (60-120 Hz) is 6.7 dB louder than the reference.",
   "The mix is 4.3 LU quieter than the reference — level-match before judging the tone (band values are already relative)."]}}
```

**4. Clean up the low end: cut everything below 150 Hz from the pad.**

```python
live_device_insert(name="EQ Eight", track="Pad")
live_device_set_parameters(track="Pad", device="EQ Eight",
                           values={"1 Filter Type A": "High Pass 48dB", "1 Frequency A": "150 Hz"})
```

**5. Let the kick through: side-chain the bass to the drums.**

```python
live_device_insert(name="Compressor", track="Bass")
live_routing_route(source="Drums", destination="Bass", method="sidechain")
live_device_set_parameters(track="Bass", device="Compressor", values={
    "Threshold": "-25 dB", "Ratio": "4.00 : 1", "Attack": "1.00 ms", "Release": "150 ms"})
```

```json
{"method": "sidechain", "source": "Drums", "destination": "Bass",
 "routing": {"input": {"type": "Drums", "channel": "Post FX"}}, "sidechain_enabled": true}
```

**6. A limiter last on the master.**

```python
live_device_insert(name="Limiter", track="master")
```

Then Claude plays the drop again, reads the meters and reports what changed. The measurements
are hints against typical full-mix values; a sparse intro or a deliberately dark mix may
differ on purpose.

## Take it further

- *"Balance the levels for me."* → `live_mixer_set_many` with targets from the skill's
  production guide (kick around −6 dB, pads lower, returns around −15 dB).
- *"Is the bass in mono?"* → `stereo.low_end_width` in `live_audio_analyze`.
- *"How loud is my reference, and where does its energy build?"* →
  `live_audio_analyze(file_path="~/Music/reference.wav", kind="mix")` returns LUFS and an
  `energy` curve over time.
