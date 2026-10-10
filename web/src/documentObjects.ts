/**
 * 阶段2b 块级补丁的纯函数（01 §6.2，S7）：由编辑区顶层块 + 加载基线算出 ops。
 * 与 DOM 无关，便于单元测试；工作台目前仍走整篇 allowMerge（见 06 偏差记录），此模块供块级保存与多标签提示使用。
 */

export interface EditorBlock { objectId: string | null; md: string }
export interface BaselineEntry { revision: number; md: string }
export type PatchOp =
  | { op: "update"; objectId: string; baseRevision: number; markdown: string }
  | { op: "insert"; tempId: string; afterObjectId: string | null; beforeObjectId: string | null; markdown: string }
  | { op: "delete"; objectId: string; baseRevision: number }
  | { op: "move"; objectId: string; afterObjectId: string | null };

const normalize = (text: string) => text.replace(/\s+/g, " ").trim();

/** 字符级相似度（公共前后缀占比），只用于重复 ID 去重时挑“最像原段”的那个。 */
function similarity(a: string, b: string): number {
  const x = normalize(a), y = normalize(b);
  if (x === y) return 1;
  const longest = Math.max(x.length, y.length) || 1;
  let prefix = 0;
  while (prefix < x.length && prefix < y.length && x[prefix] === y[prefix]) prefix++;
  let suffix = 0;
  while (suffix < x.length - prefix && suffix < y.length - prefix && x[x.length - 1 - suffix] === y[y.length - 1 - suffix]) suffix++;
  return (prefix + suffix) / longest;
}

/** 同一 ID 出现多次（回车拆段、复制粘贴带属性）：与基线最相近的保留 ID，其余视为新块。 */
export function dedupeIds(blocks: EditorBlock[], baseline: Map<string, BaselineEntry>): EditorBlock[] {
  const keep = new Map<string, number>();
  blocks.forEach((block, index) => {
    if (!block.objectId) return;
    const base = baseline.get(block.objectId);
    const current = keep.get(block.objectId);
    if (current === undefined) { keep.set(block.objectId, index); return; }
    const score = base ? similarity(block.md, base.md) : 0;
    const best = base ? similarity(blocks[current].md, base.md) : 0;
    if (score > best) keep.set(block.objectId, index);
  });
  return blocks.map((block, index) => (block.objectId && keep.get(block.objectId) !== index ? { ...block, objectId: null } : block));
}

/** 是否被移动：在保留下来的 ID 序列里，相对顺序与基线不同（按最长递增子序列判断，最少移动）。 */
function movedIds(order: string[], baselineOrder: string[]): Set<string> {
  const rank = new Map(baselineOrder.map((id, index) => [id, index]));
  const seq = order.filter(id => rank.has(id));
  const values = seq.map(id => rank.get(id)!);
  const tails: number[] = [], prev: number[] = new Array(values.length).fill(-1), tailIndex: number[] = [];
  values.forEach((value, index) => {
    let lo = 0, hi = tails.length;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (tails[mid] < value) lo = mid + 1; else hi = mid; }
    tails[lo] = value; tailIndex[lo] = index; prev[index] = lo > 0 ? tailIndex[lo - 1] : -1;
  });
  const stay = new Set<string>();
  for (let index = tailIndex[tails.length - 1] ?? -1; index >= 0; index = prev[index]) stay.add(seq[index]);
  return new Set(seq.filter(id => !stay.has(id)));
}

/**
 * 计算补丁 ops。baselineOrder 为加载时的对象顺序。
 * 无 ID 的块 → insert（锚点取前一个、后一个有 ID 的块）；基线 ID 不再出现 → delete；顺序变化 → move；内容变化 → update。
 */
export function computeOps(blocks: EditorBlock[], baseline: Map<string, BaselineEntry>, baselineOrder: string[],
  newTempId: () => string = () => crypto.randomUUID()): PatchOp[] {
  const clean = dedupeIds(blocks, baseline).map(block => (block.objectId && !baseline.has(block.objectId) ? { ...block, objectId: null } : block));
  const ops: PatchOp[] = [];
  const present = new Set(clean.map(block => block.objectId).filter((id): id is string => Boolean(id)));
  const moved = movedIds(clean.map(block => block.objectId).filter((id): id is string => Boolean(id)), baselineOrder);
  let lastAnchor: string | null = null;
  clean.forEach((block, index) => {
    if (block.objectId) {
      const base = baseline.get(block.objectId)!;
      if (moved.has(block.objectId)) ops.push({ op: "move", objectId: block.objectId, afterObjectId: lastAnchor });
      if (block.md !== base.md) ops.push({ op: "update", objectId: block.objectId, baseRevision: base.revision, markdown: block.md });
      lastAnchor = block.objectId;
      return;
    }
    if (!block.md.trim()) return;
    const tempId = newTempId();
    const before = clean.slice(index + 1).find(next => next.objectId)?.objectId ?? null;
    ops.push({ op: "insert", tempId, afterObjectId: lastAnchor, beforeObjectId: before, markdown: block.md });
    lastAnchor = tempId;
  });
  for (const id of baselineOrder) {
    if (!present.has(id)) ops.push({ op: "delete", objectId: id, baseRevision: baseline.get(id)!.revision });
  }
  return ops;
}

/** “本机 N 处”：一个对象记 1 处（4.9 第5条）；同一对象既移动又修改仍记 1。 */
export function changedPlaces(ops: PatchOp[]): number {
  const keys = new Set(ops.map(op => (op.op === "insert" ? `t:${op.tempId}` : `o:${op.objectId}`)));
  return keys.size;
}

// ---- 多标签提示（决策4：提示，不阻止；01 §6.5）
export interface PresenceMessage { type: "presence" | "presence-reply" | "bye"; tabId: string; userId: string }

/** 处理 presence 消息：同一账号的另一个标签打开同一文档 → 返回 true（显示提示）。 */
export function otherTabOpen(message: unknown, self: { tabId: string; userId: string }): boolean {
  if (!message || typeof message !== "object") return false;
  const value = message as Partial<PresenceMessage>;
  return (value.type === "presence" || value.type === "presence-reply") && typeof value.tabId === "string"
    && value.tabId !== self.tabId && value.userId === self.userId;
}
