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
