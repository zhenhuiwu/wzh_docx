import { describe, expect, it } from "vitest";
import { bodyBlockAt, chunkBlockById, contentBlockAt, sectionBlocks } from "./aiSuggestions";

/** Fake top-level node: tag, attributes, nested object IDs (for querySelector). */
type Fake = { name: string; tagName: string; attrs: Record<string, string>; inner: string[]; hasAttribute(n: string): boolean; getAttribute(n: string): string | null; querySelector(s: string): unknown };
function node(name: string, tagName = "P", attrs: Record<string, string> = {}, inner: string[] = []): Fake {
  return { name, tagName, attrs, inner, hasAttribute: n => n in attrs, getAttribute: n => attrs[n] ?? null,
    querySelector: s => inner.find(id => s === `[data-object-id="${id}"]`) ?? null };
}
const h = (n: string) => node(n, "H1", { "data-object-id": n });
const p = (n: string) => node(n, "P", { "data-object-id": n });
const ph = (headings: number | null, blocks: number | null) => node("ph", "DIV", { "data-chunk-ph": "0", "data-ai-preview": "",
  ...(headings === null ? {} : { "data-chunk-headings": String(headings) }), ...(blocks === null ? {} : { "data-chunk-blocks": String(blocks) }) });
const names = (list: Fake[]) => list.map(item => item.name);

// Full document: h0 a b | h1 c | h2 d e | h3 f — chunk 1 (h1 c h2 d) unmounted.
const full = [h("h0"), p("a"), p("b"), h("h1"), p("c"), h("h2"), p("d"), p("e"), h("h3"), p("f")];
const chunked = { children: [h("h0"), p("a"), p("b"), ph(2, 4), p("e"), h("h3"), p("f")] };

describe("AI section lookup in a chunked document (40 复验 N6)", () => {
  it("counts headings inside unmounted chunks so later sections are not shifted", () => {
    const s = sectionBlocks<Fake>(chunked, 3);
    expect(s.heading?.name).toBe("h3");
    expect(names(s.blocks)).toEqual(["f"]);
    expect(s.partial).toBe(false);
    expect(names(sectionBlocks<Fake>({ children: full }, 3).blocks)).toEqual(["f"]);
  });
  it("a section whose heading is unmounted is empty and partial, never another section", () => {
    const s = sectionBlocks<Fake>(chunked, 2);
    expect(s.heading).toBeNull();
    expect(s.blocks).toEqual([]);
    expect(s.partial).toBe(true);
  });
  it("a section running into an unmounted chunk is partial", () => {
    const doc = { children: [h("h0"), p("a"), ph(0, 3), h("h1"), p("x")] };
    const s = sectionBlocks<Fake>(doc, 0);
    expect(names(s.blocks)).toEqual(["a"]);
    expect(s.partial).toBe(true);
  });
  it("finds the section by heading object ID regardless of position", () => {
    const s = sectionBlocks<Fake>(chunked, 99, "h3");
    expect(s.heading?.name).toBe("h3");
    expect(names(s.blocks)).toEqual(["f"]);
    expect(sectionBlocks<Fake>(chunked, 0, "h2").partial).toBe(true);
  });
  it("refuses positional lookup past a placeholder of unknown size", () => {
    const doc = { children: [h("h0"), p("a"), ph(null, null), h("hx"), p("y")] };
    expect(sectionBlocks<Fake>(doc, 1)).toMatchObject({ heading: null, blocks: [], partial: true });
    expect(names(sectionBlocks<Fake>(doc, 0).blocks)).toEqual(["a"]);
  });
});

describe("clientIndex / object ID lookup in a chunked document", () => {
  it("contentBlockAt counts placeholder blocks and never returns a wrong block", () => {
    expect(contentBlockAt(chunked.children, 2)?.name).toBe("b");
    expect(contentBlockAt(chunked.children, 4)).toBeUndefined(); // inside the unmounted chunk
    expect(contentBlockAt(chunked.children, 7)?.name).toBe("e");
    expect(contentBlockAt(chunked.children, 9)?.name).toBe("f");
    expect(contentBlockAt([p("a"), ph(1, null), p("z")], 2)).toBeUndefined();
  });
  const source = () => {
    const revealed: string[] = [];
    return { revealed, allBlocks: () => full.map(n => ({ node: n })), reveal: (n: Fake) => { revealed.push(n.name); return true; } };
  };
  it("bodyBlockAt uses the whole document and mounts the target's chunk", () => {
    const chunks = source();
    expect(bodyBlockAt(chunked.children, 4, chunks)?.name).toBe("c");
    expect(chunks.revealed).toEqual(["c"]);
    expect(bodyBlockAt(chunked.children, 99, chunks)).toBeUndefined();
    expect(bodyBlockAt(full, 4, null)?.name).toBe("c");
  });
  it("chunkBlockById reaches unmounted blocks (own or nested ID) and handles missing IDs", () => {
    const chunks = source();
    expect(chunkBlockById(chunks, "d")?.name).toBe("d");
    const nested = { revealed: [] as string[], allBlocks: () => [{ node: node("ul", "UL", {}, ["li-1"]) }], reveal: () => true };
    expect(chunkBlockById(nested, "li-1")?.name).toBe("ul");
    expect(chunkBlockById(chunks, "gone")).toBeNull();
    expect(chunkBlockById(null, "d")).toBeNull();
    expect(chunks.revealed).toEqual(["d"]);
  });
});
