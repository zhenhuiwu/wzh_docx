<script setup lang="ts">
/** 分工页（30 §3.4）：任务 → 多份文档的章节 → 人，保存时写回 A07 分工；公共框架一处编辑、各文档只读引用。 */
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { ApiError } from "../api/client";
import { assignPublicBlock, createTask, deleteTask, listTasks, savePublicBlock, updateTask,
  type ProjectTask, type PublicBlock, type TasksPayload } from "../api/docChain";
import BlockPicker from "../components/BlockPicker.vue";
import ProjectFrame from "../components/ProjectFrame.vue";
import StatePanel from "../components/StatePanel.vue";

type Pick = { documentId: string; documentName: string; objectId: string; chapter: string };
const route = useRoute();
const projectId = computed(() => String(route.params.id));
const data = ref<TasksPayload | null>(null);
const phase = ref<"loading" | "ready" | "error">("loading");
const errorText = ref("");
const selection = ref<{ kind: "task"; id: string | null } | { kind: "framework"; id: string } | null>(null);
const form = ref({ title: "", description: "", assigneeId: "" as string, targets: [] as Pick[] });
const titleError = ref("");
const formError = ref("");
const busy = ref(false);
const pickerOpen = ref(false);
const frameBody = ref("");
const confirmingDelete = ref(false);

const canManage = computed(() => Boolean(data.value?.canManage));
const task = computed<ProjectTask | null>(() => selection.value?.kind === "task" && selection.value.id ? data.value?.tasks.find((t) => t.id === selection.value?.id) ?? null : null);
const framework = computed<PublicBlock | null>(() => selection.value?.kind === "framework" ? data.value?.frameworks.find((f) => f.id === selection.value?.id) ?? null : null);
const isNew = computed(() => selection.value?.kind === "task" && selection.value.id === null);
const assigneeName = computed(() => data.value?.members.find((m) => m.id === form.value.assigneeId)?.name ?? "负责人");

function message(error: unknown) { return error instanceof ApiError ? error.message : "暂时不能连接服务。"; }

async function load(keep = false) {
  if (!keep) phase.value = "loading";
  try {
    data.value = await listTasks(projectId.value);
    phase.value = "ready";
    if (!selection.value && data.value.tasks.length) selectTask(data.value.tasks[0]);
    else if (selection.value?.kind === "task" && selection.value.id) { const t = data.value.tasks.find((x) => x.id === selection.value?.id); if (t) selectTask(t); else selection.value = null; }
    else if (framework.value) selectFramework(framework.value);
  } catch (error) { phase.value = "error"; errorText.value = message(error); }
}

function selectTask(t: ProjectTask) {
  selection.value = { kind: "task", id: t.id };
  form.value = { title: t.title, description: t.description, assigneeId: t.assignee.id ?? "",
    targets: t.targets.map((x) => ({ documentId: x.documentId, documentName: x.documentName ?? "", objectId: x.objectId, chapter: x.chapter ?? "" })) };
  titleError.value = formError.value = "";
  confirmingDelete.value = false;
}
function selectFramework(f: PublicBlock) { selection.value = { kind: "framework", id: f.id }; frameBody.value = f.body; formError.value = ""; }
function newTask() {
  selection.value = { kind: "task", id: null };
  form.value = { title: "", description: "", assigneeId: "", targets: [] };
  titleError.value = formError.value = "";
}

async function saveTask() {
  if (busy.value) return;
  const title = form.value.title.trim();
  if (!title) { titleError.value = "请填写任务名称。"; return; }
  busy.value = true; formError.value = "";
  const targets = form.value.targets.map((t) => ({ documentId: t.documentId, objectId: t.objectId }));
  try {
    const saved = isNew.value
      ? await createTask(projectId.value, { title, description: form.value.description, assigneeId: form.value.assigneeId || null, targets })
      : await updateTask(task.value!.id, task.value!.revision, { title, description: form.value.description, targets,
          ...(form.value.assigneeId ? { assigneeId: form.value.assigneeId } : { clearAssignee: true }) });
    selection.value = { kind: "task", id: saved.id };
    await load(true);
  } catch (error) {
    formError.value = message(error);
    if (error instanceof ApiError && error.code === "REVISION_CONFLICT") await load(true);
  } finally { busy.value = false; }
}

async function removeTask() {
  if (!task.value || busy.value) return;
  busy.value = true; formError.value = "";
  try { await deleteTask(task.value.id, task.value.revision); selection.value = null; await load(true); }
  catch (error) { formError.value = message(error); }
  finally { busy.value = false; confirmingDelete.value = false; }
}

async function saveFramework() {
  if (!framework.value || busy.value) return;
  busy.value = true; formError.value = "";
  try { await savePublicBlock(framework.value.id, framework.value.revision, frameBody.value); await load(true); }
  catch (error) { formError.value = message(error); if (error instanceof ApiError && error.code === "REVISION_CONFLICT") await load(true); }
  finally { busy.value = false; }
}

