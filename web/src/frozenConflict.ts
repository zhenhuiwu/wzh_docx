/**
 * 方案 C（14-最终界面验收 §三）：冲突段“冻结”。冲突返回时编辑区不重绘这一段（冻结区），续写留在“我的版本”里，
 * 以只追加快照存到冲突副本；提交正文时冻结区一律用服务器上这一段的版本代替。这里是不依赖 DOM 的纯函数部分。
 */
import { diffWords } from "./textDiff";

/** 冻结区在转 Markdown 时的占位块（纯字母数字，turndown 不会转义）。 */
export function frozenMark(n: number): string {
  return `ZQFROZENMARK${n}QZ`;
}

/** 把占位块换成冻结区对应的正文版本；正文版本为空（对方删了这一段）时连同前后的空行一起去掉。 */
export function substituteFrozen(markdown: string, marks: Array<{ mark: string; theirs: string }>): string {
  if (!marks.length) return markdown;
  const table = new Map(marks.map(entry => [entry.mark, entry.theirs]));
  const out: string[] = [];
  for (const block of markdown.split("\n\n")) {
    const key = block.trim();
    if (table.has(key)) {
      const theirs = table.get(key)!;
      if (theirs) out.push(theirs);
      continue;
    }
    out.push(block);
  }
  return out.join("\n\n");
}

/** “含冲突后续写的 N 字”：最新的我的版本相对最初的我的版本新增的字数（不计空白）。 */
export function continuedChars(original: string, latest: string): number {
  if (original === latest) return 0;
  return diffWords(original, latest).filter(part => part.type === "ins").reduce((sum, part) => sum + part.text.replace(/\s/g, "").length, 0);
}

/** 测试工程师 T-S4：失焦后敲下的键全是 ASCII 字母、又出现过输入法（Process 键或组字）时，多半是拼音，不引用原文。 */
export function looksLikePinyin(typed: string, sawIme: boolean): boolean {
  return sawIme && /^[A-Za-z]+$/.test(typed);
}

/** noteAction 优先级（T-M2）：光标/失焦类 > 普通 > “他人更新”类；低优先级的提示不能覆盖正在显示的高优先级提示。 */
export type NoteKind = "caret" | "" | "others";
const RANK: Record<NoteKind, number> = { caret: 2, "": 1, others: 0 };
export function noteYields(current: NoteKind, showing: boolean, next: NoteKind): boolean {
  return showing && RANK[current] > RANK[next];
}

/** 方案 C：编辑区冻结区里我的最新版本（冲突面板用，键为 conflictId:itemIndex）。 */
export interface LiveMine { markdown: string; saved: boolean; savedAt: string | null; failing?: boolean; original: string; otherTab?: boolean }
