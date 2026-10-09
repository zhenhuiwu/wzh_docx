import { apiRequest } from "./client";

export interface KnowledgeBase {
  id: string;
  name: string;
  summary: string;
  scope: "organization" | "project";
  projectId: string | null;
  ownerId: string;
  state: "active" | "archived";
  revision: number;
  updatedAt: string;
}

export interface SourceVersion {
  id: string;
  sourceId: string;
  baseId: string;
  name: string;
  version: string;
  fileId: string;
  status: string;
  message: string | null;
  mediaType: string;
  updatedAt: string;
}

export async function listKnowledgeBases(page = 1, name = "") {
  const params = new URLSearchParams({ page: String(page), pageSize: "10" });
  if (name) params.set("name", name);
  const body = await apiRequest<{ data: { items: KnowledgeBase[]; total: number; page: number } }>(
    `/knowledge-bases?${params.toString()}`,
  );
  return body?.data ?? { items: [], total: 0, page: 1 };
}

export async function createKnowledgeBase(input: {
  name: string;
  summary: string;
  scope: "organization" | "project";
  projectId: string | null;
  ownerId: string;
}) {
  const body = await apiRequest<{ data: KnowledgeBase }>("/knowledge-bases", { method: "POST", json: input });
  if (!body?.data) throw new Error("创建响应不完整。");
  return body.data;
}

export async function getKnowledgeBase(baseId: string) {
  const body = await apiRequest<{ data: KnowledgeBase }>(`/knowledge-bases/${baseId}`);
  if (!body?.data) throw new Error("知识库响应不完整。");
  return body.data;
}

export async function listSources(baseId: string) {
  const body = await apiRequest<{ data: { items: SourceVersion[] } }>(`/knowledge-bases/${baseId}/sources`);
  return body?.data.items ?? [];
}

export async function createSource(baseId: string, name: string, fileId: string) {
  const body = await apiRequest<{ data: SourceVersion }>(`/knowledge-bases/${baseId}/sources`, {
    method: "POST",
    json: { name, fileId },
  });
  if (!body?.data) throw new Error("资料响应不完整。");
  return body.data;
}
