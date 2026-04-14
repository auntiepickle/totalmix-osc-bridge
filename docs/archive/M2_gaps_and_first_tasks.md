# M2 Gaps & First Tasks (Post-M1 Audit — April 09, 2026)

**Branch**: `M2_branch`  
**Status**: Starting point for full card polish + live state (per MIDI_SUPPORT_AND_UI_ENHANCEMENT_PLAN.md)

## Documented Gaps (verified against latest main commit)
1. **WebSocket payload is still minimal**  
   - Current broadcasts from `bridge.py` do **not** yet include: OSC command preview string, human-readable routing label (from `ufx2_channel_map.json`), full live macro value/ramp progress/LFO flag, last-trigger timestamp, or expanded MIDI trigger details.  
   - Only basic macro state and workspace/snapshot lists are sent.

2. **Card UI in `web/static/index.html` / `app.js` is still basic post-M1**  
   - CC badges exist but do **not** yet show richer data (routing line, OSC preview tooltip, progress bar for ramps, status badges, expandable JSON panel).  
   - No visual routing hint using channel map (e.g. “AN 7/8 → Orville AES/AN 7/8”).

3. **ufx2_channel_map.json integration**  
   - File exists and is loaded in backend, but **not yet passed** to frontend cards for display.

4. **Live state for ramps/LFO**  
   - Backend macro execution supports it, but frontend cards do not yet receive or render real-time value + progress animation from WebSocket.

5. **Tests & docs drift**  
   - No updated tests covering the new richer WS payloads or enhanced cards.  
   - Enhancement plan and project.md need M2 checklist.

These are the **only** gaps — M1 client-side MIDI (Web MIDI in `app.js`) is solid.

## First Tasks for this branch (in order)
- [ ] Extend WebSocket payload in `bridge.py`
- [ ] Update card templates + JS in `web/static/`
- [ ] Inject channel-map labels + OSC previews
- [ ] Doc & test updates (this file + plan.md)
- [ ] Final polish + merge to main