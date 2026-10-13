<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import StatePanel from "./StatePanel.vue";
import DOMPurify from "dompurify";
import { getDocumentReferences, listReferenceDocuments, getReferencePreview, getReferenceDocumentPreview, updateDocumentReferences,
  type DocumentReference, type DocumentReferencesState, type ReferenceDocument, type ReferenceDocumentPage, type ReferenceInput, type ReferencePreview } from "../api/documentReferences";

const props = defineProps<{ documentId: string; revision: number; disabled?: boolean }>();
const emit = defineEmits<{ saved: [state: DocumentReferencesState]; close: []; busy: [value: boolean]; dirty: [value: boolean] }>();
const state = ref<DocumentReferencesState | null>(null);
const ownerId = ref(props.documentId);
const items = ref<DocumentReference[]>([]);
const loading = ref(false);
const saving = ref(false);
const error = ref("");
const saved = ref(false);
const discardPrompt = ref(false);
const picker = ref<"" | "document" | "information">("");
const name = ref("");
const documentPage = ref<ReferenceDocumentPage | null>(null);
const pickerLoading = ref(false);
const pickerError = ref("");
const informationName = ref("");
const informationContent = ref("");
const informationNote = ref("");
const informationError = ref("");
const preview = ref<ReferencePreview | null>(null);
const previewTarget = ref<DocumentReference | ReferenceDocument | null>(null);
const previewLoading = ref(false);
const previewError = ref("");
const previewElement = ref<HTMLElement | null>(null);
const previewHtml = computed(() => DOMPurify.sanitize(preview.value?.html || "", { FORBID_TAGS: ["img", "iframe", "object", "embed", "form", "input", "button", "textarea"] }));
let request = 0;
let pickerRequest = 0;
let previewRequest = 0;
const toInput = (item: DocumentReference): ReferenceInput => item.kind === "document"
  ? { id: item.id, kind: "document", documentId: item.documentId!, sourceRevision: item.sourceRevision!, note: item.note }
  : { id: item.id, kind: "information", name: item.name, content: item.content ?? "", note: item.note };
