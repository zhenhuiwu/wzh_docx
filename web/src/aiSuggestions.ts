/**
 * Spec 30 §4: render AI block operations as inline suggestions inside the editor.
 * Insertions are green (`ai-ins`), deletions are struck through (`ai-del`), a
 * replacement is "delete + insert". Each suggestion has ✓ / ✕; nothing is saved
 * until the user accepts and the normal save path runs.
 * Suggestion nodes carry `data-ai-preview`, so serialization ignores them.
 */
import type { BlockOperation } from "./api/documents";
import "./styles/aiInplace.css";

export interface Suggestion {
  op: BlockOperation;
  target: HTMLElement;
  inserted: HTMLElement | null;
  control: HTMLElement;
  resolved: boolean;
}

export interface SuggestionCallbacks {
  sanitize: (html: string) => string;
  onResolve: (suggestion: Suggestion, accepted: boolean) => void;
}

/** Placeholders of unmounted chunks (editorChunks): they stand for headings/blocks that are not in the DOM. */
const CHUNK_ATTR = "data-chunk-ph";
const CHUNK_HEADINGS_ATTR = "data-chunk-headings";
const CHUNK_BLOCKS_ATTR = "data-chunk-blocks";

/** Minimal element shape so the lookups below can be unit-tested without a DOM. */
export interface BlockLike { hasAttribute(name: string): boolean; getAttribute?(name: string): string | null; tagName?: string }

function isPlaceholder(node: BlockLike): boolean { return node.hasAttribute(CHUNK_ATTR); }
function placeholderCount(node: BlockLike, attr: string): number | null {
  const value = Number(node.getAttribute?.(attr) ?? NaN);
  return Number.isInteger(value) && value >= 0 ? value : null;
}

export interface SectionBlocks<T> {
  heading: T | null;
  blocks: T[];
  /** Part of the section (or its heading) is in an unmounted chunk: missing targets are "not loaded", not "out of scope". */
  partial: boolean;
}

/**
 * Top-level editor blocks of section `headingIndex` (heading excluded), and the heading itself.
 * `headingIndex` counts every heading of the document; an unmounted chunk's placeholder counts for the
 * headings it stands for, so the index stays right in chunked (very large) documents. A placeholder of
 * unknown size makes positions after it unknowable: the result is then empty and `partial`.
 * With `headingId` the section is found by its heading's object ID instead of by position.
 */
export function sectionBlocks<T extends BlockLike = HTMLElement>(root: { children: ArrayLike<unknown> }, headingIndex: number, headingId?: string | null): SectionBlocks<T> {
  const children = Array.from(root.children) as T[];
  let count = -1;
  let heading: T | null = null;
  let partial = false;
  let inside = false;
  const blocks: T[] = [];
  for (const node of children) {
    if (isPlaceholder(node)) {
      const headings = placeholderCount(node, CHUNK_HEADINGS_ATTR);
      if (headingId) { if (inside) { partial = true; if (headings !== 0) break; } continue; }
      if (headings === null) { if (inside || count < headingIndex) return { heading, blocks: heading ? blocks : [], partial: true }; continue; }
      if (inside) { partial = true; if (headings > 0) break; continue; }
      if (count < headingIndex && count + headings >= headingIndex) return { heading: null, blocks: [], partial: true };
      count += headings;
      continue;
    }
    if (node.hasAttribute("data-ai-preview")) continue;
    if (/^H[1-6]$/.test(node.tagName || "")) {
      count += 1;
      if (inside) break;
      if (headingId ? node.getAttribute?.("data-object-id") === headingId : count === headingIndex) { heading = node; inside = true; }
      continue;
    }
    if (inside) blocks.push(node);
  }
  if (headingId && !heading) partial = children.some(isPlaceholder);
  return { heading, blocks, partial };
}

/** A Word heading kept as a paragraph (e.g. "1.1 标识"), marked by the server preview. */
export function isWordHeading(node: Element): boolean {
  return node.hasAttribute("data-word-heading");
}

