/**
 * 界面验收 M1：保存被某一段卡住（WORD_BLOCK_PROTECTED）时“撤销这一段的修改”。
 * 按空行把正文切成块，与上次保存的正文对齐；在有变化的片段里找文字与失败块一致的那一段，换回保存时的原文。
 * 找不到时返回 null（界面提示手动处理），绝不改动其他段落。
 */
// 设计师复验 3 C-2：表格 markdown 的分隔行（|---|、| :--- | ---: |、不带外侧竖线的 --- | ---）不是单元格文字，
// 服务器的 blockText 只取单元格文字；单元格内换行写作 <br>，blockText 里没有它。规整前先去掉这两样，否则表格永远对不上。
// 分隔行只认表头下面那一行（上一行是表格行、再上一行不是），单元格全是“-”的数据行照常保留。
const TABLE_RULE = /^[ \t]*\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t\r]*$/;
function dropTableRules(text: string): string {
  const lines = text.split("\n");
  const isRow = (line: string | undefined) => line !== undefined && line.includes("|");
  return lines.filter((line, index) => !(TABLE_RULE.test(line) && isRow(lines[index - 1]) && !isRow(lines[index - 2]))).join("\n");
}
const normalize = (text: string) => dropTableRules(text).replace(/<br\s*\/?>/gi, "").replace(/\\(.)/g, "$1").replace(/[\s*_`#>|]+/g, "").replace(/^(\d+\.|[-+])/, "");

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

const sameText = (block: string, target: string) => {
  const text = normalize(block);
  return Boolean(text) && Boolean(target) && (text === target || text.includes(target) || target.includes(text));
};

const similarity = (a: string, b: string) => {
  const x = normalize(a), y = normalize(b);
  let prefix = 0;
  while (prefix < Math.min(x.length, y.length) && x[prefix] === y[prefix]) prefix++;
  let suffix = 0;
  while (suffix < Math.min(x.length, y.length) - prefix && x[x.length - 1 - suffix] === y[y.length - 1 - suffix]) suffix++;
  return prefix + suffix;
};

/**
 * 界面复验 N1：被拒的是“删除整段”时（服务器给出 deletedText），被删的段落只在上次保存的正文里。
 * 在有变化的片段里按“旧内容”找这一段；新内容里已没有它时，只把这一段插回它原来的位置，片段里的其他修改（含新增段）不动。
 */
export function restoreDeletedBlock(saved: string, current: string, deletedText: string): string | null {
  const before = blocks(saved), after = blocks(current);
  const target = normalize(deletedText);
  const found: Array<{ hunk: Hunk; index: number }> = [];
  for (const hunk of hunks(before, after)) {
    const oldBlocks = before.slice(hunk.oldStart, hunk.oldEnd);
    const index = oldBlocks.findIndex(block => normalize(block) === target);
    if (index < 0) continue;
    // 新内容里还有含这段文字的块（例如并进了上一段）：不是单纯删除，交给按失败块撤销
    if (after.slice(hunk.newStart, hunk.newEnd).some(block => sameText(block, target))) continue;
    found.push({ hunk, index });
  }
  if (found.length !== 1) return null;
  const [{ hunk, index }] = found;
  const at = hunk.newStart + Math.min(index, hunk.newEnd - hunk.newStart);
  return [...after.slice(0, at), before[hunk.oldStart + index], ...after.slice(at)].join("\n\n");
}

/**
 * 测试复测 N2：只撤销被拒的那一块。片段里块数不同时（例如在被拒段后新增了一段）不再整片段还原：
 * - 被拒块由两段合并而来（文字正好是相邻两段之和）→ 换回这两段；
 * - 否则换回片段里与它最相近的旧块；片段里没有旧块（被拒块是新增的）→ 只去掉这一块。
 * 其余新增、删除、修改都保留。
 */
export function revertBlock(saved: string, current: string, blockText: string, deletedText?: string): string | null {
  if (deletedText) {
    const restored = restoreDeletedBlock(saved, current, deletedText);
    if (restored !== null) return restored;
  }
  const before = blocks(saved), after = blocks(current);
  const target = normalize(blockText);
  const changed = hunks(before, after);
  const match = changed.filter(hunk => after.slice(hunk.newStart, hunk.newEnd).some(block => sameText(block, target)));
  if (match.length !== 1) return null;
  const [hunk] = match;
  const newBlocks = after.slice(hunk.newStart, hunk.newEnd);
  const oldBlocks = before.slice(hunk.oldStart, hunk.oldEnd);
  const hits = newBlocks.map((block, index) => (sameText(block, target) ? index : -1)).filter(index => index >= 0);
  const pairOf = (failed: string) => oldBlocks.findIndex((block, index) => index + 1 < oldBlocks.length && normalize(block + oldBlocks[index + 1]) === normalize(failed));
  if (newBlocks.length === oldBlocks.length && !(hits.length === 1 && pairOf(newBlocks[hits[0]]) >= 0)) {
    const replacement = newBlocks.map((block, index) => (hits.includes(index) ? oldBlocks[index] : block));
    return [...after.slice(0, hunk.newStart), ...replacement, ...after.slice(hunk.newEnd)].join("\n\n");
  }
  if (hits.length !== 1) return null;
  const [hit] = hits;
  const failed = newBlocks[hit];
  let replacement: string[] = [];
  const pair = pairOf(failed);
  if (pair >= 0) {
    replacement = [oldBlocks[pair], oldBlocks[pair + 1]];
  } else if (oldBlocks.length) {
    let best = 0;
    oldBlocks.forEach((block, index) => { if (similarity(block, failed) > similarity(oldBlocks[best], failed)) best = index; });
    replacement = [oldBlocks[best]];
  }
  const start = hunk.newStart + hit;
  return [...after.slice(0, start), ...replacement, ...after.slice(start + 1)].join("\n\n");
}
