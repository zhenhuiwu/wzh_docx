import { apiRequest } from "./client";

export interface DocumentPlan {
  id: string;
  name: string;
  purpose: string;
  templateId: string | null;
  templateName: string | null;
  templateStatus: string | null;
  documentId: string | null;
}

export async function listDocumentPlans(projectId: string, page = 1) {
  const body = await apiRequest<{ data: { items: DocumentPlan[]; total: number; page: number; pageSize: number } }>(
    "/projects/" + projectId + "/document-plans?page=" + page + "&pageSize=20",
  );
  if (!body?.data) throw new Error("文档计划响应不完整。");
  return body.data;
}

export async function createDocumentPlan(
  projectId: string,
  input: { name: string; purpose: string; templateId: string | null },
) {
  const body = await apiRequest<{ data: DocumentPlan }>("/projects/" + projectId + "/document-plans", {
    method: "POST",
    json: input,
  });
  if (!body?.data) throw new Error("文档计划保存响应不完整。");
  return body.data;
}
