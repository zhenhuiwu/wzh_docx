#!/bin/bash

echo "====================================="
echo "项目文档管理系统 - 启动脚本"
echo "====================================="

# 检查并安装后端依赖
echo ""
echo "1. 检查后端依赖..."
cd server

if [ ! -d ".venv" ]; then
    echo "创建 Python 虚拟环境..."
    python -m venv .venv
fi

echo "激活虚拟环境并安装依赖..."
source .venv/Scripts/activate || source .venv/bin/activate

pip install -q -e .

echo "✓ 后端依赖已就绪"

# 检查并安装前端依赖
echo ""
echo "2. 检查前端依赖..."
cd ../web

if [ ! -d "node_modules" ]; then
    echo "安装前端依赖..."
    npm install
fi

# 检查 marked 是否安装
if ! npm list marked > /dev/null 2>&1; then
    echo "安装 marked..."
    npm install marked
fi

echo "✓ 前端依赖已就绪"

# 启动服务
echo ""
echo "3. 启动服务..."
echo ""

cd ..

# 启动后端（后台运行）
echo "启动后端服务 (http://localhost:8001)..."
cd server
source .venv/Scripts/activate || source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload > ../logs/backend.log 2>&1 &
BACKEND_PID=$!
echo "✓ 后端 PID: $BACKEND_PID"

# 等待后端启动
sleep 3

# 启动前端
cd ../web
echo ""
echo "启动前端服务 (http://localhost:5173)..."
echo ""
npm run dev

# 清理
trap "kill $BACKEND_PID 2>/dev/null" EXIT
