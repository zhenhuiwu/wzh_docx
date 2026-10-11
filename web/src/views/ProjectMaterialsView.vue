<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { ApiError } from "../api/client";
import { uploadFile } from "../api/files";
import {
  confirmFact,
  createFact,
  listBindings,
  listFacts,
  reviseFact,
  setBindings,
  type KnowledgeBinding,
  type ProjectFact,
} from "../api/facts";
import { getCurrentSession } from "../api/identity";
import { createKnowledgeBase, createSource, listKnowledgeBases, listSources, type KnowledgeBase } from "../api/knowledge";
import { getProject, type ProjectRecord } from "../api/projects";
import ProjectFrame from "../components/ProjectFrame.vue";

interface Preview {
  fact: ProjectFact;
  previous: ProjectFact["value"];
  hadPrevious: boolean;
  confirmReason: string;
}

interface MaterialRow {
  id: string;
  name: string;
  baseName: string;
  version: string;
  status: string;
}

const route = useRoute();
const projectId = computed(() => String(route.params.id));
const project = ref<ProjectRecord | null>(null);
const permissions = ref<string[]>([]);
const facts = ref<ProjectFact[]>([]);
const total = ref(0);
const page = ref(1);
const bases = ref<KnowledgeBase[]>([]);
const bindings = ref<KnowledgeBinding[]>([]);
const selected = ref<string[]>([]);
const projectRevision = ref(0);
const materials = ref<MaterialRow[]>([]);
const phase = ref<"loading" | "ready" | "error">("loading");
const refreshing = ref(false);
const errorText = ref("");
const notice = ref("");
const formError = ref("");
const tab = ref<"materials" | "facts" | "terms">("facts");
const draftName = ref("");
const draftState = ref("");
const appliedName = ref("");
const appliedState = ref("");
const materialDraft = ref("");
const materialQuery = ref("");
const label = ref("");
const value = ref("");
const reason = ref("");
const asVersion = ref(false);
const editing = ref<ProjectFact | null>(null);
const submitting = ref(false);
const uploadName = ref("");
const uploadFileRef = ref<File | null>(null);
const preview = ref<Preview | null>(null);
const active = ref<ProjectFact | null>(null);
const showSource = ref(false);

// A05 Q-1：归档项目完全只读（服务端对事实、知识库关联写入返回 409 PROJECT_ARCHIVED），界面不再给写入口。
const archived = computed(() => Boolean(project.value?.archivedAt));
const canEdit = computed(() => !archived.value && permissions.value.includes("facts.edit"));
const canConfirm = computed(() => !archived.value && permissions.value.includes("facts.confirm"));
const canBind = computed(() => !archived.value && permissions.value.includes("projects.manage"));
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / 10)));
const filtering = computed(() => Boolean(appliedName.value || appliedState.value));
const shownMaterials = computed(() => {
  const query = materialQuery.value.trim();
  if (!query) return materials.value;
  return materials.value.filter((item) => item.name.includes(query) || item.baseName.includes(query));
});

watch(asVersion, (on) => {
  if (on) label.value = "软件版本";
});

function messageOf(error: unknown) {
  return error instanceof ApiError ? error.message : "暂时不能连接服务。";
}

function stateLabel(state: string) {
  if (state === "confirmed") return "已确认";
  if (state === "candidate") return "候选";
  if (state === "superseded") return "已替代";
  return "未知";
}

function sourceText(fact: ProjectFact) {
  if (fact.sources.length > 0) {
    return fact.sources.map((item) => `${item.sourceName}（版本 ${item.sourceVersion}）`).join("、");
  }
  return fact.decisionReason || "—";
}

function parseLabel(status: string) {
  if (status === "uploaded") return "已上传，尚未解析";
  return "待处理";
}

function displayValue(input: ProjectFact["value"]) {
  if (input === null || input === "") return "—";
  return String(input);
}

