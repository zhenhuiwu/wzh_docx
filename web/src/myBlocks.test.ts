import { describe, expect, it } from "vitest";
import { aiFragmentTooLarge, applyAiOperations, buildFragments, changedOps, isPlaceholderText, wordHeadingIds } from "./myBlocks";

const o = (id: string, kind: string, markdown: string, revision = 1) => ({ id, kind, markdown, revision });
const r = (objectId: string, owners: string[], editable = true) => ({ objectId, kind: "paragraph", owners: owners.map((id) => ({ id, name: id })), via: "explicit", viaObjectId: null, editable, readonlyReason: null });

describe("buildFragments", () => {
  const objects = [o("h1", "heading", "# 3 需求"), o("p1", "paragraph", "他人段"), o("h2", "heading", "## 3.2 功能"), o("p2", "paragraph", "我的一"), o("p3", "paragraph", "我的二"), o("p4", "paragraph", "他人二"), o("p5", "paragraph", "我的三")];
  const resolved = [r("p1", ["b"]), r("p2", ["me"]), r("p3", ["me"]), r("p4", ["b"]), r("p5", ["me"]), r("h2", ["b"])];
  it("splits my consecutive blocks into fragments with heading path and context", () => {
    const fragments = buildFragments(objects, resolved, "me");
    expect(fragments.map((f) => f.blocks.map((b) => b.id))).toEqual([["p2", "p3"], ["p5"]]);
    expect(fragments[0].headingPath).toEqual(["3 需求", "3.2 功能"]);
    expect(fragments[0].before).toBeNull(); // 前面是标题，已在路径里
    expect(fragments[0].after).toBe("他人二");
    expect(fragments[1].before).toBe("他人二");
    expect(fragments.map((f) => f.headingIndex)).toEqual([1, 1]);
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
    expect(isPlaceholderText("（公共框架：引用文档，各文档引用同一内容。）")).toBe(false);
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
