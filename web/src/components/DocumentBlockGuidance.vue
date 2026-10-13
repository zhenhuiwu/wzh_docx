<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import StatePanel from "./StatePanel.vue";
import { queryWritingGuidance, updateWritingGuidance, type DocumentContentResult, type WritingGuidanceItem } from "../api/documents";

const props = defineProps<{ documentId: string; revision: number; objectIds: string[]; disabled?: boolean }>();
const emit = defineEmits<{ saved: [result: DocumentContentResult]; busy: [value: boolean]; dirty: [value: boolean] }>();
const items = ref<WritingGuidanceItem[]>([]);
const activeItem = ref("");
const itemLabel = (item: WritingGuidanceItem, index: number) => `${index + 1}. ${(item.targetText || "正文内容").replace(/\s+/g, " ").slice(0, 48)}`;
const edits = ref<Record<string, string>>({});
const loading = ref(false);
const saving = ref(false);
const error = ref("");
const warning = ref("");
const notices = ref<string[]>([]);
const saved = ref(false);
const loadedDocument = ref("");
const loadedRevision = ref<number | null>(null);
const loadedIds = ref<string[]>([]);
const isDirty = computed(() => items.value.some(item => edits.value[item.id] !== item.text));
const currentKey = computed(() => [...new Set(props.objectIds)].sort().join(","));
const stale = computed(() => loadedRevision.value !== null && (loadedDocument.value !== props.documentId
  || loadedRevision.value !== props.revision || loadedIds.value.slice().sort().join(",") !== currentKey.value));
const invalid = computed(() => items.value.some(item => (edits.value[item.id] ?? "").length > 20000));
const locked = computed(() => Boolean(props.disabled || loading.value || saving.value || stale.value));
let request = 0;
let controller: AbortController | undefined;

async function load() {
  if (saving.value) return;
  if (isDirty.value) { warning.value = "编写建议尚未保存，请先保存或放弃更改。"; return; }
  const token = ++request;
  controller?.abort(); controller = new AbortController();
  const documentId = props.documentId;
  const revision = props.revision;
  const objectIds = [...new Set(props.objectIds)];
  loading.value = objectIds.length > 0;
  items.value = []; edits.value = {}; error.value = ""; warning.value = ""; saved.value = false; notices.value = [];
  loadedDocument.value = documentId; loadedRevision.value = null; loadedIds.value = objectIds;
  if (!objectIds.length) return;
  try {
    const result = await queryWritingGuidance(documentId, revision, "objects", null, undefined, controller.signal, objectIds);
    if (token !== request) return;
    const ids = new Set(objectIds.map(id => `object:${id}`));
    items.value = result.groups.flatMap(group => group.items).filter(item => ids.has(item.id));
    edits.value = Object.fromEntries(items.value.map(item => [item.id, item.text]));
    if (!items.value.some(item => item.id === activeItem.value)) activeItem.value = items.value[0]?.id ?? "";
    notices.value = result.notices ?? [];
    loadedRevision.value = result.revision;
  } catch (e) { if (token === request) error.value = e instanceof Error ? e.message : "读取编写建议失败，请重试。"; }
  finally { if (token === request) loading.value = false; }
}
async function save() {
  if (locked.value || !isDirty.value || invalid.value || loadedRevision.value === null) return;
  const changes = items.value.filter(item => edits.value[item.id] !== item.text)
    .map(item => ({ id: item.id, text: edits.value[item.id].trim() || null }));
  saving.value = true; emit("busy", true); error.value = ""; warning.value = ""; saved.value = false;
  try {
    const result = await updateWritingGuidance(loadedDocument.value, loadedRevision.value, changes);
    for (const item of items.value) {
      const change = changes.find(entry => entry.id === item.id);
      if (change) { item.text = change.text ?? item.sourceText; edits.value[item.id] = item.text; }
    }
    loadedRevision.value = result.revision;
    saved.value = true; emit("saved", result);
  } catch (e) { error.value = e instanceof Error ? e.message : "保存编写建议失败，请重试。"; }
  finally { saving.value = false; emit("busy", false); }
}
function requestClose() {
  if (saving.value) return false;
  if (isDirty.value) { warning.value = "编写建议尚未保存，请先保存或放弃更改。"; return false; }
  return true;
}
function discard() {
  if (saving.value) return;
  edits.value = Object.fromEntries(items.value.map(item => [item.id, item.text]));
  void load();
}
watch(isDirty, value => { emit("dirty", value); if (value) saved.value = false; });
watch(() => [props.documentId, props.revision, currentKey.value], () => void load());
onMounted(() => void load());
onUnmounted(() => { request++; controller?.abort(); emit("dirty", false); emit("busy", false); });
defineExpose({ isDirty, requestClose, discard });
</script>

