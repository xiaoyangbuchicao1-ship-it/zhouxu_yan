import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Search, Users } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { createAdminApi, type AdminAccount } from "@/lib/admin-api";
import { accountConversationPath } from "./admin-navigation";
import { useAdminOutlet } from "./AdminLayout";
import { InitialLoading, InlineError, LoadError, PageHeader, RefreshButton, formatAdminDate } from "./AdminShared";

export default function AdminAccountsPage() {
  const { token } = useAdminOutlet();
  const [query, setQuery] = useState("");
  const accountsQuery = useQuery({
    queryKey: ["admin", "accounts"],
    queryFn: () => createAdminApi(token).accounts(),
    retry: false,
    placeholderData: previous => previous,
  });
  const accounts = useMemo(() => {
    const value = query.trim().toLowerCase();
    if (!value) return accountsQuery.data?.accounts ?? [];
    return (accountsQuery.data?.accounts ?? []).filter(item => `${item.email} ${item.display_name}`.toLowerCase().includes(value));
  }, [accountsQuery.data, query]);
  return <div className="admin-route-page">
    <PageHeader eyebrow="人员" title="账户" description="查看人员状态和会话记录。" action={<RefreshButton loading={accountsQuery.isFetching} onClick={() => void accountsQuery.refetch()} />} />
    {!accountsQuery.data && accountsQuery.isPending ? <InitialLoading label="正在读取账户…" />
      : !accountsQuery.data ? <LoadError message={accountsQuery.error instanceof Error ? accountsQuery.error.message : "账户暂时不可用。"} onRetry={() => void accountsQuery.refetch()} />
      : <>
        {accountsQuery.isError && <InlineError message={accountsQuery.error instanceof Error ? accountsQuery.error.message : "刷新没有完成。"} onRetry={() => void accountsQuery.refetch()} />}
        <label className="admin-account-search admin-route-search"><Search size={16} aria-hidden="true" /><input type="search" aria-label="搜索账户" placeholder="搜索邮箱或称呼" value={query} onChange={event => setQuery(event.target.value)} /></label>
        {accounts.length === 0 ? <p className="admin-empty">没有匹配的账户。</p> : <AccountList accounts={accounts} />}
      </>}
  </div>;
}

function AccountList({ accounts }: { accounts: AdminAccount[] }) {
  return <section className="admin-account-list" aria-label="账户列表">
    {accounts.map(account => <article className="admin-account-card" key={account.id}>
      <div className="admin-account-card-main"><span className="admin-avatar" aria-hidden="true">{(account.display_name || account.email).slice(0, 1).toUpperCase()}</span><div><h2>{account.display_name || account.email}</h2>{account.display_name && <p>{account.email}</p>}</div></div>
      <div className="admin-account-card-meta"><span className={`admin-pill ${account.role === "admin" ? "is-admin" : ""}`}>{account.role === "admin" ? "管理员" : "普通用户"}</span><span className={`admin-pill ${account.status === "active" ? "is-active" : "is-disabled"}`}>{account.status === "active" ? "正常" : "已停用"}</span><time dateTime={account.created_at}>{formatAdminDate(account.created_at)}</time></div>
      <Link className="admin-card-link" to={accountConversationPath(account.id)}><Users size={15} />查看会话</Link>
    </article>)}
  </section>;
}
