import { describe, expect, it } from "vitest";
import { sameTabOtherPage } from "./documentObjects";
import { claimTabId, TabIdentity, type ChannelLike } from "./tabIdentity";

class Bus {
  members: Channel[] = [];
  open() { const channel = new Channel(this); this.members.push(channel); return channel; }
}
class Channel implements ChannelLike {
  onmessage: ((event: MessageEvent) => void) | null = null;
  constructor(private bus: Bus) {}
  postMessage(message: unknown) {
    for (const other of this.bus.members) if (other !== this) other.onmessage?.({ data: message } as MessageEvent);
  }
  close() { this.bus.members = this.bus.members.filter(item => item !== this); }
}
function storage(initial: Record<string, string> = {}) {
  const data = { ...initial };
  return { data, getItem: (k: string) => data[k] ?? null, setItem: (k: string, v: string) => { data[k] = v; }, removeItem: (k: string) => { delete data[k]; } };
}
const now = () => Promise.resolve();
const pageOf = (identity: TabIdentity) => (identity as unknown as { options: { pageId: string } }).options.pageId;
/** 消息排队、由测试决定何时（以什么顺序）送达，模拟负载下的晚到与乱序。 */
class DelayedBus {
  members: DelayedChannel[] = [];
  queue: Array<() => void> = [];
  constructor(private seed = 0) {}
  open() { const channel = new DelayedChannel(this); this.members.push(channel); return channel; }
  flush() { const pending = this.queue.splice(0); for (const deliver of pending) deliver(); }
  /** 只送达一部分（按种子打乱），剩下的等窗口之后。 */
  drainSome(): Promise<void> {
    const pending = this.queue.splice(0);
    const order = pending.map((deliver, index) => ({ deliver, key: (index * 31 + this.seed * 17) % 11 }));
    order.sort((a, b) => a.key - b.key);
    const half = Math.floor(order.length / 2);
    order.slice(half).forEach(item => this.queue.push(item.deliver));
    order.slice(0, half).forEach(item => item.deliver());
    return Promise.resolve();
  }
}
class DelayedChannel implements ChannelLike {
  onmessage: ((event: MessageEvent) => void) | null = null;
  constructor(private bus: DelayedBus) {}
  postMessage(message: unknown) {
    for (const other of this.bus.members) if (other !== this) this.bus.queue.push(() => other.onmessage?.({ data: message } as MessageEvent));
  }
  close() { this.bus.members = this.bus.members.filter(item => item !== this); }
}
let counter = 0;
const make = () => `new-${++counter}`;