async function loadBases() {
  const first = await listKnowledgeBases(1);
  const items = [...first.items];
  const pages = Math.min(5, Math.ceil(first.total / 10));
  for (let next = 2; next <= pages; next += 1) {
    const pageResult = await listKnowledgeBases(next);
    items.push(...pageResult.items);
  }
  bases.value = items;
}

async function loadMaterials(items: KnowledgeBinding[]) {
  const rows: MaterialRow[] = [];
  for (const binding of items) {
    const sources = await listSources(binding.baseId);
    for (const source of sources) {
      rows.push({
        id: source.id,
        name: source.name,
        baseName: binding.name,
        version: source.version,
        status: source.status,
      });
    }
  }
  materials.value = rows;
}

async function loadFacts() {
  const data = await listFacts(projectId.value, {
    page: page.value,
    name: appliedName.value,
    state: appliedState.value,
  });
  facts.value = data.items;
  total.value = data.total;
  page.value = data.page;
  const current = active.value ? data.items.find((item) => item.id === active.value?.id && item.revision === active.value?.revision) : undefined;
  active.value = current ?? data.items[0] ?? null;
}

async function load() {
  phase.value = "loading";
  errorText.value = "";
  try {
    const session = await getCurrentSession();
    permissions.value = session.permissions;
    project.value = await getProject(projectId.value);
    await loadFacts();
    await loadBases();
    const saved = await listBindings(projectId.value);
    bindings.value = saved.items;
    selected.value = saved.items.map((item) => item.baseId);
    projectRevision.value = saved.projectRevision;
    await loadMaterials(saved.items);
    phase.value = "ready";
  } catch (error) {
    phase.value = "error";
    errorText.value = messageOf(error);
  }
}

async function refreshFacts() {
  refreshing.value = true;
  try {
    await loadFacts();
  } catch (error) {
    formError.value = messageOf(error);
  } finally {
    refreshing.value = false;
  }
}

function search() {
  appliedName.value = draftName.value.trim();
  appliedState.value = draftState.value;
  page.value = 1;
  preview.value = null;
  void refreshFacts();
}

function resetQuery() {
  draftName.value = "";
  draftState.value = "";
  appliedName.value = "";
  appliedState.value = "";
  page.value = 1;
  preview.value = null;
  void refreshFacts();
}

function searchMaterials() {
  materialQuery.value = materialDraft.value.trim();
}

function resetMaterials() {
  materialDraft.value = "";
  materialQuery.value = "";
}

function cancelEdit() {
  editing.value = null;
  label.value = "";
  value.value = "";
  reason.value = "";
  asVersion.value = false;
  formError.value = "";
}

function beginEdit(fact: ProjectFact) {
  editing.value = fact;
  label.value = fact.label;
  value.value = displayValue(fact.value) === "—" ? "" : displayValue(fact.value);
  reason.value = "";
  asVersion.value = fact.key === "software_version";
  preview.value = null;
  formError.value = "";
  showSource.value = false;
}

async function saveFact() {
  formError.value = "";
  notice.value = "";
  const nextLabel = asVersion.value ? "软件版本" : label.value.trim();
  const nextValue = value.value.trim();
  const nextReason = reason.value.trim();
  if (!nextLabel || !nextValue || !nextReason) {
    formError.value = "请填写名称、值和依据";
    return;
  }
  submitting.value = true;
  try {
    const saved = editing.value
      ? await reviseFact(editing.value.id, {
          expectedRevision: editing.value.revision,
          value: nextValue,
          decisionReason: nextReason,
        })
      : await createFact(projectId.value, {
          key: asVersion.value ? "software_version" : `fact-${crypto.randomUUID()}`,
          label: nextLabel,
          value: nextValue,
          decisionReason: nextReason,
        });
    notice.value = editing.value
      ? "已提出修改。确认前，已确认的值不会变。"
      : "已登记为候选。确认前不会替换已确认的值。";
    cancelEdit();
    await refreshFacts();
    active.value = saved;
    if (canConfirm.value) await openConfirm(saved);
  } catch (error) {
    formError.value = messageOf(error);
  } finally {
    submitting.value = false;
  }
}

