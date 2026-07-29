import os
import logging
from pythonosc.udp_client import SimpleUDPClient

logger = logging.getLogger(__name__)

# Module-level client cache keyed by (ip, port) — avoids creating a new UDP
# socket on every send. Shared by mqtt_handler (via send_osc) and bridge.py
# (via get_client), so the whole app uses one socket per target.
_clients: dict = {}

def get_client(ip: str, port: int = 7001) -> SimpleUDPClient:
    """Return the cached UDP client for (ip, port), creating it on first use."""
    key = (ip, port)
    if key not in _clients:
        _clients[key] = SimpleUDPClient(ip, port)
    return _clients[key]

def send_osc(address: str, value: float, ip: str = None, port: int = 7001):
    """Send a single OSC message. Reuses a cached UDP client per (ip, port)."""
    if ip is None:
        ip = os.getenv("OSC_IP", "127.0.0.1")
    get_client(ip, port).send_message(address, float(value))
    logger.info(f"OSC → {address} = {value}")
