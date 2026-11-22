import { Check, ChevronRight, CirclePlus, Cloud, Cpu, LoaderCircle, Save } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import {
  createAdminApi,
  type AdminModelProfile,
  type AdminModelProvider,
  type ModelFamily,
  type ModelProtocol,
  type ThinkingMode,
} from "@/lib/admin-api";
import { InitialLoading, InlineError, LoadError, PageHeader, RefreshButton } from "./AdminShared";
import { useAdminOutlet } from "./AdminLayout";

const protocols: Array<{ value: ModelProtocol; label: string }> = [
  { value: "deepseek_chat_completions", label: "DeepSeek Chat" },
  { value: "openai_chat_completions", label: "OpenAI Chat" },
  { value: "openai_responses", label: "OpenAI Responses" },
  { value: "anthropic_messages", label: "Claude Messages" },
  { value: "google_generative_language", label: "Gemini" },
];
const families: Array<{ value: ModelFamily; label: string }> = [
  { value: "deepseek", label: "DeepSeek" }, { value: "gpt", label: "GPT" },
  { value: "claude", label: "Claude" }, { value: "gemini", label: "Gemini" },
  { value: "glm", label: "GLM" }, { value: "other", label: "其他" },
];
const thinkingModes: ThinkingMode[] = ["none", "low", "medium", "high", "xhigh", "max"];
const thinkingLabels: Record<ThinkingMode, string> = { none: "关闭", low: "低", medium: "中", high: "高", xhigh: "极高", max: "最高" };

export default function AdminModelsPage() {
  const { token } = useAdminOutlet();
  const api = useMemo(() => createAdminApi(token), [token]);
  const queryClient = useQueryClient();
  const [section, setSection] = useState<"models" | "providers">("models");
  const [selected, setSelected] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const query = useQuery({
    queryKey: ["admin", "model-directory"], queryFn: ({ signal }) => api.modelDirectory(signal),
    retry: false, placeholderData: previous => previous,
  });
  const save = useMutation({
    mutationFn: async (job: { kind: "provider" | "model"; id?: string; value: Record<string, unknown> }) => {
      if (job.kind === "provider") return job.id
        ? api.updateProvider(job.id, job.value)
        : api.createProvider(job.value as never);
      return job.id ? api.updateModel(job.id, job.value) : api.createModel(job.value as never);
    },
    onSuccess: async value => {
      setCreating(false);
      setSelected("profile_id" in value ? value.profile_id : value.id);
      await queryClient.invalidateQueries({ queryKey: ["admin", "model-directory"] });
      await queryClient.invalidateQueries({ queryKey: ["workspace", "models"] });
    },
  });
  const providers = query.data?.providers ?? [];
  const models = query.data?.models ?? [];
  useEffect(() => {
    if (creating || selected) return;
    const first = section === "models" ? models[0]?.profile_id : providers[0]?.id;
    if (first) setSelected(first);
  }, [creating, models, providers, section, selected]);
  function switchSection(next: "models" | "providers") { setSection(next); setCreating(false); setSelected(null); save.reset(); }
  function startCreate() { setCreating(true); setSelected(null); save.reset(); }
  const activeModel = !creating && section === "models" ? models.find(item => item.profile_id === selected) : undefined;
  const activeProvider = !creating && section === "providers" ? providers.find(item => item.id === selected) : undefined;

  return <div className="admin-route-page admin-model-page">
    <PageHeader eyebrow="运行设置" title="模型" description="调整后只影响新任务。" action={<RefreshButton loading={query.isFetching} onClick={() => void query.refetch()} />} />
    {!query.data && query.isPending ? <InitialLoading label="正在读取模型…" />
      : !query.data ? <LoadError message={query.error instanceof Error ? query.error.message : "模型暂时不可用。"} onRetry={() => void query.refetch()} />
      : <>
        {query.isError && <InlineError message={query.error instanceof Error ? query.error.message : "刷新没有完成。"} onRetry={() => void query.refetch()} />}
        <div className="admin-model-toolbar">
          <div className="admin-model-tabs" role="tablist" aria-label="模型设置">
            <button className={section === "models" ? "is-active" : ""} type="button" role="tab" aria-selected={section === "models"} onClick={() => switchSection("models")}>模型 <span>{models.length}</span></button>
            <button className={section === "providers" ? "is-active" : ""} type="button" role="tab" aria-selected={section === "providers"} onClick={() => switchSection("providers")}>服务 <span>{providers.length}</span></button>
          </div>
          <button className="admin-primary-button" type="button" onClick={startCreate}><CirclePlus size={16} />添加{section === "models" ? "模型" : "服务"}</button>
        </div>
        <div className="admin-model-workspace">
          <section className="admin-model-index" aria-label={section === "models" ? "模型列表" : "服务列表"}>
            {section === "models" ? models.map(item => <button type="button" key={item.profile_id} className={selected === item.profile_id && !creating ? "is-active" : ""} onClick={() => { setCreating(false); setSelected(item.profile_id); save.reset(); }}>
              <span className="admin-model-index-icon"><Cpu size={16} /></span><span><strong>{item.label}</strong><small>{item.upstream_model_id}</small></span><i className={item.enabled ? "is-on" : ""}>{item.enabled ? "启用" : "停用"}</i><ChevronRight size={15} />
            </button>) : providers.map(item => <button type="button" key={item.id} className={selected === item.id && !creating ? "is-active" : ""} onClick={() => { setCreating(false); setSelected(item.id); save.reset(); }}>
              <span className="admin-model-index-icon"><Cloud size={16} /></span><span><strong>{item.name}</strong><small>{item.base_url}</small></span><i className={item.enabled ? "is-on" : ""}>{item.enabled ? "启用" : "停用"}</i><ChevronRight size={15} />
            </button>)}
          </section>
          <section className="admin-model-editor">
            {section === "providers"
              ? <ProviderEditor key={creating ? "new-provider" : activeProvider?.id} provider={creating ? undefined : activeProvider} saving={save.isPending} error={save.error instanceof Error ? save.error.message : null} onSave={value => save.mutate({ kind: "provider", id: activeProvider?.id, value })} />
              : <ModelEditor key={creating ? "new-model" : activeModel?.profile_id} model={creating ? undefined : activeModel} providers={providers} saving={save.isPending} error={save.error instanceof Error ? save.error.message : null} onSave={value => save.mutate({ kind: "model", id: activeModel?.profile_id, value })} />}
          </section>
        </div>
      </>}
  </div>;
}

