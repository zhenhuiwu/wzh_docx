import { describe, expect, it } from "vitest";
import { revertBlock } from "./blockRevert";

const saved = "# 1 范围\n\n## 1.1 标识\n\n文档标识号：TN/x；\n\n第二段。\n\n第三段。\n";

describe("撤销这一段的修改（界面验收 M1）", () => {
  it("只换回失败的那一段，其他修改保留", () => {
    const current = saved.replace("文档标识号：TN/x；", "文档标识号：ABC-01；").replace("第三段。", "第三段改。");
    expect(revertBlock(saved, current, "文档标识号：ABC-01；")).toBe(saved.replace("第三段。", "第三段改。"));
  });
  it("Markdown 转义与粗体不影响匹配", () => {
    const current = saved.replace("文档标识号：TN/x；", "文档标识号：**A\\|B**；");
    expect(revertBlock(saved, current, "文档标识号：A|B；")).toBe(saved);
  });
  it("界面复验 N1：被拒的删除整段——按原内容插回原位置，其他修改保留", () => {
    const current = saved.replace("文档标识号：TN/x；\n\n", "").replace("第三段。", "第三段改。");
    const restored = revertBlock(saved, current, "第二段。", "文档标识号：TN/x；");
    expect(restored).toBe(saved.replace("第三段。", "第三段改。"));
  });
  it("界面复验 N1：删除时连带改了相邻标题——只插回被删段，标题的修改保留", () => {
    const current = saved.replace("## 1.1 标识\n\n文档标识号：TN/x；", "## 1.1 标识文档");
    expect(revertBlock(saved, current, "1.1 标识文档", "文档标识号：TN/x；")).toBe(saved.replace("## 1.1 标识", "## 1.1 标识文档"));
  });
  it("界面复验 N1：被删段落仍在新内容里（不是删除）时退回按失败块匹配", () => {
    const current = saved.replace("文档标识号：TN/x；", "文档标识号：ABC；");
    expect(revertBlock(saved, current, "文档标识号：ABC；", "完全无关")).toBe(saved);
  });
  it("测试复测 N2：被拒段后新增的段落不会被一起撤掉", () => {
    const current = saved.replace("文档标识号：TN/x；", "文档标识号：ABC；\n\n我新加的一段。");
    expect(revertBlock(saved, current, "文档标识号：ABC；")).toBe(saved.replace("文档标识号：TN/x；", "文档标识号：TN/x；\n\n我新加的一段。"));
  });
  it("测试复测 N2：被拒段前新增的段落也保留", () => {
    const current = saved.replace("文档标识号：TN/x；", "前面新加。\n\n文档标识号：ABC；");
    expect(revertBlock(saved, current, "文档标识号：ABC；")).toBe(saved.replace("文档标识号：TN/x；", "前面新加。\n\n文档标识号：TN/x；"));
  });
  it("测试复测 N2：被拒段是并入上一段的结果——换回两段，后面新增段保留", () => {
    const current = saved.replace("## 1.1 标识\n\n文档标识号：TN/x；", "## 1.1 标识文档标识号：TN/x；\n\n新增段N");
    expect(revertBlock(saved, current, "1.1 标识文档标识号：TN/x；")).toBe(saved.replace("文档标识号：TN/x；", "文档标识号：TN/x；\n\n新增段N"));
  });
  it("界面复验 N1：删除被拒段并在原处新加一段——只插回被删段，新加的保留", () => {
    const current = saved.replace("文档标识号：TN/x；", "我新加的一段。");
    const restored = revertBlock(saved, current, "我新加的一段。", "文档标识号：TN/x；");
    expect(restored).toBe(saved.replace("文档标识号：TN/x；", "文档标识号：TN/x；\n\n我新加的一段。"));
  });
  it("找不到对应段落时不改动", () => {
    const current = saved.replace("第二段。", "第二段改。");
    expect(revertBlock(saved, current, "完全不同的文字")).toBeNull();
  });
});

// 设计师复验 3 C-2：服务器 details.blockText 只取单元格文字（server/app/documents.py word_format_error：itertext 后按空白合并）
const cellText = (table: string) => table.split("\n").filter(line => !/^[\s|:-]+$/.test(line))
  .flatMap(line => line.replace(/^\s*\||\|\s*$/g, "").split("|").map(cell => cell.replace(/<br\s*\/?>/g, "").replace(/\\(.)/g, "$1").trim()))
  .filter(Boolean).join(" ");
