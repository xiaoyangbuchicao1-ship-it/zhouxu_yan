import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { createAdminApi, type AdminTool } from "@/lib/admin-api";
import { conversationDetailPath } from "./admin-navigation";
import { epochToIso, formatMs, percent } from "./admin-format";
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

function ToolDrawer({ tool, token }: { tool: AdminTool; token: string }) {
  const calls = useQuery({
    queryKey: ["admin", "tool-calls", tool.name],
    queryFn: ({ signal }) => createAdminApi(token).toolCalls(tool.name, signal),
    retry: false,
  });
  return (
    <>
      <header className="admin-drawer-head">
        <SheetTitle><code>{tool.name}</code></SheetTitle>
        <SheetDescription>{tool.summary}</SheetDescription>
      </header>
      <div className="admin-drawer-body">
        <section className="admin-drawer-section">
          <h3>怎么用</h3>
          <div className="admin-drawer-row"><span>可用模式</span><strong>{[tool.chat && "聊天", tool.work && "工作"].filter(Boolean).join("、") || "都不可用"}</strong></div>
          <div className="admin-drawer-row"><span>能力</span><strong>{tool.read_only ? "只读，不改任何东西" : "会写入或执行"}</strong></div>
          <div className="admin-drawer-row"><span>依赖</span><span>{tool.source_label}</span><StatusPill tone={healthTone(tool.status)}>{healthLabel(tool.status)}</StatusPill></div>
          {tool.status_reason && tool.status !== "ready" && <p className="admin-drawer-note">{tool.status_reason}</p>}
          <div className="admin-drawer-row">
            <span>参数</span>
            <span>{tool.parameters.length ? tool.parameters.map(item => `${item.name}${item.required ? "（必填）" : ""}`).join("、") : "无"}</span>
          </div>
        </section>
        <section className="admin-drawer-section">
          <h3>最近 20 次调用</h3>
          {calls.isPending ? <InitialLoading label="正在读取…" />
            : calls.isError ? <InlineError message={calls.error instanceof Error ? calls.error.message : "没有加载出来。"} onRetry={() => void calls.refetch()} />
              : calls.data.calls.length === 0 ? <p className="admin-drawer-note">还没有调用记录。</p> : (
                <ul className="admin-list">
                  {calls.data.calls.map((call, index) => {
                    const when = formatAdminDate(epochToIso(call.ts));
                    const body = (
                      <>
                        <div>
                          <strong>{call.account_email ?? "（系统）"}</strong>
                          <span>{when} · {formatMs(call.duration_ms)}{call.error_code ? ` · ${call.error_code}` : ""}</span>
                        </div>
                        <StatusPill tone={call.outcome === "error" ? "bad" : call.outcome === "empty" ? "warn" : "ok"}>
                          {call.outcome === "error" ? "失败" : call.outcome === "empty" ? "没有结果" : "成功"}
                        </StatusPill>
                      </>
                    );
                    return (
                      <li key={`${call.ts}-${index}`}>
                        {call.account_id && call.conversation_id
                          ? <Link className="admin-list-row" to={conversationDetailPath(call.account_id, call.conversation_id)}>{body}</Link>
                          : <div className="admin-list-row">{body}</div>}
                      </li>
                    );
                  })}
                </ul>
              )}
        </section>
      </div>
    </>
  );
}

