import { authErrorMessage, resolveAuthApiBase, type AuthAccount } from "./auth";

export type ThinkingModeValue = "none" | "low" | "medium" | "high" | "xhigh" | "max";

export interface UserPreferences {
  default_mode: "chat" | "work";
  default_thinking_mode: ThinkingModeValue;
  concise_process: boolean;
  compact_density: boolean;
  reduced_motion: boolean;
  memory_enabled: boolean;
  memory_text: string;
  updated_at?: string | null;
}

export interface UserPromptCommand {
  id: string;
  trigger: string;
  title: string;
  content: string;
  mode_hint?: "chat" | "work" | null;
  is_builtin: boolean;
  created_at: string;
  updated_at: string;
}

export interface AccountSummary {
  account: AuthAccount;
  capabilities: {
    model_policy: "default" | "allowlist" | "disabled";
    allowed_model_labels: string[];
    skill_policy: "default" | "builtin_only" | "disabled";
    can_upload_skill: boolean;
  };
  usage: {
    project_count: number;
    conversation_count: number;
    material_count: number;
    custom_skill_count: number;
  };
}

const DEFAULT_PREFERENCES: UserPreferences = {
  default_mode: "chat",
  default_thinking_mode: "none",
  concise_process: false,
  compact_density: false,
  reduced_motion: false,
  memory_enabled: false,
  memory_text: "",
  updated_at: null,
};

export function defaultUserPreferences(): UserPreferences {
  return { ...DEFAULT_PREFERENCES };
}

async function accountRequest<T>(
  path: string,
  token: string,
  options: RequestInit = {},
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(`${resolveAuthApiBase()}${path}`, {
    ...options,
    signal,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    },
  });
  if (response.status === 204) return undefined as T;
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const code = payload && typeof payload === "object" && "detail" in payload ? String(payload.detail) : "unknown";
    throw new Error(accountErrorMessage(code));
  }
  return payload as T;
}

function accountErrorMessage(code: string): string {
  const map: Record<string, string> = {
    authentication_failed: "原密码不正确，请核对后再试。",
    password_length_invalid: "新密码至少需要 12 位字符。",
    display_name_too_long: "称呼不能超过 80 个字符。",
    command_trigger_invalid: "指令代号只能使用小写字母、数字、- 或 _（1-32位）。",
    command_trigger_exists: "这个指令代号已经存在了，请换一个名字。",
    command_title_invalid: "请填写 80 字以内的指令标题。",
    command_content_invalid: "指令正文不能为空，且不超过 4000 字。",
    memory_text_too_long: "个人记忆内容不能超过 1200 字。",
  };
  return map[code] ?? authErrorMessage(code);
}

export function createAccountSettingsApi(token: string) {
  return {
    getPreferences: (signal?: AbortSignal) =>
      accountRequest<UserPreferences>("/account/preferences", token, {}, signal),
    updatePreferences: (patch: Partial<UserPreferences>) =>
      accountRequest<UserPreferences>("/account/preferences", token, {
        method: "PATCH",
        body: JSON.stringify(patch),
      }),
    updateProfile: (displayName: string) =>
      accountRequest<AuthAccount>("/account/profile", token, {
        method: "PATCH",
        body: JSON.stringify({ display_name: displayName }),
      }),
    changePassword: (currentPassword: string, newPassword: string) =>
      accountRequest<{ status: string }>("/account/change-password", token, {
        method: "POST",
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      }),
    listCommands: (signal?: AbortSignal) =>
      accountRequest<{ commands: UserPromptCommand[] }>("/account/commands", token, {}, signal),
    createCommand: (input: {
      trigger: string;
      title: string;
      content: string;
      mode_hint?: "chat" | "work" | null;
    }) =>
      accountRequest<UserPromptCommand>("/account/commands", token, {
        method: "POST",
        body: JSON.stringify(input),
      }),
    updateCommand: (
      id: string,
      patch: Partial<{ trigger: string; title: string; content: string; mode_hint: "chat" | "work" | null }>,
    ) =>
      accountRequest<UserPromptCommand>(`/account/commands/${encodeURIComponent(id)}`, token, {
        method: "PATCH",
        body: JSON.stringify(patch),
      }),
    deleteCommand: (id: string) =>
      accountRequest<void>(`/account/commands/${encodeURIComponent(id)}`, token, { method: "DELETE" }),
    summary: (signal?: AbortSignal) =>
      accountRequest<AccountSummary>("/account/summary", token, {}, signal),
  };
}
