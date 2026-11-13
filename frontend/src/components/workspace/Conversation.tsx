import { ThinkingModeSelect } from "./ThinkingModeSelect";
import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  ArrowDown,
  ArrowUp,
  Paperclip,
  Library,
  FileText,
  X,
  Square,
  Copy,
  Check,
  ArrowUpRight,
  BriefcaseBusiness,
  Scale,
  ScanText,
  BookOpen,
  MoreHorizontal,
  ExternalLink,
  LoaderCircle,
} from "lucide-react";
import type { useWorkspace } from "@/hooks/use-workspace";
import type { Artifact, Message, SourceDocument, SourceReference } from "@/lib/workbench-api";
import { mergeRunMessages } from "@/lib/workbench-api";
import { useAuthSession } from "@/lib/auth-session";
import { maritimeApi, type MaritimeCase } from "@/lib/maritime-api";
import { IconButton, Menu, MenuItem, Loading, LoadError, Modal } from "./Primitives";

import { RunProgress } from "./RunProgress";
import { ActivityIndicator } from "./ActivityIndicator";
import { MaterialCards, MaterialDescription } from "./MaterialCards";
import { WorkPackages } from "./WorkPackages";
import { useStreamingText } from "@/hooks/use-streaming-text";

type Workspace = ReturnType<typeof useWorkspace>;

const tasks = [
  {
    label: "法律研究",
    prompt:
      "我想研究一个海事法律问题：\n\n请结合适用法、法律依据与适用条件展开，并说明仍需核实的信息。",
  },
  {
    label: "类案检索",
    prompt:
      "我想检索与以下争议相关的案例：\n\n请区分已核实来源与待核实线索，并比较关键事实与裁判理由。",
  },
  {
    label: "合同审阅",
    prompt:
      "请审阅我将提供的合同材料，关注权利义务、责任边界与争议解决条款。请区分原文约定、风险分析与修改建议。",
  },
  {
    label: "风险研判",
    prompt:
      "请根据以下事实和材料分析争议风险：\n\n请说明判断依据、关键假设、相反观点与信息缺口。",
  },
  {
    label: "证据梳理",
    prompt:
      "请梳理我将提供的材料，按时间线、待证事实与对应证据整理，并标出矛盾与缺口。",
  },
  {
    label: "索赔测算",
    prompt:
      "请协助梳理以下索赔的计算依据与项目：\n\n请列出公式、已知条件与缺失数据，不要推定尚未提供的数值。",
  },
  {
    label: "文书起草",
    prompt:
      "请协助起草一份海事争议相关文书：\n文书类型：\n用途与对象：\n事实与诉求：\n\n请将尚未确认的信息保留为待补充项。",
  },
  {
    label: "案件管理",
    prompt:
      "请根据以下案件信息梳理事项：\n\n请整理事实脉络、程序进展与待处理问题，不把待确认的日期或安排当作既定事实。",
  },
];

