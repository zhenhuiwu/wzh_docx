<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ApiError } from "../api/client";
import { changePassword } from "../api/identity";
import { ensureSession, loginDestination, readSession } from "../session";

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
      formError.value = error.message;
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

function signOut() {
  void router.push({ name: "login", query: { logout: "1" } });
}
</script>

<template>
  <main class="desk">
    <form class="card" novalidate @submit.prevent="onSubmit">
      <p class="brand">文档工作室</p>
      <h1>修改密码</h1>
      <p v-if="required" class="notice" role="status">
        账号 {{ account }} 正在使用初始或管理员设定的临时密码，修改后才能继续使用。
      </p>

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

      <button class="primary" type="submit" :disabled="busy" :aria-busy="busy">
        {{ busy ? "正在保存" : "保存新密码" }}
      </button>
      <button class="ghost sign-out" type="button" :disabled="busy" @click="signOut">退出登录</button>
      <p v-if="formError" class="form-error" role="alert">{{ formError }}</p>
    </form>
  </main>
</template>

<style scoped src="./auth-card.css"></style>
<style scoped>
.notice {
  margin: 0 0 var(--space-4);
  padding: var(--space-3);
  border-radius: var(--radius-control);
  background: var(--warn-wash);
  color: var(--ink);
  font-size: var(--text-sm);
  line-height: 1.5;
}

.hint {
  margin: 0;
  font-size: var(--text-xs);
  line-height: 1.4;
  color: var(--ink-secondary);
}

.sign-out {
  width: 100%;
  height: calc(var(--space-6) + var(--space-3));
  margin-top: var(--space-2);
  font-size: var(--text-md);
}
</style>
