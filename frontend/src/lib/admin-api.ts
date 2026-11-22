import type { AccountRole } from "./auth";

export interface AdminAccount {
  id: string;
  email: string;
  display_name: string;
  status: "active" | "disabled";
  role: AccountRole;
  created_at: string;
  updated_at: string;
}

export interface AdminOverview {
  total_accounts: number;
  active_accounts: number;
  disabled_accounts: number;
  admin_accounts: number;
  audit_events: number;
}

export type AdminAccountUpdate = Partial<Pick<AdminAccount, "status" | "role">>;

export interface AdminDashboardData {
  overview: AdminOverview;
  accounts: AdminAccount[];
  audit: AdminAuditEvent[];
}

export interface AdminAuditEvent {
  id: string;
  action: string;
  actor_id: string;
  actor_email: string;
  target_account_id: string | null;
  target_email: string | null;
  details: Record<string, unknown>;
  created_at: string;
}

export type ConversationMode = "chat" | "work";

export interface AdminConversationSummary {
  id: string;
  account_id: string;
  title: string;
  mode: ConversationMode;
  archived: boolean;
  run_count: number;
  latest_status: string | null;
  created_at: string;
  updated_at: string;
}

export interface AdminConversationNode {
  id: string;
  label: string;
  status: string;
  elapsed_ms?: number | null;
}

export interface AdminConversationSource {
  source_id: string;
  kind: "law" | "case" | "paper" | null;
  title: string;
  locator?: string | null;
  section?: string | null;
  version_date?: string | null;
  version_label?: string | null;
  authors?: string | null;
  collection?: string | null;
}

export interface AdminConversationArtifact {
  artifact_id: string;
  name: string;
  media_type: string;
  size_bytes: number;
  sha256: string;
  version: number;
  kind: "document" | "preview" | "file";
  preview_artifact_id?: string | null;
}

export interface AdminConversationRun {
  run_id: string;
  user_question: string;
  public_answer: string | null;
  status: string;
  model: string | null;
  provider: string | null;
  protocol: string | null;
  thinking_mode: string | null;
  error_code: string | null;
  created_at: string;
  first_event_at?: string | null;
  first_delta_at?: string | null;
  completed_at?: string | null;
  elapsed_ms: number | null;
  nodes: AdminConversationNode[];
  sources: AdminConversationSource[];
  artifacts: AdminConversationArtifact[];
}

export interface AdminConversationDetail {
  conversation: Omit<AdminConversationSummary, "run_count" | "last_status">;
  runs: AdminConversationRun[];
  next_cursor: string | null;
}

export interface AdminConversationList {
  conversations: AdminConversationSummary[];
  next_cursor: string | null;
}

export interface AdminCapability {
  account_id: string;
  email: string;
  display_name: string;
  model_policy: "default" | "allowlist" | "disabled";
  allowed_models: string[];
  skill_policy: "default" | "builtin_only" | "disabled";
  can_upload_skill: boolean;
  updated_at: string | null;
}

export interface AdminSkillStatus {
  id: string;
  name: string;
  summary: string;
  version: string;
  visibility: string;
  status: "ready" | "unavailable" | "not_configured";
  owner_email: string | null;
  updated_at: string | null;
}

export interface AdminTask {
  run_id: string;
  account_id: string;
  account_email: string;
  conversation_id: string;
  question: string;
  status: string;
  model: string | null;
  provider: string | null;
  created_at: string;
  completed_at: string | null;
  elapsed_ms: number | null;
  answer_chars: number;
}

export interface AdminHealthItem {
  status: "ready" | "degraded" | "unavailable" | "not_configured";
  count?: number;
  reason?: string;
}

export type ModelProtocol = "deepseek_chat_completions" | "openai_chat_completions" | "openai_responses" | "anthropic_messages" | "google_generative_language";
export type ModelFamily = "deepseek" | "gpt" | "claude" | "gemini" | "glm" | "other";
export type ThinkingMode = "none" | "low" | "medium" | "high" | "xhigh" | "max";

