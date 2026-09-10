# Setup

Three ways to run the bridge: the Windows installer (no Python, no Docker), local Python on any machine that has TotalMix, or Docker for an always-on server. MQTT is optional in every case.

---

## Windows installer

The signed `tmosc-setup-<version>.exe` from [Releases](https://github.com/auntiepickle/totalmix-osc-bridge/releases) installs the tray MIDI agent (**Client**), the bridge as a standalone exe (**Server**), or **Both**. Per-user by default (no admin needed).

**Server wizard pages** - the answers become `%APPDATA%\tmosc-bridge\config.env` (plain `KEY=VALUE`; edit any time and restart the bridge to apply; an upgrade reads the values back into the wizard):

| Field | `config.env` key | Default |
|---|---|---|
| IP of the PC running TotalMix FX | `OSC_IP` | `127.0.0.1` |
| TotalMix "Port incoming" / "Port outgoing" | `OSC_PORT` / `OSC_LISTEN_PORT` | `7001` / `9001` |
| Web UI port | `WEB_PORT` | `8088` |
| OSC transport | `OSC_TRANSPORT` (plus `GLOBAL_OSC_*` for global) | `classic` |
| MQTT broker (blank = off) | `MQTT_BROKER`, `MQTT_PORT`, `MQTT_USER`, `MQTT_PASS`, `ENABLE_MQTT` | off |

Where things live:

| What | Where |
|---|---|
| Program | `%LOCALAPPDATA%\Programs\TotalMix OSC\bridge\` (per-user) or `C:\Program Files\TotalMix OSC\bridge\` (elevated) |
| State: `mappings.json`, `ufx2_channel_map.json`, `ufx2_snapshot_map.json`, `backups\`, `bridge.log`, `config.env` | `%APPDATA%\tmosc-bridge\` |
| Templates (`*.example.json`) and the web UI | inside the program folder (`_internal\`), read-only; "Init from example" in the UI copies a template into the state dir |

Notes:

- The bridge runs as a minimized console window (Start Menu "TotalMix OSC Bridge"; the "start when I sign in" task adds it to Startup). Close the window to stop it.
- Windows Firewall: an elevated install adds an inbound rule for the bridge. A per-user install gets Windows' allow/deny prompt the first time the bridge listens - allow it if other machines should reach the web UI or TotalMix runs on another PC; localhost works either way.
- TotalMix itself is configured exactly as for any other install: OSC "Port incoming" `7001`, "Port outgoing" `9001`, remote IP = the bridge PC. See [TotalMix OSC configuration](#totalmix-osc-configuration-the-canonical-client-setup).
- `tmosc-bridge.exe --data-dir` prints the state dir, `--version` the build; `TMOSC_DATA_DIR` overrides the state dir.
- Portable use: the release also ships `tmosc-bridge-<version>-win64.zip`, the bare program folder. Unzip anywhere and run `tmosc-bridge.exe`; drop a `config.env` into `%APPDATA%\tmosc-bridge` or set the variables in the environment.

---

## Local

Best for road use, development, or running on the same machine as TotalMix.

**Prerequisites:** Python 3.12+. TotalMix FX with OSC enabled: TotalMix > Settings > OSC, enable and set a receive port (default `7001`). No broker, no Docker, no HTTPS needed for localhost.

```bash
git clone https://github.com/auntiepickle/totalmix-osc-bridge.git
cd totalmix-osc-bridge

python -m venv .venv
source .venv/bin/activate       # macOS / Linux
# .venv\Scripts\activate        # Windows

pip install -r requirements.txt

cp examples/mappings.example.json mappings.json
cp examples/ufx2_channel_map.example.json ufx2_channel_map.json
cp examples/ufx2_snapshot_map.example.json ufx2_snapshot_map.json

export OSC_IP=127.0.0.1         # TotalMix on same machine; use LAN IP if remote
export OSC_TRANSPORT=global     # the standard transport (TotalMix FX 2.1+, Remote 2)
export ENABLE_MQTT=false        # no broker on a laptop
python -m tmosc                 # or: uvicorn tmosc.api.app:app --host 0.0.0.0 --port 8088 --reload
```

Open `http://localhost:8088`. Select your MIDI input in the header. Macro cards load from `mappings.json`.

`--reload` restarts on Python file changes. Config files hot-reload via the UI without it.

**Finding what a control does:** move it in TotalMix and read `GET /api/device/activity` (the Global feedback change log), or use MIDI learn and the routing picker in the UI. The legacy log-only monitor (`ENABLE_OSC_MONITOR=true`) shares UDP 9001 with the listener, so give it `OSC_MONITOR_PORT=9003` if you ever enable it.

---

## Docker

Best for a home server that runs continuously.

**Prerequisites:** Docker and docker-compose v2. TotalMix FX with OSC enabled on a machine reachable by the server. HTTPS for Web MIDI on a real IP (see [HTTPS](#https) below). MQTT broker is optional.

### Environment variables

Only `OSC_IP` is required. Everything else has a default or is safe to omit.

| Variable | Default | Description |
|---|---|---|
| `OSC_IP` | required | IP of the machine running TotalMix |
| `OSC_PORT` | `7001` | TotalMix OSC receive port |
| `WEB_PORT` | `8088` | Internal HTTP port proxied by Caddy |
| `ENABLE_MQTT` | `true` | `false` runs without a broker (web UI / MIDI / REST only) |
| `MQTT_BROKER` | `mosquitto` | Hostname or IP of your MQTT broker (`ENABLE_MQTT=false` runs without one) |
| `MQTT_PORT` | `1883` | MQTT port |
| `MQTT_USER` | unset | MQTT username |
| `MQTT_PASS` | unset | MQTT password |
| `ENABLE_OSC_MONITOR` | `false` | Set to `true` to log incoming OSC from TotalMix |
| `BRIDGE_LOG_FILE` | `bridge.log` | Path for the rotating log (default lives in the state dir: repo root from source, `%APPDATA%\tmosc-bridge` when installed) |
| `OSC_LISTEN_PORT` | `9001` | UDP port of the classic feedback listener (TotalMix Remote 1 "Port outgoing") |
| `ENABLE_OSC_LISTENER` | `true` | Classic feedback listener (workspace/snapshot confirmation, sweep) |
| `OSC_MONITOR_PORT` | `9001` | Legacy log-only monitor port; change it if you enable the monitor (it cannot share the listener's port) |
| `API_TOKEN` | unset | Shared token required on state-changing requests and `/ws` ([security.md](security.md)) |
| `TMOSC_DATA_DIR` | state dir | Override where config JSON, backups and logs live |
| `OSC_TRANSPORT` | `classic` | `global` = write via Global OSC (TotalMix 2.1+, **the standard**) |
| `GLOBAL_OSC_IP` | `OSC_IP` | TotalMix host for the Global remote |
| `GLOBAL_OSC_PORT` | `7002` | Global remote's incoming port |
| `GLOBAL_OSC_LISTEN_PORT` | `9002` | Where the bridge receives Global feedback |
| `ENABLE_GLOBAL_OSC_LISTENER` | `false`* | Global feedback listener (*implied `true` when `OSC_TRANSPORT=global`) |

**Global OSC is the standard transport** (sub-millisecond fires, absolute
addressing, structural immunity to strip races). Classic remains the
default only for compatibility with pre-2.1 TotalMix; it is **legacy** —
new features target Global, and classic-mode gaps are documented rather
than fixed (e.g. channel-identify *pulse* requires the Global transport;
*wiggle* needs only the Global listener, which shadow mode provides).
Snapshot and workspace switching intentionally use the classic remote
under BOTH transports (2.1 b5 Global snapshot feedback is unreliable), so
Remote 1 stays configured either way.

Create a `.env` file next to `docker-compose.yml`:

```env
OSC_IP=192.168.1.50
OSC_TRANSPORT=global
# GLOBAL_OSC_PORT=7002          # Remote 2 defaults; uncomment to change
# GLOBAL_OSC_LISTEN_PORT=9002
OSC_PORT=7001
WEB_PORT=8088
# No broker? replace the four MQTT lines with:  ENABLE_MQTT=false
MQTT_BROKER=192.168.1.10
MQTT_PORT=1883
MQTT_USER=studio
MQTT_PASS=yourpassword
```

### Deploy

```bash
cp docker-compose.example.yml docker-compose.yml
cp examples/mappings.example.json mappings.json
cp examples/ufx2_channel_map.example.json ufx2_channel_map.json

docker compose build --no-cache
docker compose up -d
docker compose logs -f
```

Within a few seconds: `OSC Client ready -> 192.168.x.x:7001`. If MQTT is configured: `MQTT connected`.

### Update

```bash
git pull origin main
docker compose restart          # the checkout is bind-mounted: new code, same image
```

Rebuild only when `requirements.txt`, the `Dockerfile` or the web UI's Tailwind
classes changed: `docker compose build && docker compose up -d`.

`mappings.json` and `ufx2_channel_map.json` are git-ignored. A pull never touches them.

### Snapshot map live sync

Place `ufx2_snapshot_map.json` on a NAS and the bridge reloads it without a redeploy.

1. Mount the NAS share on the Docker host, e.g. via CIFS in `/etc/fstab`
2. Add a volume bind in `docker-compose.yml`:
   ```yaml
   volumes:
     - /mnt/nas/studio-config:/app/config
   ```
3. Place `ufx2_snapshot_map.json` in that directory

The bridge polls `/app/config/ufx2_snapshot_map.json` every 5 seconds and reloads on change. If the path does not exist it falls back to the local file without error.

### HTTPS

The Web MIDI API requires a secure context on real IPs. `localhost` is exempt.

The included `deploy/Caddyfile` uses `nip.io` for the hostname (any `IP.nip.io` name resolves to that IP, so no DNS setup) and Caddy's internal CA for the certificate (`tls internal`; a public CA cannot issue for a private address). Trust Caddy's root certificate once per browser.

Edit `deploy/Caddyfile` to match your server IP:

```
192.168.1.x.nip.io:9445 {
    tls internal
    reverse_proxy 127.0.0.1:8088
}
```

Run Caddy on the host or add it to `docker-compose.yml`. Access the UI at `https://192.168.1.x.nip.io:9445/static/index.html`.

Alternatives: [mkcert](https://github.com/FiloSottile/mkcert) for a local CA, or a real domain with Certbot.

---

## MQTT and Home Assistant

MQTT is additive. The bridge runs without it.

With it, you get bidirectional state sync and macro triggers from automations. One practical use: a VoIP call starts, Home Assistant detects it, publishes to `totalmix/macro/call_routing`, and the bridge switches TotalMix to your call preset. Your studio mic routes to the system output automatically. The call ends and HA reverses it.

### Topics published by the bridge

| Topic | Payload | Description |
|---|---|---|
| `totalmix/workspace` | slot number (retained) | Current workspace Quick Select slot |
| `totalmix/snapshot` | snapshot number (retained) | Current snapshot |
| `totalmix/knob/<name>/state` | `0..1` (retained, 4 decimals) | Knob position after any change, including a fader moved in TotalMix ([home-assistant.md](home-assistant.md)) |
| `totalmix/workspaces` | JSON array | `[{"name": "Live_set", "index": 3}, ...]` sorted by slot |
| `totalmix/snapshot_map` | JSON object | Full snapshot map |
| `totalmix/snapshot/status` | `loaded_N` | Confirms snapshot N was recalled |

### Topics the bridge subscribes to

| Topic | Payload | Effect |
|---|---|---|
| `totalmix/knob/<name>` | `0..1` or `0..100` | Set a knob (retained commands are ignored) |
| `totalmix/config/snapshot_map` | JSON object | Replace the snapshot map at runtime |
| `totalmix/workspace` | `"3"` (slot number) | Switch workspace |
| `totalmix/snapshot` | `"4"` (1-8) | Recall snapshot |
| `totalmix/macro/<name>` | `"0.0"` to `"1.0"` | Fire macro with param value |

### Fire a macro from an HA automation

```yaml
action:
  - service: mqtt.publish
    data:
      topic: totalmix/macro/reverb_send_ramp
      payload: "0.8"
```

---

## Testing

The automated suite runs with no hardware, broker, or network — OSC sends go to a fake client, MQTT callbacks are invoked directly.

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest
```

It covers macro execution (fire modes, debounce, param clamping, workspace/snapshot resolution and state-aware switching), ramp/LFO operations including cancellation, MQTT message routing and feedback-loop suppression, and the web API.

GitHub Actions runs the suite plus a Docker image build on every push to `main` and every PR (`.github/workflows/ci.yml`). **Deploy flow: push, wait for the green check, then `git pull` + `docker compose restart` on the server** (rebuild only when the image inputs changed). A red X means the server should not pull.

Hardware-in-the-loop scripts (real MQTT broker, real UFX II) live in `tools/manual/` — see the README there. pytest ignores that directory.

---

## TotalMix OSC configuration (the canonical client setup)

The bridge uses up to TWO OSC remotes in TotalMix, configured in
**Options → Mixer Settings (F3) → OSC tab** (TotalMix FX 2.1; older
versions call it Settings). Each remote is selected with the
"Remote Controller Select" radio buttons.

**Remote 1 — classic protocol (still required: workspace/snapshot switching, the sweep and the liveness probe):**

| Setting | Value |
|---|---|
| In Use | checked |
| Port incoming | `7001` (must match `OSC_PORT`) |
| Port outgoing | `9001` (must match `OSC_LISTEN_PORT`) |
| IP or Host Name | IP of the machine running the bridge |
| Number of Faders per Bank | **high enough to cover every channel** (e.g. 48) |
| Compatibility (Mode) | `TotalMix 1.96` (the classic/default mode) |

**Remote 2 — Global OSC (TotalMix FX 2.1+; the standard transport,
`OSC_TRANSPORT=global`):**

| Setting | Value |
|---|---|
| In Use | checked |
| Port incoming | `7002` (`GLOBAL_OSC_PORT`) |
| Port outgoing | `9002` (`GLOBAL_OSC_LISTEN_PORT`) |
| IP or Host Name | IP of the machine running the bridge |
| Compatibility (Mode) | `Global OSC` |
| Details… → Send changes | checked |
| Details… → Send status cyclic | **checked** (the ~1/sec heartbeat the bridge uses for liveness) |
| Details… → Receive on hidden channels | **checked** (hidden channels are otherwise silently dropped) |
| Details… → Re-send options | **unchecked** (RME warns of ping-pong loops) |
| Details… → Bandwidth Limitation | `500kByte/s` (default) |

Also in the **Options menu**: "Enable OSC Control" checked; "Submix linked
to OSC Controller 1" as required by the classic protocol; do NOT enable
submix-linking for the Global remote.

The fader-bank size matters: TotalMix only reports the strips inside the
current bank over OSC. At the default of 8, the sweep and live resolution can
only see the first 8 strips per row — ADAT and other higher channels never
appear. Raise it, then re-run the sweep (`POST /api/device/sweep`). (The bridge sends `/setBankStart 0`
before every capture/resolution so a scrolled bank cannot shift indices —
the address is 0-based.)

**CRITICAL: every OSC remote setting above is WORKSPACE-scoped and will NOT
survive a workspace load unless you re-save the workspace.** Confirmed the
hard way, three times now:

| Setting | Observed behavior |
|---|---|
| Number of Faders per Bank | reverted 48 → 8 on workspace load |
| Remote Controller Address | wiped on workspace load (kills feedback = macros stop) |
| Remote 2 "In Use" + mode | wiped on workspace load (kills Global OSC entirely) |

The required procedure, **once per quick-workspace slot you ever load**
(including by macros):

1. Load the workspace (File → Workspace Quick Select, or fire a macro).
2. Configure both remotes as above (the load just reverted them).
3. **File → Workspace Quick Select → "Save current workspace as…"** — the
   dialog prefills the current slot number and name; click Save.

After that, loads of that workspace carry the settings and nothing breaks.
A workspace you never load can keep stale settings harmlessly — but the
moment something loads it, both remotes revert until it too is re-saved.

Separately, stereo-link state and channel names change per **snapshot** —
the bridge handles that at fire time by resolving channel names against live
feedback.

**The classic listener boots blind by design** (the Global listener is not:
Remote 2's cyclic status feeds it from boot, so `/api/device/global` is live
immediately). Since retained MQTT no longer drives
the device, nothing provokes a feedback dump at startup —
`/api/device/state` starts empty and `osc_bank_width` / `live_strip_count`
are `null` until the first dump arrives. So the pre-flight check has three
states, not two: **48 = good, 8 = the workspace's bank setting was lost,
0/null = nothing received yet** (not a narrow bank — run the connection
check from the gear menu, or fire any macro, to prime it).

**First run — measure the device (one time per physical interface):**

Click **"Measure channels"** on the setup banner in the web UI, or:

```bash
# ~35s, read-only: reads each hardware channel's name at every fixed
# position, both rows. Never switches submixes or writes parameters.
curl -X POST http://YOUR-SERVER:8088/api/device/sweep \
  -H 'Content-Type: application/json' -d '{}'

# Poll status / inspect the measured table
curl http://YOUR-SERVER:8088/api/device/sweep
curl http://YOUR-SERVER:8088/api/device/physical_table
```

The sweep builds the *physical table* — hardware channel → observed names —
which is the only stored mapping the bridge needs. It never goes stale:
hardware positions are fixed; snapshots only rename/pair strips, and the
bridge learns those aliases automatically as it runs. Re-run the sweep only
if you replace the interface or want to reset accumulated aliases
(`{"reset": true}`).

`GET /api/device/state` shows everything the classic listener has captured;
`GET /api/device/picker` shows the live channel inventory the macro editor
uses.
