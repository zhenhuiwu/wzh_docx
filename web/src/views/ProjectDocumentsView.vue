<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute } from "vue-router";
import { ApiError, isMissingResource } from "../api/client";
import { documentStatusLabel, listDocuments, createDocument, type DocumentRecord } from "../api/documents";
import StatePanel from "../components/StatePanel.vue";
import { formatDateTime } from "../format";
import { listTemplates, listTemplateDocuments, type TemplateRecord, type TemplateDocument } from "../api/templates";
import { uploadFile } from "../api/files";
import ProjectFrame from "../components/ProjectFrame.vue";
import FilePicker from "../components/FilePicker.vue";
import { useFieldErrors } from "../fieldErrors";

const route = useRoute();
const projectId = computed(() => {
  const id = route.params.id;
  return (Array.isArray(id) ? id[0] : id) ?? "";
});

const documents = ref<DocumentRecord[]>([]);
const phase = ref<"loading" | "ready" | "error">("loading");
const errorText = ref("");
const notFound = ref(false);
const page = ref(1);
const total = ref(0);
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / 20)));

// 创建文档对话框
const creating = ref(false);
const sourceType = ref<"template" | "upload">("template");
const docName = ref("");
const selectedTemplateId = ref("");
const selectedTemplateDocId = ref("");
const uploadedFile = ref<File | null>(null);
const templates = ref<TemplateRecord[]>([]);
const templateDocs = ref<TemplateDocument[]>([]);
const submitting = ref(false);
const formError = ref("");
const fields = useFieldErrors<"name" | "group" | "templateDoc" | "file">("doc");
const nameInput = ref<HTMLInputElement | null>(null);
const createDialog = ref<HTMLDialogElement | null>(null);
type ChoicePhase = "idle" | "loading" | "ready" | "error";
const templatesPhase = ref<ChoicePhase>("idle");
const templateDocsPhase = ref<ChoicePhase>("idle");
const templatesError = ref("");
const templateDocsError = ref("");
let listRequest = 0;
let templatesRequest = 0;
let templateDocsRequest = 0;
const templateReady = computed(() => templatesPhase.value === "ready" && templateDocsPhase.value === "ready"
  && templates.value.some(item => item.id === selectedTemplateId.value)
  && templateDocs.value.some(item => item.id === selectedTemplateDocId.value && item.importStructure?.reviewed));

function messageOf(error: unknown) {
  return error instanceof ApiError ? error.message : "暂时不能连接服务。";
}

async function load() {
  const target = projectId.value;
  const requestedPage = page.value;
  const request = ++listRequest;
  phase.value = "loading";
  errorText.value = "";
  notFound.value = false;
  if (!target) {
    phase.value = "error";
    notFound.value = true;
    errorText.value = "没有这个项目。";
    return;
  }
  try {
    const result = await listDocuments(target, requestedPage);
    if (request !== listRequest || target !== projectId.value) return;
    const lastPage = Math.max(1, Math.ceil(result.total / 20));
    if (requestedPage > lastPage) {
      page.value = lastPage;
      await load();
      return;
    }
    documents.value = result.items;
    total.value = result.total;
    phase.value = "ready";
  } catch (error) {
    if (request !== listRequest || target !== projectId.value) return;
    phase.value = "error";
    notFound.value = isMissingResource(error);
    errorText.value = messageOf(error);
  }
}

async function loadTemplates() {
  const target = projectId.value;
  const request = ++templatesRequest;
  ++templateDocsRequest;
  selectedTemplateId.value = "";
  selectedTemplateDocId.value = "";
  templates.value = [];
  templateDocs.value = [];
  templatesPhase.value = "loading";
  templateDocsPhase.value = "idle";
  templatesError.value = "";
  templateDocsError.value = "";
  try {
    const choices = new Map<string, TemplateRecord>();
    let currentPage = 1;
    let lastPage = 1;
    do {
      const result = await listTemplates(currentPage, "");
      if (request !== templatesRequest || target !== projectId.value || !creating.value) return;
      for (const item of result.items) choices.set(item.id, item);
      lastPage = Math.max(1, Math.ceil(result.total / result.pageSize));
      currentPage += 1;
    } while (currentPage <= lastPage);
    templates.value = [...choices.values()].filter(item => item.status !== "retired");
    templatesPhase.value = "ready";
  } catch (error) {
    if (request !== templatesRequest || target !== projectId.value || !creating.value) return;
    templatesPhase.value = "error";
    templatesError.value = messageOf(error);
  }
}

