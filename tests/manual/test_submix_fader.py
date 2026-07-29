from pythonosc import udp_client
import time
from config import OSC_IP, OSC_PORT

client = udp_client.SimpleUDPClient(OSC_IP, OSC_PORT)

print("=== TEST SUBMIX SELECTION + FADER ===")
print("Setting submix index 6...")
client.send_message("/setSubmix", 6.0)
time.sleep(2)
print("Setting /1/volume1 to 0.75 (test fader)...")
client.send_message("/1/volume1", 0.75)
time.sleep(3)
print("Resetting /1/volume1 to 0.0...")
client.send_message("/1/volume1", 0.0)
print("Test complete.")