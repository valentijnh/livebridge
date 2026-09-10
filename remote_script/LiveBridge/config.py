"""Configuration for the LiveBridge remote script.

The installer writes ``config.json`` next to this file (see
``docs/ARCHITECTURE.md`` §7)::

    { "host": "127.0.0.1", "port": 9880, "token": "<random hex>",
      "allow_eval": true, "beacon": true, "beacon_port": 9881,
      "name": "Valentijn-PC", "max_clients": 8 }

Precedence: environment (``LIVEBRIDGE_HOST``, ``LIVEBRIDGE_PORT``,
``LIVEBRIDGE_TOKEN``, ``LIVEBRIDGE_ALLOW_EVAL``, ``LIVEBRIDGE_BEACON``,
``LIVEBRIDGE_BEACON_PORT``, ``LIVEBRIDGE_NAME``, ``LIVEBRIDGE_MAX_CLIENTS``,
``LIVEBRIDGE_LOG_LEVEL``) > ``config.json`` > defaults.  Environment overrides
exist so the test-suite and power users can move the port without rewriting the
installed file.

Standard library only — this module is imported inside Live.
"""

import json
import os
import socket

from . import log

#: Everything that may appear in ``config.json``.
DEFAULTS = {
    "host": "127.0.0.1",
    "port": 9880,
    "token": "",
    "allow_eval": True,
    # ``None`` = follow the mode: on in LAN mode, off on localhost (docs/NETWORK.md).
    "beacon": None,
    "beacon_port": 9881,
    "name": "",
    "max_clients": 8,
    "idle_timeout": 600.0,
    "default_timeout": 10.0,
    "max_timeout": 120.0,
    "log_level": "info",
}

_ENV_KEYS = {
    "LIVEBRIDGE_HOST": "host",
    "LIVEBRIDGE_PORT": "port",
    "LIVEBRIDGE_TOKEN": "token",
    "LIVEBRIDGE_ALLOW_EVAL": "allow_eval",
    "LIVEBRIDGE_BEACON": "beacon",
    "LIVEBRIDGE_BEACON_PORT": "beacon_port",
    "LIVEBRIDGE_NAME": "name",
    "LIVEBRIDGE_MAX_CLIENTS": "max_clients",
    "LIVEBRIDGE_IDLE_TIMEOUT": "idle_timeout",
    "LIVEBRIDGE_LOG_LEVEL": "log_level",
}

_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off")


def default_name():
    """A human readable machine name for the beacon."""
    try:
        name = socket.gethostname()
    except Exception:
        name = ""
    return name or "Live"


