import { getCurrentSession, logout } from "./api/identity";

const STAFF_MEMORY = "doc-workbench.session";

export interface Session {
  staffId: string;
  name: string;
}

let cache: Session | null = null;
let logoutTask: Promise<void> | null = null;
let signedOut = false;

try {
  sessionStorage.removeItem(STAFF_MEMORY);
} catch {
  /* 旧的浏览器记录不是登录凭据。 */
}

export function readSession(): Session | null {
  return cache;
}

export function writeSession(session: Session) {
  signedOut = false;
  cache = session;
}

export function loginDestination(value: unknown): string {
  if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//")) return "/projects";
  try {
    const target = new URL(value, window.location.origin);
    return target.origin === window.location.origin && target.pathname !== "/login"
      ? target.pathname + target.search + target.hash
      : "/projects";
  } catch {
    return "/projects";
  }
}

// 由路由在页面的离开/保存检查通过后调用。注销失败时保留当前会话。
export function endSession(): Promise<void> {
  if (!logoutTask) {
    logoutTask = logout()
      .then(() => {
        signedOut = true;
        cache = null;
      })
      .finally(() => {
        logoutTask = null;
      });
  }
  return logoutTask;
}

export async function ensureSession(): Promise<Session | null> {
  if (logoutTask) await logoutTask;
  if (signedOut) return null;
  try {
    const current = await getCurrentSession();
    cache = { staffId: current.user.account, name: current.user.name };
    return cache;
  } catch {
    cache = null;
    return null;
  }
}
