#!/usr/bin/env python3
"""LiveBridge installer for macOS and Windows (standard library only, Python 3.9+).

What it does -- every step is idempotent, so run it again after ``git pull`` to update:

1. Finds Ableton Live's ``Remote Scripts`` folder: ``--remote-scripts-dir``, else the User
   Library recorded in Live's ``Library.cfg`` (newest Live 12 first), else the default
   (macOS ``~/Music/Ableton/User Library/Remote Scripts``, Windows
   ``%USERPROFILE%\\Documents\\Ableton\\User Library\\Remote Scripts``).
2. Copies ``remote_script/LiveBridge`` there (old files and ``__pycache__`` are removed).
3. Writes the Remote Script's ``config.json`` (host, port, token, name, allow_eval, beacon).
   The token is kept across re-runs unless ``--token``/``--new-token`` is given.
   Settings you do not pass again (LAN mode, port, allow_eval, beacon) are kept from the
   installed config, so a plain re-run never silently changes them.
4. Writes ``~/.livebridge/config.json`` for the MCP server (host = 127.0.0.1, the Live machine
   given with ``--pair``, or the host already there -- e.g. persisted by ``live_connect``; same
   token).
5. Installs the MCP server (``pip install -e mcp_server``) into ``~/.livebridge/venv`` (or with
   ``--uv``, ``--python``, ``--no-venv``) and locates the ``livebridge-mcp`` executable.
6. Registers it in Claude Desktop (``claude_desktop_config.json``, merged -- other servers are
   never touched) and in Claude Code (``claude mcp add --scope user``), plus the official Splice
   MCP (``https://mcp.splice.com/mcp``) unless ``--no-splice``.
7. Installs the LiveBridge skill (``.claude/skills/livebridge``) for Claude Code in
   ``~/.claude/skills/livebridge`` and builds ``~/.livebridge/livebridge-skill.zip`` to upload
   in Claude Desktop (Settings -> Capabilities -> Skills), unless ``--no-skill``.
8. Writes ``~/.livebridge/install.json`` (what was installed where, used by ``uninstall.py``)
   and prints a checklist.

Examples::

    python installers/install.py                    # Live and Claude on this machine
    python installers/install.py --network          # also accept Claude from the LAN
    python installers/install.py --local            # back to this machine only (undo --network/--pair)
    python installers/install.py --pair 192.168.1.20 --token <token>   # Claude here, Live there
    python installers/install.py --dry-run          # print every action, write nothing
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import secrets
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

__version__ = "0.1.0"

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_SOURCE = REPO_ROOT / "remote_script" / "LiveBridge"
MCP_SOURCE = REPO_ROOT / "mcp_server"
SKILL_SOURCE = REPO_ROOT / ".claude" / "skills" / "livebridge"

SCRIPT_NAME = "LiveBridge"
MCP_NAME = "livebridge"
MCP_DIST = "livebridge-mcp"
#: The optional ``audio`` extra of mcp_server/pyproject.toml (live_audio_analyze).
AUDIO_PACKAGES = ("numpy>=1.24", "scipy>=1.10", "soundfile>=0.12", "pyloudnorm>=0.1.1")
MCP_MODULE = "livebridge_mcp"
SPLICE_NAME = "splice"
SPLICE_URL = "https://mcp.splice.com/mcp"
SKILL_NAME = "livebridge"
SKILL_ZIP_NAME = "livebridge-skill.zip"
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
DEFAULT_PORT = 9880
DEFAULT_BEACON_PORT = 9881
MIN_INSTALLER_PYTHON = (3, 9)
MIN_MCP_PYTHON = (3, 10)

#: Keys the Remote Script's config.py understands (``remote_script/LiveBridge/config.py``).
REMOTE_CONFIG_KEYS = (
    "host", "port", "token", "allow_eval", "beacon", "beacon_port", "name", "max_clients",
    "idle_timeout", "default_timeout", "max_timeout", "log_level",
)

#: Never copied into Live: caches, macOS litter and developer-local configs.
COPY_IGNORE = shutil.ignore_patterns(
    "__pycache__", "*.pyc", "*.pyo", ".DS_Store", "config.json", "config.local.json")

_TOKEN_RE = re.compile(r"^[A-Za-z0-9._~+/=-]{8,256}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9._:%\[\]-]{1,253}$")
_LIVE_PREFS_DIR_RE = re.compile(r"^Live (\d+)\.(\d+)(?:\.(\d+))?")
_VERSION_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")

#: Prints the version and ``json.dumps(sys.executable)``: ASCII-safe, so a path with characters
#: outside the Windows ANSI code page (a user folder named with a Polish L-stroke, say) cannot
#: crash the probe.
_PROBE_CODE = ("import sys, json; print('%d.%d.%d' % tuple(sys.version_info[:3])); "
               "print(json.dumps(sys.executable))")
_SCRIPTS_CODE = (
    "import json, sysconfig\n"
    "paths = [sysconfig.get_path('scripts')]\n"
    "for scheme in ('nt_user', 'posix_user', 'osx_framework_user'):\n"
    "    try:\n"
    "        paths.append(sysconfig.get_path('scripts', scheme))\n"
    "    except KeyError:\n"
    "        pass\n"
    "print(json.dumps(paths))\n"
)


class InstallError(Exception):
    """A fatal problem: printed as ``ERROR: <message>`` and the installer exits with 1."""


class ConfigError(Exception):
    """A JSON config file exists but cannot be read/parsed (it is then never overwritten)."""


# ---------------------------------------------------------------------------
# running commands
# ---------------------------------------------------------------------------

class CommandResult(object):
    """Outcome of one external command (``returncode`` 127 = not found, 124 = timed out)."""

    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout or ""
        self.stderr = stderr or ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def output(self) -> str:
        return (self.stdout + ("\n" if self.stdout and self.stderr else "") + self.stderr).strip()


def run_command(argv: Sequence[str], timeout: float = 900.0) -> CommandResult:
    """Run ``argv`` (no shell) and capture its output. Never raises.

    Child Pythons get ``PYTHONIOENCODING=utf-8``: with a piped stdout, Windows Pythons before
    3.15 would otherwise write in the ANSI code page and fail on characters outside it.
    """
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(
            [str(a) for a in argv], capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, env=env)
    except FileNotFoundError as error:
        return CommandResult(127, "", str(error))
    except subprocess.TimeoutExpired:
        return CommandResult(124, "", "timed out after %g s" % timeout)
    except OSError as error:
        return CommandResult(126, "", str(error))
    return CommandResult(proc.returncode, proc.stdout, proc.stderr)


def tail(text: str, lines: int = 15) -> str:
    """The last ``lines`` lines of ``text``, indented for the installer output."""
    rows = [row for row in (text or "").strip().splitlines() if row.strip()]
    return "\n".join("      " + row for row in rows[-lines:])


# ---------------------------------------------------------------------------
# the machine we install on
# ---------------------------------------------------------------------------

def _default_home(platform: str, environ: Dict[str, str]) -> Path:
    if platform.startswith("win"):
        if environ.get("USERPROFILE"):
            return Path(environ["USERPROFILE"])
        if environ.get("HOMEDRIVE") and environ.get("HOMEPATH"):
            return Path(environ["HOMEDRIVE"] + environ["HOMEPATH"])
    elif environ.get("HOME"):
        return Path(environ["HOME"])
    return Path.home()


def _hostname() -> str:
    try:
        name = socket.gethostname()
    except OSError:
        name = ""
    if name.endswith(".local"):
        name = name[: -len(".local")]
    return name or "Live"


class Host(object):
    """Everything OS-specific the installer needs, injectable for tests.

    Args:
        platform: ``sys.platform`` style name (``"darwin"``, ``"win32"``, ``"linux"``).
        home: the user's home folder (Windows: ``%USERPROFILE%``).
        environ: environment mapping (``APPDATA``, ``LOCALAPPDATA``, ``OneDrive``, ...). On
            Windows the keys are upper-cased like ``os.environ`` does there; always look values
            up with :meth:`env`, which is case-insensitive on Windows.
        hostname: machine name used in discovery beacons.
        which: ``shutil.which``-like lookup for ``claude``, ``npx``, ``uv``, pythons.
    """

    def __init__(self, platform: Optional[str] = None, home: Optional[os.PathLike] = None,
                 environ: Optional[Dict[str, str]] = None, hostname: Optional[str] = None,
                 which: Optional[Callable[[str], Optional[str]]] = None) -> None:
        self.platform = platform or sys.platform
        self.environ = dict(os.environ if environ is None else environ)
        if self.is_windows:
            self.environ = {key.upper(): value for key, value in self.environ.items()}
        self.home = Path(home) if home is not None else _default_home(self.platform, self.environ)
        self.hostname = hostname or _hostname()
        self._which = which or shutil.which

    @property
    def is_windows(self) -> bool:
        return self.platform.startswith("win")

    @property
    def is_mac(self) -> bool:
        return self.platform == "darwin"

    @property
    def os_label(self) -> str:
        return "Windows" if self.is_windows else "macOS" if self.is_mac else self.platform

    def env(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """An environment variable (case-insensitive on Windows: ``OneDrive`` = ``ONEDRIVE``)."""
        return self.environ.get(key.upper() if self.is_windows else key, default)

    def which(self, name: str) -> Optional[str]:
        """Full path of an executable on PATH, or None."""
        try:
            return self._which(name)
        except Exception:  # a broken PATH entry must not kill the installer
            return None

    def appdata(self) -> Path:
        """``%APPDATA%`` (Roaming)."""
        value = self.env("APPDATA")
        return Path(value) if value else self.home / "AppData" / "Roaming"

    def localappdata(self) -> Path:
        """``%LOCALAPPDATA%``."""
        value = self.env("LOCALAPPDATA")
        return Path(value) if value else self.home / "AppData" / "Local"

    def exe(self, name: str) -> str:
        return name + ".exe" if self.is_windows else name

    def venv_bin(self, venv: Path) -> Path:
        return venv / ("Scripts" if self.is_windows else "bin")

    def venv_python(self, venv: Path) -> Path:
        return self.venv_bin(venv) / self.exe("python")

    def quote(self, argv: Sequence[str]) -> str:
        """A copy-pasteable command line for this OS."""
        argv = [str(a) for a in argv]
        if self.is_windows:
            return subprocess.list2cmdline(argv)
        return shlex.join(argv)


# ---------------------------------------------------------------------------
# where things live
# ---------------------------------------------------------------------------

def livebridge_dir(host: Host) -> Path:
    """``~/.livebridge`` (Windows ``%USERPROFILE%\\.livebridge``)."""
    return host.home / ".livebridge"


def user_config_path(host: Host) -> Path:
    """The MCP server's config file; honours ``LIVEBRIDGE_CONFIG`` like the server does."""
    override = host.env("LIVEBRIDGE_CONFIG")
    if override:
        return Path(os.path.expanduser(override))
    return livebridge_dir(host) / "config.json"


