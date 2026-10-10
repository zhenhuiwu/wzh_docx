<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ApiError } from "../api/client";
import { createProject, listProjects, listProjectOwners, type ProjectOwner, type ProjectRecord } from "../api/projects";
import { projectStatuses, statusLabel } from "../projects";
import StatePanel from "../components/StatePanel.vue";
import { formatDateTime, initialOf } from "../format";

const statusTone: Record<ProjectRecord["status"], string> = { not_started: "", active: "success", paused: "warning", completed: "info" };

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
  <section class="view fill projects-page">
    <header class="page-head">
      <div>
        <h1>项目</h1>
        <p class="page-desc">查看你参与的项目，进入项目编写文档。</p>
      </div>
      <div class="page-actions">
        <button type="button" class="primary" @click="openCreate"><svg class="ico" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3v10M3 8h10" /></svg>新建项目</button>
      </div>
    </header>
    <div class="card project-surface">
      <form class="project-query" role="search" @submit.prevent="search">
        <label class="search-box"><span class="sr">项目名称</span><svg class="ico" viewBox="0 0 16 16" aria-hidden="true"><path d="M7 12A5 5 0 1 0 7 2a5 5 0 0 0 0 10zM14 14l-3.5-3.5" /></svg><input v-model="draft.name" class="control" type="search" placeholder="搜索项目名称" :disabled="phase === 'loading'" /></label>
        <label class="inline-field">状态<select v-model="draft.status" class="control" :disabled="phase === 'loading'"><option value="">全部状态</option><option v-for="item in projectStatuses" :key="item.value" :value="item.value">{{ item.label }}</option></select></label>
        <div class="query-actions"><button type="submit" class="primary" :disabled="phase === 'loading'">查询</button><button type="button" class="ghost" :disabled="phase === 'loading'" @click="resetQuery">重置</button></div>
      </form>
      <StatePanel v-if="phase === 'loading'" kind="loading" text="正在读取项目…" />
      <StatePanel v-else-if="phase === 'error'" kind="error" title="项目加载失败" :text="errorText"><button type="button" class="ghost" @click="load">重试</button></StatePanel>
      <StatePanel v-else-if="projects.length === 0" :title="hasFilters ? '没有找到项目' : '还没有项目'" :text="hasFilters ? '换个名称或状态试试。' : '新建项目后即可在项目中编写文档。'"><button type="button" :class="hasFilters ? 'ghost' : 'primary'" @click="hasFilters ? resetQuery() : openCreate()">{{ hasFilters ? "清除筛选" : "新建项目" }}</button></StatePanel>
      <div v-else class="table-wrap project-list">
        <table>
          <colgroup><col class="col-name" /><col class="col-owner" /><col class="col-dept" /><col class="col-status" /><col class="col-time" /><col class="col-ops" /></colgroup>
          <thead><tr><th>项目名称</th><th>负责人</th><th>所属部门</th><th>状态</th><th>最近更新</th><th class="th-right">操作</th></tr></thead>
          <tbody>
            <tr v-for="project in projects" :key="project.id">
              <td><div class="cell-main"><router-link class="project-name" :to="{ name: 'project', params: { id: project.id } }">{{ project.name }}</router-link><span v-if="project.description" class="cell-sub clamp" :title="project.description">{{ project.description }}</span></div></td>
              <td><span class="person"><span class="avatar" aria-hidden="true">{{ initialOf(project.ownerName) }}</span>{{ project.ownerName }}</span></td>
              <td class="muted">{{ project.department || "—" }}</td>
              <td><span class="tag" :class="statusTone[project.status]">{{ statusLabel(project.status) }}</span></td>
              <td class="time num">{{ formatDateTime(project.updatedAt) }}</td>
              <td class="cell-right"><router-link class="link" :to="{ name: 'project', params: { id: project.id } }" :aria-label="`打开项目：${project.name}`">打开</router-link></td>
            </tr>
          </tbody>
        </table>
      </div>
      <nav class="pager" aria-label="项目分页">
        <span class="total">{{ phase === 'ready' ? `共 ${total} 个项目` : "" }}</span>
        <label>每页<select v-model.number="pageSize" class="control" :disabled="phase !== 'ready'" @change="changePageSize"><option :value="10">10 条</option><option :value="20">20 条</option><option :value="50">50 条</option></select></label>
        <span>第 {{ page }} / {{ pageCount }} 页</span>
        <button type="button" class="ghost" :disabled="phase !== 'ready' || page <= 1" @click="changePage(page - 1)">上一页</button>
        <button type="button" class="ghost" :disabled="phase !== 'ready' || page >= pageCount" @click="changePage(page + 1)">下一页</button>
        <form @submit.prevent="changePage(targetPage)">
          <label>页码<input v-model.number="targetPage" class="control page-input" type="number" min="1" :max="pageCount" required :disabled="phase !== 'ready' || total === 0" /></label>
          <button type="submit" class="ghost" :disabled="phase !== 'ready' || total === 0">跳转</button>
        </form>
      </nav>
    </div>
    <dialog v-if="creating" ref="createDialog" class="app-dialog project-dialog" aria-labelledby="create-project-title" @close="resetCreate" @cancel="submitting && $event.preventDefault()">
      <form class="create-form" novalidate @submit.prevent="submitCreate">
        <div class="dialog-head"><h2 id="create-project-title">新建项目</h2><button type="button" class="subtle icon-close" aria-label="关闭新建项目" :disabled="submitting" @click="closeCreate"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg></button></div>
        <div class="dialog-fields">
          <label class="wide"><span class="required">项目名称</span><input ref="nameInput" v-model="form.name" class="control" type="text" required :aria-invalid="formError === '请填写项目名称。' ? 'true' : undefined" :disabled="submitting" /></label>
          <label><span class="required">负责人</span><select v-model="form.ownerId" class="control" required :disabled="submitting || users.length === 0"><option v-for="user in users" :key="user.id" :value="user.id">{{ user.name }}（{{ user.account }}）</option></select></label>
          <label>状态<select v-model="form.status" class="control" :disabled="submitting"><option v-for="item in projectStatuses" :key="item.value" :value="item.value">{{ item.label }}</option></select></label>
          <label>所属部门<input v-model="form.department" class="control" type="text" :disabled="submitting" /></label>
          <label>所属组<input v-model="form.group" class="control" type="text" :disabled="submitting" /></label>
          <label>甲方单位<input v-model="form.organization" class="control" type="text" :disabled="submitting" /></label>
          <label>甲方联系人<input v-model="form.clientContact" class="control" type="text" :disabled="submitting" /></label>
        </div>
        <p v-if="usersLoading" class="hint dialog-status" role="status"><span class="spinner" aria-hidden="true"></span> 正在加载负责人…</p>
        <p v-else-if="usersError" class="form-alert dialog-status" role="alert">负责人加载失败：{{ usersError }} <button type="button" class="text" @click="loadOwners">重试</button></p>
        <p v-else-if="users.length === 0" class="form-alert dialog-status" role="alert">没有可用的负责人。</p>
        <p v-if="formError" class="form-alert field-error" role="alert">{{ formError }}</p>
        <div class="dialog-actions"><button type="button" class="ghost" :disabled="submitting" @click="closeCreate">取消</button><button type="submit" class="primary" :disabled="submitting || users.length === 0">{{ submitting ? "正在创建…" : "创建项目" }}</button></div>
      </form>
    </dialog>
  </section>
