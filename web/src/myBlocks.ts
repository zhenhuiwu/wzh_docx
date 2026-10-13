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
}

function headingText(markdown: string): string {
  return markdown.replace(/^#{1,6}\s+/, "").replace(/\s+#*\s*$/, "").trim();
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
  const context = (from: number, step: 1 | -1): string | null => {
    for (let i = from; i >= 0 && i < objects.length; i += step) {
      if (objects[i].kind === "heading") continue;
      if (mine(objects[i])) return null;
      // 模板占位（“（在此编写本章内容。）”）不算上下文，继续往外找
      if (isPlaceholderText(objects[i].markdown) || !objects[i].markdown.trim()) continue;
      return objects[i].markdown;
    }
    return null;
  };
  const close = (index: number) => {
    if (current) current.after = context(index, 1);
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
        current = { key: object.id, headingPath: [...path], headingIndex: headings < 0 ? null : headings, blocks: [], lockedHeadings: {}, before: context(index - 1, -1), after: null };
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
