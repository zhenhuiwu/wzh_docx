import { ref } from "vue";
import { describe, expect, it } from "vitest";
import type { WritingGuidanceGroup, WritingGuidanceItem } from "./api/documents";
import { useGuidanceBrowser } from "./useGuidanceBrowser";

const advice = (id: string): WritingGuidanceItem => ({ id, heading: "要求", kind: "guidance", sourceText: "模板要求", text: "现有要求", overridden: false });
function setup() {
  const groups = ref<WritingGuidanceGroup[]>([
    { headingIndex: 0, heading: "概述", items: [advice("a"), advice("b")] },
    { headingIndex: 1, heading: "空章节", items: [] },
    { headingIndex: 2, heading: "功能", headingPath: "需求 / 用户管理", items: [advice("c"), advice("d")] },
  ]);
  return { groups, browser: useGuidanceBrowser(groups) };
}
describe("编写建议分组浏览", () => {
  it("大量建议只定位一项，浏览不删减原始建议", () => {
    const { groups, browser } = setup();
    expect(browser.available.value).toHaveLength(2);
    expect(browser.item.value?.id).toBe("a");
    browser.moveItem(1);
    expect(browser.item.value?.id).toBe("b");
    browser.moveItem(1);
    expect(browser.item.value?.id).toBe("b");
    browser.selectedKey.value = "2";
    expect(browser.item.value?.id).toBe("c");
    expect(groups.value.flatMap(group => group.items).map(item => item.id)).toEqual(["a", "b", "c", "d"]);
  });
  it("按章节名称或路径查找，没有匹配时不显示无关指导", () => {
    const { browser } = setup();
    browser.search.value = " 用户管理 ";
    expect(browser.group.value?.heading).toBe("功能");
    browser.search.value = "不存在";
    expect(browser.group.value).toBeUndefined();
    expect(browser.itemIndex.value).toBe(-1);
  });
  it("隐藏的冲突建议可跳转，切换浏览不会覆盖编辑的数据", () => {
    const { groups, browser } = setup();
    groups.value[2]!.items[1]!.text = "用户已修改";
    browser.search.value = "概述";
    browser.revealItem("d");
    expect(browser.search.value).toBe("");
    expect(browser.selectedKey.value).toBe("2");
    expect(browser.item.value?.text).toBe("用户已修改");
    browser.selectedKey.value = "0";
    browser.revealItem("d");
    expect(browser.item.value?.text).toBe("用户已修改");
  });
  it("刷新后建议消失时回退到有效项，新文档重置浏览条件", () => {
    const { groups, browser } = setup();
    browser.revealItem("d");
    groups.value[2]!.items.pop();
    expect(browser.item.value?.id).toBe("c");
    groups.value = [groups.value[0]!];
    expect(browser.item.value?.id).toBe("a");
    browser.search.value = "用户管理";
    browser.reset();
    expect(browser.selectedKey.value).toBe("0");
  });
});
