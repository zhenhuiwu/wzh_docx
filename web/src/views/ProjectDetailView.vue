<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute } from "vue-router";
import { ApiError } from "../api/client";
import { getProject, updateProject, type ProjectRecord } from "../api/projects";
import { listDocuments, type DocumentRecord } from "../api/documents";
import ProjectFrame from "../components/ProjectFrame.vue";
import { displayText, projectStatuses, statusLabel } from "../projects";

const route = useRoute();
const projectId = computed(() => String(route.params.id));
const project = ref<ProjectRecord | null>(null);
const documents = ref<DocumentRecord[]>([]);
const phase = ref<"loading" | "ready" | "error">("loading");
const documentPhase = ref<"loading" | "ready" | "error">("loading");
const errorText = ref("");
const documentError = ref("");
const editing = ref(false);
const saving = ref(false);
const formError = ref("");
const form = ref({ name: "", description: "", organization: "", department: "", group: "", clientContact: "", status: "not_started" as ProjectRecord["status"], periodStart: "", periodEnd: "" });
let projectRequest = 0;
let documentRequest = 0;
let saveRequest = 0;

function messageOf(error: unknown) {
  return error instanceof ApiError ? error.message : "暂时不能连接服务。";
}

function fillForm(value: ProjectRecord) {
  form.value = {
    name: value.name, description: value.description, organization: value.organization,
    department: value.department, group: value.group, clientContact: value.clientContact,
    status: value.status, periodStart: value.periodStart ?? "", periodEnd: value.periodEnd ?? "",
  };
}

async function loadDocuments() {
  const target = projectId.value;
  const request = ++documentRequest;
  documentPhase.value = "loading";
  documentError.value = "";
  try {
    const result = await listDocuments(target, 1, "", 5);
    if (request !== documentRequest || target !== projectId.value) return;
    documents.value = result.items;
    documentPhase.value = "ready";
  } catch (error) {
    if (request !== documentRequest || target !== projectId.value) return;
    documentPhase.value = "error";
    documentError.value = messageOf(error);
  }
}

async function load() {
  const target = projectId.value;
  const request = ++projectRequest;
  ++documentRequest;
  phase.value = "loading";
  errorText.value = "";
  project.value = null;
  documents.value = [];
  documentPhase.value = "loading";
  documentError.value = "";
  try {
    const result = await getProject(target);
    if (request !== projectRequest || target !== projectId.value) return;
    project.value = result;
    fillForm(result);
    phase.value = "ready";
    void loadDocuments();
  } catch (error) {
    if (request !== projectRequest || target !== projectId.value) return;
    project.value = null;
    phase.value = "error";
    errorText.value = messageOf(error);
  }
}

async function saveInfo() {
  if (!project.value || saving.value) return;
  const target = project.value.id;
  const input = {
    expectedRevision: project.value.revision,
    name: form.value.name.trim(), description: form.value.description,
    organization: form.value.organization, department: form.value.department,
    group: form.value.group, clientContact: form.value.clientContact,
    status: form.value.status, periodStart: form.value.periodStart || null,
    periodEnd: form.value.periodEnd || null,
  };
  formError.value = "";
  if (!input.name) {
    formError.value = "请填写项目名称。";
    return;
  }
  const request = ++saveRequest;
  saving.value = true;
  try {
    const result = await updateProject(target, input);
    if (request !== saveRequest || target !== projectId.value) return;
    project.value = result;
    fillForm(result);
    editing.value = false;
  } catch (error) {
    if (request !== saveRequest || target !== projectId.value) return;
    formError.value = messageOf(error);
  } finally {
    if (request === saveRequest && target === projectId.value) saving.value = false;
  }
}

function canLeave() {
  if (!saving.value) return true;
  formError.value = "正在保存项目，请稍候。";
  return false;
}

function beforeUnload(event: BeforeUnloadEvent) {
  if (!saving.value) return;
  event.preventDefault();
  event.returnValue = "";
}

onBeforeRouteLeave(canLeave);
onBeforeRouteUpdate(canLeave);
watch(projectId, () => {
  ++saveRequest;
  saving.value = false;
  editing.value = false;
  formError.value = "";
  void load();
}, { immediate: true });
onMounted(() => window.addEventListener("beforeunload", beforeUnload));
onBeforeUnmount(() => {
  ++projectRequest;
  ++documentRequest;
  ++saveRequest;
  window.removeEventListener("beforeunload", beforeUnload);
});
</script>

