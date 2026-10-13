import { describe, expect, it } from "vitest";
import {
  addButtonText, assignmentParts, batchResultText, candidateNote, changeOutcomeText, classifyMemberError, contactNames,
  blockedProjectLabel, disableBlockedArchivedHint, disableBlockedMoreText, disableBlockedText, isLockedOwner, memberErrorText, membersConflictText, membersNote, ownersText,
  noActiveOwnerText, pickError, readonlyBlockedText, retryableIds, readonlyText, recentChange, removeConfirmText, roleLabel, roleOptionDisabled,
} from "./memberModel";

const owner = (name: string, status = "active", isPrimary = false) => ({ id: name, name, status, isPrimary });

describe("负责人显示", () => {
  it("最多 2 位，之后加“等 N 人”；停用的不显示", () => {
    expect(ownersText([owner("张工", "active", true)])).toBe("张工");
    expect(ownersText([owner("张工"), owner("李工")])).toBe("张工、李工");
    expect(ownersText([owner("张工"), owner("李工"), owner("王工")])).toBe("张工、李工等 3 人");
    expect(ownersText([owner("张工"), owner("停用", "disabled"), owner("李工")])).toBe("张工、李工");
    expect(ownersText([], "—")).toBe("—");
  });
  it("联系人最多 2 位加“等”", () => {
    expect(contactNames([owner("张工"), owner("李工"), owner("王工")])).toBe("张工、李工等");
    expect(contactNames([owner("张工")])).toBe("张工");
  });
});

describe("区块说明", () => {
  it("按身份给出不同说明", () => {
    expect(membersNote({ canManage: true, projectRole: "owner", isAdmin: false })).toBe("负责人可以添加成员、调整权限。");
    expect(membersNote({ canManage: true, projectRole: null, isAdmin: true })).toContain("系统管理员身份");
    expect(membersNote({ canManage: false, projectRole: "auditor", isAdmin: false })).toBe("你以审计身份只读查看该项目。");
    expect(membersNote({ canManage: false, projectRole: "editor", isAdmin: false, owners: [owner("张工"), owner("李工")] }))
      .toBe("如需调整权限，请联系负责人 张工、李工。");
    expect(membersNote({ canManage: false, projectRole: "viewer", isAdmin: false, owners: [] })).toBe("如需调整权限，请联系系统管理员。");
  });
});

describe("最后负责人保护", () => {
  it("唯一启用负责人锁住降级和移除；停用负责人不锁", () => {
    expect(isLockedOwner({ role: "owner", userStatus: "active" }, 1)).toBe(true);
    expect(isLockedOwner({ role: "owner", userStatus: "active" }, 2)).toBe(false);
    expect(isLockedOwner({ role: "owner", userStatus: "disabled" }, 1)).toBe(false);
    expect(isLockedOwner({ role: "editor", userStatus: "active" }, 1)).toBe(false);
    expect(roleOptionDisabled({ role: "owner", userStatus: "active" }, 1, "viewer")).toBe(true);
    expect(roleOptionDisabled({ role: "owner", userStatus: "active" }, 1, "owner")).toBe(false);
  });
});

describe("角色与时间", () => {
  it("角色名，reviewer 显示为审核人员（只读）", () => {
    expect(roleLabel("viewer")).toBe("只读成员");
    expect(roleLabel("reviewer")).toBe("审核人员（只读）");
    expect(roleLabel(null)).toBe("—");
    expect(candidateNote("editor")).toBe("已是编写人员");
    expect(candidateNote(null)).toBe("");
  });
  it("最近调整：添加 / 调整 / 旧数据“—”", () => {
    const fmt = (v: string) => v.slice(5, 10);
    expect(recentChange({ addedAt: "2026-10-09T01:00:00Z", updatedAt: "2026-10-09T01:00:00Z", updatedBy: { id: "1", name: "张工" } }, fmt))
      .toEqual({ time: "10-09", who: "张工 添加" });
    expect(recentChange({ addedAt: "2026-10-09T01:00:00Z", updatedAt: "2026-10-10T01:00:00Z", updatedBy: { id: "1", name: "李工" } }, fmt))
      .toEqual({ time: "10-10", who: "李工 调整" });
    expect(recentChange({ addedAt: null, updatedAt: null, updatedBy: null }, fmt)).toEqual({ time: "—", who: "" });
  });
});

