/**
 * 30 §3.3 / 验收 P0-2：长文档正文按章节分段挂载。
 * 顶层块按标题切成若干段（chunk）；只有视口附近的段在 DOM 里，其余换成同高、不可编辑的占位块。
 * 卸载的段保留原来的 DOM 节点（编辑过的内容不会丢），取正文时用节点生成 Markdown（按段缓存）。
 * 占位块带 data-ai-preview：所有不认识分段的取正文路径都会把它去掉——所以取整篇正文必须走 inflate()/markdownOf()。
 */
export const CHUNK_ATTR = "data-chunk-ph";
const HEADING = /^H[1-6]$/;

export interface Chunk {
  nodes: Element[];
  placeholder: HTMLElement | null;
  /** 本段第一个标题在全文标题中的序号；headingCount 为本段标题数 */
  headingStart: number;
  headingCount: number;
  height: number;
  markdown: string | null;
}

export interface ChunkOptions {
  /** 每段至少多少块才在下一个标题处切开 */
  minBlocks?: number;
  /** 没有标题时，每段最多多少块 */
  maxBlocks?: number;
  /** 段挂上后调用（补锁、公共卡片、冲突标记等只作用于在 DOM 里的块） */
  onMount?: (chunk: Chunk) => void;
  /** 段换成占位前调用 */
  onUnmount?: (chunk: Chunk) => void;
}

/** 估算一块的高度（像素）：每 40 字一行、每行 28px，另加段距。只用于从未显示过的段。 */
export function estimateHeight(node: Element): number {
  const text = (node.textContent || "").length;
  if (HEADING.test(node.tagName)) return 48;
  if (node.tagName === "TABLE") return 40 + 32 * node.querySelectorAll("tr").length;
  return 12 + 28 * Math.max(1, Math.ceil(text / 40));
}

/** 把顶层块切成段：在标题处切（本段已有 minBlocks 块时），或者块数到 maxBlocks 时硬切。 */
export function splitChunks(blocks: Element[], minBlocks = 24, maxBlocks = 80): Element[][] {
  const out: Element[][] = [];
  let current: Element[] = [];
  for (const block of blocks) {
    if (current.length && ((HEADING.test(block.tagName) && current.length >= minBlocks) || current.length >= maxBlocks)) {
      out.push(current);
      current = [];
    }
    current.push(block);
  }
  if (current.length) out.push(current);
  return out;
}

export class ChunkedBody {
  readonly chunks: Chunk[] = [];
  private observer: IntersectionObserver | null = null;
  private headingOrder = new WeakMap<Element, number>();
  private scrollHandler: (() => void) | null = null;
  private frame = 0;
  /** 原始节点 → 段序号（resync 用来认出 DOM 里的块属于哪一段） */
  private owner = new WeakMap<Element, number>();
  /** 本次挂载后输入过的段：不再卸载，保证浏览器撤销栈引用的节点一直在 DOM 里 */
  private edited = new Set<number>();
  active = true;

  constructor(private root: HTMLElement, private scroller: HTMLElement | null, private options: ChunkOptions = {}) {}

