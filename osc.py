import socket
import struct

def send_osc(address, value=1.0, osc_ip=None, osc_port=7001):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        addr_padded = address + '\0' * ((4 - len(address) % 4) % 4)
        msg = addr_padded.encode() + b',f\0\0' + struct.pack('>f', float(value))
        sock.sendto(msg, (osc_ip, osc_port))
        print(f"OSC SENT → {address} = {value}")
    except Exception as e:
        print(f"OSC FAIL: {e}")
