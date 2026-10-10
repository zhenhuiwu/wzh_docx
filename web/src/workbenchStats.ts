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
 * 终审必改：把重绘前的段落下标换算到重绘后的版式里。前后两版段落文字做 LCS 对齐，
 * 分别取“尽早配对”和“尽晚配对”两种极端对齐；两种都把该段配到同一位置才算确定，否则返回 -1（调用方放弃写回）。
 * 不再按“文字相同、位置最近”找段——模板里重复的“无。”会因此写进另一段。
 */
export function mapBlockIndex(before: string[], after: string[], index: number, limit = 4_000_000): number {
  const n = before.length, m = after.length;
  if (index < 0 || index >= n || (n + 1) * (m + 1) > limit) return -1;
  const first = alignedIndex(before, after, index);
  const reversed = alignedIndex([...before].reverse(), [...after].reverse(), n - 1 - index);
  const last = reversed < 0 ? -1 : m - 1 - reversed;
  return first >= 0 && first === last ? first : -1;
}
/** 一种 LCS 对齐（能配就配、配不上先跳过新版的段）：返回 before[index] 配到的 after 下标，未配上为 -1。 */
function alignedIndex(before: string[], after: string[], index: number): number {
  const n = before.length, m = after.length, width = m + 1;
  const dp = new Int32Array((n + 1) * width);  // dp[i*width+j] = before[i:] 与 after[j:] 的 LCS 长度
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i * width + j] = before[i] === after[j] ? dp[(i + 1) * width + j + 1] + 1 : Math.max(dp[(i + 1) * width + j], dp[i * width + j + 1]);
    }
  }
  let i = 0, j = 0;
  while (i < n && j < m) {
    const here = dp[i * width + j];
    if (before[i] === after[j] && here === dp[(i + 1) * width + j + 1] + 1) {
      if (i === index) return j;
      i += 1; j += 1;
    } else if (dp[i * width + j + 1] === here) {
      j += 1;
    } else {
      if (i === index) return -1;
      i += 1;
    }
  }
  return -1;
}