async function openConfirm(fact: ProjectFact) {
  if (!canConfirm.value || fact.state !== "candidate") return;
  formError.value = "";
  try {
    const confirmed = await listFacts(projectId.value, { state: "confirmed", name: fact.label, pageSize: 50 });
    const previous = confirmed.items.find((item) => item.id === fact.id);
    preview.value = {
      fact,
      previous: previous ? previous.value : null,
      hadPrevious: Boolean(previous),
      confirmReason: fact.decisionReason || "确认",
    };
  } catch (error) {
    formError.value = messageOf(error);
  }
}

async function confirmPending() {
  if (!preview.value) return;
  submitting.value = true;
  formError.value = "";
  try {
    const hadPrevious = preview.value.hadPrevious;
    const frozen = preview.value;
    await confirmFact(frozen.fact.id, frozen.fact.revision, frozen.confirmReason);
    preview.value = null;
    reason.value = "";
    notice.value = hadPrevious
      ? "已确认新修订。旧值仍保留。当前有效的文档会标为待复核。"
      : "已确认。这是第一次确认，已有文档不会因此改成待复核。";
    await refreshFacts();
  } catch (error) {
    formError.value = messageOf(error);
  } finally {
    submitting.value = false;
  }
}

function toggle(id: string) {
  selected.value = selected.value.includes(id)
    ? selected.value.filter((item) => item !== id)
    : [...selected.value, id];
}

async function saveBindings() {
  submitting.value = true;
  notice.value = "";
  formError.value = "";
  try {
    const saved = await setBindings(projectId.value, projectRevision.value, selected.value);
    if (saved) {
      bindings.value = saved.items;
      projectRevision.value = saved.projectRevision;
      selected.value = saved.items.map((item) => item.baseId);
      await loadMaterials(saved.items);
    }
    notice.value = "已保存知识库关联。没有权限的库不会被复制进来。";
  } catch (error) {
    formError.value = messageOf(error);
  } finally {
    submitting.value = false;
  }
}

async function uploadToProject() {
  formError.value = "";
  const frozenProject = projectId.value;
  const frozenName = uploadName.value.trim();
  const frozenFile = uploadFileRef.value;
  if (!frozenName || !frozenFile) {
    formError.value = "请填写资料名称并选择文件";
    return;
  }
  const frozenRevision = projectRevision.value;
  const frozenSelected = [...selected.value];
  const frozenProjectName = project.value?.name || "项目";
  submitting.value = true;
  try {
    const session = await getCurrentSession();
    let base = bases.value.find((item) => item.scope === "project" && item.projectId === frozenProject);
    if (!base) {
      base = await createKnowledgeBase({
        name: `${frozenProjectName}资料`,
        summary: "本项目资料",
        scope: "project",
        projectId: frozenProject,
        ownerId: session.user.id,
      });
    }
    const baseId = base.id;
    const stored = await uploadFile(frozenFile, "source", { baseId, idempotencyKey: crypto.randomUUID() });
    await createSource(baseId, frozenName, stored.id);
    const nextSelected = frozenSelected.includes(baseId) ? frozenSelected : [...frozenSelected, baseId];
    const saved = await setBindings(frozenProject, frozenRevision, nextSelected);
    if (projectId.value === frozenProject && saved) {
      bindings.value = saved.items;
      projectRevision.value = saved.projectRevision;
      selected.value = saved.items.map((item) => item.baseId);
      await loadMaterials(saved.items);
      uploadName.value = "";
      uploadFileRef.value = null;
      notice.value = "已上传，待解析。";
    } else {
      notice.value = "文件已登记到原来的项目，没有改当前项目的关联。";
    }
  } catch (error) {
    formError.value = messageOf(error);
  } finally {
    submitting.value = false;
  }
}

