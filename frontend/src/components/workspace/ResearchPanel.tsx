import { useState } from "react";
import * as Dropdown from "@radix-ui/react-dropdown-menu";
import { Check, ChevronDown, Search, ArrowUpRight } from "lucide-react";
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

function SourceList({ sources, onSource }: { sources: SourceReference[]; onSource: (id: string) => void }) {
  const [search, setSearch] = useState("");
  const [kind, setKind] = useState<"all" | "law" | "case" | "paper">("all");
  const [limit, setLimit] = useState(8);
  const filtered = sources.filter(s => (kind === "all" || s.kind === kind) && `${s.title} ${s.authors ?? ""} ${s.collection ?? ""} ${s.locator ?? ""} ${s.section ?? ""} ${s.version_date ?? ""}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  return <>
    <div className="ws-source-filters" aria-label="资料类型">
      {(["all", "law", "case", "paper"] as const).map(value => <button key={value} type="button" aria-pressed={kind === value} onClick={() => { setKind(value); setLimit(8); }}>
        {{ all: "全部", law: "法规", case: "案例", paper: "论文资料" }[value]} <span>{sources.filter(s => value === "all" || s.kind === value).length}</span>
      </button>)}
    </div>
    <label className="ws-search"><Search size={15} /><input type="search" aria-label="搜索本次资料" placeholder="搜索名称或条文" value={search} onChange={event => { setSearch(event.target.value); setLimit(8); }} /></label>
    <div className="ws-research-results">
      {!filtered.length && <p className="ws-panel-empty">{sources.length ? "没有找到匹配的资料" : "本次提问尚无检索资料"}</p>}
      <ul className="ws-research-list">
        {filtered.slice(0, limit).map(source => <li key={source.source_id}><button type="button" onClick={() => onSource(source.source_id)}>
          <span className="ws-research-kind">{{ law: "法规", case: "案例", paper: "论文资料" }[source.kind]}{source.version_label ? ` · ${source.version_label}` : ""}</span>
          <strong>{source.title}</strong>
          {(source.locator || source.section || source.version_date) && <span className="ws-research-locator">{[source.locator, source.section, source.version_date].filter(Boolean).join(" · ")}</span>}
        </button></li>)}
      </ul>
      {filtered.length > limit && <button type="button" className="ws-source-more" onClick={() => setLimit(n => n + 8)}>查看更多 · 还有 {filtered.length - limit} 份</button>}
    </div>
  </>;
}

