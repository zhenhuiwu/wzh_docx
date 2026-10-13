import { describe, expect, it } from "vitest";

/** 40 复验 N3：/issues、/workbench 只给有全局角色的人（管理员、审计）。路由可达的页面不能调用，否则项目成员看到 403。 */
const sources = import.meta.glob(["./**/*.vue", "./**/*.ts", "!./**/*.test.ts"], { query: "?raw", import: "default", eager: true }) as Record<string, string>;

describe("页面不依赖全局 /issues", () => {
  it("只有未挂路由的旧工作台 HomeView 引用 listIssues / getWorkbench", () => {
    const users = Object.entries(sources)
      .filter(([path, text]) => path !== "./api/workbench.ts" && /\b(listIssues|getWorkbench)\b/.test(text))
      .map(([path]) => path);
    expect(users).toEqual(["./views/HomeView.vue"]);
    expect(sources["./router.ts"]).not.toMatch(/HomeView/);
  });
});