function button(label: string, title: string, className: string, action: () => void): HTMLButtonElement {
  const element = document.createElement("button");
  element.type = "button";
  element.className = className;
  element.textContent = label;
  element.title = title;
  element.setAttribute("aria-label", title);
  element.addEventListener("mousedown", event => event.preventDefault());
  element.addEventListener("click", (event) => { event.preventDefault(); event.stopPropagation(); action(); });
  return element;
}

/**
 * Draw operations under the section. Operations whose block is not in the
 * section (or that would replace/delete the locked heading) are dropped and
 * counted, as required by spec 30 §4.4.
 */
export function renderSuggestions(root: HTMLElement, headingIndex: number, operations: BlockOperation[], callbacks: SuggestionCallbacks, scopeHeadingId?: string | null): { suggestions: Suggestion[]; dropped: number; unloaded: number } {
  const { heading, blocks, partial } = sectionBlocks<HTMLElement>(root, headingIndex, scopeHeadingId);
  const byId = new Map<string, HTMLElement>();
  for (const block of blocks) {
    const id = block.getAttribute("data-object-id");
    if (id && !byId.has(id)) byId.set(id, block);
  }
  const headingId = heading?.getAttribute("data-object-id") || null;
  const lastAfter = new Map<HTMLElement, HTMLElement>();
  const suggestions: Suggestion[] = [];
  let dropped = 0;
  let unloaded = 0;
  for (const op of operations) {
    const isHeading = headingId !== null && op.blockId === headingId;
    const target = isHeading ? heading : byId.get(op.blockId);
    // Real headings and Word heading paragraphs (data-word-heading) are locked: only insertAfter anchors.
    // Located by block object ID only; a target in an unmounted chunk is reported, never guessed by position.
    if (!target && partial) { unloaded += 1; continue; }
    if (!target || ((isHeading || isWordHeading(target)) && op.op !== "insertAfter")) { dropped += 1; continue; }
    const control = document.createElement("div");
    control.setAttribute("data-ai-preview", "");
    control.contentEditable = "false";
    control.className = "ai-suggestion";
    let inserted: HTMLElement | null = null;
    if (op.op !== "delete") {
      inserted = document.createElement("div");
      inserted.className = "ai-ins";
      inserted.innerHTML = callbacks.sanitize(op.html);
      control.append(inserted);
    } else {
      // The ✓/✕ of a deletion sit on the right of the struck-through paragraph itself.
      control.className = "ai-suggestion ai-suggestion-inline";
    }
    if (op.op !== "insertAfter") target.classList.add("ai-del");
    const suggestion: Suggestion = { op, target, inserted, control, resolved: false };
    const actions = document.createElement("div");
    actions.className = "ai-suggestion-actions";
    actions.append(
      button("✓", "接受这一处", "ghost sm", () => resolveSuggestion(suggestion, true, callbacks)),
      button("✕", "拒绝这一处", "subtle sm", () => resolveSuggestion(suggestion, false, callbacks)),
    );
    control.append(actions);
    if (op.op === "delete") {
      target.append(control);
    } else {
      const anchor = lastAfter.get(target) ?? target;
      anchor.after(control);
      lastAfter.set(target, control);
    }
    suggestions.push(suggestion);
  }
  return { suggestions, dropped, unloaded };
}

/** Accept writes the suggestion into the editor as ordinary content; reject restores the original. */
export function resolveSuggestion(suggestion: Suggestion, accepted: boolean, callbacks: SuggestionCallbacks): void {
  if (suggestion.resolved) return;
  suggestion.resolved = true;
  const { op, target, inserted, control } = suggestion;
  if (accepted && inserted) {
    const nodes = [...inserted.childNodes].filter(node => node.nodeType === Node.ELEMENT_NODE || node.textContent?.trim()) as ChildNode[];
    // A replacement keeps the block ID on its first block so the normal blockIds save path maps it.
    const id = op.op === "replace" ? target.getAttribute("data-object-id") : null;
    const first = nodes[0];
    if (id && first instanceof HTMLElement) first.setAttribute("data-object-id", id);
    const template = formatTemplate(op, target);
    if (template) for (const node of nodes) if (node instanceof HTMLElement && node.tagName === template.tagName) copyFormat(template, node);
    control.replaceWith(...nodes);
  } else {
    control.remove();
  }
  if (op.op !== "insertAfter") {
    if (accepted) target.remove();
    else target.classList.remove("ai-del");
  }
  callbacks.onResolve(suggestion, accepted);
}

