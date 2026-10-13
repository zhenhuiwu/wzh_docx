/**
 * 28 N-5：暂存是一个列表，只追加、不替换。每条带来源块与时间。
 * 同一块 20 秒内的连续输入并进最后一条（气泡只显示这“一次输入”）；fresh=true 一定新起一条。
 * 只有用户点“复制 / 放到我的下一处 / 清除”才会清掉。
 */
export interface StashEntry { text: string; source: string; at: number }

export function parseStash(raw: string | null): StashEntry[] {
  if (!raw) return [];
  try {
    const value = JSON.parse(raw) as unknown;
    const list = Array.isArray(value) ? value : value && typeof value === "object" && "entries" in value ? (value as { entries: unknown }).entries
      : value && typeof value === "object" ? [value] : [];   // 旧格式 {text, source, at}
    return (Array.isArray(list) ? list : []).filter((item): item is StashEntry => !!item && typeof (item as StashEntry).text === "string" && !!(item as StashEntry).text)
      .map(item => ({ text: item.text, source: typeof item.source === "string" ? item.source : "", at: typeof item.at === "number" ? item.at : 0 }));
  } catch { return []; }
}

export function serializeStash(entries: StashEntry[]): string {
  return JSON.stringify({ entries });
}

export function addStashEntry(entries: StashEntry[], text: string, source: string, now: number, fresh = false): StashEntry[] {
  if (!text) return entries;
  const last = entries[entries.length - 1];
  if (!fresh && last && last.source === source && now - last.at < 20000) return [...entries.slice(0, -1), { text: last.text + text, source, at: now }];
  return [...entries, { text, source, at: now }];
}

export function stashAll(entries: StashEntry[]): string {
  return entries.map(item => item.text).join("\n\n");
}
