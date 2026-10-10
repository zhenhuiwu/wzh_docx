# 07 Windows 实测清单（local/stabilize，2026-10-10）

代码审查员终审（对应 5f6afcf）的结论是“可合入”，前提之一是下面三类项目在真实 Windows 上或 GitHub Windows CI 中跑通。开发环境是 Linux，这些项目**都还没有实测过**。逐项执行，并在每项后面记录结果、日期、Windows 版本和执行人。

准备：Windows 10/11 x64，安装 uv（≥0.4.30）、Node.js（≥22.13，见根目录 `.nvmrc`）、Git、PowerShell 5.1 或 7。用**普通用户**和**以管理员身份运行**各做一遍第 1 类。

## 1. pywin32 在真实系统上的行为（初始密码文件 ACL）

依据：`server/app/bootstrap_admin.py` 的 `write_secret_file` / `_write_owner_only_windows` / `owner_only_acl_problem`。

1. `cd server`，`uv sync --frozen`。
2. 运行仓库自带的用例（含两种独立校验：pywin32 读回，以及 `icacls /save` 的 SDDL 加 `whoami /user`）：
   ```powershell
   $env:DOCWB_TEST_DATABASE_URL = "sqlite:///$env:TEMP\docwb-test.db"
   uv run pytest -q -rs tests/test_desktop_package.py
   ```
   预期：`test_windows_password_file_has_owner_only_protected_dacl`、`test_windows_acl_seen_by_icacls`、`test_windows_initial_admin_file_is_owner_only` 三项都**通过，不能是跳过**。
3. 手工复核一次：
   ```powershell
   uv run python -c "from pathlib import Path; from app.bootstrap_admin import write_secret_file as w; w(Path('t.txt'), 'x')"
   icacls t.txt
   (Get-Acl t.txt).AreAccessRulesProtected
   whoami /user
   Remove-Item t.txt
   ```
   预期：`icacls` 只列出当前用户一行 `(F)`，没有 `(I)`；`AreAccessRulesProtected` 为 True；SID 与 `whoami /user` 一致。
4. 需要确认的三点（审查员列出）：
   - `GetFileSecurity` 返回的控制位包含 `SE_DACL_PROTECTED`（上面第 2 步通过即可说明）；
   - `PySID` 用 `==` 比较时按 `EqualSid` 判断（同上）；
   - **以管理员身份运行**时，把文件属主设为用户 SID（而不是 Administrators 组）能被系统接受：在管理员 PowerShell 中重复第 2、3 步。
5. 再用另一个本机普通用户尝试 `type 初始管理员密码.txt`，预期“拒绝访问”。

## 2. GitHub Windows CI job

依据：`.github/workflows/ci.yml` 的 `windows` job，它从未在 GitHub 上运行过。

1. **推送前必须由用户本人确认**（仓库是公开的）。确认后推送分支或开 PR，打开 Actions 页面。
2. 检查 `windows` job 的每一步：
   - `uv sync --frozen` 通过；
   - pytest：`DOCWB_TEST_DATABASE_URL=sqlite:///${{ runner.temp }}/docwb-ci-test.db` 在 Windows 路径（如 `D:\a\_temp\...`）下能被解析（`app/db.py` 去掉 `sqlite:///` 前缀后按 Path 处理）。若失败，改为 `sqlite:///docwb-ci-test.db` 这样的相对路径；
   - 第 1 类的三个 ACL 用例显示为 passed；
   - 部署快照检查 `test_snapshot_data.py` 通过；
   - `build.ps1` 语法检查打印“build.ps1 语法正确”。
3. 同时确认 `backend`、`frontend` 两个 job 也通过：actions 固定 SHA、uv 0.12.23、ruff 0.17.0、根目录 `.nvmrc`。

## 3. 打包后的 exe、build.ps1 和 start.bat

1. **start.bat**（在仓库根目录 cmd 中运行）：
   - 正常启动：后端出现在“后端服务”窗口，健康检查通过后才启动前端，浏览器打开 http://localhost:5173 可登录；
   - 先用 `python -m http.server 8001` 占住端口，再运行，预期报“后端端口 8001 已被占用”并退出；
   - `set BACKEND_PORT=8031`、`set FRONTEND_PORT=5201` 后运行，前端代理指向 8031；
   - 把 PATH 中的 Node 换成 20.x，预期报“Node.js 版本过低”。
2. **build.ps1**（PowerShell，`cd deploy`）：
   - `./build.ps1 -NoZip`：`release/文档工作室` 中只刷新 exe 和 `_internal`，原有 `data`、`.env` 保留；
   - `./build.ps1`：zip 只含 exe、`_internal`、`data`、`.env.example`、`使用说明.txt`、`部署校验.json`。在 `release/文档工作室` 里放一个 `.env` 和 `初始管理员密码.txt`，确认它们**不会**进入 zip；
   - 让某个测试任务的被测系统密码为 `123456`，并在文档正文中写“产品编号 123456”，预期打包停止并报 `documents.markdown_content`；再用 `./build.ps1 -AllowCredentialText documents.markdown_content` 打包成功，`部署校验.json` 的 `accepted_leftovers` 记录了次数；
   - 解压 zip，用文本搜索确认 `data/app.db` 中没有该任务的被测系统密码（除上一步明确放行的正文外）。
3. **exe**（把 zip 解压到一个空目录，库中没有账号的全新数据）：
   - 首次双击启动：窗口显示初始密码，目录中出现 `初始管理员密码.txt`，按第 1 类第 3 步检查其 ACL；
   - 用初始密码登录，被强制跳到改密页；改密成功后 `初始管理员密码.txt` 被自动删除；
   - 关闭程序，执行 `文档工作室.exe --reset-admin admln`（故意写错账号），预期报“没有账号 admln”、返回码 1，已有的密码文件不被删除；
   - 执行 `文档工作室.exe --reset-admin`，生成新的临时密码并写入 `初始管理员密码.txt`（ACL 同上），旧会话失效，登录后须改密；
   - 在只读目录（如 `C:\Program Files` 下且不以管理员运行）首次启动，预期报“不能写入初始密码文件”，并且没有创建账号；换到可写目录后可以正常创建。

## 提交历史注意（git bisect）

local/stabilize 不改写历史。提交 `cc655ad` 单独检出时无法运行：其中 `server/app/bootstrap_admin.py` 导入的 `password_change_guard` 要到下一个提交 `901cf01` 才加入 `identity.py`。用 git bisect 时执行：

```bash
git bisect skip cc655ad
```

只影响这一个提交，相邻的 `864eaf9` 和 `901cf01` 都可以正常测试。
