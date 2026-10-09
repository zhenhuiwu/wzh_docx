<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { onBeforeRouteLeave, onBeforeRouteUpdate } from "vue-router";
import { ApiError } from "../api/client";
import { getCurrentSession } from "../api/identity";
import { createWritingSkill, getWritingSkill, listWritingSkills, updateWritingSkill,
  type WritingSkill, type WritingSkillBaseKind, type WritingSkillInput, type WritingSkillStatus } from "../api/writingSkills";

const kinds: Record<WritingSkillBaseKind, string> = { general: "通用", srs: "438C 需求规格说明", sdd: "438C 软件设计说明" };
const draftQuery = ref({ name: "", status: "" as WritingSkillStatus | "" });
const appliedQuery = ref({ ...draftQuery.value });
const page = ref(1);
const pageSize = 20;
const items = ref<WritingSkill[]>([]);
const total = ref(0);
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / pageSize)));
const phase = ref<"loading" | "ready" | "error">("loading");
const errorText = ref("");
const notice = ref("");
const actionError = ref("");
const canManage = ref(false);
let permissionsLoaded = false;
let listRequest = 0;
let listController: AbortController | null = null;

type SkillForm = { name: string; description: string; instructions: string; baseKind: WritingSkillBaseKind; forbiddenTerms: string; maxChars: string | number };
const emptyForm = (): SkillForm => ({ name: "", description: "", instructions: "", baseKind: "general", forbiddenTerms: "", maxChars: "" });
const form = ref<SkillForm>(emptyForm());
const initialForm = ref(JSON.stringify(form.value));
const editing = ref<WritingSkill | null>(null);
const dialogOpen = ref(false);
const dialog = ref<HTMLDialogElement | null>(null);
const instructionsInput = ref<HTMLTextAreaElement | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);
const rules = ref<HTMLDetailsElement | null>(null);
const detailPhase = ref<"loading" | "ready" | "error">("ready");
const detailError = ref("");
const formError = ref("");
const fieldErrors = ref<Partial<Record<keyof SkillForm, string>>>({});
const submitting = ref(false);
const statusBusyId = ref("");
const importing = ref(false);
const mutationBusy = computed(() => submitting.value || Boolean(statusBusyId.value));
const formBusy = computed(() => mutationBusy.value || importing.value);
const dirty = computed(() => dialogOpen.value && canManage.value && detailPhase.value === "ready" && JSON.stringify(form.value) !== initialForm.value);
let detailRequest = 0;
let importRequest = 0;
let detailController: AbortController | null = null;
let createAttempt: { payload: string; key: string } | null = null;

function messageOf(error: unknown) {
  return error instanceof ApiError ? error.message : error instanceof Error ? error.message : "暂时不能连接服务。";
}

async function load() {
  const request = ++listRequest;
  listController?.abort();
  const controller = new AbortController();
  listController = controller;
  const query = { ...appliedQuery.value, page: page.value, pageSize };
  phase.value = "loading";
  errorText.value = "";
  try {
    const [result, session] = await Promise.all([listWritingSkills(query, controller.signal), permissionsLoaded ? undefined : getCurrentSession()]);
    if (request !== listRequest) return;
    if (session) {
      canManage.value = session.user.globalRoles.includes("system_admin");
      permissionsLoaded = true;
    }
    const lastPage = Math.max(1, Math.ceil(result.total / pageSize));
    if (query.page > lastPage) {
      page.value = lastPage;
      await load();
      return;
    }
    items.value = result.items;
    total.value = result.total;
    phase.value = "ready";
  } catch (error) {
    if (request !== listRequest) return;
    phase.value = "error";
    errorText.value = messageOf(error);
  }
}

function search(reset = false) {
  if (mutationBusy.value || phase.value === "loading") return;
  if (reset) draftQuery.value = { name: "", status: "" };
  appliedQuery.value = { name: draftQuery.value.name.trim(), status: draftQuery.value.status };
  page.value = 1;
  notice.value = "";
  actionError.value = "";
  void load();
}

