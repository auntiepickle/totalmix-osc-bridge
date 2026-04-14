# Setup

Getting the bridge running — Docker for the server, or a local Python environment for development.

---

## Docker (production)

### Prerequisites

- Docker + docker-compose (v2)
- A running MQTT broker (mosquitto runs well alongside the bridge)
- TotalMix FX with OSC enabled: TotalMix → Settings → OSC, set a receive port (default 7001)
- HTTPS — the Web MIDI API won't work on plain HTTP. See [HTTPS](#https) below.

### Environment variables

Create a `.env` file next to `docker-compose.yml`:

```env
OSC_IP=192.168.1.50          # IP of your TotalMix machine
OSC_PORT=7001
MQTT_BROKER=192.168.1.10
MQTT_PORT=1883
MQTT_USER=studio
MQTT_PASS=yourpassword
WEB_PORT=8088
```

Full variable list:

| Variable | Default | Description |
|---|---|---|
| `OSC_IP` | — | **Required.** IP of the TotalMix host. |
| `OSC_PORT` | `7001` | TotalMix OSC receive port. |
| `MQTT_BROKER` | `mosquitto` | **Required.** Hostname or IP of your broker. |
| `MQTT_PORT` | `1883` | MQTT port. |
| `MQTT_USER` | — | MQTT username. |
| `MQTT_PASS` | — | MQTT password. |
| `WEB_PORT` | `8088` | Internal port (Caddy proxies this). |
| `ENABLE_OSC_MONITOR` | `false` | Set `true` to log all incoming OSC from TotalMix. Useful for finding addresses. |
| `BRIDGE_LOG_FILE` | `bridge.log` | Path for the rotating bridge log. |
| `OSC_MONITOR_PORT` | `9001` | UDP port for the OSC listener. |

### Deploy

```bash
git clone https://github.com/auntiepickle/totalmix-osc-bridge.git
cd totalmix-osc-bridge

cp docker-compose.example.yml docker-compose.yml
# Edit docker-compose.yml

cp mappings.example.json mappings.json
cp ufx2_channel_map.example.json ufx2_channel_map.json
# Edit both to match your routing

docker compose build --no-cache
docker compose up -d
docker compose logs -f
```

You should see `MQTT connected` and `OSC Client ready → 192.168.x.x:7001` within a few seconds.

### Update

```bash
git pull origin main
docker compose build --no-cache
docker compose up -d
```

`mappings.json` and `ufx2_channel_map.json` are git-ignored. A pull will never touch them.

### Snapshot map live sync

`ufx2_snapshot_map.json` lives outside the repo so it can be updated from a NAS without redeploying.

1. Mount your NAS share on the Docker host (e.g. via CIFS in `/etc/fstab`).
2. In `docker-compose.yml`, bind-mount it at `/app/config`:
   ```yaml
   volumes:
     - /mnt/nas/studio-config:/app/config
   ```
3. Create `ufx2_snapshot_map.json` in that directory. See [CONFIG_REFERENCE.md](CONFIG_REFERENCE.md#ufx2_snapshot_mapjson) for the schema.

The bridge checks for changes every 5 seconds and reloads automatically.

### HTTPS

The Web MIDI API requires a secure context. Plain HTTP silently blocks MIDI access.

The included `Caddyfile` uses `nip.io` for automatic DNS and Let's Encrypt TLS. Edit it to match your server IP:

```
192.168.1.x.nip.io {
    reverse_proxy localhost:8088
}
```

Run Caddy on the host or add it to `docker-compose.yml`. Then access the UI at `https://192.168.1.x.nip.io`.

---

## Local development

### Prerequisites

- Python 3.12+
- A running MQTT broker
- TotalMix FX instance (or skip `OSC_IP` — OSC sends will fail silently)

### Setup

```bash
python -m venv .venv
source .venv/bin/activate    # macOS/Linux
# .venv\Scripts\activate     # Windows

pip install -r requirements.txt

cp mappings.example.json mappings.json
cp ufx2_channel_map.example.json ufx2_channel_map.json
cp ufx2_snapshot_map.example.json ufx2_snapshot_map.json
```

Export env vars (or create a `.env` and load it):

```bash
export OSC_IP=192.168.1.50
export MQTT_BROKER=localhost
export WEB_PORT=8088
```

### Run

```bash
uvicorn web.web_client:app --host 0.0.0.0 --port 8088 --reload
```

`--reload` restarts on Python file changes. Config files hot-reload via the UI API without it.

Navigate to `http://localhost:8088`. MIDI works on `localhost` (it's a secure context). For a real IP, you need HTTPS — use [mkcert](https://github.com/FiloSottile/mkcert) or the Caddyfile approach above.

### Run bridge standalone (no web UI)

```bash
python bridge.py
```

Connects to MQTT and starts the OSC bridge without the FastAPI server. Useful for testing macro execution in isolation.

### Finding OSC addresses

Move a fader in TotalMix while the OSC monitor is running — it logs the address:

```bash
ENABLE_OSC_MONITOR=true python bridge.py
# then check osc_monitor.log
```

---

## Home Assistant

The bridge publishes workspace and snapshot state as retained MQTT messages.

**Topics the bridge publishes**

| Topic | Payload | Description |
|---|---|---|
| `totalmix/workspaces` | JSON array | `[{"name": "Pill_setup", "index": 7}, ...]` sorted by slot |
| `totalmix/snapshot_map` | JSON object | Full snapshot map |
| `totalmix/snapshot/status` | `loaded_N` | Confirms snapshot N was recalled |

**Topics the bridge subscribes to**

| Topic | Payload | Effect |
|---|---|---|
| `totalmix/workspace` | `"7"` (slot number) | Switch workspace |
| `totalmix/snapshot` | `"4"` (1–8) | Recall snapshot |
| `totalmix/macro/<name>` | `"0.0"` – `"1.0"` | Fire macro with param |

**Fire a macro from an HA automation:**

```yaml
action:
  - service: mqtt.publish
    data:
      topic: totalmix/macro/an3_to_adat1_send
      payload: "0.8"
```
