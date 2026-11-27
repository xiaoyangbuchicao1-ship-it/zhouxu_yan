import {
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  CircleAlert,
  Download,
  Eye,
  EyeOff,
  FlaskConical,
  Link2,
  LoaderCircle,
  Pencil,
  Plus,
  Save,
  Search,
  Server,
  Settings2,
  ShieldCheck,
  Sparkles,
  Star,
  Trash2,
  Wallet,
} from "lucide-react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  AdminApiError,
  adminErrorMessage,
  createAdminApi,
  type AdminConnectivityTestResult,
  type AdminDiscoveredModel,
  type AdminDomesticDefaultResult,
  type AdminModelProfile,
  type AdminModelProvider,
  type AdminProviderBalance,
  type ModelProtocol,
  type ThinkingMode,
} from "@/lib/admin-api";
import { inferModelSpecLocal, type InferredModelSpec } from "./model-inference";
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

/* 接口格式只有四种选法；OpenAI 的两个版本直接并排，不再多一层子选项。 */
const API_FORMATS: Array<{ key: string; label: string; ecosystem: ApiEcosystem; openAiMode?: OpenAISubMode }> = [
  { key: "openai-chat", label: "OpenAI 兼容", ecosystem: "openai", openAiMode: "openai_chat_completions" },
  { key: "openai-responses", label: "OpenAI Responses", ecosystem: "openai", openAiMode: "openai_responses" },
  { key: "anthropic", label: "Anthropic", ecosystem: "anthropic" },
  { key: "gemini", label: "Gemini", ecosystem: "gemini" },
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

function formatThinkingSummary(modes: ThinkingMode[], defaultThinking: ThinkingMode): string {
  if (modes.length === 1 && modes[0] === "none") return "关闭思考";
  const defLabel = THINKING_LABELS[defaultThinking] ?? defaultThinking;
  return defaultThinking === "none" ? "支持思考（默认关闭）" : `支持思考（默认${defLabel}）`;
}

function balanceTooltip(balance: AdminProviderBalance): string | undefined {
  if (balance.status !== "ok") return balance.notice || undefined;
  const parts: string[] = [];
  if (balance.topped_up_balance != null) parts.push(`充值 ${balance.topped_up_balance}`);
  if (balance.granted_balance != null) parts.push(`赠送/额度 ${balance.granted_balance}`);
  if (balance.used_amount != null) parts.push(`已用 ${balance.used_amount}`);
  return parts.length ? parts.join(" · ") : undefined;
}

function scopeDisplayLabel(scope: string): string {
  if (scope === "admin_only") return "仅管理员";
  if (scope === "allowlist") return "按分组授权";
  return "全员默认";
}

function guessIsDomesticModel(upstreamId: string, providerPreset?: string): boolean {
  const lower = upstreamId.toLowerCase();
  const tail = lower.split("/").pop() ?? lower;
  const overseas = [
    "claude", "gpt-", "gpt4", "gpt5", "chatgpt", "o1-", "o3-", "o4-",
    "gemini", "grok", "llama", "mistral", "mixtral", "sonar", "perplexity",
    "command-r", "anthropic", "openai/",
  ];
  if (tail === "o1" || tail === "o3" || tail === "o4" || overseas.some(t => lower.includes(t))) {
    return false;
  }
  const domestic = [
    "deepseek", "qwen", "qwq", "tongyi", "glm", "chatglm", "zhipu",
    "moonshot", "kimi", "doubao", "ernie", "wenxin", "hunyuan",
    "minimax", "abab", "baichuan", "yi-", "step-", "spark", "xinghuo",
    "internlm", "sensechat",
  ];
  if (domestic.some(t => lower.includes(t))) return true;
  return providerPreset === "deepseek" || providerPreset === "volcengine";
}

interface ModelModalState {
  mode: "create" | "edit";
  providerId: string;
  model?: AdminModelProfile;
  prefillUpstreamId?: string;
  prefillLabel?: string;
  prefillSpec?: InferredModelSpec;
}

export default function AdminModelsPage() {
  const { token } = useAdminOutlet();
  const api = useMemo(() => createAdminApi(token), [token]);
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const initialParamProvider = searchParams.get("provider") || "";

  const [selectedProviderId, setSelectedProviderId] = useState<string>(initialParamProvider);
  const [creatingProvider, setCreatingProvider] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [modelModal, setModelModal] = useState<ModelModalState | null>(null);
  const [discoverTarget, setDiscoverTarget] = useState<AdminModelProvider | null>(null);
  const [archiveTarget, setArchiveTarget] = useState<{ kind: "provider" | "model"; id: string; label: string } | null>(null);
  const [domesticPreview, setDomesticPreview] = useState<AdminDomesticDefaultResult | null>(null);
  const [domesticBusy, setDomesticBusy] = useState(false);
  const [defaultSwitching, setDefaultSwitching] = useState(false);
  const [pageBanner, setPageBanner] = useState<{ tone: "ok" | "fail"; text: string } | null>(null);

  const directoryQuery = useQuery({
    queryKey: ["admin", "model-directory"],
    queryFn: ({ signal }) => api.modelDirectory(signal),
    retry: false,
    placeholderData: previous => previous,
  });

  const providers = useMemo(() => directoryQuery.data?.providers ?? [], [directoryQuery.data]);
  const models = useMemo(() => directoryQuery.data?.models ?? [], [directoryQuery.data]);
  const localHostsAllowed = Boolean(directoryQuery.data?.local_hosts_allowed);
  const openDefaultModels = useMemo(
    () => models.filter(m => m.enabled && m.access_scope === "all"),
    [models],
  );
  const activeDefaultModel = useMemo(
    () => models.find(m => m.is_default),
    [models],
  );

  async function refreshDirectory() {
    await queryClient.invalidateQueries({ queryKey: ["admin", "model-directory"] });
    await queryClient.invalidateQueries({ queryKey: ["workspace", "models"] });
  }

  async function switchGlobalDefault(profileId: string) {
    if (!profileId || defaultSwitching) return;
    setDefaultSwitching(true);
    setPageBanner(null);
    try {
      const updated = await api.updateModel(profileId, { is_default: true, enabled: true });
      await refreshDirectory();
      setPageBanner({ tone: "ok", text: `全站默认模型已切换为「${updated.label}」。` });
    } catch (error) {
      setPageBanner({ tone: "fail", text: error instanceof Error ? error.message : "切换默认模型失败。" });
    } finally {
      setDefaultSwitching(false);
    }
  }

  async function previewDomesticDefault() {
    if (domesticBusy) return;
    setDomesticBusy(true);
    setPageBanner(null);
    try {
      const result = await api.applyDomesticDefault(true);
      setDomesticPreview(result);
    } catch (error) {
      setPageBanner({ tone: "fail", text: error instanceof Error ? error.message : "预览默认策略失败。" });
    } finally {
      setDomesticBusy(false);
    }
  }

  async function confirmDomesticDefault() {
    if (domesticBusy) return;
    setDomesticBusy(true);
    try {
      const result = await api.applyDomesticDefault(false);
      setDomesticPreview(null);
      await refreshDirectory();
      setPageBanner({
        tone: "ok",
        text: `已调整 ${result.changes.length} 个模型范围，全局默认开放 ${result.domestic_open_count} 个国内主流模型。`,
      });
    } catch (error) {
      setDomesticPreview(null);
      setPageBanner({ tone: "fail", text: error instanceof Error ? error.message : "应用策略失败。" });
    } finally {
      setDomesticBusy(false);
    }
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
      setPageBanner({ tone: "ok", text: target.kind === "provider" ? "服务商已归档。" : "模型已归档。" });
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

  function openAddModel(providerId?: string, discovered?: AdminDiscoveredModel) {
    setPageBanner(null);
    const prefillSpec: InferredModelSpec | undefined = discovered?.context_tokens
      ? {
          context_tokens: discovered.context_tokens,
          max_output_tokens: discovered.max_output_tokens ?? 16384,
          thinking_modes: discovered.thinking_modes?.length ? discovered.thinking_modes : ["none"],
          default_thinking: discovered.default_thinking ?? "none",
          matched: Boolean(discovered.inferred_matched),
          source_hint: discovered.inferred_source || "通用默认值",
        }
      : undefined;
    setModelModal({
      mode: "create",
      providerId: providerId || activeProvider?.id || providers[0]?.id || "",
      prefillUpstreamId: discovered?.id,
      prefillLabel: discovered ? (discovered.label || discovered.id) : undefined,
      prefillSpec,
    });
  }

  const activeModels = activeProvider ? modelsByProvider.get(activeProvider.id) ?? [] : [];

  return (
    <div className="admin-route-page admin-cc-models">
      <PageHeader
        title="模型"
        action={
          <div className="admin-cc-header-actions">
            <RefreshButton loading={directoryQuery.isFetching} onClick={() => void directoryQuery.refetch()} />
            <button type="button" className="admin-primary-button" onClick={openNewProvider}>
              <Plus size={15} />
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
        <InitialLoading label="正在读取模型…" />
      ) : !directoryQuery.data ? (
        <LoadError
          message={directoryQuery.error instanceof Error ? directoryQuery.error.message : "模型没有加载出来。"}
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

          {models.length > 0 && (
            <section className="admin-model-policy-bar" aria-label="默认模型策略">
              <div className="admin-policy-primary">
                <span className="admin-policy-label">
                  <Star size={14} aria-hidden="true" />
                  全站默认
                </span>
                {openDefaultModels.length > 0 ? (
                  <Select
                    value={activeDefaultModel?.access_scope === "all" && activeDefaultModel.enabled ? activeDefaultModel.profile_id : ""}
                    disabled={defaultSwitching}
                    onValueChange={val => void switchGlobalDefault(val)}
                  >
                    <SelectTrigger className="admin-policy-select" aria-label="全站默认模型">
                      <SelectValue placeholder={activeDefaultModel ? activeDefaultModel.label : "选择默认模型"} />
                    </SelectTrigger>
                    <SelectContent className="admin-select-menu">
                      {openDefaultModels.map(item => (
                        <SelectItem key={item.profile_id} value={item.profile_id}>
                          {item.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                ) : (
                  <span className="admin-cell-muted">暂无全员开放的已启用模型</span>
                )}
              </div>

              <div className="admin-policy-open-list">
                <span className="admin-policy-label">默认开放</span>
                <strong className="admin-policy-count">{openDefaultModels.length} 个</strong>
                {openDefaultModels.slice(0, 3).map(item => (
                  <span key={item.profile_id} className="admin-cc-badge">{item.label}</span>
                ))}
                {openDefaultModels.length > 3 && (
                  <span
                    className="admin-cc-badge"
                    title={openDefaultModels.slice(3).map(item => item.label).join("、")}
                  >
                    +{openDefaultModels.length - 3}
                  </span>
                )}
              </div>

              <button
                type="button"
                className="admin-cc-btn admin-policy-action"
                disabled={domesticBusy}
                onClick={() => void previewDomesticDefault()}
              >
                <ShieldCheck size={14} aria-hidden="true" />
                {domesticBusy ? "检查中…" : "国内主流模型设为全局默认"}
              </button>
            </section>
          )}

          <nav className="admin-cc-tabs" aria-label="服务商">
            {providers.map(provider => {
              const list = modelsByProvider.get(provider.id) ?? [];
              const openCount = list.filter(m => m.enabled).length;
              const selected = activeProvider?.id === provider.id;
              const dotClass = !provider.enabled ? "is-off" : openCount > 0 ? "is-live" : "is-idle";
              return (
                <button
                  type="button"
                  key={provider.id}
                  className={`admin-cc-tab ${selected ? "is-active" : ""}`}
                  aria-current={selected ? "true" : undefined}
                  onClick={() => selectProvider(provider.id)}
                >
                  <span className={`admin-cc-dot ${dotClass}`} aria-hidden="true" />
                  <strong>{provider.name}</strong>
                  <small>{list.length === 0 ? "无模型" : `${openCount}/${list.length}`}</small>
                </button>
              );
            })}
            <span className="admin-cc-tabs-total">{models.filter(m => m.enabled).length}/{models.length} 个模型已启用</span>
          </nav>

          {activeProvider ? (
            <ProviderModelsSection
              key={activeProvider.id}
              api={api}
              provider={activeProvider}
              models={activeModels}
              onOpenSettings={() => setSettingsOpen(true)}
              onOpenDiscover={() => setDiscoverTarget(activeProvider)}
              onAddModel={() => openAddModel(activeProvider.id)}
              onEditModel={model => setModelModal({ mode: "edit", providerId: activeProvider.id, model })}
              onRequestArchive={model => setArchiveTarget({ kind: "model", id: model.profile_id, label: model.label })}
              onDirectoryChanged={refreshDirectory}
              onBanner={setPageBanner}
            />
          ) : (
            <div className="admin-empty-state">
              <p>还没有服务商，先添加一个。</p>
              <button type="button" className="admin-primary-button" onClick={openNewProvider}>
                <Plus size={15} />
                添加服务商
              </button>
            </div>
          )}
        </>
      )}

      <Sheet
        open={creatingProvider || (settingsOpen && Boolean(activeProvider))}
        onOpenChange={open => {
          if (!open) {
            setCreatingProvider(false);
            setSettingsOpen(false);
          }
        }}
      >
        <SheetContent side="right" className="admin-dialog admin-drawer admin-provider-sheet">
          <SheetTitle className="sr-only">{creatingProvider ? "添加服务商" : "连接设置"}</SheetTitle>
          <SheetDescription className="sr-only">服务商的接口格式、地址和 API Key。</SheetDescription>
          <div className="admin-drawer-body">
            {creatingProvider || !activeProvider ? (
              <ProviderConnectionCard
                key="new-provider-card"
                api={api}
                localHostsAllowed={localHostsAllowed}
                onSaved={async saved => {
                  setCreatingProvider(false);
                  setSelectedProviderId(saved.id);
                  setPageBanner({ tone: "ok", text: `已添加「${saved.name}」，接着添加模型。` });
                  await refreshDirectory();
                }}
                onCancel={() => setCreatingProvider(false)}
              />
            ) : (
              <ProviderConnectionCard
                key={`edit-${activeProvider.id}-${activeProvider.revision}`}
                api={api}
                provider={activeProvider}
                localHostsAllowed={localHostsAllowed}
                onSaved={async saved => {
                  setSettingsOpen(false);
                  setPageBanner({ tone: "ok", text: `已保存「${saved.name}」。` });
                  await refreshDirectory();
                }}
                onRequestArchive={() => {
                  setSettingsOpen(false);
                  setArchiveTarget({ kind: "provider", id: activeProvider.id, label: activeProvider.name });
                }}
              />
            )}
          </div>
        </SheetContent>
      </Sheet>

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
            openAddModel(pid, discovered);
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
                ? "它和下面的模型都会停用，历史对话不受影响。"
                : "用户不能再选这个模型，历史对话不受影响。"}
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

      <AlertDialog open={Boolean(domesticPreview)} onOpenChange={open => { if (!open && !domesticBusy) setDomesticPreview(null); }}>
        <AlertDialogContent className="admin-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle>将国内主流模型设为全局默认？</AlertDialogTitle>
            <AlertDialogDescription>
              DeepSeek、通义千问、GLM、Kimi、豆包等国内主流模型将设为「全员默认」；海外及其余模型将调整为「按分组授权」（已有分组或白名单不受影响，仅管理员模型保持不变）。
            </AlertDialogDescription>
          </AlertDialogHeader>
          {domesticPreview && (
            <div className="admin-policy-preview">
              <p className="admin-policy-preview-meta">
                全站默认首选：<strong>{domesticPreview.default_profile_label || "暂无已启用中国主流模型"}</strong>
              </p>
              {domesticPreview.changes.length === 0 ? (
                <p className="admin-drawer-note">所有模型的可见范围已符合该策略，无需改动。</p>
              ) : (
                <ul className="admin-policy-change-list">
                  {domesticPreview.changes.map(change => (
                    <li key={change.profile_id}>
                      <span>{change.label}</span>
                      <small>
                        {scopeDisplayLabel(change.previous_scope)} → <strong>{scopeDisplayLabel(change.target_scope)}</strong>
                      </small>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel className="admin-dialog-cancel" disabled={domesticBusy}>取消</AlertDialogCancel>
            <AlertDialogAction
              className="admin-dialog-confirm"
              disabled={domesticBusy}
              onClick={event => {
                event.preventDefault();
                void confirmDomesticDefault();
              }}
            >
              {domesticBusy ? "应用中…" : "确认应用"}
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
  onRequestArchive,
}: {
  api: ReturnType<typeof createAdminApi>;
  provider?: AdminModelProvider;
  localHostsAllowed: boolean;
  onSaved: (provider: AdminModelProvider) => Promise<void>;
  onCancel?: () => void;
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
        error_code: cause instanceof AdminApiError ? cause.code : "unavailable",
      });
    } finally {
      setProbing(false);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    if (creating && !noKeyLocal && !apiKey.trim()) {
      setError("请填写 API Key；本地服务可以勾选“不用 Key”。");
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
      setError(cause instanceof Error ? cause.message : "没有保存成功。");
    } finally {
      setSaving(false);
    }
  }

  const keyPlaceholder = creating
    ? "sk-…"
    : provider.api_key_hint
      ? `已保存 ${provider.api_key_hint}，留空不改`
      : provider.credential_mode === "env_ref" && provider.credential_ref
        ? `正在用环境变量 ${provider.credential_ref}，填写后改用新 Key`
        : provider.credential_mode === "file_ref"
          ? "正在用系统内置 Key，填写后改用新 Key"
          : "填写新的 API Key";
  const activeFormat = ecosystem === "openai" ? `openai-${openAiMode === "openai_responses" ? "responses" : "chat"}` : ecosystem;

  return (
    <form className="admin-cc-card" onSubmit={e => void submit(e)}>
      <div className="admin-cc-card-head">
        <div>
          <div className="admin-cc-title-line">
            <Server size={17} />
            <h2>{creating ? "添加服务商" : provider.name}</h2>
            {!creating && (
              <span className={`admin-cc-pill ${provider.credential_configured ? "is-ok" : "is-warn"}`}>
                {provider.credential_configured ? "Key 已配置" : "缺少 Key"}
              </span>
            )}
          </div>
          {!creating && (
            <p className="admin-cc-subline">
              标识 <code>{provider.id}</code>
              {provider.api_key_hint ? ` · Key 尾号 ${provider.api_key_hint}` : ""}
            </p>
          )}
        </div>

        {!creating && (
          <div className="admin-cc-head-actions">
            <button type="button" className="admin-cc-btn" disabled={probing} onClick={() => void runTest()}>
              {probing ? <LoaderCircle className="admin-spin" size={14} /> : <FlaskConical size={14} />}
              测试连接
            </button>
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
        <div className="admin-cc-templates" role="group" aria-label="常用服务商">
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
              ? `连接正常${probeResult.latency_ms != null ? ` · ${probeResult.latency_ms} ms` : ""}`
              : `连接失败：${probeErrorText(probeResult.error_code)}`}
          </span>
        </div>
      )}

      {/* 所有字段共用一套两列栅格：左右两列的输入框上下对齐。 */}
      <div className="admin-cc-form-grid">
        <label className="admin-cc-field">
          <span>名称</span>
          <input name="name" required placeholder="例如 硅基流动" value={name} onChange={e => setName(e.target.value)} />
        </label>
        <label className="admin-cc-field">
          <span>备注<i>选填</i></span>
          <input name="notes" placeholder="例如 公司主用" value={notes} onChange={e => setNotes(e.target.value)} />
        </label>

        <div className="admin-cc-field is-wide">
          <span id="admin-provider-format">接口格式</span>
          <div className="admin-segment" role="radiogroup" aria-labelledby="admin-provider-format">
            {API_FORMATS.map(item => (
              <button
                type="button"
                key={item.key}
                role="radio"
                aria-checked={activeFormat === item.key}
                className={activeFormat === item.key ? "is-active" : ""}
                onClick={() => {
                  setEcosystem(item.ecosystem);
                  if (item.openAiMode) setOpenAiMode(item.openAiMode);
                }}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>

        <label className="admin-cc-field">
          <span>Base URL</span>
          <input
            name="base_url"
            type="url"
            required
            placeholder="https://api.example.com/v1"
            value={baseUrl}
            onChange={e => setBaseUrl(e.target.value)}
          />
        </label>
        <div className="admin-cc-field">
          <div className="admin-cc-label-row">
            <label htmlFor="admin-provider-key">API Key</label>
            <label className="admin-cc-check">
              <input type="checkbox" checked={noKeyLocal} onChange={e => setNoKeyLocal(e.target.checked)} />
              <span>本地服务，不用 Key</span>
            </label>
          </div>
          <div className="admin-cc-secret-wrap">
            <input
              id="admin-provider-key"
              name="api_key"
              type={showKey ? "text" : "password"}
              autoComplete="new-password"
              disabled={noKeyLocal}
              placeholder={noKeyLocal ? "不用填写" : keyPlaceholder}
              value={noKeyLocal ? "" : apiKey}
              onChange={e => setApiKey(e.target.value)}
            />
            <button
              type="button"
              disabled={noKeyLocal}
              aria-label={showKey ? "隐藏 Key" : "显示 Key"}
              onClick={() => setShowKey(v => !v)}
            >
              {showKey ? <EyeOff size={15} /> : <Eye size={15} />}
            </button>
          </div>
        </div>

        {localUrlWarn && (
          <p className="admin-cc-hint is-warn is-wide">
            服务端没有放行本机地址，需要先设置 <code>ZHOUXU_ALLOW_LOCAL_MODEL_HOSTS=1</code>。
          </p>
        )}

        {creating && (
          <div className="admin-cc-slug-row is-wide">
            <span>标识 <code>{effectiveSlug}</code></span>
            <button type="button" className="admin-cc-inline-link" onClick={() => setShowSlugEdit(v => !v)}>
              {showSlugEdit ? "收起" : "修改"}
            </button>
            {showSlugEdit && (
              <input
                name="id"
                aria-label="自定义标识"
                pattern="[A-Za-z0-9][A-Za-z0-9._:\-]*"
                placeholder="字母、数字和连字符"
                value={customSlug}
                onChange={e => setCustomSlug(e.target.value)}
              />
            )}
          </div>
        )}
      </div>

      <div className="admin-cc-card-foot">
        <label className="admin-model-toggle">
          <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />
          <span />
          <b>启用</b>
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
            保存
          </button>
        </div>
      </div>
    </form>
  );
}

type ModelFilter = "all" | "enabled" | "disabled" | "unverified";
const MODEL_FILTERS: Array<{ key: ModelFilter; label: string }> = [
  { key: "all", label: "全部" },
  { key: "enabled", label: "已启用" },
  { key: "disabled", label: "已停用" },
  { key: "unverified", label: "未测通" },
];

function ProviderModelsSection({
  api,
  provider,
  models,
  onOpenSettings,
  onOpenDiscover,
  onAddModel,
  onEditModel,
  onRequestArchive,
  onDirectoryChanged,
  onBanner,
}: {
  api: ReturnType<typeof createAdminApi>;
  provider: AdminModelProvider;
  models: AdminModelProfile[];
  onOpenSettings: () => void;
  onOpenDiscover: () => void;
  onAddModel: () => void;
  onEditModel: (model: AdminModelProfile) => void;
  onRequestArchive: (model: AdminModelProfile) => void;
  onDirectoryChanged: () => Promise<void>;
  onBanner: (banner: { tone: "ok" | "fail"; text: string } | null) => void;
}) {
  const [busyId, setBusyId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<ModelFilter>("all");
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [batchBusy, setBatchBusy] = useState(false);
  const [balanceLoading, setBalanceLoading] = useState(false);
  const [balance, setBalance] = useState<AdminProviderBalance | null>(null);

  async function runBalanceCheck() {
    setBalanceLoading(true);
    try {
      const res = await api.providerBalance(provider.id);
      setBalance(res);
    } catch (cause) {
      setBalance({
        status: "unreachable",
        supported: false,
        display: null,
        currency: null,
        total_balance: null,
        granted_balance: null,
        topped_up_balance: null,
        used_amount: null,
        source: null,
        notice: cause instanceof Error ? cause.message : "余额查询未完成。",
      });
    } finally {
      setBalanceLoading(false);
    }
  }

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return models
      .filter(model => {
        if (filter === "enabled") return model.enabled;
        if (filter === "disabled") return !model.enabled;
        if (filter === "unverified") return model.connectivity_status !== "verified";
        return true;
      })
      .filter(model => !needle || model.label.toLowerCase().includes(needle) || model.upstream_model_id.toLowerCase().includes(needle))
      .sort((a, b) => Number(b.is_default) - Number(a.is_default));
  }, [models, query, filter]);
  const chosen = visible.filter(model => selected.has(model.profile_id));
  const allChosen = visible.length > 0 && chosen.length === visible.length;

  function toggleChosen(id: string) {
    setSelected(previous => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAll() {
    setSelected(allChosen ? new Set() : new Set(visible.map(model => model.profile_id)));
  }

  async function runModelTest(model: AdminModelProfile) {
    setBusyId(model.profile_id);
    onBanner(null);
    try {
      const res = await api.testModel(model.profile_id);
      await onDirectoryChanged();
      onBanner(
        res.ok
          ? { tone: "ok", text: `「${model.label}」测试通过${res.latency_ms != null ? `（${res.latency_ms} ms）` : ""}，可以启用了。` }
          : { tone: "fail", text: `「${model.label}」没测通：${probeErrorText(res.error_code)}` },
      );
    } catch (cause) {
      onBanner({ tone: "fail", text: cause instanceof Error ? cause.message : "测试没有完成。" });
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
        text: !model.enabled ? `已启用「${model.label}」。` : `已停用「${model.label}」。`,
      });
    } catch (cause) {
      onBanner({ tone: "fail", text: cause instanceof Error ? cause.message : "没有改成功。" });
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
      onBanner({ tone: "ok", text: `「${model.label}」已设为默认模型。` });
    } catch (cause) {
      onBanner({ tone: "fail", text: cause instanceof Error ? cause.message : "没有改成功。" });
    } finally {
      setBusyId(null);
    }
  }

  async function changeAccessScope(model: AdminModelProfile, scope: "all" | "allowlist" | "admin_only") {
    if (model.access_scope === scope) return;
    setBusyId(model.profile_id);
    onBanner(null);
    try {
      await api.updateModel(model.profile_id, { access_scope: scope });
      await onDirectoryChanged();
      onBanner({ tone: "ok", text: `「${model.label}」已设为${scopeDisplayLabel(scope)}。` });
    } catch (cause) {
      onBanner({ tone: "fail", text: cause instanceof Error ? cause.message : "权限范围未改成功。" });
    } finally {
      setBusyId(null);
    }
  }

  /* 批量操作逐个执行：某一个失败不影响其余，最后一次说清楚结果。 */
  async function runBatch(kind: "enable" | "disable" | "test") {
    const targets = chosen.filter(model => {
      if (kind === "enable") return !model.enabled && model.connectivity_status === "verified";
      if (kind === "disable") return model.enabled && !model.is_default;
      return true;
    });
    const skipped = chosen.length - targets.length;
    if (targets.length === 0) {
      onBanner({
        tone: "fail",
        text: kind === "enable" ? "所选模型要么已启用，要么还没测通。" : "所选模型没有可停用的（默认模型不能批量停用）。",
      });
      return;
    }
    setBatchBusy(true);
    onBanner(null);
    let done = 0;
    let failed = 0;
    for (const model of targets) {
      try {
        if (kind === "test") {
          const res = await api.testModel(model.profile_id);
          if (res.ok) done += 1;
          else failed += 1;
        } else {
          await api.updateModel(model.profile_id, { enabled: kind === "enable" });
          done += 1;
        }
      } catch {
        failed += 1;
      }
    }
    await onDirectoryChanged();
    setBatchBusy(false);
    const verb = kind === "enable" ? "启用" : kind === "disable" ? "停用" : "测通";
    onBanner({
      tone: failed === 0 ? "ok" : "fail",
      text: `${verb} ${done} 个${failed ? `，${failed} 个没成功` : ""}${skipped ? `，跳过 ${skipped} 个不适用的` : ""}。`,
    });
    if (kind !== "test") setSelected(new Set());
  }

  return (
    <section className="admin-cc-card admin-cc-models-card" aria-label={`${provider.name} 的模型`}>
      <header className="admin-cc-models-head">
        <div>
          <div className="admin-cc-title-line">
            <h2>{provider.name}</h2>
            <span className={`admin-cc-pill ${provider.credential_configured ? "is-ok" : "is-warn"}`}>
              {provider.credential_configured ? "Key 已配置" : "缺少 Key"}
            </span>
            {balance && (
              <span
                className={`admin-cc-pill ${balance.status === "ok" ? "is-ok" : balance.status === "unsupported" ? "" : "is-warn"}`}
                title={balanceTooltip(balance)}
              >
                {balance.status === "ok"
                  ? `余额 ${balance.display}`
                  : balance.status === "unsupported"
                    ? "未开放余额查询"
                    : "余额查询失败"}
              </span>
            )}
            {!provider.enabled && <span className="admin-cc-pill">已停用</span>}
            <span className="admin-cc-count">{models.length}</span>
          </div>
          {balance && balance.status !== "ok" && balance.notice && (
            <p className="admin-cc-balance-note">{balance.notice}</p>
          )}
        </div>
        <div className="admin-cc-head-actions">
          <button
            type="button"
            className="admin-cc-btn"
            disabled={balanceLoading || !provider.credential_configured}
            title={provider.credential_configured ? "向服务商查询当前账户余额" : "请先配置 API Key"}
            onClick={() => void runBalanceCheck()}
          >
            {balanceLoading ? <LoaderCircle className="admin-spin" size={14} /> : <Wallet size={14} />}
            {balance?.status === "ok" ? "刷新余额" : "查余额"}
          </button>
          <button type="button" className="admin-cc-btn" onClick={onOpenSettings}>
            <Settings2 size={14} />
            连接设置
          </button>
          <button type="button" className="admin-cc-btn" onClick={onOpenDiscover}>
            <Download size={14} />
            拉取模型
          </button>
          <button type="button" className="admin-cc-btn" onClick={onAddModel}>
            <Plus size={14} />
            添加模型
          </button>
        </div>
      </header>

      {models.length === 0 ? (
        <p className="admin-empty">还没有模型，可以拉取或手动添加。</p>
      ) : (
        <>
          <div className="admin-cc-toolbar">
            <label className="admin-cc-search">
              <Search size={14} aria-hidden="true" />
              <input
                type="search"
                aria-label="搜索模型"
                placeholder="搜索模型名称"
                value={query}
                onChange={event => setQuery(event.target.value)}
              />
            </label>
            <div className="admin-segment" role="radiogroup" aria-label="筛选模型">
              {MODEL_FILTERS.map(item => (
                <button
                  type="button"
                  key={item.key}
                  role="radio"
                  aria-checked={filter === item.key}
                  className={filter === item.key ? "is-active" : ""}
                  onClick={() => setFilter(item.key)}
                >
                  {item.label}
                </button>
              ))}
            </div>
          </div>

          {chosen.length > 0 && (
            <div className="admin-cc-batchbar" role="status">
              <span>已选 {chosen.length} 个</span>
              <button type="button" className="admin-cc-btn" disabled={batchBusy} onClick={() => void runBatch("enable")}>启用</button>
              <button type="button" className="admin-cc-btn" disabled={batchBusy} onClick={() => void runBatch("disable")}>停用</button>
              <button type="button" className="admin-cc-btn" disabled={batchBusy} onClick={() => void runBatch("test")}>
                {batchBusy ? <LoaderCircle className="admin-spin" size={14} /> : <FlaskConical size={14} />}
                测试
              </button>
              <button type="button" className="admin-cc-inline-link" disabled={batchBusy} onClick={() => setSelected(new Set())}>取消选择</button>
            </div>
          )}

          {visible.length === 0 ? (
            <p className="admin-empty">没有符合条件的模型。</p>
          ) : (
            <div className="admin-cc-table-wrap">
              <table className="admin-cc-table">
                <thead>
                  <tr>
                    <th className="admin-cc-col-check">
                      <input type="checkbox" aria-label="全选" checked={allChosen} onChange={toggleAll} />
                    </th>
                    <th>模型</th>
                    <th>接口</th>
                    <th className="admin-cc-col-wide">上下文 / 输出</th>
                    <th className="admin-cc-col-wide">思考档位</th>
                    <th>状态</th>
                    <th>启用</th>
                    <th className="admin-cc-col-actions"><span className="sr-only">操作</span></th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map(model => {
                    const busy = busyId === model.profile_id || batchBusy;
                    const verified = model.connectivity_status === "verified";
                    const scopeTone =
                      model.access_scope === "admin_only"
                        ? "is-admin"
                        : model.access_scope === "allowlist"
                          ? "is-allowlist"
                          : "is-all";
                    return (
                      <tr key={model.profile_id} className={model.enabled ? "is-enabled" : ""}>
                        <td className="admin-cc-col-check">
                          <input
                            type="checkbox"
                            aria-label={`选择 ${model.label}`}
                            checked={selected.has(model.profile_id)}
                            onChange={() => toggleChosen(model.profile_id)}
                          />
                        </td>
                        <td>
                          <div className="admin-cc-cell-title">
                            <strong>{model.label}</strong>
                            {model.is_default && (
                              <span className="admin-cc-badge is-default">
                                <Star size={11} />
                                默认
                              </span>
                            )}
                            {model.capabilities?.vision === "confirmed" && (
                              <span className="admin-cc-badge" title="支持原图直传看图">
                                <Eye size={11} aria-hidden="true" />
                                看图
                              </span>
                            )}
                            <DropdownMenu.Root>
                              <DropdownMenu.Trigger asChild>
                                <button
                                  type="button"
                                  className={`admin-scope-pill ${scopeTone}`}
                                  disabled={busy}
                                  title="点击快速调整可用范围"
                                >
                                  <span>{scopeDisplayLabel(model.access_scope)}</span>
                                  <ChevronDown size={11} aria-hidden="true" />
                                </button>
                              </DropdownMenu.Trigger>
                              <DropdownMenu.Portal>
                                <DropdownMenu.Content className="admin-scope-menu" sideOffset={5} align="start">
                                  {(["all", "allowlist", "admin_only"] as const).map(scope => (
                                    <DropdownMenu.Item
                                      key={scope}
                                      className={`admin-scope-menu-item ${model.access_scope === scope ? "is-selected" : ""}`}
                                      onSelect={() => void changeAccessScope(model, scope)}
                                    >
                                      <span>{scopeDisplayLabel(scope)}</span>
                                      {model.access_scope === scope && <Check size={13} aria-hidden="true" />}
                                    </DropdownMenu.Item>
                                  ))}
                                </DropdownMenu.Content>
                              </DropdownMenu.Portal>
                            </DropdownMenu.Root>
                          </div>
                          {model.connectivity_status === "failed" && model.connectivity_error && (
                            <p className="admin-cc-model-fail">{probeErrorText(model.connectivity_error)}</p>
                          )}
                        </td>
                        <td className="admin-cc-cell-muted">{protocolDisplayName(model.protocol)}</td>
                        <td className="admin-cc-cell-muted admin-cc-col-wide">
                          {formatTokenBudget(model.context_tokens)} / {formatTokenBudget(model.max_output_tokens)}
                        </td>
                        <td className="admin-cc-cell-muted admin-cc-col-wide">
                          {model.thinking_modes.map(m => THINKING_LABELS[m] ?? m).join("、")}
                        </td>
                        <td>
                          <span className={`admin-cc-badge is-${model.connectivity_status}`}>
                            {verified
                              ? `已测通${model.connectivity_latency_ms != null ? ` ${model.connectivity_latency_ms}ms` : ""}`
                              : model.connectivity_status === "failed"
                                ? "没测通"
                                : "未测试"}
                          </span>
                        </td>
                        <td>
                          <label className="admin-model-toggle" title={verified || model.enabled ? undefined : "测通后才能启用"}>
                            <input
                              type="checkbox"
                              aria-label={`启用模型 ${model.label}`}
                              checked={model.enabled}
                              disabled={busy || (!model.enabled && !verified)}
                              onChange={() => void toggleEnabled(model)}
                            />
                            <span />
                          </label>
                        </td>
                        <td className="admin-cc-col-actions">
                          <div className="admin-cc-row-actions">
                            <button type="button" className="admin-cc-icon-btn" title="测试连接" aria-label={`测试 ${model.label}`} disabled={busy} onClick={() => void runModelTest(model)}>
                              {busyId === model.profile_id ? <LoaderCircle className="admin-spin" size={14} /> : <FlaskConical size={14} />}
                            </button>
                            {verified && model.enabled && !model.is_default && (
                              <button type="button" className="admin-cc-icon-btn" title="设为默认" aria-label={`设 ${model.label} 为默认`} disabled={busy} onClick={() => void setDefault(model)}>
                                <Star size={14} />
                              </button>
                            )}
                            <button type="button" className="admin-cc-icon-btn" title="编辑" aria-label={`编辑 ${model.label}`} disabled={busy} onClick={() => onEditModel(model)}>
                              <Pencil size={14} />
                            </button>
                            <button type="button" className="admin-cc-icon-btn is-danger" title="归档" aria-label={`归档模型 ${model.label}`} disabled={busy} onClick={() => onRequestArchive(model)}>
                              <Trash2 size={14} />
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
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

  const initialSpec: InferredModelSpec | null = !editing
    ? (modal.prefillSpec ?? inferModelSpecLocal(modal.prefillUpstreamId ?? ""))
    : null;

  // Core model fields ONLY (auto-inferred context/output/thinking tucked into collapsible Advanced panel)
  const [upstreamId, setUpstreamId] = useState(editing?.upstream_model_id ?? modal.prefillUpstreamId ?? "");
  const [label, setLabel] = useState(editing?.label ?? modal.prefillLabel ?? "");
  const [labelTouched, setLabelTouched] = useState(Boolean(editing || modal.prefillLabel));
  const [protocolChoice, setProtocolChoice] = useState<string>(editing?.protocol_override ?? "");
  const [contextTokens, setContextTokens] = useState(editing?.context_tokens ?? initialSpec?.context_tokens ?? 131072);
  const [maxOutputTokens, setMaxOutputTokens] = useState(editing?.max_output_tokens ?? initialSpec?.max_output_tokens ?? 16384);
  const [modes, setModes] = useState<ThinkingMode[]>(editing?.thinking_modes ?? initialSpec?.thinking_modes ?? ["none"]);
  const [defaultThinking, setDefaultThinking] = useState<ThinkingMode>(editing?.default_thinking ?? initialSpec?.default_thinking ?? "none");
  const [supportsVision, setSupportsVision] = useState<boolean>(
    editing ? editing.capabilities?.vision === "confirmed" : Boolean(initialSpec?.supports_vision),
  );
  const [inferredHint, setInferredHint] = useState(initialSpec?.source_hint ?? "通用默认值");
  const [inferredMatched, setInferredMatched] = useState(Boolean(initialSpec?.matched));
  const [advancedTouched, setAdvancedTouched] = useState(Boolean(editing));
  const [showAdvanced, setShowAdvanced] = useState(false);
  const initialProvider = providers.find(p => p.id === (editing?.provider_id || modal.providerId || providers[0]?.id));
  const [accessScope, setAccessScope] = useState(
    editing?.access_scope
      ?? (guessIsDomesticModel(modal.prefillUpstreamId ?? "", initialProvider?.preset_type) ? "all" : "allowlist"),
  );
  const [scopeTouched, setScopeTouched] = useState(Boolean(editing));
  const [testImmediately, setTestImmediately] = useState(!editing);

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedProvider = providers.find(p => p.id === providerId);
  const inheritedProtocol: ModelProtocol =
    providerMode === "new"
      ? resolveProtocol(newProvEco, newProvOpenAiMode)
      : selectedProvider?.default_protocol ?? "openai_chat_completions";

  function applySpec(spec: InferredModelSpec) {
    setContextTokens(spec.context_tokens);
    setMaxOutputTokens(spec.max_output_tokens);
    setModes(spec.thinking_modes);
    setDefaultThinking(spec.default_thinking);
    if (typeof spec.supports_vision === "boolean") {
      setSupportsVision(spec.supports_vision);
    }
    setInferredHint(spec.source_hint);
    setInferredMatched(spec.matched);
  }

  useEffect(() => {
    if (editing || advancedTouched) return;
    const trimmed = upstreamId.trim();
    if (!trimmed) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      void api
        .inferModel(trimmed, controller.signal)
        .then(res => {
          applySpec({
            context_tokens: res.context_tokens,
            max_output_tokens: res.max_output_tokens,
            thinking_modes: res.thinking_modes.length ? res.thinking_modes : ["none"],
            default_thinking: res.default_thinking,
            matched: res.matched,
            source_hint: res.source_hint,
            supports_vision: res.supports_vision,
          });
        })
        .catch(() => {
          // Local inference already populated safe values; ignore transient errors.
        });
    }, 220);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [api, editing, advancedTouched, upstreamId]);

  function handleUpstreamChange(next: string) {
    setUpstreamId(next);
    if (!labelTouched) {
      setLabel(next.split("/").pop() ?? next);
    }
    if (!editing && !advancedTouched) {
      applySpec(inferModelSpecLocal(next));
    }
    if (!editing && !scopeTouched) {
      setAccessScope(guessIsDomesticModel(next, selectedProvider?.preset_type) ? "all" : "allowlist");
    }
  }

  function resetAutoConfig() {
    setAdvancedTouched(false);
    setProtocolChoice("");
    applySpec(inferModelSpecLocal(upstreamId));
  }

  function toggleThinking(mode: ThinkingMode, checked: boolean) {
    const next = checked ? THINKING_MODES.filter(m => m === mode || modes.includes(m)) : modes.filter(m => m !== mode);
    if (!next.length) return;
    setAdvancedTouched(true);
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
          setError("请填写 API Key；本地服务可以勾选“不用 Key”。");
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
      const nextCapabilities = {
        ...(editing?.capabilities ?? {}),
        vision: supportsVision ? "confirmed" : "unsupported",
      };
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
          capabilities: nextCapabilities,
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
          capabilities: nextCapabilities,
          enabled: false,
        });
      }

      if (testImmediately) {
        const probe = await api.testModel(savedModel.profile_id);
        if (probe.ok) {
          await api.updateModel(savedModel.profile_id, { enabled: true });
          await onSaved(
            targetProviderId,
            `「${savedModel.label}」已测通${probe.latency_ms != null ? `（${probe.latency_ms} ms）` : ""}并启用。`,
          );
          return;
        }
        await onSaved(
          targetProviderId,
          `「${savedModel.label}」已保存，但没测通（${probeErrorText(probe.error_code)}），先保持停用。`,
        );
        return;
      }

      await onSaved(targetProviderId, editing ? `已保存「${savedModel.label}」。` : `已保存「${savedModel.label}」，测通后再启用。`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "没有保存成功。");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open onOpenChange={open => { if (!open && !saving) onClose(); }}>
      <DialogContent className="admin-dialog admin-cc-modal">
        <DialogHeader>
          <DialogTitle>{editing ? `编辑 ${editing.label}` : "添加模型"}</DialogTitle>
          <DialogDescription>
            {editing ? "改了模型 ID 或接口格式，要重新测通才能启用。" : "选好服务商，填模型 ID。"}
          </DialogDescription>
        </DialogHeader>

        <form className="admin-cc-modal-form" onSubmit={e => void submit(e)}>
          {!editing ? (
            <div className="admin-cc-provider-box">
              <div className="admin-cc-provider-box-head">
                <span><Link2 size={14} />服务商</span>
                {hasExistingProviders && (
                  <div className="admin-segment" role="radiogroup" aria-label="服务商来源">
                    <button
                      type="button"
                      role="radio"
                      aria-checked={providerMode === "existing"}
                      className={providerMode === "existing" ? "is-active" : ""}
                      onClick={() => setProviderMode("existing")}
                    >
                      已有
                    </button>
                    <button
                      type="button"
                      role="radio"
                      aria-checked={providerMode === "new"}
                      className={providerMode === "new" ? "is-active" : ""}
                      onClick={() => setProviderMode("new")}
                    >
                      新建
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
                    <span>Key：{selectedProvider.api_key_hint || (selectedProvider.credential_configured ? "已配置" : "未配置")}</span>
                  </div>
                </>
              ) : (
                <div className="admin-cc-form-grid">
                  <label className="admin-cc-field">
                    <span>名称</span>
                    <input required placeholder="例如 硅基流动" value={newProvName} onChange={e => setNewProvName(e.target.value)} />
                  </label>
                  <label className="admin-cc-field">
                    <span>备注<i>选填</i></span>
                    <input placeholder="例如 公司主用" value={newProvNotes} onChange={e => setNewProvNotes(e.target.value)} />
                  </label>
                  <label className="admin-cc-field is-wide">
                    <span>接口格式</span>
                    <select
                      value={newProvEco === "openai" ? `openai-${newProvOpenAiMode === "openai_responses" ? "responses" : "chat"}` : newProvEco}
                      onChange={e => {
                        const picked = API_FORMATS.find(item => item.key === e.target.value);
                        if (!picked) return;
                        setNewProvEco(picked.ecosystem);
                        if (picked.openAiMode) setNewProvOpenAiMode(picked.openAiMode);
                      }}
                    >
                      {API_FORMATS.map(item => <option key={item.key} value={item.key}>{item.label}</option>)}
                    </select>
                  </label>
                  <label className="admin-cc-field">
                    <span>Base URL</span>
                    <input type="url" required placeholder="https://api.example.com/v1" value={newProvUrl} onChange={e => setNewProvUrl(e.target.value)} />
                  </label>
                  <div className="admin-cc-field">
                    <div className="admin-cc-label-row">
                      <label htmlFor="admin-new-provider-key">API Key</label>
                      <label className="admin-cc-check">
                        <input type="checkbox" checked={newProvNoKey} onChange={e => setNewProvNoKey(e.target.checked)} />
                        <span>本地服务，不用 Key</span>
                      </label>
                    </div>
                    <input
                      id="admin-new-provider-key"
                      type="password"
                      autoComplete="new-password"
                      disabled={newProvNoKey}
                      placeholder={newProvNoKey ? "不用填写" : "sk-…"}
                      value={newProvNoKey ? "" : newProvKey}
                      onChange={e => setNewProvKey(e.target.value)}
                    />
                  </div>
                  {!localHostsAllowed && /^http:\/\/(?:127\.|localhost)/i.test(newProvUrl.trim()) && (
                    <p className="admin-cc-hint is-warn is-wide">服务端没有放行本机地址。</p>
                  )}
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
              <span>模型 ID</span>
              <input
                name="upstream_model_id"
                required
                placeholder="例如 deepseek-chat"
                value={upstreamId}
                onChange={e => handleUpstreamChange(e.target.value)}
              />
            </label>
            <label className="admin-cc-field">
              <span>显示名称</span>
              <input
                name="label"
                required
                placeholder="用户看到的名字"
                value={label}
                onChange={e => {
                  setLabelTouched(true);
                  setLabel(e.target.value);
                }}
              />
            </label>
          </div>

          <label className="admin-cc-field">
            <span>可见范围</span>
            <select
              name="access_scope"
              value={accessScope}
              onChange={e => {
                setScopeTouched(true);
                setAccessScope(e.target.value);
              }}
            >
              <option value="all">全员默认（所有用户）</option>
              <option value="allowlist">按分组 / 指定账户授权</option>
              <option value="admin_only">仅管理员</option>
            </select>
          </label>

          <div className="admin-cc-auto-card">
            <div className="admin-cc-auto-head">
              <div className="admin-cc-auto-summary">
                <Sparkles size={14} className="admin-cc-auto-icon" aria-hidden="true" />
                <div>
                  <strong>
                    {editing
                      ? "当前参数配置"
                      : advancedTouched
                        ? "已手动调整参数"
                        : inferredMatched
                          ? `已按「${inferredHint}」自动配置`
                          : "已套用通用默认参数"}
                  </strong>
                  <p>
                    {formatTokenBudget(contextTokens)} 上下文 · {formatTokenBudget(maxOutputTokens)} 输出 · {formatThinkingSummary(modes, defaultThinking)} · {supportsVision ? "支持看图" : "仅文本"}
                    {protocolChoice ? ` · ${protocolDisplayName(protocolChoice as ModelProtocol)}` : ""}
                  </p>
                </div>
              </div>
              <button
                type="button"
                className="admin-cc-btn admin-cc-auto-toggle"
                aria-expanded={showAdvanced}
                onClick={() => setShowAdvanced(v => !v)}
              >
                {showAdvanced ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                {showAdvanced ? "收起高级参数" : "高级参数"}
              </button>
            </div>

            {showAdvanced && (
              <div className="admin-cc-auto-body">
                <div className="admin-cc-auto-bar">
                  <span className="admin-cc-hint">普通接入保持自动配置即可；系统会按上下文长度自动压缩长对话。</span>
                  {!editing && advancedTouched && (
                    <button type="button" className="admin-cc-inline-link" onClick={resetAutoConfig}>
                      恢复自动配置
                    </button>
                  )}
                </div>

                <label className="admin-cc-field">
                  <span>接口格式覆盖</span>
                  <select
                    name="protocol_override"
                    value={protocolChoice}
                    onChange={e => {
                      setAdvancedTouched(true);
                      setProtocolChoice(e.target.value);
                    }}
                  >
                    <option value="">跟随服务商（{protocolDisplayName(inheritedProtocol)}）</option>
                    <option value="openai_chat_completions">OpenAI 兼容</option>
                    <option value="openai_responses">OpenAI Responses</option>
                    <option value="anthropic_messages">Anthropic</option>
                    <option value="google_generative_language">Gemini</option>
                  </select>
                </label>

                <div className="admin-cc-grid-2">
                  <div className="admin-cc-field">
                    <span>上下文长度<i>tokens</i></span>
                    <input
                      name="context_tokens"
                      type="number"
                      min={1024}
                      required
                      value={contextTokens}
                      onChange={e => {
                        setAdvancedTouched(true);
                        setContextTokens(Number(e.target.value));
                      }}
                    />
                    <div className="admin-cc-presets">
                      {CONTEXT_PRESETS.map(p => (
                        <button
                          type="button"
                          key={p.value}
                          className={contextTokens === p.value ? "is-active" : ""}
                          onClick={() => {
                            setAdvancedTouched(true);
                            setContextTokens(p.value);
                          }}
                        >
                          {p.label}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="admin-cc-field">
                    <span>最大输出<i>tokens</i></span>
                    <input
                      name="max_output_tokens"
                      type="number"
                      min={256}
                      required
                      value={maxOutputTokens}
                      onChange={e => {
                        setAdvancedTouched(true);
                        setMaxOutputTokens(Number(e.target.value));
                      }}
                    />
                    <div className="admin-cc-presets">
                      {OUTPUT_PRESETS.map(p => (
                        <button
                          type="button"
                          key={p.value}
                          className={maxOutputTokens === p.value ? "is-active" : ""}
                          onClick={() => {
                            setAdvancedTouched(true);
                            setMaxOutputTokens(p.value);
                          }}
                        >
                          {p.label}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>

                <div className="admin-cc-grid-2">
                  <fieldset className="admin-thinking-field">
                    <legend>思考档位</legend>
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
                    <span>默认档位</span>
                    <select
                      name="default_thinking"
                      value={defaultThinking}
                      onChange={e => {
                        setAdvancedTouched(true);
                        setDefaultThinking(e.target.value as ThinkingMode);
                      }}
                    >
                      {modes.map(mode => (
                        <option key={mode} value={mode}>{THINKING_LABELS[mode]}</option>
                      ))}
                    </select>
                  </label>
                </div>
                <label className="admin-cc-check">
                  <input
                    type="checkbox"
                    name="supports_vision"
                    checked={supportsVision}
                    onChange={e => {
                      setAdvancedTouched(true);
                      setSupportsVision(e.target.checked);
                    }}
                  />
                  <span>支持看图（开启后允许把用户上传的原图直接传给该模型）</span>
                </label>
                <p className="admin-cc-hint">不确定时不用改：普通模型保持“关闭”，支持推理的模型按需勾选档位即可。</p>
              </div>
            )}
          </div>

          <label className="admin-cc-check">
            <input type="checkbox" checked={testImmediately} onChange={e => setTestImmediately(e.target.checked)} />
            <span>保存后马上测试，通过就启用</span>
          </label>

          {error && <p className="admin-cc-error" role="alert">{error}</p>}

          <DialogFooter>
            <button type="button" className="admin-page-button" disabled={saving} onClick={onClose}>
              取消
            </button>
            <button type="submit" className="admin-primary-button" disabled={saving}>
              {saving ? <LoaderCircle className="admin-spin" size={15} /> : <Save size={15} />}
              {testImmediately ? "保存并测试" : "保存"}
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
          <DialogTitle>从「{provider.name}」拉取模型</DialogTitle>
          <DialogDescription>选一个，带入添加表单。</DialogDescription>
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
            <p className="admin-empty">正在拉取…</p>
          ) : query.isError ? (
            <p className="admin-empty">{query.error instanceof Error ? query.error.message : "没有拉取成功。"}</p>
          ) : !query.data?.supported ? (
            <p className="admin-empty">{query.data?.notice || "这个服务商不支持拉取，请手动添加模型。"}</p>
          ) : filtered.length === 0 ? (
            <p className="admin-empty">没有找到匹配的模型。</p>
          ) : (
            filtered.map(item => {
              const ctx = item.context_tokens ? formatTokenBudget(item.context_tokens) : "128K";
              const out = item.max_output_tokens ? formatTokenBudget(item.max_output_tokens) : "16K";
              const thinkSummary = formatThinkingSummary(
                item.thinking_modes?.length ? item.thinking_modes : ["none"],
                item.default_thinking ?? "none",
              );
              return (
                <div key={item.id} className="admin-discover-item">
                  <div>
                    <strong>{item.id}</strong>
                    <small>
                      {ctx} 上下文 · {out} 输出 · {thinkSummary}
                      {item.already_added ? " · 已添加" : ""}
                    </small>
                  </div>
                  <button type="button" className="admin-cc-btn" onClick={() => onPick(item)}>
                    {item.already_added ? "再加一个" : "选用"}
                  </button>
                </div>
              );
            })
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
