<script setup lang="ts">
/** 项目首页文档依赖链（30 §3.1）：按阶段分列的卡片 + SVG 连线，可切换列表视图。 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { ApiError } from "../api/client";
import { baselineCompareRoute, getProjectChain, type ChainNode, type ProjectChain, type UpstreamBaseline } from "../api/docChain";
import CreateFromTemplateDialog from "./CreateFromTemplateDialog.vue";
import StatePanel from "./StatePanel.vue";

const props = defineProps<{ projectId: string; readonly?: boolean }>();
const emit = defineEmits<{ (event: "state", value: { ready: boolean; hasDocuments: boolean; canCreate: boolean }): void }>();
const router = useRouter();
const chain = ref<ProjectChain | null>(null);
const phase = ref<"loading" | "ready" | "error">("loading");
const errorText = ref("");
const view = ref<"graph" | "list">("graph");
const hovered = ref<string | null>(null);
const creating = ref<ChainNode | null>(null);
const board = ref<HTMLElement | null>(null);
const lines = ref<{ from: string; to: string; d: string }[]>([]);
const statusLabel: Record<string, string> = { draft: "编写中", in_progress: "编写中", completed: "已完成", missing: "未创建" };

const byKey = computed(() => new Map((chain.value?.nodes ?? []).filter((n) => n.key).map((n) => [n.key as string, n])));
const columns = computed(() => (chain.value?.stages ?? []).map((stage) => ({ ...stage, nodes: (chain.value?.nodes ?? []).filter((n) => n.stage === stage.key) })));
const canCreate = computed(() => Boolean(chain.value?.canCreate) && !props.readonly);
const hasDocuments = computed(() => (chain.value?.nodes ?? []).some((n) => n.created) || (chain.value?.others.length ?? 0) > 0);
const related = computed(() => {
  const key = hovered.value;
  if (!key) return new Set<string>();
  const set = new Set<string>([key]);
  for (const n of chain.value?.nodes ?? []) {
    if (n.key === key) n.upstream.forEach((u) => set.add(u));
    if (n.key && n.upstream.includes(key)) set.add(n.key);
  }
  return set;
});

const createdDocs = computed(() => [...(chain.value?.nodes ?? []), ...(chain.value?.others ?? [])]
  .filter((n) => n.created && n.documentId).map((n) => ({ id: n.documentId as string, name: n.name })));
function defaultUpstreams(node: ChainNode) {
  return node.upstream.map((k) => byKey.value.get(k)).filter((n): n is ChainNode => Boolean(n?.created && n.documentId)).map((n) => n.documentId as string);
}
function upstreamNames(node: ChainNode) { return node.upstream.map((k) => byKey.value.get(k)?.name ?? k); }
function percent(node: ChainNode) { return node.total ? Math.round((node.written / node.total) * 100) : 0; }

async function load() {
  phase.value = "loading";
  try {
    chain.value = await getProjectChain(props.projectId);
    phase.value = "ready";
    await nextTick();
    layoutLines();
  } catch (error) {
    phase.value = "error";
    errorText.value = error instanceof ApiError ? error.message : "暂时不能连接服务。";
  }
}

function layoutLines() {
  const root = board.value;
  if (!root || view.value !== "graph") { lines.value = []; return; }
  const box = root.getBoundingClientRect();
  const rect = (key: string) => root.querySelector<HTMLElement>(`[data-node="${key}"]`)?.getBoundingClientRect();
  const out: { from: string; to: string; d: string }[] = [];
  for (const node of chain.value?.nodes ?? []) {
    for (const up of node.upstream) {
      const a = rect(up), b = node.key ? rect(node.key) : undefined;
      if (!a || !b) continue;
      const x1 = a.right - box.left, y1 = a.top + a.height / 2 - box.top, x2 = b.left - box.left, y2 = b.top + b.height / 2 - box.top;
      const mid = x1 + (x2 - x1) / 2;
      out.push({ from: up, to: node.key as string, d: x2 > x1 ? `M${x1},${y1} H${mid} V${y2} H${x2}` : `M${a.left + a.width / 2 - box.left},${a.bottom - box.top} V${b.top - box.top}` });
    }
  }
  lines.value = out;
}

function open(node: ChainNode) {
  if (node.created && node.documentId) void router.push({ name: "document", params: { id: props.projectId, documentId: node.documentId } });
  else if (canCreate.value) creating.value = node;
}

function onCreated(documentId: string) {
  creating.value = null;
  void router.push({ name: "document", params: { id: props.projectId, documentId } });
}

/** 「X 更新到了 B2，Y 仍基于 B1」：每条有新基线的上游关系一行。 */
const baselineNotices = computed(() => (chain.value?.nodes ?? []).flatMap((node) =>
  (node.upstreamBaselines ?? []).filter((u) => u.newBaseline).map((u) => ({ node, up: u }))));
