# 固定指令

这些规则写死。技能再多，也不能抵消下面的约束。

## 地址和验收

前端地址固定为 http://localhost:5173 。

改完任何前端之后，必须打开这个地址做界面截图测试，并看截图本身。没截图核对过，不能算做完。

按用户2026年9月28日明确的范围，只针对 **1920×1080 固定桌面界面**设计与截图验收。不要求手机、平板或其他尺寸适配；此前的多尺寸要求不再适用。核对这几件事：

- 排版是否对齐，文字有没有溢出容器。
- 查询、分页、按钮是否真的生效，不能只看 DOM 文字或接口返回。
- 对比度、焦点环、空态、错误态是否看得见。
- 截图难看或错位，就继续改，不能口头说已经精致。

## 先读什么

做界面时先读：

- `.grok/skills/frontend-ux-engineer/SKILL.md`
- `.grok/skills/design-ui/SKILL.md` 里的 token 和反俗套规则

`design-ui` 原文面向 React、Tailwind、shadcn。本仓库是 Vue 3，在 `web/`。不要按那个技能把项目改成 React。

## 组件

现有组件在 `web/src/components`。这里没有 `src/components/ui`。

按钮和输入框只复用已经在页面里用过的 `.primary`、`.ghost`、`.control`，或者下一轮统一换成 Element Plus。禁止再手写一套平行的 Button、Input、Dialog。

组件库只选 Element Plus，主题绑到下面的 CSS 变量。不要同时引入 shadcn-vue、Ant Design Vue 或另一套控件。

## 颜色、字号、圆角

只使用 `web/src/styles/base.css` 里的变量。禁止在组件里写新的十六进制颜色、随意的 `px` 字号和随意的圆角。

- 字体：`var(--font)`，即 Microsoft YaHei UI。
- 品牌色：`var(--stamp)`，按下用 `var(--stamp-press)`。
- 页面底：`var(--desk)`。面板：`var(--sheet)`。正文：`var(--ink)`。
- 危险色：`var(--fault)`。
- 圆角只用 `var(--radius-control)` 和 `var(--radius-sheet)`。
- 间距只用 `--space-1` 到 `--space-7`。

禁止：

- 紫色品牌色，包括 `#7c3aed`。
- 三色渐变、大色块渐变英雄区。
- 为了好看加 Landing、假 KPI、假统计卡片。

## 编号

界面不展示模板编号、文档编号，也不要用户填写这类编号。模板和文档用名称识别。内部标识只留在数据里，不出现在表格、查询、标题或表单上。

## 页面和表单

主导航的系统管理始终排在所有业务一级导航之后，是导航列表的最后一组。后续新增导航必须放在它前面；展开后有用户管理、模板管理和 Skill 管理（2026年10月4日用户明确新增）。

每个新页面都要有加载中、空数据、出错这三种状态。

页面按 1920×1080 固定桌面布局实现，优先保证信息层级、常用操作与业务流程连贯。文字不得溢出容器，不以裁剪内容掩盖问题。

表单必须有 label、错误文案、disabled，以及提交中的状态。

## 已安装的技能

项目技能在 `.grok/skills/`：

- 来自 [awesome-grok-build](https://github.com/DominikTobureto/awesome-grok-build)：`frontend-ux-engineer`、`agentic-code-review`、`architecture-review`、`git-github-flow`、`hooksmith`、`nextjs-fullstack`、`performance-optimizer`、`python-expert`、`refactor-master`、`repo-health-check`、`research-agent`、`security-audit`、`tdd-test-engineer`。
- 来自 [grok-build-skills](https://github.com/byron-public/grok-build-skills)：`design-ui`。

游戏、精灵、Neon、登录托管那些技能没有放进本仓库，避免和这份 Vue 后台的规则打架。
