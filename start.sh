#!/usr/bin/env bash
# 开发环境一键启动（Linux/macOS/Git Bash）：后端 http://127.0.0.1:8001，前端 http://localhost:5173
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"

echo "====================================="
echo "文档工作室 - 开发启动脚本"
echo "====================================="

command -v uv >/dev/null 2>&1 || { echo "未找到 uv，请先安装：https://docs.astral.sh/uv/"; exit 1; }
command -v node >/dev/null 2>&1 || { echo "未找到 Node.js（需要 22.13 及以上）"; exit 1; }
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
if [ "$NODE_MAJOR" -lt 22 ]; then
  echo "Node.js 版本过低：$(node --version)，需要 22.13 及以上（见 .nvmrc）"
  exit 1
fi

echo ""
echo "1. 安装后端依赖（uv sync）..."
(cd "$ROOT/server" && uv sync --frozen)

echo ""
echo "2. 安装前端依赖..."
if [ ! -d "$ROOT/web/node_modules" ]; then
  (cd "$ROOT/web" && npm ci)
fi

echo ""
echo "3. 首个管理员"
if [ -n "${BOOTSTRAP_ADMIN_PASSWORD:-}" ]; then
  echo "   已设置 BOOTSTRAP_ADMIN_*，数据库没有任何账号时会在启动时创建管理员（首次登录须改密）。"
else
  echo "   如果数据库还没有账号，请另开终端执行："
  echo "     cd server && uv run python -m app.bootstrap_admin"
  echo "   该命令会生成随机初始密码并只显示一次；首次登录后必须修改。"
fi

BACKEND_PID=""
cleanup() {
  if [ -n "$BACKEND_PID" ] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo ""
    echo "停止后端（PID $BACKEND_PID）..."
    kill "$BACKEND_PID" 2>/dev/null || true
    wait "$BACKEND_PID" 2>/dev/null || true
  fi
}
# 先注册清理，再启动后台进程，Ctrl+C 时不会留下孤儿后端。
trap cleanup EXIT INT TERM

echo ""
echo "4. 启动后端（http://127.0.0.1:8001），日志：$LOG_DIR/backend.log"
(cd "$ROOT/server" && exec uv run uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload) \
  >"$LOG_DIR/backend.log" 2>&1 &
BACKEND_PID=$!

for _ in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8001/api/v1/health >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo "后端启动失败，请查看 $LOG_DIR/backend.log"
    exit 1
  fi
  sleep 0.5
done

echo ""
echo "5. 启动前端（http://localhost:5173），按 Ctrl+C 同时停止前后端"
cd "$ROOT/web"
npm run dev
