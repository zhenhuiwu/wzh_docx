<script setup lang="ts">
/** A07 项目概述 › “编写分工”（16 §4.10，K-1，H-13）：全体成员可看；“去分配”只给负责人和管理员。 */
import { computed, onMounted, ref } from "vue";
import { getAssignmentSummary, type AssignmentSummary } from "../api/assignments";

const props = defineProps<{ projectId: string; canAssign: boolean }>();
const data = ref<AssignmentSummary | null>(null);
const error = ref("");
const view = ref<"document" | "person">("document");
const focusUser = ref<string | null>(null);

async function load() {
  error.value = "";
  try { data.value = await getAssignmentSummary(props.projectId); } catch (caught) { error.value = caught instanceof Error ? caught.message : "读取失败。"; }
}
/** 成员表点了“编写分工”：切到按人并定位。 */
async function showPerson(userId: string) {
  view.value = "person";
  focusUser.value = userId;
  await load();
  window.document.querySelector(`[data-testid="overview-assign-person-row"][data-user-id="${CSS.escape(userId)}"]`)?.scrollIntoView({ block: "center" });
}
const docs = computed(() => (data.value?.byDocument ?? []) as Array<any>);
const people = computed(() => (data.value?.byPerson ?? []) as Array<any>);
onMounted(load);
defineExpose({ load, showPerson });
</script>

<template>
  <section class="overview-section assign-section" data-testid="overview-assign-section" aria-labelledby="assign-title">
    <div class="section-head">
      <h2 id="assign-title" class="section-title">编写分工</h2>
      <div class="seg" role="group" aria-label="编写分工视图">
        <button type="button" :class="{ on: view === 'document' }" :aria-pressed="view === 'document'" @click="view = 'document'">按文档</button>
        <button type="button" :class="{ on: view === 'person' }" :aria-pressed="view === 'person'" data-testid="overview-assign-by-person" @click="view = 'person'">按人</button>
      </div>
    </div>
    <p class="assign-note">按章节统计，不代表编写完成度。</p>
    <p v-if="error" class="assign-error" role="alert">{{ error }}</p>
    <ul v-else-if="view === 'document'" class="assign-rows">
      <li v-for="doc in docs" :key="doc.documentId" data-testid="overview-assign-doc-row" :data-document-id="doc.documentId">
        <span class="name">{{ doc.name }}</span>
        <span v-if="doc.hasDocumentAssignment" class="tag neutral">整篇：{{ doc.documentOwners.map((p: any) => p.name).join('、') }}</span>
        <span v-if="doc.assignmentMode === 'open'" class="tag plain">不限制分工</span>
        <span class="num">{{ doc.sectionsAssigned }} / {{ doc.sectionsTotal }} 章</span>
        <router-link v-if="doc.unassignedSections" class="text" data-testid="overview-assign-unassigned" :to="{ name: 'document', params: { id: projectId, documentId: doc.documentId }, query: { assign: '1', filter: 'unassigned' } }">未分配 {{ doc.unassignedSections }} 章</router-link>
        <router-link v-if="data?.canAssign ?? canAssign" class="text" data-testid="overview-assign-goto" :to="{ name: 'document', params: { id: projectId, documentId: doc.documentId }, query: { assign: '1' } }">去分配</router-link>
      </li>
      <li v-if="!docs.length" class="empty">还没有文档。</li>
    </ul>
    <template v-else>
      <ul class="assign-rows">
        <li v-for="person in people" :key="person.userId" data-testid="overview-assign-person-row" :data-user-id="person.userId" :class="{ focus: focusUser === person.userId }">
          <span class="name">{{ person.name }}<span v-if="!person.userActive" class="tag warning">已停用</span></span>
          <span class="num">{{ [person.sections ? `${person.sections} 章` : "", person.blocks ? `${person.blocks} 段` : "", `${person.documents} 篇`].filter(Boolean).join(" · ") }}</span>
          <span class="items"><template v-for="(item, index) in person.items" :key="item.documentId || index"><template v-if="index">；</template><router-link v-if="item.documentId" class="text" data-testid="overview-assign-person-link" :to="{ name: 'document', params: { id: projectId, documentId: item.documentId }, query: { assign: '1', person: person.userId } }">{{ item.name }} · {{ item.whole ? '整篇' : `${item.sections} 章` }}</router-link><template v-else>{{ item.name }} · {{ item.whole ? '整篇' : `${item.sections} 章` }}</template></template></span>
        </li>
        <li v-if="!people.length" class="empty">还没有分工。</li>
      </ul>
      <p v-if="data?.unassignedEditors.length" class="assign-note" data-testid="overview-assign-no-work">还没有分工：{{ data.unassignedEditors.map(person => person.name).join('、') }}</p>
    </template>
  </section>
</template>

<style scoped>
.assign-note { margin: 0 0 8px; font-size: 12px; color: var(--text-tertiary); }
.assign-error { color: var(--danger); font-size: 13px; }
.assign-rows { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
.assign-rows li { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; padding: 8px 0; border-bottom: 1px solid var(--border-subtle, #eee); font-size: 13px; }
.assign-rows li.focus { background: var(--bg-subtle, #f5f7fa); }
.assign-rows .name { font-weight: 500; min-width: 160px; }
.assign-rows .items { color: var(--text-secondary); flex-basis: 100%; font-size: 12px; }
.empty { color: var(--text-tertiary); }
.seg { display:inline-flex; border:1px solid var(--border, #d0d5dd); border-radius:6px; overflow:hidden; }
.seg button { border:0; background:transparent; padding:4px 12px; font-size:13px; cursor:pointer; color:var(--text-secondary); }
.seg button.on { background:var(--primary-subtle, #eef4ff); color:var(--primary, #1d4ed8); font-weight:500; }
.section-head { display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:8px; }
</style>
