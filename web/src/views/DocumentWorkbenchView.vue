<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, shallowRef, watch } from "vue";
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter, type RouteLocationNormalized } from "vue-router";
import { readSession } from "../session";
import DocumentDiagrams from "../components/DocumentDiagrams.vue";
import DocumentHistory from "../components/DocumentHistory.vue";
import DocumentAssignments from "../components/DocumentAssignments.vue";
import DocumentBlockGuidance from "../components/DocumentBlockGuidance.vue";
import DocumentReferences from "../components/DocumentReferences.vue";
import type { DocumentReferencesState } from "../api/documentReferences";
import { useGuidanceBrowser } from "../useGuidanceBrowser";
import { getDocumentAssignments, type DocumentAssignmentsState } from "../api/documentAssignments";
import DocumentSkillSettings from "../components/DocumentSkillSettings.vue";
import SaveStatus from "../components/SaveStatus.vue";
import LeaveDialog from "../components/LeaveDialog.vue";
import DocumentConflictPanel from "../components/DocumentConflictPanel.vue";
import { changedByText, conflictCount, conflictOpensPanel, conflictToggle, isWholeConflict, othersUpdatedText, SELF_THEIRS_NAME, theirsName, WHOLE_THEIRS_NAME, wholeConflictNote } from "../conflictModel";
import { otherTabOpen, sameTabOtherPage } from "../documentObjects";
import { readonlyBlockedText, readonlyText } from "../memberModel";
import { type WhitespaceNotes, whitespaceNoteText, accessText, backoffMs, capsuleLabel, capsuleState, changedBlockCount, classifySaveError, draftSizeText, isOffline, leaveDecision, locationText, myChangesFileName, revisionConflictText, shortTime, type LeaveDecision, type LocationInfo } from "../saveModel";
import { countChars, caretBlockIndex, keepsServerChanges, mapBlockIndex, mergedInBlocks, outlineEntries, rebaseBlocks, typedInsertion } from "../workbenchStats";
import { draftUsage, listDrafts, readDraft, recoverySummary, removeDraft, verifyDraft, writeDraft, type DraftV2 } from "../draftStore";
import { browserTabId, browserTabIdentity, pageInstance } from "../tabIdentity";
import { revertBlock } from "../blockRevert";
import { frozenMark, looksLikePinyin, nextSeq, noteYields, substituteFrozen, type NoteKind } from "../frozenConflict";
import { flash } from "../flash";
import DOMPurify from "dompurify";
import type { PDFDocumentLoadingTask, PDFDocumentProxy, RenderTask } from "pdfjs-dist";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { createTurndown, editorMarkdown, storedImageRule } from "../editorMarkdown";
import { ApiError } from "../api/client";
import { assignBlockIds, splitBlocks, type BaselineObject } from "../markdownBlocks";
import { getAllObjects, getAssignments, getRejectedSave, putRejectedSave } from "../api/assignments";
import { addStashEntry, parseStash, serializeStash, stashAll, type StashEntry } from "../stashStore";
import { keepMineOnly, lockedText, mineRegions, noAssignmentText, ownerNames, stashText, type Violation } from "../assignmentModel";
import SaveRejectedPanel from "../components/SaveRejectedPanel.vue";
import AssignmentPanel from "../components/AssignmentPanel.vue";
import { getCapabilities, getConflictSummary, getObjectsSince, listConflicts, findChangeBySaveKey, saveConflictMine, type Capabilities, type ConflictItem, type ConflictSummary, type MineLatest, type ResolveResult } from "../api/documents";
import { listDocumentVersions } from "../api/documents";
import { renderSuggestions, resolveSuggestion, pending as pendingSuggestions, sectionBlocks, type Suggestion, type SuggestionCallbacks } from "../aiSuggestions";
import { addDocumentHeading, documentHeadings, streamDocumentSection, exportDocument, getDocument, insertDocumentSection, previewDocument, updateDocumentContent, getWritingMemory, clearWritingMemory, applyDocumentEdits, replaceDocumentSelection, queryWritingGuidance, updateWritingGuidance, type WritingGuidanceGroup, type WritingGuidanceItem, type DocumentSelection, type DocumentContentResult, type DocumentRecord, type DocumentEdit, type WritingMemory } from "../api/documents";

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
const pdfScroll = ref<HTMLElement | null>(null);
const pdfPages = ref<{ number: number; width: number; height: number; ready: boolean }[]>([]);
let pageObserver: IntersectionObserver | undefined;
const visiblePages = new Set<HTMLCanvasElement>();
const pageRenders = new Map<HTMLCanvasElement, { cancelled: boolean; task?: RenderTask }>();
let pdfDocument: PDFDocumentProxy | null = null;
let pdfTask: PDFDocumentLoadingTask | null = null;
let originalController: AbortController | undefined;
const markdownContent = ref("");
const savedContent = ref("");
const phase = ref<"loading" | "ready" | "error">("loading");
const loadErrorStatus = ref(0);
const errorText = ref("");
const orphanDrafts = ref<DraftV2[]>([]);
const orphanStash = ref("");
function downloadOrphan() {
  const parts = orphanDrafts.value.map(draft => draft.markdown);
  if (orphanStash.value) parts.push(`<!-- 暂存（不归你负责的内容） -->\n\n${orphanStash.value}`);
  if (parts.length) download(myChangesFileName("本机草稿", new Date()), parts.join("\n\n---\n\n") + "\n");
}
const saving = ref(false);
let savePromise: Promise<void> | undefined;
let loadVersion = 0;
const diagrams = ref<InstanceType<typeof DocumentDiagrams> | null>(null);
const diagramBusy = ref(false);
// 界面验收 M7：写作助手、版本记录、冲突面板互斥，统一由 rightPanel 管理（02 §3.4 “与版本记录、写作助手互斥”）。
type RightPanel = "ai" | "history" | "conflict" | "assignments" | "references" | "assign" | "rejected" | null;
const rightPanel = ref<RightPanel>(null);
const historyBusy = ref(false);
const assignmentsPanel = ref<InstanceType<typeof DocumentAssignments> | null>(null);
const blockGuidancePanel = ref<InstanceType<typeof DocumentBlockGuidance> | null>(null);
const assignmentState = ref<DocumentAssignmentsState | null>(null);
const assignmentsBusy = ref(false);
const blockGuidanceBusy = ref(false);
const blockGuidanceDirty = ref(false);
const referencesPanel = ref<InstanceType<typeof DocumentReferences> | null>(null);
const referencesBusy = ref(false);
const referencesDirty = ref(false);
function openPanel(name: RightPanel) {
  if (rightPanel.value === "references" && name !== "references") {
    if (referencesBusy.value) return;
    if (referencesDirty.value) { referencesPanel.value?.requestClose(); return; }
  }
  if (rightPanel.value === "assignments" && name !== "assignments") {
    if (blockGuidanceDirty.value) { assignmentsPanel.value?.showGuidance(); blockGuidancePanel.value?.requestClose(); return; }
    if (assignmentsPanel.value?.isDirty) { assignmentsPanel.value.requestClose(); return; }
  }
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
const assignmentsOpen = panelFlag("assignments");
const referencesOpen = panelFlag("references");
const referenceCount = computed(() => document.value?.formatTemplate?.references?.length || 0);

function acceptReferences(state: DocumentReferencesState) {
  if (!document.value) return;
  document.value.revision = state.revision;
  document.value.formatTemplate.references = state.items.map(item => item.kind === "document"
    ? { id: item.id, kind: item.kind, documentId: item.documentId, sourceRevision: item.sourceRevision, note: item.note }
    : { id: item.id, kind: item.kind, name: item.name, content: item.content, note: item.note });
  referencesDirty.value = false;
  aiUndo.value = null;
  clearAiDraft();
}

function acceptAssignments(state: DocumentAssignmentsState) {
  assignmentState.value = state;
}
function locateAssignment(objectId: string) {
  editor.value?.querySelector<HTMLElement>(`[data-object-id="${CSS.escape(objectId)}"]`)?.scrollIntoView({ behavior: "smooth", block: "center" });
}
function closeAssignments() {
  if (blockGuidanceDirty.value) { assignmentsPanel.value?.showGuidance(); blockGuidancePanel.value?.requestClose(); return; }
  rightPanel.value = null;
}
async function acceptBlockGuidance(result: DocumentContentResult) {
  if (!document.value) return;
  if (result.formatTemplate) document.value.formatTemplate = result.formatTemplate;
  document.value.revision = result.revision;
  aiUndo.value = null;
  blockGuidanceDirty.value = false;
  clearAiDraft();
  await assignmentsPanel.value?.reload();
  try { assignmentState.value = await getDocumentAssignments(document.value.id); }
  catch (error) { noteAction(messageOf(error)); }
}
const assignOpen = panelFlag("assign");
const rejectedOpen = panelFlag("rejected");
const skillOpen = ref(false);
const skillBusy = ref(false);
const refreshingEditor = ref(false);
/** 终验 F-M1：编辑区正在重绘或载入时的说明（“正在载入 张工 的更新…”等），显示在提示区。 */
const refreshReason = ref("");
// 终验 F-L1：打开文档后约 0.25 秒在确认其他标签与本机草稿，期间正文不可编辑——显示载入状态，不再无声丢键
/** 设计师建议 6：载入锁定期间已记下的字数（隐形输入框 + 缓冲的按键），在载入提示里告诉用户 */
const loadHeld = ref(0);
/** 设计师 S4：载入期间记下的字（上屏后的，不含拼音），提示里直接给出 */
const loadHeldText = ref("");
const refreshNotice = computed(() => refreshReason.value || (refreshingEditor.value ? "正在刷新编辑区…" : "")
  || (checkingDrafts.value && phase.value === "ready" ? (loadHeld.value ? `正在检查本机草稿…已记下“${loadHeldText.value.length > 20 ? `…${loadHeldText.value.slice(-20)}` : loadHeldText.value}”（${loadHeld.value} 个字），载入后写在你点的位置。` : "正在检查本机草稿，马上就能编辑…") : ""));
/** 最近一次在编辑区输入的时间；锁定期间敲下的字先存起来，重绘完成后在原光标处写回。 */
let lastInputAt = 0;
let typedDuringLock = "";
// 测试工程师 F-M3：重绘后光标放不回去（失焦）时，之后敲到页面上的字先记下并提示，不无声丢掉
let caretLostAt = 0;
let typedAfterCaretLost = "";
let imeAfterCaretLost = false;
let caretLostTimer: number | undefined;
// 测试工程师 F-L1：打开文档检查本机草稿的约 0.25s 内，点在正文上的位置和敲下的字先记下，解锁后在该位置写回
let loadPoint: { node: Node; offset: number } | null = null;
let typedDuringLoad = "";
let imeDuringLoad = false;
/** F-L1 缺口：载入锁定期间点了正文，焦点先落到一个隐形输入框，直接上屏/组字/粘贴的字都先收在这里，解锁后写回点击处。 */
let loadSink: HTMLTextAreaElement | null = null;
let loadSinkComposing = false;
/**
 * F-M4（列表项等没能冻结的冲突）：冲突返回后正文重绘，光标所在的那一项换成了对方版本、光标离开正文。
 * 这时焦点落到隐形输入框，接着打的字（含直接上屏/组字）并入这条冲突“我的版本”的快照，并给出常驻提示（处理该冲突或点“知道了”才消失）。
 */
let unfrozenConflict: { conflictId: string; itemIndex: number; mine: string; at: number; item?: ConflictItem } | null = null;
/** 设计师 S1：不能冻结的那一项还没在编辑区换成对方版本时提示写“正在换成…”，重绘后看到对方版本再改成“已换成” */
let pendingSwap: { conflictId: string; item: ConflictItem; text: string } | null = null;
let caretSink: HTMLTextAreaElement | null = null;
let caretSinkTarget: { conflictId: string; itemIndex: number; mine: string; synced?: boolean } | null = null;
let caretSinkComposing = false;
let caretSinkIme = false;
let caretSinkTimer = 0;
let caretSinkSeq = 0;
const sinkNote = ref<{ conflictId: string; text: string } | null>(null);
/** 审查员 M2：隐形框里还没存进冲突副本的续写（先进本机草稿，再发；失败退避重试、联网重发、离开时守住） */
const sinkUnsaved = ref<{ conflictId: string; itemIndex: number; markdown: string } | null>(null);
/** 必改 B：冲突已在别处处理、还没接回正文的续写（写进草稿 conflictsEdits 保底，算未保存） */
const leftovers = ref<Array<{ conflictId: string; itemIndex: number; markdown: string }>>([]);
/** 隐形框换到另一条冲突时，上一条还没存上的续写（同样记草稿、继续重试） */
const sinkBacklog = ref<Array<{ conflictId: string; itemIndex: number; markdown: string }>>([]);
let caretSinkText = "";
let caretSinkPrefix = "";
let caretSinkRetry = 0;
let caretSinkRetryTimer = 0;
// 测试工程师 F-H4：最近一次保存进入冲突的时间（恢复草稿据此判断“冲突未处理”）
let lastConflictAt = 0;
let lockHadFocus = false;
let lockCaret: { index: number; offset: number }[] = [];
let lockBlocks: string[] = [];
let imeDuringLock = false;
function typingRecently(): boolean {
  if (Date.now() - lastInputAt < 2000) return true;
  const root = editor.value, selection = window.getSelection();
  return Boolean(root && window.document.activeElement === root && selection && !selection.isCollapsed && root.contains(selection.anchorNode));
}
const undoingAi = ref(false);
const aiUndo = ref<{ id: string; content: string; revision: number } | null>(null);
const localDraft = ref<DraftV2 | null>(null);
const localDrafts = ref<DraftV2[]>([]);
const draftBusy = ref(false);
/** 最后一审建议 3：恢复的草稿还没存上服务器（保存在重试）。存上前草稿不删、恢复条保持“恢复中…”、编辑区保持锁定。 */
const restorePending = ref<{ draft: DraftV2; record: DocumentRecord } | null>(null);
const exporting = ref(false);
const exportMenu = ref(false);
const exportError = ref("");
const actionNote = ref("");
/** 审查员第 2 条：“他人更新”类短提示（已显示/已合并）与常驻提示条互斥，提示条优先。T-M2：按优先级，“他人更新”类不覆盖光标/失焦类。 */
let actionNoteKind: NoteKind = "";
// T-S2：“他人更新”类短提示用浮层显示，不推挤编辑区
const actionNoteOthers = ref(false);
let actionNoteTimer: number | undefined;
// 保存状态（02 §3.2）：胶囊由这些标志按优先级得出。
const readonly = ref(false);
const readonlyReason = ref("");
/** A05：只读成员被调整为可编辑时的提示（不自动切换，16 §2.10）。 */
const permissionUpgrade = ref("");
// gone：项目不存在或已无权访问（被移出）；fromPoll：轮询发现而不是点保存时遇到；unsaved=false：没有未保存的内容（20 L-2，21 S-4）
/** A07（17 §4.5）：分工锁定。locked：本人不能修改的对象 → 编写人（文字）；baseline：最近一次服务器对象，用来给整篇保存配 blockIds。 */
const assignInfo = ref<{ revision: number; locked: Map<string, string>; restricted: boolean; order: string[]; headings: string[];
  editable: Set<string>; projectOwner: string; mode: string | null } | null>(null);
/** 29 M-2：只读块气泡（块下方）与暂存（本机，按文档）。 */
const lockedBubble = ref<{ text: string; top: number; left: number; id: string } | null>(null);
let lockedHintTimer: number | undefined;
/** 28 N-5：暂存列表（只追加）；lockedStash 是全部内容，气泡只显示最后一条（这次输入）。 */
const stashEntries = ref<StashEntry[]>([]);
const lockedStash = computed(() => stashAll(stashEntries.value));
const stashLatest = computed(() => stashEntries.value[stashEntries.value.length - 1]?.text ?? "");
const stashBarText = computed(() => stashEntries.value.length > 1 ? `暂存里有 ${stashEntries.value.length} 段不归你负责的内容（${stashText(stashLatest.value).replace(/。$/, "")}），复制或放置时一起处理。` : stashText(lockedStash.value));
/** 29 M-3：保存被拒（SECTION_NOT_ASSIGNED）。 */
const rejected = ref<{ id: string | null; violations: Violation[]; total: number; content: string } | null>(null);
const savingMine = ref(false);
/** 29 M-6：分工变化提示。 */
const assignNotice = ref<{ tone: "info" | "warning"; text: string; locate?: boolean; saveMine?: boolean } | null>(null);
const mineIntroOff = ref(false);
const outlineFilter = ref<"all" | "mine" | null>(null);
const noAssignNotice = ref("");
let noAssignReadonly = false;
// 复验 N-1：暂存按“用户 + 文档”存，只保留本次连续输入（来源块 + 时间），换块或停顿 20 秒后重新开始
const stashKey = () => `docwb.a07.stash.${account()}.${document.value?.id ?? ""}`;
function persistStash() {
  try {
    if (stashEntries.value.length) localStorage.setItem(stashKey(), serializeStash(stashEntries.value));
    else localStorage.removeItem(stashKey());
  } catch { /* 存不了就只在本页 */ }
}
function addStash(text: string, node: Element | null, fresh = false) {
  if (!text) return;
  // 只追加，永远不替换已有的暂存
  stashEntries.value = addStashEntry(stashEntries.value, text, node?.getAttribute("data-object-id") || "", Date.now(), fresh);
  persistStash();
}
function readStash() {
  try { stashEntries.value = parseStash(localStorage.getItem(stashKey())); } catch { stashEntries.value = []; }
}
let baselineObjects: BaselineObject[] = [];
let baseSnapshot: { revision: number; objects: BaselineObject[] } | null = null;
let ownershipSeq = 0;
const assignMode = computed(() => document.value?.permissions?.assignmentMode ?? null);
// 管理员 canAssign=true，所以“编写人员且不能分配”就是受限的编写人员（与服务端 restricted 一致）
const assignRestricted = computed(() => (assignInfo.value?.mode ?? assignMode.value) === "enforced" && document.value?.permissions?.projectRole === "editor" && !document.value?.permissions?.canAssign);
const canAssignDoc = computed(() => Boolean(document.value?.permissions?.canAssign));
const mineStarts = computed(() => assignInfo.value ? mineRegions(assignInfo.value.order, assignInfo.value.editable) : []);
/** 复验建议：提示条、目录、分工变化提示统一按章节计数（没有章节时按段）。 */
function countText(ids: string[], headings: string[]) {
  const set = new Set(headings);
  const chapters = ids.filter(id => set.has(id)).length;
  return chapters ? `${chapters} 章` : `${ids.length} 段`;
}
const mineCountText = computed(() => assignInfo.value ? countText([...assignInfo.value.editable].filter(id => assignInfo.value!.order.includes(id)), assignInfo.value.headings) : "");
const mineIntro = computed(() => assignRestricted.value && !mineIntroOff.value && Boolean(assignInfo.value?.editable.size) && !noAssignReadonly
  ? `你负责本文档的 ${mineCountText.value}，其他部分只读。` : "");
let ownershipPending: Promise<void> | null = null;
function refreshOwnership(record = document.value): Promise<void> {
  const task = loadOwnership(record);
  ownershipPending = task;
  void task.finally(() => { if (ownershipPending === task) ownershipPending = null; });
  return task;
}
/** 保存前等正在读取的归属（刚保存过、对象 ID 变了），块标识按最新对象配。 */
async function blockIdsReady(content: string): Promise<Array<string | null> | undefined> {
  if (ownershipPending) await ownershipPending;
  return blockIdsFor(content);
}
async function loadOwnership(record = document.value) {
  if (!record) return;
  const seq = ++ownershipSeq;
  try {
    const [data, view] = await Promise.all([getAllObjects(record.id), getAssignments(record.id).catch(() => null)]);
    if (document.value !== record || seq !== ownershipSeq) return;
    baselineObjects = data.objectModel ? data.objects.map(item => ({ id: item.id, kind: item.kind, markdown: item.markdown })) : [];
    // 27 复验 M-4：编辑区渲染所依据的那一版对象（ID 与编辑区 data-object-id 一致），“只保存我负责的部分”以它为底
    if (data.objectModel && data.documentRevision === record.revision) baseSnapshot = { revision: record.revision, objects: [...baselineObjects] };
    const locked = new Map<string, string>();
    const editableIds = new Set<string>();
    for (const item of data.objects) {
      if (item.editable === false) locked.set(item.id, ownerNames(item.owners));
      else editableIds.add(item.id);
    }
    const prev = assignInfo.value;
    if (!prev) { try { readStash(); mineIntroOff.value = localStorage.getItem(`${stashKey()}.intro`) === "1"; } catch { /* 无本机存储 */ } }
    assignInfo.value = { revision: data.assignmentsRevision ?? record.permissions?.assignmentsRevision ?? 0, locked, restricted: false,
      order: data.objects.map(item => item.id), headings: data.objects.filter(item => item.kind === "heading").map(item => item.id),
      editable: editableIds, projectOwner: view?.projectOwners?.[0]?.name ?? prev?.projectOwner ?? "", mode: view?.assignmentMode ?? prev?.mode ?? assignMode.value };
    assignInfo.value.restricted = assignRestricted.value;
    if (record.permissions && view) record.permissions.assignmentMode = view.assignmentMode;
    applyLocks();
    noteOwnershipChange(prev, assignInfo.value);
    if (!prev && assignInfo.value.restricted && editableIds.size && !route.hash) void nextTick(() => locateMine(!localDraft.value));
    // 28 N-4'：每次取到分工都拆（含改派后重取）；草稿列表还没读进来时由 findLocalDrafts 读完后再拆
    if (assignInfo.value.restricted) settleDraftOwnership();
  } catch { /* 读不到归属时不锁，保存仍由服务器把关 */ }
}
/** 整篇保存附带的块标识：对象模型可用时总是带（编写人员在 enforced 文档上缺了会被 422）。 */
function blockIdsFor(content: string): Array<string | null> | undefined {
  if (!objectModel.value && !baselineObjects.length) return undefined;
  return assignBlockIds(content, baselineObjects);
}
function lockText(id: string) {
  return lockedText(assignInfo.value?.locked.get(id) || "", assignInfo.value?.projectOwner || "");
}
/** 29 M-6：没有分给我的部分时整篇只读；分工变化时提示得到/失去的部分。 */
function noteOwnershipChange(prev: typeof assignInfo.value, next: NonNullable<typeof assignInfo.value>) {
  const owner = next.projectOwner;
  const noneMine = next.restricted && next.order.length > 0 && next.editable.size === 0;
  if (noneMine && !readonly.value) { noAssignReadonly = true; readonly.value = true; readonlyReason.value = noAssignmentText(owner); noAssignNotice.value = readonlyReason.value; }
  else if (!noneMine && noAssignReadonly) { noAssignReadonly = false; readonly.value = false; readonlyReason.value = ""; noAssignNotice.value = ""; }
  if (!prev || prev.revision === next.revision) return;
  const who = owner ? `项目负责人 ${owner}` : "项目负责人";
  // 29 复验 3：有未保存修改的判断包括保存中、保存失败/被拒还没处理的（多处改动、一处被改派时也要给［只保存我负责的部分］）
  const unsaved = isDirty.value || saving.value || Boolean(blocked.value) || Boolean(rejected.value) || failures.value > 0 || Boolean(pendingKey);
  if (prev.mode !== next.mode) {
    if (next.mode === "enforced" && next.restricted) assignNotice.value = { tone: "info", text: `${who} 开启了按分工编辑，你只能修改分配给你的部分（${countText(next.order.filter(id => next.editable.has(id)), next.headings)}）。`, locate: next.editable.size > 0 };
    else if (next.mode === "open") assignNotice.value = { tone: "info", text: `${who} 改为开放编辑，你现在可以修改全部内容。` };
    if (next.mode === "enforced" && next.restricted && unsaved) assignNotice.value = { ...assignNotice.value!, tone: "warning", text: `${assignNotice.value!.text}你还有未保存的修改，不属于你的部分可以放入暂存。`, saveMine: true };
    return;
  }
  if (!next.restricted) return;
  const gainedIds = next.order.filter(id => next.editable.has(id) && prev.locked.has(id));
  const lostIds = next.order.filter(id => next.locked.has(id) && prev.editable.has(id));
  const gained = gainedIds.length ? countText(gainedIds, next.headings) : "";
  const lost = lostIds.length ? countText(lostIds, next.headings) : "";
  if (lost && unsaved) assignNotice.value = { tone: "warning", text: `分工已调整：有 ${lost}改由他人负责，你现在只能查看。你还有未保存的修改，不属于你的部分可以放入暂存。`, saveMine: true };
  else if (lost) assignNotice.value = { tone: "info", text: `分工已调整：有 ${lost}改由他人负责，你现在只能查看。` };
  else if (gained) assignNotice.value = { tone: "info", text: `分工已调整：新分配给你 ${gained}，现在可以修改。`, locate: true };
}
function nodeOf(id: string | null | undefined): HTMLElement | null {
  return id ? editor.value?.querySelector<HTMLElement>(`[data-object-id="${CSS.escape(id)}"]`) ?? null : null;
}
/** 定位到我负责的第一处（focus=false：只滚动，用于打开时）。 */
function locateMine(focus = true) {
  const node = nodeOf(mineStarts.value[0]);
  if (!node) return;
  node.scrollIntoView({ block: "center" });
  if (!focus) return;
  editor.value?.focus({ preventScroll: true });
  const range = window.document.createRange();
  range.selectNodeContents(node); range.collapse(true);
  const selection = window.getSelection(); selection?.removeAllRanges(); selection?.addRange(range);
}
/** 复验 N-4：本机草稿里不归我改的部分不能“恢复并保存”（必然被拒）：这些放进暂存，草稿只留我负责部分的修改；全部不归我时删掉草稿。 */
function settleDraftOwnership() {
  const record = document.value, info = assignInfo.value;
  if (!record || !info || !baselineObjects.length) return;
  const drafts = localDrafts.value.filter(draft => draft.wholeDocument);
  for (const draft of drafts) {
    if (!draft.markdown) continue;
    const kept = keepMineOnly(draft.markdown, baselineObjects, info.editable);
    if (!kept.stashed.length) continue;
    addStash(kept.stashed.join("\n\n"), null, true);
    const server = baselineObjects.map(item => item.markdown).join("\n\n") + "\n";
    if (kept.markdown === server) {
      removeDraft(window.localStorage, account(), record.id, draft.tabId, { savedAt: draft.savedAt });
      localDrafts.value = localDrafts.value.filter(item => item !== draft);
    } else {
      // 29 N-4：按当前分工拆开后写回本机（只留我负责部分的修改），下次打开不再出现必然被拒的“恢复并保存”
      draft.markdown = kept.markdown;
      writeDraft(window.localStorage, account(), { ...draft, markdown: kept.markdown });
    }
    noteAction("本机草稿里有不归你负责的修改，已放入暂存，可以复制。", 10000);
  }
  localDraft.value = localDrafts.value[0] ?? null;
}
/** 选区覆盖的对象 ID（选中文字的 AI 预检用）。 */
function selectionObjectIds(): string[] {
  const root = editor.value;
  if (!root || !selectedRange) return [];
  return [...root.querySelectorAll<HTMLElement>("[data-object-id]")].filter(node => selectedRange!.intersectsNode(node)).map(node => node.getAttribute("data-object-id") || "").filter(Boolean);
}
function dismissMineIntro() { mineIntroOff.value = true; try { localStorage.setItem(`${stashKey()}.intro`, "1"); } catch { /* 无本机存储 */ } }
function copyStash() {
  const text = lockedStash.value;
  void navigator.clipboard?.writeText(text).then(() => { noteAction("已复制暂存的内容。", 4000); if (lockedStash.value === text) clearStash(); }, () => noteAction("复制失败，请手动选择复制。", 6000));
}
/** 复验 N-1：把暂存的字放到只读块之后我负责的下一处（后面没有就前面最近一处）：该处最后一个正文块的末尾；只有标题时新起一段。不写进标题。 */
function placeStash() {
  const root = editor.value;
  const text = lockedStash.value;
  if (!root || !text || readonly.value) return;
  const from = nodeOf(lockedBubble.value?.id || stashEntries.value[stashEntries.value.length - 1]?.source);
  const all = [...root.querySelectorAll<HTMLElement>("[data-object-id]")].filter(node => !node.parentElement?.closest("[data-object-id]"));
  const mine = all.filter(node => node.hasAttribute("data-mine"));
  const start = (from ? mine.find(node => from.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING)
    ?? mine.filter(node => from.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_PRECEDING).pop() : undefined) ?? mine[0];
  if (!start) { noteAction("你还没有可以修改的部分，请先复制暂存的内容。", 6000); return; }
  // 连续的一处：从 start 所在位置向两边扩展
  let lo = all.indexOf(start), hi = lo;
  while (lo > 0 && all[lo - 1].hasAttribute("data-mine")) lo -= 1;
  while (hi < all.length - 1 && all[hi + 1].hasAttribute("data-mine")) hi += 1;
  const region = all.slice(lo, hi + 1);
  const isHeading = (node: Element) => /^H[1-6]$/.test(node.tagName);
  const body = region.filter(node => !isHeading(node)).pop();
  const anchor = body ?? region[region.length - 1];
  let target: Element = anchor;
  if (anchor.tagName === "UL" || anchor.tagName === "OL") target = anchor.lastElementChild ?? anchor;
  root.focus({ preventScroll: true });
  const range = window.document.createRange();
  range.selectNodeContents(target); range.collapse(false);
  const selection = window.getSelection(); selection?.removeAllRanges(); selection?.addRange(range);
  if (!body) {
    // 只有标题：在标题后新起一段正文
    if (!window.document.execCommand("insertParagraph")) return;
    if (/^H[1-6]$/.test((window.getSelection()?.anchorNode instanceof Element ? window.getSelection()!.anchorNode as Element : window.getSelection()?.anchorNode?.parentElement)?.closest("h1,h2,h3,h4,h5,h6")?.tagName || "")) window.document.execCommand("formatBlock", false, "p");
  }
  if (!window.document.execCommand("insertText", false, text)) return;
  anchor.scrollIntoView({ block: "center" });
  stashEntries.value = []; persistStash(); lockedBubble.value = null;
}
function clearStash() { stashEntries.value = []; persistStash(); }
/** 把不能修改的顶层块设为只读（列表按条目）。只动本函数加的标记，不碰服务端的 data-word-protected。 */
function applyLocks() {
  const root = editor.value;
  if (!root) return;
  const info = assignInfo.value;
  const locked = info?.locked ?? new Map<string, string>();
  const mine = info?.restricted ? info.editable : new Set<string>();
  for (const node of root.querySelectorAll<HTMLElement>("[data-object-id]")) {
    const id = node.getAttribute("data-object-id") || "";
    if (locked.has(id)) {
      node.removeAttribute("data-mine");
      node.setAttribute("data-assignee-label", locked.get(id) || "未分配");
      if (node.hasAttribute("data-a07-lock")) continue;
      node.setAttribute("data-a07-lock", "");
      node.setAttribute("data-editable", "false");
      node.setAttribute("data-assignee", locked.get(id) || "");
      node.setAttribute("contenteditable", "false");
      node.setAttribute("title", lockText(id));
      continue;
    }
    if (node.hasAttribute("data-a07-lock")) {
      node.removeAttribute("data-a07-lock"); node.removeAttribute("data-editable"); node.removeAttribute("data-assignee"); node.removeAttribute("data-assignee-label"); node.removeAttribute("title");
      if (!node.hasAttribute("data-word-protected")) node.removeAttribute("contenteditable");
    }
    if (mine.has(id)) node.setAttribute("data-mine", ""); else node.removeAttribute("data-mine");
  }
}
function showLockedHint(node: Element) {
  const rect = node.getBoundingClientRect();
  const id = node.getAttribute("data-object-id") || "";
  lockedBubble.value = { text: lockText(id), top: Math.min(rect.bottom + 6, window.innerHeight - 120), left: Math.max(8, Math.min(rect.left, window.innerWidth - 380)), id };
  window.clearTimeout(lockedHintTimer);
  lockedHintTimer = window.setTimeout(() => { lockedBubble.value = null; }, lockedStash.value ? 12000 : 5000);
}
/** 复验 N-2：滚动时跟着块移动（点击引起的滚动不能把刚弹出的提示关掉）；块离开视口才收起。 */
function hideLockedBubble() {
  const bubble = lockedBubble.value;
  if (!bubble) return;
  const node = nodeOf(bubble.id);
  const rect = node?.getBoundingClientRect();
  if (!rect || rect.bottom < 0 || rect.top > window.innerHeight) { lockedBubble.value = null; return; }
  lockedBubble.value = { ...bubble, top: Math.min(rect.bottom + 6, window.innerHeight - 120), left: Math.max(8, Math.min(rect.left, window.innerWidth - 380)) };
}
/** 29 M-2：只读块里按键（含删除、回车）被拦下并提示；可见字符记进暂存。窗口级捕获：光标在只读块里时焦点可能不在编辑区。 */
/** 最近点中的只读块：浏览器不一定把光标放进 contenteditable=false 的块，按键时以它为准，直到点到别处。 */
let lockedFocus: Element | null = null;
function guardLockedKey(event: KeyboardEvent) {
  if (event.isComposing || event.ctrlKey || event.metaKey || event.altKey || !assignInfo.value?.locked.size) return;
  const root = editor.value;
  const anchor = window.getSelection()?.anchorNode;
  const focused = lockedFocus && root?.contains(lockedFocus) ? lockedFocus : null;
  if (!root || (!focused && (!anchor || !root.contains(anchor)))) return;
  const printable = event.key.length === 1;
  // 方向键等移动光标：不再以点中的只读块为准
  if (!printable && !["Enter", "Backspace", "Delete", "Tab"].includes(event.key)) { if (!["Shift", "Control", "Alt", "Meta"].includes(event.key)) lockedFocus = null; return; }
  const node = focused ?? assignLockedNode(event.key === "Backspace" ? "deleteContentBackward" : event.key === "Delete" ? "deleteContentForward" : "insertText");
  if (!node) return;
  event.preventDefault();
  event.stopPropagation();
  if (printable) addStash(event.key, node);
  showLockedHint(node);
}
function guardLockedComposition() {
  if (!assignInfo.value?.locked.size) return;
  const anchor = window.getSelection()?.anchorNode;
  if (!anchor || !editor.value?.contains(anchor)) return;
  const node = assignLockedNode("insertText");
  if (node) showLockedHint(node);
}
function lockedMouseDown(event: MouseEvent) {
  const node = (event.target as Element | null)?.closest?.("[data-a07-lock]");
  lockedFocus = node && editor.value?.contains(node) ? node : null;
  if (lockedFocus) showLockedHint(lockedFocus);
}
/** 选区碰到只读块（或光标在只读块边上做删除）时不让改，并提示归属。服务器仍会校验。 */
function assignLockedNode(inputType: string): Element | null {
  const root = editor.value;
  const selection = window.getSelection();
  if (!root || !selection?.rangeCount || !assignInfo.value?.locked.size) return null;
  const range = selection.getRangeAt(0);
  for (const node of root.querySelectorAll("[data-a07-lock]")) if (range.intersectsNode(node) && !range.collapsed) return node;
  const start = range.startContainer instanceof Element ? range.startContainer : range.startContainer.parentElement;
  const inside = start?.closest("[data-a07-lock]");
  if (inside && root.contains(inside)) return inside;
  if (range.collapsed && /^delete/.test(inputType)) {
    let top: Element | null = start;
    while (top && top.parentElement && top.parentElement !== root && !top.hasAttribute("data-object-id")) top = top.parentElement;
    if (!top || !root.contains(top)) return null;
    const probe = window.document.createRange();
    probe.selectNodeContents(top);
    const backward = /Backward/.test(inputType);
    if (backward) probe.setEnd(range.startContainer, range.startOffset); else probe.setStart(range.startContainer, range.startOffset);
    if (probe.toString() === "") {
      const neighbour = backward ? top.previousElementSibling : top.nextElementSibling;
      if (neighbour?.hasAttribute("data-a07-lock")) return neighbour;
    }
  }
  return null;
}
function guardLocked(event: InputEvent) {
  const node = assignLockedNode(event.inputType || "");
  if (node) {
    event.preventDefault();
    if (event.data && /^insert(Text|FromComposition|ReplacementText)$/.test(event.inputType)) addStash(event.data, node);
    showLockedHint(node);
  }
}
const blocked = ref<{ message: string; location?: LocationInfo; content: string; code: string; blockText?: string; deletedText?: string; gone?: boolean; fromPoll?: boolean; unsaved?: boolean } | null>(null);
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
watch(leaveDialog, (value) => { if (value) exportMenu.value = false; });
let leaveBypass = false;
let retryTimer: number | undefined;
let savingTimer: number | undefined;
let clockTimer: number | undefined;
/**
 * 上一次提交（同一内容的重试复用同一个键）。增量审查建议：响应丢失/超时后又打了字，先按原内容、原基准、原键重放一次，
 * 确认上一次是否已提交（已提交则服务器按键返回原结果，基准随之前移），再提交新内容，不会和自己冲突。
 */
let pendingKey: { content: string; key: string; base: number; baseMarkdown?: string; dsend: string; gen: number; editor?: EditorAtConflict } | null = null;
// S5：编辑区正文所基于的修订号。合并保存后编辑区若有更新的输入，编辑区仍基于旧修订，下次保存以它为基准做三方合并。
let mergeBase: number | null = null;
/**
 * 终验 F-H2：合并保存返回时用户还在打字、放弃了重绘，编辑区显示的仍是旧正文（缺对方的修改）。
 * dom 是那一刻编辑区的文字，value 是对应的“服务器新正文 + 本地未保存修改”。之后每次输入把编辑区相对 dom 的改动
 * 平移到 value 上（rebaseBlocks），正文始终以服务器新版本为基准，不再按旧基准提交、也不会与自己冲突。编辑区重绘后清空。
 */
let domPair: { dom: string; value: string } | null = null;
/**
 * 增量审查 M1：平移不了（rebaseBlocks 返回 null）时不再退回旧修订作基准——那样自己已保存、又接着打字的段落会被当成
 * 对方的修改而与自己冲突。改为“显式基准”：domBase 是与最近一次成功保存对应的编辑区文字；退回时正文换成编辑区文字，
 * 以 explicitBase（= 那时的 domBase）为基准、以服务器最新修订为 expectedRevision 提交（baseMarkdown），
 * 服务器据此三方合并：只有对方真改过、自己又改了的段落才冲突，冲突里的“对方”是别人。
 */
let domBase = "";
let explicitBase: string | null = null;
// 每次退回显式基准 +1；在途的保存返回时据此知道编辑区已换回编辑区文字
let fallbackGen = 0;
/**
 * 冲突后重交可合并部分时，最初那次提交的正文（content，与编辑区正文同一空间）、对应的编辑区文字（dom）和退回代数（gen）。
 * 重交本身再冲突时沿用最初这一份（测试工程师 F-H3：否则拿“可合并部分”当基线平移，会把对方刚写进的段落当成我删掉的）。
 */
interface EditorAtConflict { content: string; dom: string; gen: number }
/**
 * 方案 C（14-最终界面验收 §三）：冲突段在编辑区里“冻结”。冻结区 = 冲突段在编辑区里对应的连续顶层块，带 data-frozen="冲突:条目"。
 * 冲突返回时不重绘这一段；转正文（markdownContent）时冻结区一律换成 theirs（服务器上这一段的版本），所以在冻结区里打字不改正文、
 * 不会被自动保存写进正文；冻结区里的文字（mine）在自动保存节拍上追加为冲突副本快照（POST …/conflicts/{cid}/mine）。
 */
interface Frozen {
  key: string; conflictId: string; itemIndex: number; name: string; objectIds: string[]; deleted: boolean;
  /** 服务器正文里这一段的版本（提交时代替冻结区）；对方删段时为空 */
  theirs: string;
  /** 冲突时最初的“我的版本”（不变） */
  original: string;
  /** 编辑区冻结区当前的文字 */
  mine: string;
  /** 服务器上最新一份快照的内容与时间 */
  saved: string; savedAt: string | null; savedTab: string | null; snapshotId: string | null;
  /** 审查员 M1：以服务器上较新的一份快照为准（另一个标签页写的）；冻结区文字仍是 from 时卡片显示 markdown */
  adopted?: { from: string; markdown: string } | null;
  failing: boolean;
  /** 测试工程师 N3：副本已到上限，冻结区的新内容存不进服务器（不再重试；处理冲突时随请求带上，不再要求先保存） */
  limited?: boolean;
  /** 冻结区第一块在编辑区顶层块里的位置（冻结区被整段删掉时据此放回占位） */
  index: number;
  /** 已处理，等下一次重绘时撤掉 */
  resolved: boolean;
}
const frozen = ref<Record<string, Frozen>>({});
const frozenOpen = computed(() => Object.values(frozen.value).filter(item => !item.resolved));
const frozenUnsaved = computed(() => frozenOpen.value.filter(item => item.mine !== item.saved).length);
/** 还要存成快照的（副本已满的不算：存不进去，处理时随请求带上） */
const frozenPending = computed(() => frozenOpen.value.filter(item => item.mine !== item.saved && !item.limited).length);
/** 测试工程师 N3：编辑区里没有这一段（重新打开 / 恢复草稿后）、副本又已满存不进去的“我的版本”：卡片显示，处理时随请求带上；写进本机草稿 */
const limitedMine = ref<Record<string, { conflictId: string; itemIndex: number; markdown: string }>>({});
let snapshotTimer: number | undefined;
const snapshotKeys = new Map<string, { markdown: string; key: string; seq: number }>();
// 审查点 2：快照序号（本标签单调递增）
let snapshotSeq = 0;
let snapshotFlight: Promise<void> | undefined;
// 恢复本机草稿时编辑区显示的不是提交的内容，冲突不能冻结编辑区（按原流程重交可合并部分）
let draftRestoring = false;
// T-M1：本次会话恢复草稿时产生的冲突
const restoreConflictIds = ref<string[]>([]);
const conflictSummary = ref<ConflictSummary | null>(null);
// 界面验收 M2/M3：冲突入口按 mineOpen / 成员冲突 / 已处理记录显示；打开时停在对应页签。
const conflictTab = ref<"mine" | "members">("mine");
const conflictEntry = computed(() => conflictToggle(conflictSummary.value));
const memberConflicts = computed(() => conflictSummary.value?.allOpen != null ? Math.max(0, conflictSummary.value.allOpen - conflictSummary.value.mineOpen) : 0);
// 界面验收 M5：本人待处理冲突在正文和目录上的标记
const openConflictItems = ref<ConflictItem[]>([]);
const conflictMarks = ref<Array<{ key: string; top: number; label: string; item?: ConflictItem; approx?: boolean; gap?: boolean; tip?: string; frozen?: boolean }>>([]);
const conflictHeadings = computed(() => new Set(openConflictItems.value.map(item => item.location?.headingPath?.at(-1)).filter(Boolean) as string[]));
/** T-M1：草稿是否改到了这处冲突的段落（草稿里既没有原文也没有对方的版本，说明这一段被草稿改过）。 */
function draftTouches(draft: DraftV2, item: ConflictItem): boolean {
  const text = draft.markdown ?? "";
  const base = (item.base?.markdown ?? "").trim(), theirs = (item.theirs?.markdown ?? "").trim();
  if (!base && !theirs) return false;
  return !(base && text.includes(base)) && !(theirs && text.includes(theirs));
}
// T-M1 / T-S1：恢复被拒的原因——常驻在恢复条里，冲突处理完自动解除
const restoreRefusal = computed<{ text: string; conflictIds: string[] } | null>(() => {
  const draft = localDraft.value;
  if (!draft) return null;
  const open = openConflictItems.value.filter(item => (item.status ?? "open") === "open");
  const fromRestore = [...new Set(open.filter(item => restoreConflictIds.value.includes(item.conflictId)).map(item => item.conflictId))];
  if (fromRestore.length) return { text: `刚恢复的那份草稿有 ${fromRestore.length} 处冲突还没处理。处理完再恢复下一份，以免覆盖。`, conflictIds: fromRestore };
  const touched = [...new Set(open.filter(item => draftTouches(draft, item)).map(item => item.conflictId))];
  if (touched.length) return { text: touched.length > 1 ? `这份草稿改到的段落还有 ${touched.length} 处未处理的冲突，请先处理这些冲突再恢复。` : "这份草稿改到的段落还有未处理的冲突，请先处理这处冲突再恢复。", conflictIds: touched };
  return null;
});
function showRefusedConflicts() {
  const id = restoreRefusal.value?.conflictIds[0];
  const item = openConflictItems.value.find(entry => entry.conflictId === id);
  if (item) openConflictCard(`${item.conflictId}:${item.itemIndex}`);
  else openConflicts("mine");
}
// 审查 S2：自动保存遇到冲突时的常驻提示（面板不自动打开）
const autoConflict = ref<{ count: number; name: string; frozen?: boolean; whole?: string } | null>(null);
// 界面验收 M6 / 审查 S3：他人更新
const othersUpdated = ref<{ names: string[]; objects: number; here?: boolean; self?: boolean } | null>(null);
const updatedHeadings = ref<Set<string>>(new Set());
let updateTimer: number | undefined;
let checkingUpdates = false;
// 审查 S14：对象模型不可用（往返校验失败的旧文档）时按整篇提交。
// 重塑轮：/objects 的 objectModel 对真实内容总是 true（拆块是全覆盖切分），真正会退化的是预览块与对象对不上
// （嵌套列表、列表项带续行段落、链接引用定义、只有注释的块），这时冲突与更新只能按整段/整篇对照，同样显示“整篇保存”。
const objectModel = ref<boolean | null>(null);
const previewModel = ref<boolean | null>(null);
const wholeSave = computed(() => objectModel.value === false || previewModel.value === false);
// 底部状态栏：最近一次修改（取版本记录第一条）
const lastEditor = ref<{ name: string; at: string } | null>(null);
let lastEditorTimer: number | undefined;
// 目录：搜索与“添加标题”浮层
const outlineSearch = ref("");
const outlineSearchOpen = ref(false);
const addHeadingOpen = ref(false);
// 格式工具栏：段落样式下拉，按光标所在块显示；下拉抢走焦点后用最近一次的光标位置执行
const paraStyle = ref("p");
let lastCaret: Range | null = null;
// 冲突标记里“大致位置”的冲突（卡片位置后加“（大致）”）
const approxConflicts = ref<string[]>([]);
const draftConfirmDiscard = ref(false);
const draftDiffOpen = ref(false);
const conflictPanel = ref<InstanceType<typeof DocumentConflictPanel> | null>(null);
const mineConflicts = computed(() => conflictCount(conflictSummary.value));
// 多标签提示（决策4：提示，不阻止；02 §3.3）：只覆盖同一浏览器。
const multiTab = ref(false);
let presence: BroadcastChannel | null = null;
const lastPresenceTabs = new Set<string>();
// 复审 M1：仍在线（发过 presence、还没说 bye）的其他标签；它们的草稿不显示为恢复条。
const liveTabs = new Set<string>();
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
const guidanceGroups = ref<WritingGuidanceGroup[]>([]);
const {
  search: guidanceSearch, available: guidanceAvailable, matches: guidanceMatches,
  group: guidanceGroup, selectedKey: guidanceGroupKey, item: guidanceItem,
  itemIndex: guidanceItemIndex, keyOf: guidanceKeyOf, moveItem: moveGuidanceItem,
  revealItem: revealGuidanceItem, reset: resetGuidanceBrowser,
} = useGuidanceBrowser(guidanceGroups);
watch([aiScope, selectedHeading], resetGuidanceBrowser);
const guidanceNotices = ref<string[]>([]);
const guidancePhase = ref<"idle" | "loading" | "ready" | "error">("idle");
const guidanceError = ref("");
const guidanceSaving = ref(false);
const guidanceEdits = ref<Record<string, { text: string | null; base: string; heading: string }>>({});
const guidanceConflicts = ref<Record<string, string>>({});
const guidanceChanged = computed(() => Object.keys(guidanceEdits.value).length > 0);
let guidanceController: AbortController | undefined;
const aiDraft = ref("");
const aiEdits = ref<DocumentEdit[]>([]);
// Spec 30 §4: in-place block suggestions with per-paragraph accept/reject.
const aiSuggestions = shallowRef<Suggestion[]>([]);
const aiSuggestionCursor = ref(0);
const aiSuggestionDropped = ref(0);
const aiSuggestionAccepted = ref(0);
const aiSuggestionTick = ref(0);
const aiPendingSuggestions = computed(() => { void aiSuggestionTick.value; return pendingSuggestions(aiSuggestions.value); });
const aiComplete = ref(false);
const aiAllowedModes = ref<("append" | "replace")[]>(["append", "replace"]);
let draftInstruction = "";
let pendingMemory: WritingMemory | undefined;
const writingMemory = ref("");
const memoryOffset = ref(0);
const memoryHasMore = ref(false);
const memoryLoading = ref(false);
const memoryError = ref("");
const memoryCount = ref(0);
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
const historyRestoreBlocked = computed(() => readonly.value ? "你没有修改正文的权限，不能恢复版本。" : assignRestricted.value ? "按分工编辑的文档只有项目负责人能恢复版本。" : isDirty.value || saving.value ? "当前还有修改未保存，保存后才能恢复版本。" : localDraft.value ? "请先处理本机草稿（恢复、下载或舍弃），再恢复版本。" : "");
// 方案 C：冻结区里还没存成冲突副本的续写也算 1 处未保存修改
const pendingCount = computed(() => (isDirty.value ? Math.max(1, changedBlockCount(savedContent.value, markdownContent.value)) : 0) + frozenUnsaved.value + (sinkUnsaved.value ? 1 : 0) + sinkBacklog.value.length + leftovers.value.length);
const hasUnsaved = computed(() => isDirty.value || frozenUnsaved.value > 0 || Boolean(sinkUnsaved.value) || sinkBacklog.value.length > 0 || leftovers.value.length > 0 || Object.keys(limitedMine.value).length > 0);
const offline = computed(() => !online.value || (retrying.value && isOffline(failures.value, failingSince, now.value, online.value)));
// 终验 F-M1：“撤销这一段”要等编辑区重绘完成，胶囊才显示“刚刚保存”
const archivedNotice = computed(() => readonly.value && document.value?.permissions?.readonlyReason === "archived" ? readonlyText("archived", document.value.permissions.projectRole) : "");
const capsule = computed(() => capsuleState({ readonly: readonly.value, blocked: Boolean(blocked.value) || storageFailed.value, offline: (offline.value || (!online.value && frozenUnsaved.value > 0)) && hasUnsaved.value,
  retrying: retrying.value && isDirty.value, saving: showSaving.value || revertingBlock.value, dirty: hasUnsaved.value }));
const capsuleText = computed(() => rejected.value && blocked.value?.code === "SECTION_NOT_ASSIGNED" ? "未保存" : capsuleLabel(capsule.value, { savedAt: savedAt.value, now: now.value, pending: pendingCount.value }));
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
const outlineInfo = computed(() => outlineEntries(markdownContent.value));
/** 29 M-1：目录按“我的部分”筛选（受限编写人员默认）。目录第 i 个标题对应对象里第 i 个标题。 */
const outlineMode = computed(() => assignRestricted.value ? outlineFilter.value ?? "mine" : "all");
const outlineMineFlags = computed(() => outline.value.map((_item, index) => {
  const id = assignInfo.value?.headings[index];
  return !assignRestricted.value || (id ? assignInfo.value!.editable.has(id) : false);
}));
const outlineMineCount = computed(() => outlineMineFlags.value.filter(Boolean).length);
const outlineShown = computed(() => {
  const query = outlineSearch.value.trim();
  return outline.value.map((item, index) => ({ item, index, info: outlineInfo.value[index], mine: outlineMineFlags.value[index] }))
    .filter(entry => (!query || entry.item.text.includes(query)) && (outlineMode.value === "all" || entry.mine));
});
const pendingSections = computed(() => outlineInfo.value.filter(item => item.empty).length);
const totalChars = computed(() => countChars(markdownContent.value));
const sectionChars = computed(() => selectedHeading.value === null ? null : outlineInfo.value[selectedHeading.value]?.chars ?? null);
const currentSection = computed(() => selectedHeading.value === null ? "" : outline.value[selectedHeading.value]?.text || "");
// 重塑：右侧面板（原稿/版式预览时只保留版本记录）
const panelShown = computed(() => historyOpen.value || (mode.value === "edit" && (aiOpen.value || conflictOpen.value || assignmentsOpen.value || referencesOpen.value || assignOpen.value || rejectedOpen.value)));
const aiTargetTitle = computed(() => aiScope.value === "document" ? document.value?.name || "" : aiScope.value === "selection" ? "选中内容" : outline.value[selectedHeading.value ?? -1]?.text || "");
const aiTargetHint = computed(() => {
  if (aiScope.value === "document") return "按章节修改现有内容，确认后应用";
  if (aiScope.value === "selection") return `已选 ${countChars(selectedText.value)} 字，生成后替换选中内容`;
  const info = outlineInfo.value[selectedHeading.value ?? -1];
  return info?.empty ? "当前为空章节，将按模板要求生成正文" : `本节约 ${info?.chars ?? 0} 字，生成后可替换或追加`;
});
const skillLabel = computed(() => {
  const value = document.value?.formatTemplate?.writingSkill as { mode?: string; name?: string } | undefined;
  return value?.mode === "selected" ? value.name || "已指定" : value?.mode === "none" ? "不使用编写规范" : "按文档类型自动选择";
});
// 终验 S-4：展开后焦点进搜索框；Esc 收起、清空并把焦点还给搜索按钮
const outlineSearchInput = ref<HTMLInputElement | null>(null);
const outlineSearchButton = ref<HTMLButtonElement | null>(null);
function toggleOutlineSearch() {
  outlineSearchOpen.value = !outlineSearchOpen.value;
  if (!outlineSearchOpen.value) outlineSearch.value = "";
  else void nextTick(() => outlineSearchInput.value?.focus());
}
function closeOutlineSearch() {
  outlineSearchOpen.value = false;
  outlineSearch.value = "";
  void nextTick(() => outlineSearchButton.value?.focus());
}
// 终验 S-5：下载菜单——打开时焦点到第一项；Esc / 点外部 / 弹出对话框时关闭；上下方向键切换
const exportMenuEl = ref<HTMLElement | null>(null);
const exportMoreButton = ref<HTMLButtonElement | null>(null);
function menuItems(): HTMLButtonElement[] {
  return [...(exportMenuEl.value?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]') || [])];
}
function closeExportMenu(returnFocus = false) {
  exportMenu.value = false;
  if (returnFocus) void nextTick(() => exportMoreButton.value?.focus());
}
function onExportMenuKeydown(event: KeyboardEvent) {
  if (!exportMenu.value) return;
  if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closeExportMenu(true); return; }
  if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
  event.preventDefault();
  const items = menuItems();
  if (!items.length) return;
  const index = items.indexOf(window.document.activeElement as HTMLButtonElement);
  const next = event.key === "ArrowDown" ? (index + 1) % items.length : (index - 1 + items.length) % items.length;
  items[index < 0 ? 0 : next].focus();
}
function onExportMenuOutside(event: PointerEvent) {
  const target = event.target as Node | null;
  if (!exportMenu.value || !target) return;
  if (exportMenuEl.value?.contains(target) || exportMoreButton.value?.contains(target)) return;
  closeExportMenu();
}
watch(exportMenu, (open) => {
  if (open) void nextTick(() => menuItems()[0]?.focus());
  window.document.removeEventListener("pointerdown", onExportMenuOutside, true);
  if (open) window.document.addEventListener("pointerdown", onExportMenuOutside, true);
});
// 编辑稿跟随原稿既有的 PDF 显示比例，CSS 的 pt 则按 96 px/in 换算。
const wordDisplayZoom = 1.5 / (96 / 72);
const desk = ref<HTMLElement | null>(null);
const paperZoom = ref(wordDisplayZoom);
let deskObserver: ResizeObserver | undefined;
function fitPaper() {
  const host = desk.value;
  if (!host || !host.clientWidth) return;
  // 纸张自然宽度：Word 页面宽 + 左右页边距（pt），没有页面信息时用 --paper-w（794px）
  const pt = (value?: string) => value ? parseFloat(value) * 96 / 72 : 0;
  const style = paperStyle.value;
  const natural = style.width ? pt(style.width) + pt(style.paddingLeft) + pt(style.paddingRight) : 794;
  const room = host.clientWidth - 32;
  const zoom = natural > 0 && room < natural * wordDisplayZoom ? Math.max(0.6, Math.floor((room / natural) * 100) / 100) : wordDisplayZoom;
  if (zoom !== paperZoom.value) { paperZoom.value = zoom; scheduleMarks(); void nextTick(layoutWordTabs); }
}
function layoutWordTabs() {
  if (!editor.value || mode.value !== "edit") return;
  const zoom = paperZoom.value;
  for (const paragraph of editor.value.querySelectorAll<HTMLElement>("[data-word-tab-stop]")) {
    const step = parseFloat(paragraph.dataset.wordTabStop || "") * 96 / 72;
    if (!(step > 0)) continue;
    const origin = paragraph.getBoundingClientRect().left - parseFloat(getComputedStyle(paragraph).marginLeft) * zoom;
    for (const tab of paragraph.querySelectorAll<HTMLElement>("[data-word-tab]")) {
      if (tab.textContent !== "\t") { tab.style.width = "auto"; continue; }
      tab.style.width = "0px";
      const offset = (tab.getBoundingClientRect().left - origin) / zoom;
      tab.style.width = `${(Math.floor((offset + 0.05) / step) + 1) * step - offset}px`;
    }
  }
}
watch(desk, (host) => {
  deskObserver?.disconnect();
  if (host && typeof ResizeObserver !== "undefined") { deskObserver = new ResizeObserver(() => fitPaper()); deskObserver.observe(host); }
});
watch(paperStyle, () => void nextTick(fitPaper), { deep: true });
// 终验 S-3：显示模板组名（标准号，如“GJB 438C-2021”）；取不到或与标题相同就不显示
const templateTag = computed(() => {
  const record = document.value;
  if (record?.sourceType !== "template") return "";
  const tag = (record.templateName || "").trim();
  return tag && tag !== record.name.trim() ? tag : "";
});
// A06（16 §3.4）：模板版本。legacySnapshot 只显示“版本化之前”，不显示第 N 版与“模板已有新版本”；不提供升级。
const templateVersion = computed(() => {
  const info = document.value?.sourceType === "template" ? document.value.template : null;
  if (!info) return null;
  if (info.legacySnapshot) return { legacy: true, label: "", newer: "" };
  if (!info.seq) return null;
  const published = info.publishedAt ? info.publishedAt.slice(0, 10) : "";
  return { legacy: false, label: `第 ${info.seq} 版${published ? `（${published}）` : ""}`,
    newer: info.latestSeq && info.latestSeq > info.seq ? `模板已有第 ${info.latestSeq} 版，本文档仍按第 ${info.seq} 版的结构和指导编写。` : "" };
});
const canGenerate = computed(() => aiScope.value === "document" ? Boolean(markdownContent.value.trim()) : aiScope.value === "selection" ? Boolean(selectedText.value) : selectedHeading.value !== null);
const aiProgressLabel = computed(() => aiStopped.value ? "已停止" : aiBusy.value ? aiProgress.value.at(-1)?.message || "正在连接编写服务" : aiComplete.value ? "草稿已完成" : aiError.value ? "编写失败" : "生成未完成");
const canApplyDraft = computed(() => !readonly.value && aiComplete.value && !aiBusy.value && !aiInserting.value && !isDirty.value && editorVersion.value === draftEditorVersion.value && aiRevision.value === document.value?.revision && Boolean(aiDraft.value.trim()) && (aiScope.value !== "document" || aiEdits.value.length > 0));
// 8.2 第 7 条：aiLocked 仍是“编辑区锁”（版本记录打开、AI 预览、本机草稿未处理、重绘中都不能编辑正文），只锁编辑区与格式工具栏。
// 终验回归 H1：打开文档后、查完本机草稿之前编辑区不可输入（否则新输入会覆盖本标签的旧草稿）
const checkingDrafts = ref(false);
const aiLocked = computed(() => referencesOpen.value || referencesBusy.value || assignmentsOpen.value || assignmentsBusy.value || blockGuidanceBusy.value || guidanceSaving.value || checkingDrafts.value || skillOpen.value || skillBusy.value || historyOpen.value || historyBusy.value || aiBusy.value || aiInserting.value || refreshingEditor.value || undoingAi.value || diagramBusy.value || Boolean(aiPreviewHost.value) || Boolean(localDraft.value) || Boolean(restorePending.value));
// 重塑（S11/S13/N9）：顶栏、目录、视图切换、面板切换只在真正有操作进行时锁定；草稿恢复条和合并后重绘不再让整页变灰。
const opBusy = computed(() => referencesBusy.value || assignmentsBusy.value || blockGuidanceBusy.value || guidanceSaving.value || skillBusy.value || historyBusy.value || aiBusy.value || aiInserting.value || undoingAi.value || diagramBusy.value || Boolean(aiPreviewHost.value) || addingHeading.value);
const canUndoAi = computed(() => Boolean(aiUndo.value) && aiUndo.value?.id === document.value?.id && aiUndo.value?.revision === document.value?.revision && !isDirty.value && !aiLocked.value && !saving.value && !addingHeading.value);
const canStartAi = computed(() => !referencesOpen.value && !referencesBusy.value && !referencesDirty.value && !readonly.value && !guidanceSaving.value && !skillOpen.value && !skillBusy.value && !historyOpen.value && !historyBusy.value && !aiBusy.value && !aiInserting.value && !saving.value && !refreshingEditor.value && !undoingAi.value && !addingHeading.value && !diagramBusy.value && !localDraft.value);
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
  previewModel.value = null;
  lastEditor.value = null;
  approxConflicts.value = [];
  outlineSearch.value = "";
  outlineSearchOpen.value = false;
  addHeadingOpen.value = false;
  draftConfirmDiscard.value = false;
  draftDiffOpen.value = false;
  mergeBase = null;
  domPair = null;
  explicitBase = null;
  domBase = "";
  frozen.value = {};
  snapshotKeys.clear();
  window.clearTimeout(snapshotTimer);
  restoreConflictIds.value = [];
  skillOpen.value = false;
  aiUndo.value = null;
  memoryOffset.value = 0;
  memoryHasMore.value = false;
  localDraft.value = null;
  localDrafts.value = [];
  checkingDrafts.value = true;
  resetSaveState();
  stopOriginal();
  mode.value = "edit";
  selectedHeading.value = null;
  aiOpen.value = false;
  selectedText.value = "";
  selectedRange = null;
  pendingMemory = undefined;
  writingMemory.value = "";
  aiScope.value = "section";
  aiInstruction.value = "";
  guidanceController?.abort();
  guidanceGroups.value = [];
  resetGuidanceBrowser();
  guidanceEdits.value = {};
  guidanceConflicts.value = {};
  guidancePhase.value = "idle";
  guidanceError.value = "";
  clearAiDraft();
  phase.value = "loading";
  errorText.value = "";
  orphanDrafts.value = []; orphanStash.value = "";
  loadErrorStatus.value = 0;
  try {
    draftAccount = readSession()?.staffId || draftAccount;
    await browserTabId();
    browserTab = browserTabIdentity().current;
    void getCapabilities().then(value => { capabilities.value = value; }).catch(() => { capabilities.value = null; });
    const fetched = await getDocument(id);
    const preview = await previewDocument(id);
    const assignments = await getDocumentAssignments(id).catch(() => null);
    if (id !== documentId.value || version !== loadVersion) return;
    document.value = fetched;
    assignmentState.value = assignments;
    // 界面复验 N3：document 是深层 ref，之后的 “document.value === record” 比较要用它返回的代理对象，
    // 否则加载时取到的 objectModel（整篇保存标签）、草稿落库核对等都会被当成“文档已切换”而丢掉。
    const record = document.value!;
    if (record.permissions && !record.permissions.canEdit) {
      // 01 §6.1：canEdit=false 直接只读，不必等第一次 403。
      readonly.value = true;
      // 终审：非成员 auditor 不是“项目中的某个角色”，写成以审计身份只读查看
      // A05（16 §2.10）：按 readonlyReason 枚举选文案（archived / role），不拼后端 message
      readonlyReason.value = readonlyText(record.permissions.readonlyReason, record.permissions.projectRole);
    }
    markdownContent.value = record.markdownContent;
    savedContent.value = record.markdownContent;
    // 审查 S14：对象模型不可用（拆块往返校验失败）的文档，服务器按整篇合并
    void getObjectsSince(record.id, record.revision).then(value => { if (document.value === record) objectModel.value = value.objectModel; }).catch(() => undefined);
    selectedHeading.value = [...record.markdownContent.matchAll(/^(#{1,6})\s+(.+)$/gm)].length ? 0 : null;
    phase.value = "ready";
    await nextTick();
    if (id !== documentId.value || version !== loadVersion) return;
    showEditorHtml(preview.html, preview.objectModel);
    void refreshOwnership(record);
    if (route.query.assign === "1") assignOpen.value = true;
    assignInitialFilter.value = route.query.filter === "unassigned" ? "unassigned" : typeof route.query.person === "string" ? `person:${route.query.person}` : "all";
    savedAt.value = Date.parse(record.updatedAt) || null;
    void refreshLastEditor();
    // 复审 M1：先上线、等其他标签回答，再列本机草稿，避免另一个仍打开着的标签的草稿短暂闪现为恢复条。
    openPresence(record.id);
    void refreshMemory();
    void refreshConflicts();
    await new Promise(resolve => window.setTimeout(resolve, PRESENCE_WAIT_MS));
    if (id !== documentId.value || version !== loadVersion) return;
    findLocalDrafts(record);
  } catch (error) {
    if (id !== documentId.value || version !== loadVersion) return;
    checkingDrafts.value = false;
    phase.value = "error";
    loadErrorStatus.value = error instanceof ApiError ? error.status : 0;
    errorText.value = error instanceof ApiError && error.status === 404 ? accessText(error) : messageOf(error);
    // 28 I-7：被移出项目（404）时，本机还留着这篇的草稿/暂存——给下载入口，字不困在浏览器里
    if (loadErrorStatus.value === 404) {
      try {
        orphanDrafts.value = listDrafts(window.localStorage, account(), id).filter(draft => draft.markdown);
        orphanStash.value = stashAll(parseStash(localStorage.getItem(`docwb.a07.stash.${account()}.${id}`)));
      } catch { orphanDrafts.value = []; orphanStash.value = ""; }
    }
  }
}
function resetSaveState() {
  window.clearTimeout(retryTimer);
  if (restorePending.value) { restorePending.value = null; draftBusy.value = false; }
  window.clearTimeout(savingTimer);
  readonly.value = false;
  readonlyReason.value = "";
  permissionUpgrade.value = "";
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
/** 设计师必改 3：localDrafts 是深层 ref，元素是 Proxy，不能拿原始草稿对象做 ===；按 tabId + savedAt 认同一份草稿。 */
function sameDraft(a: Pick<DraftV2, "tabId" | "savedAt">, b: Pick<DraftV2, "tabId" | "savedAt">): boolean {
  return a.tabId === b.tabId && a.savedAt === b.savedAt;
}
function account() { return draftAccount || readSession()?.staffId || "user"; }
const PRESENCE_WAIT_MS = 250;
function findLocalDrafts(record: DocumentRecord) {
  // 终验回归 H1：本页面自己写的草稿也要列出——load() 总是把编辑区换成服务器正文，
  // 同一页面内重新打开文档（如会话过期重新登录后回到文档）时，这份草稿已不在编辑区里。
  const drafts = listDrafts(window.localStorage, account(), record.id)
    .filter(draft => !(draft.wholeDocument && draft.markdown === record.markdownContent && !draft.conflictsEdits?.length))
    .filter(draft => !liveTabs.has(draft.tabId));
  for (const draft of listDrafts(window.localStorage, account(), record.id)) {
    // 与服务器正文相同的草稿没有恢复价值；按读到的版本删除（期间被别的标签改写就不删）。
    if (draft.wholeDocument && draft.markdown === record.markdownContent && !draft.conflictsEdits?.length) removeDraft(window.localStorage, account(), record.id, draft.tabId, { savedAt: draft.savedAt });
  }
  // 方案 C（14 §3.5 第 6 条）：草稿里记着冲突段的续写——冲突仍未处理就追加为快照（不重新提交正文），之后草稿不再带这部分
  void settleDraftConflicts(record, drafts.filter(draft => draft.conflictsEdits?.length));
  localDrafts.value = drafts;
  localDraft.value = drafts[0] ?? null;
  checkingDrafts.value = false;
  // 28 N-4'：分工先到、草稿后到（打开时总是这样）——草稿读完后按当前分工拆一次
  if (assignInfo.value?.restricted) settleDraftOwnership();
  void flushLoadTyping();
  void settlePendingSaves(record);
}
async function settleDraftConflicts(record: DocumentRecord, drafts: DraftV2[]) {
  for (const draft of drafts) {
    let all = true;
    const limited: DraftV2["conflictsEdits"] = [];
    for (const [n, edit] of draft.conflictsEdits.entries()) {
      try {
        await saveConflictMine(record.id, edit.conflictId, edit.itemIndex, edit.markdown, `draft-${draft.savedAt}-${n}-${draft.tabId}`.slice(0, 80), draft.tabId, draft.savedAt || undefined);
      } catch (error) {
        // 测试工程师 N3：副本已满存不进去——留在本页（卡片显示、写进本标签草稿），处理冲突时随请求带上，不随恢复 / 舍弃草稿丢掉
        if (error instanceof ApiError && error.code === "SNAPSHOT_LIMIT") {
          if (document.value === record) { limitedMine.value = { ...limitedMine.value, [`${edit.conflictId}:${edit.itemIndex}`]: { ...edit } }; limited.push(edit); }
          else all = false;
        } else if (!(error instanceof ApiError && ["CONFLICT_ALREADY_RESOLVED", "FORBIDDEN", "NOT_FOUND", "IDEMPOTENCY_KEY_REUSED"].includes(error.code))) all = false;
        // 必改 B：草稿里的冲突续写对应的冲突已处理——不静默丢弃，内容放进常驻提示
        else if (error.code === "CONFLICT_ALREADY_RESOLVED") sinkNote.value = { conflictId: "", text: `上次在冲突段里写的内容没能存进冲突副本（这条冲突已在别处处理）：“${edit.markdown.length > 120 ? `…${edit.markdown.slice(-120)}` : edit.markdown}”。请复制后粘贴到需要的位置。` };
      }
    }
    if (limited.length && document.value === record) {
      persistLocalDraft();
      noteAction("上次在冲突段里写的内容没能存进冲突副本（副本已满），已放在冲突卡的“我的版本”里；处理这条冲突时会直接用这一版。", 10000);
    }
    if (!all || document.value !== record) continue;
    // 本标签自己的草稿：副本已满的那几份仍留在里面（上面写本标签草稿时已带上）
    const rest: DraftV2 = { ...draft, conflictsEdits: draft.tabId === browserTab ? limited : [] };
    if (draft.wholeDocument && draft.markdown === record.markdownContent) {
      removeDraft(window.localStorage, account(), record.id, draft.tabId, { savedAt: draft.savedAt });
      localDrafts.value = localDrafts.value.filter(item => !sameDraft(item, draft));
      localDraft.value = localDrafts.value[0] ?? null;
      if (!limited.length) noteAction("上次关闭页面前在冲突段里写的内容已存为冲突副本（见冲突面板）。", 6000);
    } else if (writeDraft(window.localStorage, account(), rest) === "ok") {
      localDrafts.value = localDrafts.value.map(item => sameDraft(item, draft) ? rest : item);
      if (localDraft.value && sameDraft(localDraft.value, draft)) localDraft.value = rest;
    }
  }
  if (drafts.length) void refreshConflicts();
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
    localDrafts.value = localDrafts.value.filter(item => !sameDraft(item, draft));
    localDraft.value = localDrafts.value[0] ?? null;
    noteAction("上次关闭页面前的修改已保存到服务器。");
  }
  if (waiting && !retried) window.setTimeout(() => { if (document.value === record) void settlePendingSaves(record, true); }, 1500);
}
/** 没有未保存修改时，把编辑区换成服务器最新正文（严格接口 409 后的“刷新”）。返回是否已刷新。 */
async function refreshFromServer(options: AcceptOptions = {}, force = false): Promise<boolean> {
  const record = document.value;
  if (!record || isDirty.value || saving.value) return false;
  const stopReason = options.yieldToTyping ? reasonLater(options.reason || "正在载入最新内容…") : () => undefined;
  let latest: Awaited<ReturnType<typeof getDocument>>;
  try { latest = await getDocument(record.id); }
  finally { stopReason(); }
  if (document.value !== record) return false;
  // force：有冻结冲突段时保存已把修订号推到最新，但编辑区没重绘（他人修改还没显示），“立即刷新”要按服务器正文重绘
  if (latest.revision === record.revision && !force) return true;
  if (options.yieldToTyping && (isDirty.value || saving.value)) return false;
  if (latest.formatTemplate) record.formatTemplate = latest.formatTemplate;
  const accepted = await acceptContent({ markdownContent: latest.markdownContent, revision: latest.revision, status: latest.status }, options);
  if (!accepted) return false;
  mergeBase = null;
  void refreshConflicts();
  return true;
}
function isRevisionConflict(error: unknown): error is ApiError {
  return error instanceof ApiError && error.status === 409 && error.code === "REVISION_CONFLICT";
}
/** S6：AI 采纳、标题、图表插入都带 allowMerge（01 §4.3），每次操作一个新键。 */
function mergeRequest() { return { allowMerge: true, clientSaveKey: crypto.randomUUID() }; }
function noteMerged(result: { merged?: boolean; changedBy?: Array<{ id: string; name: string; objects: number }> } & WhitespaceNotes): boolean {
  if (!result.merged) return false;
  const text = changedByText(result.changedBy);
  // 终验 S-1 及终审建议：只改空白的修改没写入/被覆盖时，明确告诉用户（不静默丢弃）
  const whitespace = whitespaceNoteText(result);
  if (whitespace) {
    noteAction(`${text}${whitespace}`, 10000);
    return true;
  }
  // 审查员第 2 条：提示条显示期间不再弹“已合并…”短提示
  if (text && !othersUpdated.value) noteAction(text, 6000, "others");
  return true;
}
/** 派生写入 409 OBJECT_CONFLICT：冲突已存为副本，按主动保存处理（打开面板 + 重交可合并部分）。 */
function derivedConflict(error: unknown): boolean {
  if (!(error instanceof ApiError) || error.code !== "OBJECT_CONFLICT" || !document.value) return false;
  handleSaveError(error, markdownContent.value, "manual", editorText());
  return true;
}
function dismissedTabs(docId: string): string[] {
  try { return JSON.parse(window.sessionStorage.getItem(`doc-workbench.multitab:${docId}`) || "[]"); } catch { return []; }
}
function openPresence(docId: string) {
  closePresence();
  multiTab.value = false;
  if (typeof BroadcastChannel === "undefined") return;
  liveTabs.clear();
  const self = { tabId: browserTab, userId: account(), pageId: pageInstance };
  presence = new BroadcastChannel(`doc-workbench:${docId}`);
  presence.onmessage = (event: MessageEvent) => {
    const message = event.data as { type?: string; tabId?: string; userId?: string };
    if (message?.type === "bye" && message.userId === self.userId) liveTabs.delete(String(message.tabId));
    // 复审 M1：同一 tabId 来自另一个页面（复制标签页的声明回答晚到/丢失）——pageId 大的一方换新并迁移草稿
    const otherPage = sameTabOtherPage(message, self);
    if (otherPage && message.userId === self.userId) {
      if (!browserTabIdentity().resolveCollision(otherPage)) presence?.postMessage({ type: "presence-reply", ...self });
      return;
    }
    if (!otherTabOpen(message, self)) return;
    lastPresenceTabs.add(String(message.tabId));
    if (message.type !== "bye") liveTabs.add(String(message.tabId));
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
/** 复审 M1：tabId 被换新（晚到的撞号回答、在线消息发现撞号、往返缓存恢复后重新声明）时迁移本页草稿键并重新上线。 */
function onTabRenamed(previous: string, next: string) {
  if (browserTab !== previous) return;
  const record = document.value;
  const old = record ? readDraft(window.localStorage, account(), record.id, previous) : null;
  if (presence) {
    presence.postMessage({ type: "bye", tabId: previous, userId: account() });
    presence.close();
    presence = null;
  }
  browserTab = next;
  if (record) {
    // 先按新键写，写成功才删旧键（只删本页面写的那份）
    if (old && old.writer === pageInstance && !isDirty.value) {
      if (writeDraft(window.localStorage, account(), { ...old, tabId: next }) === "ok") removeDraft(window.localStorage, account(), record.id, previous, { writer: pageInstance, savedAt: old.savedAt });
    } else if (persistLocalDraft() && old?.writer === pageInstance) {
      removeDraft(window.localStorage, account(), record.id, previous, { writer: pageInstance, savedAt: old.savedAt });
    }
    openPresence(record.id);
  }
}
const stopTabWatch = browserTabIdentity().onChange(onTabRenamed);
function onPageShow(event: PageTransitionEvent) {
  // 复审 M1：从往返缓存恢复的页面可能与期间新开的复制标签同号，重新声明一次
  if (!event.persisted) return;
  void browserTabIdentity().claim().then(() => {
    if (!document.value) return;
    if (!presence) openPresence(document.value.id);
    else presence.postMessage({ type: "presence", tabId: browserTab, userId: account(), pageId: pageInstance });
  });
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
/** 复审：冲突在正文里的位置。exact=false 表示只按章节路径+序号推测（大致位置）。
 * 对方删段的冲突按“我的”文字找（我的版本仍在编辑区）；找不到时返回被删段原位置的前一个块（anchor），标记画在它的下边缘。 */
function findConflictBlock(item: ConflictItem): { block?: HTMLElement; exact: boolean; anchor?: HTMLElement | null } {
  const deleted = Boolean(item.theirs?.deleted);
  const exact = findBlock(null, item.objectIds, deleted ? item.mine?.markdown : item.theirs?.markdown);
  if (exact) return { block: exact, exact: true };
  if (deleted) {
    const anchor = deletedAnchor(item.location);
    return { block: anchor || editor.value?.firstElementChild as HTMLElement | undefined, exact: false, anchor };
  }
  return { block: findBlock(item.location), exact: false };
}
/** 审查员第 3 条：被删段原来位置的前一个块：章节内第 N 个同类块现在的前一个块；章节内已没有第 N 个时取章节最后一块。 */
function deletedAnchor(location: LocationInfo | null | undefined): HTMLElement | null {
  const root = editor.value;
  if (!root || !location) return null;
  const blocks = ([...root.children] as HTMLElement[]).filter(block => !block.hasAttribute("data-ai-preview"));
  const path = location.headingPath || [];
  let start = -1;
  if (path.length) {
    start = blocks.findIndex(block => isHeading(block) && (block.textContent || "").trim() === path[path.length - 1]);
    if (start < 0) return null;
  }
  let end = blocks.length;
  for (let i = start + 1; i < blocks.length; i++) if (isHeading(blocks[i])) { end = i; break; }
  const tags: Record<string, RegExp> = { paragraph: /^(P|DIV|BLOCKQUOTE)$/, list_item: /^(UL|OL)$/, table: /^TABLE$/, image: /^(P|FIGURE|IMG)$/, code: /^PRE$/ };
  let seen = 0;
  for (let i = start + 1; i < end; i++) {
    if ((tags[location.kind || ""] || /./).test(blocks[i].tagName) && ++seen === (location.ordinal || 1)) return blocks[i - 1] ?? null;
  }
  return end - 1 >= 0 && end - 1 >= start ? blocks[end - 1] : null;
}
function sectionName(item: ConflictItem) {
  return item.location?.headingPath?.at(-1) || "文档开头";
}
const isHeading = (block: HTMLElement) => /^H[1-6]$/.test(block.tagName);
function conflictName(item: ConflictItem) {
  return theirsName(item, readSession()?.userId);
}
/** 冲突块：红色竖线 + 浅红底（class 只加在编辑区 DOM 上，转 Markdown 前去掉）；标签画在纸面浮层里，不进入正文。 */
function applyConflictMarks() {
  if (caretSink) placeCaretSink(); // 必改 A：重绘后输入框跟着那一项
  const root = editor.value;
  if (pendingSwap && root && (pendingSwap.item.theirs?.deleted || caretSinkAnchorOf(root, pendingSwap.item, true))) {
    if (sinkNote.value?.conflictId === pendingSwap.conflictId && sinkNote.value.text.startsWith("这一项与")) sinkNote.value = { conflictId: pendingSwap.conflictId, text: pendingSwap.text };
    pendingSwap = null;
  }
  if (!root) { conflictMarks.value = []; return; }
  root.querySelectorAll(".blk-conflict").forEach(node => node.classList.remove("blk-conflict"));
  const marks: typeof conflictMarks.value = [];
  const approx: string[] = [];
  if (mode.value === "edit") {
    // 方案 C：冻结区（编辑区里仍是我的版本）——红框跟着冻结区，标签“未保存 · 与 张工 的修改冲突”
    for (const entry of frozenOpen.value) {
      const blocks = frozenEls(root, entry.key);
      blocks.forEach(block => block.classList.add("blk-conflict"));
      if (!blocks.length) continue;
      marks.push({ key: entry.key, top: blocks[0].offsetTop, label: entry.deleted ? `该段已被 ${entry.name} 删除 · 未保存` : entry.name === SELF_THEIRS_NAME ? "未保存 · 与你在另一处的修改冲突" : `未保存 · 与 ${entry.name} 的修改冲突`, frozen: true,
        tip: `这一段和 ${entry.name} 的修改冲突。你在这里写的内容先存为冲突副本，不会丢失，也暂不写入正文；在冲突面板处理后才写入。` });
    }
    for (const item of openConflictItems.value) {
      if (frozen.value[`${item.conflictId}:${item.itemIndex}`] && !frozen.value[`${item.conflictId}:${item.itemIndex}`].resolved) continue;
      const { block, exact, anchor } = findConflictBlock(item);
      const deleted = Boolean(item.theirs?.deleted);
      const key = `${item.conflictId}:${item.itemIndex}`;
      if (exact && block) {
        block.classList.add("blk-conflict");
        marks.push({ key, top: block.offsetTop, label: deleted ? `该段已被 ${conflictName(item)} 删除` : conflictName(item) === SELF_THEIRS_NAME ? "与你在另一处的修改冲突" : `与 ${conflictName(item)} 的修改冲突`, item });
        continue;
      }
      approx.push(key);
      if (deleted) {
        // 审查员第 3 条：对方删段——红色虚线画在原位置前一个块的下边缘，标签“该段已被 X 删除”
        const top = anchor ? anchor.offsetTop + anchor.offsetHeight : 0;
        marks.push({ key, top, label: `该段已被 ${conflictName(item)} 删除`, item, approx: true, gap: true });
        continue;
      }
      if (!block) continue;
      // 审查员第 4 条：按章节/序号推测的位置，不给块加底色，标签写“大致位置：{章节名}”
      const top = isHeading(block) ? block.offsetTop + block.offsetHeight : block.offsetTop;
      marks.push({ key, top, label: `大致位置：${sectionName(item)}`, item, approx: true });
    }
  }
  conflictMarks.value = marks;
  approxConflicts.value = approx;
}
let marksFrame = 0;
function scheduleMarks() {
  if (!openConflictItems.value.length && !frozenOpen.value.length) return;
  window.cancelAnimationFrame(marksFrame);
  marksFrame = window.requestAnimationFrame(() => applyConflictMarks());
}
/** 卡片“定位”/正文标签：滚到正文中的块并闪一下；从正文标签进入时打开面板并滚到卡片。 */
function locateConflict(item: ConflictItem) {
  const { block, exact, anchor } = findConflictBlock(item);
  const target = anchor || block;
  if (!target) { noteAction("没能在正文中找到这一段，可能已被删除或移动。"); return; }
  if (!exact) noteAction(item.theirs?.deleted ? "这一段已被删除，已跳到它原来所在的位置。" : "没能精确定位，已跳到所在章节。");
  target.scrollIntoView({ behavior: "smooth", block: "center" });
  target.classList.add("blk-flash");
  window.setTimeout(() => target.classList.remove("blk-flash"), 1600);
}
/** 主动保存遇到冲突：面板滚到卡片，焦点留在编辑区（14 §3.4）。 */
function scrollToCard(key: string) {
  let tries = 0;
  const find = () => {
    const card = window.document.querySelector<HTMLElement>(`.conflict-panel [data-conflict="${CSS.escape(key)}"]`);
    if (card) { card.scrollIntoView({ behavior: "smooth", block: "nearest" }); return; }
    if (++tries < 20) window.setTimeout(find, 150);
  };
  window.setTimeout(find, 0);
}
function openConflictCard(key: string) {
  autoConflict.value = null;
  openConflicts("mine");
  let tries = 0;
  const find = () => {
    const card = window.document.querySelector<HTMLElement>(`.conflict-panel [data-conflict="${CSS.escape(key)}"]`);
    // 界面复验 N6：卡片带 tabindex="-1"，焦点跟到卡片上
    if (card) { card.scrollIntoView({ behavior: "smooth", block: "nearest" }); card.focus({ preventScroll: true }); return; }
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
    applyPolledPermissions(record, since.permissions);
    if (since.documentRevision <= record.revision) return;
    void refreshLastEditor();
    const names = since.changedBy.map(person => person.name);
    const objects = since.changedBy.reduce((sum, person) => sum + person.objects, 0);
    const changed = since.objects.filter(item => !item.deleted);
    // 终验 F-M1：用户正在输入（2 秒内有输入，或在编辑区里选中了文字）时推迟自动刷新，按“本地有修改”显示提示条
    const quiet = !isDirty.value && !saving.value && !aiLocked.value && !addingHeading.value && !blocked.value && !readonly.value && !typingRecently() && !frozenOpen.value.length;
    // T-M2：焦点在编辑区、光标所在段（或前后段）被别人改了：不自动刷新（不挪光标、不失焦），改为提示条
    if (quiet && caretTouched(since.objects)) {
      // 必改 B：更新人就是本人（另一个标签页）时不写“其他成员”
      const me = readSession()?.userId;
      const ids = [...since.changedBy.map(person => person.id), ...since.objects.map(item => item.updatedBy?.id)].filter(Boolean);
      othersUpdated.value = { names, objects, here: true, self: Boolean(me && ids.length && ids.every(id => id === me)) };
      return;
    }
    if (quiet || (readonly.value && !isDirty.value)) {
      const scroll = editor.value?.closest(".desk");
      const top = scroll?.scrollTop || 0;
      const reason = `正在载入 ${names.length ? names.join("、") : "他人"} 的更新…`;
      if (!(await refreshFromServer({ yieldToTyping: true, reason }))) { othersUpdated.value = { names, objects }; return; }
      if (scroll) scroll.scrollTop = top;
      // 审查员第 2 条：本地干净、自动刷新时，先清掉提示条，再显示 5 秒短提示
      othersUpdated.value = null;
      markUpdated(changed);
      noteAction(names.length ? othersUpdatedText(names, objects, true) : "已显示最新内容。", 5000, "others");
    } else {
      othersUpdated.value = { names, objects };
    }
  } catch (error) {
    // A05（16 §2.10）：被移出项目后轮询直接 404 → 与保存遇到 404 相同的状态；其他失败不影响编辑，下次再查。
    if (error instanceof ApiError && error.status === 404 && document.value === record) enterGone(markdownContent.value, error.code, true);
  } finally {
    checkingUpdates = false;
  }
}
/** A05（17 B-8）：轮询带回的权限。可编辑 → 只读立即生效（有未保存内容时给下载）；只读 → 可编辑只提示，不打断阅读。 */
function applyPolledPermissions(record: NonNullable<typeof document.value>, permissions: typeof record.permissions | undefined) {
  if (!permissions) return;
  const before = record.permissions;
  record.permissions = permissions;
  // A07：分工修订号变了（被分配 / 改模式 / 负责人变动）→ 45 秒内重新锁定
  if (permissions.assignmentsRevision !== undefined && permissions.assignmentsRevision !== assignInfo.value?.revision) void refreshOwnership(record);
  if (!permissions.canEdit && !readonly.value) {
    readonly.value = true;
    retrying.value = false;
    readonlyReason.value = readonlyText(permissions.readonlyReason, permissions.projectRole, permissions.readonlyReason !== "archived");
    if (isDirty.value) blocked.value = { message: readonlyBlockedText(permissions.readonlyReason, permissions.projectRole), content: markdownContent.value, code: "FORBIDDEN" };
    return;
  }
  if (permissions.canEdit && readonly.value && before && !before.canEdit && !permissionUpgrade.value) {
    permissionUpgrade.value = `你的权限已调整为${PROJECT_ROLE_LABEL[permissions.projectRole || ""] || "可编辑成员"}，刷新后可以编辑。`;
  }
}
function reloadPage() { window.location.reload(); }
const PROJECT_ROLE_LABEL: Record<string, string> = { owner: "负责人", editor: "编写人员" };
/** 项目不存在或已无权访问（被移出项目）：进入只读，未保存的修改可以下载（16 §2.10，沿用 404 定稿文案）。 */
function enterGone(content: string, code = "NOT_FOUND", fromPoll = false) {
  readonly.value = true;
  retrying.value = false;
  readonlyReason.value = "项目不存在或你无权访问。";
  // 空闲时被移出（没有未保存内容）不提下载（20 L-2）
  const unsaved = content !== savedContent.value;
  blocked.value = { message: unsaved ? "项目不存在或你无权访问。未保存的修改可以下载。" : "项目不存在或你无权访问。", content, code, gone: true, fromPoll, unsaved };
}
/** T-M2：光标所在顶层块及前后各一块是否在服务器上被改（按对象 ID）。 */
function caretTouched(objects: Array<{ id: string; deleted?: boolean }>): boolean {
  const root = editor.value;
  if (!root || !(window.document.activeElement === root || root.contains(window.document.activeElement))) return false;
  const selection = window.getSelection();
  const node = selection?.rangeCount ? selection.getRangeAt(0).startContainer : null;
  if (!node || !root.contains(node)) return false;
  let block = (node instanceof Element ? node : node.parentElement) as HTMLElement | null;
  while (block && block.parentElement !== root) block = block.parentElement;
  if (!block) return false;
  const near = [block.previousElementSibling, block, block.nextElementSibling].filter(Boolean) as Element[];
  const changed = new Set(objects.map(item => String(item.id)));
  return near.some(element => [element, ...element.querySelectorAll("[data-object-id]")].some(item => changed.has(item.getAttribute("data-object-id") || "")));
}
/** 被他人更新、尚未看过的块：左侧蓝色竖线，目录标题右侧蓝点；点击或编辑该块后消失。 */
function markUpdated(objects: Array<{ id: string; markdown?: string }>, keep = false) {
  const root = editor.value;
  if (!root) return;
  const headings = new Set<string>(keep ? updatedHeadings.value : []);
  for (const object of objects) {
    const node = object.id ? root.querySelector<HTMLElement>(`[data-object-id="${CSS.escape(object.id)}"]`) : null;
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
  await refreshFromServer({}, true).catch(() => false);
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
      memoryCount.value = offset + memory.items.length;
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
function showEditorHtml(html: string, mapped?: boolean) {
  domPair = null;
  explicitBase = null;
  if (mapped !== undefined) previewModel.value = mapped;
  layoutNeedsRefresh = false;
  lastCaret = null;
  selectionNeedsRefresh.value = false;
  selectedText.value = "";
  selectedRange = null;
  aiScope.value = "section";
  editorVersion.value += 1;
  for (const image of document.value?.formatTemplate.images || []) {
    html = html.replaceAll(`src="${image.ref}"`, `src="data:${image.content_type};base64,${image.data}"`);
  }
  if (editor.value) {
    // 方案 C：冻结区节点不随重绘替换（光标、组字、撤销栈都在这些节点上），重绘后放回服务器上这一段的位置
    const kept = detachFrozen(editor.value);
    // 测试 L3：服务端把目录/域段落标成 contenteditable=false（data-word-protected）。
    editor.value.innerHTML = DOMPurify.sanitize(html, { ADD_ATTR: ["contenteditable"] });
    reattachFrozen(editor.value, kept);
    paperStyle.value = {};
    try {
      const page = JSON.parse(editor.value.firstElementChild?.getAttribute("data-word-page") || "{}");
      for (const name of ["width", "minHeight", "paddingTop", "paddingBottom", "paddingLeft", "paddingRight"]) {
        if (typeof page[name] === "string" && /^\d+(?:\.\d+)?pt$/.test(page[name])) paperStyle.value[name] = page[name];
      }
      if (paperStyle.value.width) paperStyle.value.boxSizing = "content-box";
    } catch { /* Local drafts without page metadata keep the default paper. */ }
    applyLocks();
    void nextTick(() => { layoutWordTabs(); applyConflictMarks(); });
  }
}
/** yieldToTyping：不锁编辑区，先在后台取版式，期间用户一输入就放弃重绘（返回 false）；
 *  stateApplied：调用方已把正文/修订号写好（保存合并路径），这里只负责重绘。 */
/** T-S2：不锁编辑区的载入说明延迟 350ms 再出现（正常速度下不闪），以浮层显示，不推动编辑区。 */
// T-S2：加载提示延迟 350ms 出现（短刷新不闪一下）；消失时立即消失
const refreshNoticeShown = ref("");
let refreshNoticeTimer: number | undefined;
watch(refreshNotice, (value) => {
  window.clearTimeout(refreshNoticeTimer);
  if (!value) { refreshNoticeShown.value = ""; return; }
  if (refreshNoticeShown.value) { refreshNoticeShown.value = value; return; }
  refreshNoticeTimer = window.setTimeout(() => { refreshNoticeShown.value = refreshNotice.value; }, 350);
});
const floatingReason = ref("");
function reasonLater(reason: string): () => void {
  const timer = window.setTimeout(() => { floatingReason.value = reason; }, 350);
  return () => { window.clearTimeout(timer); if (floatingReason.value === reason) floatingReason.value = ""; };
}
type AcceptOptions = { reason?: string; yieldToTyping?: boolean; stateApplied?: boolean; carryTyping?: boolean };
let composing = false;
function setComposing(value: boolean) { composing = value; }
function blockTexts(root: ParentNode): string[] {
  return [...root.querySelectorAll(CARET_BLOCKS)].map(block => block.textContent || "");
}
/** 编辑区里的光标位置（块序号 + 块内文字偏移），重绘前后用来恢复光标。 */
const CARET_BLOCKS = "p,li,h1,h2,h3,h4,h5,h6,td,th,pre,blockquote,:scope > div";
function captureCaret(root: HTMLElement): { index: number; offset: number }[] {
  const selection = window.getSelection();
  const range = selection?.rangeCount ? selection.getRangeAt(0) : null;
  const blocks = [...root.querySelectorAll(CARET_BLOCKS)];
  const positions: { index: number; offset: number }[] = [];
  if (!range || !root.contains(range.startContainer) || !root.contains(range.endContainer)) return positions;
  for (const [container, offset] of [[range.startContainer, range.startOffset], [range.endContainer, range.endOffset]] as const) {
    const element = container instanceof Element ? container : container.parentElement;
    const block = [...blocks].reverse().find(item => item === element || item.contains(container));
    if (!block) return [];
    const before = range.cloneRange();
    before.selectNodeContents(block);
    before.setEnd(container, offset);
    positions.push({ index: blocks.indexOf(block), offset: (before.cloneContents().textContent || "").length });
  }
  noteCaretMark(root, range);
  return positions;
}
/** 设计师复验 3 A1 / S-A3：重绘前记下光标的纸面坐标和它所在项（li / 单元格）在块里的序号。
 * 重绘后选区已指向被换掉的节点，再取只会得到空矩形，所以冲突输入框定位只用这里记下的值。 */
let caretMark: { top: number; left: number; ordinal: number | null; at: number } | null = null;
const CARET_ITEMS = "li, td, th";
function noteCaretMark(root: HTMLElement, range: Range) {
  const paper = root.parentElement;
  if (!paper) return;
  let rect = range.getBoundingClientRect();
  const element = range.startContainer instanceof Element ? range.startContainer : range.startContainer.parentElement;
  if (!rect.width && !rect.height && !rect.top && !rect.left && element) rect = element.getBoundingClientRect();
  const item = element?.closest<HTMLElement>(CARET_ITEMS) ?? null;
  let block: HTMLElement | null = item;
  while (block && block.parentElement !== root) block = block.parentElement;
  const ordinal = item && block ? [block, ...block.querySelectorAll<HTMLElement>(CARET_ITEMS)].filter(el => el.matches(CARET_ITEMS)).indexOf(item) : -1;
  const zoom = paperZoom.value;
  const base = paper.getBoundingClientRect();
  caretMark = { top: (rect.bottom - base.top) / zoom + 4, left: (rect.left - base.left) / zoom, ordinal: ordinal >= 0 ? ordinal : null, at: Date.now() };
}
function restoreCaret(root: HTMLElement, positions: { index: number; offset: number }[]): boolean {
  if (positions.length !== 2) return false;
  const restored = window.document.createRange();
  const blocks = [...root.querySelectorAll(CARET_BLOCKS)];
  for (const [boundary, position] of positions.entries()) {
    const block = blocks[position.index];
    if (!block) return false;
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
  root.focus({ preventScroll: true });
  const selection = window.getSelection();
  selection?.removeAllRanges();
  selection?.addRange(restored);
  return true;
}
/** 终验 F-M1：锁定期间（焦点原在编辑区）敲下的字先记下，不让它们无声消失。 */
function lockedTarget(event: Event): boolean {
  if (!refreshingEditor.value || !lockHadFocus) return false;
  const target = event.target as Node | null;
  const root = editor.value;
  return !(target && root && !(target === window.document.body || root.contains(target)));
}
/** 终审建议：锁定期间的输入法输入（组字）无法缓冲，至少在解锁时提示，不能静默丢掉。 */
function noteLockedComposition(event: Event) {
  if (lockedTarget(event)) imeDuringLock = true;
  if (caretLostAt && Date.now() - caretLostAt < 30000) imeAfterCaretLost = true;
  if (checkingDrafts.value && loadPoint && onBodyTarget(event)) imeDuringLoad = true;
}
function noteLoadPointer(event: PointerEvent) {
  const root = editor.value;
  if (!checkingDrafts.value || !root || !(event.target instanceof Node) || !root.contains(event.target)) return;
  // 解锁时提示条收起会让版式上移，按点击当时的文字位置（节点 + 偏移）记，不按坐标
  const range = (window.document as Document & { caretRangeFromPoint?: (x: number, y: number) => Range | null }).caretRangeFromPoint?.(event.clientX, event.clientY);
  loadPoint = range && root.contains(range.startContainer) ? { node: range.startContainer, offset: range.startOffset } : null;
}
function openLoadSink(event: MouseEvent) {
  const root = editor.value;
  if (!checkingDrafts.value || event.button !== 0 || !root || !(event.target instanceof Node) || !root.contains(event.target)) return;
  event.preventDefault(); // 焦点不落到暂不可编辑的正文上（那样直接上屏的输入会无声丢失）
  if (!loadSink) {
    const sink = window.document.createElement("textarea");
    sink.setAttribute("aria-label", "正文载入中，输入的内容先暂存，载入后写回");
    sink.setAttribute("data-testid", "load-sink");
    // 设计师 S4：框可见（与冲突续写框同一套 token），字数只算上屏后的（组字中的拼音不算）
    sink.setAttribute("placeholder", "载入中，先记在这里");
    sink.dataset.held = "0";
    Object.assign(sink.style, { position: "fixed", left: `${Math.min(event.clientX, window.innerWidth - 260)}px`, top: `${event.clientY + 12}px`, width: "240px", height: "30px", boxSizing: "border-box", padding: "4px 8px", margin: "0", resize: "none", overflow: "hidden", zIndex: "20", background: "var(--warning-50)", border: "1px solid var(--warning-600)", borderRadius: "var(--radius-control)", color: "var(--text-primary)", fontFamily: "inherit", fontSize: "var(--text-sm)", lineHeight: "var(--leading-sm)", outline: "none", boxShadow: "0 0 0 3px var(--focus-ring)" });
    const count = () => { loadHeldText.value = typedDuringLoad + sink.value; loadHeld.value = [...loadHeldText.value].length; sink.dataset.held = String(loadHeld.value); };
    sink.addEventListener("input", (event) => { if (!loadSinkComposing && !(event as InputEvent).isComposing) count(); });
    sink.addEventListener("compositionstart", () => { loadSinkComposing = true; });
    sink.addEventListener("compositionend", () => { loadSinkComposing = false; count(); if (!checkingDrafts.value) void flushLoadTyping(); });
    window.document.body.appendChild(sink);
    loadSink = sink;
  }
  loadSink.focus({ preventScroll: true });
}
function dropLoadSink() {
  loadHeld.value = 0;
  loadHeldText.value = "";
  loadSink?.remove();
  loadSink = null;
  loadSinkComposing = false;
}
function onEditorFocusIn() { caretLostAt = 0; }
/** 测试工程师 F-L1：载入锁定结束后，把锁定期间敲下的字写到当时点击的位置；写不进去就提示，不无声丢掉。 */
async function flushLoadTyping() {
  if (loadSink && loadSinkComposing) return; // 还在组字：等上屏（compositionend）后再写回
  const sinkUsed = Boolean(loadSink);
  const point = loadPoint, typed = typedDuringLoad + (loadSink?.value || ""), ime = imeDuringLoad;
  dropLoadSink();
  loadPoint = null;
  typedDuringLoad = "";
  imeDuringLoad = false;
  if (!typed && !ime) {
    // 锁定期间点过正文但没打字：解锁后把光标放回点击处
    if (sinkUsed && point) { await nextTick(); placeLoadCaret(point); }
    return;
  }
  await nextTick();
  const root = editor.value;
  let placed = false;
  if (typed && point && root && editable.value && root.contains(point.node)) {
    root.contentEditable = "true";
    root.focus({ preventScroll: true });
    const range = window.document.createRange();
    try {
      range.setStart(point.node, Math.min(point.offset, point.node.nodeType === Node.TEXT_NODE ? (point.node.textContent || "").length : point.node.childNodes.length));
      range.collapse(true);
      const selection = window.getSelection();
      selection?.removeAllRanges();
      selection?.addRange(range);
      placed = window.document.execCommand("insertText", false, typed);
    } catch { placed = false; }
  }
  if (ime) noteAction("载入期间用输入法输入的内容没能写入（编辑区当时暂不可编辑），请重新输入。", 10000, "caret");
  else if (typed && !placed) noteAction(`载入期间输入的“${typed.slice(0, 20)}${typed.length > 20 ? "…" : ""}”没能写入正文，请在需要的位置重新输入。`, 10000, "caret");
  else if (typed && placed) noteAction(`载入期间输入的 ${[...typed].length} 个字已写在你点的位置。`, 3000);
}
function placeLoadCaret(point: { node: Node; offset: number }) {
  const root = editor.value;
  if (!root || !editable.value || !root.contains(point.node)) return;
  root.contentEditable = "true";
  root.focus({ preventScroll: true });
  try {
    const range = window.document.createRange();
    range.setStart(point.node, Math.min(point.offset, point.node.nodeType === Node.TEXT_NODE ? (point.node.textContent || "").length : point.node.childNodes.length));
    range.collapse(true);
    window.getSelection()?.removeAllRanges();
    window.getSelection()?.addRange(range);
  } catch { /* 点击处已不在：不放光标 */ }
}
function onBodyTarget(event: Event): boolean {
  const target = event.target as Node | null;
  const root = editor.value;
  return Boolean(root) && (!target || target === window.document.body || root!.contains(target));
}
function bufferLockedTyping(event: KeyboardEvent) {
  if (checkingDrafts.value && loadPoint && onBodyTarget(event)) {
    // 测试工程师 F-L1：载入锁定期间点了正文又打字：先记下，解锁后写回
    if (event.isComposing || event.key === "Process") { imeDuringLoad = true; return; }
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key.length === 1) { typedDuringLoad += event.key; loadHeld.value += 1; loadHeldText.value += event.key; event.preventDefault(); }
    return;
  }
  if (!refreshingEditor.value && caretLostAt && Date.now() - caretLostAt < 30000 && event.target === window.document.body) {
    // 测试工程师 F-M3：重绘后失焦，之后敲下的字不能无声丢失：记下并提示
    if (event.ctrlKey || event.metaKey || event.altKey || !(event.key.length === 1 || event.key === "Process")) return;
    if (event.key === "Process" || event.isComposing) imeAfterCaretLost = true;
    if (event.key.length === 1) typedAfterCaretLost += event.key;
    window.clearTimeout(caretLostTimer);
    caretLostTimer = window.setTimeout(() => {
      const typed = typedAfterCaretLost;
      // T-S4：用输入法时敲下的是拼音字母，不引用原文
      const quote = typed && !looksLikePinyin(typed, imeAfterCaretLost);
      noteAction(quote ? `编辑区刚刷新，光标不在正文里，输入的“${typed.slice(0, 40)}${typed.length > 40 ? "…" : ""}”没有写入正文。请点击要写的位置后重新输入。`
        : "编辑区刚刷新，光标不在正文里，刚才的输入没有写入正文。请点击要写的位置后重新输入。", 15000, "caret");
    }, 500);
    return;
  }
  if (!lockedTarget(event)) return;
  if (event.isComposing || event.key === "Process") { imeDuringLock = true; return; }
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  if (event.key.length === 1) { typedDuringLock += event.key; event.preventDefault(); }
}
/** 重绘后按段落序列对齐恢复光标；对不上（或光标所在段被改了）就不放光标，免得接着打的字落进别的段。 */
function restoreMappedCaret(root: HTMLElement, before: string[], caret: { index: number; offset: number }[]): boolean {
  // 方案 C：光标在冻结区里（重绘没有换掉这些节点）：原样保留
  if (keepFrozenCaret(root)) return true;
  const after = blockTexts(root);
  // 对齐不确定（含超大文档）时退一步放到文字在前后两版都唯一的段（最后一审建议 2：尽量不无谓失焦）
  const mapped = caret.map(point => ({ index: caretBlockIndex(before, after, point.index), offset: point.offset }));
  if (mapped.length === 2 && mapped.every((point, n) => point.index >= 0 && after[point.index] === before[caret[n].index]) && restoreCaret(root, mapped)) return true;
  // 方案 C：撤掉 F-M3 的“光标放到对方段末”——冲突段不再被换掉（冻结），他人更新改到光标所在段时不自动刷新（T-M2）
  leaveEditorCaret(root);
  return false;
}
function leaveEditorCaret(root: HTMLElement) {
  const selection = window.getSelection();
  // 复验 3 A1：用重绘前记下的光标（重绘后选区已空）
  const mark = caretMark && Date.now() - caretMark.at < 30000 ? caretMark : null;
  caretMark = null;
  selection?.removeAllRanges();
  if (window.document.activeElement === root || root.contains(window.document.activeElement)) {
    root.blur();
    caretLostAt = Date.now();
    typedAfterCaretLost = "";
    imeAfterCaretLost = false;
    if (unfrozenConflict && Date.now() - unfrozenConflict.at < 30000) openCaretSink(root, unfrozenConflict, mark);
  }
}
/** 设计师必改 A：被换成对方版本的那一项（按对方版本的文字找 li/单元格/段落；找不到用冲突定位） */
function caretSinkAnchorOf(root: HTMLElement, item: ConflictItem | undefined, exactOnly = false, ordinal: number | null = null): HTMLElement | null {
  if (!item) return null;
  const norm = (text: string) => text.replace(/[*_`~\\]/g, "").replace(/\s+/g, " ").trim();
  const linesOf = (markdown: string | null | undefined) => (markdown || "").split("\n").map(line => norm(line.replace(/^\s*(?:[-*+]|\d+[.)])\s+/, "").replace(/^\|/, "").replace(/\|$/, ""))).filter(Boolean);
  // S-A3：对方版本里改动的那一行先匹配（嵌套列表时不先命中只是标题的父项）
  const mineLines = new Set(linesOf(item.mine?.markdown));
  const theirLines = linesOf(item.theirs?.markdown);
  const lines = [...theirLines.filter(line => !mineLines.has(line)), ...theirLines.filter(line => mineLines.has(line))];
  const selector = "li, p, td, th, h1, h2, h3, h4, h5, h6";
  const candidates = [...root.querySelectorAll(selector)] as HTMLElement[];
  const own = (el: HTMLElement) => norm([...el.childNodes].filter(node => !(node instanceof HTMLElement && /^(UL|OL)$/.test(node.tagName))).map(node => node.textContent || "").join(""));
  // 设计师复验 2 A1：同名条目时先在冲突所在的块（列表 / 表格）里找；块里多个同名项取离换版前光标最近的一项；都找不到才退到全文
  const scope = findConflictBlock(item).block;
  // 设计师复验 3 A1 / S-A3：按换版前光标所在项在块里的序号取（同一列表里同名项、嵌套子项都能对上）；文字与对方版本对不上时才退回按文字找
  if (ordinal !== null && scope && root.contains(scope)) {
    const byOrdinal = [scope, ...scope.querySelectorAll<HTMLElement>(CARET_ITEMS)].filter(el => el.matches(CARET_ITEMS))[ordinal];
    if (byOrdinal && theirLines.includes(own(byOrdinal))) return byOrdinal;
  }
  const pools: HTMLElement[][] = [];
  if (scope && root.contains(scope)) pools.push([scope, ...scope.querySelectorAll<HTMLElement>(selector)].filter(el => el.matches(selector)));
  pools.push(candidates);
  for (const pool of pools) {
    for (const line of lines) {
      let hits = pool.filter(el => own(el) === line);
      if (!hits.length && !exactOnly) hits = pool.filter(el => line.length >= 6 && own(el).startsWith(line.slice(0, 12)));
      if (hits.length) return nearestToCaret(hits);
    }
  }
  return exactOnly ? null : scope ?? null;
}
/** 多个同名候选时取离换版前光标位置（caretSinkFallback，纸面坐标）最近的一项 */
function nearestToCaret(hits: HTMLElement[]): HTMLElement {
  const paper = editor.value?.parentElement;
  if (hits.length === 1 || !caretSinkFallback || !paper) return hits[0];
  const zoom = paperZoom.value;
  const top = paper.getBoundingClientRect().top;
  const want = caretSinkFallback.top;
  return hits.reduce((best, el) => {
    const distance = (el2: HTMLElement) => Math.abs((el2.getBoundingClientRect().bottom - top) / zoom + 4 - want);
    return distance(el) < distance(best) ? el : best;
  });
}
let caretSinkAnchor: HTMLElement | null = null;
let caretSinkItem: ConflictItem | undefined;
let caretSinkFallback: { top: number; left: number } | null = null;
let caretSinkOrdinal: number | null = null;
/** 输入框放在该项正下方（左侧与文字对齐），挂在纸面里随正文滚动；下方放不下时放到上方 */
function placeCaretSink() {
  const sink = caretSink, root = editor.value;
  const paper = root?.parentElement;
  if (!sink || !root || !paper) return;
  if (!caretSinkAnchor || !caretSinkAnchor.isConnected || !root.contains(caretSinkAnchor)) caretSinkAnchor = caretSinkAnchorOf(root, caretSinkItem, false, caretSinkOrdinal);
  const zoom = paperZoom.value;
  const base = paper.getBoundingClientRect();
  let top: number, left: number;
  if (caretSinkAnchor) {
    const rect = caretSinkAnchor.getBoundingClientRect();
    const range = window.document.createRange();
    range.selectNodeContents(caretSinkAnchor);
    const textRect = range.getBoundingClientRect();
    left = ((textRect.width ? textRect.left : rect.left) - base.left) / zoom;
    const below = (rect.bottom - base.top) / zoom + 4;
    const above = (rect.top - base.top) / zoom - sink.offsetHeight - 4;
    top = below + sink.offsetHeight <= paper.offsetHeight - 4 || above < 0 ? below : above;
  } else if (caretSinkFallback) {
    ({ top, left } = caretSinkFallback);
  } else {
    top = root.offsetTop;
    left = root.offsetLeft;
  }
  sink.style.top = `${Math.max(0, top)}px`;
  sink.style.left = `${Math.max(0, Math.min(left, paper.clientWidth - sink.offsetWidth))}px`;
}
/** 按内容增高，最多 4 行，超出后滚动 */
function growCaretSink() {
  const sink = caretSink;
  if (!sink) return;
  sink.style.height = "auto";
  const line = 20, pad = 10;
  const max = line * 4 + pad;
  sink.style.height = `${Math.min(max, Math.max(line + pad, sink.scrollHeight + 2))}px`;
  sink.style.overflowY = sink.scrollHeight + 2 > max ? "auto" : "hidden";
  window.requestAnimationFrame(revealCaretSink);
}
/** 设计师复验 2 A2：输入框（含增高后）要完整落在 .desk 可视区内，超出就把 .desk 滚过去（输入法候选框锚点随之回到屏内） */
function revealCaretSink() {
  const sink = caretSink;
  const desk = sink?.closest<HTMLElement>(".desk");
  if (!sink || !desk) return;
  const box = sink.getBoundingClientRect(), view = desk.getBoundingClientRect();
  if (box.bottom > view.bottom - 8) desk.scrollTop += box.bottom - view.bottom + 8;
  else if (box.top < view.top + 8) desk.scrollTop -= view.top + 8 - box.top;
}
function openCaretSink(root: HTMLElement, target: { conflictId: string; itemIndex: number; mine: string; item?: ConflictItem }, mark: { top: number; left: number; ordinal: number | null } | null = null) {
  dropCaretSink();
  const paper = root.parentElement || root;
  const sink = window.document.createElement("textarea");
  sink.setAttribute("aria-label", "冲突后接着输入的内容，会并入冲突副本“我的版本”");
  sink.setAttribute("data-testid", "caret-sink");
  sink.className = "caret-sink";
  sink.placeholder = "接着写的内容存进“我的版本”";
  sink.rows = 1;
  // 设计师 S2：用设计 token；实线边框 --warning-600 对白底对比度 > 3:1，焦点光圈 --focus-ring
  Object.assign(sink.style, {
    position: "absolute", width: "min(420px, 70%)", opacity: "1", border: "1px solid var(--warning-600)", borderRadius: "var(--radius-control)",
    background: "var(--warning-50)", color: "var(--text-primary)", boxShadow: "0 0 0 3px var(--focus-ring)", padding: "4px 8px", margin: "0",
    fontFamily: "inherit", fontSize: "var(--text-sm)", lineHeight: "var(--leading-sm)", resize: "none", overflowY: "hidden", zIndex: "6", outline: "none", boxSizing: "border-box",
  });
  caretSinkItem = target.item;
  caretSinkAnchor = null;
  caretSinkFallback = mark ? { top: mark.top, left: mark.left } : null;
  caretSinkOrdinal = mark?.ordinal ?? null;
  const changed = () => { if (caretSink === sink) { caretSinkText = caretSinkPrefix + sink.value; markSinkPending(); } };
  sink.addEventListener("input", () => {
    growCaretSink();
    window.clearTimeout(caretSinkTimer);
    if (caretSinkComposing) return; // 组字中不记、不发（拼音不进草稿也不进快照）
    changed();
    caretSinkTimer = window.setTimeout(() => void flushCaretSink(), 800);
  });
  sink.addEventListener("compositionstart", () => { caretSinkComposing = true; caretSinkIme = true; window.clearTimeout(caretSinkTimer); });
  sink.addEventListener("compositionend", () => { caretSinkComposing = false; caretSinkIme = false; changed(); window.clearTimeout(caretSinkTimer); caretSinkTimer = window.setTimeout(() => void flushCaretSink(), 300); });
  // 失焦不丢：文字已在 caretSinkText 与本机草稿里，框可以撤，发送失败会继续重试
  sink.addEventListener("blur", () => { caretSinkComposing = false; changed(); void flushCaretSink().finally(() => { if (caretSink === sink) dropCaretSink(); }); });
  paper.appendChild(sink);
  const same = caretSinkTarget && caretSinkTarget.conflictId === target.conflictId && caretSinkTarget.itemIndex === target.itemIndex;
  if (same) {
    caretSinkPrefix = caretSinkText; // 同一条冲突再次打开：接着记，不重发已存上的部分
  } else {
    if (sinkUnsaved.value) sinkBacklog.value = [...sinkBacklog.value.filter(edit => !(edit.conflictId === sinkUnsaved.value!.conflictId && edit.itemIndex === sinkUnsaved.value!.itemIndex)), sinkUnsaved.value];
    sinkUnsaved.value = null;
    caretSinkSent = "";
    caretSinkText = "";
    caretSinkPrefix = "";
    caretSinkRetry = 0;
    caretSinkTarget = { conflictId: target.conflictId, itemIndex: target.itemIndex, mine: target.mine };
  }
  caretSink = sink;
  growCaretSink();
  placeCaretSink();
  // 编辑区重排（重绘、他人更新、图片载入）后跟着那一项重新定位
  caretSinkObserver?.disconnect();
  let queued = 0;
  caretSinkObserver = new MutationObserver(() => { if (!queued) queued = window.requestAnimationFrame(() => { queued = 0; placeCaretSink(); }); });
  caretSinkObserver.observe(root, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ["class", "style"] });
  window.setTimeout(placeCaretSink, 300);
  sink.focus({ preventScroll: true });
}
function unsentSinkText(): string {
  return caretSinkText.startsWith(caretSinkSent) ? caretSinkText.slice(caretSinkSent.length) : caretSinkText;
}
/** 先记本机草稿（conflictsEdits），再发（审查员 M2） */
function markSinkPending() {
  const target = caretSinkTarget;
  const typed = unsentSinkText();
  sinkUnsaved.value = target && typed ? { conflictId: target.conflictId, itemIndex: target.itemIndex, markdown: target.mine + typed } : null;
  persistLocalDraft();
}
let caretSinkObserver: MutationObserver | null = null;
function dropCaretSink() {
  caretSinkObserver?.disconnect();
  caretSinkObserver = null;
  window.clearTimeout(caretSinkTimer);
  caretSink?.remove();
  caretSink = null;
}
function onlineAgain() {
  if (sinkUnsaved.value || sinkBacklog.value.length) void flushCaretSink();
}
async function flushSinkBacklog() {
  const record = document.value;
  if (!record || !sinkBacklog.value.length) return;
  for (const edit of [...sinkBacklog.value]) {
    try {
      caretSinkSeq = nextSeq(caretSinkSeq);
      await saveConflictMine(record.id, edit.conflictId, edit.itemIndex, edit.markdown, `sinkb-${caretSinkSeq}`.slice(0, 80), browserTab || undefined, caretSinkSeq);
    } catch (error) {
      if (!(error instanceof ApiError && ["CONFLICT_ALREADY_RESOLVED", "FORBIDDEN", "NOT_FOUND", "SNAPSHOT_LIMIT"].includes(error.code))) continue;
      // 必改 B：存不进去了，不能静默丢——放进常驻提示
      sinkNote.value = { conflictId: "", text: `之前在另一项冲突里接着写的“${edit.markdown.slice(-60)}”没能存进冲突副本（${error.code === "CONFLICT_ALREADY_RESOLVED" ? "这条冲突已处理" : "副本存不进去"}），请复制后粘贴到需要的位置。` };
    }
    sinkBacklog.value = sinkBacklog.value.filter(item => item !== edit);
  }
  persistLocalDraft();
  if (sinkBacklog.value.length && !caretSinkRetryTimer) caretSinkRetryTimer = window.setTimeout(() => { caretSinkRetryTimer = 0; void flushCaretSink(); }, 5000);
}
let caretSinkFlight: Promise<void> | null = null;
let caretSinkSent = "";
async function flushCaretSink(): Promise<void> {
  window.clearTimeout(caretSinkTimer);
  if (caretSinkFlight) { await caretSinkFlight.catch(() => undefined); }
  if (caretSinkComposing) return; // 设计师建议 3：组字中不提交拼音，上屏后再提交
  window.clearTimeout(caretSinkRetryTimer);
  caretSinkRetryTimer = 0;
  await flushSinkBacklog();
  const record = document.value, target = caretSinkTarget;
  const all = caretSinkText;
  const typed = unsentSinkText();
  if (!record || !target || !typed) return;
  caretSinkFlight = (async () => {
    // 设计师必改 1：在服务器最新的“我的版本”（mineLatest，含编辑区自动保存进去的续写）后面追加，每次提交后以返回的最新快照为准
    if (!target.synced) {
      try {
        const latest = (await listConflicts(record.id, "open", "me")).find(item => item.conflictId === target.conflictId && item.itemIndex === target.itemIndex);
        if (latest) target.mine = latest.mineLatest?.markdown ?? latest.mine?.markdown ?? target.mine;
      } catch { /* 取不到就用 409 时的版本 */ }
      target.synced = true;
    }
    const markdown = target.mine + typed;
    caretSinkSeq = nextSeq(caretSinkSeq);
    const quoted = all.length > 30 ? `${all.slice(0, 30)}…` : all;
    const swapped = caretSinkItem?.theirs?.deleted ? "这一项在正文中已被删除" : !caretSinkItem || isWholeConflict(caretSinkItem) ? "正文保持当前版本" : `这一项已换成 ${conflictName(caretSinkItem)} 的版本`;
    const text = looksLikePinyin(all, caretSinkIme)
      ? `冲突后接着输入的内容没有写进正文（${swapped}），已并入冲突副本“我的版本”，可在冲突面板里处理。要继续写正文，请点击要写的位置。`
      : `冲突后接着输入的“${quoted}”没有写进正文（${swapped}），已并入冲突副本“我的版本”，可在冲突面板里处理。要继续写正文，请点击要写的位置。`;
    sinkNote.value = { conflictId: target.conflictId, text };
    try {
      const result = await saveConflictMine(record.id, target.conflictId, target.itemIndex, markdown, `sink-${caretSinkSeq}`.slice(0, 80), browserTab || undefined, caretSinkSeq);
      target.mine = result.mineLatest?.markdown ?? markdown;
      caretSinkSent = all;
      caretSinkRetry = 0;
      markSinkPending();
      void conflictPanel.value?.load();
    } catch (error) {
      if (error instanceof ApiError && error.code === "CONFLICT_ALREADY_RESOLVED") {
        // 设计师必改 B（隐形框路径）：另一个标签页已处理——没存上的字接回这一项末尾（普通修改），接回后才清掉隐形框的待存
        dropCaretSink();
        caretSinkTarget = null;
        const fallback = { ...(caretSinkItem || {}), theirs: { markdown: target.mine } } as ConflictItem;
        const item = caretSinkItem;
        await reattachLeftover(typed, target.conflictId, root => caretSinkAnchorOf(root, item) || caretSinkAnchorOf(root, fallback));
        sinkUnsaved.value = null;
        caretSinkText = caretSinkSent = caretSinkPrefix = "";
        persistLocalDraft();
        void refreshConflicts().then(() => conflictPanel.value?.load());
        return;
      }
      if (error instanceof ApiError && ["FORBIDDEN", "NOT_FOUND", "SNAPSHOT_LIMIT"].includes(error.code)) {
        // 无权/副本已满：存不进去了，文字留在提示里让用户复制
        sinkUnsaved.value = null;
        persistLocalDraft();
        sinkNote.value = { conflictId: target.conflictId, text: error.code === "SNAPSHOT_LIMIT"
          ? `冲突副本已超过总大小上限，接着输入的“${all}”没能存进去。请先处理这条冲突，再把这些内容粘贴到正文。`
          : `冲突后接着输入的“${all}”没能写进正文，也没能存进冲突副本（这条冲突已处理），请复制后粘贴到需要的位置。` };
        return;
      }
      // 审查员 M2：存不上时文字已在本机草稿里；退避重试，联网时立即重发
      markSinkPending();
      const delay = Math.min(30000, 2000 * 2 ** caretSinkRetry);
      caretSinkRetry += 1;
      caretSinkRetryTimer = window.setTimeout(() => void flushCaretSink(), delay);
      sinkNote.value = { conflictId: target.conflictId, text: `冲突后接着输入的“${quoted}”暂时没存上（已记在本机），联网后会自动存进冲突副本“我的版本”，请不要清除浏览器数据。` };
    }
  })();
  try { await caretSinkFlight; } finally { caretSinkFlight = null; }
}
function beginEditorLock(reason: string) {
  const root = editor.value;
  // 已在锁定中（撤销 → 重绘接力）时沿用最初的焦点与光标
  if (!refreshingEditor.value) {
    lockHadFocus = Boolean(root && (window.document.activeElement === root || root.contains(window.document.activeElement)));
    lockCaret = root && lockHadFocus ? captureCaret(root) : [];
    lockBlocks = root && lockHadFocus ? blockTexts(root) : [];
    imeDuringLock = false;
  }
  refreshReason.value = reason;
  refreshingEditor.value = true;
  if (root) root.contentEditable = "false";
}
/** 解锁；锁定期间敲下的字在恢复的光标处写回（走正常输入流程，会触发自动保存）。 */
function endEditorLock() {
  const root = editor.value;
  const caret = lockCaret;
  lockCaret = [];
  refreshingEditor.value = false;
  refreshReason.value = "";
  if (root) root.contentEditable = String(editable.value);
  const typed = typedDuringLock;
  typedDuringLock = "";
  const ime = imeDuringLock;
  imeDuringLock = false;
  const before = lockBlocks;
  lockBlocks = [];
  if (!root || !lockHadFocus) return;
  lockHadFocus = false;
  // 终审必改：按重绘前后段落序列对齐换算光标所在段；对不上就不写入，只提示
  const placed = editable.value && restoreMappedCaret(root, before, caret);
  if (ime) noteAction("载入期间用输入法输入的内容没能写入（编辑区当时暂不可编辑），请重新输入。", 10000, "caret");
  if (!typed) return;
  if (placed && window.document.execCommand("insertText", false, typed)) return;
  // F-M4：光标所在项因冲突换成了对方版本、焦点已交给冲突续写输入框：锁定期间敲的字也并入冲突副本
  if (caretSink) { caretSink.value += typed; caretSinkText = caretSinkPrefix + caretSink.value; markSinkPending(); void flushCaretSink(); return; }
  noteAction(`载入期间输入的“${typed.slice(0, 20)}${typed.length > 20 ? "…" : ""}”没能写入正文，请在需要的位置重新输入。`, 10000, "caret");
}
async function acceptContent(result: DocumentContentResult, options: AcceptOptions = {}): Promise<boolean> {
  const record = document.value;
  if (!record) return false;
  // A07：保存/刷新后对象 ID 会变（新增块、合并），重新取归属与块标识基准
  queueMicrotask(() => void refreshOwnership(record));
  if (options.yieldToTyping) {
    // 终验 F-M1：他人更新的自动刷新不锁编辑区——先在后台取好版式，期间用户一输入就放弃这次刷新（改为显示提示条）
    const started = Date.now();
    const before = options.carryTyping && editor.value ? blockTexts(editor.value) : null;
    const stopReason = reasonLater(options.reason || "正在载入最新内容…");
    let preview: Awaited<ReturnType<typeof previewDocument>>;
    try { preview = await previewDocument(record.id); }
    catch { return false; }
    finally { stopReason(); }
    if (document.value !== record || documentId.value !== record.id) return false;
    if (refreshingEditor.value) return false;
    if (!options.stateApplied && (isDirty.value || saving.value)) return false;
    // 载入期间用户打了字：只有“在一段里连续插入”且该段在新版本里原样存在时，才重绘并把这串字原位写回；否则放弃重绘
    let carry: ReturnType<typeof typedInsertion> = null;
    let target = -1;
    if (lastInputAt >= started) {
      if (!before || composing || !editor.value) return false;
      const now = captureCaret(editor.value);
      carry = typedInsertion(before, blockTexts(editor.value), now.length === 2 && now[0].index === now[1].index && now[0].offset === now[1].offset ? now[0] : undefined);
      if (!carry) return false;
      const probe = window.document.createElement("div");
      probe.innerHTML = preview.html;
      // 终审必改：按前后两版段落序列对齐换算位置；对不上或不唯一就放弃重绘（保留编辑区，下次保存由服务器合并）
      target = mapBlockIndex(before, blockTexts(probe), carry.index);
      if (target < 0) return false;
    }
    if (result.formatTemplate) record.formatTemplate = result.formatTemplate;
    aiUndo.value = null;
    if (!options.stateApplied) {
      record.revision = result.revision;
      record.status = result.status;
      record.markdownContent = result.markdownContent;
      markdownContent.value = result.markdownContent;
      savedContent.value = result.markdownContent;
      persistLocalDraft();
    }
    const root = editor.value;
    const caret = root && window.document.activeElement === root ? captureCaret(root) : [];
    const shownBefore = root ? blockTexts(root) : [];
    showEditorHtml(preview.html, preview.objectModel);
    if (root && carry) {
      const point = { index: target, offset: carry.offset };
      const placed = blockTexts(root)[target] === carry.text && restoreCaret(root, [point, point]) && window.document.execCommand("insertText", false, carry.insert);
      if (!placed) {
        // 极少见：实际版式与预取的不一致。不猜位置，把字留在提示里
        leaveEditorCaret(root);
        noteAction(`载入期间输入的“${carry.insert.slice(0, 40)}”没能写入正文，请在需要的位置重新输入。`, 15000, "caret");
      }
    } else if (root && caret.length) restoreMappedCaret(root, shownBefore, caret);
    return true;
  }
  if (result.formatTemplate) record.formatTemplate = result.formatTemplate;
  aiUndo.value = null;
  beginEditorLock(options.reason || "正在刷新编辑区…");
  record.revision = result.revision;
  record.status = result.status;
  record.markdownContent = result.markdownContent;
  markdownContent.value = result.markdownContent;
  savedContent.value = result.markdownContent;
  persistLocalDraft();
  try {
    const preview = await previewDocument(record.id);
    if (document.value === record && documentId.value === record.id) showEditorHtml(preview.html, preview.objectModel);
    return true;
  } catch (error) {
    if (document.value === record) {
      errorText.value = `内容已保存，编辑视图加载失败：${messageOf(error)}`;
      // Block editing stale HTML; loading the saved content is safe to retry.
      phase.value = "error";
    }
    return false;
  } finally {
    endEditorLock();
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
    addHeadingOpen.value = false;
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
const suggestionCallbacks: SuggestionCallbacks = {
  sanitize: html => DOMPurify.sanitize(html, { FORBID_TAGS: ["iframe", "form", "input", "button", "textarea"] }),
  onResolve: (_item, accepted) => {
    if (accepted) aiSuggestionAccepted.value += 1;
    aiSuggestionTick.value += 1;
    if (!aiPendingSuggestions.value.length) finishSuggestions();
    else focusSuggestion(Math.min(aiSuggestionCursor.value, aiPendingSuggestions.value.length - 1));
  },
};
function focusSuggestion(position: number) {
  const items = aiPendingSuggestions.value;
  if (!items.length) return;
  aiSuggestionCursor.value = (position + items.length) % items.length;
  for (const item of aiSuggestions.value) item.control.classList.remove("current");
  const item = items[aiSuggestionCursor.value];
  item.control.classList.add("current");
  item.control.scrollIntoView({ behavior: "smooth", block: "center" });
}
function resolveAllSuggestions(accepted: boolean) {
  for (const item of [...aiPendingSuggestions.value]) resolveSuggestion(item, accepted, suggestionCallbacks);
}
function resolveCurrentSuggestion(accepted: boolean) {
  const item = aiPendingSuggestions.value[aiSuggestionCursor.value];
  if (item) resolveSuggestion(item, accepted, suggestionCallbacks);
}
function suggestionKeys(event: KeyboardEvent) {
  if (!event.altKey || !aiPendingSuggestions.value.length) return;
  if (event.key === "Enter") resolveCurrentSuggestion(true);
  else if (event.key === "Backspace") resolveCurrentSuggestion(false);
  else if (event.key === "ArrowDown") focusSuggestion(aiSuggestionCursor.value + 1);
  else if (event.key === "ArrowUp") focusSuggestion(aiSuggestionCursor.value - 1);
  else return;
  event.preventDefault();
}
function finishSuggestions() {
  window.removeEventListener("keydown", suggestionKeys);
  const accepted = aiSuggestionAccepted.value;
  aiSuggestions.value = [];
  aiSuggestionAccepted.value = 0;
  clearAiDraft();
  // Accepted suggestions are now ordinary editor content: the normal save path (blockIds, conflicts) applies.
  if (accepted) onEditorInput();
}
function clearAiDraft() {
  if (aiPendingSuggestions.value.length) {
    window.removeEventListener("keydown", suggestionKeys);
    const items = aiSuggestions.value;
    aiSuggestions.value = [];
    for (const item of pendingSuggestions(items)) resolveSuggestion(item, false, { ...suggestionCallbacks, onResolve: () => undefined });
    if (aiSuggestionAccepted.value) { aiSuggestionAccepted.value = 0; void nextTick(() => onEditorInput()); }
  }
  aiSuggestionDropped.value = 0;
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
  if (opBusy.value) return;
  // 重塑：点目录只定位章节，不再强行打开写作助手（写作助手在文档栏）；编辑区锁定时不改 AI 范围
  if (!aiLocked.value) {
    if (selectedHeading.value !== index || aiScope.value !== "section") clearAiDraft();
    selectedText.value = "";
    selectedRange = null;
    aiScope.value = "section";
  }
  selectedHeading.value = index;
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
function guidanceText(item: WritingGuidanceItem): string {
  const edit = guidanceEdits.value[item.id];
  return edit ? edit.text ?? item.sourceText : item.text;
}
function editGuidance(item: WritingGuidanceItem, text: string | null) {
  const effective = text ?? item.sourceText;
  const edits = { ...guidanceEdits.value };
  if (effective === item.text) delete edits[item.id];
  else edits[item.id] = { text, base: edits[item.id]?.base ?? item.text, heading: item.heading };
  guidanceEdits.value = edits;
  guidanceError.value = "";
}
function checkGuidanceChanges(groups: WritingGuidanceGroup[]) {
  const conflicts: Record<string, string> = {};
  for (const group of groups) for (const item of group.items) {
    const edit = guidanceEdits.value[item.id];
    if (edit && edit.base !== item.text) conflicts[item.id] = item.text;
  }
  guidanceConflicts.value = { ...guidanceConflicts.value, ...conflicts };
}
function resolveGuidanceChange(item: WritingGuidanceItem, keepMine: boolean) {
  const latest = guidanceConflicts.value[item.id];
  if (latest === undefined) return;
  const edits = { ...guidanceEdits.value };
  if (keepMine && edits[item.id]) edits[item.id] = { ...edits[item.id], base: latest };
  else delete edits[item.id];
  guidanceEdits.value = edits;
  const conflicts = { ...guidanceConflicts.value };
  delete conflicts[item.id];
  guidanceConflicts.value = conflicts;
}
async function loadGuidance(force = false): Promise<boolean> {
  if (!document.value || !canGenerate.value || (!force && (!aiOpen.value || aiBusy.value || aiPreviewHost.value || aiInserting.value || refreshingEditor.value || guidanceSaving.value))) return false;
  guidanceController?.abort();
  const controller = new AbortController();
  guidanceController = controller;
  const id = document.value.id, revision = document.value.revision;
  const scope = aiScope.value, heading = selectedHeading.value, target = targetVersion;
  const current = () => !controller.signal.aborted && guidanceController === controller && document.value?.id === id && document.value?.revision === revision && targetVersion === target && aiScope.value === scope && selectedHeading.value === heading;
  guidancePhase.value = "loading";
  guidanceError.value = "";
  try {
    if (isDirty.value) throw new Error("请先保存正文，再读取当前范围的编写建议。");
    const selection = scope === "selection" ? await resolveSelection() : undefined;
    const result = await queryWritingGuidance(id, revision, scope, scope === "document" ? null : heading, selection, controller.signal);
    if (!current()) return false;
    checkGuidanceChanges(result.groups);
    guidanceGroups.value = result.groups;
    guidanceNotices.value = result.notices || [];
    guidancePhase.value = "ready";
    return true;
  } catch (error) {
    if (current()) { guidancePhase.value = "error"; guidanceError.value = messageOf(error); }
    return false;
  }
}
async function saveGuidance(forGeneration = false): Promise<boolean> {
  if (!document.value || readonly.value || guidanceSaving.value || (!forGeneration && !canStartAi.value)) return false;
  if (!guidanceChanged.value) return true;
  guidanceSaving.value = true;
  guidanceError.value = "";
  let saved = false;
  window.clearTimeout(autoSaveTimer);
  try {
    if (isDirty.value) await save();
    if (isDirty.value) throw new Error("正文尚未保存，请保存后重试。");
    const record = document.value;
    const all = await queryWritingGuidance(record.id, record.revision, "document", null);
    checkGuidanceChanges(all.groups);
    if (Object.keys(guidanceConflicts.value).length) throw new Error("编写建议已被更新，请核对变更后再保存。");
    const items = Object.entries(guidanceEdits.value).map(([id, value]) => ({ id, text: value.text }));
    if (items.some(item => item.text !== null && (!item.text.trim() || item.text.length > 20000))) throw new Error("编写建议不能为空，且不能超过 20000 字。");
    const result = await updateWritingGuidance(record.id, all.revision, items);
    if (document.value !== record) return false;
    if (result.formatTemplate) record.formatTemplate = result.formatTemplate;
    record.revision = result.revision;
    aiUndo.value = null;
    guidanceEdits.value = {};
    guidanceConflicts.value = {};
    saved = true;
    noteAction("编写建议已保存到本篇文档。");
    return true;
  } catch (error) {
    guidanceError.value = messageOf(error);
    return false;
  } finally {
    guidanceSaving.value = false;
    if (!forGeneration && saved) void loadGuidance();
  }
}
function discardGuidance() {
  guidanceEdits.value = {};
  guidanceConflicts.value = {};
  guidanceError.value = "";
}
watch([aiOpen, aiScope, selectedHeading, selectedText, () => document.value?.revision, isDirty, aiBusy, aiPreviewHost, aiInserting, refreshingEditor], () => {
  if (aiOpen.value && canGenerate.value && !aiBusy.value && !aiPreviewHost.value && !aiInserting.value && !refreshingEditor.value && !guidanceSaving.value) void loadGuidance();
});
function captureEditorTarget() {
  if (aiLocked.value) return;
  const selection = window.getSelection();
  if (!editor.value || !selection?.rangeCount) return;
  const range = selection.getRangeAt(0).cloneRange();
  if (!editor.value.contains(range.startContainer) || !editor.value.contains(range.endContainer)) return;
  lastCaret = range.cloneRange();
  const startElement = range.startContainer instanceof Element ? range.startContainer : range.startContainer.parentElement;
  const blockTag = startElement?.closest("h1,h2,h3,h4,h5,h6,p,li,td,th,pre")?.tagName.toLowerCase() || "p";
  paraStyle.value = /^h[1-3]$/.test(blockTag) ? blockTag : /^h[4-6]$/.test(blockTag) ? "h3" : "p";
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
  // 27 复验 M-3：选区碰到只读（他人负责 / 未分配）内容时不能用 AI 改写
  const touchesLocked = !range.collapsed && [...editor.value.querySelectorAll("[data-a07-lock]")].some(node => range.intersectsNode(node));
  if (touchesLocked && text.trim()) {
    clearAiDraft();
    selectedText.value = "";
    selectedRange = null;
    aiScope.value = "selection";
    aiError.value = "所选范围包含他人负责的内容，请只选择你负责的部分。";
  } else if (!range.collapsed && text.trim() && parts && text.length <= 8000) {
    if (!selectedRange || selectedRange.compareBoundaryPoints(Range.START_TO_START, range) !== 0 || selectedRange.compareBoundaryPoints(Range.END_TO_END, range) !== 0 || selectedText.value !== text) clearAiDraft();
    selectedText.value = text;
    selectedRange = range.cloneRange();
    aiScope.value = "selection";
  } else if (!range.collapsed && text.trim() && !insideHeading) {
    clearAiDraft();
    selectedText.value = "";
    selectedRange = null;
    aiScope.value = "selection";
    // 重塑：选区不合适时只在写作助手里说明，不再自动打开面板
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
  previewModel.value = preview.objectModel ?? previewModel.value;
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
  const moduleStart = aiProgress.value.map(step => step.stage).lastIndexOf("module");
  const repeated = aiProgress.value.findIndex((step, index) => index > moduleStart && step.stage === stage && step.message === message);
  if (repeated >= 0) aiProgress.value.splice(repeated, 1);
  const last = aiProgress.value.at(-1);
  if (last?.stage === stage) last.message = message;
  else aiProgress.value.push({ stage, message });
}
/** 方案 C：AI 改写的范围里有冻结区（冲突未处理）时不让 AI 改写。 */
function aiTouchesFrozen(): boolean {
  const root = editor.value;
  if (!root || !frozenOpen.value.length) return false;
  const blocks = frozenOpen.value.flatMap(entry => frozenEls(root, entry.key));
  if (!blocks.length) return false;
  if (aiScope.value === "document") return true;
  if (aiScope.value === "selection") return Boolean(selectedRange && blocks.some(block => selectedRange!.intersectsNode(block)));
  const heading = root.querySelectorAll("h1,h2,h3,h4,h5,h6")[selectedHeading.value ?? -1] as HTMLElement | undefined;
  if (!heading) return false;
  let top: Element | null = heading;
  while (top && top.parentElement !== root) top = top.parentElement;
  for (let next = top?.nextElementSibling; next; next = next.nextElementSibling) {
    // 与服务端编写范围一致：当前标题正文在下一个标题前结束。
    if (/^H[1-6]$/.test(next.tagName)) break;
    if (next.hasAttribute("data-frozen")) return true;
  }
  return false;
}
async function generateSection() {
  if (!document.value || !canGenerate.value || !canStartAi.value) return;
  if (aiTouchesFrozen()) { aiError.value = "本节有未处理的冲突，请先处理冲突再让 AI 改写。"; return; }
  clearAiDraft();
  const id = document.value.id;
  let index = aiScope.value === "document" ? null : selectedHeading.value;
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
    if (!await saveGuidance(true)) throw new Error(guidanceError.value || "编写建议未保存，请重试。");
    if (!await loadGuidance(true)) throw new Error(guidanceError.value || "无法确认当前范围的编写建议，请重试。");
    if (aiScope.value === "selection") index = guidanceGroups.value[0]?.headingIndex ?? null;
    if (aiScope.value === "selection") draftSelection = await resolveSelection();
    draftEditorVersion.value = version;
    placeAiPreview();
    showAiProgress("connecting", "正在连接编写服务");
    const scopeIds = aiScope.value === "section" && index !== null && editor.value ? (() => {
      const { heading, blocks } = sectionBlocks(editor.value, index);
      // A07: only blocks this user may change belong to the writing scope (spec 30 §4.2).
      const denied = new Set(assignmentState.value?.items.filter(item => !item.canEdit).map(item => item.objectId) || []);
      return [heading, ...blocks].map(node => node?.getAttribute("data-object-id")).filter((value): value is string => Boolean(value) && !denied.has(value as string));
    })() : undefined;
    const headingIndex = index;
    await streamDocumentSection(id, document.value.revision, index, draftInstruction, text, (event) => {
      if (!current() || controller.signal.aborted) return;
      if (editorVersion.value !== version || document.value?.revision !== event.revision || isDirty.value) throw new ApiError(409, "REVISION_CONFLICT", "文档已变化，请重新生成草稿。");
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
      if (event.type === "done" && event.operations && aiScope.value === "section" && headingIndex !== null && editor.value) {
        const { suggestions, dropped } = renderSuggestions(editor.value, headingIndex, event.operations, suggestionCallbacks);
        aiSuggestions.value = suggestions;
        aiSuggestionDropped.value = dropped;
        aiSuggestionAccepted.value = 0;
        aiPreviewHtml.value = "";
        if (suggestions.length) {
          window.addEventListener("keydown", suggestionKeys);
          void nextTick(() => focusSuggestion(0));
        }
        return;
      }
      aiPreviewHtml.value = DOMPurify.sanitize(html, { FORBID_TAGS: [...(aiScope.value === "selection" ? ["img"] : []), "iframe", "form", "input", "button", "textarea"] });
    }, controller.signal, aiScope.value, draftSelection, aiScope.value === "selection" ? selectionObjectIds() : undefined, scopeIds);
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
  if (readonly.value) { aiError.value = readonlyReason.value || "你没有修改正文的权限。"; return; }
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
    const result = await updateDocumentContent(snapshot.id, snapshot.revision, snapshot.content, undefined, snapshot.revision, { blockIds: await blockIdsReady(snapshot.content) });
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
function stopOriginal() {
  originalController?.abort();
  pageObserver?.disconnect();
  pageObserver = undefined;
  visiblePages.clear();
  for (const render of pageRenders.values()) { render.cancelled = true; render.task?.cancel(); }
  pageRenders.clear();
  pdfPages.value = [];
  void pdfTask?.destroy();
  pdfTask = null;
  pdfDocument = null;
}
async function loadOriginal(retried = false): Promise<void> {
  if (!document.value) return;
  stopOriginal();
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
    if (controller.signal.aborted) return;
    GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
    const task = openPdf({ data: new Uint8Array(await blob.arrayBuffer()) });
    if (controller.signal.aborted) { void task.destroy(); return; }
    pdfTask = task;
    const pdf = await task.promise;
    if (controller.signal.aborted) {
      void task.destroy();
      return;
    }
    pdfDocument = pdf;
    const pages: typeof pdfPages.value = [];
    for (let number = 1; number <= pdf.numPages; number += 1) {
      const page = await pdf.getPage(number);
      if (controller.signal.aborted) return;
      const viewport = page.getViewport({ scale: 1.5 });
      pages.push({ number, width: viewport.width, height: viewport.height, ready: false });
    }
    pdfPages.value = pages;
    originalPhase.value = "ready";
    await nextTick();
    if (controller.signal.aborted || !pdfScroll.value) return;
    pageObserver = new IntersectionObserver(entries => {
      if (controller.signal.aborted) return;
      for (const entry of entries) {
        const canvas = entry.target as HTMLCanvasElement;
        if (entry.isIntersecting) {
          visiblePages.add(canvas);
          void renderPage(canvas, controller);
        } else {
          visiblePages.delete(canvas);
          const render = pageRenders.get(canvas);
          if (render) { render.cancelled = true; render.task?.cancel(); pageRenders.delete(canvas); }
          pdfPages.value[Number(canvas.dataset.page) - 1].ready = false;
          // 离开附近页面时释放画布，长文档不累积整份 PDF 的像素内存。
          canvas.width = 0;
          canvas.height = 0;
        }
      }
    }, { root: pdfScroll.value, rootMargin: `${pdfScroll.value.clientHeight}px 0px` });
    pdfScroll.value.querySelectorAll<HTMLCanvasElement>("canvas").forEach(canvas => pageObserver!.observe(canvas));
  } catch (error) {
    if (controller.signal.aborted) return;
    stopOriginal();
    originalError.value = messageOf(error);
    originalPhase.value = "error";
  }
}
async function renderPage(canvas: HTMLCanvasElement, controller: AbortController) {
  if (!pdfDocument || controller.signal.aborted || !visiblePages.has(canvas) || pageRenders.has(canvas)) return;
  const pdf = pdfDocument;
  const frame = pdfPages.value[Number(canvas.dataset.page) - 1];
  if (frame.ready) return;
  const render: { cancelled: boolean; task?: RenderTask } = { cancelled: false };
  pageRenders.set(canvas, render);
  try {
    const page = await pdf.getPage(frame.number);
    if (controller.signal.aborted || render.cancelled) return;
    const viewport = page.getViewport({ scale: 1.5 });
    const context = canvas.getContext("2d");
    if (!context) throw new Error("无法绘制 Word 页面。");
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.floor(viewport.width * ratio);
    canvas.height = Math.floor(viewport.height * ratio);
    render.task = page.render({ canvas, canvasContext: context, viewport, transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0] });
    await render.task.promise;
    if (!controller.signal.aborted && !render.cancelled) frame.ready = true;
  } catch (error) {
    if (controller.signal.aborted || render.cancelled) return;
    stopOriginal();
    originalError.value = messageOf(error);
    originalPhase.value = "error";
  } finally {
    if (pageRenders.get(canvas) === render) pageRenders.delete(canvas);
  }
}
async function switchMode(next: "original" | "edit" | "layout") {
  if (opBusy.value) return;
  if (next !== "edit" && referencesDirty.value) { referencesPanel.value?.requestClose(); return; }
  if (next !== "edit" && isDirty.value) await save();
  if (next !== "edit" && isDirty.value) return;
  mode.value = next;
  selectedText.value = "";
  selectedRange = null;
  aiScope.value = "section";
  clearAiDraft();
  if (next !== "edit") await loadOriginal();
  else stopOriginal();
}
/** 按编辑区文字更新正文（冻结区已换成正文版本）；有平移关系时把编辑区的改动平移到正文上。 */
function syncValueFromEditor() {
  const dom = editorText();
  if (domPair) {
    const value = rebaseBlocks(domPair.dom, dom, domPair.value);
    if (value !== null) {
      domPair = { dom, value };
      markdownContent.value = value;
    } else {
      // 增量审查 M1：平移不了（如改到了对方刚改过、编辑区里还是旧文字的段落）：正文换成编辑区文字，
      // 以最近一次成功保存对应的编辑区文字为显式基准提交（同段才会冲突，已保存过的内容不会被当成对方的修改）
      fallbackToExplicit(domBase, dom);
    }
  } else markdownContent.value = dom;
}
function onEditorInput(event?: Event) {
  if (!editor.value) return;
  layoutWordTabs();
  lastInputAt = Date.now();
  aiUndo.value = null;
  syncValueFromEditor();
  if (frozenPending.value) scheduleSnapshot();
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
  // 复审：按下载时读到的那份删除（写入页面与保存时间都要对得上），期间被其他标签改写过的保留
  for (const draft of bundle) removeDraft(window.localStorage, account(), draft!.docId, draft!.tabId, { writer: draft!.writer, savedAt: draft!.savedAt });
  persistLocalDraft();
}
function currentDraft(): DraftV2 | null {
  const record = document.value;
  if (!record) return null;
  return {
    v: 2, docId: record.id, tabId: browserTab, docRevision: record.revision, mergeBase: mergeBase ?? record.revision, savedAt: Date.now(),
    ...(explicitBase !== null ? { baseMarkdown: explicitBase } : {}),
    wholeDocument: true, markdown: markdownContent.value, blocks: [], changed: pendingCount.value,
    headingPaths: headingPathsOf(markdownContent.value),
    // 方案 C（14 §3.5 第 6 条）：冻结区里还没存成快照的续写
    conflictsEdits: [
      ...frozenOpen.value.filter(entry => entry.mine !== entry.saved).map(entry => ({ conflictId: entry.conflictId, itemIndex: entry.itemIndex, markdown: entry.mine })),
      ...sinkBacklog.value.map(edit => ({ ...edit })),
      ...leftovers.value.map(edit => ({ ...edit })),
      ...(sinkUnsaved.value ? [{ ...sinkUnsaved.value }] : []),
      ...Object.values(limitedMine.value).map(edit => ({ ...edit })),
    ],
    pendingSave: pendingKey ? { clientSaveKey: pendingKey.key, sentAt: Date.now() } : null,
    writer: pageInstance,
  };
}
/** 每次输入同步写本机草稿；没有未保存修改时删除本标签页的草稿。写入失败进入 blocked（01 §6.5 第7条）。 */
function persistLocalDraft(): boolean {
  const record = document.value;
  if (!record || !browserTab) return false;
  window.clearTimeout(draftTimer);
  draftTimer = undefined;
  if (!isDirty.value && !frozenUnsaved.value && !sinkUnsaved.value && !sinkBacklog.value.length && !leftovers.value.length && !Object.keys(limitedMine.value).length) {
    // 审查 M1：只删本页面写的那份（复制出的标签若曾共用 tabId，不会删掉对方的草稿）。
    // 终验回归 H1：恢复条正列着本标签的旧草稿时不删（由“恢复/舍弃”处理）
    if (!localDrafts.value.some(draft => draft.tabId === browserTab)) removeDraft(window.localStorage, account(), record.id, browserTab, { writer: pageInstance });
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
  // 方案 C：正文里冻结区是服务器版本；“我的修改”要带上冻结区里我的版本
  let text = markdownContent.value;
  for (const entry of frozenOpen.value) {
    if (entry.theirs && text.includes(entry.theirs)) text = text.replace(entry.theirs, () => entry.mine);
    else if (entry.mine) text = `${text}\n\n${entry.mine}`;
  }
  download(myChangesFileName(document.value.name), text);
}
async function restoreLocalDraft() {
  const draft = localDraft.value, record = document.value;
  if (!draft || !record || draftBusy.value || readonly.value || !draft.markdown) return;
  draftBusy.value = true;
  let pending = false;
  const restoreStarted = Date.now();
  try {
    // 审查 S6：先等在途的保存结束，保持单飞。
    if (savePromise) await savePromise.catch(() => undefined);
    // 最后一审建议 3：上一份恢复的内容还没存上服务器（重试中/冲突未处理）时不恢复下一份，否则会把它覆盖掉
    if (isDirty.value || retrying.value || blocked.value) {
      noteAction("编辑区还有没保存到服务器的内容（可能是刚恢复的那份）。等保存成功或处理完冲突后再恢复下一份，以免覆盖。", 10000);
      return;
    }
    // T-M1（替代 F-H4 的“有任何未处理冲突就拒绝”）：只在本次恢复产生的冲突未处理、
    // 或这份草稿改到的段落有未处理冲突时拒绝——原因常驻在恢复条里（T-S1），处理完自动解除
    await refreshConflicts();
    if (document.value !== record) return;
    if (restoreRefusal.value) return;
    // 终审建议：恢复别的标签的草稿时，本标签的旧草稿先换个键保存——恢复后的编辑会写本标签的键，不能把它覆盖掉
    if (draft.tabId !== browserTab && !keepOwnDraftAside(record)) {
      noteAction("本机存储写入失败，请先处理本标签的草稿（恢复、下载或舍弃）再恢复这一份。", 10000);
      return;
    }
    // S5：服务器版本已变化时按草稿的基准修订做三方合并；改到同一段落的进入冲突面板，不覆盖别人的修改。
    markdownContent.value = draft.markdown;
    draftRestoring = true;
    try {
      // 增量审查 M1：草稿记着显式基准时按它合并（expectedRevision 用当前修订）
      if (typeof draft.baseMarkdown === "string") await saveContent(draft.markdown, "manual", record.revision, undefined, { baseMarkdown: draft.baseMarkdown });
      else await saveContent(draft.markdown, "manual", draft.docRevision === record.revision ? undefined : (draft.mergeBase ?? draft.docRevision));
    } finally {
      draftRestoring = false;
    }
    if (blocked.value || document.value !== record) return;
    const conflicted = lastConflictAt >= restoreStarted;
    if (conflicted) {
      // 恢复的内容与他人的修改冲突：冲突部分已存为冲突副本，其余已（或正在）保存；不是“重试中”
      // J2-L：冲突可能来自自己先恢复的另一份草稿，不说“他人”；没有下一份草稿时不提“再恢复下一份”
      const more = localDrafts.value.some(other => other.tabId !== draft.tabId);
      noteAction(`恢复的内容有部分与正文里已有的修改冲突，已存为冲突副本（见冲突面板）；其余内容已保存。${more ? "处理完冲突后再恢复下一份草稿。" : ""}`, 10000);
    }
    if (retrying.value || saving.value) {
      // 最后一审建议 3：没存上服务器之前不删这份草稿，也不解锁编辑区（编辑区里仍是旧正文，解锁后一输入就会把恢复的内容冲掉）。
      // 重试存上后由下面的 watch 收尾：按服务器正文重绘、删草稿、显示下一份。
      restorePending.value = { draft, record };
      pending = true;
      if (!conflicted) noteAction("恢复的内容还没存上服务器，正在自动重试；存上后会显示在编辑区，再处理下一份草稿。", 10000);
      return;
    }
    await finishRestore(draft, record);
  } finally {
    if (!pending) draftBusy.value = false;
  }
}
async function finishRestore(draft: DraftV2, record: DocumentRecord) {
  // 编辑区按服务器正文重绘（草稿条存在时 refreshEditorFormatting 会被锁住）。
  // 仍有未存上的部分（如冲突已转为冲突副本）时不重绘，与原逻辑一致。
  if (!isDirty.value && document.value === record) await acceptContent({ markdownContent: savedContent.value, revision: record.revision, status: record.status });
  removeDraft(window.localStorage, account(), record.id, draft.tabId, { savedAt: draft.savedAt });
  localDrafts.value = localDrafts.value.filter(item => !sameDraft(item, draft));
  localDraft.value = localDrafts.value[0] ?? null;
  // 恢复的是本标签自己的草稿时，保存期间写下的同键草稿已经落库，按本页面写入者清掉
  if (document.value === record) persistLocalDraft();
}
watch([restorePending, isDirty, retrying, saving, blocked], async () => {
  const pending = restorePending.value;
  if (!pending || saving.value || retrying.value) return;
  if (document.value !== pending.record) { restorePending.value = null; draftBusy.value = false; return; }
  if (blocked.value) {
    // 冲突/被拒：草稿保留在恢复条里，交给冲突处理；此时恢复下一份会被 restoreLocalDraft 拒绝
    restorePending.value = null;
    draftBusy.value = false;
    return;
  }
  restorePending.value = null;
  try {
    await finishRestore(pending.draft, pending.record);
    // 测试工程师（低）：存上之后撤掉“正在自动重试”的提示
    if (actionNote.value.startsWith("恢复的内容还没存上服务器")) noteAction("恢复的内容已保存。", 4000);
  } finally { draftBusy.value = false; }
});
/** 本标签的旧草稿改存到 “<tabId>-kept-<时间>” 键下（仍列在恢复条里）；没有这份草稿或改存成功时返回 true。 */
function keepOwnDraftAside(record: DocumentRecord): boolean {
  const own = localDrafts.value.find(item => item.tabId === browserTab);
  if (!own) return true;
  const moved: DraftV2 = { ...own, tabId: `${browserTab}-kept-${own.savedAt}` };
  if (writeDraft(window.localStorage, account(), moved) !== "ok") return false;
  removeDraft(window.localStorage, account(), record.id, own.tabId, { savedAt: own.savedAt });
  localDrafts.value = localDrafts.value.map(item => item === own ? moved : item);
  if (localDraft.value === own) localDraft.value = moved;
  return true;
}
/** 审查/验收 S11：舍弃用应用内二次确认（不再用浏览器原生 confirm）。 */
function discardLocalDraft() {
  const draft = localDraft.value;
  if (!draft || !document.value || draftBusy.value) return;
  if (!draftConfirmDiscard.value) { draftConfirmDiscard.value = true; return; }
  draftConfirmDiscard.value = false;
  removeDraft(window.localStorage, account(), document.value.id, draft.tabId, { savedAt: draft.savedAt });
  localDrafts.value = localDrafts.value.filter(item => !sameDraft(item, draft));
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
function noteAction(message: string, ms = 4000, kind: NoteKind = "") {
  // T-M2：“他人更新”类提示不覆盖光标/失焦类提示
  if (noteYields(actionNoteKind, Boolean(actionNote.value), kind)) return;
  actionNote.value = message;
  actionNoteKind = kind;
  actionNoteOthers.value = kind === "others";
  window.clearTimeout(actionNoteTimer);
  actionNoteTimer = window.setTimeout(() => { actionNote.value = ""; actionNoteKind = ""; }, ms);
}
// 审查员第 2 条：提示条出现时，立即清掉正在显示的“已显示/已合并”短提示
watch(othersUpdated, (value) => {
  if (value && actionNoteKind === "others") { actionNote.value = ""; actionNoteKind = ""; window.clearTimeout(actionNoteTimer); }
});
/** 底部状态栏“最近由 X 修改于 HH:mm”：取版本记录第一条（修订号变化后 1 秒内去抖刷新）。 */
function refreshLastEditor() {
  window.clearTimeout(lastEditorTimer);
  const record = document.value;
  if (!record) return;
  lastEditorTimer = window.setTimeout(async () => {
    try {
      const page = await listDocumentVersions(record.id, 0);
      const latest = page.items[0];
      if (document.value === record) lastEditor.value = latest?.createdBy ? { name: latest.createdBy.name, at: latest.createdAt } : null;
    } catch { /* 取不到时不显示 */ }
  }, 600);
}
watch(() => document.value?.revision, (value, previous) => { if (value !== undefined && previous !== undefined && value !== previous) refreshLastEditor(); });
function save(trigger: "auto" | "manual" = "auto"): Promise<void> {
  if (savePromise) return savePromise.then(() => (isDirty.value && !retrying.value && !blockedFor(markdownContent.value) ? save(trigger) : undefined));
  if (!document.value || !isDirty.value || aiPreviewHost.value || readonly.value) return Promise.resolve();
  if (trigger === "auto" && blockedFor(markdownContent.value)) return Promise.resolve();
  // 29 M-3：保存被拒后暂停自动保存（编辑同步到被拒保存），手动保存仍可再交
  if (trigger === "auto" && rejected.value) return Promise.resolve();
  // 增量审查建议：上一次提交结果不明（响应丢失/超时，正在重试）而之后又打了字：先原样重放上一次，确认后再提交新内容
  const previous = pendingKey;
  if (previous && retrying.value && previous.content !== markdownContent.value) {
    return saveContent(previous.content, trigger, previous.base, previous.editor, { baseMarkdown: previous.baseMarkdown, replay: previous })
      .then(() => (isDirty.value && !retrying.value && !blocked.value && !savePromise ? save(trigger) : undefined));
  }
  return saveContent(markdownContent.value, trigger, undefined, undefined, explicitBase !== null ? { baseMarkdown: explicitBase } : {});
}
function blockedFor(content: string) { return Boolean(blocked.value) && blocked.value!.content === content; }
/** 单飞保存；同一内容的重试复用同一个 clientSaveKey（01 §6.3）。 */
/**
 * baseRevision：本次提交所基于的修订（缺省为编辑区基准；有显式基准时为服务器最新修订）。
 * editor：冲突后自动重交可合并部分时，编辑区当时的正文（content）及其对应的编辑区文字（dom）——
 * 用户之后没再输入才用服务器正文替换编辑区，否则把之后的输入平移到服务器正文上。
 * extra.baseMarkdown：显式基准正文（增量审查 M1）；extra.replay：重放上一次结果不明的提交（同键、同基准）。
 */
function saveContent(content: string, trigger: "auto" | "manual", baseRevision?: number, editor?: EditorAtConflict,
  extra: { baseMarkdown?: string; replay?: NonNullable<typeof pendingKey> } = {}): Promise<void> {
  const record = document.value!;
  const memory = pendingMemory;
  const current = () => document.value === record && documentId.value === record.id;
  window.clearTimeout(retryTimer);
  window.clearTimeout(autoSaveTimer);
  saving.value = true;
  window.clearTimeout(savingTimer);
  savingTimer = window.setTimeout(() => { if (saving.value) showSaving.value = true; }, 400);
  // 增量审查建议 5：同一内容的普通重试复用 pendingKey 时，连同当时的基准修订和显式基准一起复用（键相同、请求体也相同，
  // 否则基准在重试之间变了会被服务器判为“同键不同内容”）
  const reuse = extra.replay ?? (pendingKey && pendingKey.content === content ? pendingKey : null);
  const explicit = reuse ? reuse.baseMarkdown ?? null : extra.baseMarkdown ?? null;
  const base = reuse ? reuse.base : explicit !== null ? (baseRevision ?? record.revision) : (baseRevision ?? mergeBase ?? record.revision);
  // 与本次提交对应的编辑区文字：平移中为 domPair.dom，冲突重交为冲突时的编辑区文字，其余就是提交的正文
  const dsend = reuse ? reuse.dsend : editor ? editor.dom : explicit === null && domPair && content === domPair.value ? domPair.dom : content;
  const gen = reuse ? reuse.gen : editor ? editor.gen : fallbackGen;
  if (!pendingKey || pendingKey.content !== content) {
    pendingKey = { content, key: crypto.randomUUID(), base, dsend, gen, editor, ...(explicit !== null ? { baseMarkdown: explicit } : {}) };
  }
  const key = pendingKey.key;
  flushDraft();
  savePromise = (async () => {
    try {
      const result = await updateDocumentContent(record.id, base, content, memory, undefined,
        { clientSaveKey: key, allowMerge: true, blockIds: await blockIdsReady(content), ...(explicit !== null ? { baseMarkdown: explicit } : {}) });
      if (!current()) return;
      let sent = editor?.content ?? content;
      // 测试工程师 N8：这次提交里有段落落在本人另一处（另一个标签页）已冲突、未处理的段上——服务器把它追加为快照、正文保留原样（200 + frozenByConflict）。
      // 本标签编辑区里这一段同样冻结（不按服务器正文重绘掉），并以本标签的身份存一份快照（卡片上按标签页区分，见 mineOthers）
      const unseen = (result.frozenByConflict || []).filter(cid => !frozenOpen.value.some(entry => entry.conflictId === cid));
      if (unseen.length && !draftRestoring && !editor) {
        const items = await conflictsToFreeze(record, unseen);
        if (!current()) return;
        if (items.length) {
          const unchanged = markdownContent.value === sent;
          const keys = freezeConflicts(items);
          for (const key of keys) {
            const entry = frozen.value[key];
            // 服务器上最新一份不是本标签存的（是这次重交追加的，不带标签页）：按本标签再存一份
            if (entry && entry.savedTab !== browserTab) entry.saved = "\u0000";
          }
          if (keys.length) {
            syncValueFromEditor();
            if (unchanged) sent = markdownContent.value;
            scheduleDraftWrite();
            void nextTick(() => applyConflictMarks());
            scheduleSnapshot(0);
            noteAction("这一段你在另一个标签页里也改过、正有未处理的冲突。本标签写的内容已冻结在编辑区并存为冲突副本，请在冲突面板一起处理。", 10000);
          }
        }
      }
      const typedNewer = markdownContent.value !== sent;
      const serverText = result.markdownContent ?? content;
      const changedText = serverText !== sent;
      savedContent.value = serverText;
      record.revision = result.revision;
      record.status = result.status;
      record.markdownContent = serverText;
      mergeBase = null;
      domBase = dsend;
      let rerender = false;
      if (fallbackGen !== gen && explicitBase !== null) {
        // 增量审查 M1：在途期间编辑区已退回显式基准（正文 = 编辑区文字）：基准前移到本次提交对应的编辑区文字
        explicitBase = dsend;
        persistLocalDraft();
      } else if (!typedNewer && !frozenOpen.value.length) {
        explicitBase = null;
        markdownContent.value = serverText;
        // 合并带进了别人的修改：编辑区按服务器正文重绘（不受 AI/草稿锁影响，否则编辑区会停在旧内容上）。
        // 编辑区还缺之前平移进来的对方修改（domPair）或按显式基准提交过（编辑区缺对方修改）时同样要重绘。
        rerender = changedText || Boolean(domPair) || explicit !== null;
      } else if (changedText) {
        // 方案 C：有冻结区时不重绘（冻结区节点、光标、组字都不动），同样按平移处理。
        // 终验 F-H2：编辑区有更新的输入，不能替换。把未保存的输入平移到服务器新正文上，下次以新版本为基准提交；
        // 平移不了（如对方也改了正在改的段）就以本次提交对应的编辑区文字为显式基准（增量审查 M1），不按旧修订合并。
        // 冲突后重交可合并部分时同样平移（增量审查建议：否则下次按旧基准提交，同一处冲突会再记一条）。
        explicitBase = null;
        if (!rebaseOnto(sent, serverText)) fallbackToExplicit(dsend);
      } else {
        explicitBase = null;
      }
      // 审查员第 2 条：提示条显示期间（含点了“立即刷新”的这次保存）不再补发“已合并”短提示
      // 设计师建议 1：冲突后重交其余部分时合并进来的正是冲突对方的那次修改（已在冲突卡里），不再误报“已合并”
      if (Date.now() - lastConflictAt > 10000) noteMerged(result);
      // 设计师建议 2：这次保存把续写追加成了冲突快照（列表项等）：冲突卡跟着刷新
      // 测试工程师 N8：本标签可能还没收到过这条冲突（另一个标签页遇到的）——冲突入口和计数一起刷新
      if (result.frozenByConflict?.length) void refreshConflicts().then(() => conflictPanel.value?.load());
      // 审查建议 4：冲突副本到了上限，冻结段这次的新内容没能再存进副本——常驻提示，请先处理冲突
      // 测试工程师 N3：存不进去的内容不丢——冻结区标为 limited；编辑区里没有这一段时（恢复草稿等）记进 limitedMine。处理时随请求带上
      if (result.snapshotLimited?.length) {
        for (const hit of result.snapshotLimited) {
          const key = `${hit.conflictId}:${hit.itemIndex}`;
          const entry = frozen.value[key];
          if (entry && !entry.resolved) entry.limited = true;
          else if (typeof hit.markdown === "string") limitedMine.value = { ...limitedMine.value, [key]: { conflictId: hit.conflictId, itemIndex: hit.itemIndex, markdown: hit.markdown } };
        }
        persistLocalDraft();
        sinkNote.value = { conflictId: result.snapshotLimited[0].conflictId, text: "这处冲突的副本已达到保存上限（20MB 或 500 份），冲突段里新写的内容没能再存进“我的版本”，已记在本页和本机草稿里。请在冲突面板处理这处冲突，处理时会直接用这一版；处理前不要清除浏览器数据。" };
      }
      // 界面复验 N4：保存时合并进来的他人修改，重绘后同样加蓝色块标记和目录蓝点
      const mergedIn = result.merged && rerender ? mergedInBlocks(content, serverText) : [];
      if (!typedNewer && !frozenOpen.value.length) othersUpdated.value = null;
      // 方案 C：有冻结区时不重绘，合并进来的他人修改先不显示在编辑区，给出“立即刷新”（刷新时冻结区保留）
      if (frozenOpen.value.length && changedText && result.changedBy?.length) {
        othersUpdated.value = { names: result.changedBy.map(person => person.name), objects: result.changedBy.reduce((sum, person) => sum + person.objects, 0) };
      }
      pendingKey = null;
      blocked.value = null;
      rejected.value = null;
      retrying.value = false;
      failures.value = 0;
      failingSince = null;
      savedAt.value = Date.now();
      persistLocalDraft();
      if (memory === pendingMemory && memory) { pendingMemory = undefined; void refreshMemory(); }
      if (rerender && !isDirty.value) {
        const who = (result.changedBy || []).map(person => person.name).join("、");
        const reason = `正在合并 ${who || "他人"} 的更新…`;
        const merged = { markdownContent: serverText, revision: record.revision, status: record.status };
        // 终验 F-M1：先不锁编辑区；载入期间用户继续输入就不重绘（编辑区保留他的字），把这些输入平移到新正文，平移不了以显式基准再合并
        const started = Date.now();
        let shown = await acceptContent(merged, { reason, yieldToTyping: true, stateApplied: true, carryTyping: true });
        if (!shown && current()) {
          // 终验 F-H2：重绘期间用户在打字而放弃重绘：同样把这些输入平移到服务器新正文上，不按旧基准提交；
          // 平移不了就以本次提交对应的编辑区文字为显式基准（增量审查 M1）。期间已退回显式基准的保持不变。
          if (lastInputAt >= started) { if (explicitBase === null && !rebaseOnto(sent, serverText)) fallbackToExplicit(dsend); }
          else shown = await acceptContent(merged, { reason });
        }
        if (shown && mergedIn.length && document.value === record) markUpdated(mergedIn.map(markdown => ({ id: "", markdown })), true);
      } else if (layoutNeedsRefresh && !isDirty.value) await refreshEditorFormatting(false);
    } catch (error) {
      if (current()) handleSaveError(error, content, trigger, editor ? editor.dom : dsend, gen, editor?.content, !editor && !draftRestoring);
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
/**
 * 终验 F-H2：保存（提交 sent）返回服务器正文 serverText 时编辑区里已有更新的输入：
 * 正文改为“serverText + 本地未保存修改”，并记下编辑区文字与之的对应（domPair），之后的输入照此平移。成功返回 true；
 * 平移不了返回 false，由调用方退回显式基准（fallbackToExplicit）。
 */
function rebaseOnto(sent: string, serverText: string): boolean {
  const value = rebaseBlocks(sent, markdownContent.value, serverText);
  if (value === null || !editor.value) return false;
  // 测试工程师 F-H3 的硬性不变量：基准只在“正文 = 服务器新版本 + 我未保存的修改”时前移。
  // 服务器新版本里相对 sent 新出现的段落（对方写进来的）必须原样留在平移结果里，否则不前移，交给显式基准由服务器合并。
  if (!keepsServerChanges(sent, serverText, value, markdownContent.value)) return false;
  domPair = { dom: editorText(), value };
  markdownContent.value = value;
  persistLocalDraft();
  return true;
}
/** 编辑区文字（Markdown）。冲突/他人更新标记只用于显示，不进入正文；方案 C：冻结区换成服务器上这一段的版本，冻结区文字记进 mine。 */
function editorText(): string {
  if (!editor.value) return markdownContent.value;
  const root = editor.value;
  const copy = root.cloneNode(true) as HTMLElement;
  copy.querySelectorAll("[data-ai-preview]").forEach((node) => node.remove());
  copy.querySelectorAll(".blk-conflict,.blk-updated,.blk-flash").forEach((node) => {
    node.classList.remove("blk-conflict", "blk-updated", "blk-flash");
    if (!node.classList.length) node.removeAttribute("class");
  });
  const entries = Object.values(frozen.value);
  if (!entries.length) return editorMarkdown(turndown, DOMPurify.sanitize(copy.innerHTML, { RETURN_DOM: true }) as HTMLElement);
  const marks: Array<{ mark: string; theirs: string }> = [];
  const tops = topLevel(root);
  entries.forEach((entry, n) => {
    const holder = window.document.createElement("p");
    holder.textContent = frozenMark(n);
    const blocks = frozenEls(copy, entry.key);
    if (blocks.length) {
      const live = frozenEls(root, entry.key)[0];
      if (live) entry.index = Math.max(0, tops.indexOf(live));
      blocks[0].before(holder);
      const wrap = window.document.createElement("div");
      for (const block of blocks) wrap.appendChild(block);
      const mine = editorMarkdown(turndown, DOMPurify.sanitize(wrap.innerHTML, { RETURN_DOM: true }) as HTMLElement);
      if (!entry.resolved && mine !== entry.mine) entry.mine = mine;
    } else {
      // 冻结区被整段删掉：占位放回原位置，“我的版本”为空（卡片写“你在编辑区删除了这一段的全部文字”）
      const kids = topLevel(copy);
      const at = kids[Math.min(entry.index, kids.length)];
      if (at) at.before(holder); else copy.appendChild(holder);
      if (!entry.resolved && entry.mine !== "") entry.mine = "";
    }
    marks.push({ mark: frozenMark(n), theirs: entry.theirs });
  });
  return substituteFrozen(editorMarkdown(turndown, DOMPurify.sanitize(copy.innerHTML, { RETURN_DOM: true }) as HTMLElement), marks);
}
function topLevel(root: HTMLElement): HTMLElement[] {
  return ([...root.children] as HTMLElement[]).filter(block => !block.hasAttribute("data-ai-preview"));
}
function frozenEls(root: HTMLElement, key: string): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>(`:scope > [data-frozen="${CSS.escape(key)}"]`)];
}
function blocksMarkdown(blocks: HTMLElement[]): string {
  const wrap = window.document.createElement("div");
  for (const block of blocks) wrap.appendChild(block.cloneNode(true));
  wrap.querySelectorAll(".blk-conflict,.blk-updated,.blk-flash").forEach(node => node.classList.remove("blk-conflict", "blk-updated", "blk-flash"));
  return editorMarkdown(turndown, DOMPurify.sanitize(wrap.innerHTML, { RETURN_DOM: true }) as HTMLElement);
}
/**
 * 冲突段在编辑区里对应的连续顶层块：按冲突对象 ID（预览给每个顶层块标了 data-object-id，回车分出的段会带上同一个 ID）。
 * 对象 ID 在列表项等嵌套块上、或对不上连续块时返回 null（不冻结，按原流程处理）。
 */
function regionFor(root: HTMLElement, item: ConflictItem): HTMLElement[] | null {
  const ids = new Set((item.objectIds || []).map(String));
  if (!ids.size) return null;
  for (const id of ids) {
    const node = root.querySelector(`[data-object-id="${CSS.escape(id)}"]`);
    if (node && node.parentElement !== root) return null;
  }
  const tops = topLevel(root);
  const hits = tops.map((block, index) => (ids.has(block.getAttribute("data-object-id") || "") ? index : -1)).filter(index => index >= 0);
  if (!hits.length || hits[hits.length - 1] - hits[0] + 1 !== hits.length) return null;
  let region = tops.slice(hits[0], hits[hits.length - 1] + 1);
  if (region.some(block => block.hasAttribute("data-frozen") || /^(UL|OL)$/.test(block.tagName))) return null;
  // 粘贴/回车分出的、不带对象 ID 的紧随段：正好补齐冲突里“我的版本”时一并纳入
  const want = item.mine?.markdown || "";
  if (want && blocksMarkdown(region) !== want) {
    let extended = region;
    for (let next = hits[hits.length - 1] + 1; next < tops.length && next <= hits[hits.length - 1] + 20 && !tops[next].hasAttribute("data-object-id"); next += 1) {
      extended = [...extended, tops[next]];
      if (blocksMarkdown(extended) === want) { region = extended; break; }
    }
  }
  return region;
}
/** 方案 C：409 冲突返回时把冲突段冻结在编辑区（不重绘、不动光标与组字）。返回已冻结的条目键。 */
function freezeConflicts(items: ConflictItem[]): string[] {
  const root = editor.value;
  if (!root) return [];
  const keys: string[] = [];
  for (const item of items) {
    const key = `${item.conflictId}:${item.itemIndex}`;
    if (frozen.value[key] && !frozen.value[key].resolved) { keys.push(key); continue; }
    const theirsDeleted = Boolean(item.theirs?.deleted);
    if (!(item.reason === "MODIFIED" || (item.reason === "DELETED" && theirsDeleted && !item.mine?.deleted))) continue;
    const region = regionFor(root, item);
    if (!region) continue;
    region.forEach(block => block.setAttribute("data-frozen", key));
    const original = item.mine?.markdown || "";
    const latest = item.mineLatest?.markdown ?? original;
    frozen.value[key] = {
      key, conflictId: item.conflictId, itemIndex: item.itemIndex, name: conflictName(item), objectIds: (item.objectIds || []).map(String),
      deleted: theirsDeleted, theirs: theirsDeleted ? "" : item.theirs?.markdown || "", original, mine: blocksMarkdown(region), saved: latest,
      savedAt: item.mineLatest?.savedAt ?? item.createdAt ?? null, savedTab: item.mineLatest?.tabId ?? null, snapshotId: item.mineLatest?.snapshotId ?? null, failing: false,
      index: Math.max(0, topLevel(root).indexOf(region[0])), resolved: false,
    };
    keys.push(key);
  }
  if (keys.length && frozenPending.value) scheduleSnapshot(0);
  return keys;
}
/** 测试工程师 N8：保存响应里 frozenByConflict 指向、本标签还没冻结的冲突条目 */
async function conflictsToFreeze(record: DocumentRecord, ids: string[]): Promise<ConflictItem[]> {
  try {
    const items = await listConflicts(record.id, "open", "me");
    return items.filter(item => ids.includes(item.conflictId) && !(frozen.value[`${item.conflictId}:${item.itemIndex}`] && !frozen.value[`${item.conflictId}:${item.itemIndex}`].resolved));
  } catch { return []; }
}
/** 冻结区续写的快照：与自动保存同一节拍（停手 3 秒）；同一份内容重发用同一个键。 */
function scheduleSnapshot(delay = 3000) {
  window.clearTimeout(snapshotTimer);
  snapshotTimer = window.setTimeout(() => void flushSnapshots(), delay);
}
async function flushSnapshots(): Promise<void> {
  window.clearTimeout(snapshotTimer);
  if (snapshotFlight) { await snapshotFlight.catch(() => undefined); if (!frozenPending.value) return; }
  const record = document.value;
  if (!record || readonly.value) return;
  if (editor.value) editorText();
  const work = frozenOpen.value.filter(entry => entry.mine !== entry.saved && !entry.limited);
  if (!work.length) return;
  snapshotFlight = (async () => {
    let failed = false;
    for (const entry of work) {
      const markdown = entry.mine;
      let sending = snapshotKeys.get(entry.key);
      // 同一份内容重发用同一个键和序号（幂等）；内容变了才换键、取更大的序号
      if (!sending || sending.markdown !== markdown) { snapshotSeq = nextSeq(snapshotSeq); sending = { markdown, key: crypto.randomUUID(), seq: snapshotSeq }; snapshotKeys.set(entry.key, sending); }
      try {
        const result = await saveConflictMine(record.id, entry.conflictId, entry.itemIndex, markdown, sending.key, browserTab || undefined, sending.seq);
        if (document.value !== record) return;
        // stale：本标签更新的一份已先到（乱序），服务器保留那份；这份内容视为已处理
        entry.saved = result.stale ? markdown : result.mineLatest.markdown;
        entry.savedAt = result.mineLatest.savedAt;
        entry.savedTab = result.mineLatest.tabId;
        entry.snapshotId = result.mineLatest.snapshotId ?? null;
        entry.failing = false;
        snapshotKeys.delete(entry.key);
      } catch (error) {
        if (error instanceof ApiError && error.code === "CONFLICT_ALREADY_RESOLVED") {
          // 设计师必改 B：在另一个标签页处理掉了。多写的字不能当成已保存：先保底，再撤冻结、按服务器正文刷新、接回段末
          resolvedElsewhere.push(entry);
          continue;
        }
        if (error instanceof ApiError && error.code === "SNAPSHOT_LIMIT") {
          // 副本到上限（每条 500 份或总大小 20MB，422）：内容仍在编辑区和本机草稿里，提示先处理冲突（不再自动重试）
          // 测试工程师 N3：不再要求“先保存”（存不进去会死锁）——标为 limited，处理时把编辑区这一版随请求带上
          entry.limited = true;
          entry.failing = false;
          noteAction("这处冲突的副本已到上限（份数或总大小 20MB），新写的内容没能存进冲突副本，仍在编辑区和本机草稿里。请在冲突面板处理这条冲突，处理时会直接用编辑区里的这一版。", 10000);
          continue;
        }
        entry.failing = true;
        failed = true;
      }
    }
    persistLocalDraft();
    if (resolvedElsewhere.length) void settleResolvedElsewhere(resolvedElsewhere.splice(0));
    if (failed) scheduleSnapshot(Math.min(30000, backoffMs(Math.max(1, failures.value + 1))));
  })();
  try { await snapshotFlight; } finally { snapshotFlight = undefined; }
}
const resolvedElsewhere: Frozen[] = [];
/** 两版之间多出来的文字（去掉公共前后缀后中间那段） */
function extraText(saved: string, mine: string): string {
  let head = 0;
  while (head < saved.length && head < mine.length && saved[head] === mine[head]) head += 1;
  let tail = 0;
  while (tail < saved.length - head && tail < mine.length - head && saved[saved.length - 1 - tail] === mine[mine.length - 1 - tail]) tail += 1;
  return mine.slice(head, mine.length - tail).replace(/^\s+|\s+$/g, "");
}
const plainOf = (markdown: string) => markdown.replace(/^\s*(?:[-*+]|\d+[.)]|#{1,6})\s+/gm, "").replace(/[*_`~\\|]/g, "").replace(/\s+/g, " ").trim();
/** 设计师必改 B：冲突已在另一个标签页处理。①草稿保底（冻结区内容仍在 conflictsEdits）②撤冻结 ③按服务器正文刷新 ④多写的字接回该段末尾并提示，走正常保存 */
async function settleResolvedElsewhere(entries: Frozen[]) {
  for (const entry of entries) {
    if (entry.resolved) continue;
    const extra = extraText(entry.saved, entry.mine);
    const keep = extra ? { conflictId: entry.conflictId, itemIndex: entry.itemIndex, markdown: entry.mine } : null;
    if (keep) leftovers.value = [...leftovers.value, keep];
    entry.resolved = true;
    persistLocalDraft();
    const ids = new Set(entry.objectIds);
    const texts = [entry.saved, entry.mine, entry.theirs].map(md => plainOf(md.split(/\n{2,}/).filter(Boolean).pop() || "")).filter(Boolean);
    await reattachLeftover(extra, entry.conflictId, root => {
      const tops = topLevel(root);
      const byId = tops.filter(block => ids.has(block.getAttribute("data-object-id") || ""));
      if (byId.length) return byId[byId.length - 1];
      const norm = (el: HTMLElement) => (el.textContent || "").replace(/\s+/g, " ").trim();
      for (const text of texts) {
        const hit = tops.find(block => norm(block) === text) || tops.find(block => text.length >= 6 && norm(block).startsWith(text.slice(0, 12)));
        if (hit) return hit;
      }
      return tops[Math.min(entry.index, tops.length - 1)] ?? null;
    });
    // 深层 ref 里是 Proxy，不能与原对象 ===（同设计师上轮必改 3）
    if (keep) { leftovers.value = leftovers.value.filter(item => !(item.conflictId === keep.conflictId && item.itemIndex === keep.itemIndex)); persistLocalDraft(); }
  }
  void refreshConflicts().then(() => conflictPanel.value?.load());
}
/** 把多写的字接回某段末尾（普通修改，按最新修订保存）；接不回就放进常驻提示 */
async function reattachLeftover(text: string, conflictId: string, find: (root: HTMLElement) => HTMLElement | null): Promise<boolean> {
  const record = document.value;
  if (!record) return false;
  const quoted = text.length > 40 ? `${text.slice(0, 40)}…` : text;
  if (text) sinkNote.value = { conflictId: "", text: `这条冲突已在你另一个标签页处理。你之后在这里写的“${text}”没能存进冲突副本，正在接回正文…` };
  try {
    window.clearTimeout(autoSaveTimer);
    if (isDirty.value) await save("auto");
    if (savePromise) await savePromise.catch(() => undefined);
    await refreshFromServer({ reason: "正在载入另一个标签页的处理结果…" }, true);
  } catch { /* 刷新失败：下面按当前编辑区接回 */ }
  if (document.value !== record) return false;
  if (!text) { if (sinkNote.value?.conflictId === conflictId) sinkNote.value = null; noteAction("这条冲突已在你另一个标签页处理，编辑区已按处理结果更新。", 6000); return true; }
  const root = editor.value;
  const block = root && editable.value ? find(root) : null;
  if (root && block) {
    root.focus({ preventScroll: true });
    const range = window.document.createRange();
    range.selectNodeContents(block);
    range.collapse(false);
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);
    if (window.document.execCommand("insertText", false, text)) {
      persistLocalDraft();
      // N1 复核（mtab T3）：接回后马上保存，不等停手 3 秒（用户可能接着就关页）
      syncValueFromEditor();
      if (isDirty.value) void save("auto");
      sinkNote.value = { conflictId: "", text: `这条冲突已在你另一个标签页处理。你之后在这里写的“${quoted}”已接到这一段末尾，请检查。` };
      return true;
    }
  }
  sinkNote.value = { conflictId: "", text: `这条冲突已在你另一个标签页处理。你之后在这里写的“${text}”没能接回正文，请复制后粘贴到需要的位置。` };
  return false;
}
/** 重绘前取下冻结区节点（连同光标），重绘后放回服务器上这一段的位置（按对象 ID，找不到时放回原位置）。 */
function detachFrozen(root: HTMLElement) {
  frozenCaret = null;
  const kept: Array<{ entry: Frozen; blocks: HTMLElement[] }> = [];
  for (const entry of Object.values(frozen.value)) {
    if (entry.resolved) { delete frozen.value[entry.key]; snapshotKeys.delete(entry.key); continue; }
    const blocks = frozenEls(root, entry.key);
    blocks.forEach(block => block.classList.remove("blk-conflict"));
    kept.push({ entry, blocks });
  }
  const selection = window.getSelection();
  const range = selection?.rangeCount ? selection.getRangeAt(0) : null;
  const inside = range && kept.some(({ blocks }) => blocks.some(block => block.contains(range.startContainer)) && blocks.some(block => block.contains(range.endContainer)));
  const caret = inside && range ? { sc: range.startContainer, so: range.startOffset, ec: range.endContainer, eo: range.endOffset } : null;
  return { kept, caret };
}
function reattachFrozen(root: HTMLElement, state: ReturnType<typeof detachFrozen>) {
  for (const { entry, blocks } of state.kept) {
    const ids = new Set(entry.objectIds);
    const tops = topLevel(root);
    const target = tops.filter(block => ids.has(block.getAttribute("data-object-id") || ""));
    if (target.length) entry.theirs = blocksMarkdown(target);
    if (!blocks.length) { target.forEach(block => block.remove()); continue; }
    const anchor = target[0] || tops[Math.min(entry.index, tops.length)] || null;
    for (const block of blocks) root.insertBefore(block, anchor);
    target.forEach(block => block.remove());
  }
  if (state.caret) {
    try {
      const range = window.document.createRange();
      range.setStart(state.caret.sc, state.caret.so);
      range.setEnd(state.caret.ec, state.caret.eo);
      const selection = window.getSelection();
      selection?.removeAllRanges();
      selection?.addRange(range);
      frozenCaret = range;
    } catch { frozenCaret = null; }
  }
}
// 重绘后光标仍在冻结区里（节点没换）：之后的“按段落对齐恢复光标”不再挪它
let frozenCaret: Range | null = null;
function keepFrozenCaret(root: HTMLElement): boolean {
  const range = frozenCaret;
  frozenCaret = null;
  if (!range || !root.contains(range.startContainer)) return false;
  root.contentEditable = String(editable.value);
  root.focus({ preventScroll: true });
  const selection = window.getSelection();
  selection?.removeAllRanges();
  selection?.addRange(range);
  return true;
}
/** 增量审查 M1：正文换回编辑区文字，以 base（与最近一次成功保存对应的编辑区文字）为显式基准，下次保存带 baseMarkdown。 */
function fallbackToExplicit(base: string, dom?: string) {
  explicitBase = base;
  mergeBase = null;
  domPair = null;
  fallbackGen += 1;
  markdownContent.value = dom ?? editorText();
  persistLocalDraft();
}
function handleSaveError(error: unknown, content: string, trigger: "auto" | "manual", dom: string, gen = fallbackGen, origin = content, fromEditor = false) {
  const apiError = error instanceof ApiError ? error : new ApiError(0, "DEPENDENCY_UNAVAILABLE", messageOf(error));
  switch (classifySaveError(apiError)) {
    case "conflict": {
      // 01 §5.4/§6.4：冲突部分已存为冲突副本；其余可合并的部分用新键重新提交。
      lastConflictAt = Date.now();
      conflictSummary.value = { ...(conflictSummary.value || { allOpen: null }), mineOpen: Math.max(1, conflictSummary.value?.mineOpen ?? 0) };
      if (actionNote.value.startsWith("恢复的内容还没存上服务器")) actionNote.value = "";
      retrying.value = false;
      pendingKey = null;
      const details = apiError.details || {};
      const appliable = typeof details.appliableMarkdown === "string" ? details.appliableMarkdown : null;
      const appliableBase = typeof details.appliableBaseRevision === "number" ? details.appliableBaseRevision : null;
      // 409 里的条目不带 conflictId（在 details 顶层），补上
      const conflicts = (Array.isArray(details.conflicts) ? details.conflicts as ConflictItem[] : [])
        .map(item => ({ ...item, conflictId: item.conflictId ?? (typeof details.conflictId === "string" ? details.conflictId : "") }));
      if (draftRestoring && typeof details.conflictId === "string") restoreConflictIds.value = [...new Set([...restoreConflictIds.value, details.conflictId])];
      // 方案 C：编辑区里的冲突段冻结（不重绘、不动光标与组字），正文这一段用服务器版本代替后按原基准重新提交其余部分
      const frozenKeys = fromEditor ? freezeConflicts(conflicts) : [];
      // 测试工程师 N9：本标签冲突前写的字要按本标签单独存一份快照（409 里的原始 mine 不带标签页，之后别的标签页重交会成为“最新一份”把它盖住）；
      // 在服务器确认存下之前，冻结段算“未保存”，立刻写进本机草稿
      for (const key of frozenKeys) {
        const entry = frozen.value[key];
        if (entry && !entry.resolved && entry.savedTab !== browserTab && entry.mine === entry.saved) entry.saved = "\u0000";
      }
      if (frozenKeys.length && frozenPending.value) {
        persistLocalDraft();
        scheduleSnapshot(0);
      }
      const allFrozen = conflicts.length > 0 && frozenKeys.length === conflicts.length;
      const loose = fromEditor ? conflicts.find(item => item.conflictId && !frozenKeys.includes(`${item.conflictId}:${item.itemIndex}`) && !item.mine?.deleted) : undefined;
      unfrozenConflict = loose ? { conflictId: loose.conflictId, itemIndex: loose.itemIndex, mine: loose.mine?.markdown || "", at: Date.now(), item: loose } : null;
      // 设计师必改 2：不能冻结的条目（列表项等）会换成对方的版本——从这一刻起就常驻说明，不等用户再打字
      if (loose && isWholeConflict(loose)) {
        // 设计师复验 2 C：整篇冲突不说“这一项已换成 X 的版本”
        sinkNote.value = { conflictId: loose.conflictId, text: wholeConflictNote(loose) };
        pendingSwap = null;
      } else if (loose) {
        const who = conflictName(loose);
        const tail = "你在这一项里写的内容（含之后接着写的）存为冲突副本“我的版本”，可在冲突面板里处理。";
        // 设计师 S1：换成对方版本要等重绘——先说“正在换成…”，编辑区里真换好了再说“已换成”；S6：对方删掉了这一项时不说“换成版本”
        const done = loose.theirs?.deleted ? `这一项与 ${who} 的修改冲突，这一项在正文中已被删除；${tail}` : `这一项与 ${who} 的修改冲突，正文里这一项已换成 ${who} 的版本；${tail}`;
        sinkNote.value = { conflictId: loose.conflictId, text: loose.theirs?.deleted ? done : `这一项与 ${who} 的修改冲突，正文里这一项正在换成 ${who} 的版本…；${tail}` };
        pendingSwap = loose.theirs?.deleted ? null : { conflictId: loose.conflictId, item: loose, text: done };
      }
      void refreshConflicts().then(() => conflictPanel.value?.load());
      if (conflictOpensPanel(trigger)) {
        openConflicts("mine");
        // 主动保存遇到冲突：打开面板并定位到卡片，焦点留在编辑区
        if (conflicts[0]) scrollToCard(`${conflicts[0].conflictId}:${conflicts[0].itemIndex}`);
      } else autoConflict.value = { count: Math.max(1, conflicts.length), name: conflicts[0] ? conflictName(conflicts[0]) : WHOLE_THEIRS_NAME, frozen: frozenKeys.length > 0, whole: conflicts.length > 0 && conflicts.every(isWholeConflict) ? wholeConflictNote(conflicts[0]) : "" };
      if (allFrozen) {
        syncValueFromEditor();
        scheduleDraftWrite();
        void nextTick(() => applyConflictMarks());
        queueMicrotask(() => void save("auto"));
        return;
      }
      if (appliable !== null && appliableBase !== null && document.value) {
        queueMicrotask(() => void saveContent(appliable, "auto", appliableBase, { content: origin, dom, gen }));
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
    case "readonly": {
      readonly.value = true;
      retrying.value = false;
      // A05：归档（409 PROJECT_ARCHIVED）与权限被调整（403 带 projectRole）按枚举选文案；其余沿用 403 定稿文案
      const role = typeof apiError.details?.projectRole === "string" ? apiError.details.projectRole : null;
      if (apiError.code === "PROJECT_ARCHIVED") {
        readonlyReason.value = readonlyText("archived", role);
        blocked.value = { message: readonlyBlockedText("archived", role), content, code: apiError.code };
      } else if (role && role !== "auditor") {
        readonlyReason.value = readonlyText("role", role, true);
        blocked.value = { message: readonlyBlockedText("role", role), content, code: apiError.code };
      } else {
        readonlyReason.value = accessText(apiError);
        blocked.value = { message: `${accessText(apiError)}未保存的修改可以下载。`, content, code: apiError.code };
      }
      return;
    }
    case "gone":
      enterGone(content, apiError.code);
      return;
    case "assignment": {
      // A07：越过分工被拒——这次保存整份没生效（服务器已存为“被拒保存”），不自动重试；刷新归属后锁定最新范围
      retrying.value = false;
      const total = Number(apiError.details?.total ?? (Array.isArray(apiError.details?.violations) ? apiError.details.violations.length : 0));
      if (apiError.code === "SECTION_NOT_ASSIGNED") {
        const details = apiError.details ?? {};
        rejected.value = { id: typeof details.rejectedSaveId === "string" ? details.rejectedSaveId : null,
          violations: Array.isArray(details.violations) ? details.violations as Violation[] : [], total: total || 1, content };
      }
      const message = apiError.code === "SECTION_NOT_ASSIGNED"
        ? `这次修改没有保存：其中 ${total || 1} 处属于他人负责的部分。`
        : apiError.code === "BLOCK_IDS_REQUIRED" ? "页面版本过旧，请刷新页面后再保存。你的修改已保留在本机。" : `${apiError.message}你的修改已保留在本机。`;
      blocked.value = { message, content, code: apiError.code };
      void refreshOwnership();
      return;
    }
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
      const deletedText = typeof apiError.details?.deletedText === "string" ? apiError.details.deletedText : undefined;
      blocked.value = { message: location ? reason : apiError.message, location, content, code: apiError.code, blockText, deletedText };
    }
  }
}
const assignPanelObjects = ref<BaselineObject[]>([]);
const assignInitialFilter = ref("all");
const assignButton = ref<HTMLButtonElement | null>(null);
function closeAssign() { assignOpen.value = false; void nextTick(() => assignButton.value?.focus()); }
function locateViolation(item: Violation) {
  const root = editor.value;
  const node = nodeOf(item.objectId) ?? (typeof item.clientIndex === "number" ? root?.children[item.clientIndex] as HTMLElement | undefined ?? null : null);
  if (!node) { noteAction("这一处已不在正文中（可能被删除）。", 5000); return; }
  node.scrollIntoView({ behavior: "smooth", block: "center" });
  node.classList.add("a07-hit");
  window.setTimeout(() => node.classList.remove("a07-hit"), 3000);
}
async function rejectedMarkdown(): Promise<string> {
  const record = document.value, info = rejected.value;
  if (record && info?.id) { try { return (await getRejectedSave(record.id, info.id)).markdown; } catch { /* 用本页内容 */ } }
  return markdownContent.value;
}
async function downloadRejected() {
  const text = await rejectedMarkdown();
  const url = URL.createObjectURL(new Blob([text], { type: "text/markdown;charset=utf-8" }));
  const link = window.document.createElement("a");
  link.href = url; link.download = `${document.value?.name || "文档"}-未保存的修改.md`; link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function copyRejected() {
  const text = await rejectedMarkdown();
  await navigator.clipboard?.writeText(text).then(() => noteAction("已复制你的修改。", 4000), () => noteAction("复制失败，请改用下载。", 6000));
}
/** 29 M-3 / 16 §4.6 第 4 条：以服务器最新内容为底，只采用我负责部分的修改；其余放进暂存后保存。 */
/**
 * 27 复验 M-4：按编辑区真实的块 ID 取正文（每个带 data-object-id 的块前插不可见标记，转 Markdown 后再拆出来）。
 * 回车拆段时浏览器会复制属性：同一 ID 只认第一次出现，后面的算新块。有冲突冻结区时不用（返回 null，退回文字配对）。
 */
function editorBlocksWithIds(): { markdown: string; ids: Array<string | null> } | null {
  const root = editor.value;
  if (!root || Object.keys(frozen.value).length) return null;
  const copy = root.cloneNode(true) as HTMLElement;
  copy.querySelectorAll("[data-ai-preview]").forEach(node => node.remove());
  const seen = new Set<string>();
  for (const node of copy.querySelectorAll<HTMLElement>("[data-object-id]")) {
    const id = node.getAttribute("data-object-id") || "";
    if (!id || seen.has(id) || node.querySelector("[data-object-id]")) continue;
    seen.add(id);
    node.prepend(window.document.createTextNode(`\uE000${id}\uE001`));
  }
  const markdown = editorMarkdown(turndown, DOMPurify.sanitize(copy.innerHTML, { RETURN_DOM: true }) as HTMLElement);
  const marker = /\uE000([^\uE001]*)\uE001/;
  const ids: Array<string | null> = [];
  const bodies = splitBlocks(markdown).map(block => {
    const hit = marker.exec(block.body);
    ids.push(hit ? hit[1] : null);
    return hit ? block.body.replace(marker, "") : block.body;
  });
  if (bodies.some(body => body.includes("\uE000"))) return null;
  return { markdown: bodies.join("\n\n") + "\n", ids };
}
async function saveMineOnly() {
  const record = document.value;
  if (!record || savingMine.value) return;
  savingMine.value = true;
  try {
    if (savePromise) await savePromise.catch(() => undefined);
    const data = await getAllObjects(record.id);
    const editableIds = new Set(data.objects.filter(item => item.editable !== false).map(item => item.id));
    // 27 复验 M-4：以开始编辑时那一版为底（ID 与编辑区一致），按那一版的修订号提交，由服务端三方合并判冲突
    const latest = data.objects.map(item => ({ id: item.id, kind: item.kind, markdown: item.markdown }));
    const base: BaselineObject[] = baseSnapshot?.revision === record.revision ? baseSnapshot.objects
      : (() => { const ids = assignBlockIds(savedContent.value, latest); return splitBlocks(savedContent.value).map((block, index) => ({ id: ids[index] ?? `\u0000${index}`, kind: block.kind, markdown: block.body })); })();
    const fromEditor = editorBlocksWithIds();
    const kept = keepMineOnly(fromEditor?.markdown ?? markdownContent.value, base, editableIds, fromEditor?.ids);
    const blockIds = kept.blockIds.map(id => id && !id.startsWith("\u0000") ? id : null);
    if (kept.stashed.length) addStash(kept.stashed.join("\n\n"), null, true);
    pendingKey = null; blocked.value = null; rejected.value = null; assignNotice.value = null; retrying.value = false;
    if (rejectedOpen.value) rejectedOpen.value = false;
    const stashNote = kept.stashed.length ? `不归你负责的 ${kept.stashed.length} 段已放入暂存，可以复制。` : "";
    let result: Awaited<ReturnType<typeof updateDocumentContent>>;
    try {
      result = await updateDocumentContent(record.id, record.revision, kept.markdown, undefined, undefined,
        { clientSaveKey: crypto.randomUUID(), allowMerge: true, blockIds });
    } catch (caught) {
      // 我和别人都改了我负责的同一段：服务端 409 OBJECT_CONFLICT，走既有冲突流程
      if (document.value === record) handleSaveError(caught, kept.markdown, "manual", markdownContent.value);
      if (caught instanceof ApiError && caught.code === "OBJECT_CONFLICT") noteAction(`你负责的部分在你编辑期间也被他人修改过，已按冲突处理，请在冲突面板选择保留哪一版。${stashNote}`, 12000);
      else noteAction(`没能保存：${messageOf(caught)}`, 8000);
      return;
    }
    if (document.value !== record) return;
    await acceptContent({ markdownContent: result.markdownContent ?? kept.markdown, revision: result.revision, status: result.status }, { reason: "正在载入保存后的内容…" });
    savedAt.value = Date.now();
    // 被拒的那次提交可能已在服务器上记成冲突（“我的版本”存为冲突副本）——提示冲突，而不是普通的“已保存”
    await refreshConflicts().catch(() => undefined);
    const open = Math.max(mineConflicts.value, frozenOpen.value.filter(entry => !entry.resolved).length);
    if (open) noteAction(`你负责的部分有 ${open} 处在你编辑期间也被他人修改过，已按冲突处理，请在冲突面板选择保留哪一版。${stashNote}`, 12000);
    else noteAction(kept.stashed.length ? `已保存你负责部分的修改；其余 ${kept.stashed.length} 段已放入暂存，可以复制。` : "已保存你负责部分的修改。", 8000);
  } catch (caught) {
    noteAction(`没能保存：${messageOf(caught)}`, 8000);
  } finally { savingMine.value = false; }
}
let rejectedSyncTimer: number | undefined;
watch(markdownContent, content => {
  const record = document.value, info = rejected.value;
  if (!record || !info?.id || content === info.content) return;
  window.clearTimeout(rejectedSyncTimer);
  rejectedSyncTimer = window.setTimeout(() => {
    if (rejected.value !== info) return;
    void putRejectedSave(record.id, info.id!, content, blockIdsFor(content)).then(() => { info.content = content; }).catch(() => undefined);
  }, 3000);
});
watch(assignRestricted, restricted => { if (restricted && aiScope.value === "document") aiScope.value = "section"; });
watch(assignOpen, open => { if (open) void refreshOwnership().then(() => { assignPanelObjects.value = [...baselineObjects]; }); });
/** A07：被拒保存后放弃本地修改，取服务器最新正文（先下载过的内容由用户自己保管）。 */
async function reloadAfterRejected() {
  if (!window.confirm("放弃没有保存的修改，显示服务器上的最新内容？建议先点“下载我的修改”。")) return;
  blocked.value = null;
  rejected.value = null;
  if (rejectedOpen.value) rejectedOpen.value = false;
  pendingKey = null;
  markdownContent.value = savedContent.value;
  await refreshFromServer({}, true).catch(() => undefined);
  void refreshOwnership();
}
/** 界面验收 M1：把卡住保存的那一段换回上次保存的原文，其余修改照常保存。 */
async function revertBlockedBlock() {
  const record = document.value, failed = blocked.value;
  if (!record || !failed?.blockText || revertingBlock.value) return;
  const reverted = revertBlock(savedContent.value, markdownContent.value, failed.blockText, failed.deletedText);
  if (reverted === null) {
    noteAction("没能自动找到这一段，请手动改回这一段，或先下载你的修改。", 8000);
    return;
  }
  revertingBlock.value = true;
  // 界面复验 N2：撤销期间锁住编辑区（编辑区还是撤销前的样子，任何输入都会把这一段再删掉），
  // 保存成功后总是按服务器正文重绘，不依赖差异判断。
  beginEditorLock("正在恢复这一段…");
  const previous = markdownContent.value;
  let rendered = false;
  try {
    if (savePromise) await savePromise.catch(() => undefined);
    markdownContent.value = reverted;
    blocked.value = null;
    failures.value = 0;
    failingSince = null;
    await saveContent(reverted, "manual");
    if (document.value?.id !== record.id) return;
    if (!blocked.value && markdownContent.value === savedContent.value) {
      const scroller = editor.value?.closest(".desk");
      const top = scroller?.scrollTop ?? 0;
      // acceptContent 接管锁（沿用撤销开始时的焦点和光标），重绘完成后由它解锁
      rendered = true;
      await acceptContent({ markdownContent: savedContent.value, revision: record.revision, status: record.status }, { reason: "正在恢复这一段…" });
      if (scroller) scroller.scrollTop = top;
      noteAction("已撤销这一段的修改，其余修改已保存。");
    } else if (markdownContent.value === reverted) {
      // 没保存成功：正文回到与编辑区一致的内容（编辑区没重绘，不能让两者不一致），可以再点一次撤销
      markdownContent.value = previous;
      if (!blocked.value) blocked.value = failed;
      noteAction("撤销没有保存成功，请稍后再点一次“撤销这一段的修改”。", 8000);
    }
  } finally {
    revertingBlock.value = false;
    if (!rendered) endEditorLock();
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
  const explicit = explicitBase;
  if (new Blob([content + (explicit ?? "")]).size > 60000) return; // keepalive 正文上限 64KB；超出时只留本机草稿。
  const base = explicit !== null ? record.revision : mergeBase ?? record.revision;
  if (!pendingKey || pendingKey.content !== content) {
    pendingKey = { content, key: crypto.randomUUID(), base, dsend: explicit !== null ? content : domPair?.dom ?? content, gen: fallbackGen,
      ...(explicit !== null ? { baseMarkdown: explicit } : {}) };
  }
  persistLocalDraft();
  void updateDocumentContent(record.id, pendingKey.base, content, undefined, undefined,
    { clientSaveKey: pendingKey.key, allowMerge: true, keepalive: true, blockIds: blockIdsFor(content), ...(pendingKey.baseMarkdown !== undefined ? { baseMarkdown: pendingKey.baseMarkdown } : {}) }).catch(() => undefined);
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
  const lockedNode = assignLockedNode("insertFromPaste");
  if (lockedNode) {
    event.preventDefault();
    const pasted = event.clipboardData.getData("text/plain");
    if (pasted) addStash(pasted, lockedNode);
    showLockedHint(lockedNode);
    return;
  }
  const html = event.clipboardData.getData("text/html");
  // S7：对象 ID / 选区 ID 不随粘贴复制（否则产生重复 ID，01 §6.2）
  if (html) window.document.execCommand("insertHTML", false, DOMPurify.sanitize(html, { FORBID_ATTR: ["style", "class", "id", "data-object-id", "data-object-revision", "data-selection-id", "contenteditable", "data-word-protected", "data-a07-lock", "data-editable", "data-assignee"] }));
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
  // 有未保存修改也能查看历史，只是不能恢复（02 P1）。界面复验 N9：其他面板打开时可以直接切换过来。
  if (opBusy.value) return;
  openPanel("history");
}
/** 文档栏的面板按钮：再点一次收起；有操作进行时不切换。 */
function togglePanel(name: "ai" | "history" | "assignments" | "references") {
  if (rightPanel.value === name) {
    if (name === "assignments") { assignmentsPanel.value?.requestClose(); return; }
    if (name === "references") { referencesPanel.value?.requestClose(); return; }
    rightPanel.value = null; return;
  }
  if (opBusy.value) return;
  openPanel(name);
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
/** 处理冲突前先保存正文；仍有未保存修改则不处理（避免处理结果覆盖编辑区）。方案 C：先把冻结区的最新版本存成快照（处理用最新快照）。 */
async function beforeResolve(): Promise<boolean> {
  window.clearTimeout(autoSaveTimer);
  if (isDirty.value) await save("auto");
  if (savePromise) await savePromise;
  if (editor.value && frozenOpen.value.length) editorText();
  await flushSnapshots();
  await flushCaretSink();
  // 让冲突面板拿到刚存上的快照 id（审查点 3：处理请求带上它）
  await nextTick();
  // 测试工程师 N3：副本已满的冻结区不算“没保存”（存不进去，处理时随请求带上）
  return !isDirty.value && !frozenPending.value && !sinkUnsaved.value && !sinkBacklog.value.length;
}
/** 卡片上“我的版本”的实时内容（冻结区里的文字）与快照状态。 */
const liveMine = computed(() => Object.fromEntries(frozenOpen.value.map(entry => [entry.key, {
  markdown: entry.adopted && entry.adopted.from === entry.mine ? entry.adopted.markdown : entry.mine, saved: entry.mine === entry.saved, savedAt: entry.savedAt, failing: entry.failing, original: entry.original, snapshotId: entry.snapshotId,
  otherTab: Boolean(entry.savedTab && entry.savedTab !== browserTab && entry.mine === entry.saved), limited: Boolean(entry.limited),
}]).concat(Object.entries(limitedMine.value).filter(([key]) => !frozen.value[key] || frozen.value[key].resolved).map(([key, edit]) => [key, {
  markdown: edit.markdown, saved: false, savedAt: null, failing: false, original: "", snapshotId: null, otherTab: false, limited: true, detached: true,
}]))));
/** 测试工程师 N9 / N11：这个标签页里还有没存到服务器的冲突内容（正在存 / 副本已满存不进）时，面板顶部不说“不会丢失” */
const conflictLocalPending = computed<"" | "saving" | "limited">(() => {
  const values = Object.values(liveMine.value) as { saved: boolean; limited?: boolean }[];
  if (values.some(value => value.limited)) return "limited";
  return values.some(value => !value.saved) ? "saving" : "";
});
/** 审查员 M1：冲突面板发现服务器上有更新的一份“我的版本”（另一个标签页 / 本标签正文保存时追加）→ 以它为准，不再带旧 snapshotId。冻结区有没存上的字时不接（那些字会先存成更新的一份）。 */
function adoptLatest(key: string, latest: MineLatest) {
  const entry = frozen.value[key];
  if (!entry || entry.resolved || entry.mine !== entry.saved || !latest.snapshotId) return;
  entry.snapshotId = latest.snapshotId;
  entry.savedTab = latest.tabId;
  entry.savedAt = latest.savedAt;
  entry.adopted = latest.markdown === entry.mine ? null : { from: entry.mine, markdown: latest.markdown };
}
async function acceptResolution(result: ResolveResult) {
  const record = document.value;
  if (!record) return;
  if (sinkNote.value && (!result.conflictId || sinkNote.value.conflictId === result.conflictId) && result.conflictStatus !== "open") sinkNote.value = null;
  if (result.conflictId && result.items.some(entry => limitedMine.value[`${result.conflictId}:${entry.itemIndex}`])) {
    const rest = { ...limitedMine.value };
    for (const entry of result.items) if (entry.status !== "open") delete rest[`${result.conflictId}:${entry.itemIndex}`];
    limitedMine.value = rest;
    persistLocalDraft();
  }
  conflictSummary.value = { ...(conflictSummary.value || { allOpen: null }), mineOpen: result.mineOpen, mineHandled: (conflictSummary.value?.mineHandled || 0) + result.items.length };
  // 复审 M3：原位置附近已找不到时放到了章节末尾，提醒用户检查
  if (result.items.some(entry => entry.placementFallback)) noteAction("恢复的段落原来的位置已变化，已放在所属章节末尾，请检查。", 10000);
  // K1-M1（L6 第 4 条）：说清楚是替换了正文里的这一段，原来的在版本记录里
  else if (result.action === "keep_mine") noteAction("已用你的版本（含续写）替换正文里的这一段；替换前的内容可在版本记录里查看。", 6000);
  else if (result.action === "keep_both") noteAction(`已在你的版本前插入 ${result.theirsName || "对方"} 的版本，两段都在正文中。`, 4000);
  // 设计师建议 7：其余处理方式也给成功短提示
  else if (result.action === "use_theirs") noteAction(`已使用 ${result.theirsName || "对方"} 的版本。你的版本可在「已处理」里查看和下载。`, 4000);
  else if (result.action === "manual") noteAction("已用合并结果更新这一段。", 4000);
  else if (result.action === "restore_as_new") noteAction("已把你的版本恢复为新段落。", 4000);
  else if (result.action === "discard") noteAction("已放弃你的修改。你的版本可在「已处理」里查看和下载。", 4000);
  // 方案 C：处理的是冻结区——保留我的：编辑区不变，撤掉红框；其他动作：冻结区换成处理结果（重绘）
  const key = result.conflictId ? `${result.conflictId}:${result.items[0]?.itemIndex}` : "";
  const entry = key ? frozen.value[key] : undefined;
  if (entry) {
    entry.resolved = true;
    if (result.action === "keep_mine" && editor.value && markdownContent.value === record.markdownContent) {
      const root = editor.value;
      const blocks = frozenEls(root, key);
      blocks.forEach(block => { block.removeAttribute("data-frozen"); block.classList.remove("blk-conflict"); });
      delete frozen.value[key];
      snapshotKeys.delete(key);
      if (editorText() === result.markdownContent) {
        record.revision = result.documentRevision;
        record.markdownContent = result.markdownContent;
        savedContent.value = result.markdownContent;
        markdownContent.value = result.markdownContent;
        mergeBase = null;
        domPair = null;
        explicitBase = null;
        persistLocalDraft();
        void refreshConflicts();
        return;
      }
      // 编辑区与处理结果不一致（期间别人改过其他段）：放回冻结标记，走下面的重绘
      blocks.forEach(block => block.setAttribute("data-frozen", key));
      frozen.value[key] = entry;
    }
  }
  if (!entry && result.documentRevision === record.revision && result.markdownContent === record.markdownContent) return;
  // 终验回归 I-4：大文档处理冲突后重绘较慢，期间不锁编辑区；用户继续打的字原位写回或保留在编辑区，下次保存按原基准再合并
  const previous = record.revision;
  const typedNewer = markdownContent.value !== record.markdownContent;
  record.revision = result.documentRevision;
  record.markdownContent = result.markdownContent;
  savedContent.value = result.markdownContent;
  const content = { markdownContent: result.markdownContent, revision: result.documentRevision, status: record.status } as DocumentContentResult;
  if (typedNewer && entry) {
    // 处理期间又在别处输入了：先保存，再按处理结果重绘（冻结区换成处理结果）
    mergeBase = previous;
    persistLocalDraft();
    await save("auto");
    if (savePromise) await savePromise.catch(() => undefined);
    if (document.value === record && !isDirty.value) await acceptContent({ markdownContent: savedContent.value, revision: record.revision, status: record.status }, { reason: "正在载入冲突处理结果…" });
  } else if (typedNewer) {
    mergeBase = previous;
    persistLocalDraft();
    scheduleAutoSave();
  } else {
    mergeBase = null;
    markdownContent.value = result.markdownContent;
    persistLocalDraft();
    const reason = "正在载入冲突处理结果…";
    const started = Date.now();
    const shown = await acceptContent(content, { reason, yieldToTyping: true, stateApplied: true, carryTyping: true });
    if (!shown && document.value === record) {
      if (lastInputAt >= started && entry) {
        // 冻结区已处理、编辑区还没换成处理结果：先保存期间的输入，再锁定重绘（冻结区里后打的字不会被静默丢掉）
        mergeBase = previous;
        await save("auto");
        if (savePromise) await savePromise.catch(() => undefined);
        if (document.value === record && !isDirty.value) await acceptContent({ markdownContent: savedContent.value, revision: record.revision, status: record.status }, { reason });
      } else if (lastInputAt >= started) { mergeBase = previous; scheduleAutoSave(); }
      else await acceptContent(content, { reason });
    }
  }
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
  if (referencesDirty.value || referencesBusy.value) {
    referencesPanel.value?.requestClose();
    noteAction("参考资料尚未保存或正在处理，请完成后再离开。");
    return false;
  }
  if (blockGuidanceDirty.value || assignmentsPanel.value?.isDirty || assignmentsBusy.value || blockGuidanceBusy.value) {
    noteAction("分工或段落建议尚未保存，请先保存或明确放弃修改。");
    return false;
  }
  if (guidanceChanged.value || guidanceSaving.value) {
    openPanel("ai");
    noteAction("编写建议尚未保存，请保存或放弃修改后再离开。");
    return false;
  }
  if (to && sessionEnded(to)) {
    // 会话失效或需要改密（G1）：保存已不可能。草稿写入并读回校验通过就放行跳转，重新登录后由恢复条提示；
    // 校验失败只给“下载后离开 / 留在本页”。
    window.clearTimeout(autoSaveTimer);
    window.clearTimeout(retryTimer);
    if (!hasUnsaved.value || persistAndVerify()) return true;
    leaveDialog.value = { mode: "download-only", count: pendingCount.value, target: to.fullPath };
    return false;
  }
  if (skillOpen.value || skillBusy.value || historyBusy.value || aiBusy.value || aiInserting.value || addingHeading.value || diagramBusy.value || refreshingEditor.value || undoingAi.value) {
    noteAction("操作仍在进行，请完成或停止后再离开文档。");
    return false;
  }
  if (aiDraft.value && !window.confirm("尚未采纳的 AI 草稿会丢失，是否离开？")) return false;
  window.clearTimeout(autoSaveTimer);
  if (hasUnsaved.value && !readonly.value) {
    // 最多等 5 秒把修改送到服务器（01 §6.3 第3条）；方案 C：冻结区续写同时存成快照
    await Promise.race([Promise.all([save("manual"), flushSnapshots(), flushCaretSink()]), new Promise(resolve => window.setTimeout(resolve, 5000))]);
  }
  if (!hasUnsaved.value) {
    const held = frozenOpen.value.length;
    if (held) flash(`${held} 处冲突已保存在服务器（含你的续写），可稍后在本文档的“冲突”中处理。`, 6000);
    return true;
  }
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
  if (referencesDirty.value || referencesBusy.value || blockGuidanceDirty.value || assignmentsPanel.value?.isDirty || assignmentsBusy.value || blockGuidanceBusy.value || guidanceChanged.value || guidanceSaving.value || skillBusy.value || hasUnsaved.value || saving.value || aiDraft.value || aiBusy.value || diagramBusy.value) {
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
  const scroll = root.closest(".desk");
  const scrollTop = scroll?.scrollTop || 0;
  beginEditorLock("正在刷新排版…");
  window.clearTimeout(autoSaveTimer);
  try {
    if (saveFirst) await save();
    if (isDirty.value || document.value !== record) return;
    const preview = await previewDocument(record.id);
    if (document.value !== record || documentId.value !== record.id) return;
    showEditorHtml(preview.html, preview.objectModel);
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
    if (editor.value === root) endEditorLock();
    else { refreshingEditor.value = false; refreshReason.value = ""; }
  }
  captureEditorTarget();
}
async function format(command: string, value?: string) {
  if (aiLocked.value || addingHeading.value) return;
  // 段落样式下拉会抢走焦点：编辑区里没有选区时，回到最近一次的光标位置再执行
  const selection = window.getSelection();
  const inside = Boolean(selection?.rangeCount && editor.value?.contains(selection.getRangeAt(0).startContainer));
  editor.value?.focus();
  if (!inside && lastCaret && editor.value?.contains(lastCaret.startContainer)) { selection?.removeAllRanges(); selection?.addRange(lastCaret); }
  window.document.execCommand(command, false, value);
  onEditorInput();
  await refreshEditorFormatting();
}
function scrollToHeading(index: number) {
  editor.value?.querySelectorAll("h1,h2,h3,h4,h5,h6")[index]?.scrollIntoView({ behavior: "smooth", block: "center" });
}
async function handleExport() {
  exportMenu.value = false;
  if (!document.value || exporting.value || opBusy.value) return;
  // 方案 C：冻结区里的修改不在正文中
  if (frozenOpen.value.length) noteAction(`有 ${frozenOpen.value.length} 处冲突未处理，导出的是正文里的版本（不含你在冲突段里的修改）。`, 8000);
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
  if (opBusy.value) return;
  void router.push({ name: "project-documents", params: { id: projectId.value } });
}
function onKeydown(event: KeyboardEvent) {
  if (event.ctrlKey && event.key.toLowerCase() === "s") {
    event.preventDefault();
    void save("manual");
    // 测试工程师 N3：副本已满、存不进去的冲突段——Ctrl+S 不再“无反应”，说明去处并打开冲突面板
    if (frozenOpen.value.some(entry => entry.limited && entry.mine !== entry.saved) || Object.keys(limitedMine.value).length) {
      noteAction("冲突段里新写的内容存不进冲突副本（副本已满），正文其余部分照常保存。请在冲突面板处理这条冲突，处理时会直接用这一版。", 8000);
      openConflicts("mine");
    }
  }
}
watch(documentId, () => void load());
onBeforeRouteLeave(leaveDocument);
onBeforeRouteUpdate(leaveDocument);
onMounted(() => {
  window.addEventListener("beforeunload", beforeUnload);
  window.addEventListener("online", onlineAgain);
  window.addEventListener("resize", placeCaretSink);
  window.addEventListener("pagehide", onPageHide);
  window.addEventListener("pageshow", onPageShow);
  window.document.addEventListener("visibilitychange", onVisibilityChange);
  window.addEventListener("online", onOnline);
  window.addEventListener("offline", onOffline);
  clockTimer = window.setInterval(() => { now.value = Date.now(); }, 15000);
  updateTimer = window.setInterval(() => void checkOthersUpdated(), 45000);
  window.addEventListener("resize", scheduleMarks);
  window.addEventListener("keydown", bufferLockedTyping, true);
  window.addEventListener("keydown", guardLockedKey, true);
  window.addEventListener("compositionstart", guardLockedComposition, true);
  window.addEventListener("scroll", hideLockedBubble, true);
  window.addEventListener("compositionstart", noteLockedComposition, true);
  window.addEventListener("pointerdown", noteLoadPointer, true);
  window.addEventListener("mousedown", openLoadSink, true);
  window.addEventListener("focusin", onEditorFocusIn);
  void load();
});
onUnmounted(() => {
  guidanceController?.abort();
  stopTabWatch();
  loadVersion += 1;
  window.removeEventListener("beforeunload", beforeUnload);
  window.removeEventListener("online", onlineAgain);
  window.removeEventListener("resize", placeCaretSink);
  window.clearTimeout(caretSinkRetryTimer);
  window.removeEventListener("pagehide", onPageHide);
  window.removeEventListener("pageshow", onPageShow);
  window.document.removeEventListener("visibilitychange", onVisibilityChange);
  window.removeEventListener("online", onOnline);
  window.removeEventListener("offline", onOffline);
  window.clearInterval(clockTimer);
  window.clearInterval(updateTimer);
  window.removeEventListener("resize", scheduleMarks);
  window.removeEventListener("keydown", bufferLockedTyping, true);
  window.removeEventListener("keydown", guardLockedKey, true);
  window.removeEventListener("compositionstart", guardLockedComposition, true);
  window.removeEventListener("scroll", hideLockedBubble, true);
  window.removeEventListener("compositionstart", noteLockedComposition, true);
  window.removeEventListener("pointerdown", noteLoadPointer, true);
  window.removeEventListener("mousedown", openLoadSink, true);
  dropLoadSink();
  dropCaretSink();
  window.removeEventListener("focusin", onEditorFocusIn);
  window.clearTimeout(caretLostTimer);
  window.document.removeEventListener("pointerdown", onExportMenuOutside, true);
  window.cancelAnimationFrame(marksFrame);
  window.clearTimeout(retryTimer);
  window.clearTimeout(savingTimer);
  window.clearTimeout(actionNoteTimer);
  window.clearTimeout(lastEditorTimer);
  deskObserver?.disconnect();
  clearAiDraft();
  window.clearTimeout(autoSaveTimer);
  stopOriginal();
  closePresence();
});
</script>

<template>
  <div class="ws" @keydown="onKeydown">
    <div v-if="phase === 'loading'" class="state" role="status">正在加载文档...</div>
    <div v-else-if="phase === 'error'" class="state" data-testid="load-error"><p>{{ errorText }}</p>
      <p v-if="loadErrorStatus === 404 && (orphanDrafts.length || orphanStash)" class="muted" data-testid="orphan-draft">这台电脑上还留有你在这篇文档里未保存的内容，可以下载保留。
        <button type="button" class="ghost sm" data-testid="orphan-draft-download" @click="downloadOrphan">下载未保存的内容</button></p>
      <router-link v-if="loadErrorStatus === 404" class="ghost" :to="{ name: 'projects' }">返回项目列表</router-link>
      <button v-else type="button" class="ghost" @click="load">重试</button></div>
    <template v-else-if="document">
      <!-- 12 §8.1 第 3 步：文档栏（返回、标题、模板标签、胶囊紧跟标题；右侧视图切换、冲突、版本记录、写作助手、下载） -->
      <header class="docbar">
        <button type="button" class="subtle icon-btn" aria-label="返回文档列表" title="返回文档列表" :disabled="opBusy" @click="goBack"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M15 6l-6 6 6 6" /></svg></button>
        <div class="doctitle">
          <h1 :title="document.name">{{ document.name }}</h1>
          <span v-if="templateTag" class="tag plain" data-testid="doc-template-tag">{{ templateTag }}<template v-if="templateVersion && !templateVersion.legacy"> · {{ templateVersion.label }}</template></span>
          <span v-else-if="templateVersion && !templateVersion.legacy" class="tag plain" data-testid="doc-template-tag">模板{{ templateVersion.label }}</span>
          <span v-if="templateVersion?.legacy" class="tag plain" data-testid="doc-template-legacy" title="本文档创建于模板版本管理之前，使用当时的模板结构，不受之后的模板版本影响。">版本化之前</span>
          <span v-if="templateVersion?.newer" class="tag info" data-testid="doc-template-newer" :title="templateVersion.newer">模板有新版本</span>
          <SaveStatus :state="capsule" :label="capsuleText" :detail="capsuleDetail" @save="save('manual')" @retry="retryNow" @details="locateBlocked" />
          <span v-if="wholeSave" class="whole-tag" data-testid="whole-save" tabindex="0" aria-describedby="whole-save-tip">整篇保存<span id="whole-save-tip" class="tip" role="tooltip">这份文档暂时只能整篇保存。和他人同时编辑时，冲突按整段对比，不影响保存。</span></span>
        </div>
        <div class="spacer"></div>
        <div class="seg" role="group" aria-label="文档视图">
          <button type="button" :class="{ on: mode === 'edit' }" :aria-pressed="mode === 'edit'" :disabled="opBusy" @click="switchMode('edit')">编辑稿</button>
          <button type="button" :class="{ on: mode === 'original' }" :aria-pressed="mode === 'original'" :disabled="opBusy" @click="switchMode('original')">查看原稿</button>
          <button type="button" :class="{ on: mode === 'layout' }" :aria-pressed="mode === 'layout'" :disabled="opBusy || saving" @click="switchMode('layout')">版式预览</button>
        </div>
        <span class="divider-v" aria-hidden="true"></span>
        <button v-if="mode === 'edit' && conflictEntry" type="button" class="conflict-toggle" :class="[conflictEntry.tone, { on: conflictOpen }]" data-testid="conflict-toggle" :aria-expanded="conflictOpen" @click="conflictOpen ? (conflictOpen = false) : openConflicts(conflictEntry.tab)"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5l9.5 16.5h-19z" /><path d="M12 10v4.5M12 17.5v.01" /></svg><span>{{ conflictEntry.name }}</span> <span v-if="conflictEntry.count" class="count">{{ conflictEntry.count }}</span></button>
        <button v-if="assignMode && canAssignDoc" type="button" class="tag mode-chip" :class="assignMode === 'enforced' ? 'info' : 'plain'" data-testid="doc-assign-mode-chip" title="打开编写分工" @click="assignOpen = true">{{ assignMode === 'enforced' ? '按分工编辑' : '开放编辑' }}</button>
        <span v-else-if="assignMode" class="tag" :class="assignMode === 'enforced' ? 'info' : 'plain'" data-testid="doc-assign-mode-chip" :title="assignMode === 'enforced' ? (assignRestricted ? '你只能修改分配给你的部分，其他内容只读。' : '编写人员只能修改分配给自己的部分。') : '编写人员可以修改全部内容，分工只作为参考。'">{{ assignMode === 'enforced' ? '按分工编辑' : '开放编辑' }}</span>
        <button v-if="mode === 'edit'" ref="assignButton" type="button" class="subtle" :class="{ on: assignOpen }" :aria-pressed="assignOpen" data-testid="assign-open" @click="assignOpen ? closeAssign() : (assignOpen = true)">编写分工</button>
        <button v-if="mode === 'edit'" type="button" class="subtle" :class="{ on: historyOpen }" :aria-pressed="historyOpen" :disabled="opBusy && !historyOpen" @click="togglePanel('history')"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M3.5 12a8.5 8.5 0 1 0 2.5-6" /><path d="M3 4v4h4" /><path d="M12 8v4.5l3 2" /></svg>版本记录</button>
        <button v-if="mode === 'edit'" type="button" class="subtle" :class="{ on: assignmentsOpen }" :aria-pressed="assignmentsOpen" :disabled="opBusy || isDirty || guidanceChanged || Boolean(aiDraft)" :title="isDirty ? '请先保存正文' : ''" @click="togglePanel('assignments')">编写指导</button>
        <button v-if="mode === 'edit'" type="button" class="subtle" :class="{ on: referencesOpen }" :aria-pressed="referencesOpen" :disabled="opBusy || isDirty || guidanceChanged || Boolean(aiDraft)" :title="isDirty ? '请先保存正文' : ''" @click="togglePanel('references')">参考资料<span v-if="referenceCount" class="count">{{ referenceCount }}</span></button>
        <button v-if="mode === 'edit' && aiUndo" type="button" class="subtle" :disabled="!canUndoAi" @click="undoAiChange">{{ undoingAi ? '撤回中…' : '撤回 AI 修改' }}</button>
        <button v-if="mode === 'edit'" type="button" class="subtle" :class="{ on: aiOpen }" :aria-pressed="aiOpen" :disabled="readonly || Boolean(aiUnavailable('writing')) || (opBusy && !aiOpen)" :title="aiUnavailable('writing') || undefined" @mousedown.prevent @click="togglePanel('ai')"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" /><path d="M19 16l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z" /></svg>写作助手</button>
        <a v-if="mode === 'original' && document.originalDocxFileId" class="primary" :href="`/api/v1/files/${document.originalDocxFileId}/content`" download><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v11M7 10.5l5 5 5-5" /><path d="M5 20h14" /></svg>下载原稿</a>
        <div v-else class="export-group">
          <button type="button" class="primary" data-testid="export-word" :disabled="exporting || saving || opBusy || document.permissions?.canExport === false" :title="document.permissions?.canExport === false ? '你在该项目中没有导出权限' : undefined" @click="handleExport"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v11M7 10.5l5 5 5-5" /><path d="M5 20h14" /></svg>{{ exporting ? "导出中..." : "下载编辑稿" }}</button>
          <button v-if="isDirty" ref="exportMoreButton" type="button" class="primary export-more" aria-haspopup="menu" :aria-expanded="exportMenu" aria-label="更多下载方式" :disabled="exporting" @click="exportMenu = !exportMenu" @keydown="onExportMenuKeydown"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6" /></svg></button>
          <div v-if="exportMenu" ref="exportMenuEl" class="export-menu" role="menu" aria-label="下载方式" @keydown="onExportMenuKeydown">
            <button type="button" role="menuitem" @click="exportServerVersion">服务器版本（Word）</button>
            <button type="button" role="menuitem" @click="exportMenu = false; downloadMyChanges()">我的修改（.md）</button>
          </div>
          <!-- 界面验收 S9：导出失败只在导出按钮旁说明（危险色），不占通栏 -->
          <div v-if="exportError" class="export-error" role="alert" data-testid="export-error">
            <p>{{ exportError }}</p>
            <!-- 终验 S-8：只有能编辑且有未保存修改时才提供“下载我的修改” -->
            <span class="acts"><button v-if="isDirty && !readonly && document.permissions?.canEdit !== false" type="button" class="text" @click="downloadMyChanges">下载我的修改</button><button type="button" class="text neutral" @click="exportError = ''">知道了</button></span>
          </div>
        </div>
      </header>

      <!-- 第 2 步：提示条统一放在文档栏下方、三栏上方 -->
      <div class="notices">
        <p v-if="errorText" class="wb-notice danger" role="alert">{{ errorText }}</p>
        <p v-if="refreshNoticeShown" class="wb-notice neutral" role="status" data-testid="editor-refreshing"><span class="spin" aria-hidden="true"></span><span>{{ refreshNoticeShown }}</span></p>
        <div v-if="lockedBubble" class="locked-bubble" role="status" data-testid="locked-hint" :style="{ top: `${lockedBubble.top}px`, left: `${lockedBubble.left}px` }">
          <p>{{ lockedBubble.text }}</p>
          <p v-if="lockedStash" class="stash" data-testid="locked-stash"><span>{{ stashText(stashLatest) }}</span>
            <button type="button" class="ghost sm" @mousedown.prevent @click="copyStash">复制</button>
            <button type="button" class="ghost sm" data-testid="locked-stash-place" :disabled="readonly || !mineStarts.length" @mousedown.prevent @click="placeStash">放到我的下一处</button></p>
        </div>
        <p v-if="lockedStash && !lockedBubble" class="wb-notice neutral" role="status" data-testid="locked-stash-bar"><span>{{ stashBarText }}</span>
          <span class="acts"><button type="button" class="ghost sm" @click="copyStash">复制</button><button type="button" class="ghost sm" :disabled="readonly || !mineStarts.length" @mousedown.prevent @click="placeStash">放到我的下一处</button><button type="button" class="ghost sm" @click="clearStash">清除</button></span></p>
        <p v-if="assignNotice" class="wb-notice" :class="assignNotice.tone" role="status" data-testid="assign-notice"><span>{{ assignNotice.text }}</span>
          <span class="acts"><button v-if="assignNotice.locate" type="button" class="ghost sm" @click="locateMine()">定位到第一处</button><button v-if="assignNotice.saveMine" type="button" class="ghost sm" :disabled="savingMine" @click="saveMineOnly">只保存我负责的部分</button><button type="button" class="ghost sm" @click="assignNotice = null">知道了</button></span></p>
        <p v-if="noAssignNotice" class="wb-notice info" role="status" data-testid="no-assign-notice"><span>{{ noAssignNotice }}</span></p>
        <p v-if="mineIntro && !assignNotice" class="wb-notice info" role="status" data-testid="mine-intro"><span>{{ mineIntro }}</span>
          <span class="acts"><button type="button" class="ghost sm" data-testid="mine-locate" @click="locateMine()">定位到第一处</button><button type="button" class="ghost sm" @click="dismissMineIntro">知道了</button></span></p>
        <div v-if="blocked && blocked.code === 'SECTION_NOT_ASSIGNED'" class="wb-notice warning" role="alert" data-testid="save-rejected-banner">
          <span>{{ blocked.message }}</span>
          <span class="acts">
            <button type="button" class="ghost sm" data-testid="save-rejected-open" @click="rejectedOpen = true">查看并处理</button>
            <button type="button" class="ghost sm" data-testid="save-rejected-reload" @click="reloadAfterRejected">放弃修改并刷新</button>
          </span>
        </div>
        <div v-else-if="blocked" class="wb-notice danger save-blocked" role="alert" data-testid="save-blocked" :data-code="blocked.code">
          <svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5l9.5 16.5h-19z" /><path d="M12 10v4.5M12 17.5v.01" /></svg>
          <span v-if="blocked.fromPoll">{{ blocked.message }}</span>
          <span v-else><strong>{{ readonly ? '无法保存' : '保存失败' }}</strong>{{ blocked.location ? `：${locationText(blocked.location)}。` : '：' }}{{ blocked.message }}</span>
          <span class="acts">
            <button v-if="blocked.location" type="button" class="ghost sm" @click="locateBlocked">定位</button>
            <button v-if="blocked.blockText && !readonly" type="button" class="ghost sm" data-testid="revert-block" :disabled="revertingBlock" @click="revertBlockedBlock">{{ revertingBlock ? '正在撤销…' : '撤销这一段的修改' }}</button>
            <button v-if="!readonly && blocked.code !== 'SECTION_NOT_ASSIGNED'" type="button" class="ghost sm" @click="retryNow">重试保存</button>
            <button v-if="blocked.code === 'SECTION_NOT_ASSIGNED' || blocked.code === 'BLOCK_IDS_REQUIRED'" type="button" class="ghost sm" data-testid="save-rejected-reload" @click="reloadAfterRejected">放弃修改并刷新</button>
            <button v-if="blocked.unsaved !== false" type="button" class="subtle sm" @click="downloadMyChanges">下载我的修改</button>
            <router-link v-if="blocked.gone" class="ghost sm" data-testid="gone-back" :to="{ name: 'projects' }">返回项目列表</router-link>
          </span>
        </div>
        <div v-else-if="storageFailed" class="wb-notice danger save-blocked" role="alert">
          <span>浏览器存储已满，修改未能保存在本机。服务器保存仍在继续，建议先下载你的修改。<template v-if="storageUsage.length">本机共有 {{ storageUsage.length }} 份文档草稿，{{ draftSizeText(storageUsage.reduce((sum, item) => sum + item.bytes, 0)) }}。</template></span>
          <span class="acts">
            <button type="button" class="ghost sm" @click="downloadMyChanges">下载我的修改</button>
            <button v-if="otherDocDrafts.length" type="button" class="ghost sm" data-testid="clear-other-drafts" @click="exportAndClearOtherDrafts">下载并清理其他文档的草稿（{{ otherDocDrafts.length }} 份）</button>
          </span>
        </div>
        <div v-if="autoConflict && !conflictOpen" class="wb-notice warning" role="status" data-testid="auto-conflict">
          <svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5l9.5 16.5h-19z" /><path d="M12 10v4.5M12 17.5v.01" /></svg>
          <span v-if="autoConflict.frozen">这一段和 {{ autoConflict.name }} 的修改冲突。你可以继续写，写的内容先存为冲突副本，不会丢失；处理后才写入正文。</span>
          <span v-else-if="autoConflict.whole" data-testid="whole-conflict-note">{{ autoConflict.whole }}</span>
          <span v-else>有 {{ autoConflict.count }} 处修改与 {{ autoConflict.name }} 的修改冲突，已单独保存，不会丢失。</span>
          <span class="acts"><button type="button" class="ghost sm" @click="openConflicts('mine')">查看冲突</button></span>
        </div>
        <!-- 归档项目：页面级提示，不只在胶囊详情里（20 L-4） -->
        <p v-if="archivedNotice && !blocked" class="wb-notice warning" role="status" data-testid="archived-notice"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5l9.5 16.5h-19z" /><path d="M12 10v4.5M12 17.5v.01" /></svg><span>{{ archivedNotice }}</span></p>
        <div v-if="permissionUpgrade" class="wb-notice info" role="status" data-testid="permission-upgrade">
          <span>{{ permissionUpgrade }}</span>
          <span class="acts"><button type="button" class="ghost sm" :disabled="isDirty" @click="reloadPage">刷新</button></span>
        </div>
        <p v-if="actionNote && !actionNoteOthers" class="wb-notice info" role="status"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5" /><path d="M12 11v5M12 8v.01" /></svg><span>{{ actionNote }}</span></p>
        <!-- T-S2：他人类短提示与载入提示浮在编辑区上方，不推挤正文 -->
        <div class="wb-float-stack" :class="{ 'below-tool': mode === 'edit' }" aria-live="polite">
          <div v-if="othersUpdated" class="wb-notice info floating" role="status" data-testid="others-updated">
            <svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5" /><path d="M12 11v5M12 8v.01" /></svg>
            <span v-if="othersUpdated.here" data-testid="others-here">{{ othersUpdated.self ? '你在另一个标签页更新了这一段。' : `${othersUpdated.names[0] || '其他成员'} 更新了你正在看的这一段。` }}</span>
            <span v-else>{{ othersUpdatedText(othersUpdated.names, othersUpdated.objects, false, frozenOpen.length > 0) }}</span>
            <span class="acts">
              <button type="button" class="ghost sm" :disabled="saving || Boolean(blocked) || offline" :title="blocked || offline ? '需要先保存你的修改' : undefined" @click="refreshOthersNow">{{ saving ? "保存后刷新…" : "立即刷新" }}</button>
              <button type="button" class="subtle sm" :disabled="opBusy" @click="openHistory">查看修改记录</button>
              <button type="button" class="subtle icon-btn" aria-label="关闭提示" @click="othersUpdated = null"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg></button>
            </span>
          </div>
          <p v-if="actionNote && actionNoteOthers" class="wb-float-note" role="status" data-testid="float-note">{{ actionNote }}</p>
          <p v-if="sinkNote" class="wb-float-note sink" role="status" data-testid="sink-note"><span class="txt">{{ sinkNote.text }}</span><button type="button" class="link" @click="sinkNote = null">知道了</button></p>
          <p v-if="floatingReason" class="wb-float-note" role="status" data-testid="float-reason"><span class="spin" aria-hidden="true"></span>{{ floatingReason }}</p>
        </div>
        <p v-if="multiTab && !localDraft && !blocked" class="wb-notice info" role="status" data-testid="multi-tab"><span>此文档已在另一个标签页打开，两处的修改会分别保存，可能产生冲突。</span><span class="acts"><button type="button" class="ghost sm" @click="dismissMultiTab">知道了</button></span></p>
        <!-- S11：草稿恢复条只锁编辑区；顶栏、目录和面板照常可用 -->
        <div v-if="localDraft" class="draft-recovery" role="status" data-testid="draft-recovery">
          <div class="draft-row">
            <svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h9l4 4v14H6z" /><path d="M14 3v5h5" /><path d="M9 13h7M9 17h5" /></svg>
            <span>{{ recoverySummary(localDraft) }}{{ localDraft.docRevision === document.revision ? '' : '服务器上的文档在这之后被修改过，恢复时会合并，改到同一段的会列为冲突。' }}</span>
            <label v-if="localDrafts.length > 1" class="draft-pick">本机共 {{ localDrafts.length }} 份
              <select class="control" :value="localDrafts.indexOf(localDraft)" :disabled="draftBusy" @change="chooseLocalDraft(Number(($event.target as HTMLSelectElement).value))">
                <option v-for="(item, index) in localDrafts" :key="item.tabId" :value="index">{{ draftTime(item) }}（{{ item.changed || 1 }} 处）</option>
              </select>
            </label>
            <span class="acts">
              <template v-if="draftConfirmDiscard">
                <span class="draft-confirm">舍弃后这份本机修改将无法恢复。</span>
                <button type="button" class="ghost sm" @click="draftConfirmDiscard = false">返回</button>
                <button type="button" class="primary danger sm" data-testid="draft-discard-confirm" @click="discardLocalDraft">舍弃</button>
              </template>
              <template v-else>
                <button type="button" class="subtle sm" :aria-expanded="draftDiffOpen" @click="draftDiffOpen = !draftDiffOpen">查看差异</button>
                <button type="button" class="primary sm" :disabled="draftBusy || readonly || Boolean(restoreRefusal)" :title="restoreRefusal ? '请先处理未完成的冲突' : undefined" data-testid="draft-restore" @click="restoreLocalDraft">{{ draftBusy ? '恢复中…' : '恢复并保存' }}</button>
                <button type="button" class="ghost sm" @click="downloadLocalDraft">下载</button>
                <button type="button" class="ghost sm" :disabled="draftBusy" @click="discardLocalDraft">舍弃</button>
              </template>
            </span>
          </div>
          <p v-if="restoreRefusal" class="draft-refusal" role="alert" data-testid="restore-refusal"><span>{{ restoreRefusal.text }}</span><button type="button" class="ghost sm" @click="showRefusedConflicts">查看冲突</button></p>
          <p class="draft-hint">处理本机草稿（恢复、下载或舍弃）之前，正文暂时不能编辑，避免两份修改互相覆盖；目录、版本记录和下载照常可用。</p>
          <div v-if="draftDiffOpen" class="draft-diff" aria-label="本机草稿与服务器正文的差异">
            <p v-if="!draftDiff.added.length && !draftDiff.removed.length">与服务器正文相同。</p>
            <pre v-for="(block, index) in draftDiff.added" :key="`a${index}`" class="ins">{{ block }}</pre>
            <pre v-for="(block, index) in draftDiff.removed" :key="`r${index}`" class="del">{{ block }}</pre>
          </div>
        </div>
      </div>
      <DocumentDiagrams ref="diagrams" :document="document" :headings="outline" :heading-index="selectedHeading" @inserted="acceptDiagram" @busy="diagramBusy = $event" @conflict="derivedConflict" />

      <!-- 第 1 步：三栏（目录 / 编辑区 / 右侧面板），同一时间只开一个面板 -->
      <div class="main3" :class="{ 'no-outline': mode !== 'edit', 'has-panel': panelShown }">
        <div v-if="mode !== 'edit'" class="original-viewer">
          <div v-if="originalPhase === 'loading'" class="preview-state" role="status">正在使用 Word 生成{{ mode === 'layout' ? '编辑稿' : '原稿' }}预览...</div>
          <div v-else-if="originalPhase === 'error'" class="preview-state" role="alert">
            <p>{{ originalError }}</p>
            <button type="button" class="ghost" @click="loadOriginal()">重试预览</button>
          </div>
          <div v-else class="pdf-viewer">
            <div ref="pdfScroll" class="pdf-scroll" tabindex="0" role="region" :aria-label="`Word ${mode === 'layout' ? '编辑稿' : '原稿'}`">
              <div v-for="page in pdfPages" :key="page.number" class="pdf-page" :style="{ width: `${page.width}px`, height: `${page.height}px`, zoom: String(paperZoom / wordDisplayZoom) }">
                <canvas :data-page="page.number" width="0" height="0" role="img" :aria-busy="!page.ready" :aria-label="`Word ${mode === 'layout' ? '编辑稿' : '原稿'}第 ${page.number} 页`"></canvas>
                <span v-if="!page.ready" class="pdf-page-loading" role="status">正在显示第 {{ page.number }} 页…</span>
              </div>
            </div>
          </div>
        </div>

        <!-- 第 4 步：目录 -->
        <nav v-show="mode === 'edit'" class="outline" aria-label="文档目录">
          <div class="ohead">
            <span>目录</span>
            <div class="acts">
              <button ref="outlineSearchButton" type="button" class="subtle icon-btn" aria-label="搜索标题" :aria-pressed="outlineSearchOpen" @click="toggleOutlineSearch"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6.5" /><path d="M20 20l-4.2-4.2" /></svg></button>
              <button type="button" class="subtle icon-btn" aria-label="添加标题" aria-haspopup="dialog" :aria-expanded="addHeadingOpen" :disabled="readonly || opBusy" @click="addHeadingOpen = !addHeadingOpen"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg></button>
            </div>
          </div>
          <div v-if="outlineSearchOpen" class="osearch"><input ref="outlineSearchInput" v-model="outlineSearch" class="control" type="search" placeholder="搜索标题" aria-label="搜索标题" @keydown.esc.prevent="closeOutlineSearch" /></div>
          <form v-if="addHeadingOpen" class="add-heading" aria-label="添加标题" @submit.prevent="addHeading" @keydown.esc="addHeadingOpen = false">
            <label for="new-heading">标题名称</label>
            <input id="new-heading" v-model="headingTitle" class="control" type="text" maxlength="120" :disabled="addingHeading || saving || aiLocked || readonly" required />
            <template v-if="selectedHeading !== null">
              <label for="heading-placement">位置（相对“{{ outline[selectedHeading]?.text }}”）</label>
              <select id="heading-placement" v-model="headingPlacement" class="control" :disabled="addingHeading || saving || aiLocked || readonly">
                <option value="child" :disabled="(outline[selectedHeading]?.level || 0) >= 6">下级标题</option>
                <option value="sibling">同级标题</option>
              </select>
            </template>
            <p v-if="aiLocked && !addingHeading" class="hint">{{ localDraft ? '请先处理本机草稿。' : '正文暂时锁定（版本记录或写作助手的草稿正在使用），收起后再添加。' }}</p>
            <p v-if="headingError" class="form-error" role="alert">{{ headingError }}</p>
            <div class="add-acts">
              <button type="button" class="ghost sm" :disabled="addingHeading" @click="addHeadingOpen = false">取消</button>
              <button type="submit" class="primary sm" :disabled="addingHeading || saving || aiLocked || readonly || !headingTitle.trim()">{{ addingHeading ? "添加中..." : "添加到目录" }}</button>
            </div>
          </form>
          <div v-if="assignRestricted && outline.length" class="seg ofilter" role="group" aria-label="目录筛选">
            <button type="button" :class="{ on: outlineMode === 'all' }" :aria-pressed="outlineMode === 'all'" data-testid="outline-filter-all" @click="outlineFilter = 'all'">全部</button>
            <button type="button" :class="{ on: outlineMode === 'mine' }" :aria-pressed="outlineMode === 'mine'" data-testid="outline-filter-mine" @click="outlineFilter = 'mine'">我的部分 {{ outlineMineCount }}</button>
          </div>
          <p v-if="outline.length === 0" class="oempty">暂无标题</p>
          <p v-else-if="!outlineShown.length" class="oempty">{{ outlineMode === 'mine' && !outlineSearch.trim() ? '没有分给你的章节' : '没有匹配的标题' }}</p>
          <div v-else class="olist">
            <button v-for="entry in outlineShown" :key="entry.index" type="button" class="oitem outline-item" :class="[`l${Math.min((entry.info?.depth ?? 0) + 1, 4)}`, { on: selectedHeading === entry.index, empty: entry.info?.empty }]" :aria-current="selectedHeading === entry.index ? 'true' : undefined" :disabled="opBusy" @click="selectHeading(entry.index)">
              <span class="otext">{{ entry.item.text }}</span>
              <svg v-if="!entry.mine" class="ico olock" data-testid="outline-lock" viewBox="0 0 24 24" role="img" aria-label="只读"><rect x="5" y="11" width="14" height="9" rx="1.5" /><path d="M8 11V8a4 4 0 0 1 8 0v3" /></svg>
              <i v-if="conflictHeadings.has(entry.item.text)" class="omark danger outline-dot conflict" role="img" title="本章节有冲突待处理" aria-label="有冲突"></i>
              <i v-else-if="updatedHeadings.has(entry.item.text)" class="omark info outline-dot updated" role="img" title="他人更新了本章节" aria-label="有更新"></i>
              <span v-else-if="entry.info?.empty" class="ostate">待编写</span>
            </button>
          </div>
          <div class="ofoot"><span><i class="omark danger" aria-hidden="true"></i>冲突</span><span><i class="omark info" aria-hidden="true"></i>他人更新</span><span>待编写 {{ pendingSections }} 节</span></div>
        </nav>

        <section v-show="mode === 'edit'" class="editor" aria-label="编辑区">
          <!-- 第 5 步：格式工具栏（段落样式下拉 + 图标按钮），fieldset 与 disabled 绑定不变 -->
          <fieldset class="etool" aria-label="文字格式" :disabled="aiLocked || addingHeading || readonly">
            <select class="control" aria-label="段落样式" :value="paraStyle" @change="format('formatBlock', ($event.target as HTMLSelectElement).value)">
              <option value="p">正文</option><option value="h1">标题 1</option><option value="h2">标题 2</option><option value="h3">标题 3</option>
            </select>
            <span class="divider-v" aria-hidden="true"></span>
            <button type="button" class="subtle tool" title="加粗" aria-label="加粗" @mousedown.prevent @click="format('bold')"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M7 5h6a3.5 3.5 0 0 1 0 7H7zM7 12h7a3.5 3.5 0 0 1 0 7H7z" /></svg></button>
            <button type="button" class="subtle tool" title="斜体" aria-label="斜体" @mousedown.prevent @click="format('italic')"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M10 5h8M6 19h8M14 5l-4 14" /></svg></button>
            <span class="divider-v" aria-hidden="true"></span>
            <button type="button" class="subtle tool" title="项目符号" aria-label="项目符号" @mousedown.prevent @click="format('insertUnorderedList')"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6h11M9 12h11M9 18h11" /><circle cx="4.5" cy="6" r=".8" /><circle cx="4.5" cy="12" r=".8" /><circle cx="4.5" cy="18" r=".8" /></svg></button>
            <button type="button" class="subtle tool" title="编号列表" aria-label="编号列表" @mousedown.prevent @click="format('insertOrderedList')"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M10 6h10M10 12h10M10 18h10" /><path d="M4 5l1.5-1v5M3.5 14.5a1.5 1.5 0 1 1 2.5 1.2L3.5 19H6" /></svg></button>
            <span class="divider-v" aria-hidden="true"></span>
            <button type="button" class="subtle tool" :disabled="saving || addingHeading || aiLocked || Boolean(aiUnavailable('diagram'))" :title="aiUnavailable('diagram') || undefined" @mousedown.prevent @click="openDiagrams"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="4" width="7" height="6" rx="1" /><rect x="13.5" y="14" width="7" height="6" rx="1" /><path d="M7 10v4.5a2 2 0 0 0 2 2h4.5" /></svg>Visio 图表</button>
            <button type="button" class="subtle tool" :disabled="readonly || !canStartAi || Boolean(aiDraft) || assignRestricted" :title="assignRestricted ? '按分工编辑的文档，编写规范和整篇写作要求只有项目负责人可以修改。' : undefined" data-testid="skill-settings-open" @mousedown.prevent @click="openSkillSettings"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 4h14v16H5z" /><path d="M9 8h6M9 12h6M9 16h3" /></svg>编写规范</button>
            <span class="spacer"></span>
            <span v-if="sectionChars !== null" class="tool-count">本节 {{ sectionChars.toLocaleString('zh-CN') }} 字</span>
          </fieldset>
          <!-- 第 6 步：纸张区。.paper-content 的 DOM 结构与 data-word-marker 不变（8.2 第 10 条） -->
          <div ref="desk" class="desk page-scroll"><div class="paper" :style="{ ...paperStyle, zoom: String(paperZoom) }"><div ref="editor" class="paper-content" :style="{ minHeight: paperStyle.minHeight }" :contenteditable="editable" :aria-readonly="!editable" :aria-busy="refreshNotice ? 'true' : undefined" :class="{ refreshing: refreshingEditor }" role="textbox" aria-label="文档正文" aria-multiline="true" data-placeholder="点击此处开始编写文档" spellcheck="false" @beforeinput="guardLocked" @mousedown="lockedMouseDown" @input="onEditorInput" @compositionstart="setComposing(true)" @compositionend="setComposing(false)" @paste="pasteContent" @drop="blockUnsupportedImages" @dragstart="blockInternalDrag" @mouseup="captureEditorTarget" @keyup="captureEditorTarget" @click="clearUpdatedAt($event.target)"></div>
            <div v-if="conflictMarks.length" class="conflict-marks" aria-label="冲突标记">
              <template v-for="mark in conflictMarks" :key="mark.key">
                <div v-if="mark.gap" class="conflict-gap" data-testid="conflict-gap" :style="{ top: `${mark.top}px` }" />
                <button type="button" class="conflict-mark" :class="{ approx: mark.approx, gap: mark.gap, frozen: mark.frozen }" :title="mark.tip" data-testid="conflict-mark" :style="{ top: `${mark.top}px` }" @click="openConflictCard(mark.key)"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5l9.5 16.5h-19z" /><path d="M12 10v4.5M12 17.5v.01" /></svg>{{ mark.label }}</button>
              </template>
            </div></div></div>
        </section>

        <!-- 第 8 步：写作助手面板 -->
        <aside v-if="mode === 'edit' && aiOpen" class="panel ai-panel" aria-label="写作助手">
          <div class="phead"><h2>写作助手</h2><button type="button" class="subtle icon-btn" aria-label="关闭" @click="aiOpen = false"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg></button></div>
          <div class="pbody">
            <div class="seg scope" role="group" aria-label="编写范围">
              <button type="button" :class="{ on: aiScope === 'section' }" :aria-pressed="aiScope === 'section'" :disabled="aiLocked" @click="chooseAiScope('section')">本章节</button>
              <button type="button" :class="{ on: aiScope === 'selection' }" :aria-pressed="aiScope === 'selection'" :disabled="aiLocked || !selectedText" @click="chooseAiScope('selection')">选中文字</button>
              <button v-if="!assignRestricted" type="button" :class="{ on: aiScope === 'document' }" :aria-pressed="aiScope === 'document'" :disabled="aiLocked" @click="chooseAiScope('document')">全文</button>
            </div>
            <p v-if="assignRestricted" class="pnote" data-testid="ai-doc-scope-note">全文编写只有项目负责人可以使用。</p>
            <p v-if="!canGenerate" class="pnote">在目录中选择章节，或在正文中选中文字。</p>
            <p v-if="!canGenerate && aiError" class="form-error" role="alert">{{ aiError }}</p>
            <template v-if="canGenerate">
              <div class="target"><b>{{ aiTargetTitle }}</b><span>{{ aiTargetHint }}</span></div>
              <blockquote v-if="aiScope === 'selection'" class="selected-text">{{ selectedText }}</blockquote>
              <div class="field">
                <span class="flabel">编写规范</span>
                <div class="skill-row"><span>{{ skillLabel }}</span><button type="button" class="text" :disabled="readonly || !canStartAi || Boolean(aiDraft)" @click="openSkillSettings">修改</button></div>
              </div>
              <div class="field">
                <span class="flabel">参考资料</span>
                <div class="skill-row"><span>{{ referenceCount ? `已选 ${referenceCount} 份` : '未添加' }}</span><button type="button" class="text" :disabled="opBusy || isDirty || guidanceChanged || Boolean(aiDraft)" @click="openPanel('references')">{{ referenceCount ? '查看与修改' : '添加' }}</button></div>
              </div>
              <section class="writing-guidance" aria-label="当前范围编写建议" :aria-busy="guidancePhase === 'loading' || guidanceSaving">
                <h3>编写建议</h3>
                <p v-if="guidancePhase === 'loading'" class="hint" role="status">正在读取当前范围的建议…</p>
                <template v-else-if="guidancePhase === 'ready'">
                  <p v-if="!guidanceAvailable.length" class="hint">本范围暂无模板建议，可在下方补充要求。</p>
                  <div v-if="guidanceAvailable.length > 1" class="guidance-browse">
                    <div class="field"><label for="guidance-search">查找章节</label><input id="guidance-search" v-model="guidanceSearch" class="control" type="search" placeholder="输入章节名称" /></div>
                    <div v-if="guidanceMatches.length" class="field"><label for="guidance-group">查看章节</label><select id="guidance-group" v-model="guidanceGroupKey" class="control"><option v-for="group in guidanceMatches" :key="guidanceKeyOf(group)" :value="guidanceKeyOf(group)">{{ group.headingPath || group.heading }}（{{ group.items.length }} 项）</option></select></div>
                    <p v-else class="hint" role="status">未找到匹配章节。<button type="button" class="text" @click="guidanceSearch = ''">清除查找</button></p>
                  </div>
                  <div v-if="guidanceGroup && guidanceItem" class="guidance-group" data-testid="guidance-current">
                    <h4>{{ guidanceGroup.headingPath || guidanceGroup.heading }}</h4>
                    <nav v-if="guidanceGroup.items.length > 1" class="guidance-pager" aria-label="章节编写建议分页">
                      <button type="button" class="ghost sm" :disabled="guidanceItemIndex <= 0" @click="moveGuidanceItem(-1)">上一项</button>
                      <span role="status">{{ guidanceItemIndex + 1 }} / {{ guidanceGroup.items.length }}</span>
                      <button type="button" class="ghost sm" :disabled="guidanceItemIndex >= guidanceGroup.items.length - 1" @click="moveGuidanceItem(1)">下一项</button>
                    </nav>
                    <div class="field guidance-item">
                      <label for="guidance-current-text">{{ guidanceItem.kind === 'guidance' ? '编写建议' : guidanceItem.kind === 'example' ? '编写示例' : '填写要求' }}<template v-if="guidanceItem.heading !== guidanceGroup.heading"> · 来自{{ guidanceItem.heading }}</template></label>
                      <textarea id="guidance-current-text" :key="guidanceItem.id" :value="guidanceText(guidanceItem)" class="control" rows="5" maxlength="20000" :disabled="!canStartAi || Boolean(aiDraft)" @input="editGuidance(guidanceItem, ($event.target as HTMLTextAreaElement).value)"></textarea>
                      <details v-if="guidanceItem.targetText" class="guidance-source"><summary>适用内容</summary><pre>{{ guidanceItem.targetText }}</pre></details>
                      <div class="guidance-source" v-if="guidanceText(guidanceItem) !== guidanceItem.sourceText">
                        <details v-if="guidanceItem.sourceText"><summary>模板原文</summary><pre>{{ guidanceItem.sourceText }}</pre></details>
                        <button type="button" class="text" :disabled="!canStartAi || Boolean(aiDraft)" @click="editGuidance(guidanceItem, null)">{{ guidanceItem.id.startsWith('object:') ? (guidanceItem.sourceText ? '恢复原表说明' : '清除本段建议') : '恢复模板' }}</button>
                      </div>
                      <div v-if="guidanceConflicts[guidanceItem.id] !== undefined" class="notice" role="alert">
                        <p>此建议已由他人更新：</p><pre>{{ guidanceConflicts[guidanceItem.id] }}</pre>
                        <button type="button" class="ghost sm" :disabled="!canStartAi" @click="resolveGuidanceChange(guidanceItem, false)">采用最新建议</button>
                        <button type="button" class="ghost sm" :disabled="!canStartAi" @click="resolveGuidanceChange(guidanceItem, true)">保留我的修改</button>
                      </div>

                    </div>
                  </div>
                  <div v-if="Object.keys(guidanceConflicts).length" class="notice" role="alert"><span>{{ Object.keys(guidanceConflicts).length }} 项建议有冲突</span><button type="button" class="text" @click="revealGuidanceItem(Object.keys(guidanceConflicts)[0]!)">查看冲突</button></div>
                  <p v-for="notice in guidanceNotices" :key="notice" class="notice" role="status">{{ notice }}</p>
                </template>
                <p v-if="guidanceError" class="field-error" role="alert">{{ guidanceError }}</p>
                <button v-if="guidancePhase === 'error'" type="button" class="ghost sm" :disabled="!canStartAi" @click="loadGuidance()">重新读取建议</button>
                <div v-if="guidanceChanged" class="guidance-actions">
                  <span class="hint">{{ Object.keys(guidanceEdits).length }} 项修改待保存</span>
                  <button type="button" class="ghost sm" :disabled="!canStartAi" @click="saveGuidance()">{{ guidanceSaving ? '保存中…' : '保存本篇建议' }}</button>
                  <button type="button" class="text" :disabled="!canStartAi" @click="discardGuidance">放弃修改</button>
                </div>
              </section>
              <form class="ai-form" @submit.prevent="generateSection">
                <div class="field">
                  <label for="ai-instruction">补充要求（选填）</label>
                  <textarea id="ai-instruction" v-model="aiInstruction" class="control" rows="3" maxlength="2000" :disabled="!canStartAi"></textarea>
                </div>
                <button type="submit" class="primary" :disabled="!canStartAi || guidancePhase === 'loading' || (guidancePhase !== 'ready' && !isDirty) || Boolean(Object.keys(guidanceConflicts).length)">{{ aiBusy ? "正在编写…" : aiDraft ? "重新生成" : "生成草稿" }}</button>
              </form>
              <section v-if="aiProgress.length || aiDraft" class="draft ai-progress" aria-label="编写进度">
                <div class="dh"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" /></svg><strong role="status" aria-live="polite">{{ aiDraft && aiComplete && !aiBusy ? '草稿已生成' : aiProgressLabel }}</strong><span>用时 {{ aiElapsed }} 秒<template v-if="aiDraft"> · 约 {{ countChars(aiDraft) }} 字</template></span></div>
                <div class="db">
                  <p v-if="aiDraft && !aiBusy">草稿显示在正文中（浅蓝底），在正文里的工具条上替换、追加或取消。</p>
                  <details v-if="aiProgress.length" :open="aiBusy">
                    <summary>执行步骤</summary>
                    <ol><li v-for="(step, index) in aiProgress" :key="index" :class="{ current: aiBusy && index === aiProgress.length - 1 }">{{ step.message }}</li></ol>
                  </details>
                </div>
              </section>
              <p v-if="aiError" class="form-error" role="alert">{{ aiError }}</p>
              <button v-if="selectionNeedsRefresh" type="button" class="ghost" :disabled="!canStartAi || isDirty" @click="refreshSelectionEditor">刷新编辑稿</button>
            </template>
            <details class="mem writing-memory">
              <summary><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5z" /><path d="M4 20.5A2.5 2.5 0 0 1 6.5 18H20v3H6.5" /></svg>编写记忆<span>{{ memoryCount ? `本文档 ${memoryCount}${memoryHasMore ? '+' : ''} 条` : '暂无' }}</span></summary>
              <p v-if="memoryLoading" class="hint" role="status">正在读取…</p>
              <div v-else-if="memoryError"><p class="form-error" role="alert">{{ memoryError }}</p><button type="button" class="ghost sm" @click="refreshMemory(memoryOffset)">重试</button></div>
              <template v-else-if="writingMemory">
                <pre class="memory-content">{{ writingMemory }}</pre>
                <nav v-if="memoryOffset > 0 || memoryHasMore" class="memory-pages" aria-label="编写记忆分页">
                  <button type="button" class="ghost sm" :disabled="memoryLoading || memoryOffset === 0" @click="refreshMemory(memoryOffset - 10)">上一页</button>
                  <button type="button" class="ghost sm" :disabled="memoryLoading || !memoryHasMore" @click="refreshMemory(memoryOffset + 10)">下一页</button>
                </nav>
                <button type="button" class="ghost sm" :disabled="aiLocked" @click="removeMemory">清除记忆</button>
              </template>
              <p v-else class="hint">暂无编写记忆</p>
            </details>
          </div>
        </aside>
        <DocumentSkillSettings v-if="skillOpen" :key="document.id" :document-id="document.id" :revision="document.revision" :configuration="document.formatTemplate.writingSkill" :refresh="refreshFromServer" @close="skillOpen = false" @busy="skillBusy = $event" @saved="acceptSkill" />
        <!-- 第 7 步：冲突面板 -->
        <DocumentConflictPanel v-if="mode === 'edit' && conflictOpen" ref="conflictPanel" :key="`${document.id}:${conflictTab}`" :document-id="document.id" :document-name="document.name" :revision="document.revision"
          :can-view-members="conflictSummary?.allOpen != null" :can-resolve="!readonly" :before-resolve="beforeResolve"
          :initial-tab="conflictTab" :mine-count="mineConflicts" :member-count="memberConflicts" :approx-keys="approxConflicts" :live-mine="liveMine" :local-pending="conflictLocalPending"
          @close="conflictOpen = false" @resolved="acceptResolution" @adopt="adoptLatest" @count="onConflictCount" @locate="locateConflict" />
        <AssignmentPanel v-if="assignOpen && mode === 'edit'" :document-id="document.id" :project-id="document.projectId" :objects="assignPanelObjects" :initial-filter="assignInitialFilter" @close="closeAssign" @changed="refreshOwnership()" />
        <SaveRejectedPanel v-if="rejectedOpen && mode === 'edit' && rejected" :violations="rejected.violations" :total="rejected.total" :project-owner="assignInfo?.projectOwner || ''" :busy="savingMine"
          @close="rejectedOpen = false" @locate="locateViolation" @save-mine="saveMineOnly" @download="downloadRejected" @copy="copyRejected" />
        <DocumentHistory v-if="historyOpen" :document-id="document.id" :revision="document.revision" :current="markdownContent" :restore-blocked-reason="historyRestoreBlocked" @close="historyOpen = false" @restored="acceptVersion" @busy="historyBusy = $event" @refresh="refreshFromServer().catch(() => undefined)" />
        <DocumentReferences v-if="mode === 'edit' && referencesOpen" ref="referencesPanel" :document-id="document.id" :revision="document.revision"
          :disabled="readonly || isDirty" @saved="acceptReferences" @close="referencesOpen = false" @busy="referencesBusy = $event" @dirty="referencesDirty = $event" />
        <DocumentAssignments v-if="mode === 'edit' && assignmentsOpen" ref="assignmentsPanel" :document-id="document.id" :revision="document.revision"
          :disabled="isDirty || blockGuidanceDirty || blockGuidanceBusy" @busy="assignmentsBusy = $event" @changed="acceptAssignments" @locate="locateAssignment" @close="closeAssignments">
          <template #default="{ objectIds, state }">
            <DocumentBlockGuidance ref="blockGuidancePanel" :document-id="document.id" :revision="document.revision"
              :object-ids="objectIds.filter((id: string) => state.items.some((item: { objectId: string; kind: string; canEdit: boolean }) => item.objectId === id && ['paragraph', 'list_item', 'table'].includes(item.kind) && item.canEdit))"
              :disabled="readonly || isDirty || Boolean(assignmentsPanel?.isDirty) || assignmentsBusy" @dirty="blockGuidanceDirty = $event" @busy="blockGuidanceBusy = $event" @saved="acceptBlockGuidance" />
          </template>
        </DocumentAssignments>
        <Teleport v-if="aiPreviewHost" :to="aiPreviewHost">
          <section class="ai-preview ai-ghost" aria-label="AI 实时预览" @input.stop @mouseup.stop @keyup.stop>
            <div class="ai-preview-tools">
              <span class="ai-preview-status" role="status">{{ aiBusy ? aiProgressLabel : aiStopped ? '已停止' : aiPendingSuggestions.length ? `AI 建议 ${aiPendingSuggestions.length} 处待处理` : aiComplete && aiSuggestions.length === 0 && aiPreviewHtml === '' && aiScope === 'section' ? '本节无需修改' : aiComplete ? 'AI 草稿' : '生成未完成' }}<template v-if="!aiBusy && aiSuggestionDropped">，有 {{ aiSuggestionDropped }} 处超出范围，已忽略</template></span>
              <button v-if="aiBusy" type="button" class="ghost sm" @click="stopAi">停止</button>
              <template v-else-if="aiPendingSuggestions.length">
                <button type="button" class="primary sm" title="Alt+Enter 接受当前一处" @click="resolveAllSuggestions(true)">全部接受</button>
                <button type="button" class="ghost sm" title="Alt+Backspace 拒绝当前一处" @click="resolveAllSuggestions(false)">全部拒绝</button>
                <button type="button" class="subtle sm" title="Alt+↑" @click="focusSuggestion(aiSuggestionCursor - 1)">上一处</button>
                <button type="button" class="subtle sm" title="Alt+↓" @click="focusSuggestion(aiSuggestionCursor + 1)">下一处</button>
              </template>
              <button v-else-if="aiComplete && aiScope === 'section' && aiPreviewHtml === '' && !aiDraft.trim()" type="button" class="subtle sm" @click="clearAiDraft">关闭</button>
              <template v-else>
                <button type="button" class="primary sm" :disabled="!canApplyDraft || (aiScope === 'section' && !aiAllowedModes.includes('replace'))" @click="insertSection('replace')">{{ aiInserting ? '写入中…' : aiScope === 'document' ? '应用全文修改' : aiScope === 'selection' ? '替换选中内容' : aiAllowedModes.includes('append') ? '替换本节正文' : '应用本节修改' }}</button>
                <button v-if="aiScope === 'section'" type="button" class="ghost sm" :disabled="!canApplyDraft || !aiAllowedModes.includes('append')" @click="insertSection('append')">追加到本节</button>
                <p v-if="aiComplete && aiScope === 'section' && !aiAllowedModes.includes('replace')" class="form-error" role="status">本节包含特殊 Word 格式，可追加正文。</p>
                <button type="button" class="subtle sm" :disabled="aiInserting" @click="clearAiDraft">取消</button>
              </template>
            </div>
            <div class="ai-preview-body" :class="{ writing: aiBusy }" aria-label="AI 正文" v-html="aiPreviewHtml"></div>
          </section>
        </Teleport>
      </div>

      <!-- 第 9 步：底部状态栏 -->
      <footer class="ws-status">
        <span class="cur">当前：{{ currentSection || '未选择章节' }}</span><span class="sep" aria-hidden="true"></span><span class="num">全文 {{ totalChars.toLocaleString('zh-CN') }} 字</span>
        <span class="spacer"></span>
        <template v-if="lastEditor"><span>最近由 {{ lastEditor.name }} 修改于 {{ shortTime(lastEditor.at) }}</span><span class="sep" aria-hidden="true"></span></template>
        <span>Ctrl+S 立即保存</span>
      </footer>
      <LeaveDialog v-if="leaveDialog" :mode="leaveDialog.mode" :count="leaveDialog.count" @download="downloadMyChanges" @stay="leaveDialog = null" @leave="confirmLeave(false)" @download-leave="confirmLeave(true)" />
    </template>
  </div>
</template>

<style scoped>
.writing-guidance { display:grid; gap:var(--space-2); min-width:0; }
.writing-guidance h3 { margin:0; font-size:var(--text-md); font-weight:var(--weight-medium); }
.guidance-group { border:1px solid var(--border-default); border-radius:var(--radius-control); padding:var(--space-3); min-width:0; }
.guidance-group h4 { margin:0; font-size:var(--text-sm); font-weight:var(--weight-medium); overflow-wrap:anywhere; line-height:var(--leading-md); }
.guidance-browse { display:grid; gap:var(--space-2); min-width:0; }
.guidance-browse .field { margin:0; min-width:0; }
.guidance-browse .control { width:100%; min-width:0; }
.guidance-pager { display:flex; justify-content:space-between; align-items:center; gap:var(--space-2); margin-top:var(--space-2); }
.guidance-pager span { color:var(--text-secondary); font-size:var(--text-sm); }
.guidance-item { margin-top:var(--space-3); }
.writing-guidance textarea { width:100%; line-height:var(--leading-md); resize:vertical; }
.guidance-source, .guidance-actions { display:flex; gap:var(--space-2); align-items:flex-start; flex-wrap:wrap; }
.guidance-source > details { flex:1; min-width:0; }
.guidance-source summary { cursor:pointer; color:var(--text-secondary); }
.writing-guidance pre { margin:var(--space-2) 0; white-space:pre-wrap; overflow-wrap:anywhere; font-family:var(--font); font-size:var(--text-sm); line-height:var(--leading-sm); }
.guidance-actions { align-items:center; }
.guidance-actions .hint { flex-basis:100%; }
/* 工作台重塑（12 §8.1）：对照 mockups/文档编辑工作台*.html；只用 base.css 的语义变量。 */
.ws { display:grid; grid-template-rows:auto auto minmax(0,1fr) var(--status-h); height:calc(100vh - var(--topbar-h)); min-width:0; min-height:0; background:var(--bg-page); color:var(--text-primary); font-family:var(--font); }
.ws > .state { grid-row:1 / -1; }
.state { display:flex; align-items:center; justify-content:center; flex-direction:column; gap:var(--space-3); color:var(--text-secondary); }
.ico { width:var(--icon); height:var(--icon); }

/* 文档栏 */
.docbar { grid-row:1; display:flex; align-items:center; gap:var(--space-3); height:var(--docbar-h); min-width:0; padding:0 var(--space-4) 0 var(--space-3); background:var(--bg-surface); border-bottom:1px solid var(--border-default); }
.doctitle { display:flex; align-items:center; gap:var(--space-2); min-width:0; }
.doctitle h1 { margin:0; min-width:0; font-size:var(--text-lg); line-height:var(--leading-lg); font-weight:var(--weight-semibold); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.doctitle .tag { flex:none; }
.spacer { flex:1; }
.icon-btn { width:var(--control-h-sm); height:var(--control-h-sm); padding:0; flex:none; }
.docbar .subtle, .docbar .primary, .conflict-toggle { flex:none; }
.divider-v { width:1px; height:var(--space-5); background:var(--border-default); flex:none; }
.seg { display:inline-flex; flex:none; padding:2px; background:var(--gray-100); border-radius:var(--radius-control); }
.seg button { height:var(--control-h-sm); padding:0 var(--space-3); border:0; border-radius:calc(var(--radius-control) - 2px); background:transparent; color:var(--text-secondary); font-family:var(--font); font-size:var(--text-sm); white-space:nowrap; cursor:pointer; }
.seg button:hover:not(:disabled):not(.on) { color:var(--text-primary); }
.seg button.on { background:var(--bg-surface); color:var(--text-primary); font-weight:var(--weight-medium); box-shadow:var(--shadow-1); }
.seg button:disabled { cursor:not-allowed; opacity:.45; }
/* 29 M-1：A07 只读块、gutter 归属、我的部分 */
.paper-content :deep([data-a07-lock]) { color:var(--text-secondary); cursor:default; position:relative; border-radius:2px; }
.paper-content :deep([data-a07-lock]:hover) { outline:1px dashed var(--border-default); outline-offset:2px; }
.paper-content :deep([data-a07-lock])::before { content:attr(data-assignee-label) / ""; position:absolute; right:calc(100% + 12px); top:2px; max-width:72px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; padding:0 6px; border-radius:8px; background:var(--bg-subtle); color:var(--text-tertiary); font-size:11px; line-height:16px; font-weight:400; font-style:normal; text-indent:0; pointer-events:none; }
.paper-content :deep([data-mine]) { position:relative; }
.paper-content :deep([data-mine])::after { content:""; position:absolute; left:-14px; top:2px; bottom:2px; width:3px; border-radius:2px; background:var(--brand-500); pointer-events:none; }
.paper-content :deep(.a07-hit) { outline:2px solid var(--warning-600); outline-offset:2px; }
.locked-bubble { position:fixed; z-index:40; max-width:360px; padding:var(--space-2) var(--space-3); background:var(--bg-surface); border:1px solid var(--border-default); border-radius:var(--radius-control); box-shadow:var(--shadow-2); font-size:var(--text-sm); color:var(--text-primary); }
.locked-bubble p { margin:0; }
.locked-bubble .stash { display:flex; flex-wrap:wrap; align-items:center; gap:var(--space-2); margin-top:var(--space-2); color:var(--text-secondary); }
.mode-chip { cursor:pointer; border:0; font:inherit; }
.ofilter { margin:0 0 var(--space-2); }
.olock { width:12px; height:12px; flex:none; color:var(--text-tertiary); }
.seg button:focus-visible { outline:var(--focus-outline); outline-offset:1px; }
.conflict-toggle { display:inline-flex; align-items:center; gap:6px; height:var(--control-h); padding:0 var(--space-3); border:1px solid var(--border-strong); border-radius:var(--radius-control); background:var(--bg-surface); color:var(--text-secondary); font-family:var(--font); font-size:var(--text-md); font-weight:var(--weight-medium); white-space:nowrap; cursor:pointer; }
.conflict-toggle.danger { background:var(--danger-50); border-color:var(--danger-200); color:var(--danger-600); }
.conflict-toggle.danger .count { background:var(--danger-600); color:var(--text-on-brand); }
.conflict-toggle.danger.on { border-color:var(--danger-600); }
.conflict-toggle.warning { background:var(--warning-50); border-color:var(--warning-200); color:var(--warning-600); }
.conflict-toggle.warning .count { background:var(--warning-600); color:var(--text-on-brand); }
.conflict-toggle.neutral.on, .conflict-toggle.warning.on { border-color:currentColor; }
.conflict-toggle:focus-visible { outline:var(--focus-outline); outline-offset:2px; }
.whole-tag { position:relative; display:inline-flex; align-items:center; flex:none; height:var(--pill-h); padding:0 var(--space-2); border-radius:var(--radius-pill); background:var(--gray-100); color:var(--text-secondary); font-size:var(--text-xs); white-space:nowrap; cursor:help; }
.whole-tag:focus-visible { outline:var(--focus-outline); outline-offset:2px; }
.whole-tag .tip { position:absolute; top:calc(100% + var(--space-2)); left:0; z-index:30; display:none; width:calc(var(--space-7) * 6); padding:var(--space-2) var(--space-3); border-radius:var(--radius-control); background:var(--gray-800); color:var(--text-on-brand); font-size:var(--text-xs); line-height:var(--leading-xs); white-space:normal; box-shadow:var(--shadow-2); }
.whole-tag:hover .tip, .whole-tag:focus .tip { display:block; }
.export-group { position:relative; display:flex; gap:1px; flex:none; }
.export-more { padding-inline:var(--space-2); }
.export-menu { position:absolute; top:calc(100% + var(--space-1)); right:0; z-index:20; display:flex; flex-direction:column; min-width:calc(var(--space-7) * 4); padding:var(--space-1); background:var(--bg-surface); border:1px solid var(--border-default); border-radius:var(--radius-sheet); box-shadow:var(--shadow-2); }
.export-menu button { height:var(--control-h); padding:0 var(--space-3); border:0; border-radius:var(--radius-control); background:transparent; color:var(--text-primary); font-family:var(--font); font-size:var(--text-sm); text-align:left; cursor:pointer; }
.export-menu button:hover { background:var(--bg-hover); }
.export-menu button:focus-visible { outline:var(--focus-outline); }
.export-error { position:absolute; top:calc(100% + var(--space-1)); right:0; z-index:21; display:grid; gap:var(--space-2); width:calc(var(--space-7) * 8); padding:var(--space-3); background:var(--bg-surface); border:1px solid var(--danger-200); border-radius:var(--radius-control); box-shadow:var(--shadow-2); color:var(--danger-600); font-size:var(--text-sm); }
.export-error p { margin:0; overflow-wrap:anywhere; }
.export-error .acts { display:flex; gap:var(--space-3); justify-content:flex-end; }

/* 提示条 */
.notices { grid-row:2; min-width:0; }
.wb-notice { display:flex; align-items:center; gap:var(--space-2); min-height:var(--notice-h); margin:0; padding:var(--space-1) var(--space-4); border-bottom:1px solid var(--border-default); font-size:var(--text-sm); line-height:var(--leading-sm); }
.wb-notice > span:not(.acts) { min-width:0; overflow-wrap:anywhere; }
.wb-notice .acts, .draft-row .acts { margin-left:auto; display:flex; gap:var(--space-2); align-items:center; flex-shrink:0; }
.wb-notice.info { background:var(--info-50); color:var(--gray-800); }
/* 终验 F-M1：编辑区重绘/载入中的中性提示 */
.wb-notice.neutral { background:var(--bg-subtle); color:var(--text-secondary); }
.wb-notice .spin { width:12px; height:12px; flex:none; border-radius:var(--radius-pill); border:2px solid var(--brand-200); border-top-color:var(--brand-600); animation:wb-spin .8s linear infinite; }
@keyframes wb-spin { to { transform:rotate(360deg); } }
.paper-content.refreshing { cursor:progress; opacity:.7; transition:opacity var(--duration) var(--ease-out); }
/* T-S3：锁定刷新时光标显示为“处理中” */
.paper-content.refreshing[contenteditable="false"] { cursor:progress; }
/* T-S2：他人类提示浮在编辑区上方，不推挤正文 */
.wb-float-stack { position:relative; height:0; z-index:6; display:flex; flex-direction:column; align-items:center; gap:var(--space-2); }
.wb-float-stack > * { position:relative; top:var(--space-2); max-width:min(760px, calc(100% - 2 * var(--space-4))); box-shadow:var(--shadow-md, 0 4px 16px rgba(0,0,0,.12)); }
.wb-float-note { display:flex; align-items:center; gap:var(--space-2); margin:0; padding:var(--space-1) var(--space-3); border:1px solid var(--border-default); border-radius:var(--radius-control); background:var(--bg-surface); color:var(--text-secondary); font-size:var(--text-sm); line-height:var(--leading-sm); }
.wb-float-note.sink { border-color:var(--warning-300, #f5c26b); background:var(--warning-50, #fff8e6); color:var(--text-primary); }
.wb-float-note.sink .link { border:0; background:none; color:var(--brand-600); cursor:pointer; padding:0; font:inherit; white-space:nowrap; flex:none; }
.wb-float-note.sink .txt { min-width:0; flex:1 1 auto; }
/* 设计师 S3：编辑模式下浮层放在格式工具栏下方，不盖住工具栏 */
.wb-float-stack.below-tool > * { top:calc(var(--etool-h) + var(--space-2)); }
.wb-float-note .spin { width:12px; height:12px; flex:none; border-radius:var(--radius-pill); border:2px solid var(--brand-200); border-top-color:var(--brand-600); animation:wb-spin .8s linear infinite; }
.wb-notice.floating { border:1px solid var(--info-200, var(--border-default)); border-radius:var(--radius-control); }
.draft-refusal { display:flex; align-items:center; gap:var(--space-2); margin:var(--space-1) 0 0 calc(var(--icon) + var(--space-3)); color:var(--danger-700); font-size:var(--text-sm); line-height:var(--leading-sm); }
.wb-notice.info > .ico { color:var(--info-600); }
.wb-notice.danger { background:var(--danger-50); color:var(--danger-700); }
.wb-notice.warning { background:var(--warning-50); color:var(--gray-800); }
.wb-notice.warning > .ico { color:var(--warning-600); }
.draft-recovery { padding:var(--space-2) var(--space-4); background:var(--warning-50); border-bottom:1px solid var(--warning-200); font-size:var(--text-sm); line-height:var(--leading-sm); }
.draft-row { display:flex; align-items:center; flex-wrap:wrap; gap:var(--space-2) var(--space-3); }
.draft-row > .ico { color:var(--warning-600); }
.draft-row > span:not(.acts) { min-width:0; overflow-wrap:anywhere; }
.draft-pick { display:flex; align-items:center; gap:var(--space-1); color:var(--text-secondary); }
.draft-pick .control { width:auto; height:var(--control-h-sm); font-size:var(--text-sm); }
.draft-confirm { color:var(--danger-600); }
.draft-hint { margin:var(--space-1) 0 0 calc(var(--icon) + var(--space-3)); color:var(--text-secondary); font-size:var(--text-xs); line-height:var(--leading-xs); }
.draft-diff { display:grid; gap:var(--space-1); margin-top:var(--space-2); max-height:calc(var(--space-7) * 6); overflow:auto; }
.draft-diff p { margin:0; color:var(--text-secondary); }
.draft-diff pre { margin:0; padding:var(--space-1) var(--space-2); white-space:pre-wrap; overflow-wrap:anywhere; font-family:inherit; border-radius:var(--radius-control); }
.draft-diff pre.ins { background:var(--diff-ins-bg); }
.draft-diff pre.del { background:var(--diff-del-bg); text-decoration:line-through; }

/* 三栏 */
.main3 { grid-row:3; position:relative; display:grid; grid-template-columns:var(--outline-w) minmax(0,1fr); min-width:0; min-height:0; }
.main3.has-panel { grid-template-columns:var(--outline-w) minmax(0,1fr) var(--panel-w); }
.main3.no-outline { grid-template-columns:minmax(0,1fr); }
.main3.no-outline.has-panel { grid-template-columns:minmax(0,1fr) var(--panel-w); }
.original-viewer { display:flex; flex-direction:column; min-width:0; min-height:0; background:var(--bg-sunken); }
.preview-state { display:flex; flex:1; flex-direction:column; align-items:center; justify-content:center; gap:var(--space-3); color:var(--text-secondary); }
.pdf-viewer { display:flex; flex:1; flex-direction:column; min-height:0; }
.pdf-scroll { flex:1; min-height:0; overflow:auto; padding:var(--space-5); text-align:center; }
.pdf-scroll:focus-visible { outline:2px solid var(--brand-600); outline-offset:-2px; }
.pdf-page { position:relative; margin:0 auto var(--space-5); background:var(--bg-surface); box-shadow:var(--shadow-paper); }
.pdf-page:last-child { margin-bottom:0; }
.pdf-page canvas { display:block; width:100%; height:100%; }
.pdf-page-loading { position:absolute; inset:0; display:grid; place-items:center; color:var(--text-secondary); font-size:var(--text-sm); }

/* 目录 */
.outline { position:relative; display:flex; flex-direction:column; min-height:0; background:var(--bg-surface); border-right:1px solid var(--border-default); }
.ohead { display:flex; align-items:center; justify-content:space-between; height:var(--etool-h); flex:none; padding:0 var(--space-2) 0 var(--space-4); border-bottom:1px solid var(--border-subtle); font-size:var(--text-sm); font-weight:var(--weight-semibold); }
.ohead .acts { display:flex; gap:2px; }
.osearch { padding:var(--space-2) var(--space-2) 0; }
.osearch .control { height:var(--control-h-sm); font-size:var(--text-sm); }
.add-heading { position:absolute; top:calc(var(--etool-h) - var(--space-1)); left:var(--space-2); right:var(--space-2); z-index:15; display:grid; gap:var(--space-2); padding:var(--space-3); background:var(--bg-surface); border:1px solid var(--border-default); border-radius:var(--radius-sheet); box-shadow:var(--shadow-2); font-size:var(--text-sm); }
.add-heading label { font-weight:var(--weight-medium); }
.add-heading .control { height:var(--control-h-sm); font-size:var(--text-sm); }
.add-acts { display:flex; justify-content:flex-end; gap:var(--space-2); }
.oempty { margin:0; padding:var(--space-4); color:var(--text-tertiary); font-size:var(--text-sm); }
.olist { flex:1; min-height:0; overflow:auto; padding:var(--space-2); display:flex; flex-direction:column; gap:1px; }
.oitem { display:flex; align-items:center; gap:6px; width:100%; min-height:30px; padding:5px var(--space-2); border:0; border-radius:var(--radius-control); background:transparent; color:var(--text-primary); font-family:var(--font); font-size:var(--text-sm); line-height:var(--leading-sm); text-align:left; cursor:pointer; }
.oitem:hover:not(:disabled) { background:var(--bg-hover); }
.oitem:disabled { cursor:not-allowed; opacity:.6; }
.oitem:focus-visible { outline:var(--focus-outline); outline-offset:-2px; }
.oitem.l1 { font-weight:var(--weight-medium); }
.oitem.l2 { padding-left:var(--outline-indent-2); }
.oitem.l3 { padding-left:var(--outline-indent-3); }
.oitem.l4 { padding-left:var(--outline-indent-4); }
.oitem.empty .otext { color:var(--text-tertiary); }
.oitem.on { background:var(--bg-active); color:var(--brand-700); font-weight:var(--weight-medium); }
.oitem.on .otext { color:var(--brand-700); }
.otext { flex:1; min-width:0; overflow-wrap:anywhere; }
.omark { display:inline-block; width:7px; height:7px; border-radius:var(--radius-pill); flex:none; }
.omark.danger { background:var(--danger-600); }
.omark.info { background:var(--info-600); }
.ostate { flex:none; color:var(--text-tertiary); font-size:var(--text-xs); } /* 终验 S-2：对比度 ≥ 4.5 */
.ofoot { display:flex; flex-wrap:wrap; gap:var(--space-1) var(--space-3); padding:var(--space-3) var(--space-4); border-top:1px solid var(--border-subtle); color:var(--text-tertiary); font-size:var(--text-xs); }
.ofoot span { display:inline-flex; align-items:center; gap:var(--space-1); }

/* 编辑区与格式工具栏 */
.editor { display:grid; grid-template-rows:var(--etool-h) minmax(0,1fr); min-width:0; min-height:0; }
.etool { display:flex; align-items:center; flex-wrap:nowrap; gap:2px; min-width:0; margin:0; padding:0 var(--space-3); overflow:hidden; background:var(--bg-surface); border:0; border-bottom:1px solid var(--border-default); }
.etool .tool { height:30px; min-width:30px; padding:0 var(--space-2); font-size:var(--text-sm); }
.etool .divider-v { margin:0 6px; }
.etool select.control { flex:none; width:112px; height:30px; font-size:var(--text-sm); border-color:transparent; background-color:transparent; }
.etool select.control:hover:not(:disabled) { background-color:var(--bg-hover); }
.etool:disabled select.control { opacity:.45; }
.tool-count { flex:none; padding-left:var(--space-2); color:var(--text-tertiary); font-size:var(--text-xs); white-space:nowrap; }
.desk { min-height:0; overflow:auto; padding:var(--space-5) var(--space-4) var(--space-7); background:var(--bg-sunken); }
.paper { position:relative; width:var(--paper-w); min-height:calc(var(--paper-w) * 1.26); margin:0 auto; padding:72px 96px; background:var(--bg-surface); box-shadow:var(--shadow-paper); }
.paper-content { min-height:calc(var(--space-7) * 20); outline:none; color:var(--text-primary); font-family:var(--font-doc); font-size:var(--doc-text); line-height:1.75; overflow-wrap:anywhere; }
/* S16：正文区不画焦点环（光标即焦点提示） */
.paper-content:focus, .paper-content:focus-visible { outline:none; box-shadow:none; }
.paper-content:empty::before { content:attr(data-placeholder); color:var(--text-disabled); font-family:var(--font); }
.paper-content[contenteditable="false"] { cursor:default; }
.paper-content :deep(h1),.paper-content :deep(h2),.paper-content :deep(h3),.paper-content :deep(h4),.paper-content :deep(h5),.paper-content :deep(h6) { color:var(--text-primary); font-family:var(--font); font-weight:700; line-height:1.5; }
.paper-content :deep(h1) { margin:0 0 var(--space-4); font-size:calc(var(--doc-text) * 1.375); text-align:center; }
.paper-content :deep(h2) { margin:var(--space-4) 0 10px; font-size:calc(var(--doc-text) * 1.125); }
.paper-content :deep(h3),.paper-content :deep(h4),.paper-content :deep(h5),.paper-content :deep(h6) { margin:18px 0 var(--space-2); font-size:var(--doc-text); }
.paper-content :deep(p) { margin:0 0 6px; }
.paper-content :deep(ul),.paper-content :deep(ol) { padding-left:var(--space-5); margin:0 0 6px; }
.paper-content :deep(table) { width:100%; border-collapse:collapse; margin:var(--space-2) 0 var(--space-3); table-layout:fixed; font-size:calc(var(--doc-text) * 0.875); line-height:1.5; }
.paper-content :deep(th),.paper-content :deep(td) { padding:var(--space-1) var(--space-2); border:1px solid var(--text-primary); vertical-align:top; overflow-wrap:anywhere; color:inherit; background:transparent; font-size:inherit; white-space:normal; }
.paper-content :deep(th) { font-weight:700; }
.paper-content :deep(img) { display:block; max-width:100%; max-height:calc(var(--space-7) * 14); width:auto; height:auto; object-fit:contain; margin:var(--space-4) auto; }
.paper-content :deep(a) { color:var(--text-link); }
.paper-content :deep([data-word-protected]) { cursor:not-allowed; }
.paper-content :deep([data-word-marker])::before { content:attr(data-word-marker); display:inline-block; margin-right:var(--space-2); }
/* 块标记：冲突（红线浅红底）、他人更新（蓝线）、定位闪烁 */
.paper-content :deep(.blk-conflict) { margin-left:-12px; margin-right:-12px; padding:6px 12px; border-radius:2px; background:var(--danger-50); box-shadow:inset 3px 0 0 var(--danger-600); }
.paper-content :deep(.blk-updated) { margin-left:-12px; margin-right:-12px; padding-left:12px; padding-right:12px; box-shadow:inset 3px 0 0 var(--info-200); }
.paper-content :deep(.blk-flash) { outline:2px solid var(--brand-200); outline-offset:2px; }
/* 方案 C：冻结区（冲突未处理，续写只存冲突副本） */
.paper-content :deep([data-frozen]) { outline:1.5px solid var(--danger-600); outline-offset:3px; border-radius:2px; background:color-mix(in srgb, var(--danger-50) 60%, transparent); }
.conflict-mark.frozen { background:var(--danger-700); }
.conflict-marks { position:absolute; inset:0; pointer-events:none; }
.conflict-gap { position:absolute; left:84px; right:84px; border-top:2px dashed var(--danger-600); }
.conflict-mark { position:absolute; right:var(--space-2); transform:translateY(-50%); display:inline-flex; align-items:center; gap:var(--space-1); height:22px; padding:0 var(--space-2); border:0; border-radius:var(--radius-control); background:var(--danger-600); color:var(--text-on-brand); font-family:var(--font); font-size:var(--text-xs); line-height:22px; white-space:nowrap; pointer-events:auto; cursor:pointer; }
.conflict-mark .ico { width:12px; height:12px; }
.conflict-mark.approx { background:var(--bg-surface); border:1px dashed var(--danger-600); color:var(--danger-600); }
.conflict-mark:focus-visible { outline:var(--focus-outline); outline-offset:2px; }

/* 右侧面板（写作助手；冲突与版本记录在各自组件里用同一套 .phead/.pbody 尺寸） */
.panel { display:flex; flex-direction:column; min-width:0; min-height:0; background:var(--bg-surface); border-left:1px solid var(--border-default); }
.phead { display:flex; align-items:center; gap:var(--space-2); height:var(--etool-h); flex:none; padding:0 var(--space-2) 0 var(--space-4); border-bottom:1px solid var(--border-subtle); }
.phead h2 { margin:0; font-size:var(--text-md); font-weight:var(--weight-semibold); }
.phead .icon-btn { margin-left:auto; }
.pbody { flex:1; min-height:0; overflow:auto; display:grid; grid-auto-rows:max-content; align-content:start; gap:var(--space-4); padding:var(--space-4); font-size:var(--text-sm); }
.pnote { margin:0; padding:10px var(--space-3); border-radius:var(--radius-control); background:var(--gray-50); color:var(--text-secondary); font-size:var(--text-xs); line-height:var(--leading-xs); }
.scope { display:grid; grid-template-columns:repeat(3, 1fr); }
.target { display:grid; gap:2px; padding:10px var(--space-3); border:1px solid var(--border-default); border-radius:var(--radius-control); background:var(--bg-subtle); }
.target b { font-size:var(--text-sm); font-weight:var(--weight-medium); overflow-wrap:anywhere; }
.target span { color:var(--text-tertiary); font-size:var(--text-xs); }
.field { display:grid; gap:6px; }
.field > label, .flabel { font-size:var(--text-sm); line-height:var(--leading-sm); font-weight:var(--weight-medium); color:var(--text-primary); }
.skill-row { display:flex; align-items:center; justify-content:space-between; gap:var(--space-2); min-height:var(--control-h); padding:0 10px; border:1px solid var(--border-strong); border-radius:var(--radius-control); color:var(--text-primary); }
.skill-row > span { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.ai-form { display:grid; gap:var(--space-3); }
.ai-form .primary { justify-self:start; }
.ai-panel textarea.control { width:100%; resize:vertical; }
.selected-text { margin:0; padding:var(--space-2) var(--space-3); max-height:calc(var(--space-7) * 3); overflow-y:auto; background:var(--bg-subtle); border-left:3px solid var(--brand-500); color:var(--text-secondary); font-family:var(--font-doc); white-space:pre-wrap; overflow-wrap:anywhere; }
.draft { border:1px solid var(--brand-200); border-radius:var(--radius-sheet); overflow:hidden; }
.draft .dh { display:flex; align-items:center; gap:6px; padding:10px var(--space-3); background:var(--brand-50); color:var(--brand-700); font-size:var(--text-xs); font-weight:var(--weight-medium); }
.draft .dh strong { font-weight:var(--weight-medium); min-width:0; overflow-wrap:anywhere; }
.draft .dh span { margin-left:auto; flex:none; font-weight:var(--weight-regular); color:var(--text-tertiary); }
.draft .db { display:grid; gap:var(--space-2); padding:var(--space-3); color:var(--text-secondary); font-size:var(--text-xs); line-height:var(--leading-xs); }
.draft .db p { margin:0; }
.draft summary { width:fit-content; cursor:pointer; }
.draft summary:focus-visible { outline:var(--focus-outline); }
.draft ol { display:grid; gap:var(--space-1); margin:var(--space-2) 0 0; padding-left:var(--space-5); max-height:calc(var(--space-7) * 4); overflow-y:auto; }
.draft li.current { color:var(--brand-600); font-weight:var(--weight-medium); }
.mem { padding-top:var(--space-3); border-top:1px solid var(--border-subtle); color:var(--text-secondary); font-size:var(--text-sm); }
.mem summary { display:flex; align-items:center; gap:6px; cursor:pointer; list-style:none; }
.mem summary::-webkit-details-marker { display:none; }
.mem summary .ico { width:14px; height:14px; }
.mem summary span { margin-left:auto; color:var(--text-tertiary); font-size:var(--text-xs); }
.mem summary:focus-visible { outline:var(--focus-outline); }
.memory-content { margin:var(--space-3) 0; max-height:calc(var(--space-7) * 6); overflow-y:auto; white-space:pre-wrap; overflow-wrap:anywhere; font-family:var(--font); font-size:var(--text-sm); line-height:var(--leading-sm); }
.memory-pages { display:flex; gap:var(--space-2); margin-bottom:var(--space-3); }
.hint { margin:0; color:var(--text-tertiary); font-size:var(--text-xs); line-height:var(--leading-xs); }
.form-error { margin:0; color:var(--danger-700); font-size:var(--text-sm); overflow-wrap:anywhere; }
/* 正文中的 AI 草稿预览（.ai-ghost） */
.ai-ghost { margin:var(--space-2) -12px; padding:var(--space-1) 12px; background:var(--brand-50); box-shadow:inset 3px 0 0 var(--brand-500); }
.ai-preview-tools { position:sticky; top:0; z-index:1; display:flex; align-items:center; flex-wrap:wrap; gap:var(--space-2); padding:var(--space-2) 0; background:var(--brand-50); border-bottom:1px solid var(--brand-200); font-family:var(--font); }
.ai-preview-status { margin-right:auto; color:var(--brand-700); font-size:var(--text-sm); font-weight:var(--weight-semibold); }
.ai-preview-body { padding-top:var(--space-2); min-height:var(--space-7); }
.ai-preview-body:empty { display:none; }
.paper-content :deep(.ai-del) { text-decoration:line-through; color:var(--fault); background:var(--fault-wash); }
.paper-content :deep(.ai-suggestion) { position:relative; display:flex; align-items:flex-start; gap:var(--space-2); margin:var(--space-1) 0; border-left:2px solid var(--stamp); }
.paper-content :deep(.ai-suggestion.current) { outline:2px solid var(--stamp); outline-offset:2px; }
.paper-content :deep(.ai-ins) { flex:1; background:var(--ok-wash); }
.paper-content :deep(.ai-suggestion-note) { flex:1; color:var(--fault); font-size:var(--text-sm); }
.paper-content :deep(.ai-suggestion-actions) { display:flex; gap:var(--space-1); font-family:var(--font); }
.ai-preview-body.writing::after { content:''; display:inline-block; width:1px; height:1em; background:var(--brand-600); }

/* 底部状态栏 */
.ws-status { grid-row:4; display:flex; align-items:center; gap:var(--space-4); min-width:0; padding:0 var(--space-4); background:var(--bg-surface); border-top:1px solid var(--border-default); color:var(--text-tertiary); font-size:var(--text-xs); white-space:nowrap; }
.ws-status .cur { min-width:0; overflow:hidden; text-overflow:ellipsis; }
.ws-status .sep { width:1px; height:12px; flex:none; background:var(--border-default); }
.num { font-variant-numeric:tabular-nums; }

/* 1366 等窄屏：文档栏按钮只留图标，工具栏按钮去掉文字 */
@media (max-width: 1500px) {
  .docbar .subtle:not(.icon-btn) { padding:0 var(--space-2); }
  .tool-count { display:none; }
}
</style>
