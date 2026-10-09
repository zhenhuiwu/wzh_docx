<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import NavItem from "../components/NavItem.vue";
import { readSession } from "../session";
import { ApiError } from "../api/client";

const route = useRoute();
const router = useRouter();
const account = computed(() => readSession()?.staffId || "用户");
const systemOpen = ref(route.path.startsWith("/system"));
const loggingOut = ref(false);
const logoutError = ref("");

watch(() => route.path, (path) => { if (path.startsWith("/system")) systemOpen.value = true; });

async function logout() {
  if (loggingOut.value) return;
  loggingOut.value = true;
  logoutError.value = "";
  try {
    await router.push({ name: "login", query: { logout: "1" } });
  } catch (error) {
    logoutError.value = error instanceof ApiError ? error.message : "退出失败，请重试。";
  } finally {
    loggingOut.value = false;
  }
}
</script>

<template>
  <div class="app">
    <header class="header">
      <router-link class="brand" :to="{ name: 'projects' }" aria-label="返回项目列表">
        <svg class="logo" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M6 3.5h8l4 4V20H6zM14 3.5V8h4M9 12h6M9 15h6" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
        <span>文档工作室</span>
      </router-link>
      <div class="user">
        <span v-if="logoutError" class="field-error" role="alert">{{ logoutError }}</span>
        <span>{{ account }}</span>
        <button type="button" class="ghost" :disabled="loggingOut" @click="logout">{{ loggingOut ? "正在退出…" : "退出" }}</button>
      </div>
    </header>
    <div class="body">
      <aside class="sider">
        <nav class="menu" aria-label="主导航">
          <NavItem :to="{ name: 'projects' }" :current="['projects', 'project', 'project-documents', 'document'].includes(String(route.name))">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h6l2 2h8v10H4z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round" /></svg>
            <span>项目</span>
          </NavItem>
          <NavItem :to="{ name: 'test-cases' }" :current="route.name === 'test-cases'">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 5H6v15h12V5h-3M9 3h6v4H9zM9 12l2 2 4-4M9 17h6" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" /></svg>
            <span>测试用例管理</span>
          </NavItem>
          <!-- 系统管理始终排在业务导航之后；包含用户、模板及 Skill 管理。 -->
          <div class="system-menu">
            <button type="button" class="group" :class="{ current: route.path.startsWith('/system') }" :aria-expanded="systemOpen" @click="systemOpen = !systemOpen">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4.5v2m0 11v2m7.5-7.5h-2m-11 0h-2m12.8-5.3-1.4 1.4m-7.8 7.8-1.4 1.4m10.6 0-1.4-1.4M8.1 8.1 6.7 6.7M12 15.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" /></svg>
              <span>系统管理</span>
              <span class="chevron" aria-hidden="true">{{ systemOpen ? '−' : '+' }}</span>
            </button>
            <div v-if="systemOpen" class="sub">
              <NavItem :to="{ name: 'users' }" :current="route.name === 'users'">用户管理</NavItem>
              <NavItem :to="{ name: 'templates' }" :current="route.name === 'templates' || route.name === 'template'">模板管理</NavItem>
              <NavItem :to="{ name: 'writing-skills' }" :current="route.name === 'writing-skills'">Skill 管理</NavItem>
            </div>
          </div>
        </nav>
      </aside>
      <main class="content"><router-view /></main>
    </div>
  </div>
</template>

<style scoped>
.app { display:flex; flex-direction:column; min-width:0; min-height:100vh; background:var(--desk); color:var(--ink); }
.header { display:flex; align-items:center; height:calc(var(--space-7) + var(--space-3)); padding:0 var(--space-6); background:var(--sheet); border-bottom:1px solid var(--rule); }
.brand { display:inline-flex; align-items:center; gap:var(--space-2); color:var(--ink); font-size:var(--text-md); font-weight:700; text-decoration:none; white-space:nowrap; }
.logo { width:calc(var(--space-4) + var(--space-2)); height:calc(var(--space-4) + var(--space-2)); color:var(--stamp); }
.user { display:flex; align-items:center; gap:var(--space-4); margin-left:auto; color:var(--ink-secondary); font-size:var(--text-sm); }
.exit { padding:var(--space-2) var(--space-3); border:1px solid var(--rule-strong); border-radius:var(--radius-control); background:var(--sheet); color:var(--ink); cursor:pointer; }
.exit:hover { background:var(--desk); }
.body { display:flex; flex:1; min-height:0; }
.sider { width:calc(var(--space-7) * 4 + var(--space-4)); flex:none; border-right:1px solid var(--rule); background:var(--sheet); }
.menu { display:flex; flex-direction:column; height:100%; padding:var(--space-5) var(--space-3) var(--space-4); }
.menu :deep(a),.group { display:flex; align-items:center; gap:var(--space-3); width:100%; min-height:calc(var(--space-6) + var(--space-2)); padding:0 var(--space-3); border:0; border-radius:var(--radius-control); background:transparent; color:var(--ink-secondary); font-size:var(--text-sm); text-decoration:none; text-align:left; cursor:pointer; }
.menu :deep(a:hover),.group:hover { background:var(--desk); color:var(--ink); }
.menu :deep(a.current),.group.current { background:var(--stamp-wash); color:var(--stamp); font-weight:600; }
.menu svg { width:calc(var(--space-4) + var(--space-1)); height:calc(var(--space-4) + var(--space-1)); flex:none; }
.system-menu { margin-top:var(--space-1); }
.chevron { margin-left:auto; font-size:var(--text-md); }
.sub { padding-left:var(--space-5); }
.sub :deep(a) { min-height:var(--space-6); }
.content { display:flex; flex:1; flex-direction:column; min-width:0; min-height:0; padding:var(--space-5); overflow:auto; }
.brand:focus-visible,.exit:focus-visible,.group:focus-visible,.menu :deep(a:focus-visible) { outline:2px solid var(--stamp); outline-offset:2px; }
</style>
