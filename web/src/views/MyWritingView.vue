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

const PAGE = 50;
const items = ref<MyWritingItem[]>([]);
const total = ref(0);
const hasMore = ref(false);
const phase = ref<"loading" | "ready" | "error" | "denied">("loading");
const loadingMore = ref(false);
const errorText = ref("");
const query = ref("");
const filter = ref<"all" | "assigned" | "owned">("all");
const mode = ref<"list" | "edit">("list");
const activeId = ref<string | null>(null);
const editorDirty = ref(false);
const userId = readSession()?.userId ?? "";
const editable = computed(() => visible.value.filter((item) => item.assignedBlockCount > 0));
const active = computed(() => editable.value.find((item) => item.id === activeId.value) ?? editable.value[0] ?? null);
function leaveOk() {
  return !editorDirty.value || window.confirm("这份文档还有未保存的修改，离开后会丢失。确定离开？");
}
function selectDoc(id: string) {
  if (id === active.value?.id || !leaveOk()) return;
  editorDirty.value = false; activeId.value = id;
}
function setMode(next: "list" | "edit") {
  if (next === mode.value || (mode.value === "edit" && !leaveOk())) return;
  editorDirty.value = false; mode.value = next;
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
  void load();
});

const visible = computed(() => items.value.filter((item) =>
  filter.value === "all" || (filter.value === "assigned" ? item.assignedBlockCount > 0 : item.myRole === "owner")));
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
        <div class="segmented" role="group" aria-label="视图">
          <button type="button" :class="mode === 'list' ? 'primary' : 'ghost'" :aria-pressed="mode === 'list'" @click="setMode('list')">文档列表</button>
          <button type="button" :class="mode === 'edit' ? 'primary' : 'ghost'" :aria-pressed="mode === 'edit'" :disabled="!editable.length" :title="editable.length ? '' : '没有分给你的块'" @click="setMode('edit')">按文档编写</button>
        </div>
        <div class="segmented" role="group" aria-label="筛选">
          <button v-for="option in ([['all', '全部'], ['assigned', '分给我的'], ['owned', '我负责的文档']] as const)" :key="option[0]" type="button"
            :class="filter === option[0] ? 'primary' : 'ghost'" :aria-pressed="filter === option[0]" @click="filter = option[0]">{{ option[1] }}</button>
        </div>
      </form>
      <StatePanel v-if="phase === 'denied'" kind="denied" title="只读成员没有「我的编写」" text="你在项目里是只读或评审角色，可以从项目进入文档查看。" />
      <StatePanel v-else-if="phase === 'loading'" kind="loading" text="正在读取你负责的文档…" />
      <StatePanel v-else-if="phase === 'error'" kind="error" title="没能加载" :text="errorText"><button type="button" class="ghost" @click="load()">重试</button></StatePanel>
      <StatePanel v-else-if="!visible.length" kind="empty" :title="query ? '没有匹配的文档' : '目前没有分配给你的部分。'" :text="query ? '' : '负责人分配后会出现在这里。'" />
      <div v-else-if="mode === 'edit' && active" class="editor">
        <div class="doc-tabs" role="tablist" aria-label="分给我的文档">
          <button v-for="(doc, index) in editable" :id="`mw-tab-${doc.id}`" :key="doc.id" type="button" role="tab" class="doc-tab"
            :class="{ on: doc.id === active.id }" :aria-selected="doc.id === active.id" :tabindex="doc.id === active.id ? 0 : -1"
            aria-controls="mw-panel" @click="selectDoc(doc.id)" @keydown="onTabKey($event, index)">
            <span class="tab-name" :title="doc.name">{{ doc.name }}</span><small>{{ doc.project.name }} · {{ doc.assignedBlockCount }} 处</small>
          </button>
        </div>
        <div id="mw-panel" role="tabpanel" :aria-labelledby="`mw-tab-${active.id}`">
          <MyDocumentFragments :key="active.id" :doc="active" :user-id="userId" @dirty="editorDirty = $event" @saved="load(false, true)" />
        </div>
      </div>
      <div v-else class="groups">
        <section v-for="group in groups" :key="group.id" class="group" :aria-label="group.name">
          <h2 class="group-title"><router-link :to="{ name: 'project', params: { id: group.id } }">{{ group.name }}</router-link></h2>
          <div class="table-wrap">
            <table>
              <thead><tr><th>文档</th><th>我的角色</th><th>状态</th><th>分给我的</th><th>最近基线</th><th>更新时间</th><th class="actions">操作</th></tr></thead>
              <tbody>
                <tr v-for="doc in group.docs" :key="doc.id">
                  <td class="name">{{ doc.name }}</td>
                  <td>{{ doc.myRole === "owner" ? "文档负责人" : "编写" }}</td>
                  <td><span class="tag" :class="doc.status === 'completed' ? 'success' : 'info'">{{ documentStatusLabel[doc.status] ?? doc.status }}</span></td>
                  <td>{{ doc.assignedBlockCount ? `${doc.assignedBlockCount} 处` : "—" }}</td>
                  <td><span v-if="doc.latestBaseline" class="tag plain">{{ doc.latestBaseline }}</span><span v-else class="muted">未建立</span></td>
                  <td>{{ formatDateTime(doc.updatedAt) }}</td>
                  <td class="actions"><button v-if="doc.assignedBlockCount" type="button" class="ghost" @click="startEdit(doc.id)">在此编写</button> <router-link class="ghost" :to="{ name: 'document', params: { id: doc.project.id, documentId: doc.id } }">去编写</router-link></td>
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
.segmented { display: flex; gap: var(--space-1); margin-left: auto; }
.groups { display: flex; flex-direction: column; gap: var(--space-5); }
.group-title { margin: 0 0 var(--space-2); font-size: var(--text-lg); }
.name { overflow-wrap: anywhere; }
.actions { text-align: right; white-space: nowrap; }
.muted { color: var(--ink-tertiary); }
.more { align-self: center; }
.segmented + .segmented { margin-left: var(--space-3); }
.editor { display: flex; flex-direction: column; gap: var(--space-3); }
.doc-tabs { display: flex; gap: var(--space-1); border-bottom: 1px solid var(--rule); overflow-x: auto; }
.doc-tab { display: grid; gap: 0; padding: var(--space-2) var(--space-3); background: none; border: 0; border-bottom: 2px solid transparent; font: inherit; color: var(--ink); cursor: pointer; text-align: left; max-width: 280px; }
.doc-tab small { color: var(--ink-secondary); font-size: var(--text-xs); }
.doc-tab.on { border-bottom-color: var(--stamp); color: var(--stamp); }
.doc-tab:focus-visible { outline: var(--focus-outline); box-shadow: var(--focus-shadow); }
.tab-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
</style>
