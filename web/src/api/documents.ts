import { apiRequest, ApiError } from "./client";
import { rememberCrumb } from "../crumbs";

export interface DocumentRecord {
  id: string;
  projectId: string;
  name: string;
  sourceType: "template" | "upload";
  templateDocumentId?: string;
  templateDocumentName?: string;
  templateName?: string | null;
  /** A06：本文档使用的模板版本；legacySnapshot=true 表示创建于模板版本管理之前（不显示第 N 版）。 */
  template?: { name: string | null; documentName: string | null; versionId: string | null; seq: number | null; publishedAt: string | null; latestSeq: number | null; legacySnapshot: boolean } | null;
  originalDocxFileId?: string;
  formatTemplate: Record<string, any>;
  markdownContent: string;
  outline: any[];
  status: "draft" | "in_progress" | "completed";
  ownerId: string;
  ownerName: string;
  revision: number;
  updatedAt: string;
  projectName?: string;
  permissions?: DocumentPermissions;
  mineOpenConflicts?: number;
  memberOpenConflicts?: number | null;
}

/** 01 §4.7：仅用于展示，写接口仍以服务端 403 为准。 */
export interface DocumentPermissions {
  projectRole: "owner" | "editor" | "reviewer" | "viewer" | "auditor" | null;
  canEdit: boolean;
  canExport: boolean;
  canResolveConflicts: boolean;
  canViewMemberConflicts: boolean;
  readonlyReason: "role" | "archived" | "assignment" | null;
  /** A07：分工模式、分工修订号、能否分配。 */
  assignmentMode?: "open" | "enforced";
  assignmentsRevision?: number;
  canAssign?: boolean;
  projectOwners?: Array<{ id: string; name: string }>;
}

export interface Capability { available: boolean; reason: string | null }
export interface Capabilities {
  ai: { writing: Capability; diagram: Capability; testExtraction: Capability; adminHint?: string };
  wordPreview: Capability;
  visio: Capability;
}

let capabilitiesCache: Promise<Capabilities> | null = null;
/** GET /capabilities，按页面会话缓存；失败时下次重新请求。 */
export function getCapabilities(): Promise<Capabilities> {
  capabilitiesCache ??= apiRequest<{ data: Capabilities }>("/capabilities").then(body => {
    if (!body?.data) throw new Error("能力信息不完整。");
    return body.data;
  }).catch(error => { capabilitiesCache = null; throw error; });
  return capabilitiesCache;
}

