<script setup lang="ts">
/** A06 上传新 Word 作为草稿（16 §3.3，L4 的替代路径）。已有草稿时先确认替换。 */
import { ref } from "vue";
import { useDialogFocus } from "../../dialogFocus";
import { ApiError } from "../../api/client";
import { uploadFile } from "../../api/files";
import { uploadTemplateDraft, type DraftPayload } from "../../api/templates";
import FilePicker from "../FilePicker.vue";

const props = defineProps<{ documentId: string; draftRevision: number; hasDraft: boolean }>();
const emit = defineEmits<{ close: []; uploaded: [payload: DraftPayload] }>();
const dialog = ref<HTMLDialogElement | null>(null);
const file = ref<File | null>(null);
const fileError = ref("");
const formError = ref("");
const confirmReplace = ref(false);
const submitting = ref(false);
let uploadedId = "";

function choose(next: File | null) { file.value = next; uploadedId = ""; fileError.value = ""; formError.value = ""; }

async function submit() {
  if (submitting.value) return;
  formError.value = "";
  fileError.value = !file.value ? "请选择 Word 文件。" : !/\.docx$/i.test(file.value.name) ? "请选择 .docx 格式的 Word 文件。" : "";
  if (fileError.value || !file.value) return;
  if (props.hasDraft && !confirmReplace.value) { confirmReplace.value = true; return; }
  submitting.value = true;
  try {
    if (!uploadedId) uploadedId = (await uploadFile(file.value, "template", { idempotencyKey: crypto.randomUUID() })).id;
    emit("uploaded", await uploadTemplateDraft(props.documentId, uploadedId, props.draftRevision));
  } catch (error) {
    if (error instanceof ApiError && error.code === "TEMPLATE_UNCHANGED") formError.value = "与当前版本相比没有变化，无需上传。";
    else if (error instanceof ApiError && error.code === "REVISION_CONFLICT") formError.value = "草稿刚被其他人修改，请关闭后刷新再上传。";
    else formError.value = error instanceof Error ? error.message : "暂时不能连接服务。";
  } finally { submitting.value = false; }
}
useDialogFocus(dialog, { initial: '[data-testid="tpl-upload-file"]', fallback: '[data-testid="template-upload"]' });
</script>

<template>
  <dialog ref="dialog" class="app-dialog" aria-labelledby="tpl-upload-title" data-testid="tpl-upload-dialog" @cancel.prevent="!submitting && emit('close')">
    <header class="dialog-head">
      <h2 id="tpl-upload-title">上传新 Word 作为草稿</h2>
      <button type="button" class="subtle" aria-label="关闭" :disabled="submitting" @click="emit('close')">×</button>
    </header>
    <form class="dialog-body upload-body" novalidate @submit.prevent="submit">
      <p class="dialog-text">上传修改后的 Word，系统会与当前版本逐段对齐，沿用已有的编写指导。对不上的指导需要你确认位置。</p>
      <p v-if="formError" class="form-alert" role="alert" data-testid="tpl-upload-error">{{ formError }}</p>
      <label class="field">
        <span class="required">Word 文件</span>
        <FilePicker id="tpl-upload-file" accept=".docx" :file="file" :invalid="Boolean(fileError)" :disabled="submitting" data-testid="tpl-upload-file" @change="(next) => choose(next)" />
        <span v-if="fileError" class="field-error" role="alert">{{ fileError }}</span>
      </label>
      <p v-if="confirmReplace" class="notice warning replace" role="status">当前草稿有未发布的修改，上传会替换草稿。</p>
      <footer class="actions">
        <button type="button" class="ghost" :disabled="submitting" @click="emit('close')">取消</button>
        <button type="submit" class="primary" :disabled="submitting" data-testid="tpl-upload-submit">{{ submitting ? '正在解析…' : confirmReplace ? '替换草稿' : '上传并对齐' }}</button>
      </footer>
    </form>
  </dialog>
</template>

<style scoped>
.upload-body { display:grid; gap:var(--space-4); }
.upload-body .dialog-text { margin:0; }
.replace { border:0; border-radius:var(--radius-control); }
.actions { display:flex; justify-content:flex-end; gap:var(--space-2); padding-top:var(--space-2); border-top:1px solid var(--border-subtle); }
</style>
