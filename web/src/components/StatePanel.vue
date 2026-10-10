<script setup lang="ts">
/** 页面级三态（加载中 / 空 / 出错 / 无权限）的统一外观；按钮通过默认插槽传入。 */
withDefaults(defineProps<{
  kind?: "loading" | "empty" | "error" | "denied";
  title?: string;
  text?: string;
}>(), { kind: "empty", title: "", text: "" });
</script>

<template>
  <div class="state-panel" :role="kind === 'error' ? 'alert' : kind === 'loading' ? 'status' : undefined" :aria-live="kind === 'loading' ? 'polite' : undefined">
    <span v-if="kind === 'loading'" class="spinner" aria-hidden="true"></span>
    <svg v-else class="state-icon ico" viewBox="0 0 32 32" aria-hidden="true">
      <path v-if="kind === 'error'" d="M16 4.5 29 27.5H3zM16 13v6.5M16 23.2v.3" />
      <path v-else-if="kind === 'denied'" d="M9 14.5V10a7 7 0 0 1 14 0v4.5M6.5 14.5h19v13h-19zM16 19.5v3" />
      <path v-else d="M5.5 9.5h8l2.5 3h10.5v13h-21zM5.5 9.5v-3h7" />
    </svg>
    <strong v-if="title">{{ title }}</strong>
    <p v-if="text">{{ text }}</p>
    <slot />
  </div>
</template>