const MERGED = [
  "| 测试轮次 | 测试轮次 | 问题总计 | 问题类型 | 问题类型 |",
  "|---|---|---|---|---|",
  "| 测试轮次 | 测试轮次 | 问题总计 | 功能缺失 | 其它 |",
  "| 合计 | 合计 |  |  |  |",
  "| 第一轮回归测试 | 已纠正 |  |  |  |",
  "| 第一轮回归测试 | 新引入 |  |  |  |",
].join("\n");
const doc = (table: string, caption = "表-X按问题类型统计") => `# 测试结果概述\n\n别处的一段。\n\n${caption}\n\n${table}\n\n表后一段。\n`;

describe("撤销这一段的修改：表格（设计师复验 3 C-2）", () => {
  it("合并单元格表改了一格 + 别处一段修改：只还原这张表，别处的修改保留", () => {
    const saved = doc(MERGED);
    const table = MERGED.replace("| 第一轮回归测试 | 已纠正 |", "| 第一轮回归测试李工填5 | 已纠正 |");
    const current = doc(table).replace("别处的一段。", "别处的一段。【李工别处的修改】");
    expect(revertBlock(saved, current, cellText(table))).toBe(saved.replace("别处的一段。", "别处的一段。【李工别处的修改】"));
  });
  it("表题段落也改了：只还原表格，表题的修改保留", () => {
    const saved = doc(MERGED);
    const table = MERGED.replace("| 合计 | 合计 |  |", "| 合计 | 合计 | 5 |");
    const current = doc(table, "表-X按问题类型统计【李工别处的修改】");
    expect(revertBlock(saved, current, cellText(table))).toBe(doc(MERGED, "表-X按问题类型统计【李工别处的修改】"));
  });
  it("分隔行带对齐冒号和空格", () => {
    const aligned = MERGED.replace("|---|---|---|---|---|", "| :--- | :---: | ---: | --- |:-|");
    const saved = doc(aligned);
    const table = aligned.replace("功能缺失", "功能缺失X");
    expect(revertBlock(saved, doc(table).replace("表后一段。", "表后一段改。"), cellText(table))).toBe(saved.replace("表后一段。", "表后一段改。"));
  });
  it("不带外侧竖线的表格", () => {
    const bare = "甲 | 乙\n--- | :---:\n单元格甲 | 单元格乙";
    const saved = doc(bare);
    const table = bare.replace("单元格乙", "单元格乙改");
    expect(revertBlock(saved, doc(table), cellText(table))).toBe(saved);
  });
  it("单元格内换行（<br>）和转义竖线", () => {
    const rich = "| 项目 | 说明 |\n| --- | --- |\n| 甲 | 第一行<br>第二行 |\n| 乙 | A\\|B |";
    const saved = doc(rich);
    const table = rich.replace("第二行", "第二行改");
    expect(cellText(table)).toContain("第一行第二行改");
    expect(revertBlock(saved, doc(table), cellText(table))).toBe(saved);
  });
  it("单元格全是“-”的数据行不当分隔行去掉", () => {
    const dashes = "| 项目 | 说明 |\n|---|---|\n| - | - |\n| 甲 | 乙 |";
    const saved = doc(dashes);
    const table = dashes.replace("| 甲 | 乙 |", "| 甲 | 乙改 |");
    const blockText = "项目 说明 - - 甲 乙改";
    expect(revertBlock(saved, doc(table).replace("别处的一段。", "别处改。"), blockText)).toBe(saved.replace("别处的一段。", "别处改。"));
  });
  it("页面里有两张同表头的表，只改了其中一张：只还原改过的那张", () => {
    const saved = doc(MERGED) + "\n" + MERGED.replace(/问题类型/g, "问题等级") + "\n";
    const table = MERGED.replace("| 合计 | 合计 |  |", "| 合计 | 合计 | 9 |");
    const current = saved.replace(MERGED, table);
    expect(revertBlock(saved, current, cellText(table))).toBe(saved);
  });
});
