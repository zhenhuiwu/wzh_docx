<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute } from "vue-router";
import { uploadFile } from "../api/files";
import { getTemplate, listTemplateDocuments, createTemplateDocument, deleteTemplateDocument,
  parseTemplateWord, getTemplateDocument, saveTemplateStructure,
  type TemplateRecord, type TemplateDocument, type TemplateImportStructure, templateStatusLabel } from "../api/templates";
import TemplateImportEditor from "../components/TemplateImportEditor.vue";

const route = useRoute();
const templateId = computed(() => String(route.params.id ?? ""));
const template = ref<TemplateRecord | null>(null);
const documents = ref<TemplateDocument[]>([]);
const phase = ref<"loading" | "ready" | "error">("loading");
const errorText = ref("");
const adding = ref(false);
const busy = ref(false);
const docName = ref("");
const docDescription = ref("");
const docFile = ref<File | null>(null);
const wordFileId = ref("");
const structure = ref<TemplateImportStructure | null>(null);
const editingId = ref("");
const dirty = ref(false);
const formError = ref("");
const notice = ref("");
let loadNumber = 0;

function messageOf(error: unknown) {
  return error instanceof Error ? error.message : "暂时不能连接服务。";
}

async function load() {
  const target = templateId.value;
  const request = ++loadNumber;
  phase.value = "loading";
  errorText.value = "";
  try {
    const [record, result] = await Promise.all([getTemplate(target), listTemplateDocuments(target)]);
    if (request !== loadNumber) return;
    template.value = record;
    documents.value = result.items;
    phase.value = "ready";
  } catch (error) {
    if (request !== loadNumber) return;
    phase.value = "error";
    errorText.value = messageOf(error);
  }
}

function startImport() {
  if (busy.value) return;
  adding.value = true;
  editingId.value = "";
  docName.value = "";
  docDescription.value = "";
  docFile.value = null;
  wordFileId.value = "";
  structure.value = null;
  formError.value = "";
  notice.value = "";
  dirty.value = false;
}

function chooseFile(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0] ?? null;
  docFile.value = file;
  wordFileId.value = "";
  structure.value = null;
  formError.value = "";
  dirty.value = Boolean(file);
  if (file && !docName.value.trim()) docName.value = file.name.replace(/\.docx$/i, "").replace(/^(\[[^\]]*\]\s*)+/, "");
}

async function parseWord() {
  if (busy.value) return;
  formError.value = "";
  if (!docName.value.trim() || docName.value.trim().length > 120) { formError.value = "模板文档名称应为 1–120 个字符。"; return; }
  const file = docFile.value;
  if (!file || !/\.docx$/i.test(file.name)) { formError.value = "请选择 Word .docx 文件。"; return; }
  busy.value = true;
  try {
    if (!wordFileId.value) wordFileId.value = (await uploadFile(file, "template", { idempotencyKey: crypto.randomUUID() })).id;
    structure.value = await parseTemplateWord(wordFileId.value);
    dirty.value = true;
  } catch (error) { formError.value = messageOf(error); }
  finally { busy.value = false; }
}

async function review(doc: TemplateDocument) {
  if (busy.value || (dirty.value && !window.confirm("尚未保存的模板核对会丢失，是否继续？"))) return;
  startImport();
  editingId.value = doc.id;
  docName.value = doc.name;
  docDescription.value = doc.description;
  wordFileId.value = doc.wordFileId;
  busy.value = true;
  try {
    const result = await getTemplateDocument(doc.id);
    if (!result.importStructure) throw new Error("模板内容没有解析完成，请重新导入。");
    structure.value = result.importStructure;
  } catch (error) { formError.value = messageOf(error); }
  finally { busy.value = false; }
}

function retryReview() {
  const doc = documents.value.find(item => item.id === editingId.value);
  if (doc) void review(doc);
}

async function save(value: TemplateImportStructure) {
  if (busy.value) return;
  formError.value = "";
  if (!docName.value.trim() || docName.value.trim().length > 120) { formError.value = "模板文档名称应为 1–120 个字符。"; return; }
  busy.value = true;
  try {
    if (editingId.value) await saveTemplateStructure(editingId.value, value);
    else await createTemplateDocument(templateId.value, {
      name: docName.value.trim(), description: docDescription.value.trim(),
      displayOrder: documents.value.length, wordFileId: wordFileId.value, importStructure: value,
    });
    dirty.value = false;
    adding.value = false;
    structure.value = null;
    notice.value = "模板已保存。";
    await load();
  } catch (error) { formError.value = messageOf(error); }
  finally { busy.value = false; }
}

function cancel() {
  if (busy.value || (dirty.value && !window.confirm("尚未保存的模板核对会丢失，是否取消？"))) return;
  adding.value = false;
  structure.value = null;
  dirty.value = false;
  formError.value = "";
}

async function removeDocument(doc: TemplateDocument) {
  if (busy.value || !window.confirm("确定要从模板组中移除“" + doc.name + "”吗？")) return;
  busy.value = true;
  notice.value = "";
  try { await deleteTemplateDocument(doc.id); await load(); }
  catch (error) { errorText.value = messageOf(error); }
  finally { busy.value = false; }
}

