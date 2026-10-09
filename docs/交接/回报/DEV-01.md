# DEV-01 回报

日期：2026-09-27。状态：导航基线已实现，并在本地页面上点过、看过截图。不是后台完成。

## 1 本轮结论

六个入口按规格排好：工作台、项目管理、用户管理、系统管理、知识库、智能助手。系统子项是模板、设置、环境、规则、审计。用户管理只在主入口，`/system/users` 会转到 `/users`。

待办、运行任务、环境、规则、审计明确写成未接入，没有编造成功结果。项目、知识和用户列表仍是本机原型。

## 2 改动

| 路径 | 变更 |
|---|---|
| `web/src/router.ts` | `/users` 为主入口；旧地址重定向；增加环境、规则、审计路由 |
| `web/src/layouts/AppShell.vue` | 六入口顺序；768–1279 收起导航 |
| `web/src/views/HomeView.vue` | 工作台分为待办、最近项目、运行任务 |
| `web/src/views/UsersView.vue` | 列改为姓名、账号、部门、角色、启停，并标明本机原型 |
| `web/src/views/SettingsView.vue` | 设置、环境、规则、审计四种状态 |
| `web/src/views/KnowledgeView.vue` | 标明解析和发布未接入 |
| `web/src/views/AssistantView.vue` | 标明助手未接入 |
| `web/src/styles/console.css` | 工作台间距和未接入说明 |

没有新增依赖，没有数据库迁移，没有改 AGENTS.md，没有改原始模板。`npx vue-tsc --noEmit` 退出码 0。

## 3 实际验证

| 检查 | 操作 | 结果 |
|---|---|---|
| 类型 | `npx vue-tsc --noEmit` | 退出码 0 |
| 六入口 | 登录后读取导航文字 | 顺序为工作台、项目管理、用户管理、系统管理、知识库、智能助手 |
| 旧用户地址 | 打开 `/system/users` | 地址变为 `http://localhost:5173/users` |
| 环境 | 打开 `/system/environment` | 显示未批准、不能连接，没有环境 |
| 1440 | 看工作台截图 | 侧栏常驻，待办和任务写明未接入，最近项目标明本机原型 |
| 1280 | 看环境页截图 | 侧栏常驻，环境为当前项 |
| 768 | 看环境页截图 | 导航收起，顶栏有「菜单」 |
| 375 | 看环境页截图 | 导航单列展开，正文未被裁成溢出 |

截图：

- `C:\Users\15827\AppData\Local\Temp\doc-workbench-login\dev01-1440.png`
- `C:\Users\15827\AppData\Local\Temp\doc-workbench-login\dev01-1280.png`
- `C:\Users\15827\AppData\Local\Temp\doc-workbench-login\dev01-768.png`
- `C:\Users\15827\AppData\Local\Temp\doc-workbench-login\dev01-375.png`

这些图都看过。没有 Git，所以没有提交。

## 4 未完成

- 登录仍是任意非空密码的本机原型。D03 未确认，本任务也不能把它改成真认证。
- 用户部门没有服务端字段，列表里部门显示为「—」。
- 环境、规则、审计没有记录可查，因为没有批准的后端。
- 没有做 DEV-02。D02 和 D12 仍未批准。
