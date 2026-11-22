import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { ArrowLeft, CircleCheck, CircleX, Clock3, Cpu, MessageSquare } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { Markdown } from "@/components/workspace/PublicMarkdown";
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
        <PageHeader eyebrow={`${modeLabel(detail.conversation.mode)} · 会话日志`} title={detail.conversation.title} description="显示用户问题、公开回答和运行信息。" action={<RefreshButton loading={detailQuery.isFetching} onClick={() => void detailQuery.refetch()} />} />
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
    {run.public_answer && <div className="admin-run-message admin-run-answer"><span className="admin-run-label"><CircleCheck size={14} />回答</span><AdminAnswerText text={run.public_answer} /></div>}
    {run.sources.length > 0 && <div className="admin-run-message admin-run-evidence"><span className="admin-run-label">资料</span><ul className="admin-run-source-list">{run.sources.map(source => <li key={source.source_id}><strong>{source.title}</strong>{source.locator && <small>{source.locator}</small>}</li>)}</ul></div>}
    {run.artifacts.length > 0 && <div className="admin-run-message admin-run-evidence"><span className="admin-run-label">交付</span><ul className="admin-run-artifact-list">{run.artifacts.filter(item => item.kind !== "preview").map(artifact => <li key={artifact.artifact_id}><strong>{artifact.name}</strong><small>{artifact.media_type} · {formatBytes(artifact.size_bytes)}</small></li>)}</ul></div>}
    <dl className="admin-run-facts">{run.model && <div><dt><Cpu size={13} />模型</dt><dd>{run.model}</dd></div>}{run.protocol && <div><dt>协议</dt><dd>{run.protocol}</dd></div>}{run.thinking_mode && <div><dt>思考</dt><dd>{run.thinking_mode}</dd></div>}{run.error_code && <div><dt>错误</dt><dd>{run.error_code}</dd></div>}</dl>
    {run.nodes.length > 0 && <ol className="admin-run-nodes">{run.nodes.map(node => <li key={node.id}><span>{node.label}</span><small>{statusLabel(node.status)}{node.elapsed_ms == null ? "" : ` · ${formatAdminDuration(node.elapsed_ms)}`}</small></li>)}</ol>}
  </article>;
}

const LONG_ANSWER_LIMIT = 1200;

function AdminAnswerText({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(true);
  const isLong = text.length > LONG_ANSWER_LIMIT;
  return <div className={`admin-run-answer-copy ${isLong && !expanded ? "is-collapsed" : ""}`} aria-label="公开回答正文">
    <div className="admin-run-answer-text admin-run-answer-prose"><Markdown content={text} /></div>
    {isLong && <button
      type="button"
      className="admin-run-expand"
      aria-expanded={expanded}
      onClick={() => setExpanded(value => !value)}
    >{expanded ? "收起回答" : "展开完整回答"}</button>}
  </div>;
}

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}
