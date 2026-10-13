import { describe, expect, it, vi } from "vitest";
vi.mock("./styles/chapterLock.css", () => ({}));
import { chapterHeadingIds, chapterLockMap, chapterReadonlyMessage, FRAMEWORK_NOTE, SPLIT_NOTE } from "./chapterLocks";
import { classifySaveError } from "./saveModel";
import type { LockedChapter } from "./api/lockedChapters";

const chapters: LockedChapter[] = [
  { key: "运行环境", title: "3.6 运行环境", kind: "framework", headingObjectId: "h1", objectIds: ["b1", "b2"] },
  { key: "任务分解", title: "4 任务分解", kind: "split", headingObjectId: "h2", objectIds: ["t1"] },
  { key: "x", title: "unmapped", kind: "framework", headingObjectId: null, objectIds: [] },
];

describe("chapter locks", () => {
  it("locks heading and body of every server-flagged chapter", () => {
    const map = chapterLockMap(chapters);
    expect([...map.keys()].sort()).toEqual(["b1", "b2", "h1", "h2", "t1"]);
    expect(map.get("b2")).toBe(FRAMEWORK_NOTE);
    expect(map.get("t1")).toBe(SPLIT_NOTE);
    expect([...chapterHeadingIds(chapters).keys()]).toEqual(["h1", "h2"]);
    expect(chapterLockMap([]).size).toBe(0);
  });
  it("CHAPTER_READONLY is not retried nor shown as a revision conflict", () => {
    expect(classifySaveError({ status: 409, code: "CHAPTER_READONLY" })).toBe("assignment");
  });
  it("explains a CHAPTER_READONLY rejection in Chinese", () => {
    const one = chapterReadonlyMessage({ chapters: ["任务分解"] }, chapters);
    expect(one).toContain("“4 任务分解”在此只读");
    expect(one).toContain("任务自动生成");
    expect(chapterReadonlyMessage({ chapters: ["运行环境"] }, chapters)).toContain("分工 › 公共框架");
    expect(chapterReadonlyMessage(undefined)).toContain("只读章节在此只读");
  });
});
