<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from "vue-router";
import { ApiError } from "../api/client";
import { uploadFile } from "../api/files";
import { listProjects, type ProjectRecord } from "../api/projects";
import { documentHeadings, getDocument, listDocuments, type DocumentRecord } from "../api/documents";
import { analyzeTestTask, createTestTask, downloadTestReport, getTestTask, insertTestResults, listTestTasks, runTestTask, setTestCaseEnabled, stopTestTask, testEvidenceUrl, updateTestTask, type TestCase, type TestDocumentInsertion, type TestRun, type TestTask, type TestTaskInput } from "../api/testTasks";

const route = useRoute();
const router = useRouter();
const projects = ref<ProjectRecord[]>([]);
const projectsError = ref("");
const projectsLoading = ref(true);
const tasks = ref<TestTask[]>([]);
const task = ref<TestTask | null>(null);
const phase = ref<"loading" | "ready" | "error">("loading");
const detailPhase = ref<"loading" | "ready" | "error">("loading");
const errorText = ref("");
const actionError = ref("");
const detailError = ref("");
const draft = ref({ name: "", projectId: "" });
const applied = ref({ name: "", projectId: "" });
const page = ref(1);
const pageSize = ref(10);
const total = ref(0);
const targetPage = ref(1);
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)));
const taskId = computed(() => typeof route.query.task === "string" ? route.query.task : "");
const tab = ref<"cases" | "results">("cases");
const runId = ref("");
const resultCaseId = ref("");
const operation = ref("");
const downloading = ref(false);
const running = computed(() => ["queued", "running", "stopping", "stop_requested"].includes(task.value?.status ?? "") || task.value?.runs?.some(run => ["queued", "running", "stopping"].includes(run.status)));
const analyzing = computed(() => operation.value === "analyze" || task.value?.status === "analyzing");
const locked = computed(() => Boolean(operation.value) || running.value || analyzing.value);
const currentRun = computed(() => task.value?.runs?.find(run => run.id === runId.value) ?? task.value?.runs?.[0]);
const activeRun = computed(() => task.value?.runs?.find(run => ["queued", "running", "stopping"].includes(run.status)));
const activeResults = computed(() => activeRun.value?.result?.cases ?? []);
const results = computed(() => currentRun.value?.result?.cases ?? []);
const currentResult = computed(() => results.value.find(item => item.caseId === resultCaseId.value) ?? results.value[0]);
const enabledCount = computed(() => task.value?.cases?.filter(item => item.enabled && !missingInput(item)).length ?? 0);
const finishedCount = computed(() => activeResults.value.filter(item => ["passed", "failed", "blocked", "stopped", "skipped"].includes(item.status)).length);
let listController: AbortController | undefined;
let detailController: AbortController | undefined;
let analyzeController: AbortController | undefined;
let pollTimer: ReturnType<typeof window.setTimeout> | undefined;
let disposed = false;

const creating = ref(false);
const editingId = ref("");
const editingRevision = ref(0);
const createDialog = ref<HTMLDialogElement | null>(null);
const nameInput = ref<HTMLInputElement | null>(null);
const submitting = ref(false);
const formError = ref("");
const planFile = ref<File | null>(null);
const reportFile = ref<File | null>(null);
const form = ref<TestTaskInput>(emptyForm());
const previewDialog = ref<HTMLDialogElement | null>(null);
const previewUrl = ref("");
const previewName = ref("");
const resultDetail = ref<HTMLElement | null>(null);
const writeDialog = ref<HTMLDialogElement | null>(null);
const writeOpen = ref(false);
const writeRun = ref<TestRun | null>(null);
const writePhase = ref<"loading" | "ready" | "error">("loading");
const writeDocuments = ref<DocumentRecord[]>([]);
const writeDocumentId = ref("");
const writeDocument = ref<DocumentRecord | null>(null);
const writeDocumentLoading = ref(false);
const writeHeading = ref("pending");
const writeCaseIds = ref<string[]>([]);
const writeError = ref("");
const writing = ref(false);
const writeSaved = ref<TestDocumentInsertion | null>(null);
let writeRequest = 0;
const writeHeadings = computed(() => documentHeadings(writeDocument.value?.markdownContent ?? "").map((item, index) => ({ index, text: "　".repeat(item.level - 1) + item.text })));
const reportReady = computed(() => Boolean(currentRun.value && results.value.length && !["running", "queued", "stopping"].includes(currentRun.value.status)));

function emptyForm(): TestTaskInput {
  return { name: "", projectId: "", planFileId: "", frontendRoot: "", backendRoot: "", frontendUrl: "", backendUrl: "", executionMode: "agent", testUsername: "", testPassword: "", dataScope: "", concurrency: 5, requests: 20 };
}

function messageOf(error: unknown) {
  return error instanceof ApiError || error instanceof Error ? error.message : "暂时不能连接服务。";
}

function statusLabel(status?: string) {
  return ({ new: "待提取", draft: "待提取", created: "待提取", analyzing: "正在提取", ready: "待执行", analyzed: "待执行", queued: "等待执行", running: "正在测试", stopping: "正在停止", stop_requested: "正在停止", completed: "已完成", passed: "通过", failed: "失败", blocked: "缺少条件", stopped: "已停止", skipped: "未执行" } as Record<string, string>)[status ?? ""] ?? status ?? "未执行";
}

function kindLabel(kind: string) {
  return ({ functional: "功能测试", unit: "单元测试", concurrency: "并发测试" } as Record<string, string>)[kind] ?? kind;
}

function dateLabel(value?: string) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function missingInput(item: TestCase) {
  return Array.isArray(item.needsInput) ? item.needsInput.join("；") : item.needsInput || "";
}

function stepLabel(step: TestCase["steps"][number]) {
  return typeof step === "string" ? step : String(step.description || step.action || step.expected || "");
}

function stepDetails(step: TestCase["steps"][number]) {
  if (typeof step === "string") return "";
  const { testCode: _code, ...details } = step;
  return JSON.stringify(details, null, 2);
}

function stepCode(step: TestCase["steps"][number]) {
  return typeof step === "string" ? "" : typeof step.testCode === "string" ? step.testCode : "";
}

function metricEntries(metrics: Record<string, unknown>) {
  const keys = ["requested", "completed", "concurrency", "failed", "averageMs", "p95Ms", "maxLatencyMs", "minMs", "maxMs", "successRate", "exitCode"];
  return keys.filter(key => key in metrics).map(key => ({ key, value: metrics[key] == null ? "—" : String(metrics[key]) }));
}

