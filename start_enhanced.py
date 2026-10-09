"""Enhanced start script with health checks and better error handling"""

import sys
import time
import subprocess
from pathlib import Path

def print_header(text):
    print("\n" + "=" * 60)
    print(f"  {text}")
    print("=" * 60 + "\n")

def run_command(cmd, cwd=None, shell=True, check=True):
    """运行命令并实时输出"""
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            shell=shell,
            check=check,
            capture_output=True,
            text=True
        )
        return result.returncode == 0
    except subprocess.CalledProcessError as e:
        print(f"错误: {e}")
        if e.stderr:
            print(f"详情: {e.stderr}")
        return False

def check_dependencies():
    """检查系统依赖"""
    print_header("检查系统依赖")

    # 检查 Python
    try:
        result = subprocess.run([sys.executable, "--version"], capture_output=True, text=True)
        print(f"✓ Python: {result.stdout.strip()}")
    except:
        print("✗ Python 未安装")
        return False

    # 检查 Node
    try:
        result = subprocess.run(["node", "--version"], capture_output=True, text=True)
        print(f"✓ Node: {result.stdout.strip()}")
    except:
        print("✗ Node.js 未安装")
        return False

    return True

def setup_backend():
    """配置后端环境"""
    print_header("配置后端环境")

    server_dir = Path(__file__).parent / "server"
    venv_dir = server_dir / ".venv"

    # 创建虚拟环境
    if not venv_dir.exists():
        print("创建 Python 虚拟环境...")
        if not run_command(f"{sys.executable} -m venv .venv", cwd=server_dir):
            return False

    # 安装依赖
    print("安装后端依赖...")
    pip_cmd = str(venv_dir / "Scripts" / "pip.exe") if sys.platform == "win32" else str(venv_dir / "bin" / "pip")

    if not run_command(f"{pip_cmd} install -q -e .", cwd=server_dir):
        print("尝试使用 --no-cache-dir 重新安装...")
        if not run_command(f"{pip_cmd} install -e . --no-cache-dir", cwd=server_dir):
            return False

    print("✓ 后端环境就绪")
    return True

def setup_frontend():
    """配置前端环境"""
    print_header("配置前端环境")

    web_dir = Path(__file__).parent / "web"

    # 安装依赖
    if not (web_dir / "node_modules").exists():
        print("安装前端依赖...")
        if not run_command("npm install", cwd=web_dir):
            return False

    # 检查 marked
    result = subprocess.run(
        "npm list marked",
        cwd=web_dir,
        shell=True,
        capture_output=True
    )

    if result.returncode != 0:
        print("安装 marked...")
        if not run_command("npm install marked", cwd=web_dir):
            return False

    print("✓ 前端环境就绪")
    return True

def start_services():
    """启动服务"""
    print_header("启动服务")

    server_dir = Path(__file__).parent / "server"
    web_dir = Path(__file__).parent / "web"

    # 创建日志目录
    logs_dir = Path(__file__).parent / "logs"
    logs_dir.mkdir(exist_ok=True)

    print("启动后端服务 (http://localhost:8001)...")

    if sys.platform == "win32":
        python_exe = str(server_dir / ".venv" / "Scripts" / "python.exe")

        # Windows: 使用 start 命令在新窗口启动
        subprocess.Popen(
            f'start "后端服务" cmd /c "{python_exe} -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload"',
            shell=True,
            cwd=server_dir
        )
    else:
        python_exe = str(server_dir / ".venv" / "bin" / "python")

        # Linux/Mac: 后台运行
        log_file = logs_dir / "backend.log"
        with open(log_file, "w") as f:
            subprocess.Popen(
                [python_exe, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8001", "--reload"],
                cwd=server_dir,
                stdout=f,
                stderr=subprocess.STDOUT
            )

    print("✓ 后端启动中...")
    print("  等待 3 秒...")
    time.sleep(3)

    print("\n启动前端服务 (http://localhost:5173)...")
    print("按 Ctrl+C 停止所有服务\n")

    # 前端在前台运行（这样可以看到日志）
    try:
        subprocess.run(
            "npm run dev",
            cwd=web_dir,
            shell=True
        )
    except KeyboardInterrupt:
        print("\n\n正在停止服务...")
        print("✓ 服务已停止")

def main():
    """主函数"""
    print_header("项目文档管理系统 - 增强启动脚本")

    try:
        # 检查依赖
        if not check_dependencies():
            print("\n✗ 系统依赖检查失败")
            return 1

        # 配置后端
        if not setup_backend():
            print("\n✗ 后端配置失败")
            return 1

        # 配置前端
        if not setup_frontend():
            print("\n✗ 前端配置失败")
            return 1

        # 启动服务
        start_services()

        return 0

    except KeyboardInterrupt:
        print("\n\n✓ 已取消")
        return 0
    except Exception as e:
        print(f"\n✗ 启动失败: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
