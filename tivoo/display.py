"""Small, deterministic pixel font and Divoom palette encoding."""
import math
import colorsys
from datetime import datetime
from zoneinfo import ZoneInfo
from PIL import Image

FONT = dict(zip('0123456789%?-', [
    ['111','101','101','101','111'], ['010','110','010','010','111'],
    ['111','001','111','100','111'], ['111','001','111','001','111'],
    ['101','101','111','001','001'], ['111','100','111','001','111'],
    ['111','100','111','101','111'], ['111','001','010','010','010'],
    ['111','101','111','101','111'], ['111','101','111','001','111'],
    ['101','001','010','100','101'],
    ['111','001','011','000','010'],
    ['000','000','111','000','000'],
]))

DATE_FONT = dict(zip('0123456789-', [
    ['111', '101', '111'], ['010', '110', '010'],
    ['110', '010', '011'], ['110', '011', '110'],
    ['101', '111', '001'], ['011', '010', '110'],
    ['100', '111', '111'], ['111', '001', '001'],
    ['111', '111', '111'], ['111', '111', '001'],
    ['000', '111', '000'],
]))


def render(remaining: float | None, stale: bool = False, *, resets_at: int | None = None) -> Image.Image:
    image = Image.new('RGB', (16, 16))
    def text(value, y, color, x=None, font=FONT):
        if x is None:
            x = (16 - (4 * len(value) - 1)) // 2
        for char in value:
            for dy, row in enumerate(font[char]):
                for dx, on in enumerate(row):
                    if on == '1':
                        image.putpixel((x + dx, y + dy), color)
            x += 4
    if remaining is None:
        text('?', 1, (255, 160, 0))
    else:
        if not math.isfinite(remaining) or not 0 <= remaining <= 100:
            raise ValueError('Remaining percentage must be between 0 and 100')
        color = (255, 45, 35) if remaining < 10 else ((255, 170, 0) if remaining < 30 else (25, 220, 95))
        # Floor so 99.9% never claims to be completely full.
        text(f'{math.floor(remaining)}%', 1, color)
        filled = math.floor(remaining * 16 / 100)
        for x in range(16):
            for y in (8, 9):
                image.putpixel((x, y), color if x < filled else (12, 25, 28))
    if resets_at is None:
        text('----', 12, (48, 70, 90), font=DATE_FONT)
    else:
        reset = datetime.fromtimestamp(resets_at, ZoneInfo('Asia/Shanghai'))
        # MMDD fits in 15 pixels; tint month/day separately for readability.
        text(reset.strftime('%m'), 12, (48, 100, 180), x=0, font=DATE_FONT)
        text(reset.strftime('%d'), 12, (110, 170, 220), x=8, font=DATE_FONT)
    if stale:
        for point in [(15, 0), (15, 1), (15, 3)]:
            image.putpixel(point, (255, 120, 0))
    return image


FRAME_DURATION_MS = 100


def rainbow_frames(image: Image.Image) -> list[Image.Image]:
    """A dim, seamless rainbow behind the unchanged foreground pixels."""
    frames = []
    for phase in range(16):
        frame = image.copy()
        for y in range(16):
            for x in range(16):
                # Only fill blank pixels, preserving text, bar and stale marker.
                if image.getpixel((x, y)) == (0, 0, 0):
                    rgb = colorsys.hsv_to_rgb(((x + y + phase) % 16) / 16, 1, 0.35)
                    frame.putpixel((x, y), tuple(round(channel * 255) for channel in rgb))
        frames.append(frame)
    return frames


def image_frame(image: Image.Image, duration_ms: int = 0) -> bytes:
    """256 row-major palette indices packed LSB first.

    Protocol reference: solar2ain/tivoo-control (README declares MIT).
    """
    if image.size != (16, 16):
        raise ValueError('Tivoo requires exactly 16 x 16 pixels')
    if not 0 <= duration_ms <= 65535:
        raise ValueError('Frame duration must fit an unsigned 16-bit integer')
    colors, indices = [], []
    for color in image.convert('RGB').get_flattened_data():
        if color not in colors:
            colors.append(color)
        indices.append(colors.index(color))
    bits = max(1, (len(colors) - 1).bit_length())
    packed = bytearray((256 * bits + 7) // 8)
    for i, index in enumerate(indices):
        for bit in range(bits):
            if index & (1 << bit):
                pos = i * bits + bit
                packed[pos // 8] |= 1 << (pos % 8)
    content = duration_ms.to_bytes(2, 'little') + b'\x00' + bytes([len(colors) % 256])
    content += bytes(c for rgb in colors for c in rgb) + packed
    frame = b'\xaa' + (len(content) + 3).to_bytes(2, 'little') + content
    return frame


def image_payload(image: Image.Image) -> bytes:
    return b'\x44\x00\x0a\x0a\x04' + image_frame(image)


def animation_payloads(frames: list[Image.Image], duration_ms: int = FRAME_DURATION_MS) -> list[bytes]:
    """Upload once in 200-byte chunks; the device loops the complete animation."""
    if not frames or not 1 <= duration_ms <= 65535:
        raise ValueError('Animation needs frames and a duration of 1..65535 ms')
    data = b''.join(image_frame(frame, duration_ms) for frame in frames)
    # The total length is uint16, and the chunk index is uint8.
    if len(data) > 200 * 256:
        raise ValueError('Animation exceeds 256 chunks')
    return [b'\x49' + len(data).to_bytes(2, 'little') + bytes([index]) + data[start:start + 200]
            for index, start in enumerate(range(0, len(data), 200))]
