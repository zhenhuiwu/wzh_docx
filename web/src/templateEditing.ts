/**
 * A06 模板编辑器的纯函数（16 §3.2/§3.3，17 §3.5）：块列表的插入、删除、章节上移下移、标题绑定，
 * 样式取值的中文显示，影响提示与对比摘要文案。不依赖 Vue，便于单测。
 */
import type { DiffSummary, EditorBlock, EditorStructure, ImpactItem } from "./api/templates";

export const STYLE_PROP_LABELS: Record<string, string> = {
  fontEastAsia: "中文字体", fontAscii: "西文字体", size: "字号", firstLineIndent: "首行缩进",
  leftIndent: "左缩进", rightIndent: "右缩进", lineSpacing: "行距", spaceBefore: "段前", spaceAfter: "段后",
};
export const STYLE_PROPS = Object.keys(STYLE_PROP_LABELS);
export const SIZE_NAMES: [string, number][] = [
  ["初号", 42], ["小初", 36], ["一号", 26], ["小一", 24], ["二号", 22], ["小二", 18], ["三号", 16], ["小三", 15],
  ["四号", 14], ["小四", 12], ["五号", 10.5], ["小五", 9], ["六号", 7.5],
];
export const NEW_KINDS = ["heading", "body", "guidance", "placeholder", "example"] as const;

export function newBlockId(): string {
  const bytes = new Uint8Array(8);
  crypto.getRandomValues(bytes);
  return "block-" + Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("");
}

export function isTop(block: EditorBlock) { return !block.tableId; }

/** 顶层块（含它的单元格段落）组成的单元。 */
function units(blocks: EditorBlock[]): EditorBlock[][] {
  const result: EditorBlock[][] = [];
  for (const block of blocks) {
    if (block.tableId && result.length && (result[result.length - 1][0].id === block.tableId)) result[result.length - 1].push(block);
    else result.push([block]);
  }
  return result;
}
const flat = (list: EditorBlock[][]) => list.flat();

/** 章节：标题及其后直到同级或更高级标题之前的所有顶层块。 */
export function chapterIds(blocks: EditorBlock[], headingId: string): string[] {
  const list = units(blocks);
  const start = list.findIndex(unit => unit[0].id === headingId);
  if (start < 0) return [];
  const level = list[start][0].headingLevel ?? 1;
  let end = start + 1;
  while (end < list.length && !(list[end][0].kind === "heading" && (list[end][0].headingLevel ?? 1) <= level)) end++;
  return flat(list.slice(start, end)).map(block => block.id);
}

/** 标题按章节整体上移/下移（跨同级标题；遇到更高级标题即停）。返回新数组；不能移动时返回 null。 */
export function moveChapter(blocks: EditorBlock[], headingId: string, direction: -1 | 1): EditorBlock[] | null {
  const list = units(blocks);
  const index = list.findIndex(unit => unit[0].id === headingId);
  if (index < 0 || list[index][0].kind !== "heading") return null;
  const level = list[index][0].headingLevel ?? 1;
  const span = (start: number) => {
    let end = start + 1;
    while (end < list.length && !(list[end][0].kind === "heading" && (list[end][0].headingLevel ?? 1) <= (list[start][0].headingLevel ?? 1))) end++;
    return end;
  };
  const end = span(index);
  if (direction < 0) {
    let previous = index - 1;
    while (previous >= 0 && !(list[previous][0].kind === "heading" && (list[previous][0].headingLevel ?? 1) <= level)) previous--;
    if (previous < 0 || (list[previous][0].headingLevel ?? 1) < level) return null;
    return flat([...list.slice(0, previous), ...list.slice(index, end), ...list.slice(previous, index), ...list.slice(end)]);
  }
  if (end >= list.length || list[end][0].kind !== "heading" || (list[end][0].headingLevel ?? 1) < level) return null;
  const nextEnd = span(end);
  return flat([...list.slice(0, index), ...list.slice(end, nextEnd), ...list.slice(index, end), ...list.slice(nextEnd)]);
}

/** 普通块与相邻顶层块交换位置。 */
export function moveBlock(blocks: EditorBlock[], id: string, direction: -1 | 1): EditorBlock[] | null {
  const list = units(blocks);
  const index = list.findIndex(unit => unit[0].id === id);
  const target = index + direction;
  if (index < 0 || target < 0 || target >= list.length) return null;
  const next = [...list];
  [next[index], next[target]] = [next[target], next[index]];
  return flat(next);
}

