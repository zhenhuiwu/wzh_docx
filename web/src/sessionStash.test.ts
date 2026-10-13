import { describe, expect, it, vi } from "vitest";

vi.mock("./api/identity", () => ({ getCurrentSession: vi.fn(), logout: vi.fn(() => Promise.resolve()) }));

const store = new Map<string, string>();
const storage = {
  getItem: (key: string) => store.get(key) ?? null, setItem: (key: string, value: string) => void store.set(key, value),
  removeItem: (key: string) => void store.delete(key), clear: () => store.clear(), key: (i: number) => [...store.keys()][i] ?? null,
  get length() { return store.size; },
};
// Object.keys(localStorage) 在浏览器里列出键；这里用 Proxy 模拟
const proxied = new Proxy(storage, { ownKeys: () => [...store.keys()], getOwnPropertyDescriptor: (_t, key) => store.has(String(key)) ? { enumerable: true, configurable: true, value: store.get(String(key)) } : Reflect.getOwnPropertyDescriptor(storage, key) });
vi.stubGlobal("window", { localStorage: proxied, sessionStorage: proxied });
vi.stubGlobal("localStorage", proxied);
vi.stubGlobal("sessionStorage", proxied);

describe("27 复验 N-1：退出登录清掉 A07 暂存", () => {
  it("只删 docwb.a07.stash.*", async () => {
    window.localStorage.setItem("docwb.a07.stash.lisi.d1", "{\"text\":\"越权\"}");
    window.localStorage.setItem("other.key", "1");
    const { endSession } = await import("./session");
    await endSession();
    expect(window.localStorage.getItem("docwb.a07.stash.lisi.d1")).toBeNull();
    expect(window.localStorage.getItem("other.key")).toBe("1");
  });
});
