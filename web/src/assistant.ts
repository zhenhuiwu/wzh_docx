import { ref } from "vue";

export interface ChatTurn {
  id: string;
  role: "user" | "assistant";
  text: string;
}

const turns = ref<ChatTurn[]>([]);

export function useAssistant() {
  function ask(text: string) {
    const question = text.trim();
    if (!question) return;
    turns.value = [
      ...turns.value,
      { id: crypto.randomUUID(), role: "user", text: question },
      {
        id: crypto.randomUUID(),
        role: "assistant",
        text: "智能助手尚未接入，暂时不能回答。",
      },
    ];
  }

  function clear() {
    turns.value = [];
  }

  return { turns, ask, clear };
}
