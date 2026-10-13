<script setup lang="ts">
/**
 * 中文文件选择控件（08 验收 S3）：替代浏览器原生 “Choose File / No file chosen”。
 * 真实的 <input type="file"> 仍在（视觉隐藏、可聚焦、可被标签/测试定位），旁边显示“选择文件”按钮和文件名。
 * 放在 <label> 里使用；id、aria-*、accept、required 等属性透传到原生 input。
 */
import { ref, useAttrs, watch } from "vue";

defineOptions({ inheritAttrs: false });

const props = withDefaults(defineProps<{
  file?: File | null;
  disabled?: boolean;
  buttonText?: string;
  emptyText?: string;
  invalid?: boolean;
}>(), { file: null, disabled: false, buttonText: "选择文件", emptyText: "未选择文件", invalid: false });

const emit = defineEmits<{ change: [file: File | null, event: Event] }>();
const attrs = useAttrs();
const input = ref<HTMLInputElement | null>(null);

function inputAttrs() {
  const { class: _class, style: _style, ...rest } = attrs;
  return rest;
}

function onChange(event: Event) {
  emit("change", (event.target as HTMLInputElement).files?.[0] ?? null, event);
}

function pick() {
  if (!props.disabled) input.value?.click();
}

// 父组件清空文件后同步清空原生 input，便于再次选择同一个文件。
watch(() => props.file, file => { if (!file && input.value) input.value.value = ""; });

defineExpose({ input });
</script>

<template>
  <span class="file-picker" :class="[$attrs.class, { 'is-disabled': disabled, 'is-invalid': invalid }]" :style="$attrs.style as any">
    <input ref="input" v-bind="inputAttrs()" class="file-picker-input" type="file" :disabled="disabled" @change="onChange" />
    <span class="file-picker-button" aria-hidden="true" @click.prevent="pick">
      <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 11V3M4.5 6.5 8 3l3.5 3.5M3 13h10" /></svg>{{ buttonText }}
    </span>
    <span class="file-picker-name" :class="{ 'is-empty': !file }" :title="file?.name">{{ file ? file.name : emptyText }}</span>
  </span>
</template>

<style scoped>
.file-picker {
  position: relative;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
  min-height: var(--control-h);
  padding: 3px var(--space-1);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-control);
  background: var(--bg-surface);
  font-size: var(--text-sm);
  font-weight: var(--weight-regular);
}

.file-picker.is-invalid { border-color: var(--danger-600); }
.file-picker.is-disabled { background: var(--gray-50); }

.file-picker-input {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  clip-path: inset(50%);
  white-space: nowrap;
  opacity: 0;
}

.file-picker-button {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: var(--space-1);
  height: var(--control-h-sm);
  padding: 0 10px;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-control);
  background: var(--bg-surface);
  color: var(--text-primary);
  cursor: pointer;
  user-select: none;
}

.file-picker-button svg {
  width: 14px;
  height: 14px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.5;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.file-picker-button:hover { background: var(--gray-50); }
.is-disabled .file-picker-button { color: var(--text-tertiary); cursor: not-allowed; background: var(--gray-50); }

.file-picker-input:focus-visible + .file-picker-button {
  outline: var(--focus-outline);
  outline-offset: 2px;
}

.file-picker:has(.file-picker-input:focus-visible) { border-color: var(--brand-600); box-shadow: var(--focus-shadow-control); }
.file-picker.is-invalid:has(.file-picker-input:focus-visible) { border-color: var(--danger-700); box-shadow: var(--focus-shadow-danger); }

.file-picker-name {
  min-width: 0;
  overflow: hidden;
  color: var(--text-primary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-picker-name.is-empty { color: var(--text-tertiary); }
</style>