function metricLabel(key: string) {
  return ({ requested: "计划请求数", completed: "完成请求数", concurrency: "并发数", failed: "失败请求数", successRate: "成功率", averageMs: "平均耗时（毫秒）", p95Ms: "P95 耗时（毫秒）", maxLatencyMs: "最大允许耗时（毫秒）", minMs: "最短耗时（毫秒）", maxMs: "最长耗时（毫秒）", exitCode: "退出码" } as Record<string, string>)[key] ?? key;
}

async function loadProjects() {
  projectsLoading.value = true;
  projectsError.value = "";
  try {
    const items: ProjectRecord[] = [];
    let currentPage = 1;
    while (!disposed) {
      const result = await listProjects({ page: currentPage, pageSize: 50 });
      items.push(...result.items);
      if (items.length >= result.total || !result.items.length) break;
      currentPage += 1;
    }
    if (!disposed) projects.value = items;
  } catch (error) {
    if (!disposed) projectsError.value = messageOf(error);
  } finally {
    if (!disposed) projectsLoading.value = false;
  }
}

async function loadList() {
  listController?.abort();
  const controller = new AbortController();
  listController = controller;
  phase.value = "loading";
  errorText.value = "";
  try {
    const result = await listTestTasks({ page: page.value, pageSize: pageSize.value, ...applied.value }, controller.signal);
    if (controller.signal.aborted || disposed) return;
    tasks.value = result.items;
    total.value = result.total;
    page.value = result.page;
    targetPage.value = result.page;
    phase.value = "ready";
  } catch (error) {
    if (controller.signal.aborted || disposed) return;
    phase.value = "error";
    errorText.value = messageOf(error);
  }
}

function search() {
  applied.value = { name: draft.value.name.trim(), projectId: draft.value.projectId };
  page.value = 1;
  void loadList();
}

function changePage(next: number) {
  if (phase.value === "loading" || !Number.isInteger(next)) return;
  page.value = Math.min(pageCount.value, Math.max(1, next));
  void loadList();
}

function stopPolling() {
  if (pollTimer !== undefined) window.clearTimeout(pollTimer);
  pollTimer = undefined;
}

function schedulePoll() {
  stopPolling();
  if (!disposed && taskId.value && (running.value || analyzing.value)) pollTimer = window.setTimeout(() => void loadDetail(true), 2000);
}

function acceptTask(value: TestTask) {
  task.value = value;
  if (!value.runs?.some(run => run.id === runId.value)) runId.value = value.runs?.[0]?.id ?? "";
  detailPhase.value = "ready";
  schedulePoll();
}

async function loadDetail(background = false) {
  if (!taskId.value) return;
  detailController?.abort();
  const controller = new AbortController();
  detailController = controller;
  const id = taskId.value;
  if (!background) detailPhase.value = "loading";
  detailError.value = "";
  try {
    const value = await getTestTask(id, controller.signal);
    if (controller.signal.aborted || disposed || id !== taskId.value) return;
    acceptTask(value);
  } catch (error) {
    if (controller.signal.aborted || disposed || id !== taskId.value) return;
    detailError.value = messageOf(error);
    if (!background) detailPhase.value = "error";
    schedulePoll();
  }
}

async function openTask(id: string) {
  await router.replace({ query: { ...route.query, task: id } });
}

async function backToList() {
  const query = { ...route.query };
  delete query.task;
  await router.replace({ query });
  await loadList();
}

async function openCreate() {
  editingId.value = "";
  form.value = { ...emptyForm(), projectId: applied.value.projectId || projects.value[0]?.id || "" };
  planFile.value = null;
  reportFile.value = null;
  formError.value = "";
  creating.value = true;
  await nextTick();
  createDialog.value?.showModal();
  nameInput.value?.focus();
}

async function openEdit() {
  if (!task.value || locked.value) return;
  editingId.value = task.value.id;
  editingRevision.value = task.value.revision;
  form.value = { ...emptyForm(), ...task.value, testPassword: "" };
  planFile.value = null;
  reportFile.value = null;
  formError.value = "";
  creating.value = true;
  await nextTick();
  createDialog.value?.showModal();
  nameInput.value?.focus();
}

function closeCreate() {
  if (!submitting.value) createDialog.value?.close();
}

async function submitCreate() {
  formError.value = "";
  if (!form.value.name.trim()) { formError.value = "请填写任务名称。"; nameInput.value?.focus(); return; }
  if (!form.value.projectId) { formError.value = "请选择所属项目。"; return; }
  if ((!editingId.value && !planFile.value) || (planFile.value && !/\.docx$/i.test(planFile.value.name))) { formError.value = "请选择 .docx 格式的测试计划。"; return; }
  if (reportFile.value && !/\.docx$/i.test(reportFile.value.name)) { formError.value = "待补充的测试文档必须是 .docx 文件。"; return; }
  for (const value of [form.value.frontendUrl, form.value.backendUrl]) {
    if (!value.trim()) continue;
    try { if (!["http:", "https:"].includes(new URL(value.trim()).protocol)) throw new Error(); }
    catch { formError.value = "服务地址应以 http:// 或 https:// 开头。"; return; }
  }
  if (!Number.isInteger(form.value.concurrency) || form.value.concurrency < 1 || form.value.concurrency > 20 || !Number.isInteger(form.value.requests) || form.value.requests < 1 || form.value.requests > 200) { formError.value = "并发数应为 1–20，请求总数应为 1–200。"; return; }
  submitting.value = true;
  try {
    const editId = editingId.value;
    const projectId = form.value.projectId;
    const plan = planFile.value ? await uploadFile(planFile.value, "attachment", { projectId, idempotencyKey: crypto.randomUUID() }) : undefined;
    const report = reportFile.value ? await uploadFile(reportFile.value, "attachment", { projectId, idempotencyKey: crypto.randomUUID() }) : undefined;
    const input = { name: form.value.name.trim(), frontendRoot: form.value.frontendRoot.trim(), backendRoot: form.value.backendRoot.trim(), frontendUrl: form.value.frontendUrl.trim(), backendUrl: form.value.backendUrl.trim(), executionMode: form.value.executionMode, testUsername: form.value.testUsername.trim(), dataScope: form.value.dataScope.trim(), concurrency: form.value.concurrency, requests: form.value.requests };
    const created = editId
      ? await updateTestTask(editId, { ...input, expectedRevision: editingRevision.value, ...(plan ? { planFileId: plan.id } : {}), ...(report ? { reportFileId: report.id } : {}), ...(form.value.testPassword ? { testPassword: form.value.testPassword } : {}) })
      : await createTestTask({ ...input, projectId, planFileId: plan!.id, reportFileId: report?.id, testPassword: form.value.testPassword });
    if (disposed) return;
    form.value.testPassword = "";
    createDialog.value?.close();
    if (editId && editId === taskId.value) { acceptTask(created); tab.value = "cases"; }
    else await openTask(created.id);
  } catch (error) {
    formError.value = messageOf(error);
  } finally {
    submitting.value = false;
  }
}

