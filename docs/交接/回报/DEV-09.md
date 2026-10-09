# DEV-09 回报

日期：2026-09-28。状态：知识库和资料版本已接到服务端。检索、解析和 OCR 没有做。公司资料格式仍未确认。

## 1 本轮结论

知识库只显示当前账号有权查看的库。项目库必须选择项目，不会自动建成全员共享。别人看不到无权库的名称、数量和说明。

同名资料不会覆盖。新文件要登记成新版本，旧版本还在。上传成功只表示文件已保存，状态是已上传，说明里写明尚未解析、不能检索。收紧授权后，列表、详情和文件下载都会重新检查，下载不到原文字节。

停用只改状态，不删除记录。

## 2 改动

| 路径 | 变更 |
|---|---|
| `server/app/knowledge.py` | 知识库、授权、资料和版本 |
| `server/app/files.py` | 下载和预览知识库文件时再检查授权 |
| `server/app/permissions.py` | 补上知识库授权这项固定权限 |
| `server/app/main.py` | 挂上知识库路由 |
| `server/migrations/022_knowledge_bases.sql` | 知识库表 |
| `server/migrations/023_knowledge_access.sql` | 读者和维护者 |
| `server/migrations/024_sources.sql` | 资料 |
| `server/migrations/025_source_versions.sql` | 资料版本 |
| `server/tests/test_knowledge_access.py` | 越权不泄漏、版本不覆盖、收权后不能下载 |
| `server/tests/test_foundation.py` | 清空迁移时先删知识库表 |
| `web/src/api/knowledge.ts` | 知识库请求 |
| `web/src/knowledge.ts` | 去掉本地名单 |
| `web/src/views/KnowledgeView.vue` | 服务端列表和新建 |
| `web/src/views/KnowledgeDetailView.vue` | 资料登记；检索页标明未接入 |
| `web/src/router.ts` | 增加知识库详情地址 |

没有改原始模板，没有把 `tasks.json` 改成完成。D08 仍未确认。

## 3 实际验证

| 检查 | 操作 | 结果 |
|---|---|---|
| 知识库与基础测试 | `uv run pytest tests/test_foundation.py tests/test_knowledge_access.py -q` | 9 passed |
| 类型 | `npx vue-tsc --noEmit` | 退出码 0 |
| 未选项目 | 项目范围但项目为空 | 422 |
| 越权列表 | 另一个维护者查询 | 总数 0，响应里没有库名 |
| 同名资料 | 再次用同名登记 | 409，提示登记新版本 |
| 新版本 | 同一资料上传另一份文件 | 版本为 2，版本 1 仍可读取 |
| 收权 | 先加入读者再清空 | 清空后详情和下载都是 404，下载内容不是文件正文 |

测试用户已清掉。工号 `admin` 已重新创建，口令仍是之前告知的那一个。

## 4 前端截图

这些图都看过。

| 页面 | 宽度 | 结论 |
|---|---|---|
| 知识库 | 1440 | 说明、查询和空列表完整，知识库菜单为当前项 |
| 知识库 | 1280 | 侧栏常驻 |
| 知识库 | 768 | 导航收起，查询和空状态可见 |
| 知识库 | 375 | 导航单列，说明换行，没有把页面撑出屏幕 |

截图目录：`C:\Users\15827\AppData\Local\Temp\doc-workbench-login\`，文件名以 `dev09-` 开头。

## 5 未完成

- 没有解析、发布世代和检索。检索页明确写着未接入。
- 公司允许的资料格式、OCR 和向量检索没有定。当前上传仍用本机已有的文件类型限制。
- 没有引用检查来阻止停用被基线使用的原件，因为文档还没有引用资料。停用不会删除记录。

## 6 下一轮

检索要等资料格式和解析规则。模板仍不能发布。文档清单仍要等已发布的模板版本。
