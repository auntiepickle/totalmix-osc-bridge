# Grok Home Music Studio OSC Bridge Project

This is a living **Grok Project** for the home music studio.

All files, documentation, and decisions are stored here so Grok can instantly understand the full context in any future conversation.

## Project Goal
Build a reliable, single-slot OSC bridge that lets the Cirklon (and future controllers) dynamically select submixes and control fader sends to external FX units (Orville, Space Echo, etc.).

## Current Architecture
- Single slot (Remote Controller 1 on port 7001)
- Dynamic submix selection via `/setSubmix <float index>`
- Fader control via `/<row>/volume<channel>`
- Static per-unit channel map (`ufx2_channel_map.json`)

## Key Files
- `ufx2_channel_map.json` – Static definition of all submixes and channels on the Fireface UFX II
- `mappings.json` – Main fader mapping file (MIDI → submix + row + channel)
- `RME_UFX_II_TotalMix_OSC_Bridge_Project.md` – Full project documentation
- `MIDI_to_OSC_Mapping_System.md` – Mapping philosophy and examples

## How to Use with Grok
In any future conversation, simply say:

> "Reference my Grok Home Studio Project"

Grok will have full context instantly.

Last updated: April 2026
