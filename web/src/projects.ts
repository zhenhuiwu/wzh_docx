import { ref } from "vue";

export const projectStatuses = [
  { value: "not_started", label: "未开始" },
  { value: "active", label: "进行中" },
  { value: "paused", label: "已暂停" },
  { value: "completed", label: "已完成" },
] as const;

export type ProjectStatus = (typeof projectStatuses)[number]["value"];

export interface StoredProject {
  id: string;
  name: string;
  updatedAt: string;
}

const projects = ref<StoredProject[]>([]);

export function useProjects() {
  return {
    projects,
    createProject() {
      return null;
    },
    removeProject() {
      return undefined;
    },
    findProject() {
      return undefined;
    },
  };
}

export function formatDateTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function formatUpdatedAt(value: string) {
  return formatDateTime(value);
}

export function formatPeriod(start: string | null | undefined, end: string | null | undefined) {
  if (!start && !end) return "—";
  if (start && end) return `${start} 至 ${end}`;
  return start || end || "—";
}

export function displayText(value: string | null | undefined) {
  return value && value.trim() ? value : "—";
}

export function statusLabel(value: string) {
  return projectStatuses.find((item) => item.value === value)?.label ?? value;
}
