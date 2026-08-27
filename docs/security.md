# Security: locking down the control API

The bridge's HTTP + WebSocket API controls the mixer — including output/
monitor levels. By default it is **unauthenticated** and bound to all
interfaces, so anything that can reach the port can drive the mixer. On a
trusted home LAN that has been the working assumption; the moment it is
reachable more widely (the HTTPS `nip.io` proxy, a VPN, a forwarded port),
close it down.

## Option A — keep it localhost-only (simplest, if you don't need remote)

Bind the container to loopback and reach it via SSH tunnel / the machine
itself. This gives up the phone/HA remote-control story, so most people want
Option B instead.

## Option B — turn on the shared-token auth (opt-in, ships built-in)

Set an `API_TOKEN` on the server. When it is set:

- every **state-changing** request (`POST`/`PUT`/`PATCH`/`DELETE`) and the
  `/ws` socket must present the token — as an `X-Api-Token` header or a
  `?token=` query param;
- **GET**s (the UI, reads) stay open — the ear-safety risk is the writes.

When `API_TOKEN` is **unset** (the default), the gate is a pure pass-through
and nothing changes.

### Server

```yaml
# docker-compose.yml (the bridge service)
environment:
  - API_TOKEN=choose-a-long-random-string
```

### Browser

Store the token once, per browser (it rides `localStorage`, attached to every
request and the WS automatically):

```js
localStorage.setItem('tmToken', 'choose-a-long-random-string')
```

Reload the page. That's it — the UI now authenticates.

### Home Assistant / scripts / `mosquitto_pub`

MQTT is a **separate** path (the broker's own auth governs it — see the HA
setup). The token above guards the HTTP/WS API only. Any script hitting the
REST API adds the header:

```bash
curl -H "X-Api-Token: choose-a-long-random-string" \
     -X POST http://192.168.1.41:8088/api/knob/master \
     -H 'Content-Type: application/json' -d '{"value":0.3}'
```

## What this does and doesn't cover

- **Covers:** anonymous writes — nobody on the network can move a fader,
  rewrite a macro, upload config, or drive the knob stream without the token.
- **Does not cover:** transport encryption (put it behind the existing HTTPS
  proxy for that), or the MQTT path (broker auth). And a token in a browser's
  `localStorage` is only as private as that machine.

Pick a long random token, keep it out of screenshots, and rotate it if it
leaks (change the env + the `localStorage` value).
