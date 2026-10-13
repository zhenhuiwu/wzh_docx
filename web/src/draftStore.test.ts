import { describe, expect, it } from "vitest";
import { draftKey, draftUsage, indexKey, legacyKey, listDrafts, readDraft, recoverySummary, removeDraft, tabId, verifyDraft, writeDraft, type DraftV2, type StorageLike } from "./draftStore";

class Memory implements StorageLike {
  data = new Map<string, string>();
  limit = Infinity;
  getItem(key: string) { return this.data.get(key) ?? null; }
  setItem(key: string, value: string) {
    const size = [...this.data].reduce((sum, [k, v]) => sum + (k === key ? 0 : v.length), 0) + value.length;
    if (size > this.limit) throw new DOMException("full", "QuotaExceededError");
    this.data.set(key, value);
  }
  removeItem(key: string) { this.data.delete(key); }
}

const draft = (patch: Partial<DraftV2> = {}): DraftV2 => ({
  v: 2, docId: "d1", tabId: "t1", docRevision: 3, mergeBase: 3, savedAt: new Date(2026, 9, 10, 14, 32).getTime(), wholeDocument: true,
  markdown: "# A\n\nmine", blocks: [], changed: 2, headingPaths: [["3 需求", "3.2 软件能力需求"], ["4 接口"]], conflictsEdits: [], pendingSave: null, ...patch,
});

describe("draftStore", () => {
  it("writes per tab, indexes and reads back", () => {
    const storage = new Memory();
    expect(writeDraft(storage, "u1", draft())).toBe("ok");
    expect(writeDraft(storage, "u1", draft({ tabId: "t2", savedAt: 1 }))).toBe("ok");
    expect(storage.getItem(draftKey("u1", "d1", "t1"))).toContain("mine");
    expect(JSON.parse(storage.getItem(indexKey("u1", "d1"))!)).toHaveLength(2);
    expect(verifyDraft(storage, "u1", draft())).toBe(true);
    expect(verifyDraft(storage, "u1", draft({ markdown: "other" }))).toBe(false);
    expect(listDrafts(storage, "u1", "d1").map(item => item.tabId)).toEqual(["t1", "t2"]);
    expect(listDrafts(storage, "u2", "d1")).toEqual([]);
  });
  it("reports quota failures instead of throwing", () => {
    const storage = new Memory();
    storage.limit = 10;
    expect(writeDraft(storage, "u1", draft())).toBe("quota");
    expect(verifyDraft(storage, "u1", draft())).toBe(false);
  });
  it("审查 M1：只删本页面写的、且未被改写的草稿", () => {
    const storage = new Memory();
    writeDraft(storage, "u1", draft({ writer: "page-A", savedAt: 5 }));
    expect(removeDraft(storage, "u1", "d1", "t1", { writer: "page-B" })).toBe(false);
    expect(readDraft(storage, "u1", "d1", "t1")?.markdown).toBe("# A\n\nmine");
    expect(removeDraft(storage, "u1", "d1", "t1", { savedAt: 4 })).toBe(false);
    expect(removeDraft(storage, "u1", "d1", "t1", { writer: "page-A", savedAt: 5 })).toBe(true);
    expect(readDraft(storage, "u1", "d1", "t1")).toBeNull();
  });
  it("审查 S4：列出本账号草稿占用", () => {
    const storage = new (class extends Memory {
      get length() { return this.data.size; }
      key(index: number) { return [...this.data.keys()][index] ?? null; }
    })();
    writeDraft(storage, "u1", draft({ markdown: "x".repeat(100) }));
    writeDraft(storage, "u1", draft({ docId: "d2", tabId: "t9", markdown: "y" }));
    writeDraft(storage, "u2", draft({ docId: "d3" }));
    const usage = draftUsage(storage, "u1");
    expect(usage.map(item => item.docId)).toEqual(["d1", "d2"]);
    expect(usage[0].tabId).toBe("t1");
    expect(usage[0].bytes).toBeGreaterThan(usage[1].bytes);
  });
  it("removes a tab draft and its index entry", () => {
    const storage = new Memory();
    writeDraft(storage, "u1", draft());
    removeDraft(storage, "u1", "d1", "t1");
    expect(readDraft(storage, "u1", "d1", "t1")).toBeNull();
    expect(storage.getItem(indexKey("u1", "d1"))).toBeNull();
  });
  it("converts v1 drafts and deletes the old key only after the copy verifies", () => {
    const storage = new Memory();
    storage.setItem(legacyKey("u1", "d1"), JSON.stringify({ revision: 5, content: "old text", html: "<p>old text</p>" }));
    const [converted] = listDrafts(storage, "u1", "d1");
    expect(converted).toMatchObject({ v: 2, tabId: "legacy", docRevision: 5, mergeBase: 5, markdown: "old text" });
    expect(storage.getItem(legacyKey("u1", "d1"))).toBeNull();
    expect(listDrafts(storage, "u1", "d1")).toHaveLength(1);
    const full = new Memory();
    full.setItem(legacyKey("u1", "d1"), JSON.stringify({ revision: 5, content: "old text", html: "" }));
    full.limit = 0;
    expect(listDrafts(full, "u1", "d1")).toHaveLength(1);
    expect(full.getItem(legacyKey("u1", "d1"))).not.toBeNull();
  });
  it("ignores damaged entries", () => {
    const storage = new Memory();
    storage.setItem(indexKey("u1", "d1"), "not json");
    storage.setItem(legacyKey("u1", "d1"), "{");
    expect(listDrafts(storage, "u1", "d1")).toEqual([]);
  });
  it("summarises a draft for the recovery bar", () => {
    expect(recoverySummary(draft())).toBe("发现 10 月 10 日 14:32 在本机未保存的修改（2 处，3.2 软件能力需求等）。");
  });
  it("keeps one tab id per session", () => {
    const session = new Memory();
    let n = 0;
    const make = () => `tab-${++n}`;
    expect(tabId(session, make)).toBe("tab-1");
    expect(tabId(session, make)).toBe("tab-1");
  });
});
