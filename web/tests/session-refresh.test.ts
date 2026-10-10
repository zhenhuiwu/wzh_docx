import { describe, it, expect, vi, beforeEach } from "vitest";

(globalThis as any).window = globalThis;
(globalThis as any).location = { pathname: "/projects", search: "", hash: "", host: "x", origin: "http://x", href: "http://x/projects" };
(globalThis as any).history = { state: null, length: 1, replaceState() {}, pushState() {}, go() {} };
(globalThis as any).addEventListener = () => {};
(globalThis as any).removeEventListener = () => {};
(globalThis as any).sessionStorage = { removeItem() {}, getItem() { return null; }, setItem() {} };

function res(status: number, body: unknown) {
  return { status, ok: status >= 200 && status < 300, json: async () => body } as any;
}
const me = (perms: string[], account = "a") => res(200, { data: { user: { account, name: account.toUpperCase(), requiresFirstLoginChange: false }, permissions: perms, csrfToken: "t" } });

describe("403 refresh", () => {
  beforeEach(() => { vi.resetModules(); vi.useRealTimers(); });

  it("plain 403 triggers one /auth/me for concurrent 403s; throttled within 2s; no navigation loop", async () => {
    const calls: string[] = [];
    let perms = ["users.read"];
    (globalThis as any).fetch = vi.fn(async (url: string) => {
      calls.push(url);
      if (url.endsWith("/auth/me")) { await new Promise(r => setTimeout(r, 20)); return me(perms); }
      return res(403, { error: { code: "FORBIDDEN", message: "x" } });
    });
        const session = await import("../src/session");
    const client = await import("../src/api/client");
    client.onForbidden(() => { void session.refreshSession(); });
    client.onPasswordChangeRequired(() => { session.markPasswordChangeRequired(); });
    client.onSessionExpired(() => { session.markSessionExpired(); });
    await session.ensureSession();
    expect(session.readSession()?.permissions).toEqual(["users.read"]);
    calls.length = 0;
    perms = [];
        await Promise.allSettled([1, 2, 3, 4, 5].map(() => client.apiRequest("/users")));
    await new Promise(r => setTimeout(r, 60));
    expect(calls.filter(u => u.endsWith("/auth/me")).length).toBe(1);
    expect(session.readSession()?.permissions).toEqual([]);
    // another 403 within 2s: no new refresh
    await client.apiRequest("/users").catch(() => {});
    await new Promise(r => setTimeout(r, 40));
    expect(calls.filter(u => u.endsWith("/auth/me")).length).toBe(1);
      });

  it("403 on /auth/me itself does not trigger refresh (no loop)", async () => {
    const calls: string[] = []; let n = 0;
    (globalThis as any).fetch = vi.fn(async (url: string) => {
      calls.push(url);
      n++;
      if (n === 1) return me(["a"]);
      return res(403, { error: { code: "FORBIDDEN", message: "x" } });
    });
        const session = await import("../src/session");
    const client = await import("../src/api/client");
    client.onForbidden(() => { void session.refreshSession(); });
    client.onPasswordChangeRequired(() => { session.markPasswordChangeRequired(); });
    client.onSessionExpired(() => { session.markSessionExpired(); });
    await session.ensureSession();
    calls.length = 0;
    await client.apiRequest("/users").catch(() => {});
    await new Promise(r => setTimeout(r, 30));
    // one /users + one /auth/me (which itself 403s) and nothing more
    expect(calls.length).toBe(2);
    expect(session.readSession()?.permissions).toEqual(["a"]);
  });

  it("PASSWORD_CHANGE_REQUIRED 403 goes to change-password path, not refresh", async () => {
    const calls: string[] = [];
    (globalThis as any).fetch = vi.fn(async (url: string) => {
      calls.push(url);
      if (url.endsWith("/auth/me")) return me(["a"]);
      return res(403, { error: { code: "PASSWORD_CHANGE_REQUIRED", message: "x" } });
    });
        const session = await import("../src/session");
    const client = await import("../src/api/client");
    client.onForbidden(() => { void session.refreshSession(); });
    client.onPasswordChangeRequired(() => { session.markPasswordChangeRequired(); });
    client.onSessionExpired(() => { session.markSessionExpired(); });
    await session.ensureSession();
    calls.length = 0;
    await client.apiRequest("/projects").catch(() => {});
    await new Promise(r => setTimeout(r, 30));
    expect(calls.filter(u => u.endsWith("/auth/me")).length).toBe(0);
    expect(session.readSession()?.mustChangePassword).toBe(true);
  });

  it("403 with no session (login page) does not refresh", async () => {
    const calls: string[] = [];
    (globalThis as any).fetch = vi.fn(async (url: string) => { calls.push(url); return res(403, { error: { code: "FORBIDDEN", message: "账号已停用。" } }); });
        const session = await import("../src/session");
    const client = await import("../src/api/client");
    client.onForbidden(() => { void session.refreshSession(); });
    await client.apiRequest("/auth/login", { method: "POST", json: {} }).catch(() => {});
    await new Promise(r => setTimeout(r, 30));
    expect(calls.length).toBe(1);
  });

  it("refresh result for another account is discarded (R3)", async () => {
    let account = "a";
    (globalThis as any).fetch = vi.fn(async (url: string) => {
      if (url.endsWith("/auth/me")) return me(account === "a" ? ["users.read"] : ["system.manage"], account);
      return res(403, { error: { code: "FORBIDDEN", message: "x" } });
    });
    const session = await import("../src/session");
    const client = await import("../src/api/client");
    client.onForbidden(() => { void session.refreshSession(); });
    await session.ensureSession();
    expect(session.readSession()?.staffId).toBe("a");
    // 刷新途中服务端会话已换成另一个账号：结果不得写进账号 a 的会话
    account = "b";
    await session.refreshSession();
    expect(session.readSession()?.staffId).toBe("a");
    expect(session.readSession()?.permissions).toEqual(["users.read"]);
  });
});