function changePage(next: number) {
  if (mutationBusy.value || phase.value !== "ready" || next < 1 || next > pageCount.value) return;
  page.value = next;
  void load();
}

function fillForm(skill: WritingSkill) {
  form.value = { name: skill.name, description: skill.description, instructions: skill.instructions, baseKind: skill.baseKind,
    forbiddenTerms: skill.forbiddenTerms.join("\n"), maxChars: skill.maxChars === null ? "" : String(skill.maxChars) };
  initialForm.value = JSON.stringify(form.value);
}

async function loadDetail() {
  const id = editing.value?.id;
  if (!id || mutationBusy.value) return;
  const request = ++detailRequest;
  detailController?.abort();
  const controller = new AbortController();
  detailController = controller;
  detailPhase.value = "loading";
  detailError.value = "";
  try {
    const skill = await getWritingSkill(id, controller.signal);
    if (request !== detailRequest || !dialogOpen.value || editing.value?.id !== id) return;
    editing.value = skill;
    fillForm(skill);
    detailPhase.value = "ready";
    await nextTick();
    instructionsInput.value?.focus();
  } catch (error) {
    if (request !== detailRequest || !dialogOpen.value) return;
    detailPhase.value = "error";
    detailError.value = messageOf(error);
  }
}

async function openDialog(skill?: WritingSkill) {
  if (mutationBusy.value || dialogOpen.value || (!skill && !canManage.value)) return;
  editing.value = skill ?? null;
  createAttempt = null;
  form.value = emptyForm();
  initialForm.value = JSON.stringify(form.value);
  formError.value = "";
  fieldErrors.value = {};
  detailError.value = "";
  detailPhase.value = skill ? "loading" : "ready";
  dialogOpen.value = true;
  await nextTick();
  dialog.value?.showModal();
  if (skill) void loadDetail();
  else instructionsInput.value?.focus();
}

function closeDialog() {
  if (formBusy.value) return;
  if (dirty.value && !window.confirm("放弃尚未保存的 Skill 修改？")) return;
  dialog.value?.close();
}

function dialogClosed() {
  dialogOpen.value = false;
  ++detailRequest;
  ++importRequest;
  detailController?.abort();
  importing.value = false;
}

function cancelDialog(event: Event) {
  event.preventDefault();
  closeDialog();
}

async function importText(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = "";
  if (!file || formBusy.value || !canManage.value) return;
  formError.value = "";
  if (!/\.(md|txt)$/i.test(file.name) || file.size > 65536) {
    formError.value = "请选择不超过 64 KB 的 .md 或 .txt 文本文件。";
    return;
  }
  const request = ++importRequest;
  importing.value = true;
  try {
    const text = (await file.text()).replace(/^\uFEFF/, "");
    if (request !== importRequest || !dialogOpen.value) return;
    if (!text.trim() || text.length > 12000) {
      formError.value = "导入的编写要求应为 1–12000 个字符。";
      return;
    }
    form.value.instructions = text;
    fieldErrors.value.instructions = undefined;
    importing.value = false;
    await nextTick();
    instructionsInput.value?.focus();
  } catch {
    if (request === importRequest) formError.value = "文件读取失败，请重新选择。";
  } finally {
    if (request === importRequest) importing.value = false;
  }
}

