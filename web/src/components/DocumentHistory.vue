<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import DOMPurify from "dompurify";
import { getDocumentVersion, listDocumentVersions, restoreDocumentVersion, type DocumentContentResult, type DocumentVersion, type DocumentVersionDetail } from "../api/documents";
import { ApiError } from "../api/client";
import { revisionConflictText } from "../saveModel";
import { blockDiff } from "../textDiff";

const props = defineProps<{ documentId: string; revision: number; restoreBlockedReason?: string; current?: string }>();
const emit = defineEmits<{ close: []; restored: [result: DocumentContentResult]; busy: [value: boolean]; refresh: [] }>();
// S6：恢复是严格接口；409 时按 details 说明谁改了，并提供“刷新版本列表”（02 §3.7 第 11 条）。
const staleRestore = ref(false);
const items = ref<DocumentVersion[]>([]);
const offset = ref(0);
const hasMore = ref(false);
const loading = ref(false);
const detailLoading = ref(false);
const restoring = ref(false);
const error = ref("");
const detailError = ref("");
const detail = ref<DocumentVersionDetail | null>(null);
let detailRequest = 0;
/** 终验 S-7：读过的版本缓存起来（版本内容不会再变），来回切换不再等服务器渲染。 */
const detailCache = new Map<number, DocumentVersionDetail>();
const markdownCache = new Map<number, string>();
/** 界面验收 S12：版本间看差异。view=正文 / 与上一版本 / 与当前正文。 */
const view = ref<"content" | "previous" | "current">("content");
const previous = ref<{ revision: number; markdown: string } | null>(null);
const previousMissing = ref(false);
const previousLoading = ref(false);
const previousError = ref("");
let previousRequest = 0;
const comparisonReady = computed(() => view.value === "current" ? props.current !== undefined
  : previous.value?.revision === detail.value?.revision && Boolean(previous.value));
const diffRows = computed(() => {
  if (!detail.value || view.value === "content") return [];
  if (view.value === "current") return blockDiff(detail.value.markdownContent, props.current ?? "");
  return previous.value ? blockDiff(previous.value.markdown, detail.value.markdownContent) : [];
});
const diffChanged = computed(() => diffRows.value.filter(row => row.type !== "eq").length);
/** 只显示改动的块和前后各一块上下文，其余折叠为“… N 段未变 …”。 */
const diffShown = computed(() => {
  const rows = diffRows.value;
  const changed = (index: number) => index >= 0 && index < rows.length && rows[index].type !== "eq";
  const keep = rows.map((_, index) => changed(index) || changed(index - 1) || changed(index + 1));
  const out: Array<{ row?: (typeof rows)[number]; skipped?: number }> = [];
  let skipped = 0;
  rows.forEach((row, index) => {
    if (keep[index]) { if (skipped) out.push({ skipped }); skipped = 0; out.push({ row }); }
    else skipped++;
  });
  if (skipped) out.push({ skipped });
  return out;
});
async function loadPrevious() {
  const current = detail.value;
  // previous.revision 记的是“它是哪一版的上一版本”
  if (!current || previous.value?.revision === current.revision) return;
  const request = ++previousRequest;
  previous.value = null; previousMissing.value = false; previousError.value = "";
  previousLoading.value = false;
  if (current.revision <= 1) { previousMissing.value = true; return; }
  previousLoading.value = true;
  try {
    // 只取 markdown（不渲染 Word 版式），按块比较
    const cached = markdownCache.get(current.revision - 1) ?? detailCache.get(current.revision - 1)?.markdownContent;
    const markdown = cached ?? (await getDocumentVersion(props.documentId, current.revision - 1, false)).markdownContent;
    markdownCache.set(current.revision - 1, markdown);
    if (request === previousRequest && detail.value?.revision === current.revision) previous.value = { revision: current.revision, markdown };
  } catch (e) {
    if (request === previousRequest && detail.value?.revision === current.revision) {
      if (e instanceof ApiError && e.status === 404) previousMissing.value = true;
      else previousError.value = message(e);
    }
  }
  finally { if (request === previousRequest) previousLoading.value = false; }
}
function chooseView(next: "content" | "previous" | "current") {
  view.value = next;
  if (next === "previous") void loadPrevious();
  const current = detail.value;
  if (next === "content" && current && !detailCache.has(current.revision)) void loadHtml(current);
}
async function loadHtml(current: DocumentVersionDetail) {
  const request = ++detailRequest;
  detailLoading.value = true;
  try {
    const result = await getDocumentVersion(props.documentId, current.revision);
    detailCache.set(current.revision, result);
    if (request === detailRequest && detail.value?.revision === current.revision) detail.value = result;
  } catch (e) { if (request === detailRequest) detailError.value = message(e); }
  finally { if (request === detailRequest) detailLoading.value = false; }
}
const html = computed(() => {
  let value = detail.value?.html || "";
  for (const image of detail.value?.formatTemplate?.images || []) {
    value = value.replaceAll(`src="${image.ref}"`, `src="data:${image.content_type};base64,${image.data}"`);
  }
  return DOMPurify.sanitize(value, { FORBID_TAGS: ["iframe", "form", "input", "button", "textarea"] });
});
// 112 之前的版本没有操作人与处数，显示“未记录”（01 §4.5）
const actor = (item: DocumentVersion) => item.createdBy
  ? `${item.createdBy.name}${item.changedObjects ? ` · ${item.changedObjects} 处` : ""}`
  : (item.kind === "baseline" || item.kind === "created" ? "" : "修改人未记录");
