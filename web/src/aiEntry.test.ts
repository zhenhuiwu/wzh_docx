import { describe, expect, it } from "vitest";
import { aiEntryAllowed, entryLeft, leaveNavigation } from "./aiEntry";

const access = (over: Partial<Parameters<typeof aiEntryAllowed>[0] & object> = {}) => ({
  locked: new Map<string, string>(), restricted: false, editable: new Set<string>(), headings: ["h0", "h1", "h2"], ...over,
});

describe("aiEntryAllowed", () => {
  it("shows the entry everywhere for an unrestricted user", () => {
    expect(aiEntryAllowed(access(), 1)).toBe(true);
    expect(aiEntryAllowed(null, 0)).toBe(true);
    expect(aiEntryAllowed(access(), null)).toBe(false);
  });
  it("hides it on sections a restricted writer is not assigned", () => {
    const a = access({ restricted: true, editable: new Set(["h1"]) });
    expect(aiEntryAllowed(a, 0)).toBe(false);
    expect(aiEntryAllowed(a, 1)).toBe(true);
    expect(aiEntryAllowed(a, 5)).toBe(false);
  });
  it("hides it on locked (read-only chapter / A07 locked) headings even for owners", () => {
    expect(aiEntryAllowed(access({ locked: new Map([["h2", "只读"]]) }), 2)).toBe(false);
    expect(aiEntryAllowed(access({ restricted: true, editable: new Set(["h2"]), locked: new Map([["h2", "只读"]]) }), 2)).toBe(false);
  });
});

describe("entryLeft", () => {
  it("sits right of the paper when there is room", () => {
    expect(entryLeft({ paperRight: 1200, panelLeft: null, viewportWidth: 1920, width: 120 })).toBe(1208);
    expect(entryLeft({ paperRight: 1200, panelLeft: 1540, viewportWidth: 1920, width: 120 })).toBe(1208);
  });
  it("never overlaps the docked assistant panel at 1920x1080", () => {
    const left = entryLeft({ paperRight: 1460, panelLeft: 1540, viewportWidth: 1920, width: 120 });
    expect(left + 120).toBeLessThanOrEqual(1540 - 8);
    expect(left).toBe(1460 - 8 - 120);
  });
  it("stays on screen without a panel", () => {
    expect(entryLeft({ paperRight: 1900, panelLeft: null, viewportWidth: 1920, width: 120 }) + 120).toBeLessThanOrEqual(1912);
  });
});

describe("leaveNavigation", () => {
  it("goes back for a browser-back target, pushes otherwise", () => {
    expect(leaveNavigation("/projects/p", "/projects/p")).toBe("back");
    expect(leaveNavigation("/projects", "/projects/p")).toBe("push");
    expect(leaveNavigation("/projects", null)).toBe("push");
  });
});
