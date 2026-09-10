# TotalMix OSC — native agent

A background service that reads MIDI and drives the bridge **without a browser
open**. It replaces the browser's role as the live MIDI handler, so a physical
controller keeps working headless — on a desktop tray, or a Raspberry Pi /
PoE box tucked under the desk.

## Why native C, portable at the base

The interesting, reusable part — parsing MIDI, matching it to your macros,
building the bridge messages — is written as **freestanding C99 with zero
dependencies** (`agent/core/`). No OS headers, no libraries, no malloc in the
hot path. It compiles the same on Windows, macOS, Linux/ARM (Raspberry Pi),
and is a candidate to reuse on a microcontroller later (the same USB-MIDI-host
+ direct-control idea).

Platform-specific I/O (the actual MIDI device, the network socket, the tray
icon) is a thin adapter layered on top of that core — added per platform
without touching the base.

```
agent/
  core/               # THE BASE — freestanding, portable, dependency-free
    tmosc_midi.[ch]     raw MIDI byte-stream parser (running status, sysex)
    tmosc_match.[ch]    trigger matcher — faithful port of web/static/midi.js
    tmosc_clock.[ch]    MIDI clock -> BPM
    tmosc_proto.[ch]    build bridge messages (/api/knob, /api/trigger)
    tmosc_bindings.[ch] parse the /api/midi/bindings TSV
  net/net.[ch]        cross-platform HTTP client + UDP LAN discovery
  runner.[ch]         the shared loop (connect, match, coalesce, POST, retry)
  linux/              ALSA MIDI backend + main + systemd unit
  windows/            WinMM backend + console main + tray (icon, menu, startup)
                      + Inno Setup installer.iss + make_icon.py
  tests/test_core.c   dependency-free unit tests (cross-checked vs midi.js)
  CMakeLists.txt
```

## How it talks to the bridge

The agent drives the same macros the browser does, over plain HTTP on the LAN:

| MIDI               | Action                                                        |
|--------------------|--------------------------------------------------------------|
| knob CC / bend / … | `POST /api/knob/<name>`  `{"value":0..1}` (coalesced ~12ms)   |
| fire trigger       | `POST /api/trigger/<name>`  `{"param":..[,"clock_bpm":..]}`   |

Plus, for coexistence with an open browser: a presence heartbeat
(`POST /api/midi/owner/heartbeat`) so the browser yields Web MIDI to the tray,
and a throttled raw-MIDI relay (`POST /api/midi/activity`) so browser MIDI-learn
and the live activity view keep working while the agent owns the port. The one
read-only feed the bridge adds is `GET /api/midi/bindings` (the trigger table).

## Build & test the core

```sh
cmake -S agent -B agent/build
cmake --build agent/build
ctest --test-dir agent/build --output-on-failure
```

The core needs only a C compiler — on the Pi, `sudo apt install cmake gcc` and
the commands above.

## Run the Linux daemon (Ubuntu server, Pi, or any Linux the controller plugs into)

```sh
sudo apt install cmake gcc libasound2-dev
cmake -S agent -B agent/build && cmake --build agent/build
agent/build/tmosc-agent --list        # list MIDI inputs (port + friendly name)
# pick by NAME substring (no need to memorize hw:X,Y), or omit for the first input:
TMOSC_BRIDGE_HOST=192.168.1.41 TMOSC_BRIDGE_PORT=8088 TMOSC_MIDI="U6MIDI" \
  agent/build/tmosc-agent
```

`TMOSC_MIDI` accepts an ALSA id (`hw:1,0`), a case-insensitive **name
substring** (`U6MIDI`, `UFX`), or is omitted to use the first input.

Verify without touching the device:

```sh
agent/build/tmosc-agent --dry-run 192.168.1.41 8088   # print the loaded trigger table, exit
TMOSC_VERBOSE=1 ... tmosc-agent                        # log each MIDI -> action + HTTP status
```

No controller handy? Create a virtual MIDI port to test end to end:

