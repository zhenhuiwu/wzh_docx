<script setup lang="ts">
/** 版本对比：按章节折叠；默认行内差异，可切换左右对照（30 §3.5）。改动多时分页加载。 */
import { ref, watch } from "vue";
import { ApiError } from "../../api/client";
import { diffVersions, type DiffStats, type SectionChange } from "../../api/baselines";
import StatePanel from "../StatePanel.vue";

const props = defineProps<{ documentId: string; base: number; compare: number | "current" }>();
const mode = ref<"inline" | "split">("inline");
const items = ref<SectionChange[]>([]);
const stats = ref<DiffStats | null>(null);
const total = ref(0);
const hasMore = ref(false);
const phase = ref<"loading" | "ready" | "error">("loading");
const loadingMore = ref(false);
const errorText = ref("");
const collapsed = ref(new Set<number>());

async function load(append = false) {
  if (append) loadingMore.value = true; else { phase.value = "loading"; items.value = []; }
  try {
    const page = await diffVersions(props.documentId, props.base, props.compare, append ? items.value.length : 0, 50);
    items.value = append ? [...items.value, ...page.items] : page.items;
    stats.value = page.stats; total.value = page.total; hasMore.value = page.hasMore;
    phase.value = "ready";
  } catch (error) {
    errorText.value = error instanceof ApiError ? error.message : "暂时不能连接服务。";
    if (!append) phase.value = "error";
  } finally { loadingMore.value = false; }
}
function toggle(index: number) {
  const next = new Set(collapsed.value);
  if (next.has(index)) next.delete(index); else next.add(index);
  collapsed.value = next;
}
const typeLabel = { added: "新增章节", removed: "删除章节", modified: "有修改" } as const;
watch(() => [props.documentId, props.base, props.compare], () => void load(), { immediate: true });
</script>

<template>
  <section class="diff-view" aria-label="版本对比">
    <div class="diff-bar">
      <p v-if="stats" class="diff-sum">{{ total }} 处章节变化 · +{{ stats.paragraphsAdded }} 段 / −{{ stats.paragraphsRemoved }} 段</p>
      <div class="seg" role="group" aria-label="对比方式">
        <button type="button" :class="{ on: mode === 'inline' }" :aria-pressed="mode === 'inline'" @click="mode = 'inline'">行内差异</button>
        <button type="button" :class="{ on: mode === 'split' }" :aria-pressed="mode === 'split'" @click="mode = 'split'">左右对照</button>
      </div>
    </div>
    <StatePanel v-if="phase === 'loading'" kind="loading" text="正在对比…" />
    <StatePanel v-else-if="phase === 'error'" kind="error" title="对比没有完成" :text="errorText"><button type="button" class="ghost" @click="load()">重试</button></StatePanel>
    <StatePanel v-else-if="!items.length" kind="empty" title="两个版本内容相同" />
    <ol v-else class="diff-sections">
      <li v-for="(item, index) in items" :key="index" class="diff-section">
        <button type="button" class="diff-head subtle" :aria-expanded="!collapsed.has(index)" @click="toggle(index)">
          <span class="tag" :class="item.type === 'added' ? 'success' : item.type === 'removed' ? 'danger' : 'info'">{{ typeLabel[item.type] }}</span>
          <strong>{{ (item.new ?? item.old)?.heading || "（开头）" }}</strong>
        </button>
        <div v-if="!collapsed.has(index)" :class="mode === 'split' ? 'diff-split' : 'diff-inline'">
          <template v-for="(p, pi) in item.paragraphs" :key="pi">
            <template v-if="mode === 'inline'">
              <p v-if="p.old !== undefined" class="del">{{ p.old }}</p>
              <p v-if="p.new !== undefined" class="ins">{{ p.new }}</p>
            </template>
            <template v-else>
              <p :class="p.old !== undefined ? 'del' : 'blank'">{{ p.old ?? "" }}</p>
              <p :class="p.new !== undefined ? 'ins' : 'blank'">{{ p.new ?? "" }}</p>
            </template>
          </template>
        </div>
      </li>
    </ol>
    <button v-if="hasMore" type="button" class="ghost more" :disabled="loadingMore" @click="load(true)">{{ loadingMore ? "正在加载…" : "加载更多变化" }}</button>
  </section>
</template>

<style scoped>
.diff-view { display: flex; flex-direction: column; gap: var(--space-3); }
.diff-bar { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); }
.diff-sum { margin: 0; color: var(--ink-secondary); font-size: var(--text-sm); }
.seg { display: inline-flex; padding: 2px; background: var(--gray-100); border-radius: var(--radius-control); }
.seg button { height: var(--control-h-sm); padding: 0 var(--space-3); border: 0; border-radius: calc(var(--radius-control) - 2px); background: transparent; color: var(--text-secondary); font: inherit; font-size: var(--text-sm); cursor: pointer; }
.seg button.on { background: var(--bg-surface); color: var(--text-primary); font-weight: var(--weight-medium); box-shadow: var(--shadow-1); }
.seg button:focus-visible { outline: var(--focus-outline); outline-offset: 1px; }
.diff-sections { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--space-2); }
.diff-section { border: 1px solid var(--rule); border-radius: var(--radius-sheet); background: var(--sheet); }
.diff-head { display: flex; align-items: center; justify-content: flex-start; gap: var(--space-2); width: 100%; padding: var(--space-2) var(--space-3); text-align: left; }
.diff-inline, .diff-split { padding: 0 var(--space-3) var(--space-3); font-family: var(--font-doc); font-size: var(--text-md); line-height: var(--leading-md); }
.diff-split { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-2); }
.diff-inline p, .diff-split p { margin: 0 0 var(--space-1); padding: var(--space-1) var(--space-2); border-radius: var(--radius-control); white-space: pre-wrap; overflow-wrap: anywhere; }
.ins { background: var(--diff-ins-bg); }
.del { background: var(--diff-del-bg); text-decoration: line-through; }
.blank { background: var(--bg-subtle); }
.more { align-self: center; }
</style>
