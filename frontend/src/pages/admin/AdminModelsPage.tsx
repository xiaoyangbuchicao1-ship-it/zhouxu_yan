import {
  CheckCircle2,
  CircleAlert,
  Cloud,
  Cpu,
  Download,
  Eye,
  EyeOff,
  FlaskConical,
  Link2,
  LoaderCircle,
  Plus,
  Save,
  Server,
  Star,
  Trash2,
} from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  adminErrorMessage,
  createAdminApi,
  type AdminConnectivityTestResult,
  type AdminDiscoveredModel,
  type AdminModelProfile,
  type AdminModelProvider,
  type AdminProviderDiscoverResult,
  type ModelProtocol,
  type ThinkingMode,
} from "@/lib/admin-api";
import { InitialLoading, InlineError, LoadError, PageHeader, RefreshButton } from "./AdminShared";
import { useAdminOutlet } from "./AdminLayout";

type ApiEcosystem = "openai" | "anthropic" | "gemini";
type OpenAISubMode = "openai_chat_completions" | "openai_responses";

interface ProviderTemplate {
  key: string;
  label: string;
  name: string;
  ecosystem: ApiEcosystem;
  openAiMode: OpenAISubMode;
  baseUrl: string;
  noKey?: boolean;
}

const PROVIDER_TEMPLATES: ProviderTemplate[] = [
  { key: "siliconflow", label: "硅基流动", name: "硅基流动", ecosystem: "openai", openAiMode: "openai_chat_completions", baseUrl: "https://api.siliconflow.cn/v1" },
  { key: "openai", label: "OpenAI", name: "OpenAI", ecosystem: "openai", openAiMode: "openai_responses", baseUrl: "https://api.openai.com/v1" },
  { key: "anthropic", label: "Anthropic", name: "Anthropic", ecosystem: "anthropic", openAiMode: "openai_chat_completions", baseUrl: "https://api.anthropic.com/v1" },
  { key: "gemini", label: "Gemini", name: "Google Gemini", ecosystem: "gemini", openAiMode: "openai_chat_completions", baseUrl: "https://generativelanguage.googleapis.com/v1beta" },
  { key: "deepseek", label: "DeepSeek", name: "DeepSeek", ecosystem: "openai", openAiMode: "openai_chat_completions", baseUrl: "https://api.deepseek.com" },
  { key: "volcengine", label: "火山方舟", name: "火山方舟", ecosystem: "openai", openAiMode: "openai_chat_completions", baseUrl: "https://ark.cn-beijing.volces.com/api/v3" },
  { key: "ollama", label: "本地 Ollama", name: "本地 Ollama", ecosystem: "openai", openAiMode: "openai_chat_completions", baseUrl: "http://127.0.0.1:11434/v1", noKey: true },
  { key: "custom", label: "自定义中转", name: "", ecosystem: "openai", openAiMode: "openai_chat_completions", baseUrl: "" },
];

const ECOSYSTEMS: Array<{ value: ApiEcosystem; label: string; hint: string }> = [
  { value: "openai", label: "OpenAI", hint: "兼容 /chat/completions 与 Responses" },
  { value: "anthropic", label: "Anthropic", hint: "Claude Messages 接口" },
  { value: "gemini", label: "Gemini", hint: "Google Generative 接口" },
];

const CONTEXT_PRESETS = [
  { label: "32K", value: 32768 },
  { label: "64K", value: 65536 },
  { label: "128K", value: 131072 },
  { label: "200K", value: 200000 },
  { label: "1M", value: 1000000 },
];

const OUTPUT_PRESETS = [
  { label: "4K", value: 4096 },
  { label: "8K", value: 8192 },
  { label: "16K", value: 16384 },
  { label: "32K", value: 32768 },
  { label: "64K", value: 65536 },
];

const THINKING_MODES: ThinkingMode[] = ["none", "low", "medium", "high", "xhigh", "max"];
const THINKING_LABELS: Record<ThinkingMode, string> = {
  none: "关闭",
  low: "低",
  medium: "中",
  high: "高",
  xhigh: "极高",
  max: "最高",
};

function ecosystemFromProtocol(protocol: ModelProtocol): ApiEcosystem {
  if (protocol === "anthropic_messages") return "anthropic";
  if (protocol === "google_generative_language") return "gemini";
  return "openai";
}

function resolveProtocol(ecosystem: ApiEcosystem, openAiMode: OpenAISubMode, legacyProtocol?: ModelProtocol): ModelProtocol {
  if (ecosystem === "anthropic") return "anthropic_messages";
  if (ecosystem === "gemini") return "google_generative_language";
  if (legacyProtocol === "deepseek_chat_completions" && openAiMode === "openai_chat_completions") {
    return "deepseek_chat_completions";
  }
  return openAiMode;
}

function protocolDisplayName(protocol: ModelProtocol): string {
  if (protocol === "anthropic_messages") return "Anthropic";
  if (protocol === "google_generative_language") return "Gemini";
  if (protocol === "openai_responses") return "OpenAI (Responses)";
  if (protocol === "deepseek_chat_completions") return "OpenAI (DeepSeek)";
  return "OpenAI";
}

function authStyleForProtocol(protocol: ModelProtocol, noKey: boolean): "bearer" | "x-api-key" | "x-goog-api-key" | "none" {
  if (noKey) return "none";
  if (protocol === "anthropic_messages") return "x-api-key";
  if (protocol === "google_generative_language") return "x-goog-api-key";
  return "bearer";
}

function formatTokenBudget(tokens: number): string {
  if (tokens >= 1000000 && tokens % 1000000 === 0) return `${tokens / 1000000}M`;
  if (tokens >= 1024 && tokens % 1024 === 0) return `${tokens / 1024}K`;
  if (tokens >= 1000 && tokens % 1000 === 0) return `${tokens / 1000}K`;
  return tokens.toLocaleString("zh-CN");
}

function slugProviderId(name: string, presetKey?: string): string {
  const seed = (presetKey && presetKey !== "custom" ? presetKey : name)
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return seed || `provider-${Date.now().toString(36).slice(-4)}`;
}

function probeErrorText(code: string | null | undefined): string {
  if (!code) return "连接失败，请核对地址、协议与 API Key";
  const map: Record<string, string> = {
    provider_auth_failed: "API Key 无效或无访问权限（401/403）",
    provider_rate_limited: "上游请求限流（429），请稍后重试",
    provider_timeout: "连接超时，请检查地址与端口是否可达",
    provider_unreachable: "无法连接目标服务器，请核对 Base URL 与端口",
    model_not_found: "上游接口返回模型不存在，请核对上游模型 ID",
    invalid_response: "上游返回格式与所选接口格式不匹配",
  };
  return map[code] ?? adminErrorMessage(code);
}

