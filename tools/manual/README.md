# Manual rig scripts

These run against the real setup - a live MQTT broker and the bridge in front
of TotalMix FX. They are not collected by pytest. Run them from the repo root;
both read the broker credentials from the repo-root `.env` but connect to a
hard-coded broker at `127.0.0.1:1883`; `ramp_test.py` additionally hard-codes
the macro topic. Edit the constants at the top of the scripts first.

| Script | What it does |
|---|---|
| `trigger_macro.py <name> [param]` | Publish a macro trigger on `totalmix/macro/<name>` over MQTT |
| `ramp_test.py` | Drive a 2-bar triangle ramp over MQTT via `mosquitto_pub` |

The classic-transport scripts that used to live here (raw `/setSubmix` fader
tests, snapshot and workspace shell scripts, the OSC-monitor log parser) were
removed with the Global OSC transport becoming the standard. The automated
suite (no hardware) lives in `tests/` and runs with plain `pytest`.
