<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ApiError, isMissingResource } from "../api/client";
import { archiveProject, getProject, type ProjectRecord } from "../api/projects";
import { rememberProject } from "../crumbs";
import { flash } from "../flash";
import { statusLabel } from "../projects";
import { NO_ACTIVE_OWNER_TAG, activeOwners, ownersText } from "../memberModel";
import StatePanel from "./StatePanel.vue";

const statusTone: Record<ProjectRecord["status"], string> = { not_started: "", active: "success", paused: "warning", completed: "info" };

const route = useRoute();
const router = useRouter();
const props = defineProps<{ project?: ProjectRecord | null }>();
/** 归档或恢复成功：带上重新读取的项目（含 permissions），页面据此刷新自己的数据。 */
const emit = defineEmits<{ (event: "archive-changed", value: ProjectRecord): void }>();
const projectId = computed(() => String(route.params.id));
const project = ref<ProjectRecord | null>(null);
const phase = ref<"loading" | "ready" | "error">("loading");
const errorText = ref("");
// 后端对“不存在”和“非成员”统一返回 404，前端统一口径（01 §11.4，09 审查 M4）。
const notFound = ref(false);
let loadRequest = 0;

const active = computed(() => {
  if (route.name === "project-documents" || route.name === "document") return "documents";
  if (route.name === "project-baselines") return "baselines";
  return "overview";
});

async function load() {
  const target = projectId.value;
  const request = ++loadRequest;
  phase.value = "loading";
  errorText.value = "";
  notFound.value = false;
  project.value = null;
  if (props.project?.id === target) {
    project.value = props.project;
    rememberProject(props.project);
    phase.value = "ready";
    return;
  }
  try {
    const result = await getProject(target);
    if (request !== loadRequest || target !== projectId.value) return;
    project.value = result;
    rememberProject(result);
    phase.value = "ready";
  } catch (error) {
    if (request !== loadRequest || target !== projectId.value) return;
    phase.value = "error";
    notFound.value = isMissingResource(error);
    errorText.value = error instanceof ApiError ? error.message : "暂时不能连接服务。";
  }
}

watch([projectId, () => props.project], () => void load(), { immediate: true });
onBeforeUnmount(() => ++loadRequest);

// ---------- 归档 / 恢复（21 M-1）：按钮跟后端同一权限（projects.manage，且是系统管理员或项目负责人，即 permissions.canArchive） ----------
const archived = computed(() => Boolean(project.value?.archivedAt));
const canArchive = computed(() => Boolean(project.value?.permissions?.canArchive));
const noActiveOwner = computed(() => Boolean(project.value?.owners) && !activeOwners(project.value?.owners).length);
// 页头之外的页签（文档编写、审核）没有自己的归档提示，统一在这里给（20 L-4）；概述的成员区、资料页各有提示
const showArchivedNotice = computed(() => archived.value && active.value === "documents" && route.name === "project-documents");
const archiveDialog = ref<HTMLDialogElement | null>(null);
const archiveMode = ref<"archive" | "restore" | null>(null);
const archiveReason = ref("");
const archiveError = ref("");
const archiveBusy = ref(false);

function openArchive(mode: "archive" | "restore") {
  if (!project.value || archiveBusy.value) return;
  archiveMode.value = mode;
  archiveReason.value = "";
  archiveError.value = "";
  void nextTick(() => {
    archiveDialog.value?.showModal();
    (archiveDialog.value?.querySelector("textarea") as HTMLTextAreaElement | null)?.focus();
  });
}

function closeArchive() {
  if (archiveBusy.value) return;
  archiveMode.value = null;
  archiveDialog.value?.close();
}

async function submitArchive() {
  const current = project.value;
  const mode = archiveMode.value;
  if (!current || !mode || archiveBusy.value) return;
  const reason = archiveReason.value.trim();
  if (!reason) { archiveError.value = mode === "restore" ? "请填写恢复原因。" : "请填写归档原因。"; return; }
  archiveBusy.value = true;
  archiveError.value = "";
  try {
    await archiveProject(current, reason, mode === "restore");
    const fresh = await getProject(current.id);
    archiveBusy.value = false;
    closeArchive();
    if (!props.project) project.value = fresh;
    flash(mode === "restore" ? `已恢复 ${fresh.name}，现在可以修改。` : `已归档 ${fresh.name}，项目内容只能查看和导出。`);
    emit("archive-changed", fresh);
  } catch (error) {
    archiveBusy.value = false;
    if (error instanceof ApiError && error.code === "REVISION_CONFLICT") {
      // 别人刚改过项目：读取最新版本，让用户确认后再提交
      try {
        const fresh = await getProject(current.id);
        if (!props.project) project.value = fresh;
        emit("archive-changed", fresh);
        if (Boolean(fresh.archivedAt) === (mode === "restore")) { archiveError.value = "项目刚被别人修改过，已读取最新信息，请再提交一次。"; return; }
        closeArchive();
        flash(mode === "restore" ? "项目已被别人恢复。" : "项目已被别人归档。");
      } catch { archiveError.value = "项目刚被别人修改过，请刷新页面后再试。"; }
      return;
    }
    if (isMissingResource(error)) { closeArchive(); phase.value = "error"; notFound.value = true; return; }
    archiveError.value = error instanceof ApiError ? error.message : "暂时不能连接服务，没有保存。";
  }
}

// 用户管理“不能停用”弹窗里的归档项目链接带 ?restore=1：打开后直接弹出恢复确认（21 M-1）
watch([project, () => route.query.restore], ([value, flag]) => {
  if (flag !== "1" || !value) return;
  const query = { ...route.query };
  delete query.restore;
  void router.replace({ query, hash: route.hash });
  if (value.archivedAt && value.permissions?.canArchive) openArchive("restore");
}, { immediate: true });

