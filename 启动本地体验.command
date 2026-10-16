#!/bin/zsh
set -eu
cd -- "${0:A:h}"
exec services/backend/.venv/bin/python services/backend/scripts/mac_demo.py
