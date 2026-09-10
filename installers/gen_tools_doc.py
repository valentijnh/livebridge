#!/usr/bin/env python3
"""Generate ``docs/TOOLS.md`` from the live code: every MCP tool and every bridge command.

Nothing is hand-maintained: the MCP tools come from ``create_app()`` (FastMCP's own tool list:
names, docstrings, JSON input schemas), the bridge commands from the Remote Script registry
(``registry.discover()`` — the same source ``system.commands`` answers from). Which tool runs
which command is found by scanning ``mcp_server/livebridge_mcp/tools/*.py`` for command names,
plus the documented composites in :data:`COVERED_BY` for commands that are reached through a
richer tool.

Usage (from the repo root, with the repo venv)::

    .venv/bin/python installers/gen_tools_doc.py            # rewrite docs/TOOLS.md
    .venv/bin/python installers/gen_tools_doc.py --check    # exit 1 if stale or inconsistent
    .venv/bin/python installers/gen_tools_doc.py --stdout   # print instead of writing

Importing the Remote Script outside Live needs the fake ``Live`` package in ``tests/live_stub``;
it is only imported, never driven. Requires the ``mcp`` package (the repo venv has it).
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import io
import logging
import re
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO / "mcp_server" / "livebridge_mcp" / "tools"
OUTPUT = REPO / "docs" / "TOOLS.md"

#: Bridge commands that no tool calls by name, and the tool that covers them instead.
COVERED_BY: dict[str, tuple[str, str]] = {
    "transport.set_tempo": ("live_transport_set", "tempo="),
    "transport.set_time_signature": ("live_transport_set", "signature="),
    "transport.back_to_arranger": ("live_transport_set", "back_to_arranger=false"),
    "transport.capture_midi": ("live_record_capture_midi", "same command"),
    "arrangement.loop": ("live_transport_set_loop", "same loop brace"),
    "arrangement.position": ("live_transport_set_position", "read: live_transport_get"),
    "arrangement.back_to_arranger": ("live_transport_set", "back_to_arranger=false"),
    "transport.continue": ("live_transport_play", 'mode="continue"'),
    "transport.toggle": ("live_transport_play", 'mode="toggle"'),
    "transport.redo": ("live_transport_undo", "redo=true"),
    "scenes.rename": ("live_scene_set", "name="),
    "scenes.select": ("live_view_select", "scene="),
    "scenes.stop_all": ("live_transport_stop_all_clips", "same effect"),
    "view.is_visible": ("live_view_state", "visible map"),
    "view.set_detail_clip": ("live_view_select", "clip= / slot=, show=true"),
    "view.toggle_browser": ("live_view_show", 'view="Browser", hotswap=true'),
    "browser.cache": ("live_browser_search", "refresh=true"),
    "notes.theory": ("live_command_call", 'cmd="notes.theory" — chord speller (symbols, Roman '
                     'numerals, degrees in a key), note names, key notes (key=, scale=true)'),
    "song.summary": ("live_set_snapshot", "include_tracks=false"),
    "system.status": ("live_status", "include_server=true"),
    "system.reload": ("live_command_call", 'cmd="system.reload" — developer hot reload'),
}

#: BridgeClient methods that send a command without naming it in the tool module.
CLIENT_METHODS = {"hello": "system.hello", "ping": "system.ping"}

#: Tool names that deliberately do not follow ``live_<area>_<verb>``.
NAME_EXCEPTIONS = {
    "live_status": "entry point (system)",
    "live_connect": "system",
    "live_discover": "system",
    "live_commands": "system",
    "live_log": "system",
}

_TOOL_NAME_RE = re.compile(r"^live_[a-z0-9]+_[a-z0-9_]+$")


# --------------------------------------------------------------------------- loading

def _prepare_path() -> None:
    for sub in ("tests/live_stub", "remote_script", "mcp_server"):
        path = str(REPO / sub)
        if path not in sys.path:
            sys.path.insert(0, path)


def load_commands() -> list[Any]:
    """Every registered bridge command (``CommandSpec``), sorted by name."""
    _prepare_path()
    from LiveBridge import registry  # noqa: E402 - needs the sys.path above

    registry.discover()
    return registry.all_commands()


def load_tools() -> list[Any]:
    """Every MCP tool as FastMCP reports it (name, description, input schema)."""
    _prepare_path()
    from livebridge_mcp.client import BridgeClient  # noqa: E402
    from livebridge_mcp.server import create_app  # noqa: E402

    logging.getLogger("livebridge_mcp").setLevel(logging.WARNING)
    app = create_app(BridgeClient(host="127.0.0.1", port=9, timeout=0.1))
    return list(asyncio.run(app.list_tools()))


def _schema(tool: Any) -> dict[str, Any]:
    return getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None) or {}


# --------------------------------------------------------------------------- static scan

#: Filled by :func:`scan_tool_modules`: tool names defined in more than one module.
duplicates: list[str] = []


class _FunctionInfo:
    def __init__(self, name: str, module: str) -> None:
        self.name = name
        self.module = module
        self.strings: set[str] = set()
        self.calls: set[str] = set()


def scan_tool_modules(command_names: set[str]) -> tuple[dict[str, str], dict[str, set[str]],
                                                         dict[str, str]]:
    """Static scan of ``tools/*.py``.

    Returns:
        ``(tool -> module, tool -> {commands it can send}, module -> docstring first line)``.
    """
    tool_module: dict[str, str] = {}
    tool_commands: dict[str, set[str]] = {}
    module_doc: dict[str, str] = {}
    duplicates.clear()
    for path in sorted(TOOLS_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        module = path.stem
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module_doc[module] = (ast.get_docstring(tree) or "").strip().split("\n\n")[0] \
            .replace("\n", " ")
        functions: dict[str, _FunctionInfo] = {}
        module_strings: dict[str, set[str]] = {}

        # module-level constants such as _VIEW_COMMANDS = {...}
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                found = {n.value for n in ast.walk(node) if isinstance(n, ast.Constant)
                         and isinstance(n.value, str) and n.value in command_names}
                for target in targets:
                    if isinstance(target, ast.Name) and found:
                        module_strings[target.id] = found

        def visit(node: ast.AST) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    info = _FunctionInfo(child.name, module)
                    # tool_error(..., cmd="x.y") only labels an error; it sends nothing
                    labels = {id(kw.value) for sub in ast.walk(child)
                              if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                              and sub.func.id == "tool_error"
                              for kw in sub.keywords if kw.arg == "cmd"}
                    for sub in ast.walk(child):
                        if id(sub) in labels:
                            continue
                        if isinstance(sub, ast.Constant) and isinstance(sub.value, str) \
                                and sub.value in command_names:
                            info.strings.add(sub.value)
                        elif isinstance(sub, ast.Name) and sub.id in module_strings:
                            info.strings |= module_strings[sub.id]
                        elif isinstance(sub, ast.Call):
                            func = sub.func
                            if isinstance(func, ast.Name):
                                info.calls.add(func.id)
                            elif isinstance(func, ast.Attribute):
                                if func.attr in CLIENT_METHODS and \
                                        isinstance(func.value, ast.Name) and \
                                        func.value.id == "bridge":
                                    info.strings.add(CLIENT_METHODS[func.attr])
                    functions.setdefault(child.name, info)
                visit(child)

        visit(tree)

        def reach(name: str, seen: set[str]) -> set[str]:
            info = functions.get(name)
            if info is None or name in seen:
                return set()
            seen.add(name)
            found = set(info.strings)
            for called in info.calls:
                found |= reach(called, seen)
            return found

        for name in functions:
            if name.startswith("live_"):
                if name in tool_module and tool_module[name] != module:
                    duplicates.append(f"tool {name} is defined in both tools/{tool_module[name]}.py "
                                      f"and tools/{module}.py (the later one silently wins)")
                tool_module[name] = module
                tool_commands[name] = reach(name, set())
    return tool_module, tool_commands, module_doc


# --------------------------------------------------------------------------- analysis

def first_line(text: str) -> str:
    """The first paragraph of a docstring, on one line."""
    paragraph = (text or "").strip().split("\n\n")[0]
    return " ".join(part.strip() for part in paragraph.splitlines()).strip()


def _type_of(prop: dict[str, Any]) -> str:
    if "anyOf" in prop:
        parts = [_type_of(p) for p in prop["anyOf"]]
        return " | ".join(dict.fromkeys(parts))
    kind = prop.get("type")
    if kind == "array":
        items = prop.get("items") or {}
        inner = _type_of(items) if items else "any"
        return f"list[{inner}]"
    if kind == "object":
        return "object"
    if kind is None:
        return "any"
    return {"integer": "int", "number": "float", "string": "str", "boolean": "bool",
            "null": "null"}.get(kind, str(kind))


def _default(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return f'"{value}"'
    return repr(value)


def tool_args(tool: Any) -> list[dict[str, Any]]:
    schema = _schema(tool)
    required = set(schema.get("required") or [])
    args = []
    for name, prop in (schema.get("properties") or {}).items():
        args.append({"name": name, "type": _type_of(prop), "required": name in required,
                     "default": None if name in required else _default(prop.get("default"))})
    return args


def analyse() -> dict[str, Any]:
    """Everything the document and the consistency check need."""
    commands = load_commands()
    tools = load_tools()
    names = {spec.name for spec in commands}
    tool_module, tool_commands, module_doc = scan_tool_modules(names)
    tool_by_name = {t.name: t for t in tools}

    command_tools: dict[str, list[str]] = {name: [] for name in names}
    for tool_name, used in tool_commands.items():
        if tool_name not in tool_by_name:
            continue
        for cmd in used:
            command_tools[cmd].append(tool_name)

    problems: list[str] = list(duplicates)
    for tool in tools:
        if tool.name not in tool_module:
            problems.append(f"tool {tool.name} is registered but not found in tools/*.py")
        if not (tool.description or "").strip():
            problems.append(f"tool {tool.name} has no docstring")
        if tool.name not in NAME_EXCEPTIONS and not _TOOL_NAME_RE.match(tool.name):
            problems.append(f"tool {tool.name} does not follow live_<area>_<verb>")
    for spec in commands:
        if not spec.doc:
            problems.append(f"command {spec.name} has no docstring")
        if not command_tools[spec.name] and spec.name not in COVERED_BY:
            problems.append(f"command {spec.name} has no MCP tool (add one or a COVERED_BY entry)")
    for cmd, (tool_name, _how) in COVERED_BY.items():
        if cmd not in names:
            problems.append(f"COVERED_BY names unknown command {cmd}")
        if tool_name not in tool_by_name:
            problems.append(f"COVERED_BY names unknown tool {tool_name}")
    return {"commands": commands, "tools": tools, "tool_module": tool_module,
            "tool_commands": tool_commands, "module_doc": module_doc,
            "command_tools": command_tools, "problems": problems}


# --------------------------------------------------------------------------- rendering

def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render(data: dict[str, Any]) -> str:
    commands = data["commands"]
    tools = data["tools"]
    tool_module = data["tool_module"]
    tool_commands = data["tool_commands"]
    command_tools = data["command_tools"]
    module_doc = data["module_doc"]
    out = io.StringIO()
    write = out.write

    by_module: dict[str, list[Any]] = {}
    for tool in tools:
        by_module.setdefault(tool_module.get(tool.name, "?"), []).append(tool)
    mutating = sum(1 for spec in commands if spec.mutating)
    namespaces = sorted({spec.namespace for spec in commands})

    write("# LiveBridge tools reference\n\n")
    write("<!-- Generated by installers/gen_tools_doc.py — do not edit by hand. "
          "Regenerate with: .venv/bin/python installers/gen_tools_doc.py -->\n\n")
    write(f"**{len(tools)} MCP tools** in {len(by_module)} modules, backed by "
          f"**{len(commands)} bridge commands** in {len(namespaces)} namespaces "
          f"({mutating} of them mutating = one undo step each).\n\n")
    write("Conventions shared by every tool (see `docs/ARCHITECTURE.md` §5 and "
          "`remote_script/LiveBridge/resolve.py`):\n\n")
    write("- `track`: index into `song.tracks`, name (exact, case-insensitive, unique prefix, "
          "then a unique fuzzy match), return letter `\"A\"`, `\"master\"`, `\"selected\"` or a "
          "LOM path.\n")
    write("- `slot`: scene index or scene name. `clip`: LOM path (clip, clip slot or "
          "arrangement clip), clip name or `\"selected\"`. `device` / `parameter`: index, name "
          "or LOM path.\n")
    write("- Times: numbers are beats (quarter notes). Song positions, loop points and clip "
          "lengths also take `\"17.1.1\"` strings = bars.beats.sixteenths (1-based positions, "
          "0-based lengths such as `\"4.0.0\"`); note times inside a clip are clip-relative "
          "beats.\n")
    write("- `detail`: `\"minimal\"` | `\"summary\"` | `\"full\"`. Paged results carry "
          "`total`, `offset`, `count` and `next_offset` (only when more follow).\n")
    write("- Errors are `{\"error\": \"<one line>\", \"type\": ..., \"cmd\": ...}`; types: "
          "`connection`, `auth`, `bad_args`, `not_found`, `invalid_state`, `unsupported`, "
          "`timeout`, `forbidden`, `internal`.\n\n")

    write("## Contents\n\n")
    for module in sorted(by_module):
        anchor = f"module-{module}"
        write(f"- [{module}](#{anchor}) — {len(by_module[module])} tools\n")
    write("- [Bridge commands](#bridge-commands)\n\n")

    write("## MCP tools\n\n")
    for module in sorted(by_module):
        write(f"<a id=\"module-{module}\"></a>\n\n### {module}\n\n")
        if module_doc.get(module):
            write(f"{module_doc[module]}\n\n")
        for tool in by_module[module]:
            write(f"#### `{tool.name}`\n\n")
            write(f"{first_line(tool.description or '')}\n\n")
            args = tool_args(tool)
            if args:
                write("| Argument | Type | Default |\n|---|---|---|\n")
                for arg in args:
                    default = "**required**" if arg["required"] else f"`{arg['default']}`"
                    write(f"| `{arg['name']}` | {_cell(arg['type'])} | {default} |\n")
                write("\n")
            else:
                write("No arguments.\n\n")
            used = sorted(tool_commands.get(tool.name, ()))
            if used:
                write("Bridge: " + ", ".join(f"`{c}`" for c in used) + "\n\n")
            else:
                write("Bridge: none (runs in the MCP server)\n\n")

    write("## Bridge commands\n\n")
    write("Every command registered in the Remote Script (`system.commands` returns the same "
          "list at runtime). *M* = mutating (one undo step).\n\n")
    write("| Command | M | Parameters | MCP tool | Description |\n|---|---|---|---|---|\n")
    for spec in commands:
        params = ", ".join(
            p["name"] if p.get("required") else f"{p['name']}={_default(p.get('default'))}"
            for p in spec.params)
        users = sorted(command_tools.get(spec.name) or [])
        if users:
            tool_text = ", ".join(f"`{u}`" for u in users)
        elif spec.name in COVERED_BY:
            tool_name, how = COVERED_BY[spec.name]
            tool_text = f"`{tool_name}` ({how})"
        else:
            tool_text = "—"
        write(f"| `{spec.name}` | {'M' if spec.mutating else ''} | {_cell(params)} | "
              f"{_cell(tool_text)} | {_cell(first_line(spec.doc))} |\n")
    write("\n")
    return out.getvalue()


# --------------------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true",
                        help="exit 1 when docs/TOOLS.md is stale or a consistency rule fails")
    parser.add_argument("--stdout", action="store_true", help="print instead of writing")
    parser.add_argument("--output", type=Path, default=OUTPUT, help="target file")
    args = parser.parse_args(argv)

    data = analyse()
    text = render(data)
    for problem in data["problems"]:
        print(f"PROBLEM: {problem}", file=sys.stderr)
    if args.stdout:
        sys.stdout.write(text)
        return 1 if data["problems"] else 0
    if args.check:
        current = args.output.read_text(encoding="utf-8") if args.output.exists() else ""
        stale = current != text
        if stale:
            print(f"{args.output} is out of date — run installers/gen_tools_doc.py",
                  file=sys.stderr)
        return 1 if (stale or data["problems"]) else 0
    args.output.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {args.output} ({len(data['tools'])} tools, {len(data['commands'])} commands)")
    return 1 if data["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
