<script setup lang="ts">
/** A06 版本对比（16 §3.3）：宽 1200；顶部摘要；左栏按类别过滤；格式变化列“样式 · 属性 · 旧值 → 新值”。 */
import { computed, onMounted, ref } from "vue";
import { useDialogFocus } from "../../dialogFocus";
import StatePanel from "../StatePanel.vue";
import { diffTemplateVersions, type DiffItem, type VersionDiff } from "../../api/templates";
import { diffWords } from "../../textDiff";
import { EDITOR_KIND_LABELS, STYLE_PROP_LABELS, cellChanges, guidanceScopeText, summaryText, valueLabel } from "../../templateEditing";

const props = defineProps<{ documentId: string; from: string; to: string; title: string; fromLabel: string; toLabel: string }>();
const emit = defineEmits<{ close: []; locate: [blockId: string] }>();
const dialog = ref<HTMLDialogElement | null>(null);
const phase = ref<"loading" | "ready" | "error">("loading");
const errorText = ref("");
const diff = ref<VersionDiff | null>(null);
type Filter = "all" | DiffItem["category"] | "format";
const filter = ref<Filter>("all");
const CATEGORY: Record<string, string> = { headings: "标题", body: "正文", tables: "表格", guidance: "指导", format: "格式" };
const CHANGE: Record<string, string> = { added: "新增", removed: "删除", modified: "修改", moved: "移动" };
const filters = computed(() => {
  const summary = diff.value?.summary;
  return (["all", "headings", "body", "tables", "guidance", "format"] as Filter[]).map(key => ({
    key, label: key === "all" ? "全部" : CATEGORY[key],
    count: !summary ? 0 : key === "all" ? diff.value!.blocks.length + diff.value!.styles.length : summary[key as keyof typeof summary],
  }));
});
const blocks = computed(() => (diff.value?.blocks ?? []).filter(item => filter.value === "all" || item.category === filter.value));
const showStyles = computed(() => (filter.value === "all" || filter.value === "format") && (diff.value?.styles.length ?? 0) > 0);

function describe(side: DiffItem["before"]) {
  if (!side) return "";
  const kind = side.kind === "heading" ? `标题 ${side.headingLevel ?? ""}`.trim() : EDITOR_KIND_LABELS[side.kind] ?? side.kind;
  return kind;
}
function cellsText(side: DiffItem["before"]) { return (side?.cells ?? []).map(row => row.join(" | ")).join("\n"); }
function textOf(side: DiffItem["before"]) { return side?.cells ? cellsText(side) : side?.text ?? ""; }

async function load() {
  phase.value = "loading";
  try { diff.value = await diffTemplateVersions(props.documentId, props.from, props.to); phase.value = "ready"; }
  catch (error) { phase.value = "error"; errorText.value = error instanceof Error ? error.message : "暂时不能连接服务。"; }
}
useDialogFocus(dialog);
onMounted(() => { void load(); });
</script>

