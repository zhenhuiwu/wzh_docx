<script setup lang="ts">
/** 模板组详情页的只读依赖小图（30 §3.6）：按依赖深度分列，箭头指向下游；编辑在行内“设置上游”。 */
import { computed } from "vue";
import type { TemplateRef } from "../api/docChain";

const props = defineProps<{ documents: { id: string; name: string }[]; upstreams: Record<string, TemplateRef[]> }>();

const columns = computed(() => {
  const ids = new Set(props.documents.map((d) => d.id));
  const depth = new Map<string, number>();
  const visit = (id: string, trail: Set<string>): number => {
    if (depth.has(id)) return depth.get(id)!;
    if (trail.has(id)) return 0;
    trail.add(id);
    const ups = (props.upstreams[id] ?? []).filter((u) => ids.has(u.id));
    const value = ups.length ? Math.max(...ups.map((u) => visit(u.id, trail))) + 1 : 0;
    depth.set(id, value);
    return value;
  };
  props.documents.forEach((d) => visit(d.id, new Set()));
  const cols: { id: string; name: string; ups: string[]; external: string[] }[][] = [];
  for (const d of props.documents) {
    const level = depth.get(d.id) ?? 0;
    (cols[level] ??= []).push({ id: d.id, name: d.name,
      ups: (props.upstreams[d.id] ?? []).filter((u) => ids.has(u.id)).map((u) => u.name),
      external: (props.upstreams[d.id] ?? []).filter((u) => !ids.has(u.id)).map((u) => u.name) });
  }
  return cols.filter(Boolean);
});
const hasLinks = computed(() => props.documents.some((d) => (props.upstreams[d.id] ?? []).length));
</script>

<template>
  <section class="mini" aria-label="模板依赖关系" data-testid="template-chain">
    <h3 class="title">依赖关系</h3>
    <p v-if="!hasLinks" class="muted">还没有设置上游模板。在下表行内“设置上游”中设置。</p>
    <ol v-else class="cols">
      <li v-for="(col, i) in columns" :key="i" class="col">
        <span v-if="i > 0" class="arrow" aria-hidden="true">→</span>
        <ul class="nodes">
          <li v-for="n in col" :key="n.id" class="node" :title="n.ups.length ? `上游：${n.ups.join('、')}` : '无上游'">
            <span class="name">{{ n.name }}</span>
            <span v-if="n.ups.length" class="ups">上游：{{ n.ups.join("、") }}</span>
            <span v-if="n.external.length" class="ups">组外上游：{{ n.external.join("、") }}</span>
          </li>
        </ul>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.mini { display:grid; gap:var(--space-3); padding:var(--space-4) var(--space-5); border-bottom:1px solid var(--border-subtle); }
.title { margin:0; color:var(--text-secondary); font-size:var(--text-sm); font-weight:var(--weight-medium); }
.muted { margin:0; color:var(--text-tertiary); font-size:var(--text-sm); }
.cols { display:flex; align-items:center; gap:var(--space-3); margin:0; padding:0; list-style:none; overflow-x:auto; }
.col { display:flex; align-items:center; gap:var(--space-3); }
.arrow { color:var(--gray-400); }
.nodes { display:grid; gap:var(--space-2); margin:0; padding:0; list-style:none; }
.node { display:grid; gap:2px; min-width:calc(var(--space-7) * 4); padding:var(--space-2) var(--space-3); border:1px solid var(--border-default); border-radius:var(--radius-control); background:var(--bg-surface); }
.name { color:var(--text-primary); font-size:var(--text-sm); white-space:nowrap; }
.ups { color:var(--text-tertiary); font-size:var(--text-xs); white-space:nowrap; }
</style>
