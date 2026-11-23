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

export interface AdminAccountPage {
  accounts: AdminAccount[];
  query: string;
  status: "active" | "disabled" | null;
  role: AccountRole | null;
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export type AdminAuditCategory = "all" | "account" | "capability" | "model" | "conversation";
export type AdminAuditOutcome = "all" | "succeeded" | "failed";

export interface AdminAuditFilters {
  category?: AdminAuditCategory;
  outcome?: AdminAuditOutcome;
  query?: string;
  cursor?: string | null;
  limit?: number;
}

export interface AdminAuditEvent {
  id: string;
  action: string;
  actor_id: string;
  actor_email: string;
  target_account_id: string | null;
  target_email: string | null;
  resource_type?: string;
  resource_id?: string | null;
  outcome?: "succeeded" | "failed";
  details: Record<string, unknown>;
  created_at: string;
}

export interface AdminAuditPage {
  events: AdminAuditEvent[];
  category_counts?: Record<string, number> | null;
  next_cursor?: string | null;
}

export type ConversationMode = "chat" | "work";

export type ConversationStatusFilter = "all" | "succeeded" | "active" | "failed" | "no_runs";
export type TaskStatusFilter = "all" | "succeeded" | "active" | "failed";

export interface AdminConversationStatusCounts {
  all: number;
  succeeded: number;
  active: number;
  failed: number;
  no_runs: number;
}

export interface AdminConversationSummary {
  id: string;
  account_id: string;
  title: string;
  mode: ConversationMode;
  archived: boolean;
  run_count: number;
  latest_status: string | null;
  latest_run_at?: string | null;
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

export interface AdminRunMaterial {
  material_id: string;
  filename: string;
  media_type: string;
  size_bytes: number;
  status: string;
}

export interface AdminRunToolTrace {
  tool_name: string;
  tool_label: string;
  query?: string | null;
  result_count?: number | null;
  status: "succeeded" | "failed";
  summary?: string | null;
}

export interface AdminSourceDocument {
  source_id: string;
  kind?: "law" | "case" | "paper" | null;
  title: string;
  locator?: string | null;
  section?: string | null;
  version_date?: string | null;
  version_label?: string | null;
  authors?: string | null;
  collection?: string | null;
  text: string;
  source_url?: string | null;
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
  error_message?: string | null;
  created_at: string;
  first_event_at?: string | null;
  first_delta_at?: string | null;
  completed_at?: string | null;
  first_delta_ms?: number | null;
  elapsed_ms: number | null;
  current_unit?: string | null;
  context_compaction_count?: number;
  continuation_guidance?: string[];
  source_summary?: Record<string, number>;
  nodes: AdminConversationNode[];
  materials?: AdminRunMaterial[];
  tool_traces?: AdminRunToolTrace[];
  sources: AdminConversationSource[];
  artifacts: AdminConversationArtifact[];
}

export interface AdminConversationDetail {
  account?: AdminConversationAccount | null;
  conversation: Omit<AdminConversationSummary, "run_count" | "last_status">;
  runs: AdminConversationRun[];
  next_cursor: string | null;
}

export interface AdminConversationAccount {
  id: string;
  email: string;
  display_name: string;
}

export interface AdminConversationList {
  account?: AdminConversationAccount | null;
  conversations: AdminConversationSummary[];
  status_counts?: AdminConversationStatusCounts;
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
  mode_scope?: "chat" | "work" | null;
  status: "ready" | "coming_soon" | "unavailable" | "not_configured";
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
export type ModelFamily = "deepseek" | "gpt" | "claude" | "gemini" | "glm" | "qwen" | "kimi" | "other";
export type ThinkingMode = "none" | "low" | "medium" | "high" | "xhigh" | "max";
export type ConnectivityStatus = "unverified" | "verified" | "failed";

export interface AdminModelProvider {
  id: string;
  name: string;
  notes?: string;
  preset_type: string;
  default_protocol: ModelProtocol;
  auth_style: "bearer" | "x-api-key" | "x-goog-api-key" | "none";
  base_url: string;
  extra_headers?: Record<string, string>;
  credential_mode: "env_ref" | "encrypted_db" | "file_ref";
  credential_ref: string | null;
  api_key_hint?: string | null;
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
  protocol: ModelProtocol;
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
  connectivity_status: ConnectivityStatus;
  connectivity_checked_at: string | null;
  connectivity_latency_ms: number | null;
  connectivity_error: string | null;
  revision: number;
  updated_at: string;
}

export interface AdminModelDirectory {
  credential_encryption_ready?: boolean;
  local_hosts_allowed?: boolean;
  providers: AdminModelProvider[];
  models: AdminModelProfile[];
}

export interface AdminDiscoveredModel {
  id: string;
  family: ModelFamily;
  label: string;
  already_added: boolean;
}

export interface AdminProviderDiscoverResult {
  supported: boolean;
  models: AdminDiscoveredModel[];
  notice: string | null;
}

export interface AdminConnectivityTestResult {
  ok: boolean;
  connectivity_status: "verified" | "failed";
  latency_ms: number | null;
  http_status?: number | null;
  error_code: string | null;
}

export type AdminProviderCreate = {
  id?: string;
  name: string;
  notes?: string;
  preset_type?: string;
  default_protocol: ModelProtocol;
  auth_style?: "bearer" | "x-api-key" | "x-goog-api-key" | "none";
  base_url: string;
  extra_headers?: Record<string, string>;
  credential_mode?: "env_ref" | "encrypted_db";
  credential_ref?: string | null;
  api_key?: string;
  enabled?: boolean;
};
export type AdminProviderUpdate = Partial<Omit<AdminProviderCreate, "id" | "preset_type">>;
export type AdminModelCreate = {
  profile_id?: string;
  provider_id: string;
  upstream_model_id: string;
  label: string;
  family?: ModelFamily;
  protocol_override?: ModelProtocol | null;
  context_tokens: number;
  max_output_tokens: number;
  thinking_modes: ThinkingMode[];
  default_thinking: ThinkingMode;
  enabled?: boolean;
  access_scope?: string;
  is_default?: boolean;
  sort_order?: number;
  capabilities?: Record<string, string>;
  source?: string;
  checked_on?: string;
};
export type AdminModelUpdate = Partial<Pick<AdminModelProfile, "upstream_model_id" | "label" | "family" | "protocol_override" | "context_tokens" | "max_output_tokens" | "thinking_modes" | "default_thinking" | "enabled" | "access_scope" | "is_default" | "sort_order" | "capabilities">>;

export interface AdminConversationFilters {
  query?: string;
  mode?: ConversationMode | "all";
  archived?: "active" | "archived" | "all";
  status?: ConversationStatusFilter;
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
    model_connectivity_unverified: "请先测试模型连通性，通过后再启用或设为默认。",
    model_must_be_enabled_for_default: "需要先启用模型，才能设为默认。",
    credential_encryption_unconfigured: "部署环境尚未设置 ZHOUXU_ADMIN_CREDENTIAL_KEY，暂不能在网页加密保存密钥，请改用环境变量方式。",
    credential_master_key_invalid: "主密钥配置无效，请检查 ZHOUXU_ADMIN_CREDENTIAL_KEY。",
    credential_ref_forbidden: "该环境变量名属于系统内部配置，不能作为模型密钥引用。",
    credential_ref_required: "请填写密钥环境变量名。",
    credential_ref_invalid: "环境变量名只能包含字母、数字、点、冒号或下划线。",
    credential_missing: "未在部署环境中找到对应的模型密钥。",
    api_key_required: "请填写 API Key。",
    api_key_not_allowed_for_env_ref: "使用环境变量模式时不能提交明文密钥。",
    endpoint_https_required: "接口地址必须使用 https://（如需连本机模型，请先在服务端开启允许本机配置）。",
    endpoint_private_address_blocked: "接口地址指向内网或受保护网段，已被安全策略拦截。",
    endpoint_localhost_blocked: "当前未开启本机模型地址支持，暂不允许连接 localhost / 127.0.0.1。",
    endpoint_invalid: "接口地址格式不正确，请包含完整域名。",
    endpoint_required: "请填写接口地址。",
    provider_exists: "服务商编号已存在，请换一个编号。",
    profile_exists: "模型编号已存在，请换一个编号。",
    provider_id_invalid: "服务商编号格式不正确（仅限字母、数字及 . _ : -）。",
    profile_id_invalid: "模型编号格式不正确（仅限字母、数字及 . _ : -）。",
    capability_model_list_required: "选择“指定模型”时，至少要勾选 1 个模型。",
    model_extra_header_forbidden: "自定义请求头不能覆盖 Authorization / Host 等受保护字段。",
    extra_headers_invalid: "自定义请求头格式有误，请按“Header-Name: Value”逐行填写。",
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
    if (!provider.id || !provider.name || !provider.default_protocol || typeof provider.base_url !== "string" || typeof provider.revision !== "number") {
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
  if ("status" in filters && filters.status && filters.status !== "all") {
    query.set("status", filters.status);
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
    accounts: (filters: { query?: string; status?: "active" | "disabled" | "all"; role?: AccountRole | "all"; page?: number; pageSize?: number } = {}, signal?: AbortSignal) => {
      const query = new URLSearchParams();
      if (filters.query?.trim()) query.set("q", filters.query.trim());
      if (filters.status && filters.status !== "all") query.set("status", filters.status);
      if (filters.role && filters.role !== "all") query.set("role", filters.role);
      if (filters.page) query.set("page", String(filters.page));
      if (filters.pageSize) query.set("page_size", String(filters.pageSize));
      const suffix = query.toString() ? `?${query.toString()}` : "";
      return request<AdminAccountPage>(`/admin/accounts${suffix}`, token, {}, signal);
    },
    audit: async (filtersOrSignal?: AdminAuditFilters | AbortSignal, signalArg?: AbortSignal): Promise<AdminAuditPage> => {
      const isSignal = (v: unknown): v is AbortSignal =>
        Boolean(v && typeof AbortSignal !== "undefined" && v instanceof AbortSignal);
      const filters: AdminAuditFilters = isSignal(filtersOrSignal) ? {} : (filtersOrSignal ?? {});
      const signal = isSignal(filtersOrSignal) ? filtersOrSignal : signalArg;
      const query = new URLSearchParams();
      if (filters.category && filters.category !== "all") query.set("category", filters.category);
      if (filters.outcome && filters.outcome !== "all") query.set("outcome", filters.outcome);
      if (filters.query?.trim()) query.set("q", filters.query.trim());
      if (filters.cursor) query.set("cursor", filters.cursor);
      query.set("limit", String(filters.limit ?? 80));
      const result = await request<AdminAuditPage>(`/admin/audit?${query.toString()}`, token, {}, signal);
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
    deleteProvider: (id: string) =>
      request<{ archived: boolean; provider_id: string }>(`/admin/model-providers/${encodeURIComponent(id)}`, token, { method: "DELETE" }),
    discoverProviderModels: (id: string) =>
      request<AdminProviderDiscoverResult>(`/admin/model-providers/${encodeURIComponent(id)}/discover`, token, { method: "POST" }),
    testProvider: (id: string, modelId?: string) =>
      request<AdminConnectivityTestResult>(`/admin/model-providers/${encodeURIComponent(id)}/test`, token, { method: "POST", body: JSON.stringify(modelId ? { model_id: modelId } : {}) }),
    createModel: (value: AdminModelCreate) =>
      request<AdminModelProfile>("/admin/models", token, { method: "POST", body: JSON.stringify(value) }),
    updateModel: (id: string, value: AdminModelUpdate) =>
      request<AdminModelProfile>(`/admin/models/${encodeURIComponent(id)}`, token, { method: "PATCH", body: JSON.stringify(value) }),
    deleteModel: (id: string) =>
      request<{ archived: boolean; profile_id: string }>(`/admin/models/${encodeURIComponent(id)}`, token, { method: "DELETE" }),
    testModel: (id: string) =>
      request<AdminConnectivityTestResult>(`/admin/models/${encodeURIComponent(id)}/test`, token, { method: "POST" }),
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
    sourceDocument: async (sourceId: string, signal?: AbortSignal) => {
      const value = await request<AdminSourceDocument>(`/admin/sources/${encodeURIComponent(sourceId)}`, token, {}, signal);
      rejectPrivateFields(value);
      return value;
    },
    capabilities: (signal?: AbortSignal) => request<{ accounts: AdminCapability[] }>("/admin/capabilities", token, {}, signal),
    updateCapability: (accountId: string, update: Partial<Pick<AdminCapability, "model_policy" | "allowed_models" | "skill_policy" | "can_upload_skill">>) =>
      request<AdminCapability>(`/admin/capabilities/${encodeURIComponent(accountId)}`, token, { method: "PATCH", body: JSON.stringify(update) }),
    skills: (signal?: AbortSignal) => request<{ status: "ready" | "degraded" | "unavailable"; total: number; skills: AdminSkillStatus[] }>("/admin/skills", token, {}, signal),
    resourcesHealth: (signal?: AbortSignal) => request<{ status: "ready" | "degraded" | "unavailable"; sources: Record<string, AdminHealthItem> }>("/admin/resources/health", token, {}, signal),
    tasks: (limit = 50, signal?: AbortSignal, status?: TaskStatusFilter) => {
      const query = new URLSearchParams({ limit: String(limit) });
      if (status && status !== "all") query.set("status", status);
      return request<{ tasks: AdminTask[]; next_cursor: string | null }>(`/admin/tasks?${query.toString()}`, token, {}, signal);
    },
    runtimeHealth: (signal?: AbortSignal) => request<{ status: "ready" | "degraded" | "unavailable"; components: Record<string, AdminHealthItem> }>("/admin/runtime/health", token, {}, signal),
  };
}

export async function loadAdminDashboard(token: string, signal?: AbortSignal): Promise<AdminDashboardData> {
  const api = createAdminApi(token);
  const [overview, accounts, audit] = await Promise.all([api.overview(signal), api.accounts({}, signal), api.audit(signal)]);
  return { overview: overview.overview, accounts: accounts.accounts, audit: audit.events };
}
