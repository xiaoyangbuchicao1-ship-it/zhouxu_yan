import { useMemo, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { AlertCircle, Check, ChevronLeft, ChevronRight, Layers, MessagesSquare, Pencil, Plus, Search, Trash2, Users } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { createAdminApi, type AdminAccount, type AdminAccountUpdate, type AdminCapability, type AdminModelGroup, type AdminModelProfile } from "@/lib/admin-api";
import { accountRoleLabel, accountStatusLabel } from "@/lib/admin-ui";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { accountConversationPath } from "./admin-navigation";
import { MODEL_POLICY_LABELS, SKILL_POLICY_LABELS, useCapabilities, useModelGroups } from "./admin-capabilities";
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
  const api = useMemo(() => createAdminApi(token), [token]);
  const [view, setView] = useState<"accounts" | "groups">("accounts");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<"all" | "active" | "disabled">("all");
  const [role, setRole] = useState<"all" | "user" | "admin">("all");
  const [page, setPage] = useState(1);
  const [managed, setManaged] = useState<AdminAccount | null>(null);
  const [pending, setPending] = useState<PendingChange | null>(null);
  const [deleting, setDeleting] = useState<AdminAccount | null>(null);
  const [groupModal, setGroupModal] = useState<{ mode: "create" | "edit"; group?: AdminModelGroup } | null>(null);
  const [deletingGroup, setDeletingGroup] = useState<AdminModelGroup | null>(null);
  const [mutationError, setMutationError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [mutating, setMutating] = useState(false);

  const accountsQuery = useQuery({
    queryKey: ["admin", "accounts", query, status, role, page],
    queryFn: () => api.accounts({ query, status, role, page, pageSize: 20 }),
    retry: false,
    placeholderData: previous => previous,
  });
  const capabilities = useCapabilities(token);
  const modelGroups = useModelGroups(token);
  const directoryQuery = useQuery({
    queryKey: ["admin", "model-directory"],
    queryFn: ({ signal }) => api.modelDirectory(signal),
    retry: false,
  });

  const data = accountsQuery.data;
  const accounts = data?.accounts ?? [];
  const capByAccount = useMemo(() => {
    const map = new Map<string, AdminCapability>();
    for (const item of capabilities.query.data?.accounts ?? []) {
      map.set(item.account_id, item);
    }
    return map;
  }, [capabilities.query.data]);
  const groups = modelGroups.groups;
  const allModels = directoryQuery.data?.models ?? [];
  const modelLabelMap = useMemo(() => {
    const map = new Map<string, AdminModelProfile>();
    for (const m of allModels) map.set(m.profile_id, m);
    return map;
  }, [allModels]);
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

  async function confirmDeleteGroup() {
    if (!deletingGroup || mutating) return;
    setMutating(true);
    setMutationError(null);
    setSuccess(null);
    try {
      await modelGroups.remove(deletingGroup.id);
      setSuccess(`已删除分组「${deletingGroup.name}」`);
      setDeletingGroup(null);
    } catch (cause) {
      setMutationError(cause instanceof Error ? cause.message : "删除分组失败。");
    } finally {
      setMutating(false);
    }
  }

  return (
    <div className="admin-route-page">
      <PageHeader
        title="账户"
        action={
          <div className="admin-cc-header-actions">
            <RefreshButton
              loading={view === "accounts" ? accountsQuery.isFetching : modelGroups.query.isFetching}
              onClick={() => void (view === "accounts" ? accountsQuery.refetch() : modelGroups.query.refetch())}
            />
            {view === "groups" && (
              <button
                type="button"
                className="admin-primary-button"
                onClick={() => { setSuccess(null); setMutationError(null); setGroupModal({ mode: "create" }); }}
              >
                <Plus size={15} />
                新建分组
              </button>
            )}
          </div>
        }
      />

      <div className="admin-view-tabs" role="tablist" aria-label="账户与分组视图">
        <button
          type="button"
          role="tab"
          aria-selected={view === "accounts"}
          className={view === "accounts" ? "is-active" : ""}
          onClick={() => setView("accounts")}
        >
          <Users size={14} aria-hidden="true" />
          账户列表
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={view === "groups"}
          className={view === "groups" ? "is-active" : ""}
          onClick={() => setView("groups")}
        >
          <Layers size={14} aria-hidden="true" />
          权限分组
          <small>{groups.length}</small>
        </button>
      </div>

      {success && <p className="admin-success" role="status"><Check size={16} />{success}</p>}

      {view === "groups" ? (
        <ModelGroupsPanel
          loading={modelGroups.query.isPending}
          error={modelGroups.query.error instanceof Error ? modelGroups.query.error.message : null}
          groups={groups}
          modelLabelMap={modelLabelMap}
          onRetry={() => void modelGroups.query.refetch()}
          onCreate={() => setGroupModal({ mode: "create" })}
          onEdit={group => setGroupModal({ mode: "edit", group })}
          onDelete={group => { setMutationError(null); setDeletingGroup(group); }}
        />
      ) : !data && accountsQuery.isPending ? <InitialLoading label="正在读取账户…" />
        : !data ? <LoadError message={errorMessage} onRetry={() => void accountsQuery.refetch()} />
          : <>
            {accountsQuery.isError && <InlineError message={errorMessage} onRetry={() => void accountsQuery.refetch()} />}
            <div className="admin-toolbar">
              <label className="admin-search">
                <Search size={15} aria-hidden="true" />
                <input type="search" aria-label="搜索账户" placeholder="搜索备注、称呼或邮箱" value={query} onChange={event => { setQuery(event.target.value); setPage(1); }} />
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
                    <tr><th>账户</th><th>角色</th><th>模型分组</th><th>状态</th><th>注册 IP</th><th>注册时间</th><th className="is-num">操作</th></tr>
                  </thead>
                  <tbody>
                    {accounts.map(account => {
                      const cap = capByAccount.get(account.id);
                      return (
                        <tr key={account.id}>
                          <td className="admin-cell-wide">
                            <div className="admin-cell-title-row">
                              <strong className="admin-cell-title">
                                {account.alias || account.display_name || account.email}
                              </strong>
                              {account.alias && <span className="admin-alias-tag">我的备注</span>}
                            </div>
                            {(account.alias || account.display_name) && (
                              <small className="admin-cell-sub">
                                {[account.alias && account.display_name ? account.display_name : null, account.email].filter(Boolean).join(" · ")}
                              </small>
                            )}
                          </td>
                          <td className="admin-cell-nowrap">{accountRoleLabel(account.role)}</td>
                          <td className="admin-cell-nowrap">
                            {cap?.model_policy === "disabled" ? (
                              <span className="admin-group-badge is-off">已关闭</span>
                            ) : cap?.model_policy === "allowlist" ? (
                              <span className="admin-group-badge is-custom">指定 {cap.allowed_models.length} 个</span>
                            ) : cap?.group_name ? (
                              <span className="admin-group-badge is-grouped">{cap.group_name}</span>
                            ) : (
                              <span className="admin-group-badge">默认开放</span>
                            )}
                          </td>
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
                      );
                    })}
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
              onAliasUpdated={updated => {
                setManaged(updated);
                setSuccess(updated.alias ? `已更新备注「${updated.alias}」` : `已清除 ${updated.email} 的备注`);
                void accountsQuery.refetch();
              }}
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

      <AlertDialog open={Boolean(deletingGroup)} onOpenChange={open => { if (!open && !mutating) setDeletingGroup(null); }}>
        <AlertDialogContent className="admin-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle>删除权限分组「{deletingGroup?.name}」？</AlertDialogTitle>
            <AlertDialogDescription>
              将把组内 {deletingGroup?.member_count ?? 0} 位成员移出该分组，恢复为仅使用全员默认开放模型。
            </AlertDialogDescription>
          </AlertDialogHeader>
          {mutationError && <p className="admin-dialog-error" role="alert"><AlertCircle size={16} />{mutationError}</p>}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={mutating}>取消</AlertDialogCancel>
            <AlertDialogAction className="admin-danger-button" disabled={mutating} onClick={event => { event.preventDefault(); void confirmDeleteGroup(); }}>
              {mutating ? "删除中…" : "确认删除"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {groupModal && (
        <ModelGroupDialog
          mode={groupModal.mode}
          group={groupModal.group}
          models={allModels}
          busy={modelGroups.busy}
          onClose={() => setGroupModal(null)}
          onSubmit={async value => {
            if (groupModal.mode === "create") {
              const created = await modelGroups.create(value);
              setSuccess(`已创建分组「${created.name}」`);
            } else if (groupModal.group) {
              const updated = await modelGroups.update(groupModal.group.id, value);
              setSuccess(`已更新分组「${updated.name}」`);
            }
            setGroupModal(null);
          }}
        />
      )}
    </div>
  );
}

function ModelGroupsPanel({
  loading,
  error,
  groups,
  modelLabelMap,
  onRetry,
  onCreate,
  onEdit,
  onDelete,
}: {
  loading: boolean;
  error: string | null;
  groups: AdminModelGroup[];
  modelLabelMap: Map<string, AdminModelProfile>;
  onRetry: () => void;
  onCreate: () => void;
  onEdit: (group: AdminModelGroup) => void;
  onDelete: (group: AdminModelGroup) => void;
}) {
  if (loading) return <InitialLoading label="正在读取权限分组…" />;
  if (error) return <LoadError message={error} onRetry={onRetry} />;
  return (
    <section className="admin-groups-section">
      <p className="admin-tabs-hint">
        未分组用户仅可使用「全员默认」的中国主流模型；将用户加入某分组后，将额外解锁该组勾选的模型（如海外或高阶模型）。
      </p>
      {groups.length === 0 ? (
        <div className="admin-empty-state">
          <p>还没有权限分组，创建一个用来管理海外或高阶模型权限。</p>
          <button type="button" className="admin-primary-button" onClick={onCreate}>
            <Plus size={15} />
            新建分组
          </button>
        </div>
      ) : (
        <div className="admin-group-grid">
          {groups.map(group => (
            <article key={group.id} className="admin-group-card">
              <header className="admin-group-card-head">
                <div>
                  <strong>{group.name}</strong>
                  <span className="admin-group-members">{group.member_count} 位成员</span>
                </div>
                <div className="admin-cc-row-actions">
                  <button type="button" className="admin-cc-icon-btn" title="编辑分组" aria-label={`编辑分组 ${group.name}`} onClick={() => onEdit(group)}>
                    <Pencil size={14} />
                  </button>
                  <button type="button" className="admin-cc-icon-btn is-danger" title="删除分组" aria-label={`删除分组 ${group.name}`} onClick={() => onDelete(group)}>
                    <Trash2 size={14} />
                  </button>
                </div>
              </header>
              {group.description && <p className="admin-group-desc">{group.description}</p>}
              <div className="admin-group-models">
                {group.allowed_models.length === 0 ? (
                  <span className="admin-drawer-note">未勾选额外模型</span>
                ) : (
                  group.allowed_models.map(id => (
                    <span key={id} className="admin-cc-badge">
                      {modelLabelMap.get(id)?.label ?? id}
                    </span>
                  ))
                )}
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

function ModelGroupDialog({
  mode,
  group,
  models,
  busy,
  onClose,
  onSubmit,
}: {
  mode: "create" | "edit";
  group?: AdminModelGroup;
  models: AdminModelProfile[];
  busy: boolean;
  onClose: () => void;
  onSubmit: (value: { name: string; description: string; allowed_models: string[] }) => Promise<void>;
}) {
  const [name, setName] = useState(group?.name ?? "");
  const [description, setDescription] = useState(group?.description ?? "");
  const [selected, setSelected] = useState<string[]>(group?.allowed_models ?? []);
  const [error, setError] = useState<string | null>(null);

  const groupedCandidates = models.filter(m => m.access_scope === "allowlist");
  const openModels = models.filter(m => m.access_scope === "all");

  function toggle(id: string) {
    setSelected(cur => (cur.includes(id) ? cur.filter(item => item !== id) : [...cur, id]));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const cleanName = name.trim();
    if (!cleanName) {
      setError("请填写分组名称。");
      return;
    }
    setError(null);
    try {
      await onSubmit({ name: cleanName, description: description.trim(), allowed_models: selected });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存分组失败。");
    }
  }

  return (
    <Dialog open onOpenChange={open => { if (!open && !busy) onClose(); }}>
      <DialogContent className="admin-dialog admin-cc-modal">
        <DialogHeader>
          <DialogTitle>{mode === "create" ? "新建权限分组" : `编辑分组「${group?.name}」`}</DialogTitle>
          <DialogDescription>勾选该分组额外开放的模型；全员默认模型对所有人自动可用。</DialogDescription>
        </DialogHeader>
        <form className="admin-cc-modal-form" onSubmit={e => void handleSubmit(e)}>
          <div className="admin-cc-grid-2">
            <label className="admin-cc-field">
              <span>分组名称</span>
              <input required maxLength={80} placeholder="例如 课题组 / 海外模型组" value={name} onChange={e => setName(e.target.value)} />
            </label>
            <label className="admin-cc-field">
              <span>备注说明<i>选填</i></span>
              <input maxLength={240} placeholder="例如 开放 Claude 与 GPT" value={description} onChange={e => setDescription(e.target.value)} />
            </label>
          </div>
          <div className="admin-group-pick-box">
            <h3>需分组授权的模型 ({groupedCandidates.length})</h3>
            {groupedCandidates.length === 0 ? (
              <p className="admin-drawer-note">当前没有设置为「按分组授权」的模型，可在模型页将海外模型设为按分组授权。</p>
            ) : (
              <ul className="admin-check-list">
                {groupedCandidates.map(model => (
                  <li key={model.profile_id}>
                    <label className="admin-check">
                      <input type="checkbox" checked={selected.includes(model.profile_id)} disabled={busy} onChange={() => toggle(model.profile_id)} />
                      <span>{model.label}</span>
                      {!model.enabled && <small>已停用</small>}
                    </label>
                  </li>
                ))}
              </ul>
            )}
            {openModels.length > 0 && (
              <p className="admin-drawer-note">
                已全员默认开放（组员自动继承）：{openModels.map(m => m.label).join("、")}
              </p>
            )}
          </div>
          {error && <p className="admin-dialog-error" role="alert"><AlertCircle size={15} />{error}</p>}
          <DialogFooter>
            <button type="button" className="admin-row-action" disabled={busy} onClick={onClose}>取消</button>
            <button type="submit" className="admin-primary-button" disabled={busy}>{busy ? "保存中…" : "保存分组"}</button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
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

function AccountDrawer({ account, isSelf, token, busy, onAliasUpdated, onRequest, onRequestDelete }: {
  account: AdminAccount;
  isSelf: boolean;
  token: string;
  busy: boolean;
  onAliasUpdated: (updated: AdminAccount) => void;
  onRequest: (update: AdminAccountUpdate) => void;
  onRequestDelete: () => void;
}) {
  const api = useMemo(() => createAdminApi(token), [token]);
  const capabilities = useCapabilities(token);
  const modelGroups = useModelGroups(token);
  const directory = useQuery({ queryKey: ["admin", "model-directory"], queryFn: ({ signal }) => api.modelDirectory(signal), retry: false });
  const [draft, setDraft] = useState<string[] | null>(null);
  const [aliasInput, setAliasInput] = useState(account.alias ?? "");
  const [aliasNoteInput, setAliasNoteInput] = useState(account.alias_note ?? "");
  const [savingAlias, setSavingAlias] = useState(false);
  const [aliasError, setAliasError] = useState<string | null>(null);
  const capability = capabilities.query.data?.accounts.find(item => item.account_id === account.id);
  const models = directory.data?.models ?? [];
  const groups = modelGroups.groups;

  const activePolicy = draft !== null ? "allowlist" : capability?.model_policy;
  const checked = draft ?? (capability?.model_policy === "allowlist" ? capability.allowed_models : null);
  const draftChanged = draft !== null && capability !== undefined
    && (capability.model_policy !== "allowlist" || draft.length !== capability.allowed_models.length || draft.some(id => !capability.allowed_models.includes(id)));

  const effectivePreview = useMemo(() => {
    if (!capability || capability.model_policy === "disabled") return [];
    const activeSet = new Set(checked ?? capability.allowed_models);
    const groupSet = new Set(capability.group_allowed_models ?? []);
    return models
      .filter(model => {
        if (!model.enabled) return false;
        if (model.access_scope === "admin_only") return account.role === "admin";
        if (capability.model_policy === "allowlist") return activeSet.has(model.profile_id);
        if (model.access_scope === "allowlist") {
          return account.role === "admin" || groupSet.has(model.profile_id) || activeSet.has(model.profile_id);
        }
        return true;
      })
      .map(model => ({
        profile_id: model.profile_id,
        label: model.label,
        source:
          model.access_scope === "all"
            ? "默认开放"
            : capability.model_policy === "allowlist"
              ? "指定"
              : groupSet.has(model.profile_id)
                ? "分组授权"
                : account.role === "admin"
                  ? "管理员"
                  : "特例",
      }));
  }, [account.role, capability, checked, models]);

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

  async function submitAlias(nextAlias: string, nextNote: string) {
    if (savingAlias) return;
    setSavingAlias(true);
    setAliasError(null);
    try {
      const updated = await api.setAccountAlias(account.id, { alias: nextAlias, note: nextNote });
      setAliasInput(updated.alias ?? "");
      setAliasNoteInput(updated.alias_note ?? "");
      onAliasUpdated(updated);
    } catch (cause) {
      setAliasError(cause instanceof Error ? cause.message : "保存备注失败。");
    } finally {
      setSavingAlias(false);
    }
  }

  return (
    <>
      <header className="admin-drawer-head">
        <SheetTitle>{account.alias || account.display_name || account.email}</SheetTitle>
        <SheetDescription>
          {[account.alias && account.display_name ? `原称呼：${account.display_name}` : null, account.email].filter(Boolean).join(" · ")}
        </SheetDescription>
      </header>

      <div className="admin-drawer-body">
        <section className="admin-drawer-section">
          <h3>我的备注别名</h3>
          <form
            className="admin-alias-card"
            onSubmit={event => {
              event.preventDefault();
              void submitAlias(aliasInput, aliasNoteInput);
            }}
          >
            <input
              type="text"
              maxLength={80}
              aria-label="备注别名"
              placeholder="设置个人备注别名（如：张三 · 课题组）"
              value={aliasInput}
              disabled={savingAlias}
              onChange={e => setAliasInput(e.target.value)}
            />
            <input
              type="text"
              maxLength={240}
              aria-label="简短说明"
              placeholder="简短说明（选填）"
              value={aliasNoteInput}
              disabled={savingAlias}
              onChange={e => setAliasNoteInput(e.target.value)}
            />
            {aliasError && <p className="admin-form-error" role="alert">{aliasError}</p>}
            <div className="admin-alias-actions">
              {(account.alias || account.alias_note || aliasInput || aliasNoteInput) && (
                <button
                  type="button"
                  className="admin-row-action"
                  disabled={savingAlias}
                  onClick={() => void submitAlias("", "")}
                >
                  清除
                </button>
              )}
              <button type="submit" className="admin-primary-button" disabled={savingAlias}>
                {savingAlias ? "保存中…" : "保存备注"}
              </button>
            </div>
          </form>
          <p className="admin-drawer-note">仅对你当前管理员账号可见，不影响其他管理员或用户自己的称呼。</p>
        </section>

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
                <div className="admin-drawer-row">
                  <span>所属分组</span>
                  <Select
                    value={capability.group_id ?? "__none__"}
                    disabled={capabilities.saving}
                    onValueChange={value => void capabilities.update(account.id, value === "__none__" ? { clear_group: true } : { group_id: value })}
                  >
                    <SelectTrigger className="admin-drawer-select" aria-label="所属分组"><SelectValue /></SelectTrigger>
                    <SelectContent className="admin-select-menu">
                      <SelectItem value="__none__">未分组（仅默认开放模型）</SelectItem>
                      {groups.map(group => (
                        <SelectItem key={group.id} value={group.id}>{group.name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="admin-segment" role="radiogroup" aria-label="可用模型">
                  {MODEL_POLICIES.map(policy => (
                    <button key={policy} type="button" role="radio" aria-checked={activePolicy === policy} className={activePolicy === policy ? "is-active" : ""} disabled={capabilities.saving} onClick={() => choosePolicy(policy)}>
                      {MODEL_POLICY_LABELS[policy]}
                    </button>
                  ))}
                </div>
                <div className="admin-effective-models">
                  <span className="admin-drawer-note">当前生效可用模型（{effectivePreview.length} 个）：</span>
                  {effectivePreview.length === 0 ? (
                    <p className="admin-drawer-note">暂无可用模型</p>
                  ) : (
                    <div className="admin-effective-pills">
                      {effectivePreview.map(item => (
                        <span key={item.profile_id} className="admin-cc-badge">
                          {item.label}
                          <small>{item.source}</small>
                        </span>
                      ))}
                    </div>
                  )}
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
