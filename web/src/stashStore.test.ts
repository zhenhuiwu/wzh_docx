import { describe, expect, it } from "vitest";
import { addStashEntry, parseStash, serializeStash, stashAll } from "./stashStore";

describe("暂存只追加（28 N-5）", () => {
  it("fresh 新起一条，不替换旧的", () => {
    let list = addStashEntry([], "旧暂存XYZ", "p1", 1000);
    list = addStashEntry(list, "附录正文。B改", "", 2000, true);
    expect(list.map(item => item.text)).toEqual(["旧暂存XYZ", "附录正文。B改"]);
    expect(stashAll(list)).toBe("旧暂存XYZ\n\n附录正文。B改");
  });
  it("同一块 20 秒内连续输入并进最后一条；换块或超时新起一条", () => {
    let list = addStashEntry([], "A", "p1", 0);
    list = addStashEntry(list, "B", "p1", 1000);
    list = addStashEntry(list, "C", "p2", 2000);
    list = addStashEntry(list, "D", "p2", 30000);
    expect(list.map(item => item.text)).toEqual(["AB", "C", "D"]);
    expect(list[1]).toEqual({ text: "C", source: "p2", at: 2000 });
  });
  it("两份草稿拆分各自进暂存，都保留", () => {
    let list = addStashEntry([], "草稿一的B", "", 1, true);
    list = addStashEntry(list, "草稿二的C", "", 2, true);
    expect(list).toHaveLength(2);
  });
  it("读写：新格式往返，旧格式 {text,source,at} 能读，坏数据为空", () => {
    const list = addStashEntry(addStashEntry([], "甲", "p1", 1), "乙", "p2", 2);
    expect(parseStash(serializeStash(list))).toEqual(list);
    expect(parseStash("{\"text\":\"越权\",\"source\":\"p9\",\"at\":5}")).toEqual([{ text: "越权", source: "p9", at: 5 }]);
    expect(parseStash("不是 JSON")).toEqual([]);
    expect(parseStash(null)).toEqual([]);
  });
});
