import { describe, expect, it } from "vitest";
import { rebaseBlocks } from "./workbenchStats";

const doc = (rows: string[]) => rows.join("\n\n");
const base = Array.from({ length: 8 }, (_, i) => `第${i + 1}段：原始内容 ${i + 1}。`);

describe("终验 F-H2：保存在途时继续打字，把未保存的输入平移到服务器新正文上", () => {
  it("QA 复现：第 3 段继续打字、对方改第 7 段 → 两处都在，不再与自己冲突", () => {
    const sent = doc(base.map((row, i) => i === 2 ? row + "【丙01" : row));
    const mine = doc(base.map((row, i) => i === 2 ? row + "【丙0123456789ABCDEFGHIJ丁】" : row));
    const server = doc(base.map((row, i) => i === 2 ? row + "【丙01" : i === 6 ? "第7段：原始内容 7（对方改）。" : row));
    expect(rebaseBlocks(sent, mine, server)).toBe(doc(base.map((row, i) => i === 2 ? row + "【丙0123456789ABCDEFGHIJ丁】" : i === 6 ? "第7段：原始内容 7（对方改）。" : row)));
  });
  it("对方在前面插入段落、我回车拆段：位置按对齐换算", () => {
    const sent = doc(base);
    const mine = doc([...base.slice(0, 3), "新的一段", ...base.slice(3)]);
    const server = doc(["对方插入", ...base]);
    expect(rebaseBlocks(sent, mine, server)).toBe(doc(["对方插入", ...base.slice(0, 3), "新的一段", ...base.slice(3)]));
    const mine2 = doc(base.map((row, i) => i === 4 ? "前半" : row).flatMap((row, i) => i === 4 ? [row, "后半"] : [row]));
    expect(rebaseBlocks(sent, mine2, server)).toBe(doc(["对方插入", ...base.slice(0, 4), "前半", "后半", ...base.slice(5)]));
  });
  it("对方也改了我正在改的段 / 位置不确定：返回 null（按旧基准合并，不猜）", () => {
    const sent = doc(base), mine = doc(base.map((row, i) => i === 6 ? row + "我" : row));
    expect(rebaseBlocks(sent, mine, doc(base.map((row, i) => i === 6 ? row + "他" : row)))).toBeNull();
    const dup = ["无。", "A", "无。"];
    expect(rebaseBlocks(doc(["无。", "A"]), doc(["无。改", "A"]), doc(dup.concat(["A"])))).toBeNull();
  });
  it("没有未保存的输入 / 服务器没变：直接取对方", () => {
    expect(rebaseBlocks("a\n\nb", "a\n\nb", "a\n\nb\n\nc")).toBe("a\n\nb\n\nc");
    expect(rebaseBlocks("a\n\nb", "a\n\nbX", "a\n\nb")).toBe("a\n\nbX");
  });
  it("1500 段大文档：快速且正确", () => {
    const big = Array.from({ length: 1500 }, (_, i) => `填充段落${i}。`);
    const sent = doc(big.map((row, i) => i === 2 ? row + "甲" : row));
    const mine = doc(big.map((row, i) => i === 2 ? row + "甲乙丙" : row));
    const server = doc(big.map((row, i) => i === 2 ? row + "甲" : i === 6 ? row + "（对方改）" : row));
    const started = Date.now();
    expect(rebaseBlocks(sent, mine, server)).toBe(doc(big.map((row, i) => i === 2 ? row + "甲乙丙" : i === 6 ? row + "（对方改）" : row)));
    expect(Date.now() - started).toBeLessThan(1000);
  });
});
