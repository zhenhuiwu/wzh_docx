<script setup lang="ts">
/** 「我的编写」（30 §3.2）：我负责或被分工的全部文档，按 项目 › 文档 分组。分页加载，不设上限。 */
import { computed, onMounted, ref } from "vue";
import { onBeforeRouteLeave } from "vue-router";
import { ApiError } from "../api/client";
import { listMyWriting, type MyWritingItem } from "../api/baselines";
import { documentStatusLabel } from "../api/documents";
import StatePanel from "../components/StatePanel.vue";
import { formatDateTime } from "../format";
import { refreshMyWritingAccess } from "../myWritingAccess";
import { readSession } from "../session";
import MyDocumentFragments from "../components/myWriting/MyDocumentFragments.vue";
import type { FragmentSummary } from "../myBlocks";

const PAGE = 50;
const items = ref<MyWritingItem[]>([]);
const total = ref(0);
const hasMore = ref(false);
const phase = ref<"loading" | "ready" | "error" | "denied">("loading");
const loadingMore = ref(false);
const errorText = ref("");
const query = ref("");
/** 30 §3.2：筛选只有 项目 + 待编写|全部|有冲突（作用于任务树） */
const filter = ref<"todo" | "all" | "conflict">("all");
const projectFilter = ref("");
/** 验收 P1-5：默认直接进入任务树 + 片段；文档列表是次要入口 */
const mode = ref<"list" | "edit">("edit");
const activeId = ref<string | null>(null);
/** 并排视图（Q-7：最多 2 列）：第二列的文档 */
const layout = ref<"tabs" | "split">("tabs");
const secondId = ref<string | null>(null);
const dirtyDocs = ref<Record<string, boolean>>({});
const editorDirty = computed(() => Object.values(dirtyDocs.value).some(Boolean));
function setDirty(id: string, value: boolean) { dirtyDocs.value = { ...dirtyDocs.value, [id]: value }; }
/** 并排视图：工具栏“保存全部”依次保存两列（每列各自报告冲突/失败） */
const columnRefs: Array<{ save: () => Promise<void> } | null> = [null, null];
function setColumnRef(column: number, el: unknown) { columnRefs[column] = (el as { save: () => Promise<void> } | null) ?? null; }
const splitDirty = computed(() => shownDocs.value.some(doc => dirtyDocs.value[doc.id]));
const savingAll = ref(false);
async function saveAll() {
  if (savingAll.value) return;
  savingAll.value = true;
  try { for (const column of [...columnRefs]) await column?.save(); }
  finally { savingAll.value = false; }
}
const fragmentsByDoc = ref<Record<string, FragmentSummary[]>>({});
function setFragments(id: string, value: FragmentSummary[]) { fragmentsByDoc.value = { ...fragmentsByDoc.value, [id]: value }; }
const second = computed(() => editable.value.find((item) => item.id === secondId.value && item.id !== active.value?.id)
  ?? editable.value.find((item) => item.id !== active.value?.id) ?? null);