/** 在 anchor（及其单元格段落）之后插入新块；anchor 为空时插在最前。 */
export function insertAfter(blocks: EditorBlock[], anchorId: string | null, block: EditorBlock): EditorBlock[] {
  const list = units(blocks);
  const index = anchorId ? list.findIndex(unit => unit[0].id === anchorId) : -1;
  return flat([...list.slice(0, index + 1), [block], ...list.slice(index + 1)]);
}

export function createBlock(kind: (typeof NEW_KINDS)[number], text: string, level: number | null = null): EditorBlock {
  const guidance = kind === "guidance";
  return { id: newBlockId(), path: null, kind, text, sourceText: guidance ? text : "", headingLevel: kind === "heading" ? level ?? 2 : null,
    headingId: null, tableId: null, protected: false, isTable: false, lock: { text: null, delete: null }, ...(guidance ? { virtual: true } : {}) };
}

/** 删除前检查：表格、受保护段落不能删；删除整章时逐块检查。 */
export function deleteCheck(blocks: EditorBlock[], ids: string[]): string | null {
  for (const id of ids) {
    const block = blocks.find(item => item.id === id);
    if (!block || block.tableId) continue;
    if (block.isTable) return "表格不能在线删除；请在 Word 中修改后上传为新草稿。";
    if (block.kind === "toc") return "目录不能在线删除。如需删除，请在 Word 中修改后上传为新草稿。";
    if (block.lock?.delete) return block.lock.delete;
  }
  return null;
}

export function removeBlocks(blocks: EditorBlock[], ids: string[]): EditorBlock[] {
  const gone = new Set(ids);
  return blocks.filter(block => !gone.has(block.id) && !(block.tableId && gone.has(block.tableId)));
}

/** 与服务端一致：非指导块归属前一个标题；指导保留自己的绑定（无效时归前一个标题）；未落位的指导不绑定。 */
export function rebindHeadings(blocks: EditorBlock[]): EditorBlock[] {
  const headings = new Set(blocks.filter(block => block.kind === "heading").map(block => block.id));
  let current: string | null = null;
  for (const block of blocks) {
    if (block.kind === "heading") { current = block.id; block.headingId = null; }
    else if (block.unplaced) block.headingId = null;
    else if (block.kind === "guidance") { if (!block.headingId || !headings.has(block.headingId)) block.headingId = current; }
    else block.headingId = current;
  }
  return blocks;
}

const DERIVED = ["lock", "styleKey", "styleName", "tableEditable", "rowsEditable", "lockedCells", "tableEditReasons", "baseCells"] as const;

/** 保存前去掉只读派生字段；表格只在单元格改过时带 cells。 */
export function forSave(structure: EditorStructure): EditorStructure {
  return { ...structure, blocks: structure.blocks.map((block) => {
    const copy: Record<string, unknown> = { ...block };
    for (const key of DERIVED) delete copy[key];
    if (block.isTable && block.cells && block.baseCells && JSON.stringify(block.cells) === JSON.stringify(block.baseCells)) delete copy.cells;
    if (!block.isTable) delete copy.cells;
    return copy as unknown as EditorBlock;
  }) };
}

export function isCellLocked(block: EditorBlock, row: number, column: number): boolean {
  if (block.tableEditable === "none") return true;
  return (block.lockedCells ?? []).some(([r, c]) => r === row && c === column);
}

export function cellReason(block: EditorBlock, row: number, column: number): string {
  const reasons = block.tableEditReasons ?? [];
  const hit = reasons.find(reason => (reason.cells ?? []).some(([r, c]) => r === row && c === column)) ?? reasons[0];
  return `${hit?.message ?? "此单元格不能在线修改。"}如需修改，请在 Word 中修改后上传为新草稿。`;
}

// ---------------------------------------------------------------- 样式取值显示

export function sizeLabel(value: number | null | undefined): string {
  if (value == null) return "未设置";
  const named = SIZE_NAMES.find(([, pt]) => pt === value);
  return named ? `${named[0]}（${value} 磅）` : `${value} 磅`;
}

export function valueLabel(prop: string, value: any): string {
  if (value == null) return "未设置";
  if (prop === "size") return sizeLabel(value);
  if (prop === "lineSpacing") {
    const text = String(value);
    if (text.startsWith("exact:")) return `固定值 ${text.slice(6)} 磅`;
    if (text.startsWith("atLeast:")) return `最小值 ${text.slice(8)} 磅`;
    return text === "1" ? "单倍" : `${text} 倍`;
  }
  if (typeof value === "object" && "unit" in value) {
    const unit = value.unit === "chars" ? "字符" : value.unit === "lines" ? "行" : "磅";
    return `${value.value} ${unit}`;
  }
  return String(value);
}

