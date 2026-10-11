<script setup lang="ts">
/** A06 格式面板（16 §3.2 L-S）：白名单样式的字体、字号、缩进、行距、段前段后；改动后 300ms 防抖计算影响段数。 */
import { computed, onBeforeUnmount, ref } from "vue";
import { templateFormatImpact, type ImpactItem, type StyleValues } from "../../api/templates";
import { SIZE_NAMES, STYLE_PROP_LABELS, impactText } from "../../templateEditing";

const props = defineProps<{ documentId: string; styles: Record<string, StyleValues>; baseStyles: Record<string, StyleValues>; readonly: boolean }>();
const emit = defineEmits<{ change: [style: string, prop: string, value: any]; locate: [ids: string[]] }>();
const ORDER = ["正文", "标题 1", "标题 2", "标题 3", "标题 4", "标题 5", "标题 6", "表格正文", "题注"];
const names = computed(() => ORDER.filter(name => props.styles[name]));
const open = ref<string>("正文");
type Impact = { state: "loading" | "ready" | "error"; item?: ImpactItem };
const impacts = ref<Record<string, Impact>>({});
const timers = new Map<string, number>();
const errors = ref<Record<string, string>>({});

function key(style: string, prop: string) { return `${style}|${prop}`; }
function value(style: string, prop: string) { return props.styles[style]?.[prop] ?? null; }
function changed(style: string, prop: string) { return JSON.stringify(value(style, prop)) !== JSON.stringify(props.baseStyles[style]?.[prop] ?? null); }

function set(style: string, prop: string, next: any) {
  const id = key(style, prop);
  errors.value = { ...errors.value, [id]: "" };
  emit("change", style, prop, next);
  window.clearTimeout(timers.get(id));
  impacts.value = { ...impacts.value, [id]: { state: "loading" } };
  timers.set(id, window.setTimeout(async () => {
    try {
      const [item] = await templateFormatImpact(props.documentId, [{ style, prop, value: next }]);
      impacts.value = { ...impacts.value, [id]: { state: "ready", item } };
    } catch { impacts.value = { ...impacts.value, [id]: { state: "error" } }; }
  }, 300));
}

