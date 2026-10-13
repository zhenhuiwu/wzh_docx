import { describe, expect, it } from "vitest";
import { countChars, mergedInBlocks, outlineEntries } from "./workbenchStats";
import { alignRows, blockDiff, mergeRows, structureRows, tableCellDiffs } from "./textDiff";
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
  it("P2-6：只有模板占位文字的章节算待编写", () => {
    const entries = outlineEntries("## 1 范围\n\n（在此编写本章内容。）\n\n## 2 概述\n\n正文。\n");
    expect(entries.map(item => [item.empty, item.todo])).toEqual([[false, true], [false, false]]);
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
  it("N7 / I-3：表格冲突写“两份表格都保留”并提示会生成两张表", () => {
    const table = "| a |\n| --- |\n| 1 |";
    expect(actionsFor({ reason: "MODIFIED", mine: { markdown: table }, theirs: { markdown: table } } as never).map(c => c.label)).toContain("两份表格都保留");
    expect(actionsFor({ reason: "MODIFIED", mine: { markdown: table }, theirs: { markdown: table } } as never).find(c => c.action === "keep_both")?.hint).toContain("两张表");
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

describe("终验 S-6：STRUCTURE 整块对比", () => {
  it("拆分段落时不按段配对，只给一行整块", () => {
    const rows = structureRows("原来一段，前半。后半。", "原来一段，前半。\n\n后半。", "原来一段，前半改。后半。");
    expect(rows).toHaveLength(1);
    expect(rows[0].theirs).toBe("原来一段，前半。\n\n后半。");
    expect(mergeRows(rows, ["mine"], false, true)).toBe("原来一段，前半改。后半。");
    expect(mergeRows(rows, ["theirs"], false, true)).toBe("原来一段，前半。\n\n后半。");
  });
});

describe("终验 F-M1：载入期间打的字写回", () => {
  it("只认一段里的连续插入，并在新版式里找原段", async () => {
    const { typedInsertion, mapBlockIndex } = await import("./workbenchStats");
    const carry = typedInsertion(["甲", "乙段", "丙"], ["甲", "乙（新字）段", "丙"]);
    expect(carry).toEqual({ index: 1, text: "乙段", offset: 1, insert: "（新字）" });
    expect(mapBlockIndex(["甲", "乙段", "丙"], ["标题", "甲", "乙段", "丙", "乙段"], carry!.index)).toBe(2);
    expect(mapBlockIndex(["甲", "乙段", "丙"], ["甲", "乙段已被他人改", "丙"], carry!.index)).toBe(-1);
    expect(typedInsertion(["甲", "乙"], ["甲改", "乙改"])).toBeNull();
    expect(typedInsertion(["甲", "乙段"], ["甲", "乙"])).toBeNull();
    expect(typedInsertion(["甲"], ["甲", ""])).toBeNull();
    // 有多种对齐时按光标：在“（锁前）（紧”之间打了“（锁定中输入”，光标在“入”后
    const old = "（锁前）（紧", now = "（锁前）（锁定中输入（紧";
    expect(typedInsertion([old], [now], { index: 0, offset: 10 })).toEqual({ index: 0, text: old, offset: 4, insert: "（锁定中输入" });
    expect(typedInsertion([old], [now])?.insert).toBe("锁定中输入（");
  });
});

describe("终审必改：重复段落时写回位置按序列对齐换算", () => {
  it("上方插入/删除段落后，第二个“无。”仍映射到第二个", async () => {
    const { mapBlockIndex } = await import("./workbenchStats");
    const before = ["3 需求", "3.1 功能", "无。", "3.2 性能", "无。", "3.3 接口"];
    expect(mapBlockIndex(before, ["新段", ...before], 4)).toBe(5);
    expect(mapBlockIndex(before, ["新段一", "新段二", ...before], 4)).toBe(6);
    expect(mapBlockIndex(before, ["新段", ...before], 2)).toBe(3);
    expect(mapBlockIndex(before, before.slice(1), 4)).toBe(3);
    // 浏览器探针场景：甲前插入一段，丙被我改过
    const probe = ["甲", "无。", "乙", "无。", "丙（李工改丙）"];
    expect(mapBlockIndex(probe, ["张工新插入", ...probe], 3)).toBe(4);
  });
  it("对齐不唯一或目标段被改时返回 -1（放弃写回）", async () => {
    const { mapBlockIndex } = await import("./workbenchStats");
    // 两个“无。”之间的标题被删：新版只剩一个“无。”，配哪一个都说得通
    expect(mapBlockIndex(["无。", "乙", "无。"], ["无。"], 0)).toBe(-1);
    expect(mapBlockIndex(["无。", "乙", "无。"], ["无。"], 2)).toBe(-1);
    // 目标段在新版里被别人改了
    expect(mapBlockIndex(["甲", "无。", "乙"], ["甲", "无。改", "乙"], 1)).toBe(-1);
    // 越界；超出上限时去掉前后相同部分，文字唯一的仍能定位，重复的放弃
    expect(mapBlockIndex(["甲"], ["甲"], 3)).toBe(-1);
    expect(mapBlockIndex(["甲", "乙"], ["甲", "乙"], 0, 1, 0)).toBe(0);
    expect(mapBlockIndex(["无。", "无。"], ["无。", "无。"], 0, 1, 0)).toBe(-1);
    // 小规模直接精确计算：两个“无。”原样不变时确定配对
    expect(mapBlockIndex(["无。", "无。"], ["无。", "无。"], 0, 1)).toBe(0);
  });
});

describe("终审建议：只改空白的合并提示", () => {
  it("三种情况分别说明", async () => {
    const { whitespaceNoteText } = await import("./saveModel");
    expect(whitespaceNoteText({})).toBe("");
    expect(whitespaceNoteText({ whitespaceDropped: 2 })).toContain("已保留对方的写法");
    expect(whitespaceNoteText({ whitespaceDeleted: 1 })).toContain("已被对方删除");
    expect(whitespaceNoteText({ whitespaceDeleted: 1 })).not.toContain("保留对方的写法");
    expect(whitespaceNoteText({ theirsWhitespaceOverridden: 1 })).toContain("已采用你的写法");
  });
});
