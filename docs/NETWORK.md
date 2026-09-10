# LiveBridge networking

How Claude reaches Live: on the same computer (default) or across your network, how the token and
the discovery beacon work, and how to switch between machines while you work.

```
 Claude machine                                        Live machine
 ┌────────────────────┐   TCP 9880, JSON lines, token   ┌──────────────────────────────┐
 │ livebridge-mcp     │ ──────────────────────────────▶ │ LiveBridge Remote Script     │
 │ ~/.livebridge/     │                                 │ Remote Scripts/LiveBridge/   │
 │   config.json      │ ◀── UDP 9881 beacon (every 2 s) │   config.json                │
 └────────────────────┘                                 └──────────────────────────────┘
```

## Ports

| Port | Protocol | Direction | Purpose |
|---|---|---|---|
| **9880** | TCP | Claude → Live | Commands (newline-delimited JSON, [PROTOCOL.md](PROTOCOL.md)). Change with the installer's `--port` (both sides) or `port` in both config files. |
| **9881** | UDP broadcast | Live → LAN | Discovery beacon, only when `beacon` is on. Change with `beacon_port` in the Remote Script config and `live_discover(port=...)`. |

Which machine needs which **incoming** rule (details in [INSTALL.md](INSTALL.md#firewall)):

| Machine | Incoming | Why |
|---|---|---|
| Live machine (LAN mode) | **TCP 9880** for Ableton Live | Claude connects to it. The beacon it sends is outgoing — no UDP rule needed there. |
| Claude machine | **UDP 9881** for the MCP server's Python (`livebridge-mcp`), only for `live_discover` | `live_discover` listens for the beacons. Without it, connect by IP with `live_connect`. |

On **macOS 15 (Sequoia) and later** the *Local Network* privacy setting applies on top of the
firewall — see [below](#macos-local-network-permission).

## Localhost mode (default)

`host: "127.0.0.1"` in the Remote Script config: Live accepts connections from the same computer
only. Nothing is reachable from the network, no firewall prompt appears, the beacon is off. The
installer still sets a token, and the MCP server sends it from `~/.livebridge/config.json`.

## LAN mode

`host: "0.0.0.0"` (installer: `--network`): Live listens on every interface, so Claude on another
computer can connect. In LAN mode:

- **The token is always required.** The installer generates a 128-bit random token
  (`secrets.token_hex(16)`) and writes it into both config files. Every request must carry it;
  wrong or missing tokens are answered with `type: "auth"`, nothing runs and the connection is
  closed. A host that never sends the token is dropped 10 s after connecting (and does not take
  one of the `max_clients` slots). A config with `host: "0.0.0.0"` but **no token** is not
  served on the network: the Remote Script falls back to `127.0.0.1` and says so in Live's
  Log.txt.
- **The beacon is on**, so `live_discover` can find the machine.
- The MCP server on the Live machine itself keeps using `127.0.0.1`.
- It **stays on** when you re-run the installer without flags (e.g. to update): the installer
  keeps LAN mode, port, `allow_eval`, beacon and a paired / `live_connect`-persisted host.
  Leave LAN mode with `install.py --no-network` (Remote Script only) or `install.py --local`
  (Remote Script *and* the MCP server back to this machine).

Security notes:

- The token protects against other people on your network, but the connection is **not
  encrypted** (plain TCP). Use LAN mode on your home/studio network only — not on public Wi-Fi.
- Anyone with the token can run any command, including `live_eval_python` (arbitrary Python inside
  Live) when `allow_eval` is true. Install with `--no-allow-eval` if the token is shared more
  widely. Rotate it with `--new-token` (then re-pair the other machine).
- Across untrusted networks, keep Live in localhost mode and tunnel:
  `ssh -N -L 9880:127.0.0.1:9880 you@live-machine`, then point the MCP server at `127.0.0.1:9880`
  with the Live machine's token. VPNs such as Tailscale/WireGuard work too (use the VPN IP with
  `live_connect`; beacons do not cross into a VPN).

## The token

| Where | File |
|---|---|
| Remote Script (Live machine) | macOS `~/Music/Ableton/User Library/Remote Scripts/LiveBridge/config.json`, Windows `%USERPROFILE%\Documents\Ableton\User Library\Remote Scripts\LiveBridge\config.json` (or your custom User Library) |
| MCP server (Claude machine) | `~/.livebridge/config.json` (`%USERPROFILE%\.livebridge\config.json`), or `LIVEBRIDGE_CONFIG` |

The installer prints it at the end. To read it later: open the Remote Script's `config.json`, or
run `python -c "import json,pathlib; print(json.loads(pathlib.Path.home().joinpath('.livebridge','config.json').read_text())['token'])"`.

The Remote Script reads its config when Live loads the control surface — after changing it,
restart Live or re-select LiveBridge in *Preferences → Link, Tempo & MIDI → Control Surface*.

## macOS Local Network permission

macOS 15+ asks each app before it may talk to devices on the local network (System Settings →
Privacy & Security → **Local Network**). LiveBridge needs it in two places:

- **The Claude app on a Mac that controls Live elsewhere** — Claude Desktop, or the terminal app
  that runs Claude Code (Terminal, iTerm, VS Code …), because the MCP server is their child
  process. Without it, `live_status` / `live_connect` to `192.168.x.x` fail with
  **"No route to host"** (`EHOSTUNREACH`) although the other machine answers pings from
  elsewhere, and `live_discover` hears no beacons.
- **Ableton Live on a Mac in LAN mode** — so its discovery beacon can go out. (Commands to Live
  still work by IP without it on most setups; allow it anyway.)

macOS shows the prompt the first time; if it was denied, switch the app on in that list and
restart it. Localhost mode (everything on one Mac) needs no permission.

## Discovery beacon

While `beacon` is on, the Remote Script sends this every 2 seconds to `255.255.255.255:9881`
(and to `127.0.0.1:9881`, for when broadcasts are filtered):

```json
{"livebridge": 1, "name": "Studio-PC", "host": "192.168.1.20", "port": 9880,
 "live": "12.4.5", "version": "1.0.0", "needs_token": true}
```

`live_discover(seconds=3)` listens on `0.0.0.0:9881` and lists what it hears:
`{"instances": [{"name", "host", "port", "live", "needs_token"}], "notes": [...]}`. The beacon
contains no token. Broadcasts stay inside one subnet: no discovery across routers, VLANs, guest
Wi-Fi isolation or VPNs — connect by IP instead. On the *Claude* machine, the firewall must let
UDP 9881 in (Windows: `New-NetFirewallRule -DisplayName "LiveBridge discovery" -Direction Inbound
-Protocol UDP -LocalPort 9881 -Action Allow -Profile Private`) and, on macOS 15+, the Claude app
needs Local Network access.

## Files (samples, MIDI, presets)

Every sample path LiveBridge hands to Live is a path **on the Live machine**. In LAN mode a file
that only exists on Claude's machine (a Splice download, a rendered loop) is sent over first:

- `live_sample_import(file_path, ...)` and `live_splice_import_downloaded(files=[...])` notice
  that the path does not exist on the Live machine and send the file with the bridge command
  `samples.receive`: 4 MiB chunks, sha256 checked at the end, audio / MIDI / preset types only,
  the token required like for every command.
- It lands in the Live machine's inbox: `<User Library>/Samples/LiveBridge` (else
  `~/LiveBridge/Samples`), and the import continues from there.
- `live_sample_upload(local_path | url)` does it explicitly and returns the Live-side path;
  URLs are downloaded on Claude's machine first.

On one computer nothing is copied — the path is used as it is.

## Switching machines

The MCP server picks its target at start-up with this precedence: CLI arguments
(`--host/--port/--token`) > environment (`LIVEBRIDGE_HOST`, `LIVEBRIDGE_PORT`, `LIVEBRIDGE_TOKEN`,
`LIVEBRIDGE_TIMEOUT`) > `~/.livebridge/config.json` > `127.0.0.1:9880`. `live_status` shows where
each value came from (`settings_from`) and both halves' versions (`mcp_host.version`,
`bridge_version`, two separate numbers) — keep the two machines on the same LiveBridge
checkout or bundle, so both report the same pair. Tokens are bound to
their endpoint: a `--host` / `LIVEBRIDGE_HOST` override that points at another machine never
receives the file's token (pass `--token` / `LIVEBRIDGE_TOKEN`, or persist it with
`live_connect`).

