<script setup lang="ts">
/** A07 编写分工面板（16 §A07、U-9..U-16）：分工模式、整篇负责人、按章节分配。负责人和管理员可改，其他人只看。 */
import { computed, onMounted, ref } from "vue";
import { DOCUMENT_TARGET, getAssignments, putAssignmentMode, putAssignments, type AssignmentView } from "../api/assignments";
import { listMembers } from "../api/projects";
import { ApiError } from "../api/client";

const props = defineProps<{ documentId: string; projectId: string; objects: Array<{ id: string; kind: string; markdown: string }> }>();
const emit = defineEmits<{ close: []; changed: [] }>();

const view = ref<AssignmentView | null>(null);
const people = ref<Array<{ id: string; name: string; role: string }>>([]);
const error = ref("");
const busy = ref(false);
const selected = ref<string[]>([]);
const batchAssignee = ref("");

const headingText = (markdown: string) => markdown.replace(/^ {0,3}#{1,6}[ \t]*/, "").replace(/[ \t]+#+[ \t]*$/, "").trim();
const levelOf = (markdown: string) => (/^ {0,3}(#{1,6})/.exec(markdown)?.[1].length ?? 1);
const explicitOf = (objectId: string) => view.value?.explicit.find(item => item.objectId === objectId) ?? null;
const resolvedOf = (objectId: string) => view.value?.resolved?.find(item => item.objectId === objectId) ?? null;
const headings = computed(() => props.objects.filter(item => item.kind === "heading").map(item => {
  const explicit = explicitOf(item.id);
  const resolved = resolvedOf(item.id);
  return { id: item.id, text: headingText(item.markdown) || "（空标题）", level: levelOf(item.markdown),
    explicit: explicit?.assignees.map(person => person.name).join("、") || "",
    explicitId: explicit?.assignees[0]?.id || "",
    inherited: !explicit && resolved?.owners.length ? resolved.owners.map(person => person.name).join("、") : "",
    needsConfirm: Boolean(explicit?.needsConfirm) };
}));
const wholeOwner = computed(() => explicitOf(DOCUMENT_TARGET)?.assignees[0]?.id || "");
const eligible = computed(() => people.value.filter(person => person.role === "owner" || person.role === "editor"));
const canAssign = computed(() => Boolean(view.value?.canAssign));

function failText(caught: unknown) {
  if (caught instanceof ApiError) {
    if (caught.code === "REVISION_CONFLICT") return "分工刚被其他人调整过，已显示最新，请重新操作。";
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
  try { await action(); emit("changed"); } catch (caught) { error.value = failText(caught); }
  busy.value = false;
  await load();
}
const assign = (objectIds: string[], assignee: string) =>
  run(() => putAssignments(props.documentId, objectIds, assignee ? [assignee] : [], view.value!.assignmentsRevision));
const toggleMode = () =>
  run(() => putAssignmentMode(props.documentId, view.value!.assignmentMode === "enforced" ? "open" : "enforced", view.value!.assignmentsRevision));
async function applyBatch() {
  if (!selected.value.length) return;
  await assign([...selected.value], batchAssignee.value);
  selected.value = [];
}
onMounted(load);
defineExpose({ load });
</script>

<template>
  <aside class="assign-panel" data-testid="assign-panel" aria-label="编写分工">
    <header class="assign-head">
      <h2>编写分工</h2>
      <button type="button" class="subtle icon-btn" aria-label="关闭编写分工" @click="emit('close')">×</button>
    </header>
    <p v-if="error" class="assign-error" role="alert" data-testid="assign-error">{{ error }}</p>
    <template v-if="view">
      <section class="assign-mode">
        <p data-testid="assign-mode-text">{{ view.assignmentMode === 'enforced' ? '按分工编辑：编写人员只能修改分给自己的章节，未分配的章节只有负责人能改。' : '不限制分工：所有编写人员都能修改全文。' }}</p>
        <button v-if="canAssign" type="button" class="ghost sm mode-btn" data-testid="assign-mode-toggle" :disabled="busy" @click="toggleMode">{{ view.assignmentMode === 'enforced' ? '改为不限制' : '改为按分工编辑' }}</button>
      </section>
      <p class="assign-summary" data-testid="assign-summary">未分配章节 {{ view.summary.unassignedSections }} 个<template v-if="view.summary.needsConfirm"> · {{ view.summary.needsConfirm }} 处待确认</template></p>
      <label class="assign-row whole">
        <span>整篇负责人</span>
        <select data-testid="assign-doc-owner" :disabled="!canAssign || busy" :value="wholeOwner" @change="assign([DOCUMENT_TARGET], ($event.target as HTMLSelectElement).value)">
          <option value="">未指定</option>
          <option v-for="person in eligible" :key="person.id" :value="person.id">{{ person.name }}</option>
        </select>
      </label>
      <ul class="assign-list" data-testid="assign-list">
        <li v-for="item in headings" :key="item.id" class="assign-row" :style="{ paddingLeft: `${(item.level - 1) * 14}px` }" :data-heading-id="item.id" data-testid="assign-heading">
          <input v-if="canAssign" v-model="selected" type="checkbox" :value="item.id" :aria-label="`选择 ${item.text}`" />
          <span class="assign-title" :title="item.text">{{ item.text }}</span>
          <span v-if="item.needsConfirm" class="tag warn">待确认</span>
          <select v-if="canAssign" :disabled="busy" :value="item.explicitId" :aria-label="`${item.text} 的编写人`" data-testid="assign-select" @change="assign([item.id], ($event.target as HTMLSelectElement).value)">
            <option value="">{{ item.inherited ? `随上级（${item.inherited}）` : '未分配' }}</option>
            <option v-for="person in eligible" :key="person.id" :value="person.id">{{ person.name }}</option>
          </select>
          <span v-else class="assign-who" data-testid="assign-who">{{ item.explicit || (item.inherited ? `随上级（${item.inherited}）` : '未分配') }}</span>
        </li>
        <li v-if="!headings.length" class="assign-empty">文档还没有标题。可以指定整篇负责人。</li>
      </ul>
      <footer v-if="canAssign && selected.length" class="assign-batch" data-testid="assign-batch">
        <span>已选 {{ selected.length }} 个章节</span>
        <select v-model="batchAssignee" aria-label="批量分配给"><option value="">取消分配</option><option v-for="person in eligible" :key="person.id" :value="person.id">{{ person.name }}</option></select>
        <button type="button" class="primary sm" :disabled="busy" data-testid="assign-batch-apply" @click="applyBatch">应用</button>
      </footer>
    </template>
    <p v-else-if="!error" class="assign-empty" role="status">正在读取分工…</p>
  </aside>
</template>

<style scoped>
.assign-panel { position: fixed; top: 96px; right: 0; bottom: 28px; z-index: 30; width: 360px; max-width: 90vw; box-shadow: -6px 0 18px rgba(15, 23, 42, .12); border-left: 1px solid var(--border, #e5e7eb); background: var(--bg-surface, #fff); display: flex; flex-direction: column; overflow: auto; padding: 12px 14px; gap: 10px; }
.assign-head { display: flex; align-items: center; justify-content: space-between; }
.assign-head h2 { font-size: 15px; margin: 0; }
.mode-btn { align-self: flex-start; }
.assign-mode { display: flex; flex-direction: column; gap: 6px; font-size: 13px; }
.assign-mode p, .assign-summary { margin: 0; color: var(--text-secondary, #555); font-size: 13px; }
.assign-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
.assign-row { display: flex; align-items: center; gap: 6px; font-size: 13px; min-height: 30px; }
.assign-row.whole { justify-content: space-between; }
.assign-title { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.assign-row select { max-width: 150px; }
.assign-who { color: var(--text-secondary, #555); max-width: 150px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.assign-error { color: var(--danger, #b42318); font-size: 13px; margin: 0; }
.assign-batch { position: sticky; bottom: 0; background: inherit; display: flex; gap: 6px; align-items: center; font-size: 13px; padding-top: 6px; border-top: 1px solid var(--border, #e5e7eb); }
.assign-empty { color: var(--text-secondary, #555); font-size: 13px; }
</style>
