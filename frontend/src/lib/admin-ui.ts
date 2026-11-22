import type { AuthSession } from "./auth";
import type { AdminAccount, AdminAccountUpdate, AdminAuditEvent, AdminDashboardData } from "./admin-api";

export interface AccountFilters {
  query: string;
  status: "all" | AdminAccount["status"];
  role: "all" | AdminAccount["role"];
}

const statusLabels: Record<string, string> = { active: "正常", disabled: "已停用" };
const roleLabels: Record<string, string> = { user: "普通用户", admin: "管理员" };

export function canEnterAdmin(session: AuthSession | null): boolean {
  return session?.account.role === "admin";
}

export function accountStatusLabel(status: "active" | "disabled"): string {
  return statusLabels[status];
}

export function accountRoleLabel(role: "user" | "admin"): string {
  return roleLabels[role];
}

export function auditActionLabel(action: string): string {
  const labels: Record<string, string> = {
    "account.status_changed": "调整账户状态",
    "account.role_changed": "调整账户角色",
  };
  return labels[action] ?? "业务操作";
}

export function selectAccountPage(accounts: readonly AdminAccount[], filters: AccountFilters, requestedPage: number) {
  const query = filters.query.trim().toLocaleLowerCase();
  const matched = accounts.filter(account =>
    (filters.status === "all" || account.status === filters.status)
    && (filters.role === "all" || account.role === filters.role)
    && (!query || `${account.email} ${account.display_name}`.toLocaleLowerCase().includes(query)),
  );
  const pageSize = 20;
  const totalPages = Math.max(1, Math.ceil(matched.length / pageSize));
  const page = Math.min(Math.max(1, requestedPage), totalPages);
  const offset = (page - 1) * pageSize;
  return { items: matched.slice(offset, offset + pageSize), total: matched.length, totalPages, page, start: matched.length ? offset + 1 : 0, end: Math.min(offset + pageSize, matched.length) };
}

export function auditChangeText(event: Pick<AdminAuditEvent, "action" | "details">): string {
  if (event.action !== "account.status_changed" && event.action !== "account.role_changed") return "";
  const labels = event.action === "account.status_changed" ? statusLabels : roleLabels;
  return `${labels[String(event.details.from)]} → ${labels[String(event.details.to)]}`;
}

export function accountChangeText(update: AdminAccountUpdate): string {
  return [update.status ? (update.status === "active" ? "启用账户" : "停用账户") : "", update.role ? `角色改为${accountRoleLabel(update.role)}` : ""].filter(Boolean).join("，");
}

export function applyUpdatedAccount(data: AdminDashboardData, updated: AdminAccount): AdminDashboardData {
  const previous = data.accounts.find(account => account.id === updated.id)!;
  return {
    ...data,
    accounts: data.accounts.map(account => account.id === updated.id ? updated : account),
    overview: {
      ...data.overview,
      active_accounts: data.overview.active_accounts + Number(updated.status === "active") - Number(previous.status === "active"),
      disabled_accounts: data.overview.disabled_accounts + Number(updated.status === "disabled") - Number(previous.status === "disabled"),
      admin_accounts: data.overview.admin_accounts + Number(updated.role === "admin") - Number(previous.role === "admin"),
    },
  };
}
