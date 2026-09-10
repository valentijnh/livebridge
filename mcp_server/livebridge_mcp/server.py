"""Builds the LiveBridge MCP application.

`create_app(bridge)` returns a FastMCP server named "LiveBridge" with every tool module in
:mod:`livebridge_mcp.tools` registered. Modules are discovered with :mod:`pkgutil`, so adding a
tool module is just adding a file — no registry list to edit.

Every tool module must expose::

    def register(mcp, bridge) -> None:
        @mcp.tool()
        def live_something(...) -> Any:
            \"\"\"Docstring Claude reads.\"\"\"
            return bridge_call(bridge, "namespace.command", {...})

`mcp` is the FastMCP instance, `bridge` the shared :class:`~livebridge_mcp.client.BridgeClient`.
A module that fails to import or raises in `register()` is logged and skipped — one broken module
must never take the whole server down.

Toolsets: every tool definition costs context tokens in every conversation, so the server can
register a subset of the tool modules — ``--toolsets``, ``LIVEBRIDGE_TOOLSETS`` or ``"toolsets"`` in
``~/.livebridge/config.json``, e.g. ``core`` or ``core,automation`` or ``all,-view,-cues`` (see
:func:`select_tool_modules` and :data:`TOOLSETS`). Hidden modules' bridge commands stay reachable
through ``live_command_call``.

Strict arguments: after registration :func:`enforce_declared_arguments` makes every tool reject
argument names it does not declare (FastMCP silently drops them otherwise, so a call would quietly
do less than asked) and advertises ``additionalProperties: false`` in each input schema.

Compact results: FastMCP serialises a returned dict with ``indent=2`` and splits a returned list
into one content block per item, which costs 1.7-2x the characters (a 512-note table becomes one
number per line). :func:`register_tool_modules` therefore hands each module a registrar whose
``tool()`` decorator wraps the function so data results leave as one compact JSON text
(:func:`compact_result`); strings and content objects pass through unchanged. The function object
the module keeps is the original, so modules can still call their own tools directly.

Skill text: the production workflow in ``.claude/skills/livebridge/SKILL.md`` is also served to
clients without the skill (Claude Desktop without the uploaded zip) as the MCP resource
``livebridge://skill`` and the prompt ``livebridge_workflow`` (:func:`register_skill`).

Compatibility: `mcp` 1.x exposes ``mcp.server.fastmcp.FastMCP``; `mcp` 2.x renamed it to
``mcp.server.mcpserver.MCPServer`` with the same decorator/`run` surface. Both are supported.
"""

from __future__ import annotations

import difflib
import functools
import importlib
import inspect
import json
import logging
import pkgutil
from pathlib import Path
from typing import Any, Callable, Iterable

from .client import BridgeClient

__all__ = [
    "ALWAYS_LOADED",
    "FastMCP",
    "INSTRUCTIONS",
    "SERVER_NAME",
    "SKILL_PATH",
    "SKILL_URI",
    "TOOLSETS",
    "available_tool_modules",
    "compact_result",
    "create_app",
    "enforce_declared_arguments",
    "register_skill",
    "register_tool_modules",
    "select_tool_modules",
    "skill_text",
]

log = logging.getLogger(__name__)

try:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP  # type: ignore[import-not-found]
except ModuleNotFoundError:  # mcp 2.x renamed FastMCP -> MCPServer
    from mcp.server.mcpserver import MCPServer as FastMCP  # type: ignore[import-not-found]

SERVER_NAME = "LiveBridge"

