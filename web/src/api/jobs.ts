import { apiRequest } from "./client";

export interface JobRecord {
  id: string;
  projectId: string | null;
  kind: string;
  status: string;
  revision: number;
  stageName: string;
  completedSteps: number;
  totalSteps: number;
  lastSequence: number;
  allowedActions: string[];
  blockingReason: string | null;
  target?: { kind: string; name: string; id?: string };
}

export async function listJobs(
  query: { page?: number; pageSize?: number; projectId?: string } = {},
  signal?: AbortSignal,
) {
  const params = new URLSearchParams({
    page: String(query.page ?? 1),
    pageSize: String(query.pageSize ?? 10),
  });
  if (query.projectId) params.set("projectId", query.projectId);
  const body = await apiRequest<{ data: { items: JobRecord[]; page: number; pageSize: number; total: number } }>(
    `/jobs?${params.toString()}`,
    { signal },
  );
  return body?.data ?? { items: [], page: 1, pageSize: 10, total: 0 };
}

export async function getJob(jobId: string, signal?: AbortSignal) {
  const body = await apiRequest<{ data: JobRecord }>(`/jobs/${jobId}`, { signal });
  if (!body?.data) throw new Error("任务响应不完整。");
  return body.data;
}

export async function controlJob(jobId: string, action: "pause" | "resume" | "cancel" | "verify", revision: number, extra?: Record<string, unknown>) {
  return apiRequest<{ data: JobRecord }>(`/jobs/${jobId}/${action}`, {
    method: "POST",
    json: { expectedRevision: revision, ...extra },
  });
}
