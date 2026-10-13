/** 滚动位置是否已到文末（容差 2px）。 */
export function atScrollEnd(scrollTop: number, clientHeight: number, scrollHeight: number): boolean {
  return scrollHeight > clientHeight && scrollTop + clientHeight >= scrollHeight - 2;
}

/**
 * QA 复验 10：没跳转过（文末没补留白）时滚到底，视口顶边只到倒数第二三节；已到文末就显示最后一节。
 * 刚跳转到靠后某节（文末补了留白、恰好也到底）时仍以跳转目标为准；用户自己滚过（jumped 为 null）才按文末算。
 */
export function viewHeadingAt(found: number | null, count: number, jumped: number | null, atEnd: boolean): number | null {
  if (count && jumped === null && atEnd) return count - 1;
  return found === null && count ? 0 : found;
}
