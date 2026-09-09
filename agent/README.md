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

## Status

- [x] Portable core: MIDI parse, trigger match, clock->BPM, message builders
- [x] Unit tests cross-checked against `web/static/midi.js`
- [ ] I/O backends: MIDI in (PortMidi / native), sockets + WebSocket client
- [ ] Runner: connect, pull `/api/macros`, refresh on `macro_updated`, forward
- [ ] Tray (Windows first) + config + start-on-login
- [ ] Learn relay + browser MIDI-yield (so the web UI's Learn works while the
      agent owns the port)