export interface AdminModelProvider {
  id: string;
  name: string;
  preset_type: string;
  default_protocol: ModelProtocol;
  auth_style: "bearer" | "x-api-key" | "x-goog-api-key" | "none";
  base_url: string;
  credential_mode: "env_ref" | "file_ref";
  credential_ref: string | null;
  credential_configured: boolean;
  enabled: boolean;
  revision: number;
  updated_at: string;
}

export interface AdminModelProfile {
  profile_id: string;
  provider_id: string;
  upstream_model_id: string;
  label: string;
  family: ModelFamily;
  protocol_override: ModelProtocol | null;
  context_tokens: number;
  max_output_tokens: number;
  thinking_modes: ThinkingMode[];
  default_thinking: ThinkingMode;
  capabilities: Record<string, string>;
  enabled: boolean;
  access_scope: string;
  is_default: boolean;
  sort_order: number;
  source: string;
  checked_on: string | null;
  revision: number;
  updated_at: string;
}

export interface AdminModelDirectory {
  providers: AdminModelProvider[];
  models: AdminModelProfile[];
}

export type AdminProviderCreate = Omit<AdminModelProvider, "credential_configured" | "revision" | "updated_at"> & { extra_headers?: Record<string, string> };
export type AdminProviderUpdate = Partial<Omit<AdminProviderCreate, "id" | "preset_type">>;
export type AdminModelCreate = Omit<AdminModelProfile, "revision" | "updated_at" | "capabilities" | "source" | "checked_on"> & { capabilities?: Record<string, string>; source?: string; checked_on?: string };
export type AdminModelUpdate = Partial<Pick<AdminModelProfile, "upstream_model_id" | "label" | "family" | "protocol_override" | "context_tokens" | "max_output_tokens" | "thinking_modes" | "default_thinking" | "enabled" | "access_scope" | "sort_order" | "capabilities">>;

export interface AdminConversationFilters {
  query?: string;
  mode?: ConversationMode | "all";
  archived?: "active" | "archived" | "all";
  cursor?: string | null;
  limit?: number;
}

export interface AdminConversationDetailFilters {
  cursor?: string | null;
  limit?: number;
}

export class AdminApiError extends Error {
  constructor(public code: string, public status = 0) {
    super(adminErrorMessage(code));
    this.name = "AdminApiError";
  }
}

export function adminErrorMessage(code: string): string {
  const messages: Record<string, string> = {
    admin_required: "这个入口只对管理员开放。",
    cannot_change_self: "不能修改自己的管理员权限。",
    last_admin_required: "至少要保留一位管理员。",
    authentication_required: "登录状态已失效，请重新登录。",
    invalid_response: "管理台数据不完整，请重新加载。",
    account_not_found: "这个账户已不存在，请刷新列表。",
    unavailable: "管理员服务暂时不可用，请稍后重试。",
    timeout: "连接超时，请重试。",
  };
  return messages[code] ?? "这次操作未能完成，请稍后重试。";
}

const runtimeEnv: Record<string, string | undefined> =
  (import.meta as ImportMeta & { env?: Record<string, string> }).env ?? {};
const apiBase = (runtimeEnv.VITE_API_BASE_URL || "/api").replace(/\/$/, "");

const forbiddenAdminFields = new Set([
  "attachments",
  "attachment_content",
  "disk_path",
  "storage_key",
  "payload_json",
  "system_prompt",
  "hidden_reasoning",
  "raw_frames",
  "tool_receipts",
  "stack",
  "secret",
  "api_key",
  "api_key_encrypted",
  "credential_value",
  "skill_source",
]);

function rejectPrivateFields(value: unknown): void {
  if (Array.isArray(value)) {
    value.forEach(rejectPrivateFields);
    return;
  }
  if (!value || typeof value !== "object") return;
  for (const [key, child] of Object.entries(value)) {
    if (forbiddenAdminFields.has(key)) throw new AdminApiError("invalid_response");
    rejectPrivateFields(child);
  }
}

