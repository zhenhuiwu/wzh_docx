<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref } from "vue";
import { generateDocumentDiagram, insertDocumentDiagram, listDocumentDiagrams, type DocumentDiagram } from "../api/documentDiagrams";
import type { DocumentContentResult, DocumentRecord } from "../api/documents";
import { ApiError } from "../api/client";

const props = defineProps<{ document: DocumentRecord; headings: { text: string }[]; headingIndex: number | null }>();
const emit = defineEmits<{ inserted: [DocumentContentResult & { formatTemplate: Record<string, any> }]; busy: [boolean]; conflict: [ApiError] }>();
const dialog = ref<HTMLDialogElement | null>(null);
const instructionInput = ref<HTMLTextAreaElement | null>(null);
const items = ref<DocumentDiagram[]>([]);
const selected = ref<DocumentDiagram | null>(null);
const phase = ref<"loading" | "ready" | "error">("loading");
const error = ref("");
const imageError = ref(false);
const generating = ref(false);
const inserting = ref(false);
const kind = ref<DocumentDiagram["kind"]>("flowchart");
const scope = ref("");
const instruction = ref("");
const alreadyInserted = computed(() => Boolean(selected.value && props.document.markdownContent.includes(`docx-image:diagram-${selected.value.id}`)));
const outdated = computed(() => Boolean(selected.value && selected.value.sourceRevision !== props.document.revision && !alreadyInserted.value));
let controller: AbortController | undefined;
const target = () => scope.value === "" ? null : Number(scope.value);
const message = (value: unknown) => value instanceof Error ? value.message : "暂时不能完成操作。";
async function load() {
  phase.value = "loading";
  error.value = "";
  try {
    items.value = await listDocumentDiagrams(props.document.id);
    selected.value = items.value[0] || null;
    if (selected.value) kind.value = selected.value.kind;
    imageError.value = false;
    phase.value = "ready";
  } catch (value) { error.value = message(value); phase.value = "error"; }
}
async function open() {
  scope.value = props.headingIndex === null ? "" : String(props.headingIndex);
  dialog.value?.showModal();
  await load();
  await nextTick();
  instructionInput.value?.focus();
}
function close() {
  if (inserting.value) return;
  controller?.abort();
  dialog.value?.close();
}
async function generate() {
  if (generating.value || inserting.value) return;
  error.value = "";
  if (!instruction.value.trim()) { error.value = "请填写图表要求。"; instructionInput.value?.focus(); return; }
  const current = new AbortController();
  controller = current;
  generating.value = true;
  emit("busy", true);
  try {
    const result = await generateDocumentDiagram(props.document.id, props.document.revision, target(), kind.value, instruction.value.trim(), current.signal);
    items.value.unshift(result);
    selected.value = result;
    imageError.value = false;
    phase.value = "ready";
  } catch (value) { if (!current.signal.aborted) error.value = message(value); }
  finally { generating.value = false; emit("busy", false); controller = undefined; }
}
async function insert() {
  if (!selected.value || inserting.value || generating.value) return;
  error.value = "";
  inserting.value = true;
  emit("busy", true);
  try {
    // S6：与他人修改按块合并；改到同一段时进入冲突面板。
    const result = await insertDocumentDiagram(props.document.id, selected.value.id, props.document.revision, target(), { allowMerge: true, clientSaveKey: crypto.randomUUID() });
    emit("inserted", result);
    dialog.value?.close();
  } catch (value) {
    error.value = message(value);
    if (value instanceof ApiError && value.code === "OBJECT_CONFLICT") emit("conflict", value);
  }
  finally { inserting.value = false; emit("busy", false); }
}
function select(item: DocumentDiagram) { selected.value = item; kind.value = item.kind; imageError.value = false; }
defineExpose({ open });
onUnmounted(() => controller?.abort());
</script>

