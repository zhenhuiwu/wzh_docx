<script setup lang="ts">
/** Spec 30 §4.5 / §6 AiStatusChip: AI status next to the save capsule (进行中 / 待处理 / 完成 / 失败 / 已停止). */
import { computed, onUnmounted, ref, watch } from "vue";

const props = defineProps<{ busy: boolean; pending: number; error: string; stopped: boolean; progress: string; target: string }>();
const finished = ref<"" | "done" | "failed">("");
let timer: number | undefined;

function flashFinished(value: "done" | "failed") {
  finished.value = value;
  window.clearTimeout(timer);
  timer = window.setTimeout(() => { finished.value = ""; }, 6000);
}
watch(() => props.pending, (now, before) => { if (before > 0 && now === 0 && !props.busy) flashFinished("done"); });
watch(() => props.busy, (now, before) => {
  if (before && !now) {
    if (props.error && !props.stopped) flashFinished("failed");
    else if (!props.pending && !props.stopped) flashFinished("done");
  }
});
watch(() => props.error, value => { if (value && !props.busy && !props.stopped) flashFinished("failed"); });
onUnmounted(() => window.clearTimeout(timer));

const state = computed(() => props.busy ? "running" : props.pending ? "pending" : props.stopped ? "stopped" : finished.value || "");
const label = computed(() => {
  switch (state.value) {
    case "running": return `正在写${props.target ? ` ${props.target}` : ""}${props.progress ? ` · ${props.progress}` : ""}`;
    case "pending": return `AI 建议 ${props.pending} 处待处理`;
    case "stopped": return "AI 已停止";
    case "done": return "AI 已完成";
    case "failed": return "AI 编写失败";
    default: return "";
  }
});
</script>

<template>
  <span v-if="state" class="ai-chip" :class="state" role="status" aria-live="polite" data-testid="ai-status-chip" :title="state === 'failed' ? error : undefined">
    <span class="dot" aria-hidden="true"></span>{{ label }}
  </span>
</template>

<style scoped>
.ai-chip { display:inline-flex; align-items:center; gap:var(--space-1); max-width:320px; height:24px; padding:0 var(--space-2); border-radius:var(--radius-control); background:var(--stamp-wash); color:var(--stamp); font-family:var(--font); font-size:var(--text-xs); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.dot { width:6px; height:6px; flex:none; border-radius:50%; background:currentColor; }
.running .dot { animation:ai-pulse 1.2s ease-in-out infinite; }
.done { background:var(--ok-wash); color:var(--ok); }
.failed { background:var(--fault-wash); color:var(--fault); }
.stopped { background:var(--bg-subtle); color:var(--text-secondary); }
@keyframes ai-pulse { 50% { opacity:.3; } }
</style>