defineExpose({ openRestore: () => openArchive("restore") });
</script>

<template>
  <div class="view project-frame">
    <header class="page-head frame-head">
      <div>
        <h1>{{ project?.name || "项目" }}</h1>
        <p v-if="project" class="page-desc frame-meta">
          <span v-if="archived" class="tag" data-testid="frame-archived" :title="`归档前状态：${statusLabel(project.status)}`">已归档</span>
          <span v-else class="tag" :class="statusTone[project.status]">{{ statusLabel(project.status) }}</span>
          <span data-testid="frame-owners">负责人：{{ ownersText(project.owners, project.ownerName || "—") }}</span>
          <span v-if="noActiveOwner" class="tag warning" data-testid="frame-no-owner">{{ NO_ACTIVE_OWNER_TAG }}</span>
          <span v-if="project.department">所属部门：{{ project.department }}</span>
        </p>
      </div>
      <!-- 项目不存在时，空态里已有“返回项目列表”，页头不再重复（08 复验 N-S6） -->
      <div v-if="!notFound" class="page-actions">
        <button v-if="project && archived && canArchive" type="button" class="primary" data-testid="project-restore" @click="openArchive('restore')">恢复项目</button>
        <button v-else-if="project && !archived && canArchive && active === 'overview'" type="button" class="ghost" data-testid="project-archive" @click="openArchive('archive')">归档项目</button>
        <router-link class="ghost" :to="{ name: 'projects' }">返回项目列表</router-link>
      </div>
    </header>
    <div class="card frame-card">
      <nav v-if="!notFound" class="tabs subnav" aria-label="项目内导航">
        <router-link :class="{ on: active === 'overview' }" :aria-current="active === 'overview' ? 'page' : undefined" :to="{ name: 'project', params: { id: projectId } }">项目概述</router-link>
        <router-link :class="{ on: active === 'documents' }" :aria-current="active === 'documents' ? 'page' : undefined" :to="{ name: 'project-documents', params: { id: projectId } }">文档编写</router-link>
        <router-link :class="{ on: active === 'baselines' }" :aria-current="active === 'baselines' ? 'page' : undefined" :to="{ name: 'project-baselines', params: { id: projectId } }">基线</router-link>
      </nav>
      <StatePanel v-if="phase === 'loading'" kind="loading" text="正在读取项目…" />
      <StatePanel v-else-if="phase === 'error' && notFound" kind="error" title="项目不存在或你无权访问。"><router-link class="primary" :to="{ name: 'projects' }">返回项目列表</router-link></StatePanel>
      <StatePanel v-else-if="phase === 'error'" kind="error" title="项目加载失败" :text="errorText"><button type="button" class="ghost" @click="load">重试</button></StatePanel>
      <template v-else>
        <p v-if="showArchivedNotice" class="notice warning" role="status" data-testid="frame-archived-notice">项目已归档，文档只能查看和导出。{{ canArchive ? "恢复项目后才能修改。" : "如需恢复项目，请联系系统管理员。" }}</p>
        <slot :project="project" />
      </template>
    </div>
    <dialog ref="archiveDialog" class="app-dialog confirm" data-testid="project-archive-dialog" aria-labelledby="project-archive-title" @cancel.prevent="closeArchive">
      <form v-if="archiveMode && project" novalidate @submit.prevent="submitArchive">
        <div class="dialog-head">
          <h2 id="project-archive-title">{{ archiveMode === "restore" ? `恢复 ${project.name}？` : `归档 ${project.name}？` }}</h2>
          <button type="button" class="subtle icon-close" aria-label="关闭" :disabled="archiveBusy" @click="closeArchive"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg></button>
        </div>
        <div class="dialog-body archive-body">
          <p class="dialog-text">{{ archiveMode === "restore" ? "恢复后，成员可以继续编写文档，负责人可以调整成员和权限。" : "归档后，项目里的文档、资料、成员和知识库都只能查看和导出，恢复后才能修改。" }}</p>
          <div class="field">
            <label for="project-archive-reason" class="required">{{ archiveMode === "restore" ? "恢复原因" : "归档原因" }}</label>
            <textarea id="project-archive-reason" v-model="archiveReason" class="control" rows="3" maxlength="1000" data-testid="project-archive-reason" :disabled="archiveBusy" @input="archiveError = ''" />
          </div>
          <p v-if="archiveError" class="form-alert" role="alert" data-testid="project-archive-error">{{ archiveError }}</p>
        </div>
        <div class="dialog-actions">
          <button type="button" class="ghost" :disabled="archiveBusy" @click="closeArchive">取消</button>
          <button type="submit" class="primary" data-testid="project-archive-confirm" :disabled="archiveBusy">{{ archiveBusy ? "正在提交…" : archiveMode === "restore" ? "恢复项目" : "归档项目" }}</button>
        </div>
      </form>
    </dialog>
  </div>
</template>

<style scoped>
.project-frame { flex:1; min-height:var(--content-h); }
/* 超长项目名换行，返回按钮不被挤压（02 评审 P1-11） */
.frame-head { align-items:flex-start; }
.frame-head .page-actions { padding-top:var(--space-1); }
.frame-head .page-actions a { flex-shrink:0; white-space:nowrap; }
.frame-meta { display:flex; flex-wrap:wrap; align-items:center; gap:var(--space-2) var(--space-4); }
.archive-body { display:grid; gap:var(--space-4); }
.frame-card { display:flex; flex:1; flex-direction:column; min-height:0; }
</style>
