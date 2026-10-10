<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ApiError } from "../api/client";
import { changePassword } from "../api/identity";
import { ensureSession, loginDestination, readSession } from "../session";
import { flash } from "../flash";

const MIN_LENGTH = 12;

const router = useRouter();
const route = useRoute();
const current = ref("");
const next = ref("");
const confirm = ref("");
const showPasswords = ref(false);
const currentError = ref("");
const nextError = ref("");
const confirmError = ref("");
const formError = ref("");
const phase = ref<"idle" | "saving">("idle");
const busy = computed(() => phase.value === "saving");
const required = computed(() => readSession()?.mustChangePassword ?? false);
const account = computed(() => readSession()?.staffId ?? "");

const currentInput = ref<HTMLInputElement | null>(null);
const nextInput = ref<HTMLInputElement | null>(null);
const confirmInput = ref<HTMLInputElement | null>(null);

onMounted(() => currentInput.value?.focus());

function validate(): boolean {
  currentError.value = current.value ? "" : "请填写当前密码";
  if (!next.value) nextError.value = "请填写新密码";
  else if (next.value.length < MIN_LENGTH) nextError.value = `新密码至少 ${MIN_LENGTH} 位`;
  else if (next.value === current.value) nextError.value = "新密码需要和当前密码不同";
  else nextError.value = "";
  confirmError.value = confirm.value === next.value ? "" : "两次输入的新密码不一致";
  return !currentError.value && !nextError.value && !confirmError.value;
}

async function focusFirstInvalid() {
  await nextTick();
  (currentError.value ? currentInput.value : nextError.value ? nextInput.value : confirmInput.value)?.focus();
}

async function onSubmit() {
  if (busy.value) return;
  formError.value = "";
  if (!validate()) {
    void focusFirstInvalid();
    return;
  }
  phase.value = "saving";
  try {
    await changePassword(current.value, next.value);
    current.value = next.value = confirm.value = "";
    // 改密会轮换请求校验令牌，重新读取会话后再进入业务页面。
    await ensureSession();
    await router.replace(loginDestination(route.query.redirect));
    flash("密码已修改。");
  } catch (error) {
    phase.value = "idle";
    if (error instanceof ApiError && error.code === "CURRENT_PASSWORD_INCORRECT") {
      currentError.value = error.message;
    } else if (error instanceof ApiError && error.code === "AUTH_REQUIRED") {
      // 会话已失效（例如在其他窗口退出），回到登录页。
      await router.replace({ path: "/login" });
      return;
    } else if (error instanceof ApiError && error.fieldErrors.length) {
      for (const item of error.fieldErrors) {
        if (item.field === "currentPassword") currentError.value = item.message;
        else if (item.field === "newPassword") nextError.value = item.message;
      }
      // 字段下已显示具体原因，不再在底部重复（02 评审 2.4 S-2）。
      if (!currentError.value && !nextError.value) formError.value = error.message;
    } else {
      formError.value = error instanceof ApiError ? error.message : "暂时不能连接服务。";
    }
    void focusFirstInvalid();
  }
}

function clearError(field: "current" | "next" | "confirm") {
  if (field === "current") currentError.value = "";
  if (field === "next") nextError.value = "";
  if (field === "confirm") confirmError.value = "";
  formError.value = "";
}

function cancel() {
  void router.replace(loginDestination(route.query.redirect));
}

function signOut() {
  void router.push({ name: "login", query: { logout: "1" } });
}
</script>

