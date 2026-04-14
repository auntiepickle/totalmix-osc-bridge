# Deployment

The bridge runs as a Docker container on an always-on home server. This document covers everything from first clone to live MIDI-triggered macros.

---

## Prerequisites

- Docker and docker-compose (v2 syntax) on the host
- A running MQTT broker (mosquitto works; can run alongside the bridge in the same compose file)
- An RME UFX II with TotalMix FX, OSC enabled in its preferences
- HTTPS for the web UI — the Web MIDI API refuses to work on plain HTTP. A Caddyfile is included; see [HTTPS](#https) below.

---

## Environment variables

Put these in a `.env` file next to `docker-compose.yml`.

| Variable | Default | Required | Description |
|---|---|---|---|
| `OSC_IP` | — | **Yes** | IP of the machine running TotalMix FX |
| `OSC_PORT` | `7001` | No | TotalMix OSC receive port (set in TotalMix → Settings → OSC) |
| `MQTT_BROKER` | `mosquitto` | **Yes** | Hostname or IP of your MQTT broker |
| `MQTT_PORT` | `1883` | No | MQTT broker port |
| `MQTT_USER` | — | No | MQTT username |
| `MQTT_PASS` | — | No | MQTT password |
| `WEB_PORT` | `8088` | No | Internal port for the FastAPI UI (Caddy proxies this) |
| `ENABLE_OSC_MONITOR` | `false` | No | Set `true` to start the OSC address discovery listener |
| `BRIDGE_LOG_FILE` | `bridge.log` | No | Path for the rotating bridge log |
| `OSC_MONITOR_PORT` | `9001` | No | UDP port for the OSC monitor listener |

Example `.env`:

```env
OSC_IP=192.168.1.50
OSC_PORT=7001
MQTT_BROKER=192.168.1.10
MQTT_PASS=yourpassword
```

---

## Step-by-step deployment

### 1. Clone

```bash
git clone https://github.com/auntiepickle/totalmix-osc-bridge.git
cd totalmix-osc-bridge
```

### 2. Create docker-compose.yml

```bash
cp docker-compose.example.yml docker-compose.yml
```

Edit the file. At minimum, set `OSC_IP` and `MQTT_BROKER`. If you're using the SMB snapshot map (recommended), set the volume mount path — see [Snapshot map live sync](#snapshot-map-live-sync) below.

### 3. Copy and edit config files

```bash
cp mappings.example.json mappings.json
cp ufx2_channel_map.example.json ufx2_channel_map.json
```

Edit both to match your TotalMix routing. The bridge will also run with the example files — you'll see an amber banner in the UI reminding you to initialize your own copies.

`ufx2_snapshot_map.json` is not in the repo. Create it on your NAS/config share. See [Snapshot map live sync](#snapshot-map-live-sync).

### 4. Build and start

```bash
docker compose build --no-cache
docker compose up -d
```

Check logs:

```bash
docker compose logs -f
```

You should see `MQTT CONNECTED` and `OSC Client ready → 192.168.x.x:7001` within a few seconds.

### 5. Open the UI

Navigate to `https://YOUR-SERVER-IP`. Select your MIDI device in the header. If macros don't appear, click the settings gear and check the status line.

---

## Updating

The bridge has no database — all state is in files. Updating is a pull and rebuild.

```bash
git pull origin main
docker compose build --no-cache
docker compose up -d
```

`mappings.json` and `ufx2_channel_map.json` are git-ignored. A `git pull` will never touch them.

---

## Snapshot map live sync

`ufx2_snapshot_map.json` maps workspace names to TotalMix Quick Select slot numbers and snapshot names. It lives outside the repo so you can update it from a NAS without redeploying.

**Setup:**

1. Mount your NAS share on the Docker host (e.g. via `/etc/fstab` with CIFS/SMB).
2. In `docker-compose.yml`, mount that share as a read-write volume at `/app/config`:
   ```yaml
   volumes:
     - /mnt/nas/studio-config:/app/config
   ```
3. Create `ufx2_snapshot_map.json` in that directory. See [docs/MAPPINGS_REFERENCE.md](MAPPINGS_REFERENCE.md#ufx2_snapshot_mapjson) for the schema.

The bridge checks the file every 5 seconds. When it changes, it reloads automatically and syncs `bridge.snapshot_map` — no restart needed.

---

## HTTPS

The Web MIDI API requires a [secure context](https://developer.mozilla.org/en-US/docs/Web/Security/Secure_Contexts). That means `https://` or `localhost`. Plain HTTP will silently block MIDI access.

A `Caddyfile` is included that uses `nip.io` wildcard DNS with automatic Let's Encrypt TLS. Edit it to match your IP:

```
192.168.1.x.nip.io {
    reverse_proxy localhost:8088
}
```

Then run Caddy alongside the bridge (add it to `docker-compose.yml` or run it on the host).

---

## Home Assistant integration

The bridge publishes and subscribes to MQTT topics that map cleanly to Home Assistant automations and dashboards.

**Topics the bridge publishes (retained)**

| Topic | Payload | Description |
|---|---|---|
| `totalmix/workspaces` | JSON array | `[{"name": "Pill_setup", "index": 7}, ...]` sorted by slot |
| `totalmix/snapshot_map` | JSON object | Full snapshot map for HA template use |
| `totalmix/snapshot/status` | `loaded_N` | Confirms snapshot N was recalled |

**Topics the bridge subscribes to**

| Topic | Payload | Effect |
|---|---|---|
| `totalmix/workspace` | `"7"` (slot number) | Switch to workspace slot, update bridge state |
| `totalmix/snapshot` | `"4"` (1–8) | Recall snapshot, update bridge state |
| `totalmix/macro/<name>` | `"0.0"–"1.0"` | Fire macro with param |

**Example HA automation — fire a macro from a button:**

```yaml
action:
  - service: mqtt.publish
    data:
      topic: totalmix/macro/an3_to_adat1_send
      payload: "0.8"
```

**Example HA automation — switch workspace:**

```yaml
action:
  - service: mqtt.publish
    data:
      topic: totalmix/workspace
      payload: "7"
```
