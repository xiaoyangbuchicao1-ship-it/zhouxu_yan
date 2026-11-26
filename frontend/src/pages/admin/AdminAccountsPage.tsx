import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { AlertCircle, Check, ChevronLeft, ChevronRight, MessagesSquare, Search, Trash2 } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { createAdminApi, type AdminAccount, type AdminAccountUpdate, type AdminCapability } from "@/lib/admin-api";
import { accountRoleLabel, accountStatusLabel } from "@/lib/admin-ui";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { accountConversationPath } from "./admin-navigation";
import { MODEL_POLICY_LABELS, SKILL_POLICY_LABELS, useCapabilities } from "./admin-capabilities";
import { useAdminOutlet } from "./AdminLayout";
import { InitialLoading, InlineError, LoadError, PageHeader, RefreshButton, StatusPill, formatAdminDate } from "./AdminShared";

interface PendingChange {
  account: AdminAccount;
  update: AdminAccountUpdate;
}

const MODEL_POLICIES = Object.keys(MODEL_POLICY_LABELS) as AdminCapability["model_policy"][];
const SKILL_POLICIES = Object.keys(SKILL_POLICY_LABELS) as AdminCapability["skill_policy"][];

function confirmTitle(update: AdminAccountUpdate): string {
  if (update.status === "disabled") return "停用这个账户？";
  if (update.status === "active") return "启用这个账户？";
  return update.role === "admin" ? "设为管理员？" : "改为普通用户？";
}

