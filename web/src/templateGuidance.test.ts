import { compileScript, parse } from "@vue/compiler-sfc";
import ts from "typescript";
import * as vue from "vue";
import { afterEach, describe, expect, it, vi } from "vitest";
import component from "./views/TemplateEditorView.vue?raw";
import * as editing from "./templateEditing";
import type { EditorBlock } from "./api/templates";
const compiled = ts.transpileModule(compileScript(parse(component).descriptor, { id: "template-targets" }).content,
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
function block(id: string, kind: EditorBlock["kind"], extra: Partial<EditorBlock> = {}): EditorBlock {
  return { id, kind, text: `${id}正文`, path: null, sourceText: "", headingLevel: kind === "heading" ? 1 : null,
    headingId: "heading", tableId: null, protected: false, isTable: kind === "table", ...extra };
}
function setup() {
  vi.stubGlobal("window", { clearTimeout: vi.fn(), setTimeout: vi.fn() });
  const save = vi.fn().mockResolvedValue({ draftRevision: 2, updatedAt: "2026-10-13" });
  const imports: Record<string, unknown> = {
    vue: { ...vue, watch: () => () => {}, onMounted: () => {}, onBeforeUnmount: () => {} },
    "vue-router": { useRoute: () => ({ params: { id: "template", docId: "doc" } }), useRouter: () => ({}), onBeforeRouteLeave: () => {} },
    "../session": { hasPermission: () => true, readSession: () => ({}) },
    "../templateEditing": editing,
    "../api/templates": { saveTemplateDraft: save },
  };
  const exports: { default?: { setup: (props: unknown, context: unknown) => Record<string, any> } } = {};
  new Function("exports", "require", compiled)(exports, (name: string) => imports[name] ?? {});
  const state = exports.default!.setup({}, { expose: () => {} });
  state.phase.value = "ready";
  state.structure.value = { schemaVersion: 1, revision: 1, reviewed: true, sourceHash: "hash", blocks: [block("heading", "heading"), block("guide", "guidance"), block("body", "body"), block("table", "table")] };
  state.selectedId.value = "guide";
  state.current.value = { seq: 1, versionId: "version" };
  return { state, save };
}
afterEach(() => vi.unstubAllGlobals());

describe("模板精确编写指导", () => {
  it("指定正文和整表后现有草稿保存保留目标字段", async () => {
    const { state, save } = setup();
    state.setGuidanceScope({ target: { value: "targets" } });
    state.setGuidanceTarget("body", { target: { checked: true } });
    state.setGuidanceTarget("table", { target: { checked: true } });
    await state.save();
    expect(save).toHaveBeenCalledWith("doc", expect.objectContaining({ structure: expect.objectContaining({ blocks: expect.arrayContaining([expect.objectContaining({ id: "guide", targetBlockIds: ["body", "table"] })]) }) }));
  });
  it("改为章节通用显式清空目标，精确模式未选内容则不保存", async () => {
    const { state, save } = setup();
    state.setGuidanceScope({ target: { value: "targets" } });
    await state.save();
    expect(save).not.toHaveBeenCalled();
    expect(state.targetError(state.selected.value)).toContain("至少选择");
    state.setGuidanceTarget("body", { target: { checked: true } });
    state.setGuidanceScope({ target: { value: "chapter" } });
    expect(state.selected.value.targetBlockIds).toEqual([]);
    await state.save();
    expect(save).toHaveBeenCalledTimes(1);
  });
  it("失效目标保留并提示，禁止发布；改类型不带无效指导字段", async () => {
    const { state, save } = setup();
    state.selected.value.targetBlockIds = ["gone"];
    state.dirty.value = true;
    expect(state.targetError(state.selected.value)).toContain("被删除");
    expect(state.publishBlock.value).toContain("适用内容无效");
    await state.save();
    expect(save).not.toHaveBeenCalled();
    expect(state.selected.value.targetBlockIds).toEqual(["gone"]);
    state.setKind({ target: { value: "body" } });
    expect(state.selected.value).not.toHaveProperty("targetBlockIds");
  });
  it("过滤表内段、空正文、保护内容及非正文类型", () => {
    const candidates = [block("ok", "body"), block("table", "table"), block("cell", "body", { tableId: "table" }),
      block("empty", "body", { text: " " }), block("protected", "body", { protected: true }), block("heading", "heading"), block("example", "example")];
    expect(candidates.filter(editing.canTargetGuidance).map(item => item.id)).toEqual(["ok", "table"]);
    expect(editing.guidanceScopeText(["ok"], ["示例正文"])).toBe("指定 1 处：示例正文");
    expect(editing.conflictFieldLabel("targetBlockIds", "")).toBe("指导适用内容");
  });
  it("清除失效绑定保留仍有效的目标，全部失效也不会静默改为章节通用", () => {
    const { state } = setup();
    state.selected.value.targetBlockIds = ["gone", "body"];
    state.clearInvalidTargets();
    expect(state.selected.value.targetBlockIds).toEqual(["body"]);
    state.selected.value.targetBlockIds = ["gone"];
    state.clearInvalidTargets();
    expect(state.guidanceIsSpecific.value).toBe(true);
    expect(state.targetError(state.selected.value)).toContain("至少选择");
  });
});
