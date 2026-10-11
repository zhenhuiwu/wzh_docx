import { apiRequest } from "./client";

export interface TemplateRecord {
  id: string;
  name: string;
  category: string;
  description: string;
  status: "draft" | "published" | "retired";
  revision: number;
  updatedAt: string;
  documentCount: number;
}

export interface TemplateDocument {
  id: string;
  name: string;
  description: string;
  displayOrder: number;
  wordFileId: string;
  formatTemplate: Record<string, any>;
  createdAt: string;
  updatedAt: string;
  importStructure?: TemplateImportStructure;
  currentVersion?: { versionId: string; seq: number; publishedAt: string | null; publishedBy: string | null } | null;
  hasDraft?: boolean;
  usageCount?: number;
}

export type TemplateBlockKind = "heading" | "guidance" | "body" | "placeholder" | "table" | "caption" | "toc" | "cover" | "example";
export interface TemplateImportBlock {
  id: string;
  path: string;
  kind: TemplateBlockKind;
  text: string;
  sourceText: string;
  headingLevel: number | null;
  headingId: string | null;
  tableId: string | null;
  protected: boolean;
  isTable: boolean;
  warning?: string;
}
export interface TemplateImportStructure {
  schemaVersion: 1;
  revision: number;
  reviewed: boolean;
  sourceHash: string;
  blocks: TemplateImportBlock[];
}
export const templateBlockLabels: Record<TemplateBlockKind, string> = {
  heading: "标题", guidance: "AI 提示词", body: "正文", placeholder: "填写位置",
  table: "表格", caption: "题注", toc: "目录", cover: "封面与更改记录", example: "示例",
};

export const templateStatusLabel: Record<string, string> = {
  draft: "草稿",
  published: "已发布",
  retired: "已停用",
};

export async function listTemplates(
  page: number = 1,
  name: string = ""
): Promise<{ items: TemplateRecord[]; page: number; pageSize: number; total: number }> {
  const params = new URLSearchParams();
  params.set("page", page.toString());
  params.set("pageSize", "20");
  if (name) params.set("name", name);

  const body = await apiRequest<{ data: { items: TemplateRecord[]; page: number; pageSize: number; total: number } }>(
    `/templates?${params}`
  );
  return body?.data ?? { items: [], page: 1, pageSize: 20, total: 0 };
}

export async function createTemplate(input: {
  name: string;
  category: string;
  description?: string;
}): Promise<TemplateRecord> {
  const body = await apiRequest<{ data: TemplateRecord }>("/templates", {
    method: "POST",
    json: input,
  });
  if (!body?.data) throw new Error("创建响应不完整。");
  return body.data;
}

export async function getTemplate(id: string): Promise<TemplateRecord> {
  const body = await apiRequest<{ data: TemplateRecord }>(`/templates/${id}`);
  if (!body?.data) throw new Error("模板响应不完整。");
  return body.data;
}

export async function updateTemplate(
  id: string,
  input: {
    expectedRevision: number;
    name?: string;
    category?: string;
    description?: string;
    status?: string;
  }
): Promise<TemplateRecord> {
  const body = await apiRequest<{ data: TemplateRecord }>(`/templates/${id}`, {
    method: "PATCH",
    json: input,
  });
  if (!body?.data) throw new Error("更新响应不完整。");
  return body.data;
}

export async function listTemplateDocuments(templateId: string): Promise<{ items: TemplateDocument[] }> {
  const body = await apiRequest<{ data: { items: TemplateDocument[] } }>(`/templates/${templateId}/documents`);
  return body?.data ?? { items: [] };
}

export async function createTemplateDocument(
  templateId: string,
  input: {
    name: string;
    description?: string;
    displayOrder?: number;
    wordFileId: string;
    importStructure?: TemplateImportStructure;
  }
): Promise<TemplateDocument> {
  const body = await apiRequest<{ data: TemplateDocument }>(`/templates/${templateId}/documents`, {
    method: "POST",
    json: input,
  });
  if (!body?.data) throw new Error("创建响应不完整。");
  return body.data;
}

export async function deleteTemplateDocument(id: string): Promise<void> {
  await apiRequest(`/template-documents/${id}`, {
    method: "DELETE",
  });
}