const label = (item: DocumentVersion) => ({ created: "新建文档", baseline: "开始记录", edit: "正文修改", restore: "恢复版本", heading: "添加标题", diagram: "插入图表", test_results: "补充测试结果", ai_section: "章节编写", selection: "选区编写", ai_selection: "选区编写", ai_document: "全文编写", ai_undo: "撤回 AI 修改", skill_settings: "编写规范", writing_guidance: "修改编写建议", references: "修改参考资料", conflict_resolution: "处理冲突", backfill: "开始记录", realign: "对齐记录" }[item.kind] || "保存文档");
const time = (value: string) => new Date(value).toLocaleString("zh-CN", { hour12: false });
const message = (e: unknown) => e instanceof Error ? e.message : "读取失败，请重试。";
async function select(item: DocumentVersion) {
  const request = ++detailRequest;
  detailLoading.value = true;
  detailError.value = "";
  detail.value = null;
  ++previousRequest;
  previous.value = null; previousMissing.value = false; previousLoading.value = false; previousError.value = "";
  try {
    // 比较视图只要 markdown：先不渲染 Word 版式；切回“正文”时再补取
    const withHtml = view.value === "content";
    const result = detailCache.get(item.revision) ?? await getDocumentVersion(props.documentId, item.revision, withHtml);
    if (withHtml) detailCache.set(item.revision, result);
    markdownCache.set(item.revision, result.markdownContent);
    if (request === detailRequest) { detail.value = result; if (view.value === "previous") void loadPrevious(); }
  }
  catch (e) { if (request === detailRequest) detailError.value = message(e); }
  finally { if (request === detailRequest) detailLoading.value = false; }
}
async function load(next = 0) {
  loading.value = true; error.value = "";
  try {
    const result = await listDocumentVersions(props.documentId, next);
    items.value = result.items; offset.value = next; hasMore.value = result.hasMore;
    // 列表先显示，正文（需渲染 Word 版式，较慢）随后到
    loading.value = false;
    if (items.value.length) await select(items.value[0]);
    else { detailRequest++; detail.value = null; }
  } catch (e) { error.value = message(e); }
  finally { loading.value = false; }
}
async function restore() {
  if (props.restoreBlockedReason) return;
  if (!detail.value || restoring.value || detail.value.revision === props.revision) return;
  restoring.value = true; emit("busy", true); detailError.value = ""; staleRestore.value = false;
  try { emit("restored", await restoreDocumentVersion(props.documentId, detail.value.revision, props.revision)); }
  catch (e) {
    if (e instanceof ApiError && e.status === 409 && e.code === "REVISION_CONFLICT") {
      staleRestore.value = true;
      detailError.value = revisionConflictText(e.details as Parameters<typeof revisionConflictText>[0]);
    } else detailError.value = message(e);
  }
  finally { restoring.value = false; emit("busy", false); }
}
onMounted(() => void load());
</script>