describe("分工数（A07 钩子）", () => {
  it("0 的部分省略，全 0 为空", () => {
    expect(assignmentParts({ sections: 3, blocks: 1, documents: 2 })).toBe("3 章、1 段");
    expect(assignmentParts({ sections: 0, blocks: 2, documents: 1 })).toBe("2 段");
    expect(assignmentParts({ sections: 0, blocks: 0, documents: 0 })).toBe("");
  });
  it("移除确认只在有分工时追加 warning", () => {
    expect(removeConfirmText({ name: "王测", assignments: { sections: 0, blocks: 0, documents: 0 } }).warning).toBe("");
    expect(removeConfirmText({ name: "王测", assignments: { sections: 3, blocks: 1, documents: 2 } }).warning)
      .toBe("王测 在 2 篇文档中负责的 3 章、1 段将变为未分配，只有负责人能修改。");
    expect(removeConfirmText({ name: "王测" }).body).toContain("王测 不能再打开该项目");
  });
  it("成功提示：主负责人改指、分工清空", () => {
    expect(changeOutcomeText("remove", "张工", null, { ownerChanged: { to: { name: "李工" } } })).toBe("已将 张工 移出项目。主负责人改为 李工。");
    expect(changeOutcomeText("role", "王测", "viewer", { assignmentsCleared: { sections: 1, blocks: 0, documents: 1 } }))
      .toBe("已把 王测 改为只读成员。1 章变为未分配。");
    expect(changeOutcomeText("role", "王测", "viewer", { ownerChanged: null, assignmentsCleared: { sections: 0, blocks: 0, documents: 0 } })).toBe("");
  });
});

describe("错误", () => {
  it("按状态码和错误码归类", () => {
    expect(classifyMemberError({ status: 409, code: "REVISION_CONFLICT" })).toBe("conflict");
    expect(classifyMemberError({ status: 409, code: "PROJECT_ARCHIVED" })).toBe("archived");
    expect(classifyMemberError({ status: 409, code: "LAST_OWNER" })).toBe("last-owner");
    expect(classifyMemberError({ status: 422, code: "USER_DISABLED" })).toBe("disabled");
    expect(classifyMemberError({ status: 403, code: "FORBIDDEN" })).toBe("forbidden");
    expect(classifyMemberError({ status: 404 })).toBe("gone");
    expect(classifyMemberError({ status: 404, code: "NOT_FOUND" })).toBe("gone");
    expect(classifyMemberError({ status: 404, code: "MEMBER_NOT_FOUND" })).toBe("member-gone");
    expect(classifyMemberError({ status: 0 })).toBe("network");
  });
  it("文案与 16 §2.6 一致", () => {
    expect(memberErrorText({ status: 409, code: "LAST_OWNER" }, "张工")).toBe("项目至少需要一位启用中的负责人。请先把其他成员设为负责人。");
    expect(memberErrorText({ status: 422, code: "USER_DISABLED" }, "赵工", true)).toBe("赵工 的账号已停用，不能加入项目。");
    expect(memberErrorText({ status: 422, code: "USER_DISABLED" }, "赵工")).toBe("赵工 的账号已停用，不能调整权限；可以移除。");
    expect(memberErrorText({ status: 403, details: { ownerName: "张工" } }, "x")).toBe("你在该项目中没有此操作权限，如需开通请联系负责人 张工。");
    expect(memberErrorText({ status: 0 }, "x")).toBe("暂时不能连接服务，修改没有保存。");
    expect(memberErrorText({ status: 404, code: "MEMBER_NOT_FOUND" }, "李工")).toBe("李工 已不在项目中，已显示最新名单。如需加入请使用“添加成员”。");
    expect(memberErrorText({ status: 409, code: "PROJECT_ARCHIVED" }, "x")).toBe("项目已归档，成员和权限不能调整。恢复项目后才能修改。");
  });
  it("并发提示带最后调整人和时间", () => {
    const now = new Date(2026, 9, 11, 15, 0);
    const at = new Date(2026, 9, 11, 14, 32).toISOString();
    expect(membersConflictText({ lastActor: { name: "李工" }, lastUpdatedAt: at }, now)).toBe("成员名单刚被 李工 于 14:32 调整过，已显示最新名单，请确认后再修改。");
    expect(membersConflictText(undefined, now)).toBe("成员名单刚被调整过，已显示最新名单，请确认后再修改。");
  });
});

