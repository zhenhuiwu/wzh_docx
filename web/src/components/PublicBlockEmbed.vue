<script setup lang="ts">
/** 公共框架在文档中的只读引用卡片（30 §3.4）：标签“公共：名称 · 负责人”，有更新时提示，可标记已核对。 */
import { ref } from "vue";
import { ApiError } from "../api/client";
import { markPublicSeen, type PublicRef } from "../api/docChain";

const props = defineProps<{ documentId: string; reference: PublicRef; planLink?: Record<string, unknown> | null }>();
const emit = defineEmits<{ (e: "seen", blockId: string): void }>();
const busy = ref(false);
const error = ref("");

async function seen() {
  busy.value = true;
  error.value = "";
  try { await markPublicSeen(props.documentId, props.reference.block.id); emit("seen", props.reference.block.id); }
  catch (e) { error.value = e instanceof ApiError ? e.message : "暂时不能连接服务。"; }
  finally { busy.value = false; }
}
</script>

<template>
  <aside class="embed" :aria-label="`公共内容：${reference.block.name}`" data-testid="public-embed">
    <header class="head">
      <span class="tag info">公共：{{ reference.block.name }} · {{ reference.block.assignee.name || "未分配" }}</span>
      <span v-if="reference.updated" class="tag warning" role="status">公共内容有更新</span>
      <span class="spacer" />
      <router-link v-if="planLink" class="link" :to="planLink">去公共内容修改</router-link>
      <button v-if="reference.updated" type="button" class="ghost" :disabled="busy" @click="seen">{{ busy ? "正在标记…" : "标记为已核对" }}</button>
    </header>
    <p v-if="reference.block.body" class="body">{{ reference.block.body }}</p>
    <p v-else class="body empty">公共内容还没有编写。</p>
    <p class="note">此处只读，只能在公共内容里修改。</p>
    <p v-if="error" class="form-alert" role="alert">{{ error }}</p>
  </aside>
</template>

<style scoped>
.embed { display:grid; gap:var(--space-2); padding:var(--space-3) var(--space-4); border:1px solid var(--border-default); border-left:2px solid var(--stamp); border-radius:var(--radius-sheet); background:var(--bg-subtle); }
.head { display:flex; align-items:center; gap:var(--space-2); }
.spacer { flex:1; }
.body { margin:0; color:var(--text-primary); white-space:pre-wrap; overflow-wrap:anywhere; }
.body.empty, .note { color:var(--text-tertiary); font-size:var(--text-sm); }
.note { margin:0; font-size:var(--text-xs); }
.link { color:var(--text-link); font-size:var(--text-sm); }
</style>