<template>
  <dialog ref="dialog" class="diagram-dialog" aria-labelledby="diagram-title" @cancel.prevent="close">
    <header><h2 id="diagram-title">文档图表</h2><button type="button" class="ghost" :disabled="inserting" @click="close">关闭</button></header>
    <div class="diagram-workspace">
      <aside>
        <form @submit.prevent="generate">
          <label for="diagram-kind">图表类型</label>
          <select id="diagram-kind" v-model="kind" class="control" :disabled="generating || inserting"><option value="flowchart">流程图</option><option value="architecture">架构图</option></select>
          <label for="diagram-scope">引用内容与插入位置</label>
          <select id="diagram-scope" v-model="scope" class="control" :disabled="generating || inserting"><option value="">整篇文档 · 末尾插入</option><option v-for="(heading,index) in headings" :key="index" :value="String(index)">{{ heading.text }}</option></select>
          <label for="diagram-instruction">图表要求</label>
          <textarea id="diagram-instruction" ref="instructionInput" v-model="instruction" class="control" rows="5" maxlength="2000" :disabled="generating || inserting"></textarea>
          <button type="submit" class="primary" :disabled="generating || inserting || phase === 'loading'">{{ generating ? '正在生成…' : '生成图表' }}</button>
        </form>
        <nav v-if="items.length" aria-label="已有图表"><button v-for="item in items" :key="item.id" type="button" class="ghost" :aria-current="selected?.id === item.id ? 'true' : undefined" :disabled="generating || inserting" @click="select(item)">{{ item.name }}</button></nav>
      </aside>
      <main>
        <div v-if="phase === 'loading' || generating" class="diagram-state" role="status">{{ generating ? '正在生成 Visio 图表…' : '正在读取图表…' }}</div>
        <div v-else-if="phase === 'error'" class="diagram-state"><p role="alert">{{ error }}</p><button type="button" class="ghost" @click="load">重试</button></div>
        <template v-else-if="selected">
          <h3>{{ selected.name }}</h3>
          <div v-if="imageError" class="diagram-state"><p role="alert">图表预览加载失败。</p><button type="button" class="ghost" @click="load">重试</button></div>
          <div v-else class="diagram-preview"><img :key="selected.id" :src="selected.previewUrl" :alt="selected.name" @error="imageError = true" /></div>
          <p v-if="outdated" class="diagram-error" role="status">这张图依据较早的文档生成，请核对后插入。</p>
          <footer><a class="ghost" :href="selected.downloadUrl" download>下载 Visio</a><button type="button" class="primary" :disabled="inserting || imageError || alreadyInserted" @click="insert">{{ inserting ? '正在插入…' : alreadyInserted ? '已插入正文' : '插入正文' }}</button></footer>
        </template>
        <div v-else class="diagram-state">暂无图表</div>
        <p v-if="error && phase !== 'error'" class="diagram-error" role="alert">{{ error }}</p>
      </main>
    </div>
  </dialog>
</template>

<style scoped>
.diagram-dialog { width:calc(var(--space-7) * 30); height:calc(var(--space-7) * 19); max-width:calc(100vw - var(--space-7) * 2); max-height:calc(100vh - var(--space-7) * 2); padding:0; border:1px solid var(--border-strong); border-radius:var(--radius-sheet); background:var(--bg-surface); color:var(--text-primary); font-family:var(--font); }
.diagram-dialog::backdrop { background:var(--gray-900); opacity:.45; }
header,footer { display:flex; align-items:center; justify-content:space-between; gap:var(--space-3); padding:var(--space-4) var(--space-5); border-bottom:1px solid var(--border-subtle); }
h2,h3 { margin:0; font-size:var(--text-md); overflow-wrap:anywhere; }
.diagram-workspace { display:grid; grid-template-columns:calc(var(--space-7) * 7) minmax(0,1fr); height:calc(100% - var(--space-7) - var(--space-5)); }
aside { padding:var(--space-5); overflow:auto; border-right:1px solid var(--border-subtle); }
form { display:flex; flex-direction:column; gap:var(--space-3); }
label { font-size:var(--text-sm); font-weight:600; }
.control { width:100%; }
textarea { resize:vertical; }
nav { display:flex; flex-direction:column; gap:var(--space-2); margin-top:var(--space-5); border-top:1px solid var(--border-subtle); padding-top:var(--space-4); }
/* 原 visual 分支的全局左对齐规则收回到组件自身 */
nav button { justify-content:flex-start; white-space:normal; text-align:left; overflow-wrap:anywhere; }
[aria-current="true"] { color:var(--brand-600); border-color:var(--brand-600); background:var(--brand-50); }
main { display:flex; flex-direction:column; min-width:0; min-height:0; }
main h3 { padding:var(--space-4) var(--space-5); }
.diagram-preview { flex:1; min-height:0; overflow:auto; padding:var(--space-5); background:var(--bg-page); text-align:center; }
img { max-width:100%; max-height:100%; object-fit:contain; }
.diagram-state { display:flex; flex:1; align-items:center; justify-content:center; flex-direction:column; gap:var(--space-3); padding:var(--space-5); color:var(--text-secondary); }
footer { justify-content:flex-end; border-top:1px solid var(--border-subtle); border-bottom:0; }
.diagram-error { margin:0; padding:var(--space-3) var(--space-5); color:var(--danger-700); font-size:var(--text-sm); overflow-wrap:anywhere; }
</style>
