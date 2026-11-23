import { CheckCircle2, CircleAlert, Database, Gauge, Shield, SlidersHorizontal, Wrench } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { createAdminApi, type AdminCapability, type AdminModelProfile, type AdminTask, type TaskStatusFilter } from "@/lib/admin-api";
import { useAdminOutlet } from "./AdminLayout";
import { conversationDetailPath } from "./admin-navigation";
import { formatAdminDuration, InitialLoading, InlineError, LoadError, PageHeader, RefreshButton, statusLabel, statusToneClass } from "./AdminShared";

const TASK_STATUS_OPTIONS: Array<{ value: TaskStatusFilter; label: string }> = [
  { value: "all", label: "全部" },
  { value: "succeeded", label: "已完成" },
  { value: "active", label: "进行中/暂停" },
  { value: "failed", label: "未完成" },
];

function stateLabel(value: string) {
  return value === "ready"
    ? "正常"
    : value === "coming_soon"
      ? "持续推进中"
      : value === "degraded"
        ? "部分可用"
        : value === "not_configured"
          ? "未启用"
          : "不可用";
}

function StatePill({ state }: { state: string }) {
  const Icon = state === "ready" ? CheckCircle2 : CircleAlert;
  return (
    <span className={`admin-ops-state is-${state}`}>
      <Icon size={14} />
      {stateLabel(state)}
    </span>
  );
}

