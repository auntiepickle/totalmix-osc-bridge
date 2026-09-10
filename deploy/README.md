# deploy/

Everything for running the bridge as an always-on server. The `Dockerfile`
stays at the repo root (it is the build context).

| File | Purpose |
|---|---|
| `docker-compose.example.yml` | Copy to the repo root as `docker-compose.yml`; `network_mode: host` so OSC feedback and LAN discovery work; bind-mounts the checkout at `/app` |
| `docker-entrypoint.sh` | Restores the Tailwind-built `style.css` from the image on every start (the bind mount would otherwise shadow it), then execs uvicorn |
| `Caddyfile` | HTTPS front on `<ip>.nip.io` so Web MIDI works from a LAN address (see `docs/setup.md#https`) |
| `ha_config/packages/totalmix.yaml` | Home Assistant package: MQTT entities and automations for the bridge |

Deploying an update to a running compose stack is `git pull` plus
`docker compose restart` (the code is bind-mounted). Rebuild the image only
when `requirements.txt`, the `Dockerfile`, or the web UI's Tailwind classes
change: `docker compose build && docker compose up -d`.

Images built before the code moved into the `tmosc` package start with
`uvicorn web.web_client:app`; `web/web_client.py` is a shim that keeps that
working until the image is rebuilt (the new command is
`uvicorn tmosc.api.app:app`).