export function documentHeadings(markdown: string): { level: number; text: string }[] {
  return [...markdown.matchAll(/^(#{1,6})[ \t]+([^\n]+)$/gm)].map(match => ({
    level: match[1]!.length, text: match[2]!.replace(/\\([\\`*_{}\[\]<>|#])/g, "$1").trim(),
  }));
}

export const documentStatusLabel: Record<string, string> = {
  draft: "草稿",
  in_progress: "编辑中",
  completed: "已完成",
};

export async function listDocuments(
  projectId: string,
  page: number = 1,
  name: string = "",
  pageSize: number = 20
): Promise<{ items: DocumentRecord[]; page: number; pageSize: number; total: number; permissions?: DocumentPermissions }> {
  const params = new URLSearchParams();
  params.set("page", page.toString());
  params.set("pageSize", pageSize.toString());
  if (name) params.set("name", name);

  const body = await apiRequest<{ data: { items: DocumentRecord[]; page: number; pageSize: number; total: number; permissions?: DocumentPermissions } }>(
    `/projects/${projectId}/documents?${params}`
  );
  return body?.data ?? { items: [], page: 1, pageSize, total: 0 };
}

export async function createDocument(
  projectId: string,
  input: {
    name: string;
    sourceType: "template" | "upload";
    templateDocumentId?: string;
    wordFileId?: string;
    upstreamDocumentIds?: string[];
  }
): Promise<DocumentRecord> {
  const body = await apiRequest<{ data: DocumentRecord }>(`/projects/${projectId}/documents`, {
    method: "POST",
    json: input,
  });
  if (!body?.data) throw new Error("文档响应不完整。");
  return body.data;
}

export async function getDocument(id: string): Promise<DocumentRecord> {
  const body = await apiRequest<{ data: DocumentRecord }>(`/documents/${id}`);
  if (!body?.data) throw new Error("文档响应不完整。");
  // 顺带登记面包屑的文档名：工作台自己读取文档时即可显示，顶栏不再重复请求（09 复审 R6）。
  rememberCrumb(`document:${body.data.id}`, body.data.name);
  return body.data;
}

export async function updateDocumentContent(
  id: string,
  expectedRevision: number,
  markdownContent: string,
  memory?: WritingMemory,
  retractMemoryRevision?: number,
  options: { clientSaveKey?: string; allowMerge?: boolean; keepalive?: boolean; baseMarkdown?: string; blockIds?: Array<string | null> } = {}
): Promise<ContentSaveResult> {
  const { keepalive, ...extra } = options;
  const body = await apiRequest<{ data: any }>(`/documents/${id}/content`, {
    method: "PATCH",
    json: { expectedRevision, markdownContent, memory, retractMemoryRevision, ...extra },
    keepalive,
  });
  if (!body?.data) throw new Error("更新响应不完整。");
  return body.data;
}

export async function exportDocument(id: string, expectedRevision: number): Promise<{ fileId: string }> {
  const body = await apiRequest<{ data: { fileId: string } }>(`/documents/${id}/export`, {
    method: "POST",
    json: { expectedRevision },
  });
  if (!body?.data) throw new Error("导出响应不完整。");
  return body.data;
}

export async function previewDocument(id: string): Promise<{ html: string; objectModel?: boolean; revision?: number }> {
  const body = await apiRequest<{ data: { html: string; objectModel?: boolean; revision?: number } }>(`/documents/${id}/preview`, { timeoutMs: 120000 });
  if (!body?.data) throw new Error("预览响应不完整。");
  return body.data;
}

/** 他人更新（02 §3.3，界面验收 M6）：sinceRevision 之后变化的对象与修改人汇总。 */
export interface ObjectsSince {
  documentRevision: number; objectModel: boolean; changedBy: Array<Person & { objects: number }>;
  objects: Array<{ id: string; deleted: boolean; markdown?: string; updatedBy?: Person | null; deletedBy?: Person | null }>;
  /** A05（17 B-8）：轮询带当前权限，权限调整/归档在 45 秒内生效。 */
  permissions?: DocumentPermissions;
}
export async function getObjectsSince(id: string, sinceRevision: number): Promise<ObjectsSince> {
  const body = await apiRequest<{ data: ObjectsSince }>(`/documents/${id}/objects?sinceRevision=${sinceRevision}`);
  if (!body?.data) throw new Error("文档更新响应不完整。");
  return body.data;
}

/** S6：派生写入接口的 opt-in 三方合并（01 §4.3）。 */
export interface MergeRequest { allowMerge?: boolean; clientSaveKey?: string }

export interface DocumentContentResult {
  merged?: boolean;
  changedBy?: Array<{ id: string; name: string; objects: number }>;
  markdownContent: string;
  revision: number;
  status: DocumentRecord["status"];
  headingIndex?: number;
  formatTemplate?: Record<string, any>;
}

export interface DocumentSelection { blockId: string; endBlockId?: string; start: number; end: number; text: string }
export interface WritingGuidanceItem { id: string; heading: string; kind: string; sourceText: string; text: string; overridden: boolean; targetObjectIds?: string[]; targetText?: string }
export interface WritingGuidanceGroup { headingIndex: number | null; heading: string; headingPath?: string; items: WritingGuidanceItem[] }
export interface WritingGuidance { revision: number; groups: WritingGuidanceGroup[]; notices?: string[] }

export async function queryWritingGuidance(id: string, expectedRevision: number, scope: WritingMemory["scope"] | "objects", headingIndex: number | null, selection?: DocumentSelection, signal?: AbortSignal, objectIds?: string[]): Promise<WritingGuidance> {
  const body = await apiRequest<{ data: WritingGuidance }>(`/documents/${id}/writing-guidance/query`, {
    method: "POST", json: { expectedRevision, scope, headingIndex, selection, ...(objectIds ? { objectIds } : {}) }, signal,
  });
  if (!body?.data) throw new Error("编写建议响应不完整。");
  return body.data;
}
export async function updateWritingGuidance(id: string, expectedRevision: number, items: { id: string; text: string | null }[]): Promise<DocumentContentResult> {
  const body = await apiRequest<{ data: DocumentContentResult }>(`/documents/${id}/writing-guidance`, {
    method: "PATCH", json: { expectedRevision, items },
  });
  if (!body?.data) throw new Error("保存编写建议响应不完整。");
  return body.data;
}
export interface DocumentVersion { revision: number; createdAt: string; kind: string; restoredFromRevision?: number | null; createdBy?: { id: string; name: string } | null; changedObjects?: number | null }
export interface DocumentVersionPage { items: DocumentVersion[]; hasMore: boolean; currentRevision: number }
export interface DocumentVersionDetail extends DocumentVersion { html: string; markdownContent: string; formatTemplate: Record<string, any> }

export async function listDocumentVersions(id: string, offset = 0): Promise<DocumentVersionPage> {
  const body = await apiRequest<{ data: DocumentVersionPage }>(`/documents/${id}/versions?offset=${offset}&limit=5`);
  if (!body?.data) throw new Error("版本记录响应不完整。");
  return body.data;
}
export async function getDocumentVersion(id: string, revision: number, withHtml = true): Promise<DocumentVersionDetail> {
  const body = await apiRequest<{ data: DocumentVersionDetail }>(`/documents/${id}/versions/${revision}${withHtml ? "" : "?html=false"}`);
  if (!body?.data) throw new Error("版本内容响应不完整。");
  return body.data;
}
export async function restoreDocumentVersion(id: string, revision: number, expectedRevision: number): Promise<DocumentContentResult> {
  const body = await apiRequest<{ data: DocumentContentResult }>(`/documents/${id}/versions/${revision}/restore`, { method: "POST", json: { expectedRevision } });
  if (!body?.data) throw new Error("恢复版本响应不完整。");
  return body.data;
}
export async function replaceDocumentSelection(id: string, expectedRevision: number, selection: DocumentSelection, content: string, instruction: string, merge: MergeRequest = {}): Promise<DocumentContentResult> {
  const body = await apiRequest<{ data: DocumentContentResult }>(`/documents/${id}/selection`, { method: "POST", json: { expectedRevision, selection, content, instruction, ...merge } });
  if (!body?.data) throw new Error("替换选区响应不完整。");
  return body.data;
}

export async function addDocumentHeading(id: string, expectedRevision: number, afterIndex: number | null, placement: "child" | "sibling", title: string, merge: MergeRequest = {}): Promise<DocumentContentResult> {
  const body = await apiRequest<{ data: DocumentContentResult }>(`/documents/${id}/headings`, {
    method: "POST", json: { expectedRevision, afterIndex, placement, title, ...merge },
  });
  if (!body?.data) throw new Error("新增标题响应不完整。");
  return body.data;
}

export interface DraftEvent {
  type: "start" | "progress" | "draft" | "done" | "error";
  content: string;
  html: string;
  revision: number;
  message: string;
  stage?: string;
  edits?: DocumentEdit[];
  allowedModes?: ("append" | "replace")[];
  /** Spec 30 §4: block-ID operations for in-place section editing. */
  operations?: BlockOperation[];
}

export interface BlockOperation { op: "replace" | "insertAfter" | "delete"; blockId: string; content: string; html: string }

export interface DocumentEdit { headingIndex: number | null; content: string }
export interface WritingMemory { instruction: string; scope: "section" | "selection" | "document"; target: string }

export interface WritingMemoryPage { items: { target: string; instruction: string }[]; hasMore: boolean; offset: number }

export async function getWritingMemory(id: string, offset = 0): Promise<WritingMemoryPage> {
  const body = await apiRequest<{ data: WritingMemoryPage }>(`/documents/${id}/memory?offset=${offset}&limit=10`);
  if (!body?.data) throw new Error("编写记忆响应不完整。");
  return body.data;
}

export async function clearWritingMemory(id: string): Promise<void> {
  await apiRequest(`/documents/${id}/memory`, { method: "DELETE" });
}

export async function applyDocumentEdits(id: string, expectedRevision: number, edits: DocumentEdit[], instruction: string, merge: MergeRequest = {}): Promise<DocumentContentResult> {
  const body = await apiRequest<{ data: DocumentContentResult }>(`/documents/${id}/edits`, {
    method: "POST", json: { expectedRevision, edits, instruction, ...merge },
  });
  if (!body?.data) throw new Error("全文修改响应不完整。");
  return body.data;
}

export async function streamDocumentSection(id: string, expectedRevision: number, headingIndex: number | null, instruction: string, selectedText: string | undefined, onEvent: (event: DraftEvent) => void, signal: AbortSignal, scope: WritingMemory["scope"] = "section", selection?: DocumentSelection, selectionBlockIds?: string[], blockIds?: string[]): Promise<void> {
  let completed = false;
  await apiRequest<DraftEvent>(`/documents/${id}/sections/draft?stream=true`, {
    method: "POST", json: { expectedRevision, headingIndex, instruction, selectedText, scope, selection, ...(selectionBlockIds?.length ? { selectionBlockIds } : {}), ...(blockIds?.length ? { blockIds } : {}) }, timeoutMs: scope === "document" ? 600000 : 120000, signal,
    onEvent: (event) => {
      if (event.type === "error") throw new ApiError(502, "AI_FAILED", event.message || "AI 编写中断，请重试。");
      if (event.type === "done") completed = true;
      onEvent(event);
    },
  });
  if (!completed) throw new Error("AI 编写中断，请重试。");
}

export async function insertDocumentSection(id: string, expectedRevision: number, headingIndex: number, content: string, mode: "append" | "replace" = "append", instruction = "", merge: MergeRequest = {}): Promise<DocumentContentResult> {
  const body = await apiRequest<{ data: DocumentContentResult }>(`/documents/${id}/sections/insert`, {
    method: "POST", json: { expectedRevision, headingIndex, content, mode, instruction, ...merge },
  });
  if (!body?.data) throw new Error("插入正文响应不完整。");
  return body.data;
}

// ---- 阶段2 S5：合并保存与冲突（01 §4.3、§4.4） ----
export interface Person { id: string; name: string }
export interface ContentSaveResult { id: string; markdownContent: string; status: DocumentRecord["status"]; revision: number; updatedAt: string; merged?: boolean; changeId?: string | null; changedBy?: Array<Person & { objects: number }>; replayed?: boolean; snapshotLimited?: { conflictId: string; itemIndex: number; markdown?: string }[]; frozenByConflict?: string[]; whitespaceDropped?: number; whitespaceDeleted?: number; theirsWhitespaceOverridden?: number }
export interface ConflictLocation { headingPath?: string[]; ordinal?: number | null; kind?: string; label?: string | null }
export interface ConflictSide { markdown?: string | null; revision?: number | null; deleted?: boolean; updatedBy?: Person | null; updatedAt?: string | null; deletedBy?: Person | null; deletedAt?: string | null; /** K1-M1：这一段最近一次是冲突作者本人改的（如重新打开后在正文里改了） */ latestBySelf?: boolean }
export interface ConflictItem {
  conflictId: string; itemIndex: number; reason: "MODIFIED" | "DELETED" | "ANCHOR_LOST" | "STRUCTURE" | "FORMAT"; attempt: number;
  location: ConflictLocation; base: ConflictSide; theirs: ConflictSide; mine: ConflictSide; status?: "open" | "resolved" | "discarded";
  action?: string | null; actor?: Person | null; createdAt?: string; resolvedAt?: string | null; objectIds?: string[];
  /** 方案 C：最新一份“我的版本”快照（含冲突后续写）；没有快照时等于原始 mine，savedAt 为 null。 */
  mineLatest?: MineLatest;
  /** A3c：其他标签页各自最后一份快照（与最新一份不同的） */
  mineOthers?: Array<{ tabId: string; markdown: string; savedAt?: string | null }>;
}
export interface MineLatest { markdown: string; savedAt: string | null; tabId: string | null; snapshotId?: string | null }
export interface ConflictSummary { mineOpen: number; mineHandled?: number; allOpen: number | null }
export type ConflictAction = "keep_mine" | "use_theirs" | "manual" | "keep_both" | "restore_as_new" | "discard";
export interface ResolveRequestItem { itemIndex: number; action: ConflictAction; theirsRevision?: number | null; markdown?: string; placement?: { mode: "after_nearest_live" | "section_end" | "after"; afterObjectId?: string }; mineSnapshotId?: string; mineMarkdown?: string }
export interface ResolveResult { documentRevision: number; changeId: string | null; items: Array<{ itemIndex: number; status: string; placementFallback?: boolean; mineSnapshotId?: string }>; conflictStatus: string; mineOpen: number; markdownContent: string; conflictId?: string; action?: ConflictAction; theirsName?: string }

export async function getConflictSummary(id: string): Promise<ConflictSummary> {
  const body = await apiRequest<{ data: ConflictSummary }>(`/documents/${id}/conflicts/summary`);
  if (!body?.data) throw new Error("冲突数量响应不完整。");
  return body.data;
}
export async function listConflicts(id: string, status: "open" | "resolved" | "all" = "open", actor: "me" | "all" = "me"): Promise<ConflictItem[]> {
  const body = await apiRequest<{ data: { items: ConflictItem[] } }>(`/documents/${id}/conflicts?status=${status}&actor=${actor}`);
  if (!body?.data) throw new Error("冲突列表响应不完整。");
  return body.data.items;
}
export async function getConflictCopy(id: string, conflictId: string): Promise<{ mineMarkdown: string; createdAt: string }> {
  const body = await apiRequest<{ data: { mineMarkdown: string; createdAt: string } }>(`/documents/${id}/conflicts/${conflictId}`);
  if (!body?.data) throw new Error("冲突副本响应不完整。");
  return body.data;
}
/** 方案 C：冲突后在冻结段里续写的“我的版本”，只追加快照（同键幂等）。 */
export async function saveConflictMine(id: string, conflictId: string, itemIndex: number, markdown: string, clientSaveKey: string, tabId?: string, seq?: number): Promise<{ mineLatest: MineLatest; stale?: boolean }> {
  const body = await apiRequest<{ data: { mineLatest: MineLatest; stale?: boolean } }>(`/documents/${id}/conflicts/${conflictId}/mine`, {
    method: "POST", json: { itemIndex, markdown, clientSaveKey, ...(tabId ? { tabId } : {}), ...(seq ? { seq } : {}) }, timeoutMs: 15000,
  });
  if (!body?.data) throw new Error("冲突副本保存响应不完整。");
  return body.data;
}
export async function resolveConflict(id: string, conflictId: string, clientSaveKey: string, baseDocumentRevision: number, items: ResolveRequestItem[]): Promise<ResolveResult> {
  const body = await apiRequest<{ data: ResolveResult }>(`/documents/${id}/conflicts/${conflictId}/resolve`, {
    method: "POST", json: { clientSaveKey, baseDocumentRevision, items }, timeoutMs: 30000,
  });
  if (!body?.data) throw new Error("处理冲突响应不完整。");
  return body.data;
}
export async function findChangeBySaveKey(id: string, clientSaveKey: string): Promise<boolean> {
  const body = await apiRequest<{ data: unknown[] }>(`/documents/${id}/changes?clientSaveKey=${encodeURIComponent(clientSaveKey)}`);
  return Boolean(body?.data?.length);
}
