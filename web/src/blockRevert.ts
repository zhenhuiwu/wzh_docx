/**
 * 界面验收 M1：保存被某一段卡住（WORD_BLOCK_PROTECTED）时“撤销这一段的修改”。
 * 按空行把正文切成块，与上次保存的正文对齐；在有变化的片段里找文字与失败块一致的那一段，换回保存时的原文。
 * 找不到时返回 null（界面提示手动处理），绝不改动其他段落。
 */
const normalize = (text: string) => text.replace(/\\(.)/g, "$1").replace(/[\s*_`#>|]+/g, "").replace(/^(\d+\.|[-+])/, "");

function blocks(markdown: string): string[] {
  return markdown.split(/\n{2,}/);
}

interface Hunk { oldStart: number; oldEnd: number; newStart: number; newEnd: number }

function hunks(a: string[], b: string[]): Hunk[] {
  // 最长公共子序列（块数一般几百，O(n·m) 可接受）
  const n = a.length, m = b.length;
  const table: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
    table[i][j] = a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
  const found: Hunk[] = [];
  let i = 0, j = 0, start: [number, number] | null = null;
  while (i < n || j < m) {
    if (i < n && j < m && a[i] === b[j]) {
      if (start) { found.push({ oldStart: start[0], oldEnd: i, newStart: start[1], newEnd: j }); start = null; }
      i++; j++;
    } else {
      start ??= [i, j];
      if (j < m && (i >= n || table[i][j + 1] >= table[i + 1][j])) j++;
      else i++;
    }
  }
  if (start) found.push({ oldStart: start[0], oldEnd: n, newStart: start[1], newEnd: m });
  return found;
}

export function revertBlock(saved: string, current: string, blockText: string): string | null {
  const before = blocks(saved), after = blocks(current);
  const target = normalize(blockText);
  const changed = hunks(before, after);
  const match = changed.filter(hunk => after.slice(hunk.newStart, hunk.newEnd).some(block => {
    const text = normalize(block);
    return Boolean(text) && Boolean(target) && (text === target || text.includes(target) || target.includes(text));
  }));
  if (match.length !== 1) return null;
  const [hunk] = match;
  // 片段内只换回与失败块对应的那一块；新增/删除块数不同时整片段还原（片段只含这一处修改）
  const newBlocks = after.slice(hunk.newStart, hunk.newEnd);
  const oldBlocks = before.slice(hunk.oldStart, hunk.oldEnd);
  let replacement: string[];
  if (newBlocks.length === oldBlocks.length) {
    replacement = newBlocks.map((block, index) => {
      const text = normalize(block);
      return text === target || text.includes(target) || target.includes(text) ? oldBlocks[index] : block;
    });
  } else {
    replacement = oldBlocks;
  }
  return [...after.slice(0, hunk.newStart), ...replacement, ...after.slice(hunk.newEnd)].join("\n\n");
}
