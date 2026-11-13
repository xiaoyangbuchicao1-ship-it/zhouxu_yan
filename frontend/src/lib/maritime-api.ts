import { WorkbenchError } from "./workbench-api";

const REQUEST_TIMEOUT_MS = 600_000;

export interface MaritimeSkill {
  id: string;
  name: string;
  summary: string;
  instructions: string;
  category?: string;
  version: number;
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
}
export interface WorkPackage {
  id: string;
  run_id: string;
  skill_id: string;
  skill_name: string;
  method_version: number;
  title: string;
  created_at: string;
  sections: {
    key: string;
    name: string;
    columns: string[];
    rows: Record<string, string>[];
  }[];
  notes: string[];
  checks: {
    structure: string;
    arithmetic?: string;
    professional_review: string;
    totals?: Record<string, string>;
  };
}
export interface TaskFile { id: string; name: string; size: number; sha256: string; }
export interface CaseEntry {
  id: string;
  case_id: string;
  kind: "task" | "evidence" | "document";
  title: string;
  content: string;
  due_at: string | null;
  assignee_id: string | null;
  status: "open" | "done" | "draft" | "reviewed";
  version: number;
  author_id: string;
  created_at: string;
  updated_at: string;
}
export interface MaritimeCase {
  id: string;
  title: string;
  summary: string;
  status: string;
  role: string;
  updated_at: string;
  members: {
    account_id: string;
    role: string;
    display_name?: string;
    email?: string;
  }[];
  entries: CaseEntry[];
  audit: { id: string; action: string; actor_id: string; created_at: string }[];
}
export interface CorpusSource {
  id: string;
  kind: "laws" | "cases";
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
          ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        },
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
      const value = await response.json().catch(() => null);
      if (!response.ok)
        throw new WorkbenchError(
          response.status === 401
            ? "登录已失效，请重新登录。"
            : typeof value?.detail === "string"
              ? value.detail
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
      call<{ packages: WorkPackage[]; files?: TaskFile[] }>(
        `/runs/${encodeURIComponent(runId)}/deliverables?conversation_id=${encodeURIComponent(conversationId)}`,
      ),
    downloadFile: async (runId: string, conversationId: string, file: TaskFile) => {
      const response = await fetch(`${base}/maritime/runs/${encodeURIComponent(runId)}/files/${encodeURIComponent(file.id)}?conversation_id=${encodeURIComponent(conversationId)}`, {cache:'no-store',credentials:'omit',headers:{Authorization:`Bearer ${token}`}});
      if (!response.ok) throw new WorkbenchError('文件下载失败，请确认登录状态后重试。',response.status);
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a'); link.href=url; link.download=file.name; link.click();
      setTimeout(()=>URL.revokeObjectURL(url),1000);
    },
    previewFile: async (runId: string, conversationId: string, file: TaskFile) => {
      const response = await fetch(`${base}/maritime/runs/${encodeURIComponent(runId)}/files/${encodeURIComponent(file.id)}/preview?conversation_id=${encodeURIComponent(conversationId)}`, {cache:'no-store',credentials:'omit',headers:{Authorization:`Bearer ${token}`}});
      if (!response.ok) {
        const detail = await response.json().catch(() => null);
        throw new WorkbenchError(
          response.status === 503
            ? '在线预览暂不可用，请先下载文件。'
            : typeof detail?.detail === 'string'
              ? detail.detail
              : '文件预览失败，请重试。',
          response.status,
        );
      }
      return URL.createObjectURL(await response.blob());
    },
    skills: () => call<{ skills: MaritimeSkill[] }>("/skills"),
    saveSkill: (
      input: Partial<MaritimeSkill> & { expected_version?: number },
      id?: string,
    ) =>
      call<MaritimeSkill>(
        `/skills${id ? `/${encodeURIComponent(id)}` : ""}`,
        id ? "PUT" : "POST",
        input,
      ),
    cases: () => call<{ cases: MaritimeCase[] }>("/cases"),
    createCase: (title: string, summary: string) =>
      call<MaritimeCase>("/cases", "POST", { title, summary }),
    case: (id: string) =>
      call<MaritimeCase>(`/cases/${encodeURIComponent(id)}`),
    invite: (id: string, email: string, role: string) =>
      call<MaritimeCase>(`/cases/${encodeURIComponent(id)}/members`, "POST", {
        email,
        role,
      }),
    removeMember: (id: string, memberId: string) =>
      call<MaritimeCase>(
        `/cases/${encodeURIComponent(id)}/members/${encodeURIComponent(memberId)}`,
        "DELETE",
      ),
    saveEntry: (
      id: string,
      body: Partial<CaseEntry> & { expected_version?: number },
      entryId?: string,
    ) =>
      call<CaseEntry>(
        `/cases/${encodeURIComponent(id)}/entries${entryId ? `/${encodeURIComponent(entryId)}` : ""}`,
        entryId ? "PUT" : "POST",
        body,
      ),
    versions: (caseId: string, entryId: string) =>
      call<{ versions: CaseEntry[] }>(
        `/cases/${encodeURIComponent(caseId)}/entries/${encodeURIComponent(entryId)}/versions`,
      ),
    search: (query: string, kind: "laws" | "cases", current_only: boolean) =>
      call<SearchResult>("/search", "POST", {
        query,
        kind,
        current_only,
        limit: 8,
      }),
    source: (kind: string, id: string, snapshot?: string) =>
      call<CorpusSource>(
        `/sources/${kind}/${encodeURIComponent(id)}${snapshot ? `?snapshot=${encodeURIComponent(snapshot)}` : ""}`,
      ),
    status: () =>
      call<{
        model_runtime_ready: boolean;
        corpus: { indexed: boolean; counts: Record<string, number> };
      }>("/status"),
  };
}

export function downloadDraft(title: string, content: string) {
  const text = `# ${title}\n\n> 工作草稿 · 请由海事专业人员复核；导出不表示发送或提交。\n\n${content}\n`;
  const url = URL.createObjectURL(
    new Blob([text], { type: "text/markdown;charset=utf-8" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = `${title.replace(/[<>:"/\\|?*]/g, "_").slice(0, 80)}.md`;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
