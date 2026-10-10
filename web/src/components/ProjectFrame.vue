<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { ApiError } from "../api/client";
import { getProject, type ProjectRecord } from "../api/projects";
import { rememberProject } from "../crumbs";
import { statusLabel } from "../projects";
import StatePanel from "./StatePanel.vue";

const statusTone: Record<ProjectRecord["status"], string> = { not_started: "", active: "success", paused: "warning", completed: "info" };

const route = useRoute();
const props = defineProps<{ project?: ProjectRecord | null }>();
const projectId = computed(() => String(route.params.id));
const project = ref<ProjectRecord | null>(null);
const phase = ref<"loading" | "ready" | "error">("loading");
const errorText = ref("");
let loadRequest = 0;

const active = computed(() => {
  if (route.name === "project-documents" || route.name === "document") return "documents";
  return "overview";
});

async function load() {
  const target = projectId.value;
  const request = ++loadRequest;
  phase.value = "loading";
  errorText.value = "";
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
    errorText.value = error instanceof ApiError ? error.message : "暂时不能连接服务。";
  }
}

watch([projectId, () => props.project], () => void load(), { immediate: true });
onBeforeUnmount(() => ++loadRequest);
</script>

<template>
  <div class="view project-frame">
    <header class="page-head frame-head">
      <div>
        <h1>{{ project?.name || "项目" }}</h1>
        <p v-if="project" class="page-desc frame-meta">
          <span class="tag" :class="statusTone[project.status]">{{ statusLabel(project.status) }}</span>
          <span>负责人：{{ project.ownerName || "—" }}</span>
          <span v-if="project.department">所属部门：{{ project.department }}</span>
        </p>
      </div>
      <div class="page-actions"><router-link class="ghost" :to="{ name: 'projects' }">返回项目列表</router-link></div>
    </header>
    <div class="card frame-card">
      <nav class="tabs subnav" aria-label="项目内导航">
        <router-link :class="{ on: active === 'overview' }" :aria-current="active === 'overview' ? 'page' : undefined" :to="{ name: 'project', params: { id: projectId } }">项目概述</router-link>
        <router-link :class="{ on: active === 'documents' }" :aria-current="active === 'documents' ? 'page' : undefined" :to="{ name: 'project-documents', params: { id: projectId } }">文档编写</router-link>
      </nav>
      <StatePanel v-if="phase === 'loading'" kind="loading" text="正在读取项目…" />
      <StatePanel v-else-if="phase === 'error'" kind="error" title="项目加载失败" :text="errorText"><button type="button" class="ghost" @click="load">重试</button></StatePanel>
      <slot v-else :project="project" />
    </div>
  </div>
</template>

<style scoped>
.project-frame { flex:1; min-height:var(--content-h); }
/* 超长项目名换行，返回按钮不被挤压（02 评审 P1-11） */
.frame-head { align-items:flex-start; }
.frame-head .page-actions { padding-top:var(--space-1); }
.frame-head .page-actions a { flex-shrink:0; white-space:nowrap; }
.frame-meta { display:flex; flex-wrap:wrap; align-items:center; gap:var(--space-2) var(--space-4); }
.frame-card { display:flex; flex:1; flex-direction:column; min-height:0; }
</style>
