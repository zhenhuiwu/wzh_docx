<script setup lang="ts">
/** A06 发布新版本（16 §3.3/§3.4）：变更说明必填；摘要来自 diff(当前版本, draft) 与 format-impact；已有文档不受影响的提示。 */
import { computed, nextTick, onMounted, ref } from "vue";
import { useDialogFocus } from "../../dialogFocus";
import { ApiError } from "../../api/client";
import { diffTemplateVersions, publishTemplateVersion, templateFormatImpact, type ImpactItem, type TemplateVersionItem,
  type TemplateVersionList, type VersionDiff } from "../../api/templates";
import { publishImpactLine, summaryText } from "../../templateEditing";
import { revisionConflictText } from "../../saveModel";

const props = defineProps<{ documentId: string; draftRevision: number; currentVersionId: string; currentSeq: number; usage: TemplateVersionList["usage"] | null; notePrefill: string }>();
const emit = defineEmits<{ close: []; published: [version: TemplateVersionItem]; stale: [details: any]; locate: [blockId: string]; errors: [items: { blockId: string | null; message: string }[]] }>();
const dialog = ref<HTMLDialogElement | null>(null);
const note = ref(props.notePrefill);
const noteError = ref("");
const formError = ref("");
const submitting = ref(false);
const loading = ref(true);
const summaryError = ref("");
const diff = ref<VersionDiff | null>(null);
const impact = ref<ImpactItem[]>([]);
const editErrors = ref<{ blockId: string | null; message: string; where?: string }[]>([]);
const showUsage = ref(false);
const older = computed(() => (props.usage?.total ?? 0));

async function loadSummary() {
  loading.value = true;
  summaryError.value = "";
  try {
    const [changes, items] = await Promise.all([diffTemplateVersions(props.documentId, props.currentVersionId, "draft"), templateFormatImpact(props.documentId)]);
    diff.value = changes;
    impact.value = items;
  } catch (error) { summaryError.value = error instanceof Error ? error.message : "暂时无法读取变更摘要。"; }
  finally { loading.value = false; }
}

function located(blockId: string | null) {
  if (!blockId || !diff.value) return "";
  const hit = diff.value.blocks.find(item => item.blockId === blockId);
  return hit ? `${hit.heading || "文档开头"} · ${(hit.after?.text || hit.before?.text || "").slice(0, 20)}` : "";
}

async function submit() {
  if (submitting.value) return;
  formError.value = "";
  editErrors.value = [];
  const text = note.value.trim();
  noteError.value = !text ? "请填写变更说明。" : text.length > 200 ? "变更说明最多 200 个字。" : "";
  if (noteError.value) { void nextTick(() => document.getElementById("tpl-publish-note")?.focus()); return; }
  submitting.value = true;
  try {
    emit("published", await publishTemplateVersion(props.documentId, props.draftRevision, text));
  } catch (error) {
    if (error instanceof ApiError && error.code === "TEMPLATE_DRAFT_STALE") { emit("stale", error.details); return; }
    if (error instanceof ApiError && error.code === "REVISION_CONFLICT") formError.value = revisionConflictText(error.details as any).replace("该文档", "草稿");
    else if (error instanceof ApiError && error.code === "TEMPLATE_UNCHANGED") formError.value = "与当前版本相比没有变化，无需发布。";
    else if (error instanceof ApiError && (error.code === "TEMPLATE_EDIT_UNSUPPORTED" || error.code === "TEMPLATE_STRUCTURE_INVALID")) {
      const items = ((error.details as any)?.items ?? []) as { blockId: string | null; message: string }[];
      editErrors.value = items.map(item => ({ ...item, where: located(item.blockId) }));
      formError.value = error.message || "有内容不能写入 Word，请按下列提示修改。";
      emit("errors", items);
    } else formError.value = error instanceof Error ? error.message : "暂时不能连接服务。";
  } finally { submitting.value = false; }
}

useDialogFocus(dialog, { initial: '#tpl-publish-note', fallback: '[data-testid="template-publish"]' });
onMounted(() => { void loadSummary(); });
</script>