export function impactText(item: ImpactItem): string {
  const prop = STYLE_PROP_LABELS[item.prop] ?? item.prop;
  if (item.willClear > 0) return `使用“${item.style}”的段落共 ${item.affectedParagraphs} 段，其中 ${item.directFormatParagraphs} 段自带${prop}，将一并清除，统一为新值。`;
  if (item.directFormatParagraphs > 0) return `使用“${item.style}”的段落共 ${item.affectedParagraphs} 段，其中 ${item.directFormatParagraphs} 段自带${prop}，修改后这些段落看不到变化。`;
  return `使用“${item.style}”的段落共 ${item.affectedParagraphs} 段。`;
}

export function publishImpactLine(item: ImpactItem): string {
  const prop = STYLE_PROP_LABELS[item.prop] ?? item.prop;
  return `样式：${item.style} ${prop} ${valueLabel(item.prop, item.before)}→${valueLabel(item.prop, item.after)}，影响 ${item.affectedParagraphs} 段（清除 ${item.willClear} 段的直接${prop}）。`;
}

const SUMMARY_LABELS: [keyof DiffSummary, string][] = [["headings", "标题"], ["body", "正文"], ["tables", "表格"], ["guidance", "指导"], ["format", "格式"]];
export function summaryText(summary: DiffSummary): string {
  const parts = SUMMARY_LABELS.filter(([key]) => summary[key] > 0).map(([key, label]) => `${label} ${summary[key]} 处`);
  return parts.length ? parts.join("、") : "没有变化";
}
export function summaryTotal(summary: DiffSummary): number {
  return SUMMARY_LABELS.reduce((total, [key]) => total + summary[key], 0);
}

export function conflictFieldLabel(field: string, theirsName: string): string {
  if (field === "text") return "文字";
  if (field === "kind") return "类型";
  if (field === "headingLevel") return "标题级别";
  if (field === "headingId") return "指导";
  if (field === "exists") return "存在";
  const cell = /^cell:(\d+):(\d+)$/.exec(field);
  if (cell) return `单元格（第 ${Number(cell[1]) + 1} 行第 ${Number(cell[2]) + 1} 列）`;
  const style = /^style:(.+):([^:]+)$/.exec(field);
  if (style) return `样式“${style[1]}·${STYLE_PROP_LABELS[style[2]] ?? style[2]}”`;
  return theirsName ? field : field;
}

export function existsText(conflict: { theirs: any; mine: any }, seq: number): string {
  return conflict.theirs === false ? `第 ${seq} 版删除了这一块，你修改了它` : `你删除了这一块，第 ${seq} 版修改了它`;
}

export function shortSha(sha: string | null | undefined) { return (sha ?? "").slice(0, 12); }

/** 锁定原因 → 简短标签：“此内容含书签、域，无法安全删除…” → “含书签、域（保留原样）”。 */
export function lockTag(reason: string | null | undefined): string {
  if (!reason) return "";
  const hit = /含([^，。,]+?)[，。,]/.exec(reason);
  return hit ? `含${hit[1].replace(/^\s+/, "")}（保留原样）` : "保留原样";
}

/** 编辑器里的块类型名（与 16 §3 一致：指导叫“编写指导”）。 */
export const EDITOR_KIND_LABELS: Record<string, string> = {
  heading: "标题", guidance: "编写指导", body: "正文", placeholder: "填写位置",
  table: "表格", caption: "题注", toc: "目录", cover: "封面与更改记录", example: "示例",
};

/** 表格对比只列改过的格子：“第 r 行第 c 列：旧 → 新”。 */
export function cellChanges(before: string[][] | undefined, after: string[][] | undefined): { label: string; before: string; after: string }[] {
  const result: { label: string; before: string; after: string }[] = [];
  const rows = Math.max(before?.length ?? 0, after?.length ?? 0);
  for (let r = 0; r < rows; r++) {
    const columns = Math.max(before?.[r]?.length ?? 0, after?.[r]?.length ?? 0);
    for (let c = 0; c < columns; c++) {
      const old = before?.[r]?.[c] ?? "";
      const next = after?.[r]?.[c] ?? "";
      if (old !== next) result.push({ label: `第 ${r + 1} 行第 ${c + 1} 列`, before: old, after: next });
    }
  }
  return result;
}

/**
 * D-17 / 25 S-2：保存冲突时下载的“可读修改清单”（Markdown）：对照开始编辑时的版本，按章节列出新增、删除、改动（原文 → 改后）、
 * 调整顺序和样式改动。只依赖块 ID（编辑器内块 ID 延续）。
 */
