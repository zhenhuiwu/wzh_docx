/**
 * Read-only chapters (public framework references, the dev plan's 任务分解).
 * The server lists them per document with object IDs (GET /documents/{id}/locked-chapters, same rule as the
 * CHAPTER_READONLY save guard); the editor locks the heading and the body and shows a short note on the heading.
 */
import type { LockedChapter } from "./api/lockedChapters";
import "./styles/chapterLock.css";

export const FRAMEWORK_NOTE = "公共框架章节，此处只读，请在“分工 › 公共框架”中修改";
export const SPLIT_NOTE = "由任务自动生成，此处只读，请在“分工”页维护任务";
export const CHAPTER_LOCK_LABEL = "只读";

export function chapterNote(kind: LockedChapter["kind"]): string {
  return kind === "split" ? SPLIT_NOTE : FRAMEWORK_NOTE;
}

/** objectId → note, for the heading and every body block of each locked chapter. */
export function chapterLockMap(chapters: readonly LockedChapter[]): Map<string, string> {
  const out = new Map<string, string>();
  for (const chapter of chapters) {
    const note = chapterNote(chapter.kind);
    if (chapter.headingObjectId) out.set(chapter.headingObjectId, note);
    for (const id of chapter.objectIds) out.set(id, note);
  }
  return out;
}

/** Heading object IDs that get the lock icon and note. */
export function chapterHeadingIds(chapters: readonly LockedChapter[]): Map<string, string> {
  return new Map(chapters.filter(c => c.headingObjectId).map(c => [c.headingObjectId as string, chapterNote(c.kind)]));
}

/** Readable message for a save rejected with CHAPTER_READONLY (details.chapters are chapter keys). */
export function chapterReadonlyMessage(details: Record<string, unknown> | undefined, chapters: readonly LockedChapter[] = []): string {
  const keys = Array.isArray(details?.chapters) ? details!.chapters.filter((k): k is string => typeof k === "string") : [];
  const titles = keys.map(key => chapters.find(c => c.key === key)?.title || key);
  const split = keys.some(key => chapters.find(c => c.key === key)?.kind === "split") || keys.some(key => key.includes("任务分解"));
  const named = titles.length ? `“${titles.join("”“")}”` : "只读章节";
  const where = split && titles.length === 1 ? "任务分解由“分工”页的任务自动生成" : "公共框架请在“分工 › 公共框架”中修改，任务分解由任务自动生成";
  return `这次修改没有保存：${named}在此只读（${where}）。请撤销对该章节的改动后再保存，你的修改已保留在本机。`;
}

const LOCK_SVG = '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="11" width="14" height="9" rx="1.5" /><path d="M8 11V8a4 4 0 0 1 8 0v3" /></svg>';

/** Add (once) the lock icon + note inside each locked heading. Nodes carry data-ai-preview so they never reach the saved text. */
export function decorateChapterLocks(root: HTMLElement, headings: ReadonlyMap<string, string>): void {
  for (const badge of root.querySelectorAll<HTMLElement>(".chapter-lock")) {
    const host = badge.closest("[data-object-id]");
    if (!host || !headings.has(host.getAttribute("data-object-id") || "")) badge.remove();
  }
  for (const [id, note] of headings) {
    const node = root.querySelector<HTMLElement>(`[data-object-id="${CSS.escape(id)}"]`);
    if (!node || node.querySelector(":scope > .chapter-lock")) continue;
    const badge = document.createElement("span");
    badge.className = "chapter-lock";
    badge.setAttribute("data-ai-preview", "");
    badge.contentEditable = "false";
    badge.title = note;
    badge.innerHTML = `${LOCK_SVG}<span class="chapter-lock-note">${CHAPTER_LOCK_LABEL}：${note}</span>`;
    node.append(badge);
  }
}
