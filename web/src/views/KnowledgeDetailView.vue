<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { ApiError } from "../api/client";
import { uploadFile } from "../api/files";
import { createSource, getKnowledgeBase, listSources, type KnowledgeBase, type SourceVersion } from "../api/knowledge";

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
  <section class="card">
    <div class="head">
      <h1>{{ base?.name || "知识库" }}</h1>
      <router-link class="ghost" :to="{ name: 'knowledge' }">返回列表</router-link>
    </div>
    <p v-if="phase === 'loading'" class="empty">正在读取</p>
    <p v-else-if="phase === 'error'" class="notice" role="alert">{{ errorText }}</p>
    <template v-else>
      <div class="tabs">
        <button type="button" :class="{ on: tab === 'materials' }" @click="tab = 'materials'">资料</button>
        <button type="button" :class="{ on: tab === 'search' }" @click="tab = 'search'">检索</button>
      </div>
      <div v-if="tab === 'search'">
        <p class="empty">检索尚未接入。未发布和未解析的资料不会出现在检索结果里。</p>
      </div>
      <div v-else>
        <form class="create" @submit.prevent="submitFile">
          <label>资料名称<input v-model="name" class="control" type="text" :disabled="submitting" /></label>
          <label>文件<input class="control" type="file" :disabled="submitting" @change="file = ($event.target as HTMLInputElement).files?.[0] ?? null" /></label>
          <button type="submit" class="primary" :disabled="submitting">{{ submitting ? "正在登记" : "登记资料" }}</button>
        </form>
        <p v-if="notice" class="notice" role="status">{{ notice }}</p>
        <p v-if="sources.length === 0" class="empty">还没有资料</p>
        <div v-else class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>资料名</th>
                <th>版本</th>
                <th>状态</th>
                <th class="from-mid">说明</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in sources" :key="item.id">
                <td>{{ item.name }}</td>
                <td>{{ item.version }}</td>
                <td>{{ item.status === "uploaded" ? "已上传" : item.status }}</td>
                <td class="from-mid">{{ item.message || "—" }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.create { display: grid; gap: 8px; max-width: 420px; margin-bottom: 16px; }
.create label { display: grid; gap: 4px; }
.tabs { display: flex; gap: 8px; margin-bottom: 12px; }
.tabs button.on { border-color: var(--stamp); }
@media (max-width: 767px) {
  .from-mid { display: none; }
}
</style>
