# RME UFX II TotalMix OSC Bridge

**A reliable, data-driven OSC bridge for RME TotalMix FX sends + dynamic submix control.**

Designed for hardware sequencers (Cirklon, etc.) and Home Assistant.  
**Single central server** that any remote MIDI client can trigger via simple MQTT.

## Architecture (Centralized Server Model)

- **Bridge Server** (`bridge.py`): Owns *everything* — macro definitions, workspace/snapshot state, OSC communication to the UFX II. Runs in Docker or venv on a always-on machine.
- **Clients**: Lightweight scripts on MIDI-connected devices. They only read MIDI and publish one MQTT message (`totalmix/macro/<name>`).
- **Why this design?** Central logic = easy maintenance, no duplicated code, works with any number of controllers. This pattern is reusable by any TotalMix user who wants custom FX routing without hacking TotalMix’s limited MIDI learn.

Perfect for:
- Cirklon / hardware sequencers
- Home Assistant dashboards
- Future TouchOSC / Lemur / iPad control surfaces
- Multi-room or distributed studio setups

## Quick Start (after setup)

1. Edit `mappings.json` to add new macros (no code changes).
2. Run the bridge.
3. On any MIDI client device, send MQTT: `totalmix/macro/an12_aes_send` with payload `0.0–1.0`.

See `RME_UFX_II_TotalMix_OSC_Bridge_Project.md` and `MIDI_to_OSC_Mapping_System.md` for full details.

Last updated: April 2026