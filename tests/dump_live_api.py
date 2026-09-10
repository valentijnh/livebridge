"""Dump the REAL Live Python API from a running Ableton Live (dev tool, not a unit test).

Connects to the LiveBridge Remote Script, runs an introspection snippet through
``eval.python`` inside Live, and writes:

* ``docs/LIVE_API_DUMP_<version>.json`` - every class in the ``Live`` module with its
  properties (read/write), methods (Boost.Python signature docstrings) and enums.
* ``docs/LIVE_API_DUMP_<version>.md``   - the same, compact and human/LLM readable.
* A runtime section: Live/Python version, available framework packages, browser roots
  with their first-level children, the control surfaces Live has loaded.

Usage (stdlib only, works on macOS and Windows)::

    python tests/dump_live_api.py [--host 127.0.0.1] [--port 9880] [--token TOKEN]

The token defaults to the ``LIVEBRIDGE_TOKEN`` environment variable, else the one in the
installed Remote Script config.json -- found through ``~/.livebridge/install.json``, Live's
``Library.cfg`` or the default (and OneDrive-redirected) User Library locations, see
``remote_script_dirs`` -- else ``~/.livebridge/config.json``.
"""

import argparse
import json
import os
import socket
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

INTROSPECT = r'''
import json as _json, types as _types, sys as _sys, platform as _platform

def _doc(obj, limit):
    try:
        d = getattr(obj, "__doc__", None) or ""
    except Exception:
        return ""
    d = d.strip()
    cut = d.find("C++ signature")
    if cut > 0:
        d = d[:cut].rstrip()
    return d[:limit]

def _members(cls):
    res = {}
    for name in sorted(dir(cls)):
        if name.startswith("__") and name != "__init__":
            continue
        try:
            attr = getattr(cls, name)
        except Exception as e:
            res[name] = {"kind": "error", "doc": str(e)[:200]}
            continue
        tname = type(attr).__name__
        if isinstance(attr, property) or tname in ("property", "getset_descriptor"):
            res[name] = {"kind": "property",
                         "writable": getattr(attr, "fset", None) is not None,
                         "doc": _doc(attr, 400)}
        elif callable(attr) or tname in ("function", "builtin_function_or_method",
                                         "method_descriptor", "instancemethod"):
            res[name] = {"kind": "method", "doc": _doc(attr, 700)}
        else:
            try:
                val = repr(attr)[:160]
            except Exception:
                val = "<unrepr>"
            res[name] = {"kind": tname, "value": val}
    return res

_out = {"classes": {}, "other": {}}
_seen = set()

def _walk(mod, prefix, depth=0):
    if id(mod) in _seen or depth > 3:
        return
    _seen.add(id(mod))
    for name in sorted(dir(mod)):
        if name.startswith("_"):
            continue
        try:
            obj = getattr(mod, name)
        except Exception:
            continue
        full = prefix + "." + name
        if isinstance(obj, _types.ModuleType):
            # Inside Live the Boost.Python submodules are named "Clip", not "Live.Clip",
            # so recurse into every module reachable from the Live package (bounded depth),
            # but never into stdlib modules a submodule might expose.
            if getattr(obj, "__file__", None) is None or getattr(obj, "__name__", "").startswith("Live"):
                _walk(obj, full, depth + 1)
        elif isinstance(obj, type):
            entry = {"doc": _doc(obj, 500),
                     "bases": [b.__name__ for b in getattr(obj, "__bases__", ())],
                     "members": _members(obj)}
            vals = getattr(obj, "values", None)
            if isinstance(vals, dict):
                entry["enum"] = dict((str(k), str(v)) for k, v in vals.items())
            _out["classes"][full] = entry
        else:
            try:
                rep = repr(obj)[:160]
            except Exception:
                rep = "<unrepr>"
            _out["other"][full] = {"type": type(obj).__name__, "repr": rep, "doc": _doc(obj, 300)}

_walk(Live, "Live")

_rt = {}
try:
    _rt["live_version"] = "%d.%d.%d" % (app.get_major_version(), app.get_minor_version(), app.get_bugfix_version())
except Exception as e:
    _rt["live_version"] = "error: %s" % e
_rt["python"] = _sys.version
_rt["platform"] = _platform.platform()
_rt["frameworks"] = {}
for _m in ("_Framework", "_Framework.ControlSurface", "ableton", "ableton.v2", "ableton.v2.control_surface", "ableton.v3"):
    try:
        __import__(_m)
        _rt["frameworks"][_m] = True
    except Exception as e:
        _rt["frameworks"][_m] = "missing: %s" % e
_rt["browser_roots"] = {}
for _name in sorted(dir(browser)):
    if _name.startswith("_"):
        continue
    try:
        _obj = getattr(browser, _name)
    except Exception:
        continue
    if hasattr(_obj, "children") and hasattr(_obj, "name"):
        try:
            _kids = [c.name for c in list(_obj.children)[:40]]
        except Exception as e:
            _kids = ["error: %s" % e]
        _rt["browser_roots"][_name] = {"name": _obj.name, "uri": getattr(_obj, "uri", None), "children": _kids}
try:
    _rt["user_folders"] = [f.name for f in browser.user_folders]
except Exception as e:
    _rt["user_folders"] = "error: %s" % e
try:
    _rt["control_surfaces"] = [type(cs).__name__ if cs is not None else None for cs in app.control_surfaces]
except Exception as e:
    _rt["control_surfaces"] = "error: %s" % e
try:
    _rt["main_views"] = list(app.view.available_main_views())
except Exception as e:
    _rt["main_views"] = "error: %s" % e
_rt["song"] = {"tracks": len(song.tracks), "returns": len(song.return_tracks), "scenes": len(song.scenes), "tempo": song.tempo}
_rt["device_class_names_in_set"] = sorted(set(d.class_name for t in list(song.tracks) + list(song.return_tracks) + [song.master_track] for d in t.devices))
_out["runtime"] = _rt
'''


