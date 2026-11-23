import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Bot,
  CheckCircle2,
  Clock3,
  Database,
  History,
  Server,
  ShieldCheck,
  Users,
  Wrench,
} from "lucide-react";
import { useQueries } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { createAdminApi } from "@/lib/admin-api";
import { accountConversationPath, conversationDetailPath } from "./admin-navigation";
import {
  formatAdminDate,
  formatAdminDuration,
  InitialLoading,
  InlineError,
  LoadError,
  PageHeader,
  RefreshButton,
  statusBucket,
  statusLabel,
  statusToneClass,
} from "./AdminShared";
import { useAdminOutlet } from "./AdminLayout";

function healthText(status: string | undefined) {
  if (status === "ready") return "正常";
  if (status === "degraded") return "部分可用";
  if (status === "not_configured") return "未启用";
  return "不可用";
}

function healthClass(status: string | undefined) {
  if (status === "ready") return "is-ready";
  if (status === "degraded") return "is-degraded";
  if (status === "not_configured") return "is-neutral";
  return "is-unavailable";
}

function auditActionLabel(action: string): string {
  const labels: Record<string, string> = {
    "account.status_changed": "调整账户状态",
    "account.role_changed": "调整账户角色",
    "account.capability_changed": "调整能力授权",
    "model.provider_created": "新增服务商",
    "model.provider_updated": "更新服务商",
    "model.provider_archived": "归档服务商",
    "model.provider_tested": "测试服务商连接",
    "model.profile_created": "新增模型",
    "model.profile_updated": "更新模型",
    "model.profile_archived": "归档模型",
    "model.profile_tested": "测试模型连通性",
    "conversation.log_viewed": "查阅会话日志",
  };
  return labels[action] ?? action;
}

