import { describe, expect, it } from "vitest";
import { alignRows, diffWords, isTable, mergeRows, sentences, tableCells } from "./textDiff";

describe("逐词差异与并排对比（界面验收 M4）", () => {
  it("中文按字、英文按词标出增删", () => {
    expect(diffWords("采用最近邻方法", "采用多假设跟踪方法")).toEqual([
      { type: "eq", text: "采用" }, { type: "del", text: "最近邻" }, { type: "ins", text: "多假设跟踪" }, { type: "eq", text: "方法" }]);
    expect(diffWords("use JPDA now", "use MHT now").filter(p => p.type !== "eq").map(p => p.text)).toEqual(["JPDA", "MHT"]);
  });
  it("按句对齐，只有不同的句子可选", () => {
    const base = "第一句。第二句。第三句。";
    const theirs = "第一句。第二句（李）。第三句。";
    const mine = "第一句。第二句。第三句（我）。";
    const rows = alignRows(base, theirs, mine);
    expect(rows.filter(r => !r.differs).map(r => r.theirs)).toEqual(["第一句。"]);
    const merged = mergeRows(rows, rows.map(r => r.theirs.includes("李") ? "theirs" : "mine"), false);
    expect(merged).toContain("第二句（李）。");
    expect(merged.startsWith("第一句。")).toBe(true);
    expect(sentences("甲。乙！丙")).toEqual(["甲。", "乙！", "丙"]);
  });
  it("表格识别为行并拆单元格", () => {
    const table = "| a | b |\n|---|---|\n| 1 | 2\\|3 |";
    expect(isTable(table)).toBe(true);
    expect(tableCells(table)).toEqual([["a", "b"], ["1", "2\\|3"]]);
  });
});

describe("并排对比的原内容列", () => {
  it("被对方改过的句子仍显示修改前的原句", () => {
    const rows = alignRows("原来的一句。", "原来的一句（张）。", "原来的一句（李）。");
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ base: "原来的一句。", differs: true });
  });
});
