/**
 * 工作台重塑（12 §8.1 第 4、5、9 步）：目录状态、字数与“合并进来的块”，都只读 Markdown，不参与保存。
 */
import { blocksOf } from "./textDiff";

/** 去掉 Markdown 标记后的字数：不计空白和标点之外的标记符号（#、*、`、|、>、列表符号、链接地址、图片）。 */
export function countChars(markdown: string): number {
  const text = markdown
    .replace(/<!--[\s\S]*?-->/g, "")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/^\s{0,3}#{1,6}[ \t]+/gm, "")
    .replace(/^\s*(?:[-*+]|\d+[.)])[ \t]+/gm, "")
    .replace(/^\s*\|?\s*:?-{3,}.*$/gm, "")
    .replace(/^\s*>[ \t]?/gm, "")
    .replace(/\\(.)/g, "$1")
    .replace(/[*_`|~]/g, "");
  return text.replace(/\s+/g, "").length;
}

export interface OutlineEntry {
  level: number; text: string;
  /** 相对最浅一级的缩进层级，0 起。 */
  depth: number;
  /** 标题下（到下一个标题为止）的字数。 */
  chars: number;
  /** 待编写：标题下没有正文，也没有下级标题。 */
  empty: boolean;
}

/** 与 documentHeadings 同一套规则（`#` 后至少一个空格和文字）切章节，序号与目录、selectedHeading 一一对应。 */
export function outlineEntries(markdown: string): OutlineEntry[] {
  const lines = markdown.replace(/\r\n/g, "\n").split("\n");
  const found: Array<{ level: number; text: string; line: number }> = [];
  lines.forEach((line, index) => {
    const match = /^(#{1,6})[ \t]+([^\n]+)$/.exec(line);
    if (match) found.push({ level: match[1].length, text: match[2].replace(/\\([\\`*_{}\[\]<>|#])/g, "$1").trim(), line: index });
  });
  const min = found.length ? Math.min(...found.map(item => item.level)) : 1;
  return found.map((item, index) => {
    const next = found[index + 1];
    const body = lines.slice(item.line + 1, next ? next.line : lines.length).join("\n");
    const chars = countChars(body);
    const hasChild = Boolean(next && next.level > item.level);
    return { level: item.level, text: item.text, depth: item.level - min, chars, empty: chars === 0 && !hasChild };
  });
}

/** 界面复验 N4：保存时服务器合并进来的块（服务器正文里有、本次提交里没有）。 */
export function mergedInBlocks(sent: string, server: string): string[] {
  const mine = new Set(blocksOf(sent));
  return blocksOf(server).filter(block => !mine.has(block) && !/^#{1,6}[ \t]/.test(block));
}

/** 载入期间只在一个段落里插入了一串字（最常见的连续打字）：返回段落序号、插入位置和插入的字；其他改法返回 null。 */
export function typedInsertion(before: string[], after: string[], caret?: { index: number; offset: number }): { index: number; text: string; offset: number; insert: string } | null {
  if (before.length !== after.length) return null;
  const changed = before.map((text, index) => text === after[index] ? -1 : index).filter(index => index >= 0);
  if (changed.length !== 1) return null;
  const index = changed[0], old = before[index], now = after[index];
  if (now.length <= old.length) return null;
  // 优先按光标定位：插入的字止于光标处（同样的字可有多种对齐，如“（锁前）（”后再打“（…）”），
  // 这样写回后光标仍在用户原来的位置，之后接着打的字不会落错
  const added = now.length - old.length;
  if (caret && caret.index === index && caret.offset >= added && caret.offset <= now.length) {
    const start = caret.offset - added;
    if (now.slice(0, start) + now.slice(caret.offset) === old) return { index, text: old, offset: start, insert: now.slice(start, caret.offset) };
  }
  let prefix = 0;
  while (prefix < old.length && old[prefix] === now[prefix]) prefix += 1;
  let suffix = 0;
  while (suffix < old.length - prefix && old[old.length - 1 - suffix] === now[now.length - 1 - suffix]) suffix += 1;
  if (prefix + suffix !== old.length) return null;
  return { index, text: old, offset: prefix, insert: now.slice(prefix, now.length - suffix) };
}
/**
 * 把重绘前的段落下标换算到重绘后的版式里（终审必改 F-M1，最后一审建议 1/2 修正）。
 * 判据：在前后两版段落文字的**所有** LCS 最优对齐中，before[index] 都必须配对、且都配到同一个 after 下标，才返回该下标；
 * 否则返回 -1（调用方放弃写回）。用前缀 DP 与后缀 DP 求可行位置集合，只保留两行，内存 O(m)、时间 O(n·m)。
 * 超过 limit 格时先去掉前后相同的段落再算；这时只在不会和被去掉部分混淆（文字在两版中都不出现在去掉的部分）时才返回。
 */
export function mapBlockIndex(before: string[], after: string[], index: number, limit = 36_000_000, small = 250_000): number {
  const n = before.length, m = after.length;
  if (index < 0 || index >= n) return -1;
  if (n * m <= small) return exactIndex(before, after, index);
  // 增量审查 M2：任何段数都先去掉前后相同的段落，只对变化区间做精确计算
  let head = 0;
  while (head < n && head < m && before[head] === after[head]) head += 1;
  let tail = 0;
  while (tail < n - head && tail < m - head && before[n - 1 - tail] === after[m - 1 - tail]) tail += 1;
  const text = before[index];
  const full = () => (n * m <= limit ? exactIndex(before, after, index) : -1);
  if (index < head || index >= n - tail) {
    // 落在去掉的前后相同部分：文字在两版中都唯一才直接按位置确定（重复文字可能有别的最优对齐，交给整篇精确计算）
    const j = index < head ? index : m - (n - index);
    return countOf(before, text) === 1 && countOf(after, text) === 1 ? j : full();
  }
  for (let i = 0; i < head; i++) if (before[i] === text) return full();
  for (let i = n - tail; i < n; i++) if (before[i] === text) return full();
  const core = (n - head - tail) * (m - head - tail);
  if (core > limit) return -1;
  const j = exactIndex(before.slice(head, n - tail), after.slice(head, m - tail), index - head);
  return j < 0 ? -1 : j + head;
}
function countOf(list: string[], text: string): number {
  let count = 0;
  for (const item of list) if (item === text && ++count > 1) break;
  return count;
}
/** 所有 LCS 最优对齐中 before[k] 的去向：唯一且必配对时返回下标，否则 -1。 */
function exactIndex(a: string[], b: string[], k: number): number {
  const n = a.length, m = b.length;
  // 前缀 DP：P[i][j] = LCS(a[:i], b[:j])，只算到第 k 行
  let prev = new Int32Array(m + 1), row = new Int32Array(m + 1);
  for (let i = 1; i <= k; i++) {
    row[0] = 0;
    for (let j = 1; j <= m; j++) row[j] = a[i - 1] === b[j - 1] ? prev[j - 1] + 1 : Math.max(prev[j], row[j - 1]);
    [prev, row] = [row, prev];
  }
  const pk = prev;  // P[k][*]
  // 后缀 DP：S[i][j] = LCS(a[i:], b[j:])，算到第 k 行，同时留下第 k+1 行
  let next = new Int32Array(m + 1), cur = new Int32Array(m + 1);
  for (let i = n - 1; i > k; i--) {
    cur[m] = 0;
    for (let j = m - 1; j >= 0; j--) cur[j] = a[i] === b[j] ? next[j + 1] + 1 : Math.max(next[j], cur[j + 1]);
    [next, cur] = [cur, next];
  }
  const sk1 = next;  // S[k+1][*]
  let best = 0;
  for (let j = 0; j <= m; j++) {
    const viaMatch = j < m && a[k] === b[j] ? pk[j] + 1 + sk1[j + 1] : 0;
    best = Math.max(best, pk[j] + sk1[j], viaMatch);
  }
  // k 不配对也能达到最优 → 不确定
  for (let j = 0; j <= m; j++) if (pk[j] + sk1[j] === best) return -1;
  let found = -1;
  for (let j = 0; j < m; j++) {
    if (a[k] === b[j] && pk[j] + 1 + sk1[j + 1] === best) {
      if (found >= 0) return -1;
      found = j;
    }
  }
  return found;
}

/**
 * 最后一审建议 2：恢复光标用。先按 mapBlockIndex 精确换算；不确定（或超大文档放弃）时，
 * 光标所在段的文字在前后两版里都只出现一次就放到那一段，否则返回 -1（调用方不放光标）。
 */
export function caretBlockIndex(before: string[], after: string[], index: number, limit?: number): number {
  // 只用于放回光标：文字在两版中都唯一就直接取它（增量审查 M2：大文档不做整段精确计算）
  const text = before[index];
  if (text === undefined) return -1;
  const unique = before.indexOf(text) === before.lastIndexOf(text);
  const at = unique ? after.indexOf(text) : -1;
  if (at >= 0 && at === after.lastIndexOf(text)) return at;
  return mapBlockIndex(before, after, index, limit);
}

/** 每次按键做精确判定的格数上限（增量审查 M2：远低于 50ms）。 */
const KEY_LIMIT = 1_000_000;

/** 小序列一次求出每个 a[k] 在所有 LCS 最优对齐中的确定去向（不确定或不配对为 -1）。 */
function certainMatches(a: string[], b: string[]): Int32Array {
  const n = a.length, m = b.length, w = m + 1;
  const P = new Int32Array((n + 1) * w), S = new Int32Array((n + 2) * (m + 2));
  const sw = m + 2;
  for (let i = 1; i <= n; i++) for (let j = 1; j <= m; j++) P[i * w + j] = a[i - 1] === b[j - 1] ? P[(i - 1) * w + j - 1] + 1 : Math.max(P[(i - 1) * w + j], P[i * w + j - 1]);
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) S[i * sw + j] = a[i] === b[j] ? S[(i + 1) * sw + j + 1] + 1 : Math.max(S[(i + 1) * sw + j], S[i * sw + j + 1]);
  const best = P[n * w + m], out = new Int32Array(n).fill(-1);
  for (let k = 0; k < n; k++) {
    let skip = false;
    for (let j = 0; j <= m && !skip; j++) if (P[k * w + j] + S[(k + 1) * sw + j] === best) skip = true;
    if (skip) continue;
    let found = -1;
    for (let j = 0; j < m; j++) {
      if (a[k] === b[j] && P[k * w + j] + 1 + S[(k + 1) * sw + j + 1] === best) { if (found >= 0) { found = -1; break; } found = j; }
    }
    out[k] = found;
  }
  return out;
}

type Hunk = { sa: number; sb: number; ma: number; mb: number };
/** s→m 的改动区间（我自己的修改）：去掉前后相同段，中间按“确定配对”的段切成多段改动。 */
function hunksOf(s: string[], m: string[]): Hunk[] | null {
  let head = 0;
  while (head < s.length && head < m.length && s[head] === m[head]) head++;
  let tail = 0;
  while (tail < s.length - head && tail < m.length - head && s[s.length - 1 - tail] === m[m.length - 1 - tail]) tail++;
  const sa = head, sb = s.length - tail, ma = head, mb = m.length - tail;
  if (sa === sb && ma === mb) return [];
  if ((sb - sa) * (mb - ma) > 40_000) return (sb - sa) > 200 || (mb - ma) > 200 ? null : [{ sa, sb, ma, mb }];
  const match = certainMatches(s.slice(sa, sb), m.slice(ma, mb));
  const hunks: Hunk[] = [];
  let ps = sa, pm = ma;
  for (let k = 0; k < sb - sa; k++) {
    const j = match[k];
    if (j < 0 || j + ma < pm) continue;
    if (sa + k > ps || ma + j > pm) hunks.push({ sa: ps, sb: sa + k, ma: pm, mb: ma + j });
    ps = sa + k + 1; pm = ma + j + 1;
  }
  if (sb > ps || mb > pm) hunks.push({ sa: ps, sb, ma: pm, mb });
  return hunks;
}

/**
 * 把 s→m 的每段改动分别平移到 t 上（s 与 t 的差别是别人的修改）。inner 为 true 时，单段改动在 t 中找不到原文
 * （别人改了同一个列表/表格块的另一行）就按行再平移一次。任一段对不上返回 null。
 */
function rebaseSeq(s: string[], m: string[], t: string[], inner: boolean): string[] | null {
  const hunks = hunksOf(s, m);
  if (hunks === null) return null;
  if (!hunks.length) return t;
  const cs = new Map<string, number>(), ct = new Map<string, number>(), at = new Map<string, number>();
  for (const x of s) cs.set(x, (cs.get(x) ?? 0) + 1);
  t.forEach((x, i) => { ct.set(x, (ct.get(x) ?? 0) + 1); at.set(x, i); });
  const direct = (k: number) => (cs.get(s[k]) === 1 && ct.get(s[k]) === 1 ? at.get(s[k])! : mapBlockIndex(s, t, k, KEY_LIMIT, 40_000));
  // 重复文字（如“无。”）定位不了时，借相邻段：左右两邻都能确定、在 t 里正好夹着一段且就是这段原文
  const map = (k: number) => {
    const j = direct(k);
    if (j >= 0) return j;
    const l = k > 0 ? direct(k - 1) : -1;
    const r = k + 1 < s.length ? direct(k + 1) : t.length;
    return (k === 0 || l >= 0) && r >= 0 && r === l + 2 && t[l + 1] === s[k] ? l + 1 : -1;
  };
  const edits: { start: number; end: number; rows: string[] }[] = [];
  for (const h of hunks) {
    const rows = m.slice(h.ma, h.mb);
    const leftOf = () => (h.sa > 0 ? map(h.sa - 1) : -1);
    if (h.sb > h.sa) {
      let start = map(h.sa), ok = start >= 0;
      for (let k = h.sa + 1; ok && k < h.sb; k++) ok = map(k) === start + (k - h.sa);
      if (ok) { edits.push({ start, end: start + (h.sb - h.sa), rows }); continue; }
      // 改动段本身不能唯一定位（如重复的“无。”）：两侧邻段能定位、且中间原样就是这几段时放在那里
      const left = leftOf(), right = h.sb < s.length ? map(h.sb) : t.length;
      if ((h.sa > 0 && left < 0) || right < 0) return null;
      if (right - left - 1 === h.sb - h.sa && s.slice(h.sa, h.sb).every((x, i) => t[left + 1 + i] === x)) {
        edits.push({ start: left + 1, end: right, rows });
        continue;
      }
      // 单段被改且 t 里该段也被别人改了（同一列表/表格块的另一行）：按两侧邻段定位后逐行平移
      if (!inner || h.sb - h.sa !== 1 || h.mb - h.ma !== 1 || right - left !== 2) return null;
      start = left + 1;
      const merged = rebaseSeq(s[h.sa].split("\n"), m[h.ma].split("\n"), t[start].split("\n"), false);
      if (merged === null) return null;
      edits.push({ start, end: start + 1, rows: [merged.join("\n")] });
      continue;
    }
    // 纯插入：插入点前后的段都得能确定位置且在 t 里相邻
    const left = leftOf(), right = h.sa < s.length ? map(h.sa) : t.length;
    if ((h.sa > 0 && left < 0) || right < 0 || right !== left + 1) return null;
    edits.push({ start: right, end: right, rows });
  }
  for (let i = 1; i < edits.length; i++) if (edits[i].start < edits[i - 1].end) return null;
  const out = t.slice();
  for (let i = edits.length - 1; i >= 0; i--) out.splice(edits[i].start, edits[i].end - edits[i].start, ...edits[i].rows);
  return out;
}

/**
 * 终验 F-H2 / 增量审查 M1、M2：把“本地未保存的修改”平移到服务器新正文上。
 * sent：上一个一致点的正文（保存提交的正文，或编辑区与 value 对应时的编辑区文字）；mine：现在的正文；server：服务器新正文。
 * 按空行分块；sent→mine 的多段改动各自定位到 server 中位置确定的对应块（列表/表格块按行），全部对得上才返回结果。
 * 对不上（别人也改了正在改的那一行，或位置不确定）返回 null：调用方以 sent 的编辑区文字作为显式基准交服务器三方合并，
 * 已保存过的内容在基准里，不会被当成对方的修改。
 */
export function rebaseBlocks(sent: string, mine: string, server: string): string | null {
  if (sent === mine) return server;
  if (sent === server) return mine;
  const out = rebaseSeq(sent.split("\n\n"), mine.split("\n\n"), server.split("\n\n"), true);
  return out === null ? null : out.join("\n\n");
}

/**
 * 测试工程师 F-H3 的硬性不变量：只有正文确实包含服务器新版本的全部内容（除了我自己未保存的修改）时，才能把基准前移。
 * sent 是那次提交的正文，serverText 是服务器返回的新正文，value 是平移后的正文：serverText 里相对 sent 新出现的每一行
 * （对方写进来的；按行比较，列表/表格里对方改的那一行同样要在）都必须原样出现在 value 里。
 */
export function keepsServerChanges(sent: string, serverText: string, value: string): boolean {
  const before = new Set(sent.split("\n"));
  const after = new Set(value.split("\n"));
  return serverText.split("\n").every(line => before.has(line) || after.has(line));
}
