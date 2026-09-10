# LiveBridge troubleshooting

Start with `live_status` in Claude: its `error` line and `type` usually name the problem. From a
terminal, `python tests/integration_check.py --read-only` checks the connection without touching
the set (add `--host/--port/--token` for another machine).

- [Where is Log.txt?](#where-is-logtxt)
- [LiveBridge is not listed under Control Surface](#livebridge-is-not-listed-under-control-surface)
- [Listed, but Claude cannot connect](#listed-but-claude-cannot-connect)
- [Port 9880 already in use](#port-9880-already-in-use)
- [Token mismatch (`type: "auth"`)](#token-mismatch-type-auth)
- [Firewall / LAN problems](#firewall--lan-problems)
- [Timeouts](#timeouts)
- [Claude Desktop / Claude Code does not show the tools](#claude-desktop--claude-code-does-not-show-the-tools)
- [Plug-in parameters are missing](#plug-in-parameters-are-missing)
- [Windows paths and escaping](#windows-paths-and-escaping)
- [Installer problems](#installer-problems)
- [Other errors](#other-errors)

## Where is Log.txt?

Live writes the Remote Script's messages into its log (one folder per Live version):

| OS | Path |
|---|---|
| macOS | `~/Library/Preferences/Ableton/Live 12.4.5/Log.txt` (Finder: Go → Go to Folder…) |
| Windows | `%APPDATA%\Ableton\Live 12.4.5\Preferences\Log.txt` (paste into Explorer's address bar) |

LiveBridge lines look like:

```
RemoteScriptMessage: LiveBridge: [info] LiveBridge v1.0.0 ready on 127.0.0.1:9880 (Live 12.4.5, 140 commands, token required)
```

Search for `LiveBridge` and for `Traceback`. Tail it while you work:
`tail -f ~/Library/Preferences/Ableton/Live*/Log.txt` (macOS) or
`Get-Content "$env:APPDATA\Ableton\Live 12.4.5\Preferences\Log.txt" -Wait -Tail 50` (PowerShell).
Claude can write markers into it with `live_log("…")`.

## LiveBridge is not listed under Control Surface

1. **Restart Live.** Live scans the Remote Scripts folder only at startup.
2. **Check the folder.** It must be `…/User Library/Remote Scripts/LiveBridge/` containing
   `__init__.py` and `LiveBridge.py` — not `…/Remote Scripts/LiveBridge/LiveBridge/`, and not a
   ZIP. The installer prints the path it used.
3. **Custom User Library.** If you moved the User Library (Live → Preferences → Library), the
   script must be inside *that* one. The installer reads Live's `Library.cfg`; if it still picks
   the wrong place use `--remote-scripts-dir "<your User Library>/Remote Scripts"`.
4. **Several Live versions.** Each Live version has its own preferences, but they share the User
   Library. Make sure you look at Live 12 (the script needs Live 12's Python 3.11).
5. **Log.txt** shows import errors (e.g. a `SyntaxError` from a hand-edited file). Re-run the
   installer to restore a clean copy.

## Listed, but Claude cannot connect

- In *Link, Tempo & MIDI*, **LiveBridge must be selected in a Control Surface slot**; Input and
  Output stay *None*. The status bar should briefly show `LiveBridge … ready on port 9880`.
- `live_status` → `type: "connection"` and `refused`: nothing listens on that host/port. Check
  Log.txt for `could not bind` (next section) and that host/port in `~/.livebridge/config.json`
  match the Remote Script's `config.json`.
- The MCP server connects lazily and reconnects on its own after Live restarts — just retry.

## Port 9880 already in use

Log.txt: `could not bind 127.0.0.1:9880 (...) — is another Live instance running with
LiveBridge...`, status bar: `LiveBridge: port 9880 is in use`.

- Two Live instances (or LiveBridge selected in two Control Surface slots) — use one.
- Another program uses the port. Find it: macOS `lsof -nP -iTCP:9880 -sTCP:LISTEN`, Windows
  `netstat -ano | findstr :9880` then `tasklist /FI "PID eq <pid>"`.
- Pick another port on both sides: `install.py --port 9890` (on a paired machine:
  `--pair <ip> --port 9890 --token <T>`), then restart Live.
- Right after opening a set or re-selecting the control surface the old socket may linger for a
  moment: LiveBridge now **retries the bind by itself** (every second for 3 minutes, then every
  10 s). `live_status(include_server=true)` → `server.bind` shows `bound`, the `attempts`, the
  last `error` and `retry_in`. Only if it never binds: select *None*, wait a few seconds, select
  LiveBridge again.

## Token mismatch (`type: "auth"`)

The Remote Script rejects requests whose token differs from its `config.json`.

- Compare `token` in the Remote Script's `config.json` (Live machine) with
  `~/.livebridge/config.json` (Claude machine). Environment variables (`LIVEBRIDGE_TOKEN`) and CLI
  args override the file — `live_status` → `settings_from.token` says which one won.
- After `--new-token` on the Live machine, re-pair: `install.py --pair <ip> --token <new>` or
  `live_connect(host, token="<new>", persist=true)`.
- The Remote Script reads its config only when Live loads it: restart Live (or re-select the
  control surface) after editing `config.json` by hand.

## Firewall / LAN problems

- Is the Live machine in LAN mode? Its `config.json` must say `"host": "0.0.0.0"` (installer
  `--network`). With `127.0.0.1` it refuses connections from other computers.
- **Windows, Live machine**: allow *Ableton Live* on **Private** networks when Windows asks; make
  sure the network profile is *Private*; or add an inbound rule for **TCP 9880** (command in
  [INSTALL.md](INSTALL.md#firewall)). Third-party antivirus firewalls need the same exception.
- **Windows, Claude machine**: `live_discover` needs an inbound rule for **UDP 9881** there (the
  beacon arrives at the Claude machine, not at Live). Or skip discovery and `live_connect` by IP.
- **macOS firewall**: System Settings → Network → Firewall → Options… → allow incoming connections
  for Live (on the Live machine).
- **macOS 15+ Local Network permission** (System Settings → Privacy & Security → Local Network):
  on a Mac that runs Claude, Claude Desktop (or the terminal app running Claude Code) must be
  allowed, otherwise connecting to `192.168.x.x` fails with **"No route to host"** and
  `live_discover` hears nothing; on a Mac that runs Live in LAN mode, allow Ableton Live so its
  beacon goes out. Restart the app after switching it on. See
  [NETWORK.md](NETWORK.md#macos-local-network-permission).
- Test reachability from the Claude machine: macOS `nc -vz 192.168.1.20 9880`, Windows
  `Test-NetConnection 192.168.1.20 -Port 9880`.
- `live_discover` finds nothing but `live_connect` works: broadcasts are blocked (guest Wi-Fi, AP
  isolation, different subnets, VPN) — that is fine, connect by IP. See [NETWORK.md](NETWORK.md).

## Timeouts

`type: "timeout"`: the socket works but Live's main thread did not answer within the timeout
(default 10 s).

- A **modal dialog** is open in Live (save prompt, plug-in authorisation, preferences, export) —
  `live_dialog_get` shows its message and button count (`live_status(include_server=true)` →
  `server.dialog` too). Claude may answer it with `live_dialog_press(button, expect=...)` **only
  after confirming with you** which button (labels are not available — only the message and the
  count). A dialog that blocks Live's main thread blocks every command, `live_dialog_press`
  included: answer that one in Live yourself.
- Live is busy loading a set/plug-ins or indexing the browser (first browser search after start).
- A timed-out command **may still run** afterwards. Check the set (or `live_set_snapshot`) before
  repeating a mutating command, otherwise you might get it twice.
- Raise the default with `LIVEBRIDGE_TIMEOUT=30` / `--timeout 30` on the MCP server (max 120 s).

## Claude Desktop / Claude Code does not show the tools

- **Claude Desktop** loads MCP servers only at start: quit it completely (menu bar / tray → Quit)
  and reopen. Check the entry in `claude_desktop_config.json` (paths in [INSTALL.md](INSTALL.md#claude-desktop)).
  Its MCP logs: macOS `~/Library/Logs/Claude/mcp-server-livebridge.log`, Windows
  `%APPDATA%\Claude\logs\mcp-server-livebridge.log`.
- A broken JSON file stops Claude Desktop from loading any server — the installer never rewrites
  an unparsable file; validate it (e.g. `python -m json.tool claude_desktop_config.json`).
- **Claude Code**: `claude mcp list` / `claude mcp get livebridge`; inside a session `/mcp`.
  Re-register: `claude mcp remove --scope user livebridge` then the `claude mcp add …` line the
  installer printed.
- Test the server by hand: `~/.livebridge/venv/bin/livebridge-mcp --version` (Windows
  `%USERPROFILE%\.livebridge\venv\Scripts\livebridge-mcp.exe --version`). If it fails, re-run the
  installer. Never print to stdout from custom code — stdout is the MCP channel.

## Plug-in parameters are missing

Live exposes a plug-in's parameters to scripts exactly as it shows them in its device panel:

- Big plug-ins (Serum 2 VST3 and AU: **0 of 2623**) expose **nothing** after a plain load — Live
  only lists the device's Configure selection, and its API cannot add to it. For **VST3**
  plug-ins no click is needed: `live_plugin_expose(plugin="Serum 2", parameters=["sound_design"],
  new_track=true)` loads the plug-in inside a generated rack preset that exposes up to 128 chosen
  parameters (a curated Serum 2 "sound_design" set ships with LiveBridge, optional rack-macro
  wiring with `macros=`, starting sounds with `preset_file=` from `live_plugin_preset_files`).
  Other VST3 plug-ins need `live_plugin_param_map(plugin=...)` once (it probes the parameter ids,
  ~30 s). Details: [PLUGIN_RACKS.md](PLUGIN_RACKS.md). Limits: 128 parameters per rack, the sound
  restarts from the preset/template when you re-expose, `.SerumPreset` files cannot be embedded,
  and Audio Units expose nothing this way — prefer the VST3 version.
- Fallback (AU / VST2, or when you prefer it): click the plug-in device's **Configure** button
  (wrench), move the knobs you want in the plug-in window (each becomes a parameter), click
  Configure again. Save the set (or a default preset) so the selection sticks. Then
  `live_plugin_parameters` lists them (`live_plugin_configure` waits for them).
- `editor_open` on `live_plugin_expose` / `live_plugin_set` shows or hides the plug-in window
  (Live opens it on load when *Auto-Open Plug-In Windows* is on).
- Parameters that are MIDI-mapped or macro-mapped can be read-only (`enabled: false`) — set the
  macro instead (`live_rack_macros`).
- Plug-in *presets* come from the plug-in's own program list (`live_plugin_presets`); many
  plug-ins manage presets internally and expose only one program.
- A plug-in only appears in the browser when *Preferences → Plug-Ins* has VST2/VST3 (Windows/Mac)
  or Audio Units (Mac) enabled and it was scanned (use *Rescan* there).

## Windows paths and escaping

- In **JSON** (config files, Claude Desktop config) backslashes must be doubled:
  `"C:\\Users\\me\\Music\\kick.wav"` — or use forward slashes, which Windows and Live accept:
  `"C:/Users/me/Music/kick.wav"`.
- In tool arguments (e.g. `live_sample_import(file_path=...)`) pass a normal absolute path; the
  MCP client encodes it. Paths must exist **on the machine that runs Live** (for LAN setups that
  is not the Claude machine).
- In PowerShell quote paths with spaces: `--remote-scripts-dir "D:\Music\User Library\Remote Scripts"`.
  In `cmd.exe` use double quotes too; single quotes do not quote there.
- OneDrive: if *Documents* is synced by OneDrive, Live's User Library may live under
  `%USERPROFILE%\OneDrive\Documents\Ableton\User Library`. The installer checks there; mark the
  folder "Always keep on this device" so Live finds the files offline.
- Non-ASCII user names are fine; if a console shows `?` instead of `→`, it is only the console
  font/encoding. The dev tools (`tests/live_query.py`, `tests/integration_check.py`) print UTF-8
  even into a pipe, so names like "C♯" or emoji no longer crash them on Windows.
- OneDrive for Business redirects *Documents* to `%USERPROFILE%\OneDrive - <Company>\Documents`;
  the installer and the dev tools find it through the `OneDriveCommercial` variable (and read the
  exact Remote Script folder from `~/.livebridge/install.json` afterwards).

## Installer problems

| Message | Fix |
|---|---|
| `no Python 3.10+ found` | Install Python 3.12 (python.org / `brew install python` / `winget install Python.Python.3.12`), or `--uv`, or `--python <path>`. |
| `externally-managed-environment` / PEP 668 | Don't use `--no-venv` with Homebrew/system Python; the default venv avoids it. |
| `--pair needs --token` | Copy the token from the Live machine's checklist or its `config.json`. |
| Claude Desktop not found | Start Claude Desktop once (it creates its settings folder) and re-run, or pass `--desktop-config`. |
| `claude mcp add` failed | Run the printed command yourself; check `claude --version`. |
| `could not remove the old …LiveBridge` (Windows) | Live holds a file open: quit Live, re-run the installer. |
| `could not remove the old virtual environment … in use` (Windows) | Claude Desktop (still running in the tray) or a Claude Code session holds `livebridge-mcp.exe` / `python.exe`: quit them (tray icon → Quit), re-run. |
| `… is not usable for the MCP server … was not created by the LiveBridge installer` | Your `--venv-dir` has an old/broken Python; the installer never deletes a venv it did not create. Fix or remove it, or pick another `--venv-dir`. |
| `… has no pip and ensurepip failed` | The venv was made by `uv venv` without pip: install uv and re-run with `--uv`, or delete `~/.livebridge/venv` and re-run. |
| Uninstaller: `could not remove … files in it are in use` (exit code 1) | Quit Claude Desktop (tray → Quit), Claude Code sessions using LiveBridge (and Live for the Remote Script), then run the uninstaller again — it kept `install.json` so it knows what is left. |
| A re-run turned LAN mode / `--no-allow-eval` off | Not any more: re-runs keep the installed settings and print them under *Settings* ("kept from the previous install"). Change them with `--network/--no-network`, `--local`, `--allow-eval/--no-allow-eval`, `--beacon/--no-beacon`, `--port`, `--pair`. |
| PowerShell: "running scripts is disabled" | Use `powershell -ExecutionPolicy Bypass -File installers\install.ps1`, or `py installers\install.py`. |

`--dry-run` shows every step without writing anything.

## Other errors

| `type` | Meaning |
|---|---|
| `not_found` | An index/name/path does not exist (anymore) — indices shift after adding/deleting tracks, scenes or devices. Re-read with `live_set_snapshot`. |
| `invalid_state` | Live refused in the current state: slot already has a clip, track cannot be armed, MIDI clip on an audio track, frozen track… The message says which. |
| `unsupported` | This Live version/edition cannot do it (e.g. `insert_device` before 12.3, Intro track limits). |
| `bad_args` | Wrong argument; the message lists the valid ones. |
| `forbidden` | `live_eval_python` is disabled: `allow_eval: false`, **or no token is configured** (eval is always token-protected). Set a token in both config files (re-run the installer, or see [INSTALL.md](INSTALL.md#manual-install-without-the-installer)) and restart Live. |
| `internal` | A bug — the message includes Live's error; the traceback is in Log.txt. Please report it with the Log.txt lines. |

Things that are simply not possible through Live's API (saving, exporting audio, freezing,
grouping tracks, arrangement automation, mapping macros, deleting rack chains, saving presets,
Sampler zones, the Drum Sampler's sample) are listed in [LIVE_API_NOTES.md](LIVE_API_NOTES.md#12-not-possible-through-the-lom--and-what-to-do-instead)
with the workaround for each.
