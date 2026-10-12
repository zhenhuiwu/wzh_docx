<script setup lang="ts">
/**
 * A06 模板编辑器（16 §3.2–§3.5，过渡形态：块列表 + 右侧表单，见 16 §3.6 风险 1）。
 * 草稿自动保存（PUT draft，带 expectedDraftRevision）；发布、合并、上传、对比、恢复都在弹窗里完成。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { onBeforeRouteLeave, useRoute, useRouter } from "vue-router";
import StatePanel from "../components/StatePanel.vue";
import SaveStatus from "../components/SaveStatus.vue";
import TemplateTableGrid from "../components/template/TemplateTableGrid.vue";
import TemplateFormatPanel from "../components/template/TemplateFormatPanel.vue";
import TemplateVersionsPanel from "../components/template/TemplateVersionsPanel.vue";
import TemplatePublishDialog from "../components/template/TemplatePublishDialog.vue";
import TemplateRebaseDialog from "../components/template/TemplateRebaseDialog.vue";
import TemplateUploadDialog from "../components/template/TemplateUploadDialog.vue";
import TemplateCompareDialog from "../components/template/TemplateCompareDialog.vue";
import TemplateVersionViewDialog from "../components/template/TemplateVersionViewDialog.vue";
import { ApiError } from "../api/client";
import { discardTemplateDraft, getTemplateDraft, getTemplateVersion, listTemplateVersions, restoreTemplateVersion, saveTemplateDraft,
  type DraftPayload, type EditorBlock, type EditorStructure, type StyleValues, type TemplateBlockKind, type TemplateVersionItem,
  type TemplateVersionList, type VersionRef } from "../api/templates";
import { hasPermission, readSession } from "../session";
import { rememberCrumb } from "../crumbs";
import { restoreFocus } from "../dialogFocus";
import { revisionConflictText, shortTime, type Capsule } from "../saveModel";
import { EDITOR_KIND_LABELS, changesMarkdown, chapterIds, createBlock, deleteCheck, forSave, insertAfter, lockTag, moveBlock, moveChapter, rebindHeadings, removeBlocks } from "../templateEditing";

const route = useRoute();
const router = useRouter();
const templateId = computed(() => String(route.params.id ?? ""));
const documentId = computed(() => String(route.params.docId ?? ""));
const canManage = computed(() => hasPermission(readSession(), "templates.manage"));
const canPublish = computed(() => hasPermission(readSession(), "templates.publish"));

const phase = ref<"loading" | "ready" | "error" | "review">("loading");
const errorText = ref("");
const templateName = ref("");
const documentName = ref("");
const current = ref<VersionRef | null>(null);
const versions = ref<TemplateVersionList | null>(null);
const structure = ref<EditorStructure | null>(null);
const styles = ref<Record<string, StyleValues>>({});
const baseStyles = ref<Record<string, StyleValues>>({});
const hasDraft = ref(false);
const draftRevision = ref(0);
const parentVersion = ref<VersionRef | null>(null);
const restoredFrom = ref<VersionRef | null>(null);
const uploaded = ref(false);
const updatedAt = ref<string | null>(null);
const notePrefill = ref("");

const dirty = ref(false);
const saving = ref(false);
const saveError = ref("");
const offline = ref(false);
const conflict = ref("");
let changeNumber = 0;
let saveTimer = 0;
let retryTimer = 0;

const selectedId = ref("");
const rightTab = ref<"edit" | "format" | "versions">("edit");
const highlight = ref<string[]>([]);
const blockErrors = ref<Record<string, string>>({});
const notice = ref<{ kind: "success" | "warning" | "info"; text: string } | null>(null);
const stale = ref<TemplateVersionItem | null>(null);
const alignment = ref<DraftPayload["alignment"] | null>(null);
const actionBusy = ref(false);
const placing = ref<string>("");

const dialog = ref<"" | "publish" | "rebase" | "upload">("");
const compare = ref<{ from: string; to: string; title: string; fromLabel: string; toLabel: string } | null>(null);
const viewing = ref<TemplateVersionItem | null>(null);
const confirmBox = ref<{ title: string; text: string; action: string; danger?: boolean; run: () => Promise<void> | void } | null>(null);
const confirmEl = ref<HTMLDialogElement | null>(null);
const confirmBusy = ref(false);

const readonly = computed(() => !canManage.value || Boolean(conflict.value) || phase.value !== "ready");
const blocks = computed(() => structure.value?.blocks ?? []);
const topBlocks = computed(() => blocks.value.filter(block => !block.tableId && !block.unplaced));
const unplaced = computed(() => blocks.value.filter(block => block.unplaced));
const headings = computed(() => blocks.value.filter(block => block.kind === "heading" && !block.tableId));
const selected = computed(() => blocks.value.find(block => block.id === selectedId.value) ?? null);
const guidanceCount = computed(() => {
  const counts: Record<string, number> = {};
  for (const block of blocks.value) if (block.kind === "guidance" && block.headingId) counts[block.headingId] = (counts[block.headingId] ?? 0) + 1;
  return counts;
});
const currentSeq = computed(() => current.value?.seq ?? 0);
const publishBlock = computed(() => {
  if (!hasDraft.value && !dirty.value) return "还没有修改，无需发布。";
  if (unplaced.value.length) return `还有 ${unplaced.value.length} 条编写指导没有指定位置。`;
  if (conflict.value) return "草稿有冲突，请先刷新。";
  return "";
});

const capsule = computed<Capsule>(() => {
  if (!canManage.value) return "readonly";
  if (conflict.value || saveError.value) return "blocked";
  if (offline.value) return "offline";
  if (saving.value) return "saving";
  if (dirty.value) return "dirty";
  return "clean";
});
const capsuleText = computed(() => {
  switch (capsule.value) {
    case "readonly": return "只读";
    case "blocked": return conflict.value ? "保存冲突" : "未保存";
    case "offline": return "未连接，稍后重试";
    case "saving": return "正在保存…";
    case "dirty": return "有修改未保存";
    default: return hasDraft.value ? `草稿已保存${updatedAt.value ? ` · ${shortTime(updatedAt.value)}` : ""}` : "没有草稿";
  }
});
const capsuleDetail = computed(() => conflict.value || saveError.value || "");
const draftLabel = computed(() => {
  if (!hasDraft.value && !dirty.value) return `当前第 ${currentSeq.value} 版`;
  const base = parentVersion.value?.seq ?? currentSeq.value;
  return `草稿 · 基于第 ${base} 版${restoredFrom.value ? ` · 恢复自第 ${restoredFrom.value.seq} 版` : ""}${uploaded.value ? " · 来自上传的 Word" : ""}`;
});

function messageOf(error: unknown) { return error instanceof Error ? error.message : "暂时不能连接服务。"; }
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value));

function adopt(payload: DraftPayload, isDraft: boolean) {
  structure.value = clone(payload.structure);
  styles.value = clone(payload.format?.styles ?? payload.baseStyles ?? {});
  baseStyles.value = clone(payload.baseStyles ?? {});
  hasDraft.value = isDraft;
  draftRevision.value = payload.draftRevision;
  parentVersion.value = payload.parentVersion;
  restoredFrom.value = payload.restoredFrom;
  uploaded.value = payload.uploaded;
  updatedAt.value = payload.updatedAt;
  if (payload.notePrefill) notePrefill.value = payload.notePrefill;
  else if (payload.restoredFrom) notePrefill.value = `恢复自第 ${payload.restoredFrom.seq} 版`;
  dirty.value = false;
  saveError.value = "";
  conflict.value = "";
  blockErrors.value = {};
  if (!blocks.value.some(block => block.id === selectedId.value)) selectedId.value = "";
}

async function load(keepNotice = false) {
  phase.value = "loading";
  errorText.value = "";
  if (!keepNotice) notice.value = null;
  window.clearTimeout(saveTimer);
  try {
    const [state, list] = await Promise.all([getTemplateDraft(documentId.value), listTemplateVersions(documentId.value)]);
    templateName.value = state.templateName;
    documentName.value = state.documentName;
    current.value = state.current;
    versions.value = list;
    rememberCrumb(`template:${templateId.value}`, state.templateName);
    if (state.reviewRequired) { phase.value = "review"; return; }
    const payload = state.draft ?? state.base;
    if (!payload) throw new Error("模板内容没有载入。");
    adopt(payload, Boolean(state.draft));
    stale.value = state.draft && state.draft.parentVersion && state.current && state.draft.parentVersion.versionId !== state.current.versionId
      ? list.items.find(item => item.versionId === state.current!.versionId) ?? null : null;
    phase.value = "ready";
  } catch (error) {
    phase.value = "error";
    errorText.value = error instanceof ApiError && error.status === 403 ? "没有查看该模板的权限。" : messageOf(error);
  }
}

// ---------------------------------------------------------------- 保存

function changed() {
  if (readonly.value) return;
  changeNumber++;
  dirty.value = true;
  window.clearTimeout(saveTimer);
  saveTimer = window.setTimeout(() => void save(), 800);
}

async function save(): Promise<boolean> {
  if (!structure.value || readonly.value) return !dirty.value;
  if (saving.value) { window.clearTimeout(saveTimer); saveTimer = window.setTimeout(() => void save(), 300); return false; }
  window.clearTimeout(saveTimer);
  window.clearTimeout(retryTimer);
  if (!dirty.value) return true;
  saving.value = true;
  const number = changeNumber;
  try {
    const result = await saveTemplateDraft(documentId.value, {
      structure: forSave(structure.value), format: { styles: styles.value }, expectedDraftRevision: draftRevision.value,
      // D-8 / 25 S-1：始终带上开始编辑时的版本。别人已发布或放弃了这份草稿时，服务端按它重建草稿，之后走“草稿过期 → 合并”。
      // 来自上传 Word 的草稿块位置指向上传的文件，不能按版本重建，仍只在没有草稿时带。
      ...(hasDraft.value && uploaded.value ? {} : { parentVersionId: parentVersion.value?.versionId ?? current.value?.versionId }),
    });
    const rebuilt = hasDraft.value && draftRevision.value > 0 && result.draftRevision === 1;
    draftRevision.value = result.draftRevision;
    if (rebuilt) void noticeRebuilt();
    updatedAt.value = result.updatedAt;
    if (!hasDraft.value) { hasDraft.value = true; parentVersion.value = parentVersion.value ?? current.value; }
    offline.value = false;
    saveError.value = "";
    if (number === changeNumber) dirty.value = false;
    else saveTimer = window.setTimeout(() => void save(), 300);
    return number === changeNumber;
  } catch (error) {
    if (error instanceof ApiError && error.code === "REVISION_CONFLICT") {
      conflict.value = `${revisionConflictText(error.details as any).replace("更新了该文档，请刷新后重试。", "修改了草稿，请刷新后再继续。").replace("该文档", "草稿")}你的修改可以下载。`;
      void explainConflict();
    } else if (error instanceof ApiError && (error.status === 422 || error.status === 413)) {
      saveError.value = error.message;
      markErrors(((error.details as any)?.items ?? []) as { blockId: string | null; message: string }[]);
    } else if (error instanceof ApiError && error.status === 403) {
      saveError.value = "没有编辑模板的权限。";
    } else {
      offline.value = true;
      retryTimer = window.setTimeout(() => void save(), 5000);
    }
    return false;
  } finally { saving.value = false; }
}

async function flush(): Promise<boolean> {
  for (let tries = 0; tries < 20; tries++) {
    if (!dirty.value && !saving.value) return true;
    if (conflict.value || saveError.value) return false;
    if (saving.value) { await new Promise(resolve => window.setTimeout(resolve, 150)); continue; }
    if (!(await save()) && (conflict.value || saveError.value || offline.value)) return false;
  }
  return !dirty.value;
}

/** 保存冲突时查一次版本列表：如果期间有人发布了新版本，文案写明“{发布人} 已发布第 N 版”（25 S-1 最低形态）。 */
async function explainConflict() {
  const before = parentVersion.value?.versionId ?? current.value?.versionId;
  await refreshVersions();
  const latest = versions.value?.items.find(item => item.versionId === current.value?.versionId);
  if (latest && latest.versionId !== before && conflict.value) {
    conflict.value = `${latest.publishedBy?.name || "其他人"} 已发布第 ${latest.seq} 版，你打开的草稿已被发布或替换，请刷新后再继续。你的修改可以下载。`;
  }
}
/** 草稿已被别人发布或放弃、服务端按开始编辑时的版本重建了草稿：如果已有更新的版本，显示过期提示条（合并 / 对比 / 放弃）。 */
async function noticeRebuilt() {
  await refreshVersions();
  const latest = versions.value?.items.find(item => item.versionId === current.value?.versionId) ?? null;
  if (latest && parentVersion.value && latest.versionId !== parentVersion.value.versionId) stale.value = latest;
  else notice.value = { kind: "info", text: "原来的草稿已被放弃，已按你的修改重新保存为草稿。" };
}

