import type { AccountRole } from "./auth";

export interface AdminAccount {
  id: string;
  email: string;
  display_name: string;
  status: "active" | "disabled";
  role: AccountRole;
  register_ip?: string | null;
  created_at: string;
  updated_at: string;
}

export interface AdminOverview {
  total_accounts: number;
  active_accounts: number;
  disabled_accounts: number;
  admin_accounts: number;
  audit_events: number;
  tasks_24h: number;
  failed_24h: number;
  active_tasks: number;
  recent_failed: AdminTask[];
  stuck_tasks: AdminTask[];
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

export type AdminAuditCategory = "all" | "operations" | "account" | "capability" | "model" | "conversation" | "skill" | "auth";
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
  material_quota_mb?: number | null;
  material_used_bytes?: number;
  updated_at: string | null;
}

export interface AdminSkillStatus {
  id: string;
  name: string;
  summary: string;
  version: string;
  visibility: string;
  mode_scope?: "chat" | "work" | null;
  status: "ready" | "unavailable" | "not_configured";
  owner_email: string | null;
  updated_at: string | null;
}

export interface AdminUploadedSkill {
  id: string;
  name: string;
  summary: string;
  version: number;
  owner_id: string;
  owner_email: string | null;
  active: boolean;
  file_count: number;
  size_bytes: number;
  created_at: string;
  updated_at: string;
}

export interface AdminTool {
  name: string;
  summary: string;
  kind: string;
  read_only: boolean;
  chat: boolean;
  work: boolean;
  source: string;
  source_label: string;
  status: "ready" | "degraded" | "unavailable" | "not_configured";
  status_reason: string | null;
  parameters: Array<{ name: string; required: boolean }>;
  calls: number;
  errors: number;
  empty: number;
  avg_ms: number | null;
  last_call_at: number | null;
  last_error_code: string | null;
}

export interface AdminToolsResponse {
  window_days: number;
  recording: boolean;
  summary: { total: number; chat: number; work: number; calls: number; errors: number };
  tools: AdminTool[];
}

export interface AdminToolCall {
  ts: number;
  outcome: "ok" | "partial" | "empty" | "error";
  duration_ms: number;
  mode: string | null;
  account_id: string | null;
  account_email: string | null;
  conversation_id: string | null;
  run_id: string | null;
  error_code: string | null;
}

export interface AdminHostStorage {
  total_bytes: number;
  used_bytes: number;
  free_bytes: number;
  status: "ok" | "warn" | "critical";
}

export interface AdminCleanupBucket {
  key: string;
  label: string;
  bytes: number;
  items: number;
}

export interface AdminCleanupPreview {
  reclaimable_bytes: number;
  reclaimable_items: number;
  last_cleanup_at: number | null;
  last_cleanup_bytes: number;
  breakdown: AdminCleanupBucket[];
}

export interface AdminCleanupResult {
  cleaned_bytes: number;
  cleaned_dirs: number;
  cleaned_rows: number;
}

export interface AdminBackupItem {
  name: string;
  size_bytes: number;
  modified_at: number;
}

export interface AdminElasticStorage {
  policy: "normal" | "tightened_warn" | "paused_critical";
  base_quota_mb: number;
  effective_default_quota_mb: number;
  uploads_paused: boolean;
}

export interface AdminSystemMetrics {
  collected_at: string;
  compute: {
    cpu_cores: number;
    cpu_percent: number | null;
    load_1m: number | null;
    memory_used_bytes: number | null;
    memory_limit_bytes: number | null;
    memory_percent: number | null;
    process_rss_bytes: number | null;
    host_memory_total_bytes: number | null;
    host_memory_available_bytes: number | null;
    in_container: boolean;
  };
  opensearch: {
    status: "ready" | "degraded" | "unavailable" | "unconfigured";
    latency_ms?: number | null;
    heap_used_bytes?: number | null;
    heap_max_bytes?: number | null;
    heap_used_percent?: number | null;
    cpu_percent?: number | null;
    reason?: string;
  };
  concurrency: {
    active_runs: number;
    waiting_for_slot: number;
    session_queued: number;
    queued_runs: number;
    active_sessions: number;
    max_concurrent_runs: number;
    active_sse_streams: number;
  };
  http: {
    window_seconds: number;
    inflight_requests: number;
    request_count: number;
    rpm: number;
    p50_ms: number | null;
    p95_ms: number | null;
    error_count: number;
    error_rate: number;
  };
  model_calls: {
    window_minutes: number;
    recent_calls: number;
    recent_errors: number;
    recent_p50_ms: number | null;
    recent_p95_ms: number | null;
    calls_24h: number;
    errors_24h: number;
    p50_24h_ms: number | null;
    p95_24h_ms: number | null;
  };
  storage: AdminHostStorage & Partial<AdminElasticStorage>;
  capacity: {
    load_level: "normal" | "busy" | "overloaded";
    recommended_concurrent_runs: number;
    recommended_online_users: number;
    reasons: string[];
  };
}

