import { apiRequest } from "./client";

export type WritingSkillBaseKind = "general" | "srs" | "sdd";
export type WritingSkillStatus = "active" | "disabled";

export interface WritingSkill {
  id: string;
  name: string;
  description: string;
  instructions: string;
  baseKind: WritingSkillBaseKind;
  forbiddenTerms: string[];
  maxChars: number | null;
  status: WritingSkillStatus;
  revision: number;
  builtin: boolean;
  updatedAt: string;
}

export interface WritingSkillInput {
  name: string;
  description: string;
  instructions: string;
  baseKind: WritingSkillBaseKind;
  forbiddenTerms: string[];
  maxChars: number | null;
  status: WritingSkillStatus;
}

export interface WritingSkillQuery {
  page: number;
  pageSize?: number;
  name?: string;
  status?: WritingSkillStatus | "";
}

export interface WritingSkillPage { items: WritingSkill[]; total: number; page: number; pageSize: number }

export async function listWritingSkills(query: WritingSkillQuery, signal?: AbortSignal): Promise<WritingSkillPage> {
  const params = new URLSearchParams({ page: String(query.page), pageSize: String(query.pageSize ?? 20) });
  if (query.name) params.set("name", query.name);
  if (query.status) params.set("status", query.status);
  const body = await apiRequest<{ data: WritingSkillPage }>(`/writing-skills?${params}`, { signal });
  if (!body?.data) throw new Error("Skill 列表响应不完整。");
  return body.data;
}

export async function getWritingSkill(id: string, signal?: AbortSignal): Promise<WritingSkill> {
  const body = await apiRequest<{ data: WritingSkill }>(`/writing-skills/${id}`, { signal });
  if (!body?.data) throw new Error("Skill 响应不完整。");
  return body.data;
}

export async function createWritingSkill(input: WritingSkillInput, idempotencyKey?: string): Promise<WritingSkill> {
  const body = await apiRequest<{ data: WritingSkill }>("/writing-skills", { method: "POST", json: input, idempotencyKey });
  if (!body?.data) throw new Error("Skill 创建响应不完整。");
  return body.data;
}

export async function updateWritingSkill(id: string, patch: Partial<WritingSkillInput> & { expectedRevision: number }): Promise<WritingSkill> {
  const body = await apiRequest<{ data: WritingSkill }>(`/writing-skills/${id}`, { method: "PATCH", json: patch });
  if (!body?.data) throw new Error("Skill 保存响应不完整。");
  return body.data;
}
