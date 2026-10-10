import { describe, expect, it } from "vitest";
import { countChars, mergedInBlocks, outlineEntries } from "./workbenchStats";
import { alignRows, blockDiff, mergeRows, tableCellDiffs } from "./textDiff";
import { draftSizeText } from "./saveModel";
import { actionsFor, conflictToggle } from "./conflictModel";

describe("工作台重塑：目录与字数", () => {
  it("字数不计 Markdown 标记、空白和链接地址", () => {
    expect(countChars("## 1 范围\n\n本文档**适用**于 [系统](http://x/y)。")).toBe(3 + 9);
    expect(countChars("- 一\n  - 一点一\n- 二\n\n| a | b |\n| --- | --- |\n| 1 | 2 |")).toBe(1 + 3 + 1 + 4);
    expect(countChars("<!-- 注释 -->\n\n")).toBe(0);
  });
  it("目录：层级相对最浅一级，待编写=无正文且无下级", () => {
    const entries = outlineEntries("## 1 范围\n\n### 1.1 标识\n\n正文。\n\n### 1.2 概述\n\n## 2 引用文档\n");
    expect(entries.map(item => [item.text, item.depth, item.empty, item.chars])).toEqual([
      ["1 范围", 0, false, 0], ["1.1 标识", 1, false, 3], ["1.2 概述", 1, true, 0], ["2 引用文档", 0, true, 0],
    ]);
  });
  it("N4：合并进来的块＝服务器有、本次提交没有的非标题块", () => {
    expect(mergedInBlocks("# 标题\n\n甲\n\n乙（我改）", "# 标题\n\n甲（他改）\n\n乙（我改）")).toEqual(["甲（他改）"]);
    expect(mergedInBlocks("甲", "甲")).toEqual([]);
  });
});

describe("工作台重塑：差异", () => {
  it("S12：版本差异按块比较，相邻删增配成修改并逐词标出", () => {
    const rows = blockDiff("甲\n\n乙\n\n丙", "甲\n\n乙改\n\n丁\n\n丙");
    expect(rows.map(row => row.type)).toEqual(["eq", "mod", "ins", "eq"]);
    expect(rows[1].parts).toEqual([{ type: "eq", text: "乙" }, { type: "ins", text: "改" }]);
    expect(blockDiff("甲\n\n乙", "甲").map(row => row.type)).toEqual(["eq", "del"]);
  });
  it("N7：表格单元格差异计数", () => {
    expect(tableCellDiffs("| a | b |\n| --- | --- |\n| 1 | 2 |", "| a | b |\n| --- | --- |\n| 1 | 3 |\n| 4 | 5 |")).toBe(3);
  });
  it("N7：STRUCTURE 按块对齐，合并结果按空行相连", () => {
    const rows = alignRows("甲。乙。\n\n丙。", "甲。乙。\n\n丙。", "甲。\n\n乙。\n\n丙。", true);
    expect(rows.filter(row => !row.differs).map(row => row.theirs)).toEqual(["丙。"]);
    expect(rows.some(row => row.theirs === "" && row.mine === "" )).toBe(false);
    expect(mergeRows(rows, rows.map(() => "mine"), false, true)).toBe("甲。\n\n乙。\n\n丙。");
  });
  it("N7：表格冲突写“两张表都保留”", () => {
    const table = "| a |\n| --- |\n| 1 |";
    expect(actionsFor({ reason: "MODIFIED", mine: { markdown: table }, theirs: { markdown: table } } as never).map(c => c.label)).toContain("两张表都保留");
    expect(actionsFor({ reason: "MODIFIED", mine: { markdown: "甲" }, theirs: { markdown: "乙" } } as never).map(c => c.label)).toContain("两段都保留");
  });
});

describe("工作台重塑：文案", () => {
  it("N10：不到 0.1 MB", () => {
    expect(draftSizeText(2048)).toBe("不到 0.1 MB");
    expect(draftSizeText(1048576 * 1.26)).toBe("约 1.3 MB");
  });
  it("冲突按钮拆出名称和计数（docbar 用 .count 徽标）", () => {
    expect(conflictToggle({ mineOpen: 2, mineHandled: 0, allOpen: null })).toMatchObject({ name: "冲突", count: 2, label: "冲突 2" });
    expect(conflictToggle({ mineOpen: 0, mineHandled: 3, allOpen: null })).toMatchObject({ name: "冲突", count: 0 });
  });
});
