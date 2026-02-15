# TotalMix OSC Bridge

MQTT ↔ OSC bridge for **RME TotalMix FX**.

Gives Home Assistant a clean, fully dynamic workspace selector that always sends the correct slot number.

## Features
- Reads workspace names from `config.py`
- Publishes full 30-slot list + clean named list
- Clean dropdown in HA (no `<Empty>` entries)
- Correct slot numbers even with gaps
- Manual refresh button

## Docker

```yaml
services:
  totalmix-osc-bridge:
    build: https://github.com/auntiepickle/totalmix-osc-bridge.git
    container_name: totalmix-osc-bridge
    restart: unless-stopped
    env_file: .env
```

## Configuration

### `.env`
```env
OSC_IP=192.168.1.61
MQTT_BROKER=192.168.1.10
MQTT_PORT=1883
MQTT_USER=ha
MQTT_PASS=yourpassword
```

### `config.py`
```python
WORKSPACE_NAMES = [
    "Blank", "Music", "Work", "techno", "techno_7", "<Empty>",
    "Pill_setup", "<Empty>", "<Empty>", "<Empty>", "<Empty>", "<Empty>",
    # ... continue to 30 entries
]
```

## Home Assistant

### `packages/totalmix_helpers.yaml`
```yaml
totalmix:
  input_select:
    totalmix_workspace:
      name: "TotalMix Workspace"
      icon: mdi:view-grid
      options:
        - "Loading workspaces..."

  input_button:
    refresh_totalmix_workspaces:
      name: "Refresh TotalMix Workspaces"
      icon: mdi:refresh

  input_text:
    totalmix_workspace_map:
      name: "TotalMix Workspace Map (internal)"
      mode: text
```

### `automations/totalmix.yaml`
```yaml
- id: update_totalmix_workspaces
  alias: "Update TotalMix Workspace List from Bridge"
  trigger:
    - platform: mqtt
      topic: "totalmix/workspaces"
      encoding: ''
  action:
    - variables:
        full_list: "{{ trigger.payload_json }}"
        clean_options: "{{ full_list | reject('eq', '<Empty>') | reject('eq', '') | list }}"
        workspace_map: >
          {% set ns = namespace(map={}) %}
          {% for i in range(full_list | length) %}
            {% set name = full_list[i] %}
            {% if name and name != '<Empty>' %}
              {% set ns.map = ns.map | combine({name: i + 1}) %}
            {% endif %}
          {% endfor %}
          {{ ns.map | tojson }}
    - service: input_select.set_options
      target:
        entity_id: input_select.totalmix_workspace
      data:
        options: "{{ clean_options }}"
    - service: input_text.set_value
      target:
        entity_id: input_text.totalmix_workspace_map
      data:
        value: "{{ workspace_map }}"

- id: load_totalmix_workspace
  alias: "Load Selected TotalMix Workspace"
  trigger:
    - platform: state
      entity_id: input_select.totalmix_workspace
  action:
    - variables:
        selected: "{{ trigger.to_state.state }}"
        workspace_map: >
          {% set s = states('input_text.totalmix_workspace_map') %}
          {% if s in ['unknown', '', None] %}
            {}
          {% else %}
            {{ s | from_json }}
          {% endif %}
        index: "{{ workspace_map.get(selected, 1) }}"
    - service: mqtt.publish
      data:
        topic: "totalmix/workspace"
        payload: "{{ index }}"
```

## MQTT Topics

**Published by bridge (retained):**
- `totalmix/workspaces` → full 30-slot list
- `totalmix/workspaces_named` → clean list only

**Subscribed by bridge:**
- `totalmix/workspace` → slot number (1–30)
```