function markErrors(items: { blockId: string | null; message: string }[]) {
  const next: Record<string, string> = {};
  for (const item of items) if (item.blockId) next[item.blockId] = item.message;
  blockErrors.value = next;
  const first = items.find(item => item.blockId)?.blockId;
  if (first) locate(first);
}

function saveFile(content: string, type: string, name: string) {
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([content], { type }));
  link.download = name;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}
/** D-17：下载可读的修改清单（.md，对照开始编辑时的版本）；同时附带草稿数据 .json 备查。 */
async function downloadMine() {
  if (!structure.value) return;
  const name = documentName.value || "模板";
  const reference = parentVersion.value ?? current.value;
  let base: EditorBlock[] = [];
  try { if (reference) base = (await getTemplateVersion(documentId.value, reference.versionId)).structure.blocks; }
  catch { /* 取不到对照版本时只列当前内容 */ }
  saveFile(changesMarkdown({ title: name, baseLabel: reference ? `第 ${reference.seq} 版` : "当前版本", base, mine: structure.value.blocks,
    baseStyles: baseStyles.value, styles: styles.value, time: new Date().toLocaleString() }), "text/markdown;charset=utf-8", `${name}-我的修改.md`);
  saveFile(JSON.stringify({ structure: forSave(structure.value), format: { styles: styles.value } }, null, 2), "application/json", `${name}-我的草稿.json`);
}

