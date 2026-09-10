"""Threaded TCP JSON-lines server + UDP discovery beacon.

Wire format (``docs/PROTOCOL.md``): one UTF-8 JSON object per line, ``\\n``
terminated, max 16 MiB per line.  One reader thread per client, so requests on
one connection are executed in order.

The server never touches the Live Object Model.  It hands every parsed message
to an injected ``executor(message) -> response`` callable; in Live that is
``LiveBridge.dispatch`` (which enqueues the work for the main thread and waits
on an ``Event``), in tests it can be anything.  That is what makes this module
testable outside Live.

Typical use::

    server = BridgeServer("127.0.0.1", 9880, executor=bridge.dispatch)
    server.start()
    ...
    server.stop()
"""

import json
import re
import socket
import struct
import sys
import threading
import time

from . import log

#: Hard framing limit from ``docs/PROTOCOL.md``.
MAX_LINE_BYTES = 16 * 1024 * 1024

#: How long a poll on the accept/read socket blocks before re-checking ``_running``.
POLL_INTERVAL = 0.25

#: Beacon interval in seconds.
BEACON_INTERVAL = 2.0

#: Overall deadline for writing one response.  The socket keeps its short
#: :data:`POLL_INTERVAL` timeout (which since Python 3.5 bounds a *whole*
#: ``sendall``), so :meth:`_Client.send` loops on ``send`` until this deadline
#: instead: a multi-MB result over Wi-Fi is delivered, a dead peer still ends.
SEND_TIMEOUT = 30.0

#: A connection that has not authenticated (sent a request with the right
#: token) within this many seconds of connecting is closed.  Traffic without
#: the token does not extend it.
AUTH_TIMEOUT = 10.0

#: Most connections allowed to wait for authentication at the same time (the
#: oldest is closed when another one arrives).  They do not count against
#: ``max_clients``, so hosts without the token cannot lock the real MCP client out.
MAX_PENDING_CLIENTS = 4

#: A connection that sends this many malformed (non-JSON) lines in a row is
#: not a LiveBridge client: it is closed.
MAX_MALFORMED_LINES = 8

#: An HTTP request line ("POST / HTTP/1.1", "GET /ws HTTP/1.1", "PRI * HTTP/2.0").  A web page
#: can make the browser send a "simple" cross-origin POST to 127.0.0.1:9880 whose body is a
#: JSON line; without this check that body line would run as a bridge command.  LiveBridge
#: speaks JSON lines only, so a connection that sends an HTTP request line is closed at once
#: and nothing after it (headers, body) is ever executed.
_HTTP_REQUEST_LINE = re.compile(br"^[A-Za-z][A-Za-z_-]* \S+ HTTP/\d")

#: Sent to an HTTP client before its connection is closed.
_HTTP_REFUSAL_BODY = b"LiveBridge speaks JSON lines, not HTTP.\r\n"
_HTTP_REFUSAL = (b"HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain\r\n"
                 b"Connection: close\r\nContent-Length: "
                 + str(len(_HTTP_REFUSAL_BODY)).encode("ascii") + b"\r\n\r\n"
                 + _HTTP_REFUSAL_BODY)


def _encode(message):
    """One JSON line, UTF-8, newline terminated."""
    text = json.dumps(message, ensure_ascii=False, separators=(",", ":"),
                      default=str)
    return text.encode("utf-8") + b"\n"


def _set_reuse_options(sock):
    """Make a listening socket reload-friendly on every OS.

    * macOS/Linux: ``SO_REUSEADDR`` lets a reloaded script bind again while
      the previous socket lingers in TIME_WAIT.
    * Windows: ``SO_REUSEADDR`` would let a *second* socket bind a port that is
      still actively listening (a stale script instance would silently share
      it), so use ``SO_EXCLUSIVEADDRUSE`` instead.  Its catch: the port cannot
      be bound again while an *accepted* connection lingers in TIME_WAIT, so
      :meth:`BridgeServer.stop` resets its clients there (:data:`RESET_ON_STOP`)
      and ``LiveBridge`` keeps retrying a failed bind.
    """
    if sys.platform.startswith("win"):
        option = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if option is not None:
            try:
                sock.setsockopt(socket.SOL_SOCKET, option, 1)
            except OSError:
                pass
        return
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)


