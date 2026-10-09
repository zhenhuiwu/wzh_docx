# AI 测试用例管理（2026年10月3日）

## 当前实现

一级导航顺序：项目、测试用例管理、系统管理。系统管理仅有用户管理和模板管理。

新建测试任务填写所属项目、测试计划 Word、待补充 Word（可选）、前后端源码目录和服务地址、测试账号、允许修改的数据范围及并发参数。

DeepAgents 使用现有 AI 配置，通过只读工具读取计划全文及相关源码，提取测试项、预期结果和执行步骤。未读取源码或计划不完整时不会产生“提取成功”。可查看步骤及生成的测试代码，勾选或单独执行测试项。

- 功能测试：Playwright Chromium 在1920×1080下实际打开网页、填表、点击、断言，并保存每一步完整截图。凭据使用占位符，密码字段截图遮罩。
- 单元测试：执行 Python pytest 或已有 Node 内置测试文件。生成的 Python 测试必须实际导入并调用所引用源码。失败、跳过、无测试及缺少环境均不会记为通过。
- 并发测试：实际 GET/HEAD 请求，保存每次状态码、耗时、失败数、平均值和 P95；范围限制为1–20并发、1–200请求。
- SQLite 保存任务、提取结果和每次执行快照；可停止、查看历史记录、重新执行。服务重启中断的任务会明确标记失败。
- 下载 Word：在原 DOCX 后追加实测记录、步骤、截图及日志，不覆盖原文件。操作区域截图用于报告阅读，完整1920截图保留为证据；沿用原样式、表格及页眉。

## 本轮实际验收

使用独立的中性计算器页面、Python函数及健康检查接口，调用已经配置的 AI 提取并执行7项：3项网页功能、3项函数单元测试、1项并发测试，全部通过。并发20次请求、5并发，失败0，平均6.13ms、P95 9.39ms；这些数值只代表验收样例。

实际点击验证：创建任务、提取、执行、停止、历史记录、截图预览、Word下载、名称查询、分页/跳转、空态、加载态和错误态。11个临时任务用于分页验收，已归档并从业务数据中清理。

前端 `npm run build` 通过。后端 `tests/test_test_execution.py`、`tests/test_test_management.py`、`tests/test_sqlite_runtime.py` 共3个集成检查通过，包含实际子进程和 HTTP 请求以及失败分支。

下载的报告用 Microsoft Word 导出 PDF，共9页，逐页看图核对；原文段落、原表格、styles.xml、header1.xml保留，10张截图写入。证据：

- `web/.qa/test-management-20261003/`：1920×1080界面截图。
- `server/.qa-test-management-20261003/verification.json`：测试项与执行结果。
- `server/.qa-test-management-20261003/evidence/`：原始运行记录、截图、日志与临时数据备份。
- `server/.qa-test-management-20261003/downloaded-report.docx`、`report.pdf`、`render/`：实际下载及 Word 排版验收。

## 真实项目仍待输入

用户已要求同时测试真实项目，但还未提供测试计划/待补充 Word 路径、前后端源码目录、服务地址、测试账号及允许修改的数据范围。不能将上述中性样例验收声称为真实项目验收。

当前并发执行器只支持 GET/HEAD；其他测试框架、任意原文表格的定点回填尚未实现。待提供真实项目后，按实际计划补齐所需操作与测试框架，再执行并生成真实报告。

## 代码入口

`web/src/views/TestCasesView.vue`、`web/src/api/testTasks.ts`、`server/app/test_management.py`、`server/app/test_executor.py`、`server/migrations/104_test_management.sql`。

前端固定 http://localhost:5173，后端 http://127.0.0.1:8001；两项服务保持运行。首次环境安装需在 server 执行 `uv sync --group dev`，并用该虚拟环境执行 `python -m playwright install chromium --only-shell`。
