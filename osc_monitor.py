import socket
import threading
import time
from config import *

class OSCMonitor:
    """Standalone OSC receiver for learning TotalMix addresses (modular, zero deps)"""

    def __init__(self):
        self.socket = None
        self.thread = None
        self.running = False

    def _receive_loop(self):
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket.bind(('0.0.0.0', OSC_MONITOR_PORT))
            print(f"✅ OSC MONITOR STARTED → Listening on UDP port {OSC_MONITOR_PORT}")
            print("   (Point TotalMix OSC Outgoing → this port + Bridge IP to capture real addresses)"))

            while self.running:
                data, addr = self.socket.recvfrom(2048)
                if data and data.startswith(b'/'):
                    # Extract OSC address (everything before first null byte)
                    addr_end = data.find(b'\0')
                    if addr_end > 0:
                        osc_address = data[:addr_end].decode('ascii', errors='ignore')
                        timestamp = time.strftime("%H:%M:%S.%f")[:-3]
                        print(f"🛰️  [{timestamp}] {osc_address}")
                        # Future: send to WebSocket queue for live web UI
        except Exception as e:
            print(f"❌ OSC Monitor error: {e}")
        finally:
            if self.socket:
                self.socket.close()

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._receive_loop, daemon=True, name="OSCMonitor")
        self.thread.start()

    def stop(self):
        self.running = False
        if self.socket:
            self.socket.close()
        print("🛑 OSC Monitor stopped")

# Singleton
osc_monitor = OSCMonitor()