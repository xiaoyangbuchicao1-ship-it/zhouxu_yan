import { AlertCircle, LoaderCircle, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";

export function PageHeader({ title, description, action }: { title: string; description?: string; action?: ReactNode }) {
  return <header className="admin-page-heading"><div><h1>{title}</h1>{description && <span>{description}</span>}</div>{action}</header>;
}

export type StatusTone = "ok" | "warn" | "bad" | "muted";

export function StatusPill({ tone, children }: { tone: StatusTone; children: ReactNode }) {
  return <span className={`admin-pill is-${tone}`}>{children}</span>;
}

export function taskTone(value: string | null | undefined): StatusTone {
  const bucket = statusBucket(value);
  if (bucket === "succeeded") return "ok";
  if (bucket === "failed") return "bad";
  if (bucket === "active") return "warn";
  return "muted";
}

export function healthTone(value: string | null | undefined): StatusTone {
  if (value === "ready") return "ok";
  if (value === "degraded") return "warn";
  if (value === "not_configured") return "muted";
  return "bad";
}

export function healthLabel(value: string | null | undefined): string {
  if (value === "ready") return "正常";
  if (value === "degraded") return "部分可用";
  if (value === "not_configured") return "未启用";
  return "不可用";
}

export function RefreshButton({ loading, onClick }: { loading: boolean; onClick: () => void }) {
  return <button className="admin-refresh" type="button" onClick={onClick} disabled={loading}>{loading ? <LoaderCircle className="admin-spin" size={16} /> : <RefreshCw size={16} />}刷新</button>;
}

export function InitialLoading({ label = "正在加载…" }: { label?: string }) {
  return <div className="admin-loading" role="status"><LoaderCircle className="admin-spin" size={20} />{label}</div>;
}

export function LoadError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return <section className="admin-state"><AlertCircle size={27} /><h2>暂时无法加载</h2><p role="alert">{message}</p><button className="admin-primary-button" type="button" onClick={onRetry}>重新加载</button></section>;
}

export function InlineError({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return <div className="admin-alert" role="alert"><AlertCircle size={17} /><span>{message}</span>{onRetry && <button type="button" onClick={onRetry}>重试</button>}</div>;
}

export function parseAdminDate(value: string): Date {
  const trimmed = value.trim();
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(trimmed);
  const normalized = hasZone ? trimmed : `${trimmed.replace(" ", "T")}Z`;
  return new Date(normalized);
}

export function formatAdminDate(value: string | null | undefined): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(parseAdminDate(value));
}

export function formatAdminDuration(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${(value / 1000).toFixed(1)} 秒`;
}

export type StatusBucket = "succeeded" | "active" | "failed" | "no_runs";

export function statusBucket(value: string | null | undefined): StatusBucket {
  if (!value) return "no_runs";
  if (value === "succeeded") return "succeeded";
  if (value === "failed" || value === "cancelled") return "failed";
  return "active";
}

export function statusToneClass(value: string | null | undefined): string {
  const bucket = statusBucket(value);
  if (bucket === "succeeded") return "is-verified";
  if (bucket === "failed") return "is-failed";
  if (bucket === "active") return "is-unverified";
  return "";
}

export function statusLabel(value: string | null | undefined): string {
  const labels: Record<string, string> = {
    queued: "等待中", running: "处理中", pausing: "暂停中", paused: "已暂停",
    waiting_user: "等你确认", succeeded: "已完成", failed: "未完成", cancelled: "已取消",
  };
  return value ? labels[value] ?? value : "暂无任务";
}

export function modeLabel(value: string): string {
  return value === "work" ? "工作" : "聊天";
}