interface ModelModalState {
  mode: "create" | "edit";
  providerId: string;
  model?: AdminModelProfile;
  prefillUpstreamId?: string;
  prefillLabel?: string;
}

export default function AdminModelsPage() {
  const { token } = useAdminOutlet();
  const api = useMemo(() => createAdminApi(token), [token]);
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const initialParamProvider = searchParams.get("provider") || "";

  const [selectedProviderId, setSelectedProviderId] = useState<string>(initialParamProvider);
  const [creatingProvider, setCreatingProvider] = useState(false);
  const [modelModal, setModelModal] = useState<ModelModalState | null>(null);
  const [discoverTarget, setDiscoverTarget] = useState<AdminModelProvider | null>(null);
  const [archiveTarget, setArchiveTarget] = useState<{ kind: "provider" | "model"; id: string; label: string } | null>(null);
  const [pageBanner, setPageBanner] = useState<{ tone: "ok" | "fail"; text: string } | null>(null);

  const directoryQuery = useQuery({
    queryKey: ["admin", "model-directory"],
    queryFn: ({ signal }) => api.modelDirectory(signal),
    retry: false,
    placeholderData: previous => previous,
  });

  const providers = directoryQuery.data?.providers ?? [];
  const models = directoryQuery.data?.models ?? [];
  const localHostsAllowed = Boolean(directoryQuery.data?.local_hosts_allowed);

  async function refreshDirectory() {
    await queryClient.invalidateQueries({ queryKey: ["admin", "model-directory"] });
    await queryClient.invalidateQueries({ queryKey: ["workspace", "models"] });
  }

  useEffect(() => {
    if (creatingProvider || directoryQuery.isFetching) return;
    const requested = searchParams.get("provider") || "";
    if (requested && providers.some(p => p.id === requested)) {
      if (selectedProviderId !== requested) setSelectedProviderId(requested);
      return;
    }
    if (selectedProviderId && providers.some(p => p.id === selectedProviderId)) return;
    if (providers[0]) {
      setSelectedProviderId(providers[0].id);
    } else {
      setSelectedProviderId("");
    }
  }, [creatingProvider, directoryQuery.isFetching, providers, searchParams, selectedProviderId]);

  const activeProvider = creatingProvider
    ? undefined
    : providers.find(p => p.id === selectedProviderId) ?? providers[0];

  const modelsByProvider = useMemo(() => {
    const map = new Map<string, AdminModelProfile[]>();
    for (const model of models) {
      const list = map.get(model.provider_id) ?? [];
      list.push(model);
      map.set(model.provider_id, list);
    }
    return map;
  }, [models]);

  const archiveMutation = useMutation({
    mutationFn: async (target: { kind: "provider" | "model"; id: string }) => {
      return target.kind === "provider" ? api.deleteProvider(target.id) : api.deleteModel(target.id);
    },
    onSuccess: async (_, target) => {
      setArchiveTarget(null);
      if (target.kind === "provider" && target.id === selectedProviderId) {
        setSelectedProviderId("");
      }
      setPageBanner({ tone: "ok", text: target.kind === "provider" ? "已归档该服务商及其下模型。" : "已归档该模型。" });
      await refreshDirectory();
    },
    onError: error => {
      setArchiveTarget(null);
      setPageBanner({ tone: "fail", text: error instanceof Error ? error.message : "归档未完成。" });
    },
  });

  function selectProvider(id: string) {
    setCreatingProvider(false);
    setSelectedProviderId(id);
    setPageBanner(null);
    if (searchParams.get("provider")) {
      setSearchParams({}, { replace: true });
    }
  }

  function openNewProvider() {
    setCreatingProvider(true);
    setPageBanner(null);
  }

  function openAddModel(providerId?: string, prefillUpstream?: string, prefillLabel?: string) {
    setPageBanner(null);
    setModelModal({
      mode: "create",
      providerId: providerId || activeProvider?.id || providers[0]?.id || "",
      prefillUpstreamId: prefillUpstream,
      prefillLabel,
    });
  }

  const activeModels = activeProvider ? modelsByProvider.get(activeProvider.id) ?? [] : [];

  return (
    <div className="admin-route-page admin-cc-models">
      <PageHeader
        eyebrow="模型与渠道"
        title="模型接入"
        description="先配置服务商地址与 API Key，再在旗下添加或拉取模型；模型测通后即可开放使用。"
        action={
          <div className="admin-cc-header-actions">
            <RefreshButton loading={directoryQuery.isFetching} onClick={() => void directoryQuery.refetch()} />
            <button type="button" className="admin-secondary-button" onClick={() => openAddModel(activeProvider?.id)}>
              <Plus size={15} />
              添加模型
            </button>
            <button type="button" className="admin-primary-button" onClick={openNewProvider}>
              <Cloud size={15} />
              添加服务商
            </button>
          </div>
        }
      />

      {pageBanner && (
        <div className={`admin-cc-toast is-${pageBanner.tone}`} role="status">
          {pageBanner.tone === "ok" ? <CheckCircle2 size={15} /> : <CircleAlert size={15} />}
          <span>{pageBanner.text}</span>
        </div>
      )}

      {!directoryQuery.data && directoryQuery.isPending ? (
        <InitialLoading label="正在读取服务商与模型…" />
      ) : !directoryQuery.data ? (
        <LoadError
          message={directoryQuery.error instanceof Error ? directoryQuery.error.message : "模型目录暂时不可用。"}
          onRetry={() => void directoryQuery.refetch()}
        />
      ) : (
        <>
          {directoryQuery.isError && (
            <InlineError
              message={directoryQuery.error instanceof Error ? directoryQuery.error.message : "刷新没有完成。"}
              onRetry={() => void directoryQuery.refetch()}
            />
          )}

          <div className="admin-cc-workspace">
            <aside className="admin-cc-sidebar" aria-label="服务商列表">
              <div className="admin-cc-sidebar-head">
                <span>服务商 ({providers.length})</span>
                <small>{models.filter(m => m.enabled).length}/{models.length} 开放</small>
              </div>
              {providers.length === 0 ? (
                <div className="admin-cc-sidebar-empty">
                  <p>还没有服务商</p>
                  <button type="button" className="admin-primary-button" onClick={openNewProvider}>
                    <Plus size={15} />
                    添加首个服务商
                  </button>
                </div>
              ) : (
                <div className="admin-cc-provider-list">
                  {providers.map(provider => {
                    const list = modelsByProvider.get(provider.id) ?? [];
                    const openCount = list.filter(m => m.enabled).length;
                    const selected = !creatingProvider && activeProvider?.id === provider.id;
                    const dotClass = !provider.enabled
                      ? "is-off"
                      : openCount > 0
                        ? "is-live"
                        : "is-idle";
                    return (
                      <button
                        type="button"
                        key={provider.id}
                        className={`admin-cc-provider-item ${selected ? "is-active" : ""}`}
                        onClick={() => selectProvider(provider.id)}
                      >
                        <div className="admin-cc-provider-top">
                          <span className={`admin-cc-dot ${dotClass}`} aria-hidden="true" />
                          <strong>{provider.name}</strong>
                          <span className="admin-cc-proto-tag">{protocolDisplayName(provider.default_protocol)}</span>
                        </div>
                        {provider.notes ? <p className="admin-cc-provider-note">{provider.notes}</p> : null}
                        <div className="admin-cc-provider-meta">
                          <span>{provider.base_url}</span>
                          <b>{list.length === 0 ? "暂无模型" : `${openCount}/${list.length} 开放`}</b>
                        </div>
                      </button>
                    );
                  })}
                </div>
              )}
            </aside>

            <section className="admin-cc-main">
              {creatingProvider || !activeProvider ? (
                <ProviderConnectionCard
                  key="new-provider-card"
                  api={api}
                  localHostsAllowed={localHostsAllowed}
                  onSaved={async saved => {
                    setCreatingProvider(false);
                    setSelectedProviderId(saved.id);
                    setPageBanner({ tone: "ok", text: `已添加服务商「${saved.name}」，现在可以拉取模型或手动添加模型。` });
                    await refreshDirectory();
                  }}
                  onCancel={providers.length > 0 ? () => setCreatingProvider(false) : undefined}
                />
              ) : (
                <div className="admin-cc-detail-stack">
                  <ProviderConnectionCard
                    key={`edit-${activeProvider.id}-${activeProvider.revision}`}
                    api={api}
                    provider={activeProvider}
                    localHostsAllowed={localHostsAllowed}
                    onSaved={async saved => {
                      setPageBanner({ tone: "ok", text: `已保存服务商「${saved.name}」的连接设置。` });
                      await refreshDirectory();
                    }}
                    onOpenDiscover={() => setDiscoverTarget(activeProvider)}
                    onRequestArchive={() =>
                      setArchiveTarget({ kind: "provider", id: activeProvider.id, label: activeProvider.name })
                    }
                  />

                  <ProviderModelsSection
                    api={api}
                    provider={activeProvider}
                    models={activeModels}
                    onAddModel={() => openAddModel(activeProvider.id)}
                    onEditModel={model =>
                      setModelModal({ mode: "edit", providerId: activeProvider.id, model })
                    }
                    onRequestArchive={model =>
                      setArchiveTarget({ kind: "model", id: model.profile_id, label: model.label })
                    }
                    onDirectoryChanged={refreshDirectory}
                    onBanner={setPageBanner}
                  />
                </div>
              )}
            </section>
          </div>
        </>
      )}

      {modelModal && (
        <UnifiedModelDialog
          api={api}
          modal={modelModal}
          providers={providers}
          localHostsAllowed={localHostsAllowed}
          onClose={() => setModelModal(null)}
          onSaved={async (providerId, bannerText) => {
            setModelModal(null);
            setCreatingProvider(false);
            setSelectedProviderId(providerId);
            setPageBanner({ tone: "ok", text: bannerText });
            await refreshDirectory();
          }}
        />
      )}

      {discoverTarget && (
        <DiscoverModelsDialog
          api={api}
          provider={discoverTarget}
          onClose={() => setDiscoverTarget(null)}
          onPick={discovered => {
            const pid = discoverTarget.id;
            setDiscoverTarget(null);
            openAddModel(pid, discovered.id, discovered.label || discovered.id);
          }}
        />
      )}

      <AlertDialog open={Boolean(archiveTarget)} onOpenChange={open => { if (!open && !archiveMutation.isPending) setArchiveTarget(null); }}>
        <AlertDialogContent className="admin-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle>
              {archiveTarget?.kind === "provider" ? `归档服务商「${archiveTarget.label}」？` : `归档模型「${archiveTarget?.label}」？`}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {archiveTarget?.kind === "provider"
                ? "归档后将停用该服务商及旗下全部模型，不影响老对话历史查看。"
                : "归档后工作台不再可选用该模型，历史对话记录与运行快照仍完整保留。"}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="admin-dialog-cancel" disabled={archiveMutation.isPending}>取消</AlertDialogCancel>
            <AlertDialogAction
              className="admin-dialog-confirm"
              disabled={archiveMutation.isPending}
              onClick={event => {
                event.preventDefault();
                if (archiveTarget) archiveMutation.mutate({ kind: archiveTarget.kind, id: archiveTarget.id });
              }}
            >
              {archiveMutation.isPending ? "处理中…" : "确认归档"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function ProviderConnectionCard({
  api,
  provider,
  localHostsAllowed,
  onSaved,
  onCancel,
  onOpenDiscover,
  onRequestArchive,
}: {
  api: ReturnType<typeof createAdminApi>;
  provider?: AdminModelProvider;
  localHostsAllowed: boolean;
  onSaved: (provider: AdminModelProvider) => Promise<void>;
  onCancel?: () => void;
  onOpenDiscover?: () => void;
  onRequestArchive?: () => void;
}) {
  const creating = !provider;
  const initialProto: ModelProtocol = provider?.default_protocol ?? "openai_chat_completions";
  const initialEco = ecosystemFromProtocol(initialProto);
  const initialOpenAiMode: OpenAISubMode =
    initialProto === "openai_responses" ? "openai_responses" : "openai_chat_completions";

  const [presetKey, setPresetKey] = useState(provider?.preset_type ?? "siliconflow");
  const [name, setName] = useState(provider?.name ?? "硅基流动");
  const [notes, setNotes] = useState(provider?.notes ?? "");
  const [customSlug, setCustomSlug] = useState(provider?.id ?? "");
  const [showSlugEdit, setShowSlugEdit] = useState(false);
  const [ecosystem, setEcosystem] = useState<ApiEcosystem>(initialEco);
  const [openAiMode, setOpenAiMode] = useState<OpenAISubMode>(initialOpenAiMode);
  const [baseUrl, setBaseUrl] = useState(provider?.base_url ?? "https://api.siliconflow.cn/v1");
  const [apiKey, setApiKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [noKeyLocal, setNoKeyLocal] = useState(provider?.auth_style === "none");
  const [enabled, setEnabled] = useState(provider?.enabled ?? true);

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [probing, setProbing] = useState(false);
  const [probeResult, setProbeResult] = useState<AdminConnectivityTestResult | null>(null);

  const effectiveProtocol = resolveProtocol(ecosystem, openAiMode, provider?.default_protocol);
  const effectiveSlug = provider?.id ?? (customSlug.trim() || slugProviderId(name, presetKey));
  const localUrlWarn = !localHostsAllowed && /^http:\/\/(?:127\.|localhost|0\.0\.0\.0|\[::1\])/i.test(baseUrl.trim());

  function applyTemplate(tpl: ProviderTemplate) {
    setPresetKey(tpl.key);
    if (tpl.key !== "custom") {
      setName(tpl.name);
      setBaseUrl(tpl.baseUrl);
    } else {
      setName("");
      setBaseUrl("");
    }
    setEcosystem(tpl.ecosystem);
    setOpenAiMode(tpl.openAiMode);
    setNoKeyLocal(Boolean(tpl.noKey));
    setCustomSlug("");
    setError(null);
  }

  async function runTest() {
    if (!provider) return;
    setProbing(true);
    setProbeResult(null);
    try {
      const res = await api.testProvider(provider.id);
      setProbeResult(res);
    } catch (cause) {
      setProbeResult({
        ok: false,
        connectivity_status: "failed",
        latency_ms: null,
        error_code: cause instanceof Error ? cause.message : "连接失败",
      });
    } finally {
      setProbing(false);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    if (creating && !noKeyLocal && !apiKey.trim()) {
      setError("请填写 API Key（若是本地免密服务，请勾选“本地服务无需 API Key”）。");
      return;
    }
    setSaving(true);
    try {
      const authStyle = authStyleForProtocol(effectiveProtocol, noKeyLocal);
      if (creating) {
        const created = await api.createProvider({
          id: customSlug.trim() || undefined,
          name: name.trim(),
          notes: notes.trim(),
          preset_type: presetKey,
          default_protocol: effectiveProtocol,
          auth_style: authStyle,
          base_url: baseUrl.trim(),
          ...(noKeyLocal ? { credential_mode: "encrypted_db" as const } : { credential_mode: "encrypted_db" as const, api_key: apiKey.trim() }),
          enabled,
        });
        await onSaved(created);
      } else {
        const patch: Record<string, unknown> = {
          name: name.trim(),
          notes: notes.trim(),
          default_protocol: effectiveProtocol,
          auth_style: authStyle,
          base_url: baseUrl.trim(),
          enabled,
        };
        if (!noKeyLocal && apiKey.trim()) {
          patch.api_key = apiKey.trim();
        }
        const updated = await api.updateProvider(provider.id, patch);
        setApiKey("");
        await onSaved(updated);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存服务商失败。");
    } finally {
      setSaving(false);
    }
  }

  const keyPlaceholder = creating
    ? "填入服务商或中转站提供的 API Key"
    : provider.api_key_hint
      ? `当前已保存 ${provider.api_key_hint}，留空保持不变`
      : provider.credential_mode === "env_ref" && provider.credential_ref
        ? `当前使用环境变量 ${provider.credential_ref}，填入新 Key 可覆盖保存`
        : provider.credential_mode === "file_ref"
          ? "当前由系统内置配置托管，填入新 Key 可直接接管"
          : "填入新 API Key";

  return (
    <form className="admin-cc-card" onSubmit={e => void submit(e)}>
      <div className="admin-cc-card-head">
        <div>
          <div className="admin-cc-title-line">
            <Server size={17} />
            <h2>{creating ? "添加模型服务商" : provider.name}</h2>
            {!creating && (
              <span className={`admin-cc-pill ${provider.credential_configured ? "is-ok" : "is-warn"}`}>
                {provider.credential_configured ? "凭据已配置" : "缺少密钥"}
              </span>
            )}
          </div>
          <p className="admin-cc-subline">
            {creating
              ? "按 CC Switch 方式只需填名称、接口格式、API Key 与 Base URL 即可接入任意官方或中转渠道。"
              : (
                <>
                  供应商标识：<code>{provider.id}</code>
                  {provider.api_key_hint ? ` · 密钥尾号 ${provider.api_key_hint}` : ""}
                </>
              )}
          </p>
        </div>

        {!creating && (
          <div className="admin-cc-head-actions">
            <button type="button" className="admin-cc-btn" disabled={probing} onClick={() => void runTest()}>
              {probing ? <LoaderCircle className="admin-spin" size={14} /> : <FlaskConical size={14} />}
              测试连接
            </button>
            {onOpenDiscover && (
              <button type="button" className="admin-cc-btn" onClick={onOpenDiscover}>
                <Download size={14} />
                拉取模型
              </button>
            )}
            {onRequestArchive && (
              <button type="button" className="admin-cc-btn is-danger" onClick={onRequestArchive}>
                <Trash2 size={14} />
                归档
              </button>
            )}
          </div>
        )}
      </div>

      {creating && (
        <div className="admin-cc-templates" aria-label="常用服务商模板">
          {PROVIDER_TEMPLATES.map(tpl => (
            <button
              type="button"
              key={tpl.key}
              className={`admin-cc-chip ${presetKey === tpl.key ? "is-active" : ""}`}
              onClick={() => applyTemplate(tpl)}
            >
              {tpl.label}
            </button>
          ))}
        </div>
      )}

      {probeResult && (
        <div className={`admin-cc-banner ${probeResult.ok ? "is-ok" : "is-fail"}`}>
          {probeResult.ok ? <CheckCircle2 size={15} /> : <CircleAlert size={15} />}
          <span>
            {probeResult.ok
              ? `连接正常${probeResult.latency_ms != null ? ` · 延迟 ${probeResult.latency_ms} ms` : ""}`
              : `连接失败：${probeErrorText(probeResult.error_code)}`}
          </span>
        </div>
      )}

      <div className="admin-cc-grid-2">
        <label className="admin-cc-field">
          <span>名称 *</span>
          <input
            name="name"
            required
            placeholder="例如 硅基流动 / Skye Clark"
            value={name}
            onChange={e => setName(e.target.value)}
          />
        </label>
        <label className="admin-cc-field">
          <span>备注（可选）</span>
          <input
            name="notes"
            placeholder="例如 公司主用中转 / 仅研究测试"
            value={notes}
            onChange={e => setNotes(e.target.value)}
          />
        </label>
      </div>

      {creating && (
        <div className="admin-cc-slug-row">
          <span>
            供应商标识：<code>{effectiveSlug}</code>
          </span>
          <button type="button" className="admin-cc-inline-link" onClick={() => setShowSlugEdit(v => !v)}>
            {showSlugEdit ? "收起标识" : "自定义标识"}
          </button>
          {showSlugEdit && (
            <input
              name="id"
              pattern="[A-Za-z0-9][A-Za-z0-9._:\-]*"
              placeholder="小写字母、数字和连字符"
              value={customSlug}
              onChange={e => setCustomSlug(e.target.value)}
            />
          )}
        </div>
      )}

      <div className="admin-cc-field">
        <span>接口格式</span>
        <div className="admin-cc-eco-grid" role="radiogroup" aria-label="接口格式">
          {ECOSYSTEMS.map(item => (
            <button
              type="button"
              key={item.value}
              className={`admin-cc-eco-card ${ecosystem === item.value ? "is-active" : ""}`}
              onClick={() => setEcosystem(item.value)}
            >
              <strong>{item.label}</strong>
              <small>{item.hint}</small>
            </button>
          ))}
        </div>
        {ecosystem === "openai" && (
          <div className="admin-cc-submode">
            <span>OpenAI 接口版本：</span>
            <button
              type="button"
              className={openAiMode === "openai_chat_completions" ? "is-active" : ""}
              onClick={() => setOpenAiMode("openai_chat_completions")}
            >
              Chat 兼容（/chat/completions，多数厂商）
            </button>
            <button
              type="button"
              className={openAiMode === "openai_responses" ? "is-active" : ""}
              onClick={() => setOpenAiMode("openai_responses")}
            >
              Responses（GPT-5 / 新版中转）
            </button>
          </div>
        )}
      </div>

      <div className="admin-cc-grid-1">
        <div className="admin-cc-field">
          <div className="admin-cc-label-row">
            <span>API Key</span>
            <label className="admin-cc-check">
              <input
                type="checkbox"
                checked={noKeyLocal}
                onChange={e => setNoKeyLocal(e.target.checked)}
              />
              <span>本地免密服务（无需 Key）</span>
            </label>
          </div>
          {!noKeyLocal && (
            <div className="admin-cc-secret-wrap">
              <input
                name="api_key"
                type={showKey ? "text" : "password"}
                autoComplete="new-password"
                placeholder={keyPlaceholder}
                value={apiKey}
                onChange={e => setApiKey(e.target.value)}
              />
              <button
                type="button"
                aria-label={showKey ? "隐藏密钥" : "显示密钥"}
                onClick={() => setShowKey(v => !v)}
              >
                {showKey ? <EyeOff size={15} /> : <Eye size={15} />}
              </button>
            </div>
          )}
        </div>

        <label className="admin-cc-field">
          <span>Base URL（支持自定义端口与前缀，如 /v1、/api/v3）*</span>
          <input
            name="base_url"
            type="url"
            required
            placeholder="https://47.116.200.213:8086/v1"
            value={baseUrl}
            onChange={e => setBaseUrl(e.target.value)}
          />
        </label>
        {localUrlWarn && (
          <p className="admin-cc-hint is-warn">
            提示：当前服务端未启用本机内网地址放行（需设置环境变量 <code>ZHOUXU_ALLOW_LOCAL_MODEL_HOSTS=1</code>）。
          </p>
        )}
      </div>

      <div className="admin-cc-card-foot">
        <label className="admin-model-toggle">
          <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />
          <span />
          <b>启用此服务商</b>
        </label>

        <div className="admin-cc-foot-right">
          {error && <p className="admin-cc-error" role="alert">{error}</p>}
          {onCancel && (
            <button type="button" className="admin-cc-btn" disabled={saving} onClick={onCancel}>
              取消
            </button>
          )}
          <button type="submit" className="admin-primary-button" disabled={saving}>
            {saving ? <LoaderCircle className="admin-spin" size={15} /> : <Save size={15} />}
            {creating ? "保存服务商" : "保存连接设置"}
          </button>
        </div>
      </div>
    </form>
  );
}

function ProviderModelsSection({
  api,
  provider,
  models,
  onAddModel,
  onEditModel,
  onRequestArchive,
  onDirectoryChanged,
  onBanner,
}: {
  api: ReturnType<typeof createAdminApi>;
  provider: AdminModelProvider;
  models: AdminModelProfile[];
  onAddModel: () => void;
  onEditModel: (model: AdminModelProfile) => void;
  onRequestArchive: (model: AdminModelProfile) => void;
  onDirectoryChanged: () => Promise<void>;
  onBanner: (banner: { tone: "ok" | "fail"; text: string } | null) => void;
}) {
  const [busyId, setBusyId] = useState<string | null>(null);

  async function runModelTest(model: AdminModelProfile) {
    setBusyId(model.profile_id);
    onBanner(null);
    try {
      const res = await api.testModel(model.profile_id);
      await onDirectoryChanged();
      onBanner(
        res.ok
          ? { tone: "ok", text: `「${model.label}」连通测试通过${res.latency_ms != null ? `（耗时 ${res.latency_ms} ms）` : ""}，现在可以打开启用开关。` }
          : { tone: "fail", text: `「${model.label}」测试失败：${probeErrorText(res.error_code)}` },
      );
    } catch (cause) {
      onBanner({ tone: "fail", text: cause instanceof Error ? cause.message : "测试连通失败。" });
    } finally {
      setBusyId(null);
    }
  }

  async function toggleEnabled(model: AdminModelProfile) {
    setBusyId(model.profile_id);
    onBanner(null);
    try {
      await api.updateModel(model.profile_id, { enabled: !model.enabled });
      await onDirectoryChanged();
      onBanner({
        tone: "ok",
        text: !model.enabled ? `已启用「${model.label}」，聊天与工作模式现已可选。` : `已停用「${model.label}」。`,
      });
    } catch (cause) {
      onBanner({ tone: "fail", text: cause instanceof Error ? cause.message : "状态更新失败。" });
    } finally {
      setBusyId(null);
    }
  }

  async function setDefault(model: AdminModelProfile) {
    setBusyId(model.profile_id);
    onBanner(null);
    try {
      await api.updateModel(model.profile_id, { is_default: true, enabled: true });
      await onDirectoryChanged();
      onBanner({ tone: "ok", text: `已将「${model.label}」设为新对话默认模型。` });
    } catch (cause) {
      onBanner({ tone: "fail", text: cause instanceof Error ? cause.message : "设置默认模型失败。" });
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section className="admin-cc-card" aria-label="服务商旗下模型">
      <div className="admin-cc-card-head">
        <div>
          <div className="admin-cc-title-line">
            <Cpu size={17} />
            <h2>旗下模型 ({models.length})</h2>
          </div>
          <p className="admin-cc-subline">
            以下模型全部走「{provider.name}」的接口地址与密钥；如果同一中转地址混跑多种模型，可对单个模型单独指定接口格式。
          </p>
        </div>
        <button type="button" className="admin-primary-button" onClick={onAddModel}>
          <Plus size={15} />
          在此服务商下添加模型
        </button>
      </div>

      {models.length === 0 ? (
        <div className="admin-cc-models-empty">
          <p>「{provider.name}」下还没有配置模型。</p>
          <button type="button" className="admin-cc-btn" onClick={onAddModel}>
            <Plus size={14} />
            立即添加模型
          </button>
        </div>
      ) : (
        <div className="admin-cc-model-list">
          {models.map(model => {
            const busy = busyId === model.profile_id;
            const verified = model.connectivity_status === "verified";
            const scopeText =
              model.access_scope === "admin_only" ? "仅管理员" : model.access_scope === "allowlist" ? "指定白名单" : "全员可用";
            return (
              <article key={model.profile_id} className={`admin-cc-model-row ${model.enabled ? "is-enabled" : ""}`}>
                <div className="admin-cc-model-info">
                  <div className="admin-cc-model-title">
                    <strong>{model.label}</strong>
                    {model.is_default && (
                      <span className="admin-cc-badge is-default">
                        <Star size={11} />
                        默认
                      </span>
                    )}
                    <span className={`admin-cc-badge is-${model.connectivity_status}`}>
                      {verified
                        ? `已连通${model.connectivity_latency_ms != null ? ` ${model.connectivity_latency_ms}ms` : ""}`
                        : model.connectivity_status === "failed"
                          ? "未测通"
                          : "待测试"}
                    </span>
                    <span className="admin-cc-badge">{scopeText}</span>
                  </div>
                  <div className="admin-cc-model-meta">
                    <code>{model.upstream_model_id}</code>
                    <span>·</span>
                    <span>{model.protocol_override ? `单独协议：${protocolDisplayName(model.protocol)}` : `跟随服务商 (${protocolDisplayName(model.protocol)})`}</span>
                    <span>·</span>
                    <span>上下文 {formatTokenBudget(model.context_tokens)} / 输出 {formatTokenBudget(model.max_output_tokens)}</span>
                    <span>·</span>
                    <span>思考 {model.thinking_modes.map(m => THINKING_LABELS[m] ?? m).join("、")}</span>
                  </div>
                  {model.connectivity_status === "failed" && model.connectivity_error && (
                    <p className="admin-cc-model-fail">{probeErrorText(model.connectivity_error)}</p>
                  )}
                </div>

                <div className="admin-cc-model-actions">
                  <button
                    type="button"
                    className="admin-cc-btn"
                    disabled={busy}
                    onClick={() => void runModelTest(model)}
                  >
                    {busy ? <LoaderCircle className="admin-spin" size={14} /> : <FlaskConical size={14} />}
                    测试连通性
                  </button>
                  <label
                    className="admin-model-toggle"
                    title={verified ? "开启后用户可在工作台选择此模型" : "请先测试连通性并通过后开启"}
                  >
                    <input
                      type="checkbox"
                      aria-label={`开放模型 ${model.label}`}
                      checked={model.enabled}
                      disabled={busy || (!model.enabled && !verified)}
                      onChange={() => void toggleEnabled(model)}
                    />
                    <span />
                    <b>{model.enabled ? "已开放" : "已停用"}</b>
                  </label>
                  {verified && model.enabled && !model.is_default && (
                    <button
                      type="button"
                      className="admin-cc-btn"
                      disabled={busy}
                      onClick={() => void setDefault(model)}
                    >
                      设为默认
                    </button>
                  )}
                  <button type="button" className="admin-cc-btn" disabled={busy} onClick={() => onEditModel(model)}>
                    编辑
                  </button>
                  <button
                    type="button"
                    className="admin-cc-btn is-danger"
                    disabled={busy}
                    aria-label={`归档模型 ${model.label}`}
                    onClick={() => onRequestArchive(model)}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}

function UnifiedModelDialog({
  api,
  modal,
  providers,
  localHostsAllowed,
  onClose,
  onSaved,
}: {
  api: ReturnType<typeof createAdminApi>;
  modal: ModelModalState;
  providers: AdminModelProvider[];
  localHostsAllowed: boolean;
  onClose: () => void;
  onSaved: (providerId: string, bannerText: string) => Promise<void>;
}) {
  const editing = modal.model;
  const hasExistingProviders = providers.length > 0;

  const [providerMode, setProviderMode] = useState<"existing" | "new">(
    !editing && !hasExistingProviders ? "new" : "existing",
  );
  const [providerId, setProviderId] = useState(
    editing?.provider_id || modal.providerId || providers[0]?.id || "",
  );

  // Inline new-provider fields (shown directly when adding a model for a brand-new supplier!)
  const [newProvName, setNewProvName] = useState("");
  const [newProvNotes, setNewProvNotes] = useState("");
  const [newProvEco, setNewProvEco] = useState<ApiEcosystem>("openai");
  const [newProvOpenAiMode, setNewProvOpenAiMode] = useState<OpenAISubMode>("openai_chat_completions");
  const [newProvUrl, setNewProvUrl] = useState("");
  const [newProvKey, setNewProvKey] = useState("");
  const [newProvNoKey, setNewProvNoKey] = useState(false);

  // Core model fields ONLY (no legacy family / sort_order clutter!)
  const [upstreamId, setUpstreamId] = useState(editing?.upstream_model_id ?? modal.prefillUpstreamId ?? "");
  const [label, setLabel] = useState(editing?.label ?? modal.prefillLabel ?? "");
  const [labelTouched, setLabelTouched] = useState(Boolean(editing || modal.prefillLabel));
  const [protocolChoice, setProtocolChoice] = useState<string>(editing?.protocol_override ?? "");
  const [contextTokens, setContextTokens] = useState(editing?.context_tokens ?? 131072);
  const [maxOutputTokens, setMaxOutputTokens] = useState(editing?.max_output_tokens ?? 16384);
  const [modes, setModes] = useState<ThinkingMode[]>(editing?.thinking_modes ?? ["none"]);
  const [defaultThinking, setDefaultThinking] = useState<ThinkingMode>(editing?.default_thinking ?? "none");
  const [accessScope, setAccessScope] = useState(editing?.access_scope ?? "all");
  const [testImmediately, setTestImmediately] = useState(!editing);

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedProvider = providers.find(p => p.id === providerId);
  const inheritedProtocol: ModelProtocol =
    providerMode === "new"
      ? resolveProtocol(newProvEco, newProvOpenAiMode)
      : selectedProvider?.default_protocol ?? "openai_chat_completions";

  function handleUpstreamChange(next: string) {
    setUpstreamId(next);
    if (!labelTouched) {
      setLabel(next.split("/").pop() ?? next);
    }
  }

  function toggleThinking(mode: ThinkingMode, checked: boolean) {
    const next = checked ? THINKING_MODES.filter(m => m === mode || modes.includes(m)) : modes.filter(m => m !== mode);
    if (!next.length) return;
    setModes(next);
    if (!next.includes(defaultThinking)) setDefaultThinking(next[0]);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSaving(true);
    try {
      let targetProviderId = providerId;
      if (!editing && providerMode === "new") {
        if (!newProvNoKey && !newProvKey.trim()) {
          setError("请填写新服务商的 API Key（本地免密服务请勾选无需 Key）。");
          setSaving(false);
          return;
        }
        const newProto = resolveProtocol(newProvEco, newProvOpenAiMode);
        const createdProvider = await api.createProvider({
          name: newProvName.trim(),
          notes: newProvNotes.trim(),
          preset_type: "custom",
          default_protocol: newProto,
          auth_style: authStyleForProtocol(newProto, newProvNoKey),
          base_url: newProvUrl.trim(),
          credential_mode: "encrypted_db",
          ...(newProvNoKey ? {} : { api_key: newProvKey.trim() }),
          enabled: true,
        });
        targetProviderId = createdProvider.id;
      }

      const overrideValue = protocolChoice ? (protocolChoice as ModelProtocol) : null;
      let savedModel: AdminModelProfile;
      if (editing) {
        savedModel = await api.updateModel(editing.profile_id, {
          upstream_model_id: upstreamId.trim(),
          label: label.trim(),
          protocol_override: overrideValue,
          context_tokens: Number(contextTokens),
          max_output_tokens: Number(maxOutputTokens),
          thinking_modes: modes,
          default_thinking: defaultThinking,
          access_scope: accessScope,
        });
      } else {
        savedModel = await api.createModel({
          provider_id: targetProviderId,
          upstream_model_id: upstreamId.trim(),
          label: label.trim(),
          protocol_override: overrideValue,
          context_tokens: Number(contextTokens),
          max_output_tokens: Number(maxOutputTokens),
          thinking_modes: modes,
          default_thinking: defaultThinking,
          access_scope: accessScope,
          enabled: false,
        });
      }

      if (testImmediately) {
        const probe = await api.testModel(savedModel.profile_id);
        if (probe.ok) {
          await api.updateModel(savedModel.profile_id, { enabled: true });
          await onSaved(
            targetProviderId,
            `「${savedModel.label}」已测通${probe.latency_ms != null ? `（${probe.latency_ms} ms）` : ""}并开放使用。`,
          );
          return;
        }
        await onSaved(
          targetProviderId,
          `已保存模型「${savedModel.label}」，但连通测试未通过（${probeErrorText(probe.error_code)}），暂保持停用。`,
        );
        return;
      }

      await onSaved(targetProviderId, editing ? `已更新模型「${savedModel.label}」。` : `已保存模型「${savedModel.label}」，请测试连通后开放使用。`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存模型失败。");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open onOpenChange={open => { if (!open && !saving) onClose(); }}>
      <DialogContent className="admin-dialog admin-cc-modal">
        <DialogHeader>
          <DialogTitle>{editing ? `编辑模型 · ${editing.label}` : "添加模型（关联服务商）"}</DialogTitle>
          <DialogDescription>
            {editing
              ? "修改上游模型 ID 或接口协议后，需重新测通才可启用；修改仅对新任务生效。"
              : "先确定由哪家服务商提供接口，再设置模型上下文预算并测试连通性。"}
          </DialogDescription>
        </DialogHeader>

        <form className="admin-cc-modal-form" onSubmit={e => void submit(e)}>
          {!editing ? (
            <div className="admin-cc-provider-box">
              <div className="admin-cc-provider-box-head">
                <span><Link2 size={14} />服务商来源</span>
                {hasExistingProviders && (
                  <div className="admin-cc-mini-switch">
                    <button
                      type="button"
                      className={providerMode === "existing" ? "is-active" : ""}
                      onClick={() => setProviderMode("existing")}
                    >
                      选已有服务商
                    </button>
                    <button
                      type="button"
                      className={providerMode === "new" ? "is-active" : ""}
                      onClick={() => setProviderMode("new")}
                    >
                      + 新建并关联服务商
                    </button>
                  </div>
                )}
              </div>

              {providerMode === "existing" && selectedProvider ? (
                <>
                  <select name="provider_id" value={providerId} onChange={e => setProviderId(e.target.value)}>
                    {providers.map(p => (
                      <option key={p.id} value={p.id}>
                        {p.name} · {p.base_url}
                      </option>
                    ))}
                  </select>
                  <div className="admin-cc-provider-summary">
                    <span>接口：<strong>{protocolDisplayName(selectedProvider.default_protocol)}</strong></span>
                    <span>地址：<code>{selectedProvider.base_url}</code></span>
                    <span>密钥：{selectedProvider.api_key_hint || (selectedProvider.credential_configured ? "已配置" : "未配置")}</span>
                  </div>
                </>
              ) : (
                <div className="admin-cc-inline-new-prov">
                  <div className="admin-cc-grid-2">
                    <label className="admin-cc-field">
                      <span>服务商名称 *</span>
                      <input required placeholder="例如 Skye Clark / 硅基流动" value={newProvName} onChange={e => setNewProvName(e.target.value)} />
                    </label>
                    <label className="admin-cc-field">
                      <span>备注（可选）</span>
                      <input placeholder="例如 公司主用" value={newProvNotes} onChange={e => setNewProvNotes(e.target.value)} />
                    </label>
                  </div>
                  <div className="admin-cc-grid-2">
                    <label className="admin-cc-field">
                      <span>接口格式</span>
                      <select
                        value={newProvEco}
                        onChange={e => setNewProvEco(e.target.value as ApiEcosystem)}
                      >
                        <option value="openai">OpenAI（通用与 Responses）</option>
                        <option value="anthropic">Anthropic (Claude)</option>
                        <option value="gemini">Google (Gemini)</option>
                      </select>
                    </label>
                    {newProvEco === "openai" && (
                      <label className="admin-cc-field">
                        <span>OpenAI 版本</span>
                        <select value={newProvOpenAiMode} onChange={e => setNewProvOpenAiMode(e.target.value as OpenAISubMode)}>
                          <option value="openai_chat_completions">Chat 兼容（推荐）</option>
                          <option value="openai_responses">Responses API</option>
                        </select>
                      </label>
                    )}
                  </div>
                  <label className="admin-cc-field">
                    <span>Base URL（含端口与路径前缀）*</span>
                    <input type="url" required placeholder="https://47.116.200.213:8086/v1" value={newProvUrl} onChange={e => setNewProvUrl(e.target.value)} />
                  </label>
                  <div className="admin-cc-field">
                    <div className="admin-cc-label-row">
                      <span>API Key</span>
                      <label className="admin-cc-check">
                        <input type="checkbox" checked={newProvNoKey} onChange={e => setNewProvNoKey(e.target.checked)} />
                        <span>本地免密服务</span>
                      </label>
                    </div>
                    {!newProvNoKey && (
                      <input
                        type="password"
                        autoComplete="new-password"
                        placeholder="填入该服务商的 API Key"
                        value={newProvKey}
                        onChange={e => setNewProvKey(e.target.value)}
                      />
                    )}
                    {!localHostsAllowed && /^http:\/\/(?:127\.|localhost)/i.test(newProvUrl.trim()) && (
                      <small className="admin-cc-hint is-warn">提示：服务端未启用本地地址放行。</small>
                    )}
                  </div>
                </div>
              )}
            </div>
          ) : selectedProvider ? (
            <div className="admin-cc-provider-summary">
              <span>服务商：<strong>{selectedProvider.name}</strong></span>
              <span>地址：<code>{selectedProvider.base_url}</code></span>
            </div>
          ) : null}

          <div className="admin-cc-grid-2">
            <label className="admin-cc-field">
              <span>上游模型 ID *</span>
              <input
                name="upstream_model_id"
                required
                placeholder="例如 claude-opus-4-6 / Qwen/Qwen2.5-72B-Instruct"
                value={upstreamId}
                onChange={e => handleUpstreamChange(e.target.value)}
              />
            </label>
            <label className="admin-cc-field">
              <span>显示名称 *</span>
              <input
                name="label"
                required
                placeholder="给工作台展示的名称"
                value={label}
                onChange={e => {
                  setLabelTouched(true);
                  setLabel(e.target.value);
                }}
              />
            </label>
          </div>

          <div className="admin-cc-grid-2">
            <label className="admin-cc-field">
              <span>接口通信格式</span>
              <select name="protocol_override" value={protocolChoice} onChange={e => setProtocolChoice(e.target.value)}>
                <option value="">跟随服务商（{protocolDisplayName(inheritedProtocol)}）</option>
                <option value="openai_chat_completions">OpenAI（Chat 兼容）</option>
                <option value="openai_responses">OpenAI（Responses）</option>
                <option value="anthropic_messages">Anthropic</option>
                <option value="google_generative_language">Gemini</option>
              </select>
            </label>
            <label className="admin-cc-field">
              <span>开放范围</span>
              <select name="access_scope" value={accessScope} onChange={e => setAccessScope(e.target.value)}>
                <option value="all">全员可用</option>
                <option value="admin_only">仅管理员测试</option>
                <option value="allowlist">仅授权白名单</option>
              </select>
            </label>
          </div>

          <div className="admin-cc-grid-2">
            <div className="admin-cc-field">
              <span>上下文窗口（Tokens）</span>
              <input
                name="context_tokens"
                type="number"
                min={1024}
                required
                value={contextTokens}
                onChange={e => setContextTokens(Number(e.target.value))}
              />
              <div className="admin-cc-presets">
                {CONTEXT_PRESETS.map(p => (
                  <button
                    type="button"
                    key={p.value}
                    className={contextTokens === p.value ? "is-active" : ""}
                    onClick={() => setContextTokens(p.value)}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            </div>
            <div className="admin-cc-field">
              <span>最大输出（Tokens）</span>
              <input
                name="max_output_tokens"
                type="number"
                min={256}
                required
                value={maxOutputTokens}
                onChange={e => setMaxOutputTokens(Number(e.target.value))}
              />
              <div className="admin-cc-presets">
                {OUTPUT_PRESETS.map(p => (
                  <button
                    type="button"
                    key={p.value}
                    className={maxOutputTokens === p.value ? "is-active" : ""}
                    onClick={() => setMaxOutputTokens(p.value)}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="admin-cc-grid-2">
            <fieldset className="admin-thinking-field">
              <legend>支持思考档位</legend>
              <div>
                {THINKING_MODES.map(mode => (
                  <label key={mode}>
                    <input
                      type="checkbox"
                      checked={modes.includes(mode)}
                      onChange={e => toggleThinking(mode, e.target.checked)}
                    />
                    <span>{THINKING_LABELS[mode]}</span>
                  </label>
                ))}
              </div>
            </fieldset>
            <label className="admin-cc-field">
              <span>默认思考档位</span>
              <select
                name="default_thinking"
                value={defaultThinking}
                onChange={e => setDefaultThinking(e.target.value as ThinkingMode)}
              >
                {modes.map(mode => (
                  <option key={mode} value={mode}>{THINKING_LABELS[mode]}</option>
                ))}
              </select>
            </label>
          </div>

          <label className="admin-cc-check">
            <input type="checkbox" checked={testImmediately} onChange={e => setTestImmediately(e.target.checked)} />
            <span>保存后立即测试连通性（通过后自动启用开放）</span>
          </label>

          {error && <p className="admin-cc-error" role="alert">{error}</p>}

          <DialogFooter>
            <button type="button" className="admin-page-button" disabled={saving} onClick={onClose}>
              取消
            </button>
            <button type="submit" className="admin-primary-button" disabled={saving}>
              {saving ? <LoaderCircle className="admin-spin" size={15} /> : <Save size={15} />}
              {testImmediately ? "保存并测试连通" : "保存模型"}
            </button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function DiscoverModelsDialog({
  api,
  provider,
  onClose,
  onPick,
}: {
  api: ReturnType<typeof createAdminApi>;
  provider: AdminModelProvider;
  onClose: () => void;
  onPick: (item: AdminDiscoveredModel) => void;
}) {
  const [filter, setFilter] = useState("");
  const query = useQuery({
    queryKey: ["admin", "provider-discover", provider.id],
    queryFn: () => api.discoverProviderModels(provider.id),
    retry: false,
  });

  const filtered = useMemo(() => {
    const list = query.data?.models ?? [];
    const term = filter.trim().toLowerCase();
    return term ? list.filter(m => m.id.toLowerCase().includes(term) || m.label.toLowerCase().includes(term)) : list;
  }, [filter, query.data]);

  return (
    <Dialog open onOpenChange={open => { if (!open) onClose(); }}>
      <DialogContent className="admin-dialog admin-discover-dialog">
        <DialogHeader>
          <DialogTitle>从「{provider.name}」拉取可用模型</DialogTitle>
          <DialogDescription>点击任一上游模型直接带入配置表单，设置上下文窗口并测通即可上线。</DialogDescription>
        </DialogHeader>
        <input
          type="search"
          className="admin-discover-search"
          placeholder="搜索模型 ID…"
          value={filter}
          onChange={e => setFilter(e.target.value)}
        />
        <div className="admin-discover-list">
          {query.isPending ? (
            <p className="admin-empty">正在向 {provider.base_url} 请求模型列表…</p>
          ) : query.isError ? (
            <p className="admin-empty">{query.error instanceof Error ? query.error.message : "拉取模型失败。"}</p>
          ) : !query.data?.supported ? (
            <p className="admin-empty">{query.data?.notice || "该上游暂不支持自动枚举 /models，请直接在服务商下点“添加模型”。"}</p>
          ) : filtered.length === 0 ? (
            <p className="admin-empty">没有找到匹配的模型。</p>
          ) : (
            filtered.map(item => (
              <div key={item.id} className="admin-discover-item">
                <div>
                  <strong>{item.id}</strong>
                  {item.already_added && <small>已在该服务商下配置</small>}
                </div>
                <button type="button" className="admin-cc-btn" onClick={() => onPick(item)}>
                  {item.already_added ? "再添一项" : "选用"}
                </button>
              </div>
            ))
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
