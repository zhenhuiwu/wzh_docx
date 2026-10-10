import { describe, expect, it } from "vitest";
import { cellInline, escapeMarkdownText, escapeParagraphStart, finishMarkdown, SPACE, TAB } from "./editorMarkdown";

describe("editorMarkdown 纯函数（01 §6.0）", () => {
  it("表格分隔行与服务器写法一致", () => {
    expect(finishMarkdown("| a | b |\n| --- | --- |\n| 1 | 2 |")).toBe("| a | b |\n|---|---|\n| 1 | 2 |");
  });
  it("还原空格与制表符占位符", () => {
    expect(finishMarkdown(`密${SPACE}${SPACE}级：${TAB}x`)).toBe("密  级：\tx");
  });
  it("普通段落行首转义与 docx_parser 一致", () => {
    expect(escapeParagraphStart("1. 不是列表")).toBe("1\\. 不是列表");
    expect(escapeParagraphStart(`${SPACE}12.x`)).toBe(`${SPACE}12\\.x`);
    expect(escapeParagraphStart("- 也不是")).toBe("\\- 也不是");
    expect(escapeParagraphStart(`+${SPACE}a`)).toBe(`\\+${SPACE}a`);
    expect(escapeParagraphStart("---")).toBe("\\-\\-\\-");
    expect(escapeParagraphStart("产品型号-XXXX")).toBe("产品型号-XXXX");
  });
  it("文字转义含竖线", () => {
    expect(escapeMarkdownText("a|b*c#[d]")).toBe("a\\|b\\*c\\#\\[d\\]");
  });
  it("单元格内容折成单行", () => {
    expect(cellInline("\n第一行 \n\n 第二行\n")).toBe("第一行<br>第二行");
  });
});