  /** 切段并只保留 keep 里的段（默认前两段）。必须在 root.innerHTML 刚写好、浏览器还没布局时调用。 */
  build(keep: Set<number> = new Set([0, 1])) {
    const blocks = ([...this.root.children] as Element[]).filter(node => !node.hasAttribute("data-ai-preview"));
    let headings = 0;
    for (const group of splitChunks(blocks, this.options.minBlocks, this.options.maxBlocks)) {
      const start = headings;
      for (const node of group) {
        this.owner.set(node, this.chunks.length);
        if (HEADING.test(node.tagName)) this.headingOrder.set(node, headings++);
      }
      this.chunks.push({ nodes: group, placeholder: null, headingStart: start, headingCount: headings - start, height: group.reduce((sum, node) => sum + estimateHeight(node), 0), markdown: null });
    }
    this.chunks.forEach((chunk, index) => { if (!keep.has(index)) this.detach(chunk, false); });
    if (typeof IntersectionObserver !== "undefined") {
      this.observer = new IntersectionObserver(entries => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          const index = Number((entry.target as HTMLElement).getAttribute(CHUNK_ATTR));
          if (this.chunks[index]?.placeholder) this.mount(index);
        }
      }, { root: this.scroller, rootMargin: "150% 0px" });
      for (const chunk of this.chunks) if (chunk.placeholder) this.observer.observe(chunk.placeholder);
    }
    if (this.scroller) {
      this.scrollHandler = () => {
        if (this.frame) return;
        this.frame = requestAnimationFrame(() => { this.frame = 0; this.trim(); });
      };
      this.scroller.addEventListener("scroll", this.scrollHandler, { passive: true });
    }
  }

  get mountedIndexes(): Set<number> {
    return new Set(this.chunks.flatMap((chunk, index) => chunk.placeholder ? [] : [index]));
  }
  get mountedBlockCount(): number {
    return this.chunks.reduce((sum, chunk) => sum + (chunk.placeholder ? 0 : chunk.nodes.length), 0);
  }

  private detach(chunk: Chunk, measure: boolean) {
    if (chunk.placeholder || !chunk.nodes.length) return;
    const first = chunk.nodes[0], last = chunk.nodes[chunk.nodes.length - 1];
    if (measure) {
      const top = first.getBoundingClientRect().top, bottom = last.getBoundingClientRect().bottom;
      if (bottom > top) chunk.height = bottom - top;
    }
    this.options.onUnmount?.(chunk);
    const placeholder = this.root.ownerDocument.createElement("div");
    placeholder.setAttribute(CHUNK_ATTR, String(this.chunks.indexOf(chunk)));
    placeholder.setAttribute("data-ai-preview", "");
    placeholder.setAttribute("contenteditable", "false");
    placeholder.setAttribute("aria-hidden", "true");
    placeholder.className = "chunk-placeholder";
    placeholder.style.height = `${Math.round(chunk.height)}px`;
    first.before(placeholder);
    for (const node of chunk.nodes) {
      // 卸载时的嵌入宿主（公共卡片等）不属于正文，挂上时由 onMount 重放
      const next = node.nextElementSibling;
      if (next?.hasAttribute("data-public-embed")) next.remove();
      node.remove();
    }
    chunk.placeholder = placeholder;
    chunk.markdown = null;
    this.observer?.observe(placeholder);
  }

  mount(index: number) {
    const chunk = this.chunks[index];
    if (!chunk?.placeholder) return;
    this.observer?.unobserve(chunk.placeholder);
    chunk.placeholder.replaceWith(...chunk.nodes);
    chunk.placeholder = null;
    this.options.onMount?.(chunk);
  }

  /** 卸载离视口很远、且不含光标/焦点的段 */
  trim() {
    if (!this.active || !this.scroller) return;
    this.resync();
    const view = this.scroller.getBoundingClientRect();
    const margin = Math.max(2400, view.height * 3);
    const selection = this.root.ownerDocument.getSelection();
    const anchor = selection?.rangeCount ? selection.getRangeAt(0) : null;
    this.chunks.forEach((chunk, index) => {
      if (chunk.placeholder || !chunk.nodes.length || this.edited.has(index)) return;
      const top = chunk.nodes[0].getBoundingClientRect().top;
      const bottom = chunk.nodes[chunk.nodes.length - 1].getBoundingClientRect().bottom;
      if (bottom > view.top - margin && top < view.bottom + margin) return;
      if (anchor && chunk.nodes.some(node => anchor.intersectsNode(node))) return;
      if (chunk.nodes.some(node => node.contains(this.root.ownerDocument.activeElement))) return;
      this.detach(chunk, true);
    });
  }

  /**
   * 编辑会增删顶层块（回车拆段、删段、粘贴、撤销）：按 DOM 现状重算已挂载段的成员与全文标题序号。
   * 新块归到它前面最近的段；已被删掉的块从段里去掉——否则卸载时新块会留在原处、再挂上时删掉的块又回来。
   */
  resync() {
    if (!this.chunks.length) return;
    const members = new Map<number, Element[]>();
    let current = 0;
    for (const node of [...this.root.children]) {
      const ph = node.getAttribute(CHUNK_ATTR);
      // 占位后面的新块属于下一段（占位本身不可编辑，新块不会长在它里面）
      if (ph !== null) { current = Number(ph) + 1; continue; }
      if (node.hasAttribute("data-ai-preview")) continue;
      const own = this.owner.get(node);
      if (own !== undefined && !this.chunks[own].placeholder) current = Math.max(current, own);
      if (!this.chunks[current] || this.chunks[current].placeholder) continue;
      this.owner.set(node, current);
      const list = members.get(current) ?? [];
      list.push(node);
      members.set(current, list);
    }
    let headings = 0;
    this.chunks.forEach((chunk, index) => {
      if (!chunk.placeholder) chunk.nodes = members.get(index) ?? [];
      chunk.headingStart = headings;
      for (const node of chunk.nodes) if (HEADING.test(node.tagName)) this.headingOrder.set(node, headings++);
      chunk.headingCount = headings - chunk.headingStart;
    });
  }

  /** 输入发生在 node 所在的段：该段从此不卸载（撤销/重做始终作用在 DOM 里的节点上） */
  markEdited(node: Node | null) {
    if (!node) return;
    this.resync();
    const at = this.chunks.findIndex(chunk => !chunk.placeholder && chunk.nodes.some(block => block === node || block.contains(node)));
    if (at >= 0) this.edited.add(at);
  }
  isEdited(index: number) { return this.edited.has(index); }

  /** 全文所有顶层块（含未挂载段里的），按文档顺序；chunk 为所在段 */
  allBlocks(): Array<{ node: Element; chunk: number }> {
    this.resync();
    return this.chunks.flatMap((chunk, index) => chunk.nodes.map(node => ({ node, chunk: index })));
  }

  /** 挂上 node 所在的段并返回它（node 已不在任何段里时为 false） */
  reveal(node: Element): boolean {
    const at = this.chunks.findIndex(chunk => chunk.nodes.includes(node));
    if (at < 0) return false;
    this.mount(at);
    return true;
  }

  mountAll() {
    this.chunks.forEach((_, index) => this.mount(index));
  }

  /** 第 index 个标题（全文序号）所在的元素；所在段没挂上时先挂上。 */
  headingElement(index: number): HTMLElement | null {
    this.resync();
    const at = this.chunks.findIndex(chunk => index >= chunk.headingStart && index < chunk.headingStart + chunk.headingCount);
    if (at < 0) return null;
    this.mount(at);
    return (this.chunks[at].nodes.find(node => this.headingOrder.get(node) === index) as HTMLElement | undefined) ?? null;
  }

  /** 元素对应的全文标题序号（分段后 DOM 里的标题不全，不能按 querySelectorAll 的位置算） */
  headingIndexOf(node: Element): number | undefined {
    return this.headingOrder.get(node);
  }

  /** 位置 node 所属章节的全文标题序号（在第一个标题之前为 -1；不在任何已挂载段里为 undefined） */
  headingIndexAt(node: Node): number | undefined {
    this.resync();
    for (const chunk of this.chunks) {
      if (chunk.placeholder) continue;
      const at = chunk.nodes.findIndex(block => block === node || block.contains(node));
      if (at < 0) continue;
      for (let i = at; i >= 0; i--) {
        const index = this.headingOrder.get(chunk.nodes[i]);
        if (index !== undefined) return index;
      }
      return chunk.headingStart - 1;
    }
    return undefined;
  }

  /**
   * 视口里“当前章节”：顶部参考线 line（clientY）以上最后一个标题的全文序号。
   * 参考线落在占位块里时，取该段最后一个标题（没挂上的段也能算对）；整篇在第一个标题之前为 null。
   */
  headingAtLine(line: number): number | null {
    this.resync();
    let found: number | null = null;
    for (const chunk of this.chunks) {
      if (chunk.placeholder) {
        if (chunk.placeholder.getBoundingClientRect().top > line) break;
        if (chunk.headingCount) found = chunk.headingStart + chunk.headingCount - 1;
        continue;
      }
      for (const node of chunk.nodes) {
        const index = this.headingOrder.get(node);
        if (index === undefined) continue;
        if (node.getBoundingClientRect().top > line) return found;
        found = index;
      }
    }
    return found;
  }

  /** 已挂载的标题元素及其全文序号 */
  mountedHeadings(): Array<{ element: HTMLElement; index: number }> {
    this.resync();
    return this.chunks.flatMap(chunk => chunk.placeholder ? [] : chunk.nodes.flatMap(node => {
      const index = this.headingOrder.get(node);
      return index === undefined ? [] : [{ element: node as HTMLElement, index }];
    }));
  }

  /** 卸载段的 Markdown（节点不在 DOM 里就不会被编辑，算一次缓存到再次挂上） */
  markdownOf(index: number, convert: (nodes: Element[]) => string): string {
    const chunk = this.chunks[index];
    if (chunk.markdown === null) chunk.markdown = convert(chunk.nodes);
    return chunk.markdown;
  }

  /** 在 root 的副本里把占位换回节点副本，得到整篇（用于整篇转换与块 ID 对照） */
  inflate(copy: HTMLElement) {
    this.resync();
    for (const placeholder of [...copy.querySelectorAll<HTMLElement>(`:scope > [${CHUNK_ATTR}]`)]) {
      const chunk = this.chunks[Number(placeholder.getAttribute(CHUNK_ATTR))];
      placeholder.replaceWith(...(chunk?.nodes.map(node => node.cloneNode(true)) ?? []));
    }
  }

  destroy(mountAll = false) {
    if (mountAll) this.mountAll();
    this.active = false;
    this.observer?.disconnect();
    this.observer = null;
    if (this.scroller && this.scrollHandler) this.scroller.removeEventListener("scroll", this.scrollHandler);
    if (this.frame) cancelAnimationFrame(this.frame);
  }
}

