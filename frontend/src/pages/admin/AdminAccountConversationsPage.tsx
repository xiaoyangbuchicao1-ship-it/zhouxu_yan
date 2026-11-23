import { useEffect, useState } from "react";
import { ArrowLeft, ChevronRight, LoaderCircle, MessageSquare, Search } from "lucide-react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  createAdminApi,
  type AdminConversationAccount,
  type AdminConversationSummary,
  type ConversationMode,
  type ConversationStatusFilter,
} from "@/lib/admin-api";
import { useAdminOutlet } from "./AdminLayout";
import { conversationDetailPath } from "./admin-navigation";
import {
  formatAdminDate,
  InitialLoading,
  InlineError,
  LoadError,
  modeLabel,
  PageHeader,
  RefreshButton,
  statusLabel,
  statusToneClass,
} from "./AdminShared";

const STATUS_OPTIONS: Array<{ value: ConversationStatusFilter; label: string }> = [
  { value: "all", label: "全部" },
  { value: "succeeded", label: "已完成" },
  { value: "active", label: "进行中/暂停" },
  { value: "failed", label: "未完成" },
  { value: "no_runs", label: "暂无任务" },
];

function parseModeFilter(raw: string | null): ConversationMode | "all" {
  return raw === "chat" || raw === "work" ? raw : "all";
}

function parseArchivedFilter(raw: string | null): "active" | "archived" | "all" {
  return raw === "archived" || raw === "all" ? raw : "active";
}

function parseStatusFilter(raw: string | null): ConversationStatusFilter {
  if (raw === "succeeded" || raw === "active" || raw === "failed" || raw === "no_runs") return raw;
  return "all";
}