export async function parseTemplateWord(wordFileId: string): Promise<TemplateImportStructure> {
  const body = await apiRequest<{ data: TemplateImportStructure }>("/template-documents/parse", {
    method: "POST", json: { wordFileId }, timeoutMs: 60000,
  });
  if (!body?.data) throw new Error("模板解析响应不完整。");
  return body.data;
}
export async function getTemplateDocument(id: string): Promise<TemplateDocument> {
  const body = await apiRequest<{ data: TemplateDocument }>(`/template-documents/${id}`);
  if (!body?.data) throw new Error("模板响应不完整。");
  return body.data;
}
export async function saveTemplateStructure(id: string, structure: TemplateImportStructure): Promise<TemplateDocument> {
  const body = await apiRequest<{ data: TemplateDocument }>(`/template-documents/${id}/structure`, {
    method: "PATCH", json: { expectedRevision: structure.revision, importStructure: structure }, timeoutMs: 60000,
  });
  if (!body?.data) throw new Error("模板保存响应不完整。");
  return body.data;
}

// ---------------------------------------------------------------- A06 模板版本与草稿（17 §3.8）

export interface VersionRef { versionId: string; seq: number | null }
export interface Person { id: string; name: string }
export interface TemplateVersionItem {
  versionId: string; seq: number; status: "published" | "superseded";
  publishedAt: string | null; publishedBy: Person | null; note: string;
  restoredFrom: VersionRef | null; parentVersion: VersionRef | null;
  sourceSha256: string; editedSha256: string | null; hasEditedFile: boolean;
}
export interface UsageItem { projectId: string; projectName: string; documentId: string; documentName: string; seq: number | null; legacy: boolean }
export interface TemplateVersionList {
  items: TemplateVersionItem[];
  current: VersionRef | null;
  draft: { draftRevision: number; updatedAt: string; updatedBy: Person | null; parentVersion: VersionRef | null } | null;
  reviewRequired: boolean;
  original: { fileId: string; name: string; sha256: string };
  usage: { total: number; visible: UsageItem[]; hiddenCount: number };
  templateName: string; documentName: string; templateId: string;
}
export type TableEditable = "cells" | "partial" | "none";
export interface TableEditReason { code: string; message: string; cells?: number[][] }
export interface EditorBlock extends Omit<TemplateImportBlock, "path"> {
  path: string | null;
  lock?: { text: string | null; delete: string | null };
  styleKey?: string | null;
  styleName?: string | null;
  tableEditable?: TableEditable;
  rowsEditable?: boolean;
  lockedCells?: number[][];
  tableEditReasons?: TableEditReason[];
  cells?: string[][];
  baseCells?: string[][];
  virtual?: boolean;
  unplaced?: boolean;
}
export interface EditorStructure { schemaVersion: 1; revision: number; reviewed: boolean; sourceHash: string; blocks: EditorBlock[] }
export type StyleValues = Record<string, any>;
export interface TemplateFormat { styles: Record<string, StyleValues>; defined?: Record<string, StyleValues> }
export interface DraftPayload {
  structure: EditorStructure; format: TemplateFormat; baseStyles: Record<string, StyleValues>;
  draftRevision: number; parentVersion: VersionRef | null; restoredFrom: VersionRef | null;
  uploaded: boolean; unplacedCount: number; updatedAt: string | null; updatedBy: Person | null;
  alignment?: { matched: number; total: number; unmatched: { blockId: string; text: string }[] };
  notes?: { blockId: string | null; message: string }[];
  notePrefill?: string;
}
export interface DraftState { draft: DraftPayload | null; base: DraftPayload | null; reviewRequired: boolean; current: VersionRef | null; templateName: string; documentName: string; templateId: string }
export interface DiffItem { blockId: string; change: "added" | "removed" | "modified" | "moved"; category: "headings" | "body" | "tables" | "guidance"; heading: string;
  before: { kind: string; text: string; headingLevel: number | null; cells?: string[][] } | null;
  after: { kind: string; text: string; headingLevel: number | null; cells?: string[][] } | null; fields: string[] }
export interface StyleDiff { style: string; prop: string; label: string; before: any; after: any }
export interface DiffSummary { headings: number; body: number; tables: number; guidance: number; format: number }
export interface VersionDiff { summary: DiffSummary; blocks: DiffItem[]; styles: StyleDiff[] }
export interface ImpactItem { style: string; prop: string; before: any; after: any; affectedParagraphs: number; directFormatParagraphs: number; willClear: number; blockIds: string[]; directBlockIds: string[]; truncated: boolean }
export interface RebaseConflict { blockId: string | null; field: string; base: any; theirs: any; mine: any; heading: string; text: string; category: string; resolved: string | null }
export interface RebaseResult { merged: { blocks: EditorBlock[] } | null; conflicts: RebaseConflict[]; theirsChanges: VersionDiff; currentVersion: TemplateVersionItem; notes: { blockId: string | null; message: string }[] }

