/**
 * 光标、载入期间输入写回用的“正文块”序列（按序号在重绘前后对齐）。
 * QA 复验 16 补（F-M1 / 重复段落写回）：只读章节角标、公共内容卡、AI 预览等显示用节点（data-ai-preview）不算正文块。
 * 公共内容卡（aace389 起挂在引用章节后）只在编辑区里有、预取的新版式里没有：算进来时它之后的块序号整体错位，
 * 载入期间打的字按错位的序号写回（写不回或写进别的段），重绘还会冲掉他人刚写进相邻段的内容。
 */
export const CARET_BLOCKS = "p,li,h1,h2,h3,h4,h5,h6,td,th,pre,blockquote,:scope > div";

export function caretBlocks(root: ParentNode): Element[] {
  return [...root.querySelectorAll(CARET_BLOCKS)].filter(block => !block.closest("[data-ai-preview]"));
}

/** 块的正文文字：去掉块内显示用节点（只读章节标题里的“只读：…”角标）的文字。 */
export function blockText(block: Element): string {
  if (!block.querySelector("[data-ai-preview]")) return block.textContent || "";
  const copy = block.cloneNode(true) as Element;
  copy.querySelectorAll("[data-ai-preview]").forEach(node => node.remove());
  return copy.textContent || "";
}
