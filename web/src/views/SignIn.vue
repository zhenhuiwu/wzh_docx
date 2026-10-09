<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ApiError } from "../api/client";
import { fetchCsrf, login } from "../api/identity";
import { loginDestination, writeSession } from "../session";

const STAFF_KEY = "doc-workbench.staff-id";

const staffId = ref("admin");
const password = ref("");
const rememberStaffId = ref(false);
const showPassword = ref(false);
const staffError = ref("");
const passwordError = ref("");
const formError = ref("");
const phase = ref<"idle" | "checking">("idle");
const csrf = ref("");
const router = useRouter();
const route = useRoute();

const staffInput = ref<HTMLInputElement | null>(null);
const passwordInput = ref<HTMLInputElement | null>(null);

const busy = computed(() => phase.value === "checking");

onMounted(() => {
  try {
    const saved = localStorage.getItem(STAFF_KEY);
    if (saved) {
      staffId.value = saved;
      rememberStaffId.value = true;
    }
  } catch {
    rememberStaffId.value = false;
  }
});

function writeStaffId(value: string | null) {
  try {
    if (value) localStorage.setItem(STAFF_KEY, value);
    else localStorage.removeItem(STAFF_KEY);
  } catch {
    /* 浏览器拒绝本地存储时，登录表单仍可继续使用。 */
  }
}

function validate(): boolean {
  const staff = staffId.value.trim();
  staffError.value = staff ? "" : "请填写账号";
  passwordError.value = password.value ? "" : "请填写密码";
  return !staffError.value && !passwordError.value;
}

async function focusFirstInvalid() {
  await nextTick();
  const target = staffError.value ? staffInput.value : passwordInput.value;
  target?.focus();
}

async function onSubmit() {
  if (busy.value) return;
  formError.value = "";
  if (!validate()) {
    void focusFirstInvalid();
    return;
  }

  phase.value = "checking";
  const staff = staffId.value.trim();
  try {
    csrf.value = await fetchCsrf();
    const session = await login(staff, password.value, csrf.value);
    writeStaffId(rememberStaffId.value ? staff : null);
    writeSession({ staffId: session.user.account, name: session.user.name });
    password.value = "";
    showPassword.value = false;
    await router.replace(loginDestination(route.query.redirect));
  } catch (error) {
    phase.value = "idle";
    csrf.value = "";
    formError.value = error instanceof ApiError
      ? error.message
      : "暂时不能连接服务。";
    if (!(error instanceof ApiError) || error.code === "AUTH_REQUIRED") {
      await focusFirstInvalid();
      passwordInput.value?.focus();
    }
  }
}

function onStaffInput() {
  if (staffError.value) staffError.value = "";
  if (formError.value) formError.value = "";
}

function onPasswordInput() {
  if (passwordError.value) passwordError.value = "";
}
</script>

<template>
  <main class="desk">
    <form class="card" novalidate @submit.prevent="onSubmit">
      <p class="brand">文档工作室</p>
      <h1>登录</h1>

      <div class="field">
        <label for="staff-id">账号</label>
        <input
          id="staff-id"
          ref="staffInput"
          v-model="staffId"
          class="control"
          :class="{ 'is-invalid': staffError }"
          name="username"
          type="text"
          autocomplete="username"
          spellcheck="false"
          :disabled="busy"
          :aria-invalid="staffError ? 'true' : undefined"
          :aria-describedby="staffError ? 'staff-error' : undefined"
          @input="onStaffInput"
        />
        <p v-if="staffError" id="staff-error" class="field-error">{{ staffError }}</p>
      </div>

      <div class="field">
        <label for="password">密码</label>
        <div class="secret" :class="{ 'is-invalid': passwordError, 'is-disabled': busy }">
          <input
            id="password"
            ref="passwordInput"
            v-model="password"
            class="secret-input"
            :type="showPassword ? 'text' : 'password'"
            name="password"
            autocomplete="current-password"
            :disabled="busy"
            :aria-invalid="passwordError ? 'true' : undefined"
            :aria-describedby="passwordError ? 'password-error' : undefined"
            @input="onPasswordInput"
          />
          <button
            type="button"
            class="toggle"
            :aria-pressed="showPassword"
            :disabled="busy"
            @click="showPassword = !showPassword"
          >
            {{ showPassword ? "隐藏" : "显示" }}
          </button>
        </div>
        <p v-if="passwordError" id="password-error" class="field-error">{{ passwordError }}</p>
      </div>

      <label class="remember">
        <input v-model="rememberStaffId" type="checkbox" :disabled="busy" />
        <span>记住账号</span>
      </label>

      <button class="primary" type="submit" :disabled="busy" :aria-busy="busy">
        {{ busy ? "正在登录" : "登录" }}
      </button>
      <p v-if="formError" class="form-error" role="alert">{{ formError }}</p>

    </form>
  </main>
