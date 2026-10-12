<script setup lang="ts">
/** A06 草稿合并到最新版本（16 §3.3，17 §3.5）：先 dryRun，逐处选择，全部选完才能合并；409 时保留已做的选择。 */
import { computed, onMounted, ref } from "vue";
import { useDialogFocus } from "../../dialogFocus";
import { ApiError } from "../../api/client";
import { rebaseTemplateDraft, type DraftPayload, type RebaseConflict, type RebaseResult } from "../../api/templates";
import { EDITOR_KIND_LABELS, conflictFieldLabel, existsText, summaryText, valueLabel } from "../../templateEditing";
import { revisionConflictText } from "../../saveModel";
import { diffWords } from "../../textDiff";

const props = defineProps<{ documentId: string; draftRevision: number }>();
const emit = defineEmits<{ close: []; merged: [payload: DraftPayload & { seq: number }]; refresh: [] }>();
const dialog = ref<HTMLDialogElement | null>(null);
const phase = ref<"loading" | "ready" | "error">("loading");
const errorText = ref("");
const warning = ref("");
const formError = ref("");
const needRefresh = ref(false);
const merging = ref(false);
const result = ref<RebaseResult | null>(null);
const picks = ref<Record<string, "theirs" | "mine">>({});
const showChanges = ref(false);
const keyOf = (item: RebaseConflict) => `${item.blockId ?? ""}|${item.field}`;
const seq = computed(() => result.value?.currentVersion.seq ?? 0);
const conflicts = computed(() => result.value?.conflicts ?? []);
const chosen = computed(() => conflicts.value.filter(item => picks.value[keyOf(item)]).length);
const changeCount = computed(() => {
  const summary = result.value?.theirsChanges.summary;
  return summary ? summary.headings + summary.body + summary.tables + summary.guidance + summary.format : 0;
});

function display(item: RebaseConflict, value: any): string {
  if (item.field === "exists") return value === false ? "（已删除）" : "（保留）";
  if (value == null || value === "") return "—";
  if (item.field === "kind") return EDITOR_KIND_LABELS[value] ?? String(value);
  if (item.field === "headingLevel") return `标题 ${value}`;
  if (item.field === "headingId") return value ? "已绑定章节" : "不绑定";
  const style = /^style:.+:([^:]+)$/.exec(item.field);
  if (style) return valueLabel(style[1], value);
  return String(value);
}
function label(item: RebaseConflict) {
  const where = [item.heading || (item.blockId ? "文档开头" : "格式"), (item.text || "").slice(0, 20)].filter(Boolean).join(" · ");
  return `${where} · ${item.field === "exists" ? existsText(item, seq.value) : conflictFieldLabel(item.field, "")}`;
}

async function check(first = false) {
  if (first) phase.value = "loading";
  try {
    const previousSeq = seq.value;
    const data = await rebaseTemplateDraft(props.documentId, { expectedDraftRevision: props.draftRevision, dryRun: true });
    apply(data);
    if (!first && data.currentVersion.seq !== previousSeq) warning.value = `第 ${data.currentVersion.seq} 版刚刚发布，又出现了 ${data.conflicts.length} 处需要选择。`;
    phase.value = "ready";
  } catch (error) { fail(error, first); }
}
function apply(data: RebaseResult) {
  const keep: Record<string, "theirs" | "mine"> = {};
  for (const item of data.conflicts) if (picks.value[keyOf(item)]) keep[keyOf(item)] = picks.value[keyOf(item)];
  picks.value = keep;
  result.value = data;
}
function fail(error: unknown, first: boolean) {
  const message = error instanceof Error ? error.message : "暂时不能连接服务。";
  if (error instanceof ApiError && error.code === "REVISION_CONFLICT") {
    formError.value = revisionConflictText(error.details as any).replace("更新了该文档，请刷新后重试。", "修改了草稿，请刷新后重新合并。").replace("该文档", "草稿");
    needRefresh.value = true;
    if (first) { phase.value = "ready"; }
    return;
  }
  if (first) { phase.value = "error"; errorText.value = message; } else formError.value = message;
}
function pickAll(take: "theirs" | "mine") { picks.value = Object.fromEntries(conflicts.value.map(item => [keyOf(item), take])); }

