import { lazy, Suspense, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ArrowUpRight, BookOpen } from "lucide-react";
import type { workbenchApi } from "@/lib/workbench-api";
import { Modal, Loading, LoadError } from "./Primitives";

const PaperReader = lazy(() => import("./PaperReader"));
type Api = ReturnType<typeof workbenchApi>;

export function SourcePreview({ api, scope, sourceId, onClose }: {
  api: Api; scope: readonly unknown[]; sourceId: string | null; onClose: () => void;
}) {
  return <Modal open={Boolean(sourceId)} onOpenChange={open => { if (!open) onClose(); }} title="资料原文" className="ws-source-preview">
    {sourceId && <SourceContent key={sourceId} api={api} scope={scope} sourceId={sourceId} />}
  </Modal>;
}

function SourceContent({ api, scope, sourceId }: { api: Api; scope: readonly unknown[]; sourceId: string }) {
  const [reading, setReading] = useState(false);
  const source = useQuery({ queryKey: [...scope, "source", sourceId], queryFn: ({ signal }) => api.source(sourceId, signal), retry: false });
  if (source.isPending) return <Loading label="正在读取原文…" />;
  if (source.isError) return <LoadError label={source.error.message} retry={() => void source.refetch()} />;
  const document = source.data;
  return <>
    <h3 className="ws-source-title">{document.title}</h3>
    <p className="ws-muted">{[document.authors ?? document.version_label, document.locator].filter(Boolean).join(" · ")}</p>
    {reading && document.original ? <>
      <button className="ws-text-button" onClick={() => setReading(false)}><ArrowLeft size={14} />返回摘录</button>
      <Suspense fallback={<Loading label="正在打开论文…" />}><PaperReader source={document} api={api} /></Suspense>
    </> : <>
      <pre className="ws-source-text">{document.text}</pre>
      {document.original && <button className="ws-text-button ws-paper-open" onClick={() => setReading(true)}><BookOpen size={15} />查看原文</button>}
      {document.source_url && <a className="ws-text-button" href={document.source_url} target="_blank" rel="noopener noreferrer">打开发布页 <ArrowUpRight size={14} /></a>}
    </>}
  </>;
}
