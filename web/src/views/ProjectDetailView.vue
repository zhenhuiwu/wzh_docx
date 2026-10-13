<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute } from "vue-router";
import { ApiError, isMissingResource } from "../api/client";
import { getProject, updateProject, type ProjectOwnerSummary, type ProjectRecord } from "../api/projects";
import ProjectMembersSection from "../components/ProjectMembersSection.vue";
import ProjectAssignmentSummary from "../components/ProjectAssignmentSummary.vue";
import { listDocuments, type DocumentRecord } from "../api/documents";
import StatePanel from "../components/StatePanel.vue";
import ProjectFrame from "../components/ProjectFrame.vue";
import DocChainView from "../components/DocChainView.vue";
import ProjectHomeSidebar from "../components/ProjectHomeSidebar.vue";
import { projectStatuses } from "../projects";
import { useFieldErrors } from "../fieldErrors";

const route = useRoute();
const projectId = computed(() => String(route.params.id));
const project = ref<ProjectRecord | null>(null);
const documents = ref<DocumentRecord[]>([]);
const phase = ref<"loading" | "ready" | "error">("loading");
const documentPhase = ref<"loading" | "ready" | "error">("loading");
const errorText = ref("");
const notFound = ref(false);
const fields = useFieldErrors<"name" | "periodEnd">("project-edit");
const documentError = ref("");
const editing = ref(false);
const saving = ref(false);
const formError = ref("");
const form = ref({ name: "", description: "", organization: "", department: "", group: "", clientContact: "", status: "not_started" as ProjectRecord["status"], periodStart: "", periodEnd: "" });
const savedForm = ref("");
const frame = ref<InstanceType<typeof ProjectFrame> | null>(null);
const membersSection = ref<InstanceType<typeof ProjectMembersSection> | null>(null);
const assignSummary = ref<InstanceType<typeof ProjectAssignmentSummary> | null>(null);
const chainView = ref<InstanceType<typeof DocChainView> | null>(null);
const sidebar = ref<InstanceType<typeof ProjectHomeSidebar> | null>(null);
const chainState = ref({ ready: false, hasDocuments: true, canCreate: false });
/** 首页“一页一事”（30 §3.1）：项目信息和成员管理收进“…›编辑项目”“成员与权限”，按需展开。 */
const showMembers = ref(false);
const canCreateFromTemplate = computed(() => !archived.value && (project.value?.permissions?.projectRole === "owner" || Boolean(project.value?.permissions?.canArchive)));
function manageMembers() {
  showMembers.value = true;
  requestAnimationFrame(() => document.getElementById("members-anchor")?.scrollIntoView({ behavior: "smooth", block: "start" }));
}
const dirty = computed(() => editing.value && JSON.stringify(form.value) !== savedForm.value);
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
  savedForm.value = JSON.stringify(form.value);
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
  notFound.value = false;
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
    notFound.value = isMissingResource(error);
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
  // 字段级校验（08 复验 N-S3）
  if (await fields.report({
    name: !input.name ? "请填写项目名称。" : input.name.length > 120 ? "项目名称不能超过 120 个字符。" : "",
    periodEnd: input.periodStart && input.periodEnd && input.periodEnd < input.periodStart ? "结束日期不能早于开始日期。" : "",
  })) return;
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

/** A05：成员操作的响应带 owners[]，直接刷新页头和信息格，不再请求 getProject（16 §2.8 第 7 条）。 */
function onOwners(value: { owners: ProjectOwnerSummary[]; ownerId: string | null; membersRevision: number }) {
  sidebar.value?.reloadMembers();
  if (!project.value) return;
  project.value.owners = value.owners;
  project.value.ownerId = value.ownerId;
  project.value.membersRevision = value.membersRevision;
  // 主负责人按读取时的启用负责人算（19 S-1）；没有启用负责人时不再显示停用账号的姓名
  project.value.ownerName = value.owners.find(owner => owner.isPrimary)?.name ?? "";
}

/** 页头归档 / 恢复成功（21 M-1）：换成最新项目，成员区和最近文档跟着刷新。 */
function onArchiveChanged(fresh: ProjectRecord) {
  if (!project.value || fresh.id !== project.value.id) return;
  editing.value = false;
  formError.value = "";
  project.value = fresh;
  fillForm(fresh);
  void membersSection.value?.reload();
  void loadDocuments();
}

/** 涉及自己的权限变化（自身降级、归档、403）：安静地重新读取项目 permissions。 */
async function refreshPermissions() {
  const target = projectId.value;
  try {
    const result = await getProject(target);
    if (target !== projectId.value || !project.value) return;
    project.value.permissions = result.permissions;
    project.value.archivedAt = result.archivedAt;
    project.value.status = result.status;
    project.value.revision = result.revision;
    if (!editing.value) fillForm(result);
  } catch (error) {
    if (target === projectId.value && isMissingResource(error)) onGone();
  }
}

function onGone() {
  editing.value = false;
  project.value = null;
  phase.value = "error";
  notFound.value = true;
  errorText.value = "";
}

const archived = computed(() => Boolean(project.value?.archivedAt));

function canLeave() {
  if (saving.value) {
    formError.value = "正在保存项目，请稍候。";
    return false;
  }
  return !dirty.value || window.confirm("项目资料尚未保存，确定放弃修改？");
}

function cancelEdit() {
  if (!project.value || !canLeave()) return;
  fillForm(project.value);
  editing.value = false;
  formError.value = "";
  fields.clear();
}

function toggleEditing() {
  if (editing.value) { cancelEdit(); return; }
  if (!project.value || saving.value) return;
  fillForm(project.value);
  editing.value = true;
  formError.value = "";
  fields.clear();
}

