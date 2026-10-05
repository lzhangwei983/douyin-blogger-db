#!/bin/sh
set -eu
cd "$(dirname "$0")"

if [ -x ".venv/bin/python" ]; then
  APP_PY="$(pwd)/.venv/bin/python"
else
  command -v python3 >/dev/null 2>&1 || {
    echo "未找到 Python 3.10 或更高版本，请先安装 Python 3。" >&2
    exit 2
  }
  python3 -m venv .venv
  APP_PY="$(pwd)/.venv/bin/python"
fi

"$APP_PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' || {
  echo "当前 Python 版本低于 3.10，请升级后重试。" >&2
  exit 2
}

if [ ! -f ".venv/.douyinblogdb-1.1.0-ready" ]; then
  echo "首次运行正在准备依赖和 Chromium 浏览器，需要联网，请稍候……"
  "$APP_PY" -m pip install -r requirements.txt
  "$APP_PY" -m playwright install chromium
  : > ".venv/.douyinblogdb-1.1.0-ready"
fi

exec "$APP_PY" main.py
