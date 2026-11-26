import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Download,
  Eye,
  FileSpreadsheet,
  FileText,
  LoaderCircle,
} from "lucide-react";
import { useAuthSession } from "@/lib/auth-session";
import { maritimeApi } from "@/lib/maritime-api";
import { Modal } from "./Primitives";

export function DeliveryFiles({ runId }: { runId?: string }) {
  const session = useAuthSession();
  const location = useLocation();
  const conversationId = location.pathname.match(
    /\/workspace\/chat\/([^/]+)/,
  )?.[1];
  const api = useMemo(
    () => (session ? maritimeApi(session.session.token) : null),
    [session],
  );
  const query = useQuery({
    queryKey: ["maritime", session?.account.id, "deliverables", runId],
    queryFn: () => api!.deliverables(runId!, conversationId!),
    enabled: !!(api && runId && conversationId),
    retry: false,
  });
  const files = query.data?.files ?? [];
  const [downloadError, setDownloadError] = useState('');
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const [previewFile, setPreviewFile] = useState<(typeof files)[number] | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState('');
  const [previewLoading, setPreviewLoading] = useState(false);
  const previewSequence = useRef(0);
  useEffect(() => () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
  }, [previewUrl]);
  const downloadFile = async (file: (typeof files)[number]) => {
    setDownloadError('');
    setDownloadingId(file.id);
    try {
      await api!.downloadFile(runId!, conversationId!, file);
    } catch (error) {
      setDownloadError(error instanceof Error ? error.message : '文件下载失败，请重试。');
    } finally {
      setDownloadingId(null);
    }
  };
  const closePreview = () => {
    previewSequence.current += 1;
    setPreviewFile(null);
    setPreviewUrl(null);
    setPreviewError('');
    setPreviewLoading(false);
  };
  const openPreview = async (file: (typeof files)[number]) => {
    const sequence = ++previewSequence.current;
    setPreviewFile(file);
    setPreviewUrl(null);
    setPreviewError('');
    setPreviewLoading(true);
    try {
      const url = await api!.previewFile(runId!, conversationId!, file);
      if (sequence !== previewSequence.current) {
        URL.revokeObjectURL(url);
        return;
      }
      setPreviewUrl(url);
    } catch (error) {
      if (sequence === previewSequence.current) {
        setPreviewError(error instanceof Error ? error.message : '在线预览失败，请先下载文件。');
      }
    } finally {
      if (sequence === previewSequence.current) setPreviewLoading(false);
    }
  };
  if (query.isError)
    return (
      <button className="ws-text-button" onClick={() => void query.refetch()}>
        重新载入交付文件
      </button>
    );
  if (!files.length) return null;
  return (
    <>
      <section className="md-delivery" aria-label="本次交付">
        <header className="md-delivery-header">
          <div>
            <span className="md-delivery-kicker">本次交付</span>
            <h3>交付文件</h3>
          </div>
          <span className="md-delivery-count">{files.length} 项</span>
        </header>
        <div className="md-work-packages">
        {files.map(file=>{
          const FileIcon = file.name.toLowerCase().endsWith('.xlsx') ? FileSpreadsheet : FileText;
          const isDownloading = downloadingId === file.id;
          const extension = file.name.split('.').pop()?.toLowerCase() || '';
          const fileType = ({xlsx: 'Excel', docx: 'Word', csv: 'CSV', pdf: 'PDF', txt: '文本', md: 'Markdown', json: 'JSON'} as Record<string, string>)[extension] || '文件';
           const isPreviewable = extension === 'pdf' || Boolean(file.preview_artifact_id);
          return <div key={file.id} className="md-work-package md-file-package" role="group" aria-label={`交付文件 ${file.name}`}>
            <span className="md-package-icon"><FileIcon size={21}/></span>
            <span className="md-package-body"><strong>{file.name}</strong><small title={`SHA256 ${file.sha256}`}>{fileType} · {Math.ceil(file.size/1024)} KB · SHA256 {file.sha256.slice(0, 10)}…</small></span>
            <span className="md-file-status">已保存</span>
            {isPreviewable && <button className="md-file-action" type="button" onClick={()=>void openPreview(file)} disabled={previewLoading && previewFile?.id === file.id} aria-label={`预览 ${file.name}`}>
                {previewLoading && previewFile?.id === file.id ? <LoaderCircle className="md-download-spinner" size={15} aria-hidden="true" /> : <Eye size={15} aria-hidden="true" />}
                <span>预览</span>
              </button>}
            <button className="md-file-action" type="button" disabled={isDownloading} aria-label={`${isDownloading ? '正在下载' : '下载'} ${file.name}`} onClick={()=>void downloadFile(file)}>
              {isDownloading ? <LoaderCircle className="md-download-spinner" size={15} aria-hidden="true" /> : <Download size={15} aria-hidden="true" />}
              <span>{isDownloading ? '下载中…' : '下载'}</span>
            </button>
          </div>;
        })}
        {downloadError && <p className="md-delivery-error" role="alert">{downloadError}</p>}
        </div>
      </section>
      <Modal
        open={!!previewFile}
        onOpenChange={(open) => {
          if (!open) closePreview();
        }}
        title={previewFile?.name || "文件预览"}
         description={undefined}
      >
        <div className="md-office-preview" aria-live="polite">
          {previewLoading && <div className="md-office-preview-state"><LoaderCircle className="md-download-spinner" size={20} aria-hidden="true" /><span>正在准备预览…</span></div>}
          {previewError && <div className="md-office-preview-state is-error"><p>{previewError}</p><button className="ws-secondary-button" type="button" onClick={() => previewFile && void downloadFile(previewFile)}>下载文件</button></div>}
          {previewUrl && <iframe className="md-office-preview-frame" src={previewUrl} title={`${previewFile?.name || '交付文件'} 分页预览`} />}
        </div>
      </Modal>
    </>
  );
}
