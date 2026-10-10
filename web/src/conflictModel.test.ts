import { describe, expect, it } from "vitest";
import { ResolveKeys, actionsFor, changedByText, classifyResolveError, conflictCount, conflictOpensPanel } from "./conflictModel";

describe("冲突 N 与面板（⑧ ⑪）", () => {
  it("冲突数只用 mineOpen，summary 失败才用本地兜底", () => {
    expect(conflictCount({ mineOpen: 2, allOpen: null }, 5)).toBe(2);
    expect(conflictCount(null, 1)).toBe(1);
  });
  it("自动保存触发的冲突不打开面板，主动保存打开", () => {
    expect(conflictOpensPanel("auto")).toBe(false);
    expect(conflictOpensPanel("manual")).toBe(true);
  });
});

describe("合一接口（⑫）", () => {
  it("同一请求体重试复用键，换请求体或成功后换新键", () => {
    let n = 0;
    const keys = new ResolveKeys(() => `k${++n}`);
    expect(keys.keyFor("a")).toBe("k1");
    expect(keys.keyFor("a")).toBe("k1");
    expect(keys.keyFor("b")).toBe("k2");
    keys.done();
    expect(keys.keyFor("b")).toBe("k3");
  });
  it("CONFLICT_ALREADY_RESOLVED 静默归档；再次冲突原地替换；503 重试", () => {
    expect(classifyResolveError({ status: 409, code: "CONFLICT_ALREADY_RESOLVED" })).toBe("archive");
    expect(classifyResolveError({ status: 409, code: "OBJECT_CONFLICT" })).toBe("reconflict");
    expect(classifyResolveError({ status: 503, code: "DATABASE_BUSY" })).toBe("retry");
    expect(classifyResolveError({ status: 0 })).toBe("retry");
    expect(classifyResolveError({ status: 422, code: "AI_FORMAT_INVALID" })).toBe("format");
    expect(classifyResolveError({ status: 403, code: "FORBIDDEN" })).toBe("error");
  });
  it("每种原因的处理方式", () => {
    expect(actionsFor({ reason: "MODIFIED", mine: {}, theirs: {} }).map(a => a.action)).toEqual(["manual", "keep_mine", "use_theirs", "keep_both"]);
    expect(actionsFor({ reason: "DELETED", mine: { markdown: "x" }, theirs: { deleted: true } }).map(a => a.placement ?? a.action)).toEqual(["after_nearest_live", "section_end", "discard"]);
    expect(actionsFor({ reason: "FORMAT", mine: {}, theirs: {} }).map(a => a.action)).toEqual(["use_theirs"]);
  });
  it("他人更新提示", () => {
    expect(changedByText([{ id: "1", name: "李工", objects: 2 }, { id: "2", name: "王工", objects: 1 }])).toBe("李工 等 2 人更新了 3 处内容，已合并到你的文档。");
    expect(changedByText([])).toBe("");
  });
});

describe("冲突入口与他人更新文案（界面验收 M2/M3/M6）", () => {
  it("全部处理完仍能进入，负责人只有成员冲突时显示成员冲突", async () => {
    const { conflictToggle, othersUpdatedText } = await import("./conflictModel");
    expect(conflictToggle({ mineOpen: 2, mineHandled: 0, allOpen: 5 })).toMatchObject({ label: "冲突 2", tab: "mine" });
    expect(conflictToggle({ mineOpen: 0, mineHandled: 0, allOpen: 3 })).toMatchObject({ label: "成员冲突 3", tab: "members" });
    expect(conflictToggle({ mineOpen: 0, mineHandled: 1, allOpen: null })).toMatchObject({ label: "冲突", tone: "neutral" });
    expect(conflictToggle({ mineOpen: 0, mineHandled: 0, allOpen: null })).toBeNull();
    expect(conflictToggle(null, 1)).toMatchObject({ label: "冲突 1" });
    expect(othersUpdatedText(["李工"], 1, true)).toBe("已显示 李工 的最新修改。");
    expect(othersUpdatedText(["李工", "王工", "李工"], 3, false)).toBe("李工 等 2 人更新了 3 处内容。你的修改保存后会自动显示他们的更新。");
  });
});
