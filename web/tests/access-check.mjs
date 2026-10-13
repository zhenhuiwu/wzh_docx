// 导航权限与路由权限一致性检查（09 审查 S6）。
// 用 node 直接运行（--experimental-strip-types 加载 .ts；Node 22.18+ 默认开启），不依赖 vitest，避免与 local/phase2 的 vitest 配置冲突。
// 用法：node --experimental-strip-types web/tests/access-check.mjs '<{"roles": {"角色": ["权限"...]}, "all": ["权限"...]}>'
// 由 server/tests/test_frontend_access.py 调用；输出每个角色可见的导航与可进入的路由（JSON）。
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { NAV_ITEMS, ROUTE_PERMISSIONS, navVisible, routeAllowed } from "../src/access.ts";

const src = (path) => readFileSync(fileURLToPath(new URL(path, import.meta.url)), "utf8");
const input = JSON.parse(process.argv[2] ?? '{"roles":{},"all":[]}');
const problems = [];

// 1. router.ts 里每个命名路由都在权限表里登记（公开页、纯重定向、404 除外），表里也没有多余的名字。
const EXEMPT = new Set(["login", "change-password", "not-found", "project-materials", "project-reviews"]);
const routerNames = [...src("../src/router.ts").matchAll(/name:\s*"([^"]+)"/g)].map((m) => m[1]);
const declared = new Set(routerNames.filter((name) => !EXEMPT.has(name)));
for (const name of declared) if (!(name in ROUTE_PERMISSIONS)) problems.push(`路由 ${name} 没有在 access.ts 的 ROUTE_PERMISSIONS 登记`);
for (const name of Object.keys(ROUTE_PERMISSIONS)) if (!declared.has(name)) problems.push(`ROUTE_PERMISSIONS 里的 ${name} 不是 router.ts 的命名路由`);
if (!/withPermissions\(/.test(src("../src/router.ts"))) problems.push("router.ts 没有用 withPermissions 写入 meta.permission");

// 2. 每个导航项指向登记过的路由；AppShell 的导航显隐全部经过 show('路由名')，且覆盖全部导航项。
for (const item of NAV_ITEMS) if (!(item.route in ROUTE_PERMISSIONS)) problems.push(`导航 ${item.label} 指向未登记的路由 ${item.route}`);
const shell = src("../src/layouts/AppShell.vue");
const shown = new Set([...shell.matchAll(/show\('([^']+)'\)/g)].map((m) => m[1]));
for (const item of NAV_ITEMS) {
  if (item.route !== "projects" && !shown.has(item.route)) problems.push(`AppShell 的导航 ${item.label} 没有用 show('${item.route}') 控制显隐`);
}
for (const name of shown) if (!NAV_ITEMS.some((item) => item.route === name)) problems.push(`AppShell 用了 show('${name}')，但 NAV_ITEMS 没有这个导航项`);
if (/can\("(users|templates|tests|knowledge|system)\.[a-z]+"\)\s*\)/.test(shell)) problems.push("AppShell 里仍有硬编码的导航权限判断");

// 3. 用到的权限名都是后端定义过的。
const all = new Set(input.all);
if (all.size) {
  const used = [...Object.values(ROUTE_PERMISSIONS), ...NAV_ITEMS.map((item) => item.navPermission)].filter(Boolean);
  for (const permission of used) if (!all.has(permission)) problems.push(`权限 ${permission} 后端没有定义`);
}

// 4. 对每个角色：导航可见 ⇒ 路由可进入。
const result = {};
for (const [role, permissions] of Object.entries(input.roles)) {
  const nav = NAV_ITEMS.filter((item) => navVisible(item, permissions)).map((item) => item.route);
  const routes = Object.keys(ROUTE_PERMISSIONS).filter((name) => routeAllowed(name, permissions));
  for (const name of nav) if (!routes.includes(name)) problems.push(`角色 ${role}：导航 ${name} 可见但路由会显示无权限`);
  result[role] = { nav, routes };
}

if (problems.length) {
  console.error(problems.join("\n"));
  process.exit(1);
}
console.log(JSON.stringify(result));