#: Close client sockets with an RST (``SO_LINGER`` 0) in :meth:`BridgeServer.stop`
#: so no accepted connection is left in TIME_WAIT — needed on Windows, where
#: ``SO_EXCLUSIVEADDRUSE`` refuses to bind the port again until those are gone.
RESET_ON_STOP = sys.platform.startswith("win")


def _abort_on_close(sock):
    """Make ``sock.close()`` send an RST instead of FIN (no TIME_WAIT). Best effort."""
    # struct linger is {u_short, u_short} on Windows and {int, int} on macOS/Linux.
    layout = "HH" if sys.platform.startswith("win") else "ii"
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack(layout, 1, 0))
        return True
    except Exception:
        return False


def local_ip():
    """Best-effort LAN IP of this machine (``127.0.0.1`` when offline).

    Uses a connectionless UDP socket so nothing is actually sent and it never
    blocks on DNS.
    """
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0.2)
        sock.connect(("8.8.8.8", 9))
        return sock.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass


class _Client(object):
    """One connected client: its socket, its reader thread and a send lock.

    ``authenticated`` is set by the first request that carries an acceptable
    token (at once when the bridge has no token).  Until then the connection
    counts as *pending*: it is closed :data:`AUTH_TIMEOUT` seconds after it
    connected, and on its first ``auth`` error.
    """

    def __init__(self, server, sock, address, authenticated=True):
        self.server = server
        self.sock = sock
        self.address = address
        self.send_lock = threading.Lock()
        self.connected_at = time.time()
        self.last_activity = self.connected_at
        self.authenticated = bool(authenticated)
        self.malformed = 0
        self.closed = False
        self.thread = threading.Thread(target=self._run, name="LiveBridge-client")
        self.thread.daemon = True

    # -- lifecycle -------------------------------------------------------
    def start(self):
        self.thread.start()

    def close(self, reset=False):
        """Close the socket.  ``reset`` sends an RST (no TIME_WAIT, see :data:`RESET_ON_STOP`)."""
        if self.closed:
            return
        self.closed = True
        if reset and _abort_on_close(self.sock):
            pass        # no shutdown(): a FIN first would defeat the abortive close
        else:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
        try:
            self.sock.close()
        except Exception:
            pass

    def send(self, message):
        """Send one response line; closes the client on a dead socket.

        Writes with ``send`` in a loop bounded by :data:`SEND_TIMEOUT` rather than
        ``sendall``: the socket's 0.25 s poll timeout would otherwise cut any
        response the peer cannot absorb within 0.25 s (big results over Wi-Fi),
        and the MCP client would re-send the command on a fresh connection.
        """
        try:
            data = _encode(message)
        except Exception:
            log.exception("could not encode a response")
            data = _encode({"id": message.get("id") if isinstance(message, dict) else None,
                            "ok": False,
                            "error": {"type": "internal",
                                      "message": "result is not JSON serialisable"}})
        with self.send_lock:
            if self.closed:
                return False
            try:
                self._send_all(data)
                return True
            except Exception as error:
                log.debug("dropping client %s: send failed (%s)", self._who(), error)
                self.close()
                return False

    def _send_all(self, data):
        """``sendall`` with an overall deadline instead of the per-call socket timeout."""
        view = memoryview(data)
        deadline = time.time() + SEND_TIMEOUT
        while view:
            try:
                sent = self.sock.send(view)
            except socket.timeout:
                if self.closed or time.time() > deadline:
                    raise
                continue
            except InterruptedError:
                continue
            if sent <= 0:
                raise OSError("connection closed while sending")
            view = view[sent:]

    # -- reading ---------------------------------------------------------
    def _run(self):
        buffer = bytearray()
        self.sock.settimeout(POLL_INTERVAL)
        try:
            while self.server.running and not self.closed:
                if self._expired():
                    break
                try:
                    chunk = self.sock.recv(65536)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                if self.authenticated:
                    self.last_activity = time.time()
                buffer.extend(chunk)
                if len(buffer) > MAX_LINE_BYTES:
                    self.send({"id": None, "ok": False,
                               "error": {"type": "bad_request",
                                         "message": "line longer than %d bytes — page the "
                                                    "request with offset/limit"
                                                    % MAX_LINE_BYTES}})
                    break
                while True:
                    newline = buffer.find(b"\n")
                    if newline < 0:
                        break
                    line = bytes(buffer[:newline])
                    del buffer[:newline + 1]
                    self._handle_line(line)
                    if self.closed:
                        return
        except Exception:
            log.exception("client %s crashed", self._who())
        finally:
            self.close()
            self.server._remove_client(self)

    def _expired(self):
        """Idle timeout for authenticated clients, :data:`AUTH_TIMEOUT` for pending ones."""
        now = time.time()
        if not self.authenticated:
            if now - self.connected_at > self.server.auth_timeout:
                log.info("closing %s: no valid token within %.0fs", self._who(),
                         self.server.auth_timeout)
                return True
            return False
        timeout = self.server.idle_timeout
        if timeout and (now - self.last_activity) > timeout:
            log.info("closing idle client %s", self._who())
            return True
        return False

    def _who(self):
        try:
            return "%s:%d" % self.address[:2]
        except Exception:
            return str(self.address)

    def _refuse_http(self, line):
        """Close a connection that sent an HTTP request line (see ``_HTTP_REQUEST_LINE``)."""
        log.warn("closing %s: it sent an HTTP request (%r); LiveBridge speaks JSON lines, "
                 "not HTTP (a web page may be probing localhost)",
                 self._who(), line[:60].decode("latin-1"))
        with self.send_lock:
            if not self.closed:
                try:
                    self._send_all(_HTTP_REFUSAL)
                except Exception:
                    pass
        self.close()

    def _handle_line(self, line):
        line = line.strip()
        if not line:
            return
        if _HTTP_REQUEST_LINE.match(line):
            self._refuse_http(line)
            return
        try:
            message = json.loads(line.decode("utf-8"))
        except Exception as error:
            self.malformed += 1
            self.send({"id": None, "ok": False,
                       "error": {"type": "bad_request",
                                 "message": "malformed JSON: %s" % error}})
            if self.malformed >= MAX_MALFORMED_LINES:
                log.warn("closing %s: %d malformed lines in a row (not a LiveBridge client)",
                         self._who(), self.malformed)
                self.close()
            return
        self.malformed = 0
        if not self.authenticated and self.server.is_authentic(message):
            if not self.server._promote(self):
                return
        try:
            response = self.server.executor(message)
        except Exception as error:
            log.exception("executor failed")
            response = {"id": message.get("id") if isinstance(message, dict) else None,
                        "ok": False,
                        "error": {"type": "internal",
                                  "message": "%s: %s" % (type(error).__name__, error),
                                  "traceback": log.current_traceback()}}
        if response is not None:
            self.send(response)
            if _is_auth_error(response):
                # A wrong/missing token: answer, then hang up so a host without the
                # token cannot keep the connection (and a max_clients slot) forever.
                log.info("closing %s after an auth error", self._who())
                self.close()


