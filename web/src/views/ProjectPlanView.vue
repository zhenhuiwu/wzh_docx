<script setup lang="ts">
/** 分工页（30 §3.4）：读出开发计划书“任务分解”“公共框架分工”表格，只读。写回 A07 分工待 A07 合入。 */
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { ApiError } from "../api/client";
import { getPlanTasks, type PlanTasks } from "../api/docChain";
import ProjectFrame from "../components/ProjectFrame.vue";
import StatePanel from "../components/StatePanel.vue";

const route = useRoute();
const projectId = computed(() => String(route.params.id));
const data = ref<PlanTasks | null>(null);
const phase = ref<"loading" | "ready" | "error">("loading");
const errorText = ref("");
const selected = ref(0);
const task = computed(() => data.value?.tasks[selected.value] ?? null);

async function load() {
  phase.value = "loading";
  try { data.value = await getPlanTasks(projectId.value); selected.value = 0; phase.value = "ready"; }
  catch (error) { phase.value = "error"; errorText.value = error instanceof ApiError ? error.message : "暂时不能连接服务。"; }
}
onMounted(load);
</script>

<template>
  <ProjectFrame>
    <div class="plan-body">
      <div class="plan-head"><h2 class="section-title">分工</h2>
        <router-link v-if="data?.planDocument" class="ghost" :to="{ name: 'document', params: { id: projectId, documentId: data.planDocument.id } }">在开发计划书中编辑任务分解</router-link></div>
      <StatePanel v-if="phase === 'loading'" kind="loading" text="正在读取任务分解…" />
      <StatePanel v-else-if="phase === 'error'" kind="error" title="任务分解加载失败。" :text="errorText"><button type="button" class="ghost" @click="load">重试</button></StatePanel>
      <StatePanel v-else-if="data && !data.planDocument" title="还没有开发计划书" text="任务分解写在开发计划书里，请先创建开发计划书。" data-testid="plan-empty"><router-link class="primary" :to="{ name: 'project', params: { id: projectId } }">去创建</router-link></StatePanel>
      <div v-else-if="data" class="plan-grid">
        <div class="table-wrap"><table data-testid="task-table">
          <thead><tr><th>任务</th><th>负责人</th><th>涉及章节</th></tr></thead>
          <tbody>
            <tr v-if="!data.tasks.length"><td colspan="3" class="muted">开发计划书的“任务分解”表格里还没有任务。</td></tr>
            <tr v-for="(item, i) in data.tasks" :key="i" :class="{ sel: i === selected, mine: item.assignee.isMe }" tabindex="0" :aria-selected="i === selected" @click="selected = i" @keydown.enter="selected = i">
              <td>{{ item.title }}</td><td>{{ item.assignee.name || "未分配" }}</td>
              <td class="muted">{{ item.targets.map((t) => (t.documentName ?? "?") + (t.chapter ? " " + t.chapter.split(" ")[0] : "")).join("；") || "—" }}</td>
            </tr>
            <tr class="group"><td colspan="3">公共框架</td></tr>
            <tr v-for="fw in data.frameworks" :key="fw.key" :class="{ mine: fw.assignee.isMe }">
              <td>{{ fw.name }}</td><td>{{ fw.assignee.name || "未分配" }}</td><td class="muted">{{ fw.references.length }} 份文档引用</td>
            </tr>
          </tbody>
        </table></div>
        <aside class="detail" aria-label="任务详情">
          <template v-if="task">
            <h3>{{ task.title }}</h3>
            <p class="muted">负责人：{{ task.assignee.name || "未分配" }}<span v-if="task.assignee.name && !task.assignee.userId" class="tag warning">不是项目成员</span></p>
            <p class="muted">涉及块（跨 {{ task.documentCount }} 份文档）：</p>
            <ul class="targets"><li v-for="(t, i) in task.targets" :key="i">
              <router-link v-if="t.documentId" :to="{ name: 'document', params: { id: projectId, documentId: t.documentId } }">{{ t.documentName }}</router-link><span v-else>未找到文档</span>
              <span v-if="t.chapter"> › {{ t.chapter }}</span><span v-if="!t.found" class="tag warning">未找到章节</span>
            </li></ul>
            <p class="note">分配后，负责人只能修改这些块，其他部分只读。写回文档分工将在章节分工功能合入后开放。</p>
          </template>
          <p v-else class="muted">选择左侧任务查看涉及的章节。</p>
        </aside>
      </div>
    </div>
  </ProjectFrame>
</template>

<style scoped>
.plan-body { display:grid; gap:var(--space-5); padding:var(--space-5) var(--space-6) var(--space-6); }
.plan-head { display:flex; justify-content:space-between; align-items:center; }
.plan-grid { display:grid; grid-template-columns:3fr 2fr; gap:var(--space-5); align-items:start; }
tr.sel td { background:var(--stamp-wash); }
tr.mine td:first-child { font-weight:var(--weight-medium); color:var(--stamp); }
tr[tabindex]:focus-visible { outline:none; box-shadow:var(--focus-shadow); }
tr.group td { color:var(--text-tertiary); font-size:var(--text-xs); background:var(--bg-subtle); }
.detail { padding:var(--space-5); border:1px solid var(--border-default); border-radius:var(--radius-sheet); background:var(--bg-surface); }
.detail h3 { margin:0 0 var(--space-3); font-size:var(--text-lg); }
.muted { color:var(--text-secondary); font-size:var(--text-sm); }
.targets { display:grid; gap:var(--space-2); margin:0 0 var(--space-4); padding-left:var(--space-5); }
.tag { margin-left:var(--space-2); }
.note { margin:0; color:var(--text-tertiary); font-size:var(--text-xs); }
</style>
