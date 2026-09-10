# LiveBridge wire protocol (v1)

Transport: **TCP**, newline‑delimited JSON (one JSON object per line, UTF‑8, `\n` terminated).
Default endpoint `127.0.0.1:9880`. Several clients may be connected; requests on one connection are
executed in order, and the response carries the request `id`.

## Request

```json
{"id": "a1b2", "cmd": "tracks.list", "args": {"include_returns": true}, "token": "…", "timeout": 10}
```

| field | type | notes |
|---|---|---|
| `id` | string | client‑generated, echoed back. Required. |
| `cmd` | string | `namespace.name`, e.g. `transport.play`, `lom.get`. Required. |
| `args` | object | keyword arguments for the handler. Optional (default `{}`). |
| `token` | string | must equal the script's configured token when one is set (always in LAN mode; a LAN-mode bridge without a token refuses every request — `load_config` then falls back to 127.0.0.1). |
| `timeout` | number | seconds the client is willing to wait (default 10, max 120). |
| `v` | int | protocol version, optional, defaults to 1. |

## Response (success)

```json
{"id": "a1b2", "ok": true, "result": {...}, "ms": 3.2}
```

Error responses carry `ms` too (additive). Requests with unknown top-level fields are rejected
as `bad_request`; `v` must be 1 or absent.

## Response (error)

```json
{"id": "a1b2", "ok": false,
 "error": {"type": "not_found", "message": "song.tracks[7]: index out of range (5 tracks)",
           "traceback": "…optional…", "cmd": "tracks.get"}}
```

Error `type` values: `auth` (bad/missing token), `bad_request` (malformed JSON / missing fields),
`unknown_command`, `bad_args` (wrong/missing/unknown argument), `not_found` (path/index/name),
`invalid_state` (e.g. clip slot already has a clip, track cannot be armed), `unsupported`
(feature not available in this Live version/edition), `timeout` (main thread did not answer in time),
`forbidden` (eval disabled, or eval without a configured token), `internal` (unexpected exception —
traceback included).

An error response with `"id": null` concerns the connection or the request in flight: the
connection-level rejection `{"type": "invalid_state", "message": "too many clients (max N)"}`
(after which the server half-closes, drains and closes the socket) and `bad_request` for a line
that could not be parsed. Clients report it instead of ignoring it; only lines without `"ok"`
(events) are ignored.

## Events (server → client, unsolicited, optional)

Only sent to clients that subscribed via `system.subscribe`. Shape:
`{"event": "song.is_playing", "data": {...}}`. Core does not require events; they are a phase‑2
nicety. Clients must ignore lines without `"ok"` (events) that they did not ask for — an
`"ok": false` line with `"id": null` is an error (see above), not an event.

## Core commands (implemented by the Fundament agents; everything else lives in handler modules)

