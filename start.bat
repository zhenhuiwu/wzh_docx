@echo off
setlocal EnableExtensions
chcp 65001 > nul
rem 开发环境一键启动（Windows）。
rem 端口可用环境变量修改：BACKEND_PORT（默认 8001）、FRONTEND_PORT（默认 5173）。
rem 前端代理默认跟随 BACKEND_PORT；如需代理到别处，设置 VITE_API_ORIGIN。
cd /d "%~dp0"
if not defined BACKEND_PORT set "BACKEND_PORT=8001"
if not defined FRONTEND_PORT set "FRONTEND_PORT=5173"
if not defined VITE_API_ORIGIN set "VITE_API_ORIGIN=http://127.0.0.1:%BACKEND_PORT%"
set "MIN_NODE=22.13"

echo =====================================
echo 文档工作室 - 开发启动脚本
echo =====================================

where uv >nul 2>&1
if errorlevel 1 (
    echo 未找到 uv，请先安装：https://docs.astral.sh/uv/
    exit /b 1
)
where curl >nul 2>&1
if errorlevel 1 (
    echo 未找到 curl，用于检查后端是否启动成功；Windows 10 1803 及以上自带。
    exit /b 1
)
where node >nul 2>&1
if errorlevel 1 (
    echo 未找到 Node.js，需要 %MIN_NODE% 及以上，见 web\.nvmrc
    exit /b 1
)
node -e "const [n,h]=[process.argv[1],process.versions.node].map(v=>v.split('.').map(Number));process.exit(h[0]>n[0]||(h[0]===n[0]&&h[1]>=n[1])?0:1)" %MIN_NODE%
if errorlevel 1 (
    for /f "delims=" %%v in ('node --version') do echo Node.js 版本过低：%%v，需要 %MIN_NODE% 及以上，见 web\.nvmrc
    exit /b 1
)

call :port_free %BACKEND_PORT%
if errorlevel 1 (
    echo 后端端口 %BACKEND_PORT% 已被占用。请先停止占用它的程序，或设置 BACKEND_PORT 换一个端口。
    exit /b 1
)
call :port_free %FRONTEND_PORT%
if errorlevel 1 (
    echo 前端端口 %FRONTEND_PORT% 已被占用。请先停止占用它的程序，或设置 FRONTEND_PORT 换一个端口。
    exit /b 1
)

echo.
echo 1. 安装后端依赖（uv sync --frozen）...
pushd server
uv sync --frozen
if errorlevel 1 (
    popd
    exit /b 1
)
popd

echo.
echo 2. 安装前端依赖...
if not exist "web\node_modules" (
    pushd web
    call npm ci
    if errorlevel 1 (
        popd
        exit /b 1
    )
    popd
)

if not exist "logs" mkdir logs

echo.
echo 3. 首个管理员：数据库没有账号时，请另开窗口执行
echo   cd server ^&^& uv run python -m app.bootstrap_admin
echo 该命令生成随机初始密码并只显示一次，首次登录后必须修改。

echo.
echo 4. 启动后端 http://127.0.0.1:%BACKEND_PORT%（在新窗口“后端服务”中运行，关闭该窗口即停止后端）
pushd server
start "后端服务" cmd /c "uv run uvicorn app.main:app --host 127.0.0.1 --port %BACKEND_PORT% --reload"
popd

rem 端口启动前已确认空闲，因此健康检查通过的只能是刚启动的后端。
set /a TRIES=0
:wait_backend
curl -fsS --max-time 2 "http://127.0.0.1:%BACKEND_PORT%/api/v1/health" >nul 2>&1
if not errorlevel 1 goto backend_ready
set /a TRIES+=1
if %TRIES% geq 60 (
    echo 后端 60 秒内没有通过健康检查，不启动前端。请查看“后端服务”窗口中的错误信息。
    exit /b 1
)
timeout /t 1 /nobreak > nul
goto wait_backend

:backend_ready
echo.
echo 5. 启动前端 http://localhost:%FRONTEND_PORT%（代理到 %VITE_API_ORIGIN%），按 Ctrl+C 停止前端
pushd web
call npm run dev
popd
exit /b %errorlevel%

rem 端口空闲返回 0，已有程序在 127.0.0.1 或 ::1 上监听返回 1。
:port_free
node -e "const n=require('net'),p=+process.argv[1];let left=2,used=0;const done=()=>{if(--left===0)process.exit(used)};for(const h of ['127.0.0.1','::1']){const s=n.connect(p,h);s.once('connect',()=>{used=1;s.destroy();done()});s.once('error',done)}setTimeout(()=>process.exit(used),3000)" %1
exit /b %errorlevel%