function ProviderEditor({ provider, saving, error, onSave }: { provider?: AdminModelProvider; saving: boolean; error: string | null; onSave: (value: Record<string, unknown>) => void }) {
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const data = new FormData(event.currentTarget);
    onSave({
      ...(provider ? {} : { id: data.get("id"), preset_type: "custom" }),
      name: data.get("name"), default_protocol: data.get("protocol"), auth_style: data.get("auth_style"),
      base_url: data.get("base_url"), credential_mode: data.get("credential_mode"), credential_ref: data.get("credential_ref"),
      enabled: data.get("enabled") === "on",
    });
  }
  return <form className="admin-model-form" onSubmit={submit}>
    <EditorTitle title={provider ? provider.name : "添加服务"} meta={provider ? `修订 ${provider.revision}` : "连接模型服务"} />
    {!provider && <Field label="服务编号"><input name="id" required pattern="[A-Za-z0-9][A-Za-z0-9._:-]*" placeholder="例如 openai-main" /></Field>}
    <Field label="名称"><input name="name" required defaultValue={provider?.name} /></Field>
    <Field label="接口地址"><input name="base_url" type="url" required defaultValue={provider?.base_url} /></Field>
    <div className="admin-model-form-grid"><Field label="协议"><Select name="protocol" value={provider?.default_protocol ?? "openai_responses"} options={protocols} /></Field><Field label="认证"><Select name="auth_style" value={provider?.auth_style ?? "bearer"} options={[{ value: "bearer", label: "Bearer" }, { value: "x-api-key", label: "x-api-key" }, { value: "x-goog-api-key", label: "Google API Key" }, { value: "none", label: "无" }]} /></Field></div>
    <div className="admin-model-form-grid"><Field label="凭证来源"><Select name="credential_mode" value={provider?.credential_mode ?? "env_ref"} options={[{ value: "env_ref", label: "环境变量" }, { value: "file_ref", label: "凭证文件" }]} /></Field><Field label="凭证引用"><input name="credential_ref" defaultValue={provider?.credential_ref ?? ""} placeholder="无认证时可留空" /></Field></div>
    {provider && <p className={`admin-credential-state ${provider.credential_configured ? "is-on" : ""}`}>{provider.credential_configured ? <Check size={14} /> : null}{provider.credential_configured ? "凭证已配置" : "凭证未配置"}</p>}
    <Toggle defaultChecked={provider?.enabled ?? true} label="启用服务" />
    <SaveBar saving={saving} error={error} />
  </form>;
}

