/**
 * 标签页标识（审查 M1）。
 * sessionStorage 里的 tabId 会被“复制标签页”和 window.open 一并复制，两个标签就共用同一个草稿键。
 * 启动时在 BroadcastChannel 上声明（claim）自己的 tabId；200ms 内有别的页面回答“正在使用”，就换一个新的。
 * 刷新（reload）时原页面已不存在，没人回答，沿用原 tabId，刷新后仍能找回自己的草稿。
 * pageId 是每次页面加载生成的内存标识，用于草稿“写入者”核对（删除前确认是自己写的那份）。
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

/**
 * 声明 tabId，发现重复就重新生成并写回 sessionStorage。channel 在页面存续期间保持打开，以回答后来者的声明。
 * 两个页面同时声明同一个 tabId 时，pageId 较小的保留、较大的换新。
 */
export function claimTabId(options: {
  session: StorageLike; make: () => string; pageId: string;
  channel: ChannelLike | null; waitMs?: number; sleep?: (ms: number) => Promise<void>;
}): Promise<string> {
  const { session, make, pageId, channel } = options;
  let current = storedTabId(session, make);
  if (!channel) return Promise.resolve(current);
  let settled = false;
  let duplicate = false;
  channel.onmessage = (event: MessageEvent) => {
    const message = event.data as ClaimMessage;
    if (!message || message.pageId === pageId || message.tabId !== current) return;
    if (message.type === "claim") {
      if (settled || pageId < message.pageId) channel.postMessage({ type: "in-use", tabId: current, pageId });
      else duplicate = true;
    } else if (message.type === "in-use" && !settled) {
      duplicate = true;
    }
  };
  channel.postMessage({ type: "claim", tabId: current, pageId });
  const sleep = options.sleep ?? ((ms: number) => new Promise<void>(resolve => setTimeout(resolve, ms)));
  return sleep(options.waitMs ?? 200).then(() => {
    if (duplicate) {
      current = make();
      try { session.setItem(TAB_KEY, current); } catch { /* 只影响刷新后找回草稿 */ }
    }
    settled = true;
    return current;
  });
}

export const pageInstance: string = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : String(Math.random());

let identity: Promise<string> | null = null;

/** 本页面的标签标识（只声明一次）。 */
export function browserTabId(): Promise<string> {
  if (!identity) {
    const channel = typeof BroadcastChannel === "undefined" ? null : new BroadcastChannel(TAB_CHANNEL);
    identity = claimTabId({ session: window.sessionStorage, make: () => crypto.randomUUID(), pageId: pageInstance, channel });
  }
  return identity;
}