export interface TemplateDocumentVersionSummary {
  currentVersion: { versionId: string; seq: number; publishedAt: string | null; publishedBy: string | null } | null;
  hasDraft: boolean;
  usageCount: number;
}

const base = (id: string) => `/template-documents/${id}`;

export async function listTemplateVersions(id: string): Promise<TemplateVersionList> {
  const body = await apiRequest<{ data: TemplateVersionList }>(`${base(id)}/versions`);
  if (!body?.data) throw new Error("版本响应不完整。");
  return body.data;
}
export async function getTemplateVersion(id: string, versionId: string): Promise<TemplateVersionItem & { structure: EditorStructure; format: TemplateFormat }> {
  const body = await apiRequest<{ data: TemplateVersionItem & { structure: EditorStructure; format: TemplateFormat } }>(`${base(id)}/versions/${versionId}`, { timeoutMs: 60000 });
  if (!body?.data) throw new Error("版本响应不完整。");
  return body.data;
}
export function templateVersionFileUrl(id: string, versionId: string, kind: "source" | "edited") {
  return `/api/v1${base(id)}/versions/${versionId}/file?kind=${kind}`;
}
export async function diffTemplateVersions(id: string, a: string, b: string): Promise<VersionDiff> {
  const body = await apiRequest<{ data: VersionDiff }>(`${base(id)}/versions/${a}/diff/${b}`, { timeoutMs: 60000 });
  if (!body?.data) throw new Error("对比响应不完整。");
  return body.data;
}
export async function getTemplateDraft(id: string): Promise<DraftState> {
  const body = await apiRequest<{ data: DraftState }>(`${base(id)}/draft`, { timeoutMs: 60000 });
  if (!body?.data) throw new Error("草稿响应不完整。");
  return body.data;
}
export async function saveTemplateDraft(id: string, input: { structure: EditorStructure; format: TemplateFormat; expectedDraftRevision: number; parentVersionId?: string }): Promise<{ draftRevision: number; updatedAt: string }> {
  const body = await apiRequest<{ data: { draftRevision: number; updatedAt: string } }>(`${base(id)}/draft`, { method: "PUT", json: input, timeoutMs: 60000 });
  if (!body?.data) throw new Error("草稿保存响应不完整。");
  return body.data;
}
export async function discardTemplateDraft(id: string, expectedDraftRevision: number): Promise<void> {
  await apiRequest(`${base(id)}/draft?expectedDraftRevision=${expectedDraftRevision}`, { method: "DELETE" });
}
export async function publishTemplateVersion(id: string, draftRevision: number, note: string): Promise<TemplateVersionItem> {
  const body = await apiRequest<{ data: { version: TemplateVersionItem } }>(`${base(id)}/versions`, { method: "POST", json: { draftRevision, note }, timeoutMs: 180000 });
  if (!body?.data) throw new Error("发布响应不完整。");
  return body.data.version;
}
export async function restoreTemplateVersion(id: string, versionId: string, expectedDraftRevision: number): Promise<DraftPayload> {
  const body = await apiRequest<{ data: DraftPayload }>(`${base(id)}/draft:restore`, { method: "POST", json: { versionId, expectedDraftRevision }, timeoutMs: 60000 });
  if (!body?.data) throw new Error("恢复响应不完整。");
  return body.data;
}
export async function rebaseTemplateDraft(id: string, input: { expectedDraftRevision: number; dryRun?: boolean; resolutions?: { blockId: string | null; field: string; take: "mine" | "theirs" }[] }): Promise<RebaseResult & DraftPayload> {
  const body = await apiRequest<{ data: RebaseResult & DraftPayload }>(`${base(id)}/draft:rebase`, { method: "POST", json: input, timeoutMs: 60000 });
  if (!body?.data) throw new Error("合并响应不完整。");
  return body.data;
}
export async function uploadTemplateDraft(id: string, wordFileId: string, expectedDraftRevision: number): Promise<DraftPayload> {
  const body = await apiRequest<{ data: DraftPayload }>(`${base(id)}/draft:upload`, { method: "POST", json: { wordFileId, expectedDraftRevision }, timeoutMs: 120000 });
  if (!body?.data) throw new Error("上传响应不完整。");
  return body.data;
}
export async function templateFormatImpact(id: string, changes?: { style: string; prop: string; value: any }[]): Promise<ImpactItem[]> {
  const body = await apiRequest<{ data: { items: ImpactItem[] } }>(`${base(id)}/draft:format-impact`, { method: "POST", json: changes ? { changes } : {}, timeoutMs: 60000 });
  return body?.data?.items ?? [];
}
