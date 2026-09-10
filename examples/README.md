# examples/

Templates for the three config files. For `mappings.json` and
`ufx2_channel_map.json` the bridge falls back to the template when the real
file is missing and offers "init from example" in the web UI, which copies it
into the state directory; `ufx2_snapshot_map.json` has no fallback - copy the
template or generate it with `tools/scrape_totalmix_snapshots.*`. The state
directory is the repo root from source or Docker, `%APPDATA%\tmosc-bridge` for the Windows installer). Schemas are in
[docs/config.md](../docs/config.md).

| Template | Becomes |
|---|---|
| `mappings.example.json` | `mappings.json` - macros and knobs |
| `ufx2_channel_map.example.json` | `ufx2_channel_map.json` - channel names + physical table |
| `ufx2_snapshot_map.example.json` | `ufx2_snapshot_map.json` - workspaces, slots, snapshot names |
