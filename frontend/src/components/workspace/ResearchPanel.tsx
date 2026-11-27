import { useState } from "react";
import * as Dropdown from "@radix-ui/react-dropdown-menu";
import { Check, ChevronDown, Globe, Search, ArrowUpRight } from "lucide-react";
import type { useWorkspace } from "@/hooks/use-workspace";
import type { SourceReference } from "@/lib/workbench-api";
import { SourcePreview } from "./SourcePreview";

type Workspace = ReturnType<typeof useWorkspace>;

export function sourceGroups(ws: Workspace) {
  const groups = new Map<string, { id: string; question: string; sources: SourceReference[] }>();
  for (const message of ws.detail.data?.messages ?? []) {
    if (message.role === "user" && message.run_id) {
      const answer = ws.detail.data?.messages.find(m => m.run_id === message.run_id && m.role === "assistant");
      groups.set(message.run_id, { id: message.run_id, question: message.content, sources: answer?.sources ?? message.sources ?? [] });
    }
  }
  for (const run of ws.visibleRuns) {
    if (run.runId) groups.set(run.runId, { id: run.runId, question: run.question, sources: run.sources });
  }
  return [...groups.values()].map(group => ({ ...group, sources: [...new Map(group.sources.map(s => [s.source_id, s])).values()] }));
}

export function SourceSummary({ sources = [], onOpen }: { sources?: SourceReference[]; onOpen: () => void }) {
  const count = new Set(sources.map(source => source.source_id)).size;
  if (!count) return null;
  return <button type="button" className="ws-source-summary" onClick={() => onOpen()}>
    已找到 {count} 份资料 <span>查看资料 <ArrowUpRight size={13} aria-hidden="true" /></span>
  </button>;
}

const QUESTION_PREVIEW_LENGTH = 28;

function questionPreview(value: string) {
  const normalized = value.replace(/\s+/g, " ").trim();
  return normalized.length > QUESTION_PREVIEW_LENGTH
    ? `${normalized.slice(0, QUESTION_PREVIEW_LENGTH)}...`
    : normalized || "未命名问题";
}

function QuestionSelect({
  groups,
  selectedId,
  onSelect,
}: {
  groups: { id: string; question: string }[];
  selectedId?: string;
  onSelect: (id: string) => void;
}) {
  const selectedIndex = groups.findIndex(group => group.id === selectedId);
  const selected = groups[selectedIndex] ?? groups.at(-1);
  const selectedLabel = selected
    ? `第 ${selectedIndex >= 0 ? selectedIndex + 1 : groups.length} 问 · ${questionPreview(selected.question)}`
    : "还没有提问";

  return (
    <Dropdown.Root>
      <Dropdown.Trigger asChild>
        <button
          type="button"
          className="ws-question-trigger"
          aria-label="按提问查看"
          disabled={!groups.length}
        >
          <span className="ws-question-trigger-label" title={selected?.question}>
            {selectedLabel}
          </span>
          <ChevronDown size={15} className="ws-question-chevron" aria-hidden="true" />
        </button>
      </Dropdown.Trigger>
      <Dropdown.Portal>
        <Dropdown.Content
          className="ws-menu ws-question-menu"
          side="bottom"
          align="start"
          sideOffset={7}
          collisionPadding={12}
        >
          <Dropdown.RadioGroup value={selected?.id ?? ""} onValueChange={onSelect}>
            {groups.map((group, index) => (
              <Dropdown.RadioItem
                key={group.id}
                value={group.id}
                textValue={`第 ${index + 1} 问 ${group.question}`}
                className="ws-question-option"
              >
                <span className="ws-question-option-copy">
                  <span className="ws-question-option-index">第 {index + 1} 问</span>
                  <span className="ws-question-option-text" title={group.question}>{questionPreview(group.question)}</span>
                </span>
                <span className="ws-question-check" aria-hidden="true">
                  <Dropdown.ItemIndicator forceMount><Check size={15} /></Dropdown.ItemIndicator>
                </span>
              </Dropdown.RadioItem>
            ))}
          </Dropdown.RadioGroup>
        </Dropdown.Content>
      </Dropdown.Portal>
    </Dropdown.Root>
  );
}

export function ResearchPanel({ ws, selectedRunId, onSelect, sourceId, onSource }: {
  ws: Workspace; selectedRunId: string | null; onSelect: (id: string) => void;
  sourceId: string | null; onSource: (id: string | null) => void;
}) {
  const groups = sourceGroups(ws);
  const selected = groups.find(g => g.id === selectedRunId) ?? groups.at(-1);
  return <div className="ws-research-panel">
    <label className="ws-field-label" htmlFor="research-question">按提问查看</label>
    <QuestionSelect
      groups={groups}
      selectedId={selected?.id}
      onSelect={id => { onSelect(id); onSource(null); }}
    />
    {selected && <SourceList key={selected.id} sources={selected.sources} onSource={onSource} />}
    <SourcePreview api={ws.api} scope={ws.scope} sourceId={sourceId} onClose={() => onSource(null)} />
  </div>;
}

const SOURCE_TABS = ["all", "web", "law", "case", "paper"] as const;
type SourceFilterKind = (typeof SOURCE_TABS)[number];

const SOURCE_TAB_LABELS: Record<SourceFilterKind, string> = {
  all: "全部",
  web: "网络资料",
  law: "法规",
  case: "案例",
  paper: "论文资料",
};

const SOURCE_KIND_LABELS: Record<SourceReference["kind"], string> = {
  web: "网络资料",
  law: "法规",
  case: "案例",
  paper: "论文资料",
};

function isHttpUrl(url: string | null | undefined): url is string {
  return typeof url === "string" && /^https?:\/\//i.test(url.trim());
}

