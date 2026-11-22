import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, CircleCheck, CircleX, Clock3, Cpu, MessageSquare } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { createAdminApi, type AdminConversationRun } from "@/lib/admin-api";
import { useAdminOutlet } from "./AdminLayout";
import { accountConversationPath } from "./admin-navigation";
import { formatAdminDate, formatAdminDuration, InitialLoading, InlineError, LoadError, modeLabel, PageHeader, RefreshButton, statusLabel } from "./AdminShared";

export default function AdminConversationDetailPage() {
  const { accountId = "", conversationId = "" } = useParams();
  const { token } = useAdminOutlet();
  const detailQuery = useQuery({
    queryKey: ["admin", "account", accountId, "conversation", conversationId],
    queryFn: () => createAdminApi(token).conversationDetail(accountId, conversationId, { limit: 50 }),
    retry: false,
    placeholderData: previous => previous,
  });
  const detail = detailQuery.data;
  return <div className="admin-route-page admin-detail-page">
    <Link className="admin-back-link" to={accountConversationPath(accountId)}><ArrowLeft size={15} />返回会话</Link>
    {!detail && detailQuery.isPending ? <InitialLoading label="正在读取会话…" />
      : !detail ? <LoadError message={detailQuery.error instanceof Error ? detailQuery.error.message : "会话暂时不可用。"} onRetry={() => void detailQuery.refetch()} />
      : <>
        <PageHeader eyebrow={`${modeLabel(detail.conversation.mode)} · 会话日志`} title={detail.conversation.title} description="只显示公开回答和安全运行信息。" action={<RefreshButton loading={detailQuery.isFetching} onClick={() => void detailQuery.refetch()} />} />
        {detailQuery.isError && <InlineError message={detailQuery.error instanceof Error ? detailQuery.error.message : "刷新没有完成。"} onRetry={() => void detailQuery.refetch()} />}
        <section className="admin-run-list" aria-label="会话运行记录">{detail.runs.length === 0 ? <p className="admin-empty">还没有运行记录。</p> : detail.runs.map(run => <RunCard key={run.run_id} run={run} />)}</section>
      </>}
  </div>;
}

function RunCard({ run }: { run: AdminConversationRun }) {
  const statusClass = run.status === "succeeded" ? "is-success" : run.status === "failed" ? "is-error" : "";
  return <article className={`admin-run-card ${statusClass}`}>
    <header className="admin-run-card-header"><div><span className="admin-run-status">{run.status === "succeeded" ? <CircleCheck size={15} /> : run.status === "failed" ? <CircleX size={15} /> : <Clock3 size={15} />}{statusLabel(run.status)}</span><time dateTime={run.created_at}>{formatAdminDate(run.created_at)}</time></div><span>{formatAdminDuration(run.elapsed_ms)}</span></header>
    <div className="admin-run-message"><span className="admin-run-label"><MessageSquare size={14} />用户</span><p>{run.user_question}</p></div>
    {run.public_answer && <div className="admin-run-message admin-run-answer"><span className="admin-run-label"><CircleCheck size={14} />回答</span><p>{run.public_answer}</p></div>}
    <dl className="admin-run-facts">{run.model && <div><dt><Cpu size={13} />模型</dt><dd>{run.model}</dd></div>}{run.protocol && <div><dt>协议</dt><dd>{run.protocol}</dd></div>}{run.thinking_mode && <div><dt>思考</dt><dd>{run.thinking_mode}</dd></div>}{run.error_code && <div><dt>错误</dt><dd>{run.error_code}</dd></div>}</dl>
    {run.nodes.length > 0 && <ol className="admin-run-nodes">{run.nodes.map(node => <li key={node.id}><span>{node.label}</span><small>{statusLabel(node.status)}{node.elapsed_ms == null ? "" : ` · ${formatAdminDuration(node.elapsed_ms)}`}</small></li>)}</ol>}
  </article>;
}
