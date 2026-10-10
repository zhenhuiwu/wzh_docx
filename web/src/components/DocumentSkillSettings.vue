<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from "vue";
import { listWritingSkills, getWritingSkill, type WritingSkill } from "../api/writingSkills";
import { setDocumentSkill, type DocumentSkill } from "../api/documentSkills";
import type { DocumentContentResult } from "../api/documents";
import { ApiError } from "../api/client";
import { revisionConflictText, type RevisionDetails } from "../saveModel";

const props = defineProps<{ documentId: string; revision: number; configuration?: DocumentSkill; refresh?: () => Promise<boolean> }>();
const emit = defineEmits<{ close: []; saved: [result: DocumentContentResult]; busy: [value: boolean] }>();
const dialog = ref<HTMLDialogElement | null>(null);
const previousFocus = window.document.activeElement as HTMLElement | null;
const pinned = props.configuration?.mode === "selected" ? props.configuration : null;
const initialChoice = pinned ? "pinned" : props.configuration?.mode || "auto";
const choice = ref(initialChoice);
const notes = ref(props.configuration?.documentInstructions || "");
const skills = ref<WritingSkill[]>([]);
const latest = ref<WritingSkill | null>(null);
const phase = ref<"loading" | "ready" | "error">("loading");
const error = ref("");
const saving = ref(false);
const discard = ref(false);
const controller = new AbortController();
let alive = true;
const selected = computed(() => choice.value === "pinned" ? pinned : skills.value.find(item => item.id === choice.value));
const changed = computed(() => choice.value !== initialChoice || notes.value.trim() !== (props.configuration?.documentInstructions || ""));
const newer = computed(() => pinned && latest.value?.status === "active" && latest.value.revision !== pinned.revision);
const kinds = { general: "通用", srs: "GJB438C · 需求", sdd: "GJB438C · 设计" };
async function load() {
  phase.value = "loading"; error.value = "";
  try {
    const result: WritingSkill[] = [];
    for (let page = 1; ; page++) {
      const response = await listWritingSkills({ page, pageSize: 50, status: "active" }, controller.signal);
      result.push(...response.items);
      if (page * response.pageSize >= response.total) break;
    }
    if (pinned) latest.value = await getWritingSkill(pinned.id, controller.signal);
    if (alive) { skills.value = result; phase.value = "ready"; }
  } catch (e) { if (alive) { phase.value = "error"; error.value = e instanceof Error ? e.message : "无法读取 Skill。"; } }
}
function close() {
  if (saving.value) return;
  if (changed.value) { discard.value = true; return; }
  emit("close");
}
async function save() {
  if (saving.value || phase.value !== "ready" || !changed.value) return;
  saving.value = true; emit("busy", true); error.value = "";
  try {
    const value = selected.value;
    const result = await setDocumentSkill(props.documentId, {
      expectedRevision: props.revision,
      mode: value ? "selected" : choice.value === "none" ? "none" : "auto",
      ...(value ? { skillId: value.id, skillRevision: value.revision } : {}),
      documentInstructions: notes.value.trim(),
    });
    emit("saved", result);
  } catch (e) {
    if (e instanceof ApiError && e.status === 409 && e.code === "REVISION_CONFLICT") {
      // 审查 S12：与正文保存同一套文案（02 §3.4）；能刷新时刷新到最新版本，保留对话框里的选择。
      const text = revisionConflictText(e.details as RevisionDetails | undefined);
      const refreshed = await props.refresh?.().catch(() => false);
      error.value = refreshed ? `${text.replace(/请刷新后重试。$/, "")}已刷新到最新版本，你的选择仍保留，请再次保存。` : text;
    } else error.value = e instanceof Error ? e.message : "保存失败，请重试。";
  }
  finally { saving.value = false; emit("busy", false); }
}
function beforeUnload(event: BeforeUnloadEvent) {
  if (saving.value || changed.value) { event.preventDefault(); event.returnValue = ""; }
}
onMounted(() => { dialog.value?.showModal(); window.addEventListener("beforeunload", beforeUnload); void load(); });
onBeforeUnmount(() => { alive = false; controller.abort(); window.removeEventListener("beforeunload", beforeUnload); dialog.value?.close(); previousFocus?.focus(); });
</script>