async function merge() {
  if (merging.value || chosen.value < conflicts.value.length) return;
  merging.value = true;
  formError.value = "";
  const previousSeq = seq.value;
  try {
    const data = await rebaseTemplateDraft(props.documentId, { expectedDraftRevision: props.draftRevision,
      resolutions: conflicts.value.map(item => ({ blockId: item.blockId, field: item.field, take: picks.value[keyOf(item)] })) });
    emit("merged", { ...data, seq: previousSeq });
  } catch (error) {
    if (error instanceof ApiError && error.code === "TEMPLATE_REBASE_CONFLICT" && (error.details as any)?.conflicts) {
      const data = error.details as unknown as RebaseResult;
      apply(data);
      const left = data.conflicts.filter(item => !picks.value[keyOf(item)]).length;
      warning.value = data.currentVersion.seq !== previousSeq ? `第 ${data.currentVersion.seq} 版刚刚发布，又出现了 ${left} 处需要选择。` : `还有 ${left} 处需要选择。`;
    } else fail(error, false);
  } finally { merging.value = false; }
}

useDialogFocus(dialog, { fallback: '[data-testid="tpl-stale-rebase"]' });
onMounted(() => { void check(true); });
</script>

<template>
  <dialog ref="dialog" class="app-dialog rebase-dialog" aria-labelledby="tpl-rebase-title" data-testid="tpl-rebase-dialog" @cancel.prevent="!merging && emit('close')">
    <header class="dialog-head">
      <h2 id="tpl-rebase-title">{{ result ? `合并到第 ${seq} 版` : '合并到最新版本' }}</h2>
      <button type="button" class="subtle" aria-label="关闭" :disabled="merging" @click="emit('close')">×</button>
    </header>
    <div v-if="phase === 'loading'" class="dialog-body"><p class="hint" role="status"><span class="spinner" aria-hidden="true"></span> 正在检查…</p></div>
    <div v-else-if="phase === 'error'" class="dialog-body"><p class="form-alert" role="alert">{{ errorText }}</p><button type="button" class="ghost" @click="check(true)">重试</button></div>
    <div v-else class="dialog-body rebase-body">
      <p v-if="warning" class="notice warning" role="status" data-testid="tpl-rebase-warning">{{ warning }}</p>
      <p v-if="formError" class="form-alert" role="alert">{{ formError }} <button v-if="needRefresh" type="button" class="text sm" @click="emit('refresh')">刷新</button></p>
      <template v-if="result">
        <p class="dialog-text">第 {{ seq }} 版相对你开始编辑时的版本有 {{ changeCount }} 处变化：{{ summaryText(result.theirsChanges.summary) }}
          <button v-if="result.theirsChanges.blocks.length" type="button" class="text sm" :aria-expanded="showChanges" @click="showChanges = !showChanges">{{ showChanges ? '收起' : '展开' }}</button></p>
        <ul v-if="showChanges" class="theirs-list">
          <li v-for="item in result.theirsChanges.blocks" :key="item.blockId + item.change">{{ item.heading || '文档开头' }} · {{ (item.after?.text || item.before?.text || '').slice(0, 30) }}</li>
        </ul>
        <ul v-if="result.notes.length" class="theirs-list"><li v-for="(note, index) in result.notes" :key="index">{{ note.message }}</li></ul>
        <p v-if="!conflicts.length" class="notice success" data-testid="tpl-rebase-clean">你的修改与第 {{ seq }} 版没有冲突，可以直接合并。</p>
        <template v-else>
          <div class="conflict-head">
            <p class="dialog-text">有 {{ conflicts.length }} 处你和第 {{ seq }} 版都改过，请逐处选择。</p>
            <button type="button" class="ghost sm" data-testid="tpl-rebase-all-theirs" @click="pickAll('theirs')">全部采用第 {{ seq }} 版</button>
            <button type="button" class="ghost sm" data-testid="tpl-rebase-all-mine" @click="pickAll('mine')">全部保留我的</button>
          </div>
          <ol class="conflicts">
            <li v-for="item in conflicts" :key="keyOf(item)" class="conflict" data-testid="tpl-rebase-conflict" :data-field="item.field">
              <p class="conflict-title">{{ label(item) }}</p>
              <div class="three">
                <div><span class="col-head">开始时</span><p>{{ display(item, item.base) }}</p></div>
                <div><span class="col-head">第 {{ seq }} 版</span>
                  <p v-if="item.field === 'text'"><template v-for="(part, index) in diffWords(String(item.base ?? ''), String(item.theirs ?? ''))" :key="index"><del v-if="part.type === 'del'">{{ part.text }}</del><ins v-else-if="part.type === 'ins'">{{ part.text }}</ins><span v-else>{{ part.text }}</span></template></p>
                  <p v-else>{{ display(item, item.theirs) }}</p></div>
                <div><span class="col-head">我的草稿</span>
                  <p v-if="item.field === 'text'"><template v-for="(part, index) in diffWords(String(item.base ?? ''), String(item.mine ?? ''))" :key="index"><del v-if="part.type === 'del'">{{ part.text }}</del><ins v-else-if="part.type === 'ins'">{{ part.text }}</ins><span v-else>{{ part.text }}</span></template></p>
                  <p v-else>{{ display(item, item.mine) }}</p></div>
              </div>
              <div class="choices" role="radiogroup" :aria-label="label(item)">
                <label><input v-model="picks[keyOf(item)]" type="radio" :name="keyOf(item)" value="theirs" data-testid="tpl-rebase-take-theirs" /> 采用第 {{ seq }} 版</label>
                <label><input v-model="picks[keyOf(item)]" type="radio" :name="keyOf(item)" value="mine" data-testid="tpl-rebase-take-mine" /> 保留我的</label>
              </div>
            </li>
          </ol>
        </template>
      </template>
    </div>
    <footer class="dialog-actions">
      <span v-if="conflicts.length" class="dialog-note" data-testid="tpl-rebase-counter">已选择 {{ chosen }}/{{ conflicts.length }} 处</span>
      <button type="button" class="ghost" :disabled="merging" @click="emit('close')">取消</button>
      <button type="button" class="primary" data-testid="tpl-rebase-merge" :disabled="phase !== 'ready' || !result || merging || needRefresh || chosen < conflicts.length"
        :title="chosen < conflicts.length ? `还有 ${conflicts.length - chosen} 处没有选择。` : ''" @click="merge">{{ merging ? '正在合并…' : '合并' }}</button>
    </footer>
  </dialog>
