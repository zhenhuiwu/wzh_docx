import { apiRequest, ApiError } from "./client";

export interface DocumentRecord {
  id: string;
  projectId: string;
  name: string;
  sourceType: "template" | "upload";
  templateDocumentId?: string;
  templateDocumentName?: string;
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
}

/** 01 §4.7：仅用于展示，写接口仍以服务端 403 为准。 */
export interface DocumentPermissions {
  projectRole: "owner" | "editor" | "reviewer" | "viewer" | null;
  canEdit: boolean;
  canExport: boolean;
  canResolveConflicts: boolean;
  canViewMemberConflicts: boolean;
  readonlyReason: "role" | null;
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
  return body.data;
}

export async function updateDocumentContent(
  id: string,
  expectedRevision: number,
  markdownContent: string,
  memory?: WritingMemory,
  retractMemoryRevision?: number,
  options: { clientSaveKey?: string; allowMerge?: boolean } = {}
): Promise<{ id: string; markdownContent: string; status: DocumentRecord["status"]; revision: number; updatedAt: string }> {
  const body = await apiRequest<{ data: any }>(`/documents/${id}/content`, {
    method: "PATCH",
    json: { expectedRevision, markdownContent, memory, retractMemoryRevision, ...options },
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

export async function previewDocument(id: string): Promise<{ html: string }> {
  const body = await apiRequest<{ data: { html: string } }>(`/documents/${id}/preview`);
  if (!body?.data) throw new Error("预览响应不完整。");
  return body.data;
}

export interface DocumentContentResult {
  markdownContent: string;
  revision: number;
  status: DocumentRecord["status"];
  headingIndex?: number;
  formatTemplate?: Record<string, any>;
}

export interface DocumentSelection { blockId: string; endBlockId?: string; start: number; end: number; text: string }
export interface DocumentVersion { revision: number; createdAt: string; kind: string; restoredFromRevision?: number | null; createdBy?: { id: string; name: string } | null; changedObjects?: number | null }
export interface DocumentVersionPage { items: DocumentVersion[]; hasMore: boolean; currentRevision: number }
export interface DocumentVersionDetail extends DocumentVersion { html: string; markdownContent: string; formatTemplate: Record<string, any> }

export async function listDocumentVersions(id: string, offset = 0): Promise<DocumentVersionPage> {
  const body = await apiRequest<{ data: DocumentVersionPage }>(`/documents/${id}/versions?offset=${offset}&limit=5`);
  if (!body?.data) throw new Error("版本记录响应不完整。");
  return body.data;
}
export async function getDocumentVersion(id: string, revision: number): Promise<DocumentVersionDetail> {
  const body = await apiRequest<{ data: DocumentVersionDetail }>(`/documents/${id}/versions/${revision}`);
  if (!body?.data) throw new Error("版本内容响应不完整。");
  return body.data;
}
export async function restoreDocumentVersion(id: string, revision: number, expectedRevision: number): Promise<DocumentContentResult> {
  const body = await apiRequest<{ data: DocumentContentResult }>(`/documents/${id}/versions/${revision}/restore`, { method: "POST", json: { expectedRevision } });
  if (!body?.data) throw new Error("恢复版本响应不完整。");
  return body.data;
}
export async function replaceDocumentSelection(id: string, expectedRevision: number, selection: DocumentSelection, content: string, instruction: string): Promise<DocumentContentResult> {
  const body = await apiRequest<{ data: DocumentContentResult }>(`/documents/${id}/selection`, { method: "POST", json: { expectedRevision, selection, content, instruction } });
  if (!body?.data) throw new Error("替换选区响应不完整。");
  return body.data;
}

export async function addDocumentHeading(id: string, expectedRevision: number, afterIndex: number | null, placement: "child" | "sibling", title: string): Promise<DocumentContentResult> {
  const body = await apiRequest<{ data: DocumentContentResult }>(`/documents/${id}/headings`, {
    method: "POST", json: { expectedRevision, afterIndex, placement, title },
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
}

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

export async function applyDocumentEdits(id: string, expectedRevision: number, edits: DocumentEdit[], instruction: string): Promise<DocumentContentResult> {
  const body = await apiRequest<{ data: DocumentContentResult }>(`/documents/${id}/edits`, {
    method: "POST", json: { expectedRevision, edits, instruction },
  });
  if (!body?.data) throw new Error("全文修改响应不完整。");
  return body.data;
}

export async function streamDocumentSection(id: string, expectedRevision: number, headingIndex: number | null, instruction: string, selectedText: string | undefined, onEvent: (event: DraftEvent) => void, signal: AbortSignal, scope: WritingMemory["scope"] = "section", selection?: DocumentSelection): Promise<void> {
  let completed = false;
  await apiRequest<DraftEvent>(`/documents/${id}/sections/draft?stream=true`, {
    method: "POST", json: { expectedRevision, headingIndex, instruction, selectedText, scope, selection }, timeoutMs: scope === "document" ? 600000 : 120000, signal,
    onEvent: (event) => {
      if (event.type === "error") throw new ApiError(502, "AI_FAILED", event.message || "AI 编写中断，请重试。");
      if (event.type === "done") completed = true;
      onEvent(event);
    },
  });
  if (!completed) throw new Error("AI 编写中断，请重试。");
}

export async function insertDocumentSection(id: string, expectedRevision: number, headingIndex: number, content: string, mode: "append" | "replace" = "append", instruction = ""): Promise<DocumentContentResult> {
  const body = await apiRequest<{ data: DocumentContentResult }>(`/documents/${id}/sections/insert`, {
    method: "POST", json: { expectedRevision, headingIndex, content, mode, instruction },
  });
  if (!body?.data) throw new Error("插入正文响应不完整。");
  return body.data;
}
