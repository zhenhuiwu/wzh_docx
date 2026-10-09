<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { ApiError } from "../api/client";
import { uploadFile } from "../api/files";
import {
  getTemplate,
  listTemplateDocuments,
  createTemplateDocument,
  deleteTemplateDocument,
  type TemplateRecord,
  type TemplateDocument,
  templateStatusLabel,
} from "../api/templates";

const route = useRoute();
const templateId = computed(() => {
  const id = route.params.id;
  return Array.isArray(id) ? id[0] : id;
});

const template = ref<TemplateRecord | null>(null);
const documents = ref<TemplateDocument[]>([]);
const phase = ref<"loading" | "ready" | "error">("loading");
const errorText = ref("");
const adding = ref(false);
const submitting = ref(false);
const docName = ref("");
const docDescription = ref("");
const docFile = ref<File | null>(null);
const formError = ref("");

function messageOf(error: unknown) {
  return error instanceof ApiError ? error.message : "暂时不能连接服务。";
}

async function load() {
  if (!templateId.value) return;
  phase.value = "loading";
  try {
    template.value = await getTemplate(templateId.value);
    const result = await listTemplateDocuments(templateId.value);
    documents.value = result.items;
    phase.value = "ready";
  } catch (error) {
    phase.value = "error";
    errorText.value = messageOf(error);
  }
}

async function submitAdd() {
  formError.value = "";
  if (!docName.value.trim()) {
    formError.value = "请填写文档名称";
    return;
  }
  if (!docFile.value) {
    formError.value = "请选择 Word 文件";
    return;
  }

  submitting.value = true;
  try {
    const uploaded = await uploadFile(docFile.value, "template", { idempotencyKey: crypto.randomUUID() });

    await createTemplateDocument(templateId.value, {
      name: docName.value.trim(),
      description: docDescription.value.trim(),
      displayOrder: documents.value.length,
      wordFileId: uploaded.id,
    });

    adding.value = false;
    docName.value = "";
    docDescription.value = "";
    docFile.value = null;
    await load();
  } catch (error) {
    formError.value = messageOf(error);
  } finally {
    submitting.value = false;
  }
}

async function removeDocument(doc: TemplateDocument) {
  if (!confirm(`确定要从模板组中移除"${doc.name}"吗？`)) return;
  try {
    await deleteTemplateDocument(doc.id);
    await load();
  } catch (error) {
    alert(messageOf(error));
  }
}

onMounted(() => void load());
</script>

<template>
  <section class="card">
    <div class="head">
      <h1>模板详情</h1>
      <router-link class="ghost" :to="{ name: 'templates' }">返回列表</router-link>
    </div>

    <div v-if="phase === 'loading'" class="empty">正在加载...</div>
    <div v-else-if="phase === 'error'" class="empty">
      <p>{{ errorText }}</p>
      <button type="button" class="ghost" @click="load">重试</button>
    </div>
    <div v-else-if="template">
      <div class="info">
        <div class="info-row">
          <span class="label">名称</span>
          <span>{{ template.name }}</span>
        </div>
        <div class="info-row">
          <span class="label">分类</span>
          <span>{{ template.category }}</span>
        </div>
        <div class="info-row">
          <span class="label">描述</span>
          <span>{{ template.description || "—" }}</span>
        </div>
        <div class="info-row">
          <span class="label">状态</span>
          <span>{{ templateStatusLabel[template.status] }}</span>
        </div>
      </div>

      <div class="section-head">
        <h2>文档列表</h2>
        <button v-if="!adding" type="button" class="primary" @click="adding = true">添加文档</button>
      </div>

      <form v-if="adding" class="add-form" @submit.prevent="submitAdd">
        <label>文档名称<input v-model="docName" class="control" type="text" :disabled="submitting" /></label>
        <label>描述<input v-model="docDescription" class="control" type="text" :disabled="submitting" /></label>
        <label>Word 文件<input class="control" type="file" accept=".docx,.doc" :disabled="submitting" @change="docFile = ($event.target as HTMLInputElement).files?.[0] ?? null" /></label>
        <p v-if="formError" class="field-error" role="alert">{{ formError }}</p>
        <div class="actions">
          <button type="submit" class="primary" :disabled="submitting">{{ submitting ? "正在添加" : "添加" }}</button>
          <button type="button" class="ghost" @click="adding = false">取消</button>
        </div>
      </form>

      <p v-if="documents.length === 0" class="empty">模板组中还没有文档。</p>
      <div v-else class="doc-list">
        <div v-for="doc in documents" :key="doc.id" class="doc-item">
          <div class="doc-info">
            <div class="doc-name">{{ doc.name }}</div>
            <div v-if="doc.description" class="doc-desc">{{ doc.description }}</div>
          </div>
          <button type="button" class="ghost" @click="removeDocument(doc)">移除</button>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.info {
  display: grid;
  gap: var(--space-3);
  margin: var(--space-4) 0;
  padding: var(--space-4);
  background: var(--inset);
  border-radius: var(--radius-sheet);
}

.info-row {
  display: grid;
  grid-template-columns: 120px 1fr;
  gap: var(--space-3);
}

.label {
  color: var(--ink-secondary);
  font-size: var(--text-sm);
}

.section-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin: var(--space-5) 0 var(--space-4);
}

.section-head h2 {
  margin: 0;
  font-size: var(--text-md);
  font-weight: 600;
}

.add-form {
  display: grid;
  gap: var(--space-3);
  margin-bottom: var(--space-4);
  max-width: 600px;
}

.add-form label {
  display: grid;
  gap: var(--space-1);
}

.actions {
  display: flex;
  gap: var(--space-2);
}

.doc-list {
  display: grid;
  gap: var(--space-2);
}

.doc-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--space-3);
  background: var(--sheet);
  border: 1px solid var(--rule);
  border-radius: var(--radius-control);
}

.doc-info {
  flex: 1;
}

.doc-name {
  font-weight: 500;
  margin-bottom: var(--space-1);
}

.doc-desc {
  font-size: var(--text-sm);
  color: var(--ink-secondary);
}
</style>
