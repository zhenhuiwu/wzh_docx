<script setup lang="ts">
/** A06 版本面板（16 §3.3）：原始文件固定在最上方；每版可查看、对比、恢复为草稿、下载发布时的 Word。 */
import { templateVersionFileUrl, type TemplateVersionItem, type TemplateVersionList } from "../../api/templates";
import { formatDateTime } from "../../format";
import { shortSha } from "../../templateEditing";

const props = defineProps<{ documentId: string; versions: TemplateVersionList; canManage: boolean; hasDraft: boolean; busy: boolean }>();
const emit = defineEmits<{ view: [item: TemplateVersionItem]; compare: [item: TemplateVersionItem]; restore: [item: TemplateVersionItem] }>();
const fileUrl = (item: TemplateVersionItem) => templateVersionFileUrl(props.documentId, item.versionId, item.hasEditedFile ? "edited" : "source");
const canCompare = (item: TemplateVersionItem) => props.versions.items.length > 1 || item.status !== "published" || props.hasDraft;
</script>

<template>
  <div class="versions-panel" data-testid="tpl-versions-panel">
    <div class="original" data-testid="tpl-original">
      <div class="row-main">
        <strong>原始文件</strong>
        <span class="hint">导入时的 Word，不会被修改</span>
      </div>
      <a class="text sm" :href="`/api/v1/files/${versions.original.fileId}/content`" :download="versions.original.name" :title="`sha256 ${shortSha(versions.original.sha256)}`" data-testid="tpl-original-download">下载</a>
    </div>
    <ol class="list">
      <li v-for="item in versions.items" :key="item.versionId" class="version" :data-seq="item.seq" data-testid="tpl-version-item">
        <div class="row-main">
          <span class="line1"><strong>第 {{ item.seq }} 版</strong><span v-if="item.status === 'published'" class="tag success">当前</span> · {{ formatDateTime(item.publishedAt) }}<template v-if="item.publishedBy"> · {{ item.publishedBy.name }}</template></span>
          <span class="note">{{ item.note }}</span>
          <span v-if="item.restoredFrom && item.note !== `恢复自第 ${item.restoredFrom.seq} 版`" class="hint">恢复自第 {{ item.restoredFrom.seq }} 版</span>
        </div>
        <div class="ops">
          <button type="button" class="text sm" :disabled="busy" @click="emit('view', item)">查看</button>
          <button type="button" class="text sm" :disabled="busy || !canCompare(item)" :title="canCompare(item) ? '' : '只有一个版本且没有草稿，没有可对比的内容。'" data-testid="tpl-version-compare" @click="emit('compare', item)">对比</button>
          <button v-if="canManage && item.status !== 'published'" type="button" class="text sm" :disabled="busy" data-testid="tpl-version-restore" @click="emit('restore', item)">恢复为草稿</button>
          <a class="text sm" :href="fileUrl(item)" :title="item.hasEditedFile ? '发布时的 Word' : '与原始文件相同'">下载</a>
        </div>
      </li>
    </ol>
    <p class="hint foot">恢复后的 Word 以当前版本的文件为底重新生成，被恢复版本的 Word 只用于对比。</p>
  </div>
</template>

<style scoped>
.versions-panel { display:grid; align-content:start; }
.original, .version { display:flex; gap:var(--space-3); padding:var(--space-3) var(--space-4); border-bottom:1px solid var(--border-subtle); }
.original { background:var(--bg-subtle); align-items:center; }
.row-main { display:grid; flex:1; min-width:0; gap:2px; font-size:var(--text-sm); }
.line1 { display:flex; align-items:center; gap:var(--space-1); color:var(--text-secondary); white-space:nowrap; }
.line1 strong { color:var(--text-primary); }
.note { color:var(--text-primary); overflow-wrap:anywhere; }
.list { margin:0; padding:0; list-style:none; }
.version { flex-direction:column; gap:var(--space-2); }
.ops { display:flex; gap:var(--space-3); }
.foot { margin:0; padding:var(--space-3) var(--space-4); }
</style>
