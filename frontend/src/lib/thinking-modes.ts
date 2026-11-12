export type ThinkingMode = "none" | "low" | "high";

export const THINKING_MODES: readonly {
  value: ThinkingMode;
  label: string;
  description: string;
  note: string;
}[] = [
  { value: "none", label: "不思考", description: "直接回应，优先速度", note: "直接" },
  { value: "low", label: "轻度思考", description: "适度推敲，兼顾速度与分析", note: "适度" },
  { value: "high", label: "高度思考", description: "深入分析，通常需要更多时间", note: "深入" },
];

export function readThinkingMode(key: string): ThinkingMode {
  try {
    const saved = sessionStorage.getItem(key);
    return THINKING_MODES.find(mode => mode.value === saved)?.value ?? "none";
  } catch { return "none"; }
}
