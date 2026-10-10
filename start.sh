#!/usr/bin/env bash
# 开发环境一键启动（Linux/macOS/Git Bash）。
# 端口可用环境变量修改：BACKEND_PORT（默认 8001）、FRONTEND_PORT（默认 5173）。
# 前端代理默认跟随 BACKEND_PORT；如需代理到别处，设置 VITE_API_ORIGIN。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$ROOT/logs"
BACKEND_PORT="${BACKEND_PORT:-8001}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
export BACKEND_PORT FRONTEND_PORT
export VITE_API_ORIGIN="${VITE_API_ORIGIN:-http://127.0.0.1:$BACKEND_PORT}"
MIN_NODE="22.13"

fail() { echo "错误：$*" >&2; exit 1; }

for name in BACKEND_PORT FRONTEND_PORT; do
  value="${!name}"
  [[ "$value" =~ ^[0-9]+$ ]] && [ "$value" -ge 1 ] && [ "$value" -le 65535 ] || fail "$name=$value 不是有效端口"
done
[ "$BACKEND_PORT" != "$FRONTEND_PORT" ] || fail "BACKEND_PORT 和 FRONTEND_PORT 不能相同"

echo "====================================="
echo "文档工作室 - 开发启动脚本"
echo "====================================="

command -v uv >/dev/null 2>&1 || fail "未找到 uv，请先安装：https://docs.astral.sh/uv/"
command -v curl >/dev/null 2>&1 || fail "未找到 curl（用于检查后端是否启动成功）"
command -v node >/dev/null 2>&1 || fail "未找到 Node.js（需要 $MIN_NODE 及以上，见 web/.nvmrc）"
node -e '
  const [need, have] = [process.argv[1], process.versions.node].map(v => v.split(".").map(Number));
  process.exit(have[0] > need[0] || (have[0] === need[0] && have[1] >= need[1]) ? 0 : 1);
' "$MIN_NODE" || fail "Node.js 版本过低：$(node --version)，需要 $MIN_NODE 及以上（见 web/.nvmrc）"

# 有服务在监听该端口时返回 0。检查 127.0.0.1 和 localhost（vite 可能只监听 ::1）；
# 不依赖 lsof/ss，Git Bash 也可用。
port_in_use() {
  (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null || (exec 3<>"/dev/tcp/localhost/$1") 2>/dev/null
}
port_in_use "$BACKEND_PORT" && fail "后端端口 $BACKEND_PORT 已被占用。请先停止占用它的程序，或设置 BACKEND_PORT 换一个端口。"
port_in_use "$FRONTEND_PORT" && fail "前端端口 $FRONTEND_PORT 已被占用。请先停止占用它的程序，或设置 FRONTEND_PORT 换一个端口。"

mkdir -p "$LOG_DIR"

echo ""
echo "1. 安装后端依赖（uv sync --frozen）..."
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

# 每个后台任务放进自己的进程组（set -m），停止时按进程组发信号，
# uv → uvicorn（含 --reload 子进程）、npm → vite → esbuild 都会一起停止。
set -m
BACKEND_PID=""
FRONTEND_PID=""
CLEANED=0

stop_group() {
  local pid="$1" label="$2"
  [ -n "$pid" ] || return 0
  kill -0 "$pid" 2>/dev/null || return 0
  echo "停止$label（进程组 $pid）..."
  kill -TERM -- "-$pid" 2>/dev/null || true
  for _ in $(seq 1 50); do
    kill -0 -- "-$pid" 2>/dev/null || return 0
    sleep 0.1
  done
  kill -KILL -- "-$pid" 2>/dev/null || true
}

cleanup() {
  [ "$CLEANED" = 1 ] && return 0
  CLEANED=1
  if [ -n "$BACKEND_PID$FRONTEND_PID" ]; then echo ""; fi
  stop_group "$FRONTEND_PID" "前端"
  stop_group "$BACKEND_PID" "后端"
}
on_signal() { cleanup; trap - EXIT; exit "$1"; }
# 先注册清理，再启动后台进程；Ctrl+C（INT）、kill（TERM）、HUP 和正常退出都只清理一次。
trap cleanup EXIT
trap 'on_signal 130' INT
trap 'on_signal 143' TERM
trap 'on_signal 129' HUP

echo ""
echo "4. 启动后端（http://127.0.0.1:$BACKEND_PORT），日志：$LOG_DIR/backend.log"
(cd "$ROOT/server" && exec uv run uvicorn app.main:app --host 127.0.0.1 --port "$BACKEND_PORT" --reload) \
  </dev/null >"$LOG_DIR/backend.log" 2>&1 &
BACKEND_PID=$!

healthy=0
for _ in $(seq 1 120); do
  # 先确认是自己启动的后端还活着，再看健康检查；端口已在启动前确认空闲。
  kill -0 "$BACKEND_PID" 2>/dev/null || fail "后端启动失败，请查看 $LOG_DIR/backend.log"
  # --reload 模式下应用加载失败时重载进程仍在，按日志判断，避免空等 60 秒。
  if grep -qE "Application startup failed|Error loading ASGI app|Traceback \(most recent call last\)" "$LOG_DIR/backend.log"; then
    fail "后端启动失败，请查看 $LOG_DIR/backend.log"
  fi
  if curl -fsS --max-time 2 "http://127.0.0.1:$BACKEND_PORT/api/v1/health" >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 0.5
done
[ "$healthy" = 1 ] || fail "后端 60 秒内没有通过健康检查，请查看 $LOG_DIR/backend.log"
sleep 0.5
kill -0 "$BACKEND_PID" 2>/dev/null || fail "后端启动后立即退出，请查看 $LOG_DIR/backend.log"

echo ""
echo "5. 启动前端（http://localhost:$FRONTEND_PORT，代理到 $VITE_API_ORIGIN），按 Ctrl+C 同时停止前后端"
# 前端不读终端输入（否则后台进程组读终端会被暂停），输出仍显示在本窗口。
(cd "$ROOT/web" && exec npm run dev) </dev/null &
FRONTEND_PID=$!

# wait 可以被信号打断，所以收到 TERM 时会立刻进入清理，而不是等前端退出。
status=0
while true; do
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo "后端已退出，请查看 $LOG_DIR/backend.log" >&2
    status=1
    break
  fi
  if ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
    wait "$FRONTEND_PID" 2>/dev/null || status=$?
    echo "前端已退出（退出码 $status）" >&2
    [ "$status" != 0 ] || status=1
    break
  fi
  sleep 1 &
  wait $! 2>/dev/null || true
done
exit "$status"
