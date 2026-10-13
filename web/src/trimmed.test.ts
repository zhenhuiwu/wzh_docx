import { describe, expect, it } from "vitest";
import { caretBlockIndex, mapBlockIndex, rebaseBlocks } from "./workbenchStats";

function rng(seed: number) { let s = seed >>> 0; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296); }

describe("增量审查 M2：先去掉前后相同段再算，结果与整篇精确计算一致（或放弃）", () => {
  it("随机 20000 次：裁剪路径返回的位置必与整篇精确结果相同", () => {
    const r = rng(9091), alpha = ["无。", "无。", "A", "B", "C", "D"];
    let returned = 0, differ = 0; const ex: string[] = [];
    for (let t = 0; t < 20000; t++) {
      const n = 4 + Math.floor(r() * 14);
      const a = Array.from({ length: n }, () => alpha[Math.floor(r() * alpha.length)]);
      const b = a.slice();
      const ops = 1 + Math.floor(r() * 3);
      for (let o = 0; o < ops; o++) {
        const at = Math.floor(r() * (b.length + 1)), kind = r();
        if (kind < 0.4) b.splice(at, 0, alpha[Math.floor(r() * alpha.length)]);
        else if (kind < 0.7 && b.length > 1) b.splice(Math.min(at, b.length - 1), 1);
        else if (b.length) b[Math.min(at, b.length - 1)] += "改";
      }
      const k = Math.floor(r() * n);
      const exact = mapBlockIndex(a, b, k, 36_000_000);
      const trimmed = mapBlockIndex(a, b, k, 1_000_000, 0);
      if (trimmed >= 0) { returned++; if (trimmed !== exact) { differ++; if (ex.length < 3) ex.push(JSON.stringify({ a, b, k, exact, trimmed })); } }
    }
    expect(ex).toEqual([]);
    expect(differ).toBe(0);
    expect(returned).toBeGreaterThan(5000);
  });
});

describe("增量审查 M2：平移每次按键的计算远低于 50ms（性能护栏）", () => {
  for (const N of [3000, 5000]) {
    it(`${N} 段：单段打字、对方在开头插段并改中间段`, () => {
      const blocks = Array.from({ length: N }, (_, i) => i % 7 === 3 ? "无。" : `第${i}段 内容内容内容`);
      const server = ["对方插入的新段", ...blocks]; server[N / 2] = server[N / 2] + "（对方改）";
      const k = Math.floor(N * 0.8);
      const sent = blocks.join("\n\n"), serverText = server.join("\n\n");
      // 预热一次，测 20 次按键：护栏看中位数（偶发的 GC/调度抖动不该让用例失败，J7-L），最坏一次只防数量级退化
      rebaseBlocks(sent, sent.replace(blocks[k], blocks[k] + "字"), serverText);
      // 与编辑区的 domPair 一样：dom 是编辑区文字，value 是平移后的正文，每次按键更新
      const times: number[] = [];
      let mine = blocks.slice(), value = serverText;
      for (let i = 0; i < 20; i++) {
        const before = mine.join("\n\n");
        mine = mine.slice(); mine[k] += "字";
        const started = performance.now();
        const out = rebaseBlocks(before, mine.join("\n\n"), value);
        times.push(performance.now() - started);
        expect(out).not.toBeNull();
        value = out!;
      }
      expect(value).toContain(blocks[k] + "字".repeat(20));
      expect(value).toContain("（对方改）");
      const sorted = times.slice().sort((a, b) => a - b);
      expect(sorted[Math.floor(sorted.length / 2)]).toBeLessThan(50);
      expect(sorted[sorted.length - 1]).toBeLessThan(250);
      // 50 段分散改动（每段都唯一可定位）也很快
      const multi = blocks.slice(); for (let i = 0; i < 50; i++) multi[100 + i * 3] += "x";
      // 单次计时取 3 次中的最快一次，避开偶发抖动
      const fastest = (run: () => void) => Math.min(...[0, 1, 2].map(() => { const t0 = performance.now(); run(); return performance.now() - t0; }));
      expect(fastest(() => expect(rebaseBlocks(sent, multi.join("\n\n"), serverText)).not.toBeNull())).toBeLessThan(200);
      // 写回/光标换算同样先裁剪
      expect(fastest(() => expect(caretBlockIndex(blocks, server, k)).toBe(k + 1))).toBeLessThan(200);
    });
  }
});
