/** 冲突面板的纯逻辑（01 §6.4、02 §3.4–3.7；vitest ⑧ ⑪ ⑫）。 */
import type { ConflictAction, ConflictItem, ConflictSummary, Person } from "./api/documents";

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
  MODIFIED: "对方也修改了这段",
  DELETED: "这段在对方那里被删除了",
  ANCHOR_LOST: "插入位置附近的内容已被删除",
  STRUCTURE: "这一片内容双方都做了调整",
  FORMAT: "合并后的内容无法写回原 Word",
};

export interface ActionChoice { action: ConflictAction; label: string; placement?: "after_nearest_live" | "section_end" }
/** 每种原因可用的处理方式（01 §4.4.3 表格）。 */
export function actionsFor(item: Pick<ConflictItem, "reason" | "mine" | "theirs">): ActionChoice[] {
  switch (item.reason) {
    case "MODIFIED":
      return [{ action: "keep_mine", label: "保留我的" }, { action: "use_theirs", label: "使用对方的" },
        { action: "manual", label: "手动合并" }, { action: "keep_both", label: "两段都保留" }];
    case "STRUCTURE":
      return [{ action: "keep_mine", label: "保留我的" }, { action: "use_theirs", label: "使用对方的" }];
    case "FORMAT":
      return [{ action: "use_theirs", label: "使用对方的" }];
    case "DELETED":
      if (item.mine?.deleted) return [{ action: "keep_mine", label: "仍然删除" }, { action: "use_theirs", label: "保留对方的修改" }];
      return [{ action: "restore_as_new", label: "恢复为新段落", placement: "after_nearest_live" },
        { action: "restore_as_new", label: "放到本章节末尾", placement: "section_end" }, { action: "discard", label: "放弃我的修改" }];
    case "ANCHOR_LOST":
      return [{ action: "restore_as_new", label: "放到本章节末尾", placement: "section_end" }, { action: "discard", label: "放弃" }];
  }
}

export const USE_THEIRS_CONFIRM = "我的版本仍保存在冲突记录中，可在「已处理」里查看和下载。";

export const ACTION_DONE_TEXT: Record<string, string> = {
  keep_mine: "保留了我的", use_theirs: "使用了对方的", manual: "手动合并", keep_both: "两段都保留",
  restore_as_new: "恢复为新段落", discard: "放弃了我的修改", superseded: "已被后来的修改取代",
};

/** “李工 等 2 人更新了 3 处内容”（02 第4节第11条）。 */
export function changedByText(changedBy: Array<Person & { objects: number }> | undefined): string {
  if (!changedBy?.length) return "";
  const total = changedBy.reduce((sum, person) => sum + person.objects, 0);
  const who = changedBy.length === 1 ? changedBy[0].name : `${changedBy[0].name} 等 ${changedBy.length} 人`;
  return `${who}更新了 ${total} 处内容，已合并到你的文档。`;
}
