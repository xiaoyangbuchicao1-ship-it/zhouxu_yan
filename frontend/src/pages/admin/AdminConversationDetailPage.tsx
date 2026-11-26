import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import {
  AlertCircle,
  ArrowLeft,
  BookOpen,
  CheckCircle2,
  CircleCheck,
  CircleX,
  Clock3,
  Cpu,
  Download,
  ExternalLink,
  Eye,
  FileText,
  LoaderCircle,
  MessageSquare,
  Paperclip,
  Search,
  Wrench,
} from "lucide-react";
import { Link, useParams } from "react-router-dom";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Markdown } from "@/components/workspace/PublicMarkdown";
import {
  createAdminApi,
  type AdminConversationRun,
  type AdminConversationSource,
  type AdminPassageListResponse,
  type AdminSourceDocument,
} from "@/lib/admin-api";
import { useAdminOutlet } from "./AdminLayout";
import { accountConversationPath } from "./admin-navigation";
import {
  formatAdminDate,
  formatAdminDuration,
  InitialLoading,
  InlineError,
  LoadError,
  modeLabel,
  PageHeader,
  RefreshButton,
  statusLabel,
} from "./AdminShared";

function sourceKindLabel(kind: AdminConversationSource["kind"]): string {
  if (kind === "law") return "法规";
  if (kind === "case") return "案例";
  if (kind === "paper") return "论文";
  return "资料";
}

function unitLabel(unit: string | null | undefined): string | null {
  if (!unit) return null;
  const labels: Record<string, string> = {
    memory: "整理上下文",
    planning: "理解与规划",
    action: "检索与工具执行",
    model_processing: "理解问题并规划步骤",
    synthesizing: "综合分析与推进",
    skill_reading: "加载专业技能",
    law_searching: "检索法规",
    case_searching: "检索案例",
    case_reading: "核对案例原文",
    paper_searching: "检索论文",
    paper_reading: "精读论文",
    web_searching: "联网检索",
    material_parsing: "解析上传材料",
    material_reading: "读取上传材料",
    material_searching: "检索上传材料",
    source_reading: "核对资料原文",
    file_reading: "读取文稿",
    file_writing: "撰写与保存文稿",
    file_compiling: "编译输出文档",
    execution: "执行校验",
    writing: "组织最终回答",
    finalizing: "整理交付",
    retrieval: "资料检索",
  };
  return labels[unit] ?? unit;
}

