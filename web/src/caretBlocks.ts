/**
 * 光标、载入期间输入写回用的“正文块”序列（按序号在重绘前后对齐）。
 * QA 复验 16 补（F-M1 / 重复段落写回）：只读章节角标、公共内容卡、AI 预览等显示用节点（data-ai-preview）不算正文块。
 * 公共内容卡（aace389 起挂在引用章节后）只在编辑区里有、预取的新版式里没有：算进来时它之后的块序号整体错位，
 * 载入期间打的字按错位的序号写回（写不回或写进别的段），重绘还会冲掉他人刚写进相邻段的内容。
 */
export const CARET_BLOCKS = "p,li,h1,h2,h3,h4,h5,h6,td,th,pre,blockquote,:scope > div";

export function caretBlocks(root: ParentNode): Element[] {
  return [...root.querySelectorAll(CARET_BLOCKS)].filter(block => !block.closest("[data-ai-preview]") && !wrapsParagraphs(block));
}

/**
 * QA 复验 16 补 F-M4：原稿版式（/preview）的单元格里还有一层段落（td > p[data-word-cell-paragraph]），快速版式（/preview/blocks）没有。
 * 单元格和它里面的段落都算块时，同一文字出现两次、两版块数也不同，重绘后对不上光标所在单元格——光标被移走，接着打的字“没有写入”。
 * 单元格里有段落时只算里面的段落（光标本来就在段落里），两种版式的块序列一致。
 */
function wrapsParagraphs(block: Element): boolean {
  return (block.tagName === "TD" || block.tagName === "TH") && block.querySelector("p,li,h1,h2,h3,h4,h5,h6,pre,blockquote") !== null;
}

/** 块的正文文字：去掉块内显示用节点（只读章节标题里的“只读：…”角标）的文字。 */
export function blockText(block: Element): string {
  if (!block.querySelector("[data-ai-preview]")) return block.textContent || "";
  const copy = block.cloneNode(true) as Element;
  copy.querySelectorAll("[data-ai-preview]").forEach(node => node.remove());
  return copy.textContent || "";
}