export default function AdminOverviewPage() {
  const { token } = useAdminOutlet();
  const api = createAdminApi(token);

  const [overviewQ, directoryQ, tasksQ, resourcesQ, runtimeQ, auditQ] = useQueries({
    queries: [
      { queryKey: ["admin", "overview"], queryFn: ({ signal }) => api.overview(signal), staleTime: 15_000, retry: false },
      { queryKey: ["admin", "model-directory"], queryFn: ({ signal }) => api.modelDirectory(signal), staleTime: 15_000, retry: false },
      { queryKey: ["admin", "tasks", 12], queryFn: ({ signal }) => api.tasks(12, signal), staleTime: 15_000, retry: false },
      { queryKey: ["admin", "resources"], queryFn: ({ signal }) => api.resourcesHealth(signal), staleTime: 15_000, retry: false },
      { queryKey: ["admin", "runtime"], queryFn: ({ signal }) => api.runtimeHealth(signal), staleTime: 15_000, retry: false },
      { queryKey: ["admin", "audit"], queryFn: ({ signal }) => api.audit(signal), staleTime: 15_000, retry: false },
    ],
  });

  const anyFetching = overviewQ.isFetching || directoryQ.isFetching || tasksQ.isFetching || resourcesQ.isFetching || runtimeQ.isFetching;
  function refetchAll() {
    void overviewQ.refetch();
    void directoryQ.refetch();
    void tasksQ.refetch();
    void resourcesQ.refetch();
    void runtimeQ.refetch();
    void auditQ.refetch();
  }

  const overview = overviewQ.data?.overview;
  const providers = directoryQ.data?.providers ?? [];
  const models = directoryQ.data?.models ?? [];
  const tasks = tasksQ.data?.tasks ?? [];
  const resources = resourcesQ.data;
  const runtime = runtimeQ.data;
  const audits = auditQ.data?.events ?? [];

  if (!overview && overviewQ.isPending) {
    return (
      <div className="admin-route-page">
        <PageHeader eyebrow="控制台" title="运行总览" description="账户、渠道模型、检索知识库与近期用户任务态势。" />
        <InitialLoading label="正在汇总平台运行态势…" />
      </div>
    );
  }

  if (!overview) {
    return (
      <div className="admin-route-page">
        <PageHeader eyebrow="控制台" title="运行总览" />
        <LoadError
          message={overviewQ.error instanceof Error ? overviewQ.error.message : "总览暂时不可用。"}
          onRetry={refetchAll}
        />
      </div>
    );
  }

  const enabledModels = models.filter(m => m.enabled).length;
  const unverifiedModels = models.filter(m => m.connectivity_status !== "verified");
  const failedTasks = tasks.filter(t => statusBucket(t.status) === "failed");
  const succeededTasks = tasks.filter(t => statusBucket(t.status) === "succeeded");

  const alerts: Array<{ id: string; level: "warn" | "fail"; text: string; to: string; cta: string }> = [];
  if (directoryQ.isError) {
    alerts.push({ id: "dir-err", level: "fail", text: "模型目录暂时无法读取，请前往模型页复核。", to: "/admin/models", cta: "去查看" });
  } else if (directoryQ.data && enabledModels === 0) {
    alerts.push({ id: "no-model", level: "fail", text: "当前没有已开放的可用模型，工作台将无法发起对话。", to: "/admin/models", cta: "去配置模型" });
  } else if (directoryQ.data && unverifiedModels.length > 0) {
    alerts.push({ id: "unverified", level: "warn", text: `有 ${unverifiedModels.length} 个模型尚未测通或测试失败，暂未对用户开放。`, to: "/admin/models", cta: "去测试连通" });
  }
  if (resources && resources.status !== "ready") {
    alerts.push({ id: "res-health", level: "warn", text: `资料检索索引状态为「${healthText(resources.status)}」，部分海事法规/案例检索可能受限。`, to: "/admin/operations", cta: "查看详情" });
  }
  if (runtime && runtime.status !== "ready") {
    alerts.push({ id: "rt-health", level: "fail", text: `服务运行组件状态为「${healthText(runtime.status)}」。`, to: "/admin/operations", cta: "排查组件" });
  }
  if (failedTasks.length > 0) {
    const first = failedTasks[0];
    alerts.push({
      id: "task-fail",
      level: "warn",
      text: `近期有 ${failedTasks.length} 条任务未完成（最近一条来自 ${first.account_email}）。`,
      to: conversationDetailPath(first.account_id, first.conversation_id),
      cta: "定位会话",
    });
  }

  return (
    <div className="admin-route-page admin-ov-page">
      <PageHeader
        eyebrow="管理与运维控制台"
        title="运行总览"
        description="实时掌握模型渠道、账户授权、知识索引与近期任务运行状况。"
        action={<RefreshButton loading={anyFetching} onClick={refetchAll} />}
      />

      {(directoryQ.isError || tasksQ.isError || resourcesQ.isError || runtimeQ.isError) && (
        <InlineError message="部分状态区块本次未能读取完成，已按实际失败情况展示，未做静默填充。" onRetry={refetchAll} />
      )}

      <section className="admin-ov-kpi-grid" aria-label="核心运行指标">
        <Link to="/admin/accounts" className="admin-ov-kpi">
          <div className="admin-ov-kpi-head">
            <span><Users size={15} />账户与人员</span>
            <ArrowRight size={14} />
          </div>
          <strong>{overview.active_accounts.toLocaleString("zh-CN")} <small>/ {overview.total_accounts.toLocaleString("zh-CN")}</small></strong>
          <p>{overview.admin_accounts} 位管理员 · {overview.disabled_accounts} 个已停用</p>
        </Link>

        <Link to="/admin/models" className="admin-ov-kpi">
          <div className="admin-ov-kpi-head">
            <span><Bot size={15} />渠道与模型</span>
            <ArrowRight size={14} />
          </div>
          {directoryQ.data ? (
            <>
              <strong>{enabledModels} <small>/ {models.length} 开放</small></strong>
              <p>{providers.length} 家已接入服务商 · {unverifiedModels.length} 个待测/失败</p>
            </>
          ) : (
            <strong className="is-muted">{directoryQ.isPending ? "读取中…" : "读取失败"}</strong>
          )}
        </Link>

        <Link to="/admin/operations" className="admin-ov-kpi">
          <div className="admin-ov-kpi-head">
            <span><Activity size={15} />近期任务</span>
            <ArrowRight size={14} />
          </div>
          {tasksQ.data ? (
            <>
              <strong>{succeededTasks.length} <small>/ {tasks.length} 成功</small></strong>
              <p>{failedTasks.length > 0 ? `${failedTasks.length} 条未完成，可点此排查` : "最近记录运行平稳"}</p>
            </>
          ) : (
            <strong className="is-muted">{tasksQ.isPending ? "读取中…" : "读取失败"}</strong>
          )}
        </Link>

        <Link to="/admin/operations" className="admin-ov-kpi">
          <div className="admin-ov-kpi-head">
            <span><Database size={15} />知识与服务状态</span>
            <ArrowRight size={14} />
          </div>
          {resources && runtime ? (
            <>
              <strong>{healthText(runtime.status === "ready" && resources.status === "ready" ? "ready" : "degraded")}</strong>
              <p>知识索引 {healthText(resources.status)} · 运行服务 {healthText(runtime.status)}</p>
            </>
          ) : (
            <strong className="is-muted">{resourcesQ.isPending || runtimeQ.isPending ? "检测中…" : "读取失败"}</strong>
          )}
        </Link>
      </section>

      <section className="admin-ov-alerts" aria-label="关注事项">
        <div className="admin-ov-section-head">
          <h2><AlertTriangle size={15} />待处理与健康雷达</h2>
        </div>
        {alerts.length === 0 ? (
          <div className="admin-ov-alert-ok">
            <CheckCircle2 size={16} />
            <span>服务商渠道、开放模型与运行组件当前状态正常，暂无阻断项。</span>
          </div>
        ) : (
          <div className="admin-ov-alert-list">
            {alerts.map(item => (
              <div key={item.id} className={`admin-ov-alert-row is-${item.level}`}>
                <span>{item.text}</span>
                <Link to={item.to}>
                  {item.cta}
                  <ArrowRight size={13} />
                </Link>
              </div>
            ))}
          </div>
        )}
      </section>

      <div className="admin-ov-columns">
        <section className="admin-ov-panel" aria-label="已接入模型渠道">
          <div className="admin-ov-section-head">
            <h2><Server size={15} />服务商渠道速览</h2>
            <Link to="/admin/models">管理渠道与模型</Link>
          </div>
          {!directoryQ.data ? (
            <p className="admin-empty">{directoryQ.isPending ? "读取中…" : "未能读取服务商列表。"}</p>
          ) : providers.length === 0 ? (
            <p className="admin-empty">尚未添加任何服务商，请前往模型页配置。</p>
          ) : (
            <div className="admin-ov-list">
              {providers.map(provider => {
                const owned = models.filter(m => m.provider_id === provider.id);
                const live = owned.filter(m => m.enabled).length;
                return (
                  <Link
                    key={provider.id}
                    to={`/admin/models?provider=${encodeURIComponent(provider.id)}`}
                    className="admin-ov-item"
                  >
                    <div>
                      <strong>{provider.name}{provider.notes ? <small> · {provider.notes}</small> : null}</strong>
                      <span>{provider.base_url}</span>
                    </div>
                    <div className="admin-ov-item-right">
                      <span className={`admin-cc-badge ${live > 0 ? "is-verified" : ""}`}>
                        {live}/{owned.length} 开放
                      </span>
                      <ArrowRight size={14} />
                    </div>
                  </Link>
                );
              })}
            </div>
          )}

          {resources && (
            <div className="admin-ov-subblock">
              <div className="admin-ov-section-head">
                <h2><Wrench size={15} />海事资料库索引</h2>
                <span className={`admin-ops-state ${healthClass(resources.status)}`}>{healthText(resources.status)}</span>
              </div>
              <div className="admin-ov-chips">
                {Object.entries(resources.sources).map(([key, item]) => (
                  <div key={key} className="admin-ov-chip">
                    <span>{key === "laws" ? "法规库" : key === "cases" ? "案例库" : key === "papers" ? "论文库" : key}</span>
                    <b className={healthClass(item.status)}>
                      {item.count != null ? `${item.count.toLocaleString("zh-CN")} 条` : healthText(item.status)}
                    </b>
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>

        <section className="admin-ov-panel" aria-label="近期任务与审计">
          <div className="admin-ov-section-head">
            <h2><Clock3 size={15} />近期用户任务</h2>
            <Link to="/admin/operations">完整状态</Link>
          </div>
          {!tasksQ.data ? (
            <p className="admin-empty">{tasksQ.isPending ? "读取中…" : "未能读取任务记录。"}</p>
          ) : tasks.length === 0 ? (
            <p className="admin-empty">暂无最近运行记录。</p>
          ) : (
            <div className="admin-ov-list">
              {tasks.slice(0, 6).map(task => (
                <Link
                  key={task.run_id}
                  to={conversationDetailPath(task.account_id, task.conversation_id)}
                  className="admin-ov-item"
                >
                  <div>
                    <strong>{task.question || "（无题任务）"}</strong>
                    <span>{task.account_email} · {task.model || "默认"} · {formatAdminDuration(task.elapsed_ms)}</span>
                  </div>
                  <div className="admin-ov-item-right">
                    <span className={`admin-cc-badge ${statusToneClass(task.status)}`}>
                      {statusLabel(task.status)}
                    </span>
                    <ArrowRight size={14} />
                  </div>
                </Link>
              ))}
            </div>
          )}

          <div className="admin-ov-subblock">
            <div className="admin-ov-section-head">
              <h2><History size={15} />最近管理操作 ({overview.audit_events})</h2>
              <Link to="/admin/audit">查看全部审计</Link>
            </div>
            {audits.length === 0 ? (
              <p className="admin-empty">暂无管理操作记录。</p>
            ) : (
              <div className="admin-ov-audit-list">
                {audits.slice(0, 5).map(ev => (
                  <div key={ev.id} className="admin-ov-audit-row">
                    <div>
                      <b>{auditActionLabel(ev.action)}</b>
                      <span>
                        <ShieldCheck size={12} />
                        {ev.actor_email}
                        {ev.target_email && (
                          <>
                            {" → "}
                            <Link to={accountConversationPath(ev.target_account_id || "")}>{ev.target_email}</Link>
                          </>
                        )}
                      </span>
                    </div>
                    <time>{formatAdminDate(ev.created_at)}</time>
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
