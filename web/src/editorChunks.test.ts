import { describe, expect, it } from "vitest";
import { quickBodyHtml, splitChunks } from "./editorChunks";

const el = (tagName: string) => ({ tagName }) as unknown as Element;

describe("splitChunks", () => {
  it("splits at headings once a chunk has enough blocks, and hard-splits long runs", () => {
    const blocks = [el("H1"), el("P"), el("P"), el("H2"), el("P"), el("H2"), el("P"), el("P"), el("P"), el("P"), el("P")];
    expect(splitChunks(blocks, 2, 4).map(chunk => chunk.map(node => node.tagName).join(","))).toEqual(["H1,P,P", "H2,P", "H2,P,P,P", "P,P"]);
  });
  it("keeps every block exactly once", () => {
    const blocks = Array.from({ length: 1800 }, (_, i) => el(i % 5 === 0 ? "H2" : "P"));
    const chunks = splitChunks(blocks);
    expect(chunks.flat()).toHaveLength(1800);
    expect(chunks.length).toBeGreaterThan(20);
  });
});

describe("quickBodyHtml", () => {
  it("renders headings and escaped paragraphs", () => {
    expect(quickBodyHtml("# 1 范围\n\n本系统 <b>a</b>\n第二行\n\n## 1.1 标识 ##")).toBe("<h1>1 范围</h1><p>本系统 &lt;b&gt;a&lt;/b&gt;<br>第二行</p><h2>1.1 标识</h2>");
  });
});
