import { apiRequest } from "./client";
import type { ProjectRecord } from "./projects";

export interface WorkIssue {
  id: string;
  projectId: string;
  projectName: string;
  title: string;
  kind: "missing_material" | "fact_confirmation" | "review" | "impact" | "verification" | "export_error";
  status: "open" | "resolved";
  assigneeId: string | null;
  target: { kind: string; id?: string; name: string };
  updatedAt: string;
}

export interface WorkJob {
  id: string;
  projectId: string | null;
  kind: string;
  status: string;
  target: { kind: string; id?: string; name: string };
  stageName: string;
}

export interface WorkbenchData {
  issues: WorkIssue[];
  recentProjects: ProjectRecord[];
  activeJobs: WorkJob[];
  updatedAt: string;
}

export async function getWorkbench() {
  const body = await apiRequest<{ data: WorkbenchData }>("/workbench");
  if (!body?.data) throw new Error("工作台响应不完整。");
  return body.data;
}

export async function listIssues(query: { page?: number; projectId?: string; kind?: string; name?: string } = {}) {
  const params = new URLSearchParams({ page: String(query.page ?? 1), pageSize: "10" });
  if (query.projectId) params.set("projectId", query.projectId);
  if (query.kind) params.set("kind", query.kind);
  if (query.name) params.set("name", query.name);
  const body = await apiRequest<{ data: { items: WorkIssue[]; page: number; pageSize: number; total: number } }>(
    `/issues?${params.toString()}`,
  );
  return body?.data ?? { items: [], page: 1, pageSize: 10, total: 0 };
}
