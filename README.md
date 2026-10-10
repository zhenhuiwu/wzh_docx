# 文档工作室

面向公司内网的项目文档编制与测试取证系统：按 GJB 438C 等 Word 模板导入文档结构，由 AI 分章节辅助编写，在网页中编辑后导出 Word；并提供 AI 测试用例管理（真实执行、截图取证、生成 Word 报告），以及项目、用户、模板和 Skill 管理。

- 后端：Python 3.12+、FastAPI、SQLite，依赖由 [uv](https://docs.astral.sh/uv/) 管理，代码在 `server/`
- 前端：Vue 3 + TypeScript + Vite，代码在 `web/`，需要 **Node.js 22.13 及以上**（见 `.nvmrc`、`web/package.json` 的 `engines`）
- 界面只按 1920×1080 固定桌面设计与验收，开发与验收地址固定为 http://localhost:5173 ，其他约束见 [AGENTS.md](AGENTS.md)
- 设计与交接资料见 `docs/`；`docs/归档/` 中是已过时的历史说明

## 快速开始（开发环境）

```bash
# 1. 后端依赖
cd server
uv sync
cp .env.example .env          # 可选：不建 .env 时使用下表默认值

# 2. 创建第一个管理员（数据库没有任何账号时）
uv run python -m app.bootstrap_admin
#   生成随机初始密码并只显示一次；首次登录后必须修改密码

# 3. 启动后端（端口 8001）
uv run uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload

# 4. 另开终端启动前端（端口 5173，/api 代理到 8001）
cd web
npm ci
npm run dev
```

也可以在仓库根目录执行 `./start.sh`（Linux/macOS/Git Bash）或 `start.bat`（Windows）一次启动前后端；脚本会检查 uv 与 Node 版本，日志写入 `logs/backend.log`，按 Ctrl+C 同时停止前后端。

## 配置（环境变量或 `server/.env`）

后端从进程环境变量读取配置，并读取**启动目录**下的 `.env`。模板见 [`server/.env.example`](server/.env.example)。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/app.db` | 只支持 `sqlite:///` 开头的地址；启动时自动执行 `server/migrations` 中 100 号及以后的 SQLite 迁移（001–035 为已停用的 PostgreSQL 迁移） |
| `FILE_DATA_DIR` | `./data/files` | 上传文件、导出文件、测试截图的存放目录 |
| `FILE_MAX_BYTES` | `20971520` | 单个上传文件上限（字节） |
| `AI_BASE_URL` / `AI_MODEL` / `AI_API_KEY` | 空 | OpenAI 兼容接口；留空时 AI 编写与 AI 测试不可用 |
| `AI_CONTEXT_TOKENS` | `32000` | 模型上下文长度，最小 8000 |
| `AI_REASONING_EFFORT` | 空 | `none`、`minimal`、`low`、`medium`、`high`、`xhigh`、`max`；留空使用模型默认 |
| `COOKIE_SECURE` | 空 | 通过 HTTPS 访问时设为 `1` |
| `BOOTSTRAP_ADMIN_ACCOUNT` / `BOOTSTRAP_ADMIN_NAME` / `BOOTSTRAP_ADMIN_PASSWORD` | 空 | 见下节 |
| `VITE_API_ORIGIN`（前端） | `http://127.0.0.1:8001` | Vite 开发服务器代理 `/api` 的目标 |

不要把真实密钥提交到仓库：`.env` 已被 `.gitignore` 忽略。

## 第一个管理员与改密

系统没有默认账号和默认密码，也没有公开注册。数据库中没有任何账号时，可以用以下任一方式创建第一个管理员：

1. **命令行生成随机密码**（推荐）：`cd server && uv run python -m app.bootstrap_admin`。账号默认 `admin`，可用 `BOOTSTRAP_ADMIN_ACCOUNT`、`BOOTSTRAP_ADMIN_NAME` 指定；初始密码只在终端显示一次。
2. **环境变量**：同时设置 `BOOTSTRAP_ADMIN_ACCOUNT`、`BOOTSTRAP_ADMIN_NAME`、`BOOTSTRAP_ADMIN_PASSWORD`（至少 12 位），后端启动时创建。
3. **Windows 便携版**：首次启动自动生成随机初始密码，显示在启动窗口并写入程序目录下的 `初始管理员密码.txt`（改密后请删除）。

以上方式创建的管理员都标记为“首次登录须改密”：登录后前端会跳转到修改密码页面；改密完成前，后端除查看当前会话、退出登录和修改密码外的所有接口都返回 `403 PASSWORD_CHANGE_REQUIRED`。旧版便携包使用的固定密码 `admin123` 若仍在使用，后端启动时也会把该账号标记为须改密。其他用户由管理员在“系统管理 → 用户管理”中创建。

## 测试与检查

```bash
# 后端测试：必须指定一个与业务库不同的测试库地址（每个用例实际使用临时目录中的独立 SQLite）
cd server
uv run playwright install chromium        # 首次：浏览器测试需要
DOCWB_TEST_DATABASE_URL=sqlite:////tmp/docwb-test.db uv run pytest -q

# 代码检查（规则见 server/pyproject.toml 的 [tool.ruff]）
uvx ruff check . ../deploy ../test_csrf.py --config pyproject.toml

# 部署快照脚本自检
cd ../deploy && ../server/.venv/bin/python test_snapshot_data.py

# 前端类型检查与构建
cd ../web && npm run build
```

说明：

- Node 测试执行器依赖 Node.js 22 的逐文件 `test:summary` 事件；在更低版本上相关用例会跳过，执行器也会明确提示需要 Node.js 22。
- 原生 Visio 生成、Word 目录更新与版式预览需要 Windows 和本机 Microsoft Office，对应测试在其他平台跳过；非 Windows 环境调用这些功能会返回明确错误。
- 持续集成配置见 `.github/workflows/ci.yml`（pytest + Playwright、ruff、部署快照检查、vue-tsc 构建、npm audit）。

## Windows 便携版打包

在 Windows 上（需要 uv、Node.js 22、PowerShell）：

```powershell
cd deploy
./build.ps1          # 构建 exe，并把当前 SQLite 数据与文件迁移到 release/文档工作室，再压缩为 zip
./build.ps1 -NoZip   # 只构建程序，不迁移数据、不压缩
```

- 程序入口为 `server/desktop.py`，PyInstaller 配置为 `deploy/文档工作室.spec`，同时提供前端页面与 API，默认端口 5173，只监听 127.0.0.1。
- 数据迁移由 `deploy/snapshot_data.py` 完成：只读校验源数据，清空会话等临时表，拒绝覆盖已有部署目录。
- **部署包不含 `.env` 和 AI 密钥**，只生成 `.env.example`（带原环境的 AI 地址、模型等非敏感配置）。目标电脑需要 AI 功能时，复制为 `.env` 并填写 `AI_API_KEY`。发布目录中若有 `.env` 或 `初始管理员密码.txt`，打包会直接拒绝。

## 目录

```
server/            后端（app/ 源码，migrations/ SQLite 迁移，tests/ 测试，workspaces/ 内置 438C 技能与参考模板）
web/               前端
deploy/            Windows 便携版打包与数据迁移脚本
docs/              需求、设计、交接、评审资料；docs/归档/ 为过时说明
438C-2021全套模板/  参考 Word 模板
.grok/skills/      仓库内的开发技能说明
```
