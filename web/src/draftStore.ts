/**
 * 本地草稿 v2（01 §6.5、02 §3.5）。
 * - 主日志放 localStorage，随输入同步写；键按账号、文档、标签页分开，另有索引键。
 * - 不存 innerHTML 与图片数据；整篇模式存全文 Markdown（图片只是 docx-image: 引用）。
 * - 只有服务器确认后才删除；写入失败（QuotaExceededError）由调用方转为 blocked。
 * storage 以参数注入，便于在 vitest 中用内存实现测试。
 */

export interface DraftBlock {
  objectId: string | null;
  tempId?: string;
  afterObjectId?: string | null;
  beforeObjectId?: string | null;
  baseRevision?: number;
  md: string;
  headingPath: string[];
  op: "update" | "insert" | "delete";
}

export interface ConflictEdit { conflictId: string; itemIndex: number; markdown: string }

export interface DraftV2 {
  v: 2;
  docId: string;
  tabId: string;
  docRevision: number;
  mergeBase: number;
  savedAt: number;
  wholeDocument: boolean;
  markdown?: string;          // 整篇模式
  blocks: DraftBlock[];       // 块模式；整篇模式下记录变化块的位置用于提示
  changed: number;            // “处”
  headingPaths: string[][];   // 涉及章节（去重，最多 5 个）
  conflictsEdits: ConflictEdit[];
  pendingSave: { clientSaveKey: string; sentAt: number } | null;
}

export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export const draftKey = (account: string, docId: string, tabId: string) => `doc-workbench.draft.v2:${account}:${docId}:${tabId}`;
export const indexKey = (account: string, docId: string) => `doc-workbench.draft.v2.index:${account}:${docId}`;
export const legacyKey = (account: string, docId: string) => `doc-workbench.draft:${account}:${docId}`;

interface IndexEntry { tabId: string; savedAt: number }

function readIndex(storage: StorageLike, account: string, docId: string): IndexEntry[] {
  try {
    const value = JSON.parse(storage.getItem(indexKey(account, docId)) || "[]");
    return Array.isArray(value) ? value.filter(item => item && typeof item.tabId === "string") : [];
  } catch {
    return [];
  }
}

export type WriteResult = "ok" | "quota";

/** 写入并更新索引。任何写入异常（配额、隐私模式）都返回 "quota"。 */
export function writeDraft(storage: StorageLike, account: string, draft: DraftV2): WriteResult {
  try {
    storage.setItem(draftKey(account, draft.docId, draft.tabId), JSON.stringify(draft));
    const index = readIndex(storage, account, draft.docId).filter(item => item.tabId !== draft.tabId);
    index.push({ tabId: draft.tabId, savedAt: draft.savedAt });
    storage.setItem(indexKey(account, draft.docId), JSON.stringify(index));
    return "ok";
  } catch {
    return "quota";
  }
}

export function readDraft(storage: StorageLike, account: string, docId: string, tabId: string): DraftV2 | null {
  try {
    const value = JSON.parse(storage.getItem(draftKey(account, docId, tabId)) || "null");
    return value && value.v === 2 && value.docId === docId ? value as DraftV2 : null;
  } catch {
    return null;
  }
}

/** 读回校验：读出的内容与内存一致（01 §6.3 第3条）。 */
export function verifyDraft(storage: StorageLike, account: string, draft: DraftV2): boolean {
  const stored = readDraft(storage, account, draft.docId, draft.tabId);
  if (!stored) return false;
  return stored.markdown === draft.markdown && JSON.stringify(stored.blocks) === JSON.stringify(draft.blocks)
    && JSON.stringify(stored.conflictsEdits) === JSON.stringify(draft.conflictsEdits);
}

export function removeDraft(storage: StorageLike, account: string, docId: string, tabId: string): void {
  try {
    storage.removeItem(draftKey(account, docId, tabId));
    const index = readIndex(storage, account, docId).filter(item => item.tabId !== tabId);
    if (index.length) storage.setItem(indexKey(account, docId), JSON.stringify(index));
    else storage.removeItem(indexKey(account, docId));
  } catch { /* 删除失败只会多出一条恢复提示，不会丢稿。 */ }
}

/** 列出本文档的所有本机草稿（含旧 v1 转换），按时间新到旧。 */
export function listDrafts(storage: StorageLike, account: string, docId: string): DraftV2[] {
  const drafts = readIndex(storage, account, docId)
    .map(item => readDraft(storage, account, docId, item.tabId))
    .filter((item): item is DraftV2 => Boolean(item));
  const legacy = convertLegacy(storage, account, docId);
  if (legacy) drafts.push(legacy);
  return drafts.sort((a, b) => b.savedAt - a.savedAt);
}

/** v1 键 {revision, content, html} → v2（整篇模式，mergeBase = v1 的 revision）。转换成功写入 v2 后删除 v1。 */
export function convertLegacy(storage: StorageLike, account: string, docId: string): DraftV2 | null {
  let raw: string | null;
  try { raw = storage.getItem(legacyKey(account, docId)); } catch { return null; }
  if (!raw) return null;
  try {
    const value = JSON.parse(raw);
    if (!value || typeof value.content !== "string" || !Number.isInteger(value.revision)) return null;
    const draft: DraftV2 = {
      v: 2, docId, tabId: "legacy", docRevision: value.revision, mergeBase: value.revision, savedAt: Number(value.savedAt) || 0,
      wholeDocument: true, markdown: value.content, blocks: [], changed: 0, headingPaths: [], conflictsEdits: [], pendingSave: null,
    };
    if (writeDraft(storage, account, draft) === "ok" && verifyDraft(storage, account, draft)) storage.removeItem(legacyKey(account, docId));
    return draft;
  } catch {
    return null;
  }
}

/** 恢复条文案：“发现 10 月 10 日 14:32 在本机未保存的修改（3 处，3.2 软件能力需求等）。” */
export function recoverySummary(draft: DraftV2): string {
  const at = new Date(draft.savedAt);
  const pad = (n: number) => String(n).padStart(2, "0");
  const time = draft.savedAt ? `${at.getMonth() + 1} 月 ${at.getDate()} 日 ${pad(at.getHours())}:${pad(at.getMinutes())} ` : "";
  const heading = draft.headingPaths.find(path => path.length)?.at(-1);
  const parts = [`${Math.max(draft.changed, 1)} 处`, ...(heading ? [`${heading}${draft.headingPaths.length > 1 ? "等" : ""}`] : [])];
  return `发现 ${time}在本机未保存的修改（${parts.join("，")}）。`;
}

/** 每个浏览器标签页一个稳定标识（sessionStorage）。 */
export function tabId(session: StorageLike, make: () => string): string {
  try {
    const existing = session.getItem("doc-workbench.tab");
    if (existing) return existing;
    const created = make();
    session.setItem("doc-workbench.tab", created);
    return created;
  } catch {
    return make();
  }
}
