# Scripting Ableton Live without Claude

LiveBridge is also a plain remote-control API for Live: a TCP server inside Live that speaks
newline-delimited JSON ([docs/PROTOCOL.md](../../docs/PROTOCOL.md)). The MCP tools Claude uses
(`live_*`) are a layer on top of these **bridge commands** (`transport.set`, `notes.write_pattern`,
`browser.load`, …), which you can call from your own scripts, generative tools, controllers or
another language.

| Script | What it shows |
|---|---|
| [`hello_live.py`](hello_live.py) | connect with the MCP server's client and settings; print Live's version, tempo and tracks |
| [`beat.py`](beat.py) | a 4-bar house beat from a script: tempo, a drum kit from the browser, a step pattern, optionally play it |
| [`raw_protocol.py`](raw_protocol.py) | the wire protocol with only the standard library: one JSON line out, one JSON line back |

## Run them

LiveBridge must be installed and enabled in Live ([quick start](../../README.md#quick-start)).
The first two import the `livebridge_mcp` package, so run them with the MCP server's Python,
which the installer puts in `~/.livebridge/venv`:

```bash
~/.livebridge/venv/bin/python examples/python/hello_live.py
```

```bash
~/.livebridge/venv/bin/python examples/python/beat.py --tempo 126 --play
```

On Windows: `%USERPROFILE%\.livebridge\venv\Scripts\python.exe examples\python\hello_live.py`.
`raw_protocol.py` runs with any Python 3.

Output of `hello_live.py` on the set from [example 1](../01-house-beat.md):

```text
Ableton Live 12.4.5 suite · LiveBridge 1.0.0 · 200 commands
124 BPM · 4/4 · stopped
  song.tracks[0]           midi    Drums
  song.tracks[1]           midi    Bass
  song.tracks[2]           midi    Pad
  song.return_tracks[0]    return  A-Reverb
  song.return_tracks[1]    return  B-Delay
  song.master_track        master  Main
```

## Find the commands

- All 200 commands with their parameters, and the MCP tool behind each one: the
  [Bridge commands table](../../docs/TOOLS.md#bridge-commands).
- At runtime: `client.request("system.commands")`, or ask Claude for `live_commands`.
- Bridge commands take the same argument forms as the tools (track names, `"17.1.1"` positions,
  display values such as `"-6 dB"`). A few are stricter than the tool layer: `browser.load`
  wants `new_track="midi"` where `live_browser_load` also accepts `true`.
- Many commands in one round trip and one undo step: `system.batch`
  ([example 8](../08-escape-hatches.md#many-commands-one-undo-step)).

The token in `~/.livebridge/config.json` is required whenever the Remote Script has one (always
in LAN mode). Anyone with it can control Live, including running Python inside Live when
`allow_eval` is on, so keep it private.