const shownDocs = computed(() => [active.value, layout.value === "split" ? second.value : null].filter(Boolean) as MyWritingItem[]);
const statusText = { todo: "待编写", written: "已写", conflict: "冲突", rejected: "被拒保存" } as const;
function blocksShown(docId: string): FragmentSummary[] {
  const all = fragmentsByDoc.value[docId] ?? [];
  if (filter.value === "todo") return all.filter((item) => item.status === "todo");
  if (filter.value === "conflict") return all.filter((item) => item.status === "conflict" || item.status === "rejected");
  return all;
}
const tree = computed(() => {
  const map = new Map<string, { id: string; name: string; docs: MyWritingItem[] }>();
  for (const item of editable.value) {
    // 还没读到片段的文档先留着；读到后按筛选隐藏没有匹配块的文档
    if (filter.value !== "all" && fragmentsByDoc.value[item.id] && !blocksShown(item.id).length) continue;
    const group = map.get(item.project.id) ?? { id: item.project.id, name: item.project.name, docs: [] };
    group.docs.push(item); map.set(item.project.id, group);
  }
  return [...map.values()];
});
const collapsed = ref(new Set<string>());
function toggleNode(id: string) {
  const next = new Set(collapsed.value);
  if (next.has(id)) next.delete(id); else next.add(id);
  collapsed.value = next;
}
function dropDirty(keep: string[]) {
  dirtyDocs.value = Object.fromEntries(Object.entries(dirtyDocs.value).filter(([id]) => keep.includes(id)));
}
async function revealBlock(docId: string, key: string) {
  if (!shownDocs.value.some((doc) => doc.id === docId)) selectDoc(docId);
  await new Promise((resolve) => requestAnimationFrame(resolve));
  const node = window.document.getElementById(`frag-${docId}-${key}`);
  node?.scrollIntoView({ behavior: "smooth", block: "start" });
  node?.querySelector<HTMLElement>("textarea")?.focus({ preventScroll: true });
}
function treeKey(event: KeyboardEvent) {
  // APG Tree View：上下键在可见节点间移动
  if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
  const nodes = [...window.document.querySelectorAll<HTMLElement>("#mw-tree [data-tree-node]")];
  const at = nodes.indexOf(event.target as HTMLElement);
  const next = event.key === "Home" ? 0 : event.key === "End" ? nodes.length - 1 : at + (event.key === "ArrowDown" ? 1 : -1);
  if (next < 0 || next >= nodes.length) return;
  event.preventDefault();
  nodes[next].focus();
}
function setLayout(next: "tabs" | "split") {
  if (next === layout.value) return;
  if (next === "tabs" && second.value && dirtyDocs.value[second.value.id] && !window.confirm("并排的另一份文档还有未保存的修改，收起后会丢失。确定？")) return;
  if (next === "tabs") dropDirty(active.value ? [active.value.id] : []);
  layout.value = next;
}
function selectSecond(id: string) {
  if (second.value && dirtyDocs.value[second.value.id] && !window.confirm("这一列的文档还有未保存的修改，切换后会丢失。确定？")) return;
  dropDirty([active.value?.id ?? ""]);
  secondId.value = id;
}
const userId = readSession()?.userId ?? "";
const editable = computed(() => visible.value.filter((item) => item.assignedBlockCount > 0));
const active = computed(() => editable.value.find((item) => item.id === activeId.value) ?? editable.value[0] ?? null);
function leaveOk() {
  return !editorDirty.value || window.confirm("这份文档还有未保存的修改，离开后会丢失。确定离开？");
}
function selectDoc(id: string) {
  if (id === active.value?.id) return;
  if (active.value && dirtyDocs.value[active.value.id] && !window.confirm("这份文档还有未保存的修改，离开后会丢失。确定离开？")) return;
  if (id === second.value?.id && layout.value === "split") secondId.value = active.value?.id ?? null;
  dropDirty(layout.value === "split" && second.value ? [second.value.id] : []);
  activeId.value = id;
}
function setMode(next: "list" | "edit") {
  if (next === mode.value || (mode.value === "edit" && !leaveOk())) return;
  dirtyDocs.value = {}; mode.value = next;
}
onBeforeRouteLeave(() => leaveOk());
function startEdit(id: string) { mode.value = "edit"; activeId.value = id; }
function onTabKey(event: KeyboardEvent, index: number) {
  const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
  if (!step) return;
  const next = editable.value[(index + step + editable.value.length) % editable.value.length];
  selectDoc(next.id);
  (document.getElementById(`mw-tab-${next.id}`) as HTMLElement | null)?.focus();
}

