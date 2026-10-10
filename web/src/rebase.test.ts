import { describe, expect, it } from "vitest";
import { keepsServerChanges, rebaseBlocks } from "./workbenchStats";

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

describe("增量审查 M1/M2：rebaseBlocks 随机对拍与列表、表格", () => {
  function rng(seed: number) { let s = seed >>> 0; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296); }
  it("对方插/删/改别的段（含大量重复“无。”）：平移结果从不丢对方的段，从不把我的字写到别的文字的段", () => {
    const r = rng(77), pool = ["无。", "无。", "不适用。", "A", "B", "C"];
    let nul = 0, exact = 0, sameText = 0, wrongOther = 0, lost = 0;
    const trials = 20000;
    for (let t = 0; t < trials; t++) {
      let id = 0;
      const n = 3 + Math.floor(r() * 8);
      const sent = Array.from({ length: n }, () => ({ id: id++, text: r() < 0.5 ? pool[Math.floor(r() * pool.length)] : `P${id}` }));
      const u = Math.floor(r() * n);
      const server = sent.map(b => ({ ...b }));
      for (let o = 1 + Math.floor(r() * 3); o > 0; o--) {
        const kind = r(), at = Math.floor(r() * (server.length + 1));
        if (kind < 0.4) server.splice(at, 0, { id: id++, text: r() < 0.6 ? pool[Math.floor(r() * 2)] : `N${id}` });
        else {
          const i = Math.min(at, server.length - 1);
          if (!server[i] || server[i].id === sent[u].id) continue;
          if (kind < 0.7) server.splice(i, 1); else server[i] = { ...server[i], text: server[i].text + "改" };
        }
      }
      const mine = sent.map(b => b.text); mine[u] += "【我】";
      const res = rebaseBlocks(sent.map(b => b.text).join("\n\n"), mine.join("\n\n"), server.map(b => b.text).join("\n\n"));
      if (res === null) { nul++; continue; }
      if (res === server.map(b => (b.id === sent[u].id ? b.text + "【我】" : b.text)).join("\n\n")) { exact++; continue; }
      const out = res.split("\n\n");
      if (out.length !== server.length || !server.every(b => b.id === sent[u].id || out.some(x => x === b.text || x === b.text + "【我】"))) { lost++; continue; }
      const at = out.findIndex(x => x.endsWith("【我】"));
      if (server[at] && server[at].text === sent[u].text) sameText++; else wrongOther++;
    }
    expect({ lost, wrongOther }).toEqual({ lost: 0, wrongOther: 0 });
    // 写到“同样文字的另一段”只在重复段落、对方恰好在旁边插了同样文字时出现（文字上无法区分）
    expect(sameText).toBeLessThan(trials * 0.002);
    expect(exact).toBeGreaterThan(trials * 0.9);
    expect(nul + exact + sameText).toBe(trials);
  });
  it("列表：对方改了同一列表的另一项", () => {
    const sent = "# T\n\n- 项一【P1】\n- 项二\n- 项三\n\n尾段。";
    expect(rebaseBlocks(sent, sent.replace("【P1】", "【P1】【P2】"), sent.replace("项二", "项二（张改）")))
      .toBe("# T\n\n- 项一【P1】【P2】\n- 项二（张改）\n- 项三\n\n尾段。");
  });
  it("表格：对方改了另一行可平移；改了同一行的另一格返回 null（交给显式基准合并）", () => {
    const sent = "# T\n\n| a | b |\n| --- | --- |\n| 1【P1】 | 2 |\n| 3 | 4 |\n\n尾段。";
    expect(rebaseBlocks(sent, sent.replace("【P1】", "【P1】【P2】"), sent.replace("| 4 |", "| 4改 |")))
      .toBe(sent.replace("【P1】", "【P1】【P2】").replace("| 4 |", "| 4改 |"));
    expect(rebaseBlocks(sent, sent.replace("【P1】", "【P1】【P2】"), sent.replace("| 2 |", "| 2改 |"))).toBeNull();
  });
});

describe("测试工程师 F-H3：基准前移的不变量（keepsServerChanges）", () => {
  const sent = "# T\n\n第1段。\n\n第3段：原始内容 3。【丙01\n\n第7段。";
  const server = "# T\n\n第1段。\n\n第3段：原始内容 3（对方改同段）。\n\n第7段。";
  it("平移结果缺了对方刚写进的段落（同段被换成我的版本）时不允许前移", () => {
    const overwritten = "# T\n\n第1段。\n\n第3段：原始内容 3。【丙0123】\n\n第7段。";
    expect(keepsServerChanges(sent, server, overwritten)).toBe(false);
    // rebaseBlocks 本身遇到同段双方都改返回 null（交给显式基准由服务器合并/进冲突）
    expect(rebaseBlocks(sent, sent.replace("【丙01", "【丙0123】"), server)).toBeNull();
  });
  it("对方的修改都在（另一段 / 表格另一行）时允许前移", () => {
    const other = "# T\n\n第1段（对方改）。\n\n第3段：原始内容 3。【丙01\n\n第7段。";
    const value = rebaseBlocks(sent, sent.replace("【丙01", "【丙0123】"), other)!;
    expect(keepsServerChanges(sent, other, value, sent.replace("【丙01", "【丙0123】"))).toBe(true);
    const table = "| a | b |\n|---|---|\n| 1【P1】 | 2 |\n| 3 | 4 |";
    const tableServer = table.replace("| 4 |", "| 4改 |");
    const tableValue = rebaseBlocks(table, table.replace("【P1】", "【P1】【P2】"), tableServer)!;
    expect(keepsServerChanges(table, tableServer, tableValue, table.replace("【P1】", "【P1】【P2】"))).toBe(true);
  });
  it("增量审查建议 4：多重集合——对方新加的同文字行（如第二个“无。”）被吞掉时不允许前移", () => {
    const sent = "甲。\n\n无。\n\n乙。";
    const server = "甲。\n\n无。\n\n无。\n\n乙。";
    const mine = "甲。【我】\n\n无。\n\n乙。";
    expect(keepsServerChanges(sent, server, "甲。【我】\n\n无。\n\n乙。", mine)).toBe(false);
    expect(keepsServerChanges(sent, server, "甲。【我】\n\n无。\n\n无。\n\n乙。", mine)).toBe(true);
  });
});

describe("增量审查建议 3：同文字块平移要求邻居锚点", () => {
  it("S=[无, 无(改), C] → T=[无, C改, 无*]：不平移（交显式基准）", () => {
    const sent = "无。\n\n无。\n\nC。";
    const mine = "无。\n\n无。【我】\n\nC。";
    const server = "无。\n\nC改。\n\n无。";
    expect(rebaseBlocks(sent, mine, server)).toBeNull();
  });
  it("同文字块两侧有唯一邻段锚定时照常平移", () => {
    const sent = "甲。\n\n无。\n\n乙。\n\n无。\n\n丙。";
    const mine = "甲。\n\n无。【我】\n\n乙。\n\n无。\n\n丙。";
    const server = "甲。\n\n无。\n\n乙（改）。\n\n无。\n\n丙。";
    expect(rebaseBlocks(sent, mine, server)).toBe("甲。\n\n无。【我】\n\n乙（改）。\n\n无。\n\n丙。");
  });
});
