import socket
import threading
import time
import logging
import logging.handlers
import os
from config import *

# Guaranteed file + rotation
logger = logging.getLogger("OSCMonitor")
logger.setLevel(logging.INFO)

# Console (clean)
console = logging.StreamHandler()
console.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | %(message)s', '%H:%M:%S'))
logger.addHandler(console)

# File with 100 KB rotation
fileh = logging.handlers.RotatingFileHandler(
    OSC_MONITOR_LOG_FILE,
    maxBytes=LOG_MAX_BYTES,
    backupCount=LOG_BACKUP_COUNT,
    encoding='utf-8'
)
fileh.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | %(message)s', '%H:%M:%S'))
logger.addHandler(fileh)

logger.info(f"OSC MONITOR LOG INITIALIZED → {OSC_MONITOR_LOG_FILE} (max 100 KB)")

class OSCMonitor:
    def __init__(self):
        self.socket = None
        self.thread = None
        self.running = False

    def _receive_loop(self):
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket.bind(('0.0.0.0', OSC_MONITOR_PORT))
            logger.info(f"OSC MONITOR STARTED → UDP port {OSC_MONITOR_PORT} | Log: {OSC_MONITOR_LOG_FILE} (max 100 KB)")
            logger.info("   → Test ADAT 13/14 Software Playback (click channel first)")

            while self.running:
                data, addr = self.socket.recvfrom(4096)
                ts = time.strftime("%H:%M:%S.%f")[:-3]

                if data.startswith(b'#bundle'):
                    try:
                        from pythonosc.osc_bundle import OscBundle
                        from pythonosc.osc_message import OscMessage
                        bundle = OscBundle(data)
                        for item in bundle:
                            if isinstance(item, OscMessage):
                                value_str = " ".join([str(p) for p in item.params])
                                if item.address == '/' and value_str == '0.0':
                                    logger.debug(f"[OSC] {ts}  {item.address} → {value_str} (keepalive)")
                                else:
                                    logger.info(f"[OSC] {ts}  {item.address} → {value_str}   ←←← COPY THIS FOR MAPPINGS")
                    except Exception as e:
                        logger.error(f"Bundle parse error: {e}")
        except Exception as e:
            logger.error(f"OSC Monitor error: {e}")
        finally:
            if self.socket:
                self.socket.close()

    def start(self):
        if self.running: return
        self.running = True
        self.thread = threading.Thread(target=self._receive_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.socket:
            self.socket.close()
        logger.info("OSC Monitor stopped")

osc_monitor = OSCMonitor()