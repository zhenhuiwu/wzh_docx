import { shallowRef } from "vue";
import { getCurrentSession, logout } from "./api/identity";
import { objectCrumb, projectCrumb } from "./crumbs";

const STAFF_MEMORY = "doc-workbench.session";

export interface Session {
  staffId: string;
  name: string;
  mustChangePassword: boolean;
  /** 仅用于界面展示与导航显隐；真正的权限由服务端校验。 */
  department?: string;
  permissions?: string[];
}

export function hasPermission(session: Session | null, permission: string): boolean {
  return Boolean(session?.permissions?.includes(permission));
}

// 响应式：权限刷新后导航、无权限页立即更新（09 审查 S1）。
const current = shallowRef<Session | null>(null);
let refreshTask: Promise<Session | null> | null = null;
let lastRefresh = 0;
let logoutTask: Promise<void> | null = null;
let signedOut = false;

try {
  sessionStorage.removeItem(STAFF_MEMORY);
} catch {
  /* 旧的浏览器记录不是登录凭据。 */
}

export function readSession(): Session | null {
  return current.value;
}

export function writeSession(session: Session) {
  signedOut = false;
  current.value = session;
}

/** 服务端要求改密（例如管理员刚重置了密码），在下一次路由检查前就生效。 */
export function markPasswordChangeRequired() {
  if (current.value) current.value = { ...current.value, mustChangePassword: true };
}

/** 服务端返回 401：本地会话作废，下一次路由检查会重新询问服务端。 */
export function markSessionExpired() {
  current.value = null;
}

/**
 * 服务端返回 403（权限可能已被管理员调整）：重新读取 /auth/me，让导航与无权限页自愈。
 * 同一时刻只发一次，且 2 秒内不重复，避免页面上多个请求同时 403 时连发。
 */
export function refreshSession(): Promise<Session | null> {
  if (refreshTask) return refreshTask;
  if (Date.now() - lastRefresh < 2000 || !current.value) return Promise.resolve(current.value);
  lastRefresh = Date.now();
  const startedFor = current.value.staffId;
  refreshTask = getCurrentSession()
    .then((me) => {
      // 只写回发起刷新时的同一个账号：刷新途中退出并换号登录时丢弃旧结果（09 复审 R3）。
      const session = current.value;
      if (session && session.staffId === startedFor && me.user.account === startedFor) {
        current.value = { ...session, permissions: me.permissions, mustChangePassword: Boolean(me.user.requiresFirstLoginChange) };
      }
      return current.value;
    })
    // 刷新失败（含 401，已由 onSessionExpired 处理）时保留现有会话，不在这里登出。
    .catch(() => current.value)
    .finally(() => {
      refreshTask = null;
    });
  return refreshTask;
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
        current.value = null;
        // 换账号后不应短暂显示上一位用户看过的项目名（09 审查 S7）。
        projectCrumb.value = null;
        objectCrumb.value = null;
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
    const me = await getCurrentSession();
    current.value = {
      staffId: me.user.account,
      name: me.user.name,
      mustChangePassword: Boolean(me.user.requiresFirstLoginChange),
      department: me.user.department,
      permissions: me.permissions,
    };
    return current.value;
  } catch {
    current.value = null;
    return null;
  }
}
