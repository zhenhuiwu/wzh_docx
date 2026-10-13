import { compileScript, parse } from "@vue/compiler-sfc";
import ts from "typescript";
import * as vue from "vue";
import { describe, expect, it, vi } from "vitest";
import component from "./components/DocumentAssignments.vue?raw";
import type { DocumentAssignmentsState } from "./api/documentAssignments";

const compiled = ts.transpileModule(compileScript(parse(component).descriptor, { id: "assignments" }).content,
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const initial: DocumentAssignmentsState = {
  revision: 5, assignmentsRevision: 2, enabled: false, canManage: true,
  members: [{ id: "member", name: "编写人" }],
  items: [
    { objectId: "title", kind: "heading", text: "功能要求", assigneeId: null, assigneeName: null, canEdit: true },
    { objectId: "body", kind: "paragraph", text: "登录功能要求", assigneeId: null, assigneeName: null, canEdit: true },
  ],
};

function setup() {
  const get = vi.fn().mockResolvedValue(structuredClone(initial));
  const put = vi.fn();
  const emit = vi.fn();
  const imports: Record<string, unknown> = {
    vue: { ...vue, watch: () => () => {}, onMounted: () => {}, onUnmounted: () => {} },
    "../api/documentAssignments": { getDocumentAssignments: get, updateDocumentAssignments: put },
  };
  const exports: { default?: { setup: (props: unknown, context: unknown) => Record<string, any> } } = {};
  new Function("exports", "require", compiled)(exports, (name: string) => imports[name] ?? {});
  const props = vue.reactive({ documentId: "document", revision: 5, disabled: false });
  const state = exports.default!.setup(props, { expose: () => {}, emit });
  state.accept(structuredClone(initial));
  return { state, props, emit, get, put };
}

describe("文档分工", () => {
  it("批量分配多个对象，首次分配启用限制，保存双版本号", async () => {
    const { state, put, emit } = setup();
    state.selected.value = ["title", "body"];
    state.assignee.value = "member";
    state.assign();
    expect(state.enabled.value).toBe(true);
    expect(state.isDirty.value).toBe(true);
    put.mockResolvedValue({ ...initial, enabled: true, assignmentsRevision: 3 });
    await state.save();
    expect(put).toHaveBeenCalledWith("document", {
      expectedRevision: 5, expectedAssignmentsRevision: 2, enabled: true,
      items: [{ objectId: "title", assigneeId: "member" }, { objectId: "body", assigneeId: "member" }],
    });
    expect(state.isDirty.value).toBe(false);
    expect(emit).toHaveBeenCalledWith("changed", expect.objectContaining({ assignmentsRevision: 3 }));
  });

  it("清空最后一个负责人仍保留限制，必须显式关闭", () => {
    const { state } = setup();
    state.accept({ ...initial, enabled: true, items: [{ ...initial.items[0], assigneeId: "member" }] });
    state.selected.value = ["title"];
    state.assign();
    expect(state.edits.value).toEqual({ title: null });
    expect(state.enabled.value).toBe(true);
  });

  it("只读或版本变化时不发送分配请求，冲突失败保留草稿", async () => {
    const { state, props, put } = setup();
    state.selected.value = ["title"];
    state.assignee.value = "member";
    state.assign();
    props.revision = 6;
    await state.save();
    expect(put).not.toHaveBeenCalled();
    props.revision = 5;
    put.mockRejectedValue(new Error("分工已由其他人修改，请重新读取。"));
    await state.save();
    expect(state.error.value).toContain("其他人修改");
    expect(state.edits.value.title).toBe("member");
    expect(state.isDirty.value).toBe(true);
    put.mockClear();
    state.state.value.canManage = false;
    await state.save();
    expect(put).not.toHaveBeenCalled();
  });

  it("关闭未保存更改时先展示明确确认，正文搜索不改变已选范围", () => {
    const { state, emit } = setup();
    state.selected.value = ["title", "body"];
    state.assignee.value = "member";
    state.assign();
    state.search.value = "登录";
    expect(state.visible.value.map((item: any) => item.objectId)).toEqual(["body"]);
    expect(state.selected.value).toEqual(["title", "body"]);
    state.requestClose();
    expect(state.discardPrompt.value).toBe(true);
    expect(emit).not.toHaveBeenCalledWith("close");
  });

  it("重新读取失败时保留草稿，恢复成功后更新分配版本", async () => {
    const { state, get } = setup();
    state.selected.value = ["body"];
    state.assignee.value = "member";
    state.assign();
    get.mockRejectedValueOnce(new Error("网络中断"));
    await state.load();
    expect(state.edits.value.body).toBe("member");
    expect(state.loading.value).toBe(false);
    await state.load();
    expect(state.isDirty.value).toBe(false);
    expect(state.error.value).toBe("");
  });
  it("按章节查询并分页，跨页选择保持，切到建议仅收起选择器", () => {
    const { state } = setup();
    const paragraphs = Array.from({ length: 9 }, (_, index) => ({ ...initial.items[1], objectId: `p${index}`, text: `正文 ${index}` }));
    state.accept({ ...initial, items: [initial.items[0], ...paragraphs, { ...initial.items[0], objectId: "next", text: "其他章节" }] });
    state.chapter.value = "title";
    expect(state.visible.value).toHaveLength(10);
    expect(state.pageCount.value).toBe(3);
    state.selected.value = ["p0"];
    state.page.value = 2;
    expect(state.pageItems.value.map((item: any) => item.objectId)).toEqual(["p3", "p4", "p5", "p6"]);
    state.chooseTab("guidance");
    expect(state.pickerOpen.value).toBe(false);
    expect(state.selected.value).toEqual(["p0"]);
    state.chooseTab("assign");
    expect(state.pickerOpen.value).toBe(true);
    expect(state.selected.value).toEqual(["p0"]);
  });

  it("建议草稿锁定期间无法通过清空选择丢弃编辑对象", () => {
    const { state, props } = setup();
    state.selected.value = ["body"];
    props.disabled = true;
    state.clearSelection();
    expect(state.selected.value).toEqual(["body"]);
    props.disabled = false;
    state.clearSelection();
    expect(state.selected.value).toEqual([]);
  });

  it("关闭被建议草稿阻止时可显式显示建议页，不改变所选内容", () => {
    const { state, props } = setup();
    state.selected.value = ["body"];
    props.disabled = true;
    state.chooseTab("assign");
    state.showGuidance();
    expect(state.tab.value).toBe("guidance");
    expect(state.pickerOpen.value).toBe(false);
    expect(state.selected.value).toEqual(["body"]);
  });

});
