"""Configuration for the LiveBridge MCP server.

Precedence (highest first), per ``docs/ARCHITECTURE.md`` §7:

1. CLI arguments (``--host/--port/--token/--timeout/--toolsets``) — anything passed explicitly to
   :func:`load_config`.
2. Environment: ``LIVEBRIDGE_HOST``, ``LIVEBRIDGE_PORT``, ``LIVEBRIDGE_TOKEN``, ``LIVEBRIDGE_TIMEOUT``,
   ``LIVEBRIDGE_TOOLSETS``.
3. The user config file ``~/.livebridge/config.json`` (Windows: ``%USERPROFILE%\\.livebridge\\config.json``,
   or wherever ``LIVEBRIDGE_CONFIG`` points).
4. Defaults: ``127.0.0.1:9880``, no token, 10 s timeout, every tool module.

The config file looks like::

    {"host": "192.168.1.20", "port": 9880, "token": "<secret of that Live>", "timeout": 10,
     "tokens": {"192.168.1.20:9880": "<secret>", "127.0.0.1:9880": "<other secret>"},
     "toolsets": "core"}

**Tokens are bound to their endpoint.** The file's top-level ``token`` belongs to the file's own
``host``/``port`` and ``tokens`` maps further ``"host:port"`` endpoints to their token. When the
effective host/port (after env/CLI overrides) is a different endpoint, the file never lends it a
token that belongs to another Live — only ``--token``/``LIVEBRIDGE_TOKEN`` apply everywhere.
Loopback names (``localhost``, ``127.x.x.x``, ``::1``) count as one host.

`live_connect(..., persist=True)` writes the chosen endpoint back with :func:`save_config`, so the
next Claude session reaches the same Live without arguments.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Mapping

from .client import DEFAULT_HOST, DEFAULT_PORT, DEFAULT_TIMEOUT

__all__ = [
    "DEFAULTS",
    "ENV_KEYS",
    "config_dir",
    "config_path",
    "endpoint_key",
    "load_config",
    "read_config_file",
    "save_config",
    "stored_token",
]

log = logging.getLogger(__name__)

DEFAULTS: dict[str, Any] = {
    "host": DEFAULT_HOST,
    "port": DEFAULT_PORT,
    "token": None,
    "timeout": DEFAULT_TIMEOUT,
    #: ``None`` = every tool module; otherwise a spec such as ``"core"`` or ``"core,automation"``
    #: (see :func:`livebridge_mcp.server.select_tool_modules`).
    "toolsets": None,
}

ENV_KEYS = {
    "host": "LIVEBRIDGE_HOST",
    "port": "LIVEBRIDGE_PORT",
    "token": "LIVEBRIDGE_TOKEN",
    "timeout": "LIVEBRIDGE_TIMEOUT",
    "toolsets": "LIVEBRIDGE_TOOLSETS",
}

#: Host names that all mean "this machine" — one endpoint for token scoping.
_LOOPBACK_NAMES = ("localhost", "::1", "0:0:0:0:0:0:0:1")

#: Points at an alternative config file (absolute path). Handy for tests and for running two
#: MCP servers against two Live machines from one account.
ENV_CONFIG_FILE = "LIVEBRIDGE_CONFIG"


def _home(env: Mapping[str, str] | None = None) -> Path:
    """The user's home directory, Windows-aware and honouring a patched environment."""
    env = os.environ if env is None else env
    if os.name == "nt":
        profile = env.get("USERPROFILE")
        if profile:
            return Path(profile)
        drive, path = env.get("HOMEDRIVE"), env.get("HOMEPATH")
        if drive and path:
            return Path(drive + path)
    else:
        home = env.get("HOME")
        if home:
            return Path(home)
    return Path.home()


def config_dir(env: Mapping[str, str] | None = None) -> Path:
    """``~/.livebridge`` (``%USERPROFILE%\\.livebridge`` on Windows). Not created here."""
    return _home(env) / ".livebridge"


def config_path(env: Mapping[str, str] | None = None) -> Path:
    """Path of the user config file, honouring ``LIVEBRIDGE_CONFIG``."""
    env = os.environ if env is None else env
    override = env.get(ENV_CONFIG_FILE)
    if override:
        return Path(override).expanduser()
    return config_dir(env) / "config.json"


