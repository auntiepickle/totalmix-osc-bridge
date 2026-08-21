# Setup

Two ways to run the bridge: local Python on any machine that has TotalMix, or Docker for an always-on server. MQTT is optional in both cases.

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

cp mappings.example.json mappings.json
cp ufx2_channel_map.example.json ufx2_channel_map.json
cp ufx2_snapshot_map.example.json ufx2_snapshot_map.json

export OSC_IP=127.0.0.1         # TotalMix on same machine; use LAN IP if remote
export OSC_PORT=7001
uvicorn web.web_client:app --host 0.0.0.0 --port 8080 --reload
```

Open `http://localhost:8080`. Select your MIDI input in the header. Macro cards load from `mappings.json`.

`--reload` restarts on Python file changes. Config files hot-reload via the UI without it.

**Finding OSC addresses:** move a fader in TotalMix while the monitor runs. The address appears in `osc_monitor.log`.

```bash
ENABLE_OSC_MONITOR=true uvicorn web.web_client:app --host 0.0.0.0 --port 8080
```

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
| `MQTT_BROKER` | unset | Hostname or IP of your MQTT broker. Omit to disable MQTT. |
| `MQTT_PORT` | `1883` | MQTT port |
| `MQTT_USER` | unset | MQTT username |
| `MQTT_PASS` | unset | MQTT password |
| `ENABLE_OSC_MONITOR` | `false` | Set to `true` to log incoming OSC from TotalMix |
| `BRIDGE_LOG_FILE` | `bridge.log` | Path for the rotating log |
| `OSC_MONITOR_PORT` | `9001` | UDP port for the OSC listener |

Create a `.env` file next to `docker-compose.yml`:

```env
OSC_IP=192.168.1.50
OSC_PORT=7001
WEB_PORT=8088
# Remove the lines below if you have no MQTT broker
MQTT_BROKER=192.168.1.10
MQTT_PORT=1883
MQTT_USER=studio
MQTT_PASS=yourpassword
```

### Deploy

```bash
cp docker-compose.example.yml docker-compose.yml
cp mappings.example.json mappings.json
cp ufx2_channel_map.example.json ufx2_channel_map.json

docker compose build --no-cache
docker compose up -d
docker compose logs -f
```

Within a few seconds: `OSC Client ready -> 192.168.x.x:7001`. If MQTT is configured: `MQTT connected`.

### Update

```bash
git pull origin main
docker compose build --no-cache
docker compose up -d
```

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

The included `Caddyfile` uses `nip.io` for automatic DNS and Let's Encrypt TLS. `nip.io` maps any `IP.nip.io` hostname to that IP, giving you a valid HTTPS cert for a LAN address without DNS setup.

Edit `Caddyfile` to match your server IP:

```
192.168.1.x.nip.io {
    reverse_proxy localhost:8088
}
```

Run Caddy on the host or add it to `docker-compose.yml`. Access the UI at `https://192.168.1.x.nip.io`.

Alternatives: [mkcert](https://github.com/FiloSottile/mkcert) for a local CA, or a real domain with Certbot.

---

## MQTT and Home Assistant

MQTT is additive. The bridge runs without it.

With it, you get bidirectional state sync and macro triggers from automations. One practical use: a VoIP call starts, Home Assistant detects it, publishes to `totalmix/macro/call_routing`, and the bridge switches TotalMix to your call preset. Your studio mic routes to the system output automatically. The call ends and HA reverses it.

### Topics published by the bridge

| Topic | Payload | Description |
|---|---|---|
| `totalmix/workspaces` | JSON array | `[{"name": "Live_set", "index": 3}, ...]` sorted by slot |
| `totalmix/snapshot_map` | JSON object | Full snapshot map |
| `totalmix/snapshot/status` | `loaded_N` | Confirms snapshot N was recalled |

### Topics the bridge subscribes to

| Topic | Payload | Effect |
|---|---|---|
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

GitHub Actions runs the suite plus a Docker image build on every push to `main` and every PR (`.github/workflows/ci.yml`). **Deploy flow: push, wait for the green check, then `git pull` + `docker compose build` on the server.** A red X means the server should not pull.

Hardware-in-the-loop scripts (real MQTT broker, real UFX II) live in `tests/manual/` — see the README there. pytest ignores that directory.

---

## TotalMix OSC configuration (the canonical client setup)

The bridge uses up to TWO OSC remotes in TotalMix, configured in
**Options → Mixer Settings (F3) → OSC tab** (TotalMix FX 2.1; older
versions call it Settings). Each remote is selected with the
"Remote Controller Select" radio buttons.

**Remote 1 — classic protocol (required; drives macros today):**

| Setting | Value |
|---|---|
| In Use | checked |
| Port incoming | `7001` (must match `OSC_PORT`) |
| Port outgoing | `9001` (must match `OSC_LISTEN_PORT`) |
| IP or Host Name | IP of the machine running the bridge |
| Number of Faders per Bank | **high enough to cover every channel** (e.g. 48) |
| Compatibility (Mode) | `TotalMix 1.96` (the classic/default mode) |

**Remote 2 — Global OSC (TotalMix FX 2.1+; the next-generation transport,
issue #25):**

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
current bank over OSC. At the default of 8, discovery and live resolution can
only see the first 8 strips per row — ADAT and other higher channels never
appear. Raise it, then re-run discovery. (The bridge sends `/setBankStart 0`
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

**The bridge boots blind by design.** Since retained MQTT no longer drives
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
