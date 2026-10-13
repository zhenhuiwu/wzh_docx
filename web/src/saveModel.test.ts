import { describe, expect, it } from "vitest";
import { accessText, backoffMs, capsuleLabel, capsuleState, changedBlockCount, classifySaveError, isOffline, leaveDecision, locationText, myChangesFileName, revisionConflictText } from "./saveModel";

const flags = { readonly: false, blocked: false, offline: false, retrying: false, saving: false, dirty: false };

describe("capsuleState", () => {
  it("readonly is exclusive and blocked outranks the rest", () => {
    expect(capsuleState({ ...flags, readonly: true, blocked: true, dirty: true })).toBe("readonly");
    expect(capsuleState({ ...flags, blocked: true, offline: true, saving: true })).toBe("blocked");
    expect(capsuleState({ ...flags, offline: true, retrying: true })).toBe("offline");
    expect(capsuleState({ ...flags, retrying: true, saving: true })).toBe("retrying");
    expect(capsuleState({ ...flags, saving: true, dirty: true })).toBe("saving");
    expect(capsuleState({ ...flags, dirty: true })).toBe("dirty");
    expect(capsuleState(flags)).toBe("clean");
  });
});

describe("classifySaveError", () => {
  it("separates transient, conflict, permission and content failures", () => {
    expect(classifySaveError({ status: 0 })).toBe("retry");
    expect(classifySaveError({ status: 503, code: "DATABASE_BUSY" })).toBe("retry");
    expect(classifySaveError({ status: 503, code: "WORD_BUSY" })).toBe("retry");
    expect(classifySaveError({ status: 409, code: "OBJECT_CONFLICT" })).toBe("conflict");
    expect(classifySaveError({ status: 409, code: "REVISION_CONFLICT" })).toBe("stale");
    expect(classifySaveError({ status: 409, code: "BASE_REVISION_UNAVAILABLE" })).toBe("blocked");
    expect(classifySaveError({ status: 422, code: "AI_FORMAT_INVALID" })).toBe("blocked");
    expect(classifySaveError({ status: 422, code: "IDEMPOTENCY_KEY_REUSED" })).toBe("rekey");
    expect(classifySaveError({ status: 403, code: "FORBIDDEN" })).toBe("readonly");
    expect(classifySaveError({ status: 404, code: "NOT_FOUND" })).toBe("gone");
  });
  it("A05 R3：项目已归档的 409 归为只读，不当作别人更新", () => {
    expect(classifySaveError({ status: 409, code: "PROJECT_ARCHIVED" })).toBe("readonly");
  });
});

describe("retry timing", () => {
  it("backs off from 2s to a 30s ceiling", () => {
    expect([1, 2, 3, 4, 5, 6, 10].map(backoffMs)).toEqual([2000, 4000, 8000, 16000, 30000, 30000, 30000]);
  });
  it("goes offline after 3 failures, 30s of failure or a browser offline event", () => {
    expect(isOffline(2, 0, 10000, true)).toBe(false);
    expect(isOffline(3, 0, 1000, true)).toBe(true);
    expect(isOffline(1, 0, 30001, true)).toBe(true);
    expect(isOffline(0, null, 99999, false)).toBe(true);
  });
});

describe("leaveDecision", () => {
  it("only offers leaving when the draft was read back", () => {
    expect(leaveDecision(0, false)).toBe("leave");
    expect(leaveDecision(2, true)).toBe("dialog");
    expect(leaveDecision(2, false)).toBe("download-only");
  });
});

