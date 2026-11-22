import { useState } from "react";
import { ArrowLeft, ChevronRight, MessageSquare, Search } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { createAdminApi } from "@/lib/admin-api";
import { useAdminOutlet } from "./AdminLayout";
import { conversationDetailPath } from "./admin-navigation";
import { formatAdminDate, InitialLoading, InlineError, LoadError, modeLabel, PageHeader, RefreshButton, statusLabel } from "./AdminShared";

export default function AdminAccountConversationsPage() {
  const { accountId = "" } = useParams();
  const { token } = useAdminOutlet();
  const [query, setQuery] = useState("");
  const conversationsQuery = useQuery({
    queryKey: ["admin", "account", accountId, "conversations", query],
    queryFn: () => createAdminApi(token).accountConversations(accountId, { query, archived: "active", limit: 20 }),
    retry: false,
    placeholderData: previous => previous,
  });
  const data = conversationsQuery.data;
  return <div className="admin-route-page">
    <Link className="admin-back-link" to="/admin/accounts"><ArrowLeft size={15} />返回人员</Link>
    <PageHeader eyebrow="会话日志" title="这个账户的会话" description="只显示用户可见内容和运行状态。" action={<RefreshButton loading={conversationsQuery.isFetching} onClick={() => void conversationsQuery.refetch()} />} />
    {!data && conversationsQuery.isPending ? <InitialLoading label="正在读取会话…" />
      : !data ? <LoadError message={conversationsQuery.error instanceof Error ? conversationsQuery.error.message : "会话暂时不可用。"} onRetry={() => void conversationsQuery.refetch()} />
      : <>
        {conversationsQuery.isError && <InlineError message={conversationsQuery.error instanceof Error ? conversationsQuery.error.message : "刷新没有完成。"} onRetry={() => void conversationsQuery.refetch()} />}
        <label className="admin-account-search admin-route-search"><Search size={16} aria-hidden="true" /><input type="search" aria-label="搜索会话" placeholder="搜索会话标题" value={query} onChange={event => setQuery(event.target.value)} /></label>
        {data.conversations.length === 0 ? <p className="admin-empty">还没有匹配的会话。</p> : <section className="admin-conversation-list" aria-label="会话列表">{data.conversations.map(item => <Link className="admin-conversation-card" key={item.id} to={conversationDetailPath(accountId, item.id)}><span className="admin-conversation-icon"><MessageSquare size={17} /></span><span className="admin-conversation-copy"><strong>{item.title}</strong><small>{modeLabel(item.mode)} · {item.run_count} 次运行 · {statusLabel(item.latest_status)}</small></span><time dateTime={item.updated_at}>{formatAdminDate(item.updated_at)}</time><ChevronRight size={17} /></Link>)}</section>}
      </>}
  </div>;
}
