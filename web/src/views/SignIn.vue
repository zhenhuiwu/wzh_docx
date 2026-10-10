<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ApiError } from "../api/client";
import { fetchCsrf, login } from "../api/identity";
import { loginDestination, writeSession } from "../session";

const STAFF_KEY = "doc-workbench.staff-id";

const staffId = ref("");
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
    writeSession({
      staffId: session.user.account,
      userId: session.user.id,
      name: session.user.name,
      mustChangePassword: Boolean(session.user.requiresFirstLoginChange),
      department: session.user.department,
      permissions: session.permissions,
    });
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
      <p class="brand"><span class="brand-logo" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M7 3.5h7l4 4V20.5H7zM14 3.5V8h4M10 12h5M10 15.5h5" /></svg></span>文档工作室</p>
      <h1>登录</h1>
      <p class="subtitle">使用公司分配的账号登录。</p>

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

      <p v-if="formError" class="form-error" role="alert">{{ formError }}</p>
      <button class="primary" type="submit" :disabled="busy" :aria-busy="busy">
        {{ busy ? "正在登录" : "登录" }}
      </button>
    </form>
    <p class="footnote">忘记密码请联系系统管理员重置。</p>
  </main>
</template>

<style scoped src="./auth-card.css"></style>
