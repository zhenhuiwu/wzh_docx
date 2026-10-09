import { apiRequest } from "./client";

export interface TemplateRecord {
  id: string;
  name: string;
  category: string;
  description: string;
  status: "draft" | "published" | "retired";
  revision: number;
  updatedAt: string;
  documentCount: number;
}

export interface TemplateDocument {
  id: string;
  name: string;
  description: string;
  displayOrder: number;
  wordFileId: string;
  formatTemplate: Record<string, any>;
  createdAt: string;
  updatedAt: string;
}

export const templateStatusLabel: Record<string, string> = {
  draft: "草稿",
  published: "已发布",
  retired: "已停用",
};

export async function listTemplates(
  page: number = 1,
  name: string = ""
): Promise<{ items: TemplateRecord[]; page: number; pageSize: number; total: number }> {
  const params = new URLSearchParams();
  params.set("page", page.toString());
  params.set("pageSize", "20");
  if (name) params.set("name", name);

  const body = await apiRequest<{ data: { items: TemplateRecord[]; page: number; pageSize: number; total: number } }>(
    `/templates?${params}`
  );
  return body?.data ?? { items: [], page: 1, pageSize: 20, total: 0 };
}

export async function createTemplate(input: {
  name: string;
  category: string;
  description?: string;
}): Promise<TemplateRecord> {
  const body = await apiRequest<{ data: TemplateRecord }>("/templates", {
    method: "POST",
    json: input,
  });
  if (!body?.data) throw new Error("创建响应不完整。");
  return body.data;
}

export async function getTemplate(id: string): Promise<TemplateRecord> {
  const body = await apiRequest<{ data: TemplateRecord }>(`/templates/${id}`);
  if (!body?.data) throw new Error("模板响应不完整。");
  return body.data;
}

export async function updateTemplate(
  id: string,
  input: {
    expectedRevision: number;
    name?: string;
    category?: string;
    description?: string;
    status?: string;
  }
): Promise<TemplateRecord> {
  const body = await apiRequest<{ data: TemplateRecord }>(`/templates/${id}`, {
    method: "PATCH",
    json: input,
  });
  if (!body?.data) throw new Error("更新响应不完整。");
  return body.data;
}

export async function listTemplateDocuments(templateId: string): Promise<{ items: TemplateDocument[] }> {
  const body = await apiRequest<{ data: { items: TemplateDocument[] } }>(`/templates/${templateId}/documents`);
  return body?.data ?? { items: [] };
}

export async function createTemplateDocument(
  templateId: string,
  input: {
    name: string;
    description?: string;
    displayOrder?: number;
    wordFileId: string;
  }
): Promise<TemplateDocument> {
  const body = await apiRequest<{ data: TemplateDocument }>(`/templates/${templateId}/documents`, {
    method: "POST",
    json: input,
  });
  if (!body?.data) throw new Error("创建响应不完整。");
  return body.data;
}

export async function deleteTemplateDocument(id: string): Promise<void> {
  await apiRequest(`/template-documents/${id}`, {
    method: "DELETE",
  });
}
