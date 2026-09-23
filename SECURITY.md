# Security policy

## Reporting a vulnerability

Please report security problems **privately** through GitHub:
[Security → Report a vulnerability](https://github.com/valentijnh/livebridge-ableton-mcp/security/advisories/new).
Don't open a public issue. You'll get an answer as soon as possible, and credit in the fix's
release notes if you want it.

Supported: the latest release on `main`.

## How LiveBridge protects your computer

LiveBridge lets a program control Ableton Live, and optionally run Python inside it, so the
connection is what needs protecting:

- **Local by default.** The Remote Script listens on `127.0.0.1` only. Nothing is reachable from
  the network, and no firewall prompt appears.
- **A token on every request.** The installer generates a random 128-bit token and writes it to
  both config files. Wrong or missing tokens are refused before anything runs.
- **LAN mode is opt-in** (`install.py --network`) and always requires the token. A LAN config
  without a token falls back to `127.0.0.1`. The connection is **not encrypted**: use LAN mode on
  your own network only, and tunnel over SSH or a VPN anywhere else
  ([docs/NETWORK.md](docs/NETWORK.md)).
- **Python inside Live** (`live_eval_python`) needs the token as well and can be switched off
  with `install.py --no-allow-eval`. Anyone who has the token can run code with Live's
  permissions, so keep the token private and rotate it with `--new-token` if it leaks.
- **Files:** sample import and upload only read the files you name; uploads go into
  `<User Library>/Samples/LiveBridge` on the Live machine.

The token lives in `~/.livebridge/config.json` (Windows: `%USERPROFILE%\.livebridge\config.json`)
and in the Remote Script's `config.json`. Neither file belongs in a repository or a bug report;
`live_status` redacts it.
