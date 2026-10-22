"""Supervise the local Mac runtime with per-user launchd agents (no root, no system settings).

Three agents run in the foreground so launchd can restart them: PostgreSQL, the API and the
Worker. A plist never contains a secret. launchd starts ``mac_service.py exec <name>``, which
reads the private configuration at start (exactly as ``mac_local.load`` does) and replaces
itself with the real process. Model credentials therefore reach the Worker only, are never
written to a plist, and never reach the API process.

    mac_service.py install           # write plists and load them (stops hand-started processes)
    mac_service.py uninstall         # unload and remove plists (data untouched)
    mac_service.py status            # processes, readiness, queues, disk, last backup (exit 1 on problems)
    mac_service.py backup            # quiesced backup with the existing recovery tooling
"""
import argparse
import json
import os
import plistlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

if __package__:
    from .mac_local import BACKEND, REPO, alive, load, without_model_environment
else:
    from mac_local import BACKEND, REPO, alive, load, without_model_environment

LABEL_PREFIX = os.environ.get("XUEBAN_SERVICE_LABEL_PREFIX", "local.xueban")
AGENTS = ("postgres", "api", "worker")
AGENT_DIR = Path(os.environ.get("XUEBAN_SERVICE_AGENT_DIR", Path.home() / "Library/LaunchAgents"))
DEFAULT_RUNTIME = Path.home() / ".local/share/xueban/local-dev"
BASE_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
LOCALE = {"LC_ALL": "en_US.UTF-8", "LANG": "en_US.UTF-8"}  # a bare launchd environment has none
WRITER_PIDS = ("api.pid", "worker.pid")  # recovery.py's quiesce check reads these


def label(name):
    return f"{LABEL_PREFIX}.{name}"


def plist_path(name, agent_dir=AGENT_DIR):
    return Path(agent_dir) / f"{label(name)}.plist"


def build_programs(runtime, config):
    """Foreground command lines. Always exec-form; never a shell string."""
    postgres = [str(Path(config["pg_bin"]) / "postgres"), "-D", str(runtime / "postgres"),
                "-p", str(config["pg_port"]), "-h", "127.0.0.1", "-k", str(runtime)]
    api = [sys.executable, "-m", "uvicorn", "xueban_backend.app:create_app", "--factory", "--host", config["host"],
           "--port", str(config["api_port"]), "--no-access-log"]
    worker = [sys.executable, "-m", "xueban_backend.worker"]
    return {"postgres": postgres, "api": api, "worker": worker}


def process_environment(runtime, name):
    """Full environment for one process, read at start. Only the Worker gets model secrets."""
    if name == "postgres":
        return {"PATH": BASE_PATH, "HOME": str(Path.home()), **LOCALE}
    _, env = load(runtime, require_model=name == "worker")
    if name == "api":
        env = without_model_environment(env)
    env["PATH"] = BASE_PATH
    env.update(LOCALE)
    return env


def render_plist(runtime, name, log_dir, python=None):
    """A launchd job that holds no configuration or secret; it only points at the exec wrapper."""
    log = Path(log_dir) / f"{name}.service.log"
    payload = {
        "Label": label(name),
        "ProgramArguments": [str(python or sys.executable), str(Path(__file__).resolve()),
                             "--runtime", str(runtime), "exec", name],
        "WorkingDirectory": str(REPO),
        "EnvironmentVariables": {"PATH": BASE_PATH, **LOCALE},
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "ProcessType": "Background",
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
        "Umask": 0o077,
    }
    return plistlib.dumps(payload)


def write_plists(runtime, agent_dir=AGENT_DIR):
    runtime = Path(runtime)
    agent_dir = Path(agent_dir)
    agent_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name in AGENTS:
        target = plist_path(name, agent_dir)
        temporary = target.with_suffix(".tmp")
        with os.fdopen(os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600), "wb") as file:
            file.write(render_plist(runtime, name, runtime / "logs"))
        os.replace(temporary, target)
        written.append(str(target))
    return written


def exec_agent(runtime, name):
    """Replace this process with the real service. Keeps the pid file current for the quiesce check."""
    runtime = Path(runtime)
    config, _ = load(runtime)
    program = build_programs(runtime, config)[name]
    environment = process_environment(runtime, name)
    if name in ("api", "worker"):
        pidfile = runtime / f"{name}.pid"
        pidfile.write_text(str(os.getpid()))  # exec keeps the pid
        pidfile.chmod(0o600)
    os.chdir(REPO)
    os.execve(program[0], program, environment)


