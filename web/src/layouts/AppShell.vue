<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import NavItem from "../components/NavItem.vue";
import StatePanel from "../components/StatePanel.vue";
import { hasPermission, readSession } from "../session";
import { ApiError } from "../api/client";
import { getProject } from "../api/projects";
import { objectCrumb, projectCrumb, rememberProject } from "../crumbs";
import { flashMessage } from "../flash";
import { NAV_ITEMS, navVisible } from "../access";

const route = useRoute();
const router = useRouter();
const session = computed(() => readSession());
const displayName = computed(() => session.value?.name || session.value?.staffId || "用户");
const account = computed(() => session.value?.staffId || "");
const department = computed(() => session.value?.department || "");
const initial = computed(() => Array.from(displayName.value)[0] ?? "用");
const can = (permission: string) => hasPermission(session.value, permission);
// 导航显隐与路由权限共用 access.ts 的一张表（09 审查 M1/M2/S6）。
const visibleNav = computed(() => new Set(NAV_ITEMS.filter((item) => navVisible(item, session.value?.permissions ?? [])).map((item) => item.route)));
const show = (routeName: string) => visibleNav.value.has(routeName);
const showSystem = computed(() => NAV_ITEMS.some((item) => item.group === "system" && show(item.route)));

const SYSTEM_KEY = "doc-workbench.nav.system-open";
const COLLAPSE_KEY = "doc-workbench.nav.collapsed";
function readFlag(key: string, fallback: boolean) {
  try {
    const value = localStorage.getItem(key);
    return value === null ? fallback : value === "1";
  } catch {
    return fallback;
  }
}
function writeFlag(key: string, value: boolean) {
  try { localStorage.setItem(key, value ? "1" : "0"); } catch { /* 本地存储不可用时只影响偏好记忆。 */ }
}

const systemOpen = ref(readFlag(SYSTEM_KEY, true) || route.path.startsWith("/system"));
// 没有本地偏好时，窄于 1440 的窗口默认收起为图标栏（02 评审 §5.4，08 验收 S10）；用户手动切换后以偏好为准。
const userCollapsed = ref(readFlag(COLLAPSE_KEY, typeof window !== "undefined" && window.innerWidth < 1440));
// 文档编辑工作台自动收起为图标栏，为正文腾出空间（02 评审 3.1 / 5.4）。
const isWorkbench = computed(() => route.name === "document" || route.name === "template-editor");
const collapsed = computed(() => userCollapsed.value || isWorkbench.value);
const loggingOut = ref(false);
const logoutError = ref("");
const menuOpen = ref(false);
const userMenu = ref<HTMLElement | null>(null);
const userButton = ref<HTMLButtonElement | null>(null);
const menuPop = ref<HTMLElement | null>(null);

// 用户菜单键盘行为（09 审查 S4）：打开时焦点进第一项，上下/Home/End 移动，Esc 关闭并回到触发按钮，Tab 离开即关闭。
function menuItems(): HTMLButtonElement[] {
  return Array.from(menuPop.value?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)') ?? []);
}
async function openMenu(focus: "first" | "last" = "first") {
  menuOpen.value = true;
  await nextTick();
  const items = menuItems();
  (focus === "first" ? items[0] : items[items.length - 1])?.focus();
}
function closeMenu(returnFocus = true) {
  menuOpen.value = false;
  if (returnFocus) userButton.value?.focus();
}
function toggleMenu() {
  if (menuOpen.value) closeMenu(false);
  else void openMenu();
}
function onButtonKeydown(event: KeyboardEvent) {
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    void openMenu(event.key === "ArrowDown" ? "first" : "last");
  }
}
function onMenuKeydown(event: KeyboardEvent) {
  const items = menuItems();
  const index = items.indexOf(document.activeElement as HTMLButtonElement);
  let next = -1;
  if (event.key === "ArrowDown") next = (index + 1) % items.length;
  else if (event.key === "ArrowUp") next = (index - 1 + items.length) % items.length;
  else if (event.key === "Home") next = 0;
  else if (event.key === "End") next = items.length - 1;
  else if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closeMenu(); return; }
  else if (event.key === "Tab") { closeMenu(false); return; }
  if (next >= 0 && items.length) { event.preventDefault(); items[next].focus(); }
}

watch(() => route.path, (path) => { if (path.startsWith("/system")) systemOpen.value = true; menuOpen.value = false; });
watch(systemOpen, (value) => writeFlag(SYSTEM_KEY, value));

