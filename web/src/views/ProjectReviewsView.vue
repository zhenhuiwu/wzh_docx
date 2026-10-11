<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ApiError } from "../api/client";
import { getCurrentSession } from "../api/identity";
import { getProject, type ProjectRecord } from "../api/projects";
import { decideReview, listReviews, type ReviewRecord } from "../api/reviews";
import ProjectFrame from "../components/ProjectFrame.vue";

const route = useRoute();
const router = useRouter();
const projectId = computed(() => String(route.params.id));
const project = ref<ProjectRecord | null>(null);
const items = ref<ReviewRecord[]>([]);
const total = ref(0);
const page = ref(1);
const me = ref("");
const draftName = ref("");
const draftState = ref("");
const appliedName = ref("");
const appliedState = ref("");
const selectedId = ref("");
const reason = ref("");
const phase = ref<"loading" | "ready" | "error">("loading");
const errorText = ref("");
const notice = ref("");
const submitting = ref(false);
const tab = ref<"reviews" | "deliveries">("reviews");
const selected = computed(() => items.value.find((item) => item.id === selectedId.value) ?? null);
type BlockRevisionRecord = { id: string; reviewState: string; content: { type: string; text?: string }[] };
const submitted = ref<BlockRevisionRecord | null>(null);
const compare = ref<BlockRevisionRecord | null>(null);
const contentPhase = ref<"idle" | "loading" | "ready" | "error">("idle");
const contentError = ref("");
const decisionReady = computed(
  () => contentPhase.value === "ready" && submitted.value?.id === selected.value?.blockRevisionId,
);
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / 10)));
const filtering = computed(() => Boolean(appliedName.value || appliedState.value));

function messageOf(error: unknown) {
  return error instanceof ApiError ? error.message : "暂时不能连接服务。";
}

function stateLabel(state: string) {
  if (state === "pending") return "待审核";
  if (state === "approved") return "已通过";
  if (state === "rejected") return "已退回";
  return "未知";
}

async function loadContent(review: ReviewRecord) {
  submitted.value = null;
  compare.value = null;
  contentPhase.value = "error";
  contentError.value = `暂时无法读取「${review.documentName}」的提交正文`;
}

async function load() {
  phase.value = "loading";
  errorText.value = "";
  try {
    const session = await getCurrentSession();
    me.value = session.user.id;
    project.value = await getProject(projectId.value);
    const data = await listReviews(projectId.value, { page: page.value, name: appliedName.value, state: appliedState.value });
    items.value = data.items;
    total.value = data.total;
    page.value = data.page;
    const wanted = String(route.query.review || "");
    if (wanted && items.value.some((item) => item.id === wanted)) selectedId.value = wanted;
    else if (!items.value.some((item) => item.id === selectedId.value)) {
      selectedId.value = items.value[0]?.id ?? "";
      if (wanted) notice.value = "当前页没有这条审核。";
    }
    phase.value = "ready";
  } catch (error) {
    phase.value = "error";
    errorText.value = messageOf(error);
  }
}

function reviewQuery(nextPage: number, nextName: string, nextState: string) {
  const query: Record<string, string> = {};
  if (nextName) query.name = nextName;
  if (nextState) query.state = nextState;
  if (nextPage > 1) query.page = String(nextPage);
  if (route.query.review) query.review = String(route.query.review);
  return query;
}

function pushReviewQuery(nextPage: number, nextName: string, nextState: string) {
  const query = reviewQuery(nextPage, nextName, nextState);
  const same = String(route.query.name || "") === (query.name || "")
    && String(route.query.state || "") === (query.state || "")
    && String(route.query.page || "") === (query.page || "");
  if (same) void load();
  else void router.replace({ query });
}

function search() {
  pushReviewQuery(1, draftName.value.trim(), draftState.value);
}

function resetQuery() {
  draftName.value = "";
  draftState.value = "";
  pushReviewQuery(1, "", "");
}