</template>

<style scoped>
.project-surface { overflow:hidden; }
.project-query { display:flex; flex-shrink:0; align-items:center; gap:var(--space-3); padding:var(--space-3) var(--space-4); border-bottom:1px solid var(--border-subtle); }
.inline-field { display:flex; align-items:center; gap:var(--space-2); color:var(--text-secondary); font-size:var(--text-sm); white-space:nowrap; }
.inline-field .control { width:calc(var(--space-7) * 3); }
.project-list { flex:1; min-height:0; overflow-y:auto; }
.project-list table { table-layout:fixed; }
.col-owner { width:calc(var(--space-7) * 3); }
.col-dept { width:calc(var(--space-7) * 3); }
.col-status { width:calc(var(--space-7) * 2.5); }
.col-time { width:calc(var(--space-7) * 3); }
.col-ops { width:calc(var(--space-7) * 2); }
.project-list td { padding-top:var(--space-3); padding-bottom:var(--space-3); }
.project-name { color:var(--text-primary); font-size:var(--text-md); font-weight:var(--weight-medium); text-decoration:none; overflow-wrap:anywhere; }
.project-name:hover { color:var(--text-link); text-decoration:underline; text-underline-offset:3px; }
.project-name:focus-visible { outline:none; border-radius:var(--radius-control); box-shadow:var(--focus-shadow); }
.clamp { display:-webkit-box; -webkit-line-clamp:2; line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
.pager .page-input { width:calc(var(--space-7) * 1.5); }
.create-form .field-error { margin:0 var(--space-5) var(--space-4); }
</style>
