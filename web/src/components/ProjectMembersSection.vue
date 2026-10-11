<script setup lang="ts">
/**
 * A05 项目概述 ›“成员与权限”（16 §2.2–§2.8）。自己负责加载、空、错三态，失败不影响上下两个区块。
 * 前端只做展示判断，后端独立校验（membersRevision、最后负责人、归档、停用账号）。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ApiError } from "../api/client";
import {
  addMembers, listMemberCandidates, listMembers, removeMember, setMember,
  type MemberCandidate, type MemberChangeResult, type MembersSnapshot, type ProjectMember, type ProjectOwnerSummary,
} from "../api/projects";
import { flash } from "../flash";
import { formatDateTime, initialOf } from "../format";
import {
  ARCHIVED_MEMBERS_CONTACT_TEXT, ARCHIVED_MEMBERS_TEXT, DISABLED_ROW_TEXT, LAST_OWNER_TEXT, MAX_PICK, MEMBERS_INFO, ROLE_OPTIONS, ROLE_TONE,
  addButtonText, batchResultText, candidateNote, changeOutcomeText, classifyMemberError, contactNames, isLockedOwner,
  activeOwners, memberErrorText, membersConflictText, membersNote, noActiveOwnerText, pickError, retryableIds, recentChange, removeConfirmText, roleLabel, roleOptionDisabled,
  type AssignableRole,
} from "../memberModel";
import { hasPermission, readSession } from "../session";
import StatePanel from "./StatePanel.vue";

/** canRestore：当前账号能恢复归档项目（项目 permissions.canArchive），归档提示里给［恢复项目］（21 M-1）。 */
const props = defineProps<{ projectId: string; projectName: string; canRestore?: boolean }>();
const emit = defineEmits<{
  /** 负责人变化（加、降、移、主负责人改指）后刷新页头和概述信息格，不再请求 getProject。 */
  (event: "owners", value: { owners: ProjectOwnerSummary[]; ownerId: string | null; membersRevision: number }): void;
  /** 涉及自己的权限变化：父组件重新读取项目 permissions。 */
  (event: "permissions-changed"): void;
  /** 后端说项目不存在或已无权访问：整页转 404 态。 */
  (event: "gone"): void;
  /** 点了归档提示里的［恢复项目］：由页头统一弹恢复确认。 */
  (event: "restore"): void;
}>();

const route = useRoute();
const router = useRouter();
const phase = ref<"loading" | "ready" | "error">("loading");
const errorText = ref("");
const snapshot = ref<MembersSnapshot | null>(null);
const query = ref("");
const warning = ref("");
const info = ref("");
const rowState = ref<Record<string, { state: "saving" | "saved" | "error"; text: string }>>({});
const addButton = ref<HTMLButtonElement | null>(null);
let loadRequest = 0;
let alive = true;
const timers = new Map<string, number>();

const members = computed(() => snapshot.value?.items ?? []);
const archived = computed(() => snapshot.value?.readonlyReason === "archived");
const canManage = computed(() => Boolean(snapshot.value?.canManageMembers));
const activeOwnerCount = computed(() => snapshot.value?.activeOwnerCount ?? 0);
// 系统管理员（users.manage 只给系统管理员）；归档时 canManageMembers=false，说明行仍按身份写
const isAdmin = computed(() => hasPermission(readSession(), "users.manage"));
const note = computed(() => membersNote({
  canManage: canManage.value,
  projectRole: snapshot.value?.projectRole,
  isAdmin: isAdmin.value,
  owners: snapshot.value?.owners,
  archived: archived.value,
}));
// 读取时没有启用的负责人（19 S-1）：成员区给出 warning，管理员据此补负责人
const noActiveOwner = computed(() => Boolean(snapshot.value) && !activeOwners(snapshot.value?.owners).length);
const filtered = computed(() => {
  const q = query.value.trim().toLowerCase();
  if (!q) return members.value;
  return members.value.filter(member => member.name.toLowerCase().includes(q) || member.account.toLowerCase().includes(q));
});
const busy = computed(() => Object.values(rowState.value).some(item => item.state === "saving"));
let infoTimer = 0;

/** 行内结果提示。主负责人改指这类旁人的变化 4 秒后自动收起（16 §2.6/§2.8，21 S-5）；自身降级等提示常驻。 */
function showInfo(text: string, transient = false) {
  window.clearTimeout(infoTimer);
  info.value = text;
  if (transient && text) infoTimer = window.setTimeout(() => { if (info.value === text) info.value = ""; }, 4000);
}

