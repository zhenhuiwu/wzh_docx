/**
 * A05 成员与权限：纯逻辑（可在 vitest 中测试）。文案依据 16 §2.6 / §2.8 / §2.10 / §2.13。
 * 组件只负责展示和请求，判断与拼句都在这里。
 */
import { shortTime } from "./saveModel";

export type AssignableRole = "owner" | "editor" | "viewer";
export type MemberRole = AssignableRole | "reviewer";
export type ReadonlyReason = "role" | "archived" | "assignment" | null | undefined;

export interface Assignments { sections: number; blocks: number; documents: number }
export interface OwnerSummary { id: string; name: string; status: string; isPrimary: boolean }
export interface MemberLike {
  userId: string;
  name: string;
  account: string;
  userStatus: string;
  role: MemberRole;
  isMe?: boolean;
  addedAt?: string | null;
  updatedAt?: string | null;
  updatedBy?: { id: string; name: string } | null;
  assignments?: Assignments;
}

export const ROLE_LABEL: Record<string, string> = { owner: "负责人", editor: "编写人员", viewer: "只读成员", reviewer: "审核人员（只读）" };
export const ROLE_TONE: Record<string, string> = { owner: "info", editor: "success", viewer: "", reviewer: "" };

/**
 * 角色说明。16 §2.6 写的是 A07（硬限制分工）上线后的说法“可以修改文档中分配给自己的部分”；
 * A07 未上线前编写人员能改整篇文档，界面如实写“可以修改项目中的文档”（见 18-A05开发.md 偏差 F-1）。A07 上线时改这里一处即可。
 */
export const ROLE_OPTIONS: Array<{ value: AssignableRole; label: string; hint: string }> = [
  { value: "owner", label: "负责人", hint: "可以编写，并管理成员和项目资料" },
  { value: "editor", label: "编写人员", hint: "可以修改项目中的文档" },
  { value: "viewer", label: "只读成员", hint: "可以查看和导出文档" },
];

export const MEMBERS_INFO = "编写人员可以修改项目中的文档；只读成员可以查看和导出。权限调整后，对方在下次保存或 1 分钟内生效。";
export const LAST_OWNER_TEXT = "项目至少需要一位启用中的负责人。请先把其他成员设为负责人。";
export const DISABLED_ROW_TEXT = "账号已停用，不能调整权限；可以移除。";
export const ARCHIVED_MEMBERS_TEXT = "项目已归档，成员和权限不能调整。恢复项目后才能修改。";
export const NETWORK_TEXT = "暂时不能连接服务，修改没有保存。";
export const MAX_PICK = 20;

export function roleLabel(role: string | null | undefined): string {
  return role ? ROLE_LABEL[role] ?? "成员" : "—";
}

/** 启用的负责人（主负责人在前，后端已排序）。 */
export function activeOwners(owners: OwnerSummary[] | undefined): OwnerSummary[] {
  return (owners ?? []).filter(owner => owner.status === "active");
}

/** 页头 / 概述 / 列表的“负责人”：最多 2 位，之后加“等 N 人”（16 §2.8 第 6 条）。 */
export function ownersText(owners: OwnerSummary[] | undefined, fallback = "—"): string {
  const list = activeOwners(owners);
  if (!list.length) return fallback;
  const shown = list.slice(0, 2).map(owner => owner.name).join("、");
  return list.length > 2 ? `${shown}等 ${list.length} 人` : shown;
}

/** “联系负责人 …”里的姓名：最多 2 位，之后加“等”（16 §2.6）。 */
export function contactNames(owners: OwnerSummary[] | undefined): string {
  const list = activeOwners(owners);
  const shown = list.slice(0, 2).map(owner => owner.name).join("、");
  return list.length > 2 ? `${shown}等` : shown;
}

export interface NoteInput {
  canManage: boolean;
  projectRole: string | null | undefined;
  isAdmin: boolean;
  owners?: OwnerSummary[];
}

/** 区块说明行（16 §2.6）。 */
export function membersNote(input: NoteInput): string {
  if (input.projectRole === "auditor") return "你以审计身份只读查看该项目。";
  if (input.isAdmin && !input.projectRole) return "你以系统管理员身份管理该项目（你不是项目成员）。";
  if (input.canManage) return "负责人可以添加成员、调整权限。";
  const names = contactNames(input.owners);
  return names ? `如需调整权限，请联系负责人 ${names}。` : "如需调整权限，请联系项目负责人。";
}

/** 唯一启用负责人：降级选项和移除按钮禁用（16 §2.8 第 3 条；停用的负责人永远可以降级或移除）。 */
export function isLockedOwner(member: Pick<MemberLike, "role" | "userStatus">, activeOwnerCount: number): boolean {
  return member.role === "owner" && member.userStatus === "active" && activeOwnerCount <= 1;
}

