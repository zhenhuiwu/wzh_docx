<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from "vue";
import { ApiError } from "../api/client";
import {
  createAccount,
  disableAccount,
  enableAccount,
  listRoles,
  listUsers,
  resetAccountCredentials,
  revokeAccountSessions,
  type AccountUser,
  type RoleDefinition,
} from "../api/users";

const page = ref(1);
const pageSize = 10;
const applied = ref({ name: "", account: "", department: "", status: "" as "" | "active" | "disabled", role: "" });
const draft = ref({ ...applied.value });
const items = ref<AccountUser[]>([]);
const total = ref(0);
const roles = ref<RoleDefinition[]>([]);
const phase = ref<"loading" | "ready" | "error">("loading");
const errorText = ref("");
const creating = ref(false);
const submitting = ref(false);
const formError = ref("");
const notice = ref("");
const account = ref("");
const password = ref("");
const rolesError = ref("");
const personName = ref("");
const department = ref("");
const role = ref("");
const reason = ref("");
const pending = ref<AccountUser | null>(null);
const pendingAction = ref<"disable" | "enable" | "reset" | "revoke" | "">("");
const accountInput = ref<HTMLInputElement | null>(null);
let usersRequest = 0;

const globalRoles = computed(() => roles.value.filter((item) => item.scope === "global" && item.assignable));
const roleName = computed(() => Object.fromEntries(roles.value.map((item) => [item.key, item.name])));
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / pageSize)));

function statusLabel(status: AccountUser["status"]) {
  return status === "active" ? "启用" : "停用";
}

function formatTime(value: string) {
  return value.replace("T", " ").replace("Z", "");
}

function messageOf(error: unknown) {
  return error instanceof ApiError ? error.message : "暂时不能连接服务。";
}

async function loadRoles() {
  rolesError.value = "";
  try {
    roles.value = await listRoles();
    if (!role.value && globalRoles.value[0]) role.value = globalRoles.value[0].key;
  } catch (error) {
    rolesError.value = messageOf(error);
  }
}

async function loadUsers() {
  const request = ++usersRequest;
  phase.value = "loading";
  errorText.value = "";
  try {
    const result = await listUsers({ page: page.value, pageSize, ...applied.value });
    if (request !== usersRequest) return;
    items.value = result.items;
    total.value = result.total;
    page.value = result.page;
    phase.value = "ready";
  } catch (error) {
    if (request !== usersRequest) return;
    phase.value = "error";
    errorText.value = messageOf(error);
  }
}

function search() {
  applied.value = { ...draft.value };
  page.value = 1;
  void loadUsers();
}

function resetQuery() {
  draft.value = { name: "", account: "", department: "", status: "", role: "" };
  search();
}

async function openCreate() {
  if (submitting.value) return;
  cancelAsk();
  password.value = "";
  creating.value = true;
  formError.value = "";
  notice.value = "";
  await nextTick();
  accountInput.value?.focus();
}

function cancelCreate() {
  creating.value = false;
  submitting.value = false;
  account.value = "";
  password.value = "";
  personName.value = "";
  department.value = "";
  formError.value = "";
}

async function submitCreate() {
  if (submitting.value) return;
  formError.value = "";
  if (!account.value.trim() || !personName.value.trim()) {
    formError.value = "请填写账号和姓名";
    return;
  }
  if (!role.value) {
    formError.value = "角色还没有加载，暂时不能创建";
    return;
  }
  if (password.value.length < 12 || password.value.length > 1024) {
    formError.value = "密码需为 12 至 1024 个字符。";
    return;
  }
  submitting.value = true;
  try {
    await createAccount({
      account: account.value.trim(),
      password: password.value,
      name: personName.value.trim(),
      department: department.value.trim(),
      globalRoles: [role.value],
    });
    notice.value = "账号已创建。";
    cancelCreate();
    await loadUsers();
  } catch (error) {
    formError.value = messageOf(error);
  } finally {
    submitting.value = false;
  }
}

function ask(user: AccountUser, action: "disable" | "enable" | "reset" | "revoke") {
  if (submitting.value) return;
  cancelCreate();
  pending.value = user;
  pendingAction.value = action;
  reason.value = "";
  password.value = "";
  formError.value = "";
  notice.value = "";
}