function choose(fact: ProjectFact) {
  active.value = fact;
}

onMounted(() => void load());
</script>

<template>
  <ProjectFrame>
  <section class="card">
    <div class="head"><h1>资料</h1></div>
    <p class="notice">公共信息确认后生效。自动提取暂不可用。已上传的文件会标为待解析。</p>
    <p v-if="archived" class="notice" role="status" data-testid="materials-archived">项目已归档，资料、公共信息和知识库关联只能查看。恢复项目后才能修改。</p>
    <p v-if="phase === 'error'" class="notice" role="alert">{{ errorText }}</p>
    <p v-else-if="phase === 'loading'" class="empty">正在读取</p>
    <template v-else>
      <div class="tabs">
        <button type="button" :class="{ on: tab === 'materials' }" @click="tab = 'materials'">资料</button>
        <button type="button" :class="{ on: tab === 'facts' }" @click="tab = 'facts'">项目公共信息</button>
        <button type="button" :class="{ on: tab === 'terms' }" @click="tab = 'terms'">术语</button>
      </div>

      <div v-if="tab === 'terms'">
        <p class="empty">术语还没有单独的维护入口。需要共用的名称和值，请记在公共事实里。</p>
      </div>

      <div v-else-if="tab === 'materials'">
        <form v-if="!archived" class="create" @submit.prevent="uploadToProject">
          <div class="fields">
            <label>资料名称<input v-model="uploadName" class="control" type="text" :disabled="submitting" /></label>
            <label>文件<input class="control" type="file" :disabled="submitting" @change="uploadFileRef = ($event.target as HTMLInputElement).files?.[0] ?? null" /></label>
          </div>
          <div class="actions">
            <button type="submit" class="primary" :disabled="submitting">上传到本项目</button>
          </div>
        </form>
        <p class="notice">也可以关联已有知识库。解析完成后才能检索。</p>
        <form class="query" @submit.prevent="searchMaterials">
          <div class="query-fields">
            <label><span>名称</span><input id="material-query" v-model="materialDraft" class="control" type="search" /></label>
          </div>
          <div class="query-actions">
            <button type="submit" class="primary">查询</button>
            <button type="button" class="ghost" @click="resetMaterials">重置</button>
          </div>
        </form>
        <p v-if="bases.length === 0" class="empty">还没有你能关联的知识库</p>
        <div v-else class="bind">
          <label v-for="base in bases" :key="base.id" class="check">
            <input type="checkbox" :checked="selected.includes(base.id)" :disabled="submitting || !canBind" @change="toggle(base.id)" />
            <span>{{ base.name }}</span>
          </label>
          <p v-if="!canBind && !archived" class="field-error">当前账号不能修改知识库关联。</p>
          <div class="actions">
            <button type="button" class="primary" :disabled="submitting || !canBind" @click="saveBindings">
              {{ submitting ? "正在保存" : "保存关联" }}
            </button>
          </div>
        </div>
        <p v-if="formError && tab === 'materials'" class="field-error" role="alert">{{ formError }}</p>
        <p v-if="notice && tab === 'materials'" class="notice" role="status">{{ notice }}</p>
        <p v-if="bases.length > 0 && shownMaterials.length === 0" class="empty">关联的知识库里还没有资料</p>
        <div v-if="shownMaterials.length > 0" class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>名称</th>
                <th class="from-mid">来源</th>
                <th class="from-mid">版本</th>
                <th>解析</th>
                <th class="from-wide">发布</th>
                <th class="from-wide">引用</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in shownMaterials" :key="item.id">
                <td>
                  {{ item.name }}
                  <span class="below">{{ item.baseName }} · {{ parseLabel(item.status) }}</span>
                </td>
                <td class="from-mid">{{ item.baseName }}</td>
                <td class="from-mid">{{ item.version }}</td>
                <td>{{ parseLabel(item.status) }}</td>
                <td class="from-wide">未发布</td>
                <td class="from-wide">—</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div v-else>
        <form class="query" @submit.prevent="search">
          <div class="query-fields">
            <label><span>名称</span><input id="fact-query" v-model="draftName" class="control" type="search" /></label>
            <label>
              <span>状态</span>
              <select id="fact-state" v-model="draftState" class="control">
                <option value="">全部</option>
                <option value="candidate">候选</option>
                <option value="confirmed">已确认</option>
                <option value="superseded">已替代</option>
              </select>
            </label>
          </div>
          <div class="query-actions">
            <button type="submit" class="primary" :disabled="refreshing">查询</button>
            <button type="button" class="ghost" @click="resetQuery">重置</button>
          </div>
        </form>
        <form v-if="canEdit" class="create" @submit.prevent="saveFact">
          <div class="fields">
            <label for="fact-name">名称<input id="fact-name" v-model="label" class="control" type="text" :disabled="submitting || asVersion" /></label>
            <label for="fact-value">值<input id="fact-value" v-model="value" class="control" type="text" :disabled="submitting" /></label>
            <label class="span-2" for="fact-reason">依据<input id="fact-reason" v-model="reason" class="control" type="text" :disabled="submitting" /></label>
          </div>
          <label v-if="!editing" class="check">
            <input v-model="asVersion" type="checkbox" :disabled="submitting" />
            <span>这是软件版本</span>
          </label>
          <p v-if="formError" class="field-error" role="alert">{{ formError }}</p>
          <div class="actions">
            <button id="fact-submit" type="submit" class="primary" :disabled="submitting">{{ submitting ? "正在保存" : editing ? "提出修改" : "登记候选" }}</button>
            <button v-if="editing" type="button" class="ghost" :disabled="submitting" @click="cancelEdit">取消</button>
          </div>
        </form>
        <p v-else class="notice">当前账号不能登记或修改事实。</p>
        <p v-if="notice && tab === 'facts'" class="notice" role="status">{{ notice }}</p>
        <div v-if="preview" class="notice" role="status">
          <p>「{{ preview.fact.label }}」已确认的值是 {{ preview.hadPrevious ? displayValue(preview.previous) : "还没有" }}，待确认的值是 {{ displayValue(preview.fact.value) }}。</p>
          <p>依据：{{ preview.fact.decisionReason || "—" }}。确认前，已确认的值不会变。</p>
          <p v-if="preview.hadPrevious">确认后，请检查本项目相关文档是否需要复核。</p>
          <p v-else>这是第一次确认。已有文档不会因此改成待复核。</p>
          <div class="actions">
            <button type="button" class="primary" :disabled="submitting" @click="confirmPending">{{ submitting ? "正在确认" : "确认新修订" }}</button>
            <button type="button" class="ghost" :disabled="submitting" @click="preview = null">取消</button>
          </div>
        </div>
        <div class="layout" :class="{ 'show-source': showSource }">
          <div>
            <div class="switch">
              <button type="button" class="ghost" @click="showSource = !showSource">{{ showSource ? "返回列表" : "来源" }}</button>
            </div>
            <p v-if="refreshing" class="empty">正在读取</p>
            <p v-else-if="facts.length === 0" class="empty">{{ filtering ? "没有符合条件的事实" : "还没有事实" }}</p>
            <div v-else class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>名称</th>
                    <th>值</th>
                    <th>确认状态</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="fact in facts"
                    :key="`${fact.id}-${fact.revision}`"
                    :class="{ current: active?.id === fact.id && active?.revision === fact.revision }"
                    @click="choose(fact)"
                  >
                    <td>
                      {{ fact.label }}
                      <span class="below">{{ sourceText(fact) }}</span>
                    </td>
                    <td>{{ displayValue(fact.value) }}</td>
                    <td>{{ stateLabel(fact.state) }}</td>
                    <td class="ops" @click.stop>
                      <button v-if="canEdit && fact.state !== 'superseded'" type="button" class="link" @click="beginEdit(fact)">提出修改</button>
                      <button v-if="canConfirm && fact.state === 'candidate'" id="fact-row-confirm" type="button" class="link" @click="openConfirm(fact)">确认</button>
                      <span v-else-if="fact.state === 'candidate'">没有确认权</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div v-if="!refreshing" class="pager">
              <span class="total">共 {{ total }} 条</span>
              <button type="button" class="page" :disabled="page <= 1" @click="page -= 1; refreshFacts()">上一页</button>
              <button type="button" class="page" :disabled="page >= pageCount" @click="page += 1; refreshFacts()">下一页</button>
            </div>
          </div>
          <aside class="source-panel">
            <p class="source-title">来源</p>
            <template v-if="active">
              <p>{{ active.label }}：{{ displayValue(active.value) }}</p>
              <p>{{ sourceText(active) }}</p>
              <p v-if="active.sources.length === 0">这条事实用来源说明登记，还没有资料定位。</p>
              <p v-else-if="active.sources.some((item) => !item.locatorVerified)">定位还没有校验。资料尚未解析。</p>
            </template>
            <p v-else>选择一条事实查看来源。</p>
          </aside>
        </div>
      </div>
    </template>
  </section>
  </ProjectFrame>
