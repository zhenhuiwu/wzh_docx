/**
 * 关页后立即重开（QA：360 节 3.16–5.88 s）：关页保存进行中时，后端让 /preview/blocks 等保存落库（patch_inflight），
 * 于是 GET 文档拿到的是旧修订 R、快速预览却是新修订 R+1。原先修订不一致就退回等整篇 /preview（冷渲染 5–7 s）。
 * 现在按快速预览与正文的修订关系决定下一步，任何情况下都不为了“可编辑”去等整篇预览。
 */
export type FastPreviewStep = "mount" | "adoptNewer" | "refetchBlocks" | "full";

export interface FastPreviewLike { documentRevision: number; objectModel: boolean; blocks: unknown[] }

export function fastPreviewStep(fast: FastPreviewLike | null, recordRevision: number, attempt: number): FastPreviewStep {
  // 对象模型不可用（拆块往返校验失败）或文档为空：只能用整篇预览
  if (fast && (!fast.objectModel || !fast.blocks.length)) return "full";
  if (!fast) return attempt === 0 ? "refetchBlocks" : "full";
  if (fast.documentRevision === recordRevision) return "mount";
  // 快速预览更新：重新取正文到同一修订再挂（关页保存刚落库）；更旧（打开后又有提交）：再取一次快速预览
  if (attempt >= 2) return "full";
  return fast.documentRevision > recordRevision ? "adoptNewer" : "refetchBlocks";
}