// ---------------------------------------------------------------- 编辑操作

function update(mutator: (list: EditorBlock[]) => EditorBlock[] | null | void) {
  if (!structure.value || readonly.value) return;
  const result = mutator(structure.value.blocks);
  if (result === null) return;
  if (result) structure.value.blocks = result;
  rebindHeadings(structure.value.blocks);
  changed();
}

function select(id: string) { selectedId.value = id; if (rightTab.value !== "edit") rightTab.value = "edit"; }
function locate(id: string) {
  const target = blocks.value.find(block => block.id === id);
  const shown = target?.tableId ?? id;
  selectedId.value = shown;
  void nextTick(() => document.querySelector(`[data-block-id="${shown}"]`)?.scrollIntoView({ block: "center" }));
}
function showImpact(ids: string[]) {
  highlight.value = ids;
  if (ids.length) locate(ids[0]);
  selectedId.value = "";
}

function anchorId() {
  const block = selected.value;
  if (!block) return topBlocks.value.at(-1)?.id ?? null;
  return block.tableId ?? block.id;
}
function insert(kind: "body" | "heading" | "guidance") {
  const anchor = anchorId();
  const anchorIndex = blocks.value.findIndex(block => block.id === anchor);
  const before = [...blocks.value.slice(0, anchorIndex + 1)].reverse().find(block => block.kind === "heading" && !block.tableId);
  const block = createBlock(kind, kind === "heading" ? "新标题" : kind === "guidance" ? "请在此填写编写指导。" : "新段落", before?.headingLevel ?? 1);
  if (kind === "guidance") block.headingId = before?.id ?? null;
  update(list => insertAfter(list, anchor, block));
  selectedId.value = block.id;
  rightTab.value = "edit";
  void nextTick(() => { const input = document.getElementById("tpl-block-text") as HTMLTextAreaElement | null; input?.focus(); input?.select(); });
}

function move(direction: -1 | 1) {
  const block = selected.value;
  if (!block || block.tableId) return;
  const result = block.kind === "heading" ? moveChapter(blocks.value, block.id, direction) : moveBlock(blocks.value, block.id, direction);
  if (!result) { notice.value = { kind: "info", text: block.kind === "heading" ? "已经到同级章节的边界，不能再移动。" : "已经到边界，不能再移动。" }; return; }
  update(() => result);
  void nextTick(() => locate(block.id));
}

function remove() {
  const block = selected.value;
  if (!block || block.tableId) return;
  if (block.kind === "toc") { notice.value = { kind: "warning", text: "目录不能在线删除。如需删除，请在 Word 中修改后上传为新草稿。" }; return; }
  const ids = block.kind === "heading" ? chapterIds(blocks.value, block.id) : [block.id];
  const reason = deleteCheck(blocks.value, ids);
  if (reason) { notice.value = { kind: "warning", text: block.kind === "heading" && ids.length > 1 ? `这一章不能整章删除：${reason}` : reason }; return; }
  const run = () => {
    const index = topBlocks.value.findIndex(item => item.id === block.id);
    update(list => removeBlocks(list, ids));
    selectedId.value = topBlocks.value[Math.min(index, topBlocks.value.length - 1)]?.id ?? "";
  };
  if (block.kind === "heading" && ids.length > 1) {
    confirmBox.value = { title: "删除整章", text: `删除“${block.text}”及其下 ${ids.length - 1} 块内容？发布前可以在版本面板对比，或点文档栏的“放弃草稿”。`, action: "删除整章", danger: true, run };
  } else run();
}

function onBlockKey(event: KeyboardEvent, block: EditorBlock) {
  if (event.target !== event.currentTarget) return;
  if (event.key === "Delete" || event.key === "Backspace") { event.preventDefault(); selectedId.value = block.id; remove(); }
  else if (event.key === "Enter") { select(block.id); }
}

// 段落样式下拉（白名单内：正文、标题 1–6）
const styleValue = computed(() => {
  const block = selected.value;
  if (!block) return "";
  if (block.isTable) return "表格正文";
  if (block.kind === "guidance") return "编写指导";
  if (block.kind === "heading") return `标题 ${block.headingLevel ?? 1}`;
  if (block.path && block.styleKey !== "正文" && !(block.styleKey ?? "").startsWith("标题")) return "keep";
  return "正文";
});
const styleLocked = computed(() => readonly.value || !selected.value || selected.value.isTable || selected.value.kind === "guidance" || selected.value.kind === "toc" || styleValue.value === "keep");
const styleTitle = computed(() => styleValue.value === "keep" ? "此段使用模板原有样式，可以改文字，不能改样式。" : selected.value?.isTable ? "单元格使用“表格正文”样式。" : "");
function setStyle(event: Event) {
  const value = (event.target as HTMLSelectElement).value;
  const block = selected.value;
  if (!block) return;
  update(() => {
    if (value === "正文") { if (block.kind === "heading") { block.kind = "body"; block.headingLevel = null; } }
    else { block.kind = "heading"; block.headingLevel = Number(value.replace("标题 ", "")); }
  });
}