const itemsDirty = computed(() => Boolean(state.value && JSON.stringify(items.value.map(toInput)) !== JSON.stringify(state.value.items.map(toInput))));
const informationDirty = computed(() => Boolean(informationName.value || informationContent.value || informationNote.value));
const isDirty = computed(() => itemsDirty.value || informationDirty.value);
const wrongDocument = computed(() => ownerId.value !== props.documentId);
const stale = computed(() => Boolean(state.value && (wrongDocument.value || state.value.revision !== props.revision)));
const locked = computed(() => Boolean(props.disabled || loading.value || saving.value || stale.value || !state.value?.canEdit));
const pageCount = computed(() => Math.max(1, Math.ceil((documentPage.value?.total ?? 0) / 8)));
const displayName = (item: DocumentReference) => item.available ? item.name : "参考文档不可用";
const message = (e: unknown) => e instanceof Error ? e.message : "请求没有完成，请重试。";
const hasDocument = (id: string) => items.value.some(item => item.kind === "document" && item.documentId === id);
function clearInformation() { informationName.value = ""; informationContent.value = ""; informationNote.value = ""; informationError.value = ""; }
function clearPreview() { previewRequest++; preview.value = null; previewTarget.value = null; previewError.value = ""; previewLoading.value = false; }
function accept(result: DocumentReferencesState, documentId = props.documentId) {
  state.value = result; ownerId.value = documentId;
  items.value = result.items.map(item => ({ ...item }));
  clearInformation(); clearPreview(); discardPrompt.value = false;
}
async function load() {
  if (saving.value) return;
  const token = ++request;
  const documentId = props.documentId;
  loading.value = true; error.value = ""; saved.value = false;
  try {
    const result = await getDocumentReferences(documentId);
    if (token === request && documentId === props.documentId) accept(result, documentId);
  } catch (e) { if (token === request && documentId === props.documentId) error.value = message(e); }
  finally { if (token === request) loading.value = false; }
}
async function queryDocuments(page = 1) {
  if (wrongDocument.value) return;
  const token = ++pickerRequest;
  const documentId = props.documentId;
  pickerLoading.value = true; pickerError.value = "";
  try {
    const result = await listReferenceDocuments(documentId, name.value.trim(), page);
    if (token === pickerRequest && documentId === props.documentId) documentPage.value = result;
  } catch (e) { if (token === pickerRequest && documentId === props.documentId) pickerError.value = message(e); }
  finally { if (token === pickerRequest) pickerLoading.value = false; }
}
function choosePicker(value: "document" | "information") {
  picker.value = picker.value === value ? "" : value;
  if (picker.value === "document" && !documentPage.value) void queryDocuments();
}
function addDocument(source: ReferenceDocument) {
  if (locked.value || items.value.length >= 10 || hasDocument(source.id)) return;
  items.value.push({ id: crypto.randomUUID(), kind: "document", documentId: source.id, sourceRevision: source.revision,
    latestRevision: source.revision, name: source.name, projectName: source.projectName, note: "", available: true });
  saved.value = false;
}
function addInformation() {
  if (locked.value || items.value.length >= 10) return;
  informationError.value = "";
  if (!informationName.value.trim() || !informationContent.value.trim()) { informationError.value = "请填写资料名称和原文。"; return; }
  if (informationName.value.trim().length > 120 || informationContent.value.length > 20000 || informationNote.value.length > 2000) {
    informationError.value = "名称最多 120 字，原文最多 20000 字，用途说明最多 2000 字。"; return;
  }
  items.value.push({ id: crypto.randomUUID(), kind: "information", name: informationName.value.trim(), content: informationContent.value,
    note: informationNote.value, available: true });
  clearInformation(); picker.value = ""; saved.value = false;
}
function remove(item: DocumentReference) {
  if (locked.value) return;
  items.value = items.value.filter(entry => entry.id !== item.id);
  if (previewTarget.value?.id === item.id) clearPreview();
  saved.value = false;
}
function useLatest(item: DocumentReference) {
  if (locked.value || !item.available || !item.latestRevision || item.latestRevision === item.sourceRevision) return;
  item.sourceRevision = item.latestRevision; saved.value = false;
  if (previewTarget.value?.id === item.id) clearPreview();
}
async function showPreview(target: DocumentReference | ReferenceDocument, more = false) {
  if (wrongDocument.value || ("available" in target && !target.available)) return;
  const token = ++previewRequest;
  const documentId = props.documentId;
  const offset = more ? preview.value?.nextOffset ?? 0 : 0;
  if (!more) { preview.value = null; previewTarget.value = target; }
  previewLoading.value = true; previewError.value = "";
  if (!more) { await nextTick(); previewElement.value?.scrollIntoView({ block: "start" }); }
  try {
    let result: ReferencePreview;
    if ("kind" in target && target.kind === "information") result = { name: target.name, content: target.content ?? "", hasMore: false, nextOffset: 0 };
    else if ("kind" in target) {
      const original = state.value?.items.find(item => item.id === target.id && item.sourceRevision === target.sourceRevision);
      result = original ? await getReferencePreview(documentId, target.id, offset)
        : await getReferenceDocumentPreview(documentId, target.documentId!, target.sourceRevision!, offset);
    } else result = await getReferenceDocumentPreview(documentId, target.id, target.revision, offset);
    if (token === previewRequest && documentId === props.documentId) preview.value = { ...result,
      content: (more ? preview.value?.content ?? "" : "") + result.content,
      html: (more ? preview.value?.html ?? "" : "") + (result.html || "") };
  } catch (e) { if (token === previewRequest && documentId === props.documentId) previewError.value = message(e); }
  finally { if (token === previewRequest) previewLoading.value = false; }
}
async function save() {
  if (locked.value || !itemsDirty.value || !state.value || informationDirty.value) return;
  error.value = "";
  if (items.value.length > 10 || items.value.some(item => item.note.length > 2000 || (item.kind === "information" && (!item.name.trim() || item.name.length > 120 || !item.content?.trim() || item.content.length > 20000)))) {
    error.value = "请检查资料：最多 10 项，用途说明最多 2000 字，粘贴资料须填写名称和原文。"; return;
  }
  const token = ++request;
  const documentId = props.documentId;
  saving.value = true; saved.value = false;
  try {
    const result = await updateDocumentReferences(documentId, { expectedRevision: state.value.revision, items: items.value.map(toInput) });
    if (token === request && documentId === props.documentId) { accept(result, documentId); saved.value = true; emit("saved", result); }
  } catch (e) { if (token === request && documentId === props.documentId) error.value = message(e); }
  finally { if (token === request) saving.value = false; }
}
function requestClose() {
  if (loading.value || saving.value) return;
  if (isDirty.value) discardPrompt.value = true;
  else emit("close");
}
function discard() {
  if (saving.value || loading.value) return;
  if (state.value) accept(state.value, ownerId.value);
  else clearInformation();
  if (wrongDocument.value || stale.value) void load();
}
function reload() { if (!isDirty.value) return load(); }
function changeDocument() {
  request++; pickerRequest++; clearPreview();
  loading.value = false; saving.value = false; pickerLoading.value = false;
  documentPage.value = null; pickerError.value = ""; error.value = ""; name.value = ""; picker.value = "";
  if (!isDirty.value) { state.value = null; items.value = []; ownerId.value = props.documentId; void load(); }
}
watch(() => props.documentId, changeDocument);
watch(() => props.revision, () => { if (state.value?.revision !== props.revision && !isDirty.value && !loading.value && !saving.value && !wrongDocument.value) void load(); });
watch(isDirty, value => { saved.value = false; emit("dirty", value); }, { flush: "sync" });
watch([loading, saving], () => emit("busy", loading.value || saving.value), { flush: "sync" });
onMounted(() => void load());
onUnmounted(() => { request++; pickerRequest++; previewRequest++; emit("busy", false); emit("dirty", false); });
defineExpose({ isDirty, requestClose, discard, reload });
</script>

