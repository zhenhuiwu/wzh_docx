import { describe, expect, it } from "vitest";
import { PROJECT_ROUTES, projectPageCrumb } from "./crumbs";

describe("项目内面包屑", () => {
  it("分工页是项目路由，显示“分工”且不归在文档编写下", () => {
    expect(PROJECT_ROUTES).toContain("project-plan");
    expect(projectPageCrumb("project-plan")).toEqual({ label: "分工", documentList: false });
    expect(projectPageCrumb("document").documentList).toBe(true);
    expect(projectPageCrumb("project").label).toBe("首页");
  });
});
