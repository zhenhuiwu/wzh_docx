import { apiRequest } from "./client";
import type { AssignableRole, Assignments, OwnerSummary, ReadonlyReason } from "../memberModel";

export type ProjectOwnerSummary = OwnerSummary;

export interface ProjectRecord {
  id: string;
  name: string;
  description: string;
  organization: string;
  department: string;
  group: string;
  clientContact: string;
  /** 读取时的有效主负责人（19 S-1）；项目没有启用的负责人时为 null。 */
  ownerId: string | null;
  ownerName: string | null;
  periodStart: string | null;
  periodEnd: string | null;
  status: "not_started" | "active" | "paused" | "completed";
  archivedAt: string | null;
  revision: number;
  createdAt: string;
  updatedAt: string;
  softwareVersion: string | null;
  /** A05：成员名单独立版本号。 */
  membersRevision?: number;
  /** A05：启用和停用的负责人，owners[0] 是主负责人；显示时只用启用的（memberModel.ownersText）。 */
  owners?: ProjectOwnerSummary[];
  /** A05：本人在项目中的角色；只在列表返回，以管理员/审计员身份可见但不是成员时为 null。 */
  myRole?: ProjectMember["role"] | null;
  /** 只在项目详情返回（测试 L2）；列表项没有。 */
  permissions?: { projectRole: string | null; canEdit: boolean; canManageMembers: boolean; readonlyReason?: ReadonlyReason; /** 能归档 / 恢复（21 M-1，与接口同一判定）。 */ canArchive?: boolean };
}

export interface ProjectMember {
  userId: string;
  name: string;
  account: string;
  department: string;
  userStatus: "active" | "disabled" | string;
  role: "owner" | "editor" | "reviewer" | "viewer";
  isMe: boolean;
  addedAt: string | null;
  updatedAt: string | null;
  updatedBy: { id: string; name: string } | null;
  revision: number;
  /** A07 钩子：显式分工数，A07 上线前恒为 0。 */
  assignments: Assignments;
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
  myRole?: "owner" | "editor" | "viewer";
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
  if (query.myRole) params.set("myRole", query.myRole);
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

export interface MembersSnapshot {
  items: ProjectMember[];
  membersRevision: number;
  activeOwnerCount: number;
  owners: ProjectOwnerSummary[];
  ownerId: string | null;
  canManageMembers: boolean;
  readonlyReason: ReadonlyReason;
  projectRole: string | null;
}

export interface MemberChangeResult extends MembersSnapshot {
  item?: ProjectMember | null;
  ownerChanged: { from: { id: string; name: string } | null; to: { id: string; name: string } | null } | null;
  assignmentsCleared: Assignments;
  removedSelf?: boolean;
}

export interface MemberBatchResult extends MembersSnapshot {
  added: string[];
  failed: Array<{ userId: string; code: string; message: string }>;
}

export interface MemberCandidate {
  id: string;
  name: string;
  account: string;
  department: string;
  isMember: boolean;
  currentRole: ProjectMember["role"] | null;
}

export async function listMembers(projectId: string, signal?: AbortSignal) {
  const body = await apiRequest<{ data: MembersSnapshot }>(`/projects/${projectId}/members`, { signal });
  if (!body?.data) throw new Error("成员响应不完整。");
  return body.data;
}

/** A05：成员接口用独立的 membersRevision（17 B-2），不再读写项目 revision。只改已有成员的角色：对方已不在项目中时 404 MEMBER_NOT_FOUND（加入请用 addMembers）。 */
export async function setMember(projectId: string, userId: string, role: AssignableRole, expectedMembersRevision: number) {
  const body = await apiRequest<{ data: MemberChangeResult }>(
    `/projects/${projectId}/members/${userId}`,
    { method: "PUT", json: { role, expectedMembersRevision } },
  );
  if (!body?.data) throw new Error("成员响应不完整。");
  return body.data;
}

/** 返回 null 表示对方已经不是成员（204），按成功处理并刷新名单。 */
export async function removeMember(projectId: string, userId: string, expectedMembersRevision: number) {
  const body = await apiRequest<{ data: MemberChangeResult }>(
    `/projects/${projectId}/members/${userId}?expectedMembersRevision=${expectedMembersRevision}`,
    { method: "DELETE" },
  );
  return body?.data ?? null;
}

export async function addMembers(projectId: string, userIds: string[], role: AssignableRole, expectedMembersRevision: number) {
  const body = await apiRequest<{ data: MemberBatchResult }>(
    `/projects/${projectId}/members:batch`,
    { method: "POST", json: { userIds, role, expectedMembersRevision } },
  );
  if (!body?.data) throw new Error("成员响应不完整。");
  return body.data;
}

export async function listMemberCandidates(projectId: string, q: string, signal?: AbortSignal) {
  const params = new URLSearchParams({ q, page: "1", pageSize: "50" });
  const body = await apiRequest<{ data: { items: MemberCandidate[]; total: number } }>(
    `/projects/${projectId}/member-candidates?${params.toString()}`,
    { signal },
  );
  return body?.data ?? { items: [], total: 0 };
}
