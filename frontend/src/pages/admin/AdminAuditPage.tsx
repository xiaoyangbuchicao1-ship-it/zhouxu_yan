import { useEffect, useState } from "react";
import { LoaderCircle, Search, ShieldCheck } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  createAdminApi,
  type AdminAuditCategory,
  type AdminAuditEvent,
  type AdminAuditOutcome,
} from "@/lib/admin-api";
import { auditActionLabel, auditChangeText } from "@/lib/admin-ui";
import { useAdminOutlet } from "./AdminLayout";
import { accountConversationPath, conversationDetailPath } from "./admin-navigation";
import { formatAdminDate, InitialLoading, InlineError, LoadError, PageHeader, RefreshButton } from "./AdminShared";

const CATEGORY_OPTIONS: Array<{ value: AdminAuditCategory; label: string }> = [
  { value: "all", label: "全部" },
  { value: "account", label: "账户与角色" },
  { value: "capability", label: "能力授权" },
  { value: "model", label: "模型与服务商" },
  { value: "conversation", label: "会话查看" },
];

function parseCategory(raw: string | null): AdminAuditCategory {
  if (raw === "account" || raw === "capability" || raw === "model" || raw === "conversation") return raw;
  return "all";
}

function parseOutcome(raw: string | null): AdminAuditOutcome {
  if (raw === "succeeded" || raw === "failed") return raw;
  return "all";
}

function summarizeDetails(event: AdminAuditEvent): string[] {
  const tags: string[] = [];
  const accountChange = auditChangeText(event);
  if (accountChange) tags.push(accountChange);
  const details = event.details || {};
  if (typeof details.reason === "string" && details.reason) tags.push(`原因：${details.reason}`);
  if (typeof details.provider_id === "string" && details.provider_id) tags.push(`服务商：${details.provider_id}`);
  if (typeof details.profile_id === "string" && details.profile_id) tags.push(`模型：${details.profile_id}`);
  if (typeof details.run_count === "number") tags.push(`读取 ${details.run_count} 条运行`);
  if (typeof details.model_policy === "string") tags.push(`模型策略：${details.model_policy}`);
  if (event.resource_id && tags.length === 0) tags.push(`对象：${event.resource_id}`);
  return tags;
}

