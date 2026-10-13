import { describe, expect, it } from "vitest";
import history from "./components/DocumentHistory.vue?raw";
import skillSettings from "./components/DocumentSkillSettings.vue?raw";
import workbench from "./views/DocumentWorkbenchView.vue?raw";

// 复审：这些文件在主干里是 CRLF，.gitattributes 设为 -text 按原样存取；编辑器若混入 LF 行，这里会失败。
const CRLF_FILES: Record<string, string> = {
  "views/DocumentWorkbenchView.vue": workbench,
  "components/DocumentHistory.vue": history,
  "components/DocumentSkillSettings.vue": skillSettings,
};

describe("换行固定为 CRLF", () => {
  for (const [name, text] of Object.entries(CRLF_FILES)) {
    it(name, () => {
      expect(text.length).toBeGreaterThan(1000);
      const bare = text.split("\n").slice(0, -1).filter((line: string) => !line.endsWith("\r")).length;
      expect(bare).toBe(0);
    });
  }
});
