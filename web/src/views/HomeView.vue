<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ApiError, apiRequest } from "../api/client";
import { getCurrentSession } from "../api/identity";
import { getJob, type JobRecord } from "../api/jobs";
import { getWorkbench, listIssues, type WorkIssue, type WorkJob } from "../api/workbench";
import { listProjects, type ProjectRecord } from "../api/projects";
import { formatUpdatedAt } from "../projects";

const route = useRoute();
const router = useRouter();
const canCreate = ref(false);
const issues = ref<WorkIssue[]>([]);
const issueTotal = ref(0);
const projects = ref<ProjectRecord[]>([]);
const allProjects = ref<ProjectRecord[]>([]);
const jobs = ref<WorkJob[]>([]);
const issuePhase = ref<"loading" | "ready" | "error">("loading");
const boardPhase = ref<"loading" | "ready" | "error">("loading");
const issueError = ref("");
const boardError = ref("");
const draftKind = ref("");
const draftProject = ref("");
const appliedKind = ref("");
const appliedProject = ref("");
const issuePage = ref(1);
const issuePageCount = computed(() => Math.max(1, Math.ceil(issueTotal.value / 10)));
const opening = ref(false);
const openedJob = ref<JobRecord | null>(null);
const jobError = ref("");

const kindLabel: Record<string, string> = {
  missing_material: "缺材料",
  fact_confirmation: "待确认",
  review: "待审核",
  impact: "受影响",
  verification: "待核实",
  export_error: "导出失败",
};
const jobLabel: Record<string, string> = {
  queued: "排队",
  running: "执行中",
  pause_requested: "正在暂停",
  paused: "已暂停",
  waiting_input: "等待输入",
  needs_verification: "待核实",
};

const projectOptions = computed(() => allProjects.value);

function messageOf(error: unknown) {
  return error instanceof ApiError ? error.message : "暂时不能连接服务。";
}

function readIssueQuery() {
  draftKind.value = String(route.query.kind || "");
  appliedKind.value = draftKind.value;
  draftProject.value = String(route.query.project || "");
  appliedProject.value = draftProject.value;
  issuePage.value = Math.max(1, Number(route.query.page || 1) || 1);
}

function issueQuery() {
  const query: Record<string, string> = {};
  if (appliedKind.value) query.kind = appliedKind.value;
  if (appliedProject.value) query.project = appliedProject.value;
  if (issuePage.value > 1) query.page = String(issuePage.value);
  return query;
}

async function loadIssues() {
  issuePhase.value = "loading";
  issueError.value = "";
  try {
    const data = await listIssues({ page: issuePage.value, projectId: appliedProject.value, kind: appliedKind.value });
    issues.value = data.items;
    issueTotal.value = data.total;
    issuePhase.value = "ready";
  } catch (error) {
    issuePhase.value = "error";
    issueError.value = messageOf(error);
  }
}

async function loadBoard() {
  boardPhase.value = "loading";
  boardError.value = "";
  try {
    const session = await getCurrentSession();
    canCreate.value = session.permissions.includes("projects.create");
    const data = await getWorkbench();
    projects.value = data.recentProjects;
    jobs.value = data.activeJobs;
    const listed = await listProjects({ page: 1, pageSize: 50 });
    allProjects.value = listed.items;
    boardPhase.value = "ready";
  } catch (error) {
    boardPhase.value = "error";
    boardError.value = messageOf(error);
  }
}

async function publishIssueQuery() {
  const next = issueQuery();
  const current = route.query;
  const same = String(current.kind || "") === (next.kind || "")
    && String(current.project || "") === (next.project || "")
    && String(current.page || "") === (next.page || "");
  if (same) {
    await loadIssues();
    return;
  }
  await router.replace({ query: next });
}

function search() {
  appliedKind.value = draftKind.value;
  appliedProject.value = draftProject.value;
  issuePage.value = 1;
  void publishIssueQuery();
}

function resetQuery() {
  draftKind.value = "";
  draftProject.value = "";
  appliedKind.value = "";
  appliedProject.value = "";
  issuePage.value = 1;
  void publishIssueQuery();
}

function changeIssuePage(next: number) {
  issuePage.value = next;
  void publishIssueQuery();
}

async function openIssue(issue: WorkIssue) {
  opening.value = true;
  issueError.value = "";
  try {
    if (issue.kind === "review") {
      await router.push({ name: "project-reviews", params: { id: issue.projectId }, query: { review: issue.id } });
      return;
    }
    if (issue.target.kind === "block" && issue.target.id) {
      const body = await apiRequest<{ data: { documentId: string } }>(`/blocks/${issue.target.id}`);
      if (!body?.data.documentId) throw new Error("没有找到对应文档。");
      await router.push({
        name: "document",
        params: { id: issue.projectId, documentId: body.data.documentId },
        query: { block: issue.target.id },
      });
      return;
    }
    if (issue.kind === "fact_confirmation") {
      await router.push({ name: "project-materials", params: { id: issue.projectId } });
      return;
    }
    await router.push({ name: "project", params: { id: issue.projectId } });
  } catch (error) {
    issueError.value = messageOf(error);
  } finally {
    opening.value = false;
  }
}

async function openJob(job: WorkJob) {
  jobError.value = "";
  try {
    openedJob.value = await getJob(job.id);
  } catch (error) {
    openedJob.value = null;
    jobError.value = messageOf(error);
  }
}

watch(() => JSON.stringify(route.query), () => {
  readIssueQuery();
  void loadIssues();
}, { immediate: true });
onMounted(() => {
  void loadBoard();
});
</script>

