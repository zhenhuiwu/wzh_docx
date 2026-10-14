import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { patchObjectsKeepalive } from "./documents";
import { rememberSaveKey } from "../markdownBlocks";

// QA 复验 14：Ctrl+S 还没返回就继续输入再关页——关页按块保存要带上在途整篇保存的键，服务器据此在它之上重新定基
let bodies: unknown[] = [];
beforeEach(() => {
  bodies = [];
  vi.stubGlobal("window", globalThis);
  vi.stubGlobal("fetch", vi.fn(async (_url, options) => { bodies.push(JSON.parse(options.body)); return new Response('{"data":{}}', { headers: { "content-type": "application/json" } }); }));
});
afterEach(() => vi.unstubAllGlobals());

it("close-save patch carries the keys of this page's earlier saves", async () => {
  await patchObjectsKeepalive("d1", 0, [{ op: "update", objectId: "o1", baseRevision: 0, markdown: "x" }], "close-key-1", ["ctrl-s-key-1"]);
  expect(bodies[0]).toMatchObject({ baseDocumentRevision: 0, clientSaveKey: "close-key-1", afterSaveKeys: ["ctrl-s-key-1"], atomic: true });
});

it("omits afterSaveKeys when there were no earlier saves", async () => {
  await patchObjectsKeepalive("d1", 3, [], "close-key-2");
  expect(bodies[0]).not.toHaveProperty("afterSaveKeys");
});

it("rememberSaveKey keeps the latest keys once each", () => {
  let keys: string[] = [];
  for (const key of ["a", "b", "a", "c"]) keys = rememberSaveKey(keys, key, 2);
  expect(keys).toEqual(["b", "c"]);
});
