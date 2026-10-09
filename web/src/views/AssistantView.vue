<script setup lang="ts">
import { nextTick, ref } from "vue";
import { useAssistant } from "../assistant";

const { turns, ask, clear } = useAssistant();
const draft = ref("");
const thread = ref<HTMLElement | null>(null);
const input = ref<HTMLTextAreaElement | null>(null);

async function send() {
  const text = draft.value;
  if (!text.trim()) return;
  draft.value = "";
  ask(text);
  await nextTick();
  const box = thread.value;
  if (box) box.scrollTop = box.scrollHeight;
  input.value?.focus();
}

function onKeydown(event: KeyboardEvent) {
  if (event.isComposing || event.key !== "Enter" || event.shiftKey) return;
  event.preventDefault();
}
</script>

<template>
  <section class="page">
    <header class="head">
      <h1>智能助手</h1>
      <button v-if="turns.length" type="button" class="ghost" @click="clear">清空对话</button>
    </header>
    <p class="notice">助手暂不可用。模型连接完成后开放。</p>

    <div ref="thread" class="thread" aria-live="polite">
      <p v-if="turns.length === 0" class="empty">输入问题开始对话</p>
      <article v-for="turn in turns" :key="turn.id" class="turn" :class="turn.role">
        <p>{{ turn.text }}</p>
      </article>
    </div>

    <form class="composer" @submit.prevent="send">
      <label class="sr" for="assistant-input">问题</label>
      <textarea
        id="assistant-input"
        ref="input"
        v-model="draft"
        rows="2"
        disabled
        placeholder="模型连接完成后可输入"
        @keydown="onKeydown"
      ></textarea>
      <button type="submit" class="primary" disabled>发送</button>
    </form>
  </section>
</template>

<style scoped>
.page {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  background: var(--sheet);
  border: 1px solid var(--rule);
  border-radius: var(--radius-sheet);
}

.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  min-height: calc(var(--space-7) + var(--space-2));
  padding: 0 var(--space-4);
  border-bottom: 1px solid var(--rule);
}

.head h1 {
  margin: 0;
  font-size: var(--text-md);
  font-weight: 600;
}

.thread {
  flex: 1;
  min-height: 280px;
  padding: var(--space-4);
  overflow: auto;
}

.empty {
  margin: 48px 0 0;
  color: var(--ink-tertiary);
  text-align: center;
}

.turn {
  display: flex;
  margin-bottom: var(--space-3);
}

.turn p {
  max-width: min(640px, 80%);
  margin: 0;
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-control);
  font-size: var(--text-sm);
  line-height: 1.6;
  white-space: pre-wrap;
}

.turn.user {
  justify-content: flex-end;
}

.turn.user p {
  background: var(--stamp-wash);
  color: var(--ink);
}

.turn.assistant p {
  background: var(--desk);
  color: var(--ink);
}

.composer {
  display: flex;
  align-items: flex-end;
  gap: var(--space-3);
  padding: var(--space-4);
  border-top: 1px solid var(--rule);
}

textarea {
  flex: 1;
  min-height: calc(var(--space-7) + var(--space-4));
  padding: var(--space-2) var(--space-3);
  resize: vertical;
  border: 1px solid var(--inset-border);
  border-radius: var(--radius-control);
  font: inherit;
  font-size: var(--text-sm);
  line-height: 1.5;
}

textarea:focus-visible,
.primary:focus-visible,
.ghost:focus-visible {
  outline: 2px solid var(--stamp);
  outline-offset: 2px;
}

.primary,
.ghost {
  height: var(--space-6);
  padding: 0 var(--space-3);
  border-radius: var(--radius-control);
  font-size: var(--text-sm);
  cursor: pointer;
}

.primary {
  border: 0;
  background: var(--stamp);
  color: var(--on-stamp);
}

.primary:hover:not(:disabled) {
  background: var(--stamp-press);
}

.primary:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.ghost {
  border: 1px solid var(--rule-strong);
  background: var(--sheet);
}

.sr {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

@media (max-width: 720px) {
  .composer {
    flex-direction: column;
    align-items: stretch;
  }

  .primary {
    align-self: flex-end;
  }
}
</style>
