"""Splice integration: setup help for the official Splice MCP server, and importing the samples
it downloads into Live.

LiveBridge does not reimplement Splice. Searching, auditioning and downloading sounds is done
by Splice's own remote MCP server (https://mcp.splice.com/mcp); these tools take over once a
file is on disk: they import the exact files Splice reported (`files=[...]`, sent over to the
Live machine when Live runs on another computer) or find the newest download(s) in the usual
folders of the machine that runs Live (the Splice app's folder, Live's own Splice folder, the
LiveBridge inbox, Downloads, Desktop), and import them with the `samples.import` /
`samples.import_midi` bridge commands (session slot, arrangement, Simpler or Drum Rack pads).
There is no bridge command for Splice itself.
"""

from __future__ import annotations

import asyncio
import os
import time as _time
from typing import Any

from . import bridge_call, drop_none, tool_error
from .samples import MIDI_EXTENSIONS, bridge_is_remote, ensure_on_live_machine, is_midi_path
from ..client import BridgeClient

try:  # the MCP Context (progress notifications); FastMCP was renamed MCPServer in mcp 2.x
    from mcp.server.mcpserver import Context as McpContext
except ImportError:  # pragma: no cover - mcp 1.x
    from mcp.server.fastmcp import Context as McpContext  # type: ignore[no-redef]

SPLICE_MCP_URL = "https://mcp.splice.com/mcp"

_MODES = ("session", "arrangement", "simpler", "drum_rack")
_POLL_SECONDS = 3.0
_STABLE_SECONDS = 0.75
_STABLE_MAX_SECONDS = 15.0
_MAX_WATCH_SECONDS = 600.0
_DEFAULT_WATCH_SECONDS = 45.0
_IMPORT_TIMEOUT = 40.0
_MAX_FILES = 32

#: First pass of "newest N": only files that arrived within this window (cheap — folders
#: whose own time is older are not listed); a full scan follows only when it finds too few.
_RECENT_SECONDS = 86400.0
#: Entry cap for the full scan of a big library (the 5 s time limit still applies).
_FULL_SCAN_ENTRIES = 200000
#: Sub-folder levels searched in Downloads / Desktop / the inbox (the Splice folder: all).
_SHALLOW_DEPTH = 2

#: Audio + MIDI extensions listed by default.
_EXTENSIONS = ("wav,wave,aif,aiff,aifc,mp3,flac,ogg,oga,m4a,mp4,aac,caf,alac," +
               ",".join(e.lstrip(".") for e in MIDI_EXTENSIONS))