export interface AdminDataResponse {
  recording: boolean;
  corpus: Array<{
    key: string; label: string; status: "ready" | "degraded" | "unavailable" | "not_configured"; count: number | null; reason: string | null;
    searches_24h: number; empty_24h: number; errors_24h: number; avg_ms_24h: number | null;
  }>;
  stores: Array<{ file: string; label: string; purpose: string; size_bytes: number; modified_at: number; tables: Array<{ name: string; rows: number }> }>;
  backups: AdminBackupItem[];
  disk: Array<{ label: string; size_bytes: number | null; files: number | null }>;
  host_storage?: AdminHostStorage;
  elastic_storage?: AdminElasticStorage;
  cleanup_preview?: AdminCleanupPreview;
}

export interface AdminOpsSettings {
  credential_encryption_ready: boolean;
  email_verification_enabled: boolean;
  allow_password_reset: boolean;
  email_ready: boolean;
  resend_from_email: string;
  resend_from_name: string;
  resend_api_key_hint: string | null;
  resend_key_configured: boolean;
  resend_key_source: "encrypted_db" | "env" | "none";
  material_quota_mb: number;
  auto_maintenance_enabled: boolean;
  backup_keep_days: number;
  updated_at: string | null;
}

export interface AdminOpsSettingsUpdate {
  email_verification_enabled?: boolean;
  allow_password_reset?: boolean;
  resend_from_email?: string;
  resend_from_name?: string;
  resend_api_key?: string;
  clear_resend_api_key?: boolean;
  material_quota_mb?: number;
  auto_maintenance_enabled?: boolean;
  backup_keep_days?: number;
}

export interface AdminEmailTestResult {
  ok: boolean;
  message_id: string | null;
  error_code: string | null;
  detail: string | null;
}

export interface AdminMaterialPassage {
  id: string;
  ordinal: number;
  locator: string;
  heading_path: string[] | null;
  text: string;
}

export interface AdminPassageListResponse {
  chunks: AdminMaterialPassage[];
  next_cursor: string | null;
  truncated: boolean;
  parse_status: string;
  complete: boolean;
  notices: string[];
}

export interface AdminAuditSummary {
  operations_24h: number;
  logins_24h: number;
  login_failures_24h: number;
  conversation_views_24h: number;
  disabled_accounts: number;
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
  error_code: string | null;
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
  context_tokens?: number;
  max_output_tokens?: number;
  thinking_modes?: ThinkingMode[];
  default_thinking?: ThinkingMode;
  inferred_matched?: boolean;
  inferred_source?: string;
}

export interface AdminProviderDiscoverResult {
  supported: boolean;
  models: AdminDiscoveredModel[];
  notice: string | null;
}

export interface AdminProviderBalance {
  status: "ok" | "unsupported" | "auth_failed" | "unreachable";
  supported: boolean;
  display: string | null;
  currency: string | null;
  total_balance: string | null;
  granted_balance: string | null;
  topped_up_balance: string | null;
  used_amount: string | null;
  source: string | null;
  notice: string | null;
}

