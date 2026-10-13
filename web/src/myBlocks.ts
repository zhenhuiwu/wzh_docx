/** 「我的编写」片段（30 §3.2）：把文档对象按“我负责的连续块”切成片段，前后各带一段只读上下文。纯函数，便于单测。 */
import type { DocObject, ResolvedObject } from "./api/assignments";

export interface Fragment {
  key: string;
  headingPath: string[];
  /** 所在章节的标题序号（与 AI 编写接口的 headingIndex 一致；文档开头为 null） */
  headingIndex: number | null;
  blocks: DocObject[]; // 我负责、可编辑的块（标题锁定，不在其中）
  lockedHeadings: Record<string, true>;
  before: string | null;
  after: string | null;
  /** 前后文所在章节（标题路径，文档开头为空串） */
  beforeSection: string;
  afterSection: string;
}

/** 复验 P2-3：去掉 Markdown 转义（如 "1\\.1" → "1.1"），标题按用户看到的文字显示。 */
export function unescapeMarkdown(text: string): string {
  return text.replace(/\\([\\`*_{}\[\]()#+\-.!|~<>])/g, "$1");
}
export function headingText(markdown: string): string {
  return unescapeMarkdown(markdown.replace(/^#{1,6}\s+/, "").replace(/\s+#*\s*$/, "").trim());
}
function level(markdown: string): number {
  return /^(#{1,6})\s/.exec(markdown)?.[1].length ?? 1;
}

export function buildFragments(objects: DocObject[], resolved: ResolvedObject[] | undefined, userId: string): Fragment[] {
  const owner = new Map((resolved ?? []).map((item) => [item.objectId, item]));
  const mine = (object: DocObject) => {
    const item = owner.get(object.id);
    return Boolean(item && item.editable && item.owners.some((person) => person.id === userId));
  };
  const fragments: Fragment[] = [];
  const path: string[] = [];
  let current: Fragment | null = null;
  let headings = -1;
  // 界面复验 P1-5：按章节分工时片段前后紧挨的是标题，原先因此总是没有上下文。改为跨过标题找最近的正文块；
  // 那一块也是我的（相邻片段）就不重复显示。
  // 每个对象所在章节的标题路径（前后文标注“属于哪一节”）
  const sectionOf: string[] = [];
  {
    const trail: string[] = [];
    for (const object of objects) {
      if (object.kind === "heading") trail.splice(level(object.markdown) - 1, trail.length, headingText(object.markdown));
      sectionOf.push(trail.join(" › "));
    }
  }
  let found = "";
  const context = (from: number, step: 1 | -1): string | null => {
    found = "";
    for (let i = from; i >= 0 && i < objects.length; i += step) {
      if (objects[i].kind === "heading") continue;
      if (mine(objects[i])) return null;
      // 模板占位（“（在此编写本章内容。）”）不算上下文，继续往外找
      if (isContextFiller(objects[i].markdown) || !objects[i].markdown.trim()) continue;
      found = sectionOf[i];
      return objects[i].markdown;
    }
    return null;
  };
  const close = (index: number) => {
    if (current) { current.after = context(index, 1); current.afterSection = found; }
    current = null;
  };
  objects.forEach((object, index) => {
    if (object.kind === "heading") {
      headings += 1;
      const depth = level(object.markdown);
      path.splice(depth - 1, path.length, headingText(object.markdown));
      if (current && mine(object)) current.lockedHeadings[object.id] = true;
      else close(index);
      return;
    }
    if (mine(object)) {
      if (!current) {
        current = { key: object.id, headingPath: [...path], headingIndex: headings < 0 ? null : headings, blocks: [], lockedHeadings: {}, before: context(index - 1, -1), beforeSection: found, after: null, afterSection: "" };
        fragments.push(current);
      }
      current.blocks.push(object);
    } else close(index);
  });
  return fragments;
}

export interface UpdateOp { op: "update"; objectId: string; baseRevision: number; markdown: string }

/** 只提交改过的块；内容清空的块不提交（删除段落要去工作台做，避免误删）。 */
export function changedOps(blocks: DocObject[], drafts: Record<string, string>): UpdateOp[] {
  return blocks
    .filter((block) => drafts[block.id] !== undefined && drafts[block.id].trim() && drafts[block.id] !== block.markdown)
    .map((block) => ({ op: "update", objectId: block.id, baseRevision: block.revision, markdown: drafts[block.id] }));
}

export interface FragmentSummary { key: string; label: string; status: "todo" | "written" | "conflict" | "rejected" }

/** 30 Q-8：AI 编写本节一次最多 20 段、6000 字。 */
export const AI_FRAGMENT_MAX_BLOCKS = 20;
export const AI_FRAGMENT_MAX_CHARS = 6000;
export function aiFragmentTooLarge(fragment: Fragment, drafts: Record<string, string>): boolean {
  const chars = fragment.blocks.reduce((sum, block) => sum + (drafts[block.id] ?? block.markdown).length, 0);
  return fragment.blocks.length > AI_FRAGMENT_MAX_BLOCKS || chars > AI_FRAGMENT_MAX_CHARS;
}

/** 验收 P1-5：模板占位文字（如“（在此编写本章内容。）”）不算正文：显示为输入框占位提示，不计为“已写”。 */
const PLACEHOLDER = /^[（(][^（）()\n]*(在此|请在|待编写|待补充|填写|编写本)[^（）()\n]*[）)]$/;
export function isPlaceholderText(markdown: string): boolean {
  return PLACEHOLDER.test(markdown.trim());
}
/** 复验 2：公共框架的说明文字（“（公共框架：…，各文档引用同一内容。）”）在我的编写里按模板占位处理：不当上下文，输入框里显示为占位提示。
 * 工作台目录的“待编写”统计不受影响（框架章节不需要编写）。 */
export function isFrameworkNote(markdown: string): boolean {
  return /^[（(]公共框架[：:][^\n]*[）)]$/.test(markdown.trim());
}
/** 复验 P2-2：模板的来源说明（“（来源：《软件需求规格说明》范围，创建时复制的初稿…）”）也不当上下文。 */
export function isSourceNote(markdown: string): boolean {
  return /^[（(]来源[：:][^\n]*[）)]$/.test(markdown.trim());
}
export function isTemplateFiller(markdown: string): boolean {
  return isPlaceholderText(markdown) || isFrameworkNote(markdown);
}
/** 前后文跳过的块：模板占位、公共框架说明、来源说明 */
export function isContextFiller(markdown: string): boolean {
  return isTemplateFiller(markdown) || isSourceNote(markdown);
}

/** 验收 40 P1-1：AI 编写本节的块操作可能在 draft 或 done 事件里给出。只把本片段里的 replace 填进草稿，其余计为跳过。 */
export interface AiBlockOp { op: string; blockId: string; content: string }
export function applyAiOperations(operations: AiBlockOp[] | undefined, ids: Set<string>, drafts: Record<string, string>, seen: Set<string>): { applied: number; skipped: number } {
  let applied = 0, skipped = 0;
  for (const op of operations ?? []) {
    const key = `${op.op}:${op.blockId}:${op.content}`;
    if (seen.has(key)) continue; // draft 与 done 可能重复给出同一操作
    seen.add(key);
    if (op.op === "replace" && ids.has(op.blockId) && op.content.trim()) { drafts[op.blockId] = op.content; applied += 1; }
    else skipped += 1;
  }
  return { applied, skipped };
}

/** 验收 40 P2-4 / 43：/objects 里标了 isWordHeading 的块（Word 里套正文样式的标题，如“1.1 标识”）——在片段卡片里锁定。 */
export function wordHeadingIds(objects: Array<{ id: string; isWordHeading?: boolean }>): Set<string> {
  return new Set(objects.filter((item) => item.isWordHeading).map((item) => item.id));
}

/** 前后文的显示形式（复验：并排视图里直接显示 Markdown 会露出表格竖线）：表格给行列，其余去掉 Markdown 标记成纯文字。 */
export type ContextView = { kind: "table"; rows: string[][]; more: number } | { kind: "text"; text: string };
export function contextView(markdown: string, maxRows = 4): ContextView {
  const lines = markdown.trim().split("\n").map((line) => line.trim()).filter(Boolean);
  if (lines.length >= 2 && lines.every((line) => line.startsWith("|"))) {
    const rows = lines.filter((line) => !/^\|?[\s:|-]+\|?$/.test(line)).map((line) => line.replace(/^\||\|$/g, "").split("|").map((cell) => plain(cell)));
    return { kind: "table", rows: rows.slice(0, maxRows), more: Math.max(0, rows.length - maxRows) };
  }
  return { kind: "text", text: lines.map((line) => plain(line.replace(/^#{1,6}\s+/, "").replace(/^(?:[-*+]|\d+[.)])\s+/, "").replace(/^>\s?/, ""))).join("\n") };
}
function plain(text: string): string {
  return text.replace(/!\[[^\]]*\]\([^)]*\)/g, "[图片]").replace(/\[([^\]]*)\]\([^)]*\)/g, "$1").replace(/<br\s*\/?>/gi, " ").replace(/\\(.)/g, "$1").replace(/[*_`~]/g, "").trim();
}
