# Documentation

Start with the [root README](../README.md) for what the bridge is and the
three ways to install it.

| Doc | Read it when |
|---|---|
| [setup.md](setup.md) | You are installing: Windows installer, Docker, or source; env vars; HTTPS for Web MIDI; TotalMix's own OSC settings |
| [config.md](config.md) | You are writing or reading `mappings.json`, `ufx2_channel_map.json`, `ufx2_snapshot_map.json` |
| [architecture.md](architecture.md) | You are changing the code: modules, the two OSC transports, threads, frontend patterns |
| [home-assistant.md](home-assistant.md) | You want a phone slider or automations over MQTT |
| [security.md](security.md) | The bridge is reachable by more than you: `API_TOKEN` |
| [known-limitations.md](known-limitations.md) | Something looks wrong and you want to know if it is known |
| [design-modul.md](design-modul.md) | You are touching the MODUL skin: the ten rules of that design |
| [../agent/README.md](../agent/README.md) | The tray agent (client) |
| [../CHANGELOG.md](../CHANGELOG.md) | What changed and why, newest first |

`history/` keeps dated snapshots (the 2026-08-27 critical review, the
simplification notes, the August known-limitations notes) for the record;
they are not maintained.
