import { describe, expect, it } from "vitest";
import type { EditorBlock } from "./api/templates";
import { cellChanges, changesMarkdown, styleDisplayName, chapterIds, conflictFieldLabel, createBlock, deleteCheck, forSave, impactText, insertAfter, isCellLocked, moveBlock, moveChapter,
  lockTag, newBlockId, publishImpactLine, rebindHeadings, removeBlocks, sizeLabel, summaryText, valueLabel } from "./templateEditing";

function block(id: string, kind: string, level: number | null = null, extra: Partial<EditorBlock> = {}): EditorBlock {
  return { id, path: `body/${id}`, kind: kind as EditorBlock["kind"], text: id, sourceText: id, headingLevel: level, headingId: null,
    tableId: null, protected: false, isTable: false, lock: { text: null, delete: null }, ...extra };
}
const ids = (blocks: EditorBlock[] | null) => (blocks ?? []).map(item => item.id);

const doc = () => [
  block("h1", "heading", 1), block("p1", "body"),
  block("h2", "heading", 1), block("h21", "heading", 2), block("p21", "body"), block("t", "table", null, { isTable: true }),
  block("c1", "body", null, { tableId: "t", path: "body/t/row/0/cell/0/p/0" }),
  block("h3", "heading", 1), block("p3", "body"),
];

describe("A06 模板块编辑", () => {
  it("生成 block-<16 位十六进制> 的新块标识", () => {
    expect(newBlockId()).toMatch(/^block-[0-9a-f]{16}$/);
    expect(newBlockId()).not.toBe(newBlockId());
  });
  it("章节包含下级标题与表格单元格", () => {
    expect(chapterIds(doc(), "h2")).toEqual(["h2", "h21", "p21", "t", "c1"]);
    expect(chapterIds(doc(), "h21")).toEqual(["h21", "p21", "t", "c1"]);
  });
  it("整章上移、下移，遇到上级标题就停", () => {
    expect(ids(moveChapter(doc(), "h3", -1))).toEqual(["h1", "p1", "h3", "p3", "h2", "h21", "p21", "t", "c1"]);
    expect(ids(moveChapter(doc(), "h1", 1))).toEqual(["h2", "h21", "p21", "t", "c1", "h1", "p1", "h3", "p3"]);
    expect(moveChapter(doc(), "h1", -1)).toBeNull();
    expect(moveChapter(doc(), "h21", -1)).toBeNull();
    expect(moveChapter(doc(), "h21", 1)).toBeNull();
  });
  it("普通块与相邻块交换，表格带着单元格移动", () => {
    expect(ids(moveBlock(doc(), "t", -1))).toEqual(["h1", "p1", "h2", "h21", "t", "c1", "p21", "h3", "p3"]);
    expect(moveBlock(doc(), "h1", -1)).toBeNull();
  });
  it("插入在表格单元格之后，新块绑定前一个标题", () => {
    const inserted = insertAfter(doc(), "t", createBlock("body", "新段落"));
    expect(inserted[7].text).toBe("新段落");
    rebindHeadings(inserted);
    expect(inserted[7].headingId).toBe("h21");
  });
  it("删除检查：表格、目录、锁定段落不能删", () => {
    const blocks = doc();
    blocks[1].lock = { text: null, delete: "含书签（保留原样）" };
    expect(deleteCheck(blocks, ["p1"])).toBe("含书签（保留原样）");
    expect(deleteCheck(blocks, chapterIds(blocks, "h2"))).toContain("表格不能在线删除");
    expect(deleteCheck(blocks, ["p3"])).toBeNull();
    expect(ids(removeBlocks(blocks, ["t"]))).not.toContain("c1");
  });
  it("指导保留绑定；未落位的指导不绑定", () => {
    const blocks = [block("h1", "heading", 1), block("g", "guidance", null, { headingId: "h1" }), block("h2", "heading", 1),
      block("g2", "guidance", null, { headingId: "h1" }), block("u", "guidance", null, { unplaced: true, headingId: "h2" })];
    rebindHeadings(blocks);
    expect(blocks.map(item => item.headingId)).toEqual([null, "h1", null, "h1", null]);
  });
  it("保存时去掉派生字段，未改的单元格不提交", () => {
    const table = block("t", "table", null, { isTable: true, cells: [["a"]], baseCells: [["a"]], tableEditable: "cells", lockedCells: [] });
    const saved = forSave({ schemaVersion: 1, revision: 1, reviewed: true, sourceHash: "x", blocks: [table, block("p", "body")] });
    expect(saved.blocks[0]).not.toHaveProperty("cells");
    expect(saved.blocks[0]).not.toHaveProperty("lock");
    table.cells = [["b"]];
    expect(forSave({ schemaVersion: 1, revision: 1, reviewed: true, sourceHash: "x", blocks: [table] }).blocks[0].cells).toEqual([["b"]]);
  });
  it("锁定单元格判断", () => {
    const table = block("t", "table", null, { isTable: true, tableEditable: "partial", lockedCells: [[0, 1]] });
    expect(isCellLocked(table, 0, 1)).toBe(true);
    expect(isCellLocked(table, 0, 0)).toBe(false);
    expect(isCellLocked({ ...table, tableEditable: "none" }, 0, 0)).toBe(true);
  });
});