<template>
  <section class="block-guidance" aria-label="所选内容的编写建议">
    <h3>编写建议</h3>
    <StatePanel v-if="loading" kind="loading" title="正在读取编写建议" />
    <StatePanel v-else-if="!objectIds.length && !isDirty" kind="empty" title="选择需要编写的正文" text="在上方选择段落或表格，再设置编写建议。" />
    <StatePanel v-else-if="error && !items.length" kind="error" :text="error">
      <button type="button" class="ghost sm" :disabled="saving" @click="load">重试</button>
    </StatePanel>
    <template v-else>
      <p v-if="warning || stale" class="notice" role="alert">{{ stale ? '文档或选择范围已变化，更改尚未提交。请保留所需文字，放弃更改后重新读取。' : warning }}</p>
      <p v-if="error" class="form-alert" role="alert">{{ error }}</p>
      <p v-for="notice in notices" :key="notice" class="notice">{{ notice }}</p>
      <StatePanel v-if="!items.length" kind="empty" title="所选内容暂无可编辑的建议" />
      <div v-if="items.length > 1" class="field">
        <label for="block-guidance-target">编辑哪一项</label>
        <select id="block-guidance-target" v-model="activeItem" class="control" :disabled="saving">
          <option v-for="(item, index) in items" :key="item.id" :value="item.id">{{ itemLabel(item, index) }}{{ edits[item.id] !== item.text ? '（未保存）' : '' }}</option>
        </select>
      </div>
      <div v-for="(item, index) in items" v-show="item.id === activeItem" :key="item.id" class="block-guidance-item field">
        <label :for="`block-guidance-${index}`">{{ items.length > 1 ? `内容 ${index + 1} 的编写建议` : '本段编写建议' }}</label>
        <details v-if="item.targetText">
          <summary>查看对应正文</summary>
          <p class="block-source">{{ item.targetText }}</p>
        </details>
        <textarea :id="`block-guidance-${index}`" v-model="edits[item.id]" class="control" rows="7" :disabled="locked"
          :aria-invalid="(edits[item.id]?.length ?? 0) > 20000" :aria-describedby="(edits[item.id]?.length ?? 0) > 20000 ? `block-guidance-error-${index}` : undefined" />
        <p v-if="(edits[item.id]?.length ?? 0) > 20000" :id="`block-guidance-error-${index}`" class="field-error" role="alert">编写建议不能超过 20000 字。</p>
      </div>
      <div v-if="items.length" class="block-guidance-actions">
        <span v-if="saved" class="hint" role="status">编写建议已保存</span>
        <button v-if="isDirty" type="button" class="ghost sm" :disabled="saving" @click="discard">放弃建议更改</button>
        <button type="button" class="primary sm" :disabled="locked || !isDirty || invalid" @click="save">{{ saving ? '保存中…' : '保存编写建议' }}</button>
      </div>
    </template>
  </section>
</template>

<style scoped>
.block-guidance { display: grid; gap: var(--space-3); min-width: 0; }
.block-guidance h3 { margin: 0; font-size: var(--text-md); font-weight: var(--weight-semibold); }
.block-guidance-item { min-width: 0; }
.block-guidance .control { width: 100%; min-width: 0; }
.block-guidance-actions .primary { margin-left: auto; }
.block-guidance-item textarea { width: 100%; resize: vertical; }
.block-guidance-item summary { cursor: pointer; color: var(--text-secondary); font-size: var(--text-sm); }
.block-guidance-item summary:focus-visible { box-shadow: var(--focus-shadow); outline: none; }
.block-source { margin: var(--space-2) 0; color: var(--text-secondary); white-space: pre-wrap; overflow-wrap: anywhere; font-size: var(--text-sm); line-height: var(--leading-sm); }
.block-guidance-actions { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-2); }
</style>