async function analyze() {
  if (!task.value || locked.value) return;
  const id = task.value.id;
  actionError.value = "";
  operation.value = "analyze";
  analyzeController = new AbortController();
  schedulePoll();
  try {
    const value = await analyzeTestTask(id, analyzeController.signal);
    if (!disposed && id === taskId.value) { acceptTask(value); tab.value = "cases"; }
  } catch (error) {
    if (!disposed && id === taskId.value) actionError.value = messageOf(error);
  } finally {
    operation.value = "";
    if (!disposed && id === taskId.value) await loadDetail(true);
  }
}

async function toggleCase(item: TestCase, event: Event) {
  if (!task.value || locked.value) return;
  const enabled = (event.target as HTMLInputElement).checked;
  const id = task.value.id;
  operation.value = "case";
  actionError.value = "";
  try { const value = await setTestCaseEnabled(id, item.id, enabled); if (!disposed && id === taskId.value) acceptTask(value); }
  catch (error) { if (!disposed && id === taskId.value) { actionError.value = messageOf(error); (event.target as HTMLInputElement).checked = item.enabled; } }
  finally { if (id === taskId.value) operation.value = ""; }
}

async function run(caseIds?: string[]) {
  if (!task.value || locked.value || !enabledCount.value) return;
  const id = task.value.id;
  operation.value = "run";
  actionError.value = "";
  try {
    const value = await runTestTask(id, task.value.revision, caseIds);
    if (disposed || id !== taskId.value) return;
    runId.value = "";
    resultCaseId.value = caseIds?.[0] ?? "";
    tab.value = "results";
    acceptTask(value);
  } catch (error) { if (!disposed && id === taskId.value) { actionError.value = messageOf(error); await loadDetail(true); } }
  finally { if (id === taskId.value) { operation.value = ""; schedulePoll(); } }
}

async function stop() {
  if (!task.value || operation.value === "stop") return;
  const id = task.value.id;
  operation.value = "stop";
  actionError.value = "";
  try { const value = await stopTestTask(id); if (!disposed && id === taskId.value) acceptTask(value); }
  catch (error) { if (!disposed && id === taskId.value) actionError.value = messageOf(error); }
  finally { if (id === taskId.value) { operation.value = ""; schedulePoll(); } }
}

