<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";
import StatePanel from "../components/StatePanel.vue";
import { hasPermission, readSession } from "../session";

const canManage = computed(() => hasPermission(readSession(), "knowledge.manage"));
import { useRouter } from "vue-router";
import { ApiError } from "../api/client";
import { getCurrentSession } from "../api/identity";
import { createKnowledgeBase, listKnowledgeBases, type KnowledgeBase } from "../api/knowledge";
import { listProjects, type ProjectRecord } from "../api/projects";
import { useFieldErrors } from "../fieldErrors";

const router = useRouter();
const items = ref<KnowledgeBase[]>([]);
const total = ref(0);
const projects = ref<ProjectRecord[]>([]);
const phase = ref<"loading" | "ready" | "error">("loading");
const errorText = ref("");
const nameQuery = ref("");
const creating = ref(false);
const submitting = ref(false);
const name = ref("");
const summary = ref("");
const scope = ref<"organization" | "project">("project");
const projectId = ref("");
const ownerId = ref("");
const formError = ref("");
const fields = useFieldErrors<"name" | "project">("kb");

async function load() {
  phase.value = "loading";
  try {
    const result = await listKnowledgeBases(1, nameQuery.value.trim());
    items.value = result.items;
    total.value = result.total;
    phase.value = "ready";
  } catch (error) {
    phase.value = "error";
    errorText.value = error instanceof ApiError ? error.message : "暂时不能连接服务。";
  }
}

async function submitCreate() {
  formError.value = "";
  if (await fields.report({
    name: name.value.trim() ? "" : "请填写名称。",
    project: scope.value === "project" && !projectId.value ? "项目知识库必须选择项目。" : "",
  })) return;
  submitting.value = true;
  try {
    const created = await createKnowledgeBase({
      name: name.value.trim(),
      summary: summary.value.trim(),
      scope: scope.value,
      projectId: scope.value === "project" ? projectId.value : null,
      ownerId: ownerId.value,
    });
    await router.push({ name: "knowledge-base", params: { id: created.id } });
  } catch (error) {
    formError.value = error instanceof ApiError ? error.message : "暂时不能连接服务。";
  } finally {
    submitting.value = false;
  }
}

const createDialog = ref<HTMLDialogElement | null>(null);
const nameInput = ref<HTMLInputElement | null>(null);
watch(creating, async (open) => {
  await nextTick();
  const dialog = createDialog.value;
  if (!dialog) return;
  if (open && !dialog.open) { dialog.showModal(); nameInput.value?.focus(); }
  if (!open && dialog.open) dialog.close();
});
function dismissCreate() {
  if (!submitting.value) { creating.value = false; formError.value = ""; fields.clear(); }
}

onMounted(async () => {
  try {
    ownerId.value = (await getCurrentSession()).user.id;
    projects.value = (await listProjects({ page: 1, pageSize: 50 })).items;
    projectId.value = projects.value[0]?.id ?? "";
  } catch {
    projects.value = [];
  }
  await load();
});
</script>

