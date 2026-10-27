#!/usr/bin/env python3
"""Install/start/stop the per-user launchd service, without root privileges."""
import argparse
import os
import plistlib
import re
import subprocess
import sys
import time
from pathlib import Path
from tivoo.quota import codex_binary

ROOT = Path(__file__).resolve().parent
LABEL = 'local.tivoo.codex-quota'
PLIST = Path.home() / 'Library' / 'LaunchAgents' / f'{LABEL}.plist'
DOMAIN = f'gui/{os.getuid()}'
TARGET = f'{DOMAIN}/{LABEL}'


def running():
    return subprocess.run(['launchctl', 'print', TARGET], capture_output=True, text=True)


def bootstrap():
    # bootout can return before launchd has finished tearing down the old job.
    # Only retry error 5; invalid configurations must still fail promptly.
    for delay in (0, 0.25, 0.5, 1, 2, 3):
        if delay:
            time.sleep(delay)
        result = subprocess.run(['launchctl', 'bootstrap', DOMAIN, str(PLIST)],
                                capture_output=True, text=True)
        if result.returncode == 0:
            return
        if result.returncode != 5:
            break
    detail = result.stderr.strip() or result.stdout.strip() or 'No diagnostics'
    raise RuntimeError(f'加载后台服务失败（exit {result.returncode}）：{detail}')


def save_window(config, window, loaded):
    previous = PLIST.read_bytes()
    set_window(config, window)
    PLIST.write_bytes(plistlib.dumps(config))
    if not loaded:
        return
    unloaded = False
    try:
        subprocess.run(['launchctl', 'bootout', TARGET], check=True, capture_output=True, text=True)
        unloaded = True
        bootstrap()
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        PLIST.write_bytes(previous)
        if unloaded:
            try:
                bootstrap()
            except RuntimeError as recovery:
                raise RuntimeError(f'切换失败，已恢复原配置，但服务未恢复：{recovery}；切换错误：{exc}') from None
        raise RuntimeError(f'切换失败，已恢复原配置和服务状态：{exc}') from None


def configured_window(config):
    arguments = config.get('ProgramArguments', [])
    if '--window' in arguments:
        return arguments[arguments.index('--window') + 1]
    return 'weekly'


def set_window(config, window):
    arguments = list(config['ProgramArguments'])
    if '--window' in arguments:
        arguments[arguments.index('--window') + 1] = window
    else:
        arguments.insert(arguments.index('watch'), '--window')
        arguments.insert(arguments.index('watch'), window)
    config['ProgramArguments'] = arguments


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['start', 'stop', 'status', 'uninstall', 'switch'])
    parser.add_argument('--mac', default=os.environ.get('TIVOO_MAC'))
    parser.add_argument('--window', choices=['weekly', '5h'])
    args = parser.parse_args()
    if args.action == 'status':
        if PLIST.exists():
            config = plistlib.loads(PLIST.read_bytes())
            print(f'Usage window: {configured_window(config)}', flush=True)
        result = running()
        print(result.stdout or 'Service is not loaded')
        return
    if args.action in ('stop', 'uninstall'):
        if running().returncode == 0:
            subprocess.run(['launchctl', 'bootout', TARGET], check=True)
        if args.action == 'uninstall':
            PLIST.unlink(missing_ok=True)
        print('Service stopped' if args.action == 'stop' else 'Service removed')
        return
    if args.action == 'switch':
        if not args.window:
            parser.error('switch requires --window weekly or --window 5h')
        if not PLIST.exists():
            parser.error('Service is not installed; first use start --mac ADDRESS --window MODE')
        config = plistlib.loads(PLIST.read_bytes())
        loaded = running().returncode == 0
        save_window(config, args.window, loaded)
        print(f'Usage window: {args.window}; ' + ('service restarted' if loaded else 'saved for next start'))
        return
    if PLIST.exists() and not args.mac:
        config = plistlib.loads(PLIST.read_bytes())
        if running().returncode == 0:
            print('Service already loaded; use switch --window to change the usage window')
            return
        if args.window:
            set_window(config, args.window)
            PLIST.write_bytes(plistlib.dumps(config))
        bootstrap()
        print(f'Started {LABEL}; usage window: {configured_window(config)}')
        return
    if not args.mac or not re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}', args.mac):
        parser.error('Provide --mac or TIVOO_MAC')
    if running().returncode == 0:
        print('Service already loaded; use switch --window to change the usage window')
        return
    output = ROOT / 'output'
    output.mkdir(exist_ok=True)
    window = args.window or (configured_window(plistlib.loads(PLIST.read_bytes())) if PLIST.exists() else 'weekly')
    config = {
        'Label': LABEL,
        'ProgramArguments': [str(ROOT / '.venv/bin/python'), '-m', 'tivoo', '--mac', args.mac,
                             '--window', window, 'watch'],
        'WorkingDirectory': str(ROOT),
        'EnvironmentVariables': {'PYTHONUNBUFFERED': '1', 'CODEX_BIN': codex_binary(),
                                 'PATH': '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'},
        'RunAtLoad': True,
        'KeepAlive': True,
        'ThrottleInterval': 30,
        'StandardOutPath': str(output / 'service.log'),
        'StandardErrorPath': str(output / 'service.log'),
    }
    if os.environ.get('CODEX_HOME'):
        config['EnvironmentVariables']['CODEX_HOME'] = os.environ['CODEX_HOME']
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    PLIST.write_bytes(plistlib.dumps(config))
    bootstrap()
    print(f'Started {LABEL}; logs: {output / "service.log"}')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f'错误：{exc}', file=sys.stderr)
        raise SystemExit(1)