function validateForm(): WritingSkillInput | null {
  const input: WritingSkillInput = { name: form.value.name.trim(), description: form.value.description.trim(), instructions: form.value.instructions.trim(),
    baseKind: editing.value?.builtin ? editing.value.baseKind : form.value.baseKind,
    forbiddenTerms: [...new Set(form.value.forbiddenTerms.split(/\r?\n/).map(term => term.trim()).filter(Boolean))],
    maxChars: String(form.value.maxChars).trim() ? Number(form.value.maxChars) : null, status: editing.value?.status ?? "active" };
  const errors: Partial<Record<keyof SkillForm, string>> = {};
  if (!input.name || input.name.length > 120) errors.name = "名称应为 1–120 个字符。";
  if (input.description.length > 500) errors.description = "说明不能超过 500 个字符。";
  if (!input.instructions || input.instructions.length > 12000) errors.instructions = "编写要求应为 1–12000 个字符。";
  if (input.forbiddenTerms.length > 30 || input.forbiddenTerms.some(term => term.length > 80)) errors.forbiddenTerms = "最多 30 个禁用词，每个不超过 80 个字符。";
  const limitInput = document.getElementById("skill-maxChars") as HTMLInputElement | null;
  if (limitInput?.validity.badInput || (input.maxChars !== null && (!Number.isInteger(input.maxChars) || input.maxChars < 100 || input.maxChars > 20000))) errors.maxChars = "编写上限应为 100–20000 的整数，或留空。";
  fieldErrors.value = errors;
  const field = Object.keys(errors)[0] as keyof SkillForm | undefined;
  if (field) {
    if ((field === "forbiddenTerms" || field === "maxChars") && rules.value) rules.value.open = true;
    void nextTick(() => document.getElementById(`skill-${field}`)?.focus());
    return null;
  }
  return input;
}

async function save() {
  if (formBusy.value || !canManage.value || detailPhase.value !== "ready") return;
  formError.value = "";
  const input = validateForm();
  if (!input) return;
  const current = editing.value;
  const payload = JSON.stringify(input);
  if (!current && createAttempt?.payload !== payload) createAttempt = { payload, key: crypto.randomUUID() };
  submitting.value = true;
  try {
    const result = current ? await updateWritingSkill(current.id, { ...input, expectedRevision: current.revision }) : await createWritingSkill(input, createAttempt!.key);
    initialForm.value = JSON.stringify(form.value);
    notice.value = `已保存「${result.name}」。`;
    actionError.value = "";
    if (!current) {
      draftQuery.value = { name: "", status: "" };
      appliedQuery.value = { ...draftQuery.value };
      page.value = 1;
    }
    await load();
    dialog.value?.close();
  } catch (error) {
    formError.value = messageOf(error);
  } finally {
    submitting.value = false;
  }
}

async function toggleStatus(skill: WritingSkill) {
  if (!canManage.value || mutationBusy.value) return;
  statusBusyId.value = skill.id;
  actionError.value = "";
  notice.value = "";
  const status: WritingSkillStatus = skill.status === "active" ? "disabled" : "active";
  try {
    const result = await updateWritingSkill(skill.id, { expectedRevision: skill.revision, status });
    notice.value = `「${result.name}」已${status === "active" ? "启用" : "停用"}。`;
    await load();
  } catch (error) {
    actionError.value = messageOf(error);
  } finally {
    statusBusyId.value = "";
  }
}

function canLeave() {
  if (mutationBusy.value) {
    if (dialogOpen.value) formError.value = "正在保存 Skill，请稍候。";
    else actionError.value = "正在更新 Skill，请稍候。";
    return false;
  }
  if (dirty.value && !window.confirm("Skill 修改尚未保存，是否离开？")) return false;
  return true;
}

function beforeUnload(event: BeforeUnloadEvent) {
  if (!mutationBusy.value && !dirty.value) return;
  event.preventDefault();
  event.returnValue = "";
}

onBeforeRouteLeave(canLeave);
onBeforeRouteUpdate(canLeave);
onMounted(() => { window.addEventListener("beforeunload", beforeUnload); void load(); });
onBeforeUnmount(() => {
  ++listRequest; ++detailRequest; ++importRequest;
  listController?.abort(); detailController?.abort();
  window.removeEventListener("beforeunload", beforeUnload);
});
</script>