def manifest_path(host: Host) -> Path:
    """``~/.livebridge/install.json`` -- what the last install did (read by uninstall.py)."""
    return livebridge_dir(host) / "install.json"


def default_venv_dir(host: Host) -> Path:
    return livebridge_dir(host) / "venv"


def claude_skills_dir(host: Host) -> Path:
    """Claude Code's personal skills folder: ``$CLAUDE_CONFIG_DIR/skills`` or ``~/.claude/skills``."""
    override = host.env("CLAUDE_CONFIG_DIR")
    base = Path(os.path.expanduser(override)) if override else host.home / ".claude"
    return base / "skills"


def skill_zip_path(host: Host) -> Path:
    """``~/.livebridge/livebridge-skill.zip`` -- the skill packaged for Claude Desktop's upload."""
    return livebridge_dir(host) / SKILL_ZIP_NAME


def is_livebridge_skill(folder: Path) -> bool:
    """True when ``folder`` holds the LiveBridge skill (``SKILL.md`` with ``name: livebridge``)."""
    try:
        text = (folder / "SKILL.md").read_text(encoding="utf-8-sig")
    except OSError:
        return False
    return re.search(r"(?m)^name:\s*%s\s*$" % re.escape(SKILL_NAME), text) is not None


def live_preferences_root(host: Host) -> Optional[Path]:
    """Folder holding one ``Live x.y.z`` preferences folder per installed Live version."""
    if host.is_mac:
        return host.home / "Library" / "Preferences" / "Ableton"
    if host.is_windows:
        return host.appdata() / "Ableton"
    return None


def library_cfg_files(host: Host) -> List[Tuple[Tuple[int, int, int], Path]]:
    """Every Live ``Library.cfg``, newest Live 12+ first.

    macOS: ``~/Library/Preferences/Ableton/Live 12.4.5/Library.cfg``;
    Windows: ``%APPDATA%\\Ableton\\Live 12.4.5\\Preferences\\Library.cfg``.
    """
    root = live_preferences_root(host)
    if root is None or not root.is_dir():
        return []
    found = []
    try:
        children = list(root.iterdir())
    except OSError:
        return []
    for child in children:
        match = _LIVE_PREFS_DIR_RE.match(child.name)
        if not match or not child.is_dir():
            continue
        version = (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))
        cfg = child / "Library.cfg" if host.is_mac else child / "Preferences" / "Library.cfg"
        if cfg.is_file():
            found.append((version, cfg))
    found.sort(key=lambda item: (item[0][0] >= 12, item[0]), reverse=True)
    return found


def user_library_from_cfg(path: Path) -> Optional[Path]:
    """The User Library folder recorded in a ``Library.cfg`` (XML), or None.

    Live stores ``<UserLibrary><LibraryProject><ProjectPath Value="/Users/me/Music/Ableton"/>
    <ProjectName Value="User Library"/>`` -- the library is ProjectPath/ProjectName.
    """
    try:
        root = ET.parse(str(path)).getroot()
    except (ET.ParseError, OSError):
        return None
    for library in root.iter("UserLibrary"):
        for project in library.iter("LibraryProject"):
            path_node = project.find("ProjectPath")
            name_node = project.find("ProjectName")
            base = path_node.get("Value", "") if path_node is not None else ""
            name = name_node.get("Value", "") if name_node is not None else ""
            if base:
                return Path(base) / (name or "User Library")
    return None


def default_user_libraries(host: Host) -> List[Path]:
    """Live's default User Library locations for this OS (OneDrive-redirected ones too)."""
    if host.is_mac:
        return [host.home / "Music" / "Ableton" / "User Library"]
    if host.is_windows:
        candidates = [host.home / "Documents" / "Ableton" / "User Library"]
        for key in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
            value = host.env(key)
            if value:
                candidates.append(Path(value) / "Documents" / "Ableton" / "User Library")
        candidates.append(host.home / "OneDrive" / "Documents" / "Ableton" / "User Library")
        unique: List[Path] = []
        for candidate in candidates:
            if candidate not in unique:
                unique.append(candidate)
        return unique
    return []


def find_remote_scripts_dir(host: Host, explicit: Optional[str] = None
                            ) -> Tuple[Optional[Path], str, List[str]]:
    """Locate Live's ``Remote Scripts`` folder.

    Returns:
        ``(path or None, how it was found, notes)``. ``None`` only on an OS without Live
        (Linux) when no ``explicit`` folder is given.
    """
    notes: List[str] = []
    if explicit:
        path = Path(os.path.expanduser(str(explicit)))
        if path.name == SCRIPT_NAME:  # forgive pointing at .../Remote Scripts/LiveBridge
            path = path.parent
        return path, "--remote-scripts-dir", notes
    for version, cfg in library_cfg_files(host):
        library = user_library_from_cfg(cfg)
        label = "Live %d.%d.%d" % version
        if library is None:
            notes.append("could not read the User Library location from %s" % cfg)
            continue
        if library.is_dir() or library.parent.is_dir():
            return library / "Remote Scripts", "%s preferences (%s)" % (label, cfg), notes
        notes.append("%s says the User Library is %s, which does not exist (unplugged drive?)"
                     % (cfg, library))
    defaults = default_user_libraries(host)
    for library in defaults:
        if library.is_dir():
            return library / "Remote Scripts", "default User Library location", notes
    if defaults:
        notes.append("Live's User Library was not found -- using the default %s. Start Live "
                     "once, or pass --remote-scripts-dir if you moved the User Library."
                     % defaults[0])
        return defaults[0] / "Remote Scripts", "default location (not created by Live yet)", notes
    notes.append("Ableton Live does not run on %s; pass --remote-scripts-dir to install the "
                 "Remote Script anyway." % host.os_label)
    return None, "", notes


def desktop_config_candidates(host: Host) -> List[Path]:
    """``claude_desktop_config.json`` paths of every Claude Desktop install that exists.

    macOS ``~/Library/Application Support/Claude``; Windows ``%APPDATA%\\Claude`` and the
    Microsoft Store (MSIX) copy under ``%LOCALAPPDATA%\\Packages\\Claude_*\\LocalCache\\Roaming\\Claude``;
    Linux ``~/.config/Claude``. Folders that do not exist (Claude Desktop never started) are skipped.
    """
    folders: List[Path] = []
    if host.is_mac:
        folders.append(host.home / "Library" / "Application Support" / "Claude")
    elif host.is_windows:
        folders.append(host.appdata() / "Claude")
        packages = host.localappdata() / "Packages"
        if packages.is_dir():
            try:
                for package in sorted(packages.iterdir()):
                    if package.name.startswith("Claude_"):
                        folders.append(package / "LocalCache" / "Roaming" / "Claude")
            except OSError:
                pass
    else:
        folders.append(host.home / ".config" / "Claude")
    return [folder / "claude_desktop_config.json" for folder in folders if folder.is_dir()]


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def new_token() -> str:
    """A fresh 128-bit random token (32 hex characters)."""
    return secrets.token_hex(16)


