import { apiRequest } from "./client";

export interface ChainStage { key: string; name: string }
export interface ChainNode {
  key: string | null;
  name: string;
  stage: string | null;
  upstream: string[];
  documentId: string | null;
  status: string;
  created: boolean;
  written: number;
  total: number;
  ownerName: string | null;
  updatedAt: string | null;
  templateDocumentId?: string | null;
}
export interface ProjectChain { stages: ChainStage[]; nodes: ChainNode[]; others: ChainNode[]; canCreate: boolean }

export interface PlanPerson { name: string | null; userId: string | null; isMe: boolean }
export interface PlanTarget { documentId: string | null; documentName: string | null; chapter: string | null; found: boolean }
export interface PlanTask { title: string; assignee: PlanPerson; targets: PlanTarget[]; documentCount: number }
export interface PlanFramework {
  key: string;
  name: string;
  assignee: PlanPerson;
  references: { documentId: string; documentName: string; chapter: string }[];
}
export interface PlanTasks {
  planDocument: { id: string; name: string } | null;
  tasks: PlanTask[];
  frameworks: PlanFramework[];
  canManage: boolean;
}

export async function getProjectChain(projectId: string): Promise<ProjectChain> {
  const body = await apiRequest<{ data: ProjectChain }>(`/projects/${projectId}/doc-chain`);
  if (!body?.data) throw new Error("依赖链响应不完整。");
  return body.data;
}

export async function getPlanTasks(projectId: string): Promise<PlanTasks> {
  const body = await apiRequest<{ data: PlanTasks }>(`/projects/${projectId}/plan-tasks`);
  if (!body?.data) throw new Error("任务分解响应不完整。");
  return body.data;
}

export async function seedBuiltinTemplates(): Promise<{ created: string[] }> {
  const body = await apiRequest<{ data: { created: string[] } }>("/templates/builtin:seed", { method: "POST" });
  if (!body?.data) throw new Error("内置模板响应不完整。");
  return body.data;
}
