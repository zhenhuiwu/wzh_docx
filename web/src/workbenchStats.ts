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
export function mapBlockIndex(before: string[], after: string[], index: number, limit = 36_000_000): number {
  const n = before.length, m = after.length;
  if (index < 0 || index >= n) return -1;
  if (n * m <= limit) return exactIndex(before, after, index);
  let head = 0;
  while (head < n && head < m && before[head] === after[head]) head += 1;
  let tail = 0;
  while (tail < n - head && tail < m - head && before[n - 1 - tail] === after[m - 1 - tail]) tail += 1;
  const text = before[index];
  const trimmed = new Set([...before.slice(0, head), ...before.slice(n - tail), ...after.slice(0, head), ...after.slice(m - tail)]);
  if (index < head || index >= n - tail) {
    // 落在去掉的前后相同部分：文字在两版中都唯一才确定（重复文字可能有别的最优对齐）
    const j = index < head ? index : m - (n - index);
    return countOf(before, text) === 1 && countOf(after, text) === 1 ? j : -1;
  }
  if (trimmed.has(text)) return -1;
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
  const mapped = mapBlockIndex(before, after, index, limit);
  if (mapped >= 0) return mapped;
  const text = before[index];
  if (text === undefined || before.indexOf(text) !== before.lastIndexOf(text)) return -1;
  const at = after.indexOf(text);
  return at >= 0 && at === after.lastIndexOf(text) ? at : -1;
}

/**
 * 终验 F-H2：把“本地未保存的修改”平移到服务器新正文上。
 * sent：这次保存提交的正文（或编辑区与 value 对应时的编辑区文字）；mine：编辑区现在的正文；server：服务器返回的新正文。
 * 按空行分块，取 sent→mine 去掉前后相同块后的一段改动，要求这段被改的块在 server 中原样存在且位置确定、连续，
 * 才把它替换成 mine 的对应块；对不上（别人也改了这些块或位置不确定）返回 null，由调用方按旧基准合并。
 */
export function rebaseBlocks(sent: string, mine: string, server: string): string | null {
  if (sent === mine) return server;
  if (sent === server) return mine;
  const s = sent.split("\n\n"), m = mine.split("\n\n"), t = server.split("\n\n");
  let head = 0;
  while (head < s.length && head < m.length && s[head] === m[head]) head++;
  let tail = 0;
  while (tail < s.length - head && tail < m.length - head && s[s.length - 1 - tail] === m[m.length - 1 - tail]) tail++;
  const from = head, to = s.length - tail;
  const replacement = m.slice(head, m.length - tail);
  let start: number, end: number;
  if (to - from > 200) return null; // 大段改动不逐块换算，按旧基准合并
  if (from < to) {
    start = mapBlockIndex(s, t, from);
    if (start < 0) return null;
    for (let k = from; k < to; k++) if (mapBlockIndex(s, t, k) !== start + (k - from)) return null;
    end = start + (to - from);
  } else {
    // 纯插入：插入点前后的块都得能确定位置且在 server 里相邻
    const before = from > 0 ? mapBlockIndex(s, t, from - 1) : -1;
    const after = from < s.length ? mapBlockIndex(s, t, from) : t.length;
    if (from > 0 && before < 0) return null;
    if (from < s.length && after < 0) return null;
    const at = from > 0 ? before + 1 : 0;
    if (from < s.length && after !== at) return null;
    if (from === 0 && after !== 0) return null;
    start = end = at;
  }
  return [...t.slice(0, start), ...replacement, ...t.slice(end)].join("\n\n");
}
