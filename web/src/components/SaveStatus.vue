<script setup lang="ts">
import type { Capsule } from "../saveModel";

defineProps<{ state: Capsule; label: string; detail: string }>();
const emit = defineEmits<{ save: []; retry: []; details: [] }>();
</script>

<template>
  <span class="savepill" :class="state" :role="state === 'blocked' ? 'alert' : 'status'" aria-live="polite" :title="detail" data-testid="save-status">
    <svg v-if="state === 'clean'" class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12.5l5 5L20 6.5" /></svg>
    <span v-else-if="state === 'saving'" class="spin" aria-hidden="true"></span>
    <span v-else-if="state === 'dirty'" class="dot" aria-hidden="true"></span>
    <svg v-else-if="state === 'retrying'" class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11a8 8 0 1 0-2.3 5.7" /><path d="M20 5v6h-6" /></svg>
    <svg v-else-if="state === 'offline'" class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3l18 18M8.5 16.5a5 5 0 0 1 7 0M5 13a10 10 0 0 1 5-2.6M19 13a10 10 0 0 0-2.2-1.6M2 9.5a15 15 0 0 1 5-3M22 9.5A15 15 0 0 0 11 5.1" /></svg>
    <svg v-else-if="state === 'blocked'" class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5l9.5 16.5h-19z" /><path d="M12 10v4.5M12 17.5v.01" /></svg>
    <svg v-else class="i" viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="11" width="14" height="9" rx="1.5" /><path d="M8 11V8a4 4 0 0 1 8 0v3" /></svg>
    <span>{{ label }}</span>
    <button v-if="state === 'dirty'" type="button" class="pill-action" @click="emit('save')">保存</button>
    <button v-else-if="state === 'retrying' || state === 'offline'" type="button" class="pill-action" @click="emit('retry')">立即重试</button>
    <button v-else-if="state === 'blocked'" type="button" class="pill-action" @click="emit('details')">查看原因</button>
  </span>
</template>

<style scoped>
/* 类名与变量取自 wzh_collab/mockups（tokens.css/components.css）；尺寸走 --pill-h / --icon-sm。 */
.savepill { display:inline-flex; align-items:center; gap:6px; height:var(--pill-h); padding:0 10px 0 var(--space-2); border-radius:var(--radius-pill); font-size:var(--text-xs); white-space:nowrap; flex:none; }
.savepill .i { width:var(--icon-sm); height:var(--icon-sm); flex:none; stroke:currentColor; fill:none; stroke-width:1.6; stroke-linecap:round; stroke-linejoin:round; }
.savepill.clean { color:var(--text-secondary); background:var(--gray-100); }
.savepill.clean .i { color:var(--success-600); }
.savepill.dirty { color:var(--text-secondary); background:var(--gray-100); }
.savepill.saving { color:var(--brand-600); background:var(--brand-50); }
.savepill.retrying, .savepill.offline { color:var(--warning-600); background:var(--warning-50); }
.savepill.blocked { color:var(--danger-600); background:var(--danger-50); }
.savepill.readonly { color:var(--text-secondary); background:var(--gray-100); }
.dot { width:6px; height:6px; border-radius:var(--radius-pill); background:currentColor; }
.spin { width:12px; height:12px; border-radius:var(--radius-pill); border:2px solid var(--brand-200); border-top-color:var(--brand-600); animation:spin 0.8s linear infinite; }
@keyframes spin { to { transform:rotate(360deg); } }
.pill-action { height:auto; min-height:0; padding:0; margin-left:2px; border:0; background:transparent; color:inherit; font:inherit; font-weight:var(--weight-semibold); text-decoration:underline; cursor:pointer; }
.pill-action:focus-visible { outline:var(--focus-outline); outline-offset:2px; border-radius:var(--radius-control); }
</style>
