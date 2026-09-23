"""Tests for installers/install.py, installers/uninstall.py, the shell wrappers and
tests/integration_check.py.

Nothing here touches the real machine: every run gets a :class:`install.Host` with a temporary
home folder, a fake ``which`` and a fake command runner (no pip, no venv, no ``claude``).
Both the macOS (``darwin``) and the Windows (``win32``) branches are exercised on any OS because
all paths are built from the injected home/environment.
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
INSTALLERS = REPO / "installers"
for _path in (str(INSTALLERS), str(REPO / "mcp_server"), str(REPO / "tests")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import install  # noqa: E402
import uninstall  # noqa: E402
import integration_check  # noqa: E402
from install import CommandResult  # noqa: E402

LIBRARY_CFG = """<?xml version="1.0" encoding="UTF-8"?>
<Ableton MajorVersion="5" MinorVersion="12.0_12402" Creator="Ableton Live {version}">
\t<ContentLibrary>
\t\t<UserLibrary>
\t\t\t<LibraryProject Id="0">
\t\t\t\t<ProjectLocation />
\t\t\t\t<ProjectName Value="User Library" />
\t\t\t\t<ProjectPath Value="{path}" />
\t\t\t</LibraryProject>
\t\t</UserLibrary>
\t\t<SpliceDownloadFolderModeMember Value="UserLibrary" />
\t</ContentLibrary>
</Ableton>
"""


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------

class FakeRunner(object):
    """Stands in for subprocess: records argv lists and simulates venv/pip/claude."""

    def __init__(self, host, version="3.12.4", versions=None, fail=None, output=None,
                 create_exe=True, scripts_dir=None):
        self.host = host
        self.version = version
        self.versions = versions or {}
        self.fail = fail or {}          # substring of the joined argv -> returncode
        self.output = output or {}      # substring -> stdout/stderr text
        self.create_exe = create_exe
        self.scripts_dir = scripts_dir
        self.calls = []
        self.claude_servers = set()

    def __call__(self, argv, timeout=None):
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        joined = " ".join(argv)
        for needle, code in self.fail.items():
            if needle in joined:
                return CommandResult(code, "", self.output.get(needle, "simulated failure"))
        if "-c" in argv and argv[argv.index("-c") + 1] == install._PROBE_CODE:
            exe = argv[0]
            version = self.versions.get(exe, self.version)
            if version is None:
                return CommandResult(127, "", "not found")
            return CommandResult(0, "%s\n%s\n" % (version, json.dumps(exe)))
        if "-c" in argv and argv[argv.index("-c") + 1] == install._SCRIPTS_CODE:
            folders = [str(self.scripts_dir)] if self.scripts_dir else []
            return CommandResult(0, json.dumps(folders) + "\n")
        if "-m" in argv and argv[argv.index("-m") + 1] == "venv" or argv[1:2] == ["venv"]:
            venv = Path(argv[-1])
            python = self.host.venv_python(venv)
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("#!fake python\n", encoding="utf-8")
            (venv / "pyvenv.cfg").write_text("home = /fake\n", encoding="utf-8")
            return CommandResult(0, "created")
        if "pip" in argv and "install" in argv:
            if "--python" in argv:
                python = Path(argv[argv.index("--python") + 1])
            else:
                python = Path(argv[0])
            if self.create_exe:
                exe = python.parent / self.host.exe("livebridge-mcp")
                exe.write_text("#!fake exe\n", encoding="utf-8")
            return CommandResult(0, "Successfully installed livebridge-mcp-0.1.0")
        if argv[-1] == "--version":
            return CommandResult(0, "livebridge-mcp 0.1.0\n")
        if len(argv) > 2 and argv[1] == "mcp":
            verb = argv[2]
            if verb == "add":
                name = [a for a in argv[3:] if not a.startswith("-") and a not in ("user", "http")][0]
                self.claude_servers.add(name)
                return CommandResult(0, "Added %s" % name)
            if verb == "remove":
                name = argv[-1]
                if name in self.claude_servers:
                    self.claude_servers.discard(name)
                    return CommandResult(0, "Removed")
                return CommandResult(1, "", "No MCP server found with name: %s" % name)
            if verb == "get":
                return CommandResult(0 if argv[-1] in self.claude_servers else 1, "")
        return CommandResult(0)

    def commands(self, needle):
        return [c for c in self.calls if needle in " ".join(c)]

    def mutating_calls(self):
        """Calls that are not read-only python probes."""
        return [c for c in self.calls if "-c" not in c and c[-1] != "--version"]


def fake_which(mapping):
    def which(name):
        return mapping.get(name)
    return which


def mac_host(tmp_path, which=None, library=True):
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    if library:
        (home / "Music" / "Ableton" / "User Library").mkdir(parents=True)
    return install.Host(platform="darwin", home=home, environ={"HOME": str(home)},
                        hostname="TestMac", which=fake_which(which or {}))


def win_host(tmp_path, which=None, library=True):
    home = tmp_path / "Users" / "val"
    home.mkdir(parents=True, exist_ok=True)
    environ = {"USERPROFILE": str(home), "APPDATA": str(home / "AppData" / "Roaming"),
               "LOCALAPPDATA": str(home / "AppData" / "Local")}
    if library:
        (home / "Documents" / "Ableton" / "User Library").mkdir(parents=True)
    return install.Host(platform="win32", home=home, environ=environ, hostname="VAL-PC",
                        which=fake_which(which or {}))


def desktop_dir(host):
    if host.is_windows:
        return host.appdata() / "Claude"
    return host.home / "Library" / "Application Support" / "Claude"


def write_desktop_config(host, data):
    folder = desktop_dir(host)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "claude_desktop_config.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def run_install(host, argv=(), runner=None, **runner_kwargs):
    runner = runner or FakeRunner(host, **runner_kwargs)
    lines = []
    code = install.main(list(argv), host=host, runner=runner, out=lines.append)
    return code, "\n".join(lines), runner


def run_uninstall(host, argv=(), runner=None):
    runner = runner or FakeRunner(host)
    lines = []
    code = uninstall.main(list(argv), host=host, runner=runner, out=lines.append)
    return code, "\n".join(lines), runner


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def snapshot_tree(root):
    """{relative path: bytes} for every file below root (dry-run 'writes nothing' check)."""
    result = {}
    for path in sorted(Path(root).rglob("*")):
        if path.is_file():
            result[str(path.relative_to(root))] = path.read_bytes()
        else:
            result[str(path.relative_to(root)) + "/"] = b""
    return result


def remote_dir_mac(host):
    return host.home / "Music" / "Ableton" / "User Library" / "Remote Scripts" / "LiveBridge"


def remote_dir_win(host):
    return host.home / "Documents" / "Ableton" / "User Library" / "Remote Scripts" / "LiveBridge"


# ---------------------------------------------------------------------------
# path detection
# ---------------------------------------------------------------------------

def test_library_cfg_picks_newest_live_12_user_library_on_mac(tmp_path):
    host = mac_host(tmp_path, library=False)
    prefs = host.home / "Library" / "Preferences" / "Ableton"
    libs = {}
    for version in ("11.3.25", "12.0.10", "12.4.5"):
        folder = prefs / ("Live %s" % version)
        folder.mkdir(parents=True)
        base = tmp_path / ("lib-%s" % version)
        base.mkdir()
        libs[version] = base
        (folder / "Library.cfg").write_text(
            LIBRARY_CFG.format(version=version, path=base), encoding="utf-8")
    path, how, notes = install.find_remote_scripts_dir(host)
    assert path == libs["12.4.5"] / "User Library" / "Remote Scripts"
    assert "Live 12.4.5" in how


def test_library_cfg_with_missing_drive_falls_back_to_the_next_version(tmp_path):
    host = mac_host(tmp_path, library=False)
    prefs = host.home / "Library" / "Preferences" / "Ableton"
    (prefs / "Live 12.4.5").mkdir(parents=True)
    (prefs / "Live 12.4.5" / "Library.cfg").write_text(
        LIBRARY_CFG.format(version="12.4.5", path=tmp_path / "unplugged" / "drive"),
        encoding="utf-8")
    (prefs / "Live 12.0.10").mkdir(parents=True)
    (prefs / "Live 12.0.10" / "Library.cfg").write_text(
        LIBRARY_CFG.format(version="12.0.10", path=tmp_path), encoding="utf-8")
    path, how, notes = install.find_remote_scripts_dir(host)
    assert path == tmp_path / "User Library" / "Remote Scripts"
    assert any("does not exist" in note for note in notes)


def test_windows_library_cfg_lives_in_the_preferences_subfolder(tmp_path):
    host = win_host(tmp_path, library=False)
    custom = tmp_path / "D" / "Music"
    custom.mkdir(parents=True)
    cfg_dir = host.appdata() / "Ableton" / "Live 12.4.5" / "Preferences"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "Library.cfg").write_text(LIBRARY_CFG.format(version="12.4.5", path=custom),
                                         encoding="utf-8")
    path, how, _ = install.find_remote_scripts_dir(host)
    assert path == custom / "User Library" / "Remote Scripts"


def test_default_locations_per_os(tmp_path):
    mac = mac_host(tmp_path / "m")
    win = win_host(tmp_path / "w")
    assert install.find_remote_scripts_dir(mac)[0] == remote_dir_mac(mac).parent
    assert install.find_remote_scripts_dir(win)[0] == remote_dir_win(win).parent


@pytest.mark.parametrize("key", ["OneDrive", "ONEDRIVE", "OneDriveCommercial",
                                 "ONEDRIVECOMMERCIAL"])
def test_windows_onedrive_documents_is_found(tmp_path, key):
    """On real Windows os.environ keys are upper-case (ONEDRIVE); lookups must still hit."""
    home = tmp_path / "Users" / "val"
    home.mkdir(parents=True)
    onedrive = home / "OneDrive - Blub Media"
    (onedrive / "Documents" / "Ableton" / "User Library").mkdir(parents=True)
    host = install.Host(platform="win32", home=home,
                        environ={"USERPROFILE": str(home), key: str(onedrive)},
                        hostname="VAL-PC", which=fake_which({}))
    path, how, _ = install.find_remote_scripts_dir(host)
    assert path == onedrive / "Documents" / "Ableton" / "User Library" / "Remote Scripts"
    assert host.env(key.lower()) == host.env(key) == str(onedrive)


def test_missing_user_library_uses_the_default_with_a_note(tmp_path):
    host = mac_host(tmp_path, library=False)
    path, how, notes = install.find_remote_scripts_dir(host)
    assert path == host.home / "Music" / "Ableton" / "User Library" / "Remote Scripts"
    assert "not created by Live yet" in how and notes


def test_explicit_dir_accepts_the_livebridge_folder_itself(tmp_path):
    host = mac_host(tmp_path)
    target = tmp_path / "Custom" / "Remote Scripts"
    assert install.find_remote_scripts_dir(host, str(target / "LiveBridge"))[0] == target
    assert install.find_remote_scripts_dir(host, str(target))[0] == target


def test_linux_has_no_live_folder(tmp_path):
    host = install.Host(platform="linux", home=tmp_path, environ={"HOME": str(tmp_path)},
                        which=fake_which({}))
    path, _, notes = install.find_remote_scripts_dir(host)
    assert path is None and notes


def test_desktop_config_candidates_include_the_msix_install(tmp_path):
    host = win_host(tmp_path)
    (host.appdata() / "Claude").mkdir(parents=True)
    msix = host.localappdata() / "Packages" / "Claude_pzs8sxrjxfjjc" / "LocalCache" / \
        "Roaming" / "Claude"
    msix.mkdir(parents=True)
    paths = install.desktop_config_candidates(host)
    assert paths == [host.appdata() / "Claude" / "claude_desktop_config.json",
                     msix / "claude_desktop_config.json"]
    assert install.desktop_config_candidates(mac_host(tmp_path / "mac")) == []


def test_user_config_path_honours_livebridge_config(tmp_path):
    host = mac_host(tmp_path)
    assert install.user_config_path(host) == host.home / ".livebridge" / "config.json"
    host.environ["LIVEBRIDGE_CONFIG"] = str(tmp_path / "other.json")
    assert install.user_config_path(host) == tmp_path / "other.json"


# ---------------------------------------------------------------------------
# dry run
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("make_host", [mac_host, win_host], ids=["darwin", "win32"])
def test_dry_run_writes_nothing_and_prints_every_action(tmp_path, make_host):
    host = make_host(tmp_path, which={"claude": "/fake/claude", "npx": "/fake/node/bin/npx"})
    write_desktop_config(host, {"mcpServers": {"other": {"command": "x"}}})
    before = snapshot_tree(tmp_path)
    code, out, runner = run_install(host, ["--dry-run"])
    assert code == 0, out
    assert snapshot_tree(tmp_path) == before
    assert runner.mutating_calls() == []
    for needle in ("DRY RUN", "[dry-run] copy", "[dry-run] write", "config.json",
                   "[dry-run] run:", "pip install", "-m venv", "claude mcp add --scope user "
                   "livebridge", "mcp.splice.com", "Control Surface", "Token:"):
        assert needle in out, (needle, out)


def test_dry_run_on_windows_uses_windows_paths(tmp_path):
    host = win_host(tmp_path)
    code, out, _ = run_install(host, ["--dry-run", "--no-claude-code"])
    assert code == 0
    assert str(remote_dir_win(host)) in out
    venv = host.home / ".livebridge" / "venv"
    assert str(venv / "Scripts" / "python.exe") + " -m pip install" in out
    assert str(host.appdata() / "Claude") not in out  # Claude Desktop not installed: skipped


# ---------------------------------------------------------------------------
# real runs (temp dirs)
# ---------------------------------------------------------------------------

def test_full_install_on_macos(tmp_path):
    host = mac_host(tmp_path, which={"claude": "/fake/bin/claude",
                                     "npx": "/fake/node/bin/npx"})
    desktop = write_desktop_config(host, {
        "globalShortcut": "Alt+Space",
        "mcpServers": {"filesystem": {"command": "npx", "args": ["fs"]}},
    })
    code, out, runner = run_install(host)
    assert code == 0, out

    # Remote Script copied, clean
    dest = remote_dir_mac(host)
    assert (dest / "__init__.py").is_file() and (dest / "LiveBridge.py").is_file()
    assert (dest / "handlers" / "system.py").is_file()
    assert not list(dest.rglob("__pycache__")) and not list(dest.rglob("*.pyc"))

    # Remote Script config
    config = read(dest / "config.json")
    assert config["host"] == "127.0.0.1" and config["port"] == 9880
    assert re.fullmatch(r"[0-9a-f]{32}", config["token"])
    assert config["allow_eval"] is True and config["beacon"] is False
    assert config["name"] == "TestMac" and config["beacon_port"] == 9881
    if os.name != "nt":
        assert (dest / "config.json").stat().st_mode & 0o777 == 0o600

    # MCP-side config uses the same token
    user = read(host.home / ".livebridge" / "config.json")
    assert user == {"host": "127.0.0.1", "port": 9880, "token": config["token"]}

    # venv + editable pip install
    venv = host.home / ".livebridge" / "venv"
    assert runner.commands("-m venv " + str(venv))
    pip = runner.commands("pip install")[0]
    assert pip[0] == str(venv / "bin" / "python") and pip[-2:] == ["-e", str(REPO / "mcp_server")]
    exe = str(venv / "bin" / "livebridge-mcp")

    # Claude Desktop: merged, nothing clobbered, backup kept
    data = read(desktop)
    assert data["globalShortcut"] == "Alt+Space"
    assert data["mcpServers"]["filesystem"] == {"command": "npx", "args": ["fs"]}
    assert data["mcpServers"]["livebridge"] == {
        "command": exe, "args": [],
        "env": {"LIVEBRIDGE_CONFIG": str(host.home / ".livebridge" / "config.json")}}
    splice = data["mcpServers"]["splice"]
    assert splice["command"] == "/fake/node/bin/npx"
    assert splice["args"] == ["-y", "mcp-remote", "https://mcp.splice.com/mcp"]
    # npx's folder first; a Path like every path here (so "\\fake\\node\\bin" on a Windows host)
    assert splice["env"]["PATH"].startswith(str(Path("/fake/node/bin")) + ":")
    assert (desktop.parent / "claude_desktop_config.json.livebridge-backup").is_file()

    # Claude Code
    assert ["/fake/bin/claude", "mcp", "add", "--scope", "user", "livebridge", "--", exe] \
        in runner.calls
    assert ["/fake/bin/claude", "mcp", "add", "--transport", "http", "--scope", "user", "splice",
            "https://mcp.splice.com/mcp"] in runner.calls

    # manifest + checklist
    manifest = read(host.home / ".livebridge" / "install.json")
    assert manifest["remote_script_dir"] == str(dest)
    assert manifest["venv"] == str(venv) and manifest["venv_created"] is True
    assert manifest["desktop_configs"] == [str(desktop)]
    assert manifest["claude_code"] is True and manifest["splice_claude_code"] is True
    assert "Preferences -> Link, Tempo & MIDI -> Control Surface -> LiveBridge (Input/Output: None)" in out
    assert config["token"] in out and "live_status" in out


def test_full_install_on_windows(tmp_path):
    host = win_host(tmp_path, which={"npx": "C:/nodejs/npx.cmd"})
    desktop = write_desktop_config(host, {"mcpServers": {"other": {"command": "other.exe"}}})
    msix = host.localappdata() / "Packages" / "Claude_pzs8sxrjxfjjc" / "LocalCache" / \
        "Roaming" / "Claude"
    msix.mkdir(parents=True)
    code, out, runner = run_install(host)
    assert code == 0, out

    dest = remote_dir_win(host)
    assert (dest / "LiveBridge.py").is_file()
    venv = host.home / ".livebridge" / "venv"
    assert runner.commands("pip install")[0][0] == str(venv / "Scripts" / "python.exe")
    exe = str(venv / "Scripts" / "livebridge-mcp.exe")
    for path in (desktop, msix / "claude_desktop_config.json"):
        servers = read(path)["mcpServers"]
        assert servers["livebridge"]["command"] == exe
        assert servers["splice"] == {"command": "cmd", "args": ["/c", "npx", "-y", "mcp-remote",
                                                                "https://mcp.splice.com/mcp"]}
    assert read(desktop)["mcpServers"]["other"] == {"command": "other.exe"}
    # no `claude` on PATH: the exact commands are printed instead
    assert "claude mcp add --scope user livebridge -- " + exe in out
    assert "claude mcp add --transport http --scope user splice https://mcp.splice.com/mcp" in out
    assert not runner.commands(" mcp ")


def test_rerun_is_idempotent_and_keeps_the_token(tmp_path):
    host = mac_host(tmp_path, which={"claude": "/fake/claude", "npx": "/fake/npx"})
    desktop = write_desktop_config(host, {"mcpServers": {"x": {"command": "x"}}})
    code, _, runner = run_install(host)
    assert code == 0
    first_token = read(remote_dir_mac(host) / "config.json")["token"]
    first_desktop = desktop.read_text(encoding="utf-8")

    code, out, runner2 = run_install(host, runner=FakeRunner(host))
    assert code == 0, out
    assert read(remote_dir_mac(host) / "config.json")["token"] == first_token
    assert read(host.home / ".livebridge" / "config.json")["token"] == first_token
    assert desktop.read_text(encoding="utf-8") == first_desktop
    assert "unchanged: %s" % desktop in out
    assert "reusing" in out  # the venv is reused, not recreated
    assert not runner2.commands("-m venv")
    assert list(read(desktop)["mcpServers"]) == ["x", "livebridge", "splice"]


def test_rerun_does_not_readd_an_existing_splice_in_claude_code(tmp_path):
    host = mac_host(tmp_path, which={"claude": "/fake/claude"})
    runner = FakeRunner(host)
    runner.claude_servers = {"splice", "livebridge"}
    code, out, _ = run_install(host, ["--no-desktop"], runner=runner)
    assert code == 0
    assert runner.commands("mcp remove --scope user livebridge")
    assert not runner.commands("--transport http")
    assert "already registered" in out


def test_new_token_and_explicit_token(tmp_path):
    host = mac_host(tmp_path)
    run_install(host, ["--no-desktop", "--no-claude-code"])
    old = read(remote_dir_mac(host) / "config.json")["token"]
    run_install(host, ["--no-desktop", "--no-claude-code", "--new-token"])
    new = read(remote_dir_mac(host) / "config.json")["token"]
    assert new != old and re.fullmatch(r"[0-9a-f]{32}", new)
    run_install(host, ["--no-desktop", "--no-claude-code", "--token", "my-secret-token-1"])
    assert read(remote_dir_mac(host) / "config.json")["token"] == "my-secret-token-1"
    assert read(host.home / ".livebridge" / "config.json")["token"] == "my-secret-token-1"


def test_generated_tokens_are_random():
    tokens = {install.new_token() for _ in range(20)}
    assert len(tokens) == 20 and all(re.fullmatch(r"[0-9a-f]{32}", t) for t in tokens)


@pytest.mark.parametrize("token", ["short", "has space in it", 'quote"token'])
def test_invalid_token_is_rejected(tmp_path, token):
    host = mac_host(tmp_path)
    code, out, runner = run_install(host, ["--token", token])
    assert code == 1 and "--token" in out
    assert not runner.calls and not remote_dir_mac(host).exists()


def test_network_mode(tmp_path):
    host = mac_host(tmp_path)
    code, out, _ = run_install(host, ["--network", "--port", "9890", "--no-desktop",
                                      "--no-claude-code", "--name", "Studio Mac"])
    assert code == 0, out
    config = read(remote_dir_mac(host) / "config.json")
    assert config["host"] == "0.0.0.0" and config["port"] == 9890
    assert config["beacon"] is True and config["name"] == "Studio Mac" and config["token"]
    # the MCP server on the Live machine itself still talks to loopback
    assert read(host.home / ".livebridge" / "config.json")["host"] == "127.0.0.1"
    assert "--pair" in out and "--token %s" % config["token"] in out
    assert "TCP 9890" in out


def test_no_beacon_and_no_eval_flags(tmp_path):
    host = mac_host(tmp_path)
    run_install(host, ["--network", "--no-beacon", "--no-allow-eval", "--no-mcp"])
    config = read(remote_dir_mac(host) / "config.json")
    assert config["beacon"] is False and config["allow_eval"] is False


def test_pair_requires_a_token(tmp_path):
    host = mac_host(tmp_path)
    code, out, _ = run_install(host, ["--pair", "192.168.1.20"])
    assert code == 1 and "--pair needs --token" in out


@pytest.mark.parametrize("bad", ["http://192.168.1.20", "two words", ""])
def test_pair_rejects_bad_hosts(tmp_path, bad):
    code, out, _ = run_install(mac_host(tmp_path), ["--pair", bad, "--token", "abcdefgh12"])
    assert code == 1 and "--pair" in out


def test_pair_points_the_mcp_server_at_the_other_machine(tmp_path):
    host = win_host(tmp_path)
    code, out, _ = run_install(host, ["--pair", "192.168.1.20", "--token", "abcdef123456",
                                      "--no-desktop", "--no-claude-code"])
    assert code == 0, out
    assert read(host.home / ".livebridge" / "config.json") == {
        "host": "192.168.1.20", "port": 9880, "token": "abcdef123456"}
    # the local Remote Script (this machine has Live too) uses the same token
    assert read(remote_dir_win(host) / "config.json")["token"] == "abcdef123456"
    assert "Paired" in out


def test_pair_on_a_machine_without_live(tmp_path):
    host = install.Host(platform="linux", home=tmp_path, environ={"HOME": str(tmp_path)},
                        which=fake_which({}))
    code, out, _ = run_install(host, ["--pair", "10.0.0.5", "--token", "abcdef123456",
                                      "--no-desktop", "--no-claude-code"])
    assert code == 0, out
    assert "skipping the Remote Script" in out
    assert read(tmp_path / ".livebridge" / "config.json")["host"] == "10.0.0.5"


def test_reinstall_replaces_stale_files_and_keeps_custom_config_keys(tmp_path):
    host = mac_host(tmp_path)
    dest = remote_dir_mac(host)
    (dest / "handlers" / "__pycache__").mkdir(parents=True)
    (dest / "handlers" / "__pycache__" / "old.cpython-311.pyc").write_bytes(b"x")
    (dest / "handlers" / "removed_module.py").write_text("stale", encoding="utf-8")
    (dest / "config.json").write_text(json.dumps({
        "host": "0.0.0.0", "token": "keepthistoken1", "max_clients": 8, "log_level": "debug",
        "name": "My Studio"}), encoding="utf-8")
    code, out, _ = run_install(host, ["--no-mcp"])
    assert code == 0, out
    assert not (dest / "handlers" / "removed_module.py").exists()
    assert not list(dest.rglob("__pycache__"))
    config = read(dest / "config.json")
    assert config["token"] == "keepthistoken1"          # token survives
    assert config["host"] == "0.0.0.0"                  # no flag: the installed mode stays
    assert config["max_clients"] == 8 and config["log_level"] == "debug"
    assert config["name"] == "My Studio"


def test_source_config_and_caches_are_never_copied(tmp_path, monkeypatch):
    source = tmp_path / "src" / "LiveBridge"
    shutil.copytree(str(REPO / "remote_script" / "LiveBridge"), str(source),
                    ignore=shutil.ignore_patterns("__pycache__"))
    (source / "config.json").write_text('{"token": "developer"}', encoding="utf-8")
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "x.pyc").write_bytes(b"x")
    (source / ".DS_Store").write_bytes(b"x")
    monkeypatch.setattr(install, "SCRIPT_SOURCE", source)
    host = mac_host(tmp_path)
    code, out, _ = run_install(host, ["--no-mcp"])
    assert code == 0, out
    dest = remote_dir_mac(host)
    assert read(dest / "config.json")["token"] != "developer"
    assert not (dest / "__pycache__").exists() and not (dest / ".DS_Store").exists()


@pytest.mark.skipif(os.name == "nt", reason="symlinks need admin rights on Windows")
def test_symlinked_dev_install_is_not_deleted(tmp_path):
    host = mac_host(tmp_path)
    target = tmp_path / "devcopy"
    shutil.copytree(str(REPO / "remote_script" / "LiveBridge"), str(target),
                    ignore=shutil.ignore_patterns("__pycache__"))
    dest = remote_dir_mac(host)
    dest.parent.mkdir(parents=True)
    dest.symlink_to(target, target_is_directory=True)
    code, out, _ = run_install(host, ["--no-mcp"])
    assert code == 0 and "symlink" in out
    assert dest.is_symlink() and (target / "LiveBridge.py").is_file()
    assert read(target / "config.json")["token"]
    code, out, _ = run_uninstall(host)
    assert code == 0, out
    assert not dest.exists() and not dest.is_symlink()
    assert (target / "LiveBridge.py").is_file()


def test_malformed_desktop_config_is_never_clobbered(tmp_path):
    host = mac_host(tmp_path)
    folder = desktop_dir(host)
    folder.mkdir(parents=True)
    path = folder / "claude_desktop_config.json"
    path.write_text('{"mcpServers": {"a": 1},,}', encoding="utf-8")
    code, out, _ = run_install(host, ["--no-claude-code"])
    assert code == 0
    assert path.read_text(encoding="utf-8") == '{"mcpServers": {"a": 1},,}'
    assert "NOT modified" in out and '"livebridge"' in out and "warning" in out


def test_existing_custom_splice_entry_is_left_alone(tmp_path):
    host = mac_host(tmp_path, which={"npx": "/fake/npx"})
    mine = {"command": "my-splice", "args": ["--fancy"]}
    desktop = write_desktop_config(host, {"mcpServers": {"splice": mine}})
    run_install(host, ["--no-claude-code"])
    assert read(desktop)["mcpServers"]["splice"] == mine


def test_no_splice_and_missing_npx(tmp_path):
    host = mac_host(tmp_path)  # no npx on PATH
    desktop = write_desktop_config(host, {})
    code, out, _ = run_install(host, ["--no-claude-code"])
    assert code == 0
    assert "splice" not in read(desktop)["mcpServers"]
    assert "Add custom connector" in out
    code, out, runner = run_install(mac_host(tmp_path / "b", which={"claude": "/c", "npx": "/n"}),
                                    ["--no-splice"])
    assert not runner.commands("mcp.splice.com")


def test_desktop_config_is_created_when_path_is_forced(tmp_path):
    host = mac_host(tmp_path)
    forced = tmp_path / "somewhere" / "claude_desktop_config.json"
    code, out, _ = run_install(host, ["--no-claude-code", "--no-splice",
                                      "--desktop-config", str(forced)])
    assert code == 0, out
    assert list(read(forced)["mcpServers"]) == ["livebridge"]


def test_claude_desktop_missing_is_skipped(tmp_path):
    host = mac_host(tmp_path)
    code, out, _ = run_install(host, ["--no-claude-code"])
    assert code == 0 and "Claude Desktop not found" in out
    assert not desktop_dir(host).exists()


def test_claude_code_add_failure_is_a_warning_with_the_command(tmp_path):
    host = mac_host(tmp_path, which={"claude": "/fake/claude"})
    runner = FakeRunner(host, fail={"mcp add --scope user livebridge": 1})
    code, out, _ = run_install(host, ["--no-desktop", "--no-splice"], runner=runner)
    assert code == 0
    assert "WARNING" in out and "claude mcp add --scope user livebridge --" in out


def test_no_python_310_is_a_clear_error(tmp_path):
    host = mac_host(tmp_path, which={"python3": "/usr/bin/python3"})
    runner = FakeRunner(host, version="3.9.6")
    code, out, _ = run_install(host, ["--no-desktop", "--no-claude-code"], runner=runner)
    assert code == 1
    assert "no Python 3.10+ found" in out and "--uv" in out


def test_explicit_python_too_old(tmp_path):
    host = mac_host(tmp_path)
    runner = FakeRunner(host, versions={"/old/python": "3.9.6"})
    code, out, _ = run_install(host, ["--python", "/old/python"], runner=runner)
    assert code == 1 and "--python /old/python: Python 3.9.6" in out


def test_windows_py_launcher_is_used(tmp_path):
    host = win_host(tmp_path, which={"py": "C:/Windows/py.exe"})
    runner = FakeRunner(host, versions={sys.executable: None}, version="3.12.1")
    code, out, _ = run_install(host, ["--no-desktop", "--no-claude-code"], runner=runner)
    assert code == 0, out
    probe = runner.commands("C:/Windows/py.exe -3 -c")
    assert probe
    venv_cmd = runner.commands("-m venv")[0]
    assert venv_cmd[0] == "C:/Windows/py.exe"  # the probed sys.executable (fake echoes argv[0])


def test_pip_failure_explains_pep_668(tmp_path):
    host = mac_host(tmp_path)
    runner = FakeRunner(host, fail={"pip install": 1},
                        output={"pip install": "error: externally-managed-environment"})
    code, out, _ = run_install(host, ["--no-venv", "--no-desktop"], runner=runner)
    assert code == 1 and "PEP 668" in out


def test_no_venv_locates_the_scripts_folder(tmp_path):
    host = mac_host(tmp_path)
    scripts = tmp_path / "pyscripts"
    scripts.mkdir()
    (scripts / "livebridge-mcp").write_text("x", encoding="utf-8")
    runner = FakeRunner(host, create_exe=False, scripts_dir=scripts)
    code, out, _ = run_install(host, ["--no-venv", "--no-claude-code", "--no-desktop"],
                               runner=runner)
    assert code == 0, out
    assert read(host.home / ".livebridge" / "install.json")["mcp_command"] == \
        str(scripts / "livebridge-mcp")


def test_missing_executable_falls_back_to_python_m(tmp_path):
    host = mac_host(tmp_path)
    desktop = write_desktop_config(host, {})
    runner = FakeRunner(host, create_exe=False)
    code, out, _ = run_install(host, ["--no-claude-code", "--no-splice"], runner=runner)
    assert code == 0
    entry = read(desktop)["mcpServers"]["livebridge"]
    assert entry["command"].endswith("python") and entry["args"] == ["-m", "livebridge_mcp"]
    assert "WARNING" in out


def test_uv_mode(tmp_path):
    host = mac_host(tmp_path, which={"uv": "/fake/uv"})
    code, out, runner = run_install(host, ["--uv", "--no-desktop", "--no-claude-code"])
    assert code == 0, out
    venv = host.home / ".livebridge" / "venv"
    assert runner.commands("/fake/uv venv --seed --python")  # --seed: pip for later runs
    assert runner.commands("/fake/uv pip install --python %s -e" % (venv / "bin" / "python"))
    code, out, _ = run_install(mac_host(tmp_path / "x"), ["--uv"])
    assert code == 1 and "uv" in out


def test_no_pip_skips_pip(tmp_path):
    host = mac_host(tmp_path)
    venv_bin = host.home / ".livebridge" / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("x", encoding="utf-8")
    (venv_bin / "livebridge-mcp").write_text("x", encoding="utf-8")
    code, out, runner = run_install(host, ["--no-pip", "--no-desktop", "--no-claude-code"])
    assert code == 0, out
    assert not runner.commands("pip install")


def test_port_validation(tmp_path):
    code, out, _ = run_install(mac_host(tmp_path), ["--port", "70000"])
    assert code == 1 and "--port" in out


# ---------------------------------------------------------------------------
# re-runs keep what was set before (review: "a re-run silently resets settings")
# ---------------------------------------------------------------------------

def test_rerun_without_flags_keeps_lan_mode_eval_beacon_and_port(tmp_path):
    host = mac_host(tmp_path)
    code, out, _ = run_install(host, ["--network", "--no-allow-eval", "--port", "9890",
                                      "--no-desktop", "--no-claude-code"])
    assert code == 0, out
    code, out, _ = run_install(host, ["--no-desktop", "--no-claude-code"])  # the documented update
    assert code == 0, out
    config = read(remote_dir_mac(host) / "config.json")
    assert config["host"] == "0.0.0.0" and config["beacon"] is True
    assert config["allow_eval"] is False          # a security setting never flips back on
    assert config["port"] == 9890
    assert read(host.home / ".livebridge" / "config.json")["port"] == 9890
    assert "kept from the previous install" in out and "LAN mode" in out
    manifest = read(host.home / ".livebridge" / "install.json")
    assert manifest["network"] is True and manifest["port"] == 9890


def test_rerun_keeps_a_paired_or_persisted_mcp_host(tmp_path):
    host = win_host(tmp_path)
    run_install(host, ["--pair", "192.168.1.20", "--port", "9891", "--token", "abcdef123456",
                       "--no-desktop", "--no-claude-code", "--no-remote-script"])
    code, out, _ = run_install(host, ["--no-desktop", "--no-claude-code", "--no-remote-script"])
    assert code == 0, out
    assert read(host.home / ".livebridge" / "config.json") == {
        "host": "192.168.1.20", "port": 9891, "token": "abcdef123456"}
    # live_connect(persist=true) wrote another host: a re-run keeps it too
    user = host.home / ".livebridge" / "config.json"
    user.write_text(json.dumps({"host": "10.0.0.9", "port": 9880, "token": "abcdef123456"}),
                    encoding="utf-8")
    code, out, _ = run_install(host, ["--no-desktop", "--no-claude-code", "--no-remote-script"])
    assert read(user)["host"] == "10.0.0.9" and "MCP host 10.0.0.9" in out


def test_local_and_no_network_leave_lan_mode(tmp_path):
    host = mac_host(tmp_path)
    run_install(host, ["--network", "--no-mcp"])
    code, out, _ = run_install(host, ["--no-network", "--no-mcp"])
    assert code == 0, out
    config = read(remote_dir_mac(host) / "config.json")
    assert config["host"] == "127.0.0.1" and config["beacon"] is False
    # --local also points the MCP server back at this machine
    run_install(host, ["--network", "--no-mcp"])
    user = host.home / ".livebridge" / "config.json"
    data = read(user)
    data["host"] = "192.168.1.20"
    user.write_text(json.dumps(data), encoding="utf-8")
    code, out, _ = run_install(host, ["--local", "--no-mcp"])
    assert code == 0, out
    assert read(remote_dir_mac(host) / "config.json")["host"] == "127.0.0.1"
    assert read(user)["host"] == "127.0.0.1"


@pytest.mark.parametrize("extra", [["--network"], ["--pair", "10.0.0.5", "--token", "abcdefgh1"]])
def test_local_contradicts_network_and_pair(tmp_path, extra):
    code, out, _ = run_install(mac_host(tmp_path), ["--local"] + extra)
    assert code == 1 and "--local" in out


def test_explicit_flags_still_win_over_the_installed_config(tmp_path):
    host = mac_host(tmp_path)
    run_install(host, ["--network", "--no-allow-eval", "--no-mcp"])
    run_install(host, ["--allow-eval", "--no-beacon", "--port", "9895", "--no-mcp"])
    config = read(remote_dir_mac(host) / "config.json")
    assert config["host"] == "0.0.0.0" and config["allow_eval"] is True
    assert config["beacon"] is False and config["port"] == 9895


def test_installed_odd_token_is_kept_with_a_warning(tmp_path):
    host = mac_host(tmp_path)
    dest = remote_dir_mac(host)
    dest.mkdir(parents=True)
    (dest / "config.json").write_text(json.dumps({"token": "abc 1"}), encoding="utf-8")
    code, out, _ = run_install(host, ["--no-mcp"])
    assert code == 0, out
    assert read(dest / "config.json")["token"] == "abc 1"
    assert read(host.home / ".livebridge" / "config.json")["token"] == "abc 1"
    assert "unusual" in out and "--new-token" in out


# ---------------------------------------------------------------------------
# venv robustness (uv without pip, locked or foreign environments)
# ---------------------------------------------------------------------------

def _existing_venv(host, python_version=None):
    venv = host.home / ".livebridge" / "venv"
    python = host.venv_python(venv)
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("x", encoding="utf-8")
    (venv / "pyvenv.cfg").write_text("home = /x\n", encoding="utf-8")
    return venv, python


def test_venv_without_pip_is_installed_with_uv_when_available(tmp_path):
    host = mac_host(tmp_path, which={"uv": "/fake/uv"})
    venv, python = _existing_venv(host)
    runner = FakeRunner(host, fail={"-m pip --version": 1})
    code, out, _ = run_install(host, ["--no-desktop", "--no-claude-code"], runner=runner)
    assert code == 0, out
    assert runner.commands("/fake/uv pip install --python %s -e" % python)
    assert not runner.commands("-m pip install")


def test_venv_without_pip_gets_ensurepip_without_uv(tmp_path):
    host = mac_host(tmp_path)
    venv, python = _existing_venv(host)
    runner = FakeRunner(host, fail={"-m pip --version": 1})
    code, out, _ = run_install(host, ["--no-desktop", "--no-claude-code"], runner=runner)
    assert code == 0, out
    calls = [" ".join(c) for c in runner.calls]
    ensure = calls.index("%s -m ensurepip --upgrade" % python)
    pip = [i for i, c in enumerate(calls) if c.startswith("%s -m pip install" % python)]
    assert pip and pip[0] > ensure
    runner = FakeRunner(host, fail={"-m pip --version": 1, "ensurepip": 1})
    code, out, _ = run_install(host, ["--no-desktop", "--no-claude-code"], runner=runner)
    assert code == 1 and "no pip" in out and "--uv" in out


def test_rerun_after_a_uv_install_reuses_uv(tmp_path):
    host = mac_host(tmp_path, which={"uv": "/fake/uv"})
    run_install(host, ["--uv", "--no-desktop", "--no-claude-code"])
    assert read(host.home / ".livebridge" / "install.json")["uv"] is True
    code, out, runner = run_install(host, ["--no-desktop", "--no-claude-code"])
    assert code == 0, out
    assert runner.commands("/fake/uv pip install") and not runner.commands("-m pip install")
    assert "using uv again" in out


def test_locked_venv_on_reinstall_says_to_quit_claude(tmp_path, monkeypatch):
    host = win_host(tmp_path)
    venv, python = _existing_venv(host)
    runner = FakeRunner(host, versions={str(python): "3.9.13"})  # unusable -> recreate

    def locked(path):
        raise PermissionError(13, "[WinError 5] Access is denied", str(path))

    monkeypatch.setattr(install, "remove_tree", locked)
    code, out, _ = run_install(host, ["--no-desktop", "--no-claude-code"], runner=runner)
    assert code == 1
    assert "in use" in out and "quit Claude Desktop" in out
    assert venv.is_dir()


def test_foreign_venv_dir_is_never_deleted(tmp_path):
    host = mac_host(tmp_path)
    foreign = tmp_path / "myproject" / ".venv"
    (foreign / "bin").mkdir(parents=True)
    (foreign / "bin" / "python").write_text("x", encoding="utf-8")
    (foreign / "pyvenv.cfg").write_text("home = /x\n", encoding="utf-8")
    runner = FakeRunner(host, versions={str(foreign / "bin" / "python"): "3.9.6"})
    code, out, _ = run_install(host, ["--venv-dir", str(foreign), "--no-desktop",
                                      "--no-claude-code"], runner=runner)
    assert code == 1 and "not created by the LiveBridge installer" in out
    assert (foreign / "bin" / "python").is_file()
    assert not runner.commands("-m venv")


def test_uninstall_keeps_going_when_the_venv_is_locked(tmp_path, monkeypatch):
    host = win_host(tmp_path, which={"claude": "C:/claude.exe"})
    runner = FakeRunner(host)
    code, out, _ = run_install(host, runner=runner)
    assert code == 0, out
    venv = host.home / ".livebridge" / "venv"
    real_remove = install.remove_tree

    def remove(path):
        if Path(path) == venv:
            raise PermissionError(5, "[WinError 5] Access is denied", str(path))
        real_remove(path)

    monkeypatch.setattr(install, "remove_tree", remove)
    code, out, _ = run_uninstall(host, runner=runner)
    assert code == 1
    assert "in use" in out and "Quit Claude Desktop" in out and "uninstaller again" in out
    assert not remote_dir_win(host).exists()                        # other steps still ran
    assert not (host.home / ".livebridge" / "config.json").exists()
    assert (host.home / ".livebridge" / "install.json").is_file()   # kept for the re-run
    assert "====" in out                                           # the summary was printed
    monkeypatch.setattr(install, "remove_tree", real_remove)
    code, out, _ = run_uninstall(host, runner=runner)
    assert code == 0, out
    assert not venv.exists() and not (host.home / ".livebridge").exists()


# ---------------------------------------------------------------------------
# links, probes, Splice bookkeeping
# ---------------------------------------------------------------------------

def test_rmtree_handler_reraises_for_links_instead_of_silently_skipping():
    error = OSError("Cannot call rmtree on a symbolic link")
    with pytest.raises(OSError):
        install._make_writable_and_retry(os.path.islink, "/x", error)
    with pytest.raises(OSError):
        install._make_writable_and_retry(os.path.islink, "/x", (OSError, error, None))


def test_windows_junction_dev_install_is_left_alone(tmp_path, monkeypatch):
    host = win_host(tmp_path)
    dest = remote_dir_win(host)
    shutil.copytree(str(REPO / "remote_script" / "LiveBridge"), str(dest),
                    ignore=shutil.ignore_patterns("__pycache__"))
    (dest / "marker.txt").write_text("dev", encoding="utf-8")
    monkeypatch.setattr(os.path, "isjunction", lambda p: Path(p) == dest, raising=False)
    assert install.is_link_or_junction(dest)
    code, out, _ = run_install(host, ["--no-mcp"])
    assert code == 0, out
    assert "symlink/junction" in out and (dest / "marker.txt").is_file()
    unlinked = []
    monkeypatch.setattr(install, "remove_link", lambda p: unlinked.append(Path(p)))
    code, out, _ = run_uninstall(host, ["--no-mcp", "--no-desktop", "--no-claude-code"])
    assert code == 0, out
    assert unlinked == [dest] and (dest / "marker.txt").is_file()   # never deleted through it


@pytest.mark.skipif(os.name == "nt", reason="symlinks need admin rights on Windows")
def test_is_link_or_junction_on_real_links(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    assert install.is_link_or_junction(link) and not install.is_link_or_junction(target)
    install.remove_link(link)
    assert not link.exists() and target.is_dir()


def test_probe_reads_a_non_ascii_interpreter_path(tmp_path):
    host = win_host(tmp_path)
    exe = "C:\\Users\\\u0141ukasz\\AppData\\Local\\Programs\\Python\\Python312\\python.exe"

    def runner(argv, timeout=None):
        return CommandResult(0, "3.12.4\n%s\n" % json.dumps(exe))

    info = install.Ops(host, runner=runner, out=lambda line: None).probe_python(["py"])
    assert info.executable == exe and info.version == (3, 12, 4)


def test_real_probe_and_utf8_child_output():
    ops = install.Ops(install.Host(), out=lambda line: None)
    info = ops.probe_python([sys.executable])
    assert info is not None and os.path.samefile(info.executable, sys.executable)
    result = install.run_command([sys.executable, "-c",
                                  "import sys; print(sys.stdout.encoding); print('\\u266f')"])
    assert result.ok and result.stdout.split() == ["utf-8", "\u266f"]


def test_preexisting_splice_in_claude_code_survives_remove_splice(tmp_path):
    host = mac_host(tmp_path, which={"claude": "/fake/claude"})
    runner = FakeRunner(host)
    runner.claude_servers = {"splice"}          # the user's own registration
    run_install(host, ["--no-desktop"], runner=runner)
    manifest = read(host.home / ".livebridge" / "install.json")
    assert manifest["splice_claude_code"] is True
    assert manifest["splice_claude_code_added"] is False
    code, out, runner = run_uninstall(host, ["--remove-splice"], runner=runner)
    assert code == 0, out
    assert not runner.commands("mcp remove --scope user splice")
    assert "splice" in runner.claude_servers and "not added by the LiveBridge installer" in out


def test_splice_added_by_the_installer_stays_removable_after_a_rerun(tmp_path):
    host = mac_host(tmp_path, which={"claude": "/fake/claude"})
    runner = FakeRunner(host)
    run_install(host, ["--no-desktop"], runner=runner)
    run_install(host, ["--no-desktop"], runner=runner)   # now "already registered"
    assert read(host.home / ".livebridge" / "install.json")["splice_claude_code_added"] is True
    code, out, runner = run_uninstall(host, ["--remove-splice"], runner=runner)
    assert runner.commands("mcp remove --scope user splice") and "splice" not in \
        runner.claude_servers


# ---------------------------------------------------------------------------
# the skill (Claude Code + Claude Desktop)
# ---------------------------------------------------------------------------

def test_skill_is_installed_for_claude_code_and_zipped_for_claude_desktop(tmp_path):
    import zipfile

    host = mac_host(tmp_path)
    code, out, _ = run_install(host, ["--no-desktop", "--no-claude-code"])
    assert code == 0, out
    skill = host.home / ".claude" / "skills" / "livebridge"
    source = REPO / ".claude" / "skills" / "livebridge" / "SKILL.md"
    assert (skill / "SKILL.md").read_bytes() == source.read_bytes()
    zip_path = host.home / ".livebridge" / "livebridge-skill.zip"
    with zipfile.ZipFile(str(zip_path)) as archive:
        assert "livebridge/SKILL.md" in archive.namelist()
        assert archive.read("livebridge/SKILL.md") == source.read_bytes()
    assert "Capabilities -> Skills" in out and str(zip_path) in out
    manifest = read(host.home / ".livebridge" / "install.json")
    assert manifest["skill_dir"] == str(skill) and manifest["skill_zip"] == str(zip_path)
    before = zip_path.read_bytes()
    code, out, _ = run_install(host, ["--no-desktop", "--no-claude-code"])
    assert "unchanged: %s" % skill in out and "unchanged: %s" % zip_path in out
    assert zip_path.read_bytes() == before      # deterministic zip
    code, out, _ = run_uninstall(host)
    assert code == 0, out
    assert not skill.exists() and not zip_path.exists()
    assert (host.home / ".claude" / "skills").is_dir()   # only our skill goes


def test_skill_honours_claude_config_dir_and_windows_paths(tmp_path):
    host = win_host(tmp_path)
    custom = tmp_path / "claude-config"
    host.environ["CLAUDE_CONFIG_DIR"] = str(custom)
    code, out, _ = run_install(host, ["--no-desktop", "--no-claude-code"])
    assert code == 0, out
    assert (custom / "skills" / "livebridge" / "SKILL.md").is_file()
    assert not (host.home / ".claude").exists()


def test_foreign_skill_folder_and_no_skill_flag(tmp_path):
    host = mac_host(tmp_path)
    mine = host.home / ".claude" / "skills" / "livebridge"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text("---\nname: something-else\n---\n", encoding="utf-8")
    code, out, _ = run_install(host, ["--no-desktop", "--no-claude-code"])
    assert code == 0 and "not the LiveBridge skill" in out
    assert "something-else" in (mine / "SKILL.md").read_text(encoding="utf-8")
    code, out, _ = run_uninstall(host)
    assert (mine / "SKILL.md").is_file()
    host2 = mac_host(tmp_path / "b")
    code, out, _ = run_install(host2, ["--no-desktop", "--no-claude-code", "--no-skill"])
    assert code == 0 and not (host2.home / ".claude").exists()
    assert not (host2.home / ".livebridge" / "livebridge-skill.zip").exists()


# ---------------------------------------------------------------------------
# firewall / Local Network advice lands on the right machine
# ---------------------------------------------------------------------------

def test_live_machine_checklist_asks_only_for_incoming_tcp(tmp_path):
    host = win_host(tmp_path)
    code, out, _ = run_install(host, ["--network", "--no-mcp"])
    assert code == 0, out
    assert "INCOMING TCP 9880" in out and "outgoing and needs no rule here" in out
    assert "Allow TCP 9880 (and UDP 9881" not in out
    code, out, _ = run_install(mac_host(tmp_path / "m"), ["--network", "--no-mcp"])
    assert "Local Network" in out


def test_claude_machine_checklist_opens_udp_for_discovery(tmp_path):
    host = win_host(tmp_path)
    code, out, _ = run_install(host, ["--pair", "192.168.1.20", "--token", "abcdef123456",
                                      "--no-desktop", "--no-claude-code"])
    assert code == 0, out
    assert "-Direction Inbound -Protocol UDP -LocalPort 9881" in out
    code, out, _ = run_install(mac_host(tmp_path / "m"),
                               ["--pair", "192.168.1.20", "--token", "abcdef123456",
                                "--no-desktop", "--no-claude-code"])
    assert "Local Network" in out and "No route to host" in out


# ---------------------------------------------------------------------------
# compatibility with the Remote Script and MCP config loaders
# ---------------------------------------------------------------------------

def test_written_remote_config_loads_in_the_remote_script(tmp_path):
    from LiveBridge import config as remote_config  # conftest put remote_script on sys.path

    assert set(install.REMOTE_CONFIG_KEYS) == set(remote_config.DEFAULTS)
    host = mac_host(tmp_path)
    run_install(host, ["--network", "--no-mcp", "--port", "9891"])
    loaded = remote_config.load_config(str(remote_dir_mac(host) / "config.json"), environ={})
    written = read(remote_dir_mac(host) / "config.json")
    assert loaded.host == "0.0.0.0" and loaded.port == 9891
    assert loaded.token == written["token"] and loaded.requires_token and loaded.lan_mode
    assert loaded.beacon is True and loaded.allow_eval is True and loaded.name == "TestMac"


def test_written_user_config_loads_in_the_mcp_server(tmp_path):
    from livebridge_mcp import config as mcp_config

    host = mac_host(tmp_path)
    run_install(host, ["--pair", "192.168.1.9", "--token", "tok-12345678", "--no-mcp"])
    loaded = mcp_config.load_config(env={}, path=host.home / ".livebridge" / "config.json")
    assert (loaded["host"], loaded["port"], loaded["token"]) == ("192.168.1.9", 9880,
                                                                "tok-12345678")
    assert loaded["source"]["token"] == "file"


# ---------------------------------------------------------------------------
# uninstall
# ---------------------------------------------------------------------------

def test_uninstall_reverses_the_install(tmp_path):
    host = mac_host(tmp_path, which={"claude": "/fake/claude", "npx": "/fake/npx"})
    desktop = write_desktop_config(host, {"mcpServers": {"other": {"command": "o"}}})
    runner = FakeRunner(host)
    code, _, _ = run_install(host, runner=runner)
    assert code == 0
    code, out, runner = run_uninstall(host, runner=runner)
    assert code == 0, out
    assert not remote_dir_mac(host).exists()
    assert (remote_dir_mac(host).parent).is_dir()  # only our folder goes
    servers = read(desktop)["mcpServers"]
    assert "livebridge" not in servers and servers["other"] == {"command": "o"}
    assert "splice" in servers  # kept without --remove-splice
    assert runner.commands("/fake/claude mcp remove --scope user livebridge")
    assert not runner.commands("mcp remove --scope user splice")
    assert not (host.home / ".livebridge").exists()
    assert "Control Surface" in out


def test_uninstall_remove_splice_and_keep_config(tmp_path):
    host = mac_host(tmp_path, which={"claude": "/fake/claude", "npx": "/fake/npx"})
    desktop = write_desktop_config(host, {})
    runner = FakeRunner(host)
    run_install(host, runner=runner)
    code, out, runner = run_uninstall(host, ["--remove-splice", "--keep-config"], runner=runner)
    assert code == 0, out
    assert read(desktop)["mcpServers"] == {}
    assert runner.commands("mcp remove --scope user splice")
    assert (host.home / ".livebridge" / "config.json").is_file()
    assert not (host.home / ".livebridge" / "venv").exists()
    assert not (host.home / ".livebridge" / "install.json").exists()


def test_uninstall_keeps_a_foreign_splice_entry(tmp_path):
    host = mac_host(tmp_path)
    desktop = write_desktop_config(host, {"mcpServers": {
        "splice": {"command": "custom"}, "livebridge": {"command": "x"}}})
    code, out, _ = run_uninstall(host, ["--remove-splice"])
    assert code == 0
    assert read(desktop)["mcpServers"] == {"splice": {"command": "custom"}}


def test_uninstall_dry_run_removes_nothing(tmp_path):
    host = win_host(tmp_path, which={"claude": "C:/claude.exe"})
    write_desktop_config(host, {"mcpServers": {"other": {}}})
    run_install(host)
    before = snapshot_tree(tmp_path)
    code, out, runner = run_uninstall(host, ["--dry-run"])
    assert code == 0, out
    assert snapshot_tree(tmp_path) == before
    assert not runner.calls
    assert "[dry-run] remove" in out and "claude.exe mcp remove --scope user livebridge" in out


def test_uninstall_without_manifest_detects_and_refuses_foreign_folders(tmp_path):
    host = mac_host(tmp_path)
    dest = remote_dir_mac(host)
    dest.mkdir(parents=True)
    (dest / "something.txt").write_text("not ours", encoding="utf-8")
    code, out, _ = run_uninstall(host)
    assert code == 0
    assert (dest / "something.txt").is_file() and "left alone" in out


def test_uninstall_pip_mode(tmp_path):
    host = mac_host(tmp_path)
    scripts = tmp_path / "s"
    scripts.mkdir()
    (scripts / "livebridge-mcp").write_text("x", encoding="utf-8")
    runner = FakeRunner(host, create_exe=False, scripts_dir=scripts)
    run_install(host, ["--no-venv", "--no-desktop", "--no-claude-code"], runner=runner)
    code, out, runner = run_uninstall(host, ["--no-claude-code"])
    assert code == 0, out
    assert runner.commands("-m pip uninstall -y livebridge-mcp")


# ---------------------------------------------------------------------------
# wrappers
# ---------------------------------------------------------------------------

def test_shell_wrapper_passes_arguments_through():
    text = (INSTALLERS / "install.sh").read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash")
    assert 'exec "$PYTHON" "$TARGET" "$@"' in text and "uninstall.py" in text
    assert os.access(str(INSTALLERS / "install.sh"), os.X_OK) or os.name == "nt"


@pytest.mark.skipif(shutil.which("bash") is None or os.name == "nt", reason="needs bash")
def test_shell_wrapper_runs():
    subprocess.run(["bash", "-n", str(INSTALLERS / "install.sh")], check=True)
    result = subprocess.run(["bash", str(INSTALLERS / "install.sh"), "--version"],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0 and "install.py 0.1.0" in result.stdout
    result = subprocess.run(["bash", str(INSTALLERS / "install.sh"), "--uninstall", "--version"],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0 and "uninstall.py 0.1.0" in result.stdout


def test_powershell_wrapper_is_ascii_and_passes_arguments_through():
    raw = (INSTALLERS / "install.ps1").read_bytes()
    raw.decode("ascii")  # PowerShell 5.1 misreads BOM-less UTF-8; keep it ASCII
    text = raw.decode("ascii")
    assert "install.py" in text and "uninstall.py" in text
    assert "& $exe @prefix $target @passArgs" in text and "exit $LASTEXITCODE" in text
    assert "param(" not in text.lower().replace("no param() block", "")


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell not installed")
def test_powershell_wrapper_parses():
    command = ("$e=$null; [System.Management.Automation.Language.Parser]::ParseFile('%s', "
               "[ref]$null, [ref]$e) | Out-Null; if ($e.Count) { exit 1 }"
               % (INSTALLERS / "install.ps1"))
    subprocess.run(["pwsh", "-NoProfile", "-Command", command], check=True, timeout=60)


def test_installer_scripts_are_stdlib_and_python39_compatible():
    import ast

    allowed = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else None
    for name in ("install.py", "uninstall.py", "bundle.py"):
        tree = ast.parse((INSTALLERS / name).read_text(encoding="utf-8"), feature_version=(3, 9))
        if allowed is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = [node.module.split(".")[0]]
            else:
                continue
            for module in modules:
                assert module in allowed or module == "install", (name, module)
    ast.parse((REPO / "tests" / "integration_check.py").read_text(encoding="utf-8"),
              feature_version=(3, 9))


# ---------------------------------------------------------------------------
# integration_check.py (never against a real Live here)
# ---------------------------------------------------------------------------

def test_integration_check_reports_unreachable_live():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()  # nothing listens there now
    assert integration_check.main(["--port", str(port), "--token", "", "--json"]) == 2


def test_integration_check_token_lookup(tmp_path):
    assert integration_check.find_token({"LIVEBRIDGE_TOKEN": "fromenv"}) == "fromenv"
    config = tmp_path / ".livebridge" / "config.json"
    config.parent.mkdir()
    config.write_text('{"token": "fromfile"}', encoding="utf-8")
    assert integration_check.find_token({}, home=tmp_path) == "fromfile"
    assert integration_check.find_token({}, home=tmp_path / "nothing") == ""


def test_integration_check_sequence_against_the_stub_bridge(tcp_bridge, song, capsys):
    """Dry validation of the check's command/argument names against the stub-backed Remote
    Script (ephemeral port, fake Live) -- no real Live is involved."""
    tracks_before = len(song.tracks)
    code = integration_check.main(["--port", str(tcp_bridge.port), "--token", "",
                                   "--timeout", "10"])
    out = capsys.readouterr().out
    assert "FAIL" not in out, out
    assert code == 0, out
    for step in ("system.hello", "create temp MIDI track", "add notes", "read notes back",
                 "load instrument", "set a device parameter", "fire clip", "stop clip",
                 "delete temp track (cleanup)"):
        assert re.search(r"PASS\s+%s" % re.escape(step), out), (step, out)
    assert len(song.tracks) == tracks_before


def test_integration_check_beat_scenario_against_the_stub_bridge(tcp_bridge, song, capsys):
    """``--scenario beat`` chains the production commands on temporary tracks and removes
    them again; commands the stub cannot model are SKIPped, never FAILed."""
    tracks_before = len(song.tracks)
    code = integration_check.main(["--port", str(tcp_bridge.port), "--token", "",
                                   "--timeout", "10", "--scenario", "beat"])
    out = capsys.readouterr().out
    assert "FAIL" not in out, out
    assert code == 0, out
    for step in ("beat: create drum track", "beat: write drum pattern", "beat: write chords",
                 "beat: mixer set_many (dB)", "beat: delete drums track (cleanup)"):
        assert re.search(r"PASS\s+%s" % re.escape(step), out), (step, out)
    assert len(song.tracks) == tracks_before
    assert not [t for t in song.tracks if t.name.startswith("LB_")]


def test_integration_check_arrangement_scenario_against_the_stub_bridge(tcp_bridge, song,
                                                                         capsys):
    """``--scenario arrangement``: envelope copy (Live 12.4.5 rule: a track without devices
    keeps them), 16-beat resize with the 4-beat loop, move keeps the length; cleaned up."""
    import Live
    from live_stub_ext import arrangement_live

    uninstall = arrangement_live.install(Live, copy_envelopes="without_devices")
    try:
        tracks_before = len(song.tracks)
        code = integration_check.main(["--port", str(tcp_bridge.port), "--token", "",
                                       "--timeout", "10", "--no-play", "--scenario",
                                       "arrangement"])
        out = capsys.readouterr().out
    finally:
        uninstall()
    assert "FAIL" not in out, out
    assert code == 0, out
    for step in ("arr: duplicate keeps the envelope", "arr: resize to 16 beats (loop repeats)",
                 "arr: move keeps the length", "arr: delete track (cleanup)"):
        assert re.search(r"PASS\s+%s" % re.escape(step), out), (step, out)
    assert len(song.tracks) == tracks_before


# ---------------------------------------------------------------------------
# dev tools: UTF-8 output on Windows pipes, finding the installed Remote Script
# ---------------------------------------------------------------------------

def _cp1252_stdout(monkeypatch):
    """A stdout like Windows gives a piped Python before 3.15: cp1252, errors=strict."""
    import io

    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict", newline="\n")
    monkeypatch.setattr(sys, "stdout", stream)
    return raw, stream


def test_live_query_prints_non_cp1252_results_on_a_windows_pipe(monkeypatch):
    import live_query

    raw, stream = _cp1252_stdout(monkeypatch)
    monkeypatch.setattr(live_query, "call", lambda *a, **k: {
        "ok": True, "result": {"result": "C♯ → D♭ \U0001f941 鼓"}})
    assert live_query.main(["eval", "x"]) == 0
    stream.flush()
    printed = json.loads(raw.getvalue().decode("utf-8"))
    assert printed["result"]["result"] == "C♯ → D♭ \U0001f941 鼓"


def test_integration_check_prints_non_cp1252_details_on_a_windows_pipe(monkeypatch):
    raw, stream = _cp1252_stdout(monkeypatch)
    integration_check.utf8_stdout()
    integration_check.Report().add("PASS", "track", "♯\U0001f941")
    stream.flush()
    assert "♯\U0001f941" in raw.getvalue().decode("utf-8")


def test_remote_script_dirs_use_install_json_and_onedrive_business(tmp_path):
    import dump_live_api

    home = tmp_path / "home"
    recorded = tmp_path / "D" / "Live Library" / "Remote Scripts" / "LiveBridge"
    (home / ".livebridge").mkdir(parents=True)
    (home / ".livebridge" / "install.json").write_text(
        json.dumps({"remote_script_dir": str(recorded)}), encoding="utf-8")
    business = tmp_path / "OneDrive - Blub Media"
    dirs = dump_live_api.remote_script_dirs({"ONEDRIVECOMMERCIAL": str(business)}, str(home))
    assert dirs[0] == str(recorded)
    assert os.path.join(str(business), "Documents", "Ableton", "User Library", "Remote Scripts",
                        "LiveBridge") in dirs
    # the token is found in the OneDrive-for-Business copy
    target = business / "Documents" / "Ableton" / "User Library" / "Remote Scripts" / "LiveBridge"
    target.mkdir(parents=True)
    (target / "config.json").write_text('{"token": "from-business"}', encoding="utf-8")
    assert dump_live_api.find_token({"OneDriveCommercial": str(business)}, str(home)) == \
        "from-business"
    assert integration_check.find_token({"OneDriveCommercial": str(business)}, home=home) == \
        "from-business"
    explicit = dump_live_api.remote_script_dirs({"LIVEBRIDGE_SCRIPT_DIR": "/x/LiveBridge"},
                                                str(home))
    assert explicit[0] == "/x/LiveBridge"


def test_live_dev_installed_dir_prefers_the_install_record(tmp_path):
    import live_dev

    home = tmp_path / "home"
    recorded = tmp_path / "custom" / "LiveBridge"
    recorded.mkdir(parents=True)
    default = home / "Music" / "Ableton" / "User Library" / "Remote Scripts" / "LiveBridge"
    default.mkdir(parents=True)
    (home / ".livebridge").mkdir()
    (home / ".livebridge" / "install.json").write_text(
        json.dumps({"remote_script_dir": str(recorded)}), encoding="utf-8")
    assert live_dev.installed_dir({"HOME": str(home)}, str(home)) == str(recorded)
    (home / ".livebridge" / "install.json").unlink()
    assert live_dev.installed_dir({"HOME": str(home)}, str(home)) == str(default)
    with pytest.raises(SystemExit):
        live_dev.installed_dir({"HOME": str(tmp_path / "nobody")}, str(tmp_path / "nobody"))


# ---------------------------------------------------------------------------
# bundle.py (copying LiveBridge to the second computer)
# ---------------------------------------------------------------------------

def test_bundle_packs_what_the_other_computer_needs_and_nothing_secret(tmp_path):
    import zipfile

    import bundle

    source = tmp_path / "repo"
    for rel in ("remote_script/LiveBridge/__init__.py", "remote_script/LiveBridge/config.json",
                "remote_script/LiveBridge/__pycache__/x.cpython-311.pyc",
                "mcp_server/pyproject.toml", "mcp_server/livebridge_mcp.egg-info/PKG-INFO",
                "installers/install.py", "installers/install.sh", "docs/INSTALL.md",
                "tests/integration_check.py", ".claude/skills/livebridge/SKILL.md",
                ".claude/settings.local.json", ".venv/bin/python", ".git/HEAD",
                ".livebridge_work/x.json", "README.md", "dist/old.zip"):
        path = source / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    if os.name != "nt":
        (source / "installers" / "install.sh").chmod(0o644)  # built from a Windows checkout
    output = tmp_path / "out" / "LiveBridge.zip"
    files = bundle.build(output, root=source)
    assert set(files) == {"remote_script/LiveBridge/__init__.py", "mcp_server/pyproject.toml",
                          "installers/install.py", "installers/install.sh", "docs/INSTALL.md",
                          "tests/integration_check.py", ".claude/skills/livebridge/SKILL.md",
                          "README.md"}
    with zipfile.ZipFile(str(output)) as archive:
        names = archive.namelist()
        assert all(name.startswith("LiveBridge/") for name in names)
        mode = archive.getinfo("LiveBridge/installers/install.sh").external_attr >> 16
        assert mode & 0o111  # still executable after unzipping on macOS


def test_bundle_of_this_repository_has_the_installers_and_skill(tmp_path):
    import bundle

    files = bundle.collect()
    for rel in ("installers/install.py", "installers/uninstall.py", "installers/install.ps1",
                "remote_script/LiveBridge/__init__.py", "mcp_server/pyproject.toml",
                ".claude/skills/livebridge/SKILL.md", "tests/integration_check.py"):
        assert rel in files, rel
    assert not [f for f in files if "__pycache__" in f or f.endswith(".pyc")]
    assert bundle.version() != "0.0.0"
    assert bundle.main(["--output", str(tmp_path / "b.zip")]) == 0


# ---------------------------------------------------------------------------
# docs: tool names exist, firewall advice on the right machine
# ---------------------------------------------------------------------------

USER_DOCS = ("README.md", "docs/INSTALL.md", "docs/NETWORK.md", "docs/TROUBLESHOOTING.md",
             "docs/LIVE_API_NOTES.md", "docs/ARCHITECTURE.md", "docs/LIVE_API_VERIFIED.md",
             "docs/LIVE_TEST_REPORT.md", ".claude/skills/livebridge/SKILL.md")


def test_docs_only_name_tools_that_exist():
    """Catches names like the old ``livebridge_connect`` (the tool is ``live_connect``)."""
    tools = set(re.findall(r"^#### `(live_[a-z0-9_]+)`",
                           (REPO / "docs" / "TOOLS.md").read_text(encoding="utf-8"), re.M))
    assert "live_connect" in tools and "live_discover" in tools
    not_tools = {"live_stub", "live_stub_ext", "live_test"}  # folders
    problems = []
    for rel in USER_DOCS:
        text = (REPO / rel).read_text(encoding="utf-8")
        if "livebridge_connect" in text:
            problems.append((rel, "livebridge_connect"))
        for name in set(re.findall(r"(?<![\w/.])(live_[a-z0-9_]+)", text)):
            if name in tools or name in not_tools:
                continue
            if name.endswith("_") and any(tool.startswith(name) for tool in tools):
                continue  # a family such as live_simpler_*
            problems.append((rel, name))
    assert not problems, problems


def test_docs_put_the_udp_rule_on_the_claude_machine():
    network = (REPO / "docs" / "NETWORK.md").read_text(encoding="utf-8")
    assert "Only the Live machine needs incoming rules" not in network
    assert "Claude machine | **UDP 9881**" in network
    install_doc = (REPO / "docs" / "INSTALL.md").read_text(encoding="utf-8")
    firewall = install_doc[install_doc.index("## Firewall"):install_doc.index("## Verify")]
    live_part = firewall[:firewall.index("Windows Defender Firewall, Claude machine")]
    assert "-Protocol UDP" not in live_part and "-Protocol TCP -LocalPort 9880" in live_part
    assert "-Protocol UDP -LocalPort 9881" in firewall[len(live_part):]
    assert "The Claude side only makes outgoing connections" not in install_doc
    for rel in ("README.md", "docs/INSTALL.md", "docs/NETWORK.md", "docs/TROUBLESHOOTING.md"):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert "Local Network" in text, rel
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "UDP 9881 for discovery) in\nthe firewall on the Live machine" not in readme
    assert "the **Claude** machine needs incoming UDP 9881" in readme


def test_live_dev_sync_includes_owned_helpers_and_data_files():
    """``sync --modules plugin_racks`` also copies plugin_racks_lib.py and plugin_maps/*.json."""
    import live_dev

    assert live_dev.owned_helpers("plugin_racks") == ["plugin_racks_lib.py"]
    assert "plugin_racks_lib.py" not in live_dev.owned_helpers("clips")
    for core in ("server.py", "config.py", "compat.py"):
        assert core not in live_dev.owned_helpers("plugin_racks")
    data = live_dev.data_files()
    assert os.path.join("plugin_maps", "serum2_vst3.json") in data
    everything = live_dev.all_source_files()
    assert "config.json" not in everything and "plugin_racks_lib.py" in everything
    assert os.path.join("plugin_maps", "serum2fx_vst3.json") in \
        live_dev.expand_files(["plugin_maps"])
    with pytest.raises(SystemExit):
        live_dev.expand_files(["config.json"])


def test_gitignore_keeps_the_remote_script_token_out_of_git():
    """install.py writes the token-bearing config.json into the repo in the symlink setup."""
    ignore = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "remote_script/LiveBridge/config.json" in [line.strip() for line in ignore]
