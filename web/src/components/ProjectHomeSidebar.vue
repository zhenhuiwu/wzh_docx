<script setup lang="ts">
/** 项目首页右栏（30 §3.1）：当前基线、我的待办、成员。各区块自己加载，失败互不影响。 */
import { computed, ref, watch } from "vue";
import { listMyWriting, listProjectBaselines, type MyWritingItem, type ProjectBaseline } from "../api/baselines";
import { listMembers, type ProjectMember } from "../api/projects";
import { listIssues, type WorkIssue } from "../api/workbench";
import { formatDateTime, initialOf } from "../format";
import { ROLE_LABEL } from "../memberModel";
import StatePanel from "./StatePanel.vue";

const props = defineProps<{ projectId: string }>();
const emit = defineEmits<{ (event: "manage-members"): void }>();
const MEMBER_LIMIT = 8;

type Phase = "loading" | "ready" | "error";
const baseline = ref<ProjectBaseline | null>(null);
const baselinePhase = ref<Phase>("loading");
const writing = ref<MyWritingItem[]>([]);
const issues = ref<WorkIssue[]>([]);
const todoPhase = ref<Phase>("loading");
const members = ref<ProjectMember[]>([]);
const memberPhase = ref<Phase>("loading");

async function loadBaseline() {
  baselinePhase.value = "loading";
  try { baseline.value = (await listProjectBaselines(props.projectId, 0, 1)).items[0] ?? null; baselinePhase.value = "ready"; }
  catch { baselinePhase.value = "error"; }
}
async function loadTodos() {
  todoPhase.value = "loading";
  try {
    const [mine, open] = await Promise.all([listMyWriting(0, 100), listIssues({ projectId: props.projectId })]);
    writing.value = mine.items.filter((item) => item.project.id === props.projectId && item.status !== "completed");
    issues.value = open.items.filter((item) => item.status === "open");
    todoPhase.value = "ready";
  } catch { todoPhase.value = "error"; }
}
async function loadMembers() {
  memberPhase.value = "loading";
  try { members.value = (await listMembers(props.projectId)).items; memberPhase.value = "ready"; }
  catch { memberPhase.value = "error"; }
}
function reload() { void loadBaseline(); void loadTodos(); void loadMembers(); }
watch(() => props.projectId, reload, { immediate: true });
defineExpose({ reload, reloadMembers: loadMembers });

const shownMembers = computed(() => members.value.slice(0, MEMBER_LIMIT));
const todoCount = computed(() => writing.value.length + issues.value.length);
</script>

<template>
  <aside class="home-side" aria-label="项目概况">
    <section class="side-block" aria-labelledby="side-baseline">
      <div class="side-head"><h2 id="side-baseline" class="section-title">当前基线</h2>
        <router-link class="text" :to="{ name: 'project-baselines', params: { id: projectId } }">全部基线</router-link></div>
      <StatePanel v-if="baselinePhase === 'loading'" kind="loading" text="正在读取基线…" class="compact" />
      <StatePanel v-else-if="baselinePhase === 'error'" kind="error" title="基线加载失败" class="compact"><button type="button" class="ghost" @click="loadBaseline">重试</button></StatePanel>
      <p v-else-if="!baseline" class="faint" data-testid="side-no-baseline">还没有基线。文档评审通过后，在“基线”页建立。</p>
      <div v-else class="baseline-line" data-testid="side-baseline">
        <span class="tag plain">{{ baseline.label }}</span>
        <span class="baseline-name">{{ baseline.name }}</span>
        <span class="faint num">{{ formatDateTime(baseline.createdAt) }} · {{ baseline.documentCount }} 份文档</span>
      </div>
    </section>

    <section class="side-block" aria-labelledby="side-todo">
      <div class="side-head"><h2 id="side-todo" class="section-title">我的待办<span v-if="todoCount" class="count num">{{ todoCount }}</span></h2></div>
      <StatePanel v-if="todoPhase === 'loading'" kind="loading" text="正在读取待办…" class="compact" />
      <StatePanel v-else-if="todoPhase === 'error'" kind="error" title="待办加载失败" class="compact"><button type="button" class="ghost" @click="loadTodos">重试</button></StatePanel>
      <p v-else-if="!todoCount" class="faint" data-testid="side-no-todo">没有待办。</p>
      <ul v-else class="side-list" data-testid="side-todos">
        <li v-for="item in writing" :key="item.id">
          <router-link class="text ellipsis" :to="{ name: 'document', params: { id: projectId, documentId: item.id } }">{{ item.myRole === "owner" ? "编写" : "完成分配给我的部分" }}：{{ item.name }}</router-link>
        </li>
        <li v-for="item in issues" :key="item.id"><span class="ellipsis">{{ item.title }}</span></li>
      </ul>
    </section>

    <section class="side-block" aria-labelledby="side-members">
      <div class="side-head"><h2 id="side-members" class="section-title">成员<span v-if="members.length" class="count num">{{ members.length }}</span></h2>
        <button type="button" class="text" data-testid="side-manage-members" @click="emit('manage-members')">成员与权限</button></div>
      <StatePanel v-if="memberPhase === 'loading'" kind="loading" text="正在读取成员…" class="compact" />
      <StatePanel v-else-if="memberPhase === 'error'" kind="error" title="成员加载失败" class="compact"><button type="button" class="ghost" @click="loadMembers">重试</button></StatePanel>
      <ul v-else class="side-list members" data-testid="side-members">
        <li v-for="m in shownMembers" :key="m.userId">
          <span class="avatar" aria-hidden="true">{{ initialOf(m.name) }}</span>
          <span class="ellipsis">{{ m.name }}<span v-if="m.isMe" class="faint">（我）</span></span>
          <span class="faint role">{{ ROLE_LABEL[m.role] ?? m.role }}</span>
        </li>
        <li v-if="members.length > MEMBER_LIMIT" class="faint">还有 {{ members.length - MEMBER_LIMIT }} 人</li>
      </ul>
    </section>
  </aside>
</template>

<style scoped>
.home-side { display:grid; align-content:start; gap:var(--space-4); min-width:0; }
.side-block { display:grid; gap:var(--space-2); padding:var(--space-4); border:var(--border); border-radius:var(--radius-sheet); background:var(--bg-subtle); }
.side-head { display:flex; align-items:center; justify-content:space-between; gap:var(--space-2); }
.side-head .section-title { margin:0; display:flex; align-items:center; gap:var(--space-2); }
.count { color:var(--text-secondary); font-size:var(--text-xs); font-weight:400; }
.baseline-line { display:flex; flex-wrap:wrap; align-items:center; gap:var(--space-2); }
.baseline-name { color:var(--text-primary); }
.baseline-line .faint { flex-basis:100%; }
.faint { margin:0; color:var(--text-secondary); font-size:var(--text-sm); }
.side-list { display:grid; gap:var(--space-2); margin:0; padding:0; list-style:none; font-size:var(--text-sm); }
.side-list li { display:flex; align-items:center; gap:var(--space-2); min-width:0; }
.ellipsis { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.members .role { margin-left:auto; flex-shrink:0; font-size:var(--text-xs); }
</style>
