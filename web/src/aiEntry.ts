/** Placement, visibility and leave handling for the floating [AI 编写本节] entry (41 复验 P2). */

export interface EntryAccess {
  /** Object IDs the user cannot edit (A07 not assigned, read-only chapters), keyed like assignInfo.locked. */
  locked: ReadonlyMap<string, string> | ReadonlySet<string>;
  /** Enforced assignments for this writer: only `editable` sections may be written. */
  restricted: boolean;
  editable: ReadonlySet<string>;
  /** Heading object IDs in document order (heading i in the editor = headings[i]). */
  headings: readonly string[];
}

/** Same rule as the outline's "mine" flag plus the locks: no entry on a section the user may not write. */
export function aiEntryAllowed(access: EntryAccess | null | undefined, headingIndex: number | null): boolean {
  if (headingIndex === null) return false;
  if (!access) return true; // ownership not loaded: the server still checks
  const id = access.headings[headingIndex];
  if (!id) return !access.restricted;
  if (access.locked.has(id)) return false;
  return !access.restricted || access.editable.has(id);
}

export interface EntryGeometry {
  paperRight: number;
  /** Left edge of a panel docked on the right (assistant), or null when none is open. */
  panelLeft: number | null;
  viewportWidth: number;
  width: number;
  gap?: number;
}

/** Left position: just right of the paper, but never over the docked panel or off-screen; falls back inside the paper's right margin. */
export function entryLeft({ paperRight, panelLeft, viewportWidth, width, gap = 8 }: EntryGeometry): number {
  const limit = Math.min(panelLeft ?? viewportWidth, viewportWidth) - gap - width;
  const outside = paperRight + gap;
  return outside <= limit ? outside : Math.max(gap, Math.min(limit, paperRight - gap - width));
}

/** After "放弃建议并离开": go back for a browser-back (target is the previous entry), otherwise push. */
export function leaveNavigation(target: string, previous: unknown): "back" | "push" {
  return typeof previous === "string" && previous === target ? "back" : "push";
}
