<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { ApiError } from "../api/client";
import { getCurrentSession } from "../api/identity";
import { createKnowledgeBase, listKnowledgeBases, type KnowledgeBase } from "../api/knowledge";
import { listProjects, type ProjectRecord } from "../api/projects";

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
  if (!name.value.trim()) {
    formError.value = "请填写名称";
    return;
  }
  if (scope.value === "project" && !projectId.value) {
    formError.value = "项目知识库必须选择项目";
    return;
  }
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
  <section class="card">
    <div class="head">
      <h1>知识库</h1>
      <button v-if="!creating" type="button" class="primary" @click="creating = true">新建知识库</button>
    </div>
    <p class="notice">只显示你有权查看的知识库。上传成功还不等于可以检索。公司资料格式和检索范围还没有定。</p>
    <p v-if="phase === 'error'" class="notice" role="alert">{{ errorText }}</p>
    <form class="query" @submit.prevent="load">
      <div class="query-fields">
        <label><span>名称</span><input v-model="nameQuery" class="control" type="search" /></label>
      </div>
      <div class="query-actions"><button type="submit" class="primary">查询</button></div>
    </form>
    <form v-if="creating" class="create" @submit.prevent="submitCreate">
      <label>名称<input v-model="name" class="control" type="text" :disabled="submitting" /></label>
      <label>说明<input v-model="summary" class="control" type="text" :disabled="submitting" /></label>
      <label>归属
        <select v-model="scope" class="control" :disabled="submitting">
          <option value="project">项目</option>
          <option value="organization">单位</option>
        </select>
      </label>
      <label v-if="scope === 'project'">项目
        <select v-model="projectId" class="control" :disabled="submitting">
          <option value="">请选择</option>
          <option v-for="project in projects" :key="project.id" :value="project.id">{{ project.name }}</option>
        </select>
      </label>
      <p v-if="formError" class="field-error" role="alert">{{ formError }}</p>
      <div class="actions">
        <button type="submit" class="primary" :disabled="submitting">{{ submitting ? "正在创建" : "创建" }}</button>
        <button type="button" class="ghost" @click="creating = false">取消</button>
      </div>
    </form>
    <p v-if="phase === 'loading'" class="empty">正在读取知识库</p>
    <p v-else-if="items.length === 0" class="empty">还没有你能查看的知识库</p>
    <div v-else class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>名称</th>
            <th>归属</th>
            <th>状态</th>
            <th class="from-mid">说明</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in items" :key="item.id">
            <td><router-link class="link" :to="{ name: 'knowledge-base', params: { id: item.id } }">{{ item.name }}</router-link></td>
            <td>{{ item.scope === "project" ? "项目" : "单位" }}</td>
            <td>{{ item.state === "active" ? "启用" : "已归档" }}</td>
            <td class="from-mid">{{ item.summary || "—" }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-if="phase === 'ready'" class="pager">共 {{ total }} 条</p>
  </section>
</template>

<style scoped>
.create { display: grid; gap: 8px; max-width: 420px; margin: 16px 0; }
.create label { display: grid; gap: 4px; }
.actions { display: flex; gap: 8px; }
@media (max-width: 767px) {
  .from-mid { display: none; }
}
</style>
