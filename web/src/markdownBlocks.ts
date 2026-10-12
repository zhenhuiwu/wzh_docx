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

/**
 * 给提交的每个块配上对象 ID（17 §4.5.2 blockIds）：先按正文完全相同做最长公共子序列配对（顺序不变），
 * 两个配对之间剩下的块按位置逐个配对同类对象（就地修改）；配不上的是新块（null）。
 * 同一 ID 只用一次。配错时服务端按“删除+新增”处理，规则仍成立（不会放宽限制）。
 */
export function assignBlockIds(markdown: string, baseline: BaselineObject[]): Array<string | null> {
  const mine = splitBlocks(markdown);
  const a = mine.map(block => norm(block.body));
  const b = baseline.map(item => norm(item.markdown));
  const n = a.length, m = b.length;
  const ids: Array<string | null> = new Array(n).fill(null);
  if (!n || !m) return ids;
  let pairs: Array<[number, number]> = [];
  if (n * m <= 4_000_000) {
    const dp: Uint32Array[] = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
    for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    for (let i = 0, j = 0; i < n && j < m;) {
      if (a[i] === b[j]) { pairs.push([i, j]); i++; j++; } else if (dp[i + 1][j] >= dp[i][j + 1]) i++; else j++;
    }
  } else {
    // 超大文档：只按前后相同的部分配对
    let p = 0;
    while (p < n && p < m && a[p] === b[p]) { pairs.push([p, p]); p++; }
    const tail: Array<[number, number]> = [];
    for (let i = n - 1, j = m - 1; i >= p && j >= p && a[i] === b[j]; i--, j--) tail.unshift([i, j]);
    pairs = pairs.concat(tail);
  }
  const bounds: Array<[number, number]> = [[-1, -1], ...pairs, [n, m]];
  for (let k = 0; k < bounds.length - 1; k++) {
    const [i0, j0] = bounds[k];
    const [i1, j1] = bounds[k + 1];
    if (k > 0) ids[i0] = baseline[j0].id;
    for (let i = i0 + 1, j = j0 + 1; i < i1 && j < j1; i++, j++) {
      if (mine[i].kind === baseline[j].kind || (mine[i].kind !== "heading" && baseline[j].kind !== "heading")) ids[i] = baseline[j].id;
    }
  }
  const seen = new Set<string>();
  return ids.map(id => (id && !seen.has(id) ? (seen.add(id), id) : null));
}
