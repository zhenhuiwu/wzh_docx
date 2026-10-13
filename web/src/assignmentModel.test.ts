import { describe, expect, it } from "vitest";
import { keepMineOnly, lockedText, mineRegions, ownerNames, stashText, violationText, violationWhere } from "./assignmentModel";

describe("A07 编写人员文案（16 §4.8）", () => {
  it("姓名与气泡", () => {
    expect(ownerNames([{ name: "王测" }, { name: "孙敏" }])).toBe("王测、孙敏");
    expect(ownerNames([{ name: "甲" }, { name: "乙" }, { name: "丙" }])).toBe("甲、乙等");
    expect(lockedText("王测", "李工")).toBe("该段由 王测 负责，你不能修改。如需修改请联系项目负责人 李工。");
    expect(lockedText("", "李工")).toBe("该段尚未分配，你不能修改。如需编写请联系项目负责人 李工。");
    expect(stashText("延迟不大于")).toBe("刚才输入的“延迟不大于”已暂存。");
  });
  it("违规说明与位置", () => {
    expect(violationText({ type: "modify", owners: [{ name: "王测" }] })).toBe("修改了 王测 负责的内容");
    expect(violationText({ type: "heading_structure" })).toContain("章节范围");
    expect(violationWhere({ type: "modify", headingPath: ["需求", "性能"], excerpt: "一二三四五六七八九十一二三四五六七八九十多" })).toBe("需求 › 性能 · 一二三四五六七八九十一二三四五六七八九十");
  });
  it("连续区域", () => {
    expect(mineRegions(["a", "b", "c", "d", "e"], new Set(["b", "c", "e"]))).toEqual(["b", "e"]);
  });
});

describe("只保存我负责的部分", () => {
  const base = [
    { id: "h1", kind: "heading", markdown: "# 我的" }, { id: "p1", kind: "paragraph", markdown: "我的段" },
    { id: "h2", kind: "heading", markdown: "# 别人的" }, { id: "p2", kind: "paragraph", markdown: "别人的段" },
  ];
  const editable = new Set(["h1", "p1"]);
  it("保留我的修改和新段，别人的还原并进暂存", () => {
    const mine = "# 我的\n\n我的段（改）\n\n我新写的\n\n# 别人的\n\n别人的段（越权）\n\n写在别人后面\n";
    const result = keepMineOnly(mine, base, editable);
    expect(result.markdown).toBe("# 我的\n\n我的段（改）\n\n我新写的\n\n# 别人的\n\n别人的段\n");
    expect(result.stashed).toEqual(["别人的段（越权）", "写在别人后面"]);
  });
  it("删除别人的块会被恢复", () => {
    const result = keepMineOnly("# 我的\n\n我的段\n\n# 别人的\n", base, editable);
    expect(result.markdown).toContain("别人的段");
  });
  it("以开始编辑时那一版为底：别人的块还原、没改过的块原样（交给服务端三方合并）；块标识与输出一一对应", () => {
    const result = keepMineOnly("# 我的\n\n我的段（改）\n\n# 别人的\n\n别人的段\n", base, editable);
    expect(result.markdown).toBe("# 我的\n\n我的段（改）\n\n# 别人的\n\n别人的段\n");
    expect(result.stashed).toEqual([]);
    expect(result.blockIds).toEqual(["h1", "p1", "h2", "p2"]);
  });
  it("重复文字：我改了别人的“无。”只进暂存，不变成新段（27 复验 M-4）", () => {
    const dup = [
      { id: "h", kind: "heading", markdown: "# 章" }, { id: "a", kind: "paragraph", markdown: "无。" },
      { id: "b", kind: "paragraph", markdown: "无。" }, { id: "c", kind: "paragraph", markdown: "无。" },
    ];
    const result = keepMineOnly("# 章\n\n无（我改）。\n\n无。\n\n无（我改）。\n", dup, new Set(["c"]), ["h", "a", "b", "c"]);
    expect(result.markdown).toBe("# 章\n\n无。\n\n无。\n\n无（我改）。\n");
    expect(result.stashed).toEqual(["无（我改）。"]);
    expect(result.blockIds).toEqual(["h", "a", "b", "c"]);
  });
  it("按真实 ID：我删了别人的段不删（照基线保留），我删了自己的段才删（27 复验 F4）", () => {
    const base = [
      { id: "h", kind: "heading", markdown: "# 章" }, { id: "a", kind: "paragraph", markdown: "甲。" },
      { id: "b", kind: "paragraph", markdown: "乙。" },
    ];
    const other = keepMineOnly("# 章\n\n乙。\n", base, new Set(["b"]), ["h", "b"]);
    expect(other.markdown).toBe("# 章\n\n甲。\n\n乙。\n");
    expect(other.blockIds).toEqual(["h", "a", "b"]);
    const mine = keepMineOnly("# 章\n\n甲。\n", base, new Set(["b"]), ["h", "a"]);
    expect(mine.markdown).toBe("# 章\n\n甲。\n");
    expect(mine.blockIds).toEqual(["h", "a"]);
    // 新块（ID 不在基线里或重复）跟在别人的段后 → 暂存，不进正文
    const added = keepMineOnly("# 章\n\n甲。\n\n新增。\n\n乙。\n", base, new Set(["b"]), ["h", "a", "zz", "b"]);
    expect(added.markdown).toBe("# 章\n\n甲。\n\n乙。\n");
    expect(added.stashed).toEqual(["新增。"]);
  });
});
