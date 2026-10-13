import { describe, expect, it } from "vitest";
import { buildFragments, changedOps } from "./myBlocks";

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
