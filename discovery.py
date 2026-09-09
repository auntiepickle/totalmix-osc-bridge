"""LAN discovery responder — lets the tray agent / installer auto-find this
bridge, so a client install needs no host to be typed in.

A client UDP-broadcasts ``TMOSC-DISCOVER?`` to the discovery port; we reply
``TMOSC-BRIDGE <web_port>``. The client learns this host's IP from the reply's
source address (and the HTTP port from the payload). Works because the bridge
runs host-networked; harmless (and simply never answers) if the broadcast can't
reach us. Fire-and-forget daemon thread; failures are logged, never fatal.
"""
import socket
import threading
import logging

logger = logging.getLogger("Discovery")

MAGIC_REQ = b"TMOSC-DISCOVER?"
MAGIC_REP = b"TMOSC-BRIDGE"


def _serve(web_port: int, disc_port: int):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except OSError:
            pass
        s.bind(("0.0.0.0", disc_port))
        logger.info(f"discovery responder on udp/{disc_port} -> advertises http port {web_port}")
        reply = MAGIC_REP + b" " + str(web_port).encode()
        while True:
            try:
                data, addr = s.recvfrom(64)
            except OSError:
                break
            if data.strip() == MAGIC_REQ:
                try:
                    s.sendto(reply, addr)
                except OSError:
                    pass
    except Exception as e:
        logger.warning(f"discovery responder disabled: {e}")


def start(web_port: int, disc_port: int = None):
    """Start the responder in a daemon thread. disc_port defaults to web_port."""
    if disc_port is None:
        disc_port = web_port
    threading.Thread(target=_serve, args=(web_port, disc_port),
                     name="discovery", daemon=True).start()
