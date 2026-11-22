import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Download, Minus, Plus } from "lucide-react";
import { getDocument, GlobalWorkerOptions, type PDFDocumentProxy, type RenderTask } from "pdfjs-dist";
import type { SourceDocument, workbenchApi } from "@/lib/workbench-api";
import { IconButton, Loading, LoadError } from "./Primitives";

GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();

export default function PaperReader({ source, api }: { source: SourceDocument; api: ReturnType<typeof workbenchApi> }) {
  const original = source.original!;
  const [page, setPage] = useState(original.page ?? 1);
  const [zoom, setZoom] = useState(1);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [error, setError] = useState("");
  const [rendering, setRendering] = useState(true);
  const hasPainted = useRef(false);
  const canvas = useRef<HTMLCanvasElement>(null);
  const container = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  const file = useQuery({ queryKey: ["paper-file", source.source_id, original.sha256], queryFn: ({ signal }) => api.downloadPaper(source.source_id, original.sha256, signal), retry: false, gcTime: 0 });

  useEffect(() => {
    const observer = new ResizeObserver(entries => {
      const next = Math.round(entries[0].contentRect.width);
      setWidth(previous => Math.abs(previous - next) < 1 ? previous : next);
    });
    observer.observe(container.current!);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!file.data) return;
    let active = true;
    let document: ReturnType<typeof getDocument> | undefined;
    setError("");
    void file.data.arrayBuffer().then(data => {
      if (!active) return;
      const assets = `${import.meta.env.BASE_URL}pdfjs/`;
      document = getDocument({ data, cMapUrl: assets + "cmaps/", cMapPacked: true, standardFontDataUrl: assets + "standard_fonts/", wasmUrl: assets + "wasm/" });
      return document.promise.then(value => { if (active) setPdf(value); });
    }).catch(reason => { if (active) setError(`论文打开失败：${reason.message}`); });
    return () => { active = false; if (document) void document.destroy(); };
  }, [file.data]);

  useEffect(() => {
    if (!pdf || !canvas.current) return;
    let active = true;
    let task: RenderTask | undefined;
    setRendering(true);
    setError("");
    void pdf.getPage(page).then(value => {
      if (!active) return;
      const initial = value.getViewport({ scale: 1 });
      const viewport = value.getViewport({ scale: Math.max(100, width - 24) / initial.width * zoom });
      const target = canvas.current!;
      const ratio = window.devicePixelRatio || 1;
      const staging = document.createElement("canvas");
      staging.width = Math.floor(viewport.width * ratio);
      staging.height = Math.floor(viewport.height * ratio);
      const stagingContext = staging.getContext("2d");
      if (!stagingContext) throw new Error("无法创建论文页面画布");
      task = value.render({ canvas: staging, canvasContext: stagingContext, viewport, transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0] });
      return task.promise.then(() => {
        if (!active) return;
        const targetContext = target.getContext("2d");
        if (!targetContext) throw new Error("无法显示论文页面");
        target.width = staging.width;
        target.height = staging.height;
        target.style.width = `${viewport.width}px`;
        target.style.height = `${viewport.height}px`;
        targetContext.drawImage(staging, 0, 0);
        hasPainted.current = true;
      });
    }).then(() => { if (active) setRendering(false); }).catch(reason => { if (active) { setError(`本页打开失败：${reason.message}`); setRendering(false); } });
    return () => { active = false; task?.cancel(); };
  }, [pdf, page, width, zoom]);

  function download() {
    const url = URL.createObjectURL(file.data!);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${source.title}.pdf`;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <section className="ws-paper-reader" aria-label="论文阅读器">
    <div className="ws-paper-toolbar">
      <div className="ws-paper-pagination">
        <IconButton label="上一页" disabled={page <= 1} onClick={() => setPage(page - 1)}><ChevronLeft size={16} /></IconButton>
        <label><input aria-label="论文页码" type="number" min={1} max={original.page_count} value={page} onChange={event => { const next = Number(event.target.value); if (Number.isInteger(next) && next >= 1 && next <= original.page_count) setPage(next); }} /><span>/ {original.page_count}</span></label>
        <IconButton label="下一页" disabled={page >= original.page_count} onClick={() => setPage(page + 1)}><ChevronRight size={16} /></IconButton>
      </div>
      <div className="ws-paper-zoom">
        <IconButton label="缩小" disabled={zoom <= 0.75} onClick={() => setZoom(zoom - 0.25)}><Minus size={15} /></IconButton>
        <span>{Math.round(zoom * 100)}%</span>
        <IconButton label="放大" disabled={zoom >= 2} onClick={() => setZoom(zoom + 0.25)}><Plus size={15} /></IconButton>
      </div>
      <button className="ws-text-button" disabled={!file.data} onClick={download}><Download size={15} />下载原文</button>
    </div>
    <div className="ws-paper-page" ref={container} aria-busy={rendering}>
      {file.isError ? <LoadError label={file.error.message} retry={() => void file.refetch()} /> : error ? <p role="alert">{error}</p> : <>
        {rendering && !hasPainted.current && <Loading label="正在读取页面…" />}
        <canvas ref={canvas} aria-label={`论文第 ${page} 页`} />
      </>}
    </div>
  </section>;
}
