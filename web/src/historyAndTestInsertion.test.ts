import { compileScript, parse } from "@vue/compiler-sfc";
import ts from "typescript";
import * as vue from "vue";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, isMissingResource } from "./api/client";
import { blockDiff } from "./textDiff";
import history from "./components/DocumentHistory.vue?raw";
import testInsertion from "./views/TestCasesView.vue?raw";
import projectDetail from "./views/ProjectDetailView.vue?raw";
import knowledgeDetail from "./views/KnowledgeDetailView.vue?raw";

// 执行组件的真实 setup；这里只测状态与网络竞态，不模拟 DOM 或替代界面截图验收。
function setup(source: string, documents: Record<string, unknown>, tasks: Record<string, unknown> = {}, extraImports: Record<string, unknown> = {}) {
  const script = compileScript(parse(source).descriptor, { id: "regression" }).content;
  const code = ts.transpileModule(script, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const imports: Record<string, unknown> = {
    vue: { ...vue, onMounted: () => {}, onUnmounted: () => {}, onBeforeUnmount: () => {} },
    "vue-router": { useRoute: () => ({ query: {}, params: {} }), useRouter: () => ({}), onBeforeRouteLeave: () => {}, onBeforeRouteUpdate: () => {} },
    "../session": { hasPermission: () => false, readSession: () => ({ permissions: [] }) },
    "../api/documents": documents,
    "../api/testTasks": tasks,
    "../api/client": { ApiError, isMissingResource },
    "../fieldErrors": { useFieldErrors: () => ({ errors: vue.ref({}), invalid: () => ({}), clear: () => {}, report: async () => false }) },
    "../textDiff": { blockDiff },
    ...extraImports,
  };
  const exports: { default?: { setup: (props: unknown, context: unknown) => Record<string, any> } } = {};
  new Function("exports", "require", code)(exports, (name: string) => imports[name] ?? {});
  return exports.default!.setup({ documentId: "document", revision: 5 }, { expose: () => {}, emit: () => {} });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

const version = (revision: number, markdownContent = `正文 ${revision}`) => ({ revision, markdownContent, formatTemplate: {} });
const document = (id: string, canEdit: boolean) => ({ id, name: id, revision: 2, markdownContent: "正文", permissions: { canEdit } });
afterEach(() => vi.unstubAllGlobals());

describe("历史版本比较的请求与错误状态", () => {
  it("旧请求没有结束时仍能读取新版本，旧请求也不能提前清除新请求的加载状态", async () => {
    const old = deferred<ReturnType<typeof version>>();
    const next = deferred<ReturnType<typeof version>>();
    const getDocumentVersion = vi.fn((_id, revision) => revision === 2 ? old.promise : revision === 4 ? next.promise : Promise.resolve(version(revision)));
    const state = setup(history, { getDocumentVersion });
    state.view.value = "previous";
    state.detail.value = version(3);
    const first = state.loadPrevious();
    await state.select(version(5));
    expect(getDocumentVersion).toHaveBeenCalledWith("document", 4, false);
    old.resolve(version(2));
    await first;
    expect(state.previousLoading.value).toBe(true);
    expect(state.previous.value).toBeNull();
    expect(state.comparisonReady.value).toBe(false);
    next.resolve(version(4));
    await next.promise;
    await vue.nextTick();
    expect(state.previous.value).toEqual({ revision: 5, markdown: "正文 4" });
    expect(state.previousLoading.value).toBe(false);
    expect(state.comparisonReady.value).toBe(true);
  });

  it("较晚返回的旧错误不能覆盖新版本的比较结果", async () => {
    const old = deferred<ReturnType<typeof version>>();
    const getDocumentVersion = vi.fn((_id, revision) => revision === 2 ? old.promise : Promise.resolve(version(revision)));
    const state = setup(history, { getDocumentVersion });
    state.view.value = "previous";
    state.detail.value = version(3);
    const first = state.loadPrevious();
    await state.select(version(5));
    await vue.nextTick();
    old.reject(new Error("旧请求断网"));
    await first;
    expect(state.previous.value).toEqual({ revision: 5, markdown: "正文 4" });
    expect(state.previousError.value).toBe("");
    expect(state.comparisonReady.value).toBe(true);
  });

  it("读取失败不会伪装成无历史或相同，重试后正常比较", async () => {
    const getDocumentVersion = vi.fn().mockRejectedValueOnce(new Error("暂时断网")).mockResolvedValueOnce(version(2, "正文 3"));
    const state = setup(history, { getDocumentVersion });
    state.view.value = "previous";
    state.detail.value = version(3);
    await state.loadPrevious();
    expect(state.previousError.value).toBe("暂时断网");
    expect(state.previousMissing.value).toBe(false);
    expect(state.comparisonReady.value).toBe(false);
    await state.loadPrevious();
    expect(state.previousError.value).toBe("");
    expect(state.comparisonReady.value).toBe(true);
    expect(state.diffChanged.value).toBe(0);
  });

  it("只有首版或明确 404 才显示没有更早版本", async () => {
    const getDocumentVersion = vi.fn().mockRejectedValue(new ApiError(404, "NOT_FOUND", "没有这个版本"));
    const state = setup(history, { getDocumentVersion });
    state.detail.value = version(3);
    await state.loadPrevious();
    expect(state.previousMissing.value).toBe(true);
    expect(state.previousError.value).toBe("");
    state.detail.value = version(1);
    await state.loadPrevious();
    expect(getDocumentVersion).toHaveBeenCalledTimes(1);
    expect(state.previousLoading.value).toBe(false);
  });
});

const projectRecord = { id: "project", revision: 2, name: "原项目", description: "", organization: "", department: "", group: "", clientContact: "", status: "not_started", periodStart: null, periodEnd: null };

async function projectState(updateProject = vi.fn()) {
  const state = setup(projectDetail, { listDocuments: vi.fn().mockResolvedValue({ items: [] }) }, {}, {
    "../api/projects": { getProject: vi.fn().mockResolvedValue(projectRecord), updateProject },
    "vue-router": { useRoute: () => ({ params: { id: "project" } }), onBeforeRouteLeave: () => {}, onBeforeRouteUpdate: () => {} },
  });
  await vue.nextTick();
  state.toggleEditing();
  return state;
}

describe("项目概述未保存保护", () => {
  it("未修改或改回原值不确认，离开取消时保留修改", async () => {
    const confirm = vi.fn().mockReturnValue(false);
    vi.stubGlobal("window", { confirm });
    const state = await projectState();
    expect(state.canLeave()).toBe(true);
    state.form.value.department = "新部门";
    expect(state.canLeave()).toBe(false);
    expect(state.editing.value).toBe(true);
    expect(state.form.value.department).toBe("新部门");
    state.form.value.department = "";
    expect(state.canLeave()).toBe(true);
    expect(confirm).toHaveBeenCalledTimes(1);
  });

  it("取消与收起都先确认，拒绝时不丢稿，接受后恢复服务器数据", async () => {
    const confirm = vi.fn().mockReturnValue(false);
    vi.stubGlobal("window", { confirm });
    const state = await projectState();
    state.form.value.name = "修改的项目";
    state.cancelEdit();
    state.toggleEditing();
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(state.editing.value).toBe(true);
    expect(state.form.value.name).toBe("修改的项目");
    confirm.mockReturnValue(true);
    state.toggleEditing();
    expect(state.editing.value).toBe(false);
    expect(state.form.value.name).toBe("原项目");
    state.toggleEditing();
    expect(state.dirty.value).toBe(false);
  });

  it("刷新保护覆盖未保存与保存中，保存完成后解除", async () => {
    vi.stubGlobal("window", { confirm: vi.fn().mockReturnValue(false) });
    const pending = deferred<typeof projectRecord>();
    const state = await projectState(vi.fn().mockReturnValue(pending.promise));
    const clean = { preventDefault: vi.fn(), returnValue: undefined };
    state.beforeUnload(clean);
    expect(clean.preventDefault).not.toHaveBeenCalled();
    state.form.value.name = "保存后的项目";
    const dirty = { preventDefault: vi.fn(), returnValue: undefined };
    state.beforeUnload(dirty);
    expect(dirty.preventDefault).toHaveBeenCalledOnce();
    const saving = state.saveInfo();
    await vue.nextTick();
    expect(state.saving.value).toBe(true);
    expect(state.canLeave()).toBe(false);
    state.cancelEdit();
    expect(state.editing.value).toBe(true);
    const busy = { preventDefault: vi.fn(), returnValue: undefined };
    state.beforeUnload(busy);
    expect(busy.preventDefault).toHaveBeenCalledOnce();
    pending.resolve({ ...projectRecord, name: "保存后的项目", revision: 3 });
    await saving;
    expect(state.saving.value).toBe(false);
    expect(state.dirty.value).toBe(false);
    expect(state.canLeave()).toBe(true);
  });

  it("保存失败仍保留项目编辑和离开提醒", async () => {
    vi.stubGlobal("window", { confirm: vi.fn().mockReturnValue(false) });
    const state = await projectState(vi.fn().mockRejectedValue(new ApiError(500, "FAILED", "保存失败")));
    state.form.value.organization = "新的甲方";
    await state.saveInfo();
    expect(state.formError.value).toBe("保存失败");
    expect(state.dirty.value).toBe(true);
    expect(state.editing.value).toBe(true);
    expect(state.canLeave()).toBe(false);
  });
});

describe("知识库路由与请求隔离", () => {
  it("切换时清除旧资料，旧响应不能覆盖新知识库", async () => {
    const route = vue.reactive({ params: { id: "old" } });
    const oldBase = deferred<{ id: string; name: string }>();
    const oldSources = deferred<unknown[]>();
    const nextBase = deferred<{ id: string; name: string }>();
    const nextSources = deferred<unknown[]>();
    const state = setup(knowledgeDetail, {}, {}, {
      "vue-router": { useRoute: () => route },
      "../crumbs": { rememberCrumb: vi.fn() },
      "../api/knowledge": {
        getKnowledgeBase: vi.fn((id) => id === "old" ? oldBase.promise : nextBase.promise),
        listSources: vi.fn((id) => id === "old" ? oldSources.promise : nextSources.promise),
      },
    });
    state.base.value = { id: "old", name: "旧库" };
    state.sources.value = [{ name: "旧资料" }];
    state.name.value = "旧库的待上传名称";
    route.params.id = "next";
    await vue.nextTick();
    expect(state.base.value).toBeNull();
    expect(state.sources.value).toEqual([]);
    expect(state.name.value).toBe("");
    nextBase.resolve({ id: "next", name: "新库" });
    nextSources.resolve([{ name: "新资料" }]);
    await vue.nextTick();
    await vue.nextTick();
    expect(state.phase.value).toBe("ready");
    oldBase.resolve({ id: "old", name: "旧库" });
    oldSources.resolve([{ name: "旧资料" }]);
    await vue.nextTick();
    await vue.nextTick();
    expect(state.base.value.name).toBe("新库");
    expect(state.sources.value[0].name).toBe("新资料");
  });

  it("旧请求错误不会把新知识库变成错误页或无权限页", async () => {
    const route = vue.reactive({ params: { id: "old" } });
    const old = deferred<unknown>();
    const state = setup(knowledgeDetail, {}, {}, {
      "vue-router": { useRoute: () => route },
      "../crumbs": { rememberCrumb: vi.fn() },
      "../api/knowledge": { getKnowledgeBase: vi.fn((id) => id === "old" ? old.promise : Promise.resolve({ id, name: "新库" })), listSources: vi.fn().mockResolvedValue([]) },
    });
    route.params.id = "next";
    await vue.nextTick();
    await vue.nextTick();
    old.reject(new ApiError(404, "NOT_FOUND", "旧库被删除"));
    await vue.nextTick();
    await vue.nextTick();
    expect(state.base.value.name).toBe("新库");
    expect(state.phase.value).toBe("ready");
    expect(state.errorText.value).toBe("");
    expect(state.notFound.value).toBe(false);
  });

  it("旧库上传完成仍归属原库，不清空或污染新库输入", async () => {
    const route = vue.reactive({ params: { id: "old" } });
    const upload = deferred<{ id: string }>();
    const createSource = vi.fn().mockResolvedValue({});
    const state = setup(knowledgeDetail, {}, {}, {
      "vue-router": { useRoute: () => route },
      "../crumbs": { rememberCrumb: vi.fn() },
      "../api/files": { uploadFile: vi.fn().mockReturnValue(upload.promise) },
      "../api/knowledge": { getKnowledgeBase: vi.fn((id) => Promise.resolve({ id, name: id })), listSources: vi.fn().mockResolvedValue([]), createSource },
    });
    await vue.nextTick();
    state.name.value = "旧资料";
    state.file.value = new File(["old"], "old.docx");
    const submitting = state.submitFile();
    await vue.nextTick();
    route.params.id = "next";
    await vue.nextTick();
    state.name.value = "新资料名称";
    state.file.value = new File(["next"], "next.docx");
    upload.resolve({ id: "old-file" });
    await submitting;
    expect(createSource).toHaveBeenCalledWith("old", "旧资料", "old-file");
    expect(state.name.value).toBe("新资料名称");
    expect(state.file.value.name).toBe("next.docx");
    expect(state.notice.value).toBe("");
    expect(state.base.value.id).toBe("next");
  });
});

describe("测试结果写入按目标文档权限", () => {
  it("筛掉只读文档仍按原分页读取，不受全局文档权限限制", async () => {
    const listDocuments = vi.fn()
      .mockResolvedValueOnce({ items: [document("readonly", false)], permissions: { canEdit: true }, pageSize: 1, total: 2 })
      .mockResolvedValueOnce({ items: [document("editable", true)], permissions: { canEdit: true }, pageSize: 1, total: 2 });
    const state = setup(testInsertion, { listDocuments });
    state.task.value = { id: "task", projectId: "project" };
    state.writeOpen.value = true;
    await state.loadWriteDocuments();
    expect(listDocuments).toHaveBeenCalledTimes(2);
    expect(state.writeDocuments.value.map((item: { id: string }) => item.id)).toEqual(["editable"]);
    expect(state.writeCanCreateDocument.value).toBe(true);
    expect(state.canEdit.value).toBe(false);
  });

  it("只有查看权限时列表无可写目标，也不会提供新建文档入口", async () => {
    const listDocuments = vi.fn().mockResolvedValue({ items: [document("readonly", false)], permissions: { canEdit: false }, pageSize: 100, total: 1 });
    const state = setup(testInsertion, { listDocuments });
    state.task.value = { id: "task", projectId: "project" };
    state.writeOpen.value = true;
    await state.loadWriteDocuments();
    expect(state.writeDocuments.value).toEqual([]);
    expect(state.writeCanCreateDocument.value).toBe(false);
    expect(state.writePhase.value).toBe("ready");
  });

  it("选择后权限被撤销时重新读取会拒绝该目标，提交不会请求写接口", async () => {
    const getDocument = vi.fn().mockResolvedValue(document("target", false));
    const insertTestResults = vi.fn();
    const state = setup(testInsertion, { getDocument }, { insertTestResults });
    state.writeOpen.value = true;
    state.writeDocumentId.value = "target";
    await state.loadWriteDocument();
    expect(state.writeDocument.value).toBeNull();
    expect(state.writeError.value).toContain("没有编写");
    state.task.value = { id: "task" };
    state.writeRun.value = { id: "run" };
    state.writeDocument.value = document("target", false);
    await state.writeResults();
    expect(insertTestResults).not.toHaveBeenCalled();
  });

  it("项目编写人没有全局权限仍可向可编辑目标写入结果", async () => {
    const getDocument = vi.fn().mockResolvedValue(document("target", true));
    const insertTestResults = vi.fn().mockResolvedValue({ insertedCount: 1 });
    const state = setup(testInsertion, { getDocument }, { insertTestResults });
    state.writeOpen.value = true;
    state.writeDocumentId.value = "target";
    await state.loadWriteDocument();
    state.task.value = { id: "task" };
    state.writeRun.value = { id: "run" };
    state.writeHeading.value = "";
    state.writeCaseIds.value = ["case"];
    await state.writeResults();
    expect(insertTestResults).toHaveBeenCalledWith("task", { runId: "run", documentId: "target", expectedRevision: 2, headingIndex: null, caseIds: ["case"] });
    expect(state.writing.value).toBe(false);
  });
});
