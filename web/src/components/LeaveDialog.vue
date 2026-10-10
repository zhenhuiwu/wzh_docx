<script setup lang="ts">
import { nextTick, onMounted, ref } from "vue";
import type { LeaveDecision } from "../saveModel";

defineProps<{ mode: Exclude<LeaveDecision, "leave">; count: number }>();
const emit = defineEmits<{ download: []; stay: []; leave: []; downloadLeave: [] }>();
const first = ref<HTMLButtonElement | null>(null);
onMounted(() => void nextTick(() => first.value?.focus()));
</script>

<template>
  <div class="dialog-backdrop" @keydown.esc="emit('stay')">
    <section class="dialog card" role="alertdialog" aria-modal="true" aria-labelledby="leave-title" aria-describedby="leave-body">
      <h2 id="leave-title">{{ mode === 'dialog' ? '还有未保存的修改' : '你的修改未能保存到本机浏览器' }}</h2>
      <p v-if="mode === 'dialog'" id="leave-body">还有 {{ count }} 处修改未保存到服务器。修改已保存在这台电脑的浏览器中，下次打开本文档会提示恢复。</p>
      <p v-else id="leave-body">还有 {{ count }} 处修改未保存到服务器，且未能写入本机浏览器，离开前请先下载。</p>
      <footer>
        <template v-if="mode === 'dialog'">
          <button ref="first" type="button" class="ghost" @click="emit('download')">下载我的修改</button>
          <button type="button" class="ghost" @click="emit('stay')">留在本页</button>
          <button type="button" class="primary" data-testid="leave-confirm" @click="emit('leave')">离开</button>
        </template>
        <template v-else>
          <button ref="first" type="button" class="primary" @click="emit('downloadLeave')">下载后离开</button>
          <button type="button" class="ghost" @click="emit('stay')">留在本页</button>
        </template>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.dialog-backdrop { position:fixed; inset:0; z-index:50; display:flex; align-items:center; justify-content:center; background:color-mix(in srgb, var(--ink) 32%, transparent); }
.dialog { display:grid; gap:var(--space-3); width:min(480px, calc(100vw - var(--space-7))); padding:var(--space-5); background:var(--bg-surface, var(--sheet)); border:1px solid var(--border-default, var(--rule)); border-radius:var(--radius-sheet); box-shadow:var(--shadow-3, 0 var(--space-4) var(--space-6) var(--rule-strong)); font-family:var(--font); }
h2 { margin:0; font-size:var(--text-lg, var(--text-md)); font-weight:600; color:var(--text-primary, var(--ink)); }
p { margin:0; font-size:var(--text-sm); line-height:1.6; color:var(--text-secondary, var(--ink-secondary)); }
footer { display:flex; justify-content:flex-end; gap:var(--space-2); margin-top:var(--space-2); }
button:focus-visible { outline:var(--space-1) solid var(--focus-ring); outline-offset:var(--space-1); }
</style>
