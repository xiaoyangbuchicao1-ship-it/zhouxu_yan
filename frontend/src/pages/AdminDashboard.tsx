import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { AlertCircle, ArrowLeft, Check, ChevronLeft, ChevronRight, LoaderCircle, LogOut, RefreshCw, Search, ShieldCheck, UserRound, Users } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { revokeSession } from "@/lib/auth";
import { saveSession, useAuthSession } from "@/lib/auth-session";
import { AdminApiError, createAdminApi, loadAdminDashboard, type AdminAccount, type AdminAccountUpdate, type AdminAuditEvent, type AdminDashboardData } from "@/lib/admin-api";
import { accountChangeText, accountRoleLabel, accountStatusLabel, applyUpdatedAccount, auditActionLabel, auditChangeText, canEnterAdmin, selectAccountPage, type AccountFilters } from "@/lib/admin-ui";
import "./admin.css";

interface AccountChange {
  account: AdminAccount;
  update: AdminAccountUpdate;
}

export default function AdminDashboard() {
  const navigate = useNavigate();
  const session = useAuthSession();
  const token = session?.session.token;
  const allowed = canEnterAdmin(session);
  const [data, setData] = useState<AdminDashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [selected, setSelected] = useState<AccountChange | null>(null);
  const [updating, setUpdating] = useState(false);
  const [mutationError, setMutationError] = useState<string | null>(null);
  const [filters, setFilters] = useState<AccountFilters>({ query: "", status: "all", role: "all" });
  const [page, setPage] = useState(1);
  const actionTriggerRef = useRef<HTMLElement | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  const reportError = useCallback((cause: unknown, fallback: string) => {
    if (cause instanceof AdminApiError && (cause.status === 401 || cause.status === 403)) {
      setData(null);
      setSelected(null);
      saveSession(null);
      const notice = cause.status === 403 ? "当前账户没有管理员权限，请使用管理员账户登录。" : "登录状态已失效，请重新登录。";
      navigate("/admin/login", { replace: true, state: { notice } });
    } else {
      setError(cause instanceof Error ? cause.message : fallback);
    }
  }, [navigate]);

  useEffect(() => {
    const previousTitle = document.title;
    document.title = "管理台 · 舟序";
    return () => { document.title = previousTitle; };
  }, []);

  useEffect(() => {
    if (!token) { navigate("/admin/login", { replace: true }); return; }
    if (!allowed) { setLoading(false); return; }
    const controller = new AbortController();
    setData(null);
    setLoading(true);
    setError(null);
    loadAdminDashboard(token, controller.signal).then(next => {
      if (!controller.signal.aborted) setData(next);
    }).catch(cause => {
      if (!controller.signal.aborted) reportError(cause, "暂时无法加载管理台。");
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [allowed, navigate, reportError, token]);

  async function refresh() {
    if (!token || refreshing || updating) return;
    setRefreshing(true);
    setError(null);
    try { setData(await loadAdminDashboard(token)); }
    catch (cause) { reportError(cause, "刷新没有完成，请重试。"); }
    finally { setRefreshing(false); }
  }

  function requestChange(account: AdminAccount, update: AdminAccountUpdate) {
    actionTriggerRef.current = document.activeElement as HTMLElement;
    setMutationError(null);
    setSelected({ account, update });
  }

  async function confirmUpdate() {
    if (!token || !selected || updating) return;
    setUpdating(true);
    setMutationError(null);
    let updated: AdminAccount;
    try {
      updated = await createAdminApi(token).updateAccount(selected.account.id, selected.update);
    } catch (cause) {
      if (cause instanceof AdminApiError && (cause.status === 401 || cause.status === 403)) reportError(cause, "账户调整没有完成。");
      else setMutationError(cause instanceof Error ? cause.message : "账户调整没有完成。");
      setUpdating(false);
      return;
    }

    // A successful PATCH is the result. Refreshing its projections is a separate read.
    setData(current => current ? applyUpdatedAccount(current, updated) : current);
    setSuccess(`账户已更新：${updated.email}`);
    setSelected(null);
    setUpdating(false);
    setRefreshing(true);
    setError(null);
    try { setData(await loadAdminDashboard(token)); }
    catch (cause) {
      if (cause instanceof AdminApiError && (cause.status === 401 || cause.status === 403)) reportError(cause, "刷新没有完成。");
      else setError("账户已更新，业务记录暂未刷新。请重试刷新。");
    } finally { setRefreshing(false); }
  }

  async function logout() {
    if (!token) return;
    try { await revokeSession(token); }
    catch (cause) {
      if (!(cause instanceof Error && "status" in cause && cause.status === 401)) {
        setError("退出没有完成，请重试。");
        return;
      }
    }
    saveSession(null);
    navigate("/admin/login", { replace: true });
  }

  function changeFilters(update: Partial<AccountFilters>) {
    setFilters(current => ({ ...current, ...update }));
    setPage(1);
  }

  const accountPage = useMemo(() => data ? selectAccountPage(data.accounts, filters, page) : null, [data, filters, page]);
  if (!session) return null;
  const denied = !allowed;
  const busy = updating || refreshing;

  return (
    <main className="admin-page">
      <header className="admin-header">
        <Link className="admin-header-brand" to="/workspace" aria-label="舟序，返回工作台"><img src="/zhou-xu-mark.png" alt="" width="31" height="31" /><span>Zhouxu<span aria-hidden="true">.</span></span><b>管理台</b></Link>
        <div className="admin-header-actions"><span className="admin-identity"><ShieldCheck size={15} />{session.account.display_name || session.account.email}</span><button className="admin-icon-button" type="button" onClick={logout} aria-label="退出登录"><LogOut size={16} /></button></div>
      </header>
      <div className="admin-content">
        {denied ? (
          <section className="admin-state" aria-labelledby="admin-denied-title"><ShieldCheck size={28} /><h1 id="admin-denied-title">需要管理员账户</h1><p>当前账户没有管理权限。</p><div className="admin-state-actions"><Link className="admin-refresh" to="/workspace"><ArrowLeft size={16} />返回工作台</Link><Link className="admin-primary-button" to="/admin/login">管理员登录</Link></div></section>
        ) : <>
          <div className="admin-title-row"><div><p className="admin-kicker">业务管理</p><h1>账户与记录</h1></div><button className="admin-refresh" type="button" onClick={refresh} disabled={loading || busy}>{refreshing ? <LoaderCircle className="admin-spin" size={16} /> : <RefreshCw size={16} />}刷新</button></div>
          {loading ? <div className="admin-loading" role="status"><LoaderCircle className="admin-spin" size={21} />正在加载管理台…</div> : !data ? (
            <section className="admin-state" aria-labelledby="admin-load-error-title"><AlertCircle size={28} /><h2 id="admin-load-error-title">暂时无法加载</h2><p role="alert">{error}</p><button className="admin-primary-button" onClick={refresh} disabled={refreshing}>{refreshing ? "正在重试…" : "重新加载"}</button></section>
          ) : <>
            {success && <p className="admin-success" role="status"><Check size={16} />{success}</p>}
            {error && <div className="admin-alert" role="alert"><AlertCircle size={17} /><span>{error}</span><button onClick={refresh} disabled={busy}>重试刷新</button></div>}
            <section className="admin-metrics" aria-label="账户概览">
              <Metric label="全部账户" value={data.overview.total_accounts} icon={<Users size={17} />} />
              <Metric label="正常账户" value={data.overview.active_accounts} icon={<Check size={17} />} />
              <Metric label="已停用" value={data.overview.disabled_accounts} icon={<UserRound size={17} />} />
              <Metric label="管理员" value={data.overview.admin_accounts} icon={<ShieldCheck size={17} />} />
            </section>
            <Tabs defaultValue="accounts" className="admin-tabs">
              <TabsList className="admin-tab-list" aria-label="管理内容"><TabsTrigger value="accounts" className="admin-tab-trigger">人员</TabsTrigger><TabsTrigger value="audit" className="admin-tab-trigger">业务记录</TabsTrigger></TabsList>
              <TabsContent value="accounts" className="admin-tab-content">
                <div className="admin-accounts-toolbar">
                  <label className="admin-account-search"><Search size={16} aria-hidden="true" /><input ref={searchRef} type="search" aria-label="搜索账户（邮箱或称呼）" placeholder="搜索账户（邮箱或称呼）" value={filters.query} onChange={event => changeFilters({ query: event.target.value })} /></label>
                  <div className="admin-account-filters">
                    <Select value={filters.role} onValueChange={(role: AccountFilters["role"]) => changeFilters({ role })}><SelectTrigger className="admin-filter-trigger" aria-label="角色筛选"><SelectValue /></SelectTrigger><SelectContent className="admin-select-menu"><SelectItem value="all">全部角色</SelectItem><SelectItem value="user">普通用户</SelectItem><SelectItem value="admin">管理员</SelectItem></SelectContent></Select>
                    <Select value={filters.status} onValueChange={(status: AccountFilters["status"]) => changeFilters({ status })}><SelectTrigger className="admin-filter-trigger" aria-label="状态筛选"><SelectValue /></SelectTrigger><SelectContent className="admin-select-menu"><SelectItem value="all">全部状态</SelectItem><SelectItem value="active">正常</SelectItem><SelectItem value="disabled">已停用</SelectItem></SelectContent></Select>
                  </div>
                </div>
                <p className="admin-result-count" aria-live="polite">共 {accountPage.total} 个账户</p>
                {accountPage.total === 0 ? <p className="admin-empty">没有匹配的账户。</p> : <>
                  <div className="admin-table-wrap"><table className="admin-table"><caption className="admin-sr-only">人员账户</caption><thead><tr><th scope="col">账户</th><th scope="col">角色</th><th scope="col">状态</th><th scope="col">加入时间</th><th scope="col"><span className="admin-sr-only">操作</span></th></tr></thead><tbody>{accountPage.items.map(account => <AccountRow key={account.id} account={account} ownId={session.account.id} disabled={busy} onRequest={update => requestChange(account, update)} />)}</tbody></table></div>
                  <ul className="admin-mobile-list" aria-label="人员账户">{accountPage.items.map(account => <li className="admin-mobile-account" key={account.id}><div className="admin-mobile-account-top"><AccountIdentity account={account} /><AccountBadges account={account} /></div><div className="admin-mobile-account-bottom"><time dateTime={account.created_at}>{formatDate(account.created_at)}</time><AccountActions account={account} ownId={session.account.id} disabled={busy} onRequest={update => requestChange(account, update)} /></div></li>)}</ul>
                </>}
                <nav className="admin-pagination" aria-label="账户分页"><span>{accountPage.start}–{accountPage.end} / {accountPage.total}</span><div><button className="admin-page-button" aria-label="上一页" disabled={accountPage.page === 1} onClick={() => setPage(accountPage.page - 1)}><ChevronLeft size={16} /><span>上一页</span></button><span className="admin-page-number">{accountPage.page} / {accountPage.totalPages}</span><button className="admin-page-button" aria-label="下一页" disabled={accountPage.page === accountPage.totalPages} onClick={() => setPage(accountPage.page + 1)}><span>下一页</span><ChevronRight size={16} /></button></div></nav>
              </TabsContent>
              <TabsContent value="audit" className="admin-tab-content"><h2 className="admin-sr-only">业务记录</h2><div className="admin-audit-list">{data.audit.length === 0 ? <p className="admin-empty">还没有业务记录。</p> : data.audit.map(event => <AuditRow key={event.id} event={event} />)}</div></TabsContent>
            </Tabs>
          </>}
        </>}
      </div>
      <AlertDialog open={Boolean(selected)} onOpenChange={open => { if (!open && !updating) setSelected(null); }}>
        <AlertDialogContent className="admin-dialog" onCloseAutoFocus={event => { event.preventDefault(); const trigger = actionTriggerRef.current; if (trigger?.isConnected && !trigger.matches(":disabled")) trigger.focus(); else searchRef.current?.focus(); }}><AlertDialogHeader><AlertDialogTitle>{selected ? `${accountChangeText(selected.update)}？` : "确认调整？"}</AlertDialogTitle><AlertDialogDescription>{selected?.account.email}</AlertDialogDescription></AlertDialogHeader>{mutationError && <p className="admin-dialog-error" role="alert">{mutationError}</p>}<AlertDialogFooter><AlertDialogCancel className="admin-dialog-cancel" disabled={updating}>取消</AlertDialogCancel><AlertDialogAction className="admin-dialog-confirm" disabled={updating} onClick={event => { event.preventDefault(); void confirmUpdate(); }}>{updating ? "处理中…" : "确认"}</AlertDialogAction></AlertDialogFooter></AlertDialogContent>
      </AlertDialog>
    </main>
  );
}

function Metric({ label, value, icon }: { label: string; value: number; icon: ReactNode }) {
  return <div className="admin-metric"><span className="admin-metric-icon">{icon}</span><span className="admin-metric-label">{label}</span><strong>{value.toLocaleString("zh-CN")}</strong></div>;
}

function AccountIdentity({ account }: { account: AdminAccount }) {
  return <div className="admin-account-cell"><span className="admin-avatar" aria-hidden="true">{(account.display_name || account.email).slice(0, 1).toUpperCase()}</span><span><strong>{account.display_name || account.email}</strong>{account.display_name && <small>{account.email}</small>}</span></div>;
}

function AccountBadges({ account }: { account: AdminAccount }) {
  return <div className="admin-account-badges"><span className={`admin-pill ${account.role === "admin" ? "is-admin" : ""}`}>{accountRoleLabel(account.role)}</span><span className={`admin-pill ${account.status === "active" ? "is-active" : "is-disabled"}`}>{accountStatusLabel(account.status)}</span></div>;
}

function AccountActions({ account, ownId, disabled, onRequest }: { account: AdminAccount; ownId: string; disabled: boolean; onRequest: (update: AdminAccountUpdate) => void }) {
  return <div className="admin-row-actions">{account.id === ownId ? <span className="admin-self-note">当前账户</span> : <><button type="button" className="admin-row-action" disabled={disabled} onClick={() => onRequest({ status: account.status === "active" ? "disabled" : "active" })}>{account.status === "active" ? "停用" : "启用"}</button><button type="button" className="admin-row-action" disabled={disabled} onClick={() => onRequest({ role: account.role === "admin" ? "user" : "admin" })}>{account.role === "admin" ? "改为普通用户" : "设为管理员"}</button></>}</div>;
}

function AccountRow({ account, ownId, disabled, onRequest }: { account: AdminAccount; ownId: string; disabled: boolean; onRequest: (update: AdminAccountUpdate) => void }) {
  return <tr><td><AccountIdentity account={account} /></td><td><span className={`admin-pill ${account.role === "admin" ? "is-admin" : ""}`}>{accountRoleLabel(account.role)}</span></td><td><span className={`admin-pill ${account.status === "active" ? "is-active" : "is-disabled"}`}>{accountStatusLabel(account.status)}</span></td><td><time dateTime={account.created_at}>{formatDate(account.created_at)}</time></td><td><AccountActions account={account} ownId={ownId} disabled={disabled} onRequest={onRequest} /></td></tr>;
}

function AuditRow({ event }: { event: AdminAuditEvent }) {
  return <article className="admin-audit-row"><span className="admin-audit-icon" aria-hidden="true"><ShieldCheck size={16} /></span><div className="admin-audit-copy"><div className="admin-audit-summary"><h3>{auditActionLabel(event.action)}</h3><strong className="admin-audit-change">{auditChangeText(event)}</strong></div><dl className="admin-audit-people"><div><dt>操作人</dt><dd>{event.actor_email}</dd></div><div><dt>账户</dt><dd>{event.target_email ?? "原账户已不存在"}</dd></div></dl></div><time dateTime={event.created_at}>{formatDate(event.created_at)}</time></article>;
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}
