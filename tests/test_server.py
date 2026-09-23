"""Socket-level tests: framing, auth, concurrency, big payloads and the beacon."""

import json
import socket
import threading
import time

import pytest

from LiveBridge import server as server_module


# --------------------------------------------------------------------------
# the server on its own (injected executor, no Live at all)
# --------------------------------------------------------------------------

@pytest.fixture
def echo_server():
    """A BridgeServer whose executor just echoes — pure protocol testing."""
    servers = []

    def make(executor=None, **kwargs):
        def default(message):
            return {"id": message.get("id"), "ok": True, "result": message.get("args")}

        server = server_module.BridgeServer("127.0.0.1", 0, executor or default,
                                            **kwargs)
        server.start()
        servers.append(server)
        return server

    yield make
    for server in servers:
        server.stop()


def test_round_trip(echo_server, tcp_client):
    server = echo_server()
    client = tcp_client(server.port)
    response = client.request("echo", {"hello": "world"})
    assert response["ok"] is True
    assert response["result"] == {"hello": "world"}


def test_malformed_json_gets_bad_request(echo_server, tcp_client):
    server = echo_server()
    client = tcp_client(server.port)
    client.send_raw(b"{not json}\n")
    response = client.receive()
    assert response["id"] is None
    assert response["error"]["type"] == "bad_request"
    assert "malformed JSON" in response["error"]["message"]
    # the connection stays usable
    assert client.request("echo", {"a": 1})["ok"] is True


def test_blank_lines_are_ignored(echo_server, tcp_client):
    server = echo_server()
    client = tcp_client(server.port)
    client.send_raw(b"\n\n")
    assert client.request("echo", {"a": 1})["result"] == {"a": 1}


def test_several_requests_in_one_packet_keep_their_order(echo_server, tcp_client):
    server = echo_server()
    client = tcp_client(server.port)
    payload = b"".join(json.dumps({"id": str(i), "cmd": "echo",
                                   "args": {"n": i}}).encode() + b"\n"
                       for i in range(5))
    client.send_raw(payload)
    ids = [client.receive()["id"] for _ in range(5)]
    assert ids == ["0", "1", "2", "3", "4"]


def test_http_requests_are_refused_and_their_body_never_runs(echo_server):
    """A browser can POST a JSON line to localhost from any web page ("simple" request, no
    preflight).  The server must close such a connection at the request line so the JSON
    body is never executed."""
    seen = []

    def record(message):
        seen.append(message)
        return {"id": message.get("id"), "ok": True, "result": None}

    server = echo_server(executor=record)
    body = b'\n{"id":"x","cmd":"eval.python","args":{"expr":"1"}}\n'
    request = (b"POST / HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: text/plain\r\n"
               b"Origin: https://evil.example\r\nContent-Length: %d\r\n\r\n" % len(body)
               + body)
    sock = socket.create_connection(("127.0.0.1", server.port), timeout=5)
    try:
        sock.sendall(request)
        data = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
    finally:
        sock.close()
    assert data.startswith(b"HTTP/1.1 403 Forbidden")
    assert b"JSON lines" in data
    assert seen == []
    deadline = time.time() + 2
    while server.client_count and time.time() < deadline:
        time.sleep(0.02)
    assert server.client_count == 0


def test_json_lines_that_mention_http_still_work(echo_server, tcp_client):
    server = echo_server()
    client = tcp_client(server.port)
    response = client.request("echo", {"text": "GET / HTTP/1.1"})
    assert response["result"] == {"text": "GET / HTTP/1.1"}


def test_max_clients_is_enforced(echo_server, tcp_client):
    server = echo_server(max_clients=2)
    first = tcp_client(server.port)
    second = tcp_client(server.port)
    assert first.request("echo", {"a": 1})["ok"] is True
    assert second.request("echo", {"a": 1})["ok"] is True
    third = tcp_client(server.port)
    response = third.receive()
    assert response["error"]["type"] == "invalid_state"
    assert "too many clients" in response["error"]["message"]


def test_idle_connections_are_closed(echo_server, tcp_client):
    server = echo_server(idle_timeout=0.4)
    client = tcp_client(server.port)
    assert client.request("echo", {})["ok"] is True
    with pytest.raises(EOFError):
        client.receive()
    # the client thread unregisters itself just after closing the socket
    assert _wait_for(lambda: server.client_count == 0)


