/**
 * 冲突卡片逐词差异与并排对比的句子对齐（界面验收 M4；02 §3.4）。
 * 中文按字、英文/数字按词切分；规模过大时退化为整段替换，避免卡顿。
 */
export interface DiffPart { type: "eq" | "ins" | "del"; text: string }

export function tokenize(text: string): string[] {
  return text.match(/[A-Za-z0-9_]+|\s+|[\s\S]/g) ?? [];
}

const LIMIT = 2_000_000;

function lcsDiff<T>(a: T[], b: T[], same: (x: T, y: T) => boolean): Array<{ type: DiffPart["type"]; a?: T; b?: T }> {
  const n = a.length, m = b.length;
  if (n * m > LIMIT) return [...a.map(x => ({ type: "del" as const, a: x })), ...b.map(y => ({ type: "ins" as const, b: y }))];
  const table: Uint32Array[] = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
    table[i][j] = same(a[i], b[j]) ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
  const out: Array<{ type: DiffPart["type"]; a?: T; b?: T }> = [];
  let i = 0, j = 0;
  while (i < n || j < m) {
    if (i < n && j < m && same(a[i], b[j])) { out.push({ type: "eq", a: a[i], b: b[j] }); i++; j++; }
    else if (i < n && (j >= m || table[i + 1][j] >= table[i][j + 1])) { out.push({ type: "del", a: a[i] }); i++; }
    else { out.push({ type: "ins", b: b[j] }); j++; }
  }
  return out;
}

/** 相对原内容的增删（相邻同类合并）。 */
export function diffWords(base: string, changed: string): DiffPart[] {
  const parts: DiffPart[] = [];
  for (const step of lcsDiff(tokenize(base), tokenize(changed), (x, y) => x === y)) {
    const text = (step.type === "del" ? step.a : step.b) as string;
    const last = parts[parts.length - 1];
    if (last && last.type === step.type) last.text += text;
    else parts.push({ type: step.type, text });
  }
  return parts;
}

/** 按句切分（句末标点或换行之后断开，标点留在句尾）。表格按行。 */
export function sentences(text: string): string[] {
  if (isTable(text)) return text.split("\n").filter(line => line.trim());
  return (text.match(/[^。！？；!?;\n]+[。！？；!?;]*\n?|\n/g) ?? []).map(part => part.replace(/\n$/, "")).filter(Boolean);
}

export function isTable(text: string): boolean {
  const lines = text.trim().split("\n");
  return lines.length >= 2 && lines.every(line => line.trim().startsWith("|"));
}

export interface CompareRow { base: string; theirs: string; mine: string; differs: boolean }

/** 三栏按句对齐：以“对方 ↔ 我的”的句子级 LCS 为主轴，原内容按同样方式挂到对方一侧。 */
export function alignRows(base: string, theirs: string, mine: string, byBlock = false): CompareRow[] {
  // 界面复验 N7：STRUCTURE（段落被拆分/合并）按块对齐，避免逐句对齐出现多行“（无）”。
  const units = (text: string) => byBlock ? blocksOf(text) : sentences(text);
  const t = units(theirs), m = units(mine), b = units(base);
  // 原内容挂到对方一侧：相同的句子一一对应；被对方改掉的原句挂到紧随其后的对方新句上
  const baseOf = new Map<number, string>();
  let ti = 0, pending = "";
  for (const step of lcsDiff(b, t, (x, y) => x === y)) {
    if (step.type === "del") { pending += step.a as string; continue; }
    if (step.type === "eq") baseOf.set(ti, step.a as string);
    else if (pending) { baseOf.set(ti, pending); pending = ""; }
    ti++;
  }
  const rows: CompareRow[] = [];
  let theirIndex = 0;
  for (const step of lcsDiff(t, m, (x, y) => x === y)) {
    if (step.type === "eq") {
      rows.push({ base: baseOf.get(theirIndex) ?? "", theirs: step.a as string, mine: step.b as string, differs: false });
      theirIndex++;
    } else if (step.type === "del") {
      const last = rows[rows.length - 1];
      if (last && last.differs && !last.theirs) { last.theirs = step.a as string; last.base = baseOf.get(theirIndex) ?? last.base; }
      else rows.push({ base: baseOf.get(theirIndex) ?? "", theirs: step.a as string, mine: "", differs: true });
      theirIndex++;
    } else {
      const last = rows[rows.length - 1];
      if (last && last.differs && !last.mine) last.mine = step.b as string;
      else rows.push({ base: "", theirs: "", mine: step.b as string, differs: true });
    }
  }
  return rows;
}

/** 按每行选择拼出合并结果（表格按行换行连接，正文直接相连）。 */
export function mergeRows(rows: CompareRow[], picks: Array<"theirs" | "mine">, table: boolean, byBlock = false): string {
  const chosen = rows.map((row, index) => row.differs ? row[picks[index] ?? "theirs"] : row.theirs).filter(Boolean);
  return chosen.join(byBlock ? "\n\n" : table ? "\n" : "");
}

/** 按空行分块（Markdown 顶层块）。 */
export function blocksOf(text: string): string[] {
  return text.replace(/\r\n/g, "\n").split(/\n{2,}/).map(block => block.trim()).filter(Boolean);
}

/** 两张表格不同的单元格数（界面复验 N7：表格冲突卡片写“表格 · N 处单元格不同”）。 */
export function tableCellDiffs(a: string, b: string): number {
  const x = tableCells(a), y = tableCells(b);
  let count = 0;
  for (let r = 0; r < Math.max(x.length, y.length); r++) {
    for (let c = 0; c < Math.max(x[r]?.length ?? 0, y[r]?.length ?? 0); c++) if ((x[r]?.[c] ?? "") !== (y[r]?.[c] ?? "")) count++;
  }
  return count;
}

export interface BlockDiffRow { type: "eq" | "ins" | "del" | "mod"; text: string; parts?: DiffPart[] }
/** 界面验收 S12：两个版本之间按块比较；相邻的删+增配成“修改”，块内再逐词标出增删。 */
export function blockDiff(before: string, after: string): BlockDiffRow[] {
  const rows: BlockDiffRow[] = [];
  const steps = lcsDiff(blocksOf(before), blocksOf(after), (x, y) => x === y);
  let dels: string[] = [], inss: string[] = [];
  const flush = () => {
    const paired = Math.min(dels.length, inss.length);
    for (let k = 0; k < paired; k++) rows.push({ type: "mod", text: inss[k], parts: diffWords(dels[k], inss[k]) });
    for (const text of dels.slice(paired)) rows.push({ type: "del", text });
    for (const text of inss.slice(paired)) rows.push({ type: "ins", text });
    dels = []; inss = [];
  };
  for (const step of steps) {
    if (step.type === "eq") { flush(); rows.push({ type: "eq", text: step.a as string }); }
    else if (step.type === "del") dels.push(step.a as string);
    else inss.push(step.b as string);
  }
  flush();
  return rows;
}

export function tableCells(text: string): string[][] {
  return text.trim().split("\n").filter(line => !/^\s*\|?\s*:?-{3,}/.test(line))
    .map(line => line.trim().replace(/^\||\|$/g, "").split(/(?<!\\)\|/).map(cell => cell.trim()));
}
