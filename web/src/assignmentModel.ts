/** A07 编写人员视角的纯函数（16 §4.4–4.8）：文案、连续区域、只保存我负责的部分。 */
import { assignBlockIds, splitBlocks, type BaselineObject } from "./markdownBlocks";

export interface PersonLike { id?: string; name: string }

/** “王测、孙敏”，超过 2 人加“等”（16 §4.8 姓名取值）。 */
export function ownerNames(owners: PersonLike[] | null | undefined): string {
  const names = (owners ?? []).map(person => person.name).filter(Boolean);
  if (!names.length) return "";
  return names.length > 2 ? `${names.slice(0, 2).join("、")}等` : names.join("、");
}

/** 只读气泡（16 §4.5 / §4.8）。projectOwner 为项目负责人（permissions.projectOwners[0]）。 */
export function lockedText(owners: string, projectOwner: string): string {
  const contact = projectOwner ? `项目负责人 ${projectOwner}` : "项目负责人";
  return owners ? `该段由 ${owners} 负责，你不能修改。如需修改请联系${contact}。` : `该段尚未分配，你不能修改。如需编写请联系${contact}。`;
}

export function noAssignmentText(projectOwner: string): string {
  return `这篇文档还没有分配给你的部分，目前只读。如需编写，请联系项目负责人${projectOwner ? ` ${projectOwner}` : ""}。`;
}

export function stashText(stash: string): string {
  const tail = [...stash].slice(-30).join("");
  return `刚才输入的“${tail}”已暂存。`;
}

export interface Violation { type: string; objectId?: string | null; clientIndex?: number | null; owners?: PersonLike[]; headingPath?: string[]; excerpt?: string }

/** violation.type 的说明（16 §4.6 第 4 条）。 */
export function violationText(item: Violation): string {
  const who = ownerNames(item.owners) || "项目负责人";
  switch (item.type) {
    case "modify": return `修改了 ${who} 负责的内容`;
    case "delete": return `删除了 ${who} 负责的内容`;
    case "insert": return `新内容的位置属于 ${who} 负责的部分`;
    case "move": return "移到了不属于你的部分";
    default: return "改变了章节范围（标题级别或位置）";
  }
}

export function violationWhere(item: Violation): string {
  const path = (item.headingPath ?? []).join(" › ") || "封面";
  const excerpt = [...(item.excerpt ?? "")].slice(0, 20).join("");
  return excerpt ? `${path} · ${excerpt}` : path;
}

/** 按文档顺序的连续可编辑区域（“一处”），返回每处第一个对象 ID。 */
export function mineRegions(order: string[], editable: Set<string>): string[] {
  const starts: string[] = [];
  let inside = false;
  for (const id of order) {
    const mine = editable.has(id);
    if (mine && !inside) starts.push(id);
    inside = mine;
  }
  return starts;
}

/**
 * 只保存我负责的部分（16 §4.6 第 4 条）。baseline 是**开始编辑时**那一版的对象（带 ID），editable 是现在归我的对象。
 * mineIds 给了就按真实 ID 对应（重复文字不会配错）。
 * 结果仍以这一版为底：我负责的对象采用我的文字（删了就不输出），我在它后面新写的块跟着保留；
 * 不归我的对象一律还原成这一版的文字，我对它们的修改和写在它们后面的新块放进暂存。
 * 27 复验 M-4：结果必须按开始编辑时的修订号提交（allowMerge），由服务端三方合并——别人并发改了我没改的块会被并入，
 * 双方都改了同一块报冲突。这里不再拿服务器最新正文做文字配对（重复文字会配错 ID，造成静默覆盖）。
 */
/**
 * 28 L-4：没有真实块 ID 时的配对——按编辑距离对齐（相同文字 0，原位改写 1，增删各 1.5），
 * 原位改写比“删一段再加一段”便宜，重复文字（多段“无。”）不会错位。返回每个块对应的底稿 ID（新块为 null）。
 */
