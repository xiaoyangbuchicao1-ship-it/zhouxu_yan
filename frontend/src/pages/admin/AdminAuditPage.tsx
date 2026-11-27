import { useEffect, useState } from "react";
import { LoaderCircle, Search } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  createAdminApi,
  type AdminAuditCategory,
  type AdminAuditEvent,
  type AdminCapability,
} from "@/lib/admin-api";
import { auditActionLabel, auditChangeText } from "@/lib/admin-ui";
import { useAdminOutlet } from "./AdminLayout";
import { MODEL_POLICY_LABELS, SKILL_POLICY_LABELS } from "./admin-capabilities";
import { accountConversationPath } from "./admin-navigation";
import { InitialLoading, InlineError, LoadError, PageHeader, parseAdminDate, RefreshButton, StatusPill } from "./AdminShared";

type View = "operations" | "auth" | "conversation";
const VIEWS: Array<{ value: View; label: string; hint: string }> = [
  { value: "operations", label: "管理操作", hint: "管理员改了什么" },
  { value: "auth", label: "登录与密码", hint: "谁登录了、谁没登进去" },
  { value: "conversation", label: "查看会话", hint: "管理员看了谁的会话" },
];

const LOGIN_REASONS: Record<string, string> = {
  wrong_password: "密码不对",
  account_disabled: "账户已停用",
  not_an_administrator: "不是管理员",
};

function parseView(raw: string | null): View {
  return raw === "auth" || raw === "conversation" ? raw : "operations";
}