function setFont(style: string, prop: string, event: Event) {
  const text = (event.target as HTMLInputElement).value.trim();
  if (!text || text.length > 64 || text.includes("（")) { errors.value = { ...errors.value, [key(style, prop)]: "请填写字体名称（最多 64 个字）。" }; return; }
  if (text !== value(style, prop)) set(style, prop, text);
}
function setSize(style: string, event: Event) { set(style, "size", Number((event.target as HTMLSelectElement).value)); }
function sizeOptions(style: string) {
  const current = value(style, "size");
  const list = SIZE_NAMES.map(([name, pt]) => ({ label: `${name}（${pt} 磅）`, value: pt }));
  if (current != null && !list.some(item => item.value === current)) list.push({ label: `${current} 磅`, value: current });
  return list;
}
function measure(style: string, prop: string) {
  const current = value(style, prop);
  const fallback = prop.endsWith("Indent") ? "chars" : "pt";
  return { unit: current?.unit ?? fallback, value: current?.value ?? "" };
}
function setMeasure(style: string, prop: string, part: "unit" | "value", event: Event) {
  const current = measure(style, prop);
  const raw = (event.target as HTMLInputElement | HTMLSelectElement).value;
  const next = { ...current, [part]: part === "value" ? Number(raw) : raw };
  const limit = prop.endsWith("Indent") ? (next.unit === "chars" ? 40 : 400) : (next.unit === "pt" ? 100 : 10);
  const low = prop.endsWith("Indent") ? -20 : 0;
  if (next.value === "" || Number.isNaN(Number(next.value)) || Number(next.value) < low || Number(next.value) > limit) {
    errors.value = { ...errors.value, [key(style, prop)]: `请填写 ${low}–${limit} 之间的数值。` };
    return;
  }
  set(style, prop, { unit: next.unit, value: Number(next.value) });
}
function spacingMode(style: string) {
  const text = String(value(style, "lineSpacing") ?? "1");
  if (text.startsWith("exact:")) return "exact";
  if (text.startsWith("atLeast:")) return "atLeast";
  return ["1", "1.5", "2"].includes(text) ? text : "multiple";
}
function spacingNumber(style: string) { const text = String(value(style, "lineSpacing") ?? "1"); return text.includes(":") ? text.split(":")[1] : text; }
function setSpacingMode(style: string, event: Event) {
  const mode = (event.target as HTMLSelectElement).value;
  set(style, "lineSpacing", mode === "exact" ? "exact:22" : mode === "atLeast" ? "atLeast:12" : mode === "multiple" ? "1.25" : mode);
}
function setSpacingNumber(style: string, event: Event) {
  const raw = (event.target as HTMLInputElement).value.trim();
  const mode = spacingMode(style);
  const ok = mode === "multiple" ? /^[0-9](\.[0-9]{1,2})?$/.test(raw) : /^[0-9]{1,2}(\.[05])?$/.test(raw);
  if (!ok) { errors.value = { ...errors.value, [key(style, "lineSpacing")]: mode === "multiple" ? "倍数请填写 0–9.99。" : "磅值请填写 1–99，可带 .5。" }; return; }
  set(style, "lineSpacing", mode === "multiple" ? raw : `${mode}:${raw}`);
}
function reset(style: string, prop: string) { const base = props.baseStyles[style]?.[prop]; if (base != null) set(style, prop, base); }
onBeforeUnmount(() => timers.forEach(timer => window.clearTimeout(timer)));
</script>