function cancelAsk() {
  pending.value = null;
  pendingAction.value = "";
  reason.value = "";
  password.value = "";
}

async function confirmAsk() {
  if (submitting.value || !pending.value || !pendingAction.value) return;
  if (pendingAction.value !== "revoke" && !reason.value.trim()) {
    formError.value = "请填写原因";
    return;
  }
  if (pendingAction.value === "reset" && (password.value.length < 12 || password.value.length > 1024)) {
    formError.value = "密码需为 12 至 1024 个字符。";
    return;
  }
  submitting.value = true;
  formError.value = "";
  try {
    const user = pending.value;
    if (pendingAction.value === "disable") await disableAccount(user, reason.value.trim());
    if (pendingAction.value === "enable") await enableAccount(user, reason.value.trim());
    if (pendingAction.value === "reset") {
      const result = await resetAccountCredentials(user, reason.value.trim(), password.value);
      notice.value = result?.message ?? "凭据已处理。";
    }
    if (pendingAction.value === "revoke") {
      await revokeAccountSessions(user.id);
      notice.value = "这个账号的会话已经撤销。";
    }
    if (pendingAction.value === "disable") notice.value = "账号已停用。";
    if (pendingAction.value === "enable") notice.value = "账号已启用。";
    cancelAsk();
    await loadUsers();
  } catch (error) {
    formError.value = messageOf(error);
  } finally {
    submitting.value = false;
  }
}

function changePage(next: number) {
  page.value = next;
  void loadUsers();
}

onMounted(async () => {
  await Promise.all([loadRoles(), loadUsers()]);
});
</script>

