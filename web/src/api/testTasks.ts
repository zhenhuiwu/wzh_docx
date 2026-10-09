import { apiRequest } from "./client";

export type TestKind = "functional" | "unit" | "concurrency";

export interface TestCase {
  id: string;
  title: string;
  kind: TestKind;
  planReference: string;
  sourceReference: string;
  expected: string;
  steps: Array<string | Record<string, unknown>>;
  enabled: boolean;
  needsInput?: string | string[];
}

export interface TestStep {
  action: string;
  description: string;
  status: string;
  actual?: string;
  screenshot?: string;
  reportScreenshot?: string;
}

export interface CaseResult {
  caseId: string;
  title: string;
  kind: TestKind;
  status: string;
  steps: TestStep[];
  error?: string;
  metrics?: Record<string, unknown>;
  log?: string;
}

export interface TestRun {
  id: string;
  status: string;
  startedAt: string;
  endedAt?: string;
  result: { status: string; cases: CaseResult[]; error?: string };
  stopRequested?: boolean;
  error?: string;
}

export interface TestTaskInput {
  name: string;
  projectId: string;
  planFileId: string;
  reportFileId?: string;
  frontendRoot: string;
  backendRoot: string;
  frontendUrl: string;
  backendUrl: string;
  executionMode: "agent" | "steps";
  testUsername: string;
  testPassword: string;
  dataScope: string;
  concurrency: number;
  requests: number;
}

export interface TestTask extends Omit<TestTaskInput, "testPassword"> {
  id: string;
  projectName?: string;
  planName?: string;
  reportName?: string;
  revision: number;
  status: string;
  error?: string;
  cases: TestCase[];
  runs: TestRun[];
  createdAt?: string;
  updatedAt?: string;
}

async function taskRequest(path: string, options: { method?: string; json?: unknown; signal?: AbortSignal; timeoutMs?: number } = {}) {
  const body = await apiRequest<{ data: TestTask }>(path, options);
  if (!body?.data) throw new Error("测试任务响应不完整。");
  return body.data;
}

export async function listTestTasks(query: { page: number; pageSize: number; projectId?: string; name?: string }, signal?: AbortSignal) {
  const params = new URLSearchParams({ page: String(query.page), pageSize: String(query.pageSize) });
  if (query.projectId) params.set("projectId", query.projectId);
  if (query.name) params.set("name", query.name);
  const body = await apiRequest<{ data: { items: TestTask[]; total: number; page: number; pageSize: number } }>(`/test-tasks?${params}`, { signal });
  if (!body?.data) throw new Error("测试任务列表响应不完整。");
  return body.data;
}

export const createTestTask = (input: TestTaskInput) => taskRequest("/test-tasks", { method: "POST", json: input });
export const updateTestTask = (id: string, input: Partial<Omit<TestTaskInput, "projectId">> & { expectedRevision: number }) => taskRequest(`/test-tasks/${id}`, { method: "PATCH", json: input });
export const getTestTask = (id: string, signal?: AbortSignal) => taskRequest(`/test-tasks/${id}`, { signal });
export const analyzeTestTask = (id: string, signal?: AbortSignal) => taskRequest(`/test-tasks/${id}/analyze`, { method: "POST", timeoutMs: 300000, signal });
export const setTestCaseEnabled = (id: string, caseId: string, enabled: boolean) => taskRequest(`/test-tasks/${id}/cases/${caseId}`, { method: "PATCH", json: { enabled } });
export const runTestTask = (id: string, expectedRevision: number, caseIds?: string[]) => taskRequest(`/test-tasks/${id}/run`, { method: "POST", json: { expectedRevision, caseIds } });
export const stopTestTask = (id: string) => taskRequest(`/test-tasks/${id}/stop`, { method: "POST" });

export interface TestDocumentInsertion {
  documentId: string;
  projectId: string;
  documentName: string;
  revision: number;
  insertedCount: number;
  alreadyInsertedCount: number;
}

export async function insertTestResults(id: string, input: { runId: string; documentId: string; expectedRevision: number; headingIndex: number | null; caseIds: string[] }): Promise<TestDocumentInsertion> {
  const response = await apiRequest<{ data: TestDocumentInsertion }>(`/test-tasks/${encodeURIComponent(id)}/documents/insert`, { method: "POST", json: input, timeoutMs: 120000 });
  if (!response?.data) throw new Error("文档补充响应不完整。");
  return response.data;
}

export function testEvidenceUrl(id: string, runId: string, fileName: string) {
  return `/api/v1/test-tasks/${encodeURIComponent(id)}/evidence/${encodeURIComponent(fileName)}?runId=${encodeURIComponent(runId)}`;
}

export async function downloadTestReport(id: string, runId: string): Promise<string> {
  const response = await apiRequest<{ data: { downloadUrl: string } }>(`/test-tasks/${encodeURIComponent(id)}/report?runId=${encodeURIComponent(runId)}&prepare=true`, { timeoutMs: 120000 });
  if (!response?.data?.downloadUrl) throw new Error("测试报告下载地址不完整。");
  return response.data.downloadUrl;
}
