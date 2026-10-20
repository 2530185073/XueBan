"""Run the Mac development database, API and worker in a private local workspace."""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import signal
import stat
import subprocess
import sys
import time
from urllib.parse import urlsplit

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parents[1]
sys.path.insert(0, str(BACKEND))

MODEL_CONFIG_NAMES = frozenset({
    "ARK_BASE_URL",
    "ARK_API_KEY",
    "ARK_MODEL",
    "ARK_FALLBACK_MODEL",
})

# Keep removing the retired review switch from inherited environments even
# though it is no longer accepted from the private model configuration. This
# prevents an old shell setting from reaching either API or Worker.
LEGACY_MODEL_ENVIRONMENT_NAMES = frozenset({"REVIEW_PROTOCOL"})


def _is_model_environment_name(name):
    return (name in MODEL_CONFIG_NAMES or name in LEGACY_MODEL_ENVIRONMENT_NAMES
            or name.startswith(("ARK_", "OPENAI_", "ZYAI_")))


def without_model_environment(environment):
    """Keep model credentials and routing variables inside the Worker only."""
    return {name: value for name, value in environment.items()
            if not _is_model_environment_name(name)}


def _read_model_environment(path):
    values = {}
    for raw_line in read_private(path).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        name = name.strip()
        if separator and name in MODEL_CONFIG_NAMES:
            values[name] = value.strip()
    return values


def write_private(path, value):
    with os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w") as file:
        file.write(value)


def read_private(path):
    mode = path.lstat().st_mode
    if not stat.S_ISREG(mode) or mode & 0o077:
        raise ValueError("Configuration must be a private regular file (0600)")
    return path.read_text()


def load(runtime, *, require_model=False):
    config = json.loads(read_private(runtime / "runtime.json"))
    env = os.environ.copy()
    env.update(config["environment"])
    env = without_model_environment(env)
    env["PYTHONPATH"] = str(BACKEND)
    web_dist = REPO / "apps/student-web/dist"
    if (web_dist / "index.html").is_file():
        env.setdefault("STUDENT_WEB_DIST", str(web_dist))
    if require_model:
        # Shutdown, status and revocation remain usable when model credentials are removed.
        env.update(_read_model_environment(Path(config["model_config"])))
        if not env.get("ARK_BASE_URL") or not env.get("ARK_API_KEY"):
            raise ValueError("Model configuration is incomplete")
        if env.get("CORRECTION_MODE") in {"multimodel", "two_model"}:
            if config.get("gateway_config"):
                for line in read_private(Path(config["gateway_config"])).splitlines():
                    name, separator, value = line.partition("=")
                    if separator and name in {"ZYAI_BASE_URL", "ZYAI_API_KEY"}:
                        env[name] = value.strip()
            if not env.get("ZYAI_BASE_URL") or not env.get("ZYAI_API_KEY"):
                raise ValueError("Multimodel gateway configuration is incomplete")
    return config, env


def configure_models(runtime, *, mode, gateway_config=None, readback_model="kimi-k3", review_model="gpt-6-astra"):
    """Keep secrets in their existing private file; persist only explicit paths and switches."""
    config, _ = load(runtime)
    if mode not in {"single", "multimodel", "two_model"}:
        raise ValueError("Unknown correction mode")
    for model in (readback_model, review_model):
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", model):
            raise ValueError("Invalid model identifier")
    if gateway_config is not None:
        path = gateway_config.expanduser().resolve()
        read_private(path)
        config["gateway_config"] = str(path)
    if mode in {"multimodel", "two_model"}:
        path = config.get("gateway_config")
        if not path:
            raise ValueError("An explicit private gateway configuration is required")
        from scripts.vision_benchmark import read_config
        read_config(Path(path))
    config["environment"].update(CORRECTION_MODE=mode, READBACK_MODEL=readback_model, REVIEW_MODEL=review_model)
    if mode in {"multimodel", "two_model"}:
        config["environment"]["WORKER_LEASE_SECONDS"] = str(max(600, int(config["environment"].get("WORKER_LEASE_SECONDS", "300"))))
    temp = runtime / ("runtime-" + secrets.token_hex(8) + ".tmp")
    try:
        write_private(temp, json.dumps(config, indent=2))
        os.replace(temp, runtime / "runtime.json")
    finally:
        temp.unlink(missing_ok=True)


def api_environment(env):
    """Only the worker needs external model credentials."""
    return without_model_environment(env)


