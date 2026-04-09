# MIDI Support & Web UI Card Enhancement Plan

**Project**: RME UFX II TotalMix OSC Bridge  
**Date**: April 8, 2026  
**Current Commit**: `e367402fe6ad129b65a1fa38b4894cde37e2bb3f`  
**Status**: Planning Phase → Ready to Implement  
**Goal**: Make the Cirklon control the bridge natively via MIDI while turning the web dashboard cards into actually useful live-performance surfaces.

## Objectives
1. Fully activate the existing `midi_triggers` arrays already defined in `mappings.json`.
2. Dramatically improve the macro cards so they show MIDI assignments, channel names, OSC previews, live values, and status at a glance.
3. Keep everything non-breaking and data-driven.

### M1 (Client-Side Web MIDI MVP) — ✅ COMPLETE Apr 9 2026

- [x] Web MIDI listener + automatic/manual Cirklon detection in `web/static/app.js`
- [x] CC/channel matching against `mappings.json` + `fireMacro()`
- [x] Per-card dynamic CC badges (device name + CC + channel + value)
- [x] Secure HTTPS + wss:// WebSocket context via Caddy + nip.io
- [x] Working manual MIDI device selector + reconnect in top bar
- [x] Tested live with simulator + real triggers

**M1 Success**: Full client-side MIDI control with zero server changes. Cards, badges, and secure Web MIDI all working. ✅

## M2: Bulk Up the Macro Cards (Parallel – same sprint)
**Goal**: Turn the current bare-bones cards into rich, informative control surfaces.

**Tasks**
- [ ] Expose richer data via WebSocket (MIDI triggers, OSC preview, channel map labels, live value/ramp/LFO state)
- [ ] Update `web/static/` templates:
  - Show MIDI trigger (e.g. “CC 42 ch 1”)
  - Human-readable routing (e.g. “AN 1/2 → AES 7/8 Orville” pulled from `ufx2_channel_map.json`)
  - OSC command preview
  - Live value + progress bar for ramps/LFO
  - Expandable detail panel (full macro JSON + timestamps)
  - Status badges (“Ready”, “Ramping”, “LFO Active”)
- [ ] Improve workspace & snapshot cards (click-to-load + full list from `ufx2_snapshot_map.json`)
- [ ] Tiny visual routing hint using channel map data

**Success**: Open the web UI and instantly see exactly what every macro does, how to trigger it from Cirklon, and what it’s currently doing.

## M3: Documentation & Polish
- [ ] Update `RME_UFX_II_TotalMix_OSC_Bridge_Project.md` with links to this plan
- [ ] Add “Cirklon MIDI Quick-Start” section
- [ ] Version bump + release notes
- [ ] (Optional) Tiny visual FX routing diagram on the dashboard

## Timeline & Milestones
**M1**  
- MIDI handler MVP + basic card MIDI display  
- You test with physical Cirklon  

**M2**  
- Full card polish + live state  
- All docs updated  
- Tests passing  

**Deliverables**  
- Working MIDI → macro path  
- Significantly more useful web dashboard  
- This living document (with checkboxes we’ll tick off together)

## Open Questions (answer whenever convenient)
1. Preferred MIDI port name / auto-detection behavior on your machine?
2. Note On/Off triggers in v1 or save for later?
3. Any specific card feature you want first (e.g. always-visible channel map)?

## Success Criteria
- Midi device (cirklon) CC triggers any macro correctly with proper scaling
- Web cards clearly communicate MIDI assignment + current state
- Zero breakage to existing MQTT/OSC paths

*Last updated: April 8, 2026*  
*Reference commit: e367402fe6ad129b65a1fa38b4894cde37e2bb3f*