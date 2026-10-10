@echo off
chcp 65001 > nul
echo =====================================
echo 文档工作室 - 开发启动脚本
echo =====================================

echo.
echo 1. 检查后端依赖...
cd server

echo 安装后端依赖...
where uv >nul 2>&1
if errorlevel 1 (
    echo 未找到 uv，请先安装：https://docs.astral.sh/uv/
    exit /b 1
)
uv sync --frozen
if errorlevel 1 exit /b 1

echo √ 后端依赖已就绪

echo.
echo 2. 检查前端依赖...
cd ..\web

if not exist "node_modules" (
    echo 安装前端依赖...
    call npm ci
    if errorlevel 1 exit /b 1
)

echo √ 前端依赖已就绪

echo.
echo 3. 启动服务...
echo.

cd ..

if not exist "logs" mkdir logs

echo 首个管理员：数据库没有账号时，请另开窗口执行
echo   cd server ^&^& uv run python -m app.bootstrap_admin
echo 该命令生成随机初始密码并只显示一次，首次登录后必须修改。
echo.
echo 启动后端服务 (http://localhost:8001)...
cd server
start "后端服务" cmd /c "uv run uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload"

timeout /t 3 /nobreak > nul

cd ..\web
echo.
echo 启动前端服务 (http://localhost:5173)...
echo.
echo 按 Ctrl+C 停止服务
echo.
call npm run dev
