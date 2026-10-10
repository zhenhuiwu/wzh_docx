import { getCurrentSession, logout } from "./api/identity";

const STAFF_MEMORY = "doc-workbench.session";

export interface Session {
  staffId: string;
  name: string;
  mustChangePassword: boolean;
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

/** 服务端要求改密（例如管理员刚重置了密码），在下一次路由检查前就生效。 */
export function markPasswordChangeRequired() {
  if (cache) cache = { ...cache, mustChangePassword: true };
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
    cache = {
      staffId: current.user.account,
      name: current.user.name,
      mustChangePassword: Boolean(current.user.requiresFirstLoginChange),
    };
    return cache;
  } catch {
    cache = null;
    return null;
  }
}
