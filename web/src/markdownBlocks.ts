/**
 * A07（17 §4.5.2）：与服务端 app/markdown_blocks.split 相同的顶层块切分，用来给整篇保存附上 blockIds。
 * 块数必须与服务端一致（编写人员在 enforced 文档上块数不一致会被 422 拒绝）。
 */
export interface MdBlock { kind: string; body: string; level: number | null }

const FENCE = /^ {0,3}(`{3,}|~{3,})/;
const ATX = /^ {0,3}(#{1,6})(?:[ \t]+|$)/;
const LIST = /^ {0,3}(?:[-+*]|\d{1,9}[.)])(?:[ \t]+|$)/;
const TABLE_RULE = /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$/;
const SETEXT = /^ {0,3}(?:=+|-+)[ \t]*$/;
const IMAGE = /^!\[[^\]\n]*\]\(docx-image:[^)\s]+\)$/;
const HTML = /^ {0,3}<[A-Za-z!/?]/;

/** Python str.splitlines(keepends=True) 的行界。 */
function splitLines(text: string): string[] {
  const lines: string[] = [];
  const re = /\r\n|[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]/g;
  let last = 0;
  for (let m = re.exec(text); m; m = re.exec(text)) {
    lines.push(text.slice(last, m.index + m[0].length));
    last = m.index + m[0].length;
  }
  if (last < text.length) lines.push(text.slice(last));
  return lines;
}
const content = (line: string) => line.replace(/[\r\n]+$/, "");
// Python str.strip() 去掉的是 Unicode 空白；这里与 JS trim 一致（差异只在少见控制字符）
const blank = (line: string) => !line.trim();

function listItemEnd(lines: string[], index: number): number {
  const indent = lines[index].length - lines[index].replace(/^ +/, "").length;
  index += 1;
  while (index < lines.length) {
    const current = content(lines[index]);
    if (blank(current)) {
      let ahead = index;
      while (ahead < lines.length && blank(lines[ahead])) ahead += 1;
      if (ahead < lines.length && /^( {2,}|\t)\S/.test(content(lines[ahead]))) { index = ahead; continue; }
      break;
    }
    const deeper = current.length - current.replace(/^ +/, "").length > indent;
    if ((LIST.test(current) && !deeper) || ATX.test(current) || (FENCE.test(current) && !deeper)) break;
    index += 1;
  }
  return index;
}

export function splitBlocks(markdown: string): MdBlock[] {
  const lines = splitLines(markdown);
  const blocks: MdBlock[] = [];
  let index = 0;
  while (index < lines.length) {
    const line = content(lines[index]);
    if (blank(line)) { index += 1; continue; }
    const start = index;
    let level: number | null = null;
    let kind: string;
    const fence = FENCE.exec(line);
    const heading = ATX.exec(line);
    if (fence) {
      const marker = fence[1];
      const esc = marker[0] === "`" ? "`" : "~";
      const closing = new RegExp(`^ {0,3}${esc}{${marker.length},}[ \\t]*$`);
      index += 1;
      while (index < lines.length && !closing.test(content(lines[index]))) index += 1;
      index = Math.min(index + 1, lines.length);
      kind = "code";
    } else if (heading) {
      index += 1;
      kind = "heading";
      level = heading[1].length;
    } else if (line.includes("|") && index + 1 < lines.length && TABLE_RULE.test(content(lines[index + 1])) && lines[index + 1].includes("-")) {
      index += 2;
      while (index < lines.length && !blank(lines[index])) index += 1;
      kind = "table";
    } else if (LIST.test(line)) {
      index = listItemEnd(lines, index);
      kind = "list_item";
    } else if (line.trimStart().startsWith(">") || HTML.test(line) || line.startsWith("    ") || line.startsWith("\t")) {
      while (index < lines.length && !blank(lines[index])) index += 1;
      kind = "other";
    } else {
      index += 1;
      kind = "paragraph";
      while (index < lines.length && !blank(lines[index])) {
        const current = content(lines[index]);
        if (ATX.test(current) || FENCE.test(current)) break;
        if (SETEXT.test(current) && "=-".includes(current.trim()[0])) { index += 1; kind = "other"; break; }
        index += 1;
      }
      if (kind === "paragraph" && IMAGE.test(lines.slice(start, index).join("").trim())) kind = "image";
    }
    blocks.push({ kind, body: lines.slice(start, index).join("").replace(/[\r\n]+$/, ""), level });
  }
  return blocks;
}

export interface BaselineObject { id: string; kind: string; markdown: string }

const norm = (text: string) => text.replace(/\r\n?/g, "\n").trim();

/** 两段文字的相似度（二元组 Dice），用于把间隙里改过的块配回原对象。 */
function similarity(x: string, y: string): number {
  if (x === y) return 1;
  if (x.length < 2 || y.length < 2) return 0;
  const grams = new Map<string, number>();
  for (let k = 0; k < x.length - 1; k++) { const g = x.slice(k, k + 2); grams.set(g, (grams.get(g) ?? 0) + 1); }
  let hit = 0;
  for (let k = 0; k < y.length - 1; k++) { const g = y.slice(k, k + 2); const c = grams.get(g) ?? 0; if (c > 0) { hit++; grams.set(g, c - 1); } }
  return (2 * hit) / (x.length + y.length - 2);
}

/** 最长递增子序列（返回下标），用于 patience 锚点。 */
function lis(values: number[]): number[] {
  const tails: number[] = [], prev: number[] = new Array(values.length).fill(-1);
  for (let k = 0; k < values.length; k++) {
    let lo = 0, hi = tails.length;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (values[tails[mid]] < values[k]) lo = mid + 1; else hi = mid; }
    if (lo > 0) prev[k] = tails[lo - 1];
    tails[lo] = k;
  }
  const out: number[] = [];
  for (let k = tails.length ? tails[tails.length - 1] : -1; k >= 0; k = prev[k]) out.push(k);
  return out.reverse();
}

/** 正文完全相同的块配对：去掉首尾相同部分；小范围用动态规划 LCS；大范围用 patience（两边都唯一的块做锚点，分段递归）。
 *  没有任何按位置的配对，所以大文档里一处插入不会让后面整体错位（27 M-1）。 */
function matchEqual(a: string[], b: string[], a0: number, a1: number, b0: number, b1: number, out: Array<[number, number]>) {
  const head: Array<[number, number]> = [];
  while (a0 < a1 && b0 < b1 && a[a0] === b[b0]) head.push([a0++, b0++]);
  const tail: Array<[number, number]> = [];
  while (a1 > a0 && b1 > b0 && a[a1 - 1] === b[b1 - 1]) tail.unshift([--a1, --b1]);
  out.push(...head);
  const n = a1 - a0, m = b1 - b0;
  if (n > 0 && m > 0) {
    if (n * m <= 1_000_000) {
      const dp: Uint32Array[] = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
      for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) dp[i][j] = a[a0 + i] === b[b0 + j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
      for (let i = 0, j = 0; i < n && j < m;) {
        if (a[a0 + i] === b[b0 + j]) { out.push([a0 + i, b0 + j]); i++; j++; } else if (dp[i + 1][j] >= dp[i][j + 1]) i++; else j++;
      }
    } else {
      const countA = new Map<string, number>(), countB = new Map<string, number>(), posB = new Map<string, number>();
      for (let i = a0; i < a1; i++) countA.set(a[i], (countA.get(a[i]) ?? 0) + 1);
      for (let j = b0; j < b1; j++) { countB.set(b[j], (countB.get(b[j]) ?? 0) + 1); posB.set(b[j], j); }
      const anchors: Array<[number, number]> = [];
      for (let i = a0; i < a1; i++) if (countA.get(a[i]) === 1 && countB.get(a[i]) === 1) anchors.push([i, posB.get(a[i])!]);
      const chosen = lis(anchors.map(pair => pair[1])).map(k => anchors[k]);
      let pi = a0, pj = b0;
      for (const [i, j] of chosen) { matchEqual(a, b, pi, i, pj, j, out); out.push([i, j]); pi = i + 1; pj = j + 1; }
      if (chosen.length) matchEqual(a, b, pi, a1, pj, b1, out);
    }
  }
  out.push(...tail);
}

/**
 * 给提交的每个块配上对象 ID（17 §4.5.2 blockIds）：先按正文完全相同配对（顺序不变），
 * 两个配对之间的间隙里，按顺序把相似（改过）的块配回同类对象；配不上的是新块（null）。同一 ID 只用一次。
 * 配错时服务端按“删除+新增”处理，规则仍成立（不会放宽限制）；负责人配错时服务端改走文本匹配，不丢分工。
 */
export function assignBlockIds(markdown: string, baseline: BaselineObject[]): Array<string | null> {
  const mine = splitBlocks(markdown);
  const a = mine.map(block => norm(block.body));
  const b = baseline.map(item => norm(item.markdown));
  const n = a.length, m = b.length;
  const ids: Array<string | null> = new Array(n).fill(null);
  if (!n || !m) return ids;
  const pairs: Array<[number, number]> = [];
  matchEqual(a, b, 0, n, 0, m, pairs);
  const compatible = (i: number, j: number) => mine[i].kind === baseline[j].kind || (mine[i].kind !== "heading" && baseline[j].kind !== "heading");
  const bounds: Array<[number, number]> = [[-1, -1], ...pairs, [n, m]];
  for (let k = 0; k < bounds.length - 1; k++) {
    const [i0, j0] = bounds[k];
    const [i1, j1] = bounds[k + 1];
    if (k > 0) ids[i0] = baseline[j0].id;
    const gapA = i1 - i0 - 1, gapB = j1 - j0 - 1;
    if (gapA <= 0 || gapB <= 0) continue;
    if (gapA === gapB) {
      // 等长间隙：就地修改
      for (let d = 0; d < gapA; d++) if (compatible(i0 + 1 + d, j0 + 1 + d)) ids[i0 + 1 + d] = baseline[j0 + 1 + d].id;
      continue;
    }
    // 不等长：按顺序找足够相似的块（大间隙只看前 200 个，避免平方开销）
    let from = i0 + 1;
    for (let j = j0 + 1; j < j1 && j < j0 + 1 + 200; j++) {
      for (let i = from; i < i1 && i < from + 200; i++) {
        if (compatible(i, j) && similarity(a[i], b[j]) >= 0.5) { ids[i] = baseline[j].id; from = i + 1; break; }
      }
    }
  }
  const seen = new Set<string>();
  return ids.map(id => (id && !seen.has(id) ? (seen.add(id), id) : null));
}

/** 关页保存（复验 P2-1）：对象带修订号的基准。 */
export interface RevisionedObject { id: string; kind: string; revision: number; markdown: string }
export type KeepaliveOp =
  | { op: "update"; objectId: string; baseRevision: number; markdown: string }
  | { op: "delete"; objectId: string; baseRevision: number }
  | { op: "insert"; tempId: string; afterObjectId?: string; beforeObjectId?: string; markdown: string };

/**
 * 关页时把正文相对 base 的改动换成 /objects/patch 的块操作（只带改过的块，请求很小，能放进 keepalive 的 64KB）。
 * 只处理块的顺序没变的情况（改段、回车拆段/新增段、删段）；配不上、顺序变了或改动太多返回 null，由本机草稿兜底。
 */
export function keepalivePatch(content: string, base: RevisionedObject[], maxOps = 500): KeepaliveOp[] | null {
  if (!base.length) return null;
  const blocks = splitBlocks(content);
  const ids = assignBlockIds(content, base);
  const at = new Map(base.map((item, index) => [item.id, index]));
  const ops: KeepaliveOp[] = [];
  const kept = new Set<string>();
  let last = -1, anchor: string | undefined, temps = 0;
  for (const [index, block] of blocks.entries()) {
    const id = ids[index];
    if (id && at.has(id) && !kept.has(id)) {
      const position = at.get(id)!;
      if (position < last) return null; // 顺序变了（移动）：交给草稿
      last = position;
      kept.add(id);
      const object = base[position];
      if (norm(object.markdown) !== norm(block.body)) {
        if (!block.body.trim()) return null;
        ops.push({ op: "update", objectId: id, baseRevision: object.revision, markdown: block.body });
      }
      anchor = id;
    } else {
      if (!block.body.trim()) continue;
      const tempId = `ka${++temps}`;
      // 文档开头插入：放在第一个对象之前
      ops.push(anchor ? { op: "insert", tempId, afterObjectId: anchor, markdown: block.body } : { op: "insert", tempId, beforeObjectId: base[0].id, markdown: block.body });
      anchor = tempId;
    }
  }
  for (const object of base) if (!kept.has(object.id)) ops.push({ op: "delete", objectId: object.id, baseRevision: object.revision });
  return ops.length && ops.length <= maxOps ? ops : null;
}
