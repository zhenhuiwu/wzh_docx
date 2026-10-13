/** 冲突面板的纯逻辑（01 §6.4、02 §3.4–3.7；vitest ⑧ ⑪ ⑫）。 */
import type { ConflictAction, ConflictItem, ConflictSummary, Person } from "./api/documents";
import { isTable } from "./textDiff";

/** 测试工程师 J2-L：冲突“对方”就是我自己（另一份草稿、另一个标签页先写进了正文）时，不把自己的名字当成“对方”。 */
export const SELF_THEIRS_NAME = "你自己（另一份草稿或标签页）";
export function theirsName(item: Pick<ConflictItem, "theirs" | "actor"> & Partial<Pick<ConflictItem, "reason" | "objectIds">>, selfId?: string | null): string {
  const who = item.theirs?.deleted ? item.theirs?.deletedBy : item.theirs?.updatedBy;
  // 列表接口带 actor；409 返回的条目没有 actor，用当前登录用户 ID
  const me = item.actor?.id || selfId;
  if (who?.id && me && who.id === me) return SELF_THEIRS_NAME;
  // 设计师复验 2 C：整篇冲突没有修改人时写“正文当前版本”，不写“其他成员”
  return who?.name || (isWholeConflict(item) ? WHOLE_THEIRS_NAME : "其他成员");
}

/** 设计师复验 2 C：整篇冲突（FORMAT：合并后 Word 写不回；或没有对应段落的整篇结构冲突）——正文保持当前版本，不是“这一项换成了谁的版本” */
export const WHOLE_THEIRS_NAME = "正文当前版本";
export function isWholeConflict(item: Partial<Pick<ConflictItem, "reason" | "objectIds">>): boolean {
  return item.reason === "FORMAT" || (item.reason === "STRUCTURE" && !(item.objectIds?.length));
}
export function wholeConflictNote(item: Partial<Pick<ConflictItem, "reason">>): string {
  return item.reason === "FORMAT"
    ? "这次保存的内容合并后无法保持 Word 格式，正文保持当前版本；你这次的修改都在冲突副本里，可下载后手动处理。"
    : "这次保存的内容无法与正文自动合并，正文保持当前版本；你这次的修改都在冲突副本里，可下载后手动处理。";
}

/** “冲突 N”：只用服务器的 mineOpen；summary 请求失败时才用本地计数兜底。 */
export function conflictCount(summary: ConflictSummary | null, localFallback = 0): number {
  return summary ? summary.mineOpen : localFallback;
}

/** 409 OBJECT_CONFLICT：自动保存只点亮提示，主动保存（Ctrl+S、点“保存”、离开前保存）才打开面板。 */
export function conflictOpensPanel(trigger: "auto" | "manual"): boolean {
  return trigger === "manual";
}

export type ResolveFailure = "archive" | "reconflict" | "retry" | "format" | "error";
/** 处理请求失败的分流：已被其他标签页处理 → 静默归档；再次冲突 → 原地替换卡片；网络/503 → 同键重试。 */
export function classifyResolveError(error: { status?: number; code?: string }): ResolveFailure {
  if (error.status === 409 && error.code === "CONFLICT_ALREADY_RESOLVED") return "archive";
  if (error.status === 409 && error.code === "OBJECT_CONFLICT") return "reconflict";
  if (!error.status || error.status === 502 || error.status === 504 || (error.status === 503 && error.code !== "AI_UNAVAILABLE")) return "retry";
  if (error.status === 422 && (error.code === "AI_FORMAT_INVALID" || error.code === "WORD_BLOCK_PROTECTED")) return "format";
  return "error";
}

/** 每次点击生成一个键；同一请求体的重试复用；成功或换了请求体后换新键。 */
export class ResolveKeys {
  private current: { signature: string; key: string } | null = null;
  constructor(private readonly make: () => string = () => crypto.randomUUID()) {}
  keyFor(signature: string): string {
    if (!this.current || this.current.signature !== signature) this.current = { signature, key: this.make() };
    return this.current.key;
  }
  done(): void { this.current = null; }
}

export const REASON_TEXT: Record<ConflictItem["reason"], string> = {
  MODIFIED: "对方修改了同一段",
  DELETED: "这一段已被删除",
  ANCHOR_LOST: "插入位置已不存在",
  STRUCTURE: "这里的结构被同时调整",
  FORMAT: "合并后无法保持 Word 格式",
};

/** 两版之间新增的文字（去掉公共前后缀后中间那段，首尾空白去掉）；K1-M1 用来引用“你刚补的内容” */
export function addedText(before: string, after: string): string {
  let head = 0;
  while (head < before.length && head < after.length && before[head] === after[head]) head += 1;
  let tail = 0;
  while (tail < before.length - head && tail < after.length - head && before[before.length - 1 - tail] === after[after.length - 1 - tail]) tail += 1;
  return after.slice(head, after.length - tail).trim();
}

