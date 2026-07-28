#!/bin/zsh
set -euo pipefail
unsetopt BG_NICE

PROJECT_DIR="${0:A:h}"
cd "$PROJECT_DIR"

BIND_HOST="${PDFMD_HOST:-127.0.0.1}"
PORT="${PDFMD_PORT:-8000}"

if [[ "$PORT" != <-> ]] || (( PORT < 1 || PORT > 65535 )); then
  echo "PDFMD_PORT 必须是 1 到 65535 之间的整数，当前值：$PORT"
  echo
  read "?按回车键关闭窗口..."
  exit 1
fi

BROWSER_HOST="$BIND_HOST"
if [[ "$BROWSER_HOST" == "0.0.0.0" || "$BROWSER_HOST" == "::" ]]; then
  BROWSER_HOST="127.0.0.1"
fi
if [[ "$BROWSER_HOST" == *:* ]]; then
  BROWSER_HOST="[$BROWSER_HOST]"
fi
BASE_URL="http://${BROWSER_HOST}:${PORT}"

if [[ ! -x .venv/bin/pdfmd-server || ! -f frontend/dist/index.html ]]; then
  echo "首次运行需要完成安装。"
  echo "请在此目录执行：make setup"
  echo
  read "?按回车键关闭窗口..."
  exit 1
fi

EXPECTED_VERSION="$(.venv/bin/python -c 'import pdfmd; print(pdfmd.__version__)')"

service_ready() {
  local health homepage
  health="$(curl -fsS --max-time 2 "$BASE_URL/api/health" 2>/dev/null)" || return 1
  homepage="$(curl -fsS --max-time 2 "$BASE_URL/" 2>/dev/null)" || return 1
  [[ "$health" == *'"status":"ok"'* ]] || return 1
  [[ "$health" == *'"version":"'"$EXPECTED_VERSION"'"'* ]] || return 1
  [[ "$homepage" == *"PDF Markdown Studio"* ]] || return 1
}

if service_ready; then
  echo "PDF Markdown Studio 已在运行：$BASE_URL"
  if ! open "$BASE_URL"; then
    echo "未能自动打开浏览器，请手动访问：$BASE_URL"
  fi
  exit 0
fi

if command -v lsof >/dev/null 2>&1; then
  PORT_OWNER="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "$PORT_OWNER" ]]; then
    echo "端口 $PORT 已被占用，但该地址的健康检查没有通过："
    echo "$PORT_OWNER"
    echo
    echo "请关闭占用端口的旧程序，或改用其他端口："
    echo "  PDFMD_PORT=8001 ./start.command"
    echo
    read "?按回车键关闭窗口..."
    exit 1
  fi
fi

echo "正在启动 PDF Markdown Studio..."
.venv/bin/pdfmd-server --host "$BIND_HOST" --port "$PORT" &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT INT TERM

for _ in {1..120}; do
  if service_ready; then
    if ! open "$BASE_URL"; then
      echo "未能自动打开浏览器，请手动访问：$BASE_URL"
    fi
    echo "应用已启动：$BASE_URL"
    echo "关闭此窗口即可停止服务。"
    wait "$SERVER_PID"
    exit $?
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    if wait "$SERVER_PID"; then
      exit 0
    else
      exit $?
    fi
  fi
  sleep 0.25
done

echo "服务在 30 秒内没有就绪，请检查上方日志。"
echo "也可以运行：curl $BASE_URL/api/health"
exit 1
