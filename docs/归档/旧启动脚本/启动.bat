@echo off
chcp 65001 > nul
cls
echo ========================================
echo   项目文档管理系统 - 快速启动
echo ========================================
echo.
echo [1/3] 检查并安装前端依赖...
cd web
if not exist "node_modules" (
    echo 正在安装前端依赖，请稍候...
    call npm install
)

echo 检查 marked...
call npm list marked >nul 2>&1
if errorlevel 1 (
    echo 安装 marked...
    call npm install marked
)

echo.
echo [2/3] 检查并安装后端依赖...
cd ..\server
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

echo.
echo [3/3] 启动服务...
echo.
echo ----------------------------------------
echo   后端: http://localhost:8001
echo   前端: http://localhost:5173
echo ----------------------------------------
echo.
echo 正在启动后端服务...
start "【后端】项目文档管理系统" cmd /c "cd /d %~dp0server && .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload"

timeout /t 3 /nobreak > nul

echo 正在启动前端服务...
echo.
cd ..\web
echo 前端服务已启动，按 Ctrl+C 停止所有服务
echo.
call npm run dev