describe("A06 文案", () => {
  it("字号、行距、缩进按中文习惯显示", () => {
    expect(sizeLabel(12)).toBe("小四（12 磅）");
    expect(sizeLabel(11)).toBe("11 磅");
    expect(valueLabel("lineSpacing", "1.5")).toBe("1.5 倍");
    expect(valueLabel("lineSpacing", "1")).toBe("单倍");
    expect(valueLabel("lineSpacing", "exact:22")).toBe("固定值 22 磅");
    expect(valueLabel("firstLineIndent", { unit: "chars", value: 2 })).toBe("2 字符");
  });
  it("影响提示：清除直接格式的段数", () => {
    const item = { style: "正文", prop: "size", before: 10.5, after: 12, affectedParagraphs: 120, directFormatParagraphs: 80, willClear: 80, blockIds: [], directBlockIds: [], truncated: false };
    expect(impactText(item)).toBe("使用“正文”的段落共 120 段，其中 80 段自带字号，将一并清除，统一为新值。");
    expect(publishImpactLine(item)).toBe("样式：正文 字号 五号（10.5 磅）→小四（12 磅），影响 120 段（清除 80 段的直接字号）。");
  });
  it("对比摘要与冲突字段名", () => {
    expect(summaryText({ headings: 2, body: 5, tables: 1, guidance: 3, format: 1 })).toBe("标题 2 处、正文 5 处、表格 1 处、指导 3 处、格式 1 处");
    expect(summaryText({ headings: 0, body: 0, tables: 0, guidance: 0, format: 0 })).toBe("没有变化");
    expect(conflictFieldLabel("cell:0:2", "")).toBe("单元格（第 1 行第 3 列）");
    expect(conflictFieldLabel("style:正文:size", "")).toBe("样式“正文·字号”");
  });
  it("锁定原因转成短标签", () => {
    expect(lockTag("此内容含书签，无法安全删除；请在原稿中修改。")).toBe("含书签（保留原样）");
    expect(lockTag("此段包含图片、链接、域或复杂 Word 内容，无法安全替换；请在 Word 中修改后上传为新草稿。")).toBe("含图片、链接、域或复杂 Word 内容（保留原样）");
    expect(lockTag(null)).toBe("");
  });
  it("表格对比只列改过的格子", () => {
    expect(cellChanges([["a", "b"], ["c", "d"]], [["a", "b"], ["c", "X"]])).toEqual([{ label: "第 2 行第 2 列", before: "d", after: "X" }]);
    expect(cellChanges([["a"]], [["a"]])).toEqual([]);
  });
});

describe("changesMarkdown（D-17 可读修改清单）", () => {
  const block = (id: string, kind: string, text: string, extra: Record<string, unknown> = {}) =>
    ({ id, kind, text, path: id, headingLevel: kind === "heading" ? 1 : null, headingId: null, tableId: null, ...extra }) as any;
  it("按章节列出新增、删除、改字、样式与顺序", () => {
    const base = [block("h1", "heading", "范围"), block("b1", "body", "旧正文"), block("b2", "body", "要删的"), block("h2", "heading", "引用文件")];
    const mine = [block("h2", "heading", "引用文件"), block("h1", "heading", "范围"), block("b1", "body", "新正文"), block("n1", "body", "新增段", { path: null })];
    const text = changesMarkdown({ title: "SRS", baseLabel: "第 1 版", base, mine, baseStyles: { 正文: { size: 10.5, spaceBefore: null } }, styles: { 正文: { size: 12, spaceBefore: null } } });
    expect(text).toContain("# SRS · 我的修改");
    expect(text).toContain("- 修改正文 【范围】");
    expect(text).toContain("> 旧正文");
    expect(text).toContain("> 新正文");
    expect(text).toContain("- 新增正文 【范围】");
    expect(text).toContain("- 删除正文 【范围】");
    expect(text).toContain("调整了章节或段落的顺序");
    expect(text).toContain("样式“正文”字号：五号（10.5 磅） → 小四（12 磅）");
    expect(text).not.toContain("段前");
  });
  it("没有修改时说明", () => {
    const base = [block("b1", "body", "x")];
    expect(changesMarkdown({ title: "T", baseLabel: "第 2 版", base, mine: base, baseStyles: {}, styles: {} })).toContain("没有修改。");
  });
});

describe("styleDisplayName", () => {
  it("常见内置样式显示中文名", () => {
    expect(styleDisplayName("Normal Indent")).toBe("正文缩进");
    expect(styleDisplayName("toc 2")).toBe("目录 2");
    expect(styleDisplayName("表格文字")).toBe("表格文字");
    expect(styleDisplayName("Fancy X")).toBe("原有样式 Fancy X");
    expect(`${styleDisplayName("Fancy X")}（保留原样）`).not.toMatch(/）（/);
    expect(styleDisplayName(null)).toBe("原有样式");
  });
});