async function onTemplateChange() {
  const target = projectId.value;
  const templateId = selectedTemplateId.value;
  const request = ++templateDocsRequest;
  selectedTemplateDocId.value = "";
  templateDocs.value = [];
  templateDocsError.value = "";
  templateDocsPhase.value = templateId ? "loading" : "idle";
  if (!templateId) return;
  try {
    const result = await listTemplateDocuments(templateId);
    if (request !== templateDocsRequest || target !== projectId.value || templateId !== selectedTemplateId.value || !creating.value) return;
    templateDocs.value = result.items;
    selectedTemplateDocId.value = result.items.find(item => item.importStructure?.reviewed)?.id ?? "";
    templateDocsPhase.value = "ready";
  } catch (error) {
    if (request !== templateDocsRequest || target !== projectId.value || templateId !== selectedTemplateId.value || !creating.value) return;
    templateDocsPhase.value = "error";
    templateDocsError.value = messageOf(error);
  }
}

function startCreate() {
  if (submitting.value) return;
  creating.value = true;
  sourceType.value = "template";
  docName.value = "";
  selectedTemplateId.value = "";
  selectedTemplateDocId.value = "";
  uploadedFile.value = null;
  formError.value = "";
  fields.clear();
  void loadTemplates();
  void nextTick(() => nameInput.value?.focus());
}

function closeCreate() {
  if (submitting.value) return;
  creating.value = false;
  ++templatesRequest;
  ++templateDocsRequest;
}

async function submitCreate() {
  if (submitting.value) return;
  formError.value = "";
  const input = {
    projectId: projectId.value,
    name: docName.value.trim(),
    sourceType: sourceType.value,
    templateDocumentId: selectedTemplateDocId.value,
    file: uploadedFile.value,
  };

  const fromTemplate = input.sourceType === "template";
  const groupOk = templates.value.some(item => item.id === selectedTemplateId.value);
  if (await fields.report({
    name: !input.name || input.name.length > 120 ? "文档名称应为 1–120 个字符。" : "",
    group: fromTemplate && !groupOk ? "请选择模板组。" : "",
    templateDoc: fromTemplate && groupOk && !templateReady.value ? "请选择已核对的文档模板。" : "",
    file: fromTemplate ? "" : !input.file ? "请选择要上传的 Word 文件。" : !input.file.name.toLowerCase().endsWith(".docx") ? "请选择 .docx 格式的 Word 文件。" : "",
  })) return;

  submitting.value = true;
  try {
    let wordFileId: string | undefined;

    if (input.sourceType === "upload" && input.file) {
      const uploaded = await uploadFile(input.file, "attachment", {
        projectId: input.projectId,
        idempotencyKey: crypto.randomUUID(),
      });
      wordFileId = uploaded.id;
    }

    await createDocument(input.projectId, {
      name: input.name,
      sourceType: input.sourceType,
      templateDocumentId: input.sourceType === "template" ? input.templateDocumentId : undefined,
      wordFileId: wordFileId,
    });

    ++templatesRequest;
    ++templateDocsRequest;
    page.value = 1;
    await load();
    creating.value = false;
  } catch (error) {
    formError.value = messageOf(error);
  } finally {
    submitting.value = false;
  }
}

function canLeave() {
  if (!submitting.value) return true;
  formError.value = "正在创建文档，请稍候。";
  return false;
}

function beforeUnload(event: BeforeUnloadEvent) {
  if (!submitting.value) return;
  event.preventDefault();
  event.returnValue = "";
}

// 新建文档用页面内对话框承载（02 评审 5.8 C3），开关仍由 creating 控制。
watch(creating, async (open) => {
  await nextTick();
  if (open && createDialog.value && !createDialog.value.open) createDialog.value.showModal();
  if (open) nameInput.value?.focus();
  if (!open && createDialog.value?.open) createDialog.value.close();
});

onBeforeRouteLeave(canLeave);
onBeforeRouteUpdate(canLeave);
watch(projectId, () => {
  closeCreate();
  documents.value = [];
  total.value = 0;
  page.value = 1;
  void load();
}, { immediate: true });
onMounted(() => window.addEventListener("beforeunload", beforeUnload));
onBeforeUnmount(() => {
  ++listRequest;
  ++templatesRequest;
  ++templateDocsRequest;
  window.removeEventListener("beforeunload", beforeUnload);
});
</script>

