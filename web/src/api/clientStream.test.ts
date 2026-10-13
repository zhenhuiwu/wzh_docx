import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { apiRequest } from "./client";
import { streamDocumentSection } from "./documents";

let stream: ReadableStreamDefaultController<Uint8Array>;
const encode = (text: string) => new TextEncoder().encode(text);
beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("window", globalThis);
  vi.stubGlobal("fetch", vi.fn(async (_url, options) => new Response(new ReadableStream({
    start(controller) {
      stream = controller;
      options.signal.addEventListener("abort", () => controller.error(new DOMException("Aborted", "AbortError")));
    },
  }))));
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

it.each(["section", "document"] as const)("%s keeps streaming past ten minutes with heartbeat activity", async scope => {
  const events = vi.fn();
  const request = streamDocumentSection("test", 1, 0, "", undefined, events, new AbortController().signal, scope);
  await vi.advanceTimersByTimeAsync(0);
  for (let elapsed = 0; elapsed < 720000; elapsed += 15000) {
    await vi.advanceTimersByTimeAsync(15000);
    stream.enqueue(encode("\n"));
    await vi.advanceTimersByTimeAsync(0);
  }
  expect(events).not.toHaveBeenCalled();
  stream.enqueue(encode('{"type":"done","content":"完成","revision":1}\n'));
  stream.close();
  await request;
  expect(events).toHaveBeenCalledOnce();
  expect(vi.getTimerCount()).toBe(0);
});

it("reports stalled streams distinctly and releases the reader", async () => {
  const request = apiRequest("/stream", { onEvent: vi.fn(), timeoutMs: 1000 });
  const rejected = expect(request).rejects.toMatchObject({ code: "REQUEST_TIMEOUT" });
  await vi.advanceTimersByTimeAsync(1001);
  await rejected;
  expect(vi.getTimerCount()).toBe(0);
});

it("stops immediately when the user cancels", async () => {
  const controller = new AbortController();
  const request = apiRequest("/stream", { signal: controller.signal, onEvent: vi.fn() });
  const rejected = expect(request).rejects.toMatchObject({ code: "REQUEST_CANCELLED" });
  await vi.advanceTimersByTimeAsync(0);
  controller.abort();
  await rejected;
  expect(vi.getTimerCount()).toBe(0);
});

it("rejects truncated output without a done event", async () => {
  const request = streamDocumentSection("test", 1, 0, "", undefined, vi.fn(), new AbortController().signal);
  const rejected = expect(request).rejects.toThrow("AI 编写中断");
  await vi.advanceTimersByTimeAsync(0);
  stream.close();
  await rejected;
});
