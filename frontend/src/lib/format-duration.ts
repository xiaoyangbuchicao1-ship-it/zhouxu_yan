/** Round once to tenths, before splitting minutes, so 59.96s becomes 1:00.0. */
export function formatDuration(ms: number): string {
  const tenths = Math.max(0, Math.round(ms / 100));
  if (tenths < 600) return `${(tenths / 10).toFixed(1)} 秒`;
  return `${Math.floor(tenths / 600)} 分 ${((tenths % 600) / 10).toFixed(1).padStart(4, "0")} 秒`;
}