SETUP_INFO: dict[str, Any] = {
    "splice_mcp_url": SPLICE_MCP_URL,
    "what": "Splice's official remote MCP server lets Claude search, audition and download "
            "Splice sounds. LiveBridge then imports the downloaded files into Live.",
    "setup": {
        "claude_desktop": [
            "Open Claude Desktop -> Settings -> Connectors.",
            "Click 'Add custom connector', paste https://mcp.splice.com/mcp as the URL and "
            "confirm.",
            "Sign in to your Splice account when the browser window asks for it.",
        ],
        "claude_code": [
            "claude mcp add --transport http splice https://mcp.splice.com/mcp",
            "Run /mcp inside Claude Code and authenticate the 'splice' server (Splice login).",
        ],
        "installer": "installers/install.py can register it for you (Claude Desktop and "
                     "Claude Code) next to LiveBridge.",
    },
    "splice_tools": {
        "names": ["describe_a_sound", "prompt_to_stack", "create_stack", "share_stack",
                  "download_asset"],
        "note": "Tool names as documented by Splice for its MCP server — check the "
                "connector's own tool list, Splice may rename or add tools. Search/describe "
                "tools find sounds; download_asset saves a file where the user chooses "
                "(the Splice desktop app is not required).",
    },
    "subscription": {
        "search": "Searching and browsing the catalogue is free with a Splice account.",
        "downloads": "Downloading sounds needs a paid plan (Sounds+, Creator or Creator+) and "
                     "uses credits.",
        "limits": "At most 100 downloads per rolling 24 hours through the MCP.",
    },
    "download_folder": {
        "save_to": "When a Splice download asks where to save, pick a folder Live can read: "
                   "on the same computer e.g. ~/Music/Splice, ~/Downloads or the LiveBridge "
                   "inbox (<User Library>/Samples/LiveBridge). With Live on another computer, "
                   "save anywhere on this one and pass the reported path(s) as files=[...] — "
                   "LiveBridge sends them over.",
        "macos": "~/Splice/sounds (or ~/Splice) — the Splice app's download folder",
        "windows": ("%USERPROFILE%\\Splice (older installs: %USERPROFILE%\\Documents\\Splice, "
                    "or %OneDrive%\\Documents\\Splice when OneDrive backs up Documents)"),
        "searched_by_default": "Without folder/files LiveBridge searches the Splice app's "
                               "folder, Live's own Splice download folder (Library.cfg), the "
                               "LiveBridge inbox, Downloads and Desktop on the Live machine.",
        "override": "Change it in the Splice desktop app (Preferences -> Splice folder), then "
                    "pass folder=... or set LIVEBRIDGE_SPLICE_DIR in the LiveBridge MCP "
                    "server's environment.",
        "presets": "Splice preset downloads (Serum / Serum 2 presets ...) land in "
                   "<Splice folder>/presets (~/Splice/presets on macOS). They are not audio: "
                   "load them in the plug-in's own preset browser (live_plugin_set "
                   "editor_open=true) — Live lists no programs for Serum 2.",
    },
    "workflow": [
        "1. Search Splice with the Splice MCP (key, BPM, genre, instrument, one-shot vs loop).",
        "2. Download the chosen sound(s) with the Splice MCP (download_asset) into a folder "
        "Live can read (see download_folder.save_to).",
        "3. live_splice_import_downloaded(files=[<the paths it reported>], mode=...) imports "
        "exactly those files (sent to the Live machine first in LAN mode); without files it "
        "imports the newest download(s): newest=N, max_age_minutes=10.",
        "4. mode='drum_rack' lays one-shots onto Drum Rack pads from C1 up (a kit in one "
        "call); mode='simpler' plays one file from a Simpler; 'session'/'arrangement' make "
        "clips. MIDI downloads (.mid) become MIDI clips.",
        "5. live_splice_watch_folder is for downloads made by hand in the Splice app or a web "
        "browser while it waits — a Splice MCP download cannot run while the watch blocks.",
    ],
    "live_browser": "Live 12.3+ also shows Splice in its own browser sidebar, but that "
                    "section is not exposed to control-surface scripts (Live 12.4's browser "
                    "API has no Splice root). If you add your Splice folder to Places in "
                    "Live's browser it becomes searchable with live_browser_search(root="
                    "'user_folders') and live_browser_browse(path='splice').",
}


def _is_error(value: Any) -> bool:
    return isinstance(value, dict) and "error" in value and "type" in value


def _search_folders(bridge: BridgeClient, folder: str | None
                    ) -> tuple[list[dict[str, Any]], str, Any]:
    """``(folders [{path, depth, explicit}], source, error)``: an explicit folder >
    LIVEBRIDGE_SPLICE_DIR > the folders found on the Live machine (Splice folder, inbox,
    Downloads, Desktop)."""
    if folder is not None:
        if not folder.strip():
            return [], "argument", tool_error("folder must not be empty", cmd="samples.list")
        return [{"path": folder, "depth": None, "explicit": True}], "argument", None
    env = os.environ.get("LIVEBRIDGE_SPLICE_DIR", "").strip()
    if env:
        return [{"path": env, "depth": None, "explicit": True}], \
            "env LIVEBRIDGE_SPLICE_DIR", None
    locations = bridge_call(bridge, "samples.locations")
    if _is_error(locations):
        return [], "live", locations
    folders: list[dict[str, Any]] = []
    splice = locations.get("splice_folder")
    if splice:
        folders.append({"path": splice, "depth": None, "explicit": False})
    extra = [locations.get("inbox") or {}] + list(locations.get("download_folders") or [])
    for entry in extra:
        if isinstance(entry, dict) and entry.get("exists") and entry.get("path") and \
                all(f["path"] != entry["path"] for f in folders):
            folders.append({"path": entry["path"], "depth": _SHALLOW_DEPTH, "explicit": False})
    if not folders:
        looked = [entry.get("path") for entry in locations.get("splice", [])
                  if isinstance(entry, dict)]
        return [], "live", tool_error(
            "No Splice or download folder found on the machine running Live (looked in: "
            f"{', '.join(p for p in looked if p) or 'the default places'}, Downloads, "
            "Desktop). Pass folder=... or files=[...], or set LIVEBRIDGE_SPLICE_DIR.",
            type="not_found", cmd="samples.locations")
    source = "detected on the Live machine"
    return folders, source, None


