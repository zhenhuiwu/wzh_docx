import { describe, expect, it } from "vitest";
import { restoreSplitText, splitRestore } from "./assignmentModel";
import { appendUnsaved, parseUnsaved, unsavedKey } from "./unsavedOthers";

// QA 复验 16 P1-1（a07 G14）：分工变化后恢复草稿，自己负责的修改要保存，他人负责的留本机
describe("恢复草稿按分工拆开", () => {
  const base = [
    { id: "h1", kind: "heading", markdown: "# 我的" }, { id: "p1", kind: "paragraph", markdown: "我的段" },
    { id: "h2", kind: "heading", markdown: "# 附录" }, { id: "p2", kind: "paragraph", markdown: "附录段" },
  ];
  const editable = new Set(["h1", "p1"]);
  it("自己的保存、别人的留下，计数正确", () => {
    const result = splitRestore("# 我的\n\n我的段DRAFTA\n\n# 附录\n\n附录段DRAFTB\n", base, editable);
    expect(result.markdown).toContain("DRAFTA");
    expect(result.markdown).not.toContain("DRAFTB");
    expect(result.saved).toBe(1);
    expect(result.others).toEqual(["附录段DRAFTB"]);
  });
  it("负责人后来改过、我没动过的块不算我的修改（复验 17 a）", () => {
    // 我开始编辑时附录是“附录段”，之后负责人改成了“附录段（王测改）”；我只改了自己的段
    const current = [base[0], base[1], base[2], { id: "p2", kind: "paragraph", markdown: "附录段（王测改）" }];
    const draft = "# 我的\n\n我的段DRAFTA\n\n# 附录\n\n附录段\n";
    const result = splitRestore(draft, current, editable, "# 我的\n\n我的段\n\n# 附录\n\n附录段\n");
    expect(result.others).toEqual([]);
    expect(result.saved).toBe(1);
    expect(splitRestore(draft, current, editable).others).toEqual(["附录段"]);  // 没有基准时仍按旧规则
  });
  it("提示文案与 PM 定稿一致", () => {
    expect(restoreSplitText(1, 1)).toBe("已恢复并保存 1 处；另有 1 处属于他人负责的部分，未保存，已保留在本机草稿");
  });
  it("本机保留：只追加去重，坏数据不报错", () => {
    const first = appendUnsaved([], ["甲", " "], 1);
    expect(appendUnsaved(first, ["甲", "乙"], 2).map(item => item.text)).toEqual(["甲", "乙"]);
    expect(parseUnsaved("{bad")).toEqual([]);
    expect(parseUnsaved(JSON.stringify(first))).toEqual(first);
    expect(unsavedKey("u", "d")).toBe("docwb.a07.stash.u.d.unsaved");
  });
});

import { describePart, partWhere } from "./assignmentModel";
describe("describePart", () => {
  it("names section and current owner", () => {
    const md = "# 需求\n\n甲\n\n# 附录\n\n别人的段（改）";
    const base = [{ id: "h2", kind: "heading", markdown: "# 附录" }] as never;
    const where = describePart(md, "别人的段（改）", base, new Map([["h2", "王测"]]));
    expect(where).toEqual({ section: "附录", owner: "王测" });
    expect(partWhere(where)).toBe("附录 · 王测负责");
  });
});
