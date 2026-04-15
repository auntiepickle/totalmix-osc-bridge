# Setup

Two ways to run the bridge: local Python on any machine that has TotalMix, or Docker for an always-on server. Both are first-class. MQTT is optional in both cases.

---

## Local (laptop / portable)

Best for: road use, development, running on the same machine as TotalMix.

### Prerequisites

- Python 3.12+
- TotalMix FX with OSC enabled: TotalMix → Settings → OSC, enable and set a receive port (default `7001`)

No MQTT broker required. No Docker. No HTTPS required when accessing via `localhost`.

### Setup

```bash
git clone https://github.com/auntiepickle/totalmix-osc-bridge.git
cd totalmix-osc-bridge

python -m venv .venv
source .venv/bin/activate    # macOS/Linux
# .venv\Scripts\activate     # Windows

pip install -r requirements.txt

cp mappings.example.json mappings.json
cp ufx2_channel_map.example.json ufx2_channel_map.json
cp ufx2_snapshot_map.example.json ufx2_snapshot_map.json
```

### Run

```bash
export OSC_IP=127.0.0.1    # TotalMix on same machine; use its LAN IP if remote
export OSC_PORT=7001
uvicorn web.web_client:app --host 0.0.0.0 --port 8080 --reload
```

Open `http://localhost:8080`. Select your MIDI input in the header. Macro cards load from `mappings.json`.

`--reload` restarts on Python file changes. Config files hot-reload via the UI without it.

### Finding OSC addresses

Move a fader in TotalMix while the OSC monitor is running:

```bash
ENABLE_OSC_MONITOR=true uvicorn web.web_client:app --host 0.0.0.0 --port 8080
# OSC addresses appear in osc_monitor.log as you move faders
```

---

## Docker (always-on server)

Best for: home server that runs 24/7 alongside other services.

### Prerequisites

- Docker + docker-compose v2
- TotalMix FX with OSC enabled on a machine reachable by the server
- HTTPS — the Web MIDI API won't work on a real IP over plain HTTP. See [HTTPS](#https) below.

MQTT is optional. If you have a broker (e.g. Mosquitto for Home Assistant), configure it. If not, leave those vars unset — the bridge starts fine without it.

### Environment variables

Minimum required:

```env
OSC_IP=192.168.1.50
```

Full variable list:

| Variable | Default | Required | Description |
|---|---|---|---|
| `OSC_IP` | — | **Yes** | IP of the machine running TotalMix. |
| `OSC_PORT` | `7001` | No | TotalMix OSC receive port. |
| `WEB_PORT` | `8088` | No | Internal HTTP port (Caddy proxies this). |
| `MQTT_BROKER` | — | No | Hostname or IP of your MQTT broker. Omit if not using MQTT. |
| `MQTT_PORT` | `1883` | No | MQTT port. |
| `MQTT_USER` | — | No | MQTT username. |
| `MQTT_PASS` | — | No | MQTT password. |
| `ENABLE_OSC_MONITOR` | `false` | No | Set `true` to log all incoming OSC from TotalMix to `osc_monitor.log`. |
| `BRIDGE_LOG_FILE` | `bridge.log` | No | Path for the rotating bridge log. |
| `OSC_MONITOR_PORT` | `9001` | No | UDP port for the OSC listener. |

Create a `.env` file next to `docker-compose.yml`:

```env
OSC_IP=192.168.1.50
OSC_PORT=7001
WEB_PORT=8088
# MQTT — optional, remove these lines if you don't have a broker
MQTT_BROKER=192.168.1.10
MQTT_PORT=1883
MQTT_USER=studio
MQTT_PASS=yourpassword
```

### Deploy

```bash
cp docker-compose.example.yml docker-compose.yml
# Edit docker-compose.yml as needed

cp mappings.example.json mappings.json
cp ufx2_channel_map.example.json ufx2_channel_map.json

docker compose build --no-cache
docker compose up -d
docker compose logs -f
```

Within a few seconds you should see `OSC Client ready → 192.168.x.x:7001`. If MQTT is configured, `MQTT connected` appears too.

### Update

```bash
git pull origin main
docker compose build --no-cache
docker compose up -d
```

`mappings.json` and `ufx2_channel_map.json` are git-ignored. A pull never touches them.

### Snapshot map — live sync from NAS

`ufx2_snapshot_map.json` can live outside the repo so it can be updated from a NAS without redeploying. The bridge polls `/app/config/ufx2_snapshot_map.json` every 5 seconds.

1. Mount your NAS share on the Docker host (e.g. via CIFS in `/etc/fstab`)
2. Bind-mount it in `docker-compose.yml`:
   ```yaml
   volumes:
     - /mnt/nas/studio-config:/app/config
   ```
3. Place `ufx2_snapshot_map.json` in that directory

If the path doesn't exist or the file isn't there, the bridge falls back to the local copy without error.

### HTTPS

The Web MIDI API requires a secure context on real IPs. `localhost` is exempt — no HTTPS needed there. For a server on your LAN, you need HTTPS.

The included `Caddyfile` uses `nip.io` for automatic DNS and Let's Encrypt TLS. `nip.io` maps any `IP.nip.io` hostname to that IP, so you get a valid HTTPS certificate for a LAN address with zero DNS config.

Edit `Caddyfile` to match your server IP:

```
192.168.1.x.nip.io {
    reverse_proxy localhost:8088
}
```

Run Caddy on the host or add it to `docker-compose.yml`. Access the UI at `https://192.168.1.x.nip.io`.

Alternatives: [mkcert](https://github.com/FiloSottile/mkcert) for a local CA, or a real domain with Certbot.

---

## MQTT + Home Assistant (optional)

MQTT is purely additive. The bridge works without it. With it, you get bidirectional state sync and the ability to trigger macros from HA automations — useful if you want your studio routing to react to events outside TotalMix (calls, meetings, scene changes in a smart home setup).

### Topics the bridge publishes

| Topic | Payload | Description |
|---|---|---|
| `totalmix/workspaces` | JSON array | `[{"name": "Pill_setup", "index": 7}, ...]` sorted by slot |
| `totalmix/snapshot_map` | JSON object | Full snapshot map |
| `totalmix/snapshot/status` | `loaded_N` | Confirms snapshot N was recalled |

### Topics the bridge subscribes to

| Topic | Payload | Effect |
|---|---|---|
| `totalmix/workspace` | `"7"` (slot number) | Switch workspace |
| `totalmix/snapshot` | `"4"` (1–8) | Recall snapshot |
| `totalmix/macro/<name>` | `"0.0"` – `"1.0"` | Fire macro with param value |

### Fire a macro from an HA automation

```yaml
action:
  - service: mqtt.publish
    data:
      topic: totalmix/macro/reverb_send_ramp
      payload: "0.8"
```

### Example use case

VoIP call starts → HA detects it → publishes to `totalmix/macro/call_routing` → bridge switches to your call workspace and routes your studio mic to the system output. Call ends → HA triggers the reverse. Your interface stays in front of you, routing changes automatically.
