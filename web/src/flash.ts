import { ref } from "vue";

/** 跨页面的一次性成功提示（如“密码已修改”），由 AppShell 显示 4 秒。 */
export const flashMessage = ref("");
let timer = 0;

export function flash(message: string, ms = 4000) {
  flashMessage.value = message;
  window.clearTimeout(timer);
  timer = window.setTimeout(() => { flashMessage.value = ""; }, ms);
}
