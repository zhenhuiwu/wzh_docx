<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import StatePanel from "../components/StatePanel.vue";
import { useRoute } from "vue-router";
import { ApiError, isMissingResource } from "../api/client";
import { uploadFile } from "../api/files";
import { createSource, getKnowledgeBase, listSources, type KnowledgeBase, type SourceVersion } from "../api/knowledge";
import FilePicker from "../components/FilePicker.vue";
import { rememberCrumb } from "../crumbs";
import { hasPermission, readSession } from "../session";
import { useFieldErrors } from "../fieldErrors";

const route = useRoute();
const baseId = computed(() => String(route.params.id));
const base = ref<KnowledgeBase | null>(null);
const sources = ref<SourceVersion[]>([]);
const phase = ref<"loading" | "ready" | "error">("loading");
const errorText = ref("");
const notice = ref("");
const name = ref("");
const file = ref<File | null>(null);
const submitting = ref(false);
const tab = ref<"materials" | "search">("materials");
const notFound = ref(false);
const fields = useFieldErrors<"name" | "file">("source");
const canManage = computed(() => hasPermission(readSession(), "knowledge.manage"));

async function load() {
  phase.value = "loading";
  try {
    notFound.value = false;
    base.value = await getKnowledgeBase(baseId.value);
    rememberCrumb(`knowledge:${base.value.id}`, base.value.name);
    sources.value = await listSources(baseId.value);
    phase.value = "ready";
  } catch (error) {
    phase.value = "error";
    notFound.value = isMissingResource(error);
    errorText.value = error instanceof ApiError ? error.message : "暂时不能连接服务。";
  }
}

async function submitFile() {
  notice.value = "";
  // 字段级校验（08 复验 N-S3）
  if (await fields.report({
    name: name.value.trim() ? "" : "请填写资料名称。",
    file: file.value ? "" : "请选择文件。",
  }) || !file.value) return;
  const targetBase = baseId.value;
  const targetName = name.value.trim();
  const targetFile = file.value;
  submitting.value = true;
  try {
    const stored = await uploadFile(targetFile, "source", { baseId: targetBase, idempotencyKey: crypto.randomUUID() });
    await createSource(targetBase, targetName, stored.id);
    name.value = "";
    file.value = null;
    notice.value = "已登记。这份资料尚未解析，不能检索。";
    await load();
  } catch (error) {
    notice.value = error instanceof ApiError ? error.message : "暂时不能连接服务。";
  } finally {
    submitting.value = false;
  }
}

onMounted(() => void load());
</script>

<template>
  <section class="view fill">
    <header class="page-head">
      <div>
        <h1>{{ base?.name || "知识库" }}</h1>
        <p v-if="base" class="page-desc"><span class="tag plain">{{ base.scope === "project" ? "项目知识库" : "单位知识库" }}</span><span v-if="base.summary" class="summary">{{ base.summary }}</span></p>
      </div>
      <div v-if="!(phase === 'error' && notFound)" class="page-actions"><router-link class="ghost" :to="{ name: 'knowledge' }">返回列表</router-link></div>
    </header>
    <div v-if="phase === 'loading'" class="card"><StatePanel kind="loading" text="正在读取知识库…" /></div>
    <div v-else-if="phase === 'error' && notFound" class="card"><StatePanel kind="error" title="知识库不存在或已删除。" text="请从知识库列表重新进入。"><router-link class="primary" :to="{ name: 'knowledge' }">返回知识库列表</router-link></StatePanel></div>
    <div v-else-if="phase === 'error'" class="card"><StatePanel kind="error" title="知识库没有载入" :text="errorText"><button type="button" class="ghost" @click="load">重试</button></StatePanel></div>
    <div v-else class="card kb-card">
      <!-- 页签放进卡片顶部，与项目详情一致（08 验收 S5） -->
      <nav class="tabs" aria-label="知识库内容">
        <button type="button" :class="{ on: tab === 'materials' }" :aria-current="tab === 'materials' ? 'page' : undefined" @click="tab = 'materials'">资料<span class="count">{{ sources.length }}</span></button>
        <button type="button" :class="{ on: tab === 'search' }" :aria-current="tab === 'search' ? 'page' : undefined" @click="tab = 'search'">检索</button>
      </nav>
      <StatePanel v-if="tab === 'search'" title="检索尚未接入" text="未发布和未解析的资料不会出现在检索结果里。" />
      <template v-else>
        <!-- 登记资料按 knowledge.manage 显隐（09 复审 R5） -->
        <form v-if="canManage" class="query upload" @submit.prevent="submitFile">
          <div class="query-fields">
            <label><span class="required">资料名称</span><span class="field-stack"><input id="source-name" v-model="name" class="control" v-bind="fields.invalid('name')" type="text" :disabled="submitting" @input="fields.clear('name')" /><span v-if="fields.errors.value.name" :id="fields.errorId('name')" class="field-error" role="alert">{{ fields.errors.value.name }}</span></span></label>
            <label><span class="required">文件</span><span class="field-stack"><FilePicker id="source-file" class="file" :file="file" :invalid="Boolean(fields.errors.value.file)" v-bind="fields.invalid('file')" :disabled="submitting" @change="picked => { file = picked; fields.clear('file'); }" /><span v-if="fields.errors.value.file" :id="fields.errorId('file')" class="field-error" role="alert">{{ fields.errors.value.file }}</span></span></label>
          </div>
          <div class="query-actions"><button type="submit" class="primary" :disabled="submitting">{{ submitting ? "正在登记" : "登记资料" }}</button></div>
        </form>
        <p v-if="notice" class="notice" role="status">{{ notice }}</p>
        <StatePanel v-if="sources.length === 0" title="还没有资料" :text="canManage ? '选择文件并登记后，资料会出现在这里。' : '知识库维护者登记资料后，资料会出现在这里。'" />
        <div v-else class="table-wrap kb-table">
          <table>
            <thead>
              <tr>
                <th>资料名</th>
                <th class="col-ver">版本</th>
                <th class="col-state">状态</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in sources" :key="item.id">
                <td>{{ item.name }}</td>
                <td class="num">v{{ item.version }}</td>
                <td><span class="tag" :class="item.status === 'uploaded' ? 'plain' : 'warning'">{{ item.status === "uploaded" ? "已上传" : item.status }}</span></td>
                <td class="muted">{{ item.message || "—" }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
    </div>
  </section>
</template>

<style scoped>
.page-desc { display: flex; align-items: center; gap: var(--space-2); }
.summary { color: var(--text-secondary); }
.kb-card { display: flex; flex: 1; flex-direction: column; min-height: 0; overflow: hidden; }
.kb-table { flex: 1; min-height: 0; overflow-y: auto; }
.kb-table table { table-layout: fixed; }
.col-ver { width: 10%; }
.col-state { width: 12%; }
.kb-table td { white-space: normal; overflow-wrap: anywhere; }
.upload .file { width: calc(var(--space-7) * 9); }
.upload label { align-items: flex-start; }
.upload label > span:first-child { line-height: var(--control-h); }
.field-stack { display: inline-grid; gap: var(--space-1); }
.field-stack .field-error { margin: 0; white-space: normal; }
</style>
