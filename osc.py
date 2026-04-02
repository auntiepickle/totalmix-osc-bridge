import socket
import struct

def send_osc(address: str, value: float, ip: str = None, port: int = 7001):
    """Reliable OSC sender — now uses the exact method that works for snapshots."""
    from pythonosc.udp_client import SimpleUDPClient
    import os
    import logging
    logger = logging.getLogger(__name__)
    
    if ip is None:
        ip = os.getenv("OSC_IP", "127.0.0.1")
    
    client = SimpleUDPClient(ip, port)
    client.send_message(address, float(value))   # explicit float — matches the working test
    
    logger.info(f"OSC SENT → {address} = {value}")
