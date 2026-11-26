import { WorkbenchError } from "./workbench-api";

const REQUEST_TIMEOUT_MS = 600_000;

export interface MaritimeSkill {
  id: string;
  name: string;
  display_name?: string;
  starter_prompt?: string;
  status?: "ready";
  mode_scope?: "chat" | "work";
  deliverable_tag?: string;
  summary: string;
  instructions: string;
  category?: string;
  version: number | string;
  owner_id: string | null;
  visibility: "builtin" | "private" | "community";
  parent_id?: string;
  purpose?: string;
  inputs?: string[];
  steps?: [string, string][];
  checks?: string[];
  branches?: string[];
  examples?: string[];
  deliverables?: { key: string; name: string; columns: string[] }[];
  package?: {
    sha256: string;
    name: string;
    compatibility: string | null;
    files: { path: string; sha256: string; size_bytes: number }[];
  };
}
export interface SkillResource {
  path: string;
  content: string | null;
  sha256: string;
  size_bytes: number;
}
export interface TaskFile { id: string; name: string; size: number; sha256: string; preview_artifact_id?: string | null; }
export interface CorpusSource {
  id: string;
  kind: "laws" | "cases" | "information_cards";
  source_id?: string;
  title: string;
  text: string;
  excerpt?: string;
  locator?: string | string[];
  current: boolean | null;
  version: string | null;
  legal_status?: string;
  case_id?: string;
  source_path?: string;
  version_relation?: string;
  document_complete?: boolean;
}
export interface SearchResult {
  items: CorpusSource[];
}

const MARITIME_CODES: Record<string, string> = {
  skill_not_permitted: "这个账户不能使用该技能。",
  skill_upload_forbidden: "这个账户没有上传技能的权限。",
  run_not_found: "没有找到这次任务。",
  file_not_found: "这个文件已不存在。",
  invalid_skill_id: "技能编号不正确。",
  source_not_found: "没有找到这条资料。",
  paper_reference_invalid: "有一条论文引用无法打开，请重新检索。",
};

/* 后端给的是中文句子就原样显示；是内部代码就翻成中文，翻不出就用通用提示。 */
function maritimeErrorText(detail: string): string {
  if (MARITIME_CODES[detail]) return MARITIME_CODES[detail];
  return /^[a-z0-9_]+$/.test(detail) ? "这次操作没有完成，请稍后重试。" : detail;
}

export function maritimeApi(token: string) {
  const base = (import.meta.env.VITE_API_BASE_URL || "/api").replace(/\/$/, "");
  async function call<T>(
    path: string,
    method = "GET",
    body?: unknown,
  ): Promise<T> {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const response = await fetch(`${base}/maritime${path}`, {
        method,
        signal: controller.signal,
        cache: "no-store",
        credentials: "omit",
        headers: {
          Authorization: `Bearer ${token}`,
          ...(body !== undefined && !(body instanceof FormData)
            ? { "Content-Type": "application/json" }
            : {}),
        },
        body: body instanceof FormData ? body : body !== undefined ? JSON.stringify(body) : undefined,
      });
      const value = await response.json().catch(() => null);
      if (!response.ok)
        throw new WorkbenchError(
          response.status === 401
            ? "登录已失效，请重新登录。"
            : typeof value?.detail === "string"
              ? maritimeErrorText(value.detail)
              : "输入未通过校验，或服务暂不可用。",
          response.status,
        );
      return value as T;
    } catch (error) {
      if (error instanceof WorkbenchError) throw error;
      throw new WorkbenchError(
        controller.signal.aborted
          ? "请求超时，请核对最新状态后再操作。"
          : "连接未完成，请检查服务后重试。",
      );
    } finally {
      clearTimeout(timeout);
    }
  }
  return {
    deliverables: (runId: string, conversationId: string) =>
      call<{ files: TaskFile[] }>(
        `/runs/${encodeURIComponent(runId)}/deliverables?conversation_id=${encodeURIComponent(conversationId)}`,
      ),
    downloadFile: async (runId: string, conversationId: string, file: TaskFile) => {
      const response = await fetch(`${base}/maritime/runs/${encodeURIComponent(runId)}/files/${encodeURIComponent(file.id)}?conversation_id=${encodeURIComponent(conversationId)}`, {cache:'no-store',credentials:'omit',headers:{Authorization:`Bearer ${token}`}});
      if (!response.ok) throw new WorkbenchError(response.status === 404 ? '这个文件已不存在。' : response.status === 401 ? '登录已失效，请重新登录。' : '文件下载失败，请稍后重试。',response.status);
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a'); link.href=url; link.download=file.name; link.click();
      setTimeout(()=>URL.revokeObjectURL(url),1000);
    },
    previewFile: async (runId: string, conversationId: string, file: TaskFile) => {
      const response = await fetch(`${base}/maritime/runs/${encodeURIComponent(runId)}/files/${encodeURIComponent(file.id)}/preview?conversation_id=${encodeURIComponent(conversationId)}`, {cache:'no-store',credentials:'omit',headers:{Authorization:`Bearer ${token}`}});
      if (!response.ok) {
        await response.json().catch(() => null);
        throw new WorkbenchError(
          response.status === 415
            ? '这种文件不能在线预览，请直接下载。'
            : response.status === 404
              ? '这个文件已不存在。'
              : '文件预览失败，请先下载文件。',
          response.status,
        );
      }
      return URL.createObjectURL(await response.blob());
    },
    skills: () => call<{ skills: MaritimeSkill[]; can_upload_skill?: boolean }>("/skills"),
    importSkill: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return call<MaritimeSkill>("/skills/import", "POST", form);
    },
    removeSkill: (id: string) =>
      call<{ status: "removed" }>(`/skills/${encodeURIComponent(id)}`, "DELETE"),
    skillResource: (id: string, path: string, version: number | string) =>
      call<SkillResource>(`/skills/${encodeURIComponent(id)}/resources?${new URLSearchParams({ path, version: String(version) })}`),
    search: (query: string, kind: "laws" | "cases" | "information_cards", current_only: boolean) =>
      call<SearchResult>("/search", "POST", {
        query,
        kind,
        current_only,
        limit: 8,
      }),
  };
}
