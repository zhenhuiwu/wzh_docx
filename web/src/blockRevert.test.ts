import { describe, expect, it } from "vitest";
import { revertBlock } from "./blockRevert";

const saved = "# 1 范围\n\n## 1.1 标识\n\n文档标识号：TN/x；\n\n第二段。\n\n第三段。\n";

describe("撤销这一段的修改（界面验收 M1）", () => {
  it("只换回失败的那一段，其他修改保留", () => {
    const current = saved.replace("文档标识号：TN/x；", "文档标识号：ABC-01；").replace("第三段。", "第三段改。");
    expect(revertBlock(saved, current, "文档标识号：ABC-01；")).toBe(saved.replace("第三段。", "第三段改。"));
  });
  it("Markdown 转义与粗体不影响匹配", () => {
    const current = saved.replace("文档标识号：TN/x；", "文档标识号：**A\\|B**；");
    expect(revertBlock(saved, current, "文档标识号：A|B；")).toBe(saved);
  });
  it("界面复验 N1：被拒的删除整段——按原内容插回原位置，其他修改保留", () => {
    const current = saved.replace("文档标识号：TN/x；\n\n", "").replace("第三段。", "第三段改。");
    const restored = revertBlock(saved, current, "第二段。", "文档标识号：TN/x；");
    expect(restored).toBe(saved.replace("第三段。", "第三段改。"));
  });
  it("界面复验 N1：删除时连带改了相邻块，整个片段换回保存时的原文", () => {
    const current = saved.replace("## 1.1 标识\n\n文档标识号：TN/x；", "## 1.1 标识文档");
    expect(revertBlock(saved, current, "1.1 标识文档", "文档标识号：TN/x；")).toBe(saved);
  });
  it("界面复验 N1：被删段落仍在新内容里（不是删除）时退回按失败块匹配", () => {
    const current = saved.replace("文档标识号：TN/x；", "文档标识号：ABC；");
    expect(revertBlock(saved, current, "文档标识号：ABC；", "完全无关")).toBe(saved);
  });
  it("找不到对应段落时不改动", () => {
    const current = saved.replace("第二段。", "第二段改。");
    expect(revertBlock(saved, current, "完全不同的文字")).toBeNull();
  });
});
