"""Prepare credentials, then optionally configure an already installed WSL."""
import argparse
import os
from pathlib import Path
import secrets
import subprocess
import sys
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', action='store_true')
    parser.add_argument('--distribution', default='Ubuntu')
    args = parser.parse_args()
    private = ROOT / 'data/redis-private'
    private.mkdir(parents=True, exist_ok=True)
    application = private / 'application.env'
    password_file = private / 'password.txt'
    if not password_file.exists():
        password_file.write_text(secrets.token_urlsafe(36), encoding='ascii')
    password = password_file.read_text(encoding='ascii').strip()
    if not application.exists():
        application.write_text('GAMEPLAN_REDIS_URL=redis://:'+quote(password)+'@127.0.0.1:6379/0\nGAMEPLAN_REDIS_PREFIX=gameplan:v1\n', encoding='utf-8')
    print('Redis credentials prepared privately; application .env is unchanged.')
    if not args.install:
        print('After WSL/Ubuntu installation, rerun with --install.')
        return
    base = ['wsl.exe', '-d', args.distribution, '-u', 'root', '--']
    subprocess.run(base+['true'], check=True)
    subprocess.run(base+['bash', '-c', 'umask 077; mkdir -p /etc/redis; cat > /etc/redis/gameplan-password.conf'],
                   input=('requirepass '+password+'\n').encode('ascii'), check=True)
    converted = subprocess.check_output(base+['wslpath', '-u', str(ROOT / 'deploy/redis/install-wsl.sh')], text=True).strip()
    subprocess.run(base+['bash', converted], check=True)
    print('Redis installed. Import a fresh source snapshot before enabling it.')


if __name__ == '__main__':
    main()