function errorOf(error: unknown): { status: number; code: string; message: string; details?: Record<string, any> } {
  if (error instanceof ApiError) return { status: error.status, code: error.code, message: error.message, details: error.details };
  return { status: 0, code: "", message: "" };
}

function apply(data: MembersSnapshot) {
  snapshot.value = data;
  emit("owners", { owners: data.owners, ownerId: data.ownerId, membersRevision: data.membersRevision });
}

async function load(quiet = false) {
  const request = ++loadRequest;
  if (!quiet) { phase.value = "loading"; errorText.value = ""; }
  try {
    const data = await listMembers(props.projectId);
    if (!alive || request !== loadRequest) return;
    apply(data);
    phase.value = "ready";
  } catch (error) {
    if (!alive || request !== loadRequest) return;
    if (error instanceof ApiError && error.status === 404) { emit("gone"); return; }
    if (quiet) return;
    phase.value = "error";
    errorText.value = error instanceof ApiError ? error.message : "暂时不能连接服务。";
  }
}

function setRow(userId: string, state: "saving" | "saved" | "error", text: string) {
  rowState.value = { ...rowState.value, [userId]: { state, text } };
  window.clearTimeout(timers.get(userId));
  if (state === "saved") timers.set(userId, window.setTimeout(() => clearRow(userId), 1500));
}

function clearRow(userId: string) {
  const next = { ...rowState.value };
  delete next[userId];
  rowState.value = next;
}

/** 写操作失败的统一反应（16 §2.5）：并发 → 重新读取 + warning；归档 → 区块只读；404 → 整页 404。 */
async function handleWriteError(error: unknown, member: { userId: string; name: string }, adding = false): Promise<string> {
  const failure = errorOf(error);
  const kind = classifyMemberError(failure);
  const text = memberErrorText(failure, member.name, adding);
  if (kind === "conflict") {
    clearRow(member.userId);
    await load(true);
    warning.value = membersConflictText(failure.details);
    return text;
  }
  if (kind === "archived") {
    clearRow(member.userId);
    await load(true);
    warning.value = ARCHIVED_MEMBERS_TEXT;
    emit("permissions-changed");
    return text;
  }
  if (kind === "member-gone") {
    clearRow(member.userId);
    await load(true);
    warning.value = text;
    return text;
  }
  if (kind === "gone") { emit("gone"); return text; }
  if (kind === "disabled" || kind === "forbidden") void load(true);
  if (kind === "forbidden") emit("permissions-changed");
  setRow(member.userId, "error", text);
  return text;
}

// ---------- 改权限 ----------
const selfDialog = ref<HTMLDialogElement | null>(null);
const selfPending = ref<{ member: ProjectMember; role: AssignableRole; select: HTMLSelectElement } | null>(null);

function onRoleChange(member: ProjectMember, event: Event) {
  const select = event.target as HTMLSelectElement;
  const role = select.value as AssignableRole;
  if (role === member.role) return;
  if (member.isMe && role !== "owner") {
    selfPending.value = { member, role, select };
    void nextTick(() => selfDialog.value?.showModal());
    return;
  }
  void changeRole(member, role, select);
}

function cancelSelf() {
  const pending = selfPending.value;
  if (pending) pending.select.value = pending.member.role;
  selfPending.value = null;
  selfDialog.value?.close();
}

function confirmSelf() {
  const pending = selfPending.value;
  selfPending.value = null;
  selfDialog.value?.close();
  if (pending) void changeRole(pending.member, pending.role, pending.select);
}

async function changeRole(member: ProjectMember, role: AssignableRole, select: HTMLSelectElement) {
  if (!snapshot.value) return;
  warning.value = "";
  info.value = "";
  setRow(member.userId, "saving", "正在保存…");
  try {
    const result = await setMember(props.projectId, member.userId, role, snapshot.value.membersRevision);
    if (!alive) return;
    apply(result);
    setRow(member.userId, "saved", "已保存");
    const outcome = changeOutcomeText("role", member.name, role, result);
    if (outcome) showInfo(outcome, !result.assignmentsCleared?.sections && !result.assignmentsCleared?.blocks);
    if (member.isMe) {
      showInfo(`你的项目权限已调整为${roleLabel(role)}。`);
      emit("permissions-changed");
    }
  } catch (error) {
    if (!alive) return;
    select.value = member.role;
    await handleWriteError(error, member);
    const current = members.value.find(item => item.userId === member.userId);
    if (current) select.value = current.role;
  }
}