def read_config_file(path: str | os.PathLike[str] | None = None,
                     env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Read the user config file. Returns ``{}`` when it is missing or unreadable (never raises)."""
    p = Path(path).expanduser() if path is not None else config_path(env)
    try:
        raw = p.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        log.warning("cannot read %s: %s", p, exc)
        return {}
    try:
        data = json.loads(raw)
    except ValueError as exc:
        log.warning("ignoring malformed config %s: %s", p, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _normal_host(host: Any) -> str:
    text = str(host or DEFAULT_HOST).strip().lower()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    if text in _LOOPBACK_NAMES or text.startswith("127."):
        return "127.0.0.1"
    return text


def endpoint_key(host: Any, port: Any) -> str:
    """Canonical ``"host:port"`` key used to bind a token to one Live instance.

    Host names are lower-cased and every loopback spelling (``localhost``, ``127.x.x.x``,
    ``::1``) becomes ``127.0.0.1``; an invalid port falls back to the default 9880.
    """
    coerced = None
    try:
        coerced = int(port)
    except (TypeError, ValueError):
        pass
    if coerced is None or not (1 <= coerced <= 65535):
        coerced = DEFAULT_PORT
    return f"{_normal_host(host)}:{coerced}"


def _file_endpoint(data: Mapping[str, Any]) -> str:
    """The endpoint the file's top-level ``token`` belongs to (its own host/port or defaults)."""
    return endpoint_key(data.get("host") or DEFAULT_HOST, data.get("port") or DEFAULT_PORT)


def _file_tokens(data: Mapping[str, Any]) -> dict[str, str]:
    raw = data.get("tokens")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, str) and value:
            host, _sep, port = key.rpartition(":")
            out[endpoint_key(host or key, port)] = value
    return out


def _token_for(data: Mapping[str, Any], host: Any, port: Any) -> str | None:
    """The token the config data holds for exactly this endpoint, or None."""
    key = endpoint_key(host, port)
    mapped = _file_tokens(data).get(key)
    if mapped:
        return mapped
    top = data.get("token")
    if top and _file_endpoint(data) == key:
        return str(top)
    return None


def stored_token(host: str, port: int, path: str | os.PathLike[str] | None = None,
                 env: Mapping[str, str] | None = None) -> str | None:
    """The token the user config file stores for exactly ``host:port`` (never another's).

    Args:
        host, port: The endpoint to look up (loopback spellings are equivalent).
        path: Config file; defaults to :func:`config_path`.
        env: Environment used to locate the file.

    Returns:
        The token from the ``tokens`` map, or the top-level ``token`` when the file's own
        host/port is this endpoint; ``None`` otherwise. Never raises.
    """
    return _token_for(read_config_file(path, env), host, port)