INSTRUCTIONS = """\
LiveBridge controls a running Ableton Live 12 through its LiveBridge Remote Script.

How to work:
1. Start with `live_status`. It says whether Live is reachable, which version/edition it is and
   whether a token is needed. If it reports a connection problem, tell the user to start Live with
   the LiveBridge control surface enabled (Preferences -> Link, Tempo & MIDI -> Control Surface),
   or use `live_discover` to find Live on the network and `live_connect` to point at it.
2. Get the lay of the land with `live_set_snapshot` (whole set in one call) before changing
   anything. Work from the `path` fields in those summaries: they are canonical LOM paths such as
   `song.tracks[2]`, `song.tracks[2].devices[0].parameters[3]`,
   `song.tracks[0].clip_slots[3].clip`, `song.return_tracks[0]`, `song.master_track`,
   `song.scenes[1]`. Indices are 0-based and shift when tracks/clips are added or deleted, so
   re-read a summary after structural changes instead of reusing stale indices.
3. Prefer the curated tools (`live_transport_*`, `live_tracks_*`, `live_clip_*`, `live_device_*`,
   `live_browser_*`, ...) — they are compact and validated. For audio files on disk and Splice
   downloads use `live_sample_import` and `live_splice_*` (Splice search/download itself goes
   through the official Splice MCP server).
4. `live_lom_get`, `live_lom_set`, `live_lom_call`, `live_lom_describe`, `live_lom_children` and
   `live_eval_python` are the escape hatch: anything in the Live Object Model is reachable through
   them even without a curated tool. Use `live_lom_describe` to discover what an object offers,
   `live_commands()` for the index of bridge commands this Live installation supports (then
   `live_commands(namespace="clips")` for their parameters) and `live_command_call` to run one
   that has no curated tool.

Conventions:
- Every mutating command is one undo step in Live, so the user can undo your work.
- The same argument forms work in every tool: `track` = index, name (exact/prefix/fuzzy), return
  letter "A", "master", "selected" or a path; `slot` = scene index or scene name; `clip` = path,
  clip name or "selected"; `device` = index, name or path; `parameter` = index, name or path.
- Times: a number is beats (quarter notes). Song positions, loop points and clip lengths also take
  a string "17.1.1" = bars.beats.sixteenths (1-based positions, 0-based lengths such as "4.0.0" =
  4 bars) in the song's time signature. Note times inside a clip are clip-relative beats.
- Paged results carry `total`, `offset`, `count` and `next_offset` (only when more follow).
- Results are compact JSON; pass `detail="full"` or paging arguments only when you need more.
- Errors come back as `{"error": "<one line>", "type": "<kind>"}` — read the line, it says what to
  do. `type: "connection"` means Live is not reachable; `type: "not_found"` usually means an index
  moved; `type: "unsupported"` means this Live version/edition genuinely cannot do it.
- Argument names differ between tools (check each tool's schema): a name a tool does not declare
  is rejected before anything runs, and the error lists the accepted names.
- Live's file export, freeze/flatten and menu commands are not in the Live API. Bounce or
  resample a section in real time with `live_record_resample`; say what is impossible instead of
  inventing a workaround.
- For the full production workflow (beat, bass, chords, mix, arrangement, plug-ins, Splice) read
  the `livebridge://skill` resource or the `livebridge_workflow` prompt.
"""

#: The Claude skill with the production workflow (the MCP server is installed editable from the
#: repo, so the file sits three levels above this module).
SKILL_PATH = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "livebridge" / "SKILL.md"
SKILL_URI = "livebridge://skill"

_SKILL_FALLBACK = """# LiveBridge workflow

The full workflow file (.claude/skills/livebridge/SKILL.md) is not installed next to this MCP
server. Work from the server instructions: start with `live_status`, read the set with
`live_set_snapshot`, prefer the curated `live_*` tools, use `live_commands()` and
`live_command_call` for commands without a tool, and check each result before the next step.
"""


def skill_text(path: Path | None = None) -> str:
    """The text of the LiveBridge skill (SKILL.md), or a short fallback when it is missing.

    Args:
        path: The file to read; defaults to :data:`SKILL_PATH`.

    Returns:
        The file content (UTF-8, BOM tolerated), or a fallback text pointing at the server
        instructions. Never raises.
    """
    target = path or SKILL_PATH
    try:
        return target.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return _SKILL_FALLBACK


