/** 基线对比摘要（41 复验）：只把“两边都有、版本不同”的文档算作有变化；只在一边的文档单独计数，不算变化。 */
export type CrossState = "changed" | "same" | "added" | "removed";

export function crossSummary(states: CrossState[], fromLabel: string, toLabel: string): string {
  const count = (state: CrossState) => states.filter((value) => value === state).length;
  const changed = count("changed"), added = count("added"), removed = count("removed");
  const parts = [changed ? `${changed} 份文档有变化` : "没有文档变化"];
  if (added) parts.push(`${added} 份为 ${fromLabel} 之后加入`);
  if (removed) parts.push(`${removed} 份未纳入 ${toLabel}`);
  return parts.join("，");
}

/** 基线对比右侧标题（41 复验 f31c8eb）：文档版本用 v{n}，基线放括号里，与依赖链「v1 → v2（B2）」同一写法。 */
export function crossTitle(base: number | null, compare: number | "current" | null, baselines: string): string {
  const to = compare === "current" ? "当前" : compare === null ? null : `v${compare}`;
  const versions = base !== null && to ? `v${base} → ${to}` : base !== null ? `v${base}` : to ?? "";
  return `${versions}（${baselines}）`;
}
