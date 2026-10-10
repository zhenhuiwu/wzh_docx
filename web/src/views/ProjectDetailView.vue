<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute } from "vue-router";
import { ApiError } from "../api/client";
import { getProject, updateProject, type ProjectRecord } from "../api/projects";
import { documentStatusLabel, listDocuments, type DocumentRecord } from "../api/documents";
import StatePanel from "../components/StatePanel.vue";
import { formatDateTime, initialOf } from "../format";
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
  <section v-if="phase === 'loading'" class="view fill"><div class="card"><StatePanel kind="loading" text="正在读取项目…" /></div></section>
  <section v-else-if="phase === 'error' || !project" class="view fill">
    <div class="card"><StatePanel kind="error" title="项目加载失败" :text="errorText || '没有找到这个项目。'"><button type="button" class="ghost" @click="load">重试</button></StatePanel></div>
  </section>
  <ProjectFrame v-else :project="project">
    <div class="overview-body">
      <section class="overview-section" aria-labelledby="overview-title">
        <div class="section-head">
          <h2 id="overview-title" class="section-title">项目概述</h2>
          <button type="button" class="ghost" :disabled="saving" @click="editing = !editing; formError = ''">{{ editing ? "收起编辑" : "编辑项目" }}</button>
        </div>
        <form v-if="editing" class="edit-form" :aria-busy="saving" novalidate @submit.prevent="saveInfo">
          <div class="form-grid">
            <label><span class="required">项目名称</span><input v-model="form.name" class="control" type="text" required :aria-invalid="formError === '请填写项目名称。' ? 'true' : undefined" :disabled="saving" /></label>
            <label>状态<select v-model="form.status" class="control" :disabled="saving"><option v-for="item in projectStatuses" :key="item.value" :value="item.value">{{ item.label }}</option></select></label>
            <label>甲方单位<input v-model="form.organization" class="control" type="text" :disabled="saving" /></label>
            <label>甲方联系人<input v-model="form.clientContact" class="control" type="text" :disabled="saving" /></label>
            <label>所属部门<input v-model="form.department" class="control" type="text" :disabled="saving" /></label>
            <label>所属组<input v-model="form.group" class="control" type="text" :disabled="saving" /></label>
            <label>开始日期<input v-model="form.periodStart" class="control" type="date" :disabled="saving" /></label>
            <label>结束日期<input v-model="form.periodEnd" class="control" type="date" :disabled="saving" /></label>
            <label class="wide">项目说明<textarea v-model="form.description" class="control" rows="3" :disabled="saving" /></label>
          </div>
          <p v-if="formError" class="form-alert field-error" role="alert">{{ formError }}</p>
          <div class="form-actions"><button type="submit" class="primary" :disabled="saving">{{ saving ? "正在保存…" : "保存项目" }}</button><button type="button" class="ghost" :disabled="saving" @click="editing = false; fillForm(project)">取消</button></div>
        </form>
        <template v-else>
          <p v-if="project.description" class="description">{{ project.description }}</p>
          <p v-else class="description faint">还没有项目说明。</p>
          <dl class="detail-grid">
            <div><dt>负责人</dt><dd><span class="person"><span class="avatar" aria-hidden="true">{{ initialOf(project.ownerName) }}</span>{{ project.ownerName }}</span></dd></div>
            <div><dt>状态</dt><dd>{{ statusLabel(project.status) }}</dd></div>
            <div><dt>甲方单位</dt><dd>{{ displayText(project.organization) }}</dd></div>
            <div><dt>甲方联系人</dt><dd>{{ displayText(project.clientContact) }}</dd></div>
            <div><dt>所属部门</dt><dd>{{ displayText(project.department) }}</dd></div>
            <div><dt>所属组</dt><dd>{{ displayText(project.group) }}</dd></div>
            <div><dt>计划周期</dt><dd class="num">{{ project.periodStart || project.periodEnd ? `${project.periodStart || "未定"} 至 ${project.periodEnd || "未定"}` : "—" }}</dd></div>
            <div><dt>最近更新</dt><dd class="num">{{ formatDateTime(project.updatedAt) }}</dd></div>
          </dl>
        </template>
      </section>
      <section class="overview-section documents-section" aria-labelledby="documents-title">
        <div class="section-head">
          <h2 id="documents-title" class="section-title">最近文档</h2>
          <router-link class="ghost" :to="{ name: 'project-documents', params: { id: projectId } }">查看全部文档</router-link>
        </div>
        <div class="doc-box">
          <StatePanel v-if="documentPhase === 'loading'" kind="loading" text="正在读取文档…" class="compact" />
          <StatePanel v-else-if="documentPhase === 'error'" kind="error" title="文档加载失败" :text="documentError" class="compact"><button type="button" class="ghost" @click="loadDocuments">重试</button></StatePanel>
          <StatePanel v-else-if="documents.length === 0" title="还没有文档" text="在“文档编写”中从模板创建或上传 Word 文档。" class="compact"><router-link class="primary" :to="{ name: 'project-documents', params: { id: projectId } }">去新建文档</router-link></StatePanel>
          <div v-else class="table-wrap document-list">
            <table>
              <thead><tr><th>文档名称</th><th>来源</th><th>编写人</th><th>状态</th><th>最近修改</th><th class="th-right">操作</th></tr></thead>
              <tbody>
                <tr v-for="item in documents" :key="item.id">
                  <td class="doc-name-cell"><router-link class="document-name" :to="{ name: 'document', params: { id: projectId, documentId: item.id } }">{{ item.name }}</router-link></td>
                  <td class="muted">{{ item.sourceType === "upload" ? "上传 Word" : item.templateDocumentName || "模板" }}</td>
                  <td>{{ item.ownerName || "—" }}</td>
                  <td><span class="tag" :class="{ info: item.status === 'in_progress', success: item.status === 'completed' }">{{ documentStatusLabel[item.status] || item.status }}</span></td>
                  <td class="time num">{{ formatDateTime(item.updatedAt) }}</td>
                  <td class="cell-right"><router-link class="link" :to="{ name: 'document', params: { id: projectId, documentId: item.id } }" :aria-label="`打开文档：${item.name}`">打开文档</router-link></td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>
    </div>
  </ProjectFrame>
