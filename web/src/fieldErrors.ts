import { nextTick, ref } from "vue";

/**
 * 弹窗表单的字段级校验（02 评审 §5.5，08 验收 M2）：
 * 错误字段描边标红、字段下方 12px 红字，配 aria-invalid / aria-describedby；提交失败聚焦第一个错误字段。
 * 表单级提示条 .form-alert 只留给接口错误。参照 WritingSkillsView 的做法。
 *
 * 用法：
 *   const { errors, errorId, invalid, report, clear } = useFieldErrors<"name" | "category">("tpl");
 *   <input id="tpl-name" v-bind="invalid('name')" …><p v-if="errors.name" :id="errorId('name')" class="field-error">…</p>
 *   if (await report({ name: name ? "" : "请填写模板名称。" })) return;
 */
export function useFieldErrors<K extends string>(prefix: string) {
  const errors = ref<Partial<Record<K, string>>>({});
  const errorId = (field: K) => `${prefix}-${field}-error`;

  /** 绑到输入控件上；hint 为字段原有说明的 id，出错时一并保留在 aria-describedby 里。 */
  function invalid(field: K, hint?: string) {
    const message = errors.value[field];
    const describedBy = [hint, message ? errorId(field) : ""].filter(Boolean).join(" ");
    return {
      class: { "is-invalid": Boolean(message) },
      "aria-invalid": message ? ("true" as const) : undefined,
      "aria-describedby": describedBy || undefined,
    };
  }

  function clear(field?: K) {
    if (field) {
      const next = { ...errors.value };
      delete next[field];
      errors.value = next;
    } else {
      errors.value = {};
    }
  }

  /** 写入校验结果（空字符串视为通过）；有错误时聚焦第一个出错字段（输入框 id 约定为 `${prefix}-${field}`），返回是否有错。 */
  async function report(result: Partial<Record<K, string>>): Promise<boolean> {
    const next: Partial<Record<K, string>> = {};
    for (const [field, message] of Object.entries(result) as [K, string | undefined][]) if (message) next[field] = message;
    errors.value = next;
    const first = Object.keys(next)[0];
    if (!first) return false;
    await nextTick();
    document.getElementById(`${prefix}-${first}`)?.focus();
    return true;
  }

  return { errors, errorId, invalid, clear, report };
}
