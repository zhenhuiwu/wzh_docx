import { describe, expect, it } from "vitest";
import { crossSummary } from "./baselineCompare";

describe("基线对比摘要", () => {
  it("不在后一个基线里的文档不算“有变化”，单独列出", () => {
    expect(crossSummary(["changed", "removed", "removed", "same"], "B1", "B2")).toBe("1 份文档有变化，2 份未纳入 B2");
    expect(crossSummary(["removed", "added"], "B1", "B2")).toBe("没有文档变化，1 份为 B1 之后加入，1 份未纳入 B2");
    expect(crossSummary(["same"], "B1", "当前")).toBe("没有文档变化");
  });
});
