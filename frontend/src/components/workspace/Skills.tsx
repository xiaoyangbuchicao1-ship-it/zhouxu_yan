import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowUpRight,
  ChevronRight,
  Compass,
  FileArchive,
  FileText,
  LoaderCircle,
  Sparkles,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { maritimeApi, type MaritimeSkill, type SkillResource } from "@/lib/maritime-api";
import { skillDisplayName, splitSkillsByReadiness } from "@/lib/workspace-commands";
import { Loading, Modal } from "./Primitives";

type Api = ReturnType<typeof maritimeApi>;

type Props = {
  api: Api;
  accountId: string;
  onError: (text: string) => void;
  error?: string;
};

const MAX_PACKAGE_BYTES = 20 * 1024 * 1024;

function errorText(error: unknown) {
  return error instanceof Error ? error.message : "技能包没有导入，请重试。";
}

function packageSize(size: number) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function SkillCard({
  skill,
  onOpen,
  onUse,
  canInspect = false,
  comingSoon = false,
}: {
  skill: MaritimeSkill;
  onOpen?: () => void;
  onUse?: () => void;
  canInspect?: boolean;
  comingSoon?: boolean;
}) {
  const files = skill.package?.files.length ?? 0;
  const title = skillDisplayName(skill);
  const modeScope = skill.mode_scope || "work";
  const tagText = skill.deliverable_tag || (skill.package ? "" : modeScope === "chat" ? "只读检索" : "工作模式");
  const body = (
    <>
      <span className="md-skill-number" aria-hidden="true">
        {skill.package ? <FileArchive size={21} strokeWidth={1.6} /> : <Sparkles size={20} strokeWidth={1.6} />}
      </span>
      <span className="md-skill-copy">
        <span className="md-row">
          <strong>{title}</strong>
          {skill.package && <span className="md-tag">ZIP · {files} 个文件</span>}
          {!comingSoon && !skill.package && tagText && <span className="md-tag">{tagText}</span>}
          {comingSoon && <span className="md-skill-soon-badge">持续推进中</span>}
        </span>
        <span className="md-skill-summary">{skill.summary}</span>
      </span>
      {!comingSoon && <ChevronRight className="md-skill-chevron" size={17} aria-hidden="true" />}
    </>
  );

  return (
    <article className={`md-skill md-skill-package ${comingSoon ? "md-skill-coming" : ""}`}>
      {comingSoon ? (
        <div className="md-skill-content md-skill-content-static" aria-disabled="true">
          {body}
        </div>
      ) : (
        <button type="button" className="md-skill-content" onClick={canInspect && onOpen ? onOpen : onUse}>
          {body}
        </button>
      )}
      {!comingSoon && (
        <div className="md-skill-actions">
          {onUse && (
            <button type="button" className="ws-text-button" onClick={onUse}>
              {modeScope === "chat" ? "在聊天模式使用" : "在工作模式使用"}
              <ArrowUpRight size={15} />
            </button>
          )}
          {canInspect && onOpen && (
            <button type="button" className="ws-text-button" onClick={onOpen}>
              查看包文件
            </button>
          )}
        </div>
      )}
    </article>
  );
}

