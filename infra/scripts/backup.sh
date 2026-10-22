#!/bin/sh
# Does not stop services. A quiesced maintenance window is explicit and checked.
set -eu
exec python3 "$(dirname "$0")/recovery.py" backup "$@"
