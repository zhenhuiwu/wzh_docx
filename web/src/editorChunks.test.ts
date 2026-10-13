import { describe, expect, it } from "vitest";
import { CHUNK_ATTR, ChunkedBody, fastBodyHtml, quickBodyHtml, searchBlocks, splitChunks } from "./editorChunks";

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

// ---------- 极简 DOM（vitest 运行在 node 环境，没有 jsdom）：只实现 ChunkedBody 用到的接口 ----------
class FakeEl {
  parent: FakeEl | null = null;
  kids: FakeEl[] = [];
  attrs = new Map<string, string>();
  style: Record<string, string> = {};
  className = "";
  top = 0;
  constructor(public tagName: string, public text = "", public doc: FakeDoc | null = null) {}
  get ownerDocument() { return this.doc; }
  get children() { return this.kids; }
  get textContent(): string { return this.text + this.kids.map(k => k.textContent).join(""); }
  get isConnected(): boolean { return this.parent ? this.parent.isConnected : this.tagName === "ROOT"; }
  get nextElementSibling(): FakeEl | null { const p = this.parent; return p ? p.kids[p.kids.indexOf(this) + 1] ?? null : null; }
  getAttribute(name: string) { return this.attrs.get(name) ?? null; }
  hasAttribute(name: string) { return this.attrs.has(name); }
  setAttribute(name: string, value: string) { this.attrs.set(name, value); }
  contains(node: unknown): boolean { return node === this || this.kids.some(k => k.contains(node)); }
  querySelectorAll() { return []; }
  getBoundingClientRect() { return { top: this.top, bottom: this.top + 10, height: 10 }; }
  remove() { if (this.parent) { this.parent.kids.splice(this.parent.kids.indexOf(this), 1); this.parent = null; } }
  before(node: FakeEl) { const p = this.parent!; node.remove(); node.parent = p; p.kids.splice(p.kids.indexOf(this), 0, node); }
  after(node: FakeEl) { const p = this.parent!; node.remove(); node.parent = p; p.kids.splice(p.kids.indexOf(this) + 1, 0, node); }
  replaceWith(...nodes: FakeEl[]) { const p = this.parent!; const at = p.kids.indexOf(this); this.remove(); nodes.forEach((n, i) => { n.remove(); n.parent = p; p.kids.splice(at + i, 0, n); }); }
  append(node: FakeEl) { node.remove(); node.parent = this; this.kids.push(node); }
}
class FakeDoc {
  activeElement: unknown = null;
  createElement(tag: string) { return new FakeEl(tag.toUpperCase(), "", this); }
  getSelection() { return null; }
}
function fakeBody(tags: string[]) {
  const doc = new FakeDoc();
  const root = new FakeEl("ROOT", "", doc);
  tags.forEach((tag, i) => root.append(new FakeEl(tag, `${tag}${i}`, doc)));
  const scroller = { getBoundingClientRect: () => ({ top: 0, bottom: 1000, height: 1000 }), addEventListener() {}, removeEventListener() {} };
  return { doc, root, scroller };
}
const text = (root: FakeEl) => root.kids.map(k => k.hasAttribute(CHUNK_ATTR) ? `[${k.getAttribute(CHUNK_ATTR)}]` : k.textContent).join(" ");
const far = (body: ChunkedBody) => body.chunks.forEach(chunk => chunk.nodes.forEach(n => { (n as unknown as FakeEl).top = 99999; }));

