"""Build and open the local student website; model secrets never enter its build."""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.parse import urlsplit
import webbrowser

import httpx

if __package__:
    from .mac_local import BACKEND, REPO, alive, load, spawn, wait_ready, without_model_environment
else:
    from mac_local import BACKEND, REPO, alive, load, spawn, wait_ready, without_model_environment


def local_origin(value):
    origin = value.rstrip('/')
    parts = urlsplit(origin)
    if (parts.scheme not in {'http', 'https'} or parts.hostname not in {'127.0.0.1', 'localhost', '::1'}
            or parts.username or parts.password or parts.path or parts.query or parts.fragment):
        raise ValueError('The local browser launcher requires a loopback API origin')
    return origin


def ready(origin):
    try:
        with httpx.Client(timeout=3, trust_env=False, follow_redirects=False) as client:
            response = client.get(origin + '/learn/')
            return response.status_code == 200 and response.headers.get('X-Xueban-Web') == 'student-v2-accounts'
    except httpx.HTTPError:
        return False


def restart_api(runtime, config, env):
    # Enable newly built web assets without stopping PostgreSQL or in-flight model jobs.
    pidfile = runtime / 'api.pid'
    if pidfile.exists():
        pid = int(pidfile.read_text())
        if alive(pid):
            command = subprocess.check_output(['ps', '-p', str(pid), '-o', 'command='],
                                              env=without_model_environment(os.environ), text=True)
            if 'xueban_backend.app:create_app' not in command:
                raise RuntimeError('PID belongs to another process; refusing to stop it')
            os.kill(pid, signal.SIGTERM)
            deadline = time.monotonic() + 15
            while alive(pid) and time.monotonic() < deadline:
                time.sleep(.2)
            if alive(pid):
                raise RuntimeError('API has not stopped; PID file retained')
        pidfile.unlink()
    api_env = without_model_environment(env)
    spawn(runtime, 'api', [sys.executable, '-m', 'uvicorn', 'xueban_backend.app:create_app', '--factory',
          '--host', config['host'], '--port', str(config['api_port']), '--no-access-log'], api_env)
    wait_ready(runtime, config)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, default=Path.home() / '.local/share/xueban/local-dev')
    parser.add_argument('--no-open', action='store_true', help='Start the website without opening a browser')
    args = parser.parse_args()
    runtime = args.runtime.expanduser().resolve()
    config, _ = load(runtime)
    origin = local_origin(config['environment']['PUBLIC_BASE_URL'])
    if config['host'] not in {'127.0.0.1', 'localhost', '::1'}:
        raise ValueError('The local launcher requires a loopback-bound API')
    web = REPO / 'apps/student-web'
    # Read model configuration only in the backend launcher, never pass it to npm.
    build_env = {k: v for k, v in without_model_environment(os.environ).items()
                 if not k.startswith(('DATABASE_', 'JWT_', 'SIGNING_'))}
    if not (web / 'node_modules').is_dir():
        subprocess.run(['npm', 'ci'], cwd=web, env=build_env, check=True)
    subprocess.run(['npm', 'run', 'build'], cwd=web, env=build_env, check=True)
    subprocess.run([sys.executable, str(BACKEND / 'scripts/mac_local.py'), '--runtime', str(runtime), 'start'],
                   env=without_model_environment(os.environ), check=True)
    config, env = load(runtime)
    if not ready(origin):
        restart_api(runtime, config, env)
    if not ready(origin):
        raise RuntimeError('Student web is not ready')
    # Startup never signs in, selects a child, or issues an identity credential.
    launch = origin + '/learn/'
    if not args.no_open:
        if not webbrowser.open(launch):
            raise RuntimeError('Browser did not open; open the local /learn/ address manually')
    print('Student web ready: ' + origin + '/learn/')
    print('Sign in with your own learner or administrator account. Startup does not auto-login.')


if __name__ == '__main__':
    main()
