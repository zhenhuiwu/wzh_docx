import { apiRequest } from "./client";

export interface ReviewRecord {
  id: string;
  projectId: string;
  documentId: string;
  blockRevisionId: string;
  reviewerId: string;
  state: "pending" | "approved" | "rejected";
  reason: string;
  revision: number;
  updatedAt: string;
  documentName: string;
  blockTitle: string;
  reviewerName: string;
  submitterId: string;
  submitterName: string;
  createdAt: string;
}

export interface ReviewList {
  items: ReviewRecord[];
  page: number;
  pageSize: number;
  total: number;
}

export async function listReviews(projectId: string, query: { page?: number; pageSize?: number; name?: string; state?: string } = {}) {
  const params = new URLSearchParams({ page: String(query.page ?? 1), pageSize: String(query.pageSize ?? 10) });
  if (query.name) params.set("name", query.name);
  if (query.state) params.set("state", query.state);
  const body = await apiRequest<{ data: ReviewList }>(`/projects/${projectId}/reviews?${params.toString()}`);
  return body?.data ?? { items: [], page: 1, pageSize: 10, total: 0 };
}

export async function createReview(input: { blockRevisionId: string; reviewerId: string; expectedBlockRevision: number }) {
  const body = await apiRequest<{ data: ReviewRecord }>("/reviews", { method: "POST", json: input });
  if (!body?.data) throw new Error("审核响应不完整。");
  return body.data;
}

export async function decideReview(reviewId: string, expectedRevision: number, decision: "approve" | "reject", reason: string) {
  const body = await apiRequest<{ data: ReviewRecord }>(`/reviews/${reviewId}/decision`, {
    method: "POST",
    json: { expectedRevision, decision, reason },
  });
  if (!body?.data) throw new Error("决定响应不完整。");
  return body.data;
}

export async function adoptCandidate(blockId: string, revisionId: string, expectedRevision: number) {
  const body = await apiRequest<{ data: { revision: number; currentRevisionId: string | null; approvedRevisionId: string | null } }>(
    `/blocks/${blockId}/revisions/${revisionId}/adopt`,
    { method: "POST", json: { expectedRevision } },
  );
  if (!body?.data) throw new Error("采用响应不完整。");
  return body.data;
}

export async function discardCandidate(blockId: string, revisionId: string, expectedRevision: number, reason: string) {
  const body = await apiRequest<{ data: { revision: number } }>(`/blocks/${blockId}/revisions/${revisionId}/discard`, {
    method: "POST",
    json: { expectedRevision, reason },
  });
  if (!body?.data) throw new Error("放弃响应不完整。");
  return body.data;
}