```sh
sudo modprobe snd-virmidi                 # creates virtual MIDI ports
amidi -l                                  # note the Virtual RawMIDI hw:N,0
# point the agent at it, then in another shell send a CC 82 = 100 on ch1:
amidi -p hw:N,0 -S 'B0 52 64'
```

Install as a service: copy `tmosc-agent` to `/usr/local/bin/`, edit the env in
`linux/tmosc-agent.service`, then
`sudo cp agent/linux/tmosc-agent.service /etc/systemd/system/ && sudo systemctl daemon-reload`
and `sudo systemctl enable --now tmosc-agent`.

Requires the bridge to expose `GET /api/midi/bindings` (added alongside this
agent). The daemon fetches that TSV, matches incoming MIDI, and POSTs
`/api/knob/<name>` (knobs) / `/api/trigger/<name>` (fires) — the same effect as
the browser, with no browser.

## Install (Windows)

Grab the signed installer from [Releases](https://github.com/auntiepickle/totalmix-osc-bridge/releases):
`tmosc-setup-*.exe` (choose **Client**, or **Both** if this PC also hosts the
bridge). A client-only `tmosc-agent-setup-*.exe` is built on `agent-v*` tags
(none published so far). Both are Authenticode-signed (no "unknown publisher"
warning). Run it and follow the wizard: it **auto-detects the bridge on your
LAN**, lists your MIDI inputs to pick from, and offers "start with Windows".
Done. The exe is fully standalone (static CRT — no Visual C++ redistributable).

Right-click the tray icon for **Open Web UI**, **Open Web UI - HTTPS**,
**Start with Windows**, and **Quit**. The icon shows the state at a glance —
indigo knob = running, orange = the MIDI device is held by another app (it
retries and grabs it once free).

## Status

- [x] Portable core: MIDI parse, trigger match, clock->BPM, message builders,
      bindings-TSV parser — freestanding, unit-tested vs `web/static/midi.js`
- [x] Linux daemon (ALSA) + Windows console + Windows system-tray app, all from
      one shared runner; cross-platform CI (gcc+ALSA and MSVC+WinMM)
- [x] Bridge: read-only `GET /api/midi/bindings` (TSV) feed
- [x] Coexistence: the bridge tracks a single MIDI owner; the browser yields Web
      MIDI to the tray and stays a full monitor; MIDI-learn + live activity keep
      working via a relay while the tray owns the port; the tray announces
      ownership even while blocked so a browser releases the port to it
- [x] Real tray icon + status states, start-on-login, LAN auto-discovery
- [x] Signed (Azure Trusted Signing) + one-click Inno Setup installer with a
      configuration wizard; standalone exe (static CRT)
- [x] Frozen bridge exe (PyInstaller, signed) + unified Client/Server/Both installer
- [ ] WebSocket knob fast-path (measured unnecessary — HTTP is ~1.8ms on LAN)

## Build from source

```
cmake -S agent -B agent\build           && cmake --build agent\build --config Release
agent\build\Release\tmosc-agent.exe --list          REM list MIDI inputs
agent\build\Release\tmosc-agent.exe --discover       REM find the bridge on the LAN
agent\build\Release\tmosc-agent-tray.exe            REM tray app (reads config below)
```

`%APPDATA%\tmosc-agent\config.txt` (the installer writes this for you):

```
# host: the bridge, or `auto` to discover it on the LAN
host=192.168.1.41
port=8088
# midi: a substring of your controller's name; blank = first input
midi=U6MIDI
# optional — the secure client the tray's "Open Web UI - HTTPS" menu opens.
# Defaults to the Caddy/nip.io URL for an IPv4 host (https://<ip>.nip.io);
# set explicitly if your HTTPS front differs (e.g. a custom port/path).
#https_url=https://192.168.1.41.nip.io:9445/static/index.html
```

The tray menu offers **Open Web UI** (plain HTTP) and **Open Web UI - HTTPS**.
Use HTTPS when you want the browser itself to do Web MIDI (it needs a secure
context on a real IP — see [docs/setup.md](../docs/setup.md#https)); with the
tray handling MIDI, plain HTTP is fine for monitoring.
