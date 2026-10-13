import { describe, expect, it } from "vitest";
import { atScrollEnd, viewHeadingAt } from "./viewHeading";

describe("当前章节指示：滚到文末显示最后一节", () => {
  it("到底判定", () => {
    expect(atScrollEnd(196987 - 1000, 1000, 196987)).toBe(true);
    expect(atScrollEnd(195000, 1000, 196987)).toBe(false);
    expect(atScrollEnd(0, 1000, 800)).toBe(false); // 不需要滚动的短文档
  });
  it("没跳转、滚到底：最后一节（原先停在 478/480）", () => expect(viewHeadingAt(477, 480, null, true)).toBe(479));
  it("刚跳转到 478 节、补留白后恰好到底：仍是跳转目标", () => expect(viewHeadingAt(477, 480, 477, true)).toBe(477));
  it("没到底：按顶边判定；顶边之上没有标题时取第一节", () => {
    expect(viewHeadingAt(12, 480, null, false)).toBe(12);
    expect(viewHeadingAt(null, 480, null, false)).toBe(0);
    expect(viewHeadingAt(null, 0, null, true)).toBe(null);
  });
});
