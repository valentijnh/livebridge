"""Sample tools: import audio and MIDI files from disk into Live (session slot, arrangement,
Simpler or a Drum Rack pad), send files to the Live machine, list audio files in a folder (or
the usual sample folders) and inspect/validate a file. (An audio clip already in the set
becomes a Simpler / Drum Rack / MIDI with live_clip_convert.)

All paths are paths on the computer that runs Ableton Live — the Remote Script reads the disk
there. When Live runs on another machine (LAN mode), a file that only exists on this machine
is sent over first (`live_sample_upload`; `live_sample_import` does it automatically). `~`,
`$VAR`, `%VAR%` and `file://` URLs are accepted; Windows paths only work when Live runs on
Windows and vice versa.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import os
import re
import shutil
import tempfile
import urllib.parse
import urllib.request
import uuid
from typing import Any

from . import bridge_call, drop_none, tool_error
from ..client import BridgeClient

_MODES = ("session", "arrangement", "simpler")
_TARGETS = ("auto", "clip", "simpler", "browser", "drum_pad")
_SORTS = ("newest", "added", "oldest", "name", "size")
MIDI_EXTENSIONS = (".mid", ".midi", ".smf", ".kar")

#: File scans and imports can touch big folders: give them more time than the default.
_FILE_TIMEOUT = 40.0

#: Upload chunk size (raw bytes; ~5.4 MB of base64 per request line, the protocol allows 16 MiB)
#: and the largest file sent / downloaded.
UPLOAD_CHUNK = 4 * 1024 * 1024
MAX_UPLOAD_BYTES = 1024 * 1024 * 1024
_UPLOAD_TIMEOUT = 60.0
_URL_TIMEOUT = 60.0


def is_midi_path(path: str) -> bool:
    """True for a Standard MIDI File name (.mid/.midi/.smf/.kar)."""
    return path.strip().strip("\"'").lower().endswith(MIDI_EXTENSIONS)


def bridge_is_remote(bridge: BridgeClient) -> bool:
    """True when the bridge endpoint is not this machine's loopback address."""
    host = (getattr(bridge, "host", "") or "").strip().lower()
    if host in ("", "localhost", "localhost.localdomain"):
        return False
    try:
        return not ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return True


def _local_file(path: str) -> str | None:
    """``path`` as an existing file on the MCP server's machine, or None."""
    text = path.strip().strip("\"'")
    if text.lower().startswith("file://"):
        text = urllib.parse.unquote(urllib.parse.urlparse(text).path)
        if re.match(r"^/[A-Za-z]:[\\/]", text):
            text = text[1:]
    text = os.path.expandvars(os.path.expanduser(text))
    return text if os.path.isfile(text) else None