def test_executor_exceptions_become_internal_errors(echo_server, tcp_client):
    def broken(_message):
        raise RuntimeError("handler exploded")

    server = echo_server(executor=broken)
    client = tcp_client(server.port)
    response = client.request("boom")
    assert response["error"]["type"] == "internal"
    assert "handler exploded" in response["error"]["message"]
    assert "Traceback" in response["error"]["traceback"]


def test_unserialisable_results_do_not_kill_the_connection(echo_server, tcp_client):
    def weird(message):
        return {"id": message.get("id"), "ok": True, "result": object()}

    server = echo_server(executor=weird)
    client = tcp_client(server.port)
    response = client.request("weird")
    # ``default=str`` keeps it valid JSON rather than dropping the answer
    assert response["ok"] is True
    assert isinstance(response["result"], str)


def test_stop_releases_the_port(echo_server):
    server = echo_server()
    port = server.port
    server.stop()
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind(("127.0.0.1", port))   # must not raise "address in use"
    finally:
        probe.close()


def test_server_restarts_on_the_same_port(echo_server):
    server = echo_server()
    port = server.port
    server.stop()
    again = server_module.BridgeServer("127.0.0.1", port, lambda m: None)
    try:
        again.start()
        assert again.port == port
    finally:
        again.stop()


def test_broadcast_reaches_every_client(echo_server, tcp_client):
    server = echo_server()
    clients = [tcp_client(server.port) for _ in range(2)]
    for client in clients:
        client.request("echo", {})
    server.broadcast({"event": "song.is_playing", "data": {"value": True}})
    for client in clients:
        assert client.receive()["event"] == "song.is_playing"


# --------------------------------------------------------------------------
# the real bridge over TCP
# --------------------------------------------------------------------------

def test_tcp_bridge_round_trip(tcp_bridge, tcp_client, song):
    client = tcp_client(tcp_bridge.port)
    assert client.request("system.ping")["result"]["pong"] is True
    hello = client.request("system.hello")["result"]
    assert hello["name"] == "LiveBridge"
    tracks = client.request("lom.children", {"path": "song.tracks"})["result"]
    assert [item["name"] for item in tracks["items"]] == ["Bass", "Vocals", "Drums"]
    assert client.request("lom.set", {"path": "song", "prop": "tempo",
                                      "value": 132})["result"]["value"] == 132.0
    assert song.tempo == 132.0


def test_tcp_bridge_errors_travel(tcp_bridge, tcp_client):
    client = tcp_client(tcp_bridge.port)
    response = client.request("nope.nope")
    assert response["ok"] is False
    assert response["error"]["type"] == "unknown_command"
    response = client.request("lom.get", {"path": "song.tracks[42]"})
    assert response["error"]["type"] == "not_found"


def test_tcp_auth(bridge_factory, tcp_client):
    script = bridge_factory(mode="queue", auto_start=True, port=0, token="s3cret")
    stop = threading.Event()

    def pump():
        while not stop.is_set():
            script.update_display()
            stop.wait(0.005)

    thread = threading.Thread(target=pump)
    thread.daemon = True
    thread.start()
    try:
        # every auth error is answered and then the connection is closed
        client = tcp_client(script.port)
        assert client.request("system.ping")["error"]["type"] == "auth"
        with pytest.raises((EOFError, OSError)):
            client.receive()
        client = tcp_client(script.port)
        assert client.request("system.ping", token="nope")["error"]["type"] == "auth"
        with pytest.raises((EOFError, OSError)):
            client.receive()
        client = tcp_client(script.port)
        assert client.request("system.ping", token="s3cret")["ok"] is True
        assert client.request("system.ping", token="s3cret")["ok"] is True
    finally:
        stop.set()
        thread.join(timeout=2)


@pytest.fixture
def token_bridge(bridge_factory):
    """A tokened LiveBridge with the real TCP server and a pump thread."""
    made = []

    def make(**overrides):
        values = {"token": "s3cret", "port": 0}
        values.update(overrides)
        script = bridge_factory(mode="queue", auto_start=True, **values)
        stop = threading.Event()

        def pump():
            while not stop.is_set():
                script.update_display()
                stop.wait(0.005)

        thread = threading.Thread(target=pump)
        thread.daemon = True
        thread.start()
        made.append((stop, thread))
        return script

    yield make
    for stop, thread in made:
        stop.set()
        thread.join(timeout=2)


