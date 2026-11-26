import { useQuery } from "@tanstack/react-query";
import { Download, LoaderCircle, Sparkles, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { createAdminApi } from "@/lib/admin-api";
import { epochToIso, formatBytes, formatMs, percent } from "./admin-format";
import { useAdminOutlet } from "./AdminLayout";
import {
  formatAdminDate,
  healthLabel,
  healthTone,
  InitialLoading,
  InlineError,
  LoadError,
  PageHeader,
  RefreshButton,
  StatusPill,
} from "./AdminShared";

function storageTone(status: "ok" | "warn" | "critical"): "ok" | "warn" | "bad" {
  if (status === "critical") return "bad";
  if (status === "warn") return "warn";
  return "ok";
}

function storageLabel(status: "ok" | "warn" | "critical"): string {
  if (status === "critical") return "空间告急";
  if (status === "warn") return "空间偏低";
  return "空间充足";
}

export default function AdminDataPage() {
  const { token } = useAdminOutlet();
  const api = useMemo(() => createAdminApi(token), [token]);
  const data = useQuery({ queryKey: ["admin", "data"], queryFn: ({ signal }) => api.data(signal), retry: false, refetchInterval: query => (query.state.data?.disk.some(item => item.size_bytes === null) ? 4_000 : 60_000) });
  const value = data.data;
  const biggest = Math.max(1, ...(value?.disk ?? []).map(item => item.size_bytes ?? 0));
  const [cleaning, setCleaning] = useState(false);
  const [backingUp, setBackingUp] = useState(false);
  const [downloadingName, setDownloadingName] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  async function runCleanup() {
    if (cleaning) return;
    setCleaning(true);
    setActionError(null);
    setNotice(null);
    try {
      const res = await api.runDataCleanup();
      setNotice(`安全清理完成：释放 ${formatBytes(res.cleaned_bytes)}（${res.cleaned_dirs} 个临时目录、${res.cleaned_rows} 条过期票据）。`);
      await data.refetch();
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : "安全清理未完成，请稍后重试。");
    } finally {
      setCleaning(false);
    }
  }

  async function runBackup() {
    if (backingUp) return;
    setBackingUp(true);
    setActionError(null);
    setNotice(null);
    try {
      const created = await api.createDataBackup();
      setNotice(`已生成热备份：${created.name}（${formatBytes(created.size_bytes)}）。`);
      await data.refetch();
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : "备份未完成，请稍后重试。");
    } finally {
      setBackingUp(false);
    }
  }

  async function downloadBackup(name: string) {
    if (downloadingName) return;
    setDownloadingName(name);
    setActionError(null);
    try {
      await api.downloadDataBackup(name);
    } catch (cause) {
      setActionError(cause instanceof Error ? cause.message : "下载备份失败，请稍后重试。");
    } finally {
      setDownloadingName(null);
    }
  }

  const hostStorage = value?.host_storage;
  const elasticStorage = value?.elastic_storage;
  const cleanup = value?.cleanup_preview;

  return (
    <div className="admin-route-page">
      <PageHeader title="数据库" action={<RefreshButton loading={data.isFetching} onClick={() => void data.refetch()} />} />

      {data.isPending ? <InitialLoading label="正在读取…" />
        : data.isError || !value ? <LoadError message={data.error instanceof Error ? data.error.message : "没有加载出来。"} onRetry={() => void data.refetch()} />
          : (
            <>
              {notice && <p className="admin-success" role="status"><Sparkles size={15} />{notice}</p>}
              {actionError && <InlineError message={actionError} />}

              {(hostStorage || cleanup) && (
                <section className="admin-overview-grid" style={{ marginBottom: 16 }} aria-label="存储与安全清理">
                  {hostStorage && (
                    <article className="admin-kpi admin-storage-card">
                      <div className="admin-kpi-label">
                        <span>磁盘可用空间</span>
                        <StatusPill tone={storageTone(hostStorage.status)}>{storageLabel(hostStorage.status)}</StatusPill>
                      </div>
                      <strong className="admin-storage-value">{formatBytes(hostStorage.free_bytes)}</strong>
                      <p className="admin-storage-meta"><span>总容量 {formatBytes(hostStorage.total_bytes)}</span><span>已用 {formatBytes(hostStorage.used_bytes)}</span></p>
                      {elasticStorage && (
                        <p className="admin-storage-detail">
                          {elasticStorage.policy === "paused_critical"
                            ? "空间低于 5 GB，已自动暂停新附件上传"
                            : elasticStorage.policy === "tightened_warn"
                              ? `空间低于 20 GB，默认单人空间已收紧至 ${elasticStorage.effective_default_quota_mb} MB`
                              : elasticStorage.base_quota_mb > 0
                                ? `弹性配额正常：默认单人上限 ${elasticStorage.base_quota_mb} MB`
                                : "弹性配额正常：当前不限制单人空间"}
                        </p>
                      )}
                    </article>
                  )}
                  {cleanup && (
                    <article className="admin-kpi admin-storage-card">
                      <div className="admin-kpi-label">
                        <span>安全清理</span>
                        <button
                          type="button"
                          className="admin-row-action"
                          disabled={cleaning || backingUp || cleanup.reclaimable_items === 0}
                          onClick={() => void runCleanup()}
                        >
                          {cleaning ? <LoaderCircle className="admin-spin" size={14} /> : <Trash2 size={14} />}
                          {cleaning ? "清理中…" : "安全清理"}
                        </button>
                      </div>
                      <strong className="admin-storage-value">{formatBytes(cleanup.reclaimable_bytes)}</strong>
                      <p className="admin-storage-meta"><span>可释放 · {cleanup.reclaimable_items} 项</span></p>
                      <p className="admin-storage-detail">
                        {cleanup.breakdown.length > 0
                          ? cleanup.breakdown.map(item => `${item.label} ${item.items} 项（${formatBytes(item.bytes)}）`).join(" · ")
                          : "暂无可清理的临时文件或过期记录。"}
                      </p>
                      {cleanup.last_cleanup_at && <p className="admin-storage-last">上次清理：{formatAdminDate(epochToIso(cleanup.last_cleanup_at))}</p>}
                    </article>
                  )}
                </section>
              )}

              <section className="admin-panel" aria-labelledby="data-corpus">
                <header className="admin-panel-head"><h2 id="data-corpus">检索资料</h2><span className="admin-cell-muted">近 24 小时的使用情况</span></header>
                <div className="admin-data-grid">
                  {value.corpus.map(item => (
                    <article key={item.key} className="admin-data-card">
                      <div className="admin-data-card-head"><strong>{item.label}</strong><StatusPill tone={healthTone(item.status)}>{healthLabel(item.status)}</StatusPill></div>
                      <p className="admin-data-count">{item.count != null ? item.count.toLocaleString("zh-CN") : "—"}<small> 条</small></p>
                      {item.reason && <p className="admin-cell-muted">{item.reason}</p>}
                      <p className="admin-cell-muted">
                        {value.recording
                          ? `检索 ${item.searches_24h} 次 · 没有结果 ${percent(item.empty_24h, item.searches_24h)} · 平均 ${formatMs(item.avg_ms_24h)}${item.errors_24h ? ` · ${item.errors_24h} 次出错` : ""}`
                          : "调用统计没有开启"}
                      </p>
                    </article>
                  ))}
                </div>
              </section>

              <section className="admin-panel" aria-labelledby="data-stores">
                <header className="admin-panel-head"><h2 id="data-stores">数据库文件</h2><span className="admin-cell-muted">{value.stores.length} 个</span></header>
                <div className="admin-table-wrap is-flush">
                  <table className="admin-table">
                    <thead><tr><th>名称</th><th>用途</th><th>大小</th><th>最近写入</th><th>表</th></tr></thead>
                    <tbody>
                      {value.stores.map(store => (
                        <tr key={store.file}>
                          <td className="admin-cell-nowrap"><strong className="admin-cell-title">{store.label}</strong><small className="admin-cell-sub">{store.file}</small></td>
                          <td className="admin-cell-muted">{store.purpose || "—"}</td>
                          <td className="admin-cell-muted admin-cell-nowrap">{formatBytes(store.size_bytes)}</td>
                          <td className="admin-cell-muted admin-cell-nowrap">{formatAdminDate(epochToIso(store.modified_at))}</td>
                          <td className="admin-cell-wide">
                            {store.tables.length === 0 ? <span className="admin-cell-muted">只显示大小</span> : (
                              <details className="admin-tables">
                                <summary>{store.tables.length} 张表，共 {store.tables.reduce((sum, table) => sum + table.rows, 0).toLocaleString("zh-CN")} 行</summary>
                                <ul>{store.tables.map(table => <li key={table.name}><code>{table.name}</code><span>{table.rows.toLocaleString("zh-CN")}</span></li>)}</ul>
                              </details>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>

              <div className="admin-overview-grid">
                <section className="admin-panel" aria-labelledby="data-disk">
                  <header className="admin-panel-head"><h2 id="data-disk">磁盘占用</h2></header>
                  <ul className="admin-bars">
                    {value.disk.map(item => (
                      <li key={item.label}>
                        <div><strong>{item.label}</strong><span>{item.size_bytes === null ? "正在统计…" : `${formatBytes(item.size_bytes)} · ${(item.files ?? 0).toLocaleString("zh-CN")} 个文件`}</span></div>
                        <span className="admin-bar"><i style={{ width: `${item.size_bytes === null ? 0 : Math.max(2, Math.round((item.size_bytes / biggest) * 100))}%` }} /></span>
                      </li>
                    ))}
                  </ul>
                </section>
                <section className="admin-panel" aria-labelledby="data-backups">
                  <header className="admin-panel-head">
                    <h2 id="data-backups">数据库热备份</h2>
                    <button
                      type="button"
                      className="admin-row-action"
                      disabled={backingUp || cleaning}
                      onClick={() => void runBackup()}
                    >
                      {backingUp && <LoaderCircle className="admin-spin" size={14} />}
                      {backingUp ? "备份中…" : "立即备份"}
                    </button>
                  </header>
                  {value.backups.length === 0 ? <p className="admin-empty">暂无热备份，点击右上角「立即备份」生成可迁移压缩包。</p> : (
                    <ul className="admin-list">
                      {value.backups.map(item => (
                        <li key={item.name} className="admin-list-row">
                          <div><strong>{item.name}</strong><span>{formatAdminDate(epochToIso(item.modified_at))} · {formatBytes(item.size_bytes)}</span></div>
                          <button
                            type="button"
                            className="admin-row-action"
                            disabled={downloadingName !== null}
                            onClick={() => void downloadBackup(item.name)}
                          >
                            {downloadingName === item.name ? <LoaderCircle className="admin-spin" size={14} /> : <Download size={14} />}
                            {downloadingName === item.name ? "下载中…" : "下载"}
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>
              </div>
            </>
          )}
    </div>
  );
}
