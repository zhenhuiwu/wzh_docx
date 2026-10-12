<script setup lang="ts">
/** A07 保存被拒面板（16 §4.6 第 4 条、29 M-3）：逐处说明、定位、联系谁；只保存我负责的部分 / 下载 / 复制。 */
import { nextTick, onMounted, ref } from "vue";
import { ownerNames, violationText, violationWhere, type Violation } from "../assignmentModel";

const props = defineProps<{ violations: Violation[]; total: number; projectOwner: string; busy: boolean }>();
const emit = defineEmits<{ close: []; locate: [item: Violation]; saveMine: []; download: []; copy: [] }>();
const root = ref<HTMLElement | null>(null);
onMounted(async () => { await nextTick(); root.value?.querySelector<HTMLElement>("[data-autofocus]")?.focus(); });
function onKey(event: KeyboardEvent) { if (event.key === "Escape") { event.stopPropagation(); emit("close"); } }
const contact = (item: Violation) => ownerNames(item.owners) || props.projectOwner || "项目负责人";
</script>

<template>
  <aside ref="root" class="rej-panel" data-testid="save-rejected-panel" aria-label="未保存的修改" tabindex="-1" @keydown="onKey">
    <header class="rej-head">
      <h2>未保存的修改</h2>
      <button type="button" class="subtle icon-btn" aria-label="关闭" data-autofocus @click="emit('close')">×</button>
    </header>
    <p class="rej-lead">这次修改没有保存：其中 {{ total }} 处属于他人负责的部分。点一处可在正文中定位。</p>
    <ul class="rej-list">
      <li v-for="(item, index) in violations" :key="index">
        <button type="button" class="rej-item" data-testid="save-rejected-item" :data-object-id="item.objectId || ''" @click="emit('locate', item)">
          <strong>{{ violationText(item) }}</strong>
          <span class="where">{{ violationWhere(item) }}</span>
          <span class="who">请联系 {{ contact(item) }}</span>
        </button>
      </li>
      <li v-if="total > violations.length" class="more">另有 {{ total - violations.length }} 处未列出。</li>
    </ul>
    <footer class="rej-acts">
      <button type="button" class="primary sm" data-testid="save-rejected-save-mine" :disabled="busy" @click="emit('saveMine')">{{ busy ? '正在保存…' : '只保存我负责的部分' }}</button>
      <button type="button" class="ghost sm" data-testid="save-rejected-download" @click="emit('download')">下载我的修改</button>
      <button type="button" class="ghost sm" data-testid="save-rejected-copy" @click="emit('copy')">复制</button>
    </footer>
    <p class="rej-note">“只保存我负责的部分”会保存你负责部分里的修改，其余修改放入暂存，不会丢失。</p>
  </aside>
</template>

<style scoped>
.rej-panel { position: sticky; top: 0; align-self: start; max-height: calc(100vh - 140px); width: var(--panel-w); box-sizing: border-box; border-left: 1px solid var(--border-default); background: var(--bg-surface); display: flex; flex-direction: column; gap: var(--space-3); padding: var(--space-3) var(--space-4); overflow: auto; font-size: var(--text-sm); outline: none; }
.rej-head { display: flex; justify-content: space-between; align-items: center; }
.rej-head h2 { margin: 0; font-size: var(--text-md, 15px); }
.rej-lead, .rej-note { margin: 0; color: var(--text-secondary); }
.rej-note { font-size: 12px; }
.rej-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--space-2); }
.rej-item { width: 100%; text-align: left; display: grid; gap: 2px; padding: var(--space-2); border: 1px solid var(--border-default); border-radius: var(--radius-control); background: var(--bg-surface); cursor: pointer; font: inherit; }
.rej-item:hover { background: var(--bg-subtle); }
.rej-item .where { color: var(--text-secondary); }
.rej-item .who { color: var(--text-secondary); font-size: 12px; }
.more { color: var(--text-secondary); }
.rej-acts { display: flex; flex-wrap: wrap; gap: var(--space-2); }
</style>
