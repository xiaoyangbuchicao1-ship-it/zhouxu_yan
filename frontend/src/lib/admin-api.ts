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
  };
}

export async function loadAdminDashboard(token: string, signal?: AbortSignal): Promise<AdminDashboardData> {
  const api = createAdminApi(token);
  const [overview, accounts, audit] = await Promise.all([api.overview(signal), api.accounts(signal), api.audit(signal)]);
  return { overview: overview.overview, accounts: accounts.accounts, audit: audit.events };
}