<template>
  <div v-if="phase === 'loading'" class="page-state" role="status">正在读取项目…</div>
  <div v-else-if="phase === 'error' || !project" class="page-state" role="alert">
    <p>{{ errorText || "没有找到这个项目。" }}</p>
    <button type="button" class="ghost" @click="load">重试</button>
  </div>
  <ProjectFrame v-else :project="project">
    <div class="overview-body">
      <section class="overview-section">
        <div class="section-head">
          <h2>项目概述</h2>
          <button type="button" class="ghost" :disabled="saving" @click="editing = !editing; formError = ''">{{ editing ? "收起编辑" : "编辑项目" }}</button>
        </div>
        <form v-if="editing" class="edit-form" :aria-busy="saving" @submit.prevent="saveInfo">
          <div class="form-grid">
            <label>项目名称<input v-model="form.name" class="control" type="text" required :disabled="saving" /></label>
            <label>状态<select v-model="form.status" class="control" :disabled="saving"><option v-for="item in projectStatuses" :key="item.value" :value="item.value">{{ item.label }}</option></select></label>
            <label>甲方单位<input v-model="form.organization" class="control" type="text" :disabled="saving" /></label>
            <label>甲方联系人<input v-model="form.clientContact" class="control" type="text" :disabled="saving" /></label>
            <label>所属部门<input v-model="form.department" class="control" type="text" :disabled="saving" /></label>
            <label>所属组<input v-model="form.group" class="control" type="text" :disabled="saving" /></label>
            <label>开始日期<input v-model="form.periodStart" class="control" type="date" :disabled="saving" /></label>
            <label>结束日期<input v-model="form.periodEnd" class="control" type="date" :disabled="saving" /></label>
            <label class="wide">项目说明<textarea v-model="form.description" class="control" rows="3" :disabled="saving" /></label>
          </div>
          <p v-if="formError" class="field-error" role="alert">{{ formError }}</p>
          <div class="form-actions"><button type="submit" class="primary" :disabled="saving">{{ saving ? "正在保存…" : "保存项目" }}</button><button type="button" class="ghost" :disabled="saving" @click="editing = false; fillForm(project)">取消</button></div>
        </form>
        <template v-else>
          <p v-if="project.description" class="description">{{ project.description }}</p>
          <dl class="detail-grid">
            <div><dt>负责人</dt><dd>{{ project.ownerName }}</dd></div>
            <div><dt>状态</dt><dd>{{ statusLabel(project.status) }}</dd></div>
            <div><dt>甲方单位</dt><dd>{{ displayText(project.organization) }}</dd></div>
            <div><dt>甲方联系人</dt><dd>{{ displayText(project.clientContact) }}</dd></div>
            <div><dt>所属部门</dt><dd>{{ displayText(project.department) }}</dd></div>
            <div><dt>所属组</dt><dd>{{ displayText(project.group) }}</dd></div>
          </dl>
        </template>
      </section>
      <section class="overview-section documents-section">
        <div class="section-head">
          <h2>文档</h2>
          <router-link class="primary" :to="{ name: 'project-documents', params: { id: projectId } }">查看全部文档</router-link>
        </div>
        <p v-if="documentPhase === 'loading'" class="list-state" role="status">正在读取文档…</p>
        <div v-else-if="documentPhase === 'error'" class="list-state" role="alert">{{ documentError }} <button type="button" class="ghost" @click="loadDocuments">重试</button></div>
        <div v-else-if="documents.length === 0" class="list-state">还没有文档</div>
        <div v-else class="document-list">
          <router-link v-for="item in documents" :key="item.id" class="document-row" :to="{ name: 'document', params: { id: projectId, documentId: item.id } }">
            <span class="document-name">{{ item.name }}</span>
            <span class="document-action">打开文档 →</span>
          </router-link>
        </div>
      </section>
    </div>
  </ProjectFrame>
</template>

<style scoped>
.page-state { display:flex; flex-direction:column; align-items:center; justify-content:center; gap:var(--space-3); min-height:calc(var(--space-7) * 8); color:var(--ink-secondary); }
.overview-body { display:grid; min-width:0; gap:var(--space-7); padding:var(--space-6); }
.overview-section { min-width:0; }
.section-head { display:flex; justify-content:space-between; align-items:flex-start; gap:var(--space-5); margin-bottom:var(--space-5); }
.section-head h2 { margin:0; font-size:calc(var(--text-md) * 1.25); line-height:1.4; }
.description { max-width:calc(var(--space-7) * 17); margin:0 0 var(--space-5); color:var(--ink); font-size:var(--text-sm); line-height:1.8; white-space:pre-wrap; overflow-wrap:anywhere; }
.detail-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:var(--space-5) var(--space-6); margin:0; padding:var(--space-5) 0 0; border-top:1px solid var(--rule); }
.detail-grid div { min-width:0; }
.detail-grid dt { margin-bottom:var(--space-1); color:var(--ink-tertiary); font-size:var(--text-xs); }
.detail-grid dd { margin:0; color:var(--ink); font-size:var(--text-sm); overflow-wrap:anywhere; }
.documents-section { padding-top:var(--space-6); border-top:1px solid var(--rule); }
.document-list { border-top:1px solid var(--rule); }
.document-row { display:grid; grid-template-columns:minmax(0,1fr) auto; align-items:center; gap:var(--space-5); min-height:calc(var(--space-7) + var(--space-2)); border-bottom:1px solid var(--rule); color:var(--ink); text-decoration:none; }
.document-row:hover { background:var(--desk); }
.document-name { font-weight:600; overflow-wrap:anywhere; }
.document-action { color:var(--stamp); font-size:var(--text-sm); }
.list-state { padding:var(--space-5) 0; color:var(--ink-secondary); font-size:var(--text-sm); }
.edit-form { padding:var(--space-5); border:1px solid var(--rule); border-radius:var(--radius-sheet); background:var(--desk); }
.form-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:var(--space-4); }
.form-grid label { display:grid; gap:var(--space-1); color:var(--ink-secondary); font-size:var(--text-sm); }
.form-grid .wide { grid-column:1/-1; }
.form-actions { display:flex; gap:var(--space-2); margin-top:var(--space-4); }
</style>
