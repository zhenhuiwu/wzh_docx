import { describe, expect, it } from "vitest";

/** 40 复验 N3：/issues、/workbench 只给有全局角色的人（管理员、审计）。路由可达的页面不能调用，否则项目成员看到 403。 */
const sources = import.meta.glob(["./**/*.vue", "./**/*.ts", "!./**/*.test.ts"], { query: "?raw", import: "default", eager: true }) as Record<string, string>;

describe("页面不依赖全局 /issues", () => {
  it("前端不再调用全局 /issues、/workbench（未挂路由的旧 HomeView 已删除）", () => {
    const users = Object.entries(sources).filter(([, text]) => /["'`]\/(issues|workbench)\b/.test(text)).map(([path]) => path);
    expect(users).toEqual([]);
    expect(Object.keys(sources)).not.toContain("./views/HomeView.vue");
  });
});
