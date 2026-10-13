import { apiRequest } from "./client";

export interface Person { id: string; name: string }
export interface ResolvedObject {
  objectId: string; kind: string; owners: Person[]; via: string; viaObjectId: string | null; editable: boolean; readonlyReason: string | null;
}
export interface AssignmentView {
  assignmentMode: "open" | "enforced"; assignmentsRevision: number; documentRevision: number; canAssign: boolean; projectOwners: Person[];
  summary: { unassignedSections: number; unassignedRoots: number; unassignedFrontMatter: number; needsConfirm: number };
  explicit: Array<{ objectId: string; assignees: Array<Person & { userActive: boolean; stillMember: boolean; memberRole: string | null }>; source: string; needsConfirm: boolean }>;
  resolved?: ResolvedObject[];
  editableHeadings?: Array<{ headingId: string; minInsertLevel: number; explicit: boolean }>;
}
export interface DocObject { id: string; kind: string; revision: number; markdown: string; editable?: boolean; owners?: Person[]; isWordHeading?: boolean; wordHeadingLevel?: number }

export const DOCUMENT_TARGET = "@document";

export async function getAssignments(id: string): Promise<AssignmentView> {
  const body = await apiRequest<{ data: AssignmentView }>(`/documents/${id}/assignments?resolved=1`);
  if (!body?.data) throw new Error("分工响应不完整。");
  return body.data;
}
export async function putAssignments(id: string, objectIds: string[], assigneeIds: string[], expected: number): Promise<AssignmentView> {
  const body = await apiRequest<{ data: AssignmentView }>(`/documents/${id}/assignments`, {
    method: "PUT", json: { objectIds, assigneeIds, expectedAssignmentsRevision: expected },
  });
  if (!body?.data) throw new Error("分工响应不完整。");
  return body.data;
}
export async function putAssignmentMode(id: string, mode: "open" | "enforced", expected: number): Promise<{ assignmentMode: string; assignmentsRevision: number }> {
  const body = await apiRequest<{ data: { assignmentMode: string; assignmentsRevision: number } }>(`/documents/${id}/assignment-mode`, {
    method: "PUT", json: { mode, expectedAssignmentsRevision: expected },
  });
  if (!body?.data) throw new Error("分工响应不完整。");
  return body.data;
}
export async function getAllObjects(id: string): Promise<{ documentRevision: number; objectModel: boolean; objects: DocObject[]; assignmentsRevision?: number }> {
  const body = await apiRequest<{ data: any }>(`/documents/${id}/objects`);
  if (!body?.data) throw new Error("文档对象响应不完整。");
  return body.data;
}
export interface AssignmentSummary {
  byDocument: Array<{ documentId: string; name: string; assignmentMode: string; unassignedSections: number; documentOwners?: Person[] }>;
  byPerson: Array<{ userId: string; name: string; role: string | null; userActive: boolean; sections: number; blocks: number; documents: number }>;
  unassignedEditors: Array<{ userId: string; name: string }>;
  canAssign?: boolean;
}
export async function getAssignmentSummary(projectId: string): Promise<AssignmentSummary> {
  const body = await apiRequest<{ data: AssignmentSummary }>(`/projects/${projectId}/assignment-summary`);
  if (!body?.data) throw new Error("分工汇总响应不完整。");
  return body.data;
}
/** A07 被拒保存（17 §4.6）：只能取/更新本人的；PUT 只能改最新一份（暂存同步）。 */
export async function getRejectedSave(id: string, rid: string): Promise<{ markdown: string; createdAt?: string }> {
  const body = await apiRequest<{ data: any }>(`/documents/${id}/rejected-saves/${rid}`);
  if (!body?.data) throw new Error("被拒保存响应不完整。");
  return body.data;
}
export async function putRejectedSave(id: string, rid: string, markdown: string, blockIds?: Array<string | null>): Promise<void> {
  await apiRequest(`/documents/${id}/rejected-saves/${rid}`, { method: "PUT", json: { markdown, ...(blockIds ? { blockIds } : {}) } });
}
