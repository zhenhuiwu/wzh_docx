import { ref } from "vue";

/** 顶栏面包屑用的当前项目名称（由项目页面在读取项目后写入，避免顶栏重复请求）。 */
export const projectCrumb = ref<{ id: string; name: string } | null>(null);

export function rememberProject(project: { id: string; name: string } | null | undefined) {
  if (project) projectCrumb.value = { id: project.id, name: project.name };
}

/**
 * 面包屑最后一级的对象名（08 验收 S4）：详情页读到对象后写入，key 形如 "template:<id>"、"knowledge:<id>"、
 * "document:<id>"、"task:<id>"；AppShell 按当前路由算出 key，对得上才显示对象名，否则显示泛称。
 */
export const objectCrumb = ref<{ key: string; label: string } | null>(null);

export function rememberCrumb(key: string, label: string | null | undefined) {
  if (label) objectCrumb.value = { key, label };
}