def register_skill(mcp: Any, path: Path | None = None) -> bool:
    """Serve the skill as the resource ``livebridge://skill`` and the prompt ``livebridge_workflow``.

    The file is read on every request, so an updated skill is served without a restart.

    Args:
        mcp: The FastMCP/MCPServer app.
        path: The skill file (default :data:`SKILL_PATH`).

    Returns:
        True when both were registered; False (logged) when this `mcp` version lacks the
        decorators. Never raises.
    """
    try:
        @mcp.resource(SKILL_URI, name="livebridge_skill", mime_type="text/markdown",
                      description="LiveBridge production workflow for Claude (SKILL.md): "
                                  "beat, bass, chords, mix, arrangement, plug-ins, Splice")
        def livebridge_skill() -> str:
            return skill_text(path)

        @mcp.prompt(name="livebridge_workflow",
                    description="Load the LiveBridge production workflow (how to build and "
                                "mix a track in Ableton Live with the live_* tools)")
        def livebridge_workflow() -> str:
            return skill_text(path)
    except Exception:  # pragma: no cover - an mcp without resources/prompts must not break startup
        log.exception("could not register the livebridge://skill resource / prompt")
        return False
    return True


def compact_result(result: Any) -> Any:
    """A tool result as one compact JSON text (strings and MCP content objects pass through).

    Args:
        result: What a tool function returned: usually a dict or list.

    Returns:
        ``json.dumps(result, separators=(",", ":"), ensure_ascii=False, default=str)`` for data;
        the value unchanged for ``str``, ``None`` and non-JSON objects (content blocks, images).
    """
    if result is None or isinstance(result, str):
        return result
    if isinstance(result, (dict, list, tuple, int, float, bool)):
        try:
            return json.dumps(result, separators=(",", ":"), ensure_ascii=False, default=str)
        except (TypeError, ValueError):  # pragma: no cover - circular data: let FastMCP try
            return result
    return result