function firstNew(node: ChainNode) { return (node.upstreamBaselines ?? []).find((u) => u.newBaseline); }
function compare(up: UpstreamBaseline | undefined) { if (up) void router.push(baselineCompareRoute(props.projectId, up)); }

/** 页头［从模板创建］：按依赖顺序挑下一份该写的文档（上游都已创建的第一份未创建文档）。都建好了就去文档页。 */
const nextMissing = computed(() => {
  const nodes = chain.value?.nodes ?? [];
  const created = new Set(nodes.filter((n) => n.created).map((n) => n.key));
  const missing = nodes.filter((n) => !n.created);
  return missing.find((n) => n.upstream.every((k) => created.has(k))) ?? missing[0] ?? null;
});
function createNext() {
  if (nextMissing.value && canCreate.value) creating.value = nextMissing.value;
  else void router.push({ name: "project-documents", params: { id: props.projectId } });
}
watch([hasDocuments, canCreate, phase], () => emit("state", { ready: phase.value === "ready", hasDocuments: hasDocuments.value, canCreate: canCreate.value }));

function setView(value: "graph" | "list") { view.value = value; void nextTick(layoutLines); }

onMounted(() => { void load(); window.addEventListener("resize", layoutLines); });
onBeforeUnmount(() => window.removeEventListener("resize", layoutLines));
defineExpose({ reload: load, createNext });
</script>

