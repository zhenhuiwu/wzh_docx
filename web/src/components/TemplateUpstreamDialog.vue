<script setup lang="ts">
/** 模板管理 › “…” › 设置上游模板（30 §3.6）。 */
import { nextTick, ref, watch } from "vue";
import { ApiError } from "../api/client";
import { getTemplateUpstreams, setTemplateUpstreams, type TemplateRef } from "../api/docChain";

const props = defineProps<{ templateDocumentId: string | null; name: string }>();
const emit = defineEmits<{ (e: "close"): void; (e: "saved", value: TemplateRef[]): void }>();
const dialog = ref<HTMLDialogElement | null>(null);
const candidates = ref<TemplateRef[]>([]);
const chosen = ref<string[]>([]);
const phase = ref<"loading" | "ready" | "error">("loading");
const error = ref("");
const busy = ref(false);

watch(() => props.templateDocumentId, async (id) => {
  if (!id) { if (dialog.value?.open) dialog.value.close(); return; }
  await nextTick(); dialog.value?.showModal();
  phase.value = "loading"; error.value = "";
  try { const data = await getTemplateUpstreams(id); candidates.value = data.candidates; chosen.value = data.upstreams.map((u) => u.id); phase.value = "ready"; }
  catch (e) { phase.value = "error"; error.value = e instanceof ApiError ? e.message : "暂时不能连接服务。"; }
});

async function save() {
  if (!props.templateDocumentId || busy.value) return;
  busy.value = true; error.value = "";
  try { emit("saved", await setTemplateUpstreams(props.templateDocumentId, chosen.value)); }
  catch (e) { error.value = e instanceof ApiError ? e.message : "暂时不能连接服务。"; }
  finally { busy.value = false; }
}
</script>

<template>
  <dialog ref="dialog" class="app-dialog upstream-dialog" aria-labelledby="tpl-up-title" @cancel.prevent="!busy && emit('close')">
    <form novalidate :aria-busy="busy" @submit.prevent="save">
      <div class="dialog-head"><h2 id="tpl-up-title">设置上游模板：{{ name }}</h2><button type="button" class="subtle icon-close" aria-label="关闭" :disabled="busy" @click="emit('close')"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg></button></div>
      <div class="dialog-body list">
        <p v-if="phase === 'loading'" role="status">正在读取模板…</p>
        <p v-else-if="phase === 'ready' && !candidates.length" class="muted">没有其他模板可选。</p>
        <fieldset v-else-if="phase === 'ready'" :disabled="busy"><legend class="muted">从这些模板创建的文档会作为上游，用于依赖链和创建时预填。</legend>
          <label v-for="c in candidates" :key="c.id" class="check"><input v-model="chosen" type="checkbox" :value="c.id" />{{ c.name }}<span class="muted">（{{ c.groupName }}）</span></label>
        </fieldset>
        <p v-if="error" class="form-alert" role="alert">{{ error }}</p>
      </div>
      <div class="dialog-actions"><button type="button" class="ghost" :disabled="busy" @click="emit('close')">取消</button><button type="submit" class="primary" :disabled="busy || phase !== 'ready'">{{ busy ? "正在保存…" : "保存" }}</button></div>
    </form>
  </dialog>
</template>

<style scoped>
.upstream-dialog { width:560px; }
.list { display:grid; gap:var(--space-2); max-height:calc(var(--space-7) * 9); overflow:auto; }
fieldset { display:grid; gap:var(--space-2); margin:0; padding:0; border:0; }
.check { display:flex; align-items:center; gap:var(--space-2); }
.muted { color:var(--text-tertiary); font-size:var(--text-sm); }
</style>
