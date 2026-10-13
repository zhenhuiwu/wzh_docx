/** 界面上的时间显示：今年内 “10-10 14:32”，跨年 “2025-10-10 14:32”。无效值原样返回。 */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const pad = (n: number) => String(n).padStart(2, "0");
  const md = `${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  const hm = `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  return date.getFullYear() === new Date().getFullYear() ? `${md} ${hm}` : `${date.getFullYear()}-${md} ${hm}`;
}

/** 头像里显示的单字：取姓名第一个字。 */
export function initialOf(name: string | null | undefined): string {
  return Array.from((name || "").trim())[0] ?? "?";
}
