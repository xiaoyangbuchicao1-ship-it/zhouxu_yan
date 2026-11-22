export type ThinkingMode = "none" | "low" | "medium" | "high" | "xhigh" | "max";

export const THINKING_MODES: readonly {
  value: ThinkingMode;
  label: string;
  description: string;
  note: string;
}[] = [
  { value: "none", label: "关闭", description: "不启用思考", note: "关闭" },
  { value: "low", label: "低", description: "较快思考", note: "低" },
  { value: "medium", label: "中", description: "平衡速度与深度", note: "中" },
  { value: "high", label: "高", description: "深入思考", note: "高" },
  { value: "xhigh", label: "很高", description: "更充分思考", note: "很高" },
  { value: "max", label: "最高", description: "最大思考强度", note: "最高" },
];

export function readThinkingMode(key: string): ThinkingMode {
  try {
    const saved = sessionStorage.getItem(key);
    return THINKING_MODES.find(mode => mode.value === saved)?.value ?? "none";
  } catch { return "none"; }
}