function beforeUnload(event: BeforeUnloadEvent) {
  if (!saving.value && !dirty.value) return;
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
    <div class="card">
      <StatePanel v-if="notFound || !errorText" kind="error" title="项目不存在或你无权访问。"><router-link class="primary" :to="{ name: 'projects' }">返回项目列表</router-link></StatePanel>
      <StatePanel v-else kind="error" title="项目加载失败" :text="errorText"><button type="button" class="ghost" @click="load">重试</button></StatePanel>
    </div>
  </section>
  <ProjectFrame v-else ref="frame" :project="project" @archive-changed="onArchiveChanged">
    <template #actions>
      <button v-if="canCreateFromTemplate && chainState.ready && chainState.hasDocuments" type="button" class="primary" data-testid="home-create" @click="chainView?.createNext()">从模板创建</button>
    </template>
    <template #menu>
      <button v-if="project.permissions?.canEdit !== false && !archived" type="button" role="menuitem" class="menu-item" data-testid="menu-edit-project" @click="editing || toggleEditing()">编辑项目</button>
      <button type="button" role="menuitem" class="menu-item" @click="manageMembers">成员与权限</button>
    </template>
    <div class="overview-body home-grid">
      <div class="home-main">
      <section v-if="editing" class="overview-section" aria-labelledby="overview-title">
        <div class="section-head">
          <h2 id="overview-title" class="section-title">编辑项目</h2>
        </div>
        <form v-if="editing" class="edit-form" :aria-busy="saving" novalidate @submit.prevent="saveInfo">
          <div class="form-grid">
            <label><span class="required">项目名称</span><input id="project-edit-name" v-model="form.name" class="control" v-bind="fields.invalid('name')" type="text" required :disabled="saving" @input="fields.clear('name')" /><span v-if="fields.errors.value.name" :id="fields.errorId('name')" class="field-error" role="alert">{{ fields.errors.value.name }}</span></label>
            <label>状态<select v-model="form.status" class="control" :disabled="saving"><option v-for="item in projectStatuses" :key="item.value" :value="item.value">{{ item.label }}</option></select></label>
            <label>甲方单位<input v-model="form.organization" class="control" type="text" :disabled="saving" /></label>
            <label>甲方联系人<input v-model="form.clientContact" class="control" type="text" :disabled="saving" /></label>
            <label>所属部门<input v-model="form.department" class="control" type="text" :disabled="saving" /></label>
            <label>所属组<input v-model="form.group" class="control" type="text" :disabled="saving" /></label>
            <label>开始日期<input v-model="form.periodStart" class="control" type="date" :disabled="saving" /></label>
            <label>结束日期<input id="project-edit-periodEnd" v-model="form.periodEnd" class="control" v-bind="fields.invalid('periodEnd')" type="date" :disabled="saving" @input="fields.clear('periodEnd')" /><span v-if="fields.errors.value.periodEnd" :id="fields.errorId('periodEnd')" class="field-error" role="alert">{{ fields.errors.value.periodEnd }}</span></label>
            <label class="wide">项目说明<textarea v-model="form.description" class="control" rows="3" :disabled="saving" /></label>
          </div>
          <p v-if="formError" class="form-alert" role="alert">{{ formError }}</p>
          <div class="form-actions"><button type="submit" class="primary" :disabled="saving">{{ saving ? "正在保存…" : "保存项目" }}</button><button type="button" class="ghost" :disabled="saving" @click="cancelEdit">取消</button></div>
        </form>
      </section>
        <DocChainView ref="chainView" :project-id="project.id" :readonly="archived" @state="chainState = $event" />
        <div v-if="showMembers" id="members-anchor" class="home-members">
      <ProjectMembersSection ref="membersSection" :project-id="project.id" :project-name="project.name" :can-restore="Boolean(project.permissions?.canArchive)" @restore="frame?.openRestore()" @owners="onOwners" @permissions-changed="refreshPermissions" @gone="onGone" @assignments="assignSummary?.showPerson($event)" />
      <ProjectAssignmentSummary ref="assignSummary" :project-id="project.id" :can-assign="project.permissions?.projectRole === 'owner' || Boolean(project.permissions?.canArchive)" />
          <button type="button" class="ghost" @click="showMembers = false">收起成员与权限</button>
        </div>
      </div>
      <ProjectHomeSidebar ref="sidebar" :project-id="project.id" @manage-members="manageMembers" />
    </div>
  </ProjectFrame>
</template>

<style scoped>
.overview-body { display:grid; min-width:0; gap:var(--space-6); padding:var(--space-5) var(--space-6) var(--space-6); }
.home-grid { grid-template-columns:minmax(0, 1fr) 360px; align-items:start; }
.home-main, .home-members { display:grid; min-width:0; gap:var(--space-6); }
.home-members > .ghost { justify-self:start; }
.overview-section { min-width:0; }
.section-head { display:flex; justify-content:space-between; align-items:center; gap:var(--space-5); margin-bottom:var(--space-4); }
.description { max-width:calc(var(--space-7) * 20); margin:0 0 var(--space-5); color:var(--text-primary); font-size:var(--text-md); line-height:var(--leading-md); white-space:pre-wrap; overflow-wrap:anywhere; }
.detail-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:var(--space-5) var(--space-6); margin:0; padding:var(--space-5); border:1px solid var(--border-subtle); border-radius:var(--radius-sheet); background:var(--bg-subtle); }
.detail-grid div { min-width:0; }
.detail-grid dt { margin-bottom:var(--space-1); color:var(--text-tertiary); font-size:var(--text-xs); line-height:var(--leading-xs); }
.detail-grid dd { margin:0; color:var(--text-primary); font-size:var(--text-md); overflow-wrap:anywhere; }
.owner-warn { margin-left:var(--space-2); }
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
