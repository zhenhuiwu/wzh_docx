import { apiRequest, setCsrfToken } from "./client";

export interface CurrentUser {
  id: string;
  account: string;
  name: string;
  department: string;
  globalRoles: string[];
  status: "active" | "disabled";
  revision: number;
  createdAt: string;
  updatedAt: string;
  lastLoginAt: string | null;
  /** 首个管理员等使用初始密码的账号，改密前其他接口都会被拒绝。 */
  requiresFirstLoginChange: boolean;
}

export interface CurrentSession {
  user: CurrentUser;
  permissions: string[];
  expiresAt: string;
  csrfToken: string;
}

interface SessionEnvelope {
  data: CurrentSession;
  requestId: string;
}

export async function fetchCsrf(): Promise<string> {
  const body = await apiRequest<{ data: { csrfToken: string } }>("/auth/csrf");
  const token = body?.data.csrfToken ?? "";
  setCsrfToken(token);
  return token;
}

export async function login(account: string, password: string, csrf: string): Promise<CurrentSession> {
  const body = await apiRequest<SessionEnvelope>("/auth/login", {
    method: "POST",
    json: { account, password },
    csrf,
  });
  if (!body?.data?.user?.account || !body.data.csrfToken) {
    throw new Error("登录响应不完整。");
  }
  setCsrfToken(body.data.csrfToken);
  return body.data;
}

export async function getCurrentSession(): Promise<CurrentSession> {
  const body = await apiRequest<SessionEnvelope>("/auth/me");
  if (!body?.data?.user?.account) {
    throw new Error("会话响应不完整。");
  }
  setCsrfToken(body.data.csrfToken);
  return body.data;
}

export async function logout(): Promise<void> {
  await apiRequest<undefined>("/auth/logout", { method: "POST" });
  setCsrfToken("");
}

export async function changePassword(currentPassword: string, newPassword: string): Promise<void> {
  await apiRequest<undefined>("/auth/change-password", {
    method: "POST",
    json: { currentPassword, newPassword },
  });
}
