import { apiRequest } from "./client";
import type { DocumentContentResult, MergeRequest } from "./documents";

export interface DocumentDiagram {
  id: string; name: string; kind: "flowchart" | "architecture";
  sourceRevision: number; previewUrl: string; downloadUrl: string;
}
export async function listDocumentDiagrams(id: string) {
  const result = await apiRequest<{ data: { items: DocumentDiagram[] } }>(`/documents/${id}/diagrams`);
  if (!result?.data) throw new Error("图表响应不完整。");
  return result.data.items;
}
export async function generateDocumentDiagram(id: string, expectedRevision: number, headingIndex: number | null, kind: DocumentDiagram["kind"], instruction: string, signal: AbortSignal) {
  const result = await apiRequest<{ data: DocumentDiagram }>(`/documents/${id}/diagrams`, { method: "POST", json: { expectedRevision, headingIndex, kind, instruction }, timeoutMs: 180000, signal });
  if (!result?.data) throw new Error("图表生成响应不完整。");
  return result.data;
}
export async function insertDocumentDiagram(id: string, diagramId: string, expectedRevision: number, headingIndex: number | null, merge: MergeRequest = {}) {
  const result = await apiRequest<{ data: DocumentContentResult & { formatTemplate: Record<string, any> } }>(`/documents/${id}/diagrams/${diagramId}/insert`, { method: "POST", json: { expectedRevision, headingIndex, ...merge }, timeoutMs: 120000 });
  if (!result?.data) throw new Error("图表插入响应不完整。");
  return result.data;
}
