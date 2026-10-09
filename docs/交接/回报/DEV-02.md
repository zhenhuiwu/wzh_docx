# DEV-02 回报

日期：2026-09-27。状态：本机底座已实现，并在隔离库上跑过迁移、重启和接口。不是生产部署，也不是登录完成。

## 1 本轮结论

本机按已确认的推荐栈跑起来了：Python、FastAPI、单独的 PostgreSQL。健康检查只返回状态、时间和请求标识。文件上传、下载、预览在没有身份服务时一律拒绝，伪造会话也不会把文件写进数据目录。

公司生产部署、证书和密钥政策没有批准。D12 仍待确认。页面上的项目、模板和登录仍是原来的本机原型，还没有改成调用这个接口。

## 2 改动

| 路径 | 变更 |
|---|---|
| `server/pyproject.toml` | 固定 FastAPI 0.141.1、Uvicorn 0.54.0、psycopg 3.3.6、Pydantic Settings 2.15.0；开发依赖 pytest 9.1.1、httpx 0.28.1 |
| `server/uv.lock` | 上述版本的锁定结果 |
| `server/app/__init__.py` | 让 `app` 可以作为包导入 |
| `server/app/config.py` | 只从环境读取 `DATABASE_URL` 和 `FILE_DATA_DIR`，没有默认口令 |
| `server/app/db.py` | 连接与按文件名顺序执行迁移 |
| `server/app/models.py` | 文件元数据；对外字典不含存储键和本机路径 |
| `server/app/errors.py` | 错误响应为 `error` / `requestId`，不带回堆栈 |
| `server/app/files.py` | 临时写入、校验、再提交记录；HTTP 写入和读取保持关闭 |
| `server/app/main.py` | `GET /api/v1/health`；未处理异常不回显连接信息 |
| `server/migrations/001_files.sql` | `files` 表 |
| `server/tests/test_foundation.py` | 迁移、重启后仍能读到元数据、健康检查、未认证和路径越界 |
| `web/vite.config.ts` | 开发服务器把 `/api` 代理到 `http://127.0.0.1:8000`，端口仍是 5173 |
| `docs/交接/04-开工输入与决策记录.md` | D02 记为本机底座已确认，生产部署未批准 |

没有改原始模板，没有改 `tasks.json` 的状态，没有把合成样例写进默认页面。`server/.venv` 是本机安装产物。

依赖安装使用的是上面锁定的版本，虚拟环境是 Python 3.13.12。

## 3 实际验证

隔离库与现有的 `D:\datadb\data`（端口 5433）分开。数据目录是 `C:\Users\15827\AppData\Local\doc-workbench\pgdata`，只监听 `127.0.0.1:55432`，本机 trust，没有把口令写进仓库。库名 `docwb_dev02`。验收写入的一行测试记录已在检查后删除，表结构还在。

| 检查 | 操作 | 结果 |
|---|---|---|
| 安装 | 在 `server` 执行 `uv sync --group dev` | 退出码 0 |
| 语法 | `python -m py_compile` 编译 `app` 与测试 | 与下面的测试同一命令，退出码 0 |
| 测试 | 设置 `DATABASE_URL` 后执行 `uv run pytest tests/test_foundation.py -q` | 8 passed。有一条 httpx 弃用警告，不是失败 |
| 迁移 | 查询 `schema_migrations` 和 `files` 列 | 版本 `001_files.sql`。列含 `storage_key`，接口响应不含它 |
| 重启 | 测试里用新的 Python 进程写入，再换一个进程读回 | 名称、状态和哈希仍在；存储文件名不是原始文件名 |
| 健康 | `GET http://127.0.0.1:8000/api/v1/health` | 200，`{"data":{"status":"ok","timestamp":"2026-09-27T14:33:50Z"},"requestId":"04874c1c-8601-460b-8fbe-9fb088100e82"}`。正文没有连接串、数据目录和堆栈 |
| 上传 | 未登录，文件名带 `../`，并带伪造会话 | 401，`AUTH_REQUIRED`。数据目录没有出现文件 |
| 错误标识 | `GET /api/v1/files/not-a-uuid/content` | 422，`VALIDATION_FAILED` |
| 下载和预览 | 合法标识，无登录或只有伪造会话 | 均为 401，响应不是文件内容 |
| 代理 | 重启前端后请求 `http://localhost:5173/api/v1/health` | 200，得到同一形状的健康结果 |
| 页面 | `http://localhost:5173/` | 200，标题仍是「登录 · 项目文档智能管理系统」 |

数据库不可用时，健康结果里的状态是 `degraded`，测试已覆盖，响应同样不带连接信息。

## 4 前端截图

未执行。本轮没有改页面、样式、路由或展示数据，所以没有重拍 375、768、1280、1440。前端端口仍是 5173。代理是否生效用上面的健康请求确认，不靠截图。

## 5 未完成

- 上传、下载、预览还不能真正使用。身份服务没接上之前，这是故意关闭的，不能靠浏览器里的旧会话打开。
- 没有 CSRF、真实账号、密码哈希和管理员引导。这些属于 DEV-03，还要等 D03、D09。
- 生产机器、证书、密钥存放和备份没有定。本机 trust 只适合这台开发机，不能当作部署方式。
- 页面仍用本地原型数据。工作台、项目和模板没有改成读这个库。
- 文件大小和类型限制只在内部保存函数里。HTTP 目前到不了那一步。同一次请求的幂等记录也还没有，因为上传没有开放。
- 规格的完整 JSON Schema 校验这轮没有跑。
- `tasks.json` 里 DEV-02 仍是 `not_started`。进度以这份回报为准。

## 6 本机再启动

隔离库只监听本机 55432。在 `server` 目录启动接口时要自己提供这两个环境变量，仓库里没有默认值。

```text
D:\datadb\16\bin\postgres.exe -D C:\Users\15827\AppData\Local\doc-workbench\pgdata
DATABASE_URL=postgresql://docwb@127.0.0.1:55432/docwb_dev02
FILE_DATA_DIR=C:\Users\15827\AppData\Local\doc-workbench\files
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

前端仍是 `web` 里的 `npm run dev`，地址 `http://localhost:5173`。

## 7 下一轮

最近的下一项是 DEV-03：真实登录和会话。开始前需要确认身份来源（本地账号还是统一身份）以及角色和会话政策。不要在这两项未确认时把任意密码登录换成一套猜出来的账号。
