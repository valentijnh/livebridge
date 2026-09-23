# 7 · Live on one computer, Claude on another

> **You:** Live is running on my Windows PC in the studio. Connect to it, and put the kick I
> just downloaded on this Mac onto the first pad of the drum rack there.

|  |  |
|---|---|
| **Shows** | LAN mode with a token, finding Live on the network, switching machines, sending a file from Claude's computer to Live's |
| **Needs** | two computers on the same (home/studio) network, LiveBridge installed on both |
| **Tool calls** | 3 |

## One-time setup

```bash
# on the computer that runs Live: listen on the network, print the token and this machine's IP
python installers/install.py --network

# on the computer that runs Claude
python installers/install.py --pair 192.168.1.20 --token <token printed above>
```

Firewall: the **Live** computer needs incoming TCP 9880; the **Claude** computer needs incoming
UDP 9881 only for `live_discover`. On macOS 15 and later also allow Claude (or your terminal)
and Live under *Privacy & Security → Local Network*. Details in
[docs/NETWORK.md](../docs/NETWORK.md).

## What Claude does

**1. Find Live on the network** (the Remote Script sends a small UDP beacon every 2 seconds in
LAN mode).

```python
live_discover(seconds=3)
```

**2. Connect, and remember it for next time.**

```python
live_connect(host="192.168.1.20", port=9880, token="<the token>", persist=true)
```

After this, every tool talks to that Live. `live_status` shows which machine you're
connected to.

**3. Send the file along and put it on the pad.** A path that only exists on Claude's
computer is uploaded to the Live computer first (into `<User Library>/Samples/LiveBridge`).

```python
live_sample_import(file_path="~/Downloads/Kick Punchy.wav", track="Drums", target="drum_pad",
                   note="C1")
```

Splice downloads travel the same way (`live_splice_import_downloaded`).

## Good to know

- The token is always required in LAN mode, and the connection is not encrypted: use it on your
  own network, not on public Wi-Fi. For anything else, tunnel over SSH or a VPN
  ([docs/NETWORK.md](../docs/NETWORK.md)).
- Switching back to the Live on this computer: `live_connect` with `127.0.0.1` and that Live's
  token, or run `install.py --local` to make a machine standalone again.
- No git on the second computer? `python installers/bundle.py` packs a zip to copy over
  ([docs/INSTALL.md](../docs/INSTALL.md)).

*This setup is covered by the unit tests and the network documentation; it was not re-run for
this page because it needs two machines.*