</template>

<style scoped>
.overview-body { display:grid; min-width:0; gap:var(--space-6); padding:var(--space-5) var(--space-6) var(--space-6); }
.overview-section { min-width:0; }
.section-head { display:flex; justify-content:space-between; align-items:center; gap:var(--space-5); margin-bottom:var(--space-4); }
.description { max-width:calc(var(--space-7) * 20); margin:0 0 var(--space-5); color:var(--text-primary); font-size:var(--text-md); line-height:var(--leading-md); white-space:pre-wrap; overflow-wrap:anywhere; }
.detail-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:var(--space-5) var(--space-6); margin:0; padding:var(--space-5); border:1px solid var(--border-subtle); border-radius:var(--radius-sheet); background:var(--bg-subtle); }
.detail-grid div { min-width:0; }
.detail-grid dt { margin-bottom:var(--space-1); color:var(--text-tertiary); font-size:var(--text-xs); line-height:var(--leading-xs); }
.detail-grid dd { margin:0; color:var(--text-primary); font-size:var(--text-md); overflow-wrap:anywhere; }
.documents-section { padding-top:var(--space-6); border-top:1px solid var(--border-subtle); }
.doc-box { border:1px solid var(--border-default); border-radius:var(--radius-sheet); overflow:hidden; }
.doc-box .compact { min-height:calc(var(--space-7) * 4); }
.doc-name-cell { width:40%; white-space:normal; }
.document-name { color:var(--text-primary); font-weight:var(--weight-medium); text-decoration:none; overflow-wrap:anywhere; }
.document-name:hover { color:var(--text-link); text-decoration:underline; text-underline-offset:3px; }
.document-name:focus-visible { outline:none; border-radius:var(--radius-control); box-shadow:var(--focus-shadow); }
.edit-form { display:grid; gap:var(--space-4); padding:var(--space-5); border:1px solid var(--border-default); border-radius:var(--radius-sheet); background:var(--bg-subtle); }
.form-actions { display:flex; gap:var(--space-2); }
</style>
