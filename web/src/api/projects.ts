import { apiRequest } from "./client";

export interface ProjectRecord {
  id: string;
  name: string;
  description: string;
  organization: string;
  department: string;
  group: string;
  clientContact: string;
  ownerId: string;
  ownerName: string;
  periodStart: string | null;
  periodEnd: string | null;
  status: "not_started" | "active" | "paused" | "completed";
  archivedAt: string | null;
  revision: number;
  createdAt: string;
  updatedAt: string;
  softwareVersion: string | null;
  /** 只在项目详情返回（测试 L2）；列表项没有。 */
  permissions?: { projectRole: string | null; canEdit: boolean; canManageMembers: boolean };
}

export interface ProjectMember {
  userId: string;
  name: string;
  role: "owner" | "editor" | "reviewer" | "viewer";
  revision: number;
}

export interface ProjectOwner { id: string; name: string; account: string }

export async function listProjectOwners(page = 1) {
  const body = await apiRequest<{ data: { items: ProjectOwner[]; page: number; pageSize: number; total: number } }>(
    `/project-owners?page=${page}&pageSize=50`
  );
  if (!body?.data) throw new Error("负责人响应不完整。");
  return body.data;
}

export interface ProjectQuery {
  page: number;
  pageSize?: number;
  name?: string;
  department?: string;
  ownerId?: string;
  status?: string;
  archived?: boolean;
}

export async function listProjects(query: ProjectQuery, signal?: AbortSignal) {
  const params = new URLSearchParams();
  params.set("page", String(query.page));
  params.set("pageSize", String(query.pageSize ?? 10));
  if (query.name) params.set("name", query.name);
  if (query.department) params.set("department", query.department);
  if (query.ownerId) params.set("ownerId", query.ownerId);
  if (query.status) params.set("status", query.status);
  if (query.archived) params.set("archived", "true");
  const body = await apiRequest<{
    data: { items: ProjectRecord[]; page: number; pageSize: number; total: number };
  }>(`/projects?${params.toString()}`, { signal });
  return body?.data ?? { items: [], page: 1, pageSize: 10, total: 0 };
}

export async function createProject(input: {
  name: string;
  description?: string;
  organization?: string;
  department?: string;
  group?: string;
  clientContact?: string;
  ownerId: string;
  periodStart?: string | null;
  periodEnd?: string | null;
  status: ProjectRecord["status"];
}) {
  const body = await apiRequest<{ data: ProjectRecord }>("/projects", { method: "POST", json: input });
  if (!body?.data) throw new Error("创建响应不完整。");
  return body.data;
}

export async function getProject(projectId: string, signal?: AbortSignal) {
  const body = await apiRequest<{ data: ProjectRecord }>(`/projects/${projectId}`, { signal });
  if (!body?.data) throw new Error("项目响应不完整。");
  return body.data;
}

export async function updateProject(projectId: string, patch: Record<string, unknown>) {
  const body = await apiRequest<{ data: ProjectRecord }>(`/projects/${projectId}`, {
    method: "PATCH",
    json: patch,
  });
  if (!body?.data) throw new Error("保存响应不完整。");
  return body.data;
}

export async function archiveProject(project: ProjectRecord, reason: string, restore = false) {
  const body = await apiRequest<{ data: ProjectRecord }>(
    `/projects/${project.id}/${restore ? "unarchive" : "archive"}`,
    { method: "POST", json: { expectedRevision: project.revision, reason } },
  );
  if (!body?.data) throw new Error("归档响应不完整。");
  return body.data;
}

export async function listMembers(projectId: string, signal?: AbortSignal) {
  const body = await apiRequest<{ data: { items: ProjectMember[]; projectRevision: number } }>(
    `/projects/${projectId}/members`,
    { signal },
  );
  return body?.data ?? { items: [], projectRevision: 0 };
}

export async function setMember(projectId: string, userId: string, role: ProjectMember["role"], expectedProjectRevision: number) {
  const body = await apiRequest<{ data: { items: ProjectMember[]; projectRevision: number } }>(
    `/projects/${projectId}/members/${userId}`,
    { method: "PUT", json: { userId, role, expectedProjectRevision } },
  );
  return body?.data;
}

export async function removeMember(projectId: string, userId: string) {
  const body = await apiRequest<{ data: { items: ProjectMember[]; projectRevision: number } }>(
    `/projects/${projectId}/members/${userId}`,
    { method: "DELETE" },
  );
  return body?.data;
}