def _list(bridge: BridgeClient, entry: dict[str, Any], pattern: str | None, limit: int,
          **extra: Any) -> Any:
    args: dict[str, Any] = {"folder": entry["path"], "sort": "added", "limit": limit,
                            "extensions": _EXTENSIONS, **extra}
    if pattern:
        args["pattern"] = pattern
    if entry.get("depth") is not None:
        args["max_depth"] = entry["depth"]
    return bridge_call(bridge, "samples.list", args, timeout=_IMPORT_TIMEOUT)


def _newest_files(bridge: BridgeClient, folders: list[dict[str, Any]], pattern: str | None,
                  newest: int, max_age_minutes: float | None
                  ) -> tuple[list[dict[str, Any]], list[str], Any]:
    """The ``newest`` files that arrived last across ``folders``:
    ``(files, searched folder paths, error)``.

    A recent-files pass (only folders changed within the window are listed) runs first; a
    full scan follows only when it finds fewer than ``newest``. A scan that stopped before
    reaching every sub-folder (``truncated``) is an error — its "newest" could be wrong."""
    windows: list[float | None] = [max_age_minutes * 60.0] if max_age_minutes else \
        [_RECENT_SECONDS, None]
    searched: list[str] = []
    for window in windows:
        found: list[dict[str, Any]] = []
        truncated: list[str] = []
        searched = []
        for entry in folders:
            extra: dict[str, Any] = {"max_age_s": window} if window else \
                {"max_scan": _FULL_SCAN_ENTRIES}
            listing = _list(bridge, entry, pattern, newest, **extra)
            if _is_error(listing):
                if entry["explicit"]:
                    return [], searched, listing
                continue
            searched.append(listing.get("folder", entry["path"]))
            if listing.get("truncated"):
                truncated.append(listing.get("folder", entry["path"]))
            found.extend(listing.get("files") or [])
        if truncated:
            return [], searched, tool_error(
                "the scan of " + ", ".join(truncated) + " stopped before it reached every "
                "sub-folder (20000+ entries or 5 s), so the newest download may be missing — "
                "pass files=[...] with the exact paths, a narrower folder=... (e.g. the pack "
                "folder) or max_age_minutes=...", type="invalid_state", cmd="samples.list")
        found.sort(key=lambda f: f.get("age_s", 0))
        if window is None or len(found) >= newest or max_age_minutes:
            return found[:newest], searched, None
    return [], searched, None


def _import_files(bridge: BridgeClient, files: list[dict[str, Any]], track: Any, mode: str,
                  slot: Any, time_value: Any, warp: bool | None, track_name: str | None,
                  note: Any = None) -> tuple[list[Any], list[Any]]:
    """Import ``files`` in order; the first import may create the track, the rest follow it.
    Arrangement imports are laid end to end from ``time_value``; drum_rack imports fill pads
    from ``note`` (default: the next empty pad from C1). MIDI files become MIDI clips."""
    imported: list[Any] = []
    errors: list[Any] = []
    target_track = track
    next_time = time_value
    next_slot = slot
    next_note = note
    for entry in files:
        path = entry["path"]
        midi = is_midi_path(path)
        if midi and mode in ("simpler", "drum_rack"):
            errors.append({"file": path, "error": f"MIDI files cannot go into mode='{mode}' — "
                           "use mode='session' or 'arrangement'", "type": "bad_args"})
            continue
        cmd = "samples.import_midi" if midi else "samples.import"
        args = drop_none(file_path=path, track=target_track)
        if warp is not None and not midi:
            args["warp"] = warp
        if mode == "arrangement":
            args["mode"] = "arrangement"
            if next_time is not None:
                args["time"] = next_time
        elif mode == "simpler":
            args["mode"] = "simpler"
        elif mode == "drum_rack":
            args["target"] = "drum_pad"
            if next_note is not None:
                args["note"] = next_note
        elif next_slot is not None:
            args["slot"] = next_slot
        if target_track is None and track_name:
            args["track_name"] = track_name
        result = bridge_call(bridge, cmd, args, timeout=_IMPORT_TIMEOUT)
        if _is_error(result):
            errors.append({"file": path, **result})
            continue
        if entry.get("uploaded"):
            result["uploaded"] = entry["uploaded"]
        imported.append(result)
        if target_track is None and mode != "simpler":
            # later files follow the track the first import created
            target_track = (result.get("track") or {}).get("path")
        if mode == "arrangement":
            clip = result.get("clip") or {}
            end = clip.get("end_time")
            if end is None and clip.get("length") is not None:
                end = float(result.get("time") or 0.0) + float(clip["length"])
            next_time = end if end is not None else next_time
        elif mode == "drum_rack" and next_note is not None:
            # the answer holds the real note, also for "E1" / pad names
            landed = (result.get("pad") or {}).get("note")
            next_note = landed + 1 if isinstance(landed, int) and landed < 127 else None
        elif mode == "session" and next_slot is not None:
            # the answer holds the real slot index, also for scene names
            landed = result.get("slot")
            next_slot = landed + 1 if isinstance(landed, int) else None
    return imported, errors


