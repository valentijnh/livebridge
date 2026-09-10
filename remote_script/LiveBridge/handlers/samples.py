"""Audio files on the machine that runs Live: import into the set, list
folders, inspect files, validate paths, known sample locations.

Every path here is a path **on the computer running Live** (the Remote Script
reads the disk, not the MCP server — which may sit on another machine).

Import routes (``samples.import``):

* ``arrangement`` — ``Track.create_audio_clip(path, position)`` on an audio track.
* ``session``, audio track — ``ClipSlot.create_audio_clip(path)`` into an empty
  slot (Live 12.x; verified on 12.4.5).
* ``session``, MIDI track / ``target="simpler"`` — a Simpler
  (``track.insert_device("Simpler")``, 12.3+) whose sample is replaced with
  ``SimplerDevice.replace_sample(path)``.
* ``target="browser"`` (and the fallback when the direct APIs are missing) —
  find the file in Live's browser by name (Places/User Library/Current
  Project/Samples), highlight the slot and ``browser.load_item`` it.

The response always says which route was used.
"""

import fnmatch
import ntpath
import os
import posixpath
import re
import struct
import sys
import time
import warnings

from .. import compat
from .. import resolve
from ..registry import BridgeError, command
from . import browser as browser_handlers

try:
    from urllib.parse import unquote as _unquote
except ImportError:  # pragma: no cover
    def _unquote(text):
        return text

#: Extensions Live can import as audio.
AUDIO_EXTENSIONS = (".wav", ".wave", ".aif", ".aiff", ".aifc", ".mp3", ".flac", ".ogg",
                    ".oga", ".m4a", ".mp4", ".aac", ".caf", ".alac")

#: Partial-download suffixes that are never imported.
PARTIAL_SUFFIXES = (".part", ".crdownload", ".download", ".tmp", ".partial", ".downloading")

MAX_SCAN = 20000
SCAN_SECONDS = 5.0

#: Class names of Live's Simpler.
_SIMPLER_CLASSES = ("OriginalSimpler",)


# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------

def _is_windows():
    return os.name == "nt" or sys.platform.startswith("win")


def platform_label(windows=None):
    windows = _is_windows() if windows is None else windows
    if windows:
        return "Windows"
    return "macOS" if sys.platform == "darwin" else sys.platform


def _expand_percent_vars(text, environ=None):
    """``%USERPROFILE%\\Splice`` -> expanded on every platform."""
    environ = os.environ if environ is None else environ

    def replace(match):
        name = match.group(1)
        for key in (name, name.upper()):
            if key in environ:
                return environ[key]
        if name.upper() == "USERPROFILE" and "HOME" in environ:
            return environ["HOME"]
        return match.group(0)
    return re.sub(r"%([A-Za-z_][A-Za-z0-9_]*)%", replace, text)


def _looks_windows_absolute(text):
    return bool(re.match(r"^[A-Za-z]:[\\/]", text)) or text.startswith("\\\\")


def normalize_path(raw, windows=None, environ=None):
    """Clean up a user-supplied path for the machine Live runs on.

    Strips quotes, turns ``file://`` URLs into paths, expands ``~``,
    ``$VAR``/``${VAR}`` and ``%VAR%``, fixes separators and normalises.

    Returns:
        ``(path, problems, hints)`` — ``problems`` non-empty means the path is
        unusable on this platform (e.g. a Windows path while Live runs on macOS).
    """
    windows = _is_windows() if windows is None else windows
    environ = os.environ if environ is None else environ
    pathmod = ntpath if windows else posixpath
    problems = []
    hints = []
    if not isinstance(raw, str) or not raw.strip():
        return "", ["the path is empty"], hints
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    if text.lower().startswith("file://"):
        text = _unquote(text[len("file://"):])
        if text.lower().startswith("localhost/"):
            text = text[len("localhost"):]
        if re.match(r"^/[A-Za-z]:[\\/]", text):
            text = text[1:]
        hints.append("converted a file:// URL to a path")
    text = _expand_percent_vars(text, environ)
    if "$" in text:
        text = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)",
                      lambda m: environ.get(m.group(1) or m.group(2), m.group(0)), text)
    if text.startswith("~"):
        home = environ.get("USERPROFILE") if windows else None
        home = home or environ.get("HOME") or os.path.expanduser("~")
        rest = text[1:]
        if not rest or rest[0] in "/\\":
            text = home + rest
    if windows:
        if text.startswith("/") and not text.startswith("//") and \
                not _looks_windows_absolute(text):
            problems.append("%r looks like a macOS/Linux path, but Live runs on Windows "
                            "here — use a path like C:\\Users\\<you>\\Music\\sample.wav" % raw)
        text = text.replace("/", "\\")
    else:
        if _looks_windows_absolute(text):
            problems.append("%r is a Windows path, but Live runs on %s here — use a path "
                            "like /Users/<you>/Music/sample.wav (the file must be on the "
                            "machine that runs Live)" % (raw, platform_label(False)))
        elif "\\" in text and "/" not in text:
            text = text.replace("\\", "/")
            hints.append("converted backslashes to '/'")
    if text:
        text = pathmod.normpath(text)
    if not problems and not pathmod.isabs(text):
        problems.append("%r is not an absolute path — pass the full path of the file on the "
                        "machine that runs Live (~ and environment variables are expanded)"
                        % raw)
    return text, problems, hints


def is_audio_path(path):
    return str(path).lower().endswith(AUDIO_EXTENSIONS)


def check_path(raw, want="file"):
    """Validation report for a path on this machine (never raises).

    Returns:
        {"input", "path", "ok", "exists", "is_file", "is_dir", "is_audio",
         "size"?, "problems": [...], "hints": [...], "platform"}
    """
    path, problems, hints = normalize_path(raw)
    report = {"input": raw, "path": path, "platform": platform_label(),
              "exists": False, "is_file": False, "is_dir": False}
    well_formed = not problems
    if not problems:
        try:
            report["exists"] = os.path.exists(path)
            report["is_file"] = os.path.isfile(path)
            report["is_dir"] = os.path.isdir(path)
        except (OSError, ValueError) as error:
            problems.append("cannot access %r: %s" % (path, error))
    report["is_audio"] = is_audio_path(path)
    if report["is_file"]:
        try:
            report["size"] = os.path.getsize(path)
        except OSError:
            pass
    if not problems:
        if not report["exists"]:
            parent = os.path.dirname(path)
            problems.append("%r does not exist on the machine that runs Live (%s)%s"
                            % (path, platform_label(),
                               "" if parent and os.path.isdir(parent)
                               else " — its folder does not exist either"))
            if parent and os.path.isdir(parent):
                similar = _similar_names(parent, os.path.basename(path))
                if similar:
                    hints.append("files with a similar name in that folder: %s"
                                 % ", ".join(similar))
        elif want == "file" and not report["is_file"]:
            problems.append("%r is a folder, not a file" % path)
        elif want == "dir" and not report["is_dir"]:
            problems.append("%r is a file, not a folder" % path)
        elif want == "file" and not report["is_audio"]:
            problems.append("%r is not an audio file Live imports (%s)"
                            % (path, ", ".join(AUDIO_EXTENSIONS[:9])))
        if want == "file" and report["is_file"] and \
                path.lower().endswith(PARTIAL_SUFFIXES):
            problems.append("%r is an unfinished download" % path)
    report["ok"] = not problems
    report["well_formed"] = well_formed
    report["problems"] = problems
    report["hints"] = hints
    return report


def _similar_names(folder, name, limit=5):
    wanted = os.path.splitext(name)[0].lower()
    if not wanted:
        return []
    found = []
    try:
        for entry in os.listdir(folder):
            if wanted[:4] and wanted[:4] in entry.lower():
                found.append(entry)
                if len(found) >= limit:
                    break
    except OSError:
        return []
    return found


def _raise_for(report):
    """``not_found`` for a well-formed path that does not exist, else ``bad_args``."""
    message = "; ".join(report["problems"])
    if report["hints"]:
        message += " (%s)" % "; ".join(report["hints"])
    kind = "not_found" if report.get("well_formed") and not report["exists"] else "bad_args"
    raise BridgeError(kind, message)


#: What the first bytes of a file say, and the extensions that go with it.
_SNIFF_EXTENSIONS = {
    "wav": (".wav", ".wave"), "aiff": (".aif", ".aiff", ".aifc"), "flac": (".flac",),
    "ogg": (".ogg", ".oga"), "caf": (".caf",), "mp4/m4a": (".m4a", ".mp4", ".aac", ".alac"),
}
_SUGGESTED_EXTENSION = {"wav": ".wav", "aiff": ".aif", "flac": ".flac", "ogg": ".ogg",
                        "caf": ".caf", "mp4/m4a": ".m4a"}


