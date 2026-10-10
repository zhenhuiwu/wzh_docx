/**
 * 编辑区 HTML → Markdown（服务器 docx_parser 方言的逆运算）。
 * 依据 01-阶段2设计 §6.0：单元格内换行保持 <br>、单元格内容折成单行、
 * 转换前保护空格与制表符、`|` 与行首转义与服务器一致、表格分隔行规范化。
 */
import TurndownService from "turndown";
// @ts-expect-error The GFM plugin does not publish TypeScript declarations.
import { gfm } from "turndown-plugin-gfm";

export const TAB = "\uE000";
export const SPACE = "\uE001";

const inCell = (node: Node) => Boolean((node as Element).parentElement?.closest?.("td,th"));

/** 与 docx_parser.py:161-163 一致的全局转义（含 |）。 */
export function escapeMarkdownText(text: string): string {
  return text.replace(/([\\`*_{}[\]<>|#])/g, "\\$1");
}

/** 与 docx_parser.py:154-157 一致的普通段落行首转义。 */
export function escapeParagraphStart(content: string): string {
  let text = content
    .replace(/^([\s\uE000\uE001]*\d+)\./, "$1\\.")
    .replace(/^([+-])(?=[\s\uE000\uE001])/, "\\$1");
  if (/^-{3,}$/.test(text)) text = text.replace(/-/g, "\\-");
  return text;
}

/** 单元格内容折成单行，换行一律成为 <br>（docx_parser.py:175）。 */
export function cellInline(content: string): string {
  return content.replace(/^\n+|\n+$/g, "").replace(/[ \t]*\n+[ \t]*/g, "<br>");
}

/** 表格分隔行与服务器写法一致，并还原空白占位符。 */
export function finishMarkdown(markdown: string): string {
  return markdown
    .replace(/^(\|(?: --- \|)+)$/gm, (row) => "|" + "---|".repeat((row.match(/---/g) || []).length))
    .split(TAB).join("\t")
    .split(SPACE).join(" ");
}

export interface StoredImage { ref: string; content_type: string; data: string }

/** 编辑区里的图片是 data: 或 docx-image: 引用；只保留服务器已知的图片。 */
export function storedImageRule(images: () => StoredImage[]): TurndownService.Rule {
  return {
    filter: "img",
    replacement: (_content, node) => {
      const image = node as HTMLImageElement;
      const source = image.getAttribute("src") || "";
      const stored = images().find((item) => source === item.ref || source === `data:${item.content_type};base64,${item.data}`);
      return stored ? `\n\n![${image.getAttribute("alt") || "图片"}](${stored.ref})\n\n` : "";
    },
  };
}

export function createTurndown(imageRule: TurndownService.Rule): TurndownService {
  const t = new TurndownService({ headingStyle: "atx", bulletListMarker: "-" });
  t.use(gfm);
  t.addRule("storedImage", imageRule);
  t.addRule("cellBreak", { filter: (n) => n.nodeName === "BR" && inCell(n), replacement: () => "<br>" });
  t.addRule("tableCellInline", {
    filter: ["th", "td"],
    replacement: (content, node) => {
      const parent = node.parentNode as Node;
      const index = Array.prototype.indexOf.call(parent.childNodes, node);
      return (index === 0 ? "| " : " ") + cellInline(content) + " |";
    },
  });
  t.addRule("plainParagraph", {
    filter: (n) => n.nodeName === "P" && !inCell(n),
    replacement: (content) => `\n\n${escapeParagraphStart(content)}\n\n`,
  });
  t.addRule("cellParagraph", {
    filter: (n) => n.nodeName === "P" && inCell(n),
    replacement: (content) => `\n${content}\n`,
  });
  t.escape = escapeMarkdownText;
  return t;
}

/** 转换前保护空格与制表符；只跳过纯换行的 HTML 序列化空白。不改 U+00A0。 */
export function protectWhitespace(root: HTMLElement): void {
  const walker = root.ownerDocument.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const value = node.nodeValue ?? "";
    if (/^\s*$/.test(value) && value.includes("\n")) continue;
    node.nodeValue = value.replace(/\t/g, TAB).replace(/ /g, SPACE);
  }
}

/** root 必须是副本：会被就地修改。 */
export function editorMarkdown(turndown: TurndownService, root: HTMLElement): string {
  protectWhitespace(root);
  return finishMarkdown(turndown.turndown(root));
}
