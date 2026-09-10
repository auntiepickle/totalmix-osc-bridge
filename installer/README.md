# installer/

`tmosc-setup.iss` is the Inno Setup script for the unified Windows installer
published on every `v*` tag as `tmosc-setup-<version>.exe`.

- **Types**: Client + Server (this PC runs TotalMix and hosts the bridge),
  Client only (tray agent; the bridge runs elsewhere), Server only.
- **Server pages** write `%APPDATA%\tmosc-bridge\config.env`: TotalMix IP,
  OSC in/out ports, web port, transport (classic or global), optional MQTT
  (blank = `ENABLE_MQTT=False`).
- **Client page** writes `%APPDATA%\tmosc-agent\config.txt`: bridge host
  (auto-discovered on the LAN, or `127.0.0.1` when the server is installed
  too), port, MIDI input (listed by `tmosc-agent --list`), optional HTTPS URL.
- Existing values are read back into the wizard on upgrade. Per-user install
  by default; an elevated install also adds a Windows Firewall rule for the
  bridge. Uninstall keeps the `%APPDATA%` state.

Inputs are staged by CI (`.github/workflows/release.yml`): the signed agent
exes in `agent/windows/` and the signed PyInstaller folder in
`dist/tmosc-bridge/`. Compile locally from `installer/` with
`iscc /DAppVersion=0.0.0-dev tmosc-setup.iss` after staging the same inputs
(the script resolves its inputs relative to itself).
The client-only `agent/windows/installer.iss` still exists for `agent-v*` tags.
