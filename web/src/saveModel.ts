/**
 * 保存状态与失败分类（纯逻辑，可在 vitest 中测试）。
 * 依据 01 §6.3/§6.4/§11.5 与 02 §3.2/§3.6/§3.9。
 */

export type Capsule = "clean" | "dirty" | "saving" | "retrying" | "offline" | "blocked" | "readonly";

export interface CapsuleFlags {
  readonly: boolean;
  blocked: boolean;
  offline: boolean;
  retrying: boolean;
  saving: boolean;
  dirty: boolean;
}

/** 胶囊优先级：readonly 独占；否则 blocked > offline > retrying > saving > dirty > clean。冲突不参与。 */
export function capsuleState(flags: CapsuleFlags): Capsule {
  if (flags.readonly) return "readonly";
  if (flags.blocked) return "blocked";
  if (flags.offline) return "offline";
  if (flags.retrying) return "retrying";
  if (flags.saving) return "saving";
  if (flags.dirty) return "dirty";
  return "clean";
}

export type FailureKind = "retry" | "conflict" | "blocked" | "readonly" | "stale" | "rekey" | "gone";

export interface ErrorLike { status?: number; code?: string }

/** 保存失败分类（02 §3.6）。 */
export function classifySaveError(error: ErrorLike): FailureKind {
  const status = error.status ?? 0;
  const code = error.code ?? "";
  if (status === 0 || status === 502 || status === 504 || (status === 503 && code !== "AI_UNAVAILABLE")) return "retry";
  if (status === 409 && code === "OBJECT_CONFLICT") return "conflict";
  if (status === 409 && code === "BASE_REVISION_UNAVAILABLE") return "blocked";
  if (status === 409) return "stale";
  if (status === 422 && code === "IDEMPOTENCY_KEY_REUSED") return "rekey";
  if (status === 403 && code !== "PASSWORD_CHANGE_REQUIRED") return "readonly";
  if (status === 404) return "gone";
  return "blocked";
}

/** 指数退避：2s、4s、8s…上限 30s。attempt 从 1 开始。 */
export function backoffMs(attempt: number): number {
  return Math.min(30000, 2000 * 2 ** Math.max(0, attempt - 1));
}

/** 连续失败 ≥3 次、持续失败超过 30 秒或浏览器离线 → offline。 */
export function isOffline(failures: number, failingSinceMs: number | null, nowMs: number, online: boolean): boolean {
  if (!online) return true;
  if (failures >= 3) return true;
  return failingSinceMs !== null && nowMs - failingSinceMs > 30000;
}

export type LeaveDecision = "leave" | "dialog" | "download-only";

/** 离开决策（01 §6.3 第1–3条）：只有本地草稿写入并读回校验成功才允许直接离开。 */
export function leaveDecision(unsaved: number, draftVerified: boolean): LeaveDecision {
  if (unsaved <= 0) return "leave";
  return draftVerified ? "dialog" : "download-only";
}

export interface LocationInfo { headingPath?: string[]; ordinal?: number | null; kind?: string; label?: string | null }

const KIND_WORD: Record<string, string> = { paragraph: "段", list_item: "项", table: "个表格", image: "张图", code: "个代码块", other: "块" };

/** 位置文字：章节路径 + 第 N 段/项/个表格（02 §3.7 第4条），不含内部标识。 */
export function locationText(location: LocationInfo | null | undefined): string {
  if (!location) return "";
  const path = location.headingPath?.length ? location.headingPath.join(" › ") : "文档开头（第一个标题之前）";
  if (location.kind === "heading") return `${path} · 标题本身`;
  if (location.label) return `${path} · ${location.label}`;
  return `${path} · 第 ${location.ordinal ?? 1} ${KIND_WORD[location.kind || "other"] || "块"}`;
}

