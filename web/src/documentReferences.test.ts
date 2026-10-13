import { compileScript, parse } from "@vue/compiler-sfc";
import ts from "typescript";
import * as vue from "vue";
import { afterEach, describe, expect, it, vi } from "vitest";
import component from "./components/DocumentReferences.vue?raw";
import type { DocumentReferencesState } from "./api/documentReferences";

const compiled = ts.transpileModule(compileScript(parse(component).descriptor, { id: "references" }).content,
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const initial: DocumentReferencesState = { revision: 5, canEdit: true, items: [
  { id: "reference", kind: "document", documentId: "source", name: "技术要求", projectName: "项目甲", sourceRevision: 2, latestRevision: 3, note: "背景依据", available: true },
] };
const scopes: vue.EffectScope[] = [];
afterEach(() => { scopes.splice(0).forEach(scope => scope.stop()); });
function setup() {
  const get = vi.fn().mockResolvedValue(structuredClone(initial));
  const put = vi.fn();
  const list = vi.fn().mockResolvedValue({ items: [{ id: "second", name: "测试要求", projectName: "项目乙", revision: 4 }], page: 1, pageSize: 8, total: 17 });
  const snapshot = vi.fn().mockResolvedValue({ name: "技术要求", content: "原始参考内容", hasMore: true, nextOffset: 8, revision: 2 });
  const source = vi.fn().mockResolvedValue({ name: "技术要求", content: "更新内容", hasMore: false, nextOffset: 4, revision: 3 });
  const emit = vi.fn();
  const imports: Record<string, unknown> = {
    vue: { ...vue, onMounted: () => {}, onUnmounted: () => {} },
    "../api/documentReferences": { getDocumentReferences: get, updateDocumentReferences: put, listReferenceDocuments: list,
      getReferencePreview: snapshot, getReferenceDocumentPreview: source },
  };
  const exports: { default?: { setup: (props: unknown, context: unknown) => Record<string, any> } } = {};
  new Function("exports", "require", compiled)(exports, (name: string) => imports[name] ?? {});
  const props = vue.reactive({ documentId: "document", revision: 5, disabled: false });
  const scope = vue.effectScope(); scopes.push(scope);
  const state = scope.run(() => exports.default!.setup(props, { expose: () => {}, emit }))!;
  state.accept(structuredClone(initial));
  return { state, props, emit, get, put, list, snapshot, source };
}

describe("参考资料", () => {
  it("保存后父页面采用新修订，不重新加载并抹掉保存成功状态", async () => {
    const { state, props, get, put } = setup();
    state.items.value[0].note = "本次用途";
    put.mockResolvedValue({ ...initial, revision: 6, items: state.items.value.map((item: any) => ({ ...item })) });
    await state.save();
    props.revision = 6;
    await vue.nextTick();
    expect(get).not.toHaveBeenCalled();
    expect(state.saved.value).toBe(true);
    expect(state.isDirty.value).toBe(false);
  });
  it("保留选定版本，显式更新后按文档与粘贴信息的保存格式提交", async () => {
    const { state, put, emit } = setup();
    expect(state.items.value[0].sourceRevision).toBe(2);
    state.useLatest(state.items.value[0]);
    state.informationName.value = "用户背景";
    state.informationContent.value = "真实背景材料";
    state.informationNote.value = "解释术语";
    state.addInformation();
    const information = state.items.value[1];
    put.mockResolvedValue({ ...initial, revision: 6, items: state.items.value.map((item: any) => ({ ...item })) });
    await state.save();
    expect(put).toHaveBeenCalledWith("document", { expectedRevision: 5, items: [
      { id: "reference", kind: "document", documentId: "source", sourceRevision: 3, note: "背景依据" },
      { id: information.id, kind: "information", name: "用户背景", content: "真实背景材料", note: "解释术语" },
    ] });
    expect(state.isDirty.value).toBe(false);
    expect(emit).toHaveBeenCalledWith("saved", expect.objectContaining({ revision: 6 }));
  });

  it("关闭粘贴草稿显示面板提示，显式放弃后恢复已保存资料", () => {
    const { state, emit } = setup();
    state.informationContent.value = "未加入的信息";
    state.requestClose();
    expect(state.discardPrompt.value).toBe(true);
    expect(emit).not.toHaveBeenCalledWith("close");
    state.items.value[0].note = "未保存说明";
    state.discard();
    expect(state.items.value[0].note).toBe("背景依据");
    expect(state.informationContent.value).toBe("");
    expect(state.isDirty.value).toBe(false);
    expect(emit).toHaveBeenCalledWith("dirty", true);
    expect(emit).toHaveBeenCalledWith("dirty", false);
  });

  it("保存失败、权限失效与文档版本变化均保留草稿", async () => {
    const { state, props, get, put } = setup();
    state.items.value[0].note = "新用途";
    props.revision = 6;
    await vue.nextTick();
    expect(get).not.toHaveBeenCalled();
    await state.save();
    expect(put).not.toHaveBeenCalled();
    expect(state.stale.value).toBe(true);
    props.revision = 5;
    put.mockRejectedValueOnce(new Error("文档已更新"));
    await state.save();
    expect(state.error.value).toContain("文档已更新");
    expect(state.items.value[0].note).toBe("新用途");
    state.state.value.canEdit = false;
    put.mockClear();
    await state.save();
    expect(put).not.toHaveBeenCalled();
    expect(state.isDirty.value).toBe(true);
  });

  it("搜索与分页查询后加入文档，重复与十项上限不能再加入", async () => {
    const { state, list } = setup();
    state.name.value = " 测试 ";
    await state.queryDocuments(2);
    expect(list).toHaveBeenCalledWith("document", "测试", 2);
    expect(state.pageCount.value).toBe(3);
    const entry = state.documentPage.value.items[0];
    state.addDocument(entry); state.addDocument(entry);
    expect(state.items.value).toHaveLength(2);
    for (let n = 0; n < 12; n++) state.addDocument({ ...entry, id: `source-${n}` });
    expect(state.items.value).toHaveLength(10);
  });

  it("预览已保存快照，读取后续原文；更新版本后使用来源预览", async () => {
    const { state, snapshot, source } = setup();
    await state.showPreview(state.items.value[0]);
    expect(snapshot).toHaveBeenCalledWith("document", "reference", 0);
    snapshot.mockResolvedValueOnce({ name: "技术要求", content: "后续原文", hasMore: false, nextOffset: 12, revision: 2 });
    await state.showPreview(state.items.value[0], true);
    expect(snapshot).toHaveBeenLastCalledWith("document", "reference", 8);
    expect(state.preview.value.content).toBe("原始参考内容后续原文");
    state.useLatest(state.items.value[0]);
    await state.showPreview(state.items.value[0]);
    expect(source).toHaveBeenCalledWith("document", "source", 3, 0);
    state.items.value[0].available = false;
    state.items.value[0].name = "隐藏来源名称";
    expect(state.displayName(state.items.value[0])).toBe("参考文档不可用");
    source.mockClear(); snapshot.mockClear();
    await state.showPreview(state.items.value[0]);
    expect(source).not.toHaveBeenCalled();
    expect(snapshot).not.toHaveBeenCalled();
  });

  it("切换文档后隐藏旧草稿，迟到读取与保存响应不能覆盖当前文档", async () => {
    const { state, props, get, put, emit } = setup();
    let finishLoad!: (value: DocumentReferencesState) => void;
    get.mockImplementationOnce(() => new Promise(resolve => { finishLoad = resolve; }));
    const loading = state.load();
    state.items.value[0].note = "保留草稿";
    props.documentId = "other";
    await vue.nextTick();
    finishLoad({ ...initial, revision: 9 }); await loading;
    expect(state.wrongDocument.value).toBe(true);
    expect(state.items.value[0].note).toBe("保留草稿");
    expect(state.state.value.revision).toBe(5);
    props.documentId = "document";
    await vue.nextTick();
    let finishSave!: (value: DocumentReferencesState) => void;
    put.mockImplementationOnce(() => new Promise(resolve => { finishSave = resolve; }));
    const saving = state.save();
    props.documentId = "other";
    await vue.nextTick();
    finishSave({ ...initial, revision: 6 }); await saving;
    expect(emit).not.toHaveBeenCalledWith("saved", expect.anything());
    expect(state.items.value[0].note).toBe("保留草稿");
    expect(state.saving.value).toBe(false);
  });

  it("粘贴内容必填且草稿未加入时不能保存，重新读取失败保留更改", async () => {
    const { state, get, put } = setup();
    state.informationName.value = "只有名称";
    state.addInformation();
    expect(state.informationError.value).toContain("原文");
    state.items.value[0].note = "新用途";
    await state.save();
    expect(put).not.toHaveBeenCalled();
    state.clearInformation();
    get.mockRejectedValueOnce(new Error("网络中断"));
    await state.load();
    expect(state.items.value[0].note).toBe("新用途");
    expect(state.isDirty.value).toBe(true);
    expect(state.loading.value).toBe(false);
  });
});
