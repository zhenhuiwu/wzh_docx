import { describe, expect, it } from "vitest";
import { changedPlaces, computeOps, dedupeIds, otherTabOpen, type BaselineEntry } from "./documentObjects";

const baseline = new Map<string, BaselineEntry>([
  ["a", { revision: 1, md: "第一段。" }], ["b", { revision: 2, md: "第二段。" }], ["c", { revision: 0, md: "第三段。" }],
]);
const order = ["a", "b", "c"];
let n = 0;
const temp = () => `t${++n}`;

describe("块级补丁 ops（documentObjects ①–③）", () => {
  it("未改动 → 无 op；改一段 → update 带基线修订号", () => {
    expect(computeOps([{ objectId: "a", md: "第一段。" }, { objectId: "b", md: "第二段。" }, { objectId: "c", md: "第三段。" }], baseline, order, temp)).toEqual([]);
    expect(computeOps([{ objectId: "a", md: "第一段。" }, { objectId: "b", md: "改了。" }, { objectId: "c", md: "第三段。" }], baseline, order, temp))
      .toEqual([{ op: "update", objectId: "b", baseRevision: 2, markdown: "改了。" }]);
  });
  it("新块 → insert，锚点为前后有 ID 的块；消失 → delete", () => {
    n = 0;
    const ops = computeOps([{ objectId: "a", md: "第一段。" }, { objectId: null, md: "新段。" }, { objectId: "c", md: "第三段。" }], baseline, order, temp);
    expect(ops).toEqual([
      { op: "insert", tempId: "t1", afterObjectId: "a", beforeObjectId: "c", markdown: "新段。" },
      { op: "delete", objectId: "b", baseRevision: 2 },
    ]);
  });
  it("连续插入：后一个锚在前一个的 tempId 上；空块忽略", () => {
    n = 0;
    const ops = computeOps([{ objectId: null, md: "甲。" }, { objectId: null, md: "  " }, { objectId: null, md: "乙。" },
      { objectId: "a", md: "第一段。" }, { objectId: "b", md: "第二段。" }, { objectId: "c", md: "第三段。" }], baseline, order, temp);
    expect(ops.map(op => op.op === "insert" ? [op.tempId, op.afterObjectId, op.beforeObjectId] : op.op)).toEqual([["t1", null, "a"], ["t2", "t1", "a"]]);
  });
  it("顺序变化只移动最少的块", () => {
    const ops = computeOps([{ objectId: "c", md: "第三段。" }, { objectId: "a", md: "第一段。" }, { objectId: "b", md: "第二段。" }], baseline, order, temp);
    expect(ops).toEqual([{ op: "move", objectId: "c", afterObjectId: null }]);
  });
  it("重复 ID（回车拆段/复制）保留最像原段的一个，其余按新块插入", () => {
    const blocks = dedupeIds([{ objectId: "b", md: "第二" }, { objectId: "b", md: "第二段。" }], baseline);
    expect(blocks.map(block => block.objectId)).toEqual([null, "b"]);
    n = 0;
    const ops = computeOps([{ objectId: "a", md: "第一段。" }, { objectId: "a", md: "第一段。" }, { objectId: "b", md: "第二段。" }, { objectId: "c", md: "第三段。" }], baseline, order, temp);
    expect(ops).toEqual([{ op: "insert", tempId: "t1", afterObjectId: "a", beforeObjectId: "b", markdown: "第一段。" }]);
  });
  it("不在基线里的 ID（别的文档粘贴来的）按新块处理", () => {
    n = 0;
    const ops = computeOps([{ objectId: "a", md: "第一段。" }, { objectId: "x", md: "外来。" }, { objectId: "b", md: "第二段。" }, { objectId: "c", md: "第三段。" }], baseline, order, temp);
    expect(ops).toEqual([{ op: "insert", tempId: "t1", afterObjectId: "a", beforeObjectId: "b", markdown: "外来。" }]);
  });
  it("“处”按对象计：移动又修改记 1 处", () => {
    expect(changedPlaces([{ op: "move", objectId: "c", afterObjectId: null }, { op: "update", objectId: "c", baseRevision: 0, markdown: "x" },
      { op: "insert", tempId: "t9", afterObjectId: null, beforeObjectId: null, markdown: "y" }])).toBe(2);
  });
});

describe("多标签 presence（⑥）", () => {
  const self = { tabId: "tab-1", userId: "u1" };
  it("同账号另一个标签 → 提示；自己、别的账号、坏消息 → 不提示", () => {
    expect(otherTabOpen({ type: "presence", tabId: "tab-2", userId: "u1" }, self)).toBe(true);
    expect(otherTabOpen({ type: "presence-reply", tabId: "tab-2", userId: "u1" }, self)).toBe(true);
    expect(otherTabOpen({ type: "presence", tabId: "tab-1", userId: "u1" }, self)).toBe(false);
    expect(otherTabOpen({ type: "presence", tabId: "tab-2", userId: "u2" }, self)).toBe(false);
    expect(otherTabOpen({ type: "bye", tabId: "tab-2", userId: "u1" }, self)).toBe(false);
    expect(otherTabOpen("presence", self)).toBe(false);
  });
});