function dayKey(iso: string): string {
  const date = parseAdminDate(iso);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function dayLabel(key: string): string {
  const today = dayKey(new Date().toISOString());
  const yesterday = dayKey(new Date(Date.now() - 86_400_000).toISOString());
  if (key === today) return "今天";
  if (key === yesterday) return "昨天";
  return key.replace(/^(\d+)-(\d+)-(\d+)$/, "$1 年 $2 月 $3 日").replace(/ 0(\d) /g, " $1 ");
}

function timeLabel(iso: string): string {
  return parseAdminDate(iso).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
}

function describe(event: AdminAuditEvent): string[] {
  const tags: string[] = [];
  const details = event.details || {};
  const change = auditChangeText(event);
  if (change) tags.push(change);
  if (event.action === "auth.login_failed") {
    tags.push(LOGIN_REASONS[String(details.reason)] ?? "登录没成功");
    tags.push(details.entry === "admin" ? "管理员入口" : "用户入口");
  }
  if (event.action === "account.deleted") {
    if (typeof details.email === "string" && details.email) tags.push(details.email);
    if (typeof details.conversations_deleted === "number" && details.conversations_deleted > 0) tags.push(`清空会话 ${details.conversations_deleted} 个`);
    if (typeof details.materials_deleted === "number" && details.materials_deleted > 0) tags.push(`清空资料 ${details.materials_deleted} 份`);
  }
  if (typeof details.name === "string" && details.name) tags.push(`「${details.name}」`);
  if (typeof details.filename === "string" && details.filename) tags.push(`附件「${details.filename}」`);
  if (event.action === "system.cleanup" && typeof details.cleaned_bytes === "number") {
    const kb = Math.round(details.cleaned_bytes / 1024);
    tags.push(`释放 ${kb >= 1024 ? `${(kb / 1024).toFixed(1)} MB` : `${kb} KB`}`);
  }
  if (event.action === "system.backup_created" && typeof details.size_bytes === "number") {
    tags.push(`备份大小 ${(details.size_bytes / (1024 * 1024)).toFixed(1)} MB`);
  }
  if (event.action === "system.email_tested" && typeof details.to === "string" && details.to) {
    tags.push(`收件邮箱 ${details.to}`);
    if (typeof details.error_code === "string" && details.error_code) tags.push(`错误 ${details.error_code}`);
  }
  if (event.action === "system.ops_settings_updated") {
    tags.push(details.email_verification_enabled ? "已开启邮箱验证码" : "未开启邮箱验证码");
    if (typeof details.material_quota_mb === "number") tags.push(`附件上限 ${details.material_quota_mb} MB`);
  }
  if (typeof details.provider_id === "string" && details.provider_id) tags.push(`服务商 ${details.provider_id}`);
  if (typeof details.profile_id === "string" && details.profile_id) tags.push(`模型 ${details.profile_id}`);
  if (typeof details.model_policy === "string") {
    const policy = details.model_policy as AdminCapability["model_policy"];
    const picked = Array.isArray(details.allowed_models) ? details.allowed_models.length : 0;
    tags.push(`模型：${policy === "allowlist" ? `指定 ${picked} 个` : MODEL_POLICY_LABELS[policy] ?? policy}`);
  }
  if (typeof details.skill_policy === "string") {
    tags.push(`技能：${SKILL_POLICY_LABELS[details.skill_policy as AdminCapability["skill_policy"]] ?? details.skill_policy}`);
  }
  if (typeof details.can_upload_skill === "boolean") tags.push(details.can_upload_skill ? "可上传技能" : "不可上传技能");
  if (typeof details.reason === "string" && details.reason && event.action !== "auth.login_failed") tags.push(`原因：${details.reason}`);
  return tags;
}

interface TimelineItem { key: string; time: string; who: string; what: string; target?: { id: string; email: string; alias?: string | null }; details: string[]; failed: boolean; count?: number }

/** 查看会话太碎：同一天、同一个人看同一个账户，折成一行。 */
function foldViews(events: AdminAuditEvent[]): TimelineItem[] {
  const folded = new Map<string, TimelineItem>();
  for (const event of events) {
    const key = `${dayKey(event.created_at)}|${event.actor_id}|${event.target_account_id}`;
    const existing = folded.get(key);
    if (existing) existing.count = (existing.count ?? 1) + 1;
    else folded.set(key, {
      key, time: event.created_at, who: event.actor_email, what: "查看了会话",
      target: event.target_account_id && event.target_email ? { id: event.target_account_id, email: event.target_email, alias: event.target_alias } : undefined,
      details: [], failed: false, count: 1,
    });
  }
  return [...folded.values()];
}

function toItems(events: AdminAuditEvent[], view: View): TimelineItem[] {
  if (view === "conversation") return foldViews(events);
  return events.map(event => ({
    key: event.id,
    time: event.created_at,
    who: event.actor_email,
    what: auditActionLabel(event.action),
    target: event.target_account_id && event.target_email && event.target_account_id !== event.actor_id
      ? { id: event.target_account_id, email: event.target_email, alias: event.target_alias } : undefined,
    details: describe(event),
    failed: event.outcome === "failed",
  }));
}

export default function AdminAuditPage() {
  const { token } = useAdminOutlet();
  const [searchParams, setSearchParams] = useSearchParams();
  const view = parseView(searchParams.get("view"));
  const query = searchParams.get("q") ?? "";
  const onlyFailed = searchParams.get("failed") === "1";
  const [extraEvents, setExtraEvents] = useState<AdminAuditEvent[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null);

  const category: AdminAuditCategory = view;
  const outcome = onlyFailed ? "failed" : "all";

  function setParam(key: "view" | "q" | "failed", value: string) {
    const next = new URLSearchParams(searchParams);
    if (!value) next.delete(key);
    else next.set(key, value);
    if (key === "view") next.delete("failed");
    setSearchParams(next, { replace: true });
  }

  const summary = useQuery({
    queryKey: ["admin", "audit-summary"],
    queryFn: ({ signal }) => createAdminApi(token).auditSummary(signal),
    retry: false,
  });
  const auditQuery = useQuery({
    queryKey: ["admin", "audit", query, category, outcome],
    queryFn: ({ signal }) => createAdminApi(token).audit({ query, category, outcome, limit: 40 }, signal),
    retry: false,
    placeholderData: previous => previous,
  });
  const data = auditQuery.data;

  useEffect(() => {
    setExtraEvents([]);
    setNextCursor(data?.next_cursor ?? null);
    setLoadMoreError(null);
  }, [query, category, outcome, data]);

  async function loadMore() {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    setLoadMoreError(null);
    try {
      const page = await createAdminApi(token).audit({ query, category, outcome, cursor: nextCursor, limit: 40 });
      setExtraEvents(prev => [...prev, ...page.events]);
      setNextCursor(page.next_cursor ?? null);
    } catch (cause) {
      setLoadMoreError(cause instanceof Error ? cause.message : "没有加载出更多记录。");
    } finally {
      setLoadingMore(false);
    }
  }

  const events = [...(data?.events ?? []), ...extraEvents].filter((item, idx, list) => list.findIndex(other => other.id === item.id) === idx);
  const items = toItems(events, view);
  const days = items.reduce<Array<{ key: string; items: TimelineItem[] }>>((list, item) => {
    const key = dayKey(item.time);
    const group = list.find(entry => entry.key === key);
    if (group) group.items.push(item);
    else list.push({ key, items: [item] });
    return list;
  }, []);
  const counts = data?.category_counts;
  const errorMessage = auditQuery.error instanceof Error ? auditQuery.error.message : "记录没有加载出来。";
  const stats = summary.data;

  return (
    <div className="admin-route-page">
      <PageHeader title="审计" action={<RefreshButton loading={auditQuery.isFetching || summary.isFetching} onClick={() => { void auditQuery.refetch(); void summary.refetch(); }} />} />

      <section className="admin-kpi-grid is-four" aria-label="近 24 小时">
        <div className="admin-kpi"><span className="admin-kpi-label">管理操作</span><strong>{stats?.operations_24h ?? "—"}<small> 次</small></strong><p>近 24 小时</p></div>
        <div className="admin-kpi"><span className="admin-kpi-label">登录成功</span><strong>{stats?.logins_24h ?? "—"}<small> 次</small></strong><p>近 24 小时</p></div>
        <button type="button" className={`admin-kpi is-button ${stats && stats.login_failures_24h > 0 ? "is-alert" : ""}`} onClick={() => { setParam("view", "auth"); setParam("failed", "1"); }}>
          <span className="admin-kpi-label">登录失败</span><strong>{stats?.login_failures_24h ?? "—"}<small> 次</small></strong><p>点开只看失败的</p>
        </button>
        <Link className="admin-kpi" to="/admin/accounts"><span className="admin-kpi-label">已停用账户</span><strong>{stats?.disabled_accounts ?? "—"}<small> 个</small></strong><p>到账户页处理</p></Link>
      </section>

      <div className="admin-tabs" role="tablist" aria-label="审计视图">
        {VIEWS.map(option => {
          const count = option.value === "operations" ? counts?.operations : counts?.[option.value];
          return (
            <button key={option.value} type="button" role="tab" aria-selected={view === option.value} className={view === option.value ? "is-active" : ""} onClick={() => setParam("view", option.value === "operations" ? "" : option.value)}>
              <span>{option.label}</span>
              {count !== undefined && <small>{count}</small>}
            </button>
          );
        })}
      </div>
      <p className="admin-tabs-hint">{VIEWS.find(option => option.value === view)?.hint}</p>

      <div className="admin-toolbar">
        <label className="admin-search">
          <Search size={15} aria-hidden="true" />
          <input type="search" aria-label="搜索记录" placeholder="搜索邮箱" value={query} onChange={event => setParam("q", event.target.value)} />
        </label>
        {view === "auth" && (
          <label className="admin-check">
            <input type="checkbox" checked={onlyFailed} onChange={event => setParam("failed", event.target.checked ? "1" : "")} />
            只看登录失败
          </label>
        )}
      </div>

      {!data && auditQuery.isPending ? <InitialLoading label="正在读取记录…" />
        : !data ? <LoadError message={errorMessage} onRetry={() => void auditQuery.refetch()} />
          : (
            <>
              {auditQuery.isError && <InlineError message={errorMessage} onRetry={() => void auditQuery.refetch()} />}
              {days.length === 0 ? <p className="admin-empty">这里还没有记录。</p> : (
                <div className="admin-timeline">
                  {days.map(day => (
                    <section key={day.key} className="admin-timeline-day" aria-label={dayLabel(day.key)}>
                      <h2>{dayLabel(day.key)}<small>{day.items.length} 条</small></h2>
                      <ol>
                        {day.items.map(item => (
                          <li key={item.key} className={item.failed ? "is-failed" : ""}>
                            <time dateTime={item.time}>{timeLabel(item.time)}</time>
                            <div className="admin-timeline-body">
                              <p>
                                <strong>{item.who}</strong>{" "}
                                {item.what}
                                {item.count && item.count > 1 ? ` ${item.count} 次` : ""}
                                {item.target && (
                                  <> → <Link className="admin-cell-link" to={accountConversationPath(item.target.id)}>
                                    {item.target.alias ? `${item.target.alias} (${item.target.email})` : item.target.email}
                                  </Link></>
                                )}
                              </p>
                              {item.details.length > 0 && <p className="admin-timeline-detail">{item.details.join(" · ")}</p>}
                            </div>
                            {item.failed && <StatusPill tone="bad">失败</StatusPill>}
                          </li>
                        ))}
                      </ol>
                    </section>
                  ))}
                </div>
              )}
              {loadMoreError && <InlineError message={loadMoreError} onRetry={() => void loadMore()} />}
              {nextCursor && (
                <div className="admin-load-more">
                  <button type="button" className="admin-page-button" disabled={loadingMore} onClick={() => void loadMore()}>
                    {loadingMore && <LoaderCircle className="admin-spin" size={15} />}
                    {loadingMore ? "加载中…" : "加载更多"}
                  </button>
                </div>
              )}
            </>
          )}
    </div>
  );
}
