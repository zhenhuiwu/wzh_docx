<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ApiError } from "../api/client";
import { createProject, listProjects, listProjectOwners, type ProjectOwner, type ProjectRecord } from "../api/projects";
import { projectStatuses, statusLabel } from "../projects";

const route = useRoute();
const router = useRouter();
const page = ref(1);
const pageSize = ref(10);
const targetPage = ref(1);
const draft = ref({ name: "", status: "" });
const applied = ref({ ...draft.value });
const projects = ref<ProjectRecord[]>([]);
const total = ref(0);
const users = ref<ProjectOwner[]>([]);
const usersLoading = ref(false);
const usersError = ref("");
const phase = ref<"loading" | "ready" | "error">("loading");
const errorText = ref("");
const creating = ref(false);
const submitting = ref(false);
const formError = ref("");
const createDialog = ref<HTMLDialogElement | null>(null);
const nameInput = ref<HTMLInputElement | null>(null);
const form = ref({ name: "", ownerId: "", group: "", department: "", organization: "", clientContact: "", status: "not_started" as ProjectRecord["status"] });
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)));
const hasFilters = computed(() => Boolean(applied.value.name || applied.value.status));

function messageOf(error: unknown) {
  return error instanceof ApiError ? error.message : "暂时不能连接服务。";
}

async function load() {
  phase.value = "loading";
  errorText.value = "";
  try {
    const result = await listProjects({ page: page.value, pageSize: pageSize.value, name: applied.value.name, status: applied.value.status });
    projects.value = result.items;
    total.value = result.total;
    page.value = result.page;
    targetPage.value = result.page;
    phase.value = "ready";
  } catch (error) {
    phase.value = "error";
    errorText.value = messageOf(error);
  }
}

function search() {
  if (phase.value === "loading") return;
  applied.value = { ...draft.value };
  page.value = 1;
  void load();
}

function changePage(nextPage: number) {
  if (phase.value === "loading" || !Number.isInteger(nextPage)) return;
  const next = Math.min(pageCount.value, Math.max(1, nextPage));
  targetPage.value = next;
  if (next === page.value) return;
  page.value = next;
  void load();
}

function changePageSize() {
  page.value = 1;
  void load();
}

function resetQuery() {
  draft.value = { name: "", status: "" };
  search();
}

async function openCreate() {
  creating.value = true;
  formError.value = "";
  await nextTick();
  createDialog.value?.showModal();
  nameInput.value?.focus();
}

function closeCreate() {
  if (submitting.value) return;
  createDialog.value?.close();
}

async function loadOwners() {
  if (usersLoading.value) return;
  usersLoading.value = true;
  usersError.value = "";
  try {
    const owners: ProjectOwner[] = [];
    for (let ownerPage = 1; ; ownerPage++) {
      const result = await listProjectOwners(ownerPage);
      owners.push(...result.items);
      if (owners.length >= result.total || result.items.length === 0) break;
    }
    users.value = [...new Map(owners.map(user => [user.id, user])).values()];
    if (!users.value.some(user => user.id === form.value.ownerId)) form.value.ownerId = users.value[0]?.id ?? "";
  } catch (error) {
    users.value = [];
    usersError.value = messageOf(error);
  } finally {
    usersLoading.value = false;
  }
}

function resetCreate() {
  creating.value = false;
  form.value = { name: "", ownerId: users.value[0]?.id ?? "", group: "", department: "", organization: "", clientContact: "", status: "not_started" };
  formError.value = "";
}

async function submitCreate() {
  if (submitting.value) return;
  formError.value = "";
  if (!form.value.name.trim()) {
    formError.value = "请填写项目名称。";
    nameInput.value?.focus();
    return;
  }
  if (!form.value.ownerId) {
    formError.value = "请选择负责人。";
    return;
  }
  submitting.value = true;
  try {
    const created = await createProject({
      name: form.value.name.trim(), ownerId: form.value.ownerId, status: form.value.status,
      group: form.value.group.trim(), department: form.value.department.trim(),
      organization: form.value.organization.trim(), clientContact: form.value.clientContact.trim(),
    });
    createDialog.value?.close();
    await router.push({ name: "project", params: { id: created.id } });
  } catch (error) {
    formError.value = messageOf(error);
  } finally {
    submitting.value = false;
  }
}

onMounted(async () => {
  await Promise.all([loadOwners(), load()]);
  if (route.query.create === "1") await openCreate();
});
</script>

