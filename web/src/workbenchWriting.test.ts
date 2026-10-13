import { compileScript, parse } from "@vue/compiler-sfc";
import ts from "typescript";
import * as vue from "vue";
import { afterEach, describe, expect, it, vi } from "vitest";
import workbench from "./views/DocumentWorkbenchView.vue?raw";
import { ApiError } from "./api/client";
import { useGuidanceBrowser } from "./useGuidanceBrowser";

const compiled = ts.transpileModule(compileScript(parse(workbench).descriptor, { id: "writing" }).content,
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;

function setup() {
  vi.stubGlobal("window", { clearInterval: vi.fn(), clearTimeout: vi.fn(), setTimeout: vi.fn() });
  vi.stubGlobal("CSS", { escape: (text: string) => text });
  const insert = vi.fn();
  const query = vi.fn();
  const update = vi.fn();
  const imports: Record<string, unknown> = {
    // 与历史版本的状态测试一样，运行真实 setup；生命周期、浏览器 DOM 由界面验收覆盖。
    vue: { ...vue, watch: () => () => {}, onMounted: () => {}, onUnmounted: () => {} },
    "vue-router": { useRoute: () => ({ params: { id: "project", documentId: "document" } }), useRouter: () => ({}), onBeforeRouteLeave: () => {}, onBeforeRouteUpdate: () => {} },
    "../tabIdentity": { browserTabIdentity: () => ({ onChange: () => () => {} }) },
    "../editorMarkdown": { createTurndown: () => ({}), storedImageRule: () => ({}) },
    "../memberModel": { readonlyText: () => "项目只读，不能修改。" },
    "../frozenConflict": { noteYields: () => false },
    "../api/client": { ApiError },
    "../useGuidanceBrowser": { useGuidanceBrowser },
    "../api/documents": { insertDocumentSection: insert, replaceDocumentSelection: insert, applyDocumentEdits: insert, queryWritingGuidance: query, updateWritingGuidance: update },
  };
  const exports: { default?: { setup: (props: unknown, context: unknown) => Record<string, any> } } = {};
  new Function("exports", "require", compiled)(exports, (name: string) => imports[name] ?? {});
  const state = exports.default!.setup({}, { expose: () => {} });
  state.document.value = { id: "document", revision: 5, permissions: { canEdit: true } };
  state.selectedHeading.value = 0;
  state.aiDraft.value = "生成的正文。";
  state.aiRevision.value = 5;
  state.aiComplete.value = true;
  state.draftEditorVersion.value = state.editorVersion.value;
  return { state, insert, query, update };
}

afterEach(() => vi.unstubAllGlobals());

it("参考资料有未保存修改时，切换视图或面板均保留当前编辑", async () => {
  const { state } = setup();
  const requestClose = vi.fn();
  state.referencesPanel.value = { requestClose };
  state.referencesDirty.value = true;
  state.rightPanel.value = "references";
  state.openPanel("ai");
  await state.switchMode("original");
  expect(requestClose).toHaveBeenCalledTimes(2);
  expect(state.rightPanel.value).toBe("references");
  expect(state.mode.value).toBe("edit");
  expect(state.canStartAi.value).toBe(false);
});

describe("编写进度", () => {
  it("同一模块不重复列出相同操作，下一个模块仍显示自己的步骤", () => {
    const { state } = setup();
    state.aiBusy.value = true;
    state.showAiProgress("module", "正在编写 1/2：甲");
    state.showAiProgress("skills", "正在核对章节参考规则");
    state.showAiProgress("reading", "正在查找编写资料");
    state.showAiProgress("skills", "正在核对章节参考规则");
    expect(state.aiProgress.value).toHaveLength(3);
    expect(state.aiProgressLabel.value).toBe("正在核对章节参考规则");
    state.showAiProgress("module", "正在编写 2/2：乙");
    state.showAiProgress("skills", "正在核对章节参考规则");
    expect(state.aiProgress.value).toHaveLength(5);
  });
});

describe("AI 编写范围与权限", () => {
  it("草稿完成后权限被改为只读，生成和采纳同时禁用，草稿保留", () => {
    const { state } = setup();
    expect(state.canStartAi.value).toBe(true);
    expect(state.canApplyDraft.value).toBe(true);
    state.applyPolledPermissions(state.document.value, { canEdit: false, readonlyReason: "archived" });
    expect(state.canStartAi.value).toBe(false);
    expect(state.canApplyDraft.value).toBe(false);
    expect(state.aiDraft.value).toBe("生成的正文。");
  });

  it.each(["section", "selection", "document"])("%s 模式在只读状态下不会请求写入", async scope => {
    const { state, insert } = setup();
    state.aiScope.value = scope;
    state.readonly.value = true;
    state.readonlyReason.value = "项目只读，不能修改。";
    await state.insertSection("replace");
    expect(insert).not.toHaveBeenCalled();
    expect(state.aiError.value).toBe("项目只读，不能修改。");
  });

  it("章节范围截止到下一个标题，下级章节的冲突不阻塞当前正文编写", () => {
    const { state } = setup();
    const root: any = { querySelectorAll: (selector: string) => selector.startsWith("h1,") ? [parent, child] : [frozen] };
    const frozen = { parentElement: root, tagName: "P", nextElementSibling: null, hasAttribute: () => true };
    const child = { parentElement: root, tagName: "H2", nextElementSibling: frozen, hasAttribute: () => false };
    const body = { parentElement: root, tagName: "P", nextElementSibling: child, hasAttribute: () => false };
    const parent = { parentElement: root, tagName: "H1", nextElementSibling: body, hasAttribute: () => false };
    state.editor.value = vue.markRaw(root);
    state.frozen.value = { child: { key: "child", resolved: false } };
    expect(state.aiTouchesFrozen()).toBe(false);
    state.selectedHeading.value = 1;
    expect(state.aiTouchesFrozen()).toBe(true);
    state.aiScope.value = "document";
    expect(state.aiTouchesFrozen()).toBe(true);
  });
});

describe("当前范围的模板编写建议", () => {
  const item = { id: "rule-a", heading: "功能要求", kind: "guidance", sourceText: "只写功能。", text: "只写功能。", overridden: false };
  const groups = [{ headingIndex: 0, heading: "功能要求", items: [item] }];

  it("编辑后的建议在切换模块时保留，恢复模板不会修改原模板项", () => {
    const { state } = setup();
    state.editGuidance(item, "只写接口。");
    state.aiScope.value = "document";
    expect(state.guidanceText(item)).toBe("只写接口。");
    expect(item.text).toBe("只写功能。");
    state.editGuidance({ ...item, text: "本篇覆盖。", overridden: true }, null);
    expect(state.guidanceText(item)).toBe(item.sourceText);
    expect(state.guidanceEdits.value[item.id].text).toBeNull();
  });

  it("建议读取失败会明确报错，不能显示为已读取", async () => {
    const { state, query } = setup();
    query.mockRejectedValue(new Error("当前标题与来源模板无法对应。"));
    expect(await state.loadGuidance(true)).toBe(false);
    expect(state.guidancePhase.value).toBe("error");
    expect(state.guidanceError.value).toContain("无法对应");
  });

  it("旧范围迟到的响应不会覆盖新章节的建议", async () => {
    const { state, query } = setup();
    let reply!: (value: unknown) => void;
    query.mockReturnValue(new Promise(resolve => { reply = resolve; }));
    const pending = state.loadGuidance(true);
    state.selectedHeading.value = 1;
    reply({ revision: 5, groups });
    expect(await pending).toBe(false);
    expect(state.guidanceGroups.value).toEqual([]);
  });

  it("生成后的正文预览不会混入选区定位或触发建议重新读取", async () => {
    const { state, query } = setup();
    state.aiOpen.value = true;
    state.aiPreviewHost.value = {};
    expect(await state.loadGuidance()).toBe(false);
    expect(query).not.toHaveBeenCalled();
  });

  it("保存覆盖仅写本篇并更新修订，使用稳定规则id", async () => {
    const { state, query, update } = setup();
    query.mockResolvedValue({ revision: 5, groups });
    update.mockResolvedValue({ revision: 6, formatTemplate: { writingGuidance: { "rule-a": "只写接口。" } } });
    state.editGuidance(item, "只写接口。");
    expect(await state.saveGuidance(true)).toBe(true);
    expect(update).toHaveBeenCalledWith("document", 5, [{ id: "rule-a", text: "只写接口。" }]);
    expect(state.document.value.revision).toBe(6);
    expect(state.guidanceChanged.value).toBe(false);
    expect(state.markdownContent.value).toBe("");
  });

  it("他人改了同一建议时保留本地修改，明确选择后才能保存", async () => {
    const { state, query, update } = setup();
    query.mockResolvedValue({ revision: 5, groups: [{ ...groups[0], items: [{ ...item, text: "服务器新建议。" }] }] });
    state.editGuidance(item, "我的建议。");
    expect(await state.saveGuidance(true)).toBe(false);
    expect(update).not.toHaveBeenCalled();
    expect(state.guidanceText(item)).toBe("我的建议。");
    expect(state.guidanceConflicts.value[item.id]).toBe("服务器新建议。");
    state.resolveGuidanceChange(item, true);
    expect(state.guidanceEdits.value[item.id].base).toBe("服务器新建议。");
  });

  it("空建议和只读状态不提交写接口", async () => {
    const { state, query, update } = setup();
    query.mockResolvedValue({ revision: 5, groups });
    state.editGuidance(item, "  ");
    expect(await state.saveGuidance(true)).toBe(false);
    expect(state.guidanceError.value).toContain("不能为空");
    state.readonly.value = true;
    expect(await state.saveGuidance(true)).toBe(false);
    expect(update).not.toHaveBeenCalled();
  });
});