<template>
  <dialog ref="dialog" class="app-dialog tpl-compare" aria-labelledby="tpl-compare-title" data-testid="tpl-compare-dialog" @cancel.prevent="emit('close')">
    <header class="dialog-head">
      <h2 id="tpl-compare-title">{{ title }}</h2>
      <button type="button" class="subtle" aria-label="关闭对比" @click="emit('close')">×</button>
    </header>
    <StatePanel v-if="phase === 'loading'" kind="loading" text="正在对比…" />
    <StatePanel v-else-if="phase === 'error'" kind="error" title="对比没有完成" :text="errorText"><button type="button" class="ghost" @click="load">重试</button></StatePanel>
    <div v-else-if="diff" class="compare-body">
      <p class="compare-summary" data-testid="tpl-compare-summary">{{ fromLabel }} → {{ toLabel }}：{{ summaryText(diff.summary) }}</p>
      <div class="compare-main">
        <nav class="compare-filter" aria-label="按类别过滤">
          <button v-for="item in filters" :key="item.key" type="button" class="filter-item" :class="{ on: filter === item.key }" :aria-pressed="filter === item.key" @click="filter = item.key">
            <span>{{ item.label }}</span><span class="count">{{ item.count }}</span>
          </button>
        </nav>
        <div class="compare-list">
          <StatePanel v-if="!blocks.length && !showStyles" title="没有变化" text="这一类没有改动。" />
          <article v-for="item in blocks" :key="item.blockId + item.change" class="change" :data-change="item.change" data-testid="tpl-compare-item">
            <header>
              <span class="tag" :class="item.change === 'added' ? 'success' : item.change === 'removed' ? 'danger' : 'info'">{{ CHANGE[item.change] }}</span>
              <span class="where">{{ item.heading || '文档开头' }}</span>
              <span class="kind">{{ describe(item.after || item.before) }}</span>
              <button type="button" class="text sm" @click="emit('locate', item.blockId)">定位</button>
            </header>
            <ul v-if="item.change === 'modified' && item.before?.cells && item.after?.cells" class="cell-diff">
              <li v-for="cell in cellChanges(item.before.cells, item.after.cells)" :key="cell.label"><span class="hint">{{ cell.label }}：</span><del>{{ cell.before || '（空）' }}</del> → <ins>{{ cell.after || '（空）' }}</ins></li>
            </ul>
            <p v-else-if="item.change === 'modified' && item.before && item.after" class="text-diff">
              <template v-for="(part, index) in diffWords(textOf(item.before), textOf(item.after))" :key="index"><del v-if="part.type === 'del'">{{ part.text }}</del><ins v-else-if="part.type === 'ins'">{{ part.text }}</ins><span v-else>{{ part.text }}</span></template>
              <span v-if="item.before.kind !== item.after.kind || item.before.headingLevel !== item.after.headingLevel" class="hint">（{{ describe(item.before) }} → {{ describe(item.after) }}）</span>
            </p>
            <p v-else class="text-diff" :class="{ removed: item.change === 'removed' }">{{ textOf(item.after || item.before) }}</p>
            <p v-if="item.fields.includes('targetBlockIds')" class="text-diff">适用范围：{{ guidanceScopeText(item.before?.targetBlockIds, item.before?.targetTexts) }} → {{ guidanceScopeText(item.after?.targetBlockIds, item.after?.targetTexts) }}</p>
          </article>
          <table v-if="showStyles" class="style-table" data-testid="tpl-compare-styles">
            <thead><tr><th>样式</th><th>属性</th><th>{{ fromLabel }}</th><th>{{ toLabel }}</th></tr></thead>
            <tbody><tr v-for="item in diff.styles" :key="item.style + item.prop"><td>{{ item.style }}</td><td>{{ STYLE_PROP_LABELS[item.prop] ?? item.label }}</td><td>{{ valueLabel(item.prop, item.before) }}</td><td>{{ valueLabel(item.prop, item.after) }}</td></tr></tbody>
          </table>
        </div>
      </div>
    </div>
    <footer class="dialog-actions"><button type="button" class="ghost" @click="emit('close')">关闭</button></footer>
  </dialog>
</template>

<style scoped>
.tpl-compare { width:min(1200px, calc(100vw - var(--space-7) * 2)); }
.compare-body { display:grid; gap:var(--space-3); padding:var(--space-4) var(--space-5); }
.compare-summary { margin:0; font-size:var(--text-md); font-weight:var(--weight-medium); }
.compare-main { display:grid; grid-template-columns:180px minmax(0, 1fr); gap:var(--space-4); min-height:0; }
.compare-filter { display:grid; align-content:start; gap:var(--space-1); }
.filter-item { display:flex; justify-content:space-between; align-items:center; height:var(--control-h); padding:0 var(--space-3); border:0; border-radius:var(--radius-control); background:transparent; color:var(--text-secondary); font:inherit; font-size:var(--text-sm); cursor:pointer; }
.filter-item:hover { background:var(--bg-hover); }
.filter-item.on { background:var(--brand-50); color:var(--brand-700); font-weight:var(--weight-medium); }
.compare-list { display:grid; align-content:start; gap:var(--space-2); max-height:calc(100vh - var(--space-7) * 7); overflow:auto; }
.change { padding:var(--space-3); border:1px solid var(--border-subtle); border-radius:var(--radius-control); }
.change header { display:flex; align-items:center; gap:var(--space-2); font-size:var(--text-xs); color:var(--text-tertiary); }
.change .where { overflow:hidden; max-width:50%; text-overflow:ellipsis; white-space:nowrap; color:var(--text-secondary); }
.change header .text { margin-left:auto; }
.text-diff { margin:var(--space-2) 0 0; font-size:var(--text-sm); line-height:var(--leading-sm); white-space:pre-wrap; overflow-wrap:anywhere; }
.text-diff.removed { color:var(--text-tertiary); text-decoration:line-through; }
.text-diff ins { background:var(--success-50); color:var(--success-600); text-decoration:none; }
.text-diff del { background:var(--danger-50); color:var(--danger-600); }
.style-table { width:100%; }
.cell-diff { display:grid; gap:var(--space-1); margin:var(--space-2) 0 0; padding:0; list-style:none; font-size:var(--text-sm); }
.cell-diff ins { background:var(--success-50); color:var(--success-600); text-decoration:none; }
.cell-diff del { background:var(--danger-50); color:var(--danger-600); }
</style>
