# Changelog

LiveBridge's version is the Remote Script's (`VERSION` in `remote_script/LiveBridge/LiveBridge.py`);
the MCP server package and the installer have their own version numbers. `live_status` shows both.

## 1.0.0 — 2026-09-23

The first public release: Claude controls Ableton Live 12 through MCP.

- **Remote Script** for Live 12 (standard-library Python 3.11): a TCP JSON-lines server that
  runs 200 bridge commands on Live's main thread, each change one undo step.
- **MCP server** `livebridge-mcp` with 168 tools in 22 areas: transport, tracks, mixer, routing,
  recording and resampling, clips and notes (patterns, chords, arps, transforms), arrangement,
  locators, scenes, devices, racks, Drum Racks, Simpler, the browser, plug-ins, samples, Splice
  import, automation (clip envelopes and recorded arrangement automation), view, and the Live
  Object Model (describe/get/set/call, batches, Python inside Live).
- **Serum 2 and other VST3 plug-ins** fully controllable without *Configure*: generated rack
  presets expose up to 128 parameters (`live_plugin_expose`, `live_plugin_param_map`).
- **Analysis:** `live_theory_analyze` (key, chords, Roman numerals, clashes between parts) and
  `live_audio_analyze` (LUFS, true peak, spectrum, stereo, tempo, key, energy; comparison with a
  reference track), plus a production guide for Claude (`livebridge://production`).
- **Two computers:** LAN mode with a token, UDP discovery (`live_discover`), `live_connect`, and
  automatic file upload to the Live machine.
- **Splice:** the installer registers Splice's official MCP server; `live_splice_*` imports the
  downloads into Live.
- **Installer** for macOS and Windows: Remote Script, config with a random token, MCP server,
  Claude Code and Claude Desktop registration, and the LiveBridge skill.
- **Documentation and examples:** eight example walkthroughs run against Live 12.4.5, a library
  of 100+ prompts, scripting examples, and a website.
- **Tests:** 1,500+ unit tests against a simulated Live on macOS, Windows and Linux, plus an
  end-to-end check for a real Live.
