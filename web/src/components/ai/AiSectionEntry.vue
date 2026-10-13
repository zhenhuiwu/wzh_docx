<script setup lang="ts">
/** Spec 30 §4.1: floating [AI 编写本节] beside the heading of the section the caret is in. */
import { onMounted, onUnmounted, ref, watch } from "vue";
import { entryLeft } from "../../aiEntry";

const props = defineProps<{ editor: HTMLElement | null; headingIndex: number | null; disabled: boolean; hidden: boolean; version: number }>();
const emit = defineEmits<{ start: [] }>();
const position = ref<{ top: number; left: number } | null>(null);
const button = ref<HTMLButtonElement | null>(null);
let frame = 0;
let resize: ResizeObserver | null = null;

/** Left edge of a side panel docked to the right of the paper (assistant, history…), so the entry never covers it. */
function panelLeft(paper: DOMRect): number | null {
  let left: number | null = null;
  for (const panel of document.querySelectorAll<HTMLElement>("aside.panel")) {
    const rect = panel.getBoundingClientRect();
    if (rect.width > 0 && rect.left >= paper.left) left = left === null ? rect.left : Math.min(left, rect.left);
  }
  return left;
}

function heading(): HTMLElement | null {
  if (!props.editor || props.headingIndex === null) return null;
  return (props.editor.querySelectorAll("h1,h2,h3,h4,h5,h6")[props.headingIndex] as HTMLElement | undefined) ?? null;
}
function place() {
  cancelAnimationFrame(frame);
  frame = requestAnimationFrame(() => {
    const node = heading();
    const paper = props.editor?.getBoundingClientRect();
    if (!node || !paper || props.hidden) { position.value = null; return; }
    const rect = node.getBoundingClientRect();
    if (rect.bottom < 0 || rect.top > window.innerHeight) { position.value = null; return; }
    const width = button.value?.offsetWidth || 120;
    position.value = { top: rect.top + rect.height / 2, left: entryLeft({ paperRight: paper.right, panelLeft: panelLeft(paper), viewportWidth: window.innerWidth, width }) };
  });
}
watch(() => [props.headingIndex, props.hidden, props.version, props.editor], place);
onMounted(() => { window.addEventListener("scroll", place, true); window.addEventListener("resize", place); resize = new ResizeObserver(place); observe(props.editor); place(); });
/** The paper and its containers: one of them shrinks when a side panel opens and moves the paper. */
function ancestors(node: HTMLElement | null): HTMLElement[] {
  const out: HTMLElement[] = [];
  for (let item = node; item && item !== document.body && out.length < 6; item = item.parentElement) out.push(item);
  return out;
}
function observe(node: HTMLElement | null) { for (const item of ancestors(node)) resize?.observe(item); }
watch(() => props.editor, (node, old) => { for (const item of ancestors(old ?? null)) resize?.unobserve(item); observe(node); });
onUnmounted(() => { window.removeEventListener("scroll", place, true); window.removeEventListener("resize", place); cancelAnimationFrame(frame); resize?.disconnect(); });
</script>

<template>
  <button v-if="position" ref="button" type="button" class="ghost sm ai-entry" data-testid="ai-section-entry" :style="{ top: `${position.top}px`, left: `${position.left}px` }"
          :disabled="disabled" @mousedown.prevent @click="emit('start')">
    <svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" /></svg>AI 编写本节
  </button>
</template>

<style scoped>
.ai-entry { position:fixed; z-index:15; transform:translateY(-50%); display:inline-flex; align-items:center; gap:var(--space-1); white-space:nowrap; background:var(--sheet); box-shadow:var(--shadow-1); }
.ai-entry .ico { width:var(--icon-sm); height:var(--icon-sm); }
</style>
