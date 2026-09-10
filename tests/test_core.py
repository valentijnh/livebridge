"""Core tests: config, logging, registry, dispatcher, the pump and the Context."""

import importlib
import json
import os
import threading

import pytest

from LiveBridge import compat, config as config_module, log as log_module, registry
from LiveBridge.registry import BridgeError


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

def test_config_defaults_and_validation():
    config = config_module.Config({})
    assert config.host == "127.0.0.1"
    assert config.port == 9880
    assert config.allow_eval is True
    assert config.requires_token is False
    assert config.lan_mode is False
    assert config.name  # falls back to the hostname


def test_config_coerces_strings_and_clamps():
    config = config_module.Config({"port": "9999", "allow_eval": "false",
                                   "beacon": "0", "max_clients": 500,
                                   "host": "0.0.0.0", "token": "abc"})
    assert config.port == 9999
    assert config.allow_eval is False
    assert config.beacon is False
    assert config.max_clients == 64
    assert config.lan_mode is True
    assert config.requires_token is True


def test_config_file_and_env(tmp_path):
    path = tmp_path / "config.json"
    config_module.write_config(str(path), {"port": 9001, "token": "s3cret",
                                           "name": "TestBox"})
    written = json.loads(path.read_text())
    assert written["token"] == "s3cret"
    config = config_module.load_config(str(path), environ={})
    assert (config.port, config.token, config.name) == (9001, "s3cret", "TestBox")
    config = config_module.load_config(str(path), environ={"LIVEBRIDGE_PORT": "9123"})
    assert config.port == 9123
    assert config.to_dict()["token"] == "***"


def test_config_survives_a_broken_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{ not json at all")
    config = config_module.load_config(str(path), environ={})
    assert config.port == 9880


def test_config_reads_a_file_with_a_utf8_bom(tmp_path):
    """Windows editors (PowerShell 5.1 Out-File -Encoding UTF8, old Notepad) write a BOM;
    plain utf-8 json.load rejected it and LAN mode + token were silently lost."""
    path = tmp_path / "config.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(
        {"host": "0.0.0.0", "token": "bom-token", "port": 9555}).encode("utf-8"))
    config = config_module.load_config(str(path), environ={})
    assert (config.host, config.token, config.port) == ("0.0.0.0", "bom-token", 9555)
    assert config.source == str(path)


