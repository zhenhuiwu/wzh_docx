/** 「我的编写」片段（30 §3.2）：把文档对象按“我负责的连续块”切成片段，前后各带一段只读上下文。纯函数，便于单测。 */
import type { DocObject, ResolvedObject } from "./api/assignments";

export interface Fragment {
  key: string;
  headingPath: string[];
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
  objects.forEach((object, index) => {
    if (object.kind === "heading") {
      const depth = level(object.markdown);
      path.splice(depth - 1, path.length, headingText(object.markdown));
      if (current && mine(object)) current.lockedHeadings[object.id] = true;
      else if (current) current = null; // 标题已在片段路径里显示，不再当上下文
      return;
    }
    if (mine(object)) {
      if (!current) {
        const prev = objects[index - 1];
        current = { key: object.id, headingPath: [...path], blocks: [], lockedHeadings: {}, before: prev && prev.kind !== "heading" && !mine(prev) ? prev.markdown : null, after: null };
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
