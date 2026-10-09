<script setup lang="ts">
import { computed, ref, toRaw, watch } from "vue";
import { templateBlockLabels, type TemplateBlockKind, type TemplateImportStructure } from "../api/templates";
const props = defineProps<{ structure: TemplateImportStructure; busy: boolean; error: string; saveLabel: string }>();
const emit = defineEmits<{ save: [structure: TemplateImportStructure]; cancel: []; change: [] }>();
const draft = ref(structuredClone(toRaw(props.structure)));
const initial = draft.value.blocks.find(block => block.kind === "guidance") ?? draft.value.blocks[0];
const section = ref(initial?.headingId ?? "all");
const kind = ref<TemplateBlockKind | "all">("all");
const query = ref("");
const selectedId = ref(initial?.id ?? "");
const headings = computed(() => draft.value.blocks.filter(block => block.kind === "heading"));
const visibleBlocks = computed(() => draft.value.blocks.filter(block =>
  (section.value === "all" || (section.value === "front" ? !block.headingId && block.kind !== "heading" : block.id === section.value || block.headingId === section.value))
  && (kind.value === "all" || block.kind === kind.value)
  && (!query.value.trim() || block.text.includes(query.value.trim()))));
const selected = computed(() => draft.value.blocks.find(block => block.id === selectedId.value));
const precedingHeadings = computed(() => headings.value.filter(heading =>
  draft.value.blocks.indexOf(heading) < draft.value.blocks.findIndex(block => block.id === selectedId.value)));
const invalid = computed(() => draft.value.blocks.some(block =>
  (["guidance", "example", "placeholder"].includes(block.kind) && block.protected)
  || (block.kind === "guidance" && !block.text.trim())
  || (block.kind === "guidance" && !headings.value.some(heading => heading.id === block.headingId))
  || (block.kind === "heading" && (!block.headingLevel || block.headingLevel < 1 || block.headingLevel > 6))));
const missingHeadings = computed(() => headings.value.length === 0);
watch(visibleBlocks, (blocks) => {
  if (!blocks.some(block => block.id === selectedId.value)) selectedId.value = blocks[0]?.id ?? "";
});
function changeKind(event: Event) {
  if (!selected.value) return;
  const next = (event.target as HTMLSelectElement).value as TemplateBlockKind;
  selected.value.kind = next;
  selected.value.headingLevel = next === "heading" ? selected.value.headingLevel ?? 2 : null;
  if (next === "heading") selected.value.headingId = null;
  if (next !== "guidance") selected.value.text = selected.value.sourceText;
  let headingId: string | null = null;
  for (const block of draft.value.blocks) {
    if (block.kind === "heading") headingId = block.id;
    else if (block.kind !== "guidance") block.headingId = headingId;
  }
  emit("change");
}
function save() {
  if (invalid.value || missingHeadings.value || props.busy) return;
  emit("save", { ...toRaw(draft.value), reviewed: true });
}
</script>

<template>
  <section class="import-editor" aria-label="模板内容核对" :aria-busy="busy">
    <div class="review-tools">
      <h2>内容核对</h2>
      <label>内容类型<select v-model="kind" class="control" :disabled="busy"><option value="all">全部类型</option><option v-for="(label, value) in templateBlockLabels" :key="value" :value="value">{{ label }}</option></select></label>
      <label>查找内容<input v-model="query" class="control" type="search" :disabled="busy" /></label>
    </div>
    <p v-if="error" class="review-error" role="alert">{{ error }}</p>
    <div class="review-workspace">
      <nav class="review-outline" aria-label="模板章节">
        <button class="ghost" type="button" :aria-pressed="section === 'all'" :disabled="busy" @click="section = 'all'">全部内容</button>
        <button class="ghost" type="button" :aria-pressed="section === 'front'" :disabled="busy" @click="section = 'front'">封面与目录</button>
        <button v-for="heading in headings" :key="heading.id" class="ghost" type="button" :aria-pressed="section === heading.id" :disabled="busy" :style="{ paddingLeft: `calc(var(--space-3) + var(--space-3) * ${Math.max(0, (heading.headingLevel ?? 1) - 1)})` }" @click="section = heading.id">{{ heading.text }}</button>
      </nav>
      <div class="review-content" aria-label="识别内容">
        <p v-if="!visibleBlocks.length" class="review-empty">没有符合条件的内容。</p>
        <button v-for="block in visibleBlocks" :key="block.id" type="button" class="review-block" :class="{ selected: selectedId === block.id, heading: block.kind === 'heading', guidance: block.kind === 'guidance' }" :aria-pressed="selectedId === block.id" :disabled="busy" @click="selectedId = block.id">
          <span class="block-kind">{{ templateBlockLabels[block.kind] }}</span>
          <span class="block-text">{{ block.text || '空白填写位置' }}</span>
        </button>
      </div>
      <form v-if="selected" class="review-settings" @submit.prevent="save">
        <h3>选中内容</h3>
        <label>用途<select class="control" :value="selected.kind" :disabled="busy" @change="changeKind"><option v-for="(label, value) in templateBlockLabels" :key="value" :value="value" :disabled="selected.isTable ? !['table', 'cover'].includes(value) : value === 'table' || (value === 'heading' && !!selected.tableId)">{{ label }}</option></select></label>
        <label v-if="selected.kind === 'heading'">标题级别<select v-model.number="selected.headingLevel" class="control" :disabled="busy" @change="emit('change')"><option v-for="level in 6" :key="level" :value="level">{{ level }} 级标题</option></select></label>
        <label v-if="selected.kind === 'guidance'">适用章节<select v-model="selected.headingId" class="control" :disabled="busy" @change="emit('change')"><option :value="null">请选择章节</option><option v-for="heading in precedingHeadings" :key="heading.id" :value="heading.id">{{ heading.text }}</option></select></label>
        <label v-if="selected.kind === 'guidance'">AI 提示词<textarea v-model="selected.text" class="control guidance-input" rows="10" :disabled="busy" @input="emit('change')"></textarea></label>
        <p v-if="selected.kind === 'guidance'" class="usage-note">编写本节时发送给 AI，不写入正式正文。</p>
        <p v-if="selected.kind === 'example'" class="usage-note">作为编写参考，不写入正式正文。</p>
        <p v-if="selected.protected" class="usage-note">包含 Word 域、对象或分页结构。</p>
        <p v-if="selected.protected && ['guidance', 'example', 'placeholder'].includes(selected.kind)" class="field-error" role="alert">此内容不能安全移出正文，请调整用途或修改源 Word 后重新导入。</p>
        <p v-if="selected.kind === 'guidance' && !selected.text.trim()" class="field-error" role="alert">请填写编写要求。</p>
        <p v-if="selected.kind === 'guidance' && !headings.some(heading => heading.id === selected?.headingId)" class="field-error" role="alert">请选择有效的适用章节。</p>
        <p v-if="selected.warning" class="usage-note">{{ selected.warning }}</p>
        <details v-if="selected.kind === 'guidance' && selected.text !== selected.sourceText"><summary>查看原始指导</summary><p class="source-text">{{ selected.sourceText }}</p></details>
        <div v-if="selected.kind !== 'guidance'" class="source-text">{{ selected.text || '空白填写位置' }}</div>
      </form>
      <div v-else class="review-settings"><p class="review-empty">请选择要核对的内容。</p></div>
    </div>
    <div class="review-footer">
      <p v-if="missingHeadings" class="field-error" role="alert">请至少保留一个标题。</p>
      <p v-else-if="invalid" class="field-error" role="alert">请检查 AI 提示词、适用章节及特殊 Word 结构。</p>
      <button type="button" class="ghost" :disabled="busy" @click="emit('cancel')">取消</button>
      <button type="button" class="primary" :disabled="busy || invalid || missingHeadings || !draft.blocks.length" @click="save">{{ busy ? '正在保存…' : saveLabel }}</button>
    </div>
  </section>
