import { ref } from "vue";

/** 顶栏面包屑用的当前项目名称（由项目页面在读取项目后写入，避免顶栏重复请求）。 */
export const projectCrumb = ref<{ id: string; name: string } | null>(null);

export function rememberProject(project: { id: string; name: string } | null | undefined) {
  if (project) projectCrumb.value = { id: project.id, name: project.name };
}
