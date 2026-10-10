export interface FieldError {
  field: string;
  message: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly fieldErrors: FieldError[];
  readonly currentRevision?: number;
  readonly requestId: string;

  constructor(status: number, code: string, message: string, requestId = "", fieldErrors: FieldError[] = [], currentRevision?: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.fieldErrors = fieldErrors;
    this.currentRevision = currentRevision;
  }
}

let csrfToken = "";
let passwordChangeHandler: (() => void) | null = null;
let sessionExpiredHandler: (() => void) | null = null;

/** 任意请求收到 403 PASSWORD_CHANGE_REQUIRED 时调用（由路由注册，跳到改密页）。 */
export function onPasswordChangeRequired(handler: () => void) {
  passwordChangeHandler = handler;
}

/** 任意请求收到 401（会话失效或被管理员吊销）时调用（由路由注册，跳到登录页）。 */
export function onSessionExpired(handler: () => void) {
  sessionExpiredHandler = handler;
}

export function setCsrfToken(token: string) {
  csrfToken = token;
}

export function clearCsrfToken() {
  csrfToken = "";
}

const WRITE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

function requestKey() {
  return crypto.randomUUID();
}

interface RequestOptions<T> {
  method?: string;
  json?: unknown;
  form?: FormData;
  signal?: AbortSignal;
  timeoutMs?: number;
  csrf?: string | null;
  idempotencyKey?: string;
  onEvent?: (event: T) => void;
}

export async function apiRequest<T>(path: string, options: RequestOptions<T> = {}): Promise<T | undefined> {
  const method = options.method ?? "GET";
  const headers = new Headers();
  const timeout = new AbortController();
  const timer = window.setTimeout(() => timeout.abort(), options.timeoutMs ?? 15000);
  const signal = options.signal
    ? AbortSignal.any([options.signal, timeout.signal])
    : timeout.signal;
  if (options.json !== undefined) headers.set("content-type", "application/json");
  const csrf = options.csrf === undefined ? csrfToken : options.csrf;
  if (csrf && WRITE_METHODS.has(method)) headers.set("X-CSRF-Token", csrf);
  if (options.idempotencyKey) headers.set("Idempotency-Key", options.idempotencyKey);
  else if (method === "POST" && (path === "/users" || path === "/projects")) {
    headers.set("Idempotency-Key", requestKey());
  }

  try {
    const response = await fetch(`/api/v1${path}`, {
      method,
      credentials: "same-origin",
      headers,
      body: options.form ?? (options.json !== undefined ? JSON.stringify(options.json) : undefined),
      signal,
    });
    if (response.status === 204) return undefined;
    if (response.ok && options.onEvent) {
      if (!response.body) throw new ApiError(502, "AI_FAILED", "AI 响应不完整。");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      const emitLines = () => {
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        for (const line of lines) if (line.trim()) options.onEvent!(JSON.parse(line) as T);
      };
      try {
        while (true) {
          const { value, done } = await reader.read();
          buffer += decoder.decode(value, { stream: !done });
          emitLines();
          if (done) break;
        }
        if (buffer.trim()) options.onEvent(JSON.parse(buffer) as T);
        return undefined;
      } finally {
        await reader.cancel().catch(() => undefined);
        reader.releaseLock();
      }
    }
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      const error = payload?.error ?? {};
      if (response.status === 403 && error.code === "PASSWORD_CHANGE_REQUIRED") passwordChangeHandler?.();
      // /auth/me 由路由守卫自行处理，/auth/login 的 401 是账号或密码错误。
      if (response.status === 401 && path !== "/auth/me" && path !== "/auth/login") sessionExpiredHandler?.();
      throw new ApiError(
        response.status,
        typeof error.code === "string" ? error.code : "INTERNAL_ERROR",
        typeof error.message === "string" ? error.message : "请求没有完成。",
        typeof payload?.requestId === "string" ? payload.requestId : "",
        Array.isArray(error.fieldErrors) ? error.fieldErrors : [],
        typeof error.currentRevision === "number" ? error.currentRevision : undefined,
      );
    }
    return payload as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiError(0, "INTERNAL_ERROR", "请求超时或已取消。");
    }
    throw new ApiError(0, "DEPENDENCY_UNAVAILABLE", "暂时不能连接服务。");
  } finally {
    window.clearTimeout(timer);
  }
}
