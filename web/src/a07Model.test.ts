import { describe, expect, it } from "vitest";
import { assignCountText, demoteConfirmText, readonlyText, ROLE_OPTIONS } from "./memberModel";
import { classifySaveError } from "./saveModel";

describe("A07 成员“编写分工”列与 S-5", () => {
  it("章、段为 0 的部分省略，都为 0 显示 —", () => {
    expect(assignCountText({ sections: 3, blocks: 0, documents: 2 })).toEqual({ main: "3 章", docs: "2 篇文档" });
    expect(assignCountText({ sections: 1, blocks: 2, documents: 1 })).toEqual({ main: "1 章 · 2 段", docs: "1 篇文档" });
    expect(assignCountText({ sections: 0, blocks: 0, documents: 0 })).toEqual({ main: "—", docs: "" });
    expect(assignCountText(undefined)).toEqual({ main: "—", docs: "" });
  });
  it("降为只读且有分工才确认", () => {
    const member = { name: "李四", assignments: { sections: 2, blocks: 1, documents: 1 } };
    expect(demoteConfirmText(member, "viewer")?.body).toContain("2 章、1 段将变为未分配");
    expect(demoteConfirmText(member, "owner")).toBeNull();
    expect(demoteConfirmText({ name: "刘洋", assignments: { sections: 0, blocks: 0, documents: 0 } }, "viewer")).toBeNull();
  });
  it("编写人员说明改为只能改分给自己的部分（F-1 恢复）", () => {
    expect(ROLE_OPTIONS.find(item => item.value === "editor")?.hint).toBe("只能修改文档中分配给自己的部分");
    expect(readonlyText("assignment", "editor")).toContain("还没有分给你编写的章节");
  });
});

describe("A07 保存失败分类", () => {
  it("越权、检查不可用、缺 blockIds 都归 assignment（不自动重试）", () => {
    expect(classifySaveError({ status: 409, code: "SECTION_NOT_ASSIGNED" })).toBe("assignment");
    expect(classifySaveError({ status: 409, code: "ASSIGNMENT_CHECK_UNAVAILABLE" })).toBe("assignment");
    expect(classifySaveError({ status: 422, code: "BLOCK_IDS_REQUIRED" })).toBe("assignment");
    expect(classifySaveError({ status: 409, code: "REVISION_CONFLICT" })).toBe("stale");
  });
});