/** P0-2：版式预览还没到时先显示的正文（只读）：按 Markdown 粗排标题与段落，不依赖服务端。 */
export function quickBodyHtml(markdown: string): string {
  const escape = (text: string) => text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return markdown.split(/\n{2,}/).map(block => block.trim()).filter(Boolean).map(block => {
    const heading = /^(#{1,6})\s+(.+)$/.exec(block);
    if (heading && !block.includes("\n")) return `<h${heading[1].length}>${escape(heading[2].replace(/\s+#*\s*$/, ""))}</h${heading[1].length}>`;
    return `<p>${escape(block).replace(/\n/g, "<br>")}</p>`;
  }).join("");
}

export interface SearchHit {
  /** 块在 allBlocks() 里的位置 */
  block: number;
  /** 匹配在块文字里的起点与长度 */
  offset: number;
  length: number;
  /** 所属章节的全文标题序号（第一个标题之前为 -1） */
  heading: number;
  snippet: { before: string; match: string; after: string };
}

/**
 * 全文搜索（Ctrl+F 替代）：在每个顶层块的文字里找 query（不区分大小写），按文档顺序返回最多 limit 处。
 * texts[i] 为第 i 块的文字，headings[i] 为第 i 块所属章节序号。
 */
export function searchBlocks(texts: string[], headings: number[], query: string, limit = 500): { hits: SearchHit[]; total: number } {
  const needle = query.trim().toLocaleLowerCase();
  const hits: SearchHit[] = [];
  let total = 0;
  if (!needle) return { hits, total };
  texts.forEach((text, block) => {
    const hay = text.toLocaleLowerCase();
    for (let at = hay.indexOf(needle); at >= 0; at = hay.indexOf(needle, at + needle.length)) {
      total += 1;
      if (hits.length >= limit) continue;
      hits.push({ block, offset: at, length: needle.length, heading: headings[block] ?? -1, snippet: {
        before: (at > 24 ? "…" : "") + text.slice(Math.max(0, at - 24), at),
        match: text.slice(at, at + needle.length),
        after: text.slice(at + needle.length, at + needle.length + 40) + (at + needle.length + 40 < text.length ? "…" : ""),
      } });
    }
  });
  return { hits, total };
}

/** 块 root 里第 offset 个字符起、长 length 的文字范围（跨多个文本节点也行） */
export function textRange(root: Node, offset: number, length: number): Range | null {
  const doc = root.ownerDocument;
  if (!doc) return null;
  const walker = doc.createTreeWalker(root, 4 /* NodeFilter.SHOW_TEXT */);
  const range = doc.createRange();
  let seen = 0, started = false;
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const size = node.textContent?.length ?? 0;
    if (!started && offset < seen + size) { range.setStart(node, offset - seen); started = true; }
    if (started && offset + length <= seen + size) { range.setEnd(node, offset + length - seen); return range; }
    seen += size;
  }
  return null;
}
