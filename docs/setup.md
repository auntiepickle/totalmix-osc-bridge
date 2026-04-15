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
