import { apiRequest } from "./client";

export interface Person { id: string; name: string | null }
export interface DocumentVersion {
  id: string; seq: number; label: string; message: string; kind: "create" | "revert";
  revertedFromSeq: number | null; revision: number; sectionCount: number; paragraphCount: number;
  createdAt: string; author: Person | null;
}
export interface DiffStats { added: number; removed: number; modified: number; paragraphsAdded: number; paragraphsRemoved: number }
export interface ParagraphChange { op: "insert" | "delete" | "replace"; old?: string; new?: string }
export interface SectionChange {
  type: "added" | "removed" | "modified";
  old: { heading: string; level: number } | null;
  new: { heading: string; level: number } | null;
  paragraphs: ParagraphChange[];
}
export interface Page<T> { items: T[]; offset: number; limit: number; total: number; hasMore: boolean }
export interface ProjectBaseline { id: string; seq: number; label: string; name: string; createdAt: string; documentCount: number; author: Person | null }
export interface ProjectBaselineDetail extends ProjectBaseline {
  documents: { document: { id: string; name: string }; version: DocumentVersion; sinceBaseline: DiffStats; changed: boolean }[];
}
export interface MyWritingItem {
  id: string; name: string; status: string; revision: number; updatedAt: string;
  project: { id: string; name: string }; owner: Person; myRole: "owner" | "assignee";
  assignmentsEnabled: boolean; assignedBlockCount: number; latestBaseline: string | null;
}

async function data<T>(path: string, options?: Parameters<typeof apiRequest>[1]): Promise<T> {
  const body = await apiRequest<{ data: T }>(path, options as never);
  if (!body?.data) throw new Error("响应不完整。");
  return body.data;
}

export const listVersions = (documentId: string, offset = 0, limit = 20) =>
  data<Page<DocumentVersion> & { currentRevision: number }>(`/documents/${documentId}/baselines?offset=${offset}&limit=${limit}`);
export const createVersion = (documentId: string, message: string) =>
  data<DocumentVersion>(`/documents/${documentId}/baselines`, { method: "POST", json: { message } });
export const revertVersion = (documentId: string, seq: number, expectedRevision: number, message = "") =>
  data<DocumentVersion>(`/documents/${documentId}/baselines/${seq}/revert`, { method: "POST", json: { expectedRevision, message } });
export const diffVersions = (documentId: string, base: number, compare: number | "current" = "current", offset = 0, limit = 50) =>
  data<Page<SectionChange> & { stats: DiffStats }>(`/documents/${documentId}/baselines-diff?base=${base}&compare=${compare}&offset=${offset}&limit=${limit}`, { timeoutMs: 120000 });

export const listProjectBaselines = (projectId: string, offset = 0, limit = 50) =>
  data<Page<ProjectBaseline>>(`/projects/${projectId}/baselines?offset=${offset}&limit=${limit}`);
export const getProjectBaseline = (projectId: string, seq: number) =>
  data<ProjectBaselineDetail>(`/projects/${projectId}/baselines/${seq}`, { timeoutMs: 120000 });
export const createProjectBaseline = (projectId: string, name: string, documentIds: string[]) =>
  data<ProjectBaseline>(`/projects/${projectId}/baselines`, { method: "POST", json: { name, documentIds }, timeoutMs: 120000 });

export const listMyWriting = (offset = 0, limit = 20, q = "") =>
  data<Page<MyWritingItem>>(`/my-writing?offset=${offset}&limit=${limit}${q ? `&q=${encodeURIComponent(q)}` : ""}`);

export interface PatchResult { documentRevision: number; merged: boolean }
/** 按块保存（A07 硬限制与方案 C 冲突处理都在服务端）；atomic：有冲突整体拒绝，不会只存一半。 */
export const patchBlocks = (documentId: string, baseDocumentRevision: number, ops: { op: "update"; objectId: string; baseRevision: number; markdown: string }[], clientSaveKey: string) =>
  data<PatchResult>(`/documents/${documentId}/objects/patch`, { method: "POST", json: { clientSaveKey, baseDocumentRevision, atomic: true, ops }, timeoutMs: 60000 });