<template>
  <aside class="history-panel" aria-label="版本记录">
    <header class="phead"><h2>版本记录</h2><button type="button" class="subtle icon-btn" aria-label="收起版本记录" :disabled="restoring" @click="emit('close')"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg></button></header>
    <div class="pbody">
      <p v-if="loading" class="hint" role="status">正在读取版本…</p>
      <div v-else-if="error"><p class="history-error" role="alert">{{ error }}</p><button type="button" class="ghost sm" @click="load(offset)">重试</button></div>
      <p v-else-if="!items.length" class="hint">暂无版本记录</p>
      <template v-else>
        <nav class="version-list" aria-label="选择历史版本">
          <button v-for="item in items" :key="item.revision" type="button" class="vitem" :class="{ on: detail?.revision === item.revision }" :aria-current="detail?.revision === item.revision ? 'true' : undefined" :disabled="restoring || detailLoading" @click="select(item)">
            <span class="vtitle">{{ label(item) }}<span v-if="item.revision === revision" class="tag plain">当前</span></span><time>{{ time(item.createdAt) }}</time><small class="version-actor">{{ actor(item) }}</small>
          </button>
        </nav>
        <nav v-if="offset || hasMore" class="history-pages" aria-label="版本分页"><button type="button" class="ghost sm" :disabled="!offset || loading || restoring" @click="load(offset - 5)">上一页</button><button type="button" class="ghost sm" :disabled="!hasMore || loading || restoring" @click="load(offset + 5)">下一页</button></nav>
        <div v-if="detailLoading" class="skel" role="status" aria-label="正在读取正文…"><span></span><span></span><span></span><span class="short"></span></div>
        <div v-if="detailError"><p class="history-error" role="alert">{{ detailError }}</p><button v-if="!detail" type="button" class="ghost sm" @click="load(offset)">重试</button><button v-else-if="staleRestore" type="button" class="ghost sm" @click="staleRestore = false; detailError = ''; emit('refresh'); load(0)">刷新版本列表</button></div>
        <template v-if="detail">
          <div class="seg" role="group" aria-label="查看方式">
            <button type="button" :class="{ on: view === 'content' }" :aria-pressed="view === 'content'" @click="chooseView('content')">正文</button>
            <button type="button" :class="{ on: view === 'previous' }" :aria-pressed="view === 'previous'" @click="chooseView('previous')">与上一版本比较</button>
            <button type="button" :class="{ on: view === 'current' }" :aria-pressed="view === 'current'" :disabled="current === undefined" @click="chooseView('current')">与当前正文比较</button>
          </div>
          <div v-if="view === 'content'" class="version-preview" aria-label="历史版本正文" v-html="html"></div>
          <div v-else class="version-diff" :aria-label="view === 'previous' ? '与上一版本的差异' : '与当前正文的差异'">
            <div v-if="view === 'previous' && previousLoading" class="skel" role="status" aria-label="正在读取上一版本…"><span></span><span></span><span class="short"></span></div>
            <div v-else-if="view === 'previous' && previousError"><p class="history-error" role="alert">{{ previousError }}</p><button type="button" class="ghost sm" @click="loadPrevious">重试比较</button></div>
            <p v-else-if="view === 'previous' && previousMissing" class="hint">没有更早的版本可以比较。</p>
            <p v-else-if="comparisonReady && !diffChanged" class="hint">{{ view === 'previous' ? '与上一版本相同。' : '与当前正文相同。' }}</p>
            <template v-else-if="comparisonReady">
              <p class="hint">{{ view === 'previous' ? `这一版改动了 ${diffChanged} 段（绿色为新增，红色删除线为删除）。` : `当前正文与这一版相差 ${diffChanged} 段（以这一版为原文）。` }}</p>
              <template v-for="(entry, index) in diffShown" :key="index">
                <p v-if="entry.skipped" class="skip">… {{ entry.skipped }} 段未变 …</p>
                <pre v-else-if="entry.row?.type === 'mod'" class="blk mod"><template v-for="(part, n) in entry.row.parts" :key="n"><ins v-if="part.type === 'ins'">{{ part.text }}</ins><del v-else-if="part.type === 'del'">{{ part.text }}</del><template v-else>{{ part.text }}</template></template></pre>
                <pre v-else class="blk" :class="entry.row?.type">{{ entry.row?.text }}</pre>
              </template>
            </template>
          </div>
          <footer class="history-foot"><p v-if="restoreBlockedReason" class="history-hint">{{ restoreBlockedReason }}</p><button type="button" class="primary" :disabled="restoring || detail.revision === revision || Boolean(restoreBlockedReason)" :title="restoreBlockedReason || undefined" @click="restore">{{ restoring ? '恢复中…' : '恢复此版本' }}</button></footer>
        </template>
      </template>
    </div>
  </aside>
</template>