export default function AdminConversationDetailPage() {
  const { accountId = "", conversationId = "" } = useParams();
  const { token } = useAdminOutlet();
  const api = createAdminApi(token);

  const [extraRuns, setExtraRuns] = useState<AdminConversationRun[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  const [sourcePreview, setSourcePreview] = useState<{
    source: AdminConversationSource;
    loading: boolean;
    doc: AdminSourceDocument | null;
    error: string | null;
  } | null>(null);

  const [materialPreview, setMaterialPreview] = useState<{
    materialId: string;
    filename: string;
    loading: boolean;
    loadingMore: boolean;
    data: AdminPassageListResponse | null;
    error: string | null;
  } | null>(null);
  const [downloadingKey, setDownloadingKey] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const detailQuery = useQuery({
    queryKey: ["admin", "account", accountId, "conversation", conversationId],
    queryFn: () => api.conversationDetail(accountId, conversationId, { limit: 50 }),
    retry: false,
    placeholderData: previous => previous,
  });
  const detail = detailQuery.data;

  useEffect(() => {
    setExtraRuns([]);
    setNextCursor(detail?.next_cursor ?? null);
  }, [accountId, conversationId, detail]);

  async function loadMoreRuns() {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const next = await api.conversationDetail(accountId, conversationId, {
        cursor: nextCursor,
        limit: 50,
      });
      setExtraRuns(prev => [...prev, ...next.runs]);
      setNextCursor(next.next_cursor);
    } finally {
      setLoadingMore(false);
    }
  }

  async function openSourcePreview(source: AdminConversationSource) {
    setSourcePreview({ source, loading: true, doc: null, error: null });
    try {
      const doc = await api.sourceDocument(source.source_id);
      setSourcePreview({ source, loading: false, doc, error: null });
    } catch (cause) {
      const message =
        cause && typeof cause === "object" && "status" in cause && cause.status === 404
          ? "资料库里没有这条来源的原文。"
          : cause instanceof Error
            ? cause.message
            : "原文没有加载出来。";
      setSourcePreview({ source, loading: false, doc: null, error: message });
    }
  }

  async function openMaterialPreview(materialId: string, filename: string) {
    setDownloadError(null);
    setMaterialPreview({ materialId, filename, loading: true, loadingMore: false, data: null, error: null });
    try {
      const data = await api.materialPassages(materialId);
      setMaterialPreview({ materialId, filename, loading: false, loadingMore: false, data, error: null });
    } catch (cause) {
      setMaterialPreview({
        materialId,
        filename,
        loading: false,
        loadingMore: false,
        data: null,
        error: cause instanceof Error ? cause.message : "附件内容暂时无法读取。",
      });
    }
  }

  async function loadMoreMaterialPassages() {
    if (!materialPreview?.data?.next_cursor || materialPreview.loadingMore) return;
    const { materialId, data } = materialPreview;
    setMaterialPreview(prev => prev ? { ...prev, loadingMore: true } : prev);
    try {
      const next = await api.materialPassages(materialId, data.next_cursor);
      setMaterialPreview(prev => prev && prev.data ? {
        ...prev,
        loadingMore: false,
        data: {
          ...next,
          chunks: [...prev.data.chunks, ...next.chunks],
        },
      } : prev);
    } catch (cause) {
      setMaterialPreview(prev => prev ? {
        ...prev,
        loadingMore: false,
        error: cause instanceof Error ? cause.message : "加载更多片段失败。",
      } : prev);
    }
  }

  async function handleDownloadMaterial(materialId: string, filename: string) {
    if (downloadingKey) return;
    setDownloadingKey(`mat:${materialId}`);
    setDownloadError(null);
    try {
      await api.downloadMaterial(materialId, filename);
    } catch (cause) {
      setDownloadError(cause instanceof Error ? cause.message : "下载附件失败，请稍后重试。");
    } finally {
      setDownloadingKey(null);
    }
  }

  async function handleDownloadArtifact(runId: string, artifactId: string, filename: string) {
    if (downloadingKey) return;
    setDownloadingKey(`art:${artifactId}`);
    setDownloadError(null);
    try {
      await api.downloadRunArtifact(accountId, conversationId, runId, artifactId, filename);
    } catch (cause) {
      setDownloadError(cause instanceof Error ? cause.message : "下载交付文件失败，请稍后重试。");
    } finally {
      setDownloadingKey(null);
    }
  }

  const allRuns = [...(detail?.runs ?? []), ...extraRuns].filter(
    (item, idx, arr) => arr.findIndex(other => other.run_id === item.run_id) === idx,
  );
  const owner = detail?.account;
  const headerDesc = [
    detail ? modeLabel(detail.conversation.mode) : null,
    owner ? owner.display_name || owner.email : null,
    `${allRuns.length} 次运行`,
  ].filter(Boolean).join(" · ");

  return (
    <div className="admin-route-page admin-detail-page">
      <Link className="admin-back-link" to={accountConversationPath(accountId)}>
        <ArrowLeft size={15} />
        返回会话列表
      </Link>
      {!detail && detailQuery.isPending ? (
        <InitialLoading label="正在读取会话详情与检索依据…" />
      ) : !detail ? (
        <LoadError
          message={detailQuery.error instanceof Error ? detailQuery.error.message : "会话暂时不可用。"}
          onRetry={() => void detailQuery.refetch()}
        />
      ) : (
        <>
          <PageHeader
            title={detail.conversation.title}
            description={headerDesc}
            action={<RefreshButton loading={detailQuery.isFetching} onClick={() => void detailQuery.refetch()} />}
          />
          {detailQuery.isError && (
            <InlineError
              message={detailQuery.error instanceof Error ? detailQuery.error.message : "刷新没有完成。"}
              onRetry={() => void detailQuery.refetch()}
            />
          )}
          {downloadError && <InlineError message={downloadError} />}
          <section className="admin-run-list" aria-label="会话运行记录">
            {allRuns.length === 0 ? (
              <p className="admin-empty">还没有运行记录。</p>
            ) : (
              allRuns.map(run => (
                <RunCard
                  key={run.run_id}
                  run={run}
                  downloadingKey={downloadingKey}
                  onInspectSource={src => void openSourcePreview(src)}
                  onPreviewMaterial={(id, name) => void openMaterialPreview(id, name)}
                  onDownloadMaterial={(id, name) => void handleDownloadMaterial(id, name)}
                  onDownloadArtifact={(runId, artId, name) => void handleDownloadArtifact(runId, artId, name)}
                />
              ))
            )}
          </section>
          {nextCursor && (
            <div className="admin-load-more">
              <button type="button" className="admin-page-button" disabled={loadingMore} onClick={() => void loadMoreRuns()}>
                {loadingMore ? <LoaderCircle className="admin-spin" size={15} /> : null}
                {loadingMore ? "加载中…" : "加载更多记录"}
              </button>
            </div>
          )}
        </>
      )}

      <Dialog open={Boolean(sourcePreview)} onOpenChange={open => { if (!open) setSourcePreview(null); }}>
        <DialogContent className="admin-dialog admin-source-dialog">
          <DialogHeader>
            <DialogTitle>
              {sourcePreview ? `${sourceKindLabel(sourcePreview.source.kind ?? null)} · ${sourcePreview.source.title}` : "来源原文"}
            </DialogTitle>
            <DialogDescription>
              {[
                sourcePreview?.source.locator,
                sourcePreview?.source.section,
                sourcePreview?.source.version_date,
                sourcePreview?.source.version_label,
                sourcePreview?.source.authors,
              ]
                .filter(Boolean)
                .join(" · ") || "用户检索到的公开来源详情"}
            </DialogDescription>
          </DialogHeader>
          <div className="admin-source-body">
            {sourcePreview?.loading ? (
              <div className="admin-loading" style={{ minHeight: 160 }}>
                <LoaderCircle className="admin-spin" size={18} />
                正在读取资料全文…
              </div>
            ) : sourcePreview?.doc ? (
              <>
                <div className="admin-source-prose">
                  <Markdown content={sourcePreview.doc.text} />
                </div>
                {sourcePreview.doc.source_url && (
                  <a
                    href={sourcePreview.doc.source_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="admin-cc-inline-link"
                  >
                    查看原始链接
                    <ExternalLink size={13} />
                  </a>
                )}
              </>
            ) : (
              <p className="admin-cc-hint">{sourcePreview?.error}</p>
            )}
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(materialPreview)} onOpenChange={open => { if (!open) setMaterialPreview(null); }}>
        <DialogContent className="admin-dialog admin-source-dialog">
          <DialogHeader>
            <DialogTitle>{materialPreview ? `用户附件 · ${materialPreview.filename}` : "用户附件"}</DialogTitle>
            <DialogDescription>
              {materialPreview?.data
                ? `解析状态：${materialPreview.data.parse_status} · 已显示 ${materialPreview.data.chunks.length} 段`
                : "查看已解析的正文片段或下载原始附件"}
            </DialogDescription>
          </DialogHeader>
          <div className="admin-source-body">
            {materialPreview && (
              <div className="admin-row-actions" style={{ justifyContent: "flex-start" }}>
                <button
                  type="button"
                  className="admin-row-action"
                  disabled={downloadingKey !== null}
                  onClick={() => void handleDownloadMaterial(materialPreview.materialId, materialPreview.filename)}
                >
                  {downloadingKey === `mat:${materialPreview.materialId}` ? <LoaderCircle className="admin-spin" size={14} /> : <Download size={14} />}
                  下载原文件
                </button>
              </div>
            )}
            {materialPreview?.loading ? (
              <div className="admin-loading" style={{ minHeight: 160 }}>
                <LoaderCircle className="admin-spin" size={18} />
                正在读取附件内容…
              </div>
            ) : materialPreview?.data ? (
              <>
                {materialPreview.data.notices.length > 0 && (
                  <p className="admin-cc-hint">{materialPreview.data.notices.join(" ")}</p>
                )}
                {materialPreview.data.chunks.length === 0 ? (
                  <p className="admin-cc-hint">这份附件暂未提取出文本内容，请直接点击上方「下载原文件」。</p>
                ) : (
                  <ul className="admin-passage-list">
                    {materialPreview.data.chunks.map(chunk => (
                      <li key={chunk.id} className="admin-passage-item">
                        <div className="admin-passage-meta">
                          <span>#{chunk.ordinal}</span>
                          {chunk.locator && <span>{chunk.locator}</span>}
                          {chunk.heading_path && chunk.heading_path.length > 0 && <span>{chunk.heading_path.join(" / ")}</span>}
                        </div>
                        <pre className="admin-passage-text">{chunk.text}</pre>
                      </li>
                    ))}
                  </ul>
                )}
                {materialPreview.data.next_cursor && (
                  <div className="admin-load-more">
                    <button
                      type="button"
                      className="admin-page-button"
                      disabled={materialPreview.loadingMore}
                      onClick={() => void loadMoreMaterialPassages()}
                    >
                      {materialPreview.loadingMore && <LoaderCircle className="admin-spin" size={14} />}
                      {materialPreview.loadingMore ? "加载中…" : "加载更多片段"}
                    </button>
                  </div>
                )}
              </>
            ) : (
              <p className="admin-cc-hint">{materialPreview?.error}</p>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function RunCard({
  run,
  downloadingKey,
  onInspectSource,
  onPreviewMaterial,
  onDownloadMaterial,
  onDownloadArtifact,
}: {
  run: AdminConversationRun;
  downloadingKey: string | null;
  onInspectSource: (source: AdminConversationSource) => void;
  onPreviewMaterial: (materialId: string, filename: string) => void;
  onDownloadMaterial: (materialId: string, filename: string) => void;
  onDownloadArtifact: (runId: string, artifactId: string, filename: string) => void;
}) {
  const [sourceFilter, setSourceFilter] = useState<"all" | "law" | "case" | "paper">("all");
  const [showAllSources, setShowAllSources] = useState(false);

  const statusClass = run.status === "succeeded" ? "is-success" : run.status === "failed" ? "is-error" : "";
  const materials = run.materials ?? [];
  const toolTraces = run.tool_traces ?? [];
  const guidance = run.continuation_guidance ?? [];
  const sources = run.sources ?? [];
  const deliverables = run.artifacts.filter(item => item.kind !== "preview");

  const counts = {
    total: sources.length,
    law: sources.filter(s => s.kind === "law").length,
    case: sources.filter(s => s.kind === "case").length,
    paper: sources.filter(s => s.kind === "paper").length,
  };
  const filteredSources =
    sourceFilter === "all" ? sources : sources.filter(s => s.kind === sourceFilter);
  const visibleSources = showAllSources ? filteredSources : filteredSources.slice(0, 8);
  const stageText = unitLabel(run.current_unit);

  return (
    <article className={`admin-run-card ${statusClass}`}>
      <header className="admin-run-card-header">
        <div>
          <span className="admin-run-status">
            {run.status === "succeeded" ? (
              <CircleCheck size={15} />
            ) : run.status === "failed" ? (
              <CircleX size={15} />
            ) : (
              <Clock3 size={15} />
            )}
            {statusLabel(run.status)}
          </span>
          <time dateTime={run.created_at}>{formatAdminDate(run.created_at)}</time>
          {stageText && <span className="admin-cc-badge">{stageText}</span>}
          {(run.context_compaction_count ?? 0) > 0 && (
            <span className="admin-cc-badge">上下文压缩 {run.context_compaction_count} 次</span>
          )}
        </div>
        <div className="admin-run-timing">
          {run.first_delta_ms != null && <span>首字 {formatAdminDuration(run.first_delta_ms)}</span>}
          <span>总耗时 {formatAdminDuration(run.elapsed_ms)}</span>
        </div>
      </header>

      <div className="admin-run-message">
        <span className="admin-run-label">
          <MessageSquare size={14} />
          用户提问
        </span>
        <div className="admin-run-question-wrap">
          <p>{run.user_question}</p>
          {materials.length > 0 && (
            <div className="admin-run-materials" aria-label="附带材料">
              {materials.map(mat => (
                <span key={mat.material_id} className="admin-cc-material-pill">
                  <Paperclip size={12} />
                  <span>{mat.filename}</span>
                  <small>{formatBytes(mat.size_bytes)}</small>
                  <button
                    type="button"
                    className="admin-pill-btn"
                    onClick={() => onPreviewMaterial(mat.material_id, mat.filename)}
                  >
                    <Eye size={12} />
                    查看
                  </button>
                  <button
                    type="button"
                    className="admin-pill-btn"
                    disabled={downloadingKey !== null}
                    onClick={() => onDownloadMaterial(mat.material_id, mat.filename)}
                  >
                    {downloadingKey === `mat:${mat.material_id}` ? <LoaderCircle className="admin-spin" size={12} /> : <Download size={12} />}
                    下载
                  </button>
                </span>
              ))}
            </div>
          )}
          {guidance.length > 0 && (
            <div className="admin-run-guidance">
              <strong>后续补充要求：</strong>
              {guidance.join("；")}
            </div>
          )}
        </div>
      </div>

      {(run.error_message || run.error_code) && (
        <div className="admin-run-message">
          <span className="admin-run-label">
            <AlertCircle size={14} />
            状态说明
          </span>
          <div className="admin-run-error-box">
            {run.error_message || run.error_code}
            {run.error_message && run.error_code && <code> ({run.error_code})</code>}
          </div>
        </div>
      )}

      {toolTraces.length > 0 && (
        <div className="admin-run-message">
          <span className="admin-run-label">
            <Wrench size={14} />
            检索过程
          </span>
          <div className="admin-trace-grid">
            {toolTraces.map((trace, idx) => (
              <div key={`${trace.tool_name}-${idx}`} className={`admin-trace-item is-${trace.status}`}>
                <div className="admin-trace-top">
                  <span><Search size={12} />{trace.tool_label}</span>
                  <b>
                    {trace.status === "succeeded" ? <CheckCircle2 size={12} /> : <CircleX size={12} />}
                    {trace.summary || (trace.status === "succeeded" ? "完成" : "失败")}
                  </b>
                </div>
                {trace.query && <p className="admin-trace-query">检索条件：{trace.query}</p>}
              </div>
            ))}
          </div>
        </div>
      )}

      {sources.length > 0 && (
        <div className="admin-run-message admin-run-evidence">
          <span className="admin-run-label">
            <BookOpen size={14} />
            查得依据 ({counts.total})
          </span>
          <div className="admin-source-panel">
            <div className="admin-source-filter-bar">
              <button
                type="button"
                className={sourceFilter === "all" ? "is-active" : ""}
                onClick={() => setSourceFilter("all")}
              >
                全部 ({counts.total})
              </button>
              {counts.law > 0 && (
                <button
                  type="button"
                  className={sourceFilter === "law" ? "is-active" : ""}
                  onClick={() => setSourceFilter("law")}
                >
                  法规 ({counts.law})
                </button>
              )}
              {counts.case > 0 && (
                <button
                  type="button"
                  className={sourceFilter === "case" ? "is-active" : ""}
                  onClick={() => setSourceFilter("case")}
                >
                  案例 ({counts.case})
                </button>
              )}
              {counts.paper > 0 && (
                <button
                  type="button"
                  className={sourceFilter === "paper" ? "is-active" : ""}
                  onClick={() => setSourceFilter("paper")}
                >
                  论文 ({counts.paper})
                </button>
              )}
            </div>

            <ul className="admin-source-cards">
              {visibleSources.map(source => {
                const kind = source.kind ?? null;
                return (
                  <li key={source.source_id} className="admin-source-card">
                    <div className="admin-source-card-main">
                      <div className="admin-source-card-title">
                        <span className={`admin-source-kind is-${kind || "other"}`}>{sourceKindLabel(kind)}</span>
                        <strong>{source.title}</strong>
                      </div>
                      <p className="admin-source-card-meta">
                        {[source.locator, source.section, source.authors, source.collection, source.version_date, source.version_label]
                          .filter(Boolean)
                          .join(" · ") || source.source_id}
                      </p>
                    </div>
                    <button
                      type="button"
                      className="admin-cc-btn"
                      onClick={() => onInspectSource(source)}
                    >
                      查看正文
                    </button>
                  </li>
                );
              })}
            </ul>

            {filteredSources.length > 8 && (
              <button
                type="button"
                className="admin-run-expand"
                onClick={() => setShowAllSources(v => !v)}
              >
                {showAllSources ? "收起部分来源" : `展开其余 ${filteredSources.length - 8} 条查得来源`}
              </button>
            )}
          </div>
        </div>
      )}

      {run.public_answer && (
        <div className="admin-run-message admin-run-answer">
          <span className="admin-run-label">
            <CircleCheck size={14} />
            公开回答
          </span>
          <AdminAnswerText text={run.public_answer} />
        </div>
      )}

      {deliverables.length > 0 && (
        <div className="admin-run-message admin-run-evidence">
          <span className="admin-run-label">
            <FileText size={14} />
            生成交付
          </span>
          <ul className="admin-run-artifact-list">
            {deliverables.map(artifact => (
              <li key={artifact.artifact_id} className="admin-run-artifact-row">
                <div>
                  <strong>{artifact.name}</strong>
                  <small>{artifact.media_type} · {formatBytes(artifact.size_bytes)}</small>
                </div>
                <button
                  type="button"
                  className="admin-row-action"
                  disabled={downloadingKey !== null}
                  onClick={() => onDownloadArtifact(run.run_id, artifact.artifact_id, artifact.name)}
                >
                  {downloadingKey === `art:${artifact.artifact_id}` ? <LoaderCircle className="admin-spin" size={13} /> : <Download size={13} />}
                  {downloadingKey === `art:${artifact.artifact_id}` ? "下载中…" : "下载"}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <dl className="admin-run-facts">
        {run.provider && <div><dt>服务商</dt><dd>{run.provider}</dd></div>}
        {run.model && <div><dt><Cpu size={13} />模型</dt><dd>{run.model}</dd></div>}
        {run.protocol && <div><dt>协议</dt><dd>{run.protocol}</dd></div>}
        {run.thinking_mode && <div><dt>思考</dt><dd>{run.thinking_mode}</dd></div>}
      </dl>

      {run.nodes.length > 0 && (
        <ol className="admin-run-nodes">
          {run.nodes.map(node => (
            <li key={node.id}>
              <span>{node.label}</span>
              <small>{statusLabel(node.status)}{node.elapsed_ms == null ? "" : ` · ${formatAdminDuration(node.elapsed_ms)}`}</small>
            </li>
          ))}
        </ol>
      )}
    </article>
  );
}

const LONG_ANSWER_LIMIT = 1200;

function AdminAnswerText({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(true);
  const isLong = text.length > LONG_ANSWER_LIMIT;
  return (
    <div className={`admin-run-answer-copy ${isLong && !expanded ? "is-collapsed" : ""}`} aria-label="公开回答正文">
      <div className="admin-run-answer-text admin-run-answer-prose">
        <Markdown content={text} />
      </div>
      {isLong && (
        <button
          type="button"
          className="admin-run-expand"
          aria-expanded={expanded}
          onClick={() => setExpanded(value => !value)}
        >
          {expanded ? "收起回答" : "展开完整回答"}
        </button>
      )}
    </div>
  );
}

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}