// 右侧“编辑”表单
const kindOptions = computed(() => {
  const block = selected.value;
  if (!block || block.isTable || block.kind === "toc") return [];
  const base: TemplateBlockKind[] = ["heading", "body", "guidance", "placeholder", "example", "caption"];
  return base.map(kind => ({ kind, label: EDITOR_KIND_LABELS[kind], disabled: block.protected && ["guidance", "example", "placeholder"].includes(kind) && block.kind !== kind }));
});
function setKind(event: Event) {
  const kind = (event.target as HTMLSelectElement).value as TemplateBlockKind;
  const block = selected.value;
  if (!block) return;
  update(() => {
    block.kind = kind;
    block.headingLevel = kind === "heading" ? block.headingLevel ?? 2 : null;
    if (kind === "guidance" && !block.path) block.virtual = true;
    if (kind === "guidance" && !block.headingId) block.headingId = [...blocks.value.slice(0, blocks.value.indexOf(block))].reverse().find(item => item.kind === "heading")?.id ?? null;
  });
}
function setLevel(event: Event) { const block = selected.value; if (block) update(() => { block.headingLevel = Number((event.target as HTMLSelectElement).value); }); }
function setText(event: Event) { const block = selected.value; if (block) { block.text = (event.target as HTMLTextAreaElement).value; delete blockErrors.value[block.id]; update(() => undefined); } }
function setGuidanceHeading(event: Event) { const block = selected.value; if (block) update(() => { block.headingId = (event.target as HTMLSelectElement).value || null; }); }
function setCells(block: EditorBlock, cells: string[][]) { block.cells = cells; delete blockErrors.value[block.id]; update(() => undefined); }
const textLocked = computed(() => Boolean(selected.value?.lock?.text) && selected.value?.kind !== "guidance");
function setStyleValue(style: string, prop: string, value: any) {
  styles.value = { ...styles.value, [style]: { ...styles.value[style], [prop]: value } };
  changed();
}

// 未落位的编写指导
function place(block: EditorBlock, headingId: string) {
  if (!headingId) return;
  update((list) => {
    const rest = list.filter(item => item.id !== block.id);
    const chapter = chapterIds(rest, headingId);
    const last = chapter.at(-1) ?? headingId;
    block.unplaced = false;
    block.headingId = headingId;
    return insertAfter(rest, rest.find(item => item.id === last)?.tableId ?? last, block);
  });
  placing.value = "";
}
function dropUnplaced(block: EditorBlock) { update(list => list.filter(item => item.id !== block.id)); }

// ---------------------------------------------------------------- 发布、合并、上传、版本

async function openPublish() {
  if (publishBlock.value || actionBusy.value) return;
  actionBusy.value = true;
  try {
    if (!(await flush())) { notice.value = { kind: "warning", text: conflict.value || saveError.value || "草稿还没有保存，请稍后再发布。" }; return; }
    versions.value = await listTemplateVersions(documentId.value);
    dialog.value = "publish";
  } catch (error) { notice.value = { kind: "warning", text: messageOf(error) }; }
  finally { actionBusy.value = false; }
}
async function published(version: TemplateVersionItem) {
  dialog.value = "";
  notePrefill.value = "";
  stale.value = null;
  alignment.value = null;
  notice.value = { kind: "success", text: `已发布第 ${version.seq} 版。之后新建的文档使用新版本，已有文档不受影响。` };
  await load(true);
}
function onStale(details: any) {
  dialog.value = "";
  stale.value = details?.currentVersion ?? null;
  void refreshVersions();
}
async function refreshVersions() { try { versions.value = await listTemplateVersions(documentId.value); current.value = versions.value.current; } catch { /* 版本列表刷新失败不影响编辑 */ } }

async function openRebase() {
  if (!(await flush())) { notice.value = { kind: "warning", text: conflict.value || saveError.value || "草稿还没有保存，请稍后再合并。" }; return; }
  dialog.value = "rebase";
}
async function merged(payload: DraftPayload & { seq: number }) {
  dialog.value = "";
  stale.value = null;
  adopt(payload, true);
  await refreshVersions();
  notice.value = { kind: "success", text: `已合并到第 ${payload.seq} 版，可以继续编辑或发布。` };
}
function discard() {
  const seq = stale.value?.seq ?? currentSeq.value;
  confirmBox.value = { title: "放弃草稿", text: `放弃草稿后，你的修改不能找回。之后基于第 ${seq} 版重新编辑。`, action: "放弃草稿", danger: true, run: async () => {
    window.clearTimeout(saveTimer);
    window.clearTimeout(retryTimer);
    for (let tries = 0; saving.value && tries < 40; tries++) await new Promise(resolve => window.setTimeout(resolve, 100));
    dirty.value = false;
    if (hasDraft.value) await discardTemplateDraft(documentId.value, draftRevision.value);
    stale.value = null;
    notice.value = { kind: "info", text: `已放弃草稿，现在基于第 ${seq} 版编辑。` };
    await load(true);
  } };
}
async function openUpload() { if (!(await flush()) && hasDraft.value) { notice.value = { kind: "warning", text: conflict.value || saveError.value || "草稿还没有保存。" }; return; } dialog.value = "upload"; }
async function uploadedDraft(payload: DraftPayload) {
  dialog.value = "";
  stale.value = null;
  adopt(payload, true);
  alignment.value = payload.alignment ?? null;
  notice.value = null;
  await refreshVersions();
}
function compareVersion(item: TemplateVersionItem) {
  const list = versions.value?.items ?? [];
  if (item.versionId === current.value?.versionId) {
    const previous = list.find(other => other.seq === item.seq - 1);
    if (hasDraft.value && !previous) { compare.value = { from: item.versionId, to: "draft", title: `第 ${item.seq} 版与草稿对比`, fromLabel: `第 ${item.seq} 版`, toLabel: "草稿" }; return; }
    if (!previous) return;
    compare.value = { from: previous.versionId, to: item.versionId, title: `第 ${previous.seq} 版与第 ${item.seq} 版对比`, fromLabel: `第 ${previous.seq} 版`, toLabel: `第 ${item.seq} 版` };
    return;
  }
  compare.value = { from: item.versionId, to: current.value!.versionId, title: `第 ${item.seq} 版与第 ${currentSeq.value} 版对比`, fromLabel: `第 ${item.seq} 版`, toLabel: `第 ${currentSeq.value} 版` };
}
function compareDraft() {
  if (!current.value) return;
  compare.value = { from: current.value.versionId, to: "draft", title: `第 ${currentSeq.value} 版与我的草稿对比`, fromLabel: `第 ${currentSeq.value} 版`, toLabel: "我的草稿" };
}
function restore(item: TemplateVersionItem) {
  const run = async () => {
    if (!(await flush()) && hasDraft.value) throw new Error(conflict.value || saveError.value || "草稿还没有保存。");
    const payload = await restoreTemplateVersion(documentId.value, item.versionId, draftRevision.value);
    adopt(payload, true);
    stale.value = null;
    rightTab.value = "edit";
    notice.value = { kind: "success", text: `已把第 ${item.seq} 版恢复为草稿。发布后得到新版本，历史版本不变。${payload.notes?.length ? ` ${payload.notes.map(note => note.message).join("；")}` : ""}` };
    await refreshVersions();
  };
  confirmBox.value = { title: `恢复第 ${item.seq} 版`, text: hasDraft.value ? "当前草稿有未发布的修改，恢复会替换草稿。" : `把第 ${item.seq} 版的内容复制为草稿，发布后得到新版本。`, action: hasDraft.value ? "替换草稿" : "恢复为草稿", run };
}

