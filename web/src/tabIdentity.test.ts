import { describe, expect, it } from "vitest";
import { claimTabId, type ChannelLike } from "./tabIdentity";

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
  it("没有 BroadcastChannel 时直接用 sessionStorage 的值", async () => {
    expect(await claimTabId({ session: storage({ "doc-workbench.tab": "X" }), make, pageId: "p", channel: null })).toBe("X");
  });
});
