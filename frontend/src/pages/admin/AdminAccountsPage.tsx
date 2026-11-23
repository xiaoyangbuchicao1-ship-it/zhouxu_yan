import { useState } from "react";
import { Link } from "react-router-dom";
import { AlertCircle, Check, ChevronLeft, ChevronRight, Search, Shield, Users } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { createAdminApi, AdminApiError, type AdminAccount, type AdminAccountUpdate } from "@/lib/admin-api";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { accountConversationPath } from "./admin-navigation";
import { useAdminOutlet } from "./AdminLayout";
import { InitialLoading, InlineError, LoadError, PageHeader, RefreshButton, formatAdminDate } from "./AdminShared";

export default function AdminAccountsPage() {
  const { token, ownAccountId } = useAdminOutlet();
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<"all" | "active" | "disabled">("all");
  const [role, setRole] = useState<"all" | "user" | "admin">("all");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<{ account: AdminAccount; update: AdminAccountUpdate } | null>(null);
  const [mutationError, setMutationError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [mutating, setMutating] = useState(false);
  const accountsQuery = useQuery({
    queryKey: ["admin", "accounts", query, status, role, page],
    queryFn: () => createAdminApi(token).accounts({ query, status, role, page, pageSize: 20 }),
    retry: false,
    placeholderData: previous => previous,
  });
  const accounts = accountsQuery.data?.accounts ?? [];
  async function confirmUpdate() {
    if (!selected || mutating) return;
    setMutating(true); setMutationError(null); setSuccess(null);
    try {
      const updated = await createAdminApi(token).updateAccount(selected.account.id, selected.update);
      setSelected(null); setSuccess(`已更新 ${updated.email}`);
      await accountsQuery.refetch();
    } catch (cause) {
      setMutationError(cause instanceof AdminApiError ? cause.message : cause instanceof Error ? cause.message : "这次调整没有完成。");
    } finally { setMutating(false); }
  }
  return <div className="admin-route-page">
    <PageHeader eyebrow="人员" title="账户" description="搜索、调整状态，查看会话。" action={<RefreshButton loading={accountsQuery.isFetching} onClick={() => void accountsQuery.refetch()} />} />
    {!accountsQuery.data && accountsQuery.isPending ? <InitialLoading label="正在读取账户…" />
      : !accountsQuery.data ? <LoadError message={accountsQuery.error instanceof Error ? accountsQuery.error.message : "账户暂时不可用。"} onRetry={() => void accountsQuery.refetch()} />
      : <>
        {accountsQuery.isError && <InlineError message={accountsQuery.error instanceof Error ? accountsQuery.error.message : "刷新没有完成。"} onRetry={() => void accountsQuery.refetch()} />}
        {success && <p className="admin-success" role="status"><Check size={16} />{success}</p>}
        <div className="admin-accounts-toolbar">
          <label className="admin-account-search admin-route-search"><Search size={16} aria-hidden="true" /><input type="search" aria-label="搜索账户" placeholder="搜索邮箱或称呼" value={query} onChange={event => { setQuery(event.target.value); setPage(1); }} /></label>
          <div className="admin-account-filters">
            <Select value={role} onValueChange={value => { setRole(value as typeof role); setPage(1); }}><SelectTrigger className="admin-filter-trigger" aria-label="角色筛选"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">全部角色</SelectItem><SelectItem value="user">普通用户</SelectItem><SelectItem value="admin">管理员</SelectItem></SelectContent></Select>
            <Select value={status} onValueChange={value => { setStatus(value as typeof status); setPage(1); }}><SelectTrigger className="admin-filter-trigger" aria-label="状态筛选"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="all">全部状态</SelectItem><SelectItem value="active">正常</SelectItem><SelectItem value="disabled">已停用</SelectItem></SelectContent></Select>
          </div>
        </div>
        {accounts.length === 0 ? <p className="admin-empty">没有匹配的账户。</p> : <AccountList accounts={accounts} ownId={ownAccountId} disabled={mutating || accountsQuery.isFetching} onRequest={account => setSelected(account)} />}
        {accountsQuery.data && <nav className="admin-pagination" aria-label="账户分页"><span>第 {accountsQuery.data.page} / {accountsQuery.data.total_pages} 页 · 共 {accountsQuery.data.total} 个</span><div><button className="admin-page-button" type="button" aria-label="上一页" disabled={accountsQuery.data.page <= 1} onClick={() => setPage(current => current - 1)}><ChevronLeft size={16} />上一页</button><button className="admin-page-button" type="button" aria-label="下一页" disabled={accountsQuery.data.page >= accountsQuery.data.total_pages} onClick={() => setPage(current => current + 1)}>下一页<ChevronRight size={16} /></button></div></nav>}
      </>}
    <AlertDialog open={Boolean(selected)} onOpenChange={open => { if (!open && !mutating) setSelected(null); }}><AlertDialogContent className="admin-dialog"><AlertDialogHeader><AlertDialogTitle>{selected ? (selected.update.status === "disabled" ? "停用这个账户？" : selected.update.status === "active" ? "启用这个账户？" : selected.update.role === "admin" ? "设为管理员？" : "改为普通用户？") : "确认调整？"}</AlertDialogTitle><AlertDialogDescription>{selected?.account.email}</AlertDialogDescription></AlertDialogHeader>{mutationError && <p className="admin-dialog-error" role="alert"><AlertCircle size={16} />{mutationError}</p>}<AlertDialogFooter><AlertDialogCancel disabled={mutating}>取消</AlertDialogCancel><AlertDialogAction disabled={mutating} onClick={event => { event.preventDefault(); void confirmUpdate(); }}>{mutating ? "处理中…" : "确认"}</AlertDialogAction></AlertDialogFooter></AlertDialogContent></AlertDialog>
  </div>;
}

function AccountList({ accounts, ownId, disabled, onRequest }: { accounts: AdminAccount[]; ownId: string; disabled: boolean; onRequest: (change: { account: AdminAccount; update: AdminAccountUpdate }) => void }) {
  return <section className="admin-account-list" aria-label="账户列表">
    {accounts.map(account => <article className="admin-account-card" key={account.id}>
      <div className="admin-account-card-main"><span className="admin-avatar" aria-hidden="true">{(account.display_name || account.email).slice(0, 1).toUpperCase()}</span><div><h2>{account.display_name || account.email}</h2>{account.display_name && <p>{account.email}</p>}</div></div>
      <div className="admin-account-card-meta"><span className={`admin-pill ${account.role === "admin" ? "is-admin" : ""}`}>{account.role === "admin" ? "管理员" : "普通用户"}</span><span className={`admin-pill ${account.status === "active" ? "is-active" : "is-disabled"}`}>{account.status === "active" ? "正常" : "已停用"}</span><time dateTime={account.created_at}>{formatAdminDate(account.created_at)}</time></div>
      <div className="admin-card-actions"><Link className="admin-card-link" to={accountConversationPath(account.id)}><Users size={15} />查看会话</Link><Link className="admin-card-link" to={`/admin/operations?account=${encodeURIComponent(account.id)}`}><Shield size={15} />能力授权</Link>{account.id !== ownId && <><button type="button" className="admin-row-action" disabled={disabled} onClick={() => onRequest({ account, update: { status: account.status === "active" ? "disabled" : "active" } })}>{account.status === "active" ? "停用" : "启用"}</button><button type="button" className="admin-row-action" disabled={disabled} onClick={() => onRequest({ account, update: { role: account.role === "admin" ? "user" : "admin" } })}>{account.role === "admin" ? "改为普通用户" : "设为管理员"}</button></>}</div>
    </article>)}
  </section>;
}
