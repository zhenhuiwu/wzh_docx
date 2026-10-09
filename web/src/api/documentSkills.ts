import { apiRequest } from "./client";
import type { DocumentContentResult } from "./documents";
import type { WritingSkill } from "./writingSkills";

export type DocumentSkill = { mode: "auto" | "none"; documentInstructions?: string }
  | (WritingSkill & { mode: "selected"; documentInstructions?: string });

export async function setDocumentSkill(id: string, input: {
  expectedRevision: number; mode: "auto" | "none" | "selected";
  skillId?: string; skillRevision?: number; documentInstructions: string;
}): Promise<DocumentContentResult> {
  const result = await apiRequest<{ data: DocumentContentResult }>(`/documents/${id}/writing-skill`, { method: "PATCH", json: input });
  if (!result?.data) throw new Error("编写规范保存响应不完整。");
  return result.data;
}