def upload_local_file(bridge: BridgeClient, local_path: str, name: str | None = None,
                      subfolder: str | None = None, overwrite: bool = False) -> Any:
    """Send a file of the MCP server's machine to the Live machine's inbox with
    ``samples.receive`` (chunks of 4 MiB, sha256 checked). Returns the last answer
    (``{done, path, name, size, folder, sha256, renamed?, audio?}``) or an error dict."""
    cmd = "samples.receive"
    try:
        size = os.path.getsize(local_path)
    except OSError as exc:
        return tool_error(f"cannot read {local_path}: {exc}", type="not_found", cmd=cmd)
    if size > MAX_UPLOAD_BYTES:
        return tool_error(f"{local_path} is {size} bytes — at most {MAX_UPLOAD_BYTES} can be "
                          "sent", cmd=cmd)
    digest = hashlib.sha256()
    with open(local_path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    upload_id = uuid.uuid4().hex[:16]
    file_name = name or os.path.basename(local_path)
    offset = 0
    answer: Any = None
    with open(local_path, "rb") as handle:
        while True:
            chunk = handle.read(UPLOAD_CHUNK)
            args: dict[str, Any] = {"name": file_name, "upload_id": upload_id,
                                    "total_size": size, "offset": offset,
                                    "data": base64.b64encode(chunk).decode("ascii")}
            if subfolder:
                args["subfolder"] = subfolder
            if overwrite:
                args["overwrite"] = True
            if offset + len(chunk) >= size:
                args["sha256"] = digest.hexdigest()
            answer = bridge_call(bridge, cmd, args, timeout=_UPLOAD_TIMEOUT)
            if isinstance(answer, dict) and "error" in answer:
                return answer
            offset += len(chunk)
            if not chunk or offset >= size:
                break
    return answer


def download_url(url: str) -> tuple[str | None, str | None, Any]:
    """Download ``url`` (http/https) into a temp folder on the MCP server's machine.
    Returns ``(temp_dir, file_path, error)``."""
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        return None, None, tool_error("url must be an http(s) URL", cmd="samples.receive")
    temp_dir = tempfile.mkdtemp(prefix="livebridge-")
    try:
        request = urllib.request.Request(url.strip(), headers={"User-Agent": "LiveBridge"})
        with urllib.request.urlopen(request, timeout=_URL_TIMEOUT) as response:
            disposition = response.headers.get("Content-Disposition") or ""
            match = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)\"?", disposition)
            name = urllib.parse.unquote(match.group(1)) if match else \
                urllib.parse.unquote(os.path.basename(parsed.path)) or "download"
            name = os.path.basename(name.replace("\\", "/")) or "download"
            target = os.path.join(temp_dir, name)
            total = 0
            with open(target, "wb") as handle:
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    total += len(block)
                    if total > MAX_UPLOAD_BYTES:
                        raise ValueError("the download is larger than 1 GiB")
                    handle.write(block)
        return temp_dir, target, None
    except Exception as exc:  # network errors, HTTP errors, size limit
        shutil.rmtree(temp_dir, ignore_errors=True)
        return None, None, tool_error(f"could not download {url}: {exc}", type="not_found",
                                      cmd="samples.receive")


def ensure_on_live_machine(bridge: BridgeClient, file_path: str, upload: bool | None,
                           subfolder: str | None = None) -> tuple[str, Any, Any]:
    """``(path to use on the Live machine, upload answer or None, error or None)``.

    ``upload`` None = automatic: only when the bridge is on another machine, the file exists
    here, and Live cannot see the same path. True = always send a local file."""
    if upload is False:
        return file_path, None, None
    local = _local_file(file_path)
    if upload is None:
        if local is None or not bridge_is_remote(bridge):
            return file_path, None, None
        seen = bridge_call(bridge, "samples.inspect", {"file_path": file_path})
        if isinstance(seen, dict) and seen.get("exists"):
            return file_path, None, None
    elif local is None:
        return file_path, None, tool_error(
            f"{file_path} does not exist on this machine (the one running the LiveBridge MCP "
            "server), so it cannot be uploaded", type="not_found", cmd="samples.receive")
    sent = upload_local_file(bridge, local, subfolder=subfolder)
    if isinstance(sent, dict) and "error" in sent:
        return file_path, None, sent
    return sent.get("path", file_path), sent, None


