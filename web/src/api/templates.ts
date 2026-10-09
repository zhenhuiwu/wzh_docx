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
  importStructure?: TemplateImportStructure;
}

export type TemplateBlockKind = "heading" | "guidance" | "body" | "placeholder" | "table" | "caption" | "toc" | "cover" | "example";
export interface TemplateImportBlock {
  id: string;
  path: string;
  kind: TemplateBlockKind;
  text: string;
  sourceText: string;
  headingLevel: number | null;
  headingId: string | null;
  tableId: string | null;
  protected: boolean;
  isTable: boolean;
  warning?: string;
}
export interface TemplateImportStructure {
  schemaVersion: 1;
  revision: number;
  reviewed: boolean;
  sourceHash: string;
  blocks: TemplateImportBlock[];
}
export const templateBlockLabels: Record<TemplateBlockKind, string> = {
  heading: "标题", guidance: "AI 提示词", body: "正文", placeholder: "填写位置",
  table: "表格", caption: "题注", toc: "目录", cover: "封面与更改记录", example: "示例",
};

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
    importStructure?: TemplateImportStructure;
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

export async function parseTemplateWord(wordFileId: string): Promise<TemplateImportStructure> {
  const body = await apiRequest<{ data: TemplateImportStructure }>("/template-documents/parse", {
    method: "POST", json: { wordFileId }, timeoutMs: 60000,
  });
  if (!body?.data) throw new Error("模板解析响应不完整。");
  return body.data;
}
export async function getTemplateDocument(id: string): Promise<TemplateDocument> {
  const body = await apiRequest<{ data: TemplateDocument }>(`/template-documents/${id}`);
  if (!body?.data) throw new Error("模板响应不完整。");
  return body.data;
}
export async function saveTemplateStructure(id: string, structure: TemplateImportStructure): Promise<TemplateDocument> {
  const body = await apiRequest<{ data: TemplateDocument }>(`/template-documents/${id}/structure`, {
    method: "PATCH", json: { expectedRevision: structure.revision, importStructure: structure }, timeoutMs: 60000,
  });
  if (!body?.data) throw new Error("模板保存响应不完整。");
  return body.data;
}
