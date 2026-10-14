import { describe, expect, it } from "vitest";
import { assignBlockIds, keepalivePatch, splitBlocks } from "./markdownBlocks";

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

describe("assignBlockIds 大文档不错位（27 M-1）", () => {
  const build = (count: number) => Array.from({ length: count }, (_v, k) => (k % 50 === 0 ? `# 第 ${k} 章` : `第 ${k} 段：正文内容 ${k}`));
  for (const count of [2600, 3150, 6000]) {
    it(`${count} 块：一处插入 + 远处改字`, () => {
      const texts = build(count);
      const base = texts.map((text, k) => ({ id: `o${k}`, kind: text.startsWith("#") ? "heading" : "paragraph", markdown: text }));
      const mine = [...texts];
      mine[count - 10] = `${mine[count - 10]}（改）`;
      mine.splice(100, 0, "新插入的段落");
      const ids = assignBlockIds(mine.join("\n\n") + "\n", base);
      const expected = mine.map(text => (text === "新插入的段落" ? null : `o${texts.indexOf(text.replace("（改）", ""))}`));
      expect(ids.filter((id, k) => id !== expected[k]).length).toBe(0);
    });
  }
  it("间隙不等长时按相似度配对", () => {
    const base = [{ id: "h", kind: "heading", markdown: "# 一" }, { id: "p", kind: "paragraph", markdown: "原来的一段很长的正文" }, { id: "z", kind: "heading", markdown: "# 二" }];
    expect(assignBlockIds("# 一\n\n新段\n\n原来的一段很长的正文（改）\n\n# 二\n", base)).toEqual(["h", null, "p", "z"]);
  });
});

describe("keepalivePatch（关页按块保存）", () => {
  const base = [
    { id: "h", kind: "heading", revision: 3, markdown: "# 1 范围" },
    { id: "a", kind: "paragraph", revision: 1, markdown: "第一段内容比较长一些。" },
    { id: "b", kind: "paragraph", revision: 2, markdown: "第二段内容也比较长。" },
    { id: "c", kind: "paragraph", revision: 5, markdown: "第三段的内容在这里。" },
  ];
  it("只带改过的块：改段、回车新增段、删段", () => {
    const content = "# 1 范围\n\n第一段内容比较长一些。改了\n\n新加的一段\n\n第二段内容也比较长。";
    expect(keepalivePatch(content, base)).toEqual([
      { op: "update", objectId: "a", baseRevision: 1, markdown: "第一段内容比较长一些。改了" },
      { op: "insert", tempId: "ka1", afterObjectId: "a", markdown: "新加的一段" },
      { op: "delete", objectId: "c", baseRevision: 5 },
    ]);
  });
  it("没改返回 null；段落移动表示为插入新段 + 删除原段", () => {
    expect(keepalivePatch(base.map(o => o.markdown).join("\n\n"), base)).toBeNull();
    expect(keepalivePatch(["# 1 范围", base[3].markdown, base[1].markdown, base[2].markdown].join("\n\n"), base)).toEqual([
      { op: "insert", tempId: "ka1", afterObjectId: "h", markdown: base[3].markdown },
      { op: "delete", objectId: "c", baseRevision: 5 },
    ]);
  });
});

import { revisionedFromSave } from "./markdownBlocks";
describe("revisionedFromSave（保存响应直接给出关页按块保存的基准）", () => {
  const content = "# 一\n\n第一段。\n\n第二段。\n";
  it("块数对上：按位置配 id/修订，正文取保存后的块", () => {
    const base = revisionedFromSave(content, [["a", 1], ["b", 3], ["c", 2]]);
    expect(base?.map(item => [item.id, item.revision, item.markdown.trim()])).toEqual([["a", 1, "# 一"], ["b", 3, "第一段。"], ["c", 2, "第二段。"]]);
    // 以它为基准：只改第二段 → 只有一条 update，带服务器上的修订号
    expect(keepalivePatch("# 一\n\n第一段。\n\n第二段改。\n", base!)).toEqual([{ op: "update", objectId: "c", baseRevision: 2, markdown: "第二段改。" }]);
  });
  it("块数对不上或没有：null", () => {
    expect(revisionedFromSave(content, [["a", 1]])).toBeNull();
    expect(revisionedFromSave(content, undefined)).toBeNull();
  });
});
