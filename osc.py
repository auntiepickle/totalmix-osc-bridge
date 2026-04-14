import os
import logging
from pythonosc.udp_client import SimpleUDPClient

logger = logging.getLogger(__name__)

# Module-level client cache keyed by (ip, port) — avoids creating a new UDP
# socket on every send. mqtt_handler is the primary caller; bridge.py uses its
# own osc_client directly.
_clients: dict = {}

def send_osc(address: str, value: float, ip: str = None, port: int = 7001):
    """Send a single OSC message. Reuses a cached UDP client per (ip, port)."""
    if ip is None:
        ip = os.getenv("OSC_IP", "127.0.0.1")
    key = (ip, port)
    if key not in _clients:
        _clients[key] = SimpleUDPClient(ip, port)
    _clients[key].send_message(address, float(value))
    logger.info(f"OSC → {address} = {value}")
