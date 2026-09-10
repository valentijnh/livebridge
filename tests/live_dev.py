"""Dev loop against a running Live (not a unit test): copy repo code into Live's
LiveBridge Remote Script folder and hot-reload it via ``system.reload``.

    python tests/live_dev.py where                       # show the installed dev copy
    python tests/live_dev.py sync                         # copy everything, reload all handlers + helpers
    python tests/live_dev.py sync --modules clips notes   # copy + reload only these handler modules
                                                          # (+ the top-level helper files they import,
                                                          #  e.g. plugin_racks_lib.py, and data files
                                                          #  such as plugin_maps/*.json)
    python tests/live_dev.py sync --modules clips --helpers  # also copy + reload lom/serialize/compat/resolve
    python tests/live_dev.py sync --modules plugin_racks --files plugin_maps  # + extra files/folders
    python tests/live_dev.py reload [--modules a b]       # reload without copying
    python tests/live_dev.py restart                      # macOS: quit Live (answers "Don't Save"), relaunch in background, wait for LiveBridge

Never overwrites the installed ``config.json`` (it holds the token). Changes to
server.py / dispatcher.py / LiveBridge.py / config.py / registry.py are copied by a
full ``sync`` but only take effect after Live is restarted (the command says so).
Works on macOS and Windows (OneDrive-redirected Documents included).
"""

import argparse
import ast
import glob
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "remote_script", "LiveBridge")
sys.path.insert(0, HERE)
from live_query import call  # noqa: E402
from dump_live_api import remote_script_dirs  # noqa: E402

CORE_NEEDS_RESTART = ("server.py", "dispatcher.py", "config.py", "registry.py", "__init__.py", "log.py")
CORE_HOT_SWAPPABLE = ("LiveBridge.py",)  # Context class is hot-swapped by system.reload core=True
HELPERS = ("compat.py", "lom.py", "serialize.py", "resolve.py")


def installed_dir(environ=None, home=None):
    """The installed LiveBridge Remote Script folder that ``sync`` copies into.

    ``LIVEBRIDGE_SCRIPT_DIR`` > ``remote_script_dir`` in ``~/.livebridge/install.json`` > the
    User Library in Live's ``Library.cfg`` > the default macOS / Windows / OneDrive-redirected
    locations (``dump_live_api.remote_script_dirs``). The first existing folder wins.
    """
    candidates = remote_script_dirs(environ, home)
    for path in candidates:
        if os.path.isdir(path):
            return path
    raise SystemExit("LiveBridge dev copy not found (tried %s); set LIVEBRIDGE_SCRIPT_DIR" % candidates)


def _same(a, b):
    try:
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return fa.read() == fb.read()
    except OSError:
        return False


def copy_files(rel_paths, dest):
    changed = []
    for rel in rel_paths:
        src = os.path.join(SRC, rel)
        dst = os.path.join(dest, rel)
        if not os.path.isfile(src):
            raise SystemExit("no such source file: %s" % src)
        if _same(src, dst):
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
        changed.append(rel.replace(os.sep, "/"))
    return changed


#: Never copied: the installed config.json holds the token (and the host/LAN settings).
NEVER_COPIED = ("config.json", "config.local.json")
_SKIPPED_SUFFIXES = (".pyc", ".pyo")


def _wanted(rel):
    name = os.path.basename(rel)
    if os.path.dirname(rel) == "" and name in NEVER_COPIED:
        return False
    return not name.endswith(_SKIPPED_SUFFIXES) and name != ".DS_Store"


def all_source_files():
    """Every .py file of the Remote Script plus its data files (plugin_maps/*.json, ...)."""
    out = []
    for base, dirs, files in os.walk(SRC):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            rel = os.path.relpath(os.path.join(base, name), SRC)
            if name.endswith(".py") or (os.path.dirname(rel) and _wanted(rel)):
                out.append(rel)
    return sorted(out)


def data_files():
    """Non-Python files in the Remote Script's sub folders (e.g. ``plugin_maps/*.json``)."""
    return [rel for rel in all_source_files() if not rel.endswith(".py")]


def owned_helpers(module):
    """Top-level helper files handler ``module`` imports (``from .. import plugin_racks_lib``).

    Core files that need a Live restart and the shared helpers (``--helpers``) are left out.
    """
    path = os.path.join(SRC, "handlers", module + ".py")
    try:
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), path)
    except (OSError, SyntaxError):
        return []
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 2:
            if node.module:
                names.add(node.module.split(".")[0])
            else:
                names.update(alias.name for alias in node.names)
    skip = set(CORE_NEEDS_RESTART) | set(CORE_HOT_SWAPPABLE) | set(HELPERS)
    return sorted(name + ".py" for name in names
                  if os.path.isfile(os.path.join(SRC, name + ".py")) and name + ".py" not in skip)


def expand_files(entries):
    """Relative files/folders (under remote_script/LiveBridge) -> relative file paths."""
    out = []
    for entry in entries or []:
        rel = os.path.normpath(entry)
        full = os.path.join(SRC, rel)
        if os.path.isdir(full):
            out.extend(r for r in all_source_files() if r.startswith(rel + os.sep))
        elif os.path.isfile(full) and _wanted(rel):
            out.append(rel)
        else:
            raise SystemExit("no such (copyable) file or folder in %s: %s" % (SRC, entry))
    return out