export interface AdminModelInferResult {
  model_id: string;
  family: ModelFamily;
  context_tokens: number;
  max_output_tokens: number;
  thinking_modes: ThinkingMode[];
  default_thinking: ThinkingMode;
  matched: boolean;
  source_hint: string;
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
  context_tokens?: number;
  max_output_tokens?: number;
  thinking_modes?: ThinkingMode[];
  default_thinking?: ThinkingMode;
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
    cannot_delete_self: "不能删除当前登录的管理员账户。",
    last_admin_required: "至少要保留一位管理员。",
    account_has_active_runs: "这个账户还有正在运行的任务，请先停用或等任务结束再删除。",
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
    provider_not_found: "这个服务商已不存在，请刷新页面。",
    profile_not_found: "这个模型已不存在，请刷新页面。",
    conversation_not_found: "这个会话已不存在。",
    source_not_found: "这条资料已不存在。",
    skill_not_found: "这个技能已不存在，请刷新页面。",
    provider_name_required: "请填写服务商名称。",
    provider_probe_requires_model: "这个服务商还没有模型，请先添加一个模型再测试。",
    model_label_required: "请填写模型名称。",
    upstream_model_required: "请填写上游模型 ID。",
    model_protocol_invalid: "接口格式不正确。",
    model_family_invalid: "模型系列不正确。",
    thinking_mode_invalid: "默认思考档位必须在可选档位里。",
    thinking_modes_invalid: "请至少选一个思考档位。",
    context_tokens_invalid: "上下文长度需要是正整数。",
    max_output_tokens_invalid: "最大输出需要是正整数。",
    access_scope_invalid: "可见范围不正确。",
    capabilities_invalid: "模型能力格式不正确。",
    sort_order_invalid: "排序需要是整数。",
    credential_decrypt_failed: "已保存的密钥无法解密，请重新填写 API Key。",
    capability_model_policy_invalid: "模型权限设置不正确。",
    capability_skill_policy_invalid: "技能权限设置不正确。",
    capability_model_list_invalid: "指定的模型不存在或已归档，请重新勾选。",
    capability_change_required: "没有需要保存的修改。",
    account_change_required: "没有需要保存的修改。",
    invalid_audit_cursor: "翻页信息已失效，请重新加载记录。",
    invalid_conversation_cursor: "翻页信息已失效，请重新加载会话。",
    admin_service_unavailable: "管理员服务暂时不可用，请稍后重试。",
    model_directory_unavailable: "模型服务暂时不可用，请稍后重试。",
    email_provider_unconfigured: "请先填写并保存有效的 Resend API Key 与发件邮箱，再开启邮箱验证码。",
    email_invalid: "邮箱地址格式不正确，请核对后重试。",
    material_quota_invalid: "单人附件空间上限需为 0 到 1048576 MB 之间的整数。",
    backup_keep_days_invalid: "备份保留份数需为 1 到 90 之间的整数。",
    backup_not_found: "这份备份文件已不存在，请刷新列表。",
    artifact_not_found: "这份生成交付文件已不存在或校验失败。",
    material_unavailable: "这份用户附件已不存在或暂时无法读取。",
    material_service_unavailable: "资料服务暂时不可用，请稍后重试。",
    storage_space_low: "服务器磁盘剩余空间不足 5 GB，已暂停新附件上传，请先清理空间。",
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

async function downloadBinary(path: string, token: string, fallbackName: string): Promise<void> {
  const controller = new AbortController();
  let timedOut = false;
  const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, 60000);
  try {
    const response = await fetch(`${apiBase}${path}`, {
      method: "GET",
      signal: controller.signal,
      cache: "no-store",
      credentials: "omit",
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!response.ok) {
      const data = await response.json().catch(() => null);
      const code = typeof data?.detail === "string" ? data.detail : response.status >= 500 ? "unavailable" : "request_failed";
      throw new AdminApiError(code, response.status);
    }
    const blob = await response.blob();
    const disposition = response.headers.get("Content-Disposition") || "";
    const utf8Match = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
    const plainMatch = /filename="([^"]+)"/i.exec(disposition);
    const filename = utf8Match
      ? decodeURIComponent(utf8Match[1])
      : plainMatch
        ? plainMatch[1]
        : fallbackName;
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) {
    if (error instanceof AdminApiError) throw error;
    throw new AdminApiError(timedOut ? "timeout" : "unavailable");
  } finally {
    clearTimeout(timeout);
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
    deleteAccount: (id: string) =>
      request<{ deleted: boolean; account_id: string; email: string; conversations_deleted: number; materials_deleted: number; skills_deleted: number }>(
        `/admin/accounts/${encodeURIComponent(id)}`,
        token,
        { method: "DELETE" },
      ),
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
    providerBalance: (id: string) =>
      request<AdminProviderBalance>(`/admin/model-providers/${encodeURIComponent(id)}/balance`, token),
    inferModel: (modelId: string, signal?: AbortSignal) =>
      request<AdminModelInferResult>(`/admin/models/infer?model_id=${encodeURIComponent(modelId)}`, token, {}, signal),
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
    updateCapability: (accountId: string, update: Partial<Pick<AdminCapability, "model_policy" | "allowed_models" | "skill_policy" | "can_upload_skill" | "material_quota_mb">> & { clear_material_quota?: boolean }) =>
      request<AdminCapability>(`/admin/capabilities/${encodeURIComponent(accountId)}`, token, { method: "PATCH", body: JSON.stringify(update) }),
    systemMetrics: (signal?: AbortSignal) => request<AdminSystemMetrics>("/admin/system/metrics", token, {}, signal),
    skills: (signal?: AbortSignal) => request<{ status: "ready" | "degraded" | "unavailable"; total: number; skills: AdminSkillStatus[]; uploaded: AdminUploadedSkill[] }>("/admin/skills", token, {}, signal),
    deleteUploadedSkill: (skillId: string) => request<{ status: "removed" }>(`/admin/skills/${encodeURIComponent(skillId)}`, token, { method: "DELETE" }),
    tools: (signal?: AbortSignal) => request<AdminToolsResponse>("/admin/tools", token, {}, signal),
    toolCalls: (name: string, signal?: AbortSignal) => request<{ calls: AdminToolCall[] }>(`/admin/tools/${encodeURIComponent(name)}/calls`, token, {}, signal),
    data: (signal?: AbortSignal) => request<AdminDataResponse>("/admin/data", token, {}, signal),
    runDataCleanup: () => request<AdminCleanupResult>("/admin/data/cleanup", token, { method: "POST" }),
    createDataBackup: () => request<AdminBackupItem>("/admin/data/backups", token, { method: "POST" }),
    downloadDataBackup: (filename: string) =>
      downloadBinary(`/admin/data/backups/${encodeURIComponent(filename)}/download`, token, filename),
    opsSettings: (signal?: AbortSignal) => request<AdminOpsSettings>("/admin/ops/settings", token, {}, signal),
    updateOpsSettings: (update: AdminOpsSettingsUpdate) =>
      request<AdminOpsSettings>("/admin/ops/settings", token, { method: "PATCH", body: JSON.stringify(update) }),
    testOpsEmail: (toEmail: string) =>
      request<AdminEmailTestResult>("/admin/ops/email-test", token, {
        method: "POST",
        body: JSON.stringify({ to_email: toEmail }),
      }),
    materialPassages: (materialId: string, cursor?: string | null, signal?: AbortSignal) => {
      const suffix = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
      return request<AdminPassageListResponse>(`/admin/materials/${encodeURIComponent(materialId)}/passages${suffix}`, token, {}, signal);
    },
    downloadMaterial: (materialId: string, filename: string) =>
      downloadBinary(`/admin/materials/${encodeURIComponent(materialId)}/download`, token, filename),
    downloadRunArtifact: (accountId: string, conversationId: string, runId: string, artifactId: string, filename: string) =>
      downloadBinary(
        `/admin/accounts/${encodeURIComponent(accountId)}/conversations/${encodeURIComponent(conversationId)}/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(artifactId)}`,
        token,
        filename,
      ),
    auditSummary: (signal?: AbortSignal) => request<AdminAuditSummary>("/admin/audit/summary", token, {}, signal),
    resourcesHealth: (signal?: AbortSignal) => request<{ status: "ready" | "degraded" | "unavailable"; sources: Record<string, AdminHealthItem> }>("/admin/resources/health", token, {}, signal),
    runtimeHealth: (signal?: AbortSignal) => request<{ status: "ready" | "degraded" | "unavailable"; components: Record<string, AdminHealthItem> }>("/admin/runtime/health", token, {}, signal),
  };
}

export async function loadAdminDashboard(token: string, signal?: AbortSignal): Promise<AdminDashboardData> {
  const api = createAdminApi(token);
  const [overview, accounts, audit] = await Promise.all([api.overview(signal), api.accounts({}, signal), api.audit(signal)]);
  return { overview: overview.overview, accounts: accounts.accounts, audit: audit.events };
}