</template>

<style scoped>
.tabs {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4) 0;
}

.tabs button {
  height: var(--space-6);
  padding: 0 var(--space-3);
  border: 1px solid var(--rule-strong);
  border-radius: var(--radius-control);
  background: var(--sheet);
  color: var(--ink);
  font-size: var(--text-sm);
  cursor: pointer;
}

.tabs button.on {
  border-color: var(--stamp);
  background: var(--stamp-wash);
  color: var(--stamp);
}

.query-fields label:last-child {
  flex: none;
}

.query-fields label:last-child .control {
  width: calc(var(--space-7) * 4);
}

button.link {
  border: 0;
  background: transparent;
  padding: 0;
  cursor: pointer;
  font-size: var(--text-sm);
}

.bind,
.source-panel {
  padding: var(--space-3) var(--space-4);
}

.check {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin: var(--space-2) 0;
  font-size: var(--text-sm);
}

.layout {
  min-width: 0;
}

.source-panel {
  display: none;
  border-top: 1px solid var(--rule);
  color: var(--ink-secondary);
  font-size: var(--text-sm);
}

.layout.show-source .source-panel {
  display: block;
}

.layout.show-source .table-wrap,
.layout.show-source .pager,
.layout.show-source .empty {
  display: none;
}

.source-title {
  margin: 0 0 var(--space-2);
  color: var(--ink);
  font-size: var(--text-sm);
  font-weight: 600;
}