def _env(environ, key):
    """Case-insensitive on Windows-style environments (``OneDrive`` vs ``ONEDRIVE``)."""
    value = environ.get(key)
    if value is None:
        value = environ.get(key.upper())
    return value


def remote_script_dirs(environ=None, home=None):
    """Candidate folders of the installed LiveBridge Remote Script, most specific first.

    ``LIVEBRIDGE_SCRIPT_DIR``; ``remote_script_dir`` from ``~/.livebridge/install.json`` (the
    installer records the exact folder); the User Library from Live's ``Library.cfg`` (via
    ``installers/install.py``); then the default macOS / Windows / OneDrive-redirected
    ``Documents`` locations (``OneDrive``, ``OneDriveCommercial`` -- "OneDrive - Company" --
    and ``OneDriveConsumer``). Folders are returned whether or not they exist.
    """
    environ = os.environ if environ is None else environ
    home = home or _env(environ, "USERPROFILE") or _env(environ, "HOME") or os.path.expanduser("~")
    candidates = []
    explicit = _env(environ, "LIVEBRIDGE_SCRIPT_DIR")
    if explicit:
        candidates.append(explicit)
    try:
        with open(os.path.join(home, ".livebridge", "install.json"), encoding="utf-8-sig") as handle:
            recorded = json.load(handle).get("remote_script_dir")
        if isinstance(recorded, str) and recorded:
            candidates.append(recorded)
    except (OSError, ValueError, AttributeError):
        pass
    try:
        installers = os.path.join(ROOT, "installers")
        if installers not in sys.path:
            sys.path.insert(0, installers)
        import install  # stdlib-only installer module: Library.cfg parsing

        found, _, _ = install.find_remote_scripts_dir(
            install.Host(home=home, environ=dict(environ)))
        if found is not None:
            candidates.append(os.path.join(str(found), "LiveBridge"))
    except Exception:  # a dev tool: any problem here just means "no Library.cfg hint"
        pass
    tail = os.path.join("Ableton", "User Library", "Remote Scripts", "LiveBridge")
    candidates.append(os.path.join(home, "Music", tail))
    candidates.append(os.path.join(home, "Documents", tail))
    for key in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
        value = _env(environ, key)
        if value:
            candidates.append(os.path.join(value, "Documents", tail))
    candidates.append(os.path.join(home, "OneDrive", "Documents", tail))
    unique = []
    for path in candidates:
        if path not in unique:
            unique.append(path)
    return unique


def find_token(environ=None, home=None):
    """``LIVEBRIDGE_TOKEN``, else the token of the installed Remote Script config.json (see
    :func:`remote_script_dirs`), else ``~/.livebridge/config.json``; ``""`` when none."""
    environ = os.environ if environ is None else environ
    env = _env(environ, "LIVEBRIDGE_TOKEN")
    if env:
        return env
    home = home or _env(environ, "USERPROFILE") or _env(environ, "HOME") or os.path.expanduser("~")
    candidates = [os.path.join(folder, "config.json")
                  for folder in remote_script_dirs(environ, home)]
    candidates.append(os.path.join(home, ".livebridge", "config.json"))
    for path in candidates:
        try:
            with open(path, encoding="utf-8-sig") as handle:
                token = json.load(handle).get("token")
            if token:
                return token
        except (OSError, ValueError, AttributeError):
            continue
    return ""


