<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from "vue-router";
import { readSession } from "../session";
import DocumentDiagrams from "../components/DocumentDiagrams.vue";
import DocumentHistory from "../components/DocumentHistory.vue";
import DocumentSkillSettings from "../components/DocumentSkillSettings.vue";
import DOMPurify from "dompurify";
import type { PDFDocumentLoadingTask, PDFDocumentProxy } from "pdfjs-dist";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { createTurndown, editorMarkdown, storedImageRule } from "../editorMarkdown";
import { ApiError } from "../api/client";
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
const errorText = ref("");
const saving = ref(false);
let savePromise: Promise<void> | undefined;
let loadVersion = 0;
const diagrams = ref<InstanceType<typeof DocumentDiagrams> | null>(null);
const diagramBusy = ref(false);
const historyOpen = ref(false);
const historyBusy = ref(false);
const skillOpen = ref(false);
const skillBusy = ref(false);
const refreshingEditor = ref(false);
const undoingAi = ref(false);
const aiUndo = ref<{ id: string; content: string; revision: number } | null>(null);
const localDraft = ref<{ revision: number; content: string; html: string } | null>(null);
const exporting = ref(false);
const selectedHeading = ref<number | null>(null);
const headingTitle = ref("");
const headingPlacement = ref<"child" | "sibling">("child");
const addingHeading = ref(false);
const headingError = ref("");
const aiOpen = ref(false);
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
  skillOpen.value = false;
  aiUndo.value = null;
  memoryOffset.value = 0;
  memoryHasMore.value = false;
  localDraft.value = null;
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
  try {
    const record = await getDocument(id);
    const preview = await previewDocument(id);
    if (id !== documentId.value || version !== loadVersion) return;
    document.value = record;
    markdownContent.value = record.markdownContent;
    savedContent.value = record.markdownContent;
    selectedHeading.value = [...record.markdownContent.matchAll(/^(#{1,6})\s+(.+)$/gm)].length ? 0 : null;
    phase.value = "ready";
    await nextTick();
    if (id !== documentId.value || version !== loadVersion) return;
    showEditorHtml(preview.html);
    try {
      const cached = JSON.parse(localStorage.getItem(draftKey(id)) || "null");
      if (cached && typeof cached.content === "string" && typeof cached.html === "string" && Number.isInteger(cached.revision)) {
        if (cached.content === record.markdownContent) localStorage.removeItem(draftKey(id));
        else localDraft.value = cached;
      }
    } catch { /* A damaged local backup cannot replace the saved document. */ }
    void refreshMemory();
  } catch (error) {
    if (id !== documentId.value || version !== loadVersion) return;
    phase.value = "error";
    errorText.value = messageOf(error);
  }
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
    editor.value.innerHTML = DOMPurify.sanitize(html);
    paperStyle.value = {};
    try {
      const page = JSON.parse(editor.value.firstElementChild?.getAttribute("data-word-page") || "{}");
      for (const name of ["width", "minHeight", "paddingTop", "paddingBottom", "paddingLeft", "paddingRight"]) {
        if (typeof page[name] === "string" && /^\d+(?:\.\d+)?pt$/.test(page[name])) paperStyle.value[name] = page[name];
      }
      if (paperStyle.value.width) paperStyle.value.boxSizing = "content-box";
    } catch { /* Local drafts without page metadata keep the default paper. */ }
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
    if (editor.value) editor.value.contentEditable = String(!aiLocked.value && !addingHeading.value);
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
    const result = await addDocumentHeading(document.value.id, document.value.revision, selectedHeading.value, headingPlacement.value, title);
    await acceptContent(result);
    selectedHeading.value = result.headingIndex ?? null;
    headingTitle.value = "";
    aiDraft.value = "";
    aiRevision.value = null;
    await nextTick();
    if (selectedHeading.value !== null) scrollToHeading(selectedHeading.value);
  } catch (error) {
    headingError.value = messageOf(error);
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
  try {
    if (aiScope.value === "selection") {
      if (!draftSelection) throw new Error("请重新选择正文并生成。");
      const result = await replaceDocumentSelection(document.value.id, document.value.revision, draftSelection, aiDraft.value, instruction);
      clearAiDraft();
      await acceptContent(result);
      aiScope.value = "section";
      void refreshMemory();
    } else if (aiScope.value === "document") {
      if (!aiComplete.value || !aiEdits.value.length) throw new Error("全文草稿尚未完成，请重新生成。");
      const result = await applyDocumentEdits(document.value.id, document.value.revision, aiEdits.value, instruction);
      clearAiDraft();
      await acceptContent(result);
      aiScope.value = "document";
      void refreshMemory();
    } else if (selectedHeading.value !== null) {
      const result = await insertDocumentSection(document.value.id, document.value.revision, selectedHeading.value, aiDraft.value, insertMode, instruction);
      clearAiDraft();
      await acceptContent(result);
      await nextTick();
      scrollToHeading(selectedHeading.value);
      void refreshMemory();
    }
    if (document.value?.id === id && !isDirty.value && phase.value === "ready") {
      aiUndo.value = { id, content: previousContent, revision: document.value.revision };
    }
    clearAiDraft();
  } catch (error) {
    aiError.value = messageOf(error);
  } finally {
    aiInserting.value = false;
    if (editor.value) editor.value.contentEditable = String(!aiLocked.value && !addingHeading.value);
  }
}
async function undoAiChange() {
  if (!canUndoAi.value || !aiUndo.value || !document.value) return;
  const snapshot = aiUndo.value;
  undoingAi.value = true;
  if (editor.value) editor.value.contentEditable = "false";
  errorText.value = "";
  try {
    const result = await updateDocumentContent(snapshot.id, snapshot.revision, snapshot.content, undefined, snapshot.revision);
    aiUndo.value = null;
    await acceptContent(result);
    void refreshMemory();
  } catch (error) {
    errorText.value = messageOf(error);
  } finally {
    undoingAi.value = false;
    if (editor.value) editor.value.contentEditable = String(!aiLocked.value && !addingHeading.value);
  }
}
async function loadOriginal() {
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
  const html = copy.innerHTML;
  markdownContent.value = editorMarkdown(turndown, DOMPurify.sanitize(html, { RETURN_DOM: true }) as HTMLElement);
  persistLocalDraft(html);
  editorVersion.value += 1;
  if (event instanceof InputEvent && ["insertParagraph", "insertFromDrop"].includes(event.inputType)) layoutNeedsRefresh = true;
  if (aiDraft.value) aiError.value = "文档已变化，请重新生成草稿。";
  window.clearTimeout(autoSaveTimer);
  autoSaveTimer = window.setTimeout(() => void save(), 3000);
}
function draftKey(id: string) { return `doc-workbench.draft:${readSession()?.staffId || 'user'}:${id}`; }
function persistLocalDraft(html = editor.value?.innerHTML || "") {
  if (!document.value) return;
  try {
    const key = draftKey(document.value.id);
    if (!isDirty.value) localStorage.removeItem(key);
    else localStorage.setItem(key, JSON.stringify({ revision: document.value.revision, content: markdownContent.value, html: DOMPurify.sanitize(html) }));
  } catch { /* Server saving and navigation protection still apply when browser storage is full. */ }
}
function restoreLocalDraft() {
  if (!localDraft.value || !document.value || localDraft.value.revision !== document.value.revision) return;
  markdownContent.value = localDraft.value.content;
  showEditorHtml(localDraft.value.html);
  localDraft.value = null;
  window.clearTimeout(autoSaveTimer);
  autoSaveTimer = window.setTimeout(() => void save(), 3000);
}
function discardLocalDraft() {
  if (document.value) localStorage.removeItem(draftKey(document.value.id));
  localDraft.value = null;
}
function downloadLocalDraft() {
  if (!localDraft.value) return;
  const blob = new Blob([`<!doctype html><html><head><meta charset="utf-8"></head><body>${DOMPurify.sanitize(localDraft.value.html)}</body></html>`], { type: "text/html;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = window.document.createElement("a");
  link.href = url;
  link.download = `${document.value?.name || '文档'}-未保存内容.html`;
  window.document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function save(): Promise<void> {
  if (savePromise) return savePromise.then(() => isDirty.value && !errorText.value ? save() : undefined);
  if (!document.value || !isDirty.value || aiPreviewHost.value) return Promise.resolve();
  const record = document.value;
  const content = markdownContent.value;
  const memory = pendingMemory;
  const current = () => document.value === record && documentId.value === record.id;
  saving.value = true;
  errorText.value = "";
  savePromise = (async () => {
    try {
      const result = await updateDocumentContent(record.id, record.revision, content, memory);
      if (!current()) return;
      savedContent.value = content;
      record.revision = result.revision;
      record.status = result.status;
      record.markdownContent = content;
      persistLocalDraft();
      if (memory === pendingMemory && memory) { pendingMemory = undefined; void refreshMemory(); }
      if (layoutNeedsRefresh && !isDirty.value) await refreshEditorFormatting(false);
    } catch (error) { if (current()) errorText.value = messageOf(error); }
    finally {
      saving.value = false;
      savePromise = undefined;
      if (current() && isDirty.value && !errorText.value) {
        window.clearTimeout(autoSaveTimer);
        autoSaveTimer = window.setTimeout(() => void save(), 3000);
      }
    }
  })();
  return savePromise;
}
function blockUnsupportedImages(event: ClipboardEvent | DragEvent) {
  const transfer = event instanceof ClipboardEvent ? event.clipboardData : event.dataTransfer;
  if (!transfer) return;
  if ([...transfer.files].some(file => file.type.startsWith("image/")) || /<img\b/i.test(transfer.getData("text/html"))) {
    event.preventDefault();
    errorText.value = "不能直接粘贴或拖入图片。生成的图表请使用“插入正文”，原稿中的图片会保留。";
  }
}
async function pasteContent(event: ClipboardEvent) {
  blockUnsupportedImages(event);
  if (event.defaultPrevented) return;
  event.preventDefault();
  if (aiLocked.value || addingHeading.value || !event.clipboardData) return;
  const html = event.clipboardData.getData("text/html");
  if (html) window.document.execCommand("insertHTML", false, DOMPurify.sanitize(html, { FORBID_ATTR: ["style", "class", "id"] }));
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
  if (aiLocked.value || addingHeading.value || saving.value) return;
  window.clearTimeout(autoSaveTimer);
  await save();
  if (isDirty.value) return;
  aiOpen.value = false;
  historyOpen.value = true;
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
}
async function leaveDocument() {
  if (skillOpen.value || skillBusy.value || historyBusy.value || aiBusy.value || aiInserting.value || addingHeading.value || diagramBusy.value || refreshingEditor.value || undoingAi.value) {
    errorText.value = "操作仍在进行，请完成或停止后再离开文档。";
    return false;
  }
  if (aiDraft.value && !window.confirm("尚未采纳的 AI 草稿会丢失，是否离开？")) return false;
  window.clearTimeout(autoSaveTimer);
  await save();
  return !isDirty.value;
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
      root.contentEditable = "true";
      root.focus();
      selection?.removeAllRanges();
      selection?.addRange(restored);
    }
    if (scroll) scroll.scrollTop = scrollTop;
  } catch (error) {
    errorText.value = `排版刷新失败：${messageOf(error)}`;
  } finally {
    refreshingEditor.value = false;
    if (editor.value === root) root.contentEditable = String(!aiLocked.value && !addingHeading.value);
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
  if (!document.value || exporting.value || aiLocked.value) return;
  window.clearTimeout(autoSaveTimer);
  if (isDirty.value) await save();
  if (isDirty.value) return;
  exporting.value = true;
  errorText.value = "";
  try {
    const result = await exportDocument(document.value.id, document.value.revision);
    const link = window.document.createElement("a");
    link.href = `/api/v1/files/${result.fileId}/content`;
    link.download = `${document.value.name}.docx`;
    window.document.body.appendChild(link);
    link.click();
    link.remove();
  } catch (error) {
    errorText.value = messageOf(error);
  } finally {
    exporting.value = false;
  }
}
async function goBack() {
  if (aiLocked.value) return;
  window.clearTimeout(autoSaveTimer);
  if (isDirty.value) await save();
  if (!isDirty.value) void router.push({ name: "project-documents", params: { id: projectId.value } });
}
function onKeydown(event: KeyboardEvent) {
  if (event.ctrlKey && event.key.toLowerCase() === "s") {
    event.preventDefault();
    void save();
  }
}
watch(documentId, () => void load());
onBeforeRouteLeave(leaveDocument);
onBeforeRouteUpdate(leaveDocument);
onMounted(() => { window.addEventListener("beforeunload", beforeUnload); void load(); });
onUnmounted(() => {
  loadVersion += 1;
  window.removeEventListener("beforeunload", beforeUnload);
  clearAiDraft();
  window.clearTimeout(autoSaveTimer);
  originalController?.abort();
  void pdfTask?.destroy();
});
</script>

<template>
  <div class="workbench" @keydown="onKeydown">
    <div v-if="phase === 'loading'" class="state">正在加载文档...</div>
    <div v-else-if="phase === 'error'" class="state"><p>{{ errorText }}</p><button type="button" class="ghost" @click="load">重试</button></div>
    <template v-else-if="document">
      <header class="toolbar">
        <div class="toolbar-left">
          <button type="button" class="ghost" :disabled="aiLocked || addingHeading" @click="goBack">← 文档列表</button>
          <span class="toolbar-separator" aria-hidden="true"></span>
          <div class="document-heading"><h1>{{ document.name }}</h1></div>
        </div>
        <div class="toolbar-right">
          <button v-if="mode === 'edit'" type="button" class="ghost" :disabled="aiLocked || addingHeading || saving" @click="openHistory">版本记录</button>
          <button v-if="mode === 'edit' && aiUndo" type="button" class="ghost" :disabled="!canUndoAi" @click="undoAiChange">{{ undoingAi ? '撤回中…' : '撤回 AI 修改' }}</button>
          <div class="mode-switch" role="group" aria-label="文档视图">
            <button type="button" class="ghost" :class="{ active: mode === 'original' }" :disabled="aiLocked || addingHeading" @click="switchMode('original')">查看原稿</button>
            <button type="button" class="ghost" :class="{ active: mode === 'edit' }" :disabled="aiLocked || addingHeading" @click="switchMode('edit')">编辑稿</button>
            <button type="button" class="ghost" :class="{ active: mode === 'layout' }" :disabled="aiLocked || addingHeading || saving" @click="switchMode('layout')">版式预览</button>
          </div>
          <span class="toolbar-separator" aria-hidden="true"></span>
          <a v-if="mode === 'original' && document.originalDocxFileId" class="primary" :href="`/api/v1/files/${document.originalDocxFileId}/content`" download>下载原稿</a>
          <template v-else>
            <button type="button" class="ghost" :disabled="saving || !isDirty || aiLocked" @click="save">{{ saving ? "保存中..." : "保存" }}</button>
            <button type="button" class="primary" :disabled="exporting || saving || aiLocked" @click="handleExport">{{ exporting ? "导出中..." : "下载编辑稿" }}</button>
          </template>
        </div>
      </header>
      <p v-if="errorText" class="error-notice" role="alert">{{ errorText }}</p>
      <div v-if="localDraft" class="draft-recovery" role="status">
        <span>{{ localDraft.revision === document.revision ? '发现未保存的本地编辑。' : '文档已更新，请下载本地编辑核对，不能直接覆盖。' }}</span>
        <button type="button" class="ghost" :disabled="localDraft.revision !== document.revision" @click="restoreLocalDraft">恢复编辑</button>
        <button type="button" class="ghost" @click="downloadLocalDraft">下载未保存内容</button>
        <button type="button" class="ghost" @click="discardLocalDraft">舍弃</button>
      </div>
      <DocumentDiagrams ref="diagrams" :document="document" :headings="outline" :heading-index="selectedHeading" @inserted="acceptDiagram" @busy="diagramBusy = $event" />
      <div class="workspace" :class="{ 'original-mode': mode !== 'edit', 'ai-open': mode === 'edit' && aiOpen, 'history-open': historyOpen }">
        <div v-if="mode !== 'edit'" class="original-viewer">
          <div v-if="originalPhase === 'loading'" class="preview-state">正在使用 Word 生成{{ mode === 'layout' ? '编辑稿' : '原稿' }}预览...</div>
          <div v-else-if="originalPhase === 'error'" class="preview-state" role="alert">
            <p>{{ originalError }}</p>
            <button type="button" class="ghost" @click="loadOriginal">重试预览</button>
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
            <button v-for="(item, index) in outline" :key="index" type="button" class="ghost outline-item" :disabled="aiLocked || addingHeading" :class="{ selected: selectedHeading === index }" :aria-current="selectedHeading === index ? 'true' : undefined" :style="{ paddingLeft: `calc(var(--space-2) + (var(--space-3) * ${item.level - 1}))` }" @click="selectHeading(index)">{{ item.text }}</button>
          </nav>
          <form class="outline-form" @submit.prevent="addHeading">
            <h3>添加标题</h3>
            <label for="new-heading">标题名称</label>
            <input id="new-heading" v-model="headingTitle" class="control" type="text" maxlength="120" :disabled="addingHeading || saving || aiLocked" required />
            <label v-if="selectedHeading !== null" for="heading-placement">标题层级</label>
            <select v-if="selectedHeading !== null" id="heading-placement" v-model="headingPlacement" class="control" :disabled="addingHeading || saving || aiLocked">
              <option value="child" :disabled="(outline[selectedHeading]?.level || 0) >= 6">下级标题</option>
              <option value="sibling">同级标题</option>
            </select>
            <p v-if="headingError" class="form-error" role="alert">{{ headingError }}</p>
            <button type="submit" class="primary" :disabled="addingHeading || saving || aiLocked || !headingTitle.trim()">{{ addingHeading ? "添加中..." : "添加到大纲" }}</button>
          </form>
        </aside>
        <main v-show="mode === 'edit'" class="editor-area">
          <fieldset class="editor-toolbar" aria-label="文字格式" :disabled="aiLocked || addingHeading">
            <button type="button" class="ghost" title="一级标题" @mousedown.prevent @click="format('formatBlock', 'h1')">标题 1</button>
            <button type="button" class="ghost" title="二级标题" @mousedown.prevent @click="format('formatBlock', 'h2')">标题 2</button>
            <button type="button" class="ghost" title="正文" @mousedown.prevent @click="format('formatBlock', 'p')">正文</button>
            <span class="toolbar-separator" aria-hidden="true"></span>
            <button type="button" class="ghost" title="加粗" @mousedown.prevent @click="format('bold')"><strong>B</strong></button>
            <button type="button" class="ghost" title="斜体" @mousedown.prevent @click="format('italic')"><em>I</em></button>
            <span class="toolbar-separator" aria-hidden="true"></span>
            <button type="button" class="ghost" title="项目符号" @mousedown.prevent @click="format('insertUnorderedList')">项目符号</button>
            <button type="button" class="ghost" title="编号列表" @mousedown.prevent @click="format('insertOrderedList')">编号列表</button>
            <button type="button" class="primary" :aria-expanded="aiOpen" @mousedown.prevent @click="aiOpen = !aiOpen">写作助手</button>
            <button type="button" class="ghost" :disabled="!canStartAi || Boolean(aiDraft)" @mousedown.prevent @click="openSkillSettings">编写规范</button>
            <button type="button" class="ghost" :disabled="saving || addingHeading || aiLocked" @mousedown.prevent @click="openDiagrams">Visio 图表</button>
          </fieldset>
          <div class="page-scroll"><div class="paper" :style="paperStyle"><div ref="editor" class="paper-content" :style="{ minHeight: paperStyle.minHeight }" :contenteditable="!aiLocked && !addingHeading" :aria-readonly="aiLocked || addingHeading" role="textbox" aria-label="文档正文" aria-multiline="true" data-placeholder="点击此处开始编写文档" spellcheck="false" @input="onEditorInput" @paste="pasteContent" @drop="blockUnsupportedImages" @mouseup="captureEditorTarget" @keyup="captureEditorTarget"></div></div></div>
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
        <DocumentSkillSettings v-if="skillOpen" :key="document.id" :document-id="document.id" :revision="document.revision" :configuration="document.formatTemplate.writingSkill" @close="skillOpen = false" @busy="skillBusy = $event" @saved="acceptSkill" />
        <DocumentHistory v-if="historyOpen" :document-id="document.id" :revision="document.revision" @close="historyOpen = false" @restored="acceptVersion" @busy="historyBusy = $event" />
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
.draft-recovery { display:flex; align-items:center; gap:var(--space-3); padding:var(--space-2) var(--space-5); background:var(--sheet); border-bottom:1px solid var(--rule); font-size:var(--text-sm); }
.draft-recovery span { margin-right:auto; overflow-wrap:anywhere; }
.workspace { display:grid; grid-template-columns:calc(var(--space-7) * 5 + var(--space-5)) minmax(0,1fr); flex:1; min-height:0; }
.workspace.original-mode { grid-template-columns:minmax(0,1fr); }
.workspace.ai-open { grid-template-columns:calc(var(--space-7) * 5 + var(--space-5)) minmax(0,1fr) calc(var(--space-7) * 7); }
.workspace.history-open { grid-template-columns:calc(var(--space-7) * 5 + var(--space-5)) minmax(0,1fr) calc(var(--space-7) * 8); }
.workspace.original-mode.history-open { grid-template-columns:minmax(0,1fr) calc(var(--space-7) * 8); }
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
.paper-content :deep([data-word-marker])::before { content:attr(data-word-marker); display:inline-block; margin-right:var(--space-2); }
button:focus-visible { outline:var(--space-1) solid var(--focus-ring); outline-offset:var(--space-1); }
</style>
