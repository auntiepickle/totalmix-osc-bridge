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
    tmosc_proto.[ch]    build bridge messages (knob JSON, /api/trigger)
  tests/
    test_core.c         dependency-free unit tests (cross-checked vs midi.js)
  CMakeLists.txt
  (io/ + platform/ backends land next: PortMidi/WinMM/CoreMIDI/ALSA,
   sockets + minimal WebSocket, and the tray)
```

## How it talks to the bridge (no server changes)

The agent uses the exact two entry points the browser already uses:

| MIDI               | Action                                                        |
|--------------------|--------------------------------------------------------------|
| knob CC / bend / … | WebSocket text frame `{"type":"knob","name":..,"value":..}`  |
| fire trigger       | `POST /api/trigger/<name>`  `{"param":..[,"clock_bpm":..]}`   |

So the bridge and the web UI stay exactly as they are; the agent is a new,
isolated client.

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
amidi -p hw:N,0 -S 'B2 52 64'
```

Install as a service: copy `tmosc-agent` to `/usr/local/bin/`, edit the env in
`linux/tmosc-agent.service`, then `systemctl enable --now tmosc-agent`.

Requires the bridge to expose `GET /api/midi/bindings` (added alongside this
agent). The daemon fetches that TSV, matches incoming MIDI, and POSTs
`/api/knob/<name>` (knobs) / `/api/trigger/<name>` (fires) — the same effect as
the browser, with no browser.

## Status

- [x] Portable core: MIDI parse, trigger match, clock->BPM, message builders,
      bindings-TSV parser — freestanding, unit-tested vs `web/static/midi.js`
- [x] Linux daemon: ALSA MIDI in + minimal HTTP client + coalesced knob flush
      + reconnect + periodic bindings refresh; systemd unit
- [x] Bridge: read-only `GET /api/midi/bindings` (TSV) feed
- [x] Cross-platform: shared runner + net (POSIX/Winsock); portable core and
      the whole agent build on Linux (gcc+ALSA) AND Windows (MSVC+WinMM) in CI
- [x] Windows: console daemon (`tmosc-agent`) + system-tray app
      (`tmosc-agent-tray`) — sits in the notification area, runs headless, menu
      opens the web UI; config from `%APPDATA%\tmosc-agent\config.txt` or TMOSC_* env
- [ ] WebSocket knob fast-path (HTTP is fine on LAN; WS trims overhead later)
- [ ] Learn relay + browser MIDI-yield (web UI Learn while the agent owns MIDI)
- [ ] A real tray icon (currently the generic app icon) + start-on-login

### Windows

```
cmake -S agent -B agent\build           && cmake --build agent\build --config Release
agent\build\Release\tmosc-agent.exe --list          REM list MIDI inputs
agent\build\Release\tmosc-agent-tray.exe            REM tray app (reads config below)
```

`%APPDATA%\tmosc-agent\config.txt`:

```
host=192.168.1.41
port=8088
midi=U6MIDI
```
