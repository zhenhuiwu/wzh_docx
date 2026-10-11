<script setup lang="ts">
/** A06 表格单元格编辑（16 §3.2 三态）：cells 全部可改字；partial 锁定格只读；none 整表只读并引导上传新 Word。 */
import type { EditorBlock } from "../../api/templates";
import { cellReason, isCellLocked } from "../../templateEditing";

const props = defineProps<{ block: EditorBlock; readonly: boolean }>();
const emit = defineEmits<{ change: [cells: string[][]]; upload: [] }>();
function edit(row: number, column: number, event: Event) {
  const cells = (props.block.cells ?? []).map(line => [...line]);
  cells[row][column] = (event.target as HTMLTextAreaElement).value;
  emit("change", cells);
}
const rowsOf = (text: string) => Math.max(1, Math.min(6, text.split("\n").length));
</script>

<template>
  <div class="tpl-table" :data-editable="block.tableEditable" data-testid="tpl-table">
    <div class="table-tags">
      <span v-if="block.tableEditable === 'cells'" class="tag plain" title="本期只能在线修改单元格文字。增删行将在后续版本提供；增删列、合并单元格请在 Word 中修改后上传为新草稿。">仅可改文字</span>
      <span v-else-if="block.tableEditable === 'partial'" class="tag warning" title="合并单元格、多段单元格不能在线修改；其余格子可以改文字。">部分单元格锁定</span>
      <span v-else class="tag warning">此表不支持在线编辑</span>
    </div>
    <table class="grid" :class="{ frozen: block.tableEditable === 'none' }">
      <tbody>
        <tr v-for="(row, r) in block.cells ?? []" :key="r">
          <td v-for="(cell, c) in row" :key="c" :class="{ locked: isCellLocked(block, r, c) }" :title="isCellLocked(block, r, c) ? cellReason(block, r, c) : ''" :data-cell="`${r}:${c}`">
            <template v-if="isCellLocked(block, r, c) || readonly">
              <span class="cell-text">{{ cell }}</span>
              <svg v-if="isCellLocked(block, r, c)" class="lock" viewBox="0 0 12 12" aria-label="锁定"><rect x="2.5" y="5.5" width="7" height="5" rx="1" /><path d="M4 5.5V4a2 2 0 0 1 4 0v1.5" /></svg>
            </template>
            <textarea v-else class="cell-input" :rows="rowsOf(cell)" :value="cell" :aria-label="`第 ${r + 1} 行第 ${c + 1} 列`" @input="edit(r, c, $event)"></textarea>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-if="block.tableEditable === 'none'" class="hint none-hint">{{ block.tableEditReasons?.[0]?.message }}<button v-if="!readonly" type="button" class="text sm" @click="emit('upload')">在 Word 中修改后上传为新草稿</button></p>
  </div>
</template>

<style scoped>
.tpl-table { position:relative; display:grid; gap:var(--space-1); }
.table-tags { display:flex; justify-content:flex-end; }
.grid { width:100%; min-width:0; table-layout:fixed; border-collapse:collapse; }
.grid td { position:relative; padding:0; border:1px solid var(--border-strong); white-space:normal; vertical-align:top; font-size:var(--text-sm); }
.grid td.locked, .grid.frozen td { background:var(--gray-50); }
.cell-text { display:block; padding:var(--space-1) var(--space-2); min-height:28px; white-space:pre-wrap; overflow-wrap:anywhere; color:var(--text-secondary); }
.lock { position:absolute; top:2px; right:2px; width:12px; height:12px; fill:none; stroke:var(--text-tertiary); stroke-width:1.2; }
.cell-input { display:block; width:100%; min-height:28px; padding:var(--space-1) var(--space-2); border:0; background:transparent; color:var(--text-primary); font:inherit; resize:none; }
.cell-input:focus { outline:var(--focus-outline); outline-offset:-2px; background:var(--brand-50); }
.none-hint { display:flex; align-items:center; gap:var(--space-2); margin:0; }
</style>