async function runConfirm() {
  if (!confirmBox.value || confirmBusy.value) return;
  confirmBusy.value = true;
  try { await confirmBox.value.run(); confirmBox.value = null; }
  catch (error) { notice.value = { kind: "warning", text: messageOf(error) }; confirmBox.value = null; }
  finally { confirmBusy.value = false; }
}
// 25 M-4：确认框打开时记下焦点、默认聚焦“取消”（危险操作）或确认按钮，关闭后归还焦点
let confirmOpener: Element | null = null;
watch(confirmBox, (value, previous) => void nextTick(() => {
  if (value && !previous) {
    confirmOpener = document.activeElement;
    confirmEl.value?.showModal();
    confirmEl.value?.querySelector<HTMLElement>(value.danger ? "[data-confirm-cancel]" : "[data-testid=\"tpl-confirm-ok\"]")?.focus();
  } else if (!value && previous) {
    restoreFocus(confirmOpener, selectedId.value ? `[data-block-id="${selectedId.value}"]` : "[data-testid=\"tpl-editor\"] .docbar button");
    confirmOpener = null;
  }
}));

// ---------------------------------------------------------------- 离开

onBeforeRouteLeave(async () => {
  if (!dirty.value && !saving.value) return true;
  if (await flush()) return true;
  return window.confirm("模板草稿还没有保存，离开会丢失这些修改。是否离开？");
});
function beforeUnload(event: BeforeUnloadEvent) { if (dirty.value || saving.value) { event.preventDefault(); event.returnValue = ""; } }
watch(documentId, () => void load());
onMounted(() => { void load(); window.addEventListener("beforeunload", beforeUnload); });
onBeforeUnmount(() => { window.clearTimeout(saveTimer); window.clearTimeout(retryTimer); window.removeEventListener("beforeunload", beforeUnload); });

const kindLabel = (block: EditorBlock) => block.kind === "heading" ? `标题 ${block.headingLevel ?? 1}` : EDITOR_KIND_LABELS[block.kind] ?? block.kind;
const headingText = (id: string | null | undefined) => headings.value.find(item => item.id === id)?.text ?? "";
</script>

