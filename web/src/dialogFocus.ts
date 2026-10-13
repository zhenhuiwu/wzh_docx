/**
 * 25 M-4：模态对话框的初始焦点与焦点归还。在组件 setup 里调用：
 * 记下打开前的焦点，挂载后 showModal 并聚焦 initial（选择器，找不到时用对话框自身的默认焦点），
 * 卸载时（关闭、取消、成功都会卸载）把焦点还给打开前的元素；该元素已不在页面上时退到 fallback 选择器。
 */
import { nextTick, onBeforeUnmount, onMounted, type Ref } from "vue";

const FOCUSABLE = "button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])";

export function restoreFocus(previous: Element | null, fallback?: string) {
  const target = previous instanceof HTMLElement && previous.isConnected && previous !== document.body
    ? previous : fallback ? document.querySelector<HTMLElement>(fallback) : null;
  target?.focus();
}

export function useDialogFocus(dialog: Ref<HTMLDialogElement | null>, options: { initial?: string; fallback?: string } = {}) {
  const previous = typeof document !== "undefined" ? document.activeElement : null;
  onMounted(() => void nextTick(() => {
    const element = dialog.value;
    if (!element) return;
    if (!element.open) element.showModal();
    if (options.initial) {
      const found = element.querySelector<HTMLElement>(options.initial);
      const target = found && !found.matches(FOCUSABLE) ? found.querySelector<HTMLElement>(FOCUSABLE) : found;
      target?.focus();
    }
  }));
  onBeforeUnmount(() => {
    const fallback = options.fallback;
    // 卸载后 dialog 关闭，浏览器会把焦点放回 body；等 DOM 更新完再归还
    void nextTick(() => restoreFocus(previous, fallback));
  });
}
