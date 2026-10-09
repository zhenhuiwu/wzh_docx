<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { ApiError } from "../api/client";
import { getProject, type ProjectRecord } from "../api/projects";

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
    phase.value = "ready";
    return;
  }
  try {
    const result = await getProject(target);
    if (request !== loadRequest || target !== projectId.value) return;
    project.value = result;
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
  <div class="project-frame">
    <header class="frame-head">
      <div>
        <h1>{{ project?.name || "项目" }}</h1>
      </div>
      <router-link class="ghost" :to="{ name: 'projects' }">返回项目列表</router-link>
    </header>
    <nav class="subnav" aria-label="项目内导航">
      <router-link :class="{ on: active === 'overview' }" :to="{ name: 'project', params: { id: projectId } }">项目概述</router-link>
      <router-link :class="{ on: active === 'documents' }" :to="{ name: 'project-documents', params: { id: projectId } }">文档编写</router-link>
    </nav>
    <p v-if="phase === 'loading'" class="empty">正在读取项目</p>
    <div v-else-if="phase === 'error'" class="notice" role="alert">{{ errorText }} <button type="button" class="ghost" @click="load">重试</button></div>
    <slot v-else :project="project" />
  </div>
</template>

<style scoped>
.project-frame { display:flex; flex:1; flex-direction:column; width:100%; min-width:0; background:var(--sheet); border:1px solid var(--rule); border-radius:var(--radius-sheet); }
.frame-head { display:flex; align-items:center; justify-content:space-between; gap:var(--space-5); padding:var(--space-6) var(--space-6) var(--space-5); }
.frame-head h1 { margin:0; font-size:calc(var(--text-md) * 1.5); line-height:1.3; overflow-wrap:anywhere; }
.subnav { display:flex; gap:var(--space-6); padding:0 var(--space-6); border-bottom:1px solid var(--rule); }
.subnav a { padding:var(--space-3) 0; border-bottom:2px solid transparent; color:var(--ink-secondary); font-size:var(--text-sm); text-decoration:none; }
.subnav a:hover { color:var(--ink); }
.subnav a.on { border-color:var(--stamp); color:var(--stamp); font-weight:600; }
.subnav a:focus-visible { outline:2px solid var(--stamp); outline-offset:2px; }
</style>