function toggleCollapsed() {
  userCollapsed.value = !collapsed.value;
  writeFlag(COLLAPSE_KEY, userCollapsed.value);
}

// 路由 meta.permission：无权限时显示单一的无权限页，不再出现多处报错和“重试”（02 评审 P1-7）。
const pageTitle = computed(() => (typeof route.meta.title === "string" ? route.meta.title : ""));
const denied = computed(() => typeof route.meta.permission === "string" && !can(route.meta.permission));

const projectRoutes = ["project", "project-documents", "document"];
const isProjectRoute = computed(() => projectRoutes.includes(String(route.name)));
const projectsCurrent = computed(() => route.name === "projects" || isProjectRoute.value);
let crumbRequest = 0;
watch(() => [route.name, route.params.id] as const, async ([, id]) => {
  if (!isProjectRoute.value || typeof id !== "string" || projectCrumb.value?.id === id) return;
  const request = ++crumbRequest;
  try {
    const project = await getProject(id);
    if (request === crumbRequest) rememberProject(project);
  } catch {
    /* 面包屑只是辅助信息，读取失败时显示“项目”。 */
  }
}, { immediate: true });

// 详情页对象名：模板、知识库、测试任务由各自页面写入；文档名由 api/documents 的 getDocument 在工作台读取文档时写入。
const objectKey = computed(() => {
  const name = String(route.name ?? "");
  if (name === "template" || name === "template-editor") return `template:${String(route.params.id)}`;
  if (name === "knowledge-base") return `knowledge:${String(route.params.id)}`;
  if (name === "document") return `document:${String(route.params.documentId)}`;
  if (name === "test-cases" && typeof route.query.task === "string" && route.query.task) return `task:${route.query.task}`;
  return "";
});
const objectName = (fallback: string) => (objectKey.value && objectCrumb.value?.key === objectKey.value ? objectCrumb.value.label : fallback);

interface Crumb { label: string; to?: object }
const crumbs = computed<Crumb[]>(() => {
  const name = String(route.name ?? "");
  const title = typeof route.meta.title === "string" ? route.meta.title : "";
  if (isProjectRoute.value) {
    const id = String(route.params.id);
    // 项目名未知（加载中或不存在）时省略中间一级，不显示“项目 › 项目”（08 复验 N-S4）。
    const projectName = projectCrumb.value?.id === id ? projectCrumb.value.name : "";
    const list: Crumb[] = [{ label: "项目", to: { name: "projects" } }];
    if (projectName) list.push({ label: projectName, to: { name: "project", params: { id } } });
    if (name === "project") list.push({ label: "项目概述" });
    else list.push({ label: "文档编写", to: name === "document" ? { name: "project-documents", params: { id } } : undefined });
    if (name === "document") list.push({ label: objectName("文档") });
    return list;
  }
  if (route.path.startsWith("/system")) {
    const list: Crumb[] = [{ label: "系统管理" }];
    if (name === "template") list.push({ label: "模板管理", to: { name: "templates" } }, { label: objectName("模板详情") });
    else if (name === "template-editor") list.push({ label: "模板管理", to: { name: "templates" } }, { label: objectName("模板详情"), to: { name: "template", params: { id: String(route.params.id) } } }, { label: title });
    else list.push({ label: title });
    return list;
  }
  if (name === "knowledge-base") return [{ label: "知识库", to: { name: "knowledge" } }, { label: objectName("知识库详情") }];
  if (name === "test-cases" && objectKey.value) return [{ label: title || "测试用例管理", to: { name: "test-cases" } }, { label: objectName("测试任务") }];
  return [{ label: title || "文档工作室" }];
});

function onDocumentClick(event: MouseEvent) {
  if (menuOpen.value && userMenu.value && !userMenu.value.contains(event.target as Node)) menuOpen.value = false;
}
function onKeydown(event: KeyboardEvent) {
  if (event.key === "Escape" && menuOpen.value) closeMenu();
}
onMounted(() => { document.addEventListener("click", onDocumentClick); document.addEventListener("keydown", onKeydown); });
onBeforeUnmount(() => { document.removeEventListener("click", onDocumentClick); document.removeEventListener("keydown", onKeydown); });

