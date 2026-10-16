"""Open the already provisioned Mac Android demo; never embeds model credentials."""
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
LOCAL = Path.home() / ".local/share/xueban"
SDK = Path(os.environ.get("ANDROID_HOME", LOCAL / "android-sdk"))
ADB = SDK / "platform-tools/adb"
AVD = "XueBan_Tablet"
APK = ROOT / "apps/android/app/build/outputs/apk/debug/app-debug.apk"


def adb(*args, capture=False):
    return subprocess.run([str(ADB), "-s", "emulator-5554", *args], check=True, capture_output=capture, text=True)


def main():
    if not APK.is_file() or not ADB.is_file():
        raise SystemExit("Build the debug APK and provision the Android SDK first; see docs/MAC_LOCAL_RUN.md")
    subprocess.run([sys.executable, str(Path(__file__).with_name("mac_local.py")), "start"], check=True)
    devices = subprocess.check_output([str(ADB), "devices"], text=True)
    if "emulator-5554" not in devices:
        env = os.environ.copy()
        env["ANDROID_AVD_HOME"] = str(LOCAL / "android-avd")
        with (LOCAL / "emulator.log").open("ab") as output:
            subprocess.Popen([str(SDK / "emulator/emulator"), "-avd", AVD, "-port", "5554", "-no-snapshot", "-no-boot-anim"],
                             env=env, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        result = subprocess.run([str(ADB), "-s", "emulator-5554", "shell", "getprop", "sys.boot_completed"], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip() == "1":
            break
        time.sleep(1)
    else:
        raise SystemExit("Emulator did not boot; inspect ~/.local/share/xueban/emulator.log")
    name = adb("emu", "avd", "name", capture=True).stdout.splitlines()[0]
    if name != AVD:
        raise SystemExit("Port 5554 belongs to another emulator; refusing to install there")
    adb("reverse", "tcp:8000", "tcp:8000")
    adb("install", "-r", str(APK))
    for fixture in ("original.png", "corrected.png"):
        target = "/sdcard/Download/xueban-" + fixture
        adb("push", str(ROOT / "evals/correction/fixtures/ark-smoke" / fixture), target)
        adb("shell", "am", "broadcast", "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE", "-d", "file://" + target)
    adb("shell", "am", "start", "-n", "cn.xueban.companion.dev/cn.xueban.companion.MainActivity")
    print("Demo opened. API: http://127.0.0.1:8000. Use a freshly issued pairing code when requested.")


if __name__ == "__main__":
    main()
