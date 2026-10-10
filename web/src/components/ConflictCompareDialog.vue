<script setup lang="ts">
/** 并排对比弹窗（02 §3.4，界面验收 M4）：三栏按句对齐、逐句单选、合并结果预览；表格冲突为三张表格、变化单元格加底色、不逐句采纳。 */
import { computed, nextTick, onMounted, ref } from "vue";
import type { ConflictItem } from "../api/documents";
import { locationText, shortTime } from "../saveModel";
import { alignRows, isTable, mergeRows, structureRows, tableCells } from "../textDiff";

const props = defineProps<{ item: ConflictItem; theirsName: string; mineLabel?: string; canResolve: boolean; busy: boolean }>();
const emit = defineEmits<{ close: []; merge: [markdown: string]; edit: [markdown: string]; keepMine: []; useTheirs: [] }>();
const dialog = ref<HTMLDialogElement | null>(null);
const base = props.item.base?.markdown || "";
const theirs = props.item.theirs?.markdown || "";
const mine = props.item.mine?.deleted ? "" : props.item.mine?.markdown || "";
const table = isTable(theirs) || isTable(mine);
// 终验 S-6：STRUCTURE（拆分/合并段落）整块对比、整块采用，不逐段配对
const byBlock = props.item.reason === "STRUCTURE";
const rows = byBlock ? structureRows(base, theirs, mine) : alignRows(base, theirs, mine);
const pickLabel = (text: string) => !text ? "不保留这一句" : byBlock ? "采用这一版" : "采用这一句";
const picks = ref<Array<"theirs" | "mine">>(rows.map(() => "mine"));
const merged = computed(() => mergeRows(rows, picks.value, false, byBlock));
const grids = table ? { base: tableCells(base), theirs: tableCells(theirs), mine: tableCells(mine) } : null;
const changed = (grid: string[][], r: number, c: number) => (grids?.base[r]?.[c] ?? "") !== (grid[r]?.[c] ?? "");
const when = props.item.theirs?.updatedAt ? shortTime(props.item.theirs.updatedAt) : "";
onMounted(() => void nextTick(() => dialog.value?.showModal()));
</script>