<template>
  <section class="tpl-editor" data-testid="tpl-editor">
    <StatePanel v-if="phase === 'loading'" kind="loading" text="正在读取模板…" />
    <StatePanel v-else-if="phase === 'error'" kind="error" title="模板没有载入" :text="errorText"><button type="button" class="ghost" @click="load()">重试</button></StatePanel>
    <StatePanel v-else-if="phase === 'review'" title="请先核对模板" text="这份模板还没有完成内容核对。核对后才能编辑和发布版本。"><router-link class="ghost" :to="{ name: 'template', params: { id: templateId } }">返回模板详情</router-link></StatePanel>
    <template v-else-if="structure">
      <header class="docbar">
        <button type="button" class="subtle icon-btn" aria-label="返回模板详情" title="返回模板详情" @click="router.push({ name: 'template', params: { id: templateId } })"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M15 6l-6 6 6 6" /></svg></button>
        <div class="doctitle">
          <h1 :title="documentName">{{ documentName }}</h1>
          <span class="tag plain">{{ templateName }}</span>
          <span class="tag" :class="hasDraft || dirty ? 'info' : 'plain'" data-testid="tpl-draft-label">{{ draftLabel }}</span>
          <button v-if="canManage && (hasDraft || dirty) && !stale && !conflict" type="button" class="text sm" data-testid="tpl-discard-draft" :disabled="actionBusy" @click="discard">放弃草稿</button>
          <SaveStatus :state="capsule" :label="capsuleText" :detail="capsuleDetail" @save="save" @retry="save" @details="() => notice = { kind: 'warning', text: capsuleDetail }" />
        </div>
        <div class="spacer"></div>
        <button v-if="canManage" type="button" class="ghost" data-testid="template-upload" :disabled="actionBusy || Boolean(conflict)" @click="openUpload">上传新 Word 作为草稿</button>
        <button type="button" class="ghost" :aria-pressed="rightTab === 'versions'" @click="rightTab = 'versions'">版本</button>
        <button v-if="canPublish" type="button" class="primary" data-testid="template-publish" :disabled="Boolean(publishBlock) || actionBusy" :title="publishBlock" @click="openPublish">{{ actionBusy ? '正在准备…' : '发布新版本' }}</button>
      </header>
      <div class="toolbar" role="toolbar" aria-label="模板编辑工具">
        <label class="tool-label" for="tpl-style">段落样式</label>
        <select id="tpl-style" class="control style-select" data-testid="tpl-style-select" :value="styleValue" :disabled="styleLocked" :title="styleTitle" @change="setStyle">
          <option v-if="!selected" value="" disabled>先选择一段</option>
          <option v-if="styleValue === 'keep'" value="keep" disabled>{{ selected?.styleName || '原有样式' }}（保留原样）</option>
          <option v-if="styleValue === '表格正文'" value="表格正文" disabled>表格正文</option>
          <option v-if="styleValue === '编写指导'" value="编写指导" disabled>编写指导（不进 Word）</option>
          <option value="正文">正文</option>
          <option v-for="level in 6" :key="level" :value="`标题 ${level}`">标题 {{ level }}</option>
        </select>
        <span class="sep" aria-hidden="true"></span>
        <button type="button" class="subtle sm" :disabled="readonly" data-testid="tpl-insert-body" @click="insert('body')">插入段落</button>
        <button type="button" class="subtle sm" :disabled="readonly" data-testid="tpl-insert-heading" @click="insert('heading')">插入标题</button>
        <button type="button" class="subtle sm" :disabled="readonly" data-testid="tpl-insert-guidance" @click="insert('guidance')">插入编写指导</button>
        <span class="sep" aria-hidden="true"></span>
        <button type="button" class="subtle sm" :disabled="readonly || !selected" data-testid="tpl-move-up" @click="move(-1)">上移</button>
        <button type="button" class="subtle sm" :disabled="readonly || !selected" data-testid="tpl-move-down" @click="move(1)">下移</button>
        <button type="button" class="subtle sm" :disabled="readonly || !selected" data-testid="tpl-delete" @click="remove">{{ selected?.kind === 'heading' ? '删除本章' : '删除' }}</button>
        <span class="spacer"></span>
        <span v-if="!canManage" class="hint">你只能查看模板。</span>
      </div>
      <p v-if="conflict" class="notice" role="alert" data-testid="tpl-conflict">{{ conflict }} <button type="button" class="text sm" @click="load()">刷新</button> <button type="button" class="text sm" data-testid="tpl-conflict-download" @click="downloadMine">下载我的修改</button></p>
      <p v-else-if="saveError" class="notice" role="alert">{{ saveError }}</p>
      <div v-if="stale" class="notice warning stale" role="status" data-testid="tpl-stale">
        <span>{{ stale.publishedBy?.name || '其他人' }} 已发布第 {{ stale.seq }} 版。可以把你的草稿合并到最新版本后再发布。</span>
        <template v-if="canManage">
          <button type="button" class="text sm" data-testid="tpl-stale-rebase" :disabled="uploaded" :title="uploaded ? '这份草稿来自上传的新 Word，不能自动合并；请基于最新版本重新上传。' : ''" @click="openRebase">合并到最新版本</button>
          <button type="button" class="text sm" @click="compareDraft">对比我的草稿与第 {{ stale.seq }} 版</button>
          <button type="button" class="text sm" data-testid="tpl-stale-discard" @click="discard">放弃草稿，基于第 {{ stale.seq }} 版重新编辑</button>
        </template>
      </div>
      <p v-if="notice" class="notice" :class="notice.kind" role="status" data-testid="tpl-notice">{{ notice.text }} <button type="button" class="text sm" aria-label="关闭提示" @click="notice = null">知道了</button></p>
      <p v-if="alignment" class="notice info" role="status" data-testid="tpl-alignment">已对齐 {{ alignment.matched }} 段；<template v-if="unplaced.length">{{ unplaced.length }} 条编写指导没有找到位置，请逐条指定。</template><template v-else>编写指导都已就位，核对后可以发布。</template></p>
      <div class="workspace">
        <nav class="outline" aria-label="模板目录">
          <h2>模板目录</h2>
          <button v-for="heading in headings" :key="heading.id" type="button" class="outline-item" :class="{ on: selectedId === heading.id }" :style="{ paddingLeft: `calc(var(--space-3) + var(--space-4) * ${(heading.headingLevel ?? 1) - 1})` }" @click="locate(heading.id)">
            <span class="outline-text">{{ heading.text || '（空标题）' }}</span>
            <span v-if="guidanceCount[heading.id]" class="count" :title="`${guidanceCount[heading.id]} 条编写指导`">{{ guidanceCount[heading.id] }}</span>
          </button>
        </nav>
        <div class="desk">
          <section v-if="unplaced.length" class="unplaced" data-testid="tpl-unplaced" aria-label="没有找到位置的编写指导">
            <h2>没有找到位置的编写指导（{{ unplaced.length }}）</h2>
            <div v-for="block in unplaced" :key="block.id" class="unplaced-item" data-testid="tpl-unplaced-item">
              <p>{{ block.text }}</p>
              <div class="unplaced-ops">
                <template v-if="placing === block.id">
                  <select class="control" :aria-label="`指定“${block.text.slice(0, 10)}”所属章节`" data-testid="tpl-unplaced-heading" @change="place(block, ($event.target as HTMLSelectElement).value)">
                    <option value="">选择章节…</option>
                    <option v-for="heading in headings" :key="heading.id" :value="heading.id">{{ '　'.repeat((heading.headingLevel ?? 1) - 1) }}{{ heading.text }}</option>
                  </select>
                  <button type="button" class="text sm" @click="placing = ''">取消</button>
                </template>
                <template v-else-if="!readonly">
                  <button type="button" class="text sm" data-testid="tpl-unplaced-place" @click="placing = block.id">指定位置</button>
                  <button type="button" class="text sm danger" data-testid="tpl-unplaced-drop" @click="dropUnplaced(block)">删除</button>
                </template>
              </div>
            </div>
          </section>
          <article class="paper" aria-label="模板内容">
            <div v-for="block in topBlocks" :key="block.id" class="blk" :class="[block.kind, { on: selectedId === block.id, hit: highlight.includes(block.id), bad: blockErrors[block.id] }]"
              :data-block-id="block.id" :data-kind="block.kind" tabindex="0" role="button" :aria-pressed="selectedId === block.id" @click="select(block.id)" @keydown="onBlockKey($event, block)">
              <div v-if="block.lock?.delete || block.lock?.text || block.kind === 'toc' || block.kind !== 'body'" class="blk-meta">
                <span v-if="block.kind !== 'body' && block.kind !== 'guidance' && block.kind !== 'toc'" class="kind">{{ kindLabel(block) }}</span>
                <span v-if="block.kind === 'toc'" class="tag plain lock-tag"><svg class="ico" viewBox="0 0 16 16" aria-hidden="true"><rect x="3.5" y="7.5" width="9" height="6" rx="1" /><path d="M5.5 7.5V5.5a2.5 2.5 0 0 1 5 0v2" /></svg>目录（保留原样，导出后在 Word 中更新域）</span>
                <span v-else-if="!block.isTable && (block.lock?.delete || block.lock?.text)" class="tag plain lock-tag" :title="block.kind === 'heading' ? '此标题含书签或域，可以改级别，不能删除；需要删除请在 Word 中修改后上传为新草稿。' : (block.lock?.text || block.lock?.delete || '')"><svg class="ico" viewBox="0 0 16 16" aria-hidden="true"><rect x="3.5" y="7.5" width="9" height="6" rx="1" /><path d="M5.5 7.5V5.5a2.5 2.5 0 0 1 5 0v2" /></svg>{{ lockTag(block.lock?.text || block.lock?.delete) }}</span>
                <span v-if="!block.path && block.kind !== 'guidance'" class="tag info">新增</span>
              </div>
              <component :is="`h${Math.min(6, (block.headingLevel ?? 1) + 1)}`" v-if="block.kind === 'heading'" class="blk-heading" :class="`lv${block.headingLevel}`">{{ block.text || '（空标题）' }}</component>
              <div v-else-if="block.kind === 'guidance'" class="guide"><span class="guide-label">编写指导</span><span v-if="block.headingId" class="hint">· {{ headingText(block.headingId) }}</span><p>{{ block.text }}</p></div>
              <TemplateTableGrid v-else-if="block.isTable" :block="block" :readonly="readonly" @change="setCells(block, $event)" @upload="openUpload" />
              <p v-else class="blk-text">{{ block.text || '（空段落）' }}</p>
              <p v-if="blockErrors[block.id]" class="field-error" role="alert">{{ blockErrors[block.id] }}</p>
            </div>
          </article>
        </div>
        <aside class="side">
          <div class="tabs side-tabs" role="tablist" aria-label="右侧面板">
            <button type="button" role="tab" :aria-selected="rightTab === 'edit'" :class="{ on: rightTab === 'edit' }" @click="rightTab = 'edit'">编辑</button>
            <button type="button" role="tab" :aria-selected="rightTab === 'format'" :class="{ on: rightTab === 'format' }" data-testid="tpl-tab-format" @click="rightTab = 'format'">格式</button>
            <button type="button" role="tab" :aria-selected="rightTab === 'versions'" :class="{ on: rightTab === 'versions' }" data-testid="tpl-tab-versions" @click="rightTab = 'versions'">版本</button>
          </div>
          <div class="side-body">
            <template v-if="rightTab === 'edit'">
              <StatePanel v-if="!selected" title="选择一段内容" text="在中间点击标题、段落、指导或表格，在这里修改。" />
              <form v-else class="edit-form" data-testid="tpl-edit-form" @submit.prevent>
                <p class="edit-head"><strong>{{ kindLabel(selected) }}</strong><span v-if="!selected.path && !selected.virtual" class="tag info">新增</span></p>
                <template v-if="selected.isTable">
                  <p class="hint">表格请直接在中间修改单元格文字。增删行列、合并单元格请在 Word 中修改后上传为新草稿。</p>
                  <ul v-if="selected.tableEditReasons?.length" class="reasons"><li v-for="(reason, index) in selected.tableEditReasons" :key="index">{{ reason.message }}</li></ul>
                </template>
                <template v-else>
                  <label v-if="kindOptions.length" class="field"><span>类型</span>
                    <select class="control" :value="selected.kind" :disabled="readonly" data-testid="tpl-edit-kind" @change="setKind">
                      <option v-for="option in kindOptions" :key="option.kind" :value="option.kind" :disabled="option.disabled">{{ option.label }}</option>
                    </select></label>
                  <label v-if="selected.kind === 'heading'" class="field"><span>标题级别</span>
                    <select class="control" :value="selected.headingLevel ?? 1" :disabled="readonly" data-testid="tpl-edit-level" @change="setLevel">
                      <option v-for="level in 6" :key="level" :value="level">标题 {{ level }}</option>
                    </select></label>
                  <label v-if="selected.kind === 'guidance'" class="field"><span>所属章节</span>
                    <select class="control" :value="selected.headingId ?? ''" :disabled="readonly" @change="setGuidanceHeading">
                      <option value="">不绑定章节</option>
                      <option v-for="heading in headings" :key="heading.id" :value="heading.id">{{ '　'.repeat((heading.headingLevel ?? 1) - 1) }}{{ heading.text }}</option>
                    </select></label>
                  <label class="field"><span>文字</span>
                    <textarea id="tpl-block-text" class="control" rows="8" :value="selected.text" :disabled="readonly || textLocked || selected.kind === 'toc'" data-testid="tpl-edit-text" @input="setText"></textarea>
                    <span v-if="textLocked" class="hint">{{ selected.lock?.text }}</span>
                    <span v-else-if="selected.kind === 'guidance'" class="hint">编写指导只在编写时显示，不进最终 Word 正文。</span>
                  </label>
                  <p v-if="selected.lock?.delete && selected.kind !== 'toc'" class="hint">{{ selected.kind === 'heading' ? '此标题含书签或域，可以改级别，不能删除；需要删除请在 Word 中修改后上传为新草稿。' : selected.lock.delete }}</p>
                </template>
              </form>
            </template>
            <TemplateFormatPanel v-else-if="rightTab === 'format'" :document-id="documentId" :styles="styles" :base-styles="baseStyles" :readonly="readonly" @change="setStyleValue" @locate="showImpact" />
            <TemplateVersionsPanel v-else-if="versions" :document-id="documentId" :versions="versions" :can-manage="canManage && !conflict" :has-draft="hasDraft" :busy="actionBusy" @view="viewing = $event" @compare="compareVersion" @restore="restore" />
          </div>
        </aside>
      </div>
    </template>
    <TemplatePublishDialog v-if="dialog === 'publish' && current" :document-id="documentId" :draft-revision="draftRevision" :current-version-id="current.versionId" :current-seq="currentSeq"
      :usage="versions?.usage ?? null" :note-prefill="notePrefill" @close="dialog = ''" @published="published" @stale="onStale" @locate="(id) => { dialog = ''; locate(id); }" @errors="markErrors" />
    <TemplateRebaseDialog v-if="dialog === 'rebase'" :document-id="documentId" :draft-revision="draftRevision" @close="dialog = ''" @merged="merged" @refresh="() => { dialog = ''; load(); }" />
    <TemplateUploadDialog v-if="dialog === 'upload'" :document-id="documentId" :draft-revision="draftRevision" :has-draft="hasDraft" @close="dialog = ''" @uploaded="uploadedDraft" />
    <TemplateCompareDialog v-if="compare" :document-id="documentId" v-bind="compare" @close="compare = null" @locate="(id) => { compare = null; locate(id); }" />
    <TemplateVersionViewDialog v-if="viewing" :document-id="documentId" :version-id="viewing.versionId" :seq="viewing.seq" @close="viewing = null" />
    <dialog v-if="confirmBox" ref="confirmEl" class="app-dialog confirm" aria-labelledby="tpl-confirm-title" data-testid="tpl-confirm" @cancel.prevent="!confirmBusy && (confirmBox = null)">
      <header class="dialog-head"><h2 id="tpl-confirm-title">{{ confirmBox.title }}</h2></header>
      <div class="dialog-body"><p class="dialog-text">{{ confirmBox.text }}</p></div>
      <footer class="dialog-actions">
        <button type="button" class="ghost" data-confirm-cancel :disabled="confirmBusy" @click="confirmBox = null">取消</button>
        <button type="button" :class="confirmBox.danger ? 'danger' : 'primary'" :disabled="confirmBusy" data-testid="tpl-confirm-ok" @click="runConfirm">{{ confirmBusy ? '正在处理…' : confirmBox.action }}</button>
      </footer>
    </dialog>
  </section>
