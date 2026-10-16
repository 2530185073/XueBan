#!/bin/sh
set -u
task_failed=0
if java -version; then
    printf '%s\n' 'Java is available. This project requires JDK 17.'
else
    printf '%s\n' 'BLOCKED: a usable JDK is missing; macOS /usr/bin/java can be only a launcher.'
    task_failed=1
fi
task_sdk="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/Library/Android/sdk}}"
if [ -z "${ANDROID_HOME:-}${ANDROID_SDK_ROOT:-}" ] && [ -f local.properties ]; then
    task_local_sdk=$(sed -n 's/^sdk.dir=//p' local.properties | tail -n 1)
    if [ -n "$task_local_sdk" ]; then task_sdk="$task_local_sdk"; fi
fi
if [ -f "$task_sdk/platforms/android-35/android.jar" ]; then
    printf 'SDK platform found: %s\n' "$task_sdk/platforms/android-35"
else
    printf 'BLOCKED: Android SDK 35 not found at %s (or supply sdk.dir in local.properties).\n' "$task_sdk"
    task_failed=1
fi
if [ -d "$task_sdk/build-tools/35.0.0" ]; then
    printf '%s\n' 'Android Build Tools 35.0.0 found.'
else
    printf '%s\n' 'BLOCKED: Android Build Tools 35.0.0 not found.'
    task_failed=1
fi
if command -v adb >/dev/null 2>&1; then
    adb version
elif [ -x "$task_sdk/platform-tools/adb" ]; then
    "$task_sdk/platform-tools/adb" version
else
    printf '%s\n' 'NOTE: adb is missing; physical-device checks cannot run.'
fi
exit "$task_failed"
