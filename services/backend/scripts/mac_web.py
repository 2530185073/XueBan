"""Build and open the local student website; model secrets never enter its build."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.parse import urlencode, urlsplit
import webbrowser

import httpx

if __package__:
    from .mac_local import BACKEND, REPO, alive, load, read_private, spawn, wait_ready, write_private
else:
    from mac_local import BACKEND, REPO, alive, load, read_private, spawn, wait_ready, write_private


def local_origin(value):
    origin = value.rstrip('/')
    parts = urlsplit(origin)
    if (parts.scheme not in {'http', 'https'} or parts.hostname not in {'127.0.0.1', 'localhost', '::1'}
            or parts.username or parts.password or parts.path or parts.query or parts.fragment):
        raise ValueError('Automatic browser pairing is only available on a loopback API origin')
    return origin


def ready(origin):
    try:
        with httpx.Client(timeout=3, trust_env=False, follow_redirects=False) as client:
            response = client.get(origin + '/learn/')
            return response.status_code == 200 and response.headers.get('X-Xueban-Web') == 'student-v1'
    except httpx.HTTPError:
        return False


def restart_api(runtime, config, env):
    # Enable newly built web assets without stopping PostgreSQL or in-flight model jobs.
    pidfile = runtime / 'api.pid'
    if pidfile.exists():
        pid = int(pidfile.read_text())
        if alive(pid):
            command = subprocess.check_output(['ps', '-p', str(pid), '-o', 'command='], text=True)
            if 'xueban_backend.app:create_app' not in command:
                raise RuntimeError('PID belongs to another process; refusing to stop it')
            os.kill(pid, signal.SIGTERM)
            deadline = time.monotonic() + 15
            while alive(pid) and time.monotonic() < deadline:
                time.sleep(.2)
            if alive(pid):
                raise RuntimeError('API has not stopped; PID file retained')
        pidfile.unlink()
    api_env = {key: value for key, value in env.items() if not key.startswith('ARK_')}
    spawn(runtime, 'api', [sys.executable, '-m', 'uvicorn', 'xueban_backend.app:create_app', '--factory',
          '--host', config['host'], '--port', str(config['api_port']), '--no-access-log'], api_env)
    wait_ready(runtime, config)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, default=Path.home() / '.local/share/xueban/local-dev')
    parser.add_argument('--principal-id', default='tablet_actor', help='Existing locally authorized demo identity')
    parser.add_argument('--no-open', action='store_true', help='Save a private launch link without opening a browser')
    args = parser.parse_args()
    runtime = args.runtime.expanduser().resolve()
    config, _ = load(runtime)
    origin = local_origin(config['environment']['PUBLIC_BASE_URL'])
    if config['host'] not in {'127.0.0.1', 'localhost', '::1'}:
        raise ValueError('Automatic pairing requires a loopback-bound local API')
    web = REPO / 'apps/student-web'
    # Read model configuration only in the backend launcher, never pass it to npm.
    build_env = {k: v for k, v in os.environ.items() if not k.startswith(('ARK_', 'OPENAI_', 'ZYAI_', 'DATABASE_', 'JWT_', 'SIGNING_'))}
    if not (web / 'node_modules').is_dir():
        subprocess.run(['npm', 'ci'], cwd=web, env=build_env, check=True)
    subprocess.run(['npm', 'run', 'build'], cwd=web, env=build_env, check=True)
    subprocess.run([sys.executable, str(BACKEND / 'scripts/mac_local.py'), '--runtime', str(runtime), 'start'], check=True)
    config, env = load(runtime)
    if not ready(origin):
        restart_api(runtime, config, env)
    if not ready(origin):
        raise RuntimeError('Student web is not ready; no pairing credential was issued')

    # Administrator-only CLI issues the same short-lived, single-use device grant as Android.
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    pair_file = runtime / ('web-pair-' + stamp + '.json')
    subprocess.run([sys.executable, str(BACKEND / 'scripts/mac_local.py'), '--runtime', str(runtime), 'admin',
                    'pair', '--principal-id', args.principal_id, '--output', str(pair_file), '--ttl', '900'], check=True)
    code = json.loads(read_private(pair_file))['pairing_code']
    launch = origin + '/learn/#' + urlencode({'pair': code})
    launch_file = runtime / ('web-launch-' + stamp + '.json')
    write_private(launch_file, json.dumps({'url': launch, 'pair_file': str(pair_file)}, ensure_ascii=False))
    if not args.no_open:
        if not webbrowser.open(launch):
            raise RuntimeError('Browser did not open; the short-lived launch link is in the private runtime')
    print('Student web ready: ' + origin + '/learn/')
    print('Private launch file: ' + str(launch_file))
    print('Pairing uses an existing authorized identity; no model key is present in the URL or browser.')


if __name__ == '__main__':
    main()