describe("texts", () => {
  it("describes locations without internal ids", () => {
    expect(locationText({ headingPath: ["3 需求", "3.2 能力"], kind: "paragraph", ordinal: 2 })).toBe("3 需求 › 3.2 能力 · 第 2 段");
    expect(locationText({ headingPath: [], kind: "table", ordinal: 1 })).toBe("文档开头（第一个标题之前） · 第 1 个表格");
    expect(locationText({ headingPath: ["1 范围"], kind: "heading" })).toBe("1 范围 · 标题行");
    expect(locationText({ headingPath: ["1 范围"], kind: "table", ordinal: 1, label: "表 3-2 接口" })).toBe("1 范围 · 表 3-2 接口");
  });
  it("names the last actor of a revision conflict", () => {
    const now = new Date(2026, 9, 10, 15, 0);
    expect(revisionConflictText({ lastActor: { id: "u2", name: "李工" }, lastUpdatedAt: new Date(2026, 9, 10, 14, 32).toISOString() }, now)).toBe("李工 于 14:32 更新了该文档，请刷新后重试。");
    expect(revisionConflictText({ lastActorIsMe: true }, now)).toBe("你在其他窗口更新了该文档，请刷新后重试。");
    expect(revisionConflictText(undefined, now)).toBe("文档已被更新，请刷新后重试。");
  });
  it("labels capsules and access errors", () => {
    expect(capsuleLabel("clean", { savedAt: 1000, now: 2000 })).toBe("刚刚保存");
    expect(capsuleLabel("offline", { pending: 3 })).toBe("离线 · 本机保留 3 处修改");
    expect(accessText({ status: 404 })).toBe("项目不存在或你无权访问。");
    expect(accessText({ status: 403, details: { ownerName: "王工" } })).toContain("王工");
    // 三审：审计员不提示“联系负责人开通”
    expect(accessText({ status: 403, details: { projectRole: "auditor", ownerName: null } })).not.toContain("联系");
    expect(myChangesFileName("需求", new Date(2026, 9, 10, 14, 32))).toBe("需求-我的修改-1010-1432.md");
  });
});

describe("changedBlockCount", () => {
  it("counts changed top-level blocks", () => {
    const base = "# A\n\none\n\ntwo\n\nthree";
    expect(changedBlockCount(base, base)).toBe(0);
    expect(changedBlockCount(base, "# A\n\nONE\n\ntwo\n\nthree")).toBe(1);
    expect(changedBlockCount(base, "# A\n\none\n\nnew\n\ntwo\n\nthree")).toBe(1);
    expect(changedBlockCount(base, "# A\n\nONE\n\ntwo\n\nTHREE")).toBe(2);
    // 设计师 S5：列表符号后空格多少不算修改
    expect(changedBlockCount("- 甲\n- 乙\n\n段", "-   甲\n-   乙\n\n段")).toBe(0);
    expect(changedBlockCount("-   甲\n\n段", "- 甲\n\n段改")).toBe(1);
  });
});

import { waitForCloseSaves } from "./saveModel";
describe("waitForCloseSaves（40 R5.1 关页保存晚落库）", () => {
  const clock = () => { let t = 0; return { now: () => t, sleep: async (ms: number) => { t += ms; } }; };
  it("keeps polling until a late close-save lands", async () => {
    const c = clock(); let calls = 0;
    const r = await waitForCloseSaves(["a"], async () => ++calls >= 4, { waitMs: 12000, pollMs: 400, ...c });
    expect(r).toEqual({ landed: ["a"], waiting: [], stopped: false });
    expect(calls).toBe(4);
  });
  it("gives up after waitMs and reports what never landed", async () => {
    const c = clock();
    const r = await waitForCloseSaves(["a", "b"], async item => item === "a", { waitMs: 1000, pollMs: 400, ...c });
    expect(r.landed).toEqual(["a"]); expect(r.waiting).toEqual(["b"]); expect(c.now()).toBeGreaterThanOrEqual(1000);
  });
  it("treats check errors as not landed and stops when the document changes", async () => {
    const c = clock(); let current = true;
    const r = await waitForCloseSaves(["a"], async () => { current = false; throw new Error("x"); }, { waitMs: 5000, pollMs: 400, ...c, stillCurrent: () => current });
    expect(r).toEqual({ landed: [], waiting: ["a"], stopped: true });
  });
});