export function Markdown({ content }: { content: string }) {
  return (
    <div className="ws-prose">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ children, href }) => (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {children}
              <ArrowUpRight size={13} aria-hidden="true" />
            </a>
          ),
          table: ({ children }) => (
            <div
              className="ws-table-scroll"
              tabIndex={0}
              role="region"
              aria-label="回答中的表格"
            >
              <table>{children}</table>
            </div>
          ),
          img: ({ alt }) => (
            <span className="ws-muted">
              [图片：{alt || "未加载的外部图片"}]
            </span>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

function StreamingAnswer({ text, live, onSettled }: { text: string; live: boolean; onSettled?: () => void }) {
  const visible = useStreamingText(text, live);
  useEffect(() => { if (!live && visible === text) onSettled?.(); }, [live, visible, text, onSettled]);
  return <div data-testid="stream-answer"><Markdown content={visible} /></div>;
}

function sourceVersionMeta(source: SourceReference): string[] {
  const year = source.version_date?.match(/^\d{4}/)?.[0];
  const status = source.version_label === "现行版本" || source.version_label === "现行有效"
    ? "现行有效"
    : source.version_label === "历史版本" || source.version_label === "旧版"
      ? "旧版"
      : null;
  return [year ? `${year}年` : null, status].filter((value): value is string => Boolean(value));
}

function SourceCards({ sources, onOpen }: { sources?: SourceReference[]; onOpen: (source: SourceReference) => void }) {
  if (!sources?.length) return null;
  const unique = [...new Map(sources.map((source) => [source.source_id, source])).values()];
  return (
    <section className="ws-source-section" aria-label="回答依据">
      <div className="ws-source-heading">
        <span>回答依据</span>
        <small>相关法规与案例资料</small>
      </div>
      <div className="ws-source-list">
        {unique.map((source) => (
          <button
            type="button"
            className="ws-source-card"
            key={source.source_id}
            onClick={() => onOpen(source)}
          >
            <span className={`ws-source-kind ${source.kind === "case" ? "is-case" : "is-law"}`}>
              {source.kind === "case" ? "案例" : "法规"}
            </span>
            <span className="ws-source-copy">
              <span className="ws-source-title">
                <strong>{source.title}</strong>
                {sourceVersionMeta(source).map((item) => <span className="ws-source-version" key={item}>{item}</span>)}
              </span>
              <small>
                {[source.locator, source.section]
                  .filter(Boolean)
                  .join(" · ") || "已收录来源"}
              </small>
            </span>
            <span className="ws-source-action">
              查看原文
              <ExternalLink size={14} aria-hidden="true" />
            </span>
          </button>
        ))}
      </div>
    </section>
  );
}

function ArtifactCards({ artifacts, onDownload }: { artifacts?: Artifact[]; onDownload: (artifact: Artifact) => void }) {
  if (!artifacts?.length) return null;
  const visible = artifacts.filter((artifact) => artifact.kind !== "preview");
  if (!visible.length) return null;
  const previewFor = (artifact: Artifact) => artifacts.find((candidate) =>
    candidate.kind === "preview" && (
      candidate.artifact_id === artifact.preview_artifact_id ||
      candidate.preview_of_artifact_id === artifact.artifact_id
    )
  );
  return (
    <section className="ws-artifact-section" aria-label="工作产物">
      <div className="ws-source-heading"><span>工作产物</span><small>可编辑文件与预览</small></div>
      <div className="ws-artifact-list">
        {visible.map((artifact) => {
          const preview = previewFor(artifact);
          return (
            <div className="ws-artifact-card" key={artifact.artifact_id}>
              <FileText size={18} aria-hidden="true" />
              <span className="ws-artifact-copy"><strong>{artifact.name}</strong><small>版本 {artifact.version} · {Math.max(1, Math.round(artifact.size_bytes / 1024))} KB</small></span>
              <span className="ws-artifact-actions">
                {preview && (
                  <button type="button" className="ws-text-button" onClick={() => onDownload(preview)}>
                    下载 PDF <ArrowUpRight size={14} aria-hidden="true" />
                  </button>
                )}
                <button type="button" className="ws-text-button" onClick={() => onDownload(artifact)}>
                  下载 <ArrowUpRight size={14} aria-hidden="true" />
                </button>
              </span>
            </div>
          );
        })}
      </div>
    </section>
  );
}

const MARITIME_SOURCE_URL = /(?:\/api\/)?maritime\/sources\/(?:laws|cases)\/[^\s<>\x29\x5d`"”，。；]+(?:\?[^\s<>\x29\x5d`"”，。；]*)?/;
const MARITIME_SOURCE_URL_GLOBAL = new RegExp(MARITIME_SOURCE_URL.source, "g");
const MARITIME_SOURCE_MARKDOWN = new RegExp(`\\[[^\\]]*\\]\\(${MARITIME_SOURCE_URL.source}\\)`, "g");

function stripMaritimeSourceMarkup(content: string) {
  return content
    .split(/\r?\n/)
    .filter((line) => !/^\s*(?:[-*+]\s+|\d+[.)]\s+)/.test(line) || !MARITIME_SOURCE_URL.test(line))
    .map((line) => line.replace(MARITIME_SOURCE_MARKDOWN, "").replace(MARITIME_SOURCE_URL_GLOBAL, "").trimEnd())
    .filter((line, index, lines) => line.trim() || (index > 0 && lines[index - 1].trim()))
    .join("\n")
    .replace(/\n{3,}/g, "\n\n");
}

function SaveAnswer({ message, onNotice }: { message: Message; onNotice: (text: string) => void }) {
  const session = useAuthSession();
  const location = useLocation();
  const [open, setOpen] = useState(false);
  const [cases, setCases] = useState<MaritimeCase[]>([]);
  const [caseId, setCaseId] = useState("");
  const [title, setTitle] = useState("海事研究草稿");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function show() {
    if (!session) return;
    setOpen(true);
    setBusy(true);
    setError("");
    try {
      const value = await maritimeApi(session.session.token).cases();
      setCases(value.cases);
      const preferred = new URLSearchParams(location.search).get("case");
      setCaseId(value.cases.find((item) => item.id === preferred)?.id || value.cases[0]?.id || "");
    } catch (e) {
      setError(e instanceof Error ? e.message : "案件未能载入");
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (!session || !caseId || !title.trim()) return;
    setBusy(true);
    setError("");
    try {
      await maritimeApi(session.session.token).saveEntry(caseId, {
        kind: "document",
        title: title.trim(),
        content: stripMaritimeSourceMarkup(message.content),
        status: "draft",
      });
      setOpen(false);
      onNotice("已保存为案件文书草稿，待专业复核。");
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存未完成");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button className="ws-text-button" onClick={() => void show()}>
        <FileText size={15} />
        存入案件
      </button>
      <Modal
        open={open}
        onOpenChange={(value) => { if (!busy) setOpen(value); }}
        title="存入案件"
        description="只保存为草稿，不自动确认专业复核或发送。"
      >
        {error && <p role="alert" className="md-form-error">{error}</p>}
        {busy ? <Loading label="处理中…" /> : !cases.length ? (
          <p>请先在<Link to="/workspace/cases">案件协作</Link>建立案件，再保存这份草稿。</p>
        ) : (
          <form className="md-form" onSubmit={(event) => { event.preventDefault(); void save(); }}>
            <label>
              目标案件
              <select aria-label="目标案件" value={caseId} onChange={(event) => setCaseId(event.target.value)}>
                {cases.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
              </select>
            </label>
            <label>
              文书标题
              <input required maxLength={160} value={title} onChange={(event) => setTitle(event.target.value)} />
            </label>
            <button className="ws-primary-button" disabled={busy || !caseId || !title.trim()}>保存草稿</button>
          </form>
        )}
      </Modal>
    </>
  );
}

function SourceViewer({
  reference,
  document,
  loading,
  error,
  onClose,
}: {
  reference: SourceReference;
  document: SourceDocument | null;
  loading: boolean;
  error: string | null;
  onClose: () => void;
}) {
  const title = document?.title || reference.title;
  const locator = document?.locator || reference.locator;
  return (
    <>
      <div className="ws-overlay" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }} />
      <section className="ws-dialog ws-source-dialog" role="dialog" aria-modal="true" aria-labelledby="source-viewer-title">
        <div className="ws-dialog-heading">
          <div>
            <span className="ws-source-dialog-kind">{reference.kind === "case" ? "案例原文" : "法规原文"}</span>
            <h2 id="source-viewer-title">{title}</h2>
          </div>
          <IconButton label="关闭原文" onClick={onClose}><X size={18} /></IconButton>
        </div>
        <div className="ws-source-dialog-meta">
          {[locator, document?.section || reference.section, ...sourceVersionMeta(document || reference)]
            .filter(Boolean)
            .map((item) => <span key={item}>{item}</span>)}
        </div>
        {loading ? (
          <div className="ws-source-loading" role="status"><LoaderCircle size={17} className="ws-source-spinner" />正在读取原文…</div>
        ) : error ? (
          <div className="ws-source-error" role="alert">{error}</div>
        ) : document ? (
          <>
            <pre className="ws-source-text">{document.text}</pre>
            {document.source_url && (
              <a className="ws-source-publisher-link" href={document.source_url} target="_blank" rel="noopener noreferrer">
                打开发布页 <ExternalLink size={14} aria-hidden="true" />
              </a>
            )}
          </>
        ) : null}
      </section>
    </>
  );
}

function Answer({
  message,
  onNotice,
  onContinue,
  onRetry,
  onOpenSource,
  onDownloadArtifact,
}: {
  message: Message;
  onNotice: (message: string) => void;
  onContinue?: () => void;
  onRetry?: () => void;
  onOpenSource: (source: SourceReference) => void;
  onDownloadArtifact: (artifact: Artifact) => void;
}) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  useEffect(() => () => clearTimeout(timer.current), []);
  async function copy() {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 1800);
    } catch {
      onNotice("未能复制，请选择需要的文字后手动复制。");
    }
  }
  return (
    <article className="ws-answer">
      <div className="ws-answer-byline">
        <span className="ws-answer-mark" aria-hidden="true">
          Z.
        </span>
        <span>舟序</span>
      </div>
      <RunProgress activities={message.activity} />
      <Markdown content={message.content} />
      <SourceCards sources={message.sources} onOpen={onOpenSource} />
      <ArtifactCards artifacts={message.artifacts} onDownload={onDownloadArtifact} />
      <WorkPackages
        runId={message.run_id || undefined}
        renderText={(text) => <Markdown content={text} />}
      />
      {message.status === "stopped" && (
        <div className="ws-run-status ws-run-status-stopped" role="status">
          已停止
        </div>
      )}
      {message.status === "interrupted" && (
        <div className="ws-run-status" role="status">
          {message.recovery?.message || "已打断，已保留已生成内容。"}
        </div>
      )}
      {message.status === "failed" && (
        <div className="ws-run-status" role="status">
          {message.recovery?.message || "处理未完成，已保留已生成内容。"}
        </div>
      )}
      <div className="ws-answer-tools">
        {(message.recovery?.can_continue || message.recovery?.can_retry) && (
          <div className="ws-answer-action-group" aria-label="恢复操作">
            {message.recovery?.can_continue && <button className="ws-text-button" onClick={onContinue}>继续处理</button>}
            {message.recovery?.can_retry && <button className="ws-text-button" onClick={onRetry}>重新做</button>}
          </div>
        )}
        <div className="ws-answer-action-group ws-answer-action-group-secondary" aria-label="回答操作">
          <button className="ws-text-button" onClick={() => void copy()}>
            {copied ? <Check size={15} /> : <Copy size={15} />}
            {copied ? "已复制" : "复制回答"}
          </button>
          <SaveAnswer message={message} onNotice={onNotice} />
        </div>
      </div>
    </article>
  );
}

