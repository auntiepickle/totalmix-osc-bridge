import json
from config import WORKSPACE_NAMES

def publish_workspaces(client):
    client.publish("totalmix/workspaces", json.dumps(WORKSPACE_NAMES))
    print(f"Published {len(WORKSPACE_NAMES)} workspace names to HA")