// ---------- 移除 ----------
const removeDialog = ref<HTMLDialogElement | null>(null);
const removing = ref<ProjectMember | null>(null);
const removeBusy = ref(false);
const removeError = ref("");
const removeText = computed(() => removing.value ? removeConfirmText(removing.value) : { body: "", warning: "" });

function askRemove(member: ProjectMember) {
  removing.value = member;
  removeError.value = "";
  void nextTick(() => removeDialog.value?.showModal());
}

function closeRemove() {
  if (removeBusy.value) return;
  removing.value = null;
  removeDialog.value?.close();
}

async function confirmRemove() {
  const member = removing.value;
  if (!member || !snapshot.value || removeBusy.value) return;
  removeBusy.value = true;
  removeError.value = "";
  warning.value = "";
  info.value = "";
  try {
    const result: MemberChangeResult | null = await removeMember(props.projectId, member.userId, snapshot.value.membersRevision);
    if (!alive) return;
    removeBusy.value = false;
    closeRemove();
    if (result?.removedSelf && !canManageAfterSelfRemove(result)) {
      flash(`你已退出 ${props.projectName}。`);
      void router.push({ name: "projects" });
      return;
    }
    if (result && "items" in result && result.items) apply(result);
    else await load(true);
    const outcome = result ? changeOutcomeText("remove", member.name, null, result) : "";
    if (outcome) showInfo(outcome, !result?.assignmentsCleared?.sections && !result?.assignmentsCleared?.blocks);
    if (member.isMe) emit("permissions-changed");
  } catch (error) {
    if (!alive) return;
    removeBusy.value = false;
    const kind = classifyMemberError(errorOf(error));
    if (kind === "conflict" || kind === "archived" || kind === "gone") {
      closeRemove();
      await handleWriteError(error, member);
      return;
    }
    removeError.value = memberErrorText(errorOf(error), member.name);
  }
}

/** 管理员把自己从成员里移除后仍能管理（不跳走）。 */
function canManageAfterSelfRemove(result: MemberChangeResult): boolean {
  return Boolean(result.canManageMembers);
}

// ---------- 添加 ----------
const addDialog = ref<HTMLDialogElement | null>(null);
const searchInput = ref<HTMLInputElement | null>(null);
const adding = ref(false);
const addOpen = ref(false);
const candidateQuery = ref("");
const candidates = ref<MemberCandidate[]>([]);
const candidatePhase = ref<"loading" | "ready" | "error">("loading");
const candidateError = ref("");
const picked = ref<MemberCandidate[]>([]);
const addRole = ref<AssignableRole>("editor");
const addError = ref("");
const addResult = ref("");
const retryMode = ref(false);
let searchTimer = 0;
let searchRequest = 0;
let searchAbort: AbortController | null = null;

async function searchCandidates() {
  const request = ++searchRequest;
  searchAbort?.abort();
  searchAbort = new AbortController();
  candidatePhase.value = "loading";
  candidateError.value = "";
  try {
    const result = await listMemberCandidates(props.projectId, candidateQuery.value.trim(), searchAbort.signal);
    if (!alive || request !== searchRequest) return;
    candidates.value = result.items;
    candidatePhase.value = "ready";
  } catch (error) {
    if (!alive || request !== searchRequest) return;
    if (error instanceof DOMException && error.name === "AbortError") return;
    candidatePhase.value = "error";
    candidateError.value = error instanceof ApiError ? memberErrorText(errorOf(error), "") : "暂时不能连接服务。";
  }
}

watch(candidateQuery, () => {
  if (!addOpen.value) return;
  window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => void searchCandidates(), 300);
});

function openAdd() {
  picked.value = [];
  addRole.value = "editor";
  addError.value = "";
  addResult.value = "";
  retryMode.value = false;
  candidateQuery.value = "";
  candidates.value = [];
  addOpen.value = true;
  addDialog.value?.showModal();
  void nextTick(() => searchInput.value?.focus());
  void searchCandidates();
}

function closeAdd() {
  if (adding.value) return;
  addOpen.value = false;
  window.clearTimeout(searchTimer);
  searchAbort?.abort();
  addDialog.value?.close();
}

function isPicked(candidate: MemberCandidate) {
  return picked.value.some(item => item.id === candidate.id);
}

