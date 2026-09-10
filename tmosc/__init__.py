"""tmosc - the TotalMix OSC Bridge server package.

    python -m tmosc                 run the server (same as `uvicorn tmosc.api.app:app`)
    python -m tmosc.bridge          headless bridge without the web UI (MQTT/OSC only)

Layout: bridge.py (macro/knob engine + transports), api/ (FastAPI app: REST + WebSocket),
osc*.py / global_*.py (classic and Global OSC transports and feedback listeners),
mqtt_handler.py, discovery.py (LAN auto-discovery), duck_engine.py, operations.py,
physical_table.py, global_units.py, config.py (env), app_paths.py (where state lives).
"""