export default function AdminToolsPage() {
  const { token } = useAdminOutlet();
  const api = useMemo(() => createAdminApi(token), [token]);
  const [keyword, setKeyword] = useState("");
  const [picked, setPicked] = useState<AdminTool | null>(null);
  const tools = useQuery({ queryKey: ["admin", "tools"], queryFn: ({ signal }) => api.tools(signal), retry: false, refetchInterval: 30_000 });

  const needle = keyword.trim().toLocaleLowerCase();
  const rows = (tools.data?.tools ?? []).filter(tool => !needle || `${tool.name} ${tool.summary}`.toLocaleLowerCase().includes(needle));
  const groups = rows.reduce<Array<{ label: string; items: AdminTool[] }>>((list, tool) => {
    const group = list.find(item => item.label === tool.source_label);
    if (group) group.items.push(tool);
    else list.push({ label: tool.source_label, items: [tool] });
    return list;
  }, []);
  const summary = tools.data?.summary;

  return (
    <div className="admin-route-page">
      <PageHeader title="工具" action={<RefreshButton loading={tools.isFetching} onClick={() => void tools.refetch()} />} />

      {tools.isPending ? <InitialLoading label="正在读取工具…" />
        : tools.isError ? <LoadError message={tools.error instanceof Error ? tools.error.message : "工具没有加载出来。"} onRetry={() => void tools.refetch()} />
          : (
            <>
              <section className="admin-kpi-grid" aria-label="工具概况">
                <div className="admin-kpi"><span className="admin-kpi-label">工具</span><strong>{summary?.total}<small> 个已登记</small></strong><p>聊天可用 {summary?.chat} · 工作可用 {summary?.work}</p></div>
                <div className="admin-kpi"><span className="admin-kpi-label">近 {tools.data.window_days} 天调用</span><strong>{summary?.calls.toLocaleString("zh-CN")}<small> 次</small></strong><p>{summary?.errors} 次失败 · 失败率 {percent(summary?.errors ?? 0, summary?.calls ?? 0)}</p></div>
                <div className="admin-kpi"><span className="admin-kpi-label">依赖状态</span><strong>{rows.filter(tool => tool.status !== "ready").length}<small> 个工具受影响</small></strong><p>{rows.filter(tool => tool.status !== "ready").length ? "见下面标红的依赖" : "依赖都正常"}</p></div>
              </section>
              {!tools.data.recording && <p className="admin-drawer-note">调用统计没有开启，下面的次数和耗时为空。</p>}

              <div className="admin-toolbar">
                <label className="admin-search">
                  <Search size={15} aria-hidden="true" />
                  <input type="search" aria-label="搜索工具" placeholder="搜索工具名称或说明" value={keyword} onChange={event => setKeyword(event.target.value)} />
                </label>
              </div>

              {groups.length === 0 ? <p className="admin-empty">没有匹配的工具。</p> : groups.map(group => (
                <section className="admin-panel" key={group.label} aria-label={group.label}>
                  <header className="admin-panel-head"><h2>{group.label}</h2><span className="admin-cell-muted">{group.items.length} 个工具</span></header>
                  <div className="admin-table-wrap is-flush">
                    <table className="admin-table admin-tools-table">
                      <thead>
                        <tr><th>工具</th><th>模式</th><th>依赖</th><th>近 {tools.data.window_days} 天</th><th>平均耗时</th><th>最近调用</th></tr>
                      </thead>
                      <tbody>
                        {group.items.map(tool => (
                          <tr key={tool.name} className="is-clickable" onClick={() => setPicked(tool)}>
                            <td className="admin-cell-wide">
                              <button type="button" className="admin-cell-link" onClick={() => setPicked(tool)}><code>{tool.name}</code></button>
                              <small className="admin-cell-sub">{tool.summary}</small>
                            </td>
                            <td className="admin-cell-nowrap">
                              {tool.chat && <span className="admin-cc-badge">聊天</span>} {tool.work && <span className="admin-cc-badge">工作</span>} {!tool.read_only && <span className="admin-cc-badge is-unverified">可写</span>}
                            </td>
                            <td><StatusPill tone={healthTone(tool.status)}>{healthLabel(tool.status)}</StatusPill></td>
                            <td className="admin-cell-muted admin-cell-nowrap">
                              {tool.calls} 次{tool.errors > 0 && <> · <b className="admin-text-bad">{tool.errors} 失败</b></>}{tool.empty > 0 && ` · ${tool.empty} 无结果`}
                            </td>
                            <td className="admin-cell-muted admin-cell-nowrap">{formatMs(tool.avg_ms)}</td>
                            <td className="admin-cell-muted admin-cell-nowrap">{tool.last_call_at ? formatAdminDate(epochToIso(tool.last_call_at)) : "还没有"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              ))}
            </>
          )}

      <Sheet open={Boolean(picked)} onOpenChange={open => { if (!open) setPicked(null); }}>
        <SheetContent side="right" className="admin-dialog admin-drawer">
          {picked && <ToolDrawer tool={picked} token={token} />}
        </SheetContent>
      </Sheet>
    </div>
  );
}