function togglePick(candidate: MemberCandidate) {
  if (candidate.isMember || adding.value) return;
  addError.value = "";
  if (isPicked(candidate)) { picked.value = picked.value.filter(item => item.id !== candidate.id); return; }
  if (picked.value.length >= MAX_PICK) { addError.value = pickError(MAX_PICK + 1); return; }
  picked.value = [...picked.value, candidate];
}

function unpick(candidate: MemberCandidate) {
  if (adding.value) return;
  picked.value = picked.value.filter(item => item.id !== candidate.id);
}

async function submitAdd() {
  if (adding.value || !snapshot.value) return;
  const problem = pickError(picked.value.length);
  if (problem) { addError.value = problem; return; }
  adding.value = true;
  addError.value = "";
  addResult.value = "";
  const names = Object.fromEntries(picked.value.map(item => [item.id, item.name]));
  try {
    const result = await addMembers(props.projectId, picked.value.map(item => item.id), addRole.value, snapshot.value.membersRevision);
    if (!alive) return;
    apply(result);
    adding.value = false;
    if (!result.failed.length) {
      closeAdd();
      showInfo(`已添加 ${result.added.length} 人。`, true);
      return;
    }
    // 部分失败：弹窗不关；已成功的和重试也不会成功的（账号停用、不存在、已是成员）从已选里去掉，
    // 只剩可重试的才把按钮变成“重试 M 人”（16 §2.3.1，21 S-7）
    const retry = retryableIds(result.failed);
    picked.value = picked.value.filter(item => retry.has(item.id));
    addResult.value = batchResultText(result.added.length, result.failed, names);
    retryMode.value = picked.value.length > 0;
    void searchCandidates();
  } catch (error) {
    if (!alive) return;
    adding.value = false;
    const kind = classifyMemberError(errorOf(error));
    if (kind === "conflict") {
      await load(true);
      addError.value = membersConflictText(errorOf(error).details);
      return;
    }
    if (kind === "archived" || kind === "gone") {
      closeAdd();
      await handleWriteError(error, { userId: "", name: "" });
      return;
    }
    addError.value = memberErrorText(errorOf(error), "", true);
  }
}

function roleHint(member: ProjectMember) {
  if (member.userStatus !== "active") return DISABLED_ROW_TEXT;
  if (isLockedOwner(member, activeOwnerCount.value)) return LAST_OWNER_TEXT;
  return "";
}

function scrollToSelf() {
  if (route.hash !== "#members") return;
  void nextTick(() => {
    const root = window.document.getElementById("members");
    root?.scrollIntoView({ block: "start" });
    (addButton.value ?? root)?.focus({ preventScroll: true });
  });
}

watch(() => props.projectId, async () => {
  snapshot.value = null;
  warning.value = "";
  info.value = "";
  rowState.value = {};
  await load();
  scrollToSelf();
}, { immediate: true });

// 被移出、降级、归档时停在概述页也要跟上（20 I-6）：45 秒轮询一次，切回窗口时立即刷新；404 整页转“不存在或无权访问”
const POLL_MS = 45000;
let pollTimer = 0;
function quietRefresh() {
  if (document.visibilityState !== "visible" || busy.value || adding.value || removeBusy.value || selfPending.value) return;
  void load(true);
}
function onVisible() { if (document.visibilityState === "visible") quietRefresh(); }
onMounted(() => {
  pollTimer = window.setInterval(quietRefresh, POLL_MS);
  window.addEventListener("focus", quietRefresh);
  document.addEventListener("visibilitychange", onVisible);
});

onBeforeUnmount(() => {
  alive = false;
  ++loadRequest;
  window.clearInterval(pollTimer);
  window.clearTimeout(infoTimer);
  window.removeEventListener("focus", quietRefresh);
  document.removeEventListener("visibilitychange", onVisible);
  window.clearTimeout(searchTimer);
  searchAbort?.abort();
  timers.forEach(timer => window.clearTimeout(timer));
});

defineExpose({ reload: () => load(true) });
</script>

