import { CheckCircle2, CircleAlert, Database, Gauge, Shield, Wrench } from "lucide-react";
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createAdminApi, type AdminCapability } from "@/lib/admin-api";
import { useAdminOutlet } from "./AdminLayout";
import { formatAdminDate, formatAdminDuration, InitialLoading, InlineError, LoadError, PageHeader, RefreshButton, statusLabel } from "./AdminShared";

function stateLabel(value: string) {
  return value === "ready" ? "正常" : value === "degraded" ? "部分可用" : value === "not_configured" ? "未启用" : "不可用";
}

function StatePill({ state }: { state: string }) {
  const Icon = state === "ready" ? CheckCircle2 : CircleAlert;
  return <span className={`admin-ops-state is-${state}`}><Icon size={14} />{stateLabel(state)}</span>;
}

export default function AdminOperationsPage() {
  const { token } = useAdminOutlet();
  const api = useMemo(() => createAdminApi(token), [token]);
  const client = useQueryClient();
  const [activeError, setActiveError] = useState("");
  const capabilities = useQuery({ queryKey: ["admin", "capabilities"], queryFn: ({ signal }) => api.capabilities(signal), retry: false });
  const skills = useQuery({ queryKey: ["admin", "skills"], queryFn: ({ signal }) => api.skills(signal), retry: false });
  const resources = useQuery({ queryKey: ["admin", "resources"], queryFn: ({ signal }) => api.resourcesHealth(signal), retry: false });
  const tasks = useQuery({ queryKey: ["admin", "tasks"], queryFn: ({ signal }) => api.tasks(30, signal), retry: false });
  const runtime = useQuery({ queryKey: ["admin", "runtime"], queryFn: ({ signal }) => api.runtimeHealth(signal), retry: false });
  const update = useMutation({
    mutationFn: ({ accountId, change }: { accountId: string; change: Partial<AdminCapability> }) => api.updateCapability(accountId, change),
    onSuccess: updated => {
      client.setQueryData<{ accounts: AdminCapability[] }>(["admin", "capabilities"], current => current ? { accounts: current.accounts.map(item => item.account_id === updated.account_id ? updated : item) } : current);
      setActiveError("");
    },
    onError: error => setActiveError(error instanceof Error ? error.message : "权限修改没有完成。"),
  });
  const firstLoading = capabilities.isPending && skills.isPending && resources.isPending && tasks.isPending && runtime.isPending;
  if (firstLoading) return <div className="admin-route-page"><PageHeader eyebrow="运行" title="能力与状态" description="查看权限、资料和运行情况。" /><InitialLoading label="正在读取管理信息…" /></div>;
  if (capabilities.isError && !capabilities.data) return <div className="admin-route-page"><PageHeader eyebrow="运行" title="能力与状态" description="查看权限、资料和运行情况。" /><LoadError message="管理信息暂时不可用。" onRetry={() => void capabilities.refetch()} /></div>;
  return <div className="admin-route-page admin-operations-page">
    <PageHeader eyebrow="运行" title="能力与状态" description="查看权限、资料和运行情况。" action={<RefreshButton loading={[capabilities, skills, resources, tasks, runtime].some(query => query.isFetching)} onClick={() => { void Promise.all([capabilities.refetch(), skills.refetch(), resources.refetch(), tasks.refetch(), runtime.refetch()]); }} />} />
    {activeError && <InlineError message={activeError} onRetry={() => setActiveError("")} />}
    <section className="admin-ops-section" aria-labelledby="admin-ops-capabilities"><SectionTitle icon={<Shield size={16} />} id="admin-ops-capabilities" title="账户权限" /><CapabilityTable items={capabilities.data?.accounts ?? []} updating={update.isPending} onChange={(accountId, change) => update.mutate({ accountId, change })} /></section>
    <section className="admin-ops-section" aria-labelledby="admin-ops-skills"><SectionTitle icon={<Wrench size={16} />} id="admin-ops-skills" title="Skill" /><div className="admin-ops-summary"><StatePill state={skills.data?.status ?? "unavailable"} /><span>{skills.data?.total ?? "—"} 个已注册 Skill</span></div>{skills.data?.skills.length ? <div className="admin-ops-list">{skills.data.skills.map(skill => <div className="admin-ops-row" key={skill.id}><div><strong>{skill.name}</strong><small>{skill.summary}</small></div><StatePill state={skill.status} /></div>)}</div> : <p className="admin-empty">没有可显示的 Skill。</p>}</section>
    <section className="admin-ops-grid">
      <HealthSection title="资料索引" icon={<Database size={16} />} state={resources.data?.status ?? "unavailable"} items={resources.data?.sources ?? {}} />
      <HealthSection title="服务状态" icon={<Gauge size={16} />} state={runtime.data?.status ?? "unavailable"} items={runtime.data?.components ?? {}} />
    </section>
    <section className="admin-ops-section" aria-labelledby="admin-ops-tasks"><SectionTitle icon={<Gauge size={16} />} id="admin-ops-tasks" title="最近任务" /><TaskTable tasks={tasks.data?.tasks ?? []} /></section>
  </div>;
}