<template>
  <dialog ref="dialog" class="app-dialog" aria-labelledby="tpl-publish-title" data-testid="tpl-publish-dialog" @cancel.prevent="!submitting && emit('close')">
    <header class="dialog-head">
      <h2 id="tpl-publish-title">发布第 {{ currentSeq + 1 }} 版</h2>
      <button type="button" class="subtle" aria-label="关闭" :disabled="submitting" @click="emit('close')">×</button>
    </header>
    <form class="dialog-body publish-body" novalidate @submit.prevent="submit">
      <p v-if="formError" class="form-alert" role="alert" data-testid="tpl-publish-error">{{ formError }}</p>
      <ul v-if="editErrors.length" class="edit-errors">
        <li v-for="(item, index) in editErrors" :key="index"><button type="button" class="text sm" :disabled="!item.blockId" @click="item.blockId && emit('locate', item.blockId)">{{ item.where || '定位' }}</button>：{{ item.message }}</li>
      </ul>
      <label class="field">
        <span class="required">变更说明</span>
        <textarea id="tpl-publish-note" v-model="note" class="control" rows="3" maxlength="200" :aria-invalid="Boolean(noteError)" aria-describedby="tpl-publish-note-error" :disabled="submitting" data-testid="tpl-publish-note" @input="noteError = ''"></textarea>
        <span v-if="noteError" id="tpl-publish-note-error" class="field-error" role="alert">{{ noteError }}</span>
      </label>
      <section class="publish-section" aria-label="变更摘要">
        <h3>变更摘要</h3>
        <p v-if="loading" class="hint" role="status">正在读取变更摘要…</p>
        <p v-else-if="summaryError" class="hint">{{ summaryError }}</p>
        <template v-else-if="diff">
          <p class="summary" data-testid="tpl-publish-summary">{{ summaryText(diff.summary) }}</p>
          <ul v-if="impact.length" class="impact-lines"><li v-for="item in impact" :key="item.style + item.prop" data-testid="tpl-publish-impact">{{ publishImpactLine(item) }}</li></ul>
        </template>
      </section>
      <section v-if="usage" class="publish-section" aria-label="已有文档">
        <p class="notice info usage-note" data-testid="tpl-publish-usage">
          <template v-if="older > 0">已有 {{ older }} 篇文档使用本模板的较早版本。它们<strong>不受影响</strong>，继续使用各自创建时的版本；只有之后新建的文档使用新版本。</template>
          <template v-else>还没有文档使用本模板。发布后新建的文档使用新版本。</template>
        </p>
        <button v-if="usage.visible.length || usage.hiddenCount" type="button" class="text sm" :aria-expanded="showUsage" @click="showUsage = !showUsage">{{ showUsage ? '收起' : '查看这些文档' }}</button>
        <ul v-if="showUsage" class="usage-list">
          <li v-for="item in usage.visible" :key="item.documentId"><span>{{ item.projectName }} · {{ item.documentName }}</span><span v-if="item.legacy" class="tag plain" title="本文档创建于模板版本管理之前，使用当时的模板结构，不受之后的模板版本影响。">版本化之前</span><span v-else class="tag plain">第 {{ item.seq }} 版</span></li>
          <li v-if="usage.hiddenCount" class="hint">另有 {{ usage.hiddenCount }} 篇在你无权查看的项目中</li>
        </ul>
      </section>
      <p class="hint">目录和交叉引用不会自动更新，打开 Word 后请更新域（F9）。</p>
      <footer class="actions">
        <span v-if="submitting" class="hint" role="status">正在生成并校验 Word…</span>
        <button type="button" class="ghost" :disabled="submitting" @click="emit('close')">取消</button>
        <button type="submit" class="primary" :disabled="submitting" data-testid="tpl-publish-submit">{{ submitting ? '正在发布…' : '发布新版本' }}</button>
      </footer>
    </form>
  </dialog>
</template>

<style scoped>
.publish-body { display:grid; gap:var(--space-4); }
.publish-body .control { width:100%; resize:vertical; }
.publish-section { display:grid; gap:var(--space-2); }
.publish-section h3 { margin:0; font-size:var(--text-sm); font-weight:var(--weight-semibold); }
.summary { margin:0; font-size:var(--text-md); }
.impact-lines, .usage-list, .edit-errors { display:grid; gap:var(--space-1); margin:0; padding-left:var(--space-5); font-size:var(--text-sm); color:var(--text-secondary); }
.usage-list li { display:flex; align-items:center; gap:var(--space-2); }
.usage-note { border:0; border-radius:var(--radius-control); }
.edit-errors { color:var(--danger-700); }
.actions { display:flex; align-items:center; justify-content:flex-end; gap:var(--space-2); padding-top:var(--space-2); border-top:1px solid var(--border-subtle); }
.actions .hint { margin-right:auto; }
</style>
