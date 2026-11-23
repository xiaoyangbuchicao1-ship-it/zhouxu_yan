import { useEffect, useMemo, useState } from "react";
import {
  ArrowUpRight,
  BookOpen,
  Search,
  X,
} from "lucide-react";
import {
  maritimeApi,
  type SearchResult,
  type CorpusSource,
} from "@/lib/maritime-api";
import { Modal, Loading } from "./Primitives";
import Skills from "./Skills";
import { workbenchApi } from "@/lib/workbench-api";
import { SourcePreview } from "./SourcePreview";
import "./maritime.css";

export type OfficePage = "skills" | "research";
const labelFor = {
  skills: "技能",
  research: "海事检索",
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
  const sourceApi = useMemo(() => workbenchApi(token), [token]);
  const [error, setError] = useState("");
  useEffect(() => setError(""), [page]);
  return (
    <section className="maritime-desk" aria-label={labelFor[page]}>
      {error && page !== "skills" && (
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
        <Research api={api} sourceApi={sourceApi} accountId={accountId} onError={setError} error={error} />
      )}
    </section>
  );
}
type Api = ReturnType<typeof maritimeApi>;
type Props = { api: Api; onError: (text: string) => void; error?: string };

function Research({ api, sourceApi, accountId, onError }: Props & { sourceApi: ReturnType<typeof workbenchApi>; accountId: string }) {
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<"laws" | "cases" | "information_cards">("laws");
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
          <p>检索法规、案例与论文，查看原文。</p>
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
          <button type="button" aria-pressed={kind === "information_cards"} onClick={() => { setKind("information_cards"); setResult(null); }}>论文资料</button>
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
                : kind === "cases" ? "例如：提单 货损 无单放货" : "例如：海上保险 代位求偿"
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
              <span className={"md-result-kind " + (kind === "laws" ? "is-law" : kind === "information_cards" ? "is-paper" : "is-case")}>
                {kind === "laws" ? "法规" : kind === "cases" ? "案例" : "论文"}
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
        open={!!source && source.kind !== "information_cards"}
        onOpenChange={(open) => {
          if (!open) setSource(null);
        }}
        title={source?.title || "原文"}
        description={source ? "舟序资料库中的完整原文" : ""}
      >
        <div className="md-source-text">{source?.text}</div>
      </Modal>
      <SourcePreview api={sourceApi} scope={["maritime", accountId]} sourceId={source?.source_id ?? null} onClose={() => setSource(null)} />
    </>
  );
}
