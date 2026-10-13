import { apiRequest } from "./client";

export interface DocumentReference {
  id: string;
  kind: "document" | "information";
  documentId?: string;
  sourceRevision?: number;
  latestRevision?: number;
  name: string;
  projectName?: string;
  note: string;
  content?: string;
  available: boolean;
}
export interface DocumentReferencesState { revision: number; canEdit: boolean; items: DocumentReference[] }
export interface ReferenceDocument { id: string; name: string; projectName: string; revision: number }
export interface ReferenceDocumentPage { items: ReferenceDocument[]; total: number; page: number; pageSize: number }
export interface ReferencePreview { name: string; content: string; html?: string; hasMore: boolean; nextOffset: number; revision?: number }
export type ReferenceInput =
  | { id: string; kind: "document"; documentId: string; sourceRevision: number; note: string }
  | { id: string; kind: "information"; name: string; content: string; note: string };

export async function getDocumentReferences(documentId: string) {
  const body = await apiRequest<{ data: DocumentReferencesState }>(`/documents/${documentId}/references`);
  if (!body?.data) throw new Error("参考资料响应不完整。");
  return body.data;
}
export async function listReferenceDocuments(documentId: string, name = "", page = 1) {
  const query = new URLSearchParams({ name, page: String(page), pageSize: "8" });
  const body = await apiRequest<{ data: ReferenceDocumentPage }>(`/documents/${documentId}/reference-documents?${query}`);
  if (!body?.data) throw new Error("参考文档列表响应不完整。");
  return body.data;
}
export async function getReferencePreview(documentId: string, referenceId: string, offset = 0) {
  const body = await apiRequest<{ data: ReferencePreview }>(`/documents/${documentId}/references/${referenceId}?offset=${offset}&limit=12000`);
  if (!body?.data) throw new Error("参考原文响应不完整。");
  return body.data;
}
export async function getReferenceDocumentPreview(documentId: string, sourceId: string, revision: number, offset = 0) {
  const query = new URLSearchParams({ revision: String(revision), offset: String(offset), limit: "12000" });
  const body = await apiRequest<{ data: ReferencePreview }>(`/documents/${documentId}/reference-documents/${sourceId}?${query}`);
  if (!body?.data) throw new Error("文档原文响应不完整。");
  return body.data;
}
export async function updateDocumentReferences(documentId: string, input: { expectedRevision: number; items: ReferenceInput[] }) {
  const body = await apiRequest<{ data: DocumentReferencesState }>(`/documents/${documentId}/references`, { method: "PUT", json: input });
  if (!body?.data) throw new Error("参考资料保存响应不完整。");
  return body.data;
}
