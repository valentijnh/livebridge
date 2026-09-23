#!/usr/bin/env python3
"""Pack this LiveBridge checkout into one ZIP to copy to another computer (stdlib only, 3.9+).

For the two-machine setup (e.g. Live on a Windows PC, Claude on a Mac) without a git remote:

    python installers/bundle.py                     # -> dist/LiveBridge-<version>.zip
    python installers/bundle.py --output D:/LiveBridge.zip

On the other computer: unzip it into a folder you keep (the MCP server is installed *editable*
from that folder -- moving or deleting it later breaks Claude's LiveBridge until you re-run the
installer from the new place), then run ``installers/install.sh`` / ``install.ps1`` there.
To update: build a new ZIP, unzip it over the old folder (or into a new one) and re-run the
installer -- it keeps the token and every setting.

The ZIP holds exactly what the installers and docs need -- ``remote_script``, ``mcp_server``,
``installers``, ``docs``, ``tests``, ``.claude/skills``, ``examples``, README/LICENSE/CHANGELOG --
and never caches, virtual environments, git data, build output or developer configs (``config.json`` /
``config.local.json`` inside the Remote Script, which hold a token). File modes are kept, so
``installers/install.sh`` stays executable after unzipping on macOS.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import stat
import sys
import zipfile
from pathlib import Path
from typing import List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
TOP_FOLDER = "LiveBridge"

#: Top-level entries that go into the bundle (missing ones are skipped).
INCLUDE = ("remote_script", "mcp_server", "installers", "docs", "tests", ".claude/skills",
           "examples", "README.md", "LICENSE", "CHANGELOG.md", "SECURITY.md", "CONTRIBUTING.md",
           "CLAUDE.md", "PLAN.md")
#: Directory names never packed, wherever they are.
EXCLUDE_DIRS = ("__pycache__", ".git", ".venv", "venv", ".pytest_cache", ".mypy_cache",
                ".ruff_cache", "build", "dist", ".livebridge_work", ".idea", ".vscode")
#: File name patterns never packed.
EXCLUDE_FILES = ("*.pyc", "*.pyo", ".DS_Store", "Thumbs.db", "*.egg-info", "*.livebridge-tmp")
#: Developer configs inside the Remote Script (they hold a token) are never packed.
EXCLUDE_PATHS = ("remote_script/LiveBridge/config.json",
                 "remote_script/LiveBridge/config.local.json")


def version() -> str:
    """The MCP server version from ``mcp_server/pyproject.toml`` (``0.0.0`` if unreadable)."""
    try:
        text = (REPO_ROOT / "mcp_server" / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return "0.0.0"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("version") and "=" in stripped:
            return stripped.split("=", 1)[1].strip().strip("\"'") or "0.0.0"
    return "0.0.0"


def _excluded(rel: str, is_dir: bool) -> bool:
    name = rel.rsplit("/", 1)[-1]
    if is_dir:
        return name in EXCLUDE_DIRS or name.endswith(".egg-info")
    if rel in EXCLUDE_PATHS:
        return True
    return any(fnmatch.fnmatch(name, pattern) for pattern in EXCLUDE_FILES)


def collect(root: Path = REPO_ROOT) -> List[str]:
    """Relative POSIX paths of every file that goes into the bundle, sorted."""
    files: List[str] = []
    for entry in INCLUDE:
        path = root / entry
        if path.is_file():
            files.append(entry)
            continue
        if not path.is_dir():
            continue
        for base, dirs, names in os.walk(str(path)):
            base_rel = Path(base).relative_to(root).as_posix()
            dirs[:] = sorted(d for d in dirs if not _excluded("%s/%s" % (base_rel, d), True))
            for name in names:
                rel = "%s/%s" % (base_rel, name)
                if not _excluded(rel, False):
                    files.append(rel)
    return sorted(set(files))


def build(output: Path, root: Path = REPO_ROOT) -> List[str]:
    """Write the bundle to ``output``; returns the packed relative paths."""
    files = collect(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(output.name + ".livebridge-tmp")
    with zipfile.ZipFile(str(temp), "w", zipfile.ZIP_DEFLATED) as archive:
        for rel in files:
            source = root / rel
            info = zipfile.ZipInfo.from_file(str(source), "%s/%s" % (TOP_FOLDER, rel))
            mode = stat.S_IMODE(source.stat().st_mode)
            if rel.endswith(".sh"):
                mode |= 0o755  # executable after unzipping on macOS, even when built on Windows
            info.external_attr = (stat.S_IFREG | (mode or 0o644)) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, source.read_bytes())
    os.replace(str(temp), str(output))
    return files


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bundle.py",
        description="Pack this LiveBridge checkout into a ZIP for another computer.")
    parser.add_argument("--output", metavar="ZIP",
                        help="where to write the ZIP (default dist/LiveBridge-<version>.zip)")
    args = parser.parse_args(argv)
    output = Path(os.path.expanduser(args.output)) if args.output else \
        REPO_ROOT / "dist" / ("LiveBridge-%s.zip" % version())
    files = build(output)
    size = output.stat().st_size
    print("wrote %s (%d files, %.1f MB)" % (output, len(files), size / 1e6))
    print("On the other computer: unzip it into a folder you keep, then run "
          "installers/install.sh (macOS) or installers\\install.ps1 (Windows) from it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