<template>
  <section class="card skills-page">
    <header class="head"><h1>Skill 管理</h1><button v-if="canManage" type="button" class="primary" :disabled="mutationBusy || phase !== 'ready'" @click="openDialog()">新建 Skill</button></header>
    <p v-if="notice" class="feedback" role="status">{{ notice }}</p>
    <p v-if="actionError" class="feedback failure" role="alert">{{ actionError }}</p>
    <form class="skill-query" @submit.prevent="search()">
      <label>名称<input v-model="draftQuery.name" class="control" type="search" maxlength="120" :disabled="mutationBusy || phase === 'loading'" /></label>
      <label>状态<select v-model="draftQuery.status" class="control" :disabled="mutationBusy || phase === 'loading'"><option value="">全部状态</option><option value="active">启用</option><option value="disabled">停用</option></select></label>
      <div class="query-actions"><button type="submit" class="primary" :disabled="mutationBusy || phase === 'loading'">查询</button><button type="button" class="ghost" :disabled="mutationBusy || phase === 'loading'" @click="search(true)">重置</button></div>
    </form>
    <p v-if="phase === 'loading'" class="empty" role="status">正在读取 Skill…</p>
    <div v-else-if="phase === 'error'" class="empty state-error" role="alert"><p>{{ errorText }}</p><button type="button" class="ghost" :disabled="mutationBusy" @click="load">重试</button></div>
    <p v-else-if="items.length === 0" class="empty">{{ appliedQuery.name || appliedQuery.status ? '没有符合条件的 Skill' : '还没有 Skill' }}</p>
    <div v-else class="table-wrap">
      <table><colgroup><col class="name-col" /><col class="description-col" /><col class="kind-col" /><col class="status-col" /><col class="actions-col" /></colgroup>
        <thead><tr><th>名称</th><th>说明</th><th>基准规范</th><th>状态</th><th>操作</th></tr></thead>
        <tbody><tr v-for="item in items" :key="item.id">
          <td class="skill-name">{{ item.name }}</td><td>{{ item.description || '—' }}</td><td>{{ kinds[item.baseKind] }}</td><td>{{ item.status === 'active' ? '启用' : '停用' }}</td>
          <td><div class="row-actions"><button type="button" class="ghost" :disabled="mutationBusy" @click="openDialog(item)">{{ canManage ? '编辑' : '查看' }}</button><button v-if="canManage" type="button" class="ghost" :disabled="mutationBusy" :aria-label="`${item.status === 'active' ? '停用' : '启用'} ${item.name}`" @click="toggleStatus(item)">{{ statusBusyId === item.id ? '正在更新…' : item.status === 'active' ? '停用' : '启用' }}</button></div></td>
        </tr></tbody>
      </table>
    </div>
    <nav v-if="phase === 'ready'" class="skill-pager" aria-label="Skill 分页"><span>共 {{ total }} 条</span><span>{{ page }} / {{ pageCount }}</span><button type="button" class="ghost" :disabled="mutationBusy || page <= 1" @click="changePage(page - 1)">上一页</button><button type="button" class="ghost" :disabled="mutationBusy || page >= pageCount" @click="changePage(page + 1)">下一页</button></nav>

    <dialog v-if="dialogOpen" ref="dialog" class="skill-dialog" aria-labelledby="skill-dialog-title" @cancel="cancelDialog" @close="dialogClosed">
      <form novalidate :aria-busy="formBusy" @submit.prevent="save">
        <header class="dialog-head"><h2 id="skill-dialog-title">{{ !canManage ? '查看 Skill' : editing ? '编辑 Skill' : '新建 Skill' }}</h2><button type="button" class="ghost" :disabled="formBusy" @click="closeDialog">关闭</button></header>
        <p v-if="detailPhase === 'loading'" class="dialog-state" role="status">正在读取 Skill…</p>
        <div v-else-if="detailPhase === 'error'" class="dialog-state" role="alert"><p>{{ detailError }}</p><button type="button" class="ghost" @click="loadDetail">重试</button></div>
        <div v-else class="dialog-fields">
          <div class="identity-fields">
            <label for="skill-name">名称<input id="skill-name" v-model="form.name" class="control" :class="{ 'is-invalid': fieldErrors.name }" type="text" maxlength="120" aria-label="名称" :readonly="!canManage" :disabled="formBusy" :aria-invalid="Boolean(fieldErrors.name)" :aria-describedby="fieldErrors.name ? 'skill-name-error' : undefined" /><span v-if="fieldErrors.name" id="skill-name-error" class="field-error" role="alert">{{ fieldErrors.name }}</span></label>
            <label for="skill-baseKind">基准规范<select id="skill-baseKind" v-model="form.baseKind" class="control" aria-label="基准规范" :disabled="formBusy || !canManage || editing?.builtin"><option v-for="(label, value) in kinds" :key="value" :value="value">{{ label }}</option></select></label>
          </div>
          <label for="skill-description">说明<input id="skill-description" v-model="form.description" class="control" type="text" maxlength="500" aria-label="说明" :readonly="!canManage" :disabled="formBusy" :aria-invalid="Boolean(fieldErrors.description)" :aria-describedby="fieldErrors.description ? 'skill-description-error' : undefined" /><span v-if="fieldErrors.description" id="skill-description-error" class="field-error" role="alert">{{ fieldErrors.description }}</span></label>
          <div class="instruction-field">
            <div class="instruction-label"><label for="skill-instructions">编写要求</label><button v-if="canManage" type="button" class="ghost" :disabled="formBusy" @click="fileInput?.click()">{{ importing ? '正在读取…' : '导入文本' }}</button><input ref="fileInput" type="file" accept=".md,.txt" hidden :disabled="formBusy || !canManage" @change="importText" /></div>
            <textarea id="skill-instructions" ref="instructionsInput" v-model="form.instructions" class="control requirements" :class="{ 'is-invalid': fieldErrors.instructions }" rows="10" maxlength="12000" :readonly="!canManage" :disabled="formBusy" :spellcheck="false" :aria-invalid="Boolean(fieldErrors.instructions)" :aria-describedby="fieldErrors.instructions ? 'skill-instructions-error' : undefined" />
            <span v-if="fieldErrors.instructions" id="skill-instructions-error" class="field-error" role="alert">{{ fieldErrors.instructions }}</span>
          </div>
          <details ref="rules" class="validation-rules"><summary>校验规则</summary><div class="rule-fields">
            <label for="skill-forbiddenTerms">禁用词（每行一个）<textarea id="skill-forbiddenTerms" v-model="form.forbiddenTerms" class="control" rows="3" aria-label="禁用词（每行一个）" :readonly="!canManage" :disabled="formBusy" :aria-invalid="Boolean(fieldErrors.forbiddenTerms)" :aria-describedby="fieldErrors.forbiddenTerms ? 'skill-forbiddenTerms-error' : undefined" /><span v-if="fieldErrors.forbiddenTerms" id="skill-forbiddenTerms-error" class="field-error" role="alert">{{ fieldErrors.forbiddenTerms }}</span></label>
            <label for="skill-maxChars">章节 / 选区字数上限<input id="skill-maxChars" v-model="form.maxChars" class="control" type="number" min="100" max="20000" step="1" placeholder="不限制" aria-label="章节 / 选区字数上限" :readonly="!canManage" :disabled="formBusy" :aria-invalid="Boolean(fieldErrors.maxChars)" :aria-describedby="fieldErrors.maxChars ? 'skill-maxChars-error' : undefined" /><span v-if="fieldErrors.maxChars" id="skill-maxChars-error" class="field-error" role="alert">{{ fieldErrors.maxChars }}</span></label>
          </div></details>
          <p v-if="formError" class="field-error" role="alert">{{ formError }}</p>
        </div>
        <footer class="dialog-actions"><button type="button" class="ghost" :disabled="formBusy" @click="closeDialog">{{ canManage ? '取消' : '关闭' }}</button><button v-if="canManage && detailPhase === 'ready'" type="submit" class="primary" :disabled="formBusy">{{ submitting ? '正在保存…' : '保存 Skill' }}</button></footer>
      </form>
    </dialog>
  </section>
