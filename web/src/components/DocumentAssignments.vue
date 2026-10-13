<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import StatePanel from "./StatePanel.vue";
import { getDocumentAssignments, updateDocumentAssignments, type DocumentAssignmentsState } from "../api/documentAssignments";

const props = defineProps<{ documentId: string; revision: number; disabled?: boolean }>();
const emit = defineEmits<{ changed: [state: DocumentAssignmentsState]; busy: [value: boolean]; locate: [objectId: string]; selection: [objectIds: string[]]; close: [] }>();
const state = ref<DocumentAssignmentsState | null>(null);
const loading = ref(false);
const saving = ref(false);
const error = ref("");
const saved = ref(false);
const search = ref("");
const tab = ref<"assign" | "guidance">("assign");
const chapter = ref("");
const page = ref(1);
const pageSize = 4;
const pickerOpen = ref(true);
const selected = ref<string[]>([]);
const assignee = ref("");
const enabled = ref(false);
const edits = ref<Record<string, string | null>>({});
const discardPrompt = ref(false);
let request = 0;
const isDirty = computed(() => Boolean(state.value && (enabled.value !== state.value.enabled || Object.keys(edits.value).length)));
const stale = computed(() => Boolean(state.value && state.value.revision !== props.revision));
const locked = computed(() => Boolean(props.disabled || loading.value || saving.value || stale.value || !state.value?.canManage));
const chapters = computed(() => (state.value?.items ?? []).filter(item => item.kind === "heading"));
const sectionItems = computed(() => {
  const result: Array<{ item: DocumentAssignmentsState["items"][number]; chapter: string }> = [];
  let current = "";
  for (const item of state.value?.items ?? []) {
    if (item.kind === "heading") current = item.objectId;
    result.push({ item, chapter: current });
  }
  return result;
});
const visible = computed(() => {
  const text = search.value.trim().toLocaleLowerCase();
  return sectionItems.value.filter(entry => (!chapter.value || entry.chapter === chapter.value)
    && (!text || entry.item.text.toLocaleLowerCase().includes(text))).map(entry => entry.item);
});
const pageCount = computed(() => Math.max(1, Math.ceil(visible.value.length / pageSize)));
const pageItems = computed(() => visible.value.slice((page.value - 1) * pageSize, page.value * pageSize));
const previewText = (text: string) => text.replace(/^#+\s*/, "").replace(/\s+/g, " ").trim();
function chooseTab(value: "assign" | "guidance") {
  tab.value = value;
  pickerOpen.value = value === "assign" || !selected.value.length;
}
function showGuidance() { chooseTab("guidance"); }
function clearSelection() {
  if (!props.disabled && !saving.value && !stale.value) selected.value = [];
}
const kindName = (kind: string) => ({ heading: "标题", paragraph: "段落", table: "表格", list_item: "列表项", image: "图片" }[kind] || "内容");
const currentAssignee = (objectId: string, original: string | null) => Object.hasOwn(edits.value, objectId) ? edits.value[objectId] : original;
function assigneeName(objectId: string, original: string | null, originalName: string | null) {
  const id = currentAssignee(objectId, original);
  return id ? state.value?.members.find(member => member.id === id)?.name || originalName || "成员已移除" : "未分配";
}
function accept(result: DocumentAssignmentsState) {
  state.value = result;
  if (!result.canManage) tab.value = "guidance";
  enabled.value = result.enabled;
  edits.value = {};
  selected.value = [];
  page.value = 1;
  discardPrompt.value = false;
}
async function load() {
  if (saving.value) return;
  const token = ++request;
  loading.value = true; emit("busy", true); error.value = ""; saved.value = false;
  try {
    const result = await getDocumentAssignments(props.documentId);
    if (token === request) accept(result);
  } catch (e) { if (token === request) error.value = e instanceof Error ? e.message : "读取编写分工失败，请重试。"; }
  finally { if (token === request) { loading.value = false; emit("busy", false); } }
}
function assign() {
  if (locked.value || !selected.value.length) return;
  if (assignee.value && !state.value?.members.some(member => member.id === assignee.value)) return;
  for (const item of state.value?.items ?? []) {
    if (!selected.value.includes(item.objectId)) continue;
    const id = assignee.value || null;
    if (id === item.assigneeId) delete edits.value[item.objectId];
    else edits.value[item.objectId] = id;
  }
  if (assignee.value) enabled.value = true;
  saved.value = false;
}
async function save() {
  if (locked.value || !isDirty.value || !state.value) return;
  saving.value = true; emit("busy", true); error.value = ""; saved.value = false;
  try {
    const result = await updateDocumentAssignments(props.documentId, {
      expectedRevision: state.value.revision,
      expectedAssignmentsRevision: state.value.assignmentsRevision,
      enabled: enabled.value,
      items: Object.entries(edits.value).map(([objectId, assigneeId]) => ({ objectId, assigneeId })),
    });
    accept(result); saved.value = true; emit("changed", result);
  } catch (e) { error.value = e instanceof Error ? e.message : "保存失败，请重试。"; }
  finally { saving.value = false; emit("busy", false); }
}
function requestClose() {
  if (loading.value || saving.value) return;
  if (isDirty.value) discardPrompt.value = true;
  else emit("close");
}
watch(() => props.documentId, () => { state.value = null; edits.value = {}; selected.value = []; void load(); });
watch([search, chapter], () => { page.value = 1; });
watch(selected, ids => emit("selection", [...ids]));
onMounted(() => void load());
onUnmounted(() => { request++; emit("busy", false); });
defineExpose({ requestClose, showGuidance, isDirty, reload: () => { if (!isDirty.value) void load(); } });
</script>

<template>
  <aside class="assignments-panel" aria-label="分工与指导">
    <header class="assignments-head">
      <h2>分工与指导</h2>
      <button type="button" class="subtle sm" :disabled="loading || saving" @click="requestClose">关闭</button>
    </header>
    <div class="assignments-body">
      <StatePanel v-if="loading" kind="loading" title="正在读取编写分工" />
      <StatePanel v-else-if="error && !state" kind="error" :text="error">
        <button type="button" class="ghost sm" @click="load">重试</button>
      </StatePanel>
      <template v-else-if="state">
        <div v-if="discardPrompt" class="notice" role="alert">
          <p>分工更改尚未保存。</p>
          <div class="assignments-actions">
            <button type="button" class="ghost sm" @click="discardPrompt = false">继续编辑</button>
            <button type="button" class="ghost sm danger" @click="emit('close')">放弃更改并关闭</button>
          </div>
        </div>
        <div v-if="stale" class="notice" role="alert">
          <p>文档已更新，请重新读取后分配。</p>
          <button type="button" class="ghost sm" :disabled="saving" @click="load">{{ isDirty ? '放弃分工更改并重新读取' : '重新读取' }}</button>
        </div>
        <div v-if="error" class="form-alert" role="alert">
          <p>{{ error }}</p>
          <button type="button" class="ghost sm" :disabled="saving" @click="load">{{ isDirty ? '放弃分工更改并重新读取' : '重新读取' }}</button>
        </div>
        <div class="tabs assignment-tabs" aria-label="分工与指导功能">
          <button type="button" :class="{ active: tab === 'assign' }" :aria-pressed="tab === 'assign'" @click="chooseTab('assign')">分配负责人</button>
          <button type="button" :class="{ active: tab === 'guidance' }" :aria-pressed="tab === 'guidance'" @click="chooseTab('guidance')">编写建议</button>
        </div>
        <details class="assignment-picker" :open="pickerOpen" @toggle="pickerOpen = ($event.target as HTMLDetailsElement).open">
          <summary>选择文档内容<span class="count">{{ selected.length ? `已选 ${selected.length}` : state.items.length }}</span></summary>
          <div class="assignment-query">
            <div v-if="chapters.length" class="field">
              <label for="assignment-chapter">章节</label>
              <select id="assignment-chapter" v-model="chapter" class="control" :disabled="saving">
                <option value="">全部章节</option>
                <option v-for="heading in chapters" :key="heading.objectId" :value="heading.objectId">{{ previewText(heading.text) }}</option>
              </select>
            </div>
            <div class="field">
              <label for="assignment-search">查找内容</label>
              <input id="assignment-search" v-model="search" class="control" type="search" :disabled="saving" placeholder="输入正文关键词" />
            </div>
          </div>
          <StatePanel v-if="!state.items.length" kind="empty" title="暂无可分配内容" text="请先在文档中添加正文。" />
          <StatePanel v-else-if="!visible.length" kind="empty" title="没有匹配的内容" />
          <ul v-else class="assignments-list">
            <li v-for="item in pageItems" :key="item.objectId" class="assignments-item" :class="{ selected: selected.includes(item.objectId) }">
              <input v-model="selected" type="checkbox" :value="item.objectId" :aria-label="`选择${kindName(item.kind)}：${item.text || '空内容'}`" :disabled="props.disabled || saving || loading || stale || (!state.canManage && !item.canEdit)" />
              <div class="assignment-content">
                <div class="assignment-meta"><span>{{ kindName(item.kind) }}</span><span>{{ assigneeName(item.objectId, item.assigneeId, item.assigneeName) }}</span></div>
                <details v-if="previewText(item.text).length > 80" class="assignment-excerpt">
                  <summary>{{ previewText(item.text).slice(0, 80) }}…<span class="text-link">展开全文</span></summary>
                  <p>{{ item.text }}</p>
                </details>
                <p v-else>{{ previewText(item.text) || '空内容' }}</p>
                <button type="button" class="text sm" :disabled="saving || props.disabled" @click="emit('locate', item.objectId)">定位正文</button>
              </div>
            </li>
          </ul>
          <nav v-if="pageCount > 1" class="pager assignment-pager" aria-label="文档内容分页">
            <button type="button" class="ghost sm" :disabled="page <= 1" @click="page--">上一页</button>
            <span>{{ page }} / {{ pageCount }}</span>
            <button type="button" class="ghost sm" :disabled="page >= pageCount" @click="page++">下一页</button>
          </nav>
        </details>
        <div v-if="selected.length" class="assignment-selection">
          <span>已选择 {{ selected.length }} 项</span>
          <button type="button" class="text sm" :disabled="props.disabled || saving || stale" @click="clearSelection">清空选择</button>
        </div>
        <section v-show="tab === 'assign'" class="assignment-settings" aria-label="分配负责人">
          <template v-if="state.canManage">
            <div v-if="state.items.length" class="assignments-batch">
              <div class="field">
                <label for="assignment-member">负责人</label>
                <select id="assignment-member" v-model="assignee" class="control" :disabled="locked">
                  <option value="">未分配</option>
                  <option v-for="member in state.members" :key="member.id" :value="member.id">{{ member.name }}</option>
                </select>
              </div>
              <button type="button" class="ghost sm" :disabled="locked || !selected.length" @click="assign">设置选中内容</button>
            </div>
            <label class="assignments-toggle"><input v-model="enabled" type="checkbox" :disabled="locked" @change="saved = false" />按分工限制编辑范围</label>
            <p class="hint">{{ enabled ? '成员仅编辑自己负责的内容，负责人和管理员可编辑全文。' : '关闭时，有编辑权限的成员均可编写。' }}</p>
          </template>
          <StatePanel v-else kind="denied" title="分工由负责人管理" text="可选择自己负责的内容，修改编写建议。" />
        </section>
        <!-- Keep the slot mounted so switching tabs never discards unsaved guidance. -->
        <div v-show="tab === 'guidance'" class="assignment-guidance"><slot :object-ids="selected" :state="state" /></div>
      </template>
    </div>
    <footer v-if="state?.canManage && !loading && tab === 'assign'" class="assignments-footer">
      <span v-if="saved" class="hint" role="status">分工已保存</span>
      <span v-else-if="isDirty" class="hint">分工有未保存更改</span>
      <button type="button" class="primary" :disabled="locked || !isDirty" @click="save">{{ saving ? '保存中…' : '保存分工' }}</button>
    </footer>
  </aside>
</template>

<style scoped>
.assignments-panel { display: flex; flex-direction: column; min-width: 0; min-height: 0; height: 100%; background: var(--bg-surface); color: var(--text-primary); }
.assignments-head, .assignments-footer { display: flex; align-items: center; justify-content: space-between; gap: var(--space-2); padding: var(--space-3) var(--space-4); }
.assignments-head { border-bottom: var(--border); }
.assignments-head h2 { margin: 0; font-size: var(--text-md); font-weight: var(--weight-semibold); }
.assignments-body { flex: 1; display: flex; flex-direction: column; gap: var(--space-3); min-height: 0; overflow-y: auto; padding: var(--space-4); }
.assignments-toggle, .assignments-actions { display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; }
.assignments-batch { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: end; gap: var(--space-2); }
.assignments-list { list-style: none; padding: 0; margin: 0; }
.assignments-item { display: flex; align-items: flex-start; gap: var(--space-2); padding: var(--space-3) var(--space-2); border-bottom: var(--border); }
.assignment-content { min-width: 0; flex: 1; overflow-wrap: anywhere; }
.assignment-content p { margin: var(--space-2) 0; white-space: pre-wrap; font-size: var(--text-sm); line-height: var(--leading-sm); }
.assignment-meta { display: flex; flex-wrap: wrap; justify-content: space-between; gap: var(--space-2); color: var(--text-secondary); font-size: var(--text-sm); }
.assignments-footer { border-top: var(--border); margin-top: auto; flex-wrap: wrap; }
.assignments-footer .primary { margin-left: auto; }

.assignment-tabs { flex-shrink: 0; }
.assignment-tabs button { flex: 1; }
.assignment-picker { border: var(--border); border-radius: var(--radius-control); min-width: 0; }
.assignment-picker > summary { cursor: pointer; padding: var(--space-3); font-weight: var(--weight-medium); }
.assignment-picker > summary .count { margin-left: var(--space-2); }
.assignment-query { display: grid; gap: var(--space-3); padding: 0 var(--space-3) var(--space-2); }
.assignment-query .control, .assignments-batch .control { width: 100%; min-width: 0; }
.assignment-picker summary:focus-visible { outline: none; box-shadow: var(--focus-shadow); }
.assignments-item.selected { background: var(--bg-active); }
.assignments-item > input { flex-shrink: 0; margin-top: var(--space-1); }
.assignment-excerpt { font-size: var(--text-sm); line-height: var(--leading-sm); margin: var(--space-2) 0; }
.assignment-excerpt summary { cursor: pointer; }
.text-link { display: block; color: var(--text-link); margin-top: var(--space-1); }
.assignment-selection { display: flex; justify-content: space-between; align-items: center; font-size: var(--text-sm); }
.assignment-settings { display: grid; gap: var(--space-3); }
.assignment-settings .hint { margin: 0; }
.assignment-pager { justify-content: space-between; gap: var(--space-2); padding: var(--space-2); }
.assignment-guidance { min-width: 0; }
</style>
