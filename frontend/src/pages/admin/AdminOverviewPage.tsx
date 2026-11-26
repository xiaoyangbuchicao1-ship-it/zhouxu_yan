import { ArrowRight } from "lucide-react";
import { useQueries } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { createAdminApi, type AdminHealthItem, type AdminTask } from "@/lib/admin-api";
import { conversationDetailPath } from "./admin-navigation";
import {
  formatAdminDate,
  healthLabel,
  healthTone,
  InitialLoading,
  InlineError,
  LoadError,
  PageHeader,
  RefreshButton,
  statusLabel,
  StatusPill,
  taskTone,
} from "./AdminShared";
import { useAdminOutlet } from "./AdminLayout";

const SOURCE_LABELS: Record<string, string> = { laws: "法规", cases: "案例", papers: "论文" };
const COMPONENT_LABELS: Record<string, string> = {
  database: "数据库",
  runtime: "任务运行",
  events: "事件记录",
  model_directory: "模型目录",
};

function Kpi({ label, to, children }: { label: string; to?: string; children: ReactNode }) {
  const body = <><span className="admin-kpi-label">{label}{to && <ArrowRight size={14} aria-hidden="true" />}</span>{children}</>;
  return to ? <Link className="admin-kpi" to={to}>{body}</Link> : <div className="admin-kpi">{body}</div>;
}

function Panel({ title, link, children }: { title: string; link?: { to: string; label: string }; children: ReactNode }) {
  return (
    <section className="admin-panel">
      <header className="admin-panel-head">
        <h2>{title}</h2>
        {link && <Link to={link.to}>{link.label}</Link>}
      </header>
      {children}
    </section>
  );
}

function HealthRows({ items, labels }: { items: Record<string, AdminHealthItem>; labels: Record<string, string> }) {
  return (
    <ul className="admin-list">
      {Object.entries(items).map(([key, item]) => (
        <li key={key} className="admin-list-row">
          <div>
            <strong>{labels[key] ?? key}</strong>
            {item.count != null && <span>{item.count.toLocaleString("zh-CN")} 条</span>}
            {item.status !== "ready" && item.reason && <span>{item.reason}</span>}
          </div>
          <StatusPill tone={healthTone(item.status)}>{healthLabel(item.status)}</StatusPill>
        </li>
      ))}
    </ul>
  );
}

function TaskRows({ tasks }: { tasks: AdminTask[] }) {
  return (
    <ul className="admin-list">
      {tasks.map(task => (
        <li key={task.run_id}>
          <Link className="admin-list-row" to={conversationDetailPath(task.account_id, task.conversation_id)}>
            <div>
              <strong>{task.question || "（没有标题）"}</strong>
              <span>{task.account_email} · {formatAdminDate(task.created_at)}{task.model ? ` · ${task.model}` : ""}</span>
            </div>
            <StatusPill tone={taskTone(task.status)}>{statusLabel(task.status)}</StatusPill>
          </Link>
        </li>
      ))}
    </ul>
  );
}

function formatMemSize(bytes: number | null | undefined): string {
  if (bytes == null || bytes < 0) return "--";
  const gb = bytes / (1024 ** 3);
  if (gb >= 1) return `${gb.toFixed(1)} GB`;
  return `${Math.round(bytes / (1024 ** 2))} MB`;
}