<template>
  <dialog ref="dialog" class="document-skill-dialog" aria-labelledby="document-skill-title" @cancel.prevent="close">
    <form @submit.prevent="save" :aria-busy="saving">
      <header><h2 id="document-skill-title">文档编写规范</h2><button type="button" class="ghost" :disabled="saving" @click="close">关闭</button></header>
      <div class="skill-fields">
        <p v-if="phase === 'loading'" role="status">正在读取 Skill…</p>
        <template v-else-if="phase === 'ready'">
          <label for="document-skill-choice">使用 Skill</label>
          <select id="document-skill-choice" v-model="choice" class="control" :disabled="saving" @change="discard = false">
            <option v-if="pinned" value="pinned">{{ pinned.name }}（文档已保存的规范）</option>
            <option value="auto">按文档名称自动匹配</option>
            <option value="none">不使用 Skill</option>
            <option v-for="item in skills" :key="item.id" :value="item.id">{{ item.name }}</option>
          </select>
          <div v-if="choice === 'pinned' && newer" class="update"><span>公共 Skill 已更新</span><button type="button" class="ghost" :disabled="saving" @click="choice = latest!.id">使用最新规范</button></div>
          <p v-if="choice === 'pinned' && latest?.status === 'disabled'">此 Skill 已停用，本篇仍保留已保存的规范。</p>
          <p v-if="!skills.length">暂无启用的 Skill，可在系统管理中添加。</p>
          <section v-if="selected" class="requirements" aria-label="所选 Skill 要求">
            <h3>{{ kinds[selected.baseKind] }}</h3>
            <div class="requirements-text">{{ selected.instructions }}</div>
            <p v-if="selected.forbiddenTerms.length">禁用词：{{ selected.forbiddenTerms.join('、') }}</p>
            <p v-if="selected.maxChars">章节正文或选区最多 {{ selected.maxChars }} 字</p>
          </section>
          <p v-else-if="choice === 'auto'">按模板或文档名称识别 438C 需求、设计规范。</p>
          <label for="document-skill-notes">本篇补充要求</label>
          <textarea id="document-skill-notes" v-model="notes" class="control" rows="4" maxlength="2000" :disabled="saving" @input="discard = false"></textarea>
        </template>
        <p v-if="error" class="failure" role="alert">{{ error }}</p>
        <button v-if="phase === 'error'" type="button" class="ghost" @click="load">重试</button>
      </div>
      <footer v-if="discard"><span>放弃未保存的规范修改？</span><button type="button" class="ghost" @click="discard = false">继续编辑</button><button type="button" class="primary" @click="emit('close')">放弃修改</button></footer>
      <footer v-else><button type="button" class="ghost" :disabled="saving" @click="close">取消</button><button type="submit" class="primary" :disabled="saving || phase !== 'ready' || !changed">{{ saving ? '保存中…' : '保存规范' }}</button></footer>
    </form>
  </dialog>
</template>

<style scoped>
.document-skill-dialog { width:calc(var(--space-7) * 18); max-height:calc(100vh - var(--space-7)); padding:0; overflow:auto; border:1px solid var(--border-strong); border-radius:var(--radius-sheet); background:var(--bg-surface); color:var(--text-primary); font-family:var(--font); }
.document-skill-dialog::backdrop { background:var(--gray-900); opacity:.45; }
header, footer { display:flex; align-items:center; gap:var(--space-3); padding:var(--space-4) var(--space-5); }
header { justify-content:space-between; border-bottom:1px solid var(--border-subtle); }
h2 { margin:0; font-size:calc(var(--text-md) * 1.25); }
h3 { margin:0 0 var(--space-2); font-size:var(--text-sm); color:var(--text-secondary); }
footer { justify-content:flex-end; border-top:1px solid var(--border-subtle); }
footer span { margin-right:auto; }
.skill-fields { display:grid; grid-template-columns:minmax(0,1fr); gap:var(--space-2); padding:var(--space-5); }
.skill-fields label { color:var(--text-secondary); }
.skill-fields p { margin:0; overflow-wrap:anywhere; }
.skill-fields textarea { margin-bottom:var(--space-2); }
.requirements { padding:var(--space-4); margin:var(--space-2) 0; border:1px solid var(--border-subtle); background:var(--bg-page); }
.requirements-text { white-space:pre-wrap; overflow-wrap:anywhere; max-height:calc(var(--space-7) * 5); overflow:auto; line-height:1.8; }
.requirements p { margin-top:var(--space-3); }
.update { display:flex; justify-content:space-between; align-items:center; gap:var(--space-3); }
.failure { color:var(--danger-600); }
</style>
