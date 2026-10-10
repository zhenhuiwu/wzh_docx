<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";
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
import StatePanel from "../components/StatePanel.vue";
import { hasPermission, readSession } from "../session";
import { formatDateTime, initialOf } from "../format";
import { useFieldErrors } from "../fieldErrors";

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
const fields = useFieldErrors<"account" | "name" | "password">("user");
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
const roleHint = computed(() => {
  const selected = globalRoles.value.find((item) => item.key === role.value);
  return selected ? selected.description : "不选则只能访问被加入的项目；可在项目中按成员授予权限。";
});
const canManage = computed(() => hasPermission(readSession(), "users.manage"));
// 给自己重置密码时后端不要求下次改密（09 审查 S2）。
const pendingIsSelf = computed(() => Boolean(pending.value && pending.value.account === readSession()?.staffId));
const roleName = computed(() => Object.fromEntries(roles.value.map((item) => [item.key, item.name])));
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / pageSize)));

function statusLabel(status: AccountUser["status"]) {
  return status === "active" ? "启用" : "停用";
}

function formatTime(value: string) {
  return formatDateTime(value);
}

function messageOf(error: unknown) {
  return error instanceof ApiError ? error.message : "暂时不能连接服务。";
}

async function loadRoles() {
  rolesError.value = "";
  try {
    roles.value = await listRoles();
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
  role.value = "";
  formError.value = "";
  fields.clear();
}

async function submitCreate() {
  if (submitting.value) return;
  formError.value = "";
  if (await fields.report({
    account: account.value.trim() ? "" : "请填写账号。",
    name: personName.value.trim() ? "" : "请填写姓名。",
    password: password.value.length < 12 || password.value.length > 1024 ? "密码需为 12 至 1024 个字符。" : "",
  })) return;
  submitting.value = true;
  try {
    await createAccount({
      account: account.value.trim(),
      password: password.value,
      name: personName.value.trim(),
      department: department.value.trim(),
      // 不预选角色（09 审查 M3）：不选即不授予全局角色，账号只能访问被加入的项目。
      globalRoles: role.value ? [role.value] : [],
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

// 新建与确认改为页面内对话框（02 评审 P2-7），开关仍由 creating / pending 控制。
const createDialog = ref<HTMLDialogElement | null>(null);
const confirmDialog = ref<HTMLDialogElement | null>(null);
const actionTitle = computed(() => ({ disable: "停用", enable: "启用", reset: "重置密码", revoke: "撤销会话", "": "" })[pendingAction.value]);
function syncDialog(dialog: HTMLDialogElement | null, open: boolean) {
  if (!dialog) return;
  if (open && !dialog.open) dialog.showModal();
  if (!open && dialog.open) dialog.close();
}
watch(creating, async (open) => { await nextTick(); syncDialog(createDialog.value, open); if (open) accountInput.value?.focus(); });
watch(pending, async (value) => { await nextTick(); syncDialog(confirmDialog.value, Boolean(value)); });
function dismissCreate() { if (!submitting.value) cancelCreate(); }
function dismissAsk() { if (!submitting.value) { cancelAsk(); formError.value = ""; } }

onMounted(async () => {
  await Promise.all([loadRoles(), loadUsers()]);
});
</script>

<template>
  <section class="view fill">
    <header class="page-head">
      <div>
        <h1>用户管理</h1>
        <p class="page-desc">管理登录账号、全局角色与账号状态。</p>
      </div>
      <div class="page-actions">
        <button v-if="canManage" type="button" class="primary" :disabled="submitting" @click="openCreate"><svg class="ico" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3v10M3 8h10" /></svg>新建用户</button>
      </div>
    </header>

    <div class="card users-card">
      <p v-if="notice" class="notice success" role="status">{{ notice }}</p>
      <p v-if="rolesError" class="notice" role="alert">角色加载失败：{{ rolesError }} <button type="button" class="text" @click="loadRoles">重试</button></p>

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
            <select v-model="draft.status" class="control narrow">
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

      <StatePanel v-if="phase === 'loading'" kind="loading" text="正在读取账号…" />
      <StatePanel v-else-if="phase === 'error'" kind="error" title="账号没有载入" :text="errorText"><button type="button" class="ghost" @click="loadUsers">重试</button></StatePanel>
      <StatePanel v-else-if="items.length === 0" title="没有符合条件的账号" text="换个条件试试，或清除筛选。"><button type="button" class="ghost" @click="resetQuery">清除筛选</button></StatePanel>

      <div v-else class="table-wrap user-table">
        <table>
          <thead>
            <tr>
              <th>姓名</th>
              <th>账号</th>
              <th>部门</th>
              <th>角色</th>
              <th>启停</th>
              <th>更新时间</th>
              <th class="th-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="user in items" :key="user.id">
              <td><span class="person"><span class="avatar" :class="{ off: user.status !== 'active' }" aria-hidden="true">{{ initialOf(user.name) }}</span><span class="user-name">{{ user.name }}</span></span></td>
              <td class="muted">{{ user.account }}</td>
              <td>{{ user.department || "—" }}</td>
              <td><span class="role-list"><span v-for="item in user.globalRoles" :key="item" class="tag plain" :class="{ info: item === 'system_admin' }">{{ roleName[item] || item }}</span><span v-if="!user.globalRoles.length" class="faint">—</span></span></td>
              <td><span class="status" :data-status="statusLabel(user.status)">{{ statusLabel(user.status) }}</span></td>
              <td class="time num">{{ formatTime(user.updatedAt) }}</td>
              <td class="ops">
                <button type="button" class="text" :class="{ danger: user.status === 'active' }" :disabled="submitting" @click="ask(user, user.status === 'active' ? 'disable' : 'enable')">
                  {{ user.status === "active" ? "停用" : "启用" }}
                </button>
                <button type="button" class="text" :disabled="submitting" @click="ask(user, 'reset')">重置密码</button>
                <button type="button" class="text" :disabled="submitting" @click="ask(user, 'revoke')">撤销会话</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="phase === 'ready' && items.length" class="pager">
        <span class="total">共 {{ total }} 条</span>
        <template v-if="total > pageSize">
          <button type="button" class="page" :disabled="page <= 1" @click="changePage(page - 1)">上一页</button>
          <span>第 {{ page }} / {{ pageCount }} 页</span>
          <button type="button" class="page" :disabled="page >= pageCount" @click="changePage(page + 1)">下一页</button>
        </template>
      </div>
    </div>

    <dialog ref="createDialog" class="app-dialog" aria-labelledby="create-user-title" @cancel.prevent="dismissCreate">
      <form v-if="creating" class="create" novalidate @submit.prevent="submitCreate">
        <div class="dialog-head"><h2 id="create-user-title">新建用户</h2><button type="button" class="subtle icon-close" aria-label="关闭新建用户" :disabled="submitting" @click="dismissCreate"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg></button></div>
        <div class="dialog-fields">
          <div class="field"><label for="user-account" class="required">账号</label><input id="user-account" ref="accountInput" v-model="account" class="control" v-bind="fields.invalid('account')" type="text" autocomplete="off" :disabled="submitting" @input="fields.clear('account')" /><span v-if="fields.errors.value.account" :id="fields.errorId('account')" class="field-error" role="alert">{{ fields.errors.value.account }}</span></div>
          <div class="field"><label for="user-name" class="required">姓名</label><input id="user-name" v-model="personName" class="control" v-bind="fields.invalid('name')" type="text" autocomplete="off" :disabled="submitting" @input="fields.clear('name')" /><span v-if="fields.errors.value.name" :id="fields.errorId('name')" class="field-error" role="alert">{{ fields.errors.value.name }}</span></div>
          <div class="field"><label for="user-department">部门</label><input id="user-department" v-model="department" class="control" type="text" autocomplete="off" :disabled="submitting" /></div>
          <div class="field">
            <label for="user-role">全局角色</label>
            <select id="user-role" v-model="role" class="control" aria-describedby="user-role-hint" :disabled="submitting">
              <option value="">不分配全局角色（仅项目成员）</option>
              <option v-for="item in globalRoles" :key="item.key" :value="item.key">{{ item.name }}</option>
            </select>
            <p id="user-role-hint" class="hint">{{ roleHint }}</p>
          </div>
          <div class="field span-2">
            <label for="user-password" class="required">初始密码</label>
            <input id="user-password" v-model="password" class="control" v-bind="fields.invalid('password', 'user-password-hint')" type="password" autocomplete="new-password" required minlength="12" maxlength="1024" :disabled="submitting" @input="fields.clear('password')" />
            <span v-if="fields.errors.value.password" :id="fields.errorId('password')" class="field-error" role="alert">{{ fields.errors.value.password }}</span>
            <p id="user-password-hint" class="hint">至少 12 个字符，用户首次登录后须修改。</p>
          </div>
        </div>
        <p v-if="formError" class="form-alert dialog-status" role="alert">{{ formError }}</p>
        <div class="dialog-actions">
          <button type="button" class="ghost" :disabled="submitting" @click="cancelCreate">取消</button>
          <button type="submit" class="primary" :disabled="submitting">{{ submitting ? "正在创建" : "创建" }}</button>
        </div>
      </form>
    </dialog>

    <dialog ref="confirmDialog" class="app-dialog confirm" aria-labelledby="confirm-user-title" @cancel.prevent="dismissAsk">
      <form v-if="pending" class="create" novalidate @submit.prevent="confirmAsk">
        <div class="dialog-head"><h2 id="confirm-user-title">{{ actionTitle }} {{ pending.name }}（{{ pending.account }}）？</h2><button type="button" class="subtle icon-close" aria-label="关闭" :disabled="submitting" @click="dismissAsk"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg></button></div>
        <div class="dialog-body confirm-body">
          <p class="dialog-text">
            <template v-if="pendingAction === 'disable'">停用后不能登录，已有会话会失效。</template>
            <template v-else-if="pendingAction === 'enable'">启用后可以重新登录。</template>
            <template v-else-if="pendingAction === 'reset' && pendingIsSelf">重置后原密码与已有会话失效。</template>
              <template v-else-if="pendingAction === 'reset'">重置后原密码与已有会话失效，用户下次登录须修改密码。</template>
            <template v-else>撤销后该账号需要重新登录。</template>
          </p>
          <div v-if="pendingAction === 'reset'" class="field">
            <label for="reset-password" class="required">新密码</label>
            <input id="reset-password" v-model="password" class="control" type="password" autocomplete="new-password" required minlength="12" maxlength="1024" aria-describedby="reset-password-hint" :disabled="submitting" />
            <p id="reset-password-hint" class="hint">至少 12 个字符。</p>
          </div>
          <div v-if="pendingAction !== 'revoke'" class="field">
            <label for="user-reason" class="required">原因</label>
            <input id="user-reason" v-model="reason" class="control" type="text" :disabled="submitting" />
          </div>
          <p v-if="formError" class="form-alert" role="alert">{{ formError }}</p>
        </div>
        <div class="dialog-actions">
          <button type="button" class="ghost" :disabled="submitting" @click="cancelAsk">取消</button>
          <button type="submit" class="primary" :class="{ danger: pendingAction === 'disable' }" :disabled="submitting">{{ submitting ? "正在提交" : `确认${actionTitle}` }}</button>
        </div>
      </form>
    </dialog>
  </section>
</template>

<style scoped>
.users-card { overflow:hidden; }
.user-table { flex:1; min-height:0; overflow-y:auto; }
.query .narrow { width:calc(var(--space-7) * 2); }
.user-name { font-weight:var(--weight-medium); }
.avatar.off { background:var(--gray-150); color:var(--gray-600); }
.role-list { display:inline-flex; flex-wrap:wrap; gap:var(--space-1); }
.confirm-body { display:grid; gap:var(--space-4); }
.confirm-body .dialog-text { margin:0; }
.create { display:block; padding:0; }
.create label { margin:0; }
</style>
