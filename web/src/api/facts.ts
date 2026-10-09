import { apiRequest } from "./client";

export interface FactSource {
  sourceName: string;
  sourceVersion: string;
  excerpt: string;
  locatorVerified: boolean;
}

export interface ProjectFact {
  id: string;
  projectId: string;
  key: string;
  label: string;
  value: string | number | boolean | null;
  state: "candidate" | "confirmed" | "superseded";
  revision: number;
  decisionReason: string;
  sources: FactSource[];
  updatedAt: string;
}

export interface FactList {
  items: ProjectFact[];
  page: number;
  pageSize: number;
  total: number;
}

export interface KnowledgeBinding {
  baseId: string;
  name: string;
  revision: number;
}

export interface FactQuery {
  page?: number;
  pageSize?: number;
  name?: string;
  state?: string;
}

export async function listFacts(projectId: string, query: FactQuery = {}) {
  const params = new URLSearchParams({
    page: String(query.page ?? 1),
    pageSize: String(query.pageSize ?? 10),
  });
  if (query.name) params.set("name", query.name);
  if (query.state) params.set("state", query.state);
  const body = await apiRequest<{ data: FactList }>(`/projects/${projectId}/facts?${params.toString()}`);
  return body?.data ?? { items: [], page: 1, pageSize: 10, total: 0 };
}

export async function createFact(
  projectId: string,
  input: { key: string; label: string; value: string; decisionReason: string },
) {
  const body = await apiRequest<{ data: ProjectFact }>(`/projects/${projectId}/facts`, { method: "POST", json: input });
  if (!body?.data) throw new Error("事实响应不完整。");
  return body.data;
}

export async function reviseFact(
  factId: string,
  input: { expectedRevision: number; value: string; decisionReason: string },
) {
  const body = await apiRequest<{ data: ProjectFact }>(`/facts/${factId}`, { method: "PATCH", json: input });
  if (!body?.data) throw new Error("修改响应不完整。");
  return body.data;
}

export async function confirmFact(factId: string, revision: number, reason: string) {
  const body = await apiRequest<{ data: ProjectFact }>(`/facts/${factId}/confirm`, {
    method: "POST",
    json: { expectedRevision: revision, reason },
  });
  if (!body?.data) throw new Error("确认响应不完整。");
  return body.data;
}

export async function listBindings(projectId: string) {
  const body = await apiRequest<{ data: { items: KnowledgeBinding[]; projectRevision: number } }>(
    `/projects/${projectId}/knowledge-bindings`,
  );
  return body?.data ?? { items: [], projectRevision: 0 };
}

export async function setBindings(projectId: string, projectRevision: number, baseIds: string[]) {
  const body = await apiRequest<{ data: { items: KnowledgeBinding[]; projectRevision: number } }>(
    `/projects/${projectId}/knowledge-bindings`,
    { method: "PUT", json: { expectedProjectRevision: projectRevision, baseIds } },
  );
  return body?.data;
}