</template>

<style scoped>
.rebase-dialog { width:720px; }
.rebase-body { display:grid; gap:var(--space-3); }
.rebase-body .notice { border:0; border-radius:var(--radius-control); }
.rebase-body .dialog-text { margin:0; }
.theirs-list { display:grid; gap:var(--space-1); margin:0; padding-left:var(--space-5); color:var(--text-secondary); font-size:var(--text-sm); }
.conflict-head { display:flex; align-items:center; gap:var(--space-2); }
.conflict-head .dialog-text { margin-right:auto; }
.conflicts { display:grid; gap:var(--space-3); max-height:calc(100vh - var(--space-7) * 9); margin:0; padding:0; overflow:auto; list-style:none; }
.conflict { display:grid; gap:var(--space-2); padding:var(--space-3); border:1px solid var(--border-subtle); border-radius:var(--radius-control); }
.conflict-title { margin:0; font-size:var(--text-sm); font-weight:var(--weight-medium); overflow-wrap:anywhere; }
.three { display:grid; grid-template-columns:repeat(3, minmax(0, 1fr)); gap:var(--space-2); }
.three > div { min-width:0; padding:var(--space-2); border-radius:var(--radius-control); background:var(--bg-subtle); }
.three p { margin:var(--space-1) 0 0; font-size:var(--text-sm); white-space:pre-wrap; overflow-wrap:anywhere; }
.col-head { color:var(--text-tertiary); font-size:var(--text-xs); }
.three ins { background:var(--success-50); color:var(--success-600); text-decoration:none; }
.three del { background:var(--danger-50); color:var(--danger-600); }
.choices { display:flex; gap:var(--space-5); font-size:var(--text-sm); }
.choices label { display:inline-flex; align-items:center; gap:var(--space-1); cursor:pointer; }
</style>