<template>
  <section class="projects-page">
    <header class="page-head">
      <h1>项目</h1>
      <button type="button" class="primary" @click="openCreate">新建项目</button>
    </header>
    <div class="project-surface">
      <form class="project-query" @submit.prevent="search">
        <label>项目名称<input v-model="draft.name" class="control" type="search" :disabled="phase === 'loading'" /></label>
        <label>状态<select v-model="draft.status" class="control" :disabled="phase === 'loading'"><option value="">全部状态</option><option v-for="item in projectStatuses" :key="item.value" :value="item.value">{{ item.label }}</option></select></label>
        <div class="query-actions"><button type="submit" class="primary" :disabled="phase === 'loading'">查询</button><button type="button" class="ghost" :disabled="phase === 'loading'" @click="resetQuery">重置</button></div>
      </form>
      <div v-if="phase === 'loading'" class="state-panel" role="status">正在读取项目…</div>
      <div v-else-if="phase === 'error'" class="state-panel" role="alert"><strong>项目加载失败</strong><p>{{ errorText }}</p><button type="button" class="ghost" @click="load">重试</button></div>
      <div v-else-if="projects.length === 0" class="state-panel"><strong>{{ hasFilters ? "没有找到项目" : "还没有项目" }}</strong><button type="button" class="ghost" @click="hasFilters ? resetQuery() : openCreate()">{{ hasFilters ? "清除筛选" : "新建项目" }}</button></div>
      <div v-else class="project-list">
        <router-link v-for="project in projects" :key="project.id" class="project-row" :to="{ name: 'project', params: { id: project.id } }">
          <span class="project-name">{{ project.name }}</span>
          <span class="project-status">{{ statusLabel(project.status) }}</span>
          <span class="open-link">打开 →</span>
        </router-link>
      </div>
      <nav class="pager" aria-label="项目分页">
        <span v-if="phase === 'ready'" class="total">共 {{ total }} 个项目</span>
        <label>每页<select v-model.number="pageSize" class="control" :disabled="phase !== 'ready'" @change="changePageSize"><option :value="10">10 条</option><option :value="20">20 条</option><option :value="50">50 条</option></select></label>
        <span>第 {{ page }} / {{ pageCount }} 页</span>
        <button type="button" class="ghost" :disabled="phase !== 'ready' || page <= 1" @click="changePage(page - 1)">上一页</button>
        <button type="button" class="ghost" :disabled="phase !== 'ready' || page >= pageCount" @click="changePage(page + 1)">下一页</button>
        <form @submit.prevent="changePage(targetPage)">
          <label>页码<input v-model.number="targetPage" class="control" type="number" min="1" :max="pageCount" required :disabled="phase !== 'ready' || total === 0" /></label>
          <button type="submit" class="ghost" :disabled="phase !== 'ready' || total === 0">跳转</button>
        </form>
      </nav>
    </div>
    <dialog v-if="creating" ref="createDialog" class="project-dialog" aria-labelledby="create-project-title" @close="resetCreate" @cancel="submitting && $event.preventDefault()">
      <form class="create-form" novalidate @submit.prevent="submitCreate">
        <div class="dialog-head"><h2 id="create-project-title">新建项目</h2><button type="button" class="ghost" aria-label="关闭新建项目" :disabled="submitting" @click="closeCreate">关闭</button></div>
        <div class="dialog-fields">
          <label>项目名称<input ref="nameInput" v-model="form.name" class="control" type="text" required :disabled="submitting" /></label>
          <label>负责人<select v-model="form.ownerId" class="control" required :disabled="submitting || users.length === 0"><option v-for="user in users" :key="user.id" :value="user.id">{{ user.name }}（{{ user.account }}）</option></select></label>
          <label>所属组<input v-model="form.group" class="control" type="text" :disabled="submitting" /></label>
          <label>所属部门<input v-model="form.department" class="control" type="text" :disabled="submitting" /></label>
          <label>甲方单位<input v-model="form.organization" class="control" type="text" :disabled="submitting" /></label>
          <label>甲方联系人<input v-model="form.clientContact" class="control" type="text" :disabled="submitting" /></label>
          <label>状态<select v-model="form.status" class="control" :disabled="submitting"><option v-for="item in projectStatuses" :key="item.value" :value="item.value">{{ item.label }}</option></select></label>
        </div>
        <p v-if="formError" class="field-error" role="alert">{{ formError }}</p>
        <p v-if="usersLoading" class="field-error" role="status">正在加载负责人…</p>
        <p v-else-if="usersError" class="field-error" role="alert">负责人加载失败：{{ usersError }} <button type="button" class="ghost" @click="loadOwners">重试</button></p>
        <p v-else-if="users.length === 0" class="field-error" role="alert">没有可用的负责人。</p>
        <div class="dialog-actions"><button type="button" class="ghost" :disabled="submitting" @click="closeCreate">取消</button><button type="submit" class="primary" :disabled="submitting || users.length === 0">{{ submitting ? "正在创建…" : "创建项目" }}</button></div>
      </form>
    </dialog>
  </section>
