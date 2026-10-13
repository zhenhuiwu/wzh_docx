<script setup lang="ts">
/** 从内置模板创建文档（30 §3.1）：模板已预选，名称默认取模板名，上游按模板依赖关系展示。 */
import { nextTick, ref, watch } from "vue";
import { ApiError } from "../api/client";
import { createDocument } from "../api/documents";
import type { ChainNode } from "../api/docChain";

const props = defineProps<{ projectId: string; node: ChainNode | null; upstreamNames: string[] }>();
const emit = defineEmits<{ (e: "close"): void; (e: "created", documentId: string): void }>();
const dialog = ref<HTMLDialogElement | null>(null);
const name = ref("");
const nameError = ref("");
const formError = ref("");
const busy = ref(false);

watch(() => props.node, (node) => {
  if (!node) { if (dialog.value?.open) dialog.value.close(); return; }
  name.value = node.name;
  nameError.value = "";
  formError.value = "";
  void nextTick(() => { dialog.value?.showModal(); (dialog.value?.querySelector("input") as HTMLInputElement | null)?.focus(); });
});

function close() { if (!busy.value) emit("close"); }

async function submit() {
  const node = props.node;
  if (!node || busy.value) return;
  const value = name.value.trim();
  if (!value) { nameError.value = "请填写文档名称。"; return; }
  if (value.length > 120) { nameError.value = "文档名称不能超过 120 个字。"; return; }
  if (!node.templateDocumentId) { formError.value = "内置模板还没有初始化，请联系管理员在模板管理中初始化。"; return; }
  busy.value = true;
  formError.value = "";
  try {
    const doc = await createDocument(props.projectId, { name: value, sourceType: "template", templateDocumentId: node.templateDocumentId });
    emit("created", doc.id);
  } catch (error) {
    const message = error instanceof ApiError ? error.message : "暂时不能连接服务。";
    if (error instanceof ApiError && error.code === "DUPLICATE_NAME") nameError.value = message;
    else formError.value = message;
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <dialog ref="dialog" class="app-dialog chain-create" aria-labelledby="chain-create-title" @cancel.prevent="close">
    <form v-if="node" novalidate :aria-busy="busy" @submit.prevent="submit">
      <div class="dialog-head"><h2 id="chain-create-title">从模板创建</h2><button type="button" class="subtle icon-close" aria-label="关闭" :disabled="busy" @click="close"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg></button></div>
      <div class="dialog-body body">
        <div class="field"><span class="label">模板</span><strong data-testid="chain-create-template">{{ node.name }}</strong><span class="muted">共 {{ node.total }} 章</span></div>
        <label class="field"><span class="label required">文档名称</span>
          <input v-model="name" class="control" type="text" maxlength="120" :disabled="busy" :aria-invalid="nameError ? 'true' : undefined" aria-describedby="chain-create-name-error" @input="nameError = ''" />
          <span v-if="nameError" id="chain-create-name-error" class="field-error" role="alert">{{ nameError }}</span>
        </label>
        <div class="field"><span class="label">上游文档</span>
          <span v-if="upstreamNames.length">{{ upstreamNames.join("、") }}<span class="muted">（按模板依赖关系预填）</span></span>
          <span v-else class="muted">无，这是依赖链的第一份文档。</span>
        </div>
        <p v-if="formError" class="form-alert" role="alert">{{ formError }}</p>
      </div>
      <div class="dialog-actions"><button type="button" class="ghost" :disabled="busy" @click="close">取消</button><button type="submit" class="primary" :disabled="busy">{{ busy ? "正在创建…" : "创建" }}</button></div>
    </form>
  </dialog>
</template>

<style scoped>
.chain-create { width:640px; }
.body { display:grid; gap:var(--space-4); }
.field { display:grid; gap:var(--space-1); }
.label { color:var(--text-secondary); font-size:var(--text-sm); }
.muted { color:var(--text-tertiary); font-size:var(--text-sm); margin-left:var(--space-2); }
</style>
