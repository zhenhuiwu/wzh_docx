import { describe, expect, it } from "vitest";
import { aiFragmentTooLarge, applyAiOperations, buildFragments, changedOps, contextView, isPlaceholderText, isTemplateFiller, wordHeadingIds } from "./myBlocks";

const o = (id: string, kind: string, markdown: string, revision = 1) => ({ id, kind, markdown, revision });
const r = (objectId: string, owners: string[], editable = true) => ({ objectId, kind: "paragraph", owners: owners.map((id) => ({ id, name: id })), via: "explicit", viaObjectId: null, editable, readonlyReason: null });

describe("buildFragments", () => {
  const objects = [o("h1", "heading", "# 3 需求"), o("p1", "paragraph", "他人段"), o("h2", "heading", "## 3.2 功能"), o("p2", "paragraph", "我的一"), o("p3", "paragraph", "我的二"), o("p4", "paragraph", "他人二"), o("p5", "paragraph", "我的三")];
  const resolved = [r("p1", ["b"]), r("p2", ["me"]), r("p3", ["me"]), r("p4", ["b"]), r("p5", ["me"]), r("h2", ["b"])];
  it("splits my consecutive blocks into fragments with heading path and context", () => {
    const fragments = buildFragments(objects, resolved, "me");
    expect(fragments.map((f) => f.blocks.map((b) => b.id))).toEqual([["p2", "p3"], ["p5"]]);
    expect(fragments[0].headingPath).toEqual(["3 需求", "3.2 功能"]);
    expect(fragments[0].before).toBe("他人段"); // 跨过标题取最近的正文块（复验 P1-5）
    expect(fragments[0].after).toBe("他人二");
    expect(fragments[1].before).toBe("他人二");
    expect(fragments.map((f) => f.headingIndex)).toEqual([1, 1]);
  });
  it("整节分给我（标题与正文都是我的）时，前后文跨过标题取相邻章节的正文（复验 P1-5）", () => {
    const doc = [o("h1", "heading", "# 1 范围"), o("a", "paragraph", "上一节末段"), o("h2", "heading", "# 2 引用"), o("b", "paragraph", "我的段"), o("h3", "heading", "# 3 需求"), o("c", "paragraph", "下一节首段")];
    const [only] = buildFragments(doc, [r("h2", ["me"]), r("b", ["me"]), r("a", ["x"]), r("c", ["x"])], "me");
    expect([only.before, only.after]).toEqual(["上一节末段", "下一节首段"]);
    // 模板占位不当上下文
    const [skip] = buildFragments([o("x", "paragraph", "真正的前文"), o("y", "paragraph", "（在此编写本章内容。）"), ...doc.slice(2)], [r("h2", ["me"]), r("b", ["me"])], "me");
    expect(skip.before).toBe("真正的前文");
    // 相邻章节也是我的：不重复显示
    const [first, second] = buildFragments(doc, [r("a", ["me"]), r("b", ["me"])], "me");
    expect([first.after, second.before]).toEqual([null, null]);
  });
  it("uses null heading index before the first heading and limits AI scope (Q-8)", () => {
    const [first] = buildFragments([o("p0", "paragraph", "开头"), ...objects], [r("p0", ["me"])], "me");
    expect(first.headingIndex).toBeNull();
    expect(aiFragmentTooLarge(first, {})).toBe(false);
    expect(aiFragmentTooLarge(first, { p0: "字".repeat(6001) })).toBe(true);
  });
  it("skips blocks I own but cannot edit", () => {
    expect(buildFragments(objects, [r("p2", ["me"], false)], "me")).toEqual([]);
  });
});

describe("changedOps", () => {
  it("sends only changed, non-empty blocks with their revision", () => {
    const blocks = [o("a", "paragraph", "一", 3), o("b", "paragraph", "二", 4), o("c", "paragraph", "三", 5)];
    expect(changedOps(blocks, { a: "一", b: "改二", c: "  " })).toEqual([{ op: "update", objectId: "b", baseRevision: 4, markdown: "改二" }]);
  });
});

describe("isPlaceholderText", () => {
  it("recognises template placeholders only", () => {
    expect(isPlaceholderText("（在此编写本章内容。）")).toBe(true);
    expect(isPlaceholderText(" (请在此填写接口说明) ")).toBe(true);
    expect(isPlaceholderText("接口 2 的输入说明。")).toBe(false);
    expect(isPlaceholderText("（公共框架：引用文档，各文档引用同一内容。）")).toBe(false); // 工作台待编写统计不把框架章节算作待写
    expect(isPlaceholderText("（在此编写本章内容。）补充了正文")).toBe(false);
  });
});

describe("applyAiOperations", () => {
  it("applies replace ops from the done event, skips others and duplicates", () => {
    const drafts: Record<string, string> = { a: "旧", b: "乙" };
    const seen = new Set<string>();
    const ops = [{ op: "replace", blockId: "a", content: "新" }, { op: "insertAfter", blockId: "a", content: "插" }, { op: "replace", blockId: "x", content: "越界" }];
    expect(applyAiOperations(undefined, new Set(["a", "b"]), drafts, seen)).toEqual({ applied: 0, skipped: 0 });
    expect(applyAiOperations(ops, new Set(["a", "b"]), drafts, seen)).toEqual({ applied: 1, skipped: 2 });
    expect(applyAiOperations(ops, new Set(["a", "b"]), drafts, seen)).toEqual({ applied: 0, skipped: 0 });
    expect(drafts).toEqual({ a: "新", b: "乙" });
  });
});

describe("wordHeadingIds", () => {
  it("collects object ids flagged isWordHeading by /objects", () => {
    expect([...wordHeadingIds([{ id: "a", isWordHeading: true }, { id: "b" }, { id: "c", isWordHeading: true }])]).toEqual(["a", "c"]);
  });
});

describe("contextView / 公共框架占位 / 前后文章节", () => {
  it("表格转成行列（去掉分隔行），普通文字去掉 Markdown 标记", () => {
    expect(contextView("| 名称 | **版本** |\n|---|---|\n| A | 1 |")).toEqual({ kind: "table", rows: [["名称", "版本"], ["A", "1"]], more: 0 });
    expect(contextView("## 标题\n- [链接](x) 与 `代码`")).toEqual({ kind: "text", text: "标题\n链接 与 代码" });
  });
  it("公共框架说明文字算模板占位，不当上下文", () => {
    expect(isTemplateFiller("（公共框架：软件运行环境，各文档引用同一内容。）")).toBe(true);
    const doc = [o("h1", "heading", "# 1 范围"), o("a", "paragraph", "真正的前文"), o("f", "paragraph", "（公共框架：术语，各文档引用同一内容。）"), o("h2", "heading", "## 1.2 引用"), o("b", "paragraph", "我的段")];
    const [only] = buildFragments(doc, [r("b", ["me"]), r("h2", ["me"])], "me");
    expect([only.before, only.beforeSection]).toEqual(["真正的前文", "1 范围"]);
  });
});