async function load(append = false, silent = false) {
  if (append) loadingMore.value = true; else if (!silent) phase.value = "loading";
  try {
    const page = await listMyWriting(append ? items.value.length : 0, PAGE, query.value.trim());
    items.value = append ? [...items.value, ...page.items] : page.items;
    total.value = page.total; hasMore.value = page.hasMore; phase.value = "ready";
  } catch (error) {
    errorText.value = error instanceof ApiError ? error.message : "暂时不能连接服务。";
    if (!append) phase.value = "error";
  } finally { loadingMore.value = false; }
}
onMounted(async () => {
  if ((await refreshMyWritingAccess()) === false) { phase.value = "denied"; return; }
  await load();
  // 没有分给我的块（如只负责整篇的文档负责人）：直接显示文档列表
  if (!editable.value.length) mode.value = "list";
});

const projects = computed(() => [...new Map(items.value.map((item) => [item.project.id, item.project.name])).entries()]);
const visible = computed(() => items.value.filter((item) => !projectFilter.value || item.project.id === projectFilter.value));
/** 同名文档（如两个项目各有一份“软件开发计划”）在标签上带项目名 */
const duplicateNames = computed(() => {
  const count = new Map<string, number>();
  for (const item of editable.value) count.set(item.name, (count.get(item.name) ?? 0) + 1);
  return new Set([...count].filter(([, n]) => n > 1).map(([name]) => name));
});
const groups = computed(() => {
  const map = new Map<string, { id: string; name: string; docs: MyWritingItem[] }>();
  for (const item of visible.value) {
    const group = map.get(item.project.id) ?? { id: item.project.id, name: item.project.name, docs: [] };
    group.docs.push(item); map.set(item.project.id, group);
  }
  return [...map.values()];
});
const summary = computed(() => {
  const blocks = items.value.reduce((sum, item) => sum + item.assignedBlockCount, 0);
  const projects = new Set(items.value.map((item) => item.project.id)).size;
  return `你在 ${projects} 个项目、${total.value} 份文档里负责${blocks ? ` ${blocks} 处` : "编写"}。`;
});
</script>