export interface ActionChoice { action: ConflictAction; label: string; placement?: "after_nearest_live" | "section_end"; hint?: string }
/** 终验回归 I-3：表格冲突按整张表处理，“都保留”会在正文里放两份表格（单元格级合并记入遗留）。 */
export const TABLE_KEEP_BOTH_HINT = "“两份表格都保留”会在正文里放两张表（我的和对方的各一张），需要你再手动合并成一张。";
/** 每种原因可用的处理方式（01 §4.4.3 表格）。 */
export function actionsFor(item: Pick<ConflictItem, "reason" | "mine" | "theirs">): ActionChoice[] {
  switch (item.reason) {
    case "MODIFIED":
      // 界面复验 N7 / 终验回归 I-3：表格冲突明确写“两份表格都保留”，并在卡片上说明会生成两张表
      if (isTable(item.theirs?.markdown || "") || isTable(item.mine?.markdown || "")) {
        return [{ action: "manual", label: "手动合并" }, { action: "keep_mine", label: "保留我的" },
          { action: "use_theirs", label: "使用对方的版本" }, { action: "keep_both", label: "两份表格都保留", hint: TABLE_KEEP_BOTH_HINT }];
      }
      return [{ action: "manual", label: "手动合并" }, { action: "keep_mine", label: "保留我的" },
        { action: "use_theirs", label: "使用对方的版本" }, { action: "keep_both", label: "两段都保留" }];
    case "STRUCTURE":
      return [{ action: "keep_mine", label: "保留我的" }, { action: "use_theirs", label: "使用对方的版本" }];
    case "FORMAT":
      return [{ action: "use_theirs", label: "使用对方的版本" }];
    case "DELETED":
      if (item.mine?.deleted) return [{ action: "keep_mine", label: "仍然删除" }, { action: "use_theirs", label: "保留对方的修改" }];
      // 设计师建议 7：DELETED 卡只给“恢复为新段落 / 放弃我的修改”（原位置找不到时服务器自动放到章节末尾并提示）
      return [{ action: "restore_as_new", label: "恢复为新段落", placement: "after_nearest_live" }, { action: "discard", label: "放弃我的修改" }];
    case "ANCHOR_LOST":
      return [{ action: "restore_as_new", label: "放到本章节末尾", placement: "section_end" }, { action: "discard", label: "放弃" }];
  }
}

export const USE_THEIRS_CONFIRM = "我的版本仍保存在冲突记录中，可在「已处理」里查看和下载。";

export const ACTION_DONE_TEXT: Record<string, string> = {
  keep_mine: "保留了我的", use_theirs: "使用了对方的", manual: "手动合并", keep_both: "两段都保留",
  restore_as_new: "恢复为新段落", discard: "放弃了我的修改", superseded: "已被后来的修改取代",
};

/** 设计师建议 7：列表标记统一（编辑区序列化是“-   项”，服务器是“- 项”），对比前先统一，免得把空格标成插入。 */
export function normalizeListMarkers(markdown: string): string {
  return markdown.replace(/^(\s*)([-*+]|\d+[.)])[ \t]+/gm, "$1$2 ");
}

/** “李工 等 2 人更新了 3 处内容”（02 第4节第11条）。 */
export function changedByText(changedBy: Array<Person & { objects: number }> | undefined): string {
  if (!changedBy?.length) return "";
  const total = changedBy.reduce((sum, person) => sum + person.objects, 0);
  const who = changedBy.length === 1 ? `${changedBy[0].name} ` : `${changedBy[0].name} 等 ${changedBy.length} 人`;
  return `${who}更新了 ${total} 处内容，已合并到你的文档。`;
}

/** 他人更新提示条（02 §3.3，界面验收 M6）：本地没有未保存修改时已自动刷新；有修改时常驻提示。 */
export function othersUpdatedText(names: string[], objects: number, refreshed: boolean, frozen = false): string {
  const unique = [...new Set(names.filter(Boolean))];
  const who = !unique.length ? "其他成员" : unique.length === 1 ? unique[0] : `${unique[0]} 等 ${unique.length} 人`;
  const count = objects > 0 ? ` ${objects} 处` : "";
  if (refreshed) return `已显示 ${who} 的最新修改。`;
  // 方案 C：编辑区有冻结的冲突段时保存后也不自动重绘，要点“立即刷新”（冲突段保持不变）
  if (frozen) return `${who}${unique.length > 1 ? "" : " "}更新了${count}内容。编辑区里有未处理的冲突段，点“立即刷新”显示${unique.length > 1 ? "他们" : "他"}的更新（冲突段保持不变）。`;
  return `${who}${unique.length > 1 ? "" : " "}更新了${count}内容。你的修改保存后会自动显示${unique.length > 1 ? "他们" : "他"}的更新。`;
}

/** 冲突按钮（界面验收 M2/M3）：自己有待处理 → “冲突 N”；只有成员冲突（负责人/管理员）→ “成员冲突 M”；都处理完但有记录 → “冲突”，仍可进入“已处理”。 */
export interface ConflictToggle { label: string; name: string; count: number; tone: "danger" | "warning" | "neutral"; tab: "mine" | "members" }
export function conflictToggle(summary: ConflictSummary | null, local = 0): ConflictToggle | null {
  const mine = conflictCount(summary, local);
  if (mine > 0) return { label: `冲突 ${mine}`, name: "冲突", count: mine, tone: "danger", tab: "mine" };
  const members = summary?.allOpen != null ? Math.max(0, summary.allOpen - (summary.mineOpen || 0)) : 0;
  if (members > 0) return { label: `成员冲突 ${members}`, name: "成员冲突", count: members, tone: "warning", tab: "members" };
  if ((summary?.mineHandled || 0) > 0) return { label: "冲突", name: "冲突", count: 0, tone: "neutral", tab: "mine" };
  return null;
}

/** 复验 P2-2：恢复提示条只在“别人”在草稿之后改过文档时才警告；自己关页面时的保存（或无他人改动）不算。changedBy 为 null 表示还没查到。 */
export function draftStaleWarning(draftRevision: number, documentRevision: number, changedBy: Array<{ id: string }> | null): boolean {
  if (draftRevision === documentRevision) return false;
  return changedBy !== null && changedBy.length > 0;
}