</template>

<style scoped>
.desk {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-7) var(--space-5);
  background: var(--desk);
}

.card {
  width: min(calc(var(--space-7) * 9), 100%);
  margin: 0;
  padding: var(--space-6) var(--space-5) var(--space-6);
  background: var(--sheet);
  border-radius: var(--radius-sheet);
  border: 1px solid var(--rule);
}

.brand {
  margin: 0;
  font-size: var(--text-sm);
  font-weight: 600;
  line-height: 1.4;
  color: var(--stamp);
}

.card h1 {
  margin: var(--space-1) 0 var(--space-5);
  font-size: calc(var(--text-md) * 1.5);
  font-weight: 600;
  line-height: 1.3;
  letter-spacing: -0.02em;
}

.field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.field + .field {
  margin-top: var(--space-4);
}

.field label {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--ink);
}

.control,
.secret {
  height: calc(var(--space-6) + var(--space-3));
  border: 1px solid var(--rule-strong);
  border-radius: var(--radius-control);
  background: var(--sheet);
  color: var(--ink);
}

.control {
  width: 100%;
  padding: 0 var(--space-3);
  font-size: var(--text-md);
}

.control:hover:not(:disabled),
.secret:hover:not(.is-disabled) {
  border-color: var(--ink-tertiary);
}

.control:focus-visible,
.secret:focus-within {
  outline: none;
  border-color: var(--stamp);
  box-shadow: 0 0 0 3px var(--focus-ring);
}

.control.is-invalid,
.secret.is-invalid {
  border-color: var(--fault);
  background: var(--fault-wash);
}

.control.is-invalid:focus-visible,
.secret.is-invalid:focus-within {
  border-color: var(--fault);
  box-shadow: 0 0 0 3px var(--fault-wash);
}

.control:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.secret {
  display: flex;
  align-items: center;
  padding-right: var(--space-2);
}

.secret.is-disabled {
  opacity: 0.6;
}

.secret-input {
  width: 100%;
  min-width: 0;
  height: 100%;
  padding: 0 var(--space-3);
  border: 0;
  background: transparent;
  color: inherit;
  font-size: var(--text-md);
  outline: none;
}

.secret-input:disabled {
  cursor: not-allowed;
}

.toggle {
  flex: none;
  height: var(--space-6);
  padding: 0 var(--space-2);
  border: 0;
  border-radius: var(--radius-control);
  background: transparent;
  color: var(--ink-secondary);
  font-size: var(--text-sm);
  font-weight: 600;
  cursor: pointer;
}

.toggle:hover:not(:disabled) {
  color: var(--ink);
  background: var(--desk);
}

.toggle:focus-visible {
  outline: 2px solid var(--stamp);
  outline-offset: 1px;
}

.toggle:disabled {
  cursor: not-allowed;
}

.field-error {
  margin: 0;
  font-size: var(--text-xs);
  line-height: 1.4;
  color: var(--fault);
}

.remember {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-height: var(--space-6);
  margin-top: var(--space-2);
  color: var(--ink-secondary);
  font-size: var(--text-sm);
  cursor: pointer;
}

.remember input {
  width: var(--space-4);
  height: var(--space-4);
  margin: 0;
  accent-color: var(--stamp);
}

.remember:has(input:disabled) {
  cursor: not-allowed;
  opacity: 0.6;
}

.primary {
  width: 100%;
  height: calc(var(--space-6) + var(--space-3));
  margin-top: var(--space-2);
  border: 0;
  border-radius: var(--radius-control);
  background: var(--stamp);
  color: var(--on-stamp);
  font-size: var(--text-md);
  font-weight: 600;
  cursor: pointer;
  transition:
    background-color 150ms var(--ease-out),
    transform 120ms var(--ease-out);
}

.primary:hover:not(:disabled) {
  background: var(--stamp-press);
}

.primary:focus-visible {
  outline: 2px solid var(--stamp);
  outline-offset: 3px;
}

.primary:active:not(:disabled) {
  transform: scale(0.98);
}

.primary:disabled {
  cursor: progress;
  opacity: 0.55;
}

.form-error {
  margin: var(--space-3) 0 0;
  font-size: var(--text-sm);
  line-height: 1.5;
  color: var(--fault);
}

@media (max-width: 480px) {
  .desk {
    align-items: flex-start;
    padding: 12vh var(--space-4) var(--space-6);
  }

  .card {
    padding: var(--space-5) var(--space-4) var(--space-5);
    box-shadow: none;
    background: transparent;
  }
}

@media (prefers-reduced-motion: reduce) {
  .primary {
    transition: background-color 150ms var(--ease-out);
  }

  .primary:active:not(:disabled) {
    transform: none;
  }
}
</style>