async function decide(decision: "approve" | "reject") {
  if (!selected.value) return;
  if (decision === "reject" && !reason.value.trim()) {
    notice.value = "退回需要填写理由";
    return;
  }
  submitting.value = true;
  notice.value = "";
  try {
    await decideReview(selected.value.id, selected.value.revision, decision, reason.value.trim());
    reason.value = "";
    notice.value = decision === "approve" ? "已通过这一份修订。更新的工作稿不会自动批准。" : "已退回这一份修订。";
    await load();
  } catch (error) {
    notice.value = messageOf(error);
  } finally {
    submitting.value = false;
  }
}

watch(selectedId, () => {
  reason.value = "";
  if (selected.value) void loadContent(selected.value);
  else {
    submitted.value = null;
    contentPhase.value = "idle";
  }
  if (selectedId.value && String(route.query.review || "") !== selectedId.value) {
    void router.replace({ query: { ...route.query, review: selectedId.value } });
  }
});
watch(() => String(route.query.review || ""), (id) => {
  if (id && items.value.some((item) => item.id === id) && selectedId.value !== id) selectedId.value = id;
});
watch(
  () => `${projectId.value}|${String(route.query.name || "")}|${String(route.query.state || "")}|${String(route.query.page || "")}`,
  () => {
    draftName.value = String(route.query.name || "");
    appliedName.value = draftName.value;
    draftState.value = String(route.query.state || "");
    appliedState.value = draftState.value;
    page.value = Math.max(1, Number(route.query.page || 1) || 1);
    void load();
  },
  { immediate: true },
);
</script>