function changePassword() {
  menuOpen.value = false;
  void router.push({ name: "change-password", query: { redirect: route.fullPath } });
}

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
  <div class="app" :class="{ collapsed }">
    <header class="topbar">
      <router-link class="brand" :to="{ name: 'projects' }" aria-label="返回项目列表">
        <span class="logo" aria-hidden="true">
          <svg viewBox="0 0 24 24"><path d="M7 3.5h7l4 4V20.5H7zM14 3.5V8h4M10 12h5M10 15.5h5" /></svg>
        </span>
        <span v-if="!collapsed" class="brand-name">文档工作室</span>
      </router-link>
      <nav class="crumbs" aria-label="当前位置">
        <template v-for="(item, index) in crumbs" :key="index">
          <span v-if="index > 0" class="crumb-sep" aria-hidden="true">›</span>
          <router-link v-if="item.to && index < crumbs.length - 1" class="crumb" :to="item.to" :title="item.label">{{ item.label }}</router-link>
          <span v-else class="crumb" :class="{ last: index === crumbs.length - 1 }" :title="item.label" :aria-current="index === crumbs.length - 1 ? 'page' : undefined">{{ item.label }}</span>
        </template>
      </nav>
      <span v-if="logoutError" class="logout-error" role="alert">{{ logoutError }}</span>
      <div ref="userMenu" class="user-menu">
        <button ref="userButton" type="button" class="user" :title="account ? `账号：${account}` : undefined" :aria-expanded="menuOpen" aria-haspopup="menu" aria-controls="user-menu-pop" @click="toggleMenu" @keydown="onButtonKeydown">
          <span class="avatar" aria-hidden="true">{{ initial }}</span>
          <span class="user-meta"><span class="user-name">{{ displayName }}</span><small v-if="department">{{ department }}</small></span>
          <svg class="chevron" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 6l4 4 4-4" /></svg>
        </button>
        <div v-if="menuOpen" id="user-menu-pop" ref="menuPop" class="menu-pop" role="menu" aria-label="用户菜单" @keydown="onMenuKeydown">
          <div class="menu-who"><strong>{{ displayName }}</strong><span>{{ account }}</span></div>
          <button type="button" role="menuitem" tabindex="-1" @click="changePassword">修改密码</button>
          <button type="button" role="menuitem" tabindex="-1" class="danger" :disabled="loggingOut" @click="logout">{{ loggingOut ? "正在退出…" : "退出登录" }}</button>
        </div>
      </div>
    </header>
    <div class="body">
      <aside class="sidenav">
        <nav class="menu" aria-label="主导航">
          <NavItem :to="{ name: 'projects' }" :current="projectsCurrent" label="项目">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3.5 6.5h6l2 2h9v10h-17z" /></svg>
            <span class="nav-label">项目</span>
          </NavItem>
          <NavItem v-if="show('test-cases')" :to="{ name: 'test-cases' }" :current="route.name === 'test-cases'" label="测试用例管理">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 5H6v15h12V5h-3M9 3.5h6V7H9zM9 12.5l2 2 4-4M9 17h6" /></svg>
            <span class="nav-label">测试用例管理</span>
          </NavItem>
          <NavItem v-if="show('knowledge')" :to="{ name: 'knowledge' }" :current="route.name === 'knowledge' || route.name === 'knowledge-base'" label="知识库">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4.5 5.5c2.5-1 5-1 7.5.5v13c-2.5-1.5-5-1.5-7.5-.5zM19.5 5.5c-2.5-1-5-1-7.5.5v13c2.5-1.5 5-1.5 7.5-.5z" /></svg>
            <span class="nav-label">知识库</span>
          </NavItem>
          <!-- 系统管理始终排在业务导航之后，是导航列表的最后一组；包含用户、模板及 Skill 管理。 -->
          <div v-if="showSystem" class="system-menu">
            <button v-if="!collapsed" type="button" class="group" :aria-expanded="systemOpen" @click="systemOpen = !systemOpen">
              <span>系统管理</span>
              <svg class="chevron" :class="{ open: systemOpen }" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 6l4 4 4-4" /></svg>
            </button>
            <span v-else class="group-sep" aria-hidden="true"></span>
            <div v-if="systemOpen || collapsed" class="sub">
              <NavItem v-if="show('users')" :to="{ name: 'users' }" :current="route.name === 'users'" label="用户管理">
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9.5 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM3.5 20c.5-3.5 3-5.5 6-5.5s5.5 2 6 5.5M16 4.5a3.5 3.5 0 0 1 0 6.5M18 14.5c1.6.8 2.6 2.8 2.9 5.5" /></svg>
                <span class="nav-label">用户管理</span>
              </NavItem>
              <NavItem v-if="show('templates')" :to="{ name: 'templates' }" :current="route.name === 'templates' || route.name === 'template' || route.name === 'template-editor'" label="模板管理">
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3.5h8l4 4v13H6zM14 3.5V8h4M9 12h6M9 15.5h6" /></svg>
                <span class="nav-label">模板管理</span>
              </NavItem>
              <NavItem v-if="show('writing-skills')" :to="{ name: 'writing-skills' }" :current="route.name === 'writing-skills'" label="Skill 管理">
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14.5 4.5l5 5-9.5 9.5H5v-5zM12.5 6.5l5 5" /></svg>
                <span class="nav-label">Skill 管理</span>
              </NavItem>
            </div>
          </div>
        </nav>
        <button type="button" class="collapse" :aria-label="collapsed ? '展开导航' : '收起导航'" :title="isWorkbench ? '文档编辑时导航自动收起' : (collapsed ? '展开导航' : '收起导航')" :disabled="isWorkbench" @click="toggleCollapsed">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path :d="collapsed ? 'M10 7l5 5-5 5' : 'M14 7l-5 5 5 5'" /></svg>
          <span v-if="!collapsed" class="nav-label">收起导航</span>
        </button>
      </aside>
      <div class="toast-region" role="status" aria-live="polite"><p v-if="flashMessage" class="toast"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8.5l3 3 6-7" /></svg>{{ flashMessage }}</p></div>
      <main class="content" :class="{ 'is-document': isWorkbench }">
        <section v-if="denied" class="view fill">
          <header class="page-head"><div><h1>{{ pageTitle }}</h1></div></header>
          <div class="card"><StatePanel kind="denied" :title="`你没有${pageTitle}权限`" text="如需开通请联系系统管理员。" /></div>
        </section>
        <router-view v-else />
      </main>
    </div>
  </div>
