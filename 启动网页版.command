#!/bin/zsh
# 服务由 launchd 守护（见 docs/LOCAL_SERVICE.md）。已在运行时只打开浏览器，不重复构建或启动。
set -eu
cd -- "${0:A:h}"
PY=services/backend/.venv/bin/python
if curl -fsS -o /dev/null http://127.0.0.1:8000/readyz; then
  echo "学伴服务已在运行：http://127.0.0.1:8000/learn/"
  open http://127.0.0.1:8000/learn/
  exit 0
fi
echo "服务未运行，正在安装并启动 launchd 代理…"
$PY services/backend/scripts/mac_service.py install
sleep 10
$PY services/backend/scripts/mac_service.py status || { echo "启动后仍有问题，请查看上面的提示和 ~/.local/share/xueban/local-dev/logs/"; exit 1; }
open http://127.0.0.1:8000/learn/
