import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowUpRight,
  BookOpen,
  Compass,
  ChevronLeft,
  Download,
  FileText,
  Plus,
  Search,
  Users,
  X,
} from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  maritimeApi,
  downloadDraft,
  type MaritimeSkill,
  type MaritimeCase,
  type CaseEntry,
  type SearchResult,
  type CorpusSource,
} from "@/lib/maritime-api";
import { Modal, Loading } from "./Primitives";
import "./maritime.css";

export type OfficePage = "skills" | "research" | "cases";
const labelFor = {
  skills: "技能工坊",
  research: "海事检索",
  cases: "案件协作",
};
const errorText = (e: unknown) =>
  e instanceof Error ? e.message : "操作未完成，请重试。";

function publicLocator(source: CorpusSource) {
  if (source.kind === "cases") return "";
  if (Array.isArray(source.locator)) return source.locator.filter(Boolean).join(" · ");
  return source.locator || "法规原文";
}

export default function MaritimeDesk({
  page,
  token,
  accountId,
}: {
  page: OfficePage;
  token: string;
  accountId: string;
}) {
  const api = useMemo(() => maritimeApi(token), [token]);
  const [error, setError] = useState("");
  useEffect(() => setError(""), [page]);
  return (
    <section className="maritime-desk" aria-label={labelFor[page]}>
      {error && (
        <div className="md-error" role="alert">
          {error}
          <button onClick={() => setError("")} aria-label="关闭提示">
            <X size={16} />
          </button>
        </div>
      )}
      {page === "skills" && (
        <Skills
          api={api}
          accountId={accountId}
          onError={setError}
          error={error}
        />
      )}
      {page === "research" && (
        <Research api={api} onError={setError} error={error} />
      )}
      {page === "cases" && <Cases api={api} onError={setError} error={error} />}
    </section>
  );
}
type Api = ReturnType<typeof maritimeApi>;
type Props = { api: Api; onError: (text: string) => void; error?: string };

