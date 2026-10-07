import argparse
import dataclasses
import fcntl
import json
import logging
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from .device import ROOT, send, send_session
from .display import FRAME_DURATION_MS, animation_payloads, rainbow_frames, render
from .quota import read_quota

LOG = logging.getLogger('tivoo')


def preview(image):
    output = ROOT / 'output'
    output.mkdir(exist_ok=True)
    frames = rainbow_frames(image)
    frames[0].save(output / 'screen.png')
    frames[0].resize((320, 320), resample=0).save(output / 'preview.png')
    enlarged = [frame.resize((320, 320), resample=0) for frame in frames]
    enlarged[0].save(output / 'preview.gif', save_all=True, append_images=enlarged[1:],
                     duration=FRAME_DURATION_MS, loop=0, optimize=False)
    return frames


def upload(mac, image):
    frames = preview(image)
    payloads = animation_payloads(frames)
    send_session(mac, payloads)
    LOG.info('Animation acknowledged: %d frames, %d chunks; device loops locally',
             len(frames), len(payloads))


def push(mac, remaining, stale=False, *, resets_at=None):
    image = render(remaining, stale, resets_at=resets_at)
    upload(mac, image)


def describe(quota):
    reset = datetime.fromtimestamp(quota.resets_at, ZoneInfo('Asia/Shanghai')).isoformat() if quota.resets_at else 'unknown'
    return f'Weekly remaining: {quota.remaining:g}%; reset: {reset}'


def watch(args):
    output = ROOT / 'output'
    output.mkdir(exist_ok=True)
    # Prevent duplicate foreground/background watchers.
    with (output / 'watch.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('A Tivoo watcher is already running') from None
        last_quota = None
        last_pixels = None
        last_send = 0
        while True:
            stale = False
            try:
                last_quota = read_quota(args.limit_id)
                LOG.info(describe(last_quota))
            except Exception as exc:
                stale = True
                LOG.warning('Quota read failed: %s', exc)
            remaining = last_quota.remaining if last_quota else None
            resets_at = last_quota.resets_at if last_quota else None
            # Never carry an old percentage across a reset when the fetch fails.
            if stale and last_quota and last_quota.resets_at and time.time() >= last_quota.resets_at:
                remaining = None
                resets_at = None
            image = render(remaining, stale, resets_at=resets_at)
            pixels = image.tobytes()
            try:
                # Periodic refresh repairs power cycles/manual display changes.
                if pixels != last_pixels or time.monotonic() - last_send >= 1800:
                    upload(args.mac, image)
                    last_pixels, last_send = pixels, time.monotonic()
                    LOG.info('Display acknowledged%s', ' (stale)' if stale else '')
            except Exception as exc:
                last_pixels = None
                LOG.warning('Display update failed; retry next cycle: %s', exc)
            time.sleep(args.interval)


def main():
    parser = argparse.ArgumentParser(description='Codex weekly remaining quota on Tivoo')
    parser.add_argument('--mac', default=os.environ.get('TIVOO_MAC'))
    parser.add_argument('--limit-id', default='codex')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status', help='Query screen state without changing the display')
    sub.add_parser('quota', help='Read weekly quota without Bluetooth')
    test = sub.add_parser('test', help='Send a sample percentage (not real quota)')
    test.add_argument('--remaining', type=float, default=88)
    once = sub.add_parser('once', help='Read real quota and update the screen once')
    once.add_argument('--preview-only', action='store_true')
    watcher = sub.add_parser('watch', help='Refresh every five minutes; Ctrl-C stops')
    watcher.add_argument('--interval', type=int, default=300)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    needs_mac = args.command not in ('quota',) and not getattr(args, 'preview_only', False)
    if needs_mac and not args.mac:
        parser.error('Set TIVOO_MAC or pass --mac')
    if args.command == 'watch' and args.interval < 30:
        parser.error('--interval must be at least 30 seconds')
    try:
        if args.command == 'status':
            print(json.dumps({'responses': [p.hex() for p in send(args.mac, b'\x46')]}))
        elif args.command == 'test':
            push(args.mac, args.remaining)
            print(f'Test image {args.remaining:g}% acknowledged (sample data)')
        elif args.command in ('quota', 'once'):
            quota = read_quota(args.limit_id)
            if args.command == 'quota':
                print(json.dumps(dataclasses.asdict(quota)))
            else:
                if args.preview_only:
                    preview(render(quota.remaining, resets_at=quota.resets_at))
                else:
                    push(args.mac, quota.remaining, resets_at=quota.resets_at)
                print(describe(quota))
        else:
            watch(args)
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        LOG.error('%s', exc)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