def _wait_for(condition, seconds=5.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return condition()


def test_unauthenticated_connections_cannot_take_the_client_slots(token_bridge, tcp_client):
    """Hosts without the token must not lock the real MCP client out: pending connections
    do not count against max_clients (they have their own small cap)."""
    script = token_bridge(max_clients=2)
    squatters = [tcp_client(script.port) for _ in range(3)]
    assert _wait_for(lambda: script.server.pending_count == 3)
    first = tcp_client(script.port)
    second = tcp_client(script.port)
    assert first.request("system.ping", token="s3cret")["ok"] is True
    response = second.request("system.ping", token="s3cret")
    assert response["ok"] is True, response
    assert script.server.authenticated_count == 2
    # a third *authenticated* client is over max_clients
    third = tcp_client(script.port)
    response = third.request("system.ping", token="s3cret")
    assert response["error"]["type"] == "invalid_state"
    assert "too many clients" in response["error"]["message"]
    assert squatters


def test_pending_connections_are_capped_by_evicting_the_oldest(token_bridge, tcp_client):
    """A flood of token-less connections is capped (threads), but it cannot keep a real
    client from connecting: the oldest pending connection makes room."""
    script = token_bridge()
    script.server.max_pending = 2
    oldest = tcp_client(script.port)
    assert _wait_for(lambda: script.server.pending_count == 1)
    newer = tcp_client(script.port)
    assert _wait_for(lambda: script.server.pending_count == 2)
    real = tcp_client(script.port)
    with pytest.raises((EOFError, OSError)):
        oldest.receive()
    assert real.request("system.ping", token="s3cret")["ok"] is True
    assert script.server.pending_count <= 2
    assert newer


def test_unauthenticated_connections_time_out_even_when_they_keep_talking(token_bridge,
                                                                          tcp_client):
    script = token_bridge()
    script.server.auth_timeout = 0.5
    squatter = tcp_client(script.port, timeout=5.0)
    started = time.time()
    with pytest.raises((EOFError, OSError)):
        while time.time() - started < 4:
            squatter.send_raw(b"\n")          # traffic without a token does not count
            time.sleep(0.1)
            squatter.sock.settimeout(0.05)
            try:
                if not squatter.sock.recv(1):
                    raise EOFError("closed")
            except socket.timeout:
                pass
    assert time.time() - started < 3
    # an authenticated client is not subject to the short timeout
    client = tcp_client(script.port)
    assert client.request("system.ping", token="s3cret")["ok"] is True
    time.sleep(0.8)
    assert client.request("system.ping", token="s3cret")["ok"] is True


def test_lan_mode_without_a_token_accepts_nothing(bridge_factory):
    """Defence in depth for a Config built without load_config (which already falls
    back to 127.0.0.1): host 0.0.0.0 + no token must not mean 'anyone may control Live'."""
    script = bridge_factory(host="0.0.0.0", token="")
    response = script.dispatch({"id": "1", "cmd": "system.ping"})
    assert response["error"]["type"] == "auth"
    assert "LAN mode requires a token" in response["error"]["message"]


def test_tokens_are_compared_in_constant_time(bridge_factory, monkeypatch):
    import hmac

    calls = []
    real = hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(hmac, "compare_digest", spy)
    script = bridge_factory(token="s3cret")
    assert script.dispatch({"id": "1", "cmd": "system.ping", "token": "s3cret"})["ok"]
    assert script.dispatch({"id": "1", "cmd": "system.ping",
                            "token": "s3cre7"})["error"]["type"] == "auth"
    assert len(calls) == 2


def test_two_megabyte_result(bridge_factory, tcp_client):
    script = bridge_factory(mode="queue", auto_start=True, port=0, token="s3cret")
    stop = threading.Event()

    def pump():
        while not stop.is_set():
            script.update_display()
            stop.wait(0.005)

    thread = threading.Thread(target=pump)
    thread.daemon = True
    thread.start()
    try:
        client = tcp_client(script.port, timeout=30.0)
        response = client.request("eval.python", {"expr": "'x' * 2000000"}, timeout=30,
                                  token="s3cret")
        assert response["ok"] is True
        assert len(response["result"]["result"]) == 2000000
    finally:
        stop.set()
        thread.join(timeout=2)


def test_big_responses_reach_a_slow_reader_intact(echo_server):
    """The socket's 0.25 s poll timeout bounds a whole sendall(): a peer that cannot
    absorb the answer that fast (Wi-Fi LAN) used to get a truncated line and a closed
    socket — and the MCP client then re-sent the command."""
    big = "y" * 5000000

    def executor(message):
        return {"id": message.get("id"), "ok": True, "result": big}

    server = echo_server(executor=executor)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 16384)
    sock.settimeout(10.0)
    sock.connect(("127.0.0.1", server.port))
    try:
        sock.sendall(b'{"id":"big","cmd":"x"}\n')
        time.sleep(0.8)                   # the server is stuck on a full buffer meanwhile
        data = bytearray()
        while not data.endswith(b"\n"):
            chunk = sock.recv(16384)
            if not chunk:
                break
            data.extend(chunk)
            time.sleep(0.0005)
    finally:
        sock.close()
    response = json.loads(bytes(data).decode("utf-8"))
    assert response["id"] == "big" and len(response["result"]) == 5000000


