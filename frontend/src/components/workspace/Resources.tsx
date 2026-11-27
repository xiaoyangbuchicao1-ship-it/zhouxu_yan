import { useEffect, useRef, useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import {
  FileText,
  Upload,
  Search,
  Trash2,
  Plus,
  Check,
  X,
  Library,
  ArrowUpRight,
  FolderOpen,
  Maximize2,
  Minimize2,
} from "lucide-react";
import type { useWorkspace } from "@/hooks/use-workspace";
import { isImageAttachment } from "@/lib/image-attachment";
import { materialStatus, type Resource } from "@/lib/workbench-api";
import { Modal, IconButton, Loading, LoadError } from "./Primitives";
import { ActivityIndicator } from "./ActivityIndicator";

type Workspace = ReturnType<typeof useWorkspace>;

export function ResourcePreview({
  resource,
  ws,
  close,
}: {
  resource: Resource | null;
  ws: Workspace;
  close: () => void;
}) {
  const isImage = Boolean(resource && isImageAttachment(resource.filename, resource.content_type));
  const [viewTab, setViewTab] = useState<"image" | "text">("image");
  const [zoomed, setZoomed] = useState(false);
  const [imageUrl, setImageUrl] = useState<string | null>(null);

  useEffect(() => {
    setViewTab(isImage ? "image" : "text");
    setZoomed(false);
  }, [resource?.id, isImage]);

  const imageQuery = useQuery({
    queryKey: [...ws.scope, "material-blob", resource?.id],
    queryFn: ({ signal }) => ws.api.materialBlob(resource!.id, signal),
    enabled: Boolean(resource && isImage),
    retry: false,
  });

  useEffect(() => {
    if (!imageQuery.data) {
      setImageUrl(null);
      return;
    }
    const url = URL.createObjectURL(imageQuery.data);
    setImageUrl(url);
    return () => {
      URL.revokeObjectURL(url);
    };
  }, [imageQuery.data]);

  const passages = useInfiniteQuery({
    queryKey: [...ws.scope, "passages", resource?.id],
    queryFn: ({ signal, pageParam }) => ws.api.passages(resource!.id, signal, pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled: Boolean(resource),
    retry: false,
  });
  const chunks = passages.data?.pages.flatMap((page) => page.chunks) ?? [];
  const notices = [...new Set(passages.data?.pages.flatMap((page) => page.notices ?? []) ?? [])];
  return (
    <Modal
      open={Boolean(resource)}
      onOpenChange={(open) => {
        if (!open) close();
      }}
      title={resource?.filename || "资料内容"}
      description={
        isImage && viewTab === "image"
          ? "正在预览原始图片，也可切换查看解析文本。"
          : "此处展示服务已读取的正文片段，不是原文件预览。"
      }
      side="right"
    >
      {resource && (
        <div className="ws-preview-body">
          <div className="ws-preview-header-row">
            <p className="ws-status-line">
              <FileText size={16} />
              {materialStatus(resource)}
              {resource.issue && ` · ${resource.issue}`}
            </p>
            {isImage && (
              <div className="ws-preview-tabs" role="tablist" aria-label="附件预览方式">
                <button
                  type="button"
                  role="tab"
                  aria-selected={viewTab === "image"}
                  className={viewTab === "image" ? "is-active" : ""}
                  onClick={() => setViewTab("image")}
                >
                  原图预览
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={viewTab === "text"}
                  className={viewTab === "text" ? "is-active" : ""}
                  onClick={() => setViewTab("text")}
                >
                  图片识别文本
                </button>
                {viewTab === "image" && imageUrl && (
                  <button
                    type="button"
                    className="ws-preview-zoom-btn"
                    onClick={() => setZoomed((v) => !v)}
                  >
                    {zoomed ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
                    {zoomed ? "适应" : "原尺寸"}
                  </button>
                )}
              </div>
            )}
          </div>
          {isImage && viewTab === "image" ? (
            imageQuery.isPending ? (
              <Loading label="正在载入原图…" />
            ) : imageQuery.isError ? (
              <LoadError retry={() => void imageQuery.refetch()} />
            ) : imageUrl ? (
              <div className={`ws-image-preview-stage ${zoomed ? "is-zoomed" : ""}`}>
                <img src={imageUrl} alt={resource.filename} />
              </div>
            ) : null
          ) : passages.isPending ? (
            <Loading label="正在载入正文…" />
          ) : passages.isError ? (
            <LoadError retry={() => void passages.refetch()} />
          ) : !chunks.length ? (
            <p className="ws-muted">
              尚未读到可展示的正文。可检查文件内容后重新上传。
            </p>
          ) : (
            chunks.map((passage) => (
              <section className="ws-passage" key={passage.id}>
                {(passage.locator || passage.heading_path) && (
                  <h3>
                    {[
                      passage.locator,
                      Array.isArray(passage.heading_path)
                        ? passage.heading_path.join(" / ")
                        : passage.heading_path,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </h3>
                )}
                <p>{passage.text}</p>
                {passage.enabled === false && (
                  <span className="ws-muted">此段不参与检索</span>
                )}
              </section>
            ))
          )}
          {(!isImage || viewTab === "text") && notices.map((notice) => (
            <p className="ws-muted" key={notice}>{notice}</p>
          ))}
          {(!isImage || viewTab === "text") && passages.hasNextPage && (
            <button
              type="button"
              className="ws-secondary-button"
              disabled={passages.isFetchingNextPage}
              onClick={() => void passages.fetchNextPage()}
            >
              {passages.isFetchingNextPage ? "载入中…" : "加载更多"}
            </button>
          )}
        </div>
      )}
    </Modal>
  );
}

export function LibraryPage({
  ws,
  onPreview,
  onDelete,
}: {
  ws: Workspace;
  onPreview: (r: Resource) => void;
  onDelete: (r: Resource) => void;
}) {
  const [search, setSearch] = useState("");
  const [uploading, setUploading] = useState(false);
  const [failedFiles, setFailedFiles] = useState<File[]>([]);
  const fileInput = useRef<HTMLInputElement>(null);
  const uploadLock = useRef(false);
  const resources = ws.resources.data?.resources || [];
  const filtered = resources.filter((r) =>
    r.filename.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()),
  );
  async function upload(files: File[]) {
    if (uploadLock.current) return;
    uploadLock.current = true;
    setUploading(true);
    const failures: File[] = [];
    for (const file of files) {
      try {
        await ws.api.uploadResource(file);
        await ws.resources.refetch();
      } catch (error) {
        failures.push(file);
        ws.report(error);
      }
    }
    setFailedFiles(failures);
    setUploading(false);
    uploadLock.current = false;
  }
  return (
    <div className="ws-library-page">
      <header className="md-heading">
        <div>
          <h1>资料库</h1>
          <p>上传一次，之后在任何对话里都能选用。</p>
        </div>
        <button
          className="ws-primary-button"
          onClick={() => fileInput.current?.click()}
          disabled={uploading || ws.busy}
        >
          {uploading ? (
            <ActivityIndicator size={17} />
          ) : (
            <Upload size={17} />
          )}
          {uploading ? "正在上传与读取…" : "上传资料"}
        </button>
      </header>
      <input
        ref={fileInput}
        type="file"
        multiple
        className="ws-sr-only"
        tabIndex={-1}
        aria-label="上传资料库文件"
        onChange={(e) => {
          void upload(Array.from(e.target.files || []));
          e.target.value = "";
        }}
      />
      {failedFiles.length > 0 && (
        <div className="ws-interruption" role="alert">
          <p>
            以下文件未完成上传：{failedFiles.map((f) => f.name).join("、")}
            。已完成的文件不会受影响。
          </p>
          <button
            className="ws-text-button"
            onClick={() => void upload(failedFiles)}
            disabled={uploading}
          >
            重试这些文件
          </button>
          <button className="ws-text-button" onClick={() => setFailedFiles([])}>
            移除失败项
          </button>
        </div>
      )}
      <div className="ws-library-toolbar">
        <label className="ws-search">
          <Search size={17} />
          <span className="ws-sr-only">按文件名查找资料</span>
          <input
            type="search"
            placeholder="查找资料…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
        <span className="ws-muted">{resources.length} 份资料</span>
      </div>
      {ws.resources.isPending ? (
        <Loading label="正在载入资料库…" />
      ) : ws.resources.isError ? (
        <LoadError retry={() => void ws.resources.refetch()} />
      ) : !resources.length ? (
        <div className="ws-library-empty">
          <FolderOpen size={34} strokeWidth={1.2} />
          <h2>还没有资料</h2>
          <p>支持法律文本、合同范本和研究材料，单份不超过 30 MB。</p>
          <button
            className="ws-text-button"
            onClick={() => fileInput.current?.click()}
            disabled={uploading}
          >
            上传资料
            <ArrowUpRight size={16} />
          </button>
        </div>
      ) : !filtered.length ? (
        <p className="ws-empty-text">
          没有找到“{search}”相关资料。换个文件名试试。
        </p>
      ) : (
        <ul className="ws-resource-list">
          {filtered.map((resource) => (
            <li key={resource.id}>
              <button
                className="ws-resource-main"
                onClick={() => onPreview(resource)}
              >
                <span className="ws-file-icon">
                  <FileText size={22} strokeWidth={1.4} />
                </span>
                <span>
                  <strong>{resource.filename}</strong>
                  <span className="ws-resource-meta">
                    {materialStatus(resource)}
                    <span>·</span>
                    {resource.conversation_count} 个关联对话
                    {resource.issue && (
                      <span className="ws-resource-issue">
                        {resource.issue}
                      </span>
                    )}
                  </span>
                </span>
              </button>
              <IconButton
                label={`删除 ${resource.filename}`}
                disabled={ws.busy || Boolean(ws.turn)}
                onClick={() => onDelete(resource)}
              >
                <Trash2 size={17} />
              </IconButton>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function MaterialsPanel({
  ws,
  id,
  onPreview,
  goLibrary,
}: {
  ws: Workspace;
  id?: string;
  onPreview: (r: Resource) => void;
  goLibrary: () => void;
}) {
  const [tab, setTab] = useState<"current" | "library">("current");
  const [search, setSearch] = useState("");
  const current = ws.detail.data;
  const selected = current?.linked_resources || [];
  const sent = current?.messages.flatMap((m) => m.materials || []) || [];
  const files = [...(current?.pending_materials || []), ...sent];
  const available = (ws.resources.data?.resources || []).filter((r) =>
    r.filename.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()),
  );
  const receiving = ws.turn?.state === "receiving";
  async function toggleResource(resource: Resource) {
    await ws.act(async () => {
      const conversationId = await ws.ensureConversation();
      await ws.api.link(
        conversationId,
        resource.id,
        selected.some((r) => r.id === resource.id),
      );
        ws.stageMaterial(conversationId, resource, selected.some((r) => r.id === resource.id));
    });
  }
  return (
    <div className="ws-materials-panel">
      <div className="ws-panel-tabs" role="tablist" aria-label="资料范围">
        {(["current", "library"] as const).map((value) => (
          <button
            key={value}
            role="tab"
            id={`tab-${value}`}
            aria-controls={`panel-${value}`}
            aria-selected={tab === value}
            tabIndex={tab === value ? 0 : -1}
            onClick={() => setTab(value)}
            onKeyDown={(e) => {
              if (
                e.key === "ArrowLeft" ||
                e.key === "ArrowRight" ||
                e.key === "Home" ||
                e.key === "End"
              ) {
                e.preventDefault();
                const next =
                  e.key === "Home"
                    ? "current"
                    : e.key === "End"
                      ? "library"
                      : tab === "current"
                        ? "library"
                        : "current";
                setTab(next);
                document.getElementById(`tab-${next}`)?.focus();
              }
            }}
          >
            {tab === value && (
              <motion.span
                className="ws-tab-indicator"
                layoutId="ws-material-tab"
                transition={{ type: "spring", stiffness: 450, damping: 36 }}
              />
            )}
            <span>{value === "current" ? "当前对话" : "从资料库选择"}</span>
          </button>
        ))}
      </div>
      <div
        className="ws-panel-scroll"
        role="tabpanel"
        id={`panel-${tab}`}
        aria-labelledby={`tab-${tab}`}
        tabIndex={0}
      >
        {tab === "current" ? (
          <>
            <section className="ws-panel-section">
              <h3>
                已选资料<span>{selected.length}</span>
              </h3>
              <p>可供本对话使用。</p>
              {selected.length ? (
                <ul className="ws-panel-files">
                  {selected.map((resource) => (
                    <li key={resource.id}>
                      <button onClick={() => onPreview(resource)}>
                        <Library size={17} />
                        <span>
                          <strong>{resource.filename}</strong>
                          <small>{materialStatus(resource)}</small>
                        </span>
                      </button>
                      <IconButton
                        label={`从对话移除 ${resource.filename}`}
                        disabled={ws.busy || receiving}
                        onClick={() => void toggleResource(resource)}
                      >
                        <X size={15} />
                      </IconButton>
                    </li>
                  ))}
                </ul>
              ) : (
                <button
                  className="ws-add-resource"
                  onClick={() => setTab("library")}
                >
                  <Plus size={17} />
                  选择资料
                </button>
              )}
            </section>
            <section className="ws-panel-section">
              <h3>
                对话附件<span>{files.length}</span>
              </h3>
              <p>仅属于这段对话。尚未发送的附件可在输入区移除。</p>
              {files.length ? (
                <ul className="ws-panel-files">
                  {files.map((file) => (
                    <li key={file.id}>
                      <FileText size={17} />
                      <span>
                        <strong>{file.filename}</strong>
                        <small>
                          {materialStatus(file)}
                          {file.issue ? ` · ${file.issue}` : ""}
                        </small>
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="ws-panel-empty">
                  <FileText size={24} strokeWidth={1.2} />
                  <span>还没有添加附件</span>
                </div>
              )}
            </section>
            {!id && (
              <p className="ws-panel-footnote">
                选择资料或上传附件时，将为你建立新对话。
              </p>
            )}
          </>
        ) : (
          <>
            <p className="ws-panel-intro">
              选择与问题相关的资料，为这段对话提供依据。
            </p>
            <label className="ws-search">
              <Search size={16} />
              <span className="ws-sr-only">搜索可选资料</span>
              <input
                type="search"
                placeholder="按文件名查找"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </label>
            {ws.resources.isPending ? (
              <Loading />
            ) : ws.resources.isError ? (
              <LoadError retry={() => void ws.resources.refetch()} />
            ) : !available.length ? (
              <div className="ws-panel-empty">
                <Library size={24} strokeWidth={1.2} />
                <span>{search ? "没有找到相关资料" : "资料库还是空的"}</span>
                <button className="ws-text-button" onClick={goLibrary}>
                  前往资料库
                  <ArrowUpRight size={15} />
                </button>
              </div>
            ) : (
              <ul className="ws-picker-list">
                {available.map((resource) => {
                  const checked = selected.some((r) => r.id === resource.id);
                  return (
                    <li key={resource.id}>
                      <button
                        className={`ws-resource-toggle ${checked ? "is-selected" : ""}`}
                        onClick={() => void toggleResource(resource)}
                        disabled={ws.busy || receiving}
                        aria-pressed={checked}
                      >
                        <span className="ws-checkbox">
                          {checked && <Check size={13} />}
                        </span>
                        <span>
                          <strong>{resource.filename}</strong>
                          <small>{materialStatus(resource)}</small>
                        </span>
                      </button>
                      <IconButton
                        label={`查看 ${resource.filename} 的正文`}
                        onClick={() => onPreview(resource)}
                      >
                        <ArrowUpRight size={15} />
                      </IconButton>
                    </li>
                  );
                })}
              </ul>
            )}
          </>
        )}
      </div>
    </div>
  );
}


