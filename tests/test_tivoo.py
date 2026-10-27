import subprocess
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from tivoo.display import render, image_payload, rainbow_frames, animation_payloads
from tivoo.device import responses, send, send_session
from tivoo.quota import select_weekly, select_quota
import service


def window(used=8, minutes=10080):
    return {'usedPercent': used, 'windowDurationMins': minutes, 'resetsAt': 1700000000}


class QuotaTests(unittest.TestCase):
    def test_short_window_selected_by_duration_not_position(self):
        for a, b in [(window(73, 300), window(20)), (window(20), window(73, 300))]:
            value = select_quota({'rateLimitsByLimitId': {'codex': {'primary': a, 'secondary': b}}}, window='5h')
            self.assertEqual(value.remaining, 27)
            self.assertEqual(value.window, '5h')

    def test_short_window_missing_or_ambiguous_never_falls_back(self):
        for bucket in ({'primary': window()},
                       {'primary': window(20, 300), 'secondary': window(70, 300)}):
            with self.assertRaisesRegex(ValueError, '300-minute'):
                select_quota({'rateLimits': bucket}, window='5h')

    def test_short_window_invalid_data_rejected(self):
        for used in (None, True, '8', float('nan')):
            with self.assertRaises(ValueError):
                select_quota({'rateLimits': {'primary': window(used, 300)}}, window='5h')

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
    def test_short_window_uses_beijing_time_and_colon(self):
        # 1700000000 is 06:13 on the following day in Beijing.
        from tivoo.display import DATE_FONT
        image = render(50, resets_at=1700000000, window='5h')
        for char, x in zip('0613', (0, 4, 8, 12)):
            color = (48, 100, 180) if x < 8 else (110, 170, 220)
            for dy, row in enumerate(DATE_FONT[char]):
                for dx, on in enumerate(row):
                    self.assertEqual(image.getpixel((x + dx, 12 + dy)), color if on == '1' else (0, 0, 0))
        for reset in (None, 1700000000):
            self.assertEqual(render(50, resets_at=reset, window='5h').getpixel((7, 12)), (110, 170, 220))
            self.assertEqual(render(50, resets_at=reset).getpixel((7, 12)), (0, 0, 0))

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