/** The body block whose Word formatting an accepted suggestion takes over (P2-9: keep indent and gutter). */
function formatTemplate(op: BlockOperation, target: HTMLElement): HTMLElement | null {
  if (op.op === "replace") return target;
  if (/^H[1-6]$/.test(target.tagName) || isWordHeading(target)) {
    let next = target.nextElementSibling as HTMLElement | null;
    while (next && (next.hasAttribute("data-ai-preview") || isWordHeading(next))) next = next.nextElementSibling as HTMLElement | null;
    return next && !/^H[1-6]$/.test(next.tagName) ? next : null;
  }
  return target;
}

function copyFormat(from: HTMLElement, to: HTMLElement) {
  const style = from.getAttribute("style");
  if (style && !to.getAttribute("style")) to.setAttribute("style", style);
  const mine = from.getAttribute("data-mine");
  if (mine !== null) to.setAttribute("data-mine", mine);
}

export function pending(suggestions: Suggestion[]): Suggestion[] {
  return suggestions.filter(item => !item.resolved);
}

/**
 * Spec 30 §4.2–4.3: show the writing scope. Scope blocks get a 2px brand frame,
 * everything else is faded and inert; headings get a lock badge. All markers are
 * classes/attributes removed by clearScope, plus a `data-ai-preview` label that
 * serialization ignores.
 */
export function markScope(root: HTMLElement, headingIndex: number, denied: Set<string>, label: string): number {
  clearScope(root);
  const { heading, blocks } = sectionBlocks<HTMLElement>(root, headingIndex);
  const scope = blocks.filter(block => !isWordHeading(block) && !denied.has(block.getAttribute("data-object-id") || ""));
  root.classList.add("ai-scoping");
  for (const node of root.querySelectorAll<HTMLElement>(":scope > h1, :scope > h2, :scope > h3, :scope > h4, :scope > h5, :scope > h6")) {
    node.setAttribute("data-ai-lock", node === heading ? "标题已固定" : "");
    if (node === heading) appendLock(node);
  }
  for (const node of blocks) if (isWordHeading(node)) { node.setAttribute("data-ai-lock", "标题已固定"); appendLock(node); }
  for (const block of scope) block.classList.add("ai-scope");
  // Everything outside the writing scope is faded and inert (spec 30 §4.2).
  for (const node of [...root.children] as HTMLElement[]) {
    const lockedInSection = isWordHeading(node) && blocks.includes(node);
    if (node !== heading && !lockedInSection && !node.hasAttribute("data-ai-preview") && !node.classList.contains("ai-scope")) node.classList.add("ai-out");
  }
  if (scope[0]) scope[0].classList.add("ai-scope-first");
  scope.at(-1)?.classList.add("ai-scope-last");
  const tag = document.createElement("div");
  tag.setAttribute("data-ai-preview", "");
  tag.setAttribute("data-ai-scope-label", "");
  tag.contentEditable = "false";
  tag.className = "ai-scope-label";
  tag.textContent = label;
  // The label opens the scope right under the locked heading, above any suggestion anchored there.
  if (heading) heading.after(tag);
  else scope[0]?.before(tag);
  return scope.length;
}

const LOCK_SVG = '<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="11" width="14" height="9" rx="1.5" /><path d="M8 11V8a4 4 0 0 1 8 0v3" /></svg>';

