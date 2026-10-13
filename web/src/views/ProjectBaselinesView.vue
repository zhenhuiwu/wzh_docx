<script setup lang="ts">
/** 「基线」页（30 §3.5）：左侧时间线，右侧选中基线的文档清单与对比。基线不冻结文档（Q-4）。 */
import { computed, nextTick, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { ApiError } from "../api/client";
import { createProjectBaseline, getProjectBaseline, listProjectBaselines, type ProjectBaseline, type ProjectBaselineDetail } from "../api/baselines";
import { listDocuments, type DocumentRecord } from "../api/documents";
import type { ProjectRecord } from "../api/projects";
import ProjectFrame from "../components/ProjectFrame.vue";
import StatePanel from "../components/StatePanel.vue";
import DiffView from "../components/baselines/DiffView.vue";
import { formatDateTime } from "../format";

const route = useRoute();
const projectId = computed(() => String(route.params.id));
const baselines = ref<ProjectBaseline[]>([]);
const phase = ref<"loading" | "ready" | "error">("loading");
const errorText = ref("");
const selected = ref<number | null>(null);
const detail = ref<ProjectBaselineDetail | null>(null);
const detailPhase = ref<"idle" | "loading" | "ready" | "error">("idle");
const comparing = ref<{ documentId: string; name: string; seq: number } | null>(null);

const message = (error: unknown) => (error instanceof ApiError ? error.message : "暂时不能连接服务。");
const canManage = (project: ProjectRecord | null | undefined) =>
  Boolean(project && !project.archivedAt && (project.permissions?.projectRole === "owner" || project.permissions?.canManageMembers));

async function load() {
  phase.value = "loading";
  try {
    baselines.value = (await listProjectBaselines(projectId.value)).items;
    phase.value = "ready";
    if (baselines.value.length && !baselines.value.some((b) => b.seq === selected.value)) void pick(baselines.value[0].seq);
  } catch (error) { errorText.value = message(error); phase.value = "error"; }
}
async function pick(seq: number) {
  selected.value = seq; comparing.value = null; detailPhase.value = "loading";
  try { detail.value = await getProjectBaseline(projectId.value, seq); detailPhase.value = "ready"; }
  catch (error) { errorText.value = message(error); detailPhase.value = "error"; }
}
watch(projectId, () => { selected.value = null; void load(); }, { immediate: true });

// ---------- 两个基线跨文档对比（30 §3.5、§8 第 8 条；验收 P1-9） ----------
const fromSeq = ref<number | null>(null);
const toSeq = ref<number | "current">("current");
const cross = ref<{ from: ProjectBaselineDetail; to: ProjectBaselineDetail | null; label: string } | null>(null);
const crossPhase = ref<"idle" | "loading" | "ready" | "error">("idle");
const crossError = ref("");
const crossDoc = ref<string | null>(null);
watch(baselines, (list) => {
  if (!list.length) return;
  if (!list.some((b) => b.seq === fromSeq.value)) fromSeq.value = list[1]?.seq ?? list[0].seq;
  if (toSeq.value !== "current" && !list.some((b) => b.seq === toSeq.value)) toSeq.value = "current";
});
interface CrossRow { id: string; name: string; base: number | null; compare: number | "current" | null; state: "changed" | "same" | "added" | "removed"; note: string }
const crossRows = computed<CrossRow[]>(() => {
  const value = cross.value;
  if (!value) return [];
  const rows: CrossRow[] = [];
  const target = new Map((value.to?.documents ?? []).map((row) => [row.document.id, row]));
  for (const row of value.from.documents) {
    if (!value.to) {
      rows.push({ id: row.document.id, name: row.document.name, base: row.version.seq, compare: "current", state: row.changed ? "changed" : "same",
        note: row.changed ? `+${row.sinceBaseline.paragraphsAdded} 段 / −${row.sinceBaseline.paragraphsRemoved} 段` : "无变化" });
      continue;
    }
    const other = target.get(row.document.id);
    target.delete(row.document.id);
    if (!other) { rows.push({ id: row.document.id, name: row.document.name, base: row.version.seq, compare: null, state: "removed", note: `不在 ${value.to.label} 中` }); continue; }
    const same = other.version.revision === row.version.revision;
    rows.push({ id: row.document.id, name: row.document.name, base: row.version.seq, compare: other.version.seq, state: same ? "same" : "changed", note: same ? "无变化" : `${row.version.label} → ${other.version.label}` });
  }
  for (const row of target.values()) rows.push({ id: row.document.id, name: row.document.name, base: null, compare: row.version.seq, state: "added", note: `${value.from.label} 之后加入` });
  const order = { changed: 0, added: 1, removed: 2, same: 3 } as const;
  return rows.sort((a, b) => order[a.state] - order[b.state] || a.name.localeCompare(b.name, "zh-CN"));
});
const crossChanged = computed(() => crossRows.value.filter((row) => row.state !== "same").length);
const crossActive = computed(() => crossRows.value.find((row) => row.id === crossDoc.value) ?? null);
async function startCross() {
  if (fromSeq.value === null || fromSeq.value === toSeq.value) return;
  crossPhase.value = "loading"; crossError.value = ""; comparing.value = null;
  try {
    const [from, to] = await Promise.all([getProjectBaseline(projectId.value, fromSeq.value), toSeq.value === "current" ? Promise.resolve(null) : getProjectBaseline(projectId.value, toSeq.value)]);
    cross.value = { from, to, label: `${from.label} → ${to ? to.label : "当前"}` };
    crossDoc.value = crossRows.value.find((row) => row.state === "changed")?.id ?? crossRows.value[0]?.id ?? null;
    crossPhase.value = "ready";
  } catch (error) { crossError.value = message(error); crossPhase.value = "error"; }
}
function closeCross() { cross.value = null; crossPhase.value = "idle"; }
watch(projectId, closeCross);

// ---------- 建立基线 ----------
const dialog = ref<HTMLDialogElement | null>(null);
const docs = ref<DocumentRecord[]>([]);
const docsPhase = ref<"loading" | "ready" | "error">("loading");
const chosen = ref<string[]>([]);
const name = ref("");
const nameError = ref("");
const submitError = ref("");
const submitting = ref(false);

async function openCreate() {
  name.value = ""; nameError.value = ""; submitError.value = ""; docsPhase.value = "loading";
  await nextTick(); dialog.value?.showModal();
  try {
    docs.value = (await listDocuments(projectId.value, 1, "", 100)).items;
    chosen.value = docs.value.map((d) => d.id);
    docsPhase.value = "ready";
  } catch (error) { submitError.value = message(error); docsPhase.value = "error"; }
}
function closeCreate() { if (!submitting.value) dialog.value?.close(); }
async function submit() {
  nameError.value = name.value.trim() ? "" : "请填写基线名称。";
  submitError.value = chosen.value.length ? "" : "至少选择一份文档。";
  if (nameError.value || submitError.value) return;
  submitting.value = true;
  try {
    const created = await createProjectBaseline(projectId.value, name.value.trim(), chosen.value);
    dialog.value?.close();
    await load();
    void pick(created.seq);
  } catch (error) { submitError.value = message(error); }
  finally { submitting.value = false; }
}
</script>

<template>
  <ProjectFrame>
    <template #default="{ project }">
      <section class="baseline-page">
        <div class="document-head">
          <div>
            <h2 class="section-title">基线</h2>
            <p class="page-desc">基线把若干文档固定在当时的版本上，之后仍可继续编写。</p>
          </div>
          <button v-if="canManage(project)" type="button" class="primary" :disabled="phase !== 'ready'" @click="openCreate">建立基线</button>
        </div>
        <StatePanel v-if="phase === 'loading'" kind="loading" text="正在读取基线…" />
        <StatePanel v-else-if="phase === 'error'" kind="error" title="基线加载失败" :text="errorText"><button type="button" class="ghost" @click="load">重试</button></StatePanel>
        <StatePanel v-else-if="!baselines.length" kind="empty" title="还没有基线" :text="canManage(project) ? '文档写到一个阶段后，可以建立基线留档。' : '项目负责人建立基线后会出现在这里。'" />
        <form v-else class="cross-bar" aria-label="对比基线" @submit.prevent="startCross">
          <span class="cross-label">对比基线</span>
          <label class="sr-only" for="cross-from">从</label>
          <select id="cross-from" v-model="fromSeq" class="control">
            <option v-for="item in baselines" :key="item.seq" :value="item.seq">{{ item.label }} {{ item.name }}</option>
          </select>
          <span aria-hidden="true">→</span>
          <label class="sr-only" for="cross-to">到</label>
          <select id="cross-to" v-model="toSeq" class="control">
            <option value="current">当前正文</option>
            <option v-for="item in baselines" :key="item.seq" :value="item.seq" :disabled="item.seq === fromSeq">{{ item.label }} {{ item.name }}</option>
          </select>
          <button type="submit" class="ghost" :disabled="fromSeq === null || fromSeq === toSeq || crossPhase === 'loading'">{{ crossPhase === "loading" ? "正在对比…" : "对比" }}</button>
          <button v-if="cross || crossPhase === 'error'" type="button" class="subtle" @click="closeCross">返回基线列表</button>
        </form>
        <StatePanel v-if="crossPhase === 'error'" kind="error" title="对比失败" :text="crossError"><button type="button" class="ghost" @click="startCross">重试</button></StatePanel>
        <section v-else-if="cross && crossPhase === 'ready'" class="cross" :aria-label="`基线对比 ${cross.label}`">
          <nav class="cross-docs" :aria-label="`${cross.label} 涉及的文档`">
            <p class="cross-summary">{{ cross.label }}：{{ crossChanged ? `${crossChanged} 份文档有变化` : "没有文档变化" }}</p>
            <button v-for="row in crossRows" :key="row.id" type="button" class="cross-doc" :class="[row.state, { on: row.id === crossDoc }]" :aria-current="row.id === crossDoc ? 'true' : undefined" @click="crossDoc = row.id">
              <span class="cd-name">{{ row.name }}</span>
              <small>{{ row.note }}</small>
            </button>
          </nav>
          <div class="cross-diff">
            <StatePanel v-if="!crossActive" kind="empty" title="选择左侧的文档查看变化" text="" />
            <template v-else>
              <div class="compare-head"><h3>{{ crossActive.name }}：{{ cross.label }}</h3><router-link class="text" :to="{ name: 'document', params: { id: projectId, documentId: crossActive.id } }">打开文档</router-link></div>
              <StatePanel v-if="crossActive.state === 'same'" kind="empty" title="这份文档没有变化" text="" />
              <StatePanel v-else-if="crossActive.base === null || crossActive.compare === null" kind="empty" :title="crossActive.note" :text="crossActive.base === null ? '这份文档只在后一个基线里，没有可比的旧版本。' : '这份文档只在前一个基线里。'" />
              <DiffView v-else :key="`${crossActive.id}:${crossActive.base}:${crossActive.compare}`" :document-id="crossActive.id" :base="crossActive.base" :compare="crossActive.compare" />
            </template>
          </div>
        </section>
        <div v-if="baselines.length && !cross && crossPhase !== 'error'" class="baseline-grid">
          <ol class="timeline" aria-label="基线时间线">
            <li v-for="item in baselines" :key="item.id">
              <button type="button" class="timeline-item" :class="{ on: item.seq === selected }" :aria-current="item.seq === selected ? 'true' : undefined" @click="pick(item.seq)">
                <span class="tag info">{{ item.label }}</span>
                <strong class="tl-name">{{ item.name }}</strong>
                <small>{{ formatDateTime(item.createdAt) }} · {{ item.author?.name ?? "未记录" }} · {{ item.documentCount }} 份文档</small>
              </button>
            </li>
          </ol>
          <div class="baseline-detail">
            <StatePanel v-if="detailPhase === 'loading'" kind="loading" text="正在读取基线内容…" />
            <StatePanel v-else-if="detailPhase === 'error'" kind="error" title="基线内容加载失败" :text="errorText"><button type="button" class="ghost" @click="selected !== null && pick(selected)">重试</button></StatePanel>
            <template v-else-if="detail">
              <div class="table-wrap">
                <table>
                  <thead><tr><th>文档</th><th>版本</th><th>与当前相比</th><th class="actions">操作</th></tr></thead>
                  <tbody>
                    <tr v-for="row in detail.documents" :key="row.document.id">
                      <td><router-link class="text" :to="{ name: 'document', params: { id: projectId, documentId: row.document.id } }">{{ row.document.name }}</router-link></td>
                      <td><span class="tag plain">{{ row.version.label }}</span></td>
                      <td>
                        <span v-if="row.changed">基线后有修改：+{{ row.sinceBaseline.paragraphsAdded }} 段 / −{{ row.sinceBaseline.paragraphsRemoved }} 段</span>
                        <span v-else class="muted">无变化</span>
                      </td>
                      <td class="actions"><button type="button" class="subtle sm" :disabled="!row.changed" @click="comparing = { documentId: row.document.id, name: row.document.name, seq: row.version.seq }">对比</button></td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <section v-if="comparing" class="compare-card" :aria-label="`${comparing.name} 对比`">
                <div class="compare-head"><h3>{{ comparing.name }}：{{ detail.label }} → 当前</h3><button type="button" class="ghost" @click="comparing = null">关闭对比</button></div>
                <DiffView :document-id="comparing.documentId" :base="comparing.seq" compare="current" />
              </section>
            </template>
          </div>
        </div>
      </section>
      <dialog ref="dialog" class="app-dialog" aria-labelledby="baseline-create-title" @cancel.prevent="closeCreate">
        <form novalidate :aria-busy="submitting" @submit.prevent="submit">
          <div class="dialog-head"><h2 id="baseline-create-title">建立基线</h2></div>
          <div class="dialog-body create-body">
            <label class="stacked-label">
              <span class="required">基线名称</span>
              <input v-model="name" class="control" type="text" maxlength="120" :disabled="submitting" :aria-invalid="Boolean(nameError)" aria-describedby="baseline-name-error" @input="nameError = ''" />
              <span v-if="nameError" id="baseline-name-error" class="field-error" role="alert">{{ nameError }}</span>
            </label>
            <div class="stacked-label" role="group" aria-labelledby="baseline-docs-label">
              <span id="baseline-docs-label">包含的文档</span>
              <div class="doc-pick">
              <p v-if="docsPhase === 'loading'" class="hint" role="status"><span class="spinner" aria-hidden="true"></span> 正在读取文档…</p>
              <p v-else-if="docsPhase === 'ready' && !docs.length" class="hint">项目里还没有文档。</p>
              <label v-for="doc in docs" :key="doc.id" class="check-row"><input v-model="chosen" type="checkbox" :value="doc.id" :disabled="submitting" />{{ doc.name }}</label>
              </div>
            </div>
            <p v-if="submitError" class="field-error" role="alert">{{ submitError }}</p>
          </div>
          <div class="dialog-actions">
            <button type="button" class="ghost" :disabled="submitting" @click="closeCreate">取消</button>
            <button type="submit" class="primary" :disabled="submitting || docsPhase !== 'ready'">{{ submitting ? "正在建立…" : "建立基线" }}</button>
          </div>
        </form>
      </dialog>
    </template>
  </ProjectFrame>
</template>

<style scoped>
/* 验收 P2-2：与「分工」页同样的内边距和页头对齐 */
.baseline-page { display: flex; flex-direction: column; gap: var(--space-4); padding: var(--space-5) var(--space-6) var(--space-6); }
.document-head { display: flex; align-items: center; justify-content: space-between; gap: var(--space-4); }
.document-head .page-desc { margin: var(--space-1) 0 0; }
.baseline-grid { display: grid; grid-template-columns: 360px minmax(0, 1fr); gap: var(--space-5); align-items: start; }
.timeline { list-style: none; margin: 0; padding: 0 0 0 var(--space-3); border-left: 2px solid var(--rule); display: flex; flex-direction: column; gap: var(--space-2); }
.timeline-item { display: grid; gap: var(--space-1); width: 100%; padding: var(--space-2) var(--space-3); text-align: left; background: var(--sheet); border: 1px solid var(--rule); border-radius: var(--radius-sheet); font: inherit; color: var(--ink); cursor: pointer; justify-items: start; }
.timeline-item.on { border-color: var(--stamp); background: var(--stamp-wash); }
.timeline-item:focus-visible { outline: var(--focus-outline); box-shadow: var(--focus-shadow); }
.tl-name { overflow-wrap: anywhere; }
.timeline-item small { color: var(--ink-secondary); font-size: var(--text-xs); }
.baseline-detail { display: flex; flex-direction: column; gap: var(--space-4); min-width: 0; }
.actions { text-align: right; white-space: nowrap; }
.muted { color: var(--ink-tertiary); }
.compare-card { max-width: 1200px; display: flex; flex-direction: column; gap: var(--space-3); }
.compare-head { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); }
.compare-head h3 { margin: 0; font-size: var(--text-lg); }
.cross-bar { display: flex; align-items: center; gap: var(--space-2); }
.cross-bar .control { width: 220px; }
.cross-label { color: var(--ink-secondary); font-size: var(--text-sm); }
.cross { display: grid; grid-template-columns: 300px minmax(0, 1200px); gap: var(--space-5); align-items: start; }
.cross-docs { position: sticky; top: var(--space-3); display: flex; flex-direction: column; gap: var(--space-1); max-height: calc(100vh - 260px); overflow: auto; }
.cross-summary { margin: 0 0 var(--space-2); color: var(--ink-secondary); font-size: var(--text-sm); }
.cross-doc { display: grid; gap: 2px; padding: var(--space-2) var(--space-3); border: 1px solid transparent; border-radius: var(--radius-control); background: none; font: inherit; color: var(--ink); text-align: left; cursor: pointer; }
.cross-doc:hover { background: var(--bg-hover); }
.cross-doc.on { background: var(--stamp-wash); border-color: var(--stamp); }
.cross-doc:focus-visible { outline: var(--focus-outline); box-shadow: var(--focus-shadow); }
.cross-doc small { color: var(--ink-secondary); font-size: var(--text-xs); }
.cross-doc.same { color: var(--ink-tertiary); }
.cross-doc.added small { color: var(--ok); }
.cross-doc.removed small { color: var(--fault); }
.cd-name { overflow-wrap: anywhere; }
.cross-diff { display: flex; flex-direction: column; gap: var(--space-3); min-width: 0; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
.doc-pick { border: 1px solid var(--rule); border-radius: var(--radius-control); padding: var(--space-3); margin: 0; display: flex; flex-direction: column; gap: var(--space-2); max-height: 320px; overflow: auto; }
.check-row { display: flex; align-items: center; gap: var(--space-2); }
</style>