export default function AdminAuditPage() {
  const { token } = useAdminOutlet();
  const [searchParams, setSearchParams] = useSearchParams();
  const query = searchParams.get("q") ?? "";
  const category = parseCategory(searchParams.get("category"));
  const outcome = parseOutcome(searchParams.get("outcome"));
  const [extraEvents, setExtraEvents] = useState<AdminAuditEvent[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null);

  function updateParam(key: "q" | "category" | "outcome", value: string) {
    const next = new URLSearchParams(searchParams);
    const isDefault =
      (key === "q" && !value) ||
      (key === "category" && value === "all") ||
      (key === "outcome" && value === "all");
    if (isDefault) next.delete(key);
    else next.set(key, value);
    setSearchParams(next, { replace: true });
  }

  const auditQuery = useQuery({
    queryKey: ["admin", "audit", query, category, outcome],
    queryFn: ({ signal }) => createAdminApi(token).audit({ query, category, outcome, limit: 30 }, signal),
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
      const page = await createAdminApi(token).audit({
        query,
        category,
        outcome,
        cursor: nextCursor,
        limit: 30,
      });
      setExtraEvents(prev => [...prev, ...page.events]);
      setNextCursor(page.next_cursor ?? null);
    } catch (cause) {
      setLoadMoreError(cause instanceof Error ? cause.message : "加载更多审计记录失败。");
    } finally {
      setLoadingMore(false);
    }
  }

  const allEvents = [...(data?.events ?? []), ...extraEvents].filter(
    (item, idx, list) => list.findIndex(other => other.id === item.id) === idx,
  );
  const counts = data?.category_counts;

  return (
    <div className="admin-route-page">
      <PageHeader
        eyebrow="安全与追溯"
        title="审计日志"
        description="按操作类别、执行结果和相关账户检索后台变更与查看记录。"
        action={<RefreshButton loading={auditQuery.isFetching} onClick={() => void auditQuery.refetch()} />}
      />

      {!data && auditQuery.isPending ? (
        <InitialLoading label="正在读取审计日志…" />
      ) : !data ? (
        <LoadError
          message={auditQuery.error instanceof Error ? auditQuery.error.message : "审计日志暂时不可用。"}
          onRetry={() => void auditQuery.refetch()}
        />
      ) : (
        <>
          {auditQuery.isError && (
            <InlineError
              message={auditQuery.error instanceof Error ? auditQuery.error.message : "刷新未完成。"}
              onRetry={() => void auditQuery.refetch()}
            />
          )}

          <div className="admin-accounts-toolbar admin-conv-toolbar">
            <label className="admin-account-search">
              <Search size={16} aria-hidden="true" />
              <input
                type="search"
                aria-label="搜索审计记录"
                placeholder="搜索操作人邮箱、目标用户或对象编号"
                value={query}
                onChange={event => updateParam("q", event.target.value)}
              />
            </label>
            <div className="admin-audit-filters">
              <select
                aria-label="审计结果筛选"
                value={outcome}
                onChange={event => updateParam("outcome", event.target.value)}
              >
                <option value="all">全部结果</option>
                <option value="succeeded">成功</option>
                <option value="failed">失败或拒绝</option>
              </select>
            </div>
          </div>

          <div className="admin-source-filter-bar admin-conv-status-bar" role="group" aria-label="按审计类别筛选">
            {CATEGORY_OPTIONS.map(option => {
              const count = counts ? counts[option.value] : undefined;
              const active = category === option.value;
              return (
                <button
                  key={option.value}
                  type="button"
                  aria-pressed={active}
                  className={active ? "is-active" : ""}
                  onClick={() => updateParam("category", option.value)}
                >
                  <span>{option.label}</span>
                  {count !== undefined ? <small className="admin-conv-count">{count}</small> : null}
                </button>
              );
            })}
          </div>

          {loadMoreError && <InlineError message={loadMoreError} onRetry={() => void loadMore()} />}

          {allEvents.length === 0 ? (
            <p className="admin-empty">当前筛选条件下暂无审计记录。</p>
          ) : (
            <section className="admin-audit-list" aria-label="审计日志列表">
              {allEvents.map(event => {
                const detailTags = summarizeDetails(event);
                const failed = event.outcome === "failed";
                return (
                  <article className="admin-audit-card" key={event.id}>
                    <div className="admin-audit-card-head">
                      <div className="admin-audit-card-title">
                        <strong>{auditActionLabel(event.action)}</strong>
                        <span className={`admin-cc-badge ${failed ? "is-failed" : "is-verified"}`}>
                          {failed ? "失败 / 拒绝" : "成功"}
                        </span>
                      </div>
                      <time dateTime={event.created_at}>{formatAdminDate(event.created_at)}</time>
                    </div>
                    <div className="admin-audit-card-meta">
                      <span>
                        <ShieldCheck size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />
                        操作人：{event.actor_email}
                      </span>
                      {event.target_email && event.target_account_id && (
                        <span>
                          目标账户：
                          <Link to={accountConversationPath(event.target_account_id)}>
                            {event.target_email}
                          </Link>
                        </span>
                      )}
                      {event.resource_type === "conversation" && event.target_account_id && event.resource_id && (
                        <Link to={conversationDetailPath(event.target_account_id, event.resource_id)}>
                          查看对应会话 →
                        </Link>
                      )}
                    </div>
                    {detailTags.length > 0 && (
                      <div className="admin-audit-details-row">
                        {detailTags.map(tag => (
                          <span key={tag} className="admin-audit-detail-tag">{tag}</span>
                        ))}
                      </div>
                    )}
                  </article>
                );
              })}
            </section>
          )}

          {nextCursor && (
            <div className="admin-load-more">
              <button type="button" className="admin-page-button" disabled={loadingMore} onClick={() => void loadMore()}>
                {loadingMore ? <LoaderCircle className="admin-spin" size={15} /> : null}
                {loadingMore ? "加载中…" : "加载更多审计记录"}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