function formatMs(ms: number | null | undefined): string {
  if (ms == null) return "--";
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)} s`;
  return `${Math.round(ms)} ms`;
}

function loadTone(level: "normal" | "busy" | "overloaded"): "ok" | "warn" | "bad" {
  return level === "normal" ? "ok" : level === "busy" ? "warn" : "bad";
}

function loadLabel(level: "normal" | "busy" | "overloaded"): string {
  return level === "normal" ? "负载正常" : level === "busy" ? "负载偏高" : "并发拥挤";
}

export default function AdminOverviewPage() {
  const { token } = useAdminOutlet();
  const api = createAdminApi(token);

  const [overviewQ, directoryQ, resourcesQ, runtimeQ, metricsQ] = useQueries({
    queries: [
      { queryKey: ["admin", "overview"], queryFn: ({ signal }) => api.overview(signal), staleTime: 15_000, retry: false },
      { queryKey: ["admin", "model-directory"], queryFn: ({ signal }) => api.modelDirectory(signal), staleTime: 15_000, retry: false },
      { queryKey: ["admin", "resources"], queryFn: ({ signal }) => api.resourcesHealth(signal), staleTime: 15_000, retry: false },
      { queryKey: ["admin", "runtime"], queryFn: ({ signal }) => api.runtimeHealth(signal), staleTime: 15_000, retry: false },
      { queryKey: ["admin", "system-metrics"], queryFn: ({ signal }) => api.systemMetrics(signal), staleTime: 10_000, refetchInterval: 15_000, retry: false },
    ],
  });
  const queries = [overviewQ, directoryQ, resourcesQ, runtimeQ, metricsQ];
  const refetchAll = () => queries.forEach(item => void item.refetch());

  const overview = overviewQ.data?.overview;
  if (!overview) {
    return (
      <div className="admin-route-page">
        <PageHeader title="总览" />
        {overviewQ.isPending
          ? <InitialLoading label="正在读取…" />
          : <LoadError message={overviewQ.error instanceof Error ? overviewQ.error.message : "总览没有加载出来。"} onRetry={refetchAll} />}
      </div>
    );
  }

  const providers = directoryQ.data?.providers ?? [];
  const models = directoryQ.data?.models ?? [];
  const resources = resourcesQ.data;
  const runtime = runtimeQ.data;
  const metrics = metricsQ.data;
  const enabledModels = models.filter(model => model.enabled).length;
  const unverified = models.filter(model => model.enabled && model.connectivity_status !== "verified").length;

  const alerts: Array<{ id: string; tone: "warn" | "bad"; text: string; to?: string; cta?: string }> = [];
  if (directoryQ.data && enabledModels === 0) {
    alerts.push({ id: "no-model", tone: "bad", text: "没有已启用的模型，用户无法发起对话。", to: "/admin/models", cta: "去配置" });
  } else if (unverified > 0) {
    alerts.push({ id: "unverified", tone: "warn", text: `${unverified} 个已启用的模型还没测通。`, to: "/admin/models", cta: "去测试" });
  }
  if (runtime && runtime.status !== "ready") {
    alerts.push({ id: "runtime", tone: "bad", text: `任务运行${healthLabel(runtime.status)}，用户可能无法正常使用。` });
  }
  if (resources && resources.status !== "ready") {
    alerts.push({ id: "resources", tone: "warn", text: `资料库${healthLabel(resources.status)}，检索可能受影响。` });
  }
  if (metrics && metrics.capacity.load_level !== "normal") {
    const detail = metrics.capacity.reasons.length > 0 ? metrics.capacity.reasons.join("；") : "请注意资源余量。";
    alerts.push({ id: "capacity", tone: metrics.capacity.load_level === "overloaded" ? "bad" : "warn", text: `${loadLabel(metrics.capacity.load_level)}：${detail}` });
  }
  if (overview.stuck_tasks.length > 0) {
    alerts.push({ id: "stuck", tone: "warn", text: `${overview.stuck_tasks.length} 个任务超过 1 小时还没结束。` });
  }

  const partialError = directoryQ.isError || resourcesQ.isError || runtimeQ.isError || metricsQ.isError;
  const needsAttention = overview.recent_failed.length > 0 || overview.stuck_tasks.length > 0;

  return (
    <div className="admin-route-page">
      <PageHeader title="总览" action={<RefreshButton loading={queries.some(item => item.isFetching)} onClick={refetchAll} />} />

      {partialError && <InlineError message="有些数据没有加载出来。" onRetry={refetchAll} />}

      <section className="admin-kpi-grid" aria-label="概况">
        <Kpi label="账户" to="/admin/accounts">
          <strong>{overview.active_accounts.toLocaleString("zh-CN")}<small> / {overview.total_accounts.toLocaleString("zh-CN")} 正常</small></strong>
          <p>{overview.admin_accounts} 位管理员 · {overview.disabled_accounts} 个已停用</p>
        </Kpi>
        <Kpi label="模型" to="/admin/models">
          {directoryQ.data ? (
            <>
              <strong>{enabledModels}<small> / {models.length} 已启用</small></strong>
              <p>{providers.length} 个服务商</p>
            </>
          ) : <strong className="is-muted">{directoryQ.isPending ? "读取中…" : "读取失败"}</strong>}
        </Kpi>
        <Kpi label="近 24 小时任务">
          <strong>{overview.tasks_24h.toLocaleString("zh-CN")}<small> 个</small></strong>
          <p>{overview.failed_24h} 个未完成 · {overview.active_tasks} 个进行中</p>
        </Kpi>
      </section>

      {alerts.length > 0 && (
        <ul className="admin-notice-list" aria-label="需要处理">
          {alerts.map(item => (
            <li key={item.id} className={`admin-notice is-${item.tone}`}>
              <span>{item.text}</span>
              {item.to && <Link to={item.to}>{item.cta}<ArrowRight size={13} aria-hidden="true" /></Link>}
            </li>
          ))}
        </ul>
      )}

      {metrics && (
        <section className="admin-metrics-section" aria-label="系统负载与响应">
          <header className="admin-metrics-head">
            <h2>系统负载与响应</h2>
            <div className="admin-metrics-head-meta">
              <StatusPill tone={loadTone(metrics.capacity.load_level)}>{loadLabel(metrics.capacity.load_level)}</StatusPill>
              <span>当前配置建议同时运行 ≤{metrics.capacity.recommended_concurrent_runs} 个任务 · 约 {metrics.capacity.recommended_online_users} 人同时在线</span>
            </div>
          </header>
          <div className="admin-kpi-grid is-four">
            <article className="admin-kpi">
              <div className="admin-kpi-label"><span>容器算力与内存</span></div>
              <strong>
                {formatMemSize(metrics.compute.memory_used_bytes)}
                <small> / {formatMemSize(metrics.compute.memory_limit_bytes)}</small>
              </strong>
              <p>
                CPU {metrics.compute.cpu_cores} 核{metrics.compute.cpu_percent != null ? ` (${metrics.compute.cpu_percent}%)` : ""}
                {metrics.opensearch.status === "ready" && metrics.opensearch.heap_used_bytes != null
                  ? ` · 检索堆 ${formatMemSize(metrics.opensearch.heap_used_bytes)}/${formatMemSize(metrics.opensearch.heap_max_bytes)}`
                  : ""}
              </p>
            </article>
            <article className="admin-kpi">
              <div className="admin-kpi-label"><span>实时并发与排队</span></div>
              <strong>
                {metrics.concurrency.active_runs}
                <small> / 上限 {metrics.concurrency.max_concurrent_runs || "不限"}</small>
              </strong>
              <p>排队 {metrics.concurrency.queued_runs} 个 · 实时流 {metrics.concurrency.active_sse_streams} 条</p>
            </article>
            <article className="admin-kpi">
              <div className="admin-kpi-label"><span>接口响应 (近 5 分钟)</span></div>
              <strong>
                {metrics.http.p95_ms != null ? formatMs(metrics.http.p95_ms) : "--"}
                <small> P95</small>
              </strong>
              <p>P50 {formatMs(metrics.http.p50_ms)} · {metrics.http.rpm}/分 · 异常 {metrics.http.error_count}</p>
            </article>
            <article className="admin-kpi">
              <div className="admin-kpi-label"><span>模型响应速度</span></div>
              <strong>
                {formatMs(metrics.model_calls.recent_p95_ms ?? metrics.model_calls.p95_24h_ms)}
                <small> P95</small>
              </strong>
              <p>
                近 15 分 {metrics.model_calls.recent_calls} 次 · 24h {metrics.model_calls.calls_24h} 次 · 异常 {metrics.model_calls.errors_24h}
              </p>
            </article>
          </div>
        </section>
      )}

      <div className="admin-overview-grid">
        <Panel title="资料库">
          {resources ? <HealthRows items={resources.sources} labels={SOURCE_LABELS} />
            : <p className="admin-empty">{resourcesQ.isPending ? "读取中…" : "没有加载出来。"}</p>}
        </Panel>

        <Panel title="服务状态">
          {runtime ? <HealthRows items={runtime.components} labels={COMPONENT_LABELS} />
            : <p className="admin-empty">{runtimeQ.isPending ? "读取中…" : "没有加载出来。"}</p>}
        </Panel>
      </div>

      <Panel title="需要处理的任务">
        {!needsAttention ? <p className="admin-empty">最近 7 天没有未完成的任务。</p> : (
          <>
            {overview.stuck_tasks.length > 0 && (
              <>
                <p className="admin-panel-sub">卡住的</p>
                <TaskRows tasks={overview.stuck_tasks} />
              </>
            )}
            {overview.recent_failed.length > 0 && (
              <>
                <p className="admin-panel-sub">最近未完成的</p>
                <TaskRows tasks={overview.recent_failed} />
              </>
            )}
          </>
        )}
      </Panel>
    </div>
  );
}