describe("添加弹窗", () => {
  it("按钮、选择上限、部分失败", () => {
    expect(addButtonText(2, false, false)).toBe("添加 2 人");
    expect(addButtonText(2, true, false)).toBe("正在添加…");
    expect(addButtonText(1, false, true)).toBe("重试 1 人");
    expect(pickError(0)).toBe("请至少选择一个账号。");
    expect(pickError(21)).toBe("一次最多添加 20 人。");
    expect(pickError(20)).toBe("");
    expect(batchResultText(1, [{ userId: "u2", code: "USER_DISABLED", message: "x" }], { u2: "赵工" }))
      .toBe("已添加 1 人；1 人没有添加：赵工：账号已停用。");
    expect(batchResultText(2, [], {})).toBe("");
  });
});

describe("工作台只读与停用被拒", () => {
  it("只读原因按枚举选文案", () => {
    expect(readonlyText("archived", "editor")).toBe("项目已归档，可以查看和导出，不能修改正文。");
    expect(readonlyText("role", "viewer")).toBe("你在该项目中是只读成员，可以查看和导出，不能修改正文。");
    expect(readonlyText("role", "viewer", true)).toBe("你在该项目中的权限已调整为只读成员，可以查看和导出，不能修改正文。");
    expect(readonlyText("role", "auditor")).toContain("审计身份");
    expect(readonlyBlockedText("archived", "editor")).toBe("项目已归档，刚才的修改没有保存，可以下载。");
    expect(readonlyBlockedText("role", "viewer")).toBe("你的权限已调整为只读成员，刚才的修改没有保存，可以下载。");
  });
  it("停用唯一负责人的说明", () => {
    expect(disableBlockedText("赵工", 1)).toContain("是以下 1 个项目唯一的启用负责人");
    expect(disableBlockedMoreText(25, 20)).toBe("另有 5 个项目未列出，处理完以上项目后再次停用会继续列出。");
    expect(disableBlockedMoreText(3, 3)).toBe("");
  });

  it("LAST_OWNER 列表给已归档项目加“（已归档）”并提示先恢复（Q-2）", () => {
    expect(blockedProjectLabel({ id: "1", name: "甲项目", archived: true })).toBe("甲项目（已归档）");
    expect(blockedProjectLabel({ id: "2", name: "乙项目", archived: false })).toBe("乙项目");
    expect(blockedProjectLabel({ id: "3", name: "丙项目" })).toBe("丙项目");
    expect(disableBlockedArchivedHint([{ id: "1", name: "甲", archived: true }, { id: "2", name: "乙" }])).toContain("需先恢复");
    expect(disableBlockedArchivedHint([{ id: "2", name: "乙", archived: false }])).toBe("");
  });
});

describe("A05 验收修复（21 / 20）", () => {
  it("归档项目的说明行按只读写，不再说“可以添加成员”（21 S-3、20 L-3）", () => {
    expect(membersNote({ canManage: false, projectRole: "owner", isAdmin: false, archived: true, owners: [owner("张工")] })).toBe("如需调整权限，请联系负责人 张工。");
    expect(membersNote({ canManage: false, projectRole: null, isAdmin: true, archived: true })).toBe("你以系统管理员身份查看该项目（你不是项目成员）。");
    expect(membersNote({ canManage: true, projectRole: "owner", isAdmin: false, archived: true, owners: [owner("张工")] })).not.toContain("可以添加成员");
  });
  it("没有启用负责人时的提示（19 S-1）", () => {
    expect(noActiveOwnerText(true)).toContain("请把一位启用的成员设为负责人");
    expect(noActiveOwnerText(false)).toContain("联系系统管理员");
    expect(ownersText([owner("停用", "disabled", true)], "—")).toBe("—");
  });
  it("部分失败后只留可重试的人（21 S-7）", () => {
    const failed = [
      { userId: "a", code: "USER_DISABLED", message: "" },
      { userId: "b", code: "NOT_FOUND", message: "" },
      { userId: "c", code: "ALREADY_MEMBER", message: "" },
      { userId: "d", code: "SOMETHING_TRANSIENT", message: "" },
    ];
    expect([...retryableIds(failed)]).toEqual(["d"]);
    expect(retryableIds(failed.slice(0, 3)).size).toBe(0);
  });
  it("“不能停用”里的归档项目提示指向“打开并恢复”（21 M-1）", () => {
    expect(disableBlockedArchivedHint([{ id: "1", name: "甲", archived: true }])).toContain("打开并恢复");
  });
});
