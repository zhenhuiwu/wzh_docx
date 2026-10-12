<script setup lang="ts">
/** A07 编写分工面板（16 §A07、U-9..U-16）：分工模式、整篇负责人、按章节分配。负责人和管理员可改，其他人只看。 */
import { computed, nextTick, onMounted, ref } from "vue";
import { DOCUMENT_TARGET, getAssignments, putAssignmentMode, putAssignments, type AssignmentView } from "../api/assignments";
import { listMembers } from "../api/projects";
import { ApiError } from "../api/client";

const props = defineProps<{ documentId: string; projectId: string; objects: Array<{ id: string; kind: string; markdown: string }>; initialFilter?: string }>();
const emit = defineEmits<{ close: []; changed: [] }>();

const view = ref<AssignmentView | null>(null);
const people = ref<Array<{ id: string; name: string; role: string }>>([]);
const error = ref("");
const busy = ref(false);
const selected = ref<string[]>([]);
const batchAssignees = ref<string[]>([]);
/** 筛选（29 M-3/§4.3）：all / unassigned / person:<id>。 */
const filter = ref(props.initialFilter || "all");
const personFilter = ref(props.initialFilter?.startsWith("person:") ? props.initialFilter.slice(7) : "");
/** 多人分配的选择浮层：row=章节 ID，"@batch" 为批量。 */
const picking = ref<string | null>(null);
const pickIds = ref<string[]>([]);
const confirmMode = ref<"enforce" | "open" | null>(null);
const root = ref<HTMLElement | null>(null);

