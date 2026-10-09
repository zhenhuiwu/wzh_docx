<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import DOMPurify from "dompurify";
import { getDocumentVersion, listDocumentVersions, restoreDocumentVersion, type DocumentContentResult, type DocumentVersion, type DocumentVersionDetail } from "../api/documents";

const props = defineProps<{ documentId: string; revision: number }>();
const emit = defineEmits<{ close: []; restored: [result: DocumentContentResult]; busy: [value: boolean] }>();
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
const html = computed(() => {
  let value = detail.value?.html || "";
  for (const image of detail.value?.formatTemplate?.images || []) {
    value = value.replaceAll(`src="${image.ref}"`, `src="data:${image.content_type};base64,${image.data}"`);
  }
  return DOMPurify.sanitize(value, { FORBID_TAGS: ["iframe", "form", "input", "button", "textarea"] });
});
const label = (item: DocumentVersion) => ({ created: "新建文档", baseline: "开始记录", edit: "正文修改", restore: "恢复版本", heading: "添加标题", diagram: "插入图表", test_results: "补充测试结果", ai_section: "章节编写", selection: "选区编写", ai_selection: "选区编写", ai_document: "全文编写", ai_undo: "撤回 AI 修改", skill_settings: "编写规范" }[item.kind] || "保存文档");
const time = (value: string) => new Date(value).toLocaleString("zh-CN", { hour12: false });
const message = (e: unknown) => e instanceof Error ? e.message : "读取失败，请重试。";
async function select(item: DocumentVersion) {
  const request = ++detailRequest;
  detailLoading.value = true;
  detailError.value = "";
  detail.value = null;
  try { const result = await getDocumentVersion(props.documentId, item.revision); if (request === detailRequest) detail.value = result; }
  catch (e) { if (request === detailRequest) detailError.value = message(e); }
  finally { if (request === detailRequest) detailLoading.value = false; }
}
async function load(next = 0) {
  loading.value = true; error.value = "";
  try {
    const result = await listDocumentVersions(props.documentId, next);
    items.value = result.items; offset.value = next; hasMore.value = result.hasMore;
    if (items.value.length) await select(items.value[0]);
    else { detailRequest++; detail.value = null; }
  } catch (e) { error.value = message(e); }
  finally { loading.value = false; }
}
async function restore() {
  if (!detail.value || restoring.value || detail.value.revision === props.revision) return;
  restoring.value = true; emit("busy", true); detailError.value = "";
  try { emit("restored", await restoreDocumentVersion(props.documentId, detail.value.revision, props.revision)); }
  catch (e) { detailError.value = message(e); }
  finally { restoring.value = false; emit("busy", false); }
}
onMounted(() => void load());
</script>

<template>
  <aside class="history-panel" aria-label="版本记录">
    <header><h2>版本记录</h2><button type="button" class="ghost" :disabled="restoring" @click="emit('close')">收起</button></header>
    <p v-if="loading" role="status">正在读取版本…</p>
    <div v-else-if="error"><p class="history-error" role="alert">{{ error }}</p><button type="button" class="ghost" @click="load(offset)">重试</button></div>
    <p v-else-if="!items.length">暂无版本记录</p>
    <template v-else>
      <nav class="version-list" aria-label="选择历史版本">
        <button v-for="item in items" :key="item.revision" type="button" class="ghost" :class="{ selected: detail?.revision === item.revision }" :disabled="restoring || detailLoading" @click="select(item)">
          <span>{{ time(item.createdAt) }}</span><span>{{ label(item) }}{{ item.revision === revision ? ' · 当前' : '' }}</span>
        </button>
      </nav>
      <nav v-if="offset || hasMore" class="history-pages" aria-label="版本分页"><button type="button" class="ghost" :disabled="!offset || loading || restoring" @click="load(offset - 5)">上一页</button><button type="button" class="ghost" :disabled="!hasMore || loading || restoring" @click="load(offset + 5)">下一页</button></nav>
      <p v-if="detailLoading" role="status">正在读取正文…</p>
      <div v-if="detailError"><p class="history-error" role="alert">{{ detailError }}</p><button v-if="!detail" type="button" class="ghost" @click="load(offset)">重试</button></div>
      <template v-if="detail">
        <div class="version-preview" aria-label="历史版本正文" v-html="html"></div>
        <footer><button type="button" class="primary" :disabled="restoring || detail.revision === revision" @click="restore">{{ restoring ? '恢复中…' : '恢复此版本' }}</button></footer>
      </template>
    </template>
  </aside>
</template>

<style scoped>
.history-panel { display:flex; flex-direction:column; gap:var(--space-3); min-height:0; min-width:0; padding:var(--space-5) var(--space-4); background:var(--sheet); border-left:1px solid var(--rule); font-family:var(--font); font-size:var(--text-sm); }
header { display:flex; align-items:center; justify-content:space-between; gap:var(--space-2); }
h2 { margin:0; font-size:calc(var(--text-md) * 1.25); }
p { margin:0; color:var(--ink-secondary); }
.version-list { display:flex; flex-direction:column; gap:var(--space-1); }
.version-list button { display:flex; justify-content:space-between; flex-wrap:wrap; gap:var(--space-2); text-align:left; height:auto; min-height:var(--space-6); padding:var(--space-2); font-size:var(--text-sm); font-weight:400; }
.version-list .selected { border-color:var(--stamp); color:var(--stamp); background:var(--stamp-wash); }
.history-pages { display:flex; gap:var(--space-2); }
.history-error { color:var(--fault-ink); overflow-wrap:anywhere; }
.version-preview { flex:1; min-height:0; overflow:auto; border-top:1px solid var(--rule); border-bottom:1px solid var(--rule); padding:var(--space-3) 0; overflow-wrap:anywhere; }
.version-preview :deep(table) { width:100% !important; border-collapse:collapse; table-layout:fixed; }
.version-preview :deep(th),.version-preview :deep(td),.version-preview :deep(col) { width:auto !important; }
.version-preview :deep(th),.version-preview :deep(td) { border:1px solid var(--rule-strong); padding:var(--space-1); }
.version-preview :deep([data-word-marker])::before { content:attr(data-word-marker); display:inline-block; margin-right:var(--space-2); }
.version-preview :deep(img) { max-width:100%; height:auto; }
.version-preview :deep(pre) { white-space:pre-wrap; }
button:focus-visible { outline:var(--space-1) solid var(--focus-ring); outline-offset:var(--space-1); }
</style>