</template>

<style scoped>
.import-editor { display:flex; flex:1; flex-direction:column; min-height:0; }
.review-tools { display:flex; flex:none; align-items:center; gap:var(--space-5); padding:var(--space-3) var(--space-4); border-block:1px solid var(--rule); }
.review-tools h2 { margin:0 auto 0 0; font-size:var(--text-md); }
.review-tools label { display:flex; align-items:center; gap:var(--space-2); }
.review-tools .control { width:calc(var(--space-7) * 4); }
.review-error { margin:0; padding:var(--space-3) var(--space-4); background:var(--fault-wash); color:var(--fault-ink); }
.review-workspace { display:grid; grid-template-columns:calc(var(--space-7) * 5) minmax(0,1fr) calc(var(--space-7) * 7); flex:1; min-height:0; }
.review-outline,.review-content,.review-settings { overflow:auto; min-width:0; min-height:0; padding:var(--space-4); }
.review-outline { display:flex; flex-direction:column; align-items:stretch; gap:var(--space-1); border-right:1px solid var(--rule); }
.review-outline .ghost { flex:none; height:auto; min-height:var(--space-6); text-align:left; white-space:normal; overflow-wrap:anywhere; border-color:transparent; font-weight:400; padding-block:var(--space-2); }
.review-outline .ghost[aria-pressed=true] { color:var(--stamp); background:var(--stamp-wash); font-weight:600; }
.review-content { background:var(--desk); }
.review-block { display:grid; grid-template-columns:calc(var(--space-7) * 2 + var(--space-3)) minmax(0,1fr); width:100%; margin-bottom:var(--space-2); padding:var(--space-4); gap:var(--space-3); border:1px solid var(--rule); border-radius:var(--radius-sheet); background:var(--sheet); color:var(--ink); font:inherit; text-align:left; cursor:pointer; }
.review-block:hover:not(:disabled) { border-color:var(--rule-strong); }
.review-block.selected { border-color:var(--stamp); box-shadow:inset var(--space-1) 0 var(--stamp); }
.review-block:focus-visible { outline:2px solid var(--stamp); outline-offset:2px; }
.review-block:disabled { cursor:not-allowed; }
.block-kind { color:var(--ink-secondary); font-size:var(--text-sm); }
.block-text { white-space:pre-wrap; overflow-wrap:anywhere; line-height:1.7; }
.review-block.heading .block-text { font-size:var(--text-md); font-weight:600; }
.review-block.guidance .block-kind { color:var(--stamp); }
.review-settings { border-left:1px solid var(--rule); display:flex; flex-direction:column; gap:var(--space-4); }
.review-settings h3 { margin:0; font-size:var(--text-md); }
.review-settings label { display:grid; gap:var(--space-2); font-weight:600; }
.review-settings .control { width:100%; }
.guidance-input { resize:vertical; min-height:calc(var(--space-7) * 3); font:inherit; line-height:1.7; }
.usage-note { margin:0; color:var(--ink-secondary); }
.source-text { white-space:pre-wrap; overflow-wrap:anywhere; line-height:1.7; margin:0; }
.review-empty { padding:var(--space-5); text-align:center; color:var(--ink-secondary); }
.review-footer { display:flex; flex:none; align-items:center; justify-content:flex-end; gap:var(--space-3); padding:var(--space-3) var(--space-4); border-top:1px solid var(--rule); }
.review-footer .field-error { margin:0 auto 0 0; }
summary { cursor:pointer; }
summary:focus-visible { outline:2px solid var(--stamp); outline-offset:2px; }
</style>
