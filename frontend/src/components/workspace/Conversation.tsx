import { ThinkingModeSelect } from "./ThinkingModeSelect";
import { ModelSelect } from "./ModelSelect";
import { Fragment, memo, useCallback, useEffect, useRef, useState } from "react";
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
  Scale,
  ScanText,
  BookOpen,
  MoreHorizontal,
  ExternalLink,
  LoaderCircle,
} from "lucide-react";
import type { PendingTurn, useWorkspace } from "@/hooks/use-workspace";
import type { Message } from "@/lib/workbench-api";
import type { Components } from "react-markdown";
import { mergeRunMessages } from "@/lib/workbench-api";
import { effectiveModelSwitches, modelLabel, modelSwitchText, type ModelSwitchEvent } from "@/lib/model-selection";
import { visibleAnswerActions } from "@/lib/answer-actions";
import { useAuthSession } from "@/lib/auth-session";
import { maritimeApi, type MaritimeCase } from "@/lib/maritime-api";
import { IconButton, Menu, MenuItem, Loading, LoadError, Modal } from "./Primitives";

import { RunProgress } from "./RunProgress";
import { ActivityIndicator } from "./ActivityIndicator";
import { MaterialCards, MaterialDescription } from "./MaterialCards";
import { WorkPackages } from "./WorkPackages";
import { useStreamingText } from "@/hooks/use-streaming-text";
import { SourceSummary } from "./ResearchPanel";

type Workspace = ReturnType<typeof useWorkspace>;

function ModelSwitchDivider({ event, pending = false }: { event: ModelSwitchEvent; pending?: boolean }) {
  const reduced = useReducedMotion();
  return <motion.div
    className="ws-model-switch-event"
    role="status"
    aria-live="polite"
    data-model-switch-id={event.id}
    title={pending ? "下一条新消息使用此模型" : undefined}
    initial={{ opacity: 0, y: reduced ? 0 : 8 }}
    animate={{ opacity: 1, y: 0 }}
    transition={{ duration: reduced ? 0 : 0.2 }}
  >
    <span aria-hidden="true" />
    <span>{modelSwitchText(event)}</span>
    <span aria-hidden="true" />
  </motion.div>;
}

function ContextCompactionDivider() {
  const reduced = useReducedMotion();
  return <motion.div
    className="ws-model-switch-event ws-context-compaction-event"
    role="status"
    aria-live="polite"
    initial={{ opacity: 0, y: reduced ? 0 : 8 }}
    animate={{ opacity: 1, y: 0 }}
    transition={{ duration: reduced ? 0 : 0.2 }}
  >
    <span aria-hidden="true" />
    <span>已整理较早的对话，继续处理</span>
    <span aria-hidden="true" />
  </motion.div>;
}

function ModeUpgradeChoice({ uploading, onAnswer }: {
  uploading: boolean;
  onAnswer: (choice: string) => Promise<void>;
}) {
  const [submitting, setSubmitting] = useState<string | null>(null);
  async function choose(choice: string) {
    setSubmitting(choice);
    try {
      await onAnswer(choice);
    } finally {
      setSubmitting(null);
    }
  }
  return (
    <section className="ws-mode-choice" aria-label="模式确认" aria-busy={submitting !== null}>
      <p>这项操作需要工作模式</p>
      <div className="ws-mode-choice-actions">
        <button type="button" className="ws-primary-button" disabled={uploading || submitting !== null} onClick={() => void choose("upgrade_work")}>
          {submitting === "upgrade_work" ? "正在切换…" : "升级为工作模式"}
        </button>
        <button type="button" className="ws-secondary-button" disabled={uploading || submitting !== null} onClick={() => void choose("keep_chat")}>
          {submitting === "keep_chat" ? "正在确认…" : "保持聊天模式"}
        </button>
      </div>
    </section>
  );
}

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

const markdownComponents = {
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
};