async function assignFramework(value: string) {
  if (!framework.value || busy.value) return;
  busy.value = true; formError.value = "";
  try { await assignPublicBlock(framework.value.id, value || null); await load(true); }
  catch (error) { formError.value = message(error); }
  finally { busy.value = false; }
}

function where(t: ProjectTask) {
  const groups = new Map<string, string[]>();
  for (const x of t.targets) groups.set(x.documentName ?? "?", [...(groups.get(x.documentName ?? "?") ?? []), (x.chapter ?? "").split(" ")[0]]);
  return [...groups].map(([d, cs]) => `${d} ${cs.join(",")}`).join("；") || "—";
}

onMounted(() => void load());
</script>

<template>
  <ProjectFrame>
    <div class="plan-body">
      <div class="plan-head">
        <h2 class="section-title">分工</h2>
        <button v-if="canManage && phase === 'ready'" type="button" class="primary" :disabled="busy" @click="newTask">新建任务</button>
      </div>
      <StatePanel v-if="phase === 'loading'" kind="loading" text="正在读取任务…" />
      <StatePanel v-else-if="phase === 'error'" kind="error" title="任务加载失败。" :text="errorText"><button type="button" class="ghost" @click="load()">重试</button></StatePanel>
      <StatePanel v-else-if="data && !data.planDocument" title="还没有开发计划书" text="任务分解写在开发计划书里，请先创建开发计划书。" data-testid="plan-empty"><router-link class="primary" :to="{ name: 'project', params: { id: projectId } }">去创建</router-link></StatePanel>
      <div v-else-if="data" class="plan-grid">
        <div class="table-wrap"><table data-testid="task-table">
          <thead><tr><th>任务</th><th>负责人</th><th>涉及块</th></tr></thead>
          <tbody>
            <tr v-if="!data.tasks.length"><td colspan="3" class="muted">还没有任务。{{ canManage ? "点击“新建任务”添加。" : "负责人分配后会出现在这里。" }}</td></tr>
            <tr v-for="t in data.tasks" :key="t.id" :class="{ sel: selection?.kind === 'task' && selection.id === t.id, mine: t.assignee.isMe }" tabindex="0"
                :aria-selected="selection?.kind === 'task' && selection.id === t.id" @click="selectTask(t)" @keydown.enter="selectTask(t)">
              <td>{{ t.title }}</td><td>{{ t.assignee.name || "未分配" }}</td><td class="muted">{{ where(t) }}</td>
            </tr>
            <tr class="group"><td colspan="3">公共框架</td></tr>
            <tr v-for="f in data.frameworks" :key="f.id" :class="{ sel: selection?.kind === 'framework' && selection.id === f.id, mine: f.assignee.isMe }" tabindex="0"
                :aria-selected="selection?.kind === 'framework' && selection.id === f.id" data-testid="framework-row" @click="selectFramework(f)" @keydown.enter="selectFramework(f)">
              <td>{{ f.name }}</td><td>{{ f.assignee.name || "未分配" }}</td><td class="muted">{{ f.references.length }} 份文档引用</td>
            </tr>
          </tbody>
        </table></div>

        <aside class="detail" aria-label="详情">
          <form v-if="selection?.kind === 'task'" novalidate :aria-busy="busy" @submit.prevent="saveTask">
            <h3>{{ isNew ? "新建任务" : task?.title }}</h3>
            <label class="field"><span class="label required">任务名称</span>
              <input v-model="form.title" class="control" type="text" maxlength="120" :disabled="!canManage || busy" :aria-invalid="titleError ? 'true' : undefined" aria-describedby="task-title-error" @input="titleError = ''" />
              <span v-if="titleError" id="task-title-error" class="field-error" role="alert">{{ titleError }}</span></label>
            <label class="field"><span class="label">负责人</span>
              <select v-model="form.assigneeId" class="control" :disabled="!canManage || busy"><option value="">未分配</option><option v-for="m in data.members" :key="m.id" :value="m.id">{{ m.name }}</option></select></label>
            <div class="field"><span class="label">涉及块（跨文档）</span>
              <ul class="targets">
                <li v-if="!form.targets.length" class="muted">还没有选择章节。</li>
                <li v-for="(t, i) in form.targets" :key="t.documentId + t.objectId">
                  <router-link :to="{ name: 'document', params: { id: projectId, documentId: t.documentId } }">{{ t.documentName }}</router-link> › {{ t.chapter }}
                  <span v-if="task?.targets.find((x) => x.objectId === t.objectId)?.stale" class="tag warning">章节已删除</span>
                  <button v-if="canManage" type="button" class="subtle icon-close" :aria-label="`移除 ${t.chapter}`" :disabled="busy" @click="form.targets.splice(i, 1)"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg></button>
                </li>
              </ul>
              <button v-if="canManage" type="button" class="ghost add" :disabled="busy" @click="pickerOpen = true">＋ 添加块</button>
            </div>
            <label class="field"><span class="label">说明</span><textarea v-model="form.description" class="control" rows="2" :disabled="!canManage || busy" /></label>
            <p class="note">分配后，{{ form.assigneeId ? assigneeName : "负责人" }}只能修改这些块，其他部分只读。</p>
            <p v-if="formError" class="form-alert" role="alert">{{ formError }}</p>
            <div v-if="canManage" class="actions">
              <button type="submit" class="primary" :disabled="busy">{{ busy ? "正在保存…" : "保存" }}</button>
              <template v-if="!isNew">
                <button v-if="!confirmingDelete" type="button" class="ghost danger" :disabled="busy" @click="confirmingDelete = true">删除任务</button>
                <span v-else class="confirm" role="alert">删除任务不会删除文档内容，涉及块的分工会恢复为章节默认。
                  <button type="button" class="ghost danger" :disabled="busy" @click="removeTask">确认删除</button><button type="button" class="ghost" :disabled="busy" @click="confirmingDelete = false">取消</button></span>
              </template>
            </div>
          </form>

          <form v-else-if="framework" novalidate :aria-busy="busy" @submit.prevent="saveFramework">
            <h3>公共：{{ framework.name }}</h3>
            <label class="field"><span class="label">负责人</span>
              <select class="control" :value="framework.assignee.id ?? ''" :disabled="!canManage || busy" @change="assignFramework(($event.target as HTMLSelectElement).value)"><option value="">未分配</option><option v-for="m in data.members" :key="m.id" :value="m.id">{{ m.name }}</option></select></label>
            <label class="field"><span class="label">公共内容（只在这里修改，各文档只读引用）</span>
              <textarea v-model="frameBody" class="control" rows="8" :disabled="!framework.canEdit || busy" data-testid="framework-body" /></label>
            <p class="muted">第 {{ framework.revision }} 版<template v-if="framework.updatedBy"> · {{ framework.updatedBy }}</template></p>
            <div class="field"><span class="label">被引用的位置</span>
              <ul class="targets"><li v-if="!framework.references.length" class="muted">还没有文档引用。</li>
                <li v-for="r in framework.references" :key="r.documentId + r.objectId"><router-link :to="{ name: 'document', params: { id: projectId, documentId: r.documentId } }">{{ r.documentName }}</router-link> › {{ r.chapter }}</li></ul></div>
            <p v-if="formError" class="form-alert" role="alert">{{ formError }}</p>
            <div v-if="framework.canEdit" class="actions"><button type="submit" class="primary" :disabled="busy || frameBody === framework.body">{{ busy ? "正在保存…" : "保存公共内容" }}</button></div>
            <p v-else class="note">只有该公共内容的负责人或项目负责人可以修改。</p>
          </form>
          <p v-else class="muted">选择左侧任务或公共框架查看详情。</p>
        </aside>
      </div>
    </div>
    <BlockPicker v-if="data" :open="pickerOpen" :project-id="projectId" :documents="data.documents" :selected="form.targets" :assignee-id="form.assigneeId || null"
                 @close="pickerOpen = false" @confirm="(v) => { form.targets = v; pickerOpen = false; }" />
  </ProjectFrame>
