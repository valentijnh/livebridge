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
| `token` | string | must equal the script's configured token when one is set (always in LAN mode). |
| `timeout` | number | seconds the client is willing to wait (default 10, max 120). |
| `v` | int | protocol version, optional, defaults to 1. |

## Response (success)

```json
{"id": "a1b2", "ok": true, "result": {...}, "ms": 3.2}
```

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
`forbidden` (eval disabled), `internal` (unexpected exception — traceback included).

## Events (server → client, unsolicited, optional)

Only sent to clients that subscribed via `system.subscribe`. Shape:
`{"event": "song.is_playing", "data": {...}}`. Core does not require events; they are a phase‑2
nicety. Clients must ignore lines without `id` that they did not ask for.

## Core commands (implemented by the Fundament agents; everything else lives in handler modules)

| cmd | args | result |
|---|---|---|
| `system.hello` | – | `{name:"LiveBridge", version, protocol:1, live:{major,minor,bugfix,edition?}, python, platform, name (machine), allow_eval}` |
| `system.ping` | – | `{"pong": true, "t": <song time>}` |
| `system.commands` | `namespace?` | `[{cmd, doc, params:[{name, default?}], mutating}]` |
| `system.log` | `message` | `{ok}` — writes into Live's Log.txt |
| `lom.get` | `path, prop?` | value or object summary |
| `lom.set` | `path, prop, value` | `{path, prop, value(after)}` |
| `lom.call` | `path, method, args?:[…]` | return value (summarised if LOM object) |
| `lom.describe` | `path` | `{type, path, properties:[{name,value,type,writable?}], methods:[…], children:[{name,count}]}` |
| `lom.children` | `path, detail?` | list of summaries |
| `eval.python` | `code, expr?` | `{result, stdout}` — executes with `song`, `app`, `browser`, `ctx`, `Live` in scope; `expr` evaluated last and returned |

## Discovery beacon (UDP)

Every 2 s while enabled, the script broadcasts to `255.255.255.255:9881`:
`{"livebridge": 1, "name": "<machine name>", "host": "<ip>", "port": 9880, "live": "12.4.5", "needs_token": true}`.
Clients listen on `0.0.0.0:9881` for a few seconds to enumerate instances.

## Framing rules

- Max line length 16 MiB; larger results must be paged by the handler (`offset`/`limit` args).
- The server never blocks the socket thread on the LOM; it enqueues and waits on an Event.
- Client must handle a closed socket by reconnecting on the next request (with one retry).
- Keep‑alive: clients may send `system.ping` every 30 s; the server closes idle sockets after 10 min.
