"""Tests for the MCP server core: bridge client, config, discovery, errors and the core tools.

Self-sufficient: it puts `mcp_server/` and `tests/` on `sys.path` itself, so it runs whether or not
`livebridge-mcp` is pip-installed and whether or not `tests/conftest.py` exists yet.
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
_REPO = _TESTS_DIR.parent
for _path in (str(_REPO / "mcp_server"), str(_TESTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from fake_bridge import FakeBridge, free_tcp_port, free_udp_port  # noqa: E402

from livebridge_mcp import config as config_mod  # noqa: E402
from livebridge_mcp import discovery as discovery_mod  # noqa: E402
from livebridge_mcp.__main__ import build_parser  # noqa: E402
from livebridge_mcp.client import BridgeClient  # noqa: E402
from livebridge_mcp.errors import BridgeError, error_payload, friendly_error  # noqa: E402
from livebridge_mcp.server import INSTRUCTIONS, create_app  # noqa: E402


# --------------------------------------------------------------------------- helpers

@pytest.fixture()
def fake() -> Any:
    """A running FakeBridge on an ephemeral port, torn down after the test."""
    bridge = FakeBridge().start()
    try:
        yield bridge
    finally:
        bridge.stop()


@pytest.fixture()
def client(fake: FakeBridge) -> Any:
    """A BridgeClient wired to the fake bridge."""
    c = BridgeClient(host=fake.host, port=fake.port, timeout=5.0)
    try:
        yield c
    finally:
        c.close()


def call_tool(app: Any, name: str, args: dict[str, Any] | None = None) -> tuple[str, Any]:
    """Call an MCP tool in-process and return ``(text, parsed)``.

    Works with `mcp` 1.x (`call_tool` -> content blocks or a (content, structured) tuple) and
    `mcp` 2.x (`call_tool` -> CallToolResult).
    """
    result = asyncio.run(app.call_tool(name, args or {}))
    content = getattr(result, "content", None)
    if content is None:
        content = result[0] if isinstance(result, tuple) else result
    blocks = [b.text for b in content if getattr(b, "type", None) == "text"]
    text = "\n".join(blocks)

    def _parse(chunk: str) -> Any:
        try:
            return json.loads(chunk)
        except ValueError:
            return chunk

    # A tool returning a list arrives as one text block per item.
    parsed: Any = [_parse(b) for b in blocks] if len(blocks) > 1 else _parse(text)
    return text, parsed


def tool_names(app: Any) -> list[str]:
    tools = asyncio.run(app.list_tools())
    return [t.name for t in tools]


def tool_descriptions(app: Any) -> dict[str, str]:
    tools = asyncio.run(app.list_tools())
    return {t.name: (t.description or "") for t in tools}


CORE_TOOLS = [
    "live_status",
    "live_connect",
    "live_discover",
    "live_commands",
    "live_command_call",
    "live_log",
    "live_lom_get",
    "live_lom_set",
    "live_lom_call",
    "live_lom_describe",
    "live_lom_children",
    "live_eval_python",
]


# --------------------------------------------------------------------------- client

def test_hello_ping_and_framing(client: BridgeClient, fake: FakeBridge) -> None:
    hello = client.hello()
    assert hello["protocol"] == 1
    assert hello["live"]["major"] == 12
    assert client.ping()["pong"] is True
    # hello() is cached: only one system.hello reached the server.
    client.hello()
    assert [r["cmd"] for r in fake.requests].count("system.hello") == 1
    # All of it went over one connection, ids are unique and echoed.
    assert client.connects == 1
    assert fake.connections == 1
    ids = [r["id"] for r in fake.requests]
    assert len(set(ids)) == len(ids)
    assert all(isinstance(i, str) and i for i in ids)


def test_large_payload_round_trip(client: BridgeClient, fake: FakeBridge) -> None:
    blob = "x" * 300_000
    fake.set_result("lom.get", {"blob": blob})
    result = client.request("lom.get", {"path": "song"})
    assert result["blob"] == blob
    # The stream stays usable afterwards (framing is correct).
    assert client.ping()["pong"] is True
    assert client.connects == 1


def test_line_cap_incoming(client: BridgeClient, fake: FakeBridge) -> None:
    client.max_line = 2_000
    fake.set_result("lom.get", {"blob": "y" * 20_000})
    with pytest.raises(BridgeError) as excinfo:
        client.request("lom.get", {"path": "song"})
    assert excinfo.value.type == "protocol"
    assert "limit" in excinfo.value.message


def test_line_cap_outgoing(client: BridgeClient) -> None:
    client.max_line = 500
    with pytest.raises(BridgeError) as excinfo:
        client.request("lom.set", {"path": "song", "prop": "name", "value": "z" * 5_000})
    assert excinfo.value.type == "bad_request"
    assert "line limit" in excinfo.value.message


def test_unserialisable_args_are_rejected(client: BridgeClient) -> None:
    # default=str keeps json.dumps working for odd values, but a non-string dict key does not.
    with pytest.raises(BridgeError) as excinfo:
        client.request("lom.set", {"path": "song", "prop": "x", "value": {(1, 2): "nope"}})
    assert excinfo.value.type == "bad_request"


def test_events_without_id_are_ignored(client: BridgeClient, fake: FakeBridge) -> None:
    fake.emit_event_before_response = True
    assert client.ping()["pong"] is True


def test_reconnects_after_live_drops_the_socket(client: BridgeClient, fake: FakeBridge) -> None:
    client.ping()
    assert client.connects == 1
    fake.close_connections()
    time.sleep(0.05)
    assert client.ping()["pong"] is True  # transparent reconnect
    assert client.connects == 2
    assert fake.connections == 2


def test_retries_once_when_a_request_is_dropped(client: BridgeClient, fake: FakeBridge) -> None:
    client.ping()
    fake.drop_next_request()
    assert client.ping()["pong"] is True
    # The dropped request and the retry both reached the server.
    assert [r["cmd"] for r in fake.requests].count("system.ping") == 3
    assert client.connects == 2


def test_no_retry_storm_when_live_is_gone() -> None:
    port = free_tcp_port()
    client = BridgeClient(host="127.0.0.1", port=port, timeout=2.0)
    with pytest.raises(BridgeError) as excinfo:
        client.request("system.ping")
    assert excinfo.value.type == "connection"
    assert client.connects == 0
    assert client.is_alive() is False
    client.close()


def test_timeout_is_not_retried(client: BridgeClient, fake: FakeBridge) -> None:
    fake.set_delay("system.ping", 2.0)
    started = time.monotonic()
    with pytest.raises(BridgeError) as excinfo:
        client.request("system.ping", timeout=0.3)
    elapsed = time.monotonic() - started
    assert excinfo.value.type == "timeout"
    assert excinfo.value.timeout == pytest.approx(0.3)
    assert elapsed < 2.0
    assert [r["cmd"] for r in fake.requests].count("system.ping") == 1
    assert client.connected is False  # socket closed to keep the stream in sync


def test_token_is_required_when_configured() -> None:
    fake = FakeBridge(token="s3cret").start()
    try:
        anonymous = BridgeClient(host=fake.host, port=fake.port, timeout=3.0)
        with pytest.raises(BridgeError) as excinfo:
            anonymous.hello()
        assert excinfo.value.type == "auth"
        anonymous.close()

        authorised = BridgeClient(host=fake.host, port=fake.port, token="s3cret", timeout=3.0)
        assert authorised.hello()["protocol"] == 1
        assert fake.requests[-1]["token"] == "s3cret"
        authorised.close()
    finally:
        fake.stop()


def test_error_envelope_becomes_bridge_error(client: BridgeClient, fake: FakeBridge) -> None:
    fake.set_error("lom.get", "not_found", "song.tracks[7]: index out of range (3 tracks)",
                   traceback="Traceback (most recent call last): ...")
    with pytest.raises(BridgeError) as excinfo:
        client.request("lom.get", {"path": "song.tracks[7]"})
    err = excinfo.value
    assert err.type == "not_found"
    assert "index out of range" in err.message
    assert err.cmd == "lom.get"
    assert err.traceback  # kept for logging
    assert "traceback" in err.to_dict()
    # ... but never in the friendly text shown to the model.
    assert "Traceback" not in friendly_error(err)


def test_switch_endpoint_clears_cache(client: BridgeClient, fake: FakeBridge) -> None:
    client.hello()
    other = FakeBridge(name="OtherMachine").start()
    try:
        client.switch(host=other.host, port=other.port)
        assert client.connected is False
        assert client.endpoint == f"{other.host}:{other.port}"
        assert client.hello()["machine"] == "OtherMachine"
    finally:
        other.stop()


def test_client_is_thread_safe(client: BridgeClient, fake: FakeBridge) -> None:
    results: list[Any] = []
    errors: list[BaseException] = []

    def worker(i: int) -> None:
        try:
            results.append(client.request("lom.get", {"path": f"song.tracks[{i}]"}))
        except BaseException as exc:  # pragma: no cover - only on a real bug
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert not errors
    assert len(results) == 8
    paths = sorted(r["name"] for r in results)
    assert paths == sorted(f"summary-of:song.tracks[{i}]" for i in range(8))
    assert client.connects == 1


def test_describe_and_from_config() -> None:
    client = BridgeClient.from_config({"host": "10.0.0.5", "port": 9999, "token": "t", "timeout": 20})
    described = client.describe()
    assert described == {
        "host": "10.0.0.5",
        "port": 9999,
        "endpoint": "10.0.0.5:9999",
        "token_set": True,
        "timeout": 20.0,
        "connected": False,
    }


# --------------------------------------------------------------------------- errors

def test_friendly_error_texts() -> None:
    conn = BridgeError("connection", "refused", cmd="system.ping", endpoint="127.0.0.1:9880")
    text = friendly_error(conn)
    assert "Live is not reachable at 127.0.0.1:9880" in text
    assert "live_discover" in text
    assert "\n" not in text

    timeout = BridgeError("timeout", "no answer", cmd="clips.add_notes", timeout=10.0)
    assert "did not answer" in friendly_error(timeout, "1.2.3.4:9880")
    assert "10s" in friendly_error(timeout, "1.2.3.4:9880")

    auth = BridgeError("auth", "bad token", cmd="system.hello")
    assert "live_connect" in friendly_error(auth, "1.2.3.4:9880")

    forbidden = BridgeError("forbidden", "eval disabled", cmd="eval.python")
    assert "allow_eval" in friendly_error(forbidden)

    unknown = BridgeError("unknown_command", "no such command", cmd="tracks.teleport")
    assert "live_commands" in friendly_error(unknown)

    missing_item = BridgeError("not_found", "no browser item 'Foo'", cmd="browser.load")
    text = friendly_error(missing_item)
    assert "live_browser_search" in text and "live_set_snapshot" not in text
    missing_file = BridgeError("not_found", "no such file", cmd="samples.inspect")
    assert "live_sample_inspect" in friendly_error(missing_file)
    stale = BridgeError("not_found", "no track named 'X'", cmd="tracks.get")
    assert "live_set_snapshot" in friendly_error(stale)
    m4l = BridgeError("unsupported", "LFO is a Max for Live device. Use browser.load(path=...)",
                      cmd="devices.insert")
    text = friendly_error(m4l)
    assert "browser.load" in text and "no workaround" not in text

    weird = BridgeError("something_new", "hm", cmd="x.y")
    assert "something_new" in friendly_error(weird)

    payload = error_payload(conn)
    assert payload["type"] == "connection"
    assert payload["cmd"] == "system.ping"
    assert "Traceback" not in payload["error"]


# --------------------------------------------------------------------------- config

@pytest.fixture()
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point HOME/USERPROFILE at a temp dir and clear every LIVEBRIDGE_* variable."""
    for var in ("LIVEBRIDGE_HOST", "LIVEBRIDGE_PORT", "LIVEBRIDGE_TOKEN", "LIVEBRIDGE_TIMEOUT",
                "LIVEBRIDGE_CONFIG"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


def write_config(home: Path, data: dict[str, Any]) -> Path:
    path = home / ".livebridge" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_config_defaults(clean_env: Path) -> None:
    cfg = config_mod.load_config()
    assert cfg["host"] == "127.0.0.1"
    assert cfg["port"] == 9880
    assert cfg["token"] is None
    assert cfg["timeout"] == 10.0
    assert set(cfg["source"].values()) == {"default"}
    assert config_mod.config_path() == clean_env / ".livebridge" / "config.json"


def test_config_precedence_file_env_cli(clean_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_config(clean_env, {"host": "10.0.0.1", "port": 9001, "token": "file-token", "timeout": 30})
    cfg = config_mod.load_config()
    assert (cfg["host"], cfg["port"], cfg["token"], cfg["timeout"]) == ("10.0.0.1", 9001, "file-token", 30.0)
    assert cfg["source"]["host"] == "file"

    monkeypatch.setenv("LIVEBRIDGE_HOST", "10.0.0.2")
    monkeypatch.setenv("LIVEBRIDGE_PORT", "9002")
    monkeypatch.setenv("LIVEBRIDGE_TOKEN", "env-token")
    monkeypatch.setenv("LIVEBRIDGE_TIMEOUT", "20")
    cfg = config_mod.load_config()
    assert (cfg["host"], cfg["port"], cfg["token"], cfg["timeout"]) == ("10.0.0.2", 9002, "env-token", 20.0)
    assert cfg["source"]["port"] == "env"

    cfg = config_mod.load_config(host="10.0.0.3", port=9003, token="cli-token", timeout=15,
                                 toolsets="core")
    assert (cfg["host"], cfg["port"], cfg["token"], cfg["timeout"]) == ("10.0.0.3", 9003, "cli-token", 15.0)
    assert cfg["toolsets"] == "core"
    assert set(cfg["source"].values()) == {"cli"}


def test_config_ignores_junk(clean_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = clean_env / ".livebridge" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("LIVEBRIDGE_PORT", "not-a-port")
    monkeypatch.setenv("LIVEBRIDGE_TIMEOUT", "-5")
    cfg = config_mod.load_config()
    assert cfg["port"] == 9880
    assert cfg["timeout"] == 10.0


def test_config_env_file_override(clean_env: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    alt = tmp_path / "elsewhere.json"
    alt.write_text(json.dumps({"host": "192.168.1.20", "port": 9880}), encoding="utf-8")
    monkeypatch.setenv("LIVEBRIDGE_CONFIG", str(alt))
    assert config_mod.config_path() == alt
    assert config_mod.load_config()["host"] == "192.168.1.20"


def test_save_config_round_trip(clean_env: Path) -> None:
    path = config_mod.save_config(host="192.168.1.20", port=9880, token="abc", timeout=25)
    assert path == clean_env / ".livebridge" / "config.json"
    cfg = config_mod.load_config()
    assert (cfg["host"], cfg["token"], cfg["timeout"]) == ("192.168.1.20", "abc", 25.0)
    # A second save keeps untouched keys and can clear the token.
    config_mod.save_config(host="127.0.0.1", token="")
    cfg = config_mod.load_config()
    assert cfg["host"] == "127.0.0.1"
    assert cfg["token"] is None
    assert cfg["timeout"] == 25.0


# ------------------------------------------------------------------------ discovery

def test_parse_beacon() -> None:
    beacon = {"livebridge": 1, "name": "Valentijn-PC", "host": "192.168.1.20", "port": 9880,
              "live": "12.4.5", "needs_token": True}
    parsed = discovery_mod.parse_beacon(json.dumps(beacon).encode(), ("192.168.1.20", 51000))
    assert parsed == {"name": "Valentijn-PC", "host": "192.168.1.20", "port": 9880,
                      "live": "12.4.5", "needs_token": True}

    # A beacon advertising 0.0.0.0 is rewritten to the sender address.
    parsed = discovery_mod.parse_beacon(
        json.dumps({"livebridge": 1, "name": "Mac", "host": "0.0.0.0", "port": 9880}).encode(),
        ("192.168.1.33", 51000),
    )
    assert parsed["host"] == "192.168.1.33"
    assert parsed["needs_token"] is False

    assert discovery_mod.parse_beacon(b"not json", ("1.2.3.4", 1)) is None
    assert discovery_mod.parse_beacon(json.dumps({"hello": 1}).encode(), ("1.2.3.4", 1)) is None
    assert discovery_mod.parse_beacon(b"\xff\xfe\x00", ("1.2.3.4", 1)) is None


def test_discover_hears_a_beacon() -> None:
    port = free_udp_port()
    box: dict[str, Any] = {}

    def listen() -> None:
        box["result"] = discovery_mod.discover(seconds=4.0, port=port, stop_after=1)

    thread = threading.Thread(target=listen, daemon=True)
    thread.start()
    time.sleep(0.4)

    beacon = json.dumps({"livebridge": 1, "name": "Valentijn-PC", "host": "127.0.0.1",
                         "port": 9880, "live": "12.4.5", "needs_token": False}).encode()
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for _ in range(10):
            sender.sendto(beacon, ("127.0.0.1", port))
            if not thread.is_alive():
                break
            time.sleep(0.2)
    finally:
        sender.close()
    thread.join(6)

    result = box.get("result", {})
    assert result.get("instances"), f"no instance heard: {result}"
    assert result["instances"][0]["name"] == "Valentijn-PC"
    assert result["instances"][0]["port"] == 9880


def test_discover_never_raises_on_a_bad_port() -> None:
    result = discovery_mod.discover(seconds=0.2, port=-1)
    assert result["instances"] == []
    assert result["notes"]


# ------------------------------------------------------------------------- the app

@pytest.fixture()
def app_online(fake: FakeBridge) -> Any:
    bridge = BridgeClient(host=fake.host, port=fake.port, timeout=5.0)
    app = create_app(bridge)
    try:
        yield app
    finally:
        bridge.close()


@pytest.fixture()
def app_offline() -> Any:
    bridge = BridgeClient(host="127.0.0.1", port=free_tcp_port(), timeout=2.0)
    app = create_app(bridge)
    try:
        yield app
    finally:
        bridge.close()


def test_app_registers_core_tools(app_online: Any) -> None:
    names = tool_names(app_online)
    for tool in CORE_TOOLS:
        assert tool in names, f"missing tool {tool}"
    descriptions = tool_descriptions(app_online)
    for tool in CORE_TOOLS:
        assert len(descriptions[tool]) > 80, f"{tool} needs a real docstring"
    assert "system" in app_online.tool_modules
    assert "lom" in app_online.tool_modules
    assert "live_status" in INSTRUCTIONS
    assert app_online.name == "LiveBridge"


def test_live_status_online(app_online: Any, fake: FakeBridge) -> None:
    _text, data = call_tool(app_online, "live_status")
    assert data["connected"] is True
    assert data["live"]["major"] == 12
    assert data["ping_ok"] is True
    assert data["song_time"] == 8.0
    assert data["allow_eval"] is True
    assert data["endpoint"] == f"{fake.host}:{fake.port}"
    assert "settings_from" in data


def test_live_status_offline_is_friendly(app_offline: Any) -> None:
    text, data = call_tool(app_offline, "live_status")
    assert data["connected"] is False
    assert data["type"] == "connection"
    assert "Live is not reachable at" in data["error"]
    assert "live_discover" in data["error"]
    assert data["next_steps"]
    assert "Traceback" not in text


def test_live_status_reports_offline_even_with_a_cached_hello(app_online: Any, fake: FakeBridge) -> None:
    _text, data = call_tool(app_online, "live_status")
    assert data["connected"] is True
    fake.stop()  # Live quits; the cached hello must not fake a live connection
    _text, data = call_tool(app_online, "live_status")
    assert data["connected"] is False
    assert data["ping_ok"] is False
    assert data["type"] == "connection"


def test_live_connect_and_persist(app_offline: Any, clean_env: Path) -> None:
    other = FakeBridge(name="TargetMachine").start()
    try:
        _text, data = call_tool(
            app_offline, "live_connect",
            {"host": other.host, "port": other.port, "persist": True},
        )
        assert data["connected"] is True
        assert data["bridge_name"] == "LiveBridge"
        assert data["persisted"] is True
        stored = json.loads((clean_env / ".livebridge" / "config.json").read_text())
        assert stored["host"] == other.host
        assert stored["port"] == other.port
    finally:
        other.stop()


def test_live_connect_validates_and_reports_offline(app_online: Any) -> None:
    _text, data = call_tool(app_online, "live_connect", {"host": "", "port": 9880})
    assert data["type"] == "bad_args"

    _text, data = call_tool(app_online, "live_connect", {"host": "127.0.0.1", "port": 70000})
    assert data["type"] == "bad_args"

    _text, data = call_tool(
        app_online, "live_connect", {"host": "127.0.0.1", "port": free_tcp_port(), "persist": True}
    )
    assert data["connected"] is False
    assert data["persisted"] is False
    assert "Live is not reachable" in data["error"]


def test_live_connect_wrong_token_is_auth(app_offline: Any) -> None:
    secured = FakeBridge(token="right").start()
    try:
        _text, data = call_tool(
            app_offline, "live_connect",
            {"host": secured.host, "port": secured.port, "token": "wrong"},
        )
        assert data["connected"] is False
        assert data["type"] == "auth"
        assert "token" in data["error"]
    finally:
        secured.stop()


def test_live_discover_tool(app_online: Any, fake: FakeBridge) -> None:
    _text, data = call_tool(app_online, "live_discover", {"seconds": 0.3, "port": free_udp_port()})
    assert data["instances"] == []
    assert data["notes"]  # explains why nothing was heard
    assert data["current"] == f"{fake.host}:{fake.port}"


def test_live_discover_offline_still_works(app_offline: Any) -> None:
    _text, data = call_tool(app_offline, "live_discover", {"seconds": 0.2, "port": free_udp_port()})
    assert "instances" in data  # discovery does not need a connection


def test_live_commands_tool(app_online: Any, fake: FakeBridge) -> None:
    # No namespace: a compact index only — no params, no docs (the full catalogue is ~100 KB).
    text, data = call_tool(app_online, "live_commands")
    assert data["count"] > 5
    assert "commands" not in data
    assert "get" in data["namespaces"]["lom"] and "commands" in data["namespaces"]["system"]
    assert "live_commands(namespace=" in data["next"]
    assert fake.requests[-1]["args"] == {"include_doc": False}
    assert '"params"' not in text and '"doc"' not in text

    _text, data = call_tool(app_online, "live_commands", {"namespace": "lom"})
    assert data["namespace"] == "lom"
    assert data["count"] == 5
    assert all(c["cmd"].startswith("lom.") for c in data["commands"])
    children = next(c for c in data["commands"] if c["cmd"] == "lom.children")
    # Params are compact strings: bare = required, name=default otherwise.
    assert children["params"] == ["path", "detail='minimal'", "offset=0", "limit=200"]
    getter = next(c for c in data["commands"] if c["cmd"] == "lom.get")
    assert getter["params"] == ["path", "prop=null"] and "mutating" not in getter
    assert next(c for c in data["commands"] if c["cmd"] == "lom.set")["mutating"] is True
    assert getter["doc"]

    _text, data = call_tool(app_online, "live_commands", {"namespace": "lom", "include_doc": False})
    assert "doc" not in data["commands"][0] and "lom" in data["namespaces"]

    # One full command name narrows to that command.
    _text, data = call_tool(app_online, "live_commands", {"namespace": "lom.children"})
    assert data["count"] == 1 and data["commands"][0]["cmd"] == "lom.children"
    assert "namespaces" not in data
    _text, data = call_tool(app_online, "live_commands", {"namespace": "lom.teleport"})
    assert data["type"] == "not_found" and "lom.children" in data["error"]
    _text, data = call_tool(app_online, "live_commands", {"namespace": "  "})
    assert data["type"] == "bad_args"


def test_live_commands_index_stays_small_for_a_big_catalogue(app_online: Any,
                                                             fake: FakeBridge) -> None:
    """The real Live registry has ~170 commands with ~5 params each; the index must stay tiny."""
    params = [{"name": f"arg_{i}", "default": None, "required": False} for i in range(6)]
    catalogue = [{"cmd": f"ns{n}.verb_{v}", "doc": "Does a thing " * 4, "params": params,
                  "mutating": bool(v % 2)} for n in range(17) for v in range(10)]
    fake.set_result("system.commands", {"count": len(catalogue),
                                        "namespaces": sorted({c["cmd"].split(".")[0]
                                                              for c in catalogue}),
                                        "commands": catalogue})
    text, data = call_tool(app_online, "live_commands")
    assert data["count"] == 170
    full = len(json.dumps(catalogue, indent=2))
    assert len(text) < full / 8, (len(text), full)
    assert len(text) < 8_000


def test_live_commands_accepts_an_older_bare_list(app_online: Any, fake: FakeBridge) -> None:
    fake.set_result("system.commands", [{"cmd": "tracks.list", "params": [{"name": "detail",
                                                                          "default": "summary"}]}])
    _text, data = call_tool(app_online, "live_commands")
    assert data["namespaces"] == {"tracks": ["list"]}
    _text, data = call_tool(app_online, "live_commands", {"namespace": "tracks"})
    assert data["commands"] == [{"cmd": "tracks.list", "params": ["detail='summary'"]}]


def test_live_command_call_tool(app_online: Any, fake: FakeBridge) -> None:
    fake.set_result("notes.theory", {"chords": [{"symbol": "Cm7"}]})
    _text, data = call_tool(app_online, "live_command_call",
                            {"cmd": "notes.theory", "args": {"chords": ["Cm7"]}})
    assert data == {"chords": [{"symbol": "Cm7"}]}
    assert fake.requests[-1]["cmd"] == "notes.theory"
    assert fake.requests[-1]["args"] == {"chords": ["Cm7"]}
    _text, data = call_tool(app_online, "live_command_call", {"cmd": "tracks list"})
    assert data["type"] == "bad_args"
    _text, data = call_tool(app_online, "live_command_call", {"cmd": "nope.nothing"})
    assert data["type"] == "unknown_command"


def test_live_status_can_include_the_server_status(app_online: Any, fake: FakeBridge) -> None:
    fake.set_result("system.status", {"server": {"clients": 1}})
    _text, data = call_tool(app_online, "live_status", {"include_server": True})
    assert data["connected"] is True and data["server"] == {"server": {"clients": 1}}


def test_live_log_tool(app_online: Any, fake: FakeBridge) -> None:
    _text, data = call_tool(app_online, "live_log", {"message": "hello from Claude"})
    assert data["ok"] is True
    assert fake.requests[-1]["args"]["message"] == "hello from Claude"

    _text, data = call_tool(app_online, "live_log", {"message": "   "})
    assert data["type"] == "bad_args"
    _text, data = call_tool(app_online, "live_log", {})
    assert data["type"] == "bad_args"
    _text, data = call_tool(app_online, "live_log", {"message": "x", "level": "loud"})
    assert data["type"] == "bad_args"


def test_lom_tools_online(app_online: Any, fake: FakeBridge) -> None:
    _text, data = call_tool(app_online, "live_lom_get", {"path": "song.tracks[0]"})
    assert data["path"] == "song.tracks[0]"

    _text, data = call_tool(app_online, "live_lom_get", {"path": "song", "prop": "tempo"})
    assert data["prop"] == "tempo"
    assert fake.requests[-1]["args"] == {"path": "song", "prop": "tempo"}

    _text, data = call_tool(
        app_online, "live_lom_set", {"path": "song", "prop": "tempo", "value": 128.0}
    )
    assert data["value"] == 128.0

    _text, data = call_tool(
        app_online, "live_lom_call",
        {"path": "song.tracks[0].clip_slots[1]", "method": "create_clip", "args": [4.0]},
    )
    assert data["method"] == "create_clip"
    assert data["args"] == [4.0]

    _text, data = call_tool(app_online, "live_lom_call", {"path": "song", "method": "start_playing"})
    assert fake.requests[-1]["args"]["args"] == []

    _text, data = call_tool(app_online, "live_lom_describe", {"path": "song.tracks[0]"})
    assert data["type"] == "Track"
    assert data["children"][0]["name"] == "devices"

    _text, data = call_tool(
        app_online, "live_lom_children", {"path": "song.tracks", "detail": "minimal"}
    )
    assert data["kind"] == "items" and data["total"] == 2
    assert [c["index"] for c in data["items"]] == [0, 1]
    assert data["items"][0]["detail"] == "minimal"
    assert fake.requests[-1]["args"] == {"path": "song.tracks", "detail": "minimal"}

    call_tool(app_online, "live_lom_children", {"path": "song.tracks", "offset": 1, "limit": 5})
    assert fake.requests[-1]["args"] == {"path": "song.tracks", "detail": "summary",
                                         "offset": 1, "limit": 5}

    call_tool(app_online, "live_lom_call",
              {"path": "song.tracks[0]", "method": "insert_device",
               "kwargs": {"DeviceName": "Reverb"}, "detail": "minimal"})
    assert fake.requests[-1]["args"] == {"path": "song.tracks[0]", "method": "insert_device",
                                         "args": [], "kwargs": {"DeviceName": "Reverb"},
                                         "detail": "minimal"}

    _text, data = call_tool(app_online, "live_eval_python", {"expr": "song.tempo"})
    assert data["result"] == "eval:song.tempo"
    assert fake.requests[-1]["args"] == {"expr": "song.tempo"}

    _text, data = call_tool(
        app_online, "live_eval_python", {"code": "t = song.tracks[0]", "expr": "t.name"}
    )
    assert data["result"] == "eval:t.name"
    assert data["stdout"] == "ran:t = song.tracks[0]"


def test_lom_tools_validate_arguments(app_online: Any) -> None:
    for name, args in [
        ("live_lom_get", {"path": "  "}),
        ("live_lom_set", {"path": "song", "prop": "", "value": 1}),
        ("live_lom_set", {"path": " ", "prop": "tempo", "value": 1}),
        ("live_lom_call", {"path": "song", "method": ""}),
        ("live_lom_describe", {"path": ""}),
        ("live_lom_children", {"path": "song.tracks", "detail": "everything"}),
        ("live_eval_python", {"code": ""}),
    ]:
        _text, data = call_tool(app_online, name, args)
        assert isinstance(data, dict) and data.get("type") == "bad_args", (name, args, data)

    # Type errors are caught by the generated input schema before the tool runs.
    with pytest.raises(Exception):
        call_tool(app_online, "live_lom_call", {"path": "song", "method": "fire", "args": "4.0"})


def test_tool_maps_bridge_error_to_one_friendly_line(app_online: Any, fake: FakeBridge) -> None:
    fake.set_error("lom.get", "not_found", "song.tracks[7]: index out of range (3 tracks)",
                   traceback="Traceback (most recent call last): boom")
    text, data = call_tool(app_online, "live_lom_get", {"path": "song.tracks[7]"})
    assert data["type"] == "not_found"
    assert "index out of range" in data["error"]
    assert "live_lom_children" in data["error"]
    assert "Traceback" not in text

    fake.set_error("eval.python", "forbidden", "eval.python is disabled")
    _text, data = call_tool(app_online, "live_eval_python", {"code": "song.tempo = 120"})
    assert data["type"] == "forbidden"
    assert "allow_eval" in data["error"]

    fake.set_error("lom.set", "unsupported", "clip.warping is audio-only")
    _text, data = call_tool(app_online, "live_lom_set", {"path": "song", "prop": "x", "value": 1})
    assert data["type"] == "unsupported"


def test_all_tools_are_friendly_offline(app_offline: Any) -> None:
    calls: dict[str, dict[str, Any]] = {
        "live_commands": {},
        "live_log": {"message": "hi"},
        "live_lom_get": {"path": "song"},
        "live_lom_set": {"path": "song", "prop": "tempo", "value": 120},
        "live_lom_call": {"path": "song", "method": "start_playing"},
        "live_lom_describe": {"path": "song"},
        "live_lom_children": {"path": "song.tracks"},
        "live_eval_python": {"code": "song.tempo"},
    }
    for name, args in calls.items():
        text, data = call_tool(app_offline, name, args)
        assert isinstance(data, dict), (name, data)
        assert data["type"] == "connection", (name, data)
        assert "Live is not reachable at" in data["error"], (name, data)
        assert "live_discover" in data["error"]
        assert "Traceback" not in text
        assert "BridgeError" not in text


def test_tools_reuse_one_connection(app_online: Any, fake: FakeBridge) -> None:
    for _ in range(3):
        call_tool(app_online, "live_lom_get", {"path": "song"})
    assert fake.connections == 1


# ---------------------------------------------------------------------------- CLI

def test_cli_parser_defaults_and_arguments() -> None:
    args = build_parser().parse_args([])
    assert (args.host, args.port, args.token, args.timeout, args.log_level) == (
        None, None, None, None, None,
    )
    args = build_parser().parse_args(
        ["--host", "192.168.1.20", "--port", "9881", "--token", "abc", "--timeout", "30",
         "--log-level", "DEBUG"]
    )
    assert args.host == "192.168.1.20"
    assert args.port == 9881
    assert args.token == "abc"
    assert args.timeout == 30.0
    assert args.log_level == "DEBUG"
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--log-level", "LOUD"])


# ------------------------------------------------------------ tokens stay per endpoint

def test_live_connect_never_carries_the_token_to_another_endpoint(clean_env: Path) -> None:
    secured = FakeBridge(token="secret-A").start()
    other = FakeBridge(name="Elsewhere").start()
    bridge = BridgeClient(host=secured.host, port=secured.port, token="secret-A", timeout=3.0)
    app = create_app(bridge)
    try:
        assert bridge.hello()["protocol"] == 1  # the token works on its own Live

        _text, data = call_tool(app, "live_connect", {"host": other.host, "port": other.port,
                                                      "persist": True})
        assert data["connected"] is True and data["token_used"] == "none"
        # Not one request to the new endpoint carried the previous Live's secret.
        assert other.requests and all("token" not in r for r in other.requests)
        assert bridge.token is None
        stored = json.loads((clean_env / ".livebridge" / "config.json").read_text(encoding="utf-8"))
        assert stored["host"] == other.host and stored["port"] == other.port
        assert "token" not in stored and "secret-A" not in json.dumps(stored)

        # Back to the secured Live without a token: nothing stored for it -> auth, not a leak.
        _text, data = call_tool(app, "live_connect", {"host": secured.host, "port": secured.port})
        assert data["type"] == "auth" and data["token_used"] == "none"

        # With the token (persisted for that endpoint), then away and back without one.
        _text, data = call_tool(app, "live_connect", {"host": secured.host, "port": secured.port,
                                                      "token": "secret-A", "persist": True})
        assert data["connected"] is True and data["token_used"] == "given"
        _text, data = call_tool(app, "live_connect", {"host": secured.host, "port": secured.port})
        assert data["connected"] is True and data["token_used"] == "kept"
        other.requests.clear()
        _text, data = call_tool(app, "live_connect", {"host": other.host, "port": other.port})
        assert data["connected"] is True and data["token_used"] == "none"
        assert all("token" not in r for r in other.requests)
        _text, data = call_tool(app, "live_connect", {"host": "localhost", "port": secured.port})
        assert data["connected"] is True and data["token_used"] == "stored"
        assert secured.requests[-1]["token"] == "secret-A"
    finally:
        bridge.close()
        secured.stop()
        other.stop()


def test_load_config_binds_the_file_token_to_its_endpoint(clean_env: Path,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    write_config(clean_env, {"host": "127.0.0.1", "port": 9880, "token": "local-secret"})
    assert config_mod.load_config()["token"] == "local-secret"
    # Loopback spellings are the same endpoint.
    assert config_mod.load_config(host="localhost")["token"] == "local-secret"
    # Another host from env/CLI never gets the file's token ...
    monkeypatch.setenv("LIVEBRIDGE_HOST", "10.0.0.9")
    cfg = config_mod.load_config()
    assert cfg["token"] is None and cfg["source"]["token"] == "default"
    assert config_mod.load_config(host="127.0.0.1", port=9990)["token"] is None
    # ... unless the file stores one for exactly that endpoint; explicit tokens still apply.
    write_config(clean_env, {"host": "127.0.0.1", "port": 9880, "token": "local-secret",
                             "tokens": {"10.0.0.9:9880": "lan-secret"}})
    assert config_mod.load_config()["token"] == "lan-secret"
    monkeypatch.setenv("LIVEBRIDGE_TOKEN", "env-secret")
    assert config_mod.load_config()["token"] == "env-secret"
    assert config_mod.stored_token("10.0.0.9", 9880) == "lan-secret"
    assert config_mod.stored_token("10.0.0.10", 9880) is None
    assert config_mod.stored_token("::1", 9880) == "local-secret"


def test_save_config_moves_tokens_with_their_endpoint(clean_env: Path) -> None:
    config_mod.save_config(host="192.168.1.20", port=9880, token="A")
    config_mod.save_config(host="192.168.1.30", port=9880)  # no token given
    stored = json.loads(config_mod.config_path().read_text(encoding="utf-8"))
    assert "token" not in stored  # A's secret is not paired with the new host
    assert stored["tokens"] == {"192.168.1.20:9880": "A"}
    assert config_mod.load_config()["token"] is None
    config_mod.save_config(host="192.168.1.20")  # back: its own token comes back
    assert config_mod.load_config()["token"] == "A"
    config_mod.save_config(token="")  # clearing removes it for this endpoint everywhere
    stored = json.loads(config_mod.config_path().read_text(encoding="utf-8"))
    assert "token" not in stored and "tokens" not in stored


# ------------------------------------------------------------ strict tool arguments

def test_every_tool_rejects_undeclared_arguments_in_its_schema(app_online: Any) -> None:
    tools = asyncio.run(app_online.list_tools())
    assert tools
    for tool in tools:
        schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None)
        assert schema.get("additionalProperties") is False, tool.name


def test_unknown_argument_is_an_error_not_silently_dropped(app_online: Any,
                                                           fake: FakeBridge) -> None:
    before = len(fake.requests)
    with pytest.raises(Exception) as excinfo:
        call_tool(app_online, "live_lom_get", {"path": "song", "property": "tempo"})
    text = str(excinfo.value)
    assert "live_lom_get has no argument 'property'" in text
    assert "Accepted arguments: path, prop, detail" in text
    assert "did you mean 'prop'" in text
    assert len(fake.requests) == before  # nothing reached Live

    with pytest.raises(Exception) as excinfo:
        call_tool(app_online, "live_command_call", {"cmd": "system.ping", "arguments": {}})
    assert "'arguments'" in str(excinfo.value) and "'args'" in str(excinfo.value)
    # Declared arguments still work, and the strict model is idempotent.
    from livebridge_mcp.server import enforce_declared_arguments

    assert enforce_declared_arguments(app_online) == len(tool_names(app_online))
    _text, data = call_tool(app_online, "live_lom_get", {"path": "song", "prop": "tempo"})
    assert data["prop"] == "tempo"


def test_cross_module_synonyms_are_suggested() -> None:
    """The classic mix-ups from the review name the right argument (tools/clips, racks, record)."""
    app = create_app(BridgeClient(host="127.0.0.1", port=free_tcp_port(), timeout=1.0))
    names = set(tool_names(app))
    cases = [("live_clip_create", {"track": 0, "color": "red"}, "color_index"),
             ("live_rack_set_chain", {"chain": 0, "pan": "L20"}, "panning"),
             ("live_record_session", {"length": 4}, "length_bars")]
    checked = 0
    for name, args, expected in cases:
        if name not in names:
            continue
        schema_props = {t.name: t for t in asyncio.run(app.list_tools())}[name]
        props = (getattr(schema_props, "input_schema", None) or {}).get("properties", {})
        bad = next(iter(k for k in args if k not in props), None)
        if bad is None or expected not in props:
            continue  # the tool module was aligned meanwhile: nothing to reject
        with pytest.raises(Exception) as excinfo:
            call_tool(app, name, args)
        assert f"did you mean '{expected}'" in str(excinfo.value), str(excinfo.value)
        checked += 1
    app.bridge.close()
    assert checked or not names & {c[0] for c in cases}


# ------------------------------------------------------------ too many clients

def test_too_many_clients_is_reported_not_mistaken_for_live_down() -> None:
    fake = FakeBridge(max_clients=1).start()
    holder = BridgeClient(host=fake.host, port=fake.port, timeout=3.0)
    try:
        holder.ping()  # takes the only slot
        for _ in range(3):
            late = BridgeClient(host=fake.host, port=fake.port, timeout=3.0)
            with pytest.raises(BridgeError) as excinfo:
                late.request("system.hello")
            err = excinfo.value
            assert err.type == "invalid_state", err.to_dict()
            assert "too many clients" in err.message
            assert late.connected is False  # the server closes it; so does the client
            text = friendly_error(err, fake.endpoint)
            assert "max_clients" in text and "Close other Claude sessions" in text
            assert "is Live running" not in text
            late.close()
        assert fake.rejected == 3
        assert holder.ping()["pong"] is True  # the connected client is unaffected
    finally:
        holder.close()
        fake.stop()


def test_abrupt_rejection_still_says_live_is_running() -> None:
    """A server that closes at once (kernel RST) must not read as 'Live is not running'."""
    fake = FakeBridge(max_clients=1, reject_mode="abrupt").start()
    holder = BridgeClient(host=fake.host, port=fake.port, timeout=3.0)
    try:
        holder.ping()
        late = BridgeClient(host=fake.host, port=fake.port, timeout=3.0)
        with pytest.raises(BridgeError) as excinfo:
            late.request("system.hello")
        err = excinfo.value
        text = friendly_error(err, fake.endpoint)
        assert err.type in ("invalid_state", "connection"), err.to_dict()
        assert "max_clients" in text
        assert "is Live running" not in text
        late.close()
    finally:
        holder.close()
        fake.stop()


def test_id_null_error_is_raised_for_the_request_in_flight(client: BridgeClient,
                                                           fake: FakeBridge) -> None:
    # An event line (no "ok") is still skipped; an id-less error envelope is not.
    fake.emit_event_before_response = True
    assert client.ping()["pong"] is True
    fake.emit_event_before_response = False
    sock = client._ensure_socket()
    sock.sendall(b"this is not json\n")  # the server answers with id null / bad_request
    with pytest.raises(BridgeError) as excinfo:
        client._read_response(sock, "never-sent", "system.ping", 2.0)
    assert excinfo.value.type == "bad_request" and excinfo.value.connection_level is True


# ------------------------------------------------------------ friendly hints

def test_friendly_hints_are_actionable() -> None:
    timeout = BridgeError("timeout", "no answer", cmd="devices.find", timeout=10.0)
    text = friendly_error(timeout)
    assert 'live_command_call(cmd="devices.find"' in text and "timeout=" in text
    assert "pass a larger timeout" not in text
    unknown = BridgeError("unknown_command", "no such command", cmd="clips.teleport")
    assert 'live_commands(namespace="clips")' in friendly_error(unknown)
    bad = BridgeError("bad_args", "unknown argument 'colour'; accepts: track, color",
                      cmd="clips.create")
    assert 'live_commands(namespace="clips.create")' in friendly_error(bad)
    full = BridgeError("invalid_state", "too many clients (max 8)")
    assert "max_clients" in friendly_error(full)
    other = BridgeError("invalid_state", "clip is not recording")
    assert "live_set_snapshot" in friendly_error(other)
    # "needs a token" is not "disabled": no allow_eval advice for it.
    no_token = BridgeError("forbidden", "eval.python needs a token — this bridge has none, so "
                           "any local process could run code inside Live. Re-run the installer",
                           cmd="eval.python")
    text = friendly_error(no_token)
    assert "needs a token" in text and "allow_eval" not in text
    disabled = BridgeError("forbidden", "eval.python is disabled — set \"allow_eval\": true",
                           cmd="eval.python")
    assert "allow_eval" in friendly_error(disabled)


# ------------------------------------------------------------ toolsets

def test_select_tool_modules() -> None:
    from livebridge_mcp.server import ALWAYS_LOADED, TOOLSETS, select_tool_modules

    available = ["arrangement", "automation", "browser", "clips", "cues", "devices", "lom",
                 "mixer", "notes", "plugins", "racks", "record", "routing", "samples", "scenes",
                 "splice", "system", "tracks", "transport", "view"]
    assert select_tool_modules(None, available) == (available, [])
    assert select_tool_modules("all", available) == (available, [])
    core, unknown = select_tool_modules("core", available)
    assert unknown == [] and set(core) == set(TOOLSETS["core"]) & set(available)
    assert "view" not in core and "cues" not in core
    minimal, _ = select_tool_modules("minimal", available)
    assert set(minimal) < set(core) and {"system", "lom", "clips", "notes"} <= set(minimal)
    selected, _ = select_tool_modules("core, Automation", available)
    assert set(selected) == set(core) | {"automation"}
    selected, _ = select_tool_modules("all,-view,-cues", available)
    assert set(selected) == set(available) - {"view", "cues"}
    selected, _ = select_tool_modules("-view", available)  # exclusions alone start from all
    assert set(selected) == set(available) - {"view"}
    selected, _ = select_tool_modules(["production", "-splice"], available)
    assert "automation" in selected and "splice" not in selected and "view" not in selected
    selected, _ = select_tool_modules("transport,-system,-lom", available)
    assert set(ALWAYS_LOADED) <= set(selected)  # the lifeline cannot be removed
    selected, unknown = select_tool_modules("nonsense", available)
    assert selected == available and unknown == ["nonsense"]  # never a useless server
    selected, unknown = select_tool_modules("core,bogus,-nope", available)
    assert set(selected) == set(core) and unknown == ["bogus", "-nope"]


def test_create_app_with_a_reduced_toolset(fake: FakeBridge) -> None:
    bridge = BridgeClient(host=fake.host, port=fake.port, timeout=5.0)
    try:
        full = create_app(BridgeClient(host=fake.host, port=fake.port, timeout=5.0))
        lean = create_app(bridge, toolsets="core")
        full_names, lean_names = set(tool_names(full)), set(tool_names(lean))
        assert lean_names < full_names
        for tool in CORE_TOOLS:
            assert tool in lean_names
        assert "view" in lean.hidden_tool_modules and "view" not in lean.tool_modules
        assert not any(n.startswith("live_view_") for n in lean_names)
        assert full.hidden_tool_modules == []
        assert "reduced tool set" in lean.instructions and "live_command_call" in lean.instructions
        assert "reduced tool set" not in full.instructions
        _text, data = call_tool(lean, "live_status")
        assert data["connected"] is True and "view" in data["hidden_tool_modules"]
        _text, data = call_tool(full, "live_status")
        assert "hidden_tool_modules" not in data
        # The config's toolsets are used when no explicit value is given.
        from_config = create_app(bridge, config={"toolsets": "core,view"})
        assert "view" in from_config.tool_modules and "cues" in from_config.hidden_tool_modules
        full.bridge.close()
    finally:
        bridge.close()


def test_toolsets_config_sources(clean_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert config_mod.load_config()["toolsets"] is None
    write_config(clean_env, {"toolsets": ["core", "automation"]})
    cfg = config_mod.load_config()
    assert cfg["toolsets"] == "core,automation" and cfg["source"]["toolsets"] == "file"
    monkeypatch.setenv("LIVEBRIDGE_TOOLSETS", "production")
    assert config_mod.load_config()["toolsets"] == "production"
    assert config_mod.load_config(toolsets="all,-view")["toolsets"] == "all,-view"
    assert build_parser().parse_args(["--toolsets", "core"]).toolsets == "core"
    assert build_parser().parse_args([]).toolsets is None


# ------------------------------------------------------------------ cross-fixer additions

def test_tool_results_are_one_compact_json_text(app_online: Any, fake: FakeBridge) -> None:
    """Dicts leave as compact JSON (not indent=2) and lists as ONE block, not one per item."""
    text, data = call_tool(app_online, "live_status")
    assert data["connected"] is True
    assert "\n" not in text and '": ' not in text, text[:200]
    fake.set_result("lom.get", [{"name": "Bass"}, {"name": "Drums"}, {"name": "Keys"}])
    result = asyncio.run(app_online.call_tool("live_lom_get", {"path": "song", "prop": "tracks"}))
    content = getattr(result, "content", None)
    if content is None:
        content = result[0] if isinstance(result, tuple) else result
    texts = [b.text for b in content if getattr(b, "type", None) == "text"]
    assert len(texts) == 1 and json.loads(texts[0])[2]["name"] == "Keys"


def test_compact_result_passes_strings_and_none_through() -> None:
    from livebridge_mcp.server import compact_result

    assert compact_result("plain") == "plain"
    assert compact_result(None) is None
    assert compact_result({"a": [1, 2], "b": "é"}) == '{"a":[1,2],"b":"é"}'
    assert compact_result([1, {"x": None}]) == '[1,{"x":null}]'


def test_skill_is_served_as_resource_and_prompt(app_online: Any, tmp_path: Path) -> None:
    from livebridge_mcp import server as server_mod

    resources = asyncio.run(app_online.list_resources())
    assert any(str(r.uri) == "livebridge://skill" for r in resources)
    contents = asyncio.run(app_online.read_resource("livebridge://skill"))
    text = "".join(getattr(c, "content", None) or getattr(c, "text", "") for c in contents)
    assert "live_status" in text
    prompts = asyncio.run(app_online.list_prompts())
    assert any(p.name == "livebridge_workflow" for p in prompts)
    rendered = asyncio.run(app_online.get_prompt("livebridge_workflow", {}))
    messages = getattr(rendered, "messages", rendered)
    assert "live_status" in json.dumps([m.model_dump(mode="json") for m in messages])
    assert server_mod.SKILL_PATH.is_file()
    # A missing skill file never raises: the fallback points at the instructions.
    assert "live_status" in server_mod.skill_text(tmp_path / "missing.md")
    assert "livebridge://skill" in INSTRUCTIONS


def test_live_status_reports_the_mcp_version(app_online: Any) -> None:
    from livebridge_mcp import __version__

    _text, data = call_tool(app_online, "live_status")
    assert data["mcp_host"]["version"] == __version__


def test_batch_and_dialog_tools(app_online: Any, fake: FakeBridge) -> None:
    fake.set_result("system.batch", {"count": 1, "ran": 1, "ok": 1, "failed": 0, "results": []})
    steps = [{"cmd": "tracks.create", "args": {"kind": "midi"}},
             {"cmd": "tracks.set", "args": {"track": "$0.path", "name": "Bass"}}]
    _text, data = call_tool(app_online, "live_command_batch", {"commands": steps})
    assert data["ok"] == 1
    request = fake.requests[-1]
    assert request["cmd"] == "system.batch"
    assert request["args"] == {"commands": steps, "stop_on_error": True}
    assert request["timeout"] >= 30.0            # the whole batch gets a longer default
    for bad in ([], [{"cmd": "system.ping"}] * 101):
        _text, data = call_tool(app_online, "live_command_batch", {"commands": bad})
        assert data["type"] == "bad_args"
    fake.set_result("system.dialog", {"open": True, "count": 1, "message": "Save?", "buttons": 3})
    _text, data = call_tool(app_online, "live_dialog_get")
    assert data["buttons"] == 3
    fake.set_result("system.dialog_press", {"pressed": 1, "message": "Save?", "open_after": 0})
    _text, data = call_tool(app_online, "live_dialog_press", {"button": 1, "expect": "Save"})
    assert data["pressed"] == 1
    assert fake.requests[-1]["args"] == {"button": 1, "expect": "Save"}
    _text, data = call_tool(app_online, "live_dialog_press", {"button": -1})
    assert data["type"] == "bad_args"


@pytest.mark.parametrize("platform, needle", [
    ("win32", "New-NetFirewallRule"),
    ("darwin", "Local Network"),
    ("linux", None),
])
def test_discover_explains_silence_per_platform(monkeypatch: pytest.MonkeyPatch,
                                                platform: str, needle: str | None) -> None:
    monkeypatch.setattr(discovery_mod.sys, "platform", platform)
    port = free_udp_port()
    result = discovery_mod.discover(seconds=0.2, port=port)
    assert result["instances"] == []
    joined = " ".join(result["notes"])
    if needle is None:
        assert result["notes"] == []
    else:
        assert needle in joined and str(port) in joined
    # A socket error is reported instead of the platform hint.
    assert discovery_mod.silence_hint(9881, "win32").count("9881") == 2
    bad = discovery_mod.discover(seconds=0.2, port=-1)
    assert not any("nothing heard" in note for note in bad["notes"])


def test_macos_no_route_to_host_gets_the_local_network_hint(monkeypatch: pytest.MonkeyPatch
                                                            ) -> None:
    import errno as errno_mod

    from livebridge_mcp import client as client_mod
    from livebridge_mcp.errors import MACOS_LOCAL_NETWORK_HINT

    def unreachable(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError(errno_mod.EHOSTUNREACH, "No route to host")

    monkeypatch.setattr(client_mod.socket, "create_connection", unreachable)
    monkeypatch.setattr(client_mod.sys, "platform", "darwin")
    lan = BridgeClient(host="192.168.1.20", port=9880, timeout=1.0)
    with pytest.raises(BridgeError) as caught:
        lan.request("system.ping")
    assert MACOS_LOCAL_NETWORK_HINT in caught.value.message
    assert "Local Network" in friendly_error(caught.value)
    local = BridgeClient(host="127.0.0.1", port=9880, timeout=1.0)
    with pytest.raises(BridgeError) as caught:
        local.request("system.ping")
    assert MACOS_LOCAL_NETWORK_HINT not in caught.value.message
    monkeypatch.setattr(client_mod.sys, "platform", "win32")
    with pytest.raises(BridgeError) as caught:
        lan.request("system.ping")
    assert MACOS_LOCAL_NETWORK_HINT not in caught.value.message


def test_config_file_with_a_utf8_bom_is_read(clean_env: Path) -> None:
    path = clean_env / ".livebridge" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"host": "10.0.0.7", "port": 9990}).encode())
    data = config_mod.read_config_file(path)
    assert data["host"] == "10.0.0.7" and data["port"] == 9990