export default function AdminAccountsPage() {
  const { token, ownAccountId } = useAdminOutlet();
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<"all" | "active" | "disabled">("all");
  const [role, setRole] = useState<"all" | "user" | "admin">("all");
  const [page, setPage] = useState(1);
  const [managed, setManaged] = useState<AdminAccount | null>(null);
  const [pending, setPending] = useState<PendingChange | null>(null);
  const [deleting, setDeleting] = useState<AdminAccount | null>(null);
  const [mutationError, setMutationError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [mutating, setMutating] = useState(false);
  const accountsQuery = useQuery({
    queryKey: ["admin", "accounts", query, status, role, page],
    queryFn: () => createAdminApi(token).accounts({ query, status, role, page, pageSize: 20 }),
    retry: false,
    placeholderData: previous => previous,
  });
  const data = accountsQuery.data;
  const accounts = data?.accounts ?? [];
  const errorMessage = accountsQuery.error instanceof Error ? accountsQuery.error.message : "账户没有加载出来。";

  async function confirmUpdate() {
    if (!pending || mutating) return;
    setMutating(true);
    setMutationError(null);
    setSuccess(null);
    try {
      const updated = await createAdminApi(token).updateAccount(pending.account.id, pending.update);
      setPending(null);
      setManaged(current => current?.id === updated.id ? updated : current);
      setSuccess(`已更新 ${updated.email}`);
      await accountsQuery.refetch();
    } catch (cause) {
      setMutationError(cause instanceof Error ? cause.message : "这次调整没有完成。");
    } finally {
      setMutating(false);
    }
  }

  async function confirmDelete() {
    if (!deleting || mutating) return;
    setMutating(true);
    setMutationError(null);
    setSuccess(null);
    try {
      const removed = await createAdminApi(token).deleteAccount(deleting.id);
      setDeleting(null);
      setManaged(current => current?.id === removed.account_id ? null : current);
      setSuccess(`已永久删除 ${removed.email}`);
      await accountsQuery.refetch();
    } catch (cause) {
      setMutationError(cause instanceof Error ? cause.message : "删除账户失败，请稍后重试。");
    } finally {
      setMutating(false);
    }
  }

  return (
    <div className="admin-route-page">
      <PageHeader title="账户" action={<RefreshButton loading={accountsQuery.isFetching} onClick={() => void accountsQuery.refetch()} />} />
      {!data && accountsQuery.isPending ? <InitialLoading label="正在读取账户…" />
        : !data ? <LoadError message={errorMessage} onRetry={() => void accountsQuery.refetch()} />
          : <>
            {accountsQuery.isError && <InlineError message={errorMessage} onRetry={() => void accountsQuery.refetch()} />}
            {success && <p className="admin-success" role="status"><Check size={16} />{success}</p>}
            <div className="admin-toolbar">
              <label className="admin-search">
                <Search size={15} aria-hidden="true" />
                <input type="search" aria-label="搜索账户" placeholder="搜索邮箱或称呼" value={query} onChange={event => { setQuery(event.target.value); setPage(1); }} />
              </label>
              <div className="admin-toolbar-filters">
                <Select value={role} onValueChange={value => { setRole(value as typeof role); setPage(1); }}>
                  <SelectTrigger className="admin-filter-trigger" aria-label="角色筛选"><SelectValue /></SelectTrigger>
                  <SelectContent className="admin-select-menu">
                    <SelectItem value="all">全部角色</SelectItem>
                    <SelectItem value="user">普通用户</SelectItem>
                    <SelectItem value="admin">管理员</SelectItem>
                  </SelectContent>
                </Select>
                <Select value={status} onValueChange={value => { setStatus(value as typeof status); setPage(1); }}>
                  <SelectTrigger className="admin-filter-trigger" aria-label="状态筛选"><SelectValue /></SelectTrigger>
                  <SelectContent className="admin-select-menu">
                    <SelectItem value="all">全部状态</SelectItem>
                    <SelectItem value="active">正常</SelectItem>
                    <SelectItem value="disabled">已停用</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            {accounts.length === 0 ? <p className="admin-empty">没有匹配的账户。</p> : (
              <div className="admin-table-wrap">
                <table className="admin-table">
                  <thead>
                    <tr><th>账户</th><th>角色</th><th>状态</th><th>注册 IP</th><th>注册时间</th><th className="is-num">操作</th></tr>
                  </thead>
                  <tbody>
                    {accounts.map(account => (
                      <tr key={account.id}>
                        <td className="admin-cell-wide">
                          <strong className="admin-cell-title">{account.display_name || account.email}</strong>
                          {account.display_name && <small className="admin-cell-sub">{account.email}</small>}
                        </td>
                        <td className="admin-cell-nowrap">{accountRoleLabel(account.role)}</td>
                        <td><StatusPill tone={account.status === "active" ? "ok" : "muted"}>{accountStatusLabel(account.status)}</StatusPill></td>
                        <td className="admin-cell-muted admin-cell-nowrap">{account.register_ip || "—"}</td>
                        <td className="admin-cell-muted admin-cell-nowrap"><time dateTime={account.created_at}>{formatAdminDate(account.created_at)}</time></td>
                        <td className="is-num">
                          <div className="admin-row-actions">
                            <Link className="admin-row-action" to={accountConversationPath(account.id)}>会话</Link>
                            <button type="button" className="admin-row-action" onClick={() => setManaged(account)}>管理</button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <nav className="admin-pagination" aria-label="账户分页">
              <span>第 {data.page} / {data.total_pages} 页 · 共 {data.total} 个</span>
              <div>
                <button className="admin-page-button" type="button" disabled={data.page <= 1} onClick={() => setPage(current => current - 1)}><ChevronLeft size={16} />上一页</button>
                <button className="admin-page-button" type="button" disabled={data.page >= data.total_pages} onClick={() => setPage(current => current + 1)}>下一页<ChevronRight size={16} /></button>
              </div>
            </nav>
          </>}

      <Sheet open={Boolean(managed)} onOpenChange={open => { if (!open && !pending && !deleting) setManaged(null); }}>
        <SheetContent side="right" className="admin-dialog admin-drawer">
          {managed && (
            <AccountDrawer
              key={managed.id}
              account={managed}
              isSelf={managed.id === ownAccountId}
              token={token}
              busy={mutating}
              onRequest={update => { setMutationError(null); setPending({ account: managed, update }); }}
              onRequestDelete={() => { setMutationError(null); setDeleting(managed); }}
            />
          )}
        </SheetContent>
      </Sheet>

      <AlertDialog open={Boolean(pending)} onOpenChange={open => { if (!open && !mutating) setPending(null); }}>
        <AlertDialogContent className="admin-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle>{pending ? confirmTitle(pending.update) : "确认调整？"}</AlertDialogTitle>
            <AlertDialogDescription>{pending?.account.email}</AlertDialogDescription>
          </AlertDialogHeader>
          {mutationError && <p className="admin-dialog-error" role="alert"><AlertCircle size={16} />{mutationError}</p>}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={mutating}>取消</AlertDialogCancel>
            <AlertDialogAction disabled={mutating} onClick={event => { event.preventDefault(); void confirmUpdate(); }}>{mutating ? "处理中…" : "确认"}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={Boolean(deleting)} onOpenChange={open => { if (!open && !mutating) setDeleting(null); }}>
        <AlertDialogContent className="admin-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle>永久删除这个账户？</AlertDialogTitle>
            <AlertDialogDescription>
              将永久删除 {deleting?.email} 及其全部会话、任务记录、上传资料、生成文件和上传技能，删除后无法恢复。
            </AlertDialogDescription>
          </AlertDialogHeader>
          {mutationError && <p className="admin-dialog-error" role="alert"><AlertCircle size={16} />{mutationError}</p>}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={mutating}>取消</AlertDialogCancel>
            <AlertDialogAction className="admin-danger-button" disabled={mutating} onClick={event => { event.preventDefault(); void confirmDelete(); }}>
              {mutating ? "删除中…" : "永久删除"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function formatUsedBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 ** 3)).toFixed(2)} GB`;
}

function StorageQuotaSection({ capability, saving, onUpdate }: {
  capability: AdminCapability;
  saving: boolean;
  onUpdate: (change: { material_quota_mb?: number; clear_material_quota?: boolean }) => Promise<boolean>;
}) {
  const initialMode = capability.material_quota_mb === null || capability.material_quota_mb === undefined
    ? "default"
    : capability.material_quota_mb === 0
      ? "unlimited"
      : "custom";
  const [mode, setMode] = useState<"default" | "custom" | "unlimited">(initialMode);
  const [mbText, setMbText] = useState(
    capability.material_quota_mb && capability.material_quota_mb > 0 ? String(capability.material_quota_mb) : "512",
  );

  function selectMode(next: "default" | "custom" | "unlimited") {
    setMode(next);
    if (next === "default" && initialMode !== "default") {
      void onUpdate({ clear_material_quota: true });
    } else if (next === "unlimited" && initialMode !== "unlimited") {
      void onUpdate({ material_quota_mb: 0 });
    }
  }

  function saveCustom() {
    const parsed = Number.parseInt(mbText.trim(), 10);
    if (!Number.isFinite(parsed) || parsed < 1 || parsed > 1_048_576) return;
    void onUpdate({ material_quota_mb: parsed });
  }

  return (
    <section className="admin-drawer-section">
      <h3>存储空间</h3>
      <div className="admin-drawer-row">
        <span>已上传</span>
        <strong>{formatUsedBytes(capability.material_used_bytes ?? 0)}</strong>
        <small className="admin-drawer-note">
          {capability.material_quota_mb == null
            ? "跟随系统默认"
            : capability.material_quota_mb === 0
              ? "不限制"
              : `上限 ${capability.material_quota_mb} MB`}
        </small>
      </div>
      <div className="admin-segment" role="radiogroup" aria-label="存储配额模式">
        <button type="button" role="radio" aria-checked={mode === "default"} className={mode === "default" ? "is-active" : ""} disabled={saving} onClick={() => selectMode("default")}>
          跟随默认
        </button>
        <button type="button" role="radio" aria-checked={mode === "custom"} className={mode === "custom" ? "is-active" : ""} disabled={saving} onClick={() => selectMode("custom")}>
          单独限制
        </button>
        <button type="button" role="radio" aria-checked={mode === "unlimited"} className={mode === "unlimited" ? "is-active" : ""} disabled={saving} onClick={() => selectMode("unlimited")}>
          不限制
        </button>
      </div>
      {mode === "custom" && (
        <div className="admin-quota-form">
          <input
            type="number"
            min={1}
            max={1048576}
            step={64}
            aria-label="单人附件上限 (MB)"
            value={mbText}
            disabled={saving}
            onChange={e => setMbText(e.target.value)}
          />
          <span>MB</span>
          <button
            type="button"
            className="admin-primary-button"
            disabled={saving || !Number.isFinite(Number.parseInt(mbText, 10)) || Number.parseInt(mbText, 10) < 1}
            onClick={saveCustom}
          >
            {saving ? "保存中…" : "保存"}
          </button>
        </div>
      )}
    </section>
  );
}

function AccountDrawer({ account, isSelf, token, busy, onRequest, onRequestDelete }: {
  account: AdminAccount;
  isSelf: boolean;
  token: string;
  busy: boolean;
  onRequest: (update: AdminAccountUpdate) => void;
  onRequestDelete: () => void;
}) {
  const api = useMemo(() => createAdminApi(token), [token]);
  const capabilities = useCapabilities(token);
  const directory = useQuery({ queryKey: ["admin", "model-directory"], queryFn: ({ signal }) => api.modelDirectory(signal), retry: false });
  const [draft, setDraft] = useState<string[] | null>(null);
  const capability = capabilities.query.data?.accounts.find(item => item.account_id === account.id);
  const models = directory.data?.models ?? [];

  const activePolicy = draft !== null ? "allowlist" : capability?.model_policy;
  const checked = draft ?? (capability?.model_policy === "allowlist" ? capability.allowed_models : null);
  const draftChanged = draft !== null && capability !== undefined
    && (capability.model_policy !== "allowlist" || draft.length !== capability.allowed_models.length || draft.some(id => !capability.allowed_models.includes(id)));

  function choosePolicy(policy: AdminCapability["model_policy"]) {
    if (!capability) return;
    if (policy === "allowlist") {
      setDraft([...capability.allowed_models]);
      return;
    }
    setDraft(null);
    if (policy !== capability.model_policy) void capabilities.update(account.id, { model_policy: policy });
  }

  function toggleModel(id: string) {
    const base = checked ?? [];
    setDraft(base.includes(id) ? base.filter(item => item !== id) : [...base, id]);
  }

  async function saveAllowlist() {
    if (!draft?.length) return;
    if (await capabilities.update(account.id, { model_policy: "allowlist", allowed_models: draft })) setDraft(null);
  }

  return (
    <>
      <header className="admin-drawer-head">
        <SheetTitle>{account.display_name || account.email}</SheetTitle>
        <SheetDescription>{account.email}</SheetDescription>
      </header>

      <div className="admin-drawer-body">
        <section className="admin-drawer-section">
          <h3>账户</h3>
          <div className="admin-drawer-row">
            <span>状态</span>
            <StatusPill tone={account.status === "active" ? "ok" : "muted"}>{accountStatusLabel(account.status)}</StatusPill>
            {!isSelf && (
              <button type="button" className="admin-row-action" disabled={busy} onClick={() => onRequest({ status: account.status === "active" ? "disabled" : "active" })}>
                {account.status === "active" ? "停用" : "启用"}
              </button>
            )}
          </div>
          <div className="admin-drawer-row">
            <span>角色</span>
            <strong>{accountRoleLabel(account.role)}</strong>
            {!isSelf && (
              <button type="button" className="admin-row-action" disabled={busy} onClick={() => onRequest({ role: account.role === "admin" ? "user" : "admin" })}>
                {account.role === "admin" ? "改为普通用户" : "设为管理员"}
              </button>
            )}
          </div>
          <div className="admin-drawer-row">
            <span>注册 IP</span>
            <code style={{ fontSize: "12px", color: "var(--text-muted)" }}>{account.register_ip || "未知"}</code>
          </div>
          {isSelf && <p className="admin-drawer-note">这是你自己的账户，状态和角色不能自己改。</p>}
        </section>

        {capabilities.query.isPending ? <InitialLoading label="正在读取权限…" />
          : !capability ? <InlineError message={capabilities.query.error instanceof Error ? capabilities.query.error.message : "权限没有加载出来。"} onRetry={() => void capabilities.query.refetch()} />
            : <>
              {capabilities.error && <p className="admin-form-error" role="alert">{capabilities.error}</p>}
              <section className="admin-drawer-section">
                <h3>可用模型</h3>
                <div className="admin-segment" role="radiogroup" aria-label="可用模型">
                  {MODEL_POLICIES.map(policy => (
                    <button key={policy} type="button" role="radio" aria-checked={activePolicy === policy} className={activePolicy === policy ? "is-active" : ""} disabled={capabilities.saving} onClick={() => choosePolicy(policy)}>
                      {MODEL_POLICY_LABELS[policy]}
                    </button>
                  ))}
                </div>
                {checked && (
                  <>
                    {directory.isPending ? <InitialLoading label="正在读取模型…" />
                      : directory.isError ? <InlineError message={directory.error instanceof Error ? directory.error.message : "模型没有加载出来。"} onRetry={() => void directory.refetch()} />
                        : models.length === 0 ? <p className="admin-drawer-note">还没有模型，先到「模型」页添加。</p> : (
                          <ul className="admin-check-list">
                            {models.map(model => (
                              <li key={model.profile_id}>
                                <label className="admin-check">
                                  <input type="checkbox" checked={checked.includes(model.profile_id)} disabled={capabilities.saving} onChange={() => toggleModel(model.profile_id)} />
                                  <span>{model.label}</span>
                                  {!model.enabled && <small>已停用</small>}
                                  {model.access_scope === "admin_only" && <small>仅管理员</small>}
                                </label>
                              </li>
                            ))}
                          </ul>
                        )}
                    {draftChanged && (
                      <div className="admin-drawer-actions">
                        {draft?.length === 0 && <span className="admin-drawer-note">至少选一个模型。</span>}
                        <button type="button" className="admin-row-action" disabled={capabilities.saving} onClick={() => setDraft(null)}>取消</button>
                        <button type="button" className="admin-primary-button" disabled={capabilities.saving || !draft?.length} onClick={() => void saveAllowlist()}>
                          {capabilities.saving ? "保存中…" : "保存"}
                        </button>
                      </div>
                    )}
                  </>
                )}
              </section>

              <section className="admin-drawer-section">
                <h3>技能</h3>
                <div className="admin-drawer-row">
                  <span>可用技能</span>
                  <Select
                    value={capability.skill_policy}
                    disabled={capabilities.saving}
                    onValueChange={value => void capabilities.update(account.id, { skill_policy: value as AdminCapability["skill_policy"] })}
                  >
                    <SelectTrigger className="admin-drawer-select" aria-label="可用技能"><SelectValue /></SelectTrigger>
                    <SelectContent className="admin-select-menu">
                      {SKILL_POLICIES.map(policy => <SelectItem key={policy} value={policy}>{SKILL_POLICY_LABELS[policy]}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
                <div className="admin-drawer-row">
                  <span>上传技能</span>
                  <label className="admin-check">
                    <input type="checkbox" checked={capability.can_upload_skill} disabled={capabilities.saving} onChange={event => void capabilities.update(account.id, { can_upload_skill: event.target.checked })} />
                    允许
                  </label>
                </div>
              </section>

              <StorageQuotaSection
                key={`${account.id}-${capability.material_quota_mb ?? "default"}`}
                capability={capability}
                saving={capabilities.saving}
                onUpdate={change => capabilities.update(account.id, change)}
              />
            </>}
      </div>

      <footer className="admin-drawer-foot">
        <Link className="admin-row-action" to={accountConversationPath(account.id)}><MessagesSquare size={15} />查看会话</Link>
        {!isSelf && (
          <button type="button" className="admin-row-action is-danger" disabled={busy} onClick={onRequestDelete}>
            <Trash2 size={15} />删除账户
          </button>
        )}
      </footer>
    </>
  );
}
