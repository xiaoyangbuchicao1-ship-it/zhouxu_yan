import { useEffect, useState, type FormEvent } from "react";
import { Check, KeyRound, LogOut, Plus, ShieldCheck, Sparkles, SlidersHorizontal, Trash2, User } from "lucide-react";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { AuthSession } from "@/lib/auth";
import type {
  AccountSummary,
  ThinkingModeValue,
  UserPreferences,
  UserPromptCommand,
} from "@/lib/account-settings-api";

export type UserCenterTab = "account" | "memory" | "habits";

export interface UserCenterDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initialTab?: UserCenterTab;
  account: AuthSession["account"];
  preferences: UserPreferences;
  commands: UserPromptCommand[];
  summary?: AccountSummary | null;
  summaryLoading?: boolean;
  onUpdateDisplayName: (displayName: string) => Promise<AuthSession["account"]>;
  onChangePassword: (currentPassword: string, newPassword: string) => Promise<void>;
  onSavePreferences: (patch: Partial<UserPreferences>) => Promise<UserPreferences>;
  onCreateCommand: (input: {
    trigger: string;
    title: string;
    content: string;
    mode_hint?: "chat" | "work" | null;
  }) => Promise<UserPromptCommand>;
  onUpdateCommand: (
    id: string,
    patch: Partial<{ trigger: string; title: string; content: string; mode_hint: "chat" | "work" | null }>,
  ) => Promise<UserPromptCommand>;
  onDeleteCommand: (id: string) => Promise<void>;
  onOpenCommandPalette?: () => void;
  onSignOut?: () => void;
}

const THINKING_OPTIONS: { value: ThinkingModeValue; label: string }[] = [
  { value: "none", label: "关闭" },
  { value: "low", label: "轻度" },
  { value: "medium", label: "标准" },
  { value: "high", label: "深度" },
];

