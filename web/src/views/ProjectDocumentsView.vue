<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute } from "vue-router";
import { ApiError } from "../api/client";
import { listDocuments, createDocument, type DocumentRecord } from "../api/documents";
import { listTemplates, listTemplateDocuments, type TemplateRecord, type TemplateDocument } from "../api/templates";
import { uploadFile } from "../api/files";
import ProjectFrame from "../components/ProjectFrame.vue";

const route = useRoute();
const projectId = computed(() => {
  const id = route.params.id;
  return (Array.isArray(id) ? id[0] : id) ?? "";
});

const documents = ref<DocumentRecord[]>([]);
const phase = ref<"loading" | "ready" | "error">("loading");
const errorText = ref("");
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
const nameInput = ref<HTMLInputElement | null>(null);
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
  && templateDocs.value.some(item => item.id === selectedTemplateDocId.value));

function messageOf(error: unknown) {
  return error instanceof ApiError ? error.message : "暂时不能连接服务。";
}

async function load() {
  const target = projectId.value;
  const requestedPage = page.value;
  const request = ++listRequest;
  phase.value = "loading";
  errorText.value = "";
  if (!target) {
    phase.value = "error";
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
    selectedTemplateDocId.value = result.items[0]?.id ?? "";
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

  if (!input.name || input.name.length > 120) {
    formError.value = "文档名称应为 1–120 个字符。";
    nameInput.value?.focus();
    return;
  }

  if (input.sourceType === "template") {
    if (!templateReady.value) {
      formError.value = "请选择模板文档";
      return;
    }
  } else {
    if (!input.file) {
      formError.value = "请选择要上传的 Word 文件";
      return;
    }
    if (!input.file.name.toLowerCase().endsWith(".docx")) {
      formError.value = "请选择 .docx 格式的 Word 文件。";
      return;
    }
  }

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
        <h2>文档编写</h2>
        <button v-if="!creating" type="button" class="primary" :disabled="phase !== 'ready' || submitting" @click="startCreate">新建文档</button>
      </div>

      <p v-if="phase === 'error'" class="notice" role="alert">{{ errorText }}</p>

      <form v-if="creating" class="create-form" :aria-busy="submitting" @submit.prevent="submitCreate">
        <h3>新建文档</h3>

        <div class="source-type">
          <label class="radio-label">
            <input type="radio" v-model="sourceType" value="template" :disabled="submitting" />
            从模板创建
          </label>
          <label class="radio-label">
            <input type="radio" v-model="sourceType" value="upload" :disabled="submitting" />
            上传 Word 文档
          </label>
        </div>

        <label>
          文档名称
          <input ref="nameInput" v-model="docName" class="control" type="text" maxlength="120" :disabled="submitting" />
        </label>

        <div v-if="sourceType === 'template'" class="template-source">
          <label>
            选择模板组
            <select v-model="selectedTemplateId" class="control" :disabled="submitting || templatesPhase !== 'ready' || templates.length === 0" @change="onTemplateChange">
              <option value="">请选择</option>
              <option v-for="t in templates" :key="t.id" :value="t.id">
                {{ t.name }} ({{ t.category }})
              </option>
            </select>
          </label>
          <p v-if="templatesPhase === 'loading'" class="form-state" role="status">正在加载模板组…</p>
          <p v-else-if="templatesPhase === 'error'" class="field-error" role="alert">
            {{ templatesError }} <button type="button" class="ghost" :disabled="submitting" @click="loadTemplates">重试</button>
          </p>
          <p v-else-if="templatesPhase === 'ready' && templates.length === 0" class="form-state">暂无可用模板组</p>

          <label v-if="selectedTemplateId">
            选择文档模板
            <select v-model="selectedTemplateDocId" class="control" :disabled="submitting || templateDocsPhase !== 'ready' || templateDocs.length === 0">
              <option value="">请选择</option>
              <option v-for="doc in templateDocs" :key="doc.id" :value="doc.id">
                {{ doc.name }}
              </option>
            </select>
          </label>
          <p v-if="templateDocsPhase === 'loading'" class="form-state" role="status">正在加载文档模板…</p>
          <p v-else-if="templateDocsPhase === 'error'" class="field-error" role="alert">
            {{ templateDocsError }} <button type="button" class="ghost" :disabled="submitting" @click="onTemplateChange">重试</button>
          </p>
          <p v-else-if="selectedTemplateId && templateDocsPhase === 'ready' && templateDocs.length === 0" class="form-state">此模板组没有文档模板</p>
        </div>

        <label v-else>
          上传 Word 文件（.docx）
          <input
            class="control"
            type="file"
            accept=".docx"
            :disabled="submitting"
            @change="uploadedFile = ($event.target as HTMLInputElement).files?.[0] ?? null"
          />
        </label>

        <p v-if="formError" class="field-error" role="alert">{{ formError }}</p>

        <div class="actions">
          <button type="submit" class="primary" :disabled="submitting || (sourceType === 'template' && !templateReady)">
            {{ submitting ? "正在创建" : "创建" }}
          </button>
          <button type="button" class="ghost" :disabled="submitting" @click="closeCreate">取消</button>
        </div>
      </form>

      <p v-if="phase === 'loading'" class="empty" role="status">正在加载文档…</p>
      <p v-else-if="phase === 'error'" class="empty">
        <button type="button" class="ghost" @click="load">重试</button>
      </p>
      <p v-else-if="documents.length === 0" class="empty">还没有文档</p>
      <div v-else class="document-list">
        <router-link v-for="doc in documents" :key="doc.id" class="document-row" :to="{ name: 'document', params: { id: projectId, documentId: doc.id } }">
          <span class="document-name">{{ doc.name }}</span>
          <span class="document-open">继续编写 →</span>
        </router-link>
      </div>

      <div v-if="phase === 'ready' && total > 20" class="pager">
        <span>共 {{ total }} 条</span>
        <button type="button" class="ghost" :disabled="submitting || page <= 1" @click="page -= 1; load()">
          上一页
        </button>
        <button type="button" class="ghost" :disabled="submitting || page >= pageCount" @click="page += 1; load()">
          下一页
        </button>
      </div>
    </section>
  </ProjectFrame>
</template>

<style scoped>
.document-page { display:flex; flex:1; flex-direction:column; min-width:0; padding:var(--space-6); }
.document-head { display:flex; align-items:flex-start; justify-content:space-between; gap:var(--space-5); margin-bottom:var(--space-6); }
.document-head h2 { margin:0; font-size:calc(var(--text-md) * 1.25); }
.document-row { display:grid; grid-template-columns:minmax(0,1fr) auto; align-items:center; gap:var(--space-4); min-height:calc(var(--space-7) + var(--space-2)); padding:var(--space-3) var(--space-2); border-bottom:1px solid var(--rule); color:var(--ink); text-decoration:none; }
.document-row:hover { background:var(--desk); }
.document-row:focus-visible { outline:2px solid var(--stamp); outline-offset:-2px; }
.document-name { min-width:0; font-size:var(--text-md); font-weight:600; overflow-wrap:anywhere; }
.document-open { color:var(--stamp); font-size:var(--text-sm); text-align:right; }
.create-form { display:grid; gap:var(--space-4); max-width:calc(var(--space-7) * 14); margin-bottom:var(--space-6); padding:var(--space-5); border:1px solid var(--rule); border-radius:var(--radius-sheet); background:var(--desk); }
.create-form h3 { margin:0; font-size:var(--text-md); }
.create-form label { display:grid; gap:var(--space-1); color:var(--ink-secondary); font-size:var(--text-sm); }
.template-source { display:grid; gap:var(--space-4); }
.form-state { margin:0; color:var(--ink-secondary); font-size:var(--text-sm); }
.source-type,.actions { display:flex; gap:var(--space-4); }
.source-type .radio-label { display:flex; align-items:center; gap:var(--space-2); cursor:pointer; }
.source-type input { accent-color:var(--stamp); }
.document-page .pager { display:flex; align-items:center; justify-content:flex-end; gap:var(--space-2); margin-top:auto; padding:var(--space-3) 0; color:var(--ink-secondary); font-size:var(--text-xs); }
</style>