.switch {
  padding: var(--space-3) var(--space-4) 0;
}

.below {
  display: block;
  margin-top: var(--space-1);
  color: var(--ink-secondary);
  font-size: var(--text-xs);
  font-weight: 400;
}

.table-wrap :deep(table) {
  width: 100%;
}

.table-wrap :deep(th:last-child),
.table-wrap :deep(td.ops) {
  position: static;
  box-shadow: none;
}

.table-wrap :deep(tr.current) {
  background: var(--stamp-wash);
}

.from-wide {
  display: none;
}

@media (min-width: 768px) {
  .from-mid {
    display: table-cell;
  }
}

@media (max-width: 767px) {
  .from-mid {
    display: none;
  }

  .fields {
    grid-template-columns: 1fr;
  }
}

@media (min-width: 1280px) {
  .layout {
    display: grid;
    grid-template-columns: 2fr 1fr;
    align-items: start;
  }

  .source-panel,
  .layout.show-source .table-wrap,
  .layout.show-source .pager,
  .layout.show-source .empty {
    display: block;
  }

  .source-panel {
    border-top: 0;
    border-left: 1px solid var(--rule);
  }

  .switch {
    display: none;
  }

  .below {
    display: none;
  }

  .from-wide {
    display: table-cell;
  }
}
</style>