<template>
  <section class="view fill my-writing">
    <header class="page-head">
      <div>
        <h1>我的编写</h1>
        <p v-if="phase === 'ready' && items.length" class="page-desc" data-testid="my-writing-summary">{{ summary }}</p>
      </div>
    </header>
    <div class="card">
      <form v-if="phase !== 'denied'" class="toolbar" role="search" @submit.prevent="load()">
        <label class="sr-only" for="my-writing-q">按文档名称查询</label>
        <input id="my-writing-q" v-model="query" class="control" type="search" placeholder="按文档名称查询" :disabled="phase === 'loading'" />
        <button type="submit" class="ghost" :disabled="phase === 'loading'">查询</button>
        <label class="sr-only" for="my-writing-project">项目</label>
        <select id="my-writing-project" v-model="projectFilter" class="control project-pick" :disabled="phase === 'loading'">
          <option value="">全部项目</option>
          <option v-for="[id, name] in projects" :key="id" :value="id">{{ name }}</option>
        </select>
        <span class="spacer"></span>
        <div v-if="mode === 'edit'" class="seg" role="group" aria-label="筛选任务">
          <button v-for="option in ([['todo', '待编写'], ['all', '全部'], ['conflict', '有冲突']] as const)" :key="option[0]" type="button"
            :class="{ on: filter === option[0] }" :aria-pressed="filter === option[0]" @click="filter = option[0]">{{ option[1] }}</button>
        </div>
        <button type="button" class="subtle" :aria-pressed="mode === 'list'" :disabled="mode === 'list' && !editable.length" @click="setMode(mode === 'list' ? 'edit' : 'list')">{{ mode === "list" ? "返回编写" : "文档列表" }}</button>
      </form>
      <StatePanel v-if="phase === 'denied'" kind="denied" title="只读成员没有「我的编写」" text="你在项目里是只读或评审角色，可以从项目进入文档查看。" />
      <StatePanel v-else-if="phase === 'loading'" kind="loading" text="正在读取你负责的文档…" />
      <StatePanel v-else-if="phase === 'error'" kind="error" title="没能加载" :text="errorText"><button type="button" class="ghost" @click="load()">重试</button></StatePanel>
      <StatePanel v-else-if="!visible.length" kind="empty" :title="query || projectFilter ? '没有匹配的文档' : '目前没有分配给你的部分。'" :text="query || projectFilter ? '' : '负责人分配后会出现在这里。'" />
      <div v-else-if="mode === 'edit' && active" class="workspace">
        <nav class="task-tree" aria-label="我的任务">
          <p v-if="!tree.length" class="tree-empty" role="status">{{ filter === "todo" ? "没有待编写的部分。" : "没有冲突。" }}<button type="button" class="subtle sm" @click="filter = 'all'">看全部</button></p>
          <ul id="mw-tree" role="tree" aria-label="项目 › 文档 › 块" @keydown="treeKey">
            <li v-for="group in tree" :key="group.id" role="treeitem" :aria-expanded="!collapsed.has(group.id)" aria-level="1">
              <button type="button" class="node project-node" data-tree-node @click="toggleNode(group.id)">
                <span class="caret" :class="{ open: !collapsed.has(group.id) }" aria-hidden="true">▸</span><span class="label" :title="group.name">{{ group.name }}</span>
              </button>
              <ul v-if="!collapsed.has(group.id)" role="group">
                <li v-for="doc in group.docs" :key="doc.id" role="treeitem" :aria-expanded="!collapsed.has(doc.id)" aria-level="2" :aria-selected="shownDocs.some((item) => item.id === doc.id)">
                  <button type="button" class="node doc-node" :class="{ on: shownDocs.some((item) => item.id === doc.id) }" data-tree-node @click="selectDoc(doc.id)" @dblclick="toggleNode(doc.id)">
                    <span class="caret" :class="{ open: !collapsed.has(doc.id) }" aria-hidden="true" @click.stop="toggleNode(doc.id)">▸</span>
                    <span class="label" :title="doc.name">{{ doc.name }}</span>
                    <small>{{ (fragmentsByDoc[doc.id] ?? []).filter((item) => item.status === "written").length }}/{{ fragmentsByDoc[doc.id]?.length ?? doc.assignedBlockCount }}</small>
                  </button>
                  <ul v-if="!collapsed.has(doc.id) && blocksShown(doc.id).length" role="group">
                    <li v-for="block in blocksShown(doc.id)" :key="block.key" role="treeitem" aria-level="3">
                      <button type="button" class="node block-node" data-tree-node @click="revealBlock(doc.id, block.key)">
                        <span class="label" :title="block.label">{{ block.label }}</span>
                        <span class="state" :class="block.status">{{ statusText[block.status] }}</span>
                      </button>
                    </li>
                  </ul>
                </li>
              </ul>
            </li>
          </ul>
        </nav>
        <div class="editor">
          <div class="editor-bar">
            <div v-if="layout === 'tabs'" class="doc-tabs" role="tablist" aria-label="分给我的文档">
              <button v-for="(doc, index) in editable" :id="`mw-tab-${doc.id}`" :key="doc.id" type="button" role="tab" class="doc-tab"
                :class="{ on: doc.id === active.id }" :aria-selected="doc.id === active.id" :tabindex="doc.id === active.id ? 0 : -1"
                aria-controls="mw-panel" @click="selectDoc(doc.id)" @keydown="onTabKey($event, index)">
                <span class="tab-name" :title="`${doc.name}（${doc.project.name}）`">{{ doc.name }}<template v-if="duplicateNames.has(doc.name)"> · {{ doc.project.name }}</template></span><small>{{ doc.assignedBlockCount }} 处</small>
              </button>
            </div>
            <span v-else class="split-hint">并排对照两份文档，可以各自保存，也可以一起保存。</span>
            <button v-if="layout === 'split'" type="button" class="primary sm" data-testid="mw-save-all" :disabled="!splitDirty || savingAll" @click="saveAll">{{ savingAll ? "正在保存…" : "保存全部" }}</button>
            <div class="seg" role="group" aria-label="编写视图">
              <button type="button" :class="{ on: layout === 'tabs' }" :aria-pressed="layout === 'tabs'" @click="setLayout('tabs')">按文档</button>
              <button type="button" :class="{ on: layout === 'split' }" :aria-pressed="layout === 'split'" :disabled="editable.length < 2" :title="editable.length < 2 ? '至少要有两份分给你的文档' : ''" @click="setLayout('split')">并排</button>
            </div>
          </div>
          <div v-if="layout === 'tabs'" id="mw-panel" role="tabpanel" :aria-labelledby="`mw-tab-${active.id}`">
            <MyDocumentFragments :key="active.id" :doc="active" :user-id="userId" @dirty="setDirty(active.id, $event)" @fragments="setFragments(active.id, $event)" @saved="load(false, true)" />
          </div>
          <div v-else class="split">
            <section v-for="(doc, column) in shownDocs" :key="doc.id" class="column" :aria-label="`第 ${column + 1} 列：${doc.name}`">
              <label class="column-pick">
                <span class="sr-only">第 {{ column + 1 }} 列文档</span>
                <select class="control" :value="doc.id" @change="column === 0 ? selectDoc(($event.target as HTMLSelectElement).value) : selectSecond(($event.target as HTMLSelectElement).value)">
                  <option v-for="item in editable" :key="item.id" :value="item.id" :disabled="item.id === shownDocs[1 - column]?.id">{{ item.name }}（{{ item.project.name }}）</option>
                </select>
              </label>
              <MyDocumentFragments :ref="el => setColumnRef(column, el)" :doc="doc" :user-id="userId" in-split @dirty="setDirty(doc.id, $event)" @fragments="setFragments(doc.id, $event)" @saved="load(false, true)" />
            </section>
          </div>
        </div>
      </div>
      <div v-else class="groups">
        <section v-for="group in groups" :key="group.id" class="group" :aria-label="group.name">
          <h2 class="group-title"><router-link class="text" :to="{ name: 'project', params: { id: group.id } }">{{ group.name }}</router-link></h2>
          <div class="table-wrap">
            <table>
              <thead><tr><th>文档</th><th>我的角色</th><th>状态</th><th>分给我的</th><th>最近版本</th><th>更新时间</th><th class="actions">操作</th></tr></thead>
              <tbody>
                <tr v-for="doc in group.docs" :key="doc.id">
                  <td class="name">{{ doc.name }}</td>
                  <td>{{ doc.myRole === "owner" ? "文档负责人" : "编写" }}</td>
                  <td><span class="tag" :class="doc.status === 'completed' ? 'success' : 'info'">{{ documentStatusLabel[doc.status] ?? doc.status }}</span></td>
                  <td>{{ doc.assignedBlockCount ? `${doc.assignedBlockCount} 处` : "—" }}</td>
                  <td><span v-if="doc.latestBaseline" class="tag plain">{{ doc.latestBaseline }}</span><span v-else class="muted">未建立</span></td>
                  <td>{{ formatDateTime(doc.updatedAt) }}</td>
                  <td class="actions"><button v-if="doc.assignedBlockCount" type="button" class="subtle sm" @click="startEdit(doc.id)">在此编写</button> <router-link class="ghost sm" :to="{ name: 'document', params: { id: doc.project.id, documentId: doc.id } }">去编写</router-link></td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
        <button v-if="hasMore" type="button" class="ghost more" :disabled="loadingMore" @click="load(true)">{{ loadingMore ? "正在加载…" : "加载更多" }}</button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.toolbar { display: flex; align-items: center; gap: var(--space-2); margin-bottom: var(--space-4); }
