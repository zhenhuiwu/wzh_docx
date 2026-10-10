/**
 * 标签页标识（审查 M1 / 复审 M1 残留）。
 * sessionStorage 里的 tabId 会被“复制标签页”和 window.open 一并复制，两个标签就共用同一个草稿键。
 * 启动时在 BroadcastChannel 上声明（claim）自己的 tabId；有别的页面回答“正在使用”，就换一个新的。
 * - 200ms 窗口内收到：直接换新再返回。
 * - 窗口之后才收到（主线程忙、负载高时回答晚到）：同样换新，并通知订阅者迁移草稿键（onChange）。
 * - 同时声明（双方都还没定下来）：pageId 较小的保留、较大的换新。
 * - 从往返缓存恢复（pageshow persisted）：重新声明一次（reclaim）。
 * 刷新（reload）时原页面已不存在，没人回答，沿用原 tabId，刷新后仍能找回自己的草稿。
 * pageId 是每次页面加载生成的内存标识，用于草稿“写入者”核对，也随在线消息发出以便事后发现撞号。
 */
import { tabId as storedTabId, type StorageLike } from "./draftStore";

export const TAB_CHANNEL = "doc-workbench.tabs";
export const TAB_KEY = "doc-workbench.tab";

export interface ChannelLike {
  postMessage(message: unknown): void;
  onmessage: ((event: MessageEvent) => void) | null;
  close(): void;
}

interface ClaimMessage { type: "claim" | "in-use"; tabId: string; pageId: string }
type Change = (previous: string, next: string) => void;

export interface TabIdentityOptions {
  session: StorageLike; make: () => string; pageId: string;
  channel: ChannelLike | null; waitMs?: number; sleep?: (ms: number) => Promise<void>;
}

export class TabIdentity {
  current: string;
  private claiming = false;
  private duplicate = false;
  private listeners: Change[] = [];
  constructor(private options: TabIdentityOptions) {
    this.current = storedTabId(options.session, options.make);
    if (options.channel) options.channel.onmessage = event => this.receive(event.data as ClaimMessage);
  }

  /** 声明当前 tabId；返回声明后的值（可能已换新）。channel 在页面存续期间保持打开，以回答后来者的声明。 */
  claim(): Promise<string> {
    const { channel } = this.options;
    if (!channel) return Promise.resolve(this.current);
    this.claiming = true;
    this.duplicate = false;
    channel.postMessage({ type: "claim", tabId: this.current, pageId: this.options.pageId });
    const sleep = this.options.sleep ?? ((ms: number) => new Promise<void>(resolve => setTimeout(resolve, ms)));
    return sleep(this.options.waitMs ?? 200).then(() => {
      this.claiming = false;
      if (this.duplicate) this.renew();
      return this.current;
    });
  }

  /** 在线消息里发现同一 tabId、不同 pageId 的另一个页面：pageId 较大的一方换新。返回是否换了。 */
  resolveCollision(otherPageId: string): boolean {
    if (!otherPageId || otherPageId === this.options.pageId || this.options.pageId < otherPageId) return false;
    this.renew();
    return true;
  }

  onChange(listener: Change): () => void {
    this.listeners.push(listener);
    return () => { this.listeners = this.listeners.filter(item => item !== listener); };
  }

  private receive(message: ClaimMessage) {
    const { pageId, channel } = this.options;
    if (!message || message.pageId === pageId || message.tabId !== this.current) return;
    if (message.type === "claim") {
      // 已定下来的持有者总是回答；同时声明时 pageId 小的回答（保留），大的记为重复
      if (!this.claiming || pageId < message.pageId) channel?.postMessage({ type: "in-use", tabId: this.current, pageId });
      else this.duplicate = true;
    } else if (message.type === "in-use") {
      // 复审 M1：窗口外晚到的“正在使用”同样换新（回答方是持有者，声明方让出）
      if (this.claiming) this.duplicate = true;
      else this.renew();
    }
  }

  private renew() {
    const previous = this.current;
    this.current = this.options.make();
    this.duplicate = false;
    try { this.options.session.setItem(TAB_KEY, this.current); } catch { /* 只影响刷新后找回草稿 */ }
    for (const listener of [...this.listeners]) listener(previous, this.current);
  }
}

/** 兼容旧接口：声明一次并返回 tabId。 */
export function claimTabId(options: TabIdentityOptions): Promise<string> {
  return new TabIdentity(options).claim();
}

export const pageInstance: string = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : String(Math.random());

let identity: TabIdentity | null = null;
let claimed: Promise<string> | null = null;

/** 本页面的标签标识对象（整个页面共用一个）。 */
export function browserTabIdentity(): TabIdentity {
  if (!identity) {
    const channel = typeof BroadcastChannel === "undefined" ? null : new BroadcastChannel(TAB_CHANNEL);
    identity = new TabIdentity({ session: window.sessionStorage, make: () => crypto.randomUUID(), pageId: pageInstance, channel });
  }
  return identity;
}

/** 本页面的标签标识（只声明一次；之后以 browserTabIdentity().current 为准）。 */
export function browserTabId(): Promise<string> {
  if (!claimed) claimed = browserTabIdentity().claim();
  return claimed;
}
