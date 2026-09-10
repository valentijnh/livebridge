"""System tools: connection status, connecting/switching, LAN discovery, command catalogue, log.

These are the tools Claude reaches for first (`live_status`) and when something is wrong
(`live_discover`, `live_connect`, `live_commands`).
"""

from __future__ import annotations

import json
import platform
import re
import sys
from typing import Any

from . import bridge_call, drop_none, tool_error
from .. import __version__
from ..client import BridgeClient
from ..config import config_path, endpoint_key, load_config, save_config, stored_token
from ..discovery import BEACON_PORT, discover as udp_discover
from ..errors import BridgeError, error_payload, friendly_error

_COMMAND_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")


def _command_entries(result: Any) -> list[dict[str, Any]] | None:
    """The command list of a `system.commands` answer (dict or older bare list); None on error."""
    if isinstance(result, list):  # older Remote Scripts answered with a bare list
        return [entry for entry in result if isinstance(entry, dict)]
    if isinstance(result, dict) and isinstance(result.get("commands"), list) \
            and "error" not in result:
        return [entry for entry in result["commands"] if isinstance(entry, dict)]
    return None


def _render_default(value: Any) -> str:
    if isinstance(value, str):
        return "'" + value + "'"
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(value)


def _compact_command(entry: dict[str, Any]) -> dict[str, Any]:
    """One catalogue entry with parameters as compact strings: ``"track"`` / ``"slot=null"``."""
    params: list[str] = []
    for param in entry.get("params") or []:
        if isinstance(param, str):
            params.append(param)
            continue
        if not isinstance(param, dict) or not param.get("name"):
            continue
        name = str(param["name"])
        if "default" in param:
            params.append(f"{name}={_render_default(param['default'])}")
        elif param.get("required") is False:
            params.append(f"{name}=?")
        else:
            params.append(name)
    out: dict[str, Any] = {"cmd": entry.get("cmd")}
    if entry.get("doc"):
        out["doc"] = entry["doc"]
    out["params"] = params
    if entry.get("mutating"):
        out["mutating"] = True
    return out