<template>
  <aside class="references-panel" aria-label="参考资料">
    <header class="references-head"><h2>参考资料</h2><button type="button" class="subtle sm" :disabled="loading || saving" @click="requestClose">关闭</button></header>
    <div class="references-body">
      <div v-if="discardPrompt" class="notice" role="alert">
        <p>参考资料更改尚未保存。</p>
        <div class="reference-actions"><button type="button" class="ghost sm" @click="discardPrompt = false">继续编辑</button><button type="button" class="ghost sm danger" @click="discard(); emit('close')">放弃更改并关闭</button></div>
      </div>
      <div v-if="stale" class="notice" role="alert"><p>{{ wrongDocument ? '已切换文档，原文档的参考资料草稿仍保留。' : '文档已更新，参考资料草稿仍保留。请重新读取后修改。' }}</p><button type="button" class="ghost sm" :disabled="loading || saving" @click="load">{{ isDirty ? '放弃更改并重新读取' : '重新读取' }}</button></div>
      <StatePanel v-if="loading" kind="loading" title="正在读取参考资料" />
      <StatePanel v-else-if="error && (!state || wrongDocument)" kind="error" :text="error"><button type="button" class="ghost sm" @click="load">重试</button></StatePanel>
      <template v-else-if="state && !wrongDocument">
        <div v-if="error" class="form-alert" role="alert">{{ error }}</div>
        <p class="hint reference-intro">参考文档固定使用所选修订。</p>
        <StatePanel v-if="!state.canEdit" kind="denied" title="参考资料只读" text="当前没有修改此文档参考资料的权限。" />
        <div class="reference-section-head"><h3>已选资料</h3><span class="count">{{ items.length }} / 10</span></div>
        <StatePanel v-if="!items.length" kind="empty" title="暂无参考资料" text="选择已有文档或粘贴信息，补充编写依据。" />
        <ul v-else class="references-list">
          <li v-for="item in items" :key="item.id" class="reference-item">
            <div class="reference-section-head"><strong>{{ displayName(item) }}</strong><span class="tag">{{ item.kind === 'document' ? '文档' : '信息' }}</span></div>
            <p v-if="item.kind === 'document' && item.available" class="hint">{{ item.projectName }} · 所选修订 {{ item.sourceRevision }}</p>
            <p v-if="!item.available" class="hint">来源已删除或当前无权访问，原文不可查看。</p>
            <div v-if="item.available && item.latestRevision && item.latestRevision !== item.sourceRevision" class="notice"><p>来源已有修订 {{ item.latestRevision }}。</p><button type="button" class="ghost sm" :disabled="locked" @click="useLatest(item)">更新到最新修订</button></div>
            <div v-if="item.kind === 'information'" class="field"><label :for="`reference-name-${item.id}`" class="required">资料名称</label><input :id="`reference-name-${item.id}`" v-model="item.name" class="control" maxlength="120" :disabled="locked" /><p v-if="!item.name.trim()" class="field-error">请填写资料名称。</p></div>
            <div class="field"><label :for="`reference-note-${item.id}`">用途说明</label><textarea :id="`reference-note-${item.id}`" v-model="item.note" class="control" rows="2" maxlength="2000" :disabled="locked" placeholder="例如：作为术语、背景或章节写法的依据" /></div>
            <details v-if="item.kind === 'information'" class="reference-edit"><summary>编辑粘贴原文</summary><div class="field"><label :for="`reference-content-${item.id}`" class="required">资料原文</label><textarea :id="`reference-content-${item.id}`" v-model="item.content" class="control" rows="6" maxlength="20000" :disabled="locked" /><p v-if="!item.content?.trim()" class="field-error">请填写资料原文。</p></div></details>
            <div class="reference-actions"><button type="button" class="text sm" :disabled="!item.available || saving" @click="showPreview(item)">查看原文</button><button type="button" class="text sm danger" :disabled="locked" @click="remove(item)">移除</button></div>
          </li>
        </ul>
        <template v-if="state.canEdit">
          <div class="tabs reference-tabs" aria-label="添加参考资料方式"><button type="button" :class="{ active: picker === 'document' }" :aria-pressed="picker === 'document'" :disabled="locked" @click="choosePicker('document')">选择文档</button><button type="button" :class="{ active: picker === 'information' }" :aria-pressed="picker === 'information'" :disabled="locked" @click="choosePicker('information')">粘贴信息</button></div>
          <p v-if="items.length >= 10" class="hint">已达 10 项上限，请先移除不需要的资料。</p>
          <section v-if="picker === 'document'" aria-label="选择参考文档" class="reference-picker">
            <form class="reference-query" @submit.prevent="queryDocuments()"><div class="field"><label for="reference-search">文档名称</label><input id="reference-search" v-model="name" class="control" type="search" :disabled="pickerLoading || locked" placeholder="搜索有权查看的项目文档" /></div><button type="submit" class="ghost sm" :disabled="pickerLoading || locked">查询</button></form>
            <StatePanel v-if="pickerLoading" kind="loading" title="正在查询文档" />
            <StatePanel v-else-if="pickerError" kind="error" :text="pickerError"><button type="button" class="ghost sm" @click="queryDocuments(documentPage?.page ?? 1)">重试</button></StatePanel>
            <StatePanel v-else-if="!documentPage?.items.length" kind="empty" title="没有可选文档" text="可调整名称后重新查询。" />
            <ul v-else class="references-list"><li v-for="source in documentPage.items" :key="source.id" class="reference-source"><strong>{{ source.name }}</strong><p class="hint">{{ source.projectName }} · 修订 {{ source.revision }}</p><div class="reference-actions"><button type="button" class="text sm" :disabled="saving" @click="showPreview(source)">查看原文</button><button type="button" class="ghost sm" :disabled="locked || items.length >= 10 || hasDocument(source.id)" @click="addDocument(source)">{{ hasDocument(source.id) ? '已加入' : '加入' }}</button></div></li></ul>
            <nav v-if="documentPage && pageCount > 1" class="pager" aria-label="参考文档分页"><span>共 {{ documentPage.total }} 项 · {{ documentPage.page }} / {{ pageCount }}</span><button type="button" class="ghost sm" :disabled="pickerLoading || locked || documentPage.page <= 1" @click="queryDocuments(documentPage.page - 1)">上一页</button><button type="button" class="ghost sm" :disabled="pickerLoading || locked || documentPage.page >= pageCount" @click="queryDocuments(documentPage.page + 1)">下一页</button></nav>
          </section>
          <form v-if="picker === 'information'" class="reference-picker" @submit.prevent="addInformation">
            <div class="field"><label for="reference-information-name" class="required">资料名称</label><input id="reference-information-name" v-model="informationName" class="control" maxlength="120" :disabled="locked" /></div>
            <div class="field"><label for="reference-information-content" class="required">资料原文</label><textarea id="reference-information-content" v-model="informationContent" class="control" rows="7" maxlength="20000" :disabled="locked" placeholder="粘贴需参考的背景信息、要求或材料原文" /><p class="hint">最多 20000 字</p></div>
            <div class="field"><label for="reference-information-note">用途说明</label><textarea id="reference-information-note" v-model="informationNote" class="control" rows="2" maxlength="2000" :disabled="locked" /></div>
            <p v-if="informationError" class="field-error" role="alert">{{ informationError }}</p>
            <div class="reference-actions"><button type="submit" class="ghost sm" :disabled="locked || items.length >= 10">加入参考资料</button><button type="button" class="text sm" :disabled="locked" @click="clearInformation">清空</button></div>
          </form>
        </template>
        <section v-if="previewTarget" ref="previewElement" class="reference-preview" aria-label="参考资料原文">
          <div class="reference-section-head"><h3>原文预览</h3><button type="button" class="subtle sm" @click="clearPreview">收起</button></div>
          <StatePanel v-if="previewLoading && !preview" kind="loading" title="正在读取原文" />
          <StatePanel v-else-if="previewError" kind="error" :text="previewError"><button type="button" class="ghost sm" @click="showPreview(previewTarget, Boolean(preview))">重试</button></StatePanel>
          <template v-else-if="preview"><strong>{{ preview.name }}</strong><p v-if="preview.revision !== undefined && preview.revision !== null" class="hint">修订 {{ preview.revision }}</p><div v-if="previewHtml" class="reference-original reference-prose" v-html="previewHtml"></div><pre v-else-if="preview.content" class="reference-original">{{ preview.content }}</pre><StatePanel v-else kind="empty" title="原文暂无内容" /><button v-if="preview.hasMore" type="button" class="ghost sm" :disabled="previewLoading" @click="showPreview(previewTarget, true)">{{ previewLoading ? '读取中…' : '继续读取原文' }}</button></template>
        </section>
      </template>
    </div>
    <footer v-if="state?.canEdit && !wrongDocument" class="references-footer"><span class="hint" :role="saved ? 'status' : undefined">{{ informationDirty ? '请先将粘贴信息加入资料，再保存。' : saved ? '参考资料已保存' : isDirty ? '参考资料有未保存更改' : '保存后用于 AI 编写' }}</span><button type="button" class="primary" :disabled="locked || !itemsDirty || informationDirty" @click="save">{{ saving ? '保存中…' : '保存参考资料' }}</button></footer>
  </aside>