/** 某个下拉选项是否禁用。 */
export function roleOptionDisabled(member: Pick<MemberLike, "role" | "userStatus">, activeOwnerCount: number, option: AssignableRole): boolean {
  return option !== "owner" && isLockedOwner(member, activeOwnerCount);
}

/** “最近调整”两行：时间 / “{姓名} 添加 | 调整”。旧数据没有时间时显示“—”。 */
export function recentChange(member: Pick<MemberLike, "addedAt" | "updatedAt" | "updatedBy">, format: (value: string) => string): { time: string; who: string } {
  const at = member.updatedAt || member.addedAt;
  if (!at) return { time: "—", who: "" };
  const verb = member.addedAt && member.updatedAt && member.addedAt === member.updatedAt ? "添加" : "调整";
  return { time: format(at), who: member.updatedBy ? `${member.updatedBy.name} ${verb}` : "" };
}

function hasAssignments(value: Assignments | null | undefined): value is Assignments {
  return Boolean(value && (value.sections || value.blocks));
}

/** “{s} 章、{b} 段”（0 的部分省略）；都为 0 时返回空串。 */
export function assignmentParts(value: Assignments | null | undefined, joiner = "、"): string {
  if (!hasAssignments(value)) return "";
  const parts: string[] = [];
  if (value.sections) parts.push(`${value.sections} 章`);
  if (value.blocks) parts.push(`${value.blocks} 段`);
  return parts.join(joiner);
}

/** 移除确认正文（16 §2.6）。 */
export function removeConfirmText(member: Pick<MemberLike, "name" | "assignments">): { body: string; warning: string } {
  const body = `移出后，${member.name} 不能再打开该项目和其中的文档。已写的内容和版本记录不受影响。`;
  const parts = assignmentParts(member.assignments);
  const warning = parts ? `${member.name} 在 ${member.assignments!.documents} 篇文档中负责的 ${parts}将变为未分配，只有负责人能修改。` : "";
  return { body, warning };
}

export interface ChangeOutcome {
  ownerChanged?: { from?: { name: string } | null; to?: { name: string } | null } | null;
  assignmentsCleared?: Assignments | null;
}

/** 移除或改权限成功后的提示（16 §2.6）；没有需要说明的事时返回空串。 */
export function changeOutcomeText(kind: "remove" | "role", name: string, role: string | null, outcome: ChangeOutcome): string {
  const parts: string[] = [];
  const cleared = assignmentParts(outcome.assignmentsCleared ?? null);
  if (cleared) parts.push(`${cleared}变为未分配。`);
  if (outcome.ownerChanged?.to?.name) parts.push(`主负责人改为 ${outcome.ownerChanged.to.name}。`);
  if (!parts.length) return "";
  const head = kind === "remove" ? `已将 ${name} 移出项目。` : `已把 ${name} 改为${roleLabel(role)}。`;
  return head + parts.join("");
}

export interface ErrorLike { status?: number; code?: string; message?: string; details?: Record<string, any> }

/** 并发 409 的 warning（16 §2.6）。 */
export function membersConflictText(details: Record<string, any> | undefined, now: Date = new Date()): string {
  const actor = details?.lastActor?.name;
  const at = details?.lastUpdatedAt ? shortTime(details.lastUpdatedAt, now) : "";
  return actor && at ? `成员名单刚被 ${actor} 于 ${at} 调整过，已显示最新名单，请确认后再修改。` : "成员名单刚被调整过，已显示最新名单，请确认后再修改。";
}

export type MemberErrorKind = "conflict" | "archived" | "last-owner" | "disabled" | "forbidden" | "gone" | "network" | "other";

/** 成员写操作的错误归类，决定界面怎么反应（16 §2.5 流程图）。 */
export function classifyMemberError(error: ErrorLike): MemberErrorKind {
  const status = error.status ?? 0;
  if (status === 0) return "network";
  if (status === 409 && error.code === "REVISION_CONFLICT") return "conflict";
  if (status === 409 && error.code === "PROJECT_ARCHIVED") return "archived";
  if (status === 409 && error.code === "LAST_OWNER") return "last-owner";
  if (status === 422 && error.code === "USER_DISABLED") return "disabled";
  if (status === 403) return "forbidden";
  if (status === 404) return "gone";
  return "other";
}