describe("ChunkedBody resync (编辑后卸载/挂载不串段)", () => {
  // 3 段：H1 P P | H2 P P | H3 P P（minBlocks=3）
  const tags = ["H1", "P", "P", "H2", "P", "P", "H3", "P", "P"];
  function setup() {
    const { doc, root, scroller } = fakeBody(tags);
    const body = new ChunkedBody(root as unknown as HTMLElement, scroller as unknown as HTMLElement, { minBlocks: 3 });
    body.build(new Set([0, 1, 2]));
    return { doc, root, body };
  }
  it("新拆出来的段落跟着所在段卸载、挂上，删掉的段落不会回来", () => {
    const { doc, root, body } = setup();
    const added = new FakeEl("P", "新段", doc);
    root.kids[4].after(added); // 第 2 段里回车拆出一段
    root.kids[6].remove();     // 删掉原来的 P5
    far(body);
    body.trim();
    expect(text(root)).toBe("[0] [1] [2]");
    body.mountAll();
    expect(text(root)).toBe("H10 P1 P2 H23 P4 新段 H36 P7 P8");
  });
  it("新标题会重排全文标题序号", () => {
    const { doc, root, body } = setup();
    root.kids[1].after(new FakeEl("H2", "新标题", doc));
    expect(body.mountedHeadings().map(h => h.index)).toEqual([0, 1, 2, 3]);
    expect(body.chunks.map(c => [c.headingStart, c.headingCount])).toEqual([[0, 2], [2, 1], [3, 1]]);
  });
  it("输入过的段不再卸载（撤销栈引用的节点留在 DOM 里），其余段照常卸载", () => {
    const { root, body } = setup();
    body.markEdited(root.kids[4] as unknown as Node);
    far(body);
    body.trim();
    expect(text(root)).toBe("[0] H23 P4 P5 [2]");
    expect(body.isEdited(1)).toBe(true);
  });
  it("搜索结果在未挂载段里：reveal 先挂上该段", () => {
    const { root, body } = setup();
    far(body);
    body.trim();
    const blocks = body.allBlocks();
    expect(blocks.map(b => (b.node as unknown as FakeEl).text)).toEqual(tags.map((t, i) => `${t}${i}`));
    const target = blocks[7].node;
    expect(target.isConnected).toBe(false);
    expect(body.reveal(target)).toBe(true);
    expect(target.isConnected).toBe(true);
    expect(text(root)).toBe("[0] [1] H36 P7 P8");
  });
  it("参考线落在占位里时，当前章节取该段最后一个标题", () => {
    const { root, body } = setup();
    far(body);
    body.trim();
    root.kids.forEach((ph, i) => { ph.top = i * 500; });
    expect(body.headingAtLine(700)).toBe(1);
    expect(body.headingAtLine(-1)).toBe(null);
  });
});

describe("searchBlocks", () => {
  it("按文档顺序找出全部匹配，不区分大小写，带章节和上下文", () => {
    const { hits, total } = searchBlocks(["引言", "系统 API 与 api", "其他"], [0, 0, 1], "Api");
    expect(total).toBe(2);
    expect(hits.map(h => [h.block, h.offset, h.heading])).toEqual([[1, 3, 0], [1, 9, 0]]);
    expect(hits[0].snippet).toEqual({ before: "系统 ", match: "API", after: " 与 api" });
  });
  it("超过上限只列前 limit 处但计总数；空查询无结果", () => {
    const r = searchBlocks(["aaaa"], [0], "a", 2);
    expect([r.hits.length, r.total]).toEqual([2, 4]);
    expect(searchBlocks(["x"], [0], "  ").total).toBe(0);
  });
});

describe("fastBodyHtml", () => {
  it("每块带对象 ID，相邻同类列表合并，Word 标题加标记", () => {
    expect(fastBodyHtml([
      { objectId: "a", revision: 1, html: "<h2>1 范围</h2>" },
      { objectId: "b", revision: 2, html: "<p>标识</p>", wordHeadingLevel: 2 },
      { objectId: "c", revision: 1, html: "<ul>\n<li>一</li>\n</ul>" },
      { objectId: "d", revision: 1, html: "<ul>\n<li>二</li>\n</ul>" },
      { objectId: "e", revision: 1, html: "<ol>\n<li>三</li>\n</ol>" },
      { objectId: "f\"", revision: 1, html: "<table><tr><td>x</td></tr></table>" },
    ])).toBe('<h2 data-object-id="a" data-object-revision="1">1 范围</h2>'
      + '<p data-object-id="b" data-object-revision="2" data-word-heading="2">标识</p>'
      + '<ul><li data-object-id="c" data-object-revision="1">一</li><li data-object-id="d" data-object-revision="1">二</li></ul>'
      + '<ol><li data-object-id="e" data-object-revision="1">三</li></ol>'
      + '<table data-object-id="f&quot;" data-object-revision="1"><tr><td>x</td></tr></table>');
  });
});