def _mac_click(x, y):
    """Left-click at screen point (x, y) with CoreGraphics events (needs Accessibility)."""
    import ctypes
    cg = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")

    class CGPoint(ctypes.Structure):
        _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

    cg.CGEventCreateMouseEvent.restype = ctypes.c_void_p
    cg.CGEventCreateMouseEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint32, CGPoint, ctypes.c_uint32]
    cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    for etype in (5, 1, 2):  # move, left down, left up
        event = cg.CGEventCreateMouseEvent(None, etype, CGPoint(x, y), 0)
        cg.CGEventPost(0, event)
        cf.CFRelease(event)
        time.sleep(0.06)


def _osa(script, timeout=10):
    try:
        proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout.strip()
    except subprocess.TimeoutExpired:
        return 1, ""


def _live_app_name():
    apps = sorted(glob.glob("/Applications/Ableton Live 1*.app"))
    return os.path.splitext(os.path.basename(apps[-1]))[0] if apps else "Ableton Live 12 Suite"


def _live_running():
    return subprocess.run(["pgrep", "-f", "Ableton Live.*/Contents/MacOS/Live$"], capture_output=True).returncode == 0


def restart_live(wait=180):
    """Quit Live (discarding unsaved changes in the scratch set), relaunch it in the
    background and wait until LiveBridge answers. macOS only."""
    if sys.platform != "darwin":
        raise SystemExit("restart is implemented for macOS only; on Windows close Live (Don't Save) and start it again")
    app = _live_app_name()
    quitter = subprocess.Popen(["osascript", "-e", 'tell application "%s" to quit' % app],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 60
    answered = False
    while _live_running() and time.time() < deadline:
        time.sleep(1.0)
        code, out = _osa('tell application "System Events" to tell process "Live" to get {position, size} of '
                         '(first window whose subrole is "AXDialog")')
        if code == 0 and out:
            try:
                x, y, w, h = [float(v) for v in out.replace(" ", "").split(",")]
            except ValueError:
                continue
            _osa('tell application "%s" to activate' % app)
            time.sleep(0.5)
            _mac_click(x + 0.15 * w, y + 0.807 * h)   # the left "Don't Save" button
            answered = True
            time.sleep(1.5)
    try:
        quitter.wait(timeout=5)
    except subprocess.TimeoutExpired:
        quitter.kill()
    if _live_running():
        raise SystemExit("Live did not quit within 60 s (a dialog may need attention)")
    subprocess.run(["open", "-g", "-a", app])
    deadline = time.time() + wait
    while time.time() < deadline:
        time.sleep(2)
        try:
            resp = call("system.hello", {}, timeout=5)
            if resp.get("ok"):
                return {"restarted": True, "discarded_unsaved_changes": answered, "hello": resp["result"]}
        except OSError:
            continue
    raise SystemExit("Live restarted but LiveBridge did not answer within %d s" % wait)


def main():
    parser = argparse.ArgumentParser(description="Copy LiveBridge code into Live and hot-reload it")
    parser.add_argument("mode", choices=["where", "sync", "reload", "restart"])
    parser.add_argument("--modules", nargs="*", default=None, help="handler module names (e.g. clips notes)")
    parser.add_argument("--helpers", action="store_true", help="also copy/reload compat, lom, serialize, resolve")
    parser.add_argument("--core", action="store_true", help="also reload LiveBridge.py and hot-swap the Context class")
    parser.add_argument("--files", nargs="*", default=None,
                        help="extra files/folders under remote_script/LiveBridge to copy (e.g. plugin_maps)")
    args = parser.parse_args()
    if args.mode == "restart":
        print(json.dumps(restart_live(), indent=1)[:1500])
        return 0
    dest = installed_dir()
    if args.mode == "where":
        print(dest)
        return 0
    changed = []
    if args.mode == "sync":
        if args.modules is None:
            changed = copy_files(all_source_files(), dest)
        else:
            rels = [os.path.join("handlers", m + ".py") for m in args.modules]
            for module in args.modules:
                rels += [h for h in owned_helpers(module) if h not in rels]
            if args.helpers:
                rels += [h for h in HELPERS if os.path.isfile(os.path.join(SRC, h))]
            rels += [r for r in data_files() + expand_files(args.files) if r not in rels]
            changed = copy_files(rels, dest)
    core_changed = any(os.path.basename(c) in CORE_HOT_SWAPPABLE for c in changed)
    helper_changed = any("/" not in c and c.endswith(".py") and c not in CORE_HOT_SWAPPABLE
                         for c in changed)
    reload_args = {"helpers": bool(args.helpers or args.modules is None or core_changed
                                   or helper_changed),
                   "core": bool(args.core or core_changed)}
    if args.modules is not None:
        reload_args["modules"] = args.modules
    resp = call("system.reload", reload_args, timeout=60)
    restart = sorted(set(os.path.basename(c) for c in changed) & set(CORE_NEEDS_RESTART))
    print(json.dumps({"copied": changed, "reload": resp.get("result") if resp.get("ok") else resp.get("error"),
                      "restart_live_needed_for": restart}, indent=1))
    return 0 if resp.get("ok") and not (resp.get("result") or {}).get("failed") else 1


if __name__ == "__main__":
    sys.exit(main())