describe("标签页标识（审查 M1）", () => {
  it("复制标签页：sessionStorage 带着同一个 tabId，后来者换新，原页面不变", async () => {
    const bus = new Bus();
    const first = await claimTabId({ session: storage({ "doc-workbench.tab": "TAB-A" }), make, pageId: "p1", channel: bus.open(), sleep: now });
    const copied = storage({ "doc-workbench.tab": "TAB-A" });
    const second = await claimTabId({ session: copied, make, pageId: "p2", channel: bus.open(), sleep: now });
    expect(first).toBe("TAB-A");
    expect(second).not.toBe("TAB-A");
    expect(copied.data["doc-workbench.tab"]).toBe(second);
  });
  it("刷新：没有其他页面回答，沿用原 tabId", async () => {
    const bus = new Bus();
    expect(await claimTabId({ session: storage({ "doc-workbench.tab": "TAB-R" }), make, pageId: "p3", channel: bus.open(), sleep: now })).toBe("TAB-R");
  });
  it("两个页面同时声明：pageId 小的保留", async () => {
    const bus = new Bus();
    let release!: () => void;
    const gate = new Promise<void>(resolve => { release = resolve; });
    const a = claimTabId({ session: storage({ "doc-workbench.tab": "SAME" }), make, pageId: "a", channel: bus.open(), sleep: () => gate });
    const b = claimTabId({ session: storage({ "doc-workbench.tab": "SAME" }), make, pageId: "b", channel: bus.open(), sleep: () => gate });
    release();
    const [ra, rb] = await Promise.all([a, b]);
    expect(ra).toBe("SAME");
    expect(rb).not.toBe("SAME");
  });
  it("复审：窗口外晚到的“正在使用”也换新，并通知迁移草稿键", async () => {
    const bus = new DelayedBus();
    const holder = new TabIdentity({ session: storage({ "doc-workbench.tab": "LATE" }), make, pageId: "p1", channel: bus.open(), sleep: now });
    await holder.claim();
    bus.flush();
    const copied = storage({ "doc-workbench.tab": "LATE" });
    const late = new TabIdentity({ session: copied, make, pageId: "p0", channel: bus.open(), sleep: now });
    const changes: Array<[string, string]> = [];
    late.onChange((a, b) => changes.push([a, b]));
    expect(await late.claim()).toBe("LATE"); // 窗口内没收到回答（主线程忙）
    bus.flush(); // 声明送达持有者 → 持有者回答 in-use
    bus.flush(); // 回答晚到
    expect(late.current).not.toBe("LATE");
    expect(holder.current).toBe("LATE");
    expect(changes).toEqual([["LATE", late.current]]);
    expect(copied.data["doc-workbench.tab"]).toBe(late.current);
  });
  it("复审：负载下多个复制标签同时启动、消息乱序晚到，最终 tabId 两两不同", async () => {
    for (let round = 0; round < 50; round++) {
      const bus = new DelayedBus(round);
      const pages = Array.from({ length: 6 }, (_, index) =>
        new TabIdentity({ session: storage({ "doc-workbench.tab": "DUP" }), make, pageId: `p${(index * 7 + round) % 6}-${index}`, channel: bus.open(), sleep: () => bus.drainSome() }));
      await Promise.all(pages.map(page => page.claim()));
      for (let i = 0; i < 20 && bus.queue.length; i++) bus.flush();
      // 在线消息兜底：同号不同页面时 pageId 大的换新（与工作台 openPresence 相同的规则）
      for (let pass = 0; pass < 10; pass++) {
        for (const a of pages) for (const b of pages) {
          if (a !== b && sameTabOtherPage({ type: "presence", tabId: b.current, userId: "u", pageId: pageOf(b) }, { tabId: a.current, pageId: pageOf(a) })) a.resolveCollision(pageOf(b));
        }
        for (let i = 0; i < 20 && bus.queue.length; i++) bus.flush();
      }
      expect(new Set(pages.map(page => page.current)).size).toBe(pages.length);
    }
  });
  it("复审：往返缓存恢复后重新声明，期间新开的同号标签已持有时换新", async () => {
    const bus = new Bus();
    const restored = new TabIdentity({ session: storage({ "doc-workbench.tab": "BF" }), make, pageId: "p9", channel: bus.open(), sleep: now });
    await restored.claim();
    const other = new TabIdentity({ session: storage({ "doc-workbench.tab": "BF" }), make, pageId: "p1", channel: null, sleep: now });
    // 冻结期间收不到消息：另一个页面没有经过声明就用上了同一个号
    const channel = bus.open();
    channel.onmessage = event => { const m = event.data as { type: string; tabId: string }; if (m.type === "claim" && m.tabId === other.current) channel.postMessage({ type: "in-use", tabId: other.current, pageId: "p1" }); };
    expect(await restored.claim()).not.toBe("BF");
  });
  it("在线消息撞号判定：同 tabId 不同 pageId 才算", () => {
    expect(sameTabOtherPage({ type: "presence", tabId: "T", userId: "u", pageId: "b" }, { tabId: "T", pageId: "a" })).toBe("b");
    expect(sameTabOtherPage({ type: "presence-reply", tabId: "T", userId: "u", pageId: "a" }, { tabId: "T", pageId: "a" })).toBeNull();
    expect(sameTabOtherPage({ type: "presence", tabId: "U", userId: "u", pageId: "b" }, { tabId: "T", pageId: "a" })).toBeNull();
    expect(sameTabOtherPage({ type: "bye", tabId: "T", userId: "u", pageId: "b" }, { tabId: "T", pageId: "a" })).toBeNull();
    expect(sameTabOtherPage({ type: "presence", tabId: "T", userId: "u" }, { tabId: "T", pageId: "a" })).toBeNull();
  });
  it("没有 BroadcastChannel 时直接用 sessionStorage 的值", async () => {
    expect(await claimTabId({ session: storage({ "doc-workbench.tab": "X" }), make, pageId: "p", channel: null })).toBe("X");
  });
});
