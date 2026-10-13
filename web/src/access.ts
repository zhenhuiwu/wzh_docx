/**
 * 导航与路由的权限表（单一来源）。
 * - router.ts 按 ROUTE_PERMISSIONS 写入 meta.permission，无权限时由 AppShell 显示统一的无权限页；
 * - AppShell 按 NAV_ITEMS 显隐导航：导航可见 ⇒ 路由权限满足（navVisible 同时检查两者）。
 * 一致性由 server/tests/test_frontend_access.py 调 web/tests/access-check.mjs 校验。
 * 本文件不 import 其他模块，便于 node 直接加载。界面显隐只是体验，真正的权限由服务端校验。
 */

/** null：路由本身不拦截（数据可见性由服务端决定）。 */
export const ROUTE_PERMISSIONS: Record<string, string | null> = {
  projects: null, // 项目可见性按成员关系由服务端决定（phase2 S1）
  project: null,
  "project-documents": null,
  document: null,
  knowledge: "knowledge.read",
  "knowledge-base": "knowledge.read",
  "test-cases": "tests.read",
  templates: "templates.read",
  template: "templates.read",
  "template-editor": "templates.read",
  users: "users.read",
  // Skill 列表 GET 对所有会话开放（工作台写作助手要用），直接输地址进入为只读；导航只对 system.manage 显示。
  "writing-skills": null,
};

export interface NavEntry {
  route: string;
  label: string;
  group: "main" | "system";
  /** 比路由更严格的导航显示条件（导航隐藏但路由只读可见）。 */
  navPermission?: string;
}

export const NAV_ITEMS: NavEntry[] = [
  { route: "projects", label: "项目", group: "main" },
  { route: "test-cases", label: "测试用例管理", group: "main" },
  { route: "knowledge", label: "知识库", group: "main" },
  { route: "users", label: "用户管理", group: "system" },
  // 模板：有 templates.read 的角色（如项目创建者）可经地址只读查看，导航只给维护者。
  { route: "templates", label: "模板管理", group: "system", navPermission: "templates.manage" },
  { route: "writing-skills", label: "Skill 管理", group: "system", navPermission: "system.manage" },
];

export function routePermission(name: string): string | null {
  return ROUTE_PERMISSIONS[name] ?? null;
}

export function routeAllowed(name: string, permissions: readonly string[]): boolean {
  const needed = routePermission(name);
  return needed === null || permissions.includes(needed);
}

export function navVisible(item: NavEntry, permissions: readonly string[]): boolean {
  if (!routeAllowed(item.route, permissions)) return false;
  return !item.navPermission || permissions.includes(item.navPermission);
}
