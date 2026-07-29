# Manual hardware tests

These scripts exercise the real rig — a live MQTT broker, the running Docker
container, and TotalMix FX on the UFX II. They are NOT collected by pytest;
run them by hand from the repo root when validating on hardware.

| Script | What it does |
|---|---|
| `test_macro.py <name> [param]` | Publish a macro trigger over MQTT (reads `.env` credentials) |
| `ramp_test.py` | Drive a 2-bar triangle ramp over MQTT via `mosquitto_pub` |
| `test_submix_fader.py` | Send raw OSC submix-select + fader moves (imports fire immediately!) |
| `test-snapshot.sh <1-8>` | Recall a snapshot via OSC from inside the container |
| `test-workspace.sh` | Workspace switch via OSC from inside the container |
| `parse_submix_log.py` | Parse OSC monitor logs captured during a submix sweep |

The automated suite (no hardware needed) lives one level up in `tests/` and
runs with plain `pytest`.
