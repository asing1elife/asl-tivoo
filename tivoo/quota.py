"""Read-only Codex app-server client. No inference and no direct token access."""
import json
import math
import os
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Quota:
    remaining: float
    resets_at: int | None
    limit_id: str
    window: str = 'weekly'


WINDOW_MINUTES = {'weekly': 10080, '5h': 300}

# Keep the original public name available for existing callers.
WeeklyQuota = Quota


def select_quota(result: dict, limit_id: str = 'codex', window: str = 'weekly') -> Quota:
    if window not in WINDOW_MINUTES:
        raise ValueError(f'Unknown quota window: {window}')
    buckets = result.get('rateLimitsByLimitId')
    if buckets:
        bucket = buckets.get(limit_id)
    else:
        bucket = result.get('rateLimits')
        if bucket and bucket.get('limitId') not in (None, limit_id):
            bucket = None
    if not bucket:
        raise ValueError(f'No quota bucket: {limit_id}')
    windows = [bucket.get(key) for key in ('primary', 'secondary')]
    minutes = WINDOW_MINUTES[window]
    matching = [w for w in windows if w and w.get('windowDurationMins') == minutes]
    if len(matching) != 1:
        raise ValueError(f'Expected one explicit {minutes}-minute {window} window')
    selected = matching[0]
    used = selected.get('usedPercent')
    if isinstance(used, bool) or not isinstance(used, (int, float)) or not math.isfinite(used):
        raise ValueError(f'{window} usedPercent is unavailable')
    reset = selected.get('resetsAt')
    if reset is not None and (isinstance(reset, bool) or not isinstance(reset, int)):
        raise ValueError(f'Invalid {window} reset timestamp')
    return Quota(max(0, min(100, 100 - used)), reset, limit_id, window)


def select_weekly(result: dict, limit_id: str = 'codex') -> Quota:
    return select_quota(result, limit_id)


def codex_binary() -> str:
    override = os.environ.get('CODEX_BIN')
    if override:
        return override
    found = shutil.which('codex')
    if found:
        return found
    for app in ('ChatGPT', 'Codex'):
        path = Path(f'/Applications/{app}.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex')
        if path.is_file():
            return str(path)
    raise RuntimeError('Codex CLI not found; set CODEX_BIN')


class AppServer:
    def __init__(self, timeout=25):
        self.timeout = timeout
        self.sequence = 0
        self.messages = queue.Queue()
        self.proc = None

    def __enter__(self):
        self.proc = subprocess.Popen(
            [codex_binary(), 'app-server', '--listen', 'stdio://'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1,
        )
        threading.Thread(target=self._read, daemon=True).start()
        try:
            self.call('initialize', {'clientInfo': {'name': 'tivoo_quota', 'title': 'Tivoo Quota', 'version': '0.1.0'}})
            self._send({'method': 'initialized'})
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def _read(self):
        for line in self.proc.stdout:
            try:
                self.messages.put(json.loads(line))
            except json.JSONDecodeError:
                continue
        self.messages.put(None)

    def _send(self, message):
        self.proc.stdin.write(json.dumps(message) + '\n')
        self.proc.stdin.flush()

    def call(self, method, params=None):
        self.sequence += 1
        request_id = self.sequence
        request = {'id': request_id, 'method': method}
        if params is not None:
            request['params'] = params
        self._send(request)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                message = self.messages.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty:
                raise TimeoutError(f'Codex timed out: {method}') from None
            if message is None:
                raise RuntimeError('Codex app-server closed unexpectedly')
            if message.get('id') == request_id and 'method' not in message:
                if 'error' in message:
                    raise RuntimeError(f"Codex {method}: {message['error'].get('message', 'request failed')}")
                return message['result']
            if 'method' in message and 'id' in message:
                self._send({'id': message['id'], 'error': {'code': -32601, 'message': 'Read-only client does not support server requests'}})

    def __exit__(self, *args):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
            self.proc.stdin.close()
            self.proc.stdout.close()


def read_quota(limit_id='codex', window='weekly'):
    with AppServer() as server:
        return select_quota(server.call('account/rateLimits/read'), limit_id, window)