/** 行内错误文案。adding=true 表示把账号加入项目（停用账号文案不同）。 */
export function memberErrorText(error: ErrorLike, name: string, adding = false): string {
  switch (classifyMemberError(error)) {
    case "network": return NETWORK_TEXT;
    case "conflict": return membersConflictText(error.details);
    case "archived": return ARCHIVED_MEMBERS_TEXT;
    case "last-owner": return LAST_OWNER_TEXT;
    case "disabled": return adding ? `${name} 的账号已停用，不能加入项目。` : `${name} 的账号已停用，不能调整权限；可以移除。`;
    case "forbidden": {
      if (error.details?.projectRole === "auditor") return "你以审计身份只读查看该项目，可以查看和导出，不能修改。";
      const owner = error.details?.ownerName;
      return owner ? `你在该项目中没有此操作权限，如需开通请联系负责人 ${owner}。` : "你在该项目中没有此操作权限，如需开通请联系项目负责人。";
    }
    case "gone": return "项目不存在或你无权访问。";
    default: return error.message || "操作没有完成，请稍后再试。";
  }
}

/** 添加弹窗里候选人的旁注。 */
export function candidateNote(currentRole: string | null | undefined): string {
  return currentRole ? `已是${roleLabel(currentRole)}` : "";
}

export interface BatchFailure { userId: string; code: string; message: string }

/** 部分失败的说明（16 §2.6）。names: userId → 姓名。 */
export function batchResultText(added: number, failed: BatchFailure[], names: Record<string, string>): string {
  if (!failed.length) return "";
  const reasons = failed.map(item => `${names[item.userId] ?? "该账号"}：${reasonOf(item)}`).join("；");
  return `已添加 ${added} 人；${failed.length} 人没有添加：${reasons}。`;
}

function reasonOf(item: BatchFailure): string {
  if (item.code === "USER_DISABLED") return "账号已停用";
  if (item.code === "ALREADY_MEMBER") return "已在项目中";
  if (item.code === "NOT_FOUND") return "账号不存在";
  return item.message.replace(/。$/, "");
}

/** 添加按钮文字。 */
export function addButtonText(count: number, busy: boolean, retry: boolean): string {
  if (busy) return "正在添加…";
  if (retry) return `重试 ${count} 人`;
  return count ? `添加 ${count} 人` : "添加";
}

/** 未选人 / 超过上限的提示；没问题时返回空串。 */
export function pickError(count: number): string {
  if (count <= 0) return "请至少选择一个账号。";
  if (count > MAX_PICK) return `一次最多添加 ${MAX_PICK} 人。`;
  return "";
}

/** 工作台只读原因（16 §2.10，按枚举选文案，不拼后端 message）。 */
export function readonlyText(reason: ReadonlyReason, projectRole: string | null | undefined, changed = false): string {
  if (reason === "archived") return "项目已归档，可以查看和导出，不能修改正文。";
  if (projectRole === "auditor") return "你以审计身份只读查看该项目，可以查看和导出，不能修改正文。";
  const role = roleLabel(projectRole);
  return changed ? `你在该项目中的权限已调整为${role}，可以查看和导出，不能修改正文。` : `你在该项目中是${role === "—" ? "成员" : role}，可以查看和导出，不能修改正文。`;
}

/** 工作台保存被挡时的提示条（有未保存内容时）。 */
export function readonlyBlockedText(reason: ReadonlyReason, projectRole: string | null | undefined): string {
  if (reason === "archived") return "项目已归档，刚才的修改没有保存，可以下载。";
  return `你的权限已调整为${roleLabel(projectRole)}，刚才的修改没有保存，可以下载。`;
}

export interface DisableBlockedProject { id: string; name: string; archived?: boolean }

// A05 Q-2（2026-10-11）：已归档项目里的唯一负责人照样挡停用；列表里在项目名后标“（已归档）”，
// 并提示归档项目要先恢复才能调整成员（归档项目成员只读）。
export const ARCHIVED_MARK = "（已归档）";
export function blockedProjectLabel(item: DisableBlockedProject): string {
  return item.archived ? `${item.name}${ARCHIVED_MARK}` : item.name;
}
export function disableBlockedArchivedHint(projects: DisableBlockedProject[]): string {
  return projects.some((item) => item.archived) ? "标“（已归档）”的项目需先恢复，才能调整负责人。" : "";
}

/** 用户管理：停用唯一负责人被拒（16 §2.13）。 */
export function disableBlockedText(name: string, total: number): string {
  return `${name} 是以下 ${total} 个项目唯一的启用负责人。请先在这些项目的“成员与权限”中把其他成员设为负责人，再停用该账号。`;
}

export function disableBlockedMoreText(total: number, shown: number): string {
  return total > shown ? `另有 ${total - shown} 个项目未列出，处理完以上项目后再次停用会继续列出。` : "";
}