const INTERNAL_ARTIFACT_MARKDOWN = /!?(\[[^\]]*\])\((?:\/api)?\/workspace\/runs\/[^\s)]+\/artifacts\/[^\s)]+\)/gi;
const INTERNAL_ARTIFACT_URL = /(?:\/api)?\/workspace\/runs\/[^\s<>]+\/artifacts\/[^\s<>]+/gi;
const INTERNAL_ARTIFACT_PATH_LINE = /^\s*(?:[-*+]\s+)?(?:PDF|Word|DOCX|Excel|文件)?\s*[:：]\s*(?:papers|work)\/[^\s]+\.(?:pdf|docx|docm|xlsx|xlsm|pptx|pptm|csv|txt|md|json)\s*$/i;

function visibleAnswerContent(content: string) {
  return content
    .split(/\r?\n/)
    .filter((line) => !INTERNAL_ARTIFACT_PATH_LINE.test(line))
    .map((line) => line.replace(INTERNAL_ARTIFACT_MARKDOWN, "$1").replace(INTERNAL_ARTIFACT_URL, "").trimEnd())
    .filter((line, index, lines) => line.trim() || (index > 0 && lines[index - 1].trim()))
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export const Markdown = memo(function Markdown({ content, onSource }: { content: string; onSource?: (id: string) => void }) {
  const components: Components = { ...markdownComponents, a: ({ children, href }) => {
    const match = href?.match(/^(?:\/api)?\/workspace\/sources\/(paper-[A-Za-z0-9._-]+)$/);
    return match && onSource ? <button type="button" className="ws-citation-link" onClick={() => onSource(match[1])}>{children}<BookOpen size={13} aria-hidden="true" /></button> : <a href={href} target="_blank" rel="noopener noreferrer">{children}<ArrowUpRight size={13} aria-hidden="true" /></a>;
  } };
  return (
    <div className="ws-prose">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={components}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
});

function StreamingAnswer({ text, live, onSource }: { text: string; live: boolean; onSource: (id: string) => void }) {
  const visible = useStreamingText(text, live);
  return <div data-testid="stream-answer"><Markdown content={visibleAnswerContent(visible)} onSource={onSource} /></div>;
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

function Answer({
  message,
  card,
  onAnswerInputRequest,
  onNotice,
  onContinue,
  uploading = false,
  onRetry,
  onOpenSources,
}: {
  message: Message;
  card?: PendingTurn;
  onAnswerInputRequest: (choice: string) => Promise<void>;
  onNotice: (message: string) => void;
  onContinue?: () => void;
  uploading?: boolean;
  onRetry?: () => void;
  onOpenSources: (sourceId?: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const visibleContent = visibleAnswerContent(message.content);
  const actions = visibleAnswerActions({ ...message, content: visibleContent }, card?.status);
  useEffect(() => () => clearTimeout(timer.current), []);
  async function copy() {
    try {
      await navigator.clipboard.writeText(visibleContent);
      setCopied(true);
      clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 1800);
    } catch {
      onNotice("未能复制，请选择需要的文字后手动复制。");
    }
  }
  return (
    <article className="ws-answer" aria-busy={card?.state === "receiving"}>
      <div className="ws-answer-byline">
        <span className="ws-answer-mark" aria-hidden="true">
          Z.
        </span>
        <span>舟序</span>
        {message.model_snapshot && <span className="ws-answer-model" title="本次任务使用的模型">{modelLabel(message.model_snapshot.profile_id)}</span>}
      </div>
      <RunProgress
        activities={message.activity}
        stages={message.stages}
        nodes={message.node_states}
        status={message.status}
        elapsedMs={message.active_elapsed_ms}
        wallElapsedMs={message.wall_elapsed_ms}
        live={card?.state === "receiving"}
        observedAt={card?.observedAt}
      />
      {message.status === "paused" && message.content && <span className="ws-incomplete-label">未完成 · 已保留正文</span>}
      <StreamingAnswer text={message.content} live={card?.state === "receiving"} onSource={onOpenSources} />
      <SourceSummary sources={message.sources} onOpen={onOpenSources} />
      {(!card || card.status === "succeeded") && <WorkPackages
        runId={message.run_id || undefined}
        renderText={(text) => <Markdown content={text} onSource={onOpenSources} />}
      />}
      {card?.state === "waiting" && card.inputRequest && (
        card.inputRequest.kind === "mode_upgrade" ? (
          <ModeUpgradeChoice uploading={uploading} onAnswer={onAnswerInputRequest} />
        ) : (
        <div className="ws-run-status ws-run-status-waiting" role="status">
          <p>{card.inputRequest.question}</p>
          {card.inputRequest.options.length > 0 && (
            <div className="ws-waiting-options">
              {card.inputRequest.options.map((opt, idx) => (
                <button
                  key={opt.value}
                  type="button"
                  disabled={uploading}
                  className={idx === 0 ? "ws-primary-button ws-waiting-option-primary" : "ws-text-button ws-waiting-option-secondary"}
                  onClick={() => void onAnswerInputRequest(opt.value)}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          )}
          <p className="ws-muted">
            {card.inputRequest.options.length > 0
              ? "可直接点击上方选项确认，或在下方补充具体要求后发送。"
              : "请在下方输入补充信息后发送。"}
          </p>
          <button className="ws-text-button" onClick={onRetry}>重新处理本任务</button>
        </div>
        )
      )}

      {message.status === "pausing" && <div className="ws-run-status" role="status">正在保存当前进度，请稍候…</div>}
      {message.status === "cancelled" && (
        <div className="ws-run-status ws-run-status-cancelled" role="status">
          已取消
        </div>
      )}
      {message.status === "paused" && (
        <div className="ws-run-status" role="status">
          {message.recovery?.message || "已暂停，已保留当前进度。"}
        </div>
      )}
      {message.status === "failed" && (
        <div className="ws-run-status" role="status">
          {message.recovery?.message || "处理未完成，已保留已生成内容。"}
        </div>
      )}
      {(actions.answer || actions.recovery) && <div className="ws-answer-tools">
        {actions.recovery && (
          <div className="ws-answer-action-group" aria-label="恢复操作">
            {message.recovery?.can_continue && <button className="ws-text-button" disabled={uploading} onClick={onContinue}>继续处理</button>}
            {message.recovery?.can_retry && <button className="ws-text-button" onClick={onRetry}>重新做</button>}
          </div>
        )}
        {actions.answer && <div className="ws-answer-action-group ws-answer-action-group-secondary" aria-label="回答操作">
          <button className="ws-text-button" onClick={() => void copy()}>
            {copied ? <Check size={15} /> : <Copy size={15} />}
            {copied ? "已复制" : "复制回答"}
          </button>
          <SaveAnswer message={{ ...message, content: visibleContent }} onNotice={onNotice} />
        </div>}
      </div>}
    </article>
  );
}

function cardMessage(card: PendingTurn, role: Message["role"], persisted?: Message): Message {
  return {
    ...persisted,
    id: `${card.runId}:${role}`, role, run_id: card.runId,
    created_at: card.createdAt ?? persisted?.created_at ?? "",
    content: role === "user" ? card.question : card.answer,
    model_snapshot: card.modelSnapshot,
    context_compaction_count: card.contextCompactionCount,
    materials: card.materials, updates: card.updates,
    status: card.status, recovery: card.recovery,
    activity: card.activities, stages: card.stages,
    node_states: card.nodes, active_elapsed_ms: card.activeElapsedMs,
    wall_elapsed_ms: card.wallElapsedMs, sources: card.sources, artifacts: card.artifacts,
  };
}

export default function Conversation({
  ws,
  id,
  openMaterials,
  openSources,
}: {
  ws: Workspace;
  id?: string;
  openMaterials: () => void;
  openSources: (runId?: string, sourceId?: string) => void;
}) {
  const reduced = useReducedMotion();
  const reader = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const atEnd = useRef(true);
  const previousId = useRef(id);
  const [showLatest, setShowLatest] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const dragDepth = useRef(0);
  const detail = ws.detail.data;
  // One keyed list and one answer component for streaming and persisted messages.
  // Replacing the data source must never replace the reader's DOM nodes.
  const cardsByRun = new Map(ws.visibleRuns.map(card => [card.runId, card]));
  const messages = mergeRunMessages(detail?.messages ?? [], ws.suppressedRunIds).flatMap(message => {
    const card = cardsByRun.get(message.run_id);
    if (!card) return [message];
    if (card.status === "queued" || message.role === "assistant") return [];
    // A running task may have only its question persisted so far.
    return [cardMessage(card, "user", message), cardMessage(card, "assistant")];
  });
  const persistedRuns = new Set(detail?.messages.map(message => message.run_id));
  for (const card of ws.visibleRuns) {
    if (card.status !== "queued" && !persistedRuns.has(card.runId)) {
      messages.push(cardMessage(card, "user"), cardMessage(card, "assistant"));
    }
  }
  const modelSwitches = effectiveModelSwitches(messages, id || "new");

  const turn = ws.turn?.conversationId === id ? ws.turn : null;
  const receiving = turn?.status === "running";
  const continuationMode = Boolean(
    turn &&
    (turn.status === "paused" || (turn.status === "failed" && turn.recovery.can_continue)),
  );
  const selectedModel = ws.modelCatalog?.groups
    .flatMap(group => group.models)
    .find(model => model.profile_id === ws.modelProfileId && model.available);
  const empty = !detail?.messages.length && !turn;
  const materials = ws.draftMaterials;
  const uploads = ws.uploads.filter((u) => u.conversationId === id);
  const uploading = ws.uploadBusy;
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
        onWheel={(event) => { if (event.deltaY < 0) atEnd.current = false; }}
        onTouchMove={() => { atEnd.current = false; }}
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
              {messages.map((message) => <Fragment key={message.run_id ? `${message.run_id}:${message.role}` : message.id}>
                {modelSwitches.has(message.id) && <ModelSwitchDivider event={modelSwitches.get(message.id)!} />}
                {message.role === "assistant" && (message.context_compaction_count ?? 0) > 0 && <ContextCompactionDivider />}
                {
                message.role === "user" ? (
                  <article className="ws-question" key={message.run_id ? `${message.run_id}:user` : message.id}>
                    <div>{message.content}</div>
                    <MaterialCards materials={message.materials} />
                    {message.updates?.map((text, index) => <div className="ws-user-update" key={index}><small>补充要求</small><p>{text}</p></div>)}
                  </article>
                ) : (
                  <Answer
                    key={message.run_id ? `${message.run_id}:assistant` : message.id}
                    message={message}
                    card={cardsByRun.get(message.run_id)}
                    onAnswerInputRequest={(choice) => ws.answerInputRequest(choice, message.run_id || undefined)}
                    onNotice={ws.setNotice}
                    onContinue={() => void ws.continueRun(message.run_id || undefined)}
                    uploading={uploading}
                    onRetry={() => void ws.retryTurn(message.run_id || undefined)}
                    onOpenSources={(sourceId) => openSources(message.run_id ?? undefined, sourceId)}
                  />
                )}
              </Fragment>)}

            </motion.div>
          )}
          </AnimatePresence>
          {ws.pendingModelSwitch && <ModelSwitchDivider event={ws.pendingModelSwitch} pending />}
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
          {continuationMode && (
            <div className="ws-mode-note ws-continuation-note" role="status">
              <button
                type="button"
                className="ws-text-button"
                style={{ marginLeft: "auto", fontSize: "12px" }}
                onClick={ws.dismissContinuation}
              >
                新建提问
              </button>
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
              placeholder={continuationMode ? "补充继续要求（将接着当前任务处理）" : ws.conversationMode === "work" ? "描述要完成的工作和希望得到的文件。" : "让事实有序，让判断有据。"}
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
                <ModelSelect
                  catalog={ws.modelCatalog}
                  value={ws.modelProfileId}
                  onChange={ws.setModelProfile}
                />
                {selectedModel && (
                  <ThinkingModeSelect
                    value={ws.thinkingMode}
                    modes={selectedModel.thinking_modes}
                    onChange={ws.setThinkingMode}
                  />
                )}
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
                  aria-label="暂停回答"
                  onClick={ws.pause}
                  title="暂停当前运行并保存检查点"
                >
                  <Square size={13} />
                  暂停
                </button>
              )}
              {
                <button
                  className="ws-send"
                  type="submit"
                  aria-label="发送问题"
                  disabled={
                    !ws.draft.trim() ||
                    uploading ||
                    ws.turn?.state === "pausing"
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

    </div>
  );
}