<template>
  <div class="format-panel" data-testid="tpl-format-panel">
    <p class="notice info lead">修改的是样式，会作用到全文所有使用该样式的段落。</p>
    <section v-for="style in names" :key="style" class="style-block">
      <button type="button" class="style-head" :aria-expanded="open === style" @click="open = open === style ? '' : style">
        <span>{{ style }}</span><span v-if="Object.keys(STYLE_PROP_LABELS).some(prop => changed(style, prop))" class="tag info">已修改</span>
        <svg class="ico chevron" viewBox="0 0 16 16" aria-hidden="true"><path :d="open === style ? 'M4 10l4-4 4 4' : 'M4 6l4 4 4-4'" /></svg>
      </button>
      <div v-if="open === style" class="props">
        <div v-for="(label, prop) in STYLE_PROP_LABELS" :key="prop" class="prop" :data-prop="prop">
          <label class="prop-label" :for="`fmt-${style}-${prop}`">{{ label }}<button v-if="changed(style, prop) && !readonly" type="button" class="text sm" @click="reset(style, prop)">还原</button></label>
          <input v-if="prop === 'fontEastAsia' || prop === 'fontAscii'" :id="`fmt-${style}-${prop}`" class="control" type="text" maxlength="64" :value="value(style, prop) ?? ''" :disabled="readonly" @change="setFont(style, prop, $event)" />
          <select v-else-if="prop === 'size'" :id="`fmt-${style}-${prop}`" class="control" :value="value(style, prop)" :disabled="readonly" :data-testid="`tpl-format-size-${style}`" @change="setSize(style, $event)">
            <option v-if="value(style, prop) == null" :value="null" disabled>未设置</option>
            <option v-for="item in sizeOptions(style)" :key="item.value" :value="item.value">{{ item.label }}</option>
          </select>
          <div v-else-if="prop === 'lineSpacing'" class="pair">
            <select :id="`fmt-${style}-${prop}`" class="control" :value="spacingMode(style)" :disabled="readonly" @change="setSpacingMode(style, $event)">
              <option value="1">单倍</option><option value="1.5">1.5 倍</option><option value="2">2 倍</option><option value="multiple">多倍</option><option value="exact">固定值</option><option value="atLeast">最小值</option>
            </select>
            <template v-if="!['1', '1.5', '2'].includes(spacingMode(style))"><input class="control num" type="text" inputmode="decimal" :aria-label="`${style}${label}数值`" :value="spacingNumber(style)" :disabled="readonly" @change="setSpacingNumber(style, $event)" /><span class="unit">{{ spacingMode(style) === 'multiple' ? '倍' : '磅' }}</span></template>
          </div>
          <div v-else class="pair">
            <input :id="`fmt-${style}-${prop}`" class="control num" type="number" step="0.5" :value="measure(style, prop).value" :disabled="readonly" @change="setMeasure(style, prop, 'value', $event)" />
            <select class="control unit-select" :aria-label="`${style}${label}单位`" :value="measure(style, prop).unit" :disabled="readonly" @change="setMeasure(style, prop, 'unit', $event)">
              <template v-if="prop.endsWith('Indent')"><option value="chars">字符</option><option value="pt">磅</option></template>
              <template v-else><option value="pt">磅</option><option value="lines">行</option></template>
            </select>
          </div>
          <span v-if="errors[`${style}|${prop}`]" class="field-error" role="alert">{{ errors[`${style}|${prop}`] }}</span>
          <p v-if="impacts[`${style}|${prop}`]" class="hint impact" :class="{ warn: impacts[`${style}|${prop}`].item && impacts[`${style}|${prop}`].item!.willClear === 0 && impacts[`${style}|${prop}`].item!.directFormatParagraphs > 0 }" data-testid="tpl-format-impact" role="status">
            <template v-if="impacts[`${style}|${prop}`].state === 'loading'">正在计算影响…</template>
            <template v-else-if="impacts[`${style}|${prop}`].state === 'error'">暂时无法计算影响范围</template>
            <template v-else-if="impacts[`${style}|${prop}`].item">
              <span :data-affected="impacts[`${style}|${prop}`].item!.affectedParagraphs" :data-direct="impacts[`${style}|${prop}`].item!.directFormatParagraphs">{{ impactText(impacts[`${style}|${prop}`].item!) }}</span>
              <button v-if="impacts[`${style}|${prop}`].item!.directBlockIds.length" type="button" class="text sm" data-testid="tpl-format-impact-view" @click="emit('locate', impacts[`${style}|${prop}`].item!.directBlockIds)">查看这 {{ impacts[`${style}|${prop}`].item!.directFormatParagraphs }} 段</button>
              <span v-if="impacts[`${style}|${prop}`].item!.truncated">（仅显示前 500 段）</span>
            </template>
          </p>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.format-panel { display:grid; align-content:start; }
.lead { border-radius:0; }
.style-block { border-bottom:1px solid var(--border-subtle); }
.style-head { display:flex; align-items:center; gap:var(--space-2); width:100%; height:44px; padding:0 var(--space-4); border:0; background:transparent; color:var(--text-primary); font:inherit; font-size:var(--text-sm); font-weight:var(--weight-medium); text-align:left; cursor:pointer; }
.style-head:hover { background:var(--bg-hover); }
.style-head .chevron { margin-left:auto; color:var(--text-tertiary); }
.props { display:grid; gap:var(--space-3); padding:0 var(--space-4) var(--space-4); }
.prop { display:grid; gap:var(--space-1); }
.prop-label { display:flex; align-items:center; justify-content:space-between; color:var(--text-secondary); font-size:var(--text-xs); }
.prop .control { width:100%; }
.pair { display:flex; align-items:center; gap:var(--space-2); }
.pair .control.num { width:96px; flex:none; }
.pair .unit-select { width:88px; flex:none; }
.unit { color:var(--text-tertiary); font-size:var(--text-xs); }
.impact { margin:0; }
.impact.warn { color:var(--warning-600); }
</style>