def _coerce_toolsets(value: Any, source: str) -> str | None:
    """Toolset spec as a comma-separated string (``None`` for "all"); lists are joined."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        parts = [str(item).strip() for item in value if str(item).strip()]
    elif isinstance(value, str):
        parts = [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]
    else:
        log.warning("ignoring invalid toolsets %r from %s", value, source)
        return None
    return ",".join(parts) or None


def _coerce_port(value: Any, source: str) -> int | None:
    try:
        port = int(value)
    except (TypeError, ValueError):
        log.warning("ignoring invalid port %r from %s", value, source)
        return None
    if not (1 <= port <= 65535):
        log.warning("ignoring out-of-range port %r from %s", value, source)
        return None
    return port


def _coerce_timeout(value: Any, source: str) -> float | None:
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        log.warning("ignoring invalid timeout %r from %s", value, source)
        return None
    if timeout <= 0:
        log.warning("ignoring non-positive timeout %r from %s", value, source)
        return None
    return min(timeout, 120.0)


def load_config(
    host: str | None = None,
    port: int | None = None,
    token: str | None = None,
    timeout: float | None = None,
    env: Mapping[str, str] | None = None,
    path: str | os.PathLike[str] | None = None,
    toolsets: str | None = None,
) -> dict[str, Any]:
    """Merge CLI args, environment, the user config file and the defaults.

    Args:
        host, port, token, timeout, toolsets: Explicit (CLI) values; ``None`` means "not given".
        env: Environment mapping to read; defaults to ``os.environ``.
        path: Config file to read; defaults to :func:`config_path`.

    Returns:
        ``{"host": str, "port": int, "token": str | None, "timeout": float,
        "toolsets": str | None, "source": {...}}`` where ``source`` names where each value came
        from (``"cli"``, ``"env"``, ``"file"``, ``"default"``) — `live_status` shows it so the user
        can see why it connects where it does.

    Gotcha: a token from the file is used only for the endpoint it belongs to (see the module
    docstring). With ``LIVEBRIDGE_HOST=10.0.0.9`` and a file holding host 127.0.0.1 plus its
    token, no token is sent to 10.0.0.9 unless the file's ``tokens`` map has one for it.
    """
    env = os.environ if env is None else env
    values = dict(DEFAULTS)
    source = {key: "default" for key in DEFAULTS}

    file_data = read_config_file(path, env)
    if file_data:
        if file_data.get("host"):
            values["host"] = str(file_data["host"])
            source["host"] = "file"
        if file_data.get("port") is not None:
            p = _coerce_port(file_data["port"], "config file")
            if p is not None:
                values["port"] = p
                source["port"] = "file"
        if file_data.get("toolsets") is not None:
            ts = _coerce_toolsets(file_data["toolsets"], "config file")
            if ts is not None:
                values["toolsets"] = ts
                source["toolsets"] = "file"
        if file_data.get("timeout") is not None:
            t = _coerce_timeout(file_data["timeout"], "config file")
            if t is not None:
                values["timeout"] = t
                source["timeout"] = "file"

    env_host = env.get(ENV_KEYS["host"])
    if env_host:
        values["host"] = env_host
        source["host"] = "env"
    env_port = env.get(ENV_KEYS["port"])
    if env_port:
        p = _coerce_port(env_port, ENV_KEYS["port"])
        if p is not None:
            values["port"] = p
            source["port"] = "env"
    env_token = env.get(ENV_KEYS["token"])
    if env_token is not None and env_token != "":
        values["token"] = env_token
        source["token"] = "env"
    env_timeout = env.get(ENV_KEYS["timeout"])
    if env_timeout:
        t = _coerce_timeout(env_timeout, ENV_KEYS["timeout"])
        if t is not None:
            values["timeout"] = t
            source["timeout"] = "env"
    env_toolsets = env.get(ENV_KEYS["toolsets"])
    if env_toolsets:
        ts = _coerce_toolsets(env_toolsets, ENV_KEYS["toolsets"])
        if ts is not None:
            values["toolsets"] = ts
            source["toolsets"] = "env"

    if host:
        values["host"] = host
        source["host"] = "cli"
    if port is not None:
        p = _coerce_port(port, "cli")
        if p is not None:
            values["port"] = p
            source["port"] = "cli"
    if token is not None and token != "":
        values["token"] = token
        source["token"] = "cli"
    if timeout is not None:
        t = _coerce_timeout(timeout, "cli")
        if t is not None:
            values["timeout"] = t
            source["timeout"] = "cli"
    if toolsets:
        ts = _coerce_toolsets(toolsets, "cli")
        if ts is not None:
            values["toolsets"] = ts
            source["toolsets"] = "cli"

    if source["token"] == "default" and file_data:
        # The file's tokens only apply to the endpoint they were saved for.
        file_token = _token_for(file_data, values["host"], values["port"])
        if file_token:
            values["token"] = file_token
            source["token"] = "file"
        elif file_data.get("token") or file_data.get("tokens"):
            log.info("config file holds no token for %s (its token belongs to %s) — "
                     "connecting without one", endpoint_key(values["host"], values["port"]),
                     _file_endpoint(file_data))

    values["source"] = source
    return values


def save_config(
    host: str | None = None,
    port: int | None = None,
    token: str | None = None,
    timeout: float | None = None,
    path: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Write the given values into the user config file, keeping any other keys it has.

    Args:
        host, port, token, timeout: Values to store; ``None`` leaves the stored value alone.
            Pass ``token=""`` to remove the token stored for the (new) endpoint.
        path: Config file; defaults to :func:`config_path`.
        env: Environment mapping used to locate the home directory.

    Returns:
        The path that was written.

    Raises:
        OSError: when the file cannot be written (permissions, read-only home).

    Gotchas:
        - Tokens stay bound to their endpoint: when host/port change and no `token` is given,
          the old top-level token moves into the ``tokens`` map under the old endpoint and the
          top-level token becomes the one stored for the new endpoint (or none) — the old
          Live's secret is never paired with the new host.
        - A given `token` is stored top-level and in ``tokens["host:port"]``.
        - On POSIX the file is chmod 600 because it holds tokens.
    """
    p = Path(path).expanduser() if path is not None else config_path(env)
    data = read_config_file(p, env)
    old_endpoint = _file_endpoint(data)
    tokens = _file_tokens(data)
    if data.get("token"):
        tokens.setdefault(old_endpoint, str(data["token"]))
    if host:
        data["host"] = host
    if port is not None:
        coerced = _coerce_port(port, "save_config")
        if coerced is not None:
            data["port"] = coerced
    new_endpoint = _file_endpoint(data)
    if token is None:
        if new_endpoint != old_endpoint:
            if tokens.get(new_endpoint):
                data["token"] = tokens[new_endpoint]
            else:
                data.pop("token", None)
    elif token == "":
        data.pop("token", None)
        tokens.pop(new_endpoint, None)
    else:
        data["token"] = token
        tokens[new_endpoint] = token
    if tokens:
        data["tokens"] = tokens
    else:
        data.pop("tokens", None)
    if timeout is not None:
        coerced_t = _coerce_timeout(timeout, "save_config")
        if coerced_t is not None:
            data["timeout"] = coerced_t

    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if os.name != "nt":
        try:
            os.chmod(p, 0o600)
        except OSError:  # pragma: no cover - exotic filesystems
            log.debug("could not chmod %s", p)
    return p
