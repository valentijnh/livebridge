"""system.reload — hot reload of handler modules (handlers/devtools.py)."""

import importlib
import os
import sys

from LiveBridge import registry


def _call(bridge, args=None):
    return bridge.dispatch({"id": "r", "cmd": "system.reload", "args": args or {}})


def test_reload_all_keeps_every_command(bridge):
    before = set(s.name for s in registry.all_commands())
    response = _call(bridge)
    assert response["ok"], response
    result = response["result"]
    assert result["failed"] == {}
    assert "devtools" not in result["reloaded"]
    assert result["helpers"][:4] == ["compat", "lom", "serialize", "resolve"]
    assert "plugin_racks_lib" in result["helpers"]
    after = set(s.name for s in registry.all_commands())
    assert before == after
    assert result["commands"] == len(after)
    # the bridge still works after a reload
    ping = bridge.dispatch({"id": "p", "cmd": "system.ping", "args": {}})
    assert ping["ok"], ping


def test_reload_single_module(bridge):
    response = _call(bridge, {"modules": ["system"], "helpers": False})
    assert response["ok"], response
    assert response["result"]["reloaded"] == ["system"]
    assert response["result"]["helpers"] == []
    assert registry.get("system.hello") is not None


def test_reload_unknown_module_is_bad_args(bridge):
    response = _call(bridge, {"modules": ["does_not_exist"]})
    assert not response["ok"]
    assert response["error"]["type"] == "bad_args"


def test_broken_module_keeps_old_commands(bridge, tmp_path, monkeypatch):
    """A syntax error in a handler must not unregister its working commands."""
    import LiveBridge.handlers as handlers_pkg
    module_name = "zz_reload_probe"
    path = os.path.join(handlers_pkg.__path__[0], module_name + ".py")
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("from ..registry import command\n\n"
                         "@command('probe.value')\n"
                         "def probe_value(ctx):\n    return 1\n")
        response = _call(bridge, {"modules": [module_name], "helpers": False})
        assert response["ok"], response
        assert response["result"]["added"] == [module_name]
        assert bridge.dispatch({"id": "1", "cmd": "probe.value", "args": {}})["result"] == 1

        with open(path, "w", encoding="utf-8") as handle:
            handle.write("this is not python (\n")
        response = _call(bridge, {"modules": [module_name], "helpers": False})
        assert response["ok"], response
        assert module_name in response["result"]["failed"]
        assert bridge.dispatch({"id": "2", "cmd": "probe.value", "args": {}})["result"] == 1

        with open(path, "w", encoding="utf-8") as handle:
            handle.write("from ..registry import command\n\n"
                         "@command('probe.value')\n"
                         "def probe_value(ctx):\n    return 2\n")
        importlib.invalidate_caches()
        response = _call(bridge, {"modules": [module_name], "helpers": False})
        assert response["ok"], response
        assert response["result"]["failed"] == {}
        assert bridge.dispatch({"id": "3", "cmd": "probe.value", "args": {}})["result"] == 2
    finally:
        for name in list(registry._commands):
            if name.startswith("probe."):
                registry._commands.pop(name, None)
        sys.modules.pop("LiveBridge.handlers." + module_name, None)
        if os.path.exists(path):
            os.remove(path)
        cache = os.path.join(handlers_pkg.__path__[0], "__pycache__")
        if os.path.isdir(cache):
            for entry in os.listdir(cache):
                if entry.startswith(module_name):
                    os.remove(os.path.join(cache, entry))


def test_reload_core_swaps_context_class(bridge):
    """core=True reloads LiveBridge.py and switches the live Context in place."""
    ctx = bridge.ctx
    old_cls = type(ctx)
    response = _call(bridge, {"modules": ["system"], "helpers": True, "core": True})
    assert response["ok"], response
    core = response["result"]["core"]
    assert core and not core.get("error"), core
    assert core["context_swapped"] is True
    assert type(bridge.ctx) is not old_cls
    assert type(bridge.ctx).__name__ == "Context"
    assert bridge.ctx is ctx                      # same object, new class
    hello = bridge.dispatch({"id": "h", "cmd": "system.hello", "args": {}})
    assert hello["ok"], hello


def test_reload_includes_every_top_level_helper(bridge):
    """plugin_racks_lib (and any future helper file) is reloaded, core modules never are."""
    from LiveBridge.handlers import devtools

    names = devtools.helper_modules("LiveBridge")
    assert names[:4] == ["compat", "lom", "serialize", "resolve"]
    assert "plugin_racks_lib" in names
    for core in ("server", "dispatcher", "config", "registry", "LiveBridge", "log"):
        assert core not in names
    lib = sys.modules["LiveBridge.plugin_racks_lib"]
    lib.__livebridge_reload_marker__ = object()
    marker = lib.__livebridge_reload_marker__
    original = lib.MAX_PARAMETERS
    response = _call(bridge, {"modules": ["plugin_racks"]})
    assert response["ok"], response
    assert "plugin_racks_lib" in response["result"]["helpers"]
    assert response["result"]["failed"] == {}
    assert sys.modules["LiveBridge.plugin_racks_lib"] is lib            # reloaded in place
    assert lib.MAX_PARAMETERS == original == 128
    assert lib.__livebridge_reload_marker__ is marker
