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
    expect(actionsFor({ reason: "MODIFIED", mine: {}, theirs: {} }).map(a => a.action)).toEqual(["keep_mine", "use_theirs", "manual", "keep_both"]);
    expect(actionsFor({ reason: "DELETED", mine: { markdown: "x" }, theirs: { deleted: true } }).map(a => a.placement ?? a.action)).toEqual(["after_nearest_live", "section_end", "discard"]);
    expect(actionsFor({ reason: "FORMAT", mine: {}, theirs: {} }).map(a => a.action)).toEqual(["use_theirs"]);
  });
  it("他人更新提示", () => {
    expect(changedByText([{ id: "1", name: "李工", objects: 2 }, { id: "2", name: "王工", objects: 1 }])).toBe("李工 等 2 人更新了 3 处内容，已合并到你的文档。");
    expect(changedByText([])).toBe("");
  });
});
