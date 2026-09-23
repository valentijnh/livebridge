"""The public examples stay true to the code.

`examples/*.md` shows the tool calls Claude makes, as Python-style calls in ```python blocks
(`true`/`false`/`null` are plain names there, as in JSON). Every call must name a real MCP
tool and pass only arguments that tool accepts, so a renamed tool or argument breaks this
test instead of the examples. The README and the website may only mention real tools.
"""

from __future__ import annotations

import ast
import asyncio
import re
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "mcp_server") not in sys.path:
    sys.path.insert(0, str(REPO / "mcp_server"))

from livebridge_mcp.client import BridgeClient  # noqa: E402
from livebridge_mcp.server import create_app  # noqa: E402

_APP = create_app(BridgeClient(host="127.0.0.1", port=9, timeout=1.0))
TOOLS = {tool.name: tool for tool in asyncio.run(_APP.list_tools())}

EXAMPLES = sorted((REPO / "examples").glob("*.md"))
MENTIONS = [*EXAMPLES, REPO / "README.md", REPO / "examples" / "python" / "README.md",
            REPO / "site" / "index.html"]
#: `live_*` words that are folders or scripts in this repository, not tools.
NOT_TOOLS = {"live_stub", "live_stub_ext", "live_dev", "live_query", "live_test"}


def example_calls(path: Path) -> list[tuple[str, int, str, list[str]]]:
    """(file, line, tool, keyword names) of every `live_*(...)` call in ```python blocks."""
    text = path.read_text(encoding="utf-8")
    found = []
    for match in re.finditer(r"```python\n(.*?)```", text, re.S):
        first_line = text.count("\n", 0, match.start(1)) + 1
        # blocks inside list items are indented
        tree = ast.parse(textwrap.dedent(match.group(1)), filename=f"{path.name}:{first_line}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id.startswith("live_"):
                assert not node.args, f"{path.name}:{first_line + node.lineno - 1}: " \
                    f"{node.func.id} is called with positional arguments; use names"
                found.append((path.name, first_line + node.lineno - 1, node.func.id,
                               [kw.arg for kw in node.keywords]))
    return found


def test_example_calls_use_real_tools_and_arguments():
    calls = [call for path in EXAMPLES for call in example_calls(path)]
    assert len(EXAMPLES) >= 8 and len(calls) >= 40, (len(EXAMPLES), len(calls))
    problems = []
    for name, line, tool, keywords in calls:
        if tool not in TOOLS:
            problems.append(f"{name}:{line}: no tool {tool}")
            continue
        schema = getattr(TOOLS[tool], "input_schema", None) \
            or getattr(TOOLS[tool], "inputSchema", None) or {}  # mcp 2.x / 1.x
        accepted = set(schema.get("properties", {}))
        problems += [f"{name}:{line}: {tool} has no argument {kw!r}"
                     for kw in keywords if kw not in accepted]
    assert not problems, "\n".join(problems)


def test_readme_and_site_mention_only_real_tools():
    problems = []
    for path in MENTIONS:
        mentions = set(re.findall(r"\blive_[a-z_]+", path.read_text(encoding="utf-8")))
        for mention in mentions - NOT_TOOLS:
            if mention.endswith("_"):  # a family such as `live_simpler_*`
                if not any(tool.startswith(mention) for tool in TOOLS):
                    problems.append(f"{path.name}: no tool starts with {mention}")
            elif mention not in TOOLS:
                problems.append(f"{path.name}: no tool {mention}")
    assert not problems, "\n".join(sorted(problems))
