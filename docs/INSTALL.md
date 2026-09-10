# Installing LiveBridge

LiveBridge has two halves: the **Remote Script** that Live loads (on the computer that runs Live)
and the **MCP server** that Claude starts (on the computer that runs Claude). On a single computer
the installer sets up both; for two computers see [LAN pairing](#two-computers-lan-pairing).

- [What you need](#what-you-need)
- [macOS](#macos) · [Windows](#windows)
- [What the installer does](#what-the-installer-does) · [All flags](#installer-flags)
- [Claude Desktop](#claude-desktop) · [Claude Code](#claude-code) · [The skill](#the-livebridge-skill) · [Fewer tools](#fewer-tools-toolsets) · [Splice](#splice)
- [Two computers (LAN pairing)](#two-computers-lan-pairing) · [Getting LiveBridge onto the other computer](#getting-livebridge-onto-the-other-computer) · [Firewall](#firewall) · [macOS Local Network](#macos-local-network-permission)
- [Verify](#verify) · [Update](#update) · [Uninstall](#uninstall) · [Manual install](#manual-install-without-the-installer)

## What you need

| | Needed for | Notes |
|---|---|---|
| Ableton Live 12 (Suite, Standard, Intro, Lite) | the Live machine | Built against 12.4.5. `live_device_insert` needs 12.3+. |
| Python 3.10+ | the MCP server | The installer runs on 3.9+ and searches PATH, the `py` launcher and the usual install folders for a 3.10+ interpreter. `--uv` lets uv download one. |
| Claude Desktop and/or Claude Code | the Claude machine | Claude Code's `claude` CLI must be on PATH for automatic registration (otherwise the installer prints the command). |
| Node.js (`npx`) | optional | Only for Splice inside Claude Desktop's JSON config (the alternative is a custom connector, no Node needed). |
| git | optional | To clone and later `git pull`; a ZIP made with `installers/bundle.py` works too ([other computer](#getting-livebridge-onto-the-other-computer)). |

Live's embedded Python runs the Remote Script — nothing to install inside Live.

## macOS

1. Install Python 3.10+ if `python3 --version` shows 3.9 (the Xcode one): download it from
   <https://www.python.org/downloads/macos/> or `brew install python`.
2. In Terminal:

   ```bash
   cd ~/Code                                   # anywhere you like; keep the folder (editable install)
   git clone <repository-url> LiveBridge       # or unzip a bundle (installers/bundle.py)
   cd LiveBridge
   ./installers/install.sh --dry-run           # optional: see what will happen
   ./installers/install.sh
   ```

3. Start (or restart) Live → **Live → Settings/Preferences → Link, Tempo & MIDI → Control Surface**
   → pick **LiveBridge** in a free slot, Input **None**, Output **None**. The status bar shows
   `LiveBridge … ready on port 9880`.
4. Quit Claude Desktop completely (menu bar → Quit) and reopen it, or start a new Claude Code
   session.

The Remote Script goes to `~/Music/Ableton/User Library/Remote Scripts/LiveBridge` — or to the
User Library you chose in Live (the installer reads it from
`~/Library/Preferences/Ableton/Live 12.x.x/Library.cfg`).

## Windows

1. Install Python 3.10+ from <https://www.python.org/downloads/windows/> (tick **Add python.exe to
   PATH**) or `winget install Python.Python.3.12`. The Microsoft Store "python" alias is not enough.
2. In PowerShell:

   ```powershell
   cd $HOME\Code
   git clone <repository-url> LiveBridge       # or unzip a bundle (installers/bundle.py)
   cd LiveBridge
   powershell -ExecutionPolicy Bypass -File installers\install.ps1 --dry-run
   powershell -ExecutionPolicy Bypass -File installers\install.ps1
   ```

   (`py installers\install.py` works too; the `.ps1` just finds Python for you.)
3. Start (or restart) Live → **Options → Preferences → Link, Tempo & MIDI → Control Surface →
   LiveBridge**, Input/Output **None**.
4. Quit Claude Desktop from the tray icon (closing the window is not enough) and reopen it.

The Remote Script goes to `%USERPROFILE%\Documents\Ableton\User Library\Remote Scripts\LiveBridge`,
the OneDrive-redirected `Documents` folder when that is where Live's User Library lives, or the
location recorded in `%APPDATA%\Ableton\Live 12.x.x\Preferences\Library.cfg`.

## What the installer does

Every step is idempotent; re-running updates in place and keeps your token **and every setting
you do not pass again**: LAN mode, port, `allow_eval`, beacon and the host the MCP server talks to
(a `--pair` host or one saved by `live_connect(..., persist=true)`). The *Settings* step prints
the effective values and which ones were "kept from the previous install".

1. **Finds Live's Remote Scripts folder**: `--remote-scripts-dir`, else the User Library in Live's
   newest `Library.cfg`, else the default location.
2. **Copies `remote_script/LiveBridge`** there, replacing an older copy completely (stale files and
   `__pycache__` are removed; a developer symlink is left alone).
3. **Writes the Remote Script config** `Remote Scripts/LiveBridge/config.json`:

   ```json
   { "host": "127.0.0.1", "port": 9880, "token": "<32 hex chars>", "allow_eval": true,
     "beacon": false, "beacon_port": 9881, "name": "<this computer>" }
   ```

   `host` is `0.0.0.0` and `beacon` `true` with `--network` (and stay so on later runs until
   `--no-network` / `--local`). Keys you added yourself
   (`max_clients`, `log_level`, `idle_timeout`, …) are kept. The token is generated once with
   Python's `secrets` module and reused on later runs (`--new-token` rotates it).
4. **Writes the MCP server config** `~/.livebridge/config.json` (`%USERPROFILE%\.livebridge\config.json`):
   `{"host": "127.0.0.1" or the --pair host (or the host already there), "port": 9880, "token":
   "<same token>"}`. On macOS both config files are `chmod 600`. Tokens are bound to their
   endpoint: the top-level token belongs to the file's host/port, `live_connect(persist=true)`
   adds others to a `"tokens": {"host:port": token}` map, and a `--host` / `LIVEBRIDGE_HOST`
   override pointing at another machine never receives the file's token — pass `--token` /
   `LIVEBRIDGE_TOKEN` for it, or persist it with `live_connect`.
5. **Installs the MCP server**: creates `~/.livebridge/venv` with a Python 3.10+ and runs
   `pip install -e <repo>/mcp_server` in it (editable: `git pull` updates the code; re-run the
   installer when dependencies change). The console script is
   `~/.livebridge/venv/bin/livebridge-mcp` (Windows: `…\venv\Scripts\livebridge-mcp.exe`).
   A venv made with `--uv` gets pip too (`uv venv --seed`), and later runs use uv again when it is
   on PATH. The installer only ever deletes and recreates a venv it created itself.
6. **Registers it with Claude** (see below) — merging, never overwriting other servers, with a
   `*.livebridge-backup` of the previous file.
7. **Installs the skill** — the production workflow in `.claude/skills/livebridge` — for Claude
   Code in `~/.claude/skills/livebridge` and as `~/.livebridge/livebridge-skill.zip` for Claude
   Desktop ([details](#the-livebridge-skill)).
8. **Records what it did** in `~/.livebridge/install.json` (used by the uninstaller) and prints a
   checklist including the token.

## Installer flags

| Flag | Meaning |
|---|---|
| `--dry-run` | Print every action (copies, writes, commands), write nothing. |
| `--network` / `--no-network` | LAN mode: the Remote Script listens on `0.0.0.0`; beacon on; token enforced. `--no-network` goes back to `127.0.0.1`. Default: keep the installed setting (first install: off). |
| `--local` | Everything on this machine only: `--no-network` **and** the MCP server back to `127.0.0.1` (undoes `--network` and `--pair`). |
| `--pair HOST` | Claude on this machine controls Live on `HOST` (needs `--token`). Kept on later runs. |
| `--port N` | TCP port, used on both sides (default: keep the installed one, else 9880). |
| `--token T` / `--new-token` | Use this token / rotate to a new random one (default: keep the installed one). |
| `--name NAME` | Machine name shown by `live_discover` (default: host name). |
| `--allow-eval` / `--no-allow-eval` | Allow `live_eval_python` (always token-protected). Default: keep the installed setting (first install: on). |
| `--beacon` / `--no-beacon` | UDP discovery beacon (default: follows `--network`/`--no-network`, else keeps the installed setting). |
| `--remote-scripts-dir DIR` | Live's `Remote Scripts` folder, if auto-detection picks the wrong one. |
| `--no-remote-script` | Skip the Live side (a computer without Live). |
| `--no-mcp` | Skip the MCP server and Claude registration (a Live-only computer). |
| `--no-desktop` / `--desktop-config PATH` | Skip Claude Desktop / use this `claude_desktop_config.json`. |
| `--no-claude-code` | Skip `claude mcp add`. |
| `--splice` / `--no-splice` | Register Splice's MCP too (default on). |
| `--skill` / `--no-skill` | Install the LiveBridge skill for Claude Code and build the Claude Desktop zip (default on). |
| `--python PATH` | Python 3.10+ for the MCP server's venv. |
| `--uv` | Create the venv and install with uv (uv can download Python). Later runs reuse uv when it is on PATH. |
| `--venv-dir DIR` / `--no-venv` | Other venv location / pip install into `--python` directly. |
| `--no-pip` | Don't run pip; reuse the installed `livebridge-mcp`. |

`install.sh` / `install.ps1` pass all flags through; `--uninstall` as the first argument runs the
uninstaller instead.

## Claude Desktop

The installer adds this to `claude_desktop_config.json`
(macOS `~/Library/Application Support/Claude/`, Windows `%APPDATA%\Claude\` and, for the Microsoft
Store version, `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\`):

```json
{
  "mcpServers": {
    "livebridge": {
      "command": "/Users/you/.livebridge/venv/bin/livebridge-mcp",
      "args": [],
      "env": { "LIVEBRIDGE_CONFIG": "/Users/you/.livebridge/config.json" }
    }
  }
}
```

The token is deliberately **not** put into `env`: environment variables override the config file,
which would break switching machines with `live_connect(..., persist=true)`.

If Claude Desktop has never been started (no settings folder yet) the installer skips it — start
Claude Desktop once and re-run, or pass `--desktop-config`. After any change: quit Claude Desktop
completely and reopen it. The tools then appear in the tools/connectors menu of the chat box.

## Claude Code

```bash
claude mcp add --scope user livebridge -- /Users/you/.livebridge/venv/bin/livebridge-mcp
claude mcp list            # livebridge should show "connected" while Live runs
```

The installer runs exactly this (after removing an older `livebridge` entry) when `claude` is on
PATH, otherwise it prints the command. Windows: use the `…\venv\Scripts\livebridge-mcp.exe` path
in quotes. Inside a Claude Code session, `/mcp` shows the server state.

## The LiveBridge skill

`.claude/skills/livebridge/SKILL.md` teaches Claude the production workflow (session start,
conventions, composite tools, recipes for beats/bass/chords/arrangement, what Live's API cannot
do). The installer deploys it for both clients:

- **Claude Code**: copied to `~/.claude/skills/livebridge/` (Windows
  `%USERPROFILE%\.claude\skills\livebridge\`; `$CLAUDE_CONFIG_DIR/skills/` when you set that), so
  it works in every folder, not only inside this repository. Re-runs update it; a symlink there
  (developer setup) or a different skill of that name is left alone.
- **Claude Desktop** cannot read that folder. The installer builds
  `~/.livebridge/livebridge-skill.zip`; upload it once: **Settings → Capabilities → Skills →
  Upload skill** (skills need *Code execution and file creation* switched on in the same place).
  After an update, upload the new zip again (the checklist reminds you).

`--no-skill` skips both; the uninstaller removes both (`--keep-skill` keeps them) — an uploaded
Desktop skill you remove in the same Settings page. Without the upload Claude can still read the
workflow: the MCP server serves the same text as the resource `livebridge://skill` and the prompt
`livebridge_workflow` (it reads it from the repository folder the server was installed from).

## Fewer tools (toolsets)

Every tool definition costs context in every chat: all tools ≈ 61k tokens, `production` ≈ 57k,
`core` ≈ 37k, `minimal` ≈ 21k. Pick a smaller set with `livebridge-mcp --toolsets core`, the
environment variable `LIVEBRIDGE_TOOLSETS=core`, or `"toolsets": "core"` (or a list) in
`~/.livebridge/config.json`.

| Profile | Tool modules |
|---|---|
| `minimal` | system, lom, transport, tracks, clips, notes |
| `core` | minimal + devices, mixer, scenes, browser |
| `production` | core + arrangement, automation, racks, plugins, plugin_racks, samples, splice, record, routing |
| `all` | everything (default) |

Items combine and exclude: `core,automation` or `all,-view,-cues`. `system` and `lom` are always
loaded; commands of hidden modules still run through `live_command_call`, and `live_status` lists
`hidden_tool_modules`.

## Splice

- **Claude Code**: `claude mcp add --transport http --scope user splice https://mcp.splice.com/mcp`,
  then `/mcp` → `splice` → sign in with your Splice account.
- **Claude Desktop**: the installer adds `"splice": {"command": "npx", "args": ["-y", "mcp-remote",
  "https://mcp.splice.com/mcp"]}` when Node.js is installed (a browser opens for the Splice login on
  first use). Without Node: **Settings → Connectors → Add custom connector**, URL
  `https://mcp.splice.com/mcp`.
- Searching is free; downloading needs a paid Splice plan (100 downloads per 24 h via MCP).
  Splice's MCP saves a download where you choose (the Splice desktop app is not needed). Pass the
  reported path(s) to `live_splice_import_downloaded(files=[...])`; when Live runs on another
  computer LiveBridge sends the files over into `<User Library>/Samples/LiveBridge` on the Live
  machine. Without files it searches the Splice folder, Live's own Splice folder, that inbox,
  Downloads and Desktop of the Live machine. Ask Claude for `live_splice_setup_info` to see the
  detected folders.

## Two computers (LAN pairing)

Example: Live on the Windows PC `192.168.1.20`, Claude on the Mac.

1. **On the Live computer** (Windows):

   ```powershell
   powershell -ExecutionPolicy Bypass -File installers\install.ps1 --network
   ```

   The checklist prints the token, the PC's IP and the exact pairing command. Allow the firewall
   prompt for Live (incoming TCP 9880, see [Firewall](#firewall)). Restart Live so the new config
   is loaded.
2. **On the Claude computer** (Mac):

   ```bash
   ./installers/install.sh --pair 192.168.1.20 --token <token from step 1>
   ```

   On a Mac with macOS 15+, allow Claude (or your terminal, for Claude Code) under Privacy &
   Security → Local Network ([details](#macos-local-network-permission)).
3. Ask Claude for `live_status`. It should report `connected: true` and the PC's Live version.

Both machines can run Live *and* Claude; install with `--network` on both using the same
`--token`, and switch at runtime with `live_discover` + `live_connect(host, port, token,
persist=true)`. Details: [NETWORK.md](NETWORK.md).

Re-running the installer on either machine (for an update) keeps LAN mode and the paired host;
`--local` makes a machine standalone again.

## Getting LiveBridge onto the other computer

The installer runs from a LiveBridge folder on **each** computer, and the MCP server is installed
*editable* from that folder — so it must stay where it is.

- **With git** (the repository has a remote you can reach from both computers): `git clone` on
  each, `git pull` + re-run the installer to update.
- **Without a remote** (a local checkout only): make a ZIP on the computer that has the code and
  copy it over (USB stick, AirDrop, a network share, cloud drive):

  ```bash
  python installers/bundle.py                 # -> dist/LiveBridge-<version>.zip
  ```

  On the other computer unzip it into a permanent folder (e.g. `C:\Users\you\Code\LiveBridge`
  or `~/Code/LiveBridge` — **not** Downloads, which you might clean up) and run the installer
  there. The ZIP contains the Remote Script, MCP server, installers, docs, tests and the skill —
  never caches, virtual environments, git data or a developer `config.json` with a token.
- **Keep both computers on the same version**: after changing the code on one, bundle (or pull)
  again, unzip over the old folder on the other one and re-run the installer (token and settings
  are kept). `live_status` shows both versions: the Remote Script's (`bridge_version`) and the
  MCP server's (`mcp_host.version`, also `livebridge-mcp --version`).
- **Moved or deleted the folder?** Claude's LiveBridge stops starting (the editable install
  points at the old place). Re-run the installer from the new folder — it re-links everything
  and says where the previous install came from.

## Firewall

Each machine needs a different **incoming** rule:

| Machine | Allow incoming | For |
|---|---|---|
| The **Live** machine (LAN mode) | TCP 9880, for Ableton Live | Claude's commands. The discovery beacon Live sends is *outgoing* — no UDP rule here. |
| The **Claude** machine | UDP 9881, for the MCP server's Python | `live_discover` only. Without it, connect by IP with `live_connect`. |

- **Windows Defender Firewall, Live machine**: the first time Live listens on `0.0.0.0` Windows
  asks whether *Ableton Live* may communicate — allow **Private networks** (not Public). Missed
  it? Allow it manually (admin PowerShell):

  ```powershell
  New-NetFirewallRule -DisplayName "LiveBridge TCP 9880" -Direction Inbound -Protocol TCP -LocalPort 9880 -Action Allow -Profile Private
  ```

- **Windows Defender Firewall, Claude machine** (Claude on the PC, Live on the Mac) — for
  `live_discover`:

  ```powershell
  New-NetFirewallRule -DisplayName "LiveBridge discovery" -Direction Inbound -Protocol UDP -LocalPort 9881 -Action Allow -Profile Private
  ```

  On both: make sure the Wi-Fi/Ethernet network is set to *Private* (Settings → Network &
  internet → your network → Network profile type).
- **macOS firewall** (System Settings → Network → Firewall): on the Live Mac, allow incoming
  connections for **Live** when asked, or add Ableton Live under *Options…*. On a Claude Mac with
  "Block all incoming connections" enabled, discovery beacons cannot arrive — use `live_connect`
  with the IP instead of `live_discover`.
- Localhost mode (the default) needs no firewall changes.

The installer prints the matching advice for the machine it runs on (`--network`: the Live
machine; `--pair`: the Claude machine).

## macOS Local Network permission

macOS 15 (Sequoia) and later also ask per app for **Local Network** access (System Settings →
Privacy & Security → Local Network):

- On a Mac that **runs Claude** against Live on another computer, allow **Claude** (Claude
  Desktop) or the terminal app that runs Claude Code (Terminal, iTerm, VS Code …). Without it,
  `live_status` fails with **"No route to host"** and `live_discover` hears nothing.
- On a Mac that **runs Live in LAN mode**, allow **Ableton Live** so its discovery beacon can go
  out.

macOS asks the first time; if you clicked *Don't Allow*, switch the app on in that list and
restart it. Everything on one Mac (localhost mode) needs no permission.

## Verify

- In Claude: *"run live_status"* → `connected: true`, Live version and edition.
- From a terminal: `python tests/integration_check.py` (add `--host/--token` for another machine,
  `--read-only` to change nothing). It creates a temporary track, writes notes, loads an
  instrument, sets a parameter, fires and stops the clip, and deletes the track again — printing
  PASS/FAIL per step. `--scenario beat` adds the whole "make a beat" chain on temporary
  tracks (drum kit + pattern, chords, dB mixer, side-chain, automation, arrangement), `--no-play`
  keeps the transport stopped.
- `~/.livebridge/venv/bin/livebridge-mcp --version` checks the MCP server install.

## Update

```bash
git pull                         # or unzip a new bundle over the folder (see above)
./installers/install.sh          # Windows: installers\install.ps1
```

No flags needed: the token, LAN mode, port, `allow_eval`, beacon and paired host are kept (the
*Settings* step lists them). On Windows quit Claude Desktop first (tray icon → Quit) — a running
MCP server keeps the venv's files locked. Then restart Live (or re-select LiveBridge in the
Control Surface list), start Claude Desktop again and, if you use the skill there, upload the new
`~/.livebridge/livebridge-skill.zip`.

## Uninstall

First **quit Claude Desktop completely** (tray/menu bar icon → Quit — closing the window leaves it
running) and end Claude Code sessions that use LiveBridge: on Windows their running MCP server
locks the venv's files. Then:

```bash
python installers/uninstall.py --dry-run        # show what would be removed
python installers/uninstall.py                  # add --remove-splice / --keep-config / --keep-skill as needed
```

It removes the Remote Script folder, the `livebridge` entries in Claude Desktop and Claude Code,
the venv, the skill (`~/.claude/skills/livebridge`, the Desktop zip) and `~/.livebridge`.
`--remove-splice` removes only Splice entries the installer added — a Splice setup you had before
stays. If something is still in use, the uninstaller finishes the other steps, keeps
`install.json`, exits with 1 and tells you to quit Claude and run it again. Afterwards set the
Control Surface slot in Live to *None*, and remove an uploaded skill in Claude Desktop (Settings →
Capabilities → Skills).

## Manual install (without the installer)

1. Copy `remote_script/LiveBridge` into Live's `User Library/Remote Scripts/` folder (without
   `__pycache__`).
2. Create `Remote Scripts/LiveBridge/config.json` as shown above. Without it LiveBridge runs on
   localhost, port 9880, **with no token — and then `live_eval_python` is refused** (eval is
   always token-protected) and LAN mode is impossible (a network host without a token falls back
   to localhost). Generate a token with `python -c "import secrets; print(secrets.token_hex(16))"`
   and put the same token in both config files.
3. `python -m venv ~/.livebridge/venv && ~/.livebridge/venv/bin/pip install -e mcp_server`
4. Put `{"host": "127.0.0.1", "port": 9880, "token": "<token>"}` in `~/.livebridge/config.json`, or
   pass `--host/--port/--token` / `LIVEBRIDGE_HOST/PORT/TOKEN` to `livebridge-mcp`. The top-level
   token belongs to that file's host:port only; `live_connect(persist=true)` stores further
   endpoints in a `"tokens": {"host:port": token}` map, and a `--host` / `LIVEBRIDGE_HOST`
   override pointing at another machine never receives the file's token (pass `--token` /
   `LIVEBRIDGE_TOKEN` for it).
5. Register the executable with Claude Desktop / Claude Code as shown above and enable the control
   surface in Live.
