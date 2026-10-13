import { apiRequest } from "./client";

export interface DocumentAssignment {
  objectId: string;
  kind: string;
  text: string;
  assigneeId: string | null;
  assigneeName: string | null;
  canEdit: boolean;
}

export interface DocumentAssignmentsState {
  revision: number;
  assignmentsRevision: number;
  enabled: boolean;
  canManage: boolean;
  items: DocumentAssignment[];
  members: Array<{ id: string; name: string }>;
}

export async function getDocumentAssignments(documentId: string) {
  const body = await apiRequest<{ data: DocumentAssignmentsState }>(`/documents/${documentId}/assignments`);
  if (!body?.data) throw new Error("编写分工响应不完整。");
  return body.data;
}

export async function updateDocumentAssignments(documentId: string, input: {
  expectedRevision: number;
  expectedAssignmentsRevision: number;
  enabled: boolean;
  items: Array<{ objectId: string; assigneeId: string | null }>;
}) {
  const body = await apiRequest<{ data: DocumentAssignmentsState }>(`/documents/${documentId}/assignments`, { method: "PUT", json: input });
  if (!body?.data) throw new Error("编写分工保存响应不完整。");
  return body.data;
}
