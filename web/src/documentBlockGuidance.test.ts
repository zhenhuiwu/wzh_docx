import { compileScript, parse } from "@vue/compiler-sfc";
import ts from "typescript";
import * as vue from "vue";
import { describe, expect, it, vi } from "vitest";
import component from "./components/DocumentBlockGuidance.vue?raw";

const compiled = ts.transpileModule(compileScript(parse(component).descriptor, { id: "block-guidance" }).content,
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
function result(ids = ["a", "b"]) {
  return { revision: 5, groups: [{ headingIndex: 0, heading: "功能要求", items: ids.map(id => ({
    id: `object:${id}`, heading: "本段建议", kind: "guidance", sourceText: "", text: `只写${id}功能`,
    overridden: true, targetObjectIds: [id], targetText: `${id}正文`,
  })) }], notices: [] };
}
function setup() {
  const query = vi.fn().mockResolvedValue(result());
  const update = vi.fn().mockResolvedValue({ revision: 6, markdownContent: "原正文", status: "draft", formatTemplate: {} });
  const emit = vi.fn();
  const imports: Record<string, unknown> = {
    vue: { ...vue, watch: () => () => {}, onMounted: () => {}, onUnmounted: () => {} },
    "../api/documents": { queryWritingGuidance: query, updateWritingGuidance: update },
  };
  const exports: { default?: { setup: (props: unknown, context: unknown) => Record<string, any> } } = {};
  new Function("exports", "require", compiled)(exports, (name: string) => imports[name] ?? {});
  const props = vue.reactive({ documentId: "doc", revision: 5, objectIds: ["a", "b"], disabled: false });
  const state = exports.default!.setup(props, { expose: () => {}, emit });
  return { state, props, query, update, emit };
}

describe("对象编写建议", () => {
  it("清除表格自定义建议后显示原表说明", async () => {
    const { state, query } = setup();
    const response = result(["a"]);
    response.groups[0]!.items[0]!.sourceText = "注：B-测试；a-配置项。";
    query.mockResolvedValue(response);
    await state.load();
    state.edits.value["object:a"] = "";
    await state.save();
    expect(state.edits.value["object:a"]).toBe("注：B-测试；a-配置项。");
    expect(state.isDirty.value).toBe(false);
  });
  it("按对象查询，各段建议独立保存，只提交修改项", async () => {
    const { state, query, update, emit } = setup();
    await state.load();
    expect(query).toHaveBeenCalledWith("doc", 5, "objects", null, undefined, expect.any(AbortSignal), ["a", "b"]);
    state.edits.value["object:a"] = "明确 a 的输入和输出";
    await state.save();
    expect(update).toHaveBeenCalledWith("doc", 5, [{ id: "object:a", text: "明确 a 的输入和输出" }]);
    expect(state.edits.value["object:b"]).toBe("只写b功能");
    expect(state.isDirty.value).toBe(false);
    expect(emit).toHaveBeenCalledWith("saved", expect.objectContaining({ revision: 6, markdownContent: "原正文" }));
  });

  it("清空建议发送 null，超长建议不允许提交", async () => {
    const { state, update } = setup();
    await state.load();
    state.edits.value["object:a"] = "字".repeat(20001);
    await state.save();
    expect(update).not.toHaveBeenCalled();
    state.edits.value["object:a"] = "   ";
    await state.save();
    expect(update).toHaveBeenCalledWith("doc", 5, [{ id: "object:a", text: null }]);
    expect(state.edits.value["object:a"]).toBe("");
  });

  it("选择或文档版本变化不覆盖未保存内容，需明确放弃", async () => {
    const { state, props, query, update } = setup();
    await state.load();
    state.edits.value["object:a"] = "保留草稿";
    expect(state.requestClose()).toBe(false);
    props.objectIds = ["b"];
    await state.load();
    expect(query).toHaveBeenCalledTimes(1);
    expect(state.edits.value["object:a"]).toBe("保留草稿");
    expect(state.stale.value).toBe(true);
    await state.save();
    expect(update).not.toHaveBeenCalled();
    query.mockResolvedValue(result(["b"]));
    state.discard();
    await Promise.resolve();
    expect(state.items.value.map((item: any) => item.id)).toEqual(["object:b"]);
    expect(state.isDirty.value).toBe(false);
  });

  it("保存冲突保留内容并解除忙碌状态，只读时不请求保存", async () => {
    const { state, props, update, emit } = setup();
    await state.load();
    state.edits.value["object:a"] = "尚未提交的规则";
    update.mockRejectedValue(new Error("文档已更新"));
    await state.save();
    expect(state.error.value).toBe("文档已更新");
    expect(state.edits.value["object:a"]).toBe("尚未提交的规则");
    expect(emit).toHaveBeenLastCalledWith("busy", false);
    update.mockClear();
    props.disabled = true;
    await state.save();
    expect(update).not.toHaveBeenCalled();
  });

  it("空选择不查询，过期查询不能覆盖新选择", async () => {
    const { state, props, query } = setup();
    let finish: (value: unknown) => void = () => {};
    query.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    const first = state.load();
    props.objectIds = [];
    await state.load();
    finish(result());
    await first;
    expect(query).toHaveBeenCalledTimes(1);
    expect(state.items.value).toEqual([]);
    expect(state.loading.value).toBe(false);
  });
  it("多段建议逐项切换，保存包含各项更改且不会互相覆盖", async () => {
    const { state, update } = setup();
    await state.load();
    expect(state.activeItem.value).toBe("object:a");
    state.edits.value["object:a"] = "A 输入输出";
    state.activeItem.value = "object:b";
    state.edits.value["object:b"] = "B 边界条件";
    expect(state.edits.value["object:a"]).toBe("A 输入输出");
    await state.save();
    expect(update).toHaveBeenCalledWith("doc", 5, [
      { id: "object:a", text: "A 输入输出" },
      { id: "object:b", text: "B 边界条件" },
    ]);
  });

});
