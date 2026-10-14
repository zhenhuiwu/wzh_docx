<script setup lang="ts">
/** QA 复验 16 P1-1：恢复草稿后没保存的“他人负责部分”的内容——逐段查看、复制（内容留在本机，不在这里删）。 */
import { nextTick, ref, watch } from "vue";
import type { UnsavedPart } from "../unsavedOthers";
import { partWhere } from "../assignmentModel";

const props = defineProps<{ open: boolean; parts: UnsavedPart[] }>();
const emit = defineEmits<{ (e: "close"): void }>();
const dialog = ref<HTMLDialogElement | null>(null);
const copied = ref<number | "all" | null>(null);

watch(() => props.open, (open) => {
  if (open) { copied.value = null; void nextTick(() => dialog.value?.showModal()); }
  else if (dialog.value?.open) dialog.value.close();
});

function copy(text: string, which: number | "all") {
  void navigator.clipboard?.writeText(text).then(() => { copied.value = which; }, () => { copied.value = null; });
}
function when(part: UnsavedPart) {
  return part.savedAt ? new Date(part.savedAt).toLocaleString("zh-CN", { hour12: false, month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
}
</script>

<template>
  <dialog ref="dialog" class="app-dialog wide unsaved-others" aria-labelledby="unsaved-title" data-testid="unsaved-others-dialog" @cancel.prevent="emit('close')">
    <div class="dialog-head"><h2 id="unsaved-title">未保存的内容（属于他人负责的部分）</h2><button type="button" class="subtle icon-close" aria-label="关闭" @click="emit('close')"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg></button></div>
    <div class="dialog-body">
      <p class="dialog-text">这些修改改到了现在由他人负责的部分，没有保存到服务器，只保留在这台电脑上。可以复制后发给负责人，或粘贴到你负责的位置。</p>
      <ol class="parts">
        <li v-for="(part, index) in parts" :key="index" data-testid="unsaved-part">
          <div class="part-head"><span class="meta">第 {{ index + 1 }} 处<template v-if="partWhere(part)"> · {{ partWhere(part) }}</template><template v-if="when(part)"> · {{ when(part) }}</template></span>
            <button type="button" class="ghost sm" :data-testid="`unsaved-copy-${index}`" @click="copy(part.text, index)">{{ copied === index ? '已复制' : '复制' }}</button></div>
          <pre class="text">{{ part.text }}</pre>
        </li>
      </ol>
    </div>
    <div class="dialog-actions"><span class="dialog-note">共 {{ parts.length }} 处</span>
      <button type="button" class="ghost" data-testid="unsaved-copy-all" @click="copy(parts.map(part => part.text).join('\n\n'), 'all')">{{ copied === 'all' ? '已复制全部' : '复制全部' }}</button>
      <button type="button" class="primary" @click="emit('close')">关闭</button></div>
  </dialog>
</template>

<style scoped>
.parts { display:grid; gap:var(--space-3); max-height:calc(var(--space-7) * 9); margin:0; padding:0; overflow:auto; list-style:none; }
.parts li { border:1px solid var(--border-subtle); border-radius:var(--radius-sheet); background:var(--bg-subtle); }
.part-head { display:flex; align-items:center; justify-content:space-between; gap:var(--space-3); padding:var(--space-2) var(--space-3); border-bottom:1px solid var(--border-subtle); }
.meta { color:var(--text-secondary); font-size:var(--text-sm); line-height:var(--leading-sm); }
.text { margin:0; padding:var(--space-3); font-family:var(--font); font-size:var(--text-md); line-height:var(--leading-md); white-space:pre-wrap; overflow-wrap:anywhere; color:var(--text-primary); }
</style>