def config_path():
    """Absolute path of ``config.json`` (next to this module)."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def _as_bool(value, fallback):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
    return fallback


def _as_int(value, fallback, minimum=None, maximum=None):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    if minimum is not None:
        number = max(minimum, number)
    if maximum is not None:
        number = min(maximum, number)
    return number


def _as_float(value, fallback, minimum=None, maximum=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if minimum is not None:
        number = max(minimum, number)
    if maximum is not None:
        number = min(maximum, number)
    return number


class Config(object):
    """Validated configuration; attribute access, JSON-able."""

    __slots__ = tuple(DEFAULTS.keys()) + ("source",)

    def __init__(self, values=None, source=None):
        merged = dict(DEFAULTS)
        merged.update(values or {})
        self.source = source
        self.host = str(merged["host"] or "127.0.0.1").strip()
        self.port = _as_int(merged["port"], DEFAULTS["port"], 0, 65535)
        self.token = str(merged["token"] or "")
        self.allow_eval = _as_bool(merged["allow_eval"], DEFAULTS["allow_eval"])
        # Unset -> on only in LAN mode: a loopback-only bridge has nothing to advertise.
        self.beacon = _as_bool(merged["beacon"], self.lan_mode)
        self.beacon_port = _as_int(merged["beacon_port"], DEFAULTS["beacon_port"], 0, 65535)
        self.name = str(merged["name"] or "") or default_name()
        self.max_clients = _as_int(merged["max_clients"], DEFAULTS["max_clients"], 1, 64)
        self.idle_timeout = _as_float(merged["idle_timeout"], DEFAULTS["idle_timeout"], 5.0)
        self.default_timeout = _as_float(merged["default_timeout"],
                                         DEFAULTS["default_timeout"], 0.1, 600.0)
        self.max_timeout = _as_float(merged["max_timeout"], DEFAULTS["max_timeout"],
                                     self.default_timeout, 600.0)
        self.log_level = str(merged["log_level"] or "info").lower()

    @property
    def lan_mode(self):
        """True when the server is not bound to loopback only."""
        return self.host not in ("127.0.0.1", "localhost", "::1")

    @property
    def requires_token(self):
        """A token is enforced whenever one is configured."""
        return bool(self.token)

    @property
    def eval_enabled(self):
        """``eval.python`` really runs: ``allow_eval`` is on **and** a token is set.

        ``docs/ARCHITECTURE.md`` §7: eval is always token-protected, so a
        bridge without a token (manual install without config.json) refuses it.
        """
        return bool(self.allow_eval and self.token)

    def to_dict(self, include_token=False):
        """JSON-able view.  The token is redacted unless asked for."""
        data = {}
        for key in DEFAULTS:
            data[key] = getattr(self, key)
        if not include_token:
            data["token"] = "***" if self.token else ""
        return data

    def __repr__(self):
        return "<Config %s:%d token=%s eval=%s>" % (
            self.host, self.port, "yes" if self.token else "no", self.allow_eval)


def _read_file(path):
    if not path or not os.path.isfile(path):
        return {}, None
    try:
        # utf-8-sig: Windows editors (PowerShell 5.1 ``Out-File -Encoding UTF8``, old
        # Notepad, Notepad++ "UTF-8-BOM") prepend a BOM that plain utf-8 json.load rejects.
        with open(path, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except Exception as error:
        log.error("could not read config %s (%s: %s) — using the defaults "
                  "(localhost, no token)", path, type(error).__name__, error)
        return {}, None
    if not isinstance(data, dict):
        log.warn("config %s is not a JSON object — using defaults", path)
        return {}, None
    unknown = [key for key in data if key not in DEFAULTS]
    if unknown:
        log.warn("config %s: ignoring unknown keys %s", path, ", ".join(sorted(unknown)))
    return dict((k, v) for k, v in data.items() if k in DEFAULTS), path


def _read_env(environ):
    values = {}
    for env_key, key in _ENV_KEYS.items():
        if env_key in environ and environ[env_key] != "":
            values[key] = environ[env_key]
    return values


def load_config(path=None, environ=None):
    """Load the effective configuration.

    Args:
        path: config file to read; defaults to :func:`config_path`.
        environ: mapping used instead of ``os.environ`` (tests).

    Returns:
        A :class:`Config`.  Never raises — a broken file falls back to the
        defaults and logs the problem.
    """
    if path is None:
        path = config_path()
    values, source = _read_file(path)
    values.update(_read_env(os.environ if environ is None else environ))
    config = Config(values, source)
    log.set_level(config.log_level)
    if config.lan_mode and not config.token:
        # ARCHITECTURE §7 / NETWORK.md: the token is required in LAN mode.  Refuse to
        # expose an unauthenticated bridge (with eval) to the whole network.
        log.error("LAN mode (host=%s) needs a token — listening on 127.0.0.1 only. "
                  "Set \"token\" in %s (or re-run the installer with --network)",
                  config.host, path)
        config.host = "127.0.0.1"
    return config


def write_config(path, values):
    """Write ``values`` (merged over the defaults) to ``path``.

    Used by the installer and the tests; creates the parent directory.
    Returns the written dict.
    """
    config = Config(values)
    data = config.to_dict(include_token=True)
    data.pop("source", None)
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return data