def run(args, *, env=None, log=None):
    env = without_model_environment(os.environ if env is None else env)
    if log:
        with log.open("ab") as output:
            result = subprocess.run(args, cwd=REPO, env=env, stdout=output, stderr=subprocess.STDOUT)
    else:
        result = subprocess.run(args, cwd=REPO, env=env)
    if result.returncode:
        raise RuntimeError("Local command failed; inspect the private runtime log")


def pg_args(runtime, config, command):
    return [str(Path(config["pg_bin"]) / "pg_ctl"), "-D", str(runtime / "postgres"), command]


def initialize(args):
    from xueban_backend.admin import initialize_issuer
    runtime = args.runtime.expanduser().resolve()
    if runtime == REPO or REPO in runtime.parents:
        raise ValueError("Runtime data must stay outside the repository")
    pg = args.pg_bin.expanduser().resolve()
    for binary in ("initdb", "pg_ctl", "createdb"):
        if not (pg / binary).is_file():
            raise ValueError("PostgreSQL binaries are missing")
    model = args.model_config.expanduser().resolve()
    read_private(model)
    base = args.base_url.rstrip("/")
    parts = urlsplit(base)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.path or parts.username or parts.query or parts.fragment:
        raise ValueError("A plain API origin is required")
    runtime.mkdir(parents=True, exist_ok=False, mode=0o700)
    (runtime / "assets").mkdir(mode=0o700)
    (runtime / "logs").mkdir(mode=0o700)
    initialize_issuer(runtime / "identity")
    password = secrets.token_urlsafe(32)
    write_private(runtime / "postgres-password", password)
    config = {"pg_bin": str(pg), "pg_port": args.pg_port, "api_port": parts.port or (443 if parts.scheme == "https" else 80),
              "host": args.host, "model_config": str(model), "environment": {
        "DATABASE_URL": f"postgresql+psycopg://xueban_local:{password}@127.0.0.1:{args.pg_port}/xueban_local",
        "OIDC_ISSUER": "https://identity.xueban.local", "OIDC_AUDIENCE": "xueban-local-api",
        "JWT_PUBLIC_KEY_FILE": str(runtime / "identity/issuer-public.pem"),
        "SIGNING_PRIVATE_KEY_FILE": str(runtime / "identity/issuer-private.pem"),
        "DEVICE_SESSION_TTL_SECONDS": "28800", "PUBLIC_BASE_URL": base,
        "ASSET_STORAGE_DIR": str(runtime / "assets"), "WORKER_LEASE_SECONDS": "300",
        "CORS_ALLOWED_ORIGINS": "http://127.0.0.1:5173,http://localhost:5173",
    }}
    write_private(runtime / "runtime.json", json.dumps(config, indent=2))
    run([str(pg / "initdb"), "-D", str(runtime / "postgres"), "--username=xueban_local", "--auth=scram-sha-256",
         "--pwfile=" + str(runtime / "postgres-password"), "--encoding=UTF8", "--locale=C"], log=runtime / "logs/initdb.log")
    start_postgres(runtime, config)
    env = os.environ.copy()
    env["PGPASSWORD"] = password
    run([str(pg / "createdb"), "-h", "127.0.0.1", "-p", str(args.pg_port), "-U", "xueban_local", "xueban_local"], env=env)
    print("Private local runtime initialized:", runtime)