At runtime, without restarting Claude:

1. `live_discover()` → e.g. `Studio-PC 192.168.1.20:9880 (needs_token)`.
2. `live_connect(host="192.168.1.20", port=9880, token="…")` → verifies with `system.hello`.
3. Add `persist=true` to write host/port/token into `~/.livebridge/config.json`, so the next
   Claude session connects there automatically. `live_connect("127.0.0.1", token="…",
   persist=true)` switches back to the local Live.

Typical setups:

| Setup | Live machine | Claude machine |
|---|---|---|
| One computer | `install.py` | (same) |
| Back to one computer after LAN use | `install.py --local` | (same) |
| Claude on the Mac, Live on the PC | `install.py --network` on the PC | `install.py --pair <PC-IP> --token <T>` on the Mac |
| Both run Live and Claude, you alternate | `install.py --network --token <T>` on both | switch with `live_discover` / `live_connect(..., persist=true)` |

Tip: give the Live machine a fixed IP (a DHCP reservation in your router) so a persisted
`host` keeps working, or use its mDNS name (`Studio-Mac.local` on macOS; Windows machines usually
answer to their computer name on the LAN).

## Diagnosing connections

| Symptom (`live_status`) | Meaning / fix |
|---|---|
| `type: "connection"` — refused | Live is not running, LiveBridge is not selected as a control surface, or wrong port. |
| `type: "connection"` — timed out | Firewall drops the packets, wrong IP, or Live is in localhost mode (`host: 127.0.0.1`) on the other machine. |
| `type: "connection"` — "No route to host" (Claude on a Mac) | macOS Local Network permission missing for Claude Desktop / your terminal ([above](#macos-local-network-permission)), or the IP is wrong. |
| `type: "auth"` | Token mismatch: compare both `config.json` files or re-run `--pair` with the right token. |
| `type: "timeout"` | Connected, but Live's main thread did not answer in time (modal dialog open, heavy loading). |
| `live_discover` finds nothing | Beacon off (`--network` enables it), different subnet, UDP 9881 blocked **on the Claude machine**, or (macOS 15+) Local Network access missing for Claude or for Live — use `live_connect` with the IP. |

From a terminal: `python tests/integration_check.py --host <ip> --token <token> --read-only`.
More in [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