function Skills({
  api,
  accountId,
  onError,
  error,
}: Props & { accountId: string }) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const query = useQuery({
    queryKey: ["maritime", accountId, "skills"],
    queryFn: api.skills,
  });
  const [filter, setFilter] = useState("all");
  const [selected, setSelected] = useState<MaritimeSkill | null>(null);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("");
  const [summary, setSummary] = useState("");
  const [instructions, setInstructions] = useState("");
  const [visibility, setVisibility] = useState<"private" | "community">(
    "private",
  );
  const [busy, setBusy] = useState(false);
  function edit(skill: MaritimeSkill | null) {
    setSelected(skill);
    setName(skill?.name || "");
    setSummary(skill?.summary || "");
    setInstructions(skill?.instructions || "");
    setVisibility(
      skill?.owner_id === accountId && skill.visibility === "community"
        ? "community"
        : "private",
    );
    setEditing(true);
  }
  async function save() {
    setBusy(true);
    onError("");
    try {
      const own = selected?.owner_id === accountId;
      const saved = await api.saveSkill(
        {
          name,
          summary,
          instructions,
          visibility,
          ...(own
            ? { expected_version: selected.version }
            : { parent_id: selected?.id }),
        },
        own ? selected.id : undefined,
      );
      setSelected(saved);
      setEditing(false);
      await client.invalidateQueries({
        queryKey: ["maritime", accountId, "skills"],
      });
    } catch (e) {
      onError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  const rows = (query.data?.skills || []).filter(
    (s) =>
      filter === "all" ||
      (filter === "mine"
        ? s.owner_id === accountId
        : s.visibility === "community"),
  );
  return (
    <>
      <div className="md-heading">
        <div>
          <span className="md-eyebrow">你的专业方法，可以成为下一项能力</span>
          <h1>把海事经验，写成技能。</h1>
          <p>
            选择一种处理方式开始，或创建一套属于自己的工作方式。
          </p>
        </div>
        <button className="ws-primary-button" onClick={() => edit(null)}>
          <Plus size={17} />
          创建技能
        </button>
      </div>
      <div className="md-tabs" role="group" aria-label="技能范围">
        {[
          ["all", "全部技能"],
          ["mine", "我的技能"],
          ["community", "社区分享"],
        ].map(([v, t]) => (
          <button
            key={v}
            aria-pressed={filter === v}
            onClick={() => setFilter(v)}
          >
            {t}
          </button>
        ))}
      </div>
      {query.isPending ? (
        <Loading />
      ) : query.isError ? (
        <div className="md-empty">
          <p>技能暂时无法载入。</p>
          <button
            className="ws-text-button"
            onClick={() => void query.refetch()}
          >
            重新载入
          </button>
        </div>
      ) : (
        <div className={`md-skill-list ${!rows.length ? "is-empty" : ""}`}>
          {rows.map((s, i) => (
            <article className="md-skill" key={s.id}>
              <div className="md-skill-number">
                {String(i + 1).padStart(2, "0")}
              </div>
              <div>
                <div className="md-row">
                  <h2>{s.name}</h2>
                  {s.visibility !== "builtin" && (
                    <span className="md-tag">
                      {s.owner_id === accountId ? "我的技能" : "社区作者"}
                    </span>
                  )}
                </div>
                <p>{s.summary}</p>
                <div className="md-skill-actions">
                  {s.owner_id === accountId && (
                    <button
                      className="ws-text-button"
                      onClick={() => edit(s)}
                    >
                      编辑
                    </button>
                  )}
                  <button
                    className="ws-text-button"
                    onClick={() =>
                      navigate(`/workspace?skill=${encodeURIComponent(s.id)}`)
                    }
                  >
                    开始任务
                    <ArrowUpRight size={15} />
                  </button>
                </div>
              </div>
            </article>
          ))}
          {!rows.length && (
            <div className="md-empty md-skill-empty">
              <span className="md-empty-mark" aria-hidden="true"><Compass size={23} /></span>
              <p>这里还没有技能。你可以创建自己的处理方式。</p>
              <button className="ws-secondary-button" onClick={() => edit(null)}>
                <Plus size={15} />
                创建第一项技能
              </button>
            </div>
          )}
        </div>
      )}
      <Modal
        open={editing}
        onOpenChange={(open) => {
          if (!busy) {
            setEditing(open);
            if (!open) setSelected(null);
          }
        }}
        title={selected?.owner_id === accountId ? "编辑技能" : "创建你的技能"}
        description="写清使用场景、处理步骤和交付结果。"
      >
        <p role={error ? "alert" : undefined} className="md-form-error">
          {error}
        </p>
        <form
          className="md-form"
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          <label>
            技能名称
            <input
              required
              maxLength={80}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <label>
            一句话介绍
            <input
              required
              maxLength={500}
              value={summary}
              onChange={(e) => setSummary(e.target.value)}
            />
          </label>
          <label>
            处理说明
            <textarea
              required
              rows={10}
              maxLength={20000}
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
              placeholder="使用场景、需要的材料、处理步骤、交付结果、异常时怎样处理…"
            />
          </label>
          <label>
            可见范围
            <select
              value={visibility}
              onChange={(e) =>
                setVisibility(e.target.value as typeof visibility)
              }
            >
              <option value="private">仅我自己</option>
              <option value="community">分享给平台注册用户</option>
            </select>
          </label>
          <p className="md-footnote">
            分享前请移除客户信息、案件细节和任何凭证。每次保存都会保留版本。
          </p>
          <button disabled={busy} className="ws-primary-button">
            {busy ? "正在保存…" : "保存技能"}
          </button>
        </form>
      </Modal>
    </>
  );
}

function Research({ api, onError }: Props) {
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<"laws" | "cases">("laws");
  const [current, setCurrent] = useState(true);
  const [result, setResult] = useState<SearchResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [source, setSource] = useState<CorpusSource | null>(null);
  async function search() {
    setBusy(true);
    onError("");
    setResult(null);
    try {
      setResult(await api.search(query, kind, current));
    } catch (e) {
      onError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <div className="md-heading">
        <div>
          <span className="md-eyebrow">依据先于结论</span>
          <h1>回到真实的海事资料。</h1>
          <p>检索自研法规与案例库，先读完整原文，再作判断。</p>
        </div>
        <BookOpen size={28} strokeWidth={1.2} />
      </div>
      <form
        className="md-search"
        onSubmit={(e) => {
          e.preventDefault();
          void search();
        }}
      >
        <div className="md-tabs">
          <button
            type="button"
            aria-pressed={kind === "laws"}
            onClick={() => {
              setKind("laws");
              setResult(null);
            }}
          >
            法规与公约
          </button>
          <button
            type="button"
            aria-pressed={kind === "cases"}
            onClick={() => {
              setKind("cases");
              setResult(null);
            }}
          >
            海事案例
          </button>
        </div>
        <label className="md-search-field">
          <Search size={20} />
          <input
            aria-label="海事检索关键词"
            value={query}
            minLength={2}
            maxLength={500}
            required
            onChange={(e) => setQuery(e.target.value)}
            placeholder={
              kind === "laws"
                ? "例如：承运人 货损 责任限制"
                : "例如：提单 货损 无单放货"
            }
          />
          <button className="ws-primary-button" disabled={busy}>
            {busy ? "检索中…" : "检索"}
          </button>
        </label>
        {kind === "laws" && (
          <label className="md-check">
            <input
              type="checkbox"
              checked={current}
              onChange={(e) => setCurrent(e.target.checked)}
            />
            优先显示现行资料
          </label>
        )}
      </form>
      {busy && <Loading label="正在检索真实语料…" />}
      {!result && !busy && (
        <div className="md-empty md-research-empty">
          <BookOpen size={38} strokeWidth={1} />
          <h2>每一个判断，都应有来处。</h2>
          <p>
            输入具体主题或条文关键词。案例检索请同时核对关键事实与裁判语境。
          </p>
        </div>
      )}
      {result && (
        <>
          <div className="md-result-info">
            <span>找到 {result.items.length} 条匹配资料</span>
          </div>
          {result.items.map((item) => (
            <article className="md-result" key={item.id}>
              <span className={"md-result-kind " + (kind === "laws" ? "is-law" : "is-case")}>
                {kind === "laws" ? "法规" : "案例"}
              </span>
              <div className="md-result-copy">
                <h2>{item.title}</h2>
                {publicLocator(item) && (
                  <p className="md-result-locator">{publicLocator(item)}</p>
                )}
              </div>
              <button
                className="ws-text-button md-result-open"
                onClick={() => setSource(item)}
              >
                查看原文
                <ArrowUpRight size={15} />
              </button>
            </article>
          ))}
          {!result.items.length && (
            <div className="md-empty">
              本次没有匹配结果。可更换关键词。
            </div>
          )}
        </>
      )}
      <Modal
        open={!!source}
        onOpenChange={(open) => {
          if (!open) setSource(null);
        }}
        title={source?.title || "原文"}
        description={source ? "舟序资料库中的完整原文" : ""}
      >
        <div className="md-source-text">{source?.text}</div>
      </Modal>
    </>
  );
}

function Cases({ api, onError, error }: Props) {
  const navigate = useNavigate();
  const [rows, setRows] = useState<MaritimeCase[]>([]);
  const [active, setActive] = useState<MaritimeCase | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [create, setCreate] = useState(false);
  const [title, setTitle] = useState("");
  const [summary, setSummary] = useState("");
  const [busy, setBusy] = useState(false);
  const [entry, setEntry] = useState<Partial<CaseEntry> | null>(null);
  const [invite, setInvite] = useState(false);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("collaborator");
  const [versions, setVersions] = useState<CaseEntry[] | null>(null);
  useEffect(() => {
    let live = true;
    api
      .cases()
      .then((r) => {
        if (live) {
          setRows(r.cases);
          setLoaded(true);
        }
      })
      .catch((e) => {
        if (live) {
          onError(errorText(e));
          setLoaded(true);
        }
      });
    return () => {
      live = false;
    };
  }, [api, onError]);
  async function open(id: string) {
    try {
      setActive(await api.case(id));
    } catch (e) {
      onError(errorText(e));
    }
  }
  async function saveCase() {
    setBusy(true);
    try {
      setActive(await api.createCase(title, summary));
      setCreate(false);
      setTitle("");
      setSummary("");
      setRows((await api.cases()).cases);
    } catch (e) {
      onError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  async function saveEntry() {
    if (!active || !entry) return;
    setBusy(true);
    try {
      const { id, version, kind, title, content, due_at, assignee_id, status } =
        entry;
      await api.saveEntry(
        active.id,
        {
          kind,
          title,
          content,
          due_at,
          assignee_id,
          status,
          expected_version: version,
        },
        id,
      );
      setEntry(null);
      await open(active.id);
    } catch (e) {
      onError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  const statusName = {
    open: "待处理",
    done: "已完成",
    draft: "待复核",
    reviewed: "已复核",
  };
  return (
    <>
      <div className="md-heading">
        <div>
          {active ? (
            <button className="ws-text-button" onClick={() => setActive(null)}>
              <ChevronLeft size={15} />
              全部案件
            </button>
          ) : (
            <span className="md-eyebrow">让判断有记录，让协作有交接</span>
          )}
          <h1>{active?.title || "一宗案件，一个工作空间。"}</h1>
          <p>
            {active?.summary || "集中管理任务、证据笔记、文书版本与复核事项。"}
          </p>
        </div>
        {active ? (
          <button
            className="ws-secondary-button"
            onClick={() => setInvite(true)}
          >
            <Users size={16} />
            协作成员 · {active.members.length}
          </button>
        ) : (
          <button className="ws-primary-button" onClick={() => setCreate(true)}>
            <Plus size={16} />
            建立案件
          </button>
        )}
      </div>
      {!loaded ? (
        <Loading />
      ) : !active ? (
        <div className="md-case-list">
          {rows.map((row) => (
            <button
              key={row.id}
              className="md-case-row"
              onClick={() => void open(row.id)}
            >
              <FileText size={23} />
              <div>
                <h2>{row.title}</h2>
                <p>{row.summary || "尚未添加案情摘要"}</p>
              </div>
              <span className="md-tag">
                {row.role === "owner"
                  ? "负责人"
                  : row.role === "reviewer"
                    ? "复核人"
                    : "协作者"}
              </span>
              <ArrowUpRight size={17} />
            </button>
          ))}
          {!rows.length && (
            <div className="md-empty">
              <h2>从手上的第一宗案件开始。</h2>
              <p>案件只对负责人和明确加入的成员开放。</p>
            </div>
          )}
        </div>
      ) : (
        <>
          <div className="md-actions">
            <button
              className="ws-primary-button"
              onClick={() =>
                navigate(
                  `/workspace?case=${encodeURIComponent(active.id)}&skill=case-assessment`,
                )
              }
            >
              让智能体研判本案
              <ArrowUpRight size={15} />
            </button>
            {[
              ["task", "添加任务"],
              ["evidence", "记录证据"],
              ["document", "保存文书"],
            ].map(([kind, text]) => (
              <button
                key={kind}
                className="ws-secondary-button"
                onClick={() =>
                  setEntry({
                    kind: kind as CaseEntry["kind"],
                    title: "",
                    content: "",
                    status: kind === "task" ? "open" : "draft",
                  })
                }
              >
                <Plus size={15} />
                {text}
              </button>
            ))}
          </div>
          <div className="md-case-columns">
            <div>
              {active.entries.map((item) => (
                <article className="md-entry" key={item.id}>
                  <div className="md-row">
                    <span className="md-tag">
                      {
                        {
                          task: "任务",
                          evidence: "证据笔记",
                          document: "文书",
                        }[item.kind]
                      }{" "}
                      · v{item.version}
                    </span>
                    <span className={`md-status ${item.status}`}>
                      {statusName[item.status]}
                    </span>
                  </div>
                  <h2>{item.title}</h2>
                  {item.due_at && (
                    <p className="md-date">
                      记录日期：{item.due_at} · 请人工核对起算依据
                    </p>
                  )}
                  <p className="md-excerpt">
                    {item.content || "未添加详细说明"}
                  </p>
                  <div className="md-actions">
                    <button
                      className="ws-text-button"
                      onClick={() => setEntry(item)}
                    >
                      编辑 / 复核
                    </button>
                    <button
                      className="ws-text-button"
                      onClick={() =>
                        void api
                          .versions(active.id, item.id)
                          .then((r) => setVersions(r.versions))
                          .catch((e) => onError(errorText(e)))
                      }
                    >
                      版本记录
                    </button>
                    {item.kind === "document" && (
                      <button
                        className="ws-text-button"
                        onClick={() => downloadDraft(item.title, item.content)}
                      >
                        <Download size={14} />
                        导出草稿
                      </button>
                    )}
                  </div>
                </article>
              ))}
              {!active.entries.length && (
                <div className="md-empty">
                  添加第一项待办，或记录需要核对的证据和文书。
                </div>
              )}
            </div>
            <aside className="md-audit">
              <h2>操作记录</h2>
              <p className="md-footnote">
                保存动作留痕；复核状态由案件成员确认，不代表法律效力认证。
              </p>
              {active.audit.map((a) => (
                <div key={a.id}>
                  <span>
                    {{
                      "case.created": "建立案件",
                      "task.open": "保存待办",
                      "task.done": "完成任务",
                      "evidence.draft": "保存证据笔记",
                      "evidence.reviewed": "确认复核证据",
                      "document.draft": "保存文书草稿",
                      "document.reviewed": "确认复核文书",
                      "member.collaborator": "加入协作者",
                      "member.reviewer": "加入复核人",
                    }[a.action] || "更新记录"}
                  </span>
                  <time>{new Date(a.created_at).toLocaleString("zh-CN")}</time>
                </div>
              ))}
            </aside>
          </div>
        </>
      )}
      <Modal
        open={create}
        onOpenChange={setCreate}
        title="建立案件"
        description="填写实际案名与已知事实，不必为未知信息编造答案。"
      >
        <p role={error ? "alert" : undefined} className="md-form-error">
          {error}
        </p>
        <form
          className="md-form"
          onSubmit={(e) => {
            e.preventDefault();
            void saveCase();
          }}
        >
          <label>
            案件名称
            <input
              required
              maxLength={160}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </label>
          <label>
            案情摘要
            <textarea
              rows={5}
              maxLength={10000}
              value={summary}
              onChange={(e) => setSummary(e.target.value)}
            />
          </label>
          <button disabled={busy} className="ws-primary-button">
            {busy ? "保存中…" : "建立案件"}
          </button>
        </form>
      </Modal>
      <Modal
        open={!!entry}
        onOpenChange={(open) => {
          if (!open && !busy) setEntry(null);
        }}
        title="保存案件事项"
        description="修改会保留历史版本。确认复核前请实际核对内容。"
      >
        <p role={error ? "alert" : undefined} className="md-form-error">
          {error}
        </p>
        <form
          className="md-form"
          onSubmit={(e) => {
            e.preventDefault();
            void saveEntry();
          }}
        >
          <label>
            标题
            <input
              required
              maxLength={160}
              value={entry?.title || ""}
              onChange={(e) => setEntry({ ...entry, title: e.target.value })}
            />
          </label>
          <label>
            正文 / 核对说明
            <textarea
              rows={9}
              maxLength={100000}
              value={entry?.content || ""}
              onChange={(e) => setEntry({ ...entry, content: e.target.value })}
            />
          </label>
          <div className="md-form-row">
            <label>
              关键日期（可选）
              <input
                type="date"
                value={entry?.due_at || ""}
                onChange={(e) =>
                  setEntry({ ...entry, due_at: e.target.value || null })
                }
              />
            </label>
            <label>
              状态
              <select
                aria-label="状态"
                value={entry?.status}
                onChange={(e) =>
                  setEntry({
                    ...entry,
                    status: e.target.value as CaseEntry["status"],
                  })
                }
              >
                {entry?.kind === "task" ? (
                  <>
                    <option value="open">待处理</option>
                    <option value="done">已完成</option>
                  </>
                ) : (
                  <>
                    <option value="draft">待复核</option>
                    {active?.role !== "collaborator" && (
                      <option value="reviewed">已核对内容，确认复核</option>
                    )}
                  </>
                )}
              </select>
            </label>
          </div>
          <label>
            责任人
            <select
              aria-label="责任人"
              value={entry?.assignee_id || ""}
              onChange={(e) =>
                setEntry({ ...entry, assignee_id: e.target.value || null })
              }
            >
              <option value="">暂未指定</option>
              {active?.members.map((m, i) => (
                <option key={m.account_id} value={m.account_id}>
                  {m.display_name || m.email || `成员 ${i + 1}`} ·{" "}
                  {m.role === "owner"
                    ? "负责人"
                    : m.role === "reviewer"
                      ? "复核人"
                      : "协作者"}
                </option>
              ))}
            </select>
          </label>
          <button disabled={busy} className="ws-primary-button">
            {busy ? "保存中…" : "保存新版本"}
          </button>
        </form>
      </Modal>
      <Modal
        open={invite}
        onOpenChange={setInvite}
        title="案件协作成员"
        description="仅负责人可以将已注册用户加入本案件。不会自动共享个人对话或资料库。"
      >
        <div className="md-member-list">
          {active?.members.map((m, i) => (
            <p key={m.account_id}>
              {m.display_name || m.email || `成员 ${i + 1}`}
              <span>
                {
                  {
                    owner: "负责人",
                    reviewer: "复核人",
                    collaborator: "协作者",
                  }[m.role]
                }
              </span>
              {active.role === "owner" && m.role !== "owner" && (
                <button
                  disabled={busy}
                  type="button"
                  className="ws-text-button"
                  onClick={() => {
                    setBusy(true);
                    void api
                      .removeMember(active.id, m.account_id)
                      .then(setActive)
                      .catch((e) => onError(errorText(e)))
                      .finally(() => setBusy(false));
                  }}
                >
                  移除
                </button>
              )}
            </p>
          ))}
        </div>
        {active?.role === "owner" && (
          <>
            <p role={error ? "alert" : undefined} className="md-form-error">
              {error}
            </p>
            <form
              className="md-form"
              onSubmit={(e) => {
                e.preventDefault();
                if (!active) return;
                setBusy(true);
                void api
                  .invite(active.id, email, role)
                  .then((value) => {
                    setActive(value);
                    setEmail("");
                  })
                  .catch((e) => onError(errorText(e)))
                  .finally(() => setBusy(false));
              }}
            >
              <label>
                已注册邮箱
                <input
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </label>
              <label>
                角色
                <select value={role} onChange={(e) => setRole(e.target.value)}>
                  <option value="collaborator">协作者</option>
                  <option value="reviewer">专业复核人</option>
                </select>
              </label>
              <button disabled={busy} className="ws-primary-button">
                添加成员
              </button>
            </form>
          </>
        )}
      </Modal>
      <Modal
        open={versions !== null}
        onOpenChange={(open) => {
          if (!open) setVersions(null);
        }}
        title="历史版本"
        description="历史内容保持原样，不会覆盖当前版本。"
      >
        <div className="md-version-list">
          {versions?.map((v) => (
            <article key={v.version}>
              <span className="md-tag">
                v{v.version} · {statusName[v.status]}
              </span>
              <h3>{v.title}</h3>
              <p className="md-source-text">{v.content}</p>
              <time>{new Date(v.updated_at).toLocaleString("zh-CN")}</time>
            </article>
          ))}
        </div>
      </Modal>
    </>
  );
}