export function UserCenterDialog({
  open,
  onOpenChange,
  initialTab = "account",
  account,
  preferences,
  commands,
  summary,
  summaryLoading = false,
  onUpdateDisplayName,
  onChangePassword,
  onSavePreferences,
  onCreateCommand,
  onUpdateCommand,
  onDeleteCommand,
  onOpenCommandPalette,
  onSignOut,
}: UserCenterDialogProps) {
  const [tab, setTab] = useState<UserCenterTab>(initialTab);
  const [statusMessage, setStatusMessage] = useState("");
  const [errorMessage, setErrorMessage] = useState("");

  // 账号与密码
  const [displayName, setDisplayName] = useState(account.display_name || "");
  const [savingProfile, setSavingProfile] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [savingPassword, setSavingPassword] = useState(false);

  // 记忆
  const [memoryEnabled, setMemoryEnabled] = useState(preferences.memory_enabled);
  const [memoryText, setMemoryText] = useState(preferences.memory_text);
  const [savingMemory, setSavingMemory] = useState(false);

  // 自定义 / 指令
  const [editingCmdId, setEditingCmdId] = useState<string | null>(null);
  const [cmdTrigger, setCmdTrigger] = useState("");
  const [cmdTitle, setCmdTitle] = useState("");
  const [cmdModeHint, setCmdModeHint] = useState<"" | "chat" | "work">("");
  const [cmdContent, setCmdContent] = useState("");
  const [savingCmd, setSavingCmd] = useState(false);

  useEffect(() => {
    if (open) {
      setTab(initialTab);
      setStatusMessage("");
      setErrorMessage("");
      setMemoryEnabled(preferences.memory_enabled);
      setMemoryText(preferences.memory_text);
    }
  }, [open, initialTab]);

  useEffect(() => {
    setDisplayName(account.display_name || "");
  }, [account.display_name]);

  function flashStatus(message: string) {
    setErrorMessage("");
    setStatusMessage(message);
  }

  function flashError(error: unknown) {
    setStatusMessage("");
    setErrorMessage(error instanceof Error ? error.message : "保存未完成，请稍后重试。");
  }

  async function submitDisplayName(event: FormEvent) {
    event.preventDefault();
    const cleaned = displayName.trim();
    if (!cleaned) {
      flashError(new Error("请输入显示称呼。"));
      return;
    }
    setSavingProfile(true);
    try {
      await onUpdateDisplayName(cleaned);
      flashStatus("称呼已更新。");
    } catch (error) {
      flashError(error);
    } finally {
      setSavingProfile(false);
    }
  }

  async function submitPasswordChange(event: FormEvent) {
    event.preventDefault();
    if (!currentPassword) {
      flashError(new Error("请输入当前密码。"));
      return;
    }
    if ([...newPassword].length < 12) {
      flashError(new Error("新密码至少需要 12 位字符。"));
      return;
    }
    if (newPassword !== confirmPassword) {
      flashError(new Error("两次输入的新密码不一致。"));
      return;
    }
    setSavingPassword(true);
    try {
      await onChangePassword(currentPassword, newPassword);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      flashStatus("密码已更新。");
    } catch (error) {
      flashError(error);
    } finally {
      setSavingPassword(false);
    }
  }

  async function saveMemory(nextEnabled = memoryEnabled, nextText = memoryText) {
    setSavingMemory(true);
    try {
      await onSavePreferences({
        memory_enabled: nextEnabled,
        memory_text: nextText,
      });
      flashStatus("长期记忆设置已保存。");
    } catch (error) {
      flashError(error);
    } finally {
      setSavingMemory(false);
    }
  }

  function startEditCommand(cmd: UserPromptCommand) {
    setEditingCmdId(cmd.id);
    setCmdTrigger(cmd.trigger);
    setCmdTitle(cmd.title);
    setCmdModeHint(cmd.mode_hint || "");
    setCmdContent(cmd.content);
    setErrorMessage("");
  }

  function resetCommandForm() {
    setEditingCmdId(null);
    setCmdTrigger("");
    setCmdTitle("");
    setCmdModeHint("");
    setCmdContent("");
  }

  async function submitCommand(event: FormEvent) {
    event.preventDefault();
    const cleanTrigger = cmdTrigger.trim().replace(/^\/+/, "").toLowerCase();
    const cleanTitle = cmdTitle.trim();
    const cleanContent = cmdContent.trim();
    if (!cleanTrigger || !cleanTitle || !cleanContent) {
      flashError(new Error("请填写指令代号、名称与正文。"));
      return;
    }
    setSavingCmd(true);
    try {
      const payload = {
        trigger: cleanTrigger,
        title: cleanTitle,
        content: cleanContent,
        mode_hint: cmdModeHint || null,
      };
      if (editingCmdId) {
        await onUpdateCommand(editingCmdId, payload);
        flashStatus(`指令 /${cleanTrigger} 已更新。`);
      } else {
        await onCreateCommand(payload);
        flashStatus(`已添加指令 /${cleanTrigger}。`);
      }
      resetCommandForm();
    } catch (error) {
      flashError(error);
    } finally {
      setSavingCmd(false);
    }
  }

  async function removeCommand(cmd: UserPromptCommand) {
    try {
      await onDeleteCommand(cmd.id);
      if (editingCmdId === cmd.id) resetCommandForm();
      flashStatus(`已移除 /${cmd.trigger}。`);
    } catch (error) {
      flashError(error);
    }
  }

  async function patchHabit(patch: Partial<UserPreferences>, label: string) {
    try {
      await onSavePreferences(patch);
      flashStatus(`${label}已同步。`);
    } catch (error) {
      flashError(error);
    }
  }

  const initials = (account.display_name || account.email || "U").slice(0, 1).toUpperCase();
  const caps = summary?.capabilities;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="ws-dialog ws-user-center-dialog border-[#1e293b] bg-[#0d1218] text-[#eef2f6] p-0 gap-0 max-w-3xl w-[min(94vw,760px)] max-h-[86vh] overflow-hidden flex flex-col"
        data-testid="user-center-dialog"
      >
        <DialogHeader className="px-6 py-4 border-b border-white/10 text-left shrink-0">
          <div className="flex items-center gap-3 pr-8">
            <span className="ws-uc-avatar" aria-hidden="true">{initials}</span>
            <div className="min-w-0">
              <DialogTitle className="text-base font-medium text-[#f4f7fb] flex items-center gap-2">
                <span className="truncate">{account.display_name || "个人账户"}</span>
                <span className="ws-uc-role-pill">
                  {account.role === "admin" ? "管理员" : "个人空间"}
                </span>
              </DialogTitle>
              <DialogDescription className="text-xs text-[#8f9eb0] truncate mt-0.5">
                {account.email}
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        <Tabs
          value={tab}
          onValueChange={(value) => {
            setTab(value as UserCenterTab);
            setStatusMessage("");
            setErrorMessage("");
          }}
          className="flex-1 min-h-0 flex flex-col overflow-hidden"
        >
          <div className="px-6 pt-3 pb-2 border-b border-white/10 shrink-0 overflow-x-auto">
            <TabsList className="bg-[#121922] border border-white/10 h-auto p-1 gap-1 inline-flex min-w-max">
              <TabsTrigger value="account" className="ws-uc-tab text-xs px-3 py-1.5 data-[state=active]:bg-[#1e2938] data-[state=active]:text-[#f4f7fb] text-[#9aa8b8]">
                <User className="w-3.5 h-3.5 mr-1.5" />
                账号与总览
              </TabsTrigger>
              <TabsTrigger value="memory" className="ws-uc-tab text-xs px-3 py-1.5 data-[state=active]:bg-[#1e2938] data-[state=active]:text-[#f4f7fb] text-[#9aa8b8]">
                <Sparkles className="w-3.5 h-3.5 mr-1.5" />
                记忆与 / 指令
              </TabsTrigger>
              <TabsTrigger value="habits" className="ws-uc-tab text-xs px-3 py-1.5 data-[state=active]:bg-[#1e2938] data-[state=active]:text-[#f4f7fb] text-[#9aa8b8]">
                <SlidersHorizontal className="w-3.5 h-3.5 mr-1.5" />
                工作习惯与快捷键
              </TabsTrigger>
            </TabsList>
          </div>

          <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
            {(statusMessage || errorMessage) && (
              <div
                className={errorMessage ? "ws-uc-banner is-error" : "ws-uc-banner is-ok"}
                role={errorMessage ? "alert" : "status"}
              >
                {statusMessage && <Check className="w-3.5 h-3.5 shrink-0" />}
                <span>{errorMessage || statusMessage}</span>
              </div>
            )}

            <TabsContent value="account" className="mt-0 space-y-4">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3" aria-label="账户总览">
                {[
                  { label: "项目", value: summary?.usage.project_count ?? "—" },
                  { label: "对话", value: summary?.usage.conversation_count ?? "—" },
                  { label: "个人资料", value: summary?.usage.material_count ?? "—" },
                  { label: "自建技能", value: summary?.usage.custom_skill_count ?? "—" },
                ].map((stat) => (
                  <div key={stat.label} className="ws-uc-stat">
                    <span>{stat.label}</span>
                    <strong>{stat.value}</strong>
                  </div>
                ))}
              </div>

              <form className="ws-uc-card" onSubmit={submitDisplayName}>
                <div className="ws-uc-card-head">
                  <h3>显示称呼</h3>
                  <p>在工作台侧栏与管理员视角下显示的名字。</p>
                </div>
                <div className="flex flex-wrap gap-2 mt-3">
                  <input
                    id="uc-display-name"
                    aria-label="显示称呼"
                    className="ws-uc-input flex-1 min-w-[180px]"
                    value={displayName}
                    maxLength={80}
                    onChange={(e) => setDisplayName(e.target.value)}
                    placeholder="输入您的称呼"
                  />
                  <button
                    type="submit"
                    className="ws-uc-primary-btn"
                    disabled={savingProfile}
                  >
                    {savingProfile ? "保存中…" : "保存称呼"}
                  </button>
                </div>
              </form>

              <form className="ws-uc-card" onSubmit={submitPasswordChange}>
                <div className="ws-uc-card-head">
                  <h3 className="flex items-center gap-1.5">
                    <KeyRound className="w-3.5 h-3.5 text-[#89aacc]" />
                    修改登录密码
                  </h3>
                  <p>新密码需不少于 12 位字符。</p>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5 mt-3">
                  <input
                    type="password"
                    aria-label="当前密码"
                    className="ws-uc-input"
                    value={currentPassword}
                    onChange={(e) => setCurrentPassword(e.target.value)}
                    placeholder="当前密码"
                    autoComplete="current-password"
                  />
                  <input
                    type="password"
                    aria-label="新密码"
                    className="ws-uc-input"
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    placeholder="新密码（至少12位）"
                    autoComplete="new-password"
                  />
                  <input
                    type="password"
                    aria-label="确认新密码"
                    className="ws-uc-input"
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    placeholder="再次输入新密码"
                    autoComplete="new-password"
                  />
                </div>
                <div className="mt-3 flex justify-end">
                  <button
                    type="submit"
                    className="ws-uc-secondary-btn"
                    disabled={savingPassword}
                  >
                    {savingPassword ? "提交中…" : "更新密码"}
                  </button>
                </div>
              </form>

              <div className="ws-uc-card">
                <div className="ws-uc-card-head">
                  <h3 className="flex items-center gap-1.5">
                    <ShieldCheck className="w-3.5 h-3.5 text-[#89aacc]" />
                    当前账户授权范围
                  </h3>
                </div>
                {summaryLoading && !caps ? (
                  <p className="text-xs text-[#8f9eb0] mt-2">正在读取权限配置…</p>
                ) : (
                  <dl className="ws-uc-kv mt-3">
                    <div>
                      <dt>可用模型</dt>
                      <dd>
                        {caps?.model_policy === "disabled"
                          ? "已停用"
                          : caps?.model_policy === "allowlist"
                            ? `指定清单（${caps.allowed_model_labels.join("、") || "暂未分配"}）`
                            : "平台默认已启用模型"}
                      </dd>
                    </div>
                    <div>
                      <dt>技能权限</dt>
                      <dd>
                        {caps?.skill_policy === "disabled"
                          ? "已停用"
                          : caps?.can_upload_skill
                            ? "可使用内置技能并上传自定义技能包"
                            : "可使用内置技能"}
                      </dd>
                    </div>
                  </dl>
                )}
                {onSignOut && (
                  <div className="mt-4 pt-3 border-t border-white/10 flex justify-end">
                    <button
                      type="button"
                      className="ws-uc-danger-btn"
                      onClick={onSignOut}
                    >
                      <LogOut className="w-3.5 h-3.5" />
                      退出当前登录
                    </button>
                  </div>
                )}
              </div>
            </TabsContent>

            <TabsContent value="memory" className="mt-0 space-y-4">
              <div className="ws-uc-card">
                <div className="flex items-start justify-between gap-4">
                  <div className="ws-uc-card-head">
                    <h3>跨会话长期研究记忆</h3>
                    <p>开启后，每次提问都会附带这段个人研究偏好，无需每次重复说明。</p>
                  </div>
                  <Switch
                    id="uc-memory-enabled"
                    aria-label="启用长期记忆"
                    checked={memoryEnabled}
                    onCheckedChange={(checked) => {
                      setMemoryEnabled(checked);
                      void onSavePreferences({ memory_enabled: checked })
                        .then(() => flashStatus(checked ? "长期记忆已启用。" : "长期记忆已关闭。"))
                        .catch(flashError);
                    }}
                  />
                </div>
                <textarea
                  id="uc-memory-text"
                  aria-label="个人记忆正文"
                  className="ws-uc-textarea mt-3"
                  rows={4}
                  maxLength={1200}
                  value={memoryText}
                  onChange={(e) => setMemoryText(e.target.value)}
                  placeholder="例如：我是海商法研究与航运合规从业者，回答优先写适用条款与裁判要点，避免泛泛背景介绍。"
                />
                <div className="mt-2.5 flex items-center justify-between gap-3">
                  <span className="text-xs text-[#7c8b9c] tabular-nums">
                    {memoryText.length} / 1200 字
                  </span>
                  <button
                    type="button"
                    className="ws-uc-primary-btn"
                    disabled={savingMemory}
                    onClick={() => void saveMemory(memoryEnabled, memoryText)}
                  >
                    {savingMemory ? "保存中…" : "保存长期记忆"}
                  </button>
                </div>
              </div>

              <div className="ws-uc-card space-y-3">
                <div className="ws-uc-card-head">
                  <h3>快捷 / 指令</h3>
                  <p>在输入框键入 / 可快速插入常用提问模板。</p>
                </div>

                <div className="space-y-2 max-h-[220px] overflow-y-auto pr-1">
                  {commands.map((cmd) => (
                    <div key={cmd.id} className="ws-uc-cmd-row">
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <code className="ws-uc-code">/{cmd.trigger}</code>
                          <span className="text-sm font-medium text-[#eef2f6] truncate">{cmd.title}</span>
                          {cmd.mode_hint && (
                            <span className="ws-uc-tag">
                              {cmd.mode_hint === "work" ? "工作模式" : "聊天模式"}
                            </span>
                          )}
                        </div>
                        <p className="text-xs text-[#8f9eb0] truncate mt-0.5">{cmd.content}</p>
                      </div>
                      <div className="flex items-center gap-1 shrink-0">
                        <button
                          type="button"
                          className="ws-uc-ghost-btn"
                          onClick={() => startEditCommand(cmd)}
                        >
                          编辑
                        </button>
                        <button
                          type="button"
                          className="ws-uc-icon-danger"
                          aria-label={`删除指令 ${cmd.trigger}`}
                          onClick={() => void removeCommand(cmd)}
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </div>
                  ))}
                  {!commands.length && (
                    <p className="text-xs text-[#7c8b9c]">还没有快捷指令，可在下方新建。</p>
                  )}
                </div>

                <form className="pt-3 border-t border-white/10 space-y-2.5" onSubmit={submitCommand}>
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                    <input
                      aria-label="指令代号"
                      className="ws-uc-input"
                      value={cmdTrigger}
                      onChange={(e) => setCmdTrigger(e.target.value)}
                      placeholder="代号，如：brief"
                    />
                    <input
                      aria-label="指令名称"
                      className="ws-uc-input"
                      value={cmdTitle}
                      onChange={(e) => setCmdTitle(e.target.value)}
                      placeholder="名称，如：案件摘要"
                    />
                    <select
                      aria-label="建议模式"
                      className="ws-uc-input"
                      value={cmdModeHint}
                      onChange={(e) => setCmdModeHint(e.target.value as "" | "chat" | "work")}
                    >
                      <option value="">不切换模式</option>
                      <option value="work">建议使用工作模式</option>
                      <option value="chat">建议使用聊天模式</option>
                    </select>
                  </div>
                  <textarea
                    aria-label="指令模板正文"
                    className="ws-uc-textarea"
                    rows={3}
                    value={cmdContent}
                    onChange={(e) => setCmdContent(e.target.value)}
                    placeholder="填写展开后的提问正文…"
                  />
                  <div className="flex items-center justify-end gap-2">
                    {editingCmdId && (
                      <button
                        type="button"
                        className="ws-uc-ghost-btn"
                        onClick={resetCommandForm}
                      >
                        取消编辑
                      </button>
                    )}
                    <button
                      type="submit"
                      className="ws-uc-primary-btn"
                      disabled={savingCmd}
                    >
                      <Plus className="w-3.5 h-3.5" />
                      {editingCmdId ? "保存指令修改" : "添加指令"}
                    </button>
                  </div>
                </form>
              </div>
            </TabsContent>

            <TabsContent value="habits" className="mt-0 space-y-3">
              <div className="ws-uc-card flex flex-wrap items-center justify-between gap-3">
                <div className="ws-uc-card-head">
                  <h3>新建对话默认模式</h3>
                  <p>每次点「新建对话」时预设进入的模式。</p>
                </div>
                <div className="ws-uc-segment" role="group" aria-label="新建对话默认模式">
                  {(["chat", "work"] as const).map((mode) => (
                    <button
                      key={mode}
                      type="button"
                      className={preferences.default_mode === mode ? "is-active" : ""}
                      onClick={() => void patchHabit({ default_mode: mode }, "默认对话模式")}
                    >
                      {mode === "chat" ? "聊天" : "工作"}
                    </button>
                  ))}
                </div>
              </div>

              <div className="ws-uc-card flex flex-wrap items-center justify-between gap-3">
                <div className="ws-uc-card-head">
                  <h3>默认思考深度</h3>
                  <p>所选模型支持该深度时自动启用。</p>
                </div>
                <div className="ws-uc-segment" role="group" aria-label="默认思考深度">
                  {THINKING_OPTIONS.map((opt) => (
                    <button
                      key={opt.value}
                      type="button"
                      className={preferences.default_thinking_mode === opt.value ? "is-active" : ""}
                      onClick={() => void patchHabit({ default_thinking_mode: opt.value }, "默认思考深度")}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="ws-uc-card flex items-center justify-between gap-4">
                <div className="ws-uc-card-head">
                  <h3>简洁运行过程</h3>
                  <p>将检索步骤和中间阶段收拢为单行进度，减少正文上方干扰。</p>
                </div>
                <Switch
                  id="uc-concise-process"
                  aria-label="简洁运行过程"
                  checked={preferences.concise_process}
                  onCheckedChange={(checked) => void patchHabit({ concise_process: checked }, "简洁运行过程")}
                />
              </div>

              <div className="ws-uc-card flex items-center justify-between gap-4">
                <div className="ws-uc-card-head">
                  <h3>紧凑信息密度</h3>
                  <p>适当收紧侧栏条目与段落间距，单屏呈现更多案例和法条内容。</p>
                </div>
                <Switch
                  id="uc-compact-density"
                  aria-label="紧凑信息密度"
                  checked={preferences.compact_density}
                  onCheckedChange={(checked) => void patchHabit({ compact_density: checked }, "紧凑信息密度")}
                />
              </div>

              <div className="ws-uc-card flex items-center justify-between gap-4">
                <div className="ws-uc-card-head">
                  <h3>减弱界面动效</h3>
                  <p>关闭切换过渡与面板滑入动画。</p>
                </div>
                <Switch
                  id="uc-reduced-motion"
                  aria-label="减弱界面动效"
                  checked={preferences.reduced_motion}
                  onCheckedChange={(checked) => void patchHabit({ reduced_motion: checked }, "减弱界面动效")}
                />
              </div>

              <div className="ws-uc-card space-y-2.5">
                <div className="flex items-center justify-between gap-3">
                  <div className="ws-uc-card-head">
                    <h3>常用快捷键</h3>
                    <p>在工作台任意位置呼出命令或切换历史会话。</p>
                  </div>
                  {onOpenCommandPalette && (
                    <button
                      type="button"
                      className="ws-uc-secondary-btn"
                      onClick={() => {
                        onOpenChange(false);
                        onOpenCommandPalette();
                      }}
                    >
                      立即打开面板
                    </button>
                  )}
                </div>
                <div className="ws-uc-shortcuts">
                  <div>
                    <span>全局命令与会话检索</span>
                    <kbd>Ctrl / ⌘ + K</kbd>
                  </div>
                  <div>
                    <span>输入框调出快捷模板</span>
                    <kbd>/</kbd>
                  </div>
                  <div>
                    <span>发送提问 / 插入换行</span>
                    <kbd>Enter / Shift + Enter</kbd>
                  </div>
                </div>
              </div>
            </TabsContent>
          </div>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}
