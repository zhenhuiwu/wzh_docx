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
  upstreamDocuments?: UpstreamLink[];
  upstreamChanged?: boolean;
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

// ---------------------------------------------------------------- 第二轮：上游、任务、公共框架

export interface UpstreamLink {
  documentId: string;
  name: string;
  source: "template" | "manual";
  linkedRevision: number | null;
  currentRevision: number;
  changedSinceLink: boolean;
  copiedChapters: { chapter: string; upstreamDocumentId: string; blocks: number }[];
}
export interface TemplateRef { id: string; name: string; groupName?: string }

export async function getTemplateUpstreams(templateDocumentId: string) {
  const body = await apiRequest<{ data: { upstreams: TemplateRef[]; candidates: TemplateRef[] } }>(`/template-documents/${templateDocumentId}/upstreams`);
  if (!body?.data) throw new Error("上游模板响应不完整。");
  return body.data;
}

export async function setTemplateUpstreams(templateDocumentId: string, upstreamIds: string[]) {
  const body = await apiRequest<{ data: { upstreams: TemplateRef[] } }>(`/template-documents/${templateDocumentId}/upstreams`, { method: "PUT", json: { upstreamIds } });
  if (!body?.data) throw new Error("上游模板保存响应不完整。");
  return body.data.upstreams;
}

export async function setDocumentUpstreams(documentId: string, upstreamDocumentIds: string[]) {
  const body = await apiRequest<{ data: { upstreams: UpstreamLink[] } }>(`/documents/${documentId}/upstreams`, { method: "PUT", json: { upstreamDocumentIds } });
  if (!body?.data) throw new Error("上游文档保存响应不完整。");
  return body.data.upstreams;
}

export interface TaskTarget { documentId: string; documentName: string | null; objectId: string; chapter: string | null; stale: boolean; owners: string[] }
export interface ProjectTask {
  id: string;
  title: string;
  description: string;
  revision: number;
  assignee: { id: string | null; name: string | null; isMe: boolean };
  targets: TaskTarget[];
  documentCount: number;
}
export interface PublicBlock {
  id: string;
  key: string;
  name: string;
  body: string;
  revision: number;
  assignee: { id: string | null; name: string | null; isMe: boolean };
  updatedBy: string | null;
  updatedAt: string;
  canEdit: boolean;
  references: { documentId: string; documentName: string; objectId: string; chapter: string }[];
}
export interface TasksPayload {
  tasks: ProjectTask[];
  frameworks: PublicBlock[];
  planDocument: { id: string; name: string } | null;
  members: { id: string; name: string }[];
  documents: { id: string; name: string }[];
  canManage: boolean;
}
export interface PickerChapter { objectId: string; level: number; title: string; owners: { id: string; name: string }[]; task: string | null }
export interface TaskInput { title?: string; description?: string; assigneeId?: string | null; clearAssignee?: boolean; targets?: { documentId: string; objectId: string }[] }

export async function listTasks(projectId: string): Promise<TasksPayload> {
  const body = await apiRequest<{ data: TasksPayload }>(`/projects/${projectId}/tasks`);
  if (!body?.data) throw new Error("任务响应不完整。");
  return body.data;
}

export async function pickerChapters(projectId: string, documentId: string): Promise<PickerChapter[]> {
  const body = await apiRequest<{ data: { chapters: PickerChapter[] } }>(`/projects/${projectId}/documents/${documentId}/chapters`);
  if (!body?.data) throw new Error("章节响应不完整。");
  return body.data.chapters;
}

export async function createTask(projectId: string, input: TaskInput & { title: string }): Promise<ProjectTask> {
  const body = await apiRequest<{ data: ProjectTask }>(`/projects/${projectId}/tasks`, { method: "POST", json: input });
  if (!body?.data) throw new Error("任务保存响应不完整。");
  return body.data;
}

export async function updateTask(taskId: string, expectedRevision: number, input: TaskInput): Promise<ProjectTask> {
  const body = await apiRequest<{ data: ProjectTask }>(`/tasks/${taskId}`, { method: "PATCH", json: { expectedRevision, ...input } });
  if (!body?.data) throw new Error("任务保存响应不完整。");
  return body.data;
}

export async function deleteTask(taskId: string, expectedRevision: number): Promise<void> {
  await apiRequest(`/tasks/${taskId}?expectedRevision=${expectedRevision}`, { method: "DELETE" });
}

export async function savePublicBlock(blockId: string, expectedRevision: number, text: string): Promise<PublicBlock> {
  const body = await apiRequest<{ data: PublicBlock }>(`/public-blocks/${blockId}`, { method: "PUT", json: { expectedRevision, body: text } });
  if (!body?.data) throw new Error("公共内容保存响应不完整。");
  return body.data;
}

export async function assignPublicBlock(blockId: string, assigneeId: string | null): Promise<PublicBlock> {
  const body = await apiRequest<{ data: PublicBlock }>(`/public-blocks/${blockId}/assignee`, { method: "PUT", json: { assigneeId } });
  if (!body?.data) throw new Error("公共内容保存响应不完整。");
  return body.data;
}

export interface PublicRef { objectId: string; chapter: string; block: PublicBlock; seenRevision: number; updated: boolean }

export async function documentPublicRefs(documentId: string): Promise<PublicRef[]> {
  const body = await apiRequest<{ data: { references: PublicRef[] } }>(`/documents/${documentId}/public-refs`);
  if (!body?.data) throw new Error("公共内容响应不完整。");
  return body.data.references;
}

export async function markPublicSeen(documentId: string, blockId: string): Promise<void> {
  await apiRequest(`/documents/${documentId}/public-refs/${blockId}:seen`, { method: "POST" });
}