def _files_argument(bridge: BridgeClient, files: list[str], upload: bool | None
                    ) -> tuple[list[dict[str, Any]], list[Any]]:
    """Exact paths -> ``[{path (on the Live machine), name, uploaded?}]`` + errors (a path
    that only exists on this machine is sent to the Live machine in LAN mode)."""
    ready: list[dict[str, Any]] = []
    errors: list[Any] = []
    for raw in files:
        if not isinstance(raw, str) or not raw.strip():
            errors.append({"file": raw, "error": "empty path", "type": "bad_args"})
            continue
        path, sent, error = ensure_on_live_machine(bridge, raw, upload, subfolder="Splice")
        if error is not None:
            errors.append({"file": raw, **error})
            continue
        entry: dict[str, Any] = {"path": path, "name": os.path.basename(path.replace("\\", "/"))}
        if sent is not None:
            entry["uploaded"] = {"from": raw, "to": sent.get("path"), "size": sent.get("size")}
        ready.append(entry)
    return ready, errors


async def _progress(ctx: McpContext | None, done: float, total: float, message: str) -> None:
    """Best-effort MCP progress notification (keeps clients that reset their timeout on
    progress waiting); never raises."""
    if ctx is None:
        return
    try:
        await ctx.report_progress(done, total, message)
    except Exception:  # no request context / client without a progress token
        return


