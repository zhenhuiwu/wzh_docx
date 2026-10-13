<script setup lang="ts">
/** BlockPicker（30 §3.4）：左侧选文档，右侧章节树多选；已有负责人的章节显示其名字，选中时提示会覆盖。 */
import { computed, nextTick, ref, watch } from "vue";
import { ApiError } from "../api/client";
import { pickerChapters, type PickerChapter } from "../api/docChain";
import StatePanel from "./StatePanel.vue";

type Pick = { documentId: string; documentName: string; objectId: string; chapter: string };
const props = defineProps<{ open: boolean; projectId: string; documents: { id: string; name: string }[]; selected: Pick[]; assigneeId: string | null }>();
const emit = defineEmits<{ (e: "close"): void; (e: "confirm", value: Pick[]): void }>();
const dialog = ref<HTMLDialogElement | null>(null);
const current = ref<string | null>(null);
const chapters = ref<PickerChapter[]>([]);
const phase = ref<"idle" | "loading" | "ready" | "error">("idle");
const errorText = ref("");
const picks = ref<Pick[]>([]);

watch(() => props.open, (open) => {
  if (open) {
    picks.value = [...props.selected];
    void nextTick(() => dialog.value?.showModal());
    if (props.documents.length) void choose(current.value && props.documents.some((d) => d.id === current.value) ? current.value : props.documents[0].id);
  } else if (dialog.value?.open) dialog.value.close();
});

async function choose(id: string) {
  current.value = id;
  phase.value = "loading";
  try { chapters.value = await pickerChapters(props.projectId, id); phase.value = "ready"; }
  catch (error) { phase.value = "error"; errorText.value = error instanceof ApiError ? error.message : "暂时不能连接服务。"; }
}

const docName = computed(() => props.documents.find((d) => d.id === current.value)?.name ?? "");
function isPicked(c: PickerChapter) { return picks.value.some((p) => p.documentId === current.value && p.objectId === c.objectId); }
function toggle(c: PickerChapter) {
  if (!current.value) return;
  if (isPicked(c)) picks.value = picks.value.filter((p) => !(p.documentId === current.value && p.objectId === c.objectId));
  else picks.value = [...picks.value, { documentId: current.value, documentName: docName.value, objectId: c.objectId, chapter: c.title }];
}
function countIn(id: string) { return picks.value.filter((p) => p.documentId === id).length; }
function overrides(c: PickerChapter) { return isPicked(c) && c.owners.some((o) => o.id !== props.assigneeId); }
</script>

<template>
  <dialog ref="dialog" class="app-dialog picker" aria-labelledby="picker-title" @cancel.prevent="emit('close')">
    <div class="dialog-head"><h2 id="picker-title">选择涉及的章节</h2><button type="button" class="subtle icon-close" aria-label="关闭" @click="emit('close')"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg></button></div>
    <div class="dialog-body picker-body">
      <nav class="docs" aria-label="文档">
        <StatePanel v-if="!documents.length" title="项目里还没有文档" class="compact" />
        <button v-for="d in documents" :key="d.id" type="button" class="doc" :class="{ on: d.id === current }" :aria-pressed="d.id === current" @click="choose(d.id)">
          <span>{{ d.name }}</span><span v-if="countIn(d.id)" class="count">{{ countIn(d.id) }}</span>
        </button>
      </nav>
      <div class="tree">
        <StatePanel v-if="phase === 'loading'" kind="loading" text="正在读取章节…" class="compact" />
        <StatePanel v-else-if="phase === 'error'" kind="error" title="章节加载失败" :text="errorText" class="compact"><button type="button" class="ghost" @click="current && choose(current)">重试</button></StatePanel>
        <StatePanel v-else-if="phase === 'ready' && !chapters.length" title="这份文档还没有章节" class="compact" />
        <ul v-else role="tree" aria-multiselectable="true" :aria-label="docName">
          <li v-for="c in chapters" :key="c.objectId" role="treeitem" :aria-selected="isPicked(c)" :aria-level="c.level" :style="{ paddingLeft: `calc(var(--space-4) * ${c.level - 1})` }">
            <label class="row"><input type="checkbox" :checked="isPicked(c)" @change="toggle(c)" /><span class="title">{{ c.title }}</span>
              <span v-if="c.owners.length" class="owner">{{ c.owners.map((o) => o.name).join("、") }}</span>
              <span v-if="c.task" class="task">任务：{{ c.task }}</span>
            </label>
            <p v-if="overrides(c)" class="warn" role="note">将覆盖原负责人 {{ c.owners.map((o) => o.name).join("、") }}。</p>
          </li>
        </ul>
      </div>
    </div>
    <div class="dialog-actions"><span class="sum">已选 {{ picks.length }} 处</span><button type="button" class="ghost" @click="emit('close')">取消</button><button type="button" class="primary" @click="emit('confirm', picks)">确定</button></div>
  </dialog>
</template>

<style scoped>
.picker { width:960px; }
.picker-body { display:grid; grid-template-columns:260px 1fr; gap:var(--space-4); height:calc(var(--space-7) * 11); }
.docs { display:grid; align-content:start; gap:var(--space-1); overflow:auto; border-right:1px solid var(--border-subtle); padding-right:var(--space-3); }
.doc { display:flex; justify-content:space-between; gap:var(--space-2); padding:var(--space-2) var(--space-3); border:0; border-radius:var(--radius-control); background:none; color:var(--text-primary); font:inherit; text-align:left; cursor:pointer; }
.doc.on { background:var(--stamp-wash); color:var(--stamp); }
.doc:focus-visible { outline:none; box-shadow:var(--focus-shadow); }
.tree { overflow:auto; }
.tree ul { margin:0; padding:0; list-style:none; display:grid; gap:var(--space-1); }
.row { display:flex; align-items:center; gap:var(--space-2); padding:var(--space-1) 0; }
.title { overflow-wrap:anywhere; }
.owner, .task { color:var(--text-tertiary); font-size:var(--text-xs); }
.warn { margin:0 0 0 var(--space-6); color:var(--fault); font-size:var(--text-xs); }
.sum { margin-right:auto; color:var(--text-secondary); font-size:var(--text-sm); }
</style>
