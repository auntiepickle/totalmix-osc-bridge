# MIDI → OSC Mapping System (data-driven)

All control is now defined in `mappings.json`. No code changes needed.

**Structure**
{
  "macros": {
    "an12_aes_send": {
      "workspace": "Music",
      "snapshot": "Orville AES",
      "steps": [
        { "osc": "/setSubmix", "value": 3 },
        { "osc": "/2/volume7", "value": "{{param}}", "operation": { "type": "ramp", "bars": 2, "bpm": 140 } }
      ]
    }
  }
}

**Supported Operations** (operations.py)
- "ramp" – triangle/linear over bars
- "lfo" – sine synced to BPM

**Cirklon usage**: just send MQTT `totalmix/macro/an12_aes_send` with float 0.0–1.0.