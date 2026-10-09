<script setup lang="ts">
import { onMounted, ref } from "vue";
import { ApiError } from "../api/client";
import { createTemplate, listTemplates, templateStatusLabel, type TemplateRecord } from "../api/templates";

const page = ref(1);
const nameQuery = ref("");
const appliedName = ref("");
const items = ref<TemplateRecord[]>([]);
const total = ref(0);
const phase = ref<"loading" | "ready" | "error">("loading");
const errorText = ref("");
const creating = ref(false);
const submitting = ref(false);
const name = ref("");
const category = ref("");
const description = ref("");
const formError = ref("");
const pageCount = () => Math.max(1, Math.ceil(total.value / 20));

function messageOf(error: unknown) {
  return error instanceof ApiError ? error.message : "暂时不能连接服务。";
}

async function load() {
  phase.value = "loading";
  try {
    const result = await listTemplates(page.value, appliedName.value);
    items.value = result.items;
    total.value = result.total;
    phase.value = "ready";
  } catch (error) {
    phase.value = "error";
    errorText.value = messageOf(error);
  }
}

function search() {
  appliedName.value = nameQuery.value.trim();
  page.value = 1;
  void load();
}

function resetQuery() {
  nameQuery.value = "";
  search();
}

async function submitCreate() {
  formError.value = "";
  if (!name.value.trim() || !category.value.trim()) {
    formError.value = "请填写模板名称和分类";
    return;
  }
  submitting.value = true;
  try {
    await createTemplate({
      name: name.value.trim(),
      category: category.value.trim(),
      description: description.value.trim(),
    });
    creating.value = false;
    name.value = "";
    category.value = "";
    description.value = "";
    page.value = 1;
    await load();
  } catch (error) {
    formError.value = messageOf(error);
  } finally {
    submitting.value = false;
  }
}

onMounted(() => void load());
</script>

<template>
  <section class="card">
    <div class="head">
      <h1>模板管理</h1>
      <button v-if="!creating" type="button" class="primary" @click="creating = true">新建模板组</button>
    </div>
    <p v-if="phase === 'error'" class="notice" role="alert">{{ errorText }}</p>
    <form class="query" @submit.prevent="search">
      <div class="query-fields">
        <label><span>模板名称</span><input v-model="nameQuery" class="control" type="search" /></label>
      </div>
      <div class="query-actions">
        <button type="submit" class="primary">查询</button>
        <button type="button" class="ghost" @click="resetQuery">重置</button>
      </div>
    </form>
    <form v-if="creating" class="create" @submit.prevent="submitCreate">
      <label>模板名称<input v-model="name" class="control" type="text" :disabled="submitting" /></label>
      <label>分类<input v-model="category" class="control" type="text" :disabled="submitting" /></label>
      <label>描述<textarea v-model="description" class="control" rows="3" :disabled="submitting"></textarea></label>
      <p v-if="formError" class="field-error" role="alert">{{ formError }}</p>
      <div class="actions">
        <button type="submit" class="primary" :disabled="submitting">{{ submitting ? "正在创建" : "创建" }}</button>
        <button type="button" class="ghost" @click="creating = false">取消</button>
      </div>
    </form>
    <p v-if="phase === 'loading'" class="empty">正在读取模板</p>
    <p v-else-if="phase === 'error'" class="empty"><button type="button" class="ghost" @click="load">重试</button></p>
    <p v-else-if="items.length === 0" class="empty">还没有模板组。</p>
    <div v-else class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>名称</th>
            <th>分类</th>
            <th>文档数</th>
            <th>状态</th>
            <th><span class="sr">操作</span></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in items" :key="item.id">
            <td><router-link class="link" :to="{ name: 'template', params: { id: item.id } }">{{ item.name }}</router-link></td>
            <td>{{ item.category }}</td>
            <td>{{ item.documentCount }}</td>
            <td>{{ templateStatusLabel[item.status] }}</td>
            <td><router-link class="link" :to="{ name: 'template', params: { id: item.id } }">详情</router-link></td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-if="phase === 'ready' && total > 20" class="pager">
      <span>共 {{ total }} 条</span>
      <button type="button" class="page" :disabled="page <= 1" @click="page -= 1; load()">上一页</button>
      <button type="button" class="page" :disabled="page >= pageCount()" @click="page += 1; load()">下一页</button>
    </div>
  </section>
</template>

<style scoped>
.create { display: grid; gap: var(--space-3); margin: var(--space-4) 0; max-width: 600px; }
.create label { display: grid; gap: var(--space-1); }
.actions { display: flex; gap: var(--space-2); }
</style>