/** Lock badge after the heading text (same icon as the outline); "标题已固定" is its tooltip. */
function appendLock(node: HTMLElement) {
  const lock = document.createElement("span");
  lock.setAttribute("data-ai-preview", "");
  lock.contentEditable = "false";
  lock.className = "ai-lock";
  lock.title = "标题已固定";
  lock.setAttribute("role", "img");
  lock.setAttribute("aria-label", "标题已固定");
  lock.innerHTML = LOCK_SVG;
  node.append(lock);
}

export function clearScope(root: HTMLElement): void {
  root.classList.remove("ai-scoping");
  root.querySelectorAll(".ai-lock").forEach(node => node.remove());
  root.querySelectorAll("[data-ai-scope-label]").forEach(node => node.remove());
  for (const node of root.querySelectorAll<HTMLElement>("[data-ai-lock]")) node.removeAttribute("data-ai-lock");
  for (const node of root.querySelectorAll<HTMLElement>(".ai-scope, .ai-out")) {
    node.classList.remove("ai-scope", "ai-scope-first", "ai-scope-last", "ai-out");
    if (!node.className) node.removeAttribute("class");
  }
}

/** Number of section blocks the user may change (for the panel header). */
export function scopeCount(root: HTMLElement, headingIndex: number, denied: Set<string>): number {
  return sectionBlocks<HTMLElement>(root, headingIndex).blocks.filter(block => !isWordHeading(block) && !denied.has(block.getAttribute("data-object-id") || "")).length;
}

/**
 * Top-level body blocks in document order, skipping nodes the editor injects
 * (AI suggestions, scope labels, public reference cards — all `data-ai-preview`).
 * Positional lookups ("第 N 段", server clientIndex) must count only these.
 * Chunk placeholders are skipped too: in a chunked document use the full block list (ChunkedBody.allBlocks).
 */
export function contentBlocks<T extends BlockLike>(children: ArrayLike<T>): T[] {
  return Array.from(children).filter(block => !block.hasAttribute("data-ai-preview"));
}

/**
 * The body block at a server-side clientIndex (which counts body blocks of the whole document).
 * Placeholders count for the blocks they stand for; an index inside (or after an unknown-size) unmounted
 * chunk returns undefined instead of a wrong block.
 */
export function contentBlockAt<T extends BlockLike>(children: ArrayLike<T>, index: number): T | undefined {
  if (!Number.isInteger(index) || index < 0) return undefined;
  let at = 0;
  for (const node of Array.from(children)) {
    if (isPlaceholder(node)) {
      const size = placeholderCount(node, CHUNK_BLOCKS_ATTR);
      if (size === null || index < at + size) return undefined;
      at += size;
      continue;
    }
    if (node.hasAttribute("data-ai-preview")) continue;
    if (at === index) return node;
    at += 1;
  }
  return undefined;
}

/** Full-document block source of a chunked editor (ChunkedBody). */
export interface ChunkSource<T> { allBlocks(): Array<{ node: T }>; reveal(node: T): boolean }

/** clientIndex lookup that also reaches unmounted chunks: the block is mounted before it is returned. */
export function bodyBlockAt<T extends BlockLike>(children: ArrayLike<T>, index: number, chunks?: ChunkSource<T> | null): T | undefined {
  if (!chunks) return contentBlockAt(children, index);
  if (!Number.isInteger(index) || index < 0) return undefined;
  const node = chunks.allBlocks()[index]?.node;
  if (node) chunks.reveal(node);
  return node;
}

/** Element shape for object-ID lookups (a top-level block or something inside it carries data-object-id). */
export interface IdBlock extends BlockLike { querySelector(selector: string): unknown }

/** A block of a chunked editor by object ID, mounting its chunk when needed; null when the ID is not in the document. */
export function chunkBlockById<T extends IdBlock>(chunks: ChunkSource<T> | null | undefined, id: string): T | null {
  if (!chunks || !id) return null;
  const selector = `[data-object-id="${id.replace(/["\\]/g, "\\$&")}"]`;
  const found = chunks.allBlocks().find(({ node }) => node.getAttribute?.("data-object-id") === id || Boolean(node.querySelector(selector)))?.node ?? null;
  if (found) chunks.reveal(found);
  return found;
}