const headingText = (markdown: string) => markdown.replace(/^ {0,3}#{1,6}[ \t]*/, "").replace(/[ \t]+#+[ \t]*$/, "").trim();
const levelOf = (markdown: string) => (/^ {0,3}(#{1,6})/.exec(markdown)?.[1].length ?? 1);
const explicitOf = (objectId: string) => view.value?.explicit.find(item => item.objectId === objectId) ?? null;
const resolvedOf = (objectId: string) => view.value?.resolved?.find(item => item.objectId === objectId) ?? null;
const headings = computed(() => props.objects.filter(item => item.kind === "heading").map(item => {
  const explicit = explicitOf(item.id);
  const resolved = resolvedOf(item.id);
  return { id: item.id, text: headingText(item.markdown) || "（空标题）", level: levelOf(item.markdown),
    explicit: explicit?.assignees.map(person => person.name).join("、") || "",
    explicitIds: explicit?.assignees.map(person => person.id) ?? [],
    ownerIds: resolved?.owners.map(person => person.id) ?? [],
    inherited: !explicit && resolved?.owners.length ? resolved.owners.map(person => person.name).join("、") : "",
    needsConfirm: Boolean(explicit?.needsConfirm),
    // 16 §4.3：显式负责人里有已停用（或已不是编写成员）的 → 需改派
    disabled: Boolean(explicit?.assignees.some(person => person.userActive === false || person.stillMember === false)) };
}));
const wholeOwner = computed(() => explicitOf(DOCUMENT_TARGET)?.assignees[0]?.id || "");
const eligible = computed(() => people.value.filter(person => person.role === "owner" || person.role === "editor"));
const canAssign = computed(() => Boolean(view.value?.canAssign));
const reassignCount = computed(() => headings.value.filter(item => item.disabled).length);
const shown = computed(() => headings.value.filter(item => filter.value === "unassigned" ? !item.ownerIds.length
  : filter.value === "reassign" ? item.disabled
  : filter.value.startsWith("person:") ? item.ownerIds.includes(filter.value.slice(7)) : true));
function choosePerson(id: string) { personFilter.value = id; filter.value = id ? `person:${id}` : "all"; }
function openPick(target: string, current: string[]) { picking.value = target; pickIds.value = [...current]; }
async function applyPick() {
  const target = picking.value;
  if (!target) return;
  picking.value = null;
  if (target === "@batch") { batchAssignees.value = [...pickIds.value]; await applyBatch(); }
  else await assign([target], [...pickIds.value]);
}
const nameOf = (id: string) => people.value.find(person => person.id === id)?.name || "";

function failText(caught: unknown) {
  if (caught instanceof ApiError) {
    if (caught.code === "REVISION_CONFLICT") {
      const who = (caught.details?.lastActor as { name?: string } | null | undefined)?.name;
      return `分工刚被${who ? ` ${who} ` : "其他人"}调整过，已显示最新，请确认后再切换。`;
    }
    if (caught.code === "PROJECT_ARCHIVED") return "项目已归档，不能修改分工。";
    return caught.message;
  }
  return caught instanceof Error ? caught.message : "操作失败。";
}
async function load() {
  error.value = "";
  try {
    view.value = await getAssignments(props.documentId);
    const members = await listMembers(props.projectId).catch(() => null);
    if (members) people.value = members.items.filter(item => item.userStatus === "active").map(item => ({ id: item.userId, name: item.name, role: item.role }));
  } catch (caught) { error.value = failText(caught); }
}
async function run(action: () => Promise<unknown>) {
  if (!view.value || busy.value) return;
  busy.value = true;
  error.value = "";
  let failed = "";
  try { await action(); emit("changed"); } catch (caught) { failed = failText(caught); }
  busy.value = false;
  await load();
  // 复验 N-3：load() 会清空错误，操作失败的提示放在重新读取之后
  if (failed) error.value = failed;
}
const assign = (objectIds: string[], assignees: string[]) =>
  run(() => putAssignments(props.documentId, objectIds, assignees, view.value!.assignmentsRevision));
/** 16 §4.3：开关都先确认。 */
function askMode() { confirmMode.value = view.value?.assignmentMode === "enforced" ? "open" : "enforce"; }
async function applyMode() {
  const target = confirmMode.value;
  confirmMode.value = null;
  if (target) await run(() => putAssignmentMode(props.documentId, target === "enforce" ? "enforced" : "open", view.value!.assignmentsRevision));
}
function goAssign() { confirmMode.value = null; filter.value = "unassigned"; personFilter.value = ""; }
async function applyBatch() {
  if (!selected.value.length) return;
  await assign([...selected.value], batchAssignees.value);
  selected.value = [];
}
onMounted(async () => {
  await load();
  await nextTick();
  // 29 抽屉：打开后焦点进入面板
  (root.value?.querySelector<HTMLElement>("[data-autofocus]") ?? root.value)?.focus();
});
function onKey(event: KeyboardEvent) {
  if (event.key !== "Escape") return;
  event.stopPropagation();
  if (picking.value) { picking.value = null; return; }
  if (confirmMode.value) { confirmMode.value = null; return; }
  emit("close");
}
defineExpose({ load });
</script>

<template>
  <aside ref="root" class="assign-panel" data-testid="assign-panel" aria-label="编写分工" tabindex="-1" @keydown="onKey">
    <header class="assign-head">
      <h2>编写分工</h2>
      <button type="button" class="subtle icon-btn" aria-label="关闭编写分工" data-autofocus data-testid="assign-close" @click="emit('close')">×</button>
    </header>
    <p v-if="error" class="assign-error" role="alert" data-testid="assign-error">{{ error }}</p>
    <template v-if="view">
      <section class="assign-mode">
        <p data-testid="assign-mode-text"><strong>{{ view.assignmentMode === 'enforced' ? '按分工编辑' : '开放编辑' }}</strong>：{{ view.assignmentMode === 'enforced' ? '编写人员只能修改分配给自己的部分。' : '编写人员可以修改全部内容，分工只作为参考。' }}</p>
        <button v-if="canAssign" type="button" class="ghost sm mode-btn" data-testid="assign-mode-toggle" :disabled="busy" @click="askMode">{{ view.assignmentMode === 'enforced' ? '改为开放编辑' : '开启按分工编辑' }}</button>
      </section>
      <p class="assign-summary" data-testid="assign-summary">未分配章节 {{ view.summary.unassignedSections }} 个<template v-if="view.summary.needsConfirm"> · {{ view.summary.needsConfirm }} 处待确认</template></p>
      <div class="assign-filter" role="group" aria-label="筛选">
        <button type="button" class="subtle sm" :class="{ on: filter === 'all' }" :aria-pressed="filter === 'all'" data-testid="assign-filter-all" @click="choosePerson('')">全部</button>
        <button type="button" class="subtle sm" :class="{ on: filter === 'unassigned' }" :aria-pressed="filter === 'unassigned'" data-testid="assign-filter-unassigned" @click="filter = 'unassigned'; personFilter = ''">未分配</button>
        <button v-if="reassignCount" type="button" class="subtle sm" :class="{ on: filter === 'reassign' }" :aria-pressed="filter === 'reassign'" data-testid="assign-filter-reassign" @click="filter = 'reassign'; personFilter = ''">需改派 {{ reassignCount }}</button>
        <select class="control sm" :value="personFilter" aria-label="按人筛选" data-testid="assign-filter-person" @change="choosePerson(($event.target as HTMLSelectElement).value)">
          <option value="">某人…</option>
          <option v-for="person in eligible" :key="person.id" :value="person.id">{{ person.name }}</option>
        </select>
      </div>
      <label class="assign-row whole">
        <span>整篇负责人</span>
        <select data-testid="assign-doc-owner" class="control sm" :disabled="!canAssign || busy" :value="wholeOwner" @change="assign([DOCUMENT_TARGET], ($event.target as HTMLSelectElement).value ? [($event.target as HTMLSelectElement).value] : [])">
          <option value="">未指定</option>
          <option v-for="person in eligible" :key="person.id" :value="person.id">{{ person.name }}</option>
        </select>
      </label>
      <ul class="assign-list" data-testid="assign-list">
        <li v-for="item in shown" :key="item.id" class="assign-row" :style="{ paddingLeft: `${(item.level - 1) * 14}px` }" :data-heading-id="item.id" data-testid="assign-heading">
          <input v-if="canAssign" v-model="selected" type="checkbox" :value="item.id" :aria-label="`选择 ${item.text}`" />
          <span class="assign-title" :title="item.text">{{ item.text }}</span>
          <span v-if="item.needsConfirm" class="tag warning">待确认</span>
          <span v-if="item.disabled" class="tag warning" data-testid="assign-needs-reassign">负责人已停用</span>
          <span class="assign-who" data-testid="assign-who" :title="item.explicit || item.inherited">{{ item.explicit || (item.inherited ? `随上级（${item.inherited}）` : '未分配') }}</span>
          <button v-if="canAssign" type="button" class="ghost sm" :disabled="busy" :aria-label="`${item.text} 的编写人`" data-testid="assign-edit" @click="openPick(item.id, item.explicitIds)">分配</button>
          <div v-if="picking === item.id" class="assign-pick" data-testid="assign-pick" role="group" :aria-label="`${item.text} 的编写人`">
            <label v-for="person in eligible" :key="person.id"><input v-model="pickIds" type="checkbox" :value="person.id" :data-user-id="person.id" />{{ person.name }}</label>
            <span class="acts"><button type="button" class="primary sm" data-testid="assign-pick-ok" @click="applyPick">{{ pickIds.length ? '确定' : '取消分配' }}</button><button type="button" class="ghost sm" @click="picking = null">取消</button></span>
          </div>
        </li>
        <li v-if="!headings.length" class="assign-empty">文档还没有标题。可以指定整篇负责人。</li>
        <li v-else-if="!shown.length" class="assign-empty" data-testid="assign-filter-empty">没有符合筛选的章节。</li>
      </ul>
      <footer v-if="canAssign && selected.length" class="assign-batch" data-testid="assign-batch">
        <span>已选 {{ selected.length }} 个章节</span>
        <button type="button" class="primary sm" :disabled="busy" data-testid="assign-batch-apply" @click="openPick('@batch', [])">分配给…</button>
        <div v-if="picking === '@batch'" class="assign-pick" data-testid="assign-pick" role="group" aria-label="批量分配给">
          <label v-for="person in eligible" :key="person.id"><input v-model="pickIds" type="checkbox" :value="person.id" :data-user-id="person.id" />{{ person.name }}</label>
          <span class="acts"><button type="button" class="primary sm" data-testid="assign-pick-ok" @click="applyPick">{{ pickIds.length ? `分配给 ${pickIds.map(nameOf).join('、')}` : '取消分配' }}</button><button type="button" class="ghost sm" @click="picking = null">取消</button></span>
        </div>
      </footer>
    </template>
    <p v-else-if="!error" class="assign-empty" role="status">正在读取分工…</p>
    <div v-if="confirmMode && view" class="dialog-backdrop" @keydown.esc.stop="confirmMode = null">
      <section class="dialog card" role="alertdialog" aria-modal="true" aria-labelledby="mode-title" data-testid="assign-mode-confirm">
        <template v-if="confirmMode === 'enforce'">
          <h2 id="mode-title">开启按分工编辑？</h2>
          <p>开启后，编写人员只能修改分配给自己的部分，其他部分只读。正在编辑的人会在 1 分钟内切换，他们在不属于自己部分里尚未保存的修改会放入暂存，不会丢失。</p>
          <p v-if="view.summary.unassignedRoots" class="warn-line" data-testid="assign-mode-unassigned">还有 {{ view.summary.unassignedRoots }} 章未分配，这些章节只有负责人能修改。<template v-if="view.summary.needsConfirm">另有 {{ view.summary.needsConfirm }} 处分工待确认。</template></p>
          <div class="acts"><button type="button" class="ghost" @click="goAssign">先去分配</button><button type="button" class="primary" data-testid="assign-mode-confirm-ok" autofocus @click="applyMode">开启</button></div>
        </template>
        <template v-else>
          <h2 id="mode-title">改为开放编辑？</h2>
          <p>关闭后，所有编写人员都可以修改全部内容，分工只作为参考。</p>
          <div class="acts"><button type="button" class="ghost" @click="confirmMode = null">取消</button><button type="button" class="primary" data-testid="assign-mode-confirm-ok" autofocus @click="applyMode">改为开放编辑</button></div>
        </template>
      </section>
    </div>
  </aside>
</template>

<style scoped>
/* 29 抽屉：并入右侧面板栏（宽度 var(--panel-w)=400），不再浮在正文上 */
.assign-panel { position: sticky; top: 0; align-self: start; height: 100%; min-height: 0; max-height: calc(100vh - 140px); width: var(--panel-w); box-sizing: border-box; border-left: 1px solid var(--border-default); background: var(--bg-surface); display: flex; flex-direction: column; overflow: auto; padding: var(--space-3) var(--space-4); gap: var(--space-3); font-size: var(--text-sm); outline: none; }
.assign-head { display: flex; align-items: center; justify-content: space-between; }
.assign-head h2 { font-size: var(--text-md, 15px); margin: 0; }
.mode-btn { align-self: flex-start; }
.assign-mode { display: flex; flex-direction: column; gap: var(--space-2); }
.assign-mode p, .assign-summary { margin: 0; color: var(--text-secondary); }
.assign-filter { display: flex; gap: var(--space-2); align-items: center; flex-wrap: wrap; }
.assign-filter .on { background: var(--brand-50); color: var(--brand-700); }
.assign-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--space-1); }
.assign-row { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-2); min-height: 30px; }
.assign-row.whole { justify-content: space-between; }
.assign-title { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.assign-who { color: var(--text-secondary); max-width: 140px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.assign-pick { flex-basis: 100%; display: flex; flex-wrap: wrap; gap: var(--space-2); padding: var(--space-2); border: 1px solid var(--border-default); border-radius: var(--radius-control); background: var(--bg-subtle); }
.assign-pick label { display: inline-flex; gap: 4px; align-items: center; }
.assign-pick .acts { flex-basis: 100%; display: flex; gap: var(--space-2); }
.assign-error { color: var(--danger-600); margin: 0; }
.assign-batch { position: sticky; bottom: 0; background: var(--bg-surface); display: flex; flex-wrap: wrap; gap: var(--space-2); align-items: center; padding-top: var(--space-2); border-top: 1px solid var(--border-default); }
.assign-empty { color: var(--text-secondary); }
.dialog-backdrop { position:fixed; inset:0; z-index:50; display:flex; align-items:center; justify-content:center; background:color-mix(in srgb, var(--text-primary) 32%, transparent); }
.dialog { display:grid; gap:var(--space-3); width:min(480px, calc(100vw - var(--space-7))); padding:var(--space-5); background:var(--bg-surface); border:1px solid var(--border-default); border-radius:var(--radius-sheet); box-shadow:var(--shadow-3); }
.dialog h2 { margin: 0; font-size: var(--text-lg, 16px); }
.dialog p { margin: 0; }
.dialog .warn-line { color: var(--warning-700, #b54708); }
.dialog .acts { display: flex; justify-content: flex-end; gap: var(--space-2); }
</style>