</template>

<style scoped>
.app { display:flex; flex-direction:column; min-width:0; min-height:100vh; background:var(--bg-page); color:var(--text-primary); }
.topbar { position:sticky; top:0; z-index:10; display:flex; align-items:center; gap:var(--space-4); height:var(--topbar-h); padding:0 var(--space-4); background:var(--bg-surface); border-bottom:1px solid var(--border-default); }
.brand { display:flex; flex:none; align-items:center; gap:10px; width:calc(var(--sidenav-w) - var(--space-4)); color:var(--text-primary); font-size:var(--text-md); font-weight:var(--weight-semibold); letter-spacing:.02em; text-decoration:none; white-space:nowrap; }
.collapsed .brand { width:calc(var(--sidenav-w-collapsed) - var(--space-4)); }
.logo { display:inline-flex; align-items:center; justify-content:center; width:24px; height:24px; border-radius:var(--radius-control); background:var(--brand-600); color:var(--text-on-brand); }
.logo svg { width:16px; height:16px; fill:none; stroke:currentColor; stroke-width:1.8; stroke-linecap:round; stroke-linejoin:round; }
.crumbs { display:flex; flex:1; align-items:center; gap:6px; min-width:0; color:var(--text-secondary); font-size:var(--text-sm); }
.crumb { min-width:0; max-width:calc(var(--space-7) * 8); overflow:hidden; color:var(--text-secondary); text-decoration:none; text-overflow:ellipsis; white-space:nowrap; }
a.crumb:hover { color:var(--text-primary); text-decoration:underline; text-underline-offset:3px; }
.crumb.last { color:var(--text-primary); font-weight:var(--weight-medium); }
.crumb-sep { flex:none; color:var(--text-disabled); }
.logout-error { color:var(--danger-600); font-size:var(--text-sm); }
.user-menu { position:relative; flex:none; }
.user { display:flex; align-items:center; gap:var(--space-2); height:36px; padding:0 var(--space-2) 0 var(--space-1); border:0; border-radius:var(--radius-control); background:transparent; color:var(--text-primary); font-size:var(--text-sm); cursor:pointer; }
.user:hover,.user[aria-expanded="true"] { background:var(--bg-hover); }
.user-meta { display:grid; text-align:left; line-height:16px; }
.user-name { font-weight:var(--weight-medium); }
.user-meta small { color:var(--text-tertiary); font-size:var(--text-xs); }
.chevron { width:16px; height:16px; fill:none; stroke:var(--text-tertiary); stroke-width:1.6; stroke-linecap:round; stroke-linejoin:round; transition:transform var(--duration) var(--ease-out); }
.menu-pop { position:absolute; top:calc(100% + var(--space-1)); right:0; z-index:20; display:grid; min-width:calc(var(--space-7) * 4); padding:var(--space-1); background:var(--bg-surface); border:1px solid var(--border-default); border-radius:var(--radius-sheet); box-shadow:var(--shadow-2); }
.menu-who { display:grid; gap:2px; padding:var(--space-2) var(--space-3); margin-bottom:var(--space-1); border-bottom:1px solid var(--border-subtle); font-size:var(--text-sm); }
.menu-who span { color:var(--text-tertiary); font-size:var(--text-xs); }
.menu-pop button { display:flex; align-items:center; height:var(--control-h); padding:0 var(--space-3); border:0; border-radius:var(--radius-control); background:transparent; color:var(--text-primary); font-size:var(--text-sm); text-align:left; cursor:pointer; }
.menu-pop button:hover:not(:disabled) { background:var(--bg-hover); }
.menu-pop button.danger { color:var(--danger-600); }
.menu-pop button:disabled { opacity:.45; cursor:not-allowed; }
.body { display:flex; flex:1; min-height:0; }
.sidenav { position:sticky; top:var(--topbar-h); display:flex; flex:none; flex-direction:column; width:var(--sidenav-w); height:calc(100vh - var(--topbar-h)); background:var(--bg-surface); border-right:1px solid var(--border-default); transition:width var(--duration) var(--ease-out); }
.collapsed .sidenav { width:var(--sidenav-w-collapsed); }
.menu { display:flex; flex:1; flex-direction:column; gap:2px; min-height:0; padding:var(--space-3) var(--space-2); overflow-y:auto; }
.menu :deep(a) { display:flex; align-items:center; gap:10px; height:36px; padding:0 10px; border-radius:var(--radius-control); color:var(--text-secondary); font-size:var(--text-md); text-decoration:none; white-space:nowrap; }
.menu :deep(a:hover) { background:var(--bg-hover); color:var(--text-primary); }
.menu :deep(a.current) { background:var(--bg-active); color:var(--brand-600); font-weight:var(--weight-medium); }
.menu :deep(svg),.collapse svg { width:16px; height:16px; flex:none; fill:none; stroke:currentColor; stroke-width:1.6; stroke-linecap:round; stroke-linejoin:round; }
.collapsed .menu :deep(a) { justify-content:center; padding:0; }
.collapsed .nav-label { display:none; }
.system-menu { display:grid; gap:2px; margin-top:var(--space-3); padding-top:var(--space-3); border-top:1px solid var(--border-subtle); }
.group { display:flex; align-items:center; justify-content:space-between; height:28px; padding:0 10px; border:0; border-radius:var(--radius-control); background:transparent; color:var(--text-tertiary); font-size:var(--text-xs); letter-spacing:.04em; text-align:left; cursor:pointer; }
.group:hover { color:var(--text-secondary); }
.group .chevron { transform:rotate(-90deg); }
.group .chevron.open { transform:none; }
.group-sep { display:none; }
.sub { display:grid; gap:2px; }
.sub :deep(a) { height:34px; font-size:var(--text-sm); }
.collapse { display:flex; align-items:center; gap:10px; height:36px; margin:var(--space-2); padding:0 10px; border:0; border-radius:var(--radius-control); background:transparent; color:var(--text-tertiary); font-size:var(--text-sm); cursor:pointer; }
.collapsed .collapse { justify-content:center; padding:0; }
.collapse:hover:not(:disabled) { background:var(--bg-hover); color:var(--text-primary); }
.collapse:disabled { cursor:default; opacity:.6; }
.toast-region { position:fixed; top:calc(var(--topbar-h) + var(--space-3)); left:50%; z-index:30; transform:translateX(-50%); }
.toast { display:flex; align-items:center; gap:var(--space-2); margin:0; padding:var(--space-2) var(--space-4); border:1px solid var(--success-200); border-radius:var(--radius-sheet); background:var(--success-50); color:var(--success-600); box-shadow:var(--shadow-2); font-size:var(--text-sm); }
.toast svg { width:16px; height:16px; fill:none; stroke:currentColor; stroke-width:1.8; stroke-linecap:round; stroke-linejoin:round; }
.content { display:flex; flex:1; flex-direction:column; min-width:0; min-height:0; padding:var(--content-pad); }
/* 工作台重塑：文档编辑工作台贴满内容区（.ws 高度 = 100vh - 顶栏），不留内边距。 */
.content.is-document { padding:0; overflow:hidden; }
.brand:focus-visible,.user:focus-visible,.group:focus-visible,.collapse:focus-visible,.menu-pop button:focus-visible,.crumb:focus-visible,.menu :deep(a:focus-visible) { outline:none; box-shadow:var(--focus-shadow); }
</style>