| cmd | args | result |
|---|---|---|
| `system.hello` | – | `{name:"LiveBridge", version, protocol:1, live:{major,minor,bugfix,version,edition}, python, platform, machine, allow_eval, host, port, needs_token, commands, uptime}` — `allow_eval` is the effective value (`allow_eval` **and** a token) |
| `system.ping` | – | `{"pong": true, "t": <song time>, "is_playing": bool}` |
| `system.commands` | `namespace?, include_doc?` | `{count, namespaces:[…], commands:[{cmd, doc?, params:[{name, default?, required}], mutating}]}` |
| `system.log` | `message?, level?, tail?` | `{ok, lines?}` — writes into Live's Log.txt; `tail` returns the bridge's last N log lines |
| `system.status` | – | `{config (token redacted), server:{running, host, port, clients, authenticated, pending, max_clients}, bind:{bound, attempts, error, retry_in}, beacon:{running, port}, eval, dialog:{open, count, message, buttons}, commands}` — `server` is null while the port is not bound |
| `system.batch` | `commands:[{cmd, args}], stop_on_error?` | `{count, ran, ok, failed, results:[{index, cmd, ok, result\|error}], stopped_at?}` — one main-thread pass, **one undo step**, no rollback; `"$n"` / `"$n.key.0"` / `"$-1"` string arguments are replaced by (part of) an earlier step's result keeping its type, `"${n.key}"` inside longer strings, `"$$"` escapes; at most 100 steps, not nestable |
| `system.dialog` | – | `{open, count, message, buttons}` — Live's modal dialog (button labels are not exposed) |
| `system.dialog_press` | `button, expect?` | `{pressed, message, open_after}` — presses button `button` (0-based) of the open dialog; `expect` must occur in the message or nothing is pressed |
| `system.reload` | `modules?, helpers?, core?` | developer hot reload of handler/helper modules (and, with `core`, the Context class): `{helpers, core, reloaded, added, failed, commands}` |
| `lom.get` | `path, prop?, detail?` | value or object summary |
| `lom.set` | `path, prop, value` | `{path, prop, value(after), display_value?}` |
| `lom.call` | `path, method, args?:[…], kwargs?:{…}, detail?` | return value (summarised if LOM object); string args that are LOM paths are resolved to objects |
| `lom.describe` | `path, include_methods?` | `{type, path, properties:[{name,value,type,writable?}], methods:[…], children:[{name,count,path}]}` |
| `lom.children` | `path, detail?, offset?, limit?` | collection: `{kind:"items", path, total, offset, count, items:[summary…], next_offset?}`; object: `{kind:"collections", path, children:[{name,count,path}]}` |
| `eval.python` | `code?, expr?, detail?, reset?` | `{result, stdout}` — executes with `song`, `app`, `browser`, `view`, `ctx`, `Live` in scope; `expr` evaluated last and returned; names persist between calls until `reset` |

Every module command (`tracks.*`, `clips.*`, ...) is listed with its parameters in
`docs/TOOLS.md` (generated from the registry by `installers/gen_tools_doc.py`).

**Paged results** (commands with `offset`/`limit`) share one shape:
`{"total": <matching items>, "offset": o, "count": <items returned>, <items key>: [...],
"next_offset": o + count}` — `next_offset` only when more items follow. Two variations:
`devices.parameters` / `plugins.parameters` / `plugins.presets` report the device's full
`total` plus `matched` (after `filter`), and `song.snapshot` puts the keys in a nested
`paging` object (only present when the track list is paged).

## Discovery beacon (UDP)

Every 2 s while enabled, the script broadcasts to `255.255.255.255:9881`:
`{"livebridge": 1, "name": "<machine name>", "host": "<ip>", "port": 9880, "live": "12.4.5", "needs_token": true}`.
Clients listen on `0.0.0.0:9881` for a few seconds to enumerate instances. The beacon is also
sent to `127.0.0.1:9881`, so discovery works on the same machine when broadcasts are filtered.

## Framing rules

- Max line length 16 MiB; larger results must be paged by the handler (`offset`/`limit` args).
- The server never blocks the socket thread on the LOM; it enqueues and waits on an Event.
- Client must handle a closed socket by reconnecting on the next request (with one retry).
- Keep‑alive: clients may send `system.ping` every 30 s; the server closes idle sockets after 10 min.
- Not HTTP: a line that is an HTTP request line (`POST / HTTP/1.1`, `GET … HTTP/1.1` …) gets a
  plain `HTTP/1.1 403` answer and the connection is closed before anything after it runs. This
  stops a web page from making the browser POST a JSON command line to `127.0.0.1:9880`.
- Authentication: an `auth` error is answered and the connection is then closed. Connections
  that have not sent a valid token are closed 10 s after connecting and do not count against
  `max_clients` (at most 4 pending; a new one evicts the oldest pending connection).
- 8 malformed lines in a row close the connection (not a LiveBridge client).
- Responses are written with a 30 s deadline; a client that cannot absorb a response in that
  time is disconnected.
