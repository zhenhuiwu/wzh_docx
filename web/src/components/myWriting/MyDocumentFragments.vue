<script setup lang="ts">
/** 一份文档里属于我的片段（30 §3.2 MyBlockCard）：标题锁定，只改我负责的块；按文档整体保存，带对象修订号。 */
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { ApiError } from "../../api/client";
import { getAllObjects, getAssignments, type DocObject } from "../../api/assignments";
import { patchBlocks, type MyWritingItem } from "../../api/baselines";
import { streamDocumentSection } from "../../api/documents";
import { aiFragmentTooLarge, applyAiOperations, buildFragments, changedOps, isPlaceholderText, type Fragment, type FragmentSummary } from "../../myBlocks";
import StatePanel from "../StatePanel.vue";

const props = defineProps<{ doc: MyWritingItem; userId: string }>();
const emit = defineEmits<{ (event: "dirty", value: boolean): void; (event: "saved"): void; (event: "fragments", value: FragmentSummary[]): void }>();

const phase = ref<"loading" | "ready" | "error">("loading");
const errorText = ref("");
const revision = ref(0);
const fragments = ref<Fragment[]>([]);
const drafts = ref<Record<string, string>>({});
const state = ref<"idle" | "saving" | "saved" | "conflict" | "forbidden" | "failed">("idle");
const stateText = ref("");
const placeholders = ref<Record<string, string>>({});

const blocks = computed<DocObject[]>(() => fragments.value.flatMap((fragment) => fragment.blocks));
const ops = computed(() => changedOps(blocks.value, drafts.value));
const dirty = computed(() => ops.value.length > 0);
watch(dirty, (value) => emit("dirty", value), { immediate: true });

async function load() {
  phase.value = "loading";
  try {
    const [objects, view] = await Promise.all([getAllObjects(props.doc.id), getAssignments(props.doc.id)]);
    revision.value = objects.documentRevision;
    fragments.value = buildFragments(objects.objects, view.resolved, props.userId);
    // 模板占位文字不放进输入框（显示为占位提示）；不改就不提交
    placeholders.value = Object.fromEntries(fragments.value.flatMap((f) => f.blocks.filter((b) => isPlaceholderText(b.markdown)).map((b) => [b.id, b.markdown.trim()])));
    drafts.value = Object.fromEntries(fragments.value.flatMap((f) => f.blocks.map((b) => [b.id, placeholders.value[b.id] ? "" : b.markdown])));
    phase.value = "ready";
  } catch (error) {
    errorText.value = error instanceof ApiError ? error.message : "暂时不能连接服务。";
    phase.value = "error";
  }
}

async function save() {
  if (!dirty.value || state.value === "saving") return;
  state.value = "saving";
  try {
    await patchBlocks(props.doc.id, revision.value, ops.value, `mw-${crypto.randomUUID()}`);
    state.value = "saved"; stateText.value = "";
    emit("saved");
    await load();
  } catch (error) {
    const api = error instanceof ApiError ? error : null;
    if (api?.status === 409) { state.value = "conflict"; stateText.value = "别人同时改了这些段落，修改没有保存。请在文档里处理冲突。"; }
    else if (api?.status === 403) { state.value = "forbidden"; stateText.value = api.message || "分工已调整，这些段落不再由你负责。"; }
    else { state.value = "failed"; stateText.value = api?.message ?? "暂时不能连接服务，修改还在本页，可以重试。"; }
  }
}