async function downloadReport() {
  if (!task.value || !currentRun.value || downloading.value) return;
  downloading.value = true;
  actionError.value = "";
  try {
    const url = await downloadTestReport(task.value.id, currentRun.value.id);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${task.value.name}-测试报告.docx`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } catch (error) { actionError.value = messageOf(error); }
  finally { downloading.value = false; }
}

async function loadWriteDocuments() {
  if (!task.value) return;
  const request = ++writeRequest;
  const projectId = task.value.projectId;
  writePhase.value = "loading";
  writeError.value = "";
  try {
    const items: DocumentRecord[] = [];
    let requestedPage = 1;
    while (writeOpen.value && !disposed) {
      const response = await listDocuments(projectId, requestedPage, "", 100);
      if (request !== writeRequest || !writeOpen.value || disposed) return;
      items.push(...response.items);
      if (items.length >= response.total || !response.items.length) break;
      requestedPage += 1;
    }
    writeDocuments.value = items;
    writePhase.value = "ready";
  } catch (error) {
    if (request === writeRequest && writeOpen.value && !disposed) { writePhase.value = "error"; writeError.value = messageOf(error); }
  }
}

async function openWrite() {
  if (!reportReady.value || writing.value || !currentRun.value) return;
  writeRun.value = currentRun.value;
  writeDocumentId.value = "";
  writeDocument.value = null;
  writeHeading.value = "pending";
  writeDocumentLoading.value = false;
  writeCaseIds.value = currentRun.value.result.cases.map(item => item.caseId);
  writeSaved.value = null;
  writeError.value = "";
  writeOpen.value = true;
  await nextTick();
  writeDialog.value?.showModal();
  void loadWriteDocuments();
}

function closeWrite() {
  if (writing.value) return;
  ++writeRequest;
  writeDialog.value?.close();
  writeOpen.value = false;
}

async function loadWriteDocument() {
  const request = ++writeRequest;
  const id = writeDocumentId.value;
  writeDocument.value = null;
  writeHeading.value = "pending";
  writeError.value = "";
  if (!id) { writeDocumentLoading.value = false; return; }
  writeDocumentLoading.value = true;
  try {
    const value = await getDocument(id);
    if (request === writeRequest && writeOpen.value && !disposed) writeDocument.value = value;
  } catch (error) {
    if (request === writeRequest && writeOpen.value && !disposed) writeError.value = messageOf(error);
  } finally {
    if (request === writeRequest) writeDocumentLoading.value = false;
  }
}

async function writeResults() {
  if (writing.value || !task.value || !writeRun.value) return;
  writeError.value = "";
  if (!writeDocument.value) { writeError.value = "请选择要补充的文档。"; return; }
  if (writeHeading.value === "pending") { writeError.value = "请选择写入位置。"; return; }
  if (!writeCaseIds.value.length) { writeError.value = "请选择要补充的测试项。"; return; }
  const target = writeDocument.value;
  const id = task.value.id;
  writing.value = true;
  try {
    const value = await insertTestResults(id, { runId: writeRun.value.id, documentId: target.id, expectedRevision: target.revision, headingIndex: writeHeading.value === "" ? null : Number(writeHeading.value), caseIds: [...writeCaseIds.value] });
    if (!disposed && taskId.value === id) writeSaved.value = value;
  } catch (error) {
    if (!disposed && taskId.value === id) {
      writeError.value = messageOf(error);
      if (error instanceof ApiError && error.code === "REVISION_CONFLICT") writeDocument.value = null;
    }
  } finally { writing.value = false; }
}

function canLeaveWriting() {
  if (!writing.value) return true;
  writeError.value = "正在补充文档，请稍候。";
  return false;
}
onBeforeRouteLeave(canLeaveWriting);
onBeforeRouteUpdate(canLeaveWriting);

async function preview(fileName: string, description: string) {
  if (!task.value || !currentRun.value) return;
  previewUrl.value = testEvidenceUrl(task.value.id, currentRun.value.id, fileName);
  previewName.value = description;
  await nextTick();
  previewDialog.value?.showModal();
}

watch(taskId, () => {
  stopPolling();
  detailController?.abort();
  analyzeController?.abort();
  task.value = null;
  actionError.value = "";
  operation.value = "";
  runId.value = "";
  resultCaseId.value = "";
  tab.value = "cases";
  if (taskId.value) void loadDetail();
});
watch(() => currentResult.value?.steps?.length, async () => {
  const panel = resultDetail.value;
  if (!panel || currentRun.value?.id !== activeRun.value?.id || panel.scrollHeight - panel.scrollTop - panel.clientHeight > panel.clientHeight / 4) return;
  await nextTick();
  panel.scrollTop = panel.scrollHeight;
});
onMounted(() => { void loadProjects(); void loadList(); if (taskId.value) void loadDetail(); });
onUnmounted(() => { disposed = true; stopPolling(); listController?.abort(); detailController?.abort(); analyzeController?.abort(); });
</script>

<template>
  <section class="test-page">
    <header class="page-head">
      <h1>{{ taskId ? task?.name || "测试任务" : "测试用例管理" }}</h1>
      <button v-if="taskId" type="button" class="ghost" @click="backToList">返回任务列表</button>
      <button v-else type="button" class="primary" @click="openCreate">新建测试任务</button>
    </header>

    <div v-if="!taskId" class="test-surface">
      <form class="task-query" @submit.prevent="search">
        <label>任务名称<input v-model="draft.name" type="search" class="control" :disabled="phase === 'loading'" /></label>
        <label>所属项目<select v-model="draft.projectId" class="control" aria-label="筛选所属项目" :disabled="projectsLoading || phase === 'loading'"><option value="">全部项目</option><option v-for="project in projects" :key="project.id" :value="project.id">{{ project.name }}</option></select></label>
        <div class="query-actions"><button type="submit" class="primary" :disabled="phase === 'loading'">查询</button><button type="button" class="ghost" :disabled="phase === 'loading'" @click="draft = { name: '', projectId: '' }; search()">重置</button></div>
      </form>
      <p v-if="projectsError" class="error-strip" role="alert">项目读取失败：{{ projectsError }} <button type="button" class="ghost" @click="loadProjects">重试</button></p>
      <div v-if="phase === 'loading'" class="state-panel" role="status">正在读取测试任务…</div>
      <div v-else-if="phase === 'error'" class="state-panel" role="alert"><strong>测试任务加载失败</strong><p>{{ errorText }}</p><button type="button" class="ghost" @click="loadList">重试</button></div>
      <div v-else-if="!tasks.length" class="state-panel"><strong>{{ applied.name || applied.projectId ? "没有找到测试任务" : "还没有测试任务" }}</strong></div>
      <div v-else class="task-list">
        <div class="task-row task-columns" aria-hidden="true"><span>任务名称</span><span>所属项目</span><span>状态</span><span>操作</span></div>
        <button v-for="item in tasks" :key="item.id" type="button" class="task-row" @click="openTask(item.id)"><strong>{{ item.name }}</strong><span>{{ item.projectName || projects.find(project => project.id === item.projectId)?.name || "—" }}</span><span class="state-label" :data-state="item.status">{{ statusLabel(item.status) }}</span><span class="open-link">打开 →</span></button>
      </div>
      <nav class="pager" aria-label="测试任务分页"><span class="total">共 {{ total }} 个任务</span><label>每页<select v-model.number="pageSize" class="control" aria-label="每页" :disabled="phase !== 'ready'" @change="page = 1; loadList()"><option :value="10">10 条</option><option :value="20">20 条</option><option :value="50">50 条</option></select></label><span>第 {{ page }} / {{ pageCount }} 页</span><button type="button" class="ghost" :disabled="phase !== 'ready' || page <= 1" @click="changePage(page - 1)">上一页</button><button type="button" class="ghost" :disabled="phase !== 'ready' || page >= pageCount" @click="changePage(page + 1)">下一页</button><form @submit.prevent="changePage(targetPage)"><label>页码<input v-model.number="targetPage" class="control" type="number" min="1" :max="pageCount" :disabled="phase !== 'ready' || !total" required /></label><button type="submit" class="ghost" :disabled="phase !== 'ready' || !total">跳转</button></form></nav>
    </div>

    <div v-else class="test-surface">
      <div v-if="detailPhase === 'loading'" class="state-panel" role="status">正在读取测试任务…</div>
      <div v-else-if="detailPhase === 'error'" class="state-panel" role="alert"><strong>测试任务加载失败</strong><p>{{ detailError }}</p><button type="button" class="ghost" @click="loadDetail()">重试</button></div>
      <template v-else-if="task">
        <div class="detail-toolbar">
          <nav class="detail-tabs" aria-label="测试任务视图"><button type="button" :class="{ active: tab === 'cases' }" :aria-current="tab === 'cases' ? 'page' : undefined" @click="tab = 'cases'">测试项</button><button type="button" :class="{ active: tab === 'results' }" :aria-current="tab === 'results' ? 'page' : undefined" @click="tab = 'results'">执行结果</button></nav>
          <span class="state-label" :data-state="task.status" role="status">{{ analyzing ? "正在提取测试项" : activeRun?.stopRequested ? "正在停止" : running && tab === 'results' && activeResults.length ? `正在测试 ${finishedCount} / ${activeResults.length}` : statusLabel(task.status) }}</span>
          <div class="toolbar-actions"><button type="button" class="ghost" :disabled="locked" @click="analyze">{{ analyzing ? "正在提取…" : task.cases.length ? "重新提取" : "从计划提取测试项" }}</button><button v-if="running" type="button" class="ghost stop-button" :disabled="operation === 'stop' || task.runs[0]?.stopRequested" @click="stop">{{ operation === 'stop' || task.runs[0]?.stopRequested ? "正在停止…" : "停止测试" }}</button><button v-else type="button" class="primary" :disabled="locked || !enabledCount" @click="run()">{{ operation === 'run' ? "正在启动…" : "运行已勾选测试项" }}</button></div>
        </div>
        <p v-if="actionError || detailError || task.error" class="error-strip" role="alert">{{ actionError || detailError || task.error }}</p>
        <div class="config-row"><details class="task-config"><summary>测试配置</summary><dl><div><dt>所属项目</dt><dd>{{ task.projectName || projects.find(project => project.id === task?.projectId)?.name || "—" }}</dd></div><div><dt>测试计划</dt><dd>{{ task.planName || "未提供" }}</dd></div><div><dt>功能测试方式</dt><dd>{{ task.executionMode === "agent" ? "AI 实时操作" : "按固定步骤" }}</dd></div><div><dt>前端地址</dt><dd>{{ task.frontendUrl || "未提供" }}</dd></div><div><dt>前端源码</dt><dd>{{ task.frontendRoot || "未提供" }}</dd></div><div><dt>后端地址</dt><dd>{{ task.backendUrl || "未提供" }}</dd></div><div><dt>后端源码</dt><dd>{{ task.backendRoot || "未提供" }}</dd></div><div><dt>允许修改的测试数据</dt><dd>{{ task.dataScope || "未提供" }}</dd></div></dl></details><button type="button" class="ghost" :disabled="locked" @click="openEdit">编辑配置</button></div>

        <div v-if="tab === 'cases'" class="case-area">
          <div v-if="analyzing && !task.cases.length" class="state-panel" role="status">正在读取测试计划和源码…</div>
          <div v-else-if="!task.cases.length" class="state-panel"><strong>尚未提取测试项</strong><button type="button" class="primary" :disabled="locked" @click="analyze">提取测试项</button></div>
          <article v-for="item in task.cases" :key="item.id" class="case-block">
            <header class="case-head"><label class="case-title"><input type="checkbox" :checked="item.enabled && !missingInput(item)" :disabled="locked || Boolean(missingInput(item))" :aria-label="`选择测试项：${item.title}`" @change="toggleCase(item, $event)" /><strong>{{ item.title }}</strong></label><span class="kind-label">{{ kindLabel(item.kind) }}</span><button type="button" class="ghost" :aria-label="`运行测试项：${item.title}`" :disabled="locked || !item.enabled || Boolean(missingInput(item))" @click="run([item.id])">运行此项</button></header>
            <dl class="case-facts"><div class="full-row"><dt>预期结果</dt><dd>{{ item.expected || "未确定" }}</dd></div></dl>
            <details class="test-procedure"><summary>{{ item.kind === 'unit' ? "测试步骤与代码" : "测试步骤与依据" }}</summary><dl class="case-facts"><div><dt>计划依据</dt><dd>{{ item.planReference || "未提取到计划依据" }}</dd></div><div><dt>代码位置</dt><dd>{{ item.sourceReference || "未确定" }}</dd></div></dl><ol v-if="item.steps?.length" class="planned-steps"><li v-for="(step, index) in item.steps" :key="index"><strong>{{ stepLabel(step) }}</strong><details v-if="stepDetails(step)"><summary>操作参数</summary><pre>{{ stepDetails(step) }}</pre></details><pre v-if="stepCode(step)">{{ stepCode(step) }}</pre></li></ol></details>
            <div v-if="missingInput(item)" class="missing-input"><span>缺少条件：{{ missingInput(item) }}</span><button type="button" class="ghost" :disabled="locked" @click="openEdit">补充配置</button></div>
          </article>
        </div>

        <div v-else class="results-area">
          <div v-if="!task.runs.length" class="state-panel"><strong>还没有执行记录</strong><button type="button" class="ghost" @click="tab = 'cases'">查看测试项</button></div>
          <template v-else>
            <div class="run-toolbar"><label>执行记录<select v-model="runId" class="control" aria-label="执行记录" @change="resultCaseId = ''"><option v-for="record in task.runs" :key="record.id" :value="record.id">{{ dateLabel(record.startedAt) }} · {{ statusLabel(record.status) }}</option></select></label><div class="run-actions"><button type="button" class="ghost" :disabled="downloading || !reportReady" @click="downloadReport">{{ downloading ? "正在生成 Word…" : "下载测试报告 Word" }}</button><button type="button" class="primary" :disabled="writing || !reportReady" @click="openWrite">补充到文档</button></div></div>
            <p v-if="currentRun?.error || currentRun?.result?.error" class="error-strip" role="alert">{{ currentRun.error || currentRun.result.error }}</p>
            <div v-if="!results.length" class="state-panel" role="status">{{ currentRun && ['running', 'queued', 'stopping'].includes(currentRun.status) ? "正在启动测试…" : "本次执行没有测试结果" }}</div>
            <div v-else class="result-workspace">
              <nav class="result-list" aria-label="测试结果"><button v-for="item in results" :key="item.caseId" type="button" :class="{ selected: currentResult?.caseId === item.caseId }" @click="resultCaseId = item.caseId"><strong>{{ item.title }}</strong><span class="state-label" :data-state="item.status">{{ statusLabel(item.status) }}</span></button></nav>
              <article v-if="currentResult" ref="resultDetail" class="result-detail">
                <header class="result-head"><h2>{{ currentResult.title }}</h2><span class="state-label" :data-state="currentResult.status">{{ statusLabel(currentResult.status) }}</span></header>
                <p v-if="currentResult.error" class="result-error" role="alert">{{ currentResult.error }}</p>
                <dl v-if="currentResult.metrics && Object.keys(currentResult.metrics).length" class="metrics"><div v-for="entry in metricEntries(currentResult.metrics)" :key="entry.key"><dt>{{ metricLabel(entry.key) }}</dt><dd>{{ entry.value }}</dd></div></dl>
                <p v-if="!currentResult.steps?.length && !currentResult.log && !currentResult.error" class="empty">{{ currentResult.status === 'running' ? "正在执行…" : ['queued', 'pending'].includes(currentResult.status) ? "等待开始执行" : "没有操作步骤" }}</p>
                <ol v-if="currentResult.steps?.length" class="actual-steps">
                  <li v-for="(step, index) in currentResult.steps" :key="index">
                    <details class="actual-step" :open="step.status !== 'passed' || index === currentResult.steps.length - 1">
                      <summary><span class="step-head"><strong>{{ step.description || step.action }}</strong><span class="state-label" :data-state="step.status">{{ statusLabel(step.status) }}</span></span></summary>
                      <p v-if="step.actual">{{ step.actual }}</p>
                      <button v-if="step.screenshot && task && currentRun" type="button" class="evidence-image" :aria-label="`查看截图：${step.description || step.action}`" @click="preview(step.screenshot, step.description || step.action)"><img :src="testEvidenceUrl(task.id, currentRun.id, step.screenshot)" :alt="step.description || step.action" loading="lazy" /></button>
                    </details>
                  </li>
                </ol>
                <details v-if="currentResult.log" class="test-log" :open="currentResult.status === 'failed'"><summary>执行日志</summary><pre>{{ currentResult.log }}</pre></details>
              </article>
            </div>
          </template>
        </div>
      </template>
    </div>

    <dialog v-if="creating" ref="createDialog" class="test-dialog" aria-labelledby="create-test-title" @cancel="submitting && $event.preventDefault()" @close="creating = false; form.testPassword = ''; editingId = ''">
      <form novalidate @submit.prevent="submitCreate">
        <div class="dialog-head"><h2 id="create-test-title">{{ editingId ? "编辑测试配置" : "新建测试任务" }}</h2><button type="button" class="ghost" :disabled="submitting" :aria-label="editingId ? '关闭编辑测试配置' : '关闭新建测试任务'" @click="closeCreate">关闭</button></div>
        <fieldset :disabled="submitting" class="dialog-fields">
          <label>任务名称<input ref="nameInput" v-model="form.name" class="control" required maxlength="120" /></label>
          <label>所属项目<select v-model="form.projectId" class="control" aria-label="所属项目" :disabled="Boolean(editingId) || projectsLoading || !projects.length" required><option value="">{{ projectsLoading ? "正在读取项目…" : "请选择项目" }}</option><option v-for="project in projects" :key="project.id" :value="project.id">{{ project.name }}</option></select></label>
          <p v-if="projectsError" class="field-error full-row" role="alert">项目读取失败：{{ projectsError }} <button type="button" class="ghost" @click="loadProjects">重试</button></p>
          <p v-else-if="!projectsLoading && !projects.length" class="field-error full-row">请先创建项目。</p>
          <label>{{ editingId ? '替换测试计划 Word（可选）' : '测试计划 Word' }}<input type="file" accept=".docx" class="control" :aria-label="editingId ? '替换测试计划 Word（可选）' : '测试计划 Word'" :required="!editingId" @change="planFile = ($event.target as HTMLInputElement).files?.[0] ?? null" /><span v-if="editingId && !planFile">当前文件：{{ task?.planName }}</span></label>
          <label>待补充的测试文档 Word（可选）<input type="file" accept=".docx" class="control" aria-label="待补充的测试文档 Word（可选）" @change="reportFile = ($event.target as HTMLInputElement).files?.[0] ?? null" /><span v-if="editingId && !reportFile && task?.reportName">当前文件：{{ task.reportName }}</span></label>
          <label>前端源码目录<input v-model="form.frontendRoot" class="control" /></label>
          <label>前端服务地址<input v-model="form.frontendUrl" type="url" class="control" /></label>
          <label>后端源码目录<input v-model="form.backendRoot" class="control" /></label>
          <label>后端服务地址<input v-model="form.backendUrl" type="url" class="control" /></label>
          <label class="full-row">功能测试方式<select v-model="form.executionMode" class="control" aria-label="功能测试方式"><option value="agent">AI 实时操作</option><option value="steps">按固定步骤</option></select></label>
          <label>测试账号<input v-model="form.testUsername" class="control" autocomplete="off" /></label>
          <label>{{ editingId ? "测试密码（留空保留）" : "测试密码" }}<input v-model="form.testPassword" type="password" class="control" autocomplete="new-password" /></label>
          <label class="full-row">允许修改的测试数据范围<textarea v-model="form.dataScope" class="control" rows="2" /></label>
          <details class="full-row concurrency-fields"><summary>并发测试参数</summary><div><label>并发数<input v-model.number="form.concurrency" type="number" min="1" max="20" class="control" required /></label><label>请求总数<input v-model.number="form.requests" type="number" min="1" max="200" class="control" required /></label></div></details>
        </fieldset>
        <p v-if="formError" class="form-error" role="alert">{{ formError }}</p>
        <div class="dialog-actions"><button type="button" class="ghost" :disabled="submitting" @click="closeCreate">取消</button><button type="submit" class="primary" :disabled="submitting || projectsLoading || !projects.length">{{ submitting ? editingId ? "正在保存…" : "正在上传并创建…" : editingId ? "保存配置" : "创建测试任务" }}</button></div>
      </form>
    </dialog>
    <dialog v-if="writeOpen" ref="writeDialog" class="test-dialog" aria-labelledby="write-test-title" @cancel="writing && $event.preventDefault()" @close="writeOpen = false; ++writeRequest">
      <div class="dialog-head"><h2 id="write-test-title">补充测试结果</h2><button type="button" class="ghost" :disabled="writing" aria-label="关闭补充测试结果" @click="closeWrite">关闭</button></div>
      <div v-if="writeSaved" class="state-panel" role="status"><strong>{{ writeSaved.insertedCount ? "已补充测试结果" : "这些测试结果已在文档中" }}</strong><p>{{ writeSaved.documentName }}</p><RouterLink class="primary" :to="`/projects/${writeSaved.projectId}/documents/${writeSaved.documentId}`" @click="closeWrite">打开文档</RouterLink></div>
      <div v-else-if="writePhase === 'loading'" class="state-panel" role="status">正在读取项目文档…</div>
      <div v-else-if="writePhase === 'error'" class="state-panel" role="alert"><strong>文档读取失败</strong><p>{{ writeError }}</p><button type="button" class="ghost" @click="loadWriteDocuments">重试</button></div>
      <div v-else-if="!writeDocuments.length" class="state-panel"><strong>项目中还没有文档</strong><RouterLink v-if="task" class="primary" :to="`/projects/${task.projectId}/documents`" @click="closeWrite">新建文档</RouterLink></div>
      <form v-else novalidate @submit.prevent="writeResults">
        <fieldset class="dialog-fields" :disabled="writing">
          <label>目标文档<select v-model="writeDocumentId" class="control" aria-label="目标文档" required @change="loadWriteDocument"><option value="">请选择文档</option><option v-for="item in writeDocuments" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
          <label>写入位置<select v-model="writeHeading" class="control" aria-label="写入位置" :disabled="writeDocumentLoading || !writeDocument" required><option value="pending" disabled>{{ writeDocumentLoading ? "正在读取章节…" : "请选择写入位置" }}</option><option value="">文档末尾</option><option v-for="item in writeHeadings" :key="item.index" :value="String(item.index)">{{ item.text }}</option></select></label>
          <fieldset class="write-cases full-row"><legend>要补充的测试项</legend><label v-for="item in writeRun?.result.cases" :key="item.caseId" class="write-case"><input v-model="writeCaseIds" type="checkbox" :value="item.caseId" :aria-label="'补充测试项：' + item.title" /><span>{{ item.title }}</span><span class="state-label" :data-state="item.status">{{ statusLabel(item.status) }}</span></label></fieldset>
        </fieldset>
        <p v-if="writeError" class="form-error" role="alert">{{ writeError }} <button v-if="writeDocumentId && !writeDocument && !writeDocumentLoading" type="button" class="ghost" :disabled="writing" @click="loadWriteDocument">重新读取文档</button></p>
        <div class="dialog-actions"><button type="button" class="ghost" :disabled="writing" @click="closeWrite">取消</button><button type="submit" class="primary" :disabled="writing || writeDocumentLoading">{{ writing ? "正在补充…" : "写入测试结果" }}</button></div>
      </form>
    </dialog>
    <dialog ref="previewDialog" class="test-dialog screenshot-dialog" aria-labelledby="screenshot-title"><div class="dialog-head"><h2 id="screenshot-title">{{ previewName || "测试截图" }}</h2><button type="button" class="ghost" @click="previewDialog?.close()">关闭截图</button></div><img v-if="previewUrl" :src="previewUrl" :alt="previewName" /></dialog>
  </section>
</template>

<style scoped>
.test-page { display:flex; flex:none; flex-direction:column; min-width:0; min-height:0; height:calc(100vh - var(--space-7) - var(--space-3) - var(--space-5) * 2); gap:var(--space-5); }
.page-head { display:flex; align-items:center; justify-content:space-between; gap:var(--space-5); padding:var(--space-3) var(--space-2) 0; }
.page-head h1 { min-width:0; margin:0; font-size:calc(var(--text-md) * 1.75); line-height:1.25; overflow-wrap:anywhere; }
.page-head button { flex-shrink:0; }
.test-surface { display:flex; flex:1; flex-direction:column; min-height:0; min-width:0; width:100%; background:var(--sheet); border:1px solid var(--rule); border-radius:var(--radius-sheet); }
.task-query { display:flex; align-items:end; gap:var(--space-4); padding:var(--space-5); border-bottom:1px solid var(--rule); }
.task-query label { display:grid; gap:var(--space-1); width:calc(var(--space-7) * 7); color:var(--ink-secondary); font-size:var(--text-sm); }
.task-query .query-actions { display:flex; gap:var(--space-2); }
.state-panel { display:flex; flex:1; flex-direction:column; align-items:center; justify-content:center; gap:var(--space-3); padding:var(--space-5); min-height:calc(var(--space-7) * 5); color:var(--ink-secondary); text-align:center; }
.state-panel strong { color:var(--ink); font-size:var(--text-md); }
.state-panel[role="alert"] strong { color:var(--fault); }
.state-panel p { margin:0; max-width:100%; overflow-wrap:anywhere; }
.task-list { flex:1; min-height:0; overflow:auto; }
.task-row { display:grid; grid-template-columns:minmax(0,2fr) minmax(0,1fr) calc(var(--space-7) * 3) calc(var(--space-7) * 2); align-items:center; gap:var(--space-5); width:100%; min-height:calc(var(--space-7) + var(--space-2)); padding:var(--space-3) var(--space-5); border:0; border-bottom:1px solid var(--rule); background:var(--sheet); color:var(--ink); font:inherit; text-align:left; cursor:pointer; }
.task-row > * { min-width:0; overflow-wrap:anywhere; }
.task-row strong { font-size:var(--text-md); }
.task-row:hover { background:var(--desk); }
.task-row:focus-visible,.detail-tabs button:focus-visible,.result-list button:focus-visible,.evidence-image:focus-visible { outline:2px solid var(--stamp); outline-offset:-2px; }
.task-columns { min-height:var(--space-7); background:var(--desk); color:var(--ink-secondary); cursor:default; font-size:var(--text-sm); }
.open-link { text-align:right; color:var(--stamp); }
.pager { flex-shrink:0; gap:var(--space-3); margin-top:auto; padding:var(--space-3) var(--space-5); border-top:1px solid var(--rule); color:var(--ink-secondary); font-size:var(--text-sm); }
.pager .total { margin-right:auto; }
.pager label,.pager form { display:flex; align-items:center; gap:var(--space-2); }
.pager .control { width:calc(var(--space-7) * 2); }
.detail-toolbar { display:flex; flex-shrink:0; align-items:center; gap:var(--space-5); padding:0 var(--space-5); min-height:calc(var(--space-7) + var(--space-4)); border-bottom:1px solid var(--rule); }
.detail-tabs { display:flex; align-self:stretch; gap:var(--space-5); }
.detail-tabs button { padding:0; border:0; border-bottom:2px solid transparent; background:transparent; color:var(--ink-secondary); font:inherit; font-size:var(--text-md); cursor:pointer; }
.detail-tabs button.active { color:var(--stamp); border-bottom-color:var(--stamp); font-weight:600; }
.toolbar-actions { display:flex; margin-left:auto; gap:var(--space-2); }
.state-label { color:var(--ink-secondary); font-size:var(--text-sm); white-space:nowrap; }
.state-label[data-state="passed"],.state-label[data-state="completed"] { color:var(--ok); }
.state-label[data-state="running"],.state-label[data-state="analyzing"] { color:var(--stamp); }
.state-label[data-state="failed"] { color:var(--fault); }
.state-label[data-state="blocked"],.state-label[data-state="stopped"] { color:var(--warn); }
.stop-button { color:var(--fault); }
.error-strip { display:flex; flex-shrink:0; align-items:center; gap:var(--space-3); margin:0; padding:var(--space-3) var(--space-5); color:var(--fault); background:var(--fault-wash); border-bottom:1px solid var(--rule); overflow-wrap:anywhere; }
.config-row { display:flex; flex-shrink:0; align-items:flex-start; gap:var(--space-4); padding:var(--space-3) var(--space-5); border-bottom:1px solid var(--rule); }
.config-row > button { flex-shrink:0; }
.task-config { flex:1; min-width:0; padding:var(--space-2) 0; color:var(--ink-secondary); font-size:var(--text-sm); }
summary { cursor:pointer; }
summary:focus-visible { outline:2px solid var(--stamp); outline-offset:2px; }
.task-config dl,.case-facts { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:var(--space-3) var(--space-5); margin:var(--space-3) 0 0; }
dl div { min-width:0; }
dt { color:var(--ink-secondary); font-size:var(--text-sm); }
dd { margin:var(--space-1) 0 0; color:var(--ink); overflow-wrap:anywhere; }
.case-area { display:flex; flex:1; flex-direction:column; min-height:0; overflow:auto; }
.case-block { padding:var(--space-4) var(--space-5); border-bottom:1px solid var(--rule); }
.case-head { display:flex; align-items:center; gap:var(--space-4); }
.case-title { display:flex; align-items:center; gap:var(--space-3); flex:1; min-width:0; color:var(--ink); font-size:var(--text-md); overflow-wrap:anywhere; }
.case-title input { flex-shrink:0; accent-color:var(--stamp); }
.case-title input:focus-visible { outline:2px solid var(--stamp); outline-offset:2px; }
.kind-label { color:var(--ink-secondary); font-size:var(--text-sm); white-space:nowrap; }
.full-row { grid-column:1 / -1; }
.planned-steps { margin:var(--space-4) 0 0; padding-left:var(--space-5); color:var(--ink); overflow-wrap:anywhere; }
.planned-steps li + li { margin-top:var(--space-1); }
.test-procedure { margin-top:var(--space-4); color:var(--ink-secondary); }
.test-procedure pre { white-space:pre-wrap; overflow-wrap:anywhere; padding:var(--space-3); border:1px solid var(--rule); background:var(--desk); font-size:var(--text-sm); }
.missing-input { display:flex; align-items:center; gap:var(--space-4); margin:var(--space-3) 0 0; padding:var(--space-2) var(--space-3); color:var(--warn); background:var(--warn-wash); overflow-wrap:anywhere; }
.missing-input span { flex:1; min-width:0; }
.missing-input button { flex-shrink:0; }
.results-area { display:flex; flex:1; flex-direction:column; min-height:0; }
.run-toolbar { display:flex; flex-shrink:0; align-items:center; gap:var(--space-4); padding:var(--space-4) var(--space-5); border-bottom:1px solid var(--rule); }
.run-toolbar label { display:flex; align-items:center; gap:var(--space-3); color:var(--ink-secondary); font-size:var(--text-sm); }
.run-toolbar .control { width:calc(var(--space-7) * 7); }
.run-actions { display:flex; gap:var(--space-2); margin-left:auto; }
.result-workspace { display:grid; flex:1; min-height:0; min-width:0; grid-template-columns:calc(var(--space-7) * 7) minmax(0,1fr); }
.result-list { min-height:0; overflow:auto; padding:var(--space-3); border-right:1px solid var(--rule); }
.result-list button { display:flex; align-items:start; justify-content:space-between; gap:var(--space-3); width:100%; margin:0; padding:var(--space-4) var(--space-3); border:0; border-bottom:1px solid var(--rule); border-radius:var(--radius-control); background:var(--sheet); color:var(--ink); font:inherit; text-align:left; cursor:pointer; }
.result-list button:hover { background:var(--desk); }
.result-list button.selected { background:var(--stamp-wash); color:var(--stamp); }
.result-list strong { min-width:0; overflow-wrap:anywhere; }
.result-detail { min-height:0; min-width:0; overflow:auto; padding:var(--space-5); }
.result-head { display:flex; align-items:start; gap:var(--space-4); }
.result-head h2 { flex:1; min-width:0; margin:0; font-size:calc(var(--text-md) * 1.25); overflow-wrap:anywhere; }
.result-error { margin:var(--space-4) 0; padding:var(--space-3); background:var(--fault-wash); color:var(--fault); white-space:pre-wrap; overflow-wrap:anywhere; }
.metrics { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:var(--space-4); padding:var(--space-4); margin:var(--space-4) 0; background:var(--desk); border:1px solid var(--rule); }
.actual-steps { margin:var(--space-5) 0; padding-left:var(--space-5); }
.actual-steps li { padding:0 0 var(--space-3) var(--space-2); }
.actual-step > summary .step-head { display:inline-flex; width:calc(100% - var(--space-4)); vertical-align:middle; }
.step-head { display:flex; gap:var(--space-4); justify-content:space-between; }
.step-head strong { min-width:0; overflow-wrap:anywhere; }
.actual-steps p { margin:var(--space-2) 0; white-space:pre-wrap; overflow-wrap:anywhere; }
.evidence-image { display:block; width:calc(var(--space-7) * 12); max-width:100%; margin-top:var(--space-3); padding:0; border:1px solid var(--rule-strong); border-radius:var(--radius-sheet); background:var(--desk); cursor:pointer; }
.evidence-image img { display:block; width:100%; height:auto; }
.test-log { margin-top:var(--space-4); padding:var(--space-3); border:1px solid var(--rule); background:var(--desk); }
.test-log pre { white-space:pre-wrap; overflow-wrap:anywhere; font:inherit; font-size:var(--text-sm); }
.test-dialog { width:calc(var(--space-7) * 21); max-width:calc(100vw - var(--space-7) * 2); max-height:calc(100vh - var(--space-7)); padding:0; overflow:auto; border:1px solid var(--rule-strong); border-radius:var(--radius-sheet); background:var(--sheet); color:var(--ink); }
.test-dialog::backdrop { background:var(--spine); opacity:.45; }
.dialog-head { display:flex; justify-content:space-between; align-items:center; gap:var(--space-4); padding:var(--space-4) var(--space-5); border-bottom:1px solid var(--rule); }
.dialog-head h2 { min-width:0; margin:0; font-size:calc(var(--text-md) * 1.25); overflow-wrap:anywhere; }
.dialog-fields { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:var(--space-4) var(--space-5); min-width:0; margin:0; padding:var(--space-5); border:0; }
.dialog-fields label { display:grid; gap:var(--space-1); min-width:0; color:var(--ink-secondary); font-size:var(--text-sm); }
.dialog-fields label span { overflow-wrap:anywhere; }
.write-cases { display:grid; gap:var(--space-3); min-width:0; margin:0; padding:var(--space-3) var(--space-4) var(--space-4); border:1px solid var(--rule); border-radius:var(--radius-control); }
.write-cases legend { padding:0 var(--space-2); color:var(--ink-secondary); font-size:var(--text-sm); }
.dialog-fields .write-case { display:flex; align-items:center; gap:var(--space-3); color:var(--ink); font-size:var(--text-md); }
.write-case input { flex-shrink:0; accent-color:var(--stamp); }
.write-case input:focus-visible { outline:2px solid var(--stamp); outline-offset:2px; }
.write-case > span:first-of-type { flex:1; min-width:0; }
.concurrency-fields > div { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:var(--space-5); margin-top:var(--space-3); }
.dialog-fields input[type="file"] { height:auto; min-height:var(--space-6); padding:var(--space-1) var(--space-2); }
.dialog-fields .control:disabled { background:var(--desk); color:var(--ink-secondary); }
.form-error { margin:0; padding:0 var(--space-5) var(--space-4); color:var(--fault); overflow-wrap:anywhere; }
.dialog-actions { display:flex; justify-content:flex-end; gap:var(--space-2); padding:var(--space-4) var(--space-5); border-top:1px solid var(--rule); }
.screenshot-dialog { width:calc(100vw - var(--space-7) * 2); }
.screenshot-dialog > img { display:block; width:100%; height:auto; max-height:calc(100vh - var(--space-7) * 3); object-fit:contain; }
</style>
