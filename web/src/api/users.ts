import { apiRequest } from "./client";

export interface AccountUser {
  id: string;
  account: string;
  name: string;
  department: string;
  globalRoles: string[];
  status: "active" | "disabled";
  revision: number;
  createdAt: string;
  updatedAt: string;
  lastLoginAt: string | null;
}

export interface RoleDefinition {
  key: string;
  name: string;
  scope: "global" | "project";
  description: string;
  assignable: boolean;
  permissions: { key: string; name: string; description: string }[];
}

export interface UserQuery {
  page: number;
  pageSize?: number;
  name?: string;
  account?: string;
  department?: string;
  status?: "" | "active" | "disabled";
  role?: string;
}

export async function listUsers(query: UserQuery, signal?: AbortSignal) {
  const params = new URLSearchParams();
  params.set("page", String(query.page));
  params.set("pageSize", String(query.pageSize ?? 10));
  if (query.name) params.set("name", query.name);
  if (query.account) params.set("account", query.account);
  if (query.department) params.set("department", query.department);
  if (query.status) params.set("status", query.status);
  if (query.role) params.set("role", query.role);
  const body = await apiRequest<{
    data: { items: AccountUser[]; page: number; pageSize: number; total: number };
  }>(`/users?${params.toString()}`, { signal });
  return body?.data ?? { items: [], page: 1, pageSize: 10, total: 0 };
}

export async function listRoles(signal?: AbortSignal) {
  const body = await apiRequest<{ data: { items: RoleDefinition[] } }>("/roles", { signal });
  return body?.data.items ?? [];
}

export async function createAccount(input: {
  account: string;
  password: string;
  name: string;
  department: string;
  globalRoles: string[];
}) {
  const body = await apiRequest<{ data: AccountUser }>("/users", {
    method: "POST",
    json: input,
    csrf: undefined,
  });
  if (!body?.data) throw new Error("创建响应不完整。");
  return body.data;
}

export async function disableAccount(user: AccountUser, reason: string) {
  const body = await apiRequest<{ data: AccountUser }>(`/users/${user.id}/disable`, {
    method: "POST",
    json: { expectedRevision: user.revision, reason },
  });
  return body?.data;
}

export async function enableAccount(user: AccountUser, reason: string) {
  const body = await apiRequest<{ data: AccountUser }>(`/users/${user.id}/enable`, {
    method: "POST",
    json: { expectedRevision: user.revision, reason },
  });
  return body?.data;
}

export async function resetAccountCredentials(user: AccountUser, reason: string, password: string) {
  const body = await apiRequest<{
    data: { userId: string; status: string; requiresFirstLoginChange: boolean; message: string };
  }>(`/users/${user.id}/reset-credentials`, {
    method: "POST",
    json: { expectedRevision: user.revision, reason, password },
  });
  return body?.data;
}

export async function revokeAccountSessions(userId: string) {
  await apiRequest(`/users/${userId}/sessions`, { method: "DELETE" });
}
