/** QA 复验 16 P1-1：恢复草稿时留下的“他人负责部分”的修改（本机，按用户 + 文档），只追加，不随恢复清掉。 */
export interface UnsavedPart { text: string; savedAt: number }

/** 放在 A07 暂存的键下（同属本机保留的不归我负责的内容）。 */
export function unsavedKey(account: string, documentId: string): string {
  return `docwb.a07.stash.${account}.${documentId}.unsaved`;
}

export function parseUnsaved(raw: string | null): UnsavedPart[] {
  if (!raw) return [];
  try {
    const value = JSON.parse(raw);
    return Array.isArray(value) ? value.filter(item => item && typeof item.text === "string").map(item => ({ text: item.text, savedAt: Number(item.savedAt) || 0 })) : [];
  } catch { return []; }
}

export function appendUnsaved(parts: UnsavedPart[], texts: string[], now: number): UnsavedPart[] {
  const known = new Set(parts.map(item => item.text));
  return [...parts, ...texts.filter(text => text.trim() && !known.has(text)).map(text => ({ text, savedAt: now }))];
}
