import json
import os
from config import WORKSPACE_NAMES

def publish_workspaces(client):
    """Publish full list (for correct indexing) + clean list (for nice dropdown)."""
    # Full list with <Empty> slots — used for slot numbers
    client.publish("totalmix/workspaces", json.dumps(WORKSPACE_NAMES), retain=True, qos=1)
    
    # Clean list without <Empty> — used for the HA dropdown
    clean_names = [n for n in WORKSPACE_NAMES if n and n != "<Empty>"]
    client.publish("totalmix/workspaces_named", json.dumps(clean_names), retain=True, qos=1)
    
    print(f"✅ Published {len(WORKSPACE_NAMES)} full slots + {len(clean_names)} named workspaces")