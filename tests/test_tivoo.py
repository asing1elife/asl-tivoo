import unittest
from unittest.mock import patch
from PIL import Image
from tivoo.display import render, image_payload
from tivoo.device import responses, send
from tivoo.quota import select_weekly


def window(used=8, minutes=10080):
    return {'usedPercent': used, 'windowDurationMins': minutes, 'resetsAt': 1700000000}


class QuotaTests(unittest.TestCase):
    def test_weekly_selected_by_duration_not_position(self):
        for a, b in [(window(), window(90, 300)), (window(90, 300), window())]:
            value = select_weekly({'rateLimitsByLimitId': {'codex': {'primary': a, 'secondary': b}}})
            self.assertEqual(value.remaining, 92)

    def test_prefer_named_bucket_over_legacy(self):
        result = {'rateLimitsByLimitId': {'codex': {'secondary': window(20)}},
                  'rateLimits': {'secondary': window(99)}}
        self.assertEqual(select_weekly(result).remaining, 80)

    def test_never_confuse_model_or_short_window_with_week(self):
        for result in [
            {'rateLimitsByLimitId': {'other': {'secondary': window()}}},
            {'rateLimits': {'secondary': window(8, 300)}},
            {'rateLimits': {'limitId': 'other', 'secondary': window()}},
        ]:
            with self.assertRaises(ValueError):
                select_weekly(result)

    def test_unavailable_is_not_zero_usage(self):
        for used in (None, True, '8', float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                select_weekly({'rateLimits': {'secondary': window(used)}})

    def test_clamp_overuse(self):
        self.assertEqual(select_weekly({'rateLimits': {'secondary': window(110)}}).remaining, 0)


class DisplayTests(unittest.TestCase):
    def test_all_percentages_fit_and_have_correct_bar(self):
        for value in range(101):
            image = render(value)
            self.assertEqual(image.size, (16, 16))
            active = sum(image.getpixel((x, 8)) != (12, 25, 28) for x in range(16))
            self.assertEqual(active, value * 16 // 100)
        for value in (None, 99.9, 0):
            self.assertEqual(render(value, stale=True).getpixel((15, 0)), (255, 120, 0))

    def test_independent_decoder_round_trip(self):
        # Covers 1, 2, 3, 4, 5, 8, 9 and 256 colors, including non-byte-aligned indices.
        for count in (1, 2, 3, 4, 5, 8, 9, 256):
            original = [(i % count, (i % count) * 2 % 256, 120) for i in range(256)]
            image = Image.new('RGB', (16, 16))
            image.putdata(original)
            payload = image_payload(image)
            self.assertEqual(payload[:6], bytes.fromhex('44000a0a04aa'))
            frame = payload[5:]
            self.assertEqual(int.from_bytes(frame[1:3], 'little'), len(frame))
            n = frame[6] or 256
            palette = [tuple(frame[7+i*3:10+i*3]) for i in range(n)]
            pixels = int.from_bytes(frame[7+n*3:], 'little')
            width = max(1, (n-1).bit_length())
            decoded = [palette[(pixels >> (i*width)) & ((1 << width)-1)] for i in range(256)]
            self.assertEqual(decoded, original)

    def test_invalid_images_rejected(self):
        with self.assertRaises(ValueError):
            image_payload(Image.new('RGB', (32, 32)))


class BluetoothTests(unittest.TestCase):
    def test_actual_device_status_checksum(self):
        log = 'RX: 01 19 00 04 46 55 00 00 00 FF 00 00 64 00 01 0A 64 01 FF 00 00 01 01 00 00 01 8D 03 02'
        self.assertTrue(list(responses(log))[0].startswith(bytes.fromhex('044655')))
        self.assertEqual(list(responses(log.replace('8D 03', '8E 03'))), [])

    def test_fragmented_ack(self):
        self.assertEqual(list(responses('RX: 01 05\nRX: 00 04 44 55 A2 00 02')),
                         [bytes.fromhex('044455')])

    @patch('tivoo.device.subprocess.run')
    def test_exit_zero_without_ack_is_failure(self, run):
        run.return_value.returncode = 0
        run.return_value.stderr = 'RFCOMM OK'
        with self.assertRaisesRegex(RuntimeError, 'No valid device acknowledgment'):
            send('AA:BB:CC:DD:EE:FF', b'\x44')


if __name__ == '__main__':
    unittest.main()
