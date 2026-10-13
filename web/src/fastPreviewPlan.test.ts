import { describe, expect, it } from "vitest";
import { fastPreviewStep } from "./fastPreviewPlan";

const fast = (documentRevision: number, extra: Partial<{ objectModel: boolean; blocks: unknown[] }> = {}) =>
  ({ documentRevision, objectModel: true, blocks: [{}], ...extra });

describe("fastPreviewStep（关页后重开：用新修订的快速预览进入可编辑，不等整篇预览）", () => {
  it("修订一致直接挂上", () => expect(fastPreviewStep(fast(3), 3, 0)).toBe("mount"));
  it("关页保存已落库、快速预览比正文新：取新正文后挂快速预览（不是 full）", () => {
    expect(fastPreviewStep(fast(4), 3, 0)).toBe("adoptNewer");
  });
  it("快速预览比正文旧：重新取快速预览", () => expect(fastPreviewStep(fast(2), 3, 0)).toBe("refetchBlocks"));
  it("快速预览请求失败：先重试一次，再退回整篇", () => {
    expect(fastPreviewStep(null, 3, 0)).toBe("refetchBlocks");
    expect(fastPreviewStep(null, 3, 1)).toBe("full");
  });
  it("一直追不上（期间连续有人提交）才退回整篇", () => expect(fastPreviewStep(fast(5), 3, 2)).toBe("full"));
  it("对象模型不可用或无块：整篇", () => {
    expect(fastPreviewStep(fast(3, { objectModel: false }), 3, 0)).toBe("full");
    expect(fastPreviewStep(fast(3, { blocks: [] }), 3, 0)).toBe("full");
  });
});
