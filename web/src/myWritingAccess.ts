import { ref } from "vue";
import { apiRequest } from "./api/client";

/** Q-9：只读成员不显示「我的编写」。null = 还没读到（先不显示，避免一闪而过）。界面显隐只是体验，数据由服务端按负责/分工过滤。 */
export const myWritingVisible = ref<boolean | null>(null);

export async function refreshMyWritingAccess(): Promise<boolean | null> {
  try {
    const body = await apiRequest<{ data: { visible: boolean } }>("/my-writing/access");
    myWritingVisible.value = Boolean(body?.data?.visible);
  } catch {
    // 读不到时保持原状，不因网络抖动隐藏入口
  }
  return myWritingVisible.value;
}