def read_json(path: Path) -> Dict[str, Any]:
    """Read a JSON object; ``{}`` when the file is missing or empty.

    Raises:
        ConfigError: the file exists but is unreadable, not JSON, or not an object.
    """
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8-sig")  # tolerate a BOM (Windows Notepad)
    except OSError as error:
        raise ConfigError("cannot read %s (%s)" % (path, error))
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError as error:
        raise ConfigError("%s is not valid JSON (%s)" % (path, error))
    if not isinstance(data, dict):
        raise ConfigError("%s does not contain a JSON object" % path)
    return data


def _make_writable_and_retry(func: Callable, path: str, _exc: Any) -> None:
    """rmtree error handler: clear the read-only flag (Windows) and retry once.

    ``shutil.rmtree`` reports "this is a symlink/junction" through ``func = os.path.islink``;
    that is re-raised, so rmtree never silently deletes nothing (use :func:`remove_link`).
    """
    if func is os.path.islink or getattr(func, "__name__", "") == "islink":
        error = _exc[1] if isinstance(_exc, tuple) else _exc
        if isinstance(error, BaseException):
            raise error
        raise OSError("%s is a symlink or junction -- not removed" % path)
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    func(path)


def is_link_or_junction(path: Path) -> bool:
    """True for a symlink and for a Windows directory junction (``mklink /J``).

    ``Path.is_symlink()`` is False for junctions, the usual no-admin developer link on Windows.
    """
    try:
        if path.is_symlink():
            return True
    except OSError:
        return False
    isjunction = getattr(os.path, "isjunction", None)  # Python 3.12+
    if isjunction is not None:
        try:
            if isjunction(str(path)):
                return True
        except OSError:
            pass
    if os.name == "nt":
        try:
            info = os.lstat(str(path))
        except OSError:
            return False
        mount_point = getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)
        return getattr(info, "st_reparse_tag", 0) == mount_point
    return False


def remove_link(path: Path) -> None:
    """Remove a symlink or junction itself -- never what it points to."""
    try:
        os.unlink(str(path))
    except OSError:
        os.rmdir(str(path))  # Windows directory symlinks and junctions


def remove_tree(path: Path) -> None:
    """``shutil.rmtree`` that also deletes read-only files (Windows)."""
    if sys.version_info >= (3, 12):
        shutil.rmtree(str(path), onexc=_make_writable_and_retry)
    else:
        shutil.rmtree(str(path), onerror=_make_writable_and_retry)


def tree_files(root: Path) -> Dict[str, bytes]:
    """``{relative posix path: bytes}`` of every file below ``root`` (caches skipped)."""
    result: Dict[str, bytes] = {}
    if not root.is_dir():
        return result
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part == "__pycache__" or part == ".DS_Store" for part in rel.parts):
            continue
        if path.is_file() and path.suffix not in (".pyc", ".pyo"):
            result[rel.as_posix()] = path.read_bytes()
    return result


def zip_bytes(files: Dict[str, bytes], prefix: str) -> bytes:
    """A deterministic zip (sorted names, fixed timestamps) of ``files`` under ``prefix/``."""
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo("%s/%s" % (prefix, name), date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, files[name])
    return buffer.getvalue()


def purge_pycache(root: Path) -> None:
    """Delete every ``__pycache__`` folder below ``root``."""
    for cache in sorted(root.rglob("__pycache__"), reverse=True):
        if cache.is_dir():
            remove_tree(cache)