</template>

<style scoped>
.tpl-editor { display:flex; flex-direction:column; height:calc(100vh - var(--topbar-h)); min-height:0; overflow:hidden; background:var(--bg-surface); }
.docbar { display:flex; flex:none; align-items:center; gap:var(--space-2); height:52px; padding:0 var(--space-4) 0 var(--space-2); border-bottom:1px solid var(--border-subtle); }
.doctitle { display:flex; align-items:center; gap:var(--space-2); min-width:0; }
.doctitle h1 { overflow:hidden; max-width:calc(var(--space-7) * 10); margin:0; font-size:var(--text-lg); font-weight:var(--weight-semibold); text-overflow:ellipsis; white-space:nowrap; }
.spacer { flex:1; }
.icon-btn { width:var(--control-h); padding:0; }
.toolbar { display:flex; flex:none; align-items:center; gap:var(--space-2); height:44px; padding:0 var(--space-4); border-bottom:1px solid var(--border-subtle); background:var(--bg-subtle); }
.tool-label { color:var(--text-secondary); font-size:var(--text-xs); }
.style-select { width:200px; }
.sep { width:1px; height:20px; background:var(--border-subtle); }
.notice { flex:none; display:flex; align-items:center; gap:var(--space-2); flex-wrap:wrap; }
.workspace { display:grid; flex:1; grid-template-columns:264px minmax(0, 1fr) var(--panel-w); min-height:0; }
.outline { min-height:0; overflow:auto; padding:var(--space-3) 0; border-right:1px solid var(--border-subtle); }
.outline h2 { margin:0 0 var(--space-2); padding:0 var(--space-4); color:var(--text-tertiary); font-size:var(--text-xs); font-weight:var(--weight-medium); }
.outline-item { display:flex; align-items:center; gap:var(--space-2); width:100%; min-height:32px; padding-right:var(--space-3); border:0; background:transparent; color:var(--text-secondary); font:inherit; font-size:var(--text-sm); text-align:left; cursor:pointer; }
.outline-item:hover { background:var(--bg-hover); }
.outline-item.on { background:var(--brand-50); color:var(--brand-700); }
.outline-text { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.desk { min-height:0; overflow:auto; padding:var(--space-5) var(--space-5) var(--space-7); background:var(--bg-sunken); }
.paper { display:grid; gap:2px; width:var(--paper-w); max-width:100%; margin:0 auto; padding:var(--space-7) var(--space-7); background:var(--bg-surface); box-shadow:var(--shadow-1); }
.blk { position:relative; padding:var(--space-1) var(--space-2); border-left:2px solid transparent; border-radius:var(--radius-control); cursor:pointer; }
.blk:hover { background:var(--bg-hover); }
.blk:focus-visible { outline:var(--focus-outline); outline-offset:1px; }
.blk.on { border-left-color:var(--brand-600); background:var(--brand-50); }
.blk.hit { background:var(--warning-50); }
.blk.bad { border-left-color:var(--danger-600); }
.blk-meta { display:flex; align-items:center; gap:var(--space-2); color:var(--text-tertiary); font-size:var(--text-xs); }
.lock-tag { gap:var(--space-1); }
.lock-tag .ico { width:12px; height:12px; }
.blk-heading { margin:var(--space-2) 0 var(--space-1); font-weight:var(--weight-semibold); line-height:var(--leading-md); }
.blk-heading.lv1 { font-size:var(--text-xl); }
.blk-heading.lv2 { font-size:var(--text-lg); }
.blk-heading.lv3, .blk-heading.lv4, .blk-heading.lv5, .blk-heading.lv6 { font-size:var(--text-md); }
.blk-text { margin:0; font-size:var(--text-md); line-height:var(--leading-md); white-space:pre-wrap; overflow-wrap:anywhere; }
.blk.toc .blk-text, .blk.cover .blk-text { color:var(--text-secondary); }
.guide { padding:var(--space-2) var(--space-3); border-radius:var(--radius-control); background:var(--info-50); }
.guide p { margin:var(--space-1) 0 0; color:var(--text-primary); font-size:var(--text-sm); white-space:pre-wrap; overflow-wrap:anywhere; }
.guide-label { color:var(--info-600); font-size:var(--text-xs); font-weight:var(--weight-medium); }
.unplaced { width:var(--paper-w); max-width:100%; margin:0 auto var(--space-4); padding:var(--space-4); border:1px solid var(--warning-600); border-radius:var(--radius-control); background:var(--warning-50); }
.unplaced h2 { margin:0 0 var(--space-2); font-size:var(--text-sm); font-weight:var(--weight-semibold); }
.unplaced-item { display:flex; align-items:flex-start; gap:var(--space-3); padding:var(--space-2) 0; border-top:1px solid var(--border-subtle); }
.unplaced-item p { flex:1; margin:0; font-size:var(--text-sm); overflow-wrap:anywhere; }
.unplaced-ops { display:flex; flex:none; align-items:center; gap:var(--space-2); }
.unplaced-ops .control { width:220px; }
.side { display:flex; flex-direction:column; min-height:0; border-left:1px solid var(--border-subtle); }
.side-tabs { flex:none; padding:0 var(--space-4); }
.side-body { flex:1; min-height:0; overflow:auto; }
.edit-form { display:grid; gap:var(--space-4); padding:var(--space-4); }
.edit-form .control { width:100%; }
.edit-form textarea { resize:vertical; }
.edit-head { display:flex; align-items:center; gap:var(--space-2); margin:0; }
.reasons { margin:0; padding-left:var(--space-5); color:var(--text-secondary); font-size:var(--text-sm); }
</style>
