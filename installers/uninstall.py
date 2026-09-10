#!/usr/bin/env python3
"""LiveBridge uninstaller for macOS and Windows (standard library only, Python 3.9+).

Reverses ``install.py``, guided by ``~/.livebridge/install.json`` (falls back to auto-detection
when the manifest is missing):

1. Removes ``Remote Scripts/LiveBridge`` (only if it really is LiveBridge; a developer symlink or
   Windows junction is unlinked, never followed).
2. Removes the ``livebridge`` entry from every Claude Desktop config (other servers untouched;
   ``splice`` only with ``--remove-splice`` and only when it is the entry the installer wrote).
3. ``claude mcp remove --scope user livebridge`` (+ ``splice`` with ``--remove-splice``, only when
   the installer added it -- a Splice registration you made yourself is kept).
4. Deletes the MCP server's virtual environment (``~/.livebridge/venv``), or
   ``pip uninstall livebridge-mcp`` when it was installed with ``--no-venv``. On Windows a running
   Claude Desktop / Claude Code keeps the venv's files locked: the uninstaller then says so, keeps
   the install record and finishes the other steps -- quit Claude and run it again.
5. Removes the LiveBridge skill from ``~/.claude/skills/livebridge`` and
   ``~/.livebridge/livebridge-skill.zip`` (unless ``--keep-skill``).
6. Deletes ``~/.livebridge/config.json`` (unless ``--keep-config``) and the manifest.

Examples::

    python installers/uninstall.py --dry-run    # show what would be removed
    python installers/uninstall.py
    python installers/uninstall.py --remove-splice --keep-config
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import install as inst  # noqa: E402  (sibling module)
from install import (  # noqa: E402
    MCP_DIST, MCP_NAME, SCRIPT_NAME, SPLICE_NAME, SPLICE_URL, CommandResult, ConfigError, Host,
    Ops, read_json, run_command,
)

__version__ = inst.__version__


def looks_like_livebridge(folder: Path) -> bool:
    """True when ``folder`` is an installed LiveBridge Remote Script (safe to delete)."""
    return (folder / "LiveBridge.py").is_file() and (folder / "__init__.py").is_file()


def is_our_splice_entry(entry: Any) -> bool:
    """True for the Splice entry install.py writes (``npx mcp-remote https://mcp.splice.com/mcp``)."""
    if not isinstance(entry, dict):
        return False
    args = entry.get("args") or []
    return isinstance(args, list) and SPLICE_URL in [str(a) for a in args] and \
        "mcp-remote" in [str(a) for a in args]


class Uninstaller(Ops):
    """Runs the uninstall steps for parsed ``args`` (see :func:`build_parser`)."""

    def __init__(self, args: argparse.Namespace, host: Host,
                 runner: Callable[..., CommandResult] = run_command,
                 out: Optional[Callable[[str], None]] = None) -> None:
        super().__init__(host, args.dry_run, runner, out)
        self.args = args
        self.removed: List[str] = []
        #: Set when something could not be removed (files in use): the manifest is then kept,
        #: so a second run still knows what to remove.
        self.keep_manifest = False

    def execute(self) -> int:
        """Run every enabled step. Returns 0, or 1 when something was in use and is left."""
        manifest_file = inst.manifest_path(self.host)
        try:
            manifest = read_json(manifest_file)
        except ConfigError as error:
            self.info("%s -- falling back to auto-detection" % error)
            manifest = {}
        self.out("LiveBridge uninstaller %s -- %s%s" % (
            __version__, self.host.os_label, " -- DRY RUN, nothing is removed" if self.dry else ""))
        if not manifest:
            self.info("no install record at %s -- detecting what to remove" % manifest_file)
        if self.args.remote_script:
            self.remove_remote_script(manifest)
        if self.args.desktop:
            self.clean_claude_desktop(manifest)
        if self.args.claude_code:
            self.clean_claude_code(manifest)
        if self.args.mcp:
            self.remove_mcp_server(manifest)
        if self.args.skill:
            self.remove_skill(manifest)
        self.remove_user_files(manifest_file)
        self.summary()
        return 1 if self.keep_manifest else 0

    def delete_tree_or_explain(self, path: Path, what: str) -> bool:
        """``delete_tree`` that turns "file in use" (Windows) into a warning + manual step.

        Returns True when ``path`` was removed. On failure the manifest is kept so a re-run
        finds ``path`` again.
        """
        try:
            self.delete_tree(path)
        except OSError as error:
            self.keep_manifest = True
            self.warn("could not remove %s %s (%s) -- files in it are in use" % (what, path, error))
            self.manual.append(
                "Quit Claude Desktop completely (tray/menu bar icon {a} Quit) and every Claude "
                "Code session that uses LiveBridge (and Live, for the Remote Script), then run "
                "the uninstaller again to remove {path}.".format(a=self.arrow, path=path))
            return False
        return True

    # -- steps ---------------------------------------------------------------
    def remove_remote_script(self, manifest: Dict[str, Any]) -> None:
        self.step("Removing the LiveBridge Remote Script")
        folders: List[Path] = []
        if self.args.remote_scripts_dir:
            located, _, _ = inst.find_remote_scripts_dir(self.host, self.args.remote_scripts_dir)
            if located is not None:
                folders.append(located / SCRIPT_NAME)
        else:
            if manifest.get("remote_script_dir"):
                folders.append(Path(manifest["remote_script_dir"]))
            located, _, _ = inst.find_remote_scripts_dir(self.host)
            if located is not None and (located / SCRIPT_NAME) not in folders:
                folders.append(located / SCRIPT_NAME)
        found = False
        for folder in folders:
            if inst.is_link_or_junction(folder):
                found = True
                self.action("remove link %s (symlink/junction; its target is left alone)" % folder)
                if not self.dry:
                    inst.remove_link(folder)
                self.removed.append(str(folder))
            elif folder.is_dir():
                found = True
                if looks_like_livebridge(folder):
                    if self.delete_tree_or_explain(folder, "the Remote Script"):
                        self.removed.append(str(folder))
                else:
                    self.warn("%s does not look like LiveBridge (no LiveBridge.py) -- left alone"
                              % folder)
        if not found:
            self.info("not installed (looked in: %s)"
                      % (", ".join(str(f) for f in folders) or "nowhere -- no Live on this OS"))
        else:
            self.manual.append("In Live: Preferences {a} Link, Tempo & MIDI {a} Control Surface "
                               "{a} set the LiveBridge slot to None (or restart Live)."
                               .format(a=self.arrow))

    def clean_claude_desktop(self, manifest: Dict[str, Any]) -> None:
        self.step("Removing LiveBridge from Claude Desktop")
        paths: List[Path] = []
        if self.args.desktop_config:
            paths.append(Path(os.path.expanduser(self.args.desktop_config)))
        else:
            for item in manifest.get("desktop_configs") or []:
                if Path(item) not in paths:
                    paths.append(Path(item))
            for item in inst.desktop_config_candidates(self.host):
                if item not in paths:
                    paths.append(item)
        touched = False
        for path in paths:
            if not path.is_file():
                continue
            try:
                data = read_json(path)
            except ConfigError as error:
                self.warn("%s -- NOT modified; remove the \"%s\" entry by hand" % (error, MCP_NAME))
                continue
            servers = data.get("mcpServers")
            if not isinstance(servers, dict):
                continue
            servers = dict(servers)
            changed = False
            if MCP_NAME in servers:
                del servers[MCP_NAME]
                changed = True
            if self.args.remove_splice and SPLICE_NAME in servers:
                if is_our_splice_entry(servers[SPLICE_NAME]):
                    del servers[SPLICE_NAME]
                    changed = True
                else:
                    self.info("%s: the \"splice\" entry was not written by LiveBridge -- kept"
                              % path)
            if not changed:
                self.info("%s: nothing to remove" % path)
                continue
            updated = dict(data)
            updated["mcpServers"] = servers
            self.write_json(path, updated, backup=True)
            self.removed.append("%s (%s entry)" % (path, MCP_NAME))
            touched = True
        if not paths:
            self.info("Claude Desktop not found -- nothing to do")
        if touched:
            self.manual.append("Restart Claude Desktop so the change takes effect.")

    def clean_claude_code(self, manifest: Dict[str, Any]) -> None:
        """``claude mcp remove --scope user livebridge``; ``splice`` too with ``--remove-splice``,
        but only when the install record says the installer added it (never a Splice
        registration that existed before LiveBridge)."""
        self.step("Removing LiveBridge from Claude Code")
        names = [MCP_NAME]
        if self.args.remove_splice:
            if manifest.get("splice_claude_code_added"):
                names.append(SPLICE_NAME)
            else:
                self.info("`%s` in Claude Code was not added by the LiveBridge installer (or there "
                          "is no install record) -- kept. Remove it yourself with: %s"
                          % (SPLICE_NAME, self.host.quote(["claude", "mcp", "remove", "--scope",
                                                           "user", SPLICE_NAME])))
        claude = self.host.which("claude")
        if not claude:
            commands = [self.host.quote(["claude", "mcp", "remove", "--scope", "user", name])
                        for name in names]
            self.info("the Claude Code CLI (`claude`) is not on PATH -- if you registered "
                      "LiveBridge there, run: %s" % " ; ".join(commands))
            return
        for name in names:
            result = self.run([claude, "mcp", "remove", "--scope", "user", name], timeout=120.0)
            if result.ok:
                if not self.dry:
                    self.removed.append("Claude Code server `%s`" % name)
            else:
                self.info("`%s` was not registered in Claude Code (user scope)" % name)

    def remove_mcp_server(self, manifest: Dict[str, Any]) -> None:
        self.step("Removing the MCP server")
        default_venv = inst.default_venv_dir(self.host)
        venv = Path(manifest["venv"]) if manifest.get("venv") else default_venv
        ours = bool(manifest.get("venv_created")) or venv == default_venv
        if self.args.keep_venv:
            self.info("--keep-venv: %s left in place" % venv)
        elif venv.is_dir() and (venv / "pyvenv.cfg").is_file() and ours:
            if self.delete_tree_or_explain(venv, "the MCP server's virtual environment"):
                self.removed.append(str(venv))
        elif venv.exists() and not (venv / "pyvenv.cfg").is_file():
            self.warn("%s is not a virtual environment -- left alone" % venv)
        elif not manifest.get("venv") and manifest.get("python"):
            python = manifest["python"]
            result = self.run([python, "-m", "pip", "uninstall", "-y", MCP_DIST], timeout=600.0)
            if result.ok:
                if not self.dry:
                    self.removed.append("%s from %s" % (MCP_DIST, python))
            else:
                self.warn("`pip uninstall %s` failed:\n%s" % (MCP_DIST, inst.tail(result.output)))
        else:
            self.info("no LiveBridge virtual environment found (%s)" % venv)

    def remove_skill(self, manifest: Dict[str, Any]) -> None:
        """Remove the Claude Code skill copy and the Claude Desktop skill zip the installer made.

        Only a folder that really is the LiveBridge skill is deleted; a symlink/junction
        (developer setup) is left alone, like the installer does.
        """
        self.step("Removing the LiveBridge skill")
        folders: List[Path] = []
        if manifest.get("skill_dir"):
            folders.append(Path(manifest["skill_dir"]))
        default = inst.claude_skills_dir(self.host) / inst.SKILL_NAME
        if default not in folders:
            folders.append(default)
        found = False
        for folder in folders:
            if inst.is_link_or_junction(folder):
                found = True
                self.info("%s is a symlink/junction (developer setup) -- left alone" % folder)
            elif folder.is_dir():
                found = True
                if inst.is_livebridge_skill(folder):
                    if self.delete_tree_or_explain(folder, "the skill"):
                        self.removed.append(str(folder))
                else:
                    self.warn("%s is not the LiveBridge skill -- left alone" % folder)
        zip_path = Path(manifest["skill_zip"]) if manifest.get("skill_zip") \
            else inst.skill_zip_path(self.host)
        if zip_path.is_file():
            found = True
            self.delete_file(zip_path)
            self.removed.append(str(zip_path))
            self.manual.append("Claude Desktop: if you uploaded the LiveBridge skill, remove it in "
                               "Settings {a} Capabilities {a} Skills.".format(a=self.arrow))
        if not found:
            self.info("not installed (looked in: %s)" % ", ".join(str(f) for f in folders))

    def remove_user_files(self, manifest_file: Path) -> None:
        self.step("Removing LiveBridge settings")
        config = inst.user_config_path(self.host)
        if self.args.keep_config:
            self.info("--keep-config: %s kept (it holds host/port/token)" % config)
        elif config.is_file():
            self.delete_file(config)
            self.removed.append(str(config))
        if self.keep_manifest and manifest_file.is_file():
            self.info("%s kept: something could not be removed -- run the uninstaller again "
                      "after quitting Claude" % manifest_file)
        elif manifest_file.is_file():
            self.delete_file(manifest_file)
        folder = inst.livebridge_dir(self.host)
        if folder.is_dir() and not self.dry:
            try:
                next(folder.iterdir())
            except StopIteration:
                folder.rmdir()
                self.info("removed empty %s" % folder)
            except OSError:
                pass

    def summary(self) -> None:
        line = "=" * 72
        self.out("")
        self.out(line)
        self.out(" LiveBridge %s" % ("dry run finished (nothing removed)" if self.dry
                                     else "uninstalled"))
        self.out(line)
        if self.keep_manifest:
            self.out(" NOT everything could be removed -- see the steps below.")
        for item in self.removed:
            self.out("   removed: %s" % item)
        for number, item in enumerate(self.manual, 1):
            self.out(" %d. %s" % (number, item))
        if self.warnings:
            self.out(" %d warning(s) -- see above." % len(self.warnings))
        self.out(line)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="uninstall.py",
        description="Remove LiveBridge (Remote Script, MCP server, Claude registrations).")
    parser.add_argument("--dry-run", action="store_true", help="print every action, remove nothing")
    parser.add_argument("--remote-scripts-dir", metavar="DIR",
                        help="Live's 'Remote Scripts' folder (default: from the install record / "
                             "Live's preferences)")
    parser.add_argument("--no-remote-script", dest="remote_script", action="store_false",
                        help="leave the Remote Script in Live's User Library")
    parser.add_argument("--no-desktop", dest="desktop", action="store_false",
                        help="leave Claude Desktop's config alone")
    parser.add_argument("--desktop-config", metavar="PATH",
                        help="claude_desktop_config.json to clean (default: auto-detect)")
    parser.add_argument("--no-claude-code", dest="claude_code", action="store_false",
                        help="leave Claude Code's registration alone")
    parser.add_argument("--no-mcp", dest="mcp", action="store_false",
                        help="leave the MCP server installation alone")
    parser.add_argument("--remove-splice", action="store_true",
                        help="also remove the Splice MCP entries the installer added")
    parser.add_argument("--keep-config", action="store_true",
                        help="keep ~/.livebridge/config.json (host/port/token)")
    parser.add_argument("--keep-venv", action="store_true",
                        help="keep the MCP server's virtual environment")
    parser.add_argument("--keep-skill", dest="skill", action="store_false",
                        help="keep the LiveBridge skill in ~/.claude/skills and its zip")
    parser.add_argument("--version", action="version", version="%(prog)s " + __version__)
    return parser


def main(argv: Optional[Sequence[str]] = None, host: Optional[Host] = None,
         runner: Optional[Callable[..., CommandResult]] = None,
         out: Optional[Callable[[str], None]] = None) -> int:
    """Entry point. ``host``/``runner``/``out`` are injectable for tests."""
    if out is None and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(errors="replace")
        except (ValueError, OSError):
            pass
    args = build_parser().parse_args(argv)
    uninstaller = Uninstaller(args, host or Host(), runner or run_command, out)
    try:
        return uninstaller.execute()
    except (inst.InstallError, OSError) as error:
        uninstaller.out("")
        uninstaller.out("ERROR: %s" % error)
        return 1
    except KeyboardInterrupt:
        uninstaller.out("\ninterrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