<template>
  <section id="members" class="overview-section members-section" data-testid="members-section" aria-labelledby="members-title" tabindex="-1">
    <div class="section-head">
      <div class="members-head">
        <h2 id="members-title" class="section-title">成员与权限</h2>
        <span v-if="snapshot" class="count" data-testid="members-count">{{ members.length }}</span>
        <span v-if="snapshot" class="members-note" data-testid="members-note">{{ note }}</span>
      </div>
      <div class="members-tools">
        <label v-if="members.length > 10" class="search-box members-search">
          <span class="sr-only">查找成员</span>
          <svg class="ico" viewBox="0 0 16 16" aria-hidden="true"><path d="M7 12A5 5 0 1 0 7 2a5 5 0 0 0 0 10zM14 14l-3.5-3.5" /></svg>
          <input v-model="query" class="control" type="search" placeholder="查找成员" data-testid="members-search" />
        </label>
        <button v-if="canManage" ref="addButton" type="button" class="ghost" data-testid="members-add" :disabled="busy" @click="openAdd">
          <svg class="ico" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3v10M3 8h10" /></svg>添加成员
        </button>
      </div>
    </div>
    <div class="members-box">
      <StatePanel v-if="phase === 'loading'" kind="loading" text="正在读取成员…" class="compact" />
      <StatePanel v-else-if="phase === 'error'" kind="error" title="成员加载失败" :text="errorText" class="compact"><button type="button" class="ghost" @click="load()">重试</button></StatePanel>
      <StatePanel v-else-if="!members.length" title="还没有成员" text="添加成员后，他们就能在项目列表里看到该项目。" class="compact">
        <button v-if="canManage" type="button" class="ghost" @click="openAdd">添加成员</button>
      </StatePanel>
      <template v-else>
        <div v-if="archived" class="notice warning notice-row" data-testid="members-notice" role="status">
          <span>{{ canRestore ? ARCHIVED_MEMBERS_TEXT : ARCHIVED_MEMBERS_CONTACT_TEXT }}</span>
          <button v-if="canRestore" type="button" class="ghost sm" data-testid="members-restore" @click="emit('restore')">恢复项目</button>
        </div>
        <p v-else-if="warning" class="notice warning" data-testid="members-notice" role="status">{{ warning }}</p>
        <p v-if="noActiveOwner && !archived" class="notice warning" data-testid="members-no-owner" role="status">{{ noActiveOwnerText(canManage) }}</p>
        <p v-if="info" class="notice info" data-testid="members-info" role="status">{{ info }}</p>
        <div class="table-wrap members-table" :class="{ scroll: members.length > 20 }">
          <table>
            <thead>
              <tr><th class="col-member">成员</th><th class="col-dept">所属部门</th><th class="col-role">项目权限</th><th class="col-status">账号状态</th><th class="col-recent">最近调整</th><th v-if="canManage" class="col-ops th-right">操作</th></tr>
            </thead>
            <tbody>
              <tr v-if="!filtered.length"><td :colspan="canManage ? 6 : 5" class="empty-row">没有找到成员。<button type="button" class="text" @click="query = ''">清除查找</button></td></tr>
              <tr v-for="member in filtered" :key="member.userId" data-testid="member-row" :data-user-id="member.userId" :class="{ off: member.userStatus !== 'active' }">
                <td class="col-member">
                  <span class="person">
                    <span class="avatar" aria-hidden="true">{{ initialOf(member.name) }}</span>
                    <span class="who">
                      <span class="who-name">{{ member.name }}<span v-if="member.isMe" class="tag plain me" data-testid="member-me">我</span></span>
                      <span class="who-account">{{ member.account }}</span>
                    </span>
                  </span>
                </td>
                <td class="col-dept">{{ member.department || "—" }}</td>
                <td class="col-role">
                  <div class="role-cell">
                    <template v-if="canManage && member.role !== 'reviewer'">
                      <select
                        class="control sm role-select" data-testid="member-role-select" :value="member.role"
                        :aria-label="`${member.name} 的项目权限`" :title="roleHint(member) || undefined"
                        :disabled="member.userStatus !== 'active' || rowState[member.userId]?.state === 'saving'"
                        :aria-describedby="roleHint(member) ? `role-hint-${member.userId}` : undefined"
                        @change="onRoleChange(member, $event)"
                      >
                        <option v-for="option in ROLE_OPTIONS" :key="option.value" :value="option.value" :disabled="roleOptionDisabled(member, activeOwnerCount, option.value)">{{ option.label }}</option>
                      </select>
                      <span v-if="rowState[member.userId]" class="role-status" :class="rowState[member.userId].state" data-testid="member-role-status" :role="rowState[member.userId].state === 'error' ? 'alert' : 'status'">{{ rowState[member.userId].text }}</span>
                      <span v-else-if="isLockedOwner(member, activeOwnerCount)" :id="`role-hint-${member.userId}`" class="role-lock">
                        <svg class="ico" viewBox="0 0 16 16" aria-hidden="true"><path d="M5 7V5a3 3 0 0 1 6 0v2M4 7h8v6H4z" /></svg>唯一启用的负责人<span class="sr-only">。{{ LAST_OWNER_TEXT }}</span>
                      </span>
                      <span v-else-if="member.userStatus !== 'active'" :id="`role-hint-${member.userId}`" class="role-lock">{{ DISABLED_ROW_TEXT }}</span>
                    </template>
                    <template v-else>
                      <span class="tag" :class="ROLE_TONE[member.role]" data-testid="member-role-tag">{{ roleLabel(member.role) }}</span>
                      <span v-if="rowState[member.userId]" class="role-status" :class="rowState[member.userId].state" data-testid="member-role-status" role="alert">{{ rowState[member.userId].text }}</span>
                    </template>
                  </div>
                </td>
                <td class="col-status">
                  <span v-if="member.userStatus === 'active'" class="tag success">启用</span>
                  <span v-else class="tag warning" data-testid="member-disabled">已停用</span>
                </td>
                <td class="col-recent">
                  <span class="recent-time num">{{ recentChange(member, formatDateTime).time }}</span>
                  <span v-if="recentChange(member, formatDateTime).who" class="recent-who">{{ recentChange(member, formatDateTime).who }}</span>
                </td>
                <td v-if="canManage" class="col-ops cell-right">
                  <button
                    type="button" class="text danger" data-testid="member-remove"
                    :disabled="isLockedOwner(member, activeOwnerCount) || rowState[member.userId]?.state === 'saving'"
                    :title="isLockedOwner(member, activeOwnerCount) ? LAST_OWNER_TEXT : undefined"
                    :aria-label="isLockedOwner(member, activeOwnerCount) ? `移除 ${member.name}：${LAST_OWNER_TEXT}` : `移除 ${member.name}`"
                    @click="askRemove(member)"
                  >移除</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
    </div>
    <p v-if="phase === 'ready' && members.length" class="notice info notice-row members-info-line" data-testid="members-info-line">
      <svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5" /><path d="M12 11v5M12 8v.01" /></svg><span>{{ MEMBERS_INFO }}</span>
    </p>

    <dialog ref="addDialog" class="app-dialog member-add" data-testid="member-add-dialog" aria-labelledby="member-add-title" @cancel.prevent="closeAdd">
      <form v-if="addOpen" novalidate @submit.prevent="submitAdd">
        <div class="dialog-head"><h2 id="member-add-title">添加成员</h2><button type="button" class="subtle icon-close" aria-label="关闭添加成员" :disabled="adding" @click="closeAdd"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg></button></div>
        <div class="dialog-body add-body">
          <div class="field">
            <label for="member-candidate-search">查找账号</label>
            <span class="search-box candidate-search">
              <svg class="ico" viewBox="0 0 16 16" aria-hidden="true"><path d="M7 12A5 5 0 1 0 7 2a5 5 0 0 0 0 10zM14 14l-3.5-3.5" /></svg>
              <input id="member-candidate-search" ref="searchInput" v-model="candidateQuery" class="control" type="search" placeholder="输入姓名、账号或部门" autocomplete="off" data-testid="member-candidate-search" :disabled="adding" />
            </span>
          </div>
          <div class="candidates" role="group" aria-label="候选账号">
            <StatePanel v-if="candidatePhase === 'loading'" kind="loading" text="正在查找账号…" class="compact" />
            <StatePanel v-else-if="candidatePhase === 'error'" kind="error" title="账号加载失败" :text="candidateError" class="compact"><button type="button" class="ghost" @click="searchCandidates">重试</button></StatePanel>
            <p v-else-if="!candidates.length" class="candidates-empty">{{ candidateQuery.trim() ? "没有找到账号。新账号需要系统管理员在“用户管理”中创建。" : "系统里的启用账号都已在项目中。" }}</p>
            <p v-else-if="!candidateQuery.trim() && candidates.every(item => item.isMember)" class="candidates-empty">系统里的启用账号都已在项目中。</p>
            <ul v-else class="candidate-list">
              <li v-for="candidate in candidates" :key="candidate.id">
                <label class="candidate" :class="{ member: candidate.isMember, on: isPicked(candidate) }" data-testid="member-candidate" :data-user-id="candidate.id">
                  <input type="checkbox" :checked="isPicked(candidate)" :disabled="candidate.isMember || adding" @change="togglePick(candidate)" />
                  <span class="avatar" aria-hidden="true">{{ initialOf(candidate.name) }}</span>
                  <span class="cand-name">{{ candidate.name }}</span>
                  <span class="cand-account">{{ candidate.account }}</span>
                  <span class="cand-dept">{{ candidate.isMember ? candidateNote(candidate.currentRole) || "已在项目中" : candidate.department || "—" }}</span>
                </label>
              </li>
            </ul>
          </div>
          <div class="picked" aria-live="polite">
            <span class="picked-label">已选 {{ picked.length }} 人<template v-if="picked.length">：</template></span>
            <span v-for="item in picked" :key="item.id" class="tag plain picked-tag">{{ item.name }}<button type="button" class="picked-x" :aria-label="`取消选择 ${item.name}`" :disabled="adding" @click="unpick(item)">×</button></span>
          </div>
          <fieldset class="field role-field">
            <legend class="required">项目权限</legend>
            <label v-for="option in ROLE_OPTIONS" :key="option.value" class="role-radio">
              <input v-model="addRole" type="radio" name="member-add-role" :value="option.value" :data-testid="`member-add-role-${option.value}`" :disabled="adding" />
              <span class="role-radio-label">{{ option.label }}</span><span class="role-radio-hint">{{ option.hint }}</span>
            </label>
          </fieldset>
          <p v-if="addResult" class="form-alert" role="alert">{{ addResult }}</p>
          <p v-if="addError" class="field-error" data-testid="member-add-error" role="alert">{{ addError }}</p>
        </div>
        <div class="dialog-actions">
          <button type="button" class="ghost" :disabled="adding" @click="closeAdd">取消</button>
          <button type="submit" class="primary" data-testid="member-add-submit" :disabled="adding">{{ addButtonText(picked.length, adding, retryMode) }}</button>
        </div>
      </form>
    </dialog>

    <dialog ref="removeDialog" class="app-dialog confirm" data-testid="member-remove-dialog" aria-labelledby="member-remove-title" @cancel.prevent="closeRemove">
      <form v-if="removing" novalidate @submit.prevent="confirmRemove">
        <div class="dialog-head">
          <h2 id="member-remove-title">{{ removing.isMe ? "退出该项目？" : `将 ${removing.name}（${removing.account}）移出项目？` }}</h2>
          <button type="button" class="subtle icon-close" aria-label="关闭" :disabled="removeBusy" @click="closeRemove"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg></button>
        </div>
        <div class="dialog-body">
          <p class="dialog-text">{{ removing.isMe ? `退出后你将不能再打开该项目，如需恢复请联系负责人 ${contactNames(snapshot?.owners.filter(owner => owner.id !== removing?.userId)) || "项目负责人"}。` : removeText.body }}</p>
          <p v-if="removeText.warning" class="notice warning">{{ removeText.warning }}</p>
          <p v-if="removeError" class="form-alert" role="alert">{{ removeError }}</p>
        </div>
        <div class="dialog-actions">
          <button type="button" class="ghost" :disabled="removeBusy" @click="closeRemove">取消</button>
          <button type="submit" class="primary danger" data-testid="member-remove-confirm" :disabled="removeBusy">{{ removeBusy ? "正在移出…" : removing.isMe ? "退出项目" : "移出项目" }}</button>
        </div>
      </form>
    </dialog>

    <dialog ref="selfDialog" class="app-dialog confirm" data-testid="member-self-dialog" aria-labelledby="member-self-title" @cancel.prevent="cancelSelf">
      <form v-if="selfPending" novalidate @submit.prevent="confirmSelf">
        <div class="dialog-head"><h2 id="member-self-title">把自己改为{{ roleLabel(selfPending.role) }}？</h2><button type="button" class="subtle icon-close" aria-label="关闭" @click="cancelSelf"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg></button></div>
        <div class="dialog-body"><p class="dialog-text">改完后你将不能再添加成员或调整权限。</p></div>
        <div class="dialog-actions">
          <button type="button" class="ghost" @click="cancelSelf">取消</button>
          <button type="submit" class="primary" data-testid="member-self-confirm">确认修改</button>
        </div>
      </form>
    </dialog>
  </section>
