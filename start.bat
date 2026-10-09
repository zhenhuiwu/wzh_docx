@echo off
chcp 65001 > nul
echo =====================================
echo 项目文档管理系统 - 启动脚本
echo =====================================

echo.
echo 1. 检查后端依赖...
cd server

if not exist ".venv" (
    echo 创建 Python 虚拟环境...
    python -m venv .venv
)

echo 检查后端依赖...
.venv\Scripts\python.exe -c "import fastapi, uvicorn, docx, markdown, multipart, pydantic_settings" >nul 2>&1
if errorlevel 1 (
    where uv >nul 2>&1
    if errorlevel 1 (
        .venv\Scripts\python.exe -m pip install -e .
    ) else (
        uv sync --locked
    )
    if errorlevel 1 exit /b 1
)

echo √ 后端依赖已就绪

echo.
echo 2. 检查前端依赖...
cd ..\web

if not exist "node_modules" (
    echo 安装前端依赖...
    npm install
)

npm list marked >nul 2>&1
if errorlevel 1 (
    echo 安装 marked...
    npm install marked
)

echo √ 前端依赖已就绪

echo.
echo 3. 启动服务...
echo.

cd ..

if not exist "logs" mkdir logs

echo 启动后端服务 (http://localhost:8001)...
cd server
start "后端服务" cmd /c "call .venv\Scripts\activate.bat && uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload"

timeout /t 3 /nobreak > nul

cd ..\web
echo.
echo 启动前端服务 (http://localhost:5173)...
echo.
echo 按 Ctrl+C 停止服务
echo.
npm run dev