def test_many_malformed_lines_close_the_connection(echo_server, tcp_client):
    server = echo_server()
    client = tcp_client(server.port)
    client.send_raw(b"garbage\n" * server_module.MAX_MALFORMED_LINES)
    for _ in range(server_module.MAX_MALFORMED_LINES):
        assert client.receive()["error"]["type"] == "bad_request"
    with pytest.raises((EOFError, OSError)):
        client.receive()


def test_any_http_method_is_refused(echo_server):
    server = echo_server()
    sock = socket.create_connection(("127.0.0.1", server.port), timeout=5)
    try:
        sock.sendall(b"PROPFIND /x HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        data = sock.recv(4096)
    finally:
        sock.close()
    assert data.startswith(b"HTTP/1.1 403")


def test_stop_can_reset_clients_so_no_time_wait_blocks_a_rebind(echo_server, tcp_client,
                                                                 monkeypatch):
    """Windows: SO_EXCLUSIVEADDRUSE refuses the port while an accepted connection is in
    TIME_WAIT, so stop() closes clients with an RST there (RESET_ON_STOP)."""
    monkeypatch.setattr(server_module, "RESET_ON_STOP", True)
    server = echo_server()
    port = server.port
    client = tcp_client(port)
    assert client.request("echo", {})["ok"] is True
    server.stop()
    with pytest.raises((EOFError, OSError)):
        client.receive()
    again = server_module.BridgeServer("127.0.0.1", port, lambda m: None)
    try:
        again.start()
        assert again.port == port
    finally:
        again.stop()


def test_two_concurrent_clients(tcp_bridge, tcp_client):
    first = tcp_client(tcp_bridge.port, timeout=30.0)
    second = tcp_client(tcp_bridge.port, timeout=30.0)
    results = {}

    def hammer(name, client, cmd, args):
        answers = []
        for index in range(20):
            answers.append(client.request(cmd, args, id="%s-%d" % (name, index)))
        results[name] = answers

    threads = [
        threading.Thread(target=hammer, args=("a", first, "system.ping", None)),
        threading.Thread(target=hammer, args=("b", second, "lom.get",
                                              {"path": "song.tracks[0]",
                                               "detail": "minimal"})),
    ]
    for thread in threads:
        thread.daemon = True
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert len(results["a"]) == 20 and len(results["b"]) == 20
    assert all(r["ok"] for r in results["a"] + results["b"])
    assert [r["id"] for r in results["a"]] == ["a-%d" % i for i in range(20)]
    assert all(r["result"]["name"] == "Bass" for r in results["b"])


def test_client_disconnect_is_clean(tcp_bridge, tcp_client):
    client = tcp_client(tcp_bridge.port)
    client.request("system.ping")
    assert tcp_bridge.server.client_count == 1
    client.close()
    deadline = time.time() + 5
    while tcp_bridge.server.client_count and time.time() < deadline:
        time.sleep(0.02)
    assert tcp_bridge.server.client_count == 0


# --------------------------------------------------------------------------
# beacon
# --------------------------------------------------------------------------

def test_beacon_payload_shape(bridge):
    payload = bridge.beacon_payload()
    assert payload["livebridge"] == 1
    assert payload["name"] == "TestMachine"
    assert payload["live"] == "12.4.5"
    assert payload["needs_token"] is False
    assert isinstance(payload["port"], int)
    assert isinstance(payload["host"], str)


def test_beacon_broadcasts(udp_listener):
    sock, port = udp_listener()
    beacon = server_module.Beacon(
        lambda: {"livebridge": 1, "name": "TestBox", "port": 9880,
                 "live": "12.4.5", "needs_token": False},
        port=port, interval=0.1)
    beacon.start()
    try:
        data, _address = sock.recvfrom(65535)
        payload = json.loads(data.decode("utf-8"))
        assert payload["livebridge"] == 1
        assert payload["name"] == "TestBox"
        assert payload["port"] == 9880
    finally:
        beacon.stop()
    assert beacon.running is False


def test_beacon_survives_a_broken_payload(udp_listener):
    sock, port = udp_listener(timeout=0.5)

    def broken():
        raise RuntimeError("no payload today")

    beacon = server_module.Beacon(broken, port=port, interval=0.05)
    beacon.start()
    try:
        time.sleep(0.2)
        assert beacon.running is True
        with pytest.raises(socket.timeout):
            sock.recvfrom(65535)
    finally:
        beacon.stop()


def test_beacon_stop_is_idempotent(udp_listener):
    _sock, port = udp_listener()
    beacon = server_module.Beacon(lambda: {"livebridge": 1}, port=port, interval=0.1)
    beacon.start()
    beacon.start()
    beacon.stop()
    beacon.stop()
    assert beacon.running is False


def test_local_ip_is_a_string():
    assert isinstance(server_module.local_ip(), str)
    assert server_module.local_ip().count(".") == 3


def test_rejected_client_reads_the_rejection_line_not_a_reset(echo_server):
    """The 'too many clients' line survives even when the client sent a request first.

    An inline close() after sendall() lets the kernel answer the unread request with an RST,
    which discards the rejection line (verified on macOS); the server now half-closes and drains.
    """
    server = echo_server(max_clients=1)
    holder = socket.create_connection(("127.0.0.1", server.port), timeout=5.0)
    holder.sendall(b'{"id": "h", "cmd": "echo", "args": {}}\n')
    assert json.loads(holder.makefile("rb").readline())["ok"] is True
    for _attempt in range(3):
        second = socket.create_connection(("127.0.0.1", server.port), timeout=5.0)
        try:
            second.sendall(b'{"id": "x", "cmd": "echo", "args": {"a": 1}}\n')
            time.sleep(0.2)                      # let a premature close/RST arrive first
            line = second.makefile("rb").readline()
        finally:
            second.close()
        response = json.loads(line)
        assert response["id"] is None and response["ok"] is False
        assert response["error"]["type"] == "invalid_state"
        assert "too many clients" in response["error"]["message"]
    holder.close()
    names = [t.name for t in threading.enumerate()]
    deadline = time.time() + 2.0
    while "LiveBridge-reject" in names and time.time() < deadline:
        time.sleep(0.05)
        names = [t.name for t in threading.enumerate()]
    assert "LiveBridge-reject" not in names   # the drain threads end by themselves


def test_refuse_half_closes_and_drains_before_closing():
    """_refuse must not close() right after sendall(): FIN first, drain, then close."""
    events = []
    done = threading.Event()

    class FakeConnection(object):
        def __init__(self):
            self.chunks = [b"pending request\n", b""]

        def settimeout(self, value):
            events.append("settimeout")

        def sendall(self, data):
            events.append(("sendall", json.loads(data)["error"]["message"]))

        def shutdown(self, how):
            events.append(("shutdown", how))

        def recv(self, size):
            events.append("recv")
            return self.chunks.pop(0)

        def close(self):
            events.append("close")
            done.set()

    server_module.BridgeServer._refuse(FakeConnection(), "too many clients (max 1)")
    assert done.wait(2.0)
    calls = [e for e in events if e != "settimeout"]
    assert calls[0] == ("sendall", "too many clients (max 1)")
    assert calls[1] == ("shutdown", socket.SHUT_WR)
    assert calls[2:] == ["recv", "recv", "close"]          # drained until EOF, then closed