// 30 §4.2 / Q-8：AI 编写本节——复用 AI 修改接口，范围限定为这个片段里我负责的块；结果只填进草稿，由我确认后再保存。
const aiOpen = ref<string | null>(null);
const aiInstruction = ref("");
const aiBusy = ref<string | null>(null);
const aiNotice = ref<Record<string, { tone: "success" | "warning" | "danger"; text: string }>>({});
let aiAbort: AbortController | null = null;
function openAi(key: string) { aiOpen.value = aiOpen.value === key ? null : key; aiInstruction.value = ""; }
async function runAi(fragment: Fragment) {
  if (aiBusy.value || !aiInstruction.value.trim()) return;
  aiBusy.value = fragment.key;
  aiAbort = new AbortController();
  const ids = new Set(fragment.blocks.map((block) => block.id));
  let applied = 0; let skipped = 0;
  const seen = new Set<string>();
  const notice = (tone: "success" | "warning" | "danger", text: string) => { aiNotice.value = { ...aiNotice.value, [fragment.key]: { tone, text } }; };
  try {
    await streamDocumentSection(props.doc.id, revision.value, fragment.headingIndex, aiInstruction.value.trim(), undefined, (event) => {
      // 块操作在 done 事件里（有的服务端版本也会在 draft 里先给）
      if (event.type !== "draft" && event.type !== "done") return;
      const result = applyAiOperations(event.operations, ids, drafts.value, seen);
      applied += result.applied; skipped += result.skipped;
    }, aiAbort.signal, "section", undefined, undefined, [...ids]);
    if (!applied) notice("warning", "AI 没有给出可填入的修改。");
    else notice(skipped ? "warning" : "success", `AI 已改写 ${applied} 段，确认后点“保存本文档”。${skipped ? `另有 ${skipped} 处增删段落的建议需在文档中操作。` : ""}`);
    aiOpen.value = null;
  } catch (error) {
    if (aiAbort.signal.aborted) notice("warning", "已停止 AI 编写。");
    else notice("danger", error instanceof ApiError || error instanceof Error ? error.message : "AI 编写失败，请重试。");
  } finally {
    aiBusy.value = null; aiAbort = null;
  }
}
function stopAi() { aiAbort?.abort(); }
onBeforeUnmount(() => aiAbort?.abort());

function onBeforeUnload(event: BeforeUnloadEvent) { if (dirty.value) event.preventDefault(); }
window.addEventListener("beforeunload", onBeforeUnload);
onBeforeUnmount(() => window.removeEventListener("beforeunload", onBeforeUnload));
watch(() => props.doc.id, () => { state.value = "idle"; void load(); }, { immediate: true });

const pill = computed(() => {
  if (state.value === "saving") return { tone: "info", text: "正在保存…" };
  if (state.value === "conflict") return { tone: "danger", text: "冲突 1" };
  if (state.value === "forbidden") return { tone: "danger", text: "被拒保存" };
  if (state.value === "failed") return { tone: "danger", text: "保存失败" };
  if (dirty.value) return { tone: "warning", text: "有未保存修改" };
  return { tone: "success", text: state.value === "saved" ? "已保存" : "无修改" };
});
const summaries = computed<FragmentSummary[]>(() => fragments.value.map((fragment) => {
  const ids = new Set(fragment.blocks.map((block) => block.id));
  const touched = ops.value.some((op) => ids.has(op.objectId));
  const status = touched && state.value === "conflict" ? "conflict" : touched && state.value === "forbidden" ? "rejected"
    : fragment.blocks.every((block) => { const text = (drafts.value[block.id] ?? block.markdown).trim(); return !text || isPlaceholderText(text); }) ? "todo" : "written";
  return { key: fragment.key, label: fragment.headingPath.at(-1) || "（文档开头）", status };
}));
watch(summaries, (value) => emit("fragments", value), { immediate: true });
defineExpose({ dirty });
</script>

