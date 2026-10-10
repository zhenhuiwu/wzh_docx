import { createRouter, createWebHistory } from "vue-router";
import { defineAsyncComponent, h } from "vue";
import AppShell from "./layouts/AppShell.vue";
import { onPasswordChangeRequired } from "./api/client";
import { endSession, ensureSession, loginDestination, markPasswordChangeRequired } from "./session";
import DocumentWorkbenchView from "./views/DocumentWorkbenchView.vue";
import ProjectDetailView from "./views/ProjectDetailView.vue";
import ProjectDocumentsView from "./views/ProjectDocumentsView.vue";
import ProjectsView from "./views/ProjectsView.vue";
import TemplateDetailView from "./views/TemplateDetailView.vue";
import TemplatesView from "./views/TemplatesView.vue";
import SignIn from "./views/SignIn.vue";
import ChangePasswordView from "./views/ChangePasswordView.vue";
import UsersView from "./views/UsersView.vue";
import KnowledgeView from "./views/KnowledgeView.vue";
import KnowledgeDetailView from "./views/KnowledgeDetailView.vue";
import NotFoundView from "./views/NotFoundView.vue";
import WritingSkillsView from "./views/WritingSkillsView.vue";

const TestCasesView = defineAsyncComponent({
  loader: () => import("./views/TestCasesView.vue"),
  delay: 0,
  loadingComponent: { render: () => h("section", { class: "view fill" }, [
    h("header", { class: "page-head" }, h("div", h("h1", "测试用例管理"))),
    h("div", { class: "card" }, h("div", { class: "state-panel", role: "status" }, [h("span", { class: "spinner", "aria-hidden": "true" }), "正在加载…"])),
  ]) },
  errorComponent: { render: () => h("section", { class: "view fill" }, [
    h("header", { class: "page-head" }, h("div", h("h1", "测试用例管理"))),
    h("div", { class: "card" }, h("div", { class: "state-panel", role: "alert" }, [h("strong", "页面加载失败"),
      h("button", { type: "button", class: "ghost", onClick: () => window.location.reload() }, "重新加载"),
    ])),
  ]) },
});

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: "/login",
      name: "login",
      component: SignIn,
      meta: { public: true, title: "登录" },
    },
    {
      path: "/change-password",
      name: "change-password",
      component: ChangePasswordView,
      meta: { title: "修改密码" },
    },
    {
      path: "/",
      component: AppShell,
      children: [
        { path: "", redirect: { name: "projects" } },
        { path: "home", redirect: { name: "projects" } },
        {
          path: "projects",
          name: "projects",
          component: ProjectsView,
          meta: { title: "项目" },
        },
        {
          path: "projects/:id",
          name: "project",
          component: ProjectDetailView,
          meta: { title: "项目概述" },
        },
        {
          path: "projects/:id/materials",
          name: "project-materials",
          redirect: (to) => ({ name: "project", params: { id: to.params.id } }),
        },
        {
          path: "projects/:id/documents",
          name: "project-documents",
          component: ProjectDocumentsView,
          meta: { title: "文档" },
        },
        {
          path: "projects/:id/documents/:documentId",
          name: "document",
          component: DocumentWorkbenchView,
          meta: { title: "文档" },
        },
        {
          path: "projects/:id/reviews",
          name: "project-reviews",
          redirect: (to) => ({ name: "project", params: { id: to.params.id } }),
        },
        // 知识库：页面与接口均已实现（DEV-09），此前路由被整体重定向到项目列表，导致入口不可达（02 评审 P1-6）。
        {
          path: "knowledge",
          name: "knowledge",
          component: KnowledgeView,
          meta: { title: "知识库", permission: "knowledge.read" },
        },
        {
          path: "knowledge/:id",
          name: "knowledge-base",
          component: KnowledgeDetailView,
          meta: { title: "知识库详情", permission: "knowledge.read" },
        },
        { path: "assistant", redirect: { name: "projects" } },
        {
          path: "test-cases",
          name: "test-cases",
          component: { render: () => h(TestCasesView) },
          meta: { title: "测试用例管理" },
        },
        {
          path: "system/templates",
          name: "templates",
          component: TemplatesView,
          meta: { title: "模板管理" },
        },
        {
          path: "system/templates/:id",
          name: "template",
          component: TemplateDetailView,
          meta: { title: "模板详情" },
        },
        {
          path: "system/users",
          name: "users",
          component: UsersView,
          meta: { title: "用户管理", permission: "users.read" },
        },
        {
          path: "system/skills",
          name: "writing-skills",
          component: WritingSkillsView,
          meta: { title: "Skill 管理" },
        },
        { path: "users", redirect: { name: "users" } },
        // 未知地址显示 404，不再静默跳回项目列表（02 评审 P1-6）。
        { path: ":pathMatch(.*)*", name: "not-found", component: NotFoundView, meta: { title: "页面不存在" } },
      ],
    },
  ],
});

router.beforeEach(async (to) => {
  if (to.name === "login" && to.query.logout === "1") {
    await endSession();
    return true;
  }
  if (to.meta.public) {
    if (to.name !== "login") return true;
    const session = await ensureSession();
    return session ? loginDestination(to.query.redirect) : true;
  }
  const session = await ensureSession();
  if (!session) return { name: "login", query: { redirect: to.fullPath } };
  if (session.mustChangePassword && to.name !== "change-password") {
    return { name: "change-password", query: { redirect: to.fullPath } };
  }
  return true;
});

onPasswordChangeRequired(() => {
  markPasswordChangeRequired();
  const current = router.currentRoute.value;
  if (current.name === "change-password" || current.meta.public) return;
  void router.replace({ name: "change-password", query: { redirect: current.fullPath } });
});

router.afterEach((to, _from, failure) => {
  if (failure) return;
  if (to.name === "login" && to.query.logout === "1") void router.replace({ name: "login" });
  const page = typeof to.meta.title === "string" ? to.meta.title : "文档工作室";
  document.title = `${page} · 文档工作室`;
});