.toolbar .control { width: 320px; }
.spacer { flex: 1; }
.project-pick { width: 200px !important; }
.seg { display: inline-flex; flex: none; padding: 2px; background: var(--gray-100); border-radius: var(--radius-control); }
.seg button { height: var(--control-h-sm); padding: 0 var(--space-3); border: 0; border-radius: calc(var(--radius-control) - 2px); background: transparent; color: var(--text-secondary); font: inherit; font-size: var(--text-sm); white-space: nowrap; cursor: pointer; }
.seg button.on { background: var(--bg-surface); color: var(--text-primary); font-weight: var(--weight-medium); box-shadow: var(--shadow-1); }
.seg button:disabled { cursor: not-allowed; opacity: .45; }
.seg button:focus-visible { outline: var(--focus-outline); outline-offset: 1px; }
.tree-empty { display: flex; align-items: center; gap: var(--space-2); margin: 0 0 var(--space-3); color: var(--ink-secondary); font-size: var(--text-sm); }
.groups { display: flex; flex-direction: column; gap: var(--space-5); }
.group-title { margin: 0 0 var(--space-2); font-size: var(--text-lg); }
.name { overflow-wrap: anywhere; }
.actions { text-align: right; white-space: nowrap; }
.muted { color: var(--ink-tertiary); }
.more { align-self: center; }
.workspace { display: grid; grid-template-columns: 300px minmax(0, 1fr); gap: var(--space-4); align-items: start; }
.task-tree { position: sticky; top: var(--space-3); max-height: calc(100vh - 220px); overflow: auto; border: 1px solid var(--rule); border-radius: var(--radius-sheet); padding: var(--space-2); background: var(--sheet); }
.task-tree ul { list-style: none; margin: 0; padding: 0; }
.task-tree ul ul { padding-left: var(--space-4); }
.node { display: flex; align-items: center; gap: var(--space-1); width: 100%; min-height: var(--control-h-sm); padding: var(--space-1) var(--space-2); border: 0; border-radius: var(--radius-control); background: none; font: inherit; color: var(--ink); text-align: left; cursor: pointer; }
.node:hover { background: var(--bg-hover); }
.node:focus-visible { outline: var(--focus-outline); box-shadow: var(--focus-shadow); }
.node.on { background: var(--stamp-wash); color: var(--stamp); }
.node .label { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.node small { color: var(--ink-secondary); font-size: var(--text-xs); }
.project-node { font-weight: var(--weight-semibold); }
.block-node { font-size: var(--text-sm); }
.caret { display: inline-block; width: var(--space-4); color: var(--ink-tertiary); transition: transform var(--duration) var(--ease-out); }
.caret.open { transform: rotate(90deg); }
.state { font-size: var(--text-xs); color: var(--ink-tertiary); flex: none; }
.state.written { color: var(--ok); }
.state.conflict, .state.rejected { color: var(--fault); }
.editor { display: flex; flex-direction: column; gap: var(--space-3); min-width: 0; }
.editor-bar { display: flex; align-items: flex-end; gap: var(--space-3); }
.editor-bar .doc-tabs { flex: 1; min-width: 0; }
.split-hint { flex: 1; color: var(--ink-secondary); font-size: var(--text-sm); }
.split { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-4); }
.column { display: flex; flex-direction: column; gap: var(--space-2); min-width: 0; }
.column-pick .control { width: 100%; }
.doc-tabs { display: flex; gap: var(--space-1); border-bottom: 1px solid var(--rule); overflow-x: auto; }
.doc-tab { display: grid; gap: 0; padding: var(--space-2) var(--space-3); background: none; border: 0; border-bottom: 2px solid transparent; font: inherit; color: var(--ink); cursor: pointer; text-align: left; max-width: 280px; }
.doc-tab small { color: var(--ink-secondary); font-size: var(--text-xs); }
.doc-tab.on { border-bottom-color: var(--stamp); color: var(--stamp); }
.doc-tab:focus-visible { outline: var(--focus-outline); box-shadow: var(--focus-shadow); }
.tab-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
</style>