<template>
  <ProjectFrame>
    <section class="document-page">
      <div class="document-head">
        <div>
          <h2 class="section-title">文档编写</h2>
          <p class="page-desc">从模板创建或上传 Word 文档，进入后在线编写。</p>
        </div>
        <button type="button" class="primary" :disabled="phase !== 'ready' || submitting || creating" @click="startCreate"><svg class="ico" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3v10M3 8h10" /></svg>新建文档</button>
      </div>

      <dialog ref="createDialog" class="app-dialog create-dialog" aria-labelledby="create-document-title" @cancel.prevent="closeCreate" @close="creating && closeCreate()">
        <form v-if="creating" class="create-form" :aria-busy="submitting" novalidate @submit.prevent="submitCreate">
          <div class="dialog-head"><h2 id="create-document-title">新建文档</h2><button type="button" class="subtle icon-close" aria-label="关闭新建文档" :disabled="submitting" @click="closeCreate"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg></button></div>
          <div class="dialog-body create-body">
            <div class="source-type" role="radiogroup" aria-label="创建方式">
              <label class="radio-card" :class="{ on: sourceType === 'template' }">
                <input type="radio" v-model="sourceType" value="template" :disabled="submitting" />
                <span><strong>从模板创建</strong><small>使用模板管理中已核对的 Word 模板</small></span>
              </label>
              <label class="radio-card" :class="{ on: sourceType === 'upload' }">
                <input type="radio" v-model="sourceType" value="upload" :disabled="submitting" />
                <span><strong>上传 Word 文档</strong><small>导入已有的 .docx 文件继续编写</small></span>
              </label>
            </div>

            <label class="stacked-label">
              <span class="required">文档名称</span>
              <input id="doc-name" ref="nameInput" v-model="docName" class="control" v-bind="fields.invalid('name')" type="text" maxlength="120" :disabled="submitting" @input="fields.clear('name')" />
              <span v-if="fields.errors.value.name" :id="fields.errorId('name')" class="field-error" role="alert">{{ fields.errors.value.name }}</span>
            </label>

            <div v-if="sourceType === 'template'" class="template-source">
              <label class="stacked-label">
                <span class="required">选择模板组</span>
                <select id="doc-group" v-model="selectedTemplateId" class="control" v-bind="fields.invalid('group')" :disabled="submitting || templatesPhase !== 'ready' || templates.length === 0" @change="fields.clear('group'); fields.clear('templateDoc'); onTemplateChange()">
                  <option value="">请选择</option>
                  <option v-for="t in templates" :key="t.id" :value="t.id">
                    {{ t.name }} ({{ t.category }})
                  </option>
                </select>
                <span v-if="fields.errors.value.group" :id="fields.errorId('group')" class="field-error" role="alert">{{ fields.errors.value.group }}</span>
              </label>
              <p v-if="templatesPhase === 'loading'" class="hint" role="status"><span class="spinner" aria-hidden="true"></span> 正在加载模板组…</p>
              <p v-else-if="templatesPhase === 'error'" class="field-error" role="alert">
                {{ templatesError }} <button type="button" class="text" :disabled="submitting" @click="loadTemplates">重试</button>
              </p>
              <p v-else-if="templatesPhase === 'ready' && templates.length === 0" class="hint">暂无可用模板组</p>

              <label v-if="selectedTemplateId" class="stacked-label">
                <span class="required">选择文档模板</span>
                <select id="doc-templateDoc" v-model="selectedTemplateDocId" class="control" v-bind="fields.invalid('templateDoc')" @change="fields.clear('templateDoc')" :disabled="submitting || templateDocsPhase !== 'ready' || templateDocs.length === 0">
                  <option value="">请选择</option>
                  <option v-for="doc in templateDocs" :key="doc.id" :value="doc.id" :disabled="!doc.importStructure?.reviewed">
                    {{ doc.name }}{{ doc.importStructure?.reviewed ? '' : '（待核对）' }}
                  </option>
                </select>
                <span v-if="fields.errors.value.templateDoc" :id="fields.errorId('templateDoc')" class="field-error" role="alert">{{ fields.errors.value.templateDoc }}</span>
              </label>
              <p v-if="templateDocsPhase === 'loading'" class="hint" role="status"><span class="spinner" aria-hidden="true"></span> 正在加载文档模板…</p>
              <p v-else-if="templateDocsPhase === 'error'" class="field-error" role="alert">
                {{ templateDocsError }} <button type="button" class="text" :disabled="submitting" @click="onTemplateChange">重试</button>
              </p>
              <p v-else-if="selectedTemplateId && templateDocsPhase === 'ready' && templateDocs.length === 0" class="hint">此模板组没有文档模板</p>
              <p v-else-if="selectedTemplateId && templateDocsPhase === 'ready' && !templateDocs.some(doc => doc.importStructure?.reviewed)" class="field-error" role="alert">请先在模板管理中核对并保存模板内容。</p>
            </div>

            <label v-else class="stacked-label">
              <span class="required">上传 Word 文件</span>
              <FilePicker
                id="doc-file"
                accept=".docx"
                empty-text="未选择文件（仅支持 .docx）"
                :file="uploadedFile"
                :disabled="submitting"
                :invalid="Boolean(fields.errors.value.file)"
                v-bind="fields.invalid('file')"
                @change="file => { uploadedFile = file; fields.clear('file'); }"
              />
              <span v-if="fields.errors.value.file" :id="fields.errorId('file')" class="field-error" role="alert">{{ fields.errors.value.file }}</span>
            </label>

            <p v-if="formError" class="form-alert" role="alert">{{ formError }}</p>
          </div>

          <div class="dialog-actions">
            <button type="button" class="ghost" :disabled="submitting" @click="closeCreate">取消</button>
            <button type="submit" class="primary" :disabled="submitting">
              {{ submitting ? "正在创建" : "创建" }}
            </button>
          </div>
        </form>
      </dialog>

      <div class="doc-box">
        <StatePanel v-if="phase === 'loading'" kind="loading" text="正在加载文档…" />
        <StatePanel v-else-if="phase === 'error' && notFound" kind="error" title="项目不存在或你无权访问。"><router-link class="primary" :to="{ name: 'projects' }">返回项目列表</router-link></StatePanel>
        <StatePanel v-else-if="phase === 'error'" kind="error" title="文档加载失败" :text="errorText"><button type="button" class="ghost" @click="load">重试</button></StatePanel>
        <StatePanel v-else-if="documents.length === 0" title="还没有文档" text="从模板创建第一篇文档，或上传已有的 Word 文档。"><button type="button" class="primary" :disabled="submitting" @click="startCreate">新建文档</button></StatePanel>
        <div v-else class="table-wrap document-list">
          <table>
            <thead><tr><th>文档名称</th><th>来源</th><th>编写人</th><th>状态</th><th>最近修改</th><th class="th-right">操作</th></tr></thead>
            <tbody>
              <tr v-for="doc in documents" :key="doc.id" class="document-row">
                <td class="doc-name-cell"><router-link class="document-name" :to="{ name: 'document', params: { id: projectId, documentId: doc.id } }">{{ doc.name }}</router-link></td>
                <td class="muted">{{ doc.sourceType === "upload" ? "上传 Word" : doc.templateDocumentName || "模板" }}</td>
                <td>{{ doc.ownerName || "—" }}</td>
                <td><span class="tag" :class="{ info: doc.status === 'in_progress', success: doc.status === 'completed' }">{{ documentStatusLabel[doc.status] || doc.status }}</span></td>
                <td class="time num">{{ formatDateTime(doc.updatedAt) }}</td>
                <td class="cell-right"><router-link class="link document-open" :to="{ name: 'document', params: { id: projectId, documentId: doc.id } }" :aria-label="`继续编写：${doc.name}`">继续编写</router-link></td>
              </tr>
            </tbody>
          </table>
        </div>

        <div v-if="phase === 'ready' && total > 20" class="pager">
          <span class="total">共 {{ total }} 条</span>
          <button type="button" class="ghost" :disabled="submitting || page <= 1" @click="page -= 1; load()">
            上一页
          </button>
          <button type="button" class="ghost" :disabled="submitting || page >= pageCount" @click="page += 1; load()">
            下一页
          </button>
        </div>
      </div>
    </section>
  </ProjectFrame>