<template>
  <section class="card">
    <div class="head">
      <h1>用户管理</h1>
      <div class="actions">
        <button v-if="!creating" type="button" class="primary" :disabled="submitting" @click="openCreate">新建用户</button>
      </div>
    </div>

    <p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <p v-if="rolesError" class="field-error" role="alert">角色加载失败：{{ rolesError }} <button type="button" class="ghost" @click="loadRoles">重试</button></p>
    <p v-if="phase === 'error'" class="notice" role="alert">{{ errorText }}</p>

    <form class="query" @submit.prevent="search">
      <div class="query-fields">
        <label><span>姓名</span><input v-model="draft.name" class="control" type="search" /></label>
        <label><span>账号</span><input v-model="draft.account" class="control" type="search" /></label>
        <label><span>部门</span><input v-model="draft.department" class="control" type="search" /></label>
        <label>
          <span>角色</span>
          <select v-model="draft.role" class="control">
            <option value="">全部</option>
            <option v-for="item in globalRoles" :key="item.key" :value="item.key">{{ item.name }}</option>
          </select>
        </label>
        <label>
          <span>启停</span>
          <select v-model="draft.status" class="control">
            <option value="">全部</option>
            <option value="active">启用</option>
            <option value="disabled">停用</option>
          </select>
        </label>
      </div>
      <div class="query-actions">
        <button type="submit" class="primary" :disabled="phase === 'loading'">查询</button>
        <button type="button" class="ghost" @click="resetQuery">重置</button>
      </div>
    </form>

    <form v-if="creating" class="create" novalidate @submit.prevent="submitCreate">
      <label for="user-account">账号</label>
      <input id="user-account" ref="accountInput" v-model="account" class="control" type="text" autocomplete="off" :disabled="submitting" />
      <label for="user-name">姓名</label>
      <input id="user-name" v-model="personName" class="control" type="text" autocomplete="off" :disabled="submitting" />
      <label for="user-password">初始密码（至少 12 个字符，用户首次登录后须修改）</label>
      <input id="user-password" v-model="password" class="control" type="password" autocomplete="new-password" required minlength="12" maxlength="1024" :disabled="submitting" />
      <label for="user-department">部门</label>
      <input id="user-department" v-model="department" class="control" type="text" autocomplete="off" :disabled="submitting" />
      <label for="user-role">角色</label>
      <select id="user-role" v-model="role" class="control" :disabled="submitting || globalRoles.length === 0">
        <option v-for="item in globalRoles" :key="item.key" :value="item.key">{{ item.name }}</option>
      </select>
      <p v-if="formError" class="field-error" role="alert">{{ formError }}</p>
      <div class="actions">
        <button type="submit" class="primary" :disabled="submitting">{{ submitting ? "正在创建" : "创建" }}</button>
        <button type="button" class="ghost" :disabled="submitting" @click="cancelCreate">取消</button>
      </div>
    </form>

    <form v-if="pending" class="create" novalidate @submit.prevent="confirmAsk">
      <p class="notice">
        {{ pending.name }}（{{ pending.account }}）
        <template v-if="pendingAction === 'disable'">停用后不能登录，已有会话会失效。</template>
        <template v-else-if="pendingAction === 'enable'">启用后可以重新登录。</template>
        <template v-else-if="pendingAction === 'reset'">重置后原密码与已有会话失效，用户下次登录须修改密码。</template>
        <template v-else>撤销后该账号需要重新登录。</template>
      </p>
      <template v-if="pendingAction === 'reset'">
        <label for="reset-password">新密码（至少 12 个字符）</label>
        <input id="reset-password" v-model="password" class="control" type="password" autocomplete="new-password" required minlength="12" maxlength="1024" :disabled="submitting" />
      </template>
      <label v-if="pendingAction !== 'revoke'" for="user-reason">原因</label>
      <input v-if="pendingAction !== 'revoke'" id="user-reason" v-model="reason" class="control" type="text" :disabled="submitting" />
      <p v-if="formError" class="field-error" role="alert">{{ formError }}</p>
      <div class="actions">
        <button type="submit" class="primary" :disabled="submitting">{{ submitting ? "正在提交" : "确认" }}</button>
        <button type="button" class="ghost" :disabled="submitting" @click="cancelAsk">取消</button>
      </div>
    </form>

    <p v-if="phase === 'loading'" class="empty">正在读取账号</p>
    <p v-else-if="phase === 'error'" class="empty">
      账号没有载入。
      <button type="button" class="ghost" @click="loadUsers">重试</button>
    </p>
    <p v-else-if="items.length === 0" class="empty">没有符合条件的账号</p>

    <div v-else class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>姓名</th>
            <th>账号</th>
            <th class="from-wide">部门</th>
            <th class="from-mid">角色</th>
            <th>启停</th>
            <th class="from-wide">更新时间</th>
            <th class="from-mid"><span class="sr">操作</span></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="user in items" :key="user.id">
            <td>{{ user.name }}</td>
            <td>{{ user.account }}</td>
            <td class="from-wide">{{ user.department || "—" }}</td>
            <td class="from-mid">{{ user.globalRoles.map((item) => roleName[item] || item).join("、") || "—" }}</td>
            <td><span class="status" :data-status="statusLabel(user.status)">{{ statusLabel(user.status) }}</span></td>
            <td class="from-wide time">{{ formatTime(user.updatedAt) }}</td>
            <td class="ops from-mid">
              <button type="button" class="text" :disabled="submitting" @click="ask(user, user.status === 'active' ? 'disable' : 'enable')">
                {{ user.status === "active" ? "停用" : "启用" }}
              </button>
              <button type="button" class="text" :disabled="submitting" @click="ask(user, 'reset')">重置密码</button>
              <button type="button" class="text" :disabled="submitting" @click="ask(user, 'revoke')">撤销会话</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="phase === 'ready' && total > pageSize" class="pager">
      <span class="total">共 {{ total }} 条</span>
      <button type="button" class="page" :disabled="page <= 1" @click="changePage(page - 1)">上一页</button>
      <button type="button" class="page" :disabled="page >= pageCount" @click="changePage(page + 1)">下一页</button>
    </div>
  </section>
</template>

<style scoped>
.actions {
  display: flex;
  gap: var(--space-2);
  align-items: center;
}

.linkish {
  display: inline-flex;
  align-items: center;
  text-decoration: none;
}

.create {
  display: grid;
  gap: var(--space-2);
  margin: var(--space-4) 0;
}

.create .control {
  width: min(360px, 100%);
}

.table-wrap :deep(table) {
  width: 100%;
}

.table-wrap :deep(th:last-child),
.table-wrap :deep(td.ops) {
  position: static;
  right: auto;
  box-shadow: none;
}

@media (max-width: 767px) {
  .from-mid,
  .from-wide {
    display: none;
  }
}

@media (min-width: 768px) and (max-width: 1279px) {
  .from-wide {
    display: none;
  }
}
</style>
