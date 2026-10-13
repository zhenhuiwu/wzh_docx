import { apiRequest } from "./client";

/** A chapter the server's save checkpoint (CHAPTER_READONLY) refuses to let users rewrite. */
export interface LockedChapter {
  key: string;
  title: string;
  /** "framework": public framework reference, edited in 分工 › 公共框架; "split": the dev plan's 任务分解, generated from tasks. */
  kind: "framework" | "split";
  headingObjectId: string | null;
  objectIds: string[];
}

export async function getLockedChapters(documentId: string): Promise<LockedChapter[]> {
  const response = await apiRequest<{ data: { chapters: LockedChapter[] } }>(`/documents/${encodeURIComponent(documentId)}/locked-chapters`);
  return response?.data.chapters ?? [];
}