</template>

<style scoped>
.plan-body { display:grid; gap:var(--space-5); padding:var(--space-5) var(--space-6) var(--space-6); }
.plan-head { display:flex; justify-content:space-between; align-items:center; }
.plan-grid { display:grid; grid-template-columns:3fr 2fr; gap:var(--space-5); align-items:start; }
tr[tabindex] { cursor:pointer; }
tr.sel td { background:var(--stamp-wash); }
tr.mine td:first-child { font-weight:var(--weight-medium); color:var(--stamp); }
tr[tabindex]:focus-visible { outline:none; box-shadow:var(--focus-shadow); }
tr.group td { color:var(--text-tertiary); font-size:var(--text-xs); background:var(--bg-subtle); }
.detail { padding:var(--space-5); border:1px solid var(--border-default); border-radius:var(--radius-sheet); background:var(--bg-surface); }
.detail form { display:grid; gap:var(--space-4); }
.detail h3 { margin:0; font-size:var(--text-lg); overflow-wrap:anywhere; }
.field { display:grid; gap:var(--space-1); }
.label { color:var(--text-secondary); font-size:var(--text-sm); }
.muted { margin:0; color:var(--text-secondary); font-size:var(--text-sm); }
.targets { display:grid; gap:var(--space-2); margin:0; padding-left:var(--space-5); }
.targets li { display:flex; align-items:center; gap:var(--space-2); flex-wrap:wrap; }
.add { justify-self:start; }
.note { margin:0; color:var(--text-tertiary); font-size:var(--text-xs); }
.actions { display:flex; gap:var(--space-2); flex-wrap:wrap; align-items:center; }
.confirm { display:flex; gap:var(--space-2); align-items:center; flex-wrap:wrap; color:var(--fault); font-size:var(--text-sm); }
.danger { color:var(--fault); }
</style>
