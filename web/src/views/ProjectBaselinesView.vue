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
const comparing = ref<{ documentId: string; name: string; seq: number; to?: number } | null>(null);

const message = (error: unknown) => (error instanceof ApiError ? error.message : "暂时不能连接服务。");
const canManage = (project: ProjectRecord | null | undefined) =>
  Boolean(project && !project.archivedAt && (project.permissions?.projectRole === "owner" || project.permissions?.canManageMembers));

async function load() {
  phase.value = "loading";
  try {
    baselines.value = (await listProjectBaselines(projectId.value)).items;
    phase.value = "ready";
    const wanted = Number(route.query.seq);
    if (route.query.document && baselines.value.some((b) => b.seq === wanted)) void openFromQuery(wanted);
    else if (baselines.value.length && !baselines.value.some((b) => b.seq === selected.value)) void pick(baselines.value[0].seq);
  } catch (error) { errorText.value = message(error); phase.value = "error"; }
}
async function pick(seq: number) {
  selected.value = seq; comparing.value = null; detailPhase.value = "loading";
  try { detail.value = await getProjectBaseline(projectId.value, seq); detailPhase.value = "ready"; }
  catch (error) { errorText.value = message(error); detailPhase.value = "error"; }
}
/** 依赖链「上游有新基线 / 查看变化」带 ?seq=&document=&from=&to= 进来：选中基线并直接打开该文档的对比。 */
async function openFromQuery(seq: number) {
  await pick(seq);
  const row = detail.value?.documents.find((r) => r.document.id === String(route.query.document));
  if (!row) return;
  const from = Number(route.query.from), to = Number(route.query.to);
  comparing.value = Number.isFinite(from) && from > 0 && Number.isFinite(to) && to > 0 && from !== to
    ? { documentId: row.document.id, name: row.document.name, seq: from, to }
    : { documentId: row.document.id, name: row.document.name, seq: row.version.seq };
}
watch(projectId, () => { selected.value = null; void load(); }, { immediate: true });

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
        <div v-else class="baseline-grid">
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
                      <td><router-link :to="{ name: 'document', params: { id: projectId, documentId: row.document.id } }">{{ row.document.name }}</router-link></td>
                      <td><span class="tag plain">{{ row.version.label }}</span></td>
                      <td>
                        <span v-if="row.changed">基线后有修改：+{{ row.sinceBaseline.paragraphsAdded }} 段 / −{{ row.sinceBaseline.paragraphsRemoved }} 段</span>
                        <span v-else class="muted">无变化</span>
                      </td>
                      <td class="actions"><button type="button" class="ghost" :disabled="!row.changed" @click="comparing = { documentId: row.document.id, name: row.document.name, seq: row.version.seq }">对比</button></td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <section v-if="comparing" class="compare-card" :aria-label="`${comparing.name} 对比`">
                <div class="compare-head"><h3>{{ comparing.name }}：{{ comparing.to ? `版本 ${comparing.seq} → ${detail.label}` : `${detail.label} → 当前` }}</h3><button type="button" class="ghost" @click="comparing = null">关闭对比</button></div>
                <DiffView :key="`${comparing.documentId}-${comparing.seq}-${comparing.to ?? 'current'}`" :document-id="comparing.documentId" :base="comparing.seq" :compare="comparing.to ?? 'current'" />
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
            <fieldset class="doc-pick">
              <legend>包含的文档</legend>
              <p v-if="docsPhase === 'loading'" class="hint" role="status"><span class="spinner" aria-hidden="true"></span> 正在读取文档…</p>
              <p v-else-if="docsPhase === 'ready' && !docs.length" class="hint">项目里还没有文档。</p>
              <label v-for="doc in docs" :key="doc.id" class="check-row"><input v-model="chosen" type="checkbox" :value="doc.id" :disabled="submitting" />{{ doc.name }}</label>
            </fieldset>
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
.baseline-page { display: flex; flex-direction: column; gap: var(--space-4); }
.document-head { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--space-4); }
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
.doc-pick { border: 1px solid var(--rule); border-radius: var(--radius-control); padding: var(--space-3); margin: 0; display: flex; flex-direction: column; gap: var(--space-2); max-height: 320px; overflow: auto; }
.check-row { display: flex; align-items: center; gap: var(--space-2); }
</style>