function SectionTitle({ icon, id, title }: { icon: React.ReactNode; id: string; title: string }) {
  return <header className="admin-ops-section-title"><h2 id={id}><span>{icon}</span>{title}</h2></header>;
}

function CapabilityTable({ items, updating, onChange }: { items: AdminCapability[]; updating: boolean; onChange: (accountId: string, change: Partial<AdminCapability>) => void }) {
  if (!items.length) return <p className="admin-empty">没有账户。</p>;
  return <div className="admin-ops-table-wrap"><table className="admin-ops-table"><thead><tr><th>账户</th><th>模型</th><th>Skill</th><th>上传</th></tr></thead><tbody>{items.map(item => <tr key={item.account_id}><td><strong>{item.display_name || item.email}</strong><small>{item.email}</small></td><td><select aria-label={`${item.email} 模型权限`} value={item.model_policy} disabled={updating} onChange={event => onChange(item.account_id, { model_policy: event.target.value as AdminCapability["model_policy"] })}><option value="default">按默认</option><option value="allowlist">指定模型</option><option value="disabled">关闭</option></select>{item.model_policy === "allowlist" && <small className="admin-ops-subvalue">{item.allowed_models.join("、") || "未指定"}</small>}</td><td><select aria-label={`${item.email} Skill 权限`} value={item.skill_policy} disabled={updating} onChange={event => onChange(item.account_id, { skill_policy: event.target.value as AdminCapability["skill_policy"] })}><option value="default">按默认</option><option value="builtin_only">仅内置</option><option value="disabled">关闭</option></select></td><td><input aria-label={`${item.email} 上传 Skill`} type="checkbox" checked={item.can_upload_skill} disabled={updating} onChange={event => onChange(item.account_id, { can_upload_skill: event.target.checked })} /></td></tr>)}</tbody></table></div>;
}

function HealthSection({ title, icon, state, items }: { title: string; icon: React.ReactNode; state: string; items: Record<string, { status: string; count?: number; reason?: string }> }) {
  return <section className="admin-ops-section admin-ops-health" aria-label={title}><SectionTitle icon={icon} id={`admin-health-${title}`} title={title} /><div className="admin-ops-summary"><StatePill state={state} /></div><div className="admin-ops-list">{Object.entries(items).map(([name, item]) => <div className="admin-ops-row" key={name}><div><strong>{name}</strong><small>{item.reason ?? (item.count === undefined ? "" : `${item.count.toLocaleString("zh-CN")} 条`)}</small></div><StatePill state={item.status} /></div>)}</div></section>;
}

function TaskTable({ tasks }: { tasks: Array<{ run_id: string; account_email: string; question: string; status: string; model: string | null; elapsed_ms: number | null; answer_chars: number }> }) {
  if (!tasks.length) return <p className="admin-empty">还没有任务。</p>;
  return <div className="admin-ops-table-wrap"><table className="admin-ops-table admin-ops-task-table"><thead><tr><th>问题</th><th>账户</th><th>状态</th><th>模型</th><th>耗时</th></tr></thead><tbody>{tasks.map(task => <tr key={task.run_id}><td><strong title={task.question}>{task.question}</strong><small>{task.answer_chars.toLocaleString("zh-CN")} 字回答</small></td><td>{task.account_email}</td><td>{statusLabel(task.status)}</td><td>{task.model ?? "—"}</td><td>{formatAdminDuration(task.elapsed_ms)}</td></tr>)}</tbody></table></div>;
}