def register(mcp: Any, bridge: BridgeClient) -> None:
    """Register the sample tools on the MCP app."""

    @mcp.tool()
    def live_sample_import(
        file_path: str,
        track: int | str | None = None,
        mode: str = "session",
        slot: int | str | None = None,
        time: float | str | None = None,
        target: str = "auto",
        track_name: str | None = None,
        clip_name: str | None = None,
        warp: bool | None = None,
        select: bool = False,
        overwrite: bool = False,
        note: int | str | None = None,
        midi_track: int | str | None = None,
        upload: bool | None = None,
    ) -> Any:
        """Import a file from disk into Live — the reliable way to bring in samples (Splice
        downloads, recordings, anything on disk): audio into a clip slot, the arrangement, a
        Simpler or a Drum Rack pad; MIDI files (.mid) as a MIDI clip. One undo step.

        Args:
            file_path: absolute path on the machine running Live ("~/Splice/sounds/.../kick.wav",
                "C:\\Users\\me\\Music\\loop.wav", "~/Downloads/chords.mid"). In LAN mode a file
                that only exists on this machine is sent to Live first (see `upload`).
            track: target track (index, name or path). Omit to create a new track (audio for
                clips, MIDI for a Simpler / Drum Rack / MIDI file) named `track_name` or after
                the file.
            mode: "session" (default; into a clip slot), "arrangement" (on the timeline) or
                "simpler" (session, into a Simpler on a MIDI track).
            slot: session clip slot index or scene name; default: the highlighted slot if it
                is an empty slot of that track, else the first empty slot (a scene is added
                when the track is full).
            time: arrangement position in beats or "bars.beats.sixteenths" ("17.1.1");
                default: the playhead.
            target: session route for audio — "auto" (audio track -> clip, MIDI track with a
                Drum Rack -> its next empty pad, other MIDI track -> Simpler), "clip",
                "simpler", "drum_pad" (a Simpler on a Drum Rack pad; the Drum Rack is inserted
                when the track has no instrument) or "browser" (load the file's browser item;
                only for files inside a folder Live's browser shows, and — for clip slots —
                while Live's Session view is focused).
            track_name: name for a new track (only when `track` is omitted).
            clip_name: rename the new clip (default: the file name) / the pad's chain.
            warp: switch the clip's Warp on/off (null = Live's default for the file).
            select: select the track / highlight the slot afterwards so the user sees it.
            overwrite: replace a clip already in `slot`, or clear a filled pad (drum_pad).
            note: the Drum Rack pad (36, "C1", Live's numbering) — implies target "drum_pad";
                default: the first empty pad from C1 up. A pad with one Simpler gets its
                sample replaced.
            midi_track: MIDI files only — which track of a multi-track .mid (index or name);
                default: all tracks merged into one clip.
            upload: null (default) = send the file to the Live machine automatically when
                LiveBridge talks to Live on another computer and only this computer has the
                file; true = always send it (into the Live machine's LiveBridge inbox); false =
                never.

        Returns:
            `{route, file:{path,name,size}, track:{path,name,type}, created_track?, slot?,
            clip?:{path,name,length,start_time?,end_time?}, device?:{path,name,...}, pad?:
            {note,key,name,chain}, rack?, created_scene?, applied?, notes?, uploaded?}` —
            `route` says how it was done ("clip_slot.create_audio_clip",
            "track.create_audio_clip", "track.insert_device + simpler.replace_sample",
            "rack.insert_chain + ...", "clip_slot.create_clip + add_new_notes" for MIDI ...).
            MIDI files add `midi:{format, tempo, signature, tracks}`, `notes_added`, `length`.

        Gotchas: audio clips need an audio track; a MIDI track that already has another
        instrument is refused for Simpler (use another track or omit `track`). Drum pads need
        Live 12.3+. A file whose header does not match its extension (AIFF data named .wav)
        is refused up front — Live cannot read it. Live references the file in place —
        "Collect All and Save" copies it into the project. MIDI files keep their beat
        positions (the file's tempo is reported, not applied).
        """
        cmd = "samples.import"
        if not file_path.strip():
            return tool_error("file_path must not be empty", cmd=cmd)
        if mode not in _MODES:
            return tool_error(f"mode must be one of {', '.join(_MODES)}", cmd=cmd)
        if target not in _TARGETS:
            return tool_error(f"target must be one of {', '.join(_TARGETS)}", cmd=cmd)
        if mode == "arrangement" and slot is not None:
            return tool_error("slot is for the session; use time for the arrangement", cmd=cmd)
        if mode != "arrangement" and time is not None:
            return tool_error("time is for mode='arrangement'", cmd=cmd)
        if isinstance(time, (int, float)) and time < 0:
            return tool_error("time must be >= 0 beats", cmd=cmd)
        if track_name is not None and track is not None:
            return tool_error("track_name only applies when track is omitted (new track)",
                              cmd=cmd)
        if note is not None and (target not in ("auto", "drum_pad") or mode != "session"):
            return tool_error("note is for Drum Rack pads (target='drum_pad', session mode)",
                              cmd=cmd)
        midi = is_midi_path(file_path)
        if midi and (mode == "simpler" or target != "auto" or note is not None
                     or warp is not None):
            return tool_error("MIDI files become MIDI clips: mode 'session' or 'arrangement' "
                              "only, no target/note/warp", cmd="samples.import_midi")
        if midi_track is not None and not midi:
            return tool_error("midi_track only applies to MIDI files (.mid)", cmd=cmd)
        path, uploaded, error = ensure_on_live_machine(bridge, file_path, upload)
        if error is not None:
            return error
        if midi:
            args = drop_none(file_path=path, track=track, slot=slot, time=time,
                             midi_track=midi_track, track_name=track_name, name=clip_name)
            if mode != "session":
                args["mode"] = mode
            if select:
                args["select"] = True
            if overwrite:
                args["overwrite"] = True
            result = bridge_call(bridge, "samples.import_midi", args, timeout=_FILE_TIMEOUT)
        else:
            args = drop_none(file_path=path, track=track, slot=slot, time=time,
                             track_name=track_name, name=clip_name, warp=warp, note=note)
            if mode != "session":
                args["mode"] = mode
            if target != "auto":
                args["target"] = target
            if select:
                args["select"] = True
            if overwrite:
                args["overwrite"] = True
            result = bridge_call(bridge, cmd, args, timeout=_FILE_TIMEOUT)
        if uploaded is not None and isinstance(result, dict) and "error" not in result:
            result["uploaded"] = {"from": file_path, "to": uploaded.get("path"),
                                  "size": uploaded.get("size")}
        return result

    @mcp.tool()
    def live_sample_upload(
        local_path: str | None = None,
        url: str | None = None,
        name: str | None = None,
        subfolder: str | None = None,
        overwrite: bool = False,
    ) -> Any:
        """Send a file to the computer that runs Live (into its LiveBridge inbox,
        `<User Library>/Samples/LiveBridge`) and return the path Live can use — for LAN
        mode (Claude on one computer, Live on another) and for files that only exist as a
        URL (e.g. a download link a Splice tool returned).

        Args:
            local_path: a file on THIS computer (the one running the LiveBridge MCP server),
                e.g. what Splice's download tool saved. Audio, MIDI and preset files only.
            url: or an http(s) URL — this computer downloads it (max 1 GiB) and sends it.
            name: file name on the Live machine (default: the original name).
            subfolder: relative folder inside the inbox, e.g. "Splice/Pack A".
            overwrite: replace a file of the same name (default: " (2)" is appended).

        Returns:
            `{done, path (on the Live machine — pass it to live_sample_import), name, size,
            folder, sha256, renamed?, audio?}`

        Gotchas: live_sample_import already sends local files automatically in LAN mode —
        call this for batches, URLs, or to keep a copy next to Live's User Library (Live's
        browser shows the inbox). Files are checked with sha256.
        """
        cmd = "samples.receive"
        if (local_path is None) == (url is None):
            return tool_error("pass exactly one of local_path or url", cmd=cmd)
        if name is not None and not name.strip():
            return tool_error("name must not be empty", cmd=cmd)
        if local_path is not None:
            local = _local_file(local_path)
            if local is None:
                return tool_error(f"{local_path} does not exist on this machine (the one "
                                  "running the LiveBridge MCP server)", type="not_found",
                                  cmd=cmd)
            return upload_local_file(bridge, local, name=name, subfolder=subfolder,
                                     overwrite=overwrite)
        temp_dir, downloaded, error = download_url(url or "")
        if error is not None:
            return error
        try:
            result = upload_local_file(bridge, downloaded or "", name=name, subfolder=subfolder,
                                       overwrite=overwrite)
        finally:
            shutil.rmtree(temp_dir or "", ignore_errors=True)
        if isinstance(result, dict) and "error" not in result:
            result["url"] = url
        return result

    @mcp.tool()
    def live_sample_list(
        folder: str | None = None,
        pattern: str | None = None,
        extensions: str | None = None,
        recursive: bool = True,
        sort: str = "newest",
        limit: int = 50,
        offset: int = 0,
        newer_than: float | None = None,
        max_age_minutes: float | None = None,
    ) -> Any:
        """List audio files in a folder on the Live machine, newest first by default — or,
        without `folder`, the usual sample folders there (Splice folder, User Library, Live's
        Core Library, Factory Packs, Downloads, Desktop, the LiveBridge inbox).

        Args:
            folder: absolute folder path ("~/Splice/sounds", "~/Music/Samples"). Omit it to get
                the known folders instead of a listing.
            pattern: glob(s) on the file name, case-insensitive: "*.wav", "*kick*",
                "*.wav,*.aif".
            extensions: allowed extensions, e.g. "wav,aif", "mid" for MIDI files ("*" = any
                file). Default: every audio format Live imports.
            recursive: include sub-folders (default true).
            sort: "newest" (modified time, default), "added" (when the file arrived — best for
                downloads), "oldest", "name", "size".
            limit, offset: paging (limit 1-1000, default 50).
            newer_than: only files that arrived after this epoch time (use `now` from an
                earlier answer — it is the Live machine's clock).
            max_age_minutes: only files that arrived within this many minutes (fast on big
                libraries: only folders that changed are listed).

        Returns:
            With folder: `{folder, now, total, count, offset, scanned, truncated, next_offset?,
            files:[{path, name, size, mtime, age_s}]}`.
            Without: `{platform, home, splice:[{path, exists}], splice_folder,
            user_library:[{path, exists}], core_library, factory_packs, downloads, desktop,
            download_folders, inbox, live_library_cfg, music, env}` — e.g.
            `<core_library>/Samples/One Shots/Drums` holds the factory drum one-shots.

        Gotchas: partial downloads and hidden files are skipped; `truncated` means the scan
        stopped at 20000 entries / 5 s before reaching every sub-folder — files (even the
        newest) may be missing: use a narrower folder or max_age_minutes. The User Library
        comes from Live's own Library.cfg; the Splice folder can be set with the
        LIVEBRIDGE_SPLICE_DIR environment variable.
        """
        if folder is None:
            if any(v is not None for v in (pattern, extensions, newer_than, max_age_minutes)) \
                    or offset or limit != 50 or sort != "newest" or not recursive:
                return tool_error("listing options need a folder (omit them to get the known "
                                  "sample folders)", cmd="samples.list")
            return bridge_call(bridge, "samples.locations")
        cmd = "samples.list"
        if not folder.strip():
            return tool_error("folder must not be empty (omit it to get the known folders)",
                              cmd=cmd)
        if sort not in _SORTS:
            return tool_error(f"sort must be one of {', '.join(_SORTS)}", cmd=cmd)
        if not 1 <= limit <= 1000:
            return tool_error("limit must be between 1 and 1000", cmd=cmd)
        if offset < 0:
            return tool_error("offset must be >= 0", cmd=cmd)
        if max_age_minutes is not None and max_age_minutes <= 0:
            return tool_error("max_age_minutes must be > 0", cmd=cmd)
        args = drop_none(folder=folder, pattern=pattern, extensions=extensions,
                         newer_than=newer_than)
        if max_age_minutes is not None:
            args["max_age_s"] = max_age_minutes * 60.0
        if not recursive:
            args["recursive"] = False
        if sort != "newest":
            args["sort"] = sort
        if limit != 50:
            args["limit"] = limit
        if offset:
            args["offset"] = offset
        return bridge_call(bridge, cmd, args, timeout=_FILE_TIMEOUT)

    @mcp.tool()
    def live_sample_inspect(file_path: str) -> Any:
        """Check a path on the Live machine and describe the audio file: exists?, size, format,
        channels, sample rate, bit depth, duration. Use it to validate a path before importing.

        Args:
            file_path: file (or folder) path; `~`, env vars and `file://` URLs are expanded.

        Returns:
            `{input, path (normalised), ok, exists, is_file, is_dir, is_audio, size?, mtime?,
            platform, problems:[...], hints:[...], audio?:{format, channels, sample_rate,
            bit_depth, frames, duration, encoding?, bitrate_kbps?}}` — problems (missing file,
            Windows path on a Mac, relative path, ...) are reported, not raised.

        Gotchas: duration is exact for WAV/AIFF/FLAC, estimated for some MP3s; M4A/OGG/CAF only
        report the format.
        """
        if not file_path.strip():
            return tool_error("file_path must not be empty", cmd="samples.inspect")
        return bridge_call(bridge, "samples.inspect", {"file_path": file_path})