export default function Conversation({
  ws,
  id,
  openMaterials,
}: {
  ws: Workspace;
  id?: string;
  openMaterials: () => void;
}) {
  const reduced = useReducedMotion();
  const reader = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const atEnd = useRef(true);
  const previousId = useRef(id);
  const [showLatest, setShowLatest] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [sourceReference, setSourceReference] = useState<SourceReference | null>(null);
  const [sourceDocument, setSourceDocument] = useState<SourceDocument | null>(null);
  const [sourceLoading, setSourceLoading] = useState(false);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const dragDepth = useRef(0);
  const detail = ws.detail.data;
  const turn = ws.turn?.conversationId === id ? ws.turn : null;
  const receiving = turn?.status === "running";
  const empty = !detail?.messages.length && !turn;
  const materials = ws.draftMaterials;
  const uploads = ws.uploads.filter((u) => u.conversationId === id);
  const uploading = uploads.some((u) => !u.error);
  const scrollLatest = useCallback(
    (smooth = false) => {
      const el = reader.current;
      if (el)
        el.scrollTo({
          top: el.scrollHeight,
          behavior: smooth && !reduced ? "smooth" : "instant",
        });
      atEnd.current = true;
      setShowLatest(false);
    },
    [reduced],
  );
  useEffect(() => {
    if (previousId.current !== id) {
      atEnd.current = true;
      previousId.current = id;
    }
    if (atEnd.current) {
      const frame = requestAnimationFrame(() => {
        if (atEnd.current) scrollLatest();
      });
      return () => cancelAnimationFrame(frame);
    }
  }, [
    id,
    detail?.messages.length,
    turn?.answer,
    turn?.activities.length,
    scrollLatest,
  ]); // Never pull a reader away from older messages.
  useEffect(() => {
    const el = input.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = `${Math.min(el.scrollHeight, 168)}px`;
    }
  }, [ws.draft, id]);
  useEffect(() => {
    if (!reader.current || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      if (atEnd.current) scrollLatest();
    });
    const content = reader.current.firstElementChild;
    if (content) observer.observe(content);
    return () => observer.disconnect();
  }, [empty, id, ws.detail.isPending, scrollLatest]);
  const chooseTask = (index: number) => {
    ws.setDraft(tasks[index].prompt);
    requestAnimationFrame(() => input.current?.focus());
  };
  const openSource = useCallback(async (reference: SourceReference) => {
    setSourceReference(reference);
    setSourceDocument(null);
    setSourceError(null);
    setSourceLoading(true);
    try {
      setSourceDocument(await ws.api.source(reference.source_id));
    } catch {
      setSourceError("原文暂时无法打开，请稍后再试。");
    } finally {
      setSourceLoading(false);
    }
  }, [ws.api]);
  const downloadArtifact = useCallback((runId: string | null | undefined, artifact: Artifact) => {
    if (!runId) return;
    void ws.downloadArtifact(runId, artifact.artifact_id, artifact.name).catch(() => ws.setNotice("文件暂时无法下载，请稍后再试。"));
  }, [ws]);

  if (id && ws.detail.isPending && !turn)
    return (
      <div className="ws-center-state">
        <Loading label="正在打开对话…" />
      </div>
    );
  if (id && ws.detail.isError)
    return (
      <div className="ws-center-state">
        <LoadError
          label={ws.detail.error.message}
          retry={() => void ws.detail.refetch()}
        />
      </div>
    );

  return (
    <div
      className={`ws-conversation ${empty ? "is-empty" : ""} ${ws.conversationMode === "work" ? "is-work" : ""} ${dragOver ? "is-dragging" : ""}`}
      onDragEnter={(e) => {
        if (e.dataTransfer.types.includes("Files")) {
          e.preventDefault();
          dragDepth.current++;
          setDragOver(true);
        }
      }}
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes("Files")) e.preventDefault();
      }}
      onDragLeave={(e) => {
        e.preventDefault();
        dragDepth.current--;
        if (dragDepth.current <= 0) setDragOver(false);
      }}
      onDrop={(e) => {
        e.preventDefault();
        dragDepth.current = 0;
        setDragOver(false);
        void ws.uploadFiles(Array.from(e.dataTransfer.files));
      }}
    >
      <AnimatePresence>
        {dragOver && (
          <motion.div
            className="ws-drop-target"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            <Paperclip size={28} />
            <span>
              松开，添加到当前对话
            </span>
          </motion.div>
        )}
      </AnimatePresence>
      <div
        className="ws-reader"
        ref={reader}
        tabIndex={0}
        aria-label="对话内容"
        onScroll={() => {
          const el = reader.current;
          if (el) {
            atEnd.current =
              el.scrollHeight - el.scrollTop - el.clientHeight < 90;
            setShowLatest(!atEnd.current);
          }
        }}
      >
        <div className="ws-message-column">
          <AnimatePresence mode="popLayout">
          {empty ? (
            <motion.div
              key="welcome"
              className="ws-welcome"
              initial={{
                opacity: 0,
                transform: reduced ? "none" : "translateY(8px)",
              }}
              animate={{ opacity: 1, transform: "none" }}
              exit={{ opacity: 0, filter: reduced ? "none" : "blur(3px)", transition: { duration: 0.12 } }}
              transition={{ duration: 0.28 }}
            >
              <h1>{ws.conversationMode === "work" ? "今天要完成哪件工作？" : "今天，先理清哪件事？"}</h1>
              <p>{ws.conversationMode === "work" ? <><span>描述要交付的结果，或添加</span><wbr /><span>合同、提单与往来材料。</span></> : <><span>描述问题，或添加</span><wbr /><span>合同、提单与往来材料。</span></>}</p>
            </motion.div>
          ) : (
            <motion.div key="thread" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: .18 }}>
              {mergeRunMessages(detail?.messages || [], [...ws.visibleRuns.map(r => r.runId), ...ws.suppressedRunIds]).map((message) =>
                message.role === "user" ? (
                  <article className="ws-question" key={message.id}>
                    <div>{message.content}</div>
                    <MaterialCards materials={message.materials} />
                    {message.updates?.map((text, index) => <div className="ws-user-update" key={index}><small>补充要求</small><p>{text}</p></div>)}
                  </article>
                ) : (
                  <Answer
                    key={message.id}
                    message={message}
                    onNotice={ws.setNotice}
                    onContinue={() => void ws.continueRun(message.run_id || undefined)}
                    onRetry={() => void ws.retryTurn(message.run_id || undefined)}
                    onOpenSource={(source) => void openSource(source)}
                    onDownloadArtifact={(artifact) => downloadArtifact(message.run_id, artifact)}
                  />
                ),
              )}
              {ws.visibleRuns.filter(card => card.status !== "queued").map((card) => (
                <Fragment key={card.runId}>
                  <article className="ws-question">
                    <div>{card.question}</div>
                    <MaterialCards materials={card.materials} />
                    {card.updates?.map((text, index) => <div className="ws-user-update" key={index}><small>补充要求</small><p>{text}</p></div>)}
                  </article>
                  <article
                    className="ws-answer"
                    aria-busy={card.state === "receiving"}
                  >
                    <div className="ws-answer-byline">
                      <span className="ws-answer-mark" aria-hidden="true">
                        Z.
                      </span>
                      <span>舟序</span>
                      <span className="ws-stream-state">
                        {card.status === "running" ? "正在回答" : card.status === "waiting_user" ? "等待补充" : card.status === "completed" ? "已完成" : card.status === "failed" ? "运行失败" : card.status === "stopped" ? "已停止" : card.status === "interrupted" ? "已打断" : "未确认保存"}
                      </span>
                    </div>
                    <RunProgress
                      activities={card.activities}
                      live={card.state === "receiving"}
                    />
                    <StreamingAnswer key={card.runId} text={card.answer} live={card.state === "receiving"} onSettled={card.status === "completed" ? () => void ws.finishPresentation(card.runId!) : undefined} />
                    <SourceCards sources={card.sources} onOpen={(source) => void openSource(source)} />
                    <ArtifactCards artifacts={card.artifacts} onDownload={(artifact) => downloadArtifact(card.runId, artifact)} />
                    {card.state === "waiting" && card.inputRequest && (
                      <div className="ws-run-status ws-run-status-waiting" role="status">
                        <p>{card.inputRequest.question}</p>
                        <p className="ws-muted">请在下方输入补充信息后发送。</p>
                        <button className="ws-text-button" onClick={() => void ws.retryTurn(card.runId)}>重新处理本任务</button>
                      </div>
                    )}
                    {(card.state === "interrupted" || card.state === "failed") && (
                      <div className="ws-run-status" role="status">
                        <p>{card.recovery.message || card.error || "处理未完成，已有内容已保留。"}</p>
                        <div className="ws-run-status-actions">
                          {card.recovery.can_continue && <button className="ws-text-button" onClick={() => void ws.continueRun(card.runId)}>继续处理</button>}
                          {card.recovery.can_retry && <button className="ws-text-button" onClick={() => void ws.retryTurn(card.runId)}>重新做</button>}
                        </div>
                      </div>
                    )}
                    {card.state === "stopped" && (
                      <div className="ws-run-status ws-run-status-stopped" role="status">
                        已停止
                      </div>
                    )}
                  </article>
                </Fragment>
              ))}

            </motion.div>
          )}
          </AnimatePresence>
          <div className="ws-reader-end" />
        </div>
      </div>
      <div className="ws-composer-zone">
        <AnimatePresence>
          {!empty && showLatest && (
            <motion.button
              className="ws-latest"
              onClick={() => scrollLatest(true)}
              initial={{ opacity: 0, transform: "translateY(6px)" }}
              animate={{ opacity: 1, transform: "translateY(0)" }}
              exit={{ opacity: 0 }}
            >
              <ArrowDown size={15} />
              回到最新
            </motion.button>
          )}
        </AnimatePresence>
        <motion.div
          layout="position"
          transition={{ type: "spring", stiffness: 400, damping: 40 }}
          className="ws-composer-wrap"
        >
          <AnimatePresence initial={false} mode="popLayout">
            {ws.queuedRuns.map((card) => (
              <motion.div
                key={card.runId}
                layout
                className="ws-queued-message"
                initial={reduced ? { opacity: 0 } : { opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={reduced ? { opacity: 0 } : { opacity: 0, y: -4 }}
                transition={{ duration: 0.18, ease: [0.23, 1, 0.32, 1] }}
              >
                <span className="ws-queued-mark" aria-hidden="true" />
                <div className="ws-queued-copy">
                  <span className="ws-queued-label">排队中</span>
                  <span className="ws-queued-text" title={card.question}>{card.question}</span>
                  {card.materials.length > 0 && <span className="ws-queued-meta">已附 {card.materials.length} 份资料</span>}
                </div>
                {turn?.status === "running" && (
                  <button
                    type="button"
                    className="ws-queued-guide"
                    onClick={() => void ws.steerQueued(card.runId)}
                    aria-label={`引导当前任务处理：${card.question}`}
                  >
                    引导
                    <ArrowUpRight size={14} aria-hidden="true" />
                  </button>
                )}
              </motion.div>
            ))}
          </AnimatePresence>
          {ws.conversationMode === "work" && (
            <div className="ws-mode-note" role="status">
              <BriefcaseBusiness size={14} aria-hidden="true" />
              <span>工作模式 · 需要文件操作时才准备执行环境</span>
            </div>
          )}
          <form
            className="ws-composer"
            onSubmit={(e) => {
              e.preventDefault();
              atEnd.current = true;
              void ws.send();
            }}
          >
            <AnimatePresence initial={false}>
              {(materials.length > 0 || uploads.length > 0) && (
                <motion.div
                  className="ws-attachment-list"
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.18 }}
                >
                  {materials.map((m) => (
                    <motion.div
                      layout
                      key={m.id}
                      className={`ws-attachment ${m.issue ? "has-issue" : ""}`}
                    >
                      <FileText size={17} />
                      <div>
                        <strong title={m.filename}>{m.filename}</strong>
                        <span title={m.issue || undefined}>
                          <MaterialDescription material={m} />{m.issue ? ` · ${m.issue}` : ""}
                        </span>
                      </div>
                      <IconButton
                        label={`移除 ${m.filename}`}
                        disabled={false}
                        onClick={() =>
                          void ws.removeDraftMaterial(m)
                        }
                      >
                        <X size={14} />
                      </IconButton>
                    </motion.div>
                  ))}
                  {uploads.map((u) => (
                    <motion.div
                      layout
                      key={u.key}
                      className={`ws-attachment ${u.error ? "has-issue" : ""}`}
                    >
                      {u.error ? (
                        <FileText size={17} />
                      ) : (
                        <ActivityIndicator size={17} />
                      )}
                      <div>
                        <strong>{u.file.name}</strong>
                        <span>{u.error || "正在上传…"}</span>
                      </div>
                      {u.error && (
                        <>
                          <button
                            type="button"
                            className="ws-text-button"
                            onClick={() => void ws.uploadOne(u)}
                          >
                            重试
                          </button>
                          <IconButton
                            label={`移除 ${u.file.name}`}
                            onClick={() => ws.dismissUpload(u.key)}
                          >
                            <X size={14} />
                          </IconButton>
                        </>
                      )}
                    </motion.div>
                  ))}
                </motion.div>
              )}
            </AnimatePresence>
            <label className="ws-sr-only" htmlFor="workspace-question">
              你的问题
            </label>
            <textarea
              id="workspace-question"
              ref={input}
              value={ws.draft}
              onChange={(e) => ws.setDraft(e.target.value)}
              placeholder={ws.conversationMode === "work" ? "描述要完成的工作和希望得到的文件。" : "让事实有序，让判断有据。"}
              rows={2}
              maxLength={50000}
              disabled={false}
              onKeyDown={(e) => {
                if (
                  e.key === "Enter" &&
                  !e.shiftKey && !e.ctrlKey && !e.metaKey &&
                  !e.nativeEvent.isComposing
                ) {
                  e.preventDefault();
                  atEnd.current = true;
                  void ws.send();
                }
              }}
            />
            <div className="ws-composer-toolbar">
              <div className="ws-composer-actions">
                <Menu
                  align="start"
                  trigger={
                    <IconButton
                      label="添加材料"
                      disabled={false}
                    >
                      <Paperclip size={19} />
                    </IconButton>
                  }
                >
                  <MenuItem onSelect={() => fileInput.current?.click()}>
                    <Paperclip size={16} />
                    上传本轮附件
                  </MenuItem>
                  <MenuItem onSelect={openMaterials}>
                    <Library size={16} />
                    从资料库选择
                  </MenuItem>
                </Menu>
                <ThinkingModeSelect value={ws.thinkingMode} onChange={ws.setThinkingMode} />
                {detail?.linked_resources.length ? (
                  <span className="ws-composer-hint">
                    <button type="button" onClick={openMaterials}>
                      {detail.linked_resources.length} 份已选资料
                    </button>
                  </span>
                ) : null}
              </div>
              {receiving && (
                <button
                  type="button"
                  className="ws-stop"
                  aria-label="停止回答"
                  onClick={ws.stop}
                  title="停止当前运行并保留已生成结果"
                >
                  <Square size={13} />
                  停止
                </button>
              )}
              {
                <button
                  className="ws-send"
                  type="submit"
                  aria-label="发送问题"
                  disabled={
                    !ws.draft.trim() ||
                    uploading
                  }
                >
                  <ArrowUp size={20} />
                </button>
              }
            </div>
            <input
              ref={fileInput}
              type="file"
              multiple
              className="ws-sr-only"
              tabIndex={-1}
              aria-label="上传本轮附件"
              onChange={(e) => {
                void ws.uploadFiles(Array.from(e.target.files || []));
                e.target.value = "";
              }}
            />
          </form>
          {empty ? (
            <div className="ws-suggestions">
              <button onClick={() => chooseTask(4)}>
                <ScanText size={16} />
                梳理事实
              </button>
              <button onClick={() => chooseTask(2)}>
                <Scale size={16} />
                审阅合同
              </button>
              <button onClick={() => chooseTask(0)}>
                <BookOpen size={16} />
                研究法律问题
              </button>
              <Menu
                trigger={
                  <button>
                    <MoreHorizontal size={16} />
                    更多任务
                  </button>
                }
              >
                {tasks.map((task, i) => (
                  <MenuItem key={task.label} onSelect={() => chooseTask(i)}>
                    {task.label}
                  </MenuItem>
                ))}
              </Menu>
            </div>
          ) : (
            <p className="ws-disclaimer">
              <span>Shift + Enter 换行</span>
            </p>
          )}
        </motion.div>
      </div>
      {sourceReference && (
        <SourceViewer
          reference={sourceReference}
          document={sourceDocument}
          loading={sourceLoading}
          error={sourceError}
          onClose={() => { setSourceReference(null); setSourceDocument(null); setSourceError(null); }}
        />
      )}
    </div>
  );
}

