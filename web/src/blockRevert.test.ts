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
  it("找不到对应段落时不改动", () => {
    const current = saved.replace("第二段。", "第二段改。");
    expect(revertBlock(saved, current, "完全不同的文字")).toBeNull();
  });
});
