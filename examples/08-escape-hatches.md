# 8 · Anything else: batches, the Live Object Model, Python inside Live

> **You:** Rename every clip after its track and its scene, like "Drums · Intro".

|  |  |
|---|---|
| **Shows** | what Claude does when no dedicated tool exists: explore the Live Object Model, read and write any property, run many commands as one undo step, run Python inside Live |
| **Needs** | any Live 12 edition · `live_eval_python` needs `allow_eval` (on by default, always behind the token) |

LiveBridge has 168 dedicated tools, but Live's API is bigger than that. These tools reach the rest.

## Explore: what does an object offer?

```python
live_lom_describe(path="song.scenes[2]", include_methods=false)
```

```json
{"type": "Scene", "path": "song.scenes[2]",
 "properties": [{"name": "name", "value": "Drop", "type": "str", "writable": true},
                {"name": "tempo", "value": -1.0, "type": "float", "writable": true},
                {"name": "tempo_enabled", "value": false, "type": "bool", "writable": true},
                {"name": "is_empty", "value": false, "type": "bool", "writable": false}, ...],
 "children": [{"name": "clip_slots", "count": 3, "path": "song.scenes[2].clip_slots"}]}
```

## Read and write any property, call any method

```python
live_lom_get(path="song.tracks[0].devices[0]", prop="can_have_drum_pads")      # → true
live_lom_set(path="song.scenes[2]", prop="name", value="Drop A")
live_lom_call(path="song", method="create_scene", args=[-1])
```

## Python inside Live, for bulk edits

The request above in one call. The code runs on Live's main thread with `song` in scope.

```python
live_eval_python(code="""
renamed = []
for track in song.tracks:
    for slot, scene in zip(track.clip_slots, song.scenes):
        if slot.has_clip:
            slot.clip.name = f"{track.name} · {scene.name or 'Scene'}"
            renamed.append(slot.clip.name)
""", expr="renamed")
```

```json
{"result": ["Drums · Intro", "Drums · Build", "Drums · Drop", "Bass · Drop",
            "Pad · Intro", "Pad · Build", "Pad · Drop", "Pad · Break"], "stdout": ""}
```

Claude prefers a dedicated tool when there is one, and says what it is about to run when
the code changes your set.

## Many commands, one undo step

`live_command_batch` runs bridge commands in one round trip. The whole batch is one undo step,
and `"$0.path"` refers to the result of step 0:

```python
live_command_batch(commands=[
    {"cmd": "tracks.create",    "args": {"type": "midi", "name": "Lead"}},
    {"cmd": "devices.insert",   "args": {"name": "Drift", "track": "$0.path"}},
    {"cmd": "notes.write_arp",  "args": {"track": "$0.path", "slot": 0, "chords": "Am F C G",
                                         "style": "up", "rate": "1/16", "octave": 4}},
    {"cmd": "mixer.set",        "args": {"track": "$0.path", "volume": "-12 dB"}},
])
```

```json
{"count": 4, "ran": 4, "ok": 4, "failed": 0, "results": [
  {"cmd": "tracks.create",   "ok": true, "result": {"path": "song.tracks[3]", "name": "Lead"}},
  {"cmd": "devices.insert",  "ok": true, "result": {"name": "Drift", "via": "insert_device"}},
  {"cmd": "notes.write_arp", "ok": true, "result": {"added": 64, "length": 16}},
  {"cmd": "mixer.set",       "ok": true, "result": {"volume": {"display": "-12.0 dB"}}}]}
```

## Which bridge commands exist?

```python
live_commands()                     # the index: 200 commands in 23 namespaces
live_commands(namespace="clips")    # parameters of one namespace
live_command_call(cmd="arrangement.back_to_arranger")
```

`live_command_call` runs any bridge command that has no dedicated tool. The full list, with
the tool behind each command, is in [docs/TOOLS.md](../docs/TOOLS.md#bridge-commands).
