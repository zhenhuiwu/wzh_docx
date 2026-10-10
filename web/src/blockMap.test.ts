import { describe, expect, it } from "vitest";
import { caretBlockIndex, mapBlockIndex } from "./workbenchStats";

/** 暴力基准：枚举全部 LCS 最优对齐，返回 before[k] 在其中的去向集合（-1 表示某个最优对齐里 k 不配对）。 */
function bruteTargets(a: string[], b: string[], k: number): Set<number> {
  let best = -1;
  let targets = new Set<number>();
  const walk = (i: number, j: number, size: number, target: number) => {
    if (size + Math.min(a.length - i, b.length - j) < best) return;
    if (i === a.length) {
      if (size > best) { best = size; targets = new Set([target]); }
      else if (size === best) targets.add(target);
      return;
    }
    walk(i + 1, j, size, target);  // a[i] 不配对
    for (let jj = j; jj < b.length; jj++) if (a[i] === b[jj]) walk(i + 1, jj + 1, size + 1, i === k ? jj : target);
  };
  walk(0, 0, 0, -1);
  return targets;
}
function rng(seed: number) { let s = seed >>> 0; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296); }

describe("最后一审建议 1：只有所有最优对齐都把该段配到同一位置才写回", () => {
  it("审查员的两个最小反例都放弃", () => {
    expect(mapBlockIndex(["无。", "B"], ["无。", "无。", "B改"], 0)).toBe(-1);
    expect(mapBlockIndex(["3.2 性能", "无。", "3.3 接口"], ["3.2 性能", "无。", "无。", "3.3 接口（对方改）"], 1)).toBe(-1);
  });
  it("确定的情况照常写回", () => {
    const before = ["3 需求", "3.1 功能", "无。", "3.2 性能", "无。", "3.3 接口"];
    expect(mapBlockIndex(before, ["新段", ...before], 4)).toBe(5);
    expect(mapBlockIndex(before, ["新段一", "新段二", ...before], 4)).toBe(6);
    expect(mapBlockIndex(before, before.slice(1), 4)).toBe(3);
    expect(mapBlockIndex(["甲", "无。", "乙", "无。", "丙改"], ["张工新插入", "甲", "无。", "乙", "无。", "丙改"], 3)).toBe(4);
  });
  it("随机对拍（暴力枚举全部最优对齐为基准）：0 次写错，也不漏掉确定的位置", () => {
    const r = rng(20261010);
    const alphabets = [["无。", "无。", "A", "B", "C"], ["无。", "A"], ["无。", "无。", "无。", "A", "B", "C", "D", "E"]];
    let wrong = 0, missed = 0, written = 0;
    const examples: string[] = [];
    for (let t = 0; t < 30000; t++) {
      const alpha = alphabets[t % alphabets.length];
      const n = 1 + Math.floor(r() * 7), m = Math.floor(r() * 8);
      const a = Array.from({ length: n }, () => alpha[Math.floor(r() * alpha.length)]);
      const b = Array.from({ length: m }, () => alpha[Math.floor(r() * alpha.length)]);
      const k = Math.floor(r() * n);
      const got = mapBlockIndex(a, b, k);
      const truth = bruteTargets(a, b, k);
      const certain = truth.size === 1 && !truth.has(-1) ? [...truth][0] : -1;
      if (got >= 0) written += 1;
      if (got >= 0 && got !== certain) { wrong += 1; if (examples.length < 3) examples.push(JSON.stringify({ a, b, k, got, truth: [...truth] })); }
      if (got < 0 && certain >= 0) missed += 1;
    }
    expect(examples).toEqual([]);
    expect(wrong).toBe(0);
    expect(missed).toBe(0);
    expect(written).toBeGreaterThan(1000);
  });
});

describe("最后一审建议 2：大文档不再一律放弃", () => {
  const big = (count: number) => Array.from({ length: count }, (_, i) => (i % 7 === 3 ? "无。" : `第 ${i} 段：正文内容。`));
  it("3000 段：精确算法（O(m) 内存）上方插入后仍能定位，且 1 秒内完成", () => {
    const before = big(3000);
    const after = ["新插入的一段。", ...before];
    const k = before.indexOf("第 2000 段：正文内容。");
    const started = performance.now();
    expect(mapBlockIndex(before, after, k)).toBe(k + 1);
    expect(performance.now() - started).toBeLessThan(1000);
  });
  it("8000 段：超上限时去掉前后相同的段落再算", () => {
    const before = big(8000);
    const after = [...before];
    after.splice(4000, 0, "中间新插入的一段。");
    after[4100] = after[4100] + "（对方改）";
    // 光标在改动区域内的唯一文字段
    const k = 4050;
    expect(mapBlockIndex(before, after, k, 1_000_000)).toBe(k + 1);
    // 在去掉的前缀里的唯一文字段：原位
    expect(mapBlockIndex(before, after, 12, 1_000_000)).toBe(12);
    // 前缀里的重复“无。”：不确定 → 放弃
    expect(mapBlockIndex(before, after, 3, 1_000_000)).toBe(-1);
    // 后缀里的唯一文字段：按末尾对齐
    expect(mapBlockIndex(before, after, 7000, 1_000_000)).toBe(7001);
  });
});