<template>
  <dialog ref="dialog" class="app-dialog compare-dialog" aria-labelledby="compare-title" data-testid="compare-dialog" @cancel.prevent="emit('close')">
    <header class="dialog-head">
      <h2 id="compare-title">并排对比 · {{ locationText(item.location) }}</h2>
      <button type="button" class="subtle" aria-label="关闭并排对比" @click="emit('close')">×</button>
    </header>
    <div class="dialog-body">
      <p v-if="byBlock" class="compare-note">对方拆分或合并了段落，两边段落对不上，只能整块采用其中一版；两边都要的内容，请选“在合并结果中继续编辑”。</p>
      <div class="compare-cols" role="table" aria-label="三方内容对比">
        <div class="compare-row head" role="row">
          <span role="columnheader">原内容（修改前）</span>
          <span role="columnheader">{{ theirsName }} 的版本{{ when ? ` · ${when}` : "" }}</span>
          <span role="columnheader">{{ mineLabel || "我的版本" }}</span>
        </div>
        <template v-if="grids">
          <div class="compare-row" role="row">
            <div v-for="(grid, key) in grids" :key="key" role="cell" class="compare-cell">
              <table class="compare-table"><tbody>
                <tr v-for="(row, r) in grid" :key="r"><td v-for="(cell, c) in row" :key="c" :class="{ changed: key !== 'base' && changed(grid, r, c) }">{{ cell }}</td></tr>
              </tbody></table>
            </div>
          </div>
        </template>
        <template v-else>
          <div v-for="(row, index) in rows" :key="index" class="compare-row" :class="{ differs: row.differs, whole: byBlock }" role="row">
            <span role="cell" class="compare-cell base">{{ row.base }}</span>
            <label role="cell" class="compare-cell" :class="{ picked: row.differs && picks[index] === 'theirs' }">
              <span>{{ row.theirs || "（无）" }}</span>
              <span v-if="row.differs && canResolve" class="pick"><input v-model="picks[index]" type="radio" :name="`pick-${index}`" value="theirs" /> {{ pickLabel(row.theirs) }}</span>
            </label>
            <label role="cell" class="compare-cell" :class="{ picked: row.differs && picks[index] === 'mine' }">
              <span>{{ row.mine || "（无）" }}</span>
              <span v-if="row.differs && canResolve" class="pick"><input v-model="picks[index]" type="radio" :name="`pick-${index}`" value="mine" /> {{ pickLabel(row.mine) }}</span>
            </label>
          </div>
        </template>
      </div>
      <section v-if="!grids && canResolve" class="compare-preview" aria-label="合并结果预览">
        <h3>合并结果预览</h3>
        <p data-testid="compare-merged">{{ merged || "（空）" }}</p>
      </section>
      <p v-else-if="grids" class="compare-note">表格按整张处理：可保留我的、使用对方的版本，或在卡片里选择“两份表格都保留”（正文里会有两张表，需要再手动合并）。</p>
    </div>
    <footer class="dialog-actions">
      <button type="button" class="ghost" @click="emit('close')">取消</button>
      <template v-if="canResolve && !grids">
        <button type="button" class="ghost" :disabled="busy || !merged.trim()" @click="emit('edit', merged)">在合并结果中继续编辑</button>
        <button type="button" class="primary" data-testid="compare-apply" :disabled="busy || !merged.trim()" @click="emit('merge', merged)">{{ busy ? "正在处理…" : "采用合并结果" }}</button>
      </template>
      <template v-else-if="canResolve">
        <button type="button" class="ghost" :disabled="busy" @click="emit('useTheirs')">使用对方的版本</button>
        <button type="button" class="primary" :disabled="busy" @click="emit('keepMine')">{{ busy ? "正在处理…" : "保留我的" }}</button>
      </template>
    </footer>
  </dialog>
</template>

<style scoped>
.compare-dialog { width:min(1200px, calc(100vw - var(--space-7) * 2)); }
.compare-cols { display:grid; gap:var(--space-1); max-height:calc(100vh - var(--space-7) * 8); overflow:auto; }
.compare-row { display:grid; grid-template-columns:repeat(3, minmax(0, 1fr)); gap:var(--space-2); }
.compare-row.head { position:sticky; top:0; background:var(--bg-surface); color:var(--text-tertiary); font-size:var(--text-xs); font-weight:var(--weight-medium); padding-bottom:var(--space-1); }
.compare-cell { display:grid; gap:var(--space-1); align-content:start; padding:var(--space-2); border-radius:var(--radius-control); background:var(--bg-subtle); font-size:var(--text-sm); line-height:var(--leading-sm); overflow-wrap:anywhere; }
.compare-cell.base { color:var(--text-secondary); }
.compare-row.differs .compare-cell:not(.base) { background:var(--warning-50); }
.compare-row.differs label.compare-cell:has(input) { cursor:pointer; }
.compare-row.differs .compare-cell.picked { background:var(--diff-ins-bg); box-shadow:inset 0 0 0 1px var(--brand-200); }
.pick { color:var(--text-secondary); font-size:var(--text-xs); }
.compare-table { width:100%; border-collapse:collapse; }
.compare-table td { border:1px solid var(--border-default); padding:var(--space-1); vertical-align:top; }
.compare-table td.changed { background:var(--diff-ins-bg); }
.compare-preview { margin-top:var(--space-4); }
.compare-preview h3 { margin:0 0 var(--space-1); font-size:var(--text-sm); color:var(--text-secondary); }
.compare-preview p, .compare-note { margin:0; padding:var(--space-2); border-radius:var(--radius-control); background:var(--bg-subtle); white-space:pre-wrap; overflow-wrap:anywhere; }
.compare-row.whole .compare-cell > span:first-child { white-space:pre-wrap; }
</style>