def _is_auth_error(response):
    try:
        return (response.get("ok") is False
                and (response.get("error") or {}).get("type") == "auth")
    except Exception:
        return False


class BridgeServer(object):
    """The TCP JSON-lines server.

    Args:
        host: ``127.0.0.1`` (local) or ``0.0.0.0`` (LAN).
        port: TCP port; ``0`` binds an ephemeral port (tests) — read
            :attr:`port` after :meth:`start`.
        executor: ``callable(message_dict) -> response_dict``; runs on the
            reader thread and must not touch the LOM itself.
        max_clients: most *authenticated* connections; extra ones are
            rejected with an ``invalid_state`` line and closed.
        idle_timeout: seconds before an idle connection is closed (0 = never).
        authenticate: ``callable(message_or_None) -> bool`` — ``True`` when the
            message carries an acceptable token.  It is called with ``None`` on
            accept: ``True`` there means no token is needed and every connection
            is authenticated at once.  ``None`` (default) = no authentication.
            Pending (unauthenticated) connections are capped at ``max_pending``
            (the oldest is closed for a new one), closed ``auth_timeout`` seconds
            after they connected and after their first ``auth`` error, and never
            count against ``max_clients``.
    """

    def __init__(self, host, port, executor, max_clients=4, idle_timeout=600.0,
                 authenticate=None, auth_timeout=AUTH_TIMEOUT,
                 max_pending=MAX_PENDING_CLIENTS):
        self.host = host
        self.port = int(port)
        self.executor = executor
        self.max_clients = max(1, int(max_clients))
        self.idle_timeout = float(idle_timeout or 0)
        self.authenticate = authenticate
        self.auth_timeout = float(auth_timeout)
        self.max_pending = max(1, int(max_pending))
        self.running = False
        self._socket = None
        self._thread = None
        self._clients = []
        self._lock = threading.Lock()

    # -- lifecycle -------------------------------------------------------
    def start(self):
        """Bind, listen and start accepting on a daemon thread.

        Raises:
            OSError: when the port cannot be bound (another Live instance, or
                a previous script that did not shut down).
        """
        if self.running:
            return self.port
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _set_reuse_options(sock)
        try:
            sock.bind((self.host, self.port))
        except OSError:
            try:
                sock.close()
            except Exception:
                pass
            raise
        sock.listen(8)
        sock.settimeout(POLL_INTERVAL)
        self._socket = sock
        self.port = sock.getsockname()[1]
        self.running = True
        self._thread = threading.Thread(target=self._accept_loop, name="LiveBridge-accept")
        self._thread.daemon = True
        self._thread.start()
        return self.port

    def stop(self):
        """Stop accepting, close every client and release the port."""
        if not self.running:
            return
        self.running = False
        sock, self._socket = self._socket, None
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
        with self._lock:
            clients = list(self._clients)
            self._clients = []
        for client in clients:
            client.close(reset=RESET_ON_STOP)
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        for client in clients:
            if client.thread is not threading.current_thread():
                client.thread.join(timeout=1.0)

    @property
    def client_count(self):
        """How many connections are open right now (authenticated or not)."""
        with self._lock:
            return len(self._clients)

    @property
    def authenticated_count(self):
        """How many connections have authenticated (these count against ``max_clients``)."""
        with self._lock:
            return sum(1 for c in self._clients if c.authenticated)

    @property
    def pending_count(self):
        """How many connections are still waiting to authenticate."""
        with self._lock:
            return sum(1 for c in self._clients if not c.authenticated)

    def is_authentic(self, message):
        """``True`` when ``message`` (``None`` = a fresh connection) needs no more auth."""
        if self.authenticate is None:
            return True
        try:
            return bool(self.authenticate(message))
        except Exception:
            log.exception("authenticate() failed")
            return False

    def broadcast(self, message):
        """Send an unsolicited line (an event) to every connected client."""
        with self._lock:
            clients = list(self._clients)
        for client in clients:
            client.send(message)

    # -- internals -------------------------------------------------------
    def _accept_loop(self):
        while self.running:
            sock = self._socket
            if sock is None:
                break
            try:
                connection, address = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            except Exception:
                log.exception("accept failed")
                break
            try:
                connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except Exception:
                pass
            authenticated = self.is_authentic(None)
            evicted = None
            with self._lock:
                if authenticated:
                    full = sum(1 for c in self._clients if c.authenticated) >= self.max_clients
                else:
                    full = False
                    pending = [c for c in self._clients if not c.authenticated]
                    if len(pending) >= self.max_pending:
                        # Evict the oldest pending connection rather than refusing the new
                        # one: a real client sends its token right after connecting, so
                        # hosts without the token cannot keep new clients out.
                        evicted = min(pending, key=lambda c: c.connected_at)
            if full:
                log.warn("refusing connection from %s: %d clients already connected",
                         address, self.max_clients)
                self._refuse(connection, "too many clients (max %d)" % self.max_clients)
                continue
            if evicted is not None:
                log.info("closing %s: too many connections waiting to authenticate (max %d)",
                         evicted._who(), self.max_pending)
                evicted.close()
                self._remove_client(evicted)
            client = _Client(self, connection, address, authenticated=authenticated)
            with self._lock:
                self._clients.append(client)
            log.debug("client connected from %s (%d total)", address, self.client_count)
            client.start()

    @staticmethod
    def _refuse(connection, message):
        """Send the connection-level rejection line, then close without losing it.

        Closing right after ``sendall`` makes the kernel answer the client's
        unread request with an RST, and the RST discards the rejection line
        before the client reads it (verified on macOS). So a short-lived daemon
        thread half-closes (FIN after the line), drains what the client sent for
        up to :data:`REJECT_DRAIN_SECONDS` and only then closes.
        """
        try:
            connection.settimeout(1.0)
            connection.sendall(_encode({"id": None, "ok": False,
                                        "error": {"type": "invalid_state",
                                                  "message": message}}))
        except Exception:
            try:
                connection.close()
            except Exception:
                pass
            return
        thread = threading.Thread(target=_drain_and_close, args=(connection,),
                                  name="LiveBridge-reject")
        thread.daemon = True
        thread.start()

    def _promote(self, client):
        """Mark a pending client authenticated; ``False`` (and closed) when the slots are full."""
        with self._lock:
            full = sum(1 for c in self._clients if c.authenticated) >= self.max_clients
            if not full:
                client.authenticated = True
                client.last_activity = time.time()
        if full:
            log.warn("refusing %s: %d clients already connected", client._who(),
                     self.max_clients)
            client.send({"id": None, "ok": False,
                         "error": {"type": "invalid_state",
                                   "message": "too many clients (max %d)" % self.max_clients}})
            client.close()
            return False
        return True

    def _remove_client(self, client):
        with self._lock:
            if client in self._clients:
                self._clients.remove(client)