export default function Skills({ api, accountId, onError, error }: Props) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const query = useQuery({
    queryKey: ["maritime", accountId, "skills"],
    queryFn: api.skills,
  });
  const [selected, setSelected] = useState<MaritimeSkill | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [resource, setResource] = useState<SkillResource | null>(null);
  const [resourceBusy, setResourceBusy] = useState(false);
  const [uploadBusy, setUploadBusy] = useState(false);
  const [removing, setRemoving] = useState(false);

  function openDetails(skill: MaritimeSkill) {
    setSelected(skill);
    setResource(null);
    onError("");
    setDetailOpen(true);
  }

  async function upload(file: File) {
    if (!file.name.toLowerCase().endsWith(".zip")) {
      onError("请选择 ZIP 技能包。");
      return;
    }
    if (file.size > MAX_PACKAGE_BYTES) {
      onError("技能包不能超过 20 MB。");
      return;
    }
    setUploadBusy(true);
    onError("");
    try {
      const imported = await api.importSkill(file);
      await client.invalidateQueries({ queryKey: ["maritime", accountId, "skills"] });
      openDetails(imported);
    } catch (cause) {
      onError(errorText(cause));
    } finally {
      setUploadBusy(false);
    }
  }

  async function removeSelected() {
    if (!selected?.package || selected.owner_id !== accountId) return;
    setRemoving(true);
    onError("");
    try {
      await api.removeSkill(selected.id);
      setDetailOpen(false);
      await client.invalidateQueries({ queryKey: ["maritime", accountId, "skills"] });
    } catch (cause) {
      onError(errorText(cause));
    } finally {
      setRemoving(false);
    }
  }

  async function openResource(skill: MaritimeSkill, path: string) {
    setResourceBusy(true);
    onError("");
    try {
      setResource(await api.skillResource(skill.id, path, skill.version));
    } catch (cause) {
      onError(errorText(cause));
    } finally {
      setResourceBusy(false);
    }
  }

  const skills = query.data?.skills ?? [];
  const { ready, comingSoon, mine } = splitSkillsByReadiness(skills, accountId);

  function startSkill(skill: MaritimeSkill) {
    const targetMode = skill.mode_scope === "chat" ? "chat" : "work";
    navigate(`/workspace?skill=${encodeURIComponent(skill.id)}&mode=${targetMode}`);
  }

  return (
    <>
      <div className="md-heading md-skills-heading">
        <div>
          <h1>技能</h1>
          <p>选择内置技能，或上传自己的 ZIP 技能包。</p>
        </div>
        <div className="md-skill-upload">
          <input
            ref={fileInput}
            type="file"
            accept=".zip,application/zip"
            hidden
            onChange={(event) => {
              const file = event.currentTarget.files?.[0];
              event.currentTarget.value = "";
              if (file) void upload(file);
            }}
          />
          <button
            type="button"
            className="ws-primary-button"
            disabled={uploadBusy}
            onClick={() => fileInput.current?.click()}
          >
            {uploadBusy ? <LoaderCircle className="md-spin" size={16} /> : <Upload size={16} />}
            {uploadBusy ? "导入中…" : "上传 ZIP"}
          </button>
          <span className="md-upload-limit">最大 20 MB</span>
        </div>
      </div>
      {error && !detailOpen && (
        <div className="md-error md-skill-error" role="alert">
          {error}
          <button type="button" onClick={() => onError("")} aria-label="关闭提示">
            <X size={16} />
          </button>
        </div>
      )}
      {query.isPending ? (
        <Loading />
      ) : query.isError ? (
        <div className="md-empty">
          <p>技能暂时无法载入。</p>
          <button type="button" className="ws-text-button" onClick={() => void query.refetch()}>
            重新载入
          </button>
        </div>
      ) : (
        <div className="md-skill-groups">
          <section className="md-skill-group" aria-labelledby="builtin-skills">
            <div className="md-skill-group-heading">
              <div>
                <h2 id="builtin-skills">已开放使用</h2>
              </div>
              <span className="md-group-count">{ready.length}</span>
            </div>
            {ready.length ? (
              <div className="md-skill-list">
                {ready.map((skill) => (
                  <SkillCard
                    key={skill.id}
                    skill={skill}
                    onUse={() => startSkill(skill)}
                  />
                ))}
              </div>
            ) : (
              <p className="md-group-empty">暂时没有已开放的内置技能。</p>
            )}
          </section>

          {comingSoon.length > 0 && (
            <section className="md-skill-group" aria-labelledby="coming-skills">
              <div className="md-skill-group-heading">
                <div>
                  <h2 id="coming-skills">持续推进中</h2>
                </div>
                <span className="md-group-count">{comingSoon.length}</span>
              </div>
              <div className="md-skill-list md-skill-coming-list">
                {comingSoon.map((skill) => (
                  <SkillCard key={skill.id} skill={skill} comingSoon />
                ))}
              </div>
            </section>
          )}

          <section className="md-skill-group" aria-labelledby="my-skills">
            <div className="md-skill-group-heading">
              <div>
                <h2 id="my-skills">我的技能</h2>
              </div>
              <span className="md-group-count">{mine.length}</span>
            </div>
            {mine.length ? (
              <div className="md-skill-list">
                {mine.map((skill) => (
                  <SkillCard
                    key={skill.id}
                    skill={skill}
                    canInspect
                    onOpen={() => openDetails(skill)}
                    onUse={() => startSkill(skill)}
                  />
                ))}
              </div>
            ) : (
              <div className="md-skill-empty">
                <span className="md-empty-mark" aria-hidden="true"><Compass size={22} /></span>
                <p>上传一个 ZIP 技能包，它会出现在这里。</p>
                <button type="button" className="ws-secondary-button" onClick={() => fileInput.current?.click()}>
                  <Upload size={15} /> 上传 ZIP
                </button>
              </div>
            )}
          </section>
        </div>
      )}
      <Modal
        open={detailOpen}
        onOpenChange={setDetailOpen}
        title={selected ? skillDisplayName(selected) : "技能包"}
      >
        {selected && selected.package && (
          <div className="md-skill-detail">
            <section>
              <span className="md-detail-label">用途</span>
              <p>{selected.purpose ?? selected.summary}</p>
            </section>
            <section>
              <span className="md-detail-label">运行要求</span>
              <p>{selected.package.compatibility || "工作模式"}</p>
            </section>
            <section>
              <div className="md-detail-section-heading">
                <span className="md-detail-label">包文件</span>
                <span className="md-tag">{selected.package.files.length} 个文件</span>
              </div>
              <ul className="md-skill-files">
                {selected.package.files.map((file) => (
                  <li key={file.path}>
                    <FileText size={15} aria-hidden="true" />
                    <span title={file.path}>{file.path}</span>
                    <small>{packageSize(file.size_bytes)}</small>
                    <button type="button" className="ws-text-button" disabled={resourceBusy} onClick={() => void openResource(selected, file.path)}>
                      查看
                    </button>
                  </li>
                ))}
              </ul>
            </section>
            {resource && (
              <section className="md-skill-resource">
                <div className="md-detail-section-heading">
                  <span className="md-detail-label">{resource.path}</span>
                  <button type="button" className="ws-text-button" onClick={() => setResource(null)}>收起</button>
                </div>
                <pre>{resource.content === null ? "这个文件没有可显示的文字内容。" : resource.content}</pre>
              </section>
            )}
            <div className="md-detail-actions">
              <button type="button" className="ws-primary-button" onClick={() => startSkill(selected)}>
                {(selected.mode_scope || "work") === "chat" ? "在聊天模式使用" : "在工作模式使用"} <ArrowUpRight size={15} />
              </button>
              {selected.owner_id === accountId && (
                <button type="button" className="ws-text-button md-danger-button" disabled={removing} onClick={() => void removeSelected()}>
                  <Trash2 size={15} /> {removing ? "移除中…" : "移除技能包"}
                </button>
              )}
            </div>
            {error && <p className="md-form-error" role="alert">{error}</p>}
          </div>
        )}
      </Modal>
    </>
  );
}