<template>
  <section class="chain" aria-labelledby="chain-title">
    <div class="chain-head">
      <h2 id="chain-title" class="section-title">文档依赖链</h2>
      <div class="chain-tools">
        <ul class="legend" aria-label="图例">
          <li><span class="swatch writing" />编写中</li>
          <li><span class="tag plain">B1</span>已基线</li>
          <li><span class="tag warning">上游有新基线</span></li>
          <li><span class="swatch missing" />未创建</li>
        </ul>
        <div class="seg" role="group" aria-label="视图">
          <button type="button" class="ghost" :aria-pressed="view === 'graph'" :class="{ on: view === 'graph' }" @click="setView('graph')">依赖图</button>
          <button type="button" class="ghost" :aria-pressed="view === 'list'" :class="{ on: view === 'list' }" @click="setView('list')">列表</button>
        </div>
      </div>
    </div>
    <div v-if="phase === 'loading'" class="skeleton" role="status" aria-label="正在读取依赖链"><div v-for="i in 3" :key="i" class="sk-col"><span class="sk-card" /><span class="sk-card" /></div></div>
    <StatePanel v-else-if="phase === 'error'" kind="error" title="依赖链加载失败。" :text="errorText"><button type="button" class="ghost" @click="load">重试</button></StatePanel>
    <template v-else-if="chain">
      <div v-if="!hasDocuments" class="empty-hint" data-testid="chain-empty">
        <span>还没有文档。按模板依赖链，第一份通常是任务计划书。</span>
        <button v-if="canCreate" type="button" class="primary" data-testid="chain-empty-create" @click="createNext">从模板创建</button>
      </div>
      <div v-if="view === 'graph'" ref="board" class="board" data-testid="doc-chain">
        <svg class="wires" aria-hidden="true"><path v-for="line in lines" :key="line.from + line.to" :d="line.d" :class="{ hot: related.has(line.from) && related.has(line.to) && (hovered === line.from || hovered === line.to) }" /></svg>
        <div v-for="col in columns" :key="col.key" class="col">
          <h3 class="col-title">{{ col.name }}</h3>
          <button v-for="node in col.nodes" :key="node.key ?? node.name" type="button" class="node" :data-node="node.key"
                  :class="{ missing: !node.created, dim: hovered && !related.has(node.key ?? '') , done: node.status === 'completed' }"
                  :disabled="!node.created && !canCreate"
                  :aria-label="node.created ? `打开文档：${node.name}` : canCreate ? `从模板创建：${node.name}` : `${node.name}，未创建`"
                  @mouseenter="hovered = node.key" @mouseleave="hovered = null" @focus="hovered = node.key" @blur="hovered = null" @click="open(node)">
            <span class="node-name">{{ node.name }}</span>
            <template v-if="node.created">
              <span v-if="node.baselineSeq || node.upstreamChanged" class="node-flags">
                <span v-if="node.baselineSeq" class="tag plain" data-testid="baseline-seq">B{{ node.baselineSeq }}</span>
                <span v-if="node.upstreamChanged" class="tag warning clickable" data-testid="upstream-changed" title="查看上游变化" @click.stop="compare(firstNew(node))">上游有新基线</span>
              </span>
              <span class="node-meta"><span class="tag" :class="{ info: node.status !== 'completed', success: node.status === 'completed' }">{{ statusLabel[node.status] ?? node.status }}</span><span class="num">{{ node.written }}/{{ node.total }} 章</span></span>
              <span class="bar" aria-hidden="true"><span :style="{ width: percent(node) + '%' }" /></span>
              <span class="owner">{{ node.ownerName || "—" }}</span>
            </template>
            <span v-else class="node-missing">{{ canCreate ? "从模板创建" : "未创建" }}</span>
          </button>
        </div>
      </div>
      <div v-else class="table-wrap"><table data-testid="doc-chain-list">
        <thead><tr><th>文档</th><th>上游</th><th>状态</th><th>进度</th><th>负责人</th></tr></thead>
        <tbody><tr v-for="node in chain.nodes" :key="node.key ?? node.name">
          <td><button type="button" class="link" :disabled="!node.created && !canCreate" @click="open(node)">{{ node.name }}</button></td>
          <td>{{ upstreamNames(node).join("、") || "—" }}</td>
          <td><span class="tag" :class="{ info: node.created && node.status !== 'completed', success: node.status === 'completed' }">{{ node.created ? statusLabel[node.status] ?? node.status : "未创建" }}</span>
            <span v-if="node.baselineSeq" class="tag plain">B{{ node.baselineSeq }}</span>
            <button v-if="node.upstreamChanged" type="button" class="tag warning clickable" @click="compare(firstNew(node))">上游有新基线</button></td>
          <td class="num">{{ node.created ? `${node.written}/${node.total} 章` : "—" }}</td>
          <td>{{ node.ownerName || "—" }}</td>
        </tr></tbody>
      </table></div>
      <ul v-if="baselineNotices.length" class="baseline-notices" data-testid="baseline-notices">
        <li v-for="item in baselineNotices" :key="item.node.documentId + item.up.documentId">
          {{ item.up.name }} 更新到了 B{{ item.up.latestBaselineSeq }}，{{ item.node.name }} 仍基于 {{ item.up.baseBaselineSeq ? `B${item.up.baseBaselineSeq}` : "未基线的版本" }}
          <button type="button" class="text" @click="compare(item.up)">查看变化</button>
        </li>
      </ul>
      <p v-if="chain.others.length" class="others">不在依赖链中的文档：{{ chain.others.map((n) => n.name).join("、") }}</p>
    </template>
    <CreateFromTemplateDialog :project-id="projectId" :node="creating" :upstream-options="createdDocs" :default-upstream-ids="creating ? defaultUpstreams(creating) : []" @close="creating = null" @created="onCreated" />
  </section>
</template>