def register(mcp: Any, bridge: BridgeClient) -> None:
    """Register the Splice tools on the MCP app."""

    @mcp.tool()
    def live_splice_setup_info(check_live: bool = True) -> Any:
        """How to connect Splice to Claude and get Splice sounds into Live: the official Splice
        MCP URL, exact setup steps for Claude Desktop and Claude Code, Splice's MCP tool names,
        subscription limits, where to save downloads, and the recommended workflow.

        Args:
            check_live: also ask Live where Splice content is visible (Splice folder on disk,
                download folders, the LiveBridge inbox, a Splice folder in the browser's
                Places). Harmless when Live is not running.

        Returns:
            `{splice_mcp_url, setup:{claude_desktop:[...], claude_code:[...]}, splice_tools,
            subscription, download_folder, workflow:[...], live_browser, live?:{splice_folder,
            download_folders, inbox, platform, browser_splice, lan_mode}}`

        Gotchas: searching Splice is free; downloads need Sounds+/Creator/Creator+ and are
        capped at 100 per rolling 24 h. Splice's MCP saves files where the user chooses —
        pass the reported paths to live_splice_import_downloaded(files=[...]).
        """
        info = dict(SETUP_INFO)
        if check_live:
            live: dict[str, Any] = {}
            locations = bridge_call(bridge, "samples.locations")
            if _is_error(locations):
                live["error"] = locations["error"]
            else:
                live["splice_folder"] = locations.get("splice_folder")
                live["platform"] = locations.get("platform")
                if locations.get("download_folders") is not None:
                    live["download_folders"] = [
                        entry.get("path") for entry in locations.get("download_folders") or []
                        if isinstance(entry, dict) and entry.get("exists")]
                if locations.get("inbox") is not None:
                    live["inbox"] = (locations.get("inbox") or {}).get("path")
                roots = bridge_call(bridge, "browser.roots", {"counts": False})
                if not _is_error(roots):
                    live["browser_splice"] = roots.get("splice")
            live["lan_mode"] = bridge_is_remote(bridge)
            env = os.environ.get("LIVEBRIDGE_SPLICE_DIR")
            if env:
                live["LIVEBRIDGE_SPLICE_DIR"] = env
            info["live"] = live
        return info

    @mcp.tool()
    def live_splice_import_downloaded(
        files: list[str] | None = None,
        folder: str | None = None,
        track: int | str | None = None,
        mode: str = "session",
        newest: int = 1,
        pattern: str | None = None,
        slot: int | str | None = None,
        time: float | str | None = None,
        note: int | str | None = None,
        max_age_minutes: float | None = None,
        warp: bool | None = None,
        track_name: str | None = None,
        upload: bool | None = None,
    ) -> Any:
        """Import Splice downloads into Live: the exact files the Splice MCP reported
        (`files`), or the newest download(s) found on the Live machine.

        Args:
            files: exact paths of downloaded files (1-32), e.g. the paths Splice's
                download_asset reported. A path that only exists on this computer is sent
                to the Live machine first (LAN mode, see `upload`). Imported in this order.
            folder: without `files` — where to look (recursive). Default: $LIVEBRIDGE_SPLICE_DIR,
                else the Splice folder, Live's own Splice folder, the LiveBridge inbox,
                Downloads and Desktop of the Live machine.
            track: target track (index, name or path). Omitted: a new track is created for
                the first file and the others follow on that same track ("simpler": one new
                MIDI track + Simpler per file).
            mode: "session" (clip slots, default), "arrangement" (end to end from `time`),
                "simpler" (one-shot into a Simpler on a MIDI track) or "drum_rack" (one pad per
                file — a Drum Rack kit in one call; a new MIDI track + Drum Rack when `track`
                is omitted). MIDI files (.mid) always become MIDI clips (session/arrangement).
            newest: without `files` — how many of the newest files to import (1-32), newest
                first.
            pattern: file-name glob(s), e.g. "*.wav", "*.wav,*.aif", "*kick*". Default: every
                audio and MIDI file.
            slot: first session slot index or scene name (the next files use the following
                slots); default: the first empty slots.
            time: arrangement start in beats or "bars.beats.sixteenths" (default: the playhead).
            note: drum_rack — first pad (36 / "C1"); the next files use the following notes.
                Default: the next empty pads from C1 up.
            max_age_minutes: without `files` — only files that arrived within this many
                minutes (also makes the search fast on big libraries).
            warp: set Warp on/off on the new audio clips (null = Live's default).
            track_name: name of the new track when `track` is omitted.
            upload: `files` only — null = send files to Live automatically in LAN mode,
                true = always send, false = never.

        Returns:
            `{folder?, folder_source?, searched?:[folders], found:[{path,name,age_s?}],
            imported:[samples.import results with route/track/clip/pad], errors?:[...],
            hint?}`

        Gotchas: "newest" is by arrival time on disk (downloads keep working even when the
        file's modified time is old). A scan that stops before reaching every sub-folder of
        a huge library answers an error instead of importing a wrong "newest" file — pass
        `files`, a narrower `folder` or `max_age_minutes`. mode="simpler" with an existing
        track takes one file only (a Simpler holds one sample) — use mode="drum_rack" for
        several one-shots.
        """
        cmd = "samples.import"
        if mode not in _MODES:
            return tool_error(f"mode must be one of {', '.join(_MODES)}", cmd=cmd)
        if not 1 <= newest <= _MAX_FILES:
            return tool_error(f"newest must be between 1 and {_MAX_FILES}", cmd=cmd)
        if mode == "arrangement" and slot is not None:
            return tool_error("slot is for the session; use time for the arrangement", cmd=cmd)
        if mode != "arrangement" and time is not None:
            return tool_error("time is for mode='arrangement'", cmd=cmd)
        if note is not None and mode != "drum_rack":
            return tool_error("note is for mode='drum_rack'", cmd=cmd)
        if slot is not None and mode != "session":
            return tool_error("slot is for mode='session'", cmd=cmd)
        if max_age_minutes is not None and max_age_minutes <= 0:
            return tool_error("max_age_minutes must be > 0", cmd=cmd)
        if track_name is not None and track is not None:
            return tool_error("track_name only applies when track is omitted", cmd=cmd)
        if pattern is not None and not pattern.strip():
            return tool_error("pattern must not be empty (omit it for every audio/MIDI file)",
                              cmd=cmd)
        if files is not None:
            if not files or len(files) > _MAX_FILES:
                return tool_error(f"files must list 1-{_MAX_FILES} paths", cmd=cmd)
            if folder is not None or pattern is not None or max_age_minutes is not None \
                    or newest != 1:
                return tool_error("files are exact paths — folder, pattern, newest and "
                                  "max_age_minutes only apply without files", cmd=cmd)
        elif upload is not None:
            return tool_error("upload only applies to files=[...]", cmd=cmd)
        count = len(files) if files is not None else newest
        if mode == "simpler" and track is not None and count > 1:
            return tool_error("mode='simpler' with a track holds one sample, so every file "
                              "would replace the previous one — pass one file (newest=1), "
                              "omit track (one new MIDI track per file) or use "
                              "mode='drum_rack' (one pad per file)", cmd=cmd)
        if isinstance(note, int) and not isinstance(note, bool) and \
                not 0 <= note <= 128 - count:
            return tool_error(f"note must leave room for {count} pad(s) (0..{128 - count})",
                              cmd=cmd)
        if mode == "drum_rack" and track is None and track_name is None:
            track_name = "Splice Kit"
        result: dict[str, Any] = {}
        pre_errors: list[Any] = []
        if files is not None:
            ready, pre_errors = _files_argument(bridge, files, upload)
            result["found"] = [{"path": f["path"], "name": f["name"]} for f in ready]
        else:
            folders, source, error = _search_folders(bridge, folder)
            if error is not None:
                return error
            ready, searched, error = _newest_files(bridge, folders, pattern, newest,
                                                   max_age_minutes)
            if error is not None:
                return error
            result["folder"] = searched[0] if searched else folders[0]["path"]
            result["folder_source"] = source
            if len(searched) > 1:
                result["searched"] = searched
            result["found"] = [{"path": f["path"], "name": f["name"], "age_s": f.get("age_s")}
                               for f in ready]
            if not ready:
                result["imported"] = []
                result["hint"] = ("no matching audio/MIDI files" +
                                  (f" newer than {max_age_minutes} min" if max_age_minutes
                                   else "") +
                                  f" in {', '.join(searched) or result['folder']}" +
                                  (f" (pattern {pattern!r})" if pattern else "") +
                                  " — download with the Splice MCP first and pass the "
                                  "reported paths as files=[...], or pass folder=...")
                return result
        imported, errors = _import_files(bridge, ready, track, mode, slot, time, warp,
                                         track_name, note)
        result["imported"] = imported
        if pre_errors or errors:
            result["errors"] = pre_errors + errors
        return result

    @mcp.tool()
    async def live_splice_watch_folder(
        seconds: float = _DEFAULT_WATCH_SECONDS,
        folder: str | None = None,
        track: int | str | None = None,
        mode: str = "session",
        pattern: str | None = None,
        import_file: bool = True,
        warp: bool | None = None,
        ctx: McpContext | None = None,
    ) -> Any:
        """Wait for a new sample to land on the Live machine — a download you make BY HAND
        in the Splice desktop app or a web browser while this waits — then import it.

        Args:
            seconds: how long to wait (1-600, default 45). Returns as soon as a file arrives
                and has finished downloading (its size stopped changing).
            folder: folder to watch (recursive); default: the Splice folder, the LiveBridge
                inbox, Downloads and Desktop of the Live machine (like
                `live_splice_import_downloaded`).
            track: target track; omitted = a new track.
            mode: "session", "arrangement", "simpler" or "drum_rack" (see
                `live_splice_import_downloaded`).
            pattern: file-name glob(s); default every audio and MIDI file.
            import_file: false = only report the new file, don't import it.
            warp: set Warp on/off on the new clip.

        Returns:
            `{folder, folder_source, watched?, new_file:{path,name,size}|null,
            imported?:{...samples.import result...}, errors?, waited_s}` — `new_file: null`
            when nothing arrived in time.

        Gotchas: do NOT use it for Splice MCP downloads — Claude cannot run the Splice
        download while this tool is waiting. Download with the Splice MCP first, then call
        live_splice_import_downloaded(files=[...]) (or max_age_minutes=5). Only the first new
        file is imported. Polls every 3 s and sends progress notifications; keep `seconds`
        short — some clients give up on a tool call after 60 s.
        """
        cmd = "samples.list"
        if not 1 <= seconds <= _MAX_WATCH_SECONDS:
            return tool_error(f"seconds must be between 1 and {int(_MAX_WATCH_SECONDS)}",
                              cmd=cmd)
        if mode not in _MODES:
            return tool_error(f"mode must be one of {', '.join(_MODES)}", cmd=cmd)
        if pattern is not None and not pattern.strip():
            return tool_error("pattern must not be empty", cmd=cmd)
        folders, source, error = await asyncio.to_thread(_search_folders, bridge, folder)
        if error is not None:
            return error

        def poll(entry: dict[str, Any], **extra: Any) -> Any:
            return _list(bridge, entry, pattern, 50, **extra)

        known: set[str] = set()
        since: float | None = None
        watched: list[dict[str, Any]] = []
        for entry in folders:
            # baseline: cheap (only recently changed folders are listed) and warms the
            # Live-side folder cache so the polls below only list folders that change
            baseline = await asyncio.to_thread(poll, entry, max_age_s=10.0)
            if _is_error(baseline):
                if entry["explicit"]:
                    return baseline
                continue
            watched.append(entry)
            known |= {f["path"] for f in baseline.get("files") or []}
            now = float(baseline.get("now") or 0.0)
            since = now - 1.0 if since is None else min(since, now - 1.0)
        if not watched:
            return tool_error("none of the folders could be read on the Live machine",
                              type="not_found", cmd=cmd)
        started = _time.monotonic()
        deadline = started + seconds
        new_file = None
        warnings: list[str] = []
        while _time.monotonic() < deadline and new_file is None:
            await asyncio.sleep(min(_POLL_SECONDS, max(0.05, deadline - _time.monotonic())))
            elapsed = _time.monotonic() - started
            await _progress(ctx, round(elapsed, 1), seconds, "waiting for a new download")
            for entry in watched:
                current = await asyncio.to_thread(poll, entry, newer_than=since)
                if _is_error(current):
                    return current
                if current.get("truncated") and entry["path"] not in warnings:
                    warnings.append(entry["path"])
                fresh = [f for f in current.get("files") or [] if f["path"] not in known]
                if fresh:
                    new_file = fresh[0]
                    break
        result: dict[str, Any] = {"folder": watched[0]["path"], "folder_source": source}
        if len(watched) > 1:
            result["watched"] = [entry["path"] for entry in watched]
        if warnings:
            result["warnings"] = ["the scan of %s stopped early (huge folder) — a download "
                                  "there may be missed; pass folder=<the pack folder>" % w
                                  for w in warnings]
        if new_file is None:
            result.update(new_file=None, waited_s=round(_time.monotonic() - started, 1),
                          hint="nothing new arrived — is the download saved to one of these "
                               "folders? Pass folder=... or raise seconds. For Splice MCP "
                               "downloads use live_splice_import_downloaded(files=[...]).")
            return result
        # wait until the download is complete (size stable)
        size = new_file.get("size")
        stable_deadline = _time.monotonic() + _STABLE_MAX_SECONDS
        while _time.monotonic() < stable_deadline:
            await asyncio.sleep(_STABLE_SECONDS)
            info = await asyncio.to_thread(bridge_call, bridge, "samples.inspect",
                                           {"file_path": new_file["path"]})
            if _is_error(info) or not isinstance(info, dict) or not info.get("exists"):
                break
            if info.get("size") == size and size:
                break
            size = info.get("size")
        result["new_file"] = {"path": new_file["path"], "name": new_file["name"], "size": size}
        result["waited_s"] = round(_time.monotonic() - started, 1)
        if import_file:
            await _progress(ctx, seconds, seconds, "importing " + new_file["name"])
            imported, errors = await asyncio.to_thread(
                _import_files, bridge, [new_file], track, mode, None, None, warp, None)
            if imported:
                result["imported"] = imported[0]
            if errors:
                result["errors"] = errors
        return result