export function alignByEdits(blocks: Array<{ kind: string; body: string }>, baseline: BaselineObject[]): Array<string | null> {
  const n = blocks.length, m = baseline.length;
  const cost: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = 1; i <= n; i += 1) cost[i][0] = i * 1.5;
  for (let j = 1; j <= m; j += 1) cost[0][j] = j * 1.5;
  const grams = (text: string) => { const chars = [...text.trim()]; const out = new Map<string, number>(); for (let k = 0; k < chars.length; k += 1) { for (const g of [chars[k], chars[k] + (chars[k + 1] ?? "")]) out.set(g, (out.get(g) ?? 0) + 1); } return out; };
  const mineGrams = blocks.map(block => grams(block.body)), baseGrams = baseline.map(item => grams(item.markdown));
  const dice = (a: Map<string, number>, b: Map<string, number>) => { let both = 0, total = 0; for (const [g, c] of a) { both += Math.min(c, b.get(g) ?? 0); total += c; } for (const c of b.values()) total += c; return total ? 2 * both / total : 0; };
  // 原位改写的代价随文字相似度（单字 + 双字的 Dice）降低（0.2–1.8，总比“删一段 + 加一段”的 3 便宜）：改过的段更愿意配回自己原来那段
  const subCache = new Map<number, number>();
  const sub = (i: number, j: number) => {
    const key = i * (m + 1) + j; const hit = subCache.get(key); if (hit !== undefined) return hit;
    const value = blocks[i].body.trim() === baseline[j].markdown.trim() ? 0 : blocks[i].kind === baseline[j].kind ? 0.2 + 1.6 * (1 - dice(mineGrams[i], baseGrams[j])) : 2.5;
    subCache.set(key, value); return value;
  };
  for (let i = 1; i <= n; i += 1) {
    for (let j = 1; j <= m; j += 1) cost[i][j] = Math.min(cost[i - 1][j - 1] + sub(i - 1, j - 1), cost[i - 1][j] + 1.5, cost[i][j - 1] + 1.5);
  }
  const ids: Array<string | null> = new Array(n).fill(null);
  let i = n, j = m;
  while (i > 0 && j > 0) {
    if (cost[i][j] === cost[i - 1][j - 1] + sub(i - 1, j - 1)) { if (sub(i - 1, j - 1) < 2.5) ids[i - 1] = baseline[j - 1].id; i -= 1; j -= 1; }
    else if (cost[i][j] === cost[i - 1][j] + 1.5) i -= 1;
    else j -= 1;
  }
  return ids;
}

export function keepMineOnly(markdown: string, baseline: BaselineObject[], editable: Set<string>, mineIds?: Array<string | null>): { markdown: string; stashed: string[]; blockIds: Array<string | null> } {
  const blocks = splitBlocks(markdown);
  // mineIds：编辑区里每块真实的对象 ID（优先）；没有时退回文字配对（本机草稿等）
  const known = new Set(baseline.map(item => item.id));
  const byIds = !!mineIds && mineIds.length === blocks.length;
  const ids = byIds ? mineIds!.map(id => id && known.has(id) ? id : null) : blocks.length * baseline.length <= 1_000_000 ? alignByEdits(blocks, baseline) : assignBlockIds(markdown, baseline);
  const mineById = new Map<string, string>();
  const following = new Map<string, string[]>();
  const leading: string[] = [];
  let last: string | null = null;
  blocks.forEach((block, index) => {
    const id = ids[index];
    if (id) { mineById.set(id, block.body); last = id; return; }
    if (last === null) leading.push(block.body);
    else following.set(last, [...(following.get(last) ?? []), block.body]);
  });
  const out: string[] = [];
  const outIds: Array<string | null> = [];
  const stashed: string[] = [];
  const emit = (body: string, id: string | null) => { out.push(body); outIds.push(id); };
  const firstEditable = baseline.length > 0 && editable.has(baseline[0].id);
  if (firstEditable) leading.forEach(body => emit(body, null)); else stashed.push(...leading);
  for (const item of baseline) {
    const mine = mineById.get(item.id);
    if (editable.has(item.id)) {
      if (mine !== undefined) emit(mine, item.id);
      // 28 L-4：没有真实 ID（文字配对）时，配不上的可编辑块按底稿保留，不当作“我删了”——宁可不删，也不静默删掉没动过的段
      else if (!byIds) emit(item.markdown, item.id);
    } else {
      emit(item.markdown, item.id);
      if (mine !== undefined && mine.trim() !== item.markdown.trim()) stashed.push(mine);
    }
    const extra = following.get(item.id) ?? [];
    if (editable.has(item.id)) extra.forEach(body => emit(body, null)); else stashed.push(...extra);
  }
  return { markdown: out.join("\n\n") + "\n", stashed, blockIds: outIds };
}

/**
 * QA 复验 16 P1-1：恢复本机草稿时按当前分工拆开——我负责部分的修改照常保存，他人负责部分的修改不提交（提交必被整份拒绝），
 * 留在本机供查看、复制。saved：我负责部分改动的块数；others：他人负责部分被留下的块。
 */
export function splitRestore(markdown: string, baseline: BaselineObject[], editable: Set<string>): { markdown: string; saved: number; others: string[] } {
  const kept = keepMineOnly(markdown, baseline, editable);
  const base = new Set(baseline.map(item => item.markdown.trim()));
  const saved = splitBlocks(kept.markdown).filter(block => !base.has(block.body.trim())).length;
  return { markdown: kept.markdown, saved, others: kept.stashed };
}

export function restoreSplitText(saved: number, others: number): string {
  return `已恢复并保存 ${saved} 处；另有 ${others} 处属于他人负责的部分，未保存，已保留在本机草稿`;
}
