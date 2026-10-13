<script setup lang="ts">
/** Spec 30 §8-9: leaving with unresolved AI suggestions asks first (they are not saved). */
import { nextTick, onMounted, ref } from "vue";

defineProps<{ count: number }>();
const emit = defineEmits<{ stay: []; leave: [] }>();
const first = ref<HTMLButtonElement | null>(null);
onMounted(() => void nextTick(() => first.value?.focus()));
</script>

<template>
  <div class="dialog-backdrop" @keydown.esc="emit('stay')">
    <section class="dialog card" role="alertdialog" aria-modal="true" aria-labelledby="ai-leave-title" aria-describedby="ai-leave-body" data-testid="ai-leave-dialog">
      <h2 id="ai-leave-title">还有 {{ count }} 处 AI 建议未处理</h2>
      <p id="ai-leave-body">AI 建议还没有接受或拒绝，不会保存。离开后这些建议会丢失，正文保持原样。</p>
      <footer>
        <button ref="first" type="button" class="primary" @click="emit('stay')">留在本页处理</button>
        <button type="button" class="ghost" data-testid="ai-leave-confirm" @click="emit('leave')">放弃建议并离开</button>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.dialog-backdrop { position:fixed; inset:0; z-index:50; display:flex; align-items:center; justify-content:center; background:color-mix(in srgb, var(--text-primary) 32%, transparent); }
.dialog { display:grid; gap:var(--space-3); width:min(480px, calc(100vw - var(--space-7))); padding:var(--space-5); background:var(--bg-surface); border:1px solid var(--border-default); border-radius:var(--radius-sheet); box-shadow:var(--shadow-3); font-family:var(--font); }
h2 { margin:0; font-size:var(--text-lg); font-weight:600; color:var(--text-primary); }
p { margin:0; font-size:var(--text-sm); line-height:1.6; color:var(--text-secondary); }
footer { display:flex; justify-content:flex-end; gap:var(--space-2); margin-top:var(--space-2); }
</style>