</template>

<style scoped>
.projects-page { display:flex; flex:none; flex-direction:column; min-width:0; min-height:0; height:calc(100vh - var(--space-7) - var(--space-3) - var(--space-5) * 2); gap:var(--space-5); }
.page-head { display:flex; align-items:end; justify-content:space-between; gap:var(--space-5); padding:var(--space-3) var(--space-2) 0; }
.page-head h1 { margin:0; font-size:calc(var(--text-md) * 1.75); line-height:1.25; }
.project-surface { display:flex; flex:1; flex-direction:column; width:100%; min-width:0; min-height:0; background:var(--sheet); border:1px solid var(--rule); border-radius:var(--radius-sheet); }
.project-query { display:flex; flex-shrink:0; align-items:end; gap:var(--space-4); padding:var(--space-5); border-bottom:1px solid var(--rule); }
.project-query label { display:grid; gap:var(--space-1); min-width:calc(var(--space-7) * 4); color:var(--ink-secondary); font-size:var(--text-xs); }
.project-query label:first-child { width:calc(var(--space-7) * 8); }
.query-actions { display:flex; gap:var(--space-2); }
.project-list { flex:1; min-height:0; overflow-y:auto; }
.project-row { display:grid; grid-template-columns:minmax(0,1fr) calc(var(--space-7) * 2) calc(var(--space-7) * 2); align-items:center; gap:var(--space-4); min-height:calc(var(--space-7) + var(--space-2)); padding:var(--space-3) var(--space-5); border-bottom:1px solid var(--rule); color:var(--ink); text-decoration:none; }
.project-row:hover { background:var(--desk); }
.project-name { min-width:0; font-size:var(--text-md); font-weight:600; overflow-wrap:anywhere; }
.project-status { color:var(--ink-secondary); font-size:var(--text-sm); }
.open-link { color:var(--stamp); font-size:var(--text-sm); text-align:right; }
.state-panel { display:flex; flex:1; flex-direction:column; align-items:center; justify-content:center; gap:var(--space-3); min-height:calc(var(--space-7) * 7); color:var(--ink-secondary); text-align:center; }
.state-panel strong { color:var(--ink); font-size:var(--text-md); }
.pager { display:flex; flex-shrink:0; justify-content:flex-end; align-items:center; gap:var(--space-3); margin-top:auto; padding:var(--space-3) var(--space-5); border-top:1px solid var(--rule); color:var(--ink-secondary); font-size:var(--text-sm); }
.pager label,.pager form { display:flex; align-items:center; gap:var(--space-2); }
.pager .control { width:calc(var(--space-7) * 2); }
.project-dialog { width:calc(var(--space-7) * 15); max-height:calc(100vh - var(--space-7)); padding:0; overflow:auto; border:1px solid var(--rule-strong); border-radius:var(--radius-sheet); background:var(--sheet); color:var(--ink); }
.project-dialog::backdrop { background:var(--spine); opacity:.45; }
.dialog-head { display:flex; align-items:center; justify-content:space-between; padding:var(--space-5); border-bottom:1px solid var(--rule); }
.dialog-head h2 { margin:0; font-size:calc(var(--text-md) * 1.25); }
.dialog-fields { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:var(--space-4); padding:var(--space-5); }
.dialog-fields label { display:grid; gap:var(--space-1); color:var(--ink-secondary); font-size:var(--text-sm); }
.create-form .field-error { margin:0; padding:0 var(--space-5) var(--space-3); }
.dialog-actions { display:flex; justify-content:flex-end; gap:var(--space-2); padding:var(--space-4) var(--space-5); border-top:1px solid var(--rule); }
.project-row:focus-visible { outline:2px solid var(--stamp); outline-offset:-2px; }
</style>
