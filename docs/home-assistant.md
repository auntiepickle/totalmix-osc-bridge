# Home Assistant: a TotalMix knob on your phone

The bridge exposes every KNOB macro over MQTT:

- **Command**: publish a value to `totalmix/knob/<name>` - `0..1` float,
  or `0..100` (auto-detected as percent). Retained commands are ignored
  (a retained value replayed at reconnect would yank your volume at boot).
- **State**: the bridge publishes `totalmix/knob/<name>/state` (retained,
  `0..1`, 4 decimals) on every change from ANY source - phone, MIDI
  controller, web UI, or a snapshot re-assert - so the slider tracks
  reality.

## The speaker-volume slider

Make a knob in the web UI aimed at your speaker output (row 3 volume,
e.g. channel `Main` - cap the range below unity so a fat-fingered
slider can't hurt you), then in Home Assistant's `configuration.yaml`:

```yaml
mqtt:
  number:
    - name: "Speaker Volume"
      command_topic: "totalmix/knob/speaker_vol"
      state_topic: "totalmix/knob/speaker_vol/state"
      min: 0
      max: 100
      step: 1
      mode: slider
      icon: mdi:volume-high
      command_template: "{{ (value | float) / 100 }}"
      value_template: "{{ ((value | float) * 100) | round(0) }}"
```

Reload MQTT entities (or restart HA) and the slider appears - add it to
a dashboard and your phone is now the remote for an RME fader, through
the knob's own range guard and the real RME fader law.

Knob values are knob-normalized (`0..1` across the knob's configured
range), so `100` on the slider means the TOP OF THE KNOB'S RANGE - if
the knob is capped at -20 dB, the slider's max is -20 dB. That is the
point.
