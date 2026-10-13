<script setup lang="ts">
/**
 * 工作台提示条「上游有新基线」（30 §3.5，41 P1-3）：「需求规格说明 有新基线 B2［查看变化］［标记为已核对］」。
 * 不改正文、不强制同步（Q-5）；只读成员看不到“标记为已核对”。
 */
import { ref, watch } from "vue";
import { useRouter } from "vue-router";
import { ApiError } from "../api/client";
import { ackUpstream, baselineCompareRoute, getUpstreamNotices, type UpstreamBaseline } from "../api/docChain";

const props = defineProps<{ documentId: string; projectId: string }>();
const router = useRouter();
const notices = ref<UpstreamBaseline[]>([]);
const canAck = ref(false);
const busy = ref<string | null>(null);
const errorText = ref("");

async function load() {
  try {
    const data = await getUpstreamNotices(props.documentId);
    notices.value = data.notices; canAck.value = data.canAck; errorText.value = "";
  } catch { notices.value = []; } // 提示条加载失败不打扰编写
}
async function ack(item: UpstreamBaseline) {
  busy.value = item.documentId; errorText.value = "";
  try {
    await ackUpstream(props.documentId, item.documentId);
    notices.value = notices.value.filter((n) => n.documentId !== item.documentId);
  } catch (error) { errorText.value = error instanceof ApiError ? error.message : "暂时不能连接服务。"; }
  finally { busy.value = null; }
}
watch(() => props.documentId, load, { immediate: true });
defineExpose({ reload: load });
</script>

<template>
  <div v-if="notices.length" class="upstream-notices" data-testid="upstream-notice">
    <p v-for="item in notices" :key="item.documentId" class="un-row" role="status">
      <span>{{ item.name }} 有新基线 B{{ item.latestBaselineSeq }}<template v-if="item.baseBaselineSeq">（本文档基于 B{{ item.baseBaselineSeq }}）</template></span>
      <span class="acts">
        <button type="button" class="ghost sm" @click="router.push(baselineCompareRoute(projectId, item))">查看变化</button>
        <button v-if="canAck" type="button" class="ghost sm" :disabled="busy === item.documentId" @click="ack(item)">{{ busy === item.documentId ? "正在标记…" : "标记为已核对" }}</button>
      </span>
    </p>
    <p v-if="errorText" class="un-row error" role="alert">{{ errorText }}</p>
  </div>
</template>

<style scoped>
.un-row { display:flex; align-items:center; gap:var(--space-2); min-height:var(--notice-h); margin:0; padding:var(--space-1) var(--space-4); border-bottom:1px solid var(--border-default); background:var(--warning-50); color:var(--gray-800); font-size:var(--text-sm); line-height:var(--leading-sm); }
.un-row > span:first-child { min-width:0; overflow-wrap:anywhere; }
.un-row .acts { margin-left:auto; display:flex; gap:var(--space-2); flex-shrink:0; white-space:nowrap; }
.un-row.error { background:var(--danger-50); }
</style>