<template>
  <div class="stack home">
    <section class="card">
      <div class="head">
        <h1>待办</h1>
        <router-link v-if="canCreate" class="primary" :to="{ name: 'projects', query: { create: '1' } }">新建项目</router-link>
      </div>
      <form class="query" @submit.prevent="search">
        <div class="query-fields">
          <label>
            <span>项目</span>
            <select v-model="draftProject" class="control">
              <option value="">全部</option>
              <option v-for="project in projectOptions" :key="project.id" :value="project.id">{{ project.name }}</option>
            </select>
          </label>
          <label>
            <span>类型</span>
            <select v-model="draftKind" class="control">
              <option value="">全部</option>
              <option v-for="(label, key) in kindLabel" :key="key" :value="key">{{ label }}</option>
            </select>
          </label>
        </div>
        <div class="query-actions">
          <button type="submit" class="primary" :disabled="issuePhase === 'loading'">查询</button>
          <button type="button" class="ghost" @click="resetQuery">重置</button>
        </div>
      </form>
      <p v-if="issuePhase === 'loading'" class="empty">正在读取待办</p>
      <div v-else-if="issuePhase === 'error'" class="region-error">
        <p class="notice" role="alert">{{ issueError }}</p>
        <button type="button" class="ghost" @click="loadIssues">重试</button>
      </div>
      <p v-else-if="issues.length === 0" class="empty">暂无待处理事项</p>
      <div v-else class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>事项</th>
              <th>项目</th>
              <th class="from-mid">文档或块</th>
              <th>类型</th>
              <th class="from-wide">更新时间</th>
              <th>处理</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="issue in issues" :key="issue.id">
              <td>{{ issue.title }}</td>
              <td>{{ issue.projectName }}</td>
              <td class="from-mid">{{ issue.target.name }}</td>
              <td>{{ kindLabel[issue.kind] || "待处理" }}</td>
              <td class="from-wide">{{ formatUpdatedAt(issue.updatedAt) }}</td>
              <td><button type="button" class="link" :disabled="opening" @click="openIssue(issue)">处理</button></td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-if="issuePhase === 'ready'" class="pager">
        <span class="total">共 {{ issueTotal }} 条</span>
        <button type="button" class="page" :disabled="issuePage <= 1" @click="changeIssuePage(issuePage - 1)">上一页</button>
        <button type="button" class="page" :disabled="issuePage >= issuePageCount" @click="changeIssuePage(issuePage + 1)">下一页</button>
      </div>
    </section>
    <div class="side">
      <section class="card">
        <div class="head"><h1>最近项目</h1></div>
        <p v-if="boardPhase === 'loading'" class="empty">正在读取项目</p>
        <div v-else-if="boardPhase === 'error'" class="region-error">
          <p class="notice" role="alert">{{ boardError }}</p>
          <button type="button" class="ghost" @click="loadBoard">重试</button>
        </div>
        <p v-else-if="projects.length === 0" class="empty">还没有项目</p>
        <ul v-else class="recent">
          <li v-for="project in projects" :key="project.id">
            <router-link class="link" :to="{ name: 'project', params: { id: project.id } }">{{ project.name }}</router-link>
            <span>{{ formatUpdatedAt(project.updatedAt) }}</span>
          </li>
        </ul>
      </section>
      <section class="card">
        <div class="head"><h1>运行任务</h1></div>
        <p v-if="boardPhase === 'loading'" class="empty">正在读取任务</p>
        <div v-else-if="boardPhase === 'error'" class="region-error">
          <p class="notice" role="alert">{{ boardError }}</p>
          <button type="button" class="ghost" @click="loadBoard">重试</button>
        </div>
        <p v-else-if="jobs.length === 0" class="empty">没有运行中的任务</p>
        <ul v-else class="recent">
          <li v-for="job in jobs" :key="job.id">
            <button type="button" class="link" @click="openJob(job)">{{ job.target.name }}</button>
            <span>{{ jobLabel[job.status] || "进行中" }}</span>
          </li>
        </ul>
        <p v-if="jobError" class="notice" role="alert">{{ jobError }}</p>
        <p v-else-if="openedJob" class="job-detail">
          {{ openedJob.target?.name || "任务" }} · {{ jobLabel[openedJob.status] || openedJob.status }}
          <span v-if="openedJob.stageName"> · {{ openedJob.stageName }}</span>
          <span v-if="openedJob.blockingReason"> · {{ openedJob.blockingReason }}</span>
        </p>
      </section>
    </div>
  </div>
</template>

<style scoped>
.home { display: grid; gap: var(--space-4); }
.side { display: grid; gap: var(--space-4); }
.region-error { padding: var(--space-3) var(--space-4); }
.query-fields label:last-child { flex: none; }
.query-fields label:last-child .control { width: calc(var(--space-7) * 4); }
.recent { margin: 0; padding: 0; list-style: none; }
.job-detail { margin: 0; padding: var(--space-3) var(--space-4); color: var(--ink-secondary); font-size: var(--text-sm); }
.recent li {
  display: flex;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border-top: 1px solid var(--rule);
  font-size: var(--text-sm);
}
button.link { border: 0; background: transparent; padding: 0; cursor: pointer; font-size: var(--text-sm); }
.table-wrap :deep(table) { width: 100%; }
.from-wide { display: none; }
@media (max-width: 767px) {
  .from-mid { display: none; }
}
@media (min-width: 768px) {
  .from-mid { display: table-cell; }
  .side { grid-template-columns: 1fr 1fr; }
}
@media (min-width: 1280px) {
  .home { grid-template-columns: 2fr 1fr; align-items: start; }
  .side { grid-template-columns: 1fr; }
  .from-wide { display: table-cell; }
}
</style>