<template>
  <section class="view fill">
    <header class="page-head">
      <div><h1>知识库</h1><p class="page-desc">只显示你有权查看的知识库。上传成功还不等于可以检索；公司资料格式和检索范围尚未确定。</p></div>
      <div v-if="canManage" class="page-actions"><button type="button" class="primary" :disabled="creating" @click="creating = true"><svg class="ico" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3v10M3 8h10" /></svg>新建知识库</button></div>
    </header>
    <div class="card kb-card">
      <form class="query" @submit.prevent="load">
        <div class="query-fields">
          <label class="search-box"><svg class="ico" viewBox="0 0 16 16" aria-hidden="true"><path d="M7 12A5 5 0 1 0 7 2a5 5 0 0 0 0 10zM14 14l-3.5-3.5" /></svg><input v-model="nameQuery" class="control" type="search" placeholder="搜索知识库名称" aria-label="名称" /></label>
        </div>
        <div class="query-actions"><button type="submit" class="primary">查询</button></div>
      </form>
      <StatePanel v-if="phase === 'loading'" kind="loading" text="正在读取知识库…" />
      <StatePanel v-else-if="phase === 'error'" kind="error" title="知识库加载失败" :text="errorText"><button type="button" class="ghost" @click="load">重试</button></StatePanel>
      <StatePanel v-else-if="items.length === 0" :title="nameQuery.trim() ? '没有符合条件的知识库' : '还没有你能查看的知识库'" text="知识库用于存放项目或单位的参考资料。" />
      <div v-else class="table-wrap kb-table">
        <table>
          <thead>
            <tr>
              <th class="col-name">名称</th>
              <th class="col-scope">归属</th>
              <th class="col-state">状态</th>
              <th>说明</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in items" :key="item.id">
              <td><router-link class="link" :to="{ name: 'knowledge-base', params: { id: item.id } }">{{ item.name }}</router-link></td>
              <td><span class="tag plain">{{ item.scope === "project" ? "项目" : "单位" }}</span></td>
              <td><span class="status" :data-status="item.state === 'active' ? '启用' : '已归档'">{{ item.state === "active" ? "启用" : "已归档" }}</span></td>
              <td class="muted">{{ item.summary || "—" }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-if="phase === 'ready' && items.length" class="pager"><span class="total">共 {{ total }} 条</span></div>
    </div>

    <dialog ref="createDialog" class="app-dialog" aria-labelledby="create-kb-title" @cancel.prevent="dismissCreate">
      <form v-if="creating" class="create" novalidate @submit.prevent="submitCreate">
        <div class="dialog-head"><h2 id="create-kb-title">新建知识库</h2><button type="button" class="subtle icon-close" aria-label="关闭" :disabled="submitting" @click="dismissCreate"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg></button></div>
        <div class="dialog-fields">
          <div class="field span-2"><label for="kb-name" class="required">名称</label><input id="kb-name" ref="nameInput" v-model="name" class="control" v-bind="fields.invalid('name')" type="text" :disabled="submitting" @input="fields.clear('name')" /><span v-if="fields.errors.value.name" :id="fields.errorId('name')" class="field-error" role="alert">{{ fields.errors.value.name }}</span></div>
          <div class="field span-2"><label for="kb-summary">说明</label><input id="kb-summary" v-model="summary" class="control" type="text" :disabled="submitting" /></div>
          <div class="field">
            <label for="kb-scope" class="required">归属</label>
            <select id="kb-scope" v-model="scope" class="control" :disabled="submitting">
              <option value="project">项目</option>
              <option value="organization">单位</option>
            </select>
          </div>
          <div v-if="scope === 'project'" class="field">
            <label for="kb-project" class="required">项目</label>
            <select id="kb-project" v-model="projectId" class="control" v-bind="fields.invalid('project')" :disabled="submitting" @change="fields.clear('project')">
              <option value="">请选择</option>
              <option v-for="project in projects" :key="project.id" :value="project.id">{{ project.name }}</option>
            </select>
            <span v-if="fields.errors.value.project" :id="fields.errorId('project')" class="field-error" role="alert">{{ fields.errors.value.project }}</span>
          </div>
        </div>
        <p v-if="formError" class="form-alert dialog-status" role="alert">{{ formError }}</p>
        <div class="dialog-actions">
          <button type="button" class="ghost" :disabled="submitting" @click="dismissCreate">取消</button>
          <button type="submit" class="primary" :disabled="submitting">{{ submitting ? "正在创建" : "创建" }}</button>
        </div>
      </form>
    </dialog>
  </section>
</template>

<style scoped>
.kb-card { overflow: hidden; }
.kb-table { flex: 1; min-height: 0; overflow-y: auto; }
.kb-table table { table-layout: fixed; }
.col-name { width: 28%; }
.col-scope { width: 12%; }
.col-state { width: 12%; }
.kb-table td { white-space: normal; overflow-wrap: anywhere; }
.create { display: block; padding: 0; }
</style>