def test_lan_mode_without_a_token_falls_back_to_localhost(tmp_path):
    """ARCHITECTURE §7: the token is required in LAN mode — never expose an
    unauthenticated bridge (with eval) to the network."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"host": "0.0.0.0", "token": ""}), encoding="utf-8")
    config = config_module.load_config(str(path), environ={})
    assert config.host == "127.0.0.1" and config.lan_mode is False
    config = config_module.load_config(str(path), environ={"LIVEBRIDGE_HOST": "0.0.0.0"})
    assert config.host == "127.0.0.1"
    config = config_module.load_config(str(path), environ={"LIVEBRIDGE_TOKEN": "t"})
    assert config.host == "0.0.0.0" and config.lan_mode is True


def test_beacon_defaults_to_the_mode():
    """NETWORK.md: localhost mode has no beacon; LAN mode has one unless disabled."""
    assert config_module.Config({}).beacon is False
    assert config_module.Config({"host": "0.0.0.0", "token": "t"}).beacon is True
    assert config_module.Config({"host": "0.0.0.0", "token": "t",
                                 "beacon": False}).beacon is False
    assert config_module.Config({"beacon": True}).beacon is True


def test_eval_needs_allow_eval_and_a_token():
    assert config_module.Config({}).eval_enabled is False
    assert config_module.Config({"token": "t"}).eval_enabled is True
    assert config_module.Config({"token": "t", "allow_eval": False}).eval_enabled is False


def test_config_path_is_next_to_the_script():
    assert os.path.basename(config_module.config_path()) == "config.json"
    assert os.path.isdir(os.path.dirname(config_module.config_path()))


# --------------------------------------------------------------------------
# log
# --------------------------------------------------------------------------

def test_log_writes_to_the_sink_and_history():
    lines = []
    log_module.clear()
    log_module.set_sink(lines.append)
    log_module.set_level("debug")
    try:
        log_module.info("hello %s", "world")
        log_module.debug("quiet")
    finally:
        log_module.set_sink(None)
    assert any("hello world" in line for line in lines)
    assert any("hello world" in line for line in log_module.history())


def test_log_level_filters():
    log_module.clear()
    log_module.set_level("warn")
    try:
        log_module.info("not logged")
        log_module.error("logged")
    finally:
        log_module.set_level("debug")
    history = " ".join(log_module.history())
    assert "not logged" not in history
    assert "logged" in history


def test_log_never_raises_when_the_sink_is_dead():
    def broken(_line):
        raise RuntimeError("Live went away")

    log_module.set_sink(broken)
    try:
        log_module.info("still fine")
    finally:
        log_module.set_sink(None)


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------

def test_registry_discovered_the_core_handlers():
    registry.discover()
    names = [spec.name for spec in registry.all_commands()]
    for expected in ("system.hello", "system.ping", "system.commands", "system.log",
                     "lom.get", "lom.set", "lom.call", "lom.describe",
                     "lom.children", "eval.python"):
        assert expected in names
    assert "system" in registry.namespaces()


def test_registry_introspects_parameters():
    spec = registry.get("lom.children")
    params = dict((p["name"], p) for p in spec.params)
    assert params["path"]["required"] is True
    assert params["detail"]["default"] == "minimal"
    assert spec.mutating is False
    assert registry.get("lom.set").mutating is True
    assert "ctx" not in spec.param_names()


def test_registry_rejects_duplicates_and_bad_names():
    def handler(ctx):
        return None

    def other(ctx):
        return None

    spec = registry.CommandSpec("tests.duplicate", handler)
    registry.register(spec)
    try:
        with pytest.raises(ValueError):
            registry.register(registry.CommandSpec("tests.duplicate", other))
        registry.register(spec)  # same function again is a no-op
        with pytest.raises(ValueError):
            registry.register(registry.CommandSpec("nodot", handler))
    finally:
        registry.all_commands()  # keep the registry consistent
        registry._commands.pop("tests.duplicate", None)


def test_registry_validate_args():
    spec = registry.get("lom.get")
    assert registry.validate_args(spec, {"path": "song"}) == {"path": "song"}
    with pytest.raises(BridgeError) as info:
        registry.validate_args(spec, {"pth": "song"})
    assert info.value.type == "bad_args"
    assert "unknown argument" in info.value.message
    with pytest.raises(BridgeError) as info:
        registry.validate_args(spec, {})
    assert "missing required argument" in info.value.message
    with pytest.raises(BridgeError) as info:
        registry.validate_args(spec, ["song"])
    assert info.value.type == "bad_args"


def test_bridge_error_coerces_unknown_types():
    error = BridgeError("nonsense", "boom")
    assert error.type == "internal"
    assert error.to_dict("x.y")["cmd"] == "x.y"


# --------------------------------------------------------------------------
# dispatch: success + every error type
# --------------------------------------------------------------------------

def test_dispatch_success_shape(bridge):
    response = bridge.dispatch({"id": "abc", "cmd": "system.ping"})
    assert response["id"] == "abc"
    assert response["ok"] is True
    assert response["result"]["pong"] is True
    assert isinstance(response["ms"], float)


def test_dispatch_unknown_command(bridge):
    response = bridge.dispatch({"id": "1", "cmd": "does.not_exist"})
    assert response["ok"] is False
    assert response["error"]["type"] == "unknown_command"
    assert response["error"]["cmd"] == "does.not_exist"


def test_dispatch_bad_request_variants(bridge):
    assert bridge.dispatch({"cmd": "system.ping"})["error"]["type"] == "bad_request"
    assert bridge.dispatch({"id": "1"})["error"]["type"] == "bad_request"
    assert bridge.dispatch("not a dict")["error"]["type"] == "bad_request"
    assert bridge.dispatch({"id": "1", "cmd": "system.ping",
                            "surprise": 1})["error"]["type"] == "bad_request"
    assert bridge.dispatch({"id": "1", "cmd": "system.ping",
                            "v": 99})["error"]["type"] == "bad_request"


def test_dispatch_bad_args(bridge):
    response = bridge.dispatch({"id": "1", "cmd": "system.commands",
                                "args": {"nope": 1}})
    assert response["error"]["type"] == "bad_args"
    response = bridge.dispatch({"id": "1", "cmd": "system.ping", "args": []})
    assert response["error"]["type"] == "bad_args"


def test_dispatch_not_found(bridge):
    response = bridge.dispatch({"id": "1", "cmd": "lom.get",
                                "args": {"path": "song.tracks[99]"}})
    assert response["error"]["type"] == "not_found"
    assert "index out of range" in response["error"]["message"]


def test_dispatch_internal_error_has_a_traceback(eval_bridge):
    response = run_eval(eval_bridge, code="raise ValueError('kaboom')")
    assert response["error"]["type"] == "internal"
    assert "kaboom" in response["error"]["message"]
    assert "Traceback" in response["error"]["traceback"]


def test_dispatch_auth(bridge_factory):
    bridge = bridge_factory(token="hunter2")
    assert bridge.dispatch({"id": "1", "cmd": "system.ping"})["error"]["type"] == "auth"
    assert bridge.dispatch({"id": "1", "cmd": "system.ping",
                            "token": "wrong"})["error"]["type"] == "auth"
    assert bridge.dispatch({"id": "1", "cmd": "system.ping",
                            "token": "hunter2"})["ok"] is True


def test_dispatch_timeout_when_the_main_thread_never_runs(bridge_factory):
    bridge = bridge_factory(mode="queue")  # nothing drains the queue
    response = bridge.dispatch({"id": "1", "cmd": "system.ping", "timeout": 0.2})
    assert response["ok"] is False
    assert response["error"]["type"] == "timeout"
    assert "0.2s" in response["error"]["message"]
    # the job is still queued and runs on the next main-thread tick
    assert len(bridge._queue) == 1
    bridge.update_display()
    assert len(bridge._queue) == 0


def test_timeout_is_clamped(bridge):
    assert bridge.dispatcher.clamp_timeout(None) == bridge.config.default_timeout
    assert bridge.dispatcher.clamp_timeout("nonsense") == bridge.config.default_timeout
    assert bridge.dispatcher.clamp_timeout(9999) == bridge.config.max_timeout
    assert bridge.dispatcher.clamp_timeout(0) == bridge.config.default_timeout
    assert bridge.dispatcher.clamp_timeout(3) == 3.0


# --------------------------------------------------------------------------
# undo wrapping + the pump
# --------------------------------------------------------------------------

def test_mutating_commands_are_one_undo_step(bridge, song):
    before = len(song._undo_steps)
    response = bridge.dispatch({"id": "1", "cmd": "lom.set",
                                "args": {"path": "song", "prop": "tempo",
                                         "value": 130}})
    assert response["ok"] is True
    assert len(song._undo_steps) == before + 1
    assert song._undo_depth == 0
    assert song.tempo == 130.0


def test_non_mutating_commands_do_not_touch_undo(bridge, song):
    before = len(song._undo_steps)
    bridge.dispatch({"id": "1", "cmd": "lom.get", "args": {"path": "song"}})
    assert len(song._undo_steps) == before


def test_undo_step_is_closed_even_when_the_handler_raises(bridge, song):
    bridge.dispatch({"id": "1", "cmd": "lom.set",
                     "args": {"path": "song", "prop": "nope", "value": 1}})
    assert song._undo_depth == 0


def test_queue_mode_pump_executes_and_is_bounded(bridge_factory):
    bridge = bridge_factory(mode="queue")
    results = {}
    threads = []

    def ask(index):
        results[index] = bridge.dispatch({"id": str(index), "cmd": "system.ping",
                                          "timeout": 5})

    for index in range(5):
        thread = threading.Thread(target=ask, args=(index,))
        thread.daemon = True
        thread.start()
        threads.append(thread)
    deadline = threading.Event()
    for _ in range(200):
        bridge.update_display()
        if len(results) == 5:
            break
        deadline.wait(0.01)
    for thread in threads:
        thread.join(timeout=5)
    assert len(results) == 5
    assert all(response["ok"] for response in results.values())


def test_disconnect_is_idempotent_and_fails_pending_jobs(bridge_factory):
    bridge = bridge_factory(mode="queue", auto_start=True, port=0)
    assert bridge.server.running is True
    bridge.disconnect()
    assert bridge.server is None
    bridge.disconnect()
    response = bridge.dispatch({"id": "1", "cmd": "system.ping"})
    assert response["error"]["type"] == "invalid_state"


# --------------------------------------------------------------------------
# system handlers
# --------------------------------------------------------------------------

def test_system_hello(bridge):
    info = bridge.dispatch({"id": "1", "cmd": "system.hello"})["result"]
    assert info["name"] == "LiveBridge"
    assert info["protocol"] == 1
    assert info["live"]["major"] == 12
    assert info["live"]["version"] == "12.4.5"
    assert info["python"].startswith("3.")
    assert info["allow_eval"] is False          # no token -> eval refused (ARCHITECTURE §7)
    assert info["needs_token"] is False
    assert info["commands"] >= 10


def test_system_hello_reports_eval_on_with_a_token(eval_bridge):
    info = eval_bridge.dispatch({"id": "1", "cmd": "system.hello",
                                 "token": EVAL_TOKEN})["result"]
    assert info["allow_eval"] is True and info["needs_token"] is True


def test_system_commands_filtering(bridge):
    result = bridge.dispatch({"id": "1", "cmd": "system.commands",
                              "args": {"namespace": "lom"}})["result"]
    assert result["count"] >= 5
    assert all(entry["cmd"].startswith("lom.") for entry in result["commands"])
    assert all(entry["doc"] for entry in result["commands"])
    response = bridge.dispatch({"id": "1", "cmd": "system.commands",
                                "args": {"namespace": "nope"}})
    assert response["error"]["type"] == "not_found"


def test_system_log_round_trip(bridge):
    result = bridge.dispatch({"id": "1", "cmd": "system.log",
                              "args": {"message": "from the test", "tail": 10}})["result"]
    assert result["ok"] is True
    assert any("from the test" in line for line in result["lines"])
    response = bridge.dispatch({"id": "1", "cmd": "system.log",
                                "args": {"message": "x", "level": "nope"}})
    assert response["error"]["type"] == "bad_args"


def test_system_status(bridge):
    status = bridge.dispatch({"id": "1", "cmd": "system.status"})["result"]
    assert status["config"]["token"] == ""
    assert status["commands"] >= 10
    assert status["dialog"] == {"open": False, "count": 0, "message": "", "buttons": 0}
    assert status["bind"]["bound"] is False       # auto_start=False: never bound
    assert status["eval"] is False


def test_system_status_reports_the_server_and_its_clients(tcp_bridge, tcp_client):
    client = tcp_client(tcp_bridge.port)
    status = client.request("system.status")["result"]
    assert status["server"]["clients"] == 1 and status["server"]["authenticated"] == 1
    assert status["server"]["pending"] == 0
    assert status["bind"] == {"bound": True, "attempts": 1, "error": None, "retry_in": None}


# --------------------------------------------------------------------------
# modal dialogs
# --------------------------------------------------------------------------

def _open_dialog(app, message="Save changes to 'LB_Set'?", buttons=3, count=1):
    app._open_dialog_count = count
    app._current_dialog_message = message
    app._current_dialog_button_count = buttons


def test_system_dialog_reads_the_open_dialog(bridge, app):
    assert bridge.dispatch({"id": "1", "cmd": "system.dialog"})["result"]["open"] is False
    _open_dialog(app)
    result = bridge.dispatch({"id": "1", "cmd": "system.dialog"})["result"]
    assert result == {"open": True, "count": 1, "message": "Save changes to 'LB_Set'?",
                      "buttons": 3}
    status = bridge.dispatch({"id": "2", "cmd": "system.status"})["result"]
    assert status["dialog"]["open"] is True


def test_system_dialog_press(bridge, app, monkeypatch):
    pressed = []
    real = type(app).press_current_dialog_button

    def spy(self, index, /):
        pressed.append(index)
        return real(self, index)

    monkeypatch.setattr(type(app), "press_current_dialog_button", spy)
    response = bridge.dispatch({"id": "1", "cmd": "system.dialog_press",
                                "args": {"button": 0}})
    assert response["error"]["type"] == "invalid_state"          # nothing open
    _open_dialog(app)
    response = bridge.dispatch({"id": "1", "cmd": "system.dialog_press",
                                "args": {"button": 1, "expect": "delete"}})
    assert response["error"]["type"] == "invalid_state" and pressed == []
    for bad in (3, -1, 1.5, True, "0"):
        response = bridge.dispatch({"id": "1", "cmd": "system.dialog_press",
                                    "args": {"button": bad}})
        assert response["error"]["type"] == "bad_args", bad
    assert pressed == []
    result = bridge.dispatch({"id": "1", "cmd": "system.dialog_press",
                              "args": {"button": 2, "expect": "save CHANGES"}})["result"]
    assert result == {"pressed": 2, "message": "Save changes to 'LB_Set'?", "open_after": 0}
    assert pressed == [2]


# --------------------------------------------------------------------------
# system.batch
# --------------------------------------------------------------------------

def _batch(bridge, commands, **args):
    args["commands"] = commands
    return bridge.dispatch({"id": "b", "cmd": "system.batch", "args": args})


def test_system_batch_runs_steps_in_order_as_one_undo_step(bridge, song):
    response = _batch(bridge, [
        {"cmd": "lom.set", "args": {"path": "song", "prop": "tempo", "value": 101}},
        {"cmd": "lom.get", "args": {"path": "song.tracks[1]", "detail": "minimal"}},
        {"cmd": "lom.set", "args": {"path": "$1.path", "prop": "name", "value": "LB_Vox"}},
        "system.ping",
    ])
    assert response["ok"] is True, response
    result = response["result"]
    assert (result["count"], result["ran"], result["ok"], result["failed"]) == (4, 4, 4, 0)
    assert "stopped_at" not in result
    assert [r["cmd"] for r in result["results"]] == ["lom.set", "lom.get", "lom.set",
                                                     "system.ping"]
    assert song.tempo == 101.0 and song.tracks[1].name == "LB_Vox"
    assert result["results"][2]["result"]["path"] == "song.tracks[1]"
    assert bridge.ctx._undo_depth == 0


def test_system_batch_is_one_undo_step(bridge, song, monkeypatch):
    calls = []
    monkeypatch.setattr(type(song), "begin_undo_step",
                        lambda self: calls.append("begin"), raising=False)
    monkeypatch.setattr(type(song), "end_undo_step",
                        lambda self: calls.append("end"), raising=False)
    _batch(bridge, [
        {"cmd": "lom.set", "args": {"path": "song", "prop": "tempo", "value": 100}},
        {"cmd": "lom.set", "args": {"path": "song", "prop": "tempo", "value": 102}},
    ])
    assert calls == ["begin", "end"]


def test_system_batch_stops_at_the_first_error_or_keeps_going(bridge, song):
    steps = [
        {"cmd": "lom.set", "args": {"path": "song", "prop": "tempo", "value": 90}},
        {"cmd": "lom.get", "args": {"path": "song.tracks[42]"}},
        {"cmd": "lom.set", "args": {"path": "song", "prop": "tempo", "value": 91}},
    ]
    result = _batch(bridge, steps)["result"]
    assert (result["ran"], result["ok"], result["failed"], result["stopped_at"]) == (2, 1, 1, 1)
    assert result["results"][1]["error"]["type"] == "not_found"
    assert song.tempo == 90.0
    result = _batch(bridge, steps, stop_on_error=False)["result"]
    assert (result["ran"], result["ok"], result["failed"]) == (3, 2, 1)
    assert "stopped_at" not in result and song.tempo == 91.0


def test_system_batch_references(bridge, song):
    result = _batch(bridge, [
        {"cmd": "lom.children", "args": {"path": "song.tracks"}},
        {"cmd": "lom.get", "args": {"path": "$0.items.2.path", "prop": "name"}},
        {"cmd": "lom.set", "args": {"path": "song.tracks[0]", "prop": "name",
                                    "value": "$-1"}},
        {"cmd": "lom.set", "args": {"path": "song.tracks[1]", "prop": "name",
                                    "value": "$$5 bill"}},
        {"cmd": "lom.get", "args": {"path": "$0.nope"}},
        {"cmd": "lom.get", "args": {"path": "$9.path"}},
        {"cmd": "lom.get", "args": {"path": "$4.path"}},
    ], stop_on_error=False)["result"]
    outcomes = [r["ok"] for r in result["results"]]
    assert outcomes == [True, True, True, True, False, False, False]
    assert song.tracks[0].name == "Drums" and song.tracks[1].name == "$5 bill"
    errors = [r["error"]["message"] for r in result["results"][4:]]
    assert "no key 'nope'" in errors[0]
    assert "only earlier steps" in errors[1]
    assert "which failed" in errors[2]


def test_system_batch_templates_inside_strings(bridge, song):
    result = _batch(bridge, [
        {"cmd": "lom.get", "args": {"path": "song.tracks[0]", "detail": "minimal"}},
        {"cmd": "lom.get", "args": {"path": "${0.path}.devices[0]", "prop": "name"}},
        {"cmd": "lom.set", "args": {"path": "${0.path}", "prop": "name",
                                    "value": "LB ${1} on ${0.index} ${9x}"}},
        {"cmd": "lom.get", "args": {"path": "${0.path}", "prop": "name"}},
        {"cmd": "lom.get", "args": {"path": "song.tracks[${0.index}]", "prop": "name"}},
    ])["result"]
    assert result["ok"] == 5, result
    assert song.tracks[0].name == "LB Operator on 0 ${9x}"
    assert result["results"][3]["result"] == "LB Operator on 0 ${9x}"
    assert result["results"][4]["result"] == "LB Operator on 0 ${9x}"
    # a whole-string template keeps the JSON type, like "$n"
    result = _batch(bridge, [
        {"cmd": "lom.get", "args": {"path": "song.tracks[0]", "detail": "minimal"}},
        {"cmd": "lom.call", "args": {"path": "song.tracks[0]", "method": "delete_device",
                                     "args": ["${0.index}"]}},
    ])["result"]
    assert result["ok"] == 2, result
    assert [d.name for d in song.tracks[0].devices] == ["Bass Rack"]


def test_system_batch_validates_its_steps(bridge):
    for bad in ([], "system.ping", [1] * 101):
        assert _batch(bridge, bad)["error"]["type"] == "bad_args"
    result = _batch(bridge, [
        42,
        {"args": {}},
        {"cmd": "system.ping", "argz": {}},
        {"cmd": "system.batch", "args": {"commands": ["system.ping"]}},
        {"cmd": "system.ping", "args": [1]},
        {"cmd": "nope.nope"},
        {"cmd": "system.commands", "args": {"bogus": 1}},
    ], stop_on_error=False)["result"]
    types = [r["error"]["type"] for r in result["results"]]
    assert types == ["bad_args", "bad_args", "bad_args", "bad_args", "bad_args",
                     "unknown_command", "bad_args"]
    assert result["failed"] == 7 and result["ok"] == 0


def test_system_batch_over_tcp_is_one_round_trip(tcp_bridge, tcp_client, song):
    client = tcp_client(tcp_bridge.port)
    response = client.request("system.batch", {"commands": [
        {"cmd": "lom.set", "args": {"path": "song", "prop": "tempo", "value": 133}},
        {"cmd": "system.ping"}]})
    assert response["ok"] is True and response["result"]["ok"] == 2
    assert song.tempo == 133.0


# --------------------------------------------------------------------------
# eval
# --------------------------------------------------------------------------

EVAL_TOKEN = "eval-t0ken"


@pytest.fixture
def eval_bridge(bridge_factory):
    """A bridge with a token — eval.python refuses to run without one."""
    return bridge_factory(token=EVAL_TOKEN)


def run_eval(bridge, **args):
    return bridge.dispatch({"id": "1", "cmd": "eval.python", "args": args,
                            "token": EVAL_TOKEN})


def test_eval_python_runs_with_the_lom_in_scope(eval_bridge, song):
    result = run_eval(eval_bridge, code="print('tracks:', len(song.tracks))",
                      expr="song.tracks[0].name")["result"]
    assert result["result"] == "Bass"
    assert "tracks: 3" in result["stdout"]


def test_eval_python_can_mutate_and_summarize(eval_bridge, song):
    result = run_eval(eval_bridge, code="song.tempo = 111.0",
                      expr="summarize(song.tracks[1])")["result"]
    assert song.tempo == 111.0
    assert result["result"]["name"] == "Vocals"


def test_eval_python_globals_persist(eval_bridge):
    run_eval(eval_bridge, code="stash = 41", reset=True)
    result = run_eval(eval_bridge, expr="stash + 1")["result"]
    assert result["result"] == 42


def test_eval_python_is_forbidden_when_disabled(bridge_factory):
    bridge = bridge_factory(allow_eval=False, token=EVAL_TOKEN)
    response = run_eval(bridge, expr="1 + 1")
    assert response["error"]["type"] == "forbidden"
    assert "allow_eval" in response["error"]["message"]


def test_eval_python_is_forbidden_without_a_token(bridge, song):
    """ARCHITECTURE §7: eval is always token-protected.  A config-less (manual) install
    has no token, so any local process — or a web page, before the HTTP refusal — could
    otherwise run Python inside Live."""
    response = bridge.dispatch({"id": "1", "cmd": "eval.python",
                                "args": {"code": "song.tempo = 99.0"}})
    assert response["error"]["type"] == "forbidden"
    assert "token" in response["error"]["message"]
    assert song.tempo != 99.0
    status = bridge.dispatch({"id": "2", "cmd": "system.status"})["result"]
    assert status["eval"] is False


def test_eval_python_syntax_error_is_bad_args(eval_bridge):
    response = run_eval(eval_bridge, code="def (")
    assert response["error"]["type"] == "bad_args"


def test_eval_python_needs_something_to_run(eval_bridge):
    response = run_eval(eval_bridge)
    assert response["error"]["type"] == "bad_args"


@pytest.mark.parametrize("code", ["import sys; sys.exit()", "exit(3)",
                                  "raise SystemExit('bye')", "raise KeyboardInterrupt"])
def test_eval_python_contains_system_exit(eval_bridge, code):
    response = run_eval(eval_bridge, code="stash_before_exit = 1\n" + code)
    assert response["error"]["type"] == "bad_args"
    assert "exit" in response["error"]["message"].lower() or \
        "KeyboardInterrupt" in response["error"]["message"]
    # globals are still saved in the finally
    assert run_eval(eval_bridge, expr="stash_before_exit")["result"]["result"] == 1


def test_system_exit_from_any_handler_never_escapes_update_display(bridge_factory,
                                                                    monkeypatch):
    """A SystemExit that escapes a handler must still answer the waiting client and
    never propagate out of update_display into Live (queue mode = real threading)."""
    script = bridge_factory(mode="queue")

    def exploding(ctx):
        raise SystemExit("library called sys.exit")

    spec = registry.CommandSpec("test.exit", exploding, mutating=True)
    registry.register(spec)
    try:
        results = {}

        def ask():
            results["r"] = script.dispatch({"id": "9", "cmd": "test.exit", "timeout": 5})

        worker = threading.Thread(target=ask)
        worker.daemon = True
        worker.start()
        for _ in range(400):
            if script._queue:
                break
            threading.Event().wait(0.005)
        script.update_display()           # must not raise SystemExit
        worker.join(timeout=5)
        assert results["r"]["ok"] is False
        assert results["r"]["error"]["type"] == "internal"
        assert "SystemExit" in results["r"]["error"]["message"]
        assert script.ctx._undo_depth == 0
    finally:
        registry._commands.pop("test.exit", None)


def test_update_display_contains_base_exceptions_from_the_drain(bridge_factory,
                                                                monkeypatch):
    script = bridge_factory(mode="queue")

    def boom():
        raise KeyboardInterrupt()

    monkeypatch.setattr(script, "_drain", boom)
    script.update_display()               # contained, logged, never raised


# --------------------------------------------------------------------------
# compat + Context helpers
# --------------------------------------------------------------------------

def test_compat_helpers(song):
    assert compat.live_version() == (12, 4, 5)
    assert compat.live_version_string() == "12.4.5"
    assert compat.edition() in ("suite", "standard/intro", "unknown")
    assert compat.has(song, "tempo") is True
    assert compat.has(song, "not_a_property") is False
    assert compat.safe_getattr(song, "nope", "fallback") == "fallback"
    ok, value = compat.safe_call(song, "start_playing")
    assert ok is True and value is None
    assert compat.safe_call(song, "not_a_method")[0] is False
    assert compat.python_version().startswith("3.")


def test_context_track_resolution(bridge, song):
    ctx = bridge.ctx
    assert ctx.track(0) is song.tracks[0]
    assert ctx.track(-1) is song.tracks[-1]
    assert ctx.track("Bass") is song.tracks[0]
    assert ctx.track("voc") is song.tracks[1]          # case-insensitive prefix
    assert ctx.track("master") is song.master_track
    assert ctx.track("song.return_tracks[1]") is song.return_tracks[1]
    assert ctx.track(song.tracks[2]) is song.tracks[2]
    with pytest.raises(BridgeError) as info:
        ctx.track("nope")
    assert info.value.type == "not_found"
    with pytest.raises(BridgeError) as info:
        ctx.track(99)
    assert "5 tracks" not in info.value.message  # 3 regular tracks
    assert "3 tracks" in info.value.message


def test_context_scene_clip_and_device_resolution(bridge, song):
    ctx = bridge.ctx
    assert ctx.scene(1) is song.scenes[1]
    assert ctx.scene("Chorus") is song.scenes[2]
    assert ctx.clip_slot("Bass", 0) is song.tracks[0].clip_slots[0]
    assert ctx.clip_slot("Bass", "Verse") is song.tracks[0].clip_slots[1]
    assert ctx.clip("Bass", 0).name == "Bass Loop"
    with pytest.raises(BridgeError) as info:
        ctx.clip("Bass", 3)
    assert info.value.type == "not_found"
    assert ctx.device("Bass", 0).name == "Operator"
    assert ctx.device("Bass", "Bass Rack").can_have_chains is True
    assert ctx.parameter(ctx.device("Bass", 0), "Filter Freq").max == 20000.0


def test_context_log_and_show_message(bridge):
    bridge.ctx.log("hello from ctx")
    bridge.ctx.show_message("status bar")
    assert any("hello from ctx" in line for line in log_module.history())
    assert "status bar" in bridge.c_instance.messages


def test_reload_does_not_leave_the_port_bound(bridge_factory, tcp_client):
    """Live re-instantiates the script on reload — the port must come back free."""
    first = bridge_factory(mode="queue", auto_start=True, port=0)
    port = first.port
    first.disconnect()
    second = bridge_factory(mode="queue", auto_start=True, port=port)
    try:
        assert second.port == port
        assert second.server.running is True
    finally:
        second.disconnect()


def test_a_failed_bind_is_retried_from_update_display(bridge_factory, monkeypatch):
    """Live rebuilds the control surface on set load; on Windows the old instance's
    connections in TIME_WAIT keep the port refused for a while. The bridge must keep
    retrying instead of staying unreachable until Live restarts."""
    import socket
    import time as time_module

    lb_module = importlib.import_module("LiveBridge.LiveBridge")

    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    try:
        script = bridge_factory(mode="queue", auto_start=True, port=port)
        assert script.server is None
        state = script.bind_state()
        assert state["bound"] is False and state["attempts"] == 1 and state["error"]
        assert state["retry_in"] is not None
        script.update_display()                 # throttled: too early to retry
        assert script.bind_state()["attempts"] == 1
        monkeypatch.setattr(lb_module, "REBIND_FAST_SECONDS", 0.0)
        script._next_bind_at = time_module.time() - 1
        script.update_display()                 # still blocked: counted, not logged again
        assert script.server is None and script.bind_state()["attempts"] == 2
    finally:
        blocker.close()
    script._next_bind_at = time_module.time() - 1
    script.update_display()
    assert script.server is not None and script.server.running is True
    assert script.port == port
    assert script.bind_state() == {"bound": True, "attempts": 3, "error": None,
                                   "retry_in": None}


def test_rebind_slows_down_after_the_fast_period(bridge_factory, monkeypatch):
    import socket

    lb_module = importlib.import_module("LiveBridge.LiveBridge")

    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    try:
        script = bridge_factory(mode="queue", auto_start=True,
                                port=blocker.getsockname()[1])
        script._bind_failed_at -= lb_module.REBIND_FAST_PERIOD + 1
        script._next_bind_at = 0
        script.update_display()
        assert script.bind_state()["retry_in"] > lb_module.REBIND_FAST_SECONDS
    finally:
        blocker.close()


def test_disconnect_does_not_block_on_clients_waiting_for_queued_jobs(bridge_factory,
                                                                     tcp_client):
    """disconnect() used to stop the server (joining each client thread for up to 1 s)
    before failing the queued jobs those threads were waiting on."""
    import time as time_module

    script = bridge_factory(mode="queue", auto_start=True, port=0)
    clients = [tcp_client(script.port, timeout=10.0) for _ in range(3)]
    for index, client in enumerate(clients):
        client.send({"id": str(index), "cmd": "system.ping", "timeout": 30})
    deadline = time_module.time() + 5
    while len(script._queue) < 3 and time_module.time() < deadline:
        time_module.sleep(0.01)
    assert len(script._queue) == 3
    started = time_module.time()
    script.disconnect()
    assert time_module.time() - started < 1.0


def test_beacon_starts_and_stops_with_the_bridge(bridge_factory, udp_listener):
    _sock, beacon_port = udp_listener()
    script = bridge_factory(mode="queue", auto_start=True, port=0, beacon=True,
                            beacon_port=beacon_port)
    assert script.beacon is not None and script.beacon.running is True
    script.disconnect()
    assert script.beacon is None


# --------------------------------------------------------------------------
# threading model (docs/LIVE_API_VERIFIED.md §1, ARCHITECTURE §10)
# --------------------------------------------------------------------------

def test_dispatch_on_the_main_thread_runs_inline(bridge):
    assert bridge.main_thread_id == threading.get_ident()
    response = bridge.dispatch({"id": "1", "cmd": "system.ping"})
    assert response["ok"] is True
    assert len(bridge._queue) == 0


def test_dispatch_from_another_thread_waits_for_update_display(bridge):
    results = {}

    def ask():
        results["r"] = bridge.dispatch({"id": "7", "cmd": "system.ping", "timeout": 5})

    worker = threading.Thread(target=ask)
    worker.daemon = True
    worker.start()
    for _ in range(200):
        if bridge._queue:
            break
        threading.Event().wait(0.005)
    assert "r" not in results            # nothing runs until the main-thread tick
    bridge.update_display()
    worker.join(timeout=5)
    assert results["r"]["ok"] is True


def test_update_display_yields_the_gil_around_a_drain_while_clients_are_connected(bridge,
                                                                                  monkeypatch):
    """Inside Live socket threads only run while the main thread releases the GIL: with
    clients connected, update_display sleeps before the drain (requests get queued) and
    after running jobs (responses get sent) — 0.5 s -> 0.1 s round trips on Live 12.4.5."""
    import importlib
    live_bridge_module = importlib.import_module("LiveBridge.LiveBridge")
    sleeps = []
    monkeypatch.setattr(type(bridge), "_yield_gil",
                        staticmethod(lambda: sleeps.append(live_bridge_module.GIL_YIELD_SECONDS)))

    class FakeServer(object):
        client_count = 0

    monkeypatch.setattr(bridge, "_server", FakeServer())
    bridge.update_display()
    assert sleeps == []                                   # no clients: never sleeps
    FakeServer.client_count = 1
    bridge.update_display()
    assert sleeps == [live_bridge_module.GIL_YIELD_SECONDS]   # idle tick: once
    del sleeps[:]
    worker = threading.Thread(target=bridge.dispatch,
                              args=({"id": "1", "cmd": "system.ping", "timeout": 5},))
    worker.start()
    for _ in range(200):
        if bridge._queue:
            break
        threading.Event().wait(0.005)
    bridge.update_display()
    worker.join(timeout=5)
    assert len(sleeps) == 2                               # before and after the job


def test_socket_threads_never_call_schedule_message(bridge):
    calls = []
    original = bridge.schedule_message
    bridge.schedule_message = lambda *a, **k: calls.append(a) or original(*a, **k)
    worker = threading.Thread(target=bridge.dispatch,
                              args=({"id": "1", "cmd": "system.ping", "timeout": 0.3},))
    worker.start()
    worker.join(timeout=5)
    bridge.update_display()
    assert calls == []


def test_update_display_runs_the_framework_scheduled_messages(bridge):
    """Overriding update_display must keep calling the base class: that is
    where _Framework runs schedule_message callbacks."""
    hits = []
    bridge.schedule_message(1, lambda: hits.append("tick"))
    bridge.schedule_message(2, hits.append, "param")
    bridge.update_display()
    assert hits == ["tick"]
    bridge.update_display()
    assert hits == ["tick", "param"]


def test_log_lines_from_socket_threads_reach_live_on_the_main_thread(bridge):
    sink_lines = bridge.c_instance.log
    before = len(sink_lines)
    worker = threading.Thread(target=log_module.info, args=("from a socket thread",))
    worker.start()
    worker.join()
    assert log_module.pending_count() >= 1
    assert not any("from a socket thread" in line for line in sink_lines[before:])
    bridge.update_display()
    assert any("from a socket thread" in line for line in sink_lines[before:])
    assert log_module.pending_count() == 0


def test_beacon_payload_uses_the_cached_live_version(bridge, song):
    """The beacon runs on its own thread, so it must not ask Live for the
    version — it reports what the main thread cached at start-up."""
    import Live
    from live_stub import factory
    app = Live.Application.get_application()
    factory.make_application(song, version=(11, 3, 0))   # a LOM read would see this
    try:
        payload = bridge.beacon_payload()
    finally:
        Live.Application.set_application(app)
    assert payload["live"] == "12.4.5"


def test_edition_comes_from_get_variant(song):
    from live_stub import factory
    factory.make_application(song, variant="Standard")
    assert compat.variant() == "Standard"
    assert compat.edition() == "standard"
    assert compat.is_suite() is False
    factory.make_application(song, variant="Suite")
    assert compat.edition() == "suite"


# --------------------------------------------------------------------------
# the stub mirrors the verified Live 12.4 API (docs/LIVE_API_VERIFIED.md)
# --------------------------------------------------------------------------

def test_stub_enums_are_boost_style():
    import Live
    assert int(Live.Device.DeviceType.midi_effect) == 4
    assert Live.Device.DeviceType.midi_effect.name == "midi_effect"
    assert not hasattr(Live.Clip.WarpMode, "__members__")
    assert Live.Clip.WarpMode.names["repitch"] == 3
    assert Live.Clip.WarpMode.values[6].name == "complex_pro"
    assert int(Live.Clip.ClipLaunchQuantization.q_global) == 0
    assert int(Live.Clip.GridQuantization.g_sixteenth) == 8
    assert int(Live.Song.RecordingQuantization.rec_q_sixtenth) == 5
    assert int(Live.Song.Quantization.q_bar) == 4
    assert int(Live.Track.Track.monitoring_states.OFF) == 2
    assert not hasattr(Live.Track, "MonitoringState")
    assert int(Live.Browser.Relation.none) == 3
    assert not hasattr(Live.Clip, "AutomationEnvelope")


def test_stub_devices_have_device_on_and_read_only_is_active(song):
    operator = song.tracks[0].devices[0]
    assert operator.parameters[0].name == "Device On"
    with pytest.raises(AttributeError):
        operator.is_active = False
    operator.parameters[0].value = 0
    assert operator.is_active is False
    with pytest.raises(AttributeError):
        operator.parameters[2].min = 0.0
    with pytest.raises(RuntimeError):
        operator.parameters[2].value_items      # not quantized
    with pytest.raises(RuntimeError):
        operator.parameters[6].default_value    # quantized


def test_stub_note_api_matches_live(song):
    import Live
    clip = song.tracks[0].clip_slots[0].clip
    with pytest.raises(TypeError):
        Live.Clip.MidiNoteSpecification(pitch=60)            # start/duration required
    spec = Live.Clip.MidiNoteSpecification(60, 0.5, 0.25, velocity=90)
    assert not hasattr(spec, "pitch")                        # write-only value object
    ids = clip.add_new_notes((spec,))
    assert isinstance(ids, tuple) and len(ids) == 1
    notes = clip.get_notes_extended(from_pitch=0, pitch_span=128, from_time=0.0,
                                    time_span=4.0)
    assert isinstance(notes, Live.Clip.MidiNoteVector)
    for note in notes:
        note.velocity = 10.0
    with pytest.raises(TypeError):
        clip.apply_note_modifications(list(notes))           # must be the vector
    clip.apply_note_modifications(notes)
    assert all(n.velocity == 10.0 for n in clip.get_all_notes_extended())
    subset = clip.get_notes_by_id(ids)
    assert [n.pitch for n in subset] == [60]
    clip.remove_notes_by_id(ids)
    assert len(clip.get_all_notes_extended()) == 4
    with pytest.raises(RuntimeError):
        song.tracks[1].clip_slots[0].clip.get_all_notes_extended()   # audio clip


def test_stub_clip_properties_match_live(song):
    midi = song.tracks[0].clip_slots[0].clip
    audio = song.tracks[1].clip_slots[0].clip
    assert midi.launch_quantization == 0                     # 0 = global
    with pytest.raises(RuntimeError):
        midi.warping                                         # audio clips only
    with pytest.raises(AttributeError):
        midi.length = 8.0                                    # read-only
    with pytest.raises(TypeError):
        audio.pitch_coarse = 1.5                             # int property
    audio.pitch_coarse = -12
    with pytest.raises(ValueError):
        audio.gain = 2.0                                     # 0..1
    arrangement = song.tracks[0].arrangement_clips[0]
    parameter = song.tracks[0].devices[0].parameters[1]
    assert arrangement.automation_envelope(parameter) is None
    with pytest.raises(RuntimeError):
        arrangement.create_automation_envelope(parameter)
    envelope = midi.create_automation_envelope(parameter)
    envelope.insert_step(0.0, 1.0, 0.5)
    assert envelope.value_at_time(0.5) == 0.5
    other_track_param = song.tracks[1].devices[0].parameters[1]
    assert midi.automation_envelope(other_track_param) is None


def test_stub_track_and_mixer_match_live(song):
    import Live
    assert song.return_tracks[0].can_be_armed is False
    with pytest.raises(RuntimeError):
        song.return_tracks[0].arm
    with pytest.raises(RuntimeError):
        song.tracks[0].fold_state                            # not a group track
    mixer = song.tracks[0].mixer_device
    assert mixer.crossfade_assign == 1
    assert Live.MixerDevice.MixerDevice.crossfade_assignments.values[2].name == "B"
    assert not hasattr(mixer, "crossfader_assign")
    with pytest.raises(RuntimeError):
        mixer.cue_volume                                     # main track only
    track = song.tracks[0]
    track.current_monitoring_state = Live.Track.Track.monitoring_states.IN
    assert track.current_monitoring_state == 0
    with pytest.raises(RuntimeError):
        song.master_track.input_routing_type


def test_stub_insert_device_and_clip_creation(song):
    track = song.tracks[1]                                   # audio track
    reverb = track.insert_device("Utility", 0)
    assert reverb.class_name == "StereoGain"
    with pytest.raises(ValueError):
        track.insert_device("Serum")                         # "Device Serum not found."
    with pytest.raises(ValueError):
        track.insert_device("utility")                       # exact UI names only
    with pytest.raises(RuntimeError):
        track.insert_device("Operator")                      # audio track: effects only
    with pytest.raises(RuntimeError):
        track.clip_slots[1].create_clip(4.0)                 # MIDI clips on MIDI tracks
    with pytest.raises(RuntimeError):
        track.clip_slots[1].create_audio_clip("relative/loop.wav")
    clip = track.clip_slots[1].create_audio_clip("/Samples/loop.wav")
    assert clip.is_audio_clip and clip.name == "loop"
    arranged = track.create_audio_clip("C:\\Samples\\hit.wav", 8.0)
    assert arranged.is_arrangement_clip and arranged.start_time == 8.0
    assert song.duplicate_track(0) is None                   # returns None in Live
    assert song.create_scene(-1) is song.scenes[-1]


def test_stub_scene_cue_and_view_rules(song):
    scene = song.scenes[0]
    assert scene.tempo == -1.0                               # disabled
    assert scene.time_signature_numerator == -1
    scene.time_signature_enabled = True                      # writable in 12.4.5
    assert (scene.time_signature_numerator, scene.time_signature_denominator) == (4, 4)
    scene.time_signature_enabled = False
    assert scene.time_signature_numerator == -1
    with pytest.raises(AttributeError):
        song.cue_points[0].time = 4.0                        # read-only
    with pytest.raises(AttributeError):
        song.view.selected_parameter = None
    assert not hasattr(song.view, "selected_device")
    with pytest.raises(AttributeError):
        song.tracks[0].view.selected_device = song.tracks[0].devices[0]
    song.view.select_device(song.tracks[0].devices[1])
    assert song.tracks[0].view.selected_device is song.tracks[0].devices[1]


def test_stub_browser_matches_live(app):
    browser = app.browser
    assert isinstance(browser.colors, tuple)
    assert isinstance(browser.user_folders, tuple) and browser.user_folders
    assert not hasattr(browser, "splice")
    items = browser.instruments.iter_children                # a property
    assert len(items) == 5 and [i.name for i in items][0] == "Operator"
    assert not hasattr(browser.instruments, "canonical_parent")
    assert browser.relation_to_hotswap_target(browser.instruments) == 3


def test_stub_collections_are_live_vectors(song):
    """Live 12.4.5 collections are ``Base.Vector`` — neither list nor tuple."""
    tracks = song.tracks
    assert type(tracks).__name__ == "Vector" and not isinstance(tracks, (list, tuple))
    assert len(tracks) == 3 and tracks[-1] is song.tracks[2]._canonical_parent.tracks[2]
    assert [t.name for t in tracks[1:]] == ["Vocals", "Drums"]
    assert song.tracks[0] in tracks and not hasattr(tracks, "index")
    with pytest.raises(RuntimeError):
        tracks.append(None)
    assert type(song.tracks[0].clip_slots).__name__ == "Vector"


def test_lom_paths_see_through_vectors(song):
    """``lom._is_sequence`` accepts Vectors, so path_of walks parents (and take lanes)."""
    from LiveBridge import compat, lom

    class Ctx(object):
        pass

    ctx = Ctx()
    ctx.song, ctx.app, ctx.browser = song, None, None
    assert compat.is_sequence(song.tracks) and not compat.is_sequence("abc")
    device = song.tracks[2].devices[0]
    assert lom.path_of(device, ctx) == "song.tracks[2].devices[0]"
    lane = song.tracks[0].create_take_lane()
    clip = lane.create_midi_clip(0.0, 4.0)
    assert lom.path_of(clip, ctx) == "song.tracks[0].take_lanes[0].arrangement_clips[0]"
    assert lom._scan_for(clip, ctx) == "song.tracks[0].take_lanes[0].arrangement_clips[0]"


def test_stub_drum_rack_pads_follow_in_note(song):
    rack = song.tracks[2].devices[0]
    assert len(rack.drum_pads) == 128
    assert [p.note for p in rack.visible_drum_pads][:2] == [36, 37]
    chain = rack.insert_chain()
    assert chain.in_note == 36                               # new chains land on C1
    with pytest.raises(RuntimeError):
        chain.in_note = -1                                   # no "All Notes" via the API
    chain.in_note = 40
    assert list(rack.drum_pads[40].chains) == [chain]
    with pytest.raises(RuntimeError):
        song.tracks[0].devices[1].drum_pads                  # not a drum rack


def test_stub_deleted_objects_compare_equal_to_none(song):
    """Ableton's own ``liveobj_valid(obj)`` is ``obj != None``."""
    track = song.tracks[2]
    device = track.devices[0]
    assert track != None  # noqa: E711 - this is exactly Live's idiom
    song.delete_track(2)
    assert track == None  # noqa: E711
    assert device == None  # noqa: E711
    assert song.tracks[0] != None  # noqa: E711