#: How long a refused connection is drained before it is closed (see ``_refuse``).
REJECT_DRAIN_SECONDS = 0.5


def _drain_and_close(connection, seconds=None):
    """Half-close ``connection``, read until EOF or ``seconds`` passed, then close it."""
    limit = REJECT_DRAIN_SECONDS if seconds is None else seconds
    try:
        try:
            connection.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        deadline = time.time() + limit
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            try:
                connection.settimeout(remaining)
                if not connection.recv(65536):
                    break
            except (OSError, ValueError):
                break
    finally:
        try:
            connection.close()
        except Exception:
            pass


class Beacon(object):
    """UDP discovery beacon.

    Broadcasts ``payload_provider()`` as one JSON datagram to
    ``255.255.255.255:<port>`` (and ``127.0.0.1:<port>``, so discovery also
    works when broadcast is filtered) every :data:`BEACON_INTERVAL` seconds.
    Best effort: it logs the first failure and then keeps quiet — a beacon must
    never take the bridge down.
    """

    def __init__(self, payload_provider, port=9881, interval=BEACON_INTERVAL):
        self.payload_provider = payload_provider
        self.port = int(port)
        self.interval = float(interval)
        self.running = False
        self._thread = None
        self._stop = threading.Event()
        self._warned = False

    def start(self):
        """Start broadcasting on a daemon thread."""
        if self.running:
            return
        self._stop.clear()
        self.running = True
        self._thread = threading.Thread(target=self._run, name="LiveBridge-beacon")
        self._thread.daemon = True
        self._thread.start()

    def stop(self):
        """Stop broadcasting."""
        if not self.running:
            return
        self.running = False
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def payload(self):
        """The dict that is broadcast (also used by ``system.hello``)."""
        try:
            data = self.payload_provider()
        except Exception:
            log.exception("beacon payload failed")
            return None
        return data if isinstance(data, dict) else None

    def _run(self):
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except Exception:
            log.exception("could not open the beacon socket")
            self.running = False
            return
        try:
            while self.running and not self._stop.is_set():
                self._send_once(sock)
                self._stop.wait(self.interval)
        finally:
            try:
                sock.close()
            except Exception:
                pass

    def _send_once(self, sock):
        payload = self.payload()
        if payload is None:
            return
        try:
            data = _encode(payload).rstrip(b"\n")
        except Exception:
            return
        for target in ("255.255.255.255", "127.0.0.1"):
            try:
                sock.sendto(data, (target, self.port))
            except Exception:
                if not self._warned:
                    self._warned = True
                    log.debug("beacon send to %s failed (this is usually a firewall)",
                              target)
