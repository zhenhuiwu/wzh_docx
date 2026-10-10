<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import StatePanel from "../components/StatePanel.vue";
import { useRoute } from "vue-router";
import { ApiError } from "../api/client";
import { uploadFile } from "../api/files";
import { createSource, getKnowledgeBase, listSources, type KnowledgeBase, type SourceVersion } from "../api/knowledge";
import FilePicker from "../components/FilePicker.vue";

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

async function load() {
  phase.value = "loading";
  try {
    base.value = await getKnowledgeBase(baseId.value);
    sources.value = await listSources(baseId.value);
    phase.value = "ready";
  } catch (error) {
    phase.value = "error";
    errorText.value = error instanceof ApiError ? error.message : "暂时不能连接服务。";
  }
}

async function submitFile() {
  notice.value = "";
  if (!name.value.trim() || !file.value) {
    notice.value = "请填写资料名称并选择文件";
    return;
  }
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
      <div class="page-actions"><router-link class="ghost" :to="{ name: 'knowledge' }">返回列表</router-link></div>
    </header>
    <div v-if="phase === 'loading'" class="card"><StatePanel kind="loading" text="正在读取知识库…" /></div>
    <div v-else-if="phase === 'error'" class="card"><StatePanel kind="error" title="知识库没有载入" :text="errorText"><button type="button" class="ghost" @click="load">重试</button></StatePanel></div>
    <template v-else>
      <nav class="tabs" aria-label="知识库内容">
        <button type="button" :class="{ on: tab === 'materials' }" :aria-current="tab === 'materials' ? 'page' : undefined" @click="tab = 'materials'">资料<span class="count">{{ sources.length }}</span></button>
        <button type="button" :class="{ on: tab === 'search' }" :aria-current="tab === 'search' ? 'page' : undefined" @click="tab = 'search'">检索</button>
      </nav>
      <div v-if="tab === 'search'" class="card">
        <StatePanel title="检索尚未接入" text="未发布和未解析的资料不会出现在检索结果里。" />
      </div>
      <div v-else class="card kb-card">
        <form class="query upload" @submit.prevent="submitFile">
          <div class="query-fields">
            <label><span>资料名称</span><input v-model="name" class="control" type="text" :disabled="submitting" /></label>
            <label><span>文件</span><FilePicker class="file" :file="file" :disabled="submitting" @change="picked => { file = picked; }" /></label>
          </div>
          <div class="query-actions"><button type="submit" class="primary" :disabled="submitting">{{ submitting ? "正在登记" : "登记资料" }}</button></div>
        </form>
        <p v-if="notice" class="notice" role="status">{{ notice }}</p>
        <StatePanel v-if="sources.length === 0" title="还没有资料" text="选择文件并登记后，资料会出现在这里。" />
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
      </div>
    </template>
  </section>
</template>

<style scoped>
.page-desc { display: flex; align-items: center; gap: var(--space-2); }
.summary { color: var(--text-secondary); }
.kb-card { overflow: hidden; }
.kb-table { flex: 1; min-height: 0; overflow-y: auto; }
.kb-table table { table-layout: fixed; }
.col-ver { width: 10%; }
.col-state { width: 12%; }
.kb-table td { white-space: normal; overflow-wrap: anywhere; }
.upload .file { width: calc(var(--space-7) * 9); }
</style>
