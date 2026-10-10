# 文档工作室

面向公司内网的项目文档编制与测试取证系统：按 GJB 438C 等 Word 模板导入文档结构，由 AI 分章节辅助编写，在网页中编辑后导出 Word；并提供 AI 测试用例管理（真实执行、截图取证、生成 Word 报告），以及项目、用户、模板和 Skill 管理。

- 后端：Python 3.12+、FastAPI、SQLite，依赖由 [uv](https://docs.astral.sh/uv/) 管理，代码在 `server/`。**uv 最低 0.4.30**：`server/uv.lock`（revision 5）已用 uv 0.4.30、0.5.0、0.5.31、0.6.0、0.6.17、0.7.0、0.7.22、0.8.0、0.8.24、0.9.5、0.10.0、0.12.23 实测：`uv export --frozen` / `uv lock --check` 都能读取，`uv lock` 也不会改写它；CI 固定使用 0.12.23
- 前端：Vue 3 + TypeScript + Vite，代码在 `web/`，需要 **Node.js 22.13 及以上**（见 `web/.nvmrc`、`web/package.json` 的 `engines`；`web/.npmrc` 开启了 `engine-strict`，版本不够时 `npm ci` 直接失败）。前端依赖只以 `web/package-lock.json` 为准，仓库根目录没有 Node 工程
- 界面只按 1920×1080 固定桌面设计与验收，开发与验收地址固定为 http://localhost:5173 ，其他约束见 [AGENTS.md](AGENTS.md)
- 设计与交接资料见 `docs/`；`docs/归档/` 中是已过时的历史说明

## 快速开始（开发环境）

```bash
# 1. 后端依赖
cd server
uv sync --frozen              # 严格按 uv.lock 安装，不改写锁文件
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

也可以在仓库根目录执行 `./start.sh`（Linux/macOS/Git Bash）或 `start.bat`（Windows）一次启动前后端。脚本会：

- 检查 uv、curl 与 Node.js（22.13 及以上）；
- 启动前检查端口，已被占用就报错退出，不会连到别人的服务；
- 后端通过健康检查（并确认是脚本自己启动的进程）后才启动前端，后端启动失败则不启动前端；
- `start.sh` 的后端日志写入 `logs/backend.log`；按 Ctrl+C 或对脚本发送 SIGTERM，都会按进程组停止前后端。`start.bat` 的后端在单独的“后端服务”窗口运行。

改端口（在共享机器上同时跑多套时使用）：

```bash
BACKEND_PORT=8031 FRONTEND_PORT=5201 ./start.sh
```

Windows 下先 `set BACKEND_PORT=8031` 和 `set FRONTEND_PORT=5201` 再运行 `start.bat`。Vite 的端口读取 `FRONTEND_PORT`，`/api` 代理默认跟随 `BACKEND_PORT`。如果手动启动后端时换了端口，单独启动前端也要设置同样的 `BACKEND_PORT`，或直接设置 `VITE_API_ORIGIN=http://127.0.0.1:<端口>`。正式验收仍以 5173 为准（见 AGENTS.md）。

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
| `BACKEND_PORT`（启动脚本、前端） | `8001` | 启动脚本的后端端口；Vite 代理默认指向 `http://127.0.0.1:$BACKEND_PORT` |
| `FRONTEND_PORT`（启动脚本、前端） | `5173` | Vite 开发服务器端口（strictPort，被占用即失败） |
| `VITE_API_ORIGIN`（前端） | `http://127.0.0.1:$BACKEND_PORT` | Vite 代理 `/api` 的目标，设置后优先于 `BACKEND_PORT` |

不要把真实密钥提交到仓库：`.env` 已被 `.gitignore` 忽略。

## 第一个管理员与改密

系统没有默认账号和默认密码，也没有公开注册。数据库中没有任何账号时，可以用以下任一方式创建第一个管理员：

1. **命令行生成随机密码**（推荐）：`cd server && uv run python -m app.bootstrap_admin`。账号默认 `admin`，可用 `BOOTSTRAP_ADMIN_ACCOUNT`、`BOOTSTRAP_ADMIN_NAME` 指定；初始密码只在终端显示一次。
2. **环境变量**：同时设置 `BOOTSTRAP_ADMIN_ACCOUNT`、`BOOTSTRAP_ADMIN_NAME`、`BOOTSTRAP_ADMIN_PASSWORD`（至少 12 位），后端启动时创建。
3. **Windows 便携版**：首次启动自动生成随机初始密码，先写入程序目录下的 `初始管理员密码.txt`（仅当前用户可读）并显示在启动窗口，两步都成功后才创建账号；任何一步失败都不会留下无人知道密码的账号，修正问题后重新启动即可。首次改密成功后该文件会被自动删除。

“首次登录须改密”的情况：

- 以上方式创建的第一个管理员；
- 管理员在“系统管理 → 用户管理”中新建的账号，以及被管理员重置了密码的账号（管理员重置自己的密码除外）；
- 后端启动时发现仍在使用旧文档公开过的密码 `admin123` 的任何账号。

登录后前端会跳转到修改密码页面；改密完成前，后端除查看当前会话、退出登录和修改密码外的所有接口都返回 `403 PASSWORD_CHANGE_REQUIRED`，前端任意请求收到该错误也会立即跳到改密页。当前密码输错返回 `422 CURRENT_PASSWORD_INCORRECT`；同一账号 10 分钟内输错 8 次后改密接口返回 `429`，不影响登录。

**忘记管理员密码（离线重置）**：在服务器上停止服务后执行

```bash
cd server && uv run python -m app.reset_admin            # 重置 admin
cd server && uv run python -m app.reset_admin <账号>      # 重置指定账号
```

便携版在程序目录执行 `文档工作室.exe --reset-admin [账号]`，新密码同时写入 `初始管理员密码.txt`。重置会生成随机临时密码（只显示一次），使该账号所有会话失效、清除登录锁定，下次登录必须改密。命令使用与服务相同的 `DATABASE_URL`。

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
cd ../web && npm ci && npm run build && npm audit
```

说明：

- **运行 pytest 前先确认 `node --version` 是 22.13 及以上**（例如 `nvm use` 读取 `web/.nvmrc`）。测试执行器相关用例依赖 Node.js 22 的逐文件 `test:summary` 事件；PATH 中是更低版本时这些用例会被跳过（不是失败），跳过数会比 CI 多。
- 原生 Visio 生成、Word 目录更新与版式预览需要 Windows 和本机 Microsoft Office，对应测试在其他平台跳过；非 Windows 环境调用这些功能会返回明确错误。
- 持续集成配置见 `.github/workflows/ci.yml`（pytest + Playwright、ruff、部署快照检查、vue-tsc 构建、npm audit）。

## Windows 便携版打包

在 Windows 上（需要 uv、Node.js 22、PowerShell）：

```powershell
cd deploy
./build.ps1          # 构建 exe，并把当前 SQLite 数据与文件迁移到 release/文档工作室，再压缩为 zip
./build.ps1 -NoZip   # 只构建程序，不迁移数据、不压缩
```

- `deploy/build.ps1` 与 `*.bat` 以 CRLF 换行保存，`.gitattributes` 禁止 Git 转换它们的换行。
- 程序入口为 `server/desktop.py`，PyInstaller 配置为 `deploy/文档工作室.spec`，同时提供前端页面与 API，默认端口 5173，只监听 127.0.0.1。
- 数据迁移由 `deploy/snapshot_data.py` 完成：只读校验源数据，清空会话等临时表，拒绝覆盖已有部署目录。
- 部署包在 `deploy/.build/package` 中**按允许清单**重新组装（程序、`_internal`、迁移的 `data`、`.env.example`、`使用说明.txt`、`部署校验.json`），每次先清空；`release/` 中本机试用留下的 `.env`、数据、日志、密码文件不会进入 zip。出现清单外的文件时打包直接失败。`-NoZip` 只刷新 `release/文档工作室` 中的程序文件，保留其旁边的数据和 `.env`。
- **部署包不含 `.env` 和 AI 密钥**，只生成 `.env.example`（带原环境的 AI 地址、模型等非敏感配置，值按 python-dotenv 规则转义）。目标电脑需要 AI 功能时，复制为 `.env` 并填写 `AI_API_KEY`。
- **部署包不含被测系统的登录密码**：只在测试执行相关的列（`test_tasks.config_json/cases_json/error`、`test_task_runs.snapshot_json/result_json`）中删除 `testPassword` 等凭据键（password、secret、apiKey、token），并把这些列里残留的明文替换为 `******`，按“表.列”记入 `部署校验.json`；其他列保证原样，迁移时会校验。若被删除的密码作为完整词还出现在其他表（例如弱密码 `123456` 出现在正文、或与某个账号同名），迁移会停止并指出表和列，不会替换，请先在原系统中修改该测试任务的被测系统密码后重试。数据库随后 VACUUM 重建。**部署后请在“测试用例管理”中逐个任务重新填写被测系统密码再执行测试。** 账号密码以哈希形式迁移，原密码继续有效。

## 目录

```
server/            后端（app/ 源码，migrations/ SQLite 迁移，tests/ 测试，workspaces/ 内置 438C 技能与参考模板）
web/               前端
deploy/            Windows 便携版打包与数据迁移脚本
docs/              需求、设计、交接、评审资料；docs/归档/ 为过时说明
438C-2021全套模板/  参考 Word 模板
.grok/skills/      仓库内的开发技能说明
```