def local_ip() -> Optional[str]:
    """This machine's LAN address (no packet is sent), or None."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))  # TEST-NET: only selects the outgoing interface
        address = sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()
    return None if address.startswith("127.") or address == "0.0.0.0" else address


def _arrow() -> str:
    """``→`` when the console can print it, else ``->`` (Windows cp1252 consoles)."""
    encoding = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        "\u2192".encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return "->"
    return "\u2192"


def control_surface_hint(arrow: str = "->") -> str:
    """The exact Live menu path the user must click."""
    return ("Live {a} Preferences {a} Link, Tempo & MIDI {a} Control Surface {a} LiveBridge "
            "(Input/Output: None)").format(a=arrow)


class PythonInfo(object):
    """A probed Python interpreter."""

    def __init__(self, executable: str, version: Tuple[int, int, int]) -> None:
        self.executable = executable
        self.version = version

    @property
    def label(self) -> str:
        return "%d.%d.%d" % self.version


# ---------------------------------------------------------------------------
# dry-run aware operations (shared with uninstall.py)
# ---------------------------------------------------------------------------

class Ops(object):
    """Output + file/command operations that honour ``--dry-run``.

    In dry-run mode every write/delete/external command is only printed (prefixed
    ``[dry-run]``); read-only probes (``python --version``-style) still run.
    """

    def __init__(self, host: Host, dry_run: bool = False,
                 runner: Callable[..., CommandResult] = run_command,
                 out: Optional[Callable[[str], None]] = None) -> None:
        self.host = host
        self.dry = bool(dry_run)
        self.runner = runner
        self.out = out or (lambda line: print(line, flush=True))
        self.warnings: List[str] = []
        self.manual: List[str] = []
        self.arrow = _arrow() if out is None else "->"

    # -- output ------------------------------------------------------------
    def step(self, title: str) -> None:
        self.out("")
        self.out("==> " + title)

    def info(self, message: str) -> None:
        for index, line in enumerate(str(message).splitlines() or [""]):
            self.out(("    " if index == 0 else "      ") + line)

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        self.info("WARNING: " + message)

    def action(self, message: str) -> None:
        self.info(("[dry-run] " if self.dry else "") + message)

    # -- files -------------------------------------------------------------
    def ensure_dir(self, path: Path) -> None:
        if path.is_dir():
            return
        self.action("create folder %s" % path)
        if not self.dry:
            path.mkdir(parents=True, exist_ok=True)

    def write_json(self, path: Path, data: Dict[str, Any], secret: bool = False,
                   backup: bool = False) -> bool:
        """Write ``data`` atomically. Returns False when the file already had this content.

        Args:
            secret: chmod 600 on POSIX (the file holds the token).
            backup: keep the previous content as ``<name>.livebridge-backup``.
        """
        old_text = None
        if path.exists():
            try:
                old_text = path.read_text(encoding="utf-8-sig")
                if json.loads(old_text) == data:
                    self.info("unchanged: %s" % path)
                    return False
            except (OSError, ValueError):
                pass
        self.action("write %s" % path)
        if self.dry:
            return True
        path.parent.mkdir(parents=True, exist_ok=True)
        if backup and old_text is not None:
            backup_path = path.with_name(path.name + ".livebridge-backup")
            backup_path.write_text(old_text, encoding="utf-8")
            self.info("backup of the previous file: %s" % backup_path)
        temp = path.with_name(path.name + ".livebridge-tmp")
        temp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if secret and os.name != "nt":
            os.chmod(str(temp), 0o600)
        os.replace(str(temp), str(path))
        return True

    def write_bytes(self, path: Path, data: bytes) -> bool:
        """Write a binary file atomically. Returns False when it already had this content."""
        if path.is_file():
            try:
                if path.read_bytes() == data:
                    self.info("unchanged: %s" % path)
                    return False
            except OSError:
                pass
        self.action("write %s" % path)
        if self.dry:
            return True
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".livebridge-tmp")
        temp.write_bytes(data)
        os.replace(str(temp), str(path))
        return True

    def delete_tree(self, path: Path) -> None:
        self.action("remove %s" % path)
        if not self.dry:
            remove_tree(path)

    def delete_file(self, path: Path) -> None:
        self.action("remove %s" % path)
        if not self.dry:
            path.unlink()

    # -- commands ----------------------------------------------------------
    def run(self, argv: Sequence[str], readonly: bool = False, timeout: float = 900.0,
            quiet: bool = False) -> CommandResult:
        """Run an external command; in dry-run only print it (unless ``readonly``)."""
        argv = [str(a) for a in argv]
        if self.dry and not readonly:
            self.action("run: " + self.host.quote(argv))
            return CommandResult(0)
        if not quiet:
            self.info("$ " + self.host.quote(argv))
        return self.runner(argv, timeout=timeout)

    def probe_python(self, argv: Sequence[str]) -> Optional[PythonInfo]:
        """Version + real executable of a Python interpreter, or None if it does not run."""
        result = self.run(list(argv) + ["-c", _PROBE_CODE], readonly=True, timeout=30.0,
                          quiet=True)
        if not result.ok:
            return None
        lines = result.stdout.strip().splitlines()
        match = _VERSION_RE.search(lines[0]) if lines else None
        if not match:
            return None
        version = (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))
        executable = lines[1].strip() if len(lines) > 1 else ""
        if executable.startswith('"'):
            try:
                executable = str(json.loads(executable))
            except ValueError:
                pass
        return PythonInfo(executable or str(argv[0]), version)


# ---------------------------------------------------------------------------
# the installer
# ---------------------------------------------------------------------------

class Installer(Ops):
    """Runs the install steps for parsed ``args`` (see :func:`build_parser`)."""

    def __init__(self, args: argparse.Namespace, host: Host,
                 runner: Callable[..., CommandResult] = run_command,
                 out: Optional[Callable[[str], None]] = None) -> None:
        super().__init__(host, args.dry_run, runner, out)
        self.args = args
        self.token = ""
        self.token_source = ""
        self.script_dir: Optional[Path] = None
        self.mcp_command: Optional[Tuple[str, List[str]]] = None
        #: Effective settings (see :meth:`resolve_settings`); ``kept`` lists what came from
        #: the previous install instead of a flag or a default.
        self.settings: Dict[str, Any] = {
            "host": "127.0.0.1", "network": False, "port": DEFAULT_PORT, "allow_eval": True,
            "beacon": False, "mcp_host": "127.0.0.1", "mcp_port": DEFAULT_PORT, "kept": []}
        self.previous_manifest: Dict[str, Any] = {}
        self.manifest: Dict[str, Any] = {
            "installer": __version__,
            "repo": str(REPO_ROOT),
            "platform": host.platform,
            "network": False,
            "pair": args.pair,
            "port": DEFAULT_PORT,
            "mcp_host": None,
            "user_config": str(user_config_path(host)),
            "remote_script_dir": None,
            "remote_script_symlink": False,
            "venv": None,
            "venv_created": False,
            "uv": False,
            "python": None,
            "mcp_command": None,
            "mcp_args": [],
            "desktop_configs": [],
            "splice_desktop_configs": [],
            "claude_code": False,
            "splice_claude_code": False,
            "splice_claude_code_added": False,
            "skill_dir": None,
            "skill_zip": None,
        }

    # -- main flow ---------------------------------------------------------
    def execute(self) -> int:
        """Run every enabled step. Returns the process exit code."""
        self.validate()
        try:
            self.previous_manifest = read_json(manifest_path(self.host))
        except ConfigError:
            self.previous_manifest = {}
        self.header()
        remote_scripts = None
        if self.args.remote_script:
            remote_scripts = self.locate_remote_scripts()
        self.resolve_token(remote_scripts)
        self.resolve_settings(remote_scripts)
        if remote_scripts is not None:
            self.install_remote_script(remote_scripts)
        self.write_user_config()
        if self.args.mcp:
            self.mcp_command = self.install_mcp_server()
            if self.args.desktop:
                self.register_claude_desktop()
            if self.args.claude_code:
                self.register_claude_code()
            if self.args.skill:
                self.install_skill()
        self.write_manifest()
        self.checklist()
        return 0

    def validate(self) -> None:
        args = self.args
        if args.port is not None and not 1 <= args.port <= 65535:
            raise InstallError("--port must be between 1 and 65535 (got %d)" % args.port)
        if args.token is not None and not _TOKEN_RE.match(args.token):
            raise InstallError("--token must be 8-256 characters of letters, digits or "
                               "._~+/=- (no spaces or quotes)")
        if args.local and (args.network or args.pair):
            raise InstallError("--local (this machine only) contradicts --network / --pair")
        if args.local:
            args.network = False
        if args.pair is not None:
            args.pair = args.pair.strip()
            if not args.pair or not _HOST_RE.match(args.pair) or "://" in args.pair:
                raise InstallError("--pair takes a host name or IP address, e.g. "
                                   "--pair 192.168.1.20")
            if not args.token:
                raise InstallError(
                    "--pair needs --token: use the token the installer printed on the Live "
                    "machine (it is also in Remote Scripts/LiveBridge/config.json there)")
        if args.no_venv and args.venv_dir:
            raise InstallError("--no-venv and --venv-dir contradict each other")
        if args.remote_script and not (SCRIPT_SOURCE / "__init__.py").is_file():
            raise InstallError("%s is missing -- run the installer from a complete LiveBridge "
                               "checkout" % SCRIPT_SOURCE)
        if args.mcp and not (MCP_SOURCE / "pyproject.toml").is_file():
            raise InstallError("%s is missing -- run the installer from a complete LiveBridge "
                               "checkout" % (MCP_SOURCE / "pyproject.toml"))

    def header(self) -> None:
        self.out("LiveBridge installer %s -- %s, Python %s%s" % (
            __version__, self.host.os_label, ".".join(map(str, sys.version_info[:3])),
            " -- DRY RUN, nothing is written" if self.dry else ""))
        self.info("repository: %s" % REPO_ROOT)
        previous_repo = self.previous_manifest.get("repo")
        if previous_repo and previous_repo != str(REPO_ROOT):
            self.info("the previous install came from %s -- LiveBridge now runs from this folder "
                      "(keep it where it is: the MCP server is an editable install)"
                      % previous_repo)

    # -- remote script -----------------------------------------------------
    def locate_remote_scripts(self) -> Optional[Path]:
        self.step("Locating Ableton Live's Remote Scripts folder")
        path, how, notes = find_remote_scripts_dir(self.host, self.args.remote_scripts_dir)
        for note in notes:
            self.info(note)
        if path is None:
            self.warn("skipping the Remote Script (no Live on %s). Use --pair HOST to control "
                      "Live on another machine." % self.host.os_label)
            return None
        self.info("%s  (%s)" % (path, how))
        return path

    def resolve_token(self, remote_scripts: Optional[Path]) -> None:
        """--token > --new-token > token already installed > a new random one.

        An installed token is always kept (a re-run must never break the paired machine), even
        when it would not pass ``--token`` validation -- that only earns a warning.
        """
        if self.args.token:
            self.token, self.token_source = self.args.token, "--token"
            return
        if not self.args.new_token:
            candidates = []
            if remote_scripts is not None:
                candidates.append(remote_scripts / SCRIPT_NAME / "config.json")
            candidates.append(user_config_path(self.host))
            for path in candidates:
                try:
                    token = read_json(path).get("token")
                except ConfigError:
                    continue
                if isinstance(token, str) and token.strip():
                    self.token, self.token_source = token, "kept from %s" % path
                    if not _TOKEN_RE.match(token):
                        self.warn("the installed token in %s is unusual (under 8 characters or "
                                  "with spaces/quotes) -- kept so the paired machine keeps "
                                  "working; run with --new-token to replace it" % path)
                    return
        self.token = new_token()
        self.token_source = "newly generated"

    def resolve_settings(self, remote_scripts: Optional[Path]) -> None:
        """Work out every setting: explicit flag > the installed config > the default.

        Remote Script (``Remote Scripts/LiveBridge/config.json``): ``host`` (``--network`` =
        0.0.0.0, ``--no-network``/``--local`` = 127.0.0.1), ``port``, ``allow_eval`` and
        ``beacon``. MCP server (``~/.livebridge/config.json``): ``host`` (``--pair`` >
        ``--local`` > the host already there, e.g. persisted by ``live_connect`` > 127.0.0.1)
        and ``port``. A re-run without flags therefore changes nothing that was set before.
        """
        args = self.args
        existing: Dict[str, Any] = {}
        if remote_scripts is not None:
            try:
                existing = read_json(remote_scripts / SCRIPT_NAME / "config.json")
            except ConfigError:
                existing = {}
        try:
            user = read_json(user_config_path(self.host))
        except ConfigError:
            user = {}
        kept: List[str] = []

        old_host = existing.get("host")
        if args.network is not None:
            host = "0.0.0.0" if args.network else "127.0.0.1"
        elif isinstance(old_host, str) and old_host.strip():
            host = old_host.strip()
            kept.append("LAN mode" if host not in LOCAL_HOSTS else "localhost mode")
        else:
            host = "127.0.0.1"
        network = host not in LOCAL_HOSTS

        if args.port is not None:
            port = args.port
        elif _valid_port(existing.get("port")):
            port = int(existing["port"])
            kept.append("port")
        else:
            port = DEFAULT_PORT

        if args.allow_eval is not None:
            allow_eval = bool(args.allow_eval)
        elif isinstance(existing.get("allow_eval"), bool):
            allow_eval = existing["allow_eval"]
            kept.append("allow_eval")
        else:
            allow_eval = True

        if args.beacon is not None:
            beacon = bool(args.beacon)
        elif args.network is not None:
            beacon = bool(args.network)
        elif isinstance(existing.get("beacon"), bool):
            beacon = existing["beacon"]
            kept.append("beacon")
        else:
            beacon = network

        old_mcp_host = user.get("host")
        if args.pair:
            mcp_host = args.pair
        elif args.local:
            mcp_host = "127.0.0.1"
        elif isinstance(old_mcp_host, str) and old_mcp_host.strip():
            mcp_host = old_mcp_host.strip()
            if mcp_host not in LOCAL_HOSTS:
                kept.append("MCP host %s" % mcp_host)
        else:
            mcp_host = "127.0.0.1"
        if args.port is not None:
            mcp_port = args.port
        elif mcp_host in LOCAL_HOSTS and remote_scripts is not None:
            mcp_port = port  # the Live on this machine
        elif _valid_port(user.get("port")) and (not args.pair or old_mcp_host == args.pair):
            mcp_port = int(user["port"])
            if mcp_port != DEFAULT_PORT:
                kept.append("MCP port")
        else:
            mcp_port = DEFAULT_PORT

        self.settings = {"host": host, "network": network, "port": port,
                         "allow_eval": allow_eval, "beacon": beacon, "mcp_host": mcp_host,
                         "mcp_port": mcp_port, "kept": kept}
        self.manifest.update({"network": network, "port": port, "mcp_host": mcp_host})
        self.step("Settings")
        if remote_scripts is not None:
            self.info("Live Remote Script: %s, port %d, allow_eval %s, beacon %s" % (
                "LAN mode (%s)" % host if network else "this machine only (%s)" % host,
                port, "on" if allow_eval else "off", "on" if beacon else "off"))
        self.info("MCP server connects to %s:%d" % (mcp_host, mcp_port))
        if kept:
            self.info("kept from the previous install: %s (pass --network/--no-network, --local, "
                      "--port, --allow-eval/--no-allow-eval, --beacon/--no-beacon or --pair to "
                      "change)" % ", ".join(kept))

    def install_remote_script(self, remote_scripts: Path) -> None:
        self.step("Installing the LiveBridge Remote Script")
        dest = remote_scripts / SCRIPT_NAME
        config_path = dest / "config.json"
        try:
            existing = read_json(config_path)
        except ConfigError as error:
            self.warn("%s -- it will be replaced" % error)
            existing = {}
        self.ensure_dir(remote_scripts)
        self.copy_script(SCRIPT_SOURCE, dest)

        settings = self.settings
        values = dict(existing)
        values.update({
            "host": settings["host"],
            "port": settings["port"],
            "token": self.token,
            "allow_eval": settings["allow_eval"],
            "beacon": settings["beacon"],
            "beacon_port": existing.get("beacon_port", DEFAULT_BEACON_PORT),
            "name": self.args.name or existing.get("name") or self.host.hostname,
        })
        unknown = sorted(key for key in values if key not in REMOTE_CONFIG_KEYS)
        if unknown:
            self.info("keeping unknown config keys %s (the Remote Script ignores them)"
                      % ", ".join(unknown))
        self.write_json(config_path, values, secret=True)
        self.info("token: %s" % self.token_source)
        self.script_dir = dest
        self.manifest["remote_script_dir"] = str(dest)

    def copy_script(self, source: Path, dest: Path) -> None:
        """Replace ``dest`` with a clean copy of ``source`` (no caches, no configs).

        A symlink or Windows junction at ``dest`` (developer setup) is left alone.
        """
        if is_link_or_junction(dest):
            self.warn("%s is a symlink/junction (developer setup) -- left as it is; only its "
                      "config.json is updated" % dest)
            self.manifest["remote_script_symlink"] = True
            return
        if dest.exists() and not dest.is_dir():
            raise InstallError("%s exists and is not a folder -- remove it and run again" % dest)
        if dest.exists():
            self.action("replace %s with %s (old files and __pycache__ removed)" % (dest, source))
        else:
            self.action("copy %s -> %s" % (source, dest))
        if self.dry:
            return
        if dest.exists():
            try:
                remove_tree(dest)
            except OSError as error:
                self.warn("could not remove the old %s (%s) -- Live may hold a file open. "
                          "Copied over it instead; restart Live afterwards." % (dest, error))
                shutil.copytree(str(source), str(dest), ignore=COPY_IGNORE, dirs_exist_ok=True)
                purge_pycache(dest)
                return
        shutil.copytree(str(source), str(dest), ignore=COPY_IGNORE)
        if not (dest / "__init__.py").is_file():
            raise InstallError("copying to %s failed (no __init__.py afterwards)" % dest)

    # -- MCP side config -----------------------------------------------------
    def write_user_config(self) -> None:
        self.step("Writing the MCP server settings")
        path = user_config_path(self.host)
        try:
            data = read_json(path)
        except ConfigError as error:
            self.warn("%s -- it will be replaced" % error)
            data = {}
        data.update({"host": self.settings["mcp_host"], "port": self.settings["mcp_port"],
                     "token": self.token})
        self.write_json(path, data, secret=True)
        self.info("the MCP server connects to %s:%d" % (data["host"], data["port"]))

    # -- MCP server ----------------------------------------------------------
    def install_mcp_server(self) -> Tuple[str, List[str]]:
        self.step("Installing the LiveBridge MCP server (%s)" % MCP_DIST)
        args = self.args
        uv = None
        if args.uv:
            uv = self.host.which("uv")
            if not uv:
                raise InstallError("--uv was given but `uv` is not on PATH -- install it "
                                   "(https://docs.astral.sh/uv/) or drop --uv")
        elif self.previous_manifest.get("uv") and not args.no_venv:
            uv = self.host.which("uv")
            if uv:
                self.info("using uv again, like the previous install (--uv)")
        self.manifest["uv"] = bool(uv)
        venv: Optional[Path] = None
        if not args.no_venv:
            venv = Path(os.path.expanduser(args.venv_dir)) if args.venv_dir \
                else default_venv_dir(self.host)
            python = self.ensure_venv(venv, uv)
        else:
            info = self.find_python(args.python)
            python = Path(info.executable)
            self.info("installing into %s (Python %s)" % (python, info.label))
        self.manifest["venv"] = str(venv) if venv else None
        self.manifest["python"] = str(python)
        if args.no_pip:
            self.info("--no-pip: using the livebridge-mcp that is already installed")
        else:
            self.pip_install(python, uv)
        command, command_args = self.locate_mcp(python, venv)
        if not self.dry:
            check = self.run([command] + command_args + ["--version"], readonly=True,
                             timeout=60.0, quiet=True)
            if check.ok:
                self.info("%s works (%s)" % (command, check.output.splitlines()[-1]
                                             if check.output else "ok"))
            else:
                self.warn("`%s --version` failed:\n%s" % (
                    self.host.quote([command] + command_args), tail(check.output)))
        self.manifest["mcp_command"] = command
        self.manifest["mcp_args"] = command_args
        return command, command_args

    def find_python(self, explicit: Optional[str] = None,
                    required: bool = True) -> Optional[PythonInfo]:
        """Find a Python >= 3.10 for the MCP server (explicit path, this one, PATH, py launcher)."""
        candidates: List[List[str]] = []
        if explicit:
            candidates.append([os.path.expanduser(explicit)])
        else:
            if sys.version_info[:2] >= MIN_MCP_PYTHON and sys.executable:
                candidates.append([sys.executable])
            for name in ("python3.14", "python3.13", "python3.12", "python3.11", "python3.10"):
                found = self.host.which(name)
                if found:
                    candidates.append([found])
            if self.host.is_windows:
                launcher = self.host.which("py")
                if launcher:
                    candidates.append([launcher, "-3"])
            for name in ("python3", "python"):
                found = self.host.which(name)
                if found:
                    candidates.append([found])
            if self.host.is_mac:
                for fixed in ("/opt/homebrew/bin/python3", "/usr/local/bin/python3",
                              "/Library/Frameworks/Python.framework/Versions/Current/bin/python3"):
                    if os.path.exists(fixed):
                        candidates.append([fixed])
        tried: List[str] = []
        seen = set()
        last: Optional[PythonInfo] = None
        for argv in candidates:
            key = tuple(argv)
            if key in seen:
                continue
            seen.add(key)
            last = info = self.probe_python(argv)
            if info is not None and info.version[:2] >= MIN_MCP_PYTHON:
                return info
            tried.append("%s (%s)" % (" ".join(argv), info.label if info else "does not run"))
        if explicit:
            raise InstallError("--python %s: %s -- the MCP server needs Python 3.10 or newer"
                               % (explicit, "Python %s" % last.label if last else
                                  "could not run it"))
        if not required:
            return None
        raise InstallError(
            "no Python 3.10+ found (tried: %s). Install Python 3.12+ from "
            "https://www.python.org/downloads/ (Windows: tick 'Add python.exe to PATH'; macOS: "
            "or `brew install python`), or install uv and re-run with --uv, or pass "
            "--python PATH." % (", ".join(tried) or "nothing on PATH"))

    def _venv_is_ours(self, venv: Path) -> bool:
        """The default venv, or one an earlier run of this installer created."""
        if venv == default_venv_dir(self.host):
            return True
        previous = self.previous_manifest
        return bool(previous.get("venv_created")) and previous.get("venv") == str(venv)

    def ensure_venv(self, venv: Path, uv: Optional[str]) -> Path:
        """Create (or reuse) the virtual environment; returns its python.

        An unusable venv (Python < 3.10, or its python does not run) is only deleted and
        recreated when the installer owns it (see :meth:`_venv_is_ours`); a foreign
        ``--venv-dir`` is an error instead.
        """
        python = self.host.venv_python(venv)
        problem = None
        if python.exists():
            info = self.probe_python([str(python)])
            if info is not None and info.version[:2] >= MIN_MCP_PYTHON:
                self.info("reusing %s (Python %s)" % (venv, info.label))
                return python
            problem = "Python %s" % info.label if info else "its python does not run"
        if venv.exists():
            if not (venv / "pyvenv.cfg").is_file():
                raise InstallError("%s exists but is not a virtual environment -- remove it or "
                                   "pass --venv-dir" % venv)
            if not self._venv_is_ours(venv):
                raise InstallError(
                    "%s is not usable for the MCP server (%s) and was not created by the "
                    "LiveBridge installer -- it is left alone. Fix or remove it, or pass another "
                    "--venv-dir." % (venv, problem or "no python in it"))
            self.info("%s is unusable (%s) -- recreating it" % (venv, problem or "no python"))
            try:
                self.delete_tree(venv)
            except OSError as error:
                raise InstallError(
                    "could not remove the old virtual environment %s (%s). It is probably in "
                    "use: quit Claude Desktop (tray/menu bar icon -> Quit) and every Claude Code "
                    "session that uses LiveBridge, then run the installer again." % (venv, error))
        self.ensure_dir(venv.parent)
        if uv:
            base = self.find_python(self.args.python, required=False)
            # --seed: put pip into the venv, so a later run without uv can still pip install.
            argv = [uv, "venv", "--seed", "--python", base.executable if base else ">=3.10",
                    str(venv)]
        else:
            base = self.find_python(self.args.python)
            argv = [base.executable, "-m", "venv", str(venv)]
        if base is not None:
            self.info("using Python %s (%s)" % (base.label, base.executable))
        result = self.run(argv, timeout=600.0)
        if not result.ok:
            raise InstallError("creating the virtual environment %s failed (exit %d):\n%s"
                               % (venv, result.returncode, tail(result.output)))
        self.manifest["venv_created"] = True
        return python

    def pip_install(self, python: Path, uv: Optional[str]) -> None:
        """``pip install -e mcp_server`` (or ``uv pip install``).

        Without uv, a venv that has no pip (created by ``uv venv`` without ``--seed``) is
        handled: ``uv pip install`` when uv is on PATH, else ``python -m ensurepip`` first.
        """
        if not uv and not self.dry and python.exists():
            probe = self.run([str(python), "-m", "pip", "--version"], readonly=True,
                             timeout=120.0, quiet=True)
            if not probe.ok:
                uv = self.host.which("uv")
                if uv:
                    self.info("%s has no pip (a uv-made environment) -- installing with uv"
                              % python)
                else:
                    self.info("%s has no pip -- adding it with ensurepip" % python)
                    seeded = self.run([str(python), "-m", "ensurepip", "--upgrade"],
                                      timeout=600.0)
                    if not seeded.ok:
                        raise InstallError(
                            "%s has no pip and `ensurepip` failed (exit %d):\n%s\n      Install "
                            "uv (https://docs.astral.sh/uv/) and re-run with --uv, or remove the "
                            "virtual environment so the installer recreates it."
                            % (python, seeded.returncode, tail(seeded.output)))
        if uv:
            argv = [uv, "pip", "install", "--python", str(python), "-e", str(MCP_SOURCE)]
        else:
            argv = [str(python), "-m", "pip", "install", "--disable-pip-version-check",
                    "-e", str(MCP_SOURCE)]
        result = self.run(argv, timeout=1800.0)
        if not result.ok:
            hint = ""
            if "externally-managed-environment" in result.output:
                hint = (" -- this Python is managed by the OS/Homebrew (PEP 668); drop "
                        "--no-venv so the installer uses its own virtual environment")
            raise InstallError("installing the MCP server failed (exit %d)%s:\n%s"
                               % (result.returncode, hint, tail(result.output)))
        self.info("installed %s (editable) from %s" % (MCP_DIST, MCP_SOURCE))
        if getattr(self.args, "no_audio", False):
            self.info("--no-audio: live_audio_analyze stays off until its libraries are added")
            return
        # Optional: the libraries behind live_audio_analyze. Never fatal -- the server runs
        # without them and the tool answers with the pip command.
        audio = self.run(argv[:-2] + list(AUDIO_PACKAGES), timeout=1800.0)
        if audio.ok:
            self.info("installed the audio analysis libraries (%s)" % ", ".join(AUDIO_PACKAGES))
        else:
            self.warn("the audio analysis libraries could not be installed (%s) -- everything "
                      "else works; live_audio_analyze will say how to add them"
                      % ", ".join(AUDIO_PACKAGES))

    def locate_mcp(self, python: Path, venv: Optional[Path]) -> Tuple[str, List[str]]:
        """Path of the ``livebridge-mcp`` executable, or ``python -m livebridge_mcp`` as fallback."""
        name = self.host.exe(MCP_DIST)
        candidates: List[Path] = []
        if venv is not None:
            candidates.append(self.host.venv_bin(venv) / name)
            if self.dry:
                return str(candidates[0]), []
        else:
            result = self.run([str(python), "-c", _SCRIPTS_CODE], readonly=True, timeout=30.0,
                              quiet=True)
            if result.ok:
                try:
                    for folder in json.loads(result.stdout.strip().splitlines()[-1]):
                        if folder:
                            candidates.append(Path(folder) / name)
                except (ValueError, IndexError):
                    pass
            on_path = self.host.which(MCP_DIST)
            if on_path:
                candidates.append(Path(on_path))
        for candidate in candidates:
            if candidate.is_file():
                self.info("MCP server executable: %s" % candidate)
                return str(candidate), []
        if not self.dry:
            self.warn("the %s executable was not found; registering `%s -m %s` instead"
                      % (MCP_DIST, python, MCP_MODULE))
        return str(python), ["-m", MCP_MODULE]

    # -- Claude registration -------------------------------------------------
    def register_claude_desktop(self) -> None:
        self.step("Registering with Claude Desktop")
        command, command_args = self.mcp_command or ("", [])
        if self.args.desktop_config:
            paths = [Path(os.path.expanduser(self.args.desktop_config))]
        else:
            paths = desktop_config_candidates(self.host)
        if not paths:
            self.info("Claude Desktop not found (it creates its settings folder on first start) "
                      "-- skipped. Re-run the installer after installing Claude Desktop, or pass "
                      "--desktop-config PATH.")
            return
        entry = {"command": command, "args": command_args,
                 "env": {"LIVEBRIDGE_CONFIG": str(user_config_path(self.host))}}
        splice_entry: Optional[Dict[str, Any]] = None
        splice_checked = False
        for path in paths:
            try:
                data = read_json(path)
            except ConfigError as error:
                self.warn("%s -- NOT modified. Fix it, then add this under \"mcpServers\":\n%s"
                          % (error, json.dumps({MCP_NAME: entry}, indent=2)))
                continue
            servers = data.get("mcpServers", {})
            if not isinstance(servers, dict):
                self.warn("%s: \"mcpServers\" is not an object -- NOT modified" % path)
                continue
            servers = dict(servers)
            servers[MCP_NAME] = entry
            if self.args.splice:
                if SPLICE_NAME in servers:
                    self.info("a \"splice\" server is already configured -- left unchanged")
                else:
                    if not splice_checked:
                        splice_entry, splice_checked = self.splice_desktop_entry(), True
                    if splice_entry is not None:
                        servers[SPLICE_NAME] = splice_entry
                        self.manifest["splice_desktop_configs"].append(str(path))
            updated = dict(data)
            updated["mcpServers"] = servers
            self.write_json(path, updated, backup=True)
            self.manifest["desktop_configs"].append(str(path))
        self.manual.append("Quit Claude Desktop completely (tray/menu bar icon {a} Quit) and start "
                           "it again so it loads LiveBridge.".format(a=self.arrow))

    def splice_desktop_entry(self) -> Optional[Dict[str, Any]]:
        """Claude Desktop entry bridging the remote Splice MCP through ``npx mcp-remote``."""
        connector = ("Splice in Claude Desktop: Settings {a} Connectors {a} Add custom connector "
                     "{a} URL %s, then sign in with your Splice account." % SPLICE_URL
                     ).format(a=self.arrow)
        npx = self.host.which("npx")
        if not npx:
            self.info("npx (Node.js) not found -- Splice is not added to the JSON config.")
            self.manual.append(connector)
            return None
        self.manual.append("Splice in Claude Desktop starts through npx mcp-remote and opens a "
                           "browser for the Splice login on first use. (Alternative: %s)"
                           % connector)
        if self.host.is_windows:
            return {"command": "cmd", "args": ["/c", "npx", "-y", "mcp-remote", SPLICE_URL]}
        # GUI apps on macOS do not inherit the shell PATH (nvm/Homebrew node), so pin it.
        node_dir = str(Path(npx).parent)
        path = ":".join([node_dir, "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"])
        return {"command": npx, "args": ["-y", "mcp-remote", SPLICE_URL], "env": {"PATH": path}}

    def register_claude_code(self) -> None:
        self.step("Registering with Claude Code")
        command, command_args = self.mcp_command or ("", [])
        add = ["claude", "mcp", "add", "--scope", "user", MCP_NAME, "--", command] + command_args
        splice_add = ["claude", "mcp", "add", "--transport", "http", "--scope", "user",
                      SPLICE_NAME, SPLICE_URL]
        claude = self.host.which("claude")
        if not claude:
            self.info("the Claude Code CLI (`claude`) is not on PATH -- run these yourself once "
                      "it is installed:")
            self.info("  " + self.host.quote(add))
            self.manual.append("Register with Claude Code: " + self.host.quote(add))
            if self.args.splice:
                self.info("  " + self.host.quote(splice_add))
                self.manual.append("Add Splice to Claude Code: " + self.host.quote(splice_add))
            return
        # `claude mcp add` refuses an existing name, so replace our own entry.
        self.run([claude, "mcp", "remove", "--scope", "user", MCP_NAME], timeout=120.0)
        result = self.run([claude] + add[1:], timeout=120.0)
        if result.ok:
            self.info("registered `%s` (user scope)" % MCP_NAME)
            self.manifest["claude_code"] = True
        else:
            self.warn("`claude mcp add` failed (exit %d):\n%s\n      Run it yourself: %s"
                      % (result.returncode, tail(result.output), self.host.quote(add)))
            self.manual.append("Register with Claude Code: " + self.host.quote(add))
        if not self.args.splice:
            return
        if not self.dry:
            existing = self.run([claude, "mcp", "get", SPLICE_NAME], readonly=True,
                                timeout=120.0, quiet=True)
            if existing.ok:
                self.info("`%s` is already registered in Claude Code -- left unchanged"
                          % SPLICE_NAME)
                self.manifest["splice_claude_code"] = True
                # Only an entry this installer added may be removed by uninstall --remove-splice.
                self.manifest["splice_claude_code_added"] = bool(
                    self.previous_manifest.get("splice_claude_code_added"))
                return
        result = self.run([claude] + splice_add[1:], timeout=120.0)
        if result.ok:
            self.info("registered `%s` -> %s" % (SPLICE_NAME, SPLICE_URL))
            self.manifest["splice_claude_code"] = True
            self.manifest["splice_claude_code_added"] = True
            self.manual.append("In Claude Code run /mcp, pick `splice` and sign in with your "
                               "Splice account.")
        else:
            self.warn("adding Splice to Claude Code failed (exit %d):\n%s"
                      % (result.returncode, tail(result.output)))
            self.manual.append("Add Splice to Claude Code: " + self.host.quote(splice_add))

    # -- skill ---------------------------------------------------------------
    def install_skill(self) -> None:
        """Deploy the LiveBridge skill for Claude Code and package it for Claude Desktop.

        Claude Code: a copy in ``~/.claude/skills/livebridge`` (``$CLAUDE_CONFIG_DIR/skills``),
        so the workflow is active outside this repository too. A link there (developer setup)
        or a foreign folder of that name is left alone. Claude Desktop cannot read that folder:
        it gets ``~/.livebridge/livebridge-skill.zip`` plus the upload step in the checklist.
        """
        self.step("Installing the LiveBridge skill (production workflow for Claude)")
        source_files = tree_files(SKILL_SOURCE)
        if "SKILL.md" not in source_files:
            self.warn("%s is missing -- the skill is not installed" % (SKILL_SOURCE / "SKILL.md"))
            return
        dest = claude_skills_dir(self.host) / SKILL_NAME
        if is_link_or_junction(dest):
            self.info("%s is a symlink/junction (developer setup) -- left as it is" % dest)
        elif dest.exists() and not (dest.is_dir() and is_livebridge_skill(dest)):
            self.warn("%s exists but is not the LiveBridge skill -- left alone (pass --no-skill "
                      "to silence this)" % dest)
        elif dest.is_dir() and tree_files(dest) == source_files:
            self.info("unchanged: %s" % dest)
            self.manifest["skill_dir"] = str(dest)
        else:
            self.action("%s Claude Code skill %s" % ("update" if dest.exists() else "install",
                                                     dest))
            if not self.dry:
                try:
                    if dest.exists():
                        remove_tree(dest)
                    shutil.copytree(str(SKILL_SOURCE), str(dest), ignore=COPY_IGNORE)
                except OSError as error:
                    self.warn("could not copy the skill to %s (%s)" % (dest, error))
                    dest = None  # type: ignore[assignment]
            if dest is not None:
                self.manifest["skill_dir"] = str(dest)
        zip_path = skill_zip_path(self.host)
        self.write_bytes(zip_path, zip_bytes(source_files, SKILL_NAME))
        self.manifest["skill_zip"] = str(zip_path)
        self.manual.append(
            "Claude Desktop skill (optional, recommended): Settings {a} Capabilities {a} Skills "
            "{a} Upload skill {a} {zip} (Skills need 'Code execution and file creation' turned "
            "on). Claude Code already has it in {dest}.".format(
                a=self.arrow, zip=zip_path, dest=claude_skills_dir(self.host) / SKILL_NAME))

    # -- bookkeeping ---------------------------------------------------------
    def write_manifest(self) -> None:
        self.step("Recording the installation")
        path = manifest_path(self.host)
        previous = self.previous_manifest
        data = dict(self.manifest)
        for key in ("desktop_configs", "splice_desktop_configs"):
            merged = list(previous.get(key) or [])
            for item in data[key]:
                if item not in merged:
                    merged.append(item)
            data[key] = merged
        if previous.get("venv") == data.get("venv") and previous.get("venv_created"):
            data["venv_created"] = True
        # A partial run (--no-remote-script, --no-mcp, --no-skill) keeps what an earlier run
        # installed, so uninstall.py still finds it.
        for key in ("remote_script_dir", "mcp_command", "mcp_args", "venv", "python",
                    "skill_dir", "skill_zip"):
            if not data.get(key) and previous.get(key):
                data[key] = previous[key]
        if not self.args.mcp:
            for key in ("uv", "splice_claude_code", "splice_claude_code_added", "claude_code"):
                if previous.get(key):
                    data[key] = previous[key]
        data["installed_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        self.write_json(path, data)

    def checklist(self) -> None:
        args = self.args
        settings = self.settings
        a = self.arrow
        line = "=" * 72
        self.out("")
        self.out(line)
        self.out(" LiveBridge %s -- next steps" % ("dry run finished" if self.dry else "installed"))
        self.out(line)
        number = 1
        if self.script_dir is not None:
            self.out(" %d. Start (or restart) Ableton Live 12 -- it only scans Remote Scripts at "
                     "startup." % number)
            number += 1
            self.out(" %d. %s" % (number, control_surface_hint(a)))
            self.out("    The status bar then shows \"LiveBridge ... ready on port %d\"."
                     % settings["port"])
            number += 1
        for item in self.manual:
            self.out(" %d. %s" % (number, item))
            number += 1
        self.out(" %d. Verify: ask Claude to \"run live_status\" (expect connected: true), or run"
                 % number)
        self.out("    %s" % self.host.quote(
            [Path(sys.executable).name if not self.host.is_windows else "py",
             str(REPO_ROOT / "tests" / "integration_check.py")]))
        self.out("")
        self.out(" Token: %s   (%s)" % (self.token, self.token_source))
        if self.script_dir is not None:
            self.out("   Remote Script config: %s" % (self.script_dir / "config.json"))
        self.out("   MCP server config:    %s" % user_config_path(self.host))
        if settings["kept"]:
            self.out("   Kept from the previous install: %s" % ", ".join(settings["kept"]))
        if settings["network"] and self.script_dir is not None:
            ip = local_ip() or "<this machine's IP>"
            self.out("")
            self.out(" LAN mode: Live accepts connections on port %d from other machines."
                     % settings["port"])
            self.out("   On the machine that runs Claude:")
            self.out("     python installers/install.py --pair %s --port %d --token %s"
                     % (ip, settings["port"], self.token))
            for hint in live_machine_network_hints(self.host, settings["port"], a):
                self.out("   " + hint)
        if settings["mcp_host"] not in LOCAL_HOSTS:
            self.out("")
            if args.pair:
                self.out(" Paired: Claude here controls Live at %s:%d. That machine must be "
                         "installed with --network and the same token."
                         % (settings["mcp_host"], settings["mcp_port"]))
            else:
                self.out(" Claude here controls Live at %s:%d (kept from the previous install; "
                         "--local points it back at this machine)."
                         % (settings["mcp_host"], settings["mcp_port"]))
            self.out("   Switch at runtime with live_discover / live_connect (docs/NETWORK.md).")
            for hint in claude_machine_network_hints(self.host, a):
                self.out("   " + hint)
        if self.warnings:
            self.out("")
            self.out(" %d warning(s) -- see above:" % len(self.warnings))
            for warning in self.warnings:
                self.out("   - " + warning.splitlines()[0])
        self.out(line)


def _valid_port(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 65535


def live_machine_network_hints(host: Host, port: int, arrow: str = "->") -> List[str]:
    """Firewall/permission notes for the machine that runs Live in LAN mode.

    Live only needs INCOMING TCP ``port``; the discovery beacon it sends is outgoing.
    """
    hints = ["Firewall here (the Live machine): allow INCOMING TCP %d for Ableton Live -- the "
             "discovery beacon (UDP %d) is outgoing and needs no rule here." % (port,
                                                                                DEFAULT_BEACON_PORT)]
    if host.is_windows:
        hints.append("Windows asks the first time Live listens: allow Private networks. Missed "
                     "it? docs/INSTALL.md#firewall has the New-NetFirewallRule command.")
    elif host.is_mac:
        hints.append("macOS 15+: allow Ableton Live in System Settings {a} Privacy & Security "
                     "{a} Local Network, or its discovery beacon never leaves this Mac."
                     .format(a=arrow))
    hints.append("The Claude machine needs INCOMING UDP %d only for live_discover "
                 "(docs/NETWORK.md)." % DEFAULT_BEACON_PORT)
    return hints


def claude_machine_network_hints(host: Host, arrow: str = "->") -> List[str]:
    """Firewall/permission notes for the machine that runs Claude against a Live elsewhere.

    The MCP server connects out (TCP) and, for ``live_discover``, RECEIVES the UDP beacon.
    """
    if host.is_windows:
        return ["live_discover listens for UDP %d here: allow it (admin PowerShell) with\n"
                "     New-NetFirewallRule -DisplayName \"LiveBridge discovery\" -Direction "
                "Inbound -Protocol UDP -LocalPort %d -Action Allow -Profile Private\n"
                "     (or skip discovery and use live_connect with the IP)."
                % (DEFAULT_BEACON_PORT, DEFAULT_BEACON_PORT)]
    if host.is_mac:
        return ["macOS 15+: give Claude Desktop -- or the terminal app that runs Claude Code -- "
                "access in System Settings {a} Privacy & Security {a} Local Network; without it "
                "connecting to a LAN address fails with \"No route to host\" and live_discover "
                "hears nothing.".format(a=arrow)]
    return []


# ---------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="install.py",
        description="Install LiveBridge (Ableton Live 12 <-> Claude) on macOS or Windows. "
                    "Idempotent: run it again to update -- settings you do not pass again "
                    "(LAN mode, port, allow_eval, beacon, paired host) are kept.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python installers/install.py                       # Live + Claude on this machine\n"
            "  python installers/install.py --network             # Live also reachable from the LAN\n"
            "  python installers/install.py --local               # back to this machine only\n"
            "  python installers/install.py --pair 192.168.1.20 --token TOKEN\n"
            "                                                     # Claude here, Live on 192.168.1.20\n"
            "  python installers/install.py --dry-run             # show what would happen\n"
        ))
    live = parser.add_argument_group("Live side (Remote Script)")
    live.add_argument("--network", action=argparse.BooleanOptionalAction, default=None,
                      help="LAN mode: the Remote Script listens on 0.0.0.0 so Claude on another "
                           "machine can connect (token enforced, beacon on); --no-network goes "
                           "back to 127.0.0.1 (default: keep the installed setting, first "
                           "install: off)")
    live.add_argument("--local", action="store_true",
                      help="this machine only: --no-network AND point the MCP server back at "
                           "127.0.0.1 (undoes --network and --pair)")
    live.add_argument("--port", type=int, default=None,
                      help="TCP port of the Remote Script, used on both sides (default: keep "
                           "the installed one, else %d)" % DEFAULT_PORT)
    live.add_argument("--token", help="shared secret to use (default: keep the installed one, "
                                      "else generate a random one)")
    live.add_argument("--new-token", action="store_true",
                      help="generate a new random token even if one is installed")
    live.add_argument("--name", help="machine name shown by live_discover (default: hostname)")
    live.add_argument("--allow-eval", action=argparse.BooleanOptionalAction, default=None,
                      help="allow live_eval_python (arbitrary Python inside Live; default: keep "
                           "the installed setting, first install: on)")
    live.add_argument("--beacon", action=argparse.BooleanOptionalAction, default=None,
                      help="UDP discovery beacon on port %d (default: follows --network/"
                           "--no-network, else keeps the installed setting)"
                           % DEFAULT_BEACON_PORT)
    live.add_argument("--remote-scripts-dir", metavar="DIR",
                      help="Live's 'Remote Scripts' folder (default: from Live's preferences)")
    live.add_argument("--no-remote-script", dest="remote_script", action="store_false",
                      help="do not install the Remote Script (a machine without Live)")

    claude = parser.add_argument_group("Claude side (MCP server)")
    claude.add_argument("--pair", metavar="HOST",
                        help="Live runs on HOST (installed there with --network); needs --token")
    claude.add_argument("--no-desktop", dest="desktop", action="store_false",
                        help="do not touch Claude Desktop's config")
    claude.add_argument("--desktop-config", metavar="PATH",
                        help="claude_desktop_config.json to update (default: auto-detect)")
    claude.add_argument("--no-claude-code", dest="claude_code", action="store_false",
                        help="do not register with Claude Code")
    claude.add_argument("--splice", action=argparse.BooleanOptionalAction, default=True,
                        help="also register the official Splice MCP (%s; default on)" % SPLICE_URL)
    claude.add_argument("--skill", action=argparse.BooleanOptionalAction, default=True,
                        help="install the LiveBridge skill in ~/.claude/skills and build "
                             "~/.livebridge/%s for Claude Desktop (default on)" % SKILL_ZIP_NAME)
    claude.add_argument("--no-mcp", dest="mcp", action="store_false",
                        help="do not install/register the MCP server (a Live-only machine)")
    claude.add_argument("--python", metavar="PATH",
                        help="Python 3.10+ used for the MCP server's virtual environment "
                             "(or, with --no-venv, installed into)")
    claude.add_argument("--uv", action="store_true",
                        help="create the environment and install with uv (can fetch Python); "
                             "later runs reuse uv when it is on PATH")
    claude.add_argument("--venv-dir", metavar="DIR",
                        help="virtual environment for the MCP server (default ~/.livebridge/venv)")
    claude.add_argument("--no-venv", action="store_true",
                        help="pip install into --python / this Python instead of a venv")
    claude.add_argument("--no-pip", action="store_true",
                        help="skip pip; use the livebridge-mcp that is already installed")
    claude.add_argument("--no-audio", action="store_true",
                        help="do not install the audio analysis libraries (numpy, scipy, "
                             "soundfile, pyloudnorm; ~150 MB) behind live_audio_analyze")

    parser.add_argument("--dry-run", action="store_true",
                        help="print every action, write nothing")
    parser.add_argument("--version", action="version", version="%(prog)s " + __version__)
    return parser


def main(argv: Optional[Sequence[str]] = None, host: Optional[Host] = None,
         runner: Optional[Callable[..., CommandResult]] = None,
         out: Optional[Callable[[str], None]] = None) -> int:
    """Entry point. ``host``/``runner``/``out`` are injectable for tests."""
    if sys.version_info[:2] < MIN_INSTALLER_PYTHON:
        sys.stderr.write("LiveBridge's installer needs Python 3.9 or newer.\n")
        return 1
    if out is None and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(errors="replace")
        except (ValueError, OSError):
            pass
    args = build_parser().parse_args(argv)
    installer = Installer(args, host or Host(), runner or run_command, out)
    try:
        return installer.execute()
    except InstallError as error:
        installer.out("")
        installer.out("ERROR: %s" % error)
        return 1
    except KeyboardInterrupt:
        installer.out("\ninterrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