/** “14:32”或“10-09 14:32”。 */
export function shortTime(value: string | number | Date, now: Date = new Date()): string {
  const date = new Date(value);
  const pad = (n: number) => String(n).padStart(2, "0");
  const hm = `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  const sameDay = date.getFullYear() === now.getFullYear() && date.getMonth() === now.getMonth() && date.getDate() === now.getDate();
  return sameDay ? hm : `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${hm}`;
}

export interface RevisionDetails { lastActor?: { id: string; name: string } | null; lastUpdatedAt?: string | null; lastActorIsMe?: boolean }

/** REVISION_CONFLICT 基础句（02 §3.9 C 定稿）。 */
export function revisionConflictText(details: RevisionDetails | undefined, now: Date = new Date()): string {
  if (details?.lastActorIsMe) return "你在其他窗口更新了该文档，请刷新后重试。";
  const at = details?.lastUpdatedAt ? shortTime(details.lastUpdatedAt, now) : "";
  if (!details?.lastActor) return at ? `文档已在 ${at} 被更新，请刷新后重试。` : "文档已被更新，请刷新后重试。";
  return `${details.lastActor.name} 于 ${at} 更新了该文档，请刷新后重试。`;
}

/** 胶囊文案（02 §3.2）。 */
export function capsuleLabel(state: Capsule, options: { savedAt?: number | null; now?: number; pending?: number; online?: boolean } = {}): string {
  const now = options.now ?? Date.now();
  switch (state) {
    case "clean":
      if (!options.savedAt) return "已保存";
      return now - options.savedAt < 60000 ? "刚刚保存" : `已保存 ${shortTime(options.savedAt, new Date(now))}`;
    case "dirty": return "有未保存修改";
    case "saving": return "保存中…";
    case "retrying": return "连接不稳定，正在重试";
    case "offline": return `离线 · 本机保留 ${options.pending ?? 0} 处修改`;
    case "blocked": return "保存失败";
    case "readonly": return "只读";
  }
}

export const PROJECT_ROLE_TEXT: Record<string, string> = { owner: "负责人", editor: "编写人员", reviewer: "审核人员", viewer: "查看者", auditor: "审计查看者（只读审计）" };

/** 403/404 文案（02 §3.9 C 第4条）。 */
export function accessText(error: { status?: number; details?: Record<string, any> }): string {
  if (error.status === 404) return "项目不存在或你无权访问。";
  const owner = error.details?.ownerName;
  return owner ? `你在该项目中没有此操作权限，如需开通请联系负责人 ${owner}。` : "你在该项目中没有此操作权限，如需开通请联系项目负责人。";
}

/** 下载文件名：“软件需求规格说明-我的修改-1010-1432.md”。 */
export function myChangesFileName(name: string, at: Date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${name}-我的修改-${pad(at.getMonth() + 1)}${pad(at.getDate())}-${pad(at.getHours())}${pad(at.getMinutes())}.md`;
}

/** 按顶层块比较的“处”数（整篇模式，01 §4.9 第5条）：块按空行切分后做 LCS 差异计数。 */
export function changedBlockCount(base: string, mine: string): number {
  const split = (text: string) => text.replace(/\r\n/g, "\n").split(/\n{2,}/).map(block => block.trim()).filter(Boolean);
  const a = split(base), b = split(mine);
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start += 1;
  let endA = a.length, endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) { endA -= 1; endB -= 1; }
  const x = a.slice(start, endA), y = b.slice(start, endB);
  if (!x.length || !y.length) return Math.max(x.length, y.length);
  const table: number[][] = Array.from({ length: x.length + 1 }, () => new Array(y.length + 1).fill(0));
  for (let i = x.length - 1; i >= 0; i -= 1) for (let j = y.length - 1; j >= 0; j -= 1) {
    table[i][j] = x[i] === y[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
  }
  const common = table[0][0];
  // 替换一个块记 1 处；纯增删各记 1 处。
  return Math.max(x.length, y.length) - common;
}
