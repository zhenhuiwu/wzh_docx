<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter, type RouteLocationNormalized } from "vue-router";
import { readSession } from "../session";
import DocumentDiagrams from "../components/DocumentDiagrams.vue";
import DocumentHistory from "../components/DocumentHistory.vue";
import DocumentSkillSettings from "../components/DocumentSkillSettings.vue";
import SaveStatus from "../components/SaveStatus.vue";
import LeaveDialog from "../components/LeaveDialog.vue";
import DocumentConflictPanel from "../components/DocumentConflictPanel.vue";
import { changedByText, conflictCount, conflictOpensPanel, conflictToggle, othersUpdatedText } from "../conflictModel";
import { otherTabOpen } from "../documentObjects";
import { PROJECT_ROLE_TEXT, accessText, backoffMs, capsuleLabel, capsuleState, changedBlockCount, classifySaveError, isOffline, leaveDecision, locationText, myChangesFileName, revisionConflictText, type LeaveDecision, type LocationInfo } from "../saveModel";
import { draftUsage, listDrafts, readDraft, recoverySummary, removeDraft, verifyDraft, writeDraft, type DraftV2 } from "../draftStore";
import { browserTabId, pageInstance } from "../tabIdentity";
import { revertBlock } from "../blockRevert";
import DOMPurify from "dompurify";
import type { PDFDocumentLoadingTask, PDFDocumentProxy } from "pdfjs-dist";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { createTurndown, editorMarkdown, storedImageRule } from "../editorMarkdown";
import { ApiError } from "../api/client";
import { getCapabilities, getConflictSummary, getObjectsSince, listConflicts, findChangeBySaveKey, type Capabilities, type ConflictItem, type ConflictSummary, type ResolveResult } from "../api/documents";
import { addDocumentHeading, documentHeadings, streamDocumentSection, exportDocument, getDocument, insertDocumentSection, previewDocument, updateDocumentContent, getWritingMemory, clearWritingMemory, applyDocumentEdits, replaceDocumentSelection, type DocumentSelection, type DocumentContentResult, type DocumentRecord, type DocumentEdit, type WritingMemory } from "../api/documents";

const route = useRoute();
const router = useRouter();
const documentId = computed(() => String(route.params.documentId));
const projectId = computed(() => String(route.params.id));
const document = ref<DocumentRecord | null>(null);
const editor = ref<HTMLElement | null>(null);
const paperStyle = ref<Record<string, string>>({});
const mode = ref<"original" | "edit" | "layout">("edit");
const originalPhase = ref<"loading" | "ready" | "error">("loading");
const originalError = ref("");
const pageCanvas = ref<HTMLCanvasElement | null>(null);
const pageNumber = ref(1);
const pageCount = ref(0);
const renderingPage = ref(false);
let pdfDocument: PDFDocumentProxy | null = null;
let pdfTask: PDFDocumentLoadingTask | null = null;
let originalController: AbortController | undefined;
const markdownContent = ref("");
const savedContent = ref("");
const phase = ref<"loading" | "ready" | "error">("loading");
const loadErrorStatus = ref(0);
const errorText = ref("");
const saving = ref(false);
let savePromise: Promise<void> | undefined;
let loadVersion = 0;
const diagrams = ref<InstanceType<typeof DocumentDiagrams> | null>(null);
const diagramBusy = ref(false);
// 界面验收 M7：写作助手、版本记录、冲突面板互斥，统一由 rightPanel 管理（02 §3.4 “与版本记录、写作助手互斥”）。
type RightPanel = "ai" | "history" | "conflict" | null;
const rightPanel = ref<RightPanel>(null);
const historyBusy = ref(false);
function openPanel(name: RightPanel) {
  // 版本记录正在恢复时不切走，避免恢复结果丢失
  if (rightPanel.value === "history" && historyBusy.value && name !== "history") return;
  rightPanel.value = name;
}
function panelFlag(name: Exclude<RightPanel, null>) {
  return computed<boolean>({
    get: () => rightPanel.value === name,
    set: (open) => { if (open) openPanel(name); else if (rightPanel.value === name) rightPanel.value = null; },
  });
}
const historyOpen = panelFlag("history");
const aiOpen = panelFlag("ai");
const conflictOpen = panelFlag("conflict");
const skillOpen = ref(false);
const skillBusy = ref(false);
const refreshingEditor = ref(false);
const undoingAi = ref(false);
const aiUndo = ref<{ id: string; content: string; revision: number } | null>(null);
const localDraft = ref<DraftV2 | null>(null);
const localDrafts = ref<DraftV2[]>([]);
const draftBusy = ref(false);
const exporting = ref(false);
const exportMenu = ref(false);
const exportError = ref("");
const actionNote = ref("");
let actionNoteTimer: number | undefined;
// 保存状态（02 §3.2）：胶囊由这些标志按优先级得出。
const readonly = ref(false);
const readonlyReason = ref("");
const blocked = ref<{ message: string; location?: LocationInfo; content: string; code: string; blockText?: string } | null>(null);
const revertingBlock = ref(false);
const retrying = ref(false);
const failures = ref(0);
let failingSince: number | null = null;
const online = ref(typeof navigator === "undefined" ? true : navigator.onLine);
const savedAt = ref<number | null>(null);
const showSaving = ref(false);
const now = ref(Date.now());
const storageFailed = ref(false);
const leaveDialog = ref<{ mode: Exclude<LeaveDecision, "leave">; count: number; target: string } | null>(null);
let leaveBypass = false;
let retryTimer: number | undefined;
let savingTimer: number | undefined;
let clockTimer: number | undefined;
let pendingKey: { content: string; key: string } | null = null;
// S5：编辑区正文所基于的修订号。合并保存后编辑区若有更新的输入，编辑区仍基于旧修订，下次保存以它为基准做三方合并。
let mergeBase: number | null = null;
const conflictSummary = ref<ConflictSummary | null>(null);
// 界面验收 M2/M3：冲突入口按 mineOpen / 成员冲突 / 已处理记录显示；打开时停在对应页签。
const conflictTab = ref<"mine" | "members">("mine");
const conflictEntry = computed(() => conflictToggle(conflictSummary.value));
const memberConflicts = computed(() => conflictSummary.value?.allOpen != null ? Math.max(0, conflictSummary.value.allOpen - conflictSummary.value.mineOpen) : 0);
// 界面验收 M5：本人待处理冲突在正文和目录上的标记
const openConflictItems = ref<ConflictItem[]>([]);
const conflictMarks = ref<Array<{ key: string; top: number; label: string; item: ConflictItem }>>([]);
const conflictHeadings = computed(() => new Set(openConflictItems.value.map(item => item.location?.headingPath?.at(-1)).filter(Boolean) as string[]));
// 审查 S2：自动保存遇到冲突时的常驻提示（面板不自动打开）
const autoConflict = ref<{ count: number; name: string } | null>(null);
// 界面验收 M6 / 审查 S3：他人更新
const othersUpdated = ref<{ names: string[]; objects: number } | null>(null);
const updatedHeadings = ref<Set<string>>(new Set());
let updateTimer: number | undefined;
let checkingUpdates = false;
// 审查 S14：对象模型不可用（往返校验失败的旧文档）时按整篇提交
const objectModel = ref<boolean | null>(null);
const draftConfirmDiscard = ref(false);
const draftDiffOpen = ref(false);
const conflictPanel = ref<InstanceType<typeof DocumentConflictPanel> | null>(null);
const mineConflicts = computed(() => conflictCount(conflictSummary.value));
// 多标签提示（决策4：提示，不阻止；02 §3.3）：只覆盖同一浏览器。
const multiTab = ref(false);
let presence: BroadcastChannel | null = null;
const lastPresenceTabs = new Set<string>();
const capabilities = ref<Capabilities | null>(null);
// 打开文档时的账号：会话失效后 readSession() 为空，草稿仍要写到原账号名下（G1）。
let draftAccount = "";
// 审查 M1：tabId 经 BroadcastChannel 声明去重（复制标签页会带着同一个 sessionStorage 值）。
let browserTab = "";
let draftTimer: number | undefined;
const selectedHeading = ref<number | null>(null);
const headingTitle = ref("");
const headingPlacement = ref<"child" | "sibling">("child");
const addingHeading = ref(false);
const headingError = ref("");
const aiScope = ref<WritingMemory["scope"]>("section");
const selectedText = ref("");
const selectionNeedsRefresh = ref(false);
let selectedRange: Range | null = null;
let draftSelection: DocumentSelection | undefined;
let targetVersion = 0;
const editorVersion = ref(0);
const draftEditorVersion = ref(-1);
const aiInstruction = ref("");
const aiDraft = ref("");
const aiEdits = ref<DocumentEdit[]>([]);
const aiComplete = ref(false);
const aiAllowedModes = ref<("append" | "replace")[]>(["append", "replace"]);
let draftInstruction = "";
let pendingMemory: WritingMemory | undefined;
const writingMemory = ref("");
const memoryOffset = ref(0);
const memoryHasMore = ref(false);
const memoryLoading = ref(false);
const memoryError = ref("");
const aiRevision = ref<number | null>(null);
const aiBusy = ref(false);
const aiInserting = ref(false);
const aiError = ref("");
const aiPreviewHost = ref<HTMLElement | null>(null);
const aiPreviewHtml = ref("");
const aiStopped = ref(false);
const aiProgress = ref<{ stage: string; message: string }[]>([]);
const aiElapsed = ref(0);
let aiProgressTimer: number | undefined;
let aiController: AbortController | undefined;
let autoSaveTimer: number | undefined;
let layoutNeedsRefresh = false;

const isDirty = computed(() => markdownContent.value !== savedContent.value);
const editable = computed(() => !aiLocked.value && !addingHeading.value && !readonly.value);
const historyRestoreBlocked = computed(() => readonly.value ? "你没有修改正文的权限，不能恢复版本。" : isDirty.value || saving.value ? "当前还有修改未保存，保存后才能恢复版本。" : "");
const pendingCount = computed(() => isDirty.value ? Math.max(1, changedBlockCount(savedContent.value, markdownContent.value)) : 0);
const offline = computed(() => !online.value || (retrying.value && isOffline(failures.value, failingSince, now.value, online.value)));
const capsule = computed(() => capsuleState({ readonly: readonly.value, blocked: Boolean(blocked.value) || storageFailed.value, offline: offline.value && isDirty.value,
  retrying: retrying.value && isDirty.value, saving: showSaving.value, dirty: isDirty.value }));
