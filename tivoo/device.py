"""Bounded native Bluetooth bridge calls with validated device acknowledgments."""
import logging
import re
from collections import Counter
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = logging.getLogger('tivoo.bluetooth')


def log_diagnostics(stderr):
    """Keep connection evidence even on success, without logging image payloads."""
    if isinstance(stderr, bytes):
        stderr = stderr.decode('utf-8', errors='replace')
    stderr = stderr or ''
    for line in stderr.splitlines():
        if 'BT_DIAG ' in line:
            LOG.info('Bluetooth: %s', line.split('BT_DIAG ', 1)[1])
    # Keep native errors useful but bounded; raw TX/RX can be many kilobytes.
    return '\n'.join(line for line in stderr.splitlines()
                     if 'TX:' not in line and 'RX:' not in line)[-4000:]


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
    return send_session(mac, [payload])


def send_session(mac: str, payloads: list[bytes]):
    """Keep one RFCOMM connection open for the complete upload."""
    if not re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}', mac):
        raise ValueError('Invalid Bluetooth MAC address')
    if not payloads or any(not payload for payload in payloads):
        raise ValueError('Cannot send empty payloads')
    binary = ROOT / 'build' / 'tivoo_cmd'
    if not binary.exists():
        raise RuntimeError('Bluetooth bridge missing; run ./setup.sh')
    args = [str(binary), '-a', mac, '-s']
    for index, payload in enumerate(payloads):
        if index:
            args.append('--')
        args.append(payload.hex())
    # Budget for initialization, three native open attempts and retry delays.
    # The parent also bounds a stuck synchronous macOS Bluetooth call.
    timeout = 60 + len(payloads) * 0.2
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        detail = log_diagnostics(exc.stderr)
        raise RuntimeError(f'Bluetooth bridge timed out after {timeout:g}s; '
                           f'partial diagnostics: {detail or "none"}') from None
    detail = log_diagnostics(result.stderr)
    if result.returncode:
        raise RuntimeError(f'Bluetooth failed ({result.returncode}): {detail.strip()}')
    packets = list(responses(result.stderr))
    expected = Counter(payload[0] for payload in payloads)
    if 0x49 in expected:
        # Tivoo acknowledges the completed animation once, not each chunk.
        expected[0x49] = 1
    received = Counter(p[1] for p in packets if len(p) >= 3 and p[0] == 4 and p[2] == 0x55)
    for command, count in expected.items():
        if received[command] < count:
            raise RuntimeError(f'No valid device acknowledgment for command 0x{command:02x}: '
                               f'{received[command]}/{count} received')
    return packets
