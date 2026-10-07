"""Bounded native Bluetooth bridge calls with validated device acknowledgments."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def responses(log: str):
    data = bytearray()
    for line in log.splitlines():
        if 'RX:' in line:
            data.extend(bytes.fromhex(line.split('RX:', 1)[1]))
    while len(data) >= 6:
        if data[0] != 1:
            del data[0]
            continue
        size = int.from_bytes(data[1:3], 'little')
        total = size + 4
        if len(data) < total:
            break
        frame = data[:total]
        del data[:total]
        if frame[-1] != 2 or sum(frame[1:-3]) % 65536 != int.from_bytes(frame[-3:-1], 'little'):
            continue
        yield bytes(frame[3:-3])


def send(mac: str, payload: bytes):
    if not re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}', mac):
        raise ValueError('Invalid Bluetooth MAC address')
    binary = ROOT / 'build' / 'tivoo_cmd'
    if not binary.exists():
        raise RuntimeError('Bluetooth bridge missing; run ./setup.sh')
    result = subprocess.run([str(binary), '-a', mac, payload.hex()],
                            capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise RuntimeError(f'Bluetooth failed ({result.returncode}): {result.stderr.strip()}')
    packets = list(responses(result.stderr))
    if not any(p[:3] == bytes([4, payload[0], 0x55]) for p in packets):
        raise RuntimeError(f'No valid device acknowledgment for command 0x{payload[0]:02x}')
    return packets