def start_postgres(runtime, config):
    status = subprocess.run(pg_args(runtime, config, "status"), env=without_model_environment(os.environ),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if status.returncode:
        run(pg_args(runtime, config, "start") + ["-l", str(runtime / "logs/postgres.log"), "-o",
            f"-p {config['pg_port']} -h 127.0.0.1 -k {shlex.quote(str(runtime))}"], log=runtime / "logs/postgres-control.log")


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def spawn(runtime, name, args, env):
    pidfile = runtime / (name + ".pid")
    if pidfile.exists() and alive(int(pidfile.read_text())):
        command = subprocess.check_output(["ps", "-p", pidfile.read_text().strip(), "-o", "command="],
                                          env=without_model_environment(os.environ), text=True)
        expected = "xueban_backend.worker" if name == "worker" else "xueban_backend.app:create_app"
        if expected not in command:
            raise RuntimeError("PID was reused; refusing to reuse another process")
        print(name, "already running; stop/start is required after code or configuration changes")
        return
    with (runtime / "logs" / (name + ".log")).open("ab") as output:
        process = subprocess.Popen(args, cwd=REPO, env=env, stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
    pidfile.write_text(str(process.pid))
    pidfile.chmod(0o600)
    time.sleep(0.5)
    if process.poll() is not None:
        raise RuntimeError(f"{name} exited; inspect its private runtime log")


def wait_ready(runtime, config):
    import httpx
    deadline = time.monotonic() + 15
    ready_since = None
    with httpx.Client(timeout=1, follow_redirects=False, trust_env=False) as client:
        while time.monotonic() < deadline:
            if not all(alive(int((runtime / (name + ".pid")).read_text())) for name in ("api", "worker")):
                raise RuntimeError("A local process exited during startup; inspect the private runtime log")
            try:
                response = client.get(f"http://127.0.0.1:{config['api_port']}/v1/me")
                ready = response.status_code == 401 and bool(response.headers.get("X-Request-ID"))
            except httpx.HTTPError:
                ready = False
            if ready:
                ready_since = ready_since or time.monotonic()
                if time.monotonic() - ready_since >= 1:
                    return
            else:
                ready_since = None
            time.sleep(0.2)
    raise RuntimeError("API readiness was not verified; inspect the private runtime log")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, default=Path.home() / ".local/share/xueban/local-dev")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--pg-bin", type=Path, default=Path("/opt/homebrew/opt/postgresql@17/bin"))
    init.add_argument("--model-config", type=Path, required=True)
    init.add_argument("--base-url", default="http://127.0.0.1:8000")
    init.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1", "0.0.0.0"])
    init.add_argument("--pg-port", type=int, default=55432)
    for command in ("start", "stop", "status", "migrate"):
        commands.add_parser(command)
    models = commands.add_parser("configure-models")
    models.add_argument("--mode", choices=("single", "multimodel", "two_model"), required=True)
    models.add_argument("--gateway-config", type=Path)
    models.add_argument("--readback-model", default="kimi-k3")
    models.add_argument("--review-model", default="gpt-6-astra")
    admin = commands.add_parser("admin")
    admin.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    runtime = args.runtime.expanduser().resolve()
    if args.command == "init":
        initialize(args)
        return
    if args.command == "configure-models":
        configure_models(runtime, mode=args.mode, gateway_config=args.gateway_config,
                         readback_model=args.readback_model, review_model=args.review_model)
        print("Correction configuration saved; restart the worker to apply:", args.mode)
        return
    config, env = load(runtime, require_model=args.command == "start")
    if args.command in {"start", "migrate", "admin"}:
        start_postgres(runtime, config)
    if args.command in {"start", "migrate"}:
        run([sys.executable, "-m", "alembic", "-c", str(BACKEND / "alembic.ini"), "upgrade", "head"], env=env)
    if args.command == "start":
        api_env = api_environment(env)
        spawn(runtime, "api", [sys.executable, "-m", "uvicorn", "xueban_backend.app:create_app", "--factory", "--host",
              config["host"], "--port", str(config["api_port"]), "--no-access-log"], api_env)
        spawn(runtime, "worker", [sys.executable, "-m", "xueban_backend.worker"], env)
        wait_ready(runtime, config)
        print("Local API ready and worker running:", config["environment"]["PUBLIC_BASE_URL"])
    elif args.command == "admin":
        run([sys.executable, "-m", "xueban_backend.admin", *args.arguments], env=env)
    elif args.command == "stop":
        for name in ("worker", "api"):
            pidfile = runtime / (name + ".pid")
            if pidfile.exists():
                pid = int(pidfile.read_text())
                if alive(pid):
                    command = subprocess.check_output(["ps", "-p", str(pid), "-o", "command="],
                                                      env=without_model_environment(os.environ), text=True)
                    if ("xueban_backend.worker" if name == "worker" else "xueban_backend.app:create_app") not in command:
                        raise RuntimeError("PID was reused; refusing to stop another process")
                    os.kill(pid, signal.SIGTERM)
                    deadline = time.monotonic() + 15
                    while alive(pid) and time.monotonic() < deadline:
                        time.sleep(0.2)
                    if alive(pid):
                        raise RuntimeError(f"{name} has not stopped; PID retained and database left running")
                pidfile.unlink()
        run(pg_args(runtime, config, "stop") + ["-m", "fast"], log=runtime / "logs/postgres-control.log")
        print("Local processes stopped; stored data retained.")
    elif args.command == "status":
        for name in ("api", "worker"):
            pidfile = runtime / (name + ".pid")
            print(name, "running" if pidfile.exists() and alive(int(pidfile.read_text())) else "stopped")
        status = subprocess.run(pg_args(runtime, config, "status"), env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("postgres", "running" if status.returncode == 0 else "stopped")


if __name__ == "__main__":
    main()
