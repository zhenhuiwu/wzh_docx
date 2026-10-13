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
  objects.forEach((object, index) => {
    if (object.kind === "heading") {
      headings += 1;
      const depth = level(object.markdown);
      path.splice(depth - 1, path.length, headingText(object.markdown));
      if (current && mine(object)) current.lockedHeadings[object.id] = true;
      else if (current) current = null; // 标题已在片段路径里显示，不再当上下文
      return;
    }
    if (mine(object)) {
      if (!current) {
        const prev = objects[index - 1];
        current = { key: object.id, headingPath: [...path], headingIndex: headings < 0 ? null : headings, blocks: [], lockedHeadings: {}, before: prev && prev.kind !== "heading" && !mine(prev) ? prev.markdown : null, after: null };
        fragments.push(current);
      }
      current.blocks.push(object);
    } else if (current) {
      current.after = object.markdown;
      current = null;
    }
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

/** 验收 40 P2-4：编辑稿 HTML 里标了 data-word-heading 的块（Word 里套正文样式的标题，如“1.1 标识”）——在片段卡片里锁定。 */
export function wordHeadingIds(html: string): Set<string> {
  const ids = new Set<string>();
  for (const match of html.matchAll(/<[a-z0-9]+\b[^>]*>/gi)) {
    const tag = match[0];
    if (!/\sdata-word-heading(?:=|\s|>)/.test(tag)) continue;
    const id = /\sdata-object-id="([^"]+)"/.exec(tag)?.[1];
    if (id) ids.add(id);
  }
  return ids;
}