def request(sock_file, sock, cmd, args, token, timeout):
    req = {"id": uuid.uuid4().hex[:8], "cmd": cmd, "args": args, "timeout": timeout}
    if token:
        req["token"] = token
    sock.sendall((json.dumps(req) + "\n").encode("utf-8"))
    line = sock_file.readline()
    if not line:
        raise SystemExit("connection closed by Live")
    resp = json.loads(line.decode("utf-8"))
    if not resp.get("ok"):
        raise SystemExit("%s failed: %s" % (cmd, json.dumps(resp.get("error"), indent=1)))
    return resp["result"]


def render_md(dump):
    rt = dump.get("runtime", {})
    lines = ["# Live Python API dump — Live %s" % rt.get("live_version"), "",
             "Generated from a running Live by `tests/dump_live_api.py` (introspection of the `Live` module via eval.python).",
             "Ground truth for this Live version. Signatures come from Boost.Python docstrings: "
             "`name( (Class)arg1, (type)arg2) -> ret`, where arg1 is self.", "",
             "## Runtime", "", "```json", json.dumps(rt, indent=1, ensure_ascii=False), "```", ""]
    for cls_name in sorted(dump["classes"]):
        cls = dump["classes"][cls_name]
        lines.append("## %s" % cls_name)
        if cls.get("bases"):
            lines.append("bases: %s" % ", ".join(cls["bases"]))
        if cls.get("doc"):
            lines.append("> " + cls["doc"].replace("\n", " ")[:300])
        if cls.get("enum"):
            lines.append("enum: " + ", ".join("%s=%s" % (v, k) for k, v in cls["enum"].items()))
        props = [(n, m) for n, m in cls["members"].items() if m.get("kind") == "property"]
        meths = [(n, m) for n, m in cls["members"].items() if m.get("kind") == "method" and not n.startswith("__")]
        if props:
            lines.append("")
            lines.append("properties:")
            for n, m in props:
                doc = (m.get("doc") or "").replace("\n", " ")
                lines.append("- `%s`%s — %s" % (n, " (rw)" if m.get("writable") else " (r)", doc[:220]))
        if meths:
            lines.append("")
            lines.append("methods:")
            for n, m in meths:
                if n.startswith("add_") and n.endswith("_listener") or n.startswith("remove_") and n.endswith("_listener") or n.endswith("_has_listener"):
                    continue
                doc = (m.get("doc") or "").replace("\n", " ")
                doc = " ".join(doc.split())
                lines.append("- %s" % (doc[:420] if doc else "`%s`" % n))
        lines.append("")
    if dump.get("other"):
        lines.append("## Module-level functions and constants")
        for n, o in sorted(dump["other"].items()):
            lines.append("- `%s` (%s) %s" % (n, o.get("type"), " ".join((o.get("doc") or o.get("repr") or "").split())[:200]))
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9880)
    parser.add_argument("--token", default=None)
    parser.add_argument("--out-dir", default=os.path.join(ROOT, "docs"))
    args = parser.parse_args()
    token = args.token if args.token is not None else find_token()
    sock = socket.create_connection((args.host, args.port), timeout=130)
    sock_file = sock.makefile("rb")
    hello = request(sock_file, sock, "system.hello", {}, token, 10)
    print("connected:", json.dumps(hello)[:300])
    result = request(sock_file, sock, "eval.python",
                     {"code": INTROSPECT, "expr": "_json.dumps(_out)", "reset": True}, token, 120)
    dump = json.loads(result["result"])
    version = dump.get("runtime", {}).get("live_version", "unknown").replace(" ", "_")
    os.makedirs(args.out_dir, exist_ok=True)
    json_path = os.path.join(args.out_dir, "LIVE_API_DUMP_%s.json" % version)
    md_path = os.path.join(args.out_dir, "LIVE_API_DUMP_%s.md" % version)
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(dump, handle, indent=1, ensure_ascii=False, sort_keys=True)
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write(render_md(dump))
    print("classes: %d, other: %d" % (len(dump["classes"]), len(dump["other"])))
    print("wrote", json_path)
    print("wrote", md_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