const capsuleText = computed(() => capsuleLabel(capsule.value, { savedAt: savedAt.value, now: now.value, pending: pendingCount.value }));
const capsuleDetail = computed(() => {
  switch (capsule.value) {
    case "clean": return savedAt.value ? `所有修改已保存到服务器（${new Date(savedAt.value).toLocaleTimeString("zh-CN", { hour12: false })}）` : "所有修改已保存到服务器";
    case "dirty": return "停止输入 3 秒后自动保存，也可以按 Ctrl+S";
    case "saving": return `正在保存 ${pendingCount.value} 处修改`;
    case "retrying": return `第 ${failures.value} 次重试未成功，稍后自动再试。你的修改保存在本机`;
    case "offline": return online.value ? "连不上服务器。你的修改保存在本机，恢复后会自动保存" : "网络已断开。你的修改保存在本机，联网后会自动保存";
    case "blocked": return storageFailed.value && !blocked.value ? "浏览器存储已满，请下载你的修改" : blocked.value?.message || "保存失败";
    case "readonly": return readonlyReason.value || "你没有修改正文的权限";
  }
  return "";
});
const outline = computed(() => documentHeadings(markdownContent.value));
const canGenerate = computed(() => aiScope.value === "document" ? Boolean(markdownContent.value.trim()) : aiScope.value === "selection" ? Boolean(selectedText.value) : selectedHeading.value !== null);
const aiProgressLabel = computed(() => aiStopped.value ? "已停止" : aiBusy.value ? aiProgress.value.at(-1)?.message || "正在连接编写服务" : aiComplete.value ? "草稿已完成" : aiError.value ? "编写失败" : "生成未完成");
const canApplyDraft = computed(() => aiComplete.value && !aiBusy.value && !aiInserting.value && !isDirty.value && editorVersion.value === draftEditorVersion.value && aiRevision.value === document.value?.revision && Boolean(aiDraft.value.trim()) && (aiScope.value !== "document" || aiEdits.value.length > 0));
const aiLocked = computed(() => skillOpen.value || skillBusy.value || historyOpen.value || historyBusy.value || aiBusy.value || aiInserting.value || refreshingEditor.value || undoingAi.value || diagramBusy.value || Boolean(aiPreviewHost.value) || Boolean(localDraft.value));
const canUndoAi = computed(() => Boolean(aiUndo.value) && aiUndo.value?.id === document.value?.id && aiUndo.value?.revision === document.value?.revision && !isDirty.value && !aiLocked.value && !saving.value && !addingHeading.value);
const canStartAi = computed(() => !skillOpen.value && !skillBusy.value && !historyOpen.value && !historyBusy.value && !aiBusy.value && !aiInserting.value && !saving.value && !refreshingEditor.value && !undoingAi.value && !addingHeading.value && !diagramBusy.value && !localDraft.value);
const turndown = createTurndown(storedImageRule(() => document.value?.formatTemplate.images || []));
function messageOf(error: unknown) {
  return error instanceof ApiError || error instanceof Error ? error.message : "暂时不能连接服务。";
}
async function load() {
  const id = documentId.value;
  const version = ++loadVersion;
  window.clearTimeout(autoSaveTimer);
  document.value = null;
  historyOpen.value = false;
  conflictOpen.value = false;
  conflictSummary.value = null;
  openConflictItems.value = [];
  conflictMarks.value = [];
  autoConflict.value = null;
  othersUpdated.value = null;
  updatedHeadings.value = new Set();
  objectModel.value = null;
  draftConfirmDiscard.value = false;
  draftDiffOpen.value = false;
  mergeBase = null;
  skillOpen.value = false;
  aiUndo.value = null;
  memoryOffset.value = 0;
  memoryHasMore.value = false;
  localDraft.value = null;
  localDrafts.value = [];
  resetSaveState();
  originalController?.abort();
  void pdfTask?.destroy();
  pdfTask = null;
  pdfDocument = null;
  mode.value = "edit";
  selectedHeading.value = null;
  aiOpen.value = false;
  selectedText.value = "";
  selectedRange = null;
  pendingMemory = undefined;
  writingMemory.value = "";
  aiScope.value = "section";
  aiInstruction.value = "";
  clearAiDraft();
  phase.value = "loading";
  errorText.value = "";
  loadErrorStatus.value = 0;
  try {
    draftAccount = readSession()?.staffId || draftAccount;
    browserTab = await browserTabId();
    void getCapabilities().then(value => { capabilities.value = value; }).catch(() => { capabilities.value = null; });
    const record = await getDocument(id);
    const preview = await previewDocument(id);
    if (id !== documentId.value || version !== loadVersion) return;
    document.value = record;
    if (record.permissions && !record.permissions.canEdit) {
      // 01 §6.1：canEdit=false 直接只读，不必等第一次 403。
      readonly.value = true;
      readonlyReason.value = `你在该项目中是${PROJECT_ROLE_TEXT[record.permissions.projectRole || ""] || "成员"}，可以查看和导出，不能修改正文。`;
    }
    markdownContent.value = record.markdownContent;
    savedContent.value = record.markdownContent;
    // 审查 S14：对象模型不可用（拆块往返校验失败）的文档，服务器按整篇合并
    void getObjectsSince(record.id, record.revision).then(value => { if (document.value === record) objectModel.value = value.objectModel; }).catch(() => undefined);
    selectedHeading.value = [...record.markdownContent.matchAll(/^(#{1,6})\s+(.+)$/gm)].length ? 0 : null;
    phase.value = "ready";
    await nextTick();
    if (id !== documentId.value || version !== loadVersion) return;
    showEditorHtml(preview.html);
    savedAt.value = Date.parse(record.updatedAt) || null;
    findLocalDrafts(record);
    void refreshMemory();
    void refreshConflicts();
    openPresence(record.id);
  } catch (error) {
    if (id !== documentId.value || version !== loadVersion) return;
    phase.value = "error";
    loadErrorStatus.value = error instanceof ApiError ? error.status : 0;
    errorText.value = error instanceof ApiError && error.status === 404 ? accessText(error) : messageOf(error);
  }
}
function resetSaveState() {
  window.clearTimeout(retryTimer);
  window.clearTimeout(savingTimer);
  readonly.value = false;
  readonlyReason.value = "";
  blocked.value = null;
  retrying.value = false;
  failures.value = 0;
  failingSince = null;
  showSaving.value = false;
  storageFailed.value = false;
  leaveDialog.value = null;
  exportMenu.value = false;
  exportError.value = "";
  pendingKey = null;
}
function account() { return draftAccount || readSession()?.staffId || "user"; }
function findLocalDrafts(record: DocumentRecord) {
  const drafts = listDrafts(window.localStorage, account(), record.id)
    .filter(draft => !(draft.wholeDocument && draft.markdown === record.markdownContent));
  for (const draft of listDrafts(window.localStorage, account(), record.id)) {
    // 与服务器正文相同的草稿没有恢复价值；按读到的版本删除（期间被别的标签改写就不删）。
    if (draft.wholeDocument && draft.markdown === record.markdownContent) removeDraft(window.localStorage, account(), record.id, draft.tabId, { savedAt: draft.savedAt });
  }
  localDrafts.value = drafts;
  localDraft.value = drafts[0] ?? null;
  void settlePendingSaves(record);
}
/** 01 §6.5：页面关闭时发出的保存若已落库（按 clientSaveKey 查），对应草稿不再提示恢复。查询失败时保留草稿。 */
async function settlePendingSaves(record: DocumentRecord, retried = false) {
  let waiting = false;
  for (const draft of [...localDrafts.value]) {
    const key = draft.pendingSave?.clientSaveKey;
    if (!key) continue;
    let landed = false;
    try { landed = await findChangeBySaveKey(record.id, key); } catch { continue; }
    if (!landed) {
      // 审查 S5 / 测试 L7：关页时的 keepalive 保存可能比重新打开还晚落库，或合并后与服务器正文相同（不记键）。
      try {
        const latest = await getDocument(record.id);
        landed = draft.wholeDocument && latest.markdownContent === draft.markdown;
      } catch { /* 查不到就按未落库处理 */ }
      if (!landed && !retried) { waiting = true; continue; }
    }
    if (!landed || document.value !== record) continue;
    if (!removeDraft(window.localStorage, account(), record.id, draft.tabId, { savedAt: draft.savedAt })) continue;
    localDrafts.value = localDrafts.value.filter(item => item !== draft);
    localDraft.value = localDrafts.value[0] ?? null;
    noteAction("上次关闭页面前的修改已保存到服务器。");
  }
  if (waiting && !retried) window.setTimeout(() => { if (document.value === record) void settlePendingSaves(record, true); }, 1500);
}
/** 没有未保存修改时，把编辑区换成服务器最新正文（严格接口 409 后的“刷新”）。返回是否已刷新。 */
async function refreshFromServer(): Promise<boolean> {
  const record = document.value;
  if (!record || isDirty.value || saving.value) return false;
  const latest = await getDocument(record.id);
  if (document.value !== record) return false;
  if (latest.revision === record.revision) return true;
  if (latest.formatTemplate) record.formatTemplate = latest.formatTemplate;
  mergeBase = null;
  await acceptContent({ markdownContent: latest.markdownContent, revision: latest.revision, status: latest.status });
  void refreshConflicts();
  return true;
}
function isRevisionConflict(error: unknown): error is ApiError {
  return error instanceof ApiError && error.status === 409 && error.code === "REVISION_CONFLICT";
}
/** S6：AI 采纳、标题、图表插入都带 allowMerge（01 §4.3），每次操作一个新键。 */
function mergeRequest() { return { allowMerge: true, clientSaveKey: crypto.randomUUID() }; }
function noteMerged(result: { merged?: boolean; changedBy?: Array<{ id: string; name: string; objects: number }> }): boolean {
  if (!result.merged) return false;
  const text = changedByText(result.changedBy);
  if (text) noteAction(text, 6000);
  return true;
}
/** 派生写入 409 OBJECT_CONFLICT：冲突已存为副本，按主动保存处理（打开面板 + 重交可合并部分）。 */
function derivedConflict(error: unknown): boolean {
  if (!(error instanceof ApiError) || error.code !== "OBJECT_CONFLICT" || !document.value) return false;
  handleSaveError(error, markdownContent.value, "manual", document.value.revision);
  return true;
}
function dismissedTabs(docId: string): string[] {
  try { return JSON.parse(window.sessionStorage.getItem(`doc-workbench.multitab:${docId}`) || "[]"); } catch { return []; }
}
function openPresence(docId: string) {
  closePresence();
  multiTab.value = false;
  if (typeof BroadcastChannel === "undefined") return;
  const self = { tabId: browserTab, userId: account() };
  presence = new BroadcastChannel(`doc-workbench:${docId}`);
  presence.onmessage = (event: MessageEvent) => {
    const message = event.data as { type?: string; tabId?: string };
    if (!otherTabOpen(message, self)) return;
    lastPresenceTabs.add(String(message.tabId));
    // 审查 M1：仍打开着的其他标签的草稿由它自己管理，本标签不显示为“恢复条”，也就不会被这里舍弃。
    if (message.type !== "bye" && localDrafts.value.some(draft => draft.tabId === message.tabId)) {
      localDrafts.value = localDrafts.value.filter(draft => draft.tabId !== message.tabId);
      localDraft.value = localDrafts.value[0] ?? null;
    }
    if (message.type === "presence") presence?.postMessage({ type: "presence-reply", ...self });
    // “知道了”后本标签不再为同一个标签提示；新的标签页出现时重新提示
    if (!dismissedTabs(docId).includes(String(message.tabId))) multiTab.value = true;
  };
  presence.postMessage({ type: "presence", ...self });
}
function closePresence() {
  if (!presence) return;
  presence.postMessage({ type: "bye", tabId: browserTab, userId: account() });
  presence.close();
  presence = null;
}
function dismissMultiTab() {
  multiTab.value = false;
  if (!document.value) return;
  // 记下当前已知的其他标签：回复过 presence 的都算
  const key = `doc-workbench.multitab:${document.value.id}`;
  const known = new Set(dismissedTabs(document.value.id));
  for (const tab of lastPresenceTabs) known.add(tab);
  window.sessionStorage.setItem(key, JSON.stringify([...known]));
}
async function refreshConflicts() {
  const record = document.value;
  if (!record) return;
  try {
    const summary = await getConflictSummary(record.id);
    if (document.value !== record) return;
    conflictSummary.value = summary;
    if (!summary.mineOpen) autoConflict.value = null;
    openConflictItems.value = summary.mineOpen > 0 ? await listConflicts(record.id, "open", "me") : [];
    if (document.value !== record) return;
    await nextTick();
    applyConflictMarks();
  } catch {
    // 数量取不到时保留原值，不影响编辑。
  }
}
const plainKey = (text: string) => text.replace(/\\(.)/g, "$1").replace(/!\[[^\]]*\]\([^)]*\)/g, "").replace(/[\s*_`#>|]+/g, "").replace(/^(\d+\.|[-+])/, "");
/** 按纯文本找正文中的顶层块（Word 预览没有对象 ID 时用；只认唯一匹配）。 */
function findBlockByText(markdown: string | null | undefined): HTMLElement | undefined {
  const root = editor.value, key = markdown ? plainKey(markdown) : "";
  if (!root || key.length < 2) return undefined;
  const found = ([...root.children] as HTMLElement[]).filter(block => plainKey(block.textContent || "") === key);
  return found.length === 1 ? found[0] : undefined;
}
/** 按对象 ID 找正文中的块；没有 ID 时按文字，再按章节路径 + 第 N 段（与“保存失败 · 定位”同一逻辑）。 */
function findBlock(location: LocationInfo | null | undefined, objectIds?: string[], text?: string | null): HTMLElement | undefined {
  const root = editor.value;
  if (!root) return undefined;
  for (const id of objectIds || []) {
    const node = root.querySelector<HTMLElement>(`[data-object-id="${CSS.escape(id)}"]`);
    if (node) return (node.closest(".paper-content > *") as HTMLElement | null) || node;
  }
  const byText = findBlockByText(text);
  if (byText) return byText;
  if (!location) return undefined;
  const path = location.headingPath || [];
  const blocks = [...root.children] as HTMLElement[];
  let start = 0;
  if (path.length) {
    const last = path[path.length - 1];
    const index = blocks.findIndex(block => /^H[1-6]$/.test(block.tagName) && (block.textContent || "").trim() === last);
    if (index >= 0) start = index;
  }
  let target: HTMLElement | undefined = blocks[start];
  if (location.ordinal && location.kind && location.kind !== "heading") {
    const tags: Record<string, RegExp> = { paragraph: /^(P|DIV|BLOCKQUOTE)$/, list_item: /^(UL|OL)$/, table: /^TABLE$/, image: /^(P|FIGURE|IMG)$/, code: /^PRE$/ };
    let seen = 0;
    for (const block of blocks.slice(path.length ? start + 1 : 0)) {
      if (/^H[1-6]$/.test(block.tagName)) break;
      if ((tags[location.kind] || /./).test(block.tagName) && ++seen === location.ordinal) { target = block; break; }
    }
  }
  return target;
}
function conflictName(item: ConflictItem) {
  return (item.theirs?.deleted ? item.theirs?.deletedBy?.name : item.theirs?.updatedBy?.name) || "其他成员";
}
/** 冲突块：红色竖线 + 浅红底（class 只加在编辑区 DOM 上，转 Markdown 前去掉）；标签画在纸面浮层里，不进入正文。 */
function applyConflictMarks() {
  const root = editor.value;
  if (!root) { conflictMarks.value = []; return; }
  root.querySelectorAll(".blk-conflict").forEach(node => node.classList.remove("blk-conflict"));
  const marks: typeof conflictMarks.value = [];
  if (mode.value === "edit") {
    for (const item of openConflictItems.value) {
      const block = findBlock(item.location, item.objectIds, item.theirs?.deleted ? null : item.theirs?.markdown);
      if (!block) continue;
      block.classList.add("blk-conflict");
      marks.push({ key: `${item.conflictId}:${item.itemIndex}`, top: block.offsetTop, label: `与 ${conflictName(item)} 的修改冲突`, item });
    }
  }
  conflictMarks.value = marks;
}
let marksFrame = 0;
function scheduleMarks() {
  if (!openConflictItems.value.length) return;
  window.cancelAnimationFrame(marksFrame);
  marksFrame = window.requestAnimationFrame(() => applyConflictMarks());
}
/** 卡片“定位”/正文标签：滚到正文中的块并闪一下；从正文标签进入时打开面板并滚到卡片。 */
function locateConflict(item: ConflictItem) {
  const block = findBlock(item.location, item.objectIds, item.theirs?.deleted ? null : item.theirs?.markdown);
  if (!block) { noteAction("没能在正文中找到这一段，可能已被删除或移动。"); return; }
  block.scrollIntoView({ behavior: "smooth", block: "center" });
  block.classList.add("blk-flash");
  window.setTimeout(() => block.classList.remove("blk-flash"), 1600);
}
function openConflictCard(key: string) {
  autoConflict.value = null;
  openConflicts("mine");
  let tries = 0;
  const find = () => {
    const card = window.document.querySelector<HTMLElement>(`.conflict-panel [data-conflict="${CSS.escape(key)}"]`);
    if (card) { card.scrollIntoView({ behavior: "smooth", block: "nearest" }); card.focus?.(); return; }
    if (++tries < 20) window.setTimeout(find, 150);
  };
  window.setTimeout(find, 0);
}
/** 他人更新（02 §3.3）：标签页回到前台或每 45 秒查一次；本地没有未保存修改时直接刷新，有修改时常驻提示。 */
async function checkOthersUpdated() {
  const record = document.value;
  if (!record || phase.value !== "ready" || mode.value !== "edit" || checkingUpdates || window.document.visibilityState === "hidden") return;
  checkingUpdates = true;
  try {
    const since = await getObjectsSince(record.id, record.revision);
    if (document.value !== record) return;
    objectModel.value = since.objectModel;
    if (since.documentRevision <= record.revision) return;
    const names = since.changedBy.map(person => person.name);
    const objects = since.changedBy.reduce((sum, person) => sum + person.objects, 0);
    const changed = since.objects.filter(item => !item.deleted);
    const quiet = !isDirty.value && !saving.value && !aiLocked.value && !addingHeading.value && !blocked.value && !readonly.value;
    if (quiet || (readonly.value && !isDirty.value)) {
      const scroll = editor.value?.closest(".page-scroll");
      const top = scroll?.scrollTop || 0;
      if (!(await refreshFromServer())) { othersUpdated.value = { names, objects }; return; }
      if (scroll) scroll.scrollTop = top;
      othersUpdated.value = null;
      markUpdated(changed);
      noteAction(names.length ? othersUpdatedText(names, objects, true) : "已显示最新内容。", 5000);
    } else {
      othersUpdated.value = { names, objects };
    }
  } catch {
    // 查询失败不影响编辑，下次再查。
  } finally {
    checkingUpdates = false;
  }
}
/** 被他人更新、尚未看过的块：左侧蓝色竖线，目录标题右侧蓝点；点击或编辑该块后消失。 */
function markUpdated(objects: Array<{ id: string; markdown?: string }>) {
  const root = editor.value;
  if (!root) return;
  const headings = new Set<string>();
  for (const object of objects) {
    const node = root.querySelector<HTMLElement>(`[data-object-id="${CSS.escape(object.id)}"]`);
    const block = (node?.closest(".paper-content > *") as HTMLElement | null) || node || findBlockByText(object.markdown);
    if (!block) continue;
    block.classList.add("blk-updated");
    let cursor: Element | null = block;
    while (cursor && !/^H[1-6]$/.test(cursor.tagName)) cursor = cursor.previousElementSibling;
    if (cursor) headings.add((cursor.textContent || "").trim());
  }
  updatedHeadings.value = headings;
}
function clearUpdatedAt(target: EventTarget | null) {
  const root = editor.value;
  if (!root || !(target instanceof Node) || !updatedHeadings.value.size && !root.querySelector(".blk-updated")) return;
  const element = target instanceof Element ? target : target.parentElement;
  const block = element?.closest(".blk-updated");
  if (!block) return;
  block.classList.remove("blk-updated");
  const remaining = new Set<string>();
  for (const node of root.querySelectorAll(".blk-updated")) {
    let cursor: Element | null = node;
    while (cursor && !/^H[1-6]$/.test(cursor.tagName)) cursor = cursor.previousElementSibling;
    if (cursor) remaining.add((cursor.textContent || "").trim());
  }
  updatedHeadings.value = remaining;
}
/** 提示条“立即刷新”：先保存自己的修改（服务器合并他人的更新），再按服务器正文重绘。 */
async function refreshOthersNow() {
  window.clearTimeout(autoSaveTimer);
  if (isDirty.value) await save("manual");
  if (savePromise) await savePromise.catch(() => undefined);
  if (isDirty.value) return;
  await refreshFromServer().catch(() => false);
  othersUpdated.value = null;
}
function headingPathsOf(markdown: string): string[][] {
  // 变化块所在的章节（只用于恢复条提示，不参与保存）。
  const base = savedContent.value.replace(/\r\n/g, "\n").split(/\n{2,}/).map(item => item.trim());
  const known = new Set(base);
  const paths: string[][] = [];
  const stack: { level: number; text: string }[] = [];
  for (const block of markdown.replace(/\r\n/g, "\n").split(/\n{2,}/).map(item => item.trim()).filter(Boolean)) {
    const heading = /^(#{1,6})[ \t]+(.+)$/.exec(block);
    if (heading) {
      while (stack.length && stack[stack.length - 1].level >= heading[1].length) stack.pop();
      stack.push({ level: heading[1].length, text: heading[2].replace(/\\([\\`*_{}\[\]<>|#])/g, "$1").trim() });
    }
    if (!known.has(block)) {
      const path = stack.map(item => item.text);
      if (!paths.some(item => item.join("/") === path.join("/"))) paths.push(path);
      if (paths.length >= 5) break;
    }
  }
  return paths;
}
async function refreshMemory(offset = 0) {
  const id = documentId.value;
  memoryOffset.value = offset;
  memoryLoading.value = true;
  memoryError.value = "";
  try {
    const memory = await getWritingMemory(id, offset);
    if (documentId.value === id) {
      writingMemory.value = memory.items.map(item => `${item.target}：${item.instruction}`).join("\n\n");
      memoryOffset.value = offset;
      memoryHasMore.value = memory.hasMore;
    }
  } catch (error) {
    if (documentId.value === id) memoryError.value = messageOf(error);
  } finally {
    if (documentId.value === id) memoryLoading.value = false;
  }
}
async function removeMemory() {
  if (aiLocked.value || memoryLoading.value || !window.confirm("清除这份文档的编写记忆？")) return;
  memoryLoading.value = true;
  memoryError.value = "";
  try {
    await clearWritingMemory(documentId.value);
    writingMemory.value = "";
  } catch (error) {
    memoryError.value = messageOf(error);
  } finally {
    memoryLoading.value = false;
  }
}
function chooseAiScope(scope: WritingMemory["scope"]) {
  if (aiLocked.value) return;
  clearAiDraft();
  aiScope.value = scope;
  aiOpen.value = true;
}
function showEditorHtml(html: string) {
  layoutNeedsRefresh = false;
  selectionNeedsRefresh.value = false;
  selectedText.value = "";
  selectedRange = null;
  aiScope.value = "section";
  editorVersion.value += 1;
  for (const image of document.value?.formatTemplate.images || []) {
    html = html.replaceAll(`src="${image.ref}"`, `src="data:${image.content_type};base64,${image.data}"`);
  }
  if (editor.value) {
    // 测试 L3：服务端把目录/域段落标成 contenteditable=false（data-word-protected）。
    editor.value.innerHTML = DOMPurify.sanitize(html, { ADD_ATTR: ["contenteditable"] });
    paperStyle.value = {};
    try {
      const page = JSON.parse(editor.value.firstElementChild?.getAttribute("data-word-page") || "{}");
      for (const name of ["width", "minHeight", "paddingTop", "paddingBottom", "paddingLeft", "paddingRight"]) {
        if (typeof page[name] === "string" && /^\d+(?:\.\d+)?pt$/.test(page[name])) paperStyle.value[name] = page[name];
      }
      if (paperStyle.value.width) paperStyle.value.boxSizing = "content-box";
    } catch { /* Local drafts without page metadata keep the default paper. */ }
    void nextTick(() => applyConflictMarks());
  }
}
async function acceptContent(result: DocumentContentResult) {
  const record = document.value;
  if (!record) return;
  if (result.formatTemplate) record.formatTemplate = result.formatTemplate;
  aiUndo.value = null;
  refreshingEditor.value = true;
  if (editor.value) editor.value.contentEditable = "false";
  record.revision = result.revision;
  record.status = result.status;
  record.markdownContent = result.markdownContent;
  markdownContent.value = result.markdownContent;
  savedContent.value = result.markdownContent;
  persistLocalDraft();
  try {
    const preview = await previewDocument(record.id);
    if (document.value === record && documentId.value === record.id) showEditorHtml(preview.html);
  } catch (error) {
    if (document.value === record) {
      errorText.value = `内容已保存，编辑视图加载失败：${messageOf(error)}`;
      // Block editing stale HTML; loading the saved content is safe to retry.
      phase.value = "error";
    }
  } finally {
    refreshingEditor.value = false;
    if (editor.value) editor.value.contentEditable = String(editable.value);
  }
}
async function addHeading() {
  if (!document.value || addingHeading.value || aiLocked.value) return;
  headingError.value = "";
  const title = headingTitle.value.trim();
  if (!title || title.length > 120 || /[\r\n]/.test(title)) {
    headingError.value = "请输入不超过 120 字的单行标题。";
    return;
  }
  addingHeading.value = true;
  window.clearTimeout(autoSaveTimer);
  try {
    if (isDirty.value) await save();
    if (isDirty.value) return;
    const result = await addDocumentHeading(document.value.id, document.value.revision, selectedHeading.value, headingPlacement.value, title, mergeRequest());
    await acceptContent(result);
    noteMerged(result);
    selectedHeading.value = result.headingIndex ?? null;
    headingTitle.value = "";
    aiDraft.value = "";
    aiRevision.value = null;
    await nextTick();
    if (selectedHeading.value !== null) scrollToHeading(selectedHeading.value);
  } catch (error) {
    if (!derivedConflict(error)) headingError.value = messageOf(error);
  } finally {
    addingHeading.value = false;
  }
}
function clearAiDraft() {
  aiController?.abort();
  aiController = undefined;
  aiBusy.value = false;
  window.clearInterval(aiProgressTimer);
  aiProgressTimer = undefined;
  aiProgress.value = [];
  aiElapsed.value = 0;
  aiPreviewHost.value?.remove();
  aiPreviewHost.value = null;
  aiPreviewHtml.value = "";
  aiStopped.value = false;
  aiDraft.value = "";
  aiEdits.value = [];
  aiComplete.value = false;
  aiAllowedModes.value = ["append", "replace"];
  draftInstruction = "";
  draftSelection = undefined;
  aiRevision.value = null;
  aiError.value = "";
  targetVersion += 1;
}
function selectHeading(index: number) {
  if (aiLocked.value) return;
  if (selectedHeading.value !== index || aiScope.value !== "section") clearAiDraft();
  selectedText.value = "";
  selectedRange = null;
  aiScope.value = "section";
  selectedHeading.value = index;
  aiOpen.value = true;
  if (outline.value[index]?.level >= 6) headingPlacement.value = "sibling";
  scrollToHeading(index);
}
function selectionParts(range: Range) {
  const root = editor.value;
  if (!root || !root.contains(range.startContainer) || !root.contains(range.endContainer)) return null;
  const boundaryBlock = (container: Node, offset: number, end: boolean) => {
    const node = container instanceof Element ? container.childNodes[end ? offset - 1 : offset] || container : container;
    const element = node instanceof Element ? node : node.parentElement;
    return element?.closest("p,li") ?? null;
  };
  const first = boundaryBlock(range.startContainer, range.startOffset, false);
  const last = boundaryBlock(range.endContainer, range.endOffset, true);
  const forbidden = "h1,h2,h3,h4,h5,h6,table,img,blockquote,pre";
  if (!first || !last || first.closest(forbidden) || last.closest(forbidden)
      || [...root.querySelectorAll(forbidden)].some(node => range.intersectsNode(node))) return null;
  const blocks = [...root.querySelectorAll("p,li")];
  const firstIndex = blocks.indexOf(first), lastIndex = blocks.indexOf(last);
  if (firstIndex < 0 || lastIndex < firstIndex) return null;
  const selected = blocks.slice(firstIndex, lastIndex + 1);
  if (selected.length > 1 && selected.some(block => block.tagName !== "P" || block.parentElement !== first.parentElement)) return null;
  const offsetIn = (block: Element, container: Node, offset: number) => {
    const before = range.cloneRange();
    before.selectNodeContents(block);
    before.setEnd(container, offset);
    return (before.cloneContents().textContent || "").length;
  };
  const start = offsetIn(first, range.startContainer, range.startOffset);
  const end = offsetIn(last, range.endContainer, range.endOffset);
  const text = selected.map((block, index) => (block.textContent || "").slice(index === 0 ? start : 0, index === selected.length - 1 ? end : undefined)).join("\n\n");
  return { blocks, firstIndex, lastIndex, start, end, text };
}
function captureEditorTarget() {
  if (aiLocked.value) return;
  const selection = window.getSelection();
  if (!editor.value || !selection?.rangeCount) return;
  const range = selection.getRangeAt(0).cloneRange();
  if (!editor.value.contains(range.startContainer) || !editor.value.contains(range.endContainer)) return;
  // A selection ending at the next paragraph's start must not consume that paragraph break.
  if (!range.collapsed && range.endOffset === 0) {
    const walker = window.document.createTreeWalker(editor.value, NodeFilter.SHOW_TEXT);
    let lastText: Text | null = null;
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (node !== range.endContainer && node.textContent?.trim() && range.intersectsNode(node)) lastText = node as Text;
    }
    if (lastText) range.setEnd(lastText, lastText.length);
  }
  const headings = [...editor.value.querySelectorAll("h1,h2,h3,h4,h5,h6")];
  const index = headings.map((heading) => heading.contains(range.startContainer) || Boolean(heading.compareDocumentPosition(range.startContainer) & Node.DOCUMENT_POSITION_FOLLOWING)).lastIndexOf(true);
  const parts = selectionParts(range);
  const text = parts?.text || range.toString();
  const insideHeading = index >= 0 && headings[index].contains(range.startContainer) && headings[index].contains(range.endContainer);
  if (!range.collapsed && text.trim() && parts && text.length <= 8000) {
    if (!selectedRange || selectedRange.compareBoundaryPoints(Range.START_TO_START, range) !== 0 || selectedRange.compareBoundaryPoints(Range.END_TO_END, range) !== 0 || selectedText.value !== text) clearAiDraft();
    selectedText.value = text;
    selectedRange = range.cloneRange();
    aiScope.value = "selection";
  } else if (!range.collapsed && text.trim() && !insideHeading) {
    clearAiDraft();
    selectedText.value = "";
    selectedRange = null;
    aiScope.value = "selection";
    aiOpen.value = true;
    aiError.value = text.length > 8000 ? "每次最多选择 8000 字。" : "请选择连续的正文段落，不要包含标题、表格或图片。";
  } else {
    if (aiScope.value === "document" && range.collapsed) {
      selectedHeading.value = index >= 0 ? index : null;
      return;
    }
    if (aiScope.value !== "section" || selectedHeading.value !== (index >= 0 ? index : null)) clearAiDraft();
    selectedText.value = "";
    selectedRange = null;
    aiScope.value = "section";
  }
  selectedHeading.value = index >= 0 ? index : null;
}
async function resolveSelection(): Promise<DocumentSelection> {
  if (!document.value || !editor.value || !selectedRange || !editor.value.contains(selectedRange.startContainer) || !editor.value.contains(selectedRange.endContainer)) throw new Error("请重新选择正文。");
  const parts = selectionParts(selectedRange);
  if (!parts) throw new Error("请选择连续的正文段落，不要包含标题、表格或图片。");
  const { blocks, firstIndex, lastIndex, start, end, text } = parts;
  // Refresh source anchors after autosaving; never guess between repeated paragraphs.
  const preview = await previewDocument(document.value.id);
  const canonical = new DOMParser().parseFromString(DOMPurify.sanitize(preview.html), "text/html");
  const expected = [...canonical.querySelectorAll("p,li")];
  if (blocks.length !== expected.length || blocks.some((item, i) => item.tagName !== expected[i].tagName || item.textContent !== expected[i].textContent)) {
    selectionNeedsRefresh.value = true;
    throw new Error("段落结构已变化，请刷新编辑稿后重新选择。");
  }
  const blockId = expected[firstIndex]?.getAttribute("data-selection-id");
  const endBlockId = expected[lastIndex]?.getAttribute("data-selection-id");
  if (!blockId || !endBlockId || expected.slice(firstIndex, lastIndex + 1).some(block => !block.hasAttribute("data-selection-id")) || !text || text !== selectedText.value) throw new Error("此处包含特殊结构，请使用章节编写。");
  return { blockId, ...(firstIndex === lastIndex ? {} : { endBlockId }), start, end, text };
}
async function refreshSelectionEditor() {
  if (!document.value || !canStartAi.value || isDirty.value) return;
  await acceptContent({ markdownContent: savedContent.value, revision: document.value.revision, status: document.value.status });
  aiError.value = "请在刷新后的正文中重新选择。";
}
function placeAiPreview() {
  if (!editor.value) return;
  const host = window.document.createElement("div");
  host.setAttribute("data-ai-preview", "");
  host.contentEditable = "false";
  if (aiScope.value === "selection" && selectedRange) {
    let block: Node = selectedRange.endContainer;
    while (block.parentNode && block.parentNode !== editor.value) block = block.parentNode;
    editor.value.insertBefore(host, block.nextSibling);
  } else if (aiScope.value === "document") {
    editor.value.insertBefore(host, editor.value.firstChild);
  } else {
    const headings = editor.value.querySelectorAll("h1,h2,h3,h4,h5,h6");
    const next = headings[(selectedHeading.value ?? -1) + 1];
    editor.value.insertBefore(host, next ?? null);
  }
  aiPreviewHost.value = host;
  void nextTick(() => host.scrollIntoView({ behavior: "smooth", block: "center" }));
}
function stopAi() {
  aiStopped.value = true;
  aiController?.abort();
}
function showAiProgress(stage: string, message: string) {
  const last = aiProgress.value.at(-1);
  if (last?.stage === stage) last.message = message;
  else aiProgress.value.push({ stage, message });
}
async function generateSection() {
  if (!document.value || !canGenerate.value || !canStartAi.value) return;
  clearAiDraft();
  const id = document.value.id;
  const index = aiScope.value === "document" ? null : selectedHeading.value;
  const text = aiScope.value === "selection" ? selectedText.value : undefined;
  const target = targetVersion;
  const version = editorVersion.value;
  const controller = new AbortController();
  draftInstruction = aiInstruction.value.trim();
  aiController = controller;
  const current = () => aiController === controller && document.value?.id === id && targetVersion === target;
  aiBusy.value = true;
  showAiProgress("preparing", isDirty.value ? "正在保存文档" : "正在定位编写范围");
  const startedAt = performance.now();
  aiProgressTimer = window.setInterval(() => { aiElapsed.value = Math.floor((performance.now() - startedAt) / 1000); }, 1000);
  window.clearTimeout(autoSaveTimer);
  try {
    if (isDirty.value) await save();
    if (isDirty.value) throw new Error("文档尚未保存，请保存后重试。");
    if (aiScope.value === "selection") draftSelection = await resolveSelection();
    draftEditorVersion.value = version;
    placeAiPreview();
    showAiProgress("connecting", "正在连接编写服务");
    await streamDocumentSection(id, document.value.revision, index, draftInstruction, text, (event) => {
      if (!current() || controller.signal.aborted) return;
      if (editorVersion.value !== version || document.value?.revision !== event.revision || isDirty.value) throw new Error("文档已变化，请重新生成草稿。");
      if (event.type === "progress") {
        showAiProgress(event.stage || "writing", event.message);
        return;
      }
      aiRevision.value = event.revision;
      aiDraft.value = event.content;
      aiEdits.value = event.edits || [];
      aiComplete.value = event.type === "done";
      aiAllowedModes.value = event.allowedModes || ["append", "replace"];
      let html = event.html;
      if (aiScope.value !== "selection") {
        for (const image of document.value?.formatTemplate.images || []) {
          html = html.replaceAll(`src="${image.ref}"`, `src="data:${image.content_type};base64,${image.data}"`);
        }
      }
      aiPreviewHtml.value = DOMPurify.sanitize(html, { FORBID_TAGS: [...(aiScope.value === "selection" ? ["img"] : []), "iframe", "form", "input", "button", "textarea"] });
    }, controller.signal, aiScope.value, draftSelection);
  } catch (error) {
    if (current() && !aiStopped.value) {
      aiComplete.value = false;
      if (!aiDraft.value) {
        aiPreviewHost.value?.remove();
        aiPreviewHost.value = null;
        aiPreviewHtml.value = "";
      }
      aiError.value = messageOf(error);
    }
  } finally {
    if (current()) {
      window.clearInterval(aiProgressTimer);
      aiProgressTimer = undefined;
      aiElapsed.value = Math.floor((performance.now() - startedAt) / 1000);
      aiBusy.value = false;
      aiController = undefined;
    }
  }
}
async function insertSection(insertMode: "append" | "replace") {
  if (!document.value || aiInserting.value) return;
  aiError.value = "";
  if (!aiComplete.value) { aiError.value = "草稿尚未完整生成，不能直接写入。"; return; }
  if (!aiDraft.value.trim()) {
    aiError.value = "草稿不能为空。";
    return;
  }
  if (aiScope.value === "section" && !aiAllowedModes.value.includes(insertMode)) {
    aiError.value = "原稿格式不支持此操作，请使用可用的采用方式。";
    return;
  }
  if (isDirty.value || aiRevision.value !== document.value.revision || editorVersion.value !== draftEditorVersion.value) {
    aiError.value = "文档已变化，请重新生成草稿。";
    return;
  }
  aiInserting.value = true;
  const previousContent = savedContent.value;
  const id = document.value.id;
  const instruction = draftInstruction;
  let merged = false;
  try {
    if (aiScope.value === "selection") {
      if (!draftSelection) throw new Error("请重新选择正文并生成。");
      const result = await replaceDocumentSelection(document.value.id, document.value.revision, draftSelection, aiDraft.value, instruction, mergeRequest());
      clearAiDraft();
      await acceptContent(result);
      merged = noteMerged(result);
      aiScope.value = "section";
      void refreshMemory();
    } else if (aiScope.value === "document") {
      if (!aiComplete.value || !aiEdits.value.length) throw new Error("全文草稿尚未完成，请重新生成。");
      const result = await applyDocumentEdits(document.value.id, document.value.revision, aiEdits.value, instruction, mergeRequest());
      clearAiDraft();
      await acceptContent(result);
      merged = noteMerged(result);
      aiScope.value = "document";
      void refreshMemory();
    } else if (selectedHeading.value !== null) {
      const result = await insertDocumentSection(document.value.id, document.value.revision, selectedHeading.value, aiDraft.value, insertMode, instruction, mergeRequest());
      clearAiDraft();
      await acceptContent(result);
      merged = noteMerged(result);
      await nextTick();
      scrollToHeading(selectedHeading.value);
      void refreshMemory();
    }
    // 合并了他人修改时不提供“撤回 AI 修改”：撤回会用采纳前的全文覆盖他人的修改（撤回是严格接口，01 §8.2 用例 18）。
    if (document.value?.id === id && !isDirty.value && phase.value === "ready" && !merged) {
      aiUndo.value = { id, content: previousContent, revision: document.value.revision };
    }
    clearAiDraft();
  } catch (error) {
    if (derivedConflict(error)) clearAiDraft();
    else aiError.value = messageOf(error);
  } finally {
    aiInserting.value = false;
    if (editor.value) editor.value.contentEditable = String(editable.value);
  }
}
async function undoAiChange() {
  if (!canUndoAi.value || !aiUndo.value || !document.value) return;
  const snapshot = aiUndo.value;
  undoingAi.value = true;
  if (editor.value) editor.value.contentEditable = "false";
  try {
    const result = await updateDocumentContent(snapshot.id, snapshot.revision, snapshot.content, undefined, snapshot.revision);
    aiUndo.value = null;
    await acceptContent(result);
    void refreshMemory();
  } catch (error) {
    // 撤回是严格接口（用例 18）：他人更新后不能撤回，说明是谁改的。
    if (isRevisionConflict(error)) { aiUndo.value = null; noteAction(`不能撤回：${revisionConflictText(error.details).replace("请刷新后重试。", "撤回会覆盖这些修改。")}`, 8000); }
    else noteAction(`撤回失败：${messageOf(error)}`, 8000);
  } finally {
    undoingAi.value = false;
    if (editor.value) editor.value.contentEditable = String(editable.value);
  }
}
async function loadOriginal(retried = false): Promise<void> {
  if (!document.value) return;
  originalController?.abort();
  const controller = new AbortController();
  originalController = controller;
  originalPhase.value = "loading";
  originalError.value = "";
  try {
    const query = mode.value === "layout" ? `?scope=edited&expectedRevision=${document.value.revision}` : "";
    const response = await fetch(`/api/v1/documents/${document.value.id}/word-preview${query}`, {
      credentials: "same-origin", signal: controller.signal,
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => null);
      // 版式预览是严格接口：他人刚更新过时刷新到最新正文后自动重试一次（02 §3.7 第 11 条）。
      if (response.status === 409 && payload?.error?.code === "REVISION_CONFLICT" && !retried && !controller.signal.aborted && await refreshFromServer().catch(() => false)) {
        if (originalController === controller) return loadOriginal(true);
        return;
      }
      throw new Error(payload?.error?.message || "版式预览暂时不可用。");
    }
    const blob = await response.blob();
    const { getDocument: openPdf, GlobalWorkerOptions } = await import("pdfjs-dist");
    GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
    const task = openPdf({ data: new Uint8Array(await blob.arrayBuffer()) });
    const pdf = await task.promise;
    if (controller.signal.aborted) {
      void task.destroy();
      return;
    }
    void pdfTask?.destroy();
    pdfTask = task;
    pdfDocument = pdf;
    pageNumber.value = 1;
    pageCount.value = pdf.numPages;
    originalPhase.value = "ready";
    await nextTick();
    await renderPage();
  } catch (error) {
    if (controller.signal.aborted) return;
    originalError.value = messageOf(error);
    originalPhase.value = "error";
  }
}
async function renderPage() {
  if (!pdfDocument || !pageCanvas.value || mode.value === "edit") return;
  renderingPage.value = true;
  try {
    const page = await pdfDocument.getPage(pageNumber.value);
    const viewport = page.getViewport({ scale: 1.5 });
    const canvas = pageCanvas.value;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("无法绘制 Word 页面。");
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.floor(viewport.width * ratio);
    canvas.height = Math.floor(viewport.height * ratio);
    canvas.style.width = `${viewport.width}px`;
    canvas.style.height = `${viewport.height}px`;
    await page.render({ canvas, canvasContext: context, viewport, transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0] }).promise;
  } catch (error) {
    originalError.value = messageOf(error);
    originalPhase.value = "error";
  } finally {
    renderingPage.value = false;
  }
}
async function goPage(change: number) {
  if (renderingPage.value || pageNumber.value + change < 1 || pageNumber.value + change > pageCount.value) return;
  pageNumber.value += change;
  await renderPage();
  pageCanvas.value?.parentElement?.scrollTo({ top: 0 });
}
async function switchMode(next: "original" | "edit" | "layout") {
  if (aiLocked.value) return;
  if (next !== "edit" && isDirty.value) await save();
  if (next !== "edit" && isDirty.value) return;
  mode.value = next;
  selectedText.value = "";
  selectedRange = null;
  aiScope.value = "section";
  clearAiDraft();
  if (next !== "edit") await loadOriginal();
}
function onEditorInput(event?: Event) {
  if (!editor.value) return;
  aiUndo.value = null;
  const copy = editor.value.cloneNode(true) as HTMLElement;
  copy.querySelectorAll("[data-ai-preview]").forEach((node) => node.remove());
  // 冲突/他人更新标记只用于显示，不进入正文
  copy.querySelectorAll(".blk-conflict,.blk-updated,.blk-flash").forEach((node) => {
    node.classList.remove("blk-conflict", "blk-updated", "blk-flash");
    if (!node.classList.length) node.removeAttribute("class");
  });
  const html = copy.innerHTML;
  markdownContent.value = editorMarkdown(turndown, DOMPurify.sanitize(html, { RETURN_DOM: true }) as HTMLElement);
  scheduleDraftWrite();
  scheduleMarks();
  if (event) clearUpdatedAt(event.target instanceof Node && editor.value?.contains(event.target) ? window.getSelection()?.anchorNode ?? null : null);
  editorVersion.value += 1;
  if (event instanceof InputEvent && ["insertParagraph", "insertFromDrop"].includes(event.inputType)) layoutNeedsRefresh = true;
  if (aiDraft.value) aiError.value = "文档已变化，请重新生成草稿。";
  window.clearTimeout(autoSaveTimer);
  scheduleAutoSave();
}
function scheduleAutoSave(delay = 3000) {
  window.clearTimeout(autoSaveTimer);
  autoSaveTimer = window.setTimeout(() => void save(), delay);
}
/** 审查 S4：大文档每次按键都整篇序列化写 localStorage 太慢，改为 400ms 去抖；保存、离开、隐藏、关页前强制写。 */
function scheduleDraftWrite() {
  window.clearTimeout(draftTimer);
  draftTimer = window.setTimeout(() => { draftTimer = undefined; persistLocalDraft(); }, 400);
}
function flushDraft() {
  if (draftTimer === undefined) return;
  persistLocalDraft();
}
function onVisibilityChange() {
  if (window.document.visibilityState === "hidden") flushDraft();
  else void checkOthersUpdated();
}
const storageUsage = computed(() => storageFailed.value ? draftUsage(window.localStorage, account()) : []);
const otherDocDrafts = computed(() => storageUsage.value.filter(item => item.docId !== document.value?.id));
/** 配额不足时：把其他文档的本机草稿打包下载后再从浏览器中移除（先下载，不丢稿）。 */
function exportAndClearOtherDrafts() {
  const items = otherDocDrafts.value;
  if (!items.length) return;
  const bundle = items.map(item => readDraft(window.localStorage, account(), item.docId, item.tabId)).filter(Boolean);
  download(`本机草稿备份-${new Date().toISOString().slice(0, 16).replace(/[-:T]/g, "")}.json`, JSON.stringify(bundle, null, 2), "application/json;charset=utf-8");
  for (const item of items) removeDraft(window.localStorage, account(), item.docId, item.tabId);
  persistLocalDraft();
}
function currentDraft(): DraftV2 | null {
  const record = document.value;
  if (!record) return null;
  return {
    v: 2, docId: record.id, tabId: browserTab, docRevision: record.revision, mergeBase: mergeBase ?? record.revision, savedAt: Date.now(),
    wholeDocument: true, markdown: markdownContent.value, blocks: [], changed: pendingCount.value,
    headingPaths: headingPathsOf(markdownContent.value), conflictsEdits: [], pendingSave: pendingKey ? { clientSaveKey: pendingKey.key, sentAt: Date.now() } : null,
    writer: pageInstance,
  };
}
/** 每次输入同步写本机草稿；没有未保存修改时删除本标签页的草稿。写入失败进入 blocked（01 §6.5 第7条）。 */
function persistLocalDraft(): boolean {
  const record = document.value;
  if (!record || !browserTab) return false;
  window.clearTimeout(draftTimer);
  draftTimer = undefined;
  if (!isDirty.value) {
    // 审查 M1：只删本页面写的那份（复制出的标签若曾共用 tabId，不会删掉对方的草稿）。
    removeDraft(window.localStorage, account(), record.id, browserTab, { writer: pageInstance });
    storageFailed.value = false;
    return true;
  }
  const draft = currentDraft()!;
  const ok = writeDraft(window.localStorage, account(), draft) === "ok";
  storageFailed.value = !ok;
  return ok;
}
/** 写入并读回校验；离开对话框是否提供“离开”取决于它（01 §6.3 第3条）。 */
function persistAndVerify(): boolean {
  if (!persistLocalDraft()) return false;
  const draft = currentDraft();
  return Boolean(draft) && verifyDraft(window.localStorage, account(), draft!);
}
function download(name: string, text: string, type = "text/markdown;charset=utf-8") {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = window.document.createElement("a");
  link.href = url;
  link.download = name;
  window.document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function downloadMyChanges() {
  if (!document.value) return;
  download(myChangesFileName(document.value.name), markdownContent.value);
}
async function restoreLocalDraft() {
  const draft = localDraft.value, record = document.value;
  if (!draft || !record || draftBusy.value || readonly.value || !draft.markdown) return;
  draftBusy.value = true;
  try {
    // 审查 S6：先等在途的保存结束，保持单飞。
    if (savePromise) await savePromise.catch(() => undefined);
    // S5：服务器版本已变化时按草稿的基准修订做三方合并；改到同一段落的进入冲突面板，不覆盖别人的修改。
    markdownContent.value = draft.markdown;
    await saveContent(draft.markdown, "manual", draft.docRevision === record.revision ? undefined : (draft.mergeBase ?? draft.docRevision));
    // 编辑区按服务器正文重绘（草稿条存在时 refreshEditorFormatting 会被锁住）。
    if (!blocked.value && !isDirty.value && document.value === record) await acceptContent({ markdownContent: savedContent.value, revision: record.revision, status: record.status });
    if (!blocked.value) {
      removeDraft(window.localStorage, account(), record.id, draft.tabId, { savedAt: draft.savedAt });
      localDrafts.value = localDrafts.value.filter(item => item !== draft);
      localDraft.value = localDrafts.value[0] ?? null;
    }
  } finally {
    draftBusy.value = false;
  }
}
/** 审查/验收 S11：舍弃用应用内二次确认（不再用浏览器原生 confirm）。 */
function discardLocalDraft() {
  const draft = localDraft.value;
  if (!draft || !document.value) return;
  if (!draftConfirmDiscard.value) { draftConfirmDiscard.value = true; return; }
  draftConfirmDiscard.value = false;
  removeDraft(window.localStorage, account(), document.value.id, draft.tabId, { savedAt: draft.savedAt });
  localDrafts.value = localDrafts.value.filter(item => item !== draft);
  localDraft.value = localDrafts.value[0] ?? null;
}
function chooseLocalDraft(index: number) {
  localDraft.value = localDrafts.value[index] ?? localDraft.value;
  draftConfirmDiscard.value = false;
}
/** 恢复条“查看差异”：列出本机草稿与服务器正文不同的段落（按空行分块）。 */
const draftDiff = computed(() => {
  const draft = localDraft.value;
  if (!draft?.markdown) return { added: [] as string[], removed: [] as string[] };
  const split = (text: string) => text.replace(/\r\n/g, "\n").split(/\n{2,}/).map(item => item.trim()).filter(Boolean);
  const server = split(savedContent.value), mine = split(draft.markdown);
  const serverSet = new Set(server), mineSet = new Set(mine);
  return { added: mine.filter(block => !serverSet.has(block)).slice(0, 20), removed: server.filter(block => !mineSet.has(block)).slice(0, 20) };
});
function draftTime(draft: DraftV2) {
  return new Date(draft.savedAt || Date.now()).toLocaleString("zh-CN", { hour12: false, month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
function downloadLocalDraft() {
  if (!localDraft.value?.markdown || !document.value) return;
  download(myChangesFileName(document.value.name, new Date(localDraft.value.savedAt || Date.now())), localDraft.value.markdown);
}
function noteAction(message: string, ms = 4000) {
  actionNote.value = message;
  window.clearTimeout(actionNoteTimer);
  actionNoteTimer = window.setTimeout(() => { actionNote.value = ""; }, ms);
}
function save(trigger: "auto" | "manual" = "auto"): Promise<void> {
  if (savePromise) return savePromise.then(() => (isDirty.value && !retrying.value && !blockedFor(markdownContent.value) ? save(trigger) : undefined));
  if (!document.value || !isDirty.value || aiPreviewHost.value || readonly.value) return Promise.resolve();
  if (trigger === "auto" && blockedFor(markdownContent.value)) return Promise.resolve();
  return saveContent(markdownContent.value, trigger);
}
function blockedFor(content: string) { return Boolean(blocked.value) && blocked.value!.content === content; }
/** 单飞保存；同一内容的重试复用同一个 clientSaveKey（01 §6.3）。 */
/**
 * baseRevision：本次提交所基于的修订（缺省为编辑区基准）。
 * editor：冲突后自动重交可合并部分时，编辑区当时的正文和基准——用户之后没再输入才用服务器正文替换编辑区。
 */
function saveContent(content: string, trigger: "auto" | "manual", baseRevision?: number, editor?: { content: string; base: number }): Promise<void> {
  const record = document.value!;
  const memory = pendingMemory;
  const current = () => document.value === record && documentId.value === record.id;
  window.clearTimeout(retryTimer);
  window.clearTimeout(autoSaveTimer);
  saving.value = true;
  window.clearTimeout(savingTimer);
  savingTimer = window.setTimeout(() => { if (saving.value) showSaving.value = true; }, 400);
  if (!pendingKey || pendingKey.content !== content) pendingKey = { content, key: crypto.randomUUID() };
  const key = pendingKey.key;
  flushDraft();
  savePromise = (async () => {
    try {
      const base = baseRevision ?? mergeBase ?? record.revision;
      const result = await updateDocumentContent(record.id, base, content, memory, undefined, { clientSaveKey: key, allowMerge: true });
      if (!current()) return;
      const typedNewer = markdownContent.value !== (editor?.content ?? content);
      const serverText = result.markdownContent ?? content;
      const changedText = serverText !== (editor?.content ?? content);
      savedContent.value = serverText;
      record.revision = result.revision;
      record.status = result.status;
      record.markdownContent = serverText;
      let rerender = false;
      if (!typedNewer) {
        mergeBase = null;
        markdownContent.value = serverText;
        // 合并带进了别人的修改：编辑区按服务器正文重绘（不受 AI/草稿锁影响，否则编辑区会停在旧内容上）。
        rerender = changedText;
      } else if (changedText) {
        // 编辑区有更新的输入，不能替换；下次保存仍以编辑区原基准合并（同段落会再次冲突，新冲突取代旧的）。
        mergeBase = editor?.base ?? base;
      } else {
        mergeBase = null;
      }
      if (result.merged) {
        const text = changedByText(result.changedBy);
        if (text) noteAction(text, 6000);
      }
      if (!typedNewer) othersUpdated.value = null;
      pendingKey = null;
      blocked.value = null;
      retrying.value = false;
      failures.value = 0;
      failingSince = null;
      savedAt.value = Date.now();
      persistLocalDraft();
      if (memory === pendingMemory && memory) { pendingMemory = undefined; void refreshMemory(); }
      if (rerender && !isDirty.value) await acceptContent({ markdownContent: serverText, revision: record.revision, status: record.status });
      else if (layoutNeedsRefresh && !isDirty.value) await refreshEditorFormatting(false);
    } catch (error) {
      if (current()) handleSaveError(error, content, trigger, baseRevision ?? mergeBase ?? record.revision);
    } finally {
      saving.value = false;
      showSaving.value = false;
      window.clearTimeout(savingTimer);
      savePromise = undefined;
      if (current() && isDirty.value && !retrying.value && !blockedFor(markdownContent.value) && !readonly.value) scheduleAutoSave();
    }
  })();
  return savePromise;
}
function handleSaveError(error: unknown, content: string, trigger: "auto" | "manual", base?: number) {
  const apiError = error instanceof ApiError ? error : new ApiError(0, "DEPENDENCY_UNAVAILABLE", messageOf(error));
  switch (classifySaveError(apiError)) {
    case "conflict": {
      // 01 §5.4/§6.4：冲突部分已存为冲突副本；其余可合并的部分用新键重新提交。
      retrying.value = false;
      pendingKey = null;
      const details = apiError.details || {};
      const appliable = typeof details.appliableMarkdown === "string" ? details.appliableMarkdown : null;
      const appliableBase = typeof details.appliableBaseRevision === "number" ? details.appliableBaseRevision : null;
      void refreshConflicts().then(() => conflictPanel.value?.load());
      const conflicts = Array.isArray(details.conflicts) ? details.conflicts as ConflictItem[] : [];
      if (conflictOpensPanel(trigger)) openConflicts("mine");
      else autoConflict.value = { count: Math.max(1, conflicts.length), name: conflicts[0] ? conflictName(conflicts[0]) : "其他成员" };
      if (appliable !== null && appliableBase !== null && document.value) {
        const editorBase = base ?? mergeBase ?? document.value.revision;
        queueMicrotask(() => void saveContent(appliable, "auto", appliableBase, { content, base: editorBase }));
      }
      return;
    }
    case "retry": {
      failures.value += 1;
      failingSince ??= Date.now();
      retrying.value = true;
      now.value = Date.now();
      window.clearTimeout(retryTimer);
      retryTimer = window.setTimeout(() => void save("auto"), backoffMs(failures.value));
      return;
    }
    case "readonly":
      readonly.value = true;
      readonlyReason.value = accessText(apiError);
      retrying.value = false;
      blocked.value = { message: `${accessText(apiError)}未保存的修改可以下载。`, content, code: apiError.code };
      return;
    case "rekey":
      pendingKey = null;
      blocked.value = { message: apiError.message, content, code: apiError.code };
      retrying.value = false;
      return;
    case "stale":
      retrying.value = false;
      blocked.value = { message: `${revisionConflictText(apiError.details)}你的修改已保留在本机。`, content, code: apiError.code };
      return;
    default: {
      retrying.value = false;
      const location = apiError.details?.location as LocationInfo | undefined;
      // 界面验收 S8：位置单独显示，说明文字只用原因（details.reason），不再写两遍。
      const reason = typeof apiError.details?.reason === "string" ? apiError.details.reason : apiError.message;
      const blockText = typeof apiError.details?.blockText === "string" ? apiError.details.blockText : undefined;
      blocked.value = { message: location ? reason : apiError.message, location, content, code: apiError.code, blockText };
    }
  }
}
/** 界面验收 M1：把卡住保存的那一段换回上次保存的原文，其余修改照常保存。 */
async function revertBlockedBlock() {
  const record = document.value, failed = blocked.value;
  if (!record || !failed?.blockText || revertingBlock.value) return;
  const reverted = revertBlock(savedContent.value, markdownContent.value, failed.blockText);
  if (reverted === null) {
    noteAction("没能自动找到这一段，请手动改回这一段，或先下载你的修改。", 8000);
    return;
  }
  revertingBlock.value = true;
  try {
    if (savePromise) await savePromise.catch(() => undefined);
    markdownContent.value = reverted;
    blocked.value = null;
    failures.value = 0;
    failingSince = null;
    await saveContent(reverted, "manual");
    if (!blocked.value && !isDirty.value && document.value === record) {
      await acceptContent({ markdownContent: savedContent.value, revision: record.revision, status: record.status });
      noteAction("已撤销这一段的修改，其余修改已保存。");
    }
  } finally {
    revertingBlock.value = false;
  }
}
function retryNow() {
  failures.value = 0;
  failingSince = null;
  void save("manual");
}
/** 把编辑区滚动到保存失败的位置（按章节路径 + 第 N 段，找不到就停在章节标题）。 */
function locateBlocked() {
  const location = blocked.value?.location;
  if (!location) return;
  findBlock(location)?.scrollIntoView({ behavior: "smooth", block: "center" });
}
function onOnline() {
  online.value = true;
  // 联网后立即重试，不等退避（02 P0-3）。
  if (isDirty.value && !readonly.value) { failures.value = 0; failingSince = null; void save("auto"); }
}
function onOffline() { online.value = false; }
/** 01 §6.5：关闭页面时用 keepalive 发出最后一次保存；键先写入草稿，下次打开按键核对是否落库。 */
function onPageHide() {
  const record = document.value;
  if (!record || !isDirty.value || readonly.value || aiPreviewHost.value || blockedFor(markdownContent.value)) return;
  const content = markdownContent.value;
  if (new Blob([content]).size > 60000) return; // keepalive 正文上限 64KB；超出时只留本机草稿。
  if (!pendingKey || pendingKey.content !== content) pendingKey = { content, key: crypto.randomUUID() };
  persistLocalDraft();
  void updateDocumentContent(record.id, mergeBase ?? record.revision, content, undefined, undefined, { clientSaveKey: pendingKey.key, allowMerge: true, keepalive: true }).catch(() => undefined);
}
/** 02 P1-3：编辑区内部拖拽会把段落整块搬走（产生移动/冲突），默认禁止；外部拖入仍走粘贴清洗。 */
function blockInternalDrag(event: DragEvent) {
  if (editor.value && event.target instanceof Node && editor.value.contains(event.target)) {
    event.preventDefault();
    noteAction("编辑区内不支持拖动段落，请使用剪切和粘贴。");
  }
}
function blockUnsupportedImages(event: ClipboardEvent | DragEvent) {
  const transfer = event instanceof ClipboardEvent ? event.clipboardData : event.dataTransfer;
  if (!transfer) return;
  if ([...transfer.files].some(file => file.type.startsWith("image/")) || /<img\b/i.test(transfer.getData("text/html"))) {
    event.preventDefault();
    noteAction("不能直接粘贴或拖入图片。生成的图表请使用“插入正文”，原稿中的图片会保留。");
  }
}
async function pasteContent(event: ClipboardEvent) {
  blockUnsupportedImages(event);
  if (event.defaultPrevented) return;
  event.preventDefault();
  if (aiLocked.value || addingHeading.value || !event.clipboardData) return;
  const html = event.clipboardData.getData("text/html");
  // S7：对象 ID / 选区 ID 不随粘贴复制（否则产生重复 ID，01 §6.2）
  if (html) window.document.execCommand("insertHTML", false, DOMPurify.sanitize(html, { FORBID_ATTR: ["style", "class", "id", "data-object-id", "data-object-revision", "data-selection-id", "contenteditable", "data-word-protected"] }));
  else window.document.execCommand("insertText", false, event.clipboardData.getData("text/plain"));
  onEditorInput();
  await refreshEditorFormatting();
}
async function openSkillSettings() {
  if (!canStartAi.value || aiDraft.value) return;
  window.clearTimeout(autoSaveTimer);
  await save();
  if (!isDirty.value) skillOpen.value = true;
}
function acceptSkill(result: DocumentContentResult) {
  if (!document.value) return;
  if (result.formatTemplate) document.value.formatTemplate = result.formatTemplate;
  document.value.revision = result.revision;
  aiUndo.value = null;
  clearAiDraft();
  skillOpen.value = false;
}
async function openDiagrams() {
  if (aiLocked.value || addingHeading.value) return;
  await save();
  if (!isDirty.value) await diagrams.value?.open();
}
async function openHistory() {
  // 有未保存修改也能查看历史，只是不能恢复（02 P1）。
  if ((aiLocked.value && !historyOpen.value) || addingHeading.value) return;
  openPanel("history");
}
function openConflicts(tab?: "mine" | "members") {
  conflictTab.value = tab || (conflictCount(conflictSummary.value) > 0 ? "mine" : conflictEntry.value?.tab || "mine");
  autoConflict.value = null;
  openPanel("conflict");
}
function onConflictCount(mineOpen: number) {
  if (conflictSummary.value) conflictSummary.value = { ...conflictSummary.value, mineOpen };
  void refreshConflicts();
}
/** 处理冲突前先保存正文；仍有未保存修改则不处理（避免处理结果覆盖编辑区）。 */
async function beforeResolve(): Promise<boolean> {
  window.clearTimeout(autoSaveTimer);
  if (isDirty.value) await save("auto");
  if (savePromise) await savePromise;
  return !isDirty.value;
}
async function acceptResolution(result: ResolveResult) {
  const record = document.value;
  if (!record) return;
  conflictSummary.value = { ...(conflictSummary.value || { allOpen: null }), mineOpen: result.mineOpen, mineHandled: (conflictSummary.value?.mineHandled || 0) + result.items.length };
  // 复审 M3：原位置附近已找不到时放到了章节末尾，提醒用户检查
  if (result.items.some(entry => entry.placementFallback)) noteAction("恢复的段落原来的位置已变化，已放在所属章节末尾，请检查。", 10000);
  if (result.documentRevision === record.revision && result.markdownContent === record.markdownContent) return;
  mergeBase = null;
  await acceptContent({ ...record, revision: result.documentRevision, markdownContent: result.markdownContent } as unknown as DocumentContentResult);
  void refreshConflicts();
}
async function acceptVersion(result: DocumentContentResult) {
  historyOpen.value = false;
  clearAiDraft();
  await acceptContent(result);
  void refreshMemory();
}
async function acceptDiagram(result: DocumentContentResult & { formatTemplate: Record<string, any> }) {
  if (!document.value) return;
  document.value.formatTemplate = result.formatTemplate;
  clearAiDraft();
  await acceptContent(result);
  noteMerged(result);
}
async function leaveDocument(to?: RouteLocationNormalized) {
  if (leaveBypass) { leaveBypass = false; return true; }
  if (to && sessionEnded(to)) {
    // 会话失效或需要改密（G1）：保存已不可能。草稿写入并读回校验通过就放行跳转，重新登录后由恢复条提示；
    // 校验失败只给“下载后离开 / 留在本页”。
    window.clearTimeout(autoSaveTimer);
    window.clearTimeout(retryTimer);
    if (!isDirty.value || persistAndVerify()) return true;
    leaveDialog.value = { mode: "download-only", count: pendingCount.value, target: to.fullPath };
    return false;
  }
  if (skillOpen.value || skillBusy.value || historyBusy.value || aiBusy.value || aiInserting.value || addingHeading.value || diagramBusy.value || refreshingEditor.value || undoingAi.value) {
    noteAction("操作仍在进行，请完成或停止后再离开文档。");
    return false;
  }
  if (aiDraft.value && !window.confirm("尚未采纳的 AI 草稿会丢失，是否离开？")) return false;
  window.clearTimeout(autoSaveTimer);
  if (isDirty.value && !readonly.value) {
    // 最多等 5 秒把修改送到服务器（01 §6.3 第3条）。
    await Promise.race([save("manual"), new Promise(resolve => window.setTimeout(resolve, 5000))]);
  }
  if (!isDirty.value) return true;
  const decision = leaveDecision(pendingCount.value, persistAndVerify());
  if (decision === "leave") return true;
  leaveDialog.value = { mode: decision, count: pendingCount.value, target: to?.fullPath || router.resolve({ name: "project-documents", params: { id: projectId.value } }).fullPath };
  return false;
}
/** AI 功能不可用时的原因（02 P1-5，文案不含配置项名）；可用返回空串。 */
function aiUnavailable(feature: "writing" | "diagram"): string {
  const state = capabilities.value?.ai[feature];
  if (!state || state.available) return "";
  return capabilities.value?.ai.adminHint ? `AI 服务未配置或配置不正确（${capabilities.value.ai.adminHint}）` : "AI 服务暂不可用，请联系系统管理员";
}
function sessionEnded(to: RouteLocationNormalized) {
  return (to.name === "login" && to.query.logout !== "1") || to.name === "change-password";
}
function confirmLeave(downloadFirst: boolean) {
  const target = leaveDialog.value?.target;
  if (downloadFirst) downloadMyChanges();
  leaveDialog.value = null;
  if (!target) return;
  leaveBypass = true;
  void router.push(target).finally(() => { leaveBypass = false; });
}
function beforeUnload(event: BeforeUnloadEvent) {
  if (skillBusy.value || isDirty.value || saving.value || aiDraft.value || aiBusy.value || diagramBusy.value) {
    persistLocalDraft();
    event.preventDefault();
    event.returnValue = "";
  }
}
async function refreshEditorFormatting(saveFirst = true) {
  const root = editor.value, record = document.value;
  if (!root || !record || aiLocked.value) return;
  const selection = window.getSelection();
  const range = selection?.rangeCount ? selection.getRangeAt(0) : null;
  const selector = "p,li,h1,h2,h3,h4,h5,h6,td,th,pre,blockquote,:scope > div";
  const blocks = [...root.querySelectorAll(selector)];
  const positions: { index: number; offset: number }[] = [];
  if (range && root.contains(range.startContainer) && root.contains(range.endContainer)) {
    for (const [container, offset] of [[range.startContainer, range.startOffset], [range.endContainer, range.endOffset]] as const) {
      const element = container instanceof Element ? container : container.parentElement;
      const block = [...blocks].reverse().find(block => block === element || block.contains(container));
      if (!block) break;
      const before = range.cloneRange();
      before.selectNodeContents(block);
      before.setEnd(container, offset);
      positions.push({ index: blocks.indexOf(block), offset: (before.cloneContents().textContent || "").length });
    }
  }
  const scroll = root.closest(".page-scroll");
  const scrollTop = scroll?.scrollTop || 0;
  refreshingEditor.value = true;
  root.contentEditable = "false";
  window.clearTimeout(autoSaveTimer);
  try {
    if (saveFirst) await save();
    if (isDirty.value || document.value !== record) return;
    const preview = await previewDocument(record.id);
    if (document.value !== record || documentId.value !== record.id) return;
    showEditorHtml(preview.html);
    if (positions.length === 2) {
      const restored = window.document.createRange();
      const currentBlocks = [...root.querySelectorAll(selector)];
      for (const [boundary, position] of positions.entries()) {
        const block = currentBlocks[position.index];
        if (!block) break;
        const walker = window.document.createTreeWalker(block, NodeFilter.SHOW_TEXT);
        let remaining = Math.min(position.offset, block.textContent?.length || 0);
        let node = walker.nextNode();
        while (node) {
          const length = node.textContent?.length || 0;
          if (remaining <= length) break;
          remaining -= length;
          node = walker.nextNode();
        }
        if (boundary === 0) restored.setStart(node || block, node ? remaining : 0);
        else restored.setEnd(node || block, node ? remaining : 0);
      }
      root.contentEditable = String(editable.value);
      root.focus();
      selection?.removeAllRanges();
      selection?.addRange(restored);
    }
    if (scroll) scroll.scrollTop = scrollTop;
  } catch (error) {
    noteAction(`排版刷新失败：${messageOf(error)}`);
  } finally {
    refreshingEditor.value = false;
    if (editor.value === root) root.contentEditable = String(editable.value);
  }
  captureEditorTarget();
}
async function format(command: string, value?: string) {
  if (aiLocked.value || addingHeading.value) return;
  editor.value?.focus();
  window.document.execCommand(command, false, value);
  onEditorInput();
  await refreshEditorFormatting();
}
function scrollToHeading(index: number) {
  editor.value?.querySelectorAll("h1,h2,h3,h4,h5,h6")[index]?.scrollIntoView({ behavior: "smooth", block: "center" });
}
async function handleExport() {
  exportMenu.value = false;
  if (!document.value || exporting.value || aiLocked.value) return;
  window.clearTimeout(autoSaveTimer);
  if (isDirty.value && !readonly.value) await save("manual");
  if (isDirty.value) {
    exportError.value = "还有修改没有保存到服务器，导出的 Word 会是服务器上的版本。可以先下载“我的修改”。";
    return;
  }
  exporting.value = true;
  exportError.value = "";
  try {
    let result: { fileId: string };
    try {
      result = await exportDocument(document.value.id, document.value.revision);
    } catch (error) {
      // 导出是严格接口：他人刚更新过时先刷新到最新正文，再自动重试一次（02 §3.7 第 11 条）。
      if (!isRevisionConflict(error) || !(await refreshFromServer())) throw error;
      noteAction(`${revisionConflictText(error.details).replace("请刷新后重试。", "")}已刷新到最新内容并导出。`, 6000);
      result = await exportDocument(document.value!.id, document.value!.revision);
    }
    const link = window.document.createElement("a");
    link.href = `/api/v1/files/${result.fileId}/content`;
    link.download = `${document.value!.name}.docx`;
    window.document.body.appendChild(link);
    link.click();
    link.remove();
  } catch (error) {
    // 导出失败只影响导出，不阻断编辑（02 P0-3）。
    exportError.value = `导出失败：${messageOf(error)}`;
  } finally {
    exporting.value = false;
  }
}
async function exportServerVersion() {
  exportMenu.value = false;
  if (!document.value || exporting.value) return;
  if (!isDirty.value) return handleExport();
  exporting.value = true;
  exportError.value = "";
  try {
    const result = await exportDocument(document.value.id, document.value.revision);
    const link = window.document.createElement("a");
    link.href = `/api/v1/files/${result.fileId}/content`;
    link.download = `${document.value.name}-服务器版本.docx`;
    window.document.body.appendChild(link);
    link.click();
    link.remove();
  } catch (error) {
    exportError.value = `导出失败：${messageOf(error)}`;
  } finally {
    exporting.value = false;
  }
}
async function goBack() {
  if (aiLocked.value) return;
  void router.push({ name: "project-documents", params: { id: projectId.value } });
}
function onKeydown(event: KeyboardEvent) {
  if (event.ctrlKey && event.key.toLowerCase() === "s") {
    event.preventDefault();
    void save("manual");
  }
}
watch(documentId, () => void load());
onBeforeRouteLeave(leaveDocument);
onBeforeRouteUpdate(leaveDocument);
onMounted(() => {
  window.addEventListener("beforeunload", beforeUnload);
  window.addEventListener("pagehide", onPageHide);
  window.document.addEventListener("visibilitychange", onVisibilityChange);
  window.addEventListener("online", onOnline);
  window.addEventListener("offline", onOffline);
  clockTimer = window.setInterval(() => { now.value = Date.now(); }, 15000);
  updateTimer = window.setInterval(() => void checkOthersUpdated(), 45000);
  window.addEventListener("resize", scheduleMarks);
  void load();
});
onUnmounted(() => {
  loadVersion += 1;
  window.removeEventListener("beforeunload", beforeUnload);
  window.removeEventListener("pagehide", onPageHide);
  window.document.removeEventListener("visibilitychange", onVisibilityChange);
  window.removeEventListener("online", onOnline);
  window.removeEventListener("offline", onOffline);
  window.clearInterval(clockTimer);
  window.clearInterval(updateTimer);
  window.removeEventListener("resize", scheduleMarks);
  window.cancelAnimationFrame(marksFrame);
  window.clearTimeout(retryTimer);
  window.clearTimeout(savingTimer);
  window.clearTimeout(actionNoteTimer);
  clearAiDraft();
  window.clearTimeout(autoSaveTimer);
  originalController?.abort();
  void pdfTask?.destroy();
  closePresence();
});
</script>

<template>
  <div class="workbench" @keydown="onKeydown">
    <div v-if="phase === 'loading'" class="state">正在加载文档...</div>
    <div v-else-if="phase === 'error'" class="state" data-testid="load-error"><p>{{ errorText }}</p>
      <router-link v-if="loadErrorStatus === 404" class="ghost" :to="{ name: 'projects' }">返回项目列表</router-link>
      <button v-else type="button" class="ghost" @click="load">重试</button></div>
    <template v-else-if="document">
      <header class="toolbar">
        <div class="toolbar-left">
          <button type="button" class="ghost" :disabled="aiLocked || addingHeading" @click="goBack">← 文档列表</button>
          <span class="toolbar-separator" aria-hidden="true"></span>
          <div class="document-heading"><h1>{{ document.name }}</h1></div>
        </div>
        <div class="toolbar-right">
          <button v-if="mode === 'edit' && conflictEntry" type="button" class="ghost conflict-toggle" :class="conflictEntry.tone" data-testid="conflict-toggle" :aria-expanded="conflictOpen" @click="conflictOpen ? (conflictOpen = false) : openConflicts(conflictEntry.tab)">{{ conflictEntry.label }}</button>
          <button v-if="mode === 'edit'" type="button" class="ghost" :disabled="aiLocked || addingHeading" @click="openHistory">版本记录</button>
          <button v-if="mode === 'edit' && aiUndo" type="button" class="ghost" :disabled="!canUndoAi" @click="undoAiChange">{{ undoingAi ? '撤回中…' : '撤回 AI 修改' }}</button>
          <div class="mode-switch" role="group" aria-label="文档视图">
            <button type="button" class="ghost" :class="{ active: mode === 'original' }" :disabled="aiLocked || addingHeading" @click="switchMode('original')">查看原稿</button>
            <button type="button" class="ghost" :class="{ active: mode === 'edit' }" :disabled="aiLocked || addingHeading" @click="switchMode('edit')">编辑稿</button>
            <button type="button" class="ghost" :class="{ active: mode === 'layout' }" :disabled="aiLocked || addingHeading || saving" @click="switchMode('layout')">版式预览</button>
          </div>
          <span class="toolbar-separator" aria-hidden="true"></span>
          <a v-if="mode === 'original' && document.originalDocxFileId" class="primary" :href="`/api/v1/files/${document.originalDocxFileId}/content`" download>下载原稿</a>
          <template v-else>
            <SaveStatus :state="capsule" :label="capsuleText" :detail="capsuleDetail" @save="save('manual')" @retry="retryNow" @details="locateBlocked" />
            <span v-if="objectModel === false" class="tag plain whole-tag" data-testid="whole-save" tabindex="0" title="本文档结构较复杂，修改按整篇提交；发生冲突时按整篇对比">整篇保存</span>
            <div class="export-group">
              <button type="button" class="primary" data-testid="export-word" :disabled="exporting || saving || aiLocked || document.permissions?.canExport === false" :title="document.permissions?.canExport === false ? '你在该项目中没有导出权限' : undefined" @click="handleExport">{{ exporting ? "导出中..." : "下载编辑稿" }}</button>
              <button v-if="isDirty" type="button" class="primary export-more" aria-haspopup="menu" :aria-expanded="exportMenu" aria-label="更多下载方式" :disabled="exporting" @click="exportMenu = !exportMenu">▾</button>
              <div v-if="exportMenu" class="export-menu card" role="menu" @keydown.esc="exportMenu = false">
                <button type="button" class="ghost" role="menuitem" @click="exportServerVersion">服务器版本（Word）</button>
                <button type="button" class="ghost" role="menuitem" @click="exportMenu = false; downloadMyChanges()">我的修改（.md）</button>
              </div>
              <!-- 界面验收 S9：导出失败只在导出按钮旁说明（危险色），不占通栏 -->
              <div v-if="exportError" class="export-error" role="alert" data-testid="export-error">
                <p>{{ exportError }}</p>
                <span class="acts"><button type="button" class="text" @click="downloadMyChanges">下载我的修改</button><button type="button" class="text neutral" @click="exportError = ''">知道了</button></span>
              </div>
            </div>
          </template>
        </div>
      </header>
      <p v-if="errorText" class="error-notice" role="alert">{{ errorText }}</p>
      <div v-if="blocked" class="notice danger save-blocked" role="alert" data-testid="save-blocked">
        <span><strong>{{ readonly ? '无法保存' : '保存失败' }}</strong>{{ blocked.location ? `：${locationText(blocked.location)}。` : '：' }}{{ blocked.message }}</span>
        <span class="acts">
          <button v-if="blocked.location" type="button" class="ghost" @click="locateBlocked">定位</button>
          <button v-if="blocked.blockText && !readonly" type="button" class="ghost" data-testid="revert-block" :disabled="revertingBlock" @click="revertBlockedBlock">{{ revertingBlock ? '正在撤销…' : '撤销这一段的修改' }}</button>
          <button v-if="!readonly" type="button" class="ghost" @click="retryNow">重试保存</button>
          <button type="button" class="ghost" @click="downloadMyChanges">下载我的修改</button>
        </span>
      </div>
      <div v-else-if="storageFailed" class="notice danger save-blocked" role="alert">
        <span>浏览器存储已满，修改未能保存在本机。服务器保存仍在继续，建议先下载你的修改。<template v-if="storageUsage.length">本机共有 {{ storageUsage.length }} 份文档草稿，约 {{ (storageUsage.reduce((sum, item) => sum + item.bytes, 0) / 1048576).toFixed(1) }} MB。</template></span>
        <span class="acts">
          <button type="button" class="ghost" @click="downloadMyChanges">下载我的修改</button>
          <button v-if="otherDocDrafts.length" type="button" class="ghost" data-testid="clear-other-drafts" @click="exportAndClearOtherDrafts">下载并清理其他文档的草稿（{{ otherDocDrafts.length }} 份）</button>
        </span>
      </div>
      <div v-if="autoConflict && !conflictOpen" class="notice warning" role="status" data-testid="auto-conflict">
        <span>有 {{ autoConflict.count }} 处修改与 {{ autoConflict.name }} 的修改冲突，已单独保存，不会丢失。</span>
        <span class="acts"><button type="button" class="ghost" @click="openConflicts('mine')">查看冲突</button></span>
      </div>
      <div v-if="othersUpdated" class="notice info" role="status" data-testid="others-updated">
        <span>{{ othersUpdatedText(othersUpdated.names, othersUpdated.objects, false) }}</span>
        <span class="acts">
          <button type="button" class="ghost" :disabled="saving || Boolean(blocked) || offline" :title="blocked || offline ? '需要先保存你的修改' : undefined" @click="refreshOthersNow">{{ saving ? "保存后刷新…" : "立即刷新" }}</button>
          <button type="button" class="ghost" @click="openHistory">查看修改记录</button>
          <button type="button" class="subtle" aria-label="关闭提示" @click="othersUpdated = null">×</button>
        </span>
      </div>
      <p v-if="actionNote" class="notice info" role="status">{{ actionNote }}</p>
      <p v-if="multiTab && !localDraft && !blocked" class="notice info" role="status" data-testid="multi-tab">此文档已在另一个标签页打开，两处的修改会分别保存，可能产生冲突。 <button type="button" class="ghost" @click="dismissMultiTab">知道了</button></p>
      <div v-if="localDraft" class="draft-recovery" role="status" data-testid="draft-recovery">
        <div class="draft-row">
          <span>{{ recoverySummary(localDraft) }}{{ localDraft.docRevision === document.revision ? '' : '服务器上的文档在这之后被修改过，恢复时会合并，改到同一段的会列为冲突。' }}</span>
          <label v-if="localDrafts.length > 1" class="draft-pick">本机共 {{ localDrafts.length }} 份
            <select class="control" :value="localDrafts.indexOf(localDraft)" :disabled="draftBusy" @change="chooseLocalDraft(Number(($event.target as HTMLSelectElement).value))">
              <option v-for="(item, index) in localDrafts" :key="item.tabId" :value="index">{{ draftTime(item) }}（{{ item.changed || 1 }} 处）</option>
            </select>
          </label>
          <template v-if="draftConfirmDiscard">
            <span class="draft-confirm">舍弃后这份本机修改将无法恢复。</span>
            <button type="button" class="ghost" @click="draftConfirmDiscard = false">返回</button>
            <button type="button" class="primary danger" data-testid="draft-discard-confirm" @click="discardLocalDraft">舍弃</button>
          </template>
          <template v-else>
            <button type="button" class="ghost" :aria-expanded="draftDiffOpen" @click="draftDiffOpen = !draftDiffOpen">查看差异</button>
            <button type="button" class="primary" :disabled="draftBusy || readonly" @click="restoreLocalDraft">{{ draftBusy ? '恢复中…' : '恢复并保存' }}</button>
            <button type="button" class="ghost" @click="downloadLocalDraft">下载</button>
            <button type="button" class="ghost" @click="discardLocalDraft">舍弃</button>
          </template>
        </div>
        <p class="draft-hint">先处理本机草稿（恢复、下载或舍弃），再继续编辑；处理前编辑区暂时锁定，避免两份修改互相覆盖。</p>
        <div v-if="draftDiffOpen" class="draft-diff" aria-label="本机草稿与服务器正文的差异">
          <p v-if="!draftDiff.added.length && !draftDiff.removed.length">与服务器正文相同。</p>
          <pre v-for="(block, index) in draftDiff.added" :key="`a${index}`" class="ins">{{ block }}</pre>
          <pre v-for="(block, index) in draftDiff.removed" :key="`r${index}`" class="del">{{ block }}</pre>
        </div>
      </div>
      <DocumentDiagrams ref="diagrams" :document="document" :headings="outline" :heading-index="selectedHeading" @inserted="acceptDiagram" @busy="diagramBusy = $event" @conflict="derivedConflict" />
      <div class="workspace" :class="{ 'original-mode': mode !== 'edit', 'ai-open': mode === 'edit' && aiOpen, 'history-open': historyOpen || (mode === 'edit' && conflictOpen) }">
        <div v-if="mode !== 'edit'" class="original-viewer">
          <div v-if="originalPhase === 'loading'" class="preview-state">正在使用 Word 生成{{ mode === 'layout' ? '编辑稿' : '原稿' }}预览...</div>
          <div v-else-if="originalPhase === 'error'" class="preview-state" role="alert">
            <p>{{ originalError }}</p>
            <button type="button" class="ghost" @click="loadOriginal()">重试预览</button>
          </div>
          <div v-else class="pdf-viewer">
            <div class="page-controls" aria-label="Word 翻页">
              <button type="button" class="ghost" :disabled="pageNumber <= 1 || renderingPage" @click="goPage(-1)">上一页</button>
              <span>第 {{ pageNumber }} / {{ pageCount }} 页</span>
              <button type="button" class="ghost" :disabled="pageNumber >= pageCount || renderingPage" @click="goPage(1)">下一页</button>
            </div>
            <div class="pdf-scroll"><canvas ref="pageCanvas" role="img" :aria-label="`Word ${mode === 'layout' ? '编辑稿' : '原稿'}第 ${pageNumber} 页`"></canvas></div>
          </div>
        </div>
        <aside v-show="mode === 'edit'" class="outline-panel" aria-label="文档大纲">
          <h2>文档大纲</h2>
          <p v-if="outline.length === 0" class="empty-outline">暂无标题</p>
          <nav v-else class="outline-tree">
            <button v-for="(item, index) in outline" :key="index" type="button" class="ghost outline-item" :disabled="aiLocked || addingHeading" :class="{ selected: selectedHeading === index }" :aria-current="selectedHeading === index ? 'true' : undefined" :style="{ paddingLeft: `calc(var(--space-2) + (var(--space-3) * ${item.level - 1}))` }" @click="selectHeading(index)">{{ item.text }}<span v-if="conflictHeadings.has(item.text)" class="outline-dot conflict" title="本章节有冲突待处理" aria-label="有冲突"></span><span v-else-if="updatedHeadings.has(item.text)" class="outline-dot updated" title="他人更新了本章节" aria-label="有更新"></span></button>
          </nav>
          <form class="outline-form" @submit.prevent="addHeading">
            <h3>添加标题</h3>
            <label for="new-heading">标题名称</label>
            <input id="new-heading" v-model="headingTitle" class="control" type="text" maxlength="120" :disabled="addingHeading || saving || aiLocked || readonly" required />
            <label v-if="selectedHeading !== null" for="heading-placement">标题层级</label>
            <select v-if="selectedHeading !== null" id="heading-placement" v-model="headingPlacement" class="control" :disabled="addingHeading || saving || aiLocked || readonly">
              <option value="child" :disabled="(outline[selectedHeading]?.level || 0) >= 6">下级标题</option>
              <option value="sibling">同级标题</option>
            </select>
            <p v-if="headingError" class="form-error" role="alert">{{ headingError }}</p>
            <button type="submit" class="primary" :disabled="addingHeading || saving || aiLocked || readonly || !headingTitle.trim()">{{ addingHeading ? "添加中..." : "添加到大纲" }}</button>
          </form>
        </aside>
        <main v-show="mode === 'edit'" class="editor-area">
          <fieldset class="editor-toolbar" aria-label="文字格式" :disabled="aiLocked || addingHeading || readonly">
            <button type="button" class="ghost" title="一级标题" @mousedown.prevent @click="format('formatBlock', 'h1')">标题 1</button>
            <button type="button" class="ghost" title="二级标题" @mousedown.prevent @click="format('formatBlock', 'h2')">标题 2</button>
            <button type="button" class="ghost" title="正文" @mousedown.prevent @click="format('formatBlock', 'p')">正文</button>
            <span class="toolbar-separator" aria-hidden="true"></span>
            <button type="button" class="ghost" title="加粗" @mousedown.prevent @click="format('bold')"><strong>B</strong></button>
            <button type="button" class="ghost" title="斜体" @mousedown.prevent @click="format('italic')"><em>I</em></button>
            <span class="toolbar-separator" aria-hidden="true"></span>
            <button type="button" class="ghost" title="项目符号" @mousedown.prevent @click="format('insertUnorderedList')">项目符号</button>
            <button type="button" class="ghost" title="编号列表" @mousedown.prevent @click="format('insertOrderedList')">编号列表</button>
            <button type="button" class="primary" :aria-expanded="aiOpen" :disabled="readonly || Boolean(aiUnavailable('writing'))" :title="aiUnavailable('writing') || undefined" @mousedown.prevent @click="aiOpen = !aiOpen">写作助手</button>
            <button type="button" class="ghost" :disabled="readonly || !canStartAi || Boolean(aiDraft)" @mousedown.prevent @click="openSkillSettings">编写规范</button>
            <button type="button" class="ghost" :disabled="saving || addingHeading || aiLocked || Boolean(aiUnavailable('diagram'))" :title="aiUnavailable('diagram') || undefined" @mousedown.prevent @click="openDiagrams">Visio 图表</button>
          </fieldset>
          <div class="page-scroll"><div class="paper" :style="paperStyle"><div ref="editor" class="paper-content" :style="{ minHeight: paperStyle.minHeight }" :contenteditable="editable" :aria-readonly="!editable" role="textbox" aria-label="文档正文" aria-multiline="true" data-placeholder="点击此处开始编写文档" spellcheck="false" @input="onEditorInput" @paste="pasteContent" @drop="blockUnsupportedImages" @dragstart="blockInternalDrag" @mouseup="captureEditorTarget" @keyup="captureEditorTarget" @click="clearUpdatedAt($event.target)"></div>
            <div v-if="conflictMarks.length" class="conflict-marks" aria-label="冲突标记">
              <button v-for="mark in conflictMarks" :key="mark.key" type="button" class="conflict-mark" data-testid="conflict-mark" :style="{ top: `${mark.top}px` }" @click="openConflictCard(mark.key)">{{ mark.label }}</button>
            </div></div></div>
        </main>
        <aside v-if="mode === 'edit' && aiOpen" class="ai-panel" aria-label="写作助手">
          <div class="ai-panel-head"><h2>写作助手</h2><button type="button" class="ghost" aria-label="关闭写作助手" @click="aiOpen = false">收起</button></div>
          <div class="scope-switch" role="group" aria-label="编写范围">
            <button type="button" class="ghost" :aria-pressed="aiScope === 'section'" :disabled="aiLocked" @click="chooseAiScope('section')">章节</button>
            <button type="button" class="ghost" :aria-pressed="aiScope === 'selection'" :disabled="aiLocked || !selectedText" @click="chooseAiScope('selection')">选中文字</button>
            <button type="button" class="ghost" :aria-pressed="aiScope === 'document'" :disabled="aiLocked" @click="chooseAiScope('document')">全文</button>
          </div>
          <p v-if="!canGenerate" class="empty-outline">选择标题或选中正文。</p>
          <p v-if="!canGenerate && aiError" class="form-error" role="alert">{{ aiError }}</p>
          <template v-if="canGenerate">
            <p class="ai-target">{{ aiScope === 'document' ? document.name : aiScope === 'selection' ? '选中内容' : outline[selectedHeading!]?.text }}</p>
            <blockquote v-if="aiScope === 'selection'" class="selected-text">{{ selectedText }}</blockquote>
            <form class="ai-form" @submit.prevent="generateSection">
              <label for="ai-instruction">编写要求（选填）</label>
              <textarea id="ai-instruction" v-model="aiInstruction" class="control" rows="4" maxlength="2000" :disabled="!canStartAi"></textarea>
              <button type="submit" class="primary" :disabled="!canStartAi">{{ aiBusy ? "正在编写…" : aiDraft ? "重新生成" : "生成草稿" }}</button>
            </form>
            <section v-if="aiProgress.length" class="ai-progress" aria-label="编写进度">
              <p class="ai-progress-head"><strong role="status" aria-live="polite">{{ aiProgressLabel }}</strong><span>已用 {{ aiElapsed }} 秒</span></p>
              <details :open="aiBusy">
                <summary>执行步骤</summary>
                <ol><li v-for="(step, index) in aiProgress" :key="index" :class="{ current: aiBusy && index === aiProgress.length - 1 }">{{ step.message }}</li></ol>
              </details>
            </section>
            <p v-if="aiError" class="form-error" role="alert">{{ aiError }}</p>
            <button v-if="selectionNeedsRefresh" type="button" class="ghost" :disabled="!canStartAi || isDirty" @click="refreshSelectionEditor">刷新编辑稿</button>
          </template>
          <details class="writing-memory">
            <summary>编写记忆</summary>
            <p v-if="memoryLoading" class="empty-outline" role="status">正在读取…</p>
            <div v-else-if="memoryError"><p class="form-error" role="alert">{{ memoryError }}</p><button type="button" class="ghost" @click="refreshMemory(memoryOffset)">重试</button></div>
            <template v-else-if="writingMemory">
              <pre class="memory-content">{{ writingMemory }}</pre>
              <nav v-if="memoryOffset > 0 || memoryHasMore" class="memory-pages" aria-label="编写记忆分页">
                <button type="button" class="ghost" :disabled="memoryLoading || memoryOffset === 0" @click="refreshMemory(memoryOffset - 10)">上一页</button>
                <button type="button" class="ghost" :disabled="memoryLoading || !memoryHasMore" @click="refreshMemory(memoryOffset + 10)">下一页</button>
              </nav>
              <button type="button" class="ghost" :disabled="aiLocked" @click="removeMemory">清除记忆</button>
            </template>
            <p v-else class="empty-outline">暂无编写记忆</p>
          </details>
        </aside>
        <DocumentSkillSettings v-if="skillOpen" :key="document.id" :document-id="document.id" :revision="document.revision" :configuration="document.formatTemplate.writingSkill" :refresh="refreshFromServer" @close="skillOpen = false" @busy="skillBusy = $event" @saved="acceptSkill" />
        <DocumentConflictPanel v-if="mode === 'edit' && conflictOpen" ref="conflictPanel" :key="`${document.id}:${conflictTab}`" :document-id="document.id" :document-name="document.name" :revision="document.revision"
          :can-view-members="conflictSummary?.allOpen != null" :can-resolve="!readonly" :before-resolve="beforeResolve"
          :initial-tab="conflictTab" :mine-count="mineConflicts" :member-count="memberConflicts"
          @close="conflictOpen = false" @resolved="acceptResolution" @count="onConflictCount" @locate="locateConflict" />
        <DocumentHistory v-if="historyOpen" :document-id="document.id" :revision="document.revision" :restore-blocked-reason="historyRestoreBlocked" @close="historyOpen = false" @restored="acceptVersion" @busy="historyBusy = $event" @refresh="refreshFromServer().catch(() => undefined)" />
        <Teleport v-if="aiPreviewHost" :to="aiPreviewHost">
          <section class="ai-preview" aria-label="AI 实时预览" @input.stop @mouseup.stop @keyup.stop>
            <div class="ai-preview-tools">
              <span class="ai-preview-status" role="status">{{ aiBusy ? aiProgressLabel : aiStopped ? '已停止' : aiComplete ? 'AI 草稿' : '生成未完成' }}</span>
              <button v-if="aiBusy" type="button" class="ghost" @click="stopAi">停止</button>
              <template v-else>
                <button type="button" class="primary" :disabled="!canApplyDraft || (aiScope === 'section' && !aiAllowedModes.includes('replace'))" @click="insertSection('replace')">{{ aiInserting ? '写入中…' : aiScope === 'document' ? '应用全文修改' : aiScope === 'selection' ? '替换选中内容' : '替换本节正文' }}</button>
                <button v-if="aiScope === 'section'" type="button" class="ghost" :disabled="!canApplyDraft || !aiAllowedModes.includes('append')" @click="insertSection('append')">追加到本节</button>
                <p v-if="aiComplete && aiScope === 'section' && !aiAllowedModes.includes('replace')" class="form-error" role="status">本节包含特殊 Word 格式，可追加正文。</p>
                <button type="button" class="ghost" :disabled="aiInserting" @click="clearAiDraft">取消</button>
              </template>
            </div>
            <div class="ai-preview-body" :class="{ writing: aiBusy }" aria-label="AI 正文" v-html="aiPreviewHtml"></div>
          </section>
        </Teleport>
      </div>
      <LeaveDialog v-if="leaveDialog" :mode="leaveDialog.mode" :count="leaveDialog.count" @download="downloadMyChanges" @stay="leaveDialog = null" @leave="confirmLeave(false)" @download-leave="confirmLeave(true)" />
    </template>
  </div>
</template>

<style scoped>
.workbench { display:flex; flex-direction:column; height:calc(100vh - var(--space-7) - var(--space-3) - var(--space-5) * 2); min-height:0; background:var(--desk); font-family:var(--font); border:1px solid var(--rule); }
.state { display:flex; flex:1; align-items:center; justify-content:center; flex-direction:column; gap:var(--space-3); color:var(--ink-secondary); }
.toolbar { display:flex; justify-content:space-between; align-items:center; min-height:calc(var(--space-7) + var(--space-5)); padding:var(--space-2) var(--space-5); background:var(--sheet); border-bottom:1px solid var(--rule); }
.toolbar-left,.toolbar-right { display:flex; align-items:center; gap:var(--space-3); min-width:0; }
.mode-switch { display:flex; gap:var(--space-1); }
.mode-switch .active { border-color:var(--stamp); background:var(--stamp-wash); color:var(--stamp); }
.toolbar-separator { display:block; width:1px; align-self:stretch; background:var(--rule-strong); }
.document-heading { min-width:0; }
.document-heading h1 { margin:0; font-size:var(--text-md); font-weight:600; overflow-wrap:anywhere; }
.error-notice { margin:0; padding:var(--space-2) var(--space-5); background:var(--fault-wash); color:var(--fault-ink); border-bottom:1px solid var(--fault); }
.notice { display:flex; align-items:center; gap:var(--space-2); min-height:40px; margin:0; padding:var(--space-1) var(--space-4); font-size:var(--text-sm); border-bottom:1px solid var(--border-default, var(--rule)); }
.notice.info { background:var(--info-50, var(--sheet)); color:var(--gray-800, var(--ink)); }
.notice.danger { background:var(--danger-50, var(--fault-wash)); color:var(--danger-700, var(--fault-ink)); }
.notice.warning { background:var(--warning-50); color:var(--gray-800); }
.whole-tag { cursor:help; }
.export-error { position:absolute; top:calc(100% + var(--space-1)); right:0; z-index:21; display:grid; gap:var(--space-2); width:calc(var(--space-7) * 8); padding:var(--space-3); background:var(--bg-surface); border:1px solid var(--danger-200); border-radius:var(--radius-control); box-shadow:var(--shadow-2); color:var(--danger-600); font-size:var(--text-sm); }
.export-error p { margin:0; overflow-wrap:anywhere; }
.export-error .acts { display:flex; gap:var(--space-3); justify-content:flex-end; }
.conflict-toggle.warning { color:var(--warning-600); }
.conflict-toggle.neutral { color:var(--text-secondary); }
.outline-dot { display:inline-block; width:6px; height:6px; margin-left:var(--space-1); border-radius:var(--radius-pill); vertical-align:middle; }
.outline-dot.conflict { background:var(--danger-600); }
.outline-dot.updated { background:var(--info-600); }
.paper { position:relative; }
.conflict-marks { position:absolute; inset:0; pointer-events:none; }
.conflict-mark { position:absolute; right:var(--space-2); transform:translateY(-100%); height:auto; padding:0 var(--space-2); border:0; border-radius:var(--radius-control); background:var(--danger-50); color:var(--danger-600); font-size:var(--text-xs); line-height:var(--leading-sm, 1.6); pointer-events:auto; cursor:pointer; }
.paper-content :deep(.blk-conflict) { box-shadow:inset 3px 0 0 var(--danger-600); background:var(--danger-50); padding-left:var(--space-2); }
.paper-content :deep(.blk-updated) { box-shadow:inset 3px 0 0 var(--info-200); padding-left:var(--space-2); }
.paper-content :deep(.blk-flash) { outline:2px solid var(--brand-200); outline-offset:2px; }
.draft-row { display:flex; align-items:center; flex-wrap:wrap; gap:var(--space-3); }
.draft-pick { display:flex; align-items:center; gap:var(--space-1); color:var(--text-secondary); }
.draft-pick .control { width:auto; }
.draft-confirm { color:var(--danger-600); }
.draft-hint { margin:var(--space-1) 0 0; color:var(--text-secondary); font-size:var(--text-xs); }
.draft-diff { display:grid; gap:var(--space-1); margin-top:var(--space-2); max-height:calc(var(--space-7) * 6); overflow:auto; }
.draft-diff p { margin:0; color:var(--text-secondary); }
.draft-diff pre { margin:0; padding:var(--space-1) var(--space-2); white-space:pre-wrap; overflow-wrap:anywhere; font-family:inherit; border-radius:var(--radius-control); }
.draft-diff pre.ins { background:var(--diff-ins-bg); }
.draft-diff pre.del { background:var(--diff-del-bg); text-decoration:line-through; }
.notice .acts { margin-left:auto; display:flex; gap:var(--space-2); align-items:center; flex-shrink:0; }
.notice > span:first-child { overflow-wrap:anywhere; }
.export-group { position:relative; display:flex; gap:1px; }
.export-more { padding-inline:var(--space-2); }
.export-menu { position:absolute; top:calc(100% + var(--space-1)); right:0; z-index:20; display:flex; flex-direction:column; min-width:180px; padding:var(--space-1); background:var(--bg-surface, var(--sheet)); border:1px solid var(--border-default, var(--rule)); }
.export-menu button { justify-content:flex-start; text-align:left; border:0; }
.draft-recovery { padding:var(--space-2) var(--space-5); background:var(--sheet); border-bottom:1px solid var(--rule); font-size:var(--text-sm); }
.draft-row > span:first-child { margin-right:auto; overflow-wrap:anywhere; }
.workspace { display:grid; grid-template-columns:calc(var(--space-7) * 5 + var(--space-5)) minmax(0,1fr); flex:1; min-height:0; }
.workspace.original-mode { grid-template-columns:minmax(0,1fr); }
/* 审查 S4：右侧面板统一 --panel-w（400） */
.workspace.ai-open,
.workspace.history-open { grid-template-columns:calc(var(--space-7) * 5 + var(--space-5)) minmax(0,1fr) var(--panel-w); }
.workspace.original-mode.history-open { grid-template-columns:minmax(0,1fr) var(--panel-w); }
.original-viewer { display:flex; flex-direction:column; min-width:0; min-height:0; background:var(--desk); }
.preview-state { display:flex; flex:1; flex-direction:column; align-items:center; justify-content:center; gap:var(--space-3); color:var(--ink-secondary); }
.pdf-viewer { display:flex; flex:1; flex-direction:column; min-height:0; }
.page-controls { display:flex; align-items:center; justify-content:center; gap:var(--space-3); padding:var(--space-2); background:var(--sheet); border-bottom:1px solid var(--rule); color:var(--ink-secondary); font-size:var(--text-sm); }
.pdf-scroll { flex:1; min-height:0; overflow:auto; padding:var(--space-5); text-align:center; }
.pdf-scroll canvas { display:block; max-width:100%; height:auto; margin:0 auto; background:var(--sheet); border:1px solid var(--rule); box-shadow:0 var(--space-1) var(--space-4) var(--rule); }
.outline-panel { display:flex; flex-direction:column; min-height:0; overflow:hidden; padding:var(--space-5) var(--space-3); background:var(--sheet); border-right:1px solid var(--rule); }
.outline-panel h2 { margin:0 0 var(--space-4); padding:0 var(--space-2); color:var(--ink-secondary); font-size:var(--text-sm); font-weight:600; }
.empty-outline { padding:0 var(--space-2); color:var(--ink-tertiary); font-size:var(--text-sm); }
.outline-tree { display:flex; flex:1; min-height:0; flex-direction:column; gap:var(--space-1); overflow-y:auto; scrollbar-color:var(--rule-strong) transparent; }
.outline-item { width:100%; height:auto; min-height:var(--space-6); border:0; background:transparent; font-weight:400; text-align:left; white-space:normal; overflow-wrap:anywhere; line-height:1.5; }
.outline-item:hover { color:var(--stamp); background:var(--stamp-wash); }
.outline-item.selected { color:var(--stamp); background:var(--stamp-wash); font-weight:600; }
.outline-form { display:flex; flex-direction:column; gap:var(--space-2); margin-top:var(--space-5); padding:var(--space-4) var(--space-2) 0; border-top:1px solid var(--rule); }
.outline-form h3 { margin:0; font-size:var(--text-sm); }
.outline-form label,.ai-form label { color:var(--ink-secondary); font-size:var(--text-sm); font-weight:600; }
.outline-form .primary,.ai-form .primary { align-self:flex-start; }
.form-error { margin:0; color:var(--fault-ink); font-size:var(--text-sm); overflow-wrap:anywhere; }
.ai-panel { display:flex; flex-direction:column; gap:var(--space-4); min-height:0; overflow-y:auto; padding:var(--space-5) var(--space-4); background:var(--sheet); border-left:1px solid var(--rule); }
.ai-panel-head { display:flex; align-items:center; justify-content:space-between; gap:var(--space-2); }
.ai-panel-head h2 { margin:0; font-size:calc(var(--text-md) * 1.25); }
.scope-switch { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:var(--space-1); }
.scope-switch .ghost { padding:var(--space-1); }
.scope-switch [aria-pressed="true"] { color:var(--stamp); border-color:var(--stamp); background:var(--stamp-wash); }
.writing-memory { margin-top:auto; padding-top:var(--space-4); border-top:1px solid var(--rule); font-size:var(--text-sm); }
.writing-memory summary { cursor:pointer; font-weight:600; }
.writing-memory summary:focus-visible { outline:var(--space-1) solid var(--focus-ring); }
.memory-content { margin:var(--space-3) 0; max-height:calc(var(--space-7) * 6); overflow-y:auto; white-space:pre-wrap; overflow-wrap:anywhere; font-family:var(--font); font-size:var(--text-sm); line-height:1.6; color:var(--ink-secondary); }
.memory-pages { display:flex; gap:var(--space-2); margin-bottom:var(--space-3); }
.ai-target { margin:0; padding-bottom:var(--space-3); border-bottom:1px solid var(--rule); color:var(--ink); font-size:var(--text-sm); font-weight:600; overflow-wrap:anywhere; }
.selected-text { margin:0; padding:var(--space-3); max-height:calc(var(--space-7) * 3); overflow-y:auto; background:var(--desk); border-left:var(--space-1) solid var(--stamp); color:var(--ink-secondary); font-size:var(--text-sm); white-space:pre-wrap; overflow-wrap:anywhere; }
.ai-form { display:flex; flex-direction:column; gap:var(--space-3); }
.ai-progress { border-top:1px solid var(--rule); padding-top:var(--space-3); font-size:var(--text-sm); }
.ai-progress-head { display:flex; align-items:baseline; gap:var(--space-2); margin:0 0 var(--space-2); }
.ai-progress-head strong { flex:1; min-width:0; color:var(--ink); overflow-wrap:anywhere; }
.ai-progress-head span { flex:none; color:var(--ink-secondary); }
.ai-progress summary { width:fit-content; cursor:pointer; color:var(--ink-secondary); }
.ai-progress summary:focus-visible { outline:var(--space-1) solid var(--focus-ring); }
.ai-progress ol { display:flex; flex-direction:column; gap:var(--space-2); margin:var(--space-3) 0 0; padding-left:var(--space-5); max-height:calc(var(--space-7) * 4); overflow-y:auto; color:var(--ink-secondary); }
.ai-progress li { padding-left:var(--space-1); overflow-wrap:anywhere; }
.ai-progress li.current { color:var(--stamp); font-weight:600; }
.ai-panel textarea.control { width:100%; min-height:calc(var(--space-7) * 2); resize:vertical; }
.ai-preview { margin:var(--space-4) 0; padding-left:var(--space-4); border-left:var(--space-1) solid var(--stamp); }
.ai-preview-tools { position:sticky; top:0; z-index:1; display:flex; align-items:center; flex-wrap:wrap; gap:var(--space-2); padding:var(--space-2) 0; background:var(--sheet); border-bottom:1px solid var(--rule); }
.ai-preview-status { margin-right:auto; color:var(--stamp); font-size:var(--text-sm); font-weight:600; }
.ai-preview-body { padding-top:var(--space-3); min-height:var(--space-7); }
.ai-preview-body.writing::after { content:''; display:inline-block; width:1px; height:1em; background:var(--stamp); }
.editor-area { display:flex; flex-direction:column; min-width:0; min-height:0; }
.editor-toolbar { display:flex; align-items:center; flex-wrap:wrap; gap:var(--space-2); min-width:0; margin:0; padding:var(--space-2) var(--space-5); background:var(--sheet); border:0; border-bottom:1px solid var(--rule); }
.editor-toolbar .ghost { min-width:var(--space-6); }
.page-scroll { flex:1; min-height:0; overflow:auto; padding:var(--space-5) var(--space-4); }
.paper { width:calc(var(--space-7) * 18); min-height:calc(var(--space-7) * 24); margin:0 auto var(--space-7); padding:var(--space-7) calc(var(--space-7) * 1.5); background:var(--sheet); border:1px solid var(--rule); box-shadow:0 var(--space-1) var(--space-4) var(--rule); }
.paper-content { min-height:calc(var(--space-7) * 20); outline:none; color:var(--ink); font-family:var(--font); font-size:var(--text-sm); line-height:1.85; overflow-wrap:anywhere; }
.paper-content:focus-visible { box-shadow:0 0 0 var(--space-1) var(--focus-ring); }
.paper-content:empty::before { content:attr(data-placeholder); color:var(--ink-tertiary); }
.paper-content :deep(h1),.paper-content :deep(h2),.paper-content :deep(h3),.paper-content :deep(h4) { color:var(--ink); font-family:var(--font); font-weight:600; line-height:1.45; }
.paper-content :deep(h1) { margin:0 0 var(--space-5); font-size:calc(var(--text-md) * 1.5); text-align:center; }
.paper-content :deep(h2) { margin:var(--space-5) 0 var(--space-3); font-size:calc(var(--text-md) * 1.25); }
.paper-content :deep(h3) { margin:var(--space-4) 0 var(--space-2); font-size:var(--text-md); }
.paper-content :deep(p) { margin:0 0 var(--space-3); }
.paper-content :deep(ul),.paper-content :deep(ol) { padding-left:var(--space-5); margin:0 0 var(--space-3); }
.paper-content :deep(table) { width:100%; border-collapse:collapse; margin:var(--space-4) 0; table-layout:fixed; }
.paper-content :deep(th),.paper-content :deep(td) { padding:var(--space-2); border:1px solid var(--rule-strong); text-align:left; vertical-align:top; overflow-wrap:anywhere; }
.paper-content :deep(th) { background:var(--desk); font-weight:600; }
.paper-content :deep(img) { display:block; max-width:100%; max-height:calc(var(--space-7) * 14); width:auto; height:auto; object-fit:contain; margin:var(--space-4) auto; }
.paper-content :deep(a) { color:var(--stamp); }
.paper-content :deep([data-word-protected]) { cursor:not-allowed; color:var(--text-secondary); background:var(--bg-subtle); }
.paper-content :deep([data-word-marker])::before { content:attr(data-word-marker); display:inline-block; margin-right:var(--space-2); }
button:focus-visible { outline:var(--space-1) solid var(--focus-ring); outline-offset:var(--space-1); }
</style>