def _compact_tool(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a tool function so its result leaves as compact JSON (keeps name, doc, signature)."""
    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            return compact_result(await fn(*args, **kwargs))

        return async_wrapper

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return compact_result(fn(*args, **kwargs))

    return wrapper


class _CompactRegistrar:
    """The ``mcp`` handed to tool modules: ``tool()`` registers a compact-result wrapper, every
    other attribute is the real app's (read at call time, so late attributes work)."""

    def __init__(self, app: Any) -> None:
        self._app = app

    def tool(self, *args: Any, **kwargs: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        register = self._app.tool(*args, **kwargs)

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            register(_compact_tool(fn))
            return fn  # the module keeps the original (direct calls still get data)

        return decorator

    def __getattr__(self, name: str) -> Any:
        return getattr(self._app, name)


#: Modules registered whatever the toolset: status/connect/commands/command_call and the LOM
#: escape hatch are the lifeline that keeps every hidden command reachable.
ALWAYS_LOADED: tuple[str, ...] = ("system", "lom")

_CORE: tuple[str, ...] = ("system", "lom", "transport", "tracks", "clips", "notes", "devices",
                          "mixer", "scenes", "browser")

#: Named toolsets (profiles) → tool modules. ``all`` (the default) is every module in
#: :mod:`livebridge_mcp.tools`, including ones added later. Module names are accepted too.
TOOLSETS: dict[str, tuple[str, ...]] = {
    # Smallest useful set (~1/3 of the full tool definitions): transport, tracks, clips + notes;
    # everything else through the LOM tools and live_command_call.
    "minimal": ("system", "lom", "transport", "tracks", "clips", "notes"),
    # Everyday session work: transport, tracks, clips + notes, devices, mixer, scenes, browser.
    "core": _CORE,
    # core + arrangement, automation, racks, plug-ins, samples/Splice, recording and routing.
    "production": _CORE + ("arrangement", "automation", "racks", "plugins", "plugin_racks",
                           "samples", "splice", "record", "routing"),
}

_ALL_NAMES = ("all", "full", "*")


def available_tool_modules() -> list[str]:
    """Names of the tool modules in :mod:`livebridge_mcp.tools` (sorted, helpers excluded)."""
    from . import tools as tools_pkg

    return sorted(m.name for m in pkgutil.iter_modules(tools_pkg.__path__)
                  if not m.name.startswith("_"))


def select_tool_modules(spec: str | Iterable[str] | None,
                        available: Iterable[str] | None = None) -> tuple[list[str], list[str]]:
    """Resolve a toolset spec into the tool modules to register.

    Args:
        spec: ``None``/``""``/``"all"`` for every module, or comma-separated items — profile
            names from :data:`TOOLSETS` (``minimal``, ``core``, ``production``), ``all``, module
            names (``automation``, ``view``, ...) and exclusions prefixed with ``-`` (``-view``,
            ``-cues``). A spec made only of exclusions starts from ``all``. Case-insensitive; a
            list of items works too.
        available: The existing module names; defaults to :func:`available_tool_modules`.

    Returns:
        ``(selected, unknown)``: the sorted module names to register (always including
        :data:`ALWAYS_LOADED`) and the spec items that matched nothing (logged by the caller).

    Example: ``select_tool_modules("core,automation")`` → core modules + ``automation``.
    """
    names = sorted(available if available is not None else available_tool_modules())
    if spec is None:
        items: list[str] = []
    elif isinstance(spec, str):
        items = [part.strip().lower() for part in spec.replace(";", ",").split(",")]
    else:
        items = [str(part).strip().lower() for part in spec]
    items = [item for item in items if item]

    def expand(item: str) -> list[str] | None:
        if item in _ALL_NAMES:
            return list(names)
        if item in TOOLSETS:
            return [name for name in TOOLSETS[item] if name in names]
        if item in names:
            return [item]
        return None

    include = [item for item in items if not item.startswith("-")]
    exclude = [item[1:].strip() for item in items if item.startswith("-")]
    selected: set[str] = set() if include else set(names)
    unknown: list[str] = []
    for item in include:
        modules = expand(item)
        if modules is None:
            unknown.append(item)
        else:
            selected.update(modules)
    if include and not selected:  # nothing valid was named: never start a useless server
        selected = set(names)
    for item in exclude:
        modules = expand(item)
        if modules is None:
            unknown.append("-" + item)
        else:
            selected.difference_update(modules)
    selected.update(name for name in ALWAYS_LOADED if name in names)
    return sorted(selected), unknown


def register_tool_modules(mcp: Any, bridge: BridgeClient,
                          only: Iterable[str] | None = None) -> list[str]:
    """Import the modules in :mod:`livebridge_mcp.tools` and call their ``register(mcp, bridge)``.

    Args:
        mcp: The FastMCP instance to register tools on.
        bridge: The shared bridge client handed to every module.
        only: Module names to register (see :func:`select_tool_modules`); ``None`` = all.

    Returns:
        The names of the modules that registered successfully, sorted.

    Gotcha: modules are imported in alphabetical order; a module without a `register` function is
    skipped with a warning (it may be a helper module).
    """
    from . import tools as tools_pkg

    wanted = set(only) if only is not None else None
    registered: list[str] = []
    for module_info in sorted(pkgutil.iter_modules(tools_pkg.__path__), key=lambda m: m.name):
        name = module_info.name
        if name.startswith("_"):
            continue
        if wanted is not None and name not in wanted:
            continue
        full_name = f"{tools_pkg.__name__}.{name}"
        try:
            module = importlib.import_module(full_name)
        except Exception:
            log.exception("LiveBridge: could not import tool module %s — skipped", full_name)
            continue
        register = getattr(module, "register", None)
        if not callable(register):
            log.warning("LiveBridge: tool module %s has no register(mcp, bridge) — skipped", full_name)
            continue
        try:
            register(_CompactRegistrar(mcp), bridge)
        except Exception:
            log.exception("LiveBridge: register() failed for %s — skipped", full_name)
            continue
        registered.append(name)
    return registered


#: Well-known argument-name mix-ups between tool modules → the names to suggest instead (only
#: suggestions the tool really declares are shown).
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "color": ("color_index",),
    "colour": ("color", "color_index"),
    "color_index": ("color",),
    "pan": ("panning",),
    "panning": ("pan",),
    "active": ("activator", "enabled", "on"),
    "activator": ("active", "enabled", "on"),
    "enabled": ("active", "activator", "on"),
    "length": ("length_bars", "length_beats", "duration"),
    "duration": ("length",),
    "position": ("time", "start", "start_time"),
    "time": ("position", "start", "start_time"),
    "start": ("time", "position", "start_time"),
    "start_time": ("time", "start", "position"),
    "index": ("slot", "scene", "track", "device"),
    "name": ("query",),
    "query": ("name",),
}


def _unknown_argument_message(tool_name: str, unknown: list[str], accepted: list[str]) -> str:
    hints: list[str] = []
    for name in unknown:
        close = [c for c in _SYNONYMS.get(name.lower(), ()) if c in accepted]
        close += [c for c in difflib.get_close_matches(name, accepted, n=2, cutoff=0.6)
                  if c not in close]
        if close:
            hints.append(f"'{name}' → did you mean " + " or ".join(f"'{c}'" for c in close) + "?")
    listed = ", ".join(accepted) if accepted else "(none)"
    plural = "s" if len(unknown) > 1 else ""
    message = (f"{tool_name} has no argument{plural} "
               + ", ".join(f"'{n}'" for n in unknown)
               + f" — nothing was done. Accepted arguments: {listed}.")
    if hints:
        message += " " + " ".join(hints)
    return message


def _strict_model(tool_name: str, model: Any) -> Any:
    """A subclass of a tool's pydantic argument model that rejects undeclared argument names."""
    from pydantic import ConfigDict, model_validator

    accepted_names: set[str] = set()
    listed: list[str] = []
    for field_name, field in model.model_fields.items():
        public = field.alias or field_name
        accepted_names.update({field_name, public})
        listed.append(public)

    def _reject_undeclared(cls: Any, data: Any) -> Any:
        if isinstance(data, dict):
            unknown = [str(key) for key in data if key not in accepted_names]
            if unknown:
                raise ValueError(_unknown_argument_message(tool_name, unknown, listed))
        return data

    namespace = {
        "__module__": model.__module__,
        "__qualname__": getattr(model, "__qualname__", model.__name__),
        "model_config": ConfigDict(**{**dict(model.model_config), "extra": "forbid"}),
        "_reject_undeclared": model_validator(mode="before")(classmethod(_reject_undeclared)),
    }
    # Same class name as the original so validation errors still read "... for <tool>Arguments".
    return type(model)(model.__name__, (model,), namespace)


def _registered_tools(mcp: Any) -> list[Any]:
    manager = getattr(mcp, "_tool_manager", None)
    if manager is None or not hasattr(manager, "list_tools"):
        return []
    try:
        return list(manager.list_tools())
    except Exception:  # pragma: no cover - defensive
        log.exception("could not list registered tools")
        return []


def enforce_declared_arguments(mcp: Any) -> int:
    """Make every registered tool reject argument names it does not declare.

    FastMCP/MCPServer build each tool's argument model with pydantic's default
    ``extra="ignore"``: a misspelt or cross-module name (``color`` for ``color_index``, ``pan`` for
    ``panning``, ``length`` for ``length_bars``) is dropped silently and the call does less than
    asked. This swaps each argument model for a strict subclass whose error names the unknown
    argument(s), lists the accepted ones and suggests the likely intended name, and sets
    ``additionalProperties: false`` on the advertised input schema.

    Args:
        mcp: The FastMCP/MCPServer app, after all tools are registered.

    Returns:
        The number of tools made strict. Works with `mcp` 1.x and 2.x (both keep tools in
        ``app._tool_manager`` with ``fn_metadata.arg_model`` and ``parameters``); on an
        unrecognised layout it logs and leaves the tool as it was — never raises.
    """
    count = 0
    for tool in _registered_tools(mcp):
        try:
            metadata = tool.fn_metadata
            model = metadata.arg_model
            if getattr(model, "__livebridge_strict__", False):
                count += 1
                continue
            strict = _strict_model(tool.name, model)
            strict.__livebridge_strict__ = True  # type: ignore[attr-defined]
            try:
                metadata.arg_model = strict
            except (TypeError, ValueError, AttributeError):  # frozen/validated model
                object.__setattr__(metadata, "arg_model", strict)
            parameters = getattr(tool, "parameters", None)
            if isinstance(parameters, dict):
                parameters["additionalProperties"] = False
            count += 1
        except Exception:  # pragma: no cover - a future mcp layout must not break startup
            log.exception("could not make tool %s strict — unknown arguments stay ignored",
                          getattr(tool, "name", "?"))
    return count


def create_app(bridge: BridgeClient | None = None, config: dict[str, Any] | None = None,
               toolsets: str | Iterable[str] | None = None) -> Any:
    """Create the LiveBridge FastMCP application with the tool modules registered.

    Args:
        bridge: The bridge client to use. When omitted, one is built from `config` (or from the
            merged user configuration when that is omitted too).
        config: Config dict as returned by :func:`livebridge_mcp.config.load_config`. Builds the
            bridge when `bridge` is None and supplies ``config["toolsets"]`` when `toolsets` is
            None.
        toolsets: Which tool modules to register (see :func:`select_tool_modules`); ``None`` falls
            back to the config, then to every module.

    Returns:
        The FastMCP/MCPServer instance. It carries extra attributes for tests and tools:
        ``app.bridge`` (the client), ``app.tool_modules`` (registered module names) and
        ``app.hidden_tool_modules`` (modules left out by the toolset).
    """
    if bridge is None:
        if config is None:
            from .config import load_config

            config = load_config()
        bridge = BridgeClient.from_config(config)
    if toolsets is None and config is not None:
        toolsets = config.get("toolsets")

    from . import __version__

    available = available_tool_modules()
    selected, unknown = select_tool_modules(toolsets, available)
    for item in unknown:
        log.warning("LiveBridge: toolset item %r matches no profile or tool module — ignored "
                    "(profiles: all, %s; modules: %s)", item, ", ".join(TOOLSETS),
                    ", ".join(available))
    hidden = [name for name in available if name not in selected]
    instructions = INSTRUCTIONS
    if hidden:
        instructions += (
            "\nThis server runs with a reduced tool set (toolsets setting). Tool modules left out: "
            + ", ".join(hidden) + ". Their bridge commands still work through "
            "`live_command_call` — see `live_commands(namespace=...)`.\n"
        )

    try:  # mcp 2.x reports the version in serverInfo
        mcp = FastMCP(name=SERVER_NAME, instructions=instructions, version=__version__)
    except (TypeError, ValueError):  # mcp 1.x FastMCP: no version argument (settings reject it)
        mcp = FastMCP(name=SERVER_NAME, instructions=instructions)
    modules = register_tool_modules(mcp, bridge, only=selected)
    strict = enforce_declared_arguments(mcp)
    register_skill(mcp)
    try:
        mcp.bridge = bridge  # type: ignore[attr-defined]
        mcp.tool_modules = modules  # type: ignore[attr-defined]
        mcp.hidden_tool_modules = hidden  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - a future FastMCP could forbid attributes
        log.debug("could not attach bridge to the MCP app")
    log.info("LiveBridge MCP ready (%d tool modules, %d strict tools: %s%s)", len(modules), strict,
             ", ".join(modules), f"; hidden: {', '.join(hidden)}" if hidden else "")
    return mcp
