import type { ThinkingMode } from "@/lib/admin-api";

export interface InferredModelSpec {
  context_tokens: number;
  max_output_tokens: number;
  thinking_modes: ThinkingMode[];
  default_thinking: ThinkingMode;
  matched: boolean;
  source_hint: string;
  supports_vision?: boolean;
}

const VISION_PATTERNS: RegExp[] = [
  /deepseek[-_]?flash/i,
  /(?:^|\/)gpt-(?:4o|4\.1|4\.5|5|6)/i,
  /(?:^|\/)o(?:1|3|4)(?:[-.]|$)/i,
  /claude[-._]/i,
  /gemini[-._]/i,
  /glm-(?:4v|5)/i,
  /(?:qwen.*vl|vision|[-_]vl(?:[-.]|$))/i,
];

const NON_VISION_PATTERNS: RegExp[] = [
  /deepseek.*(?:v4-pro|pro|r1|v3|chat|coder|reasoner)/i,
  /(?:embedding|rerank|whisper|tts|audio)/i,
];

export function inferVisionSupportLocal(modelId: string): boolean {
  const raw = modelId.trim().toLowerCase();
  if (!raw) return false;
  const tail = raw.includes("/") ? raw.slice(raw.lastIndexOf("/") + 1) : raw;
  if (NON_VISION_PATTERNS.some(p => p.test(tail) || p.test(raw))) return false;
  return VISION_PATTERNS.some(p => p.test(tail) || p.test(raw));
}

const RULES: Array<[RegExp, number, number, ThinkingMode[], ThinkingMode, string]> = [
  [/deepseek.*(?:v4|flash)/i, 1_000_000, 393_216, ["none", "low", "high", "max"], "none", "DeepSeek V4"],
  [/deepseek.*(?:r1|reasoner|thinking)/i, 131_072, 32_768, ["none", "low", "medium", "high"], "high", "DeepSeek 推理"],
  [/deepseek.*v3/i, 131_072, 16_384, ["none", "low", "high"], "none", "DeepSeek V3"],
  [/deepseek/i, 131_072, 16_384, ["none"], "none", "DeepSeek"],
  [/(?:^|\/)gpt-(?:5|6)/i, 400_000, 128_000, ["none", "low", "medium", "high"], "medium", "OpenAI GPT 推理"],
  [/(?:^|\/)o(?:1|3|4)(?:[-.]|$)/i, 200_000, 100_000, ["low", "medium", "high"], "medium", "OpenAI o 系列推理"],
  [/(?:^|\/)gpt-4\.1/i, 1_048_576, 32_768, ["none"], "none", "OpenAI GPT-4.1"],
  [/(?:^|\/)gpt-4o/i, 128_000, 16_384, ["none"], "none", "OpenAI GPT-4o"],
  [/claude.*(?:4(?:[-.]|$)|3[.-]7|sonnet-4|opus-4|haiku-4)/i, 200_000, 64_000, ["none", "low", "medium", "high"], "none", "Claude 思考系列"],
  [/claude/i, 200_000, 8_192, ["none"], "none", "Claude"],
  [/gemini-(?:2\.5|3|4)/i, 1_048_576, 65_536, ["none", "low", "medium", "high"], "none", "Gemini 思考系列"],
  [/gemini-/i, 1_048_576, 8_192, ["none"], "none", "Gemini"],
  [/(?:qwq|qwen3|qwen.*thinking)/i, 131_072, 32_768, ["none", "low", "medium", "high"], "none", "Qwen 思考系列"],
  [/qwen/i, 131_072, 16_384, ["none"], "none", "Qwen"],
  [/glm-(?:4\.[5-9]|5|z1)/i, 131_072, 32_768, ["none", "low", "high"], "none", "GLM 思考系列"],
  [/glm-/i, 131_072, 16_384, ["none"], "none", "GLM"],
  [/(?:kimi.*thinking|k2-thinking)/i, 131_072, 32_768, ["none", "low", "high"], "high", "Kimi 思考系列"],
  [/(?:kimi|moonshot)/i, 131_072, 16_384, ["none"], "none", "Kimi"],
  [/(?:thinking|reasoner|-r1(?:[-.]|$))/i, 131_072, 32_768, ["none", "low", "high"], "none", "通用思考模型"],
];

export const SAFE_MODEL_DEFAULTS: InferredModelSpec = {
  context_tokens: 131_072,
  max_output_tokens: 16_384,
  thinking_modes: ["none"],
  default_thinking: "none",
  matched: false,
  source_hint: "通用默认值",
  supports_vision: false,
};

export function inferModelSpecLocal(modelId: string): InferredModelSpec {
  const raw = modelId.trim();
  if (!raw) return SAFE_MODEL_DEFAULTS;
  const lowered = raw.toLowerCase();
  const tail = lowered.includes("/") ? lowered.slice(lowered.lastIndexOf("/") + 1) : lowered;
  const supports_vision = inferVisionSupportLocal(raw);
  for (const [pattern, context_tokens, max_output_tokens, thinking_modes, default_thinking, source_hint] of RULES) {
    if (pattern.test(tail) || pattern.test(lowered)) {
      return {
        context_tokens,
        max_output_tokens,
        thinking_modes: [...thinking_modes],
        default_thinking,
        matched: true,
        source_hint,
        supports_vision,
      };
    }
  }
  return { ...SAFE_MODEL_DEFAULTS, supports_vision };
}