</template>

<style scoped>
.references-panel { display: flex; flex-direction: column; min-width: 0; min-height: 0; height: 100%; background: var(--bg-surface); color: var(--text-primary); }
.references-head, .references-footer { display: flex; align-items: center; justify-content: space-between; gap: var(--space-2); padding: var(--space-3) var(--space-4); }
.references-head { border-bottom: var(--border); }
.references-head h2 { margin: 0; font-size: var(--text-md); font-weight: var(--weight-semibold); }
.references-body { flex: 1; display: flex; flex-direction: column; gap: var(--space-3); min-height: 0; overflow-y: auto; padding: var(--space-4); }
.references-body .control { width: 100%; min-width: 0; }
.references-body :deep(.state-panel) { flex: none; min-height: calc(var(--control-h) * 3); padding: var(--space-4); }
.reference-intro { margin: 0; }
.reference-section-head { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--space-2); }
.reference-section-head strong, .reference-source strong { min-width: 0; overflow-wrap: anywhere; font-weight: var(--weight-medium); }
.reference-section-head h3 { margin: 0; font-size: var(--text-sm); font-weight: var(--weight-semibold); }
.reference-section-head .tag, .reference-section-head .count { flex-shrink: 0; }
.references-list { list-style: none; margin: 0; padding: 0; }
.reference-item, .reference-source { display: grid; gap: var(--space-2); padding: var(--space-3) 0; border-bottom: var(--border); min-width: 0; }
.reference-item:first-child { padding-top: 0; }
.reference-item .hint, .reference-source .hint, .reference-picker .hint { margin: 0; overflow-wrap: anywhere; }
.reference-actions { display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; }
.reference-tabs button { flex: 1; }
.reference-picker { display: grid; gap: var(--space-3); padding: var(--space-3); border: var(--border); border-radius: var(--radius-control); }
.reference-query { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: var(--space-2); align-items: end; }
.reference-picker .pager { flex-wrap: wrap; gap: var(--space-2); }
.reference-picker .pager span { margin-right: auto; font-size: var(--text-xs); }
.reference-edit summary { cursor: pointer; color: var(--text-link); font-size: var(--text-sm); }
.reference-edit summary:focus-visible { outline: none; box-shadow: var(--focus-shadow); }
.reference-edit .field { margin-top: var(--space-2); }
.reference-preview { border: var(--border); border-radius: var(--radius-control); padding: var(--space-3); display: grid; gap: var(--space-2); min-width: 0; }
.reference-preview > strong { overflow-wrap: anywhere; }
.reference-original { font-family: var(--font); font-size: var(--text-sm); line-height: var(--leading-sm); white-space: pre-wrap; overflow-wrap: anywhere; max-height: calc(var(--control-h) * 10); overflow-y: auto; padding: var(--space-3); margin: 0; background: var(--bg-subtle); color: var(--text-primary); }
.reference-prose { white-space: normal; overflow-x: auto; }
.reference-prose :deep(h1), .reference-prose :deep(h2), .reference-prose :deep(h3), .reference-prose :deep(h4), .reference-prose :deep(h5), .reference-prose :deep(h6) { font-size: var(--text-md); line-height: var(--leading-md); margin: 0 0 var(--space-2); }
.reference-prose :deep(p) { margin: 0 0 var(--space-2); }
.reference-prose :deep(table) { border-collapse: collapse; }
.reference-prose :deep(td), .reference-prose :deep(th) { border: var(--border); padding: var(--space-2); }
.references-footer { border-top: var(--border); flex-wrap: wrap; }
.references-footer .primary { margin-left: auto; }
</style>