<template>
  <section class="fragments" :aria-label="`${doc.name} 中我负责的部分`" :aria-busy="phase === 'loading'">
    <div class="frag-bar">
      <span class="tag" :class="pill.tone" role="status">{{ pill.text }}</span>
      <span class="frag-count">{{ phase === "ready" ? `${fragments.length} 处片段` : "" }}</span>
      <router-link class="subtle sm" :to="{ name: 'document', params: { id: doc.project.id, documentId: doc.id } }">在文档中打开↗</router-link>
      <button type="button" class="primary sm" :disabled="!dirty || state === 'saving' || phase !== 'ready'" @click="save">{{ state === "saving" ? "正在保存…" : "保存本文档" }}</button>
    </div>
    <p v-if="stateText" class="notice" :class="state === 'failed' ? 'warning' : 'danger'" role="alert">{{ stateText }}
      <router-link v-if="state === 'conflict'" :to="{ name: 'document', params: { id: doc.project.id, documentId: doc.id } }">去处理冲突</router-link>
    </p>
    <template v-if="phase === 'loading'">
      <div v-for="n in 2" :key="n" class="frag-card skeleton" aria-hidden="true"><span></span><span></span><span></span></div>
    </template>
    <div v-else-if="phase === 'error'" class="frag-card"><StatePanel kind="error" title="这一处没能加载。" :text="errorText"><button type="button" class="ghost" @click="load">重试</button></StatePanel></div>
    <StatePanel v-else-if="!fragments.length" kind="empty" :title="doc.myRole === 'owner' ? '这份文档没有单独分给你的块' : '目前没有分配给你的部分。'" :text="doc.myRole === 'owner' ? '你是文档负责人，可以在文档中编写整篇。' : '负责人分配后会出现在这里。'" />
    <article v-for="fragment in fragments" v-else :id="`frag-${doc.id}-${fragment.key}`" :key="fragment.key" class="frag-card">
      <header class="frag-head">
        <svg class="lock" viewBox="0 0 16 16" role="img" aria-label="标题锁定"><path d="M5 7V5a3 3 0 0 1 6 0v2M3.5 7h9v6.5h-9z" /></svg>
        <strong class="path">{{ fragment.headingPath.join(" › ") || "（文档开头）" }}</strong>
        <button type="button" class="subtle sm" :aria-expanded="aiOpen === fragment.key" :disabled="state === 'saving' || (aiBusy !== null && aiBusy !== fragment.key) || aiFragmentTooLarge(fragment, drafts)" :title="aiFragmentTooLarge(fragment, drafts) ? '本节超过 20 段或 6000 字，请在文档中选中部分后使用 AI' : ''" @click="openAi(fragment.key)">AI 编写本节</button>
      </header>
      <form v-if="aiOpen === fragment.key" class="ai-form" @submit.prevent="runAi(fragment)">
        <input v-model="aiInstruction" class="control" :aria-label="`AI 编写要求：${fragment.headingPath.at(-1) ?? '片段'}`" placeholder="说明要怎么写，例如：补充接口鉴权要求" :disabled="aiBusy === fragment.key" maxlength="500" />
        <button v-if="aiBusy === fragment.key" type="button" class="ghost" @click="stopAi">停止</button>
        <button v-else type="submit" class="primary sm" :disabled="!aiInstruction.trim()">生成</button>
      </form>
      <p v-if="aiBusy === fragment.key" class="notice info" role="status">AI 正在编写本节…</p>
      <p v-else-if="aiNotice[fragment.key]" class="notice" :class="aiNotice[fragment.key].tone" role="status">{{ aiNotice[fragment.key].text }}</p>
      <p v-if="fragment.before" class="context" :title="'前文（他人负责，只读）'"><span class="ctx-label">前文 · 只读</span>{{ fragment.before }}</p>
      <label v-for="(block, index) in fragment.blocks" :key="block.id" class="block">
        <span class="sr-only">{{ fragment.headingPath.at(-1) ?? "片段" }} 第 {{ index + 1 }} 段</span>
        <textarea v-model="drafts[block.id]" class="control" rows="3" :placeholder="placeholders[block.id] || '在此编写这一段'" :disabled="state === 'saving'" :aria-invalid="!placeholders[block.id] && !drafts[block.id]?.trim()"></textarea>
        <span v-if="!placeholders[block.id] && !drafts[block.id]?.trim()" class="field-error">内容不能为空；删除段落请在文档中操作。</span>
      </label>
      <p v-if="fragment.after" class="context" :title="'后文（他人负责，只读）'"><span class="ctx-label">后文 · 只读</span>{{ fragment.after }}</p>
    </article>
  </section>
</template>

<style scoped>
.fragments { display: flex; flex-direction: column; gap: var(--space-3); }
.frag-bar { display: flex; align-items: center; gap: var(--space-2); }
.frag-count { color: var(--ink-secondary); font-size: var(--text-sm); margin-right: auto; }
.frag-card { border: 1px solid var(--rule); border-radius: var(--radius-sheet); background: var(--sheet); padding: var(--space-3) var(--space-4); display: flex; flex-direction: column; gap: var(--space-2); }
.frag-head { display: flex; align-items: center; gap: var(--space-2); }
.path { overflow-wrap: anywhere; margin-right: auto; }
.lock { width: var(--icon-sm); height: var(--icon-sm); fill: none; stroke: var(--ink-secondary); stroke-width: 1.4; flex: none; }
.context { margin: 0; padding: var(--space-2); color: var(--ink-tertiary); background: var(--bg-subtle); border-radius: var(--radius-control); white-space: pre-wrap; overflow-wrap: anywhere; font-size: var(--text-sm); display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.ctx-label { display: block; margin-bottom: 2px; font-size: var(--text-xs); color: var(--ink-tertiary); }
.ai-form { display: flex; gap: var(--space-2); }
.ai-form input { flex: 1; }
.block { display: flex; flex-direction: column; gap: var(--space-1); }
.block textarea { width: 100%; min-height: calc(var(--control-h) * 2); font-family: var(--font-doc); font-size: var(--text-md); line-height: var(--leading-md); resize: vertical; height: auto; }
.skeleton span { display: block; height: var(--space-4); background: var(--bg-subtle); border-radius: var(--radius-control); }
.skeleton span:last-child { width: 60%; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
</style>
