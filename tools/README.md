# tools/

Helpers that are not part of the server and not collected by pytest.

| Tool | Runs where | Purpose |
|---|---|---|
| `smoke_frozen.ps1` | dev box / CI (Windows) | Boots `dist/tmosc-bridge/tmosc-bridge.exe` from a foreign directory with state redirected and checks health, template fallback, the bundled UI, a config write, and a WebSocket handshake. `pwsh tools/smoke_frozen.ps1 -Port 8098` |
| `validate_capture.sh` | the server hosting the container | End-to-end check of OSC device capture and discovery; `--apply` promotes the result to the live channel map |
| `global_osc_probe.py` | the TotalMix host | Global OSC hardware harness: read triggers and write-verify probes that restore what they change |
| `global_hw_gates.py` | the TotalMix host | Hardware gates HW-3/5/6/8 (+ snapshot wash) used to calibrate the Global unit transforms |
| `scrape_totalmix_snapshots.py` / `.ps1` | the TotalMix host | Scrape TotalMix's workspace/snapshot files into `ufx2_snapshot_map.json` (HASS.Agent friendly) |
| `manual/` | the rig | Hand-run scripts against a live broker and device; see `manual/README.md` |

The Python tools add the repo root to `sys.path` themselves and import from
the `tmosc` package, so run them from any directory of a checkout.