</template>

<style scoped>
.document-page { display:flex; flex:1; flex-direction:column; gap:var(--space-4); min-width:0; padding:var(--space-5) var(--space-6) var(--space-6); }
.document-head { display:flex; align-items:center; justify-content:space-between; gap:var(--space-5); }
.doc-box { border:1px solid var(--border-default); border-radius:var(--radius-sheet); overflow:hidden; }
.doc-name-cell { width:40%; white-space:normal; }
.document-name { color:var(--text-primary); font-weight:var(--weight-medium); text-decoration:none; overflow-wrap:anywhere; }
.document-name:hover { color:var(--text-link); text-decoration:underline; text-underline-offset:3px; }
.document-name:focus-visible { outline:none; border-radius:var(--radius-control); box-shadow:var(--focus-shadow); }
.create-dialog { width:var(--dialog-w-form); }
.create-body { display:grid; gap:var(--space-4); }
.template-source { display:grid; gap:var(--space-3); }
.source-type { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:var(--space-3); }
.radio-card { display:flex; align-items:flex-start; gap:var(--space-3); padding:var(--space-3) var(--space-4); border:1px solid var(--border-strong); border-radius:var(--radius-control); cursor:pointer; }
.radio-card:hover { background:var(--bg-subtle); }
.radio-card.on { border-color:var(--brand-600); background:var(--bg-active); }
.radio-card:focus-within { box-shadow:var(--focus-shadow); }
.radio-card input { margin:var(--space-1) 0 0; }
.radio-card span { display:grid; gap:2px; }
.radio-card strong { font-size:var(--text-md); font-weight:var(--weight-medium); }
.radio-card small { color:var(--text-tertiary); font-size:var(--text-xs); }
</style>
