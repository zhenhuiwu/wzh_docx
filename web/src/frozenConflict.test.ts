import { describe, expect, it } from "vitest";
import { continuedChars, frozenMark, looksLikePinyin, nextSeq, noteYields, substituteFrozen } from "./frozenConflict";

describe("frozenConflict", () => {
  it("substitutes frozen placeholders with the server version", () => {
    const md = `# 标题\n\n前一段\n\n${frozenMark(0)}\n\n后一段`;
    expect(substituteFrozen(md, [{ mark: frozenMark(0), theirs: "对方的版本" }])).toBe("# 标题\n\n前一段\n\n对方的版本\n\n后一段");
    expect(substituteFrozen(md, [{ mark: frozenMark(0), theirs: "对方\n\n两段" }])).toBe("# 标题\n\n前一段\n\n对方\n\n两段\n\n后一段");
    // 对方删了这一段：占位连同空行去掉
    expect(substituteFrozen(md, [{ mark: frozenMark(0), theirs: "" }])).toBe("# 标题\n\n前一段\n\n后一段");
    expect(substituteFrozen(`${frozenMark(1)}\n\n尾`, [{ mark: frozenMark(1), theirs: "" }])).toBe("尾");
  });
  it("counts continued characters", () => {
    expect(continuedChars("我的前半句", "我的前半句，后半句接着写完。")).toBe(9);
    expect(continuedChars("一样", "一样")).toBe(0);
    expect(continuedChars("段。【丙012", "段。【丙0123456789ABCDEFGHIJ丁】")).toBe(19);
  });
  it("detects pinyin typed after losing focus", () => {
    expect(looksLikePinyin("wojiezhexie", true)).toBe(true);
    expect(looksLikePinyin("wojiezhexie", false)).toBe(false);
    expect(looksLikePinyin("abc 1", true)).toBe(false);
  });
  it("orders notes by priority", () => {
    expect(noteYields("caret", true, "others")).toBe(true);
    expect(noteYields("", true, "others")).toBe(true);
    expect(noteYields("others", true, "")).toBe(false);
    expect(noteYields("caret", false, "others")).toBe(false);
    expect(noteYields("caret", true, "caret")).toBe(false);
  });
  it("快照序号单调递增（审查点 2）", () => {
    expect(nextSeq(0, 1000)).toBe(1000);
    expect(nextSeq(1000, 1000)).toBe(1001);
    expect(nextSeq(5000, 1000)).toBe(5001);
  });
});