export default function AdminOperationsPage() {
  const { token } = useAdminOutlet();
  const api = useMemo(() => createAdminApi(token), [token]);
  const client = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const filterAccountId = searchParams.get("account")?.trim() ?? "";
  const rawTaskStatus = searchParams.get("status");
  const taskStatus: TaskStatusFilter =
    rawTaskStatus === "succeeded" || rawTaskStatus === "active" || rawTaskStatus === "failed"
      ? rawTaskStatus
      : "all";
  const [activeError, setActiveError] = useState("");
  const [pickerTarget, setPickerTarget] = useState<AdminCapability | null>(null);
  const [draftModels, setDraftModels] = useState<string[]>([]);
  const [pickerSearch, setPickerSearch] = useState("");

  function updateTaskStatus(nextStatus: TaskStatusFilter) {
    const next = new URLSearchParams(searchParams);
    if (nextStatus === "all") {
      next.delete("status");
    } else {
      next.set("status", nextStatus);
    }
    setSearchParams(next, { replace: true });
  }

  const capabilities = useQuery({ queryKey: ["admin", "capabilities"], queryFn: ({ signal }) => api.capabilities(signal), retry: false });
  const directory = useQuery({ queryKey: ["admin", "model-directory"], queryFn: ({ signal }) => api.modelDirectory(signal), retry: false });
  const skills = useQuery({ queryKey: ["admin", "skills"], queryFn: ({ signal }) => api.skills(signal), retry: false });
  const resources = useQuery({ queryKey: ["admin", "resources"], queryFn: ({ signal }) => api.resourcesHealth(signal), retry: false });
  const tasks = useQuery({
    queryKey: ["admin", "tasks", taskStatus],
    queryFn: ({ signal }) => api.tasks(30, signal, taskStatus),
    retry: false,
    placeholderData: previous => previous,
  });
  const runtime = useQuery({ queryKey: ["admin", "runtime"], queryFn: ({ signal }) => api.runtimeHealth(signal), retry: false });

  const update = useMutation({
    mutationFn: ({ accountId, change }: { accountId: string; change: Partial<AdminCapability> }) => api.updateCapability(accountId, change),
    onSuccess: updated => {
      client.setQueryData<{ accounts: AdminCapability[] }>(["admin", "capabilities"], current =>
        current
          ? { accounts: current.accounts.map(item => (item.account_id === updated.account_id ? updated : item)) }
          : current,
      );
      setActiveError("");
      setPickerTarget(null);
    },
    onError: error => setActiveError(error instanceof Error ? error.message : "权限修改没有完成。"),
  });

  const allModels = useMemo(() => directory.data?.models ?? [], [directory.data]);
  const modelLabels = useMemo(() => {
    const map = new Map<string, string>();
    for (const model of allModels) {
      map.set(model.profile_id, model.label);
    }
    return map;
  }, [allModels]);

  const rawAccounts = capabilities.data?.accounts ?? [];
  const visibleAccounts = filterAccountId
    ? rawAccounts.filter(item => item.account_id === filterAccountId)
    : rawAccounts;

  function openAllowlistPicker(account: AdminCapability) {
    const enabledIds = allModels.filter(m => m.enabled).map(m => m.profile_id);
    const seed = account.allowed_models.length
      ? account.allowed_models
      : enabledIds.length
        ? [enabledIds[0]]
        : [];
    setDraftModels(seed);
    setPickerSearch("");
    setPickerTarget(account);
  }

  function handlePolicyChange(account: AdminCapability, nextPolicy: AdminCapability["model_policy"]) {
    if (nextPolicy === "allowlist") {
      if (account.allowed_models.length === 0) {
        openAllowlistPicker(account);
        return;
      }
      update.mutate({
        accountId: account.account_id,
        change: { model_policy: "allowlist", allowed_models: account.allowed_models },
      });
      return;
    }
    update.mutate({ accountId: account.account_id, change: { model_policy: nextPolicy } });
  }

  function confirmPicker() {
    if (!pickerTarget) return;
    if (draftModels.length === 0) {
      setActiveError("请至少勾选一个允许使用的模型。");
      return;
    }
    update.mutate({
      accountId: pickerTarget.account_id,
      change: { model_policy: "allowlist", allowed_models: draftModels },
    });
  }

  const firstLoading = capabilities.isPending && skills.isPending && resources.isPending && tasks.isPending && runtime.isPending;
  if (firstLoading) {
    return (
      <div className="admin-route-page">
        <PageHeader eyebrow="运行" title="能力与状态" description="查看权限、资料和运行情况。" />
        <InitialLoading label="正在读取管理信息…" />
      </div>
    );
  }
  if (capabilities.isError && !capabilities.data) {
    return (
      <div className="admin-route-page">
        <PageHeader eyebrow="运行" title="能力与状态" description="查看权限、资料和运行情况。" />
        <LoadError message="管理信息暂时不可用。" onRetry={() => void capabilities.refetch()} />
      </div>
    );
  }

  const filteredModels = allModels.filter(
    item =>
      !pickerSearch.trim()
      || item.label.toLowerCase().includes(pickerSearch.trim().toLowerCase())
      || item.profile_id.toLowerCase().includes(pickerSearch.trim().toLowerCase()),
  );

  return (
    <div className="admin-route-page admin-operations-page">
      <PageHeader
        eyebrow="运行"
        title="能力与状态"
        description="查看账户模型/技能授权、资料索引和运行健康状态。"
        action={
          <RefreshButton
            loading={[capabilities, skills, resources, tasks, runtime, directory].some(q => q.isFetching)}
            onClick={() => {
              void Promise.all([
                capabilities.refetch(),
                directory.refetch(),
                skills.refetch(),
                resources.refetch(),
                tasks.refetch(),
                runtime.refetch(),
              ]);
            }}
          />
        }
      />
      {activeError && <InlineError message={activeError} onRetry={() => setActiveError("")} />}
      <section className="admin-ops-section" aria-labelledby="admin-ops-capabilities">
        <header className="admin-ops-section-title admin-ops-section-split">
          <h2 id="admin-ops-capabilities">
            <span>
              <Shield size={16} />
            </span>
            账户权限
          </h2>
          {filterAccountId && (
            <button
              type="button"
              className="admin-row-action admin-model-btn"
              onClick={() => setSearchParams({})}
            >
              显示全部账户
            </button>
          )}
        </header>
        <CapabilityTable
          items={visibleAccounts}
          modelLabels={modelLabels}
          updating={update.isPending}
          onPolicyChange={handlePolicyChange}
          onPickModels={openAllowlistPicker}
          onChange={(accountId, change) => update.mutate({ accountId, change })}
        />
      </section>
      <section className="admin-ops-section" aria-labelledby="admin-ops-skills">
        <SectionTitle icon={<Wrench size={16} />} id="admin-ops-skills" title="技能" />
        <div className="admin-ops-summary">
          <StatePill state={skills.data?.status ?? "unavailable"} />
          <span>{skills.data ? `${skills.data.skills.filter(skill => skill.status !== "coming_soon").length} 个已开放，${skills.data.skills.filter(skill => skill.status === "coming_soon").length} 个未开放` : "—"}</span>
        </div>
        {skills.data?.skills.length ? (
          <div className="admin-ops-list">
            {skills.data.skills.filter(skill => skill.status !== "coming_soon").map(skill => (
              <div className="admin-ops-row" key={skill.id}>
                <div>
                  <strong>
                    {skill.name}
                    {skill.mode_scope && (
                      <small style={{ marginLeft: 8, fontWeight: 400, opacity: 0.72 }}>
                        {skill.mode_scope === "chat" ? "聊天模式" : "工作模式"}
                      </small>
                    )}
                  </strong>
                  <small>{skill.summary}</small>
                </div>
                <StatePill state={skill.status} />
              </div>
            ))}
          </div>
        ) : (
          <p className="admin-empty">没有可显示的 Skill。</p>
        )}
      </section>
      <section className="admin-ops-grid">
        <HealthSection title="资料索引" icon={<Database size={16} />} state={resources.data?.status ?? "unavailable"} items={resources.data?.sources ?? {}} />
        <HealthSection title="服务状态" icon={<Gauge size={16} />} state={runtime.data?.status ?? "unavailable"} items={runtime.data?.components ?? {}} />
      </section>
      <section className="admin-ops-section" aria-labelledby="admin-ops-tasks">
        <div className="admin-ops-section-head">
          <SectionTitle icon={<Gauge size={16} />} id="admin-ops-tasks" title="最近任务" />
          <div className="admin-source-filter-bar admin-conv-status-bar" role="group" aria-label="按任务状态筛选">
            {TASK_STATUS_OPTIONS.map(option => {
              const isSelected = taskStatus === option.value;
              return (
                <button
                  key={option.value}
                  type="button"
                  aria-pressed={isSelected}
                  className={isSelected ? "is-active" : ""}
                  onClick={() => updateTaskStatus(option.value)}
                >
                  {option.label}
                </button>
              );
            })}
          </div>
        </div>
        <TaskTable tasks={tasks.data?.tasks ?? []} hasStatusFilter={taskStatus !== "all"} />
      </section>

      <Dialog open={Boolean(pickerTarget)} onOpenChange={open => { if (!open && !update.isPending) setPickerTarget(null); }}>
        <DialogContent className="admin-dialog admin-allowlist-dialog">
          <DialogHeader>
            <DialogTitle>指定可用模型 · {pickerTarget?.display_name || pickerTarget?.email}</DialogTitle>
            <DialogDescription>勾选后，该账户仅可在工作台选择以下模型。</DialogDescription>
          </DialogHeader>
          <input
            type="search"
            className="admin-discover-search"
            placeholder="搜索模型名称或编号…"
            value={pickerSearch}
            onChange={e => setPickerSearch(e.target.value)}
          />
          <div className="admin-discover-list">
            {directory.isPending ? (
              <p className="admin-empty">正在读取模型…</p>
            ) : directory.isError ? (
              <p className="admin-empty">{directory.error instanceof Error ? directory.error.message : "模型列表读取失败。"}</p>
            ) : filteredModels.length === 0 ? (
              <p className="admin-empty">没有可选模型。</p>
            ) : (
              filteredModels.map((model: AdminModelProfile) => {
                const checked = draftModels.includes(model.profile_id);
                return (
                  <label key={model.profile_id} className="admin-allowlist-option">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={e =>
                        setDraftModels(current =>
                          e.target.checked
                            ? [...current, model.profile_id]
                            : current.filter(id => id !== model.profile_id),
                        )
                      }
                    />
                    <span>
                      <strong>{model.label}</strong>
                      <small>
                        {model.profile_id}
                        {!model.enabled ? "（已停用）" : ""}
                        {model.access_scope === "admin_only" ? "（仅管理员）" : ""}
                      </small>
                    </span>
                  </label>
                );
              })
            )}
          </div>
          <DialogFooter>
            <button type="button" className="admin-page-button" disabled={update.isPending} onClick={() => setPickerTarget(null)}>
              取消
            </button>
            <button type="button" className="admin-primary-button" disabled={update.isPending || draftModels.length === 0} onClick={confirmPicker}>
              {update.isPending ? "保存中…" : `确认保存 (${draftModels.length})`}
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function SectionTitle({ icon, id, title }: { icon: React.ReactNode; id: string; title: string }) {
  return (
    <header className="admin-ops-section-title">
      <h2 id={id}>
        <span>{icon}</span>
        {title}
      </h2>
    </header>
  );
}

function CapabilityTable({
  items,
  modelLabels,
  updating,
  onPolicyChange,
  onPickModels,
  onChange,
}: {
  items: AdminCapability[];
  modelLabels: Map<string, string>;
  updating: boolean;
  onPolicyChange: (account: AdminCapability, policy: AdminCapability["model_policy"]) => void;
  onPickModels: (account: AdminCapability) => void;
  onChange: (accountId: string, change: Partial<AdminCapability>) => void;
}) {
  if (!items.length) return <p className="admin-empty">没有账户。</p>;
  return (
    <div className="admin-ops-table-wrap">
      <table className="admin-ops-table">
        <thead>
          <tr>
            <th>账户</th>
            <th>模型授权</th>
            <th>技能授权</th>
            <th>上传技能</th>
          </tr>
        </thead>
        <tbody>
          {items.map(item => (
            <tr key={item.account_id}>
              <td>
                <strong>{item.display_name || item.email}</strong>
                <small>{item.email}</small>
              </td>
              <td>
                <div className="admin-ops-policy-cell">
                  <select
                    aria-label={`${item.email} 模型权限`}
                    value={item.model_policy}
                    disabled={updating}
                    onChange={event => onPolicyChange(item, event.target.value as AdminCapability["model_policy"])}
                  >
                    <option value="default">按默认</option>
                    <option value="allowlist">指定模型</option>
                    <option value="disabled">关闭</option>
                  </select>
                  {item.model_policy === "allowlist" && (
                    <button
                      type="button"
                      className="admin-ops-pick-btn"
                      disabled={updating}
                      onClick={() => onPickModels(item)}
                    >
                      <SlidersHorizontal size={12} />
                      选择 ({item.allowed_models.length})
                    </button>
                  )}
                </div>
                {item.model_policy === "allowlist" && (
                  <small className="admin-ops-subvalue" title={item.allowed_models.join(", ")}>
                    {item.allowed_models.map(id => modelLabels.get(id) ?? id).join("、") || "未指定"}
                  </small>
                )}
              </td>
              <td>
                <select
                  aria-label={`${item.email} Skill 权限`}
                  value={item.skill_policy}
                  disabled={updating}
                  onChange={event => onChange(item.account_id, { skill_policy: event.target.value as AdminCapability["skill_policy"] })}
                >
                  <option value="default">按默认</option>
                  <option value="builtin_only">仅内置</option>
                  <option value="disabled">关闭</option>
                </select>
              </td>
              <td>
                <input
                  aria-label={`${item.email} 上传技能`}
                  type="checkbox"
                  checked={item.can_upload_skill}
                  disabled={updating}
                  onChange={event => onChange(item.account_id, { can_upload_skill: event.target.checked })}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const HEALTH_ITEM_LABELS: Record<string, string> = {
  laws: "法规",
  cases: "案例",
  papers: "论文",
};

function HealthSection({
  title,
  icon,
  state,
  items,
}: {
  title: string;
  icon: React.ReactNode;
  state: string;
  items: Record<string, { status: string; count?: number; reason?: string }>;
}) {
  return (
    <section className="admin-ops-section admin-ops-health" aria-label={title}>
      <SectionTitle icon={icon} id={`admin-health-${title}`} title={title} />
      <div className="admin-ops-summary">
        <StatePill state={state} />
      </div>
      <div className="admin-ops-list">
        {Object.entries(items).map(([name, item]) => {
          const countText = item.count === undefined ? "" : `${item.count.toLocaleString("zh-CN")} 条`;
          const detailText = [countText, item.reason].filter(Boolean).join(" · ");
          return (
          <div className="admin-ops-row" key={name}>
            <div>
              <strong>{HEALTH_ITEM_LABELS[name] ?? name}</strong>
              <small>{detailText}</small>
            </div>
            <StatePill state={item.status} />
          </div>
          );
        })}
      </div>
    </section>
  );
}

function TaskTable({
  tasks,
  hasStatusFilter,
}: {
  tasks: AdminTask[];
  hasStatusFilter: boolean;
}) {
  if (!tasks.length) {
    return <p className="admin-empty">{hasStatusFilter ? "当前状态筛选下还没有任务。" : "还没有任务。"}</p>;
  }
  return (
    <div className="admin-ops-table-wrap">
      <table className="admin-ops-table admin-ops-task-table">
        <thead>
          <tr>
            <th>问题</th>
            <th>账户</th>
            <th>状态</th>
            <th>模型</th>
            <th>耗时</th>
          </tr>
        </thead>
        <tbody>
          {tasks.map(task => (
            <tr key={task.run_id}>
              <td>
                <Link className="admin-ops-task-link" to={conversationDetailPath(task.account_id, task.conversation_id)}>
                  <strong title={task.question}>{task.question || "（无题任务）"}</strong>
                  <small>{task.answer_chars.toLocaleString("zh-CN")} 字回答 · 查看会话 →</small>
                </Link>
              </td>
              <td>{task.account_email}</td>
              <td>
                <span className={`admin-cc-badge ${statusToneClass(task.status)}`}>
                  {statusLabel(task.status)}
                </span>
              </td>
              <td>{task.model ?? "—"}</td>
              <td>{formatAdminDuration(task.elapsed_ms)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
