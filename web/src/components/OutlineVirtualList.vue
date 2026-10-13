<script setup lang="ts">
/** 长文档目录（30 §3.3）：@tanstack/vue-virtual 虚拟化渲染，键盘交互按 APG Tree View（上下键、Home/End）。 */
import { computed, nextTick, ref, watch } from "vue";
import { useVirtualizer } from "@tanstack/vue-virtual";

interface Entry { item: { text: string; level: number }; index: number; info?: { depth?: number; empty?: boolean }; mine?: boolean }
const props = defineProps<{ entries: Entry[]; selected: number | null; disabled?: boolean; conflicts: Set<string>; updated: Set<string> }>();
const emit = defineEmits<{ (event: "select", index: number): void }>();

const scroller = ref<HTMLElement | null>(null);
const virtualizer = useVirtualizer(computed(() => ({
  count: props.entries.length,
  getScrollElement: () => scroller.value,
  estimateSize: () => 31,
  overscan: 12,
})));
const rows = computed(() => virtualizer.value.getVirtualItems());
const depth = (entry: Entry) => Math.min((entry.info?.depth ?? 0) + 1, 4);
const selectedPosition = computed(() => props.entries.findIndex(entry => entry.index === props.selected));

function focusRow(position: number) {
  virtualizer.value.scrollToIndex(position, { align: "auto" });
  void nextTick(() => requestAnimationFrame(() => scroller.value?.querySelector<HTMLElement>(`[data-outline-pos="${position}"]`)?.focus()));
}
function onKey(event: KeyboardEvent, position: number) {
  const last = props.entries.length - 1;
  const next = event.key === "ArrowDown" ? position + 1 : event.key === "ArrowUp" ? position - 1 : event.key === "Home" ? 0 : event.key === "End" ? last : null;
  if (next === null || next < 0 || next > last) return;
  event.preventDefault();
  focusRow(next);
}
watch(selectedPosition, (position) => { if (position >= 0) virtualizer.value.scrollToIndex(position, { align: "auto" }); });
</script>

<template>
  <div ref="scroller" class="olist virtual" role="tree" aria-label="目录" data-testid="outline-virtual">
    <div class="size" :style="{ height: `${virtualizer.getTotalSize()}px` }">
      <button v-for="row in rows" :key="entries[row.index].index" type="button" role="treeitem" class="oitem outline-item row" :data-outline-pos="row.index"
        :class="[`l${depth(entries[row.index])}`, { on: selected === entries[row.index].index, empty: entries[row.index].info?.empty }]"
        :style="{ transform: `translateY(${row.start}px)` }" :aria-selected="selected === entries[row.index].index" :aria-level="depth(entries[row.index])"
        :aria-posinset="row.index + 1" :aria-setsize="entries.length"
        :tabindex="row.index === (selectedPosition >= 0 ? selectedPosition : 0) ? 0 : -1"
        :disabled="disabled" @click="emit('select', entries[row.index].index)" @keydown="onKey($event, row.index)">
        <span class="otext" :title="entries[row.index].item.text">{{ entries[row.index].item.text }}</span>
        <svg v-if="entries[row.index].mine === false" class="ico olock" viewBox="0 0 24 24" role="img" aria-label="只读"><rect x="5" y="11" width="14" height="9" rx="1.5" /><path d="M8 11V8a4 4 0 0 1 8 0v3" /></svg>
        <i v-if="conflicts.has(entries[row.index].item.text)" class="omark danger" role="img" title="本章节有冲突待处理" aria-label="有冲突"></i>
        <i v-else-if="updated.has(entries[row.index].item.text)" class="omark info" role="img" title="他人更新了本章节" aria-label="有更新"></i>
        <span v-else-if="entries[row.index].info?.empty" class="ostate">待编写</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.olist { flex:1; min-height:0; overflow:auto; padding:var(--space-2); position:relative; }
.size { position:relative; width:100%; }
.oitem { position:absolute; top:0; left:0; display:flex; align-items:center; gap:6px; width:100%; min-height:30px; padding:5px var(--space-2); border:0; border-radius:var(--radius-control); background:transparent; color:var(--text-primary); font-family:var(--font); font-size:var(--text-sm); line-height:var(--leading-sm); text-align:left; cursor:pointer; }
.oitem:hover:not(:disabled) { background:var(--bg-hover); }
.oitem:disabled { cursor:not-allowed; opacity:.6; }
.oitem:focus-visible { outline:var(--focus-outline); outline-offset:-2px; }
.oitem.on { background:var(--bg-active); color:var(--brand-700); font-weight:var(--weight-medium); }
.oitem.l1 { font-weight:var(--weight-medium); }
.oitem.l2 { padding-left:var(--outline-indent-2); }
.oitem.l3 { padding-left:var(--outline-indent-3); }
.oitem.l4 { padding-left:var(--outline-indent-4); }
.otext { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.olock { width:var(--icon-sm); height:var(--icon-sm); flex:none; fill:none; stroke:var(--text-tertiary); stroke-width:1.6; }
.omark { width:7px; height:7px; border-radius:var(--radius-pill); flex:none; }
.omark.danger { background:var(--danger-600); }
.omark.info { background:var(--info-600); }
.ostate { font-size:var(--text-xs); color:var(--text-tertiary); flex:none; }
</style>
