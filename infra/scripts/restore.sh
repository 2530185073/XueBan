#!/bin/sh
# Stages a NEW database and asset store. Never overwrites or switches live data.
set -eu
exec python3 "$(dirname "$0")/recovery.py" restore "$@"
