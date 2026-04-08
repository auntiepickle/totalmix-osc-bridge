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

## Phase 1: MIDI Support (High Priority – 2–3 hours to MVP)
**Goal**: Cirklon (or any MIDI controller) plugs straight in → fires any macro instantly.

**Tasks**
- [ ] Create `midi_handler.py` (clean, separate module using `mido`)
  - Configurable input port via `config.py`/`.env` (with auto-list on startup)
  - Background asyncio task listening for CC messages
  - Match incoming CC/channel against every macro’s `midi_triggers` list
  - Scale MIDI 0–127 → macro’s `param_range` (or 0.0–1.0 fallback)
  - Call `self.run_macro(macro_name, scaled_value)`
- [ ] Integrate into `bridge.py` (start handler in `__init__`, add status logging + WebSocket broadcast)
- [ ] Update `MIDI_to_OSC_Mapping_System.md` with handler details + Cirklon setup example
- [ ] Add `tests/test_midi.py` (unit + synthetic message tests)
- [ ] Optional: MIDI output port for future feedback (Cirklon LEDs)

**Success**: Send one CC from Cirklon → submix switches + Orville AES/AN fader ramps exactly as defined.

## Phase 2: Bulk Up the Macro Cards (Parallel – same sprint)
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

## Phase 3: Documentation & Polish
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