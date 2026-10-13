import { describe, expect, it } from "vitest";
import { contentBlockAt, contentBlocks } from "./aiSuggestions";

const block = (name: string, injected = false) => ({ name, hasAttribute: (attr: string) => injected && attr === "data-ai-preview" });

describe("positional block counting skips injected cards (40 P2-6)", () => {
  const children = [block("h1"), block("card", true), block("p1"), block("label", true), block("p2")];

  it("keeps only body blocks in order", () => {
    expect(contentBlocks(children).map(item => item.name)).toEqual(["h1", "p1", "p2"]);
  });

  it("maps a server clientIndex past public reference cards", () => {
    expect(contentBlockAt(children, 1)?.name).toBe("p1");
    expect(contentBlockAt(children, 2)?.name).toBe("p2");
    expect(contentBlockAt(children, 3)).toBeUndefined();
    expect(contentBlockAt(children, -1)).toBeUndefined();
    expect(contentBlockAt(children, 1.5)).toBeUndefined();
  });
});
