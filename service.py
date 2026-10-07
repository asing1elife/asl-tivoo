#!/usr/bin/env python3
"""Install/start/stop the per-user launchd service, without root privileges."""
import argparse
import os
import plistlib
import re
import subprocess
from pathlib import Path
from tivoo.quota import codex_binary

ROOT = Path(__file__).resolve().parent
LABEL = 'local.tivoo.codex-quota'
PLIST = Path.home() / 'Library' / 'LaunchAgents' / f'{LABEL}.plist'
DOMAIN = f'gui/{os.getuid()}'
TARGET = f'{DOMAIN}/{LABEL}'


def running():
    return subprocess.run(['launchctl', 'print', TARGET], capture_output=True, text=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['start', 'stop', 'status', 'uninstall'])
    parser.add_argument('--mac', default=os.environ.get('TIVOO_MAC'))
    args = parser.parse_args()
    if args.action == 'status':
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
    if not args.mac or not re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}', args.mac):
        parser.error('Provide --mac or TIVOO_MAC')
    if running().returncode == 0:
        print('Service already loaded; use stop then start to reconfigure')
        return
    output = ROOT / 'output'
    output.mkdir(exist_ok=True)
    config = {
        'Label': LABEL,
        'ProgramArguments': [str(ROOT / '.venv/bin/python'), '-m', 'tivoo', '--mac', args.mac, 'watch'],
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
    subprocess.run(['launchctl', 'bootstrap', DOMAIN, str(PLIST)], check=True)
    print(f'Started {LABEL}; logs: {output / "service.log"}')


if __name__ == '__main__':
    main()
