# DEV-08 回报

日期：2026-09-27。状态：模板可以按名称登记到服务端，发布和格式确认仍被拒绝。Word 验收没有做，也没有把原件改掉。

## 1 本轮结论

模板管理不再使用浏览器里的本地目录。登记需要名称、适用场景和已上传的原件。只有内容块、还没有试填样例和格式确认时，发布会被拒绝。页面上勾选或调用格式确认，也不能把格式记为已通过。

438C 原件仍在原来的目录，没有被改写，也没有批量登记成已适配。以前浏览器里那份带内容块的本地记录没有当作真实映射导入。

## 2 改动

| 路径 | 变更 |
|---|---|
| `server/app/templates.py` | 模板、版本、验收任务、发布和格式确认 |
| `server/app/docx_renderer.py` | 没有办公环境时拒绝生成可发布结果 |
| `server/app/main.py` | 挂上模板路由 |
| `server/migrations/017_templates.sql` | 模板表 |
| `server/migrations/018_template_versions.sql` | 版本表 |
| `server/tests/test_templates.py` | 只有内容块时不能发布，格式确认不会落成已通过 |
| `server/tests/test_foundation.py` | 清空迁移时先删模板表 |
| `web/src/api/templates.ts` | 模板请求 |
| `web/src/api/files.ts` | 上传带上请求键 |
| `web/src/api/client.ts` | 允许调用方传入请求键 |
| `web/src/templates.ts` | 去掉本地目录 |
| `web/src/views/TemplatesView.vue` | 服务端列表和登记 |
| `web/src/views/TemplateDetailView.vue` | 版本和发布失败说明 |

没有改原始模板，没有把 `tasks.json` 改成完成。D06、D07 仍未确认。

## 3 实际验证

| 检查 | 操作 | 结果 |
|---|---|---|
| 模板与基础测试 | `uv run pytest tests/test_templates.py tests/test_foundation.py -q` | 9 passed |
| 类型 | `npx vue-tsc --noEmit` | 退出码 0 |
| 只有内容块就发布 | 登记模板并提交一个内容块后申请发布 | 409，`PRECONDITION_MISSING`，说明缺少试填样例和格式确认 |
| 格式确认 | 以“看起来可以”申请通过 | 409，数据库里的确认时间和验收通过标志仍为空 |
| Word 试填 | 未执行 | 没有批准的办公软件、字体和合格成品，不能把验收说成通过 |

测试清空过用户表。结束后已重新创建工号 `admin`，口令仍是之前告知的那一个。

## 4 前端截图

这些图都看过。页面说明不能发布，并且没有模板编号。

| 页面 | 宽度 | 结论 |
|---|---|---|
| 模板管理 | 1440 | 侧栏常驻，查询和空状态完整 |
| 模板管理 | 1280 | 系统菜单展开，模板为当前项 |
| 模板管理 | 768 | 导航收起，查询和重置可见 |
| 模板管理 | 375 | 导航单列，说明换行，查询按钮仍在卡片内 |

截图目录：`C:\Users\15827\AppData\Local\Temp\doc-workbench-login\`，文件名以 `dev08-` 开头。

## 5 未完成

- 不能发布，也不能记录格式已通过。
- 没有把 20 份原件自动登记成已适配。
- 验收任务会停在待核实，不会变成验收通过。
- 长表、多图和特殊字符的试填没有在指定软件里看过。

## 6 下一轮

知识库仍要等允许的资料格式。文档实例要等真正发布的模板版本。在 Word 验收环境确定之前，不要把模板标成已发布。