<template>
  <ProjectFrame @archive-changed="load()">
  <section class="card">
    <div class="head">
      <h1>审核</h1>
    </div>
    <div class="tabs">
      <button type="button" :class="{ on: tab === 'reviews' }" @click="tab = 'reviews'">待审核</button>
      <button type="button" :class="{ on: tab === 'deliveries' }" @click="tab = 'deliveries'">交付</button>
    </div>
    <p v-if="phase === 'error'" class="notice" role="alert">{{ errorText }}</p>
    <p v-else-if="phase === 'loading'" class="empty">正在读取</p>
    <div v-else-if="tab === 'deliveries'">
      <p class="empty">交付暂未开放。</p>
    </div>
    <template v-else>
    <form class="query" @submit.prevent="search">
          <div class="query-fields">
            <label><span>名称</span><input v-model="draftName" class="control" type="search" /></label>
            <label>
              <span>状态</span>
              <select v-model="draftState" class="control">
                <option value="">全部</option>
                <option value="pending">待审核</option>
                <option value="approved">已通过</option>
                <option value="rejected">已退回</option>
              </select>
            </label>
          </div>
          <div class="query-actions">
            <button type="submit" class="primary">查询</button>
            <button type="button" class="ghost" @click="resetQuery">重置</button>
          </div>
        </form>
    <div class="split">
      <div>
        <p v-if="items.length === 0" class="empty">{{ filtering ? "没有符合条件的审核" : "还没有待审核的修订" }}</p>
        <div v-else class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>文档</th>
                <th>内容项</th>
                <th class="from-mid">提交人</th>
                <th>状态</th>
                <th class="from-wide">时间</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in items" :key="item.id" :class="{ current: item.id === selectedId }">
                <td><router-link class="link" :to="{ query: { ...route.query, review: item.id } }">{{ item.documentName }}</router-link></td>
                <td>{{ item.blockTitle }}</td>
                <td class="from-mid">{{ item.submitterName }}</td>
                <td>{{ stateLabel(item.state) }}</td>
                <td class="from-wide">{{ item.updatedAt }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="pager">
          <span class="total">共 {{ total }} 条</span>
          <button type="button" class="page" :disabled="page <= 1" @click="pushReviewQuery(page - 1, appliedName, appliedState)">上一页</button>
          <button type="button" class="page" :disabled="page >= pageCount" @click="pushReviewQuery(page + 1, appliedName, appliedState)">下一页</button>
        </div>
      </div>
      <aside class="detail">
        <p class="source-title">本次提交</p>
        <template v-if="selected">
          <p>{{ selected.documentName }} · {{ selected.blockTitle }}</p>
          <p>提交人 {{ selected.submitterName }}，审核人 {{ selected.reviewerName }}。状态：{{ stateLabel(selected.state) }}。</p>
          <p v-if="contentPhase === 'loading'" class="empty">正在读取提交的正文</p>
          <p v-else-if="contentPhase === 'error'" class="notice" role="alert">{{ contentError }}。正文没有读到，不能做决定。</p>
          <div v-else-if="submitted" class="reading">
            <div class="diff">
              <div>
                <p v-for="(part, index) in submitted.content" :key="`sub-${index}`" class="part">
                  {{ part.type === "paragraph" ? (part.text || "（空段）") : part.type === "image" ? "图片，当前只读" : part.type === "table" ? "表格，当前只读" : "只读内容" }}
                </p>
                <p v-if="submitted.content.length === 0" class="part">（空）</p>
              </div>
              <div>
                <p class="source-title">对照版本</p>
                <p v-if="!compare" class="part">没有对照版本</p>
                <p v-for="(part, index) in compare?.content || []" :key="`cmp-${index}`" class="part">
                  {{ part.type === "paragraph" ? (part.text || "（空段）") : part.type === "image" ? "图片，当前只读" : part.type === "table" ? "表格，当前只读" : "只读内容" }}
                </p>
              </div>
            </div>
            <p class="part">这次提交没有附带资料依据。</p>
          </div>
          <p v-if="selected.submitterId === me">不能审核自己提交的修订。</p>
          <form v-else-if="selected.state === 'pending' && selected.reviewerId === me" class="decide" @submit.prevent="decide('approve')">
            <label>退回理由<input v-model="reason" class="control" type="text" :disabled="submitting || !decisionReady" /></label>
            <div class="actions">
              <button type="submit" class="primary" :disabled="submitting || !decisionReady">通过本次提交</button>
              <button type="button" class="ghost" :disabled="submitting || !decisionReady" @click="decide('reject')">退回</button>
            </div>
            <p v-if="!decisionReady" class="field-error">要先读到这次提交的正文，才能通过或退回。</p>
          </form>
          <p v-else-if="selected.state === 'pending'">这份审核指定了其他审核人。</p>
        </template>
        <p v-else>选择一条审核，阅读提交的正文后再决定。</p>
        <p v-if="notice" class="notice" role="status">{{ notice }}</p>
      </aside>
    </div>
    </template>
  </section>
  </ProjectFrame>
</template>

<style scoped>
.tabs { display: flex; gap: var(--space-2); padding: var(--space-3) var(--space-4) 0; }
.tabs button {
  height: var(--space-6);
  padding: 0 var(--space-3);
  border: 1px solid var(--rule-strong);
  border-radius: var(--radius-control);
  background: var(--sheet);
  color: var(--ink);
  cursor: pointer;
}
.tabs button.on { border-color: var(--stamp); background: var(--stamp-wash); color: var(--stamp); }
.query-fields label:last-child { flex: none; }
.query-fields label:last-child .control { width: calc(var(--space-7) * 4); }
.detail { padding: var(--space-3) var(--space-4); color: var(--ink-secondary); font-size: var(--text-sm); }
.source-title { margin: 0 0 var(--space-2); color: var(--ink); font-weight: 600; }
.decide { display: grid; gap: var(--space-2); margin-top: var(--space-3); }
.narrow-note { display: block; }
.desktop { display: none; }
.table-wrap :deep(table) { width: 100%; }
.table-wrap :deep(tr.current) { background: var(--stamp-wash); }
.from-wide { display: none; }
@media (max-width: 767px) {
  .from-mid { display: none; }
}
@media (min-width: 768px) {
  .from-mid { display: table-cell; }
  .desktop { display: flex; }
  .narrow-note { display: none; }
}
.split { display: grid; grid-template-columns: 320px minmax(0, 1fr); align-items: start; }
.split .from-mid, .split .from-wide { display: none; }
.detail { border-left: 1px solid var(--rule); min-height: 480px; }
.from-wide { display: table-cell; }
.diff { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-3); }
.part { margin: 0 0 var(--space-2); white-space: pre-wrap; }
</style>