function conversationList(value: unknown): AdminConversationList {
  rejectPrivateFields(value);
  if (!value || typeof value !== "object" || !Array.isArray((value as AdminConversationList).conversations)) {
    throw new AdminApiError("invalid_response");
  }
  for (const item of (value as AdminConversationList).conversations) {
    const fields = item as AdminConversationSummary & Record<string, unknown>;
    if (!fields.id || !fields.account_id || !fields.title || !fields.mode || typeof fields.run_count !== "number") {
      throw new AdminApiError("invalid_response");
    }
    if (["question", "user_question", "answer", "public_answer", "partial_answer", "runs"].some(key => key in fields)) {
      throw new AdminApiError("invalid_response");
    }
  }
  return value as AdminConversationList;
}

function conversationDetail(value: unknown): AdminConversationDetail {
  rejectPrivateFields(value);
  if (!value || typeof value !== "object") throw new AdminApiError("invalid_response");
  const result = value as AdminConversationDetail;
  if (!result.conversation?.id || !result.conversation.account_id || !Array.isArray(result.runs)) {
    throw new AdminApiError("invalid_response");
  }
  for (const run of result.runs) {
    if (!run.run_id || typeof run.user_question !== "string" || !run.status
      || !Array.isArray(run.nodes) || !Array.isArray(run.sources) || !Array.isArray(run.artifacts)) {
      throw new AdminApiError("invalid_response");
    }
  }
  return result;
}

function modelDirectory(value: unknown): AdminModelDirectory {
  rejectPrivateFields(value);
  if (!value || typeof value !== "object") throw new AdminApiError("invalid_response");
  const result = value as AdminModelDirectory;
  if (!Array.isArray(result.providers) || !Array.isArray(result.models)) throw new AdminApiError("invalid_response");
  for (const provider of result.providers) {
    if (!provider.id || !provider.name || !provider.default_protocol || !provider.base_url || typeof provider.revision !== "number") {
      throw new AdminApiError("invalid_response");
    }
  }
  for (const model of result.models) {
    if (!model.profile_id || !model.provider_id || !model.upstream_model_id || !Array.isArray(model.thinking_modes)) {
      throw new AdminApiError("invalid_response");
    }
  }
  return result;
}

function conversationQuery(filters: AdminConversationFilters | AdminConversationDetailFilters): string {
  const query = new URLSearchParams();
  if ("query" in filters && filters.query?.trim()) query.set("query", filters.query.trim());
  if ("mode" in filters && filters.mode && filters.mode !== "all") query.set("mode", filters.mode);
  if ("archived" in filters && filters.archived && filters.archived !== "all") {
    query.set("archived", filters.archived === "archived" ? "true" : "false");
  }
  if (filters.cursor) query.set("cursor", filters.cursor);
  if (filters.limit) query.set("limit", String(filters.limit));
  const value = query.toString();
  return value ? `?${value}` : "";
}