<style scoped>
.node-flags { display:flex; gap:var(--space-1); flex-wrap:wrap; }
.clickable { cursor:pointer; border:0; }
.clickable:hover { text-decoration:underline; }
.baseline-notices { margin:var(--space-3) 0 0; padding:0; list-style:none; display:grid; gap:var(--space-1); color:var(--text-secondary); font-size:var(--text-sm); }
.chain { min-width:0; }
.chain-head { display:flex; justify-content:space-between; align-items:center; gap:var(--space-5); margin-bottom:var(--space-4); }
.chain-tools { display:flex; align-items:center; gap:var(--space-5); }
.legend { display:flex; gap:var(--space-4); margin:0; padding:0; list-style:none; color:var(--text-secondary); font-size:var(--text-xs); }
.legend li { display:flex; align-items:center; gap:var(--space-1); }
.swatch { width:var(--space-3); height:var(--space-3); border-radius:var(--radius-control); border:1px solid var(--border-strong); background:var(--bg-surface); }
.swatch.writing { background:var(--brand-100); border-color:var(--stamp); }
.swatch.done { background:var(--stamp); border-color:var(--stamp); }
.swatch.missing { border-style:dashed; }
.seg { display:flex; gap:var(--space-1); }
.seg .on { background:var(--stamp-wash); color:var(--stamp); border-color:var(--stamp); }
.board { position:relative; display:grid; grid-template-columns:repeat(4, 220px); justify-content:space-between; gap:var(--space-6); padding:var(--space-5); border:1px solid var(--border-default); border-radius:var(--radius-sheet); background:var(--bg-subtle); overflow-x:auto; }
.wires { position:absolute; inset:0; width:100%; height:100%; pointer-events:none; }
.wires path { fill:none; stroke:var(--gray-400); stroke-width:1.5; }
.wires path.hot { stroke:var(--stamp); stroke-width:2; }
.col { position:relative; display:grid; align-content:start; gap:var(--space-4); }
.col-title { margin:0; color:var(--text-tertiary); font-size:var(--text-xs); font-weight:var(--weight-medium); }
.node { display:grid; gap:var(--space-2); width:220px; padding:var(--space-3) var(--space-4); text-align:left; border:1px solid var(--border-default); border-radius:var(--radius-sheet); background:var(--bg-surface); color:var(--text-primary); font:inherit; cursor:pointer; transition:opacity .15s; }
.node:hover:not(:disabled) { border-color:var(--stamp); }
.node:focus-visible { outline:none; box-shadow:var(--focus-shadow); }
.node.missing { border-style:dashed; background:transparent; }
.node:disabled { cursor:default; }
.node.dim { opacity:.45; }
.node .tag.warning { justify-self:start; }
.node-name { font-weight:var(--weight-medium); overflow-wrap:anywhere; }
.node-meta { display:flex; justify-content:space-between; align-items:center; color:var(--text-secondary); font-size:var(--text-xs); }
.node-missing { color:var(--stamp); font-size:var(--text-sm); }
.node:disabled .node-missing { color:var(--text-tertiary); }
.bar { height:4px; border-radius:var(--radius-control); background:var(--gray-300); overflow:hidden; }
.bar span { display:block; height:100%; background:var(--stamp); }
.owner { color:var(--text-secondary); font-size:var(--text-xs); }
.skeleton { display:grid; grid-template-columns:repeat(3, 220px); gap:var(--space-6); padding:var(--space-5); }
.sk-col { display:grid; gap:var(--space-4); }
.sk-card { height:calc(var(--space-7) * 2); border-radius:var(--radius-sheet); background:var(--gray-300); opacity:.4; }
.empty-hint { display:flex; align-items:center; gap:var(--space-3); }
.empty-hint, .others { margin:0 0 var(--space-3); color:var(--text-secondary); font-size:var(--text-sm); }
.others { margin-top:var(--space-3); }
.link { border:0; background:none; padding:0; color:var(--text-link); font:inherit; cursor:pointer; }
.link:disabled { color:var(--text-primary); cursor:default; }
</style>