def sniff_format(path):
    """Container format from the file header ("wav", "aiff", "flac", "ogg", "caf",
    "mp4/m4a") or ``None`` (unknown / MP3 / unreadable)."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(12)
    except OSError:
        return None
    if head[:4] in (b"RIFF", b"RF64") and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        return "aiff"
    if head[:4] == b"fLaC":
        return "flac"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:4] == b"caff":
        return "caf"
    if head[4:8] == b"ftyp":
        return "mp4/m4a"
    return None


def format_mismatch(path):
    """A problem text when the header contradicts the extension (Live refuses such files:
    'The audio file ... cannot be read. This file does not appear to be a valid WAV
    file.' — verified on 12.4.5 with AIFF data named .wav), else ``None``."""
    found = sniff_format(path)
    if found is None:
        return None
    lowered = str(path).lower()
    if lowered.endswith(_SNIFF_EXTENSIONS[found]):
        return None
    if not any(lowered.endswith(exts) for exts in _SNIFF_EXTENSIONS.values()):
        return None     # .mp3 and friends: not sniffed
    return ("%r contains %s data but its extension says otherwise — Live refuses it; rename "
            "it to %s" % (os.path.basename(path), found.upper(), _SUGGESTED_EXTENSION[found]))


def require_file(raw):
    """Validated absolute audio file path or a ``bad_args``/``not_found`` error."""
    report = check_path(raw, "file")
    if not report["ok"]:
        _raise_for(report)
    mismatch = format_mismatch(report["path"])
    if mismatch:
        raise BridgeError("bad_args", mismatch)
    return report["path"]


def require_dir(raw):
    """Validated existing folder path or a ``bad_args``/``not_found`` error."""
    report = check_path(raw, "dir")
    if not report["ok"]:
        _raise_for(report)
    return report["path"]


# --------------------------------------------------------------------------
# listing
# --------------------------------------------------------------------------

def _patterns(pattern):
    if pattern is None:
        return []
    if isinstance(pattern, (list, tuple)):
        values = pattern
    elif isinstance(pattern, str):
        values = re.split(r"[,;|]", pattern)
    else:
        raise BridgeError("bad_args", "pattern must be a string like '*.wav' or a list")
    return [v.strip().lower() for v in values if isinstance(v, str) and v.strip()]


def _extensions(extensions):
    if extensions is None:
        return AUDIO_EXTENSIONS
    values = extensions if isinstance(extensions, (list, tuple)) else \
        re.split(r"[,;| ]+", str(extensions))
    result = []
    for value in values:
        value = str(value).strip().lower()
        if not value:
            continue
        if value in ("*", "any", "all"):
            return None
        result.append(value if value.startswith(".") else "." + value)
    return tuple(result) or AUDIO_EXTENSIONS


#: A folder counts as unchanged ("quiet") when its own modification time is at least this
#: many seconds older than ``newer_than`` (FAT/exFAT and some network shares store folder
#: times with 2 s resolution).
QUIET_MARGIN = 2.0

#: ``{root: {folder: (folder mtime, [sub-folders])}}`` — the sub-folders of quiet folders,
#: remembered between scans of the same root so a poll only lists folders that changed.
_DIR_CACHE = {}
_DIR_CACHE_ROOTS = 4
_DIR_CACHE_MAX = 200000


def _dir_cache_for(root):
    """The sub-folder cache of ``root`` (the most recently used roots are kept)."""
    cache = _DIR_CACHE.pop(root, None)
    if cache is None or len(cache) > _DIR_CACHE_MAX:
        cache = {}
    _DIR_CACHE[root] = cache
    while len(_DIR_CACHE) > _DIR_CACHE_ROOTS:
        _DIR_CACHE.pop(next(iter(_DIR_CACHE)))
    return cache


def _folder_time(path):
    """When a folder last gained/lost an entry (max of mtime and ctime), or ``None``."""
    try:
        stat = os.stat(path)
    except (OSError, ValueError):
        return None
    return max(stat.st_mtime, stat.st_ctime)


def scan_folder(folder, patterns=(), extensions=AUDIO_EXTENSIONS, recursive=True,
                max_scan=MAX_SCAN, max_seconds=SCAN_SECONDS, newer_than=None,
                dir_cache=None, max_depth=None):
    """Matching files under ``folder``.

    Returns ``(files, scanned, truncated)``; each file is a dict with ``path``,
    ``name``, ``size``, ``mtime`` and ``added`` (max of mtime and ctime: when
    the file arrived on this disk, even if the download kept an old mtime).

    With ``newer_than`` a folder whose own modification time is older than
    ``newer_than - QUIET_MARGIN`` cannot have gained a file since then (adding,
    moving or renaming a file into a folder updates *that* folder's time, not
    its parents'), so its files are not stat'ed and do not count towards
    ``max_scan``; only its sub-folders are followed.  ``dir_cache`` (a dict)
    remembers the sub-folders of such quiet folders, so a repeated scan of the
    same tree only lists the folders that changed.  ``scanned`` counts the
    entries actually examined.  ``max_depth`` limits how many sub-folder levels
    below ``folder`` are entered (0 = only ``folder``; ``recursive=False`` is 0).
    """
    files = []
    scanned = 0
    truncated = False
    deadline = time.time() + max_seconds
    quiet_before = None if newer_than is None else newer_than - QUIET_MARGIN
    if not recursive:
        max_depth = 0
    stack = [(folder, 0)]
    visited = 0
    while stack:
        current, depth = stack.pop()
        descend = max_depth is None or depth < max_depth
        visited += 1
        if (visited & 63) == 0 and time.time() > deadline:
            return files, scanned, True
        quiet = False
        folder_time = None
        if quiet_before is not None:
            folder_time = _folder_time(current)
            quiet = folder_time is not None and folder_time <= quiet_before
            if quiet and dir_cache is not None:
                cached = dir_cache.get(current)
                if cached is not None and cached[0] == folder_time:
                    scanned += 1
                    if descend:
                        stack.extend((sub, depth + 1) for sub in cached[1])
                    continue
            if quiet and not descend:
                scanned += 1
                continue
        try:
            iterator = os.scandir(current)
        except OSError:
            continue
        subfolders = []
        with iterator:
            for entry in iterator:
                name = entry.name
                if quiet:
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                    except OSError:
                        continue
                    if not is_dir:
                        continue
                scanned += 1
                if scanned > max_scan or ((scanned & 255) == 0 and time.time() > deadline):
                    truncated = True
                    return files, scanned, truncated
                if name.startswith("."):
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        subfolders.append(entry.path)
                        continue
                    if not entry.is_file():
                        continue
                except OSError:
                    continue
                lowered = name.lower()
                if lowered.endswith(PARTIAL_SUFFIXES):
                    continue
                if extensions is not None and not lowered.endswith(extensions):
                    continue
                if patterns and not any(fnmatch.fnmatchcase(lowered, p) for p in patterns):
                    continue
                try:
                    stat = entry.stat()
                except OSError:
                    continue
                added = max(stat.st_mtime, stat.st_ctime)
                if newer_than is not None and added <= newer_than:
                    continue
                files.append({"path": entry.path, "name": name, "size": stat.st_size,
                              "mtime": stat.st_mtime, "added": added})
        if dir_cache is not None and folder_time is not None:
            dir_cache[current] = (folder_time, subfolders)
        if descend:
            stack.extend((sub, depth + 1) for sub in subfolders)
    return files, scanned, truncated


_SORTS = {
    "newest": (lambda f: f["mtime"], True),
    "oldest": (lambda f: f["mtime"], False),
    "added": (lambda f: f["added"], True),
    "name": (lambda f: f["name"].lower(), False),
    "size": (lambda f: f["size"], True),
}


# --------------------------------------------------------------------------
# audio metadata (stdlib only)
# --------------------------------------------------------------------------

def _extended_to_float(data):
    """IEEE 754 80-bit extended (AIFF sample rate) -> float."""
    exponent = struct.unpack(">H", data[:2])[0]
    mantissa = struct.unpack(">Q", data[2:10])[0]
    sign = -1.0 if exponent & 0x8000 else 1.0
    exponent &= 0x7FFF
    if exponent == 0 and mantissa == 0:
        return 0.0
    return sign * mantissa * 2.0 ** (exponent - 16383 - 63)


def _riff_info(path):
    """Channels, rate, bits and duration from a WAV's fmt/data chunks
    (handles WAVE_FORMAT_EXTENSIBLE and float files the ``wave`` module rejects)."""
    with open(path, "rb") as handle:
        header = handle.read(12)
        if len(header) < 12 or header[:4] not in (b"RIFF", b"RF64") or header[8:12] != b"WAVE":
            return None
        info = {"format": "wav"}
        byte_rate = None
        while True:
            chunk = handle.read(8)
            if len(chunk) < 8:
                break
            chunk_id, size = chunk[:4], struct.unpack("<I", chunk[4:8])[0]
            if chunk_id == b"fmt ":
                fmt = handle.read(size)
                if len(fmt) >= 16:
                    code, channels, rate, byte_rate, _align, bits = struct.unpack(
                        "<HHIIHH", fmt[:16])
                    info.update({"channels": channels, "sample_rate": rate,
                                 "bit_depth": bits,
                                 "encoding": {1: "pcm", 3: "float", 0xFFFE: "extensible"}.get(
                                     code, "code %d" % code)})
                if size % 2:
                    handle.read(1)
                continue
            if chunk_id == b"data":
                if byte_rate:
                    info["duration"] = round(size / float(byte_rate), 4)
                    if info.get("channels") and info.get("bit_depth"):
                        info["frames"] = size // (info["channels"] * max(1, info["bit_depth"] // 8))
                break
            handle.seek(size + (size % 2), 1)
        return info


def _aiff_info(path):
    """AIFF/AIFC COMM chunk (stdlib ``aifc`` is deprecated and gone in 3.13)."""
    with open(path, "rb") as handle:
        header = handle.read(12)
        if len(header) < 12 or header[:4] != b"FORM" or header[8:12] not in (b"AIFF", b"AIFC"):
            return None
        info = {"format": "aiff" if header[8:12] == b"AIFF" else "aifc"}
        while True:
            chunk = handle.read(8)
            if len(chunk) < 8:
                break
            chunk_id, size = chunk[:4], struct.unpack(">I", chunk[4:8])[0]
            if chunk_id == b"COMM":
                data = handle.read(size)
                if len(data) >= 18:
                    channels, frames, bits = struct.unpack(">hIh", data[:8])
                    rate = _extended_to_float(data[8:18])
                    info.update({"channels": channels, "frames": frames, "bit_depth": bits,
                                 "sample_rate": int(round(rate))})
                    if rate:
                        info["duration"] = round(frames / rate, 4)
                break
            handle.seek(size + (size % 2), 1)
        return info


def _aifc_module_info(path):
    """The stdlib ``aifc`` route (Python 3.11/3.12 only; guarded)."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            import aifc  # noqa: F401  (removed in Python 3.13)
    except Exception:
        return None
    try:
        handle = aifc.open(path, "rb")
    except Exception:
        return None
    try:
        rate = handle.getframerate()
        frames = handle.getnframes()
        return {"format": "aiff", "channels": handle.getnchannels(), "sample_rate": rate,
                "bit_depth": handle.getsampwidth() * 8, "frames": frames,
                "duration": round(frames / float(rate), 4) if rate else None}
    finally:
        handle.close()


def _wave_module_info(path):
    try:
        import wave
        handle = wave.open(path, "rb")
    except Exception:
        return None
    try:
        rate = handle.getframerate()
        frames = handle.getnframes()
        return {"format": "wav", "encoding": "pcm", "channels": handle.getnchannels(),
                "sample_rate": rate, "bit_depth": handle.getsampwidth() * 8,
                "frames": frames, "duration": round(frames / float(rate), 4) if rate else None}
    finally:
        handle.close()


def _flac_info(path):
    with open(path, "rb") as handle:
        if handle.read(4) != b"fLaC":
            return None
        header = handle.read(4)
        if len(header) < 4 or (header[0] & 0x7F) != 0:
            return {"format": "flac"}
        block = handle.read(34)
        if len(block) < 18:
            return {"format": "flac"}
        packed = struct.unpack(">Q", block[10:18])[0]
        rate = packed >> 44
        channels = ((packed >> 41) & 0x7) + 1
        bits = ((packed >> 36) & 0x1F) + 1
        frames = packed & 0xFFFFFFFFF
        return {"format": "flac", "sample_rate": rate, "channels": channels,
                "bit_depth": bits, "frames": frames,
                "duration": round(frames / float(rate), 4) if rate else None}


_MP3_BITRATES = {
    (1, 1): [0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448],
    (1, 2): [0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384],
    (1, 3): [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],
    (2, 1): [0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256],
    (2, 2): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],
}
_MP3_RATES = {1: [44100, 48000, 32000], 2: [22050, 24000, 16000], 25: [11025, 12000, 8000]}


def _mp3_info(path, size):
    """Best effort: first frame header (+ Xing/Info frame count), else CBR estimate."""
    with open(path, "rb") as handle:
        data = handle.read(65536)
        base = 0
        if data[:3] == b"ID3" and len(data) >= 10:
            base = 10 + (((data[6] & 0x7F) << 21) | ((data[7] & 0x7F) << 14) |
                         ((data[8] & 0x7F) << 7) | (data[9] & 0x7F))
            handle.seek(base)
            data = handle.read(65536)
    for index in range(0, max(0, len(data) - 4)):
        if data[index] != 0xFF or (data[index + 1] & 0xE0) != 0xE0:
            continue
        b1, b2, b3 = data[index + 1], data[index + 2], data[index + 3]
        version_bits = (b1 >> 3) & 0x3
        layer_bits = (b1 >> 1) & 0x3
        if version_bits == 1 or layer_bits == 0:
            continue
        version = {3: 1, 2: 2, 0: 25}[version_bits]
        layer = 4 - layer_bits
        bitrate_index = (b2 >> 4) & 0xF
        rate_index = (b2 >> 2) & 0x3
        if bitrate_index in (0, 15) or rate_index == 3:
            continue
        table = _MP3_BITRATES.get((1 if version == 1 else 2, layer if version == 1 else
                                   (1 if layer == 1 else 2)))
        bitrate = table[bitrate_index] * 1000
        rate = _MP3_RATES[version][rate_index]
        channels = 1 if ((b3 >> 6) & 0x3) == 3 else 2
        info = {"format": "mp3", "sample_rate": rate, "channels": channels,
                "bitrate_kbps": bitrate // 1000}
        samples_per_frame = 1152 if layer == 3 and version == 1 else (576 if layer == 3 else
                                                                     (384 if layer == 1 else 1152))
        xing = data.find(b"Xing", index, index + 64)
        if xing < 0:
            xing = data.find(b"Info", index, index + 64)
        if xing >= 0 and len(data) >= xing + 12:
            flags = struct.unpack(">I", data[xing + 4:xing + 8])[0]
            if flags & 1:
                frames = struct.unpack(">I", data[xing + 8:xing + 12])[0]
                info["duration"] = round(frames * samples_per_frame / float(rate), 3)
                return info
        if bitrate:
            info["duration"] = round((size - base - index) * 8.0 / bitrate, 3)
            info["duration_estimated"] = True
        return info
    return {"format": "mp3"}


def audio_info(path):
    """Format details of an audio file (never raises; ``{}`` when unknown)."""
    lowered = path.lower()
    try:
        size = os.path.getsize(path)
        if lowered.endswith((".wav", ".wave")):
            return _wave_module_info(path) or _riff_info(path) or {}
        if lowered.endswith((".aif", ".aiff", ".aifc")):
            return _aifc_module_info(path) or _aiff_info(path) or {}
        if lowered.endswith(".flac"):
            return _flac_info(path) or {}
        if lowered.endswith(".mp3"):
            return _mp3_info(path, size)
        with open(path, "rb") as handle:
            head = handle.read(12)
        if head[:4] == b"RIFF":
            return _riff_info(path) or {}
        if head[:4] == b"FORM":
            return _aiff_info(path) or {}
        if head[4:8] == b"ftyp":
            return {"format": "mp4/m4a"}
        if head[:4] == b"OggS":
            return {"format": "ogg"}
        if head[:4] == b"caff":
            return {"format": "caf"}
    except (OSError, struct.error, ValueError, IndexError, KeyError):
        return {}
    return {}


# --------------------------------------------------------------------------
# known locations
# --------------------------------------------------------------------------

#: ``HKCU\...\Explorer\User Shell Folders`` value names of the known folders used here.
_SHELL_DOCUMENTS = "Personal"
_SHELL_DESKTOP = "Desktop"
_SHELL_DOWNLOADS = "{374DE290-123F-4565-9164-39C4925E467B}"


def windows_shell_folders(environ=None):
    """Windows known folders from the registry (``User Shell Folders``): ``{value name:
    expanded path}`` for Documents (``Personal``), Desktop and Downloads.  This is where
    Windows really keeps them — OneDrive folder backup and a manually moved Documents
    folder included.  ``{}`` when not on Windows or the key cannot be read (never
    raises)."""
    environ = os.environ if environ is None else environ
    try:
        import winreg
    except ImportError:
        return {}
    result = {}
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             "Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\"
                             "User Shell Folders")
    except OSError:
        return {}
    try:
        for name in (_SHELL_DOCUMENTS, _SHELL_DESKTOP, _SHELL_DOWNLOADS):
            try:
                value, _kind = winreg.QueryValueEx(key, name)
            except OSError:
                continue
            if isinstance(value, str) and value.strip():
                result[name] = _expand_percent_vars(value.strip(), environ)
    finally:
        try:
            winreg.CloseKey(key)
        except OSError:
            pass
    return result


def _shell_folders(windows, environ, shell_folders):
    if shell_folders is not None:
        return shell_folders
    if windows and _is_windows():
        return windows_shell_folders(environ)
    return {}


def _windows_documents_dirs(environ, home, shell_folders=None):
    """The Windows "Documents" folders to try: the real Documents known folder from the
    registry first (it follows OneDrive folder backup and a moved Documents folder), then
    ``%USERPROFILE%\\Documents``, then OneDrive-redirected ones (``%OneDrive%`` /
    ``%OneDriveConsumer%`` / ``%OneDriveCommercial%`` + ``Documents``, and
    ``%USERPROFILE%\\OneDrive\\Documents``) — Ableton's User Library and Splice's older
    download folder live inside Documents."""
    dirs = []
    known = (shell_folders or {}).get(_SHELL_DOCUMENTS)
    if known:
        dirs.append(ntpath.normpath(known))
    dirs.append(ntpath.join(home, "Documents"))
    for key in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        root = environ.get(key)
        if root:
            dirs.append(ntpath.join(root, "Documents"))
    dirs.append(ntpath.join(home, "OneDrive", "Documents"))
    return _unique(dirs)


def _unique(paths):
    unique = []
    for path in paths:
        if path and path not in unique:
            unique.append(path)
    return unique


def _home(environ, windows, home):
    return home or (environ.get("USERPROFILE") if windows else None) or \
        environ.get("HOME") or os.path.expanduser("~")


# ---- Live's Library.cfg ----------------------------------------------------

_LIVE_PREFS_RE = re.compile(r"^Live (\d+)\.(\d+)(?:\.(\d+))?")


def live_preferences_dirs(environ=None, windows=None, home=None, version=None):
    """Live's per-version preferences folders, newest first (the running Live's version
    first when ``version`` — e.g. ``"12.4.5"`` — is given):

    * macOS: ``~/Library/Preferences/Ableton/Live 12.4.5``
    * Windows: ``%APPDATA%\\Ableton\\Live 12.4.5\\Preferences``
    """
    environ = os.environ if environ is None else environ
    windows = _is_windows() if windows is None else windows
    home = _home(environ, windows, home)
    # these folders are read from disk, so they are joined with the real os.path
    if windows:
        appdata = environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
        root = os.path.join(appdata, "Ableton")
    else:
        root = os.path.join(home, "Library", "Preferences", "Ableton")
    try:
        names = os.listdir(root)
    except (OSError, ValueError):
        return []
    found = []
    for name in names:
        match = _LIVE_PREFS_RE.match(name)
        if not match:
            continue
        key = (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))
        folder = os.path.join(root, name, "Preferences") if windows else os.path.join(root, name)
        preferred = bool(version) and name == "Live %s" % version
        found.append(((preferred, key), folder))
    found.sort(reverse=True)
    return [folder for _key, folder in found]


def read_library_cfg(path):
    """What Live's ``Library.cfg`` says (never raises; ``{}`` when unreadable):

    * ``user_library`` — ``<UserLibrary><LibraryProject><ProjectPath Value=.../>
      <ProjectName Value="User Library"/>`` joined;
    * ``factory_packs`` — ``PreferredFactoryPacksInstallationPath`` (when set);
    * ``splice_download_mode`` / ``splice_download_path`` —
      ``SpliceDownloadFolderModeMember`` ("UserLibrary" by default) and
      ``CustomSpliceDownloadPathMember``: where Live 12.3+'s own Splice browser
      saves downloads.
    """
    try:
        from xml.etree import ElementTree
        root = ElementTree.parse(path).getroot()
    except Exception:
        return {}
    result = {}
    for library in root.iter("UserLibrary"):
        for project in library.iter("LibraryProject"):
            base_node = project.find("ProjectPath")
            name_node = project.find("ProjectName")
            base = base_node.get("Value", "") if base_node is not None else ""
            name = name_node.get("Value", "") if name_node is not None else ""
            if base:
                separator = "\\" if _looks_windows_absolute(base) else "/"
                result["user_library"] = base.rstrip("\\/") + separator + \
                    (name or "User Library")
                break
        if "user_library" in result:
            break
    for tag, key in (("PreferredFactoryPacksInstallationPath", "factory_packs"),
                     ("SpliceDownloadFolderModeMember", "splice_download_mode"),
                     ("CustomSpliceDownloadPathMember", "splice_download_path")):
        node = root.find(".//" + tag)
        value = node.get("Value", "") if node is not None else ""
        if value:
            result[key] = value
    return result


def live_library_config(environ=None, windows=None, home=None, version=None):
    """``read_library_cfg`` of the newest (or running) Live's ``Library.cfg`` plus
    ``cfg`` (its path), or ``{}``."""
    for folder in live_preferences_dirs(environ, windows, home, version):
        cfg = os.path.join(folder, "Library.cfg")
        if os.path.isfile(cfg):
            data = read_library_cfg(cfg)
            if data:
                data["cfg"] = cfg
                return data
    return {}


def _running_version():
    try:
        return compat.live_version_string()
    except Exception:
        return None


def splice_candidates(environ=None, windows=None, home=None, shell_folders=None,
                      library=None):
    """Folders where Splice downloads usually land, most specific first:
    ``LIVEBRIDGE_SPLICE_DIR``, the Splice app's folder (``~/Splice/sounds`` ...), Live's
    own Splice download folder from ``Library.cfg`` (``library``: a
    ``read_library_cfg`` result), then Downloads and Desktop (Splice's MCP and web
    downloads go wherever the user saves them — usually one of those)."""
    environ = os.environ if environ is None else environ
    windows = _is_windows() if windows is None else windows
    pathmod = ntpath if windows else posixpath
    home = _home(environ, windows, home)
    shell = _shell_folders(windows, environ, shell_folders)
    candidates = []
    custom = environ.get("LIVEBRIDGE_SPLICE_DIR")
    if custom:
        candidates.append(normalize_path(custom, windows, environ)[0] or custom)
    if windows:
        candidates += [pathmod.join(home, "Splice", "sounds"), pathmod.join(home, "Splice")]
        for documents in _windows_documents_dirs(environ, home, shell):
            candidates += [pathmod.join(documents, "Splice", "sounds"),
                           pathmod.join(documents, "Splice")]
    else:
        candidates += [pathmod.join(home, "Splice", "sounds"), pathmod.join(home, "Splice"),
                       pathmod.join(home, "Music", "Splice"),
                       pathmod.join(home, "Documents", "Splice")]
    candidates += live_splice_folders(library or {}, windows)
    return _unique(candidates)


def live_splice_folders(library, windows=None):
    """Where Live's own Splice browser (12.3+) saves downloads, from a ``Library.cfg``:
    the custom path when ``SpliceDownloadFolderModeMember`` is not "UserLibrary",
    else ``<User Library>/Samples/Splice`` and ``<User Library>/Splice`` (unverified
    guesses — only existing ones matter)."""
    windows = _is_windows() if windows is None else windows
    pathmod = ntpath if windows else posixpath
    folders = []
    mode = str(library.get("splice_download_mode") or "")
    custom = library.get("splice_download_path")
    if custom and mode.lower() != "userlibrary":
        folders.append(custom)
    user_library = library.get("user_library")
    if user_library:
        folders += [pathmod.join(user_library, "Samples", "Splice"),
                    pathmod.join(user_library, "Splice")]
    return folders


def download_folders(environ=None, windows=None, home=None, shell_folders=None):
    """``(downloads, desktop)`` candidate lists (registry known folders first on Windows,
    then ``~/Downloads`` / ``~/Desktop`` and OneDrive's Desktop)."""
    environ = os.environ if environ is None else environ
    windows = _is_windows() if windows is None else windows
    pathmod = ntpath if windows else posixpath
    home = _home(environ, windows, home)
    shell = _shell_folders(windows, environ, shell_folders)
    downloads = []
    desktop = []
    if shell.get(_SHELL_DOWNLOADS):
        downloads.append(pathmod.normpath(shell[_SHELL_DOWNLOADS]))
    if shell.get(_SHELL_DESKTOP):
        desktop.append(pathmod.normpath(shell[_SHELL_DESKTOP]))
    downloads.append(pathmod.join(home, "Downloads"))
    desktop.append(pathmod.join(home, "Desktop"))
    if windows:
        for key in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
            if environ.get(key):
                desktop.append(pathmod.join(environ[key], "Desktop"))
    return _unique(downloads), _unique(desktop)


def user_library_candidates(environ=None, windows=None, home=None, shell_folders=None,
                            library=None):
    """Live's User Library: the one recorded in ``Library.cfg`` (``library``) first, then
    the default place(s) — on Windows every Documents folder (see
    ``_windows_documents_dirs``)."""
    environ = os.environ if environ is None else environ
    windows = _is_windows() if windows is None else windows
    pathmod = ntpath if windows else posixpath
    home = _home(environ, windows, home)
    candidates = []
    if library and library.get("user_library"):
        candidates.append(library["user_library"])
    if windows:
        shell = _shell_folders(windows, environ, shell_folders)
        candidates += [pathmod.join(documents, "Ableton", "User Library")
                       for documents in _windows_documents_dirs(environ, home, shell)]
    else:
        candidates.append(pathmod.join(home, "Music", "Ableton", "User Library"))
    return _unique(candidates)


def core_library_candidates(executable=None, windows=None):
    """Live's own Core Library folder (factory samples, racks, clips), derived from the
    running Live executable:

    * macOS: ``/Applications/Ableton Live 12 Suite.app/Contents/MacOS/Live`` ->
      ``.../Contents/App-Resources/Core Library``
    * Windows: ``C:\\ProgramData\\Ableton\\Live 12 Suite\\Program\\Ableton Live 12 Suite.exe``
      -> ``C:\\ProgramData\\Ableton\\Live 12 Suite\\Resources\\Core Library``
    """
    windows = _is_windows() if windows is None else windows
    pathmod = ntpath if windows else posixpath
    executable = executable if executable is not None else (sys.executable or "")
    if not executable:
        return []
    folder = pathmod.dirname(executable)
    candidates = []
    for _level in range(3):
        parent = pathmod.dirname(folder)
        for sub in (("App-Resources", "Core Library"), ("Resources", "Core Library")):
            candidates.append(pathmod.join(folder, *sub))
        if not parent or parent == folder:
            break
        folder = parent
    unique = []
    for path in candidates:
        if path not in unique:
            unique.append(path)
    return unique


def factory_packs_candidates(environ=None, windows=None, home=None, shell_folders=None,
                             library=None):
    """Where Live installs downloaded packs: ``PreferredFactoryPacksInstallationPath``
    from ``Library.cfg`` when set, else the default ``Factory Packs`` folder(s)."""
    environ = os.environ if environ is None else environ
    windows = _is_windows() if windows is None else windows
    pathmod = ntpath if windows else posixpath
    home = _home(environ, windows, home)
    candidates = []
    if library and library.get("factory_packs"):
        candidates.append(library["factory_packs"])
    if windows:
        shell = _shell_folders(windows, environ, shell_folders)
        candidates += [pathmod.join(documents, "Ableton", "Factory Packs")
                       for documents in _windows_documents_dirs(environ, home, shell)]
    else:
        candidates.append(pathmod.join(home, "Music", "Ableton", "Factory Packs"))
    return _unique(candidates)


def _location(path):
    try:
        exists = os.path.isdir(path)
    except (OSError, ValueError):
        exists = False
    return {"path": path, "exists": exists}


def _first_existing(paths):
    """``{path, exists: true}`` of the first existing folder, else ``None``."""
    for path in paths:
        location = _location(path)
        if location["exists"]:
            return location
    return None


# --------------------------------------------------------------------------
# import helpers
# --------------------------------------------------------------------------

def _get(obj, name, default=None):
    return compat.safe_getattr(obj, name, default)


def _track_kind(ctx, track):
    song = ctx.song
    master = _get(song, "master_track")
    if master is not None and (master is track or master == track):
        return "master"
    for candidate in _get(song, "return_tracks", ()) or ():
        if candidate is track or candidate == track:
            return "return"
    if _get(track, "has_midi_input", False):
        return "midi"
    if _get(track, "has_audio_input", False) or _get(track, "has_audio_output", False):
        return "audio"
    return "audio"


def _file_stem(path):
    return os.path.splitext(os.path.basename(path.replace("\\", "/")))[0]


def _live_error(what, error):
    return BridgeError("invalid_state", "%s failed: %s" % (what, error))


def _first_empty_slot(ctx, track, start=0):
    """Index of the first empty clip slot at/after ``start``; adds a scene when
    the track is full.  Returns ``(index, created_scene)``."""
    slots = list(_get(track, "clip_slots", ()) or ())
    for index in range(max(0, start), len(slots)):
        if not _get(slots[index], "has_clip", False):
            return index, False
    song = ctx.song
    if not compat.has(song, "create_scene"):
        raise BridgeError("invalid_state", "every clip slot of %r is full"
                          % _get(track, "name", ""))
    try:
        song.create_scene(-1)
    except Exception as error:
        raise _live_error("song.create_scene", error)
    return len(list(_get(track, "clip_slots", ()) or ())) - 1, True


def _slot_index(track, clip_slot):
    for index, candidate in enumerate(_get(track, "clip_slots", ()) or ()):
        try:
            if candidate is clip_slot or candidate == clip_slot:
                return index
        except Exception:
            continue
    return None


def _apply_clip_options(clip, name, warp):
    applied = {}
    if clip is None:
        return applied
    if name:
        try:
            clip.name = str(name)
            applied["name"] = str(name)
        except Exception:
            pass
    if warp is not None and compat.has(clip, "warping"):
        try:
            clip.warping = bool(warp)
            applied["warping"] = bool(warp)
        except Exception:
            pass
    return applied


def _instrument_index(devices):
    """Where an instrument goes: after MIDI effects, before audio effects."""
    index = 0
    for device in devices:
        if int(_get(device, "type", 0) or 0) == 4:  # midi_effect
            index += 1
        else:
            break
    return index


def _find_instrument(devices):
    for device in devices:
        if int(_get(device, "type", 0) or 0) == 1:
            return device
    return None


def _import_simpler(ctx, track, path, notes):
    """Simpler on ``track`` holding ``path``.  Returns ``(route, device)``."""
    devices = list(_get(track, "devices", ()) or ())
    instrument = _find_instrument(devices)
    if instrument is not None:
        if str(_get(instrument, "class_name", "")) not in _SIMPLER_CLASSES:
            raise BridgeError(
                "invalid_state",
                "%r already has an instrument (%s) — pick a MIDI track without an instrument, "
                "a track with a Simpler (its sample is replaced), or leave track empty to "
                "create a new MIDI track" % (_get(track, "name", ""),
                                             _get(instrument, "name", "?")))
        if not compat.has(instrument, "replace_sample"):
            raise BridgeError("unsupported", "SimplerDevice.replace_sample is not available "
                                             "in this Live")
        try:
            instrument.replace_sample(path)
        except Exception as error:
            raise _live_error("Simpler.replace_sample", error)
        notes.append("replaced the sample of the existing Simpler")
        return "simpler.replace_sample", instrument
    device = None
    if compat.has(track, "insert_device"):
        try:
            device = track.insert_device("Simpler", _instrument_index(devices))
        except Exception as error:
            raise _live_error("track.insert_device('Simpler')", error)
        route = "track.insert_device + simpler.replace_sample"
    else:
        browser = browser_handlers.require_browser(ctx)
        entry = browser_handlers.find_by_path(browser, "instruments/Simpler")
        browser_handlers.load_entry(ctx, browser, entry, track)
        route = "browser Simpler + simpler.replace_sample"
    if device is None:
        device = _find_instrument(list(_get(track, "devices", ()) or ()))
    if device is None or not compat.has(device, "replace_sample"):
        raise BridgeError("unsupported", "could not get a Simpler with replace_sample on %r "
                                         "(needs Live 12)" % _get(track, "name", ""))
    try:
        device.replace_sample(path)
    except Exception as error:
        raise _live_error("Simpler.replace_sample", error)
    return route, device


def _session_slot(ctx, track_obj, slot, overwrite, notes, result):
    """The clip slot a session import goes to (see ``samples.import`` ``slot``);
    records ``slot`` / ``created_scene`` in ``result``."""
    highlighted = _get(ctx.view, "highlighted_clip_slot")
    highlighted_index = _slot_index(track_obj, highlighted) if highlighted is not None \
        else None
    if slot is None and highlighted_index is not None and \
            not _get(highlighted, "has_clip", False):
        clip_slot, index = highlighted, highlighted_index
        notes.append("used the highlighted clip slot")
    elif slot is None:
        index, created_scene = _first_empty_slot(ctx, track_obj)
        if created_scene:
            result["created_scene"] = True
            notes.append("the track was full, so a scene was added")
        clip_slot = list(track_obj.clip_slots)[index]
    else:
        clip_slot = ctx.clip_slot(track_obj, slot)
        index = _slot_index(track_obj, clip_slot)
        if _get(clip_slot, "has_clip", False):
            if not overwrite:
                raise BridgeError("invalid_state", "clip slot %s of %r already has a clip "
                                  "— pass overwrite=true or another slot"
                                  % (slot, _get(track_obj, "name", "")))
            try:
                clip_slot.delete_clip()
            except Exception as error:
                raise _live_error("clip_slot.delete_clip", error)
            notes.append("replaced the clip that was in the slot")
    result["slot"] = index
    return clip_slot


def _is_drum_rack(device):
    return bool(_get(device, "can_have_drum_pads", False))


def drum_rack_on(ctx, track, notes):
    """The top-level Drum Rack of ``track``, inserting an empty one when the track has
    no instrument.  Returns ``(rack, created)``."""
    devices = list(_get(track, "devices", ()) or ())
    for device in devices:
        if _is_drum_rack(device):
            return device, False
    instrument = _find_instrument(devices)
    if instrument is not None:
        raise BridgeError("invalid_state", "%r already has an instrument (%s) and no Drum "
                          "Rack — pick a track with a Drum Rack or without an instrument, or "
                          "leave track empty to create one"
                          % (_get(track, "name", ""), _get(instrument, "name", "?")))
    if not compat.has(track, "insert_device"):
        raise BridgeError("unsupported", "Track.insert_device needs Live 12.3+")
    try:
        rack = track.insert_device("Drum Rack", _instrument_index(devices))
    except Exception as error:
        raise _live_error("track.insert_device('Drum Rack')", error)
    if rack is None:
        rack = next((d for d in _get(track, "devices", ()) or () if _is_drum_rack(d)), None)
    if rack is None:
        raise BridgeError("internal", "the new Drum Rack did not appear")
    notes.append("inserted a Drum Rack")
    return rack, True


def _pad_chains(rack, note):
    """``(pad or None, [chains])`` of MIDI note ``note``."""
    for pad in _get(rack, "drum_pads", ()) or ():
        if _get(pad, "note") == note:
            return pad, list(_get(pad, "chains", ()) or ())
    return None, [chain for chain in _get(rack, "chains", ()) or ()
                  if _get(chain, "in_note") == note]


def first_empty_pad(rack, start=36):
    """The first pad without chains at/after ``start`` (then below it), or ``None``."""
    for note in list(range(start, 128)) + list(range(start - 1, -1, -1)):
        if not _pad_chains(rack, note)[1]:
            return note
    return None


def _note_label(note):
    names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
    return "%s%d" % (names[note % 12], note // 12 - 2)


def import_drum_pad(ctx, rack, note, path, chain_name, overwrite, notes):
    """Put ``path`` on pad ``note`` of Drum Rack ``rack``: replace the sample of the
    pad's Simpler, or add a chain with a Simpler (``insert_chain`` + ``in_note`` +
    ``insert_device("Simpler")`` + ``replace_sample``, Live 12.3+).
    Returns ``(route, device, chain)``."""
    pad, chains = _pad_chains(rack, note)
    if chains:
        simplers = [d for chain in chains for d in _get(chain, "devices", ()) or ()
                    if str(_get(d, "class_name", "")) in _SIMPLER_CLASSES]
        if len(chains) == 1 and simplers and compat.has(simplers[0], "replace_sample"):
            try:
                simplers[0].replace_sample(path)
            except Exception as error:
                raise _live_error("Simpler.replace_sample", error)
            notes.append("replaced the sample of the Simpler on pad %s" % _note_label(note))
            if chain_name:
                try:
                    chains[0].name = str(chain_name)
                except Exception:
                    pass
            return "drum_pad simpler.replace_sample", simplers[0], chains[0]
        if not overwrite:
            raise BridgeError("invalid_state", "pad %s (%d) already holds %s — pass "
                              "overwrite=true to replace it, or another note"
                              % (_note_label(note), note,
                                 ", ".join(repr(_get(c, "name", "?")) for c in chains[:4])))
        if pad is None or not compat.has(pad, "delete_all_chains"):
            raise BridgeError("unsupported", "cannot clear pad %s (nested Drum Rack)"
                              % _note_label(note))
        try:
            pad.delete_all_chains()
        except Exception as error:
            raise _live_error("drum_pad.delete_all_chains", error)
        notes.append("cleared pad %s" % _note_label(note))
    if not compat.has(rack, "insert_chain"):
        raise BridgeError("unsupported", "RackDevice.insert_chain needs Live 12.3+")
    before = list(_get(rack, "chains", ()) or ())
    try:
        chain = rack.insert_chain(-1)
    except Exception as error:
        raise _live_error("rack.insert_chain", error)
    if chain is None:
        after = list(_get(rack, "chains", ()) or ())
        chain = after[len(before)] if len(after) > len(before) else None
    if chain is None:
        raise BridgeError("internal", "the new drum chain did not appear")
    try:
        chain.in_note = int(note)
    except Exception as error:
        raise _live_error("chain.in_note", error)
    if chain_name:
        try:
            chain.name = str(chain_name)
        except Exception:
            pass
    try:
        device = chain.insert_device("Simpler", -1)
    except Exception as error:
        raise _live_error("chain.insert_device('Simpler')", error)
    if device is None:
        device = next((d for d in _get(chain, "devices", ()) or ()
                       if str(_get(d, "class_name", "")) in _SIMPLER_CLASSES), None)
    if device is None or not compat.has(device, "replace_sample"):
        raise BridgeError("unsupported", "could not get a Simpler with replace_sample on the "
                          "new pad (needs Live 12)")
    try:
        device.replace_sample(path)
    except Exception as error:
        raise _live_error("Simpler.replace_sample", error)
    return "rack.insert_chain + chain.insert_device('Simpler') + simpler.replace_sample", \
        device, chain


def _import_browser(ctx, track, path, clip_slot, notes):
    """Fallback: load the file's browser item (it must be in a browsable folder)."""
    browser = browser_handlers.require_browser(ctx)
    entry = browser_handlers.find_file_item(browser, path)
    if entry is None:
        raise BridgeError("not_found", "%r is not in a folder Live's browser shows (Places / "
                          "User Library / Current Project) — add its folder to Places in "
                          "Live's browser, or use target='clip' (audio track) or "
                          "target='simpler'" % os.path.basename(path))
    changes = browser_handlers.load_entry(ctx, browser, entry, track, slot=clip_slot)
    notes.extend(changes.pop("notes", []))
    return changes, entry


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

_MODES = ("session", "arrangement", "simpler")
_TARGETS = ("auto", "clip", "simpler", "browser", "drum_pad")


@command("samples.import", mutating=True,
         doc="Import an audio file into a session slot, the arrangement or a Simpler")
def samples_import(ctx, file_path, track=None, mode="session", slot=None, time=None,
                   target="auto", track_name=None, name=None, warp=None, select=False,
                   overwrite=False, note=None):
    """Put an audio file from disk into the Live set.

    Args:
        file_path: absolute path on the machine that runs Live (``~``, ``$VAR``
            and ``%VAR%`` are expanded; ``file://`` URLs accepted).
        track: target track (index, name, path).  Omitted: a new track is created
            (audio for clips, MIDI for Simpler), named ``track_name`` or after the file.
        mode: "session" (default), "arrangement", or "simpler" (= session +
            target "simpler").
        slot: session clip slot index or scene name (default: the highlighted
            slot when it is an empty slot of this track, else the first empty slot;
            a scene is added when the track is full).
        time: arrangement position in beats or "bars.beats.sixteenths" ("17.1.1");
            default: the playhead.
        target: session route — "auto" (audio track -> clip, MIDI track with a
            Drum Rack -> its next empty pad, other MIDI track -> Simpler), "clip",
            "simpler", "drum_pad" (a Simpler on a Drum Rack pad — the track's
            Drum Rack, inserted when the track has no instrument; a new MIDI
            track + Drum Rack when ``track`` is omitted) or "browser" (load the file's browser
            item; the file must be inside a folder Live's browser shows, and
            on audio tracks Live only places browser samples while the
            Session view is focused — otherwise ``invalid_state``).
        track_name: name for a newly created track.
        name: clip name (default: the file name).
        warp: set the clip's Warp switch (audio clips); null keeps Live's default.
        select: select the track (and highlight the slot) afterwards.
        overwrite: replace a clip already in ``slot`` / clear a filled pad
            that has no single Simpler (target "drum_pad").
        note: target "drum_pad" — the pad: 0..127, a note name in Live's
            numbering ("C1" = 36) or a pad / chain name ("Kick").  Default: the
            first empty pad from C1 up.  A pad holding one Simpler gets its
            sample replaced.

    Returns:
        {"route": "clip_slot.create_audio_clip" | "track.create_audio_clip" |
         "track.insert_device + simpler.replace_sample" | "simpler.replace_sample" |
         "browser.load_item" ..., "file": {path, name, size}, "track": {path, name, type},
         "created_track"?: true, "slot"?: index, "clip"?: summary, "device"?: summary,
         "created_scene"?: true, "pad"?: {note, key, name, chain (path)},
         "rack"?: summary (drum_pad), "notes"?: [...]}

    Gotchas:
        Audio clips need an audio track (MIDI tracks get a Simpler instead);
        drum pads need Live 12.3+ (``insert_chain``) and a top-level Drum Rack;
        return/master tracks cannot hold clips.  Live references the file where
        it is — collect-and-save copies it into the project.
    """
    if mode not in _MODES:
        raise BridgeError("bad_args", "mode must be one of %s" % ", ".join(_MODES))
    if target not in _TARGETS:
        raise BridgeError("bad_args", "target must be one of %s" % ", ".join(_TARGETS))
    if mode == "simpler":
        if target not in ("auto", "simpler"):
            raise BridgeError("bad_args", "mode='simpler' implies target='simpler'")
        mode, target = "session", "simpler"
    if mode == "arrangement" and target not in ("auto", "clip"):
        raise BridgeError("bad_args", "the arrangement only takes audio clips (target='clip')")
    if mode == "arrangement" and slot is not None:
        raise BridgeError("bad_args", "slot is for mode='session'; use time for the arrangement")
    if mode == "session" and time is not None:
        raise BridgeError("bad_args", "time is for mode='arrangement'; use slot for the session")
    if note is not None and target not in ("auto", "drum_pad"):
        raise BridgeError("bad_args", "note only applies to target='drum_pad'")
    if note is not None:
        target = "drum_pad"
    if target == "drum_pad" and slot is not None:
        raise BridgeError("bad_args", "slot does not apply to target='drum_pad' (use note)")
    file_path = require_file(file_path)
    song = ctx.song
    notes = []
    result = {"file": {"path": file_path, "name": os.path.basename(file_path),
                       "size": os.path.getsize(file_path)}}
    created = False
    if track is None:
        kind = "midi" if target in ("simpler", "drum_pad") else "audio"
        default_name = "Drum Rack" if target == "drum_pad" else _file_stem(file_path)
        track_obj = browser_handlers._create_track(ctx, kind, track_name or default_name)
        created = True
    else:
        if track_name is not None:
            raise BridgeError("bad_args", "track_name only applies when a new track is created "
                                          "(track omitted)")
        track_obj = ctx.track(track)
    kind = _track_kind(ctx, track_obj)
    if kind in ("return", "master"):
        raise BridgeError("invalid_state", "%s tracks cannot hold clips or instruments — pick "
                          "an audio or MIDI track" % kind)
    if created:
        result["created_track"] = True
    clip = None
    device = None

    if mode == "arrangement":
        if kind != "audio":
            raise BridgeError("invalid_state", "%r is a MIDI track — the arrangement needs an "
                              "audio track (or omit track to create one)"
                              % _get(track_obj, "name", ""))
        if not compat.has(track_obj, "create_audio_clip"):
            raise BridgeError("unsupported", "Track.create_audio_clip is not available in this "
                                             "Live (needs Live 12)")
        if time is None:
            position = float(_get(song, "current_song_time", 0.0) or 0.0)
        else:
            position = resolve.parse_time(song, time, "time")
        try:
            clip = track_obj.create_audio_clip(file_path, position)
        except Exception as error:
            raise _live_error("track.create_audio_clip", error)
        result["route"] = "track.create_audio_clip"
        result["time"] = position
    else:
        if target == "auto":
            if kind == "midi":
                has_rack = any(_is_drum_rack(d) for d in _get(track_obj, "devices", ()) or ())
                target = "drum_pad" if has_rack else "simpler"
            else:
                target = "clip"
        if target == "drum_pad":
            if kind != "midi":
                raise BridgeError("invalid_state", "%r is an audio track — a Drum Rack needs a "
                                  "MIDI track (or omit track to create one)"
                                  % _get(track_obj, "name", ""))
            rack, _created_rack = drum_rack_on(ctx, track_obj, notes)
            if note is not None:
                from . import racks as racks_handlers
                groups = racks_handlers._pad_groups(rack)[0]
                note = racks_handlers.parse_note(note, racks_handlers._pad_names(groups))
            else:
                note = first_empty_pad(rack)
                if note is None:
                    raise BridgeError("invalid_state", "every pad of %r is filled — pass "
                                      "note and overwrite=true" % _get(rack, "name", ""))
            route, device, chain = import_drum_pad(ctx, rack, note, file_path,
                                                   name or _file_stem(file_path), overwrite,
                                                   notes)
            result["route"] = route
            result["rack"] = ctx.summarize(rack, "minimal")
            result["pad"] = {"note": note, "key": _note_label(note),
                             "name": _get(chain, "name", ""),
                             "chain": ctx.path_of(chain) if chain is not None else None}
            name = None     # the chain carries the name; there is no clip
        elif target == "clip" and kind != "audio":
            raise BridgeError("invalid_state", "%r is a MIDI track — audio clips need an audio "
                              "track; use target='simpler' to play the file from a Simpler"
                              % _get(track_obj, "name", ""))
        elif target == "simpler":
            if kind != "midi":
                raise BridgeError("invalid_state", "%r is an audio track — a Simpler needs a "
                                  "MIDI track (or omit track to create one)"
                                  % _get(track_obj, "name", ""))
            if slot is not None:
                raise BridgeError("bad_args", "slot does not apply to target='simpler'")
            route, device = _import_simpler(ctx, track_obj, file_path, notes)
            result["route"] = route
        else:
            if target == "browser" and kind == "midi":
                # a sample loaded onto a MIDI track becomes a Simpler — no clip slot
                if slot is not None:
                    raise BridgeError("bad_args", "slot does not apply to a MIDI track (the "
                                      "browser loads the sample into a Simpler)")
                clip_slot = None
            else:
                if target == "browser" and _get(_get(ctx.app, "view"),
                                                "focused_document_view") == "Arranger":
                    raise BridgeError("invalid_state", "Live only places browser samples in "
                                      "clip slots while the Session view is focused (the "
                                      "Arrangement view is in front) — use target='clip' "
                                      "(works in any view) or show the Session view")
                clip_slot = _session_slot(ctx, track_obj, slot, overwrite, notes, result)
            if target == "clip" and compat.has(clip_slot, "create_audio_clip"):
                try:
                    clip = clip_slot.create_audio_clip(file_path)
                except Exception as error:
                    raise _live_error("clip_slot.create_audio_clip", error)
                result["route"] = "clip_slot.create_audio_clip"
            else:
                if target == "clip":
                    notes.append("ClipSlot.create_audio_clip is not available in this Live; "
                                 "used the browser instead")
                changes, entry = _import_browser(ctx, track_obj, file_path, clip_slot, notes)
                result["route"] = "browser.load_item"
                result["browser_item"] = entry.info("minimal")
                result.update(dict((k, v) for k, v in changes.items() if k != "track"))
                clip = _get(clip_slot, "clip") if clip_slot is not None else None
                if clip is None and not any(changes.get(k) for k in
                                            ("inserted", "changed", "clips", "new_tracks")):
                    raise BridgeError("invalid_state", "Live did not load %r through the "
                                      "browser: %s" % (os.path.basename(file_path),
                                                       "; ".join(notes) or "no change"))
    applied = _apply_clip_options(clip, name, warp)
    if applied:
        result["applied"] = applied
    result["track"] = ctx.summarize(track_obj, "minimal")
    if clip is not None:
        summary = ctx.summarize(clip, "minimal")
        if mode == "arrangement":
            summary["start_time"] = _get(clip, "start_time")
            summary["end_time"] = _get(clip, "end_time")
        result["clip"] = summary
    if device is not None:
        result["device"] = ctx.summarize(device, "minimal")
    if select:
        browser_handlers.select_track(ctx, track_obj)
        if mode == "session" and result.get("slot") is not None:
            browser_handlers.highlight_slot(ctx, list(track_obj.clip_slots)[result["slot"]])
    if notes:
        result["notes"] = notes
    return result


@command("samples.list", doc="List audio files in a folder (recursive, filtered, newest first)")
def samples_list(ctx, folder, pattern=None, extensions=None, recursive=True, sort="newest",
                 limit=50, offset=0, newer_than=None, max_age_s=None, max_scan=MAX_SCAN,
                 max_depth=None):
    """List audio files in a folder on the machine that runs Live.

    Args:
        folder: absolute folder path (``~`` and env vars expanded).
        pattern: glob(s) on the file name, case-insensitive: "*.wav", "*kick*",
            "*.wav,*.aif" (comma/semicolon/| separated) or a list.
        extensions: allowed extensions ("wav,aif" or a list; "*" = any file;
            "mid" for MIDI files).  Default: every audio format Live imports.
        recursive: include sub-folders (default true; hidden folders skipped).
        sort: "newest" (mtime, default), "added" (max of mtime/ctime — when the
            file arrived, even if a download kept an old mtime), "oldest", "name", "size".
        limit, offset: paging (limit 1..1000).
        newer_than: only files that arrived after this epoch time (seconds, the
            Live machine's clock — use the ``now`` of an earlier answer).
        max_age_s: only files that arrived within this many seconds (measured
            on the Live machine's clock; combines with ``newer_than``).
        max_scan: stop after this many directory entries (100..500000).
        max_depth: how many sub-folder levels to enter (0 = only ``folder``;
            default unlimited when ``recursive``).

    Returns:
        {"folder", "now", "total", "count", "offset", "scanned", "truncated",
         "files": [{"path", "name", "size", "mtime", "age_s"}], "next_offset"?}

    Gotchas:
        Partial downloads (.part, .crdownload, ...) and hidden files are skipped.
        ``truncated`` means the scan stopped at ``max_scan`` or 5 s before it
        reached every sub-folder — files may be missing (also the newest ones:
        the walk is in folder order, not by date); pick a narrower folder or
        pass ``newer_than``/``max_age_s``.  With ``newer_than``/``max_age_s``
        only folders whose own time changed since then are listed (adding a
        file updates its folder's time), so such scans stay fast on big
        libraries; a file overwritten in place under an old name is not seen.
    """
    root = require_dir(folder)
    if sort not in _SORTS:
        raise BridgeError("bad_args", "sort must be one of %s" % ", ".join(sorted(_SORTS)))
    limit = browser_handlers._check_int(limit, "limit", 1, 1000)
    offset = browser_handlers._check_int(offset, "offset", 0)
    max_scan = browser_handlers._check_int(max_scan, "max_scan", 100, 500000)
    if max_depth is not None:
        max_depth = browser_handlers._check_int(max_depth, "max_depth", 0, 64)
    if newer_than is not None:
        if isinstance(newer_than, bool):
            raise BridgeError("bad_args", "newer_than must be an epoch time in seconds")
        try:
            newer_than = float(newer_than)
        except (TypeError, ValueError):
            raise BridgeError("bad_args", "newer_than must be an epoch time in seconds")
    started = time.time()
    if max_age_s is not None:
        if isinstance(max_age_s, bool) or not isinstance(max_age_s, (int, float)) \
                or max_age_s <= 0:
            raise BridgeError("bad_args", "max_age_s must be a number of seconds > 0")
        cutoff = started - float(max_age_s)
        newer_than = cutoff if newer_than is None else max(newer_than, cutoff)
    dir_cache = _dir_cache_for(root) if newer_than is not None else None
    files, scanned, truncated = scan_folder(root, _patterns(pattern), _extensions(extensions),
                                            bool(recursive), max_scan, SCAN_SECONDS,
                                            newer_than, dir_cache, max_depth)
    key, reverse = _SORTS[sort]
    files.sort(key=key, reverse=reverse)
    now = time.time()
    page = files[offset:offset + limit]
    result = {"folder": root, "now": round(now, 3), "total": len(files), "count": len(page),
              "offset": offset, "scanned": scanned, "truncated": truncated,
              "files": [{"path": f["path"], "name": f["name"], "size": f["size"],
                         "mtime": round(f["mtime"], 3),
                         "age_s": int(max(0.0, now - f["added"]))} for f in page]}
    if offset + limit < len(files):
        result["next_offset"] = offset + limit
    return result


@command("samples.inspect", doc="Validate a path and read an audio file's size/format/duration")
def samples_inspect(ctx, file_path):
    """Check a path on the Live machine and describe the audio file there.

    Args:
        file_path: file (or folder) path; ``~``/env vars expanded, ``file://``
            accepted.

    Returns:
        {"input", "path", "ok", "exists", "is_file", "is_dir", "is_audio", "size"?,
         "mtime"?, "problems": [...], "hints": [...], "platform",
         "audio"?: {format, channels, sample_rate, bit_depth, frames, duration (s),
                    encoding?, bitrate_kbps?}}
        A missing or malformed path is reported (``ok`` false + ``problems``),
        not raised.

    Gotchas:
        WAV/AIFF/FLAC details are exact; MP3 duration is an estimate for VBR
        files without a Xing header; M4A/OGG/CAF report only the format.  A
        file whose header contradicts its extension (AIFF data named .wav) is
        reported as a problem — Live refuses to import it.
    """
    if not isinstance(file_path, str):
        raise BridgeError("bad_args", "file_path must be a string")
    report = check_path(file_path, "any")
    report.pop("well_formed", None)
    if report["is_file"]:
        try:
            report["mtime"] = round(os.path.getmtime(report["path"]), 3)
        except OSError:
            pass
        if report["is_audio"]:
            report["audio"] = audio_info(report["path"])
            mismatch = format_mismatch(report["path"])
            if mismatch:
                report["problems"].append(mismatch)
                report["ok"] = False
        else:
            report["hints"].append("not an audio file Live imports")
    return report


@command("samples.locations", doc="Known sample folders on the Live machine (Splice, User Library, ...)")
def samples_locations(ctx):
    """Where samples usually live on the machine that runs Live.

    Returns:
        {"platform", "home",
         "splice": [{path, exists}], "splice_folder": first existing Splice
           folder or null (``LIVEBRIDGE_SPLICE_DIR``, the Splice app's folder,
           Live's own Splice download folder),
         "user_library": [{path, exists}] (the one in Live's Library.cfg first),
         "core_library": {path, exists} | null (Live's factory content, found
           next to the running Live — e.g. ".../Core Library/Samples/One Shots/Drums"),
         "factory_packs": {path, exists},
         "downloads", "desktop": {path, exists} (first existing),
         "download_folders": [{path, exists}] (Downloads + Desktop candidates —
           where Splice MCP / web downloads usually get saved),
         "inbox": {path, exists} (where samples.receive stores uploaded files),
         "live_library_cfg": {cfg, user_library?, factory_packs?,
           splice_download_mode?, splice_download_path?} | null,
         "music": {path, exists}, "env": {LIVEBRIDGE_SPLICE_DIR?}}

    Gotchas:
        Splice's download folder is configurable in the Splice app (Preferences
        -> Splice folder); set LIVEBRIDGE_SPLICE_DIR or pass the folder
        explicitly when yours is elsewhere.  The User Library comes from Live's
        own ``Library.cfg`` (newest Live first, the running version preferred);
        on Windows the Documents/Desktop/Downloads known folders come from the
        registry, so OneDrive folder backup and moved folders are found.
    """
    home = os.path.expanduser("~")
    library = live_library_config(home=home, version=_running_version())
    splice = [_location(p) for p in splice_candidates(home=home, library=library)]
    factory_packs = factory_packs_candidates(home=home, library=library)
    user_library = user_library_candidates(home=home, library=library)
    downloads, desktop = download_folders(home=home)
    existing = [entry["path"] for entry in splice if entry["exists"]]
    result = {
        "platform": platform_label(),
        "home": home,
        "splice": splice,
        "splice_folder": existing[0] if existing else None,
        "user_library": [_location(p) for p in user_library],
        "core_library": _first_existing(core_library_candidates()),
        "factory_packs": _first_existing(factory_packs) or _location(factory_packs[0]),
        "downloads": _first_existing(downloads) or _location(downloads[0]),
        "desktop": _first_existing(desktop) or _location(desktop[0]),
        "download_folders": [_location(p) for p in downloads + desktop],
        "inbox": _location(inbox_folder(user_library, home)),
        "live_library_cfg": library or None,
        "music": _location(os.path.join(home, "Music")),
    }
    env = {}
    if os.environ.get("LIVEBRIDGE_SPLICE_DIR"):
        env["LIVEBRIDGE_SPLICE_DIR"] = os.environ["LIVEBRIDGE_SPLICE_DIR"]
    result["env"] = env
    return result


# --------------------------------------------------------------------------
# receiving files (LAN mode: the MCP server's machine -> the Live machine)
# --------------------------------------------------------------------------

#: File types ``samples.receive`` accepts: audio, MIDI, Live device/rack/clip
#: presets and common plug-in preset files — nothing executable.
RECEIVE_EXTENSIONS = AUDIO_EXTENSIONS + (
    ".mid", ".midi", ".adg", ".adv", ".alc", ".agr", ".vstpreset", ".fxp", ".fxb",
    ".aupreset", ".serumpreset", ".nmsv")

#: Largest file ``samples.receive`` accepts (bytes) and the largest decoded chunk.
MAX_RECEIVE_BYTES = 1024 * 1024 * 1024
MAX_RECEIVE_CHUNK = 8 * 1024 * 1024

#: Unfinished uploads older than this are deleted from the inbox.
STALE_PART_SECONDS = 24 * 3600.0

_UPLOAD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
_BAD_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def inbox_folder(user_libraries=None, home=None):
    """Where ``samples.receive`` stores files: ``<User Library>/Samples/LiveBridge``
    (the first existing User Library, so Live's browser shows the files too), else
    ``<home>/LiveBridge/Samples``."""
    home = home or os.path.expanduser("~")
    if user_libraries is None:
        user_libraries = user_library_candidates(
            home=home, library=live_library_config(home=home, version=_running_version()))
    for library in user_libraries:
        try:
            if os.path.isdir(library):
                return os.path.join(library, "Samples", "LiveBridge")
        except (OSError, ValueError):
            continue
    return os.path.join(home, "LiveBridge", "Samples")


def safe_file_name(name):
    """A plain file name (no folders) that is valid on Windows and macOS, or
    ``bad_args``."""
    if not isinstance(name, str) or not name.strip():
        raise BridgeError("bad_args", "name must be a file name like 'Kick 01.wav'")
    base = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    base = _BAD_NAME_CHARS.sub("_", base).rstrip(". ")
    if not base or base in (".", "..") or base.startswith("."):
        raise BridgeError("bad_args", "%r is not a usable file name" % name)
    stem, ext = os.path.splitext(base)
    if stem.upper() in ("CON", "PRN", "AUX", "NUL") or \
            re.match(r"^(COM|LPT)\d$", stem.upper()):
        base = "_" + base
    if not ext.lower() in RECEIVE_EXTENSIONS:
        raise BridgeError("bad_args", "%r: only audio, MIDI and preset files can be sent "
                          "(%s)" % (name, ", ".join(RECEIVE_EXTENSIONS)))
    return base[:200]


def _safe_subfolder(subfolder):
    if subfolder is None:
        return []
    if not isinstance(subfolder, str):
        raise BridgeError("bad_args", "subfolder must be a relative folder name like "
                          "'Splice/Pack A'")
    parts = []
    for part in subfolder.replace("\\", "/").split("/"):
        part = part.strip()
        if part == "..":
            raise BridgeError("bad_args", "subfolder must stay inside the inbox (no '..')")
        part = _BAD_NAME_CHARS.sub("_", part).rstrip(". ")
        if not part:
            continue
        if part.startswith("."):
            raise BridgeError("bad_args", "subfolder must stay inside the inbox (no '..' "
                              "or hidden folders)")
        parts.append(part[:100])
    if len(parts) > 8:
        raise BridgeError("bad_args", "subfolder is nested too deeply (max 8 levels)")
    return parts


def _unique_target(folder, name):
    """``folder/name``, or ``folder/<stem> (2)<ext>`` ... when that exists."""
    target = os.path.join(folder, name)
    if not os.path.exists(target):
        return target
    stem, ext = os.path.splitext(name)
    for number in range(2, 1000):
        candidate = os.path.join(folder, "%s (%d)%s" % (stem, number, ext))
        if not os.path.exists(candidate):
            return candidate
    raise BridgeError("invalid_state", "too many files named %r in %s" % (name, folder))


def _clean_stale_parts(folder, keep=None):
    now = time.time()
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return
    for entry in entries:
        if not entry.name.endswith(".part") or not entry.name.startswith(".") or \
                entry.path == keep:
            continue
        try:
            if now - entry.stat().st_mtime > STALE_PART_SECONDS:
                os.remove(entry.path)
        except OSError:
            continue


@command("samples.receive",
         doc="Receive a file in base64 chunks into the LiveBridge inbox on the Live machine")
def samples_receive(ctx, name, data, upload_id, total_size, offset=0, subfolder=None,
                    overwrite=False, sha256=None):
    """Write an uploaded file onto the machine that runs Live — how files from the MCP
    server's machine (a Splice download in LAN mode, a URL the MCP server fetched)
    reach Live.  Send the file in order, one chunk per call.

    Args:
        name: file name (folders are stripped; audio, MIDI and preset types only:
            .wav .aif .mp3 .flac ... .mid .adg .adv .alc .vstpreset .fxp ...).
        data: this chunk, base64 (at most 8 MiB decoded; "" for an empty file).
        upload_id: a random id (6-64 letters/digits/_/-) the sender keeps for
            every chunk of this file.
        total_size: the whole file's size in bytes (<= 1 GiB).
        offset: byte offset of this chunk — must equal what was received so far
            (0 restarts the upload).
        subfolder: optional relative folder inside the inbox ("Splice/Pack A").
        overwrite: replace a file of the same name (default: " (2)" is appended).
        sha256: optional hex digest of the whole file, checked at the end.

    Returns:
        Unfinished: {"done": false, "received": bytes so far, "total_size"}.
        Last chunk: {"done": true, "path" (absolute path on the Live machine — use it
        with samples.import), "name", "size", "folder", "renamed"?: true,
        "sha256"?, "audio"?: {format, duration, ...}}.

    Gotchas:
        The inbox is ``<User Library>/Samples/LiveBridge`` (Live's browser shows
        it), else ``~/LiveBridge/Samples``; see samples.locations ``inbox``.
        Unfinished uploads are hidden ``.part`` files (never listed by
        samples.list) and are deleted after 24 h.  Needs the token like every
        command in LAN mode.
    """
    import base64
    import binascii
    import hashlib
    base = safe_file_name(name)
    if not isinstance(upload_id, str) or not _UPLOAD_ID_RE.match(upload_id):
        raise BridgeError("bad_args", "upload_id must be 6-64 letters, digits, '_' or '-'")
    total_size = browser_handlers._check_int(total_size, "total_size", 0, MAX_RECEIVE_BYTES)
    offset = browser_handlers._check_int(offset, "offset", 0, MAX_RECEIVE_BYTES)
    if not isinstance(data, str):
        raise BridgeError("bad_args", "data must be a base64 string")
    try:
        chunk = base64.b64decode(data.encode("ascii"), validate=True)
    except (binascii.Error, ValueError, UnicodeEncodeError) as error:
        raise BridgeError("bad_args", "data is not valid base64: %s" % error)
    if len(chunk) > MAX_RECEIVE_CHUNK:
        raise BridgeError("bad_args", "a chunk may hold at most %d bytes (got %d)"
                          % (MAX_RECEIVE_CHUNK, len(chunk)))
    if offset + len(chunk) > total_size:
        raise BridgeError("bad_args", "chunk ends at byte %d, past total_size %d"
                          % (offset + len(chunk), total_size))
    folder = os.path.join(inbox_folder(), *_safe_subfolder(subfolder))
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as error:
        raise BridgeError("invalid_state", "cannot create the inbox folder %s: %s"
                          % (folder, error))
    part = os.path.join(folder, ".%s.%s.part" % (base, upload_id))
    try:
        current = os.path.getsize(part) if os.path.exists(part) else None
    except OSError:
        current = None
    if offset == 0:
        mode = "wb"
        _clean_stale_parts(folder, keep=part)
    elif current is None:
        raise BridgeError("invalid_state", "no upload %r of %r in progress — start again "
                          "with offset 0" % (upload_id, base))
    elif current != offset:
        raise BridgeError("invalid_state", "upload %r has %d bytes, but this chunk starts at "
                          "%d — resend from offset %d" % (upload_id, current, offset, current))
    else:
        mode = "ab"
    try:
        with open(part, mode) as handle:
            handle.write(chunk)
    except OSError as error:
        raise BridgeError("invalid_state", "cannot write %s: %s" % (part, error))
    received = offset + len(chunk)
    if received < total_size:
        return {"done": False, "received": received, "total_size": total_size}
    result = {"done": True, "name": base, "size": received, "folder": folder}
    if sha256 is not None:
        digest = hashlib.sha256()
        with open(part, "rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        if not isinstance(sha256, str) or digest.hexdigest() != sha256.strip().lower():
            try:
                os.remove(part)
            except OSError:
                pass
            raise BridgeError("invalid_state", "sha256 mismatch for %r — the upload was "
                              "discarded, send it again" % base)
        result["sha256"] = digest.hexdigest()
    target = os.path.join(folder, base) if overwrite else _unique_target(folder, base)
    try:
        os.replace(part, target)
    except OSError as error:
        raise BridgeError("invalid_state", "cannot move the upload to %s: %s"
                          % (target, error))
    if os.path.basename(target) != base:
        result["renamed"] = True
    result["name"] = os.path.basename(target)
    result["path"] = target
    if is_audio_path(target):
        info = audio_info(target)
        if info:
            result["audio"] = info
    return result


# --------------------------------------------------------------------------
# MIDI files (Standard MIDI File, stdlib parser)
# --------------------------------------------------------------------------

MIDI_EXTENSIONS = (".mid", ".midi", ".smf", ".kar")

#: Largest MIDI file parsed (bytes) and most notes imported into one clip.
MAX_MIDI_BYTES = 16 * 1024 * 1024
MAX_MIDI_NOTES = 20000


def _varlen(data, pos):
    value = 0
    for _step in range(4):
        if pos >= len(data):
            raise ValueError("truncated variable-length number")
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, pos
    raise ValueError("variable-length number longer than 4 bytes")


def parse_midi_file(data):
    """Parse a Standard MIDI File (format 0, 1 or 2) from ``data`` (bytes).

    Returns ``{"format", "ticks_per_beat", "tempo" (first BPM or None),
    "signature" ([num, den] or None), "tracks": [{"index", "name", "channels",
    "notes": [(pitch, start, duration, velocity)] (times in beats = quarter
    notes, sorted), "end" (beats)}]}``.  Raises ``ValueError`` for anything that
    is not a usable SMF (SMPTE time division included).
    """
    if data[:4] != b"MThd" or len(data) < 14:
        raise ValueError("not a Standard MIDI File (no MThd header)")
    header_len = struct.unpack(">I", data[4:8])[0]
    fmt, count, division = struct.unpack(">HHH", data[8:14])
    if division & 0x8000:
        raise ValueError("SMPTE time division is not supported (only ticks per beat)")
    if division == 0:
        raise ValueError("ticks per beat is 0")
    pos = 8 + header_len
    tracks = []
    tempo = None
    signature = None
    while pos + 8 <= len(data) and len(tracks) < max(count, 1) + 64:
        chunk_id = data[pos:pos + 4]
        length = struct.unpack(">I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + length]
        pos += 8 + length
        if chunk_id != b"MTrk":
            continue
        track = {"index": len(tracks), "name": "", "channels": set(), "notes": [], "end": 0.0}
        open_notes = {}
        tick = 0
        cursor = 0
        status = None
        while cursor < len(body):
            delta, cursor = _varlen(body, cursor)
            tick += delta
            if cursor >= len(body):
                break
            byte = body[cursor]
            if byte == 0xFF:
                if cursor + 2 > len(body):
                    break
                kind = body[cursor + 1]
                size, cursor = _varlen(body, cursor + 2)
                payload = body[cursor:cursor + size]
                cursor += size
                if kind == 0x2F:
                    break
                if kind == 0x03 and not track["name"]:
                    track["name"] = payload.decode("latin-1", "replace").strip("\x00 ").strip()
                elif kind == 0x51 and len(payload) == 3 and tempo is None:
                    micros = (payload[0] << 16) | (payload[1] << 8) | payload[2]
                    if micros:
                        tempo = round(60000000.0 / micros, 3)
                elif kind == 0x58 and len(payload) >= 2 and signature is None:
                    signature = [payload[0], 2 ** payload[1]]
                continue
            if byte in (0xF0, 0xF7):
                size, cursor = _varlen(body, cursor + 1)
                cursor += size
                status = None
                continue
            if byte & 0x80:
                status = byte
                cursor += 1
            elif status is None:
                raise ValueError("running status without a status byte")
            kind = status & 0xF0
            channel = status & 0x0F
            width = 1 if kind in (0xC0, 0xD0) else 2
            params = body[cursor:cursor + width]
            cursor += width
            if len(params) < width:
                break
            if kind == 0x90 and params[1] > 0:
                open_notes.setdefault((channel, params[0]), []).append((tick, params[1]))
                track["channels"].add(channel)
            elif kind == 0x80 or (kind == 0x90 and params[1] == 0):
                stack = open_notes.get((channel, params[0]))
                if stack:
                    start, velocity = stack.pop(0)
                    track["notes"].append((params[0], start, max(tick - start, 1), velocity))
        for (channel, pitch), stack in open_notes.items():
            for start, velocity in stack:
                track["notes"].append((pitch, start, max(tick - start, 1), velocity))
        track["notes"] = sorted((pitch, start / float(division), length / float(division),
                                 velocity) for pitch, start, length, velocity in track["notes"])
        track["end"] = tick / float(division)
        track["channels"] = sorted(c + 1 for c in track["channels"])
        tracks.append(track)
    if not tracks:
        raise ValueError("the file has no MIDI tracks (MTrk chunks)")
    return {"format": fmt, "ticks_per_beat": division, "tempo": tempo,
            "signature": signature, "tracks": tracks}


def require_midi_file(raw):
    """Validated absolute path of a MIDI file or ``bad_args``/``not_found``."""
    report = check_path(raw, "any")
    if not report["ok"]:
        _raise_for(report)
    path = report["path"]
    if not report["is_file"]:
        raise BridgeError("bad_args", "%r is not a file" % path)
    try:
        with open(path, "rb") as handle:
            head = handle.read(4)
    except OSError as error:
        raise BridgeError("invalid_state", "cannot read %r: %s" % (path, error))
    if head != b"MThd":
        raise BridgeError("bad_args", "%r is not a Standard MIDI File (.mid)"
                          % os.path.basename(path))
    return path


def _pick_midi_tracks(parsed, midi_track):
    tracks = [t for t in parsed["tracks"] if t["notes"]]
    if midi_track is None:
        return tracks
    if isinstance(midi_track, bool):
        raise BridgeError("bad_args", "midi_track must be an index or a track name")
    if isinstance(midi_track, int) or (isinstance(midi_track, str)
                                       and midi_track.strip().isdigit()):
        index = int(midi_track)
        for track in parsed["tracks"]:
            if track["index"] == index:
                return [track]
        raise BridgeError("not_found", "the MIDI file has no track %d (tracks 0..%d)"
                          % (index, len(parsed["tracks"]) - 1))
    wanted = str(midi_track).strip().lower()
    matches = [t for t in parsed["tracks"] if t["name"].lower() == wanted] or \
        [t for t in parsed["tracks"] if wanted and wanted in t["name"].lower()]
    if len(matches) == 1:
        return matches
    names = ", ".join("%d %r" % (t["index"], t["name"]) for t in parsed["tracks"][:16])
    raise BridgeError("bad_args" if matches else "not_found",
                      "midi_track %r %s (tracks: %s)"
                      % (midi_track, "is ambiguous" if matches else "not found", names))


def _clip_length(song, signature, end):
    """``end`` rounded up to whole bars (at least one bar)."""
    numerator, denominator = signature or (
        _get(song, "signature_numerator", 4) or 4, _get(song, "signature_denominator", 4) or 4)
    bar = float(numerator) * 4.0 / float(denominator or 4)
    if bar <= 0:
        bar = 4.0
    bars = max(1, int(-(-round(end, 6) // bar)))
    return bars * bar


def _add_midi_notes(clip, notes):
    spec = compat.live_enum("Clip.MidiNoteSpecification")
    if spec is None or not compat.has(clip, "add_new_notes"):
        raise BridgeError("unsupported", "this Live has no add_new_notes (Live 11+)")
    objects = [spec(pitch=int(p), start_time=float(s), duration=float(d),
                    velocity=float(max(1, min(127, v)))) for p, s, d, v in notes]
    try:
        clip.add_new_notes(tuple(objects))
    except Exception as error:
        raise _live_error("clip.add_new_notes", error)


@command("samples.import_midi", mutating=True,
         doc="Import a MIDI file (.mid) from disk as a MIDI clip (session slot or arrangement)")
def samples_import_midi(ctx, file_path, track=None, mode="session", slot=None, time=None,
                        midi_track=None, track_name=None, name=None, overwrite=False,
                        select=False):
    """Read a Standard MIDI File on the Live machine and write its notes into a new
    MIDI clip — for MIDI packs, Splice MIDI downloads or any .mid on disk (no need for
    the file to be in Live's browser).

    Args:
        file_path: absolute path of the .mid file on the machine running Live.
        track: target MIDI track (index, name, path).  Omitted: a new MIDI track
            named ``track_name`` or after the file.
        mode: "session" (default; a clip slot) or "arrangement".
        slot: session slot index or scene name (default: the highlighted empty
            slot of that track, else the first empty slot; a scene is added
            when the track is full).
        time: arrangement position in beats or "bars.beats.sixteenths"
            (default: the playhead).
        midi_track: which track of a multi-track file (index or name); default:
            every track with notes merged into one clip.
        track_name: name of a new track; name: clip name (default: the file
            name, or the MIDI track's name when one is picked).
        overwrite: replace a clip already in ``slot``.
        select: select the track (and highlight the slot) afterwards.

    Returns:
        {"route": "clip_slot.create_clip + add_new_notes" |
         "track.create_midi_clip + add_new_notes", "file": {path, name, size},
         "midi": {format, ticks_per_beat, tempo, signature, tracks: [{index,
         name, notes, channels}]}, "track", "created_track"?, "slot"?,
         "time"?, "clip": summary, "notes_added", "length" (beats),
         "notes"?: [...]}

    Gotchas:
        Times are in beats (quarter notes) — the file's own tempo is reported,
        not applied to the set.  The clip length is rounded up to whole bars
        (the file's time signature, else the set's).  Channels are merged;
        program changes, controllers and pitch bend are not imported.  At
        most 20000 notes.
    """
    if mode not in ("session", "arrangement"):
        raise BridgeError("bad_args", "mode must be 'session' or 'arrangement'")
    if mode == "arrangement" and slot is not None:
        raise BridgeError("bad_args", "slot is for mode='session'; use time for the arrangement")
    if mode == "session" and time is not None:
        raise BridgeError("bad_args", "time is for mode='arrangement'; use slot for the session")
    path = require_midi_file(file_path)
    size = os.path.getsize(path)
    if size > MAX_MIDI_BYTES:
        raise BridgeError("bad_args", "%r is too big for a MIDI file (%d bytes)"
                          % (os.path.basename(path), size))
    with open(path, "rb") as handle:
        data = handle.read()
    try:
        parsed = parse_midi_file(data)
    except (ValueError, struct.error, IndexError) as error:
        raise BridgeError("bad_args", "%r is not a readable MIDI file: %s"
                          % (os.path.basename(path), error))
    chosen = _pick_midi_tracks(parsed, midi_track)
    notes = sorted(n for t in chosen for n in t["notes"])
    if not notes:
        raise BridgeError("invalid_state", "%r has no notes%s" % (
            os.path.basename(path), "" if midi_track is None else " in that track"))
    if len(notes) > MAX_MIDI_NOTES:
        raise BridgeError("bad_args", "%r has %d notes — at most %d per clip (pick one "
                          "midi_track)" % (os.path.basename(path), len(notes), MAX_MIDI_NOTES))
    song = ctx.song
    messages = []
    result = {"file": {"path": path, "name": os.path.basename(path), "size": size},
              "midi": {"format": parsed["format"], "ticks_per_beat": parsed["ticks_per_beat"],
                       "tempo": parsed["tempo"], "signature": parsed["signature"],
                       "tracks": [{"index": t["index"], "name": t["name"],
                                   "notes": len(t["notes"]), "channels": t["channels"]}
                                  for t in parsed["tracks"]]}}
    if track is None:
        track_obj = browser_handlers._create_track(ctx, "midi", track_name or _file_stem(path))
        result["created_track"] = True
    else:
        if track_name is not None:
            raise BridgeError("bad_args", "track_name only applies when a new track is created "
                                          "(track omitted)")
        track_obj = ctx.track(track)
    if _track_kind(ctx, track_obj) != "midi":
        raise BridgeError("invalid_state", "%r is not a MIDI track — MIDI clips need a MIDI "
                          "track (or omit track to create one)" % _get(track_obj, "name", ""))
    end = max(start + length for _p, start, length, _v in notes)
    length = _clip_length(song, parsed["signature"], max(end, max(t["end"] for t in chosen)))
    if mode == "arrangement":
        if not compat.has(track_obj, "create_midi_clip"):
            raise BridgeError("unsupported", "Track.create_midi_clip needs Live 12")
        position = float(_get(song, "current_song_time", 0.0) or 0.0) if time is None \
            else resolve.parse_time(song, time, "time")
        try:
            clip = track_obj.create_midi_clip(position, length)
        except Exception as error:
            raise _live_error("track.create_midi_clip", error)
        result["route"] = "track.create_midi_clip + add_new_notes"
        result["time"] = position
    else:
        clip_slot = _session_slot(ctx, track_obj, slot, overwrite, messages, result)
        try:
            clip_slot.create_clip(length)
        except Exception as error:
            raise _live_error("clip_slot.create_clip", error)
        clip = _get(clip_slot, "clip")
        result["route"] = "clip_slot.create_clip + add_new_notes"
    if clip is None:
        raise BridgeError("internal", "the new MIDI clip did not appear")
    _add_midi_notes(clip, notes)
    clip_name = name or (chosen[0]["name"] if midi_track is not None and chosen[0]["name"]
                         else _file_stem(path))
    try:
        clip.name = str(clip_name)
    except Exception:
        pass
    result["track"] = ctx.summarize(track_obj, "minimal")
    summary = ctx.summarize(clip, "minimal")
    if mode == "arrangement":
        summary["start_time"] = _get(clip, "start_time")
        summary["end_time"] = _get(clip, "end_time")
    result["clip"] = summary
    result["notes_added"] = len(notes)
    result["length"] = length
    if select:
        browser_handlers.select_track(ctx, track_obj)
        if mode == "session" and result.get("slot") is not None:
            browser_handlers.highlight_slot(ctx, list(track_obj.clip_slots)[result["slot"]])
    if messages:
        result["notes"] = messages
    return result
