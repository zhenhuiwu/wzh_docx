import { describe, expect, it } from "vitest";
import { assignBlockIds, splitBlocks } from "./markdownBlocks";

// 期望值由服务端 app.markdown_blocks.split 生成（块数、种类、正文必须一致）
const SAMPLES: string[] = ["# 标题\n正文一\n\n## 二\n\n- a\n- b\n  续\n\n1. x\n\n   缩进续\n2. y\n", "| a | b |\n|---|---|\n| 1 | 2 |\n\n```py\nx\n\n```\n\n> 引用\n> 二\n\n![图](docx-image:abc)\n\n段落\n===\n\n<div>x</div>\n\n    code\n", "\n\n文本\r\n继续\r\n\r\n# H\r\n", "段\n# 紧跟标题\n段2\n```\nc\n```\n尾"];
const EXPECTED: Array<Array<[string, string, number | null]>> = [[["heading", "# 标题", 1], ["paragraph", "正文一", null], ["heading", "## 二", 2], ["list_item", "- a", null], ["list_item", "- b\n  续", null], ["list_item", "1. x\n\n   缩进续", null], ["list_item", "2. y", null]], [["table", "| a | b |\n|---|---|\n| 1 | 2 |", null], ["code", "```py\nx\n\n```", null], ["other", "> 引用\n> 二", null], ["image", "![图](docx-image:abc)", null], ["other", "段落\n===", null], ["other", "<div>x</div>", null], ["other", "    code", null]], [["paragraph", "文本\r\n继续", null], ["heading", "# H", 1]], [["paragraph", "段", null], ["heading", "# 紧跟标题", 1], ["paragraph", "段2", null], ["code", "```\nc\n```", null], ["paragraph", "尾", null]]];

describe("splitBlocks 与服务端一致", () => {
  SAMPLES.forEach((sample, index) => it(`样例 ${index}`, () => {
    expect(splitBlocks(sample).map(b => [b.kind, b.body, b.level])).toEqual(EXPECTED[index]);
  }));
});

describe("assignBlockIds", () => {
  const base = [{ id: "h", kind: "heading", markdown: "# 一" }, { id: "p", kind: "paragraph", markdown: "甲" }, { id: "q", kind: "paragraph", markdown: "乙" }];
  it("未改动全部配上", () => expect(assignBlockIds("# 一\n\n甲\n\n乙\n", base)).toEqual(["h", "p", "q"]));
  it("就地修改沿用原 ID", () => expect(assignBlockIds("# 一\n\n甲改\n\n乙\n", base)).toEqual(["h", "p", "q"]));
  it("新增块为 null", () => expect(assignBlockIds("# 一\n\n甲\n\n新\n\n乙\n", base)).toEqual(["h", "p", null, "q"]));
  it("删除后其余对上", () => expect(assignBlockIds("# 一\n\n乙\n", base)).toEqual(["h", "q"]));
  it("段落不配标题", () => expect(assignBlockIds("改\n\n甲\n\n乙\n", base)).toEqual([null, "p", "q"]));
  it("重复正文只用一次 ID", () => expect(assignBlockIds("甲\n\n甲\n", [{ id: "p", kind: "paragraph", markdown: "甲" }])).toEqual(["p", null]));
});