async function request<T>(path: string, token: string, options: RequestInit = {}, signal?: AbortSignal): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (signal?.aborted) controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  let timedOut = false;
  const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, 15000);
  try {
    const response = await fetch(`${apiBase}${path}`, {
      ...options,
      signal: controller.signal,
      cache: "no-store",
      credentials: "omit",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}`, ...options.headers },
    });
    if (response.status === 204) return undefined as T;
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      const code = typeof data?.detail === "string" ? data.detail : response.status >= 500 ? "unavailable" : "request_failed";
      throw new AdminApiError(code, response.status);
    }
    if (data === null) throw new AdminApiError("invalid_response");
    return data as T;
  } catch (error) {
    if (signal?.aborted) throw error;
    if (error instanceof AdminApiError) throw error;
    throw new AdminApiError(timedOut ? "timeout" : "unavailable");
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener("abort", abort);
  }
}

export function createAdminApi(token: string) {
  return {
    overview: (signal?: AbortSignal) => request<{ overview: AdminOverview }>("/admin/overview", token, {}, signal),
    accounts: (signal?: AbortSignal) => request<{ accounts: AdminAccount[] }>("/admin/accounts", token, {}, signal),
    audit: async (signal?: AbortSignal) => {
      const result = await request<{ events: AdminAuditEvent[] }>("/admin/audit?limit=80", token, {}, signal);
      for (const event of result.events) {
        const values = event.action === "account.status_changed" ? ["active", "disabled"]
          : event.action === "account.role_changed" ? ["user", "admin"] : null;
        if (values && (!values.includes(String(event.details.from)) || !values.includes(String(event.details.to)))) {
          throw new AdminApiError("invalid_response");
        }
      }
      return result;
    },
    updateAccount: (id: string, update: AdminAccountUpdate) =>
      request<AdminAccount>(`/admin/accounts/${encodeURIComponent(id)}`, token, { method: "PATCH", body: JSON.stringify(update) }),
    modelDirectory: async (signal?: AbortSignal) => modelDirectory(
      await request<unknown>("/admin/model-directory", token, {}, signal),
    ),
    createProvider: (value: AdminProviderCreate) =>
      request<AdminModelProvider>("/admin/model-providers", token, { method: "POST", body: JSON.stringify(value) }),
    updateProvider: (id: string, value: AdminProviderUpdate) =>
      request<AdminModelProvider>(`/admin/model-providers/${encodeURIComponent(id)}`, token, { method: "PATCH", body: JSON.stringify(value) }),
    createModel: (value: AdminModelCreate) =>
      request<AdminModelProfile>("/admin/models", token, { method: "POST", body: JSON.stringify(value) }),
    updateModel: (id: string, value: AdminModelUpdate) =>
      request<AdminModelProfile>(`/admin/models/${encodeURIComponent(id)}`, token, { method: "PATCH", body: JSON.stringify(value) }),
    accountConversations: async (accountId: string, filters: AdminConversationFilters = {}) =>
      conversationList(await request<unknown>(
        `/admin/accounts/${encodeURIComponent(accountId)}/conversations${conversationQuery(filters)}`,
        token,
      )),
    conversationDetail: async (
      accountId: string,
      conversationId: string,
      filters: AdminConversationDetailFilters = {},
    ) => conversationDetail(await request<unknown>(
      `/admin/accounts/${encodeURIComponent(accountId)}/conversations/${encodeURIComponent(conversationId)}${conversationQuery(filters)}`,
      token,
    )),
    capabilities: (signal?: AbortSignal) => request<{ accounts: AdminCapability[] }>("/admin/capabilities", token, {}, signal),
    updateCapability: (accountId: string, update: Partial<Pick<AdminCapability, "model_policy" | "allowed_models" | "skill_policy" | "can_upload_skill">>) =>
      request<AdminCapability>(`/admin/capabilities/${encodeURIComponent(accountId)}`, token, { method: "PATCH", body: JSON.stringify(update) }),
    skills: (signal?: AbortSignal) => request<{ status: "ready" | "degraded" | "unavailable"; total: number; skills: AdminSkillStatus[] }>("/admin/skills", token, {}, signal),
    resourcesHealth: (signal?: AbortSignal) => request<{ status: "ready" | "degraded" | "unavailable"; sources: Record<string, AdminHealthItem> }>("/admin/resources/health", token, {}, signal),
    tasks: (limit = 50, signal?: AbortSignal) => request<{ tasks: AdminTask[]; next_cursor: string | null }>(`/admin/tasks?limit=${limit}`, token, {}, signal),
    runtimeHealth: (signal?: AbortSignal) => request<{ status: "ready" | "degraded" | "unavailable"; components: Record<string, AdminHealthItem> }>("/admin/runtime/health", token, {}, signal),
  };
}

export async function loadAdminDashboard(token: string, signal?: AbortSignal): Promise<AdminDashboardData> {
  const api = createAdminApi(token);
  const [overview, accounts, audit] = await Promise.all([api.overview(signal), api.accounts(signal), api.audit(signal)]);
  return { overview: overview.overview, accounts: accounts.accounts, audit: audit.events };
}