<style scoped>
/* 第 7/10 步：与冲突面板同一套 .phead/.pbody；只用语义变量 */
.history-panel { display:flex; flex-direction:column; min-height:0; min-width:0; background:var(--bg-surface); border-left:1px solid var(--border-default); font-family:var(--font); font-size:var(--text-sm); }
.ico { width:var(--icon); height:var(--icon); }
.phead { display:flex; align-items:center; gap:var(--space-2); height:var(--etool-h); flex:none; padding:0 var(--space-2) 0 var(--space-4); border-bottom:1px solid var(--border-subtle); }
.phead h2 { margin:0; font-size:var(--text-md); font-weight:var(--weight-semibold); }
.icon-btn { width:var(--control-h-sm); height:var(--control-h-sm); padding:0; margin-left:auto; }
.pbody { flex:1; min-height:0; display:flex; flex-direction:column; gap:var(--space-3); padding:var(--space-4); overflow:hidden; }
.hint { margin:0; color:var(--text-tertiary); font-size:var(--text-xs); line-height:var(--leading-xs); }
.version-list { display:grid; gap:2px; flex:none; }
.vitem { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:2px var(--space-2); padding:var(--space-2) 10px; border:0; border-radius:var(--radius-control); background:transparent; color:var(--text-primary); font-family:var(--font); font-size:var(--text-sm); text-align:left; cursor:pointer; }
.vitem:hover:not(:disabled) { background:var(--bg-hover); }
.vitem.on { background:var(--bg-active); color:var(--brand-700); }
.vitem:disabled { cursor:not-allowed; opacity:.6; }
.vitem:focus-visible { outline:var(--focus-outline); outline-offset:-2px; }
.vtitle { display:inline-flex; align-items:center; gap:var(--space-2); font-weight:var(--weight-medium); }
.vitem time { color:var(--text-tertiary); font-size:var(--text-xs); }
.version-actor { grid-column:1 / -1; color:var(--text-tertiary); font-size:var(--text-xs); }
.history-pages { display:flex; gap:var(--space-2); }
.history-error { margin:0 0 var(--space-2); color:var(--danger-700); overflow-wrap:anywhere; }
.seg { display:grid; grid-template-columns:repeat(3, 1fr); flex:none; padding:2px; background:var(--gray-100); border-radius:var(--radius-control); }
.seg button { height:var(--control-h-sm); padding:0 var(--space-2); border:0; border-radius:calc(var(--radius-control) - 2px); background:transparent; color:var(--text-secondary); font-family:var(--font); font-size:var(--text-xs); white-space:nowrap; cursor:pointer; }
.seg button.on { background:var(--bg-surface); color:var(--text-primary); font-weight:var(--weight-medium); box-shadow:var(--shadow-1); }
.seg button:disabled { cursor:not-allowed; opacity:.45; }
.seg button:focus-visible { outline:var(--focus-outline); outline-offset:1px; }
.version-preview, .version-diff { flex:1; min-height:0; overflow:auto; padding:var(--space-3); border:1px solid var(--border-subtle); border-radius:var(--radius-control); background:var(--bg-subtle); font-family:var(--font-doc); line-height:1.7; overflow-wrap:anywhere; }
.version-diff { display:grid; align-content:start; gap:var(--space-2); font-family:var(--font); }
.version-preview :deep(h1),.version-preview :deep(h2),.version-preview :deep(h3),.version-preview :deep(h4) { font-family:var(--font); font-size:var(--text-md); }
.version-preview :deep(table) { width:100% !important; border-collapse:collapse; table-layout:fixed; }
.version-preview :deep(th),.version-preview :deep(td),.version-preview :deep(col) { width:auto !important; }
.version-preview :deep(th),.version-preview :deep(td) { border:1px solid var(--border-strong); padding:var(--space-1); }
.version-preview :deep([data-word-marker])::before { content:attr(data-word-marker); display:inline-block; margin-right:var(--space-2); }
.version-preview :deep(img) { max-width:100%; height:auto; }
.version-preview :deep(pre) { white-space:pre-wrap; }
.blk { margin:0; padding:var(--space-1) var(--space-2); border-radius:var(--radius-control); white-space:pre-wrap; overflow-wrap:anywhere; font-family:var(--font-doc); font-size:var(--text-sm); line-height:1.7; color:var(--text-secondary); }
.blk.ins { background:var(--diff-ins-bg); color:var(--text-primary); }
.blk.del { background:var(--diff-del-bg); color:var(--danger-700); text-decoration:line-through; }
.blk.mod { background:var(--bg-surface); color:var(--text-primary); box-shadow:inset 3px 0 0 var(--info-200); }
.blk ins { background:var(--diff-ins-bg); text-decoration:none; }
.blk del { background:var(--diff-del-bg); color:var(--danger-700); }
/* 终验 S-7：读取中显示骨架 */
.skel { display:grid; gap:var(--space-2); padding:var(--space-2) 0; }
.skel span { height:var(--space-3); border-radius:var(--radius-control); background:linear-gradient(90deg, var(--gray-100), var(--gray-150), var(--gray-100)); background-size:200% 100%; animation:skel 1.2s ease-in-out infinite; }
.skel span.short { width:60%; }
@keyframes skel { from { background-position:100% 0; } to { background-position:-100% 0; } }
.skip { margin:0; color:var(--text-tertiary); font-size:var(--text-xs); text-align:center; }
.history-foot { display:flex; flex-direction:column; align-items:flex-start; gap:var(--space-2); flex:none; }
.history-hint { margin:0; color:var(--warning-600); font-size:var(--text-xs); }
</style>