function ModelEditor({ model, providers, saving, error, onSave }: { model?: AdminModelProfile; providers: AdminModelProvider[]; saving: boolean; error: string | null; onSave: (value: Record<string, unknown>) => void }) {
  const initialModes = model?.thinking_modes ?? ["none"];
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const data = new FormData(event.currentTarget); const modes = thinkingModes.filter(mode => data.get(`thinking_${mode}`) === "on");
    onSave({
      ...(model ? {} : { profile_id: data.get("profile_id"), provider_id: data.get("provider_id"), capabilities: {}, access_scope: "all", is_default: false }),
      upstream_model_id: data.get("upstream_model_id"), label: data.get("label"), family: data.get("family"), protocol_override: data.get("protocol_override"),
      context_tokens: Number(data.get("context_tokens")), max_output_tokens: Number(data.get("max_output_tokens")), thinking_modes: modes,
      default_thinking: data.get("default_thinking"), enabled: data.get("enabled") === "on", sort_order: Number(data.get("sort_order")),
    });
  }
  if (!model && providers.length === 0) return <div className="admin-model-editor-empty"><Cloud size={21} /><p>先添加模型服务。</p></div>;
  return <form className="admin-model-form" onSubmit={submit}>
    <EditorTitle title={model ? model.label : "添加模型"} meta={model ? `修订 ${model.revision}` : "加入聊天与工作模式"} />
    {!model && <div className="admin-model-form-grid"><Field label="模型编号"><input name="profile_id" required pattern="[A-Za-z0-9][A-Za-z0-9._:-]*" /></Field><Field label="所属服务"><Select name="provider_id" value={providers[0]?.id ?? ""} options={providers.map(item => ({ value: item.id, label: item.name }))} /></Field></div>}
    <div className="admin-model-form-grid"><Field label="显示名称"><input name="label" required defaultValue={model?.label} /></Field><Field label="上游模型名"><input name="upstream_model_id" required defaultValue={model?.upstream_model_id} /></Field></div>
    <div className="admin-model-form-grid"><Field label="模型家族"><Select name="family" value={model?.family ?? "other"} options={families} /></Field><Field label="协议"><Select name="protocol_override" value={model?.protocol_override ?? "openai_responses"} options={protocols} /></Field></div>
    <div className="admin-model-form-grid"><Field label="上下文"><input name="context_tokens" type="number" min="1" required defaultValue={model?.context_tokens ?? 128000} /></Field><Field label="最大输出"><input name="max_output_tokens" type="number" min="1" required defaultValue={model?.max_output_tokens ?? 16000} /></Field></div>
    <fieldset className="admin-thinking-field"><legend>思考档位</legend><div>{thinkingModes.map(mode => <label key={mode}><input name={`thinking_${mode}`} type="checkbox" defaultChecked={initialModes.includes(mode)} /><span>{thinkingLabels[mode]}</span></label>)}</div></fieldset>
    <div className="admin-model-form-grid"><Field label="默认档位"><Select name="default_thinking" value={model?.default_thinking ?? initialModes[0]} options={thinkingModes.map(mode => ({ value: mode, label: thinkingLabels[mode] }))} /></Field><Field label="排序"><input name="sort_order" type="number" required defaultValue={model?.sort_order ?? 100} /></Field></div>
    <Toggle defaultChecked={model?.enabled ?? true} label="启用模型" />
    <SaveBar saving={saving} error={error} />
  </form>;
}

function EditorTitle({ title, meta }: { title: string; meta: string }) { return <header className="admin-model-editor-title"><div><h2>{title}</h2><p>{meta}</p></div></header>; }
function Field({ label, children }: { label: string; children: React.ReactNode }) { return <label className="admin-model-field"><span>{label}</span>{children}</label>; }
function Select({ name, value, options }: { name: string; value: string; options: Array<{ value: string; label: string }> }) { return <select name={name} defaultValue={value}>{options.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select>; }
function Toggle({ defaultChecked, label }: { defaultChecked: boolean; label: string }) { return <label className="admin-model-toggle"><input name="enabled" type="checkbox" defaultChecked={defaultChecked} /><span aria-hidden="true" /><b>{label}</b></label>; }
function SaveBar({ saving, error }: { saving: boolean; error: string | null }) { return <footer className="admin-model-save">{error && <p role="alert">{error}</p>}<button className="admin-primary-button" type="submit" disabled={saving}>{saving ? <LoaderCircle className="admin-spin" size={16} /> : <Save size={16} />}{saving ? "保存中" : "保存"}</button></footer>; }
