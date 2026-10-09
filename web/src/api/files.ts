import { apiRequest } from "./client";

export interface FileInfo {
  id: string;
  name: string;
  mediaType: string;
  size: number;
  sha256: string;
  status: "stored" | "quarantined" | "unavailable";
  createdAt: string;
}

export async function uploadFile(
  file: File,
  purpose: "source" | "template" | "evidence" | "attachment",
  options: { projectId?: string; baseId?: string; idempotencyKey: string; signal?: AbortSignal },
): Promise<FileInfo> {
  const form = new FormData();
  form.set("file", file);
  form.set("purpose", purpose);
  if (options.projectId) form.set("projectId", options.projectId);
  if (options.baseId) form.set("baseId", options.baseId);
  const body = await apiRequest<{ data: FileInfo }>("/files", {
    method: "POST",
    form,
    signal: options.signal,
    csrf: undefined,
    idempotencyKey: options.idempotencyKey,
  });
  if (!body?.data?.id) throw new Error("上传响应不完整。");
  return body.data;
}

export async function downloadFile(fileId: string, signal?: AbortSignal): Promise<Blob> {
  const timeout = new AbortController();
  const timer = window.setTimeout(() => timeout.abort(), 15000);
  const signals = signal ? AbortSignal.any([signal, timeout.signal]) : timeout.signal;
  try {
    const response = await fetch(`/api/v1/files/${fileId}/content`, {
      credentials: "same-origin",
      signal: signals,
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => null);
      const message = payload?.error?.message;
      throw new Error(typeof message === "string" ? message : "文件没有下载。");
    }
    return await response.blob();
  } finally {
    window.clearTimeout(timer);
  }
}
