# LiveBridge examples

What it looks like when you make music in Ableton Live by talking to Claude. You only type the
prompt; Claude picks the tools. Each page also shows the tool calls Claude makes, written as
function calls, so you can see what happens in Live and learn the tools if you script Live
yourself.

| # | Example | You say | Shows |
|---|---|---|---|
| 1 | [An 8-bar house beat](01-house-beat.md) | *"Make an 8-bar house beat at 124 BPM…"* | drum patterns, presets from the browser, a bassline, chords, mixing in dB |
| 2 | [Chords, bass and an arp that fit together](02-chords-bass-arp.md) | *"Write a melancholic lo-fi progression in D minor…"* | Roman numerals, arpeggios, a theory check that finds and fixes a clash |
| 3 | [A Reese bass in Serum 2](03-serum-2-reese-bass.md) | *"Load Serum 2 and design a dark Reese bass…"* | 120 Serum parameters without *Configure*, macros, an LFO on the filter |
| 4 | [Splice samples straight into your set](04-splice-samples.md) | *"Find a rolling hi-hat loop on Splice…"* | Splice's MCP server + LiveBridge: search, choose, download, import, warp |
| 5 | [From loop to song](05-song-structure.md) | *"Turn the loop into a full track…"* | scenes, a build-up with a clap roll and filter sweep, the arrangement, locators |
| 6 | [Mix check](06-mix-check.md) | *"Check my mix against my reference track…"* | meters, a bounce, LUFS and spectrum vs a reference, EQ, side-chain, limiter |
| 7 | [Live on another computer](07-two-computers.md) | *"Connect to Live on my Windows PC…"* | LAN mode, discovery, sending files to the Live machine |
| 8 | [Anything else](08-escape-hatches.md) | *"Rename every clip after its track and scene"* | the Live Object Model, batches, Python inside Live |

More: [100+ prompts to copy](prompts.md) · [Scripting Live from Python without Claude](python/).

## How these were made

Examples 1–6 and 8 were run against **Ableton Live 12.4.5 Suite on macOS** with LiveBridge 1.0
(September 2026). The JSON under the calls is real output, shortened. File names in the Splice
example are placeholders, and example 7 needs two computers, so it wasn't re-run for this page.
[`tests/test_examples.py`](../tests/test_examples.py) checks on every commit that each tool
and argument used on these pages exists.

The calls are written like Python so they're easy to read. `true`, `false` and `null` are JSON
values, as in the MCP messages Claude actually sends.

## Try them yourself

1. Install LiveBridge ([quick start](../README.md#quick-start)) and enable the control surface
   in Live.
2. Open Claude Code or Claude Desktop and paste a prompt from a page. Start with an empty set
   or a scratch set.
3. Everything Claude does is one undo step per call in Live: Cmd/Ctrl+Z takes it back.