</template>

<style scoped>
.skill-query { display:flex; align-items:flex-end; gap:var(--space-4); padding:var(--space-4); border-bottom:1px solid var(--rule); }
.skill-query label { display:grid; gap:var(--space-1); min-width:calc(var(--space-7) * 4); color:var(--ink-secondary); font-size:var(--text-sm); }
.feedback { margin:0; padding:var(--space-3) var(--space-4); color:var(--ok); font-size:var(--text-sm); border-bottom:1px solid var(--rule); overflow-wrap:anywhere; }
.feedback.failure { color:var(--fault); }
.state-error { display:flex; flex-direction:column; align-items:center; gap:var(--space-2); }
.state-error p,.dialog-state p { margin:0; }
.state-error p,.dialog-state[role="alert"] { color:var(--fault); }
.skills-page table { width:100%; table-layout:fixed; }
.skills-page th,.skills-page td { white-space:normal; overflow-wrap:anywhere; }
.name-col { width:22%; }.description-col { width:40%; }.kind-col { width:16%; }.status-col { width:8%; }.actions-col { width:14%; }
.skill-name { font-weight:600; }
.row-actions { display:flex; gap:var(--space-2); align-items:center; }
.row-actions button { white-space:nowrap; }
.skill-pager { display:flex; justify-content:flex-end; align-items:center; gap:var(--space-3); padding:var(--space-3) var(--space-4); margin-top:auto; color:var(--ink-secondary); font-size:var(--text-sm); }
.skill-dialog { width:calc(var(--space-7) * 20); max-height:calc(100vh - var(--space-7)); padding:0; overflow:auto; border:1px solid var(--rule-strong); border-radius:var(--radius-sheet); background:var(--sheet); color:var(--ink); font-family:var(--font); }
.skill-dialog::backdrop { background:var(--spine); opacity:.45; }
.dialog-head { display:flex; align-items:center; justify-content:space-between; padding:var(--space-5); border-bottom:1px solid var(--rule); }
.dialog-head h2 { margin:0; font-size:calc(var(--text-md) * 1.25); }
.dialog-fields { display:grid; gap:var(--space-4); padding:var(--space-5); }
.identity-fields,.rule-fields { display:grid; grid-template-columns:minmax(0,2fr) minmax(0,1fr); gap:var(--space-4); align-items:start; }
.dialog-fields label { display:grid; gap:var(--space-1); color:var(--ink-secondary); font-size:var(--text-sm); }
.dialog-fields .field-error { margin:0; overflow-wrap:anywhere; }
.instruction-field { display:grid; gap:var(--space-2); }
.instruction-label { display:flex; justify-content:space-between; align-items:center; gap:var(--space-3); }
.requirements { min-height:calc(var(--space-7) * 5); line-height:1.7; }
.validation-rules summary { width:fit-content; padding:var(--space-1) 0; cursor:pointer; color:var(--ink-secondary); font-size:var(--text-sm); }
.validation-rules summary:focus-visible { outline:2px solid var(--stamp); outline-offset:2px; }
.rule-fields { padding-top:var(--space-3); }
.dialog-state { display:grid; justify-items:start; gap:var(--space-3); padding:var(--space-5); color:var(--ink-secondary); font-size:var(--text-sm); }
.dialog-actions { display:flex; justify-content:flex-end; gap:var(--space-2); padding:var(--space-4) var(--space-5); border-top:1px solid var(--rule); }
</style>
