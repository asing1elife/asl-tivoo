import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(sys.platform == 'darwin', 'IOBluetooth requires macOS')
class NativeConnectionTests(unittest.TestCase):
    def test_connection_recovery_with_fake_devices(self):
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as directory:
            binary = str(Path(directory) / 'connection_test')
            compiled = subprocess.run([
                'clang', '-Wall', '-Werror', '-fobjc-arc', '-framework', 'Foundation',
                '-framework', 'IOBluetooth', '-o', binary,
                str(root / 'tests/native_connection_test.m'),
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([binary], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Native connection scenarios passed', result.stdout)