class ServiceTests(unittest.TestCase):
    @patch.object(service.time, 'sleep')
    @patch.object(service.subprocess, 'run')
    def test_bootstrap_retries_transient_teardown_error(self, run, sleep):
        run.side_effect = [subprocess.CompletedProcess([], code, '', 'Input/output error')
                           for code in (5, 5, 0)]
        service.bootstrap()
        self.assertEqual(run.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.25, 0.5])

    @patch.object(service.time, 'sleep')
    @patch.object(service.subprocess, 'run')
    def test_bootstrap_retry_is_bounded_and_other_errors_fail_immediately(self, run, sleep):
        for code, attempts in ((5, 6), (78, 1)):
            run.reset_mock()
            run.return_value = subprocess.CompletedProcess([], code, '', 'test failure')
            with self.assertRaisesRegex(RuntimeError, f'exit {code}'):
                service.bootstrap()
            self.assertEqual(run.call_count, attempts)

    def test_failed_switch_restores_previous_configuration_and_service(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'service.plist'
            original = plistlib.dumps({'ProgramArguments': ['python', '-m', 'tivoo', 'watch']})
            path.write_bytes(original)
            with patch.object(service, 'PLIST', path), patch.object(service, 'bootstrap') as bootstrap, \
                    patch.object(service.subprocess, 'run'):
                bootstrap.side_effect = [RuntimeError('load failed'), None]
                with self.assertRaisesRegex(RuntimeError, '已恢复原配置和服务状态'):
                    service.save_window(plistlib.loads(original), '5h', True)
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(bootstrap.call_count, 2)

    def test_switch_preserves_configuration_and_stopped_state(self):
        for loaded in (False, True):
            with self.subTest(loaded=loaded), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'service.plist'
                original = {'ProgramArguments': ['python', '-m', 'tivoo', '--mac', 'AA:BB:CC:DD:EE:FF',
                                                  '--limit-id', 'codex', 'watch', '--interval', '60'],
                            'EnvironmentVariables': {'CODEX_BIN': '/custom/codex'}, 'KeepAlive': True}
                path.write_bytes(plistlib.dumps(original))
                with patch.object(service, 'PLIST', path), patch.object(service, 'running') as running, \
                        patch.object(service.subprocess, 'run') as run, \
                        patch('sys.argv', ['service.py', 'switch', '--window', '5h']):
                    running.return_value.returncode = 0 if loaded else 1
                    run.return_value.returncode = 0
                    service.main()
                    self.assertEqual(run.call_count, 2 if loaded else 0)
                    if loaded:
                        self.assertEqual(run.call_args_list[0].args[0], ['launchctl', 'bootout', service.TARGET])
                        self.assertEqual(run.call_args_list[1].args[0], ['launchctl', 'bootstrap', service.DOMAIN, str(path)])
                updated = plistlib.loads(path.read_bytes())
                self.assertEqual(service.configured_window(updated), '5h')
                service.set_window(updated, 'weekly')
                self.assertEqual(updated['ProgramArguments'].count('--window'), 1)
                self.assertEqual(service.configured_window(updated), 'weekly')
                args = updated['ProgramArguments']
                index = args.index('--window')
                del args[index:index + 2]
                self.assertEqual(updated, original)


class BluetoothTests(unittest.TestCase):
    @patch('tivoo.device.subprocess.run')
    def test_success_retains_retry_diagnostics_without_payloads(self, run):
        run.return_value.returncode = 0
        run.return_value.stderr = ('native BT_DIAG stage=open_result attempt=1/3 result=-1\n'
                                  'native BT_DIAG stage=retry wait=1s\n'
                                  'native BT_DIAG stage=open_result attempt=2/3 result=0\n'
                                  'TX: DE AD BE EF\nRX: 01 05 00 04 44 55 A2 00 02')
        with self.assertLogs('tivoo.bluetooth', level='INFO') as logs:
            self.assertEqual(send('AA:BB:CC:DD:EE:FF', b'\x44'), [bytes.fromhex('044455')])
        self.assertIn('attempt=2/3 result=0', '\n'.join(logs.output))
        self.assertNotIn('DE AD', '\n'.join(logs.output))
        self.assertEqual(run.call_count, 1)  # retries belong to the native process
        self.assertGreaterEqual(run.call_args.kwargs['timeout'], 60)

    @patch('tivoo.device.subprocess.run')
    def test_timeout_preserves_partial_diagnostics(self, run):
        run.side_effect = subprocess.TimeoutExpired('bridge', 60,
            stderr=b'BT_DIAG stage=open attempt=3/3\nTX: DE AD BE EF')
        with self.assertLogs('tivoo.bluetooth', level='INFO'):
            with self.assertRaisesRegex(RuntimeError, 'timed out.*attempt=3/3') as error:
                send('AA:BB:CC:DD:EE:FF', b'\x44')
        self.assertNotIn('DE AD', str(error.exception))

    @patch('tivoo.device.subprocess.run')
    def test_failure_does_not_dump_image_payloads(self, run):
        run.return_value.returncode = 2
        run.return_value.stderr = 'TX: DE AD BE EF\nRX: 01 02\nWrite failed'
        with self.assertRaisesRegex(RuntimeError, 'Write failed') as error:
            send('AA:BB:CC:DD:EE:FF', b'\x44')
        self.assertNotIn('DE AD', str(error.exception))

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

    @patch('tivoo.device.subprocess.run')
    def test_animation_uses_one_completion_ack_for_all_chunks(self, run):
        run.return_value.returncode = 0
        run.return_value.stderr = 'RX: 01 05 00 04 49 55 A7 00 02'
        self.assertEqual(len(send_session('AA:BB:CC:DD:EE:FF', [b'\x49\x00', b'\x49\x01'])), 1)
        args = run.call_args.args[0]
        self.assertIn('-s', args)
        self.assertEqual(args.count('--'), 1)
        run.return_value.stderr = 'RX: 01 05 00 04 49 55 A8 00 02'
        with self.assertRaisesRegex(RuntimeError, '0/1 received'):
            send_session('AA:BB:CC:DD:EE:FF', [b'\x49\x00', b'\x49\x01'])
        run.return_value.returncode = 2
        run.return_value.stderr = 'Write failed'
        with self.assertRaisesRegex(RuntimeError, 'Bluetooth failed'):
            send_session('AA:BB:CC:DD:EE:FF', [b'\x49\x00', b'\x49\x01'])


class AnimationTests(unittest.TestCase):
    def test_rainbow_fills_blanks_and_preserves_foreground(self):
        for stale in (False, True):
            original = render(92, stale, resets_at=1700000000)
            frames = rainbow_frames(original)
            self.assertEqual(len(frames), 16)
            self.assertEqual(len({frame.tobytes() for frame in frames}), 16)
            for frame in frames:
                for y in range(16):
                    for x in range(16):
                        before = original.getpixel((x, y))
                        after = frame.getpixel((x, y))
                        if before == (0, 0, 0):
                            self.assertGreater(max(after), 0)
                            self.assertLessEqual(max(after), 89)
                        else:
                            self.assertEqual(after, before)
                if stale:
                    self.assertEqual(frame.getpixel((15, 0)), (255, 120, 0))
            # Last phase advances into the first phase without a discontinuity.
            self.assertEqual(frames[-1].getpixel((1, 0)), frames[0].getpixel((0, 0)))

    def test_chunk_reassembly_and_frame_duration(self):
        frames = rainbow_frames(render(92))
        chunks = animation_payloads(frames, 100)
        data = b''.join(chunk[4:] for chunk in chunks)
        for index, chunk in enumerate(chunks):
            self.assertEqual(chunk[0], 0x49)
            self.assertEqual(chunk[3], index)
            self.assertEqual(int.from_bytes(chunk[1:3], 'little'), len(data))
            self.assertLessEqual(len(chunk[4:]), 200)
        pos = 0
        for expected in frames:
            self.assertEqual(data[pos], 0xaa)
            size = int.from_bytes(data[pos + 1:pos + 3], 'little')
            frame = bytearray(data[pos:pos + size])
            self.assertEqual(int.from_bytes(frame[3:5], 'little'), 100)
            frame[3:5] = b'\x00\x00'
            self.assertEqual(bytes(frame), image_payload(expected)[5:])
            pos += size
        self.assertEqual(pos, len(data))

    def test_invalid_animation_rejected(self):
        for frames, duration in [([], 100), ([render(92)], 0), ([render(92)], 65536),
                                 ([render(92)] * 1000, 100)]:
            with self.assertRaises(ValueError):
                animation_payloads(frames, duration)


if __name__ == '__main__':
    unittest.main()