def register(mcp: Any, bridge: BridgeClient) -> None:
    """Register the system tools on the MCP app."""

    @mcp.tool()
    def live_status(refresh: bool = False, include_server: bool = False) -> Any:
        """Check whether Ableton Live is reachable and report what it is. Call this first.

        Combines the connection settings, `system.hello` and `system.ping` in one round trip set,
        so one call tells you everything about the session you are controlling.

        Args:
            refresh: Ask Live again for its `hello` data instead of using the cached copy
                (use after the user restarted Live or loaded another Live version).
            include_server: Also return the Remote Script's own status (`system.status`):
                its config (token redacted), connected clients, beacon and command count —
                useful when debugging LAN mode or a second connected client.

        Returns:
            When connected: ``{"connected": true, "endpoint": "127.0.0.1:9880", "live": {...},
            "protocol": 1, "allow_eval": bool, "song_time": float, "ping_ok": true,
            "client": {...}, "config_file": "<path>", "settings_from": {...}}``.
            When Live is not reachable: ``{"connected": false, "endpoint": ..., "error":
            "<one friendly line>", "type": "connection", "next_steps": [...]}`` — show the line to
            the user, it says exactly what to do.

        Gotchas:
            - `allow_eval: false` means `live_eval_python` is refused: the Remote Script config
              has `"allow_eval": false` or no token (eval is always token-protected).
            - `settings_from` shows where host/port/token/toolsets came from
              (cli/env/file/default).
            - `mcp_host.version` is this MCP server's version and `bridge_version` the Remote
              Script's (two separate numbers). When Claude and Live run on two machines, both
              machines should report the same pair (same LiveBridge checkout/bundle on both).
            - `hidden_tool_modules` appears when the server runs with a reduced toolset
              (`--toolsets` / `LIVEBRIDGE_TOOLSETS`): those modules have no tools in this session,
              but their bridge commands still run through `live_command_call`.
            - `server` (with `include_server=true`): `{config, server: {running, host, port,
              clients, authenticated, pending, max_clients}, bind: {bound, attempts, error,
              retry_in}, beacon: {running, port}, eval, dialog: {open, count, message, buttons},
              commands}`. `server` is null while the port is not bound (`bind` says why and when
              the next retry is); `dialog.open` true means a modal dialog blocks work — see
              `live_dialog_get`.
        """
        info: dict[str, Any] = {
            "endpoint": bridge.endpoint,
            "client": bridge.describe(),
            "config_file": str(config_path()),
            "mcp_host": {
                "version": __version__,
                "python": sys.version.split()[0],
                "platform": platform.system(),
            },
        }
        hidden = getattr(mcp, "hidden_tool_modules", None)
        if hidden:
            info["hidden_tool_modules"] = list(hidden)
        # Ping first: it is the only proof the connection is alive right now (`hello` is cached).
        try:
            pong = bridge.ping(timeout=min(bridge.timeout, 5.0))
        except BridgeError as exc:
            info.update(error_payload(exc, bridge.endpoint))
            info["connected"] = False
            info["ping_ok"] = False
            info["next_steps"] = [
                "Start Ableton Live and enable Preferences -> Link, Tempo & MIDI -> Control "
                "Surface -> LiveBridge (Input/Output: None).",
                "Run live_discover() to find Live instances on the network.",
                "Run live_connect(host, port, token) to point at another machine.",
            ]
            return info

        info["connected"] = True
        info["ping_ok"] = True
        if isinstance(pong, dict) and "t" in pong:
            info["song_time"] = pong["t"]

        try:
            hello = bridge.hello(refresh=refresh)
        except BridgeError as exc:
            info["hello_error"] = friendly_error(exc, bridge.endpoint)
            hello = {}
        info["live"] = hello.get("live")
        info["protocol"] = hello.get("protocol")
        info["allow_eval"] = hello.get("allow_eval")
        for key in ("name", "version", "python", "platform"):
            if key in hello:
                info[f"bridge_{key}" if key in ("name", "version") else key] = hello[key]
        settings = load_config()
        info["settings_from"] = settings.get("source", {})
        if include_server:
            info["server"] = bridge_call(bridge, "system.status")
        return info

    @mcp.tool()
    def live_connect(
        host: str,
        port: int = 9880,
        token: str | None = None,
        persist: bool = False,
    ) -> Any:
        """Point LiveBridge at a (different) Ableton Live instance and verify it answers.

        Use after `live_discover`, or when the user moved to their other machine.

        Args:
            host: IP or hostname where Live runs (`127.0.0.1` for this machine).
            port: TCP port of the LiveBridge Remote Script (default 9880).
            token: Shared token from the Remote Script's `config.json`. Required when Live runs
                in LAN mode (`needs_token: true` in discovery). Omit it to reuse the current token
                when you reconnect to the *same* host:port, or the token saved for exactly this
                host:port in `~/.livebridge/config.json`. Pass `""` to connect without a token.
            persist: Also write host/port/token to `~/.livebridge/config.json` so the next
                Claude session connects here automatically (the token is saved for this
                host:port only; tokens of other endpoints stay stored under their own endpoint).

        Returns:
            ``{"connected": bool, "endpoint": "host:port", "token_used": "given"|"kept"|
            "stored"|"none", "live": {...}, "persisted": bool, "config_file": "<path>"}``, or the
            friendly ``{"error": ..., "type": ...}`` shape when the new endpoint does not answer
            (the client stays pointed at the new endpoint so you can retry after the user starts
            Live). `token_used` says which token is in use (never the token itself): passed now,
            kept (same endpoint), the one stored for this endpoint, or none.

        Gotchas:
            - Tokens are never carried over to another endpoint: switching to a different
              host:port without `token` never sends the previous Live's secret there (that
              would hand it to whatever answers, e.g. a spoofed discovery beacon).
            - Connecting is lazy: this tool immediately calls `system.hello` to prove it works.
            - A wrong or missing token comes back as `type: "auth"`, not as a connection problem.
        """
        if not isinstance(host, str) or not host.strip():
            return tool_error("host must be a non-empty string, e.g. '127.0.0.1'", cmd="live_connect")
        try:
            port = int(port)
        except (TypeError, ValueError):
            return tool_error(f"port must be a number, got {port!r}", cmd="live_connect")
        if not (1 <= port <= 65535):
            return tool_error(f"port {port} is out of range (1-65535)", cmd="live_connect")

        host = host.strip()
        if token is not None:
            use_token, token_source = token, ("given" if token else "none")
        elif endpoint_key(host, port) == endpoint_key(bridge.host, bridge.port):
            use_token, token_source = None, ("kept" if bridge.token else "none")  # same Live
        else:
            # Another Live: only a token saved for exactly this endpoint, never the current one.
            saved = stored_token(host, port)
            use_token, token_source = (saved or ""), ("stored" if saved else "none")
        bridge.switch(host=host, port=port, token=use_token)
        result: dict[str, Any] = {"endpoint": bridge.endpoint, "token_used": token_source,
                                  "config_file": str(config_path())}
        try:
            hello = bridge.hello(refresh=True)
        except BridgeError as exc:
            result.update(error_payload(exc, bridge.endpoint))
            result["connected"] = False
            result["persisted"] = False
            return result

        result["connected"] = True
        result["live"] = hello.get("live")
        result["bridge_name"] = hello.get("name")
        result["allow_eval"] = hello.get("allow_eval")
        result["persisted"] = False
        if persist:
            try:
                path = save_config(host=host, port=port, token=bridge.token or "")
                result["persisted"] = True
                result["config_file"] = str(path)
            except OSError as exc:
                result["persisted"] = False
                result["persist_error"] = f"Could not write {config_path()}: {exc}"
        return result

    @mcp.tool()
    def live_discover(seconds: float = 3.0, port: int = BEACON_PORT) -> Any:
        """Find Ableton Live machines running LiveBridge on the local network (UDP beacons).

        Args:
            seconds: How long to listen. Beacons arrive every 2 s, so 3 s is enough; raise to 6 s
                on a busy Wi-Fi network.
            port: Beacon port to listen on (default 9881; only change it if the Remote Script's
                `beacon_port` was changed).

        Returns:
            ``{"instances": [{"name", "host", "port", "live", "needs_token"}, ...],
            "listened": <s>, "notes": [...], "current": "host:port"}``. Feed a chosen instance into
            `live_connect(host, port, token)`.

        Gotchas:
            - Broadcasts do not cross subnets or VPNs — if the list is empty but Live is running
              elsewhere, ask the user for the IP and use `live_connect` directly.
            - `needs_token: true` means you must supply the token from the Remote Script's
              `config.json` (Live's User Library -> Remote Scripts -> LiveBridge).
            - Never raises: a blocked port shows up in `notes`.
            - Heard nothing: on Windows the firewall of the Claude machine may drop the beacons
              (`notes` has the `New-NetFirewallRule` line to allow UDP 9881); on macOS 15+ the
              app running Claude needs Local Network access (System Settings > Privacy &
              Security > Local Network), and the Live machine must have the beacon on
              (installed with `--network`).
        """
        result = udp_discover(seconds=seconds, port=port)
        result["current"] = bridge.endpoint
        socket_error = any(str(note).startswith(("could not listen", "stopped listening",
                                                 "unexpected discovery error"))
                           for note in result["notes"])
        if not result["instances"] and not socket_error:
            result["notes"].insert(0, 
                "No beacons heard. Live may be on another subnet, the beacon may be disabled in "
                "the Remote Script config, or a firewall blocks UDP — use live_connect(host, port, "
                "token) with the IP instead."
            )
        return result

    @mcp.tool()
    def live_commands(namespace: str | None = None, include_doc: bool = True) -> Any:
        """List the bridge commands this Live installation actually supports (the truth, live).

        Useful when a curated tool is missing or when you suspect an older Remote Script: the
        catalogue is generated from the registry inside Live. Work in two steps — the index
        first, then the one namespace (or command) you need; the full catalogue with every
        parameter is far too large to fetch at once.

        Args:
            namespace: Omit for the compact index ``{namespace: [verbs]}`` (no parameters or
                docs). Pass a namespace such as `"clips"`, `"tracks"`, `"devices"`, `"browser"`,
                `"lom"`, `"system"` for its commands with parameters and docs, or one full command
                such as `"clips.create"` for just that command.
            include_doc: False drops the one-line docs from a namespace listing.

        Returns:
            Without namespace: ``{"count": n, "namespaces": {"clips": ["create", "get", ...],
            ...}, "next": "..."}``. With a namespace/command: ``{"namespace": ..., "count": n,
            "commands": [{"cmd": "clips.create", "doc"?: "...", "params": ["track",
            "slot=null", "unit='beats'"], "mutating"?: true}], "namespaces"?: [...]}``
            (`namespaces` only for a namespace listing). In
            `params` a bare name is required and `name=default` is optional (defaults as JSON,
            strings in single quotes). Or the friendly error shape.

        Gotcha: these are *bridge* commands (`tracks.list`), not MCP tool names (`live_tracks_list`).
        Run one directly with `live_command_call(cmd, args)` only when no curated tool exists.
        """
        if namespace is not None and (not isinstance(namespace, str) or not namespace.strip()):
            return tool_error("namespace must be a non-empty string such as 'clips' or "
                              "'clips.create' — or omit it for the index", cmd="system.commands")
        wanted_cmd: str | None = None
        ns = namespace.strip() if namespace else None
        if ns and "." in ns:
            wanted_cmd, ns = ns, ns.split(".", 1)[0]
        if ns is None:
            result = bridge_call(bridge, "system.commands", {"include_doc": False})
            commands = _command_entries(result)
            if commands is None:
                return result
            index: dict[str, list[str]] = {}
            for entry in commands:
                name = str(entry.get("cmd", ""))
                head, _dot, verb = name.partition(".")
                index.setdefault(head, []).append(verb or head)
            return {"count": len(commands),
                    "namespaces": {key: sorted(verbs) for key, verbs in sorted(index.items())},
                    "next": "live_commands(namespace='<namespace>') for parameters and docs; "
                            "live_command_call(cmd, args) runs a command without a curated tool"}
        result = bridge_call(bridge, "system.commands",
                             drop_none(namespace=ns, include_doc=None if include_doc else False))
        commands = _command_entries(result)
        if commands is None:
            return result
        if wanted_cmd is not None:
            chosen = [entry for entry in commands if entry.get("cmd") == wanted_cmd]
            if not chosen:
                names = ", ".join(str(entry.get("cmd")) for entry in commands)
                return tool_error(f"no command {wanted_cmd!r}; namespace {ns!r} has: {names}",
                                  type="not_found", cmd="system.commands")
            commands = chosen
        out: dict[str, Any] = {"namespace": wanted_cmd or ns, "count": len(commands),
                               "commands": [_compact_command(entry) for entry in commands]}
        if wanted_cmd is None and isinstance(result, dict) and result.get("namespaces"):
            out["namespaces"] = result["namespaces"]
        return out

    @mcp.tool()
    def live_command_call(cmd: str, args: dict[str, Any] | None = None,
                          timeout: float | None = None) -> Any:
        """Run any LiveBridge bridge command by name — the escape hatch for the few commands
        that have no curated tool (e.g. `notes.theory`, `transport.continue`, `browser.cache`).

        Prefer the curated `live_*` tools: they validate arguments and document results. Use
        `live_commands(namespace=...)` first to see the exact command names and parameters.

        Args:
            cmd: Bridge command, `namespace.verb`, e.g. `"notes.theory"`, `"scenes.select"`.
            args: Keyword arguments for the command as an object, e.g.
                `{"chords": ["Cm7", "F7"]}`. Unknown or missing arguments come back as
                `type: "bad_args"` listing what the command accepts.
            timeout: Seconds to wait (default: the client timeout, max 120).

        Returns:
            The command's result, or the friendly `{"error", "type", "cmd"}` shape.

        Gotchas:
            - Mutating commands are one undo step each, exactly like the curated tools.
            - `eval.python` stays gated by `allow_eval` — this does not bypass it.
        """
        if not isinstance(cmd, str) or not _COMMAND_RE.match(cmd.strip()):
            return tool_error("cmd must look like 'namespace.verb', e.g. 'notes.theory' — "
                              "see live_commands()", cmd="live_command_call")
        if timeout is not None and not 0 < timeout <= 120:
            return tool_error("timeout must be within (0, 120] seconds", cmd=cmd.strip())
        return bridge_call(bridge, cmd.strip(), args or {}, timeout=timeout)

    @mcp.tool()
    def live_log(message: str | None = None, level: str = "info", tail: int = 0) -> Any:
        """Write a line into Ableton Live's Log.txt (through the Remote Script), and/or read back
        what LiveBridge logged this session.

        Handy to mark what you were doing when the user has to send a log, to leave a
        breadcrumb before a risky sequence, or (with `tail`) to see the bridge's recent
        warnings/errors without asking the user for Log.txt.

        Args:
            message: The text to log. Keep it short; it is prefixed with `LiveBridge`. Optional
                when you only want `tail`.
            level: "debug", "info" (default), "warn" or "error".
            tail: Also return the last N lines LiveBridge logged this session (0 = none,
                max 400).

        Returns:
            ``{"ok": true, "lines"?: [...]}`` or the friendly error shape.

        Gotcha: Log.txt lives in `~/Library/Preferences/Ableton/Live <version>/Log.txt` (macOS) or
        `%APPDATA%\\Ableton\\Live <version>\\Preferences\\Log.txt` (Windows).
        """
        if message is None and not tail:
            return tool_error("pass message (text to log) and/or tail (lines to read back)",
                              cmd="system.log")
        if message is not None and (not isinstance(message, str) or not message.strip()):
            return tool_error("message must be a non-empty string", cmd="system.log")
        if level not in ("debug", "info", "warn", "error"):
            return tool_error("level must be 'debug', 'info', 'warn' or 'error'", cmd="system.log")
        if not 0 <= tail <= 400:
            return tool_error("tail must be within 0..400", cmd="system.log")
        return bridge_call(bridge, "system.log",
                           drop_none(message=message, level=None if level == "info" else level,
                                     tail=tail or None))

    @mcp.tool()
    def live_command_batch(commands: list[dict[str, Any]], stop_on_error: bool = True,
                           timeout: float | None = None) -> Any:
        """Run several bridge commands in one round trip, in one main-thread pass, as ONE undo
        step (`system.batch`). The fast way to do multi-step builds such as an 8-bar beat
        (create tracks, load devices, write clips, set the mix) without one tool call per step.

        Args:
            commands: The steps, in order, at most 100: each ``{"cmd": "tracks.create",
                "args": {...}}`` with bridge command names (see `live_commands`), not tool names
                (`args` optional; a bare ``"cmd"`` string is a step without args).
                References to earlier results: a string argument that is exactly ``"$<n>"`` or
                ``"$<n>.<key>.<key>..."`` is replaced by (part of) step n's result and keeps its
                type — n is 0-based, ``-1`` is the previous step, keys walk dicts and list
                indices: ``{"track": "$0.path"}``, ``{"index": "$-1.index"}``, ``"$1.notes.0"``.
                Inside a longer string use ``"${<n>.<key>}"``:
                ``{"path": "${0.path}.devices[0]"}``. Start a literal string with ``"$$"`` to
                send it with one ``"$"`` less.
            stop_on_error: Stop at the first failing step (default). False runs every step and
                reports each outcome.
            timeout: Seconds for the WHOLE batch (default: the client timeout, at least 30 s;
                max 120). Raise it for batches that load devices, plug-ins or browser items.

        Returns:
            ``{"count": <steps>, "ran": <steps executed>, "ok": <succeeded>, "failed": <failed>,
            "results": [{"index", "cmd", "ok", "result" | "error": {type, message}}],
            "stopped_at"?: <index of the failing step>}``, or the friendly error shape.

        Gotchas:
            - One undo step and NO rollback: steps before a failure stay applied (one
              `live_transport_undo` reverts the whole batch).
            - Every step runs in the same main-thread tick, so next-tick state (the playhead
              after `transport.play`, `is_playing`, loop/punch, clip recording) reads stale
              inside the batch — check it with a separate call afterwards.
            - Live's UI is busy while the batch runs; keep batches focused.
            - Each step is validated like its own request (unknown arguments -> `bad_args` for
              that step); batches cannot be nested.
        """
        if not isinstance(commands, list) or not commands:
            return tool_error("commands must be a non-empty list of {\"cmd\": ..., "
                              "\"args\": {...}} steps", cmd="system.batch")
        if len(commands) > 100:
            return tool_error(f"at most 100 steps per batch, got {len(commands)} — split it",
                              cmd="system.batch")
        if timeout is not None and not 0 < timeout <= 120:
            return tool_error("timeout must be within (0, 120] seconds", cmd="system.batch")
        wait = timeout or min(max(bridge.timeout, 30.0), 120.0)
        return bridge_call(bridge, "system.batch",
                           {"commands": commands, "stop_on_error": stop_on_error},
                           timeout=wait)

    @mcp.tool()
    def live_dialog_get() -> Any:
        """Is a modal dialog (message box) open in Live, and what does it say?
        (`system.dialog`). Check it when commands suddenly fail or time out.

        Returns:
            ``{"open": bool, "count": <open dialogs>, "message": "<text of the current
            dialog>", "buttons": <number of buttons, 0 when none is open>}``, or the friendly
            error shape.

        Gotchas:
            - Button labels are not exposed by Live — only the message and the button count;
              index 0 is the first (leftmost/default) button.
            - A dialog that blocks Live's main thread also blocks this command: it then times
              out instead of answering. When commands time out, ask the user to look at Live.
            - `message` may still hold the last dialog's text right after it closed; trust
              `open`.
        """
        return bridge_call(bridge, "system.dialog")

    @mcp.tool()
    def live_dialog_press(button: int, expect: str | None = None) -> Any:
        """Answer Live's current modal dialog by pressing one of its buttons
        (`system.dialog_press`). ALWAYS confirm with the user which button to press first.

        Args:
            button: Button index, 0-based (0 = first button), below the `buttons` count that
                `live_dialog_get` reports.
            expect: Text that must appear in the dialog's message (case-insensitive); when it
                does not, nothing is pressed. Always pass it (a word from the message you showed
                the user) so a different dialog that popped up meanwhile is never answered.

        Returns:
            ``{"pressed": <index>, "message": "<text of the dialog that was answered>",
            "open_after": <dialogs still open>}``, or the friendly error shape.

        Gotchas:
            - Dialogs ask destructive questions ("Save changes?", "Delete?", "Replace?") and
              the labels are not available: show the user the message and ask which button
              (by position) to press.
            - `invalid_state` when no dialog is open or `expect` does not match; `bad_args` for
              an index outside 0..buttons-1.
            - A dialog that blocks Live's main thread blocks this command too (it times out);
              the user must answer that one in Live.
        """
        if isinstance(button, bool) or not isinstance(button, int) or button < 0:
            return tool_error("button must be a whole button index, 0 = first",
                              cmd="system.dialog_press")
        return bridge_call(bridge, "system.dialog_press", drop_none(button=button, expect=expect))
