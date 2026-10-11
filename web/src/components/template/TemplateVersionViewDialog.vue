<script setup lang="ts">
/** A06 查看某个版本（只读）：按块列出标题、正文、指导与表格。 */
import { nextTick, onMounted, ref } from "vue";
import StatePanel from "../StatePanel.vue";
import { getTemplateVersion, type EditorStructure } from "../../api/templates";
import { EDITOR_KIND_LABELS } from "../../templateEditing";

const props = defineProps<{ documentId: string; versionId: string; seq: number }>();
const emit = defineEmits<{ close: [] }>();
const dialog = ref<HTMLDialogElement | null>(null);
const phase = ref<"loading" | "ready" | "error">("loading");
const errorText = ref("");
const structure = ref<EditorStructure | null>(null);
async function load() {
  phase.value = "loading";
  try { structure.value = (await getTemplateVersion(props.documentId, props.versionId)).structure; phase.value = "ready"; }
  catch (error) { phase.value = "error"; errorText.value = error instanceof Error ? error.message : "暂时不能连接服务。"; }
}
onMounted(() => { void nextTick(() => dialog.value?.showModal()); void load(); });
</script>

<template>
  <dialog ref="dialog" class="app-dialog wide" aria-labelledby="tpl-view-title" data-testid="tpl-version-view" @cancel.prevent="emit('close')">
    <header class="dialog-head"><h2 id="tpl-view-title">第 {{ seq }} 版（只读）</h2><button type="button" class="subtle" aria-label="关闭" @click="emit('close')">×</button></header>
    <StatePanel v-if="phase === 'loading'" kind="loading" text="正在读取版本…" />
    <StatePanel v-else-if="phase === 'error'" kind="error" title="版本没有载入" :text="errorText"><button type="button" class="ghost" @click="load">重试</button></StatePanel>
    <div v-else-if="structure" class="dialog-body view-body">
      <template v-for="block in structure.blocks" :key="block.id">
        <p v-if="!block.tableId" class="row" :class="block.kind" :style="block.kind === 'heading' ? { paddingLeft: `calc(var(--space-4) * ${(block.headingLevel ?? 1) - 1})` } : undefined">
          <span class="kind">{{ block.kind === 'heading' ? `标题 ${block.headingLevel}` : EDITOR_KIND_LABELS[block.kind] }}</span>
          <span class="text">{{ block.isTable ? (block.cells ?? []).map(row => row.join(' | ')).join('\n') || block.text : block.text }}</span>
        </p>
      </template>
    </div>
    <footer class="dialog-actions"><button type="button" class="ghost" @click="emit('close')">关闭</button></footer>
  </dialog>
</template>

<style scoped>
.view-body { display:grid; gap:var(--space-1); max-height:calc(100vh - var(--space-7) * 6); overflow:auto; }
.row { display:flex; gap:var(--space-3); margin:0; font-size:var(--text-sm); }
.row.heading { font-weight:var(--weight-semibold); }
.row.guidance .text { color:var(--info-600); }
.kind { flex:none; width:64px; color:var(--text-tertiary); font-size:var(--text-xs); font-weight:var(--weight-regular); }
.text { white-space:pre-wrap; overflow-wrap:anywhere; }
</style>