function SourceItemRow({
  source,
  dimmed,
  onSource,
}: {
  source: SourceReference;
  dimmed: boolean;
  onSource: (id: string) => void;
}) {
  const cardClass = `ws-research-item${dimmed ? " is-reference-only" : ""}`;
  if (source.kind === "web" && isHttpUrl(source.url)) {
    const domain = source.domain || source.version_label || "网页来源";
    return (
      <li>
        <a
          className={`${cardClass} ws-research-web`}
          href={source.url}
          target="_blank"
          rel="noopener noreferrer"
        >
          <span className="ws-research-kind ws-research-web-head">
            <span className="ws-research-kind-main">
              <Globe size={12} aria-hidden="true" />
              <span>{domain}</span>
              {source.version_date && <span>· {source.version_date}</span>}
              {source.verified_read && <span className="ws-research-verified-badge">已读正文</span>}
              {source.used_in_answer === true && <span className="ws-research-cited-badge">已引用</span>}
            </span>
            <ArrowUpRight size={13} className="ws-web-arrow" aria-hidden="true" />
          </span>
          <strong>{source.title}</strong>
          {source.snippet && <span className="ws-research-snippet">{source.snippet}</span>}
        </a>
      </li>
    );
  }
  return (
    <li>
      <button type="button" className={cardClass} onClick={() => onSource(source.source_id)}>
        <span className="ws-research-kind">
          <span className="ws-research-kind-main">
            <span>{SOURCE_KIND_LABELS[source.kind]}{source.version_label ? ` · ${source.version_label}` : ""}</span>
            {source.used_in_answer === true && <span className="ws-research-cited-badge">已引用</span>}
          </span>
        </span>
        <strong>{source.title}</strong>
        {(source.locator || source.section || source.version_date) && (
          <span className="ws-research-locator">
            {[source.locator, source.section, source.version_date].filter(Boolean).join(" · ")}
          </span>
        )}
      </button>
    </li>
  );
}

function SourceList({ sources, onSource }: { sources: SourceReference[]; onSource: (id: string) => void }) {
  const [search, setSearch] = useState("");
  const [kind, setKind] = useState<SourceFilterKind>("all");
  const [limit, setLimit] = useState(8);
  const needle = search.trim().toLocaleLowerCase();
  const filtered = sources.filter(s =>
    (kind === "all" || s.kind === kind)
    && `${s.title} ${s.domain ?? ""} ${s.url ?? ""} ${s.snippet ?? ""} ${s.authors ?? ""} ${s.collection ?? ""} ${s.locator ?? ""} ${s.section ?? ""} ${s.version_date ?? ""}`
      .toLocaleLowerCase()
      .includes(needle),
  );
  const hasUsageSplit = filtered.some(s => typeof s.used_in_answer === "boolean");
  const ordered = hasUsageSplit
    ? [
        ...filtered.filter(s => s.used_in_answer === true),
        ...filtered.filter(s => s.used_in_answer !== true),
      ]
    : filtered;
  const visible = ordered.slice(0, limit);
  const visibleCited = hasUsageSplit ? visible.filter(s => s.used_in_answer === true) : [];
  const visibleReferenceOnly = hasUsageSplit ? visible.filter(s => s.used_in_answer !== true) : [];
  const totalCited = hasUsageSplit ? filtered.filter(s => s.used_in_answer === true).length : 0;
  const totalReferenceOnly = hasUsageSplit ? filtered.length - totalCited : 0;

  return <>
    <div className="ws-source-filters" aria-label="资料类型">
      {SOURCE_TABS.map(value => <button key={value} type="button" aria-pressed={kind === value} onClick={() => { setKind(value); setLimit(8); }}>
        {SOURCE_TAB_LABELS[value]} <span>{sources.filter(s => value === "all" || s.kind === value).length}</span>
      </button>)}
    </div>
    <label className="ws-search"><Search size={15} /><input type="search" aria-label="搜索本次资料" placeholder="搜索名称、域名或条文" value={search} onChange={event => { setSearch(event.target.value); setLimit(8); }} /></label>
    <div className="ws-research-results">
      {!filtered.length && <p className="ws-panel-empty">{sources.length ? "没有找到匹配的资料" : "本次提问尚无检索资料"}</p>}
      {hasUsageSplit ? (
        <>
          {visibleCited.length > 0 && (
            <div className="ws-research-group">
              <div className="ws-research-group-head is-cited">
                <span>回答已引用</span>
                <small>{totalCited}</small>
              </div>
              <ul className="ws-research-list">
                {visibleCited.map(source => (
                  <SourceItemRow key={source.source_id} source={source} dimmed={false} onSource={onSource} />
                ))}
              </ul>
            </div>
          )}
          {visibleReferenceOnly.length > 0 && (
            <div className="ws-research-group">
              <div className="ws-research-group-head">
                <span>其他检索参考</span>
                <small>{totalReferenceOnly}</small>
              </div>
              <ul className="ws-research-list">
                {visibleReferenceOnly.map(source => (
                  <SourceItemRow key={source.source_id} source={source} dimmed={totalCited > 0} onSource={onSource} />
                ))}
              </ul>
            </div>
          )}
        </>
      ) : (
        <ul className="ws-research-list">
          {visible.map(source => (
            <SourceItemRow key={source.source_id} source={source} dimmed={false} onSource={onSource} />
          ))}
        </ul>
      )}
      {ordered.length > limit && <button type="button" className="ws-source-more" onClick={() => setLimit(n => n + 8)}>查看更多 · 还有 {ordered.length - limit} 份</button>}
    </div>
  </>;
}