export function changesMarkdown(input: {
  title: string; baseLabel: string; base: EditorBlock[]; mine: EditorBlock[];
  baseStyles: Record<string, Record<string, any>>; styles: Record<string, Record<string, any>>; time?: string;
}): string {
  const top = (list: EditorBlock[]) => list.filter(block => !block.tableId && !block.unplaced);
  const old = new Map(input.base.map(block => [block.id, block]));
  const now = new Map(input.mine.map(block => [block.id, block]));
  const label = (block: EditorBlock) => block.kind === "heading" ? `标题 ${block.headingLevel ?? 1}` : EDITOR_KIND_LABELS[block.kind] ?? block.kind;
  const quote = (text: string) => (text || "（空）").split("\n").map(line => `> ${line}`).join("\n");
  const chapterOf = (list: EditorBlock[], id: string) => {
    const index = list.findIndex(block => block.id === id);
    const heading = [...list.slice(0, index + 1)].reverse().find(block => block.kind === "heading" && !block.tableId);
    return heading ? heading.text || "（空标题）" : "（文档开头）";
  };
  const lines: string[] = [`# ${input.title} · 我的修改`, "", `对照：${input.baseLabel}${input.time ? `　导出时间：${input.time}` : ""}`, ""];
  const items: string[] = [];
  for (const block of top(input.mine)) {
    const before = old.get(block.id);
    const where = `【${chapterOf(input.mine, block.id)}】`;
    if (!before) { items.push(`- 新增${label(block)} ${where}`, quote(block.text), ""); continue; }
    const parts: string[] = [];
    if (before.kind !== block.kind || before.headingLevel !== block.headingLevel) parts.push(`类型：${label(before)} → ${label(block)}`);
    if (before.isTable || block.isTable) {
      for (const cell of cellChanges(before.cells, block.cells)) parts.push(`${cell.label}：“${cell.before}” → “${cell.after}”`);
    } else if ((before.text ?? "") !== (block.text ?? "")) {
      items.push(`- 修改${label(block)} ${where}`, "  原文：", quote(before.text), "  改后：", quote(block.text), "");
    }
    if (before.kind === "guidance" && before.headingId !== block.headingId) parts.push("所属章节已调整");
    if (parts.length) items.push(`- ${label(block)} ${where}“${(block.text || "").slice(0, 30)}”：${parts.join("；")}`, "");
  }
  for (const block of top(input.base)) {
    if (!now.has(block.id)) items.push(`- 删除${label(block)} 【${chapterOf(input.base, block.id)}】`, quote(block.text), "");
  }
  const keep = (list: EditorBlock[]) => top(list).filter(block => old.has(block.id) && now.has(block.id)).map(block => block.id);
  if (keep(input.base).join() !== keep(input.mine).join()) items.push("- 调整了章节或段落的顺序", "");
  for (const [style, props] of Object.entries(input.styles)) {
    for (const [prop, value] of Object.entries(props ?? {})) {
      const before = input.baseStyles[style]?.[prop] ?? null;
      if (JSON.stringify(before) !== JSON.stringify(value ?? null)) {
        items.push(`- 样式“${style}”${STYLE_PROP_LABELS[prop] ?? prop}：${valueLabel(prop, before)} → ${valueLabel(prop, value)}`);
      }
    }
  }
  if (!items.length) items.push("没有修改。");
  return [...lines, ...items, ""].join("\n");
}

const BUILTIN_STYLE_NAMES: Record<string, string> = {
  "Normal Indent": "正文缩进", "Body Text": "正文文本", "Body Text Indent": "正文文本缩进", "Body Text First Indent": "正文首行缩进",
  "List Paragraph": "列表段落", "Plain Text": "纯文本", "Title": "标题", "Subtitle": "副标题", "Caption": "题注",
  "header": "页眉", "footer": "页脚", "Table Grid": "网格型", "Quote": "引用", "List Bullet": "项目符号列表", "List Number": "编号列表",
};
/** 25 S-12：“（保留原样）”前显示中文样式名；不认识的显示“原有样式（英文名）”。 */
export function styleDisplayName(name: string | null | undefined): string {
  if (!name) return "原有样式";
  if (/[\u4e00-\u9fff]/.test(name)) return name;
  const toc = /^toc (\d)$/i.exec(name);
  if (toc) return `目录 ${toc[1]}`;
  return BUILTIN_STYLE_NAMES[name] ?? `原有样式（${name}）`;
}
