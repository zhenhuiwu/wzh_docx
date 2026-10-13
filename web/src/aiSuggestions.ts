/**
 * Spec 30 §4: render AI block operations as inline suggestions inside the editor.
 * Insertions are green (`ai-ins`), deletions are struck through (`ai-del`), a
 * replacement is "delete + insert". Each suggestion has ✓ / ✕; nothing is saved
 * until the user accepts and the normal save path runs.
 * Suggestion nodes carry `data-ai-preview`, so serialization ignores them.
 */
import type { BlockOperation } from "./api/documents";

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

/** Top-level editor blocks of section `headingIndex` (heading excluded), and the heading itself. */
export function sectionBlocks(root: HTMLElement, headingIndex: number): { heading: HTMLElement | null; blocks: HTMLElement[] } {
  let count = -1;
  let heading: HTMLElement | null = null;
  const blocks: HTMLElement[] = [];
  for (const node of [...root.children] as HTMLElement[]) {
    if (node.hasAttribute("data-ai-preview")) continue;
    if (/^H[1-6]$/.test(node.tagName)) {
      count += 1;
      if (count === headingIndex) heading = node;
      continue;
    }
    if (count === headingIndex) blocks.push(node);
  }
  return { heading, blocks };
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
export function renderSuggestions(root: HTMLElement, headingIndex: number, operations: BlockOperation[], callbacks: SuggestionCallbacks): { suggestions: Suggestion[]; dropped: number } {
  const { heading, blocks } = sectionBlocks(root, headingIndex);
  const byId = new Map<string, HTMLElement>();
  for (const block of blocks) {
    const id = block.getAttribute("data-object-id");
    if (id && !byId.has(id)) byId.set(id, block);
  }
  const headingId = heading?.getAttribute("data-object-id") || null;
  const lastAfter = new Map<HTMLElement, HTMLElement>();
  const suggestions: Suggestion[] = [];
  let dropped = 0;
  for (const op of operations) {
    const isHeading = headingId !== null && op.blockId === headingId;
    const target = isHeading ? heading : byId.get(op.blockId);
    if (!target || (isHeading && op.op !== "insertAfter")) { dropped += 1; continue; }
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
      const note = document.createElement("span");
      note.className = "ai-suggestion-note";
      note.textContent = "建议删除上一段";
      control.append(note);
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
    const anchor = lastAfter.get(target) ?? target;
    anchor.after(control);
    lastAfter.set(target, control);
    suggestions.push(suggestion);
  }
  return { suggestions, dropped };
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

export function pending(suggestions: Suggestion[]): Suggestion[] {
  return suggestions.filter(item => !item.resolved);
}