export default function AdminAccountConversationsPage() {
  const { accountId = "" } = useParams();
  const { token } = useAdminOutlet();
  const [searchParams, setSearchParams] = useSearchParams();
  const query = searchParams.get("q") ?? "";
  const mode = parseModeFilter(searchParams.get("mode"));
  const archived = parseArchivedFilter(searchParams.get("archived"));
  const status = parseStatusFilter(searchParams.get("status"));
  const [extraPages, setExtraPages] = useState<AdminConversationSummary[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null);

  function updateFilterParam(key: "q" | "mode" | "archived" | "status", value: string) {
    const next = new URLSearchParams(searchParams);
    const isDefault =
      (key === "q" && !value) ||
      (key === "mode" && value === "all") ||
      (key === "archived" && value === "active") ||
      (key === "status" && value === "all");
    if (isDefault) {
      next.delete(key);
    } else {
      next.set(key, value);
    }
    setSearchParams(next, { replace: true });
  }

  const conversationsQuery = useQuery({
    queryKey: ["admin", "account", accountId, "conversations", query, mode, archived, status],
    queryFn: () => createAdminApi(token).accountConversations(accountId, { query, mode, archived, status, limit: 20 }),
    retry: false,
    placeholderData: previous => previous,
  });

  const data = conversationsQuery.data;
  useEffect(() => {
    setExtraPages([]);
    setNextCursor(data?.next_cursor ?? null);
    setLoadMoreError(null);
  }, [accountId, query, mode, archived, status, data]);

  async function loadMore() {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    setLoadMoreError(null);
    try {
      const page = await createAdminApi(token).accountConversations(accountId, {
        query,
        mode,
        archived,
        status,
        cursor: nextCursor,
        limit: 20,
      });
      setExtraPages(prev => [...prev, ...page.conversations]);
      setNextCursor(page.next_cursor);
    } catch (cause) {
      setLoadMoreError(cause instanceof Error ? cause.message : "加载更多会话失败。");
    } finally {
      setLoadingMore(false);
    }
  }

  const allItems = [...(data?.conversations ?? []), ...extraPages].filter(
    (item, index, arr) => arr.findIndex(other => other.id === item.id) === index,
  );
  const statusCounts = data?.status_counts;
  const accountInfo: AdminConversationAccount | null | undefined = data?.account;
  const pageTitle = accountInfo ? accountInfo.display_name || accountInfo.email : "这个账户的会话";
  const pageDescription = accountInfo
    ? `账户邮箱：${accountInfo.email} · 只显示用户可见内容和运行状态`
    : "只显示用户可见内容和运行状态。";

  return (
    <div className="admin-route-page">
      <Link className="admin-back-link" to="/admin/accounts">
        <ArrowLeft size={15} />
        返回人员
      </Link>
      <PageHeader
        eyebrow="会话日志"
        title={pageTitle}
        description={pageDescription}
        action={<RefreshButton loading={conversationsQuery.isFetching} onClick={() => void conversationsQuery.refetch()} />}
      />
      {!data && conversationsQuery.isPending ? (
        <InitialLoading label="正在读取会话…" />
      ) : !data ? (
        <LoadError
          message={conversationsQuery.error instanceof Error ? conversationsQuery.error.message : "会话暂时不可用。"}
          onRetry={() => void conversationsQuery.refetch()}
        />
      ) : (
        <>
          {conversationsQuery.isError && (
            <InlineError
              message={conversationsQuery.error instanceof Error ? conversationsQuery.error.message : "刷新没有完成。"}
              onRetry={() => void conversationsQuery.refetch()}
            />
          )}
          <div className="admin-accounts-toolbar admin-conv-toolbar">
            <label className="admin-account-search">
              <Search size={16} aria-hidden="true" />
              <input
                type="search"
                aria-label="搜索会话"
                placeholder="搜索会话标题"
                value={query}
                onChange={event => updateFilterParam("q", event.target.value)}
              />
            </label>
            <div className="admin-conv-filters">
              <select aria-label="会话模式" value={mode} onChange={e => updateFilterParam("mode", e.target.value)}>
                <option value="all">全部模式</option>
                <option value="chat">聊天</option>
                <option value="work">工作</option>
              </select>
              <select aria-label="归档状态" value={archived} onChange={e => updateFilterParam("archived", e.target.value)}>
                <option value="active">未归档</option>
                <option value="archived">已归档</option>
                <option value="all">全部会话</option>
              </select>
            </div>
          </div>
          <div className="admin-source-filter-bar admin-conv-status-bar" role="group" aria-label="按运行状态筛选">
            {STATUS_OPTIONS.map(option => {
              const count = statusCounts ? statusCounts[option.value] : undefined;
              const isSelected = status === option.value;
              return (
                <button
                  key={option.value}
                  type="button"
                  aria-pressed={isSelected}
                  className={isSelected ? "is-active" : ""}
                  onClick={() => updateFilterParam("status", option.value)}
                >
                  <span>{option.label}</span>
                  {count !== undefined ? <small className="admin-conv-count">{count}</small> : null}
                </button>
              );
            })}
          </div>
          {loadMoreError && <InlineError message={loadMoreError} onRetry={() => void loadMore()} />}
          {allItems.length === 0 ? (
            <p className="admin-empty">还没有匹配的会话。</p>
          ) : (
            <section className="admin-conversation-list" aria-label="会话列表">
              {allItems.map(item => {
                const displayTime = item.latest_run_at ?? item.created_at ?? item.updated_at;
                const tone = statusToneClass(item.latest_status);
                return (
                  <Link className="admin-conversation-card" key={item.id} to={conversationDetailPath(accountId, item.id)}>
                    <span className="admin-conversation-icon">
                      <MessageSquare size={17} />
                    </span>
                    <span className="admin-conversation-copy">
                      <strong>{item.title}</strong>
                      <small className="admin-conv-meta">
                        <span>{modeLabel(item.mode)}</span>
                        {item.archived ? <span>· 已归档</span> : null}
                        <span>· {item.run_count} 次运行</span>
                        <span className={`admin-cc-badge ${tone}`}>{statusLabel(item.latest_status)}</span>
                      </small>
                    </span>
                    <time dateTime={displayTime}>{formatAdminDate(displayTime)}</time>
                    <ChevronRight size={17} />
                  </Link>
                );
              })}
            </section>
          )}
          {nextCursor && (
            <div className="admin-load-more">
              <button type="button" className="admin-page-button" disabled={loadingMore} onClick={() => void loadMore()}>
                {loadingMore ? <LoaderCircle className="admin-spin" size={15} /> : null}
                {loadingMore ? "加载中…" : "加载更多会话"}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
