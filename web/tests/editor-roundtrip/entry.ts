// 浏览器内往返测试入口：与工作台相同的 DOMPurify + editorMarkdown 流程。
import DOMPurify from "dompurify";
import { createTurndown, editorMarkdown, storedImageRule, type StoredImage } from "../../src/editorMarkdown";

declare global { interface Window { roundtrip: (html: string, images: StoredImage[]) => string; editorRoundtrip: (root: HTMLElement, images: StoredImage[]) => string } }

window.editorRoundtrip = (root, images) => {
  const turndown = createTurndown(storedImageRule(() => images));
  const copy = root.cloneNode(true) as HTMLElement;
  return editorMarkdown(turndown, DOMPurify.sanitize(copy.innerHTML, { RETURN_DOM: true }) as HTMLElement);
};
window.roundtrip = (html, images) => {
  for (const image of images) html = html.split(`src="${image.ref}"`).join(`src="data:${image.content_type};base64,${image.data}"`);
  const host = document.createElement("div");
  host.innerHTML = DOMPurify.sanitize(html);
  document.body.replaceChildren(host);
  return window.editorRoundtrip(host, images);
};