function canLeave() { return !busy.value && (!dirty.value || window.confirm("尚未保存的模板核对会丢失，是否离开？")); }
function beforeUnload(event: BeforeUnloadEvent) { if (dirty.value || busy.value) { event.preventDefault(); event.returnValue = ""; } }
onBeforeRouteLeave(canLeave);
onBeforeRouteUpdate(canLeave);
watch(templateId, () => { startImport(); adding.value = false; void load(); });
onMounted(() => { void load(); window.addEventListener("beforeunload", beforeUnload); });
onBeforeUnmount(() => { ++loadNumber; window.removeEventListener("beforeunload", beforeUnload); });
</script>

<template>
  <section class="card template-detail">
    <header class="head">
      <h1>{{ template?.name || '模板详情' }}</h1>
      <button v-if="!adding && phase === 'ready'" class="primary" type="button" :disabled="busy" @click="startImport">导入 Word 模板</button>
      <router-link class="ghost" :to="{ name: 'templates' }">返回模板管理</router-link>
    </header>
    <p v-if="phase === 'loading'" class="empty" role="status">正在读取模板…</p>
    <div v-else-if="phase === 'error'" class="empty" role="alert"><p>{{ errorText }}</p><button type="button" class="ghost" @click="load">重试</button></div>
    <template v-else-if="template">
      <p v-if="notice" class="saved-notice" role="status">{{ notice }}</p>
      <p v-if="errorText" class="field-error detail-error" role="alert">{{ errorText }}</p>
      <template v-if="adding">
        <form v-if="!editingId" class="import-form" @submit.prevent="parseWord">
          <label>模板文档名称<input v-model="docName" type="text" class="control" :disabled="busy" @input="dirty = true" /></label>
          <label>Word 文件<input type="file" accept=".docx" class="control" :disabled="busy || Boolean(structure)" @change="chooseFile" /></label>
          <button v-if="!structure" class="primary" type="submit" :disabled="busy">{{ busy ? '正在解析…' : '解析并核对' }}</button>
          <button v-if="!structure" class="ghost" type="button" :disabled="busy" @click="cancel">取消</button>
        </form>
        <div v-else class="editing-name"><h2>{{ docName }}</h2><p v-if="busy && !structure" role="status">正在读取模板内容…</p></div>
        <p v-if="formError && !structure" class="field-error detail-error" role="alert">{{ formError }}</p>
        <button v-if="editingId && !structure && !busy" type="button" class="ghost retry-review" @click="retryReview">重新读取</button>
        <TemplateImportEditor v-if="structure" :key="wordFileId + ':' + structure.revision" :structure="structure" :busy="busy" :error="formError" :save-label="editingId ? '保存核对' : '确认并保存模板'" @save="save" @cancel="cancel" @change="dirty = true" />
      </template>
      <template v-else>
        <div class="template-meta"><span>{{ template.category }}</span><span>{{ templateStatusLabel[template.status] }}</span><p v-if="template.description">{{ template.description }}</p></div>
        <p v-if="documents.length === 0" class="empty">还没有模板文档。请导入 Word 模板。</p>
        <div v-else class="table-wrap"><table><thead><tr><th>模板文档</th><th>内容核对</th><th><span class="sr">操作</span></th></tr></thead><tbody><tr v-for="doc in documents" :key="doc.id"><td>{{ doc.name }}</td><td>{{ doc.importStructure?.reviewed ? '已核对' : '待核对' }}</td><td class="doc-actions"><button type="button" class="ghost" :disabled="busy" @click="review(doc)">核对模板</button><button type="button" class="ghost" :disabled="busy" @click="removeDocument(doc)">移除</button></td></tr></tbody></table></div>
      </template>
    </template>
  </section>
</template>

<style scoped>
.template-detail { min-height:0; height:calc(100vh - var(--space-7) - var(--space-3) - var(--space-5) * 2); max-height:calc(100vh - var(--space-7) - var(--space-3) - var(--space-5) * 2); }
.import-form { display:flex; align-items:flex-end; gap:var(--space-4); padding:var(--space-4); }
.import-form label { display:grid; flex:1; min-width:0; gap:var(--space-2); font-weight:600; }
.import-form .control { width:100%; }
.template-meta { display:flex; align-items:center; gap:var(--space-5); padding:var(--space-4); border-bottom:1px solid var(--rule); color:var(--ink-secondary); }
.template-meta p { margin:0; overflow-wrap:anywhere; }
.saved-notice { margin:0; padding:var(--space-3) var(--space-4); color:var(--ok); background:var(--ok-wash); }
.detail-error { margin:0; padding:var(--space-3) var(--space-4); }
.editing-name { padding:var(--space-4); }
.editing-name h2 { margin:0; font-size:var(--text-md); overflow-wrap:anywhere; }
.doc-actions { display:flex; justify-content:flex-end; gap:var(--space-2); }
.retry-review { align-self:flex-start; margin:var(--space-4); }
</style>