</template>

<style scoped>
.section-head { display:flex; justify-content:space-between; align-items:center; gap:var(--space-5); margin-bottom:var(--space-4); }
.members-section { min-width:0; padding-top:var(--space-6); border-top:1px solid var(--border-subtle); scroll-margin-top:var(--space-5); }
.members-section:focus { outline:none; }
.members-head { display:flex; align-items:center; gap:var(--space-2); min-width:0; }
.members-note { margin-left:var(--space-2); color:var(--text-tertiary); font-size:var(--text-sm); }
.members-tools { display:flex; align-items:center; gap:var(--space-3); }
.members-search { width:240px; }
.members-box { border:1px solid var(--border-default); border-radius:var(--radius-sheet); overflow:hidden; }
.members-box .compact { min-height:calc(var(--space-7) * 4); }
.members-table.scroll { max-height:calc(52px * 10 + 40px); overflow-y:auto; }
.members-table.scroll thead th { position:sticky; top:0; z-index:1; background:var(--bg-subtle); }
.members-table td { height:52px; vertical-align:middle; }
.col-member { width:340px; }
.col-dept { width:220px; }
.col-role { width:420px; }
.col-status { width:140px; }
.col-recent { width:240px; }
.col-ops { width:120px; }
.who { display:inline-flex; flex-direction:column; gap:2px; }
.who-name { display:inline-flex; align-items:center; gap:var(--space-2); color:var(--text-primary); font-size:var(--text-md); font-weight:var(--weight-medium); }
.who-account { color:var(--text-tertiary); font-size:var(--text-xs); }
.me { height:18px; line-height:18px; }
tr.off .who-name, tr.off .col-dept, tr.off .recent-time { color:var(--text-tertiary); }
.role-cell { display:flex; align-items:center; gap:var(--space-3); min-height:32px; }
.role-select { width:180px; }
.role-status, .role-lock { display:inline-flex; align-items:center; gap:4px; color:var(--text-tertiary); font-size:var(--text-xs); white-space:normal; max-width:220px; }
.role-status.saved { color:var(--success-600); }
.role-status.error { color:var(--danger-600); }
.recent-time { display:block; color:var(--text-primary); }
.recent-who { display:block; color:var(--text-tertiary); font-size:var(--text-xs); }
.empty-row { color:var(--text-tertiary); text-align:center; }
.notice-row { display:flex; align-items:center; gap:var(--space-3); }
.notice-row > span { flex:1; min-width:0; }
.members-info-line { margin-top:var(--space-3); border:1px solid var(--info-200); border-radius:var(--radius-control); }
.members-info-line > .ico { flex:none; color:var(--info-600); }
.candidate-search { width:100%; }
.member-add { width:var(--dialog-w-form); }
.add-body { display:grid; gap:var(--space-4); }
.candidates { max-height:calc(44px * 8); overflow-y:auto; border:1px solid var(--border-default); border-radius:var(--radius-control); }
.candidates .compact { min-height:calc(var(--space-7) * 3); }
.candidates-empty { margin:0; padding:var(--space-5); color:var(--text-tertiary); font-size:var(--text-sm); text-align:center; }
.candidate-list { margin:0; padding:0; list-style:none; }
.candidate-list li + li { border-top:1px solid var(--border-subtle); }
.candidate { display:grid; grid-template-columns:20px 24px 140px 160px 1fr; align-items:center; gap:var(--space-3); min-height:44px; padding:0 var(--space-4); cursor:pointer; font-size:var(--text-sm); }
.candidate.on { background:var(--info-50); }
.candidate.member { cursor:default; color:var(--text-tertiary); }
.cand-name { color:inherit; font-weight:var(--weight-medium); }
.candidate:not(.member) .cand-name { color:var(--text-primary); }
.cand-account, .cand-dept { color:var(--text-tertiary); }
.candidate:not(.member) .cand-dept { color:var(--text-secondary); }
.picked { display:flex; flex-wrap:wrap; align-items:center; gap:var(--space-2); min-height:24px; font-size:var(--text-sm); color:var(--text-secondary); }
.picked-tag { background:var(--info-50); color:var(--info-600); }
.picked-x { margin-left:2px; padding:0 2px; border:0; background:none; color:inherit; cursor:pointer; font:inherit; }
.role-field { display:grid; gap:var(--space-2); margin:0; padding:0; border:0; }
.role-field legend { margin-bottom:var(--space-2); font-size:var(--text-sm); color:var(--text-secondary); }
.role-radio { display:flex; align-items:center; gap:var(--space-2); font-size:var(--text-sm); }
.role-radio-label { color:var(--text-primary); }
.role-radio-hint { color:var(--text-tertiary); font-size:var(--text-xs); }
.sr-only { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); white-space:nowrap; }
</style>