def launchctl(*args, check=True):
    result = subprocess.run(["launchctl", *args], capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError("launchctl " + args[0] + " failed (" + str(result.returncode) + ")")
    return result


def domain():
    return f"gui/{os.getuid()}"


def install(runtime):
    runtime = Path(runtime)
    subprocess.run([sys.executable, str(BACKEND / "scripts/mac_local.py"), "--runtime", str(runtime), "stop"],
                   check=True, env=without_model_environment(os.environ))
    written = write_plists(runtime)
    for name in AGENTS:
        launchctl("bootout", f"{domain()}/{label(name)}", check=False)
        launchctl("bootstrap", domain(), str(plist_path(name)))
        launchctl("enable", f"{domain()}/{label(name)}")
    return written


def uninstall(runtime):
    for name in reversed(AGENTS):
        launchctl("bootout", f"{domain()}/{label(name)}", check=False)
        plist_path(name).unlink(missing_ok=True)
    for pid in WRITER_PIDS:
        (Path(runtime) / pid).unlink(missing_ok=True)


def _http_status(url):
    import httpx
    try:
        with httpx.Client(timeout=2, trust_env=False, follow_redirects=False) as client:
            return client.get(url).status_code
    except httpx.HTTPError:
        return None


def last_backup(runtime):
    root = Path(runtime) / "scheduled-backups"
    done = []
    if root.is_dir():
        for child in root.iterdir():
            for stamp in ([child] if (child / "manifest.json").is_file() else []):
                done.append(stamp)
    return max(done, key=lambda p: (p / "manifest.json").stat().st_mtime) if done else None


def status(runtime, now=None):
    runtime = Path(runtime)
    config, _ = load(runtime)
    report = {"agents": {}, "problems": []}
    for name in AGENTS:
        result = launchctl("print", f"{domain()}/{label(name)}", check=False)
        pid = None
        for line in result.stdout.splitlines():
            if line.strip().startswith("pid ="):
                pid = int(line.split("=", 1)[1])
        running = bool(result.returncode == 0 and pid and alive(pid))
        report["agents"][name] = {"loaded": result.returncode == 0, "running": running}
        if not running:
            report["problems"].append(f"{name} is not running; run `mac_service.py install`")
    report["ready"] = _http_status(f"http://127.0.0.1:{config['api_port']}/readyz") == 200
    if not report["ready"]:
        report["problems"].append("API /readyz is not ready; see logs/api.service.log")
    report["disk_free_gb"] = round(shutil.disk_usage(runtime).free / 1e9, 1)
    if report["disk_free_gb"] < 5:
        report["problems"].append("less than 5 GB free disk")
    backup = last_backup(runtime)
    if backup is None:
        report["last_backup"] = None
        report["problems"].append("no scheduled backup yet")
    else:
        age_h = ((now or time.time()) - (backup / "manifest.json").stat().st_mtime) / 3600
        report["last_backup"] = {"directory": backup.name, "age_hours": round(age_h, 1)}
        if age_h > 30:
            report["problems"].append("last backup is older than 30 hours")
    report["ok"] = not report["problems"]
    return report


def prune(root, keep):
    """Keep the newest ``keep`` complete backups. Incomplete directories and other files are never touched."""
    complete = sorted((p for p in Path(root).iterdir() if (p / "manifest.json").is_file()),
                      key=lambda p: (p / "manifest.json").stat().st_mtime)
    doomed = complete[:-keep] if keep > 0 else []
    for old in doomed:
        shutil.rmtree(old)
    return [p.name for p in doomed]


def run_backup(runtime, keep=14):
    """Stop the writers, take a verified backup, always restart them."""
    runtime = Path(runtime)
    target = runtime / "scheduled-backups"
    target.mkdir(mode=0o700, exist_ok=True)
    stopped = []
    try:
        for name in ("worker", "api"):
            launchctl("bootout", f"{domain()}/{label(name)}", check=False)
            stopped.append(name)
        for name in WRITER_PIDS:
            (runtime / name).unlink(missing_ok=True)
        result = subprocess.run(
            [sys.executable, str(REPO / "infra/scripts/recovery.py"), "--runtime", str(runtime),
             "backup", "--writers-quiesced", "--backup-dir", str(target)],
            capture_output=True, text=True, env=without_model_environment(os.environ))
        if result.returncode:
            raise RuntimeError("backup failed: " + (result.stderr.strip().splitlines() or ["unknown"])[-1][:200])
        info = json.loads(result.stdout.strip().splitlines()[-1])
    finally:
        for name in reversed(stopped):
            launchctl("bootstrap", domain(), str(plist_path(name)), check=False)
    info["pruned"] = prune(target, keep)
    return info


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("install", "uninstall", "status"):
        commands.add_parser(command)
    run = commands.add_parser("exec")
    run.add_argument("name", choices=AGENTS)
    backup = commands.add_parser("backup")
    backup.add_argument("--keep", type=int, default=14)
    args = parser.parse_args(argv)
    runtime = args.runtime.expanduser().resolve()
    if args.command == "exec":
        exec_agent(runtime, args.name)
    elif args.command == "install":
        print(json.dumps({"installed": install(runtime)}))
    elif args.command == "uninstall":
        uninstall(runtime)
        print("agents removed; stored data retained")
    elif args.command == "backup":
        print(json.dumps(run_backup(runtime, args.keep)))
    else:
        report = status(runtime)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