<template>
  <main class="desk">
    <form class="card" novalidate @submit.prevent="onSubmit">
      <p class="brand"><span class="brand-logo" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M7 3.5h7l4 4V20.5H7zM14 3.5V8h4M10 12h5M10 15.5h5" /></svg></span>文档工作室</p>
      <h1>修改密码</h1>
      <p v-if="required" class="notice" role="status">
        账号 {{ account }} 正在使用初始或管理员设定的临时密码，修改后才能继续使用。
      </p>
      <p v-else class="subtitle">账号 {{ account }}。修改后其他已登录的窗口需要重新登录。</p>

      <div class="field">
        <label for="current-password">当前密码</label>
        <input
          id="current-password"
          ref="currentInput"
          v-model="current"
          class="control"
          :class="{ 'is-invalid': currentError }"
          :type="showPasswords ? 'text' : 'password'"
          autocomplete="current-password"
          :disabled="busy"
          :aria-invalid="currentError ? 'true' : undefined"
          :aria-describedby="currentError ? 'current-error' : undefined"
          @input="clearError('current')"
        />
        <p v-if="currentError" id="current-error" class="field-error">{{ currentError }}</p>
      </div>

      <div class="field">
        <label for="new-password">新密码</label>
        <input
          id="new-password"
          ref="nextInput"
          v-model="next"
          class="control"
          :class="{ 'is-invalid': nextError }"
          :type="showPasswords ? 'text' : 'password'"
          autocomplete="new-password"
          :disabled="busy"
          :aria-invalid="nextError ? 'true' : undefined"
          aria-describedby="new-hint"
          @input="clearError('next')"
        />
        <p v-if="nextError" class="field-error">{{ nextError }}</p>
        <p v-else id="new-hint" class="hint">至少 {{ MIN_LENGTH }} 位，且不能与当前密码相同。</p>
      </div>

      <div class="field">
        <label for="confirm-password">确认新密码</label>
        <input
          id="confirm-password"
          ref="confirmInput"
          v-model="confirm"
          class="control"
          :class="{ 'is-invalid': confirmError }"
          :type="showPasswords ? 'text' : 'password'"
          autocomplete="new-password"
          :disabled="busy"
          :aria-invalid="confirmError ? 'true' : undefined"
          :aria-describedby="confirmError ? 'confirm-error' : undefined"
          @input="clearError('confirm')"
        />
        <p v-if="confirmError" id="confirm-error" class="field-error">{{ confirmError }}</p>
      </div>

      <label class="remember">
        <input v-model="showPasswords" type="checkbox" :disabled="busy" />
        <span>显示密码</span>
      </label>

      <p v-if="formError" class="form-error" role="alert">{{ formError }}</p>
      <button class="primary" type="submit" :disabled="busy" :aria-busy="busy">
        {{ busy ? "正在保存" : "保存新密码" }}
      </button>
      <!-- 次要操作用文字按钮，避免与主按钮同权重（02 评审 2.4 S-3 / S-5）。 -->
      <button v-if="required" class="link-button" type="button" :disabled="busy" @click="signOut">不修改，退出登录</button>
      <button v-else class="link-button" type="button" :disabled="busy" @click="cancel">取消</button>
    </form>
  </main>
</template>

<style scoped src="./auth-card.css"></style>
<style scoped>
.notice {
  margin: 0 0 var(--space-5);
  padding: var(--space-3);
  border: 1px solid var(--warning-200);
  border-radius: var(--radius-control);
  background: var(--warning-50);
  color: var(--text-primary);
  font-size: var(--text-sm);
  line-height: var(--leading-sm);
}

.hint {
  margin: 0;
  font-size: var(--text-xs);
  line-height: var(--leading-xs);
  color: var(--text-tertiary);
}

.link-button {
  display: block;
  margin: var(--space-3) auto 0;
  padding: var(--space-1) var(--space-2);
  border: 0;
  border-radius: var(--radius-control);
  background: transparent;
  color: var(--text-secondary);
  font-size: var(--text-sm);
  cursor: pointer;
}

.link-button:hover:not(:disabled) {
  color: var(--text-primary);
  text-decoration: underline;
  text-underline-offset: 3px;
}

.link-button:focus-visible {
  outline: none;
  box-shadow: var(--focus-shadow);
}

.link-button:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}
</style>