/** 并入审查员 /tmp/p6r/web/src/zz_r6_claim.test.ts：用独立写法的前缀/后缀 DP 求出 before[k] 在所有 LCS 里的可能去向（可不配对时含 -1）。 */
function feasible(a: string[], b: string[], k: number): Set<number> {
  const n = a.length, m = b.length;
  const P = Array.from({ length: n + 1 }, () => new Int32Array(m + 1));
  for (let i = 1; i <= n; i++) for (let j = 1; j <= m; j++) P[i][j] = a[i - 1] === b[j - 1] ? P[i - 1][j - 1] + 1 : Math.max(P[i - 1][j], P[i][j - 1]);
  const S = Array.from({ length: n + 2 }, () => new Int32Array(m + 2));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) S[i][j] = a[i] === b[j] ? S[i + 1][j + 1] + 1 : Math.max(S[i + 1][j], S[i][j + 1]);
  const L = P[n][m], out = new Set<number>();
  for (let j = 0; j < m; j++) if (a[k] === b[j] && P[k][j] + 1 + S[k + 1][j + 1] === L) out.add(j);
  for (let j = 0; j <= m; j++) if (P[k][j] + S[k + 1][j] === L) { out.add(-1); break; }
  return out;
}

describe("审查员示例测试（zz_r6_claim）：‘两种极端对齐一致’之外的反例", () => {
  it("最小反例：feasible 不唯一，mapBlockIndex 放弃", () => {
    const before = ["3.2 性能", "无。", "3.3 接口"], after = ["3.2 性能", "无。", "无。", "3.3 接口（对方改）"];
    expect([...feasible(before, after, 1)].length).toBeGreaterThan(1);
    expect(mapBlockIndex(before, after, 1)).toBe(-1);
    const b2 = ["无。", "B"], a2 = ["无。", "无。", "B改"];
    expect([...feasible(b2, a2, 0)].length).toBeGreaterThan(1);
    expect(mapBlockIndex(b2, a2, 0)).toBe(-1);
  });
  it("随机 100000 次（种子 4242）：返回 j 时 LCS 去向唯一且就是 j", () => {
    const r = rng(4242), alpha = ["无。", "无。", "A", "B", "C"];
    let returned = 0, ambiguous = 0; const ex: string[] = [];
    for (let t = 0; t < 100000; t++) {
      const n = 2 + Math.floor(r() * 7), m = 1 + Math.floor(r() * 8);
      const a = Array.from({ length: n }, () => alpha[Math.floor(r() * alpha.length)]);
      const b = Array.from({ length: m }, () => alpha[Math.floor(r() * alpha.length)]);
      const k = Math.floor(r() * n), j = mapBlockIndex(a, b, k);
      const f = feasible(a, b, k);
      const certain = f.size === 1 && !f.has(-1);
      if (j < 0) { if (certain) { ambiguous++; if (ex.length < 3) ex.push(JSON.stringify({ a, b, k, j, f: [...f] })); } continue; }
      returned++;
      if (!certain || !f.has(j)) { ambiguous++; if (ex.length < 3) ex.push(JSON.stringify({ a, b, k, j, f: [...f] })); }
    }
    expect(ex).toEqual([]);
    expect(ambiguous).toBe(0);
    expect(returned).toBeGreaterThan(10000);
  });
});

describe("最后一审建议 2：写回放弃时光标尽量放到原文字仍唯一的段", () => {
  it("对齐不确定但文字前后都唯一：放到那一段；文字重复或已改：不放", () => {
    expect(mapBlockIndex(["甲", "乙", "丙"], ["乙", "甲", "丙"], 0)).toBe(-1);
    expect(caretBlockIndex(["甲", "乙", "丙"], ["乙", "甲", "丙"], 0)).toBe(1);
    expect(caretBlockIndex(["无。", "B"], ["无。", "无。", "B改"], 0)).toBe(-1);
    expect(caretBlockIndex(["无。", "B"], ["无。", "无。", "B改"], 1)).toBe(-1);
    expect(caretBlockIndex(["甲", "乙"], ["甲", "乙"], 1)).toBe(1);
  });
  it("8000 段、首尾互换（去掉前后相同段后仍超上限）：写回放弃，光标仍能放回", () => {
    const before = Array.from({ length: 8000 }, (_, i) => `第${i}段`);
    const after = [before[7999], ...before.slice(1, 7999), before[0]];
    expect(mapBlockIndex(before, after, 4000, 1_000_000)).toBe(-1);
    expect(caretBlockIndex(before, after, 4000, 1_000_000)).toBe(4000);
    expect(caretBlockIndex(before, after, 0, 1_000_000)).toBe(7999);
  });
});
