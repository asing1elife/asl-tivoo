"""Small, deterministic pixel font and Divoom palette encoding."""
import math
from PIL import Image

FONT = dict(zip('0123456789%W?', [
    ['111','101','101','101','111'], ['010','110','010','010','111'],
    ['111','001','111','100','111'], ['111','001','111','001','111'],
    ['101','101','111','001','001'], ['111','100','111','001','111'],
    ['111','100','111','101','111'], ['111','001','010','010','010'],
    ['111','101','111','101','111'], ['111','101','111','001','111'],
    ['101','001','010','100','101'], ['101','101','101','111','101'],
    ['111','001','011','000','010'],
]))


def render(remaining: float | None, stale: bool = False) -> Image.Image:
    image = Image.new('RGB', (16, 16))
    def text(value, y, color):
        x = (16 - (4 * len(value) - 1)) // 2
        for char in value:
            for dy, row in enumerate(FONT[char]):
                for dx, on in enumerate(row):
                    if on == '1':
                        image.putpixel((x + dx, y + dy), color)
            x += 4
    text('W', 0, (48, 100, 180))
    if remaining is None:
        text('?', 6, (255, 160, 0))
    else:
        if not math.isfinite(remaining) or not 0 <= remaining <= 100:
            raise ValueError('Remaining percentage must be between 0 and 100')
        color = (255, 45, 35) if remaining < 10 else ((255, 170, 0) if remaining < 30 else (25, 220, 95))
        # Floor so 99.9% never claims to be completely full.
        text(f'{math.floor(remaining)}%', 6, color)
        filled = math.floor(remaining * 16 / 100)
        for x in range(16):
            for y in (13, 14):
                image.putpixel((x, y), color if x < filled else (12, 25, 28))
    if stale:
        for point in [(15, 0), (15, 1), (15, 3)]:
            image.putpixel(point, (255, 120, 0))
    return image


def image_payload(image: Image.Image) -> bytes:
    """Static-image command, 256 row-major palette indices packed LSB first.

    Protocol reference: solar2ain/tivoo-control (README declares MIT).
    """
    if image.size != (16, 16):
        raise ValueError('Tivoo requires exactly 16 x 16 pixels')
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
    content = b'\x00\x00\x00' + bytes([len(colors) % 256])
    content += bytes(c for rgb in colors for c in rgb) + packed
    frame = b'\xaa' + (len(content) + 3).to_bytes(2, 'little') + content
    return b'\x44\x00\x0a\x0a\x04' + frame
